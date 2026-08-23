"""Compare two screens of the same bank, item by item.

Built to answer one question honestly: **did the token-budget fix (§4.13) change
any retention verdict, or only the parse rate?** The quarantined pilot screen
(600-token budget) and the corrected screen (2400) cover the same bank, so the
comparison is direct.

WHY "NO FLIPS" IS NOT AUTOMATICALLY REASSURING. Truncation bias moves the
baseline by a small amount. A small movement can only change a verdict for items
NEAR the retention threshold, so a comparison that happens to contain only
extreme items (baseline 0.00-0.03 or 1.00) will report zero flips no matter how
large the bias is. Reporting that as evidence of robustness would be a selection
effect dressed up as a result.

This script therefore reports flips AND the distance of each compared item from
the threshold, and refuses to summarise robustness when the near-threshold items
are absent from the overlap.

Usage:
    python3 compare_screens.py \\
        --a data/runs/QUARANTINE_screen_bankA_ol_truncation_budget600_2026-08-16.jsonl \\
        --b data/runs/screen_bankA_ol.jsonl
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

import config as C

#: An item this close to the threshold could plausibly flip on a small baseline
#: shift. Items further away cannot, so their agreement is uninformative.
NEAR_THRESHOLD = 0.10


def resolve(spec: str) -> Path:
    """Accept a repo-relative path, matching `recompute_verdicts.py`.

    The usage block above says `--a data/runs/...`, which is relative to the
    REPO root, while the script must be run from `harness/` for `import config`
    to work. So the documented command could not run from either directory: from
    `harness/` the path does not exist, and from the root the import fails. A
    tool nobody can invoke as documented is a tool that gets invoked wrongly
    under time pressure -- and this one decides whether the §4.29 re-screen
    changed a retention verdict.
    """
    p = Path(spec)
    if p.exists():
        return p
    if not p.is_absolute():
        alt = C.ROOT / spec
        if alt.exists():
            return alt
    raise SystemExit(f"no such screen file: {spec} (also tried {C.ROOT / spec})")


def load(path: Path) -> dict[str, dict]:
    return {r["item_id"]: r for r in
            (json.loads(l) for l in path.read_text().splitlines() if l.strip())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="earlier screen (JSONL)")
    ap.add_argument("--b", required=True, help="later screen (JSONL)")
    args = ap.parse_args()

    a, b = load(resolve(args.a)), load(resolve(args.b))
    thr = (1.0 / 3.0) + C.HEADROOM_MARGIN
    both = sorted(set(a) & set(b))
    if not both:
        raise SystemExit("no items in common")

    print(f"threshold {thr:.3f}   overlap {len(both)} items "
          f"(A has {len(a)}, B has {len(b)})\n")
    print(f"{'item':<12}{'A base/parsed':>16}{'B base/parsed':>16}"
          f"{'d(base)':>9}{'dist(thr)':>10}  flip")

    flips, near, near_flips = 0, 0, 0
    for i in both:
        ra, rb = a[i], b[i]
        d = rb["baseline"] - ra["baseline"]
        dist = min(abs(ra["baseline"] - thr), abs(rb["baseline"] - thr))
        is_near = dist <= NEAR_THRESHOLD
        near += is_near
        flip = ra["passes_headroom"] != rb["passes_headroom"]
        flips += flip
        near_flips += flip and is_near
        print(f"{i:<12}"
              f"{ra['baseline']:>7.2f}/{ra['parsed_fraction']:.2f}"
              f"{'P' if ra['passes_headroom'] else 'R':>3}"
              f"{rb['baseline']:>10.2f}/{rb['parsed_fraction']:.2f}"
              f"{'P' if rb['passes_headroom'] else 'R':>3}"
              f"{d:>+9.2f}{dist:>10.2f}  {'FLIP' if flip else ''}"
              f"{'  <-near' if is_near else ''}")

    pa = st.mean(a[i]["parsed_fraction"] for i in both)
    pb = st.mean(b[i]["parsed_fraction"] for i in both)
    md = st.mean(abs(b[i]["baseline"] - a[i]["baseline"]) for i in both)
    print(f"\nmean parsed fraction  {pa:.3f} -> {pb:.3f}")
    print(f"mean |baseline shift| {md:.3f}")
    print(f"verdict flips         {flips}/{len(both)}")

    print()
    if near == 0:
        print(f"NO CONCLUSION ON ROBUSTNESS. None of the {len(both)} compared items")
        print(f"lies within {NEAR_THRESHOLD} of the threshold, so no baseline shift of the")
        print(f"observed size ({md:.3f} mean) could have flipped one. Zero flips here is")
        print("arithmetic, not evidence. Re-run once the near-threshold items are")
        print("present in both screens.")
    else:
        print(f"{near} item(s) within {NEAR_THRESHOLD} of the threshold; "
              f"{near_flips} of them flipped.")
        print("Those are the only items whose agreement is informative.")

    # Items present in A but not yet in B, ordered by how close they sit to the
    # threshold: the ones that matter most for this comparison.
    pending = sorted(set(a) - set(b), key=lambda i: abs(a[i]["baseline"] - thr))
    if pending:
        print(f"\nawaiting re-measurement (nearest the threshold first):")
        for i in pending[:6]:
            print(f"  {i:<12} A base={a[i]['baseline']:.2f}  "
                  f"dist={abs(a[i]['baseline'] - thr):.2f}")


if __name__ == "__main__":
    main()
