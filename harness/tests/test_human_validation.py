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
