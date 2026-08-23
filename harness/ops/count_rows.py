#!/usr/bin/env python3
"""Progress for an append-only stage file, in ONE place.

    count_rows.py <path>        -> print the number of completed units of work
    count_rows.py               -> self-test, no arguments needed

WHY THIS IS A FILE AND NOT A `python3 -c` IN EACH SCRIPT.  `wc -l` is not
progress for any stage that writes one row per ATTEMPT (spec 4.39): `generate`
wrote 176 quota refusals as `ok=False` rows, `load_done` retired them, and the
supervisor counted LINES, so the failures satisfied its own target.  The rule
that fixes that is "count successes", and the moment two scripts each carry
their own copy of it, one of them is eventually the stale one -- which is 4.33,
the defect behind most of this harness's wrong-but-plausible numbers.

The rule itself is deliberately per-file rather than per-stage:

  * a record WITH an `ok` field is one attempt -- count it only if the attempt
    succeeded and actually carries text (an `ok=True` row with empty content is
    the truncation failure mode, not a unit of work);
  * a record WITHOUT an `ok` field is one completed unit by construction --
    `solve` writes a scored response per row and has no attempt field at all.

Asking the ROW which kind it is, rather than asking a table keyed by stage
name, is what keeps this correct when a new stage arrives: a stage missing from
a table is silently exempt (4.36), a row missing a field is not.
"""
import json
import sys
from pathlib import Path


def count(path) -> int:
    n = 0
    try:
        fh = open(path)
    except FileNotFoundError:
        return 0
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue          # a torn final line during a live write
            if "ok" in r:
                if r.get("ok") and str(r.get("response") or "").strip():
                    n += 1
            else:
                n += 1
    return n


def _selftest() -> int:
    import tempfile

    bad = 0

    def check(name, got, want):
        nonlocal bad
        if got != want:
            bad += 1
            print(f"FAIL {name}: got {got!r}, want {want!r}")
        else:
            print(f"ok   {name}")

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)

        check("missing file is 0", count(d / "nope.jsonl"), 0)

        p = d / "attempts.jsonl"
        p.write_text(
            json.dumps({"ok": True, "response": "hi"}) + "\n"
            + json.dumps({"ok": False, "error_kind": "quota_exhausted"}) + "\n"
            + json.dumps({"ok": True, "response": "   "}) + "\n"
            + json.dumps({"ok": True, "response": None}) + "\n"
            + json.dumps({"ok": True, "response": "yes"}) + "\n"
        )
        check("per-attempt file counts successes only", count(p), 2)
        check("...and is NOT wc -l", count(p) != 5, True)

        p = d / "solve.jsonl"
        p.write_text(
            json.dumps({"item": "a", "leak_adj": 0.5}) + "\n"
            + json.dumps({"item": "b", "leak_adj": 0.1}) + "\n"
        )
        check("no ok field: every record is a unit", count(p), 2)

        p = d / "torn.jsonl"
        p.write_text(json.dumps({"item": "a"}) + "\n" + '{"item": "b"')
        check("torn final line ignored", count(p), 1)

        p = d / "blank.jsonl"
        p.write_text("\n\n" + json.dumps({"item": "a"}) + "\n\n")
        check("blank lines ignored", count(p), 1)

    print("SELFTEST", "FAILED" if bad else "PASSED")
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(_selftest())
    print(count(sys.argv[1]))
