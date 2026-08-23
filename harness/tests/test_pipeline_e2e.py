"""End-to-end walk of screen -> placebo -> generate -> solve -> judge, no network.

WHY THIS FILE EXISTS. The four downstream stages cost hours of paid quota per
run, so a defect in them is discovered late and expensively. Every stage had
unit coverage; none had coverage of the wiring BETWEEN stages, and that is
exactly where the bugs were:

  - `stage_solve` never called `roster(rost)`, so `solvers` and `k` were
    undefined. It would raise NameError on its first response. The stage had
    never successfully run on any roster.
  - `stage_solve` read the screen file as `_jsonl_path("screen", bank)` with no
    roster, so an `--roster ol` run silently differenced Ollama hint arms
    against Kiro baselines. Baselines are solver-dependent (A-L01-01: 0.15 on
    Kiro, 0.47 on Ollama), and `per_solver_leak` would find no solver present in
    both records -- so `by_solver` would be empty and the within-solver analysis
    and the panel-agreement ICC would vanish silently rather than fail.

Both are wiring faults invisible to a unit test of either end. The tests here
run the real stage functions against a stubbed backend and assert on the shape
of what lands on disk, including that `analyse.solver_agreement` can consume it.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analyse as A  # noqa: E402
import backends  # noqa: E402
import config as C  # noqa: E402
import run as R  # noqa: E402


BANK = {
    "bank_id": "T",
    "items": [
        {"id": "T-01", "type": "mcq", "topic": "loops",
         "stem": "What does range(3) yield?", "options": ["0,1,2", "1,2,3", "0,1,2,3"],
         "answer_index": 0, "answer": "0,1,2", "level": 1},
        {"id": "T-02", "type": "mcq", "topic": "strings",
         "stem": "What is len('abc')?", "options": ["2", "3", "4"],
         "answer_index": 1, "answer": "3", "level": 1},
    ],
}


class Scripted:
    """Deterministic stand-in for the chat backend.

    Solver prompts get an option number; generator prompts get tutor prose;
    judge prompts get a verdict. Which is which is decided from the prompt text,
    the same way a real backend would see it.
    """

    def __init__(self, solver_pick=1):
        self.solver_pick = solver_pick
        self.calls = []

    def __call__(self, model, messages, **kw):
        prompt = messages[-1]["content"]
        self.calls.append((model, prompt))
        if "cannot see" in prompt:                 # masked-stem solver prompt
            text = str(self.solver_pick)
        elif "YES" in prompt or "NO" in prompt:    # judge prompt
            text = "NO"
        else:                                      # generator
            text = "Think about where counting starts."
        return backends.CallResult(
            ok=True, model=model, path=kw.get("path", C.PATH_CONTROLLED),
            content=text, status=200, error_kind=None, error_detail=None,
            prompt_tokens=10, completion_tokens=5, cache_hit=False,
            latency_ms=10, temperature=kw.get("temperature", 0.0),
            system_prompt_sha256="test", meta={})


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point the harness at a temp bank and temp run directory."""
    banks = tmp_path / "banks"
    banks.mkdir()
    (banks / "bank_t.json").write_text(json.dumps(BANK), encoding="utf-8")
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(C, "BANKS_DIR", banks, raising=False)
    monkeypatch.setattr(R, "RUNS", runs, raising=False)
    monkeypatch.setattr(R, "_bank_path", lambda b: banks / f"bank_{b.lower()}.json")
    return runs


def _install(monkeypatch, scripted):
    for mod_name in ("solvability", "pipelines", "judge"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "chat"):
            monkeypatch.setattr(mod, "chat", scripted)
    monkeypatch.setattr(backends, "chat", scripted)


