"""The anonymised manuscript and the title page are GENERATED, never hand-synced.

Before P0.4 `paper_anon.tex` and `title_page.tex` were hand-made copies of
`paper.tex`, and they had already drifted in the parts nobody looks at: the
anonymised copy had lost a co-author's e-mail line and carried a misspelt street
name, both inside commented-out text. Harmless there; the next drift would not
be. `make_variants.py` derives both from block markers in `paper.tex`, and the
first test here fails if either file on disk is edited by hand.
"""
import subprocess
import sys

import pytest

import make_variants as MV


def _gen(text, variant):
    return MV.render(text, variant)


# ------------------------------------------------- files on disk ---------
def test_the_variants_on_disk_are_exactly_what_paper_tex_generates():
    if not MV.PAPER.exists():
        pytest.skip("paper.tex is not part of this tree")
    src = MV.PAPER.read_text(encoding="utf-8")
    for variant, path in MV.OUTPUTS.items():
        assert path.exists(), f"{path.name} missing -- run make_variants.py"
        assert path.read_text(encoding="utf-8") == _gen(src, variant), (
            f"{path.name} differs from what paper.tex generates: edit paper.tex "
            f"and re-run make_variants.py, never the variant by hand")


def test_generated_files_say_they_are_generated():
    out = _gen("a\n", "anon")
    assert out.splitlines()[0].startswith("%") and "make_variants" in out.splitlines()[0]


# ------------------------------------------------- each marker type ------
DOC = """pre
%<*identity>
\\author{Someone}

\\ead{x@y.z}
%</identity>
%<*body>
Body text.
%</body>
%<*anon-only>
%\\author{Anonymous}
%</anon-only>
%<*titlepage-only>
%Title-page-only line
%</titlepage-only>
post
"""


def _content(out):
    """The generated text minus its banner line."""
    return out.split("\n", 1)[1]


def test_identity_is_commented_in_anon_and_kept_on_the_title_page():
    a, t = _content(_gen(DOC, "anon")), _content(_gen(DOC, "title"))
    assert "%\\author{Someone}" in a and "%\\ead{x@y.z}" in a
    assert "\n\\author{Someone}" in t and "\n\\ead{x@y.z}" in t


def test_body_is_kept_in_anon_and_commented_on_the_title_page():
    a, t = _content(_gen(DOC, "anon")), _content(_gen(DOC, "title"))
    assert "\nBody text.\n" in a
    assert "\n%Body text.\n" in t


def test_anon_only_is_uncommented_in_anon_only():
    a, t = _content(_gen(DOC, "anon")), _content(_gen(DOC, "title"))
    assert "\n\\author{Anonymous}\n" in a
    assert "\n%\\author{Anonymous}\n" in t


def test_titlepage_only_is_uncommented_on_the_title_page_only():
    a, t = _content(_gen(DOC, "anon")), _content(_gen(DOC, "title"))
    assert "\nTitle-page-only line\n" in t
    assert "\n%Title-page-only line\n" in a


def test_blank_lines_stay_blank_and_markers_pass_through():
    a = _content(_gen(DOC, "anon"))
    assert "%\\author{Someone}\n\n%\\ead{x@y.z}" in a
    for m in ("%<*identity>", "%</identity>", "%<*body>", "%</body>"):
        assert f"\n{m}\n" in "\n" + a


def test_text_outside_every_block_is_untouched():
    for v in ("anon", "title"):
        out = _content(_gen(DOC, v))
        assert out.startswith("pre\n") and out.endswith("post\n")


def test_line_count_and_trailing_newline_follow_the_source():
    for v in ("anon", "title"):
        assert _content(_gen(DOC, v)).count("\n") == DOC.count("\n")


def test_body_nested_in_identity_is_commented_in_both_variants_once():
    doc = ("%<*identity>\n%<*body>\n\\shortauthors{A.B. X}\n%</body>\n"
           "%<*titlepage-only>\n%\\shortauthors{A. B. X}\n%</titlepage-only>\n"
           "%</identity>\n")
    a, t = _content(_gen(doc, "anon")), _content(_gen(doc, "title"))
    assert "\n%\\shortauthors{A.B. X}\n" in a
    assert "\n%%\\shortauthors{A. B. X}\n" in a        # identity comments it again
    assert "\n%\\shortauthors{A.B. X}\n" in t
    assert "\n\\shortauthors{A. B. X}\n" in t


