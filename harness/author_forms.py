#!/usr/bin/env python3
"""Apply the audited `answer_forms` decisions to the banks. Idempotent.

WHY A SCRIPT AND NOT A HAND-EDIT. Every form below was accepted or rejected by
`verifier_audit.form_is_safe`, and the script re-checks each one at apply time,
so a form that becomes unsafe when the corpus is strengthened fails here instead
of silently persisting. A hand-edited bank would record the decisions without
the reasoning that produced them.

WHAT IT DOES, and why the two banks are treated differently:

  Bank B sample -- ADD forms to 9 of 27 retained items. Those are the items
  whose key text survives the answer-free corpus as a bare form. The other 18
  keys (`0`, `2`, `string`, `c`, `Error`, `If statement`, `Option B`, `Output
  a/c` ...) are ordinary Python-tutoring English; a form for them buys Tier-1
  sensitivity at the cost of false positives, and a false positive rejects a
  CLEAN response in the P2/P3 guardrail, flattering RQ3 -- the study's own
  headline question. Authors' decision 2026-08-18: leave them unformed and
  report Tier-1 coverage per stratum.

  Bank A -- REMOVE 5 ambiguous bare forms that were authored without ever being
  measured: `is`, `y`, `5`, `3`, `30`. `A-L03-04`'s key is the English word
  `is`, so it flagged "Think about whether the object is mutable" as an explicit
  answer reveal -- on a RETAINED, frozen, deposited item. Their distinctive and
  positional siblings (`is operator`, `option 1`, `answer 1`) stay, and
  sensitivity is unchanged at 100% because the positional forms carry it.

SAFE FOR THE FROZEN BANK AND THE COMPLETED SCREEN. `answer_forms` is read only
by `checker`; it never enters a prompt, and `config.measurement_signature`
excludes it by construction (that function exists precisely so a metadata repair
does not invalidate hours of measurement -- see its docstring). This script
asserts the signature is unchanged and refuses to write if it moved.

    python3 author_forms.py --dry-run
    python3 author_forms.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import config as C
import run as R
import checker
from verifier_audit import LEAK_TEMPLATES, form_is_safe

#: Bank B: forms to ADD, keyed by item id, loaded from `banks/` rather than
#: written here. Every entry survived `form_is_safe` against the audit corpus;
#: the variants that did not are recorded in the spec (§4.38) rather than
#: silently omitted.
#:
#: THE TABLE IS NOT INLINE BECAUSE SOME VARIANTS ARE OPTION TEXT VERBATIM.
#: Bank B is GFDL 1.3 with Invariant Sections, so the deposit publishes our
#: measurements and this code but never the items. An inline table put two
#: option strings into the public repository while the manuscript said a
#: mechanical scan had confirmed no artefact carried any -- caught by
#: `ops/release_scan.py` once the scan became a script that could be re-run.
BANK_B_FORMS_PATH = Path(__file__).resolve().parent / "banks" / "bank_b_answer_forms.json"


def _bank_b_forms() -> dict[str, list[str]]:
    if not BANK_B_FORMS_PATH.exists():
        sys.exit(
            f"{BANK_B_FORMS_PATH.name} is missing. It ships with the bank, not "
            f"with the code, because it contains Bank B option text; the public "
            f"copy of this repository does not carry it.")
    return json.loads(BANK_B_FORMS_PATH.read_text(encoding="utf-8"))["forms"]


#: Bank A: forms to REMOVE are DERIVED, not listed. An earlier version of this
#: file named five items by hand -- the five whose false positives I had noticed
#: -- and applying it left those five items blind while the audit immediately
#: found two more (`float`, `dict`) that the same criterion rejects. Hand-picking
#: reproduces exactly the failure this whole exercise is about: a rule applied
#: where someone happened to look. The criterion is `form_is_safe`, and it is
#: applied to every form in every item of both banks.
#:
#: Positional forms (`option 3`, `answer 3`) always survive it, so no item is
#: left with an empty form list; `apply()` still asserts that per item.


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def apply(dry_run: bool) -> int:
    changed = []

    # ---- Bank B: add ----
    bp = R._bank_path("bs")
    bank = _load(bp)
    sig_before = C.measurement_signature(bank["items"])
    by = {i["id"]: i for i in bank["items"]}
    for iid, forms in _bank_b_forms().items():
        it = by.get(iid)
        if it is None:
            sys.exit(f"{iid} is not in {bp.name}")
        unsafe = [f for f in forms if not form_is_safe(it, f)]
        if unsafe:
            sys.exit(f"refusing to write: {iid} form(s) {unsafe} fire on the "
                     f"answer-free corpus. The corpus has been strengthened "
                     f"since these were chosen; re-audit and drop them.")
        if it.get("answer_forms") != forms:
            it["answer_forms"] = forms
            changed.append(f"  + {iid:<32} {forms}")
    sig_after = C.measurement_signature(bank["items"])
    if sig_before != sig_after:
        sys.exit(f"refusing to write: measurement_signature moved "
                 f"{sig_before[:16]} -> {sig_after[:16]}. `answer_forms` must "
                 f"not be a measured field; something else was edited.")

    # ---- Bank A: remove every form the criterion rejects ----
    ap = R._bank_path("A")
    bank_a = _load(ap)
    sig_a_before = C.measurement_signature(bank_a["items"])
    for it in bank_a["items"]:
        cur = list(it.get("answer_forms") or [])
        if not cur:
            continue
        new = [f for f in cur if form_is_safe(it, f)]
        # BLINDNESS IS MEASURED, NOT INFERRED FROM AN EMPTY LIST. This refused
        # whenever every form failed, on the assumption that no forms means no
        # detection. That was true when `answer_forms` and `option_verbatim`
        # were the only handles on a key; `assertive_index` and `assertive_value`
        # now catch framed assertions with no forms at all, so the assumption
        # would block a safe repair. Ask the checker instead of predicting it.
        if not new:
            probe = dict(it)
            probe["answer_forms"] = []
            key = it["options"][it["answer_index"]]
            still = any(checker.check_explicit(t.format(k=key), probe).explicit
                        for t in LEAK_TEMPLATES)
            if not still:
                sys.exit(
                    f"refusing to write: every form on {it['id']} fails the "
                    f"corpus AND no other detector fires on it, so dropping "
                    f"them trades a false positive for a blind item. That is a "
                    f"judgement call -- raise it rather than letting this "
                    f"script decide.")
        if new != cur:
            it["answer_forms"] = new
            changed.append(f"  - {it['id']:<32} drop "
                           f"{[f for f in cur if f not in new]}")
    sig_a_after = C.measurement_signature(bank_a["items"])
    if sig_a_before != sig_a_after:
        sys.exit("refusing to write: Bank A measurement_signature moved.")

    if not changed:
        print("no changes; banks already carry the audited forms.")
        return 0
    print(f"{len(changed)} item(s) change:")
    print("\n".join(changed))
    print(f"\nmeasurement_signature unchanged: bs {sig_before[:16]}, "
          f"A {sig_a_before[:16]} -- the frozen bank and completed screens "
          f"stay valid.")
    if dry_run:
        print("\n--dry-run: nothing written.")
        return 0
    bp.write_text(json.dumps(bank, indent=2, ensure_ascii=False) + "\n",
                  encoding="utf-8")
    ap.write_text(json.dumps(bank_a, indent=2, ensure_ascii=False) + "\n",
                  encoding="utf-8")
    print(f"\nwrote {bp.name} and {ap.name}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    return apply(p.parse_args().dry_run)


if __name__ == "__main__":
    sys.exit(main())
