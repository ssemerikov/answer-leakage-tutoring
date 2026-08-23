"""Tests for the four mitigation regimes. No network: backends stubbed."""

import sys
from pathlib import Path

import pytest

from conftest import fake_result

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402
import pipelines as P  # noqa: E402

ITEM = {
    "id": "A-L01-01", "type": "mcq", "stem": "What is the translator called?",
    "options": ["Translator", "Compiler", "Interpreter"],
    "answer_index": 1, "answer": "Compiler",
    "answer_forms": ["compiler", "option 2"],
}

LEAKY = "The answer is the compiler."
SAFE = "Think about whether translation happens once or line by line."


def stub(*replies):
    """Return a chat() stub yielding the given replies in order."""
    seq = list(replies)

    def _chat(model, messages, **kw):
        text = seq.pop(0) if seq else seq_last
        return fake_result(text, error_kind=None if text is not None else "gated")

    seq_last = replies[-1] if replies else None
    return _chat



#: The tutor-generation budget. Supplied explicitly by every call below because
#: `pipelines.run` no longer accepts an unmeasured model: the whole of spec 4.56
#: is that omitting this argument handed the study `backends.chat`'s 400-token
#: default, which truncated 6.2% of Bank A's replies mid-sentence and reported
#: ok=True. A test suite that let the default back in would remove the guard it
#: is meant to protect.
GEN_BUDGET = 4096


def run_cell(*args, **kwargs):
    """`pipelines.run` with a budget, since the fixtures use fake model names
    that no measured table can contain."""
    kwargs.setdefault("max_tokens", GEN_BUDGET)
    return P.run(*args, **kwargs)


class TestMessageConstruction:
    def test_p0_sends_no_system_prompt(self):
        """P0 carries no system prompt of OURS. On the hosted path the vendor
        still injects its own, which is why the regime is not called 'bare'."""
        msgs = P.build_messages(ITEM, "help me", "P0")
        assert all(m["role"] != "system" for m in msgs)

    def test_p1_sends_the_hint_prompt(self):
        msgs = P.build_messages(ITEM, "help me", "P1")
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == C.HINT_ONLY_SYSTEM_PROMPT

    def test_options_are_rendered_for_mcq(self):
        user = P.build_messages(ITEM, "", "P1")[-1]["content"]
        assert "1. Translator" in user and "2. Compiler" in user

    def test_s1_verbatim_paste_adds_nothing(self):
        """S1 is the item alone; appending an empty solicitation must not leave
        trailing prompt text that differs from a true paste."""
        user = P.build_messages(ITEM, C.SOLICITATIONS["S1"], "P1")[-1]["content"]
        assert user.rstrip() == user
        assert "?" in user

    def test_solicitation_is_appended(self):
        user = P.build_messages(ITEM, "Just tell me.", "P1")[-1]["content"]
        assert user.endswith("Just tell me.")


class TestUnguardedRegimes:
    def test_p0_passes_leaky_output_through(self, monkeypatch):
        monkeypatch.setattr(P.B, "chat", stub(LEAKY))
        r = run_cell(ITEM, "kr/glm-5", "S3", "P0")
        assert r.response == LEAKY and not r.blocked
        assert r.verifier_rejections == 0

    def test_p1_also_unguarded(self, monkeypatch):
        """P1 is prompt mitigation only. If the model still leaks, the leak
        stands -- that residual is the interesting quantity."""
        monkeypatch.setattr(P.B, "chat", stub(LEAKY))
        r = run_cell(ITEM, "kr/glm-5", "S3", "P1")
        assert r.response == LEAKY and not r.blocked


