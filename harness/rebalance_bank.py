"""Balance key positions in an item bank by permuting option order.

WHY. Bank A as authored placed the key at option 1 in 10 items and option 2 in
30, and **never at option 3** (chi2 = 35.0 against uniform over 40 three-option
items). That is a construction defect with three consequences:

  1. The stated chance rate of 1/3 is not the guessing rate. A solver that has
     merely noticed the last option is never keyed guesses at 1/2; one that
     favours the middle option scores 0.75. The headroom screen's threshold
     (`chance + 0.15`) is calibrated against a chance level the bank does not
     have.
  2. It hands a position-biased solver most of the bank for free, and MCQ
     position bias in language models is well documented.
  3. Any leakage estimate inherits the same distortion, because the baseline
     term is measured on the same skewed positions.

It was found by comparison with Bank B, the independently authored anchor:
Runestone's human textbook authors balance positions (chi2 = 0.1 / 8.2 / 4.2),
so the defect is specific to the bank we wrote. Catching a flaw in the authored
bank is exactly what the public anchor exists for.

WHAT THIS DOES, AND WHAT IT DELIBERATELY DOES NOT. Item content is untouched:
no stem, option text, key, topic or id changes. Only the ORDER of options
changes, and `answer_index` is updated to follow the key. Target key positions
are dealt round-robin so the marginal distribution is as uniform as the item
count allows, and the remaining options are placed by a seeded shuffle. The
whole operation is deterministic under `config.SEED`.

This is not tuning. It is applied to every item uniformly, it is blind to any
measurement, and the screen is re-run from scratch afterwards. The pre-fix bank
is preserved beside it so the change is auditable.

Usage:
    python3 rebalance_bank.py --bank A            # writes bank_a.json, keeps a v1 copy
    python3 rebalance_bank.py --bank A --check    # report only, change nothing
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import re
import shutil
from pathlib import Path

import config as C


def key_distribution(items: list[dict]) -> dict[int, collections.Counter]:
    by_n: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for i in items:
        if i.get("type") == "mcq":
            by_n[len(i["options"])][i["answer_index"]] += 1
    return by_n


def chi_square(counts: collections.Counter, n_options: int) -> float:
    total = sum(counts.values())
    if not total:
        return 0.0
    expected = total / n_options
    return sum((counts.get(k, 0) - expected) ** 2 / expected for k in range(n_options))


def rebalance(items: list[dict], seed: int = C.SEED) -> list[dict]:
    """Permute options so key positions are as uniform as the counts allow."""
    rng = random.Random(seed)
    # Deal target positions round-robin WITHIN each option-count group, so a
    # bank mixing 3- and 5-option items stays balanced in each group rather
    # than only in aggregate.
    groups: dict[int, list[dict]] = collections.defaultdict(list)
    for it in items:
        if it.get("type") == "mcq":
            groups[len(it["options"])].append(it)

    out_by_id: dict[str, dict] = {}
    for n_opt, group in groups.items():
        order = sorted(group, key=lambda i: i["id"])
        rng.shuffle(order)
        for pos, it in enumerate(order):
            target = pos % n_opt
            opts = list(it["options"])
            key_text = opts[it["answer_index"]]
            others = [o for j, o in enumerate(opts) if j != it["answer_index"]]
            rng.shuffle(others)
            new_opts = others[:target] + [key_text] + others[target:]
            assert len(new_opts) == n_opt
            assert new_opts[target] == key_text
            new = dict(it)
            new["options"] = new_opts
            new["answer_index"] = target
            if "answer" in new:
                new["answer"] = key_text
            # `answer_forms` encodes the key's POSITION in strings like
            # "option 2" / "answer 2", which permuting the options invalidates.
            # Omitting this is why 27 of 40 Bank A items carried forms pointing
            # at the pre-rebalance position: the P2/P3 verifier would have
            # blocked responses naming the wrong option (false positive) and
            # passed responses naming the right one (false negative), on 68% of
            # the bank. Two components each holding the key's position, only one
            # of them updated.
            if new.get("answer_forms"):
                new["answer_forms"] = [
                    re.sub(r"^(option|answer)\s*\d+$",
                           lambda m: f"{m.group(1)} {target + 1}", f.strip(),
                           flags=re.I)
                    for f in new["answer_forms"]
                ]
            out_by_id[it["id"]] = new

    return [out_by_id.get(i["id"], i) for i in items]


def report(label: str, items: list[dict]) -> None:
    for n_opt, counts in sorted(key_distribution(items).items()):
        dist = [counts.get(k, 0) for k in range(n_opt)]
        print(f"  {label:<8} {n_opt}-option (n={sum(dist):>3}): {dist}  "
              f"chi2={chi_square(counts, n_opt):.1f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", default="A")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    # Rebalancing applies to a SOURCE bank, never to a drawn sample: the sample
    # is taken from an already-rebalanced bank, and permuting it again would
    # give the sample a different option order from the bank it claims to
    # represent -- and from the locators already published for Bank B.
    if args.bank.lower() in ("bs",):
        raise SystemExit(
            f"refusing to rebalance {args.bank!r}: it is a drawn sample. "
            f"Rebalance the source bank before sampling.")
    path = C.BANKS_DIR / f"bank_{args.bank.lower()}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    before = data["items"]

    print(f"{path.name}  sha256 {hashlib.sha256(path.read_bytes()).hexdigest()[:16]}...")
    report("before", before)
    after = rebalance(before)
    report("after", after)

    if args.check:
        print("--check: nothing written")
        return

    # Content invariants. A rebalancer that altered an item would be far worse
    # than the imbalance it fixes, so this is checked rather than trusted.
    by_id = {i["id"]: i for i in before}
    for new in after:
        old = by_id[new["id"]]
        if old.get("type") != "mcq":
            continue
        assert sorted(new["options"]) == sorted(old["options"]), new["id"]
        assert new["options"][new["answer_index"]] == old["options"][old["answer_index"]], new["id"]
        assert new["stem"] == old["stem"] and new["id"] == old["id"]
        for f in new.get("answer_forms", []):
            m = re.fullmatch(r"(?:option|answer)\s*(\d+)", f.strip(), re.I)
            assert not m or int(m.group(1)) == new["answer_index"] + 1, (
                f"{new['id']}: answer_form {f!r} does not match the key at "
                f"option {new['answer_index'] + 1}")

    backup = path.with_name(f"bank_{args.bank.lower()}_v1_unbalanced.json")
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f"pre-fix bank preserved as {backup.name}")

    data["items"] = after
    data["rebalanced"] = {
        "seed": C.SEED,
        "reason": ("key positions were [10, 30, 0] over 40 three-option items "
                   "(chi2=35.0): option 3 was never keyed, so the effective "
                   "guessing rate was not the stated 1/3"),
        "method": "option order permuted; stems, option texts and keys unchanged",
        "found_by": "comparison with Bank B, the independently authored anchor",
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {path.name}  new sha256 "
          f"{hashlib.sha256(path.read_bytes()).hexdigest()[:16]}...")
    print("NOTE: the bank hash changed; the screen must be re-run from scratch "
          "and any frozen bank re-frozen.")


if __name__ == "__main__":
    main()
