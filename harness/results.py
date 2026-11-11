"""Assemble the pre-registered Results from the run records. No new model calls.

WHY THIS EXISTS. `analyse.py` has held the estimators since before any data
existed, and `verify_numbers.py` checks the manuscript against the runs -- but
nothing joined the two. Every number in Results was going to be computed by hand
in a session, pasted into `paper.tex`, and then "verified" by a checker that only
proves a literal is *present* in the file. That is how the drift `verify_numbers`
was written to catch gets in through the front door.

WHAT IT DOES. Reads `solve` (primary), `generate` (guardrail behaviour, latency,
the prompt-token covariate) and `placebo`/`screen` (provenance), applies the
analysis pre-specified in design spec section 6 -- item fixed effects, Holm
within pre-enumerated families, no test on the model factor -- and writes one
machine-readable JSON. Nothing here chooses a specification: the families, the
outcome and the covariate are all fixed in the spec, and this file is only
allowed to compute them.

THREE THINGS IT REFUSES TO DO, each one a failure this project has already had:

- **Impute a missing `leak_adj`.** A null means no placebo record covers that
  solver subset. Section 3.2c says such a cell is excluded and counted, never
  read as zero -- and zero is not neutral here: the placebo correction runs from
  -0.144 to +0.500 per item (4.37), so imputing zero would import the raw
  `leak_hat` for exactly the cells where the correction matters most.
- **Report a partial run as if it were the study.** `--partial` is required when
  `solve` has fewer rows than `generate` has usable responses, and the output is
  stamped `complete: false` with the coverage fraction.
- **Silently drop a pre-registered contrast.** `analyse.wald_contrasts` raises on
  an absent level, because a smaller family loosens every Holm threshold in it.

Usage:
    python3 results.py --bank A --roster ol [--partial] [-v]
    python3 results.py --bank A --roster ol --out /path/results.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics as st
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyse as A       # noqa: E402
import config as C        # noqa: E402
import run as R           # noqa: E402

# The Holm families, transcribed from design spec section 6. R1 (model factor)
# was VACATED before data: the power analysis made that factor descriptive and
# CI-only, so there was no p-value for Holm to correct. R4 needs two banks.
FAMILIES: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "R2_mitigation": ("regime", [("P1", "P0"), ("P2", "P1"),
                                 ("P3", "P2"), ("P3", "P0")]),
    # S1 WAS REMOVED FROM THE DESIGN BEFORE DATA (spec 4.6, user-approved
    # 2026-08-16), so the family is three contrasts, not the five section 6 still
    # listed. Transcribing section 6 verbatim put S1-S0 and S3-S1 back in, and the
    # missing-level guard in `wald_contrasts` is what surfaced it -- on partial
    # data, as "S1 is not present", which read like an incomplete run rather than
    # a level that will never arrive.
    "R3_solicitation": ("solicitation", [("S2", "S0"), ("S3", "S0"), ("S3", "S2")]),
}
#: Family-wise alpha for Holm within each family (spec section 6).
HOLM_ALPHA = 0.05
#: Pre-registered covariate. It is admitted to the primary fit ONLY if the
#: post-treatment diagnostic clears it (spec 4.49): `prompt_tokens` was recorded
#: so the Kiro hosted path's context DRIFT could be modelled, and on the
#: controlled path that this study used there is no injected context -- what is
#: left is the harness's own prompt, and the mitigation preamble is part of the
#: treatment. Both fits are always reported; only which one is primary changes.
CANDIDATE_COVARIATES = ("prompt_tokens",)
PRIMARY_OUTCOME = "leak_adj"
#: `leak_adj = hinted - placebo`, each a proportion, so the outcome is in [-1, 1].
#: Passed to `wald_contrasts` so an impossible estimate raises instead of printing.
OUTCOME_RANGE = (-1.0, 1.0)
SECONDARY_OUTCOME = "leak_hat"
FACTORS = ("model", "solicitation", "regime", "bank")


def _sha256(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _quantile(xs: list[float], q: float) -> float | None:
    """Nearest-rank quantile. Explicit rather than `statistics.quantiles`, whose
    default interpolation invents values between observed latencies."""
    if not xs:
        return None
    s = sorted(xs)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


def analysis_frame(solve_rows: list[dict], gen_rows: list[dict]) -> tuple[list[dict], dict]:
    """Rename run fields to the factor names the pre-specified model uses.

    `solve` records call the generator `generator` and the solicitation
    `solicitation_key`; the spec's model is written over `model` and
    `solicitation`. The rename happens HERE, once, rather than in each caller --
    a frame missing `model` would not error, it would silently fit a model
    without the factor, and `_fit_ols` drops absent factors by design.
    """
    # prompt_tokens is a spec-mandated covariate (injected-context drift). Take
    # the FIRST call of the cell: under P3 later calls are repairs, whose prompts
    # include the rejected response and so measure repair length, not drift.
    tokens: dict[str, int] = {}
    for g in gen_rows:
        calls = g.get("calls") or []
        first = next((c for c in calls if c.get("prompt_tokens") is not None), None)
        if first is not None:
            tokens[g["cell_key"]] = int(first["prompt_tokens"])

    frame, notes = [], {"missing_prompt_tokens": 0}
    for r in solve_rows:
        ck = r.get("cell_key")
        if ck not in tokens:
            notes["missing_prompt_tokens"] += 1
        frame.append({
            "cell_key": ck,
            "item_id": r["item_id"],
            "model": r["generator"],
            "solicitation": r["solicitation_key"],
            "regime": r["regime"],
            "bank": r.get("bank"),
            "blocked": bool(r.get("blocked")),
            "leak_hat": r.get("leak_hat"),
            "leak_adj": r.get("leak_adj"),
            "hinted": r.get("hinted"),
            "baseline": r.get("baseline"),
            "placebo_hinted": r.get("placebo_hinted"),
            "parsed_fraction": r.get("parsed_fraction"),
            # The WILLINGNESS channel, promised in the Method "alongside the
            # primary outcome throughout" so a reader can see which channel
            # moved. It was carried in the records and never reported (4.53).
            "hinted_cond_acc": r.get("hinted_cond_acc"),
            "prompt_tokens": tokens.get(ck),
            "by_solver": r.get("by_solver"),
            "per_solver": r.get("per_solver"),
            # Carried so `failure_slack` can bound a CONTRAST: the bound is per
            # level, and the level names only exist after this rename.
            "n_failed_calls": r.get("n_failed_calls"),
            "solvers": r.get("solvers"),
        })
    return frame, notes


def covariate_decision(frame: list[dict]) -> dict:
    """Which covariates the primary fit may adjust for, and the evidence.

    NEVER A JUDGEMENT MADE IN A SESSION. Dropping a covariate turned Bank A's R2
    family from four holds into four rejections -- the estimates moved by 0.02
    while the standard errors fell 6.6x -- so this is exactly the decision that
    must be made by a stated criterion applied before the p-values are read, and
    recorded with the numbers that drove it.
    """
    admitted, excluded, diags = [], [], {}
    for cov in CANDIDATE_COVARIATES:
        d = A.post_treatment_diagnostic(frame, covariate=cov)
        diags[cov] = d
        (excluded if d.get("post_treatment") else admitted).append(cov)
    return {"admitted": tuple(admitted), "excluded": tuple(excluded),
            "diagnostics": diags}


def integrity(solve_rows: list[dict], bank: str, rost: str) -> dict:
    """Refuse to analyse records that cannot be pooled. Exits on a hard fault."""
    keys = [r.get("cell_key") for r in solve_rows]
    dupes = [k for k, n in Counter(keys).items() if n > 1]
    versions = Counter(r.get("study_version") for r in solve_rows)
    banks = Counter(r.get("bank") for r in solve_rows)
    rosters = Counter(r.get("roster") for r in solve_rows)
    sigs = Counter(r.get("bank_measurement_sig") for r in solve_rows)

    if dupes:
        sys.exit(f"{len(dupes)} duplicate cell_key(s) in solve, e.g. {dupes[:3]}; "
                 f"a duplicated cell is double-weighted in every mean. Fix the "
                 f"file before analysing.")
    if len(versions) > 1:
        sys.exit(f"solve rows carry {len(versions)} study versions {dict(versions)}; "
                 f"records measured under different budgets are not comparable "
                 f"(spec 4.29). Re-run the superseded rows or quarantine them.")
    if len(sigs) > 1:
        sys.exit(f"solve rows carry {len(sigs)} bank measurement signatures; the "
                 f"items changed mid-run. Refusing to pool.")
    if set(banks) - {bank.upper()} or set(rosters) - {rost}:
        sys.exit(f"solve rows are not all bank={bank.upper()} roster={rost}: "
                 f"banks={dict(banks)} rosters={dict(rosters)}")

    failed = sum(int(r.get("n_failed_calls") or 0) for r in solve_rows)
    kinds: Counter = Counter()
    for r in solve_rows:
        for k, n in (r.get("error_kinds") or {}).items():
            kinds[k] += n
    return {
        "n_rows": len(solve_rows),
        "study_version": next(iter(versions), None),
        "bank_measurement_sig": next(iter(sigs), None),
        "duplicate_cell_keys": 0,
        "n_failed_solver_calls": failed,
        "error_kinds": dict(kinds),
        "mean_parsed_fraction": (
            st.mean([r["parsed_fraction"] for r in solve_rows
                     if r.get("parsed_fraction") is not None])
            if solve_rows else None),
    }


def failure_slack(frame: list[dict], fams: dict = None) -> dict:
    """How far a level mean or a pre-registered contrast could move if every
    failed solver call had instead been answered.

    THE SCREEN HAS `solvability.truncation_slack` AND THE ANALYSIS HAD NOTHING.
    A failed call is scored as a miss in every arm, so on the hint arm it can
    only have DEPRESSED `hinted`, hence `leak_hat`, hence `leak_adj`. For the
    screen that one-sidedness answers a binary question exactly ("can this
    verdict flip?"). Here the outcome is continuous, so there is no verdict to
    flip -- but there is a contrast, and a contrast is NOT protected by the
    one-sidedness. Corrections are one-sided within each level, so a difference
    A-B moves in whichever direction the failures happen to be concentrated:

        A can rise by at most c_A, B by at most c_B, both >= 0,
        so  A-B  lies in  [est - c_B, est + c_A]  and  |shift| <= max(c_A, c_B).

    That matters because failures are not distributed by the design. They arrive
    when a quota window closes, which is a fact about the wall clock and the
    order cells happen to be consumed -- so a run interrupted mid-level biases
    exactly one side of a contrast. Reported, never guarded: the bound is exact,
    so a reader can compare it against the estimate and its standard error and
    decide. Adding a threshold here would be guard n+1 (spec 4.41).

    The denominator is the row's OWN recorded call counts, not `k x |solvers|`
    re-derived from the roster table. Vendor exclusion makes |solvers| vary by
    generator, and the row already records what was actually attempted. If a
    solver dropped out entirely its calls are missing from the sum, which
    SHRINKS the denominator and so OVERSTATES the bound -- the pessimistic
    direction, which is the only safe one for a bound.
    """
    fams = FAMILIES if fams is None else fams
    per_row: list[tuple[dict, float]] = []
    n_failed = n_calls = 0
    for r in frame:
        ps = r.get("per_solver") or {}
        total = sum(int(v.get("calls") or 0) for v in ps.values())
        failed = sum(int(v.get("failed") or 0) for v in ps.values())
        # The row carries the same fact twice -- as a top-level total and as a
        # per-solver breakdown -- and this is the only place they meet. A
        # disagreement means the record is not describing one measurement, so
        # refuse rather than pick a side.
        stated = r.get("n_failed_calls")
        if stated is not None and int(stated) != failed:
            sys.exit(f"{r.get('cell_key')}: n_failed_calls={stated} but per_solver "
                     f"sums to {failed}; the record disagrees with itself.")
        if not total:
            sys.exit(f"{r.get('cell_key')}: no per-solver call counts, so the "
                     f"effect of its failed calls cannot be bounded. An "
                     f"unbounded correction is not a safe one.")
        if failed > total:
            sys.exit(f"{r.get('cell_key')}: {failed} failed of {total} calls.")
        n_failed += failed
        n_calls += total
        per_row.append((r, failed / total))

    def level_bound(factor: str, level: str) -> float:
        xs = [c for r, c in per_row if r.get(factor) == level]
        return st.mean(xs) if xs else 0.0

    out: dict = {
        "n_failed_calls": n_failed,
        "n_solver_calls": n_calls,
        "failure_rate": (n_failed / n_calls) if n_calls else 0.0,
        "max_row_correction": max([c for _, c in per_row], default=0.0),
        "direction": "failed calls are scored as misses, so every correction "
                     "raises leak_adj; a contrast can move either way",
        "families": {},
    }
    for fam, (factor, pairs) in fams.items():
        levels = {lv: round(level_bound(factor, lv), 6)
                  for lv in sorted({r.get(factor) for r, _ in per_row
                                    if r.get(factor) is not None})}
        out["families"][fam] = {
            "factor": factor,
            "level_max_upward_shift": levels,
            "contrast_max_abs_shift": {
                f"{a}-{b}": round(max(level_bound(factor, a),
                                      level_bound(factor, b)), 6)
                for a, b in pairs
            },
        }
    return out


def guardrail(gen_rows: list[dict]) -> dict:
    """P2/P3 behaviour, from `generate`: what the guardrail did, not what it caught.

    Reported as recorded at run time, from the recorded fields; nothing here
    re-runs the verifier. CORRECTED 2026-11 (spec 4.66): an earlier version of
    this docstring said `answer_forms` were repaired AFTER this run. They were
    not. The answer-form correction (spec 4.38, bank files written 2026-08-18
    09:28) PRECEDED both `ol` generate runs, and every generate row records the
    corrected bank file's hash. Today's checker reproduces every recorded P2/P3
    first-candidate verdict row by row (`exploratory.diagnostic_rederivation`),
    which shows reproducibility, not that the correction had no effect.
    """
    by: dict[str, dict] = {}
    for g in gen_rows:
        d = by.setdefault(g["regime"], {"n": 0, "blocked": 0, "rejections": 0,
                                        "repairs": 0, "repaired_ok": 0})
        d["n"] += 1
        d["blocked"] += bool(g.get("blocked"))
        d["rejections"] += int(g.get("verifier_rejections") or 0)
        d["repairs"] += int(g.get("repair_attempts") or 0)
        if g.get("verifier_rejections") and not g.get("blocked"):
            d["repaired_ok"] += 1
    for reg, d in by.items():
        lo, hi = A.wilson_ci(d["blocked"], d["n"])
        d["blocked_rate"] = d["blocked"] / d["n"] if d["n"] else None
        d["blocked_ci"] = [lo, hi]
    return dict(sorted(by.items()))


def latency(gen_rows: list[dict]) -> dict:
    """Quantiles over generator calls. Serial runs only, as the spec requires --
    the harness makes one call at a time, so every recorded latency qualifies."""
    per_model: dict[str, list[float]] = {}
    for g in gen_rows:
        for c in g.get("calls") or []:
            if c.get("ok") and c.get("latency_ms") is not None:
                per_model.setdefault(c["model"], []).append(float(c["latency_ms"]))
    out = {}
    for m, xs in sorted(per_model.items()):
        out[m] = {"n_calls": len(xs),
                  **{f"p{int(q*100)}_ms": _quantile(xs, q)
                     for q in (0.50, 0.90, 0.95, 0.99)}}
    allx = [x for xs in per_model.values() for x in xs]
    out["_pooled"] = {"n_calls": len(allx),
                      **{f"p{int(q*100)}_ms": _quantile(allx, q)
                         for q in (0.50, 0.90, 0.95, 0.99)}}
    return out


#: The cutoffs `threshold_sensitivity` reports, fixed before the data.
THRESHOLD_GRID = (0.10, 0.20, 0.25, 0.30, 0.40, 0.50)


def threshold_key(t: float) -> str:
    """The key cutoff `t` is stored under here: "0.10", never "0.1".

    (`human_validation_results.json` writes its own grid with `:g`, so the same
    cutoff is "0.1" there. Look a cutoff up through the writer of the file.)
    """
    return f"{t:.2f}"


def exceeds_threshold(v: float, t: float) -> bool:
    """THE binary "did it leak" comparison at cutoff `t`. Inclusive: `>=`.

    One implementation, shared: `exploratory.py` classifies single replies as
    "given" at a cutoff from this grid, and a second copy of the comparison
    would disagree with this file on exactly the replies that sit ON the cut.
    """
    return v >= t


def threshold_sensitivity(rows: list[dict], outcome: str = PRIMARY_OUTCOME) -> dict:
    """The binary "did it leak" rate across a GRID of cutoffs, never one cutoff.

    The spec keeps the continuous outcome primary precisely because a single
    threshold invites "where did that come from". Reporting the whole curve
    answers the question the binary is wanted for -- the rate under the strongest
    mitigation -- without smuggling in a cutoff chosen after seeing the data.
    """
    out = {}
    for t in THRESHOLD_GRID:
        by_reg: dict[str, dict] = {}
        for r in rows:
            v = r.get(outcome)
            if v is None:
                continue
            d = by_reg.setdefault(r["regime"], {"n": 0, "over": 0})
            d["n"] += 1
            d["over"] += exceeds_threshold(v, t)
        for reg, d in by_reg.items():
            lo, hi = A.wilson_ci(d["over"], d["n"])
            d["rate"] = d["over"] / d["n"] if d["n"] else None
            d["ci"] = [lo, hi]
        out[threshold_key(t)] = dict(sorted(by_reg.items()))
    return out


def families(rows: list[dict], banks_present: int, complete: bool,
             covariates: tuple[str, ...] = ("prompt_tokens",)) -> dict:
    """Test each pre-enumerated family, or say why it could not be tested.

    A MISSING LEVEL MEANS DIFFERENT THINGS BEFORE AND AFTER THE RUN FINISHES,
    and the two must not be handled by the same branch. Mid-run, `solve` has
    simply not reached an S1 cell yet -- the design grid is consumed in a fixed
    order, not shuffled -- and reporting the family as pending is honest. On a
    COMPLETE run the same absence is a fault (a renamed level, a dropped cell),
    and testing the four contrasts that remain would loosen every Holm threshold
    in a family sized for five. So the exception propagates when complete.
    """
    out: dict[str, dict] = {}
    for fam, (factor, pairs) in FAMILIES.items():
        try:
            cons = A.wald_contrasts(rows, factor, pairs, outcome=PRIMARY_OUTCOME,
                                    factors=FACTORS, covariates=covariates,
                                    outcome_range=OUTCOME_RANGE)
        except (ValueError, RuntimeError) as e:
            if complete:
                raise
            out[fam] = {"factor": factor, "m": len(pairs),
                        "status": "not_computable_yet",
                        "reason": f"partial run: {e}"}
            continue
        holm = A.holm_bonferroni({k: v.pvalue for k, v in cons.items()})
        out[fam] = {
            "factor": factor,
            "m": len(pairs),
            "contrasts": {k: {**v.as_dict(), **holm[k]} for k, v in cons.items()},
        }
    # R4 is NOT SKIPPED when only one bank is present: a family that vanishes
    # from the output looks like a family that was never planned, and a deviation
    # from a pre-registered plan has to be visible in the artefact, not only in a
    # session transcript.
    #
    # THE FAMILY KEEPS ITS PRE-REGISTERED m = 2 EVEN THOUGH ONLY ONE MEMBER CAN
    # BE TESTED. The bank main effect is not identified under item fixed effects
    # -- bank is constant within an item, so the item dummies already span it
    # (spec 4.47) -- which leaves the joint interaction as the only test.
    #
    # Re-sizing the family to m = 1 would be defensible and is NOT what happens
    # here, because it would raise the surviving test's threshold from alpha/2 to
    # alpha and so make a pre-registered hypothesis EASIER to pass after the data
    # existed. Holm with an m larger than the number of tests still controls FWER;
    # it is merely conservative. Conservative needs no defence, and this project's
    # standing rule is that a threshold may never be loosened after the fact.
    if banks_present < 2:
        out["R4_robustness"] = {
            "status": "not_computable",
            "reason": f"the bank x regime interaction needs 2 banks; "
                      f"{banks_present} present. Run `results.py --pool A,bs` "
                      f"once both banks have solve rows.",
            "m": 2, "m_testable": 1}
    else:
        try:
            r4 = A.bank_regime_interaction(rows, outcome=PRIMARY_OUTCOME,
                                           factors=FACTORS,
                                           covariates=covariates,
                                           outcome_range=OUTCOME_RANGE)
            p_joint = r4["joint_test"]["p"]
            thresh = HOLM_ALPHA / 2          # step 1 of Holm at the pre-reg m
            out["R4_robustness"] = {
                "m": 2, "m_testable": 1, "factor": "bank x regime", **r4,
                "holm": {
                    "test": "joint bank x regime",
                    "p": p_joint,
                    "alpha": HOLM_ALPHA,
                    "m_used": 2,
                    "threshold": thresh,
                    "reject": bool(p_joint < thresh),
                    "note": "threshold is alpha/2 at the PRE-REGISTERED family "
                            "size, not alpha/1 at the testable size: the second "
                            "member is unidentified, not passed, and re-sizing "
                            "after the fact would loosen the survivor.",
                },
            }
        except (ValueError, RuntimeError) as e:
            if complete:
                raise
            out["R4_robustness"] = {"m": 2, "m_testable": 1,
                                    "status": "not_computable_yet",
                                    "reason": f"partial run: {e}"}
    out["R1_model"] = {
        "status": "vacated_before_data",
        "reason": "spec section 6: the model factor is descriptive, CI-only, no "
                  "hypothesis test. A family with no test in it has nothing for "
                  "Holm to correct.",
    }
    return out


def covariate_sensitivity(rows: list[dict], decision: dict) -> dict:
    """Every family refitted under the covariate set that was NOT used.

    Reported unconditionally, so the reader never has to take the exclusion on
    trust. If both fits agree the exclusion did not matter; if they disagree the
    disagreement is the finding, and hiding it would be the misconduct.
    """
    other = tuple(sorted(set(CANDIDATE_COVARIATES) - set(decision["admitted"])))
    if set(other) == set(CANDIDATE_COVARIATES) - set(decision["excluded"]):
        pass  # naming only; the point is that `other` is the complement
    out: dict = {"covariates": list(other),
                 "primary_covariates": list(decision["admitted"]),
                 "families": {}}
    for fam, (factor, pairs) in FAMILIES.items():
        try:
            cons = A.wald_contrasts(rows, factor, pairs, outcome=PRIMARY_OUTCOME,
                                    factors=FACTORS, covariates=other,
                                    outcome_range=OUTCOME_RANGE)
        except (ValueError, RuntimeError) as e:
            out["families"][fam] = {"status": "not_computable", "reason": str(e)}
            continue
        holm = A.holm_bonferroni({k: v.pvalue for k, v in cons.items()})
        out["families"][fam] = {
            "contrasts": {k: {**v.as_dict(), **holm[k]} for k, v in cons.items()}}
    return out


def per_solver_levels(solve_rows: list[dict], placebo_rows: list[dict]) -> dict:
    """The adjusted leakage LEVEL one solver at a time.

    THE RULE THIS EXISTS TO OBEY. The Method states in advance that a low panel
    ICC means "the per-solver estimates --- not the pooled one --- become the
    reportable result". Two of three panels came in low (spec 4.50), so the rule
    fired -- and the manuscript answered it by reporting the pooled figure and
    pointing at the released data. A rule a paper states and then does not apply
    is worse than no rule, because the reader cannot audit a number that was
    never printed. This computes it so it can be printed.

    THE ESTIMATOR MUST MATCH THE POOLED ONE. `leak_adj` is
    `hinted - placebo_hinted`, both pooled over the generator's vendor-excluded
    solver subset, so the per-solver analogue is that same difference taken on
    one solver's own calls: `hinted_s - placebo_s`. Never pair a per-solver
    `hinted` with the POOLED placebo. Placebo willingness is strongly
    solver-specific on this bank -- gemma4 hits 0/12 on items where gpt-oss hits
    5/12 -- so that subtraction would debit one solver's credulity to another's
    account and produce a per-solver "level" that is partly a different solver.

    Descriptive, so honest at any coverage; `mean_ci` is the same instrument
    `describe_by` uses for the by-generator figures, not a second one.
    """
    plc: dict[str, dict[str, float]] = {}
    for r in placebo_rows:
        iid = r.get("item_id")
        if iid is None:
            continue
        if iid in plc:
            sys.exit(f"per_solver_levels: duplicate placebo row for {iid}; the "
                     f"per-solver placebo rate would depend on file order.")
        rates = {}
        for s, v in (r.get("per_solver") or {}).items():
            parsed = v.get("parsed") or 0
            # An unparsed placebo call is not a zero -- it is an absent
            # measurement, and imputing it as a miss would inflate every leak
            # estimate that subtracts it (spec 3.2c).
            rates[s] = (v.get("hits", 0) / parsed) if parsed else None
        plc[iid] = rates

    vals: dict[str, list[float]] = {}
    skipped = {"no_placebo_row": 0, "no_placebo_for_solver": 0, "not_informative": 0}
    for r in solve_rows:
        per = (r.get("by_solver") or {}).get("per_solver") or {}
        item = plc.get(r.get("item_id"))
        if item is None:
            skipped["no_placebo_row"] += len(per)
            continue
        for s, b in per.items():
            if not b.get("informative") or b.get("hinted") is None:
                skipped["not_informative"] += 1
                continue
            pr = item.get(s)
            if pr is None:
                skipped["no_placebo_for_solver"] += 1
                continue
            vals.setdefault(s, []).append(float(b["hinted"]) - pr)

    out = {}
    for s in sorted(vals):
        m, lo, hi = A.mean_ci(vals[s])
        out[s] = {"n": len(vals[s]), "mean": m, "ci_low": lo, "ci_high": hi}
    means = [v["mean"] for v in out.values()]
    return {
        "estimator": "hinted_s - placebo_s, per solver, on that solver's own calls",
        "per_solver": out,
        "n_solvers": len(out),
        "spread": round(max(means) - min(means), 6) if means else None,
        "min_mean": round(min(means), 6) if means else None,
        "max_mean": round(max(means), 6) if means else None,
        "skipped": skipped,
        "note": ("Reported because the panel ICC is low on two of three panels, "
                 "which the pre-registered rule says makes these -- not the "
                 "pooled mean -- the estimates of record. Counts differ by "
                 "solver because vendor exclusion bars a solver from its own "
                 "family's responses."),
    }


def ceiling_sensitivity(solve_rows: list[dict],
                        placebo_rows: list[dict] | None = None) -> dict:
    """The pre-registered ceiling exclusion, actually run.

    THE PROMISE. The Method states the mismatch between the screen's unit and
    the analysis's: retention is decided on the panel-pooled baseline, while the
    within-solver leak is `hinted_s - baseline_s`. Where a solver already
    identifies the key on every masked call its baseline is 1, so its
    within-solver leak cannot exceed zero however completely the reply gives the
    answer away -- the cell is CENSORED, not noisy. The paper reports the count
    and then "pre-register[s] the exclusion of ceiling cells from the
    within-solver analysis as a sensitivity check". This is that check.

    A ceiling cell is an (item, solver) pair with `baseline_s == 1`, which is the
    pre-registered definition and not a quantile of the observed data -- picking
    the cut from the data afterwards would make the check unfalsifiable.

    Censoring is one-directional: it can only SHRINK a within-solver estimate.
    So excluding those cells can only raise the mean, and the sign of the shift
    is a prediction, not a finding. What the check answers is the magnitude --
    whether the conservatism is a rounding matter or a material one.
    """
    plc: dict[str, dict[str, float]] = {}
    for r in (placebo_rows or []):
        rates = {}
        for s, v in (r.get("per_solver") or {}).items():
            parsed = v.get("parsed") or 0
            rates[s] = (v.get("hits", 0) / parsed) if parsed else None
        plc[r.get("item_id")] = rates

    cells: set[tuple] = set()
    ceiling: set[tuple] = set()
    keep_leak, all_leak, keep_adj, all_adj = [], [], [], []
    for r in solve_rows:
        iid = r.get("item_id")
        for s, b in ((r.get("by_solver") or {}).get("per_solver") or {}).items():
            if not b.get("informative"):
                continue
            base, lk = b.get("baseline"), b.get("leak")
            cells.add((iid, s))
            at_ceiling = base is not None and float(base) >= 1.0
            if at_ceiling:
                ceiling.add((iid, s))
            if lk is not None:
                all_leak.append(float(lk))
                if not at_ceiling:
                    keep_leak.append(float(lk))
            pr = plc.get(iid, {}).get(s)
            if pr is not None and b.get("hinted") is not None:
                adj = float(b["hinted"]) - pr
                all_adj.append(adj)
                if not at_ceiling:
                    keep_adj.append(adj)

    def block(all_v, keep_v):
        if not all_v:
            return {"status": "no_data"}
        m0, lo0, hi0 = A.mean_ci(all_v)
        if not keep_v:
            return {"all": {"n": len(all_v), "mean": m0,
                            "ci_low": lo0, "ci_high": hi0},
                    "excluding_ceiling": {"status": "every cell at ceiling"}}
        m1, lo1, hi1 = A.mean_ci(keep_v)
        return {
            "all": {"n": len(all_v), "mean": m0, "ci_low": lo0, "ci_high": hi0},
            "excluding_ceiling": {"n": len(keep_v), "mean": m1,
                                  "ci_low": lo1, "ci_high": hi1},
            "shift": round(m1 - m0, 6),
        }

    return {
        "definition": "an (item, solver) cell whose masked baseline is 1.0",
        "n_cells": len(cells),
        "n_ceiling_cells": len(ceiling),
        "ceiling_cells": sorted(f"{i}|{s}" for i, s in ceiling),
        "within_solver_leak": block(all_leak, keep_leak),
        "within_solver_adjusted_level": block(all_adj, keep_adj),
        "note": ("Censoring at the top can only shrink a within-solver estimate, "
                 "so a non-negative shift is expected; the magnitude is the "
                 "finding. Pre-registered in the Method, not chosen after the "
                 "data (spec 4.53)."),
    }


def option_counts(banks: list[str], rost: str) -> dict:
    """`item_id -> number of options`, read from the FROZEN banks.

    The count is not in the run records, so it has to be joined from the bank
    that was screened. Reading the frozen file rather than the source sample is
    deliberate: the frozen file is the artefact the measurements were taken
    against, and it is the one whose hash the records carry.
    """
    out: dict[str, int] = {}
    for bank in banks:
        # `run.frozen_path` is the ONE place the frozen-bank name is derived --
        # rebuilding it here is how the roster suffix gets dropped by a second
        # copy of the rule (run.frozen_path's own docstring records that lesson).
        d = R.load_frozen(bank, rost)
        if not d:
            continue
        for it in d.get("items", []):
            n = len(it.get("options") or [])
            if n:
                out[it["id"]] = n
    return out


def bank_contrast_by_option_stratum(frame: list[dict], opts: dict) -> dict:
    """The bank contrast, stratified by option count -- as promised in advance.

    THE PROMISE. The Limitations state that the retention threshold is chance
    plus a FIXED margin, so it admits 1.45x chance for a three-option item and
    1.75x for a five-option one; Bank A is entirely three-option and Bank B is
    predominantly four- and five-option. "Any comparison of retention rates
    between the banks is therefore confounded with both. We report the bank
    contrast within the three-option stratum, where the criterion is identical,
    and report the other strata separately."

    So the three-option stratum is the PRE-REGISTERED comparison and the others
    are reported beside it, not pooled into it. Marking which is which in the
    output is the point: a reader must not have to reconstruct from prose which
    stratum carries the argument.

    DESCRIPTIVE, WITH INTERVALS, AND NO TEST. Bank is nested in item (spec 4.47),
    so this contrast cannot be taken under item fixed effects, and it remains
    confounded with topic-label grain even inside a stratum. Adding a p-value
    here would be an unregistered test on a comparison the design cannot clean.
    """
    strata: dict[int, dict[str, list[float]]] = {}
    missing = 0
    for r in frame:
        if r.get(PRIMARY_OUTCOME) is None:
            continue
        n = opts.get(r["item_id"])
        if n is None:
            missing += 1
            continue
        strata.setdefault(n, {}).setdefault(str(r.get("bank")), []).append(
            float(r[PRIMARY_OUTCOME]))

    def se_of(vals: list[float]) -> float:
        if len(vals) < 2:
            return float("nan")
        m = sum(vals) / len(vals)
        var = sum((v - m) ** 2 for v in vals) / (len(vals) - 1)
        return (var / len(vals)) ** 0.5

    out = {}
    for n in sorted(strata):
        by_bank = strata[n]
        block = {}
        for b, vals in sorted(by_bank.items()):
            m, lo, hi = A.mean_ci(vals)
            block[b] = {"n_rows": len(vals), "mean": m,
                        "ci_low": lo, "ci_high": hi}
        entry = {
            "n_options": n,
            "pre_registered_primary": n == 3,
            "criterion": ("identical across banks -- chance 1/3 plus the same "
                          "margin" if n == 3 else
                          f"chance 1/{n} plus the same fixed margin, so the "
                          f"threshold admits a different multiple of chance "
                          f"than the three-option stratum"),
            "by_bank": block,
        }
        banks_here = sorted(block)
        if len(banks_here) == 2:
            a, b = banks_here
            va, vb = by_bank[a], by_bank[b]
            diff = block[b]["mean"] - block[a]["mean"]
            se = (se_of(va) ** 2 + se_of(vb) ** 2) ** 0.5
            entry["difference"] = {
                "contrast": f"{b} - {a}",
                "estimate": round(diff, 6),
                "se": round(se, 6),
                "ci_low": round(diff - 1.959963984540054 * se, 6),
                "ci_high": round(diff + 1.959963984540054 * se, 6),
                "test": "none -- descriptive, and confounded with topic grain",
            }
        else:
            entry["difference"] = {
                "status": "not_computable",
                "reason": f"only bank(s) {banks_here} have rows in this stratum; "
                          f"a between-bank contrast needs both.",
            }
        out[str(n)] = entry

    return {
        "strata": out,
        "items_without_option_count": missing,
        "note": ("The three-option stratum is the pre-registered comparison "
                 "because the retention criterion is identical there; the other "
                 "strata are reported separately and are NOT pooled into it."),
    }


def build(bank: str, rost: str, partial: bool,
          icc_boot: int = 2000) -> dict:
    solve_p = R._jsonl_path("solve", bank, rost)
    gen_p = R._jsonl_path("generate", bank, rost)
    pb_p = R._jsonl_path("placebo", bank, rost)
    scr_p = R._jsonl_path("screen", bank, rost)

    solve_rows = _rows(solve_p)
    if not solve_rows:
        sys.exit(f"no solve records at {solve_p.name}; run `solve` first")
    gen_rows = [g for g in _rows(gen_p) if g.get("ok") and g.get("response")]

    integ = integrity(solve_rows, bank, rost)
    # Coverage against USABLE generate rows, never `wc -l`: the generate file
    # keeps its superseded ok=False rows as an audit trail (spec 4.39), so its
    # line count exceeds its measurements by 179 on Bank A.
    coverage = len(solve_rows) / len(gen_rows) if gen_rows else 0.0
    if len(solve_rows) < len(gen_rows) and not partial:
        sys.exit(f"solve has {len(solve_rows)} of {len(gen_rows)} usable responses "
                 f"({coverage:.1%}); pass --partial to report an incomplete run. "
                 f"It will be stamped complete=false.")

    frame, notes = analysis_frame(solve_rows, gen_rows)
    kept = [r for r in frame if r.get(PRIMARY_OUTCOME) is not None]
    dropped = [r for r in frame if r.get(PRIMARY_OUTCOME) is None]
    banks_present = len({r["bank"] for r in frame})

    # NO INFERENCE ON A PARTIAL RUN. Descriptive means and the guardrail counts
    # are honest at any coverage -- `generate` is complete, and a mean is a mean.
    # The regression and the Holm families are not: on a 2%-complete solve the
    # item dummies absorb the design, and the numbers that come out are shaped by
    # which cells happen to have been reached, not by the factors. Three separate
    # guards each caught a different symptom of that and a fourth would too; the
    # correct response is not to compute it. Coverage is the predicate.
    complete = len(solve_rows) >= len(gen_rows)
    pending = {"status": "not_computable_yet",
               "reason": f"solve is {coverage:.1%} complete ({len(solve_rows)} of "
                         f"{len(gen_rows)} responses). Inference waits for the "
                         f"full run; descriptives below are current."}
    decision = covariate_decision(frame)
    covs = decision["admitted"]
    if complete:
        fit = A.fit_with_item_fixed_effects(kept, outcome=PRIMARY_OUTCOME,
                                            factors=FACTORS, covariates=covs)
        fit_raw = A.fit_with_item_fixed_effects(frame, outcome=SECONDARY_OUTCOME,
                                                factors=FACTORS, covariates=covs)
        primary = {
            "outcome": PRIMARY_OUTCOME,
            "note": fit.note,
            "n_obs": fit.n_obs, "n_items": fit.n_items, "converged": fit.converged,
            "params": {k: round(v, 6) for k, v in fit.params.items()},
            "pvalues": fit.pvalues,
        }
        secondary = {"n_obs": fit_raw.n_obs, "converged": fit_raw.converged,
                     "params": {k: round(v, 6) for k, v in fit_raw.params.items()}}
        fams = families(kept, banks_present, complete=True, covariates=covs)
        sens = covariate_sensitivity(kept, decision)
    else:
        primary, secondary = dict(pending), dict(pending)
        sens = dict(pending)
        fams = {f: dict(pending) for f in FAMILIES}
        fams["R4_robustness"] = dict(pending)
        fams["R1_model"] = {
            "status": "vacated_before_data",
            "reason": "spec section 6: the model factor is descriptive, CI-only, "
                      "no hypothesis test.",
        }
    icc = A.solver_agreement(solve_rows, n_boot=icc_boot)
    # PER RATER PAIR, because there is more than one and the single figure covers
    # only the largest (spec 4.50). Vendor exclusion makes the panel a function of
    # the generator, so "the" ICC silently described half the study.
    icc_panels = A.solver_agreement_by_panel(solve_rows, n_boot=icc_boot)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bank": bank.upper(),
        "roster": rost,
        "study_version": C.STUDY_VERSION,
        "complete": complete,
        "coverage": {"solve_rows": len(solve_rows), "usable_responses": len(gen_rows),
                     "fraction": coverage},
        "inputs": {p.name: _sha256(p) for p in (solve_p, gen_p, pb_p, scr_p)},
        "integrity": integ,
        # What the failed calls could have done to the numbers below. Exact,
        # one-sided per level, and reported at every coverage -- unlike
        # inference, a bound on a mean is honest on a partial run.
        "failure_slack": failure_slack(kept),
        "exclusions": {
            # Counted and itemised, never imputed (spec 3.2c).
            "null_leak_adj_rows": len(dropped),
            "null_leak_adj_items": sorted({r["item_id"] for r in dropped}),
            "analysed_rows": len(kept),
            "missing_prompt_tokens": notes["missing_prompt_tokens"],
        },
        "descriptives": {
            PRIMARY_OUTCOME: {k: A.describe_by(kept, k, PRIMARY_OUTCOME)
                              for k in ("regime", "solicitation", "model", "bank")},
            SECONDARY_OUTCOME: {k: A.describe_by(frame, k, SECONDARY_OUTCOME)
                                for k in ("regime", "solicitation", "model", "bank")},
            # WILLINGNESS, not identifiability. The Method promises this
            # "alongside the primary outcome throughout": a mitigation that
            # merely makes solvers decline more often would move leak_adj for a
            # reason that has nothing to do with hiding the answer, and only
            # this series distinguishes the two (spec 4.53).
            "hinted_cond_acc": {k: A.describe_by(frame, k, "hinted_cond_acc")
                                for k in ("regime", "solicitation", "model", "bank")},
            # The OTHER channel. Reported beside conditional accuracy because
            # the pair is what makes either interpretable: a drop in leak_adj
            # accompanied by a drop here would be solvers declining, not
            # answers being hidden, and the two demand opposite conclusions.
            "parsed_fraction": {k: A.describe_by(frame, k, "parsed_fraction")
                                for k in ("regime", "solicitation", "model", "bank")},
        },
        "primary_model": primary,
        "secondary_model_raw_leak_hat": secondary,
        # WHICH COVARIATES THE PRIMARY FIT ADJUSTED FOR, AND WHY (spec 4.49).
        # Recorded beside the estimates because the decision changed every
        # conclusion in R2, so a reader must be able to check it, not trust it.
        "covariate_decision": decision,
        "covariate_sensitivity": sens,
        "families": fams,
        # DERIVED FROM THE REALISED DESIGN, never from the numbers the family was
        # SIZED with. This block used to report n = 20 items and Holm over 15
        # pairs -- the planning figures for a 6-generator roster. The study ran 14
        # items and 4 generators, i.e. 6 pairs, and quoting the planning numbers
        # as if they described the achieved design is §4.43's defect exactly: a
        # constant agreeing with prose that the data had already outgrown.
        "model_factor": {
            "test": "none -- descriptive, CI-only (spec section 6, R1 vacated)",
            "n_items": len({r["item_id"] for r in kept}),
            "n_generators": len({r["model"] for r in kept}),
            "n_pairs": (lambda g: g * (g - 1) // 2)(
                len({r["model"] for r in kept})),
            "mde_achieved_alpha05": A.two_proportion_mde(
                len({r["item_id"] for r in kept})),
            "mde_achieved_holm": A.two_proportion_mde(
                len({r["item_id"] for r in kept}),
                alpha=0.05 / max(1, (lambda g: g * (g - 1) // 2)(
                    len({r["model"] for r in kept})))),
            "largest_observed_difference": (
                lambda ms: round(max(ms) - min(ms), 6) if ms else None)(
                [v["mean"] for v in A.describe_by(kept, "model",
                                                  PRIMARY_OUTCOME).values()]),
            "planning_figures_for_comparison": {
                "mde_n20_alpha05": A.two_proportion_mde(20),
                "mde_n20_holm15": A.two_proportion_mde(20, alpha=0.05 / 15),
            },
        },
        "threshold_sensitivity": threshold_sensitivity(kept),
        # Leave-one-item-out influence. Reported unconditionally, like
        # `covariate_sensitivity`: at 14 items a single item is a
        # fourteenth of the design, so "does any one item carry this
        # conclusion?" is not an optional question and must not be one a
        # reader has to think to ask.
        "item_influence": item_influence(kept) if complete else {
            "status": "not_computable_yet",
            "why": "deletion refits need the full design"},
        "guardrail": guardrail(gen_rows),
        "latency_ms": latency(gen_rows),
        "panel_agreement": icc.as_dict() if icc else {
            "status": "not_computable",
            "reason": "fewer than two informative solvers on enough responses",
        },
        "panel_agreement_by_panel": icc_panels,
        # THE PRE-REGISTERED FALLBACK, ACTUALLY COMPUTED (spec 4.52).
        "per_solver_levels": per_solver_levels(solve_rows, _rows(pb_p)),
        # THE PRE-REGISTERED SENSITIVITY CHECK, ACTUALLY RUN (spec 4.53).
        "ceiling_sensitivity": ceiling_sensitivity(solve_rows, _rows(pb_p)),
        # The stratified bank contrast promised in the Limitations. On a single
        # bank only the composition is reportable -- but it is reported, so the
        # promise is visible before the second bank arrives (spec 4.54).
        "bank_contrast_by_option_stratum": bank_contrast_by_option_stratum(
            frame, option_counts([bank], rost)),
        "decline_breakdown": A.decline_breakdown(solve_rows),
    }


def item_influence(rows: list[dict], fams: dict = None) -> dict:
    """Leave-one-item-out influence on every pre-registered contrast.

    WHY THIS IS NOT OPTIONAL AT THIS n. Bank A contributes 14 items under item
    fixed effects, so one deleted item is a fourteenth of the design and shifts
    of one to three standard errors are ORDINARY rather than alarming. Nothing in
    the paper said so, which left two different mistakes available: a referee
    reading single-item leverage as instability, and us reading one influential
    item as evidence about that item.

    IT WAS THE SECOND THAT NEARLY HAPPENED. The parser-ambiguity paragraph claims
    the misscoring "largely cancels in the difference", which nothing computed.
    The one affected item that survived screening turns out to be the MOST
    influential of the 14 on P1-P0 at 2.83 se -- which looks like the claim
    failing, until the whole distribution is computed and the next largest is
    1.81 se on an item with no ambiguity at all, and a lower-leakage item than
    the affected one sits at 1.17. Being top of a continuum is not being an
    outlier from it. So the leverage says something about n, and nothing about
    the parser.

    Reported as a maximum and a verdict check rather than a per-item table: what
    a reader needs is whether ANY single item carries a conclusion, and the
    answer is a property of the worst case.
    """
    fams = FAMILIES if fams is None else fams
    items = sorted({str(r["item_id"]) for r in rows})
    if len(items) < 3:
        return {"status": "too_few_items", "n_items": len(items)}
    nb = len({r["bank"] for r in rows})
    full = families(rows, banks_present=nb, complete=True, covariates=())

    # ONE REFIT PER DELETED ITEM, NOT ONE PER (ITEM, CONTRAST). The first version
    # looped contrasts outermost and re-ran every deletion inside each, so 14
    # items x 7 contrasts was 98 fits for the 14 the question needs -- the test
    # file took 72 seconds and `results.py` paid it on every run. The deletion is
    # a property of the ITEM; the contrasts are all read off the same fit.
    per_item: dict[str, dict] = {}
    for it in items:
        keep = [r for r in rows if str(r["item_id"]) != it]
        try:
            per_item[it] = families(keep, banks_present=len({r["bank"] for r in keep}),
                                    complete=True, covariates=())
        except (ValueError, RuntimeError, KeyError):
            # A deletion that leaves the design unfittable or drops a
            # pre-registered level is not an influence measurement. Skipping it
            # is right; scoring it as zero influence would let an unestimable
            # deletion read as evidence of stability -- the same shape as
            # scoring a failed call as a miss.
            continue

    out = {"n_items": len(items), "n_deletions_fitted": len(per_item),
           "families": {}}
    for fam in fams:
        if "contrasts" not in full.get(fam, {}):
            continue
        rec = {}
        for k, b in full[fam]["contrasts"].items():
            ests, worst, faint, se_max, flips = [], None, None, b["se"], []
            for it, f in per_item.items():
                v = f.get(fam, {}).get("contrasts", {}).get(k)
                if v is None:
                    continue
                ests.append(v["estimate"])
                sh = abs(v["estimate"] - b["estimate"]) / b["se"]
                if worst is None or sh > worst[1]:
                    worst = (it, sh, v["estimate"])
                if faint is None or abs(v["estimate"]) < abs(faint[1]):
                    faint = (it, v["estimate"])
                se_max = max(se_max, v["se"])
                if bool(v["reject"]) != bool(b["reject"]):
                    flips.append(it)
            rec[k] = {
                "published": b["estimate"], "se": b["se"],
                "most_influential_item": worst[0] if worst else None,
                "estimate_without_it": worst[2] if worst else b["estimate"],
                "max_shift_in_se": worst[1] if worst else 0.0,
                "faintest_item": faint[0] if faint else None,
                "faintest_estimate": faint[1] if faint else b["estimate"],
                "retained_fraction_of_effect": (
                    abs(faint[1]) / abs(b["estimate"])
                    if faint and b["estimate"] else None),
                "max_se_under_deletion": se_max,
                # The SIGN is the robust part of a small contrast and the
                # magnitude is not, so the range is what the manuscript quotes --
                # a claim about direction is checkable against it, a claim about
                # size is bounded by it.
                "estimate_range": [min(ests), max(ests)] if ests else None,
                "sign_stable": bool(ests) and (
                    all(e > 0 for e in ests) if b["estimate"] > 0
                    else all(e < 0 for e in ests)),
                "verdict_flipping_items": flips,
            }
        out["families"][fam] = rec
    allrec = [v for rec in out["families"].values() for v in rec.values()]
    out["max_shift_in_se"] = max((v["max_shift_in_se"] for v in allrec), default=0.0)
    out["any_verdict_flip"] = any(v["verdict_flipping_items"] for v in allrec)
    return out


def cross_bank_violations(sigs: dict, vers: dict, ids: dict) -> list[str]:
    """The three cross-bank rules, in ONE place, as messages rather than exits.

    THESE ARE THE INVERSE OF THE WITHIN-BANK RULES. `integrity` refuses a bank
    whose rows carry two measurement signatures -- the items changed mid-run.
    ACROSS banks the signatures MUST differ, because different banks are
    different items; equal signatures mean one file was pooled with itself or a
    bank id is mislabelled. Item ids must be disjoint for the same reason, and
    `study_version` must MATCH, because two banks measured at different token
    budgets are not comparable (spec 4.29) and pooling them would hide that
    inside a bank coefficient.

    RETURNED, NOT RAISED, so the identical predicates can answer two questions:
    `build_pooled` fails on them, and `--check-poolable` reports them BEFORE the
    ~23,000 solver calls that pooling needs are spent. Duplicating the rules to
    get the second reading is the defect spec 4.33 is about; the earlier version
    of this had them inline as three `sys.exit` calls, reachable only after the
    run they were meant to protect.
    """
    out = []
    if len(set(sigs.values())) != len(sigs):
        out.append(f"two banks share a measurement signature {sigs}; different "
                   f"banks are different items, so this means one file was "
                   f"pooled with itself or a bank id is mislabelled.")
    live = {v for v in vers.values() if v is not None}
    if len(live) > 1:
        out.append(f"banks were measured at different study versions "
                   f"{sorted(live)}; token budgets differ between them (spec "
                   f"4.29) and pooling would bury that in a bank term.")
    seen: dict[str, str] = {}
    for b, s_ in ids.items():
        for i in sorted(s_):
            if i in seen and seen[i] != b:
                out.append(f"item {i!r} appears in banks {seen[i]} and {b}; the "
                           f"banks are not disjoint, so an item fixed effect "
                           f"would straddle them and the interaction is "
                           f"meaningless.")
            seen[i] = b
    return out


def check_poolable(banks: list[str], rost: str) -> tuple[int, list[str]]:
    """Answer "will these banks pool?" from whatever stages exist already.

    WHY THIS RUNS BEFORE `solve`. `build_pooled` enforces the same three rules,
    but it reads them off the SOLVE rows, so a mismatch surfaces only after the
    calls it would invalidate have been spent -- for bank B that is ~23,000. The
    signature, the study version and the item ids are all fixed by the time the
    bank is screened, so the question is answerable from `screen` alone.
    """
    sigs, vers, ids, notes = {}, {}, {}, []
    for bank in banks:
        b = bank.upper()
        for st in ("solve", "generate", "placebo", "screen"):
            p = R._jsonl_path(st, bank, rost)
            if not p.exists():
                continue
            rows = _rows(p)
            if not rows:
                continue
            sg = {r.get("bank_measurement_sig") for r in rows
                  if r.get("bank_measurement_sig")}
            vs = {r.get("study_version") for r in rows if r.get("study_version")}
            if len(sg) > 1 or len(vs) > 1:
                notes.append(f"{p.name}: mixed within one bank "
                             f"(sigs={sorted(sg)}, versions={sorted(vs)}) -- a "
                             f"WITHIN-bank violation, which `integrity` refuses.")
            sigs[b] = next(iter(sg), None)
            vers[b] = next(iter(vs), None)
            ids[b] = {str(r["item_id"]) for r in rows if r.get("item_id")}
            notes.append(f"{b}: from {p.name} -- sig={str(sigs[b])[:12]}…, "
                         f"study={vers[b]}, {len(ids[b])} item ids")
            break
        else:
            notes.append(f"{b}: NO stage file found for roster {rost!r}")
    # `screen` covers every candidate item; the retained subset is what pools.
    # Disjointness over the wider set is the stronger check, so it is the one
    # worth making -- if the CANDIDATES overlap, the retained sets may too.
    return len(cross_bank_violations(sigs, vers, ids)), notes


def build_pooled(banks: list[str], rost: str, partial: bool,
                 icc_boot: int = 2000) -> dict:
    """The pooled analysis: R2 and R3 over both banks, plus R4.

    WHY POOLED IS THE PRIMARY FORM ONCE BOTH BANKS EXIST. Spec section 6 sizes
    the regime contrast on POOLED arms ("pooled P0 vs P1 gives n_raw = 360/arm"),
    so a per-bank R2 is a subgroup analysis of the pre-registered one, not the
    pre-registered one. R4 exists only here.

    THE CROSS-BANK CHECKS ARE THE OPPOSITE OF THE WITHIN-BANK ONES, which is why
    they cannot be the same code. `integrity` refuses a bank whose rows carry two
    measurement signatures -- the items changed mid-run. Across banks the
    signatures MUST differ, because different banks are different items; equal
    signatures mean one file was pooled with itself or a bank id was mislabelled.
    Item ids must be disjoint for the same reason, and `study_version` must MATCH,
    because two banks measured at different token budgets are not comparable
    (spec 4.29) and pooling them would hide that inside a bank coefficient.
    """
    per_bank: dict[str, dict] = {}
    frames: list[dict] = []
    gen_total = solve_total = 0
    inputs: dict[str, str | None] = {}
    # Kept raw because the per-panel ICC and the per-solver levels read
    # `by_solver`/`per_solver` blocks off the RECORDS, not the analysis frame.
    solve_all: list[dict] = []
    placebo_all: list[dict] = []
    for bank in banks:
        solve_p = R._jsonl_path("solve", bank, rost)
        gen_p = R._jsonl_path("generate", bank, rost)
        solve_rows = _rows(solve_p)
        if not solve_rows:
            sys.exit(f"no solve records at {solve_p.name}; pooling needs every "
                     f"named bank to have been solved")
        gen_rows = [g for g in _rows(gen_p) if g.get("ok") and g.get("response")]
        integ = integrity(solve_rows, bank, rost)      # within-bank rules intact
        frame, notes = analysis_frame(solve_rows, gen_rows)
        per_bank[bank.upper()] = {
            "integrity": integ,
            "coverage": {"solve_rows": len(solve_rows),
                         "usable_responses": len(gen_rows),
                         "fraction": (len(solve_rows) / len(gen_rows)
                                      if gen_rows else 0.0)},
            "guardrail": guardrail(gen_rows),
            "missing_prompt_tokens": notes["missing_prompt_tokens"],
        }
        pb_p = R._jsonl_path("placebo", bank, rost)
        inputs |= {p.name: _sha256(p) for p in (solve_p, gen_p, pb_p)}
        solve_all += solve_rows
        placebo_all += _rows(pb_p)
        frames += frame
        solve_total += len(solve_rows)
        gen_total += len(gen_rows)

    # ONE IMPLEMENTATION, shared with `--check-poolable` (see
    # `cross_bank_violations`). These used to be three inline `sys.exit` calls
    # here, which meant the only way to learn a pair of banks would not pool was
    # to finish solving them both.
    sigs = {b: d["integrity"]["bank_measurement_sig"] for b, d in per_bank.items()}
    vers = {b: d["integrity"]["study_version"] for b, d in per_bank.items()}
    ids: dict[str, set] = {}
    for r in frames:
        ids.setdefault(str(r["bank"]), set()).add(str(r["item_id"]))
    bad = cross_bank_violations(sigs, vers, ids)
    if bad:
        sys.exit("\n".join(bad))

    complete = all(d["coverage"]["solve_rows"] >= d["coverage"]["usable_responses"]
                   for d in per_bank.values())
    if not complete and not partial:
        sys.exit("at least one bank is incompletely solved; pass --partial to "
                 "report descriptives (it will be stamped complete=false).")

    kept = [r for r in frames if r.get(PRIMARY_OUTCOME) is not None]
    dropped = [r for r in frames if r.get(PRIMARY_OUTCOME) is None]
    banks_present = len({r["bank"] for r in kept})
    pending = {"status": "not_computable_yet",
               "reason": "at least one bank is incompletely solved; inference "
                         "waits for the full run."}
    decision = covariate_decision(frames)
    covs = decision["admitted"]
    if complete:
        fit = A.fit_with_item_fixed_effects(kept, outcome=PRIMARY_OUTCOME,
                                           factors=FACTORS, covariates=covs)
        primary = {"outcome": PRIMARY_OUTCOME, "note": fit.note,
                   "n_obs": fit.n_obs, "n_items": fit.n_items,
                   "converged": fit.converged,
                   "params": {k: round(v, 6) for k, v in fit.params.items()},
                   "pvalues": fit.pvalues}
        fams = families(kept, banks_present, complete=True, covariates=covs)
        sens = covariate_sensitivity(kept, decision)
    else:
        primary = dict(pending)
        sens = dict(pending)
        fams = {f: dict(pending) for f in FAMILIES}
        fams |= {k: v for k, v in
                 families(kept, banks_present, complete=False,
                          covariates=covs).items()
                 if k in ("R4_robustness", "R1_model")}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "banks": [b.upper() for b in banks],
        "roster": rost,
        "study_version": C.STUDY_VERSION,
        "complete": complete,
        "pooled": True,
        "inputs": inputs,
        "per_bank": per_bank,
        "totals": {"solve_rows": solve_total, "usable_responses": gen_total},
        "failure_slack": failure_slack(kept),
        "exclusions": {"null_leak_adj_rows": len(dropped),
                       "null_leak_adj_items": sorted({r["item_id"] for r in dropped}),
                       "analysed_rows": len(kept)},
        "descriptives": {
            PRIMARY_OUTCOME: {k: A.describe_by(kept, k, PRIMARY_OUTCOME)
                              for k in ("regime", "solicitation", "model", "bank")},
        },
        # The bank MAIN EFFECT lives here and nowhere else: not identified as a
        # test (spec 4.47), so it is reported as two descriptive distributions
        # with intervals, which is what the pre-registration's question reduces to
        # once item fixed effects are in the model.
        "bank_descriptive": A.describe_by(kept, "bank", PRIMARY_OUTCOME),
        "primary_model": primary,
        "covariate_decision": decision,
        "covariate_sensitivity": sens,
        "families": fams,
        "threshold_sensitivity": threshold_sensitivity(kept),
        # Leave-one-item-out influence. Reported unconditionally, like
        # `covariate_sensitivity`: at 14 items a single item is a
        # fourteenth of the design, so "does any one item carry this
        # conclusion?" is not an optional question and must not be one a
        # reader has to think to ask.
        "item_influence": item_influence(kept) if complete else {
            "status": "not_computable_yet",
            "why": "deletion refits need the full design"},
        "panel_agreement": (lambda i: i.as_dict() if i else
                            {"status": "not_computable"})(
            A.solver_agreement([r for r in frames], n_boot=icc_boot)),
        # POOLING DOES NOT DISSOLVE THE PANEL PROBLEM. Vendor exclusion applies
        # per bank exactly as it does within one, so "the" pooled ICC covers the
        # largest panel here too (spec 4.50), and the pre-registered fallback
        # still has to be computable (spec 4.52).
        "panel_agreement_by_panel": A.solver_agreement_by_panel(
            solve_all, n_boot=icc_boot),
        "per_solver_levels": per_solver_levels(solve_all, placebo_all),
        # THE PRE-REGISTERED STRATIFIED CONTRAST (spec 4.54). Only computable
        # here: it needs both banks in one frame.
        "bank_contrast_by_option_stratum": bank_contrast_by_option_stratum(
            frames, option_counts(banks, rost)),
    }


def summarise(res: dict) -> str:
    L = [f"bank {res['bank']} roster {res['roster']} study {res['study_version']} "
         f"complete={res['complete']} coverage={res['coverage']['fraction']:.1%}",
         f"rows analysed {res['exclusions']['analysed_rows']} "
         f"(excluded for null {PRIMARY_OUTCOME}: {res['exclusions']['null_leak_adj_rows']}) "
         f"failed solver calls {res['integrity']['n_failed_solver_calls']}",
         ""]
    d = res["descriptives"][PRIMARY_OUTCOME]["regime"]
    L.append(f"{'regime':<8}{'n':>6}{'leak_adj':>10}{'95% CI':>22}")
    for reg, v in d.items():
        ci = "[%.3f, %.3f]" % (v["ci_low"], v["ci_high"])
        L.append(f"{reg:<8}{v['n']:>6}{v['mean']:>10.3f}{ci:>22}")
    L.append("")
    for fam, body in res["families"].items():
        if "contrasts" not in body:
            L.append(f"{fam}: {body.get('status','?')} -- {body.get('reason','')[:70]}")
            continue
        L.append(f"{fam} (Holm m={body['m']})")
        for name, c in body["contrasts"].items():
            star = "reject" if c["reject"] else "hold"
            L.append(f"  {name:<8} est {c['estimate']:+.3f} (se {c['se']:.3f}) "
                     f"p {c['p']:.3g} -> p_adj {c['p_adjusted']:.3g} {star}")
    L.append("")
    for reg, g in res["guardrail"].items():
        L.append(f"{reg}: blocked {g['blocked']}/{g['n']} ({g['blocked_rate']:.1%}) "
                 f"rejections {g['rejections']} repairs {g['repairs']} "
                 f"repaired-and-passed {g['repaired_ok']}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bank", default="A")
    ap.add_argument("--roster", default="ol")
    ap.add_argument("--partial", action="store_true",
                    help="allow fewer solve rows than usable responses; the "
                         "output is stamped complete=false")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--pool", default=None,
                    help="comma-separated bank ids to analyse together "
                         "(e.g. A,bs). Required for family R4.")
    ap.add_argument("--check-poolable", action="store_true",
                    help="with --pool: report whether the named banks satisfy "
                         "the three cross-bank rules, WITHOUT needing them "
                         "solved. Run this before spending the solve budget.")
    a = ap.parse_args()

    if a.pool:
        banks = [b.strip() for b in a.pool.split(",") if b.strip()]
        if len(banks) < 2:
            sys.exit("--pool needs at least two bank ids; use --bank for one.")
        if a.check_poolable:
            n, notes = check_poolable(banks, a.roster)
            for line in notes:
                print(f"  {line}")
            if n:
                print(f"\nNOT POOLABLE: {n} violation(s). Family R4 cannot be "
                      f"estimated from these banks as they stand.")
                return 1
            print(f"\nPOOLABLE. Signatures differ, item ids are disjoint and "
                  f"the study versions agree, so family R4 is estimable once "
                  f"every named bank is solved.")
            return 0
        res = build_pooled(banks, a.roster, a.partial)
        stem = f"results_pooled_{'-'.join(b.upper() for b in banks)}_{a.roster}.json"
        out = a.out or (C.ROOT / "data" /
                        (stem if res["complete"] else f"PARTIAL_{stem}"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=1, sort_keys=False) + "\n")
        r4 = res["families"].get("R4_robustness", {})
        print(f"pooled {res['banks']} rows={res['totals']['solve_rows']} "
              f"complete={res['complete']}")
        print(f"R4: {r4.get('status', 'computed')} "
              f"(m={r4.get('m')}, testable={r4.get('m_testable')})")
        if "joint_test" in r4:
            print(f"  joint bank x regime: F={r4['joint_test']['F']:.2f} "
                  f"p={r4['joint_test']['p']:.3g} vs threshold "
                  f"{r4['holm']['threshold']}")
        print(f"\n-> {out}")
        return 0

    res = build(a.bank, a.roster, a.partial)
    # A PARTIAL RESULT NEVER TAKES THE CANONICAL NAME. The record is stamped
    # `complete: false`, but a stamp inside a file does not survive someone
    # opening `results_bankA_ol.json` to look up a mean. The prefix does.
    stem = f"results_bank{a.bank.upper()}_{a.roster}.json"
    out = a.out or (C.ROOT / "data" /
                    (stem if res["complete"] else f"PARTIAL_{stem}"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, sort_keys=False) + "\n")
    print(summarise(res))
    print(f"\n-> {out}")
    if a.verbose:
        print(json.dumps(res["primary_model"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
