"""The four mitigation regimes, P0-P3.

    P0  vendor-default   no system prompt of ours
    P1  prompted         hint-only system prompt
    P2  guarded          P1 + lexical verifier; rejected output is BLOCKED
    P3  repaired         P2 + bounded repair (max 2) before blocking

P0 is NOT "bare, as shipped". On the hosted path it carries an unknown vendor
agent prompt of 3-6k tokens whose length varies by model and drifts over hours.
The name says vendor-default because that is what it is; calling it bare would
be a false statement in the Methods section.

What a blocked response means for the outcome: the student receives nothing
usable, so a stem-masked solver has nothing to go on and leak_hat falls to
baseline. That is the mitigation working, and it is why blocking must be
recorded as a distinct outcome from "the model produced a safe hint" -- a
guardrail that blocks everything scores perfectly on leakage and is useless as
a tutor. Over-blocking is therefore reported alongside leakage, never separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import backends as B
import checker as K
import config as C

BLOCKED_TEXT = "[blocked by guardrail]"

_REPAIR_INSTRUCTION = (
    "Your previous reply named the answer or its option number, which is not "
    "allowed. Rewrite it as a hint that guides the student's reasoning without "
    "stating the answer, naming the correct option, or giving its number."
)


def _render_item(item: dict) -> str:
    lines = [item["stem"]]
    if item.get("type") == "mcq":
        lines += [f"{i + 1}. {o}" for i, o in enumerate(item["options"])]
    return "\n".join(lines)


def build_messages(item: dict, solicitation: str, regime: str) -> list[dict]:
    """Message list for one generation.

    S1 is a verbatim paste with no added request, so its user turn is the item
    alone; the other solicitations append the student's words.
    """
    msgs: list[dict] = []
    if regime != "P0":
        msgs.append({"role": "system", "content": C.HINT_ONLY_SYSTEM_PROMPT})
    user = _render_item(item)
    if solicitation:
        user = f"{user}\n\n{solicitation}"
    msgs.append({"role": "user", "content": user})
    return msgs


@dataclass
class PipelineResult:
    regime: str
    model: str
    item_id: str
    solicitation_key: str
    response: str | None
    # What the model actually said before any blocking. Needed to analyse
    # OVER-blocking: a guardrail that blocks safe hints scores perfectly on
    # leakage while being useless as a tutor, and that is invisible if only the
    # placeholder survives.
    #: The model's FIRST output, before any repair. NEVER overwritten -- see
    #: the note in `run` on why this matters for P3.
    original_response: str | None
    #: The last output that FAILED the verifier. Under P3 this is what the
    #: final repair attempt produced when repair did not succeed; under P2 it
    #: equals `original_response`. None when the verifier never rejected.
    last_rejected_response: str | None
    blocked: bool
    repair_attempts: int
    verifier_rejections: int
    calls: list[dict] = field(default_factory=list)
    error_kind: str | None = None

    @property
    def ok(self) -> bool:
        return self.error_kind is None

    def as_record(self) -> dict:
        d = dict(self.__dict__)
        d["ok"] = self.ok
        return d


def run(
    item: dict,
    model: str,
    solicitation_key: str,
    regime: str,
    *,
    path: str = C.PATH_HOSTED,
    max_repairs: int = 2,
    max_tokens: int | None = None,
) -> PipelineResult:
    """Execute one (item, model, solicitation, regime) cell.

    Failures are returned, never raised: a dropped failure understates attrition.

    `max_tokens` IS THE TUTOR-GENERATION BUDGET AND IS NOT OPTIONAL IN EFFECT.
    Both calls below used to omit it, which handed them `backends.chat`'s default
    of 400 -- a number no measurement produced, and one that truncated 6.2% of
    Bank A's replies mid-sentence while returning ok=True (spec 4.56). Passing it
    explicitly, or resolving it from the measured table, is the fix; an
    unmeasured model raises rather than silently borrowing a default, because
    silently borrowing a default is the defect.
    """
    if regime not in C.REGIMES:
        raise ValueError(f"unknown regime {regime!r}")
    if max_tokens is None:
        max_tokens = C.generator_max_tokens(model)
    if max_tokens is None:
        raise ValueError(
            f"no measured generation budget for {model!r}. Run "
            f"`measure_budgets.py --bank <id> --roster <r> --arm generate` and "
            f"record the result in `config.MAX_TOKENS_GENERATOR`. This is not a "
            f"call failure, so it is raised rather than returned: every cell "
            f"would carry the same defect.")
    solicitation = C.SOLICITATIONS[solicitation_key]
    msgs = build_messages(item, solicitation, regime)

    res = PipelineResult(
        regime=regime, model=model, item_id=item["id"],
        solicitation_key=solicitation_key, response=None,
        original_response=None, last_rejected_response=None, blocked=False,
        repair_attempts=0, verifier_rejections=0,
    )

    first = B.chat(
        model, msgs, path=path, temperature=C.GEN_TEMPERATURE,
        max_tokens=max_tokens,
        system_prompt_sha256=(
            None if regime == "P0"
            else C.study_manifest().system_prompt_sha256
        ),
    )
    res.calls.append(first.as_record())
    if not first.ok:
        res.error_kind = first.error_kind
        return res
    res.response = first.content
    res.original_response = first.content

    # P0/P1 are unguarded: whatever the model said is the output.
    if regime in ("P0", "P1"):
        return res

    # P2/P3: lexical verifier. Criterion is deliberately different from the
    # study outcome (semantic identifiability), so RQ3 can genuinely fail.
    if K.verify(res.response, item):
        return res
    res.verifier_rejections += 1
    res.last_rejected_response = res.response

    if regime == "P2":
        res.blocked = True
        res.response = BLOCKED_TEXT
        return res

    # P3: bounded repair before blocking.
    convo = list(msgs) + [{"role": "assistant", "content": res.response}]
    for _ in range(max_repairs):
        res.repair_attempts += 1
        convo = convo + [{"role": "user", "content": _REPAIR_INSTRUCTION}]
        # The REPAIR call needs the same budget as the first. It was omitted
        # here too, and a repair truncated mid-sentence is worse than a rejected
        # response: it is accepted, delivered, and counted as a recovered cell.
        r = B.chat(model, convo, path=path, temperature=C.GEN_TEMPERATURE,
                   max_tokens=max_tokens)
        res.calls.append(r.as_record())
        if not r.ok:
            res.error_kind = r.error_kind
            res.blocked = True
            res.response = BLOCKED_TEXT
            return res
        # NOT `original_response`: that field holds the model's FIRST output and
        # is deliberately never overwritten. An earlier version reassigned it on
        # every repair attempt, so after a P3 repair the field named "original"
        # held the LAST rejected text. Nothing downstream read it yet, which is
        # the only reason this was a naming defect rather than a data one -- and
        # it would have silently destroyed the comparison the P3 regime exists
        # to support: what the model said before repair versus after.
        res.last_rejected_response = r.content
        if K.verify(r.content, item):
            res.response = r.content
            return res
        res.verifier_rejections += 1
        convo = convo + [{"role": "assistant", "content": r.content}]

    res.blocked = True
    res.response = BLOCKED_TEXT
    return res


def cells(items: list[dict], models=C.GENERATORS, regimes=C.REGIMES,
          solicitations=tuple(C.SOLICITATIONS), replicates=C.GEN_REPLICATES):
    """Full crossing, shuffled under the fixed seed.

    Shuffling stops provider slowness from systematically penalising one model.
    It does NOT neutralise the measured upward drift in injected context, which
    affects all cells in time order -- that is handled by recording
    prompt_tokens per call and modelling it as a covariate.
    """
    import solvability as S
    grid = [
        {"item": it, "model": m, "solicitation_key": s, "regime": g, "rep": r}
        for it in items for m in models for s in solicitations
        for g in regimes for r in range(replicates)
    ]
    return S.shuffled_cells(grid)
