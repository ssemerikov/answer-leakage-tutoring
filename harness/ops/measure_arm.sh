#!/usr/bin/env bash
# Measure one (bank, arm) token budget, retrying ONLY the failure that retrying
# can fix.
#
#   measure_arm.sh <bank> <arm> [items] [calls]
#
# WHY THIS WRAPPER EXISTS AND A BARE LOOP DOES NOT DO (spec 4.57).  On
# 2026-08-20 Bank A's first generate-arm probe lost 146 of 192 calls to quota and
# every one of the 46 survivors came from ONE item, yet the report printed four
# confident `RAISE ... recommended=` lines -- and the watcher's success test was
# `grep -q 'recommended=' log`, i.e. "is there a recommendation?", the exact
# question 4.40 says never to ask.  An inadequate sample DOES print a
# recommendation.  So the answer moved into the exit code, and reading it is the
# whole job of this script:
#
#   0  adequate sample, configured ceiling clears it            -> done
#   1  adequate sample, RAISE (or a hard error / truncated probe) -> STOP.
#      A HUMAN EDITS A CONSTANT.  Looping cannot help, and neither can it help
#      a traceback, which is why both land here: the response is the same one.
#   2  not yet a measurement                                    -> retry; the
#      file is append-only, so each attempt ADDS to the sample.
#
# `run_pipeline.sh` once treated every nonzero as "wait 20m", which would have
# burned four hours on an rc=1 that needs a constant edited by hand.  That was
# this rule's third home; it has one now.
set -u

BANK="${1:?bank}"; ARM="${2:?arm}"
ITEMS="${3:-8}"; CALLS="${4:-6}"
WAIT="${WAIT:-1200}"
MAX_TRIES="${MAX_TRIES:-36}"          # ~12 h at the 20-minute cycle

H="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$H/ops/logs/measure_${ARM}_${BANK}.log"
mkdir -p "$H/ops/logs"

log() { echo "$(date -Is) $*" | tee -a "$LOG"; }

try=0
while : ; do
  try=$((try + 1))
  log "measure arm=$ARM bank=$BANK attempt $try/$MAX_TRIES (items=$ITEMS calls=$CALLS)"
  ( cd "$H" && python3 measure_budgets.py --bank "$BANK" --roster ol \
      --arm "$ARM" --items "$ITEMS" --calls "$CALLS" ) 2>&1 | tee -a "$LOG"
  rc="${PIPESTATUS[0]}"

  case "$rc" in
    0) log "OK: adequate sample and every configured ceiling clears it."
       exit 0 ;;
    1) log "STOP (rc=1): a number is ready to apply, or the run failed hard."
       log "Either way a human acts -- edit the arm's table in config.py, or read"
       log "the traceback above.  Re-running unchanged reproduces this."
       exit 1 ;;
    2) if [ "$try" -ge "$MAX_TRIES" ]; then
         log "GIVING UP (rc=2) after $try attempts: still not a measurement."
         exit 2
       fi
       log "rc=2: not yet a measurement (quota, most likely). The file is"
       log "append-only, so the next attempt grows the sample. waiting ${WAIT}s"
       sleep "$WAIT" ;;
    *) log "UNEXPECTED rc=$rc from measure_budgets.py -- stopping rather than"
       log "guessing which of the three cases this is."
       exit "$rc" ;;
  esac
done
