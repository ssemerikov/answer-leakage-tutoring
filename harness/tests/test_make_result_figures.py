"""Result figures (revision 2026-11, P1C): coordinates computed, never typed.

The figure snippets are read back here and every plotted value is compared with
the assembled JSON it came from. A figure that disagrees with the table beside
it is the hand-transcription drift `make_screen_figure.py` was written to end.

Fix round 1 (controller's figure ruling): Fig 2 is drawn on the items that HAVE
answer forms, stated only where it is a measurement (P0, P1), P2/P3 stated as an
open marker at 0 "by construction"; Fig 3 has two panels; every error bar is the
item-cluster interval, and each header names its basis and its CI kind.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C               # noqa: E402
import make_result_figures as MF  # noqa: E402

TOL = 5.1e-5      # the snippets carry 4 decimals
REGS = list(C.REGIMES)

_PT = re.compile(r"\(([-\d.]+),([-\d.]+)\)(?: \+= \(0,([-\d.]+)\) -= \(0,([-\d.]+)\))?")
_BLOCK = re.compile(r"^% series: ([^\n]*)\n\\addplot[^\n]*coordinates \{(.*?)\};",
                    re.MULTILINE | re.DOTALL)
_AXIS = re.compile(r"\\begin\{axis\}(.*?)\\end\{axis\}", re.DOTALL)


def _series(tex: str) -> list[tuple[str, list[tuple]]]:
    """[(series name, [(x, y, up|None, down|None), ...]), ...] in plotting order.

    Every `\\addplot` must be announced by a `% series:` line, so a plot the
    parser cannot name is a failure rather than a skipped series.
    """
    out = [(m.group(1), [tuple(None if v is None else float(v) for v in p.groups())
                         for p in _PT.finditer(m.group(2))])
           for m in _BLOCK.finditer(tex)]
    assert len(out) == tex.count("\\addplot"), "an unannounced \\addplot"
    return out


def _panels(tex: str) -> list[dict]:
    """One {series name: points} per axis environment, in order."""
    return [dict(_series(m.group(0))) for m in _AXIS.finditer(tex)]


def _close(a, b):
    return abs(a - b) <= TOL


def _check(points, want, xs=None):
    """want: [(y, lo, hi)]; xs: the regime positions the points must sit at."""
    assert len(points) == len(want)
    for (x, y, up, down), (wy, lo, hi) in zip(points, want):
        assert up is not None and down is not None, "an error bar is missing"
        assert _close(y, wy) and _close(y + up, hi) and _close(y - down, lo), (
            (y, up, down), (wy, lo, hi))
    if xs is not None:
        assert [round(p[0]) for p in points] == xs


def _json(name):
    return json.loads((C.ROOT / "data" / name).read_text(encoding="utf-8"))


def test_fmt_never_prints_a_negative_zero():
    # hv.wilson does not clamp: at k = 0 its lower bound is about -1e-19.
    assert MF.fmt(-1e-19) == "0.0000" and MF.fmt(0.12345) == "0.1235"


# ---- Fig 2 ------------------------------------------------------------------

def test_stated_given_is_drawn_on_items_with_forms_with_cluster_intervals():
    tex = MF.fig_stated_given()
    panels = _panels(tex)
    assert len(panels) == 2
    for panel, f in zip(panels, ("exploratory_bankA_ol.json",
                                 "exploratory_bankBS_ol.json")):
        fc = _json(f)["by_form_coverage"]["with_forms"]
        assert set(panel) == {"given", "flagged", "flagged, zero by construction"}
        _check(panel["given"], [(fc[r]["given"]["rate"],
                                 *fc[r]["given"]["ci_cluster_item"]) for r in REGS],
               xs=[0, 1, 2, 3])
        _check(panel["flagged"], [(fc[r]["stated"]["rate"],
                                   *fc[r]["stated"]["ci_cluster_item"])
                                  for r in ("P0", "P1")], xs=[0, 1])
        bc = panel["flagged, zero by construction"]
        assert [round(p[0]) for p in bc] == [2, 3]
        assert all(p[1] == 0 and p[2] is None for p in bc), "no bar at 0 by construction"
        for r in ("P2", "P3"):
            assert fc[r]["stated"]["k"] == 0


def test_stated_given_header_names_basis_n_and_interval():
    tex = MF.fig_stated_given()
    head = "\n".join(l for l in tex.splitlines() if l.startswith("%"))
    for phrase in ("items with answer forms", "Bank A: 13 of 14 items, 468 replies",
                   "Bank B: 9 of 27 items, 324 replies",
                   "item-cluster percentile bootstrap", "B=10000", "seed 20261103",
                   "0 by construction"):
        assert phrase in head, phrase


# ---- Fig 3 ------------------------------------------------------------------

def test_leak_by_regime_has_two_panels_with_cluster_intervals():
    tex = MF.fig_leak_by_regime()
    a, b = _panels(tex)
    for bank, f, rf in (("A", "exploratory_bankA_ol.json", "results_bankA_ol.json"),
                        ("B", "exploratory_bankBS_ol.json", "results_bankBS_ol.json")):
        g = _json(f)["given"]
        desc = _json(rf)["descriptives"]["leak_adj"]["regime"]
        for r in REGS:
            assert g[r]["mean"] == desc[r]["mean"]
        _check(a[f"Bank {bank}"], [(g[r]["mean"], *g[r]["mean_ci_cluster_item"])
                                   for r in REGS])
    h = _json("human_validation_results.json")["by_regime"]
    for rule in ("both", "either"):
        _check(b[rule], [(h[r][rule]["p"], *h[r][rule]["ci_cluster_item"]) for r in REGS])


def test_leak_by_regime_header_names_each_interval():
    tex = MF.fig_leak_by_regime()
    head = "\n".join(l for l in tex.splitlines() if l.startswith("%"))
    for phrase in ("(a)", "(b)", "item-cluster percentile bootstrap",
                   "240-response Bank A sample", "mean leak_adj",
                   "n_clusters 14 (Bank A), 27 (Bank B)"):
        assert phrase in head, phrase


def test_leak_by_regime_header_prints_a_one_value_set_as_a_number():
    # P1C re-review N4: "([60] responses ...)" leaked a Python list.
    head = "\n".join(l for l in MF.fig_leak_by_regime().splitlines()
                     if l.startswith("%"))
    assert "(60 responses per condition" in head and "[60]" not in head


# ---- design revision 1, Section 4: the strings the figures carry -------------
#
# The design fixes panel titles, tick labels, axis labels, legends, marker
# styles and offsets. Each series is read back with its style and legend.

_FULL = re.compile(r"^% series: ([^\n]*)\n\\addplot\[([^\n]*)\] coordinates \{(.*?)\};\n"
                   r"(?:\\addlegendentry\{([^\n]*)\}\n)?", re.MULTILINE | re.DOTALL)
TICKS = ["{P0\\\\none}", "{P1\\\\instruction}", "{P2\\\\+ filter}", "{P3\\\\+ repair}"]


def _axes(tex):
    """[(axis options, {series id: (style, [x...], legend|None)})] per axis."""
    out = []
    for m in _AXIS.finditer(tex):
        body = m.group(0)
        opts = body[len("\\begin{axis}["):body.index("]\n")]
        ser = {}
        for s in _FULL.finditer(body):
            xs = [float(p.group(1)) for p in _PT.finditer(s.group(3))]
            ser[s.group(1)] = (s.group(2), xs, s.group(4))
        out.append((opts, ser))
    return out


def _offsets(xs):
    return sorted({round(x - round(x), 2) for x in xs})


#: P1F review Minor 6: the axis reads 0 to 1, with headroom above 1 so a bar
#: ending exactly at 1.0 shows its cap instead of sitting on the frame.
YTICK = "ytick={0,0.2,0.4,0.6,0.8,1}"
HEADROOM = "enlarge y limits={upper,value=0.03}"


def _common_axis(opts):
    assert "xticklabels={" + ",".join(TICKS) + "}" in opts
    assert "xticklabel style={align=center}" in opts
    assert "ymin=0," in opts and "ymax=1," in opts
    assert YTICK + "," in opts and HEADROOM + "," in opts


def test_a_bar_ending_at_one_has_headroom_in_both_figures():
    # The case Minor 6 is about: Fig 2 Bank A P0 "gives the key" and Fig 3b
    # "at least one annotator" P0 both end at 1.0000 on the current data.
    for fn in (MF.fig_stated_given, MF.fig_leak_by_regime):
        tex = fn()
        tops = [y + up for _, pts in _series(tex) for (_, y, up, _) in pts
                if up is not None]
        assert any(_close(t, 1.0) for t in tops), fn.__name__
        for opts, _ in _axes(tex):
            _common_axis(opts)


def test_fig2_carries_the_design_strings():
    (l_opts, l_ser), (r_opts, r_ser) = _axes(MF.fig_stated_given())
    assert "title={Bank A: 13 of 14 items}" in l_opts
    assert "title={Bank B: 9 of 27 items}" in r_opts
    assert "ylabel={Share of replies}" in l_opts
    for opts in (l_opts, r_opts):
        _common_axis(opts)
    for ser, legend in ((l_ser, True), (r_ser, False)):
        style, xs, leg = ser["given"]
        assert "mark=*," in style and "color=black!55" in style
        assert "fill=black!55" in style and "error bars" in style
        assert _offsets(xs) == [0.12]
        assert leg == ("Gives the key (adjusted $\\geq 0.25$)" if legend else None)
        style, xs, leg = ser["flagged"]
        assert "mark=square*," in style and "color=black," in style
        assert "fill=black}" in style and "error bars" in style
        assert _offsets(xs) == [-0.12] and [round(x) for x in xs] == [0, 1]
        assert leg == ("Flagged by the answer check" if legend else None)
        style, xs, leg = ser["flagged, zero by construction"]
        assert "mark=square," in style and "fill=white" in style
        assert "error bars" not in style
        assert _offsets(xs) == [-0.12] and [round(x) for x in xs] == [2, 3]
        assert leg == ("Flagged: zero by construction" if legend else None)


def test_fig3_carries_the_design_strings():
    (a_opts, a_ser), (b_opts, b_ser) = _axes(MF.fig_leak_by_regime())
    assert "title={(a) Adjusted recoverability}" in a_opts
    assert "ylabel={Mean $\\mathrm{leak}_{\\mathrm{adj}}$}" in a_opts
    assert "title={(b) Human readers, Bank A sample}" in b_opts
    assert "ylabel={Share of sampled replies}" in b_opts
    for opts in (a_opts, b_opts):
        _common_axis(opts)
    want = {
        "Bank A": ("mark=square*,", "color=black,", "fill=black}", -0.1, "Bank A (14 items)"),
        "Bank B": ("mark=*,", "color=black!55", "fill=black!55}", 0.1, "Bank B (27 items)"),
        "both": ("mark=triangle*,", "color=black,", "fill=black}", -0.1, "Both annotators"),
        "either": ("mark=triangle,", "color=black!55", "fill=white}", 0.1,
                   "At least one annotator"),
    }
    for ser in (a_ser, b_ser):
        for sid, (style, xs, leg) in ser.items():
            mark, color, fill, off, legend = want[sid]
            assert mark in style and color in style and fill in style, sid
            assert "error bars" in style, sid
            assert _offsets(xs) == [off] and leg == legend, sid
    assert set(a_ser) == {"Bank A", "Bank B"} and set(b_ser) == {"both", "either"}


def _figure_inputs(tmp_path, monkeypatch):
    for f in MF.INPUTS:
        shutil.copy(C.ROOT / "data" / f, tmp_path / f)
    monkeypatch.setattr(MF, "DATA", tmp_path)
    return tmp_path


def _edit(path, fn):
    d = json.loads(path.read_text(encoding="utf-8"))
    fn(d)
    path.write_text(json.dumps(d), encoding="utf-8")


def test_a_filled_marker_is_refused_where_stated_is_zero_by_construction(
        tmp_path, monkeypatch):
    # The generator reads stated.<regime>.by_construction: a filled (measured)
    # marker for a value that is 0 by construction would present a tautology
    # as a finding.
    d = _figure_inputs(tmp_path, monkeypatch)
    _edit(d / "exploratory_bankBS_ol.json",
          lambda x: x["stated"]["P1"].__setitem__("by_construction", True))
    with pytest.raises(ValueError, match="by_construction"):
        MF.fig_stated_given()


@pytest.mark.parametrize("change", ["drop", "false"])
def test_zero_by_construction_is_drawn_only_where_the_flag_says_so(
        tmp_path, monkeypatch, change):
    d = _figure_inputs(tmp_path, monkeypatch)

    def edit(x):
        if change == "drop":
            del x["stated"]["P2"]["by_construction"]
        else:
            x["stated"]["P3"]["by_construction"] = False
    _edit(d / "exploratory_bankA_ol.json", edit)
    with pytest.raises(ValueError, match="by_construction"):
        MF.fig_stated_given()


def test_fig2_header_names_the_clusters_of_each_panel():
    head = "\n".join(l for l in MF.fig_stated_given().splitlines() if l.startswith("%"))
    assert "n_clusters 13 (Bank A), 9 (Bank B)" in head


# ---- currency and provenance ---------------------------------------------------

def test_the_emitted_files_are_current():
    for name, fn in MF.FIGURES.items():
        on_disk = (MF.FIG_DIR / name).read_text(encoding="utf-8")
        assert on_disk == fn(), f"{name} is stale; re-run make_result_figures.py"


def test_a_rerun_changing_only_generated_at_leaves_the_figures_current(
        tmp_path, monkeypatch):
    # Review F6: the header hashes are of content, not of the timestamp.
    for f in MF.INPUTS:
        shutil.copy(C.ROOT / "data" / f, tmp_path / f)
    for f in ("results_bankA_ol.json", "results_bankBS_ol.json"):
        d = json.loads((tmp_path / f).read_text(encoding="utf-8"))
        d["generated_at"] = "2099-01-01T00:00:00+00:00"
        (tmp_path / f).write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
    monkeypatch.setattr(MF, "DATA", tmp_path)
    for name, fn in MF.FIGURES.items():
        assert fn() == (MF.FIG_DIR / name).read_text(encoding="utf-8"), name


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="no TeX here")
def test_each_snippet_compiles_inside_a_minimal_standalone(tmp_path):
    for name, fn in MF.FIGURES.items():
        (tmp_path / name).write_text(fn(), encoding="utf-8")
        wrap = tmp_path / f"wrap_{Path(name).stem}.tex"
        wrap.write_text(MF.WRAPPER.format(name=name), encoding="utf-8")
        r = subprocess.run(["pdflatex", "-interaction=nonstopmode",
                            "-halt-on-error", wrap.name], cwd=tmp_path,
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stdout[-2000:]
        assert (tmp_path / f"wrap_{Path(name).stem}.pdf").exists()
