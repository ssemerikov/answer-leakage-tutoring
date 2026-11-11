"""Check every empirical number in the manuscript against the data it came from.

WHY THIS EXISTS. Success criterion 2 of the design spec is that every quantity in
`paper.tex` traces to a file in `data/`. Nothing enforced it, and the drift that
follows is silent: `config.py` carried
`corr(parsed_fraction, baseline) = 0.95` (honestly labelled "first 10 items")
long after the completed screen made it 0.892, which is what the manuscript
already said. Two files, two numbers, same name, no error anywhere. A reviewer
recomputing from the deposited data would have found it before we did.

WHAT IT DOES. Each check recomputes a quantity from the run records and asserts
that the rendered literal appears in `paper.tex`. It is an audit, not a parser:
we never try to read numbers out of LaTeX and guess what they mean, because a
regex that silently matches nothing passes just as quietly as the drift it was
meant to catch. Hence `MISSING` is a failure, not a skip.

PRESENCE IS TESTED ON THE COMMENT-STRIPPED TEXT (texutil.strip_comments), and
so is the absence of superseded literals: before 2026-11-02 a number that
survived only in a `%%` comment "verified". Since then the tool also: retires a
check only by an explicit RETIRED entry with a reason (an entry matching no
check is an ERROR); lints the abstract as the plain text Editorial Manager
receives, with every number pinned by a current check (LINT); checks
`highlights.txt` beside the manuscript; and counts the main text off the built
PDF against MAIN_TEXT_CAP (OVERLONG). Each of these makes the run exit 1.

Checks whose data does not exist yet are reported as PENDING and do not fail the
run -- the manuscript still carries `\\TBD` for those, which is the intended
state until the backend returns.

THE ONE EXCEPTION (P2-I2, 2026-11-11). Three checks LOCATE text in the
paper before comparing it: the rows of Table 3 (found by row label inside
their panel, so a cell may be typeset `-0.127` or `$-0.127$`), the stems and
reply of Table 4 (read as printed: `\\texttt{x}` is x), and the App A screen
figure (compared with `make_screen_figure.py`'s output). They still parse no
number's MEANING: each compares what is printed with what was computed, and
a row or passage that cannot be found makes the check MISSING, never PENDING.

KNOWN LIMITATION, stated rather than papered over. A short literal such as `40`
or `29` occurs in many places in a LaTeX file, so those checks prove the number
is *present*, not that it sits in the sentence that claims it. They catch drift
(the value changed and the prose did not) and not misplacement (the right value
in the wrong clause). The multi-digit checks -- `0.892`, `0.845`, `0.182` -- are
specific enough to be real. Do not read a green run as proof the prose is right;
read it as proof the prose is not stale.

Usage:
    python3 verify_numbers.py            # audit
    python3 verify_numbers.py -v         # show recomputed values too
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics as st
import subprocess
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as C  # noqa: E402  - the recorded pre-measurement ceiling
import texutil  # noqa: E402  - ONE comment stripper (P0.4 R1)
import solvability as S  # noqa: E402  - after sys.path, so this runs from any cwd
from analyse import mean_ci as A_mean_ci  # noqa: E402
from analyse import wilson_ci as S_wilson  # noqa: E402  - one Wilson, not two

ROOT = Path(__file__).resolve().parent.parent
PAPER = ROOT / "source" / "paper.tex"
RUNS = ROOT / "data" / "runs"
# Module-level so tests can redirect it the way they already redirect RUNS.
# Inline `ROOT / "harness" / "banks" / ...` left the bank reads unpatchable, so
# a test with a temporary runs directory silently read the REAL banks.
BANKS = ROOT / "harness" / "banks"
PDF = ROOT / "source" / "paper.pdf"

# CEAI: "a concise and factual abstract which does not exceed 250 words".
# Retrieved from the guide for authors via search -- ScienceDirect 403s a direct
# fetch, so treat it as well supported rather than verified, and re-check at
# submission. The action is unambiguous under either 250 or 300.
# P2-I2 (2026-11-11): kept at 250 for Computers & Education by the integration
# checklist's ruling ("ABSTRACT_LIMIT = 250 (C&E)"); still re-check at submission.
ABSTRACT_LIMIT = 250

# THE HIGHLIGHTS FILE LIVES BESIDE THE MANUSCRIPT IT SUMMARISES, so `main()`
# reads `PAPER.with_name(HIGHLIGHTS_NAME)` rather than a second module path: a
# test that redirects PAPER is redirected here for free, and cannot start
# reading the real file the day the C&E rewrite creates it.
HIGHLIGHTS_NAME = "highlights.txt"
HIGHLIGHTS_MIN, HIGHLIGHTS_MAX = 3, 5
HIGHLIGHT_MAX_CHARS = 85      # Elsevier: "maximum 85 characters, including spaces"

# Main text, counted off the build (see `main_text_words`). TARGET is where
# the rewrite aims; CAP is the hard ceiling that fails the tool.
MAIN_TEXT_TARGET = 7600
MAIN_TEXT_CAP = 8000

# RETIRING A CHECK IS AN EXPLICIT ACT WITH A REASON, never a deletion.
#
# label (exactly as `checks()` emits it) -> why it no longer applies. A
# retired check is reported as RETIRED, counts as neither verified nor
# missing, and its literal stops counting as CURRENT -- so it no longer
# protects a superseded twin from STALE and no longer pins a number in the
# abstract or highlights. A label here that matches no check is an ERROR: a
# typo must not silently retire nothing (4.36's silently-exempt failure).
#
# P2-I2 (2026-11-11), the C&E restructure. Reasons cite the number-triage
# disposition (docs/revision-2026-11/number_triage.csv; "row N" is its 0-based
# data row) and the integration checklist ruling that removed the sentence.
_DEPOSIT = ("moved to the deposited instrumentation report at the C&E "
            "restructure (triage DEPOSIT): the manuscript no longer prints it")
_IID = ("iid normal interval over replies, superseded in the Results by the "
        "item-resampled intervals at the C&E restructure (triage rows 114-124; "
        "checklist ruling); its literal survives only as an accidental twin")
_P4_DUP = "prose duplicate removed in P4; value checked by "
RETIRED: dict[str, str] = {
    # the kr pilot panel left the paper
    "n screened (Bank A, kr)": "kr pilot screen of the pre-rebalance bank; the "
        "pilot left the paper at the C&E restructure (triage row 0). 40 stays "
        "pinned by 'Bank A mcq items' and 'bank A mcq'",
    "n retained": "kr pilot retained count, never printed; '29' matched only "
        "inside other numbers (triage row 1)",
    "corr(parsed, baseline) all 40": "pilot parse/baseline correlation; "
        + _DEPOSIT + " (triage row 2); '0.89' matched only minimax's parse rate",
    "kr pilot parse rate (context only)": "pilot decline rate; " + _DEPOSIT
        + " (triage row 3)",
    "levels containing a discard": "computed from the kr pilot screen (11 "
        "discards) while App A describes the ol screen (triage rows 12-13, "
        "FLAG F1); replaced by 'App A: levels with a discard (ol)'",
    "rho(level, discard)": "kr pilot screen (triage rows 12-13, FLAG F1); "
        "replaced by 'App A: rho(level, discard) (ol)'",
    # the token-budget / truncation story (pilot 600-token screen)
    "truncated calls (numerator)": "pilot 600-token truncation; " + _DEPOSIT
        + " (triage row 14)",
    "truncated calls (denominator)": "pilot 600-token truncation; " + _DEPOSIT
        + " (triage row 15); '240' matched only the human-validation n",
    "truncation rate": "pilot 600-token truncation rate; " + _DEPOSIT
        + " (triage row 16)",
    "shift, >=5% truncated": "truncation-correction shift; " + _DEPOSIT
        + " (triage row 17)",
    "most negative shift": "truncation-correction shift; " + _DEPOSIT
        + " (triage row 18)",
    "truncation shift, noise floor (SE, calls)": "noise floor of the "
        "truncation comparison; " + _DEPOSIT + " (triage row 19)",
    # retries, routing, run scale
    "retry attempt cap": "retry policy; " + _DEPOSIT + " (triage row 30)",
    "stage abort threshold": "stage abort threshold; " + _DEPOSIT
        + " (triage row 31)",
    "overhead min": "router injected-context overhead; " + _DEPOSIT
        + " (triage row 32)",
    "overhead max": "router injected-context overhead; " + _DEPOSIT
        + " (triage row 33)",
    "overhead spread": "router injected-context overhead; " + _DEPOSIT
        + " (triage row 34)",
    "overhead drift (mean)": "router injected-context drift; " + _DEPOSIT
        + " (triage row 35)",
    "primary: transport failures": "duplicate rounding (0.1%) of the 2 of "
        "2400 failed screen calls, which App A states once as 0.08% (triage "
        "row 38, RETIRE)",
    "primary: corr(parsed, baseline)": "meaningful only against the pilot's "
        "0.89; " + _DEPOSIT + " (triage row 39); '0.20' matched only the "
        "rebalanced item's final-instrument baseline",
    "solver calls consumed": "run-scale total over every stage; " + _DEPOSIT
        + " (triage row 202)",
    # the iid regime intervals and the Bank A regime means cut from Section 4
    "P0 leak_adj CI low": _IID + " ('0.685' is the within-solver gain)",
    "P0 leak_adj CI high": _IID,
    "P1 leak_adj CI low": _IID,
    "P1 leak_adj CI high": _IID + " ('0.609' is the S2 mean and gemma4's "
        "all-40 accuracy)",
    "P2 leak_adj CI low": _IID,
    "P2 leak_adj CI high": _IID,
    "P3 leak_adj CI low": _IID,
    "P3 leak_adj CI high": _IID,
    "P2 mean leak_adj": "cut from Section 4 for length (D4 re-review K12; "
        "triage row 119); '0.552' matched only a pgfplots coordinate",
    "P3 mean leak_adj": "cut from Section 4 for length (D4 re-review K13; "
        "triage row 122); '0.583' is the A-L09-01 placebo rate",
    "gemma4:cloud mean leak_adj": "printed nowhere since the C&E restructure: "
        "Section 4.1 gives only the range of the tutor-model means (triage "
        "row 126; checklist 'Dropped rows'), pinned by 'Bank A tutor-model range'",
    "minimax-m3:cloud mean leak_adj": "printed nowhere since the C&E "
        "restructure (triage row 128); the range check pins the extremes",
    # human-side prevalence over all 240 (Section 4.4 now reports P1-P3 and P0)
    "consensus recovered": "all-240 consensus count printed nowhere since the "
        "C&E restructure; Section 4.4 reports the mitigated 99 of 180 and P0 "
        "39 of 60 (triage rows 227-232); '138' only twins a coordinate",
    "consensus rate": "printed nowhere since the C&E restructure (triage rows "
        "227-232)",
    "consensus CI": "printed nowhere since the C&E restructure (triage rows "
        "227-232); a loosened check would pass on App C's '0.512'",
    "liberal recovered": "printed nowhere since the C&E restructure (triage "
        "rows 227-232)",
    "liberal rate": "printed nowhere since the C&E restructure (triage rows "
        "227-232); a loosened check would pass on App C's '87.9' cell",
    "liberal CI": "printed nowhere since the C&E restructure (triage rows "
        "227-232)",
    # P4 (2026-11-11, presentation round; controller ruling R2.4): the prose copy
    # of each value left Section 4; the value is still printed and checked once,
    # in its Table 3 row.
    "RQ2: P1-P0 both banks": _P4_DUP + "'Table 3a row P1-P0'",
    "RQ2: P2-P1 per bank": _P4_DUP + "'Table 3a row P2-P1'",
    "RQ2: P2-P1 pooled": _P4_DUP + "'Table 3b row P2-P1'",
    "RQ2: P3-P1, Bank A": _P4_DUP + "'Table 3a row P3-P1'",
    "RQ3: pooled S3-S2 interval": _P4_DUP + "'Table 3b row S3-S2'",
    "RQ4: joint test df": _P4_DUP + "'Table 3b: joint test df and p'",
}

# Numbers allowed in the abstract (and highlights) WITHOUT a check computing
# them: number -> reason. Normalised form, e.g. "2,016", "98" for 98%. Every
# entry is a decision a reviewer can read; an empty table means every number
# in the abstract is backed by a recomputation.
ABSTRACT_NUMBER_ALLOW: dict[str, str] = {}


def _letters(tok: str) -> str:
    return re.sub(r"[^a-z]", "", tok.lower())


def _pdf_staleness(pdf: Path) -> str | None:
    """ONE answer to "is the build current?", for every count taken off it.

    Returns None when `pdf` exists and is not older than PAPER, else the
    reason it cannot be read as the manuscript. Both `abstract_words` and
    `main_text_words` ask this function; two copies of a freshness rule are
    two rules (4.33).
    """
    if not pdf.exists():
        return f"no build at {pdf.name} -- run make"
    if pdf.stat().st_mtime < PAPER.stat().st_mtime:
        return f"{pdf.name} is older than paper.tex -- run make"
    return None


def abstract_words(tex: str, pdf: Path = PDF) -> tuple[int | None, str]:
    r"""Count the abstract as RENDERED, because there is only one rendering.

    This number has been produced by hand four times across the life of the
    manuscript -- 292, 265, 249 and 247 -- and every one of them came from a
    fresh macro stripper written at the moment it was needed. They disagree for
    a reason that is invisible unless you look: replacing `$` with a space turns
    `($27$ items` into `( 27 items`, inventing a token, while deleting it turns
    `\Delta` into nothing and destroys one. A stripper is a second
    implementation of LaTeX and it will not agree with the first (4.33).

    So the count is taken off the built PDF, which is what a reader sees and
    what a paste into Editorial Manager carries. The abstract is located by
    anchors DERIVED FROM THE SOURCE each run -- its own first and last three
    words -- so a rewrite moves the anchors with it. Tokens are compared
    letters-only, which makes `game's` and the PDF's `game’s` the same word.

    Returns (count, note). A count of None is not a pass: it means the PDF is
    missing or older than the source, so the question was not asked.
    """
    # Located in the COMMENT-STRIPPED text, as `abstract_lint` does: a
    # commented-out old abstract above the new one used to win the search.
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}",
                  texutil.strip_comments(tex), re.S)
    if not m:
        return None, "no abstract environment in paper.tex"
    body = m.group(1)
    src_toks = [t for t in _letters_seq(body) if t]
    if len(src_toks) < 6:
        return None, "abstract too short to anchor"
    stale = _pdf_staleness(pdf)
    if stale:
        return None, stale

    try:
        out = subprocess.run(["pdftotext", "-f", "1", "-l", "1", str(pdf), "-"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"pdftotext unavailable ({e})"
    if out.returncode != 0:
        return None, "pdftotext failed on page 1"
    page = out.stdout.replace("-\n", "").split()
    keys = [_letters(t) for t in page]

    def find(seq, start=0):
        n = len(seq)
        for i in range(start, len(keys) - n + 1):
            if keys[i:i + n] == seq:
                return i
        return -1

    head, tail = src_toks[:3], src_toks[-3:]
    i = find(head)
    if i < 0:
        return None, "abstract head not found on page 1 of the PDF"
    j = find(tail, i)
    if j < 0:
        return None, "abstract tail not found after its head"
    return len(page[i:j + len(tail)]), "rendered, page 1"


def _letters_seq(body: str) -> list[str]:
    """Letters-only tokens of a LaTeX fragment, for ANCHORING only.

    Deliberately crude: it has to find three words, not count them, so its
    disagreements with the renderer do not reach the number.
    """
    s = re.sub(r"\\[a-zA-Z]+\*?", " ", body)
    s = re.sub(r"[${}~\\]", " ", s)
    return [_letters(t) for t in s.split()]


# ---------------------------------------------------------------------------
# NUMBERS IN PLAIN TEXT (abstract lint, highlights) -- P0.4 R3/R4
# ---------------------------------------------------------------------------

# A standalone quantity; documented piece by piece in `number_tokens`.
NUMBER_RE = re.compile(r"""
    (?<![A-Za-z0-9_.])
    (?<![A-Za-z]-)
    (?:
        (?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?
      | \.\d+
    )
    (?:[eE][-+]?\d+)?
    (?:%|[A-Za-z]+)?
    (?![A-Za-z0-9_])
    (?!\.\d)
""", re.X)

# Spans masked BEFORE tokenising, because the number in them NAMES something
# rather than counts it: a version (`Python 3`), a cross-reference
# (`Section 4.5`, `Table 2`), a parameterised index (`ICC(2,1)`), a model name
# written with spaces (`Llama 3.1 70B`, `Claude 3.5 Sonnet`) or a model tag
# (`gemma4:31b`). The lists are short on purpose. A missing entry costs a LINT
# that names the token -- loud; an over-broad entry would mask a real
# quantity, which would then never be pinned -- silent. Add a name only when
# it is a NAME, never a count.
_NAMED_NUMBER_RE = re.compile(
    r"\b(?:Python|Section|Sections|Sec\.|Table|Tables|Figure|Figures|Fig\.|"
    r"Eq\.|Equation|Appendix|Chapter|Tier|Version)[\s~]*\d+(?:\.\d+)*")
_INDEX_RE = re.compile(r"\b[A-Za-z]+\(\d+(?:\s*,\s*\d+)*\)")
# A model family's version is masked when ATTACHED or hyphenated (Qwen3,
# Llama-3.1, GPT-4o) or, written with a space, only when it is a decimal or
# carries a size letter (Llama 3.1 70B, Mistral 7B) -- never a bare count:
# "Gemma 27 items" is 27 items (fix round 2, N-b).
_MODEL_NAME_RE = re.compile(
    r"\b(?:Llama|Claude|Gemini|Gemma|Mistral|Mixtral|Qwen|Phi|GPT|DeepSeek|"
    r"MiniMax|Kimi|OLMo|Falcon|Grok|Yi)"
    r"(?:-?v?\d+(?:\.\d+)*[A-Za-z]{0,2}"
    r"|\s+(?:\d+\.\d+(?:\.\d+)*[A-Za-z]{0,2}|\d+[A-Za-z]{1,2})\b)"
    r"(?:[\s-]+\d+(?:\.\d+)?[BbMm]\b)?")
# name:tag only when the name carries a digit or a hyphen (gemma4:31b,
# gpt-oss:120b) AND the tag is one of the forms Ollama uses -- a size
# (27b, 1.5b, 7m), `latest`, or letters and hyphens (cloud, it-qat). A bare
# numeric tag is a quantity: "S3:0.6", "P2:-0.05" (fix round 2, N-b); a plain
# word:value such as `kappa:.56` was never masked.
_MODEL_TAG_RE = re.compile(
    r"\b[A-Za-z](?=[\w.-]*[\d-])[\w.-]*:"
    r"(?:\d+(?:\.\d+)?[bmBM]\b|latest\b|[A-Za-z][A-Za-z-]*)")

# A letter-led hyphenated compound is a NAME, digits and all: A-L09-01,
# gpt-oss-120b, SHA-256, Foo-3.5 (P0.6: item ids yielded "01"). A digit-led
# run (3-4, 0.033--0.450) is not matched, so a range stays two quantities.
_HYPHEN_ID_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9.]*[A-Za-z0-9])+")
_DECIMAL_SEGMENT_RE = re.compile(r"\d*\.\d+")


def _mask_hyphen_id(m: re.Match) -> str:
    """Mask a letter-led hyphenated compound, EXCEPT trailing decimal
    segments: "S3-S2-0.024" is a contrast and its value, "Bank~B-0.05" a name
    and a minus 0.05 (P0.6 fix round 1, M5). A decimal followed by a
    letter-bearing segment stays part of the name (Llama-3.1-70B, A-0.5-B);
    an integer after a hyphen stays an identifier (GPT-4, top-10 -- F1)."""
    segs = m.group(0).split("-")
    keep = len(segs)
    while keep > 1 and _DECIMAL_SEGMENT_RE.fullmatch(segs[keep - 1]):
        keep -= 1
    head = "-".join(segs[:keep])
    return " " * len(head) + m.group(0)[len(head):]


def normalise_numeric(text: str) -> str:
    r"""LaTeX number forms to plain text: `{,}`->`,`, drop `$`, `\%`->`%`,
    and a space between a control word and a digit glued to it (`\pm0.09`
    -> `\pm 0.09`; P0.6 fix round 1, M6 -- otherwise the digit reads as part
    of the control word's name and no token is found)."""
    text = text.replace("{,}", ",").replace("$", "").replace("\\%", "%")
    return re.sub(r"(\\[A-Za-z]+)(?=[\d.])", r"\1 ", text)


def number_tokens(text: str) -> list[str]:
    r"""Standalone quantities in `text`, in order, normalised for comparison.

    Integers that are part of an identifier or a name are NOT numbers --
    `RQ1`, `P0`, `S3`, `Python 3`, `ICC(2,1)`, `SHA-256`, `GPT-4o`,
    `Llama-3.1`, `Qwen3`, `gemma4:31b`, `Section 4.5`, version `2.3.0` --
    only standalone quantities count, and every form of one counts: `0.605`,
    `.56`, `2,016`, `98%`, `16k`, `5x`, `1e-20`. Two steps:

    1. MASK spans whose number names something (replaced by spaces):
       `_NAMED_NUMBER_RE`  a name word then a number: Python 3, Section 4.5,
                           Table 2, Fig. 3, Eq. 1, Appendix 2, Tier 1 ...
       `_INDEX_RE`         a parameterised index: ICC(2,1)
       `_MODEL_NAME_RE`    a model family then its version and size, written
                           with spaces: Llama 3.1 70B, Claude 3.5 Sonnet
       `_MODEL_TAG_RE`     a name:tag model id: gemma4:31b, gpt-oss:120b
       `_HYPHEN_ID_RE`     a letter-led hyphenated compound: A-L09-01, SHA-256
    2. MATCH `NUMBER_RE` on what is left:
       (?<![A-Za-z0-9_.])          not glued to an identifier or another
                                   number: RQ1, P0, S3, gemma4, the 5 of 1.5
       (?<![A-Za-z]-)              not the tail of a hyphenated name:
                                   SHA-256, GPT-4o, Tier-1, P1-P0, Llama-3.1
       (?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?   27, 2,016, 0.605
         | \.\d+                   or a leading-dot decimal (APA): .556
       (?:[eE][-+]?\d+)?           e-notation: 1e-20, 2.5E+3
       (?:%|[A-Za-z]+)?            a percent sign, or a unit/letter suffix
                                   that stays in the token: 16k, 5x, 2nd
       (?![A-Za-z0-9_])            nothing glued after it
       (?!\.\d)                    not the head of a version triple: 2.3.0

    NORMALISATION, identical on both sides of a comparison: a trailing `%`
    is dropped (`98%` -> `98`) and a leading-dot decimal gains its zero
    (`.556` -> `0.556`). A sign is not part of the token (`-0.144` ->
    `0.144`), so pinning cannot catch a flipped sign; the literal presence
    checks can, because they compare the whole typeset literal.
    Call on text already passed through `normalise_numeric` if it is LaTeX.
    """
    for rx in (_NAMED_NUMBER_RE, _INDEX_RE, _MODEL_NAME_RE, _MODEL_TAG_RE):
        text = rx.sub(lambda m: " " * len(m.group(0)), text)
    text = _HYPHEN_ID_RE.sub(_mask_hyphen_id, text)
    out = []
    for m in NUMBER_RE.finditer(text):
        tok = m.group(0).rstrip("%")
        out.append("0" + tok if tok.startswith(".") else tok)
    return out


def pinned_numbers(literals) -> set[str]:
    """Every number token any current check literal contains.

    Pinning is TOKEN-SET MEMBERSHIP, never substring: `2` is a substring of
    `0.620` and of `2/378`'s neighbour `378`, and a substring test would make
    nearly every small integer "pinned" by accident. The guarantee is modest
    -- some check recomputes this number somewhere -- and stated as such.

    The token set of `_pin_index` -- one composition of `number_tokens` and
    `normalise_numeric` over the literals, not two (P0.6 fix round 2, N6).
    """
    return set(_pin_index(literals))


def _unpinned(text: str, literals) -> list[str]:
    """Distinct numbers in `text` that no literal pins and the allow list
    does not excuse, in order of first appearance."""
    pinned = pinned_numbers(literals)
    seen: list[str] = []
    for n in number_tokens(normalise_numeric(text)):
        if n not in pinned and n not in ABSTRACT_NUMBER_ALLOW and n not in seen:
            seen.append(n)
    return seen


_GREEK_MACROS = frozenset("""
    alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota
    kappa varkappa lambda mu nu xi omicron pi varpi rho varrho sigma varsigma
    tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma
    Upsilon Phi Psi Omega""".split())


def _is_greek(ch: str) -> bool:
    if "\u0370" <= ch <= "\u03ff" or "\u1f00" <= ch <= "\u1fff":
        return True
    return "GREEK" in unicodedata.name(ch, "")   # incl. mathematical italic


def abstract_lint(tex: str, literals: set[str]) -> list[str]:
    r"""Problems that would stop the abstract surviving as PLAIN TEXT.

    Editorial Manager takes the abstract as a pasted string, so LaTeX in it
    arrives as LaTeX: `$0.605$`, `2{,}016`, `\kappa`. Applied to the source of
    the `abstract` environment AFTER comment stripping. Problems, one per
    distinct offender with a count: any `$`; any macro other than `\%`;
    `{,}`; Greek letters (macro or Unicode); and every number token that no
    CURRENT check literal pins (`pinned_numbers`) unless ABSTRACT_NUMBER_ALLOW
    excuses it. Empty list = clean. No abstract = nothing to lint (the word
    count already reports a missing abstract as PENDING).
    """
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}",
                  texutil.strip_comments(tex), re.S)
    if not m:
        return []
    src = m.group(1)
    probs: list[str] = []
    k = src.count("$")
    if k:
        probs.append(f"`$` math mode ({k}x) -- arrives in Editorial Manager "
                     f"as literal dollar signs")
    k = src.count("{,}")
    if k:
        probs.append(f"`{{,}}` thousands separator ({k}x) -- write 2,016 as plain text")
    # Fix round 1 (M10): ties, TeX dashes and braces also arrive verbatim.
    k = src.count("~")
    if k:
        probs.append(f"`~` tie ({k}x) -- arrives as a literal tilde; use a space")
    k = len(re.findall(r"(?<!-)---(?!-)", src))
    if k:
        probs.append(f"`---` TeX em dash ({k}x) -- write the dash character")
    k = len(re.findall(r"(?<!-)--(?!-)", src))
    if k:
        probs.append(f"`--` TeX en dash ({k}x) -- write the dash character")
    k = sum(src.replace("{,}", "").count(c) for c in "{}")
    if k:
        probs.append(f"`{{`/`}}` braces ({k}x) -- grouping arrives as literal braces")
    macros: Counter = Counter()
    greek: Counter = Counter()
    for mm in re.finditer(r"\\([A-Za-z]+|.)", src, re.S):
        name = mm.group(1)
        if name == "%":
            continue
        (greek if name in _GREEK_MACROS else macros)["\\" + name] += 1
    for ch in src:
        if _is_greek(ch):
            greek[ch] += 1
    for name, k in macros.items():
        probs.append(f"macro {name} ({k}x) -- not plain text")
    for g, k in greek.items():
        probs.append(f"Greek letter {g} ({k}x) -- spell it out")
    for n in _unpinned(src, literals):
        probs.append(f"unpinned number {n} -- no current check literal computes "
                     f"it (pin it, or allow it in ABSTRACT_NUMBER_ALLOW with a reason)")
    return probs


def highlights_check(path: Path, literals: set[str]) -> tuple[str, list[str]]:
    """Check the highlights file: ('PENDING'|'OK'|'FAIL', problems).

    PENDING when the file does not exist yet. Otherwise: HIGHLIGHTS_MIN to
    HIGHLIGHTS_MAX non-empty lines (blank lines are ignored); each at most
    HIGHLIGHT_MAX_CHARS Unicode characters including spaces, counted after
    trimming the line; every number pinned exactly as in `abstract_lint`
    (same tokens, same allow list -- a highlight restates the abstract).
    """
    if not path.exists():
        return "PENDING", []
    lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    probs: list[str] = []
    if not HIGHLIGHTS_MIN <= len(lines) <= HIGHLIGHTS_MAX:
        probs.append(f"{len(lines)} highlights; {HIGHLIGHTS_MIN} to "
                     f"{HIGHLIGHTS_MAX} are required")
    for i, line in enumerate(lines, 1):
        if len(line) > HIGHLIGHT_MAX_CHARS:
            probs.append(f"highlight {i} is {len(line)} characters (limit "
                         f"{HIGHLIGHT_MAX_CHARS}): {line[:50]}...")
        for n in _unpinned(line, literals):
            probs.append(f"highlight {i}: unpinned number {n}")
    return ("FAIL" if probs else "OK"), probs


# ---------------------------------------------------------------------------
# MAIN-TEXT LENGTH, OFF THE BUILD -- P0.4 R5
# ---------------------------------------------------------------------------

# THE SCOPE ANCHOR, one rule for the word count and the coverage report
# (`_intro_anchor`). Accepted forms (P0.6 fix round 1, C2 ruling):
# \section{Introduction}, \section[...]{Introduction},
# \section{\label{...}Introduction} and \section*{Introduction}. Anything else
# is NOT an anchor -- the coverage gate then refuses rather than scanning an
# empty scope. \section{Introduction\label{...}} stays refused (P0.6 fix
# round 2, N3 ruling): the integrator writes \section{Introduction}\label{...}.
_INTRO_RE = re.compile(
    r"\\section\*?\s*(?:\[[^\]]*\]\s*)?\{\s*(?:\\label\s*\{[^{}]*\}\s*)?"
    r"Introduction\s*\}", re.I)
_DOCUMENT_BEGIN_RE = re.compile(r"\\begin\s*\{\s*document\s*\}")
_SECTION_TOKEN_RE = re.compile(r"\\section(?![A-Za-z])")
_NO_ANCHOR = "no \\section{Introduction}"
_ANCHOR_HINT = ("the anchor is the first \\section after \\begin{document}; write "
                "\\section{Introduction}\\label{...}, not "
                "\\section{Introduction\\label{...}}")
_NO_ANCHOR_NOTE = f"{_NO_ANCHOR} in paper.tex -- {_ANCHOR_HINT}"


def _intro_anchor(code: str) -> re.Match | None:
    r"""THE scope anchor in comment-stripped `code`, or None.

    The anchor is the FIRST `\section` (starred or not) after
    `\begin{document}` -- from the start of `code` when it has none (a
    fragment) -- and only when that heading is an accepted Introduction form
    (`_INTRO_RE`). P0.6 fix round 2, N3: the first `_INTRO_RE` match ANYWHERE
    let a late `\section*{Introduction}` (a prompt quoted in an appendix)
    become the anchor when the real heading was renamed or written
    `\section{Introduction\label{...}}` -- the body dropped out of the
    coverage scan and the word count started at the decoy. One function, read
    by `_scoped_code`, `_count_main_text` and `main_text_words`.
    """
    doc = _DOCUMENT_BEGIN_RE.search(code)
    first = _SECTION_TOKEN_RE.search(code, doc.end() if doc else 0)
    return _INTRO_RE.match(code, first.start()) if first else None


def _anchor_hint(refusal: str | None) -> str:
    """What to append to a printed refusal: the no-anchor one says how to
    write the heading (P0.6 fix round 2, N3)."""
    return f" -- {_ANCHOR_HINT}" if refusal == _NO_ANCHOR else ""


# The first of these, after the Introduction, ends the main text. Each is
# found in the SOURCE, and the count stops at the HEADING it renders. For a
# `\section*{...}` or an appendix `\section{...}` that heading is the source
# title; `\printcredits` and `\bibliography` render a heading the class or the
# bibliography style supplies, which the source cannot contain -- those two
# strings are `_RENDERED_HEADINGS`, the only page text written here.
# ONE list of back-matter section titles, read by the main-text count and by
# the uncovered-numbers report (P0.6). Matched as FULL titles (P0.6 fix round
# 1, M7): a body section "Ethics of answer leakage" or "Funding models" is not
# back matter, and the prefix rule used to blank it -- and to end the word
# count early.
_BACK_MATTER_TITLES = (
    "Declaration of competing interest", "Declaration of competing interests",
    "Declaration of interest", "Declaration of interests", "Competing interests",
    "CRediT authorship contribution statement", "Funding", "Funding sources",
    "Ethics", "Ethics statement", "Data availability", "Data availability statement",
    "Declaration of generative AI and AI-assisted technologies in the writing process",
    "Declaration of generative AI", "References")
_MAIN_TEXT_END_RES = (
    ("section", re.compile(
        r"\\section\*?\s*\{\s*((?:" + "|".join(map(re.escape, _BACK_MATTER_TITLES))
        + r"))\s*\}", re.I)),
    ("printcredits", re.compile(r"\\printcredits\b")),
    ("bibliography", re.compile(r"\\bibliography\s*\{|\\begin\s*\{thebibliography\}"
                                r"|\\printbibliography\b")),
    ("appendix", re.compile(r"\\appendix\b")),
)
_RENDERED_HEADINGS = {   # cas-sc.cls / cas-model2-names render these
    "printcredits": "CRediT authorship contribution statement",
    "bibliography": "References",
}
_FURN_TOP, _FURN_BOTTOM = 2, 3    # page-edge lines eligible as furniture
_HEADING_WRAP = 3                 # a heading may wrap over this many lines
# A rendered heading's number: "1. ", "4.5 ", "A. ", "Appendix A. "
_HEADING_NUMBER_RE = re.compile(r"^(?:Appendix\s+)?(?:[A-Z]|\d+)(?:\.\d+)*\.?\s+")


def _heading_text(s: str) -> str:
    """A heading as it PRINTS, for a strict comparison (fix round 2, N-a).

    Case and punctuation are kept: a CAS heading is printed in its source
    case with no trailing full stop, so "references." or "funding." at the
    end of a paragraph is not the heading. Only what TeX itself changes is
    normalised: macros and braces go, `~` is a space, `---`/`--` are the
    dashes they print, and typographic quotes equal straight ones.
    """
    s = re.sub(r"\\[A-Za-z]+\*?", " ", s).replace("{", "").replace("}", "")
    s = s.replace("~", " ").replace("---", "\u2014").replace("--", "\u2013")
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("`", "'")
    return " ".join(s.split())