class TestSolveRosterWiring:
    """The two regressions, pinned."""

    def test_stage_solve_resolves_its_roster(self):
        """`solvers`/`k`/`path` must come from the roster, not be free names.

        A NameError here means the stage cannot run at all -- which is what the
        code did before this test existed.
        """
        import inspect
        src = inspect.getsource(R.stage_solve)
        assert "roster(rost)" in src, "stage_solve must resolve its roster"
        body = src.split("\n", 1)[1]
        assert 'R["solvers"]' in body or "R['solvers']" in body

    def test_stage_solve_reads_the_screen_for_its_own_roster(self):
        """The screen path must carry `rost`; defaulting to `kr` is the bug."""
        import inspect
        src = inspect.getsource(R.stage_solve)
        assert '_jsonl_path("screen", bank, rost)' in src, (
            "stage_solve must read the screen for ITS OWN roster; without rost "
            "it silently differences against the kr panel's baselines")

    def test_refuses_a_screen_from_a_foreign_panel(self, sandbox, monkeypatch):
        """A screen recorded by a disjoint panel must abort, not produce empties."""
        (sandbox / "screen_bankT_ol.jsonl").write_text(json.dumps({
            "item_id": "T-01", "baseline": 0.1, "passes_headroom": True,
            "per_solver": {"kr/some-other-model": {"hits": 1, "calls": 20, "parsed": 20}},
        }) + "\n", encoding="utf-8")
        (sandbox / "generate_bankT_ol.jsonl").write_text(json.dumps({
            "cell_key": "c1", "item_id": "T-01", "model": "ol/gemma4:cloud",
            "regime": "P0", "solicitation_key": "S0", "ok": True,
            "response": "some hint",
        }) + "\n", encoding="utf-8")
        with pytest.raises(SystemExit) as e:
            R.stage_solve("T", None, C.PATH_CONTROLLED, "ol")
        assert "shares no solver" in str(e.value)

    def test_missing_screen_for_roster_aborts_clearly(self, sandbox):
        (sandbox / "generate_bankT_ol.jsonl").write_text("", encoding="utf-8")
        with pytest.raises(SystemExit) as e:
            R.stage_solve("T", None, C.PATH_CONTROLLED, "ol")
        assert "no screen for roster" in str(e.value)


class TestStagesProduceConsumableRecords:

    def test_screen_then_placebo_write_per_solver_blocks(self, sandbox, monkeypatch):
        _install(monkeypatch, Scripted(solver_pick=1))
        R.stage_screen("T", 2, None, C.PATH_CONTROLLED, "ol")
        rows = [json.loads(l) for l in
                (sandbox / "screen_bankT_ol.jsonl").read_text().splitlines() if l.strip()]
        assert len(rows) == 2
        for r in rows:
            assert set(r["per_solver"]) == set(C.OL_SOLVERS)
            assert r["roster"] == "ol"
            # Scripted solver always answers "1"; T-01's key IS option 1.
            assert r["n_failed_calls"] == 0

    def test_screen_resumes_without_duplicating(self, sandbox, monkeypatch):
        _install(monkeypatch, Scripted(solver_pick=1))
        R.stage_screen("T", 2, 1, C.PATH_CONTROLLED, "ol")
        first = (sandbox / "screen_bankT_ol.jsonl").read_text().splitlines()
        R.stage_screen("T", 2, None, C.PATH_CONTROLLED, "ol")
        rows = [json.loads(l) for l in
                (sandbox / "screen_bankT_ol.jsonl").read_text().splitlines() if l.strip()]
        assert len(first) == 1 and len(rows) == 2
        assert len({r["item_id"] for r in rows}) == 2


