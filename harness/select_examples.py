"""Worked examples for the revised manuscript, chosen by a rule fixed in advance.

An example is the one place a reader sees a reply, which makes it the easiest
place to show the most dramatic one. So nothing here is chosen by reading
replies. The rule below was written (in this docstring and in the P1C agent log)
before this module first ran, and the module only applies it.

THE RULE
--------
Bank A only. Bank B is GFDL text that is measured but never redistributed, so
`build` refuses any other bank and no Bank B reply, stem or option is ever read
for output.

Population: the rows of `human_validation.join_generate_solve("A", "ol")` -- the
2,016 delivered Bank A replies, each with its `leak_adj`.

"stated" is the C2 detector, `exploratory.detect(row, items).explicit`:
`checker.check_explicit` on the reply AS DELIVERED (`response`), with the
current item table `harness/banks/bank_a.json`.

Order: within each category every candidate is ranked by sha256(cell_key as
UTF-8), ascending, and the FIRST is selected. There is no other preference --
not length, generator, item or wording -- and no distinct-item constraint, so
one item may appear in more than one category.

  (a) given_not_stated      regime P1, not blocked, not stated,
                            leak_adj >= 0.75  -- a reply that gives the answer
                            without naming it.
  (b) repaired_still_given  regime P3, verifier_rejections > 0, not blocked,
                            leak_adj >= 0.50  -- the verifier rejected the first
                            candidate, the repair passed it, and the delivered
                            reply still gives the answer away.
  (c) help_not_given        any regime, not blocked, not stated,
                            leak_adj <= 0.10  -- help that does not give the
                            answer. If no reply qualifies, `selected` is null and
                            `n_candidates` is 0; the cut is never loosened.
                            ("not stated" is an addition to the brief's (c),
                            fixed before the first run: a reply that names the
                            key is not help that withholds it.)
  (d) stated_P0             regime P0, stated  -- the unmitigated baseline.

The two ">=" cuts are `results.exceeds_threshold`, the comparison the results
file's threshold grid uses. A (b) example also carries the first candidate
(`original_response`) and its stated flag, to show what the repair removed.

AMENDMENT 1 (2026-11-08, fix round 1, review F5)
------------------------------------------------
Amendment 1 was written in the P1C agent log and here before the amended
selection was run.
The original (c) pick, `A-L07-03|ol/minimax-m3:cloud|S2|P3|1`, was a reply cut
off at the generator cap. Ruling: amend every category uniformly and disclose
it; never re-select by eye, never keep the truncated pick.

  Every category (a)-(d) also excludes a reply whose delivering call hit the
  generator cap.

  * The delivering call is the row's LAST call: the delivered `response` equals
    `calls[-1].content` on every unblocked Bank A row (1,963 of 1,963).
  * The cap is the budget that call ran under: the row's recorded
    `generator_max_tokens` when present, otherwise
    `config.GENERATOR_BUDGET_BEFORE_MEASUREMENT["value"]` (400, backends.chat's
    default, which every Bank A row was generated at -- spec 4.56; no Bank A row
    records the field). `run.py` now takes the budget from
    `config.generator_max_tokens(model)`, a table that did not exist for that run.
  * The call records keep no stop reason (`meta` holds only
    `tokens_in_header`; no done_reason or finish_reason), so a hit is
    `completion_tokens >= cap`. Kept candidates within 5 tokens below the cap
    are counted (`n_near_cap`).
  * A blocked reply delivers the placeholder, which no call produced: never a hit.

`rule_amendment` in the output records the date, the reason, every pick the
original rule would have made that the amendment superseded, and the candidate
counts under both rules -- computed by applying both, not typed.

    python3 select_examples.py          # writes data/examples_bankA.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import checker as K              # noqa: E402
import config as C               # noqa: E402
import exploratory as X          # noqa: E402
import human_validation as hv    # noqa: E402
import results as RS             # noqa: E402

OUT_PATH = C.ROOT / "data" / "examples_bankA.json"
ORDER = "sha256(cell_key as UTF-8), ascending; the first candidate is selected"
AMENDMENT_DATE = "2026-11-08"
AMENDMENT_REASON = (
    "Review F5: the original (c) pick was a reply cut off at the generator cap. "
    "Every category now excludes a reply whose delivering call hit the cap "
    "(completion_tokens >= the budget that call ran under; no stop reason is "
    "recorded). Applied uniformly; nothing re-selected by eye.")
NEAR_CAP = 5


def cap_of(row: dict) -> int:
    """The budget the row's calls ran under (see AMENDMENT 1)."""
    b = row.get("generator_max_tokens")
    return int(b) if b is not None else int(
        C.GENERATOR_BUDGET_BEFORE_MEASUREMENT["value"])


