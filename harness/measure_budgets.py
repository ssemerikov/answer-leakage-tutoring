#!/usr/bin/env python3
"""Measure each solver's completion-token distribution on a bank's OWN items.

WHY THIS EXISTS AS A TOOL. `C.MAX_TOKENS_SOLVER` carries the comment "measured,
never guessed", and the spec (section 4.28) explains why: a reasoning model emits
content only after its trace, so a budget below that trace returns HTTP 200 with
empty content, which `parse_choice` reads as a DECLINE. Declines are counted as
misses, so truncation DEPRESSES the baseline -- and it does so hardest on the
items whose ambiguity provokes the longest reasoning, which are the items nearest
the retention threshold. A degenerate item can therefore read as though it had
headroom and be wrongly RETAINED.

The rule was stated but never mechanised, so it did not survive a second bank.
The Bank A budgets (gpt-oss 2400 for an observed max of 1461) were measured on
Bank A's items and then reused on the Bank B sample, whose items are longer real
exam questions. Result, measured 2026-08-17 on the Bank B pilot re-screen: 9
truncations in 300 calls, against 1 in 2400 for the completed Bank A screen --
and 8 of the 9 landed on ONE item, `B-assess_question5_1_1_2`, which sat 0.100
below its threshold reading PASS. That is the failure mode above, reproduced
exactly.

A budget is therefore a property of the (solver, bank) PAIR, not of the solver.
Run this before screening any bank the current budgets were not measured on.

    python3 measure_budgets.py --bank bs --roster ol [--items 8] [--calls 6]

Raw per-call measurements are written to
`data/runs/budget_<BANK>_<roster>_<arm>.jsonl` so the recommendation can be
re-derived and audited rather than trusted. The three ARMS are measured and
stored separately, because no arm's budget predicts another's:

  baseline  the prompt `screen` sends
  placebo   the prompt `placebo` sends -- hinted shape, content-free filler from
            C.PLACEBO_RESPONSES. Measurable NOW, needs no `generate`.
  hinted    the prompt `solve` sends; needs `generate` to have run first

The placebo arm was added 2026-08-17 after `placebo` was found to send
`_MASKED_WITH_HINT` while only `solve` had a budget guard -- so the negative
control, which GATES `solve`, was the one stage able to run at an unmeasured
budget. That is the section 4.33 pattern once more: a rule enforced in one place
and absent in another that needed it just as much.

(`budget_BS_ol_baseline.jsonl` was written before the arm split and carries no
`arm` field on its rows. It is baseline-arm data: the code that wrote it had only
`_baseline_prompt`. The rows are left unedited rather than backfilled, because
run files are append-only here and the filename already records the fact.)
"""

from __future__ import annotations

import argparse
import json
import random
import statistics as st
import sys
from datetime import date

import backends as B
import config as C
import run as R
import solvability as S

#: Ceiling used while MEASURING. Must be far above any plausible trace: a
#: truncated measurement would under-report the very maximum it exists to find,
#: and would do so silently.
PROBE_CEILING = 16000

#: Recommended budget = observed maximum x this. Bank A shipped at 1.64x
#: (2400/1461) and still took a truncation, so 1.64 is demonstrably too tight.
#: A token is far cheaper than a wrongly retained item.
SAFETY_FACTOR = 3.0


def _gen_combos(rost: dict) -> tuple[tuple[str, str], ...]:
    """Every (regime, solicitation) the generate stage actually sends.

    FROM THE ROSTER, NOT FROM `C.SOLICITATIONS` -- and this function exists
    because the first version of it got that wrong. `C.SOLICITATIONS` still holds
    FOUR entries; S1 was cut before any data was collected (spec 4.6), and the
    roster carries the three the study runs. Sizing the frame from the config
    constant is precisely spec 4.43's defect: `verify_numbers` did the same thing,
    derived 64 cells instead of 48, and therefore AGREED with prose the data had
    already outgrown. 4 regimes x 3 solicitations = 12.
    """
    return tuple((reg, sol) for reg in C.REGIMES
                 for sol in rost["solicitations"])


