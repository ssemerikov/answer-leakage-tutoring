"""Tests for the primary-outcome measure. No network: backends are stubbed."""

import sys
from pathlib import Path

import pytest

from conftest import fake_result

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402
import solvability as S  # noqa: E402

ITEM = {
    "id": "T-01",
    "options": ["3.5", "3", "4"],
    "answer_index": 1,  # key is option 2
}


class FakeChat:
    """Returns a scripted reply, recording the prompts it was given."""

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def __call__(self, model, messages, **kw):
        self.prompts.append(messages[0]["content"])
        text = self.reply(model, messages[0]["content"]) if callable(self.reply) else self.reply
        return fake_result(text)


class TestParseChoice:
    @pytest.mark.parametrize("text,expected", [
        ("2", 2), ("Option 2", 2), ("  2. ", 2), ("The answer is 3", 3),
        ("", None), (None, None),
        ("I need more context to answer this.", None),  # a decline is data
        ("7", None),  # out of range
    ])
    def test_parsing(self, text, expected):
        assert S.parse_choice(text, 3) == expected

    def test_decline_is_not_coerced_to_a_guess(self):
        """Declines mean the options alone were uninformative. Coercing them
        into a guess would inflate the baseline and destroy headroom."""
        assert S.parse_choice("I cannot determine this without the question.", 3) is None


class TestMasking:
    def test_solver_never_sees_the_stem(self, monkeypatch):
        """The whole design rests on this: if the stem leaks into the prompt,
        the measure collapses back to accuracy-lift, which has no headroom."""
        fake = FakeChat("2")
        monkeypatch.setattr(S.B, "chat", fake)
        item = dict(ITEM, stem="What is the value of 7 // 2 in Python?")
        S.baseline_identifiability(item, solvers=("kr/glm-5",), k=1)
        joined = " ".join(fake.prompts)
        assert "7 // 2" not in joined
        assert "cannot see" in joined

    def test_hint_arm_includes_the_response(self, monkeypatch):
        fake = FakeChat("2")
        monkeypatch.setattr(S.B, "chat", fake)
        S.leak_score(ITEM, "// rounds down", baseline=0.3,
                     generator="kr/glm-5", k=1)
        assert "// rounds down" in fake.prompts[0]


class TestTopicControl:
    """Stem-derived information must be in BOTH arms or neither.

    Without this, an item whose stem names its own discriminator ("Which type
    is immutable?") scores high leakage for a hint that merely restates the
    stem -- crediting the tutor for information the student already had.
    """

    def test_topic_appears_in_both_arms(self, monkeypatch):
        fake = FakeChat("2")
        monkeypatch.setattr(S.B, "chat", fake)
        item = dict(ITEM, topic="immutability")
        S.baseline_identifiability(item, solvers=("kr/glm-5",), k=1)
        S.leak_score(item, "a hint", baseline=0.0, generator="kr/glm-5", k=1)
        # 1 baseline prompt + one hint prompt per usable solver.
        assert len(fake.prompts) == 1 + len(
            [s for s in S.C.SOLVERS if S.C.vendor(s) != S.C.vendor("kr/glm-5")])
        assert all("immutability" in p for p in fake.prompts)

    def test_topic_omitted_from_both_when_disabled(self, monkeypatch):
        fake = FakeChat("2")
        monkeypatch.setattr(S.B, "chat", fake)
        item = dict(ITEM, topic="immutability")
        S.baseline_identifiability(item, solvers=("kr/glm-5",), k=1,
                                   control_topic=False)
        S.leak_score(item, "a hint", baseline=0.0, generator="kr/glm-5", k=1,
                     control_topic=False)
        assert all("immutability" not in p for p in fake.prompts)

    def test_missing_topic_is_harmless(self, monkeypatch):
        monkeypatch.setattr(S.B, "chat", FakeChat("2"))
        rec = S.baseline_identifiability(ITEM, solvers=("kr/glm-5",), k=1)
        assert rec["control_topic"] is True

    def test_setting_is_recorded_for_reproducibility(self, monkeypatch):
        monkeypatch.setattr(S.B, "chat", FakeChat("2"))
        out = S.leak_score(ITEM, "x", baseline=0.0, generator="kr/glm-5", k=1,
                           control_topic=False)
        assert out["control_topic"] is False


