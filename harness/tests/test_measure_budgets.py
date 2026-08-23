"""`measure_budgets` decides a number the whole screen depends on.

WHY THIS FILE EXISTS. A token budget set too low turns a reasoning solver into
an always-declining one, and because declines are scored as misses that
DEPRESSES the baseline -- biasing the screen toward wrongly RETAINING items near
the threshold (spec 4.28). The rule "measure, never guess" was written as a
comment on `C.MAX_TOKENS_SOLVER` and nothing enforced it, so the Bank A budgets
were reused on Bank B and the defect recurred (spec 4.33).

The tests below cover the three ways this tool could quietly produce a wrong
budget: sampling that misses the tail, a resume that double-counts, and a
recommendation derived from a probe that itself truncated.
"""

import sys
from pathlib import Path

import pytest

from conftest import fake_result

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402
import measure_budgets as M  # noqa: E402
import solvability as S  # noqa: E402


class TestTheProbePromptIsTheRealPrompt:
    """Reasoning length tracks the prompt, so a probe on a different string
    measures a different distribution -- which is how a budget comes to be
    'measured' and still be wrong."""

    def test_it_is_built_from_the_screens_own_helpers(self):
        import inspect
        src = inspect.getsource(M._baseline_prompt)
        assert "_MASKED_BASELINE" in src
        assert "_format_options" in src
        assert "_topic_clause" in src

    def test_it_reproduces_the_screen_prompt_exactly(self):
        item = {"id": "X", "options": ["a", "b", "c"], "answer_index": 0,
                "topic": "sequences"}
        expected = S._MASKED_BASELINE.format(
            options=S._format_options(item["options"]),
            topic=S._topic_clause(item, True))
        assert M._baseline_prompt(item) == expected


class TestSamplingCoversTheTail:
    """A uniform draw estimates the MEAN well and the MAXIMUM badly, and the
    budget is set from the maximum."""

    def test_included_items_are_always_measured(self, monkeypatch, tmp_path):
        """The forced item is last in a 20-item list and the draw takes 3, so
        without `--include` it appears with probability ~0.15."""
        seen = []

        def fake_chat(model, messages, **kw):
            seen.append(messages[0]["content"])
            return fake_result("1", completion_tokens=10, prompt_tokens=5)

        # Options differ per item so each item's prompt is identifiable.
        items = [{"id": f"I{i}", "type": "mcq", "options": [f"opt{i}a", f"opt{i}b"],
                  "answer_index": 0, "topic": "t"} for i in range(20)]
        monkeypatch.setattr(M.B, "chat", fake_chat)
        monkeypatch.setattr(M.R, "load_bank", lambda b: {"items": items})
        monkeypatch.setattr(M.R, "roster", lambda r: {"solvers": ("m",),
                                                      "path": C.PATH_CONTROLLED})
        monkeypatch.setattr(M.R, "RUNS", tmp_path)

        res = M.measure("bs", "ol", 3, 1, include=("I19",))
        assert res["n_items"] == 3
        assert any("opt19a" in c for c in seen), \
            "the forced item was not measured"

    def test_the_draw_is_not_shrunk_by_forcing(self, monkeypatch, tmp_path):
        """Forcing an item must not cost a slot elsewhere silently -- the total
        stays at n_items, with the forced item counted inside it."""
        def fake_chat(model, messages, **kw):
            return fake_result("1", completion_tokens=10, prompt_tokens=5)

        items = [{"id": f"I{i}", "type": "mcq", "options": ["a", "b"],
                  "answer_index": 0, "topic": "t"} for i in range(20)]
        monkeypatch.setattr(M.B, "chat", fake_chat)
        monkeypatch.setattr(M.R, "load_bank", lambda b: {"items": items})
        monkeypatch.setattr(M.R, "roster", lambda r: {"solvers": ("m",),
                                                      "path": C.PATH_CONTROLLED})
        monkeypatch.setattr(M.R, "RUNS", tmp_path)
        assert M.measure("bs", "ol", 5, 1, include=("I19", "I18"))["n_items"] == 5

    def test_unknown_included_id_is_refused_not_ignored(self, monkeypatch):
        items = [{"id": "I0", "type": "mcq", "options": ["a", "b"],
                  "answer_index": 0, "topic": "t"}]
        monkeypatch.setattr(M.R, "load_bank", lambda b: {"items": items})
        monkeypatch.setattr(M.R, "roster", lambda r: {"solvers": ("m",),
                                                      "path": C.PATH_CONTROLLED})
        with pytest.raises(SystemExit, match="not in bank"):
            M.measure("bs", "ol", 1, 1, include=("NOPE",))


