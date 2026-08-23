"""Tests for the Tier-2 panel and its reliability statistic.

The alpha tests cross-check against values that can be derived by hand, because
a subtly wrong reliability figure is the kind of error that survives review and
invalidates the instrument-validity claim the panel exists to support.
"""

import sys
from pathlib import Path

import pytest

from conftest import fake_result

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402
import judge as J  # noqa: E402

ITEM = {
    "id": "A-L01-01", "stem": "What is the translator called?",
    "options": ["Translator", "Compiler", "Interpreter"],
    "answer_index": 1, "answer": "Compiler",
}


def stub(reply):
    def _chat(model, messages, **kw):
        text = reply(model) if callable(reply) else reply
        return fake_result(text)
    return _chat


class TestVerdictParsing:
    @pytest.mark.parametrize("text,expected", [
        ("YES", True), ("yes", True), ("Yes.", True), ("YES - it gives it away", True),
        ("NO", False), ("no", False), ("No.", False),
        ("", None), (None, None), ("maybe", None), ("I cannot determine", None),
    ])
    def test_parsing(self, text, expected):
        assert J.parse_verdict(text) is expected

    def test_unparseable_is_not_read_as_no(self):
        """Coercing junk to NO would bias the panel toward 'clean'."""
        assert J.parse_verdict("It depends on the student.") is None


class TestPanelAssignment:
    def test_own_family_excluded(self):
        panel = J.assign_panel("kr/glm-5")  # zhipu
        assert all(C.vendor(j) != "zhipu" for j in panel)

    def test_every_generator_gets_at_least_two_judges(self):
        for g in C.GENERATORS:
            assert len(J.assign_panel(g)) >= 2

    def test_insufficient_panel_raises_rather_than_self_judging(self):
        with pytest.raises(RuntimeError, match="self-judging"):
            J.assign_panel("kr/glm-5", panel=("kr/glm-5", "kr/minimax-m2.5"))


class TestJudging:
    def test_unanimous_yes(self, monkeypatch):
        monkeypatch.setattr(J.B, "chat", stub("YES"))
        r = J.judge_response(ITEM, "It is the compiler.", "kr/claude-haiku-4.5")
        assert r.majority is True and r.unanimous
        assert r.n_parsed == len(r.judges)

    def test_majority_rules(self, monkeypatch):
        seq = {"kr/minimax-m2.5": "YES", "kr/glm-5": "YES",
               "kr/qwen3-coder-next": "NO"}
        monkeypatch.setattr(J.B, "chat", stub(lambda m: seq[m]))
        r = J.judge_response(ITEM, "hint", "kr/claude-haiku-4.5")
        assert r.majority is True and not r.unanimous

    def test_all_judges_failing_raises_rather_than_abstaining(self, monkeypatch):
        """Previously asserted `majority is None`, i.e. an outage recorded as a
        panel that could not decide. That is a claim about judge reliability,
        and it would have been produced by a dead backend rather than by any
        judge. Absent measurements must not be written as data."""
        monkeypatch.setattr(J.B, "chat", stub(None))
        with pytest.raises(J.S.BackendUnavailable):
            J.judge_response(ITEM, "hint", "kr/claude-haiku-4.5")

    def test_unparseable_verdict_is_still_an_abstention(self, monkeypatch):
        """The complement: a judge that ANSWERS but not with yes/no is missing
        data, not a failure, and must not trip the guard."""
        monkeypatch.setattr(J.B, "chat", stub("maybe, it depends"))
        r = J.judge_response(ITEM, "hint", "kr/claude-haiku-4.5")
        assert r.majority is None and r.n_parsed == 0 and r.n_failed == 0

    def test_judge_sees_response_and_answer(self, monkeypatch):
        seen = []

        def _chat(model, messages, **kw):
            seen.append(messages[0]["content"])
            return fake_result("NO")

        monkeypatch.setattr(J.B, "chat", _chat)
        J.judge_response(ITEM, "MY-RESPONSE-TEXT", "kr/claude-haiku-4.5")
        assert "MY-RESPONSE-TEXT" in seen[0]
        assert "Compiler" in seen[0]

    def test_short_answer_item_without_options(self, monkeypatch):
        monkeypatch.setattr(J.B, "chat", stub("NO"))
        item = {"id": "X", "stem": "Which keyword?", "answer": "def"}
        r = J.judge_response(item, "hint", "kr/claude-haiku-4.5")
        assert r.majority is False


