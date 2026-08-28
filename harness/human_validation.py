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
import io
import json
import math
from pathlib import Path

import config as C

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent
RUNS = ROOT / "data" / "runs"
# The annotation sheets sit beside the codebook that governs them, under
# harness/docs -- not under the project root's docs/. The first version
# split them: `_options_by_item` resolved ROOT/harness/banks while `sheets`
# resolved ROOT/docs, so the files landed where the instructions did not say.
ANNOTATION_DIR = HARNESS / "docs" / "annotation"

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
    out_dir = out_dir or ANNOTATION_DIR
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


EXPECTED_HEADER = ("response_id", "options", "tutor_reply", "choice", "notes")


def _read_sheet_rows(path: Path):
    """Parse a returned sheet, whatever spreadsheet wrote it.

    A sheet comes back from a PERSON, and their spreadsheet picks the dialect.
    A2's sheet was returned UTF-8-with-BOM, semicolon-delimited, and carrying an
    extra `Column1;...;Column5` banner row above the real header -- none of it
    the annotator's doing, none of it touching a label. Reading only the dialect
    `sheets()` writes would have rejected 240 correct labels over Excel's
    regional separator.

    This is a CHECKED parse, not a sniff. The header row is located by NAME, the
    delimiter is the candidate that actually splits that line into the expected
    columns, and the parsed header must equal EXPECTED_HEADER exactly. A file
    that cannot be parsed into those five columns raises rather than being
    mis-split: putting a label in the wrong column is precisely the failure this
    module must never resolve by guessing, and it is the one that would not
    announce itself -- a shifted row still yields a number.
    """
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines)
                  if "response_id" in ln and "tutor_reply" in ln), None)
    if start is None:
        raise ValueError(
            f"{path.name} has no header row naming response_id and tutor_reply; "
            f"this does not look like an annotation sheet.")
    body = "".join(lines[start:])
    for delim in (",", ";", "\t"):
        try:
            rows = list(csv.reader(io.StringIO(body), delimiter=delim))
        except csv.Error:
            continue
        if rows and tuple(h.strip() for h in rows[0]) == EXPECTED_HEADER:
            return [dict(zip(EXPECTED_HEADER, r)) for r in rows[1:]
                    if len(r) == len(EXPECTED_HEADER)], delim
    raise ValueError(
        f"{path.name}: no delimiter in ',', ';', tab splits the header line "
        f"into exactly {list(EXPECTED_HEADER)}. Refusing to guess how the "
        f"columns line up -- a mis-split sheet still produces a number.")


def sheet_path(ann_dir: Path, ann: str) -> Path:
    """The file an annotator actually returned, preferring `_completed`.

    `sheets()` writes a BLANK template at `annotation_<ann>.csv`. When a
    completed copy sits beside it the template is not data, and reading the
    template would report `not_labelled_yet` over a finished sheet -- the same
    shape as 4.33's quiet half, where the stale copy yields no number rather
    than a wrong one. The resolved path is returned in the result so the reader
    can see which file the statistics came from.
    """
    done = ann_dir / f"annotation_{ann}_completed.csv"
    return done if done.exists() else ann_dir / f"annotation_{ann}.csv"