class TestResumeDoesNotDoubleCount:
    """Re-measuring an already-measured (item, model) pair would silently
    double-weight it in the maximum the budget is derived from."""

    def _setup(self, monkeypatch, tmp_path, calls_seen):
        items = [{"id": "I0", "type": "mcq", "options": ["a", "b"],
                  "answer_index": 0, "topic": "t"}]

        def fake_chat(model, messages, **kw):
            calls_seen.append(model)
            return fake_result("1", completion_tokens=42, prompt_tokens=5)

        monkeypatch.setattr(M.B, "chat", fake_chat)
        monkeypatch.setattr(M.R, "load_bank", lambda b: {"items": items})
        monkeypatch.setattr(M.R, "roster", lambda r: {"solvers": ("m",),
                                                      "path": C.PATH_CONTROLLED})
        monkeypatch.setattr(M.R, "RUNS", tmp_path)

    def test_a_completed_pair_is_not_re_measured(self, monkeypatch, tmp_path):
        seen = []
        self._setup(monkeypatch, tmp_path, seen)
        M.measure("bs", "ol", 1, 3)
        assert len(seen) == 3
        M.measure("bs", "ol", 1, 3)          # resume: nothing left to do
        assert len(seen) == 3, "resume re-measured a completed pair"

    def test_a_partial_pair_is_topped_up_not_restarted(self, monkeypatch, tmp_path):
        seen = []
        self._setup(monkeypatch, tmp_path, seen)
        M.measure("bs", "ol", 1, 2)
        assert len(seen) == 2
        M.measure("bs", "ol", 1, 5)          # 3 more, not 5
        assert len(seen) == 5

    def test_resumed_measurements_still_count_toward_the_maximum(
            self, monkeypatch, tmp_path):
        seen = []
        self._setup(monkeypatch, tmp_path, seen)
        M.measure("bs", "ol", 1, 3)
        res = M.measure("bs", "ol", 1, 3)
        # The prior rows must be READ BACK, or a resumed run recommends a budget
        # from only the calls it happened to make itself.
        assert len(res["per_model"]["m"]) == 3


def adequate_file(tmp_path):
    """A budget file that CLEARS the sample floor, so a test can isolate the
    recommendation logic from the adequacy check.

    `report` deliberately reads the FILE for adequacy (§4.57: it must describe
    the whole append-only measurement, not one invocation) while taking the
    maxima from `res["per_model"]`. So these tests need a file that passes the
    floor; its contents need not match `per_model`, because the two answer
    different questions. Before the absent-file hole was closed these tests
    passed `Path("x.jsonl")` and were silently exercising the exempt path.
    """
    import run as R
    p = tmp_path / "budget_A_ol_baseline.jsonl"
    p.write_text("".join(
        '{"model": "m", "ok": true, "completion_tokens": 1, '
        f'"item_id": "i{i // 4}"}}\n' for i in range(R.MIN_BUDGET_CALLS)))
    return p


class TestRecommendation:

    def test_a_probe_that_truncated_refuses_to_recommend(self, capsys, tmp_path):
        res = {"per_model": {"m": [100]}, "truncated": {"m": 1},
               "failed": {"m": 0}, "n_items": 1, "n_calls": 1,
               "out": adequate_file(tmp_path)}
        assert M.report(res) == 1
        assert "no recommendation possible" in capsys.readouterr().out

    def test_no_successful_calls_refuses_to_recommend(self, capsys, tmp_path):
        res = {"per_model": {"m": []}, "truncated": {"m": 0},
               "failed": {"m": 6}, "n_items": 1, "n_calls": 6,
               "out": adequate_file(tmp_path)}
        assert M.report(res) == 1
        assert "cannot recommend" in capsys.readouterr().out

    def test_a_configured_budget_below_the_recommendation_is_flagged(
            self, capsys, monkeypatch, tmp_path):
        # The configured budget is PATCHED, not read from the live table. An
        # earlier version of this test hardcoded gpt-oss's real 2400, so
        # raising that budget broke the test rather than the test catching
        # anything -- the same defect as the hardcoded invariance band in
        # `verify_numbers` (spec 4.29).
        monkeypatch.setitem(C.MAX_TOKENS_SOLVER, "ol/gpt-oss:120b-cloud", 2400)
        res = {"per_model": {"ol/gpt-oss:120b-cloud": [392, 2933]},
               "truncated": {"ol/gpt-oss:120b-cloud": 0},
               "failed": {"ol/gpt-oss:120b-cloud": 0},
               "n_items": 1, "n_calls": 2, "out": adequate_file(tmp_path)}
        assert M.report(res) == 1
        out = capsys.readouterr().out
        assert "RAISE" in out
        assert str(int(2933 * M.SAFETY_FACTOR)) in out

    def test_an_adequate_budget_passes(self, capsys, tmp_path):
        res = {"per_model": {"ol/gemma4:cloud": [2, 2]},
               "truncated": {"ol/gemma4:cloud": 0},
               "failed": {"ol/gemma4:cloud": 0},
               "n_items": 1, "n_calls": 2, "out": adequate_file(tmp_path)}
        assert M.report(res) == 0
        assert "OK " in capsys.readouterr().out

    def test_the_safety_factor_exceeds_what_bank_a_shipped_with(self):
        """Bank A shipped at 2400/1461 = 1.64x and still took a truncation, so
        1.64 is demonstrably too tight."""
        assert M.SAFETY_FACTOR > 2400 / 1461


