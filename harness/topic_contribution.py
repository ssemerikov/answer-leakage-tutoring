"""Measure how much the topic clause contributes to baseline identifiability.

WHY. The topic phrase is supplied in both arms so it cancels in the leak
difference, but the headroom screen thresholds the BASELINE alone -- and the two
banks label topics at completely different granularity (§4.16):

    Bank A   60 items, 51 topics  -> 1.2 items per topic (per-item concepts)
    Bank B  185 items, 22 topics  -> 8.4 items per topic (chapter headings)

"on the subject of translation models" over {translator, compiler, interpreter}
nearly restates the question. "on the subject of iteration and loops", shared by
37 items, does not. If that difference drives the banks' retention rates apart,
the bank comparison measures our labelling rather than distractor quality.

WHAT THIS MEASURES. The same items screened twice, with and without the topic
clause. The difference is the topic's contribution, per bank. No new machinery:
`baseline_identifiability` already takes `control_topic` and records it.

Run this only when no screen is running -- two stages contending for a
quota-limited backend is how transient failures become aborts.

Usage:
    python3 topic_contribution.py --bank A --n 8 --k 10
"""

from __future__ import annotations

import argparse
import json
import statistics as st

import config as C
import run as R
import solvability as S


def read_rows(out) -> list[dict]:
    """Every row in the output file, not just this invocation's.

    THE FILE, NOT THE INVOCATION. The summary used to average `deltas`, the
    list built by the CURRENT call. Every real run of this script on a
    quota-limited backend is a RESUMED one, so that number described how much
    quota was left rather than the data -- and once every item was done,
    `deltas` was empty and no summary printed at all. Same defect, and the same
    fix, as `measure_budgets.report` before `run.read_budget_file` (spec §4.57).
    """
    if not out.exists():
        return []
    return [json.loads(l) for l in
            out.read_text(encoding="utf-8").splitlines() if l.strip()]


def summarise(rows: list[dict], n_requested: int, n_this_run: int) -> list[str]:
    """Report the mean topic contribution, with its own coverage.

    A partial file is NOT a random subset of the requested sample: the draw is
    made under `C.SEED` and consumed in a fixed order, so what a partial run
    holds is a prefix. It is reported as descriptive only, in the same spirit as
    `cap_sensitivity`'s coverage gate (§4.59) -- the difference being that no
    decision hangs on this number, so it is labelled rather than withheld.
    """
    if not rows:
        return []
    vals = [r["topic_contribution"] for r in rows]
    extra = f", {n_this_run} measured this run" if n_this_run else ""
    out = [f"\nmean topic contribution: {st.mean(vals):+.3f}  "
           f"(n={len(vals)} of {n_requested} requested{extra})"]
    if len(vals) < n_requested:
        out.append(
            f"  PARTIAL: {n_requested - len(vals)} item(s) unmeasured. The draw "
            f"is consumed in a fixed order, so this mean is DESCRIPTIVE ONLY -- "
            f"run to completion before quoting it.")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", default="A")
    ap.add_argument("--n", type=int, default=8, help="items to test")
    ap.add_argument("--k", type=int, default=10, help="calls per solver per arm")
    ap.add_argument("--roster", default="ol")
    args = ap.parse_args()

    rost = R.roster(args.roster)
    solvers, path = rost["solvers"], rost["path"]
    data = R.load_bank(args.bank)
    mcq = [i for i in data["items"] if i.get("type") == "mcq"]

    # Deterministic subset under the study seed: this is a measurement, not a
    # search for items that make the point.
    import random
    rng = random.Random(C.SEED)
    items = rng.sample(mcq, min(args.n, len(mcq)))

    out = R.RUNS / f"topic_contribution_bank{args.bank.upper()}_{args.roster}.jsonl"
    done = R.load_done(out, "item_id")

    print(f"[topic] bank {args.bank.upper()} n={len(items)} k={args.k}/solver/arm "
          f"roster={args.roster}")
    deltas = []
    for it in items:
        if it["id"] in done:
            continue
        with_t = S.baseline_identifiability(it, solvers=solvers, k=args.k,
                                            path=path, control_topic=True)
        without = S.baseline_identifiability(it, solvers=solvers, k=args.k,
                                             path=path, control_topic=False)
        d = with_t["baseline"] - without["baseline"]
        deltas.append(d)
        rec = {
            "item_id": it["id"], "bank": args.bank.upper(), "roster": args.roster,
            "topic": it.get("topic"), "k_per_solver": args.k,
            "baseline_with_topic": with_t["baseline"],
            "baseline_without_topic": without["baseline"],
            "topic_contribution": d,
            "parsed_with": with_t["parsed_fraction"],
            "parsed_without": without["parsed_fraction"],
        }
        R.append(out, rec)
        print(f"  {it['id']:<12} with={with_t['baseline']:.2f} "
              f"without={without['baseline']:.2f}  delta={d:+.2f}", flush=True)

    for line in summarise(read_rows(out), len(items), len(deltas)):
        print(line)
    print(f"-> {out}")


if __name__ == "__main__":
    main()