# ------------------------------------------------- failing loudly --------
@pytest.mark.parametrize("doc,why", [
    ("%<*identity>\n%<*body>\nx\n%</identity>\n%</body>\n", "mis-nested"),
    ("%<*identity>\nx\n", "unclosed"),
    ("x\n%</body>\n", "without"),
    ("%<*idnetity>\nx\n%</idnetity>\n", "unknown"),
    ("%<*body>\n%<*body>\nx\n%</body>\n%</body>\n", "nested inside itself"),
    ("%<*identity>\n%<*anon-only>\n%x\n%</anon-only>\n%</identity>\n", "never"),
    ("%<*body>\n%<*titlepage-only>\n%x\n%</titlepage-only>\n%</body>\n", "never"),
    ("%<*anon-only>\n%<*body>\n%x\n%</body>\n%</anon-only>\n", "inside"),
    ("%<*anon-only>\n\\author{Visible in paper.tex}\n%</anon-only>\n", "stored commented"),
    ("%<*identity\nx\n%</identity>\n", "malformed"),
])
def test_bad_markers_fail_loudly(doc, why):
    with pytest.raises(MV.MarkerError, match=why):
        _gen(doc, "anon")


def test_marker_errors_name_the_line():
    with pytest.raises(MV.MarkerError, match=r"line 3"):
        _gen("ok\n%<*identity>\n%</body>\n", "anon")


def test_an_unknown_variant_is_refused():
    with pytest.raises(ValueError):
        _gen("x\n", "paper_typo")


# ------------------------------------------------- CLI --------------------
def test_check_mode_fails_on_a_hand_edited_variant(tmp_path, monkeypatch):
    src = tmp_path / "paper.tex"
    src.write_text(DOC, encoding="utf-8")
    outs = {"anon": tmp_path / "paper_anon.tex", "title": tmp_path / "title_page.tex"}
    monkeypatch.setattr(MV, "PAPER", src)
    monkeypatch.setattr(MV, "OUTPUTS", outs)
    assert MV.main([]) == 0
    assert MV.main(["--check"]) == 0
    outs["anon"].write_text(outs["anon"].read_text() + "% hand edit\n")
    assert MV.main(["--check"]) == 1


def test_cli_reports_a_marker_error_with_a_nonzero_exit(tmp_path, monkeypatch, capsys):
    src = tmp_path / "paper.tex"
    src.write_text("%<*identity>\nx\n", encoding="utf-8")
    monkeypatch.setattr(MV, "PAPER", src)
    monkeypatch.setattr(MV, "OUTPUTS", {"anon": tmp_path / "a.tex",
                                        "title": tmp_path / "t.tex"})
    assert MV.main([]) == 2
    assert "unclosed" in capsys.readouterr().out
    assert not (tmp_path / "a.tex").exists(), "nothing may be written on error"


# ------------------------------------------------- F4 (rounds 1 and 2) -----
# The hand-made title page replaced two `\ref`s with literal section numbers,
# because its body (and so every \label) is commented out. A literal number is
# a hand-kept copy of the build: the C&E restructure renumbers sections and
# nothing would notice. Round 1 checked a literal only against a body twin on
# the very next line, and the re-review rendered a stale number through a
# blank line, a comment line, and a block with no twin at all. Round 2's rule
# (binding): a VISIBLE title-page line may not carry a literal cross-reference
# number at all -- it says \auxref{label}, which paper.aux resolves.
import os


TWIN = ("%<*body>\nset out in Section~\\ref{sec:banks}. Two\n%</body>\n"
        "%<*titlepage-only>\n%% (a note that stays a comment)\n"
        "%set out in Section~{num}. Two\n%</titlepage-only>\n")


def _aux_tree(tmp_path, monkeypatch, labels, older=False):
    paper = tmp_path / "paper.tex"
    paper.write_text("x\n", encoding="utf-8")
    aux = tmp_path / "paper.aux"
    aux.write_text("".join(f"\\newlabel{{{k}}}{{{{{v}}}{{6}}{{T}}{{s.{v}}}{{}}}}\n"
                           for k, v in labels.items()), encoding="utf-8")
    if older:
        os.utime(aux, (0, 0))
    monkeypatch.setattr(MV, "PAPER", paper)
    return aux


AUX_TWIN = TWIN.replace("{num}", "\\auxref{sec:banks}")


def test_an_auxref_title_page_line_resolves_from_paper_aux(tmp_path, monkeypatch):
    _aux_tree(tmp_path, monkeypatch, {"sec:banks": "4.5"})
    t = _content(_gen(AUX_TWIN, "title"))
    assert "\nset out in Section~4.5. Two\n" in t and "\\auxref" not in t.replace("%", "")