def _heading_lines(lines: list[str], title: str, start: int = 0) -> list[int]:
    """Indices of lines that ARE the heading `title` exactly as it prints
    (`_heading_text`; a number prefix such as "1. " or "A. " is allowed),
    including a heading wrapped over up to _HEADING_WRAP lines."""
    want = _heading_text(title)
    hits = []
    for i in range(start, len(lines)):
        got = _heading_text(_HEADING_NUMBER_RE.sub("", lines[i].strip(), count=1))
        if not got:
            continue
        j = i
        while got != want and want.startswith(got + " ") and j - i < _HEADING_WRAP - 1 \
                and j + 1 < len(lines):
            j += 1
            got = f"{got} {_heading_text(lines[j])}".strip()
        if got == want:
            hits.append(i)
    return hits


def _strip_page_furniture(text: str) -> tuple[str, int]:
    """Remove running heads and feet from `pdftotext` output.

    A line is furniture when it sits among the first _FURN_TOP or last
    _FURN_BOTTOM non-empty lines of a page AND the same line (digits
    normalised, so `Page 3 of 20` matches `Page 4 of 20`) does so on at least
    half the pages. Restricting to page EDGES is what keeps a numeric table
    cell that recurs on many pages from being mistaken for furniture.
    Derived from the PDF, never from hard-coded page text, so a new short
    title or author line is recognised without an edit here.

    Returns (text with pages joined by newlines, number of tokens removed).
    """
    pages = [pg.split("\n") for pg in text.split("\f")]
    nonempty = [[i for i, l in enumerate(ls) if l.strip()] for ls in pages]
    n_pages = sum(1 for ne in nonempty if ne)

    def norm(line: str) -> str:
        return re.sub(r"\d+", "#", " ".join(line.split()))

    edges = [set(ne[:_FURN_TOP] + ne[-_FURN_BOTTOM:]) for ne in nonempty]
    counts: Counter = Counter()
    for ls, idx in zip(pages, edges):
        counts.update({norm(ls[i]) for i in idx})
    need = max(2, -(-n_pages // 2))
    furniture = ({k for k, c in counts.items() if c >= need}
                 if n_pages >= 2 else set())
    removed = 0
    out = []
    for ls, idx in zip(pages, edges):
        keep = []
        for i, line in enumerate(ls):
            if i in idx and norm(line) in furniture:
                removed += len(line.split())
                continue
            keep.append(line)
        out.append("\n".join(keep).strip("\n"))
    return "\n".join(out), removed


def _count_main_text(pdf_text: str, tex: str,
                     strip_furniture: bool = True) -> tuple[int | None, str]:
    """Count the main text in `pdftotext` output, between two HEADINGS.

    Span: from the Introduction heading to the heading of whichever comes
    first in the comment-stripped source after it -- a back-matter
    `\\section*` (competing interest, CRediT, Funding, Ethics, Data
    availability, generative-AI declaration, References), `\\printcredits`,
    the bibliography, or `\\appendix` (its first `\\section`). Anchoring on
    headings rather than on the first and last body words means a citation
    or a formula in either sentence cannot break the count (fix round 1, F2).

    Refuses (None) rather than guesses when the source has no scope anchor
    (`_intro_anchor`: the first `\\section` after `\\begin{document}` must be
    the Introduction -- P0.6 fix round 2, N3), when the Introduction heading
    occurs more than once (a contents list would make the count stop at its
    copy of the end heading -- an under-count, the dangerous direction for a
    cap) or when the end heading is not found after it, or found MORE than
    once (fix round 2, N-a: a body line or table cell that prints exactly like
    the title would otherwise end the count early). Headings are matched as
    they print (`_heading_text`): case kept, no trailing full stop.

    What is counted: every whitespace token from the Introduction heading line
    (its number included) up to the end heading line -- tables, captions,
    footnotes and display maths inside the span included -- minus running
    heads and feet (`_strip_page_furniture`; `strip_furniture=False` exists
    for the test that proves the stripping removes something). Page-1
    front-matter footnotes (corresponding-author mark, e-mails, ORCIDs) fall
    inside the span and ARE counted: a small over-count, on the side the cap
    protects.
    """
    body = texutil.strip_comments(tex)
    m = _intro_anchor(body)
    if not m:
        return None, _NO_ANCHOR_NOTE
    found = [(e.start(), kind, e) for kind, rx in _MAIN_TEXT_END_RES
             for e in [rx.search(body, m.end())] if e]
    if not found:
        return None, ("no back matter, bibliography or appendix after the "
                      "Introduction in paper.tex")
    _, kind, e = min(found, key=lambda t: t[0])
    if kind == "section":
        end_title = e.group(1)
    elif kind == "appendix":
        sec = re.search(r"\\section\*?\s*\{([^{}]*)\}", body[e.end():])
        if not sec:
            return None, "\\appendix with no \\section after it in paper.tex"
        end_title = sec.group(1)
    else:
        end_title = _RENDERED_HEADINGS[kind]

    if strip_furniture:
        text, n_furn = _strip_page_furniture(pdf_text)
    else:
        text, n_furn = pdf_text.replace("\f", "\n"), 0
    lines = text.split("\n")
    starts = _heading_lines(lines, "Introduction")
    if not starts:
        return None, "Introduction heading not found in the PDF"
    if len(starts) > 1:
        return None, (f"Introduction heading found {len(starts)} times in the "
                      f"PDF -- refusing to guess where the main text starts")
    ends = _heading_lines(lines, end_title, starts[0] + 1)
    if not ends:
        return None, f"end heading {end_title!r} not found after the Introduction"
    if len(ends) > 1:
        # a table cell or a body line that prints exactly like the title:
        # stopping at the first would under-count (fix round 2, N-a)
        return None, (f"end heading {end_title!r} found {len(ends)} times after "
                      f"the Introduction -- refusing to guess where the main "
                      f"text ends")
    span = "\n".join(lines[starts[0]:ends[0]]).replace("-\n", "")
    return len(span.split()), (f"rendered, Introduction to {end_title!r}; running "
                               f"heads/feet removed ({n_furn} tokens "
                               f"document-wide); tables, captions and footnotes "
                               f"in the span counted")


def main_text_words(tex: str, pdf: Path = PDF) -> tuple[int | None, str]:
    """Main-text word count off the BUILT PDF (see `_count_main_text`).

    Same contract as `abstract_words`: None is not a pass, it means the
    question was not asked -- no build, a stale build (`_pdf_staleness`, the
    one freshness rule), or anchors that could not be found.
    """
    body = texutil.strip_comments(tex)
    if not _intro_anchor(body):
        return None, _NO_ANCHOR_NOTE
    stale = _pdf_staleness(pdf)
    if stale:
        return None, stale
    text = _pdftotext(pdf)
    if text is None:
        return None, "pdftotext failed or unavailable"
    return _count_main_text(text, tex)


def _pdftotext(pdf: Path) -> str | None:
    """The whole document as `pdftotext` renders it, or None."""
    try:
        out = subprocess.run(["pdftotext", str(pdf), "-"],
                             capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


# ---------------------------------------------------------------------------
# THE UNCOVERED-NUMBERS REPORT -- P0.6 (fix round 1)
# ---------------------------------------------------------------------------
# "Every number in the new text gets a check." Without a tool that rule rests
# on discipline, which is how the paper came to hold ~34 unchecked quantities
# (number_triage.md §c). `uncovered_numbers` lists every number token in the
# main text and appendices that no current check literal carries.

@dataclass
class Uncovered:
    kind: str        # "UNCOVERED" | "NEW" | "INFO literature" | "INFO year"
    token: str       # normalised as number_tokens() normalises it
    line: int        # 1-based line in paper.tex
    context: str     # ~60 characters of the source line around the token
    note: str = ""
    strict_fail: bool = False   # an INFO year that --strict-coverage counts

    @property
    def label(self) -> str:
        return "NEW (check pending)" if self.kind == "NEW" else self.kind


@dataclass
class Coverage:
    """What `_coverage_scan` found. `refusal` not None means the scan is NOT
    a measurement (no scope anchor, or a scope holding no number at all) and
    the gate must fail rather than report "0 uncovered" (fix round 1, C2)."""
    records: list
    n_tokens: int
    n_covered: int
    covered: list          # (line, token, [labels of the checks pinning it])
    refusal: str | None = None


def _current_literals(results) -> dict[str, str]:
    """{label: literal} of every CURRENT check in `results` (a `checks()`
    list): its literal is computed and its label is not RETIRED. The one
    place this filter is written (P0.6 fix round 2, N6): main()'s presence,
    STALE, lint, highlight and coverage checks and `uncovered_numbers`'
    default read it."""
    return {label: lit for label, lit, _ in results
            if lit and label not in RETIRED}


class ScopeNotAnchored(ValueError):
    """`uncovered_numbers` was asked to scan a paper whose scope is not a
    measurement: no scope anchor, or an anchored scope holding no number
    token (fix round 1, C2; P0.6 fix round 2, N4)."""


_YEAR_RE = re.compile(r"(?:19|20)\d\d")
# TAGS, read from the RAW line's comment part. Well-formed:
#   %% NEW: <source>              (a parenthesised qualifier is allowed:
#   %% NEW (<qualifier>): <source>  the drafts use it, and it is unambiguous)
#   %% LIT: <bibkey> p.<n>[ trailing text]   (pp.<n>-<m> too)
#   %% YEAR[ anything]
# Anything else that LOOKS like one of these (`%% new: ...`, `%% NEW` with no
# colon, `%% lit: ...`, `% year ...`) is a NEAR-MISS: reported, never ignored.
_NEW_TAG_RE = re.compile(r"%+\s*NEW\s*(?:\(([^()]*)\))?\s*:\s*(\S.*?)\s*$")
_NEW_EMPTY_RE = re.compile(r"%+\s*NEW\s*(?:\([^()]*\))?\s*:\s*$")
_LIT_TAG_RE = re.compile(r"%+\s*LIT\s*:(.*)$")
_LIT_BODY_RE = re.compile(
    r"\s*([A-Za-z][\w:.+-]*)\s+pp?\.\s*\d+(?:\s*[-\u2013]+\s*\d+)?(?:\s.*)?$")
_YEAR_TAG_RE = re.compile(r"%+\s*YEAR\b")
_TAGLIKE_RE = re.compile(r"%+\s*((?i:new|lit|year))\b")
# ONE definition of "a citation command" (P0.6 fix round 2, N6): \cite, \citep,
# \citet, \citeauthor, \Cite ... `_CITE_CMD_RE` asks whether a line CITES (the
# LIT and year rules); `_CITE_RE` names the commands whose arguments are keys,
# which adds \nocite -- it prints no citation, so it never makes a line cite.
_CITE_NAME = r"[Cc]ite[A-Za-z]*"
_CITE_CMD_RE = re.compile(r"\\" + _CITE_NAME + r"\*?\s*[\[{]")
_BIB_CMD_RE = re.compile(r"\\bibliography\s*\{([^{}]*)\}")
_BIB_KEY_RE = re.compile(r"@\s*([A-Za-z]+)\s*[{(]\s*([^,\s]+)\s*,")
_BACK_MATTER_START_RE = re.compile(
    r"\\section\*?\s*\{\s*(?:" + "|".join(map(re.escape, _BACK_MATTER_TITLES))
    + r")\s*\}|\\printcredits\b", re.I)
# A back-matter region ends at the next section whose title is NOT a
# back-matter title, at \appendix, or at \end{document}.
_SECTION_RE = re.compile(r"\\section\*?\s*\{([^{}]*)\}")
_BACK_TITLE_RE = re.compile(r"\s*(?:" + "|".join(map(re.escape, _BACK_MATTER_TITLES))
                            + r")\s*$", re.I)

# Commands whose arguments never carry a reported quantity:
# name -> (optional-group openers allowed, mandatory brace groups consumed).
# Only the listed mandatory groups go -- \multicolumn{3}{c}{TEXT} keeps TEXT.
# \cite* is handled apart: its optional notes stay IN scope except for page
# locators (fix round 1, M4: "\citep[reporting 32\%;][]" carries a quantity).
_EXCLUDED_ARGS: dict[str, tuple[str, int]] = {
    **{r: ("", 1) for r in texutil.REF_COMMANDS}, "auxref": ("", 1),
    "label": ("", 1), "includegraphics": ("[", 1), "input": ("", 1),
    "include": ("", 1), "usepackage": ("[", 1), "RequirePackage": ("[", 1),
    "url": ("", 1), "href": ("", 1), "hspace": ("", 1), "vspace": ("", 1),
    "setlength": ("", 2), "addtolength": ("", 2), "setcounter": ("", 2),
    "addtocounter": ("", 2), "renewcommand": ("", 2), "newcommand": ("[", 2),
    "cmidrule": ("[(", 1), "cline": ("", 1), "multicolumn": ("", 2),
    "multirow": ("[", 2), "resizebox": ("", 2), "scalebox": ("", 1),
    "rule": ("[", 2), "raisebox": ("", 1), "fontsize": ("", 2),
    "linespread": ("", 1), "enlargethispage": ("", 1), "addlinespace": ("[", 0),
    "bibliography": ("", 1), "bibliographystyle": ("", 1),
    "printbibliography": ("[", 0), "tag": ("", 1),
}
_CITE_RE = re.compile(r"(?:" + _CITE_NAME + r"|nocite)$")
# A page/part locator inside a \cite note: p.~12, pp.~3--5, ch.~3, Fig.~2 ...
# (the separator is texutil.TEX_SPACE, shared with make_variants; N6)
_LOCATOR_RE = re.compile(
    r"\b(?:pp?|pg|chs?|chap|secs?|sect|figs?|tabs?|tbl|eqs?|vol|no|nos|para|fn|app|"
    r"pages?|chapters?)\.?" + texutil.TEX_SPACE + r"*\d+[a-z]?"
    + r"(?:\s*(?:--?|\u2013|,|&)\s*\d+[a-z]?)*", re.I)
# \begin{env}: its [options] go (typeset keys excepted); these envs also lose
# N spec args.
_ENV_SPEC_ARGS = {"tabular": 1, "tabular*": 2, "tabularx": 2, "tabulary": 2,
                  "array": 1, "longtable": 1, "minipage": 1, "wrapfigure": 2,
                  "wraptable": 2, "multicols": 1, "thebibliography": 1}
# Option keys whose VALUES are typeset text, so stay in scope (fix round 1,
# I1): a number in an axis title or a node label is a claim.
_TYPESET_KEYS = {"label", "pin", "xlabel", "ylabel", "zlabel", "title",
                 "legend entries", "xticklabels", "yticklabels", "zticklabels",
                 "nodes near coords", "extra x tick labels", "extra y tick labels",
                 "node contents"}     # P0.6 fix round 2, N2
# TeX lengths. Applied ONLY at TikZ code level now (fix round 1, M4): in prose
# "fell 5.3pt" is a quantity, and in arguments the whole group goes anyway.
_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9_.])-?\d*\.?\d+(?:pt|mm|cm|in|em|ex|bp|pc|sp|dd|cc|mu)\b"
    r"|(?<![A-Za-z0-9_.])-?\d*\.?\d+\s*\\(?:textwidth|linewidth|columnwidth|"
    r"textheight|paperwidth|hsize|vsize|baselineskip|tabcolsep|arrayrulewidth)\b")
# `[` must follow on the SAME line (fix round 1, M4): `\\` then a newline
# then "[0.617]" is text.
_LINEBREAK_OPT_RE = re.compile(r"\\\\\*?[ \t]*(?=\[)")
_TIKZ_RE = re.compile(r"\\begin\s*\{tikzpicture\}.*?\\end\s*\{tikzpicture\}", re.S)
# Fix round 1 (I1 ruling): `[` is an option list only after these commands,
# after \begin{...} (the generic pass), or after a TikZ keyword.
_TIKZ_OPT_CMDS = {"node", "draw", "path", "addplot", "coordinate", "fill",
                  "filldraw", "matrix"}
_TIKZ_KEYWORD_BEFORE_RE = re.compile(r"\b(?:node|coordinate|edge|pic|child|plot|to)\s*$")
# A brace group that opens with a drawing command, or with a TikZ/pgfplots
# environment (a \foreach body that opens with \begin{scope}), is CODE; any
# other brace group is TEXT and is kept whole -- including node text that
# opens with \begin{tabular} or \begin{minipage} (P0.6 fix round 2, N1: a bare
# `begin|end` here made such text code, and its numbers dropped out).
_TIKZ_CODE_ENVS = ("scope", "pgfonlayer", "axis", "semilogxaxis", "semilogyaxis",
                   "loglogaxis", "groupplot", "polaraxis")
_TIKZ_CODE_START_RE = re.compile(
    r"\s*\\(?:(?:draw|node|path|fill|filldraw|addplot|coordinate|foreach|matrix|"
    r"clip|shade|shadedraw|pic|pgfmathsetmacro)\b"
    r"|(?:begin|end)\s*\{\s*(?:" + "|".join(_TIKZ_CODE_ENVS) + r")\s*\})")
_TIKZ_DATA_RE = re.compile(r"\b(?:coordinates|table|file)\s*(?=[\[{])")
# A \foreach list is code (positions, counts) and goes -- unless its variable
# list is slash-paired (`\x/\l`): then its values are typically node text, so
# the WHOLE list stays in scope, the loud direction (P0.6 fix round 2, N2).
_FOREACH_LIST_RE = re.compile(r"\\foreach\b(?P<vars>[^{]*?)\bin\s*(?=\{)")
_FOREACH_PAIRED_RE = re.compile(r"\\[A-Za-z]+\s*/\s*\\[A-Za-z]+")
_CTRL_RE = re.compile(r"\\([A-Za-z]+)\*?")


def _blank(buf: list[str], i: int, j: int) -> None:
    for k in range(i, min(j, len(buf))):
        if buf[k] != "\n":
            buf[k] = " "


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in " \t\n":
        i += 1
    return i


def _skip_sp(s: str, i: int) -> int:
    """Spaces and tabs only: an optional `[` never crosses a line (M4)."""
    while i < len(s) and s[i] in " \t":
        i += 1
    return i


def _command_args_end(s: str, i: int, opts: str, n_mand: int) -> int:
    """End of a command's excluded arguments, starting just after its name."""
    end = i
    j = _skip_sp(s, i)
    while j < len(s) and s[j] in opts and s[j] in "[(":
        end = j = texutil.group_end(s, j)
        j = _skip_sp(s, j)
    for _ in range(n_mand):
        j = _skip_ws(s, end)
        if j < len(s) and s[j] == "{":
            end = texutil.group_end(s, j)
        else:
            break
    return end


def _blank_options(s: str, buf: list[str], i: int, j: int) -> None:
    """Blank the option list `s[i:j]` (`[` ... `]`), keeping the VALUES of
    typeset keys (`_TYPESET_KEYS`). For `label`/`pin` the position prefix
    (`above:`, `60:`) and any `[...]` before it go too."""
    buf[i] = " "
    if j - 1 > i and s[j - 1] == "]":
        buf[j - 1] = " "
    items, start, k = [], i + 1, i + 1
    while k < j - 1:
        c = s[k]
        if c == "\\":
            k += 2
            continue
        if c in "{[(":
            k = texutil.group_end(s, k)
            continue
        if c == ",":
            items.append((start, k))
            start = k + 1
        k += 1
    items.append((start, j - 1))
    for a, b in items:
        eq, k = -1, a
        while k < b:
            c = s[k]
            if c == "\\":
                k += 2
                continue
            if c in "{[(":
                k = texutil.group_end(s, k)
                continue
            if c == "=":
                eq = k
                break
            k += 1
        key = " ".join(s[a:eq].split()).lower() if eq >= 0 else ""
        if key not in _TYPESET_KEYS:
            _blank(buf, a, b)
            continue
        v = eq + 1
        if key in ("label", "pin"):
            v = _skip_ws(s, v)
            if v < b and s[v] == "[":
                v = texutil.group_end(s, v)
            colon = s.find(":", v, b)
            brace = s.find("{", v, b)
            if colon >= 0 and (brace < 0 or colon < brace):
                v = colon + 1
        _blank(buf, a, v)


def _scan_tikz(s: str, buf: list[str], a: int, b: int) -> None:
    """Inside one tikzpicture: blank option lists (typeset keys kept),
    coordinates, data blocks, \\foreach lists (not slash-paired ones) and
    code-level lengths. A brace group that does not open with a drawing
    command or a TikZ/pgfplots environment is TEXT and is skipped whole, so
    "(14 items, 26 discarded)" in a node keeps its numbers."""
    text_spans: list[tuple[int, int]] = []
    i = a
    while i < b:
        c = s[i]
        if c == "\\":
            m = _CTRL_RE.match(s, i)
            if not m:
                i += 2
                continue
            k = m.end()
            if m.group(1) in _TIKZ_OPT_CMDS:
                k2 = k
                while k2 < b and s[k2] in "+ \t":
                    k2 += 1
                if k2 < b and s[k2] == "[":
                    e = texutil.group_end(s, k2)
                    _blank_options(s, buf, k2, e)
                    i = e
                    continue
            i = k
            continue
        if c == "{":
            e = texutil.group_end(s, i)
            if _TIKZ_CODE_START_RE.match(s, i + 1):
                i += 1
            else:
                text_spans.append((i, e))
                i = e
            continue
        if c == "[" and _TIKZ_KEYWORD_BEFORE_RE.search(s, a, i):
            e = texutil.group_end(s, i)
            _blank_options(s, buf, i, e)
            i = e
            continue
        if c == "(":
            e = texutil.group_end(s, i)
            if any(ch in s[i:e] for ch in ",:"):
                _blank(buf, i, e)
            i = e if e > i else i + 1
            continue
        i += 1
    for dm in _TIKZ_DATA_RE.finditer(s, a, b):
        j = _skip_ws(s, dm.end())
        while j < b and s[j] == "[":
            j = _skip_ws(s, texutil.group_end(s, j))
        if j < b and s[j] == "{":
            _blank(buf, dm.start(), texutil.group_end(s, j))
    for fm in _FOREACH_LIST_RE.finditer(s, a, b):
        if _FOREACH_PAIRED_RE.search(fm.group("vars")):
            continue
        _blank(buf, fm.end(), texutil.group_end(s, fm.end()))
    for um in _UNIT_RE.finditer(s, a, b):
        if not any(x <= um.start() < y for x, y in text_spans):
            _blank(buf, um.start(), um.end())


def _scoped_code(tex: str) -> tuple[str, bool]:
    """(paper.tex with everything OUT of scope blanked, anchored?).

    Same length and the same line breaks as `tex`, so an offset is a line.
    Comments go first (per line, by the shared rule), then everything before
    the Introduction and after \\end{document}, the back matter, the
    bibliography, and the arguments and regions listed in `uncovered_numbers`.
    Not anchored (`_intro_anchor` finds none) -> everything blanked, False.
    """
    lines = []
    for raw in tex.split("\n"):
        code = texutil.strip_comment_line(raw)
        lines.append("" if code is None else code)
    # keep each line's length so offsets stay put: pad stripped lines
    code_lines = [c + " " * (len(r) - len(c)) for c, r in zip(lines, tex.split("\n"))]
    s = "\n".join(code_lines)
    buf = list(s)

    m = _intro_anchor(s)
    if not m:
        return "".join(" " if c != "\n" else c for c in s), False
    end_doc = s.find("\\end{document}", m.end())
    end = len(s) if end_doc < 0 else end_doc
    _blank(buf, 0, m.start())
    _blank(buf, end, len(s))

    # back matter: to the next non-back-matter section, \appendix or the end
    for bm in _BACK_MATTER_START_RE.finditer(s, m.end(), end):
        stop = end
        for sec in _SECTION_RE.finditer(s, bm.end(), end):
            if not _BACK_TITLE_RE.match(sec.group(1)):
                stop = sec.start()
                break
        app = s.find("\\appendix", bm.end(), end)
        if 0 <= app < stop:
            stop = app
        _blank(buf, bm.start(), stop)
    for bib in re.finditer(r"\\begin\s*\{thebibliography\}.*?\\end\s*\{thebibliography\}",
                           s, re.S):
        _blank(buf, bib.start(), bib.end())

    # command arguments
    for cm in re.finditer(r"\\([A-Za-z]+)(\*?)", s):
        name = cm.group(1)
        if name == "begin":
            j = _skip_ws(s, cm.end())
            if j >= len(s) or s[j] != "{":
                continue
            env_end = texutil.group_end(s, j)
            env = s[j + 1:env_end - 1].strip()
            k = _skip_sp(s, env_end)
            stop = env_end
            while k < len(s) and s[k] == "[":
                e = texutil.group_end(s, k)
                _blank_options(s, buf, k, e)
                stop = e
                k = _skip_sp(s, e)
            _blank(buf, stop, _command_args_end(s, stop, "", _ENV_SPEC_ARGS.get(env, 0)))
            continue
        if _CITE_RE.match(name):
            _blank(buf, cm.start(), cm.end())
            k = _skip_sp(s, cm.end())
            while k < len(s) and s[k] == "[":
                e = texutil.group_end(s, k)
                buf[k] = " "
                if s[e - 1] == "]":
                    buf[e - 1] = " "
                for loc in _LOCATOR_RE.finditer(s, k + 1, e - 1):
                    _blank(buf, loc.start(), loc.end())
                k = _skip_sp(s, e)
            k = _skip_ws(s, k)
            if k < len(s) and s[k] == "{":
                _blank(buf, k, texutil.group_end(s, k))
            continue
        spec = _EXCLUDED_ARGS.get(name)
        if spec is not None:
            _blank(buf, cm.start(), _command_args_end(s, cm.end(), *spec))
    for lb in _LINEBREAK_OPT_RE.finditer(s):
        _blank(buf, lb.end(), texutil.group_end(s, lb.end()))

    for tz in _TIKZ_RE.finditer(s):
        _scan_tikz(s, buf, tz.start(), tz.end())
    return "".join(buf), True


def _math_mask(s: str) -> list[bool]:
    """Per character: inside inline or display maths ($, $$, \\( \\), \\[ \\])?"""
    mask = [False] * len(s)
    inside, i, n = False, 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt in "([":
                inside = True
            elif nxt in ")]":
                inside = False
            mask[i] = mask[i + 1] = inside
            i += 2
            continue
        if c == "$":
            step = 2 if s.startswith("$$", i) else 1
            inside = not inside
            for k in range(i, min(n, i + step)):
                mask[k] = True
            i += step
            continue
        mask[i] = inside
        i += 1
    return mask


def _context(raw_line: str, token: str) -> str:
    code = " ".join((texutil.split_comment(raw_line)[0]).split())
    probes = [token, token.replace(",", "{,}")]
    if token.startswith("0."):
        probes.append(token[1:])
    k = next((code.find(p) for p in probes if p in code), 0)
    lo = max(0, k - 30)
    return code[lo:lo + 60 + len(token)]


def _pin_index(literals) -> dict[str, list[str]]:
    """token -> the labels of every check whose literal carries it. A mapping
    is {label: literal}; a plain collection of literals labels each by itself."""
    pairs = literals.items() if hasattr(literals, "items") else ((l, l) for l in literals)
    out: dict[str, list[str]] = {}
    for label, lit in pairs:
        for t in dict.fromkeys(number_tokens(normalise_numeric(lit))):
            out.setdefault(t, []).append(label)
    return out


def _bib_keys(tex_path: Path, tex: str) -> set[str] | None:
    """Keys of the bibliography `\\bibliography{...}` names (beside paper.tex;
    `bibliography.bib` only when there is no such command). None when no file
    exists -- a LIT tag then cannot be checked, so it excuses nothing."""
    names = []
    for m in _BIB_CMD_RE.finditer(texutil.strip_comments(tex)):
        names += [n.strip() for n in m.group(1).split(",") if n.strip()]
    if not names:
        names = ["bibliography"]
    keys, found = set(), False
    for n in names:
        f = Path(tex_path).parent / (n if n.endswith(".bib") else n + ".bib")
        if f.exists():
            found = True
            for kind, key in _BIB_KEY_RE.findall(f.read_text(encoding="utf-8",
                                                             errors="replace")):
                if kind.lower() not in ("comment", "string", "preamble"):
                    keys.add(key)
    return keys if found else None


def _line_tag(comment: str, bib_keys) -> tuple[str, str]:
    """(kind, detail) of the tag in a raw line's comment part. kind is one of
    "" (none), "NEW", "LIT", "YEAR", "BAD-NEW" (any near-miss tag, or an
    empty `%% NEW:` -- it beats coverage, fail-safe), "BAD" (a `%% LIT:` tag
    that cannot excuse; it does not beat coverage)."""
    m = _NEW_TAG_RE.search(comment)
    if m:
        qual = f" ({m.group(1).strip()})" if m.group(1) else ""
        return "NEW", f"source{qual}: {m.group(2)}"
    if _NEW_EMPTY_RE.search(comment):
        return "BAD-NEW", "malformed NEW tag, want `%% NEW: <source>`"
    m = _LIT_TAG_RE.search(comment)
    if m:
        body = _LIT_BODY_RE.match(m.group(1))
        if not body:
            return "BAD", "malformed LIT tag, want `%% LIT: <bibkey> p.<n>`"
        key = body.group(1)
        if bib_keys is None:
            return "BAD", f"LIT key {key!r} unchecked: no bibliography file found"
        if key not in bib_keys:
            return "BAD", f"LIT key {key!r} is not in the bibliography"
        return "LIT", m.group(1).strip()
    if _YEAR_TAG_RE.search(comment):
        return "YEAR", ""
    m = _TAGLIKE_RE.search(comment)
    if m:
        frag = comment[m.start():m.start() + 40].strip()
        return "BAD-NEW", (f"near-miss tag {frag!r}: want `%% NEW: <source>`, "
                           f"`%% LIT: <bibkey> p.<n>` or `%% YEAR`")
    return "", ""


def _is_year(tok: str, code: str, line_start: int, math: list[bool]) -> bool:
    """A 4-digit 1900-2099 integer written outside maths (M2b)."""
    if not _YEAR_RE.fullmatch(tok):
        return False
    for m in re.finditer(rf"(?<![\d.,]){tok}(?!\d)", code):
        if not math[line_start + m.start()]:
            return True
    return False


def _coverage_scan(tex: str, literals, bib_keys=None) -> Coverage:
    """Scan `tex`; see `uncovered_numbers` for the rules.

    `literals`: {label: literal} (labels then appear in notes and in
    --list-covered) or a plain collection of literals. `bib_keys`: the keys a
    LIT tag may name; None means no bibliography could be read.
    """
    pins = _pin_index(literals)
    scoped_text, anchored = _scoped_code(tex)
    if not anchored:
        return Coverage([], 0, 0, [], _NO_ANCHOR)
    math = _math_mask(scoped_text)
    raw_lines = tex.split("\n")
    out: list[Uncovered] = []
    covered: list[tuple[int, str, list[str]]] = []
    n_tok = n_cov = 0
    start = 0
    for no, (code, raw) in enumerate(zip(scoped_text.split("\n"), raw_lines), 1):
        line_start, start = start, start + len(code) + 1
        if not code.strip():
            continue
        toks = number_tokens(normalise_numeric(code))
        if not toks:
            continue
        raw_code, comment = texutil.split_comment(raw)
        tag, detail = _line_tag(comment, bib_keys)
        cites = bool(_CITE_CMD_RE.search(raw_code))
        for tok in toks:
            n_tok += 1
            labels = pins.get(tok)
            ctx = _context(raw, tok)
            if tag == "NEW":
                twin = f"; also pinned by: {'; '.join(labels)}" if labels else ""
                out.append(Uncovered("NEW", tok, no, ctx, detail + twin))
            elif tag == "BAD-NEW":
                out.append(Uncovered("UNCOVERED", tok, no, ctx, detail))
            elif labels:
                n_cov += 1
                covered.append((no, tok, labels))
            elif tag == "LIT" and cites:
                out.append(Uncovered("INFO literature", tok, no, ctx, detail))
            elif _is_year(tok, code, line_start, math):
                excused = cites or tag == "YEAR"
                out.append(Uncovered(
                    "INFO year", tok, no, ctx,
                    "" if excused else "no \\cite or %% YEAR on this line: "
                                       "fails --strict-coverage",
                    strict_fail=not excused))
            elif tag == "LIT":
                out.append(Uncovered("UNCOVERED", tok, no, ctx,
                                     "LIT tag, but this line has no \\cite"))
            elif tag == "BAD":
                out.append(Uncovered("UNCOVERED", tok, no, ctx, detail))
            else:
                out.append(Uncovered("UNCOVERED", tok, no, ctx))
    refusal = None if n_tok else "0 number tokens in scope"
    return Coverage(out, n_tok, n_cov, covered, refusal)


