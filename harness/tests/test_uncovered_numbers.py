"""P0.6: the uncovered-numbers report -- "every number in the new text gets a check".

Without a tool that rule rests on the drafters' discipline, which is how today's
paper came to hold ~34 unchecked quantities (number_triage.md §c). The report
lists every number token in the main text and appendices that no current
`checks()` literal carries; `--strict-coverage` makes any of them fail the run
(the P2 exit gate). These tests are synthetic; the last one reads the real paper.
"""
import sys
from pathlib import Path

import pytest

import verify_numbers as V


HEAD = "\\begin{abstract}\nThe abstract has 0.111 in it.\n\\end{abstract}\n"
INTRO = "\\section{Introduction}\n"
BACK = ("\\section*{Declaration of competing interest}\nNone, grant 0.222.\n"
        "\\printcredits\n\\section*{Data availability}\nSee 0.333 at the site.\n")
BIB = "\\bibliographystyle{cas-model2-names}\n\\bibliography{refs2020}\n"


BIB_KEYS = ("Adesope2017", "Macina2023MathDial", "Zhao2026", "Smith2020")


def _write_bib(tmp_path, keys=BIB_KEYS):
    (tmp_path / "refs2020.bib").write_text(
        "".join(f"@article{{{k},\n  title = {{T}},\n}}\n" for k in keys), encoding="utf-8")


def _scan(tmp_path, body, literals=(), head=HEAD, tail=BACK + BIB + "\\end{document}\n",
          bib_keys=BIB_KEYS):
    p = tmp_path / "paper.tex"
    p.write_text(head + INTRO + body + "\n" + tail, encoding="utf-8")
    if bib_keys is not None:
        _write_bib(tmp_path, bib_keys)
    lits = literals if isinstance(literals, dict) else set(literals)
    try:
        return V.uncovered_numbers(p, lits)
    except V.ScopeNotAnchored as e:
        # Fix round 2 (N4): an anchored scope holding NO number now raises.
        # The exclusion tests ask only "did every number go?", so for them an
        # empty scope is []; the no-anchor refusal still raises.
        if "(0 number tokens in scope)" not in str(e):
            raise
        return []


def _toks(recs, kind=None):
    return [r.token for r in recs if kind is None or r.kind == kind]


# ------------------------------------------------- basics ----------------
def test_an_unchecked_number_in_the_body_is_reported_with_line_and_context(tmp_path):
    recs = _scan(tmp_path, "Mean leakage was 0.617 overall.")
    assert [(r.kind, r.token) for r in recs] == [("UNCOVERED", "0.617")]
    r = recs[0]
    lines = (tmp_path / "paper.tex").read_text().split("\n")
    assert "0.617" in lines[r.line - 1]
    assert "Mean leakage was 0.617" in r.context and len(r.context) <= 80


def test_a_number_some_check_computes_is_covered(tmp_path):
    assert _scan(tmp_path, "Mean leakage was 0.605.", {"$0.605$"}) == []


def test_coverage_is_loose_a_twin_literal_counts(tmp_path):
    """Covered = equals a token of ANY literal: a twin covers it. The report
    is about absence, not correctness (that is what the checks are for)."""
    assert _scan(tmp_path, "The interval ends at 0.619.", {"$[0.590, 0.619]$"}) == []


def test_coverage_is_by_token_never_by_substring(tmp_path):
    """0.61 is a substring of 0.619 and 2 of 2,016; neither is covered by it."""
    recs = _scan(tmp_path, "A gap of 0.61 over 2 panels.", {"$0.619$", "$2{,}016$"})
    assert _toks(recs) == ["0.61", "2"]


def test_tokens_are_normalised_like_the_abstract_lint(tmp_path):
    recs = _scan(tmp_path, "Across $2{,}016$ responses, $98\\%$ leak, kappa $.556$.",
                 {"$2{,}016$", "98\\%", "0.556"})
    assert recs == []


def test_identifiers_are_not_numbers_here_either(tmp_path):
    assert _scan(tmp_path, "RQ1 asks whether P0 and S3 differ in Python 3 or "
                           "ICC(2,1) on gpt-oss:120b and GPT-4o.") == []


def test_line_numbers_survive_full_line_comments(tmp_path):
    recs = _scan(tmp_path, "% a comment line\n%% another 0.999\nValue 0.444 here.")
    assert _toks(recs) == ["0.444"]
    lines = (tmp_path / "paper.tex").read_text().split("\n")
    assert "0.444" in lines[recs[0].line - 1]


# ------------------------------------------------- scope -----------------
def test_the_abstract_and_front_matter_are_out_of_scope(tmp_path):
    """abstract_lint owns the abstract; the scan starts at the Introduction."""
    assert "0.111" not in _toks(_scan(tmp_path, "Body."))


def test_back_matter_declarations_are_out_of_scope(tmp_path):
    toks = _toks(_scan(tmp_path, "Body 0.444."))
    assert "0.222" not in toks and "0.333" not in toks and "0.444" in toks


@pytest.mark.parametrize("title", ["Funding", "Ethics", "Data availability",
                                   "CRediT authorship contribution statement",
                                   "Declaration of generative AI and AI-assisted "
                                   "technologies in the writing process"])
def test_each_back_matter_section_is_excluded(tmp_path, title):
    tail = f"\\section*{{{title}}}\nIt cost 0.888 units.\n\\end{{document}}\n"
    assert _scan(tmp_path, "Body.", tail=tail) == []


def test_the_bibliography_is_out_of_scope(tmp_path):
    tail = ("\\begin{thebibliography}{9}\n\\bibitem{a} A. Smith, 2020, pp. "
            "111--124, vol. 37.\n\\end{thebibliography}\n" + BIB + "\\end{document}\n")
    assert _scan(tmp_path, "Body.", tail=tail) == []