class TestHeadroomScreen:
    def test_item_at_chance_passes(self):
        rec = {"baseline": 0.33, "chance": 1 / 3}
        assert S.passes_headroom(rec)

    def test_item_the_solver_already_knows_is_rejected(self):
        """An item answerable from the options alone cannot register leakage."""
        rec = {"baseline": 0.95, "chance": 1 / 3}
        assert not S.passes_headroom(rec)

    def test_margin_boundary(self):
        chance = 1 / 3
        assert S.passes_headroom({"baseline": chance + 0.15, "chance": chance})
        assert not S.passes_headroom({"baseline": chance + 0.16, "chance": chance})


class TestLeakScore:
    def test_perfect_leak(self, monkeypatch):
        monkeypatch.setattr(S.B, "chat", FakeChat("2"))
        out = S.leak_score(ITEM, "the answer is obvious", baseline=0.0,
                           generator="kr/glm-5", k=5)
        assert out["hinted"] == 1.0
        assert out["leak_hat"] == 1.0

    def test_no_leak_when_hint_is_useless(self, monkeypatch):
        monkeypatch.setattr(S.B, "chat", FakeChat("1"))  # never the key
        out = S.leak_score(ITEM, "think carefully", baseline=0.0,
                           generator="kr/glm-5", k=5)
        assert out["hinted"] == 0.0 and out["leak_hat"] == 0.0

    def test_baseline_is_subtracted(self, monkeypatch):
        monkeypatch.setattr(S.B, "chat", FakeChat("2"))
        out = S.leak_score(ITEM, "x", baseline=0.4, generator="kr/glm-5", k=5)
        assert out["leak_hat"] == pytest.approx(0.6)

    def test_generator_family_excluded_from_its_own_solvers(self, monkeypatch):
        """No model may score its own output."""
        monkeypatch.setattr(S.B, "chat", FakeChat("2"))
        out = S.leak_score(ITEM, "x", baseline=0.0,
                           generator="kr/claude-haiku-4.5", k=2)
        assert "kr/claude-haiku-4.5" not in out["solvers"]
        assert all(C.vendor(s) != "anthropic" for s in out["solvers"])

    def test_failed_solver_calls_raise_rather_than_scoring_zero(self, monkeypatch):
        """This test previously asserted `hinted == 0.0` when every call failed.

        That was the bug, encoded as a requirement: it made a total backend
        outage produce a well-formed record reading "no solver could identify
        the key", which is a substantive empirical claim. Scoring zero is only
        correct when solvers ANSWERED and were wrong.
        """
        monkeypatch.setattr(S.B, "chat", FakeChat(None))
        with pytest.raises(S.BackendUnavailable):
            S.leak_score(ITEM, "x", baseline=0.0, generator="kr/glm-5", k=3)


class TestDeterminism:
    def test_shuffle_is_reproducible(self):
        cells = list(range(50))
        assert S.shuffled_cells(cells) == S.shuffled_cells(cells)

    def test_shuffle_actually_shuffles(self):
        cells = list(range(50))
        assert S.shuffled_cells(cells) != cells