def uncovered_numbers(tex_path: Path, literals=None) -> list[Uncovered]:
    r"""Every number token in the main text and appendices that no check pins.

    SCOPE. The comment-stripped source from the scope anchor to
    `\end{document}`. The anchor (`_intro_anchor`) is the FIRST `\section`
    after `\begin{document}`, and it must be `\section{Introduction}`,
    `\section[...]{Introduction}`, `\section{\label{...}Introduction}` or
    `\section*{Introduction}` (`\section{Introduction\label{...}}` is refused:
    write `\section{Introduction}\label{...}`). With no anchor, or with an
    anchored scope that holds no number token, the scan REFUSES (raises
    `ScopeNotAnchored`; `main()` prints `FAIL coverage: scope not anchored`):
    neither is a measurement (fix round 1, C2; P0.6 fix round 2, N3 and N4).
    Excluded:
      - the back-matter declarations, matched by their FULL titles
        (`_BACK_MATTER_TITLES`) or `\printcredits`, each running to the next
        section that is not back matter, `\appendix` or `\end{document}`;
      - the bibliography (`\bibliography`, `\bibliographystyle`,
        `thebibliography`);
      - the arguments of the `\ref` family (`texutil.REF_COMMANDS`), `\label`,
        `\includegraphics[...]`, `\input`, `\usepackage`, `\url`/`\href`'s URL,
        spacing and length commands, table specs (`\begin{tabular}{...}`,
        `\multicolumn{n}{spec}` -- its text stays -- `\cmidrule`, `\\[2pt]` on
        the same line), and `\begin{env}[options]`;
      - `\cite*` keys and the page LOCATORS in their notes (`p.~12`,
        `ch.~3`); any other note text ("reporting 32\%") stays in scope;
      - inside `tikzpicture`: option lists after `\node`, `\draw`, `\path`,
        `\addplot`, `\coordinate`, `\fill`, `\filldraw`, `\matrix`, a TikZ
        keyword or `\begin{...}`; coordinates (a code-level parenthesised
        group with a comma or colon); `coordinates`/`table` data blocks;
        `\foreach` lists, except a slash-paired one (`\x/\l`), which stays
        WHOLE (P0.6 fix round 2, N2); code-level lengths.
    KEPT, because typeset: node text (any brace group that does not open with
    a drawing command or a TikZ/pgfplots environment such as `scope` or
    `axis` -- `{\begin{tabular}...}` is text), the values of `_TYPESET_KEYS`
    (label, pin, x/y/zlabel, title, legend entries, x/y/zticklabels, extra
    x/y tick labels, nodes near coords, node contents), captions, table
    cells, and prose -- including "width = 0.12" or
    "fell 5.3pt" (no global key=value or length rule; fix round 1, I1/M4).
    The abstract is out of scope here because `abstract_lint` owns it.

    PGFPLOTS DATA IS NOT CHECKED HERE. Coordinates come from generated figure
    code: the P1C result figures are compared with their generator by
    `tests/test_make_result_figures.py::test_the_emitted_files_are_current`.
    The screen histogram pasted inline in paper.tex (`make_screen_figure.py`
    output) has NO such comparison today -- this report does not cover it.

    TOKENS. `number_tokens(normalise_numeric(...))`, exactly as the abstract
    lint, so the identifier rule (RQ1, P0, Python 3, GPT-4o ...) is shared.

    COVERED is LOOSE: a token is covered if it equals a token of ANY current
    check literal. A twin -- the same string for another quantity -- counts
    as covered (ruled, M1). `--list-covered` prints every covered token with
    the labels of the checks that pin it, for the P2 exit review.

    KINDS, in precedence order (fix round 1, C1 and M2 rulings):
      NEW              the line carries a well-formed `%% NEW: <source>`
                       (or `%% NEW (<qualifier>): <source>`): EVERY token on
                       it is NEW, even one a check pins -- the note then names
                       the pinning check(s). Removing the tag is the
                       integrator's deliberate act after writing the check.
      UNCOVERED        any near-miss tag (`%% new: ...`, `%% NEW` with no
                       colon, `%% lit: ...`, `% Year ...`) or an empty
                       `%% NEW:` also beats coverage, with a note saying what
                       the tag should be. A `%% LIT:` whose body or key is bad
                       does not: it is reported only when no check pins the
                       token (below)
      covered          not reported (see --list-covered)
      INFO literature  a valid `%% LIT: <bibkey> p.<n>` (trailing text
                       allowed) on a line that carries a `\cite`, with a key
                       that is in the bibliography
      INFO year        a 4-digit 1900-2099 integer written outside maths;
                       under --strict-coverage it FAILS unless its line
                       carries a `\cite` or a `%% YEAR` tag
      UNCOVERED        everything else -- including a LIT-tagged token whose
                       line cites nothing or whose key is unknown, with a note
    Tags are read from the RAW line and apply to that line only.

    `literals`: {label: literal} or a collection of literals; None computes
    {label: literal} with `_current_literals(checks())`.
    """
    if literals is None:
        literals = _current_literals(checks())
    tex = Path(tex_path).read_text(encoding="utf-8")
    cov = _coverage_scan(tex, literals, _bib_keys(Path(tex_path), tex))
    if cov.refusal:     # ANY refusal: an empty scope is not "0 uncovered" (N4)
        raise ScopeNotAnchored(f"{tex_path}: scope not anchored ({cov.refusal})"
                               f"{_anchor_hint(cov.refusal)}")
    return cov.records

def _load(name: str) -> list[dict] | None:
    p = RUNS / name
    if not p.exists():
        return None
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return rows or None


def _corr(xs: list[float], ys: list[float]) -> float:
    """Pearson r. Raises on a constant input rather than dividing by zero.

    A constant input is reachable here, not merely hypothetical: this is called
    on `parsed_fraction`, which saturates at 1.0 whenever every solver answers
    every call -- the intended state once the token budgets are right. On a
    small or homogeneous item set that column can be constant, and correlation
    is undefined there, not zero. The bare division raised ZeroDivisionError
    from four frames deep, which reads as a tool crash rather than as "this
    quantity does not exist for this data".
    """
    if len(xs) != len(ys):
        raise ValueError(f"length mismatch: {len(xs)} vs {len(ys)}")
    if len(xs) < 2:
        raise ValueError("correlation needs at least two observations")
    mx, my = st.mean(xs), st.mean(ys)
    sx = sum((a - mx) ** 2 for a in xs)
    sy = sum((b - my) ** 2 for b in ys)
    if sx == 0 or sy == 0:
        which = "x" if sx == 0 else "y"
        raise ValueError(
            f"correlation is undefined: {which} has zero variance "
            f"(every value is {mx if which == 'x' else my:.4g}). If this is "
            f"parsed_fraction, a constant 1.0 means no solver ever declined -- "
            f"report that fact instead of a correlation with it.")
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    return num / (sx * sy) ** 0.5