@pytest.mark.parametrize("where", ["after the back matter", "after the bibliography",
                                   "before the back matter"])
def test_an_appendix_number_is_in_scope_wherever_the_appendix_sits(tmp_path, where):
    app = "\\appendix\n\\section{Extra tables}\nThe floor was 0.172 here.\n"
    tail = {"after the back matter": BACK + app + BIB,
            "after the bibliography": BACK + BIB + app,
            "before the back matter": app + BACK + BIB}[where] + "\\end{document}\n"
    assert _toks(_scan(tmp_path, "Body.", tail=tail)) == ["0.172"]


def test_appendix_text_before_its_first_section_is_in_scope(tmp_path):
    """\\appendix itself ends the back matter, not only the next \\section."""
    tail = BACK + "\\appendix\nThe appendices report 0.172 more.\n\\section{Extra}\nx\n" \
        + BIB + "\\end{document}\n"
    assert _toks(_scan(tmp_path, "Body.", tail=tail)) == ["0.172"]


def test_nothing_after_end_document(tmp_path):
    tail = BACK + BIB + "\\end{document}\nstray 0.777\n"
    assert _scan(tmp_path, "Body.", tail=tail) == []


# ------------------------------------------------- exclusions ------------
@pytest.mark.parametrize("snippet", [
    "as shown~\\citep[p.~12]{Smith2020}", "\\citet[see][ch.~3]{Lee2021}",
    "\\citealp{Kim2022}", "Section~\\ref{sec:4}", "Eq.~\\eqref{eq:2}",
    "\\autoref{tab:3}", "\\cref{fig:5}", "\\Cref{fig:6}", "\\pageref{p:7}",
    "\\nameref{s:8}", "\\label{sec:9}",
    "\\includegraphics[width=0.5\\textwidth, angle=90]{fig3.pdf}",
    "\\input{figures/fig2.tex}", "\\usepackage[margin=2cm]{geometry}",
    "\\hspace{2mm}", "\\vspace*{-3pt}", "\\setlength{\\tabcolsep}{4pt}",
    "\\url{https://ceur-ws.org/Vol-3662/paper25.pdf}",
    "\\href{https://doi.org/10.1/2}{the deposit}",
    "\\begin{tabular}{p{3.2cm}@{}lX@{}r}", "\\begin{tabularx}{0.9\\linewidth}{lX}",
    "\\begin{table}[t]", "\\\\[2pt]", "\\addlinespace[3pt]", "\\cmidrule(lr){2-4}",
    "\\cline{2-3}", "\\multicolumn{3}{c}{Header}", "\\multirow{2}{*}{Cell}",
    "\\rule{4.6cm}{1pt}", "\\resizebox{0.9\\textwidth}{!}{x}",
    "\\begin{minipage}{0.48\\linewidth}",
])
def test_each_exclusion_drops_its_numbers(tmp_path, snippet):
    assert _scan(tmp_path, f"Text {snippet} text.") == [], snippet


def test_multicolumn_content_is_kept(tmp_path):
    assert _toks(_scan(tmp_path, "\\multicolumn{3}{c}{Mean 0.777}")) == ["0.777"]


def test_a_table_cell_is_in_scope(tmp_path):
    body = ("\\begin{tabular}{lccc}\n\\toprule\nSolver & A & B & C \\\\\n\\midrule\n"
            "\\texttt{gemma4} & 1.00 & 0.096 & 0.777 \\\\\n\\bottomrule\n\\end{tabular}")
    assert _toks(_scan(tmp_path, body, {"1.00", "$0.096$"})) == ["0.777"]


# The real screen histogram (paper.tex fig:screen), abridged: options,
# coordinates and the coordinate block go; node text stays.
TIKZ = r"""\begin{figure}[t]
\centering
\begin{tikzpicture}
\begin{axis}[
  width=\linewidth, height=4.6cm,
  ybar interval, ymin=0, ymax=18,
  xmin=0, xmax=1.0,
  xtick={0,0.2,0.4,0.483,0.6,0.8,1.0},
  xticklabels={0,0.2,0.4,,0.6,0.8,1.0},
]
\addplot[fill=gray!35, draw=gray!60] coordinates {
  (0.0,3) (0.1,3) (0.2,3) (0.3,3) (0.4,2)
  (0.5,3) (0.6,2) (0.7,3) (0.8,1) (0.9,17) (1.0,0)};
\draw[dashed, thick] (axis cs:0.483,0) -- (axis cs:0.483,18);
\node[font=\scriptsize, anchor=south west] at (axis cs:0.50,13.1)
  {threshold $0.483$};
\node[font=\scriptsize, anchor=south] at (axis cs:0.25,14.0) {retained (14)};
\node[font=\scriptsize, anchor=south] at (axis cs:0.80,8.5) {discarded (26)};
\foreach \x in {1,2,5} {\draw (\x,0) -- (\x,1);}
\end{axis}
\end{tikzpicture}
\caption{Baseline identifiability of the 40 candidate items.}
\end{figure}"""


def test_inline_pgfplots_table_rows_are_excluded(tmp_path):
    """Rows of an inline `table {...}` are not parenthesised, so only the
    data-block rule removes them."""
    body = ("\\begin{tikzpicture}\n\\begin{axis}\n\\addplot table [x=a, y=b] {\n"
            "a b\n0.1 17\n0.2 23\n};\n\\end{axis}\n\\end{tikzpicture}")
    assert _scan(tmp_path, body) == []


def test_tikz_coordinates_and_options_go_and_node_text_stays(tmp_path):
    """Fix round 1 (I1 ruling): xticklabels are TYPESET, so they are in scope
    now; the tick positions (xtick) and every other option still go."""
    assert _toks(_scan(tmp_path, TIKZ)) == ["0", "0.2", "0.4", "0.6", "0.8", "1.0",
                                            "0.483", "14", "26", "40"]