class TestBySolverFeedsTheICC:
    """The integration point that the ICC silently depended on."""

    def test_per_solver_leak_shape_is_what_solver_agreement_expects(self):
        import solvability as S
        base = {"per_solver": {
            "ol/a": {"hits": 2, "calls": 20, "parsed": 20},
            "ol/b": {"hits": 4, "calls": 20, "parsed": 20}}}
        hint = {"per_solver": {
            "ol/a": {"hits": 10, "calls": 20, "parsed": 20},
            "ol/b": {"hits": 12, "calls": 20, "parsed": 20}}}
        block = S.per_solver_leak(base, hint)
        rows = [{"by_solver": block} for _ in range(4)]
        res = A.solver_agreement(rows)
        assert res is not None
        assert res.n_raters == 2, "must read per_solver, not the summary keys"

    def test_disjoint_panels_produce_an_empty_block_not_a_wrong_number(self):
        import solvability as S
        base = {"per_solver": {"kr/x": {"hits": 2, "calls": 20, "parsed": 20}}}
        hint = {"per_solver": {"ol/y": {"hits": 9, "calls": 20, "parsed": 20}}}
        block = S.per_solver_leak(base, hint)
        assert block["per_solver"] == {}
        assert block["mean_leak_across_solvers"] is None
        assert A.solver_agreement([{"by_solver": block}] * 4) is None


class TestBankHashIntegrity:
    """The manuscript claims the harness refuses to run when a bank changes.

    That claim was false: stages RECORDED `bank_sha256` per row (and only
    `screen` did even that) but nothing ever compared it. Editing an item
    mid-run produced one file containing two populations, and only
    `freeze_bank` noticed -- at the end of a multi-hour run, not the start.
    """

    def test_every_stage_stamps_the_bank_hash(self):
        import inspect
        import run as R
        for name in ("stage_screen", "stage_placebo", "stage_generate",
                     "stage_solve", "stage_judge"):
            src = inspect.getsource(getattr(R, name))
            assert "bank_sha256" in src, f"{name} does not record the bank hash"

    def test_every_stage_calls_the_guard(self):
        """EVERY stage, enumerated from the module -- not a hand-written list.

        The first version of this test listed four stages by name and omitted
        `stage_generate`, which was the one stage that did not call the guard.
        A test written around the code rather than around the requirement
        documents the bug instead of catching it, and passes while doing so.
        Deriving the list from the module makes that impossible: a new stage is
        covered the moment it exists.
        """
        import inspect
        import run as R
        stages = [n for n in dir(R)
                  if n.startswith("stage_") and n != "stage_status"]
        assert len(stages) >= 5, f"expected 5+ stages, found {stages}"
        for name in stages:
            src = inspect.getsource(getattr(R, name))
            assert "assert_bank_unchanged" in src, f"{name} does not check the hash"

    def test_the_stage_list_is_not_hand_maintained(self):
        """Guard the guard: if someone re-hardcodes the list, say so."""
        import inspect
        src = inspect.getsource(self.test_every_stage_calls_the_guard)
        assert 'startswith("stage_")' in src

    def test_guard_rejects_a_changed_bank(self, tmp_path):
        import json
        import pytest
        import run as R
        f = tmp_path / "screen_bankT_ol.jsonl"
        f.write_text(json.dumps({"item_id": "T-01", "bank_sha256": "a" * 64}) + "\n")
        with pytest.raises(SystemExit) as e:
            R.assert_bank_unchanged(f, "b" * 64, "screen")
        assert "bank changed" in str(e.value)

    def test_guard_allows_the_same_bank(self, tmp_path):
        import json
        import run as R
        f = tmp_path / "screen_bankT_ol.jsonl"
        f.write_text(json.dumps({"item_id": "T-01", "bank_sha256": "a" * 64}) + "\n")
        R.assert_bank_unchanged(f, "a" * 64, "screen")   # must not raise

    def test_guard_is_silent_on_a_fresh_file(self, tmp_path):
        import run as R
        R.assert_bank_unchanged(tmp_path / "nope.jsonl", "a" * 64, "screen")

    def test_guard_survives_a_torn_final_line(self, tmp_path):
        import json
        import run as R
        f = tmp_path / "screen_bankT_ol.jsonl"
        f.write_text(json.dumps({"item_id": "T-01", "bank_sha256": "a" * 64})
                     + "\n{\"item_id\": \"T-02\", trunc")
        R.assert_bank_unchanged(f, "a" * 64, "screen")   # must not raise


