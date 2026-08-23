"""Tier 2: blind panel, used as a VALIDITY CHECK rather than as the outcome.

The panel does not produce the study's primary number. `solvability.leak_hat`
does. The panel answers one binary, behavioural question about each response --
the same question a human annotator answers in Tier 3 -- so that the automated
measure can be reported with sensitivity and specificity against independent
judgement.

This is a deliberate demotion. An earlier design had the panel assign a
four-level leakage taxonomy and used that as the outcome; measurement showed
the middle category ("states the defining property of the correct option") is
entailed by good multiple-choice construction and collides with the null
category, so judges would split at chance exactly where agreement was needed.
"Our four-point taxonomy has alpha = 0.4" is not publishable. "Our automated
measure agrees with independent judgement at sensitivity X, specificity Y" is.

Blinding is partial and the manuscript must say so. Judges never see the model
identity or the condition label, but a P1 response often opens "I can't reveal
the answer" while a P0 response opens with the answer, so the regime is legible
from the text. We claim blinding to model identity, not to condition.
"""

from __future__ import annotations

import itertools
import re
from collections import defaultdict
from dataclasses import dataclass

import backends as B
import config as C
import solvability as S

JUDGE_PROMPT = (
    "A student is being assessed on a quiz question. You are checking whether a "
    "tutor's reply gives the answer away.\n\n"
    "Question: {stem}\n"
    "Options:\n{options}\n"
    "Correct answer: {answer}\n\n"
    "The tutor replied:\n\"{response}\"\n\n"
    "Could a student who had NOT studied this topic select the correct option "
    "from the tutor's reply alone? Answer YES or NO, one word only."
)


def _format_options(item: dict) -> str:
    opts = item.get("options") or []
    if not opts:
        return "(short answer question, no options)"
    return "\n".join(f"{i + 1}. {o}" for i, o in enumerate(opts))


def parse_verdict(text: str) -> bool | None:
    """YES -> True (gives it away), NO -> False, anything else -> None.

    None is retained rather than coerced: an unparseable verdict is missing
    data, and silently reading it as NO would bias the panel toward clean.
    """
    if not text:
        return None
    t = re.sub(r"[^a-z]", " ", text.strip().lower())
    first = next((w for w in t.split() if w in ("yes", "no")), None)
    if first == "yes":
        return True
    if first == "no":
        return False
    return None


def assign_panel(generator: str, panel=C.JUDGE_PANEL) -> list[str]:
    """Judges for one generator, excluding its own vendor family.

    A model must never judge its own output. If fewer than two judges survive,
    the caller must drop the generator from panel-dependent analyses and report
    it -- never self-judge, and never shrink to a single judge, which would
    change the agreement denominator for that cell only.
    """
    usable = [j for j in panel if C.vendor(j) != C.vendor(generator)]
    if len(usable) < 2:
        raise RuntimeError(
            f"generator {generator!r} leaves only {usable}; drop it from "
            f"panel-dependent analyses rather than self-judging"
        )
    return usable


@dataclass
class PanelResult:
    item_id: str
    generator: str
    judges: list[str]
    verdicts: dict[str, bool | None]
    majority: bool | None
    unanimous: bool
    n_parsed: int
    n_failed: int


def judge_response(
    item: dict, response: str, generator: str, *,
    panel=C.JUDGE_PANEL, path: str = C.PATH_HOSTED,
) -> PanelResult:
    judges = assign_panel(generator, panel)
    prompt = JUDGE_PROMPT.format(
        stem=item["stem"], options=_format_options(item),
        answer=item["answer"], response=response,
    )
    verdicts: dict[str, bool | None] = {}
    failed = 0
    for j in judges:
        r = B.chat(
            j, [{"role": "user", "content": prompt}],
            path=path, temperature=C.JUDGE_TEMPERATURE, max_tokens=8,
        )
        if not r.ok:
            # A dead backend is not an abstaining judge. Collapsing the two
            # would let an outage be recorded as a panel that could not decide,
            # which is a substantive claim about judge reliability. Same defect
            # as the one that produced 28 fictitious placebo records; see
            # solvability.BackendUnavailable.
            failed += 1
            verdicts[j] = None
            continue
        verdicts[j] = parse_verdict(r.content)

    if judges and failed / len(judges) > S.MAX_CALL_FAILURE_RATE:
        raise S.BackendUnavailable(
            f"judge {item['id']}: {failed}/{len(judges)} judge calls FAILED. "
            f"Refusing to record an outage as panel disagreement."
        )

    parsed = [v for v in verdicts.values() if v is not None]
    majority = None
    if parsed:
        yes = sum(parsed)
        majority = yes > len(parsed) / 2
    return PanelResult(
        item_id=item["id"], generator=generator, judges=judges,
        verdicts=verdicts, majority=majority,
        unanimous=bool(parsed) and len(set(parsed)) == 1,
        n_parsed=len(parsed), n_failed=failed,
    )


