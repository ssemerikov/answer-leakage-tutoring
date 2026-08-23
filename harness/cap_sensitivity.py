#!/usr/bin/env python3
"""Did the 400-token generation cap change the answer? (spec 4.56)

WHY THIS EXISTS RATHER THAN A RE-RUN OR AN ARGUMENT. `pipelines.run` omitted
`max_tokens`, so every tutor reply was generated at `backends.chat`'s 400-token
default. On Bank A, 129 of 2,080 generator calls sat at exactly 400 -- replies cut
off mid-sentence, returned as ok=True, and analysed as though complete.

Two responses were available and both are bad. Re-running Bank A's `generate` and
`solve` costs ~14,000 calls and rewrites every number in the manuscript. Merely
disclosing the cap leaves the reader -- and us -- with no idea whether it mattered.

This measures it instead, on the only cells where it could possibly matter: the
ones that reached the cap. Regenerate those at the measured budget, re-solve them
through the SAME estimator against the SAME screen baselines and the SAME placebo
records, and compare `leak_adj` cell by cell. About 900 calls, i.e. 6% of a
re-run, for a paired estimate of the cap's effect.

A 400-token cap is a real deployment parameter -- plenty of deployed tutors cap
reply length -- so the defect was never that the configuration is unrealistic. It
is that it was neither chosen nor measured nor disclosed. This closes the last
two, and tells us whether the first needs revisiting.

NOTHING HERE TOUCHES THE COMPLETED ARTEFACTS. Output goes to `capcheck_*` files;
`generate_bankA_ol.jsonl` and `solve_bankA_ol.jsonl` are read-only inputs. If the
cap turns out to matter, the re-run is a decision taken with a number in hand.
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as C          # noqa: E402
import pipelines as P       # noqa: E402
import run as R             # noqa: E402
import solvability as S     # noqa: E402

CAP = C.GENERATOR_BUDGET_BEFORE_MEASUREMENT["value"]


def capped_cells(bank: str, rost: str) -> list[dict]:
    """Generate rows with at least one call at the old cap.

    `>= CAP` and not `== CAP`: a provider that counts one token differently, or a
    future cap change, must not make this silently select nothing. Selecting too
    much would only widen the check; selecting nothing would report "no effect".
    """
    path = R._jsonl_path("generate", bank, rost)
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if not (r.get("ok") and r.get("response")):
            continue
        if any((c.get("completion_tokens") or 0) >= CAP for c in (r.get("calls") or [])):
            out.append(r)
    return out


def decide(cs: dict) -> tuple[int, str]:
    """Re-run, or disclose? The decision, separated so it can be tested.

    A VERDICT FLIP IS THE CRITERION, not the size of the displacement. A shift
    of half a standard error that leaves every conclusion intact costs ~14,000
    calls to remove and changes nothing a reader would act on; a shift that
    turns a rejected contrast into a retained one means the published estimate
    is an artefact of an unmeasured library default, and no amount of disclosure
    substitutes for re-running it.
    """
    if cs["verdict_flips"]:
        return 1, (
            f"RE-RUN REQUIRED. The ceiling changes the verdict of "
            f"{', '.join(cs['verdict_flips'])}, so the published estimate is an "
            f"artefact of it and bank {cs['bank']}'s generate and solve must be "
            f"re-run.")
    return 0, (
        f"DISCLOSURE IS SUFFICIENT. No pre-registered contrast changes its "
        f"verdict and the largest displacement is {cs['max_shift_in_se']:.2f} "
        f"standard errors, so re-running bank {cs['bank']} would rewrite every "
        f"number without changing a conclusion. Report the paired difference "
        f"and this re-estimation.")


def contrast_sensitivity(bank: str, rost: str, orig: dict,
                         newval: dict) -> dict:
    """Re-estimate every pre-registered contrast with the uncapped cells in it.

    WHY THE PAIRED MEAN IS NOT THE ANSWER ON ITS OWN. The paired difference says
    how much the ceiling moved the affected CELLS. What the manuscript claims is
    a property of the CONTRASTS, and the two do not follow from one another: the
    affected cells are unevenly spread over the regimes, so a difference of the
    same size can displace a contrast by anything between nothing and its whole
    magnitude depending on where it lands.

    The paper argued the direction from cell counts -- 79 of 127 affected cells
    are P0, the reference level, so the bias "makes the reported mitigation
    effects conservative". That is an argument, and this function is what checks
    it. MEASURED, IT IS WRONG IN SIGN: the affected P0 cells came back slightly
    LOWER, while the 23 affected P3 cells came back much higher, so |P1-P0| and
    |P3-P0| both SHRINK when the ceiling is removed and the published estimates
    slightly overstate mitigation rather than understating it. The magnitude is
    negligible either way (<= 0.51 se, no verdict changes), which is exactly why
    the sign had to be measured rather than reasoned about -- an argument that
    lands on the wrong side of zero and is never checked reads as a safeguard.

    Substituting into the frame and re-running `results.families` uses the study's
    own estimator, its own covariate decision and its own Holm thresholds. A
    reimplementation here would be a second copy of the analysis rule (spec
    §4.33), and the number it produced would not be comparable to the published
    one.
    """
    import results as RS
    solve = RS._rows(R._jsonl_path("solve", bank, rost))
    gen = RS._rows(R._jsonl_path("generate", bank, rost))
    frame, _ = RS.analysis_frame(solve, gen)
    # The published fit excluded the pre-registered covariate as post-treatment
    # (spec §4.49). Ask the data rather than hard-coding the answer, so this
    # tracks the primary fit if that decision ever changes.
    dec = RS.covariate_decision(frame)
    covs = tuple(dec.get("admitted") or ())

    swapped, n = [], 0
    for row in frame:
        row = dict(row)
        if row.get("cell_key") in newval:
            row["leak_adj"] = newval[row["cell_key"]]
            n += 1
        swapped.append(row)

    base = RS.families(frame, banks_present=1, complete=True, covariates=covs)
    alt = RS.families(swapped, banks_present=1, complete=True, covariates=covs)

    out = {"bank": bank.upper(), "roster": rost, "substituted": n,
           "frame_rows": len(frame), "covariates": list(covs), "families": {}}
    worst, flips = 0.0, []
    for fam in base:
        if "contrasts" not in base[fam]:
            continue
        rec = {}
        for k, b in base[fam]["contrasts"].items():
            a = alt[fam]["contrasts"][k]
            shift = a["estimate"] - b["estimate"]
            same = bool(a["reject"]) == bool(b["reject"])
            worst = max(worst, abs(shift) / b["se"])
            if not same:
                flips.append(f"{fam}:{k}")
            rec[k] = {"published": b["estimate"], "uncapped": a["estimate"],
                      "shift": shift, "se": b["se"],
                      "shift_in_se": abs(shift) / b["se"],
                      "reject_published": bool(b["reject"]),
                      "reject_uncapped": bool(a["reject"]),
                      "same_verdict": same}
        out["families"][fam] = rec
    out["max_shift_in_se"] = worst
    out["verdict_flips"] = flips
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bank", default="A")
    ap.add_argument("--roster", default="ol")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    bank, rost = args.bank, args.roster

    rec_r = R.roster(rost)
    # `k_hint`, the same key `stage_solve` reads -- the hinted arm's call count.
    # Reading a different k would change the denominator and make the paired
    # comparison a comparison of two different measurements.
    solvers, k, path = rec_r["solvers"], rec_r["k_hint"], rec_r["path"]
    data = R.load_bank(bank)
    by_id = {i["id"]: i for i in data["items"]}

    cells = capped_cells(bank, rost)
    gen_out = R.RUNS / f"capcheck_generate_bank{bank.upper()}_{rost}.jsonl"
    solve_out = R.RUNS / f"capcheck_solve_bank{bank.upper()}_{rost}.jsonl"
    print(f"[capcheck] {len(cells)} cells reached the {CAP}-token cap")

    # ---- the ORIGINAL leak_adj for those cells, from the completed solve
    orig = {}
    for line in (R._jsonl_path("solve", bank, rost)
                 ).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("leak_adj") is not None:
            orig[r["cell_key"]] = r["leak_adj"]

    if not args.report_only:
        # ---- regenerate at the MEASURED budget
        done_gen = R.load_done_ok(gen_out, "cell_key")
        todo = [c for c in cells if c["cell_key"] not in done_gen]
        if args.limit:
            todo = todo[:args.limit]
        print(f"[capcheck] regenerate: done={len(done_gen)} todo={len(todo)}",
              flush=True)
        for n, c in enumerate(todo, 1):
            budget = C.generator_max_tokens(c["model"])
            if budget is None:
                sys.exit(f"no measured generation budget for {c['model']}; run "
                         f"`measure_budgets.py --bank {bank} --roster {rost} "
                         f"--arm generate` first. The whole point of this check "
                         f"is to compare against a MEASURED budget.")
            res = P.run(by_id[c["item_id"]], c["model"], c["solicitation_key"],
                        c["regime"], path=path, max_tokens=budget)
            row = res.as_record() if hasattr(res, "as_record") else dict(res.__dict__)
            row["cell_key"] = c["cell_key"]
            row["rep"] = c.get("rep")
            # ONE NAME FOR ONE FACT. This wrote `generation_budget` while
            # `run.stage_generate` wrote `generator_max_tokens` for the identical
            # quantity, so an audit of generate-shaped rows found the field
            # missing in exactly the file created to audit the ceiling. Named to
            # match `solver_max_tokens`, which solve rows already carried.
            row["generator_max_tokens"] = budget
            row["superseded_budget"] = CAP
            R.append(gen_out, row)
            if n % 20 == 0 or n == len(todo):
                print(f"  [{n}/{len(todo)}] regenerated", flush=True)

        # ---- re-solve, through the SAME estimator the real stage uses
        # The SAME screen file and the SAME keying as `stage_solve`, including its
        # panel guard: a baseline recorded by a different solver panel cannot be
        # differenced against this one, and silently doing so would make the
        # comparison meaningless in exactly the way this check exists to avoid.
        screen_path = R._jsonl_path("screen", bank, rost)
        screen = {}
        for line in screen_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                s = json.loads(line)
                screen[s["item_id"]] = s
        screened_solvers = set(next(iter(screen.values()), {}).get("per_solver", {}))
        if screened_solvers and not screened_solvers & set(solvers):
            sys.exit(f"screen in {screen_path.name} was run by "
                     f"{sorted(screened_solvers)}, which shares no solver with "
                     f"roster {rost!r}. Refusing to difference against another "
                     f"panel's baselines.")
        placebo = {}
        pb_path = R._jsonl_path("placebo", bank, rost)
        if pb_path.exists():
            for line in pb_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    s = json.loads(line)
                    placebo[s["item_id"]] = s

        regen = [json.loads(l) for l in
                 gen_out.read_text(encoding="utf-8").splitlines() if l.strip()]
        done_solve = R.load_done(solve_out, "cell_key")
        todo2 = [r for r in regen
                 if r["cell_key"] not in done_solve and r.get("ok")
                 and r.get("response")]
        if args.limit:
            todo2 = todo2[:args.limit]
        print(f"[capcheck] re-solve: done={len(done_solve)} todo={len(todo2)}",
              flush=True)
        for n, r in enumerate(todo2, 1):
            item = by_id[r["item_id"]]
            base = screen[r["item_id"]]
            rec = S.leak_score(item, r["response"], base["baseline"],
                               generator=r["model"], solvers=solvers, k=k,
                               path=path)
            rec["by_solver"] = S.per_solver_leak(base, rec)
            rec["cell_key"] = r["cell_key"]
            rec["regime"] = r["regime"]
            rec["solicitation_key"] = r["solicitation_key"]
            # TOLERANT ON READ, CANONICAL ON WRITE. Rows regenerated before the
            # rename carry `generation_budget`; accepting both means the 127 rows
            # already on disk need no migration, while only one name is ever
            # written from here on.
            rec["generator_max_tokens"] = r.get(
                "generator_max_tokens", r.get("generation_budget"))
            prec = placebo.get(r["item_id"])
            pb = S.placebo_for_solvers(prec, rec["solvers"]) if prec else None
            rec["placebo_hinted"] = pb
            rec["leak_adj"] = None if pb is None else rec["hinted"] - pb
            R.append(solve_out, rec)
            if n % 20 == 0 or n == len(todo2):
                print(f"  [{n}/{len(todo2)}] re-solved", flush=True)

    # ---- paired comparison
    if not solve_out.exists():
        print("[capcheck] nothing re-solved yet")
        return 0
    pairs, solved_rows = [], []
    for line in solve_out.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("leak_adj") is None or r["cell_key"] not in orig:
            continue
        pairs.append((orig[r["cell_key"]], r["leak_adj"]))
        solved_rows.append(r)
    if not pairs:
        print("[capcheck] no comparable pairs yet")
        return 0
    d = [b - a for a, b in pairs]
    m = st.mean(d)
    se = st.stdev(d) / len(d) ** 0.5 if len(d) > 1 else float("nan")
    print(f"\n[capcheck] {len(pairs)} of {len(cells)} paired cells "
          f"(capped at {CAP} -> measured budget)")
    print(f"  mean leak_adj capped   {st.mean(a for a, _ in pairs):.4f}")
    print(f"  mean leak_adj uncapped {st.mean(b for _, b in pairs):.4f}")
    print(f"  paired difference      {m:+.4f} (se {se:.4f}, "
          f"95% CI [{m - 1.96 * se:+.4f}, {m + 1.96 * se:+.4f}])")

    # THE DECISION IS GATED ON COVERAGE, and the guidance is part of the
    # computation rather than a footnote to it. Section 4.41's lesson was that a
    # partial run produced `P1-P0 = -1.317` after clearing two guards, and the
    # question there was whether the computation should run AT ALL. Here it
    # should -- the paired descriptives are honest at any coverage -- but the
    # RECOMMENDATION is not, because the cells are consumed in file order, not
    # drawn at random. Realised at 31 of 127: P0 was 74% of the partial against
    # 62% of the target, and P0 is the leakiest regime, so the partial mean is
    # biased upward by its own progress.
    done_reg = collections.Counter(r["regime"] for r in solved_rows)
    want_reg = collections.Counter(c["regime"] for c in cells)
    if len(pairs) < len(cells):
        print(f"\n  COVERAGE {len(pairs)}/{len(cells)} -- the paired difference "
              f"above is DESCRIPTIVE ONLY and the re-run decision is not yet "
              f"answerable.")
        print(f"  Cells are re-solved in file order, so the shortfall is not a "
              f"random subset. Regime mix so far vs target:")
        for k in sorted(want_reg):
            got, want = done_reg.get(k, 0), want_reg[k]
            print(f"    {k}  {got:3}/{want:3}"
                  f"  ({(got / len(pairs) * 100) if pairs else 0:4.1f}% of done"
                  f" vs {want / len(cells) * 100:4.1f}% of target)")
        print("  Re-run to completion before reading it as an answer.")
        return 0

    # FULL COVERAGE ONLY, for the same reason the paired mean is gated: a
    # contrast re-estimated with two thirds of its affected cells substituted is
    # not a sensitivity analysis of anything.
    newval = {r["cell_key"]: r["leak_adj"] for r in solved_rows
              if r.get("leak_adj") is not None}
    cs = contrast_sensitivity(bank, rost, orig, newval)
    print(f"\n[capcheck] pre-registered contrasts re-estimated with the "
          f"{cs['substituted']} uncapped cells substituted "
          f"({cs['substituted'] / cs['frame_rows']:.2%} of the frame):")
    for fam, recs in cs["families"].items():
        print(f"  {fam}")
        for k, v in recs.items():
            print(f"    {k:<7} {v['published']:+.4f} -> {v['uncapped']:+.4f}  "
                  f"(shift {v['shift']:+.4f} = {v['shift_in_se']:.2f} se)  "
                  f"{'same verdict' if v['same_verdict'] else '*** FLIPPED ***'}")
    art = C.ROOT / "data" / f"capcheck_contrasts_bank{bank.upper()}_{rost}.json"
    art.write_text(json.dumps(cs, indent=1, sort_keys=True), encoding="utf-8")
    print(f"  -> {art.relative_to(C.ROOT)}")

    rc, msg = decide(cs)
    print("\n" + msg)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