class TestStudyVersionIsStamped:
    """A constant change must be visible in the data it produced.

    `config.py` opens by saying every drifting constant is "stamped into every
    output file via `study_manifest()`". That was false: the manifest was built
    in each stage, `bank_sha256` was lifted out of it, and the rest -- including
    `study_version` -- was discarded. No record carried it.

    That gap became load-bearing on 2026-08-17, when `MAX_TOKENS_SOLVER` had to
    change for minimax (spec 4.28). The module's own rule is "bump
    STUDY_VERSION and re-run, do not mix" -- but with no version in any record
    there is nothing to mix *by*: a 2.1.0 screen and a 2.2.0 screen are
    byte-indistinguishable in provenance. Recording the version is what makes
    the rule enforceable rather than aspirational.

    Derived from the module, not hand-listed, for the reason given in
    `test_every_stage_calls_the_guard` above.
    """

    def test_every_stage_stamps_the_study_version(self):
        import inspect
        import run as R
        stages = [n for n in dir(R)
                  if n.startswith("stage_") and n != "stage_status"]
        assert len(stages) >= 5, f"expected 5+ stages, found {stages}"
        for name in stages:
            src = inspect.getsource(getattr(R, name))
            assert "study_version" in src, (
                f"{name} does not stamp study_version; a constant change would "
                f"be invisible in the records it produced")

    def test_the_version_reaches_a_written_record(self, sandbox, monkeypatch):
        import json
        import run as R
        _install(monkeypatch, Scripted(solver_pick=1))
        R.stage_screen("T", 2, 1, C.PATH_CONTROLLED, "ol")
        rows = [json.loads(l) for l in
                (sandbox / "screen_bankT_ol.jsonl").read_text().splitlines()
                if l.strip()]
        assert rows and rows[0].get("study_version") == C.STUDY_VERSION


class TestHintedBudgetGuard:
    """`solve` sends a longer prompt than `screen`, with its own budget.

    A truncated call is scored as a miss in BOTH arms, and `leak_hat` is
    `hinted - baseline`, so hinted-arm truncation DEPRESSES the leak score --
    understating the very effect the study measures, and hardest on the
    responses that provoke the longest reasoning. Nothing checked that the
    hinted arm had ever been calibrated (spec 4.33).
    """

    def _wire(self, sandbox):
        (sandbox / "screen_bankT_ol.jsonl").write_text(json.dumps({
            "item_id": "T-01", "baseline": 0.1, "passes_headroom": True,
            "chance": 0.25, "n_failed_calls": 0, "k_per_solver": 20,
            "per_solver": {"ol/gemma4:cloud": {"hits": 1, "calls": 20,
                                               "parsed": 20, "failed": 0}},
        }) + "\n", encoding="utf-8")
        (sandbox / "generate_bankT_ol.jsonl").write_text(json.dumps({
            "cell_key": "c1", "item_id": "T-01", "model": "ol/gemma4:cloud",
            "regime": "P0", "solicitation_key": "S0", "ok": True,
            "response": "some hint",
        }) + "\n", encoding="utf-8")

    def _budget(self, sandbox, version):
        (sandbox / "budget_T_ol_hinted.jsonl").write_text(json.dumps({
            "bank": "T", "roster": "ol", "arm": "hinted",
            "item_id": "T-01", "model": "ol/gemma4:cloud", "ok": True,
            "completion_tokens": 12, "study_version": version,
        }) + "\n", encoding="utf-8")

    def test_an_unmeasured_hinted_arm_aborts(self, sandbox):
        self._wire(sandbox)
        with pytest.raises(SystemExit) as e:
            R.stage_solve("T", None, C.PATH_CONTROLLED, "ol")
        assert "hinted-arm budget" in str(e.value)

    def test_the_refusal_names_the_command_that_fixes_it(self, sandbox):
        self._wire(sandbox)
        with pytest.raises(SystemExit) as e:
            R.stage_solve("T", None, C.PATH_CONTROLLED, "ol")
        assert "--arm hinted" in str(e.value)

    def test_a_stale_study_version_aborts(self, sandbox):
        """A budget change is exactly what invalidates a budget measurement."""
        self._wire(sandbox)
        self._budget(sandbox, "0.0.1")
        with pytest.raises(SystemExit) as e:
            R.stage_solve("T", None, C.PATH_CONTROLLED, "ol")
        assert "study version" in str(e.value)

    def test_wiring_faults_are_reported_before_calibration(self, sandbox):
        """A missing screen has a more specific diagnosis than a missing
        budget, and answering the calibration question first would answer one
        the caller has not reached."""
        (sandbox / "generate_bankT_ol.jsonl").write_text("", encoding="utf-8")
        with pytest.raises(SystemExit) as e:
            R.stage_solve("T", None, C.PATH_CONTROLLED, "ol")
        assert "no screen for roster" in str(e.value)