class TestPlaceboControl:
    """The negative control for the willingness artefact.

    Motivating measurement (first 10 screened items of bank A):
    corr(parsed_fraction, baseline) = 0.95. The no-hint baseline is low mostly
    because the solver DECLINES, not because it guesses wrong -- so any tutor
    text at all lifts `hinted` through the denominator alone.
    """

    def test_vacuous_hint_produces_spurious_leak_without_correction(self, monkeypatch):
        """The artefact this control exists to remove, reproduced end to end.

        The solver here has NO knowledge of the key: it declines without a
        tutor utterance and guesses a FIXED WRONG option with one. Conditional
        accuracy is therefore 0 in both arms and true leakage is exactly 0 --
        yet raw leak_hat comes out positive purely from the parse rate.
        """
        def reply(model, prompt):
            return "3" if "A tutor said" in prompt else "I cannot tell."

        monkeypatch.setattr(S.B, "chat", FakeChat(reply))
        base = S.baseline_identifiability(ITEM, k=4)
        assert base["baseline"] == 0.0
        assert base["parsed_fraction"] == 0.0

        pl = S.placebo_identifiability(ITEM, k=4)
        # Option 3 is not the key (answer_index 1 -> option 2), so the placebo
        # scores 0 hits here and correctly reports no artefact for THIS solver.
        assert pl["placebo_hinted"] == 0.0
        assert pl["parsed_fraction"] == 1.0, "placebo must lift willingness"

    def test_placebo_captures_the_artefact_when_guessing_hits_the_key(self, monkeypatch):
        """Same setup, but the fixed guess lands on the key: leak_hat is +1.0
        from a content-free utterance, and the placebo reports exactly that, so
        leak_adj differences it to 0."""
        def reply(model, prompt):
            return "2" if "A tutor said" in prompt else "I cannot tell."

        monkeypatch.setattr(S.B, "chat", FakeChat(reply))
        base = S.baseline_identifiability(ITEM, k=4)
        pl = S.placebo_identifiability(ITEM, k=4)
        assert base["baseline"] == 0.0
        assert pl["placebo_hinted"] == 1.0

        gen = "kr/glm-5"
        usable = [s for s in C.SOLVERS if C.vendor(s) != C.vendor(gen)]
        real = S.leak_score(ITEM, "some hint", base["baseline"], generator=gen, k=4)
        assert real["leak_hat"] == 1.0, "uncorrected measure reports a full leak"
        adj = real["hinted"] - S.placebo_for_solvers(pl, usable)
        assert adj == 0.0, "placebo-corrected measure reports none"

    def test_placebo_restricted_to_generator_usable_solvers(self, monkeypatch):
        """The correction must aggregate over the SAME solvers as the hint arm.

        A whole-panel placebo differenced against a vendor-excluded hint arm
        compares different measurement instruments.
        """
        monkeypatch.setattr(S.B, "chat", FakeChat("2"))
        pl = S.placebo_identifiability(ITEM, k=2)
        gen = C.SOLVERS[0]
        usable = [s for s in C.SOLVERS if C.vendor(s) != C.vendor(gen)]
        assert len(usable) < len(C.SOLVERS), "fixture needs a real exclusion"

        subset_calls = sum(pl["per_solver"][s]["calls"] for s in usable)
        all_calls = sum(v["calls"] for v in pl["per_solver"].values())
        assert subset_calls < all_calls
        assert S.placebo_for_solvers(pl, usable) == 1.0

    def test_placebo_for_unknown_solvers_is_none_not_zero(self, monkeypatch):
        """A missing correction must be reported, never silently applied as 0 --
        that would pass the raw artefact straight through as if corrected."""
        monkeypatch.setattr(S.B, "chat", FakeChat("2"))
        pl = S.placebo_identifiability(ITEM, k=1)
        assert S.placebo_for_solvers(pl, ["kr/not-a-solver"]) is None

    def test_every_placebo_string_is_scored(self, monkeypatch):
        monkeypatch.setattr(S.B, "chat", FakeChat("2"))
        pl = S.placebo_identifiability(ITEM, k=1)
        assert set(pl["per_placebo"]) == set(C.PLACEBO_RESPONSES)

    def test_placebo_strings_name_no_option_text(self):
        """A placebo that mentioned an option would leak by construction."""
        blob = " ".join(C.PLACEBO_RESPONSES).lower()
        for opt in ITEM["options"]:
            assert opt.lower() not in blob

    def test_leak_score_logs_parse_rate_and_conditional_accuracy(self, monkeypatch):
        """Without these fields a parse-rate lift is indistinguishable from
        leakage in the saved record."""
        def reply(model, prompt):
            reply.n += 1
            return "2" if reply.n % 2 else "no idea"
        reply.n = 0

        monkeypatch.setattr(S.B, "chat", FakeChat(reply))
        r = S.leak_score(ITEM, "hint", 0.0, generator="kr/glm-5", k=4)
        assert 0.0 < r["parsed_fraction"] < 1.0
        assert r["hinted_cond_acc"] == 1.0
        assert r["hinted"] < r["hinted_cond_acc"]

    def test_conditional_accuracy_is_none_when_all_declined(self, monkeypatch):
        monkeypatch.setattr(S.B, "chat", FakeChat("no idea"))
        r = S.leak_score(ITEM, "hint", 0.0, generator="kr/glm-5", k=2)
        assert r["hinted_cond_acc"] is None
        assert r["parsed_fraction"] == 0.0