def read_labels(path: Path, sample_ids: set[str], n_options_by_id):
    """Parse one annotator's completed sheet. Refuses anything ambiguous."""
    rows, _delim = _read_sheet_rows(path)
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

    ann_dir = ann_dir or ANNOTATION_DIR
    labels, sources = {}, {}
    for ann in annotators:
        p = sheet_path(ann_dir, ann)
        if not p.exists():
            return {"status": "not_labelled_yet",
                    "reason": f"{p.name} has not been returned.",
                    "n_expected": len(by_id)}
        labels[ann] = read_labels(p, set(by_id), n_opt_by_id)
        sources[ann] = p.name

    done = min(len(v) for v in labels.values())
    if done < len(by_id):
        # 4.59: a prefix of an ordered sheet is not a sample of it.
        return {"status": "partial", "n_labelled": done, "n_expected": len(by_id),
                "sources": sources,
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
    return {"status": "complete", "n": len(ids), "sources": sources,
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

    # THE REPLICATE IS INSIDE `cell_key`, NOT BESIDE IT. A solve row's key is
    # `<item>|<generator>|<solicitation>|<regime>|<rep>` and its own `rep` field
    # is None on all 2,016 rows, so joining on the PAIR matched nothing at all.
    # The sample carries `rep` separately as well, which is why the pair looked
    # like the safer key. It is still load-bearing -- as a CHECK that the two
    # encodings of the replicate agree, below -- but it is not part of the join.
    #
    # This never ran before labels existed, and the guard is the reason that
    # cost a diagnosis rather than a number: it refuses the whole comparison on
    # the first miss instead of computing over the rows that happened to match,
    # which would have silently reported a sensitivity over a biased subset.
    auto = {}
    for line in (RUNS / f"solve_bank{bank}_{roster}.jsonl").read_text(
            encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["cell_key"] in auto:
            raise ValueError(
                f"solve output carries {r['cell_key']!r} twice; cell_key is the "
                f"join key and must identify one response.")
        auto[r["cell_key"]] = r.get("leak_adj")

    pairs = []
    for rid, hv_leak in h["human_leak"].items():
        srow = by_id[rid]
        if srow["cell_key"].rsplit("|", 1)[-1] != str(srow["rep"]):
            raise ValueError(
                f"sampled response {rid} has rep {srow['rep']!r} but a cell_key "
                f"ending {srow['cell_key'].rsplit('|', 1)[-1]!r}; the two "
                f"encodings of the replicate disagree.")
        v = auto.get(srow["cell_key"])
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


RESULTS_PATH = ROOT / "data" / "human_validation_results.json"
ADJUDICATIONS_PATH = ANNOTATION_DIR / "adjudications.json"


def adjudications():
    """Cells changed after a sheet came back, as data rather than as prose.

    An adjudication is a person's reading of their own ambiguous entry, so it
    belongs in the record with its alternative. Keeping it as a sentence in a
    notes file would leave the manuscript asserting `it changes nothing` with
    nothing computing that -- a literal with no check, which is how the 256/240
    slip survived a day of green runs (4.46). `assemble` re-scores the whole
    sample under every `alternative` and reports the displacement.
    """
    if not ADJUDICATIONS_PATH.exists():
        return []
    return json.loads(ADJUDICATIONS_PATH.read_text(encoding="utf-8"))["adjudications"]


def _rewrite_sheets(ann_dir: Path, dest: Path, annotators, subs):
    """Copy both sheets into `dest`, applying {(sheet, rid): choice}."""
    dest.mkdir(parents=True, exist_ok=True)
    for ann in annotators:
        rows, _ = _read_sheet_rows(sheet_path(ann_dir, ann))
        with (dest / f"annotation_{ann}_completed.csv").open(
                "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(EXPECTED_HEADER))
            w.writeheader()
            for r in rows:
                k = (ann, r["response_id"])
                if k in subs:
                    r = dict(r, choice=subs[k])
                w.writerow(r)


def adjudication_sensitivity(bank: str = "A", roster: str = "ol",
                             ann_dir: Path | None = None,
                             annotators=("A1", "A2")):
    """Re-score the sample under the other reading of every adjudicated cell.

    Returns None when nothing was adjudicated. Raises if a sheet does not carry
    the reading the record claims for it -- the record and the labelled file are
    two copies of the same fact, and the one you read must not be the stale one
    (4.33).
    """
    adj = adjudications()
    if not adj:
        return None
    ann_dir = ann_dir or ANNOTATION_DIR
    doc = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    by_id = {r["response_id"]: r for r in doc["responses"]}
    n_opt = {i: len(_options_by_item(bank, roster)[r["item_id"]][0])
             for i, r in by_id.items()}
    subs = {}
    for a in adj:
        rows, _ = _read_sheet_rows(sheet_path(ann_dir, a["sheet"]))
        got = {r["response_id"]: r["choice"].strip() for r in rows}
        if got.get(a["response_id"]) != a["read_as"]:
            raise ValueError(
                f"adjudications.json says {a['sheet']} row {a['response_id']} "
                f"reads {a['read_as']!r}, but the sheet carries "
                f"{got.get(a['response_id'])!r}. The record and the labels have "
                f"drifted; fix the sheet or the record, do not report either.")
        subs[(a["sheet"], a["response_id"])] = a["alternative"]

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        alt_dir = Path(td)
        _rewrite_sheets(ann_dir, alt_dir, annotators, subs)
        alt = score(bank, roster, ann_dir=alt_dir, annotators=annotators)
        alt_av = automated_vs_human(bank, roster, ann_dir=alt_dir,
                                    annotators=annotators)
    base = score(bank, roster, ann_dir=ann_dir, annotators=annotators)
    base_av = automated_vs_human(bank, roster, ann_dir=ann_dir,
                                 annotators=annotators)
    worst = max(
        max(abs(alt_av["by_threshold"][t][k] - base_av["by_threshold"][t][k])
            for k in ("sensitivity", "specificity"))
        for t in base_av["by_threshold"])
    return {"n_adjudicated": len(adj),
            "alt_percent_agreement": (alt["percent_agreement"]),
            "alt_cohen_kappa": alt["cohen_kappa"],
            "alt_n_human_leak": alt["n_human_leak"],
            "max_rate_displacement": worst}


def assemble(bank: str = "A", roster: str = "ol", **kw):
    """Everything the manuscript quotes, in one artefact with its inputs hashed.

    `results.py` is the study's rule that a reported number is read from an
    assembled file rather than recomputed at the point of use (4.41); this is
    the same rule for the Tier-3 tier. `verify_numbers` reads THIS, so a
    re-labelled sheet moves the manuscript's numbers or fails the check, and
    cannot leave the two disagreeing quietly.

    The agreement STRUCTURE is included, not just the headline: the two
    annotators differ far more in willingness to commit than in which option
    they pick, and a percent-agreement figure alone hides that.
    """
    h = score(bank, roster, **kw)
    if h["status"] != "complete":
        return h
    av = automated_vs_human(bank, roster, **kw)

    doc = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    by_id = {r["response_id"]: r for r in doc["responses"]}
    opts = _options_by_item(bank, roster)
    n_opt = {i: len(opts[r["item_id"]][0]) for i, r in by_id.items()}
    key = {i: opts[r["item_id"]][1] for i, r in by_id.items()}
    ann_dir = kw.get("ann_dir") or ANNOTATION_DIR
    annotators = kw.get("annotators", ("A1", "A2"))
    L = {a: read_labels(sheet_path(ann_dir, a), set(by_id), n_opt)
         for a in annotators}
    a, b = annotators
    ids = sorted(by_id)
    n = len(ids)

    committed = [i for i in ids if L[a][i] is not None and L[b][i] is not None]
    per_ann = {x: {"cannot_tell": sum(1 for i in ids if L[x][i] is None),
                   "recovered_key": sum(1 for i in ids if L[x][i] == key[i])}
               for x in annotators}
    struct = {
        "both_cannot_tell": sum(1 for i in ids
                                if L[a][i] is None and L[b][i] is None),
        "exactly_one_cannot_tell": sum(1 for i in ids
                                       if (L[a][i] is None) ^ (L[b][i] is None)),
        "same_option": sum(1 for i in committed if L[a][i] == L[b][i]),
        "different_option": sum(1 for i in committed if L[a][i] != L[b][i]),
        "n_both_committed": len(committed),
    }
    # The consensus rule is the reported one; the liberal rule is stated beside
    # it because the two BRACKET the human-side prevalence, and quoting either
    # alone would present one end of a wide band as a point estimate.
    liberal = sum(1 for i in ids if key[i] in (L[a][i], L[b][i]))

    def _ci(k, m):
        p_, lo, hi = wilson(k, m)
        return {"k": k, "n": m, "p": p_, "ci": [lo, hi]}

    sha = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
           for p in [SAMPLE_PATH] + [sheet_path(ann_dir, x) for x in annotators]}
    return {
        "status": "complete", "bank": bank, "roster": roster, "n": n,
        "sources": h["sources"], "input_sha256": sha,
        "percent_agreement": _ci(struct["same_option"]
                                 + struct["both_cannot_tell"], n),
        "cohen_kappa": h["cohen_kappa"],
        "agreement_given_both_committed": _ci(struct["same_option"],
                                              struct["n_both_committed"]),
        "structure": struct, "per_annotator": per_ann,
        "adjudications": adjudications(),
        "adjudication_sensitivity": adjudication_sensitivity(
            bank, roster, ann_dir=ann_dir, annotators=annotators),
        "recovered_consensus": _ci(h["n_human_leak"], n),
        "recovered_either": _ci(liberal, n),
        "by_threshold": av["by_threshold"],
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=("freeze", "sheets", "score", "assemble"))
    ap.add_argument("--bank", default="A")
    ap.add_argument("--roster", default="ol")
    a = ap.parse_args()
    if a.action == "freeze":
        d = freeze(a.bank, a.roster)
        print(f"sample frozen: {d['n']} responses, sha256 {d['sha256'][:16]}...")
    elif a.action == "sheets":
        for p in sheets(bank=a.bank, roster=a.roster):
            print(f"wrote {p}")
    elif a.action == "assemble":
        r = assemble(a.bank, a.roster)
        if r["status"] != "complete":
            print(json.dumps(r, indent=2))
            raise SystemExit(2)
        RESULTS_PATH.write_text(json.dumps(r, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
        print(f"wrote {RESULTS_PATH.relative_to(ROOT)} "
              f"(n={r['n']}, agreement {r['percent_agreement']['p']:.3f}, "
              f"kappa {r['cohen_kappa']:.3f})")
    else:
        r = automated_vs_human(a.bank, a.roster)
        print(json.dumps(r, indent=2)[:4000])
        if r["status"] != "complete":
            raise SystemExit(2)
