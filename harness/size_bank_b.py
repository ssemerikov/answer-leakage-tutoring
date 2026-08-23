"""Size the Bank B screening sample from observed retention and the time budget.

THE PROBLEM. Bank A retains far fewer items on the Ollama panel than on the Kiro
panel (§4.9: 38% vs 69% over the same items), so Bank A alone may contribute
~15 items where the analysis wants item fixed effects across
4 generators x 3 solicitations x 4 regimes = 48 cells. Bank B has to make up the
shortfall.

DO NOT "CORRECT" THE 3 BACK TO A 4. This line read 3, was changed to 4 on
2026-08-17 with a comment defending it ("there are four, S0-S3, which is also
what R3's five pre-enumerated contrasts imply"), and §4.43 established on
2026-08-19 that the ORIGINAL was right: S1 was cut before any data existed
(§4.6), so the realised design is 4 x 4 x 3 = 48 cells and R3 is m = 3, not
five contrasts. The 2026-08-17 edit reasoned from `C.SOLICITATIONS`, which
still holds all four entries -- frames come from the ROSTER, never from that
constant, and `measure_budgets.py` carries the same lesson from the same cause.
Nothing here computes from this number (`--target` is supplied by the caller),
which is exactly why the wrong one survived two days of green checks.
But Bank B's retention rate is exactly what nobody knows: the whole hypothesis is
that textbook-authored distractors are less guessable than ours, and if that is
true its retention is higher, and fewer items need screening.

Sizing from a guess would be circular. Sizing from the data is a two-stage
design, and it is only legitimate if it cannot touch WHICH items are used.

THE DESIGN.

  Stage 0  Draw N_max items ONCE, under config.SEED, stratified by option count
           (sample_bank_b.py). The DRAW and its ORDER are fixed here, before any
           Bank B measurement exists. N_max is the largest number that might
           ever be screened, NOT the pilot size: proportional allocation does
           not nest, so re-drawing at a different n yields a different set
           (measured: a draw of 20 and a draw of 45 share 10 items). Drawing
           once and consuming a prefix is what makes stage 2 unable to reselect.
  Stage 1  Screen the first `pilot` items of that fixed order. Estimate retention
           with a Wilson interval -- never a point estimate, because at n=20 the
           interval is wide enough to change the answer.
  Stage 2  Screen as many further items OF THE SAME PRE-DRAWN ORDER as the
           estimate says are needed to reach the target retained count.

Stage 2 changes only HOW MANY of an already-fixed sequence are used. It cannot
select favourable items, because the sequence was fixed at stage 0 and is
consumed in order. That is what separates this from screening until the numbers
look good.

We size against the LOWER Wilson bound, not the point estimate, because it is
the only choice that meets the target with 95% confidence in one pass. A second
screening run is not merely slow: it restarts against a quota that may have
lapsed, and it invites re-deciding the target once retention is known.

That choice has a COST, and it is the opposite of what this docstring used to
claim. Sizing on the lower bound screens MORE items, so if true retention is
higher than the bound we retain more than the target -- overshoot is a
consequence of the policy, not an argument for it. At the 15-item mark the
exposure is concrete: the bound (36%) says screen 45, but at the point estimate
(60%) those 45 yield ~27 retained against a shortfall of 16. Every surplus
retained item costs CALLS_PER_RETAINED calls in the main study, so ~11 surplus
items is roughly 7 extra hours.

Overshoot buys precision rather than nothing, so it is not waste. But it is
spent quota, and the alternative -- consuming retained items in the pre-drawn
order up to the target and stopping -- would be legitimate, since that order was
fixed before any Bank B measurement existed. No such truncation is implemented;
`screened_items` returns everything that passes headroom. Deciding whether to
add it is a design call, not a code cleanup, and it belongs to the authors.

Usage:
    python3 size_bank_b.py --target 40                 # what's needed, given Bank A
    python3 size_bank_b.py --target 40 --pilot-file data/runs/screen_bankBS_ol.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import namedtuple
from pathlib import Path

import analyse as A
import config as C

#: Screen timing, as ONE object rather than two constants.
#:
#: WHY A TUPLE AND NOT TWO NAMES. This was `SEC_PER_ITEM` plus a separate
#: `SEC_PER_ITEM_MEASURED_AT`, with the comment "bump both together" -- and on
#: 2026-08-17 an edit updated the version string and silently failed to update
#: the number. The guard then compared "2.3.0" to "2.3.0", declared the timing
#: fresh, and printed hour estimates from the SUPERSEDED 132 s -- 3.3x
#: optimistic, with no warning, in the tool whose whole job is to stop exactly
#: that. A guard that compares two variables cannot notice that only one of them
#: moved.
#:
#: The measurement and the conditions it holds under are one fact, so they are
#: one value. A partial update is now a syntax error rather than a silent
#: 3.3x error. This is the §4.33 lesson applied to a constant instead of a rule.
#:
#: MEASURED 2026-08-17 under study 2.3.0: 438 s/item, from the two active
#: intervals of the Bank B re-screen (396 s/item over 5 items, then 455 over 12;
#: reproduce with `timing_from_screen.py --from-log <supervisor log>`). Quota
#: stalls are excluded -- they are the provider's clock, not work -- but the
#: overhead of items ABORTED mid-measurement IS included, because a
#: quota-limited run really does pay it.
#:
#: It is 3.3x the 2.2.0 value of 132, and that increase is the direct cost of
#: the 4.33 budget raise: calls that used to hit the cap and stop early now run
#: to completion. Every hour figure below therefore roughly TRIPLES against
#: anything quoted before 2026-08-17. Read it alongside 4.35: the per-item
#: distribution is heavily skewed, so this mean understates the chance of a run
#: stalling on one pathological item.
ScreenTiming = namedtuple("ScreenTiming", "sec_per_item measured_at")
TIMING = ScreenTiming(438.0, "2.3.0")

CALLS_PER_RETAINED = 1044  # generate + solve + placebo, reduced design

#: Stage-1 pilot size. The two-stage design in the docstring only means anything
#: if "the pilot" names a FIXED set of items, and nothing in the harness enforced
#: that boundary: `run.py screen --limit N` takes N items NOT YET DONE, so a
#: resumed run walks straight past 20 into stage 2 (observed 2026-08-17 18:29 --
#: the supervisor relaunched at done=17 with the original `--limit 20` and
#: continued to sample position 21). That is legitimate work in the pre-drawn
#: order, but `retention()` read every row in the file and the report labelled
#: the result "Bank B pilot", so the recorded 13/20 would have silently become
#: 14/22 under the same name -- a figure already written into spec 4.26 and 4.34,
#: no longer reproducible from the file that supposedly holds it.
#:
#: The estimate itself may pool: the order was fixed under seed before any Bank B
#: measurement existed, so items 21+ are as unbiased as items 1-20. Only the
#: LABEL was wrong. Both are reported, and sizing uses the pooled one because
#: more screened items is a better estimate of the same quantity.
PILOT_N = 20


def timing_is_fresh() -> bool:
    return C.STUDY_VERSION == TIMING.measured_at


def hours(n_items: float) -> str:
    """Hours for `n_items`, or an explicit refusal when the timing is stale.

    SUPPRESSES THE HOUR FIGURE ONLY. An earlier version of this aborted the
    whole tool, which also disabled the redraw guard -- a CORRECTNESS check
    about which items may be consumed, entirely independent of timing. Refusing
    to answer a question nobody asked, in order to protect an estimate, is worse
    than the staleness: the item counts below are exactly as valid as before.
    """
    if not timing_is_fresh():
        return "(hours unknown: timing stale)"
    return f"{n_items * TIMING.sec_per_item / 3600:.1f}h"


def _timing_warning() -> str:
    return (
        f"\nWARNING: TIMING.sec_per_item={TIMING.sec_per_item:.0f}s was measured "
        f"under study {TIMING.measured_at}, but the harness is at "
        f"{C.STUDY_VERSION}. Latency tracks tokens emitted, so a token-budget "
        f"change invalidates it. ITEM COUNTS BELOW ARE UNAFFECTED; only the hour "
        f"estimates are withheld, and those are what the authors weigh when "
        f"deciding how much quota to spend. Re-measure:\n"
        f"    python3 timing_from_screen.py --screen data/runs/screen_bankBS_ol.jsonl\n"
        f"    python3 timing_from_screen.py --from-log <supervisor log>   "
        f"# when the screen is STALLED on quota and cannot be sampled\n"
        f"It prints the replacement line to paste, generated from this module's "
        f"own field order rather than hard-coded.")


def retention(path: Path, order: list[str] | None = None,
              first_n: int | None = None) -> tuple[int, int] | None:
    """Retained/screened, optionally restricted to the first `first_n` of `order`.

    RESTRICT BY SAMPLE ORDER, NOT FILE ORDER. Rows are appended as items finish,
    and a resumed run appends whatever was left -- so file position tracks the
    draw only by coincidence, and a slice of the file is not a prefix of the
    sample. Ranking by the seed-locked order says what the two-stage design
    means: the first `first_n` items that were DRAWN.
    """
    if not path.exists():
        return None
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if first_n is not None and order is not None:
        rank = {iid: k for k, iid in enumerate(order)}
        rows = [r for r in rows if rank.get(r["item_id"], len(order)) < first_n]
    if not rows:
        return None
    return sum(1 for r in rows if r["passes_headroom"]), len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=40,
                    help="total retained MCQ items wanted across both banks")
    ap.add_argument("--bank-a", default="data/runs/screen_bankA_ol.jsonl")
    ap.add_argument("--pilot-file", default="data/runs/screen_bankBS_ol.jsonl")
    ap.add_argument("--n-max", type=int, default=None,
                    help="items that may be consumed; defaults to the size of the "
                         "drawn sample when the pilot is a `bs` screen, else all "
                         "of Bank B")
    args = ap.parse_args()

    if not timing_is_fresh():
        print(_timing_warning())

    root = C.ROOT

    # WHICH POPULATION MAY BE CONSUMED. This defaulted to 185 -- all of Bank B --
    # while the pilot file it is paired with screens the DRAWN SAMPLE (`bs`, 60
    # items, fixed under seed 2026-08-16). Those are different populations, and
    # the cap is subtracted from `b_n`, so the old default could recommend
    # screening more items than the sample contains. Acting on that means
    # screening un-drawn Bank B items, which is a second draw -- and proportional
    # allocation does not nest, so a second draw is a different sample, not a
    # bigger one (spec 4.14). Derive the cap from the sample the pilot actually
    # screened rather than letting a default from before the draw survive it.
    sample_path = C.BANKS_DIR / "bank_b_sample.json"
    drawn, order = None, None
    if "bankBS" in Path(args.pilot_file).name and sample_path.exists():
        sample = json.loads(sample_path.read_text(encoding="utf-8"))["items"]
        drawn, order = len(sample), [i["id"] for i in sample]

    if args.n_max is None:
        args.n_max = drawn if drawn is not None else 185
    elif drawn is not None and args.n_max > drawn:
        raise SystemExit(
            f"--n-max {args.n_max} exceeds the {drawn} items drawn into "
            f"bank_b_sample.json. The sample is consumed in its fixed order; "
            f"screening beyond it requires a new draw, which is forbidden -- "
            f"proportional allocation does not nest, so a redraw at a different "
            f"n is a DIFFERENT sample (spec 4.14). If the target genuinely "
            f"cannot be met from {drawn} items, the pre-registered answer is a "
            f"shorter bank and wider intervals, not more items.")

    if drawn is not None:
        print(f"consumable: {drawn} items drawn into bank_b_sample.json "
              f"(fixed order, seed-locked)")

    a = retention(root / args.bank_a)
    if a is None:
        raise SystemExit(f"no Bank A screen at {args.bank_a}")
    a_keep, a_n = a
    a_lo, a_hi = A.wilson_ci(a_keep, a_n)
    complete = a_n >= 40
    print(f"Bank A: {a_keep}/{a_n} retained = {a_keep / a_n:.0%}  "
          f"Wilson95 [{a_lo:.2f}, {a_hi:.2f}]"
          f"{'' if complete else '   (INCOMPLETE -- projection only)'}")

    # Project Bank A's final retained count. Use the point estimate for the
    # expectation but show the interval, because the shortfall Bank B must cover
    # depends on it.
    a_proj = a_keep if complete else round(40 * a_keep / a_n)
    a_proj_lo = a_keep if complete else round(40 * a_lo)
    print(f"        projected final: {a_proj} of 40 (low end {a_proj_lo})")

    b = retention(root / args.pilot_file)
    if b is None:
        print(f"\nBank B: no pilot screen yet ({args.pilot_file}).")
        print("Retention is UNKNOWN and must not be guessed -- it is the hypothesis.")
        shortfall = max(0, args.target - a_proj)
        print(f"\nShortfall to cover: {shortfall} items.")
        print("\nn needed, as a function of Bank B retention:")
        print(f"  {'retention':>10}{'items to screen':>18}{'screen time':>14}")
        for r in (0.30, 0.40, 0.50, 0.60, 0.70, 0.80):
            need = min(args.n_max, -(-shortfall // max(r, 0.01)))
            print(f"  {r:>10.0%}{int(need):>18}{hours(int(need)):>14}")
        print("\nRECOMMENDATION: two-stage. Draw N_max under the seed now, screen a")
        print("pilot of 20 in that fixed order, then extend the SAME order to the")
        print("size this table implies. Extending a pre-drawn sequence cannot")
        print("select favourable items; screening until the numbers look good can.")
        return

    b_keep, b_n = b
    b_lo, b_hi = A.wilson_ci(b_keep, b_n)

    # Name the stage-1 pilot separately once the screen has run past it, so the
    # figure quoted in spec 4.26/4.34 stays reproducible from this file.
    pilot = retention(root / args.pilot_file, order, PILOT_N)
    if pilot and pilot[1] < b_n:
        p_keep, p_n = pilot
        p_lo, p_hi = A.wilson_ci(p_keep, p_n)
        print(f"\nBank B stage-1 pilot (first {PILOT_N} drawn): {p_keep}/{p_n} = "
              f"{p_keep / p_n:.0%}  Wilson95 [{p_lo:.2f}, {p_hi:.2f}]")
        print(f"Bank B all screened so far:            {b_keep}/{b_n} = "
              f"{b_keep / b_n:.0%}  Wilson95 [{b_lo:.2f}, {b_hi:.2f}]  <- sizes below")
        print("Pooling is legitimate: the order was seed-locked before any Bank B "
              "measurement existed,\nso items past the pilot are as unbiased as "
              "items inside it.")
    else:
        print(f"\nBank B pilot: {b_keep}/{b_n} retained = {b_keep / b_n:.0%}  "
              f"Wilson95 [{b_lo:.2f}, {b_hi:.2f}]")

    shortfall = max(0, args.target - a_proj - b_keep)
    if shortfall == 0:
        print(f"\nTarget {args.target} already met ({a_proj} + {b_keep}). Screen no more.")
        return

    # Size against the LOWER bound: it is the only choice that meets the target
    # in one pass with 95% confidence. It is also the choice that can OVERSHOOT
    # -- see the module docstring; the surplus is real and costs main-study
    # hours, and it is accepted deliberately rather than overlooked.
    need_lo = min(args.n_max - b_n, int(-(-shortfall // max(b_lo, 0.01))))
    need_pt = min(args.n_max - b_n, int(-(-shortfall // max(b_keep / b_n, 0.01))))
    print(f"\nshortfall {shortfall} items")
    print(f"  sized on point estimate ({b_keep / b_n:.0%}): screen {need_pt} more"
          f"  [{hours(need_pt)}]")
    print(f"  sized on lower bound    ({b_lo:.0%}): screen {need_lo} more"
          f"  [{hours(need_lo)}]   <-- use this")

    total = a_proj + b_keep + int(need_lo * b_lo)
    study = (f"~{total * CALLS_PER_RETAINED * (TIMING.sec_per_item / 60) / 3600:.1f}h"
             if timing_is_fresh() else "(hours unknown: timing stale)")
    print(f"\nprojected total retained {total}  ->  main study {study}")


if __name__ == "__main__":
    main()
