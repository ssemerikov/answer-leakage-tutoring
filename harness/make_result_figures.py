"""Emit the pgfplots/TikZ for the revised manuscript's two result figures.

WHY. The same reason as `make_screen_figure.py`: a figure's coordinates typed by
hand drift from the data they depict. These are computed from the assembled
JSON files only -- never from the run logs, never from a number in prose -- and
`tests/test_make_result_figures.py` reads every plotted value back and compares
it with its source.

Layout (controller's figure ruling, fix round 1):

  fig_stated_given.tex    Two panels, Bank A and Bank B, x = P0-P3. Both series
                          on the items that HAVE answer forms (13 of 14 on A, 9
                          of 27 on B): only there is "stated" a meaningful
                          measurement, and one basis per figure.
                          given (leak_adj >= 0.25) at all four conditions;
                          stated at P0 and P1; at P2/P3 stated is an open marker
                          at 0 with no bar -- 0 by construction, since the
                          delivered text passed the same detector.
                          From by_form_coverage.with_forms of
                          data/exploratory_bank{A,BS}_ol.json.
  fig_leak_by_regime.tex  (a) mean leak_adj by condition, both banks
                          (exploratory given.mean, equal to the results files'
                          descriptives.leak_adj.regime); (b) human recovery of
                          the key, both / either annotator, on the 240-response
                          Bank A sample (by_regime of
                          data/human_validation_results.json).

Every error bar is the ITEM-CLUSTER percentile bootstrap interval
(`analyse.cluster_bootstrap_ci`), and each header names its basis, n, CI kind
and number of clusters. The two quantities of Fig 3 sit in separate panels: a
placebo-corrected accuracy difference is not a recovery proportion.

Labels (P1F): design revision 1, Section 4 fixes the panel titles, the two-line
tick labels, the axis labels, the legends, the marker styles and the offsets;
they are the constants below. "Flagged" is the answer check's verdict
(`stated` in the artefacts). The open "zero by construction" squares are drawn
only where the artefact's `stated.<regime>.by_construction` flag says so, and a
filled (measured) marker is refused wherever that flag is set. Captions are not
generated: the manuscript carries them.

Each file is a complete `tikzpicture` using core pgfplots only (no libraries),
so it compiles inside a minimal standalone document (`WRAPPER`) and can be
`\\input` into the manuscript, whose preamble already loads pgfplots with
compat=1.17. Nothing is written into `source/`.

Header hashes are `exploratory.content_sha256` -- canonical content with
`generated_at` removed -- so a re-run that only re-stamps a results file leaves
the figures current.

No threshold-keyed entry is read here (the "0.10" vs "0.1" key trap between the
results files and the human-validation file does not arise).

    python3 make_result_figures.py [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as C          # noqa: E402
import exploratory as X     # noqa: E402

DATA = C.ROOT / "data"
FIG_DIR = C.ROOT / "docs" / "revision-2026-11" / "figures"
#: Every file a figure reads, so a test can copy them all.
INPUTS = ("exploratory_bankA_ol.json", "exploratory_bankBS_ol.json",
          "results_bankA_ol.json", "results_bankBS_ol.json",
          "human_validation_results.json")
REGS = list(C.REGIMES)

#: Design revision 1, Section 4: tick labels (two lines each), legends, grey.
TICKS = {"P0": "P0\\\\none", "P1": "P1\\\\instruction",
         "P2": "P2\\\\+ filter", "P3": "P3\\\\+ repair"}
GREY = "black!55"
LEG_GIVEN = f"Gives the key (adjusted $\\geq {X.GIVEN_CUT:g}$)"
LEG_FLAGGED = "Flagged by the answer check"
LEG_ZERO = "Flagged: zero by construction"
LEG_BOTH = "Both annotators"
LEG_EITHER = "At least one annotator"

#: The minimal standalone document the snippets must compile in.
WRAPPER = (
    "\\documentclass[border=4pt]{{standalone}}\n"
    "\\usepackage{{pgfplots}}\n"
    "\\pgfplotsset{{compat=1.17}}\n"
    "\\begin{{document}}\n"
    "\\input{{{name}}}\n"
    "\\end{{document}}\n")

#: The y axis reads 0 to 1 (`ytick`), with 3% headroom above 1 so that a bar
#: ending exactly at 1.0 shows its cap instead of merging with the frame
#: (P1F review Minor 6: Fig 2 Bank A P0 and Fig 3b P0 end at 1.0000).
_AXIS = ["width=7.4cm", "height=5.2cm", "ymin=0", "ymax=1",
         "ytick={0,0.2,0.4,0.6,0.8,1}", "enlarge y limits={upper,value=0.03}",
         "xmin=-0.5", "xmax=3.5", "xtick={0,1,2,3}",
         "xticklabels={" + ",".join("{" + TICKS[r] + "}" for r in REGS) + "}",
         "xticklabel style={align=center}",
         "tick label style={font=\\scriptsize}",
         "label style={font=\\small}", "title style={font=\\small}"]


def fmt(x: float) -> str:
    """Four decimals, and never "-0.0000": `human_validation.wilson` does not
    clamp, so a lower bound at k = 0 comes out near -1e-19."""
    return f"{round(float(x), 4) + 0.0:.4f}"


def _pt(x: float, y: float, lo: float | None = None, hi: float | None = None) -> str:
    """One point, with an asymmetric error bar unless lo/hi are None. The bar
    ends are taken from the ROUNDED interval, so y + up reproduces the printed
    bound exactly."""
    ry = round(y, 4)
    if lo is None:
        return f"({x:.2f},{fmt(ry)})"
    rlo, rhi = round(lo, 4), round(hi, 4)
    return f"({x:.2f},{fmt(ry)}) += (0,{fmt(rhi - ry)}) -= (0,{fmt(ry - rlo)})"


def _load(name: str) -> tuple[dict, str]:
    p = DATA / name
    return (json.loads(p.read_text(encoding="utf-8")),
            X.content_sha256(p)[:16])


def _plot(lines: list[str], name: str, style: str, pts: list[str],
          legend: str | None, bars: bool = True) -> None:
    """One series: `name` is its id (a `% series:` comment the tests parse),
    `legend` the printed legend entry, or None for no entry."""
    # `\addplot` without `+`: the default cycle list's mark fill would
    # otherwise survive a `color=` and print the marks in blue and red.
    lines.append(f"% series: {name}")
    eb = ", error bars/.cd, y dir=both, y explicit" if bars else ""
    lines.append(f"\\addplot[{style}{eb}] coordinates {{")
    lines.extend(f"  {p}" for p in pts)
    lines.append("};")
    if legend is not None:
        lines.append(f"\\addlegendentry{{{legend}}}")


def _one(values) -> str:
    """A set of counts as text: the number itself when the set holds one value
    (P1C re-review N4: a Python list leaked into a header as "[60]")."""
    vs = sorted(set(values))
    return str(vs[0]) if len(vs) == 1 else ", ".join(map(str, vs))


def _zero_by_construction(label: str, d: dict, filled: list, zero: list) -> None:
    """Read `stated.<regime>.by_construction` and refuse a contradiction: a
    filled (measured) marker where the flag is set, or an open "zero by
    construction" marker where it is not."""
    for r in filled:
        if d["stated"][r].get("by_construction"):
            raise ValueError(
                f"{label} {r}: stated.{r}.by_construction is set, so the value is "
                f"0 by construction; refusing to draw it as a filled (measured) "
                f"marker.")
    for r in zero:
        if d["stated"][r].get("by_construction") is not True:
            raise ValueError(
                f"{label} {r}: stated.{r}.by_construction is not set; refusing to "
                f"draw it as an open 'zero by construction' marker.")


