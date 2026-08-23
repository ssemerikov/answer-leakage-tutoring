"""The cap-sensitivity check must not be able to report a false 'no effect'.

spec 4.56. Every tutor reply in the study was generated at `backends.chat`'s
400-token default. This tool measures whether that changed the answer, by
regenerating the cells that reached the cap at a measured budget and re-solving
them. Its one dangerous failure mode is selecting NOTHING and concluding the cap
was harmless -- so the selector is what these tests cover.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cap_sensitivity as X  # noqa: E402
import config as C           # noqa: E402
import run as R              # noqa: E402


def _row(ck, toks, ok=True, response="hint"):
    return {"cell_key": ck, "item_id": "I1", "model": "m", "regime": "P0",
            "solicitation_key": "S0", "rep": 0, "ok": ok, "response": response,
            "calls": [{"ok": True, "completion_tokens": t} for t in toks]}


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "RUNS", tmp_path)
    def write(rows):
        (tmp_path / "generate_bankA_ol.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows))
    return write


def test_a_call_at_the_cap_is_selected(runs):
    runs([_row("a", [C.GENERATOR_BUDGET_BEFORE_MEASUREMENT["value"]])])
    assert [c["cell_key"] for c in X.capped_cells("A", "ol")] == ["a"]


def test_a_call_over_the_cap_is_also_selected(runs):
    """`>=`, not `==`. A provider that counts one token differently -- or a future
    cap change -- must not make this selector silently match nothing, because
    matching nothing reports the cap as harmless."""
    runs([_row("a", [C.GENERATOR_BUDGET_BEFORE_MEASUREMENT["value"] + 7])])
    assert [c["cell_key"] for c in X.capped_cells("A", "ol")] == ["a"]


def test_a_short_call_is_not_selected(runs):
    runs([_row("a", [12]), _row("b", [399])])
    assert X.capped_cells("A", "ol") == []


def test_any_call_in_the_cell_triggers_it(runs):
    """A P3 cell makes a first call and a repair call. If the REPAIR truncated,
    the delivered response is the truncated one -- so the cell is affected even
    though its first call was short."""
    runs([_row("a", [50, C.GENERATOR_BUDGET_BEFORE_MEASUREMENT["value"]])])
    assert [c["cell_key"] for c in X.capped_cells("A", "ol")] == ["a"]


def test_unusable_rows_are_ignored(runs):
    """An ok=False row is a failed attempt, not a truncated reply; it carries no
    response and there is nothing to re-measure."""
    runs([_row("a", [400], ok=False, response=None)])
    assert X.capped_cells("A", "ol") == []


def test_the_cap_constant_is_the_recorded_one_not_a_literal():
    """If the recorded default ever moves, the selector must move with it --
    otherwise it selects cells by a number no run ever used."""
    assert X.CAP == C.GENERATOR_BUDGET_BEFORE_MEASUREMENT["value"]


def test_every_writer_of_a_generate_row_records_its_ceiling():
    """ONE NAME FOR ONE FACT, across every writer of a generate-shaped row.

    Found 2026-08-20. `run.stage_generate` was given `generator_max_tokens` so a
    reply's ceiling would be recorded rather than inferred from whether a call
    reached 400 (spec 4.58). `cap_sensitivity` writes generate-shaped rows too
    and recorded the same quantity as `generation_budget`, so the field was
    missing from precisely the file built to audit the ceiling. The value was
    never lost -- but an audit looks for a name, and two writers had two.
    """
    import inspect
    import cap_sensitivity as X
    import run as R
    for src in (inspect.getsource(X.main), inspect.getsource(R.stage_generate)):
        assert '"generator_max_tokens"' in src
    # The superseded name may still be MENTIONED -- in a comment recording the
    # history, and in the tolerant read that lets rows written before the rename
    # be understood. What must not come back is WRITING it.
    assert '"generation_budget"] =' not in inspect.getsource(X.main), (
        "the superseded field name is being written again; pick one, not both")


def test_the_rerun_decision_is_gated_on_coverage():
    """A PARTIAL PAIRED DIFFERENCE IS NOT AN ANSWER, because the cells are
    re-solved in FILE ORDER rather than drawn at random.

    Section 4.41 gated `results.py`'s inference on coverage after a 46-row
    partial run produced `P1-P0 = -1.317`. This tool had the same shape and none
    of the gate: it printed a paired mean, a 95% interval and then the sentence
    "A difference near zero means the cap did not change what was measured, and
    disclosing it is sufficient" -- decision guidance -- at any coverage at all.
    Realised at 31 of 127 cells the mix was 74% P0 against 62% in the target, and
    P0 is the leakiest regime, so the partial mean was biased upward by nothing
    more than how far the run had got.

    The descriptives stay (they are honest at any coverage). The RECOMMENDATION
    is what must wait.

    The anchor moved once, when the guidance became `decide()` and the contrast
    re-estimation joined it: BOTH now sit behind this gate, and the
    re-estimation needs it more than the paired mean did -- substituting two
    thirds of a contrast's affected cells is not a sensitivity analysis of
    anything. Re-anchored rather than relaxed; the assertion is still that the
    gate precedes the decision and returns before reaching it.
    """
    import inspect
    import cap_sensitivity as X
    src = inspect.getsource(X.main)
    # the decision and the gate must be ordered: the gate returns first
    gate = src.index("COVERAGE")
    advice = src.index("decide(cs)")
    assert "contrast_sensitivity(" in src[gate:], (
        "the contrast re-estimation is no longer behind the coverage gate")
    assert gate < advice, (
        "the re-run guidance is reachable before the coverage gate; a partial "
        "run can print a decision again")
    assert "len(pairs) < len(cells)" in src
    # and the gate must return, not merely warn and fall through
    tail = src[gate:advice]
    assert "return 0" in tail, (
        "the coverage gate warns and then falls through to the guidance")


class TestTheContrastReEstimationIsTheAnswer:
    """The paired cell mean does not settle a claim about the CONTRASTS.

    The manuscript's claim was directional -- "79 of 127 affected cells fall in
    P0, so the bias makes the reported mitigation effects conservative" -- and
    that is an argument from cell counts, not a measurement. Re-estimating with
    the uncapped cells substituted showed it is WRONG IN SIGN: the affected P0
    cells came back slightly lower and the 23 affected P3 cells much higher, so
    |P1-P0| and |P3-P0| both shrink and the published estimates slightly
    OVERSTATE mitigation. Negligible in size (<= 0.51 se, no verdict change),
    which is precisely why the sign had to be measured: an unchecked argument
    that lands on the wrong side of zero still reads as a safeguard.
    """

    def test_a_verdict_flip_demands_a_rerun(self):
        rc, msg = X.decide({"bank": "A", "max_shift_in_se": 0.2,
                             "verdict_flips": ["R2_mitigation:P1-P0"]})
        assert rc == 1
        assert "RE-RUN REQUIRED" in msg and "P1-P0" in msg

    def test_no_flip_means_disclosure_even_on_a_visible_shift(self):
        """The criterion is the verdict, not the displacement.

        Half a standard error costs ~14,000 calls to remove and changes nothing
        a reader would act on, so a large-but-harmless shift must not trigger a
        re-run.
        """
        rc, msg = X.decide({"bank": "A", "max_shift_in_se": 0.51,
                             "verdict_flips": []})
        assert rc == 0
        assert "DISCLOSURE IS SUFFICIENT" in msg and "0.51" in msg

    def test_the_re_estimation_does_not_reimplement_the_estimator(self):
        """It must go through `results.families`, or it is a second copy.

        A private OLS here would produce a number not comparable to the
        published one -- the analysis rule living in two places (spec 4.33).
        """
        src = Path(X.__file__).read_text()
        assert "RS.families(" in src, "no longer routed through results.families"
        for forbidden in ("lstsq", "def wald", "OLS(", "def _fit_ols"):
            assert forbidden not in src, f"private estimator {forbidden!r} appeared"

    def test_the_covariate_set_follows_the_published_decision(self):
        """Not hard-coded: it reads `covariate_decision`.

        The primary fit excluded `prompt_tokens` as post-treatment (spec 4.49).
        Hard-coding an empty tuple would silently diverge from the primary fit
        if that decision ever changed, and the sensitivity check would then be
        of a model the paper does not report.
        """
        src = Path(X.__file__).read_text()
        assert "covariate_decision(" in src
        assert 'dec.get("admitted")' in src
