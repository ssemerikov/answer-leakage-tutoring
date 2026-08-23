"""Every script CLAUDE.md names by path must exist, or be marked as gone.

On 2026-08-22 this file cited FOUR scripts by path that the `/tmp` cleanup had
deleted -- `watch_stage.sh`, `measure_arm.sh`, `run_pipeline.sh`,
`watch_pipeline.sh` -- and one of them backed a load-bearing instruction ("use
`measure_arm.sh`, never a bare `measure_budgets.py` in a loop").  An instruction
that points at a missing file does not merely fail to help: it sends the next
session to do the thing the instruction forbids, because the alternative it
names cannot be run.

There is no git repo here, so nothing else notices a path going stale.  This is
the cheapest check that does, and it is the same mechanism as
`verify_numbers`' `superseded` list: a deliberate exception, written next to the
claim it excuses, rather than a silently relaxed rule.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "CLAUDE.md"
ROOTS = [ROOT, ROOT / "harness", ROOT / "harness/ops", ROOT / "harness/tests"]

# A citation may name a file that is deliberately absent -- but only if the
# paragraph SAYS SO.  Keep these narrow: a vague marker turns the guard off.
GONE = ("gone", "was lost", "is not being rebuilt", "does not exist")

NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:sh|py)")


def unresolved(text) -> list:
    """Cited script paths that neither resolve nor sit beside an excuse.

    The excuse is looked for in the PARAGRAPH, never the document: searched
    document-wide, one "gone" anywhere would excuse every stale path in the
    file, which is the failure mode of every global exemption in this harness
    (a stage missing from a table is silently exempt -- spec 4.36).
    """
    out = []
    for para in text.split("\n\n"):
        excused = any(m in para.lower() for m in GONE)
        for name in set(NAME.findall(para)):
            if any((r / name).exists() for r in ROOTS):
                continue
            if excused:
                continue
            out.append(name)
    return out


def test_every_cited_script_exists_or_is_marked_gone():
    unresolved_names = unresolved(DOC.read_text())
    assert not unresolved_names, (
        "CLAUDE.md names scripts that do not exist: "
        + ", ".join(sorted(set(unresolved_names)))
        + ". Rebuild them, correct the path, or say in the same paragraph that "
          "the file is gone -- an instruction pointing at a missing file sends "
          "the next session to do the thing it forbids."
    )


def test_the_check_would_actually_fire():
    # A guard is only as good as its predicate (spec 4.40).  Prove this one
    # rejects a citation of a file that is not there.
    bad = "Run `ops/definitely_not_a_real_script.sh` before screening."
    assert unresolved(bad) == ["ops/definitely_not_a_real_script.sh"]


def test_an_excuse_covers_its_own_paragraph_and_no_other():
    doc = (
        "`ops/old_thing.sh` was lost with `/tmp` and is not being rebuilt."
        "\n\n"
        "Run `ops/other_thing.sh` before screening."
    )
    assert unresolved(doc) == ["ops/other_thing.sh"], (
        "the excuse must not leak into neighbouring paragraphs"
    )


def test_the_scripts_the_notes_tell_you_to_run_are_all_present():
    # The four that were missing, named explicitly: a regression here is the
    # exact failure this file exists to prevent.
    for rel in ("ops/supervise.sh", "ops/watch_stage.sh", "ops/measure_arm.sh",
                "ops/failure_since.py", "ops/count_rows.py"):
        assert (ROOT / "harness" / rel).exists(), rel
