#!/usr/bin/env python3
"""Generate `paper_anon.tex` and `title_page.tex` from `paper.tex`.

    make_variants.py            # (re)write both variants
    make_variants.py --check    # exit 1 if either file on disk differs

WHY THIS EXISTS. The anonymised manuscript and the separate title page used to
be hand-made copies of `paper.tex`, re-synced by whoever remembered. By
2026-11-02 they had drifted in the places nobody reads -- the anonymised copy
had lost a co-author's e-mail line and misspelt a street, both inside
commented-out text. Harmless there; the next drift could be a reworded
sentence or a corrected number that reaches only one of three PDFs. Three
hand-kept copies of one text are three texts (4.33). Now `paper.tex` is the
only source and the variants are a function of it;
`tests/test_variants.py` fails if either file on disk is edited by hand.

THE MARKERS are comment lines of their own in `paper.tex`, so they never
change what `paper.tex` renders:

    marker pair                         paper.tex   paper_anon   title_page
    %<*identity> ... %</identity>       kept        commented    kept
    %<*body> ... %</body>               kept        kept         commented
    %<*anon-only> ... %</anon-only>     stored %    uncommented  as stored
    %<*titlepage-only> ... %</...>      stored %    as stored    uncommented

"Commented" prefixes each NON-EMPTY line with one `%` (blank lines stay blank:
in vertical mode a `\\par` is a no-op). "Uncommented" removes the first `%`
of each non-empty line, which therefore MUST be stored commented in
`paper.tex` -- a visible line there would render in the main manuscript.
Marker lines pass through unchanged.

NESTING. Blocks must nest properly, and the rule for a line inside several
is: if ANY enclosing block comments it in this variant, prefix one `%`;
otherwise, if it sits in this variant's `-only` block, uncomment it;
otherwise emit it as stored. `body` may nest in `identity` and vice versa
(the running short-author line is identity in the anonymised copy AND
replaced on the title page). Refused, loudly, with the line number: an
unknown or malformed marker; a close with no open or of the wrong block; an
unclosed block; a block nested in itself; anything nested inside an `-only`
block; an `anon-only` inside `identity` or a `titlepage-only` inside `body`
(each would be commented out in the one variant meant to show it); and a
visible line inside an `-only` block.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import texutil  # noqa: E402  - one comment rule (P0.6, R2-a)

ROOT = Path(__file__).resolve().parent.parent
PAPER = ROOT / "source" / "paper.tex"
OUTPUTS = {
    "anon": ROOT / "source" / "paper_anon.tex",
    "title": ROOT / "source" / "title_page.tex",
}

BLOCKS = ("identity", "body", "anon-only", "titlepage-only")
STORED_COMMENTED = {"anon-only", "titlepage-only"}
# variant -> blocks whose lines it comments out / uncomments
COMMENTS = {"anon": {"identity"}, "title": {"body"}}
UNCOMMENTS = {"anon": {"anon-only"}, "title": {"titlepage-only"}}
# an -only block inside one of these is dead in its own variant
_DEAD_INSIDE = {"anon-only": "identity", "titlepage-only": "body"}
_VARIANT_OF = {"anon-only": "anon", "titlepage-only": "title"}

OPEN_RE = re.compile(r"^\s*%<\*([A-Za-z-]+)>\s*$")
CLOSE_RE = re.compile(r"^\s*%</([A-Za-z-]+)>\s*$")
_MARKERISH = re.compile(r"^\s*%<[*/]")

BANNER = ("%% GENERATED from paper.tex by harness/make_variants.py -- DO NOT EDIT."
          " Edit paper.tex and re-run it.")


class MarkerError(ValueError):
    """A block marker in paper.tex is malformed, unknown or mis-nested."""


def _parse(text: str) -> list[tuple[str, tuple[str, ...] | None]]:
    """(line, enclosing blocks outer->inner) per line; None for a marker."""
    out: list[tuple[str, tuple[str, ...] | None]] = []
    stack: list[tuple[str, int]] = []
    for no, line in enumerate(text.split("\n"), 1):
        mo, mc = OPEN_RE.match(line), CLOSE_RE.match(line)
        if not mo and not mc and _MARKERISH.match(line):
            raise MarkerError(f"line {no}: malformed marker {line.strip()!r}")
        if mo:
            name = mo.group(1)
            if name not in BLOCKS:
                raise MarkerError(f"line {no}: unknown block {name!r}; "
                                  f"expected one of {', '.join(BLOCKS)}")
            names = [n for n, _ in stack]
            if name in names:
                raise MarkerError(f"line {no}: {name} nested inside itself "
                                  f"(opened at line {stack[names.index(name)][1]})")
            if stack and stack[-1][0] in STORED_COMMENTED:
                raise MarkerError(f"line {no}: nothing may be nested inside "
                                  f"{stack[-1][0]} (opened at line {stack[-1][1]})")
            if _DEAD_INSIDE.get(name) in names:
                raise MarkerError(
                    f"line {no}: {name} inside {_DEAD_INSIDE[name]} would never be "
                    f"uncommented: {_DEAD_INSIDE[name]} comments it out in "
                    f"{_VARIANT_OF[name]}")
            stack.append((name, no))
            out.append((line, None))
            continue
        if mc:
            name = mc.group(1)
            if not stack:
                raise MarkerError(f"line {no}: closes {name} without an open block")
            if stack[-1][0] != name:
                raise MarkerError(
                    f"line {no}: mis-nested -- closes {name} but the innermost "
                    f"open block is {stack[-1][0]} (opened at line {stack[-1][1]})")
            stack.pop()
            out.append((line, None))
            continue
        if (stack and stack[-1][0] in STORED_COMMENTED and line.strip()
                and not line.lstrip().startswith("%")):
            raise MarkerError(
                f"line {no}: a line in {stack[-1][0]} must be stored commented "
                f"(it would render in paper.tex): {line.strip()[:60]!r}")
        out.append((line, tuple(n for n, _ in stack)))
    if stack:
        name, opened = stack[-1]
        raise MarkerError(f"line {opened}: {name} is unclosed at end of file")
    return out


def _uncomment(line: str) -> str:
    i = line.find("%")
    return line[:i] + line[i + 1:]


# ---------------------------------------------------------------------------
# SECTION NUMBERS ON THE TITLE PAGE COME FROM paper.aux (F4, rounds 1-2)
#
# The title page's body is commented out, so no \label exists there and a
# \ref would print "??". The hand-made title page therefore carried literal
# numbers ("Section~4.5") -- a hand-kept copy of the build that goes stale the
# moment sections are renumbered. Round 1 checked a literal against a body
# twin on the very next line; the re-review showed that a blank line, a
# comment line, or a missing twin let a stale number through. Round 2's rule
# replaces the twin check outright:
#
#   1. REFUSE any VISIBLE title-page line (a `titlepage-only` line once its
#      leading `%` is removed, and not itself a comment) that carries a
#      literal cross-reference number: Section(s)/Sect./Table(s)/Figure(s)/
#      Fig./Appendix, then `~` or a space, then a digit or an appendix letter;
#      or `§` and a digit. The error names the line and says "use
#      `\auxref{label}`". No adjacency, no twin: the literal itself is wrong.
#   2. SUBSTITUTE `\auxref{label}` from paper.aux at generation time (it is
#      an undefined macro, so if it ever reached LaTeX unreplaced the build
#      would halt rather than print).
#
# paper.aux must exist, be no older than paper.tex, and hold the label; each
# failure is a MarkerError. Only the title page needs it.
# ---------------------------------------------------------------------------
#
# P0.6 additions. R2-a: each visible line is split once with the shared
# comment rule (texutil.split_comment); the refusals and the substitution
# read the CODE part only and the comment part is emitted verbatim -- a note
# may quote "Section~4.5" or name \auxref{label} without effect. R2-c: the
# keywords are case-insensitive and include Sec./Figs., and the digit may sit
# in braces (Section~{4.5}); the appendix-letter class stays case-sensitive so
# "Figure x shows" is prose. R2-d: the \ref family is refused too -- the title
# page has no labels, so it would print "??".
#
# P0.6 fix round 1 (M8): also `Eq.`/`Eqs.`/`Tab.`; the separator may be a TeX
# space command (`\ `, `\,`, `\;`, `\:`, `\!`) as well as `~` or a space;
# `§` may be followed by `~`; and a formatting macro may wrap the number
# (`Section~\textbf{4}`).
# The separator class is texutil.TEX_SPACE, shared with verify_numbers'
# \cite-locator rule (P0.6 fix round 2, N6).
_LITERAL_XREF_RE = re.compile(
    r"\b(?i:Sections?|Sect\.|Sec\.|Tables?|Tab\.|Figures?|Figs?\.|Eqs?\.|Appendix)"
    + texutil.TEX_SPACE + "+"
    + r"(?:\\[A-Za-z]+\s*)?\{?(?:\d|[A-Z](?![a-z]))"
    + r"|§" + texutil.TEX_SPACE + "*"
    + r"(?:\\[A-Za-z]+\s*)?\{?\d")
_REF_FAMILY_RE = re.compile(
    r"\\(?:" + "|".join(texutil.REF_COMMANDS) + r")\*?\s*\{")
_AUXREF_RE = re.compile(r"\\auxref\{([^}]+)\}")
_NEWLABEL_RE = re.compile(r"\\newlabel\{([^}]+)\}\{\{([^}]*)\}")


def aux_labels(aux: Path) -> dict[str, str]:
    """label -> printed number, from a LaTeX .aux file."""
    return dict(_NEWLABEL_RE.findall(aux.read_text(encoding="utf-8", errors="replace")))


class _Aux:
    """paper.aux, loaded on first use and refused loudly when unusable."""

    def __init__(self) -> None:
        self._labels: dict[str, str] | None = None

    def number(self, label: str, where: str) -> str:
        if self._labels is None:
            aux = PAPER.with_suffix(".aux")
            if not aux.exists():
                raise MarkerError(f"{where}: no paper.aux to resolve {label!r} "
                                  f"-- build paper.tex first")
            if aux.stat().st_mtime < PAPER.stat().st_mtime:
                raise MarkerError(f"{where}: paper.aux is older than paper.tex "
                                  f"-- rebuild before generating the title page")
            self._labels = aux_labels(aux)
        if label not in self._labels:
            raise MarkerError(f"{where}: paper.aux lacks label {label!r}")
        return self._labels[label]


def render(text: str, variant: str) -> str:
    """The `variant` ('anon' or 'title') of `text`, banner first."""
    if variant not in COMMENTS:
        raise ValueError(f"unknown variant {variant!r}; expected one of "
                         f"{sorted(COMMENTS)}")
    parsed = _parse(text)
    aux = _Aux()
    lines = []
    for no, (line, blocks) in enumerate(parsed, 1):
        if blocks is None or not line.strip():
            lines.append(line)
        elif any(b in COMMENTS[variant] for b in blocks):
            lines.append("%" + line)
        elif blocks and blocks[-1] in UNCOMMENTS[variant]:
            code, comment = texutil.split_comment(_uncomment(line))
            if variant == "title":
                lit = _LITERAL_XREF_RE.search(code)
                if lit:
                    raise MarkerError(
                        f"line {no}: literal cross-reference number "
                        f"{lit.group(0)!r} on the title page goes stale when "
                        f"sections are renumbered -- use `\\auxref{{label}}`")
                ref = _REF_FAMILY_RE.search(code)
                if ref:
                    raise MarkerError(
                        f"line {no}: {ref.group(0).rstrip('{').strip()!r} on the "
                        f"title page prints '??' (its labels are commented out) "
                        f"-- use `\\auxref{{label}}`")
            lines.append(_AUXREF_RE.sub(lambda m: aux.number(m.group(1), f"line {no}"),
                                        code) + comment)
        else:
            lines.append(line)
    return BANNER + "\n" + "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    check = "--check" in argv
    try:
        src = PAPER.read_text(encoding="utf-8")
        rendered = {v: render(src, v) for v in OUTPUTS}
    except MarkerError as e:
        print(f"ERROR    {PAPER.name}: {e}")
        return 2
    differs = []
    for variant, path in OUTPUTS.items():
        same = path.exists() and path.read_text(encoding="utf-8") == rendered[variant]
        if check:
            if not same:
                differs.append(path.name)
                print(f"DIFFERS  {path.name} is not what {PAPER.name} generates")
        elif not same:
            path.write_text(rendered[variant], encoding="utf-8")
            print(f"wrote    {path.name}")
        else:
            print(f"same     {path.name}")
    return 1 if differs else 0


if __name__ == "__main__":
    sys.exit(main())
