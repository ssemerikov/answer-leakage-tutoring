"""Shared LaTeX text utilities -- ONE implementation of each rule.

WHY THIS EXISTS. Before 2026-11-02 the manuscript was read by three comment
strippers: `promise_audit.strip_latex` had a loop with a `(?<!\\\\)%`
lookbehind, `verify_numbers.abstract_words` dropped full-line comments only,
and `verify_numbers.main` stripped nothing at all -- so a number that survived
only in a `%%` comment "verified" against the data while no reader could see
it. Three strippers are three opinions about what the manuscript says (4.33).
"""

from __future__ import annotations


def _comment_start(line: str) -> int:
    """Index of the `%` that opens a comment on `line`, or -1.

    A `%` is ESCAPED only when an ODD number of backslashes precedes it:
    `\\%` is a literal percent sign, but `\\\\%` is a forced line break
    followed by a real comment, and `\\\\\\%` is a line break followed by a
    literal percent. A one-character lookbehind gets the second case wrong.

    Known limitation, stated rather than papered over: `%` inside `\\verb`,
    a verbatim environment, or a top-level `\\url{...}` (url.sty changes its
    catcode) is text to TeX but a comment here. The manuscript uses none of
    them with a bare `%`; if it ever does, this errs towards HIDING text,
    which makes a presence check report MISSING -- loud, not silent.
    """
    i = line.find("%")
    while i != -1:
        n = 0
        j = i - 1
        while j >= 0 and line[j] == "\\":
            n += 1
            j -= 1
        if n % 2 == 0:
            return i
        i = line.find("%", i + 1)
    return -1


def split_comment(line: str) -> tuple[str, str]:
    """ONE line split into (code, comment) at the first UNESCAPED `%`.

    `comment` starts with that `%` and is "" when there is none; `code` is
    everything before it, as written. `code + comment == line` always. Used
    wherever code and comment must be treated differently -- e.g. the title
    page generator applies its rules to the code part only and emits the
    comment verbatim (P0.6, R2-a).
    """
    k = _comment_start(line)
    return (line, "") if k < 0 else (line[:k], line[k:])


def strip_comment_line(line: str) -> str | None:
    """ONE line with its comment removed, or None if the line IS a comment.

    None (not "") for a full-line comment, because a caller that splits
    paragraphs must tell a comment line -- which does not end a paragraph in
    TeX -- from a blank line, which does. A trailing comment is cut at the
    first UNESCAPED `%` (see `_comment_start`); the text before it is kept as
    written, and `\\%` is text.
    """
    code, comment = split_comment(line)
    if comment and not code.strip():
        return None
    return code


def strip_comments(text: str) -> str:
    """Remove LaTeX comments, keeping the order of everything else.

    Applies `strip_comment_line` to every line: a FULL-LINE comment is dropped
    entirely, line and all (line numbers are therefore NOT preserved; callers
    that need them, e.g. `promise_audit.sentences`, call `strip_comment_line`
    per line), and a TRAILING comment is cut at the first unescaped `%`.
    """
    out = [strip_comment_line(ln) for ln in text.splitlines()]
    return "\n".join(ln for ln in out if ln is not None)


#: The `\ref` family: commands whose argument is a LABEL and whose output is a
#: number LaTeX computes. ONE list, read by `make_variants` (refused on the
#: title page, which has no labels) and by `verify_numbers.uncovered_numbers`
#: (their arguments are not quantities). P0.6.
REF_COMMANDS = ("ref", "pageref", "eqref", "autoref", "cref", "Cref", "nameref",
                # P0.6 fix round 1 (M8): cleveref / varioref / subcaption forms.
                # cas-sc loads none of them, but a refusal costs nothing and an
                # excluded label argument is never a quantity.
                "vref", "cpageref", "subref", "autopageref", "labelcref")

#: A TeX inter-word separator: `~`, white space, or a spacing command (`\ `,
#: `\,`, `\;`, `\:`, `\!`). ONE class, read by `make_variants` (literal
#: cross-references on the title page) and by `verify_numbers` (page locators
#: in \cite notes); each caller adds its own quantifier. P0.6 fix round 2, N6.
TEX_SPACE = r"(?:~|\s|\\[ ,;:!])"

_PAIRS = {"{": "}", "[": "]", "(": ")"}


def group_end(text: str, i: int) -> int:
    """Index just past the delimited group that opens at `text[i]`.

    `text[i]` must be `{`, `[` or `(`. Nested groups of the same kind are
    counted, a brace group inside a bracket or paren group is skipped whole
    (so `[a={x]y}]` closes at the last `]`), and a backslash escapes the next
    character (`\\{`, `\\}`, `\\]`). An unbalanced group runs to the end of
    `text` -- the caller blanks too much rather than too little.
    """
    open_, close = text[i], _PAIRS[text[i]]
    depth, j, n = 0, i, len(text)
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "{" and open_ != "{":
            j = group_end(text, j)
            continue
        if c == open_:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n
