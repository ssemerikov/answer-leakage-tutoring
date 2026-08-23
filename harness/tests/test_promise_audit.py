"""A dropped promise leaves no number to mismatch, so nothing else can find it.

Round 4 of the simulated review found three Method commitments the Results did
not deliver while `verify_numbers` reported 157 verified / 0 missing and the
build was clean (spec 4.52).  The audit that finds them is mechanical, and was
run by hand twice -- a rule with no home, which is the defect this harness keeps
hitting (4.33).  These tests pin the mechanism, not the current verdict.
"""
import json
import subprocess
import sys
from pathlib import Path

import promise_audit as pa

HERE = Path(__file__).resolve().parents[1]


def test_the_live_manuscript_has_no_unadjudicated_promise():
    # THE check: a Method edit that adds a promise-shaped sentence fails here
    # until a human decides whether the Results keep it.
    text = Path(pa.PAPER).read_text()
    r = pa.audit(text, pa.load_ledger())
    assert not r["new"], (
        "unadjudicated promise-shaped sentences: "
        + "; ".join(f"L{p['line']} {p['sentence'][:80]}" for p in r["new"])
    )
    assert not r["broken"], [f"L{p['line']} {w}" for p, w in r["broken"]]
    assert not r["stale"], [k for k, _ in r["stale"]]


def test_every_blocked_promise_records_why():
    # A promise waiting on data that stops being visible is one that gets
    # forgotten; the reason is what makes it actionable later.
    text = Path(pa.PAPER).read_text()
    r = pa.audit(text, pa.load_ledger())
    for p, reason in r["blocked"]:
        assert reason and reason != "(no reason recorded)", f"L{p['line']} has no reason"


def test_a_new_promise_fails_the_audit(tmp_path):
    paper = tmp_path / "p.tex"
    paper.write_text("\\section{Method}\nWe report the widget count.\n")
    r = pa.audit(paper.read_text(), {})
    assert len(r["new"]) == 1
    assert r["new"][0]["section"] == "Method"


def test_a_kept_promise_reopens_when_its_evidence_leaves(tmp_path):
    # 4.52's shape exactly: the Method still promises, the Results no longer
    # deliver, and every number in the file is still correct.
    paper = tmp_path / "p.tex"
    paper.write_text(
        "\\section{Method}\nWe report the widget count.\n\n"
        "\\section{Results}\nThe widget count is 42.\n"
    )
    k = pa.promises(paper.read_text())[0]["key"]
    ledger = {k: {"status": "satisfied", "evidence": "widget count is 42"}}
    assert not pa.audit(paper.read_text(), ledger)["broken"]

    paper.write_text("\\section{Method}\nWe report the widget count.\n\n\\section{Results}\n")
    broken = pa.audit(paper.read_text(), ledger)["broken"]
    assert len(broken) == 1 and "evidence has left" in broken[0][1]


def test_a_ledger_entry_for_deleted_prose_is_stale(tmp_path):
    paper = tmp_path / "p.tex"
    paper.write_text("\\section{Method}\nNothing is claimed here.\n")
    r = pa.audit(paper.read_text(), {"deadbeef": {"status": "satisfied", "evidence": "x"}})
    assert [k for k, _ in r["stale"]] == ["deadbeef"]


def test_the_key_survives_rewrapping_a_paragraph():
    # Re-wrapping is the commonest LaTeX edit; it must not orphan the ledger.
    a = "We report the widget count."
    b = "We  report\nthe widget\ncount."
    assert pa.key(a) == pa.key(" ".join(b.split()))


def test_the_stripper_does_not_eat_an_escaped_percent():
    # `re.sub(r'%.*', '', line)` truncates the sentence at a literal \%, which
    # is how two abstract word counts came out wrong.
    s = pa.strip_latex("We report a rate of 5\\% and we will release the data.")
    assert "release the data" in s


def test_an_unknown_status_is_refused_not_ignored(tmp_path):
    # A typo'd status must not read as "fine": a silently exempt entry is 4.36.
    paper = tmp_path / "p.tex"
    paper.write_text("\\section{Method}\nWe report the widget count.\n")
    k = pa.promises(paper.read_text())[0]["key"]
    r = pa.audit(paper.read_text(), {k: {"status": "satsified"}})
    assert len(r["broken"]) == 1 and "unknown status" in r["broken"][0][1]


def test_cli_exit_status_agrees_with_its_output(tmp_path):
    # The failure_since.py lesson: a caller must not have to parse prose to
    # learn the verdict.
    paper = tmp_path / "p.tex"
    paper.write_text("\\section{Method}\nWe report the widget count.\n")
    ledger = tmp_path / "l.json"
    ledger.write_text("{}")
    r = subprocess.run(
        [sys.executable, str(HERE / "promise_audit.py"),
         "--paper", str(paper), "--ledger", str(ledger)],
        capture_output=True, text=True,
    )
    assert r.returncode == 1 and "NEW" in r.stdout

    k = pa.promises(paper.read_text())[0]["key"]
    ledger.write_text(json.dumps({k: {"status": "not-a-promise"}}))
    r = subprocess.run(
        [sys.executable, str(HERE / "promise_audit.py"),
         "--paper", str(paper), "--ledger", str(ledger)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout
