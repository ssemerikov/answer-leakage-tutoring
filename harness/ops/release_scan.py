#!/usr/bin/env python3
"""Refuse to publish a file that carries Bank B's item text.

WHY THIS IS A SCRIPT AND NOT A JUDGEMENT
----------------------------------------
Bank B's stems and options are GFDL 1.3 with Invariant Sections. The study
measures over them and the deposit releases LOCATORS plus our own measurements,
never the text. Reading the paper's own wording already excludes a tutor reply
-- a reply is not a measurement -- but "I read the filenames and thought about
it" is not a check that survives the next person adding a file. CLAUDE.md told
its reader to "re-run the scan" for a day while the scan existed only as
something done once by hand: an instruction pointing at nothing (4.51).

WHAT IT CHECKS
--------------
Every long verbatim span from Bank B: stems of at least MIN_STEM characters and
options of at least MIN_OPT. Short options ('1', 'True', '42') are excluded
because they are not the protected expression and a rule that flags every file
in the tree is one its reader learns to ignore.

Note the asymmetry runs the OPPOSITE way to the Tier-1 verifier (4.38): there a
false positive was the expensive error, here a false negative is -- it publishes
protected text. So matching is deliberately permissive. Both sides are compared
with whitespace collapsed and JSON string escapes undone, because a stem stored
in a .jsonl has its newlines written as the two characters backslash-n and a raw
substring test cannot see it. That defect was in the hand-run scan this script
replaces: it could only ever have found single-line OPTIONS, which is exactly
what it found, so nothing in the result revealed that stems were invisible to it.

    release_scan.py <path> [<path> ...]   scan files or directories; rc 1 if any hit
    release_scan.py --self-test           prove both directions, no arguments

Exit status IS the verdict; nothing parses this script's prose.
"""
import json
import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent
BANKS = HARNESS / "banks"
MIN_STEM = 40
MIN_OPT = 25
SKIP_SUFFIXES = {".pdf", ".png", ".jpeg", ".jpg", ".pyc"}
NO_BANKS = ("No Bank B artefacts in this checkout, so there is nothing to scan "
            "against -- expected in the public copy, which does not carry them. "
            "Run this from the working tree.")


def _norm(s: str) -> str:
    """Collapse whitespace and undo JSON string escapes, on BOTH sides.

    A protected stem contains newlines; stored in a .jsonl it becomes literal
    backslash-n. Comparing raw text misses it entirely.
    """
    for a, b in (("\\r\\n", " "), ("\\n", " "), ("\\t", " "),
                 ('\\"', '"'), ("\\/", "/")):
        s = s.replace(a, b)
    return " ".join(s.split())


def protected_strings(banks_dir: Path = BANKS) -> set[str]:
    """Long verbatim spans from every Bank B artefact on disk."""
    out: set[str] = set()
    for p in sorted(banks_dir.glob("*.json")):
        name = p.name.lower()
        if "bank_b" in name or "bank_bs" in name:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            for it in d.get("items", []):
                stem = (it.get("stem") or "").strip()
                if len(stem) >= MIN_STEM:
                    out.add(stem)
                for o in it.get("options") or []:
                    o = str(o).strip()
                    if len(o) >= MIN_OPT:
                        out.add(o)
    return out


def scan(paths, banks_dir: Path = BANKS):
    """[(file, n_spans, example)] for every file carrying protected text."""
    prot = protected_strings(banks_dir)
    if not prot:
        raise SystemExit(NO_BANKS + " Refusing to certify a tree as clean "
                         "using an empty rule.")
    files = []
    for p in paths:
        p = Path(p)
        files.extend(sorted(q for q in p.rglob("*") if q.is_file())
                     if p.is_dir() else [p])
    hits = []
    for f in files:
        if f.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        hay = _norm(text)
        found = [s for s in prot if _norm(s) in hay]
        if found:
            hits.append((f, len(found), found[0][:70]))
    return hits, len(prot), len(files)


def _self_test():
    prot = protected_strings()
    if not prot:
        # The public copy of this repository does not carry Bank B, which is
        # the whole point of the scan. Saying so beats failing (which reads as
        # a broken tool) and beats passing silently (which reads as a clean
        # tree). The scan is run before publishing, from a checkout that has
        # the banks.
        #
        # `scan` refuses in the same situation and this used to explain it in
        # different words, so the two answers to one condition disagreed about
        # what had gone wrong -- the reader of the harsher one learned nothing
        # about why. One sentence, two exit codes: proving nothing is fine
        # (0), certifying nothing is not (1).
        print("SKIPPED: " + NO_BANKS)
        return 0
    longest = max(prot, key=len)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "clean.jsonl").write_text('{"leak_adj": 0.6}\n', encoding="utf-8")
        (d / "dirty.jsonl").write_text(
            '{"response": ' + json.dumps("... " + longest + " ...") + "}\n",
            encoding="utf-8")
        hits, n, _ = scan([d])
        names = {h[0].name for h in hits}
    assert names == {"dirty.jsonl"}, f"expected only dirty.jsonl, got {names}"
    print(f"self-tests pass ({n} protected spans; detects text, ignores measurements)")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "--self-test":
        raise SystemExit(_self_test())
    hits, n_prot, n_files = scan(args)
    print(f"scanned {n_files} files against {n_prot} protected spans")
    for f, k, ex in hits:
        print(f"  REFUSED {f}: {k} verbatim span(s), e.g. {ex!r}")
    print("CLEAN" if not hits else f"{len(hits)} file(s) must not be published")
    raise SystemExit(1 if hits else 0)
