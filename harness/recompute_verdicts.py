"""Recompute `passes_headroom` on existing screen records. No API calls.

WHY THIS IS NOT RE-SCREENING. The baselines are already measured and are not
touched. Only the VERDICT is recomputed, from the same stored number, under a
corrected comparison: `passes_headroom` used a bare `<=` and so decided exact
ties by floating-point representation. `A-L05-03` scored exactly 29/60 -- the
threshold to the last bit -- and was rejected because `29/60` and `1/3 + 0.15`
differ by one ULP.

The stated rule has always been "ties pass". This makes the code obey it. It is
a bug fix applied uniformly to every record, blind to which way each item moves,
not a threshold chosen after seeing results.

Keeps the pre-change file as `QUARANTINE_<name>_pre_verdict_recompute_<date>_<sha8>.jsonl`
and reports every changed verdict.

Usage:
    python3 recompute_verdicts.py --screen data/runs/screen_bankA_ol.jsonl
    python3 recompute_verdicts.py --screen ... --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import date
from pathlib import Path

import config as C
import solvability as S


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--screen", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    p = Path(args.screen)
    if not p.is_absolute():
        p = C.ROOT / p
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    changed = []
    for r in rows:
        old = r["passes_headroom"]
        new = S.passes_headroom(r)
        if old != new:
            changed.append((r["item_id"], r["baseline"], old, new))
        r["passes_headroom"] = new

    print(f"{p.name}: {len(rows)} records, {len(changed)} verdict(s) changed")
    for item, base, old, new in changed:
        print(f"  {item:<12} baseline={base!r}")
        print(f"               {'PASS' if old else 'REJECT'} -> "
              f"{'PASS' if new else 'REJECT'}")
    if not changed:
        print("  (nothing to do)")
        return

    kept = sum(1 for r in rows if r["passes_headroom"])
    print(f"\nretention {kept}/{len(rows)} = {kept / len(rows):.0%}")

    if not args.apply:
        print("\n--apply not given: nothing written")
        return

    # The pre-change file is AUDIT TRAIL, not a scratch backup: it is the only
    # record that these verdicts were derived later rather than measured, and
    # for `A-L05-03` it is the difference between a 14/40 and a 15/40 bank.
    #
    # Two things were wrong with `p.suffix + ".bak"`. It fell outside the
    # `QUARANTINE_*.jsonl` convention that CLAUDE.md and the spec treat as
    # never-deleted, so a reader enumerating the trail missed it entirely. And
    # `copy2` overwrites, so a second `--apply` that changed anything would
    # destroy the original -- the one file the project says must never be
    # deleted. Naming it from the content hash makes both impossible.
    keep = p.with_name(
        f"QUARANTINE_{p.stem}_pre_verdict_recompute_"
        f"{date.today().isoformat()}_"
        f"{hashlib.sha256(p.read_bytes()).hexdigest()[:8]}{p.suffix}")
    if keep.exists():
        sys.exit(f"refusing: {keep.name} already exists -- these exact bytes "
                 f"have already been preserved, so this rewrite would be a "
                 f"second pass over an already-recomputed file")
    shutil.copy2(p, keep)
    with p.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"\nwrote {p.name} (pre-change copy kept at {keep.name})")


if __name__ == "__main__":
    main()