# ------------------------------------------------- tags and years --------
def test_a_new_tag_marks_a_pending_check(tmp_path):
    recs = _scan(tmp_path, "Human mean 0.617. %% NEW: human_validation.py sample mean")
    assert [(r.kind, r.token) for r in recs] == [("NEW", "0.617")]


def test_a_lit_tag_marks_a_literature_number(tmp_path):
    recs = _scan(tmp_path, "Retention gains of 0.50 sd are typical~\\citep{Adesope2017}. "
                           "%% LIT: Adesope2017 p.672")
    assert [(r.kind, r.token) for r in recs] == [("INFO literature", "0.50")]


@pytest.mark.parametrize("tag", ["%% LIT: Adesope2017", "%% LIT: p.672",
                                 "%% NEW:", "%% NEW:   "])
def test_a_malformed_tag_falls_back_to_uncovered(tmp_path, tag):
    recs = _scan(tmp_path, f"A gain of 0.50 here. {tag}")
    assert [(r.kind, r.token) for r in recs] == [("UNCOVERED", "0.50")]
    assert "malformed" in recs[0].note


def test_a_tag_applies_to_every_uncovered_token_on_its_line(tmp_path):
    recs = _scan(tmp_path, "Gains 0.50 and 0.60~\\citep{Adesope2017}. %% LIT: Adesope2017 p.672")
    assert _toks(recs, "INFO literature") == ["0.50", "0.60"]


def test_a_tag_does_not_reach_another_line(tmp_path):
    recs = _scan(tmp_path, "Gain 0.50~\\citep{Adesope2017}. %% LIT: Adesope2017 p.672\n"
                           "And 0.60 here~\\citep{Adesope2017}.")
    assert [(r.kind, r.token) for r in recs] == [("INFO literature", "0.50"),
                                                 ("UNCOVERED", "0.60")]


@pytest.mark.parametrize("text,kind", [
    ("published in 2023 and 1987", "INFO year"),
    ("in 1900", "INFO year"), ("by 2099", "INFO year"),
])
def test_a_four_digit_year_is_info(tmp_path, text, kind):
    recs = _scan(tmp_path, text)
    assert recs and all(r.kind == kind for r in recs)


@pytest.mark.parametrize("text,tok", [
    ("Across $2{,}016$ responses", "2,016"),
    ("Across 2,016 responses", "2,016"),
    ("in 1899", "1899"), ("in 2100", "2100"), ("of 20231 items", "20231"),
])
def test_a_non_year_stays_uncovered(tmp_path, text, tok):
    assert [(r.kind, r.token) for r in _scan(tmp_path, text)] == [("UNCOVERED", tok)]


def test_a_new_tag_beats_a_year(tmp_path):
    """Fix round 1 (C1/M2a ruling): every token on a NEW line is NEW. The
    test this replaces pinned the opposite precedence."""
    recs = _scan(tmp_path, "In 2023 the mean was 0.617. %% NEW: results.json")
    assert [(r.kind, r.token) for r in recs] == [("NEW", "2023"), ("NEW", "0.617")]


# ------------------------------------------------- main() and the gate ---
def _run_main(tmp_path, monkeypatch, capsys, body, argv=(), checks=(), intro=INTRO):
    paper = tmp_path / "paper.tex"
    paper.write_text(HEAD + intro + body + "\n" + BACK + BIB + "\\end{document}\n",
                     encoding="utf-8")
    _write_bib(tmp_path)
    monkeypatch.setattr(V, "PAPER", paper)
    monkeypatch.setattr(V, "PDF", tmp_path / "absent.pdf")
    monkeypatch.setattr(V, "checks", lambda: list(checks))
    monkeypatch.setattr(V, "RETIRED", {})
    monkeypatch.setattr(V, "abstract_lint", lambda tex, lits: [])
    monkeypatch.setattr(sys, "argv", ["verify_numbers.py", *argv])
    rc = V.main()
    return rc, capsys.readouterr().out


def test_the_report_prints_but_does_not_fail_by_default(tmp_path, monkeypatch, capsys):
    rc, out = _run_main(tmp_path, monkeypatch, capsys,
                        "A 0.617 and 0.618. %% NEW: x\nAnd 0.619.")
    assert "UNCOVERED" in out and "0.619" in out and "NEW (check pending)" in out
    assert "coverage: 1 uncovered, 2 new" in out
    assert rc == 0
    assert "FAIL coverage" not in out


def test_strict_coverage_fails_on_uncovered_or_new(tmp_path, monkeypatch, capsys):
    rc, out = _run_main(tmp_path, monkeypatch, capsys,
                        "A 0.617. %% NEW: x\nAnd 0.619.", argv=["--strict-coverage"])
    assert "FAIL coverage: 1 uncovered, 1 new" in out
    assert rc == 1


def test_strict_coverage_passes_on_info_only(tmp_path, monkeypatch, capsys):
    rc, out = _run_main(tmp_path, monkeypatch, capsys,
                        "In 2023 gains were 0.50~\\citep{Adesope2017}. "
                        "%% LIT: Adesope2017 p.672", argv=["--strict-coverage"])
    assert "FAIL coverage" not in out
    assert rc == 0


def test_the_existing_summary_line_and_pending_count_are_untouched(
        tmp_path, monkeypatch, capsys):
    rc, out = _run_main(tmp_path, monkeypatch, capsys, "A 0.619.",
                        checks=[("x", "0.619", "n")])
    assert "1 verified, 0 missing, 3 pending, 0 stale, 0 retired" in out


