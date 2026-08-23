"""Check that every promise the manuscript makes is one the manuscript keeps.

WHY THIS EXISTS. Round 4 of the simulated review found three commitments that
the Method stated and the Results did not deliver (spec 4.52, 4.53): a
pre-registered fallback that fired and was answered with the pooled figure
anyway, a distinction the primary outcome cannot draw whose diagnostic series
was never reported, and a pre-registered sensitivity check that was never run.

NOTHING IN THE TOOLCHAIN COULD FIND THEM. The build, `verify_numbers` and the
`\\TBD` guard all detect a number that is missing or wrong; a dropped promise
leaves NO number to mismatch, so every check stayed green -- 157 verified, 0
missing -- while three commitments went unmet. The audit that finds them was
mechanical (grep the Method for "we report X", check the Results contain X) and
was run by hand twice, which is a rule with no home: the next Method edit that
adds a promise-shaped sentence is invisible again the moment nobody remembers to
re-run it.

WHAT IT DOES. It is a LEDGER check, deliberately not a semantic one. Whether a
Results paragraph really discharges a Method promise is a judgement, and a tool
that guessed would be trusted for a judgement it cannot make. So:

  1. Every promise-shaped sentence in `paper.tex` must have a ledger entry.
     A NEW one -- exactly what a Method edit introduces -- fails the audit until
     a human adjudicates it. This is the check that would have caught 4.52.
  2. Every entry marked `satisfied` names an `evidence` literal that must still
     appear in the manuscript. If the Results are rewritten and the sentence
     that kept the promise goes with them, the promise re-opens.
  3. Entries marked `blocked` are REPORTED on every run, never silently
     accepted, so a promise waiting on data cannot fade out of view.
  4. A ledger entry whose sentence has left the manuscript is STALE and fails:
     the adjudication described text that is no longer there.

WHAT IT CANNOT DO, stated rather than papered over. It cannot tell that
`evidence` genuinely discharges the promise -- only that both texts are present.
Adjudication is human, and `evidence` should therefore be the most specific
literal available (a number, a named quantity), never a common word. It also
cannot find a promise phrased in a way `PATTERNS` does not match; the patterns
are broad and the ledger absorbs the false positives once each, which is the
right trade, because a missed promise is silent and a false positive is one line
of ledger.

Usage:
    python3 promise_audit.py               # audit
    python3 promise_audit.py --list        # every promise-shaped sentence found
    python3 promise_audit.py --new         # only the unadjudicated ones
    python3 promise_audit.py --stub        # ledger skeleton for the new ones
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAPER = HERE.parent / "source" / "paper.tex"
LEDGER = HERE / "docs" / "promise_ledger.json"

# Promise-shaped openings. Broad on purpose: a missed promise is silent, a false
# positive costs one ledger line marked `not-a-promise`. Every one of the three
# 4.52 misses matches the first pattern.
PATTERNS = [
    r"\bwe (?:also |further |additionally |therefore |then )?report\b",
    r"\bwe will\b",
    r"\bwe (?:also |therefore )?present\b",
    r"\bwe (?:also )?provide\b",
    r"\bwe (?:also )?release\b",
    r"\bwe (?:also )?record\b",
    r"\bwe (?:pre-?)?register\b",
    r"\bwe (?:also )?state\b",
    r"\bis reported\b",
    r"\bare reported\b",
    r"\bwill be reported\b",
    r"\breportable\b",
    r"\bwe treat\b.*\bas the\b",
]
PROMISE_RE = re.compile("|".join(PATTERNS), re.I)

STATUSES = {"satisfied", "blocked", "not-a-promise"}


def strip_latex(text: str) -> str:
    """Comment LINES, then macros, then residue -- in that order.

    `re.sub(r'%.*', '', line)` eats an escaped `\\%` and truncates the sentence
    around it, which is how two abstract word counts came out wrong before
    anyone noticed the stripper rather than the count was at fault.
    """
    lines = []
    for ln in text.splitlines():
        if ln.lstrip().startswith("%"):
            continue
        # a real comment starts at an unescaped %
        ln = re.sub(r"(?<!\\)%.*$", "", ln)
        lines.append(ln)
    s = "\n".join(lines)
    s = re.sub(r"\\(?:label|ref|eqref|cite[tp]?|citealp)\s*\{[^}]*\}", " ", s)
    s = re.sub(r"\\(?:textbf|textit|texttt|emph|mathrm|text)\s*\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\[A-Za-z@]+\s*(\[[^\]]*\])?", " ", s)
    s = s.replace("{", " ").replace("}", " ").replace("$", " ").replace("~", " ")
    return s


def sections(text: str) -> list[tuple[int, str]]:
    """(line number, section title) for each \\section, in file order."""
    out = []
    for i, ln in enumerate(text.splitlines(), 1):
        m = re.match(r"\s*\\section\*?\{([^}]*)\}", ln)
        if m:
            out.append((i, m.group(1)))
    return out


def section_of(line_no: int, secs: list[tuple[int, str]]) -> str:
    name = "(front matter)"
    for start, title in secs:
        if start <= line_no:
            name = title
        else:
            break
    return name


def sentences(text: str) -> list[tuple[int, str]]:
    """(line number of the sentence's first character, sentence text).

    Line numbers are kept because a promise is adjudicated by reading it in
    place; a hash alone would make the report unusable.
    """
    raw = text.splitlines()
    out, buf, start = [], [], 1
    for i, ln in enumerate(raw, 1):
        if ln.lstrip().startswith("%"):
            continue
        if not ln.strip():
            if buf:
                out.extend(_split(" ".join(buf), start))
                buf = []
            continue
        if not buf:
            start = i
        buf.append(ln)
    if buf:
        out.extend(_split(" ".join(buf), start))
    return out


def _split(para: str, start: int) -> list[tuple[int, str]]:
    para = strip_latex(para)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", para)
    return [(start, re.sub(r"\s+", " ", p).strip()) for p in parts if p.strip()]


def key(sentence: str) -> str:
    """Stable id: whitespace- and case-normalised, so re-wrapping a paragraph
    does not orphan its ledger entry."""
    norm = re.sub(r"[^a-z0-9 ]+", "", sentence.lower())
    norm = re.sub(r"\s+", " ", norm).strip()
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def promises(text: str) -> list[dict]:
    secs = sections(text)
    out = []
    for line_no, s in sentences(text):
        if PROMISE_RE.search(s):
            out.append(
                {
                    "key": key(s),
                    "line": line_no,
                    "section": section_of(line_no, secs),
                    "sentence": s,
                }
            )
    return out


def load_ledger(path: Path = LEDGER) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def audit(text: str, ledger: dict) -> dict:
    found = promises(text)
    by_key = {p["key"]: p for p in found}
    body = re.sub(r"\s+", " ", strip_latex(text))

    new, satisfied, blocked, not_promise, broken, stale = [], [], [], [], [], []
    for p in found:
        e = ledger.get(p["key"])
        if e is None:
            new.append(p)
            continue
        st = e.get("status")
        if st == "satisfied":
            ev = e.get("evidence", "")
            if not ev:
                broken.append((p, "satisfied with no evidence literal"))
            elif re.sub(r"\s+", " ", strip_latex(ev)).strip() not in body:
                broken.append((p, f"evidence has left the manuscript: {ev!r}"))
            else:
                satisfied.append(p)
        elif st == "blocked":
            blocked.append((p, e.get("reason", "(no reason recorded)")))
        elif st == "not-a-promise":
            not_promise.append(p)
        else:
            broken.append((p, f"unknown status {st!r}; expected one of {sorted(STATUSES)}"))

    for k, e in ledger.items():
        if k not in by_key:
            stale.append((k, e))

    return {
        "found": found,
        "new": new,
        "satisfied": satisfied,
        "blocked": blocked,
        "not_promise": not_promise,
        "broken": broken,
        "stale": stale,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--paper", default=str(PAPER))
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--list", action="store_true", help="print every promise found")
    ap.add_argument("--new", action="store_true", help="print unadjudicated only")
    ap.add_argument("--stub", action="store_true", help="ledger skeleton for the new ones")
    a = ap.parse_args(argv)

    text = Path(a.paper).read_text()
    ledger = load_ledger(Path(a.ledger))
    r = audit(text, ledger)

    if a.list or a.new:
        for p in (r["new"] if a.new else r["found"]):
            print(f"{p['key']}  L{p['line']:<5} [{p['section']}]  {p['sentence'][:150]}")
        return 0

    if a.stub:
        print(json.dumps(
            {p["key"]: {"status": "", "section": p["section"],
                        "sentence": p["sentence"][:200], "evidence": "", "reason": ""}
             for p in r["new"]}, indent=2))
        return 0

    rc = 0
    for p, why in r["broken"]:
        print(f"BROKEN   L{p['line']} [{p['section']}] {why}\n         {p['sentence'][:140]}")
        rc = 1
    for p in r["new"]:
        print(f"NEW      L{p['line']} [{p['section']}] unadjudicated promise\n"
              f"         {p['sentence'][:140]}")
        rc = 1
    for k, e in r["stale"]:
        print(f"STALE    ledger entry {k} describes a sentence no longer in the "
              f"manuscript:\n         {str(e.get('sentence'))[:140]}")
        rc = 1
    # Blocked promises are printed on EVERY run, pass or fail: a promise waiting
    # on data that stops being visible is a promise that gets forgotten.
    for p, reason in r["blocked"]:
        print(f"BLOCKED  L{p['line']} [{p['section']}] {reason}")

    print(f"\n{len(r['satisfied'])} kept, {len(r['blocked'])} blocked, "
          f"{len(r['not_promise'])} not promises, {len(r['new'])} unadjudicated, "
          f"{len(r['broken'])} broken, {len(r['stale'])} stale "
          f"({len(r['found'])} promise-shaped sentences)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
