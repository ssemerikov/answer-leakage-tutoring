#!/usr/bin/env bash
# Run a quota-limited harness stage to completion, absorbing quota stops.
#
#   supervise.sh <stage> <bank> <target> [roster]
#
# Waits WAIT seconds after a quota stop and relaunches; ABORTS if a full launch
# adds no rows, because that means the next unit of work is failing
# reproducibly and looping cannot help.
#
# PROGRESS IS SUCCESSFUL ROWS, NOT `wc -l` (spec 4.39). Stages that write one row
# per ATTEMPT record failures too, so line-counting reported "2039/2016" -- past
# target while 156 cells were still empty. `rows()` counts records whose `ok` is
# true and whose response is non-empty; records with no `ok` field are counted as
# done (screen/placebo write one row per item).
set -u
STAGE=$1; BANK=$2; TARGET=$3; ROSTER=${4:-ol}
WAIT=${WAIT:-300}
H="$(cd "$(dirname "$0")/.." && pwd)"          # harness/
# OUT, LOG, SLOG and PIDFILE come from ops/stage_paths.sh, shared with
# watch_stage.sh.  See that file for why the two logs must not be confused.
STAGE="$STAGE" BANK="$BANK" ROSTER="$ROSTER" . "$H/ops/stage_paths.sh"
mkdir -p "$(dirname "$LOG")"

rows() {
  # Progress is SUCCESSFUL rows, never `wc -l` (spec 4.39).  One implementation,
  # in ops/count_rows.py, shared with watch_stage.sh -- a second copy of this
  # rule is 4.33, and the stale copy is the one that reports a stalled stage as
  # working.
  python3 "$H/ops/count_rows.py" "$OUT"
}

log() { echo "$(date -Is) $*" | tee -a "$SLOG"; }

# ITS OWN PID, WRITTEN DOWN. A watcher that finds the supervisor by
# `pgrep -f "supervise.sh solve bs" | head -1` can pick a transient wrapper that
# exits seconds later and then report the supervisor as DEAD while it is still
# working -- observed 2026-08-21, a false "supervisor GONE at 127/3888" while
# rows were still landing. Matching on the command line has a second trap
# (spec 4.51): the pattern is an argument of the watcher, so it matches the
# watcher itself and liveness reads as permanently true. A pidfile has neither
# failure mode: exactly one writer, and $$ cannot be anything but this shell.
# PIDFILE comes from stage_paths.sh (sourced above) -- not re-derived here.
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

attempt=0
stall=0
authstall=0
# A 401 is credentials, not entitlement, so looping cannot fix a bad one -- but
# on this backend it has twice been TRANSIENT (2026-08-22 07:09 and 23:28, both
# self-clearing, the first in ~43 min). Exiting at once cost a 9-hour idle
# night; looping forever is what 4.64 exists to prevent. So: bounded. Past the
# bound a human really is needed, and an hour is well past the observed clear.
MAX_AUTH=${MAX_AUTH:-12}     # 12 x 5min = 1h of refused credentials before giving up
MAX_STALL=${MAX_STALL:-144}  # 144 x 5min = 12h of closed quota before giving up.
# This is a TIME budget written in cycles: it moves whenever WAIT moves, or the
# bound silently shortens.  12h is the figure to preserve -- 13 consecutive
# 20-minute waits were once needed (2026-08-19) and the allowance did return.
while :; do
  before=$(rows)
  if [ "$before" -ge "$TARGET" ]; then
    log "$STAGE $BANK COMPLETE ($before/$TARGET)"; exit 0
  fi
  attempt=$((attempt + 1))
  log "supervisor start: $STAGE bank=$BANK target=$TARGET; $before rows; out=$OUT"
  echo "=== supervisor launch $(date -Is) at $before/$TARGET ===" >> "$LOG"
  base=$(wc -c < "$LOG" 2>/dev/null || echo 0)
  (cd "$H" && python3 -u run.py "$STAGE" --bank "$BANK" --roster "$ROSTER") >> "$LOG" 2>&1
  after=$(rows)

  hits=$(python3 "$H/ops/failure_since.py" "$base" "$LOG" 2>/dev/null | tail -1)
  if [ -n "$hits" ]; then
    # An auth-only stop is still a HARD failure -- it is not absorbed silently,
    # it is bounded. `--auth` asks whether auth explains EVERY hard failure in
    # the slice, never whether one appears in it: a slice mixing a 401 with a
    # genuine traceback must still stop, because waiting cannot fix the crash.
    if python3 "$H/ops/failure_since.py" --auth "$base" "$LOG" 2>/dev/null; then
      authstall=$((authstall + 1))
      if [ "$authstall" -ge "$MAX_AUTH" ]; then
        log "ABORT: $STAGE $BANK refused credentials for $authstall consecutive"
        log "cycles (~$(( authstall * WAIT / 60 ))min) at $after/$TARGET -- re-authenticate."
        exit 1
      fi
      log "auth refused at $after/$TARGET (auth $authstall/$MAX_AUTH); waiting ${WAIT}s"
      sleep "$WAIT"
      continue
    fi
    log "HARD FAILURE at $after/$TARGET: $(printf '%s' "$hits" | cut -c1-200)"
    exit 1
  fi
  if [ "$after" -ge "$TARGET" ]; then
    log "$STAGE $BANK COMPLETE ($after/$TARGET)"; exit 0
  fi
  if [ "$after" -le "$before" ]; then
    # ZERO PROGRESS HAS TWO CAUSES NEEDING OPPOSITE RESPONSES, and conflating
    # them cost 3.4 hours on 2026-08-21: solve bs relaunched 20 minutes after a
    # quota stop, the window was STILL closed, the run added no rows in two
    # seconds, and this branch called it "failing reproducibly" and exited. The
    # project's own note says a multi-hour stall is normal and that 13
    # consecutive 20-minute waits were once needed to clear one.
    #
    # So ask which it was. A deterministic crash leaves a traceback with no
    # quota reason, which the `failure_since.py` check above already catches and
    # exits on -- so zero progress WITH a quota reason is routine. The only
    # reason to bound it at all is to stop an unbounded loop if the allowance
    # never returns, hence a generous cap rather than a single strike.
    if python3 "$H/ops/failure_since.py" --quota "$base" "$LOG" 2>/dev/null; then
      stall=$((stall + 1))
      if [ "$stall" -ge "$MAX_STALL" ]; then
        log "ABORT: $STAGE $BANK stalled on quota for $stall consecutive cycles"
        log "(~$(( stall * WAIT / 3600 ))h) at $after/$TARGET -- the allowance is not returning."
        exit 2
      fi
      log "quota still closed at $after/$TARGET (stall $stall/$MAX_STALL); waiting ${WAIT}s"
      sleep "$WAIT"
      continue
    fi
    log "ABORT: $STAGE $BANK made no progress ($after/$TARGET) and the stop was"
    log "not quota -- the next unit of work is failing reproducibly."
    exit 2
  fi
  stall=0
  authstall=0
  log "quota exhausted at $after/$TARGET; waiting ${WAIT}s"
  sleep "$WAIT"
done