def _P():
    """`pipelines`, imported lazily.

    Imported here rather than at module scope for the same reason `run` does it:
    the prompt builder is the ONE implementation of what the tutor is asked, and
    measuring against a re-typed copy would measure a prompt the study never
    sends.
    """
    import pipelines
    return pipelines


def _baseline_prompt(item: dict) -> str:
    """The EXACT prompt the screen sends, not an approximation of it.

    Reasoning length tracks the prompt, so a probe on a different string
    measures a different distribution -- which is how a budget comes to be
    'measured' and still be wrong.
    """
    return S._MASKED_BASELINE.format(
        options=S._format_options(item["options"]),
        topic=S._topic_clause(item, True),
    )


def _hinted_prompt(item: dict, response: str) -> str:
    """The prompt `leak_score` sends: baseline plus a tutor response.

    THE TWO ARMS NEED SEPARATE BUDGETS. They are different prompts, and the
    config table already records that the difference is large and not in a
    predictable direction -- minimax measured 2794 tokens on the screen prompt
    for A-L01-01 and 4644 on the hinted one, but 2851 and 760 for A-L05-03,
    reasoning LONGER on the SHORTER prompt. Neither arm's budget predicts the
    other's, so a bank calibrated on the masked arm alone is calibrated for
    `screen` and guessed for `solve`.
    """
    return S._MASKED_WITH_HINT.format(
        options=S._format_options(item["options"]),
        response=response,
        topic=S._topic_clause(item, True),
    )


def _placebo_prompt(item: dict, response: str) -> str:
    """The prompt `placebo_identifiability` sends: hinted shape, placebo text.

    A THIRD ARM, NOT A SPECIAL CASE OF THE HINTED ONE. `stage_placebo` formats
    `_MASKED_WITH_HINT` exactly as `leak_score` does, so it was easy to read the
    placebo as "the hinted arm with different filler" and let it borrow that
    budget. It cannot, for the same reason the hinted arm cannot borrow the
    baseline's: what varies is not the prompt's length but what the solver has
    to work with. A placebo is content-free by construction -- that is its whole
    purpose -- so a reasoning solver gets nothing to latch onto and may reason
    at length before answering, the effect already recorded in `_hinted_prompt`
    (minimax: 2851 tokens on the longer prompt, 760 on the shorter).

    MEASURABLE NOW, unlike the hinted arm. The text comes from
    `C.PLACEBO_RESPONSES`, which is fixed in config, so this needs no `generate`
    run -- and `placebo` gates `solve`, so measuring it is on the critical path.
    """
    return S._MASKED_WITH_HINT.format(
        options=S._format_options(item["options"]),
        response=response,
        topic=S._topic_clause(item, True),
    )


def _responses_for(bank: str, roster: str) -> dict[str, str]:
    """One generated response per item, for the hinted arm.

    The hinted budget CANNOT be measured before `generate` has run: the prompt
    contains a real tutor response, and substituting a placeholder would
    measure a prompt the study never sends -- the exact error this module
    exists to prevent, committed one level down.
    """
    path = R.RUNS / f"generate_bank{bank.upper()}" \
                    f"{'' if roster == 'kr' else '_' + roster}.jsonl"
    if not path.exists():
        sys.exit(
            f"cannot measure the hinted arm: {path.name} does not exist.\n"
            f"The hinted prompt embeds a real tutor response, so this budget is "
            f"only measurable AFTER `run.py generate --bank {bank} --roster "
            f"{roster}`. Measure the baseline arm now (--arm baseline), run "
            f"generate at that budget, then measure the hinted arm before solve.")
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("ok") and rec.get("response"):
            # Longest response per item: reasoning length tracks the prompt, so
            # the budget must be set from the worst case the stage will send,
            # not from an average one.
            prev = out.get(rec["item_id"], "")
            if len(rec["response"]) > len(prev):
                out[rec["item_id"]] = rec["response"]
    if not out:
        sys.exit(f"cannot measure the hinted arm: {path.name} has no successful "
                 f"responses to build a prompt from.")
    return out


