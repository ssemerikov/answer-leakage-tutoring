"""Stage-2 sizing must not be able to recommend a second draw.

WHY THIS FILE EXISTS. `size_bank_b.py --n-max` defaulted to 185 -- every item in
Bank B -- and that default outlived the decision it predated. On 2026-08-16 a
60-item sample was drawn under seed (spec 4.14) and frozen to
`banks/bank_b_sample.json`; from then on the pilot screened the SAMPLE, not the
bank. The cap is used as `min(n_max - b_n, ...)`, so with n_max=185 the tool
could return "screen 100 more" when only 60 items exist in the sample.

Acting on that means screening un-drawn Bank B items. That is a second draw, and
proportional allocation does not nest -- a draw at a different n is a DIFFERENT
sample, not a larger one (measured: draws of 20 and 45 share 10 items). The
number would have looked entirely reasonable, which is the failure mode this
project keeps meeting: a plausible figure produced by two components holding
different copies of one rule.
"""

import json
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402
import size_bank_b as S  # noqa: E402

HARNESS = Path(__file__).resolve().parent.parent
SCRIPT = HARNESS / "size_bank_b.py"


def _require_screens() -> None:
    """Skip when the input data is absent, instead of failing.

    These tests exercise the sizing tool's GUARDS, not its arithmetic, but two
    of them drive a full successful run and so need a Bank A screen on disk.
    When the screens are quarantined -- as they were on 2026-08-17 while the
    budget defect of spec 4.29 was corrected -- the tool exits 1 with "no Bank A
    screen", which is right, and the tests failed for a reason that had nothing
    to do with what they check. A data-dependent test must declare its data.
    """
    from config import ROOT
    if not (ROOT / "data" / "runs" / "screen_bankA_ol.jsonl").exists():
        pytest.skip("no Bank A screen on disk (quarantined or not yet run)")


def _drawn() -> int:
    p = C.BANKS_DIR / "bank_b_sample.json"
    if not p.exists():
        pytest.skip("bank_b_sample.json not drawn")
    return len(json.loads(p.read_text(encoding="utf-8"))["items"])


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=str(HARNESS), timeout=120)


class TestRedrawGuard:

    def test_n_max_above_the_drawn_sample_is_refused(self):
        """The whole point: 185 must not be quietly accepted against a bs pilot."""
        n = _drawn()
        r = _run("--target", "40", "--n-max", str(n + 1))
        assert r.returncode != 0, "a cap beyond the sample was accepted"
        msg = r.stdout + r.stderr
        assert "does not nest" in msg, msg[:400]

    def test_the_refusal_names_the_pre_registered_alternative(self):
        """A guard that only forbids invites working around it.

        The pre-registered answer to an unmeetable target is a shorter bank and
        wider intervals -- never more items. The message must say so, or the
        next reader's obvious move is to pass a bigger --n-max.
        """
        r = _run("--target", "40", "--n-max", str(_drawn() + 25))
        msg = r.stdout + r.stderr
        assert "shorter bank" in msg and "wider interval" in msg, msg[:400]

    def test_default_cap_is_the_drawn_sample_not_the_whole_bank(self):
        _require_screens()
        n = _drawn()
        r = _run("--target", "40")
        assert r.returncode == 0, r.stderr[:400]
        assert f"consumable: {n} items" in r.stdout, r.stdout[:400]
        assert "185" not in r.stdout, (
            "the pre-draw Bank B size reached the output; the sample is the "
            "population now")

    def test_recommendation_never_exceeds_what_remains_undrawn(self, tmp_path):
        """Whatever it recommends, screened + recommended <= drawn.

        DRIVEN BY A SYNTHETIC PILOT, NOT THE LIVE RUN. This read the real screen
        file and skipped when no sizing table was printed. On 2026-08-17 21:19
        the retained target was met, the tool correctly switched to "Screen no
        more", and this test began skipping with the message "no pilot rows yet"
        -- against a file holding 44 rows. The guard stopped being exercised at
        the moment the project stopped needing it, which is precisely when a
        silent regression would have gone unnoticed, and the reported reason was
        false. Construct a pilot whose retention leaves a real shortfall, so the
        recommendation is always produced and always checked.
        """
        _require_screens()
        n = _drawn()
        order = [i["id"] for i in json.loads(
            (C.BANKS_DIR / "bank_b_sample.json").read_text(encoding="utf-8"))["items"]]
        # 2/20 retained: a low rate, so the lower Wilson bound demands far more
        # items than the sample holds and the cap has to do the work.
        pilot = tmp_path / "screen_bankBS_ol.jsonl"
        pilot.write_text("".join(
            json.dumps({"item_id": order[k], "passes_headroom": k < 2}) + "\n"
            for k in range(20)), encoding="utf-8")
        r = _run("--target", "40", "--pilot-file", str(pilot))
        assert r.returncode == 0, r.stderr[:400]
        import re
        m = re.search(r"lower bound\s+\(\s*\d+%\): screen (\d+) more", r.stdout)
        assert m is not None, (
            "no sizing table for a pilot with a real shortfall:\n" + r.stdout[:600])
        rec = int(m.group(1))
        # Two labels: "Bank B pilot" while the screen is still inside stage 1,
        # "all screened so far" once it has run past PILOT_N. The invariant is
        # about what has been SCREENED, so match whichever line is present.
        screened = re.search(r"Bank B (?:pilot|all screened so far):\s+\d+/(\d+)",
                             r.stdout)
        assert screened is not None, r.stdout[:400]
        assert rec + int(screened.group(1)) <= n, (
            f"recommended {rec} on top of {screened.group(1)} screened, "
            f"exceeding the {n} drawn")


