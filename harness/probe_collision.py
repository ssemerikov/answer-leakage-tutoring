"""Measure whether solvers ever answer with an option's VALUE instead of its position.

WHY ONLY THIS MATTERS FOR ONE ITEM. `checker.index_text_collisions` finds items
whose keyed option's text is an in-range integer differing from its position; a
value-reply on those is scored as a miss, which DEFLATES the baseline. Deflation
can only make an item look more retainable, so it cannot flip a REJECT. It can
flip a PASS: if the true baseline is higher than measured, an item we retained
might not deserve retention.

So the exposure that matters is exactly: collision-exposed items that PASSED.
On Bank A that is `A-L07-02` (baseline 0.30, threshold 0.483).

WHAT THIS DOES. Re-issues the identical masked-stem prompt -- the instrument is
not changed -- and records the RAW reply text, which the screen does not keep.
Counting how often the reply equals the key's value rather than its position
measures the exposure directly instead of bounding it by argument.

Usage:
    python3 probe_collision.py --item A-L07-02 --bank A --k 20
"""

from __future__ import annotations

import argparse
import collections
import json

import backends as B
import checker
import config as C
import run as R
import solvability as S


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", required=True)
    ap.add_argument("--bank", default="A")
    ap.add_argument("--roster", default="ol")
    ap.add_argument("--k", type=int, default=20)
    args = ap.parse_args()

    data = R.load_bank(args.bank)
    it = next((i for i in data["items"] if i["id"] == args.item), None)
    if it is None:
        raise SystemExit(f"no item {args.item} in bank {args.bank}")

    exposed = {h["id"]: h for h in checker.index_text_collisions([it])}
    if args.item not in exposed:
        print(f"{args.item} is not collision-exposed; nothing to measure.")
        return
    h = exposed[args.item]
    key_pos, key_val = h["key_index"], int(h["key_text"])
    print(f"{args.item}: options {it['options']}  key at position {key_pos} "
          f"with text {h['key_text']!r}")
    print(f"a reply of {key_val!r} would be scored as position {key_val}, not {key_pos}\n")

    rost = R.roster(args.roster)
    prompt = S._MASKED_BASELINE.format(
        options=S._format_options(it["options"]),
        topic=S._topic_clause(it, True))

    replies: dict[str, collections.Counter] = {}
    for m in rost["solvers"]:
        c: collections.Counter = collections.Counter()
        for _ in range(args.k):
            r = B.chat(m, [{"role": "user", "content": prompt}],
                       path=rost["path"], temperature=C.SOLVER_TEMPERATURE,
                       max_tokens=C.solver_max_tokens(m))
            c[(r.content or "").strip()[:40] if r.ok else f"<FAIL {r.error_kind}>"] += 1
        replies[m] = c
        print(f"{m}")
        for text, n in c.most_common():
            parsed = S.parse_choice(text, len(it["options"]))
            flag = ""
            if parsed == key_val and key_val != key_pos:
                flag = "   <-- AMBIGUOUS: value-reply and position-reply collide here"
            print(f"   {n:>3}x {text!r:<26} -> position {parsed}{flag}")
        print()

    ambiguous = sum(n for c in replies.values() for t, n in c.items()
                    if S.parse_choice(t, len(it["options"])) == key_val)
    total = sum(sum(c.values()) for c in replies.values())
    print(f"replies parsing to the ambiguous position: {ambiguous}/{total}")

    # Counting ambiguous replies is not the answer; the answer is whether the
    # VERDICT could change. Value-replies would have been scored as misses, so
    # correcting them can only RAISE the baseline. Compute how many corrections
    # the item can absorb before it crosses the threshold, and compare that with
    # what was observed. A first version of this script stopped at "treat
    # retention as uncertain", which left the actual question to a manual
    # follow-up -- and an unanswered caveat in a paper is worse than a bound.
    scr_path = R._jsonl_path("screen", args.bank, args.roster)
    if not scr_path.exists():
        print(f"\n(no screen at {scr_path.name}; cannot bound the verdict)")
        return
    rec = next((r for r in
                (json.loads(l) for l in scr_path.read_text().splitlines() if l.strip())
                if r["item_id"] == args.item), None)
    if rec is None:
        print(f"\n({args.item} not in {scr_path.name}; cannot bound the verdict)")
        return

    hits = sum(d["hits"] for d in rec["per_solver"].values())
    calls = sum(d["calls"] for d in rec["per_solver"].values())
    thr = rec["chance"] + C.HEADROOM_MARGIN
    headroom = 0
    while S.at_or_below((hits + headroom + 1) / calls, thr):
        headroom += 1

    print(f"\nscreen record: {hits}/{calls} = {rec['baseline']:.3f}, "
          f"threshold {thr:.3f}, verdict "
          f"{'PASS' if rec['passes_headroom'] else 'REJECT'}")
    print(f"corrections this item can absorb before the verdict flips: {headroom}")
    print(f"ambiguous replies observed in {total} independent calls: {ambiguous}")
    if not rec["passes_headroom"]:
        print("\nVERDICT ROBUST: the item was discarded, and value-replies can "
              "only raise the baseline, which discards it more firmly.")
    elif ambiguous == 0:
        print("\nVERDICT ROBUST: no reply could have been a value-reply.")
    elif ambiguous <= headroom:
        print(f"\nVERDICT ROBUST: even if EVERY one of the {total} calls that "
              f"could have been a value-reply were one, the baseline would stay "
              f"at or below the threshold. Retention does not depend on the "
              f"parsing convention for this item.")
    else:
        print(f"\nVERDICT AT RISK: {ambiguous} ambiguous replies against "
              f"{headroom} of headroom. Report this item's retention as "
              f"contingent on the parsing convention.")


if __name__ == "__main__":
    main()
