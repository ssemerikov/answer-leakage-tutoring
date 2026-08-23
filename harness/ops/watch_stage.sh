#!/usr/bin/env bash
# Periodic progress report for a supervised stage.  One stdout line per check,
# so it can be armed as a Monitor and every line becomes a notification.
#
#   watch_stage.sh <stage> <bank> <target> [interval_s]
#
# Four rules are encoded here, each of which was learned by getting it wrong:
#
#  1. PROGRESS IS SUCCESSFUL ROWS, not `wc -l` (spec 4.39).  Delegated to
#     ops/count_rows.py, which supervise.sh also calls.  No private counter.
#  2. LIVENESS COMES FROM THE PIDFILE, never `pgrep -f` (4.65).  `pgrep | head -1`
#     picked a transient wrapper and reported a working stage dead at 127/3888;
#     passing the pattern as the watcher's own argument makes the watcher match
#     itself, so death can never be reported (4.51).  Both directions are wrong.
#  3. DEATH NEEDS A SECOND SIGNAL.  Reported only when the pidfile says gone AND
#     no new rows arrived since the last check.  Advancing rows override a stale
#     pidfile -- work in progress outranks bookkeeping.
#  4. FAILURE DETECTION IS DELEGATED to ops/failure_since.py (4.51).  Never
#     retype `grep Traceback | grep -v quota`: a traceback is ONE event spanning
#     many lines and the quota reason sits only on the last, so line-wise
#     exclusion reports every routine quota stop as a hard failure.
#
# Two rates are printed because neither alone is honest.  `cal` is measured over
# the wall clock since the previous check, which counts host suspend as working
# time and so UNDER-reports throughput; `act` is a short live probe taken at the
# check itself.  A suspended host freezes `sleep`, so a multi-hour gap between
# checks is usually the machine sleeping rather than an outage -- tell them apart
# by the stall counter, which only a real quota outage accumulates.
#
#  5. THE PROBE IS A RATE ONLY IF IT COLLECTED ENOUGH ROWS TO BE ONE (4.57).
#     `act` is printed with its own sample size and the ETA falls back to `cal`
#     below MIN_PROBE_ROWS, because a probe that caught ONE row does not have
#     low confidence -- it reports a different number.
#
#  6. AN AUTH-ONLY STOP IS BOUNDED, NOT FATAL, AND THIS SCRIPT DOES NOT OWN THE
#     BOUND.  supervise.sh counts auth cycles to MAX_AUTH; the watcher reports
#     and keeps watching.  Duplicating the bound here would be the same rule in
#     two places, which is how this branch came to exit 3 on an event the
#     supervisor was already handling correctly.
set -u

STAGE="${1:?stage}"; BANK="${2:?bank}"; TARGET="${3:?target}"
INTERVAL="${4:-7200}"
PROBE="${PROBE:-300}"
# Below this many rows the live probe is not a rate; see rule 5.
MIN_PROBE_ROWS="${MIN_PROBE_ROWS:-5}"

H="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# OUT, LOG, SLOG, PIDFILE come from ops/stage_paths.sh -- shared with
# supervise.sh.  A private copy here derived OUT differently and greped the
# WRONG log for the stall counter; see that file's header.
STAGE="$STAGE" BANK="$BANK" ROSTER="${ROSTER:-ol}" . "$H/ops/stage_paths.sh"

rows() { python3 "$H/ops/count_rows.py" "$OUT"; }

alive() {   # pidfile only -- see rule 2
  [ -f "$PIDFILE" ] || return 1
  local p; p="$(cat "$PIDFILE" 2>/dev/null)"
  [ -n "$p" ] || return 1
  kill -0 "$p" 2>/dev/null
}

stalls() {
  # A real quota outage accumulates these; a suspend produces none -- which is
  # the ONLY way to tell the two apart, so reading the wrong file here does not
  # break the report, it silently inverts it.  The counter lives in SLOG (the
  # supervisor's own narration), never in LOG (run.py's captured output).
  [ -f "$SLOG" ] || { echo 0; return; }
  awk '/supervisor start/{n=0} / stall [0-9]+\//{n++} END{print n+0}' "$SLOG"
}

hhmm() { printf '%dh%02dm' $(( $1 / 60 )) $(( $1 % 60 )); }

# Byte offset into LOG at arm time: failures BEFORE we started watching are not
# ours to report, and the supervisor's log is append-only across relaunches so
# this stays valid.  failure_since.py takes <offset> <log>; called with one
# argument it used to run its self-test and exit 0, which is how this check spent
# an hour unable to fire.
# BASE=0 re-arms over the WHOLE log (useful after a restart, and how the
# hard-failure branch is tested -- an untested branch here is one that
# spent an hour unable to fire).
base="${BASE:-$(wc -c < "$LOG" 2>/dev/null || echo 0)}"
prev="$(rows)"; prev_t="$(date +%s)"
echo "$(date +%H:%M) watch $STAGE $BANK armed at $prev/$TARGET, reporting every $((INTERVAL / 60))m"