class TestPilotBoundarySurvivesRunningPastIt:
    """`--limit N` screens N items NOT YET DONE -- it is not a prefix guard.

    Observed 2026-08-17: the supervisor relaunched at done=17 with the original
    `--limit 20`, which selected sample positions 18-37, so the screen walked
    through the 20-item pilot boundary without stopping. The work is legitimate
    (fixed order, consumed in order), but `retention()` read every row and the
    report called the result "Bank B pilot", so the 13/20 already written into
    spec 4.26 and 4.34 would have silently become 14/22 under the same name.
    """

    ORDER = [f"i{k}" for k in range(30)]

    def _file(self, tmp_path, n, keep):
        rows = [{"item_id": self.ORDER[k], "passes_headroom": k in keep}
                for k in range(n)]
        p = tmp_path / "screen_bankBS_ol.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return p

    def test_pilot_slice_is_stable_when_later_items_are_appended(self, tmp_path):
        pilot_keep = set(range(13))
        p = self._file(tmp_path, 20, pilot_keep)
        assert S.retention(p, self.ORDER, S.PILOT_N) == (13, 20)
        # Five further items screened, all retained. Only the pooled figure moves.
        p = self._file(tmp_path, 25, pilot_keep | set(range(20, 25)))
        assert S.retention(p, self.ORDER, S.PILOT_N) == (13, 20)
        assert S.retention(p) == (18, 25), "the pooled figure must still pool"

    def test_the_slice_follows_the_draw_not_the_file(self, tmp_path):
        """A resumed run appends out of draw order; a file slice would be wrong."""
        rows = [{"item_id": "i25", "passes_headroom": False},   # stage-2 item, written first
                {"item_id": "i0", "passes_headroom": True},
                {"item_id": "i1", "passes_headroom": True}]
        p = tmp_path / "screen_bankBS_ol.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        assert S.retention(p, self.ORDER, 2) == (2, 2), (
            "sliced by file position, i25 would have been counted as pilot")

    def test_unknown_items_are_never_counted_into_the_pilot(self, tmp_path):
        rows = [{"item_id": "not-in-sample", "passes_headroom": True},
                {"item_id": "i0", "passes_headroom": True}]
        p = tmp_path / "screen_bankBS_ol.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        assert S.retention(p, self.ORDER, S.PILOT_N) == (1, 1)

    def test_the_recorded_pilot_retention_is_reproducible(self):
        """Pins the 13/20 that spec 4.26 and 4.34 quote, against the real file."""
        _require_screens()
        path = C.ROOT / "data/runs/screen_bankBS_ol.jsonl"
        sample = json.loads((C.BANKS_DIR / "bank_b_sample.json")
                            .read_text(encoding="utf-8"))["items"]
        order = [i["id"] for i in sample]
        assert S.retention(path, order, S.PILOT_N) == (13, 20)