def _delivering_tokens(row: dict) -> int | None:
    if row.get("blocked"):
        return None
    calls = row.get("calls") or []
    if not calls or calls[-1].get("completion_tokens") is None:
        raise ValueError(f"{row.get('cell_key')!r}: no token count for the call "
                         f"that delivered the reply; the cap cannot be checked.")
    return int(calls[-1]["completion_tokens"])


def hit_generator_cap(row: dict) -> bool:
    t = _delivering_tokens(row)
    return t is not None and t >= cap_of(row)


def near_generator_cap(row: dict) -> bool:
    t = _delivering_tokens(row)
    return t is not None and cap_of(row) - NEAR_CAP <= t < cap_of(row)


def _rejected(r) -> bool:
    return int(r.get("verifier_rejections") or 0) > 0


#: Each predicate reads only fields that the written record also carries
#: (regime, blocked, stated, leak_adj, verifier_rejections, hit_generator_cap),
#: so the rule can be re-applied to the published file, not just to the rows it
#: was read from. ORIGINAL_RULES is the rule as first fixed; RULES adds
#: Amendment 1 to every category.
ORIGINAL_RULES = {
    "given_not_stated": {
        "text": "(a) regime P1, not blocked, not stated, leak_adj >= 0.75",
        "predicate": lambda r: (r["regime"] == "P1" and not r["blocked"]
                                and not r["stated"]
                                and RS.exceeds_threshold(r["leak_adj"], 0.75)),
    },
    "repaired_still_given": {
        "text": "(b) regime P3, verifier_rejections > 0, not blocked, "
                "leak_adj >= 0.50",
        "predicate": lambda r: (r["regime"] == "P3" and _rejected(r)
                                and not r["blocked"]
                                and RS.exceeds_threshold(r["leak_adj"], 0.50)),
    },
    "help_not_given": {
        "text": "(c) any regime, not blocked, not stated, leak_adj <= 0.10",
        "predicate": lambda r: (not r["blocked"] and not r["stated"]
                                and r["leak_adj"] <= 0.10),
    },
    "stated_P0": {
        "text": "(d) regime P0, stated",
        "predicate": lambda r: r["regime"] == "P0" and r["stated"],
    },
}

RULES = {
    cat: {"text": rule["text"] + "; delivering call not at the generator cap "
                                 "(Amendment 1)",
          "predicate": (lambda f: lambda r: f(r) and not r["hit_generator_cap"])(
              rule["predicate"])}
    for cat, rule in ORIGINAL_RULES.items()}


def _hash(cell_key: str) -> str:
    return hashlib.sha256(cell_key.encode("utf-8")).hexdigest()


def _view(row: dict, items: dict) -> dict:
    """The fields the rules read, with `stated` from the C2 detector."""
    res = X.detect(row, items)
    return {"regime": row["regime"], "blocked": bool(row.get("blocked")),
            "stated": res.explicit, "stated_reason": res.reason,
            "leak_adj": row["leak_adj"],
            "verifier_rejections": int(row.get("verifier_rejections") or 0),
            "hit_generator_cap": hit_generator_cap(row)}


def _record(row: dict, view: dict, items: dict, cat: str) -> dict:
    it = items[row["item_id"]]
    k = it["answer_index"]
    rec = {
        "cell_key": row["cell_key"], "cell_key_sha256": _hash(row["cell_key"]),
        "item_id": row["item_id"], "topic": it.get("topic"),
        "stem": it["stem"], "options": list(it["options"]),
        "key_index": k, "key_number": k + 1, "key_text": it["options"][k],
        "regime": row["regime"], "solicitation_key": row["solicitation_key"],
        "solicitation_text": C.SOLICITATIONS.get(row["solicitation_key"]),
        "generator": row["generator"], "rep": row.get("rep"),
        "reply": row["response"], "leak_adj": row["leak_adj"],
        "stated": view["stated"], "stated_reason": view["stated_reason"],
        "blocked": view["blocked"],
        "verifier_rejections": view["verifier_rejections"],
        "repair_attempts": int(row.get("repair_attempts") or 0),
        "hit_generator_cap": view["hit_generator_cap"],
        "generator_cap": cap_of(row),
        "delivering_call_completion_tokens": _delivering_tokens(row),
    }
    if cat == "repaired_still_given":
        first = row.get("original_response") or ""
        rec["first_candidate"] = first
        rec["first_candidate_stated"] = K.check_explicit(
            first, items[row["item_id"]]).explicit
    return rec