def test_main_uses_the_current_literals_not_a_second_checks_call(
        tmp_path, monkeypatch, capsys):
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return [("x", "0.619", "n")]

    paper = tmp_path / "paper.tex"
    paper.write_text(INTRO + "A 0.619.\n\\end{document}\n", encoding="utf-8")
    monkeypatch.setattr(V, "PAPER", paper)
    monkeypatch.setattr(V, "PDF", tmp_path / "absent.pdf")
    monkeypatch.setattr(V, "checks", counting)
    monkeypatch.setattr(sys, "argv", ["verify_numbers.py"])
    V.main()
    assert calls["n"] == 1
    assert "0 uncovered" in capsys.readouterr().out


# ------------------------------------------------- the real paper --------
def test_the_real_paper_report_points_at_real_lines():
    if not V.PAPER.exists():
        pytest.skip("needs the private tree")
    recs = V.uncovered_numbers(V.PAPER)
    lines = V.PAPER.read_text(encoding="utf-8").split("\n")
    intro = next(i for i, l in enumerate(lines, 1) if l.startswith("\\section{Introduction}"))
    assert recs, "today's paper has unchecked numbers (number_triage.md §c)"
    for r in recs:
        assert intro <= r.line <= len(lines)
        assert r.kind in ("UNCOVERED", "NEW", "INFO literature", "INFO year")



# =========================================================== FIX ROUND 1 ====
# C1 (ruling): a well-formed %% NEW tag beats coverage AND the year rule. A
# drafter's "this number has no check yet" must not be overruled by an
# unrelated check that happens to carry the same string; removing the tag is
# the integrator's deliberate act after writing the check.
def test_a_new_tag_beats_coverage_and_names_the_twin(tmp_path):
    recs = _scan(tmp_path, "Half-width $\\pm 0.090$. %% NEW: new_numbers.py -> 0.0901",
                 {"P1-P0 deletion range": "$[-0.138, -0.090]$"})
    assert [(r.kind, r.token) for r in recs] == [("NEW", "0.090")]
    assert "P1-P0 deletion range" in recs[0].note


def test_a_new_tag_beats_coverage_with_a_plain_literal_set(tmp_path):
    recs = _scan(tmp_path, "Half-width $\\pm 0.090$. %% NEW: x", {"$[-0.138, -0.090]$"})
    assert [(r.kind, r.token) for r in recs] == [("NEW", "0.090")]
    assert "[-0.138, -0.090]" in recs[0].note


def test_a_new_tagged_four_digit_count_is_new(tmp_path):
    recs = _scan(tmp_path, "Over 2016 replies. %% NEW: results -> n")
    assert [(r.kind, r.token) for r in recs] == [("NEW", "2016")]


# C2 (ruling): the scope must be anchored, or the gate refuses.
@pytest.mark.parametrize("heading", [
    "\\section{Introduction}\n", "\\section[Intro]{Introduction}\n",
    "\\section{\\label{sec:intro}Introduction}\n", "\\section*{Introduction}\n",
])
def test_each_accepted_heading_form_anchors_the_scope(tmp_path, heading):
    p = tmp_path / "paper.tex"
    p.write_text(HEAD + heading + "Mean leakage was 0.617.\n" + BACK + BIB, encoding="utf-8")
    cov = V._coverage_scan(p.read_text(encoding="utf-8"), set())
    assert cov.refusal is None
    assert [(r.kind, r.token) for r in cov.records] == [("UNCOVERED", "0.617")]


@pytest.mark.parametrize("heading", [
    "\\section{Introduction and motivation}\n", "\\section{1. Introduction}\n",
    "\\section{Background}\n", "",
    # P0.6 fix round 2: the label INSIDE the title stays refused (N3 ruling), and a
    # commented-out heading is no anchor (N5, X4)
    "\\section{Introduction\\label{sec:intro}}\n", "% \\section{Introduction}\n",
])
def test_every_other_heading_is_refused_not_scanned_as_empty(tmp_path, heading):
    p = tmp_path / "paper.tex"
    p.write_text(HEAD + heading + "Mean leakage was 0.617.\n" + BACK + BIB, encoding="utf-8")
    cov = V._coverage_scan(p.read_text(encoding="utf-8"), set())
    assert cov.refusal == "no \\section{Introduction}"
    with pytest.raises(V.ScopeNotAnchored):
        V.uncovered_numbers(p, set())


def test_a_scope_with_no_number_tokens_is_refused(tmp_path):
    p = tmp_path / "paper.tex"
    p.write_text(HEAD + INTRO + "No digits here.\n" + BACK + BIB, encoding="utf-8")
    assert V._coverage_scan(p.read_text(encoding="utf-8"), set()).refusal == \
        "0 number tokens in scope"


def test_an_unanchored_scope_fails_strict_and_is_printed_by_default(
        tmp_path, monkeypatch, capsys):
    head = "\\section{Introduction and motivation}\n"
    rc, out = _run_main(tmp_path, monkeypatch, capsys, "Mean 0.617 and 0.618.", intro=head)
    assert "FAIL coverage: scope not anchored (no \\section{Introduction})" in out
    assert rc == 0, "the default exit status is unchanged by the report"
    rc, out = _run_main(tmp_path, monkeypatch, capsys, "Mean 0.617 and 0.618.",
                        intro=head, argv=["--strict-coverage"])
    assert "FAIL coverage: scope not anchored (no \\section{Introduction})" in out
    assert rc == 1


def test_a_zero_token_scope_fails_strict(tmp_path, monkeypatch, capsys):
    rc, out = _run_main(tmp_path, monkeypatch, capsys, "No digits.",
                        argv=["--strict-coverage"])
    assert "FAIL coverage: scope not anchored (0 number tokens in scope)" in out
    assert rc == 1


# I2: NEW alone must fail the strict gate.
def test_strict_coverage_fails_on_a_single_new_token(tmp_path, monkeypatch, capsys):
    rc, out = _run_main(tmp_path, monkeypatch, capsys, "A 0.617. %% NEW: x",
                        argv=["--strict-coverage"])
    assert "FAIL coverage: 0 uncovered, 1 new" in out
    assert rc == 1


