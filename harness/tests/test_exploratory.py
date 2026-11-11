"""Stated vs given (revision 2026-11, P1C): what the tests protect.

The claim the revised manuscript rests on is that a mitigated reply rarely
STATES the answer yet still GIVES it away. Each half has exactly one definition,
and both are borrowed rather than re-typed:

  stated  -- `checker.check_explicit` on the reply AS DELIVERED (a blocked turn
             is delivered as `pipelines.BLOCKED_TEXT`);
  given   -- `leak_adj` over the 0.25 cut, by `results.exceeds_threshold`, the
             comparison `results.threshold_sensitivity` uses.

So the properties under test are the borrowings, the refusals and the
cross-checks, not the arithmetic of a share.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import exploratory as X       # noqa: E402
import pipelines as PL        # noqa: E402
import results as RS          # noqa: E402

SRC = Path(X.__file__).read_text(encoding="utf-8")

#: A synthetic item: key is option 2 ("tuple"), with one answer form.
ITEM = {"id": "I1", "options": ["list", "tuple", "dict"], "answer_index": 1,
        "answer_forms": ["tuple"]}
ITEMS = {"I1": ITEM}


def row(regime="P1", sol="S0", leak=0.5, response="Think about mutability.",
        blocked=False, rejections=0, original=None, rep=0, gen="g/x:cloud"):
    return {"cell_key": f"I1|{gen}|{sol}|{regime}|{rep}", "item_id": "I1",
            "regime": regime, "solicitation_key": sol, "generator": gen,
            "model": gen, "leak_adj": leak, "response": response,
            "blocked": blocked, "verifier_rejections": rejections,
            "original_response": original if original is not None else response}


# ---------------------------------------------------- the borrowed comparison

def test_results_exposes_one_threshold_rule():
    assert RS.exceeds_threshold(0.25, 0.25) is True     # inclusive: >=
    assert RS.exceeds_threshold(0.2499, 0.25) is False
    assert RS.threshold_key(0.25) == "0.25" and RS.threshold_key(0.1) == "0.10"
    assert 0.25 in RS.THRESHOLD_GRID


def test_given_and_threshold_sensitivity_share_one_comparison(monkeypatch):
    # Swap the ONE rule for a sentinel. If either side had its own copy of the
    # comparison, that side would not move.
    rows = [row(leak=0.0), row(leak=0.9, rep=1)]
    monkeypatch.setattr(RS, "exceeds_threshold", lambda v, t: True)
    s = X.summarise(rows, ITEMS)
    assert s["given"]["P1"]["k"] == 2
    ts = RS.threshold_sensitivity([{"regime": "P1", "leak_adj": 0.0}])
    assert ts["0.25"]["P1"]["over"] == 1


def test_exploratory_never_retypes_the_cut_comparison():
    # Asked of the AST, so a docstring quoting `>=` cannot trip it.
    tree = ast.parse(SRC)
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare):
            names = {x.id for x in ast.walk(n) if isinstance(x, ast.Name)}
            consts = {x.value for x in ast.walk(n) if isinstance(x, ast.Constant)}
            assert "GIVEN_CUT" not in names and X.GIVEN_CUT not in consts, (
                f"line {n.lineno}: the cut is compared here instead of by "
                f"results.exceeds_threshold")
    assert X.GIVEN_CUT in RS.THRESHOLD_GRID


def test_a_reply_exactly_on_the_cut_is_given():
    s = X.summarise([row(leak=0.25)], ITEMS)
    assert s["given"]["P1"]["k"] == 1


# ------------------------------------------------------------ the detector

def test_stated_reads_the_delivered_reply_not_the_first_candidate():
    # A repaired P3 reply: the first candidate named the key, the delivered
    # one does not. What the student received is what is scored.
    r = row(regime="P3", response="Which of these cannot change after creation?",
            original="The answer is tuple.", rejections=1)
    s = X.summarise([r], ITEMS)
    assert s["stated"]["P3"]["k"] == 0
    r2 = row(regime="P0", response="It's a tuple, option 2.")
    assert X.summarise([r2], ITEMS)["stated"]["P0"]["k"] == 1


def test_a_blocked_turn_is_the_blocked_message_and_is_not_stated():
    r = row(regime="P2", response=PL.BLOCKED_TEXT, blocked=True, rejections=1,
            original="The answer is tuple.", leak=0.0)
    s = X.summarise([r], ITEMS)
    assert s["stated"]["P2"]["k"] == 0 and s["stated"]["P2"]["n"] == 1
    assert s["stated"]["P2"]["n_blocked_counted_not_stated"] == 1


def test_a_blocked_row_carrying_other_text_is_refused():
    # Neutral text, so the OTHER refusal (detector firing on a blocked row)
    # cannot be what raises here (review F15, mutant D6).
    r = row(regime="P2", response="Think again.", blocked=True)
    with pytest.raises(ValueError, match="contradicts itself"):
        X.summarise([r], ITEMS)


def test_an_unblocked_row_carrying_the_blocked_message_is_refused():
    r = row(regime="P2", response=PL.BLOCKED_TEXT, blocked=False)
    with pytest.raises(ValueError, match="contradicts itself"):
        X.summarise([r], ITEMS)


def test_a_detector_that_fires_on_the_blocked_message_is_refused():
    # Relying on the checker HAPPENING not to fire on the placeholder would
    # make "blocked counts as not stated" an accident of the answer forms.
    item = dict(ITEM, answer_forms=["blocked"])
    r = row(regime="P2", response=PL.BLOCKED_TEXT, blocked=True)
    # `detect` itself, not `summarise`: there the by-construction refusal
    # (which also says "blocked message") would mask this one (mutant D4).
    with pytest.raises(ValueError, match="detector fires on the blocked"):
        X.detect(r, {"I1": item})


# ------------------------------------------------------------ the tables

def test_the_two_by_two_partitions_each_regime():
    rows = [row(response="The answer is tuple.", leak=0.9, rep=0),
            row(response="The answer is tuple.", leak=0.0, rep=1),
            row(leak=0.9, rep=2), row(leak=0.0, rep=3), row(leak=0.6, rep=4)]
    s = X.summarise(rows, ITEMS)
    t = s["stated_and_given"]["P1"]
    assert t == {"stated_given": 1, "stated_not_given": 1,
                 "not_stated_given": 2, "not_stated_not_given": 1}
    assert sum(t.values()) == s["stated"]["P1"]["n"] == s["given"]["P1"]["n"] == 5
    nsg = s["not_stated_among_given"]["P1"]
    assert (nsg["k"], nsg["n"]) == (2, 3)


def test_request_style_spread_is_max_minus_min_of_the_three_means():
    rows = [row(sol="S0", leak=0.2), row(sol="S0", leak=0.4, rep=1),
            row(sol="S2", leak=0.7), row(sol="S3", leak=0.5)]
    s = X.summarise(rows, ITEMS)
    rs = s["request_style"]["P1"]
    assert rs["S0"]["n"] == 2 and rs["S0"]["mean"] == pytest.approx(0.3)
    assert s["request_style_spread"]["P1"] == pytest.approx(0.7 - 0.3)


def test_the_largest_spread_is_named_with_its_regime():
    # The manuscript quotes "the largest request-style spread"; a number the
    # writers can pin must exist in the file, with the regime it belongs to.
    rows = [row(regime="P0", sol=s, leak=v, rep=i) for i, (s, v) in
            enumerate([("S0", 0.1), ("S2", 0.9), ("S3", 0.5)])]
    rows += [row(regime="P2", sol=s, leak=v, rep=i) for i, (s, v) in
             enumerate([("S0", 0.4), ("S2", 0.5), ("S3", 0.6)])]
    m = X.summarise(rows, ITEMS)["request_style_spread_max"]
    assert m["all_regimes"]["regime"] == "P0"
    assert m["all_regimes"]["spread"] == pytest.approx(0.8)
    assert m["mitigated_P1_P3"]["regime"] == "P2"
    assert m["mitigated_P1_P3"]["spread"] == pytest.approx(0.2)


def test_a_row_outside_the_design_is_refused():
    with pytest.raises(ValueError, match="S1"):
        X.summarise([row(sol="S1")], ITEMS)


def test_wilson_is_the_human_validation_one():
    import human_validation as hv
    s = X.summarise([row(leak=0.9), row(leak=0.0, rep=1)], ITEMS)
    p, lo, hi = hv.wilson(1, 2)
    assert s["given"]["P1"]["rate"] == p and s["given"]["P1"]["ci"] == [lo, hi]


# ------------------------------------------------- guardrail, as run and today

def test_as_run_guardrail_reads_the_recorded_fields():
    rows = [row(regime="P2", response=PL.BLOCKED_TEXT, blocked=True, rejections=1),
            row(regime="P2", rep=1),
            row(regime="P3", rejections=1, rep=2),
            row(regime="P3", response=PL.BLOCKED_TEXT, blocked=True,
                rejections=3, rep=3)]
    g = X.as_run_guardrail(rows)
    assert g["P2"] == {"n": 2, "rejected_rows": 1, "blocked": 1,
                       "repaired_delivered": 0, "rejections_total": 1,
                       "repairs_total": 0}
    assert g["P3"] == {"n": 2, "rejected_rows": 2, "blocked": 1,
                       "repaired_delivered": 1, "rejections_total": 4,
                       "repairs_total": 0}      # no repair_attempts recorded


def test_the_rederivation_reports_disagreement_and_never_raises():
    # Recorded as rejected, but today's checker passes the first candidate --
    # and the reverse. Neither is an error: it is the diagnostic's finding.
    rows = [row(regime="P2", rejections=1, original="Think it through.",
                response=PL.BLOCKED_TEXT, blocked=True),
            row(regime="P3", rejections=0, original="The answer is tuple.",
                response="The answer is tuple.", rep=1),
            row(regime="P3", rejections=0, response="ok", rep=2)]
    rows[2]["original_response"] = None          # absent text: not an error
    d = X.diagnostic_rederivation(rows, ITEMS)
    assert d["P2"]["recorded_only"] == 1 and d["P2"]["both"] == 0
    assert d["P2"]["agreement"] == 0
    assert d["P3"]["today_only"] == 1 and d["P3"]["neither"] == 1
    assert d["P3"]["agreement"] == 1 and d["P3"]["n"] == 2
    assert d["P3"]["today_only_cell_keys"] == ["I1|g/x:cloud|S0|P3|1"]


# ------------------------------------------------------------ cross-checks

def _results_for(rows):
    return {"threshold_sensitivity": RS.threshold_sensitivity(rows),
            "descriptives": {"leak_adj": {"regime": __import__("analyse")
                                          .describe_by(rows, "regime", "leak_adj")}},
            "guardrail": RS.guardrail(rows)}


def _guard_rows():
    return [row(leak=0.9), row(leak=0.1, rep=1),
            row(regime="P2", response=PL.BLOCKED_TEXT, blocked=True,
                rejections=1, rep=2, leak=0.0),
            row(regime="P3", rejections=1, rep=3, leak=0.6)]


def test_the_cross_check_passes_when_the_file_agrees():
    rows = _guard_rows()
    X.cross_check(X.summarise(rows, ITEMS), X.as_run_guardrail(rows),
                  _results_for(rows))


@pytest.mark.parametrize("path,delta,match", [
    # The 0.25 cases match the GIVEN check's own message: the whole-grid check
    # would also catch `over`, and must not be what satisfies this (mutant X1).
    (("threshold_sensitivity", "0.25", "P1", "over"), 1, r"^given\[P1\]"),
    (("threshold_sensitivity", "0.25", "P1", "rate"), 1e-12, r"^given\[P1\]"),
    (("threshold_sensitivity", "0.50", "P1", "over"), 1, r"^given_by_grid"),
    (("descriptives", "leak_adj", "regime", "P1", "mean"), 1e-12, "descriptives"),
    (("guardrail", "P2", "blocked"), 1, "guardrail"),
    (("guardrail", "P3", "repairs"), 1, "guardrail"),
])
def test_the_cross_check_names_each_tampered_field(path, delta, match):
    # Review F7/F14: every compared field has a tamper case of its own, so
    # deleting any one comparison fails a test.
    rows = _guard_rows()
    for r in rows:
        r["repair_attempts"] = r["verifier_rejections"] if r["regime"] == "P3" else 0
    bad = json.loads(json.dumps(_results_for(rows)))
    d = bad
    for k in path[:-1]:
        d = d[k]
    d[path[-1]] += delta
    with pytest.raises(ValueError, match=match):
        X.cross_check(X.summarise(rows, ITEMS), X.as_run_guardrail(rows), bad)


# ------------------------------------------------------------ real data

@pytest.fixture(scope="module")
def built():
    return {b: X.build(b, "ol") for b in ("A", "bs")}


def test_the_real_builds_agree_with_the_results_files(built):
    # `build` runs the cross-check; reaching here means it held.
    for b, n in (("A", 504), ("bs", 972)):
        d = built[b]
        for reg in ("P0", "P1", "P2", "P3"):
            assert d["stated"][reg]["n"] == d["given"][reg]["n"] == n
        assert d["detector"]["function"] == "checker.check_explicit"
        assert d["detector"]["input_field"] == "response"
        assert d["detector"]["blocked_message"] == PL.BLOCKED_TEXT


def test_the_artefacts_on_disk_are_current(built):
    for b in ("A", "bs"):
        on_disk = json.loads(X.out_path(b, "ol").read_text(encoding="utf-8"))
        assert on_disk == json.loads(json.dumps(built[b])), b


#: The grammar of a string this module may write: a code, an id, a file
#: name, a dotted path. No spaces, so no sentence fits. Notes are matched
#: exactly against `X.NOTES`, hashes and cell keys by their own shapes.
_CODE = re.compile(r"^[A-Za-z0-9_.\-/:]+$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _strings(x, key=None):
    if isinstance(x, dict):
        for k, v in x.items():
            yield "<key>", k                      # keys can carry text too
            yield from _strings(v, k)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v, key)
    elif isinstance(x, str):
        yield key, x


def _norm(t):
    return " ".join(str(t).split()).casefold()


#: Our own codes that happen to equal a (one-character) Bank B option once
#: case-folded. Exact strings, each with its reason; nothing else is exempt.
_OWN_CODES_EQUAL_TO_AN_OPTION = {
    "B",    # bootstrap replicate count in the method record, named by the brief
    "n",    # the count key of every share and table entry
}


def _is_bank_b_text(t, opts, stems) -> bool:
    return (t in opts or t in stems
            or any(len(o) >= 8 and o in t for o in opts)
            or any(len(m) >= 20 and m in t for m in stems))


def test_the_bank_b_artefact_carries_no_text():
    """GFDL: measure over Bank B, never redistribute it (review F13).

    Two independent conditions, both strict:
    1. every string is a NOTES value, a sha256, a Bank B cell key whose parts
       are all design ids, or a code with no spaces;
    2. no string EQUALS a Bank B option or stem (so a one-word option such as
       `string` written as a value fails), and none CONTAINS an option of 8+
       characters or a stem of 20+.
    """
    import config as C
    d = json.loads(X.out_path("bs", "ol").read_text(encoding="utf-8"))
    bank = json.loads(Path(X.item_table("bs")[1]).read_text(encoding="utf-8"))
    ids = {it["id"] for it in bank["items"]}
    opts = {_norm(o) for it in bank["items"] for o in it["options"]}
    stems = {_norm(it["stem"]) for it in bank["items"]}
    import human_validation as hv_
    notes = (set(X.NOTES.values())                      # exact strings we wrote
             | {PL.BLOCKED_TEXT, hv_.cluster_method(0)["method"]})
    for key, s in _strings(d):
        if s in notes:
            continue
        parts = s.split("|")
        cell_key = (len(parts) == 5 and parts[0] in ids
                    and parts[1] in C.OL_GENERATORS
                    and parts[2] in C.OL_SOLICITATION_KEYS
                    and parts[3] in C.REGIMES and parts[4].isdigit())
        assert (_SHA.match(s) or cell_key or s in ids or _CODE.match(s)), (
            f"free text under {key!r}: {s[:30]!r}...")
        if s in _OWN_CODES_EQUAL_TO_AN_OPTION:
            continue
        # Booleans, so a failure names the key and never prints Bank B text.
        assert not _is_bank_b_text(_norm(s), opts, stems), (
            f"Bank B option/stem text under {key!r}")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ops"))
    import release_scan
    hits, n_prot, _ = release_scan.scan([X.out_path("bs", "ol")])
    assert n_prot > 0 and hits == []



# ================================================= fix round 1 (review F1-F9)

import analyse as AN          # noqa: E402
import human_validation as HV  # noqa: E402

REGS = ("P0", "P1", "P2", "P3")


def _two_items(regime="P1"):
    """Two items, so an item-cluster interval exists."""
    item2 = dict(ITEM, id="I2")
    rows = []
    for i, iid in enumerate(("I1", "I1", "I1", "I2", "I2", "I2")):
        r = row(regime=regime, leak=(0.9 if i % 2 else 0.1), rep=i)
        r["item_id"] = iid
        r["cell_key"] = r["cell_key"].replace("I1|", f"{iid}|", 1)
        rows.append(r)
    return rows, {"I1": ITEM, "I2": item2}


# ---- F1: item-cluster intervals ---------------------------------------------

def test_cluster_intervals_come_from_the_shared_function():
    rows, items = _two_items()
    s = X.summarise(rows, items)
    cl = [r["item_id"] for r in rows]
    want = AN.cluster_bootstrap_ci([X.is_given(r) for r in rows], cl, "proportion")
    assert s["given"]["P1"]["ci_cluster_item"] == list(want)
    want_m = AN.cluster_bootstrap_ci([r["leak_adj"] for r in rows], cl, "mean")
    assert s["given"]["P1"]["mean_ci_cluster_item"] == list(want_m)
    assert len(s["stated"]["P1"]["ci_cluster_item"]) == 2


def test_a_single_item_has_no_cluster_interval():
    s = X.summarise([row(leak=0.9), row(leak=0.1, rep=1)], ITEMS)
    assert s["given"]["P1"]["ci_cluster_item"] is None
    assert s["spread_ci_cluster_item"].get("P1") is None


def test_the_real_artefacts_record_the_method_and_label_the_old_intervals(built):
    for b, k in (("A", 14), ("bs", 27)):
        iv = built[b]["intervals"]
        assert iv["ci_cluster_item"] == {
            "method": "item-cluster percentile bootstrap", "B": 10000,
            "seed": 20261103, "n_clusters": k}
        assert "unadjusted for item clustering" in iv["ci"]
        assert "unadjusted for item clustering" in iv["mean_ci"]
        assert "upper bound" in iv["spread_ci_cluster_item"]
        assert "R3" in iv["spread_ci_cluster_item"]
        for reg in REGS:
            for blk in ("stated", "given"):
                lo, hi = built[b][blk][reg]["ci_cluster_item"]
                assert lo <= built[b][blk][reg]["rate"] <= hi
            lo, hi = built[b]["given"][reg]["mean_ci_cluster_item"]
            assert lo <= built[b]["given"][reg]["mean"] <= hi
            lo, hi = built[b]["spread_ci_cluster_item"][reg]
            assert lo <= hi
        ub = built[b]["request_style_spread_upper_bound"]
        assert ub["all_regimes"]["upper"] == max(
            built[b]["spread_ci_cluster_item"][r][1] for r in REGS)


def test_the_real_cluster_intervals_are_wider_than_the_unadjusted_ones(built):
    for b in ("A", "bs"):
        g = built[b]["given"]["P1"]
        assert (g["ci_cluster_item"][1] - g["ci_cluster_item"][0]) > \
            2 * (g["ci"][1] - g["ci"][0])


# ---- F2: the re-derivation totals ---------------------------------------------

def test_the_rederivation_total_is_explicit(built):
    for b, n in (("A", 1008), ("bs", 1944)):
        t = built[b]["diagnostic_rederivation"]["total"]
        assert t["compared"] == t["agreeing"] == n
        assert t["today_only"] == t["recorded_only"] == t["not_checkable"] == 0


# ---- F3: answer-form coverage ---------------------------------------------------

def test_form_coverage_partitions_every_regime(built):
    for b, (nw, nwo) in (("A", (13, 1)), ("bs", (9, 18))):
        fc = built[b]["by_form_coverage"]
        for reg in REGS:
            w, wo = fc["with_forms"][reg], fc["without_forms"][reg]
            assert (w["n_items"], wo["n_items"]) == (nw, nwo)
            for blk in ("stated", "given"):
                assert w[blk]["k"] + wo[blk]["k"] == built[b][blk][reg]["k"]
                assert w[blk]["n"] + wo[blk]["n"] == built[b][blk][reg]["n"]
            assert ("verifier_rejections_recorded" in w) == (reg in ("P2", "P3"))
        assert fc["without_forms"]["P1"]["stated"]["ci_cluster_item"] is (
            None if nwo < 2 else fc["without_forms"]["P1"]["stated"]["ci_cluster_item"])


def test_form_coverage_reproduces_the_review_split(built):
    fc = built["A"]["by_form_coverage"]["with_forms"]
    assert (fc["P1"]["stated"]["k"], fc["P1"]["stated"]["n"]) == (39, 468)
    assert (fc["P1"]["given"]["k"], fc["P2"]["verifier_rejections_recorded"]["k"]) == (391, 42)
    fb = built["bs"]["by_form_coverage"]
    assert (fb["with_forms"]["P1"]["stated"]["k"], fb["with_forms"]["P1"]["stated"]["n"]) == (22, 324)
    assert fb["without_forms"]["P2"]["verifier_rejections_recorded"]["k"] == 3


def test_the_detector_without_forms_diagnostic_matches_the_review(built):
    for b, want in (("A", {"P0": (346, 104), "P1": (39, 2)}),
                    ("bs", {"P0": (243, 114), "P1": (22, 1)})):
        d = built[b]["detector_without_forms_diagnostic"]
        for reg, (k, k0) in want.items():
            assert (d[reg]["stated_with_forms"], d[reg]["stated_forms_removed"]) == (k, k0)


def test_the_stated_definition_says_what_the_detector_is_not():
    t = X.NOTES["lower_bound"]
    for phrase in ("Tier-1 lexical detector", "unframed statements",
                   "items without answer forms", "4.38",
                   "neither a strict lower bound nor a validated measure"):
        assert phrase in t, phrase


# ---- F3b: guardrail detail --------------------------------------------------------

def test_guardrail_detail_reproduces_the_appendix_counts(built):
    for b, (af, n2, k3, n3) in (("A", (41, 44, 9, 9)), ("bs", (17, 20, 3, 3))):
        gd = built[b]["guardrail_detail"]
        p2 = gd["first_candidate_rule"]["P2"]
        assert (p2["by_rule"]["answer_form"], p2["n_rejected"]) == (af, n2)
        assert p2["not_reproduced"] == 0
        ex = gd["p3_residual_blocks_exhausted"]
        assert (ex["k"], ex["n_blocked"], ex["max_rejections"]) == (k3, n3, 3)


# ---- F4: by construction ------------------------------------------------------------

def test_guarded_stated_is_flagged_as_by_construction(built):
    # P1C re-review N1 (ruling amended, P1F): `stated` is exactly 0 by
    # construction; its complement among given replies is exactly 1, and its
    # note says so instead of repeating "exactly 0" beside k = n.
    for b in ("A", "bs"):
        for reg in ("P2", "P3"):
            for blk in ("stated", "not_stated_among_given"):
                assert built[b][blk][reg]["by_construction"] is True
            assert "exactly 0 by construction" in built[b]["stated"][reg]["note"]
            e = built[b]["not_stated_among_given"][reg]
            assert e["note"] == X.NOTES["by_construction_complement"] == (
                "exactly 1 by construction: flagged is 0 by construction under "
                "P2/P3, so every given reply is unflagged")
            assert e["k"] == e["n"] and e["rate"] == 1.0
        for reg in ("P0", "P1"):
            assert "by_construction" not in built[b]["stated"][reg]


def test_a_stated_guarded_reply_is_a_contradiction():
    # P2/P3 deliveries passed this very detector; one that it flags means the
    # detector or the table is not the one that ran.
    with pytest.raises(ValueError, match="by construction"):
        X.summarise([row(regime="P2", response="The answer is tuple.")], ITEMS)


# ---- F6: provenance hashes ignore `generated_at` ------------------------------------------

def test_content_hash_ignores_generated_at(tmp_path):
    src = X.results_path("A", "ol")
    d = json.loads(src.read_text(encoding="utf-8"))
    d["generated_at"] = "2099-01-01T00:00:00+00:00"
    p = tmp_path / src.name
    p.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
    assert X.content_sha256(p) == X.content_sha256(src)
    d["coverage"]["fraction"] = 0.5
    p.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
    assert X.content_sha256(p) != X.content_sha256(src)


def test_a_rerun_that_changes_only_generated_at_leaves_the_artefact_current(
        tmp_path, monkeypatch, built):
    src = X.results_path("A", "ol")
    d = json.loads(src.read_text(encoding="utf-8"))
    d["generated_at"] = "2099-01-01T00:00:00+00:00"
    p = tmp_path / src.name
    p.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
    monkeypatch.setattr(X, "results_path", lambda bank, roster="ol": p)
    on_disk = json.loads(X.out_path("A", "ol").read_text(encoding="utf-8"))
    assert json.loads(json.dumps(X.build("A", "ol"))) == on_disk


# ---- F7: build stops on a failed cross-check -----------------------------------------------

@pytest.mark.parametrize("path,delta", [
    (("threshold_sensitivity", "0.25", "P1", "over"), 1),
    (("descriptives", "leak_adj", "regime", "P2", "mean"), 1e-12),
    (("guardrail", "P3", "blocked"), 1),
])
def test_build_refuses_a_tampered_results_file(tmp_path, monkeypatch, path, delta):
    src = X.results_path("A", "ol")
    d = json.loads(src.read_text(encoding="utf-8"))
    x = d
    for k in path[:-1]:
        x = x[k]
    x[path[-1]] += delta
    p = tmp_path / src.name
    p.write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setattr(X, "results_path", lambda bank, roster="ol": p)
    with pytest.raises(ValueError):
        X.build("A", "ol")


# ---- F8: the frozen-table note follows the count ---------------------------------------------

def test_the_frozen_table_note_depends_on_the_count(built):
    for b, n in (("A", 13), ("bs", 0)):
        f = built[b]["detector"]["frozen_table_not_used"]
        assert f["items_whose_answer_forms_differ_from_current"] == n
        assert f["note"] == (X.NOTES["frozen_table_differs"] if n
                             else X.NOTES["frozen_table_same"])


# ---- F9: the whole grid, and ties on the cut ---------------------------------------------------

def test_given_by_grid_covers_the_results_grid(built):
    keys = [RS.threshold_key(t) for t in RS.THRESHOLD_GRID]
    for b in ("A", "bs"):
        g = built[b]["given_by_grid"]
        assert list(g["all_items"]) == keys and list(g["with_forms"]) == keys
        res = json.loads(X.results_path(b, "ol").read_text(encoding="utf-8"))
        for t in keys:
            for reg in REGS:
                ts = res["threshold_sensitivity"][t][reg]
                assert (g["all_items"][t][reg]["k"], g["all_items"][t][reg]["n"]) == (
                    ts["over"], ts["n"])
    assert built["bs"]["given_by_grid"]["n_on_cut"]["P1"] == 22
    assert built["A"]["given_by_grid"]["n_on_cut"]["P3"] == 2


def test_n_on_cut_counts_replies_exactly_on_the_cut():
    rows = [row(leak=0.25), row(leak=0.25, rep=1), row(leak=0.26, rep=2)]
    g = X.summarise(rows, ITEMS)["given_by_grid"]
    assert g["n_on_cut"]["P1"] == 2 and g["n_on_cut"]["cut"] == "0.25"


def test_an_exhausted_block_used_every_rejection():
    # Mutant V4: on the real data every usable blocked P3 row has exactly
    # MAX_REJECTIONS rejections, so ">= 2" and "== 3" agree there. The
    # definition is "used all of them", and a synthetic row pins it.
    rows = [row(regime="P3", response=PL.BLOCKED_TEXT, blocked=True,
                rejections=X.MAX_REJECTIONS, rep=0),
            row(regime="P3", response=PL.BLOCKED_TEXT, blocked=True,
                rejections=X.MAX_REJECTIONS - 1, rep=1)]
    ex = X.guardrail_detail(rows, ITEMS)["p3_residual_blocks_exhausted"]
    assert (ex["k"], ex["n_blocked"], ex["max_rejections"]) == (1, 2, 3)


# ================================================= P1F: resampled contrasts
#
# A headline claim must pass the pre-specified Holm test AND survive item
# resampling (design revision 1, D15). The contrast is reduced to one number per
# item -- that item's difference of condition means -- and items are resampled
# (`analyse.item_bootstrap_ci`). In this balanced design the mean of the
# per-item differences IS the item fixed-effects estimate, so every estimate is
# checked against the results file and a disagreement stops the build.

SOLS = ("S0", "S2", "S3")
CONTRAST_KEYS = ["P1-P0", "P2-P1", "P3-P2", "P3-P0", "P3-P1",
                 "S2-S0", "S3-S0", "S3-S2"]
INTERACTION_KEYS = ["(P1-P0)x(BS-A)", "(P2-P0)x(BS-A)", "(P3-P0)x(BS-A)"]
ENTRY = {"estimate", "ci_cluster_item", "n_items", "B", "seed"}


def _design_rows(items, value, regs=REGS, sols=SOLS, reps=2):
    """A balanced synthetic design: every item x regime x style x replicate."""
    out = []
    for it in items:
        for reg in regs:
            for sol in sols:
                for rep in range(reps):
                    r = row(regime=reg, sol=sol, leak=value(it, reg, sol, rep),
                            rep=rep)
                    r["item_id"] = it
                    r["cell_key"] = f"{it}|g/x:cloud|{sol}|{reg}|{rep}"
                    out.append(r)
    return out


def _f(it, reg, sol, rep):
    """Items differ in level AND in their P1-P0 effect, so per-item
    differences vary and an interval has something to resample."""
    k = int(it.lstrip("abI")) if it.lstrip("abI").isdigit() else 0
    eff = {"P0": 0.0, "P1": -0.05 * (k % 4), "P2": -0.03 * (k % 3), "P3": -0.02 * k}
    return round(0.2 + 0.05 * k + eff[reg] + {"S0": 0, "S2": 0.01, "S3": -0.01}[sol]
                 + 0.001 * rep, 6)


def _mean(xs):
    return sum(xs) / len(xs)


# ---- REQ-1: per-bank contrasts ----------------------------------------------

def test_per_item_contrast_is_each_items_difference_of_condition_means():
    rows = _design_rows(["I1", "I2", "I3"], _f)
    got = X.per_item_contrast(rows, "regime", "P1", "P0")
    for it in ("I1", "I2", "I3"):
        a = _mean([r["leak_adj"] for r in rows if r["item_id"] == it and r["regime"] == "P1"])
        b = _mean([r["leak_adj"] for r in rows if r["item_id"] == it and r["regime"] == "P0"])
        assert got[it] == pytest.approx(a - b, abs=1e-15)
    s = X.per_item_contrast(rows, "solicitation_key", "S3", "S2")
    assert s["I2"] == pytest.approx(-0.02, abs=1e-12)


def test_an_item_missing_a_condition_is_refused():
    rows = [r for r in _design_rows(["I1", "I2"], _f)
            if not (r["item_id"] == "I2" and r["regime"] == "P1")]
    with pytest.raises(ValueError, match="I2"):
        X.per_item_contrast(rows, "regime", "P1", "P0")


def test_an_unbalanced_design_is_refused():
    # The mean of per-item differences equals the fixed-effects estimate only
    # when every item holds the same number of replies in every condition.
    rows = _design_rows(["I1", "I2"], _f)
    rows.pop(5)
    with pytest.raises(ValueError, match="balanced"):
        X.contrasts_ci(rows)


def test_contrast_intervals_come_from_the_shared_item_bootstrap():
    rows = _design_rows(["I1", "I2", "I3", "I4", "I5"], _f)
    c = X.contrasts_ci(rows)
    assert list(c) == CONTRAST_KEYS
    for key, factor, a, b in X.CONTRASTS:
        per = X.per_item_contrast(rows, factor, a, b)
        e = c[key]
        assert set(e) == ENTRY
        assert e["estimate"] == AN.item_statistic(per)
        assert e["ci_cluster_item"] == list(AN.item_bootstrap_ci(per))
        assert (e["n_items"], e["B"], e["seed"]) == (5, 10000, 20261103)


def test_contrast_intervals_resample_items_not_replies():
    # Per-reply resampling of the same contrast would be several times
    # narrower: 5 items, 24 replies per item and condition.
    rows = _design_rows(["I1", "I2", "I3", "I4", "I5"], _f, reps=8)
    lo, hi = X.contrasts_ci(rows)["P1-P0"]["ci_cluster_item"]
    per = X.per_item_contrast(rows, "regime", "P1", "P0")
    assert (lo, hi) == AN.item_bootstrap_ci(per)
    assert hi - lo > 0.05


def _results_doc(rows, nd=6):
    """A results file shaped like results_bank*_ol.json, rounded like it."""
    c = {k: {"estimate": round(AN.item_statistic(X.per_item_contrast(rows, f, a, b)), nd)}
         for k, f, a, b in X.CONTRASTS}
    fam = {"R2_mitigation": {"contrasts": {k: c[k] for k in
                                           ("P1-P0", "P2-P1", "P3-P2", "P3-P0")}},
           "R3_solicitation": {"contrasts": {k: c[k] for k in
                                             ("S2-S0", "S3-S0", "S3-S2")}}}
    desc = AN.describe_by(rows, "regime", "leak_adj")
    return {"families": fam, "descriptives": {"leak_adj": {"regime": desc}}}


def test_the_contrast_cross_check_passes_on_the_rounded_results():
    rows = _design_rows(["I1", "I2", "I3"], _f)
    chk = X.contrast_cross_check(X.contrasts_ci(rows), _results_doc(rows))
    assert chk["status"] == "passed" and chk["tolerance"] == 1e-6
    assert len(chk["checked_equal"]) == 8


@pytest.mark.parametrize("path,key", [
    (("families", "R2_mitigation", "contrasts", "P1-P0", "estimate"), "P1-P0"),
    (("families", "R2_mitigation", "contrasts", "P3-P2", "estimate"), "P3-P2"),
    (("families", "R3_solicitation", "contrasts", "S3-S2", "estimate"), "S3-S2"),
    (("descriptives", "leak_adj", "regime", "P3", "mean"), "P3-P1"),
])
def test_the_contrast_cross_check_names_each_tampered_estimate(path, key):
    rows = _design_rows(["I1", "I2", "I3"], _f)
    doc = _results_doc(rows)
    d = doc
    for k in path[:-1]:
        d = d[k]
    d[path[-1]] += 2e-6
    with pytest.raises(ValueError, match=re.escape(f"contrasts_ci_cluster_item[{key!r}]")):
        X.contrast_cross_check(X.contrasts_ci(rows), doc)


def test_the_contrast_cross_check_refuses_a_missing_results_contrast():
    rows = _design_rows(["I1", "I2", "I3"], _f)
    doc = _results_doc(rows)
    del doc["families"]["R3_solicitation"]["contrasts"]["S2-S0"]
    with pytest.raises(ValueError, match="S2-S0"):
        X.contrast_cross_check(X.contrasts_ci(rows), doc)


@pytest.mark.parametrize("path", [
    ("families", "R2_mitigation", "contrasts", "P2-P1", "estimate"),
    ("families", "R3_solicitation", "contrasts", "S2-S0", "estimate"),
])
def test_build_refuses_a_tampered_contrast_estimate(tmp_path, monkeypatch, path):
    src = X.results_path("A", "ol")
    d = json.loads(src.read_text(encoding="utf-8"))
    x = d
    for k in path[:-1]:
        x = x[k]
    x[path[-1]] += 1e-5
    p = tmp_path / src.name
    p.write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setattr(X, "results_path", lambda bank, roster="ol": p)
    with pytest.raises(ValueError, match=re.escape(f"[{path[-2]!r}]")):
        X.build("A", "ol")


def test_the_real_contrasts_match_the_results_files(built):
    for b, n in (("A", 14), ("bs", 27)):
        d = built[b]
        res = json.loads(X.results_path(b, "ol").read_text(encoding="utf-8"))
        c = d["contrasts_ci_cluster_item"]
        assert list(c) == CONTRAST_KEYS
        for key, e in c.items():
            assert set(e) == ENTRY
            assert (e["n_items"], e["B"], e["seed"]) == (n, 10000, 20261103)
            lo, hi = e["ci_cluster_item"]
            assert lo <= e["estimate"] <= hi
            fam = ("R2_mitigation" if key.startswith("P") else "R3_solicitation")
            if key == "P3-P1":
                rg = res["descriptives"]["leak_adj"]["regime"]
                want = rg["P3"]["mean"] - rg["P1"]["mean"]
            else:
                want = res["families"][fam]["contrasts"][key]["estimate"]
            assert abs(e["estimate"] - want) <= 1e-6, (b, key)
        assert d["contrasts_cross_check"]["status"] == "passed"
        assert "item" in d["intervals"]["contrasts_ci_cluster_item"]


def test_the_real_contrast_intervals_are_the_per_item_bootstrap(built):
    import human_validation as hv_
    for b in ("A", "bs"):
        rows = hv_.join_generate_solve(b, "ol")
        per = X.per_item_contrast(rows, "regime", "P3", "P1")
        e = built[b]["contrasts_ci_cluster_item"]["P3-P1"]
        assert e["ci_cluster_item"] == list(AN.item_bootstrap_ci(per))


# ---- REQ-2: pooled contrasts and the bank x condition interaction -----------

def _two_banks():
    a = _design_rows(["a1", "a2", "a3"], _f)
    bb = _design_rows([f"b{i}" for i in range(1, 8)], _f)
    return {"A": a, "BS": bb}


def test_pooled_contrasts_resample_items_within_bank():
    rb = _two_banks()
    c = X.pooled_contrasts_ci(rb)
    assert list(c) == CONTRAST_KEYS
    for key, factor, a, b in X.CONTRASTS:
        per, strata = {}, {}
        for bank, rows in rb.items():
            for it, v in X.per_item_contrast(rows, factor, a, b).items():
                per[(bank, it)] = v
                strata[(bank, it)] = bank
        e = c[key]
        assert e["estimate"] == AN.item_statistic(per, strata)
        assert e["ci_cluster_item"] == list(AN.item_bootstrap_ci(per, strata))
        assert (e["n_items"], e["n_items_by_bank"]) == (10, {"A": 3, "BS": 7})
        assert (e["B"], e["seed"]) == (10000, 20261103)


def test_the_interaction_is_bank_b_minus_bank_a():
    # Bank A: P1 equals P0 on every item; Bank B: P1 is 0.5 above P0. The
    # interaction (P1-P0) x (BS-A) is +0.5, never -0.5.
    def fa(it, reg, sol, rep):
        return 0.3 + 0.01 * int(it[1:])

    def fb(it, reg, sol, rep):
        return 0.2 + 0.01 * int(it[1:]) + (0.5 if reg == "P1" else 0.0)
    rb = {"A": _design_rows(["a1", "a2", "a3"], fa),
          "BS": _design_rows(["b1", "b2", "b3", "b4"], fb)}
    i = X.interactions_ci(rb)
    assert list(i) == INTERACTION_KEYS
    assert i["(P1-P0)x(BS-A)"]["estimate"] == pytest.approx(0.5, abs=1e-12)
    assert i["(P1-P0)x(BS-A)"]["ci_cluster_item"] == pytest.approx([0.5, 0.5], abs=1e-12)
    assert i["(P2-P0)x(BS-A)"]["estimate"] == pytest.approx(0.0, abs=1e-12)


def test_the_interaction_interval_is_the_weighted_item_bootstrap():
    rb = _two_banks()
    i = X.interactions_ci(rb)
    for key, a in zip(INTERACTION_KEYS, ("P1", "P2", "P3")):
        per, strata = {}, {}
        for bank, rows in rb.items():
            for it, v in X.per_item_contrast(rows, "regime", a, "P0").items():
                per[(bank, it)] = v
                strata[(bank, it)] = bank
        w = {"A": -1.0, "BS": 1.0}
        e = i[key]
        assert e["estimate"] == AN.item_statistic(per, strata, w)
        assert e["ci_cluster_item"] == list(AN.item_bootstrap_ci(per, strata, w))
        assert (e["n_items"], e["n_items_by_bank"]) == (10, {"A": 3, "BS": 7})


def _pooled_doc(rb, nd=6):
    c = X.pooled_contrasts_ci(rb)
    i = X.interactions_ci(rb)
    fam = {"R2_mitigation": {"contrasts": {k: {"estimate": round(c[k]["estimate"], nd)}
                                           for k in ("P1-P0", "P2-P1", "P3-P2", "P3-P0")}},
           "R3_solicitation": {"contrasts": {k: {"estimate": round(c[k]["estimate"], nd)}
                                             for k in ("S2-S0", "S3-S0", "S3-S2")}},
           "R4_robustness": {"interaction": {
               k.replace(")x(", ") x ("): {"estimate": round(v["estimate"], nd)}
               for k, v in i.items()}}}
    rows = [r for rs in rb.values() for r in rs]
    desc = AN.describe_by(rows, "regime", "leak_adj")
    return c, i, {"families": fam, "descriptives": {"leak_adj": {"regime": desc}}}


def test_the_pooled_cross_check_passes_on_the_rounded_results():
    c, i, doc = _pooled_doc(_two_banks())
    chk = X.pooled_cross_check(c, i, doc)
    assert chk["status"] == "passed" and len(chk["checked_equal"]) == 11


@pytest.mark.parametrize("path,match", [
    (("families", "R2_mitigation", "contrasts", "P2-P1", "estimate"), "contrasts_ci_cluster_item['P2-P1']"),
    (("families", "R3_solicitation", "contrasts", "S3-S0", "estimate"), "contrasts_ci_cluster_item['S3-S0']"),
    (("families", "R4_robustness", "interaction", "(P2-P0) x (BS-A)", "estimate"),
     "interaction_ci_cluster_item['(P2-P0)x(BS-A)']"),
    (("descriptives", "leak_adj", "regime", "P1", "mean"), "contrasts_ci_cluster_item['P3-P1']"),
])
def test_the_pooled_cross_check_names_each_tampered_estimate(path, match):
    c, i, doc = _pooled_doc(_two_banks())
    d = doc
    for k in path[:-1]:
        d = d[k]
    d[path[-1]] += 2e-6
    with pytest.raises(ValueError, match=re.escape(match)):
        X.pooled_cross_check(c, i, doc)


@pytest.fixture(scope="module")
def built_pooled():
    return X.build_pooled(("A", "bs"), "ol")


@pytest.mark.parametrize("path,match", [
    (("families", "R2_mitigation", "contrasts", "P3-P0", "estimate"), "P3-P0"),
    (("families", "R3_solicitation", "contrasts", "S3-S2", "estimate"), "S3-S2"),
    (("families", "R4_robustness", "interaction", "(P1-P0) x (BS-A)", "estimate"),
     "(P1-P0)x(BS-A)"),
])
def test_build_pooled_refuses_a_tampered_pooled_results_file(tmp_path, monkeypatch,
                                                              path, match):
    src = X.pooled_results_path(("A", "bs"), "ol")
    d = json.loads(src.read_text(encoding="utf-8"))
    x = d
    for k in path[:-1]:
        x = x[k]
    x[path[-1]] += 1e-5
    p = tmp_path / src.name
    p.write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setattr(X, "pooled_results_path", lambda banks, roster="ol": p)
    with pytest.raises(ValueError, match=re.escape(match)):
        X.build_pooled(("A", "bs"), "ol")


def test_the_real_pooled_artefact_matches_the_pooled_results_file(built_pooled):
    d = built_pooled
    res = json.loads(X.pooled_results_path(("A", "bs"), "ol").read_text(encoding="utf-8"))
    assert d["banks"] == ["A", "BS"] and d["roster"] == "ol"
    c, i = d["contrasts_ci_cluster_item"], d["interaction_ci_cluster_item"]
    assert list(c) == CONTRAST_KEYS and list(i) == INTERACTION_KEYS
    for key, e in c.items():
        assert set(e) == ENTRY | {"n_items_by_bank"}
        assert (e["n_items"], e["n_items_by_bank"]) == (41, {"A": 14, "BS": 27})
        lo, hi = e["ci_cluster_item"]
        assert lo <= e["estimate"] <= hi
        if key == "P3-P1":
            rg = res["descriptives"]["leak_adj"]["regime"]
            want = rg["P3"]["mean"] - rg["P1"]["mean"]
        else:
            fam = "R2_mitigation" if key.startswith("P") else "R3_solicitation"
            want = res["families"][fam]["contrasts"][key]["estimate"]
        assert abs(e["estimate"] - want) <= 1e-6, key
    for key, e in i.items():
        assert set(e) == ENTRY | {"n_items_by_bank"}
        want = res["families"]["R4_robustness"]["interaction"][
            key.replace(")x(", ") x (")]["estimate"]
        assert abs(e["estimate"] - want) <= 1e-6, key
        lo, hi = e["ci_cluster_item"]
        assert lo <= e["estimate"] <= hi
    assert d["cross_check"]["status"] == "passed"


def test_the_pooled_artefact_on_disk_is_current(built_pooled):
    on_disk = json.loads(X.pooled_out_path(("A", "bs"), "ol").read_text(encoding="utf-8"))
    assert on_disk == json.loads(json.dumps(built_pooled))


def test_the_pooled_artefact_records_content_hashes_of_its_inputs(built_pooled):
    import human_validation as hv_
    want = {f"{s}_bank{b}_ol.jsonl" for s in ("generate", "solve") for b in ("A", "BS")}
    want.add("results_pooled_A-BS_ol.json")
    ins = built_pooled["inputs"]
    assert set(ins) == want and built_pooled["inputs_hash_rule"] == "content_sha256"
    for name, h in ins.items():
        p = (hv_.RUNS / name) if name.endswith(".jsonl") else (X.C.ROOT / "data" / name)
        assert h == X.content_sha256(p), name


def test_the_pooled_artefact_carries_no_bank_b_text(built_pooled):
    import config as C
    import human_validation as hv_
    d = json.loads(X.pooled_out_path(("A", "bs"), "ol").read_text(encoding="utf-8"))
    bank = json.loads(Path(X.item_table("bs")[1]).read_text(encoding="utf-8"))
    opts = {_norm(o) for it in bank["items"] for o in it["options"]}
    stems = {_norm(it["stem"]) for it in bank["items"]}
    # Our own design ids with parentheses or spaces, as exact strings: the
    # interaction keys and the two paths each interaction is checked between.
    allowed = (set(X.NOTES.values()) | set(INTERACTION_KEYS)
               | {f"interaction_ci_cluster_item.{k}.estimate" for k in INTERACTION_KEYS}
               | {f"families.R4_robustness.interaction.{k.replace(')x(', ') x (')}.estimate"
                  for k in INTERACTION_KEYS}
               | {hv_.cluster_method(0)["method"]})
    for key, s in _strings(d):
        if s in allowed:
            continue
        assert _SHA.match(s) or _CODE.match(s), f"free text under {key!r}: {s[:30]!r}"
        # "A": the Bank A id (an entry of `banks`, a key of n_rows / n_items /
        # n_items_by_bank). It equals a one-letter Bank B option once
        # case-folded; it is our design id, not their text.
        if s in _OWN_CODES_EQUAL_TO_AN_OPTION | {"A"}:
            continue
        assert not _is_bank_b_text(_norm(s), opts, stems), f"Bank B text under {key!r}"
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ops"))
    import release_scan
    hits, n_prot, _ = release_scan.scan([X.pooled_out_path(("A", "bs"), "ol")])
    assert n_prot > 0 and hits == []


def test_main_writes_both_banks_and_the_pooled_file(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "out_path",
                        lambda bank, roster="ol": tmp_path / f"e_{bank.upper()}.json")
    monkeypatch.setattr(X, "pooled_out_path",
                        lambda banks, roster="ol": tmp_path / "e_pooled.json")
    assert X.main([]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "e_A.json", "e_BS.json", "e_pooled.json"]
    for p in tmp_path.iterdir():
        p.unlink()
    assert X.main(["--bank", "A"]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["e_A.json"]


# ---- REQ-3: recoverability by delivery type ---------------------------------

DELIVERY = ["P2_blocked", "P2_delivered", "P3_blocked", "P3_repaired_delivered",
            "P3_first_pass_delivered"]


def _delivery_rows():
    B_ = PL.BLOCKED_TEXT
    rows = [row(regime="P0", leak=0.9, rep=0), row(regime="P1", leak=0.8, rep=1),
            row(regime="P2", response=B_, blocked=True, rejections=1, leak=0.0, rep=2),
            row(regime="P2", response=B_, blocked=True, rejections=1, leak=0.3, rep=3),
            row(regime="P2", leak=0.6, rep=4),
            row(regime="P3", response=B_, blocked=True, rejections=3, leak=0.1, rep=5),
            row(regime="P3", rejections=1, leak=0.7, rep=6),
            row(regime="P3", rejections=2, leak=0.2, rep=7),
            row(regime="P3", leak=0.5, rep=8)]
    for i, r in enumerate(rows):            # two items, so intervals exist
        if i % 2:
            r["item_id"] = "I2"
            r["cell_key"] = r["cell_key"].replace("I1|", "I2|", 1)
    return rows, {"I1": ITEM, "I2": dict(ITEM, id="I2")}


def test_leak_by_delivery_partitions_the_guarded_rows():
    rows, items = _delivery_rows()
    ld = X.guardrail_detail(rows, items)["leak_by_delivery"]
    assert [k for k in ld if k != "note"] == DELIVERY
    assert ld["note"] == X.NOTES["leak_by_delivery"]
    want = {"P2_blocked": (2, 0.15, 1, 2), "P2_delivered": (1, 0.6, 1, 1),
            "P3_blocked": (1, 0.1, 0, 1), "P3_repaired_delivered": (2, 0.45, 1, 2),
            "P3_first_pass_delivered": (1, 0.5, 1, 1)}
    for k, (n, m, g, ni) in want.items():
        e = ld[k]
        assert set(e) == {"n", "n_items", "mean_leak_adj", "given_k",
                          "mean_ci_cluster_item"}
        assert (e["n"], e["given_k"], e["n_items"]) == (n, g, ni), k
        assert e["mean_leak_adj"] == pytest.approx(m, abs=1e-12), k
    assert sum(ld[k]["n"] for k in DELIVERY) == 7          # P0/P1 rows excluded
    # Two items in a group -> an interval; one item -> none.
    assert ld["P2_blocked"]["mean_ci_cluster_item"] == HV.cluster_ci(
        [0.0, 0.3], ["I1", "I2"], "mean")
    assert ld["P2_delivered"]["mean_ci_cluster_item"] is None


def test_a_delivery_table_that_does_not_partition_is_refused():
    rows, _ = _delivery_rows()
    dropped = {k: v for k, v in X.DELIVERY_TYPES.items() if k != "P3_first_pass_delivered"}
    with pytest.raises(ValueError, match="partition"):
        X.leak_by_delivery(rows, dropped)
    overlap = dict(X.DELIVERY_TYPES, P3_any=("P3", lambda r: True))
    with pytest.raises(ValueError, match="partition"):
        X.leak_by_delivery(rows, overlap)


def test_a_rejected_p2_reply_that_was_delivered_is_refused():
    # P2 has no repair step: a rejected first candidate is a blocked turn.
    rows = [row(regime="P2", rejections=1, leak=0.4)]
    with pytest.raises(ValueError, match="no repair"):
        X.leak_by_delivery(rows)


def test_an_empty_delivery_group_has_no_mean():
    rows = [row(regime="P3", leak=0.4), row(regime="P3", leak=0.2, rep=1)]
    ld = X.leak_by_delivery(rows)
    for k in ("P2_blocked", "P2_delivered", "P3_blocked", "P3_repaired_delivered"):
        assert ld[k] == {"n": 0, "n_items": 0, "mean_leak_adj": None, "given_k": 0,
                         "mean_ci_cluster_item": None}, k
    assert ld["P3_first_pass_delivered"]["n"] == 2
    assert ld["P3_first_pass_delivered"]["n_items"] == 1


#: design/evidence/mechanism_check_out.json (design reviser, 2026-11-08): n,
#: mean and the count at or above 0.25, per delivery type. Counts exact,
#: means to 1e-6 (brief REQ-3).
MECHANISM = {
    "A": {"P2_blocked": (44, 0.03787878787878787, 7),
          "P2_delivered": (460, 0.6013586956521739, 389),
          "P3_blocked": (9, 0.14814814814814817, 1),
          "P3_repaired_delivered": (38, 0.5307017543859649, 36),
          "P3_first_pass_delivered": (457, 0.5961889132020423, 385)},
    "bs": {"P2_blocked": (20, 0.05208333333333333, 2),
           "P2_delivered": (952, 0.43553046218487396, 647),
           "P3_blocked": (3, 0.05555555555555555, 0),
           "P3_repaired_delivered": (27, 0.6527777777777778, 22),
           "P3_first_pass_delivered": (942, 0.4428963198867658, 646)},
}


def test_the_real_leak_by_delivery_reproduces_the_mechanism_check(built):
    for b, want in MECHANISM.items():
        ld = built[b]["guardrail_detail"]["leak_by_delivery"]
        for k, (n, m, g) in want.items():
            assert (ld[k]["n"], ld[k]["given_k"]) == (n, g), (b, k)
            assert abs(ld[k]["mean_leak_adj"] - m) <= 1e-6, (b, k)


def test_the_real_leak_by_delivery_agrees_with_the_as_run_record(built):
    for b in ("A", "bs"):
        d = built[b]
        ld, g = d["guardrail_detail"]["leak_by_delivery"], d["as_run_guardrail"]
        assert ld["P2_blocked"]["n"] == g["P2"]["blocked"]
        assert ld["P2_blocked"]["n"] + ld["P2_delivered"]["n"] == g["P2"]["n"]
        assert ld["P3_blocked"]["n"] == g["P3"]["blocked"]
        assert ld["P3_repaired_delivered"]["n"] == g["P3"]["repaired_delivered"]
        assert sum(ld[k]["n"] for k in DELIVERY if k.startswith("P3")) == g["P3"]["n"]
        for reg in ("P2", "P3"):
            parts = [k for k in DELIVERY if k.startswith(reg)]
            tot = sum(ld[k]["n"] * ld[k]["mean_leak_adj"] for k in parts)
            assert tot / g[reg]["n"] == pytest.approx(d["given"][reg]["mean"], abs=1e-12)
            assert sum(ld[k]["given_k"] for k in parts) == d["given"][reg]["k"]


def test_build_checks_leak_by_delivery_against_the_as_run_record():
    ld = {"P2_blocked": {"n": 1}, "P2_delivered": {"n": 3}, "P3_blocked": {"n": 0},
          "P3_repaired_delivered": {"n": 1}, "P3_first_pass_delivered": {"n": 2}}
    guard = {"P2": {"n": 4, "blocked": 1}, "P3": {"n": 3, "blocked": 0,
                                                  "repaired_delivered": 1}}
    X.delivery_cross_check(ld, guard)
    guard["P3"]["repaired_delivered"] = 2
    with pytest.raises(ValueError, match="P3_repaired_delivered"):
        X.delivery_cross_check(ld, guard)


# ---- P1C re-review N2: the few-clusters caveat lives in the artefacts --------

def test_the_intervals_note_carries_the_undercoverage_caveat(built):
    phrase = "approximate: a percentile bootstrap over 9–27 items tends to undercover"
    for b in ("A", "bs"):
        assert phrase in built[b]["intervals"]["note"]
        assert built[b]["intervals"]["note"].startswith(
            "ci_cluster_item is null where a block holds fewer than two items")


def test_build_refuses_delivery_counts_that_disagree_with_the_as_run_record(monkeypatch):
    # Mutation P3 (P1F): on the real data the two counts agree, so nothing
    # showed whether `build` compares them at all. A table that still
    # partitions the rows but reads them wrongly (repaired and first pass
    # swapped) must stop the build.
    t = dict(X.DELIVERY_TYPES)
    t["P3_repaired_delivered"], t["P3_first_pass_delivered"] = (
        X.DELIVERY_TYPES["P3_first_pass_delivered"],
        X.DELIVERY_TYPES["P3_repaired_delivered"])
    monkeypatch.setattr(X, "DELIVERY_TYPES", t)
    with pytest.raises(ValueError, match="as_run_guardrail"):
        X.build("A", "ol")


# ================================================= P1H: P1F review Minors 1, 3-5
#
# Minor 4: one spelling of "blocked". `_blocked` is the module's only reading
# of the recorded `blocked` field as a predicate; every site that decides
# something on it calls `_blocked`, so a change to the rule reaches them all.

def _sentinel_blocked_rows():
    """Blocked turns marked ONLY by a sentinel field, so a site that still reads
    `blocked` itself sees them as delivered and disagrees with `_blocked`."""
    B_ = PL.BLOCKED_TEXT
    rows = [row(regime="P2", response=B_, rejections=1, leak=0.0, rep=0),
            row(regime="P2", leak=0.6, rep=1),
            row(regime="P3", response=B_, rejections=X.MAX_REJECTIONS, leak=0.1, rep=2),
            row(regime="P3", rejections=1, leak=0.7, rep=3),
            row(regime="P3", leak=0.5, rep=4)]
    for r in rows:
        r["blocked"] = False
        r["sentinel_blocked"] = r["response"] == B_
    return rows


def test_every_blocked_decision_goes_through_one_rule(monkeypatch):
    # Swap the ONE rule for a sentinel (as for the cut, above). A site with its
    # own copy of the rule would not move, and its count would disagree.
    monkeypatch.setattr(X, "_blocked", lambda r: bool(r.get("sentinel_blocked")))
    rows = _sentinel_blocked_rows()
    s = X.summarise(rows, ITEMS)                       # delivered_text, detect
    assert s["stated"]["P2"]["n_blocked_counted_not_stated"] == 1
    assert s["stated"]["P3"]["n_blocked_counted_not_stated"] == 1
    g = X.as_run_guardrail(rows)
    assert (g["P2"]["blocked"], g["P3"]["blocked"]) == (1, 1)
    assert g["P3"]["repaired_delivered"] == 1          # the blocked P3 row is not one
    gd = X.guardrail_detail(rows, ITEMS)
    ex = gd["p3_residual_blocks_exhausted"]
    assert (ex["k"], ex["n_blocked"]) == (1, 1)
    ld = gd["leak_by_delivery"]
    assert (ld["P2_blocked"]["n"], ld["P3_blocked"]["n"]) == (1, 1)
    X.delivery_cross_check(ld, g)
    # `detect` refuses a firing on a turn the ONE rule calls blocked.
    item = dict(ITEM, answer_forms=["blocked"])
    with pytest.raises(ValueError, match="detector fires on the blocked"):
        X.detect(rows[0], {"I1": item})


def test_blocked_is_read_from_the_record_in_one_place():
    # Asked of the AST: `.get("blocked")` may appear in `_blocked` and, as the
    # raw recorded value, inside an error message (an f-string); nowhere else.
    tree = ast.parse(SRC)
    parent = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}

    def inside(n, kind, name=None):
        while n in parent:
            n = parent[n]
            if isinstance(n, kind) and (name is None or n.name == name):
                return True
        return False
    sites = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "get" and n.args
             and isinstance(n.args[0], ast.Constant) and n.args[0].value == "blocked"]
    assert sites, "the rule reads the field somewhere"
    stray = [n.lineno for n in sites
             if not inside(n, ast.FunctionDef, "_blocked") and not inside(n, ast.JoinedStr)]
    assert stray == [], f"lines {stray} read `blocked` instead of calling _blocked"
    # And `_blocked` sits beside `_rejected`, the other recorded-field rule.
    defs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert abs(defs.index("_blocked") - defs.index("_rejected")) == 1


# Minor 3: every delivery group says how many items it rests on.

#: Distinct items per delivery group, counted independently of this module
#: (`scratch/P1H/independent_n_items.py`: the joined rows, classified from the
#: raw recorded fields). Five are the P1F review's own (Minor 3); all ten agree.
DELIVERY_N_ITEMS = {
    "A": {"P2_blocked": 7, "P2_delivered": 14, "P3_blocked": 2,
          "P3_repaired_delivered": 7, "P3_first_pass_delivered": 14},
    "bs": {"P2_blocked": 4, "P2_delivered": 27, "P3_blocked": 1,
           "P3_repaired_delivered": 6, "P3_first_pass_delivered": 27},
}


def test_the_real_delivery_groups_carry_their_item_counts(built):
    for b, want in DELIVERY_N_ITEMS.items():
        ld = built[b]["guardrail_detail"]["leak_by_delivery"]
        for k, ni in want.items():
            assert ld[k]["n_items"] == ni, (b, k)
            # The interval exists exactly when there are two items to resample.
            assert (ld[k]["mean_ci_cluster_item"] is None) == (ni < 2), (b, k)
    rows = __import__("human_validation").join_generate_solve("A", "ol")
    ld = built["A"]["guardrail_detail"]["leak_by_delivery"]
    for k, (reg, pred) in X.DELIVERY_TYPES.items():
        ids = {r["item_id"] for r in rows if r["regime"] == reg and pred(r)}
        assert ld[k]["n_items"] == len(ids), k


def test_the_delivery_note_says_why_an_interval_is_null_or_wide():
    t = X.NOTES["leak_by_delivery"]
    for phrase in ("n_items", "null where a group holds fewer than two items",
                   "rest on few items"):
        assert phrase in t, phrase


# Minor 5 (the P1F reviewer's mutants O6 and O7): two test gaps.

def test_a_delivery_interval_is_clustered_by_item_not_by_reply():
    # Mutant O6 clustered by reply (cell key) and survived every unit test: the
    # synthetic groups above hold one row per item, where the two coincide.
    # Here P2_blocked holds two replies of I1 and one of I2, and P3_blocked two
    # replies of I1 only. Every row has its own cell key.
    B_ = PL.BLOCKED_TEXT
    spec = [("P2", "I1", 0.0, True, 1), ("P2", "I1", 0.2, True, 1),
            ("P2", "I2", 0.9, True, 1), ("P2", "I2", 0.5, False, 0),
            ("P3", "I1", 0.1, True, 3), ("P3", "I1", 0.6, True, 3),
            ("P3", "I2", 0.4, False, 0)]
    rows = []
    for i, (reg, iid, v, blk, rej) in enumerate(spec):
        r = row(regime=reg, leak=v, rep=i, blocked=blk, rejections=rej,
                response=B_ if blk else "Think about mutability.")
        r["item_id"] = iid
        r["cell_key"] = r["cell_key"].replace("I1|", f"{iid}|", 1)
        rows.append(r)
    assert len({r["cell_key"] for r in rows}) == len(rows)
    ld = X.leak_by_delivery(rows)

    def group(reg, blk):
        return [r for r in rows if r["regime"] == reg and r["blocked"] is blk]
    p2b = group("P2", True)
    vals = [r["leak_adj"] for r in p2b]
    by_item = HV.cluster_ci(vals, [r["item_id"] for r in p2b], "mean")
    by_reply = HV.cluster_ci(vals, [r["cell_key"] for r in p2b], "mean")
    assert ld["P2_blocked"]["n_items"] == 2 and ld["P2_blocked"]["n"] == 3
    assert ld["P2_blocked"]["mean_ci_cluster_item"] == by_item
    assert by_item != by_reply
    # One item behind two replies: no item-level interval, although a
    # reply-level one would exist.
    p3b = group("P3", True)
    assert ld["P3_blocked"]["n_items"] == 1 and ld["P3_blocked"]["n"] == 2
    assert ld["P3_blocked"]["mean_ci_cluster_item"] is None
    assert HV.cluster_ci([r["leak_adj"] for r in p3b],
                         [r["cell_key"] for r in p3b], "mean") is not None


def _unbalanced_by_style(items):
    """Balanced by condition, not by request style: one P1 reply of the first
    item moves from S0 to S2 (mutant O7 checked the condition only)."""
    rows = _design_rows(items, _f)
    r = next(r for r in rows if r["item_id"] == items[0] and r["regime"] == "P1"
             and r["solicitation_key"] == "S0")
    r["solicitation_key"] = "S2"
    r["cell_key"] = r["cell_key"].replace("|S0|", "|S2|")
    X._assert_balanced(rows, "regime")         # still balanced by condition
    return rows


def test_a_design_unbalanced_only_by_request_style_is_refused():
    with pytest.raises(ValueError, match=r"^solicitation_key: .*not balanced"):
        X.contrasts_ci(_unbalanced_by_style(["I1", "I2"]))


@pytest.mark.parametrize("bad", ["A", "BS"])
def test_a_pooled_design_unbalanced_only_by_request_style_is_refused(bad):
    rb = {"A": _design_rows(["a1", "a2", "a3"], _f),
          "BS": _design_rows(["b1", "b2", "b3"], _f)}
    rb[bad] = _unbalanced_by_style([f"{bad[0].lower()}{i}" for i in (1, 2, 3)])
    with pytest.raises(ValueError, match=r"^solicitation_key: .*not balanced"):
        X.pooled_contrasts_ci(rb)


# Minor 1: endpoints carry Monte Carlo error; the notes say so, with no endpoint.

def test_every_intervals_note_carries_the_monte_carlo_caveat(built, built_pooled):
    import inspect
    mc = X.NOTES["monte_carlo"]
    assert "Monte Carlo error at B = 10,000" in mc
    assert "within about 0.002 of zero is borderline" in mc
    # B in the sentence is the B both bootstraps ran with, not a typed copy.
    for fn in (AN.item_bootstrap_ci, AN.cluster_bootstrap_ci):
        assert f"B = {inspect.signature(fn).parameters['B'].default:,}" in mc
    notes = {"A": built["A"]["intervals"]["note"], "bs": built["bs"]["intervals"]["note"],
             "pooled": built_pooled["intervals"]["note"]}
    for name, note in notes.items():
        assert note.endswith(" " + mc), name
        # The few-clusters caveat stays where it was; the sentence is appended.
        assert X._UNDERCOVER in note, name
        # No endpoint value: the only numbers are the caveats' own.
        nums = set(re.findall(r"\d[\d,]*(?:\.\d+)?", note))
        assert nums <= {"9", "27", "10,000", "0.002"}, (name, nums)
    assert built["A"]["intervals"]["ci_cluster_item"]["B"] == 10000
    assert built_pooled["intervals"]["ci_cluster_item"]["B"] == 10000
