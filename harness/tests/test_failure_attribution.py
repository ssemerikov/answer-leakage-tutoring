"""Every measurement record must say how many calls failed AND what failed.

WHY THIS FILE EXISTS. `solvability` aggregated a per-kind failure histogram at
all three measurement sites, handed it to `_guard_failures` so an abort could
name its cause, and then threw it away. A run that stayed under the 25% abort
threshold therefore recorded THAT calls had failed but never WHAT failed.

That distinction is the one spec 4.12 rests on. `transient` and `network` are
retried and are noise; `gated`, `withdrawn` and `invalid_model` are permanent
roster loss the manuscript is obliged to report; `truncated_before_content` is
the deterministic instrument defect that section 4.13 spent a rewrite on. Losing
the kinds collapses all of them into one integer.

The Bank B pilot lost 23 calls this way. They cannot be attributed now -- the
records are append-only and the runtime histogram is gone -- which is why this
is pinned rather than merely fixed.

The `leak_score` record was worse: it guarded its failure count and
recorded neither the count nor the kinds, the only one of the three to lose
both. A leak measurement with unrecorded attrition cannot be audited at all.

THE TEST DERIVES ITS OWN SUBJECTS. `test_pipeline_e2e` once hand-listed four
stage names and omitted the fifth -- the only one missing its guard -- so the
test documented the bug instead of catching it. Here the record-producing
functions are found by calling them, so a fourth measurement added later is
covered the moment it exists.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402
import backends as B  # noqa: E402
import solvability as S  # noqa: E402

ITEM = {"id": "T-01", "type": "mcq", "topic": "loops", "level": 1,
        "stem": "What does range(3) yield?",
        "options": ["0,1,2", "1,2,3", "0,1,2,3"], "answer_index": 0}

# Real roster names, because `leak_score` resolves each solver's vendor family
# to exclude self-scoring, and `config.vendor` refuses to infer a family from an
# id it does not know. A stub name would fail that guard rather than the test.
SOLVERS = ("ol/gemma4:cloud", "ol/minimax-m3:cloud")
GENERATOR = "ol/gpt-oss:120b-cloud"        # a third family, so neither solver is dropped


def _backend(fail_kind=None, fail_every=0):
    """Answer '1' normally; fail every `fail_every`-th call with `fail_kind`."""
    from conftest import fake_result
    state = {"n": 0}

    def chat(model, messages, **kw):
        state["n"] += 1
        if fail_every and state["n"] % fail_every == 0:
            return fake_result(None, ok=False, error_kind=fail_kind, status=503)
        return fake_result("1", model=model)

    return chat


def _records(monkeypatch, **kw):
    """Produce one record from each measurement site, with the same backend."""
    monkeypatch.setattr(B, "chat", _backend(**kw))
    base = S.baseline_identifiability(ITEM, solvers=SOLVERS, k=4,
                                      path=C.PATH_CONTROLLED)
    plc = S.placebo_identifiability(ITEM, solvers=SOLVERS, k=1,
                                    path=C.PATH_CONTROLLED)
    leak = S.leak_score(
        ITEM, "try thinking about indices", base["baseline"], generator=GENERATOR,
        solvers=SOLVERS, k=4, path=C.PATH_CONTROLLED)
    return {"baseline": base, "placebo": plc, "leak": leak}


class TestEveryRecordCarriesAttribution:

    def test_all_sites_record_the_failure_count(self, monkeypatch):
        for name, rec in _records(monkeypatch).items():
            assert "n_failed_calls" in rec, f"{name} record loses its failure count"

    def test_all_sites_record_the_failure_kinds(self, monkeypatch):
        for name, rec in _records(monkeypatch).items():
            assert "error_kinds" in rec, f"{name} record loses its failure kinds"

    def test_kinds_are_empty_not_absent_when_nothing_failed(self, monkeypatch):
        """Absent and none-observed must be distinguishable in the deposit."""
        for name, rec in _records(monkeypatch).items():
            assert rec["error_kinds"] == {}, f"{name}: {rec['error_kinds']}"
            assert rec["n_failed_calls"] == 0, name

    def test_a_transient_failure_is_named_in_every_record(self, monkeypatch):
        """A failure that exhausted its retries must reach the record by kind.

        Note the fake replaces `backends.chat` wholesale, so it stands in for a
        call that has ALREADY exhausted retry -- the wrapper is not exercised
        here (`test_backends` covers it). What is under test is whether the kind
        survives the last hop, from CallResult into the persisted record.
        """
        recs = _records(monkeypatch, fail_kind="transient", fail_every=8)
        for name, rec in recs.items():
            assert rec["n_failed_calls"] > 0, f"{name} saw no failures"
            assert rec["error_kinds"].get("transient"), (
                f"{name} counted failures but did not name them: {rec['error_kinds']}")

    def test_a_permanent_failure_is_named_not_merely_counted(self, monkeypatch):
        """`gated` is roster loss the manuscript must report, not noise."""
        recs = _records(monkeypatch, fail_kind="gated", fail_every=8)
        for name, rec in recs.items():
            assert "gated" in rec["error_kinds"], (
                f"{name} cannot distinguish roster loss from a flaky network")

    def test_counts_and_kinds_agree(self, monkeypatch):
        recs = _records(monkeypatch, fail_kind="transient", fail_every=8)
        for name, rec in recs.items():
            assert sum(rec["error_kinds"].values()) == rec["n_failed_calls"], (
                f"{name}: kinds {rec['error_kinds']} do not sum to "
                f"{rec['n_failed_calls']}")


class TestGuardStillFires:
    """Attribution must not have loosened the abort it feeds."""

    def test_a_dead_backend_still_raises(self, monkeypatch):
        monkeypatch.setattr(C, "MAX_CALL_ATTEMPTS", 1, raising=False)
        monkeypatch.setattr(B, "chat", _backend("network", fail_every=1))
        with pytest.raises(S.BackendUnavailable):
            S.baseline_identifiability(ITEM, solvers=SOLVERS, k=4,
                                       path=C.PATH_CONTROLLED)