def measure(bank: str, roster: str, n_items: int, n_calls: int,
            include: tuple[str, ...] = (), seed: int = 20260817,
            arm: str = "baseline") -> dict:
    # Roster, solvers and path come from `run.roster` -- the SAME resolver the
    # screen uses. Re-deriving them here would be a second copy of the rule,
    # which is how every defect in section 4.8-4.17 was built.
    rost = R.roster(roster)
    path = rost["path"]
    # THE GENERATOR ARM PROBES A DIFFERENT PANEL. The three solver arms measure
    # how long a SOLVER reasons about a masked item; this one measures how long a
    # TUTOR writes. Same question, different population -- and reusing `solvers`
    # here would have measured the wrong models entirely (spec 4.56).
    solvers = rost["generators"] if arm == "generate" else rost["solvers"]
    # Only the generator arm needs the solicitation frame; asking a solver arm's
    # roster for it would make a stub without that key fail for no reason.
    gen_combos = _gen_combos(rost) if arm == "generate" else ()
    items = [i for i in R.load_bank(bank)["items"] if i.get("type") == "mcq"]
    by_id = {i["id"]: i for i in items}

    # A uniform sample estimates the MEAN well and the MAXIMUM badly, and the
    # budget is set from the maximum. Items already observed to truncate are the
    # upper tail by direct evidence, so they are measured deliberately rather
    # than left to a lucky draw -- omitting them would reproduce the original
    # error of setting a budget from items that never stressed it.
    forced = [by_id[i] for i in include if i in by_id]
    missing = [i for i in include if i not in by_id]
    if missing:
        sys.exit(f"--include names items not in bank {bank}: {missing}")
    rng = random.Random(seed)
    pool = [i for i in items if i["id"] not in {f["id"] for f in forced}]
    sample = forced + rng.sample(pool, max(0, min(n_items - len(forced), len(pool))))

    if arm == "generate":
        # `generate` runs over the SCREENED items, so measuring over the whole
        # bank would draw prompts the stage never sends -- the same defect the
        # hinted arm hit in 4.40, where 5 of 6 draws had no response and the
        # budget rested on one prompt.
        kept = {i["id"] for i in R.screened_items(bank, R.load_bank(bank), roster)}
        elig = [i for i in items if i["id"] in kept]
        if elig:
            items = elig
            by_id = {i["id"]: i for i in items}
            forced = [by_id[i] for i in include if i in by_id]
            pool = [i for i in items if i["id"] not in {f["id"] for f in forced}]
            sample = forced + rng.sample(
                pool, max(0, min(n_items - len(forced), len(pool))))

    responses = _responses_for(bank, roster) if arm == "hinted" else {}
    if arm == "hinted":
        # SAMPLE FROM ITEMS THAT HAVE RESPONSES. The draw above is over every
        # mcq item in the bank, but `generate` only runs on items that PASSED
        # the screen -- 14 of Bank A's 40. A uniform draw of 6 therefore landed
        # on 5 items with no response, they were skipped, and the "measurement"
        # rested on ONE item: a maximum from four draws of a single prompt,
        # which is not a distribution. Observed 2026-08-18, and it reported OK.
        #
        # Restrict the pool first, then draw, so `--items N` means N measured
        # items rather than N attempted ones.
        eligible = [i for i in items if i["id"] in responses]
        if eligible:
            forced_ok = [f for f in forced if f["id"] in responses]
            pool = [i for i in eligible
                    if i["id"] not in {f["id"] for f in forced_ok}]
            rng2 = random.Random(seed)
            sample = forced_ok + rng2.sample(
                pool, max(0, min(n_items - len(forced_ok), len(pool))))
        # Only items that actually have a response can be measured; a forced
        # item without one would otherwise be dropped in silence.
        missing = [i["id"] for i in sample if i["id"] not in responses]
        if missing:
            sample = [i for i in sample if i["id"] in responses]
            print(f"[hinted] {len(missing)} sampled items have no generated "
                  f"response and are skipped: {missing[:5]}")
        if not sample:
            sys.exit("cannot measure the hinted arm: no sampled item has a "
                     "generated response.")

    # The arm is in the FILENAME, not just the record. The two arms are
    # different measurements of different prompts, and a single file holding
    # both would let a max be taken across them -- which is how one arm's
    # budget comes to be justified by the other's traces.
    out_path = R.RUNS / f"budget_{bank.upper()}_{roster}_{arm}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    per_model: dict[str, list[int]] = {s: [] for s in solvers}
    truncated: dict[str, int] = {s: 0 for s in solvers}
    failed: dict[str, int] = {s: 0 for s in solvers}

    # RESUMABLE AND APPEND-ONLY, like every other stage. Reasoning models at a
    # 16k ceiling make this the slowest thing in the harness per call, so an
    # interruption must cost nothing -- and re-measuring an already-measured
    # (item, model) pair would silently double-weight it in the maximum.
    done: dict[tuple[str, str], int] = {}
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            key = (r["item_id"], r["model"])
            done[key] = done.get(key, 0) + 1
            if r["ok"] and r.get("completion_tokens") is not None:
                per_model.setdefault(r["model"], []).append(r["completion_tokens"])
            elif r.get("error_kind") == "truncated_before_content":
                truncated[r["model"]] = truncated.get(r["model"], 0) + 1
            elif not r["ok"]:
                failed[r["model"]] = failed.get(r["model"], 0) + 1
    if done:
        print(f"[resume] {sum(done.values())} calls already measured in "
              f"{out_path.name}")

    with out_path.open("a", encoding="utf-8") as fh:
        for item in sample:
            base_prompt = (_baseline_prompt(item) if arm == "baseline"
                           else _hinted_prompt(item, responses[item["id"]])
                           if arm == "hinted" else None)
            for s in solvers:
                start = done.get((item["id"], s), 0)
                for c in range(start, max(start, n_calls)):
                    # The placebo arm sends FOUR different prompts per item, one
                    # per entry in C.PLACEBO_RESPONSES. Probing a single one
                    # would measure a quarter of the stage and set the budget
                    # from it. Cycling by call index covers all four and stays
                    # resumable, because the index is derived from the count of
                    # calls already on disk rather than from a fresh counter.
                    placebo = (C.PLACEBO_RESPONSES[c % len(C.PLACEBO_RESPONSES)]
                               if arm == "placebo" else None)
                    prompt = (base_prompt if placebo is None
                              else _placebo_prompt(item, placebo))
                    combo = None
                    if arm == "generate":
                        # REGIME AND SOLICITATION BOTH CHANGE THE PROMPT: P0
                        # sends no system prompt at all and the guarded regimes
                        # do, while the solicitation is the learner's turn.
                        # Probing one combination would measure a twelfth of the
                        # stage -- the mistake the placebo arm avoided by
                        # cycling its four utterances.
                        combo = gen_combos[c % len(gen_combos)]
                        msgs = _P().build_messages(
                            item, C.SOLICITATIONS[combo[1]], combo[0])
                        temp = C.GEN_TEMPERATURE
                    else:
                        msgs = [{"role": "user", "content": prompt}]
                        temp = C.SOLVER_TEMPERATURE
                    r = B.chat(s, msgs, path=path, temperature=temp,
                               max_tokens=PROBE_CEILING)
                    rec = {
                        "bank": bank.upper(), "roster": roster, "arm": arm,
                        "item_id": item["id"],
                        # Which placebo produced this trace. Without it a long
                        # tail cannot be attributed, and "one placebo is much
                        # worse than the others" is exactly the finding that
                        # would justify measuring more of them.
                        "placebo": placebo,
                        # Which (regime, solicitation) this trace came from.
                        # Without it a long tail cannot be attributed, and
                        # "P0 is much longer than the guarded regimes" is
                        # exactly the finding that would change the budget.
                        "regime": combo[0] if combo else None,
                        "solicitation_key": combo[1] if combo else None,
                        "model": s, "ok": r.ok, "error_kind": r.error_kind,
                        "completion_tokens": r.completion_tokens,
                        "prompt_tokens": r.prompt_tokens,
                        # Recorded because the budget decision has a RUNTIME
                        # cost as well as a correctness one: a heavy-tailed
                        # completion distribution means a generous cap is paid
                        # for in wall-clock on the rare long trace, and
                        # `C.SECONDS_PER_CALL` -- which the spec calls the thing
                        # that "bounds the runnable design" -- was measured at
                        # the OLD caps and is stale the moment a cap moves.
                        "latency_ms": r.latency_ms,
                        "probe_ceiling": PROBE_CEILING,
                        "study_version": C.STUDY_VERSION,
                        "measured": date.today().isoformat(),
                    }
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fh.flush()
                    if r.ok and r.completion_tokens is not None:
                        per_model[s].append(r.completion_tokens)
                    elif r.error_kind == "truncated_before_content":
                        truncated[s] += 1
                    elif not r.ok:
                        failed[s] += 1
                    print(f"  {item['id']:32s} {s:26s} "
                          f"{r.completion_tokens if r.ok else (r.error_kind or 'FAIL')}",
                          flush=True)

    return {"per_model": per_model, "truncated": truncated, "failed": failed,
            "n_items": len(sample), "n_calls": n_calls, "out": out_path,
            "arm": arm}


