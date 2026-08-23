"""Tests for the resumable stage runner.

Resumability is the property under test: a screening run costs thousands of API
calls, so losing completed work to an interruption is expensive, and silently
REDOING it would double-count records in the output.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run as R  # noqa: E402


class TestResume:
    def test_load_done_reads_keys(self, tmp_path):
        p = tmp_path / "s.jsonl"
        p.write_text('{"item_id": "A-1"}\n{"item_id": "A-2"}\n')
        assert R.load_done(p, "item_id") == {"A-1", "A-2"}

    def test_missing_file_is_empty(self, tmp_path):
        assert R.load_done(tmp_path / "nope.jsonl", "item_id") == set()

    def test_torn_final_line_is_skipped_not_fatal(self, tmp_path):
        """A process killed mid-write leaves a partial line. Resuming must
        tolerate it; aborting would strand every completed record."""
        p = tmp_path / "s.jsonl"
        p.write_text('{"item_id": "A-1"}\n{"item_id": "A-2"}\n{"item_i')
        assert R.load_done(p, "item_id") == {"A-1", "A-2"}

    def test_blank_lines_ignored(self, tmp_path):
        p = tmp_path / "s.jsonl"
        p.write_text('{"item_id": "A-1"}\n\n\n{"item_id": "A-2"}\n')
        assert R.load_done(p, "item_id") == {"A-1", "A-2"}

    def test_append_is_one_object_per_line(self, tmp_path):
        p = tmp_path / "s.jsonl"
        R.append(p, {"item_id": "A-1", "baseline": 0.3})
        R.append(p, {"item_id": "A-2", "baseline": 0.5})
        lines = p.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[1])["item_id"] == "A-2"

    def test_append_then_resume_round_trip(self, tmp_path):
        p = tmp_path / "s.jsonl"
        for i in range(5):
            R.append(p, {"item_id": f"A-{i}"})
        done = R.load_done(p, "item_id")
        remaining = [f"A-{i}" for i in range(8) if f"A-{i}" not in done]
        assert remaining == ["A-5", "A-6", "A-7"]


class TestBankLoading:
    def test_real_bank_loads(self):
        data = R.load_bank("A")
        assert data["bank_id"] == "A"
        assert len(data["items"]) == 60

    def test_mcq_filter_matches_bank(self):
        data = R.load_bank("A")
        mcq = [i for i in data["items"] if i["type"] == "mcq"]
        assert len(mcq) == 40
        # Short-answer items have no option set, so masked-stem identifiability
        # does not apply to them and they are screened separately.
        assert all("options" in i for i in mcq)


class TestScreenGate:
    """Generation must not start on an unscreened bank."""

    def test_unscreened_bank_exits(self, tmp_path, monkeypatch):
        import pytest
        monkeypatch.setattr(R, "_jsonl_path", lambda s, b, r="kr": tmp_path / "missing.jsonl")
        with pytest.raises(SystemExit, match="not screened"):
            R.screened_items("A", {"items": []})

    def test_incomplete_screen_exits(self, tmp_path, monkeypatch):
        import json as _j
        import pytest
        p = tmp_path / "screen.jsonl"
        p.write_text(_j.dumps({"item_id": "I1", "passes_headroom": True}) + "\n")
        monkeypatch.setattr(R, "_jsonl_path", lambda s, b, r="kr": p)
        data = {"items": [{"id": "I1", "type": "mcq"}, {"id": "I2", "type": "mcq"}]}
        with pytest.raises(SystemExit, match="incomplete"):
            R.screened_items("A", data)

    def test_rejected_items_excluded(self, tmp_path, monkeypatch):
        import json as _j
        p = tmp_path / "screen.jsonl"
        p.write_text(
            _j.dumps({"item_id": "I1", "passes_headroom": True}) + "\n"
            + _j.dumps({"item_id": "I2", "passes_headroom": False}) + "\n")
        monkeypatch.setattr(R, "_jsonl_path", lambda s, b, r="kr": p)
        data = {"items": [{"id": "I1", "type": "mcq"}, {"id": "I2", "type": "mcq"}]}
        kept = R.screened_items("A", data)
        assert [i["id"] for i in kept] == ["I1"]


class TestCellKey:
    def test_key_is_stable_and_unique(self):
        cell = {"item": {"id": "I1"}, "model": "kr/glm-5",
                "solicitation_key": "S3", "regime": "P1", "rep": 2}
        k1 = R._cell_key(cell)
        assert k1 == R._cell_key(cell)
        other = dict(cell, rep=3)
        assert R._cell_key(other) != k1


class TestScreenedItemsOnAPrefix:
    """Generation over a partly screened bank is permitted ONLY by a freeze.

    The permission lives in the deposited artefact rather than in a flag: a flag
    would let anyone generate over a partly screened bank with nothing in the
    record saying so, and the record is what a reader of the paper gets.
    """

    def _wire(self, tmp_path, monkeypatch, screened, frozen=None):
        items = [{"id": f"T-{n}", "type": "mcq", "options": ["a", "b", "c"],
                  "answer_index": 0} for n in range(1, 5)]
        s = tmp_path / "screen.jsonl"
        s.write_text("".join(
            json.dumps({"item_id": i, "passes_headroom": p}) + "\n"
            for i, p in screened.items()))
        monkeypatch.setattr(R, "_jsonl_path", lambda stage, b, r="kr": s)
        fp = tmp_path / "frozen.json"
        if frozen is not None:
            fp.write_text(json.dumps(frozen))
        monkeypatch.setattr(R, "frozen_path", lambda b, r="kr": fp)
        return {"bank_id": "T", "items": items}

    def test_an_undeclared_prefix_is_refused(self, tmp_path, monkeypatch):
        data = self._wire(tmp_path, monkeypatch, {"T-1": True, "T-2": False})
        with pytest.raises(SystemExit) as e:
            R.screened_items("t", data, "ol")
        assert "--prefix" in str(e.value)

    def test_a_declared_prefix_is_accepted(self, tmp_path, monkeypatch):
        data = self._wire(tmp_path, monkeypatch, {"T-1": True, "T-2": False},
                          frozen={"screened_prefix": True,
                                  "unscreened_mcq_ids": ["T-3", "T-4"]})
        assert [i["id"] for i in R.screened_items("t", data, "ol")] == ["T-1"]

    def test_a_freeze_that_no_longer_matches_the_screen_is_refused(
            self, tmp_path, monkeypatch):
        """The screen is append-only and resumable, so it can grow after a
        freeze. Generating then would mix items the deposited bank says were
        never screened with verdicts that exist -- so the freeze is stale, and
        staleness has to be an error rather than a silent widening."""
        data = self._wire(tmp_path, monkeypatch,
                          {"T-1": True, "T-2": False, "T-3": True},
                          frozen={"screened_prefix": True,
                                  "unscreened_mcq_ids": ["T-3", "T-4"]})
        with pytest.raises(SystemExit) as e:
            R.screened_items("t", data, "ol")
        assert "moved since the freeze" in str(e.value)

    def test_a_complete_screen_needs_no_freeze(self, tmp_path, monkeypatch):
        data = self._wire(tmp_path, monkeypatch,
                          {"T-1": True, "T-2": False, "T-3": True, "T-4": True})
        assert len(R.screened_items("t", data, "ol")) == 3