def _spearman(x: list[float], y: list[float]) -> float:
    def rank(v: list[float]) -> list[float]:
        s = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(s):
            j = i
            while j + 1 < len(s) and v[s[j + 1]] == v[s[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[s[k]] = avg
            i = j + 1
        return r

    return _corr(rank(x), rank(y))


_UNITS = ("zero", "one", "two", "three", "four", "five", "six", "seven",
          "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
          "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety")


def _spell(n: int) -> str:
    """Spell an integer below 100 as words, for checks against running prose.

    The manuscript's number convention is mixed, and deliberately so. Reported
    data and design counts stay numerals -- "60 items", "40 three-option
    multiple-choice items", "20 short-answer items", "64 cells" -- because they
    are quantities a reader may want to recompute. Incidental quantities in
    running prose are spelled out: "sixty calls", "two items in each bank",
    "four times in total".

    So this is the right renderer for the second kind ONLY. Using it on a
    reported count produces a literal the manuscript does not contain, which is
    a MISSING that reflects the check and not the prose -- exactly what happened
    when the short-answer count was first pinned here. When adding a check, read
    the sentence and match it; do not assume either convention.

    Returning the numeral above 99 is deliberate: it fails loudly rather than
    passing on a wrong literal.
    """
    if n < 0 or n >= 100:
        return str(n)
    if n < 20:
        return _UNITS[n]
    tens, unit = divmod(n, 10)
    return _TENS[tens] if unit == 0 else f"{_TENS[tens]}-{_UNITS[unit]}"


def _verifier_audit(bank: str, roster: str = "ol") -> dict:
    """One call into the audit tool, so the manuscript and the CLI agree."""
    import verifier_audit as VA
    return VA.audit(bank, roster, "stress", True)


def checks() -> list[tuple[str, str | None, str]]:
    """(label, literal-that-must-appear-in-paper, note). None => PENDING."""
    out: list[tuple[str, str | None, str]] = []
    kr = _load("screen_bankA.jsonl")

    if kr is None:
        out.append(("kiro screen", None, "data/runs/screen_bankA.jsonl absent"))
        return out

    ret = [r for r in kr if r["passes_headroom"]]
    solvers = list(kr[0]["per_solver"].keys())

    def tot(rows, s, f):
        return sum(r["per_solver"][s][f] for r in rows)

    out.append(("n screened (Bank A, kr)", str(len(kr)), "all candidates"))
    out.append(("n retained", str(len(ret)), "passes_headroom"))

    r_all = _corr([r["parsed_fraction"] for r in kr], [r["baseline"] for r in kr])
    out.append(("corr(parsed, baseline) all 40", f"{r_all:.2f}", "quoted as r in placebo section"))

    # The Kiro conditional-accuracy figures (0.384 / 0.391) and its 0.50 parse
    # rate were REMOVED from the manuscript, not merely reworded: they were the
    # evidence for a large willingness artefact, and that artefact was an
    # instrument defect (a 16-token budget recording truncations as declines).
    # The primary panel's figures replace them and are checked below.
    parsed = sum(tot(ret, s, "parsed") for s in solvers)
    calls = sum(tot(ret, s, "calls") for s in solvers)
    out.append(("kr pilot parse rate (context only)", f"{parsed / calls:.2f}", "not quoted as a result"))

    # The solver table is now built from the PRIMARY (ol) panel; see below.
    # The Kiro per-solver figures are no longer quoted in the manuscript.

    # Screen gap and threshold invariance -- the answer to "you tuned the cutoff".
    #
    # THE PRIMARY SCREEN IS THE OLLAMA ONE. The Kiro screen measured the
    # PRE-REBALANCE bank (sha e5b52f1b), which is not the study bank
    # (d1b4b6c8); it also ran every solver at a 16-token budget with no
    # truncation guard. Its numbers describe a different artefact measured with
    # a worse instrument, so the manuscript reports the Ollama screen and these
    # checks follow it. The Kiro figures survive only where the manuscript
    # explicitly labels them as the earlier panel.
    ol = _load("screen_bankA_ol.jsonl")
    # AN IN-PROGRESS SCREEN IS NOT A SCREEN. The stage is append-only and
    # resumable, so mid-run the file holds a valid prefix -- and every quantity
    # below is a property of the COMPLETE set. Computing them on a prefix does
    # not merely give an early answer, it gives a wrong one: with one row
    # written, `min(rejected) - max(retained)` is not an invariance band at all,
    # and the assertion below fired with "the band is 48.00 calls" during the
    # 2026-08-17 re-screen -- a confusing failure in the one tool whose job is to
    # keep the manuscript and the data honest. Report PENDING and wait.
    n_mcq = sum(1 for i in json.loads(
        (BANKS / "bank_a.json").read_text(encoding="utf-8")
    )["items"] if i.get("type") == "mcq")
    if ol is None:
        out.append(("primary screen (ol)", None, "screen_bankA_ol.jsonl absent"))
    elif len(ol) < n_mcq:
        out.append(("primary screen (ol)", None,
                    f"screen IN PROGRESS: {len(ol)}/{n_mcq} items -- every "
                    f"figure below is a property of the complete set"))
        ol = None
    else:
        ol_ret = [r for r in ol if r["passes_headroom"]]
        kept_b = sorted(r["baseline"] for r in ol_ret)
        rej_b = sorted(r["baseline"] for r in ol if not r["passes_headroom"])
        out.append(("primary: n retained", str(len(ol_ret)), "ol screen"))
        out.append(("primary: n discarded", str(len(rej_b)), "ol screen"))
        out.append(("retained baseline range",
                    f"$[{min(kept_b):.2f}, {max(kept_b):.2f}]$", "ol screen gap"))
        out.append(("discarded baseline range",
                    f"$[{min(rej_b):.2f}, {max(rej_b):.2f}]$", "ol screen gap"))
        # The band is quoted in CALLS: as a decimal, 0.05 reads as a comfortable
        # margin when it is in fact three solver calls out of sixty.
        #
        # This was an `assert 0.99 <= band_calls <= 1.01` -- a CONSTANT from the
        # v2.1 data, with the prose's "one solver call wide" baked into the tool
        # that was supposed to be checking the prose. When the §4.29 re-screen
        # widened the band to three calls it aborted `verify_numbers` outright,
        # so the one tool that keeps the manuscript honest crashed at exactly
        # the moment the manuscript became wrong. A check that hardcodes the
        # value it is verifying cannot survive its own subject changing.
        #
        # Emitted as a literal instead, like every other quantity here: the
        # value is recomputed from the data and the manuscript must contain it.
        band_calls, k_tot = _screen_band(ol)
        # Rounded with a tolerance, not compared exactly: the band is a
        # difference of two exact sixtieths scaled by 60, so it lands on
        # 2.9999999999999996 rather than 3.0 and an equality test sends a whole
        # number down the decimal branch. The same class of error as §4.17,
        # where a bare `<=` decided an exact tie by one ULP.
        # RE-TARGETED at P2-I2: the bare word ("three") matched any "three"
        # in the paper (triage row 8). It is now the App A sentence that
        # states the band, anchored by its own words.
        out.append(("invariance band (solver calls)",
                    f"is {_spell(round(band_calls))} reader-model calls wide"
                    if abs(band_calls - round(band_calls)) < 1e-6
                    else f"{band_calls:.2f}",
                    "min(rejected) - max(retained), in calls out of "
                    f"{k_tot}; spelled out because App A reads "
                    f"'{_spell(round(band_calls))} reader-model calls wide'"))

        # THE SCREEN RETAINS ON A POOLED BASELINE, THE ANALYSIS WORKS PER
        # SOLVER (spec 4.34). `per_solver_leak` computes
        # `leak_s = hinted_s - baseline_s`, so a solver already at baseline
        # 1.000 has `leak_s <= 0` BY CONSTRUCTION -- that cell is censored, not
        # noisy, and no tutor response can register in it. The pooled retention
        # rule cannot see this, because it is a statement about the panel.
        #
        # Emitted as literals rather than asserted: the bias is CONSERVATIVE
        # (censoring at the top can only shrink measured leakage), so this is a
        # limitation the manuscript must disclose with its size, not a defect
        # that should stop the run.
        cells = [(r["item_id"], sv, v["hits"] / v["calls"], r["chance"] + 0.15)
                 for r in ol_ret for sv, v in r["per_solver"].items()
                 if v.get("calls")]
        if cells:
            sat = [c for c in cells if c[2] >= 1.0]
            above = [c for c in cells if c[2] > c[3]]
            # SPELLED AND QUALIFIED, not bare integers. `str(2)` matches the
            # digit "2" anywhere in a LaTeX file -- including a citation year,
            # a table column or a macro argument -- so the check would report
            # VERIFIED against a manuscript that never states the number. A
            # check that cannot fail is worse than no check, because it is
            # counted in the "0 missing" line that the release rests on.
            out.append(("solver-saturated baseline cells",
                        f"${len(sat)}$ of the ${len(cells)}$",
                        "(item, solver) cells on retained items where baseline "
                        "is 1.000, so leak_s <= 0 by construction (spec 4.34)"))
            out.append(("cells above the retention threshold",
                        f"${len(above)}$ of the ${len(cells)}$",
                        "retained on the POOLED baseline while individually "
                        "identifiable to that solver"))

        # THE INVARIANCE BAND ASSUMES EVERY VERDICT IS ESTABLISHED. It reports
        # how far apart the retained and rejected populations sit, which is
        # only a statement about the partition if the partition is real. A
        # failed call is scored as a miss and so can only have DEPRESSED a
        # baseline, which means truncation manufactures apparent headroom and
        # pushes items INTO the retained set -- widening the very band that is
        # quoted as evidence the screen is robust. Checked here and not only in
        # `freeze_bank` because the manuscript quotes the band whether or not
        # anyone re-freezes.
        at_risk = [(r["item_id"], S.truncation_slack(r)) for r in ol
                   if S.truncation_slack(r) < 0]
        assert not at_risk, (
            "screen_bankA_ol.jsonl contains retained verdicts reachable by "
            f"their own failed calls: {at_risk}. The invariance band above is "
            "not a property of a partition the measurement establishes. "
            "Re-measure these items (see measure_budgets.py, spec 4.33).")

    # THE FROZEN BANK IS A SECOND COPY OF THE RETENTION DECISION, and copies
    # drift. `run.screened_items` derives retention live from the screen jsonl;
    # `freeze_bank` derives it again into `bank_a_screened_ol.json`, which is
    # the file that gets deposited and the file CLAUDE.md and the spec cite by
    # sha256. Nothing compared them, and on 2026-08-17 they disagreed: the
    # frozen bank held 15 items from a screen since quarantined for the minimax
    # truncation defect, while the named screen file was being rewritten by the
    # re-screen. The pointer stayed valid because `screen_file` records a NAME,
    # and run files are append-only, so the name silently retargeted.
    #
    # Deliberately NOT gated on the screen being complete: a mismatch between a
    # deposited artefact and the data under it is worth reporting at any moment,
    # and mid-re-screen is exactly when it is true.
    frozen_p = BANKS / "bank_a_screened_ol.json"
    screen_p = RUNS / "screen_bankA_ol.jsonl"
    if frozen_p.exists() and screen_p.exists():
        fz = json.loads(frozen_p.read_text(encoding="utf-8"))
        live = hashlib.sha256(screen_p.read_bytes()).hexdigest()
        claimed = fz.get("screen_sha256")
        if claimed is None:
            out.append(("frozen bank <-> screen", None,
                        f"{frozen_p.name} predates `screen_sha256` and names "
                        f"its screen only as {fz.get('screen_file')!r}; the "
                        f"link cannot be checked. Re-freeze after the screen "
                        f"completes."))
        elif claimed != live:
            out.append(("frozen bank <-> screen", None,
                        f"DRIFT: {frozen_p.name} was frozen from screen "
                        f"{claimed[:12]}..., {screen_p.name} is now "
                        f"{live[:12]}.... The deposited bank no longer "
                        f"describes the data on disk."))
        else:
            out.append(("frozen bank <-> screen", "match",
                        f"{fz.get('n_retained_mcq')} retained, screen "
                        f"{live[:12]}..."))

    # Difficulty independence. This sentence has drifted twice: once claiming
    # rejections were confined to levels 1-2, once claiming they occurred at
    # EVERY level. Levels 3, 4 and 5 have none, so both were wrong. Pin the
    # count and the rank correlation rather than the adjective.
    lv = [(int(r["item_id"].split("-")[1][1:]), 0 if r["passes_headroom"] else 1) for r in kr]
    n_levels_with_rej = len({a for a, b in lv if b})
    out.append(("levels containing a discard", str(n_levels_with_rej), "difficulty independence"))
    rho = _spearman([a for a, _ in lv], [b for _, b in lv])
    out.append(("rho(level, discard)", f"{rho:.2f}", "difficulty independence"))

    # Truncation bias (§4.13). These trace to the QUARANTINED screen, which is
    # the point: the quarantine file is evidence, not waste, and the manuscript
    # cites it. Deleting it would strand these numbers.
    q = _load("QUARANTINE_screen_bankA_ol_truncation_budget600_2026-08-16.jsonl")
    if q is None:
        out.append(("gpt-oss truncation rate", None, "quarantined pilot screen absent"))
    else:
        m = "ol/gpt-oss:120b-cloud"
        calls = sum(r["per_solver"][m]["calls"] for r in q if m in r.get("per_solver", {}))
        failed = sum(r["per_solver"][m]["failed"] for r in q if m in r.get("per_solver", {}))
        if calls:
            out.append(("truncated calls (numerator)", str(failed), "pilot budget 600"))
            out.append(("truncated calls (denominator)", str(calls), "pilot budget 600"))
            # One decimal deliberately: 54/240 is exactly 22.5%, and rounding a
            # half is ambiguous (Python rounds to even -> 22, convention -> 23).
            # Reporting the decimal removes the disagreement rather than picking
            # a side of it.
            out.append(("truncation rate", f"{100 * failed / calls:.1f}\\%", "pilot budget 600"))

    # Truncation-correction direction and magnitude (§4.13). The manuscript
    # first claimed truncation biased baselines UPWARD; the data showed the
    # opposite. Pinned so the corrected direction cannot silently drift back:
    # if any shift ever goes negative, `all_upward` flips and the claim in the
    # Reproducibility section stops being true.
    # `ol` is None above when the screen is incomplete, and these figures are
    # properties of the complete set for the same reason the invariance band is.
    # The old `len(both) >= 5` gate was meant to guard this and does not: a
    # 5-item prefix of a re-running screen passes it, and on 2026-08-17 that
    # produced "shift +0.039" and "most negative shift 0.000" from five rows --
    # numbers with the right shape and no meaning. A count threshold cannot
    # express "complete"; only the bank size can.
    new = _load("screen_bankA_ol.jsonl") if ol is not None else None
    if q and new:
        o = {r["item_id"]: r for r in q}
        n_ = {r["item_id"]: r for r in new}
        both = sorted(set(o) & set(n_))
        # Completeness is enforced on `new` above; what matters here is that the
        # two screens actually overlap. An empty intersection means the files
        # describe different item sets and the comparison is meaningless.
        if both:
            shifts = [(1 - o[i]["parsed_fraction"],
                       n_[i]["baseline"] - o[i]["baseline"]) for i in both]
            lo = [d for t, d in shifts if t < 0.05]
            hi = [d for t, d in shifts if t >= 0.05]
            if lo and hi:
                out.append(("shift, >=5% truncated", f"${st.mean(hi):+.3f}$",
                            "truncation correction"))
                out.append(("most negative shift", f"${min(d for _, d in shifts):.3f}$",
                            "truncation correction"))
            # Two checks were REMOVED here, and the reason matters more than the
            # code. The manuscript used to print the untruncated-group mean as
            # "0.000" and a correlation of 0.87. Both were dropped from the
            # paper, not from the data:
            #
            #   - "0.000" became the stronger prose claim that untruncated items
            #     reproduced EXACTLY, which the assertion below enforces at
            #     1e-9. A printed literal is weaker than an invariant.
            #   - the correlation moved 0.87 -> 0.65 as items 10-12 arrived. It
            #     is unstable at n=12 and leans on one influential point, so the
            #     manuscript no longer claims it. Checking a number the paper
            #     does not assert would be theatre.
            #
            # Deleting a check because the claim was withdrawn is legitimate.
            # Deleting one because it went red is not. This was the former.
            # TOLERANCE IS THE MEASUREMENT'S OWN NOISE FLOOR, not a call count.
            #
            # This assertion has now fired twice, both times on A-L03-05, both
            # times because the threshold was an arbitrary small integer rather
            # than a property of the measurement. Version one demanded every
            # shift be >= 0 and fired at -0.017 (one call). It was "fixed" to
            # two calls -- and on 2026-08-17 the §4.29 re-screen moved the same
            # item -0.067 (four calls) and it fired again.
            #
            # Two calls is 0.033, which at k=60 and p~0.35 is 0.54 of one
            # binomial standard error (SE = sqrt(p(1-p)/60) = 0.062 = 3.7
            # calls). The check demanded a precision roughly twenty times finer
            # than the instrument delivers: solvers run at temperature 1.0, so
            # two independent 60-call estimates of the same baseline differ by
            # up to ~10 calls at 95%. It was not detecting anything; it was
            # waiting to fire.
            #
            # Deriving the floor from the data makes the check say something
            # real: a downward move beyond 3 SE is not sampling variation and
            # would genuinely contradict the directional claim.
            k_total = q[0].get("k_per_solver", 20) * len(q[0].get("per_solver", {}) or [1])
            # Recomputed from `both` rather than from `shifts`, whose first
            # element is a truncation fraction: the error message has to be able
            # to NAME the offending item, and twice now the useful fact about
            # this assertion has been which item tripped it.
            per_item = {i: n_[i]["baseline"] - o[i]["baseline"] for i in both}
            worst_item = min(per_item, key=per_item.get)
            worst = per_item[worst_item]
            # p_hat from the corrected screen over the compared items; clamped
            # away from 0 and 1 so a saturated overlap cannot report SE = 0 and
            # turn this back into an exact-equality check.
            p_hat = st.mean(n_[i]["baseline"] for i in both)
            p_hat = min(max(p_hat, 0.05), 0.95)
            se = (p_hat * (1 - p_hat) / k_total) ** 0.5 if k_total else 0.06
            assert worst >= -3 * se, (
                f"{worst_item} moved down by {worst:.3f}, beyond 3 standard "
                f"errors ({3 * se:.3f}) of the k={k_total} measurement. That is "
                f"no longer sampling variation and contradicts the directional "
                f"claim. Re-check before publishing.")
            out.append(("truncation shift, noise floor (SE, calls)",
                        f"{se * k_total:.1f}",
                        f"binomial SE at k={k_total}; individual shifts below "
                        f"this are not evidence of direction"))
            # The untruncated items are NOT a control, and the manuscript now
            # says so. Every one of them sits at the measurement ceiling
            # (baseline 1.00 in both runs), so reproducing exactly is what a
            # saturated item does, not evidence that the measurement is stable.
            # Solvers run at temperature 1.0 (C.SOLVER_TEMPERATURE), so repeated
            # draws DO carry variance; we simply have no unsaturated item that
            # was measured twice.
            #
            # This check therefore asserts the SATURATION, which is the fact the
            # manuscript's caveat depends on. If an unsaturated item ever lands
            # in the zero-truncation group, the caveat is too strong and the
            # comparison becomes informative -- so this fires to say so.
            untouched = [(t, d) for t, d in shifts if t < 0.005]
            unsat = [i for i, (t, _) in zip(both, shifts)
                     if t < 0.005 and 0.0 < o[i]["baseline"] < 1.0]
            assert not unsat, (
                f"zero-truncation items {unsat} are NOT saturated. The "
                f"manuscript states that every untruncated item sat at the "
                f"ceiling and so provides no evidence of measurement stability. "
                f"That caveat is now wrong -- and the comparison may be "
                f"stronger than claimed. Re-examine before publishing.")

    # Rebalancing effect (section 4.7). The manuscript quotes 0.47 -> 0.12, an
    # instrument-MATCHED pair: both measured under the 600-token budget, so the
    # only difference is option order. It also states the item's value under the
    # final instrument (0.13) so a reader recomputing from the primary screen is
    # not left with an apparent contradiction. All three are pinned, because the
    # temptation when they drift is to quote whichever pair looks tidiest.
    unb = _load("QUARANTINE_screen_bankA_ol_unbalanced_bank_2026-08-16.jsonl")
    bal = _load("QUARANTINE_screen_bankA_ol_truncation_budget600_2026-08-16.jsonl")
    cur = _load("screen_bankA_ol.jsonl")
    def _base(rows, item):
        if not rows:
            return None
        for r in rows:
            if r["item_id"] == item:
                return r["baseline"]
        return None
    b_unb, b_bal, b_cur = (_base(unb, "A-L01-01"), _base(bal, "A-L01-01"),
                           _base(cur, "A-L01-01"))
    if b_unb is not None and b_bal is not None:
        out.append(("rebalance: before (unbalanced, 600tok)", f"${b_unb:.2f}$",
                    "instrument-matched pair"))
        out.append(("rebalance: after (balanced, 600tok)", f"${b_bal:.2f}$",
                    "instrument-matched pair"))
    if b_cur is not None:
        out.append(("same item, final instrument", f"${b_cur:.2f}$",
                    "stated so the deposit is not self-contradictory"))

    # Roster counts. Fillable without any measurement -- the roster is frozen
    # in config -- so these were TBD longer than they needed to be. Pinned
    # because a roster change must not leave the manuscript claiming the old
    # counts.
    import config as _C
    gens = list(_C.OL_GENERATORS)
    fams = sorted({_C.vendor(m) for m in gens})
    out.append(("generators (ol roster)", _spell(len(gens)),
                "spelled out in the manuscript"))
    out.append(("generator vendor families", _spell(len(fams)),
                "spelled out in the manuscript"))

    # Retry and abort policy. Writing these two constants into Method prose put
    # each of them in a third place -- config.py, the code that reads it, and now
    # a sentence -- which is the shape of every drift defect this study has had.
    # Pin the prose to the constant so that changing one fails here, loudly and
    # immediately, rather than in review.
    # Holm families. R1 (model main effects) was vacated before data because the
    # model factor carries no hypothesis test; three families remain. The count
    # is pinned because the manuscript and the pre-registration disagreed about
    # it for as long as both existed -- the Analysis subsection claimed four
    # families in one paragraph and removed the tests from one of them three
    # paragraphs later. Nothing recomputes this from data; it is a design
    # commitment, and pinning it is what keeps the two documents in step.
    out.append(("Holm families", "within three pre-enumerated",
                "R1 vacated 2026-08-17; see spec section 6"))

    # Bank A composition. The deposited bank_a.json holds 60 items: 40 mcq and
    # 20 short-answer. Only the mcq are screenable, because headroom is defined
    # against options and a short-answer item has none. The manuscript says so,
    # but nothing pinned the split, so a reviewer recomputing from the deposit
    # would be the first to notice if it drifted -- which is the exact failure
    # this file exists to prevent.
    _bank_a = json.loads((BANKS / "bank_a.json")
                         .read_text(encoding="utf-8"))["items"]
    _n_short = sum(1 for i in _bank_a if i.get("type") == "short")
    _n_mcq = sum(1 for i in _bank_a if i.get("type") == "mcq")
    out.append(("Bank A mcq items", f"{_n_mcq} three-option multiple-choice items",
                "deposited bank_a.json"))
    out.append(("Bank A short-answer items", f"{_n_short} short-answer items",
                "deposited bank_a.json; excluded from the measure"))

    # Human-validation sample: a function of the roster, not a round number.
    #
    # THIS CHECK USED `_C.SOLICITATIONS` -- ALL FOUR -- AND SO AGREED WITH A
    # MANUSCRIPT THAT SAID 64 CELLS, while the study that ran has three
    # solicitations and 48 (spec 4.6 dropped S1 before data). Two copies of one
    # stale assumption confirming each other is precisely the drift this file
    # exists to catch, and it passed for two days. The roster's own keys are the
    # source of truth, and the realised design is asserted against them below so
    # that a constant and a measurement can never again disagree in silence.
    n_cells = len(gens) * len(_C.REGIMES) * len(_C.OL_SOLICITATION_KEYS)
    per_cell = 5
    out.append(("validation cells", f"{n_cells} cells",
                "generators x regimes x solicitations (ol roster), spelled out"))
    out.append(("validation sample", f"sample of {per_cell * n_cells} responses",
                f"{per_cell} per cell, re-derived after the S1 removal"))
    _gen_rows = _load("generate_bankA_ol.jsonl")
    if _gen_rows:
        realised = {(g["model"], g["regime"], g["solicitation_key"])
                    for g in _gen_rows if g.get("ok") and g.get("response")}
        assert len(realised) == n_cells, (
            f"the roster implies {n_cells} design cells but the responses "
            f"realise {len(realised)}; the sampling frame quoted in the "
            f"manuscript describes a design that was not run.")

    import solvability as _S
    out.append(("retry attempt cap",
                f"{_spell(_C.MAX_CALL_ATTEMPTS)} times in total",
                "C.MAX_CALL_ATTEMPTS, spelled out in the Method"))
    fraction_words = {0.25: "a quarter", 0.5: "half", 0.75: "three quarters"}
    frac = fraction_words.get(_S.MAX_CALL_FAILURE_RATE)
    out.append(("stage abort threshold",
                f"more than {frac} of its calls" if frac else None,
                "solvability.MAX_CALL_FAILURE_RATE, spelled out in the Method"
                if frac else
                f"MAX_CALL_FAILURE_RATE={_S.MAX_CALL_FAILURE_RATE} has no prose "
                "wording; add one to fraction_words and reword the Method"))

    # Injected-context overhead. Traces to data/telemetry/injected_context.json,
    # not to data/runs, so it needs its own loader. Pinned because the paragraph
    # was TBD while the measurement had existed for a day -- the numbers were in
    # the spec prose and in a telemetry file, and nobody had connected them.
    tel = ROOT / "data" / "telemetry" / "injected_context.json"
    if not tel.exists():
        out.append(("injected-context overhead", None, "telemetry file absent"))
    else:
        t = json.loads(tel.read_text())
        first = t["observations"][0]["tokens"]
        out.append(("overhead min", f"${min(first.values()):,}$".replace(",", "{,}"),
                    "identical 8-token probe"))
        out.append(("overhead max", f"${max(first.values()):,}$".replace(",", "{,}"),
                    "identical 8-token probe"))
        spread = max(first.values()) - min(first.values())
        out.append(("overhead spread", f"${spread:,}$".replace(",", "{,}"),
                    "aliased with the model factor"))
        if len(t["observations"]) > 1:
            second = t["observations"][1]["tokens"]
            deltas = [second[m] - first[m] for m in second if m in first]
            if deltas:
                approx = round(sum(deltas) / len(deltas))
                out.append(("overhead drift (mean)", f"${approx:,}$".replace(",", "{,}"),
                            "same-day re-probe"))

    # Placebo-section figures, from the PRIMARY panel. These are the numbers the
    # manuscript now argues from, replacing the pilot panel's.
    #
    # PROVENANCE, because the label is easy to over-read. Every figure below is
    # computed from the SCREEN arm alone -- `ol` is screen_bankA_ol.jsonl. The
    # manuscript introduces them with "On the panel used for the results
    # reported here", which a reader may take to span the placebo and hinted
    # arms too. It does not: those arms use a different, longer prompt, and
    # section 4.28 records minimax truncating on it at a budget the screen never
    # exhausted. When the placebo and solve arms exist, either recompute these
    # across all arms or qualify the sentence to name the arm -- do not leave a
    # screen-only rate standing as a study-wide one.
    if ol is not None:
        import analyse as _A
        d = _A.decline_breakdown(ol)
        if d.get("instrumented"):
            out.append(("primary: parseable rate", f"{100 * d['parsed_fraction']:.1f}\\%",
                        "placebo section"))
            out.append(("primary: decline rate", f"{100 * d['decline_fraction']:.1f}\\%",
                        "placebo section"))
            out.append(("primary: transport failures", f"{100 * d['transport_failed_fraction']:.1f}\\%",
                        "placebo section"))
        r_ol = _corr([r["parsed_fraction"] for r in ol], [r["baseline"] for r in ol])
        out.append(("primary: corr(parsed, baseline)", f"{r_ol:.2f}", "placebo section"))

    # Solver table, primary panel. Each row is checked, plus the all-candidates
    # column that carries the screen-validity argument (at/below chance on
    # retained items, far above it on the full set).
    if ol is not None:
        ol_ret = [r for r in ol if r["passes_headroom"]]
        ol_solvers = list(ol[0]["per_solver"].keys())

        def _t(rows, sv, f):
            return sum(r["per_solver"][sv][f] for r in rows)

        for sv in ol_solvers:
            short = sv.split("/")[-1].replace(":cloud", "").replace(":", "-")
            pr, ca = _t(ol_ret, sv, "parsed"), _t(ol_ret, sv, "calls")
            hi = _t(ol_ret, sv, "hits")
            out.append((f"{short}: parseable", f"{pr / ca:.2f}", "solver table"))
            out.append((f"{short}: cond. acc (retained)", f"{hi / pr:.3f}", "solver table"))
            pa, ha = _t(ol, sv, "parsed"), _t(ol, sv, "hits")
            out.append((f"{short}: cond. acc (all 40)", f"{ha / pa:.3f}", "solver table"))
            # The screen-validity claim, asserted rather than printed: every
            # solver must be at/below chance on retained items and above it on
            # the full candidate set. If this ever fails the screen is not doing
            # what the manuscript says it does.
            chance = ol_ret[0]["chance"] if ol_ret else 1 / 3
            assert hi / pr <= chance + 0.01, (
                f"{sv} scores {hi / pr:.3f} on RETAINED items, above chance "
                f"{chance:.3f}. The manuscript claims every solver sits at or "
                f"below chance there.")
            assert ha / pa > chance, (
                f"{sv} scores {ha / pa:.3f} over all candidates, not above "
                f"chance {chance:.3f}. The screen-validity argument fails.")

    # Bank sizes.
    for b, field in (("a", None), ("b", "n_candidates")):
        # Resolve through run._bank_path so a future bank id with a
        # non-default filename (e.g. `bs` -> bank_b_sample.json) cannot
        # silently miss here and report a PENDING that is really a bug.
        import run as _R
        f = _R._bank_path(b)
        if not f.exists():
            out.append((f"bank {b.upper()} size", None, f"{f.name} absent"))
            continue
        d = json.loads(f.read_text())
        mcq = [i for i in d["items"] if i.get("type") == "mcq"]
        if b == "a":
            out.append(("bank A total items", str(len(d["items"])), "manuscript"))
            out.append(("bank A mcq", str(len(mcq)), "manuscript"))
            out.append(("bank A short-answer", str(len(d["items"]) - len(mcq)), "manuscript"))
        else:
            out.append(("bank B candidates", str(d.get("n_candidates")), "manuscript"))
            out.append(("bank B retained", str(d.get("n_retained")), "manuscript"))

    # The realised call counts, and the Bank B sample screen.
    #
    # BANK A'S RETENTION, BASELINE RANGES AND INVARIANCE BAND ARE ALREADY
    # CHECKED ABOVE AND ARE NOT RECOMPUTED HERE. A first version of this block
    # emitted all of them a second time from its own loop -- two checks deriving
    # one number by two routes, which is the drift this file exists to detect,
    # appearing inside the detector. `ol` is None above when the screen is a
    # prefix, so it doubles as the completeness predicate; reuse it rather than
    # writing a second one.
    if ol:
        a_calls = sum(v["calls"] for r in ol for v in r["per_solver"].values())
        a_failed = sum(v["failed"] for r in ol for v in r["per_solver"].values())
        out.append(("bank A: solver calls", str(a_calls), "results screening subsection"))
        out.append(("bank A: transport failures", str(a_failed), "results"))
        out.append(("bank A: transport failure %",
                    f"{a_failed / a_calls * 100:.2f}", "results, two decimals"))
        out.append(("bank A: retained baseline median",
                    f"{st.median([r['baseline'] for r in ol if r['passes_headroom']]):.3f}",
                    "results"))

    bs = _load("screen_bankBS_ol.jsonl")
    if not bs:
        out.append(("bank B sample screen", None, "screen_bankBS_ol.jsonl absent"))
    else:
        bs_ret = [r for r in bs if r["passes_headroom"]]
        bs_rej = [r for r in bs if not r["passes_headroom"]]
        # The Bank B screen is a PREFIX BY DESIGN -- 44 of the 60 drawn, stopped
        # when the retention target was met -- so unlike Bank A it is not checked
        # against a full-bank row count. What keeps the prose honest is the
        # screened count itself, emitted as a literal: if the stage is resumed
        # and the file grows past 44, this check goes MISSING and the sentence
        # that says "44 of the 60 drawn" gets revisited. A partition needs both
        # groups, though, so a screen too short to have one reports PENDING
        # rather than computing min() of an empty set.
        out.append(("bank B sample: screened", str(len(bs)), "results, 44 of 60 drawn"))
        if not bs_ret or not bs_rej:
            out.append(("bank B sample: partition", None,
                        f"screen has {len(bs_ret)} retained and {len(bs_rej)} "
                        f"discarded; no partition to report yet"))
        else:
            calls = sum(v["calls"] for r in bs for v in r["per_solver"].values())
            parsed = sum(v["parsed"] for r in bs for v in r["per_solver"].values())
            failed = sum(v["failed"] for r in bs for v in r["per_solver"].values())
            lo, hi = S_wilson(len(bs_ret), len(bs))
            kept = [r["baseline"] for r in bs_ret]
            rej = [r["baseline"] for r in bs_rej]
            per_item = (bs[0].get("k_per_solver", 20)
                        * max(1, len(bs[0].get("per_solver") or {})))
            out.append(("bank B sample: retained", str(len(bs_ret)), "results"))
            out.append(("bank B sample: discarded", str(len(bs_rej)), "results"))
            out.append(("bank B sample: retention %",
                        f"{len(bs_ret) / len(bs) * 100:.1f}", "results"))
            out.append(("bank B sample: retention CI low", f"{lo:.2f}", "Wilson"))
            out.append(("bank B sample: retention CI high", f"{hi:.2f}", "Wilson"))
            out.append(("bank B sample: solver calls", str(calls), "results"))
            out.append(("bank B sample: parse %", f"{parsed / calls * 100:.1f}", "results"))
            out.append(("bank B sample: decline %",
                        f"{(calls - parsed - failed) / calls * 100:.1f}", "results"))
            out.append(("bank B sample: retained baseline median",
                        f"{st.median(kept):.3f}", "results"))
            out.append(("bank B sample: transport failures", str(failed), "results"))
            out.append(("bank B sample: retained baseline min", f"{min(kept):.3f}", "results"))
            out.append(("bank B sample: retained baseline max", f"{max(kept):.3f}", "results"))
            out.append(("bank B sample: discarded baseline min", f"{min(rej):.3f}", "results"))
            out.append(("bank B sample: band in solver calls",
                        f"{(min(rej) - max(kept)) * per_item:.0f}", "results"))
            # The claim the band supports, on both banks: nothing lies inside it.
            assert max(kept) < min(rej), (
                "a Bank B item's baseline sits inside the invariance band; the "
                "manuscript claims the partition is strict on both banks.")

    # The placebo floor. Complete: 14 retained Bank A items x 4 utterances.
    #
    # Every figure the placebo subsection quotes is recomputed here, INCLUDING
    # the per-item extremes. The pooled mean is the number a summary would keep,
    # and it is the one number that does not carry the finding: +0.048 pooled
    # against a per-item range of -0.144 to +0.500 (spec 4.37).
    plc = _load("placebo_bankA_ol.jsonl")
    scr_ol = _load("screen_bankA_ol.jsonl")
    if not plc or not scr_ol:
        out.append(("placebo floor", None, "placebo_bankA_ol.jsonl absent"))
    else:
        base = {r["item_id"]: r for r in scr_ol}
        pcalls = sum(v["calls"] for r in plc for v in r["per_solver"].values())
        pparsed = sum(v["parsed"] for r in plc for v in r["per_solver"].values())
        pfailed = sum(v["failed"] for r in plc for v in r["per_solver"].values())
        out.append(("placebo items", str(len(plc)), "retained Bank A"))
        out.append(("placebo calls", str(pcalls), "placebo subsection"))
        out.append(("placebo parse rate", f"{pparsed / pcalls * 100:.1f}", "per cent"))
        assert pfailed == 0, f"{pfailed} placebo transport failures; the text says none"
        # P2-I2: anchored twin of the bare `504` (also the P2/P3 reply counts)
        out.append(("placebo calls (anchored)",
                    f"${pcalls}$ reader calls, none failed, with a parseable reply on "
                    f"${pparsed / pcalls * 100:.1f}\\%$", "App B placebo floor"))

        pooled = [r["placebo_hinted"] for r in plc]
        m, lo, hi = A_mean_ci(pooled)
        out.append(("placebo pooled mean", f"{m:.3f}", "placebo subsection"))
        out.append(("placebo pooled CI low", f"{lo:.3f}", "placebo subsection"))
        out.append(("placebo pooled CI high", f"{hi:.3f}", "placebo subsection"))

        diffs = {r["item_id"]: r["placebo_hinted"] - base[r["item_id"]]["baseline"]
                 for r in plc if r["item_id"] in base}
        dm, dlo, dhi = A_mean_ci(list(diffs.values()))
        out.append(("placebo minus baseline, mean", f"{dm:.3f}", "placebo subsection"))
        out.append(("placebo minus baseline, CI low", f"{dlo:.3f}", "placebo subsection"))
        out.append(("placebo minus baseline, CI high", f"{dhi:.3f}", "placebo subsection"))
        out.append(("placebo minus baseline, min", f"{min(diffs.values()):.3f}", "per item"))
        out.append(("placebo minus baseline, max", f"{max(diffs.values()):.3f}", "per item"))
        # P2-I2: the per-item range as the Method prints it, anchoring the
        # twins `0.500` / `-0.144` (triage rows 84-85)
        out.append(("placebo minus baseline, range (anchored)",
                    f"by ${min(diffs.values()):.3f}$ to ${max(diffs.values()):.3f}$",
                    "Method: why the placebo is per item"))

        for sv in sorted({sv for r in plc for sv in r["per_solver"]}):
            short = sv.split("/")[-1].replace(":cloud", "").replace(":", "-")
            h = sum(r["per_solver"][sv]["hits"] for r in plc if sv in r["per_solver"])
            pa = sum(r["per_solver"][sv]["parsed"] for r in plc if sv in r["per_solver"])
            slo, shi = S_wilson(h, pa)
            out.append((f"placebo {short}", f"{h / pa:.3f}", "per-solver floor"))
            out.append((f"placebo {short} CI low", f"{slo:.3f}", "Wilson"))
            out.append((f"placebo {short} CI high", f"{shi:.3f}", "Wilson"))

        # The extreme item, quoted verbatim in the manuscript. Named rather than
        # recomputed as "the max", so that a change of which item is extreme
        # shows up as a MISSING check instead of silently renaming the example.
        worst = max(diffs, key=diffs.get)
        assert worst == "A-L09-01", (
            f"the largest placebo excess is now {worst}, not A-L09-01; the "
            f"placebo subsection names the item explicitly.")
        w_p = next(r for r in plc if r["item_id"] == worst)
        out.append(("A-L09-01 baseline", f"{base[worst]['baseline']:.3f}", "placebo subsection"))
        out.append(("A-L09-01 placebo", f"{w_p['placebo_hinted']:.3f}", "placebo subsection"))
        # P2-I2: App B's "credited with 0.500", the same item's excess, anchored
        out.append(("A-L09-01 placebo excess (anchored)",
                    f"credited with ${diffs[worst]:.3f}$ of recoverability",
                    "placebo minus masked baseline on A-L09-01"))
        g = "ol/gemma4:cloud"
        out.append(("A-L09-01 gemma4 placebo hits",
                    f"{w_p['per_solver'][g]['hits']}", "9 of 12"))
        assert base[worst]["per_solver"][g]["hits"] == 0, (
            "the text says gemma4 identified the key on 0 of 20 masked calls")
        # Both arms fully parseable is the whole point of the example: it rules
        # out willingness as the explanation. If either arm ever stops being
        # 100% parseable the sentence is wrong.
        assert (w_p["per_solver"][g]["parsed"] == w_p["per_solver"][g]["calls"]
                and base[worst]["per_solver"][g]["parsed"]
                == base[worst]["per_solver"][g]["calls"]), (
            "A-L09-01 gemma4 is not 100% parseable in both arms; the manuscript "
            "uses that fact to exclude willingness as the explanation.")

    # Guardrail activation, from the COMPLETE generate stage. These are the only
    # Results numbers available before `solve` finishes, because they describe
    # what the generator and verifier did rather than what a solver could infer.
    #
    # USABLE ROWS, NEVER `wc -l`. The file keeps its superseded ok=False rows as
    # an audit trail (spec 4.39): 2,195 lines, 2,016 measurements.
    gen = _load("generate_bankA_ol.jsonl")
    gen = [g for g in (gen or []) if g.get("ok") and g.get("response")]
    if not gen:
        out.append(("guardrail activation", None, "generate_bankA_ol.jsonl absent"))
    else:
        for reg in ("P2", "P3"):
            rows = [g for g in gen if g["regime"] == reg]
            blocked = sum(bool(g.get("blocked")) for g in rows)
            rej_cells = sum(1 for g in rows if g.get("verifier_rejections"))
            rej = sum(g.get("verifier_rejections") or 0 for g in rows)
            passed = sum(1 for g in rows if g.get("verifier_rejections")
                         and not g.get("blocked"))
            lo, hi = S_wilson(blocked, len(rows))
            out.append((f"{reg} cells", str(len(rows)), "generate, usable rows"))
            out.append((f"{reg} blocked", str(blocked), "guardrail subsection"))
            out.append((f"{reg} blocked rate", f"{blocked / len(rows) * 100:.1f}",
                        "guardrail subsection, per cent"))
            out.append((f"{reg} blocked CI low", f"{lo * 100:.1f}", "Wilson"))
            out.append((f"{reg} blocked CI high", f"{hi * 100:.1f}", "Wilson"))
            if reg == "P3":
                plo, phi = S_wilson(passed, rej_cells)
                # RE-TARGETED at P2-I2 (triage row 106): the bare `47` matched
                # any 47; App D's filter table prints it in this row.
                out.append(("P3 cells rejected at least once",
                            f"\\quad flagged at least once & {rej_cells} &",
                            "App D table, Bank A cell"))
                out.append(("P3 total rejections", str(rej), "manuscript"))
                out.append(("P3 repaired and passed", str(passed), "manuscript"))
                out.append(("P3 recovery rate", f"{passed / rej_cells * 100:.1f}",
                            "per cent of rejected cells"))
                out.append(("P3 recovery CI low", f"{plo * 100:.1f}", "Wilson"))
                out.append(("P3 recovery CI high", f"{phi * 100:.1f}", "Wilson"))
                # The manuscript says the residual blocks are a repair-BUDGET
                # limit, not a separate failure mode: every blocked P3 cell
                # should have exhausted `max_repairs=2`, i.e. three rejections.
                # If that ever stops holding the sentence is wrong, so assert it
                # here rather than trusting the prose.
                exhausted = sum(1 for g in rows if g.get("blocked")
                                and (g.get("verifier_rejections") or 0) >= 3)
                assert exhausted == blocked, (
                    f"{blocked} P3 cells blocked but {exhausted} exhausted the "
                    f"repair budget; the manuscript claims they are the same set.")
            # P0/P1 carry no verifier at all, so their zero is structural.
            assert all(not g.get("verifier_rejections") for g in gen
                       if g["regime"] in ("P0", "P1")), (
                "a P0/P1 cell recorded a verifier rejection; those regimes have "
                "no verifier and the manuscript says so.")

    # --- the assembled Results, read from results.py's own output ---------
    #
    # NOT RECOMPUTED HERE. Every other block in this file derives its numbers
    # from `data/runs/*.jsonl`, which is right for counts. The inferential
    # numbers are different: recomputing a Holm-adjusted contrast in a second
    # implementation is how two estimators come to disagree while both look
    # plausible (spec 4.33), and the whole point of `results.py` is that it is
    # the one implementation. So this block checks the manuscript against the
    # ASSEMBLED artefact, and separately asserts that the artefact came from the
    # data now on disk by comparing the recorded input hash.
    res_p = _C.ROOT / "data" / "results_bankA_ol.json"
    if res_p.exists():
        res = json.loads(res_p.read_text())
        solve_p = _C.ROOT / "data" / "runs" / "solve_bankA_ol.jsonl"
        want = hashlib.sha256(solve_p.read_bytes()).hexdigest()
        got = (res.get("inputs") or {}).get("solve_bankA_ol.jsonl")
        assert got == want, (
            f"results_bankA_ol.json was assembled from a different "
            f"solve file (recorded {str(got)[:12]}, on disk {want[:12]}). "
            f"Re-run `python3 results.py --bank A --roster ol`.")
        assert res.get("complete") is True, (
            "results_bankA_ol.json is stamped incomplete; the manuscript quotes "
            "it as a finished analysis.")

        d = res["descriptives"]["leak_adj"]
        # THE HEADLINE NUMBER, quoted in the abstract, the prevalence subsection
        # and the conclusion. It was the one figure in the abstract that no check
        # computed -- and an abstract is the part a reader trusts without looking.
        grand = sum(v["mean"] * v["n"] for v in d["regime"].values()) \
            / sum(v["n"] for v in d["regime"].values())
        out.append(("grand mean leak_adj", f"{grand:.3f}",
                    "weighted over regimes; abstract, prevalence, conclusion"))
        for reg, v in d["regime"].items():
            out.append((f"{reg} mean leak_adj", f"{v['mean']:.3f}", "results.py"))
            out.append((f"{reg} leak_adj CI low", f"{v['ci_low']:.3f}", "results.py"))
            out.append((f"{reg} leak_adj CI high", f"{v['ci_high']:.3f}", "results.py"))
        for mdl, v in d["model"].items():
            out.append((f"{mdl.split('/')[-1]} mean leak_adj",
                        f"{v['mean']:.3f}", "results.py"))
        # The solicitation means bound the R3 null ("a range narrower than the
        # width of any one of their intervals"), and pinning them also resolves a
        # literal COLLISION: S0's mean rounds to 0.611, which is a superseded
        # gemma4 accuracy in the stale list below. The documented rule is that a
        # superseded literal is stale only if no current check computes it -- so
        # the collision is resolved by pinning the current quantity, never by
        # deleting the stale entry (spec 4.46).
        sol = d["solicitation"]
        for k, v in sol.items():
            out.append((f"{k} mean leak_adj", f"{v['mean']:.3f}", "results.py"))
        lo = min(v["mean"] for v in sol.values())
        hi = max(v["mean"] for v in sol.values())
        widest = max(v["ci_high"] - v["ci_low"] for v in sol.values())
        assert (hi - lo) < widest, (
            f"the solicitation means span {hi - lo:.4f}, which is no longer "
            f"narrower than the widest interval ({widest:.4f}); the manuscript "
            f"says it is.")
        for fam in ("R2_mitigation", "R3_solicitation"):
            for name, c in res["families"][fam]["contrasts"].items():
                out.append((f"{name} estimate", f"{c['estimate']:.3f}", fam))
                out.append((f"{name} se", f"{c['se']:.3f}", fam))
        # The covariate decision is the reason those contrasts read as they do,
        # so its diagnostic is pinned too -- a reader must be able to check the
        # exclusion, and an unpinned justification drifts like any other number.
        diag = res["covariate_decision"]["diagnostics"]["prompt_tokens"]
        assert diag["post_treatment"] is True, (
            "prompt_tokens is no longer flagged post-treatment, but the "
            "manuscript's Analysis subsection says it is and reports the "
            "primary fit without it.")
        out.append(("prompt_tokens partial R2", f"{diag['partial_r2']:.3f}",
                    "regime on the covariate, after item/model/solicitation"))
        # The sensitivity pair must stay reported, and must stay a SE story
        # rather than an estimate story -- that is the claim in the prose.
        sens = res["covariate_sensitivity"]["families"]["R2_mitigation"]["contrasts"]
        prim = res["families"]["R2_mitigation"]["contrasts"]
        for name in ("P1-P0", "P3-P0"):
            assert sens[name]["se"] > 5 * prim[name]["se"], (
                f"{name}: the adjusted fit's se is no longer inflated, so the "
                f"manuscript's explanation of the exclusion is wrong.")
            assert abs(sens[name]["estimate"] - prim[name]["estimate"]) < 0.05, (
                f"{name}: the two specifications now disagree on the ESTIMATE, "
                f"not just its precision; the prose says otherwise.")
        out.append(("adjusted P1-P0 se", f"{sens['P1-P0']['se']:.3f}",
                    "sensitivity fit, reported in Analysis"))

        # Panel agreement, per rater pair. The single pooled ICC covered only the
        # largest panel (spec 4.50), so each panel is pinned separately.
        panels = res["panel_agreement_by_panel"]["panels"]
        assert len(panels) == 3, (
            f"{len(panels)} rater panels in the results but the manuscript "
            f"describes three; vendor exclusion determines this count.")
        for key, v in panels.items():
            short = "+".join(k.split("/")[-1].split(":")[0] for k in key.split("+"))
            out.append((f"ICC {short}", f"{v['icc_absolute_agreement_2_1']:.3f}",
                        f"n={v['n_responses']}"))
            out.append((f"ICC {short} CI low", f"{v['ci_low']:.3f}", "bootstrap"))
            out.append((f"ICC {short} CI high", f"{v['ci_high']:.3f}", "bootstrap"))
        # THE TWO CHANNELS (spec 4.53). The Method promises conditional
        # accuracy "alongside the primary outcome throughout"; the pair with
        # parsed fraction is what tells hiding the answer apart from refusing to
        # answer, and a drop in the primary outcome means opposite things under
        # the two readings.
        ca = res["descriptives"]["hinted_cond_acc"]["regime"]
        pf = res["descriptives"]["parsed_fraction"]["regime"]
        for reg in sorted(ca):
            out.append((f"cond acc {reg}", f"${ca[reg]['mean']:.3f}$",
                        f"n={ca[reg]['n']}"))
            out.append((f"cond acc {reg} CI",
                        f"$[{ca[reg]['ci_low']:.3f}, {ca[reg]['ci_high']:.3f}]$",
                        "mean_ci"))
        # The manuscript's claim is that WILLINGNESS barely moves while
        # option-choice falls. Both halves are assertions about the data and
        # both can drift, so both are checked rather than quoted.
        assert pf["P0"]["mean"] > 0.999, (
            f"P0 parsed fraction is {pf['P0']['mean']:.4f}; the manuscript "
            f"states 1.000, and the two-channel argument starts from it.")
        mit = [pf[r]["mean"] for r in pf if r != "P0"]
        assert 0.9855 <= min(mit) and max(mit) <= 0.9905, (
            f"mitigated parsed fractions now span [{min(mit):.4f}, "
            f"{max(mit):.4f}]; the manuscript says 0.986 to 0.990.")
        assert ca["P0"]["mean"] - max(ca[r]["mean"] for r in ca if r != "P0") > 0.10, (
            "conditional accuracy no longer drops by more than 0.10 under "
            "mitigation; the claim that the option-choice channel is the one "
            "that moved rests on that gap being large beside the willingness one.")

        # THE OPTION-COUNT STRATA (spec 4.54). The Limitations rest a
        # pre-registered stratified contrast on two factual claims about the
        # banks; both are checked against the frozen files, because the
        # stratification is only "where the criterion is identical" if they hold.
        strat = res["bank_contrast_by_option_stratum"]
        assert strat["items_without_option_count"] == 0, (
            f"{strat['items_without_option_count']} analysed rows have no option "
            f"count, so a stratum is quietly smaller than the bank it describes.")
        assert set(strat["strata"]) == {"3"}, (
            f"Bank A now spans option strata {sorted(strat['strata'])}; the "
            f"manuscript states it is entirely three-option, and the "
            f"stratified bank contrast is built on that.")
        assert strat["strata"]["3"]["pre_registered_primary"] is True

        # THE PRE-REGISTERED CEILING EXCLUSION, ACTUALLY RUN (spec 4.53).
        cs = res["ceiling_sensitivity"]
        out.append(("ceiling cells", f"${cs['n_ceiling_cells']}$",
                    f"of {cs['n_cells']} item-by-solver cells"))
        out.append(("item-by-solver cells", f"${cs['n_cells']}$", "retained items"))
        for key, label in (("within_solver_leak", "within-solver leak"),
                           ("within_solver_adjusted_level", "within-solver level")):
            b = cs[key]
            out.append((f"{label} all", f"${b['all']['mean']:.3f}$",
                        f"n={b['all']['n']}"))
            out.append((f"{label} excl ceiling",
                        f"${b['excluding_ceiling']['mean']:.3f}$",
                        f"n={b['excluding_ceiling']['n']}"))
        # Censoring at the top can only shrink a within-solver estimate, so a
        # NEGATIVE shift would mean the ceiling cells are not what the paper
        # says they are -- and the "conservative by a bounded amount" reading
        # would be unsupported in the direction that matters.
        for key in ("within_solver_leak", "within_solver_adjusted_level"):
            assert cs[key]["shift"] >= 0, (
                f"{key}: excluding ceiling cells LOWERED the mean by "
                f"{-cs[key]['shift']:.4f}. Censoring can only shrink an "
                f"estimate, so either the ceiling definition or the estimator "
                f"has changed.")
        assert cs["n_ceiling_cells"] == 2 and cs["n_cells"] == 42, (
            f"{cs['n_ceiling_cells']} of {cs['n_cells']} cells at ceiling; the "
            f"Method states 2 of 42 and the sensitivity check must be run on "
            f"the same cells the count describes.")

        # THE PRE-REGISTERED FALLBACK (spec 4.52). A low ICC makes these the
        # estimates of record, so they are manuscript numbers, not appendix
        # numbers, and they drift like any other.
        psl = res["per_solver_levels"]
        assert psl["skipped"]["no_placebo_row"] == 0, (
            "per-solver levels skipped responses for want of a placebo row; the "
            "printed levels would cover less of the design than the pooled mean.")
        assert psl["n_solvers"] == 3, (
            f"{psl['n_solvers']} solvers carry a per-solver level but the "
            f"manuscript prints three.")
        for sv, v in psl["per_solver"].items():
            short = sv.split("/")[-1].replace(":cloud", "")
            out.append((f"per-solver level {short}", f"${v['mean']:.3f}$",
                        f"n={v['n']}"))
            out.append((f"per-solver level {short} CI",
                        f"$[{v['ci_low']:.3f}, {v['ci_high']:.3f}]$", "mean_ci"))
            out.append((f"per-solver level {short} n",
                        f"${v['n']:,}$".replace(",", "{,}"), "responses scored"))
        out.append(("per-solver level spread", f"${psl['spread']:.3f}$",
                    "max - min across solvers"))
        # The claim that carries the paragraph: the panel disputes the RANKING,
        # not the magnitude. If a per-solver level ever falls to the floor, the
        # pooled headline really is an average over a leaky and a clean
        # instrument, and both the Results and the abstract would be wrong.
        assert min(v["ci_low"] for v in psl["per_solver"].values()) > 0.55, (
            "a per-solver interval has dropped to or below 0.55; the manuscript "
            "says every interval lies above it, and the 'ranking not level' "
            "reading of the low ICC depends on that.")
        # The prose says consistency tracks absolute agreement within 0.02, which
        # is what rules out a fixed per-solver offset.
        for key, v in panels.items():
            assert abs(v["icc_absolute_agreement_2_1"]
                       - v["icc_consistency_3_1"]) < 0.02, (
                f"panel {key}: absolute and consistency ICC now differ by more "
                f"than 0.02, so a systematic offset IS present and the "
                f"manuscript's reading of the gap is wrong.")

    # ---- the generator completion ceiling (spec 4.56/4.58) -------------------
    # DISCLOSED BECAUSE IT WAS NOT CHOSEN. Every tutor reply before 2026-08-19
    # was generated at `backends.chat`'s 400 default. These checks pin the
    # figures the Method now reports, and the assertions pin the two claims that
    # make the disclosure honest rather than decorative: the affected cells are
    # the LONG ones (so the ceiling marked verbosity rather than censoring the
    # answer), and they concentrate in P0 (so the bias is conservative for R2).
    gena = _load("generate_bankA_ol.jsonl")
    slva = _load("solve_bankA_ol.jsonl")
    if gena and slva:
        cap = C.GENERATOR_BUDGET_BEFORE_MEASUREMENT["value"]
        cells = {r["cell_key"]: (
                    any((c.get("completion_tokens") or 0) >= cap
                        for c in (r.get("calls") or [])),
                    len(r.get("response") or ""), r.get("regime"))
                 for r in gena if r.get("ok")}
        hit = [k for k, v in cells.items() if v[0]]
        out.append(("cap-affected Bank A cells", f"${len(hit)}$",
                    f"of {len(cells):,} usable, at the {cap}-token ceiling"))
        # P2-I2: App C states the count with its share and the ceiling; one
        # computation of `hit`, so the share cannot drift from the count.
        out.append(("cap-affected share of Bank A replies",
                    f"Of Bank~A's {len(cells):,} replies, ${len(hit)}$ "
                    f"({_pct(len(hit) / len(cells))}) contain at",
                    "cap-affected cells / usable cells"))
        out.append(("generator ceiling", f"a completion ceiling of {cap} tokens",
                    "config.GENERATOR_BUDGET_BEFORE_MEASUREMENT"))
        ch_hit = st.mean(cells[k][1] for k in hit)
        ch_rest = st.mean(v[1] for v in cells.values() if not v[0])
        out.append(("cap-affected mean chars", f"${ch_hit:.0f}$", "truncated cells"))
        out.append(("unaffected mean chars", f"${ch_rest:.0f}$", "all other cells"))
        # THE DIRECTION IS THE FINDING. If truncated replies were the SHORT ones
        # the ceiling would be censoring answers out of replies, the bias would
        # run the other way, and "conservative" would be false.
        assert ch_hit > ch_rest, (
            "truncated replies are no longer the long ones, so the Method's "
            "reading of the ceiling -- that it marked verbose replies rather "
            "than removing the answer from them -- is now wrong.")
        n_p0 = sum(1 for k in hit if cells[k][2] == "P0")
        out.append(("cap-affected P0 cells", f"${n_p0}$",
                    "of the affected cells, in the R2 reference regime"))
        n_p3 = sum(1 for k in hit if cells[k][2] == "P3")
        out.append(("cap-affected P3 cells", f"${n_p3}$",
                    "of the affected cells, in the fully mitigated regime"))
        # THIS ASSERTION USED TO CARRY A DIRECTIONAL CLAIM, AND THE CLAIM WAS
        # WRONG. It read "the cap-affected cells no longer concentrate in P0, so
        # the claim that the resulting bias makes R2 CONSERVATIVE no longer
        # follows" -- inferring the SIGN of the displacement from WHERE the
        # affected cells sit. Measured by regenerating them (spec 4.60), the
        # affected P0 cells came back LOWER and the affected P3 cells much
        # higher, so both contrasts against P0 shrink and the published
        # estimates slightly OVERSTATE mitigation. The concentration is still a
        # fact the manuscript states, so it is still checked; what it is no
        # longer allowed to do is stand in for the measurement below.
        assert n_p0 * 2 > len(hit), (
            "the cap-affected cells no longer concentrate in P0, which the "
            "Results state as a fact about the affected subset.")
        la = {True: [], False: []}
        for r in slva:
            v, k = r.get("leak_adj"), r.get("cell_key")
            if v is not None and k in cells:
                la[cells[k][0]].append(v)
        out.append(("cap-affected leak_adj", f"${st.mean(la[True]):.3f}$",
                    "mean over affected cells"))
        out.append(("unaffected leak_adj", f"${st.mean(la[False]):.3f}$",
                    "mean elsewhere"))

    # ---- leave-one-item-out influence (spec 4.62) -------------------------------
    # AT 14 ITEMS A SINGLE ITEM IS A FOURTEENTH OF THE DESIGN, so "does one item
    # carry this conclusion?" is a question the Results must answer rather than
    # leave to a referee. Read from `results.py`'s canonical output -- never
    # recomputed here, which would be a second estimator (spec 4.33).
    rp = ROOT / "data" / "results_bankA_ol.json"
    if rp.exists():
        ii = (json.loads(rp.read_text(encoding="utf-8")) or {}).get(
            "item_influence") or {}
        r2 = (ii.get("families") or {}).get("R2_mitigation") or {}
        if r2:
            out.append(("item count under fixed effects", f"${ii['n_items']}$",
                        "Bank A items, the deletion denominator"))
            for k in ("P1-P0", "P3-P0", "P3-P2"):
                lo, hi = r2[k]["estimate_range"]
                out.append((f"{k} deletion range",
                            f"$[{lo:+.3f}, {hi:+.3f}]$",
                            "over all single-item deletions"))
            out.append(("P2-P1 faintest", f"${r2['P2-P1']['faintest_estimate']:+.3f}$",
                        "worst single-item deletion"))
            out.append(("P1-P0 effect retained",
                        f"${r2['P1-P0']['retained_fraction_of_effect'] * 100:.0f}\\%$",
                        "of magnitude, worst deletion"))
            n_flip = len(r2["P2-P1"]["verdict_flipping_items"])
            out.append(("marginal contrasts flipping items", f"${n_flip}$",
                        "of the deletions, for P2-P1 and P3-P2"))
            # LOAD-BEARING, BOTH DIRECTIONS.
            # The Results report the SIGN of each R2 contrast as the finding and
            # its magnitude as provisional; that split is only honest while the
            # signs actually hold.
            for k, v in r2.items():
                assert v["sign_stable"], (
                    f"{k} changes sign under a single-item deletion, so the "
                    f"Results' claim that the direction of each R2 contrast is "
                    f"robust while its magnitude is provisional no longer holds.")
            # And the two P0 contrasts are reported as estimates rather than
            # directions, which requires their verdicts to survive deletion.
            for k in ("P1-P0", "P3-P0"):
                assert not r2[k]["verdict_flipping_items"], (
                    f"{k} now loses its verdict under deletion of "
                    f"{r2[k]['verdict_flipping_items']}; the Results read it as "
                    f"an estimate, not merely a direction.")

    # ---- the measured cap sensitivity (spec 4.60) ------------------------------
    # THE PAIRED CELL MEAN DOES NOT SETTLE A CLAIM ABOUT THE CONTRASTS, so both
    # are checked: the paired difference over the 127 regenerated cells, and the
    # re-estimation of every pre-registered contrast with those cells substituted
    # into the analysis frame. Read from `cap_sensitivity`'s own artefact rather
    # than recomputed here -- a second implementation of the analysis rule is
    # what spec 4.33 is about.
    cc = ROOT / "data" / "capcheck_contrasts_bankA_ol.json"
    capr = _load("capcheck_solve_bankA_ol.jsonl")
    if cc.exists() and capr and slva:
        cs = json.loads(cc.read_text(encoding="utf-8"))
        base = {r["cell_key"]: r["leak_adj"] for r in slva
                if r.get("leak_adj") is not None}
        pairs, byreg = [], {}
        for r in capr:
            k, v = r.get("cell_key"), r.get("leak_adj")
            if v is None or k not in base:
                continue
            d = v - base[k]
            pairs.append(d)
            byreg.setdefault(r.get("regime"), []).append(d)
        m = st.mean(pairs)
        se = st.stdev(pairs) / len(pairs) ** 0.5
        out.append(("capcheck cells", f"${len(pairs)}$", "regenerated and re-solved"))
        out.append(("capcheck paired difference", f"${m:+.3f}$",
                    "uncapped minus capped adjusted leakage"))
        out.append(("capcheck paired se", f"${se:.3f}$", "paired standard error"))
        # ONE LITERAL, BECAUSE THE PAPER WRITES ONE. The interval is typeset as a
        # single math group, `$[-0.004, +0.051]$`, so checking the two bounds
        # separately looks for `$-0.004$` and `$+0.051$` -- literals that do not
        # and should not appear. Matching what is actually typeset, not loosening.
        z = 1.96
        out.append(("capcheck 95% CI",
                    f"$[{m - z * se:+.3f}, {m + z * se:+.3f}]$",
                    "paired difference interval"))
        # P2-I2: the interval's level, from the same z that builds it.
        out.append(("capcheck interval level",
                    f"{_level_from_z(z)}\\% interval $[{m - z * se:+.3f}, {m + z * se:+.3f}]$",
                    "level of the paired interval"))
        out.append(("capcheck max shift", f"${cs['max_shift_in_se']:.2f}$",
                    "largest contrast displacement, in standard errors"))
        r2 = cs["families"]["R2_mitigation"]
        for k in ("P1-P0", "P3-P0"):
            out.append((f"capcheck {k} uncapped", f"${r2[k]['uncapped']:+.3f}$",
                        f"re-estimated ({r2[k]['published']:+.3f} as published)"))
        # LOAD-BEARING. The manuscript discloses the ceiling INSTEAD of re-running
        # the bank, and that choice is licensed by exactly one thing: no
        # pre-registered contrast changes its verdict. If one ever does, the
        # published estimate is an artefact of an unmeasured library default and
        # disclosure is not an adequate response to it.
        assert not cs["verdict_flips"], (
            f"the 400-token ceiling flips {cs['verdict_flips']}, so the Results' "
            f"decision to disclose it rather than re-run Bank A is no longer "
            f"defensible.")
        # AND THE DIRECTION, which the Results now state explicitly after the
        # cell-count argument for it turned out to be backwards.
        assert st.mean(byreg["P0"]) < 0 < st.mean(byreg["P3"]), (
            "the Results state that the affected P0 cells returned LOWER "
            "adjusted leakage on regeneration and the affected P3 cells higher; "
            f"measured now, P0 {st.mean(byreg['P0']):+.4f} and P3 "
            f"{st.mean(byreg['P3']):+.4f}.")

    # ---- the cost of extending to Bank B --------------------------------------
    # A VAGUE MULTIPLIER IS NOT A CHECKABLE CLAIM. The Results said extending the
    # design to Bank B "would have cost roughly three times the solver budget the
    # study has consumed in total". Nothing computed it, so nothing caught that it
    # was inherited from a plan to screen ALL of Bank B (~130 retained items) and
    # never recomputed for the 27-item sample, where the true ratio is about 1.25.
    # The decision to omit Bank B rested on that number. Stated as counts now, so
    # it is verifiable rather than merely plausible.
    solver_calls = 0
    for name in ("screen_bankA_ol.jsonl", "placebo_bankA_ol.jsonl",
                 "solve_bankA_ol.jsonl", "screen_bankBS_ol.jsonl",
                 # Added 2026-08-23 when Bank B's response stage completed. The
                 # description said "both banks" while the list omitted this
                 # file, so the total was right for the study as it stood and
                 # wrong for the study as described -- a check can be stale in
                 # its SCOPE without any number being wrong.
                 "placebo_bankBS_ol.jsonl", "solve_bankBS_ol.jsonl"):
        rows = _load(name) or []
        for r in rows:
            ps = r.get("per_solver")
            if isinstance(ps, dict):
                solver_calls += sum((v or {}).get("calls", 0) or 0
                                    for v in ps.values())
    if solver_calls:
        out.append(("solver calls consumed",
                    f"${solver_calls:,}$".replace(",", "{,}"),
                    "screening + placebo + response stages, both banks"))
        slv = _load("solve_bankA_ol.jsonl") or []
        if slv:
            # Calls per response, taken from the realised data rather than from
            # the roster size -- vendor exclusion makes the panel a function of
            # the generator (spec 4.50), so |solvers| is not the multiplier.
            per_resp = sum(sum((v or {}).get("calls", 0) or 0
                               for v in (r.get("per_solver") or {}).values())
                           for r in slv) / len(slv)
            # RETIRED 2026-08-23: this used to price the Bank B extension as a
            # PROJECTION (n_retained x 48 cells x 3 reps x calls-per-response).
            # Bank B has now been run, so the projection is not a claim the
            # manuscript makes any more -- and leaving the check in place would
            # have been worse than useless: a superseded literal is flagged only
            # if NO CURRENT CHECK COMPUTES IT, so this check would have silently
            # neutered the `$18{,}612$` entry appended below. Replaced by the
            # REALISED count, which the R4 section now reports.
            bsolve = _load("solve_bankBS_ol.jsonl") or []
            if bsolve:
                bs_calls = sum(sum((v or {}).get("calls", 0) or 0
                                   for v in (r.get("per_solver") or {}).values())
                               for r in bsolve)
                out.append(("bank B solver calls",
                            f"${bs_calls:,}$".replace(",", "{,}"),
                            f"realised over {len(bsolve)} responses"))

    # ---- Bank B's own mitigation family, quoted in the R4 subsection ----------
    # These are here for their own sake AND because one of them collides with a
    # superseded literal: Bank B's P2-P1 adjusted p is 0.097, which is also
    # gemma4's pre-re-screen conditional accuracy. The superseded list is
    # literals, not quantities, so the ONLY thing that stops it flagging a
    # correct sentence is a current check computing the same string. Suppressing
    # the guard instead would have been the cheaper edit and the wrong one.
    bsres_p = ROOT / "data" / "results_bankBS_ol.json"
    if bsres_p.exists():
        bsres = json.loads(bsres_p.read_text(encoding="utf-8"))
        # RE-TARGETED at P2-I2 (triage rows 205, 209, 211): the paper prints
        # these p values only in Table 3, as `<0.001` (which the bare `0.000`
        # never matched -- it passed on unrelated `0.000`s) and as `0.095`,
        # which App B's "from 0.081 to 0.095" twins. Each is now the Table 3
        # row its cell sits in, compared cell by cell (`_table3_rows`). P2-P1
        # (row 207) keeps its bare `0.097`: it has no twin in the paper, and it
        # is the current literal that keeps the superseded `0.097` (gemma4's
        # pre-re-screen accuracy) from flagging this correct cell.
        t3 = _table3_rows()
        for name, c in (bsres["families"]["R2_mitigation"]["contrasts"]).items():
            out.append((f"bank BS {name} estimate", f"{c['estimate']:.3f}",
                        "R2 within Bank B"))
            if name == "P2-P1":
                out.append((f"bank BS {name} adj p", f"{c['p_adjusted']:.3f}",
                            "Holm-adjusted within R2"))
            else:
                lit, note = t3.get(("a", name), (None, "Table 3 artefacts absent"))
                out.append((f"bank BS {name} adj p", lit,
                            f"Holm-adjusted within R2, printed "
                            f"{_p_table(c['p_adjusted'])} in its Table 3 row; {note}"))

    # ---- the deposit's licence scan (Data availability) ------------------------
    # The manuscript states how much Bank B text the withheld logs carry. That
    # figure came from a hand-run scan that could not see a multi-line stem, so
    # it is now computed by the released script on every check.
    try:
        sys.path.insert(0, str(_C.ROOT / "harness" / "ops"))
        import release_scan as _rs
        logs = [ROOT / "data" / "runs" / "generate_bankBS_ol.jsonl",
                ROOT / "data" / "runs" /
                ("QUARANTINE_generate_bankBS_ol_generator_cap400_PARTIAL"
                 "_clean_rows_kept_2026-08-20_a61f4004.jsonl")]
        if all(p.exists() for p in logs):
            prot = _rs.protected_strings()
            union, total = set(), 0
            for f in logs:
                hay = _rs._norm(f.read_text(encoding="utf-8", errors="ignore"))
                hit = {x for x in prot if _rs._norm(x) in hay}
                union |= hit
                total += sum(hay.count(_rs._norm(x)) for x in hit)
            out.append(("withheld-log protected occurrences", f"${total}$",
                        f"{len(union)} distinct option strings"))
    except ImportError:
        out.append(("withheld-log protected occurrences", None,
                    "ops/release_scan.py not importable"))

    # ---- Tier-3 human validation (sec:results-human) ---------------------------
    # Read from the assembled artefact, never recomputed here (4.41). The
    # adjudication counterfactual is checked too: the manuscript says the one
    # re-read cell changes nothing, and a claim of that shape with nothing
    # computing it is exactly the literal that goes stale unnoticed (4.46).
    hv_p = ROOT / "data" / "human_validation_results.json"
    if not hv_p.exists():
        out.append(("human validation agreement", None,
                    "run `python3 human_validation.py assemble`"))
    else:
        hv = json.loads(hv_p.read_text(encoding="utf-8"))
        pa, gc = hv["percent_agreement"], hv["agreement_given_both_committed"]
        cons, eith = hv["recovered_consensus"], hv["recovered_either"]
        st_ = hv["structure"]
        out += [
            ("human validation n", f"${hv['n']}$", "sampled responses"),
            ("labels collected", f"${2 * hv['n']}$", "two annotators x n"),
            ("agreement count", f"${pa['k']}$", "responses both labelled alike"),
            ("percent agreement", f"${100 * pa['p']:.1f}\\%$", "of 240"),
            ("agreement CI", f"$[{pa['ci'][0]:.3f}, {pa['ci'][1]:.3f}]$",
             "Wilson"),
            ("cohen kappa", f"${hv['cohen_kappa']:.3f}$", "chance-corrected"),
            ("both committed", f"${gc['n']}$", "neither said cannot_tell"),
            ("same option", f"${gc['k']}$", "and agreed on which"),
            ("conditional agreement", f"${100 * gc['p']:.1f}\\%$",
             "given both committed"),
            ("conditional agreement CI",
             f"$[{gc['ci'][0]:.3f}, {gc['ci'][1]:.3f}]$", "Wilson"),
            ("disagreements", f"${hv['n'] - pa['k']}$", "240 minus agreements"),
            ("one-sided cannot_tell", f"${st_['exactly_one_cannot_tell']}$",
             "of those disagreements"),
            ("A1 cannot_tell", f"${hv['per_annotator']['A1']['cannot_tell']}$",
             "declined"),
            ("A2 cannot_tell", f"${hv['per_annotator']['A2']['cannot_tell']}$",
             "declined"),
            ("consensus recovered", f"${cons['k']}$", "both chose the key"),
            ("consensus rate", f"${100 * cons['p']:.1f}\\%$", "of 240"),
            ("consensus CI", f"$[{cons['ci'][0]:.3f}, {cons['ci'][1]:.3f}]$",
             "Wilson"),
            ("liberal recovered", f"${eith['k']}$", "either chose the key"),
            ("liberal rate", f"${100 * eith['p']:.1f}\\%$", "of 240"),
            ("liberal CI", f"$[{eith['ci'][0]:.3f}, {eith['ci'][1]:.3f}]$",
             "Wilson"),
        ]
        for t in ("0.1", "0.25", "0.5"):
            d = hv["by_threshold"][t]
            out += [
                (f"sensitivity at {t}", f"${d['sensitivity']:.3f}$", "vs consensus"),
                (f"specificity at {t}", f"${d['specificity']:.3f}$", "vs consensus"),
            ]
        adj = hv.get("adjudication_sensitivity")
        if adj:
            out += [
                ("adjudicated cells", f"${adj['n_adjudicated']}$",
                 "read back by their annotator"),
                ("alt agreement",
                 f"${100 * adj['alt_percent_agreement']:.1f}\\%$",
                 "under the other reading"),
                ("alt kappa", f"${adj['alt_cohen_kappa']:.3f}$", "same"),
            ]

    # ---- family R4 and the option-count strata (pooled A + BS) ------------------
    # These report PENDING until `results.py --pool A,bs` has run, which is the
    # intended state while `solve bs` is in flight: the manuscript still carries
    # \TBD for R4, so there is nothing yet to be stale against.
    #
    # Read from the ASSEMBLED artefact, never recomputed here. A second
    # implementation of a Holm-adjusted contrast is how two estimators come to
    # disagree while both look plausible (spec 4.33).
    pool_p = ROOT / "data" / "results_pooled_A-BS_ol.json"
    if not pool_p.exists():
        out.append(("R4 joint bank x regime", None,
                    "run `python3 results.py --pool A,bs --roster ol`"))
        out.append(("option-count strata", None, "same pooled artefact"))
    else:
        pool = json.loads(pool_p.read_text(encoding="utf-8"))
        for stem in ("solve_bankA_ol.jsonl", "solve_bankBS_ol.jsonl"):
            sp = ROOT / "data" / "runs" / stem
            want = hashlib.sha256(sp.read_bytes()).hexdigest()
            got = (pool.get("inputs") or {}).get(stem)
            assert got == want, (
                f"results_pooled_A-BS_ol.json was assembled from a different "
                f"{stem} (recorded {str(got)[:12]}, on disk {want[:12]}). "
                f"Re-run `python3 results.py --pool A,bs --roster ol`.")
        assert pool.get("complete") is True, (
            "results_pooled_A-BS_ol.json is stamped incomplete; the manuscript "
            "quotes R4 as a finished analysis.")

        r4 = (pool.get("families") or {}).get("R4_robustness") or {}
        jt = r4.get("joint_test") or {}
        if jt:
            out.append(("R4 joint F", f"{jt['F']:.2f}", "bank x regime, one test"))
            out.append(("R4 df", f"{jt['df_num']}", "interaction dummies"))
            # The threshold is alpha/2 at the PRE-REGISTERED m, not alpha/1 at
            # the testable size: re-sizing after the data existed would loosen
            # the survivor, which this project never does.
            h = r4.get("holm") or {}
            if h:
                out.append(("R4 Holm threshold", f"{h['threshold']:.3f}",
                            "alpha/2 at the pre-registered m=2"))
        strat = (pool.get("bank_contrast_by_option_stratum") or {})
        blocks = strat.get("strata") or {}
        assert strat.get("items_without_option_count", 0) == 0, (
            f"{strat.get('items_without_option_count')} analysed rows have no "
            f"option count, so a stratum is quietly smaller than the bank it "
            f"describes.")
        # THE LIMITATIONS PROMISE HAS CONTENT ONLY ONCE BANK B ARRIVES: with
        # Bank A alone the paper occupies stratum 3 by itself, so "we report the
        # other strata separately" was vacuously true. Assert the strata really
        # did multiply rather than trusting the prose.
        # Expressed as a SET, not a count: "more than one" is a threshold and a
        # small integer cannot say which strata were expected. Bank A occupies
        # the three-option stratum alone, so the promise has content exactly
        # when the pooled strata are not that single set.
        assert set(blocks) != {"3"}, (
            f"pooled analysis spans only option strata {sorted(blocks)} -- the "
            f"same single stratum Bank A occupies. The Limitations promise to "
            f"report the other strata separately is still vacuous, so either "
            f"the pool is wrong or the promise is.")
        for n_opt in sorted(blocks, key=lambda k: int(k)):
            for bank, v in sorted((blocks[n_opt].get("by_bank") or {}).items()):
                out.append((f"stratum {n_opt}-option, bank {bank}: mean",
                            f"{v['mean']:.3f}",
                            f"n={v['n_rows']} rows"))

    # TIER-1 VERIFIER AUDIT. The verifier IS the P2/P3 treatment, so its error
    # rates belong to the regimes and not to the harness. The manuscript cited
    # this audit twice from the Results and described it nowhere until the
    # fourth review round -- a number with no provenance, which no check could
    # see because the check would have had to notice an ABSENCE.
    #
    # Recomputed here rather than read from a file: `verifier_audit` reads the
    # frozen banks, which are the artefact, and it is the same call the CLI
    # makes. A stale copy of these figures is exactly what the audit is for.
    for bank in ("A", "bs"):
        try:
            a = _verifier_audit(bank)
        except (Exception, SystemExit) as e:
            # SystemExit, not just Exception: `run.load_bank` reports a missing
            # bank with sys.exit, which inherits from BaseException and sails
            # straight through `except Exception`. Written the narrow way this
            # guard could not fire, and the public copy -- which withholds
            # Bank B by design -- went from 6 expected failures to 19.
            out.append((f"verifier audit, bank {bank}", None, f"unavailable: {e}"))
            continue
        out.append((f"verifier sensitivity, bank {bank}",
                    f"${a['sens_n']}/{a['sens_d']}$",
                    f"{a['sensitivity']:.1%} on the stress corpus"))
        out.append((f"verifier false positives, bank {bank}",
                    f"${a['fp_n']}/{a['fp_d']}$",
                    f"{a['fp_rate']:.1%} on answer-free text"))
    # The integrated C&E manuscript (P2-I2): every `%% NEW:` quantity. `ol` is
    # passed so that screen-derived figures stay behind the completeness gate.
    out += _ce_checks(ol)
    return out


# ---------------------------------------------------------------------------
# THE INTEGRATED C&E MANUSCRIPT -- P2-I2 (2026-11-11)
# ---------------------------------------------------------------------------
# Every `%% NEW: <source>` quantity of the restructured paper, computed from
# the source its tag names. The helpers below render a value the way the
# manuscript prints it; none carries a number of its own beyond the reporting
# conventions their docstrings name. A context phrase in a literal is there to
# anchor a twin (the same string meaning something else elsewhere) and is
# always on ONE source line: presence is tested on the comment-stripped text,
# newlines included.

def _data(name: str):
    """A `data/*.json` artefact, or None."""
    p = ROOT / "data" / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _tex_int(n: int) -> str:
    """2016 -> 2{,}016, the form used inside maths."""
    return f"{int(n):,}".replace(",", "{,}")


def _pct(x: float) -> str:
    """A share as the prose prints it: 0.9802 -> 98.0\\%."""
    return f"{100 * x:.1f}\\%"


def _pct_bound(x: float) -> str:
    """One bound of a percentage interval: one decimal, except that a bound
    at 100% prints as 100, as in $[94.2, 100]$."""
    s = f"{100 * x:.1f}"
    return "100" if s == "100.0" else s


def _pct_ci(ci) -> str:
    return f"$[{_pct_bound(ci[0])}, {_pct_bound(ci[1])}]$"


def _signed_ci(lo: float, hi: float) -> str:
    """A contrast interval in prose: explicit signs, $[-0.053, +0.001]$."""
    return f"$[{lo:+.3f}, {hi:+.3f}]$"


def _table_ci(lo: float, hi: float) -> str:
    """A contrast interval in Table 3: braced, no plus sign."""
    return f"{{[{lo:.3f}, {hi:.3f}]}}"


def _p_table(p: float) -> str:
    """A Holm p as Table 3 prints it, by the convention its source comment
    states: <0.001 below 0.001, three decimals below 0.10, two above."""
    if p < 0.001:
        return "<0.001"
    return f"{p:.3f}" if p < 0.10 else f"{p:.2f}"


_DAGGER = "\\rlap{$^{\\dagger}$}"


def _disagree(reject: bool, ci) -> bool:
    """Table 3's dagger: the Holm verdict and the item-resampled interval
    disagree -- one rejects while the other includes zero, or the reverse."""
    return bool(reject) != (ci[0] > 0 or ci[1] < 0)


def _level_from_z(z: float) -> int:
    """Coverage in per cent of a two-sided normal interval at z (95 at 1.96)."""
    return round(100 * (2 * st.NormalDist().cdf(z) - 1))


def _ceil_at(x: float, nd: int) -> str:
    """The smallest `nd`-decimal number >= x. A sentence saying that nothing
    moves 'by more than X' is true only when X >= the exact value, so a bound
    is rendered by ceiling, never by rounding (P2-I2: 0.000661 is not
    'no more than 0.00066')."""
    import math
    v = math.ceil(x * 10 ** nd - 1e-9) / 10 ** nd
    return f"{v:.{nd}f}"


def _floor_pct_at_ten(x: float) -> str:
    """'at least N%' for a share, N a multiple of ten at or below it."""
    import math
    return str(int(math.floor(x * 10 + 1e-9) * 10))


# 'about <fraction>' in running prose. A share is 'about' the NEAREST fraction
# of this vocabulary (a tie goes to the smaller denominator), and only when it
# lies within _ABOUT_TOL of it -- so the words cannot drift from the number.
# The tolerance is the one first written (0.025). P2-I2 briefly raised it to
# 0.05 after "about half" for 55.0% failed; that was a check loosened by the
# sentence it checks, so it was reverted and the sentence is reported instead.
_FRACTION_WORDS = {(1, 2): "half", (1, 3): "a third", (2, 3): "two thirds",
                   (1, 4): "a quarter", (3, 4): "three quarters",
                   (1, 5): "a fifth", (2, 5): "two fifths",
                   (3, 5): "three fifths", (4, 5): "four fifths",
                   (1, 10): "a tenth", (3, 10): "three tenths",
                   (7, 10): "seven tenths", (9, 10): "nine tenths"}
_ABOUT_TOL = 0.025


def _about_fraction(x: float) -> str | None:
    """The fraction word a share is 'about', or None if no word fits."""
    best = min(_FRACTION_WORDS,
               key=lambda f: (round(abs(x - f[0] / f[1]), 9), f[1]))
    return (_FRACTION_WORDS[best]
            if round(abs(x - best[0] / best[1]), 9) <= _ABOUT_TOL else None)


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{_spell(n)} times")


def _ordinal(i: int) -> str:
    """0 -> first: an option POSITION as the prose names it."""
    return ("first", "second", "third", "fourth", "fifth")[i]


def _rname(model: str) -> str:
    """ol/gpt-oss:120b-cloud -> gpt-oss:120b: a model as the paper names it."""
    return model.split("/")[-1].replace("-cloud", "").replace(":cloud", "")


def _code_threshold_checker() -> int | None:
    """The `len(key_text) >= N` of checker.check_explicit's option_verbatim
    rule, read from the code: the rule has no named constant, and a typed copy
    here would be a second copy of it."""
    import ast
    tree = ast.parse((ROOT / "harness" / "checker.py").read_text(encoding="utf-8"))
    hits = [n.comparators[0].value for n in ast.walk(tree)
            if isinstance(n, ast.Compare) and isinstance(n.left, ast.Call)
            and getattr(n.left.func, "id", "") == "len" and n.left.args
            and getattr(n.left.args[0], "id", "") == "key_text"
            and isinstance(n.ops[0], ast.GtE)
            and isinstance(n.comparators[0], ast.Constant)]
    return hits[0] if len(hits) == 1 else None


def _code_size_bank_b_target() -> int | None:
    """`size_bank_b.py --target`'s default: the retained-item target fixed
    in advance, read from its argparse declaration."""
    import ast
    tree = ast.parse((ROOT / "harness" / "size_bank_b.py").read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "add_argument"
                and n.args and isinstance(n.args[0], ast.Constant)
                and n.args[0].value == "--target"):
            for kw in n.keywords:
                if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                    return kw.value.value
    return None


# A CLAIM THAT STOPS BEING TRUE MUST FAIL, NOT WAIT. A check whose literal is
# a sentence's claim (a verdict, an ordering, "all", a fraction word) emits
# this when the data no longer support it: a string the paper cannot hold, so
# the check is MISSING (exit 1). None stays reserved for data that are ABSENT
# (PENDING); emitting None for a false claim would pass the run with exit 0.
_FALSE = "<<claim not supported by the data -- see this check's note>>"


def _ce_checks(ol) -> list[tuple[str, str | None, str]]:
    """The C&E manuscript's `%% NEW:` quantities (P2-I2), section by section.

    `ol` is the Bank A screen as `checks()` gated it: None while incomplete,
    so every screen-derived figure here waits for the complete set too.
    """
    out: list[tuple[str, str | None, str]] = []
    import inspect

    import analyse as _A
    import config as _C
    import pipelines as _P
    import results as _Res

    resA, resB = _data("results_bankA_ol.json"), _data("results_bankBS_ol.json")
    pool = _data("results_pooled_A-BS_ol.json")
    exA, exB = _data("exploratory_bankA_ol.json"), _data("exploratory_bankBS_ol.json")
    exP = _data("exploratory_pooled_A-BS_ol.json")
    hv = _data("human_validation_results.json")
    if not all((resA, resB, pool, exA, exB, exP, hv)):
        out.append(("C&E manuscript checks", None,
                     "a results, exploratory or human-validation artefact is absent"))
        return out
    banks = (("A", resA, exA), ("BS", resB, exB))
    bname = {"A": "Bank~A", "BS": "Bank~B"}

    # ---- design facts, read from the realised data and the configuration ----
    per_reply = {b: r["failure_slack"]["n_solver_calls"] / r["coverage"]["usable_responses"]
                 for b, r, _ in banks}
    assert len(set(per_reply.values())) == 1 and per_reply["A"].is_integer(), (
        f"reader calls per reply differ by bank or are fractional: {per_reply}")
    calls = int(per_reply["A"])
    draws = _C.OL_SOLVER_K_HINT
    readers, rem = divmod(calls, draws)
    assert rem == 0, f"{calls} calls per reply is not readers x {draws} draws"
    n_cells = (len(_C.OL_GENERATORS) * len(_C.REGIMES)
               * len(_C.OL_SOLICITATION_KEYS))
    reps = {b: r["coverage"]["usable_responses"]
            / (r["primary_model"]["n_items"] * n_cells) for b, r, _ in banks}
    assert set(reps.values()) == {_C.OL_GEN_REPLICATES}, (
        f"replies per item and cell are {reps}, not OL_GEN_REPLICATES")
    max_repairs = inspect.signature(_P.run).parameters["max_repairs"].default
    n_utt = len(_C.PLACEBO_RESPONSES)
    grid = _Res.THRESHOLD_GRID
    cut = exA["given_rule"]["cut"]
    assert cut == exB["given_rule"]["cut"] and cut in grid
    ci_level = round(100 * inspect.signature(
        _A.item_bootstrap_ci).parameters["level"].default)
    wilson_level = _level_from_z(inspect.signature(
        _A.wilson_ci).parameters["z"].default)
    boot_B = {e["intervals"]["ci_cluster_item"]["B"] for e in (exA, exB)}
    assert boot_B == {inspect.signature(_A.item_bootstrap_ci).parameters["B"].default}

    # ---- Section 3: Method ----------------------------------------------------
    out.append(("Fig 1: readers x draws per reply",
                f"({readers} models $\\times$ {draws} draws)",
                f"{calls} calls per reply = n_solver_calls / usable_responses"))
    out.append(("Fig 1: placebo utterances per item",
                f"({n_utt} utterances per item)", "config.PLACEBO_RESPONSES"))
    man_p = BANKS / "bank_b_sample_manifest.json"
    if man_p.exists():
        man = json.loads(man_p.read_text(encoding="utf-8"))
        assert man["method"].startswith("stratified by option count")
        out.append(("Bank B sample size (Method)",
                    f"sample, stratified by option count, of {man['n_sample']}",
                    "bank_b_sample_manifest.json n_sample, method"))
        out.append(("Bank B sample size (App A)",
                    f"A sample of {man['n_sample']} was drawn from",
                    "bank_b_sample_manifest.json n_sample"))
        out.append(("Bank B sample population (App A)",
                    f"the {man['n_population']} before any screening",
                    "bank_b_sample_manifest.json n_population"))
    bs_scr = _load("screen_bankBS_ol.jsonl")
    if ol is not None and bs_scr:
        ks = {r["k_per_solver"] for r in ol + bs_scr}
        assert len(ks) == 1, f"screens ran at different k: {ks}"
        k = ks.pop()
        out.append(("screen calls per reader model (Method)",
                    f"every item {k} times with the stem masked",
                    "k_per_solver on every screen row, both banks"))
    m = _C.HEADROOM_MARGIN
    out.append(("retention rule (Method)",
                f"chance plus {m:.2f} ({1 / 3 + m:.3f} for three options)",
                "config.HEADROOM_MARGIN; 1/3 + margin"))
    target = _code_size_bank_b_target()
    out.append(("retained-item target (Method)",
                f"the protocol's target of {target}" if target else None,
                "size_bank_b.py --target default"))
    fz = {b: json.loads((BANKS / f).read_text(encoding="utf-8"))
          for b, f in (("A", "bank_a_screened_ol.json"),
                       ("BS", "bank_bs_screened_ol.json"))
          if (BANKS / f).exists()}
    n41 = pool["families"]["R4_robustness"]["n_items"]
    if len(fz) == 2:
        assert n41 == fz["A"]["n_retained_mcq"] + fz["BS"]["n_retained_mcq"]
    out.append(("analysed items (Method)",
                f"The analysed set is therefore {n41} items", "R4 n_items"))
    out.append(("analysed items (Limitations)",
                f"The {n41} items come from two banks", "R4 n_items"))
    out.append(("analysed items (Table 3b)",
                f"(b) Both banks pooled ({n41} items)", "R4 n_items"))
    out.append(("tutor temperature",
                f"sampled at temperature~{_C.GEN_TEMPERATURE:g};",
                "config.GEN_TEMPERATURE"))
    out.append(("request styles",
                f"one of {_spell(len(_C.OL_SOLICITATION_KEYS))} request styles",
                "config.OL_SOLICITATION_KEYS"))
    out.append(("conditions",
                f"{_spell(len(_C.REGIMES)).capitalize()} cumulative conditions "
                f"were applied", "config.REGIMES"))
    out.append(("repair budget (Method)",
                f"for rewriting, at most {_times(max_repairs)}",
                "pipelines.run(max_repairs)"))
    out.append(("repair budget (App C)",
                f"repair a flagged reply up to {_times(max_repairs)}",
                "pipelines.run(max_repairs)"))
    out.append(("repair budget (App D)",
                f"rewrite it as a hint, up to {_times(max_repairs)}",
                "pipelines.run(max_repairs)"))
    uA, uB = (r["coverage"]["usable_responses"] for _, r, _ in banks)
    n_obs = pool["families"]["R4_robustness"]["n_obs"]
    assert n_obs == uA + uB
    out.append(("replies per item and the reply counts (Method)",
                f"{_spell(_C.OL_GEN_REPLICATES)} replies per item: "
                f"${_tex_int(n_obs)}$ replies, ${_tex_int(uA)}$ on Bank~A and "
                f"${_tex_int(uB)}$ on Bank~B.",
                "R4 n_obs; coverage.usable_responses per bank"))
    out.append(("reader temperature and draws",
                f"{_spell(draws)} draws each at temperature~"
                f"{_C.SOLVER_TEMPERATURE:g};", "OL_SOLVER_K_HINT; SOLVER_TEMPERATURE"))
    out.append(("calls per reply (Method)",
                f"the hinted rate is the share of these {calls}",
                "n_solver_calls / usable_responses"))
    out.append(("calls per reply (validity)",
                f"where the measure puts it to {calls}", "same quantity"))
    out.append(("calls per reply (Table 4 caption)",
                f"share of {calls} reader calls", "same quantity"))
    out.append(("placebo utterances (Method)",
                f"{_spell(n_utt)} content-free utterances per item",
                "config.PLACEBO_RESPONSES"))
    out.append(("placebo utterances (App B)",
                f"the {_spell(n_utt)} content-free utterances were scored on all",
                "config.PLACEBO_RESPONSES"))
    plc_bs = _load("placebo_bankBS_ol.jsonl")
    if plc_bs:
        assert all(len(r["per_placebo"]) == n_utt for r in plc_bs)
        plc_bs_mean = st.mean(r["placebo_hinted"] for r in plc_bs)
        out.append(("Bank B placebo floor (Method)",
                    f"Bank~A but {plc_bs_mean:.3f} on Bank~B",
                    "mean placebo_hinted over placebo_bankBS_ol.jsonl items"))
    out.append(("descriptive cut and grid (Method)",
                f"{cut:.2f}, one cut on a grid from {min(grid):.2f} to "
                f"{max(grid):.2f}", "results.THRESHOLD_GRID; given_rule.cut"))
    # P0 highest and P2 lowest at every cut on both banks: a statement, pinned
    # by its phrase only while it holds.
    order_holds = True
    for _, _, e in banks:
        for c in e["given_by_grid"]["all_items"].values():
            rates = {reg: v["rate"] for reg, v in c.items()}
            others = [v for reg, v in rates.items() if reg != "P0"]
            if not (rates["P0"] > max(others)
                    and rates["P2"] < min(v for reg, v in rates.items() if reg != "P2")):
                order_holds = False
    assert len(exA["given_by_grid"]["all_items"]) == len(grid)
    out.append(("P0 highest, P2 lowest at every cut (Method)",
                "on both banks, the share of replies giving the key away is highest "
                "with no safeguard and lowest under the filter" if order_holds
                else "(P0 highest / P2 lowest fails at some cut)",
                "given_by_grid.all_items, every cut, both banks"))
    out.append(("P0 highest, P2 lowest at every cut (App C)",
                "At every cut on both banks P0 is highest and P2 lowest"
                if order_holds else "(P0 highest / P2 lowest fails at some cut)",
                "given_by_grid.all_items, every cut, both banks"))
    vlen = _code_threshold_checker()
    out.append(("verbatim-option length (Method)",
                f"verbatim option text of at least {vlen} characters" if vlen else None,
                "checker.check_explicit option_verbatim rule"))
    out.append(("verbatim-option length (App D)",
                f"it is at least {vlen} characters long" if vlen else None,
                "checker.check_explicit option_verbatim rule"))
    fA, fB = (e["detector"] for e in (exA, exB))
    out.append(("items with answer forms, Bank A (Method)",
                f"for {fA['retained_items_with_answer_forms']} of "
                f"{fA['n_retained_items']} Bank~A items and", "detector"))
    out.append(("items with answer forms, Bank B (Method)",
                f"{fB['retained_items_with_answer_forms']} of "
                f"{fB['n_retained_items']} Bank~B items, so comparisons", "detector"))
    hvs_p = ROOT / "data" / "human_validation_sample.json"
    if hvs_p.exists():
        hvs = json.loads(hvs_p.read_text(encoding="utf-8"))
        assert hvs["n"] == hvs["per_cell"] * n_cells
        out.append(("human sample per cell (Method)",
                    f"{_spell(hvs['per_cell'])} from each of the {n_cells} cells",
                    "human_validation_sample.json per_cell"))
    npan = {r["panel_agreement_by_panel"]["n_panels"] for _, r, _ in banks}
    assert len(npan) == 1
    out.append(("rater panels",
                f"so the design has {_spell(npan.pop())} rater panels",
                "panel_agreement_by_panel.n_panels, both banks"))
    pans = {b: r["panel_agreement_by_panel"]["panels"] for b, r, _ in banks}
    best_a = max(v["icc_absolute_agreement_2_1"] for v in pans["A"].values())
    best_b = max(v["icc_absolute_agreement_2_1"] for v in pans["BS"].values())
    out.append(("no Bank B panel reaches Bank A's best",
                "no Bank~B panel reaches Bank~A's" if best_b < best_a
                else "(a Bank B panel reaches Bank A's best ICC)",
                f"max ICC A {best_a}, B {best_b}"))
    # Table 2 (validity): the human cut rows, then both banks' reader rows.
    out.append(("Table 2 caption: interval level",
                f"Intervals are {wilson_level}\\%, unadjusted for item clustering",
                "analyse.wilson_ci z"))
    for t in ("0.1", "0.25", "0.5"):
        d = hv["by_threshold"][t]
        # P4: the reported cut's row is marked (the given_rule cut, not a typed 0.25)
        mark = " (reported cut)" if float(t) == cut else ""
        out.append((f"Table 2 row: cut {t}",
                    f"{float(t):.2f}{mark} & ${d['sensitivity']:.3f}$ & "
                    f"${d['specificity']:.3f}$ \\\\", "by_threshold"))
    # P4 (Y2): Section 3.5 pairs the reported cut with its two values, the same
    # quantities as that cut's Table 2 row.
    d = hv["by_threshold"][f"{cut:g}"]
    out.append(("Method: sensitivity and specificity at the reported cut",
                f"sensitivity is high and specificity low ({d['sensitivity']:.3f} and "
                f"{d['specificity']:.3f} at the {cut:.2f} cut"
                if d["sensitivity"] > d["specificity"] else _FALSE,
                "human_validation_results by_threshold[given_rule.cut]"))
    for key, va in pans["A"].items():
        vb = pans["BS"][key]
        a, b = (_rname(x) for x in key.split("+"))
        out.append((f"Table 2 row: ICC {a}+{b}",
                    f"ICC, \\texttt{{{a}}} + \\texttt{{{b}}} & "
                    f"${va['icc_absolute_agreement_2_1']:.3f}$ "
                    f"$[{va['ci_low']:.3f}, {va['ci_high']:.3f}]$ & "
                    f"${vb['icc_absolute_agreement_2_1']:.3f}$ "
                    f"$[{vb['ci_low']:.3f}, {vb['ci_high']:.3f}]$ \\\\",
                    "panel_agreement_by_panel, both banks"))
    lv = {b: r["per_solver_levels"] for b, r, _ in banks}
    for sv, va in lv["A"]["per_solver"].items():
        vb = lv["BS"]["per_solver"][sv]
        out.append((f"Table 2 row: level {_rname(sv)}",
                    f"Level, \\texttt{{{_rname(sv)}}} ($n = $ ${_tex_int(va['n'])}$) & "
                    f"${va['mean']:.3f}$ $[{va['ci_low']:.3f}, {va['ci_high']:.3f}]$ & "
                    f"${vb['mean']:.3f}$ $[{vb['ci_low']:.3f}, {vb['ci_high']:.3f}]$ \\\\",
                    "per_solver_levels, both banks"))
    out.append(("Table 2 row: spread of levels",
                f"Spread of the {_spell(lv['A']['n_solvers'])} levels & "
                f"${lv['A']['spread']:.3f}$ & ${lv['BS']['spread']:.3f}$ \\\\",
                "per_solver_levels.spread, both banks"))
    out.append(("bootstrap draws (Analysis)",
                f"percentile bootstraps (${_tex_int(min(boot_B))}$",
                "intervals.ci_cluster_item.B"))
    out.append(("bootstrap draws (App B)",
                f"(${_tex_int(min(boot_B))}$ draws, fixed seed)",
                "intervals.ci_cluster_item.B"))

    D = dict(resA=resA, resB=resB, pool=pool, exA=exA, exB=exB, exP=exP, hv=hv,
             banks=banks, bname=bname, calls=calls, draws=draws, readers=readers,
             n_cells=n_cells, max_repairs=max_repairs, n_utt=n_utt, grid=grid,
             cut=cut, ci_level=ci_level, wilson_level=wilson_level, n41=n41,
             target=target, fz=fz, ol=ol, bs_scr=bs_scr, plc_bs=plc_bs)
    out += _ce_results(D)
    out += _ce_table3(D)
    out += _ce_table4(D)
    out += _ce_app_a(D)
    out += _ce_app_b(D)
    out += _ce_app_c(D)
    out += _ce_app_d(D)
    return out


def _floor_at(x: float, nd: int) -> str:
    """The largest `nd`-decimal number <= x: an 'at least X' claim is true
    only when X <= the exact minimum (the counterpart of `_ceil_at`)."""
    import math
    v = math.floor(x * 10 ** nd + 1e-9) / 10 ** nd
    return f"{v:.{nd}f}"


def _ce_app_c(D) -> list[tuple[str, str | None, str]]:
    """Appendix C: channels, ceiling cells, influence, failed calls, the
    generator ceiling, the cut, option strata."""
    out: list[tuple[str, str | None, str]] = []
    import analyse as _A
    resA, resB, pool, exA, exB = (D[k] for k in ("resA", "resB", "pool", "exA", "exB"))
    pfA = resA["descriptives"]["parsed_fraction"]["regime"]
    pfB = resB["descriptives"]["parsed_fraction"]["regime"]
    caB = resB["descriptives"]["hinted_cond_acc"]["regime"]
    mit = ("P1", "P2", "P3")
    out.append(("App C: Bank A willingness, P0",
                f"hinted calls is {pfA['P0']['mean']:.3f}", "parsed_fraction P0, A"))
    out.append(("App C: Bank A willingness, P1-P3",
                f"between {min(pfA[r]['mean'] for r in mit):.3f} and "
                f"{max(pfA[r]['mean'] for r in mit):.3f} under P1 to P3",
                "parsed_fraction P1-P3, A"))
    out.append(("App C: cond. acc interval level",
                f"({_level_from_z(inspect_default(_A.mean_ci, 'z'))}\\% intervals, unadjusted",
                "analyse.mean_ci z"))
    out.append(("App C: Bank B declines",
                f"on {_pct(resB['decline_breakdown']['decline_fraction'])} of hinted calls",
                "decline_breakdown.decline_fraction, B"))
    out.append(("App C: Bank B willingness",
                f"from {pfB['P0']['mean']:.3f} under P0 to between", "parsed_fraction P0, B"))
    lo_c = min(caB[r]["mean"] for r in mit)
    hi_c = max(caB[r]["mean"] for r in mit)
    out.append(("App C: Bank B willingness P1-P3; cond. acc",
                f"{min(pfB[r]['mean'] for r in mit):.3f} and {max(pfB[r]['mean'] for r in mit):.3f}, "
                f"while conditional accuracy falls from {caB['P0']['mean']:.3f} to between "
                f"{lo_c:.3f}", "parsed_fraction, hinted_cond_acc, B"))
    out.append(("App C: Bank B cond. acc max", f"and {hi_c:.3f}. On both banks",
                "hinted_cond_acc, B"))
    csA, csB = resA["ceiling_sensitivity"], resB["ceiling_sensitivity"]
    lvA = csA["within_solver_adjusted_level"]
    out.append(("App C: ceiling shift, Bank A",
                f"from ${lvA['all']['mean']:.3f}$ to ${lvA['excluding_ceiling']['mean']:.3f}$, "
                f"a shift of about {lvA['shift']:.2f}",
                "ceiling_sensitivity.within_solver_adjusted_level, A"))
    out.append(("App C: ceiling cells, Bank B",
                f"On Bank~B, {csB['n_ceiling_cells']} of {csB['n_cells']} cells sit at",
                "ceiling_sensitivity, B"))
    wl, al = csB["within_solver_leak"], csB["within_solver_adjusted_level"]
    out.append(("App C: within-reader gain, Bank B",
                f"{wl['all']['mean']:.3f} to {wl['excluding_ceiling']['mean']:.3f} and leaves "
                f"the adjusted level essentially unchanged ({al['all']['mean']:.3f}",
                "ceiling_sensitivity, B"))
    out.append(("App C: adjusted level excl. ceiling, Bank B",
                f"against {al['excluding_ceiling']['mean']:.3f}).", "ceiling_sensitivity, B"))
    iiB = resB["item_influence"]["families"]
    keep = [iiB["R2_mitigation"][k] for k in ("P1-P0", "P3-P0")]
    stable = all(v["sign_stable"] and not v["verdict_flipping_items"] for v in keep)
    out.append(("App C: Bank B P0 contrasts retain",
                f"retain at least {_floor_pct_at_ten(min(v['retained_fraction_of_effect'] for v in keep))}"
                f"\\% of their magnitude" if stable else _FALSE,
                "item_influence B: P1-P0, P3-P0 retained_fraction (floor to tens)"))
    f21 = iiB["R2_mitigation"]["P2-P1"]["verdict_flipping_items"]
    f32 = iiB["R2_mitigation"]["P3-P2"]["verdict_flipping_items"]
    no_rej = not any(resB["families"]["R2_mitigation"]["contrasts"][k]["reject"]
                     for k in ("P2-P1", "P3-P2"))
    out.append(("App C: Bank B P2-P1 flips",
                f"Removing one of {len(f21)} items would" if no_rej else _FALSE,
                "item_influence B P2-P1 verdict_flipping_items"))
    out.append(("App C: Bank B P3-P2 flips",
                f"removing {len(f32)} item would do the same" if len(f32) == 1
                else f"removing {len(f32)} items would do the same",
                "item_influence B P3-P2 verdict_flipping_items"))
    cB = resB["families"]["R3_solicitation"]["contrasts"]["S3-S2"]
    out.append(("App C: Bank B S3-S2",
                f"S3$-$S2 (${cB['estimate']:.3f}$, Holm-adjusted $p = {cB['p_adjusted']:.3f}$)",
                "R3 S3-S2, B"))
    out.append(("App C: Bank B S3-S2 flips",
                f"would reject after deleting any one of "
                f"{len(iiB['R3_solicitation']['S3-S2']['verdict_flipping_items'])} items.",
                "item_influence B S3-S2"))
    iiP = pool["item_influence"]["families"]
    pr2 = pool["families"]["R2_mitigation"]["contrasts"]
    same = (set(iiP["R2_mitigation"]["P2-P1"]["verdict_flipping_items"])
            == set(iiP["R2_mitigation"]["P3-P2"]["verdict_flipping_items"]))
    out.append(("App C: pooled P2-P1 and P3-P2 reject",
                "Pooled over both banks, P2$-$P1 and P3$-$P2 reject, and deleting either of the"
                if pr2["P2-P1"]["reject"] and pr2["P3-P2"]["reject"] and same else _FALSE,
                "pooled R2 verdicts; the same flipping items"))
    pf = iiP["R2_mitigation"]["P2-P1"]["verdict_flipping_items"]
    out.append(("App C: the same pooled items, one per bank",
                f"same {len(pf)} items, one from each bank"
                if sorted(i.split("-")[0] for i in pf) == ["A", "B"] else _FALSE,
                "pooled verdict_flipping_items"))
    out.append(("App C: pooled S3-S2 flips",
                f"Pooled S3$-$S2 also rejects, and deleting any one of "
                f"{len(iiP['R3_solicitation']['S3-S2']['verdict_flipping_items'])} items"
                if pool["families"]["R3_solicitation"]["contrasts"]["S3-S2"]["reject"] else _FALSE,
                "pooled item_influence S3-S2"))
    # failed calls: exact bounds, stated as 'no more than' -> ceiling
    def slack_max(res):
        fs = res["failure_slack"]["families"].values()
        return max(max(max(f["level_max_upward_shift"].values()),
                       max(f["contrast_max_abs_shift"].values())) for f in fs)
    fsA, fsB, fsP = resA["failure_slack"], resB["failure_slack"], pool["failure_slack"]
    ekA, ekB = resA["integrity"]["error_kinds"], resB["integrity"]["error_kinds"]
    out.append(("App C: Bank A failed calls",
                f"{fsA['n_solver_calls']:,} hinted reader-model calls failed, "
                + ("both" if fsA["n_failed_calls"] == 2 else "all")
                + " on provider quota" if set(ekA) == {"quota_exhausted"} else _FALSE,
                "failure_slack, integrity.error_kinds, A"))
    out.append(("App C: Bank A failure bound",
                f"mean or contrast moves by more than {_ceil_at(slack_max(resA), 5)}.",
                f"failure_slack max shift, A = {slack_max(resA)}; a 'no more than' bound "
                f"is rendered by ceiling"))
    out.append(("App C: Bank B failed calls",
                f"On Bank~B, {fsB['n_failed_calls']} of ${_tex_int(fsB['n_solver_calls'])}$ failed",
                "failure_slack, B"))
    out.append(("App C: Bank B failure kinds",
                f"({ekB.get('quota_exhausted', 0)} on quota, {ekB.get('auth', 0)} on authentication)"
                if set(ekB) <= {"quota_exhausted", "auth"} else _FALSE,
                "integrity.error_kinds, B"))
    out.append(("App C: Bank B failure bound",
                f"and the bound is {_ceil_at(slack_max(resB), 4)}.",
                f"failure_slack max shift, B = {slack_max(resB)}; a bound is rendered by ceiling"))
    out.append(("App C: pooled failed calls", f"Over both banks, {fsP['n_failed_calls']}",
                "pooled failure_slack.n_failed_calls"))
    out.append(("App C: pooled calls failed",
                f"of {fsP['n_solver_calls']:,} calls failed", "pooled failure_slack"))
    order = all(10 * slack_max(res) <= min(abs(c["estimate"]) for c in
                res["families"]["R2_mitigation"]["contrasts"].values())
                for res in (resA, resB))
    out.append(("App C: pooled failure bound; order of magnitude",
                f"{_ceil_at(slack_max(pool), 4)}. Every bound is an order of magnitude or more "
                f"below every mitigation" if order else _FALSE,
                "pooled failure_slack max (ceiling); 10 x bound <= min |R2| per bank"))
    # the generator ceiling: the regenerated replies, and Bank B
    import config as _C
    cap = _C.GENERATOR_BUDGET_BEFORE_MEASUREMENT["value"]
    cg = [r for r in (_load("capcheck_generate_bankA_ol.jsonl") or [])
          if r.get("ok") and (r.get("response") or "").strip()]
    if cg:
        def maxtok(r):
            return max([c.get("completion_tokens") or 0
                        for c in (r.get("calls") or []) if c.get("ok")] or [0])
        within = sum(1 for r in cg if maxtok(r) <= cap)
        out.append(("App C: regenerated replies within the old ceiling",
                    f"and {within} of the {len(cg)} finished within {cap} tokens.",
                    "capcheck_generate_bankA_ol.jsonl usable rows, max ok-call tokens"))
    gB = _load("generate_bankBS_ol.jsonl") or []
    usable = [r for r in gB if r.get("ok") and (r.get("response") or "").strip()]
    if usable:
        def maxtok(r):
            return max([c.get("completion_tokens") or 0
                        for c in (r.get("calls") or []) if c.get("ok")] or [0])
        w = sum(1 for r in usable if "generator_max_tokens" in r)
        wo = [r for r in usable if "generator_max_tokens" not in r]
        out.append(("App C: Bank B replies under measured ceilings",
                    f"under the measured ceilings ({w:,}) or", "generate_bankBS_ol.jsonl"))
        out.append(("App C: Bank B replies kept from the 400-token run",
                    f"kept from a {cap}-token run because they finished below it ({len(wo):,})"
                    if all(maxtok(r) < cap for r in wo) else _FALSE,
                    "generate_bankBS_ol.jsonl rows without generator_max_tokens"))
        qrows = _load("QUARANTINE_generate_bankBS_ol_generator_cap400_PARTIAL_clean_rows_"
                      "kept_2026-08-20_a61f4004.jsonl") or []
        if qrows:
            q_use = [r for r in qrows if r.get("ok") and (r.get("response") or "").strip()]
            cut_cells = ({r["cell_key"] for r in q_use if maxtok(r) >= cap}
                         | {r["cell_key"] for r in qrows if not r.get("ok")
                            and r.get("error_kind") == "truncated_before_content"})
            live = {}
            for r in usable:
                live.setdefault(r["cell_key"], []).append(r)
            long_now = sum(1 for k in cut_cells if any(maxtok(r) > cap for r in live.get(k, [])))
            fewer = len(cut_cells) - long_now
            out.append(("App C: fewer long Bank B replies",
                        f"leaves {fewer} fewer long replies than were first",
                        "cut-off cells (quarantine ok call at the cap, or truncated before "
                        "content) minus those whose live reply ran past the cap"))
            out.append(("App C: fewer long replies as a share",
                        f"drawn ({_pct(fewer / len(usable))} of the {len(usable):,})",
                        "fewer / usable Bank B replies"))
    # where the cut is placed
    grid, cut = D["grid"], D["cut"]
    out.append(("App C: the descriptive cut", f"The cut of {cut:.2f} used to say",
                "given_rule.cut"))
    gA, gB2 = exA["given_by_grid"]["all_items"], exB["given_by_grid"]["all_items"]
    gap = max(abs(c["P1"]["rate"] - c["P3"]["rate"]) * 100
              for g in (gA, gB2) for c in g.values())
    out.append(("App C: P1 and P3 within N percentage points",
                f"within {_ceil_at(gap, 1)} percentage points of each other",
                "max over cuts and banks |P1 - P3|, ceiling at one decimal"))
    top = f"{max(grid):.2f}"
    minA = min(c[r]["rate"] for c in gA.values() for r in mit) * 100
    out.append(("App C: Bank A at least, every cut",
                f"On Bank~A at least {_floor_at(minA, 1)}\\% of",
                "min over cuts and P1-P3 of the given share, A (floor at one decimal)"))
    k40 = sorted(gB2, key=float)[-2]               # the second-highest cut
    cut40 = [k for k in gB2 if float(k) <= float(k40)]
    minB = min(gB2[k][r]["rate"] for k in cut40 for r in mit) * 100
    out.append(("App C: Bank B at least, cuts up to 0.40",
                f"mitigated replies reach every cut up to {top}. On Bank~B at least "
                f"{_floor_at(minB, 1)}\\% reach", "min over cuts up to the second-highest, B (floor)"))
    rb = [gB2[top][r]["rate"] for r in mit]
    out.append(("App C: Bank B at the top cut",
                f"every cut up to {float(k40):.2f}, and {100 * min(rb):.1f}\\% to "
                f"{100 * max(rb):.1f}\\% reach {top}.", "given_by_grid[max], B"))
    out.append(("App C: Table caption counts",
                f"Bank~A: {gA[top]['P0']['n']} replies per condition; Bank~B: "
                f"{gB2[top]['P0']['n']}.", "given_by_grid n"))
    for k in gA:
        cells = [gA[k][r]["rate"] for r in ("P0", "P1", "P2", "P3")] + \
                [gB2[k][r]["rate"] for r in ("P0", "P1", "P2", "P3")]
        out.append((f"App C table: cut {k}",
                    f"{k} & " + " & ".join(f"{100 * x:.1f}" for x in cells) + " \\\\",
                    "given_by_grid.all_items, both banks"))
    # option strata
    so = pool["bank_contrast_by_option_stratum"]["strata"]
    s3 = so["3"]
    out.append(("App C: three-option stratum",
                f"recoverability is {s3['by_bank']['A']['mean']:.3f} on Bank~A over "
                f"{s3['by_bank']['A']['n_rows']:,} replies and {s3['by_bank']['BS']['mean']:.3f} "
                f"on Bank~B over {s3['by_bank']['BS']['n_rows']:,},", "strata.3.by_bank"))
    out.append(("App C: three-option difference",
                f"a difference of ${s3['difference']['estimate']:.3f}$ (standard error "
                f"${s3['difference']['se']:.3f}$", "strata.3.difference"))
    out.append(("App C: four-option stratum",
                f"stand at {so['4']['by_bank']['BS']['mean']:.3f} over "
                f"{so['4']['by_bank']['BS']['n_rows']:,} four-option", "strata.4.by_bank.BS"))
    bsm = {k: so[k]["by_bank"]["BS"]["mean"] for k in ("3", "4", "5")}
    out.append(("App C: five-option stratum; not ordered",
                f"replies and {bsm['5']:.3f} over {so['5']['by_bank']['BS']['n_rows']:,} "
                f"five-option replies. Option count does not order"
                if max(bsm, key=bsm.get) == "5" else _FALSE, "strata.5.by_bank.BS"))
    return out


def _ce_app_d(D) -> list[tuple[str, str | None, str]]:
    """Appendix D: the answer-text filter as run."""
    out: list[tuple[str, str | None, str]] = []
    resA, resB, exA, exB = (D[k] for k in ("resA", "resB", "exA", "exB"))
    fA, fB = exA["detector"], exB["detector"]
    out.append(("App D: items with answer forms",
                f"for {fA['retained_items_with_answer_forms']} of Bank~A's "
                f"{fA['n_retained_items']} retained items but for only "
                f"{fB['retained_items_with_answer_forms']} of Bank~B's {fB['n_retained_items']}",
                "detector"))
    ex = [e["guardrail_detail"]["p3_residual_blocks_exhausted"] for e in (exA, exB)]
    exhausted = all(x["k"] == x["n_blocked"] and x["max_rejections"] == D["max_repairs"] + 1
                    for x in ex)
    out.append(("App D: residual blocks exhausted the repairs",
                f"{_spell(D['max_repairs'])} rewrites; the residual is a budget limit"
                if exhausted else _FALSE, "p3_residual_blocks_exhausted, both banks"))
    tot = [e["diagnostic_rederivation"]["total"] for e in (exA, exB)]
    out.append(("App D: verdicts re-derived",
                f"every recorded verdict: {tot[0]['compared']:,} on Bank~A and "
                f"{tot[1]['compared']:,} on Bank~B."
                if all(t["compared"] == t["agreeing"] for t in tot) else _FALSE,
                "diagnostic_rederivation.total"))
    out.append(("App D: table interval level",
                f"Brackets: {D['wilson_level']}\\% Wilson intervals", "analyse.wilson_ci z"))
    gA, gB = resA["guardrail"], resB["guardrail"]
    arA, arB = exA["as_run_guardrail"], exB["as_run_guardrail"]

    def rate(k, n, ci):
        return f"{k} ({_pct(k / n)} [{100 * ci[0]:.1f}, {100 * ci[1]:.1f}])"

    for reg in ("P2", "P3"):
        out.append((f"App D table: {reg} replies",
                    f"{reg} replies & {gA[reg]['n']} & {gB[reg]['n']} \\\\", "guardrail n"))
    out.append(("App D table: P2 withheld",
                f"\\quad withheld & {rate(gA['P2']['blocked'], gA['P2']['n'], gA['P2']['blocked_ci'])}"
                f" & {rate(gB['P2']['blocked'], gB['P2']['n'], gB['P2']['blocked_ci'])} \\\\",
                "guardrail.P2 blocked, blocked_ci"))
    out.append(("App D table: P3 flagged at least once",
                f"\\quad flagged at least once & {arA['P3']['rejected_rows']} & "
                f"{arB['P3']['rejected_rows']} \\\\", "as_run_guardrail.P3.rejected_rows"))
    out.append(("App D table: P3 rejections",
                f"\\quad rejections in total & {gA['P3']['rejections']} & "
                f"{gB['P3']['rejections']} \\\\", "guardrail.P3.rejections"))
    out.append(("App D table: P3 rewrite attempts",
                f"\\quad rewrite attempts & {gA['P3']['repairs']} & {gB['P3']['repairs']} \\\\",
                "guardrail.P3.repairs"))
    rd = []
    for g, ar in ((gA, arA), (gB, arB)):
        assert g["P3"]["repaired_ok"] == ar["P3"]["repaired_delivered"]
        rd.append(rate(g["P3"]["repaired_ok"], ar["P3"]["rejected_rows"],
                       S_wilson(g["P3"]["repaired_ok"], ar["P3"]["rejected_rows"])))
    out.append(("App D table: P3 rewritten and delivered",
                f"\\quad rewritten and delivered & {rd[0]} & {rd[1]} \\\\",
                "guardrail.P3.repaired_ok of as_run rejected_rows; Wilson"))
    out.append(("App D table: P3 withheld",
                f"\\quad withheld & {rate(gA['P3']['blocked'], gA['P3']['n'], gA['P3']['blocked_ci'])}"
                f" & {rate(gB['P3']['blocked'], gB['P3']['n'], gB['P3']['blocked_ci'])} \\\\",
                "guardrail.P3 blocked, blocked_ci"))
    wf = {b: e["by_form_coverage"] for b, e in (("A", exA), ("B", exB))}
    vB = wf["B"]["with_forms"]["P2"]["verifier_rejections_recorded"]
    vB0 = wf["B"]["without_forms"]["P2"]["verifier_rejections_recorded"]
    vA = wf["A"]["with_forms"]["P2"]["verifier_rejections_recorded"]
    out.append(("App D: Bank B P2 withheld on items with forms",
                f"On Bank~B, P2 withheld {vB['k']} of {vB['n']} replies ({_pct(vB['k'] / vB['n'])})",
                "by_form_coverage.with_forms.P2.verifier_rejections_recorded, B"))
    out.append(("App D: Bank B P2 withheld on items without forms",
                f"to items with answer forms and {vB0['k']} of {vB0['n']} "
                f"({_pct(vB0['k'] / vB0['n'])}) to items without them",
                "by_form_coverage.without_forms.P2, B"))
    out.append(("App D: Bank A P2 rate on items with forms",
                f"Bank~A the rate on items with forms was {vA['k']} of {vA['n']} "
                f"({_pct(vA['k'] / vA['n'])}).", "by_form_coverage.with_forms.P2, A"))
    fc = [e["guardrail_detail"]["first_candidate_rule"]["P2"] for e in (exA, exB)]
    out.append(("App D: caught by an answer form, Bank A",
                f"caught by an answer form: {fc[0]['by_rule']['answer_form']} of",
                "first_candidate_rule.P2, A"))
    out.append(("App D: caught by an answer form, counts",
                f"{fc[0]['n_rejected']} under P2 on Bank~A and {fc[1]['by_rule']['answer_form']} "
                f"of {fc[1]['n_rejected']} on Bank~B.", "first_candidate_rule.P2"))
    out.append(("App D: withheld on Bank A",
                f"not the {gA['P2']['blocked'] + gA['P3']['blocked']}",
                "guardrail P2 + P3 blocked, A"))
    out.append(("App D: withheld on Bank B",
                f"replies withheld on Bank~A or the {gB['P2']['blocked'] + gB['P3']['blocked']} "
                f"withheld on Bank~B", "guardrail P2 + P3 blocked, B"))
    return out


_SPEC = ROOT / "docs" / "superpowers" / "specs" / "2026-08-15-ceai-answer-leakage-design.md"


def _spec_section(num: str) -> str:
    """The text of one numbered spec section ("### 4.21 ..." up to the next
    heading of that level), or "" -- facts are read from the section the
    `%% NEW:` tag names, never from the first match anywhere in the file."""
    if not _SPEC.exists():
        return ""
    m = re.search(rf"(?ms)^### {re.escape(num)} .*?(?=^### |\Z)",
                  _SPEC.read_text(encoding="utf-8"))
    return m.group(0) if m else ""


def _spec_fact(rx: str, section: str, group: int = 1) -> str | None:
    """One protocol fact read from a spec section that the `%% NEW:` tags of
    App B cite (§4.21 sizing, §4.43 the original R3). None when the section
    or the passage is absent -- the check is then PENDING."""
    m = re.search(rx, _spec_section(section))
    return m.group(group) if m else None


def _ce_app_b(D) -> list[tuple[str, str | None, str]]:
    """Appendix B: design table, deviations, placebo floor, human labels."""
    out: list[tuple[str, str | None, str]] = []
    import analyse as _A
    import config as _C
    import results as _Res
    resA, resB, pool, hv = D["resA"], D["resB"], D["pool"], D["hv"]
    G, Rg, S = len(_C.OL_GENERATORS), len(_C.REGIMES), len(_C.OL_SOLICITATION_KEYS)
    reps = _C.OL_GEN_REPLICATES
    out.append(("Table A1 (design): cells",
                f"Cells & {D['n_cells']} ({_spell(G)} tutor models $\\times$ {_spell(Rg)} "
                f"conditions $\\times$ {_spell(S)} request styles); {_spell(reps)} replies "
                f"per item and cell", "config; replicates asserted from coverage"))
    out.append(("Table A1 (design): readers",
                f"{_spell(D['readers'])} per reply (own vendor excluded), {_spell(D['draws'])} "
                f"draws each: {D['calls']} calls per reply",
                "n_solver_calls / usable_responses; OL_SOLVER_K_HINT"))
    fz = D["fz"]
    if len(fz) == 2:
        out.append(("Table A1 (design): items",
                    f"Items & Bank~A {fz['A']['n_retained_mcq']} retained of "
                    f"{fz['A']['n_candidates_mcq']}; Bank~B {fz['BS']['n_retained_mcq']} "
                    f"retained of {fz['BS']['n_screened_mcq']} screened",
                    "frozen banks"))
    uA, uB = (r["coverage"]["usable_responses"] for r in (resA, resB))
    out.append(("Table A1 (design): replies",
                f"Replies & Bank~A ${_tex_int(uA)}$; Bank~B ${_tex_int(uB)}$; total "
                f"${_tex_int(pool['families']['R4_robustness']['n_obs'])}$",
                "usable_responses; R4 n_obs"))
    # R3 cut from five contrasts to three
    orig = _spec_fact(r"enumerated R3 as \w+ contrasts\s+over \w+ levels: `([^`]*)`", "4.43")
    m_orig = len(orig.split(",")) if orig else None
    m_now = len(_Res.FAMILIES["R3_solicitation"][1])
    assert m_now == pool["families"]["R3_solicitation"]["m"]
    a = _Res.HOLM_ALPHA
    out.append(("App B: R3 five to three",
                f"cut family R3 from {_spell(m_orig)} contrasts to {_spell(m_now)},"
                if m_orig else None, "spec §4.43 original R3; results.FAMILIES"))
    out.append(("App B: first Holm threshold",
                f"(the first becomes {a:g}/{m_now} rather than {a:g}/{m_orig})"
                if m_orig else None, "results.HOLM_ALPHA / m"))
    pS = pool["families"]["R3_solicitation"]["contrasts"]["S3-S2"]
    out.append(("App B: pooled S3-S2 unadjusted p",
                f"unadjusted $p$ of {pS['p']:.4f}, below even a {_spell(m_orig)}-contrast "
                f"family's strictest" if m_orig and pS["p"] < a / m_orig else _FALSE,
                "pooled R3 S3-S2 p < alpha / original m"))
    n_old_cells = G * Rg * len(_C.SOLICITATIONS)
    out.append(("App B: strictest threshold; 64 to 48 cells",
                f"threshold of {a / m_orig:.3f}. It also cut the design from {n_old_cells} "
                f"cells to {D['n_cells']} cells" if m_orig else None,
                "alpha / original m; config.SOLICITATIONS vs OL_SOLICITATION_KEYS"))
    # the tutor-model family: planning and achieved MDE
    mf = resA["model_factor"]
    plan = mf["planning_figures_for_comparison"]
    n_plan = next((n for n in range(2, 200)
                   if abs(_A.two_proportion_mde(n) - plan["mde_n20_alpha05"]) < 1e-12), None)
    m_plan = next((k for k in range(1, 200) if n_plan and abs(
        _A.two_proportion_mde(n_plan, alpha=a / k) - plan["mde_n20_holm15"]) < 1e-12), None)
    out.append(("App B: planning items per cell",
                f"at {n_plan} items per cell, put the" if n_plan else None,
                "n that reproduces planning mde_n20_alpha05"))
    pw = inspect_default(_A.two_proportion_mde, "power")
    p1 = inspect_default(_A.two_proportion_mde, "p1")
    out.append(("App B: MDE assumptions",
                f"({100 * pw:.0f}\\% power, base rate {p1:g})", "two_proportion_mde defaults"))
    out.append(("App B: planning MDE",
                f"at {plan['mde_n20_alpha05']:.3f} before correction and "
                f"{plan['mde_n20_holm15']:.3f} after correction over {m_plan} pairs"
                if m_plan else None, "planning_figures_for_comparison"))
    none_det = abs(mf["mde_achieved_holm"] - (1 - p1)) < 1e-9
    out.append(("App B: achieved MDE",
                f"and {mf['n_pairs']} pairs, the difference is {mf['mde_achieved_alpha05']:.3f} "
                f"before correction, and after it no" if none_det else _FALSE,
                "model_factor; mde_achieved_holm at the search ceiling 1 - p1"))
    n_fam = sum(1 for f in pool["families"].values() if f.get("status") != "vacated_before_data")
    out.append(("App B: largest tutor-model difference",
                f"difference between tutor models is {mf['largest_observed_difference']:.3f}. "
                f"Holm therefore runs over {_spell(n_fam)}", "model_factor; pooled families"))
    # the covariate
    def flips(res):
        prim, cov = res["families"], res["covariate_sensitivity"]["families"]
        return {(f, k) for f in ("R2_mitigation", "R3_solicitation")
                for k, v in prim[f]["contrasts"].items()
                if v["reject"] != cov[f]["contrasts"][k]["reject"]}
    fA, fB, fP = flips(resA), flips(resB), flips(pool)
    r2A = resA["families"]["R2_mitigation"]["contrasts"]
    covA = resA["covariate_sensitivity"]["families"]["R2_mitigation"]["contrasts"]
    p0 = [k for k in r2A if k.endswith("-P0")]
    out.append(("App B: adjusted P1-P0",
                f"(P1$-$P0 moves from ${r2A['P1-P0']['estimate']:.3f}$ to "
                f"${covA['P1-P0']['estimate']:.3f}$)", "covariate_sensitivity R2 P1-P0"))
    se_p = {f"{r2A[k]['se']:.3f}" for k in p0}
    se_c = {f"{covA[k]['se']:.3f}" for k in p0}
    out.append(("App B: SE of the contrasts involving P0",
                f"the {_spell(len(p0))} contrasts involving P0 from ${se_p.pop()}$ to"
                if len(se_p) == 1 and len(se_c) == 1 else _FALSE, "R2 se, both fits"))
    all_lost = fA == {("R2_mitigation", k) for k in r2A} and all(v["reject"] for v in r2A.values())
    out.append(("App B: Bank A loses every R2 rejection",
                f"${se_c.pop()}$; they lose their rejections" if all_lost and se_c else _FALSE,
                "Bank A: 4 of 4 R2 rejections -> 0 of 4 with the covariate"))
    stated = (not fB and fP == {("R3_solicitation", "S3-S2")}
              and resB["covariate_decision"]["excluded"] == ["prompt_tokens"]
              and pool["covariate_decision"]["excluded"] == ["prompt_tokens"])
    out.append(("App B: Bank B unchanged; pooled S3-S2 loses",
                "excludes the covariate on Bank~B, where no verdict changes, and in the pooled"
                if stated else _FALSE, "reject flags, both fits, Bank B and pooled"))
    out.append(("Table 3 note: covariate verdicts",
                "Bank~A safeguard rejection and the pooled demand-versus-help one"
                if stated and all_lost else _FALSE,
                "the only verdicts the covariate changes: Bank A R2 (all) and pooled S3-S2"))
    cS = pool["covariate_sensitivity"]["families"]["R3_solicitation"]["contrasts"]["S3-S2"]
    out.append(("App B: pooled S3-S2 with and without the covariate",
                f"(Holm $p$ {pS['p_adjusted']:.3f} without the covariate, "
                f"{cS['p_adjusted']:.3f} with it)", "pooled R3 S3-S2, both fits"))
    r4m = pool["families"]["R4_robustness"]["m"]
    out.append(("App B: R4 sized as two", f"sized family R4 as {_spell(r4m)} tests",
                "R4_robustness.m"))
    out.append(("App B: R4 keeps its size", f"keeps its size of {_spell(r4m)},",
                "R4_robustness.m"))
    am = (_data("examples_bankA.json") or {}).get("rule_amendment") or {}
    out.append(("App B: one amendment, all categories",
                f"one amendment, applied to all {_spell(len(am['candidates_before']))} categories"
                if am else None, "examples_bankA.json rule_amendment"))
    # A-L09-01
    scr = {r["item_id"]: r for r in (_load("screen_bankA_ol.jsonl") or [])}
    plc = {r["item_id"]: r for r in (_load("placebo_bankA_ol.jsonl") or [])}
    g = "ol/gemma4:cloud"
    if "A-L09-01" in scr and "A-L09-01" in plc:
        b0, p9 = scr["A-L09-01"]["per_solver"][g], plc["A-L09-01"]["per_solver"][g]
        out.append(("App B: A-L09-01 gemma4 masked",
                    f"\\texttt{{gemma4}} picks the key on none of its {b0['calls']} masked"
                    if b0["hits"] == 0 else _FALSE, "screen A-L09-01 gemma4"))
        out.append(("App B: A-L09-01 gemma4 placebo",
                    f"on ${p9['hits']}$ of {p9['calls']} calls that carry",
                    "placebo A-L09-01 gemma4"))
        out.append(("App B: A-L09-01 placebo rate (anchored)",
                    f"and the placebo ${plc['A-L09-01']['placebo_hinted']:.3f}$.",
                    "placebo_bankA_ol.jsonl A-L09-01 placebo_hinted; twin of P3's 0.583"))
    if plc:
        out.append(("App B: Bank A placebo items",
                    f"were scored on all {len(plc)} retained items",
                    "placebo_bankA_ol.jsonl rows"))
    pb = D["plc_bs"]
    if pb:
        c = sum(v["calls"] for r in pb for v in r["per_solver"].values())
        f = sum(v["failed"] for r in pb for v in r["per_solver"].values())
        pr = sum(v["parsed"] for r in pb for v in r["per_solver"].values())
        out.append(("App B: Bank B placebo calls",
                    f"carried over: ${c}$", "placebo_bankBS_ol.jsonl calls"))
        out.append(("App B: Bank B placebo items and failures",
                    f"reader calls over its {len(pb)} retained items, {_spell(f)} failed",
                    "placebo_bankBS_ol.jsonl"))
        out.append(("App B: Bank B placebo parse and floor",
                    f"${_pct(pr / c)}$, and a pooled placebo rate of "
                    f"${st.mean(r['placebo_hinted'] for r in pb):.3f}$",
                    "placebo_bankBS_ol.jsonl parsed/calls; mean placebo_hinted"))
    # sizing of the human sample (spec §4.21)
    sec = _spec_section("4.21")
    p_size = _spec_fact(r"Wilson half-width at p = ([0-9.]+)", "4.21")
    share = _spec_fact(r"\| \*\*(\d+)%\*\* \|", "4.21")
    shares = [int(x) for x in re.findall(r"(?m)^\| \**(\d+)%\**? \|", sec)]
    target_hw = _spec_fact(r"conventional ±([0-9.]+) target", "4.21")
    old = re.search(r"N = (\d+) — (\w+) responses from each of the (\d+) cells", sec)
    n, per = hv["n"], None
    hvs_p = ROOT / "data" / "human_validation_sample.json"
    if hvs_p.exists():
        per = json.loads(hvs_p.read_text(encoding="utf-8"))["per_cell"]

    def hw(nn):
        lo, hi = _A.wilson_ci(float(p_size) * nn, nn)
        return (hi - lo) / 2

    if p_size and share and shares and target_hw and old and per:
        q, low = int(share) / 100, min(shares) / 100
        wq = _about_fraction(q)
        out.append(("App B: sizing, quarter minority",
                    f"{wq.split(' ')[-1]} of the {n} responses, a {_level_from_z(inspect_default(_A.wilson_ci, 'z'))}"
                    f"\\% Wilson interval about {float(p_size):.2f} has a half-width",
                    "spec §4.21 sizing row and p; n"))
        out.append(("App B: sizing half-widths",
                    f"of $\\pm {hw(q * n):.3f}$; at a {min(shares)}\\% minority it is "
                    f"$\\pm {hw(low * n):.3f}$", "analyse.wilson_ci at p, n x share"))
        n_old, per_old, cells_old = int(old.group(1)), old.group(2), int(old.group(3))
        assert n_old == {"four": 4}.get(per_old, -1) * cells_old
        out.append(("App B: the stale frame",
                    f"{per_old} responses per cell on the {cells_old}-cell frame",
                    "spec §4.21"))
        n_alt = n_old // cells_old * D["n_cells"]
        out.append(("App B: four per cell on 48 cells",
                    f"On {D['n_cells']} cells, {per_old} per cell would have given "
                    f"$\\pm {hw(q * n_alt):.3f}$ at a" if hw(q * n_alt) > float(target_hw)
                    else _FALSE, "analyse.wilson_ci; exceeds the conventional target"))
        out.append(("App B: conventional target; five per cell",
                    f"outside the conventional $\\pm {float(target_hw):.2f}$, so "
                    f"{_spell(per)} per cell were", "spec §4.21; per_cell"))
    neg = {min(v["n_negative"], v["n_positive"]) for v in hv["by_threshold"].values()}
    if share and len(neg) == 1:
        out.append(("App B: realised minority class",
                    f"minority class is {neg.pop()} responses, not {round(int(share) / 100 * n)}.",
                    "by_threshold n_negative/n_positive; planned share x n"))
    bt = hv["by_threshold"]
    ws = [(v["sens_ci"][1] - v["sens_ci"][0]) / 2 for v in bt.values()]
    wp = [(v["spec_ci"][1] - v["spec_ci"][0]) / 2 for v in bt.values()]
    out.append(("App B: number of cuts", f"Across the {_spell(len(bt))} cuts",
                "by_threshold keys"))
    out.append(("App B: Wilson half-widths",
                f"run from {min(ws):.3f} to {max(ws):.3f} for sensitivity and from "
                f"{min(wp):.3f} to {max(wp):.3f} for specificity", "by_threshold CIs"))
    adj = hv["adjudication_sensitivity"]
    out.append(("App B: alternative kappa (anchored)",
                f"${adj['alt_cohen_kappa']:.3f}$, and no sensitivity or specificity",
                "adjudication_sensitivity.alt_cohen_kappa"))
    out.append(("App B: adjudication moves no rate by 0.005",
                f"moves by as much as {_ceil_at(adj['max_rate_displacement'], 3)}",
                "adjudication_sensitivity.max_rate_displacement, ceiling"))
    sct = hv["structure"]
    out.append(("App B: different option",
                f"({sct['different_option']} of the {sct['n_both_committed']} replies both "
                f"committed on)", "structure"))
    pa = hv["per_annotator"]
    out.append(("App B: per-annotator cannot_tell",
                f"${pa['A1']['cannot_tell']}$ responses ({_pct(pa['A1']['cannot_tell'] / n)}) "
                f"and the other on ${pa['A2']['cannot_tell']}$ "
                f"({_pct(pa['A2']['cannot_tell'] / n)})", "per_annotator"))
    out.append(("App B: specificity at the cut", f"At the {D['cut']:.2f} cut",
                "given_rule.cut"))
    if hvs_p.exists():
        import human_validation as _HV
        J = {r["cell_key"]: r["leak_adj"] for r in _HV.join_generate_solve("A", "ol")}
        smp = json.loads(hvs_p.read_text(encoding="utf-8"))["responses"]
        ms = st.mean(J[r["cell_key"]] for r in smp)
        grand = st.mean(J.values())
        out.append(("App B: representativeness",
                    f"sampled responses is {ms:.3f}, against {grand:.3f} over all Bank~A replies",
                    "mean leak_adj over the sample vs all replies (join_generate_solve)"))
    return out


def _screen_figure_checks(ol, body: str) -> list[tuple[str, str | None, str]]:
    """App A's histogram against `make_screen_figure.py` run NOW on the
    screen file (P0.6 ruling 5): bins, ymax, threshold line and node texts.
    The tick labels are checked against the axis's own tick positions and
    the generator's threshold: every label equals its tick, and the
    threshold's tick is the one left blank."""
    out: list[tuple[str, str | None, str]] = []
    gen = subprocess.run([sys.executable, str(ROOT / "harness" / "make_screen_figure.py"),
                          "--screen", str(RUNS / "screen_bankA_ol.jsonl")],
                         capture_output=True, text=True, timeout=120)
    if gen.returncode != 0:
        return [("App A fig: regenerated histogram", None,
                 f"make_screen_figure.py failed: {gen.stderr.strip()[:120]}")]
    g = gen.stdout

    def norm(s):
        return " ".join(s.split())

    i = body.find("\\begin{figure}", max(0, body.find("\\label{app:screening-rule}")))
    j = body.find("\\end{figure}", i)
    fig = norm(body[i:j]) if i >= 0 and j >= 0 else ""
    want = [norm(ln) for ln in re.findall(
        r"\\addplot\[[^\n]*coordinates \{\n[^\n]*\};|\\draw\[[^\n]*;|\\node\[[^\n]*;", g)]
    ymax = re.search(r"ymax=(\d+)", g).group(1)
    ok = bool(fig) and all(w in fig for w in want) and f"ymax={ymax}," in fig
    first = next((ln.strip() for ln in body[i:j].split("\n")
                  if ln.strip().startswith("(0.0,")), None) if fig else None
    out.append(("App A fig: regenerated histogram matches",
                first if ok and first else norm(" ".join(want)),
                "make_screen_figure.py output (bins, ymax, threshold line, node "
                "texts) " + ("found in the figure" if ok else "NOT in the figure")))
    for rx, lab in ((r"\{(threshold \$[0-9.]+\$)\}", "threshold node"),
                    (r"\{(retained \(\d+\))\}", "retained node"),
                    (r"\{(discarded \(\d+\))\}", "discarded node")):
        m = re.search(rx, g)
        out.append((f"App A fig: {lab}", "{" + m.group(1) + "}" if m else None,
                    "make_screen_figure.py"))
    thr = float(re.search(r"threshold \$([0-9.]+)\$", g).group(1))
    t = re.search(r"xtick=\{([^}]*)\},", body[i:j]) if fig else None
    tl = re.search(r"xticklabels=\{([^}]*)\},", body[i:j]) if fig else None
    lit = None
    if t and tl:
        ticks = [float(x) for x in t.group(1).split(",")]
        labels = tl.group(1).split(",")
        good = (len(ticks) == len(labels)
                and all((lab == "") == (abs(tk - thr) < 1e-9)
                        and (lab == "" or abs(float(lab) - tk) < 1e-9)
                        for tk, lab in zip(ticks, labels))
                and any(abs(tk - thr) < 1e-9 for tk in ticks))
        if good:
            lit = tl.group(0)
    out.append(("App A fig: tick labels", lit or "xticklabels=(inconsistent)",
                "every label equals its xtick; only the generator's threshold "
                "tick is blank"))
    return out


def _ce_app_a(D) -> list[tuple[str, str | None, str]]:
    """Appendix A: banks, option order, the screen and its figure, readers."""
    out: list[tuple[str, str | None, str]] = []
    import config as _C
    ol, bs = D["ol"], D["bs_scr"]
    body = _paper_body()
    m = _C.HEADROOM_MARGIN
    # option order of the bank as first written
    v1p = BANKS / "bank_a_v1_unbalanced.json"
    if v1p.exists():
        v1 = [i for i in json.loads(v1p.read_text(encoding="utf-8"))["items"]
              if i.get("type") == "mcq"]
        pos = Counter(i["answer_index"] for i in v1)
        n_opt = {len(i["options"]) for i in v1}
        assert n_opt == {3}, n_opt
        mode, k = pos.most_common(1)[0]
        never = [p for p in range(3) if pos[p] == 0]
        out.append(("App A: key position before rebalancing",
                    f"in the {_ordinal(mode)} position on {k} of its {len(v1)} "
                    f"multiple-choice items and never in the",
                    "bank_a_v1_unbalanced.json answer_index"))
        out.append(("App A: the position never used",
                    f"{_ordinal(never[0])}, so its effective guessing rate"
                    if len(never) == 1 else _FALSE,
                    "bank_a_v1_unbalanced.json answer_index"))
    if ol is None or not bs:
        out.append(("App A: screen figures", None, "a screen is absent or incomplete"))
        return out
    k = ol[0]["k_per_solver"]
    n_sv = len(ol[0]["per_solver"])
    out.append(("App A: calls per reader model",
                f"no tutor reply: {k} calls from", "screen k_per_solver"))
    out.append(("App A: calls per item",
                f"each of {_spell(n_sv)} reader models, {k * n_sv} per item.",
                "k_per_solver x reader models"))
    out.append(("App A: retention rule",
                f"at most $1/n + {m:.2f}$ for an $n$-option item", "HEADROOM_MARGIN"))
    opts = sorted({r["n_options"] for r in bs} | {3})
    out.append(("App A: thresholds by option count",
                ", ".join(f"${1 / n + m:.3f}$" for n in opts[:-1])
                + f" and ${1 / opts[-1] + m:.3f}$ for "
                + ", ".join(_spell(n) for n in opts[:-1]) + f" and {_spell(opts[-1])} options",
                "1/n + HEADROOM_MARGIN over the option counts screened"))
    kept = [r for r in ol if r["passes_headroom"]]
    rej = [r for r in ol if not r["passes_headroom"]]
    lo, hi = S_wilson(len(kept), len(ol))
    wl = _level_from_z(inspect_default(S_wilson, "z"))
    out.append(("App A: Bank A retention",
                f"retained (${_pct(len(kept) / len(ol))}$, ${wl}\\%$ Wilson interval "
                f"over items $[{lo:.2f}, {hi:.2f}]$) and {len(rej)} discarded.",
                "screen_bankA_ol.jsonl passes_headroom; analyse.wilson_ci"))
    band, k_tot = _screen_band(ol)
    bw = _spell(round(band)) if abs(band - round(band)) < 1e-6 else f"{band:.2f}"
    out.append(("App A: the band in calls of the total",
                f"{bw.capitalize()} calls in {k_tot} is a narrow", "_screen_band"))
    out.append(("App A fig caption: threshold",
                f"one third plus ${m:.2f}$", "HEADROOM_MARGIN"))
    out.append(("App A fig caption: the empty band",
                f"${max(r['baseline'] for r in kept):.2f}$ and "
                f"${min(r['baseline'] for r in rej):.2f}$, an interval {bw} "
                f"reader-model calls wide", "screen baselines; _screen_band"))
    out += _screen_figure_checks(ol, body)
    fz, target = D["fz"], D["target"]
    if len(fz) == 2:
        n_unscr = fz["BS"]["n_unscreened_mcq"]
        man_p = BANKS / "bank_b_sample_manifest.json"
        if man_p.exists():
            assert n_unscr == json.loads(man_p.read_text(encoding="utf-8"))["n_sample"] - len(bs)
        out.append(("App A: screening stopped at",
                    f"together held {D['n41']}", "frozen banks n_retained_mcq"))
        out.append(("App A: target and unscreened",
                    f"meeting a target of {target} fixed in advance; the other "
                    f"{n_unscr} drawn", "size_bank_b --target; n_unscreened_mcq"))
    byo = {n: (sum(1 for r in bs if r["n_options"] == n and r["passes_headroom"]),
               sum(1 for r in bs if r["n_options"] == n)) for n in opts}
    out.append(("App A: Bank B retained by option count (3)",
                f"By option count, Bank~B retained {byo[3][0]} of {byo[3][1]}",
                "screen_bankBS_ol.jsonl n_options"))
    out.append(("App A: Bank B retained by option count (4, 5)",
                f"three-option, {byo[4][0]} of {byo[4][1]} four-option and "
                f"{byo[5][0]} of {byo[5][1]} five-option items.",
                "screen_bankBS_ol.jsonl n_options"))
    out.append(("App C: Bank B retained items by option count",
                f"three-option, {byo[4][0]} four-option and {byo[5][0]} five-option items",
                "screen_bankBS_ol.jsonl n_options, retained"))
    out.append(("App C: Bank B retained three-option",
                f"Bank~B's {sum(v[0] for v in byo.values())} retained items comprise "
                f"{byo[3][0]}", "screen_bankBS_ol.jsonl n_options, retained"))
    bs_kept = sum(1 for r in bs if r["passes_headroom"])
    out.append(("App A: retention compared",
                f"Bank~B retained {100 * bs_kept / len(bs):.1f}\\% of its screened items, "
                f"Bank~A {100 * len(kept) / len(ol):.1f}\\%.", "both screens"))
    out.append(("App A: multiple of chance admitted",
                f"{(1 / 3 + m) * 3:.2f} times chance for a three-option item but "
                f"{(1 / 5 + m) * 5:.2f} times for a five-option one",
                "(1/n + HEADROOM_MARGIN) x n"))
    lv = [int(r["item_id"].split("-")[1][1:]) for r in ol]
    dis = [0 if r["passes_headroom"] else 1 for r in ol]
    n_lv = len(set(lv))
    with_d = len({a for a, d in zip(lv, dis) if d})
    out.append(("App A: levels with a discard (ol)",
                f"discarded items occur at all {n_lv}." if with_d == n_lv
                else f"discarded items occur at {with_d} of {n_lv}",
                "screen_bankA_ol.jsonl item levels"))
    out.append(("App A: levels", f"follows a progression of {n_lv}",
                "screen_bankA_ol.jsonl item levels"))
    rho = _spearman([float(a) for a in lv], [float(d) for d in dis])
    t = rho * ((len(lv) - 2) / (1 - rho ** 2)) ** 0.5
    out.append(("App A: rho(level, discard) (ol)",
                f"$\\rho = {rho:.2f}$ ($t_{{{len(lv) - 2}}} = {t:.2f}$)",
                "Spearman over the 40 screened items"))
    rho_b = _spearman([float(a) for a in lv], [r["baseline"] for r in ol])
    out.append(("App A: rho(level, baseline) (ol)",
                f"$\\rho = {rho_b:.2f}$. Whether", "Spearman over the 40 screened items"))
    it = next((r for r in ol if r["item_id"] == "A-L08-04"), None)
    if it is not None:
        assert it["passes_headroom"]
        ps = sorted(it["per_solver"].items(), key=lambda kv: -kv[1]["hits"])
        (s_all, v_all), (s_mid, v_mid), (s_none, v_none) = ps
        ok = v_all["hits"] == v_all["calls"] and v_none["hits"] == 0
        out.append(("App A: A-L08-04 split, all",
                    f"\\texttt{{{_rname(s_all)}}} picked the key on all {v_all['calls']} "
                    f"of its masked calls," if ok else _FALSE, "screen A-L08-04 per_solver"))
        out.append(("App A: A-L08-04 split, some and none",
                    f"\\texttt{{{_rname(s_mid)}}} on {v_mid['hits']} and "
                    f"\\texttt{{{_rname(s_none)}}} on none" if ok else _FALSE,
                    "screen A-L08-04 per_solver"))
        rates = {v["hits"] / v["calls"] for _, v in ps}
        out.append(("App A: A-L08-04 pooled baseline",
                    f"of ${it['baseline']:.3f}$ describes no single reader model"
                    if all(abs(it["baseline"] - x) > 1e-9 for x in rates) else _FALSE,
                    "screen A-L08-04 baseline vs its readers' own rates"))
    ch = {round(r["chance"], 9) for r in ol}
    assert len(ch) == 1
    out.append(("App A: chance in Table 5", f"chance is ${ch.pop():.3f}$", "screen chance"))
    return out


def inspect_default(fn, name: str):
    """A function's default for one parameter (a convention the code fixes)."""
    import inspect
    return inspect.signature(fn).parameters[name].default


def _screen_band(ol) -> tuple[float, int]:
    """(band in calls, calls per item) of a COMPLETE screen: the gap between
    the lowest discarded and the highest retained baseline, in calls. One
    implementation, read by `checks()` and by App A's checks."""
    kept = [r["baseline"] for r in ol if r["passes_headroom"]]
    rej = [r["baseline"] for r in ol if not r["passes_headroom"]]
    k_tot = ol[0].get("k_per_solver", 20) * len(ol[0].get("per_solver", {}) or [1])
    return (min(rej) - max(kept)) * k_tot, k_tot


def _paper_body() -> str:
    """The manuscript as the presence test reads it (comment-stripped), or ""
    when there is none. Read by the few checks that must LOCATE a row before
    comparing it -- the comparison is computed, and a row that cannot be
    found makes the check MISSING, never PENDING."""
    return texutil.strip_comments(PAPER.read_text(encoding="utf-8")) \
        if PAPER.exists() else ""


def _ce_results(D) -> list[tuple[str, str | None, str]]:
    """Section 4 (RQ1-RQ4) and the Discussion's figures."""
    out: list[tuple[str, str | None, str]] = []
    resA, resB, pool, exA, exB, exP, hv = (D[k] for k in (
        "resA", "resB", "pool", "exA", "exB", "exP", "hv"))
    cut, grid, banks, bname = D["cut"], D["grid"], D["banks"], D["bname"]
    ck = f"{cut:.2f}"
    out.append(("Results: interval level",
                f"Every interval in this section is a {D['ci_level']}\\% "
                f"item-resampled interval", "analyse.item_bootstrap_ci level"))
    out.append(("Results: the descriptive cut",
                f"{ck} (Section~\\ref{{sec:measure}}).", "exploratory given_rule.cut"))
    # RQ1: unmitigated shares, all items
    for b, r, e in banks:
        t = r["threshold_sensitivity"][ck]["P0"]
        g = e["given"]["P0"]
        assert (g["k"], g["n"]) == (t["over"], t["n"]), (
            f"{b}: exploratory given.P0 {g['k']}/{g['n']} disagrees with "
            f"threshold_sensitivity {t['over']}/{t['n']}")
    tA, tB = (r["threshold_sensitivity"][ck]["P0"] for _, r, _ in banks)
    out.append(("RQ1: P0 gives the key, Bank A",
                f"{tA['over']} of {tA['n']} Bank~A replies ({_pct(tA['rate'])}, "
                f"{_pct_ci(exA['given']['P0']['ci_cluster_item'])}) and",
                "threshold_sensitivity[cut].P0; given.P0.ci_cluster_item"))
    out.append(("RQ1: P0 gives the key, Bank B (count)",
                f"and {tB['over']} of {tB['n']} Bank~B", "threshold_sensitivity[cut].P0"))
    out.append(("RQ1: P0 gives the key, Bank B (share)",
                f"replies ({_pct(tB['rate'])}, "
                f"{_pct_ci(exB['given']['P0']['ci_cluster_item'])}).",
                "threshold_sensitivity[cut].P0.rate; given.P0.ci_cluster_item"))
    fA, fB = exA["detector"], exB["detector"]
    out.append(("RQ1: items with answer forms",
                f"On the items with answer forms, {fA['retained_items_with_answer_forms']} "
                f"of {fA['n_retained_items']} on Bank~A and "
                f"{fB['retained_items_with_answer_forms']} of {fB['n_retained_items']} "
                f"on Bank~B,", "detector"))
    wf = {b: e["by_form_coverage"]["with_forms"] for b, _, e in banks}
    for b, word, tail in (("A", "Bank~A replies", ""), ("BS", "Bank~B replies", ",")):
        s = wf[b]["P0"]["stated"]
        out.append((f"RQ1: P0 flagged, {b}",
                    f"{s['k']} of {s['n']} {word} ({_pct(s['rate'])}, "
                    f"{_pct_ci(s['ci_cluster_item'])}){tail}",
                    "by_form_coverage.with_forms.P0.stated"))
    gA, gB = wf["A"]["P0"]["given"], wf["BS"]["P0"]["given"]
    out.append(("RQ1: P0 given on items with forms",
                f"while {gA['k']} ({_pct(gA['rate'])}, {_pct_ci(gA['ci_cluster_item'])}) "
                f"and {gB['k']} ({_pct(gB['rate'])}, {_pct_ci(gB['ci_cluster_item'])}) "
                f"of the", "by_form_coverage.with_forms.P0.given"))
    for b, r, _ in banks:
        ms = [v["mean"] for v in r["descriptives"]["leak_adj"]["model"].values()]
        out.append((("Bank A tutor-model range" if b == "A"
                     else "Bank B tutor-model range"),
                    f"{min(ms):.3f} to {max(ms):.3f} on {bname[b]}",
                    "descriptives.leak_adj.model min/max"))
    for b, e in (("A", exA), ("B", exB)):
        out.append((f"Fig 2 title: Bank {b} items with forms",
                    f"title={{Bank {b}: {e['detector']['retained_items_with_answer_forms']} "
                    f"of {e['detector']['n_retained_items']} items}}", "detector"))
    out.append(("Fig 2 legend: the cut",
                f"Gives the key (adjusted $\\geq {ck}$)", "given_rule.cut"))
    out.append(("Fig 2/3 caption: interval level",
                f"Bars: {D['ci_level']}\\% item-resampled intervals.",
                "analyse.item_bootstrap_ci level"))
    # RQ2
    reg = {b: r["descriptives"]["leak_adj"]["regime"] for b, r, _ in banks}
    for b in ("A", "BS"):
        out.append((f"RQ2: P0 to P1 means, {b}",
                    f"from {reg[b]['P0']['mean']:.3f} to {reg[b]['P1']['mean']:.3f} "
                    f"on {bname[b]}", "descriptives.leak_adj.regime"))
    c10 = {b: r["families"]["R2_mitigation"]["contrasts"]["P1-P0"] for b, r, _ in banks}
    out.append(("RQ2: P1-P0 both banks",
                f"P1$-$P0 (${c10['A']['estimate']:.3f}$ and ${c10['BS']['estimate']:.3f}$)",
                "families.R2_mitigation P1-P0 estimate"))
    ciA = exA["contrasts_ci_cluster_item"]["P1-P0"]["ci_cluster_item"]
    ciB = exB["contrasts_ci_cluster_item"]["P1-P0"]["ci_cluster_item"]
    both_tests = all(c10[b]["reject"] and c10[b]["p_adjusted"] < 0.001 for b in c10) \
        and all(c[1] < 0 for c in (ciA, ciB))
    out.append(("RQ2: P1-P0 passes both tests",
                ("a drop that passes both tests on each bank"
                 if both_tests else _FALSE),
                "p_adjusted < 0.001 and interval below zero, both banks"))
    shareA = -c10["A"]["estimate"] / reg["A"]["P0"]["mean"]
    shareB = -c10["BS"]["estimate"] / reg["BS"]["P0"]["mean"]
    out.append(("RQ2: share removed by the instruction, Bank A (REQ-4)",
                f"By point estimate the instruction removed "
                f"{_pct(shareA)} of", "-P1-P0 estimate / P0 mean, Bank A"))
    out.append(("RQ2: share removed by the instruction, Bank B (REQ-4)",
                f"unmitigated recoverability and {_pct(shareB)} of Bank~B's",
                "-P1-P0 estimate / P0 mean, Bank B"))
    wA, wB = _about_fraction(shareA), _about_fraction(shareB)
    out.append(("abstract: REQ-4 words (about a fifth / three tenths)",
                f"by about {wA} (A) and {wB} (B)" if wA and wB else _FALSE,
                f"shares {shareA:.4f}, {shareB:.4f}; nearest fraction word within "
                f"{_ABOUT_TOL}"))
    sA, sB = wf["A"]["P1"]["stated"], wf["BS"]["P1"]["stated"]
    g1A, g1B = wf["A"]["P1"]["given"], wf["BS"]["P1"]["given"]
    out.append(("RQ2: P1 flagged, Bank A",
                f"flagged {sA['k']} of {sA['n']} instructed Bank~A replies "
                f"({_pct(sA['rate'])}, {_pct_ci(sA['ci_cluster_item'])}) and {sB['k']} of",
                "by_form_coverage.with_forms.P1.stated"))
    out.append(("RQ2: P1 flagged B, given A",
                f"{sB['n']} Bank~B replies ({_pct(sB['rate'])}, {_pct_ci(sB['ci_cluster_item'])}),"
                f" while {g1A['k']} ({_pct(g1A['rate'])}, {_pct_ci(g1A['ci_cluster_item'])})",
                "with_forms.P1.stated (B), .given (A)"))
    apart = all(wf[b]["P1"]["stated"]["ci_cluster_item"][1]
                < wf[b]["P1"]["given"]["ci_cluster_item"][0] for b in wf)
    out.append(("RQ2: P1 given B; intervals do not overlap",
                f"and {g1B['k']} ({_pct(g1B['rate'])}, {_pct_ci(g1B['ci_cluster_item'])}) "
                f"of them gave the key away; on neither bank do"
                if apart else "(flagged and given intervals overlap on a bank)",
                "with_forms.P1.given (B); stated hi < given lo on both banks"))
    gr = {b: r["guardrail"] for b, r, _ in banks}
    out.append(("RQ2: P2 withheld, Bank A",
                f"{gr['A']['P2']['blocked']} of {gr['A']['P2']['n']} turns on Bank~A "
                f"({_pct(gr['A']['P2']['blocked_rate'])})", "guardrail.P2"))
    out.append(("RQ2: P2 withheld, Bank B",
                f"and {gr['BS']['P2']['blocked']} of {gr['BS']['P2']['n']} on Bank~B "
                f"({_pct(gr['BS']['P2']['blocked_rate'])})", "guardrail.P2"))
    c21 = {b: r["families"]["R2_mitigation"]["contrasts"]["P2-P1"] for b, r, _ in banks}
    p21 = pool["families"]["R2_mitigation"]["contrasts"]["P2-P1"]
    out.append(("RQ2: P2-P1 per bank",
                f"P2$-$P1 was ${c21['A']['estimate']:.3f}$ on Bank~A, "
                f"${c21['BS']['estimate']:.3f}$ on Bank~B", "R2 P2-P1 estimates"))
    out.append(("RQ2: P2-P1 pooled",
                f"and ${p21['estimate']:.3f}$ pooled.", "pooled R2 P2-P1 estimate"))
    out.append(("RQ2: P2-P1 Holm, Bank A",
                "The Holm test detects the reduction on Bank~A and in the pooled fit"
                if c21["A"]["reject"] and not c21["BS"]["reject"] else _FALSE,
                "R2 P2-P1 p_adjusted; rejects on A, not on B"))
    ci21 = [e["contrasts_ci_cluster_item"]["P2-P1"]["ci_cluster_item"]
            for e in (exA, exB, exP)]
    none_excl = all(lo <= 0 <= hi for lo, hi in ci21)
    out.append(("RQ2: P2-P1 Holm, pooled; no interval excludes zero",
                "in the pooled fit, but with items resampled no interval excludes"
                if p21["reject"] and none_excl else _FALSE,
                "pooled p_adjusted; A, B and pooled intervals all include zero"))
    lo, hi = ci21[2]
    out.append(("RQ2: P2-P1 pooled interval",
                f"the pooled one, {_signed_ci(lo, hi)}, only just includes it",
                "exploratory_pooled contrasts_ci_cluster_item P2-P1"))
    lbd = {b: e["guardrail_detail"]["leak_by_delivery"] for b, _, e in banks}
    out.append(("RQ2: blocking notice scored",
                f"{lbd['A']['P2_blocked']['mean_leak_adj']:.3f} on average on Bank~A and "
                f"{lbd['BS']['P2_blocked']['mean_leak_adj']:.3f} on Bank~B",
                "leak_by_delivery.P2_blocked.mean_leak_adj"))
    dA, dB = lbd["A"]["P2_delivered"], lbd["BS"]["P2_delivered"]
    out.append(("RQ2: passed replies scored",
                f"the filter passed scored {dA['mean_leak_adj']:.3f} and "
                f"{dB['mean_leak_adj']:.3f}", "leak_by_delivery.P2_delivered"))
    out.append(("RQ2: passed replies that gave the key",
                f"({dA['given_k']} of {dA['n']} and {dB['given_k']} of {dB['n']})",
                "leak_by_delivery.P2_delivered.given_k / n"))
    t2A, t2B = (r["threshold_sensitivity"][ck]["P2"] for _, r, _ in banks)
    out.append(("RQ2: P2 gives the key, Bank A (count)",
                f"{t2A['over']} of {t2A['n']} Bank~A", "threshold_sensitivity[cut].P2"))
    out.append(("RQ2: P2 gives the key, A share; B count",
                f"replies ({_pct(t2A['rate'])}, {_pct_ci(exA['given']['P2']['ci_cluster_item'])}) "
                f"and {t2B['over']} of {t2B['n']} Bank~B replies ({_pct(t2B['rate'])},",
                "threshold_sensitivity[cut].P2; given.P2.ci_cluster_item"))
    out.append(("RQ2: P2 gives the key, Bank B interval",
                f"{_pct_ci(exB['given']['P2']['ci_cluster_item'])}).",
                "given.P2.ci_cluster_item"))
    p31 = {b: e["contrasts_ci_cluster_item"]["P3-P1"] for b, _, e in banks}
    out.append(("RQ2: P3-P1, Bank A",
                f"P3$-$P1 was ${p31['A']['estimate']:+.3f}$ "
                f"({_signed_ci(*p31['A']['ci_cluster_item'])}) on Bank~A",
                "contrasts_ci_cluster_item P3-P1"))
    bound = max(abs(x) for v in p31.values() for x in v["ci_cluster_item"])
    out.append(("RQ2: P3-P1, Bank B; within bound",
                f"the instruction alone lay within "
                f"{_ceil_at(bound, 2)} on each bank", "P3-P1 interval bounds, ceiling"))
    rA, rB = lbd["A"]["P3_repaired_delivered"], lbd["BS"]["P3_repaired_delivered"]
    out.append(("RQ2: repaired replies still gave the key",
                f"{rA['given_k']} of the {rA['n']} repaired and delivered Bank~A replies "
                f"(Appendix~\\ref{{app:filter}}) and {rB['given_k']} of the {rB['n']} "
                f"Bank~B ones", "leak_by_delivery.P3_repaired_delivered"))
    c32 = {b: r["families"]["R2_mitigation"]["contrasts"]["P3-P2"] for b, r, _ in banks}
    out.append(("RQ2: P3-P2 per bank",
                f"(repair minus filter: ${c32['A']['estimate']:+.3f}$ on Bank~A, "
                f"${c32['BS']['estimate']:+.3f}$ on Bank~B)", "R2 P3-P2 estimates"))
    fl = pool["item_influence"]["families"]["R2_mitigation"]["P3-P2"]["verdict_flipping_items"]
    one_per_bank = sorted(i.split("-")[0] for i in fl) == ["A", "B"]
    out.append(("RQ2: pooled P3-P2 rests on 2 items",
                f"either of {len(fl)} items, one per bank, removes the pooled Holm rejection"
                if one_per_bank else _FALSE,
                "pooled item_influence R2 P3-P2 verdict_flipping_items"))
    br = hv["by_regime"]
    per = {br[r]["n"] for r in ("P0", "P1", "P2", "P3")}
    assert len(per) == 1 and sum(br[r]["n"] for r in ("P0", "P1", "P2", "P3")) == hv["n"]
    out.append(("Fig 3 caption: human sample",
                f"{hv['n']}-reply Bank~A sample, {per.pop()} replies per condition",
                "human_validation_results by_regime n"))
    # RQ3
    sol = {b: r["descriptives"]["leak_adj"]["solicitation"] for b, r, _ in banks}
    for b in ("A", "BS"):
        s = sol[b]
        out.append((f"RQ3: request-style means, {b}",
                    f"{s['S0']['mean']:.3f}, {s['S2']['mean']:.3f} and "
                    f"{s['S3']['mean']:.3f} on {bname[b]}",
                    "descriptives.leak_adj.solicitation"))
    lowest = all(min(s, key=lambda k: s[k]["mean"]) == "S3" for s in sol.values())
    out.append(("RQ3: the demand lowest on both banks",
                "so the explicit demand was the lowest of the three styles on both banks"
                if lowest else _FALSE, "S3 has the lowest mean on each bank"))
    c3s = {b: r["families"]["R3_solicitation"]["contrasts"]["S3-S2"] for b, r, _ in banks}
    out.append(("RQ3: S3-S2 Bank A",
                f"the demand changed it by ${c3s['A']['estimate']:.3f}$ on Bank~A",
                "R3 S3-S2 estimate"))
    out.append(("RQ3: S3-S2 Bank B", f"and ${c3s['BS']['estimate']:.3f}$ on Bank~B.",
                "R3 S3-S2 estimate"))
    pS = pool["families"]["R3_solicitation"]["contrasts"]["S3-S2"]
    pSci = exP["contrasts_ci_cluster_item"]["S3-S2"]["ci_cluster_item"]
    out.append(("RQ3: pooled S3-S2 passes both tests",
                f"a request for help, ${pS['estimate']:.3f}$, passes both tests"
                if pS["reject"] and pSci[1] < 0 else _FALSE,
                "pooled R3 S3-S2; interval below zero"))
    out.append(("RQ3: pooled S3-S2 interval",
                f"{_signed_ci(*pSci)}), but it rests on single items",
                "exploratory_pooled S3-S2 interval"))
    flS = pool["item_influence"]["families"]["R3_solicitation"]["S3-S2"]["verdict_flipping_items"]
    covS = pool["covariate_sensitivity"]["families"]["R3_solicitation"]["contrasts"]["S3-S2"]
    out.append(("RQ3: pooled S3-S2 single items; covariate",
                f"{len(flS)} items (Appendix~\\ref{{app:robustness-influence}}), or adding "
                f"prompt length, removes the Holm rejection."
                if not covS["reject"] else _FALSE,
                "pooled item_influence S3-S2 flips; covariate_sensitivity S3-S2 reject"))
    ups = [e["request_style_spread_upper_bound"]["all_regimes"]["upper"] for e in (exA, exB)]
    same = len({f"{u:.2f}" for u in ups}) == 1
    out.append(("RQ3: spread upper bound",
                f"by more than about {max(ups):.2f}, the item-resampled upper bound"
                if same else _FALSE, "request_style_spread_upper_bound, both banks"))
    # RQ4
    r4 = pool["families"]["R4_robustness"]
    out.append(("RQ4: joint test df",
                f"and ${_tex_int(r4['joint_test']['df_den'])}$ df; Table",
                "R4 joint_test.df_den"))
    i10 = r4["interaction"]["(P1-P0) x (BS-A)"]
    iCI = exP["interaction_ci_cluster_item"]["(P1-P0)x(BS-A)"]["ci_cluster_item"]
    out.append(("RQ4: instruction effect between banks",
                "not distinguishable from zero (Table~\\ref{tab:contrasts}b)"
                if iCI[0] <= 0 <= iCI[1]
                else _FALSE, "R4 interaction (P1-P0); its interval includes zero"))
    out.append(("RQ4: Bank B answer forms",
                f"answer forms for only {fB['retained_items_with_answer_forms']} of the "
                f"{fB['n_retained_items']} items", "detector"))
    out.append(("RQ4: Bank A answer forms",
                f"where it had them for {fA['retained_items_with_answer_forms']} of the "
                f"{fA['n_retained_items']}, its further reduction", "detector"))
    pb, pe = br["pooled_P1_P3"]["both"], br["pooled_P1_P3"]["either"]
    # Controller 2026-11-11: 55.0% is 0.05 from both 'half' and 'three fifths', so no
    # 'about <fraction>' word fits within _ABOUT_TOL; the text says "more than half"
    # instead, true only while the share lies strictly between 1/2 and 3/5 (above 3/5
    # a stronger word would be owed). The tolerance itself is unchanged.
    wb = "more than half" if 0.5 < pb["p"] < 0.6 else None
    out.append(("RQ4: both annotators, mitigated",
                f"key from {pb['k']} of the {pb['n']} mitigated replies ({_pct(pb['p'])}, "
                f"{_pct_ci(pb['ci_cluster_item'])}),", "by_regime.pooled_P1_P3.both"))
    out.append(("RQ4: more than half of the mitigated replies",
                f"{_pct_ci(pb['ci_cluster_item'])}), {wb}," if wb else _FALSE,
                f"share {pb['p']:.4f}; strictly between 1/2 and 3/5"))
    out.append(("RQ4: at least one annotator, mitigated",
                f"from {pe['k']} of {pe['n']} ({_pct(pe['p'])}, {_pct_ci(pe['ci_cluster_item'])})",
                "by_regime.pooled_P1_P3.either"))
    b0, e0 = br["P0"]["both"], br["P0"]["either"]
    assert b0["n"] == e0["n"]
    out.append(("RQ4: P0 counts",
                f"the counts were {b0['k']} and {e0['k']} of {b0['n']}.",
                "by_regime.P0.{both,either}"))
    pa = hv["per_annotator"]
    hi_a, lo_a = sorted((pa[a]["recovered_key"] for a in pa), reverse=True)
    out.append(("RQ4: per-annotator recovery",
                f"recovered the key from {hi_a} of the {hv['n']} replies against the "
                f"other's {lo_a}", "per_annotator.recovered_key"))
    out.append(("more than half: both annotators, mitigated (Intro/Conclusion)",
                f"from {wb} of them" if wb else _FALSE,
                f"by_regime.pooled_P1_P3.both.p = {pb['p']:.4f}"))
    out.append(("more than half: both annotators, mitigated (Discussion)",
                f"both still picked the key from {wb} of" if wb else _FALSE,
                f"by_regime.pooled_P1_P3.both.p = {pb['p']:.4f}"))
    # Discussion
    t10 = {b: r["threshold_sensitivity"][f"{min(grid):.2f}"]["P1"] for b, r, _ in banks}
    out.append(("Discussion: instructed replies below the lowest cut",
                f"{t10['A']['n'] - t10['A']['over']} of {t10['A']['n']} Bank~A replies and "
                f"{t10['BS']['n'] - t10['BS']['over']} of {t10['BS']['n']} Bank~B replies "
                f"stayed below {min(grid):.2f}.", "threshold_sensitivity[lowest].P1"))
    out.append(("Discussion: not saturated",
                f"the replies below {min(grid):.2f} show.", "results.THRESHOLD_GRID"))
    gain = 1 / 2 - 1 / 3
    out.append(("Discussion: ruling out one distractor stays below the cut",
                f"which stays below the {ck} cut" if gain < cut else _FALSE,
                "1/2 - 1/3 < descriptive cut"))
    top = f"{max(grid):.2f}"
    s50 = {b: r["threshold_sensitivity"][top]["P1"]["rate"] for b, r, _ in banks}
    w50A, w50B = _about_fraction(s50["A"]), _about_fraction(s50["BS"])
    out.append(("Discussion: strictest cut, Bank A",
                f"the grid, {top}, about {w50A} of instructed Bank~A replies "
                f"({_pct(s50['A'])}) and about" if w50A else _FALSE,
                "threshold_sensitivity[max].P1.rate, A"))
    out.append(("Discussion: strictest cut, Bank B",
                f"{w50B} of Bank~B's ({_pct(s50['BS'])}) still qualified" if w50B else _FALSE,
                "threshold_sensitivity[max].P1.rate, B"))
    nA, nB = (r["primary_model"]["n_items"] for _, r, _ in banks)
    out.append(("Discussion: item counts", f"imprecise at {nA} and {nB} items",
                "primary_model.n_items"))
    wthird = _about_fraction(fB["retained_items_with_answer_forms"] / fB["n_retained_items"])
    out.append(("Discussion: Bank B answer-form coverage",
                f"for only about {wthird} of" if wthird else _FALSE, "detector, Bank B"))
    return out


# Table 3 (tab:contrasts). A row is located by its label inside its panel and
# compared CELL BY CELL with the computed values, so the check holds whether
# a cell is typeset in text mode (`-0.127`, `{[-0.231, -0.040]}`) or in maths
# (`$-0.127$`, `$[-0.231, -0.040]$`) -- the controller wraps the table in
# maths after P2-I2. On a match the literal is the row as printed; otherwise
# it is the canonical row, which is absent, so the check reports MISSING.
_T3_PANELS = {"a": "\\emph{(a) Within each bank}", "b": "\\emph{(b) Both banks pooled"}
_T3_DAG = "^dag"


def _t3_norm_cell(c: str) -> str:
    c = c.strip()
    dag = c.endswith(_DAGGER)
    if dag:
        c = c[: -len(_DAGGER)].strip()
    while len(c) >= 2 and ((c[0] == "$" and c[-1] == "$")
                           or (c[0] == "{" and c[-1] == "}")):
        c = c[1:-1].strip()
    return c + (_T3_DAG if dag else "")


def _t3_row(body: str, panel: str, row_label: str, cells: list[str]) -> tuple[str, str]:
    """(literal, note) for one Table 3 row; see the comment above."""
    canon = f"{row_label} & " + " & ".join(
        c.replace(_T3_DAG, _DAGGER) for c in cells) + " \\\\"
    i = body.find(_T3_PANELS[panel])
    j = body.find("\\end{tabularx}", i) if i >= 0 else -1
    if i < 0 or j < 0:
        return canon, f"panel ({panel}) of Table 3 not found"
    hits = [ln.strip() for ln in body[i:j].split("\n")
            if ln.strip().startswith(row_label + " &")]
    if len(hits) != 1:
        return canon, f"{len(hits)} rows labelled {row_label!r} in panel ({panel})"
    row = hits[0].split("\\\\")[0].rstrip()
    found = [_t3_norm_cell(x) for x in row.split("&")[1:]]
    if found != cells:
        return canon, f"printed {found} vs computed {cells}"
    return row, "cells match (text or maths form)"


def _ce_table3(D) -> list[tuple[str, str | None, str]]:
    """Table 3: one check per row, covering every cell of it."""
    out: list[tuple[str, str | None, str]] = []
    rows = _table3_rows(D)
    for (panel, name), (lit, note) in rows.items():
        out.append((f"Table 3{panel} row {name}", lit, note))
    out.append(("Table 3 header: interval level",
                f"Item-resampled {D['ci_level']}\\%", "analyse.item_bootstrap_ci level"))
    r4 = D["pool"]["families"]["R4_robustness"]["joint_test"]
    mant, ex = f"{r4['p']:.1e}".split("e")
    out.append(("Table 3b: joint test df and p",
                f"and ${_tex_int(r4['df_den'])}$ df, $p = {mant} \\times 10^{{{int(ex)}}}$;",
                "R4 joint_test.{df_den,p}"))
    return out


# P4 (2026-11-11): Table 3 prints reader-facing row labels, not the deposit's
# codes. This is the ONE code -> label mapping every Table 3 check uses
# (`_table3_rows`, and through it the 'bank BS ... adj p' checks); an unknown
# code raises rather than falling back to the code form.
_T3_LABELS = {
    "P1-P0": "Instruction $-$ none",
    "P2-P1": "Filter $-$ instruction",
    "P3-P2": "Repair $-$ filter",
    "P3-P0": "Repair $-$ none",
    "P3-P1": "Repair $-$ instruction, descriptive",
    "S2-S0": "Help $-$ stuck",
    "S3-S0": "Demand $-$ stuck",
    "S3-S2": "Demand $-$ help",
    "(P1-P0) x bank": "(Instruction $-$ none) $\\times$ bank",
    "(P2-P0) x bank": "(Filter $-$ none) $\\times$ bank",
    "(P3-P0) x bank": "(Repair $-$ none) $\\times$ bank",
}


def _t3_label(name: str) -> str:
    """Table 3's printed label for a contrast code; KeyError when unmapped."""
    return _T3_LABELS[name]


def _table3_rows(D=None) -> dict:
    """{(panel, contrast): (literal, note)} for every row of Table 3; {} when
    an artefact is absent (the callers then report PENDING)."""
    if D is None:
        D = {k: _data(f) for k, f in (
            ("resA", "results_bankA_ol.json"), ("resB", "results_bankBS_ol.json"),
            ("pool", "results_pooled_A-BS_ol.json"), ("exA", "exploratory_bankA_ol.json"),
            ("exB", "exploratory_bankBS_ol.json"), ("exP", "exploratory_pooled_A-BS_ol.json"))}
    if not all(D.get(k) for k in ("resA", "resB", "pool", "exA", "exB", "exP")):
        return {}
    body = _paper_body()
    out = {}

    def tested(res, ex, fam, name):
        c = res["families"][fam]["contrasts"][name]
        ci = ex["contrasts_ci_cluster_item"][name]["ci_cluster_item"]
        p = _p_table(c["p_adjusted"]) + (_T3_DAG if _disagree(c["reject"], ci) else "")
        return [f"{c['estimate']:.3f}", f"{c['se']:.3f}", p,
                f"[{ci[0]:.3f}, {ci[1]:.3f}]"]

    def descriptive(ex, name):
        v = ex["contrasts_ci_cluster_item"][name]
        ci = v["ci_cluster_item"]
        return [f"{v['estimate']:.3f}", "--", "--", f"[{ci[0]:.3f}, {ci[1]:.3f}]"]

    fams = (("R2_mitigation", ("P1-P0", "P2-P1", "P3-P2", "P3-P0")),
            ("R3_solicitation", ("S2-S0", "S3-S0", "S3-S2")))
    for fam, names in fams:
        for n in names:
            lab = _t3_label(n)
            out[("a", n)] = _t3_row(body, "a", lab,
                                    tested(D["resA"], D["exA"], fam, n)
                                    + tested(D["resB"], D["exB"], fam, n))
            out[("b", n)] = _t3_row(body, "b", lab, tested(D["pool"], D["exP"], fam, n))
    lab = _t3_label("P3-P1")
    out[("a", "P3-P1")] = _t3_row(body, "a", lab, descriptive(D["exA"], "P3-P1")
                                  + descriptive(D["exB"], "P3-P1"))
    out[("b", "P3-P1")] = _t3_row(body, "b", lab, descriptive(D["exP"], "P3-P1"))
    inter = D["pool"]["families"]["R4_robustness"]["interaction"]
    for k, v in inter.items():
        a = k.split(" x ")[0]                       # "(P1-P0)"
        ci = D["exP"]["interaction_ci_cluster_item"][k.replace(" x ", "x")]["ci_cluster_item"]
        out[("b", f"{a} x bank")] = _t3_row(
            body, "b", _t3_label(f"{a} x bank"),
            [f"{v['estimate']:.3f}", f"{v['se']:.3f}", "--", f"[{ci[0]:.3f}, {ci[1]:.3f}]"])
    return out


def _ce_table4(D) -> list[tuple[str, str | None, str]]:
    """Table 4 (tab:examples): the picks' scores and their verbatim text."""
    out: list[tuple[str, str | None, str]] = []
    ex = _data("examples_bankA.json")
    if not ex:
        return [("Table 4 examples", None, "examples_bankA.json absent")]
    body = _paper_body()
    E = {k: v["selected"] for k, v in ex["examples"].items()}

    def verbatim(label, text, note):
        """The data's text must appear in the paper once TeX markup is read
        as what it prints: \\texttt{x} -> x, ~ -> space, `` '' -> quotes.
        Literal: the paper's own line when it does, else the data text."""
        def plain(s):
            s = re.sub(r"\\texttt\{((?:[^{}]|\{[^{}]*\})*)\}", r"\1", s)
            s = re.sub(r"\\emph\{([^{}]*)\}", r"\1", s)
            return s.replace("~", " ").replace("``", '"').replace("''", '"')
        lines = body.split("\n")
        for w in (1, 2):                  # a sentence may wrap once
            for i in range(len(lines) - w + 1):
                seg = lines[i:i + w]
                if text in plain(" ".join(x.strip() for x in seg)):
                    return (label, "\n".join(seg), note)
        return (label, text, note + " -- NOT FOUND in the paper")

    def opts(sel, wrap):
        o = json.loads(sel["options"]) if isinstance(sel["options"], str) else sel["options"]
        return [f"({i})~" + (f"\\texttt{{{t}}}" if wrap else t) for i, t in enumerate(o, 1)]

    g = E["given_not_stated"]
    out.append(("Table 4a: adjusted recoverability",
                f"adjusted recoverability {float(g['leak_adj']):.2f}.}}", "examples leak_adj"))
    out.append(("Table 4a: options and key",
                " ".join(opts(g, True)) + f". Key: {g['key_number']}.",
                "examples.given_not_stated options, key_number"))
    out.append(verbatim("Table 4a: stem verbatim", g["stem"],
                        "examples.given_not_stated stem"))
    h = E["help_not_given"]
    out.append(("Table 4b: adjusted recoverability",
                f"adjusted recoverability ${float(h['leak_adj']):.2f}$.}}", "examples leak_adj"))
    out.append(verbatim("Table 4b: stem verbatim", h["stem"], "examples.help_not_given stem"))
    out.append(("Table 4b: options and key",
                " ".join(opts(h, True)) + f". Key: {h['key_number']}.",
                "examples.help_not_given options, key_number"))
    s = E["stated_P0"]
    slv = {r["cell_key"]: r for r in (_load("solve_bankA_ol.jsonl") or [])}
    row = slv.get(s["cell_key"])
    if row:
        hits = sum(v["hits"] for v in row["per_solver"].values())
        n = sum(v["calls"] for v in row["per_solver"].values())
        assert abs(float(s["leak_adj"]) - row["leak_adj"]) < 1e-12
        out.append(("Table 4c: all calls chose the key",
                    f"all {n} calls chose the key; adjusted recoverability "
                    f"{row['leak_adj']:.2f}.}}" if hits == n else _FALSE,
                    "solve_bankA_ol.jsonl hits == calls for the pick"))
    o = opts(s, False)
    out.append(("Table 4c: options 1-2", " ".join(o[:2]), "examples.stated_P0 options"))
    out.append(("Table 4c: option 3 and key", " ".join(o[2:]) + f". Key: {s['key_number']}.",
                "examples.stated_P0 options, key_number"))
    out.append(verbatim("Table 4c: stem verbatim", s["stem"], "examples.stated_P0 stem"))
    out.append(("Table 4c: reply verbatim", f"& {s['reply']}", "examples.stated_P0 reply"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--strict-coverage", action="store_true",
                    help="any UNCOVERED or NEW number, an unexcused year, or an "
                         "unanchored scope fails the run (the P2 exit gate)")
    ap.add_argument("--list-covered", action="store_true",
                    help="also list every covered token with the checks pinning it")
    args = ap.parse_args()

    if not PAPER.exists():
        print(f"no manuscript at {PAPER}")
        return 1
    tex = PAPER.read_text(encoding="utf-8")
    # WHAT THE READER SEES. Every presence and absence test below runs on the
    # comment-stripped text: until 2026-11-02 a number surviving only in a
    # `%%` comment "verified" (P0.4 R1). The highlight scan just below is the
    # one deliberate exception -- it reads comment lines by design.
    body = texutil.strip_comments(tex)

    # Highlights are submitted to the journal but live in LaTeX comments, so
    # they render nothing and the \TBD guard never sees them. Surface any that
    # still assert an unmeasured result, so they cannot reach a submission by
    # being invisible in the built PDF.
    unresolved = [l.split("TBD-RESULT:", 1)[1].strip()
                  for l in tex.splitlines()
                  if "TBD-RESULT:" in l and l.strip().startswith("%% HIGHLIGHT")]
    for h in unresolved:
        print(f"PENDING  {'highlight awaiting results':<44} ({h[:60]})")

    # THE ABSTRACT HAS A HARD LIMIT AND THE MARGIN IS ONE WORD.
    #
    # Nothing computed this until 2026-08-28, so it was recounted by hand every
    # time the abstract changed and the answers disagreed (see abstract_words).
    # A limit checked by whoever remembers to check it is not a limit.
    # A COUNT THAT CANNOT BE TAKEN ON A CURRENT BUILD FAILS (fix round 1, F2).
    # PENDING means "the question was not asked" -- no build, or one older
    # than paper.tex. When the build is current and a count still comes back
    # None, the anchors failed, and a non-failing PENDING there is how a cap
    # disappears with exit 0: "is there a measurement?" instead of "is this
    # a measurement?". The freshness question is `_pdf_staleness`, the same
    # one both counts ask.
    build_current = _pdf_staleness(PDF) is None
    uncountable = 0
    pending_extra = 0
    n_abs, abs_note = abstract_words(tex, PDF)
    if n_abs is None:
        if build_current:
            print(f"FAIL     {'abstract word count':<44} ({abs_note}) -- the "
                  f"build is current, so the count could not be taken")
            uncountable += 1
        else:
            print(f"PENDING  {'abstract word count':<44} ({abs_note})")
            pending_extra += 1
    else:
        slack = ABSTRACT_LIMIT - n_abs
        if slack < 0:
            print(f"OVERLONG {'abstract':<44} {n_abs} words, limit "
                  f"{ABSTRACT_LIMIT} ({abs_note})")
        elif slack <= 5:
            print(f"note     {'abstract':<44} {n_abs}/{ABSTRACT_LIMIT} words, "
                  f"{slack} to spare -- any addition displaces something")

    # THE MAIN TEXT HAS A CAP TOO, counted off the same build by the same
    # freshness rule (P0.4 R5).
    n_main, main_note = main_text_words(tex, PDF)
    if n_main is None:
        if build_current:
            print(f"FAIL     {'main text word count':<44} ({main_note}) -- the "
                  f"build is current, so the count could not be taken")
            uncountable += 1
        else:
            print(f"PENDING  {'main text word count':<44} ({main_note})")
            pending_extra += 1
    elif n_main > MAIN_TEXT_CAP:
        print(f"OVERLONG main text {n_main}/{MAIN_TEXT_CAP} words ({main_note})")
    elif n_main > MAIN_TEXT_TARGET:
        print(f"note     {'main text':<44} {n_main} words, above the "
              f"{MAIN_TEXT_TARGET} target (cap {MAIN_TEXT_CAP})")

    # SUPERSEDED LITERALS MUST BE ABSENT, not merely outvoted by a correct one.
    #
    # Every check below asks whether a value is PRESENT, which cannot see a
    # stale copy of the same quantity elsewhere in the file. On 2026-08-17 the
    # solver table was updated to the re-screened values and the sentence two
    # paragraphs above it still read "from 0.097 to 0.337" -- the checks for
    # 0.096 and 0.319 passed, because those literals were now in the table, and
    # the prose quoting the superseded pair went unflagged. The module docstring
    # calls this out as a known limitation ("drift, not misplacement"); this
    # closes the half of it that can be closed cheaply.
    #
    # When a reported quantity changes, add its OLD rendering here with the
    # reason. The list is append-only in practice: a value that was once wrong
    # in this manuscript stays wrong.
    superseded: list[tuple[str, str]] = [
        ("0.097", "gemma4 cond. acc before the 2026-08-17 re-screen; now 0.096"),
        ("0.337", "gpt-oss cond. acc before the re-screen; now 0.319"),
        ("0.276", "minimax cond. acc before the re-screen; now 0.296"),
        ("0.611", "gemma4 cond. acc all-40 before the re-screen; now 0.609"),
        ("0.698", "gpt-oss cond. acc all-40 before the re-screen; now 0.692"),
        # Narrowed from the bare "96.3" at P2-I2 (2026-11-11): the integrated
        # paper prints 96.3 as the upper bound of the human "at least one"
        # item-resampled interval over P1-P3, $[69.2, 96.3]$ (Section 4.4) --
        # a DIFFERENT quantity, pinned by its own check. The superseded value
        # was a percentage and was typeset with \%, so this form still catches
        # it wherever it creeps back, and no longer flags the twin.
        ("96.3\\%", "parseable rate before the re-screen; now 97.2"),
        # Controller, 2026-11-11 (P2-I2 mismatches): bounds that were rounded the
        # wrong way for an "at most"/"at least" claim. Phrase-anchored so the
        # digits stay free for any other quantity.
        ("more than 0.00066", "Bank A failure bound; max shift 0.000661, now 0.00067"),
        ("the bound is 0.0010.", "Bank B failure bound; max shift 0.001029, now 0.0011"),
        ("within 1.3 percentage points", "max |P1-P3| over the cut grid is 1.337 pp; now 1.4"),
        ("at least 63.1\\%", "Bank A minimum over cuts is 63.095% (318/504); now 63.0"),
        ("about half of them", "human both-annotator recovery 55.0% (99/180) is not 'about half' "
                               "within the fraction-word tolerance; now 'more than half'"),
        ("$[0.00, 0.48]$", "retained baseline range before the re-screen"),
        ("one solver call wide", "invariance band before the re-screen; now three"),
        # The S1 removal (spec 4.43) re-sized the human-validation sample from
        # 4-per-cell of 64 cells to 5-per-cell of 48. The Method was corrected and
        # the check above pins 240 -- but the RESULTS scaffold still said 256 for a
        # day, because a present-value check cannot see a stale twin in another
        # section. That is the entire reason this list exists.
        ("256-response", "validation sample under the pre-S1-removal frame; now 240"),
        # Bank B's solve landed 2026-08-23 and the omission paragraph that
        # carried this went with it. NOTE what is NOT here: `$23{,}328$`, which
        # was the PROJECTED extension cost in that paragraph and is now the
        # REALISED Bank B solver-call count in the R4 section. Same literal,
        # opposite status -- listing it would flag a correct sentence, which is
        # how a checker teaches its reader to ignore it.
        ("$18{,}612$", "solver calls consumed before Bank B's solve; the "
                       "sentence quoting it was removed when Bank B landed"),
        # P2-I2 (2026-11-11). The difficulty-independence figures came from
        # the kr PILOT screen while the sentence described the ol screen
        # (triage FLAG F1); App A now prints the ol values (10 of 10 levels,
        # rho 0.09, t_38 0.56, rho 0.15). Each old form was grepped first: none
        # is printed anywhere in the integrated paper.
        ("7 of the 10 levels", "kr-pilot levels with a discard; ol screen: all 10"),
        ("$\\rho = 0.11$", "kr-pilot rho(level, discard); ol screen: 0.09"),
        ("$t_{38} = 0.66$", "kr-pilot t for rho(level, discard); ol screen: 0.56"),
        ("$\\rho = 0.06$", "kr-pilot rho(level, baseline); ol screen: 0.15"),
        # The screen's transport failures, once rounded to 0.1%; App A states
        # the same 2 of 2400 as 0.08% (triage row 38).
        ("$0.1\\%$", "screen transport failures rounded to 0.1%; stated as 0.08%"),
    ]
    # A SUPERSEDED VALUE CAN BE A CURRENT VALUE FOR SOMETHING ELSE, and this
    # list is literals rather than quantities, so it cannot tell them apart on
    # its own. First run: `0.698` flagged as gpt-oss's pre-re-screen all-40
    # accuracy while being, in the same table, minimax's correct one. Reporting
    # that would train the reader to ignore the check -- the failure mode of
    # every warning that cries wolf.
    #
    # So a superseded literal is only stale if no CURRENT check computes it.
    # `checks()` is the authority on what is currently right, so ask it rather
    # than maintaining a second list that would drift from the first.
    results = checks()
    # RETIREMENT IS BY LABEL AND MUST HIT SOMETHING (P0.4 R2). A retired
    # check's literal is not current: it pins nothing and vouches for nothing.
    labels = {label for label, _, _ in results}
    # A LABEL MUST NAME ONE CHECK (fix round 1, M7): RETIRED is keyed by
    # label, so a duplicate would let one entry retire several checks.
    dup_labels = sorted(l for l, k in Counter(lb for lb, _, _ in results).items()
                        if k > 1)
    for l in dup_labels:
        print(f"ERROR    duplicate check label: {l!r} -- RETIRED is keyed by "
              f"label, so every label must name exactly one check")
    unknown_retired = [(l, why) for l, why in RETIRED.items() if l not in labels]
    for l, why in unknown_retired:
        print(f"ERROR    RETIRED label matches no check: {l!r} ({why}) -- "
              f"a typo must not silently retire nothing")
    # ONE filter for "current" (P0.6 fix round 2, N6). A dict keeps one
    # literal per label; a duplicated label is already an ERROR above that
    # fails the run.
    current_map = _current_literals(results)
    current = set(current_map.values())
    stale = [(lit, why) for lit, why in superseded
             if lit in body and lit not in current]

    # THE ABSTRACT IS PASTED INTO EDITORIAL MANAGER AS PLAIN TEXT (P0.4 R3).
    lint = abstract_lint(tex, current)
    for problem in lint:
        print(f"LINT     abstract: {problem}")

    # HIGHLIGHTS, from the file beside the manuscript (P0.4 R4).
    hl_status, hl_problems = highlights_check(
        PAPER.with_name(HIGHLIGHTS_NAME), current)
    if hl_status == "PENDING":
        print(f"PENDING  {'highlights':<44} (no {HIGHLIGHTS_NAME} beside "
              f"{PAPER.name} yet)")
        pending_extra += 1
    elif hl_status == "OK":
        print(f"ok       {'highlights':<44} {HIGHLIGHTS_NAME}")
    for problem in hl_problems:
        print(f"FAIL     highlights: {problem}")
    for lit, why in stale:
        print(f"STALE    {'superseded value still present':<44} "
              f"{lit!r} -- {why}")

    ok = missing = retired = 0
    pending = len(unresolved) + pending_extra
    for label, literal, note in results:
        if label in RETIRED:
            print(f"RETIRED  {label}  ({RETIRED[label]})")
            retired += 1
        elif literal is None:
            print(f"PENDING  {label:<44} ({note})")
            pending += 1
        elif literal in body:
            ok += 1
            if args.verbose:
                print(f"ok       {label:<44} {literal}")
        else:
            print(f"MISSING  {label:<44} computed {literal!r} not found in paper.tex ({note})")
            missing += 1

    # EVERY NUMBER IN THE TEXT GETS A CHECK (P0.6). Reported always; it fails
    # the run only under --strict-coverage, the P2 exit gate. It reads
    # `current_map` (built once from `results` above), so checks() still runs
    # once, and it never touches the counts in the summary line below.
    cov = _coverage_scan(tex, current_map, _bib_keys(PAPER, tex))
    for r in cov.records:
        note = f"  [{r.note}]" if r.note else ""
        print(f"{r.label:<19} {r.token:<10} l.{r.line:<5} {r.context}{note}")
    if args.list_covered:
        # M1 (ruling): loose twins stay; the P2 exit review reads this list
        for line_no, tok, labels in cov.covered:
            print(f"COVERED  l.{line_no:<5} {tok:<10} {'; '.join(labels)}")
    n_unc = sum(1 for r in cov.records if r.kind == "UNCOVERED")
    n_new = sum(1 for r in cov.records if r.kind == "NEW")
    n_lit = sum(1 for r in cov.records if r.kind == "INFO literature")
    n_year = sum(1 for r in cov.records if r.kind == "INFO year")
    n_year_fail = sum(1 for r in cov.records if r.strict_fail)
    print(f"coverage: {n_unc} uncovered, {n_new} new, {n_lit} literature, "
          f"{n_year} year ({n_year_fail} without \\cite or %% YEAR) -- "
          f"{cov.n_covered} of {cov.n_tokens} number tokens in the main text "
          f"and appendices are pinned by a check")
    # NOT A MEASUREMENT (fix round 1, C2): printed on every run, failing only
    # under --strict-coverage, like every other coverage finding.
    if cov.refusal:
        print(f"FAIL coverage: scope not anchored ({cov.refusal})"
              f"{_anchor_hint(cov.refusal)}")
    cov_fail = args.strict_coverage and bool(
        cov.refusal or n_unc or n_new or n_year_fail)
    if args.strict_coverage and (n_unc or n_new or n_year_fail):
        year_part = (f", {n_year_fail} year without \\cite or %% YEAR"
                     if n_year_fail else "")
        print(f"FAIL coverage: {n_unc} uncovered, {n_new} new{year_part}")

    print(f"\n{ok} verified, {missing} missing, {pending} pending, "
          f"{len(stale)} stale, {retired} retired")
    if missing:
        print("A MISSING check means the manuscript and the data disagree, OR the\n"
              "sentence was reworded so the literal no longer appears. Both need a\n"
              "human decision -- do not 'fix' it by loosening the check.")
    if stale:
        print("A STALE hit means a value this manuscript has already corrected is\n"
              "still somewhere in the file. The correct value being present too is\n"
              "exactly the condition that hides it -- find the second occurrence.")
    over = n_abs is not None and n_abs > ABSTRACT_LIMIT
    if over:
        print("An OVERLONG abstract is a desk-reject at submission. The count is\n"
              "taken from the built PDF, so it is what the editor will see.")
    main_over = n_main is not None and n_main > MAIN_TEXT_CAP
    if main_over:
        print("An OVERLONG main text exceeds the journal's length cap. It is\n"
              "counted off the built PDF, Introduction to back matter.")
    if lint:
        print("A LINT means the abstract would not survive a paste into Editorial\n"
              "Manager as plain text, or carries a number no check computes.")
    hl_fail = hl_status == "FAIL"
    if uncountable:
        print("A FAIL on a word count means the build is current but the count\n"
              "could not be anchored: the limit is unchecked, which is not a pass.")
    return 1 if (missing or stale or over or main_over or lint or hl_fail
                 or unknown_retired or uncountable or dup_labels or cov_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