def report_from_file(bank: str, roster: str, arm: str) -> dict:
    """Build a report input from an existing measurement, WITHOUT probing.

    A budget measurement is append-only, so "what do we know about this arm?" is
    answerable from the file alone -- and asking it that way is free. The pipeline
    used to re-probe an arm it had already measured because the only way to get a
    verdict was to spend ~200 calls getting one, on a backend where quota is the
    binding constraint of the whole study.
    """
    out = R.RUNS / f"budget_{bank.upper()}_{roster}_{arm}.jsonl"
    per_model: dict[str, list[int]] = {}
    truncated: dict[str, int] = {}
    failed: dict[str, int] = {}
    items: set = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            m = rec.get("model")
            per_model.setdefault(m, [])
            truncated.setdefault(m, 0)
            failed.setdefault(m, 0)
            if rec.get("ok") and rec.get("completion_tokens") is not None:
                per_model[m].append(int(rec["completion_tokens"]))
                items.add(rec.get("item_id"))
            elif rec.get("error_kind") == "truncated_before_content":
                truncated[m] += 1
            elif not rec.get("ok"):
                failed[m] += 1
    return {"per_model": per_model, "truncated": truncated, "failed": failed,
            "n_items": len(items), "n_calls": sum(len(v) for v in per_model.values()),
            "out": out, "arm": arm}