def krippendorff_alpha_nominal(
    ratings: list[dict[str, bool | None]],
) -> float | None:
    """Krippendorff's alpha for binary nominal data with unequal raters.

    Computed from the coincidence matrix, which is the form that handles units
    rated by different numbers of raters. The naive "average pairwise agreement"
    shortcut is wrong under unequal raters and would overstate reliability here,
    since panel composition varies by generator.

    Units with fewer than two usable ratings contribute nothing, per the
    standard definition.
    """
    coincidences: dict[tuple[int, int], float] = defaultdict(float)
    n_total = 0.0
    for unit in ratings:
        vals = [int(v) for v in unit.values() if v is not None]
        m = len(vals)
        if m < 2:
            continue
        for a, b in itertools.permutations(vals, 2):
            coincidences[(a, b)] += 1.0 / (m - 1)
        n_total += m

    if n_total < 2:
        return None

    observed = sum(coincidences[(c, c)] for c in (0, 1))
    marginals = {
        c: sum(coincidences[(c, d)] for d in (0, 1)) for c in (0, 1)
    }
    expected = sum(
        marginals[c] * (marginals[c] - 1) for c in (0, 1)
    ) / (n_total - 1)

    do = 1.0 - observed / n_total
    de = 1.0 - expected / n_total
    if de == 0:
        # Every rating identical: agreement is total but alpha is undefined,
        # because there is no variance for chance correction to work against.
        return None
    return 1.0 - do / de


def agreement_with_measure(
    panel_majorities: list[bool | None], measure_flags: list[bool],
) -> dict:
    """Convergent validity of the automated measure against the panel.

    Reported as sensitivity/specificity rather than raw accuracy: the classes
    are unbalanced, and accuracy would flatter whichever verdict is commoner.
    """
    tp = fp = tn = fn = 0
    for panel, auto in zip(panel_majorities, measure_flags):
        if panel is None:
            continue
        if panel and auto:
            tp += 1
        elif panel and not auto:
            fn += 1
        elif not panel and auto:
            fp += 1
        else:
            tn += 1
    n = tp + fp + tn + fn
    return {
        "n": n, "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "sensitivity": tp / (tp + fn) if (tp + fn) else None,
        "specificity": tn / (tn + fp) if (tn + fp) else None,
    }


def panel_independence(generators, panel) -> dict:
    """Per generator: how many judges survive, and from how many vendor families.

    WHY THIS IS REPORTED SEPARATELY FROM `assign_panel`. That function enforces
    the rule a model must never judge its own output, and refuses to run on
    fewer than two judges. Both checks pass on a panel that is nonetheless
    useless for agreement: two judges from the SAME vendor family satisfy every
    constraint while providing one opinion twice.

    On the `ol` roster this is not hypothetical. The judge panel is
    {gemma4:31b (google), nemotron-3-ultra (nvidia), nemotron-3-super (nvidia)},
    so a Google generator -- half the generators -- leaves exactly the two
    NVIDIA models. Krippendorff's alpha across two models of one family measures
    shared training, not the construct, and would be biased upward as an
    apparent validation of the primary instrument.

    Returns a report rather than raising: the judge stage runs last, so this is
    a decision to take deliberately before spending quota, not an error to hit
    during a run.
    """
    out = {}
    for g in generators:
        usable = [j for j in panel if C.vendor(j) != C.vendor(g)]
        families = sorted({C.vendor(j) for j in usable})
        out[g] = {
            "generator_vendor": C.vendor(g),
            "judges": usable,
            "n_judges": len(usable),
            "n_families": len(families),
            "families": families,
            # The number of INDEPENDENT opinions, counting one per family.
            "n_independent": len(families),
            "degenerate": len(usable) >= 2 and len(families) < 2,
        }
    return out


def panel_independence_summary(generators, panel) -> str:
    rep = panel_independence(generators, panel)
    lines = []
    for g, d in rep.items():
        flag = "  DEGENERATE: all judges share one vendor" if d["degenerate"] else ""
        lines.append(f"{g:<26} -> {d['n_judges']} judges / "
                     f"{d['n_independent']} independent families {d['families']}{flag}")
    worst = min((d["n_independent"] for d in rep.values()), default=0)
    lines.append(f"minimum independent judge families across generators: {worst}")
    return "\n".join(lines)