def test_a_renumbered_section_follows_paper_aux(tmp_path, monkeypatch):
    _aux_tree(tmp_path, monkeypatch, {"sec:banks": "5.2"})
    assert "\nset out in Section~5.2. Two\n" in _content(_gen(AUX_TWIN, "title"))


# The re-review's three probes: a literal that round 1 let through because its
# body twin was not on the very next line -- or did not exist.
PROBES = {
    "blank-line gap": ("%<*body>\nin Section~\\ref{sec:banks}.\n%</body>\n\n"
                       "%<*titlepage-only>\n%in Section~4.5.\n%</titlepage-only>\n"),
    "comment-line gap": ("%<*body>\nin Section~\\ref{sec:banks}.\n%</body>\n% note\n"
                         "%<*titlepage-only>\n%in Section~4.5.\n%</titlepage-only>\n"),
    "no twin": "%<*titlepage-only>\n%in Section~4.5.\n%</titlepage-only>\n",
}


@pytest.mark.parametrize("name", list(PROBES))
def test_a_literal_section_number_fails_the_generator_wherever_it_sits(
        tmp_path, monkeypatch, name):
    _aux_tree(tmp_path, monkeypatch, {"sec:banks": "5.2"})
    with pytest.raises(MV.MarkerError, match=r"line \d+.*use `\\auxref\{label\}`"):
        _gen(PROBES[name], "title")


@pytest.mark.parametrize("literal", [
    "Section~4.5", "Section 4", "Sections~3 and 4", "Sect.~2", "Table~2",
    "Tables 1", "Figure~3", "Figures 2", "Fig.~1", "Appendix~A", "Appendix B",
    "\u00a7 4", "\u00a74.2",
])
def test_every_literal_cross_reference_form_is_refused(tmp_path, monkeypatch, literal):
    monkeypatch.setattr(MV, "PAPER", tmp_path / "paper.tex")   # refused before aux
    doc = f"%<*titlepage-only>\n%as shown in {literal}, we\n%</titlepage-only>\n"
    with pytest.raises(MV.MarkerError, match=r"auxref"):
        _gen(doc, "title")


@pytest.mark.parametrize("line", [
    "Section~\\auxref{sec:banks} shows", "the Section titles", "a Table of",
    "Appendix material", "Figure caption text",
])
def test_words_without_a_literal_number_pass(tmp_path, monkeypatch, line):
    _aux_tree(tmp_path, monkeypatch, {"sec:banks": "4.5"})
    _gen(f"%<*titlepage-only>\n%{line}\n%</titlepage-only>\n", "title")


def test_a_literal_in_a_comment_or_outside_the_title_variant_is_not_refused(
        tmp_path, monkeypatch):
    """Only VISIBLE title-page lines matter: a `%%` note inside the block stays
    a comment after uncommenting, and the anonymised variant never shows a
    titlepage-only line."""
    monkeypatch.setattr(MV, "PAPER", tmp_path / "paper.tex")
    doc = "%<*titlepage-only>\n%% was Section~4.5 before\n%</titlepage-only>\n"
    _gen(doc, "title")
    _gen(PROBES["no twin"], "anon")


def test_the_rule_is_the_title_pages_only(tmp_path, monkeypatch):
    """The anonymised variant keeps the body, so its \\refs resolve; a literal
    in an anon-only line is outside this rule (it is the TITLE page that has
    no labels)."""
    monkeypatch.setattr(MV, "PAPER", tmp_path / "paper.tex")
    doc = "%<*anon-only>\n%as shown in Section~4, we\n%</anon-only>\n"
    assert "\nas shown in Section~4, we\n" in _content(_gen(doc, "anon"))


def test_the_cli_exits_nonzero_on_a_literal(tmp_path, monkeypatch, capsys):
    src = tmp_path / "paper.tex"
    src.write_text(PROBES["no twin"], encoding="utf-8")
    monkeypatch.setattr(MV, "PAPER", src)
    monkeypatch.setattr(MV, "OUTPUTS", {"anon": tmp_path / "a.tex",
                                        "title": tmp_path / "t.tex"})
    assert MV.main([]) == 2
    out = capsys.readouterr().out
    assert "line 2" in out and "\\auxref{label}" in out