# I1 (ruling): typeset TikZ/pgfplots text stays in scope.
TZ = "\\begin{tikzpicture}\n%s\n\\end{tikzpicture}"


@pytest.mark.parametrize("node,want", [
    ("\\node at (0,0) {replies (2{,}016)};", ["2,016"]),
    ("\\node at (0,0) {(14 items, 26 discarded)};", ["14", "26"]),
    ("\\node at (0,0) {Readers\\\\(2 models, 3 draws)};", ["2", "3"]),
    ("\\node at (0,0) {(ratio: 0.5)};", ["0.5"]),
    ("\\node[box] (r) {reply\\\\(2 models $\\times$ 3 draws)};", ["2", "3"]),
    ("\\node at (0,0) {\\small [n = 40]};", ["40"]),
    ("\\node[label=above:{40 items}] at (0,0) {x};", ["40"]),
    ("\\node[pin=60:{12 calls}] at (1,2) {x};", ["12"]),
    ("\\draw (0,0) -- (1,1) node[right] {0.617};", ["0.617"]),
    # P0.6 fix round 2 (N1): node text that OPENS with an environment is still text
    ("\\node at (0,0) {\\begin{tabular}{c}reply\\\\(2 models, 3 draws)\\end{tabular}};",
     ["2", "3"]),
    ("\\node[align=center] at (0,0) {\\begin{tabular}{c}Bank A\\\\(ratio: 0.5)\\end{tabular}};",
     ["0.5"]),
    ("\\node[draw] {\\begin{minipage}{2cm}fell 5.3pt\\end{minipage}};", ["5.3pt"]),
])
def test_tikz_node_text_numbers_are_in_scope(tmp_path, node, want):
    assert _toks(_scan(tmp_path, TZ % node)) == want


@pytest.mark.parametrize("opt,want", [
    ("\\begin{axis}[xlabel={Share of 40 items}]\n\\end{axis}", ["40"]),
    ("\\begin{axis}[title={Bank A, 14 items}]\n\\end{axis}", ["14"]),
    ("\\begin{axis}[ylabel={Calls (k = 20)}, ymax=18]\n\\end{axis}", ["20"]),
    ("\\begin{axis}[legend entries={P0 0.705, P1 0.578}]\n\\end{axis}", ["0.705", "0.578"]),
    ("\\begin{axis}[yticklabels={0.1,0.3}, ytick={1,3}]\n\\end{axis}", ["0.1", "0.3"]),
    ("\\begin{axis}[nodes near coords={n = 27}, width=4cm]\n\\end{axis}", ["27"]),
    # P0.6 fix round 2 (N5, X13): the z and extra-tick keys are typeset too
    ("\\begin{axis}[zlabel={Share of 40 items}]\n\\end{axis}", ["40"]),
    ("\\begin{axis}[zticklabels={0.1,0.3}, ztick={1,3}]\n\\end{axis}", ["0.1", "0.3"]),
    ("\\begin{axis}[extra x ticks={0.483}, extra x tick labels={0.483}]\n\\end{axis}", ["0.483"]),
    ("\\begin{axis}[extra y ticks={12}, extra y tick labels={12 calls}]\n\\end{axis}", ["12"]),
])
def test_pgfplots_typeset_option_values_are_in_scope(tmp_path, opt, want):
    assert _toks(_scan(tmp_path, TZ % opt)) == want


@pytest.mark.parametrize("code", [
    "\\draw (0,0) circle (2pt);", "\\fill[red!35] (0,0) rectangle (2,3);",
    "\\path[draw, line width=0.4pt] (0,0) -- (3,4);",
    "\\filldraw[gray!60] (1,1) circle (3pt);", "\\coordinate[label=left:{}] (A) at (1,2);",
    "\\draw (0,0) -- (30:1);",
])
def test_tikz_code_level_coordinates_lengths_and_options_still_go(tmp_path, code):
    assert _scan(tmp_path, TZ % code) == []


def test_options_after_an_unlisted_tikz_command_are_reported(tmp_path):
    """I1 ruling: `[` is an option list only after \\node, \\draw, \\path,
    \\addplot, \\coordinate, \\fill, \\filldraw, \\matrix, \\begin{...} or a
    TikZ keyword. Anywhere else its numbers stay in scope -- the loud
    direction (here a shading angle the integrator can read and dismiss)."""
    assert _toks(_scan(tmp_path, TZ % "\\shade[shading angle=45] (0,0) rectangle (1,1);")) == ["45"]


# M1 (ruling): loose twins stay, but every covered token can be listed.
def test_list_covered_names_every_pinning_check(tmp_path, monkeypatch, capsys):
    rc, out = _run_main(tmp_path, monkeypatch, capsys, "A 0.605 and 0.617.",
                        argv=["--list-covered"],
                        checks=[("mean leak", "0.605", "n"), ("twin", "$[0.590, 0.605]$", "n")])
    lines = [l for l in out.splitlines() if l.startswith("COVERED")]
    assert len(lines) == 1
    assert "0.605" in lines[0] and "mean leak" in lines[0] and "twin" in lines[0]
    src = (tmp_path / "paper.tex").read_text().split("\n")
    assert "0.605" in src[5 - 1] and "l.5 " in lines[0]


def test_covered_tokens_are_not_listed_by_default(tmp_path, monkeypatch, capsys):
    rc, out = _run_main(tmp_path, monkeypatch, capsys, "A 0.605.",
                        checks=[("mean leak", "0.605", "n")])
    assert "COVERED" not in out


