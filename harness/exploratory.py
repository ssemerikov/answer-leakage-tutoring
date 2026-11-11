"""Stated vs given: does a tutor reply NAME the answer, or only GIVE it away?

Descriptive, from data that already exist: no model calls, no network. Written
for the 2026-11 revision, whose central claim is that replies under "do not
reveal" instructions and an answer-text filter rarely STATE the answer yet
still GIVE it away (the key stays recoverable from the reply).

ONE DEFINITION EACH, BORROWED, NEVER RE-TYPED
--------------------------------------------
stated  `checker.check_explicit(reply, item)` -- the Tier-1 lexical detector,
        the same function the P2/P3 verifier calls -- on the reply AS DELIVERED
        (field `response`), with the bank's CURRENT item table
        (`run._bank_path`: `bank_a.json`, `bank_b_sample.json`). A blocked turn
        is delivered as `pipelines.BLOCKED_TEXT`; it is counted as not stated,
        and this module raises if the detector ever fires on it. See
        `stated_definition` in the artefact for what this detector is NOT.
given   `leak_adj` at the 0.25 cut by `results.exceeds_threshold` -- the
        comparison `results.threshold_sensitivity` uses -- so the share here IS
        the share in `threshold_sensitivity["0.25"]`. `build` asserts that, for
        every cut of the grid (`given_by_grid`).

Rows are exactly `human_validation.join_generate_solve`: one per delivered reply
with a `leak_adj`, in solve-file order, so the regime means reproduce the
results file's `descriptives.leak_adj.regime` exactly (asserted too).

INTERVALS (fix round 1). `ci` (Wilson) and `mean_ci` (normal) treat replies as
independent; they are kept, and labelled "unadjusted for item clustering".
Beside each sits `ci_cluster_item`, from `analyse.cluster_bootstrap_ci`: items
are resampled, which is the honest unit (14 items on Bank A, 27 on Bank B).

READING IT
----------
* Under P2/P3 `stated` is EXACTLY 0 BY CONSTRUCTION: the delivered text passed
  the same detector, or is the blocked message. Those entries carry
  `by_construction: true`; a guarded reply the detector flags raises.
  Stated-vs-given is informative for P0 and P1 only.
* `stated` depends on answer-form coverage (`by_form_coverage`); compare banks,
  and stated with given, on the items that HAVE forms.
* `as_run_guardrail` is what the verifier did at generation time, read from the
  recorded fields. `diagnostic_rederivation` runs TODAY's checker on the P2/P3
  first candidates beside the recorded rejections and never asserts agreement.
  The answer-form correction preceded generation (spec 4.66), so agreement shows
  reproducibility, not that the correction had no effect.

RESAMPLED CONTRASTS (P1F, spec 4.69)
-----------------------------------
A headline claim must pass the pre-specified Holm test in `results_*.json` AND
survive item resampling (design revision 1, D15). `contrasts_ci_cluster_item`
gives, per bank, the R2 and R3 contrasts plus P3-P1: the mean over items of
each item's difference of condition (or request-style) means of `leak_adj`,
with `analyse.item_bootstrap_ci` (items resampled, each drawn item keeping its
own difference). In this balanced design that mean IS the item fixed-effects
estimate, and `build` refuses to write unless every estimate equals the
results file's to 1e-6 (P3-P1: the descriptive regime means). The intervals
sit BESIDE the Holm tests, never instead of them.

`data/exploratory_pooled_A-BS_ol.json` (`build_pooled`) does the same over both
banks with items resampled WITHIN their bank (stratified), and adds the bank x
condition interaction: Bank B's mean per-item (Px - P0) minus Bank A's, each
bank resampled within itself. Both are checked against
`results_pooled_A-BS_ol.json` (R2, R3, R4 interaction).

`guardrail_detail.leak_by_delivery` splits P2/P3 replies by how they were
delivered (blocked, repaired, first pass), read from the recorded generate
fields, never by re-running the checker; the five groups partition the P2 and
P3 rows (asserted) and their counts equal `as_run_guardrail`.

Provenance hashes of JSON inputs are taken over a canonical serialisation with
`generated_at` removed (`content_sha256`), so a re-run of `results.py` that
changes nothing but its timestamp leaves this artefact current.

Bank B is GFDL text: this module writes counts, ids, hashes and reason codes for
it, never a stem, an option, a reply or a matched span.

    python3 exploratory.py [--roster ol]     # both banks AND the pooled file
    python3 exploratory.py --bank A|bs       # that bank only
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyse as A              # noqa: E402
import checker as K              # noqa: E402
import config as C               # noqa: E402
import human_validation as hv    # noqa: E402
import pipelines as PL           # noqa: E402
import results as RS             # noqa: E402
import run as R                  # noqa: E402

#: The "given" cut. A point of the pre-registered grid, never a new one.
GIVEN_CUT = 0.25
DETECTOR = "checker.check_explicit"
#: Regimes whose replies went through the lexical verifier at generation time.
GUARDED = ("P2", "P3")
#: Regimes where "stated" is a measurement rather than a tautology.
INFORMATIVE = ("P0", "P1")
#: The banks the pooled file pools (CLI ids), as `results.py --pool A,bs`.
POOL_DEFAULT = ("A", "bs")
#: A P3 cell is blocked after its first candidate and every repair failed.
MAX_REJECTIONS = 1 + inspect.signature(PL.run).parameters["max_repairs"].default

#: The few-clusters caveat (P1C re-review N2): 9 to 27 items per block.
_UNDERCOVER = ("Every ci_cluster_item is approximate: a percentile bootstrap "
               "over 9\u201327 items tends to undercover.")
#: The Monte Carlo caveat (P1F review Minor 1). B is read from the shared
#: bootstrap's own default; 0.002 is the review's seed sweep (endpoint sd up to
#: 0.0018 at this B, `scratch/P1F-review/seed_sweep_out.json`). No endpoint
#: value is written here: the note must stay true when an endpoint moves.
_MONTE_CARLO = (
    "Endpoints carry Monte Carlo error at B = "
    f"{inspect.signature(A.item_bootstrap_ci).parameters['B'].default:,}: an "
    "endpoint within about 0.002 of zero is borderline, and its sign should not "
    "carry a claim.")

#: Every prose string this module writes into an artefact. Kept in one table so
#: a test can confirm that the Bank B file carries no text but these.
NOTES = {
    "detector": (
        "stated = checker.check_explicit on the reply as delivered (field "
        "`response`), using the bank's current item table. A blocked turn is "
        "delivered as the blocked message and counted as not stated; the build "
        "raises if the detector fires on it."),
    "lower_bound": (
        "stated means flagged by the Tier-1 lexical detector "
        "(checker.check_explicit), which names or asserts the key. It misses "
        "unframed statements on all items and most statements on items without "
        "answer forms, and it has the residual false positives of spec 4.38. So "
        "it is neither a strict lower bound nor a validated measure of stating. "
        "Compare banks, and stated with given, on items with answer forms "
        "(by_form_coverage.with_forms)."),
    "guarded": (
        "Under P2/P3 stated is exactly 0 by construction: the delivered P2/P3 "
        "text passed the same detector, or is the blocked message. "
        "Stated-vs-given is informative for P0 and P1 only."),
    "by_construction": (
        "exactly 0 by construction: the delivered P2/P3 text passed the same "
        "detector, or is the blocked message"),
    # P1C re-review N1: not_stated_among_given is the complement of stated.
    "by_construction_complement": (
        "exactly 1 by construction: flagged is 0 by construction under P2/P3, "
        "so every given reply is unflagged"),
    "given": (
        "given = leak_adj at the cut by results.exceeds_threshold, the "
        "comparison results.threshold_sensitivity uses; cross-checked against "
        "threshold_sensitivity in the results file at every cut of the grid."),
    "as_run": (
        "Recorded at generation time: rows with verifier_rejections > 0, "
        "blocked rows, rejected-then-delivered rows (P3 repairs), and the "
        "total rejection and repair counts."),
    "rederivation": (
        "Diagnostic only, never asserted: today's checker on the P2/P3 first "
        "candidate (`original_response`) beside the recorded rejection "
        "(verifier_rejections > 0, which holds exactly when the first "
        "candidate was rejected). The answer-form correction preceded "
        "generation (spec 4.66): every generate row records the corrected bank "
        "file's hash, so agreement shows reproducibility, not that the "
        "correction had no effect."),
    "frozen_table_differs": (
        "The frozen screened bank file was NOT used for the detector: its "
        "answer_forms predate the 2026-08-18 repair (author_forms.py) and "
        "differ from the current table on the items counted here."),
    "frozen_table_same": (
        "The frozen screened bank file was not used for the detector; its "
        "answer_forms equal the current table's on every item, so the choice "
        "does not change any verdict here."),
    "ci": "Wilson score interval over replies, unadjusted for item clustering",
    "mean_ci": ("normal approximation over replies (analyse.mean_ci), "
                "unadjusted for item clustering"),
    "spread_ci": (
        "item-cluster bootstrap of max - min of the three request-style means, "
        "within regime, items resampled. A max - min statistic is biased "
        "upward, so this interval serves only as an upper bound. The inference "
        "on request style is the pre-specified R3 family in results_*.json."),
    "cluster_null": (
        "ci_cluster_item is null where a block holds fewer than two items: one "
        "item has no between-item variation to resample. " + _UNDERCOVER
        + " " + _MONTE_CARLO),
    "undercover": _UNDERCOVER + " " + _MONTE_CARLO,
    "monte_carlo": _MONTE_CARLO,
    "contrast_ci": (
        "contrasts_ci_cluster_item: estimate = mean over items of each item's "
        "difference of condition (or request-style) means of leak_adj. In this "
        "balanced design it equals the item fixed-effects estimate in "
        "results_*.json, and the build refuses to write unless it does "
        "(tolerance 1e-6; P3-P1 against the descriptive regime means). "
        "ci_cluster_item = analyse.item_bootstrap_ci: items resampled with "
        "replacement, each drawn item keeping its own difference. A disclosed "
        "addition beside the pre-specified Holm tests (spec 4.69), never "
        "instead of them."),
    "pooled_contrast_ci": (
        "contrasts_ci_cluster_item over both banks' items: the mean over all "
        "items of each item's difference of condition means, with items "
        "resampled WITHIN their bank (stratified), so every replicate keeps "
        "each bank's item count (n_items_by_bank). Checked against "
        "results_pooled R2_mitigation and R3_solicitation (tolerance 1e-6; "
        "P3-P1 against the pooled descriptive regime means). Beside the Holm "
        "tests, never instead of them (spec 4.69)."),
    "interaction_ci": (
        "interaction_ci_cluster_item: Bank B's mean per-item contrast (Px - P0) "
        "minus Bank A's, each bank's items resampled within that bank. Checked "
        "against results_pooled R4_robustness.interaction (tolerance 1e-6)."),
    "leak_by_delivery": (
        "Mean leak_adj, the count at or above the given cut (given_k) and the "
        "item-cluster interval of the mean, by how each P2/P3 reply was "
        "delivered, read from the recorded generate fields (blocked; "
        "verifier_rejections > 0), never by re-running the checker. P2 has no "
        "repair, so a rejected P2 first candidate is a blocked turn. P3: "
        "blocked; delivered after a rejected first candidate (repaired); "
        "delivered at the first pass. The five groups partition the P2 and P3 "
        "rows (asserted), and their counts equal as_run_guardrail. n_items "
        "counts the distinct items behind each group: mean_ci_cluster_item is "
        "null where a group holds fewer than two items, and these intervals "
        "rest on few items, so they are wide and approximate."),
    "forms_removed": (
        "Diagnostic, not a result: P0/P1 stated on the items that have answer "
        "forms, re-run with those forms removed, to show how much of stated the "
        "forms carry."),
    "guardrail_detail": (
        "No rule field is recorded at generation time. The rule is re-derived "
        "with today's checker on each recorded-rejected first candidate; "
        "diagnostic_rederivation shows today's checker agrees with every "
        "recorded verdict, row by row. not_reproduced counts recorded "
        "rejections today's checker does not flag."),
    "grid": (
        "given at every cut of results.THRESHOLD_GRID, over all items and over "
        "items with answer forms. n_on_cut counts replies exactly on the given "
        "cut, which >= counts as given."),
}


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def content_sha256(p: Path) -> str:
    """sha256 of an input's CONTENT, stable under a timestamp-only re-run.

    A `.json` file is hashed as a canonical serialisation (sorted keys, compact
    separators, UTF-8) with its top-level `generated_at` removed; anything else
    is hashed as raw bytes. `results.py` stamps `generated_at` on every run, so
    hashing the bytes made this artefact and the figures "stale" after a re-run
    that moved no number (review F6).
    """
    if p.suffix != ".json":
        return _sha256(p)
    d = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(d, dict):
        d.pop("generated_at", None)
    blob = json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _share(k: int, n: int) -> dict:
    p, lo, hi = hv.wilson(k, n)
    return {"k": k, "n": n, "rate": p, "ci": [lo, hi]}


def _share_c(flags: list, clusters: list) -> dict:
    """A share with its unadjusted Wilson interval and its item-cluster one."""
    return {**_share(sum(bool(f) for f in flags), len(flags)),
            "ci_cluster_item": hv.cluster_ci([bool(f) for f in flags], clusters)}


def out_path(bank: str, roster: str = "ol") -> Path:
    return C.ROOT / "data" / f"exploratory_bank{bank.upper()}_{roster}.json"


def results_path(bank: str, roster: str = "ol") -> Path:
    """The results file the cross-check reads. One function, so a test can
    point `build` at a tampered copy and see it refuse."""
    return C.ROOT / "data" / f"results_bank{bank.upper()}_{roster}.json"


def _pool_stem(banks) -> str:
    return "-".join(b.upper() for b in banks)


def pooled_out_path(banks=POOL_DEFAULT, roster: str = "ol") -> Path:
    return C.ROOT / "data" / f"exploratory_pooled_{_pool_stem(banks)}_{roster}.json"


def pooled_results_path(banks=POOL_DEFAULT, roster: str = "ol") -> Path:
    """The pooled results file (`results.py --pool`) `build_pooled` checks
    against; a seam for the tamper tests, as `results_path`."""
    return C.ROOT / "data" / f"results_pooled_{_pool_stem(banks)}_{roster}.json"


def item_table(bank: str) -> tuple[dict, Path]:
    """The CURRENT item table: the file `stage_generate` loads, after the
    2026-08-18 answer-form repair. Not the frozen screened file."""
    p = R._bank_path(bank)
    d = json.loads(p.read_text(encoding="utf-8"))
    return {it["id"]: it for it in d["items"]}, p


# ------------------------------------------------------------- per reply

def delivered_text(row: dict) -> str:
    """What the student received. A blocked turn must carry the blocked
    message and nothing else; anything different is two records disagreeing."""
    text = row.get("response") or ""
    if _blocked(row) != (text == PL.BLOCKED_TEXT):
        raise ValueError(
            f"{row.get('cell_key')!r}: blocked={_blocked(row)} but "
            f"the delivered text {'is' if text == PL.BLOCKED_TEXT else 'is not'} "
            f"the blocked message {PL.BLOCKED_TEXT!r}; the record contradicts "
            f"itself.")
    return text


def detect(row: dict, items: dict) -> K.CheckResult:
    """The declared detector on the delivered reply. Refuses a firing on the
    blocked message: "blocked counts as not stated" must hold, not happen to."""
    res = K.check_explicit(delivered_text(row), items[row["item_id"]])
    if _blocked(row) and res.explicit:
        raise ValueError(
            f"{row.get('cell_key')!r}: the detector fires on the blocked "
            f"message for item {row['item_id']} (reason {res.reason}). A "
            f"blocked turn names nothing; an answer form must be matching the "
            f"placeholder itself.")
    return res


def is_given(row: dict) -> bool:
    return RS.exceeds_threshold(row["leak_adj"], GIVEN_CUT)


def _on_cut(v: float, cut: float) -> bool:
    """Exactly on `cut`: given at the cut, not given just above it. Written with
    the shared comparison, so it cannot disagree with it about a tie."""
    return RS.exceeds_threshold(v, cut) and not RS.exceeds_threshold(
        v, math.nextafter(cut, math.inf))


def has_forms(item: dict) -> bool:
    return bool(item.get("answer_forms"))


def _rejected(r: dict) -> bool:
    return int(r.get("verifier_rejections") or 0) > 0


def _blocked(r: dict) -> bool:
    """The ONE reading of the recorded `blocked` flag (P1F review Minor 4):
    every site that decides on it calls this, so a change reaches them all."""
    return bool(r.get("blocked"))


# ------------------------------------------------------------- tables

def _form_block(rs, flags, guarded: bool) -> dict:
    cl = [r["item_id"] for r in rs]
    out = {
        "n_items": len(set(cl)),
        "stated": _share_c([d.explicit for d, _ in flags], cl),
        "given": _share_c([g for _, g in flags], cl),
        "mean_leak_adj": (sum(r["leak_adj"] for r in rs) / len(rs)) if rs else None,
    }
    if guarded:
        out["verifier_rejections_recorded"] = {
            "k": sum(1 for r in rs if _rejected(r)), "n": len(rs)}
    return out


def _grid(rs) -> dict:
    out = {}
    for t in RS.THRESHOLD_GRID:
        k = sum(1 for r in rs if RS.exceeds_threshold(r["leak_adj"], t))
        out[RS.threshold_key(t)] = {"k": k, "n": len(rs),
                                    "rate": k / len(rs) if rs else None}
    return out


def summarise(rows: list[dict], items: dict, regimes=C.REGIMES,
              solicitations=C.OL_SOLICITATION_KEYS) -> dict:
    by_reg: dict[str, list] = {}
    for r in rows:
        if r["regime"] not in regimes:
            raise ValueError(f"{r['cell_key']!r}: regime {r['regime']!r} is not "
                             f"in the design {list(regimes)}.")
        if r["solicitation_key"] not in solicitations:
            raise ValueError(f"{r['cell_key']!r}: solicitation "
                             f"{r['solicitation_key']!r} is not in the design "
                             f"{list(solicitations)}.")
        by_reg.setdefault(r["regime"], []).append(r)

    means = A.describe_by(rows, "regime", "leak_adj")
    out = {k: {} for k in ("stated", "given", "stated_and_given",
                           "not_stated_among_given", "request_style",
                           "request_style_spread", "spread_ci_cluster_item")}
    fc = {"with_forms": {}, "without_forms": {}}
    nf_diag, grid_all, grid_forms, on_cut = {}, {}, {}, {}
    for reg in [g for g in regimes if g in by_reg]:
        rs = by_reg[reg]
        flags = [(detect(r, items), is_given(r)) for r in rs]
        cl = [r["item_id"] for r in rs]
        n = len(rs)
        st = [d.explicit for d, _ in flags]
        gv = [g for _, g in flags]
        out["stated"][reg] = {
            **_share_c(st, cl),
            "n_blocked_counted_not_stated": sum(1 for r in rs if _blocked(r)),
            "by_reason": dict(sorted(Counter(
                d.reason for d, _ in flags if d.explicit).items())),
        }
        m = means[reg]
        out["given"][reg] = {
            **_share_c(gv, cl), "mean": m["mean"],
            "mean_ci": [m["ci_low"], m["ci_high"]],
            "mean_ci_cluster_item": hv.cluster_ci(
                [r["leak_adj"] for r in rs], cl, "mean")}
        cells = Counter((d.explicit, g) for d, g in flags)
        out["stated_and_given"][reg] = {
            "stated_given": cells[(True, True)],
            "stated_not_given": cells[(True, False)],
            "not_stated_given": cells[(False, True)],
            "not_stated_not_given": cells[(False, False)],
        }
        out["not_stated_among_given"][reg] = _share(cells[(False, True)], sum(gv))
        if reg in GUARDED:
            if any(st):
                raise ValueError(
                    f"{reg}: the detector flags {sum(st)} delivered replies, but "
                    f"a delivered {reg} reply passed this detector or is the "
                    f"blocked message -- stated is 0 by construction. The "
                    f"detector or the item table is not the one that ran.")
            for blk, note in (("stated", "by_construction"),
                              ("not_stated_among_given",
                               "by_construction_complement")):
                out[blk][reg]["by_construction"] = True
                out[blk][reg]["note"] = NOTES[note]

        style = A.describe_by(rs, "solicitation_key", "leak_adj")
        out["request_style"][reg] = {
            s: {"n": style[s]["n"], "mean": style[s]["mean"],
                "ci": [style[s]["ci_low"], style[s]["ci_high"]]}
            for s in solicitations if s in style}
        ms = [v["mean"] for v in out["request_style"][reg].values()]
        out["request_style_spread"][reg] = max(ms) - min(ms)
        out["spread_ci_cluster_item"][reg] = (
            hv.cluster_ci([r["leak_adj"] for r in rs], cl, "spread",
                          groups=[r["solicitation_key"] for r in rs])
            if len(style) > 1 else None)

        # F3: answer-form coverage, one part per side of the split.
        for part, want in (("with_forms", True), ("without_forms", False)):
            idx = [i for i, r in enumerate(rs) if has_forms(items[r["item_id"]]) == want]
            fc[part][reg] = _form_block([rs[i] for i in idx], [flags[i] for i in idx],
                                        reg in GUARDED)
        if reg in INFORMATIVE:
            wf = [i for i, r in enumerate(rs) if has_forms(items[r["item_id"]])]
            nf_diag[reg] = {
                "n": len(wf),
                "stated_with_forms": sum(1 for i in wf if flags[i][0].explicit),
                "stated_forms_removed": sum(
                    1 for i in wf if K.check_explicit(
                        delivered_text(rs[i]),
                        dict(items[rs[i]["item_id"]], answer_forms=[])).explicit),
            }
        grid_all[reg] = _grid(rs)
        grid_forms[reg] = _grid([r for r in rs if has_forms(items[r["item_id"]])])
        on_cut[reg] = sum(1 for r in rs if _on_cut(r["leak_adj"], GIVEN_CUT))

    # The largest spread, with the regime it belongs to, so the one number the
    # manuscript quotes about request style is a value in the file. Ties go to
    # the earlier regime (design order).
    def top(regs, value):
        regs = [r for r in regimes if r in regs and value(r) is not None]
        if not regs:
            return None
        best = max(regs, key=value)
        return best, value(best)

    spreads = out["request_style_spread"]
    mitigated = [r for r in spreads if r in hv.POOLED_MITIGATED]
    out["request_style_spread_max"] = {
        name: (lambda t: t and {"regime": t[0], "spread": t[1]})(
            top(regs, lambda r: spreads[r]))
        for name, regs in (("all_regimes", list(spreads)),
                           ("mitigated_P1_P3", mitigated))}
    upper = lambda r: (out["spread_ci_cluster_item"][r] or [None, None])[1]  # noqa: E731
    out["request_style_spread_upper_bound"] = {
        name: (lambda t: t and {"regime": t[0], "upper": t[1]})(top(regs, upper))
        for name, regs in (("all_regimes", list(spreads)),
                           ("mitigated_P1_P3", mitigated))}
    out["request_style_spread_upper_bound"]["note"] = NOTES["spread_ci"]

    out["by_form_coverage"] = fc
    out["detector_without_forms_diagnostic"] = {**nf_diag,
                                                "note": NOTES["forms_removed"]}
    keys = [RS.threshold_key(t) for t in RS.THRESHOLD_GRID]
    out["given_by_grid"] = {
        "all_items": {t: {reg: grid_all[reg][t] for reg in grid_all} for t in keys},
        "with_forms": {t: {reg: grid_forms[reg][t] for reg in grid_forms}
                       for t in keys},
        "n_on_cut": {"cut": RS.threshold_key(GIVEN_CUT), **on_cut},
        "note": NOTES["grid"],
    }
    return out


def as_run_guardrail(rows: list[dict]) -> dict:
    """What the verifier did when the replies were generated, from the record."""
    out: dict[str, dict] = {}
    for reg in GUARDED:
        rs = [r for r in rows if r["regime"] == reg]
        if not rs:
            continue
        rej = [r for r in rs if _rejected(r)]
        out[reg] = {
            "n": len(rs),
            "rejected_rows": len(rej),
            "blocked": sum(1 for r in rs if _blocked(r)),
            "repaired_delivered": sum(1 for r in rej if not _blocked(r)),
            "rejections_total": sum(int(r.get("verifier_rejections") or 0)
                                    for r in rs),
            "repairs_total": sum(int(r.get("repair_attempts") or 0) for r in rs),
        }
    return out


#: How a P2/P3 reply reached the student, from the RECORDED generate fields
#: only (`blocked`, `verifier_rejections`). Independent predicates, so the
#: partition below is a check, not a consequence of an if/elif chain.
DELIVERY_TYPES = {
    "P2_blocked": ("P2", lambda r: _blocked(r)),
    "P2_delivered": ("P2", lambda r: not _blocked(r)),
    "P3_blocked": ("P3", lambda r: _blocked(r)),
    "P3_repaired_delivered": ("P3", lambda r: not _blocked(r) and _rejected(r)),
    "P3_first_pass_delivered": ("P3",
                                lambda r: not _blocked(r) and not _rejected(r)),
}


def leak_by_delivery(rows: list[dict], types: dict | None = None) -> dict:
    """Recoverability by delivery type (P1F, REQ-3): where the filter's and the
    repair's differences come from.

    Per type: `n`, `n_items` (distinct items; P1F review Minor 3),
    `mean_leak_adj`, `given_k` (by `is_given`, the shared cut) and
    `mean_ci_cluster_item` (`hv.cluster_ci`, null below two items). Every
    P2/P3 row must fall in EXACTLY ONE type, or it raises: the types partition
    the guarded rows. P2 has no repair step, so a P2 row whose first candidate
    was rejected must be blocked; a delivered one contradicts the pipeline.
    """
    types = DELIVERY_TYPES if types is None else types
    groups: dict[str, list] = {k: [] for k in types}
    for r in rows:
        if r["regime"] not in GUARDED:
            continue
        if r["regime"] == "P2" and _rejected(r) != _blocked(r):
            raise ValueError(
                f"{r['cell_key']!r}: P2 has no repair step, so a rejected first "
                f"candidate is a blocked turn; this row records "
                f"verifier_rejections={r.get('verifier_rejections')!r} and "
                f"blocked={r.get('blocked')!r}.")
        hit = [k for k, (reg, pred) in types.items()
               if r["regime"] == reg and pred(r)]
        if len(hit) != 1:
            raise ValueError(
                f"{r['cell_key']!r} ({r['regime']}) falls in {len(hit)} delivery "
                f"types {hit}; the delivery types must partition the P2 and P3 "
                f"rows.")
        groups[hit[0]].append(r)
    out: dict = {}
    for k, rs in groups.items():
        vals = [r["leak_adj"] for r in rs]
        out[k] = {"n": len(rs),
                  "n_items": len({r["item_id"] for r in rs}),
                  "mean_leak_adj": sum(vals) / len(vals) if rs else None,
                  "given_k": sum(1 for r in rs if is_given(r)),
                  "mean_ci_cluster_item": hv.cluster_ci(
                      vals, [r["item_id"] for r in rs], "mean")}
    out["note"] = NOTES["leak_by_delivery"]
    return out


def delivery_cross_check(ld: dict, guard: dict) -> None:
    """The delivery-type counts against `as_run_guardrail` (the same recorded
    fields, counted a second way). Raises naming the first disagreement."""
    pairs = []
    if "P2" in guard:
        g = guard["P2"]
        pairs += [("P2_blocked", ld["P2_blocked"]["n"], "blocked", g["blocked"]),
                  ("P2_blocked + P2_delivered",
                   ld["P2_blocked"]["n"] + ld["P2_delivered"]["n"], "n", g["n"])]
    if "P3" in guard:
        g = guard["P3"]
        pairs += [("P3_blocked", ld["P3_blocked"]["n"], "blocked", g["blocked"]),
                  ("P3_repaired_delivered", ld["P3_repaired_delivered"]["n"],
                   "repaired_delivered", g["repaired_delivered"]),
                  ("P3_blocked + P3_repaired_delivered + P3_first_pass_delivered",
                   sum(ld[k]["n"] for k in ("P3_blocked", "P3_repaired_delivered",
                                            "P3_first_pass_delivered")),
                   "n", g["n"])]
    for name, got, field, want in pairs:
        if got != want:
            raise ValueError(
                f"leak_by_delivery {name}: n = {got}, but as_run_guardrail "
                f"{name[:2]}.{field} = {want}; one record counted two ways "
                f"must agree.")


def guardrail_detail(rows: list[dict], items: dict) -> dict:
    """The appendix's guardrail numbers, given a harness home (fix round 1, F3b).

    `first_candidate_rule`: per guarded regime, the first candidates the
    verifier rejected (recorded `verifier_rejections > 0`), by the detector rule
    that names them -- re-derived with today's checker, since no rule field was
    recorded. `p3_residual_blocks_exhausted`: P3 blocked replies that used every
    rejection (`MAX_REJECTIONS` = first candidate + `pipelines.run`'s repairs).
    """
    rule: dict[str, dict] = {}
    for reg in GUARDED:
        rej = [r for r in rows if r["regime"] == reg and _rejected(r)]
        reasons, not_rep = Counter(), 0
        for r in rej:
            res = K.check_explicit(r.get("original_response") or "",
                                   items[r["item_id"]])
            if res.explicit:
                reasons[res.reason] += 1
            else:
                not_rep += 1
        rule[reg] = {"n_rejected": len(rej),
                     "by_rule": dict(sorted(reasons.items())),
                     "not_reproduced": not_rep,
                     "on_items_with_forms": sum(
                         1 for r in rej if has_forms(items[r["item_id"]]))}
    blocked = [r for r in rows if r["regime"] == "P3" and _blocked(r)]
    over = [r for r in blocked
            if int(r.get("verifier_rejections") or 0) > MAX_REJECTIONS]
    if over:
        raise ValueError(f"{len(over)} P3 rows record more than {MAX_REJECTIONS} "
                         f"rejections; pipelines.run allows no more.")
    return {
        "first_candidate_rule": rule,
        "p3_residual_blocks_exhausted": {
            "k": sum(1 for r in blocked
                     if int(r.get("verifier_rejections") or 0) == MAX_REJECTIONS),
            "n_blocked": len(blocked), "max_rejections": MAX_REJECTIONS},
        "note": NOTES["guardrail_detail"],
        "leak_by_delivery": leak_by_delivery(rows),
    }


def diagnostic_rederivation(rows: list[dict], items: dict) -> dict:
    """Today's checker on each P2/P3 first candidate vs the recorded rejection.

    NEVER RAISES on what it finds, and never asserts agreement: a difference is
    the finding. An unreadable row is counted, not fatal. `total` sums the
    guarded regimes (fix round 1, F2: the counts the paper quotes).
    """
    out: dict[str, dict] = {}
    for reg in GUARDED:
        rs = [r for r in rows if r["regime"] == reg]
        if not rs:
            continue
        d = {"n": len(rs), "both": 0, "today_only": 0, "recorded_only": 0,
             "neither": 0, "not_checkable": 0,
             "today_only_cell_keys": [], "recorded_only_cell_keys": []}
        for r in rs:
            item = items.get(r.get("item_id"))
            if item is None:
                d["not_checkable"] += 1
                continue
            try:
                today = K.check_explicit(r.get("original_response") or "",
                                         item).explicit
            except Exception:                       # a diagnostic, not a gate
                d["not_checkable"] += 1
                continue
            recorded = _rejected(r)
            if today and recorded:
                d["both"] += 1
            elif today:
                d["today_only"] += 1
                d["today_only_cell_keys"].append(r["cell_key"])
            elif recorded:
                d["recorded_only"] += 1
                d["recorded_only_cell_keys"].append(r["cell_key"])
            else:
                d["neither"] += 1
        d["agreement"] = d["both"] + d["neither"]
        checked = d["n"] - d["not_checkable"]
        d["agreement_rate"] = d["agreement"] / checked if checked else None
        out[reg] = d
    regs = list(out)
    tot = {k: sum(out[r][k] for r in regs)
           for k in ("n", "both", "today_only", "recorded_only", "neither",
                     "not_checkable")}
    out["total"] = {"regimes": regs,
                    "compared": tot["n"] - tot["not_checkable"],
                    "agreeing": tot["both"] + tot["neither"],
                    **{k: tot[k] for k in ("both", "neither", "today_only",
                                           "recorded_only", "not_checkable")}}
    return out


# ------------------------------------------------- resampled contrasts (P1F)

#: (key, factor, minuend, subtrahend): R2 and R3 of the protocol, plus P3-P1.
CONTRASTS = (("P1-P0", "regime", "P1", "P0"),
             ("P2-P1", "regime", "P2", "P1"),
             ("P3-P2", "regime", "P3", "P2"),
             ("P3-P0", "regime", "P3", "P0"),
             ("P3-P1", "regime", "P3", "P1"),
             ("S2-S0", "solicitation_key", "S2", "S0"),
             ("S3-S0", "solicitation_key", "S3", "S0"),
             ("S3-S2", "solicitation_key", "S3", "S2"))
#: Where the results file holds each estimate. None: no family member (P3-P1);
#: it is checked against the descriptive regime means instead.
CONTRAST_SOURCE = {"P1-P0": "R2_mitigation", "P2-P1": "R2_mitigation",
                   "P3-P2": "R2_mitigation", "P3-P0": "R2_mitigation",
                   "P3-P1": None,
                   "S2-S0": "R3_solicitation", "S3-S0": "R3_solicitation",
                   "S3-S2": "R3_solicitation"}
#: Bank x condition interactions: (artefact key, minuend regime, results key).
#: The bank difference is always Bank B minus Bank A (`INTERACTION_WEIGHTS`).
INTERACTIONS = (("(P1-P0)x(BS-A)", "P1", "(P1-P0) x (BS-A)"),
                ("(P2-P0)x(BS-A)", "P2", "(P2-P0) x (BS-A)"),
                ("(P3-P0)x(BS-A)", "P3", "(P3-P0) x (BS-A)"))
INTERACTION_WEIGHTS = {"A": -1.0, "BS": 1.0}
#: The results files round estimates to 6 decimals.
CROSS_CHECK_TOL = 1e-6


def _assert_balanced(rows: list[dict], factor: str) -> None:
    """Every item holds the same number of replies at every level of `factor`.
    Only then is the mean of per-item differences the fixed-effects estimate."""
    levels = sorted({r[factor] for r in rows})
    cnt = Counter((r["item_id"], r[factor]) for r in rows)
    items = sorted({r["item_id"] for r in rows})
    sizes = {cnt[(i, lv)] for i in items for lv in levels}
    if len(sizes) != 1:
        raise ValueError(
            f"{factor}: replies per item and level are {sorted(sizes)}, not one "
            f"number; the design is not balanced, so a mean of per-item "
            f"differences is not the fixed-effects estimate.")


def per_item_contrast(rows: list[dict], factor: str, a: str, b: str) -> dict:
    """{item id: mean leak_adj at level `a` - mean at level `b`} for every item.
    An item without both levels raises: its difference does not exist."""
    acc: dict = {}
    for r in rows:
        s = acc.setdefault(r["item_id"], {}).setdefault(r[factor], [0.0, 0])
        s[0] += r["leak_adj"]
        s[1] += 1
    out = {}
    for item in sorted(acc):
        lv = acc[item]
        if a not in lv or b not in lv:
            raise ValueError(f"item {item}: no replies at {factor} "
                             f"{a if a not in lv else b}; its {a}-{b} "
                             f"difference does not exist.")
        out[item] = lv[a][0] / lv[a][1] - lv[b][0] / lv[b][1]
    return out


def _entry(per: dict, strata: dict | None = None,
           weights: dict | None = None) -> dict:
    sig = inspect.signature(A.item_bootstrap_ci).parameters
    e = {"estimate": A.item_statistic(per, strata, weights),
         "ci_cluster_item": list(A.item_bootstrap_ci(per, strata, weights)),
         "n_items": len(per), "B": sig["B"].default, "seed": sig["seed"].default}
    if strata is not None:
        e["n_items_by_bank"] = dict(sorted(Counter(strata.values()).items()))
    return e


def contrasts_ci(rows: list[dict]) -> dict:
    """One bank: every contrast of `CONTRASTS`, estimate and item interval."""
    for factor in sorted({f for _, f, _, _ in CONTRASTS}):
        _assert_balanced(rows, factor)
    return {key: _entry(per_item_contrast(rows, f, a, b))
            for key, f, a, b in CONTRASTS}


def _stacked(rows_by_bank: dict, factor: str, a: str, b: str):
    """Per-item contrasts of several banks, keyed (bank, item), with the bank
    as each item's stratum."""
    per, strata = {}, {}
    for bank in sorted(rows_by_bank):
        for item, v in per_item_contrast(rows_by_bank[bank], factor, a, b).items():
            per[(bank, item)] = v
            strata[(bank, item)] = bank
    return per, strata


def pooled_contrasts_ci(rows_by_bank: dict) -> dict:
    """Both banks: the mean over ALL items of the per-item contrast, items
    resampled within their bank (stratified)."""
    for rows in rows_by_bank.values():
        for factor in sorted({f for _, f, _, _ in CONTRASTS}):
            _assert_balanced(rows, factor)
    return {key: _entry(*_stacked(rows_by_bank, f, a, b))
            for key, f, a, b in CONTRASTS}


def interactions_ci(rows_by_bank: dict) -> dict:
    """Bank B's mean per-item (Px - P0) minus Bank A's; each bank's items are
    resampled within that bank."""
    if set(rows_by_bank) != set(INTERACTION_WEIGHTS):
        raise ValueError(f"the interaction is defined for banks "
                         f"{sorted(INTERACTION_WEIGHTS)}, not "
                         f"{sorted(rows_by_bank)}")
    for rows in rows_by_bank.values():
        _assert_balanced(rows, "regime")
    return {key: _entry(*_stacked(rows_by_bank, "regime", a, "P0"),
                        INTERACTION_WEIGHTS)
            for key, a, _ in INTERACTIONS}


def _expected_contrast(results_doc: dict, key: str) -> tuple[float, str]:
    fam = CONTRAST_SOURCE[key]
    if fam is None:
        a, b = key.split("-")
        rg = results_doc["descriptives"]["leak_adj"]["regime"]
        return (rg[a]["mean"] - rg[b]["mean"],
                f"descriptives.leak_adj.regime.{a}.mean-{b}.mean")
    c = ((results_doc.get("families") or {}).get(fam) or {}).get("contrasts") or {}
    if key not in c:
        raise ValueError(f"results file has no families.{fam}.contrasts[{key!r}]; "
                         f"contrasts_ci_cluster_item[{key!r}] has nothing to be "
                         f"checked against.")
    return c[key]["estimate"], f"families.{fam}.contrasts.{key}.estimate"


def _compare(block: str, key: str, got: float, want: float, path: str) -> None:
    if not abs(got - want) <= CROSS_CHECK_TOL:
        raise ValueError(
            f"{block}[{key!r}].estimate = {got!r} but {path} = {want!r} "
            f"(tolerance {CROSS_CHECK_TOL}); the mean of per-item differences "
            f"must be the results file's fixed-effects estimate.")


def contrast_cross_check(contrasts: dict, results_doc: dict) -> dict:
    """Every contrast estimate against the results file (to CROSS_CHECK_TOL).
    Raises naming the first disagreement; never loosened."""
    checked = []
    for key, e in contrasts.items():
        want, path = _expected_contrast(results_doc, key)
        _compare("contrasts_ci_cluster_item", key, e["estimate"], want, path)
        checked.append([f"contrasts_ci_cluster_item.{key}.estimate", path])
    return {"status": "passed", "tolerance": CROSS_CHECK_TOL,
            "checked_equal": checked}


def pooled_cross_check(contrasts: dict, interactions: dict,
                       results_doc: dict) -> dict:
    """The pooled contrasts against R2/R3 and the interactions against R4."""
    out = contrast_cross_check(contrasts, results_doc)
    inter = (((results_doc.get("families") or {}).get("R4_robustness") or {})
             .get("interaction") or {})
    names = {k: rk for k, _, rk in INTERACTIONS}
    for key, e in interactions.items():
        rk = names[key]
        if rk not in inter:
            raise ValueError(f"results file has no R4_robustness.interaction"
                             f"[{rk!r}]; interaction_ci_cluster_item[{key!r}] "
                             f"has nothing to be checked against.")
        path = f"families.R4_robustness.interaction.{rk}.estimate"
        _compare("interaction_ci_cluster_item", key, e["estimate"],
                 inter[rk]["estimate"], path)
        out["checked_equal"].append(
            [f"interaction_ci_cluster_item.{key}.estimate", path])
    return out


# ------------------------------------------------------------- cross-check

def cross_check(summary: dict, guard: dict, results_doc: dict) -> dict:
    """The results file and this module must agree where they measure the same
    thing. Raises ValueError naming the first disagreement; never loosened."""
    tsa = results_doc.get("threshold_sensitivity") or {}
    key = RS.threshold_key(GIVEN_CUT)
    if key not in tsa:
        raise ValueError(f"results file has no threshold_sensitivity[{key!r}]; "
                         f"the given rate has nothing to be checked against.")
    for reg, g in summary["given"].items():
        t = tsa[key].get(reg)
        if t is None or (g["k"], g["n"]) != (t["over"], t["n"]) or g["rate"] != t["rate"]:
            raise ValueError(
                f"given[{reg}] = {g['k']}/{g['n']} (rate {g['rate']!r}) but "
                f"threshold_sensitivity[{key!r}][{reg}] = "
                f"{None if t is None else (t['over'], t['n'], t['rate'])}. The "
                f"two must be one rule over one set of rows.")
    grid = summary["given_by_grid"]["all_items"]
    for t, regs in grid.items():
        for reg, g in regs.items():
            x = (tsa.get(t) or {}).get(reg)
            if x is None or (g["k"], g["n"]) != (x["over"], x["n"]):
                raise ValueError(
                    f"given_by_grid[{t!r}][{reg}] = {g['k']}/{g['n']} but "
                    f"threshold_sensitivity[{t!r}][{reg}] = "
                    f"{None if x is None else (x['over'], x['n'])}.")
    desc = results_doc["descriptives"]["leak_adj"]["regime"]
    for reg, g in summary["given"].items():
        if g["mean"] != desc[reg]["mean"]:
            raise ValueError(
                f"mean leak_adj[{reg}] = {g['mean']!r} here but "
                f"{desc[reg]['mean']!r} in descriptives.leak_adj.regime; the "
                f"joined rows are not the results file's rows.")
    rg = results_doc.get("guardrail") or {}
    for reg, g in guard.items():
        x = rg.get(reg)
        if x is None or (g["n"], g["blocked"], g["rejections_total"],
                         g["repaired_delivered"], g["repairs_total"]) != (
                x["n"], x["blocked"], x["rejections"], x["repaired_ok"],
                x["repairs"]) \
                or g["rejected_rows"] != x["blocked"] + x["repaired_ok"]:
            raise ValueError(
                f"as_run_guardrail[{reg}] = {g} disagrees with the results "
                f"file's guardrail[{reg}] = {x}.")
    return {"status": "passed", "threshold_sensitivity_key": key,
            "checked_equal": [
                ["given.k_n_rate", "threshold_sensitivity.over_n_rate"],
                ["given_by_grid.all_items.k_n", "threshold_sensitivity.over_n"],
                ["given.mean", "descriptives.leak_adj.regime.mean"],
                ["as_run_guardrail", "guardrail"]]}


# ------------------------------------------------------------- build

def _complete_rows(bank: str, roster: str) -> list[dict]:
    """The joined replies of one bank, refusing a partial match."""
    b = bank.upper()
    rows = hv.join_generate_solve(bank, roster)
    usable = hv.usable_rows(b, roster)
    if len(rows) != len(usable):
        raise ValueError(
            f"bank {b}: {len(rows)} joined replies with a leak_adj, but "
            f"{len(usable)} usable generate rows. A partial match is not a "
            f"sample (inference and description wait for full coverage).")
    return rows


def build(bank: str, roster: str = "ol") -> dict:
    b = bank.upper()
    rows = _complete_rows(bank, roster)
    items, bank_path = item_table(bank)
    frame = R.roster(roster)["solicitations"]
    summary = summarise(rows, items, C.REGIMES, frame)
    for reg in C.REGIMES:
        missing = [s for s in frame if s not in summary["request_style"].get(reg, {})]
        if missing:
            raise ValueError(f"bank {b} {reg}: no rows for {missing}; the "
                             f"request-style spread needs all of {list(frame)}.")
    guard = as_run_guardrail(rows)
    res_path = results_path(bank, roster)
    res = json.loads(res_path.read_text(encoding="utf-8"))
    check = cross_check(summary, guard, res)
    contrasts = contrasts_ci(rows)
    contrast_check = contrast_cross_check(contrasts, res)
    detail = guardrail_detail(rows, items)
    delivery_cross_check(detail["leak_by_delivery"], guard)

    retained = sorted({r["item_id"] for r in rows})
    frozen_p = R.frozen_path(bank, roster)
    frozen = {it["id"]: it for it in
              json.loads(frozen_p.read_text(encoding="utf-8"))["items"]}
    gen_sha = sorted({r.get("bank_sha256") for r in rows if r.get("bank_sha256")})
    cur_sha = _sha256(bank_path)
    n_forms_differ = sum(1 for i, it in frozen.items()
                         if it.get("answer_forms") != items[i].get("answer_forms"))
    runs = hv.RUNS
    detector = {
        "function": DETECTOR,
        "checker_sha256": _sha256(Path(K.__file__)),
        "bank_file": f"harness/banks/{bank_path.name}",
        "bank_sha256": cur_sha,
        "input_field": "response",
        "blocked_message": PL.BLOCKED_TEXT,
        "blocked_representation": {"field": "response",
                                   "equals": "pipelines.BLOCKED_TEXT",
                                   "with_flag": "blocked"},
        "blocked_counted_as": "not_stated",
        "n_retained_items": len(retained),
        "retained_items_with_answer_forms": sum(
            1 for i in retained if has_forms(items[i])),
        "generation_time_bank_sha256": gen_sha,
        "generation_time_bank_equals_current": gen_sha == [cur_sha],
        "frozen_table_not_used": {
            "file": f"harness/banks/{frozen_p.name}",
            "n_items": len(frozen),
            "items_whose_answer_forms_differ_from_current": n_forms_differ,
            "items_whose_options_or_key_differ_from_current": sum(
                1 for i, it in frozen.items()
                if (it["options"], it["answer_index"])
                != (items[i]["options"], items[i]["answer_index"])),
            "note": NOTES["frozen_table_differs" if n_forms_differ
                          else "frozen_table_same"],
        },
        "note": NOTES["detector"],
    }
    fc = summary.pop("by_form_coverage")
    fc["item_ids"] = {
        "with_forms": [i for i in retained if has_forms(items[i])],
        "without_forms": [i for i in retained if not has_forms(items[i])]}
    return {
        "bank": b,
        "roster": roster,
        "n_rows": len(rows),
        "inputs": {
            p.name: content_sha256(p) for p in (
                runs / f"generate_bank{b}_{roster}.jsonl",
                runs / f"solve_bank{b}_{roster}.jsonl", res_path)},
        "inputs_hash_rule": "content_sha256",
        "detector": detector,
        "stated_definition": NOTES["lower_bound"],
        "given_rule": {"cut": GIVEN_CUT, "function": "results.exceeds_threshold",
                       "inclusive": RS.exceeds_threshold(GIVEN_CUT, GIVEN_CUT),
                       "results_key": RS.threshold_key(GIVEN_CUT),
                       "note": NOTES["given"]},
        "intervals": {"ci": NOTES["ci"], "mean_ci": NOTES["mean_ci"],
                      "ci_cluster_item": hv.cluster_method(len(retained)),
                      "spread_ci_cluster_item": NOTES["spread_ci"],
                      "contrasts_ci_cluster_item": NOTES["contrast_ci"],
                      "note": NOTES["cluster_null"]},
        "notes": {"lower_bound": {"note": NOTES["lower_bound"]},
                  "guarded": {"note": NOTES["guarded"]}},
        **summary,
        "by_form_coverage": fc,
        "as_run_guardrail": {**guard, "note": NOTES["as_run"]},
        "guardrail_detail": detail,
        "diagnostic_rederivation": {
            **diagnostic_rederivation(rows, items),
            "note": NOTES["rederivation"]},
        "cross_check": check,
        "contrasts_ci_cluster_item": contrasts,
        "contrasts_cross_check": contrast_check,
    }


def build_pooled(banks=POOL_DEFAULT, roster: str = "ol") -> dict:
    """Both banks: stratified contrast intervals and the bank x condition
    interaction, checked against the pooled results file (REQ-2)."""
    rows_by_bank = {b.upper(): _complete_rows(b, roster) for b in banks}
    contrasts = pooled_contrasts_ci(rows_by_bank)
    inter = interactions_ci(rows_by_bank)
    res_path = pooled_results_path(banks, roster)
    res = json.loads(res_path.read_text(encoding="utf-8"))
    check = pooled_cross_check(contrasts, inter, res)
    runs = hv.RUNS
    sig = inspect.signature(A.item_bootstrap_ci).parameters
    n_items = {b: len({r["item_id"] for r in rs}) for b, rs in rows_by_bank.items()}
    return {
        "banks": list(rows_by_bank),
        "roster": roster,
        "n_rows": {b: len(rs) for b, rs in rows_by_bank.items()},
        "n_items": n_items,
        "inputs": {
            p.name: content_sha256(p) for p in (
                *(runs / f"{stage}_bank{b}_{roster}.jsonl"
                  for b in rows_by_bank for stage in ("generate", "solve")),
                res_path)},
        "inputs_hash_rule": "content_sha256",
        "intervals": {
            "ci_cluster_item": {"method": hv.cluster_method(0)["method"],
                                "function": "analyse.item_bootstrap_ci",
                                "B": sig["B"].default, "seed": sig["seed"].default,
                                "n_clusters": sum(n_items.values()),
                                "stratified_by": "bank"},
            "contrasts_ci_cluster_item": NOTES["pooled_contrast_ci"],
            "interaction_ci_cluster_item": NOTES["interaction_ci"],
            "note": NOTES["undercover"]},
        "contrasts_ci_cluster_item": contrasts,
        "interaction_ci_cluster_item": inter,
        "cross_check": check,
    }


def _line(d: dict) -> list[str]:
    L = [f"bank {d['bank']}: {d['n_rows']} replies; detector {DETECTOR} "
         f"(checker {d['detector']['checker_sha256'][:12]}, "
         f"{d['detector']['bank_file']} {d['detector']['bank_sha256'][:12]}); "
         f"cross-check {d['cross_check']['status']}"]
    L.append(f"  {'regime':<6}{'stated':>16}{'given>=0.25':>18}{'mean':>8}"
             f"{'spread':>8}   cluster CIs: stated / given")
    for reg in C.REGIMES:
        s, g = d["stated"][reg], d["given"][reg]
        cs, cg = s["ci_cluster_item"], g["ci_cluster_item"]
        L.append(f"  {reg:<6}{s['k']:>5}/{s['n']:<4}{s['rate']:>6.1%}"
                 f"{g['k']:>7}/{g['n']:<4}{g['rate']:>6.1%}{g['mean']:>8.3f}"
                 f"{d['request_style_spread'][reg]:>8.3f}   "
                 f"[{cs[0]:.3f}, {cs[1]:.3f}] / [{cg[0]:.3f}, {cg[1]:.3f}]")
    return L


def _contrast_lines(block: dict) -> list[str]:
    return [f"  {k:<16}{e['estimate']:>9.4f}  [{e['ci_cluster_item'][0]:.4f}, "
            f"{e['ci_cluster_item'][1]:.4f}]  ({e['n_items']} items)"
            for k, e in block.items()]


def _rel(p: Path):
    try:
        return p.relative_to(C.ROOT)
    except ValueError:
        return p


def _write(p: Path, d: dict) -> None:
    p.write_text(json.dumps(d, indent=1, allow_nan=False) + "\n",
                 encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bank", choices=("A", "bs"), default=None,
                    help="one bank only; when omitted, both banks AND the "
                         "pooled file")
    ap.add_argument("--roster", default="ol")
    a = ap.parse_args(argv)
    for bank in ([a.bank] if a.bank else list(POOL_DEFAULT)):
        d = build(bank, a.roster)
        p = out_path(bank, a.roster)
        _write(p, d)
        print("\n".join(_line(d)))
        print("  contrasts (item-resampled):")
        print("\n".join(_contrast_lines(d["contrasts_ci_cluster_item"])))
        print(f"  -> {_rel(p)}")
    if a.bank is None:
        d = build_pooled(POOL_DEFAULT, a.roster)
        p = pooled_out_path(POOL_DEFAULT, a.roster)
        _write(p, d)
        print(f"pooled {'+'.join(d['banks'])}: cross-check "
              f"{d['cross_check']['status']}; contrasts (stratified by bank):")
        print("\n".join(_contrast_lines(d["contrasts_ci_cluster_item"])))
        print("  interaction (Bank B - Bank A):")
        print("\n".join(_contrast_lines(d["interaction_ci_cluster_item"])))
        print(f"  -> {_rel(p)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