class TestPlaceboBudgetGuard:
    """The negative control sends a HINTED-shape prompt, and nothing checked it.

    `placebo_identifiability` formats `_MASKED_WITH_HINT` exactly as `leak_score`
    does -- only the filler differs -- so the placebo is a third arm, not a
    baseline one. Until 2026-08-17 only `solve` was guarded, which left the one
    stage that GATES `solve` free to run at a budget measured on a different
    prompt. The quarantined Bank A placebo was quarantined for exactly that
    (minimax truncation), so re-running it unguarded would have repeated it.

    The placebo cannot borrow the hinted arm's budget either: it is content-free
    by construction, so a reasoning solver has nothing to latch onto and may
    reason at length before answering.
    """

    def _wire(self, sandbox):
        # Every MCQ item in the sandbox bank must be screened: `placebo` runs
        # over the SCREENED set and refuses a partial screen, which is a
        # different (and more specific) complaint than a missing budget.
        (sandbox / "screen_bankT_ol.jsonl").write_text("".join(
            json.dumps({
                "item_id": iid, "baseline": 0.1, "passes_headroom": True,
                "chance": 0.25, "n_failed_calls": 0, "k_per_solver": 20,
                "per_solver": {"ol/gemma4:cloud": {"hits": 1, "calls": 20,
                                                   "parsed": 20, "failed": 0}},
            }) + "\n" for iid in ("T-01", "T-02")), encoding="utf-8")

    def _budget(self, sandbox, arm, version):
        (sandbox / f"budget_T_ol_{arm}.jsonl").write_text(json.dumps({
            "bank": "T", "roster": "ol", "arm": arm,
            "item_id": "T-01", "model": "ol/gemma4:cloud", "ok": True,
            "completion_tokens": 12, "study_version": version,
        }) + "\n", encoding="utf-8")

    def test_an_unmeasured_placebo_arm_aborts(self, sandbox):
        self._wire(sandbox)
        with pytest.raises(SystemExit) as e:
            R.stage_placebo("T", None, C.PATH_CONTROLLED, "ol")
        assert "placebo-arm budget" in str(e.value)
        assert "--arm placebo" in str(e.value)

    def test_the_hinted_arm_does_not_satisfy_the_placebo_guard(self, sandbox):
        """The bug this guard exists for, stated as a test.

        Both stages send `_MASKED_WITH_HINT`, so a single 'hinted' measurement
        looks like it covers both. It does not, and accepting it here would
        rebuild the defect with the guard in place.
        """
        self._wire(sandbox)
        self._budget(sandbox, "hinted", C.STUDY_VERSION)
        with pytest.raises(SystemExit) as e:
            R.stage_placebo("T", None, C.PATH_CONTROLLED, "ol")
        assert "placebo-arm budget" in str(e.value)

    def test_a_stale_study_version_aborts(self, sandbox):
        self._wire(sandbox)
        self._budget(sandbox, "placebo", "0.0.1")
        with pytest.raises(SystemExit) as e:
            R.stage_placebo("T", None, C.PATH_CONTROLLED, "ol")
        assert "study version" in str(e.value)

    def test_every_model_calling_stage_declares_its_arm(self):
        """A stage missing from STAGE_ARM is silently unguarded.

        The placebo gap was invisible for exactly this reason: nothing listed
        which stages call a solver, so nothing could notice one was absent.

        AND THEN THE SAME BLINDNESS RECURRED ONE LEVEL UP (spec 4.56). This test
        was named `..._solver_calling_stage...` and asserted the set was exactly
        the three solver stages -- so `generate`, which calls a GENERATOR, was
        not merely missing from the table, it was excluded from the concept the
        table encoded. It ran at `backends.chat`'s default budget for the whole
        study. The unit is a MODEL-calling stage; a generator has a completion
        distribution exactly as a solver does.
        """
        assert set(R.STAGE_ARM) == {"screen", "placebo", "solve", "generate"}
        for stage, arm in R.STAGE_ARM.items():
            assert hasattr(R, f"stage_{stage}"), stage
            assert arm in ("baseline", "placebo", "hinted", "generate"), (stage, arm)
            # Every arm must say where its configured ceiling comes from,
            # otherwise the shared guard cannot compare configuration to
            # measurement -- which is the whole of spec 4.48.
            assert arm in R.ARM_BUDGET, (stage, arm)
            table, accessor = R.ARM_BUDGET[arm]
            assert table.startswith("C.MAX_TOKENS_"), table
            assert callable(accessor)


