"""Masked-stem identifiability: the study's primary outcome.

    LEAK(response r, item i) = P(solver picks key | options(i), r, stem masked)
                             - P(solver picks key | options(i),    stem masked)

The solver never sees the question. It sees only the answer options and what
the tutor said. The estimand is therefore: does the tutor's text alone let
somebody who never saw the question pick the keyed option? That is exactly the
pedagogical harm -- the learner no longer has to perform retrieval.

Why not the obvious alternatives:

- A leakage TAXONOMY (does the response state the defining property of the
  correct option?) is degenerate. A well-built multiple-choice item partitions
  its distractors on the target concept, so any true statement about that
  concept determines the key. Measured: on "7 // 2" with options 3.5/3/4, all
  three models produced "// rounds down to a whole number" -- simultaneously
  the best hint a tutor could give and a complete determination. The taxonomy
  scores the ideal tutor worst.

- ACCURACY LIFT with the stem visible has no headroom. Measured: the solver
  answered both pilot items correctly with no hint at all. Every current model
  already knows a beginner Python bank.

Masking the stem restores headroom, verified: options-only the solver declined
or guessed wrong; options-plus-hint it picked the key both times.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field

import backends as B
import config as C

_MASKED_WITH_HINT = (
    "A quiz question you cannot see{topic} has these answer options:\n"
    "{options}\n\n"
    "A tutor said the following to a student attempting it:\n"
    '"{response}"\n\n'
    "Which option is the keyed-correct one? Reply with ONLY the option number."
)

_MASKED_BASELINE = (
    "A quiz question you cannot see{topic} has these answer options:\n"
    "{options}\n\n"
    "Which option is the keyed-correct one? Reply with ONLY the option number."
)


def _topic_clause(item: dict, control_topic: bool) -> str:
    """Topic phrase inserted into BOTH arms, or neither.

    Why this exists (discovered by running the screen on real items): when an
    item's stem names its own discriminating concept -- e.g. "Which of these
    built-in types is IMMUTABLE?" over [list, tuple, dict] -- a tutor hint that
    merely restates the stem ("immutable types cannot be modified") tells the
    masked solver what the question was about, and it picks the key. leak_hat
    then scores high for a hint that gave the STUDENT nothing, because the
    student already had the stem.

    Supplying the topic in both arms absorbs that stem-derived information into
    the baseline, so it is differenced away: a vacuous restatement now scores
    ~0, while a genuinely informative hint on a non-vacuous item still scores
    high. The alternative -- discarding every item whose stem names its
    discriminator -- throws away well-formed items for an artefact of the
    instrument rather than a property of the item.
    """
    if not control_topic:
        return ""
    topic = (item.get("topic") or "").strip()
    return f" (on the subject of {topic})" if topic else ""


def _format_options(options: list[str]) -> str:
    return "\n".join(f"{i + 1}. {o}" for i, o in enumerate(options))


def parse_choice(text: str, n_options: int) -> int | None:
    """Extract a 1-based option index from a solver reply.

    Returns None when the solver declined or gave something unparseable --
    a decline is data (it means the options alone were uninformative), not an
    error to be coerced into a guess.
    """
    if not text:
        return None
    m = re.search(r"\b([1-9][0-9]?)\b", text.strip())
    if not m:
        return None
    v = int(m.group(1))
    return v if 1 <= v <= n_options else None


class BackendUnavailable(RuntimeError):
    """Raised when calls are FAILING rather than being declined.

    These two are not interchangeable and must never be collapsed. A decline is
    a measurement -- it means the options alone were uninformative, which is
    precisely the quantity the baseline estimates. A transport failure is the
    absence of a measurement.

    This distinction was not hypothetical: a placebo run wrote 28 items in which
    every solver "declined" every call, which was in fact the router answering
    HTTP 400 `No credentials for provider: kiro` to all 1,680 calls. Because a
    failed call was mapped to the same `None` as a decline, the records were
    indistinguishable from a real and highly publishable finding (every solver
    refuses every content-free hint). Failing loudly is the only safe behaviour.
    """


#: Above this fraction of failed calls a record is refused rather than written.
#: Not zero: single transient 5xx responses are retried upstream and the odd
#: unretryable failure should not abandon an otherwise complete item.
MAX_CALL_FAILURE_RATE = 0.25


@dataclass
class SolveOutcome:
    picks: list[int | None]
    n_calls: int
    n_parsed: int
    n_failed: int            # transport failures; NOT declines
    accuracy: float          # over PARSED replies; declines excluded
    decline_rate: float
    #: Which error kinds failed, and how often. Carried so that a
    #: BackendUnavailable abort names its own cause: the 2026-08-16 abort on
    #: A-L03-05 reported only "19/60 failed" and took a manual probe to
    #: diagnose as transient noise rather than an exhausted quota.
    error_kinds: dict = field(default_factory=dict)

    @property
    def informative(self) -> bool:
        return self.n_parsed > 0


def _run(model: str, prompt: str, n_options: int, k: int, path: str) -> SolveOutcome:
    picks: list[int | None] = []
    failed = 0
    kinds: dict[str, int] = {}
    for _ in range(k):
        r = B.chat(
            model, [{"role": "user", "content": prompt}],
            path=path, temperature=C.SOLVER_TEMPERATURE,
            # Per-model, because reasoning models emit content only after their
            # trace. A shared 16-token budget silently converts them into
            # always-declining solvers.
            max_tokens=C.solver_max_tokens(model),
        )
        if not r.ok:
            failed += 1
            kinds[r.error_kind or "unknown"] = kinds.get(r.error_kind or "unknown", 0) + 1
            picks.append(None)
            continue
        picks.append(parse_choice(r.content, n_options))
    parsed = [p for p in picks if p is not None]
    # Declines are measured only over calls that actually returned.
    answered = k - failed
    return SolveOutcome(
        picks=picks, n_calls=k, n_parsed=len(parsed), n_failed=failed,
        accuracy=0.0,
        decline_rate=1.0 - len(parsed) / answered if answered else 1.0,
        error_kinds=kinds,
    )


def _guard_failures(failed: int, calls: int, where: str,
                    kinds: dict | None = None) -> None:
    if calls and failed / calls > MAX_CALL_FAILURE_RATE:
        detail = ""
        if kinds:
            top = ", ".join(f"{k}={v}" for k, v in
                            sorted(kinds.items(), key=lambda kv: -kv[1]))
            detail = (f" Error kinds: {top}."
                      f" (`transient`/`network` already survived "
                      f"{C.MAX_CALL_ATTEMPTS} attempts each; `gated` or "
                      f"`invalid_model` means roster loss, not an outage.)")
        # NAME THE REMEDY THAT WORKS. The generic advice was "fix the backend
        # and re-run", which on 2026-08-17 pointed at the one action that could
        # not help: the account had hit a quota wall, so every re-run failed the
        # same way. An operator following the message would have burned the
        # remaining allowance proving it. Quota is the failure this project
        # meets most often and the only one that no code change resolves.
        if kinds and kinds.get("auth"):
            remedy = (
                " AUTHENTICATION FAILED on every attempt. A 401 is credentials, "
                "not entitlement and not an outage: the backend is reachable and "
                "refusing. It is retried because it is usually a token refresh, "
                "so surviving all "
                f"{C.MAX_CALL_ATTEMPTS} attempts means the credential is "
                "genuinely bad -- re-authenticate rather than re-running.")
        elif kinds and kinds.get("quota_exhausted"):
            remedy = (
                " QUOTA EXHAUSTED -- this is not an outage and re-running will "
                "not help. The account's allowance is spent for the current "
                "window; it restores on the provider's clock, so wait it out or "
                "switch roster. Stages are resumable and append-only, so the "
                "completed items are already on disk and nothing is lost by "
                "stopping here.")
        else:
            remedy = " Fix the backend and re-run."
        raise BackendUnavailable(
            f"{where}: {failed}/{calls} calls FAILED "
            f"({failed / calls:.0%} > {MAX_CALL_FAILURE_RATE:.0%}). These are "
            f"transport failures, not solver declines; writing them would "
            f"record absent measurements as data.{detail}{remedy}"
        )


def baseline_identifiability(
    item: dict, *, solvers=C.SOLVERS, k: int = C.SOLVER_K_BASELINE,
    path: str = C.PATH_HOSTED, control_topic: bool = True,
) -> dict:
    """P(solver picks key | options only). The headroom screen.

    Estimated per ITEM over more draws than the hint arm, because it is the
    more precise quantity and every hint measurement is differenced against it.
    """
    options, key = item["options"], item["answer_index"] + 1
    prompt = _MASKED_BASELINE.format(
        options=_format_options(options),
        topic=_topic_clause(item, control_topic),
    )
    per_solver, hits, parsed, failed = {}, 0, 0, 0
    kinds: dict[str, int] = {}
    for s in solvers:
        out = _run(s, prompt, len(options), k, path)
        for kk, vv in out.error_kinds.items():
            kinds[kk] = kinds.get(kk, 0) + vv
        ok = sum(1 for p in out.picks if p == key)
        per_solver[s] = {
            "hits": ok, "parsed": out.n_parsed, "calls": out.n_calls,
            "failed": out.n_failed,
            "decline_rate": round(out.decline_rate, 3),
        }
        hits += ok
        parsed += out.n_parsed
        failed += out.n_failed
    total = k * len(solvers)
    _guard_failures(failed, total, f"baseline {item['id']}", kinds)
    return {
        "item_id": item["id"],
        "n_options": len(options),
        "chance": 1.0 / len(options),
        "baseline": hits / total if total else 0.0,
        "control_topic": control_topic,
        "parsed_fraction": parsed / total if total else 0.0,
        "n_failed_calls": failed,
        # WHY THE KINDS ARE PERSISTED, not just counted. `kinds` was aggregated
        # here, handed to `_guard_failures` for its abort message, and then
        # dropped. So a run that stayed under the abort threshold recorded THAT
        # calls failed but not WHAT failed -- and the distinction is exactly the
        # one section 4.12 rests on: `transient`/`network` are retried and are
        # noise, while `gated`/`withdrawn`/`invalid_model` are permanent roster
        # loss the manuscript must report. The Bank B pilot lost 23 calls this
        # way and they can no longer be attributed. Empty dict = no failures.
        "error_kinds": dict(kinds),
        "per_solver": per_solver,
    }


#: Tolerance for "at or below the threshold". Six orders of magnitude below one
#: call's worth of the outcome (1/60 = 0.0167 at the smallest k in use), so it
#: can only ever resolve an exact tie and never a genuine margin.
THRESHOLD_TOL = 1e-12


def at_or_below(value: float, threshold: float) -> bool:
    """`value <= threshold`, with exact ties resolved in favour of passing.

    ONE definition, used by `passes_headroom`, by `freeze_bank`'s invariance
    report and by the figure generator. Three copies of this comparison is how
    the ULP bug would come back: `passes_headroom` was fixed first, which then
    made `freeze_bank` report the invariance band as violated by the same ULP,
    because it still used a bare `<=` against the same threshold.

    Every silent-wrongness bug in this project has been two components holding
    separate copies of one rule. This is the rule.
    """
    return value <= threshold or math.isclose(
        value, threshold, rel_tol=THRESHOLD_TOL, abs_tol=THRESHOLD_TOL)


def passes_headroom(baseline_record: dict, margin: float = C.HEADROOM_MARGIN) -> bool:
    """An item is usable only if the options alone do not already give it away.

    Applied BEFORE any tutor response exists, so it cannot be tuned to produce
    a desired leakage result. This is the mechanical answer to the circularity
    objection against an author-written bank.

    TIES PASS, AND THE TIE MUST SURVIVE FLOATING POINT. With three options and
    a 0.15 margin the threshold is exactly 1/3 + 3/20 = 29/60, and a solver
    panel of 3 x k=20 produces baselines that are exact sixtieths -- so landing
    precisely ON the threshold is not a measure-zero curiosity, it is a
    reachable outcome. `A-L05-03` scored exactly 29/60 and was rejected, because
    `29/60` and `1/3 + 0.15` differ by one ULP in binary floating point
    (0.48333333333333334 vs 0.48333333333333328). An item's inclusion in the
    study was decided by representation error.

    `math.isclose` restores the stated rule. The tolerance is far below one
    call's worth of the outcome (1/60 = 0.0167 at the smallest k we use), so it
    can only affect exact ties -- never a genuine margin.
    """
    threshold = baseline_record["chance"] + margin
    b = baseline_record["baseline"]
    return at_or_below(b, threshold)


def truncation_slack(baseline_record: dict, margin: float = C.HEADROOM_MARGIN) -> float:
    """How far a record's verdict is from being reversible by its failed calls.

    A failed call is scored as a miss, so it can only ever have DEPRESSED the
    baseline. The largest correction a re-measurement could produce is therefore
    `n_failed_calls / total_calls`, and it can only move the baseline UP. That
    makes the arithmetic one-sided and exact:

      - a RETAINED item is at risk, because rising baseline crosses the
        threshold from below;
      - a REJECTED item is safe by construction -- it is already above the
        threshold and correction only pushes it further away.

    Returns `distance_to_threshold - max_possible_correction`. A value >= 0
    means the verdict CANNOT change no matter what the failed calls would have
    returned; < 0 means the verdict is not established by this measurement and
    the item must be re-measured at a corrected budget.

    WHY THIS IS A FUNCTION AND NOT A PARAGRAPH IN A COMMIT MESSAGE. After the
    2026-08-17 Bank B pilot showed the budget was calibrated on Bank A's items
    and never re-measured for Bank B, the immediate question for every existing
    screen was "must it be re-run?". That question has an exact answer, and
    deriving it by hand per bank is how the two-copies-of-a-rule defects in
    section 4.8-4.17 were built. Derive it here, once.
    """
    failed = baseline_record.get("n_failed_calls", 0)
    if not failed:
        # Nothing was scored as a miss for want of an answer, so no correction
        # exists and no accounting is needed to prove it. Checked FIRST because
        # it is the common case, and because requiring the measurement fields
        # of a record that cannot be threatened would reject valid records for
        # a risk they do not carry.
        return float("inf")

    threshold = baseline_record["chance"] + margin
    b = baseline_record["baseline"]
    if not at_or_below(b, threshold):
        return float("inf")  # rejected: correction moves it further away

    # Total calls come from `k_per_solver x |solvers|`, which is how
    # `baseline_identifiability` computes the denominator of `baseline` itself.
    # Re-deriving it by summing `per_solver` call counts would be a second copy
    # of that rule, and the two would disagree the moment a solver drops out
    # mid-item -- exactly the condition under which this bound gets consulted.
    k = baseline_record.get("k_per_solver")
    solvers = baseline_record.get("solvers")
    if k and solvers:
        total = k * len(solvers)
    else:
        total = sum(v.get("calls", 0)
                    for v in (baseline_record.get("per_solver") or {}).values())
    if not total:
        # Failures are recorded but the call count is not, so the correction
        # cannot be bounded. Report the pessimistic answer rather than a
        # reassuring one: an unverifiable verdict is not a safe verdict.
        return float("-inf")
    return (threshold - b) - failed / total


def leak_score(
    item: dict, response: str, baseline: float, *,
    generator: str, solvers=None, k: int = C.SOLVER_K_HINT,
    path: str = C.PATH_HOSTED, control_topic: bool = True,
) -> dict:
    """leak_hat in [-1, 1] for one tutor response.

    Solvers of the generator's own vendor family are excluded so that no model
    scores its own output.
    """
    options, key = item["options"], item["answer_index"] + 1
    # Panel is passed in, never read from a module global: the roster in play
    # must match the generator's, or vendor exclusion is computed against the
    # wrong panel and a model can end up scoring its own family's output.
    panel = C.SOLVERS if solvers is None else tuple(solvers)
    usable = [s for s in panel if C.vendor(s) != C.vendor(generator)]
    if len(usable) < 2:
        raise RuntimeError(
            f"generator {generator} leaves only {usable} solvers; drop it from "
            f"solver-dependent analyses rather than scoring it with its own family"
        )
    prompt = _MASKED_WITH_HINT.format(
        options=_format_options(options), response=response,
        topic=_topic_clause(item, control_topic),
    )
    hits, total, parsed, failed, per_solver = 0, 0, 0, 0, {}
    kinds: dict[str, int] = {}
    for s in usable:
        out = _run(s, prompt, len(options), k, path)
        for kk, vv in out.error_kinds.items():
            kinds[kk] = kinds.get(kk, 0) + vv
        ok = sum(1 for p in out.picks if p == key)
        per_solver[s] = {"hits": ok, "calls": out.n_calls,
                         "parsed": out.n_parsed, "failed": out.n_failed}
        hits += ok
        total += out.n_calls
        parsed += out.n_parsed
        failed += out.n_failed
    _guard_failures(failed, total, f"leak {item['id']}", kinds)
    hinted = hits / total if total else 0.0
    return {
        "item_id": item["id"],
        "generator": generator,
        "solvers": usable,
        "hinted": hinted,
        "baseline": baseline,
        "leak_hat": hinted - baseline,
        "control_topic": control_topic,
        # Logged so the willingness confound stays auditable after the fact: a
        # lift in `hinted` that is all parse-rate and no conditional accuracy is
        # the placebo artefact, not leakage. Without these fields the two are
        # indistinguishable in the saved record.
        "parsed_fraction": parsed / total if total else 0.0,
        "hinted_cond_acc": hits / parsed if parsed else None,
        # This record guarded its failure count and then recorded neither the
        # count nor the kinds -- the only stage of the three to lose both. A
        # leak measurement with unrecorded attrition cannot be audited at all.
        "n_failed_calls": failed,
        "error_kinds": dict(kinds),
        "per_solver": per_solver,
    }


def placebo_identifiability(
    item: dict, *, solvers=C.SOLVERS, placebos=C.PLACEBO_RESPONSES,
    k: int = C.SOLVER_K_HINT, path: str = C.PATH_HOSTED,
    control_topic: bool = True,
) -> dict:
    """The negative control: leak measured on content-free tutor utterances.

    Stored PER SOLVER and PER PLACEBO rather than pre-aggregated, because
    `leak_score` drops solvers sharing the generator's vendor family. The
    placebo must be differenced over the SAME solver subset as the response it
    corrects, so the subset cannot be chosen until the generator is known.
    Aggregating here would silently compare a 3-solver placebo against a
    2-solver hint arm.
    """
    options, key = item["options"], item["answer_index"] + 1
    topic = _topic_clause(item, control_topic)
    per_solver: dict[str, dict] = {
        s: {"hits": 0, "calls": 0, "parsed": 0, "failed": 0} for s in solvers
    }
    per_placebo: dict[str, float] = {}
    kinds: dict[str, int] = {}

    for text in placebos:
        prompt = _MASKED_WITH_HINT.format(
            options=_format_options(options), response=text, topic=topic,
        )
        hits, total = 0, 0
        for s in solvers:
            out = _run(s, prompt, len(options), k, path)
            for kk, vv in out.error_kinds.items():
                kinds[kk] = kinds.get(kk, 0) + vv
            ok = sum(1 for p in out.picks if p == key)
            per_solver[s]["hits"] += ok
            per_solver[s]["calls"] += out.n_calls
            per_solver[s]["parsed"] += out.n_parsed
            per_solver[s]["failed"] += out.n_failed
            hits += ok
            total += out.n_calls
        per_placebo[text] = hits / total if total else 0.0

    calls = sum(v["calls"] for v in per_solver.values())
    hits = sum(v["hits"] for v in per_solver.values())
    parsed = sum(v["parsed"] for v in per_solver.values())
    failed = sum(v["failed"] for v in per_solver.values())
    _guard_failures(failed, calls, f"placebo {item['id']}", kinds)
    return {
        "item_id": item["id"],
        "n_failed_calls": failed,
        "error_kinds": dict(kinds),
        "placebo_hinted": hits / calls if calls else 0.0,
        "parsed_fraction": parsed / calls if calls else 0.0,
        "control_topic": control_topic,
        "k_per_placebo_solver": k,
        "per_placebo": per_placebo,
        "per_solver": per_solver,
    }


def placebo_for_solvers(record: dict, solvers) -> float | None:
    """Placebo hit rate restricted to `solvers` -- the generator's usable subset.

    Returns None unless the record covers EVERY requested solver, so the caller
    reports a missing correction rather than silently applying a mismatched one.

    Full coverage is required, not merely some overlap. `placebo_identifiability`
    stores per solver precisely so the control can be differenced over the same
    subset as the hint arm it corrects; accepting a partial subset would compare
    a 2-solver placebo against a 3-solver hint arm and call the difference
    leakage. That is the same failure the per-solver storage exists to prevent,
    reintroduced one function later.

    A None here surfaces as a null `leak_adj` -- a visible hole in the data,
    which is the intended outcome. A wrong correction is invisible.
    """
    per = record.get("per_solver") or {}
    wanted = list(solvers)
    if not wanted or any(s not in per for s in wanted):
        return None
    hits = sum(per[s]["hits"] for s in wanted)
    calls = sum(per[s]["calls"] for s in wanted)
    return hits / calls if calls else None


def shuffled_cells(cells: list, seed: int = C.SEED) -> list:
    """Fixed-seed shuffle so provider slowness cannot systematically penalise
    one model, and so the run order is reproducible."""
    rng = random.Random(seed)
    out = list(cells)
    rng.shuffle(out)
    return out


def per_solver_leak(baseline_record: dict, leak_record: dict) -> dict:
    """Within-solver leak: leak_s = hinted_s - baseline_s, per solver.

    The pooled figure (`leak_hat`) divides summed hits by summed calls, which
    silently weights each solver by how often it answers. That is not a
    cosmetic concern here: measured over the 40-item screen, `kr/claude-haiku-4.5`
    returns a parseable answer to only 8% of calls on retained items and to none
    at all on 18 of 29 of them, while `kr/deepseek-3.2` answers 96% of the time.
    A solver contributing no information still contributes a third of the
    denominator.

    That does not bias the pooled difference -- the same denominator applies to
    both arms -- but it compresses the scale, and it concentrates the
    willingness artefact: a hint that moves a near-silent solver from declining
    to answering lifts the hint arm alone. Differencing WITHIN each solver makes
    every solver its own control, so heterogeneous decline policies cannot
    masquerade as leakage.

    Returns one entry per solver present in BOTH records, plus the unweighted
    mean across solvers (each solver counting once, regardless of talkativeness).
    """
    out: dict[str, dict] = {}
    for s, hv in leak_record.get("per_solver", {}).items():
        bv = baseline_record.get("per_solver", {}).get(s)
        if bv is None or not hv.get("calls") or not bv.get("calls"):
            continue
        hinted_s = hv["hits"] / hv["calls"]
        base_s = bv["hits"] / bv["calls"]
        out[s] = {
            "hinted": hinted_s,
            "baseline": base_s,
            "leak": hinted_s - base_s,
            "hinted_parsed": hv.get("parsed", 0) / hv["calls"],
            "baseline_parsed": bv.get("parsed", 0) / bv["calls"],
            # A solver silent in BOTH arms measured nothing; its zero is not
            # evidence of no leakage and must be excludable downstream.
            "informative": bool(hv.get("parsed") or bv.get("parsed")),
        }
    informative = [v["leak"] for v in out.values() if v["informative"]]
    return {
        "per_solver": out,
        "mean_leak_across_solvers": (
            sum(informative) / len(informative) if informative else None),
        "n_informative_solvers": len(informative),
    }