# M2 (rulings): a token inside $...$ is never a year; under strict a year
# fails unless its line cites or carries %% YEAR.
@pytest.mark.parametrize("text", ["$1994$ replies", "over $2{,}016$ and $2016$"])
def test_a_token_in_math_is_never_a_year(tmp_path, text):
    assert all(r.kind == "UNCOVERED" for r in _scan(tmp_path, text))


def test_a_year_in_math_spanning_lines_is_not_a_year(tmp_path):
    recs = _scan(tmp_path, "the bound $p <\n2016$ held")
    assert [(r.kind, r.token) for r in recs] == [("UNCOVERED", "2016")]


@pytest.mark.parametrize("text,fails", [
    ("over 2016 replies", True),
    ("as published in 1987~\\citep{Smith2020}", False),
    ("written in 1987. %% YEAR", False),
])
def test_under_strict_a_year_needs_a_citation_or_a_year_tag(
        tmp_path, monkeypatch, capsys, text, fails):
    rc, out = _run_main(tmp_path, monkeypatch, capsys, text, argv=["--strict-coverage"])
    assert ("FAIL coverage" in out) == fails, out
    assert rc == (1 if fails else 0)
    if fails:
        assert "FAIL coverage: 0 uncovered, 0 new, 1 year without \\cite or %% YEAR" in out


def test_by_default_a_year_does_not_fail(tmp_path, monkeypatch, capsys):
    rc, out = _run_main(tmp_path, monkeypatch, capsys, "over 2016 replies")
    assert "INFO year" in out and "FAIL coverage" not in out and rc == 0


# M3: tag hygiene.
def test_a_lit_tag_may_carry_trailing_text_after_the_page(tmp_path):
    recs = _scan(tmp_path, "Telling rates near 0.57~\\citep{Macina2023MathDial}. "
                           "%% LIT: Macina2023MathDial p.5610 (Fig. 5, Telling@10)")
    assert [(r.kind, r.token) for r in recs] == [("INFO literature", "0.57")]


def test_a_lit_page_range_is_accepted(tmp_path):
    recs = _scan(tmp_path, "A rate of 0.57~\\citep{Zhao2026}. %% LIT: Zhao2026 pp. 30604-30605")
    assert [(r.kind, r.token) for r in recs] == [("INFO literature", "0.57")]


@pytest.mark.parametrize("tag", ["%% NEW results.json", "%% new: results.json",
                                 "%% lit: Adesope2017 p.1", "% Year 1987",
                                 "%%NEW(x) results.json"])
def test_a_near_miss_tag_is_flagged_not_ignored(tmp_path, tag):
    recs = _scan(tmp_path, f"A gain of 0.57 here. {tag}")
    assert [(r.kind, r.token) for r in recs] == [("UNCOVERED", "0.57")]
    assert "near-miss" in recs[0].note


def test_a_new_tag_may_carry_a_qualifier(tmp_path):
    """The drafts write `%% NEW (<what>): <source>`; it is unambiguous, so it
    is well formed -- and under C1 every token on the line is NEW."""
    recs = _scan(tmp_path, "It fell from 0.127 to 0.106. "
                           "%% NEW (adjusted estimate only): results.json",
                 {"P1-P0": "$-0.127$"})
    assert [(r.kind, r.token) for r in recs] == [("NEW", "0.127"), ("NEW", "0.106")]
    assert "adjusted estimate only" in recs[1].note and "P1-P0" in recs[0].note


def test_a_near_miss_new_tag_beats_coverage_too(tmp_path):
    """Fail-safe: a drafter who MEANT to tag a number new must not be
    overruled by a twin because the tag is mistyped."""
    recs = _scan(tmp_path, "Half-width 0.090. %% new: x", {"$[-0.138, -0.090]$"})
    assert [(r.kind, r.token) for r in recs] == [("UNCOVERED", "0.090")]
    assert "near-miss" in recs[0].note


def test_a_lit_key_missing_from_the_bibliography_is_uncovered(tmp_path):
    recs = _scan(tmp_path, "Gain 0.57~\\citep{NoSuchKey9999}. %% LIT: NoSuchKey9999 p.1")
    assert [(r.kind, r.token) for r in recs] == [("UNCOVERED", "0.57")]
    assert "NoSuchKey9999" in recs[0].note and "bibliography" in recs[0].note


def test_a_lit_tag_without_a_bibliography_file_is_uncovered(tmp_path):
    recs = _scan(tmp_path, "Gain 0.57~\\citep{Adesope2017}. %% LIT: Adesope2017 p.672",
                 bib_keys=None)
    assert [(r.kind, r.token) for r in recs] == [("UNCOVERED", "0.57")]
    assert "bibliography" in recs[0].note


def test_a_lit_tag_on_a_line_that_cites_nothing_is_uncovered(tmp_path):
    """The reviewer's H3: our own result beside a LIT tag must not be hidden."""
    recs = _scan(tmp_path, "Ours is 0.617 against 0.50 there. %% LIT: Adesope2017 p.672")
    assert [(r.kind, r.token) for r in recs] == [("UNCOVERED", "0.617"), ("UNCOVERED", "0.50")]
    assert all("\\cite" in r.note for r in recs)


def test_the_bibliography_is_found_from_the_bibliography_command(tmp_path):
    """BIB names refs2020; a bibliography.bib beside it must not be used."""
    (tmp_path / "bibliography.bib").write_text("@article{Other1,\n}\n", encoding="utf-8")
    recs = _scan(tmp_path, "Gain 0.57~\\citep{Adesope2017}. %% LIT: Adesope2017 p.672")
    assert [(r.kind, r.token) for r in recs] == [("INFO literature", "0.57")]


