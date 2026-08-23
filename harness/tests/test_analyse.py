"""Tests for the pre-specified analysis.

Values are cross-checked against published worked examples or hand computation
where possible. A statistic that is subtly wrong here produces a plausible
number that no reviewer can catch from the text.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analyse as A  # noqa: E402


class TestWilson:
    def test_known_value(self):
        """Wilson 95% for x=10, n=20 is approximately (0.299, 0.701)."""
        lo, hi = A.wilson_ci(10, 20)
        assert lo == pytest.approx(0.299, abs=0.002)
        assert hi == pytest.approx(0.701, abs=0.002)

    def test_boundary_zero_stays_in_range(self):
        """The reason Wald is rejected: at x=0 it gives a degenerate [0,0]."""
        lo, hi = A.wilson_ci(0, 20)
        assert lo == 0.0
        assert 0 < hi < 0.20

    def test_boundary_all_successes(self):
        lo, hi = A.wilson_ci(20, 20)
        assert hi == 1.0 and 0.80 < lo < 1.0

    def test_zero_n(self):
        assert A.wilson_ci(0, 0) == (0.0, 1.0)

    def test_interval_narrows_with_n(self):
        w_small = A.wilson_ci(5, 10)
        w_big = A.wilson_ci(500, 1000)
        assert (w_big[1] - w_big[0]) < (w_small[1] - w_small[0])


class TestMeanCI:
    def test_known_mean(self):
        m, lo, hi = A.mean_ci([0.0, 1.0, 0.5, 0.5])
        assert m == pytest.approx(0.5)
        assert lo < m < hi

    def test_single_value_has_no_interval(self):
        m, lo, hi = A.mean_ci([0.3])
        assert m == pytest.approx(0.3)
        assert lo != lo  # NaN

    def test_empty(self):
        m, _, _ = A.mean_ci([])
        assert m != m  # NaN


class TestHolm:
    def test_step_down_ordering(self):
        """Classic Holm: smallest p compared against alpha/m."""
        res = A.holm_bonferroni({"a": 0.001, "b": 0.04, "c": 0.03}, alpha=0.05)
        assert res["a"]["threshold"] == pytest.approx(0.05 / 3)
        assert res["a"]["reject"] is True

    def test_stops_at_first_failure(self):
        """Once a hypothesis is retained, all larger p-values are retained too,
        even if one would pass its own threshold."""
        res = A.holm_bonferroni({"a": 0.5, "b": 0.001}, alpha=0.05)
        assert res["b"]["reject"] is True
        assert res["a"]["reject"] is False

    def test_no_rejection_when_all_large(self):
        res = A.holm_bonferroni({"a": 0.4, "b": 0.6, "c": 0.9})
        assert not any(v["reject"] for v in res.values())

    def test_adjusted_p_is_capped_at_one(self):
        res = A.holm_bonferroni({"a": 0.9, "b": 0.95})
        assert all(v["p_adjusted"] <= 1.0 for v in res.values())

    def test_single_hypothesis_is_uncorrected(self):
        res = A.holm_bonferroni({"only": 0.04}, alpha=0.05)
        assert res["only"]["threshold"] == pytest.approx(0.05)
        assert res["only"]["reject"] is True


class TestInverseNormal:
    @pytest.mark.parametrize("p,expected", [
        (0.5, 0.0), (0.975, 1.959964), (0.80, 0.841621), (0.025, -1.959964),
        (0.99, 2.326348), (0.001, -3.090232),
    ])
    def test_known_quantiles(self, p, expected):
        assert A._z(p) == pytest.approx(expected, abs=1e-5)

    def test_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            A._z(0.0)


class TestPower:
    def test_reproduces_the_reviews_mde(self):
        """The review computed MDE ~0.395 at n=20/cell, p1=0.5, alpha=.05,
        and ~0.497 under Holm over 15 pairs (alpha=.0033). Both are why the
        model factor is not tested pairwise."""
        assert A.two_proportion_mde(20, 0.5, alpha=0.05) == pytest.approx(0.395, abs=0.02)
        assert A.two_proportion_mde(20, 0.5, alpha=0.05 / 15) == pytest.approx(0.497, abs=0.03)

    def test_mde_shrinks_as_n_grows(self):
        assert A.two_proportion_mde(200) < A.two_proportion_mde(20)

    def test_stricter_alpha_raises_mde(self):
        assert A.two_proportion_mde(20, alpha=0.001) > A.two_proportion_mde(20, alpha=0.05)


class TestDescribeBy:
    def test_grouping_and_means(self):
        rows = [
            {"regime": "P0", "leak_hat": 1.0}, {"regime": "P0", "leak_hat": 0.8},
            {"regime": "P1", "leak_hat": 0.2}, {"regime": "P1", "leak_hat": 0.0},
        ]
        out = A.describe_by(rows, "regime")
        assert out["P0"]["mean"] == pytest.approx(0.9)
        assert out["P1"]["mean"] == pytest.approx(0.1)
        assert out["P0"]["n"] == 2

    def test_missing_values_skipped(self):
        rows = [{"regime": "P0", "leak_hat": None}, {"regime": "P0", "leak_hat": 0.4}]
        assert A.describe_by(rows, "regime")["P0"]["n"] == 1


class TestFixedEffectsFit:
    def _rows(self):
        # Regime effect present; item difficulty deliberately confounded with
        # nothing, so the fixed effects have something to absorb.
        #
        # `prompt_tokens` VARIES WITHIN ITEM. It used to read `4000 + 10 * i` --
        # an exact linear function of item identity, hence perfectly collinear
        # with the item dummies. That made the design rank deficient, and the fit
        # still returned -0.5 for the regime coefficient because pseudo-inverse
        # OLS picks the minimum-norm member of the solution set. The assertion
        # below therefore passed on a degenerate design for as long as this
        # fixture existed. Real prompts differ by regime and solicitation within
        # an item, so this is also the more faithful fixture.
        rows = []
        for i in range(12):
            for regime, base in (("P0", 0.9), ("P1", 0.4)):
                for rep in range(3):
                    rows.append({
                        "item_id": f"I{i}", "regime": regime, "model": "m1",
                        "leak_hat": base + 0.01 * i + 0.001 * rep,
                        # Within-item variation that does NOT track regime. A
                        # first attempt at this fixture added +180 tokens under
                        # P1, which fixed the rank deficiency by making the
                        # covariate a proxy for the factor -- the regime
                        # coefficient then came out -0.68 instead of -0.5,
                        # because the fit had no way to apportion the effect
                        # between two columns carrying the same signal.
                        "prompt_tokens": 4000 + 10 * i + 7 * rep,
                    })
        return rows

    def test_covariate_collinear_with_item_dummies_is_refused(self):
        """A covariate that is a function of the item is not "absorbed" -- it
        makes the design rank deficient, and every coefficient in the collinear
        space is then arbitrary. Refuse, rather than report the minimum-norm
        solution as though it were an estimate."""
        rows = [dict(r, prompt_tokens=4000 + 10 * int(r["item_id"][1:]))
                for r in self._rows()]
        r = A.fit_with_item_fixed_effects(rows)
        assert not r.converged
        assert "rank deficient" in r.note

    def test_recovers_regime_effect(self):
        r = A.fit_with_item_fixed_effects(self._rows())
        assert r.converged
        key = next(k for k in r.params if "regime" in k)
        assert r.params[key] == pytest.approx(-0.5, abs=0.05)

    def test_item_dummies_absorbed_not_reported(self):
        """60 nuisance coefficients would swamp the results table."""
        r = A.fit_with_item_fixed_effects(self._rows())
        assert not any("item_id" in k for k in r.params)
        assert r.n_items == 12

    def test_requires_item_id(self):
        r = A.fit_with_item_fixed_effects([{"regime": "P0", "leak_hat": 0.5}])
        assert not r.converged and "item_id" in r.note

    def test_empty_input(self):
        assert A.fit_with_item_fixed_effects([]).n_obs == 0


class TestICC:
    """Panel agreement. The manuscript pre-commits to reporting this, so the
    estimator must be correct before any data exist to apply it to."""

    def test_perfect_agreement_is_one(self):
        m = [[0.1, 0.1], [0.5, 0.5], [0.9, 0.9], [0.3, 0.3], [0.7, 0.7]]
        r = A.icc_two_way(m, n_boot=0)
        assert abs(r.icc_a1 - 1.0) < 1e-9
        assert abs(r.icc_c1 - 1.0) < 1e-9

    def test_constant_everywhere_is_agreement_not_nan(self):
        # Every rater gives every target the same score. Zero variance, but the
        # raters do not disagree, so this must not be NaN.
        r = A.icc_two_way([[0.4, 0.4]] * 6, n_boot=0)
        assert r.icc_a1 == 1.0

    def test_systematic_offset_splits_the_two_forms(self):
        """The reason ICC(2,1) is primary.

        Rater B is rater A plus a constant 0.3. The ranking is identical, so
        consistency is perfect -- but they disagree about the VALUE, which is
        what the paper reports as a leakage rate. Absolute agreement must be
        strictly lower, or the statistic cannot detect a biased solver.
        """
        a = [0.05, 0.20, 0.35, 0.50, 0.65, 0.80]
        m = [[x, x + 0.3] for x in a]
        r = A.icc_two_way(m, n_boot=0)
        assert r.icc_c1 > 0.99
        assert r.icc_a1 < r.icc_c1
        assert r.icc_a1 < 0.9

    def test_independent_noise_is_near_zero(self):
        import random
        rng = random.Random(7)
        m = [[rng.random(), rng.random(), rng.random()] for _ in range(200)]
        r = A.icc_two_way(m, n_boot=0)
        assert -0.15 < r.icc_a1 < 0.15

    def test_matches_shrout_fleiss_published_values(self):
        """External validation against Shrout & Fleiss (1979), Table 1.

        Their published values for this dataset are ICC(2,1) = 0.29 and
        ICC(3,1) = 0.71. Checking against a benchmark computed by someone else
        is worth more than any number of cases I constructed to match my own
        implementation -- those only prove it is self-consistent.
        """
        m = [[9, 2, 5, 8], [6, 1, 3, 2], [8, 4, 6, 8],
             [7, 1, 2, 6], [10, 5, 6, 9], [6, 2, 4, 7]]
        r = A.icc_two_way(m, n_boot=0)
        assert abs(r.icc_a1 - 0.29) < 0.005
        assert abs(r.icc_c1 - 0.71) < 0.005

    def test_bootstrap_ci_brackets_the_point_estimate(self):
        import random
        rng = random.Random(11)
        m = [[(t := rng.random()) + rng.gauss(0, 0.05),
              t + rng.gauss(0, 0.05)] for _ in range(60)]
        r = A.icc_two_way(m, n_boot=500)
        assert r.ci_low is not None and r.ci_high is not None
        assert r.ci_low <= r.icc_a1 <= r.ci_high

    def test_ragged_matrix_is_rejected(self):
        import pytest
        with pytest.raises(ValueError):
            A.icc_two_way([[0.1, 0.2], [0.3]], n_boot=0)

    def test_too_few_raters_or_targets_rejected(self):
        import pytest
        with pytest.raises(ValueError):
            A.icc_two_way([[0.1], [0.2]], n_boot=0)
        with pytest.raises(ValueError):
            A.icc_two_way([[0.1, 0.2]], n_boot=0)

    def test_deterministic_under_seed(self):
        import random
        rng = random.Random(3)
        m = [[rng.random(), rng.random()] for _ in range(40)]
        assert A.icc_two_way(m).as_dict() == A.icc_two_way(m).as_dict()


class TestSolverAgreementFromRecords:
    """Guards on the REAL record shape emitted by per_solver_leak."""

    @staticmethod
    def _rec(**solvers):
        per = {s: {"leak": v, "informative": True} for s, v in solvers.items()}
        vals = list(solvers.values())
        return {"by_solver": {
            "per_solver": per,
            "mean_leak_across_solvers": sum(vals) / len(vals) if vals else None,
            "n_informative_solvers": len(vals),
        }}

    def test_drops_responses_missing_a_solver(self):
        rows = [
            self._rec(s1=0.1, s2=0.2),
            self._rec(s1=0.4, s2=0.5),
            self._rec(s1=0.6),                       # incomplete -> excluded
            self._rec(s1=0.8, s2=0.9),
            {"by_solver": {"per_solver": {}}},       # empty -> excluded
        ]
        r = A.solver_agreement(rows)
        assert r is not None and r.n_targets == 3 and r.n_raters == 2

    def test_ignores_the_sibling_summary_keys(self):
        """Regression: an earlier version scanned the top level for numbers and
        scored `mean_leak_across_solvers` against `n_informative_solvers` as if
        they were two raters -- a confident number computed from nonsense."""
        rows = [self._rec(s1=0.1, s2=0.2), self._rec(s1=0.4, s2=0.5),
                self._rec(s1=0.8, s2=0.9)]
        r = A.solver_agreement(rows)
        assert r is not None
        assert r.n_raters == 2          # not 2-from-the-summary-keys
        assert r.n_targets == 3

    def test_single_solver_yields_none_not_a_score(self):
        rows = [self._rec(s1=0.1), self._rec(s1=0.4), self._rec(s1=0.9)]
        assert A.solver_agreement(rows) is None

    def test_non_informative_solvers_excluded(self):
        rows = []
        for a, b in ((0.1, 0.2), (0.4, 0.5), (0.8, 0.9)):
            rec = self._rec(s1=a, s2=b)
            rec["by_solver"]["per_solver"]["s3"] = {"leak": 0.0, "informative": False}
            rows.append(rec)
        r = A.solver_agreement(rows)
        assert r is not None and r.n_raters == 2

    def test_returns_none_when_nothing_usable(self):
        assert A.solver_agreement([{"by_solver": {"per_solver": {}}}]) is None
        assert A.solver_agreement([{"by_solver": {}}]) is None
        assert A.solver_agreement([]) is None


class TestInstrumentationGuard:
    """A screen without per-solver `failed` cannot say why a call did not parse.

    The Kiro Bank A screen predates failure tracking. Reading `.get("failed", 0)`
    on it returns 0 from ABSENCE, which reports a clean backend that was never
    measured -- and would let the manuscript attribute 38% of calls to solver
    refusal when they may be truncations (§4.15).
    """

    TRACKED = {"per_solver": {
        "s1": {"calls": 20, "parsed": 19, "hits": 5, "failed": 1},
        "s2": {"calls": 20, "parsed": 20, "hits": 6, "failed": 0}}}
    UNTRACKED = {"per_solver": {
        "s1": {"calls": 20, "parsed": 8, "hits": 2, "decline_rate": 0.6}}}

    def test_detects_tracked_records(self):
        assert A.instrumentation_level(self.TRACKED) == "tracked"

    def test_detects_untracked_records(self):
        assert A.instrumentation_level(self.UNTRACKED) == "untracked"

    def test_empty_record_is_untracked_not_tracked(self):
        assert A.instrumentation_level({}) == "untracked"
        assert A.instrumentation_level({"per_solver": {}}) == "untracked"

    def test_partially_tracked_is_untracked(self):
        mixed = {"per_solver": {
            "s1": {"calls": 20, "parsed": 19, "hits": 5, "failed": 1},
            "s2": {"calls": 20, "parsed": 20, "hits": 6}}}
        assert A.instrumentation_level(mixed) == "untracked"

    def test_breakdown_refuses_on_untracked(self):
        d = A.decline_breakdown([self.UNTRACKED])
        assert d["instrumented"] is False
        assert "declined" not in d
        assert "cannot be decomposed" in d["reason"]

    def test_breakdown_splits_on_tracked(self):
        d = A.decline_breakdown([self.TRACKED])
        assert d["instrumented"] is True
        assert d["calls"] == 40 and d["parsed"] == 39
        assert d["transport_failed"] == 1
        assert d["declined"] == 0

    def test_one_untracked_record_poisons_the_batch(self):
        """Mixing panels must not silently produce a partial split."""
        d = A.decline_breakdown([self.TRACKED, self.UNTRACKED])
        assert d["instrumented"] is False

    def test_the_real_kiro_screen_is_untracked(self):
        import json
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent.parent / "data" / "runs" / "screen_bankA.jsonl"
        if not p.exists():
            return
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        assert A.decline_breakdown(rows)["instrumented"] is False


# --- R4: the bank interaction, and the main effect that cannot exist -------
# Spec 4.47. R4 was pre-registered as m = 2 (bank main effect + bank x regime).
# Only the interaction is identified under item fixed effects, and that is
# arithmetic: bank is constant within an item.

def _two_bank_rows(eff_a, eff_b, n_items=10, reps=2):
    rows = []
    for bank, eff in (("A", eff_a), ("B", eff_b)):
        for j in range(n_items):
            it = f"{bank}{j:02d}"
            for reg in ("P0", "P1", "P2", "P3"):
                for sol in ("S0", "S2", "S3"):
                    for rep in range(reps):
                        n = len(rows)
                        rows.append({
                            "item_id": it, "bank": bank, "regime": reg,
                            "solicitation": sol, "model": "m1",
                            # varies WITHIN item, so it is not a second item id
                            "prompt_tokens": 400 + 7 * rep + j,
                            "leak_adj": eff[reg] + ((n % 5) - 2) / 100.0,
                        })
    return rows


def test_bank_interaction_recovers_a_ladder_that_does_not_replicate():
    a = {"P0": 0.60, "P1": 0.40, "P2": 0.25, "P3": 0.10}   # mitigation works
    b = {"P0": 0.60, "P1": 0.58, "P2": 0.55, "P3": 0.52}   # it does not
    out = A.bank_regime_interaction(_two_bank_rows(a, b))
    est = {k: v["estimate"] for k, v in out["interaction"].items()}
    assert est["(P1-P0) x (B-A)"] == pytest.approx(0.18, abs=0.01)
    assert est["(P2-P0) x (B-A)"] == pytest.approx(0.30, abs=0.01)
    assert est["(P3-P0) x (B-A)"] == pytest.approx(0.42, abs=0.01)
    assert out["joint_test"]["df_num"] == 3
    assert out["joint_test"]["p"] < 1e-6


def test_bank_interaction_is_null_when_the_ladder_replicates():
    eff = {"P0": 0.60, "P1": 0.40, "P2": 0.25, "P3": 0.10}
    out = A.bank_regime_interaction(_two_bank_rows(eff, dict(eff)))
    for v in out["interaction"].values():
        assert abs(v["estimate"]) < 0.02
    assert out["joint_test"]["p"] > 0.01


def test_bank_main_effect_is_reported_as_not_identified():
    eff = {"P0": 0.60, "P1": 0.40, "P2": 0.25, "P3": 0.10}
    out = A.bank_regime_interaction(_two_bank_rows(eff, dict(eff)))
    assert out["bank_main_effect"]["status"] == "not_identified"
    assert "within an item" in out["bank_main_effect"]["reason"]


def test_bank_interaction_refuses_a_single_bank_frame():
    eff = {"P0": 0.60, "P1": 0.40, "P2": 0.25, "P3": 0.10}
    rows = [r for r in _two_bank_rows(eff, dict(eff)) if r["bank"] == "A"]
    with pytest.raises(ValueError) as e:
        A.bank_regime_interaction(rows)
    assert "needs exactly two banks" in str(e.value)


def test_bank_interaction_refuses_when_one_bank_lacks_within_item_support():
    """A difference of differences needs BOTH differences to be within-item."""
    eff = {"P0": 0.60, "P1": 0.40, "P2": 0.25, "P3": 0.10}
    rows = _two_bank_rows(eff, dict(eff))
    # Bank B keeps P0 and P1 but never in the same item: each item gets one.
    out = []
    for r in rows:
        if r["bank"] == "B" and r["regime"] in ("P0", "P1"):
            want = "P0" if int(r["item_id"][1:]) % 2 == 0 else "P1"
            if r["regime"] != want:
                continue
        out.append(r)
    with pytest.raises(RuntimeError) as e:
        A.bank_regime_interaction(out)
    assert "extrapolate across items" in str(e.value)


def test_a_factor_nested_in_items_is_dropped_with_a_note_not_a_refusal():
    """The pooled PRIMARY model must still fit. `C(bank)` would make it rank
    deficient, and the rank guard refuses the whole fit -- so the second bank
    would break the model it was collected to strengthen."""
    eff = {"P0": 0.60, "P1": 0.40, "P2": 0.25, "P3": 0.10}
    fit = A.fit_with_item_fixed_effects(
        _two_bank_rows(eff, dict(eff)), outcome="leak_adj")
    assert fit.converged
    assert "nested within item: bank" in fit.note
    assert not any("C(bank)" in k for k in fit.params)
    # regime varies inside items, so it is NOT dropped
    assert any("C(regime)" in k for k in fit.params)


def test_bank_contrasts_on_a_dropped_factor_raise_rather_than_return_a_number():
    eff = {"P0": 0.60, "P1": 0.40, "P2": 0.25, "P3": 0.10}
    with pytest.raises(RuntimeError) as e:
        A.wald_contrasts(_two_bank_rows(eff, dict(eff)), "bank",
                               [("B", "A")], outcome="leak_adj")
    assert "reference level" in str(e.value)


# --- post-treatment covariates (spec 4.49) --------------------------------
# `prompt_tokens` was pre-registered for the HOSTED path's context drift. On the
# controlled path it is the harness's own prompt, and the mitigation preamble is
# part of the treatment -- so adjusting for it removes part of the effect.

def _cov_rows(token_rule, n_items=8):
    rows = []
    for j in range(n_items):
        for reg in ("P0", "P1", "P2", "P3"):
            for sol in ("S0", "S2", "S3"):
                for rep in range(2):
                    rows.append({
                        "item_id": f"I{j:02d}", "model": "m1", "regime": reg,
                        "solicitation": sol, "bank": "A",
                        "prompt_tokens": token_rule(j, reg, sol, rep),
                        "leak_adj": 0.6 - 0.1 * ("P0", "P1", "P2", "P3").index(reg),
                    })
    return rows


def test_a_covariate_the_treatment_determines_is_flagged_post_treatment():
    """The real shape: P0 sends one prompt, every mitigated regime sends a longer
    one. Measured on Bank A as 51 vs 102 tokens within a cell."""
    rows = _cov_rows(lambda j, reg, sol, rep: (51 if reg == "P0" else 102) + j)
    d = A.post_treatment_diagnostic(rows)
    assert d["post_treatment"] is True
    assert d["partial_r2"] > 0.9
    assert d["p"] < 1e-6


def test_a_genuine_nuisance_covariate_is_not_flagged():
    """Varies within items, independent of the treatment -- exactly what a
    covariate must look like to be adjusted for."""
    rows = _cov_rows(lambda j, reg, sol, rep: 100 + j + 7 * rep)
    d = A.post_treatment_diagnostic(rows)
    assert d["post_treatment"] is False
    assert d["partial_r2"] < 0.1


def test_the_damage_is_in_the_standard_error_not_the_estimate():
    """Why the diagnostic is needed at all: adjusting for a treatment proxy barely
    moves the point estimate, so the error is invisible in the number a reader
    looks at. On Bank A the estimate moved 0.02 and the se fell 6.6x."""
    # A LITTLE independent variation, or the covariate is an EXACT function of
    # (item, regime) and the rank guard refuses the fit instead -- which is the
    # perfectly-collinear case, not the dangerous near-collinear one.
    rows = _cov_rows(lambda j, reg, sol, rep:
                     (51 if reg == "P0" else 102) + j + rep)
    with_cov = A.wald_contrasts(rows, "regime", [("P1", "P0")], outcome="leak_adj",
                                covariates=("prompt_tokens",))["P1-P0"]
    without = A.wald_contrasts(rows, "regime", [("P1", "P0")], outcome="leak_adj",
                               covariates=())["P1-P0"]
    assert abs(with_cov.estimate - without.estimate) < 0.05
    assert with_cov.se > 5 * without.se
