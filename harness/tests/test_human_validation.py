"""The Tier-3 sample must be reproducible, blind, and impossible to fake."""
import csv
import json
import re
from pathlib import Path

import pytest

import human_validation as hv

SRC = Path(hv.__file__).read_text()


def test_the_draw_is_deterministic_and_seed_free():
    # A seed fixed today is a seed chosen after the responses existed. There
    # must be no seed to have chosen.
    assert not re.search(r"\bseed\b\s*=", SRC), "the draw must not take a seed"
    a = [r["response_id"] for r in hv.draw()]
    b = [r["response_id"] for r in hv.draw()]
    assert a == b and len(set(a)) == 240


def test_the_allocation_is_the_pre_registered_one():
    s = hv.draw()
    cells = {}
    for r in s:
        cells.setdefault(
            (r["generator"], r["regime"], r["solicitation_key"]), []).append(r)
    assert len(cells) == 48, "4 generators x 4 regimes x 3 solicitations"
    assert {len(v) for v in cells.values()} == {hv.PER_CELL}


def test_the_frame_comes_from_the_roster_not_the_config_constant():
    # 4.43: C.SOLICITATIONS still holds four entries because S1's key was left
    # in place when the level was cut. Reading it yields a 64-cell frame.
    #
    # Asked of the AST, not of the text: the module's own docstring names the
    # constant in order to say it must not be read, and a raw substring test
    # cannot tell a prohibition from the thing prohibited. That is the same
    # reason tests/test_ops_watch_stage.py strips comments before matching.
    import ast
    tree = ast.parse(SRC)
    reads = [n for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and n.attr == "SOLICITATIONS"]
    assert not reads, "the frame is being read from the config constant"
    assert len(hv.SOLICITATION_KEYS) == 3


def test_the_sheet_carries_neither_the_key_nor_the_condition(tmp_path):
    hv.sheets(out_dir=tmp_path)
    rows = list(csv.DictReader((tmp_path / "annotation_A1.csv").open()))
    assert len(rows) == 240
    cols = set(rows[0])
    for banned in ("regime", "generator", "solicitation_key", "answer",
                   "answer_index", "key"):
        assert banned not in cols, f"the sheet leaks {banned}"
    assert cols == {"response_id", "options", "tutor_reply", "choice", "notes"}


def test_the_two_sheets_hold_the_same_rows_in_different_orders(tmp_path):
    hv.sheets(out_dir=tmp_path)
    a = [r["response_id"] for r in csv.DictReader((tmp_path / "annotation_A1.csv").open())]
    b = [r["response_id"] for r in csv.DictReader((tmp_path / "annotation_A2.csv").open())]
    assert set(a) == set(b), "annotators must label the same rows"
    assert a != b, "row order must differ, or the sheet order is a shared cue"


def test_scoring_refuses_a_partial_labelling(tmp_path):
    hv.sheets(out_dir=tmp_path)
    p = tmp_path / "annotation_A1.csv"
    rows = list(csv.DictReader(p.open()))
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        for i, r in enumerate(rows):
            r["choice"] = "1" if i < 10 else ""
            w.writerow(r)
    out = hv.score(ann_dir=tmp_path)
    assert out["status"] == "partial", out
    assert "n_labelled" in out and "cohen_kappa" not in out


def test_scoring_refuses_a_label_outside_the_codebook(tmp_path):
    hv.sheets(out_dir=tmp_path)
    doc = json.loads(hv.SAMPLE_PATH.read_text())
    ids = {r["response_id"] for r in doc["responses"]}
    n_opt = {i: 3 for i in ids}
    p = tmp_path / "bad.csv"
    rid = sorted(ids)[0]
    p.write_text("response_id,options,tutor_reply,choice,notes\n"
                 f"{rid},x,y,probably 2,\n")
    with pytest.raises(ValueError, match="neither an option number"):
        hv.read_labels(p, ids, n_opt)