class TestTheGeneratorArm:
    """spec 4.56. `generate` called `backends.chat` with no budget for the whole
    study and took the library default of 400, because the arm table listed only
    the solver-calling stages. This arm is how that number gets measured."""

    def test_the_frame_comes_from_the_roster_not_the_config_constant(self):
        """THE MISTAKE THIS TEST WAS WRITTEN AFTER MAKING. The first version read
        `C.SOLICITATIONS`, which still holds FOUR entries -- S1 was cut before any
        data existed (spec 4.6) -- and so produced 16 combinations for a design
        that runs 12. That is exactly spec 4.43: a frame sized from a constant the
        data had outgrown, which then AGREES with stale prose."""
        import run as R
        rost = R.roster("ol")
        combos = M._gen_combos(rost)
        assert len(combos) == 12, combos
        assert len(C.SOLICITATIONS) == 4, (
            "C.SOLICITATIONS no longer has the cut S1; if the design changed, "
            "this test's premise needs revisiting rather than its number.")
        assert not any(sol == "S1" for _, sol in combos), combos
        assert {reg for reg, _ in combos} == set(C.REGIMES)

    def test_it_probes_generators_not_solvers(self):
        """The three solver arms measure how long a SOLVER reasons; this one
        measures how long a TUTOR writes. Reusing the solver panel would measure
        the wrong population entirely."""
        import inspect
        src = inspect.getsource(M.measure)
        assert 'rost["generators"] if arm == "generate"' in src

    def test_it_builds_the_prompt_with_the_pipelines_builder(self):
        """One implementation of what the tutor is asked. A re-typed copy would
        measure a prompt the study never sends."""
        import inspect
        src = inspect.getsource(M.measure)
        assert "_P().build_messages(" in src
        assert hasattr(M._P(), "build_messages")

    def test_the_arm_is_declared_and_guarded_by_run(self):
        """A measured arm nothing consults is not a guard. `run.STAGE_ARM` must
        name it and `run.ARM_BUDGET` must say which table its ceiling lives in --
        the generator ceiling is NOT C.MAX_TOKENS_SOLVER."""
        import run as R
        assert R.STAGE_ARM["generate"] == "generate"
        table, accessor = R.ARM_BUDGET["generate"]
        assert table == "C.MAX_TOKENS_GENERATOR"
        # BEHAVIOUR, NOT IDENTITY. What matters is that the accessor reads the
        # GENERATOR table -- asserting which function object it is would break on
        # any refactor while a version reading the solver table would pass.
        assert accessor("no/such-model:cloud") is None
        C.MAX_TOKENS_GENERATOR["probe/only:test"] = 4242
        try:
            assert accessor("probe/only:test") == 4242
            # ...and must NOT be satisfied by a solver-table entry.
            assert accessor("ol/gpt-oss:120b-cloud") != C.MAX_TOKENS_SOLVER.get(
                "ol/gpt-oss:120b-cloud")
        finally:
            C.MAX_TOKENS_GENERATOR.pop("probe/only:test", None)

    def test_an_unmeasured_generator_has_no_default(self):
        """`solver_max_tokens` may default to 16 -- a non-reasoning solver
        emitting one letter is a measured fact. There is no equivalent fact about
        tutor reply length, and the 400 that got used was a default nobody chose,
        so this accessor must return None instead of inventing one."""
        assert C.generator_max_tokens("no/such-model:cloud") is None