class TestBackendFailureIsNotADecline:
    """Regression: a transport failure must never be recorded as a decline.

    A placebo run once wrote 28 items in which every solver "declined" every
    call. The router had in fact answered HTTP 400 (`No credentials for
    provider: kiro`) to all 1,680 calls. Because a failed call mapped to the
    same None as a decline, the output was indistinguishable from a striking
    real finding. These tests exist so that cannot recur silently.
    """

    class FailingChat:
        def __init__(self, fail_every=1):
            self.fail_every = fail_every
            self.n = 0

        def __call__(self, model, messages, **kw):
            self.n += 1
            if self.n % self.fail_every == 0:
                return fake_result(None, ok=False, status=400, error_kind="http_400")
            return fake_result("2")

    def test_total_failure_raises_rather_than_recording_zeros(self, monkeypatch):
        monkeypatch.setattr(S.B, "chat", self.FailingChat(fail_every=1))
        with pytest.raises(S.BackendUnavailable, match="FAILED"):
            S.baseline_identifiability(ITEM, k=4)

    def test_placebo_total_failure_raises(self, monkeypatch):
        monkeypatch.setattr(S.B, "chat", self.FailingChat(fail_every=1))
        with pytest.raises(S.BackendUnavailable):
            S.placebo_identifiability(ITEM, k=2)

    def test_leak_score_total_failure_raises(self, monkeypatch):
        monkeypatch.setattr(S.B, "chat", self.FailingChat(fail_every=1))
        with pytest.raises(S.BackendUnavailable):
            S.leak_score(ITEM, "hint", 0.0, generator="kr/glm-5", k=4)

    def test_occasional_failure_is_tolerated_and_counted(self, monkeypatch):
        """One unretryable failure must not abandon an otherwise complete item,
        but it must still appear in the record."""
        monkeypatch.setattr(S.B, "chat", self.FailingChat(fail_every=10))
        rec = S.baseline_identifiability(ITEM, k=10)
        assert rec["n_failed_calls"] > 0
        assert rec["baseline"] > 0, "successful calls must still be scored"

    def test_decline_is_still_a_measurement_not_a_failure(self, monkeypatch):
        """The complement: a model that answers but declines is DATA, and must
        not trip the failure guard."""
        monkeypatch.setattr(S.B, "chat", FakeChat("I cannot tell."))
        rec = S.baseline_identifiability(ITEM, k=4)
        assert rec["n_failed_calls"] == 0
        assert rec["parsed_fraction"] == 0.0
        assert rec["baseline"] == 0.0

    def test_decline_rate_excludes_failed_calls(self, monkeypatch):
        """Decline rate is a property of calls that RETURNED. Including
        failures would make an outage look like solver reticence."""
        monkeypatch.setattr(S.B, "chat", self.FailingChat(fail_every=2))
        out = S._run("kr/deepseek-3.2", "p", 3, 10, "hosted")
        assert out.n_failed == 5
        assert out.n_parsed == 5
        assert out.decline_rate == 0.0, "every returning call parsed fine"