class TestGenerateDoesNotRetireFailedCells:
    """A quota failure must not permanently consume a cell.

    Observed 2026-08-18 on the real Bank A run: 176 of 2,016 cells hit the
    Ollama quota, each wrote an `ok=False` row, `load_done` counted them as
    finished, and the supervisor -- which counts ROWS -- logged COMPLETE. 8.7%
    of the study's generated responses were lost to a condition that clears
    itself in twenty minutes, and nothing in the pipeline said so.
    """

    def _write(self, sandbox, rows):
        p = sandbox / "generate_bankT_ol.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return p

    def test_a_failed_cell_is_not_done(self, sandbox):
        p = self._write(sandbox, [
            {"cell_key": "c1", "ok": True, "response": "a hint"},
            {"cell_key": "c2", "ok": False, "response": None,
             "error_kind": "quota_exhausted"},
        ])
        assert R.load_done_ok(p, "cell_key") == {"c1"}
        # The old behaviour, kept for stages whose unit of work is the record.
        assert R.load_done(p, "cell_key") == {"c1", "c2"}

    def test_an_ok_row_with_an_empty_response_is_not_done(self, sandbox):
        """`ok` alone is not enough: every consumer also requires text, so a
        cell that returned nothing usable must be retried like a failure."""
        p = self._write(sandbox, [{"cell_key": "c1", "ok": True, "response": "   "}])
        assert R.load_done_ok(p, "cell_key") == set()

    def test_a_later_success_supersedes_an_earlier_failure(self, sandbox):
        p = self._write(sandbox, [
            {"cell_key": "c1", "ok": False, "response": None},
            {"cell_key": "c1", "ok": True, "response": "filled on the retry"},
        ])
        assert R.load_done_ok(p, "cell_key") == {"c1"}

    def test_consumers_ignore_the_superseded_failure(self, sandbox):
        """Re-running appends rather than rewriting, so both rows exist. This is
        only safe because every consumer filters on ok+response -- assert it,
        rather than trusting the comment that says so."""
        rows = [{"cell_key": "c1", "item_id": "T-01", "ok": False, "response": None},
                {"cell_key": "c1", "item_id": "T-01", "ok": True, "response": "hint"}]
        usable = [r for r in rows if r.get("ok") and (r.get("response") or "").strip()]
        assert len(usable) == 1 and usable[0]["response"] == "hint"


