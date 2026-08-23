"""Draw the pre-registered Bank B screening sample.

WHY A SAMPLE AND NOT THE WHOLE BANK. Bank B retains 185 items after mechanical
selection. Screening all of them costs ~6.8 h, which is affordable -- but the
items that survive then enter the main study, and that is not:

    retained items   generate+solve+placebo      wall clock
             29           30,276 calls              18.5 h
             59           61,596 calls              37.6 h
            159          165,996 calls             101.4 h

At ~70% retention the full bank would contribute ~130 items and the study would
take over four days of continuous backend availability, on a quota-limited free
tier that has already lapsed twice. The design is only runnable if Bank B
contributes a sample.

This is the same failure the v1 grid had (§4.6): a volume that nobody multiplied
out until it was too late. It is caught here by measuring the per-item cost on
the live screen (132 s/item, 2.20 s/call) and extrapolating BEFORE committing.

WHY THIS IS NOT CHERRY-PICKING. The sample is drawn:

  - BEFORE any screening, so no baseline value can influence it;
  - under `config.SEED`, so it is reproducible and cannot be redrawn until a
    convenient set appears;
  - stratified by option count, because 3-, 4- and 5-option items have chance
    rates of 1/3, 1/4 and 1/5 and therefore different headroom -- an unstratified
    draw would confound bank with option count;
  - to a size fixed in advance from the runtime budget, not from the results.

The drawn ids are written to a file, IN ORDER, and hashed. The unsampled
remainder stays in the released bank, so a reader can screen it themselves and
check that our sample was not favourable.

DRAW ONCE, AT N_MAX. CONSUME A PREFIX.

Proportional allocation does not nest: a draw of 20 and a draw of 45 from the
same population under the same seed share only about half their items
(measured: 10 of 20). So a two-stage design must NOT draw 20, screen them, then
draw 45 -- that discards most of stage 1 and, worse, offers a redraw at every
size, which is exactly the freedom the pre-registration is meant to remove.

The protocol is therefore: draw ONCE at the largest n that might ever be needed,
and treat the stored ORDER as fixed. Stage 1 screens the first k items; stage 2
continues from item k+1 of the SAME file. `run.py screen --limit` plus the
resume logic does this naturally, because it consumes items in file order and
skips those already recorded.

The refusal to overwrite an existing sample enforces the "draw once" half. The
ordering in the file enforces the rest.

Usage:
    python3 sample_bank_b.py --n 45          # draw and write
    python3 sample_bank_b.py --n 45 --dry    # show the strata, write nothing
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
from datetime import date
from pathlib import Path

import config as C


def stratified_sample(items: list[dict], n: int, seed: int = C.SEED) -> list[dict]:
    """Proportional allocation over option-count strata, largest-remainder.

    Proportional rather than equal allocation: the point of Bank B is to be a
    faithful public anchor, so the sample should look like the bank. Equal
    allocation would over-represent the rare 5-option items and change the
    bank's own composition, which is one of the things being compared.
    """
    by_k: dict[int, list[dict]] = collections.defaultdict(list)
    for it in items:
        by_k[len(it["options"])].append(it)

    total = len(items)
    # Largest remainder, so the strata sum to exactly n rather than to n+-2.
    exact = {k: n * len(v) / total for k, v in by_k.items()}
    alloc = {k: int(v) for k, v in exact.items()}
    short = n - sum(alloc.values())
    for k in sorted(exact, key=lambda k: (-(exact[k] - alloc[k]), k))[:short]:
        alloc[k] += 1

    rng = random.Random(seed)
    drawn: dict[int, list[dict]] = {}
    for k in sorted(by_k):
        pool = sorted(by_k[k], key=lambda i: i["id"])   # deterministic order
        take = min(alloc[k], len(pool))
        drawn[k] = rng.sample(pool, take)

    # Return in DRAW order, interleaved across strata -- never sorted by id.
    #
    # This is what makes a PREFIX usable as a stage-1 pilot. Sorting by id would
    # look tidier and be quietly wrong: Bank B ids carry the source chapter
    # (`B-question4_4_1`), so the alphabetically-first 20 items are the
    # earliest chapters, i.e. the easiest topics. A pilot drawn that way would
    # estimate retention on a systematically unrepresentative subset and then
    # size the whole second stage from it.
    #
    # Interleaving proportionally means every prefix is itself approximately
    # stratified, so the pilot has the same option-count mix as the full draw.
    out: list[dict] = []
    idx = {k: 0 for k in drawn}
    while any(idx[k] < len(drawn[k]) for k in drawn):
        # Take from whichever stratum is furthest behind its target share.
        k = min((k for k in drawn if idx[k] < len(drawn[k])),
                key=lambda k: (idx[k] / len(drawn[k])) if drawn[k] else 1.0)
        out.append(drawn[k][idx[k]])
        idx[k] += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, required=True,
                    help="N_MAX: the largest number of items that might be "
                         "screened. NOT the pilot size -- see the note below.")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    src = C.BANKS_DIR / "bank_b.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    items = data["items"]

    dest = C.BANKS_DIR / "bank_b_sample.json"
    if dest.exists() and not args.dry:
        raise SystemExit(
            f"refusing to overwrite {dest.name}: the sample is pre-registered and "
            f"redrawing it after seeing any screen result would destroy that. "
            f"Delete it deliberately if you really mean to.")

    sample = stratified_sample(items, args.n)
    before = collections.Counter(len(i["options"]) for i in items)
    after = collections.Counter(len(i["options"]) for i in sample)

    print(f"bank B: {len(items)} items -> sample {len(sample)}")
    print(f"  {'opts':>4} {'bank':>6} {'share':>7} {'sample':>7} {'share':>7}")
    for k in sorted(before):
        print(f"  {k:>4} {before[k]:>6} {before[k]/len(items):>7.1%} "
              f"{after[k]:>7} {after[k]/len(sample):>7.1%}")

    if args.dry:
        print("--dry: nothing written")
        return

    out = {
        "bank_id": "B",
        "sample_of": src.name,
        "drawn": date.today().isoformat(),
        "n_population": len(items),
        "n_sample": len(sample),
        "seed": C.SEED,
        "method": ("stratified by option count, proportional allocation with "
                   "largest remainder, drawn before any screening"),
        "rationale": ("full-bank screening would contribute ~130 retained items "
                      "and a ~101 h main study; the sample is sized from the "
                      "measured per-item cost, not from any result"),
        "source_bank_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
        "sampled_ids": [i["id"] for i in sample],
        "items": sample,
    }
    payload = json.dumps(out, indent=2, ensure_ascii=False).encode()
    dest.write_bytes(payload)
    print(f"wrote {dest.name}  sha256 {hashlib.sha256(payload).hexdigest()[:16]}...")
    print("The unsampled remainder stays in bank_b.json so a reader can screen "
          "it and check the sample was not favourable.")


if __name__ == "__main__":
    main()
