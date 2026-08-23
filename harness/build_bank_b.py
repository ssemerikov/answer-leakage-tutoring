"""Build Bank B, the public anchor bank, from Runestone `fopp` RST source.

WHY THIS SHAPE. `fopp` (Foundations of Python Programming, Miller & Resnick et
al.) is **GFDL 1.3 with Invariant Sections** -- verified against
`_sources/FrontBackMatter/copyright.rst`, not against a search snippet. GFDL is
copyleft: distributing extracted items would create a "modified version" and
oblige the entire deposit to carry GFDL plus those invariant sections.

So we measure but do not redistribute. Running measurements over a work is not
distribution, so no copyleft obligation is triggered. Two files are written:

  bank_b.json           full items. LOCAL ONLY -- never deposited, never
                        committed. Needed to issue the API calls.
  bank_b_locators.json  DEPOSITED. Item id, source path, directive id, and a
                        SHA-256 of the exact item text used, so a reviewer can
                        fetch the public book and verify byte-for-byte that we
                        measured what we claim, without us redistributing it.

Selection rules, all mechanical and applied before any measurement:

  - `:multiple_answers:` or a comma in `:correct:` -> EXCLUDED. The measure
    assumes exactly one keyed option; a multi-key item has no single "key" for
    the solver to identify.
  - fewer than 3 options -> EXCLUDED (chance floor too high to leave headroom).
  - stem referencing a figure/image -> EXCLUDED as not self-contained.
  - an empty or trivially short stem -> EXCLUDED.

Everything surviving still faces the same headroom screen as Bank A. Nothing
here is hand-picked.

Usage:
    python3 build_bank_b.py --src /tmp/fopp_src/fopp-master
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from pathlib import Path

import config as C
import rebalance_bank as RB

#: Chapter directory -> topic phrase used by the topic-control clause. Derived
#: from the book's own structure rather than assigned by us, so the topic labels
#: are not another authorial degree of freedom.
TOPIC = {
    "SimplePythonData": "variables, expressions and statements",
    "Sequences": "sequences and strings",
    "Iteration": "iteration and loops",
    "Conditionals": "conditionals and boolean expressions",
    "Functions": "functions",
    "Files": "files and input/output",
    "Dictionaries": "dictionaries",
    "TransformingSequences": "transforming sequences",
    "SortingandNesting": "sorting and nested data",
    "Classes": "classes and objects",
    "Inheritance": "inheritance",
    "TestCases": "test cases",
    "Exceptions": "exceptions",
    "GeneralIntro": "general Python concepts",
    "PythonModules": "modules",
    "AdvancedAccumulation": "accumulation patterns",
    "NestedData": "nested data structures",
    "MoreAboutIteration": "iteration",
    "DefiningFunctions": "defining functions",
    "Strings": "strings",
    "Lists": "lists",
    "Selection": "selection",
    "AdvancedFunctions": "advanced functions",
    "Debugging": "debugging",
    "PythonTurtle": "turtle graphics",
    "Sorting": "sorting",
    "Tuples": "tuples",
}

#: Every chapter contributing a retained item MUST appear in TOPIC. An unmapped
#: chapter yields an empty topic, and `_topic_clause` then silently omits the
#: topic phrase for those items only -- disabling topic control for part of the
#: bank while leaving it on elsewhere. That is a different instrument applied to
#: a subset, not a missing label, so the build fails rather than warns.

_DIRECTIVE = re.compile(r"^\.\. mchoice:: (\S+)\s*$", re.M)
_OPTION = re.compile(r"^\s+:answer_([a-h]):\s?(.*)$")
_CORRECT = re.compile(r"^\s+:correct:\s*(.+?)\s*$")
_FIELD = re.compile(r"^\s+:[a-z_]+:")
_IMAGE = re.compile(r"(\.\. image::|\.\. figure::|shown above|the figure|the image)", re.I)


def _clean(text: str) -> str:
    """RST/HTML entity cleanup. Options carry `&quot;` and friends verbatim."""
    return html.unescape(text).strip()


def parse_file(path: Path, rel: str) -> list[dict]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    starts = [(i, m.group(1)) for i, l in enumerate(lines)
              for m in [_DIRECTIVE.match(l)] if m]

    for n, (start, qid) in enumerate(starts):
        # An RST directive body ends at the first non-blank line that is NOT
        # indented. Bounding by the next mchoice instead swallows everything
        # between two questions -- surrounding prose, activecode exercises,
        # their unittest scaffolding -- into the first one's stem. Two items
        # survived selection with narrative text and `~~~~`/`====` activecode
        # separators in place of a question before this was fixed.
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        for j in range(start + 1, end):
            line = lines[j]
            if line.strip() and not line[:1].isspace():
                end = j
                break
        block = lines[start + 1:end]

        options: dict[str, str] = {}
        correct = None
        multiple = False
        body_from = 0
        for j, line in enumerate(block):
            if line.strip() == ":multiple_answers:":
                multiple = True
            mo = _OPTION.match(line)
            if mo:
                options[mo.group(1)] = _clean(mo.group(2))
                body_from = j + 1
                continue
            mc = _CORRECT.match(line)
            if mc:
                correct = mc.group(1).strip()
                body_from = j + 1
                continue
            if _FIELD.match(line):
                body_from = j + 1

        # Stem: everything after the last field line, dedented. Code blocks are
        # kept -- for items of the "which of I, II, III" form the code IS the
        # question, and dropping it would leave an unanswerable stem.
        stem_lines = block[body_from:]
        stem = "\n".join(l[3:] if l.startswith("   ") else l for l in stem_lines)
        stem = re.sub(r"\n{3,}", "\n\n", stem).strip()
        # Normalise RST code directives to plain fenced code. The solver and the
        # tutor should see a quiz question, not reStructuredText markup.
        stem = re.sub(r"\.\.\s+(?:code-block|sourcecode)::\s*\w*\s*\n",
                      "\n```\n", stem)
        stem = re.sub(r"\.\.\s+\w[\w-]*::.*\n(?:\s+:\w+:.*\n)*", "", stem)
        if "```" in stem:
            stem = stem.rstrip() + "\n```" if stem.count("```") % 2 else stem
        stem = re.sub(r"\n{3,}", "\n\n", stem).strip()

        reject = None
        if multiple or (correct and "," in correct):
            reject = "multiple_answers"
        elif not correct or correct not in options:
            reject = "no_single_key"
        elif len(options) < 3:
            reject = "too_few_options"
        elif len(stem) < 15:
            reject = "stem_too_short"
        elif _IMAGE.search(stem):
            reject = "not_self_contained"

        keys = sorted(options)
        record = {
            "id": f"B-{qid}",
            "source_directive": qid,
            "source_path": rel,
            "topic": TOPIC.get(rel.split("/")[1] if "/" in rel else "", ""),
            "type": "mcq",
            "stem": stem,
            "options": [options[k] for k in keys],
            "answer_index": keys.index(correct) if (correct in options) else -1,
            "answer": options.get(correct, ""),
            "rejected_reason": reject,
        }
        record["answer"] = record["answer"] or ""
        out.append(record)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="extracted fopp repo root")
    ap.add_argument("--out", default=str(Path(__file__).parent / "banks"))
    args = ap.parse_args()

    src = Path(args.src)
    srcdir = src / "_sources"
    if not srcdir.is_dir():
        raise SystemExit(f"no _sources under {src}")

    all_items: list[dict] = []
    for p in sorted(srcdir.rglob("*.rst")):
        all_items.extend(parse_file(p, str(p.relative_to(src))))

    # Count first: `kept` holds the same dict objects as `all_items`, so
    # stripping the field before counting empties the tally it is drawn from.
    from collections import Counter
    reasons = Counter(i["rejected_reason"] for i in all_items if i["rejected_reason"])

    kept_published = [i for i in all_items if i["rejected_reason"] is None]
    kept = kept_published
    unmapped = sorted({i["source_path"].split("/")[1] for i in kept if not i["topic"]})
    if unmapped:
        raise SystemExit(
            f"refusing to build: chapters missing from TOPIC map: {unmapped}. "
            f"An empty topic disables topic control for those items only.")
    for i in kept:
        i.pop("rejected_reason")

    # Hash the items AS PUBLISHED before any reordering, so the deposited
    # locators can still prove we measured the published items byte-for-byte.
    published_sha = {
        i["id"]: hashlib.sha256(
            (i["stem"] + "\u241f" + "\u241f".join(i["options"])).encode()).hexdigest()
        for i in kept
    }

    # Balance key positions with the SAME seeded transform applied to Bank A.
    # Applied to both banks or neither: rebalancing only the authored bank would
    # leave option-position bias free to masquerade as a bank effect, which is
    # precisely the comparison Bank B exists to make.
    kept = RB.rebalance(kept)

    outdir = Path(args.out)
    bank = {
        "bank_id": "B",
        "provenance": {
            "source": "Runestone Interactive, Foundations of Python Programming (fopp)",
            "authors": "Brad Miller, David Ranum, Jeffrey Elkner, Peter Wentworth, "
                       "Allen B. Downey, Chris Meyers, Dario Mitchell",
            "repository": "https://github.com/RunestoneInteractive/fopp",
            "licence": "GNU Free Documentation License 1.3, with Invariant Sections "
                       "(Forward, Prefaces, Contributor List)",
            "redistribution": "NOT redistributed. This file is local-only. Only "
                              "bank_b_locators.json and our measurements are deposited.",
        },
        "n_candidates": len(all_items),
        "n_retained": len(kept),
        "rejected_counts": dict(reasons),
        "items": kept,
    }
    (outdir / "bank_b.json").write_text(
        json.dumps(bank, indent=2, ensure_ascii=False), encoding="utf-8")

    locators = {
        "bank_id": "B",
        "option_order": ("Options were permuted by harness/rebalance_bank.py under "
                         f"seed {C.SEED} to balance key positions; the same transform "
                         "is applied to Bank A. Stems, option texts and keys are "
                         "unchanged. `sha256_as_published` verifies the items against "
                         "the source; `sha256_as_measured` matches what we ran."),
        "note": ("Item text is NOT reproduced here: the source is GFDL 1.3 with "
                 "Invariant Sections. Fetch the repository at the recorded commit "
                 "and re-extract with harness/build_bank_b.py to obtain byte-identical "
                 "items; the SHA-256 below verifies the match."),
        "provenance": bank["provenance"],
        "n_retained": len(kept),
        "items": [{
            "id": i["id"],
            "source_path": i["source_path"],
            "source_directive": i["source_directive"],
            "topic": i["topic"],
            "n_options": len(i["options"]),
            "answer_index": i["answer_index"],
            "sha256_as_published": published_sha[i["id"]],
            "sha256_as_measured": hashlib.sha256(
                (i["stem"] + "␟" + "␟".join(i["options"])).encode()
            ).hexdigest(),
        } for i in kept],
    }
    (outdir / "bank_b_locators.json").write_text(
        json.dumps(locators, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"candidates {len(all_items)} -> retained {len(kept)}")
    for r, n in reasons.most_common():
        print(f"  rejected {r:<22} {n}")
    print(f"wrote {outdir/'bank_b.json'} (LOCAL ONLY)")
    print(f"wrote {outdir/'bank_b_locators.json'} (deposited)")


if __name__ == "__main__":
    main()
