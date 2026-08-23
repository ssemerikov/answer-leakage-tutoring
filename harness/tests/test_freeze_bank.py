"""Tests for the post-screen bank freeze.

The frozen bank is what the study consumes and what gets deposited, so a defect
here is not a bug in a tool -- it is a defect in the released evidence.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import freeze_bank as F  # noqa: E402


def _bank(tmp_path, items):
    p = tmp_path / "bank_t.json"
    p.write_text(json.dumps({"bank_id": "T", "items": items}))
    return p


def _wire(monkeypatch, tmp_path, bank_path, screen_rows):
    s = tmp_path / "screen.jsonl"
    s.write_text("".join(json.dumps(r) + "\n" for r in screen_rows))
    monkeypatch.setattr(F.C, "BANKS_DIR", tmp_path)
    monkeypatch.setattr(F.R, "_jsonl_path", lambda stage, b, r="kr": s)
    return s


ITEMS = [
    {"id": "T-1", "type": "mcq", "options": ["a", "b", "c"], "answer_index": 0},
    {"id": "T-2", "type": "mcq", "options": ["a", "b", "c"], "answer_index": 1},
    {"id": "T-3", "type": "short", "answer_forms": ["x"]},
]


def _rows(bank_path, verdicts):
    h = hashlib.sha256(bank_path.read_bytes()).hexdigest()
    return [{"item_id": k, "baseline": b, "passes_headroom": p,
             "bank_sha256": h, "k_per_solver": 20}
            for k, (b, p) in verdicts.items()]


class TestFreeze:
    def test_short_answer_items_are_never_silently_dropped(self, tmp_path, monkeypatch):
        """The regression that motivated these tests.

        Filtering on the MCQ screen verdict alone drops every short-answer item,
        because they were never screened -- shrinking the bank by a third
        between the released pre-screen file and the frozen one, with nothing in
        either file to explain the gap.
        """
        p = _bank(tmp_path, ITEMS)
        _wire(monkeypatch, tmp_path, p, _rows(p, {"T-1": (0.1, True), "T-2": (1.0, False)}))
        F.freeze("t")
        out = json.loads((tmp_path / "bank_t_screened.json").read_text())

        assert out["n_short_answer_unscreened"] == 1
        assert [i["id"] for i in out["items_short_answer_unscreened"]] == ["T-3"]
        total = (len(out["items"]) + out["n_rejected_mcq"]
                 + len(out["items_short_answer_unscreened"]))
        assert total == len(ITEMS), "every candidate item must be accounted for"

    def test_short_answer_not_mixed_into_retained_items(self, tmp_path, monkeypatch):
        """Pooling formats would silently violate the pre-registered stratification."""
        p = _bank(tmp_path, ITEMS)
        _wire(monkeypatch, tmp_path, p, _rows(p, {"T-1": (0.1, True), "T-2": (1.0, False)}))
        F.freeze("t")
        out = json.loads((tmp_path / "bank_t_screened.json").read_text())
        assert all(i["type"] == "mcq" for i in out["items"])

    def test_refuses_incomplete_screen(self, tmp_path, monkeypatch):
        p = _bank(tmp_path, ITEMS)
        _wire(monkeypatch, tmp_path, p, _rows(p, {"T-1": (0.1, True)}))
        with pytest.raises(SystemExit, match="unscreened"):
            F.freeze("t")

    def test_refuses_when_bank_edited_after_screening(self, tmp_path, monkeypatch):
        """Editing an item after seeing its screen result is exactly what makes
        an author-written bank worthless as evidence."""
        p = _bank(tmp_path, ITEMS)
        _wire(monkeypatch, tmp_path, p, _rows(p, {"T-1": (0.1, True), "T-2": (1.0, False)}))
        edited = list(ITEMS)
        edited[1] = dict(edited[1], options=["a", "b", "z"])
        p.write_text(json.dumps({"bank_id": "T", "items": edited}))
        with pytest.raises(SystemExit, match="screen ran against"):
            F.freeze("t")

    def test_invariance_band_brackets_the_threshold(self, tmp_path, monkeypatch):
        p = _bank(tmp_path, ITEMS)
        _wire(monkeypatch, tmp_path, p, _rows(p, {"T-1": (0.10, True), "T-2": (0.90, False)}))
        F.freeze("t")
        out = json.loads((tmp_path / "bank_t_screened.json").read_text())
        lo, hi = out["threshold_invariance_band"]
        assert lo == 0.10 and hi == 0.90
        assert lo <= out["operative_threshold"] <= hi

    def test_frozen_output_is_deterministic(self, tmp_path, monkeypatch):
        """A deposited hash must be reproducible from the same inputs."""
        p = _bank(tmp_path, ITEMS)
        _wire(monkeypatch, tmp_path, p, _rows(p, {"T-1": (0.1, True), "T-2": (1.0, False)}))
        F.freeze("t")
        first = (tmp_path / "bank_t_screened.json").read_bytes()
        F.freeze("t")
        assert (tmp_path / "bank_t_screened.json").read_bytes() == first

    def test_records_source_hash_for_audit(self, tmp_path, monkeypatch):
        p = _bank(tmp_path, ITEMS)
        _wire(monkeypatch, tmp_path, p, _rows(p, {"T-1": (0.1, True), "T-2": (1.0, False)}))
        F.freeze("t")
        out = json.loads((tmp_path / "bank_t_screened.json").read_text())
        assert out["source_bank_sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()


class TestTheFrozenBankIsTiedToTheScreenItCameFrom:
    """`screen_file` recorded a NAME, and a name is not an identity here.

    Run files are append-only and every stage is resumable, so
    `screen_bankA_ol.jsonl` denotes whatever the last run left there. On
    2026-08-17 the re-screen forced by §4.29 rewrote that file while
    `bank_a_screened_ol.json` -- the artefact that gets deposited, and the one
    CLAUDE.md and the spec cite by sha256 -- still described the superseded,
    since-quarantined measurement. Both files were valid; the pointer between
    them had silently retargeted, and nothing in the project compared them.
    """

    def test_freeze_records_the_screen_bytes_it_actually_read(self, tmp_path, monkeypatch):
        p = _bank(tmp_path, ITEMS)
        s = _wire(monkeypatch, tmp_path, p,
                  _rows(p, {"T-1": (0.1, True), "T-2": (1.0, False)}))
        F.freeze("t")
        out = json.loads((tmp_path / "bank_t_screened.json").read_text())
        assert out["screen_sha256"] == hashlib.sha256(s.read_bytes()).hexdigest()

    def test_appending_to_the_screen_invalidates_the_recorded_hash(self, tmp_path, monkeypatch):
        """The whole point: the name still resolves, the bytes do not match."""
        p = _bank(tmp_path, ITEMS)
        s = _wire(monkeypatch, tmp_path, p,
                  _rows(p, {"T-1": (0.1, True), "T-2": (1.0, False)}))
        F.freeze("t")
        out = json.loads((tmp_path / "bank_t_screened.json").read_text())
        with s.open("a") as fh:
            fh.write(json.dumps({"item_id": "T-9", "baseline": 0.5}) + "\n")
        assert out["screen_file"] == s.name, "the pointer by name is unchanged"
        assert out["screen_sha256"] != hashlib.sha256(s.read_bytes()).hexdigest()

    def test_refuses_to_freeze_across_a_study_version_boundary(self, tmp_path, monkeypatch):
        """Two instruments in one file, with every `bank_sha256` matching.

        §4.28 changed a solver's token budget and touched no item, so the bank
        guard saw nothing wrong while half the rows had been measured through a
        truncating budget. Resumability makes this the DEFAULT result of fixing
        a budget mid-screen.
        """
        p = _bank(tmp_path, ITEMS)
        rows = _rows(p, {"T-1": (0.1, True), "T-2": (1.0, False)})
        rows[0]["study_version"] = "2.1.0"
        rows[1]["study_version"] = "2.2.0"
        _wire(monkeypatch, tmp_path, p, rows)
        with pytest.raises(SystemExit, match="mixes study versions"):
            F.freeze("t")

    def test_a_single_version_freezes_and_is_recorded(self, tmp_path, monkeypatch):
        p = _bank(tmp_path, ITEMS)
        rows = _rows(p, {"T-1": (0.1, True), "T-2": (1.0, False)})
        for r in rows:
            r["study_version"] = "2.2.0"
        _wire(monkeypatch, tmp_path, p, rows)
        F.freeze("t")
        out = json.loads((tmp_path / "bank_t_screened.json").read_text())
        assert out["study_version"] == "2.2.0"


class TestAFrozenBankIsNotOverwrittenSilently:
    """"Frozen" has to mean something.

    The write was an unguarded `dst.write_bytes`, so a second freeze replaced
    the deposited artefact in place: same filename, different retention set, no
    record that it had ever held anything else. The file is cited by sha256 in
    CLAUDE.md and in the spec, so a silent overwrite turns those citations into
    references to a file that no longer exists.
    """

    @staticmethod
    def _freeze_two_ways(tmp_path, monkeypatch, **kw):
        p = _bank(tmp_path, ITEMS)
        _wire(monkeypatch, tmp_path, p, _rows(p, {"T-1": (0.1, True), "T-2": (1.0, False)}))
        F.freeze("t")
        first = (tmp_path / "bank_t_screened.json").read_bytes()
        # A different retention set from the same bank: exactly what a re-screen
        # at a corrected budget produces.
        _wire(monkeypatch, tmp_path, p, _rows(p, {"T-1": (0.9, False), "T-2": (0.1, True)}))
        return p, first

    def test_a_differing_re_freeze_is_refused_by_default(self, tmp_path, monkeypatch):
        _, first = self._freeze_two_ways(tmp_path, monkeypatch)
        with pytest.raises(SystemExit, match="already exists and differs"):
            F.freeze("t")
        assert (tmp_path / "bank_t_screened.json").read_bytes() == first, \
            "the refusal must not have written anything"

    def test_force_supersedes_and_keeps_the_old_one(self, tmp_path, monkeypatch):
        _, first = self._freeze_two_ways(tmp_path, monkeypatch)
        F.freeze("t", force=True)
        assert (tmp_path / "bank_t_screened.json").read_bytes() != first
        kept = list(tmp_path.glob("QUARANTINE_bank_t_screened_superseded_*.json"))
        assert len(kept) == 1, f"expected one superseded copy, got {kept}"
        assert kept[0].read_bytes() == first, "the audit trail must be byte-exact"

    def test_an_identical_re_freeze_is_not_an_overwrite(self, tmp_path, monkeypatch):
        """Re-running the same freeze is idempotent, not an error.

        `test_frozen_output_is_deterministic` depends on this, and so does any
        habit of re-running a command to check it.
        """
        p = _bank(tmp_path, ITEMS)
        _wire(monkeypatch, tmp_path, p, _rows(p, {"T-1": (0.1, True), "T-2": (1.0, False)}))
        F.freeze("t")
        F.freeze("t")
        assert not list(tmp_path.glob("QUARANTINE_*"))


class TestRosterIsolation:
    """A frozen bank is only valid for the roster that screened it.

    Baselines are solver-dependent and the gap is large: A-L01-01 measured 0.15
    on the Kiro panel and 0.47 on the Ollama panel. Applying one roster's
    retention list to another's measurements would silently mix instruments.
    """

    def test_freeze_records_the_roster_and_its_own_solvers(self, tmp_path, monkeypatch):
        import json
        p = _bank(tmp_path, ITEMS)
        _wire(monkeypatch, tmp_path, p, _rows(p, {"T-1": (0.1, True), "T-2": (1.0, False)}))
        F.freeze("t", "ol")
        out = json.loads((tmp_path / "bank_t_screened_ol.json").read_text())
        assert out["roster"] == "ol"
        assert out["solvers"] == list(F.C.OL_SOLVERS)
        assert all(m.startswith("ol/") for m in out["solvers"])

    def test_rosters_write_to_different_files(self, tmp_path, monkeypatch):
        p = _bank(tmp_path, ITEMS)
        _wire(monkeypatch, tmp_path, p, _rows(p, {"T-1": (0.1, True), "T-2": (1.0, False)}))
        F.freeze("t", "kr")
        F.freeze("t", "ol")
        assert (tmp_path / "bank_t_screened.json").exists()
        assert (tmp_path / "bank_t_screened_ol.json").exists()

    def test_solvers_field_is_not_overwritten_by_a_duplicate_key(self, tmp_path, monkeypatch):
        """Regression: the output dict briefly carried "solvers" twice, and the
        later roster-blind literal won, so an Ollama freeze recorded the Kiro
        panel. Python accepts duplicate keys silently."""
        import json
        p = _bank(tmp_path, ITEMS)
        _wire(monkeypatch, tmp_path, p, _rows(p, {"T-1": (0.1, True), "T-2": (1.0, False)}))
        F.freeze("t", "ol")
        out = json.loads((tmp_path / "bank_t_screened_ol.json").read_text())
        assert not any(m.startswith("kr/") for m in out["solvers"])


class TestBankPathIsNotDuplicated:
    """Every module must resolve a bank id through run._bank_path.

    A second copy of the naming rule is how `stage_solve` came to read the `kr`
    screen for every roster. `freeze_bank` had the same defect: it rebuilt
    `bank_{id}.json` itself, so bank id `bs` resolved to a `bank_bs.json` that
    does not exist -- and freezing is the step immediately after screening, so
    it would have failed at the end of a multi-hour run rather than the start.
    """

    def test_freeze_resolves_bs_to_the_sample_file(self):
        import inspect
        import freeze_bank as F
        src = inspect.getsource(F.freeze)
        assert "R._bank_path(bank)" in src, (
            "freeze_bank must resolve through run._bank_path, not rebuild the name")
        assert 'C.BANKS_DIR / f"bank_{bank.lower()}.json"' not in src

    def test_no_module_rebuilds_the_bank_filename(self):
        """Catches the pattern returning anywhere it matters."""
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        offenders = []
        for py in root.glob("*.py"):
            if py.name in ("run.py", "rebalance_bank.py", "config.py"):
                continue  # run.py owns the rule; rebalance refuses samples
            text = py.read_text()
            if 'f"bank_{' in text and "_screened" not in text.split('f"bank_{')[1][:40]:
                offenders.append(py.name)
        assert not offenders, f"these rebuild the bank filename: {offenders}"

    def test_rebalance_refuses_a_drawn_sample(self):
        import subprocess
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        r = subprocess.run([sys.executable, str(root / "rebalance_bank.py"),
                            "--bank", "bs", "--check"],
                           capture_output=True, text=True)
        assert r.returncode != 0
        assert "drawn sample" in (r.stdout + r.stderr)


class TestFreezeEndToEndOnOl:
    """Freeze is the step immediately after a multi-hour screen, so a defect
    here surfaces at the worst possible moment. Two have already been found in
    this file: a duplicate `solvers` key that recorded the Kiro panel for an
    Ollama freeze, and a bank path rebuilt locally so `bs` resolved to a file
    that does not exist. This exercises the whole path on a synthetic bank."""

    @staticmethod
    def _setup(tmp_path, monkeypatch, verdicts):
        import hashlib
        import json
        import config as C
        import run as R
        banks = tmp_path / "banks"; banks.mkdir()
        runs = tmp_path / "runs"; runs.mkdir()
        bank = {"bank_id": "T", "items": [
            {"id": f"T-{i}", "type": "mcq", "topic": "t", "stem": "s",
             "options": ["a", "b", "c"], "answer_index": 0, "answer": "a"}
            for i in range(len(verdicts))
        ] + [{"id": "T-SA", "type": "short", "stem": "explain", "answer": "x"}]}
        bp = banks / "bank_t.json"
        bp.write_text(json.dumps(bank))
        sha = hashlib.sha256(bp.read_bytes()).hexdigest()
        monkeypatch.setattr(C, "BANKS_DIR", banks, raising=False)
        monkeypatch.setattr(R, "RUNS", runs, raising=False)
        monkeypatch.setattr(R, "_bank_path", lambda b: banks / f"bank_{b.lower()}.json")
        with (runs / "screen_bankT_ol.jsonl").open("w") as fh:
            for i, (base, ok) in enumerate(verdicts):
                fh.write(json.dumps({
                    "item_id": f"T-{i}", "baseline": base, "passes_headroom": ok,
                    "bank_sha256": sha, "k_per_solver": 20, "roster": "ol",
                    "per_solver": {s: {"hits": 1, "calls": 20, "parsed": 20, "failed": 0}
                                   for s in C.OL_SOLVERS},
                }) + "\n")
        return banks

    def test_records_the_ol_panel_not_the_kr_panel(self, tmp_path, monkeypatch):
        import json
        import config as C
        import freeze_bank as F
        banks = self._setup(tmp_path, monkeypatch, [(0.05, True), (0.12, True), (0.95, False)])
        F.freeze("T", "ol")
        out = json.loads((banks / "bank_t_screened_ol.json").read_text())
        assert out["roster"] == "ol"
        assert out["solvers"] == list(C.OL_SOLVERS)
        assert out["solvers"] != list(C.SOLVERS)

    def test_short_answer_items_are_carried_not_dropped(self, tmp_path, monkeypatch):
        import json
        import freeze_bank as F
        banks = self._setup(tmp_path, monkeypatch, [(0.05, True), (0.95, False)])
        F.freeze("T", "ol")
        out = json.loads((banks / "bank_t_screened_ol.json").read_text())
        assert out["n_short_answer_unscreened"] == 1
        assert len(out["items_short_answer_unscreened"]) == 1
        # and never pooled with the retained MCQ set
        assert all(i["type"] == "mcq" for i in out["items"])

    def test_invariance_band_brackets_the_threshold(self, tmp_path, monkeypatch):
        import json
        import freeze_bank as F
        banks = self._setup(tmp_path, monkeypatch, [(0.05, True), (0.12, True), (0.95, False)])
        F.freeze("T", "ol")
        out = json.loads((banks / "bank_t_screened_ol.json").read_text())
        lo, hi = out["threshold_invariance_band"]
        assert lo <= out["operative_threshold"] <= hi

    def test_refuses_when_the_bank_changed_after_screening(self, tmp_path, monkeypatch):
        import json
        import pytest
        import config as C
        import freeze_bank as F
        banks = self._setup(tmp_path, monkeypatch, [(0.05, True), (0.95, False)])
        # Edit the bank after the screen ran -- the exact integrity failure.
        bp = banks / "bank_t.json"
        d = json.loads(bp.read_text())
        d["items"][0]["options"] = ["a", "b", "z"]
        bp.write_text(json.dumps(d))
        with pytest.raises(SystemExit) as e:
            F.freeze("T", "ol")
        assert "screen ran against" in str(e.value)


class TestPrefixFreeze:
    """Freezing a PARTLY screened bank -- spec 4.25, resolved 2026-08-18.

    Bank B's sample is drawn once in a seed-locked order and screened in that
    order until the retention target is met, so the study needs to freeze 44 of
    60 drawn items. Every guard here exists because the alternative -- screening
    the remaining 16 purely to satisfy a freeze -- costs measurement time that
    buys no data the study uses.
    """

    def _four(self, tmp_path, monkeypatch, screened=("T-1", "T-2")):
        items = [{"id": f"T-{n}", "type": "mcq", "options": ["a", "b", "c"],
                  "answer_index": 0} for n in range(1, 5)]
        p = _bank(tmp_path, items)
        verdicts = {"T-1": (0.1, True), "T-2": (1.0, False),
                    "T-3": (0.1, True), "T-4": (1.0, False)}
        rows = _rows(p, {k: v for k, v in verdicts.items() if k in screened})
        _wire(monkeypatch, tmp_path, p, rows)
        return p

    def test_unscreened_bank_is_refused_without_the_flag(self, tmp_path, monkeypatch):
        self._four(tmp_path, monkeypatch)
        with pytest.raises(SystemExit) as e:
            F.freeze("t")
        assert "--prefix" in str(e.value)

    def test_prefix_freeze_records_what_was_not_screened(self, tmp_path, monkeypatch):
        self._four(tmp_path, monkeypatch)
        F.freeze("t", prefix=True)
        out = json.loads((tmp_path / "bank_t_screened.json").read_text())
        assert out["screened_prefix"] is True
        assert out["n_screened_mcq"] == 2
        assert out["n_unscreened_mcq"] == 2
        assert out["unscreened_mcq_ids"] == ["T-3", "T-4"]
        # The retained set is what the study consumes: only screened items.
        assert [i["id"] for i in out["items"]] == ["T-1"]

    def test_a_non_prefix_subset_is_refused(self, tmp_path, monkeypatch):
        """An arbitrary screened SUBSET is not a stratified sample.

        Every prefix of the drawn order is stratified, which is the whole
        argument for screening part of it; a subset with a hole carries no such
        guarantee, and once the verdicts are in a dict the two look identical.
        """
        self._four(tmp_path, monkeypatch, screened=("T-1", "T-3"))
        with pytest.raises(SystemExit) as e:
            F.freeze("t", prefix=True)
        assert "prefix of the drawn order" in str(e.value)

    def test_a_complete_screen_is_not_marked_as_a_prefix(self, tmp_path, monkeypatch):
        self._four(tmp_path, monkeypatch, screened=("T-1", "T-2", "T-3", "T-4"))
        F.freeze("t", prefix=True)
        out = json.loads((tmp_path / "bank_t_screened.json").read_text())
        assert out["screened_prefix"] is False
        assert out["n_unscreened_mcq"] == 0


class TestBankChangedSinceScreen:
    """Bytes vs measurement: `answer_forms` repairs change one and not the other."""

    def _wire_sig(self, tmp_path, monkeypatch, screen_items, bank_items):
        """Screen recorded against `screen_items`; bank now holds `bank_items`."""
        p = _bank(tmp_path, screen_items)
        rows = _rows(p, {"T-1": (0.1, True), "T-2": (1.0, False)})
        for r in rows:
            r["bank_measurement_sig"] = F.C.measurement_signature(screen_items)
        p.write_text(json.dumps({"bank_id": "T", "items": bank_items}))
        _wire(monkeypatch, tmp_path, p, rows)
        return p

    def test_metadata_only_change_still_freezes(self, tmp_path, monkeypatch):
        """`author_forms` rewrote answer_forms on both banks AFTER screening.

        The screen never reads that field and `measurement_signature` excludes
        it, but every bank file's sha256 moved -- so a byte-only guard refuses a
        bank whose verdicts are entirely valid.
        """
        before = [{"id": "T-1", "type": "mcq", "options": ["a", "b", "c"],
                   "answer_index": 0, "answer_forms": ["a"]},
                  {"id": "T-2", "type": "mcq", "options": ["a", "b", "c"],
                   "answer_index": 1}]
        after = [dict(before[0], answer_forms=["a", "alpha"]), dict(before[1])]
        self._wire_sig(tmp_path, monkeypatch, before, after)
        F.freeze("t")
        out = json.loads((tmp_path / "bank_t_screened.json").read_text())
        assert out["metadata_changed_since_screen"] is True
        assert out["source_bank_sha256"] != out["source_bank_sha256_at_screen"][0]

    def test_a_changed_item_is_still_refused(self, tmp_path, monkeypatch):
        before = [{"id": "T-1", "type": "mcq", "options": ["a", "b", "c"],
                   "answer_index": 0},
                  {"id": "T-2", "type": "mcq", "options": ["a", "b", "c"],
                   "answer_index": 1}]
        after = [dict(before[0], options=["a", "b", "z"]), dict(before[1])]
        self._wire_sig(tmp_path, monkeypatch, before, after)
        with pytest.raises(SystemExit) as e:
            F.freeze("t")
        assert "signature does NOT match" in str(e.value)
