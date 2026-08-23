"""`measure_arm.sh` must switch on the EXIT CODE, not on the report's prose.

An inadequate budget sample prints `recommended=` just as a good one does
(spec 4.57), so a wrapper that greps stdout for it calls a one-item probe a
success.  The three codes mean three different things and two of them need
opposite responses, so this exercises the wrapper against a stub that returns
each one.
"""
import shutil
import subprocess
import textwrap
from pathlib import Path

OPS = Path(__file__).resolve().parents[1] / "ops"
SCRIPT = OPS / "measure_arm.sh"


def _harness(tmp_path, rc):
    """A fake harness whose measure_budgets.py exits with `rc`."""
    (tmp_path / "ops" / "logs").mkdir(parents=True)
    shutil.copy(SCRIPT, tmp_path / "ops" / "measure_arm.sh")
    (tmp_path / "measure_budgets.py").write_text(textwrap.dedent(f"""
        import sys
        # An inadequate sample prints this too -- that is the whole point.
        print("RAISE gpt-oss:120b: n=4 max=605 recommended=1815")
        sys.exit({rc})
    """))
    return tmp_path / "ops" / "measure_arm.sh"


def _run(script, **env):
    e = {"PATH": "/usr/bin:/bin", "WAIT": "0", "MAX_TRIES": "1", **env}
    return subprocess.run(["bash", str(script), "A", "generate"],
                          capture_output=True, text=True, env=e, timeout=60)


def test_rc0_is_done(tmp_path):
    r = _run(_harness(tmp_path, 0))
    assert r.returncode == 0
    assert "adequate sample" in r.stdout


def test_rc1_stops_and_does_not_retry(tmp_path):
    # rc=1 means a number is ready to apply (or the run failed hard).  Both need
    # a human; looping burns hours.  MAX_TRIES is 1 here, so the proof is the
    # message, not the count.
    r = _run(_harness(tmp_path, 1), MAX_TRIES="9")
    assert r.returncode == 1
    assert "STOP" in r.stdout
    assert "attempt 2/9" not in r.stdout, "rc=1 must not be retried"


def test_rc2_retries_then_gives_up(tmp_path):
    r = _run(_harness(tmp_path, 2), MAX_TRIES="3")
    assert r.returncode == 2
    assert "attempt 3/3" in r.stdout, "rc=2 must be retried -- the file grows"
    assert "GIVING UP" in r.stdout


def test_an_unexpected_code_stops_rather_than_guessing(tmp_path):
    r = _run(_harness(tmp_path, 7))
    assert r.returncode == 7
    assert "UNEXPECTED" in r.stdout


def test_the_wrapper_never_reads_the_verdict_out_of_the_prose():
    code = "\n".join(
        ln for ln in SCRIPT.read_text().splitlines()
        if not ln.lstrip().startswith("#")
    )
    # "is there a recommendation?" is the question 4.40 forbids; the answer is
    # in the exit status.
    assert "recommended=" not in code
    assert "grep" not in code
    for want in ('0)', '1)', '2)'):
        assert want in code, f"missing the rc={want[0]} branch"