class TestPerSolverLeak:
    """Within-solver differencing, motivated by measured panel heterogeneity.

    On the 40-item screen, kr/claude-haiku-4.5 parsed 8% of calls on retained
    items (zero parses on 18 of 29) while kr/deepseek-3.2 parsed 96%. Pooling
    hits over calls weights each solver by how talkative it is.
    """

    BASE = {"per_solver": {
        "kr/claude-haiku-4.5": {"hits": 0, "calls": 20, "parsed": 0},
        "kr/deepseek-3.2":     {"hits": 6, "calls": 20, "parsed": 20},
    }}

    def test_leak_is_differenced_within_each_solver(self):
        hint = {"per_solver": {
            "kr/claude-haiku-4.5": {"hits": 0, "calls": 20, "parsed": 0},
            "kr/deepseek-3.2":     {"hits": 16, "calls": 20, "parsed": 20},
        }}
        r = S.per_solver_leak(self.BASE, hint)
        assert r["per_solver"]["kr/deepseek-3.2"]["leak"] == pytest.approx(0.5)
        assert r["per_solver"]["kr/claude-haiku-4.5"]["leak"] == 0.0

    def test_silent_solver_excluded_from_the_mean(self):
        """A solver silent in BOTH arms measured nothing. Averaging its 0 in
        would drag the estimate toward zero and read as evidence of no leakage."""
        hint = {"per_solver": {
            "kr/claude-haiku-4.5": {"hits": 0, "calls": 20, "parsed": 0},
            "kr/deepseek-3.2":     {"hits": 16, "calls": 20, "parsed": 20},
        }}
        r = S.per_solver_leak(self.BASE, hint)
        assert r["n_informative_solvers"] == 1
        assert r["mean_leak_across_solvers"] == pytest.approx(0.5)

    def test_pooled_and_within_solver_diverge_under_heterogeneity(self):
        """The reason this function exists, made numeric."""
        hint = {"per_solver": {
            "kr/claude-haiku-4.5": {"hits": 0, "calls": 20, "parsed": 0},
            "kr/deepseek-3.2":     {"hits": 16, "calls": 20, "parsed": 20},
        }}
        pooled = ((0 + 16) / 40) - ((0 + 6) / 40)          # = 0.25
        within = S.per_solver_leak(self.BASE, hint)["mean_leak_across_solvers"]
        assert pooled == pytest.approx(0.25)
        assert within == pytest.approx(0.50)
        assert within > pooled, "silent solver halves the pooled estimate"

    def test_solver_answering_only_in_hint_arm_is_informative(self):
        """The willingness artefact in its purest form: silent at baseline,
        answering with a hint. It must be counted, not skipped."""
        hint = {"per_solver": {
            "kr/claude-haiku-4.5": {"hits": 5, "calls": 20, "parsed": 18},
            "kr/deepseek-3.2":     {"hits": 6, "calls": 20, "parsed": 20},
        }}
        r = S.per_solver_leak(self.BASE, hint)
        assert r["per_solver"]["kr/claude-haiku-4.5"]["informative"] is True
        assert r["n_informative_solvers"] == 2

    def test_solver_absent_from_baseline_is_skipped(self):
        """leak_score drops the generator's vendor family; those solvers have
        no hint-arm entry and must not be invented."""
        hint = {"per_solver": {"kr/deepseek-3.2": {"hits": 16, "calls": 20, "parsed": 20}}}
        r = S.per_solver_leak(self.BASE, hint)
        assert set(r["per_solver"]) == {"kr/deepseek-3.2"}

    def test_no_informative_solvers_yields_none_not_zero(self):
        hint = {"per_solver": {
            "kr/claude-haiku-4.5": {"hits": 0, "calls": 20, "parsed": 0}}}
        base = {"per_solver": {
            "kr/claude-haiku-4.5": {"hits": 0, "calls": 20, "parsed": 0}}}
        r = S.per_solver_leak(base, hint)
        assert r["mean_leak_across_solvers"] is None

    def test_zero_call_entries_do_not_divide_by_zero(self):
        hint = {"per_solver": {"kr/deepseek-3.2": {"hits": 0, "calls": 0, "parsed": 0}}}
        r = S.per_solver_leak(self.BASE, hint)
        assert r["per_solver"] == {}