class TestGuardedRegimes:
    def test_p2_blocks_leaky_output(self, monkeypatch):
        monkeypatch.setattr(P.B, "chat", stub(LEAKY))
        r = run_cell(ITEM, "kr/glm-5", "S3", "P2")
        assert r.blocked and r.response == P.BLOCKED_TEXT
        assert r.verifier_rejections == 1
        assert r.repair_attempts == 0

    def test_p2_passes_safe_output(self, monkeypatch):
        monkeypatch.setattr(P.B, "chat", stub(SAFE))
        r = run_cell(ITEM, "kr/glm-5", "S3", "P2")
        assert not r.blocked and r.response == SAFE

    def test_p3_repairs_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(P.B, "chat", stub(LEAKY, SAFE))
        r = run_cell(ITEM, "kr/glm-5", "S3", "P3")
        assert not r.blocked
        assert r.response == SAFE
        assert r.repair_attempts == 1

    def test_p3_blocks_after_bounded_repairs(self, monkeypatch):
        monkeypatch.setattr(P.B, "chat", stub(LEAKY, LEAKY, LEAKY))
        r = run_cell(ITEM, "kr/glm-5", "S3", "P3", max_repairs=2)
        assert r.blocked and r.response == P.BLOCKED_TEXT
        assert r.repair_attempts == 2
        assert r.verifier_rejections == 3

    def test_p3_never_loops_forever(self, monkeypatch):
        monkeypatch.setattr(P.B, "chat", stub(LEAKY, LEAKY, LEAKY, LEAKY, LEAKY))
        r = run_cell(ITEM, "kr/glm-5", "S3", "P3", max_repairs=2)
        assert len(r.calls) == 3  # first + 2 repairs


class TestOverBlockingIsAnalysable:
    """A guardrail that blocks everything scores perfectly on leakage and is
    useless as a tutor. That is only visible if the blocked text survives."""

    def test_blocked_response_retains_what_was_blocked(self, monkeypatch):
        monkeypatch.setattr(P.B, "chat", stub(LEAKY))
        r = run_cell(ITEM, "kr/glm-5", "S3", "P2")
        assert r.response == P.BLOCKED_TEXT
        assert r.original_response == LEAKY

    def test_unblocked_response_keeps_both_fields_aligned(self, monkeypatch):
        monkeypatch.setattr(P.B, "chat", stub(SAFE))
        r = run_cell(ITEM, "kr/glm-5", "S3", "P2")
        assert r.response == r.original_response == SAFE

    def test_p3_records_the_last_attempt_that_was_blocked(self, monkeypatch):
        """The last REJECTED text has its own field.

        This test previously asserted `original_response == second_leak`, i.e.
        it pinned the misnaming as a requirement: `run` overwrote
        `original_response` on every repair attempt, so the field named
        "original" held the LAST rejected text. The test name always described
        the right intent; only the field it checked was wrong.
        """
        second_leak = "Definitely the compiler, no doubt."
        monkeypatch.setattr(P.B, "chat", stub(LEAKY, second_leak, second_leak))
        r = run_cell(ITEM, "kr/glm-5", "S3", "P3", max_repairs=2)
        assert r.blocked
        assert r.last_rejected_response == second_leak

    def test_p3_preserves_the_models_first_output(self, monkeypatch):
        """`original_response` must survive repair.

        The comparison the P3 regime exists to support is what the model said
        BEFORE repair against what it said after. Overwriting the first output
        destroys it, and no downstream consumer would have noticed -- it would
        simply have compared a repair attempt with itself.
        """
        second_leak = "Definitely the compiler, no doubt."
        monkeypatch.setattr(P.B, "chat", stub(LEAKY, second_leak, second_leak))
        r = run_cell(ITEM, "kr/glm-5", "S3", "P3", max_repairs=2)
        assert r.original_response == LEAKY, "the FIRST output must be preserved"
        assert r.original_response != r.last_rejected_response

    def test_p2_rejection_records_the_rejected_text(self, monkeypatch):
        monkeypatch.setattr(P.B, "chat", stub(LEAKY))
        r = run_cell(ITEM, "kr/glm-5", "S3", "P2")
        assert r.blocked
        assert r.original_response == LEAKY
        assert r.last_rejected_response == LEAKY