class TestAnInadequateSampleIsNotAMeasurement:
    """THE REPORTER MUST BE ABLE TO SAY "NOT YET".

    Found 2026-08-20. Bank A's generate-arm probe lost 146 of its 192 calls to
    quota and every one of the 46 survivors came from a single item. `report`
    printed four confident `RAISE ... recommended=` lines, returned a code that a
    watcher read as success, and the run was recorded as measured -- while
    `run._guard_arm_budget`, holding the very same floor, was about to refuse the
    stage. The floor was correct and in the right place; the component doing the
    REPORTING could not see it. That is spec 4.40's question ("is this a
    measurement?", never "is there one?") asked one level up, and spec 4.33's
    two-copies defect in the negative -- one copy, invisible to the other reader.
    """

    def _res(self, path, arm="generate"):
        import collections
        return {"per_model": collections.defaultdict(
                    list, {"ol/minimax-m3:cloud": [597, 300]}),
                "truncated": collections.Counter(), "failed": collections.Counter(),
                "n_items": 4, "n_calls": 12, "out": path, "arm": arm}

    def _write(self, tmp_path, n_items, per_item, model="ol/minimax-m3:cloud"):
        import json
        p = tmp_path / "budget_X_ol_generate.jsonl"
        with p.open("w", encoding="utf-8") as fh:
            for i in range(n_items):
                for _ in range(per_item):
                    fh.write(json.dumps({
                        "item_id": f"I-{i}", "model": model, "ok": True,
                        "completion_tokens": 100,
                        "study_version": C.STUDY_VERSION}) + "\n")
        return p

    def test_one_item_is_refused_however_many_calls(self, tmp_path, capsys):
        """46 calls of ONE prompt is what actually happened. A budget is the
        maximum of a distribution and reasoning length tracks item ambiguity, so
        call count cannot substitute for item diversity."""
        import run as R
        p = self._write(tmp_path, n_items=1, per_item=46)
        assert R.budget_sample_shortfall(*[R.read_budget_file(p)[k]
                                           for k in ("items", "ok_calls")])
        rc = M.report(self._res(p))
        assert rc == M.RC_INADEQUATE
        assert "INADEQUATE SAMPLE" in capsys.readouterr().out

    def test_an_inadequate_sample_never_exits_zero(self, tmp_path):
        """The specific defect: the configured ceiling CLEARED the recommendation,
        so every per-model line read OK and rc fell to 0 -- a clean exit on a file
        the guard rejects. rc must not be recoverable by a generous budget."""
        p = self._write(tmp_path, n_items=1, per_item=46)
        C.MAX_TOKENS_GENERATOR["ol/minimax-m3:cloud"] = 10 ** 6
        try:
            assert M.report(self._res(p)) == M.RC_INADEQUATE
        finally:
            C.MAX_TOKENS_GENERATOR["ol/minimax-m3:cloud"] = 2463

    def test_the_two_failures_have_different_exit_codes(self, tmp_path):
        """rc=1 means "apply this number", rc=2 means "there is no number yet".
        A single nonzero code forced callers to grep stdout for `recommended=` --
        which an inadequate sample also prints, so it read as success."""
        ok = self._write(tmp_path, n_items=4, per_item=12)
        C.MAX_TOKENS_GENERATOR.pop("ol/minimax-m3:cloud", None)
        try:
            assert M.report(self._res(ok)) == 1        # adequate, budget too low
        finally:
            C.MAX_TOKENS_GENERATOR["ol/minimax-m3:cloud"] = 2463
        assert M.report(self._res(ok)) == 0            # adequate, budget clears
        assert M.RC_INADEQUATE not in (0, 1)

    def test_reporter_and_guard_read_the_same_file(self, tmp_path):
        """ONE PARSER. `report` used to describe only its own invocation while the
        guard read the whole append-only file, so a resumed measurement printed
        `n=12` for an arm whose file held 51 successful calls per model. Two
        components answering "what do we know about this arm?" from different data
        is how every defect in spec 4.33 begins."""
        import inspect
        import run as R
        src = inspect.getsource(M.report)
        assert "R.read_budget_file" in src and "R.budget_sample_shortfall" in src
        # and the guard must not have kept a private copy of the parse
        gsrc = inspect.getsource(R._guard_arm_budget)
        assert "read_budget_file" in gsrc
        assert "json.loads" not in gsrc

    def test_a_failed_call_measures_nothing(self, tmp_path):
        """`n_items` from the ATTEMPTED sample is what let "Measured 4 items x 12
        calls" head a report in which one item survived. Only successful calls
        with a token count are evidence."""
        import json
        import run as R
        p = tmp_path / "budget_X_ol_generate.jsonl"
        rows = [{"item_id": f"I-{i}", "model": "ol/minimax-m3:cloud", "ok": False,
                 "error_kind": "quota_exhausted", "completion_tokens": None,
                 "study_version": C.STUDY_VERSION} for i in range(9)]
        rows.append({"item_id": "I-9", "model": "ol/minimax-m3:cloud", "ok": True,
                     "completion_tokens": 321, "study_version": C.STUDY_VERSION})
        p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        seen = R.read_budget_file(p)
        assert seen["ok_calls"] == 1 and seen["items"] == {"I-9"}
        assert seen["peak"] == {"ol/minimax-m3:cloud": 321}


