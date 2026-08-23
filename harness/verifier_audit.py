#!/usr/bin/env python3
"""Measure the Tier-1 verifier's sensitivity and false-positive rate per bank.

WHY THIS EXISTS. `checker.check_explicit` is Tier 1 of the leakage taxonomy and
the P2/P3 guardrail, and its own docstring says a checker that mis-decides an
item "is worse than no checker at all, because its verdict outranks the panel
wherever it fires". Nothing measured whether it decides correctly. Both banks
were wrong, in OPPOSITE directions, and neither was visible without this:

  Bank B sample, no `answer_forms` at all -> 28.9% sensitivity. Of 27 retained
  items, 17 catch ZERO blatant answer-naming responses. `option_verbatim` needs
  a key of 12+ characters and Bank B's median key is 6; `assertive_index` only
  matches DIGITS, so a key of `Option B` or `c` has no detector whatever.

  Bank A, `answer_forms` on 40/40 -> 100% sensitivity but 5.0% false positives,
  driven almost entirely by ONE item: `A-L03-04`'s key is the word `is`, so its
  bare form fires on any hint containing the English copula ("Think about
  whether the object is mutable" reads as an explicit reveal). `A-L04-01` has
  the same shape with `y`.

The lesson is the §4.33 one again. "Author forms like Bank A's" was a rule held
in someone's head and never checked against data; the process that produced 38
good form-sets also produced two that break the item, and no test could tell.

WHAT IT MEASURES. Two rates, deliberately separate -- they trade off, and a
single "accuracy" number would hide which way an edit moved them:

  sensitivity  fraction of blatant answer-naming responses correctly flagged.
               Templates are deliberately crude ("The answer is X."): if the
               checker misses THESE it will miss subtler reveals, so this is an
               upper bound on real-world sensitivity, not an estimate of it.

  fp_rate      fraction of ANSWER-FREE responses wrongly flagged. Two corpora:
               `--corpus stress` (hand-written tutoring prose, adversarial by
               construction -- it deliberately contains digits, type names and
               copulas) and `--corpus placebo` (`C.PLACEBO_RESPONSES`, genuinely
               answer-free but only four bland utterances, too weak to stress
               anything -- it reports 0% at every threshold, which is why the
               stress corpus exists).

NEITHER CORPUS IS A SAMPLE OF REAL RESPONSES, and this tool must not be read as
if it were. Once `generate` has run, pass `--corpus generated` to audit against
the real thing; until then these are stress tests that establish a floor.

DIRECTION OF HARM, since the two errors are not symmetric. A false positive
rejects a CLEAN response in the P2/P3 guardrail, which makes prompt-level
mitigation look more effective than it is -- biasing RQ3, the study's headline
question, toward "yes, it suffices". A false negative lets a leaking response
through, where the solver-based `leak_hat` still measures it. So FP is the more
dangerous error here, and a form set that buys sensitivity with FP is not
obviously a good trade.

    python3 verifier_audit.py --bank A
    python3 verifier_audit.py --bank bs --retained-only
    python3 verifier_audit.py --bank A --corpus placebo --verbose
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import checker
import config as C
import run as R

#: Crude, explicit answer-naming. If the checker misses these it misses everything.
LEAK_TEMPLATES = (
    "The answer is {k}.",
    "The correct option is {k}.",
    "It's {k} -- that's the one you want.",
    "You should pick {k} here.",
    "Go with {k}.",
)

#: Answer-free tutoring prose, adversarial ON PURPOSE. Real hints contain digits,
#: type names and copulas, which is exactly what an over-broad form matches. A
#: corpus of bland encouragement would report 0% and prove nothing.
STRESS_CORPUS = (
    "Remember that Python sequences are indexed from 0, so the first element is at position 0.",
    "Think about what type the expression evaluates to before you choose.",
    "Try tracing the loop by hand for a list of 3 items and see what changes each pass.",
    "Consider whether the object is mutable, and what that implies for the assignment.",
    "Look at the difference between an integer and a string here -- int and str behave differently.",
    "One of these creates a new list; the other modifies it in place. Which does your code need?",
    "Check the order of operations carefully; 2 + 3 * 4 is not 20.",
    "What happens at the boundary, when the list is empty or has exactly 1 element?",
    "Work through each option and eliminate the ones that cannot be right.",
    "The key idea is scope: where is the name visible, and where is it not?",
    "What is the difference between the two operators being compared here?",
    "Ask yourself what value the expression has after the second statement runs.",
    # META-REFERENCES. A hint may legitimately talk ABOUT the options, the
    # printed output, or a named construct without asserting any of them. These
    # four sentences are what demoted `Error`, `If statement`, `Option B` and
    # `Output a` from "distinctive" to "ambiguous": each looked like a safe
    # distinctive key by inspection, and each fires on ordinary prose.
    "Compare option a, option b, option c and option d carefully before deciding.",
    "Look at output a, output b and output c side by side.",
    "This code raises an error at runtime -- think about why.",
    "Think about the if statement and when its body actually runs.",
    # SYMMETRY OVER FAMILIES, not a list of words that happened to occur to me.
    # `tuple` first read as SAFE and `string` as UNSAFE purely because an
    # earlier corpus mentioned strings and lists but not tuples -- a gap in the
    # corpus manufacturing a safe verdict for an equally common word. Where a
    # key belongs to an obvious family (builtin types, option labels, output
    # labels), the corpus must cover the whole family or it privileges whichever
    # members it forgot.
    "Is the result an int, a str, a string, a list, a tuple, a dict, a set, "
    "a float or a bool? Work it out from the literal.",
    "The loop runs 0, 1, 2, 3 or more times depending on the input.",
    # DISCOURSE FRAMES AND PARTIAL OPTION TEXT. Every sentence below started as
    # an ad-hoc probe of a candidate form I merely suspected, and every one of
    # those suspicions was correct: `to sum up`, `both b and c` and `save to a
    # list` all read as distinctive key text and all occur as ordinary prose.
    # They live here rather than in my head so the classification reproduces.
    "To sum up, think about what the loop accumulates each pass.",
    "Consider both b and c before you decide; are both b and c the same?",
    "You could save to a list, or you could print each value directly.",
    "Let me put it another way, in short, and to conclude: reason it through.",
    # NEGATED AND INTERROGATIVE FRAMES. `assertive_value` matches an assertive
    # word followed by the key, and a denial or a question contains the same
    # words in the opposite sense. Each of these fired, or nearly did, during
    # adversarial probing of that detector.
    "That is not right -- start again from 0 and re-count.",
    "Which is correct, option 1 or option 2? Count the iterations: 0 then 1.",
    "Pick the option that matches; compare a, b and c in turn.",
    "Choose the type: is it a list, a string or a tuple?",
    "The right approach is to eliminate the ones that cannot work.",
)


def form_is_safe(item: dict, form: str, corpus=STRESS_CORPUS) -> bool:
    """Would adding `form` to this item flag any answer-free text?

    THE CLASSIFICATION MUST BE MEASURED, NOT LISTED. This started as a wordlist
    of "common" tokens, and the wordlist was wrong on four of fifteen items:
    `Error`, `If statement`, `Option B` and `Output a` all read as distinctive
    and all fire on ordinary tutoring prose ("this raises an error", "compare
    option b and option c"). A judgement about which strings are ambiguous in
    Python-tutoring English is not one to make by intuition when it can be run.

    A BARE NUMERAL IS NEVER SAFE, and this one rule is a priori rather than
    measured. Which numbers appear in a corpus is an accident of what its author
    wrote: `10` and `4.5` passed a corpus containing 0-3, and would have failed
    one containing 10. Since a hint may legitimately mention any number at all,
    no corpus can establish that a numeric form is safe -- so the empirical test
    is not applicable to that class rather than passing it.
    """
    stripped = checker.normalise(form).replace(".", "").replace("-", "")
    if stripped.isdigit():
        return False
    probe = dict(item)
    probe["answer_forms"] = [form]
    return not any(checker.check_explicit(t, probe).explicit for t in corpus)


def _retained(bank: str, roster: str) -> set[str]:
    p = R.RUNS / f"screen_bank{bank.upper()}_{roster}.jsonl"
    if not p.exists():
        sys.exit(f"no screen at {p.name}; run it or drop --retained-only")
    out = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("passes_headroom"):
                out.add(r["item_id"])
    return out


def _corpus(name: str, bank: str, roster: str) -> tuple[str, ...]:
    if name == "stress":
        return STRESS_CORPUS
    if name == "placebo":
        return tuple(C.PLACEBO_RESPONSES)
    if name == "generated":
        p = R.RUNS / (f"generate_bank{bank.upper()}"
                      f"{'' if roster == 'kr' else '_' + roster}.jsonl")
        if not p.exists():
            sys.exit(f"--corpus generated needs {p.name}, which does not exist "
                     f"yet. Run `run.py generate` first, or use --corpus stress.")
        # Only P2/P3 responses are meant to be answer-free; P0/P1 are not, so
        # counting them as false positives would penalise the checker for
        # correctly flagging a genuine reveal.
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("ok") and rec.get("response") and rec.get("regime") in ("P2", "P3"):
                out.append(rec["response"])
        if not out:
            sys.exit(f"{p.name} holds no successful P2/P3 responses to audit.")
        return tuple(out)
    raise ValueError(name)


def audit(bank: str, roster: str, corpus: str, retained_only: bool) -> dict:
    items = [i for i in R.load_bank(bank)["items"] if i.get("type") == "mcq"]
    if retained_only:
        keep = _retained(bank, roster)
        items = [i for i in items if i["id"] in keep]
    if not items:
        sys.exit("no items to audit")
    texts = _corpus(corpus, bank, roster)

    per_item = {}
    for it in items:
        key = it["options"][it["answer_index"]]
        caught = sum(1 for t in LEAK_TEMPLATES
                     if checker.check_explicit(t.format(k=key), it).explicit)
        fps = [(t, checker.check_explicit(t, it)) for t in texts]
        fps = [(t, r) for t, r in fps if r.explicit]
        per_item[it["id"]] = {
            "key": key,
            "forms": list(it.get("answer_forms") or []),
            "caught": caught,
            "n_templates": len(LEAK_TEMPLATES),
            "fp": len(fps),
            "n_corpus": len(texts),
            "fp_examples": [(r.matched, t) for t, r in fps[:3]],
        }
    sens_n = sum(v["caught"] for v in per_item.values())
    sens_d = len(items) * len(LEAK_TEMPLATES)
    fp_n = sum(v["fp"] for v in per_item.values())
    fp_d = len(items) * len(texts)
    return {"bank": bank.upper(), "roster": roster, "corpus": corpus,
            "n_items": len(items), "per_item": per_item,
            "sensitivity": sens_n / sens_d, "sens_n": sens_n, "sens_d": sens_d,
            "fp_rate": fp_n / fp_d, "fp_n": fp_n, "fp_d": fp_d}


def report(res: dict, verbose: bool) -> int:
    print(f"\nTier-1 verifier audit -- bank {res['bank']}, {res['n_items']} mcq "
          f"items, corpus={res['corpus']}\n")
    print(f"  sensitivity  {res['sens_n']:4d}/{res['sens_d']:<4d} = "
          f"{res['sensitivity']:6.1%}   (blatant reveals correctly flagged)")
    print(f"  false pos.   {res['fp_n']:4d}/{res['fp_d']:<4d} = "
          f"{res['fp_rate']:6.1%}   (answer-free text wrongly flagged)")

    blind = [(k, v) for k, v in res["per_item"].items() if v["caught"] == 0]
    noisy = sorted(((k, v) for k, v in res["per_item"].items() if v["fp"]),
                   key=lambda kv: -kv[1]["fp"])
    if blind:
        print(f"\n  {len(blind)} item(s) catch NOTHING -- no detector fires on any "
              f"template:")
        for k, v in (blind if verbose else blind[:8]):
            print(f"    {k:<32} key={v['key']!r} forms={v['forms'] or 'NONE'}")
        if not verbose and len(blind) > 8:
            print(f"    ... and {len(blind) - 8} more (--verbose)")
    if noisy:
        print(f"\n  {len(noisy)} item(s) fire on answer-free text:")
        for k, v in (noisy if verbose else noisy[:8]):
            print(f"    {k:<32} key={v['key']!r} {v['fp']}/{v['n_corpus']}")
            for m, t in v["fp_examples"]:
                print(f"        via {m!r} on: {t[:64]}")
    if not blind and not noisy:
        print("\n  No blind items and no false positives on this corpus.")
    print()
    # Non-zero exit is for CI-style use: either failure mode is a defect.
    return 1 if (blind or noisy) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bank", required=True)
    ap.add_argument("--roster", default="ol")
    ap.add_argument("--corpus", default="stress",
                    choices=("stress", "placebo", "generated"))
    ap.add_argument("--retained-only", action="store_true",
                    help="audit only items that passed the headroom screen -- "
                         "the set that actually reaches generate")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    return report(audit(a.bank, a.roster, a.corpus, a.retained_only), a.verbose)


if __name__ == "__main__":
    sys.exit(main())