class TestStaleTimingSuppressesHoursNotItems:
    """A timing measured at other token budgets must not silently price a
    decision -- but it must not disable unrelated correctness checks either.

    The first version of this guard aborted the whole tool, which also took out
    the redraw guard: a check about WHICH items may be consumed, independent of
    timing. Refusing to answer a question nobody asked, to protect an estimate,
    is worse than the staleness it prevents.
    """

    def test_hours_are_withheld_when_the_timing_is_stale(self, monkeypatch):
        monkeypatch.setattr(S, "TIMING", S.ScreenTiming(438.0, "0.0.0"))
        assert "unknown" in S.hours(10)

    def test_hours_are_given_when_the_timing_is_fresh(self, monkeypatch):
        monkeypatch.setattr(S, "TIMING", S.ScreenTiming(438.0, C.STUDY_VERSION))
        assert S.hours(10).endswith("h")
        assert "unknown" not in S.hours(10)

    def test_the_measurement_and_its_conditions_cannot_desynchronise(self):
        """The regression this structure exists for.

        These were two module-level constants with a comment saying "bump both
        together". On 2026-08-17 an edit bumped the version string and silently
        failed to bump the number, so the guard compared "2.3.0" to "2.3.0",
        called the timing fresh, and printed hour estimates from the superseded
        132 s -- 3.3x optimistic, unflagged, in the tool built to prevent
        exactly that.

        One value, so a partial update cannot parse.
        """
        assert isinstance(S.TIMING, tuple)
        assert S.TIMING._fields == ("sec_per_item", "measured_at")
        src = (Path(__file__).resolve().parent.parent / "size_bank_b.py").read_text()
        # No free-standing copy may reappear alongside it.
        assert "\nSEC_PER_ITEM = " not in src
        assert "\nSEC_PER_ITEM_MEASURED_AT = " not in src

    def test_the_recorded_timing_is_the_measured_one(self):
        """Pins the actual number, so a future budget change that forgets to
        re-measure shows up as a failing test and not as a cheap estimate."""
        assert S.TIMING == (438.0, "2.3.0")

    def test_the_warning_names_the_tool_that_fixes_it(self, monkeypatch):
        """An error that names a nonexistent remedy is worse than none."""
        monkeypatch.setattr(S, "TIMING", S.ScreenTiming(438.0, "0.0.0"))
        msg = S._timing_warning()
        assert "timing_from_screen.py" in msg
        assert (Path(__file__).resolve().parent.parent
                / "timing_from_screen.py").exists()

    def test_the_remedy_tool_emits_a_line_this_module_accepts(self):
        """The half of the desync the first test could not see.

        `test_..._cannot_desynchronise` guards this module's source. It cannot
        guard the tool that TELLS a reader how to edit this module -- and until
        2026-08-17 that tool printed `SEC_PER_ITEM = ...` plus a separate
        `SEC_PER_ITEM_MEASURED_AT = "..."`, i.e. it instructed the reader to
        hand-write the two-name form that the merge abolished and that the test
        above rejects. Following the measurement tool's own output would have
        recreated the bug AND broken the suite.

        So assert the suggestion is something this module would actually
        accept: the live type name, every field, and no resurrected constant.
        """
        import timing_from_screen as T

        text = T.suggestion(438.0)
        assert "SEC_PER_ITEM" not in text
        assert f"{type(S.TIMING).__name__}(" in text
        assert "UNKNOWN FIELD" not in text, "suggestion has a field it cannot fill"
        assert "438" in text and C.STUDY_VERSION in text

        # It must parse, and rebuild an equal tuple under this module's names.
        stmt = [ln.strip() for ln in text.splitlines()
                if ln.strip().startswith("TIMING = ")][0]
        ns = {type(S.TIMING).__name__: type(S.TIMING)}
        exec(stmt, ns)
        assert ns["TIMING"] == S.ScreenTiming(438.0, C.STUDY_VERSION)

    def test_an_unfillable_field_is_visible_rather_than_fatal(self, monkeypatch):
        """A traceback at the print step would discard a 15-minute measurement.

        If a field is added to ScreenTiming and nobody updates the suggestion,
        the reader must see the gap in the text they are about to paste -- but
        the measured number must still reach them.
        """
        import timing_from_screen as T

        Extended = namedtuple("ScreenTiming", "sec_per_item measured_at bank")
        monkeypatch.setattr(S, "TIMING", Extended(438.0, "2.3.0", "bs"))
        text = T.suggestion(438.0)
        assert "UNKNOWN FIELD 'bank'" in text
        assert "438" in text
