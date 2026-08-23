"""Tests for Tier-1 explicit-naming detection.

The substring cases are the point of this file. Tier 1 outranks the panel
wherever it fires, so a false positive silently corrupts every downstream
statistic, and a false negative sends a giveaway response into the solvability
run as if it were a hint.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import checker as K  # noqa: E402

ITEM_DEF = {
    "id": "A-L08-01", "type": "short", "answer": "def",
    "answer_forms": ["def", "def keyword"],
}
ITEM_MCQ = {
    "id": "A-L01-01", "type": "mcq",
    "options": ["Translator", "Compiler", "Interpreter"],
    "answer_index": 1, "answer": "Compiler",
    "answer_forms": ["compiler", "compilers", "option 2", "answer 2"],
}
ITEM_OPERATOR = {
    "id": "A-L03-02", "type": "short", "answer": "**",
    "answer_forms": ["**", "double asterisk"],
}
ITEM_LONG = {
    "id": "A-L08-02", "type": "mcq",
    "options": ["Lists cannot be defaults",
                "The default list is created once and shared across calls",
                "It raises a SyntaxError"],
    "answer_index": 1, "answer": "The default list is created once and shared across calls",
    "answer_forms": ["shared", "created once"],
}


class TestSubstringTrap:
    """The failure that motivated word-boundary matching."""

    def test_definition_does_not_match_def(self):
        # Verbatim from a real generation (kr/deepseek-3.2, pilot).
        resp = "Think about what word introduces a function definition when you write your code."
        assert "def" in resp                       # naive substring fires
        assert not K.check_explicit(resp, ITEM_DEF).explicit   # word boundary does not

    def test_bare_keyword_does_match(self):
        r = K.check_explicit("You need the def keyword here.", ITEM_DEF)
        assert r.explicit and r.reason == "answer_form"

    @pytest.mark.parametrize("resp", [
        "Consider how functions are defined.",
        "The default value matters.",
        "This is a definitive answer to a different question.",
    ])
    def test_def_substrings_never_fire(self, resp):
        assert not K.check_explicit(resp, ITEM_DEF).explicit

    def test_compiled_does_not_match_compiler(self):
        resp = "Think about whether the code is compiled ahead of time."
        assert not K.check_explicit(resp, ITEM_MCQ).explicit

    def test_compiler_named_does_fire(self):
        assert K.check_explicit("It is the compiler.", ITEM_MCQ).explicit


class TestPunctuationTokens:
    """`\\b` is useless for operator answers; they need their own boundaries."""

    def test_double_asterisk_matches(self):
        assert K.check_explicit("Use the ** operator.", ITEM_OPERATOR).explicit

    def test_single_asterisk_does_not_match_double(self):
        assert not K.check_explicit("Use the * operator.", ITEM_OPERATOR).explicit

    def test_operator_at_sentence_end(self):
        assert K.check_explicit("The operator you want is **.", ITEM_OPERATOR).explicit


class TestAssertiveIndex:
    def test_answer_is_two_fires(self):
        assert K.check_explicit("The answer is 2.", ITEM_MCQ).explicit

    def test_option_two_is_correct_fires(self):
        assert K.check_explicit("Option 2 is correct.", ITEM_MCQ).explicit

    def test_wrong_index_does_not_fire(self):
        """Naming a distractor is not revealing the key."""
        assert not K.check_explicit("The answer is 3.", ITEM_MCQ).explicit

    def test_neutral_option_mention_does_not_fire(self):
        r = K.check_explicit("Compare options 1 and 2 carefully.", ITEM_MCQ)
        assert not r.explicit

    def test_elimination_phrasing_does_not_fire(self):
        assert not K.check_explicit(
            "You can rule out 1 straight away.", ITEM_MCQ).explicit


class TestOptionVerbatim:
    def test_long_option_text_fires(self):
        assert K.check_explicit(
            "Remember: the default list is created once and shared across calls.",
            ITEM_LONG).explicit

    def test_short_option_text_not_used_as_evidence(self):
        """Short option strings appear in ordinary prose; answer_forms covers
        them instead."""
        item = {"options": ["int", "str", "float"], "answer_index": 0,
                "answer_forms": ["int"]}
        r = K.check_explicit("Think about integer types in general.", item)
        assert not r.explicit


class TestNormalisation:
    def test_unicode_and_whitespace_folded(self):
        assert K.check_explicit("It is the  COMPILER .", ITEM_MCQ).explicit

    def test_empty_response(self):
        r = K.check_explicit("", ITEM_MCQ)
        assert not r.explicit and r.reason == "empty"


class TestSemantics:
    def test_not_explicit_is_not_a_claim_of_no_leakage(self):
        """The definitional case: no key token, yet the answer is handed over.
        Tier 1 must return False here and let the semantic measure decide."""
        resp = ("It reads the entire source code and converts it to machine "
                "code before execution.")
        r = K.check_explicit(resp, ITEM_MCQ)
        assert not r.explicit
        assert not r.decided     # Tier 1 declines; it has NOT ruled out leakage

    def test_verify_is_the_inverse_for_the_guardrail(self):
        assert K.verify("Think about it carefully.", ITEM_MCQ) is True
        assert K.verify("The answer is 2.", ITEM_MCQ) is False

    def test_guardrail_criterion_is_lexical_not_semantic(self):
        """RQ3 must not be tautological: the guardrail passes a response that
        the semantic outcome would score as full leakage."""
        definitional = ("It reads the entire source code and converts it to "
                        "machine code before execution.")
        assert K.verify(definitional, ITEM_MCQ) is True


class TestIndexTextCollisions:
    """Options that are themselves small integers can be misread as positions."""

    def test_flags_the_misscoring_case(self):
        it = {"id": "X", "options": ["3", "4", "5"], "answer_index": 0}
        hits = K.index_text_collisions([it])
        assert len(hits) == 1
        assert hits[0]["key_text"] == "3"
        assert hits[0]["key_index"] == 1
        assert hits[0]["would_be_scored_as_index"] == 3

    def test_ignores_the_harmless_coincidence(self):
        """Key text equal to its own position cannot misscore."""
        it = {"id": "X", "options": ["a", "2", "c"], "answer_index": 1}
        assert K.index_text_collisions([it]) == []

    def test_ignores_out_of_range_integers(self):
        """An option text of 7 with 3 options parses to None, i.e. a decline."""
        it = {"id": "X", "options": ["7", "8", "9"], "answer_index": 0}
        assert K.index_text_collisions([it]) == []

    def test_ignores_non_numeric_options(self):
        it = {"id": "X", "options": ["list", "tuple", "dict"], "answer_index": 0}
        assert K.index_text_collisions([it]) == []

    def test_handles_malformed_items_without_raising(self):
        assert K.index_text_collisions([{"id": "X"}]) == []
        assert K.index_text_collisions([{"id": "X", "options": [], "answer_index": 0}]) == []
        assert K.index_text_collisions([{"id": "X", "options": ["1"], "answer_index": 5}]) == []

    def test_real_banks_exposure_is_pinned(self):
        """Pin the measured exposure so a bank edit that worsens it is visible."""
        import json
        from pathlib import Path
        banks = Path(__file__).resolve().parent.parent / "banks"
        for name, cap in (("bank_a.json", 2), ("bank_b_sample.json", 2)):
            p = banks / name
            if not p.exists():
                continue
            items = [i for i in json.loads(p.read_text())["items"]
                     if i.get("type", "mcq") == "mcq"]
            assert len(K.index_text_collisions(items)) <= cap, name


class TestAssertiveValue:
    """A short key is catchable in an assertive frame and nowhere else.

    Added 2026-08-18 (spec 4.38). Before it, `check_explicit` had two handles on
    a key's text -- an `answer_forms` entry matched bare, and `option_verbatim`
    needing 12+ characters -- and a measured audit put Tier-1 sensitivity at
    28.9% on the Bank B sample, with 17 of 27 retained items catching nothing at
    all, "The answer is 0." included. Adding the bare key as a form fixes that
    and imports false positives, because `0`, `2`, `string` and `Error` are
    ordinary Python-tutoring English.
    """

    ITEM = {"id": "V-1", "type": "mcq", "stem": "s",
            "options": ["alpha", "0", "beta"], "answer_index": 1}

    def test_framed_assertion_of_a_short_key_is_caught(self):
        for t in ("The answer is 0.", "Go with 0.", "You should pick 0 here.",
                  "It's 0 -- that's the one."):
            assert K.check_explicit(t, self.ITEM).explicit, t

    def test_bare_mention_of_the_same_token_is_not(self):
        """The whole point: this is what a form of `0` would have flagged."""
        for t in ("Remember that indexing starts at 0 in Python.",
                  "The list is empty, so it has 0 elements.",
                  "Count from 0 upward and see where you land."):
            assert not K.check_explicit(t, self.ITEM).explicit, t

    def test_negation_is_not_assertion(self):
        """"That is not right ... 0" fired before the lookbehind guard: a denial
        reads as a claim to a matcher that only looks forward. Found by
        adversarial probing, not by the template set."""
        for t in ("That is not right -- start again from 0 and re-count.",
                  "It isn't 0, so try the next one.",
                  "I would never say 0 without checking."):
            assert not K.check_explicit(t, self.ITEM).explicit, t

    def test_the_reason_names_the_detector(self):
        assert K.check_explicit("The answer is 0.", self.ITEM).reason == "assertive_value"

    def test_more_specific_detectors_keep_their_reason(self):
        """It runs last, so an exact form or verbatim match keeps its own label
        in the saved record rather than being relabelled by the broadest rule."""
        it = dict(self.ITEM, answer_forms=["zero"])
        assert K.check_explicit("The answer is zero.", it).reason == "answer_form"

    def test_it_does_not_reach_across_a_sentence_boundary(self):
        """The frame window stops at `.` and newline: two unrelated sentences
        must not combine into an assertion."""
        assert not K.check_explicit(
            "Which one is correct? Start counting at 0.", self.ITEM).explicit


class TestVerifierAuditIsWiredToTheRealBanks:
    """The audit is the acceptance test for `answer_forms`; pin its result.

    Both banks were wrong in OPPOSITE directions and neither was visible until
    measured: Bank B at 28.9% sensitivity, Bank A at 4.8% false positives from
    forms like `is` and `y` that no one had ever run against text.
    """

    def _audit(self, bank):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import verifier_audit as V
        return V.audit(bank, "ol", "stress", retained_only=True)

    def test_every_retained_item_catches_a_blatant_reveal(self):
        for bank in ("A", "bs"):
            res = self._audit(bank)
            blind = [k for k, v in res["per_item"].items() if v["caught"] == 0]
            assert not blind, f"{bank}: blind items {blind}"

    def test_false_positives_stay_under_one_percent(self):
        """Not zero. `A-L03-04`'s key is the English word `is`, which no lexical
        rule can separate from the copula; it is a known, disclosed limit rather
        than a target to drive to zero by weakening detection elsewhere."""
        for bank in ("A", "bs"):
            res = self._audit(bank)
            assert res["fp_rate"] < 0.01, (bank, res["fp_rate"])

    def test_a_bare_numeral_is_never_accepted_as_a_form(self):
        """A priori, not measured: which numbers a corpus happens to contain is
        an accident of who wrote it, so no corpus can clear a numeric form."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import verifier_audit as V
        it = {"id": "N-1", "type": "mcq", "options": ["a", "10"], "answer_index": 1,
              "stem": "s"}
        assert not V.form_is_safe(it, "10")
        assert not V.form_is_safe(it, "4.5")
