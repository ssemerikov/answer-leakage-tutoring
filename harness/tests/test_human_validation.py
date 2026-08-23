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