def _ranked(views, predicate):
    return sorted(((r, v) for r, v in views if predicate(v)),
                  key=lambda rv: _hash(rv[0]["cell_key"]))


def select(rows: list[dict], items: dict, original: bool = False) -> dict:
    """Apply RULES (or, with `original=True`, ORIGINAL_RULES) to `rows`."""
    views = [(r, _view(r, items)) for r in rows]
    out = {}
    rules = ORIGINAL_RULES if original else RULES
    for cat, rule in rules.items():
        cands = _ranked(views, rule["predicate"])
        before = _ranked(views, ORIGINAL_RULES[cat]["predicate"])
        at_cap = [r["cell_key"] for r, v in before if v["hit_generator_cap"]]
        out[cat] = {
            "rule": rule["text"], "n_candidates": len(cands),
            "n_candidates_before_amendment": len(before),
            "n_excluded_at_cap": len(at_cap),
            "excluded_at_cap_cell_keys": at_cap,
            "n_near_cap": sum(1 for r, _ in cands if near_generator_cap(r)),
            "selected": _record(*cands[0], items, cat) if cands else None,
        }
    return out


def build(bank: str = "A", roster: str = "ol") -> dict:
    if bank.upper() != "A":
        raise ValueError(
            f"examples are drawn from Bank A only; {bank!r} refused. Bank B is "
            f"GFDL text: measured, never redistributed.")
    rows = hv.join_generate_solve("A", roster)
    items, path = X.item_table("A")
    # The item table the detector reads and the one the annotators saw must
    # agree on what the options and the key ARE (answer_forms may differ).
    screened = hv._options_by_item("A", roster)
    for iid in {r["item_id"] for r in rows}:
        if screened[iid] != (items[iid]["options"], items[iid]["answer_index"]):
            raise ValueError(f"{iid}: options/key differ between "
                             f"{path.name} and the frozen screened bank.")
    return {
        "bank": "A", "roster": roster,
        "population": {"source": "human_validation.join_generate_solve",
                       "n_rows": len(rows)},
        "detector": {"function": X.DETECTOR,
                     "checker_sha256": X._sha256(Path(K.__file__)),
                     "bank_file": f"harness/banks/{path.name}",
                     "bank_sha256": X._sha256(path),
                     "input_field": "response"},
        "order": ORDER,
        "examples": (ex := select(rows, items)),
        "rule_amendment": _amendment(ex, select(rows, items, original=True)),
    }


def _amendment(amended: dict, original: dict) -> dict:
    """What Amendment 1 changed, computed by applying both rules."""
    pick = lambda e: e["selected"]["cell_key"] if e["selected"] else None  # noqa: E731
    return {
        "id": "Amendment 1", "date": AMENDMENT_DATE, "reason": AMENDMENT_REASON,
        "superseded": {cat: pick(original[cat]) for cat in amended
                       if pick(original[cat]) != pick(amended[cat])},
        "candidates_before": {c: original[c]["n_candidates"] for c in original},
        "candidates_after": {c: amended[c]["n_candidates"] for c in amended},
        "near_cap_definition": f"cap - {NEAR_CAP} <= completion_tokens < cap",
    }


def main() -> int:
    d = build("A")
    OUT_PATH.write_text(json.dumps(d, indent=1, ensure_ascii=False,
                                   allow_nan=False) + "\n", encoding="utf-8")
    for cat, e in d["examples"].items():
        s = e["selected"]
        print(f"{cat:<22} candidates {e['n_candidates']:>4}  " + (
            f"{s['cell_key']}  leak_adj {s['leak_adj']:.3f}  "
            f"stated {s['stated']}" if s else "NONE"))
    print(f"-> {OUT_PATH.relative_to(C.ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
