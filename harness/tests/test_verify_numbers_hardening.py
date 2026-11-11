"""P0.4 hardening of `verify_numbers`: the blind spots a green run could hide.

R1  presence is tested on COMMENT-STRIPPED text -- a number that survives only
    in a `%%` comment used to "verify";
R2  a check is retired explicitly, by label, with a reason -- and a retired
    label that matches nothing is an error, not a silent no-op;
R3  the abstract is linted as the plain text Editorial Manager will receive,
    and every number in it must be pinned by a current check;
R4  the highlights file is checked for count, length and pinned numbers;
R5  the main text is counted off the BUILT PDF, with the same freshness rule
    as the abstract.

These tests drive `main()` on synthetic manuscripts. None of them asserts that
the REAL manuscript passes: it does not, by design, until the C&E rewrite.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

import texutil
import verify_numbers as V


def _run(tmp_path, monkeypatch, capsys, tex, checks=(), retired=None,
         highlights=None):
    """Run main() on a synthetic manuscript with no build beside it."""
    paper = tmp_path / "paper.tex"
    paper.write_text(tex, encoding="utf-8")
    monkeypatch.setattr(V, "PAPER", paper)
    monkeypatch.setattr(V, "PDF", tmp_path / "absent.pdf")
    monkeypatch.setattr(V, "checks", lambda: list(checks))
    monkeypatch.setattr(V, "RETIRED", dict(retired or {}))
    if highlights is not None:
        (tmp_path / "highlights.txt").write_text(highlights, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["verify_numbers.py"])
    rc = V.main()
    return rc, capsys.readouterr().out


# --------------------------------------------------------------- R1 ------
class TestPresenceIsTestedOnWhatTheReaderSees:

    def test_a_literal_only_in_a_full_line_comment_is_missing(
            self, tmp_path, monkeypatch, capsys):
        rc, out = _run(tmp_path, monkeypatch, capsys,
                       "Leakage is high.\n%% it was 0.605\n",
                       checks=[("mean leak", "0.605", "n")])
        assert "MISSING" in out and "0.605" in out
        assert rc == 1

    def test_a_literal_only_in_a_trailing_comment_is_missing(
            self, tmp_path, monkeypatch, capsys):
        rc, out = _run(tmp_path, monkeypatch, capsys,
                       "Leakage is high. % 0.605\n",
                       checks=[("mean leak", "0.605", "n")])
        assert "MISSING" in out
        assert rc == 1

    def test_an_escaped_percent_literal_still_verifies(
            self, tmp_path, monkeypatch, capsys):
        rc, out = _run(tmp_path, monkeypatch, capsys,
                       "and $98\\%$ of replies % trailing\n",
                       checks=[("share", "$98\\%$", "n")])
        assert "MISSING" not in out
        assert "1 verified" in out

    def test_a_superseded_literal_only_in_a_comment_is_not_stale(
            self, tmp_path, monkeypatch, capsys):
        rc, out = _run(tmp_path, monkeypatch, capsys,
                       "accuracy is fine\n% was 0.097 before the re-screen\n")
        assert "STALE" not in out
        assert rc == 0

    def test_abstract_words_uses_the_shared_stripper(self, monkeypatch):
        seen = []
        real = texutil.strip_comments

        def spy(text):
            seen.append(text)
            return real(text)

        monkeypatch.setattr(texutil, "strip_comments", spy)
        V.abstract_words("\\begin{abstract}\none two three four five six "
                         "seven\n\\end{abstract}", pdf=Path("/nonexistent.pdf"))
        assert seen, "abstract_words kept a private comment stripper"


# --------------------------------------------------------------- R2 ------
class TestRetirementIsExplicit:

    def test_a_retired_check_is_reported_and_neither_verified_nor_missing(
            self, tmp_path, monkeypatch, capsys):
        rc, out = _run(tmp_path, monkeypatch, capsys, "no numbers here\n",
                       checks=[("old quantity", "0.611", "n")],
                       retired={"old quantity": "sentence cut in the C&E rewrite"})
        assert "RETIRED  old quantity  (sentence cut in the C&E rewrite)" in out
        assert "MISSING" not in out
        assert "0 verified, 0 missing" in out
        assert ", 1 retired" in out
        assert rc == 0

    def test_a_retired_label_matching_no_check_is_an_error(
            self, tmp_path, monkeypatch, capsys):
        rc, out = _run(tmp_path, monkeypatch, capsys, "x 0.605\n",
                       checks=[("mean leak", "0.605", "n")],
                       retired={"mean laek": "typo"})
        assert "ERROR" in out and "mean laek" in out
        assert rc == 1, "a typo must not silently retire nothing"

    def test_the_summary_always_reports_the_retired_count(
            self, tmp_path, monkeypatch, capsys):
        rc, out = _run(tmp_path, monkeypatch, capsys, "x 0.605\n",
                       checks=[("mean leak", "0.605", "n")])
        assert ", 0 retired" in out

    def test_a_retired_check_no_longer_vouches_for_a_superseded_literal(
            self, tmp_path, monkeypatch, capsys):
        """A retired check pins nothing: its literal is not 'current'."""
        rc, out = _run(tmp_path, monkeypatch, capsys, "it read 0.097 here\n",
                       checks=[("gemma4 acc", "0.097", "n")],
                       retired={"gemma4 acc": "superseded"})
        assert "STALE" in out
        assert rc == 1

    # M7 (fix round 1): RETIRED is keyed by label, so a label must name ONE check.
    def test_a_duplicate_check_label_is_an_error(self, tmp_path, monkeypatch, capsys):
        rc, out = _run(tmp_path, monkeypatch, capsys, "x 0.605 0.611\n",
                       checks=[("mean leak", "0.605", "n"), ("mean leak", "0.611", "n")])
        assert "ERROR    duplicate check label" in out and "mean leak" in out
        assert rc == 1


# --------------------------------------------------------------- R3 ------
class TestNumberTokens:

    @pytest.mark.parametrize("text,want", [
        ("Across 2,016 responses", ["2,016"]),
        ("mean 0.605 and 98% of them", ["0.605", "98"]),
        ("from -0.144 to +0.500", ["0.144", "0.500"]),
        ("RQ1, P0, S3 and P1-P0", []),
        ("written in Python 3 for", []),
        ("ICC(2,1) 0.620, 0.245", ["0.620", "0.245"]),
        ("a SHA-256 of each item, GPT-4 and Tier-1", []),
        ("see Section 4.5 and Table 2", []),
        ("27 items, 3,888 responses.", ["27", "3,888"]),
    ])
    def test_only_standalone_quantities_are_numbers(self, text, want):
        assert V.number_tokens(text) == want

    # F1 (fix round 1): QUANTITIES THE FIRST TOKENISER DROPPED SILENTLY.
    # APA style -- Computers & Education's -- drops the leading zero of a
    # kappa, a p or a correlation, so `.556` is the likely form after the
    # rewrite, and it used to yield no token at all.
    @pytest.mark.parametrize("text,want", [
        ("kappa = .56, p < .001", ["0.56", "0.001"]),
        ("p < .05.", ["0.05"]),
        ("a 16k-word manuscript", ["16k"]),
        ("5x more leakage", ["5x"]),
        ("95% of replies", ["95"]),
        ("p = 1e-20 and 2.5E+3", ["1e-20", "2.5E+3"]),
        ("the 2nd and 3D cases", ["2nd", "3D"]),
        ("agreement kappa:.56 overall", ["0.56"]),   # a word:value is not a model tag
    ])
    def test_quantities_in_every_form_are_numbers(self, text, want):
        assert V.number_tokens(text) == want

    # ...and the binding ruling: a digit run attached to a letter or to a
    # hyphenated word is part of a NAME, including the model names a
    # plain-text abstract is likely to carry.
    @pytest.mark.parametrize("text", [
        "contrasts P1, S0 and R4", "GPT-4o and GPT-4o-mini", "Llama-3.1",
        "Qwen3", "gemma4:31b, gpt-oss:120b and minimax-m3", "gemma4",
        "Llama 3.1 70B", "Claude 3.5 Sonnet", "Gemini 1.5 Pro", "Mistral 7B",
        "study version 2.3.0", "ICC(2,1)", "Python 3.10",
    ])
    def test_identifiers_and_model_names_are_not_numbers(self, text):
        assert V.number_tokens(text) == [], text

    # N-b (fix round 2): the model masks hid real quantities. A tag is masked
    # only in the forms Ollama uses (27b, 1.5m, latest, letters-and-hyphens),
    # and a model family name takes a SPACED version only when it is a
    # decimal or carries a size letter (Llama 3.1, Mistral 7B) -- never a bare
    # count. Tokens are unsigned on BOTH sides by design (see number_tokens),
    # so "-0.05" yields the token 0.05.
    @pytest.mark.parametrize("text,want", [
        ("S3:0.6", ["0.6"]),
        ("S3:0.6 and S0:0.4", ["0.6", "0.4"]),
        ("delta P2:-0.05", ["0.05"]),
        ("Gemma 27 items", ["27"]),
        ("GPT 4 replies", ["4"]),
        ("item A-L03:12", ["12"]),
    ])
    def test_a_bare_number_after_a_tag_or_family_name_is_a_quantity(self, text, want):
        assert V.number_tokens(V.normalise_numeric(text)) == want

    @pytest.mark.parametrize("text", [
        "GPT-4o-mini", "GPT-4o", "Llama-3.1", "Llama 3.1 70B", "gemma4:31b",
        "gpt-oss:120b", "Qwen3", "Qwen2.5-72B", "Claude 3.5 Sonnet",
        "Gemini 2.5 Flash", "GPT-4.1", "o3", "DeepSeek-R1", "gpt-oss-120b",
        "Phi-3.5-mini", "gemma3:27b-it-qat", "llama3:latest", "minimax-m3:cloud",
        "Mistral 7B", "Gemini 1.5 Pro", "qwen2.5:1.5b",
    ])
    def test_every_model_name_still_yields_no_number(self, text):
        assert V.number_tokens(V.normalise_numeric(text)) == [], text

    # P0.6: found by the uncovered-numbers baseline -- item ids such as
    # A-L09-01 yielded the "number" 01, because the hyphen before it follows a
    # DIGIT. A digit run inside a letter-led hyphenated compound is part of an
    # identifier (the F1 ruling); a digit-led range stays a pair of numbers.
    @pytest.mark.parametrize("text,want", [
        ("on \\texttt{A-L09-01} the baseline", []),
        ("A-L07-02 and A-L08-04", []),
        ("item A-L03:12", ["12"]),
        # fix round 1 (M5): a trailing DECIMAL segment is a value, not a version
        ("Foo-3.5 and v2-3", ["3.5"]),
        ("a 3-4 point gap", ["3", "4"]),
        ("from 0.033--0.450", ["0.033", "0.450"]),
        ("P2:-0.05", ["0.05"]),
    ])
    def test_a_letter_led_hyphenated_compound_is_an_identifier(self, text, want):
        assert V.number_tokens(V.normalise_numeric(text)) == want

    # P0.6 fix round 1, M5: the hyphen-identifier mask must not swallow a
    # trailing decimal value -- "S3-S2-0.024" is a contrast and its value.
    # Integers after a hyphen stay identifiers (F1 ruling: GPT-4, top-10).
    @pytest.mark.parametrize("text,want", [
        ("P3-P1 is small", []), ("S3-S2 is small", []), ("Bank-B items", []),
        ("the S3-S2 contrast is -0.024 here", ["0.024"]),
        ("S3-S2=-0.024", ["0.024"]), ("S3-S2: -0.024", ["0.024"]),
        ("P3--0.05 shift", ["0.05"]),
        ("S3-S2-0.024", ["0.024"]), ("Bank~B$-$0.05", ["0.05"]),
        ("$p-0.05$", ["0.05"]), ("$\\Delta$-0.05", ["0.05"]),
        ("A-0.5-B", []), ("Llama-3.1-70B", []), ("SHA-256", []), ("GPT-4", []),
        ("top-10 replies", []), ("level-2 items", []),
    ])
    def test_a_trailing_decimal_after_a_hyphenated_name_is_a_value(self, text, want):
        assert V.number_tokens(V.normalise_numeric(text)) == want

    # P0.6 fix round 1, M6: a digit glued to a control word is still a number.
    @pytest.mark.parametrize("text,want", [
        ("$\\pm0.09$", ["0.09"]), ("$\\times3$", ["3"]),
        ("$\\approx0.85$", ["0.85"]), ("$\\leq0.05$", ["0.05"]),
        ("$\\pm 0.09$", ["0.09"]), ("$10^{-20}$", ["10", "20"]),
    ])
    def test_a_number_glued_to_a_control_word_is_a_token(self, text, want):
        assert V.number_tokens(V.normalise_numeric(text)) == want

    def test_a_leading_dot_decimal_is_pinned_by_its_zero_form(self):
        tex = "\\begin{abstract}\nAgreement was kappa .556 here.\n\\end{abstract}\n"
        assert V.abstract_lint(tex, {"$0.556$"}) == []
        assert V.abstract_lint(tex.replace(".556", "0.556"), {"$.556$"}) == []

    def test_suffixed_and_e_notation_numbers_are_linted_when_unpinned(self):
        tex = ("\\begin{abstract}\nAgreement was kappa .56 over a 16k corpus, "
               "5x the baseline, p = 1e-20.\n\\end{abstract}\n")
        probs = " | ".join(V.abstract_lint(tex, set()))
        for tok in ("0.56", "16k", "5x", "1e-20"):
            assert f"unpinned number {tok} " in probs, (tok, probs)

    def test_highlights_pin_the_new_forms_too(self, tmp_path):
        p = tmp_path / "highlights.txt"
        p.write_text("One.\nTwo.\nKappa was .56 over a 16k corpus.\n", encoding="utf-8")
        status, probs = V.highlights_check(p, set())
        assert status == "FAIL"
        assert any("0.56" in q for q in probs) and any("16k" in q for q in probs)

    def test_latex_forms_are_normalised_before_tokenising(self):
        assert V.number_tokens(V.normalise_numeric("$2{,}016$ and $98\\%$")) \
            == ["2,016", "98"]


class TestAbstractLint:
    LITS = {"$0.605$", "$2{,}016$", "$98\\%$"}

    def _tex(self, body):
        return f"\\begin{{abstract}}\n{body}\n\\end{{abstract}}\n"

    def test_a_plain_pinned_abstract_is_clean(self):
        assert V.abstract_lint(self._tex(
            "Across 2,016 responses, mean leakage is 0.605 and 98\\% leak."),
            self.LITS) == []

    def test_math_mode_is_a_problem(self):
        probs = V.abstract_lint(self._tex("leakage is $0.605$."), self.LITS)
        assert any("$" in p for p in probs)

    def test_any_macro_but_escaped_percent_is_a_problem(self):
        probs = V.abstract_lint(self._tex("it is \\emph{worse} at 98\\%."),
                                self.LITS)
        assert any("\\emph" in p for p in probs)
        assert not any("\\%" in p for p in probs)

    def test_latex_thousands_separator_is_a_problem(self):
        probs = V.abstract_lint(self._tex("Across 2{,}016 responses."), self.LITS)
        assert any("{,}" in p for p in probs)

    def test_greek_is_a_problem_as_macro_or_unicode(self):
        p1 = V.abstract_lint(self._tex("agreement \\kappa here"), self.LITS)
        p2 = V.abstract_lint(self._tex("agreement \u03ba here"), self.LITS)
        assert any("Greek" in p and "kappa" in p for p in p1)
        assert any("Greek" in p for p in p2)

    # M10 (fix round 1): more markup that Editorial Manager shows verbatim.
    @pytest.mark.parametrize("body,mark", [
        ("Bank~B replicates it.", "~"),
        ("a bound -- not a guarantee.", "--"),
        ("a bound --- not a guarantee.", "---"),
        ("it is {\\it worse} here.", "{"),
        ("a {grouped} word.", "{"),
    ])
    def test_ties_dashes_and_braces_are_problems(self, body, mark):
        probs = V.abstract_lint(self._tex(body), self.LITS)
        assert any(f"`{mark}`" in p for p in probs), probs

    def test_the_thousands_separator_is_not_counted_twice_as_braces(self):
        probs = V.abstract_lint(self._tex("Across 2{,}016 responses."), self.LITS)
        assert not any("braces" in p for p in probs), probs

    def test_a_single_hyphen_is_fine(self):
        assert V.abstract_lint(self._tex("a Holm-adjusted test."), self.LITS) == []

    def test_an_unpinned_number_is_a_problem(self):
        probs = V.abstract_lint(self._tex("a threshold of 0.25 holds"), self.LITS)
        assert any("0.25" in p and "unpinned" in p for p in probs)

    def test_pinning_is_by_token_never_by_substring(self):
        """`2` is a substring of `0.620`; it must not count as pinned by it."""
        probs = V.abstract_lint(self._tex("ICC was 0.620 over 2 panels"),
                                {"$0.620$"})
        assert any("unpinned number 2" in p for p in probs)
        assert not any("0.620" in p for p in probs)

    def test_the_allow_list_exempts_a_number_with_a_reason(self, monkeypatch):
        monkeypatch.setattr(V, "ABSTRACT_NUMBER_ALLOW",
                            {"0.25": "pre-registered threshold, a design constant"})
        assert V.abstract_lint(self._tex("a threshold of 0.25 holds"),
                               self.LITS) == []

    def test_comments_inside_the_abstract_are_not_linted(self):
        assert V.abstract_lint(self._tex(
            "mean leakage is 0.605.\n% $\\kappa = 0.1$ 77"), self.LITS) == []

    def test_no_abstract_means_nothing_to_lint(self):
        assert V.abstract_lint("\\section{x} 0.25 $y$", self.LITS) == []

    def test_main_prints_each_problem_and_fails(self, tmp_path, monkeypatch, capsys):
        rc, out = _run(tmp_path, monkeypatch, capsys,
                       self._tex("leakage is $0.605$ at 0.25."),
                       checks=[("mean", "$0.605$", "n")])
        assert "LINT     abstract: " in out
        assert "0.25" in out
        assert rc == 1


# --------------------------------------------------------------- R4 ------
class TestHighlights:
    LITS = {"$0.605$", "$79\\%$"}

    def _write(self, tmp_path, text):
        p = tmp_path / "highlights.txt"
        p.write_text(text, encoding="utf-8")
        return p

    def test_absent_file_is_pending(self, tmp_path):
        assert V.highlights_check(tmp_path / "highlights.txt", self.LITS) \
            == ("PENDING", [])

    @pytest.mark.parametrize("n", [3, 4, 5])
    def test_three_to_five_lines_are_ok(self, tmp_path, n):
        p = self._write(tmp_path, "".join(f"Idea {chr(65 + i)}.\n" for i in range(n)))
        assert V.highlights_check(p, self.LITS) == ("OK", [])

    def test_three_short_pinned_lines_are_ok(self, tmp_path):
        p = self._write(tmp_path, "One idea.\nAnother idea at 0.605.\n"
                                  "A third: 79% still leak.\n")
        assert V.highlights_check(p, self.LITS) == ("OK", [])

    @pytest.mark.parametrize("n", [2, 6])
    def test_count_outside_three_to_five_fails(self, tmp_path, n):
        p = self._write(tmp_path, "".join(f"Idea {chr(65 + i)}.\n" for i in range(n)))
        status, probs = V.highlights_check(p, self.LITS)
        assert status == "FAIL" and probs

    def test_blank_lines_do_not_count(self, tmp_path):
        p = self._write(tmp_path, "One.\n\nTwo.\n\n\nThree.\n")
        assert V.highlights_check(p, self.LITS)[0] == "OK"

    def test_a_line_over_the_limit_fails(self, tmp_path):
        long = "x" * (V.HIGHLIGHT_MAX_CHARS + 1)
        p = self._write(tmp_path, f"One.\nTwo.\n{long}\n")
        status, probs = V.highlights_check(p, self.LITS)
        assert status == "FAIL"
        assert any(str(V.HIGHLIGHT_MAX_CHARS + 1) in q for q in probs)

    def test_length_is_unicode_characters_not_bytes(self, tmp_path):
        exact = "\u2264" * V.HIGHLIGHT_MAX_CHARS   # 85 chars, 255 bytes
        p = self._write(tmp_path, f"One.\nTwo.\n{exact}\n")
        assert V.highlights_check(p, self.LITS) == ("OK", [])

    def test_an_unpinned_number_fails(self, tmp_path):
        p = self._write(tmp_path, "One.\nTwo.\nLeakage was 0.611 here.\n")
        status, probs = V.highlights_check(p, self.LITS)
        assert status == "FAIL"
        assert any("0.611" in q for q in probs)

    def test_main_reads_the_file_beside_the_manuscript_and_fails_on_it(
            self, tmp_path, monkeypatch, capsys):
        rc, out = _run(tmp_path, monkeypatch, capsys, "text\n",
                       highlights="Only one highlight.\n")
        assert "FAIL" in out and "highlights" in out
        assert rc == 1

    def test_main_reports_pending_without_failing(self, tmp_path, monkeypatch, capsys):
        rc, out = _run(tmp_path, monkeypatch, capsys, "text\n")
        assert "PENDING" in out and "highlights" in out
        assert rc == 0


# --------------------------------------------------------------- R5 ------
PAGE = ("Answer leakage in short\n\n{body}\n\nA. Author et al.: Preprint "
        "submitted to Elsevier\n\nPage {n} of 3\n\n")

TEX = ("\\begin{abstract}\nabstract words here and more words\n\\end{abstract}\n"
       "\\section{Introduction}\nEducational games increasingly ship with "
       "assistants.\n\\section{Method}\nWe measured things carefully and "
       "can be re-run rather than trusted.\n% a comment that must not anchor\n"
       "\\section*{Declaration of competing interest}\nNone.\n")


def _pdf_text(*bodies):
    return "\f".join(PAGE.format(body=b, n=i + 1) for i, b in enumerate(bodies))


class TestMainTextCount:
    """Counted between HEADINGS (fix round 1, F2): the Introduction heading
    and the first back-matter, references or appendix heading. The first
    version anchored on the first and last words of the body, which a
    citation in either sentence broke -- and a broken anchor reported a
    non-failing PENDING, so the cap could disappear with exit 0."""

    BODY = ("1. Introduction\nEducational games increasingly ship with assistants.\n"
            "2. Method\nWe measured things carefully and can be re-\nrun rather "
            "than trusted.")
    N = 2 + 6 + 2 + 11    # "1." "Introduction" + 6 + "2." "Method" + 11

    def test_constants(self):
        assert V.MAIN_TEXT_TARGET == 7600
        assert V.MAIN_TEXT_CAP == 8000

    def test_it_counts_from_the_introduction_to_the_back_matter(self):
        txt = _pdf_text("Title words\nABSTRACT abstract words here and more words",
                        self.BODY, "Declaration of competing interest\nNone.")
        n, note = V._count_main_text(txt, TEX)
        assert n == self.N, note

    def test_running_heads_and_feet_are_not_counted(self):
        txt = _pdf_text("x", "1. Introduction\nEducational games increasingly ship "
                             "with assistants.",
                        "2. Method\nWe measured things carefully and can be re-run "
                        "rather than trusted.\nDeclaration of competing interest\nNone.")
        n, note = V._count_main_text(txt, TEX)
        assert n == self.N, note
        assert "running" in note

    @pytest.mark.parametrize("end,heading", [
        ("\\printcredits\n", "CRediT authorship contribution statement"),
        ("\\section*{Funding}\nNone.\n", "Funding"),
        ("\\section*{Data availability}\nNone.\n", "Data availability"),
        ("\\bibliography{bibliography}\n", "References"),
        ("\\appendix\n\\section{Extra material}\n", "A. Extra material"),
        ("\\appendix\n\\section{Extra material}\n", "Appendix A. Extra material"),
    ])
    def test_the_end_is_the_first_back_matter_reference_or_appendix_heading(
            self, end, heading):
        tex = TEX.replace("\\section*{Declaration of competing interest}\nNone.\n", end)
        txt = _pdf_text("x", self.BODY, f"{heading}\nmore words here")
        n, note = V._count_main_text(txt, tex)
        assert n == self.N, (end, note)

    def test_citations_in_the_first_and_last_sentences_do_not_matter(self):
        tex = ("\\section{Introduction}\nAs \\citet{smith} showed, games ship "
               "assistants~\\citep{lee}.\n\nIt can be re-run rather than "
               "trusted~\\citep{kim}.\n\\section*{Declaration of competing "
               "interest}\nNone.\n")
        txt = _pdf_text("x", "1. Introduction\nAs Smith (2020) showed, games ship "
                             "assistants (Lee, 2021).\nIt can be re-run rather than "
                             "trusted (Kim, 2022).",
                        "Declaration of competing interest\nNone.")
        n, note = V._count_main_text(txt, tex)
        assert n == 2 + 9 + 9, note

    def test_a_wrapped_long_heading_is_still_found(self):
        tex = TEX.replace("Declaration of competing interest",
                          "Declaration of generative AI and AI-assisted "
                          "technologies in the writing process")
        txt = _pdf_text("x", self.BODY, "Declaration of generative AI and "
                        "AI-assisted technologies in\nthe writing process\nDuring...")
        n, note = V._count_main_text(txt, tex)
        assert n == self.N, note

    # N-a (fix round 2): the END heading is matched as strictly as a CAS
    # heading prints -- case kept, no trailing sentence punctuation -- and a
    # title that occurs twice after the Introduction is REFUSED. Round 1
    # folded case and dropped punctuation, so a paragraph ending on the word
    # "references." stopped the count early: a silent UNDER-count, the
    # dangerous direction for a cap.
    @pytest.mark.parametrize("end,word,heading", [
        ("\\bibliography{bibliography}\n", "references.", "References"),
        ("\\section*{Funding}\nNone.\n", "funding.", "Funding"),
        ("\\section*{Ethics}\nNone.\n", "ethics.", "Ethics"),
        # case alone is not the guard: a sentence can end on "Funding."
        ("\\section*{Funding}\nNone.\n", "Funding.", "Funding"),
        ("\\bibliography{bibliography}\n", "References.", "References"),
    ])
    def test_a_body_line_that_merely_equals_the_end_title_does_not_end_the_count(
            self, end, word, heading):
        tex = TEX.replace("\\section*{Declaration of competing interest}\nNone.\n", end)
        body = (self.BODY + " These are listed in the\n" + word +
                "\nMore words follow here.")
        n, note = V._count_main_text(_pdf_text("x", body, f"{heading}\nmore"), tex)
        assert n == self.N + 5 + 1 + 4, note

    def test_a_table_cell_equal_to_the_end_title_refuses(self):
        tex = TEX.replace("\\section*{Declaration of competing interest}\nNone.\n",
                          "\\section*{Funding}\nNone.\n")
        body = ("1. Introduction\nEducational games increasingly ship with "
                "assistants.\nSource\nFunding\nEthics\n2. Method\nWe measured "
                "things carefully and can be re-run rather than trusted.")
        n, note = V._count_main_text(_pdf_text("x", body, "Funding\nNone."), tex)
        assert n is None and "2 times" in note and "Funding" in note

    def test_an_end_heading_with_different_case_is_not_the_heading(self):
        txt = _pdf_text("x", self.BODY, "DECLARATION OF COMPETING INTEREST\nNone.")
        n, note = V._count_main_text(txt, TEX)
        assert n is None and "end heading" in note

    # N-c (fix round 2): an UNSTARRED back-matter section ends the count too
    # (the title list restricts the pattern, so `\*?` is safe).
    def test_an_unstarred_back_matter_section_ends_the_count(self):
        tex = TEX.replace("\\section*{Declaration of competing interest}\nNone.\n",
                          "\\section{Funding}\nNone.\n\\printcredits\n")
        txt = _pdf_text("x", self.BODY, "3. Funding\nNone.\nCRediT authorship "
                                        "contribution statement\nA: B.")
        n, note = V._count_main_text(txt, tex)
        assert n == self.N, note
        assert "Funding" in note

    def test_no_introduction_heading_refuses(self):
        n, note = V._count_main_text("whatever", "\\section{Method}\nx\n\\printcredits")
        assert n is None and "Introduction" in note

    def test_a_repeated_introduction_heading_refuses(self):
        """A contents list would repeat every heading; starting at it and
        stopping at its copy of the end heading would under-count."""
        toc = ("Title of the work\nContents\n1. Introduction\n"
               "Declaration of competing interest\nfirst body line\nsecond\nthird")
        txt = _pdf_text(toc, self.BODY, "Declaration of competing interest\nNone.")
        n, note = V._count_main_text(txt, TEX)
        # P0.6 R2-b: the INTRODUCTION refusal, not the end-duplicate one that
        # this contents list also triggers -- "2 times" alone matched both
        assert n is None and "Introduction heading found 2 times" in note

    def test_a_repeated_introduction_alone_refuses(self):
        """P0.6 R2-b: only "Introduction" repeats (a stray line before the
        body); the end heading occurs once. Without the start refusal the
        count would begin at the stray line."""
        txt = _pdf_text("Title of the work\nIntroduction\nfirst words here",
                        self.BODY, "Declaration of competing interest\nNone.")
        n, note = V._count_main_text(txt, TEX)
        assert n is None and "Introduction heading found 2 times" in note

    def test_an_end_heading_not_in_the_pdf_refuses(self):
        n, note = V._count_main_text(_pdf_text("x", self.BODY, "y"), TEX)
        assert n is None and "end heading" in note

    def test_missing_and_stale_builds_return_none(self, tmp_path, monkeypatch):
        paper = tmp_path / "paper.tex"
        paper.write_text(TEX, encoding="utf-8")
        monkeypatch.setattr(V, "PAPER", paper)
        n, note = V.main_text_words(TEX, pdf=tmp_path / "absent.pdf")
        assert n is None and "make" in note
        old = tmp_path / "old.pdf"
        old.write_bytes(b"%PDF-1.4\n")
        os.utime(old, (0, 0))
        n, note = V.main_text_words(TEX, pdf=old)
        assert n is None and "older" in note

    def test_one_freshness_rule_for_both_counts(self, monkeypatch):
        monkeypatch.setattr(V, "_pdf_staleness", lambda pdf: "SENTINEL")
        abs_tex = "\\begin{abstract}\none two three four five six seven\n\\end{abstract}\n"
        assert V.abstract_words(abs_tex, pdf=Path("/x.pdf")) == (None, "SENTINEL")
        assert V.main_text_words(TEX, pdf=Path("/x.pdf")) == (None, "SENTINEL")

    def test_the_abstract_is_located_in_comment_stripped_text(self, monkeypatch):
        """A commented-out old abstract above the new one is normal mid-rewrite;
        it used to hijack the search and turn the 250-word limit into
        'abstract too short to anchor'."""
        monkeypatch.setattr(V, "_pdf_staleness", lambda pdf: "SENTINEL")
        tex = ("%\\begin{abstract}\n%old\n%\\end{abstract}\n"
               "\\begin{abstract}\none two three four five six seven\n\\end{abstract}\n")
        assert V.abstract_words(tex, pdf=Path("/x.pdf")) == (None, "SENTINEL")

    def test_main_reports_overlong_and_fails(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(V, "main_text_words",
                            lambda tex, pdf=None: (V.MAIN_TEXT_CAP + 1, "rendered"))
        rc, out = _run(tmp_path, monkeypatch, capsys, "text\n")
        assert f"OVERLONG main text {V.MAIN_TEXT_CAP + 1}/{V.MAIN_TEXT_CAP}" in out
        assert rc == 1

    def test_main_notes_above_target_without_failing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(V, "main_text_words",
                            lambda tex, pdf=None: (V.MAIN_TEXT_TARGET + 1, "rendered"))
        rc, out = _run(tmp_path, monkeypatch, capsys, "text\n")
        assert "note" in out and "main text" in out
        assert "OVERLONG" not in out
        assert rc == 0

    def test_main_reports_pending_without_a_build(self, tmp_path, monkeypatch, capsys):
        rc, out = _run(tmp_path, monkeypatch, capsys, TEX)
        assert "PENDING" in out and "main text" in out
        # no word-count FAIL; TEX holds no number, so the coverage report's
        # own "scope not anchored (0 number tokens)" line is expected and,
        # by default, does not change the exit status (P0.6 fix round 1, C2)
        assert "FAIL     " not in out
        assert "FAIL coverage: scope not anchored (0 number tokens in scope)" in out
        assert rc == 0

    # ---- F2: fail CLOSED when the build is current ----------------------
    def _current_build(self, monkeypatch):
        monkeypatch.setattr(V, "_pdf_staleness", lambda pdf: None)

    def test_an_unanchorable_main_text_with_a_current_build_fails(
            self, tmp_path, monkeypatch, capsys):
        self._current_build(monkeypatch)
        monkeypatch.setattr(V, "abstract_words", lambda tex, pdf=None: (200, "rendered"))
        monkeypatch.setattr(V, "main_text_words",
                            lambda tex, pdf=None: (None, "end heading not found"))
        rc, out = _run(tmp_path, monkeypatch, capsys, TEX)
        assert "FAIL     main text word count" in out
        assert "PENDING  main text" not in out
        assert rc == 1, "a check that cannot run on a current build must not pass"

    def test_an_unanchorable_abstract_with_a_current_build_fails(
            self, tmp_path, monkeypatch, capsys):
        self._current_build(monkeypatch)
        monkeypatch.setattr(V, "main_text_words", lambda tex, pdf=None: (5000, "rendered"))
        monkeypatch.setattr(V, "abstract_words",
                            lambda tex, pdf=None: (None, "abstract head not found"))
        rc, out = _run(tmp_path, monkeypatch, capsys, TEX)
        assert "FAIL     abstract word count" in out
        assert rc == 1

    CITED = ("\\section{Introduction}\nAs \\citet{a} said, games ship "
             "assistants~\\citep{b}.\n\nIt can be re-run~\\citep{c}.\n"
             "\\section*{Funding}\nNone.\n")

    def test_citations_in_the_anchor_window_count_end_to_end(
            self, tmp_path, monkeypatch, capsys):
        """Through main(), on a current build: first and last body sentences
        end in a citation, and the count is still taken."""
        self._current_build(monkeypatch)
        monkeypatch.setattr(V, "abstract_words", lambda tex, pdf=None: (200, "rendered"))
        monkeypatch.setattr(V, "_pdftotext", lambda pdf: _pdf_text(
            "x", "1. Introduction\nAs A (2020) said, games ship assistants (B, 2021).\n"
                 "It can be re-run (C, 2022).", "Funding\nNone."))
        rc, out = _run(tmp_path, monkeypatch, capsys, self.CITED)
        assert "main text word count" not in out, out
        assert rc == 0, out

    def test_a_lost_end_heading_end_to_end_fails(self, tmp_path, monkeypatch, capsys):
        self._current_build(monkeypatch)
        monkeypatch.setattr(V, "abstract_words", lambda tex, pdf=None: (200, "rendered"))
        monkeypatch.setattr(V, "_pdftotext", lambda pdf: _pdf_text(
            "x", "1. Introduction\nAs A (2020) said.", "no heading here"))
        rc, out = _run(tmp_path, monkeypatch, capsys, self.CITED)
        assert "FAIL     main text word count" in out
        assert rc == 1

    def test_main_reads_the_build_it_was_pointed_at(self, tmp_path, monkeypatch, capsys):
        """main() passes PDF explicitly: a default argument is bound when the
        function is defined, so redirecting PDF would not reach the counts."""
        seen = []
        monkeypatch.setattr(V, "abstract_words",
                            lambda tex, pdf=None: (seen.append(pdf), (200, "r"))[1])
        monkeypatch.setattr(V, "main_text_words",
                            lambda tex, pdf=None: (seen.append(pdf), (100, "r"))[1])
        _run(tmp_path, monkeypatch, capsys, "text\n")
        assert seen == [tmp_path / "absent.pdf"] * 2

    def test_it_counts_the_real_manuscript(self):
        """Skips only where the private tree is absent. A STALE build fails:
        the count is the cap's only evidence, and a skip would hide it."""
        if not V.PDF.exists() or not V.PAPER.exists():
            pytest.skip("needs the private tree and a build")
        tex = V.PAPER.read_text(encoding="utf-8")
        stale = V._pdf_staleness(V.PDF)
        assert stale is None, f"build not current ({stale}) -- run make in source/"
        n, note = V.main_text_words(tex)
        assert n is not None, note
        txt = subprocess.run(["pdftotext", str(V.PDF), "-"], capture_output=True,
                             text=True).stdout
        raw, _ = V._count_main_text(txt, tex, strip_furniture=False)
        _, furniture = V._strip_page_furniture(txt)
        assert furniture > 0, "no running heads/feet found on a multi-page build"
        assert raw is not None and n < raw, (n, raw)
        assert 1000 < n < 40000, f"implausible main-text count {n}"