# M4: no global key=value or length rule; '[' after a newline is text;
# a \cite prenote keeps its numbers, a page locator goes.
@pytest.mark.parametrize("prose,want", [
    ("a Wilson half-width = 0.090 at that size", ["0.090"]),
    ("with width=0.12 the band is narrow", ["0.12"]),
    ("search depth = 3 turns", ["3"]),
    ("leakage fell 5.3pt under P1", ["5.3pt"]),
    ("a 2in difference", ["2in"]),
])
def test_prose_numbers_survive_the_length_rules(tmp_path, prose, want):
    assert _toks(_scan(tmp_path, prose)) == want


@pytest.mark.parametrize("body", [
    "\\begin{itemize}\n[0.617 of replies leak]\n\\end{itemize}",
    "row a \\\\\n[0.617] next",
    "as \\citep[reporting 0.617;][]{Zhao2026} show",
])
def test_brackets_across_a_newline_and_cite_prenotes_are_text(tmp_path, body):
    assert _toks(_scan(tmp_path, body)) == ["0.617"]


@pytest.mark.parametrize("snippet", [
    "\\citep[p.~12]{Smith2020}", "\\citep[pp.~3--5]{Smith2020}", "\\citep[p. 672]{S}",
    "\\citet[see][ch.~3]{Lee2021}", "\\citep[Fig.~2]{Smith2020}", "\\citep[Table 4]{S}",
    "\\begin{tabular}{*{3}{c}}", "\\begin{table}[p{2}]", "row \\\\[3pt] next",
    "\\begin{minipage}[t]{0.5\\linewidth}",
])
def test_locators_specs_and_options_holding_digits_still_go(tmp_path, snippet):
    assert _scan(tmp_path, f"Text {snippet} text. 0.617") and \
        _toks(_scan(tmp_path, f"Text {snippet} text. 0.617")) == ["0.617"]


# M7: back matter is matched by its FULL title, and each boundary holds alone.
@pytest.mark.parametrize("title", ["Ethics of answer leakage", "Funding models and tutors",
                                   "References in tutor replies"])
def test_a_body_section_with_a_back_matter_prefix_is_in_scope(tmp_path, title):
    body = f"\\section{{{title}}}\nThe mean was 0.617.\n\\section{{Discussion}}\nok"
    assert _toks(_scan(tmp_path, body)) == ["0.617"]


def test_a_section_after_the_back_matter_is_in_scope_again(tmp_path):
    tail = ("\\section*{Funding}\nNone 0.888.\n\\section{Late material}\nIt was 0.172.\n"
            "\\end{document}\n")
    assert _toks(_scan(tmp_path, "Body.", tail=tail)) == ["0.172"]


def test_printcredits_alone_starts_the_back_matter(tmp_path):
    tail = "\\printcredits\nCRediT text 0.888\n\\end{document}\n"
    assert _toks(_scan(tmp_path, "Body 0.617.", tail=tail)) == ["0.617"]


def test_end_document_alone_ends_the_scope(tmp_path):
    tail = "\\end{document}\nstray 0.777\n"
    assert _toks(_scan(tmp_path, "Body 0.617.", tail=tail)) == ["0.617"]


def test_a_back_matter_prefix_section_does_not_end_the_main_text_count():
    tex = ("\\section{Introduction}\nWords here.\n\\section{Ethics of answer leakage}\n"
           "More words.\n\\section*{Funding}\nNone.\n")
    txt = ("1. Introduction\nWords here.\n2. Ethics of answer leakage\nMore words.\n"
           "Funding\nNone.")
    n, note = V._count_main_text(txt, tex)
    assert n == 2 + 2 + 5 + 2, note



# =========================================================== FIX ROUND 2 ====
# N1: a brace group is CODE only when it opens with a drawing command or a
# TikZ/pgfplots environment; node text opening with tabular/minipage is TEXT
# (the three strings are in test_tikz_node_text_numbers_are_in_scope). The
# boundary: a \foreach body opening with \begin{scope} is still code.
def test_a_foreach_body_opening_with_a_scope_is_still_code(tmp_path):
    body = TZ % ("\\foreach \\x in {1,2} {\\begin{scope}[xshift=12mm]"
                 "\\draw (0,0) -- (1.5,2);\\end{scope}}")
    assert _scan(tmp_path, body) == []


# N2: `node contents` is typeset; a slash-paired \foreach list stays in scope
# (the loud direction: its values are often node text, so ALL of them are
# reported, positions included).
def test_node_contents_is_typeset(tmp_path):
    assert _toks(_scan(tmp_path, TZ % "\\node[node contents={40 items}];")) == ["40"]


@pytest.mark.parametrize("loop,want", [
    ("\\foreach \\x/\\l in {0/0.25, 1/0.5} {\\node at (\\x,0) {\\l};}",
     ["0", "0.25", "1", "0.5"]),
    ("\\foreach \\x / \\l / \\n in {0/a/12} {\\node at (\\x,0) {\\l\\n};}", ["0", "12"]),
    # unpaired lists still go
    ("\\foreach \\x in {0.25, 0.5} {\\draw (\\x,0) circle (2pt);}", []),
    ("\\foreach \\x [count=\\i] in {1,2,5} {\\draw (\\x,0) -- (\\x,1);}", []),
])
def test_a_slash_paired_foreach_list_is_in_scope(tmp_path, loop, want):
    assert _toks(_scan(tmp_path, TZ % loop)) == want


# N3: the anchor is the FIRST \section after \begin{document} (comment-
# stripped); a later \section*{Introduction} -- a quoted prompt in an
# appendix -- must not become the anchor and drop the body. ONE rule for the
# coverage scan and the word count.
DECOY = ("Body result 0.617 and 0.618.\n\\appendix\n\\section{Prompts}\n"
         "\\section*{Introduction}\nLate value 0.999.\n")


@pytest.mark.parametrize("heading", ["\\section{Introduction and motivation}\n",
                                     "\\section{Introduction\\label{sec:intro}}\n"])
