"""Tests for the results assembler. Synthetic fixtures only -- spec section 6
requires the analysis code to be exercised before the real data arrives, and
section 6 also forbids synthetic data from leaving `harness/tests/`.

The properties under test are the REFUSALS, not the arithmetic. `analyse.py`
already has tests for the estimators; what this file protects is the set of
things `results.py` must decline to compute, each of which was reached by an
actual run of the real pipeline while it was being written.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analyse as A          # noqa: E402
import results as RS         # noqa: E402
import run as R              # noqa: E402

REGIMES = ("P0", "P1", "P2", "P3")
#: The design as RUN. S1 was cut before data (spec 4.6), so a fixture
#: carrying it would exercise a study that does not exist -- and would hide
#: the very mismatch that spec 4.43 was found by counting.
SOLS = ("S0", "S2", "S3")
MODELS = ("v1/alpha:cloud", "v2/beta:cloud")
#: The ICC bootstrap is 2000 draws in production. The tests assert on the
#: point estimate and the refusals, never on the interval width, so paying
#: for 2000 resamples per test buys nothing but 20 seconds of suite time.
BOOT = 40


def _cell_key(item, model, sol, reg, rep):
    return f"{item}|{model}|{sol}|{reg}|{rep}"


def _design(n_items=12, reps=2):
    for i in range(n_items):
        for m in MODELS:
            for s in SOLS:
                for g in REGIMES:
                    for rep in range(reps):
                        yield f"I{i:02d}", m, s, g, rep


def synth(n_items=12, reps=2, leak_by_regime=None, null_items=(), blocked_regimes=("P2",)):
    """A complete, well-behaved study: solve rows and their generate rows."""
    eff = leak_by_regime or {"P0": 0.60, "P1": 0.45, "P2": 0.30, "P3": 0.15}
    solve, gen = [], []
    for n, (item, model, sol, reg, rep) in enumerate(_design(n_items, reps)):
        ck = _cell_key(item, model, sol, reg, rep)
        # A deterministic wobble: identifiable per-item variation without an RNG,
        # so a failing test is reproducible from the fixture alone.
        wob = ((n % 7) - 3) / 100.0
        hinted = min(1.0, max(0.0, eff[reg] + 0.35 + wob))
        pb = 0.35
        adj = None if item in null_items else hinted - pb
        solve.append({
            "cell_key": ck, "item_id": item, "generator": model,
            "solicitation_key": sol, "regime": reg, "bank": "A", "roster": "ol",
            "study_version": "9.9.9", "bank_measurement_sig": "sig",
            "hinted": hinted, "baseline": 0.30, "leak_hat": hinted - 0.30,
            "placebo_hinted": pb, "leak_adj": adj,
            "parsed_fraction": 1.0, "n_failed_calls": 0, "error_kinds": {},
            "blocked": reg in blocked_regimes and rep == 0,
            "per_solver": {"s1": {"hits": 2, "calls": 3, "parsed": 3, "failed": 0},
                           "s2": {"hits": 1, "calls": 3, "parsed": 3, "failed": 0}},
            # `hinted`/`baseline` are present in the real records and
            # per_solver_levels reads them; a fixture without them let the new
            # code path skip every row and still report success (spec 4.48).
            # Baselines differ so each solver's `leak` is unchanged from before.
            "by_solver": {"per_solver": {
                "s1": {"leak": hinted - 0.30, "hinted": hinted,
                       "baseline": 0.30, "informative": True},
                "s2": {"leak": hinted - 0.35, "hinted": hinted,
                       "baseline": 0.35, "informative": True}},
                "mean_leak_across_solvers": hinted - 0.325,
                "n_informative_solvers": 2},
        })
        gen.append({
            "cell_key": ck, "item_id": item, "model": model, "regime": reg,
            "solicitation_key": sol, "rep": rep, "ok": True, "response": "hint text",
            "blocked": reg in blocked_regimes and rep == 0,
            "verifier_rejections": 1 if (reg in blocked_regimes and rep == 0) else 0,
            "repair_attempts": 0, "study_version": "9.9.9",
            "calls": [{"ok": True, "model": model, "prompt_tokens": 100 + n % 5,
                       "completion_tokens": 40, "latency_ms": 1000.0 + n}],
        })
    return solve, gen


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "RUNS", tmp_path)
    def write(solve, gen, placebo=None):
        (tmp_path / "solve_bankA_ol.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in solve))
        (tmp_path / "generate_bankA_ol.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in gen))
        if placebo is not None:
            (tmp_path / "placebo_bankA_ol.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in placebo))
    return write


def placebo_rows(solve, s1_hits=1, s2_hits=5, calls=10):
    """One row per item, with DELIBERATELY UNEQUAL per-solver rates."""
    out = []
    for item in sorted({r["item_id"] for r in solve}):
        out.append({"item_id": item, "placebo_hinted": 0.35,
                    "per_solver": {
                        "s1": {"hits": s1_hits, "calls": calls,
                               "parsed": calls, "failed": 0},
                        "s2": {"hits": s2_hits, "calls": calls,
                               "parsed": calls, "failed": 0}}})
    return out


# ------------------------------------------------------------------ refusals

def test_incomplete_run_refuses_without_partial(runs):
    solve, gen = synth()
    runs(solve[:100], gen)
    with pytest.raises(SystemExit) as e:
        RS.build("A", "ol", partial=False, icc_boot=BOOT)
    assert "--partial" in str(e.value)


def test_duplicate_cell_key_is_fatal(runs):
    solve, gen = synth()
    runs(solve + [solve[0]], gen)
    with pytest.raises(SystemExit) as e:
        RS.build("A", "ol", partial=True, icc_boot=BOOT)
    assert "duplicate" in str(e.value).lower()


def test_mixed_study_version_is_fatal(runs):
    solve, gen = synth()
    solve[5] = {**solve[5], "study_version": "1.0.0"}
    runs(solve, gen)
    with pytest.raises(SystemExit) as e:
        RS.build("A", "ol", partial=True, icc_boot=BOOT)
    assert "study version" in str(e.value).lower()


def test_foreign_bank_or_roster_is_fatal(runs):
    solve, gen = synth()
    solve[3] = {**solve[3], "roster": "kr"}
    runs(solve, gen)
    with pytest.raises(SystemExit):
        RS.build("A", "ol", partial=True, icc_boot=BOOT)


def test_partial_run_reports_descriptives_but_no_inference(runs):
    solve, gen = synth()
    runs(solve[:120], gen)
    res = RS.build("A", "ol", partial=True, icc_boot=BOOT)
    assert res["complete"] is False
    assert res["descriptives"]["leak_adj"]["regime"], "descriptives must still appear"
    assert res["primary_model"]["status"] == "not_computable_yet"
    for fam in RS.FAMILIES:
        assert res["families"][fam]["status"] == "not_computable_yet"
    # The reason must name the coverage, so a reader knows what to do about it.
    assert "%" in res["families"]["R2_mitigation"]["reason"]


# ------------------------------------------------------------- no imputation

def test_null_leak_adj_is_excluded_and_counted_never_imputed(runs):
    solve, gen = synth(null_items=("I03",))
    runs(solve, gen)
    res = RS.build("A", "ol", partial=False, icc_boot=BOOT)
    n_null = sum(1 for r in solve if r["leak_adj"] is None)
    assert n_null > 0
    assert res["exclusions"]["null_leak_adj_rows"] == n_null
    assert res["exclusions"]["null_leak_adj_items"] == ["I03"]
    assert res["exclusions"]["analysed_rows"] == len(solve) - n_null
    # The excluded item must not appear in the primary descriptives at all: an
    # imputed zero would leave the row count unchanged and the mean shifted.
    assert res["primary_model"]["n_obs"] == len(solve) - n_null
    per_item_n = sum(v["n"] for v in res["descriptives"]["leak_adj"]["regime"].values())
    assert per_item_n == len(solve) - n_null


# -------------------------------------------------------------- the families

def test_complete_run_tests_the_pre_registered_families(runs):
    solve, gen = synth()
    runs(solve, gen)
    res = RS.build("A", "ol", partial=False, icc_boot=BOOT)
    assert res["complete"] is True
    r2 = res["families"]["R2_mitigation"]
    assert r2["m"] == 4 and len(r2["contrasts"]) == 4
    r3 = res["families"]["R3_solicitation"]
    # THREE, not the five that section 6 listed until 2026-08-18: S1 was deleted
    # from the design before any response existed, so two of the five contrasts
    # named a level with no rows. Pinned as a literal here on purpose -- deriving
    # it from RS.FAMILIES would make this test agree with whatever the table says.
    assert r3["m"] == 3 and len(r3["contrasts"]) == 3
    assert not any("S1" in name for name in r3["contrasts"]), (
        "a contrast names S1, which the design does not contain")
    # Holm divisors within a family of four: the smallest p is tested at a/4.
    smallest = min(r2["contrasts"].values(), key=lambda c: c["p"])
    assert smallest["threshold"] == pytest.approx(0.05 / 4)
    # The fixture built a monotone mitigation effect; the contrast must recover
    # its sign and stay inside the outcome's arithmetic range.
    assert r2["contrasts"]["P1-P0"]["estimate"] < 0
    assert abs(r2["contrasts"]["P3-P0"]["estimate"]) <= 2.0


def test_r4_is_reported_as_not_computable_with_one_bank(runs):
    solve, gen = synth()
    runs(solve, gen)
    res = RS.build("A", "ol", partial=False, icc_boot=BOOT)
    r4 = res["families"]["R4_robustness"]
    assert r4["status"] == "not_computable"
    assert r4["m"] == 2, "the family keeps its pre-registered size when unavailable"
    # Only the interaction is identifiable under item FE (spec 4.47); the family
    # still divides alpha by its PRE-REGISTERED size, never by the testable one.
    assert r4["m_testable"] == 1
    assert "bank" in r4["reason"]


def test_model_factor_carries_no_pvalue(runs):
    """R1 was vacated before data: the model factor is descriptive, CI-only."""
    solve, gen = synth()
    runs(solve, gen)
    res = RS.build("A", "ol", partial=False, icc_boot=BOOT)
    assert res["families"]["R1_model"]["status"] == "vacated_before_data"
    assert "none" in res["model_factor"]["test"]
    assert "p" not in res["model_factor"]
    for level, d in res["descriptives"]["leak_adj"]["model"].items():
        assert set(d) == {"n", "mean", "ci_low", "ci_high"}


# ------------------------------------------------------- descriptive sections

def test_threshold_sensitivity_is_a_grid_and_is_monotone(runs):
    solve, gen = synth()
    runs(solve, gen)
    res = RS.build("A", "ol", partial=False, icc_boot=BOOT)
    grid = res["threshold_sensitivity"]
    assert len(grid) >= 5, "a single cutoff is exactly what this must not report"
    thresholds = sorted(grid, key=float)
    for reg in REGIMES:
        rates = [grid[t][reg]["rate"] for t in thresholds]
        assert rates == sorted(rates, reverse=True), "rate must fall as t rises"


def test_guardrail_counts_come_from_generate_not_solve(runs):
    solve, gen = synth(blocked_regimes=("P2", "P3"))
    # Truncate solve hard: the guardrail section describes what the GENERATOR did,
    # so it must not shrink when fewer responses have been solved.
    runs(solve[:80], gen)
    res = RS.build("A", "ol", partial=True, icc_boot=BOOT)
    g = res["guardrail"]
    assert g["P2"]["n"] == sum(1 for r in gen if r["regime"] == "P2")
    assert g["P2"]["blocked"] > 0 and g["P0"]["blocked"] == 0
    assert g["P2"]["blocked_ci"][0] <= g["P2"]["blocked_rate"] <= g["P2"]["blocked_ci"][1]


def test_coverage_counts_usable_responses_not_lines(runs):
    """`generate` keeps superseded ok=False rows as an audit trail (spec 4.39)."""
    solve, gen = synth()
    junk = [{**gen[0], "cell_key": gen[0]["cell_key"], "ok": False,
             "response": None, "error_kind": "quota_exhausted"} for _ in range(9)]
    runs(solve, junk + gen)
    res = RS.build("A", "ol", partial=False, icc_boot=BOOT)
    assert res["coverage"]["usable_responses"] == len(gen)
    assert res["complete"] is True, "9 failed rows must not make the run look short"


def test_latency_quantiles_are_observed_values(runs):
    solve, gen = synth()
    runs(solve, gen)
    res = RS.build("A", "ol", partial=False, icc_boot=BOOT)
    observed = {c["latency_ms"] for r in gen for c in r["calls"]}
    pooled = res["latency_ms"]["_pooled"]
    for q in ("p50_ms", "p90_ms", "p95_ms", "p99_ms"):
        assert pooled[q] in observed, "quantiles must not interpolate new latencies"
    assert pooled["p50_ms"] <= pooled["p90_ms"] <= pooled["p99_ms"]


def test_provenance_records_input_hashes(runs):
    solve, gen = synth()
    runs(solve, gen)
    res = RS.build("A", "ol", partial=False, icc_boot=BOOT)
    assert res["inputs"]["solve_bankA_ol.jsonl"], "solve must be hashed"
    assert res["inputs"]["placebo_bankA_ol.jsonl"] is None, "absent file -> null"


# --------------------------------------------- guards inside wald_contrasts

def _flat(n_items=10, reps=3, levels=REGIMES):
    return [{"item_id": f"I{i}", "regime": g, "model": "m", "solicitation": "S0",
             "leak_adj": 0.5 - 0.1 * levels.index(g) + 0.01 * i}
            for i in range(n_items) for g in levels for _ in range(reps)]


def test_absent_level_raises_rather_than_shrinking_the_family():
    rows = _flat(levels=("P0", "P1", "P2"))
    with pytest.raises(ValueError, match="P3"):
        A.wald_contrasts(rows, "regime", [("P3", "P2")])


def test_contrast_without_within_item_support_raises():
    """Thin within-item support, but a design that is NOT rank deficient.

    The two guards overlap at the extreme: if NO item has both levels, regime is
    a function of item identity and the rank guard fires first. So this fixture
    gives two items both levels -- enough to identify the factor, short of the
    three-item floor -- which is the case the support guard exists for and the
    only one that reaches it.
    """
    rows = [{"item_id": f"I{i}", "regime": "P0" if i % 2 else "P1", "model": "m",
             "solicitation": "S0", "leak_adj": 0.4 + 0.01 * i + 0.001 * rep}
            for i in range(40) for rep in range(3)]
    for item in ("X1", "X2"):
        for g in ("P0", "P1"):
            rows += [{"item_id": item, "regime": g, "model": "m",
                      "solicitation": "S0", "leak_adj": 0.5 if g == "P0" else 0.3}
                     for _ in range(3)]
    assert A.within_item_support(rows, "regime", "P1", "P0") == 2
    with pytest.raises(RuntimeError, match="within items"):
        A.wald_contrasts(rows, "regime", [("P1", "P0")])


def test_estimate_outside_the_outcome_range_raises():
    rows = _flat()
    # Inflate one cell far outside [-1, 1] so the fitted contrast must leave it.
    for r in rows:
        if r["regime"] == "P3":
            r["leak_adj"] = 40.0
    with pytest.raises(RuntimeError, match="outside the"):
        A.wald_contrasts(rows, "regime", [("P3", "P0")], outcome_range=(-1.0, 1.0))


def test_within_item_support_counts_items_with_both_levels():
    rows = ([{"item_id": "A", "regime": "P0"}, {"item_id": "A", "regime": "P1"},
             {"item_id": "B", "regime": "P0"}])
    assert A.within_item_support(rows, "regime", "P0", "P1") == 1


# --- failure_slack -------------------------------------------------------
# The screen has `truncation_slack`; the analysis had no equivalent, so a run
# interrupted by a quota window could bias one side of a contrast with nothing
# saying by how much.

def _slack_frame(rows):
    frame, _ = RS.analysis_frame(rows, [])
    return frame


def test_failure_slack_is_zero_when_nothing_failed(runs):
    solve, gen = synth(n_items=6)
    s = RS.failure_slack(_slack_frame(solve))
    assert s["n_failed_calls"] == 0
    assert s["failure_rate"] == 0.0
    assert s["max_row_correction"] == 0.0
    for fam in s["families"].values():
        assert set(fam["contrast_max_abs_shift"].values()) == {0.0}


def test_failure_slack_bounds_a_contrast_by_the_worse_level(runs):
    """Failures concentrated in ONE level bias the contrasts touching it."""
    solve, gen = synth(n_items=6)
    for r in solve:
        if r["regime"] == "P3":
            r["n_failed_calls"] = 1
            r["per_solver"] = {"s1": {"hits": 2, "calls": 3, "parsed": 3, "failed": 0},
                               "s2": {"hits": 1, "calls": 3, "parsed": 2, "failed": 1}}
    s = RS.failure_slack(_slack_frame(solve))
    lv = s["families"]["R2_mitigation"]["level_max_upward_shift"]
    assert lv["P3"] == pytest.approx(1 / 6, abs=1e-6)   # reported rounded to 6 dp
    assert lv["P0"] == lv["P1"] == lv["P2"] == 0.0
    cs = s["families"]["R2_mitigation"]["contrast_max_abs_shift"]
    # Every contrast with P3 on either side inherits P3's correction; the one
    # without it is untouched. This is the property the bound exists to state.
    assert cs["P3-P2"] == pytest.approx(1 / 6, abs=1e-6)
    assert cs["P3-P0"] == pytest.approx(1 / 6, abs=1e-6)
    assert cs["P1-P0"] == 0.0
    # A solicitation contrast is only shifted through the P3 rows it contains,
    # so it is diluted rather than zero -- failures do not respect the design.
    assert 0 < s["families"]["R3_solicitation"]["contrast_max_abs_shift"]["S2-S0"] < 1 / 6


def test_failure_slack_refuses_a_record_that_disagrees_with_itself(runs):
    solve, gen = synth(n_items=3)
    solve[0]["n_failed_calls"] = 2          # per_solver still sums to 0
    with pytest.raises(SystemExit) as e:
        RS.failure_slack(_slack_frame(solve))
    assert "disagrees with itself" in str(e.value)


def test_failure_slack_refuses_a_record_with_no_call_counts(runs):
    solve, gen = synth(n_items=3)
    solve[0]["per_solver"] = {}
    with pytest.raises(SystemExit) as e:
        RS.failure_slack(_slack_frame(solve))
    assert "cannot be bounded" in str(e.value)


def test_failure_slack_is_reported_on_a_partial_run(runs):
    """A bound on a mean is honest at any coverage -- unlike inference."""
    solve, gen = synth(n_items=6)
    runs(solve[:20], gen)
    res = RS.build("A", "ol", partial=True, icc_boot=BOOT)
    assert res["complete"] is False
    assert res["families"]["R2_mitigation"]["status"] == "not_computable_yet"
    assert res["failure_slack"]["n_solver_calls"] == 20 * 6


# --- pooled analysis and family R4 ---------------------------------------
# The cross-bank rules are the INVERSE of the within-bank ones: signatures must
# differ, item ids must be disjoint, study versions must match.

@pytest.fixture
def pooled_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "RUNS", tmp_path)

    def write(pairs):
        for bank, (solve, gen) in pairs.items():
            (tmp_path / f"solve_bank{bank.upper()}_ol.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in solve))
            (tmp_path / f"generate_bank{bank.upper()}_ol.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in gen))
    return write


def _bank(bank, sig, eff, n_items=8, prefix=None):
    solve, gen = synth(n_items=n_items, leak_by_regime=eff)
    pre = prefix or bank
    for r in solve:
        r["bank"] = bank.upper()
        r["bank_measurement_sig"] = sig
        r["item_id"] = f"{pre}-{r['item_id']}"
        r["cell_key"] = f"{pre}|{r['cell_key']}"
    for g in gen:
        g["cell_key"] = f"{pre}|{g['cell_key']}"
        g["item_id"] = f"{pre}-{g['item_id']}"
    return solve, gen


def test_pooled_r4_recovers_a_bank_interaction(pooled_runs):
    works = {"P0": 0.60, "P1": 0.40, "P2": 0.25, "P3": 0.10}
    fails = {"P0": 0.60, "P1": 0.58, "P2": 0.55, "P3": 0.52}
    pooled_runs({"A": _bank("A", "sigA", works), "bs": _bank("bs", "sigB", fails)})
    res = RS.build_pooled(["A", "bs"], "ol", partial=False, icc_boot=BOOT)
    r4 = res["families"]["R4_robustness"]
    assert r4["m"] == 2 and r4["m_testable"] == 1
    assert r4["joint_test"]["p"] < 0.01
    assert r4["holm"]["threshold"] == pytest.approx(0.025)
    assert r4["bank_main_effect"]["status"] == "not_identified"
    # the main-effect QUESTION is still answered, descriptively
    assert set(res["bank_descriptive"]) == {"A", "BS"}


def test_pooled_r2_and_r3_are_computed_over_both_banks(pooled_runs):
    eff = {"P0": 0.60, "P1": 0.40, "P2": 0.25, "P3": 0.10}
    pooled_runs({"A": _bank("A", "sigA", eff), "bs": _bank("bs", "sigB", dict(eff))})
    res = RS.build_pooled(["A", "bs"], "ol", partial=False, icc_boot=BOOT)
    r2 = res["families"]["R2_mitigation"]
    assert r2["m"] == 4
    assert res["primary_model"]["n_items"] == 16
    # bank is nested in item, so it cannot appear as a coefficient
    assert not any("bank" in k for k in res["primary_model"]["params"])
    assert "nested within item: bank" in res["primary_model"]["note"]


def test_pooled_refuses_two_banks_sharing_a_measurement_signature(pooled_runs):
    eff = {"P0": 0.60, "P1": 0.40, "P2": 0.25, "P3": 0.10}
    pooled_runs({"A": _bank("A", "same", eff), "bs": _bank("bs", "same", dict(eff))})
    with pytest.raises(SystemExit) as e:
        RS.build_pooled(["A", "bs"], "ol", partial=False, icc_boot=BOOT)
    assert "share a measurement signature" in str(e.value)


def test_pooled_refuses_overlapping_item_ids(pooled_runs):
    eff = {"P0": 0.60, "P1": 0.40, "P2": 0.25, "P3": 0.10}
    # same item id prefix in both banks: an item fixed effect would straddle them
    pooled_runs({"A": _bank("A", "sigA", eff),
                 "bs": _bank("bs", "sigB", dict(eff), prefix="A")})
    with pytest.raises(SystemExit) as e:
        RS.build_pooled(["A", "bs"], "ol", partial=False, icc_boot=BOOT)
    assert "not disjoint" in str(e.value)


def test_pooled_refuses_mixed_study_versions(pooled_runs):
    eff = {"P0": 0.60, "P1": 0.40, "P2": 0.25, "P3": 0.10}
    a = _bank("A", "sigA", eff)
    b = _bank("bs", "sigB", dict(eff))
    for r in b[0]:
        r["study_version"] = "8.8.8"
    pooled_runs({"A": a, "bs": b})
    with pytest.raises(SystemExit) as e:
        RS.build_pooled(["A", "bs"], "ol", partial=False, icc_boot=BOOT)
    assert "different study versions" in str(e.value)


def test_pooled_partial_refuses_inference_but_still_bounds_failures(pooled_runs):
    eff = {"P0": 0.60, "P1": 0.40, "P2": 0.25, "P3": 0.10}
    a = _bank("A", "sigA", eff)
    b = _bank("bs", "sigB", dict(eff))
    pooled_runs({"A": a, "bs": (b[0][:30], b[1])})
    res = RS.build_pooled(["A", "bs"], "ol", partial=True, icc_boot=BOOT)
    assert res["complete"] is False
    assert res["primary_model"]["status"] == "not_computable_yet"
    assert res["families"]["R2_mitigation"]["status"] == "not_computable_yet"
    assert res["failure_slack"]["n_solver_calls"] > 0


# --- the covariate decision (spec 4.49) ----------------------------------
# Dropping `prompt_tokens` turned Bank A's R2 family from four holds into four
# rejections. That decision must be made by a stated criterion, not in a session.

def test_a_treatment_determined_covariate_is_excluded_from_the_primary_fit(runs):
    solve, gen = synth(n_items=8)
    # the real shape: the mitigation preamble lengthens every non-P0 prompt
    for g in gen:
        base = 51 if g["regime"] == "P0" else 102
        g["calls"][0]["prompt_tokens"] = base + int(g["item_id"][1:]) + g["rep"]
    runs(solve, gen)
    res = RS.build("A", "ol", partial=False, icc_boot=BOOT)
    d = res["covariate_decision"]
    assert d["excluded"] == ("prompt_tokens",)
    assert d["admitted"] == ()
    assert d["diagnostics"]["prompt_tokens"]["partial_r2"] > 0.9
    assert "prompt_tokens" not in res["primary_model"]["note"].split("~")[1]


def test_a_nuisance_covariate_is_admitted(runs):
    solve, gen = synth(n_items=8)
    for g in gen:
        g["calls"][0]["prompt_tokens"] = 100 + int(g["item_id"][1:]) + 7 * g["rep"]
    runs(solve, gen)
    res = RS.build("A", "ol", partial=False, icc_boot=BOOT)
    assert res["covariate_decision"]["admitted"] == ("prompt_tokens",)
    assert "prompt_tokens" in res["primary_model"]["note"]


def test_the_alternative_specification_is_always_reported(runs):
    """If both fits agree the exclusion did not matter; if they disagree, the
    disagreement is the finding. Either way the reader gets both."""
    solve, gen = synth(n_items=8)
    for g in gen:
        base = 51 if g["regime"] == "P0" else 102
        g["calls"][0]["prompt_tokens"] = base + int(g["item_id"][1:]) + g["rep"]
    runs(solve, gen)
    res = RS.build("A", "ol", partial=False, icc_boot=BOOT)
    sens = res["covariate_sensitivity"]
    assert sens["covariates"] == ["prompt_tokens"]      # the excluded set
    assert sens["primary_covariates"] == []
    for fam in ("R2_mitigation", "R3_solicitation"):
        assert "contrasts" in sens["families"][fam]
        assert set(sens["families"][fam]["contrasts"]) == {
            f"{a}-{b}" for a, b in RS.FAMILIES[fam][1]}


# ------------------------------------------- per-solver levels (spec 4.52)

def test_per_solver_level_uses_each_solvers_own_placebo(runs):
    """THE TRAP THIS TEST EXISTS FOR: pairing a per-solver `hinted` with the
    POOLED placebo. Both solvers here have the same `hinted` and differ only in
    placebo willingness, so a correct estimator separates them by exactly the
    placebo gap and the buggy one collapses them onto a single value."""
    solve, gen = synth(n_items=6)
    pb = placebo_rows(solve, s1_hits=1, s2_hits=5, calls=10)   # 0.10 vs 0.50
    runs(solve, gen, placebo=pb)
    psl = RS.per_solver_levels(solve, pb)["per_solver"]
    assert set(psl) == {"s1", "s2"}
    mean_hinted = sum(r["hinted"] for r in solve) / len(solve)
    assert psl["s1"]["mean"] == pytest.approx(mean_hinted - 0.10, abs=1e-6)
    assert psl["s2"]["mean"] == pytest.approx(mean_hinted - 0.50, abs=1e-6)
    # ... and NOT the pooled-placebo answer, which would make them equal.
    assert psl["s1"]["mean"] != pytest.approx(psl["s2"]["mean"], abs=1e-3)


def test_per_solver_levels_reported_by_build(runs):
    """The whole point is that the number gets PRINTED, so `build` must carry
    it -- and must not report a per-solver breakdown that silently covers no
    responses at all."""
    solve, gen = synth(n_items=6)
    runs(solve, gen, placebo=placebo_rows(solve))
    res = RS.build("A", "ol", partial=False, icc_boot=BOOT)
    psl = res["per_solver_levels"]
    assert psl["n_solvers"] == 2
    assert sum(psl["skipped"].values()) == 0
    assert all(v["n"] > 0 for v in psl["per_solver"].values())
    assert psl["spread"] == pytest.approx(0.40, abs=1e-6)


def test_absent_placebo_file_skips_and_says_so(runs):
    """No placebo run means no adjusted per-solver level. That must read as
    'not measured', never as a level of zero."""
    solve, gen = synth(n_items=4)
    runs(solve, gen)                       # no placebo written
    res = RS.build("A", "ol", partial=False, icc_boot=BOOT)
    psl = res["per_solver_levels"]
    assert psl["per_solver"] == {}
    assert psl["spread"] is None
    assert psl["skipped"]["no_placebo_row"] > 0


def test_duplicate_placebo_row_is_fatal(runs):
    """Two rows for one item make the per-solver rate depend on file order."""
    solve, gen = synth(n_items=4)
    pb = placebo_rows(solve)
    with pytest.raises(SystemExit) as e:
        RS.per_solver_levels(solve, pb + [pb[0]])
    assert "duplicate" in str(e.value).lower()


def test_unparsed_placebo_is_absent_not_zero(runs):
    """A placebo call that never parsed is a missing measurement. Scoring it as
    a miss would subtract a zero and inflate every level that uses it."""
    solve, gen = synth(n_items=4)
    pb = placebo_rows(solve)
    for r in pb:
        r["per_solver"]["s1"] = {"hits": 0, "calls": 10, "parsed": 0, "failed": 10}
    psl = RS.per_solver_levels(solve, pb)
    assert "s1" not in psl["per_solver"]
    assert psl["skipped"]["no_placebo_for_solver"] > 0
    assert psl["per_solver"]["s2"]["n"] == len(solve)


# ------------------------------------- option strata (spec 4.54)

def _stratum_frame():
    """Two banks, three option strata, deliberately unbalanced like the real
    ones: bank A entirely three-option, bank BS spread across 3/4/5."""
    rows = []
    for i in range(4):                              # A: three-option only
        rows.append({"item_id": f"A{i}", "bank": "A", "leak_adj": 0.60})
    for i, n in ((0, 3), (1, 3), (2, 4), (3, 4), (4, 5)):
        rows.append({"item_id": f"B{i}", "bank": "BS", "leak_adj": 0.50})
    opts = {f"A{i}": 3 for i in range(4)}
    opts |= {"B0": 3, "B1": 3, "B2": 4, "B3": 4, "B4": 5}
    return rows, opts


def test_three_option_stratum_is_flagged_as_the_registered_comparison():
    """The Limitations promise the bank contrast WITHIN the three-option
    stratum, because only there is the retention criterion identical. A reader
    must not have to infer from prose which stratum carries the argument."""
    rows, opts = _stratum_frame()
    res = RS.bank_contrast_by_option_stratum(rows, opts)
    assert res["strata"]["3"]["pre_registered_primary"] is True
    for n in ("4", "5"):
        assert res["strata"][n]["pre_registered_primary"] is False
    # ... and the registered stratum is the one that gets a contrast, because
    # it is the only one both banks populate.
    assert res["strata"]["3"]["difference"]["estimate"] == pytest.approx(-0.10)
    assert res["strata"]["3"]["difference"]["contrast"] == "BS - A"


def test_other_strata_are_reported_separately_never_pooled():
    """Pooling the strata would reinstate exactly the confound the stratifying
    exists to remove -- a threshold that admits 1.45x chance in one stratum and
    1.75x in another."""
    rows, opts = _stratum_frame()
    res = RS.bank_contrast_by_option_stratum(rows, opts)
    assert set(res["strata"]) == {"3", "4", "5"}
    for n in ("4", "5"):
        assert res["strata"][n]["difference"]["status"] == "not_computable"
        assert "needs both" in res["strata"][n]["difference"]["reason"]
    # The three-option stratum must contain ONLY three-option rows.
    assert res["strata"]["3"]["by_bank"]["A"]["n_rows"] == 4
    assert res["strata"]["3"]["by_bank"]["BS"]["n_rows"] == 2


def test_no_hypothesis_test_is_attached_to_the_bank_contrast():
    """Bank is nested in item, so this contrast cannot be taken under item
    fixed effects and stays confounded with topic grain. A p-value here would be
    an unregistered test on a comparison the design cannot clean."""
    rows, opts = _stratum_frame()
    d = RS.bank_contrast_by_option_stratum(rows, opts)["strata"]["3"]["difference"]
    assert "none" in d["test"]
    assert not any(k.startswith("p") for k in d)


def test_items_with_no_option_count_are_counted_not_dropped():
    """A silent drop would shrink a stratum without saying so."""
    rows, opts = _stratum_frame()
    opts.pop("B4")
    res = RS.bank_contrast_by_option_stratum(rows, opts)
    assert res["items_without_option_count"] == 1
    assert "5" not in res["strata"]


class TestTheCrossBankRulesAreTheInverseOfTheWithinBankOnes:
    """Signatures must DIFFER, ids must be DISJOINT, versions must MATCH.

    `integrity` refuses a bank whose rows carry two measurement signatures --
    within one bank that means the items changed mid-run. Across banks the same
    equality is the fault, which is why these cannot be the same code, and why
    getting the direction wrong in either place is silent.

    They were three inline `sys.exit` calls inside `build_pooled`, so the only
    way to learn that two banks would not pool was to finish solving them both
    -- ~23,000 solver calls for bank B. Extracted so `--check-poolable` can ask
    the same question from `screen` alone, which fixes all three facts.
    """

    def test_a_clean_pair_has_no_violations(self):
        assert RS.cross_bank_violations(
            {"A": "sigA", "BS": "sigB"},
            {"A": "2.3.0", "BS": "2.3.0"},
            {"A": {"A-1", "A-2"}, "BS": {"B-1"}}) == []

    def test_equal_signatures_are_a_violation_across_banks(self):
        bad = RS.cross_bank_violations(
            {"A": "same", "BS": "same"},
            {"A": "2.3.0", "BS": "2.3.0"},
            {"A": {"A-1"}, "BS": {"B-1"}})
        assert len(bad) == 1 and "share a measurement signature" in bad[0]

    def test_differing_study_versions_are_a_violation(self):
        bad = RS.cross_bank_violations(
            {"A": "sigA", "BS": "sigB"},
            {"A": "2.2.0", "BS": "2.3.0"},
            {"A": {"A-1"}, "BS": {"B-1"}})
        assert len(bad) == 1 and "different study versions" in bad[0]

    def test_an_absent_version_does_not_manufacture_a_mismatch(self):
        """A stage that records no version is unknown, not disagreeing.

        Treating `None` as a distinct value would make every pair with one
        unstamped file unpoolable, which is a false refusal rather than a
        missed one.
        """
        assert RS.cross_bank_violations(
            {"A": "sigA", "BS": "sigB"},
            {"A": "2.3.0", "BS": None},
            {"A": {"A-1"}, "BS": {"B-1"}}) == []

    def test_a_shared_item_is_a_violation(self):
        bad = RS.cross_bank_violations(
            {"A": "sigA", "BS": "sigB"},
            {"A": "2.3.0", "BS": "2.3.0"},
            {"A": {"X-1", "A-2"}, "BS": {"X-1"}})
        assert len(bad) == 1 and "not disjoint" in bad[0] and "X-1" in bad[0]

    def test_build_pooled_does_not_keep_its_own_copy_of_the_rules(self):
        """One implementation, or the pre-flight and the analysis can disagree."""
        import inspect
        src = inspect.getsource(RS.build_pooled)
        assert "cross_bank_violations(" in src
        for gone in ("share a measurement signature",
                     "different study versions", "not disjoint"):
            assert gone not in src, (
                f"build_pooled still carries its own {gone!r} check")


class TestSingleItemInfluenceIsMeasuredNotAssumed:
    """At 14 items one item is a fourteenth of the design.

    Nothing in the paper said how far a contrast depended on a single item, which
    left two opposite mistakes available: a referee reading ordinary leverage as
    instability, and us reading one influential item as evidence about that item.
    The second nearly happened -- the parser-ambiguous item is the MOST
    influential of the 14 on P1-P0 (2.83 se), which looks like the ambiguity
    biting until the whole distribution is computed and the next largest is
    1.81 se on an item with no ambiguity at all. Being top of a continuum is not
    being an outlier from it.

    THE FIXTURE CARRIES THE WHOLE PRE-REGISTERED DESIGN -- four regimes and three
    solicitations -- because `families` refuses a frame missing a pre-registered
    level rather than silently testing a smaller family (a smaller family loosens
    every Holm threshold in it). A P0/P1-only fixture therefore does not exercise
    a reduced version of this; it raises.
    """

    REGIMES = ("P0", "P1", "P2", "P3")
    SOLICITATIONS = ("S0", "S2", "S3")

    def _frame(self, n_items=6, effect=-0.2, reps=2):
        """A balanced frame where only regime carries signal."""
        rows = []
        for i in range(n_items):
            for reg in self.REGIMES:
                for sol in self.SOLICITATIONS:
                    for rep in range(reps):
                        rows.append({
                            "item_id": f"i{i}", "bank": "A", "model": "m",
                            "solicitation": sol, "regime": reg,
                            "cell_key": f"i{i}|m|{sol}|{reg}|{rep}",
                            "leak_adj": (0.5
                                         + (effect if reg != "P0" else 0.0)
                                         + 0.001 * rep + 0.002 * i)})
        return rows

    def test_too_few_items_is_stated_not_computed(self):
        r = RS.item_influence(self._frame(n_items=2))
        assert r["status"] == "too_few_items"
        assert r["n_items"] == 2

    def test_a_homogeneous_design_shows_little_influence(self):
        r = RS.item_influence(self._frame())
        assert r["n_items"] == 6 and r["n_deletions_fitted"] == 6
        assert r["max_shift_in_se"] < 1.0
        assert r["any_verdict_flip"] is False

    def test_one_deviant_item_is_found_and_named(self):
        """An item carrying the whole effect must be identified as carrying it."""
        rows = self._frame(effect=0.0)
        for r in rows:
            if r["item_id"] == "i3" and r["regime"] == "P1":
                r["leak_adj"] -= 1.0
        rec = RS.item_influence(rows)["families"]["R2_mitigation"]["P1-P0"]
        assert rec["most_influential_item"] == "i3", rec["most_influential_item"]
        assert abs(rec["faintest_estimate"]) < abs(rec["published"]) / 2

    def test_the_range_brackets_the_published_estimate(self):
        rec = RS.item_influence(self._frame())["families"]["R2_mitigation"]["P1-P0"]
        lo, hi = rec["estimate_range"]
        assert lo <= rec["published"] <= hi
        assert rec["sign_stable"] is True

    def test_deleting_an_item_never_shrinks_the_standard_error(self):
        """Removing data cannot buy precision.

        This is the arithmetic that makes a marginal contrast flip without its
        estimate moving, and conflating the two overstates instability -- so the
        report records the worst se as well as the worst estimate.
        """
        rec = RS.item_influence(self._frame())["families"]["R2_mitigation"]["P1-P0"]
        assert rec["max_se_under_deletion"] >= rec["se"]

    def test_one_refit_per_deletion_not_per_contrast(self):
        """The deletion is a property of the item; contrasts read one fit.

        Looping contrasts outermost re-ran all 14 deletions inside each, giving
        98 fits for the 14 the question needs.
        """
        import inspect
        src = inspect.getsource(RS.item_influence)
        body = src[src.index("per_item"):]
        assert body.index("for fam in fams") > body.index("for it in items"), (
            "contrasts are being looped outside the deletions again")
        assert src.count("families(keep") == 1
