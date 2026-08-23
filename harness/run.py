"""Resumable stage runner.

Every stage appends one JSON object per completed unit of work to a JSONL file
and skips units already present. A run that dies at 80% resumes at 80%; nothing
is recomputed, and nothing already paid for is lost.

Stages:
    screen    masked-stem no-hint baseline per item -> the headroom screen
    placebo   content-free tutor utterances -> the negative control
    generate  the P0-P3 cells
    solve     leak_hat (and placebo-corrected leak_adj) per response
    judge     Tier-2 panel verdicts

The bank hash is checked against the manifest before any stage that consumes
items. Editing an item after seeing results is the failure mode that makes an
author-written bank worthless as evidence, so the run refuses rather than warns.

Usage:
    python3 run.py screen --bank A [--limit N] [--k 20]
    python3 run.py status
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import config as C
import solvability as S

DATA = C.ROOT / "data"
RUNS = DATA / "runs"


#: Bank id -> filename, for ids whose file is not `bank_<id>.json`.
#:
#: `bs` is the pre-registered Bank B SAMPLE (see sample_bank_b.py). It is a
#: separate bank id, not a flag on `B`, for two reasons:
#:
#:   1. `--bank B` must keep meaning the whole 185-item bank. Screening that
#:      costs 6.8 h and yields ~130 retained items, which implies a ~101 h main
#:      study -- the outcome the sample exists to avoid. A flag that is easy to
#:      forget would make the expensive path the default one.
#:   2. Output files are named from the bank id (`screen_bankBS_ol.jsonl`), so
#:      sample and full-bank measurements can never land in the same file. They
#:      are different populations and pooling them would misstate n.
_BANK_FILES = {"bs": "bank_b_sample.json"}


def _bank_path(bank: str) -> Path:
    name = _BANK_FILES.get(bank.lower(), f"bank_{bank.lower()}.json")
    return C.BANKS_DIR / name


def frozen_path(bank: str, rost: str = "kr") -> Path:
    """Where `freeze_bank` writes, and where the stages read it back from.

    Defined here rather than in `freeze_bank` so the writer and every reader
    derive the name once. `stage_solve` already learned this the hard way with
    the screen path (see its comment on the roster argument).
    """
    suffix = "" if rost == "kr" else f"_{rost}"
    return C.BANKS_DIR / f"bank_{bank.lower()}_screened{suffix}.json"


def load_frozen(bank: str, rost: str = "kr") -> dict | None:
    p = frozen_path(bank, rost)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_bank(bank: str) -> dict:
    p = _bank_path(bank)
    if not p.exists():
        sys.exit(f"bank not found: {p}")
    return json.loads(p.read_text())


#: Roster -> (generators, solvers, judges, path, replicates, k_hint).
#: Panels are never mixed across rosters: vendor exclusion is computed WITHIN a
#: roster, so pairing `kr/minimax-m2.1` with `ol/minimax-m3` would place the same
#: family on both sides of an exclusion it is supposed to enforce.
ROSTERS = {
    "kr": dict(generators=lambda: C.GENERATORS, solvers=lambda: C.SOLVERS,
               judges=lambda: C.JUDGE_PANEL, path=lambda: C.PATH_HOSTED,
               reps=lambda: C.GEN_REPLICATES, k_hint=lambda: C.SOLVER_K_HINT,
               solicitations=lambda: tuple(C.SOLICITATIONS)),
    "ol": dict(generators=lambda: C.OL_GENERATORS, solvers=lambda: C.OL_SOLVERS,
               judges=lambda: C.OL_JUDGE_PANEL, path=lambda: C.PATH_CONTROLLED,
               reps=lambda: C.OL_GEN_REPLICATES, k_hint=lambda: C.OL_SOLVER_K_HINT,
               solicitations=lambda: C.OL_SOLICITATION_KEYS),
}


def roster(name: str) -> dict:
    if name not in ROSTERS:
        sys.exit(f"unknown roster {name!r}; choose from {sorted(ROSTERS)}")
    return {k: v() for k, v in ROSTERS[name].items()}


def _jsonl_path(stage: str, bank: str, rost: str = "kr") -> Path:
    RUNS.mkdir(parents=True, exist_ok=True)
    # The `kr` files predate rosters and keep their original names so the
    # existing Bank A screen and its recorded `screen_file` stay valid.
    suffix = "" if rost == "kr" else f"_{rost}"
    return RUNS / f"{stage}_bank{bank.upper()}{suffix}.jsonl"


def load_done(path: Path, key: str) -> set[str]:
    """Keys already completed, so the stage can resume."""
    if not path.exists():
        return set()
    done = set()
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)[key])
            except (json.JSONDecodeError, KeyError):
                # A torn final line from a killed process is expected; skip it
                # rather than aborting a resumable run.
                continue
    return done


def load_done_ok(path: Path, key: str) -> set[str]:
    """Keys completed SUCCESSFULLY, so a resumed run retries the failures.

    WHY THIS IS NOT `load_done`. `generate` writes one row per cell whether the
    call succeeded or not, and `load_done` treats any row with a `cell_key` as
    finished. On 2026-08-18 a Bank A run hit the Ollama quota 176 times in 2,016
    cells: every one of those wrote an `ok=False` row, the supervisor saw 2,016
    rows and logged COMPLETE, and those 176 cells were permanently skipped by
    every later resume. 8.7% of the study's generated responses were lost to a
    condition that lapses on its own in twenty minutes.

    That is the project's recurring failure in a new place -- a transport
    failure recorded as though it were a measurement (cf. `solvability`'s
    `n_failed` split, and the QUARANTINE files it produced). The row itself is
    honest: it says `ok=False, error_kind=quota_exhausted`. What was wrong is
    that RESUMPTION could not tell "we asked and got an answer" from "we asked
    and the backend refused".

    Re-running appends a second row for the same cell. That is safe here and
    checked: every consumer of a generate file (`stage_solve`, `stage_judge`,
    `measure_budgets._responses_for`, `verifier_audit`) filters on `ok` and a
    non-empty `response`, and each dedupes by `cell_key` or takes an extremum,
    so a superseded failure cannot be double-counted.
    """
    if not path.exists():
        return set()
    done = set()
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("ok") and (rec.get("response") or "").strip():
                    done.add(rec[key])
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def append(path: Path, record: dict) -> None:
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
        fh.flush()


def _declared_predecessors(out: Path) -> set[str]:
    """Short hashes the bank declares as documented predecessors.

    Read from the bank the stage is about to use, so a predecessor can only be
    honoured if it is written down in the artefact itself.
    """
    import glob
    found: set[str] = set()
    for f in glob.glob(str(C.BANKS_DIR / "bank_*.json")):
        try:
            d = json.loads(Path(f).read_text())
        except Exception:
            continue
        for h in d.get("hash_history") or []:
            if h.get("measurement_fields_changed") is False and h.get("sha256"):
                found.add(h["sha256"])
    # Records store the full hash; history may store a prefix.
    return found


def assert_bank_unchanged(out: Path, bank_sha: str, stage: str) -> None:
    """Refuse to extend a run file that was written against a different bank.

    The module docstring has always claimed the run "refuses rather than warns"
    when a bank changes, and the manuscript repeats it. It was not true: stages
    RECORDED `bank_sha256` per row but never compared it, so editing an item
    mid-screen produced one file containing two populations, silently. Only
    `freeze_bank` noticed, and only at the end of a multi-hour run.

    Editing an item after seeing its result is the failure mode that makes an
    author-written bank worthless as evidence, so this checks at the point where
    the damage would start rather than where it would surface.
    """
    if not out.exists():
        return
    seen = set()
    for line in out.read_text().splitlines():
        if not line.strip():
            continue
        try:
            h = json.loads(line).get("bank_sha256")
        except json.JSONDecodeError:
            continue
        if h:
            seen.add(h)
    # A hash the bank itself declares as a documented predecessor is accepted.
    # Provenance is preserved -- the superseded hash stays in the records and the
    # reason stays in the bank -- while a metadata repair no longer forces a
    # re-run of measurements it cannot have affected. An UNDECLARED mismatch
    # still stops the stage, which is the case this guard exists for.
    if seen and bank_sha not in seen and any(any(x.startswith(h) for h in _declared_predecessors(out)) for x in seen):
        print(f"[{stage}] NOTE: {out.name} records superseded bank hash "
              f"{sorted(seen)[0][:16]}...; the bank declares it as a documented "
              f"predecessor with measurement fields unchanged. Continuing.",
              flush=True)
        return
    if seen and bank_sha not in seen:
        sys.exit(
            f"refusing to {stage}: {out.name} was written against bank "
            f"{sorted(seen)[0][:16]}..., current bank is {bank_sha[:16]}.... "
            f"The bank changed after this run started. Either restore the bank "
            f"or start a new run file; appending would mix two populations.")


def assert_measurement_unchanged(out: Path, sig: str, stage: str) -> None:
    """Refuse when the fields a MEASUREMENT depends on have changed.

    Complements `assert_bank_unchanged`, which compares the whole-file hash.
    That hash is the right provenance record but the wrong gate: it cannot
    distinguish an edit that invalidates a verdict from one that does not, so a
    metadata repair forces a choice between re-running hours of measurement and
    switching the guard off.

    This happened. `rebalance_bank` permuted option order and updated
    `answer_index` but left `answer_forms` alone, so 27 of 40 Bank A items
    carried positional forms ("option 2") pointing at the pre-rebalance
    position. Repairing them changed the file hash and nothing a screen reads.

    The two guards are complementary and both run: this one decides whether the
    measurement is still valid, the file hash keeps the edit visible in the
    record.
    """
    if not out.exists():
        return
    seen = set()
    for line in out.read_text().splitlines():
        if not line.strip():
            continue
        try:
            v = json.loads(line).get("bank_measurement_sig")
        except json.JSONDecodeError:
            continue
        if v:
            seen.add(v)
    if seen and sig not in seen:
        sys.exit(
            f"refusing to {stage}: {out.name} measured a bank whose items "
            f"hashed {sorted(seen)[0][:16]}..., current items hash "
            f"{sig[:16]}.... A field the measurement depends on changed "
            f"(stem, options, key position, topic or id). Re-run, do not append.")


def stage_screen(bank: str, k: int, limit: int | None, path: str,
                 rost: str = "kr") -> None:
    """Headroom screen: can the options alone already give the answer away?

    Run BEFORE any tutor response exists, so it cannot be tuned to produce a
    desired leakage result. Short-answer items are excluded: with no option set
    there is nothing to identify from, and they need their own instrument.
    """
    R = roster(rost)
    solvers = R["solvers"]
    path = R["path"]
    data = load_bank(bank)
    out = _jsonl_path("screen", bank, rost)
    done = load_done(out, "item_id")

    items = [i for i in data["items"] if i.get("type") == "mcq"]
    todo = [i for i in items if i["id"] not in done]
    if limit:
        todo = todo[:limit]

    manifest = C.study_manifest({bank.upper(): _bank_path(bank)})
    meas_sig = C.measurement_signature(data["items"])
    # Both guards run. The measurement signature decides whether the
    # existing records are still valid; the file hash keeps any other
    # edit visible in the record rather than silent.
    assert_measurement_unchanged(out, meas_sig, "screen")
    assert_bank_unchanged(out, manifest.bank_sha256[bank.upper()], "screen")
    print(f"[screen] bank {bank.upper()}  mcq={len(items)}  done={len(done)}  "
          f"todo={len(todo)}  k={k}/solver x {len(solvers)} solvers "
          f"[roster={rost} path={path}]")
    print(f"[screen] bank sha256 {manifest.bank_sha256[bank.upper()][:16]}...")

    t0 = time.monotonic()
    for n, item in enumerate(todo, 1):
        rec = S.baseline_identifiability(item, solvers=solvers, k=k, path=path)
        rec["passes_headroom"] = S.passes_headroom(rec)
        rec["bank"] = bank.upper()
        rec["bank_sha256"] = manifest.bank_sha256[bank.upper()]
        rec["study_version"] = manifest.study_version
        rec["bank_measurement_sig"] = meas_sig
        rec["k_per_solver"] = k
        rec["roster"] = rost
        rec["solvers"] = list(solvers)
        rec["solver_max_tokens"] = _budgets_used(solvers)
        append(out, rec)
        elapsed = time.monotonic() - t0
        rate = elapsed / n
        print(f"  [{n:>3}/{len(todo)}] {item['id']:<11} "
              f"base={rec['baseline']:.2f} chance={rec['chance']:.2f} "
              f"parsed={rec['parsed_fraction']:.2f} "
              f"{'PASS' if rec['passes_headroom'] else 'REJECT'}  "
              f"eta={(len(todo) - n) * rate / 60:.1f}m", flush=True)

    print(f"[screen] complete -> {out}")


def screened_items(bank: str, data: dict, rost: str = "kr") -> list[dict]:
    """Items that survived the headroom screen.

    Refuses to proceed on an unscreened bank: running generation over items
    that cannot register leakage would bury a real effect in dead cells, and
    screening after the fact would let the retention rule be chosen to suit
    the results.
    """
    out = _jsonl_path("screen", bank, rost)
    if not out.exists():
        sys.exit(f"bank {bank.upper()} not screened on roster {rost!r}; run: "
                 f"run.py screen --bank {bank} --roster {rost}")
    verdicts = {}
    for line in out.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        verdicts[r["item_id"]] = r["passes_headroom"]
    kept = [i for i in data["items"] if verdicts.get(i["id"]) is True]
    missing = [i["id"] for i in data["items"]
               if i.get("type") == "mcq" and i["id"] not in verdicts]
    if missing:
        # A PREFIX IS PERMITTED ONLY WHEN A FREEZE HAS RECORDED IT. The
        # permission deliberately comes from the frozen artefact and not from a
        # command-line flag: `freeze_bank --prefix` checks that the screened set
        # really is a prefix of the drawn order (which is what makes it
        # stratified) and writes the unscreened ids into the deposited file. A
        # flag here would let anyone generate over a partly screened bank with
        # nothing in the record saying so.
        fz = load_frozen(bank, rost) or {}
        if not fz.get("screened_prefix"):
            sys.exit(f"screen incomplete: {len(missing)} MCQ items unscreened "
                     f"(first: {missing[:3]}). To proceed on the screened "
                     f"prefix, freeze it first: "
                     f"freeze_bank.py --bank {bank} --roster {rost} --prefix")
        declared = set(fz.get("unscreened_mcq_ids") or [])
        if set(missing) != declared:
            sys.exit(
                f"screen incomplete and the frozen prefix does not match it: "
                f"{len(missing)} unscreened here, {len(declared)} recorded in "
                f"{frozen_path(bank, rost).name}. The screen has moved since the "
                f"freeze; re-freeze before generating.")
        print(f"[screened_items] bank {bank.upper()}: frozen PREFIX -- "
              f"{len(kept)} retained of {len(verdicts)} screened, "
              f"{len(missing)} drawn items unscreened", flush=True)
    return kept


def stage_generate(bank: str, limit: int | None, path: str,
                   models: tuple[str, ...] | None, rost: str = "kr") -> None:
    """The P0-P3 cells over screened items."""
    import pipelines as P

    # THE STAGE EVERY OTHER STAGE CONSUMES, AND THE LAST ONE TO BE GUARDED
    # (spec 4.56). `pipelines.run` omitted `max_tokens`, so tutor replies were
    # generated at `backends.chat`'s 400-token default: 6.2% of Bank A's calls
    # sat exactly at it, truncated mid-sentence and recorded ok=True. The guard
    # is the same one `placebo` and `solve` use -- it simply was never applied
    # here, because the table it reads listed only the solver-calling stages.
    _guard_arm_budget(bank, rost, "generate")
    R = roster(rost)
    path = R["path"]
    data = load_bank(bank)
    items = screened_items(bank, data, rost)
    out = _jsonl_path("generate", bank, rost)
    # Successful cells only: a quota failure must not retire a cell (see
    # `load_done_ok`). Re-running this stage after a quota window lapses fills
    # the gaps rather than declaring them finished.
    done = load_done_ok(out, "cell_key")

    grid = P.cells(items, models=models or R["generators"],
                   solicitations=R["solicitations"], replicates=R["reps"])
    todo = [c for c in grid
            if _cell_key(c) not in done]
    if limit:
        todo = todo[:limit]

    manifest = C.study_manifest({bank.upper(): _bank_path(bank)})
    meas_sig = C.measurement_signature(data["items"])
    # Both guards run. The measurement signature decides whether the
    # existing records are still valid; the file hash keeps any other
    # edit visible in the record rather than silent.
    assert_measurement_unchanged(out, meas_sig, "generate")
    assert_bank_unchanged(out, manifest.bank_sha256[bank.upper()], "generate")
    print(f"[generate] items={len(items)} cells={len(grid)} done={len(done)} "
          f"todo={len(todo)} path={path}", flush=True)

    t0 = time.monotonic()
    n_failed, kinds = 0, {}
    for n, cell in enumerate(todo, 1):
        res = P.run(cell["item"], cell["model"], cell["solicitation_key"],
                    cell["regime"], path=path)
        rec = res.as_record()
        rec["cell_key"] = _cell_key(cell)
        rec["rep"] = cell["rep"]
        rec["bank"] = bank.upper()
        rec["path"] = path
        rec["roster"] = rost
        rec["bank_sha256"] = manifest.bank_sha256[bank.upper()]
        rec["study_version"] = manifest.study_version
        rec["bank_measurement_sig"] = meas_sig
        rec["harness_git_sha"] = manifest.harness_git_sha
        # THE CEILING THIS REPLY WAS DRAWN UNDER, recorded because it was once
        # not. Every reply before 2026-08-19 was generated at `backends.chat`'s
        # 400 default (spec 4.56) and no field said so, so the only way to tell an
        # affected row from a clean one is to test whether one of its calls
        # reached 400 -- inference from a symptom, which works only while the
        # ceiling is known and only for rows that HIT it. `solve` rows already
        # carry `solver_max_tokens` for exactly this reason.
        rec["generator_max_tokens"] = C.generator_max_tokens(cell["model"])
        append(out, rec)
        if not rec.get("ok"):
            n_failed += 1
            kinds[rec.get("error_kind") or "unknown"] = (
                kinds.get(rec.get("error_kind") or "unknown", 0) + 1)
        # GENERATE WAS THE ONLY SOLVER-FREE STAGE WITH NO FAILURE GUARD. `screen`,
        # `placebo` and `solve` all abort above C.MAX_CALL_FAILURE_RATE through
        # `solvability._guard_failures`; this one wrote whatever came back and
        # called it done. A backend that is down produces a full file of
        # `ok=False` rows and a "complete" line, which is the same shape as the
        # outage that produced the QUARANTINE files -- absent measurements
        # presented as a finished stage.
        #
        # Checked after a floor of attempts so a couple of early failures in a
        # short --limit run cannot trip it on noise.
        # THE SHARED GUARD, not a second copy of its threshold. A first version
        # of this line compared against `C.MAX_CALL_FAILURE_RATE` -- a constant
        # that does not exist (it lives in `solvability`), so the stage crashed
        # with AttributeError the moment it reached call 20. Rewriting the
        # comparison here would have been the same defect the guard exists to
        # prevent: one rule, two implementations, drifting apart.
        if n >= 20:
            S._guard_failures(n_failed, n, f"generate {bank.upper()}", kinds)
        if n % 25 == 0 or n == len(todo):
            rate = (time.monotonic() - t0) / n
            print(f"  [{n}/{len(todo)}] eta={(len(todo)-n)*rate/60:.0f}m"
                  f"{f' failed={n_failed}' if n_failed else ''}", flush=True)
    if n_failed:
        # Say it at the end too: the per-25 line scrolls past, and a supervisor
        # that counts ROWS cannot tell a completed stage from a failed one.
        print(f"[generate] {n_failed}/{len(todo)} calls failed ({kinds}); "
              f"those cells are NOT done -- re-run this stage to fill them.",
              flush=True)
    print(f"[generate] complete -> {out}")


def _cell_key(cell: dict) -> str:
    return (f"{cell['item']['id']}|{cell['model']}|{cell['solicitation_key']}"
            f"|{cell['regime']}|{cell['rep']}")


def stage_placebo(bank: str, limit: int | None, path: str, rost: str = "kr") -> None:
    """Negative control: leak measured on content-free tutor utterances.

    Runs over the SCREENED items and, like the screen, before any real response
    is scored -- so the correction cannot be tuned once the effect is known.
    """
    R = roster(rost)
    solvers, path, k = R["solvers"], R["path"], R["k_hint"]
    data = load_bank(bank)
    items = [i for i in screened_items(bank, data, rost) if i.get("type") == "mcq"]
    out = _jsonl_path("placebo", bank, rost)
    manifest = C.study_manifest({bank.upper(): _bank_path(bank)})
    meas_sig = C.measurement_signature(data["items"])
    # Both guards run. The measurement signature decides whether the
    # existing records are still valid; the file hash keeps any other
    # edit visible in the record rather than silent.
    assert_measurement_unchanged(out, meas_sig, "placebo")
    assert_bank_unchanged(out, manifest.bank_sha256[bank.upper()], "placebo")
    done = load_done(out, "item_id")
    todo = [i for i in items if i["id"] not in done]
    if limit:
        todo = todo[:limit]

    # Last, after the bank and measurement guards above: those diagnose a
    # specific mis-wiring, and a budget refusal would preempt them. Runs before
    # any call, so a wrong budget costs nothing rather than a quota window.
    _guard_arm_budget(bank, rost, "placebo")

    print(f"[placebo] items={len(items)} done={len(done)} todo={len(todo)} "
          f"({len(C.PLACEBO_RESPONSES)} placebos x {len(solvers)} solvers "
          f"x {k} draws) [roster={rost}]", flush=True)

    t0 = time.monotonic()
    for n, item in enumerate(todo, 1):
        rec = S.placebo_identifiability(item, solvers=solvers, k=k, path=path)
        rec["bank"] = bank.upper()
        rec["roster"] = rost
        rec["solver_max_tokens"] = _budgets_used(solvers)
        rec["bank_sha256"] = manifest.bank_sha256[bank.upper()]
        rec["study_version"] = manifest.study_version
        rec["bank_measurement_sig"] = meas_sig
        append(out, rec)
        rate = (time.monotonic() - t0) / n
        print(f"  [{n:>3}/{len(todo)}] {item['id']:<11} "
              f"placebo={rec['placebo_hinted']:.2f} "
              f"parsed={rec['parsed_fraction']:.2f} "
              f"eta={(len(todo) - n) * rate / 60:.1f}m", flush=True)
    print(f"[placebo] complete -> {out}")


#: Which measured arm each stage's prompt belongs to. ONE TABLE, because the
#: mapping is the thing that was wrong: `placebo` formats `_MASKED_WITH_HINT`
#: exactly as `solve` does, but only `solve` was guarded -- so the negative
#: control, which GATES `solve`, was the one stage free to run at an unmeasured
#: budget. Found 2026-08-17 while re-running the quarantined Bank A placebo.
#:
#: A stage absent from this table is unguarded, so adding a stage that calls a
#: solver means adding a row here. `test_every_solver_stage_declares_its_arm`
#: fails when one is missing rather than leaving it silently exempt.
#: Floors for a budget measurement to count as one. Low on purpose -- they
#: reject a file that is obviously not a measurement (one item, four calls),
#: not a file that is merely small. See `_guard_arm_budget`.
MIN_BUDGET_ITEMS = 3
MIN_BUDGET_CALLS = 12


def budget_sample_shortfall(items: set, ok_calls: int) -> str | None:
    """Why this budget sample is not a measurement, or None if it clears.

    ONE IMPLEMENTATION, read both by `_guard_arm_budget`, which REFUSES a stage,
    and by `measure_budgets.report`, which prints the recommendation. Keeping the
    floor only in the guard is what let the Bank A generate-arm probe of
    2026-08-20 -- 146 of its 192 calls lost to quota, all 46 survivors drawn from
    ONE item -- print four clean `RAISE ... recommended=` lines and exit 0. The
    watcher script then reported "measured, recommendation present", because its
    success test was "is there a recommendation?" rather than "is this a
    measurement?". That is section 4.40's question asked at the wrong level: the
    floor existed and was correct, and the component doing the REPORTING could
    not see it. A reporter that cannot say "inadequate" teaches you to trust a
    number the guard is about to reject.
    """
    # AN EMPTY SAMPLE IS THE EXTREME INADEQUATE ONE, NOT AN EXEMPT CASE. This
    # returned None -- "no shortfall" -- with the comment "nothing measured at
    # all, the caller's own error to state". `_guard_arm_budget` does state it
    # (it checks the file's absence first, with a better message), but
    # `measure_budgets --report-only` calls straight in, so an ABSENT arm file
    # printed "Attempted 0 items x 0 calls" and exited 0: an empty measurement
    # trivially clears any ceiling, because nothing in it exceeds one.
    # `measure_arm.sh` read that rc=0 as "already measured", `run_pipeline.sh`
    # logged "measured hinted budgets for bs -- configured ceiling clears them"
    # ONE SECOND after generate finished, and `solve bs` then refused for want of
    # the very file, aborting the queue for four hours (2026-08-20).
    #
    # That is section 4.40's question -- "is this a measurement?", never "is there
    # a measurement?" -- failing inside the function written to answer it, because
    # the requirement lived in a COMMENT delegating it to callers rather than in
    # the predicate. Section 4.51: a rule with nothing enforcing it is not a rule.
    if not items:
        return (f"no successful calls at all: 0 items, {ok_calls} call(s). "
                f"An empty sample clears every ceiling vacuously.")
    if len(items) < MIN_BUDGET_ITEMS or ok_calls < MIN_BUDGET_CALLS:
        return (f"only {len(items)} item(s) in {ok_calls} successful call(s), "
                f"below the floor of {MIN_BUDGET_ITEMS} items / "
                f"{MIN_BUDGET_CALLS} calls")
    return None


def read_budget_file(arm_file) -> dict:
    """Parse a budget-measurement file into the facts a budget decision needs.

    THE FILE, NOT THE INVOCATION. A measurement is append-only and resumable, so
    what one run of `measure_budgets.py` observed is a SUBSET of what has been
    measured -- and the guard has always read the file while `report` described
    only its own calls. Two components answering "what do we know about this
    arm?" from different data is section 4.33's defect in its purest form; the
    visible symptom was a reporter printing `n=12` for an arm whose file held 51
    successful calls per model. Both read this now.

    Counts SUCCESSFUL calls only: a call that failed measured nothing, and
    `n_items` from the attempted sample is what let "Measured 4 items x 12 calls"
    head a report in which a single item survived.
    """
    versions: set = set()
    items: set = set()
    ok_calls = 0
    peak: dict[str, int] = {}
    ok_items: dict[str, set] = {}
    for line in arm_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        versions.add(rec.get("study_version"))
        if rec.get("ok") and rec.get("completion_tokens") is not None:
            items.add(rec.get("item_id"))
            ok_calls += 1
            m = rec.get("model")
            if m:
                peak[m] = max(peak.get(m, 0), int(rec["completion_tokens"]))
                ok_items.setdefault(m, set()).add(rec.get("item_id"))
    return {"versions": versions, "items": items, "ok_calls": ok_calls,
            "peak": peak, "ok_items": ok_items}

STAGE_ARM: dict[str, str] = {
    "screen": "baseline",
    "placebo": "placebo",
    "solve": "hinted",
    # THE FOURTH ARM, ADDED 2026-08-19 (spec 4.56). This table used to hold only
    # the SOLVER-calling stages, and the test enforcing its completeness was
    # named for them -- so `generate`, which calls a GENERATOR, was not missing
    # from the table so much as excluded from the concept. It called
    # `backends.chat` with no budget at all and took the library default of 400.
    # A generator is a model with a completion distribution like any other.
    "generate": "generate",
}

#: Which configured table each arm's ceiling comes from -- resolved through
#: `config`, which owns the mapping. This module held its own copy for about an
#: hour, and in that hour `measure_budgets.report` was found to hold a third
#: (spec 4.56). Kept as a name here because `STAGE_ARM`'s completeness test reads
#: it alongside the stage table.
ARM_BUDGET: dict[str, tuple] = {
    arm: (C.arm_budget_table(arm), lambda m, _a=arm: C.arm_budget(_a, m))
    for arm in C.ARM_BUDGET
}


def _budgets_used(solvers) -> dict[str, int]:
    """The completion ceilings this row's calls were sent with.

    NO RUN RECORD CARRIED THIS UNTIL 2026-08-19, and the omission only became
    load-bearing when a budget had to change mid-study. `C.MAX_TOKENS_SOLVER` is
    a live constant and there is no git repo here, so once it is edited nothing
    in the artefact says what the completed rows used -- the audit trail for
    "measured, never guessed" was a value that could be silently overwritten.

    Bank A's rows predate the field. Their values are frozen in
    `C.SUPERSEDED_BUDGETS`, so an absent field resolves to a recorded table
    rather than to a guess.
    """
    return {m: C.solver_max_tokens(m) for m in solvers}


def _guard_arm_budget(bank: str, rost: str, stage: str) -> None:
    """Refuse a stage whose prompt arm has no budget measurement.

    GENERALISED FROM `_guard_hinted_budget` 2026-08-17. The original guarded
    `solve` only, and the reasoning below applies unchanged to every arm -- so
    the specific version was itself an instance of the defect it prevents: a
    rule enforced at one call site while another needed it just as much.
    """
    arm = STAGE_ARM[stage]
    arm_file = RUNS / f"budget_{bank.upper()}_{rost}_{arm}.jsonl"
    if not arm_file.exists():
        sys.exit(
            f"refusing to {stage}: no {arm}-arm budget measurement for bank "
            f"{bank.upper()} on roster {rost!r} ({arm_file.name} absent).\n"
            f"Arms do not predict each other -- what varies is not the prompt's "
            f"length but what the solver has to work with. A truncated call is "
            f"scored as a miss, so an unmeasured budget biases the result in a "
            f"direction the stage cannot detect. Measure it:\n"
            f"    python3 measure_budgets.py --bank {bank} --roster {rost} "
            f"--arm {arm}\n"
            f"then raise {ARM_BUDGET[arm][0]} if it says RAISE.")
    seen = read_budget_file(arm_file)
    versions, items, ok_calls, peak = (
        seen["versions"], seen["items"], seen["ok_calls"], seen["peak"])
    if versions and C.STUDY_VERSION not in versions:
        sys.exit(
            f"refusing to {stage}: {arm_file.name} was measured under study "
            f"version(s) {sorted(str(v) for v in versions)}, but the harness is "
            f"at {C.STUDY_VERSION}. A budget change is exactly what invalidates "
            f"it. Re-measure the {arm} arm first.")
    # EXISTENCE IS NOT ADEQUACY. This guard checked only that a file was there.
    # On 2026-08-18 the hinted measurement sampled 6 items from the whole bank
    # while only the 14 SCREENED items have generated responses, so 5 were
    # skipped and the file recorded ONE item -- a maximum drawn from four calls
    # of a single prompt. It reported OK, and this guard would have accepted it.
    #
    # A budget is set from the MAXIMUM of a distribution, and reasoning length
    # tracks item ambiguity (spec 4.28), so item diversity is what makes the
    # maximum meaningful. Both floors are deliberately low: this rejects a
    # measurement that is obviously not one, and does not pretend to certify
    # that a larger sample is sufficient.
    shortfall = budget_sample_shortfall(items, ok_calls)
    if shortfall:
        sys.exit(
            f"refusing to {stage}: {arm_file.name} measured {shortfall}.\n"
            f"A budget is the maximum of a distribution and reasoning length "
            f"tracks item ambiguity, so a max drawn from one item is not a "
            f"measurement. Re-measure with more items:\n"
            f"    python3 measure_budgets.py --bank {bank} --roster {rost} "
            f"--arm {arm} --items 8 --calls 6")

    # THE GUARD VALIDATED THE MEASUREMENT AND NEVER COMPARED THE CONFIGURATION TO
    # IT. Every check above asks whether a measurement exists and is worth the
    # name; none asks the question the measurement was taken to answer -- is the
    # configured ceiling above what this arm actually emits? Until 2026-08-19 the
    # only thing enforcing that was `measure_budgets.py`'s exit code, i.e. a
    # human or a shell script reading it. §4.40's lesson was "ask whether this is
    # a measurement, not whether one exists"; the same sentence applies one level
    # up, to what the measurement is FOR.
    #
    # HARD-FAIL ON EVIDENCE, WARN ON POLICY. Refusing below the observed maximum
    # is refusing on proof that a call in this very sample would have been cut
    # off. `measure_budgets.SAFETY_FACTOR` (3x) is a policy for CHOOSING a budget,
    # justified by a heavy tail that a small sample understates -- a good reason
    # to raise a ceiling, and a bad reason to block a run whose own data shows no
    # truncation. Two layers, deliberately different strictness.
    table_name, budget_of = ARM_BUDGET[arm]
    # AN ABSENT ENTRY IS NOT A ZERO SHORTFALL, IT IS THE WORST CASE. The
    # generator table starts empty on purpose, and `generator_max_tokens` returns
    # None rather than a default -- so None must compare as below every observed
    # maximum. Reading it as 0 and then testing `0 < mx` happens to work; saying
    # so explicitly is what stops the next reader "simplifying" it to a default.
    short = {m: (budget_of(m), mx) for m, mx in sorted(peak.items())
             if (budget_of(m) or 0) < mx}
    if short:
        detail = "; ".join(
            f"{m}: {'UNMEASURED' if cur is None else f'configured {cur}'} "
            f"< observed max {mx}" for m, (cur, mx) in short.items())
        sys.exit(
            f"refusing to {stage}: the configured budget is BELOW what the "
            f"{arm} arm was measured to emit on bank {bank} -- {detail}. A "
            f"truncated call is scored as a miss, so this biases the result in "
            f"the direction the study is trying to measure. Raise "
            f"{table_name} (the recommendation is 3x the maximum) and "
            f"re-run:\n"
            f"    python3 measure_budgets.py --bank {bank} --roster {rost} "
            f"--arm {arm}")
    thin = {m: (budget_of(m), mx) for m, mx in sorted(peak.items())
            if (budget_of(m) or 0) < 3 * mx}
    if thin:
        print(f"[{stage}] NOTE: budget below the 3x recommendation for "
              + "; ".join(f"{m} ({cur} vs {3 * mx})"
                          for m, (cur, mx) in thin.items())
              + ". No call in the measurement would have truncated, but the "
                "tail is heavy (spec 4.28); truncation_slack reports any "
                "residual effect.", flush=True)


def _guard_hinted_budget(bank: str, rost: str) -> None:
    """`solve` sends a prompt whose budget may never have been measured.

    The hinted prompt is the baseline prompt plus a tutor response, and the two
    arms do not predict each other: minimax measured 2794 tokens on the screen
    prompt for A-L01-01 and 4644 on the hinted one, but 2851 and 760 for
    A-L05-03 -- longer reasoning on the SHORTER prompt (spec 4.28). So a bank
    whose budgets were calibrated by screening it is calibrated for `screen`
    and guessed for `solve`.

    That guess fails in the direction that matters. A truncated call is scored
    as a miss in BOTH arms, and `leak_hat` is `hinted - baseline`, so truncation
    in the hinted arm DEPRESSES the leak score -- understating exactly the
    leakage the study exists to measure, and doing so hardest on the responses
    that provoke the longest reasoning.

    Enforced rather than documented because that is the whole lesson of §4.33:
    the rule "budgets are measured, never guessed" was written as a comment,
    nothing checked it, and it was violated at the next opportunity.

    Kept as a named wrapper so the reasoning above stays attached to `solve`;
    the mechanism is `_guard_arm_budget`, shared with `placebo`.
    """
    _guard_arm_budget(bank, rost, "solve")


def stage_solve(bank: str, limit: int | None, path: str, rost: str = "kr") -> None:
    """leak_hat for each generated response, against the screened baseline."""
    R = roster(rost)
    solvers, k, path = R["solvers"], R["k_hint"], R["path"]
    data = load_bank(bank)
    by_id = {i["id"]: i for i in data["items"]}

    # Full records, not just the scalar baseline: the within-solver
    # decomposition needs each solver's own baseline hits/calls.
    #
    # THE ROSTER ARGUMENT IS LOAD-BEARING. Omitting it silently reads the `kr`
    # screen for every roster, and baselines are solver-dependent: A-L01-01
    # measures 0.15 on the Kiro panel and 0.47 on the Ollama panel. An `ol`
    # solve differenced against `kr` baselines would be wrong by that much, and
    # `per_solver_leak` would find no solver present in both records, so
    # `by_solver` would come out empty and the within-solver analysis and the
    # panel-agreement ICC would both quietly vanish rather than fail.
    screen_path = _jsonl_path("screen", bank, rost)
    if not screen_path.exists():
        sys.exit(f"no screen for roster {rost!r} at {screen_path.name}; "
                 f"run `screen --roster {rost}` first")
    screen = {}
    for line in screen_path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            screen[r["item_id"]] = r

    # Belt and braces: even with the right file, a screen recorded by a
    # different panel cannot supply per-solver baselines for this one.
    screened_solvers = set(next(iter(screen.values()), {}).get("per_solver", {}))
    if screened_solvers and not screened_solvers & set(solvers):
        sys.exit(f"screen in {screen_path.name} was run by {sorted(screened_solvers)}, "
                 f"which shares no solver with roster {rost!r} ({sorted(solvers)}). "
                 f"Refusing to difference against another panel's baselines.")

    placebo_path = _jsonl_path("placebo", bank, rost)
    placebo: dict[str, dict] = {}
    if placebo_path.exists():
        for line in placebo_path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                placebo[r["item_id"]] = r
    else:
        print("[solve] WARNING: no placebo control run; leak_adj will be null "
              "and the willingness artefact stays uncorrected", flush=True)

    gen_path = _jsonl_path("generate", bank, rost)
    if not gen_path.exists():
        sys.exit("nothing generated yet")

    # Checked LAST, after every structural precondition. A missing screen or a
    # foreign panel is a wiring fault with a more specific diagnosis, and a
    # calibration complaint raised ahead of those would answer a question the
    # caller has not reached yet. This is the last thing that must be true
    # before the stage starts spending quota.
    _guard_hinted_budget(bank, rost)
    responses = [json.loads(l) for l in gen_path.read_text().splitlines() if l.strip()]

    out = _jsonl_path("solve", bank, rost)
    manifest = C.study_manifest({bank.upper(): _bank_path(bank)})
    meas_sig = C.measurement_signature(data["items"])
    # Both guards run. The measurement signature decides whether the
    # existing records are still valid; the file hash keeps any other
    # edit visible in the record rather than silent.
    assert_measurement_unchanged(out, meas_sig, "solve")
    assert_bank_unchanged(out, manifest.bank_sha256[bank.upper()], "solve")
    done = load_done(out, "cell_key")
    todo = [r for r in responses
            if r["cell_key"] not in done and r.get("ok") and r.get("response")]
    if limit:
        todo = todo[:limit]

    print(f"[solve] responses={len(responses)} done={len(done)} todo={len(todo)}",
          flush=True)

    t0 = time.monotonic()
    for n, r in enumerate(todo, 1):
        item = by_id[r["item_id"]]
        base_rec = screen[r["item_id"]]
        rec = S.leak_score(item, r["response"], base_rec["baseline"],
                           generator=r["model"], solvers=solvers, k=k, path=path)
        # Pooled leak_hat weights each solver by how often it answers; on this
        # panel one solver answers 8% of the time and another 96%. Record the
        # within-solver differencing alongside it.
        rec["by_solver"] = S.per_solver_leak(base_rec, rec)
        rec["cell_key"] = r["cell_key"]
        rec["regime"] = r["regime"]
        rec["solicitation_key"] = r["solicitation_key"]
        rec["blocked"] = r.get("blocked")
        rec["bank"] = bank.upper()
        rec["roster"] = rost
        rec["solver_max_tokens"] = _budgets_used(rec["solvers"])
        rec["bank_sha256"] = manifest.bank_sha256[bank.upper()]
        rec["study_version"] = manifest.study_version
        rec["bank_measurement_sig"] = meas_sig
        # Correct against the placebo over the SAME solver subset this response
        # was scored on -- leak_score drops the generator's own vendor family.
        prec = placebo.get(r["item_id"])
        pb = S.placebo_for_solvers(prec, rec["solvers"]) if prec else None
        rec["placebo_hinted"] = pb
        rec["leak_adj"] = None if pb is None else rec["hinted"] - pb
        append(out, rec)
        if n % 25 == 0 or n == len(todo):
            rate = (time.monotonic() - t0) / n
            print(f"  [{n}/{len(todo)}] eta={(len(todo)-n)*rate/60:.0f}m", flush=True)
    print(f"[solve] complete -> {out}")


def stage_judge(bank: str, limit: int | None, path: str, rost: str = "kr") -> None:
    """Tier-2 blind panel over generated responses.

    Validation, never the outcome: the panel's agreement with `leak_hat` is
    what gets reported, so the automated measure can be defended without
    resting on a judge's opinion of the very quantity in dispute.
    """
    import judge as J

    R = roster(rost)
    judges, path = R["judges"], R["path"]
    data = load_bank(bank)
    by_id = {i["id"]: i for i in data["items"]}

    gen_path = _jsonl_path("generate", bank, rost)
    if not gen_path.exists():
        sys.exit("nothing generated yet")

    # Checked LAST, after every structural precondition. A missing screen or a
    # foreign panel is a wiring fault with a more specific diagnosis, and a
    # calibration complaint raised ahead of those would answer a question the
    # caller has not reached yet. This is the last thing that must be true
    # before the stage starts spending quota.
    _guard_hinted_budget(bank, rost)
    responses = [json.loads(l) for l in gen_path.read_text().splitlines() if l.strip()]

    out = _jsonl_path("judge", bank, rost)
    manifest = C.study_manifest({bank.upper(): _bank_path(bank)})
    meas_sig = C.measurement_signature(data["items"])
    # Both guards run. The measurement signature decides whether the
    # existing records are still valid; the file hash keeps any other
    # edit visible in the record rather than silent.
    assert_measurement_unchanged(out, meas_sig, "judge")
    assert_bank_unchanged(out, manifest.bank_sha256[bank.upper()], "judge")
    done = load_done(out, "cell_key")
    todo = [r for r in responses
            if r["cell_key"] not in done and r.get("ok") and r.get("response")]

    # A generator whose vendor family covers too much of the panel cannot be
    # judged without self-judging. Skip and report; never quietly self-judge.
    skipped: dict[str, int] = {}
    runnable = []
    for r in todo:
        try:
            J.assign_panel(r["model"], panel=judges)
        except RuntimeError:
            skipped[r["model"]] = skipped.get(r["model"], 0) + 1
            continue
        runnable.append(r)
    if limit:
        runnable = runnable[:limit]

    print(f"[judge] responses={len(responses)} done={len(done)} "
          f"todo={len(runnable)}", flush=True)
    for m, n in skipped.items():
        print(f"[judge] SKIPPED {n} responses from {m}: panel would self-judge; "
              f"exclude it from panel-dependent analyses", flush=True)

    t0 = time.monotonic()
    for n, r in enumerate(runnable, 1):
        res = J.judge_response(by_id[r["item_id"]], r["response"], r["model"],
                               panel=judges, path=path)
        append(out, {
            "cell_key": r["cell_key"], "item_id": res.item_id,
            "generator": res.generator, "judges": res.judges,
            "verdicts": res.verdicts, "majority": res.majority,
            "unanimous": res.unanimous, "n_parsed": res.n_parsed,
            "n_failed": res.n_failed, "regime": r["regime"],
            "solicitation_key": r["solicitation_key"], "bank": bank.upper(),
            "roster": rost,
            "bank_sha256": manifest.bank_sha256[bank.upper()],
            "bank_measurement_sig": meas_sig,
            "study_version": manifest.study_version,
        })
        if n % 25 == 0 or n == len(runnable):
            rate = (time.monotonic() - t0) / n
            print(f"  [{n}/{len(runnable)}] eta={(len(runnable)-n)*rate/60:.0f}m", flush=True)
    print(f"[judge] complete -> {out}")


def _guard_judge_independence(rost: str, override: bool) -> None:
    """Refuse to run the Tier-2 panel where its members are not independent.

    DEFERRED BY USER DECISION, 2026-08-16 (spec section 4.11). On the `ol`
    roster, `assign_panel`'s two rules -- never judge your own vendor, never
    fewer than two judges -- are both satisfied by a panel of two NVIDIA
    nemotrons for every Google generator, which is half of them. That is one
    opinion counted twice. Krippendorff's alpha across it would be inflated by
    shared training rather than by agreement about the construct, and would read
    as validation of the primary instrument.

    An inflated alpha is worse than no alpha: a missing tier is visibly missing,
    while a biased one is a number a reviewer would reasonably trust. Tier 3
    (human annotation by the authors) remains the validation the design commits
    to, and is unaffected.

    This refuses rather than warns because the cost of the mistake is asymmetric:
    running it wastes hours of quota AND produces a figure that could reach the
    manuscript, while not running it costs nothing that Tier 3 does not cover.
    """
    import judge as J
    rep = J.panel_independence(roster(rost)["generators"], roster(rost)["judges"])
    worst = min((d["n_independent"] for d in rep.values()), default=0)
    degenerate = [g for g, d in rep.items() if d["degenerate"]]
    if worst >= 2 or override:
        if override and worst < 2:
            print(f"[judge] OVERRIDE: proceeding with {worst} independent judge "
                  f"family for {len(degenerate)} generator(s). Report alpha as "
                  f"unreliable for those cells.", flush=True)
        return
    sys.exit(
        f"refusing to judge on roster {rost!r}: {len(degenerate)} of "
        f"{len(rep)} generators leave judges from only {worst} vendor family "
        f"({', '.join(g.split('/')[-1] for g in degenerate)}).\n"
        f"Krippendorff's alpha across same-family judges measures shared "
        f"training, not the construct, and would be biased upward.\n"
        f"This tier is DEFERRED by decision (spec 4.11); Tier 3 human validation "
        f"is unaffected. Pass --i-have-read-spec-4-11 to override deliberately."
    )


def stage_status() -> None:
    if not RUNS.exists():
        print("no runs yet")
        return
    for p in sorted(RUNS.glob("*.jsonl")):
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        print(f"{p.name:<28} {len(rows):>5} records")
        if p.name.startswith("screen") and rows:
            passed = sum(1 for r in rows if r.get("passes_headroom"))
            print(f"  {'passed headroom':<26} {passed:>5} / {len(rows)}")
            rejects = [r["item_id"] for r in rows if not r.get("passes_headroom")]
            if rejects:
                print(f"  rejected: {', '.join(rejects)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="stage", required=True)

    sc = sub.add_parser("screen")
    sc.add_argument("--bank", default="A")
    sc.add_argument("--roster", default="kr", choices=sorted(ROSTERS))
    sc.add_argument("--k", type=int, default=C.SOLVER_K_BASELINE)
    sc.add_argument("--limit", type=int, default=None)
    sc.add_argument("--path", default=C.PATH_HOSTED)

    gn = sub.add_parser("generate")
    gn.add_argument("--bank", default="A")
    gn.add_argument("--roster", default="kr", choices=sorted(ROSTERS))
    gn.add_argument("--limit", type=int, default=None)
    gn.add_argument("--path", default=C.PATH_HOSTED)
    gn.add_argument("--models", default=None,
                    help="comma-separated subset of the roster")

    pb = sub.add_parser("placebo")
    pb.add_argument("--bank", default="A")
    pb.add_argument("--roster", default="kr", choices=sorted(ROSTERS))
    pb.add_argument("--limit", type=int, default=None)
    pb.add_argument("--path", default=C.PATH_HOSTED)

    sv = sub.add_parser("solve")
    sv.add_argument("--bank", default="A")
    sv.add_argument("--roster", default="kr", choices=sorted(ROSTERS))
    sv.add_argument("--limit", type=int, default=None)
    sv.add_argument("--path", default=C.PATH_HOSTED)

    jd = sub.add_parser("judge")
    jd.add_argument("--bank", default="A")
    jd.add_argument("--roster", default="kr", choices=sorted(ROSTERS))
    jd.add_argument("--limit", type=int, default=None)
    jd.add_argument("--path", default=C.PATH_HOSTED)
    jd.add_argument("--i-have-read-spec-4-11", action="store_true",
                    help="required on a roster whose judge panel is not "
                         "independent; see spec section 4.11")

    sub.add_parser("status")

    args = ap.parse_args()
    if args.stage == "screen":
        stage_screen(args.bank, args.k, args.limit, args.path, args.roster)
    elif args.stage == "generate":
        models = tuple(args.models.split(",")) if args.models else None
        stage_generate(args.bank, args.limit, args.path, models, args.roster)
    elif args.stage == "placebo":
        stage_placebo(args.bank, args.limit, args.path, args.roster)
    elif args.stage == "solve":
        stage_solve(args.bank, args.limit, args.path, args.roster)
    elif args.stage == "judge":
        _guard_judge_independence(args.roster, args.i_have_read_spec_4_11)
        stage_judge(args.bank, args.limit, args.path, args.roster)
    elif args.stage == "status":
        stage_status()


if __name__ == "__main__":
    main()