def _method(d: dict) -> str:
    m = d["intervals"]["ci_cluster_item"]
    return f"{m['method']}, B={m['B']}, seed {m['seed']}"


def fig_stated_given() -> str:
    banks = [("Bank A", *_load("exploratory_bankA_ol.json"), "exploratory_bankA_ol.json"),
             ("Bank B", *_load("exploratory_bankBS_ol.json"), "exploratory_bankBS_ol.json")]

    def basis(label, d):
        fc = d["by_form_coverage"]["with_forms"]
        ns = {fc[r]["given"]["n"] for r in REGS}
        n = ns.pop() if len(ns) == 1 else None
        return (f"{label}: {fc[REGS[0]]['n_items']} of "
                f"{d['detector']['n_retained_items']} items, {n} replies per "
                f"condition"), n

    L = ["% GENERATED by harness/make_result_figures.py -- do not edit; re-run it.",
         *(f"% from data/{f} (content sha256 {sha}...)" for _, _, sha, f in banks),
         "% basis: items with answer forms only (by_form_coverage.with_forms); "
         + "; ".join(basis(lab, d)[0] for lab, d, _, _ in banks) + ".",
         "% given = leak_adj >= 0.25 (results.exceeds_threshold), all four "
         "conditions; stated = checker.check_explicit on the delivered reply, "
         "P0 and P1.",
         "% error bars: 95% " + _method(banks[0][1]) + " (items resampled); "
         "n_clusters " + ", ".join(
             f"{d['by_form_coverage']['with_forms'][REGS[0]]['n_items']} ({lab})"
             for lab, d, _, _ in banks) + ".",
         "% flagged = stated in the artefact (the answer check's verdict). At P2 "
         "and P3 it is 0 by construction (the delivered text passed the same "
         "detector, or is the blocked message);",
         "%   it is drawn as an open marker at 0 with no error bar, only where "
         "stated.<regime>.by_construction is set.",
         "\\begin{tikzpicture}"]
    for idx, (label, d, _sha, _f) in enumerate(banks):
        fc = d["by_form_coverage"]["with_forms"]
        title = (f"{label}: {fc[REGS[0]]['n_items']} of "
                 f"{d['detector']['n_retained_items']} items")
        opts = _AXIS + [f"title={{{title}}}"]
        if idx == 0:
            # Legend BELOW the panels: inside, it hid Bank A's P1 flagged point.
            opts += ["name=left", "ylabel={Share of replies}",
                     "legend columns=3",
                     "legend style={font=\\scriptsize, at={(1.08,-0.25)}, "
                     "anchor=north, cells={anchor=west}, "
                     "/tikz/every even column/.append style={column sep=0.4cm}}"]
        else:
            opts += ["at={(left.south east)}", "anchor=south west",
                     "xshift=1.3cm"]
        L.append("\\begin{axis}[" + ",\n  ".join(opts) + "]")
        legend = idx == 0
        filled = [r for r in REGS if r in X.INFORMATIVE]
        zero = [r for r in REGS if r in X.GUARDED]
        _zero_by_construction(label, d, filled, zero)
        _plot(L, "given", f"only marks, mark=*, color={GREY}, "
              f"mark options={{fill={GREY}}}",
              [_pt(i + 0.12, fc[r]["given"]["rate"], *fc[r]["given"]["ci_cluster_item"])
               for i, r in enumerate(REGS)], LEG_GIVEN if legend else None)
        _plot(L, "flagged", "only marks, mark=square*, color=black, "
              "mark options={fill=black}",
              [_pt(i - 0.12, fc[r]["stated"]["rate"], *fc[r]["stated"]["ci_cluster_item"])
               for i, r in enumerate(REGS) if r in filled],
              LEG_FLAGGED if legend else None)
        for r in zero:
            if fc[r]["stated"]["k"] != 0:
                raise ValueError(f"{label} {r}: stated is {fc[r]['stated']['k']}, "
                                 f"not 0 by construction; refusing to draw it so.")
        _plot(L, "flagged, zero by construction",
              "only marks, mark=square, color=black, mark options={fill=white}",
              [_pt(i - 0.12, 0.0) for i, r in enumerate(REGS) if r in zero],
              LEG_ZERO if legend else None, bars=False)
        L.append("\\end{axis}")
    L.append("\\end{tikzpicture}")
    return "\n".join(L) + "\n"