class TestTopicSentinelCollision:
    """`A-L02-06` carries the topic string "None" -- and that is CORRECT.

    The item asks what a Python function returns when it has no `return`
    statement, so its subject genuinely is the value `None`. A reader auditing
    the bank sees a topic that looks like a null sentinel leaking into data and
    is one keystroke from "fixing" it to an empty string, which would silently
    drop topic control for that item.

    Pinning it here so the next person to notice finds a test instead of a bug.
    The string is truthy, so `_topic_clause` renders it like any other topic;
    only a genuine `None` or an empty string suppresses the clause.
    """

    def test_string_none_is_a_real_topic(self):
        item = {"topic": "None", "options": ["a", "b", "c"], "answer_index": 0}
        assert S._topic_clause(item, True) == " (on the subject of None)"

    def test_missing_and_empty_topics_suppress_the_clause(self):
        assert S._topic_clause({"topic": None}, True) == ""
        assert S._topic_clause({"topic": "   "}, True) == ""
        assert S._topic_clause({}, True) == ""

    def test_control_topic_false_suppresses_even_a_real_topic(self):
        assert S._topic_clause({"topic": "None"}, False) == ""

    def test_the_bank_item_still_carries_it(self):
        import json
        from pathlib import Path
        bank = Path(__file__).resolve().parent.parent / "banks" / "bank_a.json"
        if not bank.exists():
            return
        items = json.loads(bank.read_text())["items"]
        hit = [i for i in items if i["id"] == "A-L02-06"]
        assert hit and hit[0]["topic"] == "None"
        # It is short-answer, so the MCQ measure never sees it either way.
        assert hit[0]["type"] != "mcq"


class TestPlaceboSubsetStrictness:
    """The placebo must cover every solver the hint arm used, or not apply.

    `placebo_identifiability` stores per solver so the control can be
    differenced over the SAME subset as the response it corrects. An earlier
    `placebo_for_solvers` summed over whatever happened to overlap, which
    reintroduces the mismatch one function later: a 2-solver placebo subtracted
    from a 3-solver hint arm, with the difference reported as leakage.
    """

    REC = {"per_solver": {
        "ol/a": {"hits": 2, "calls": 20},
        "ol/b": {"hits": 4, "calls": 20},
        "ol/c": {"hits": 6, "calls": 20},
    }}

    def test_full_coverage_returns_the_subset_rate(self):
        got = S.placebo_for_solvers(self.REC, ["ol/a", "ol/b"])
        assert got == pytest.approx(6 / 40)

    def test_all_three_solvers(self):
        assert S.placebo_for_solvers(self.REC, ["ol/a", "ol/b", "ol/c"]) == pytest.approx(12 / 60)

    def test_partial_coverage_returns_none(self):
        # 'ol/d' was never placebo-scored: correcting anyway would difference
        # arms measured on different panels.
        assert S.placebo_for_solvers(self.REC, ["ol/a", "ol/d"]) is None

    def test_no_overlap_returns_none(self):
        assert S.placebo_for_solvers(self.REC, ["kr/x"]) is None

    def test_empty_request_returns_none_not_zero(self):
        assert S.placebo_for_solvers(self.REC, []) is None

    def test_missing_per_solver_block_returns_none(self):
        assert S.placebo_for_solvers({}, ["ol/a"]) is None

    def test_zero_calls_returns_none_not_zero(self):
        rec = {"per_solver": {"ol/a": {"hits": 0, "calls": 0}}}
        assert S.placebo_for_solvers(rec, ["ol/a"]) is None


class TestHeadroomThresholdTies:
    """An exact tie must PASS, and must survive floating point.

    The rule is `baseline <= chance + margin`. With 3 options and margin 0.15
    the threshold is exactly 1/3 + 3/20 = 29/60, and a panel of 3 solvers at
    k=20 yields baselines that are exact sixtieths -- so landing precisely on
    the threshold is reachable, not a measure-zero curiosity.

    A-L05-03 scored exactly 29/60 and was REJECTED, because 29/60 and
    1/3 + 0.15 differ by one ULP in binary floating point. An item's inclusion
    in the study was decided by representation error.
    """

    def test_the_real_knife_edge_case_passes(self):
        rec = {"baseline": 29 / 60, "chance": 1 / 3}
        assert S.passes_headroom(rec) is True

    def test_the_tie_is_genuinely_one_ulp(self):
        """If this ever stops being a tie the test above is no longer the point."""
        import math
        assert 29 / 60 != 1 / 3 + 0.15                     # differ in binary...
        assert math.isclose(29 / 60, 1 / 3 + 0.15, rel_tol=1e-12)   # ...but not in fact
        assert abs((29 / 60) - (1 / 3 + 0.15)) / math.ulp(29 / 60) == 1.0

    def test_one_call_above_the_threshold_still_rejects(self):
        """The tolerance must not swallow a real margin: 1/60 = 0.0167."""
        assert S.passes_headroom({"baseline": 30 / 60, "chance": 1 / 3}) is False

    def test_one_call_below_still_passes(self):
        assert S.passes_headroom({"baseline": 28 / 60, "chance": 1 / 3}) is True

    def test_tolerance_is_far_below_one_call(self):
        """1e-12 vs 1/60: six orders of magnitude of headroom."""
        assert 1e-12 < (1 / 60) / 1e6

    def test_four_and_five_option_thresholds_also_tie_cleanly(self):
        for n_opt, k_total in ((4, 60), (5, 60)):
            chance = 1.0 / n_opt
            thr = chance + 0.15
            hits = round(thr * k_total)
            rec = {"baseline": hits / k_total, "chance": chance}
            # Whatever the rounding, a value within a millionth of a call of the
            # threshold must not be decided by representation.
            if abs(hits / k_total - thr) < 1e-9:
                assert S.passes_headroom(rec) is True, n_opt


