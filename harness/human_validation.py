"""Tier-3 validation: the 240-response human-labelled sample (sec:validation).

WHAT THIS MODULE WILL NOT DO
----------------------------
It will not produce labels. The Method promises "labelled by two authors
independently against a written codebook", and a label invented here would be
fabricated data in a paper -- not a shortcut but a different kind of object.
`score()` reads label files written by people and refuses every degenerate case
rather than filling one in: a missing file, a partial file, a row the sample does
not contain, a label outside the codebook, two annotators who were not given the
same rows.

WHAT IT DOES
------------
Everything up to the labelling, and everything after it:

  draw()   the pre-registered sample -- 5 responses from each of the 48 cells of
           the generator x regime x solicitation design, chosen DETERMINISTICALLY
           by hash order rather than by a seed. There is no seed to have been
           chosen after seeing the data; re-running reproduces the sample exactly.
  sheets() two blind annotation sheets. The annotator sees the reply and the
           option set and performs the SOLVER'S OWN TASK -- pick the option the
           reply points to, or "cannot tell" -- so the human label and the
           automated measure are answers to the same question rather than to two
           questions that happen to correlate. Condition columns are withheld and
           row order is per-annotator, so the sheet does not carry the regime.
           (Section 4.24 of the paper is explicit that blinding is imperfect:
           a mitigated reply often announces its own refusal. Withholding the
           column is not a claim to have solved that.)
  score()  inter-annotator agreement, and the sensitivity/specificity of the
           automated measure against the human labels, with Wilson intervals --
           reported across the SAME threshold grid the manuscript already uses,
           never at a cut invented here for the purpose.

Coverage is gated the way `results.py` gates inference and `cap_sensitivity` gates
its recommendation (4.59): a partial labelling is not a small sample of the
intended one, because the sheet is walked in order and the cells are not
exchangeable. Below full coverage `score()` returns counts and refuses the
statistics.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import config as C

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "data" / "runs"

PER_CELL = 5                      # pre-registered; see sec:validation
SOLICITATION_KEYS = ("S0", "S2", "S3")   # S1 was cut before data (4.43)
LABELS_CANNOT_TELL = "cannot_tell"
SAMPLE_PATH = ROOT / "data" / "human_validation_sample.json"


# ---------------------------------------------------------------- sample -----
def _cells():
    """The 48 cells, derived from the ROSTER, never from C.SOLICITATIONS.

    4.43 was reproduced once by reading the config constant, which still holds
    four solicitations because S1's entry was left in place when the level was
    cut. The realised design has three.
    """
    out = []
    for g in C.OL_GENERATORS:
        for r in C.REGIMES:
            for s in SOLICITATION_KEYS:
                out.append((g, r, s))
    return out


def _order_key(row) -> str:
    """Deterministic, seed-free draw order.

    A seed fixed today is a seed chosen after the responses existed. Hashing the
    row's own identity gives an order nobody selected, and one that any reader
    can recompute.
    """
    ident = f"{row['cell_key']}|{row.get('rep')}|{row['item_id']}"
    return hashlib.sha256(ident.encode("utf-8")).hexdigest()


def usable_rows(bank: str = "A", roster: str = "ol"):
    p = RUNS / f"generate_bank{bank}_{roster}.jsonl"
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        # 4.39: a row with ok=False is an ATTEMPT, not a unit of work.
        if r.get("ok") is not True:
            continue
        if not (r.get("response") or "").strip():
            continue
        out.append(r)
    return out


def draw(bank: str = "A", roster: str = "ol"):
    rows = usable_rows(bank, roster)
    by_cell: dict[tuple, list] = {c: [] for c in _cells()}
    for r in rows:
        key = (r["model"], r["regime"], r["solicitation_key"])
        if key in by_cell:
            by_cell[key].append(r)

    thin = {f"{g}|{rg}|{s}": len(v) for (g, rg, s), v in by_cell.items()
            if len(v) < PER_CELL}
    if thin:
        raise ValueError(
            f"{len(thin)} of {len(by_cell)} cells hold fewer than {PER_CELL} "
            f"usable responses, so the pre-registered allocation cannot be "
            f"drawn: {thin}. Re-sizing the sample here would change a quantity "
            f"the Method fixed before any response was generated.")

    sample = []
    for (g, rg, s), v in by_cell.items():
        for r in sorted(v, key=_order_key)[:PER_CELL]:
            sample.append({
                "response_id": hashlib.sha256(
                    f"{r['cell_key']}|{r.get('rep')}".encode()).hexdigest()[:16],
                "cell_key": r["cell_key"], "rep": r.get("rep"),
                "item_id": r["item_id"], "generator": r["model"],
                "regime": r["regime"], "solicitation_key": r["solicitation_key"],
                "response": r["response"],
            })
    assert len(sample) == PER_CELL * len(by_cell) == 240, len(sample)
    return sample


def freeze(bank: str = "A", roster: str = "ol"):
    """Write the sample once, with its own hash. Refuses to move a frozen file."""
    sample = draw(bank, roster)
    blob = json.dumps({"bank": bank, "roster": roster, "per_cell": PER_CELL,
                       "n": len(sample), "responses": sample},
                      indent=2, sort_keys=True)
    sha = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    if SAMPLE_PATH.exists():
        old = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
        if old.get("sha256") != sha:
            raise ValueError(
                f"{SAMPLE_PATH.name} already exists with a different content "
                f"hash. The sample is the thing two people labelled; silently "
                f"redrawing it would invalidate their work. Move the old file "
                f"aside deliberately if that is really what you mean.")
        return old
    doc = json.loads(blob); doc["sha256"] = sha
    SAMPLE_PATH.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    return doc


def _options_by_item(bank: str = "A", roster: str = "ol"):
    p = ROOT / "harness" / "banks" / f"bank_{bank.lower()}_screened_{roster}.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    return {it["id"]: (it["options"], it["answer_index"]) for it in d["items"]}


def sheets(annotators=("A1", "A2"), bank: str = "A", roster: str = "ol",
           out_dir: Path | None = None):
    """One blind CSV per annotator; per-annotator row order.

    The KEY IS NOT IN THE SHEET. The annotator picks the option the reply points
    to; whether that is the key is computed at scoring time. A sheet carrying the
    key would be asking a different, easier question -- "does this reply agree
    with the answer I can already see" -- and the agreement statistic would
    measure compliance rather than recoverability.
    """
    doc = freeze(bank, roster)
    opts = _options_by_item(bank, roster)
    out_dir = out_dir or (ROOT / "docs" / "annotation")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for ann in annotators:
        rows = sorted(doc["responses"],
                      key=lambda r: hashlib.sha256(
                          f"{ann}|{r['response_id']}".encode()).hexdigest())
        p = out_dir / f"annotation_{ann}.csv"
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["response_id", "options", "tutor_reply", "choice",
                        "notes"])
            for r in rows:
                o, _key = opts[r["item_id"]]
                w.writerow([r["response_id"],
                            " | ".join(f"{i + 1}. {t}" for i, t in enumerate(o)),
                            r["response"], "", ""])
        written.append(p)
    return written


# ---------------------------------------------------------------- scoring ----
def wilson(k: int, n: int, z: float = 1.959963984540054):
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, c - h, c + h)


def read_labels(path: Path, sample_ids: set[str], n_options_by_id):
    """Parse one annotator's completed sheet. Refuses anything ambiguous."""
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    seen, out = set(), {}
    for r in rows:
        rid = (r.get("response_id") or "").strip()
        if rid not in sample_ids:
            raise ValueError(
                f"{path.name} carries response_id {rid!r}, which is not in the "
                f"frozen sample. The two annotators must label the same rows.")
        if rid in seen:
            raise ValueError(f"{path.name} labels {rid!r} twice.")
        seen.add(rid)
        raw = (r.get("choice") or "").strip().lower()
        if raw == "":
            continue                      # not yet labelled; coverage gate below
        if raw in (LABELS_CANNOT_TELL, "cannot tell", "?"):
            out[rid] = None
            continue
        if not raw.isdigit():
            raise ValueError(
                f"{path.name} row {rid}: choice {raw!r} is neither an option "
                f"number nor {LABELS_CANNOT_TELL!r}. The codebook admits no "
                f"third form, and guessing which was meant is exactly the "
                f"judgement this module refuses to make for a person.")
        i = int(raw)
        n_opt = n_options_by_id[rid]
        if not (1 <= i <= n_opt):
            raise ValueError(
                f"{path.name} row {rid}: option {i} outside 1..{n_opt}.")
        out[rid] = i - 1
    return out