def fig_leak_by_regime() -> str:
    ea, sha_ea = _load("exploratory_bankA_ol.json")
    eb, sha_eb = _load("exploratory_bankBS_ol.json")
    ra, sha_a = _load("results_bankA_ol.json")
    rb, sha_b = _load("results_bankBS_ol.json")
    hv, sha_h = _load("human_validation_results.json")
    for e, r, lab in ((ea, ra, "A"), (eb, rb, "B")):
        desc = r["descriptives"]["leak_adj"]["regime"]
        for reg in REGS:
            if e["given"][reg]["mean"] != desc[reg]["mean"]:
                raise ValueError(f"Bank {lab} {reg}: exploratory mean differs "
                                 f"from descriptives.leak_adj.regime.")
    h = hv["by_regime"]
    hm = h["intervals"]["ci_cluster_item"]
    n_h = _one(h[r]["n"] for r in REGS)
    na, nb = (e["detector"]["n_retained_items"] for e in (ea, eb))

    L = ["% GENERATED by harness/make_result_figures.py -- do not edit; re-run it.",
         f"% from data/exploratory_bankA_ol.json ({sha_ea}...), "
         f"data/exploratory_bankBS_ol.json ({sha_eb}...),",
         f"%      data/results_bankA_ol.json ({sha_a}...), "
         f"data/results_bankBS_ol.json ({sha_b}...),",
         f"%      data/human_validation_results.json ({sha_h}...); content sha256.",
         "% (a) mean leak_adj (placebo-corrected) by condition, all items: Bank A "
         f"{na} items, Bank B {nb} items. Error bars: 95% "
         + _method(ea) + f"; n_clusters {na} (Bank A), {nb} (Bank B).",
         "% (b) human recovery of the key on the 240-response Bank A sample "
         f"({n_h} responses per condition, {hm['n_clusters']} items): both "
         "annotators / at least one annotator. Error bars: 95% "
         f"{hm['method']}, B={hm['B']}, seed {hm['seed']}; n_clusters "
         f"{hm['n_clusters']}.",
         "% The panels hold different quantities (an accuracy difference vs a "
         "proportion of responses); do not compare heights across them.",
         "\\begin{tikzpicture}",
         "\\begin{axis}[" + ",\n  ".join(
             _AXIS + ["name=left", "title={(a) Adjusted recoverability}",
                      "ylabel={Mean $\\mathrm{leak}_{\\mathrm{adj}}$}",
                      "legend style={font=\\scriptsize, at={(0.03,0.03)}, "
                      "anchor=south west, cells={anchor=west}}"]) + "]"]
    for name, e, n, off, style in (
            ("Bank A", ea, na, -0.1, "only marks, mark=square*, color=black, "
                                     "mark options={fill=black}"),
            ("Bank B", eb, nb, 0.1, f"only marks, mark=*, color={GREY}, "
                                    f"mark options={{fill={GREY}}}")):
        g = e["given"]
        _plot(L, name, style,
              [_pt(i + off, g[r]["mean"], *g[r]["mean_ci_cluster_item"])
               for i, r in enumerate(REGS)], legend=f"{name} ({n} items)")
    L += ["\\end{axis}",
          "\\begin{axis}[" + ",\n  ".join(
              _AXIS + ["at={(left.south east)}", "anchor=south west",
                       "xshift=1.3cm",
                       "title={(b) Human readers, Bank A sample}",
                       "ylabel={Share of sampled replies}",
                       "legend style={font=\\scriptsize, at={(0.03,0.03)}, "
                       "anchor=south west, cells={anchor=west}}"]) + "]"]
    for name, legend, off, style in (
            ("both", LEG_BOTH, -0.1, "only marks, mark=triangle*, color=black, "
                                     "mark options={fill=black}"),
            ("either", LEG_EITHER, 0.1, f"only marks, mark=triangle, color={GREY}, "
                                        "mark options={fill=white}")):
        _plot(L, name, style,
              [_pt(i + off, h[r][name]["p"], *h[r][name]["ci_cluster_item"])
               for i, r in enumerate(REGS)], legend=legend)
    L += ["\\end{axis}", "\\end{tikzpicture}"]
    return "\n".join(L) + "\n"


FIGURES = {"fig_stated_given.tex": fig_stated_given,
           "fig_leak_by_regime.tex": fig_leak_by_regime}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=FIG_DIR)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    for name, fn in FIGURES.items():
        p = a.out / name
        p.write_text(fn(), encoding="utf-8")
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