while : ; do
  sleep "$INTERVAL"

  # A hard failure is reported before anything else and ends the watch: looping
  # cannot help a deterministic failure (4.64).
  # Capture the size BEFORE reading, so advancing `base` past a reported auth
  # stop cannot swallow a failure appended while we were reading it.
  size="$(wc -c < "$LOG" 2>/dev/null || echo "$base")"
  fail="$(python3 "$H/ops/failure_since.py" "$base" "$LOG" 2>/dev/null)"
  if [ -n "$fail" ]; then
    # AN AUTH-ONLY STOP IS BOUNDED, NOT FATAL, AND THE BOUND LIVES IN
    # supervise.sh (MAX_AUTH) -- one implementation, not two.  This watcher
    # exited 3 on an auth stop at 3788/3888 on 2026-08-23 while the supervisor
    # absorbed the identical event in three cycles and carried on: the rule had
    # been updated in one place only (4.33), so the run lost its monitoring and
    # kept none of its risk.  Report it, advance past it, keep watching; a
    # credential that is genuinely bad exhausts MAX_AUTH and the supervisor
    # exits, which the pidfile check below reports.
    if python3 "$H/ops/failure_since.py" --auth "$base" "$LOG" 2>/dev/null; then
      echo "$(date +%H:%M) AUTH REFUSED in $STAGE $BANK at $(rows)/$TARGET -- bounded by supervise.sh MAX_AUTH, still watching"
      base="$size"
    else
      echo "HARD FAILURE in $STAGE $BANK at $(rows)/$TARGET -- not a quota stop:"
      printf '%s\n' "$fail" | tail -4 | cut -c1-400
      exit 3
    fi
  fi

  now="$(rows)"; now_t="$(date +%s)"
  gap=$(( (now_t - prev_t) / 60 ))
  d=$(( now - prev ))
  cal="n/a"
  [ "$gap" -gt 0 ] && cal="$(python3 -c "print(f'{$d*60/$gap:.0f}')")"

  if [ "$now" -ge "$TARGET" ]; then
    echo "COMPLETE $STAGE $BANK $now/$TARGET"
    exit 0
  fi

  # live probe: the ACTIVE rate, unaffected by any suspend inside the interval
  a0="$now"; sleep "$PROBE"; a1="$(rows)"
  pr=$(( a1 - a0 ))
  act="$(python3 -c "print(f'{$pr*3600/$PROBE:.0f}')")"
  now="$a1"

  left=$(( TARGET - now ))
  eta="?"
  # Ask "is this a measurement?", never "is there a measurement?" (4.57).  A
  # solve row costs ~6 solver calls, so a 300s probe that catches ONE row
  # measures 12/h with 100% resolution: that printed `ETA 223.2h run` at 12:05
  # on 2026-08-22 while the calendar rate said ~24h, and the run was healthy.
  # Below the floor the ETA falls back to `cal` -- which under-reports when the
  # host has slept, but is never fabricated.  `act` is still printed, with its
  # sample size, because the raw count is an honest observation.
  if [ "$pr" -ge "$MIN_PROBE_ROWS" ]; then
    eta="$(python3 -c "print(f'{$left/$act:.1f}h run')")"
  elif [ "$cal" != "n/a" ] && [ "$cal" -gt 0 ]; then
    eta="$(python3 -c "print(f'{$left/$cal:.1f}h cal')")"
  fi

  st="$(stalls)"
  state="alive"
  if ! alive; then
    if [ "$d" -le 0 ]; then
      echo "SUPERVISOR GONE: $STAGE $BANK at $now/$TARGET, pidfile empty and no new rows in $(hhmm "$gap"). Relaunch: cd $H/ops && nohup bash supervise.sh $STAGE $BANK $TARGET ol > logs/nohup_${STAGE}_${BANK}.out 2>&1 &"
      exit 4
    fi
    state="pidfile-stale-but-advancing"   # rule 3: rows outrank bookkeeping
  fi

  pct="$(python3 -c "print(f'{100*$now/$TARGET:.1f}')")"
  susp=""
  [ "$gap" -gt $(( INTERVAL / 30 )) ] && [ "$st" -eq 0 ] && susp=" host-slept($(hhmm "$gap") gap, stall 0)"
  echo "$(date +%H:%M) $STAGE $BANK $now/$TARGET (${pct}%) +$d in $(hhmm "$gap") cal=${cal}/h act=${act}/h(${pr}r/${PROBE}s) ETA ${eta} stall=${st} ${state}${susp}"

  prev="$now"; prev_t="$(date +%s)"
done