#: Exit code meaning "this file is not yet a measurement", as distinct from 1,
#: "a recommendation is ready to apply". See the comment in `report`.
RC_INADEQUATE = 2


def report(res: dict) -> int:
    print(f"\nAttempted {res['n_items']} items x {res['n_calls']} calls on the "
          f"{res.get('arm', 'baseline')} arm at ceiling {PROBE_CEILING}. "
          f"Raw: {res['out'].name}\n")
    rc = 0
    arm = res.get("arm", "baseline")
    # IS THIS A MEASUREMENT? Asked of the FILE, through the same predicate the
    # stage guard uses (`run.budget_sample_shortfall`), so the reporter can never
    # recommend a number the guard will reject. Before 2026-08-20 it could and
    # did: Bank A's generate arm lost 146 of 192 calls to quota, every survivor
    # came from one item, and this function printed four confident
    # `RAISE ... recommended=` lines and returned 1 -- which a watcher reasonably
    # read as "measured". The floor was already correct; nothing SHOWED it here.
    # AN ABSENT FILE IS THE MOST INADEQUATE SAMPLE THERE IS, not an exemption
    # from the check. This read `... if res["out"].exists() else None` and then
    # `... if seen else None`, so a missing arm file skipped the floor entirely;
    # with no models, the loop below never runs either, so `rc` stayed 0. On
    # 2026-08-20 `--report-only` on the never-measured bs hinted arm therefore
    # printed "Attempted 0 items x 0 calls" and exited 0, `measure_arm.sh` read
    # that as "already measured", and the pipeline logged "measured hinted
    # budgets for bs -- configured ceiling clears them" one second after
    # generate completed. `solve bs` refused for want of that exact file and the
    # queue sat aborted for four hours.
    if not res["out"].exists():
        shortfall = "no measurement at all -- the file does not exist"
    else:
        seen = R.read_budget_file(res["out"])
        shortfall = R.budget_sample_shortfall(seen["items"], seen["ok_calls"])
    if shortfall:
        # DISTINCT EXIT CODE, because the two failures need opposite responses.
        # rc=1 means "a number is ready, apply it"; rc=2 means "there is no
        # number yet, measure more". A single nonzero code forced the caller to
        # tell them apart by grepping stdout for `recommended=`, which is exactly
        # what my watcher did on 2026-08-20 -- and an inadequate sample DOES
        # print `recommended=`, so it read as success. Codes, not prose.
        rc = RC_INADEQUATE
        print(f"INADEQUATE SAMPLE: {res['out'].name} holds {shortfall}.\n"
              f"  The maxima below are drawn from too little of the bank to be a "
              f"budget, and `run.py` will REFUSE the stage that sends this arm "
              f"until they are not. Re-measure with more items -- the file is "
              f"append-only, so a re-run adds to it rather than starting over.\n")
    for model, toks in res["per_model"].items():
        # THE ARM'S OWN TABLE. This read `C.solver_max_tokens` unconditionally,
        # so on the generator arm it printed the SOLVER ceiling next to a
        # generator's measured maximum -- "configured=16" for a model whose
        # replies run to 330 tokens -- and then advised editing the wrong
        # constant (spec 4.56).
        cur = C.arm_budget(arm, model)
        if res["truncated"][model]:
            # The probe itself truncated. The maximum is then a lower bound and
            # any recommendation derived from it is too, so say so rather than
            # printing a number that looks measured.
            print(f"{model}: {res['truncated'][model]} calls truncated AT THE "
                  f"PROBE CEILING {PROBE_CEILING} -- raise PROBE_CEILING and "
                  f"re-measure; no recommendation possible.")
            rc = max(rc, 1)
            continue
        if not toks:
            print(f"{model}: no successful calls "
                  f"({res['failed'][model]} failed) -- cannot recommend.")
            rc = max(rc, 1)
            continue
        mx = max(toks)
        rec = int(mx * SAFETY_FACTOR)
        # An UNMEASURED ceiling is not a large one. `generator_max_tokens`
        # returns None by design, and `None >= rec` would raise; reading it as 0
        # makes it RAISE, which is the honest verdict.
        flag = "OK " if (cur or 0) >= rec else "RAISE"
        print(f"{flag} {model}: n={len(toks)} max={mx} "
              f"p95={int(st.quantiles(toks, n=20)[18]) if len(toks) >= 20 else '-'} "
              f"median={int(st.median(toks))} "
              f"| configured={'UNMEASURED' if cur is None else cur} "
              f"recommended={rec} (max x {SAFETY_FACTOR})")
        if (cur or 0) < rec:
            rc = max(rc, 1)
    if rc == RC_INADEQUATE:
        return rc
    if rc:
        where = C.arm_budget_table(arm)
        after = ("re-screen" if arm == "baseline" else
                 "re-run the stage that sends this arm")
        print(f"\nEdit {where} and {after}. A stage run at a budget below the "
              f"recommendation truncates, and a truncated call is scored as a "
              f"miss -- on the baseline arm that biases the screen toward "
              f"RETAINING near-threshold items (spec 4.28); on the generate arm "
              f"it silently delivers a tutor reply cut off mid-sentence "
              f"(spec 4.56).")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bank", required=True)
    ap.add_argument("--roster", default="ol")
    ap.add_argument("--items", type=int, default=8)
    ap.add_argument("--calls", type=int, default=6)
    ap.add_argument("--arm", default="baseline",
                    choices=("baseline", "hinted", "placebo", "generate"),
                    help="baseline = the screen prompt; placebo = the negative "
                         "control's prompt, measurable now; hinted = the solve "
                         "prompt, which needs generate to have run first; "
                         "generate = the TUTOR prompt, measured over the "
                         "generator panel and all 12 regime x solicitation "
                         "combinations")
    ap.add_argument("--include", default="",
                    help="comma-separated item ids to measure regardless of the "
                         "draw; use for items already observed to truncate")
    ap.add_argument("--report-only", action="store_true",
                    help="answer from the existing measurement file and spend no "
                         "calls; exit codes are identical, so a caller can use "
                         "this to skip an arm that is already measured")
    a = ap.parse_args()
    include = tuple(x.strip() for x in a.include.split(",") if x.strip())
    if a.report_only:
        return report(report_from_file(a.bank, a.roster, a.arm))
    return report(measure(a.bank, a.roster, a.items, a.calls, include,
                          arm=a.arm))


if __name__ == "__main__":
    sys.exit(main())