def score(bank: str = "A", roster: str = "ol", ann_dir: Path | None = None,
          annotators=("A1", "A2")):
    if not SAMPLE_PATH.exists():
        raise FileNotFoundError(
            f"{SAMPLE_PATH.name} does not exist -- run `freeze` and hand the "
            f"sheets to the annotators before scoring.")
    doc = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    by_id = {r["response_id"]: r for r in doc["responses"]}
    opts = _options_by_item(bank, roster)
    n_opt_by_id = {rid: len(opts[r["item_id"]][0]) for rid, r in by_id.items()}
    key_by_id = {rid: opts[r["item_id"]][1] for rid, r in by_id.items()}

    ann_dir = ann_dir or (ROOT / "docs" / "annotation")
    labels = {}
    for ann in annotators:
        p = ann_dir / f"annotation_{ann}.csv"
        if not p.exists():
            return {"status": "not_labelled_yet",
                    "reason": f"{p.name} has not been returned.",
                    "n_expected": len(by_id)}
        labels[ann] = read_labels(p, set(by_id), n_opt_by_id)

    done = min(len(v) for v in labels.values())
    if done < len(by_id):
        # 4.59: a prefix of an ordered sheet is not a sample of it.
        return {"status": "partial", "n_labelled": done, "n_expected": len(by_id),
                "reason": "coverage below 100%; agreement and the "
                          "sensitivity/specificity pair are withheld, because "
                          "the sheet is walked in order and the cells are not "
                          "exchangeable."}

    a, b = annotators
    ids = sorted(by_id)
    agree = sum(1 for i in ids if labels[a][i] == labels[b][i])
    po = agree / len(ids)
    # Cohen's kappa over the choice, treating cannot_tell as its own category.
    cats = sorted({labels[a][i] for i in ids} | {labels[b][i] for i in ids},
                  key=lambda x: (x is None, x))
    pe = sum((sum(1 for i in ids if labels[a][i] == c) / len(ids)) *
             (sum(1 for i in ids if labels[b][i] == c) / len(ids)) for c in cats)
    kappa = (po - pe) / (1 - pe) if pe < 1 else float("nan")

    # A human "recovered the key" when BOTH annotators chose it: the consensus
    # is the conservative reading, and it is the one the Method's construct
    # names -- an unprepared reader picking the keyed option from the reply.
    human_leak = {i: (labels[a][i] == key_by_id[i] == labels[b][i]) for i in ids}
    return {"status": "complete", "n": len(ids),
            "percent_agreement": po, "cohen_kappa": kappa,
            "n_human_leak": sum(human_leak.values()),
            "human_leak": human_leak}