class TestGenerateFailureGuard:
    """`generate` was the one stage that would write a whole file of failures.

    `screen`, `placebo` and `solve` abort above `C.MAX_CALL_FAILURE_RATE` via
    `solvability._guard_failures`. `generate` had no guard at all, so a dead
    backend produced a full-length file of `ok=False` rows and a "complete"
    line -- the same shape as the outage behind the QUARANTINE files.
    """

    def test_the_guard_threshold_is_shared_with_every_other_stage(self):
        """One constant, not a second copy: the recurring defect in this project
        is two components holding separate copies of one rule (spec 4.33)."""
        src = (Path(__file__).resolve().parent.parent / "run.py").read_text()
        gen = src[src.index("def stage_generate"):src.index("def _cell_key")]
        # The SHARED guard, called -- not its threshold re-compared. The first
        # attempt at this line named `C.MAX_CALL_FAILURE_RATE`, which does not
        # exist (the constant is in `solvability`), and the stage died with
        # AttributeError on call 20 of a live run.
        assert "S._guard_failures(" in gen
        # CODE ONLY. The first version of this assertion read the whole function
        # and failed on the COMMENT that explains the history -- prose naming a
        # constant is not a second copy of it. A test that cannot tell code from
        # commentary punishes documenting the bug.
        code = "\n".join(l for l in gen.splitlines()
                          if not l.strip().startswith("#"))
        assert "MAX_CALL_FAILURE_RATE" not in code, (
            "compare through _guard_failures rather than re-reading the "
            "threshold; a second copy is how these rules drift")

    def test_it_reports_unfilled_cells_at_the_end(self):
        """A supervisor counts rows and cannot tell a filled cell from a failed
        one, so the stage must say so itself, after the progress lines have
        scrolled past."""
        src = (Path(__file__).resolve().parent.parent / "run.py").read_text()
        gen = src[src.index("def stage_generate"):src.index("def _cell_key")]
        assert "NOT done" in gen and "re-run" in gen


