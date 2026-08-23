#!/usr/bin/env python3
"""Measure seconds-per-screened-item at the CURRENT token budgets.

WHY THIS IS NOT DERIVED FROM A COMPLETED RUN FILE. Screen records carry no
timing field -- `baseline_identifiability` writes verdict and attrition data and
nothing about wall clock -- so a finished screen cannot be re-timed after the
fact. The honest measurement is therefore of a screen that is RUNNING: sample
the append-only output over a window and divide.

That field should be added at the next `STUDY_VERSION` bump, not now: the Bank B
re-screen is in flight, and changing what a record contains mid-run puts two
schemas in one append-only file -- the same "two populations in one file" hazard
that `freeze_bank`'s study-version guard exists to catch (spec 4.30, 4.33).
A metadata-only field would not corrupt a verdict, but the discipline is worth
more than the convenience.

WHY IT MATTERS. `size_bank_b.TIMING` converts item counts into hours, and
those hours are what the authors weigh when deciding how many items to screen
and whether to consume surplus retained ones. Call latency is dominated by
tokens EMITTED, so raising `C.MAX_TOKENS_SOLVER` lengthens precisely the calls
that used to hit the cap and stop early. A timing measured under the old budgets
understates the new cost, and understating the cost of a quota-limited run is
how a study runs out of quota mid-stage.

    python3 timing_from_screen.py --screen data/runs/screen_bankBS_ol.jsonl
    python3 timing_from_screen.py --screen <path> --window 600
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import config as C


def rows(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
               if line.strip())


def measure(path: Path, window_s: float, poll_s: float = 10.0) -> dict:
    start_n = rows(path)
    t0 = time.monotonic()
    deadline = t0 + window_s
    last_report = t0
    while time.monotonic() < deadline:
        time.sleep(poll_s)
        now, n = time.monotonic(), rows(path)
        if now - last_report >= 60:
            gained = n - start_n
            print(f"  [{int(now - t0):4d}s] {n} rows (+{gained})", flush=True)
            last_report = now
        # An item that takes longer than the whole window would otherwise
        # report zero progress and divide by zero below; keep waiting only if
        # the window has not expired.
    elapsed = time.monotonic() - t0
    end_n = rows(path)
    gained = end_n - start_n
    return {"start": start_n, "end": end_n, "gained": gained,
            "elapsed_s": elapsed,
            "sec_per_item": (elapsed / gained) if gained else None}


def from_supervisor_log(log: Path) -> dict:
    """Derive s/item retrospectively from a supervisor log.

    Live sampling needs a running stage, and a quota-limited run spends most of
    its wall clock stalled -- so on the day you most need the number, there is
    nothing to sample. The supervisor already records what is required: the
    timestamp and row count at every launch and every stall.

    ONLY ACTIVE INTERVALS COUNT. Wall clock between a stall and the next launch
    is the provider's quota window, not work, and including it would report a
    number three times too slow in a way that looks like a measurement. Each
    interval runs from a `launching` line to the next `quota exhausted` or
    `COMPLETE` line, and its item count is the difference of the row counts the
    supervisor itself logged.

    The overhead of items ABORTED mid-measurement is deliberately included: a
    quota-limited run really does pay it, and `TIMING` exists to predict
    how long a real run takes, not how fast the models could go.
    """
    import re
    ev = []
    pat = re.compile(r"^(\S+) (?:ready at (\d+)/\d+ -- launching"
                     r"|quota exhausted at (\d+)/\d+"
                     r"|COMPLETE: (\d+)/\d+)")
    for line in log.read_text(encoding="utf-8").splitlines():
        m = pat.match(line)
        if not m:
            continue
        ts, launch, stall, done = m.groups()
        kind = "launch" if launch is not None else "stop"
        rows_ = launch or stall or done
        ev.append((datetime.fromisoformat(ts), kind, int(rows_)))

    spans, i = [], 0
    while i < len(ev):
        if ev[i][1] != "launch":
            i += 1
            continue
        # The next stop closes this span; consecutive stalls at the same row
        # count are the wait loop, not new information.
        j = i + 1
        while j < len(ev) and ev[j][1] != "stop":
            j += 1
        if j >= len(ev):
            break
        secs = (ev[j][0] - ev[i][0]).total_seconds()
        gained = ev[j][2] - ev[i][2]
        if gained > 0 and secs > 0:
            spans.append((ev[i][0], ev[j][0], secs, gained))
        i = j + 1

    tot_s = sum(x[2] for x in spans)
    tot_n = sum(x[3] for x in spans)
    return {"spans": spans, "elapsed_s": tot_s, "gained": tot_n,
            "sec_per_item": (tot_s / tot_n) if tot_n else None}


def suggestion(spi: float) -> str:
    """The line to paste into `size_bank_b.py`, BUILT FROM ITS ACTUAL STRUCTURE.

    This printed `SEC_PER_ITEM = ...` and `SEC_PER_ITEM_MEASURED_AT = "..."` on
    two lines until 2026-08-17. Those names no longer exist: they were merged
    into one `ScreenTiming` tuple precisely because updating one without the
    other silently produced a 3.3x-optimistic estimate. So the measurement tool
    was instructing its reader to recreate, by hand, the exact defect the merge
    existed to prevent -- and `test_size_bank_b.py` would have failed them for
    complying.

    Hard-coding the new form would only move the drift one release along. The
    text is therefore GENERATED from `size_bank_b`'s own type and field order,
    so a rename or a reorder there rewrites this output instead of invalidating
    it. An unrecognised field prints a visible marker rather than raising:
    losing a 15-minute measurement to a traceback at the print step would be a
    worse failure than a suggestion the reader can see is incomplete.
    """
    import size_bank_b as SB

    known = {"sec_per_item": f"{spi:.0f}", "measured_at": f'"{C.STUDY_VERSION}"'}
    args = ", ".join(
        known.get(f, f"<UNKNOWN FIELD {f!r} -- update timing_from_screen.suggestion>")
        for f in SB.TIMING._fields
    )
    return (f"In size_bank_b.py set:\n"
            f"    TIMING = {type(SB.TIMING).__name__}({args})\n"
            f"\nOne value, not two: the measurement and the study version it was "
            f"taken under are a single fact, and a partial update is the bug "
            f"this tuple exists to make impossible.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--screen", help="append-only screen jsonl of a RUNNING stage")
    ap.add_argument("--from-log",
                    help="supervisor log to derive timing retrospectively; use "
                         "when the stage is stalled on quota and cannot be sampled")
    ap.add_argument("--window", type=float, default=900.0,
                    help="seconds to observe (default 900)")
    a = ap.parse_args()

    if a.from_log:
        res = from_supervisor_log(Path(a.from_log))
        if not res["gained"]:
            print("no completed items in any active interval of that log")
            return 1
        print(f"active intervals in {Path(a.from_log).name} "
              f"(quota stalls excluded):")
        for t0, t1, secs, n in res["spans"]:
            print(f"  {t0:%H:%M}-{t1:%H:%M}  {secs:6.0f}s  {n:2d} items  "
                  f"{secs / n:5.0f} s/item")
        spi = res["sec_per_item"]
        print(f"\n  TOTAL {res['elapsed_s']:.0f}s / {res['gained']} items "
              f"= {spi:.0f} s/item")
        print(f"\n{suggestion(spi)}")
        return 0

    if not a.screen:
        ap.error("one of --screen or --from-log is required")

    path = Path(a.screen)
    if not path.is_absolute():
        path = C.ROOT / path

    print(f"observing {path.name} for {a.window:.0f}s at study {C.STUDY_VERSION}")
    res = measure(path, a.window)

    if not res["gained"]:
        print(f"\nNO ITEMS COMPLETED in {res['elapsed_s']:.0f}s (still at "
              f"{res['end']} rows). Either the stage is not running, or one item "
              f"is taking longer than the window -- rerun with a larger --window "
              f"rather than reading this as 'infinitely slow'.")
        return 1

    spi = res["sec_per_item"]
    print(f"\n{res['gained']} items in {res['elapsed_s']:.0f}s "
          f"= {spi:.0f} s/item ({spi / 60:.1f} s/call at 60 calls/item)")
    print(f"\n{suggestion(spi)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
