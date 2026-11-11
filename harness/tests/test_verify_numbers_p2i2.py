"""P2-I2 (2026-11-11): the helpers that render the integrated C&E manuscript's
numbers, and the Table 3 row check that must hold in text AND maths form.

The rendering helpers decide whether a sentence is TRUE, not only how it is
spelled: a bound stated as "no more than X" is checked by ceiling, an "at
least X" by floor, an "about <fraction>" by the nearest fraction word. Each
is pinned here on cases where rounding would give the other answer.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import verify_numbers as V  # noqa: E402


# ----------------------------------------------------------- bounds -------
def test_a_no_more_than_bound_is_a_ceiling_not_a_rounding():
    # 0.000661 rounds to 0.00066, but "moves by no more than 0.00066" is false
    assert V._ceil_at(0.000661, 5) == "0.00067"
    assert V._ceil_at(0.0324, 2) == "0.04"
    assert V._ceil_at(0.03, 2) == "0.03"            # an exact value stays itself
    assert V._ceil_at(1.3374, 1) == "1.4"


def test_an_at_least_claim_is_a_floor_not_a_rounding():
    # 63.095 rounds to 63.1, but "at least 63.1%" is false
    assert V._floor_at(63.0952, 1) == "63.0"
    assert V._floor_at(54.733, 1) == "54.7"
    assert V._floor_at(54.7, 1) == "54.7"
    assert V._floor_pct_at_ten(0.908) == "90"
    assert V._floor_pct_at_ten(0.899) == "80"


# -------------------------------------------------- 'about <fraction>' ----
@pytest.mark.parametrize("x, word", [
    (0.1797, "a fifth"), (0.3051, "three tenths"), (0.6587, "two thirds"),
    (0.5051, "half"), (9 / 27, "a third"),
])
def test_about_is_the_nearest_fraction_word(x, word):
    assert V._about_fraction(x) == word


@pytest.mark.parametrize("x", [0.03, 0.97,
                               # 55.0%: 0.05 from both 1/2 and 3/5 -- not
                               # "about" either within the tolerance first set
                               0.55])
def test_about_refuses_when_no_word_is_near(x):
    assert V._about_fraction(x) is None


def test_the_about_tolerance_was_not_loosened():
    assert V._ABOUT_TOL == 0.025


def test_a_false_claim_fails_instead_of_pending(monkeypatch):
    """Flip one verdict in the data: the claim's check must become MISSING
    (a literal the paper cannot hold), never PENDING (None, exit 0)."""
    if not V.PAPER.exists():
        pytest.skip("needs the private tree")
    import copy
    real = V._data

    def flipped(name):
        d = real(name)
        if name == "results_bankA_ol.json":
            d = copy.deepcopy(d)
            d["families"]["R2_mitigation"]["contrasts"]["P1-P0"]["reject"] = False
        return d

    monkeypatch.setattr(V, "_data", flipped)
    lab = "RQ2: P1-P0 passes both tests"
    got = {l: lit for l, lit, _ in V._ce_checks(None)}
    assert got[lab] is not None and got[lab] not in V._paper_body()


# ------------------------------------------------------- formatting -------
@pytest.mark.parametrize("p, s", [
    (1.46e-21, "<0.001"), (0.0009, "<0.001"), (0.043448, "0.043"),
    (0.067658, "0.068"), (0.41375, "0.41"), (0.842, "0.84"), (0.11883, "0.12"),
])
def test_holm_p_follows_the_table_convention(p, s):
    assert V._p_table(p) == s


@pytest.mark.parametrize("reject, ci, dagger", [
    (True, (-0.083, 0.014), True),     # Holm rejects, interval includes zero
    (True, (-0.231, -0.040), False),
    (False, (-0.025, -0.004), True),   # Holm does not, interval excludes zero
    (False, (-0.050, 0.003), False),
])
def test_the_dagger_marks_disagreement(reject, ci, dagger):
    assert V._disagree(reject, ci) is dagger


def test_percentage_intervals_print_100_bare():
    assert V._pct_ci((0.9425, 1.0)) == "$[94.2, 100]$"
    assert V._pct_ci((0.8591, 0.9733)) == "$[85.9, 97.3]$"
    assert V._pct(0.9801587) == "98.0\\%"


def test_the_interval_level_comes_from_z():
    assert V._level_from_z(1.959963984540054) == 95
    assert V._level_from_z(1.96) == 95


def test_tex_int_and_signed_interval():
    assert V._tex_int(5904) == "5{,}904"
    assert V._signed_ci(-0.053297, 0.00079) == "$[-0.053, +0.001]$"


# ------------------------------------------------------ code constants ----
def test_the_verbatim_option_length_is_read_from_the_checker():
    n = V._code_threshold_checker()
    src = (V.ROOT / "harness" / "checker.py").read_text(encoding="utf-8")
    assert isinstance(n, int) and f"len(key_text) >= {n}" in src


def test_the_retained_item_target_is_read_from_size_bank_b():
    n = V._code_size_bank_b_target()
    src = (V.ROOT / "harness" / "size_bank_b.py").read_text(encoding="utf-8")
    assert isinstance(n, int)
    assert re.search(rf'"--target", type=int, default={n}\b', src)


def test_a_spec_fact_is_read_from_its_own_section():
    if not V._SPEC.exists():
        pytest.skip("needs the private tree")
    sec = V._spec_section("4.21")
    assert sec.startswith("### 4.21 ") and "### 4.22" not in sec
    assert V._spec_fact(r"Wilson half-width at p = ([0-9.]+)", "4.21") is not None
    assert V._spec_fact(r"Wilson half-width at p = ([0-9.]+)", "4.43") is None


def test_the_screen_band_is_one_computation():
    rows = [{"baseline": b, "passes_headroom": b < 0.483, "k_per_solver": 20,
             "per_solver": {"a": {}, "b": {}, "c": {}}}
            for b in (0.10, 0.45, 0.50, 0.90)]
    band, k_tot = V._screen_band(rows)
    assert k_tot == 60 and abs(band - 3.0) < 1e-9


# --------------------------------------------------------- Table 3 --------
_BODY = r"""\begin{tabularx}{\linewidth}{lrrrl}
\multicolumn{9}{@{}l}{\emph{(a) Within each bank}} \\
P1$-$P0 & ROW_A \\
\end{tabularx}
\begin{tabularx}{\linewidth}{lrrrl}
\multicolumn{5}{@{}l}{\emph{(b) Both banks pooled (41 items)}} \\
P1$-$P0 & ROW_B \\
\end{tabularx}"""
_CELLS = ["-0.127", "0.013", "0.043^dag", "[-0.231, -0.040]"]


@pytest.mark.parametrize("row", [
    r"-0.127 & 0.013 & 0.043\rlap{$^{\dagger}$} & {[-0.231, -0.040]}",
    r"$-0.127$ & $0.013$ & $0.043$\rlap{$^{\dagger}$} & $[-0.231, -0.040]$",
])
def test_a_table3_row_matches_in_text_and_in_maths_form(row):
    body = _BODY.replace("ROW_A", row).replace("ROW_B", "x & y & z & w")
    lit, note = V._t3_row(body, "a", "P1$-$P0", _CELLS)
    assert lit == f"P1$-$P0 & {row}" and lit in body, note


def test_a_table3_row_with_a_wrong_cell_is_missing():
    row = r"-0.127 & 0.013 & 0.044\rlap{$^{\dagger}$} & {[-0.231, -0.040]}"
    body = _BODY.replace("ROW_A", row).replace("ROW_B", row)
    lit, note = V._t3_row(body, "a", "P1$-$P0", _CELLS)
    assert lit not in body and "0.044" in note


def test_a_missing_dagger_is_a_wrong_cell():
    row = r"-0.127 & 0.013 & 0.043 & {[-0.231, -0.040]}"
    body = _BODY.replace("ROW_A", row).replace("ROW_B", row)
    lit, _ = V._t3_row(body, "a", "P1$-$P0", _CELLS)
    assert lit not in body


def test_the_panel_decides_which_row_is_read():
    good = r"-0.127 & 0.013 & 0.043\rlap{$^{\dagger}$} & {[-0.231, -0.040]}"
    body = _BODY.replace("ROW_A", "1 & 2 & 3 & 4").replace("ROW_B", good)
    assert V._t3_row(body, "a", "P1$-$P0", _CELLS)[0] not in body
    assert V._t3_row(body, "b", "P1$-$P0", _CELLS)[0] in body


def test_the_real_table3_rows_match_in_both_forms():
    """Every row of the live Table 3 matches, and still matches once its
    numeric cells are wrapped in maths (the controller's planned edit)."""
    if not V.PAPER.exists():
        pytest.skip("needs the private tree")
    body = V._paper_body()
    rows = V._table3_rows()
    import results as Res
    per_panel = sum(len(c) for _, c in Res.FAMILIES.values()) + 1     # + P3-P1
    n_inter = len(V._data("results_pooled_A-BS_ol.json")["families"]
                  ["R4_robustness"]["interaction"])
    assert len(rows) == 2 * per_panel + n_inter
    for (panel, name), (lit, note) in rows.items():
        assert lit in body, (panel, name, note)
        cells = lit.split("&")
        wrapped = cells[0].rstrip() + "".join(
            " & " + _wrap(c.strip()) for c in cells[1:])
        body2 = body.replace(lit, wrapped, 1)
        label = cells[0].strip()
        found = V._t3_row(body2, panel, label,
                          [V._t3_norm_cell(c) for c in cells[1:]])
        assert found[0] == wrapped, (panel, name, found[1])


def _wrap(c: str) -> str:
    dag = "\\rlap{$^{\\dagger}$}"
    if c.endswith(dag):
        return f"${c[:-len(dag)]}${dag}"
    m = re.fullmatch(r"\{(\[.*\])\}", c)
    if m:
        return f"${m.group(1)}$"
    return c if c == "--" else f"${c}$"


# ------------------------------------------------ the live manuscript -----
def test_every_retired_label_names_a_current_check():
    if not V.PAPER.exists():
        pytest.skip("needs the private tree")
    labels = {lab for lab, _, _ in V.checks()}
    assert not set(V.RETIRED) - labels


def test_the_abstract_numbers_are_pinned_by_same_quantity_checks():
    """REQ-4 and the abstract's ten measurements: each is carried by a check
    of the quantity the abstract states, not by an accidental twin."""
    if not V.PAPER.exists():
        pytest.skip("needs the private tree")
    lits = V._current_literals(V.checks())
    want = {
        "5,904": "replies per item and the reply counts (Method)",
        "98.0": "RQ1: P0 gives the key, Bank A",
        "92.5": "RQ1: P0 gives the key, Bank B (share)",
        "8.3": "RQ2: P1 flagged, Bank A",
        "6.8": "RQ2: P1 flagged B, given A",
        "83.5": "RQ2: P1 flagged B, given A",
        "74.4": "RQ2: P1 given B; intervals do not overlap",
        "180": "RQ4: both annotators, mitigated",
        "99": "RQ4: both annotators, mitigated",
        "153": "RQ4: at least one annotator, mitigated",
    }
    idx = V._pin_index(lits)
    for tok, label in want.items():
        assert label in idx.get(tok, []), (tok, idx.get(tok))
    assert "about a fifth (A) and three tenths (B)" in lits[
        "abstract: REQ-4 words (about a fifth / three tenths)"]