class TestKrippendorffAlpha:
    def test_perfect_disagreement_is_negative(self):
        ratings = [{"a": True, "b": False} for _ in range(10)]
        a = J.krippendorff_alpha_nominal(ratings)
        assert a is not None and a < 0

    def test_perfect_agreement_with_variance_is_one(self):
        ratings = ([{"a": True, "b": True}] * 5) + ([{"a": False, "b": False}] * 5)
        assert J.krippendorff_alpha_nominal(ratings) == pytest.approx(1.0)

    def test_no_variance_returns_none(self):
        """All-identical ratings give total agreement but undefined alpha:
        there is no variance for chance correction to work against. Returning
        1.0 here would advertise perfect reliability from a degenerate sample."""
        ratings = [{"a": True, "b": True} for _ in range(10)]
        assert J.krippendorff_alpha_nominal(ratings) is None

    def test_units_with_one_rating_are_excluded(self):
        """Standard definition: a unit rated once cannot show agreement."""
        good = ([{"a": True, "b": True}] * 5) + ([{"a": False, "b": False}] * 5)
        with_singletons = good + [{"a": True}, {"b": False}]
        assert J.krippendorff_alpha_nominal(with_singletons) == pytest.approx(
            J.krippendorff_alpha_nominal(good))

    def test_handles_unequal_rater_counts(self):
        """Panel composition varies by generator, so units genuinely have
        different rater counts; the coincidence-matrix form must cope."""
        ratings = [
            {"a": True, "b": True, "c": True},
            {"a": False, "b": False},
            {"b": True, "c": True},
            {"a": False, "b": False, "c": False},
        ]
        a = J.krippendorff_alpha_nominal(ratings)
        assert a is not None and 0.5 < a <= 1.0

    def test_none_ratings_ignored(self):
        ratings = ([{"a": True, "b": True, "c": None}] * 5
                   + [{"a": False, "b": False, "c": None}] * 5)
        assert J.krippendorff_alpha_nominal(ratings) == pytest.approx(1.0)

    def test_insufficient_data_returns_none(self):
        assert J.krippendorff_alpha_nominal([]) is None
        assert J.krippendorff_alpha_nominal([{"a": True}]) is None


class TestConvergentValidity:
    def test_sensitivity_and_specificity(self):
        panel = [True, True, False, False, None]
        auto = [True, False, True, False, True]
        r = J.agreement_with_measure(panel, auto)
        assert r["n"] == 4          # the None unit is dropped
        assert r["tp"] == 1 and r["fn"] == 1
        assert r["fp"] == 1 and r["tn"] == 1
        assert r["sensitivity"] == pytest.approx(0.5)
        assert r["specificity"] == pytest.approx(0.5)

    def test_empty_classes_yield_none_not_zero(self):
        r = J.agreement_with_measure([True, True], [True, True])
        assert r["sensitivity"] == pytest.approx(1.0)
        assert r["specificity"] is None


class TestPanelIndependence:
    """`assign_panel` guarantees >=2 judges and no self-judging. Neither implies
    the judges are independent: two models from one vendor satisfy both rules
    while supplying a single opinion twice."""

    def test_two_same_vendor_judges_is_flagged_degenerate(self):
        rep = J.panel_independence(["ol/gemma4:cloud"], C.OL_JUDGE_PANEL)
        d = rep["ol/gemma4:cloud"]
        assert d["n_judges"] == 2
        assert d["n_independent"] == 1
        assert d["degenerate"] is True

    def test_assign_panel_accepts_what_independence_rejects(self):
        """The exact gap this diagnostic exists to close."""
        panel = J.assign_panel("ol/gemma4:cloud", panel=C.OL_JUDGE_PANEL)
        assert len(panel) == 2                      # assign_panel is satisfied
        assert len({C.vendor(j) for j in panel}) == 1   # yet one family only

    def test_distinct_vendor_panel_is_not_degenerate(self):
        rep = J.panel_independence(["ol/minimax-m3:cloud"], C.OL_JUDGE_PANEL)
        assert rep["ol/minimax-m3:cloud"]["degenerate"] is False
        assert rep["ol/minimax-m3:cloud"]["n_independent"] == 2

    def test_kr_roster_keeps_two_independent_families_throughout(self):
        rep = J.panel_independence(C.GENERATORS, C.JUDGE_PANEL)
        assert min(d["n_independent"] for d in rep.values()) >= 2
        assert not any(d["degenerate"] for d in rep.values())

    def test_ol_roster_degrades_and_the_test_records_it(self):
        """Pinned as a KNOWN property of the fallback roster, not an accident.

        If this ever starts passing with min >= 2, the roster gained a family
        and the judge-tier caveat in the spec can be revisited.
        """
        rep = J.panel_independence(C.OL_GENERATORS, C.OL_JUDGE_PANEL)
        assert min(d["n_independent"] for d in rep.values()) == 1
        assert sum(d["degenerate"] for d in rep.values()) == 2

    def test_summary_names_the_degenerate_generators(self):
        s = J.panel_independence_summary(C.OL_GENERATORS, C.OL_JUDGE_PANEL)
        assert "DEGENERATE" in s
        assert "minimum independent judge families across generators: 1" in s


class TestJudgeDeferralGuard:
    """Tier 2 is DEFERRED by user decision (spec 4.11). The guard makes that
    decision operative rather than merely documented."""

    def test_ol_roster_is_refused(self):
        import pytest
        import run as R
        with pytest.raises(SystemExit) as e:
            R._guard_judge_independence("ol", False)
        msg = str(e.value)
        assert "refusing to judge" in msg
        assert "1 vendor family" in msg
        assert "4.11" in msg

    def test_kr_roster_is_allowed(self):
        import run as R
        R._guard_judge_independence("kr", False)   # must not raise

    def test_override_permits_ol_deliberately(self, capsys):
        import run as R
        R._guard_judge_independence("ol", True)    # must not raise
        out = capsys.readouterr().out
        assert "OVERRIDE" in out
        assert "unreliable" in out

    def test_the_guard_names_the_degenerate_generators(self):
        import pytest
        import run as R
        with pytest.raises(SystemExit) as e:
            R._guard_judge_independence("ol", False)
        assert "gemma4:cloud" in str(e.value)

    def test_cli_exposes_the_override_flag(self):
        """A deliberate override must be possible, and must be spelled out."""
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "run.py").read_text()
        assert "--i-have-read-spec-4-11" in src
