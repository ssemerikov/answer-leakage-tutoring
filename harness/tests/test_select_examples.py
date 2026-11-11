"""Worked examples (revision 2026-11, P1C): chosen by a rule, not by a reader.

An example in a paper is the one place where a reader sees a reply, so it is the
easiest place to put the most dramatic one. The module fixes the rule in its
docstring, orders every candidate by a hash of its `cell_key`, and takes the
first. These tests pin that: the rule as written, the hash order, Bank A only.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipelines as PL         # noqa: E402
import select_examples as SE   # noqa: E402

ITEM = {"id": "A-T1", "options": ["list", "tuple", "dict"], "answer_index": 1,
        "answer_forms": ["tuple"], "stem": "Which is immutable?"}
ITEMS = {"A-T1": ITEM}


def row(regime="P1", leak=0.8, response="Which can change in place?",
        blocked=False, rejections=0, rep=0, sol="S0", original=None,
        tokens=(120,), budget=None):
    r = {"cell_key": f"A-T1|g/x:cloud|{sol}|{regime}|{rep}", "item_id": "A-T1",
         "regime": regime, "solicitation_key": sol, "generator": "g/x:cloud",
         "model": "g/x:cloud", "rep": rep, "leak_adj": leak,
         "response": response, "blocked": blocked,
         "verifier_rejections": rejections, "repair_attempts": rejections,
         "original_response": original if original is not None else response,
         "calls": [{"ok": True, "completion_tokens": t,
                    "content": (response if i == len(tokens) - 1 else "draft")}
                   for i, t in enumerate(tokens)]}
    if budget is not None:
        r["generator_max_tokens"] = budget
    return r


def _h(r):
    return hashlib.sha256(r["cell_key"].encode("utf-8")).hexdigest()


def test_the_rule_is_written_in_the_docstring():
    doc = SE.__doc__
    for phrase in ("sha256(cell_key", "(a)", "(b)", "(c)", "(d)",
                   "leak_adj >= 0.75", "leak_adj >= 0.50", "leak_adj <= 0.10",
                   "verifier_rejections > 0", "Bank A only"):
        assert phrase in doc, phrase


def test_each_category_takes_the_lowest_hash_candidate():
    rows = [row(rep=i, leak=0.9) for i in range(6)]
    pick = SE.select(rows, ITEMS)["given_not_stated"]
    assert pick["n_candidates"] == 6
    assert pick["selected"]["cell_key"] == min(rows, key=_h)["cell_key"]
    assert pick["selected"]["cell_key_sha256"] == _h(min(rows, key=_h))


def test_the_boundaries_are_inclusive_as_written():
    a = SE.select([row(leak=0.75)], ITEMS)["given_not_stated"]
    assert a["n_candidates"] == 1
    b = SE.select([row(regime="P3", rejections=1, leak=0.5)], ITEMS)
    assert b["repaired_still_given"]["n_candidates"] == 1
    c = SE.select([row(regime="P2", leak=0.10)], ITEMS)["help_not_given"]
    assert c["n_candidates"] == 1


def test_a_stated_reply_is_never_a_given_not_stated_example():
    rows = [row(response="The answer is tuple.", leak=0.95)]
    assert SE.select(rows, ITEMS)["given_not_stated"]["n_candidates"] == 0


def test_blocked_and_unrepaired_replies_are_not_repair_examples():
    rows = [row(regime="P3", rejections=2, blocked=True,
                response=PL.BLOCKED_TEXT, leak=0.9, rep=0),
            row(regime="P3", rejections=0, leak=0.9, rep=1)]
    assert SE.select(rows, ITEMS)["repaired_still_given"]["n_candidates"] == 0


def test_an_empty_category_says_so_rather_than_loosening():
    rows = [row(leak=0.5)]
    c = SE.select(rows, ITEMS)["help_not_given"]
    assert c["n_candidates"] == 0 and c["selected"] is None


def test_a_repair_example_shows_what_the_repair_removed():
    r = row(regime="P3", rejections=1, leak=0.6,
            original="The answer is tuple.")
    b = SE.select([r], ITEMS)["repaired_still_given"]
    assert b["selected"]["first_candidate"] == "The answer is tuple."
    assert b["selected"]["first_candidate_stated"] is True
    assert b["selected"]["stated"] is False


def test_bank_b_is_refused():
    with pytest.raises(ValueError, match="Bank A"):
        SE.build("bs")


# ------------------------------------------------------------ real data

@pytest.fixture(scope="module")
def built():
    return SE.build("A")


def test_the_artefact_on_disk_is_current(built):
    on_disk = json.loads(SE.OUT_PATH.read_text(encoding="utf-8"))
    assert on_disk == json.loads(json.dumps(built))


def test_every_example_is_bank_a_and_obeys_its_rule(built):
    for cat, e in built["examples"].items():
        s = e["selected"]
        if s is None:
            continue
        assert s["item_id"].startswith("A-"), "Bank B text must never be written"
        # The written record carries every field its rule reads, so the rule
        # can be re-applied to what was published, not only to what was read.
        assert SE.RULES[cat]["predicate"](s), cat
        assert e["n_candidates"] >= 1
    assert built["examples"]["given_not_stated"]["selected"] is not None
    assert built["examples"]["repaired_still_given"]["selected"] is not None



# ---- fix round 1, F5: Amendment 1 -- a reply cut off at the cap is no example

CAP = __import__("config").GENERATOR_BUDGET_BEFORE_MEASUREMENT["value"]


def test_the_amendment_is_written_in_the_docstring():
    doc = SE.__doc__
    for phrase in ("Amendment 1", "2026-11-08", "generator cap", "delivering call",
                   "GENERATOR_BUDGET_BEFORE_MEASUREMENT", "generator_max_tokens",
                   "completion_tokens >= cap", "no stop reason"):
        assert phrase in doc, phrase


def test_the_cap_is_the_recorded_budget_or_the_pre_measurement_default():
    assert SE.cap_of(row()) == CAP == 400
    assert SE.cap_of(row(budget=1626)) == 1626
    assert SE.hit_generator_cap(row(tokens=(CAP,))) is True
    assert SE.hit_generator_cap(row(tokens=(CAP - 1,))) is False
    assert SE.hit_generator_cap(row(tokens=(1000,), budget=1626)) is False
    assert SE.hit_generator_cap(row(tokens=(1626,), budget=1626)) is True


def test_only_the_delivering_call_counts():
    # A P3 repair: the FIRST candidate ran to the cap, the delivered repair
    # did not. The student received a complete reply.
    r = row(regime="P3", rejections=1, leak=0.6, tokens=(CAP, 150))
    assert SE.hit_generator_cap(r) is False
    r2 = row(regime="P3", rejections=1, leak=0.6, tokens=(150, CAP))
    assert SE.hit_generator_cap(r2) is True


def test_a_blocked_reply_was_produced_by_no_call():
    import pipelines as PL
    r = row(regime="P2", blocked=True, response=PL.BLOCKED_TEXT, rejections=1,
            tokens=(CAP,))
    assert SE.hit_generator_cap(r) is False


def test_every_category_excludes_a_reply_at_the_cap_and_counts_it():
    rows = [row(leak=0.9, rep=0, tokens=(CAP,)), row(leak=0.9, rep=1),
            row(leak=0.9, rep=2, tokens=(CAP - 3,))]
    e = SE.select(rows, ITEMS)["given_not_stated"]
    assert e["n_candidates"] == 2 and e["n_candidates_before_amendment"] == 3
    assert e["n_excluded_at_cap"] == 1 and e["n_near_cap"] == 1
    assert e["excluded_at_cap_cell_keys"] == [rows[0]["cell_key"]]
    assert e["selected"]["hit_generator_cap"] is False


def test_the_real_amendment_is_recorded_and_superseded_only_pick_c(built):
    a = built["rule_amendment"]
    assert a["date"] == "2026-11-08" and "cap" in a["reason"]
    assert a["superseded"] == {"help_not_given": "A-L07-03|ol/minimax-m3:cloud|S2|P3|1"}
    assert a["candidates_before"] == {"given_not_stated": 219,
                                      "repaired_still_given": 27,
                                      "help_not_given": 161, "stated_P0": 370}
    for cat, e in built["examples"].items():
        assert a["candidates_after"][cat] == e["n_candidates"]
        assert e["selected"]["hit_generator_cap"] is False
