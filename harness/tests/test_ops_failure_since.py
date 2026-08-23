"""`ops/failure_since.py`'s self-tests, run by the suite rather than by hand.

The module self-tests when invoked with no arguments, which is better than a
comment but still only runs when someone remembers to run it -- and the whole
point of that file is that a rule with no enforcement gets retyped broken
(§4.51). These import it and assert the same properties, so the suite catches a
regression in the quota/hard-failure distinction.

Both directions matter and they are not symmetric:

  a hard failure read as quota  -> the supervisor waits forever on a crash
  a quota stop read as hard     -> a routine 20-minute wait aborts the run,
                                   which is what cost 3.4 hours (§4.64)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "ops" / "failure_since.py"


@pytest.fixture(scope="module")
def fs():
    spec = importlib.util.spec_from_file_location("failure_since", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


QUOTA = ("=== supervisor launch 1 ===\n"
         "Traceback (most recent call last):\n"
         "  File \"run.py\", line 1, in <module>\n"
         "    raise BackendUnavailable(\n"
         "solvability.BackendUnavailable: leak B-q7: 6/6 calls FAILED. "
         "Error kinds: quota_exhausted=6. QUOTA EXHAUSTED\n")
HARD = ("=== supervisor launch 1 ===\n"
        "Traceback (most recent call last):\n"
        "  File \"run.py\", line 1, in <module>\n"
        "KeyError: 'answer_index'\n")


class TestTheModuleSelfTestsPass:
    def test_selftest_runs_clean(self, fs):
        assert fs.main(["failure_since.py"]) == 0


class TestQuotaIsNotAHardFailure:
    def test_a_quota_traceback_is_not_reported(self, fs):
        """The reason is on the LAST line, so line-wise exclusion keeps the rest.

        `grep -E 'Traceback|BackendUnavailable' | grep -v quota` reports this,
        because it excludes LINES from an event that spans many.
        """
        assert fs.failures(QUOTA) == []

    def test_a_real_traceback_is_reported(self, fs):
        assert fs.failures(HARD)

    def test_both_in_one_log_surfaces_only_the_hard_one(self, fs):
        log = QUOTA + "=== supervisor launch 2 ===\n" + HARD
        assert len(fs.failures(log)) == 1

    def test_a_clean_log_reports_nothing(self, fs):
        assert fs.failures("") == []
        assert fs.failures("[solve] responses=4605 done=120 todo=3768\n") == []


class TestQuotaIsDetectedForItsOwnSake:
    """The second question: was the stop quota? (§4.64)

    Zero progress plus a quota reason is routine; zero progress without one is a
    reproducibly failing unit of work. `failures` answers only the first by
    staying silent, so the supervisor needs this to tell "still closed" from
    "broken" -- without it, a single 20-minute wait ended the run.
    """

    def test_quota_is_recognised(self, fs):
        assert fs.quota_in(QUOTA)

    def test_a_hard_failure_is_not_mistaken_for_quota(self, fs):
        assert not fs.quota_in(HARD)

    def test_silence_is_not_a_quota_stop(self, fs):
        assert not fs.quota_in("")

    @pytest.mark.parametrize("body", [
        "you (admin5) have reached your weekly usage limit",
        "you have reached your session usage limit, upgrade for higher limits",
        "HTTP 429 Too Many Requests",
        "402 You have reached the limit",
        "insufficient_quota",
    ])
    def test_the_observed_quota_bodies_all_count(self, fs, body):
        """Every wording this project has actually seen from either backend."""
        assert fs.quota_in(f"=== launch ===\nraise BackendUnavailable\n{body}\n")


# --- misuse must be loud, and the exit status must agree with the output -----

import subprocess as _sp
import sys as _sys
from pathlib import Path as _P

_SCRIPT = _P(__file__).resolve().parents[1] / "ops" / "failure_since.py"


def _run(*args):
    return _sp.run([_sys.executable, str(_SCRIPT), *args],
                   capture_output=True, text=True, timeout=60)


def test_no_arguments_is_the_documented_selftest():
    r = _run()
    assert r.returncode == 0
    assert "self-tests pass" in r.stdout


def test_one_argument_is_a_usage_error_not_a_selftest(tmp_path):
    # THIS is the bug.  A one-argument call used to fall through to the
    # self-test: it printed "self-tests pass" and exited 0, so `watch_stage.sh`
    # read a passing self-test as "no failure in the log" and its hard-failure
    # branch could never fire.  A misuse that answers "fine" is worse than a
    # crash (4.63: an empty measurement clears every ceiling vacuously).
    log = tmp_path / "x.log"
    log.write_text("Traceback (most recent call last):\nRuntimeError: boom\n")
    r = _run(str(log))
    assert r.returncode == 64, r.stdout + r.stderr
    assert "self-tests pass" not in r.stdout
    assert "usage" in r.stderr.lower()


def test_a_non_numeric_offset_is_a_usage_error(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("clean\n")
    r = _run(str(log), str(log))          # both args swapped/duplicated
    assert r.returncode == 64
    assert "byte offset" in r.stderr


def test_exit_status_agrees_with_the_printed_lines(tmp_path):
    # Two protocols for one question is how the wrong one gets used: supervise.sh
    # reads stdout, the watcher reads both.  They must never disagree.
    clean = tmp_path / "clean.log"
    clean.write_text("[solve] responses=1 done=1 todo=0\n")
    r = _run("0", str(clean))
    assert r.returncode == 0 and r.stdout.strip() == ""

    bad = tmp_path / "bad.log"
    bad.write_text("Traceback (most recent call last):\nRuntimeError: boom\n")
    r = _run("0", str(bad))
    assert r.returncode == 1 and r.stdout.strip() != ""

    quota = tmp_path / "quota.log"
    quota.write_text("Traceback (most recent call last):\n"
                     "BackendUnavailable: Error kinds: quota_exhausted=6. "
                     "QUOTA EXHAUSTED -- this is not an outage.\n")
    r = _run("0", str(quota))
    assert r.returncode == 0 and r.stdout.strip() == "", (
        "a quota stop is routine, so it is neither printed nor signalled"
    )


def test_a_missing_log_is_not_a_failure(tmp_path):
    r = _run("0", str(tmp_path / "nope.log"))
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_auth_only_predicate(fs):
    """A 401 is credentials, not entitlement, so the a-priori rule is "stop --
    looping cannot help". Measured wrong on THIS backend: `auth=6` on 2026-08-22
    at 07:09 and again at 23:28, both self-clearing with no human action, the
    first in ~43 min. Exiting at once cost a 9-hour idle night. The supervisor
    therefore BOUNDS the wait -- and the bound is only safe if the predicate asks
    whether auth explains EVERY hard failure in the slice, never whether one
    appears somewhere (§4.40's forbidden question, in the shape that would
    swallow a genuine crash).
    """
    auth = ("=== supervisor launch 1 ===\n"
            "Traceback (most recent call last):\n"
            "solvability.BackendUnavailable: 6/6 calls FAILED. Error kinds: auth=6.\n")
    hard = ("=== supervisor launch 2 ===\n"
            "Traceback (most recent call last):\n"
            "KeyError: 'item_id'\n")
    assert fs.auth_only(auth)
    assert not fs.auth_only(hard), "a plain crash must not read as auth"
    assert not fs.auth_only(""), "no failure at all is not an auth failure"
    assert not fs.auth_only(QUOTA), "a quota stop is not a hard failure at all"
    assert not fs.auth_only(auth + hard), (
        "auth PLUS a genuine traceback must stop: waiting cannot fix the crash")


def test_the_auth_flag_reports_through_exit_status(tmp_path):
    # The caller branches on the status, never on prose.
    import subprocess, sys
    log = tmp_path / "log"
    log.write_text("=== supervisor launch 1 ===\n"
                   "Traceback (most recent call last):\n"
                   "solvability.BackendUnavailable: 6/6 FAILED. Error kinds: auth=6.\n")
    r = subprocess.run([sys.executable, str(_SRC), "--auth", "0", str(log)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr

    log.write_text("=== supervisor launch 1 ===\n"
                   "Traceback (most recent call last):\nKeyError: 'x'\n")
    r = subprocess.run([sys.executable, str(_SRC), "--auth", "0", str(log)],
                       capture_output=True, text=True)
    assert r.returncode == 1


def test_supervisor_bounds_the_auth_wait_rather_than_looping_or_exiting():
    # Both failure modes are wrong: exiting at once wastes a night on a
    # transient 401; looping forever is what §4.64 forbids on a bad credential.
    src = "\n".join(
        ln for ln in (_SRC.parent / "supervise.sh").read_text().splitlines()
        if not ln.lstrip().startswith("#")
    )
    assert "--auth" in src, "the supervisor does not ask whether the stop was auth"
    assert "MAX_AUTH" in src, "the auth wait is unbounded"
    assert "authstall=0" in src, "the auth counter is never reset after progress"