@pytest.mark.parametrize("labels,older,why", [
    (None, False, "no paper.aux"),
    ({"sec:banks": "4.5"}, True, "older than paper.tex"),
    ({"sec:other": "1"}, False, "lacks"),
])
def test_paper_aux_must_exist_be_current_and_hold_the_label(
        tmp_path, monkeypatch, labels, older, why):
    if labels is None:
        (tmp_path / "paper.tex").write_text("x\n", encoding="utf-8")
        monkeypatch.setattr(MV, "PAPER", tmp_path / "paper.tex")
    else:
        _aux_tree(tmp_path, monkeypatch, labels, older)
    with pytest.raises(MV.MarkerError, match=why):
        _gen(AUX_TWIN, "title")


def test_the_anonymised_variant_does_not_need_paper_aux(tmp_path, monkeypatch):
    monkeypatch.setattr(MV, "PAPER", tmp_path / "paper.tex")   # no aux anywhere
    _gen(AUX_TWIN, "anon")


def test_an_alternate_without_a_cross_reference_needs_no_aux(tmp_path, monkeypatch):
    """The short-author alternate differs from its twin on purpose."""
    monkeypatch.setattr(MV, "PAPER", tmp_path / "paper.tex")   # no aux anywhere
    doc = ("%<*body>\n\\shortauthors{A.B. X}\n%</body>\n"
           "%<*titlepage-only>\n%\\shortauthors{A. B. X}\n%</titlepage-only>\n")
    assert "\n\\shortauthors{A. B. X}\n" in _content(_gen(doc, "title"))


def test_the_real_title_page_takes_its_numbers_from_paper_aux():
    """The live check: paper.tex's title-page alternates carry \\auxref, not
    literals, and render to what paper.aux says sec:banks and sec:method are."""
    aux = MV.PAPER.with_suffix(".aux")
    if not MV.PAPER.exists() or not aux.exists():
        pytest.skip("needs the private tree and a build")
    labels = MV.aux_labels(aux)
    src = MV.PAPER.read_text(encoding="utf-8")
    blocks = re.findall(r"%<\*titlepage-only>\n((?:(?!%<).*\n)*?)%</titlepage-only>", src)
    refs = [l for b in blocks for l in re.findall(r"\\auxref\{([^}]+)\}", b)]
    assert sorted(refs) == ["sec:banks", "sec:method"], refs
    out = MV.render(src, "title")
    for label in refs:
        assert f"Section~{labels[label]}" in out


# ------------------------------------------------- M8: anon content --------
import re  # noqa: E402

import texutil  # noqa: E402


def test_the_generated_anonymised_text_carries_no_identity():
    """Comment-stripped paper_anon.tex: what LaTeX actually reads. A new
    identifying line placed outside an identity block would pass every other
    test here -- regeneration would faithfully reproduce it."""
    path = MV.OUTPUTS["anon"]
    if not path.exists():
        pytest.skip("paper_anon.tex is not part of this tree")
    live = texutil.strip_comments(path.read_text(encoding="utf-8"))
    banned = re.compile(
        r"\\(?:author|ead|affiliation|cormark|cortext|credit|printcredits)\b"
        r"|ssemerikov|[\w.+-]+@[\w-]+\.[A-Za-z]|\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b")
    assert banned.findall(live) == []


# ------------------------------------------------- P0.6: R2-a / R2-d / R2-c --
# R2-a: comments are inert. The literal rule and the \auxref substitution
# apply to the CODE part of a visible title-page line only; the comment part
# is emitted verbatim. The generator's own advice ("use \auxref{label}") used
# to trip it when written into a note.
def test_a_note_naming_auxref_label_renders_verbatim(tmp_path, monkeypatch):
    monkeypatch.setattr(MV, "PAPER", tmp_path / "paper.tex")   # no aux needed
    doc = "%<*titlepage-only>\n%% (use \\auxref{label} here)\n%</titlepage-only>\n"
    assert "\n% (use \\auxref{label} here)\n" in _content(_gen(doc, "title"))


def test_a_note_naming_a_real_label_is_not_rewritten(tmp_path, monkeypatch):
    _aux_tree(tmp_path, monkeypatch, {"sec:banks": "5.2"})
    doc = ("%<*titlepage-only>\n%Bank~B, Section~\\auxref{sec:banks}. "
           "% (filled by \\auxref{sec:banks})\n%</titlepage-only>\n")
    out = _content(_gen(doc, "title"))
    assert "\nBank~B, Section~5.2. % (filled by \\auxref{sec:banks})\n" in out


def test_a_trailing_comment_quoting_a_literal_is_accepted(tmp_path, monkeypatch):
    _aux_tree(tmp_path, monkeypatch, {"x": "4.5"})
    doc = "%<*titlepage-only>\n%Section~\\auxref{x}. % was Section~4.5\n%</titlepage-only>\n"
    assert "\nSection~4.5. % was Section~4.5\n" in _content(_gen(doc, "title"))


