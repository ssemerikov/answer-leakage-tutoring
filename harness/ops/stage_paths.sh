# Canonical file locations for a supervised stage.  SOURCE this; it defines
# variables and does nothing else.
#
#   STAGE=solve BANK=bs ROSTER=ol . stage_paths.sh
#     -> OUT, LOG, SLOG, PIDFILE
#
# THE TWO LOGS ARE NOT INTERCHANGEABLE, and confusing them silently disables a
# guard rather than breaking it:
#
#   LOG   run.py's captured stdout/stderr -- progress lines and TRACEBACKS.
#         This is what failure_since.py reads, because the BackendUnavailable
#         message (and its "QUOTA EXHAUSTED" reason) is part of the traceback.
#   SLOG  the supervisor's OWN narration -- "quota exhausted ... waiting 1200s",
#         "stall n/36", ABORT.  This is the only place the STALL COUNTER exists.
#
# A watcher that greps LOG for `stall` finds nothing, reports `stall=0` forever,
# and then labels a genuine quota outage "host-slept" -- because stall=0 is
# exactly how a suspend is distinguished from an outage.  The predicate cannot
# fire, so the output is reassuring rather than absent, which is the failure mode
# this harness keeps producing (4.40: ask "is this a measurement?", not "is there
# one?").  Observed 2026-08-22, one hour after watch_stage.sh was rebuilt.
#
# OUT is derived once here too.  supervise.sh uppercased the bank id while the
# rebuilt watcher hard-coded a `case` for `A`/`bs`; the two happened to agree on
# every bank in use, which is what a second copy of a rule looks like right
# before it stops agreeing (4.33).

: "${STAGE:?STAGE}"; : "${BANK:?BANK}"
ROSTER="${ROSTER:-ol}"
_SP_H="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"        # harness/

LOG="${LOG:-$_SP_H/ops/logs/${STAGE}_${BANK}.log}"
SLOG="${SLOG:-$_SP_H/ops/logs/supervise_${STAGE}_${BANK}.log}"
PIDFILE="${PIDFILE:-$_SP_H/ops/logs/${STAGE}_${BANK}.pid}"
OUT="${OUT:-$_SP_H/../data/runs/${STAGE}_bank$(echo "$BANK" | tr '[:lower:]' '[:upper:]')_${ROSTER}.jsonl}"
