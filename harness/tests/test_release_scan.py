"""The licence boundary is a check, not a habit.

Bank B is GFDL 1.3 with Invariant Sections. The deposit publishes locators and
our measurements; the items themselves are never redistributed. That rule was
kept by hand for a day -- CLAUDE.md told its reader to "re-run the scan" while
the scan existed only as something someone had once done -- and the hand version
had a defect nothing could reveal: it compared raw text, so a stem stored in a
.jsonl with escaped newlines was invisible to it. It could only ever find
single-line OPTIONS, which is exactly what it found.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ops"))
import release_scan as rs

HARNESS = Path(__file__).resolve().parent.parent
ROOT = HARNESS.parent


def test_the_script_self_tests_with_no_arguments():
    r = subprocess.run([sys.executable, str(HARNESS / "ops" / "release_scan.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    # In the public copy there is no Bank B to scan against, and the script
    # says so rather than failing or passing quietly.
    assert ("self-tests pass" in r.stdout) or ("SKIPPED" in r.stdout), r.stdout


def test_it_sees_a_stem_whose_newlines_are_escaped(tmp_path):
    # The exact blindness of the hand-run scan.
    prot = rs.protected_strings()
    if not prot:
        pytest.skip("no Bank B in this checkout")
    multiline = max((s for s in prot if "\n" in s), key=len, default=None)
    if multiline is None:
        pytest.skip("no multi-line protected span in this checkout")
    p = tmp_path / "log.jsonl"
    p.write_text(json.dumps({"response": "see: " + multiline}) + "\n",
                 encoding="utf-8")
    assert "\\n" in p.read_text(), "the fixture must store escaped newlines"
    assert multiline not in p.read_text(), "a raw substring test would miss it"
    hits, _, _ = rs.scan([p])
    assert [h[0] for h in hits] == [p]


def test_it_refuses_to_certify_using_an_empty_rule(tmp_path):
    # A scan with nothing to look for reports CLEAN on anything.
    (tmp_path / "empty_banks").mkdir()
    with pytest.raises(SystemExit):
        rs.scan([tmp_path], banks_dir=tmp_path / "empty_banks")


def test_short_options_are_not_protected_expression():
    if not rs.protected_strings():
        pytest.skip("no Bank B in this checkout")
    # '1', 'True', '42' are in every file in the tree; a rule that fires on
    # everything is one its reader learns to ignore.
    assert all(len(s) >= min(rs.MIN_STEM, rs.MIN_OPT) for s in rs.protected_strings())


def test_the_published_tree_carries_no_bank_b_text():
    rel = ROOT / "release"
    if not rel.exists():
        pytest.skip("no staged release tree in this checkout")
    hits, _, n = rs.scan([rel])
    assert n > 0, "nothing scanned"
    assert hits == [], "\n".join(f"{h[0]}: {h[2]!r}" for h in hits)


def test_the_answer_form_table_ships_with_the_bank_not_the_code():
    # Some variants ARE option text. Inline in author_forms.py they reached the
    # public repository while the manuscript said no artefact carried any.
    src = (HARNESS / "author_forms.py").read_text(encoding="utf-8")
    assert "BANK_B_FORMS: dict" not in src, "the table must not be inline again"
    fp = HARNESS / "banks" / "bank_b_answer_forms.json"
    if not fp.exists():
        pytest.skip("the forms file ships with the bank, not with the code")
    assert len(json.loads(fp.read_text(encoding="utf-8"))["forms"]) == 9


def test_both_empty_rule_paths_give_one_explanation_and_two_exit_codes():
    """The self-test and a tree scan meet the same condition; they must not
    describe it differently.

    They used to: the self-test explained that a public checkout has no Bank B
    and exited 0, while `scan` said only "refusing to certify a tree as clean
    using an empty rule" and exited 1. Both were right and only one was useful,
    and the useless one is the one a reader of the deposit hits. Two renderings
    of a rule is the defect this whole harness keeps finding (4.33), so the
    sentence is a constant now. The exit codes stay different on purpose:
    proving nothing is a fair outcome, certifying nothing is not.
    """
    src = Path(rs.__file__).read_text(encoding="utf-8")
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert body.count("NO_BANKS = ") == 1
    assert body.count("NO_BANKS") >= 3, "both paths must use the shared sentence"
    # The sentence spans concatenated literals, so the runtime value never
    # appears verbatim in the source and `body.replace(rs.NO_BANKS, "")` would
    # remove nothing -- a test that can pass only by accident. Count the phrase
    # instead: once, in the definition.
    assert body.count("nothing to scan") == 1, \
        "a second copy of the explanation has been reintroduced"

    with tempfile.TemporaryDirectory() as td:
        empty = Path(td) / "banks"
        empty.mkdir()
        with pytest.raises(SystemExit) as e:
            rs.scan([td], banks_dir=empty)
        assert rs.NO_BANKS in str(e.value)
