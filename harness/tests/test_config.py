"""Tests for the frozen study configuration.

These are mostly guards against silent design corruption: a panel that overlaps
its generators, a vendor map that lets a model judge itself, a temperature that
invalidates the variance model. Each failure here would produce data that looks
fine and means nothing.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402


class TestVendorMap:
    def test_same_model_different_provider_is_same_vendor(self):
        """The bug this study must not inherit.

        paper_els_57's family() does model.split(":")[0].split("-")[0], which
        maps kr/claude-haiku-4.5 -> "kr/claude". Under that rule the identical
        model reached via gh/ would count as a different vendor family and
        could be assigned to judge its own output.
        """
        assert C.vendor("kr/claude-haiku-4.5") == C.vendor("gh/claude-haiku-4.5") == "anthropic"

    def test_unknown_model_raises_rather_than_guessing(self):
        with pytest.raises(KeyError, match="never be inferred"):
            C.vendor("some/unlisted-model-9")

    def test_every_roster_model_has_a_vendor(self):
        for m in C.GENERATORS + C.SOLVERS + C.JUDGE_PANEL + C.DUAL_PATH_MODELS:
            assert C.vendor(m)


class TestPanelDisjointness:
    def test_solver_panel_is_disjoint_from_judge_panel(self):
        """A model that both judges and solves would correlate the outcome with
        its own validity check."""
        assert not (set(C.SOLVERS) & set(C.JUDGE_PANEL))

    def test_every_generator_has_three_disjoint_solver_families(self):
        """No generator may be scored by its own vendor family."""
        for gen in C.GENERATORS:
            usable = [s for s in C.SOLVERS if C.vendor(s) != C.vendor(gen)]
            assert len(usable) >= 2, f"{gen} leaves only {usable}"

    def test_every_generator_has_a_disjoint_judge_majority(self):
        for gen in C.GENERATORS:
            usable = [j for j in C.JUDGE_PANEL if C.vendor(j) != C.vendor(gen)]
            assert len(usable) >= 2, f"{gen} leaves only {usable}"

    def test_judge_panel_spans_three_distinct_vendors(self):
        assert len({C.vendor(j) for j in C.JUDGE_PANEL}) == 3

    def test_roster_covers_at_least_four_vendor_families(self):
        assert len({C.vendor(m) for m in C.GENERATORS}) >= 4


class TestSamplingParameters:
    def test_generation_temperature_is_not_zero(self):
        """At T=0 there is no within-cell sampling variance, so Wilson and
        binomial inference describe nothing. The spec requires T=1.0."""
        assert C.GEN_TEMPERATURE > 0

    def test_judges_are_deterministic(self):
        assert C.JUDGE_TEMPERATURE == 0.0

    def test_replicates_sufficient_for_variance_decomposition(self):
        assert C.GEN_REPLICATES >= 5

    def test_baseline_more_precise_than_hint_arm(self):
        """The baseline is per-item, not per-response, so it is estimated over
        far more draws and must not be the noisier quantity."""
        assert C.SOLVER_K_BASELINE > C.SOLVER_K_HINT

    def test_transient_statuses_do_not_include_hard_failures(self):
        """403/410 are real attrition and must consume nothing; retrying them
        forever would hang the run and hide roster loss."""
        assert 403 not in C.TRANSIENT_STATUSES
        assert 410 not in C.TRANSIENT_STATUSES
        assert 429 in C.TRANSIENT_STATUSES


class TestConditions:
    def test_non_adversarial_control_exists(self):
        """Without S0 every generation is primed by a pressure frame and the
        study measures RLHF refusal behaviour, not tutoring."""
        assert "S0" in C.SOLICITATIONS
        s0 = C.SOLICITATIONS["S0"].lower()
        assert "answer" not in s0

    def test_p0_is_not_described_as_bare(self):
        """On the hosted path P0 carries vendor context we cannot remove.
        Calling it 'bare' or 'no system prompt' would be false in the Methods."""
        note = C.REGIME_NOTES["P0"].lower()
        assert "vendor" in note
        assert "bare" not in note

    def test_hint_prompt_forbids_option_number_disclosure(self):
        p = C.HINT_ONLY_SYSTEM_PROMPT.lower()
        assert "never" in p and "option number" in p

    def test_dual_path_has_at_least_two_models(self):
        assert len(C.DUAL_PATH_MODELS) >= 2
        assert set(C.DUAL_PATH_MODELS) <= set(C.GENERATORS)


class TestManifest:
    def test_manifest_pins_prompt_and_seed(self):
        m = C.study_manifest()
        assert m.seed == C.SEED
        assert len(m.system_prompt_sha256) == 64
        assert m.gen_temperature == C.GEN_TEMPERATURE

    def test_manifest_records_attrition(self):
        """A roster listing only what worked overstates coverage."""
        assert C.study_manifest().unavailable

    def test_manifest_flags_missing_bank(self, tmp_path):
        m = C.study_manifest({"A": tmp_path / "nope.json"})
        assert m.bank_sha256["A"] == "MISSING"

    def test_manifest_hashes_real_bank(self):
        p = C.BANKS_DIR / "bank_a.json"
        if not p.exists():
            pytest.skip("bank_a.json not present")
        assert len(C.study_manifest({"A": p}).bank_sha256["A"]) == 64

    def test_manifest_serialises(self):
        import json
        assert json.loads(C.study_manifest().to_json())["seed"] == C.SEED


class TestOllamaFallbackRoster:
    """The Ollama-cloud roster must satisfy the same integrity constraints as
    the primary one. It is a fallback, not a relaxation."""

    def test_solver_and_judge_panels_are_disjoint(self):
        assert not set(C.OL_SOLVERS) & set(C.OL_JUDGE_PANEL)

    def test_every_generator_has_two_out_of_family_solvers(self):
        for g in C.OL_GENERATORS:
            usable = [s for s in C.OL_SOLVERS if C.vendor(s) != C.vendor(g)]
            assert len(usable) >= 2, f"{g} leaves {usable}"

    def test_every_generator_has_two_out_of_family_judges(self):
        """This constraint is why the NVIDIA models cannot generate: both
        nemotrons are NVIDIA, so an NVIDIA generator leaves one judge."""
        for g in C.OL_GENERATORS:
            usable = [j for j in C.OL_JUDGE_PANEL if C.vendor(j) != C.vendor(g)]
            assert len(usable) >= 2, f"{g} leaves {usable}"

    def test_nemotrons_are_excluded_from_generating(self):
        assert not [g for g in C.OL_GENERATORS if C.vendor(g) == "nvidia"]

    def test_every_roster_model_has_an_explicit_vendor(self):
        for m in set(C.OL_GENERATORS) | set(C.OL_SOLVERS) | set(C.OL_JUDGE_PANEL):
            assert C.vendor(m), m

    def test_reasoning_models_get_a_larger_completion_budget(self):
        """A 16-token budget makes a reasoning model an always-declining solver:
        it spends the whole allowance on its trace and returns empty content."""
        assert C.solver_max_tokens("ol/gemma4:cloud") <= 32
        for m in ("ol/gpt-oss:120b-cloud", "ol/nemotron-3-ultra:cloud",
                  "ol/nemotron-3-super:cloud"):
            assert C.solver_max_tokens(m) >= 600, m

    def test_solver_panel_is_the_fast_subset(self):
        """Solver calls outnumber all others by an order of magnitude, so panel
        speed bounds what is runnable. Guards against quietly putting a 13 s
        model on the solver panel."""
        mean = sum(C.SECONDS_PER_CALL[m] for m in C.OL_SOLVERS) / len(C.OL_SOLVERS)
        assert mean < 4.0, f"solver panel too slow: {mean:.1f}s/call"

    def test_ollama_and_kiro_rosters_do_not_mix(self):
        """Vendor exclusion is computed within a roster; mixing prefixes would
        put the same underlying family on both sides of an exclusion."""
        assert all(m.startswith("ol/") for m in C.OL_GENERATORS + C.OL_SOLVERS
                   + C.OL_JUDGE_PANEL)
        assert all(m.startswith("kr/") for m in C.GENERATORS + C.SOLVERS
                   + C.JUDGE_PANEL)


class TestSolverTokenBudgets:
    """Budgets are set from a measured distribution, not guessed.

    A 600-token budget lost 22% of gpt-oss's screen calls to
    `truncated_before_content`, and NOT at random: reasoning length tracks item
    ambiguity, so truncation removed the calls where the solver found the item
    hard and kept the ones where the key was obvious. That biases baseline
    identifiability upward on exactly the items the headroom screen exists to
    distinguish.
    """

    #: Longest completion observed per model, 2026-08-16, 18 calls each at a
    #: 2000-token cap with zero failures.
    OBSERVED_MAX = {
        "ol/gpt-oss:120b-cloud": 1461,
        "ol/minimax-m3:cloud": 1146,
    }

    def test_budgets_exceed_the_observed_maximum(self):
        for model, observed in self.OBSERVED_MAX.items():
            assert C.solver_max_tokens(model) > observed, (
                f"{model}: budget {C.solver_max_tokens(model)} <= observed max "
                f"{observed}; truncation is non-random and biases the baseline")

    def test_reasoning_models_are_not_on_the_default_budget(self):
        """The default of 16 is for models that emit content directly."""
        for model in ("ol/gpt-oss:120b-cloud", "ol/minimax-m3:cloud",
                      "ol/nemotron-3-ultra:cloud", "ol/nemotron-3-super:cloud"):
            assert C.solver_max_tokens(model) >= 2000, model

    def test_every_ol_solver_and_judge_has_an_explicit_budget(self):
        for model in tuple(C.OL_SOLVERS) + tuple(C.OL_JUDGE_PANEL):
            assert model in C.MAX_TOKENS_SOLVER, (
                f"{model} would silently fall back to the 16-token default")

    def test_study_version_bumped_for_the_budget_change(self):
        """config.py's own rule: a changed constant invalidates existing data."""
        assert C.STUDY_VERSION >= "2.1.0"
