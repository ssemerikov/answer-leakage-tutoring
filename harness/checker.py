"""Tier 1: deterministic detection of EXPLICIT answer naming.

Scope is deliberately narrow. This decides one question -- did the response name
the answer outright? -- and declines everything else. A checker that mis-decides
an item is worse than no checker at all, because its verdict outranks the panel
wherever it fires.

Two jobs, and they must not be confused:

1. As the P2/P3 VERIFIER. Its criterion is lexical. The study's outcome is
   semantic (masked-stem identifiability). Keeping those criteria different is
   what stops RQ3 from being a tautology: if the guardrail rejected on the same
   criterion the outcome measures, "the guardrail reduces leakage" would be a
   threshold applied to the dependent variable and could not fail.

2. As an EXCLUSION filter, removing trivially-explicit responses from the
   solvability run (no point paying for solver draws on a response that says
   "the answer is 2").

Matching is word-boundary and case-folded, never substring. The motivating
failure is real: a model answered "Think about what word introduces a function
definition", where naive `"def" in text` fires on "definition" and wrongly
reports an explicit reveal of the key `def`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Phrases that turn a bare option number into an assertion of the answer.
# "option 2" asserts; "look at options 1 and 2" does not.
_ASSERTIVE_INDEX = re.compile(
    r"(?:\b(?:answer|correct|right|keyed|solution)\b[^.\n]{0,24}?\b(?:is|:)\s*"
    r"(?:option\s*)?#?(\d{1,2})\b)"
    r"|(?:\boption\s*#?(\d{1,2})\b[^.\n]{0,24}?\bis\b[^.\n]{0,16}?"
    r"\b(?:correct|right|answer|it)\b)",
    re.IGNORECASE,
)

# Frames that turn a bare VALUE into an assertion of the answer. `_ASSERTIVE_INDEX`
# does this for option positions and matches DIGITS ONLY, so it says nothing about
# a key whose text is `0`, `string`, `c` or `Error`.
#
# WHY THIS EXISTS (added 2026-08-18, spec 4.38). `check_explicit` had exactly two
# handles on a key's text: an `answer_forms` entry, matched bare, and
# `option_verbatim`, which needs 12+ characters. Both fail on short keys, and a
# measured audit put Tier-1 sensitivity at 28.9% on the Bank B sample -- 17 of 27
# retained items caught NOTHING, not even "The answer is 0."  The obvious repair,
# adding the bare key as a form, buys that back at the cost of false positives:
# `0`, `2`, `string` and `Error` are ordinary Python-tutoring English, and a false
# positive rejects a CLEAN response in the P2/P3 guardrail, flattering RQ3 -- the
# study's own headline question. Bank A had been carrying that cost unmeasured:
# its key `is` flagged "Think about whether the object is mutable".
#
# The frame separates the two cases instead of trading them off. "the answer is 0"
# asserts; "indexed from 0" does not.
#
# HONEST LIMIT, measured on held-out phrasings rather than the templates this was
# built against: it catches FRAMED assertions only. "The output will be 0." and
# "Try 0." name the answer and are NOT caught. That is not a bug to be widened
# away -- a frame broad enough to catch them matches ordinary prose, which is the
# false-positive direction. Tier 1 is deliberately narrow (see module docstring);
# Tiers 2-3 and the solver-based `leak_hat`, which does not read this module at
# all, carry what it cannot see.
#
# NEGATION IS NOT ASSERTION. "That is not right -- start again from 0" fired on
# `right ... 0` before the lookbehinds below: a denial reads as a claim to any
# matcher that only looks forward. Caught by adversarial probe, not by the
# template set, which is why the probes are now corpus entries in
# `verifier_audit`.
# The window stops at any SENTENCE terminator, not just `.`. With `[^.\n]` it
# spanned "Which one is correct? Start counting at 0." -- a question and an
# unrelated instruction, read as one assertion. `?` and `!` end a sentence as
# surely as `.` does, and the interrogative case is the common one here: a hint
# that asks "which is correct, option 1 or option 2?" is the opposite of a
# reveal. Caught by a test written for the sentence-boundary property itself,
# which is why that test exists separately from the phrasing tests.
_ASSERTIVE_VALUE_FRAME = (
    r"(?<!not )(?<!n't )(?<!never )"
    r"\b(?:answer|correct|right|solution|keyed|pick|choose|select|"
    r"it's|it is|that's|thats|go with)\b[^.?!\n]{0,24}?"
)


def normalise(text: str) -> str:
    """NFKC fold + collapse whitespace. Keeps punctuation: it carries meaning
    for operators such as `//` and `**`, which are legitimate answer tokens."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def _token_pattern(form: str) -> re.Pattern:
    """Word-boundary pattern for one answer form.

    \\b is useless against forms that begin or end with punctuation (`//`, `**`,
    `#`), so the boundary is asserted only on the sides that start or end with a
    word character. Otherwise `**` would never match, and `//` would match
    inside a URL.
    """
    esc = re.escape(form)
    left = r"(?<!\w)" if form[:1].isalnum() or form[:1] == "_" else r"(?<![^\s(\[{,;:'\"])"
    right = r"(?!\w)" if form[-1:].isalnum() or form[-1:] == "_" else r"(?![^\s)\]},.;:'\"!?])"
    return re.compile(left + esc + right, re.IGNORECASE)