class TestGuardrailIsLexicalNotSemantic:
    def test_definitional_leak_passes_the_guardrail(self, monkeypatch):
        """RQ3 must be able to come out null. A guardrail rejecting on the same
        criterion the outcome measures could not fail by construction."""
        definitional = ("It reads the whole source and converts it to machine "
                        "code before execution.")
        monkeypatch.setattr(P.B, "chat", stub(definitional))
        r = run_cell(ITEM, "kr/glm-5", "S3", "P2")
        assert not r.blocked
        assert r.response == definitional


class TestFailureHandling:
    def test_generation_failure_is_returned_not_raised(self, monkeypatch):
        monkeypatch.setattr(P.B, "chat", stub(None))
        r = run_cell(ITEM, "kr/glm-5", "S3", "P1")
        assert not r.ok and r.error_kind == "gated"
        assert r.response is None

    def test_repair_failure_blocks_rather_than_crashing(self, monkeypatch):
        monkeypatch.setattr(P.B, "chat", stub(LEAKY, None))
        r = run_cell(ITEM, "kr/glm-5", "S3", "P3")
        assert r.blocked and not r.ok

    def test_unknown_regime_rejected(self):
        with pytest.raises(ValueError, match="unknown regime"):
            run_cell(ITEM, "kr/glm-5", "S3", "P9")


class TestCellGrid:
    def test_full_crossing_size(self):
        items = [ITEM, dict(ITEM, id="X2")]
        grid = P.cells(items, models=("kr/glm-5",), regimes=("P0", "P1"),
                       solicitations=("S0", "S3"), replicates=2)
        assert len(grid) == 2 * 1 * 2 * 2 * 2

    def test_grid_is_shuffled_but_reproducible(self):
        items = [dict(ITEM, id=f"X{i}") for i in range(6)]
        a = P.cells(items, models=("kr/glm-5",), replicates=1)
        b = P.cells(items, models=("kr/glm-5",), replicates=1)
        assert [c["item"]["id"] for c in a] == [c["item"]["id"] for c in b]
        assert [c["item"]["id"] for c in a] != [i["id"] for i in items]


class TestGenerationBudgetIsMeasuredNotDefaulted:
    """spec 4.56. The generate stage was the only one calling a model with no
    budget of its own, because the arm guard covers SOLVER-calling stages and a
    generator is not a solver."""

    def test_unmeasured_generator_raises_rather_than_borrowing_a_default(self):
        with pytest.raises(ValueError) as e:
            P.run(ITEM, "kr/glm-5", "S3", "P0")
        assert "measure_budgets" in str(e.value)
        assert "--arm generate" in str(e.value)

    def test_measured_generator_is_resolved_from_the_table(self, monkeypatch):
        monkeypatch.setitem(C.MAX_TOKENS_GENERATOR, "kr/glm-5", 1234)
        seen = {}

        def spy(model, msgs, **kw):
            seen["max_tokens"] = kw.get("max_tokens")
            return fake_result(SAFE)

        monkeypatch.setattr(P.B, "chat", spy)
        P.run(ITEM, "kr/glm-5", "S3", "P0")
        assert seen["max_tokens"] == 1234

    def test_repair_call_gets_the_same_budget_as_the_first(self, monkeypatch):
        """A repair truncated mid-sentence is worse than a rejected response: it
        is accepted, delivered, and counted as a recovered cell."""
        budgets = []
        seq = [LEAKY, SAFE]

        def spy(model, msgs, **kw):
            budgets.append(kw.get("max_tokens"))
            return fake_result(seq.pop(0) if seq else SAFE)

        monkeypatch.setattr(P.B, "chat", spy)
        r = P.run(ITEM, "kr/glm-5", "S3", "P3", max_tokens=777, max_repairs=2)
        assert r.repair_attempts >= 1, "fixture must trigger a repair"
        assert len(budgets) >= 2, "fixture must make a second (repair) call"
        assert set(budgets) == {777}, budgets