def automated_vs_human(bank: str = "A", roster: str = "ol", **kw):
    """Sensitivity and specificity of the automated measure, with Wilson intervals.

    The cut is NOT invented here. `leak_adj` is continuous, so a sensitivity
    needs a threshold, and choosing one now -- after both the responses and the
    labels exist -- would be choosing the operating point that flatters the
    instrument. The grid is the one the manuscript already reports
    (`threshold_sensitivity` in results_bank<ID>_<roster>.json), so every point
    was fixed before this comparison was possible.
    """
    h = score(bank, roster, **kw)
    if h["status"] != "complete":
        return h
    res = json.loads((ROOT / "data" / f"results_bank{bank}_{roster}.json")
                     .read_text(encoding="utf-8"))
    grid = sorted(float(t) for t in (res.get("threshold_sensitivity") or {}))
    if not grid:
        raise ValueError(
            "results file carries no threshold grid; refusing to invent one.")

    doc = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    by_id = {r["response_id"]: r for r in doc["responses"]}
    auto = {}
    for line in (RUNS / f"solve_bank{bank}_{roster}.jsonl").read_text(
            encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        auto[(r["cell_key"], r.get("rep"))] = r.get("leak_adj")

    pairs = []
    for rid, hv_leak in h["human_leak"].items():
        s = by_id[rid]
        v = auto.get((s["cell_key"], s["rep"]))
        if v is None:
            raise ValueError(
                f"no automated score for sampled response {rid}; the sample and "
                f"the solve output have diverged and the comparison would be "
                f"computed over whichever rows happened to match.")
        pairs.append((hv_leak, v))

    out = {"status": "complete", "n": len(pairs), "by_threshold": {}}
    for t in grid:
        tp = sum(1 for hl, v in pairs if hl and v >= t)
        fn = sum(1 for hl, v in pairs if hl and v < t)
        tn = sum(1 for hl, v in pairs if not hl and v < t)
        fp = sum(1 for hl, v in pairs if not hl and v >= t)
        sens, sl, sh = wilson(tp, tp + fn)
        spec, pl, ph = wilson(tn, tn + fp)
        out["by_threshold"][f"{t:g}"] = {
            "sensitivity": sens, "sens_ci": [sl, sh], "n_positive": tp + fn,
            "specificity": spec, "spec_ci": [pl, ph], "n_negative": tn + fp}
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=("freeze", "sheets", "score"))
    ap.add_argument("--bank", default="A")
    ap.add_argument("--roster", default="ol")
    a = ap.parse_args()
    if a.action == "freeze":
        d = freeze(a.bank, a.roster)
        print(f"sample frozen: {d['n']} responses, sha256 {d['sha256'][:16]}...")
    elif a.action == "sheets":
        for p in sheets(bank=a.bank, roster=a.roster):
            print(f"wrote {p}")
    else:
        r = automated_vs_human(a.bank, a.roster)
        print(json.dumps(r, indent=2)[:4000])
        if r["status"] != "complete":
            raise SystemExit(2)