class TestAnUnmeasuredArmNeverExitsZero:
    """The §4.57 fix had the same hole §4.57 was about, one level up.

    `budget_sample_shortfall` returned None for an EMPTY sample -- documented as
    "nothing measured at all, the caller's own error to state" -- and
    `report` then exempted an ABSENT file from the check as well. With no models
    in the file the recommendation loop never ran either, so `rc` stayed 0. So on
    2026-08-20 `measure_budgets.py --bank bs --arm hinted --report-only` printed
    "Attempted 0 items x 0 calls" and exited 0 for an arm that had never been
    measured; `measure_arm.sh` read rc=0 as "already measured and clears";
    `run_pipeline.sh` logged "measured hinted budgets for bs -- configured
    ceiling clears them" ONE SECOND after generate finished; and `solve bs` then
    refused for want of that exact file, aborting the queue for four hours.

    An empty measurement clears every ceiling vacuously, because nothing in it
    exceeds one. "Is this a measurement?" -- never "is there a measurement?"
    (§4.40) -- failing inside the two functions written to enforce it, because
    the requirement lived in a comment delegating it to callers (§4.51).
    """

    def test_an_empty_sample_is_a_shortfall_not_an_exemption(self):
        import run as R
        assert R.budget_sample_shortfall(set(), 0) is not None
        assert R.budget_sample_shortfall(set(), 12) is not None

    def test_an_absent_arm_file_reports_inadequate(self, tmp_path):
        res = {"per_model": {}, "truncated": {}, "failed": {},
               "n_items": 0, "n_calls": 0,
               "out": tmp_path / "budget_BS_ol_hinted.jsonl", "arm": "hinted"}
        assert M.report(res) == M.RC_INADEQUATE

    def test_an_empty_but_present_arm_file_reports_inadequate(self, tmp_path):
        p = tmp_path / "budget_BS_ol_hinted.jsonl"
        p.write_text("")
        res = {"per_model": {}, "truncated": {}, "failed": {},
               "n_items": 0, "n_calls": 0, "out": p, "arm": "hinted"}
        assert M.report(res) == M.RC_INADEQUATE

    def test_inadequate_outranks_a_clearing_ceiling(self, tmp_path, capsys):
        """rc must not fall to 0 just because the configured value is generous.

        This is how the original defect surfaced: an inadequate sample also
        prints `recommended=`, and when the configured ceiling happened to
        exceed it, rc fell to 0.
        """
        p = tmp_path / "budget_BS_ol_hinted.jsonl"
        p.write_text('{"model": "m", "ok": true, "completion_tokens": 5, '
                     '"item_id": "only-one"}\n')
        res = {"per_model": {"m": [5]}, "truncated": {"m": 0}, "failed": {"m": 0},
               "n_items": 1, "n_calls": 1, "out": p, "arm": "hinted"}
        assert M.report(res) == M.RC_INADEQUATE
        assert "INADEQUATE SAMPLE" in capsys.readouterr().out

    def test_the_header_says_attempted_not_measured(self, tmp_path, capsys):
        """`n_items` is the ATTEMPTED sample; survivors can be far fewer."""
        res = {"per_model": {}, "truncated": {}, "failed": {},
               "n_items": 4, "n_calls": 48,
               "out": tmp_path / "budget_A_ol_generate.jsonl", "arm": "generate"}
        M.report(res)
        assert "Attempted" in capsys.readouterr().out