class TestBudgetGuardChecksAdequacyNotExistence:
    """A file is not a measurement.

    Observed 2026-08-18: `measure_budgets --arm hinted --items 6` drew its
    sample from all 40 Bank A mcq items, but `generate` only runs on the 14 that
    passed the screen, so 5 draws had no response and were skipped. The run
    recorded ONE item, reported `OK` for every solver, and this guard -- which
    checked only that the file existed and carried the right study version --
    would have let `solve` spend hours against a maximum drawn from four calls
    of a single prompt.
    """

    def _wire(self, sandbox):
        (sandbox / "screen_bankT_ol.jsonl").write_text("".join(
            json.dumps({
                "item_id": iid, "baseline": 0.1, "passes_headroom": True,
                "chance": 0.25, "n_failed_calls": 0, "k_per_solver": 20,
                "per_solver": {"ol/gemma4:cloud": {"hits": 1, "calls": 20,
                                                   "parsed": 20, "failed": 0}},
            }) + "\n" for iid in ("T-01", "T-02")), encoding="utf-8")
        (sandbox / "generate_bankT_ol.jsonl").write_text(json.dumps({
            "cell_key": "c1", "item_id": "T-01", "model": "ol/gemma4:cloud",
            "regime": "P0", "solicitation_key": "S0", "ok": True,
            "response": "a hint",
        }) + "\n", encoding="utf-8")

    def _budget(self, sandbox, n_items, calls_each):
        rows = []
        for i in range(n_items):
            for _ in range(calls_each):
                rows.append({"bank": "T", "roster": "ol", "arm": "hinted",
                             "item_id": f"T-{i:02d}", "model": "ol/gemma4:cloud",
                             "ok": True, "completion_tokens": 12,
                             "study_version": C.STUDY_VERSION})
        (sandbox / "budget_T_ol_hinted.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def test_a_single_item_measurement_is_refused(self, sandbox):
        self._wire(sandbox)
        self._budget(sandbox, n_items=1, calls_each=12)
        with pytest.raises(SystemExit) as e:
            R.stage_solve("T", None, C.PATH_CONTROLLED, "ol")
        assert "only 1 item" in str(e.value)

    def test_too_few_calls_is_refused(self, sandbox):
        self._wire(sandbox)
        self._budget(sandbox, n_items=3, calls_each=2)
        with pytest.raises(SystemExit) as e:
            R.stage_solve("T", None, C.PATH_CONTROLLED, "ol")
        assert "below the floor" in str(e.value)

    def test_an_adequate_measurement_passes_the_guard(self, sandbox):
        """The floors must not block a real measurement.

        Calls the guard DIRECTLY rather than running `stage_solve` and letting
        it fall through. The first version did the latter, got past the guard as
        intended, and then made real network calls -- turning a 3-second offline
        suite into a 59-second one that depends on a paid backend. `_guard_arm_budget`
        is the unit under test; the stage is not.
        """
        self._wire(sandbox)
        self._budget(sandbox, n_items=R.MIN_BUDGET_ITEMS, calls_each=6)
        R._guard_arm_budget("T", "ol", "solve")   # must not raise

    def test_the_refusal_names_a_command_that_would_satisfy_it(self, sandbox):
        self._wire(sandbox)
        self._budget(sandbox, n_items=1, calls_each=12)
        with pytest.raises(SystemExit) as e:
            R.stage_solve("T", None, C.PATH_CONTROLLED, "ol")
        msg = str(e.value)
        assert "--arm hinted" in msg and "--items" in msg

    # --- adequacy of the CONFIGURATION, not just of the measurement -------
    # Spec 4.48. Every check above validates the measurement; none asked the
    # question it was taken to answer.

    def _budget_at(self, sandbox, peak, n_items=3, calls_each=6,
                   model="ol/gemma4:cloud"):
        rows = []
        for i in range(n_items):
            for j in range(calls_each):
                rows.append({"bank": "T", "roster": "ol", "arm": "hinted",
                             "item_id": f"T-{i:02d}", "model": model, "ok": True,
                             # the peak appears once, as a real tail would
                             "completion_tokens": peak if (i, j) == (0, 0) else 4,
                             "study_version": C.STUDY_VERSION})
        (sandbox / "budget_T_ol_hinted.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def test_a_budget_below_the_observed_maximum_is_refused(self, sandbox):
        """Proof that a call in this very sample would have been cut off."""
        self._wire(sandbox)
        cur = C.solver_max_tokens("ol/gemma4:cloud")
        self._budget_at(sandbox, peak=cur + 1)
        with pytest.raises(SystemExit) as e:
            R._guard_arm_budget("T", "ol", "solve")
        msg = str(e.value)
        assert "BELOW what the" in msg and f"observed max {cur + 1}" in msg

    def test_a_budget_above_the_maximum_but_under_3x_warns_and_proceeds(
            self, sandbox, capsys):
        """3x is a policy for CHOOSING a budget, not evidence of a defect: a run
        whose own data shows no truncation must not be blocked by it."""
        self._wire(sandbox)
        cur = C.solver_max_tokens("ol/gemma4:cloud")
        self._budget_at(sandbox, peak=max(1, cur - 1))
        R._guard_arm_budget("T", "ol", "solve")        # must not raise
        assert "below the 3x recommendation" in capsys.readouterr().out

    def test_the_budget_used_is_recorded_on_every_solver_stage_record(self):
        """`MAX_TOKENS_SOLVER` is a live constant and there is no git repo, so a
        row that does not state its own ceiling has no audit trail once the
        constant is edited."""
        used = R._budgets_used(["ol/gemma4:cloud", "ol/gpt-oss:120b-cloud"])
        assert used == {m: C.solver_max_tokens(m)
                        for m in ("ol/gemma4:cloud", "ol/gpt-oss:120b-cloud")}
        assert all(isinstance(v, int) and v > 0 for v in used.values())
