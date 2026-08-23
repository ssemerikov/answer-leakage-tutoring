"""The ops scripts must not each carry their own copy of a rule.

Every wrong-but-plausible number this harness has produced came from two
components holding separate copies of one rule, or from a rule written only as
a comment with nothing enforcing it (spec 4.33).  `watch_stage.sh` was lost
once with `/tmp` and rebuilt from prose, and the rebuild reintroduced two
defects the prose stated without their exceptions.  So the rules are asserted
here, against the scripts as files.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

OPS = Path(__file__).resolve().parents[1] / "ops"
COUNT = OPS / "count_rows.py"
WATCH = OPS / "watch_stage.sh"
SUP = OPS / "supervise.sh"


def _src(p):
    """The script's CODE, with whole-line comments dropped.

    These assertions are about what the script DOES.  The header comments
    deliberately quote the very constructs the code must avoid -- `pgrep -f`,
    `wc -l`, `grep Traceback` -- because a rule with no rationale beside it is
    the rule that gets rebuilt wrong (4.51).  Matching raw text would make the
    explanation of a fix indistinguishable from the defect, so the prose that
    justifies each rule would have to be deleted to satisfy the test that
    enforces it.
    """
    return "\n".join(
        ln for ln in p.read_text().splitlines() if not ln.lstrip().startswith("#")
    )


def test_the_ops_scripts_exist_inside_the_project():
    # They lived in /tmp once and were cleaned between sessions, taking three
    # encoded rules with them while this project's notes went on citing them
    # by path.  Operationally load-bearing scripts are not scratch files.
    for p in (COUNT, WATCH, SUP, OPS / "failure_since.py"):
        assert p.exists(), f"{p.name} is cited by path and must exist"


def test_neither_shell_script_keeps_a_private_row_counter():
    # 4.39: progress is successful rows, not `wc -l`.  ONE implementation.
    for p in (WATCH, SUP):
        s = _src(p)
        assert "count_rows.py" in s, f"{p.name} must delegate counting"
        assert "json.loads" not in s, (
            f"{p.name} has its own counting loop again; the stale copy is the "
            "one that reports a stalled stage as working"
        )
        assert not re.search(r"wc\s+-l", s), f"{p.name} sizes progress with wc -l"


def test_watcher_finds_the_supervisor_by_pidfile_not_pattern():
    s = _src(WATCH)
    # The path itself now comes from stage_paths.sh; what matters here is that
    # liveness is a signal to a recorded pid, not a pattern match.
    assert '"$PIDFILE"' in s and "kill -0" in s
    assert 'PIDFILE=' in _src(OPS / "stage_paths.sh")
    # 4.65 in both directions: `pgrep | head -1` can pick a transient wrapper
    # and call a working stage dead; passing the pattern as the watcher's own
    # argument makes the watcher match itself, so death is never reported.
    assert "pgrep" not in s, "liveness must come from the pidfile"


def test_watcher_requires_a_second_signal_before_reporting_death():
    s = _src(WATCH)
    gone = s.index("SUPERVISOR GONE")
    guard = s[:gone]
    assert "if ! alive; then" in guard
    # advancing rows must override a stale pidfile
    assert re.search(r'\$d"?\s*-le\s*0', guard), (
        "death must require BOTH an empty pidfile and no new rows"
    )


def test_watcher_delegates_failure_detection():
    s = _src(WATCH)
    assert "failure_since.py" in s
    # Never retype the filter: a traceback is one event spanning many lines and
    # the quota reason sits only on the last, so line-wise exclusion reports
    # every routine quota stop as a hard failure.
    assert "Traceback" not in s, "failure detection must not be re-implemented"


def test_watcher_reports_both_rates_and_the_stall_counter():
    s = _src(WATCH)
    # A rate over the wall clock counts host suspend as working time; the stall
    # counter is what separates a suspend from a real quota outage.
    assert "cal=" in s and "act=" in s
    assert "stall=" in s


def test_count_rows_selftest_passes():
    r = subprocess.run([sys.executable, str(COUNT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SELFTEST PASSED" in r.stdout


def test_count_rows_is_not_wc_l_on_a_per_attempt_file(tmp_path):
    sys.path.insert(0, str(OPS))
    import count_rows

    p = tmp_path / "attempts.jsonl"
    p.write_text(
        json.dumps({"ok": True, "response": "a"}) + "\n"
        + json.dumps({"ok": False, "error_kind": "quota_exhausted"}) + "\n"
        + json.dumps({"ok": True, "response": ""}) + "\n"
    )
    assert count_rows.count(p) == 1
    assert len(p.read_text().strip().splitlines()) == 3


def test_count_rows_counts_every_solve_record():
    # solve writes one scored response per row and has no `ok` field at all, so
    # asking the ROW which kind it is keeps this right without a stage table --
    # a stage missing from a table is silently exempt (4.36).
    sys.path.insert(0, str(OPS))
    import count_rows

    live = Path(__file__).resolve().parents[2] / "data/runs/solve_bankBS_ol.jsonl"
    if not live.exists():
        return
    n = sum(1 for ln in live.read_text().splitlines() if ln.strip())
    assert count_rows.count(live) == n


# --- the three defects the 2026-08-22 rebuild introduced, each asserted ------
#
# All three share one shape: the check ran, printed something reassuring, and
# could not have detected the thing it was for.  Rebuilding a script from prose
# reproduces the rule without its exceptions, which is how 4.64 and 4.65 got in
# too.  So each is pinned here rather than in a comment.

PATHS = OPS / "stage_paths.sh"


def test_the_stall_counter_is_read_from_the_supervisors_own_log():
    # The counter exists ONLY in SLOG.  Read from LOG it is permanently 0 -- and
    # stall==0 is exactly how a host suspend is distinguished from a quota
    # outage, so the wrong file does not blank the report, it INVERTS it.
    s = _src(WATCH)
    body = s[s.index("stalls()"):s.index("hhmm()")]
    assert '"$SLOG"' in body, "stalls() must read SLOG"
    assert '"$LOG"' not in body, "stalls() must not read run.py's output log"


def test_neither_script_derives_stage_paths_privately():
    # supervise.sh uppercased the bank id; the rebuilt watcher hard-coded a case
    # for A/bs.  They agreed on every bank in use -- which is what a second copy
    # of a rule looks like just before it stops agreeing (4.33).
    assert PATHS.exists()
    for p in (WATCH, SUP):
        s = _src(p)
        assert "stage_paths.sh" in s, f"{p.name} must source the shared paths"
        assert "data/runs/" not in s, f"{p.name} still builds an output path"
        assert "logs/supervise_" not in s, f"{p.name} still builds a log path"


def test_watcher_calls_failure_since_with_an_offset_and_a_log():
    # Called with ONE argument it ran its self-test, printed "self-tests pass"
    # and exited 0, so the hard-failure branch could never fire.  When solve bs
    # died on an auth failure the watcher said "supervisor gone" -- true, and not
    # the reason, which was in the log it had not read.
    s = _src(WATCH)
    m = re.search(r'failure_since\.py"\s+("\$\w+")\s+("\$\w+")', s)
    assert m, "failure_since.py must be called as <offset> <log>"
    assert m.group(1) == '"$base"' and m.group(2) == '"$LOG"'
    assert 'base="${BASE:-' in s, "the offset must be overridable, so this branch is testable"


def test_the_hard_failure_branch_actually_fires(tmp_path):
    # An untested branch here is one that spent an hour unable to fire.
    out = tmp_path / "solve_bankX_ol.jsonl"
    out.write_text('{"item": "a"}\n')
    log = tmp_path / "x.log"
    # NOT an auth failure: that is the bounded path now, tested below.  A crash
    # is the case where looping cannot help and the watch must end.
    log.write_text(
        "=== supervisor launch 2026-08-22T00:00:00 at 1/9 ===\n"
        "Traceback (most recent call last):\n"
        '  File "run.py", line 1, in <module>\n'
        "KeyError: 'solver_max_tokens'\n"
    )
    pid = tmp_path / "x.pid"
    pid.write_text("1\n")          # pid 1 is alive, so liveness cannot mask this
    env = {
        "PATH": "/usr/bin:/bin", "BASE": "0", "PROBE": "1",
        "OUT": str(out), "LOG": str(log), "SLOG": str(log), "PIDFILE": str(pid),
    }
    r = subprocess.run(["bash", str(WATCH), "solve", "X", "9", "1"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "HARD FAILURE" in r.stdout
    # failure_since.failures() reports the LAST line matching FAIL, which for a
    # BackendUnavailable is the informative one but for a bare crash is the
    # traceback banner -- so the exception type does not reach the report.  A
    # known gap, asserted as it is rather than as it should be, because a test
    # that describes behaviour the code does not have is a promise, not a check.
    assert "Traceback" in r.stdout


def test_an_auth_only_stop_is_reported_but_does_not_end_the_watch(tmp_path):
    # 4.33 in the ops scripts: supervise.sh bounds auth stops with MAX_AUTH
    # because on this backend a 401 has twice been transient, but the watcher
    # kept the a-priori rule and exited 3.  On 2026-08-23 it did exactly that at
    # 3788/3888 while the supervisor absorbed the same event in three cycles and
    # carried on -- so the run finished unmonitored.  One bound, in the
    # supervisor; the watcher reports and keeps going.
    out = tmp_path / "solve_bankX_ol.jsonl"
    out.write_text('{"item": "a"}\n')
    log = tmp_path / "x.log"
    log.write_text(
        "Traceback (most recent call last):\n"
        "solvability.BackendUnavailable: leak X: 6/6 calls FAILED (100% > 25%). "
        "Error kinds: auth=6. AUTHENTICATION FAILED on every attempt.\n"
    )
    pid = tmp_path / "x.pid"
    pid.write_text("1\n")
    env = {
        "PATH": "/usr/bin:/bin", "BASE": "0", "PROBE": "1",
        "OUT": str(out), "LOG": str(log), "SLOG": str(log), "PIDFILE": str(pid),
    }
    r = subprocess.run(["bash", str(WATCH), "solve", "X", "9", "1"],
                       capture_output=True, text=True, env=env, timeout=25)
    # Reported -- an auth stop is absorbed, never silent.
    assert "AUTH REFUSED" in r.stdout, r.stdout
    # ...and not as the branch that ends the watch.
    assert "HARD FAILURE" not in r.stdout, r.stdout
    assert r.returncode != 3, r.stdout


def test_the_watcher_does_not_keep_its_own_copy_of_the_auth_bound():
    # The bound is a TIME budget owned by supervise.sh.  A second counter here
    # would be the same rule in two places, which is the defect this whole file
    # exists to prevent -- and the two copies would drift in cycles of
    # different lengths, since the two scripts sleep on different clocks.
    s = _src(WATCH)
    assert "--auth" in s, "the watcher must ask failure_since.py the question"
    # The copy would be a COUNTER, not a mention: naming supervise.sh's bound in
    # a log line is how the reader learns who owns it.
    assert not re.search(r"MAX_AUTH=", s), (
        "the watcher is defining its own auth bound; it belongs to supervise.sh"
    )
    assert not re.search(r"authstall", s), (
        "the watcher is counting auth cycles itself; two counters on two "
        "different sleep clocks drift, and the stale one reports the run wrong"
    )


def test_a_quota_stop_is_not_reported_as_a_hard_failure(tmp_path):
    # 4.51: a traceback is ONE event spanning many lines and the quota reason is
    # only on the last, so a line-wise filter calls every routine quota stop a
    # hard failure.  The watcher must stay quiet here and keep reporting.
    out = tmp_path / "solve_bankX_ol.jsonl"
    out.write_text('{"item": "a"}\n')
    log = tmp_path / "x.log"
    log.write_text(
        "Traceback (most recent call last):\n"
        "solvability.BackendUnavailable: leak X: 6/6 calls FAILED. Error kinds: "
        "quota_exhausted=6. QUOTA EXHAUSTED -- this is not an outage.\n"
    )
    pid = tmp_path / "x.pid"
    pid.write_text("1\n")
    env = {
        "PATH": "/usr/bin:/bin", "BASE": "0", "PROBE": "1",
        "OUT": str(out), "LOG": str(log), "SLOG": str(log), "PIDFILE": str(pid),
    }
    r = subprocess.run(["bash", str(WATCH), "solve", "X", "9", "1"],
                       capture_output=True, text=True, env=env, timeout=25)
    assert "HARD FAILURE" not in r.stdout, r.stdout


def test_the_eta_is_not_computed_from_a_probe_too_small_to_be_a_rate():
    # 4.57 in the watcher: ask "is this a measurement?", never "is there a
    # measurement?".  `act > 0` answers the second question -- and a 300s probe
    # that catches ONE solve row (~6 solver calls apiece) reports 12/h with 100%
    # resolution, which printed `ETA 223.2h run` on a run whose calendar rate
    # said ~24h.  The run-branch must be gated on the SAMPLE, not on the rate.
    s = _src(WATCH)
    assert "MIN_PROBE_ROWS" in s, "the probe needs a sample floor"
    m = re.search(r"if \[ ([^]]+) \]; then\n\s*eta=.*h run", s)
    assert m, "the run-ETA branch is no longer recognisable"
    assert "MIN_PROBE_ROWS" in m.group(1), (
        "the run-ETA is still gated on the rate rather than on the number of "
        f"rows the probe collected: {m.group(1)!r}"
    )


def test_a_one_row_probe_prints_its_sample_and_declines_to_extrapolate(tmp_path):
    # Driven for real: the probe window catches exactly one row.
    out = tmp_path / "out.jsonl"
    out.write_text("")
    log = tmp_path / "log"
    log.write_text("")
    slog = tmp_path / "slog"
    slog.write_text("")
    pidfile = tmp_path / "pid"
    pidfile.write_text(str(__import__("os").getpid()))   # a PID that is alive

    writer = subprocess.Popen(
        ["bash", "-c", f"sleep 3.5; echo '{{\"a\":1}}' >> {out}"]
    )
    env = dict(__import__("os").environ)
    env.update(
        OUT=str(out), LOG=str(log), SLOG=str(slog), PIDFILE=str(pidfile),
        PROBE="3", MIN_PROBE_ROWS="5",
    )
    proc = subprocess.Popen(
        ["bash", str(WATCH), "solve", "bs", "3888", "2"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    try:
        text = proc.communicate(timeout=9)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        text = proc.communicate()[0]
    writer.wait()

    report = [ln for ln in text.splitlines() if "solve bs 1/3888" in ln]
    assert report, f"no progress line was printed:\n{text}"
    line = report[0]
    assert "(1r/3s)" in line, f"the probe's sample size is not printed: {line}"
    assert "h run" not in line, (
        f"a one-row probe was extrapolated into an ETA: {line}"
    )