def test_scoring_refuses_a_row_the_sample_does_not_contain(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("response_id,options,tutor_reply,choice,notes\n"
                 "deadbeefdeadbeef,x,y,1,\n")
    with pytest.raises(ValueError, match="not in the frozen sample"):
        hv.read_labels(p, {"cafecafecafecafe"}, {"cafecafecafecafe": 3})


def test_freeze_will_not_silently_move_a_sample_people_have_labelled():
    doc = json.loads(hv.SAMPLE_PATH.read_text())
    assert doc["n"] == 240 and len(doc["sha256"]) == 64
    assert hv.freeze()["sha256"] == doc["sha256"], "re-freezing must be a no-op"


def test_no_label_is_ever_manufactured():
    # The one thing this module must never do. A default, a fill-forward or an
    # imputation here is fabricated data in a paper.
    for bad in ("random.choice", "np.random", "imput", "fillna", "default_label"):
        assert bad not in SRC, f"{bad} has no business in a human-label module"


def test_the_threshold_grid_is_not_invented_at_scoring_time():
    assert "threshold_sensitivity" in SRC
    assert "refusing to invent one" in SRC


# ---- the sheet comes back from a person, in their spreadsheet's dialect ----
#
# A2 returned the real sheet UTF-8-with-BOM, semicolon-delimited, and carrying an
# extra `Column1;...;Column5` banner row above the header. Reading only the
# dialect `sheets()` writes would have rejected 240 correct labels over Excel's
# regional separator -- so the reader accepts what a spreadsheet emits, but as a
# CHECKED parse: the header is found by name and must split into exactly the five
# expected columns, or the file raises. A mis-split sheet still produces a number.

def _excelise(src: Path, dst: Path, choices):
    """Rewrite a sheet the way Excel handed A2's back."""
    rows = list(csv.DictReader(src.open()))
    out = ["﻿Column1;Column2;Column3;Column4;Column5\r\n",
           ";".join(hv.EXPECTED_HEADER) + "\r\n"]
    for r, c in zip(rows, choices):
        vals = [r["response_id"], r["options"], r["tutor_reply"], c, ""]
        out.append(";".join('"' + v.replace('"', '""') + '"' for v in vals) + "\r\n")
    dst.write_bytes("".join(out).encode("utf-8"))


def test_a_spreadsheet_dialect_yields_the_same_labels_as_the_written_one(tmp_path):
    hv.sheets(out_dir=tmp_path)
    plain = tmp_path / "annotation_A1.csv"
    ids = {r["response_id"] for r in json.loads(hv.SAMPLE_PATH.read_text())["responses"]}
    n_opt = {i: 3 for i in ids}
    choices = ["1", "2", "cannot_tell"] * 80

    rows = list(csv.DictReader(plain.open()))
    with plain.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        for r, c in zip(rows, choices):
            r["choice"] = c
            w.writerow(r)

    xl = tmp_path / "annotation_A1_completed.csv"
    _excelise(plain, xl, choices)
    assert hv.read_labels(xl, ids, n_opt) == hv.read_labels(plain, ids, n_opt)


def test_a_sheet_that_cannot_be_split_into_the_five_columns_raises(tmp_path):
    # The dangerous outcome is not a refusal, it is a shifted row: labels land
    # in the wrong column and the statistics are computed anyway.
    p = tmp_path / "weird.csv"
    p.write_text("response_id|options|tutor_reply|choice|notes\n"
                 "cafecafecafecafe|x|y|1|\n")
    with pytest.raises(ValueError, match="Refusing to guess"):
        hv.read_labels(p, {"cafecafecafecafe"}, {"cafecafecafecafe": 3})


def test_a_returned_sheet_supersedes_the_blank_template(tmp_path):
    # `sheets()` writes a blank template under the plain name. Reading it while
    # a completed copy sits beside it reports `not_labelled_yet` over finished
    # work -- 4.33's quiet half, where the stale copy yields NO number.
    hv.sheets(out_dir=tmp_path)
    assert hv.sheet_path(tmp_path, "A1").name == "annotation_A1.csv"
    (tmp_path / "annotation_A1_completed.csv").write_text("x")
    assert hv.sheet_path(tmp_path, "A1").name == "annotation_A1_completed.csv"


def test_accepting_a_dialect_did_not_soften_the_codebook(tmp_path):
    # Widening WHAT can be parsed must not widen WHICH labels are admissible:
    # an option number outside 1..n is still a refusal, in any dialect.
    ids = {"cafecafecafecafe"}
    p = tmp_path / "bad.csv"
    p.write_bytes("﻿Column1;Column2;Column3;Column4;Column5\r\n"
                  "response_id;options;tutor_reply;choice;notes\r\n"
                  "cafecafecafecafe;1. 5 | 2. 4 | 3. 6;why;5;\r\n".encode("utf-8"))
    with pytest.raises(ValueError, match="outside 1..3"):
        hv.read_labels(p, ids, {"cafecafecafecafe": 3})


# ---- adjudications: a person's re-reading of their own entry, as data -------

def test_the_adjudication_record_must_agree_with_the_labelled_sheet(tmp_path):
    # The record and the sheet are two copies of one fact. If they drift, the
    # sensitivity is computed against a reading nobody made -- and it would
    # still print a number (4.33).
    import shutil
    for f in hv.ANNOTATION_DIR.glob("annotation_A*_completed.csv"):
        shutil.copy(f, tmp_path / f.name)
    rows, _ = hv._read_sheet_rows(tmp_path / "annotation_A2_completed.csv")
    with (tmp_path / "annotation_A2_completed.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(hv.EXPECTED_HEADER)); w.writeheader()
        for r in rows:
            if r["response_id"] == "e5194ca9cd8c11b9":
                r = dict(r, choice="2")          # neither the reading nor the alt
            w.writerow(r)
    with pytest.raises(ValueError, match="have drifted"):
        hv.adjudication_sensitivity(ann_dir=tmp_path)


def test_the_adjudication_sensitivity_is_computed_not_asserted():
    # The manuscript says the one re-read cell changes nothing. That claim is
    # only checkable because the alternative reading is recorded beside the
    # chosen one and the whole sample is re-scored under it.
    a = hv.adjudication_sensitivity()
    assert a is not None and a["n_adjudicated"] >= 1
    assert 0.0 <= a["max_rate_displacement"] < 0.05
    for x in hv.adjudications():
        assert x["alternative"] != x["read_as"], "an alternative must differ"


def test_the_assembled_artefact_is_internally_consistent():
    d = hv.assemble()
    s = d["structure"]
    assert d["percent_agreement"]["k"] == s["same_option"] + s["both_cannot_tell"]
    assert s["n_both_committed"] == s["same_option"] + s["different_option"]
    assert d["recovered_consensus"]["k"] <= d["recovered_either"]["k"]
    assert d["percent_agreement"]["n"] == d["n"] == 240


def test_the_manuscript_reads_the_artefact_and_not_the_sheets():
    # 4.41: a reported number comes from an assembled file. A second reader of
    # the CSVs would be a second implementation of the scoring rule.
    vn = Path(hv.HARNESS / "verify_numbers.py").read_text()
    assert "human_validation_results.json" in vn
    assert "annotation_A1" not in vn, (
        "verify_numbers must not parse the annotation sheets itself")


# ---- C1: one join between `generate` and `solve` (revision 2026-11, P1C) ----
#
# Every consumer of "the reply and its score" used to read the solve file
# itself. Two readers of one mapping are two copies of the join rule, and the
# first one already lost a day to joining on (cell_key, rep) when the replicate
# lives INSIDE cell_key. The join is one function now, and it refuses rather
# than computing over whatever happened to match.

def _jl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _gen(ck, ok=True, response="a hint", blocked=False, **kw):
    item, gen, sol, reg, rep = ck.split("|")
    row = {"cell_key": ck, "item_id": item, "model": gen,
           "solicitation_key": sol, "regime": reg, "rep": int(rep), "ok": ok,
           "response": response if ok else None, "blocked": blocked,
           "verifier_rejections": 0, "original_response": response}
    row.update(kw)
    return row


def _solve(ck, leak_adj=0.5, **kw):
    item, gen, sol, reg, _rep = ck.split("|")
    row = {"cell_key": ck, "item_id": item, "generator": gen,
           "solicitation_key": sol, "regime": reg, "blocked": False,
           "leak_adj": leak_adj, "rep": None}
    row.update(kw)
    return row


@pytest.fixture
def fake_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(hv, "RUNS", tmp_path)

    def write(gen, solve, bank="A", roster="ol"):
        _jl(tmp_path / f"generate_bank{bank}_{roster}.jsonl", gen)
        _jl(tmp_path / f"solve_bank{bank}_{roster}.jsonl", solve)
    return write


CK1 = "I1|g/x:cloud|S0|P1|0"
CK2 = "I1|g/x:cloud|S0|P1|1"


def test_join_returns_one_record_per_solved_reply_in_solve_order(fake_runs):
    fake_runs([_gen(CK1), _gen(CK2)], [_solve(CK2, 0.25), _solve(CK1, 0.75)])
    out = hv.join_generate_solve("A", "ol")
    # Solve-file order: the order results.py's frame uses, so a mean computed
    # over the join sums in the same order and reproduces its float exactly.
    assert [r["cell_key"] for r in out] == [CK2, CK1]
    r = out[1]
    assert r["leak_adj"] == 0.75 and r["generator"] == "g/x:cloud"
    assert r["regime"] == "P1" and r["solicitation_key"] == "S0"
    assert r["response"] == "a hint" and r["original_response"] == "a hint"


def test_join_reads_past_superseded_attempts(fake_runs):
    # 4.39: an ok=False row is an attempt, not a unit of work.
    fake_runs([_gen(CK1, ok=False), _gen(CK1)], [_solve(CK1)])
    assert len(hv.join_generate_solve("A", "ol")) == 1


def test_join_leaves_out_a_reply_with_no_leak_adj(fake_runs):
    # Never imputed (results.py, spec 3.2c): the record is absent, not zero.
    fake_runs([_gen(CK1), _gen(CK2)], [_solve(CK1, None), _solve(CK2, 0.5)])
    assert [r["cell_key"] for r in hv.join_generate_solve("A", "ol")] == [CK2]


def test_join_refuses_a_duplicate_solve_key(fake_runs):
    fake_runs([_gen(CK1)], [_solve(CK1), _solve(CK1)])
    with pytest.raises(ValueError, match="twice"):
        hv.join_generate_solve("A", "ol")


def test_join_refuses_a_duplicate_usable_generate_key(fake_runs):
    fake_runs([_gen(CK1), _gen(CK1)], [_solve(CK1)])
    with pytest.raises(ValueError, match="twice"):
        hv.join_generate_solve("A", "ol")


def test_join_refuses_a_solve_row_with_no_usable_generate_row(fake_runs):
    # The generate row exists but only as a failed attempt: there is no reply
    # for this score to be a score OF.
    fake_runs([_gen(CK1), _gen(CK2, ok=False)], [_solve(CK1), _solve(CK2)])
    with pytest.raises(ValueError, match="no usable generate row"):
        hv.join_generate_solve("A", "ol")


@pytest.mark.parametrize("field,value", [
    ("regime", "P2"), ("solicitation_key", "S3"), ("generator", "g/y:cloud"),
    ("item_id", "I9"), ("blocked", True)])
def test_join_refuses_when_the_two_copies_disagree(fake_runs, field, value):
    # Both files carry these facts. If they differ, one of them is describing
    # a different reply, and either answer would be a guess.
    fake_runs([_gen(CK1)], [_solve(CK1, **{field: value})])
    with pytest.raises(ValueError, match="disagree"):
        hv.join_generate_solve("A", "ol")


def test_join_accepts_the_lowercase_bank_id(fake_runs):
    # `bs` is the CLI's bank id; the run files are named `bankBS`.
    fake_runs([_gen(CK1)], [_solve(CK1)], bank="BS")
    assert len(hv.join_generate_solve("bs", "ol")) == 1


def test_join_on_the_real_runs_is_complete_and_one_to_one():
    for bank, n in (("A", 2016), ("bs", 3888)):
        out = hv.join_generate_solve(bank, "ol")
        assert len(out) == n == len(hv.usable_rows(bank.upper(), "ol"))
        assert len({r["cell_key"] for r in out}) == n
        assert all(r["leak_adj"] is not None for r in out)


def test_automated_vs_human_uses_the_one_join():
    import inspect
    src = inspect.getsource(hv.automated_vs_human)
    assert "join_generate_solve(" in src
    assert "solve_bank" not in src, "a second reader of the solve file is back"


# ---- C3: human recovery by condition (revision 2026-11, P1C) ----------------

def _syn_labels():
    responses = {"r1": {"regime": "P0", "item_id": "I1"},
                 "r2": {"regime": "P1", "item_id": "I1"},
                 "r3": {"regime": "P1", "item_id": "I2"},
                 "r4": {"regime": "P2", "item_id": "I2"},
                 "r5": {"regime": "P3", "item_id": "I3"}}
    key = {"r1": 0, "r2": 1, "r3": 2, "r4": 0, "r5": 1}
    labels = {"A1": {"r1": 0, "r2": 1, "r3": None, "r4": 1, "r5": 1},
              "A2": {"r1": 0, "r2": 0, "r3": 2, "r4": 1, "r5": 1}}
    return responses, labels, key


def test_by_regime_counts_both_and_either_per_regime():
    responses, labels, key = _syn_labels()
    out = hv.by_regime(responses, labels, key, ("A1", "A2"))
    want = {"P0": (1, 1, 1), "P1": (2, 0, 2), "P2": (1, 0, 0), "P3": (1, 1, 1)}
    for reg, (n, both, either) in want.items():
        assert out[reg]["n"] == n
        assert (out[reg]["both"]["k"], out[reg]["both"]["n"]) == (both, n)
        assert (out[reg]["either"]["k"], out[reg]["either"]["n"]) == (either, n)
        p, lo, hi = hv.wilson(either, n)
        assert out[reg]["either"]["p"] == p and out[reg]["either"]["ci"] == [lo, hi]
    pooled = out["pooled_P1_P3"]
    assert pooled["regimes"] == ["P1", "P2", "P3"]
    assert (pooled["n"], pooled["both"]["k"], pooled["either"]["k"]) == (4, 1, 3)


def test_by_regime_refuses_a_response_outside_the_design():
    responses, labels, key = _syn_labels()
    responses["r5"] = {"regime": "P9"}
    with pytest.raises(ValueError, match="P9"):
        hv.by_regime(responses, labels, key, ("A1", "A2"))


def test_by_regime_uses_the_same_recovery_rules_as_the_headline():
    # One rule, one implementation: the per-regime counts must partition the
    # headline counts exactly, or the two are computing different things.
    import config as C
    d = hv.assemble()
    br = d["by_regime"]
    assert sum(br[r]["n"] for r in C.REGIMES) == d["n"] == 240
    assert sum(br[r]["both"]["k"] for r in C.REGIMES) == d["recovered_consensus"]["k"]
    assert sum(br[r]["either"]["k"] for r in C.REGIMES) == d["recovered_either"]["k"]
    pooled = br["pooled_P1_P3"]
    assert pooled["n"] == sum(br[r]["n"] for r in ("P1", "P2", "P3"))
    assert pooled["both"]["k"] == sum(br[r]["both"]["k"] for r in ("P1", "P2", "P3"))
    assert pooled["either"]["k"] == sum(br[r]["either"]["k"] for r in ("P1", "P2", "P3"))


def test_the_assembled_file_on_disk_is_current():
    # The manuscript reads the FILE. A by_regime added to the code but never
    # assembled would leave the writers pinning numbers nobody computed.
    on_disk = json.loads(hv.RESULTS_PATH.read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(hv.assemble(), sort_keys=True))
    assert on_disk == fresh


# ---- fix round 1: item-cluster intervals, one threshold rule ----------------

def test_by_regime_gives_no_cluster_interval_for_a_single_item():
    # One item has no between-item variation to resample; an interval there
    # would be a number with no meaning, so the entry says so instead.
    responses, labels, key = _syn_labels()
    out = hv.by_regime(responses, labels, key, ("A1", "A2"))
    assert out["P0"]["n_items"] == 1
    assert out["P0"]["both"]["ci_cluster_item"] is None
    assert out["P1"]["n_items"] == 2
    assert len(out["P1"]["either"]["ci_cluster_item"]) == 2
    assert out["pooled_P1_P3"]["n_items"] == 3


def test_by_regime_records_item_cluster_intervals_from_the_shared_function():
    import analyse as A
    d = hv.assemble()
    br = d["by_regime"]
    m = br["intervals"]["ci_cluster_item"]
    assert m == {"method": "item-cluster percentile bootstrap", "B": 10000,
                 "seed": 20261103, "n_clusters": 14}
    assert "unadjusted for item clustering" in br["intervals"]["ci"]
    doc = json.loads(hv.SAMPLE_PATH.read_text(encoding="utf-8"))
    rows = [r for r in doc["responses"] if r["regime"] in ("P1", "P2", "P3")]
    labels = d["by_regime"]["pooled_P1_P3"]
    # recompute the pooled `both` interval through the ONE implementation
    opts = hv._options_by_item("A", "ol")
    L = {a: hv.read_labels(hv.sheet_path(hv.ANNOTATION_DIR, a),
                           {r["response_id"] for r in doc["responses"]},
                           {r["response_id"]: len(opts[r["item_id"]][0])
                            for r in doc["responses"]})
         for a in ("A1", "A2")}
    both = [hv._recovered_both(L["A1"][r["response_id"]], L["A2"][r["response_id"]],
                               opts[r["item_id"]][1]) for r in rows]
    want = A.cluster_bootstrap_ci(both, [r["item_id"] for r in rows], "proportion")
    assert labels["both"]["ci_cluster_item"] == list(want)
    for reg in ("P0", "P1", "P2", "P3", "pooled_P1_P3"):
        for rule in ("both", "either"):
            lo, hi = br[reg][rule]["ci_cluster_item"]
            assert lo <= br[reg][rule]["p"] <= hi


def test_automated_vs_human_uses_the_shared_threshold_rule():
    import ast, inspect, textwrap
    src = textwrap.dedent(inspect.getsource(hv.automated_vs_human))
    assert "exceeds_threshold(" in src
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Compare):
            assert not any(isinstance(o, (ast.GtE, ast.Gt, ast.Lt, ast.LtE))
                           for o in n.ops), "a copy of the threshold rule is back"


def test_by_regime_intervals_note_carries_the_undercoverage_caveat():
    # P1C re-review N2 (P1F): the few-clusters caveat lives in the artefact the
    # writers pin numbers from, not only in a report.
    responses, labels, key = _syn_labels()
    note = hv.by_regime(responses, labels, key, ("A1", "A2"))["intervals"]["note"]
    assert note.startswith("Responses are clustered within items")
    assert ("approximate: a percentile bootstrap over 9–27 items tends to "
            "undercover") in note
    on_disk = json.loads(hv.RESULTS_PATH.read_text(encoding="utf-8"))
    assert on_disk["by_regime"]["intervals"]["note"] == note
