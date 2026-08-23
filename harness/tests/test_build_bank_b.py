"""Tests for the Bank B extractor.

Bank B is the study's only external check on whether findings are a property of
author-written items. A silently mis-parsed key would not fail loudly -- it
would just make the anchor bank measure the wrong thing, which is worse than
having no anchor at all.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_bank_b as BB  # noqa: E402


def _write(tmp_path, body: str) -> Path:
    p = tmp_path / "Chapter.rst"
    p.write_text(body, encoding="utf-8")
    return p


SIMPLE = """\
Some heading
============

.. mchoice:: question1_1
   :answer_a: alpha
   :answer_b: beta
   :answer_c: gamma
   :correct: b
   :feedback_a: no
   :feedback_b: yes
   :practice: T

   Which letter is second?
"""


class TestParsing:
    def test_extracts_stem_options_and_key(self, tmp_path):
        items = BB.parse_file(_write(tmp_path, SIMPLE), "_sources/Functions/Chapter.rst")
        assert len(items) == 1
        it = items[0]
        assert it["id"] == "B-question1_1"
        assert it["options"] == ["alpha", "beta", "gamma"]
        assert it["answer_index"] == 1
        assert it["answer"] == "beta"
        assert it["stem"] == "Which letter is second?"
        assert it["rejected_reason"] is None

    def test_topic_comes_from_the_chapter_directory(self, tmp_path):
        items = BB.parse_file(_write(tmp_path, SIMPLE), "_sources/Functions/Chapter.rst")
        assert items[0]["topic"] == "functions"

    def test_html_entities_are_decoded(self, tmp_path):
        body = SIMPLE.replace(":answer_a: alpha", ":answer_a: &quot;False&quot;")
        items = BB.parse_file(_write(tmp_path, body), "_sources/Functions/C.rst")
        assert items[0]["options"][0] == '"False"'

    def test_feedback_and_practice_never_leak_into_the_stem(self, tmp_path):
        items = BB.parse_file(_write(tmp_path, SIMPLE), "_sources/Functions/C.rst")
        stem = items[0]["stem"]
        assert "feedback" not in stem and "practice" not in stem
        assert "yes" not in stem


class TestBlockBounding:
    """Regression: the directive body ends at the first unindented line.

    Bounding by the NEXT mchoice instead swallows everything between two
    questions. Two items originally survived selection with narrative prose and
    activecode scaffolding standing in for the question text.
    """

    def test_following_prose_does_not_enter_the_stem(self, tmp_path):
        body = SIMPLE + """
This is narrative prose that follows the question and belongs to the chapter,
not to the item. It mentions ``set_alarm`` and other things.

.. mchoice:: question1_2
   :answer_a: one
   :answer_b: two
   :answer_c: three
   :correct: a

   Pick the first.
"""
        items = BB.parse_file(_write(tmp_path, body), "_sources/Functions/C.rst")
        assert len(items) == 2
        assert items[0]["stem"] == "Which letter is second?"
        assert "narrative prose" not in items[0]["stem"]
        assert items[1]["stem"] == "Pick the first."

    def test_activecode_scaffolding_does_not_enter_the_stem(self, tmp_path):
        body = SIMPLE + """
.. activecode:: ac_1
   :nocodelens:

   places = {"Australia": 2000}
   ====
   from unittest.gui import TestCaseGui
"""
        items = BB.parse_file(_write(tmp_path, body), "_sources/Functions/C.rst")
        assert "TestCaseGui" not in items[0]["stem"]
        assert "====" not in items[0]["stem"]

    def test_last_directive_in_file_is_still_bounded(self, tmp_path):
        items = BB.parse_file(_write(tmp_path, SIMPLE), "_sources/Functions/C.rst")
        assert items[0]["stem"] == "Which letter is second?"


class TestExclusions:
    def test_multiple_answers_flag_rejected(self, tmp_path):
        body = SIMPLE.replace("   :answer_a:", "   :multiple_answers:\n   :answer_a:")
        items = BB.parse_file(_write(tmp_path, body), "_sources/Functions/C.rst")
        assert items[0]["rejected_reason"] == "multiple_answers"

    def test_comma_separated_key_rejected(self, tmp_path):
        """The measure asks which single option is keyed; `a,b` has no answer."""
        body = SIMPLE.replace(":correct: b", ":correct: a,b")
        items = BB.parse_file(_write(tmp_path, body), "_sources/Functions/C.rst")
        assert items[0]["rejected_reason"] == "multiple_answers"

    def test_two_option_item_rejected(self, tmp_path):
        body = SIMPLE.replace("   :answer_c: gamma\n", "")
        items = BB.parse_file(_write(tmp_path, body), "_sources/Functions/C.rst")
        assert items[0]["rejected_reason"] == "too_few_options"

    def test_item_referring_to_a_figure_rejected(self, tmp_path):
        body = SIMPLE.replace("Which letter is second?",
                              "Which letter is shown above in the figure?")
        items = BB.parse_file(_write(tmp_path, body), "_sources/Functions/C.rst")
        assert items[0]["rejected_reason"] == "not_self_contained"

    def test_key_not_among_options_rejected(self, tmp_path):
        body = SIMPLE.replace(":correct: b", ":correct: z")
        items = BB.parse_file(_write(tmp_path, body), "_sources/Functions/C.rst")
        assert items[0]["rejected_reason"] == "no_single_key"


class TestCodeNormalisation:
    def test_rst_code_block_becomes_a_fence(self, tmp_path):
        body = SIMPLE.replace("   Which letter is second?", """\
   What does this print?

   .. code-block:: python

      x = 1
      print(x)
""")
        items = BB.parse_file(_write(tmp_path, body), "_sources/Functions/C.rst")
        stem = items[0]["stem"]
        assert ".. code-block" not in stem
        assert stem.count("```") % 2 == 0
        assert "print(x)" in stem, "the code IS the question; it must survive"


@pytest.mark.skipif(not (Path(__file__).parent.parent / "banks" / "bank_b.json").exists(),
                    reason="bank B not built in this checkout")
class TestBuiltBank:
    """Invariants over the real built bank."""

    @staticmethod
    def _bank():
        import json
        return json.loads((Path(__file__).parent.parent / "banks" / "bank_b.json").read_text())

    def test_every_item_has_a_topic(self):
        """An empty topic silently disables topic control for that item alone,
        applying a different instrument to part of the bank."""
        assert [i["id"] for i in self._bank()["items"] if not i["topic"]] == []

    def test_key_index_always_resolves_to_the_answer_text(self):
        for i in self._bank()["items"]:
            assert 0 <= i["answer_index"] < len(i["options"])
            assert i["options"][i["answer_index"]] == i["answer"]

    def test_no_rst_or_activecode_residue_in_stems(self):
        for i in self._bank()["items"]:
            assert ".. " not in i["stem"], i["id"]
            assert "TestCaseGui" not in i["stem"], i["id"]

    def test_all_items_have_at_least_three_options(self):
        assert all(len(i["options"]) >= 3 for i in self._bank()["items"])

    def test_item_text_is_not_in_the_deposited_locator_file(self):
        """The whole licence position rests on this: the deposited file must
        carry no GFDL item text, only pointers and hashes."""
        import json
        p = Path(__file__).parent.parent / "banks" / "bank_b_locators.json"
        loc = json.loads(p.read_text())
        for entry in loc["items"]:
            assert "stem" not in entry and "options" not in entry
            assert "answer" not in entry
        raw = p.read_text()
        for i in self._bank()["items"][:25]:
            assert i["stem"][:40] not in raw