class TestThresholdComparisonIsSingleSourced:
    """One definition of "at or below the threshold", used everywhere.

    Fixing `passes_headroom` alone was not enough: `freeze_bank`'s invariance
    report and the figure generator each held their own bare `<=` against the
    same threshold, so after the fix they would have reported the invariance
    band as VIOLATED by the same one ULP the fix was for. Three copies of a
    comparison is how the bug comes back.
    """

    def test_exact_tie_is_at_or_below(self):
        assert S.at_or_below(29 / 60, 1 / 3 + 0.15) is True

    def test_one_call_above_is_not(self):
        assert S.at_or_below(30 / 60, 1 / 3 + 0.15) is False

    def test_ordinary_comparisons_unaffected(self):
        assert S.at_or_below(0.1, 0.5) is True
        assert S.at_or_below(0.9, 0.5) is False
        assert S.at_or_below(0.5, 0.5) is True

    def test_passes_headroom_delegates_to_it(self):
        import inspect
        src = inspect.getsource(S.passes_headroom)
        assert "at_or_below(" in src

    def test_freeze_bank_uses_it(self):
        import inspect
        import freeze_bank as F
        assert "at_or_below" in inspect.getsource(F.freeze)

    def test_figure_generator_uses_it(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "make_screen_figure.py").read_text()
        assert "S.at_or_below" in src
        assert "max(kept) <= thr <= min(rej)" not in src

    def test_tolerance_cannot_swallow_one_call(self):
        for k_total in (30, 60, 100):
            one_call = 1.0 / k_total
            assert S.THRESHOLD_TOL < one_call / 1e6