def test_a_late_decoy_introduction_is_not_the_anchor(tmp_path, heading):
    tex = "\\begin{document}\n" + HEAD + heading + DECOY + BACK + BIB + "\\end{document}\n"
    p = tmp_path / "paper.tex"
    p.write_text(tex, encoding="utf-8")
    assert V._coverage_scan(tex, set()).refusal == "no \\section{Introduction}"
    with pytest.raises(V.ScopeNotAnchored):
        V.uncovered_numbers(p, set())
    pdf = ("Title\n\f1. Introduction and motivation\nBody result 0.617 and 0.618.\n"
           "Appendix A. Prompts\nIntroduction\nLate value 0.999.\n"
           "Declaration of competing interest\nNone.")
    n, note = V._count_main_text(pdf, tex)
    assert n is None and "Introduction" in note
    # main_text_words asks the same anchor before it asks about the build
    n, note = V.main_text_words(tex, pdf=tmp_path / "absent.pdf")
    assert n is None and "first \\section" in note


def test_a_section_in_the_preamble_is_not_the_first_section(tmp_path):
    tex = ("\\newcommand{\\appsec}{\\section{Prompts}}\n\\begin{document}\n" + HEAD
           + INTRO + "Mean 0.617.\n" + BACK + BIB + "\\end{document}\n")
    cov = V._coverage_scan(tex, set())
    assert cov.refusal is None and [r.token for r in cov.records] == ["0.617"]


def test_the_refusal_says_how_to_write_the_heading(tmp_path, monkeypatch, capsys):
    """N3 ruling: \\section{Introduction\\label{...}} stays refused; the
    message tells the integrator the accepted form."""
    head = "\\section{Introduction\\label{sec:intro}}\n"
    rc, out = _run_main(tmp_path, monkeypatch, capsys, "Mean 0.617.", intro=head)
    line = next(l for l in out.splitlines() if l.startswith("FAIL coverage: scope not anchored"))
    assert "(no \\section{Introduction})" in line
    assert "\\section{Introduction}\\label{...}" in line and "first \\section" in line
    with pytest.raises(V.ScopeNotAnchored, match=r"\\section\{Introduction\}\\label"):
        V.uncovered_numbers(tmp_path / "paper.tex", set())


# N4: the documented API refuses an empty scope too, not only a missing anchor.
def test_uncovered_numbers_refuses_a_scope_with_no_number_tokens(tmp_path):
    p = tmp_path / "paper.tex"
    p.write_text(HEAD + INTRO + "No digits here.\n" + BACK + BIB, encoding="utf-8")
    with pytest.raises(V.ScopeNotAnchored, match="0 number tokens in scope"):
        V.uncovered_numbers(p, set())


# N5: the rulings' rules that no test pinned (each kills a re-review mutant).
def test_a_year_on_a_near_miss_line_is_uncovered(tmp_path):            # X1
    recs = _scan(tmp_path, "In 2016 we found it. %% new: x")
    assert [(r.kind, r.token) for r in recs] == [("UNCOVERED", "2016")]
    assert "near-miss" in recs[0].note


@pytest.mark.parametrize("text", ["\\(2016\\) replies", "\\[2016\\]",      # X5
                                  "$$2016$$"])                             # X6
def test_display_and_paren_maths_are_maths_for_the_year_rule(tmp_path, text):
    assert [(r.kind, r.token) for r in _scan(tmp_path, text)] == [("UNCOVERED", "2016")]


def test_an_escaped_dollar_does_not_open_maths(tmp_path):                 # X7
    recs = _scan(tmp_path, "cost \\$12 for 2016 calls")
    assert ("INFO year", "2016") in [(r.kind, r.token) for r in recs]


def test_list_covered_lists_every_occurrence_of_a_twin(                   # X9
        tmp_path, monkeypatch, capsys):
    rc, out = _run_main(tmp_path, monkeypatch, capsys, "A 0.605 here.\nAnd 0.605 there.",
                        argv=["--list-covered"], checks=[("mean leak", "0.605", "n")])
    lines = [l for l in out.splitlines() if l.startswith("COVERED")]
    assert len(lines) == 2 and "l.5 " in lines[0] and "l.6 " in lines[1]


def test_a_cite_inside_the_comment_is_not_a_citation(tmp_path):           # X15
    recs = _scan(tmp_path, "Ours 0.617. %% LIT: Smith2020 p.3 \\citep{Smith2020}")
    assert [(r.kind, r.token) for r in recs] == [("UNCOVERED", "0.617")]
    recs = _scan(tmp_path, "Over 2016 replies. % as in \\citep{Smith2020}")
    assert [(r.kind, r.token, r.strict_fail) for r in recs] == [("INFO year", "2016", True)]


# N6: one definition of "a citation command" for both cite rules. \nocite is
# an excluded-argument command (its keys are not quantities) but prints no
# citation, so it does not make a line "cite" (M2c, M3c).
@pytest.mark.parametrize("name", ["cite", "citep", "citet", "Citet", "citealp",
                                  "citeauthor", "citeyearpar", "Cite"])
def test_both_cite_rules_agree_on_what_a_citation_command_is(name):
    assert V._CITE_RE.match(name)
    assert V._CITE_CMD_RE.search(f"as in \\{name}{{Smith2020}}")
    assert V._CITE_CMD_RE.search(f"as in \\{name}*[p.~3]{{Smith2020}}")


def test_nocite_keys_are_excluded_but_nocite_is_not_a_citation(tmp_path):
    assert V._CITE_RE.match("nocite") and not V._CITE_CMD_RE.search("\\nocite{Smith2020}")
    recs = _scan(tmp_path, "Over 2016 replies.\\nocite{Smith2020}")
    assert [(r.kind, r.token, r.strict_fail) for r in recs] == [("INFO year", "2016", True)]