def test_a_literal_in_the_code_part_is_still_refused_beside_a_note(tmp_path, monkeypatch):
    monkeypatch.setattr(MV, "PAPER", tmp_path / "paper.tex")
    doc = "%<*titlepage-only>\n%Section~4.5 % note\n%</titlepage-only>\n"
    with pytest.raises(MV.MarkerError, match="auxref"):
        _gen(doc, "title")


# R2-d: a raw \ref on the title page prints "??" -- its labels are commented out.
@pytest.mark.parametrize("cmd", ["ref", "pageref", "eqref", "autoref", "cref",
                                 "Cref", "nameref"])
def test_the_ref_family_is_refused_on_the_title_page(tmp_path, monkeypatch, cmd):
    monkeypatch.setattr(MV, "PAPER", tmp_path / "paper.tex")
    doc = f"%<*titlepage-only>\n%as set out in Section~\\{cmd}{{sec:banks}}.\n%</titlepage-only>\n"
    with pytest.raises(MV.MarkerError, match=r"line 2.*use `\\auxref\{label\}`"):
        _gen(doc, "title")


def test_a_ref_in_a_title_page_note_is_inert(tmp_path, monkeypatch):
    monkeypatch.setattr(MV, "PAPER", tmp_path / "paper.tex")
    doc = "%<*titlepage-only>\n%Bank B. % twin says \\ref{sec:banks}\n%</titlepage-only>\n"
    _gen(doc, "title")


# R2-c: the reviewer's residual forms.
@pytest.mark.parametrize("literal", [
    "section~4.5", "SECTION~4", "Sec.~4.5", "Sect.~4.5", "Section~{4.5}",
    "Fig.~3", "Figs.~3", "table 2", "appendix~B",
])
def test_the_residual_literal_forms_are_refused(tmp_path, monkeypatch, literal):
    monkeypatch.setattr(MV, "PAPER", tmp_path / "paper.tex")
    doc = f"%<*titlepage-only>\n%as shown in {literal}, we\n%</titlepage-only>\n"
    with pytest.raises(MV.MarkerError, match="auxref"):
        _gen(doc, "title")


@pytest.mark.parametrize("line", ["Figure x shows", "the section titles",
                                  "a figure of speech", "Sec.~\\auxref{s}"])
def test_case_insensitive_keywords_do_not_refuse_prose(tmp_path, monkeypatch, line):
    _aux_tree(tmp_path, monkeypatch, {"s": "2"})
    _gen(f"%<*titlepage-only>\n%{line}\n%</titlepage-only>\n", "title")


# ------------------------------------------------- P0.6 fix round 1 (M8) ---
@pytest.mark.parametrize("literal", [
    "Fig.\\ 3", "Sec.\\ 4.5", "§~4", "Section\\,4", "Section~\\textbf{4}",
    "Eq.~2", "Eqs.~2--3", "Tab.~2", "Sections~4--5", "Appendix~{B}", r"Section\;4",
])
def test_the_remaining_literal_forms_are_refused(tmp_path, monkeypatch, literal):
    monkeypatch.setattr(MV, "PAPER", tmp_path / "paper.tex")
    doc = f"%<*titlepage-only>\n%as shown in {literal}, we\n%</titlepage-only>\n"
    with pytest.raises(MV.MarkerError, match="auxref"):
        _gen(doc, "title")


@pytest.mark.parametrize("ref", ["\\ref*{x}", "\\ref {x}", "\\Cref*{x}", "\\vref{x}",
                                 "\\cpageref{x}", "\\subref{x}", "\\autopageref{x}",
                                 "\\labelcref{x}"])
def test_every_ref_command_and_spacing_is_refused(tmp_path, monkeypatch, ref):
    monkeypatch.setattr(MV, "PAPER", tmp_path / "paper.tex")
    doc = f"%<*titlepage-only>\n%as set out in Section~{ref}.\n%</titlepage-only>\n"
    with pytest.raises(MV.MarkerError, match="auxref"):
        _gen(doc, "title")


@pytest.mark.parametrize("line", ["Section~\\auxref{s} shows", "Tab.~\\auxref{s}",
                                  "the Equation of state", "Eq. of state"])
def test_the_wider_keywords_still_pass_prose_and_auxref(tmp_path, monkeypatch, line):
    _aux_tree(tmp_path, monkeypatch, {"s": "2"})
    _gen(f"%<*titlepage-only>\n%{line}\n%</titlepage-only>\n", "title")