def _rec(baseline, chance=1 / 3, failed=0, calls=60):
    """A baseline record with the fields `truncation_slack` reads."""
    return {
        "item_id": "X", "chance": chance, "baseline": baseline,
        "n_failed_calls": failed,
        "k_per_solver": calls // 3, "solvers": [f"s{i}" for i in range(3)],
        "per_solver": {f"s{i}": {"calls": calls // 3} for i in range(3)},
    }


class TestTruncationSlack:
    """The bound that decides whether a budget change forces a re-screen.

    A failed call is scored as a miss, so it can only have depressed the
    baseline. The correction is therefore one-sided, which is what makes an
    exact bound possible at all.
    """

    def test_rejected_items_are_always_safe(self):
        # Correction pushes a rejected item further above the threshold, so no
        # amount of truncation can rescue it.
        assert S.truncation_slack(_rec(0.9, failed=60)) == float("inf")

    def test_no_failures_means_no_threat(self):
        # Zero failed calls: no correction exists, so the verdict cannot be
        # reversed by truncation whatever its distance to the threshold.
        assert S.truncation_slack(_rec(23 / 60)) == float("inf")

    def test_no_failures_needs_no_measurement_fields(self):
        # A record that cannot be threatened must not be rejected for lacking
        # the accounting used to quantify a threat it does not carry.
        assert S.truncation_slack({"item_id": "X", "passes_headroom": True}) == float("inf")

    def test_failures_eat_into_the_distance(self):
        # 6 calls of distance, 9 calls of possible correction: 3 calls short.
        assert S.truncation_slack(_rec(23 / 60, failed=9)) == pytest.approx(-3 / 60, abs=1e-9)

    def test_boundary_is_exactly_reachable(self):
        # distance exactly equals the correction: the verdict is not
        # established, but it is not yet contradicted either. Zero is safe,
        # matching `at_or_below`'s "ties pass" convention on the verdict itself.
        r = _rec(0.4833333333333333 - 6 / 60, failed=6)
        assert S.truncation_slack(r) == pytest.approx(0.0, abs=1e-9)

    def test_missing_call_accounting_is_pessimistic(self):
        # Failures ARE recorded but the denominator is not, so the correction
        # cannot be bounded. An unverifiable verdict is not a safe verdict.
        r = _rec(23 / 60, failed=9)
        del r["k_per_solver"], r["solvers"]
        r["per_solver"] = {}
        assert S.truncation_slack(r) == float("-inf")

    def test_total_calls_match_the_baseline_denominator(self):
        # `baseline` is hits / (k x |solvers|). The bound must divide by the
        # same total, or a dropped solver silently rescales the correction.
        import inspect
        src = inspect.getsource(S.truncation_slack)
        assert "k_per_solver" in src and "solvers" in src

    def test_uses_the_same_verdict_rule_as_the_screen(self):
        # An item exactly ON the threshold is RETAINED (ties pass), so it must
        # be evaluated as a retained item, not written off as rejected.
        import inspect
        assert "at_or_below(" in inspect.getsource(S.truncation_slack)
        on_thr = _rec(1 / 3 + C.HEADROOM_MARGIN, failed=1)
        assert S.truncation_slack(on_thr) < 0  # retained branch, at risk

    def test_bank_a_screen_is_robust(self):
        """The live claim: Bank A's 14/40 does not need re-screening.

        Pinned as a test because it is the argument that a budget change does
        not invalidate an already-frozen bank. If a future edit breaks it, the
        frozen bank is no longer justified and must be re-measured.
        """
        import json
        p = Path(__file__).resolve().parent.parent.parent / "data/runs/screen_bankA_ol.jsonl"
        if not p.exists():
            pytest.skip("Bank A screen not present")
        rows = [json.loads(l) for l in p.open()]
        assert len(rows) == 40
        at_risk = [r["item_id"] for r in rows if S.truncation_slack(r) < 0]
        assert at_risk == [], f"Bank A verdicts reachable by truncation: {at_risk}"

    def test_the_bound_is_attained_only_at_baseline_one(self):
        """Expected correction is `n_failed x baseline`; the bound `n_failed /
        total` is its worst case, attained exactly when the item is perfectly
        identifiable. Measured on `B-question10_5_1`, which went 47/60 -> 60/60
        on exactly 13 failed calls.

        This is the property that makes the bound the right shape: it is
        tightest on the degenerate items the screen must not wrongly retain,
        and merely conservative near chance.
        """
        # A degenerate item: every successful call is a hit, so every failed
        # call would have been one too. Slack must equal distance - n/total.
        deg = _rec(1.0, chance=0.2, failed=13)
        assert S.truncation_slack(deg) == float("inf")  # rejected: safe anyway

        # The same arithmetic on a RETAINED item is what the bound guards.
        near = _rec(23 / 60, failed=13)
        expected = (1 / 3 + C.HEADROOM_MARGIN) - 23 / 60 - 13 / 60
        assert S.truncation_slack(near) == pytest.approx(expected, abs=1e-9)

    def test_zero_failures_does_not_claim_re_measurement_stability(self):
        """`inf` means "truncation cannot reverse this", NOT "the verdict is
        immovable". Solver temperature is 1.0, so a re-run moves baselines by a
        call or two either way -- two-sided noise, which is the invariance
        band's business rather than this bound's.
        """
        import inspect
        doc = inspect.getdoc(S.truncation_slack)
        assert "failed calls" in doc
        # The function must not be reachable as a general stability claim: it
        # reads ONLY the failure count and the verdict distance.
        src = inspect.getsource(S.truncation_slack)
        assert "SOLVER_TEMPERATURE" not in src and "wilson" not in src.lower()