@dataclass
class CheckResult:
    explicit: bool
    reason: str | None = None
    matched: str | None = None

    @property
    def decided(self) -> bool:
        """Tier 1 only ever decides the positive case. `explicit=False` means
        'not explicitly named', NOT 'no leakage' -- the semantic measure owns
        that judgement."""
        return self.explicit


def check_explicit(response: str, item: dict) -> CheckResult:
    """Did the response name the answer outright?"""
    text = normalise(response)
    if not text:
        return CheckResult(False, "empty")

    for form in item.get("answer_forms", []):
        if not form:
            continue
        m = _token_pattern(form).search(text)
        if m:
            return CheckResult(True, "answer_form", m.group(0))

    options = item.get("options") or []
    key_idx = item.get("answer_index")
    if options and key_idx is not None:
        key_text = normalise(options[key_idx])
        # Only count verbatim option text when it is substantial: short strings
        # produce false positives inside ordinary prose.
        #
        # CAVEAT, measured 2026-08-16. The original rationale continued "a
        # one-word option such as `int` is already covered by answer_forms" --
        # which holds only for a bank that HAS answer_forms. Bank A has them on
        # 40/40 items; the Bank B sample has none, so on 33 of its 60 items
        # (55%) this threshold leaves assertive-index phrasing as the verifier's
        # ONLY handle. The keys that fall through are the short distinctive ones
        # a tutor is most likely to name verbatim (`.index()`, `cawdra`, `0`).
        # The threshold is not wrong; its justification was bank-specific and
        # was never re-checked against a second bank. See spec 4.20.
        if len(key_text) >= 12 and _token_pattern(key_text).search(text):
            return CheckResult(True, "option_verbatim", key_text)

        for m in _ASSERTIVE_INDEX.finditer(text):
            idx = next((g for g in m.groups() if g), None)
            if idx and int(idx) == key_idx + 1:
                return CheckResult(True, "assertive_index", m.group(0))

        # Last, because it is the broadest: a key of ANY length, but only inside
        # an assertive frame. Runs after the cheaper exact handles so their more
        # specific `reason` survives into the record where they apply.
        m = _assertive_value(text, key_text)
        if m:
            return CheckResult(True, "assertive_value", m)

    return CheckResult(False, "no_explicit_naming")


def _assertive_value(text: str, key_text: str) -> str | None:
    """`key_text` asserted as the answer, rather than merely mentioned."""
    if not key_text:
        return None
    pat = re.compile(_ASSERTIVE_VALUE_FRAME + r"(?<![\w])"
                     + re.escape(key_text) + r"(?![\w])", re.IGNORECASE)
    m = pat.search(text)
    return m.group(0) if m else None


def verify(response: str, item: dict) -> bool:
    """P2/P3 guardrail: True = allowed through, False = rejected.

    Lexical only, by design (see module docstring).
    """
    return not check_explicit(response, item).explicit


def index_text_collisions(items: list[dict]) -> list[dict]:
    """Items where an option's TEXT could be misread as an option INDEX.

    The masked-stem prompt ends "Reply with ONLY the option number", and
    `solvability.parse_choice` takes the first integer in [1, n_options]. For an
    item whose options are themselves small integers -- `1. 3  2. 4  3. 5` -- a
    solver that answers with the option's TEXT rather than its position is
    scored as having chosen a different option.

    Only one case actually misscores: the KEY's text is an in-range integer that
    differs from the key's own position. Then a solver that correctly identified
    the key, but reported it by value, is recorded as a miss.

    Direction, for the record: this DEFLATES both the baseline and the hinted
    arm, so it makes the screen more permissive (an item is likelier to be
    retained) and understates leakage on the affected items. It partly cancels
    in the difference, since both arms use the same prompt and the same parser.

    Measured 2026-08-16: 2 of 40 Bank A items and 2 of 60 Bank B sample items.
    A direct probe of two solvers on `A-L07-02` produced only positional
    replies, so no text-reply was observed -- but six calls is not evidence of
    absence, which is why this is reported rather than dismissed.

    Returns one dict per affected item. Empty list means no exposure.
    """
    out = []
    for it in items:
        opts = it.get("options") or []
        ki = it.get("answer_index")
        if not opts or ki is None or not (0 <= ki < len(opts)):
            continue
        key_text = str(opts[ki]).strip()
        if not key_text.lstrip("-").isdigit():
            continue
        value = int(key_text)
        if 1 <= value <= len(opts) and value != ki + 1:
            out.append({
                "id": it.get("id"),
                "key_index": ki + 1,
                "key_text": key_text,
                "would_be_scored_as_index": value,
                "n_options": len(opts),
            })
    return out
