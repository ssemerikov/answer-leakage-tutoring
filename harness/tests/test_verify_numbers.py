"""`verify_numbers` must hold its fire while a screen is being written.

WHY THIS FILE EXISTS. Every screen-derived figure in the manuscript -- retained
and discarded counts, the baseline ranges, the invariance band, the truncation
correction -- is a property of the COMPLETE set of screened items. The screen
stage is append-only and resumable, so while it runs the file on disk is a valid
prefix and an invalid screen.

On 2026-08-17, during the re-screen forced by spec 4.29, that distinction bit
twice in this one file:

  * The invariance-band assertion computed `min(rejected) - max(retained)` over
    a single row and aborted the whole tool with "the band is 48.00 calls, not
    one" -- a crash, in the one tool whose job is to keep the manuscript and the
    data honest, exactly when the data is in motion.
  * The truncation-correction checks were guarded by `len(both) >= 5`, which a
    five-item prefix satisfies. They reported "shift +0.039" and "most negative
    shift 0.000": numbers of the right shape, computed from five rows, meaning
    nothing.

The second is the more instructive failure. A count threshold cannot express
"complete" -- only the bank size can -- and an arbitrary minimum reads as though
it were a safeguard while admitting exactly the case it was meant to exclude.

A partial screen must therefore be reported as PENDING, never as a value and
never as a crash.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402
import verify_numbers as V  # noqa: E402


def _n_mcq() -> int:
    items = json.loads((C.BANKS_DIR / "bank_a.json").read_text(encoding="utf-8"))["items"]
    return sum(1 for i in items if i.get("type") == "mcq")


def _row(item_id: str, baseline: float, passes: bool,
         parsed: float | None = None) -> dict:
    # `parsed_fraction` must VARY across rows: `_corr` divides by the product of
    # the standard deviations, so a fixture with one repeated value raises
    # ZeroDivisionError from inside the tool rather than testing it. Derived
    # from the baseline so every row differs.
    return {
        "item_id": item_id, "baseline": baseline, "passes_headroom": passes,
        "chance": 1 / 3,
        "parsed_fraction": parsed if parsed is not None else 0.90 + baseline / 20,
        "n_failed_calls": 0,
        "k_per_solver": 20, "error_kinds": {},
        "per_solver": {f"ol/s{i}": {"hits": 0, "calls": 20, "parsed": 20,
                                    "failed": 0} for i in range(3)},
    }


@pytest.fixture
def runs(tmp_path, monkeypatch):
    d = tmp_path / "runs"
    d.mkdir()
    monkeypatch.setattr(V, "RUNS", d)
    monkeypatch.setattr(V, "PAPER", tmp_path / "paper.tex")
    (tmp_path / "paper.tex").write_text("nothing here", encoding="utf-8")
    # `checks()` returns early unless the kr screen is present.
    (d / "screen_bankA.jsonl").write_text(
        "\n".join(json.dumps(_row(f"A-L0{i}-01", 0.1 * i, i < 3))
                  for i in range(1, 4)),
        encoding="utf-8")
    return d


def _labels(pending_only=False):
    out = []
    for label, literal, note in V.checks():
        if pending_only and literal is not None:
            continue
        out.append((label, literal, note))
    return out


class TestPartialScreenIsPending:

    def test_a_one_row_screen_does_not_crash(self, runs):
        """The invariance-band assertion must not fire on a prefix."""
        (runs / "screen_bankA_ol.jsonl").write_text(
            json.dumps(_row("A-L01-01", 0.20, True)) + "\n", encoding="utf-8")
        V.checks()   # must not raise

    def test_a_one_row_screen_is_reported_pending(self, runs):
        (runs / "screen_bankA_ol.jsonl").write_text(
            json.dumps(_row("A-L01-01", 0.20, True)) + "\n", encoding="utf-8")
        pend = [(l, n) for l, lit, n in V.checks() if lit is None]
        assert any("IN PROGRESS" in n for _, n in pend), pend

    def test_the_pending_note_names_the_progress(self, runs):
        (runs / "screen_bankA_ol.jsonl").write_text(
            json.dumps(_row("A-L01-01", 0.20, True)) + "\n", encoding="utf-8")
        note = next(n for l, lit, n in V.checks()
                    if lit is None and "IN PROGRESS" in n)
        assert f"1/{_n_mcq()}" in note, note

    def test_no_screen_derived_value_is_emitted_from_a_prefix(self, runs):
        """The real requirement: a prefix yields NO numbers, not wrong ones.

        Five rows is the size that defeated the old `len(both) >= 5` guard, so
        it is the size worth pinning.
        """
        rows = [_row(f"A-L0{i}-01", 0.1 * i, i < 3) for i in range(1, 6)]
        (runs / "screen_bankA_ol.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        emitted = [l for l, lit, _ in V.checks() if lit is not None]
        for banned in ("primary: n retained", "retained baseline range",
                       "discarded baseline range", "shift, >=5% truncated",
                       "most negative shift"):
            assert banned not in emitted, (
                f"{banned!r} was computed from a 5-item prefix")

    def test_a_count_threshold_is_not_used_as_a_completeness_test(self):
        """Guard the guard: `len(...) >= <small int>` must not gate these.

        The defect was not a wrong number but a wrong KIND of test. If someone
        reintroduces an arbitrary minimum, say so here rather than waiting for
        it to admit another prefix.
        """
        import inspect
        import re
        src = inspect.getsource(V.checks)
        # Strip comments first: this file explains the removed guard by quoting
        # it, and a naive substring search matches the explanation. Test the
        # CODE, not the prose about the code.
        code = "\n".join(l.split("#")[0] for l in src.splitlines())
        hits = re.findall(r"len\([^)]*\)\s*>=\s*\d+", code)
        assert not hits, (
            f"count threshold(s) {hits} gate a completeness check; a small "
            f"integer cannot express \"complete\" -- compare against the bank's "
            f"mcq count instead")

    def test_a_complete_screen_does_emit_its_values(self, runs):
        """The guard must not be so strict that a finished screen stays pending.

        Uses the REAL Bank A screen when one is on disk, rather than a synthetic
        one. `checks()` carries several assertions about genuine measurements --
        that the retained and rejected sets are separated by exactly one solver
        call, that every solver scores above chance over all candidates -- and a
        fabricated screen satisfying all of them would be a reimplementation of
        the data, not a test. When no complete screen exists (mid re-run, as on
        2026-08-17) there is nothing to assert and the test skips.
        """
        real = C.ROOT / "data" / "runs" / "screen_bankA_ol.jsonl"
        if not real.exists():
            pytest.skip("no Bank A screen on disk")
        rows = [l for l in real.read_text(encoding="utf-8").splitlines() if l.strip()]
        if len(rows) < _n_mcq():
            pytest.skip(f"screen incomplete on disk ({len(rows)}/{_n_mcq()})")
        (runs / "screen_bankA_ol.jsonl").write_text(
            "\n".join(rows) + "\n", encoding="utf-8")
        emitted = [l for l, lit, _ in V.checks() if lit is not None]
        assert "primary: n retained" in emitted
        return

    def _unused(self, runs):
        n = _n_mcq()
        # The band assertion encodes a property of the REAL bank: the highest
        # retained and lowest rejected baselines differ by exactly one solver
        # call (1/60 here). A fixture that ignores it trips an assertion about
        # Bank A while testing the completeness guard, so reproduce it.
        top_kept = 0.400
        rows = [_row(f"A-L{i:02d}-01", 0.10 + i / 400, True) for i in range(1, n - 1)]
        rows.append(_row(f"A-L{n - 1:02d}-01", top_kept, True))
        rows.append(_row(f"A-L{n:02d}-01", top_kept + 1 / 60, False))
        (runs / "screen_bankA_ol.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        emitted = [l for l, lit, _ in V.checks() if lit is not None]
        assert "primary: n retained" in emitted


class TestFrozenBankIsCheckedAgainstItsScreen:
    """The deposited bank and the screen under it are two copies of one decision.

    `run.screened_items` derives retention live from the screen jsonl;
    `freeze_bank` derives it again into `bank_a_screened_ol.json`, which is what
    gets deposited and what CLAUDE.md and the spec cite by sha256. Nothing
    compared them. On 2026-08-17 they disagreed -- the frozen bank described a
    screen since quarantined for the minimax truncation defect, while the file
    it named was being rewritten by the re-screen. `screen_file` recorded a
    NAME, and run files are append-only, so the pointer stayed valid while its
    target changed underneath it.
    """

    @pytest.fixture
    def wired(self, tmp_path, monkeypatch):
        runs = tmp_path / "runs"
        runs.mkdir()
        banks = tmp_path / "banks"
        banks.mkdir()
        # Carry the REAL bank_a.json across: `checks()` sizes the completeness
        # gate from its mcq count, and a synthetic bank would move that gate
        # without any test saying so.
        (banks / "bank_a.json").write_bytes((C.BANKS_DIR / "bank_a.json").read_bytes())
        monkeypatch.setattr(V, "RUNS", runs)
        monkeypatch.setattr(V, "BANKS", banks)
        monkeypatch.setattr(V, "PAPER", tmp_path / "paper.tex")
        (tmp_path / "paper.tex").write_text("nothing here", encoding="utf-8")
        (runs / "screen_bankA.jsonl").write_text(
            "\n".join(json.dumps(_row(f"A-L0{i}-01", 0.1 * i, i < 3))
                      for i in range(1, 4)),
            encoding="utf-8")
        screen = runs / "screen_bankA_ol.jsonl"
        screen.write_text(json.dumps(_row("A-L01-01", 0.20, True)) + "\n",
                          encoding="utf-8")
        return banks, screen

    @staticmethod
    def _freeze(banks, screen, **over):
        import hashlib
        rec = {"bank_id": "A", "roster": "ol", "n_retained_mcq": 15,
               "screen_file": screen.name,
               "screen_sha256": hashlib.sha256(screen.read_bytes()).hexdigest()}
        rec.update(over)
        (banks / "bank_a_screened_ol.json").write_text(json.dumps(rec),
                                                       encoding="utf-8")

    def _note(self):
        return next((n for l, lit, n in V.checks()
                     if l == "frozen bank <-> screen"), None)

    def _value(self):
        return next((lit for l, lit, n in V.checks()
                     if l == "frozen bank <-> screen"), "absent")

    def test_matching_hashes_verify(self, wired):
        banks, screen = wired
        self._freeze(banks, screen)
        assert self._value() == "match"

    def test_a_rewritten_screen_is_reported_as_drift(self, wired):
        """The live case: the name still resolves, the bytes have moved."""
        banks, screen = wired
        self._freeze(banks, screen)
        screen.write_text(json.dumps(_row("A-L01-01", 0.55, False)) + "\n",
                          encoding="utf-8")
        assert self._value() is None
        assert "DRIFT" in self._note()

    def test_drift_is_reported_even_though_the_screen_is_incomplete(self, wired):
        """Deliberately NOT gated on completeness.

        A one-row screen is a prefix, and every screen-derived FIGURE is
        withheld for it. But a deposited artefact disagreeing with the data
        under it is a fact about provenance, not a statistic, and mid-re-screen
        is precisely when it is true. Gating it behind completeness would
        silence the check for the entire window in which it matters.
        """
        banks, screen = wired
        self._freeze(banks, screen)
        screen.write_text(json.dumps(_row("A-L01-01", 0.55, False)) + "\n",
                          encoding="utf-8")
        labels = {l for l, lit, _ in V.checks() if lit is not None}
        assert "primary: n retained" not in labels, "prefix figures still withheld"
        assert "DRIFT" in self._note(), "but drift is still reported"

    def test_a_legacy_frozen_bank_says_it_cannot_be_checked(self, wired):
        """A missing field must not read as agreement.

        The frozen bank in the repo on 2026-08-17 predates `screen_sha256`, so
        the honest report is "unverifiable", not "match" and not "drift".
        """
        banks, screen = wired
        self._freeze(banks, screen)
        rec = json.loads((banks / "bank_a_screened_ol.json").read_text())
        del rec["screen_sha256"]
        (banks / "bank_a_screened_ol.json").write_text(json.dumps(rec), encoding="utf-8")
        assert self._value() is None
        assert "cannot be checked" in self._note()

    def test_no_frozen_bank_means_no_check_rather_than_a_failure(self, wired):
        banks, screen = wired
        assert not (banks / "bank_a_screened_ol.json").exists()
        assert self._value() == "absent"


class TestSupersededValuesAreReportedAbsent:
    """A "present" check cannot see a stale COPY of the same quantity.

    On 2026-08-17 the solver table was updated to the re-screened values while a
    sentence two paragraphs above still read "from 0.097 to 0.337". Every check
    passed: 0.096 and 0.319 were now in the table, and nothing asked whether the
    superseded pair was still in the file. The module docstring names this
    limitation ("drift, not misplacement"); the `superseded` list closes the
    half of it that can be closed.

    The subtle half is the false positive. `0.698` was gpt-oss's pre-re-screen
    all-40 accuracy AND is minimax's correct one, in the same table. A check
    that flags it teaches the reader to ignore the check.
    """

    def test_a_superseded_literal_still_in_the_file_is_reported(self, tmp_path, monkeypatch, capsys):
        import verify_numbers as V2
        monkeypatch.setattr(V2, "PAPER", tmp_path / "p.tex")
        (tmp_path / "p.tex").write_text("accuracy ran from 0.097 to 0.337 here",
                                        encoding="utf-8")
        monkeypatch.setattr(V2, "checks", lambda: [])
        monkeypatch.setattr(sys, "argv", ["verify_numbers.py"])
        rc = V2.main()
        out = capsys.readouterr().out
        assert "STALE" in out and "0.097" in out
        assert rc == 1, "a stale value must fail the run, not just print"

    def test_a_superseded_literal_that_is_now_correct_elsewhere_is_not_reported(
            self, tmp_path, monkeypatch, capsys):
        """The false positive that would train readers to ignore this."""
        import verify_numbers as V2
        monkeypatch.setattr(V2, "PAPER", tmp_path / "p.tex")
        (tmp_path / "p.tex").write_text("minimax scores 0.698 over all 40",
                                        encoding="utf-8")
        # `checks()` is the authority on what is currently right.
        monkeypatch.setattr(V2, "checks",
                            lambda: [("minimax: cond. acc (all 40)", "0.698", "table")])
        monkeypatch.setattr(sys, "argv", ["verify_numbers.py"])
        rc = V2.main()
        out = capsys.readouterr().out
        assert "STALE" not in out, out
        assert rc == 0

    def test_checks_is_evaluated_once(self, tmp_path, monkeypatch):
        """Guard against the obvious regression: calling `checks()` twice would
        re-read every run file and could report against two different states if
        a stage is mid-write."""
        import verify_numbers as V2
        monkeypatch.setattr(V2, "PAPER", tmp_path / "p.tex")
        (tmp_path / "p.tex").write_text("nothing", encoding="utf-8")
        calls = {"n": 0}

        def counting():
            calls["n"] += 1
            return []

        monkeypatch.setattr(V2, "checks", counting)
        monkeypatch.setattr(sys, "argv", ["verify_numbers.py"])
        V2.main()
        assert calls["n"] == 1, f"checks() ran {calls['n']} times"


class TestTruncationReachableVerdictsAreCaught:
    """The invariance band is only a fact about a partition that is real.

    A failed call is scored as a miss, so truncation can only depress a
    baseline -- which pushes items INTO the retained set and WIDENS the band
    quoted as evidence that the screen is robust. A defect that flatters the
    headline number is the kind this project keeps finding, so the band and the
    bound are checked together.

    These fixtures write a COMPLETE screen (the real bank's MCQ count), because
    the checks under test sit behind the completeness gate -- a three-row
    fixture is reported PENDING and never reaches them.
    """

    def _complete(self, runs):
        """40 rows: 14 retained well below threshold, 26 rejected well above."""
        n_mcq = sum(1 for i in json.loads(
            (V.BANKS / "bank_a.json").read_text(encoding="utf-8")
        )["items"] if i.get("type") == "mcq")
        rows = []
        for i in range(n_mcq):
            retained = i < 14
            # Spread the baselines so `_corr` sees variance and the band is
            # a clean gap rather than a coincidence of the fixture.
            b = 0.05 + 0.02 * i if retained else 0.55 + 0.01 * (i - 14)
            r = _row(f"A-L{i:02d}-01", round(b, 4), retained)
            # `_row` hardcodes `hits: 0`, which trips the unrelated
            # screen-validity assertion (a panel that never picks the key
            # cannot support the argument the screen rests on). Make the
            # per-solver hits agree with the baseline the row claims, so this
            # fixture exercises the truncation bound and nothing else.
            for sv in r["per_solver"]:
                r["per_solver"][sv]["hits"] = round(b * 20)
            rows.append(r)
        return rows

    def _write(self, runs, rows):
        (runs / "screen_bankA_ol.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    def test_a_complete_clean_screen_passes(self, runs):
        self._write(runs, self._complete(runs))
        V.checks()  # must not raise

    def test_a_reachable_retained_verdict_aborts(self, runs):
        rows = self._complete(runs)
        # The tightest retained row, given enough failed calls to cross.
        rows[13]["n_failed_calls"] = 20      # 20/60 = 0.333 of possible lift
        self._write(runs, rows)
        with pytest.raises(AssertionError, match="reachable by"):
            V.checks()

    def test_failures_too_few_to_cross_do_not_abort(self, runs):
        rows = self._complete(runs)
        rows[13]["n_failed_calls"] = 1       # 1/60 cannot span the distance
        self._write(runs, rows)
        V.checks()  # must not raise

    def test_failures_on_a_REJECTED_item_do_not_abort(self, runs):
        """Correction only pushes a rejected item further above the threshold."""
        rows = self._complete(runs)
        rows[20]["n_failed_calls"] = 59
        self._write(runs, rows)
        V.checks()  # must not raise
