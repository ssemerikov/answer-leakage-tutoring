"""Check every empirical number in the manuscript against the data it came from.

WHY THIS EXISTS. Success criterion 2 of the design spec is that every quantity in
`paper.tex` traces to a file in `data/`. Nothing enforced it, and the drift that
follows is silent: `config.py` carried
`corr(parsed_fraction, baseline) = 0.95` (honestly labelled "first 10 items")
long after the completed screen made it 0.892, which is what the manuscript
already said. Two files, two numbers, same name, no error anywhere. A reviewer
recomputing from the deposited data would have found it before we did.

WHAT IT DOES. Each check recomputes a quantity from the run records and asserts
that the rendered literal appears in `paper.tex`. It is an audit, not a parser:
we never try to read numbers out of LaTeX and guess what they mean, because a
regex that silently matches nothing passes just as quietly as the drift it was
meant to catch. Hence `MISSING` is a failure, not a skip.

Checks whose data does not exist yet are reported as PENDING and do not fail the
run -- the manuscript still carries `\\TBD` for those, which is the intended
state until the backend returns.

KNOWN LIMITATION, stated rather than papered over. A short literal such as `40`
or `29` occurs in many places in a LaTeX file, so those checks prove the number
is *present*, not that it sits in the sentence that claims it. They catch drift
(the value changed and the prose did not) and not misplacement (the right value
in the wrong clause). The multi-digit checks -- `0.892`, `0.845`, `0.182` -- are
specific enough to be real. Do not read a green run as proof the prose is right;
read it as proof the prose is not stale.

Usage:
    python3 verify_numbers.py            # audit
    python3 verify_numbers.py -v         # show recomputed values too
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics as st
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as C  # noqa: E402  - the recorded pre-measurement ceiling
import solvability as S  # noqa: E402  - after sys.path, so this runs from any cwd
from analyse import mean_ci as A_mean_ci  # noqa: E402
from analyse import wilson_ci as S_wilson  # noqa: E402  - one Wilson, not two

ROOT = Path(__file__).resolve().parent.parent
PAPER = ROOT / "source" / "paper.tex"
RUNS = ROOT / "data" / "runs"
# Module-level so tests can redirect it the way they already redirect RUNS.
# Inline `ROOT / "harness" / "banks" / ...` left the bank reads unpatchable, so
# a test with a temporary runs directory silently read the REAL banks.
BANKS = ROOT / "harness" / "banks"
PDF = ROOT / "source" / "paper.pdf"

# CEAI: "a concise and factual abstract which does not exceed 250 words".
# Retrieved from the guide for authors via search -- ScienceDirect 403s a direct
# fetch, so treat it as well supported rather than verified, and re-check at
# submission. The action is unambiguous under either 250 or 300.
ABSTRACT_LIMIT = 250


def _letters(tok: str) -> str:
    return re.sub(r"[^a-z]", "", tok.lower())


def abstract_words(tex: str, pdf: Path = PDF) -> tuple[int | None, str]:
    r"""Count the abstract as RENDERED, because there is only one rendering.

    This number has been produced by hand four times across the life of the
    manuscript -- 292, 265, 249 and 247 -- and every one of them came from a
    fresh macro stripper written at the moment it was needed. They disagree for
    a reason that is invisible unless you look: replacing `$` with a space turns
    `($27$ items` into `( 27 items`, inventing a token, while deleting it turns
    `\Delta` into nothing and destroys one. A stripper is a second
    implementation of LaTeX and it will not agree with the first (4.33).

    So the count is taken off the built PDF, which is what a reader sees and
    what a paste into Editorial Manager carries. The abstract is located by
    anchors DERIVED FROM THE SOURCE each run -- its own first and last three
    words -- so a rewrite moves the anchors with it. Tokens are compared
    letters-only, which makes `game's` and the PDF's `game’s` the same word.

    Returns (count, note). A count of None is not a pass: it means the PDF is
    missing or older than the source, so the question was not asked.
    """
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", tex, re.S)
    if not m:
        return None, "no abstract environment in paper.tex"
    body = "\n".join(l for l in m.group(1).splitlines()
                     if not l.lstrip().startswith("%"))
    src_toks = [t for t in _letters_seq(body) if t]
    if len(src_toks) < 6:
        return None, "abstract too short to anchor"
    if not pdf.exists():
        return None, f"no build at {pdf.name} -- run make"
    if pdf.stat().st_mtime < PAPER.stat().st_mtime:
        return None, f"{pdf.name} is older than paper.tex -- run make"

    try:
        out = subprocess.run(["pdftotext", "-f", "1", "-l", "1", str(pdf), "-"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"pdftotext unavailable ({e})"
    if out.returncode != 0:
        return None, "pdftotext failed on page 1"
    page = out.stdout.replace("-\n", "").split()
    keys = [_letters(t) for t in page]

    def find(seq, start=0):
        n = len(seq)
        for i in range(start, len(keys) - n + 1):
            if keys[i:i + n] == seq:
                return i
        return -1

    head, tail = src_toks[:3], src_toks[-3:]
    i = find(head)
    if i < 0:
        return None, "abstract head not found on page 1 of the PDF"
    j = find(tail, i)
    if j < 0:
        return None, "abstract tail not found after its head"
    return len(page[i:j + len(tail)]), "rendered, page 1"


def _letters_seq(body: str) -> list[str]:
    """Letters-only tokens of a LaTeX fragment, for ANCHORING only.

    Deliberately crude: it has to find three words, not count them, so its
    disagreements with the renderer do not reach the number.
    """
    s = re.sub(r"\\[a-zA-Z]+\*?", " ", body)
    s = re.sub(r"[${}~\\]", " ", s)
    return [_letters(t) for t in s.split()]


def _load(name: str) -> list[dict] | None:
    p = RUNS / name
    if not p.exists():
        return None
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return rows or None


def _corr(xs: list[float], ys: list[float]) -> float:
    """Pearson r. Raises on a constant input rather than dividing by zero.

    A constant input is reachable here, not merely hypothetical: this is called
    on `parsed_fraction`, which saturates at 1.0 whenever every solver answers
    every call -- the intended state once the token budgets are right. On a
    small or homogeneous item set that column can be constant, and correlation
    is undefined there, not zero. The bare division raised ZeroDivisionError
    from four frames deep, which reads as a tool crash rather than as "this
    quantity does not exist for this data".
    """
    if len(xs) != len(ys):
        raise ValueError(f"length mismatch: {len(xs)} vs {len(ys)}")
    if len(xs) < 2:
        raise ValueError("correlation needs at least two observations")
    mx, my = st.mean(xs), st.mean(ys)
    sx = sum((a - mx) ** 2 for a in xs)
    sy = sum((b - my) ** 2 for b in ys)
    if sx == 0 or sy == 0:
        which = "x" if sx == 0 else "y"
        raise ValueError(
            f"correlation is undefined: {which} has zero variance "
            f"(every value is {mx if which == 'x' else my:.4g}). If this is "
            f"parsed_fraction, a constant 1.0 means no solver ever declined -- "
            f"report that fact instead of a correlation with it.")
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    return num / (sx * sy) ** 0.5


def _spearman(x: list[float], y: list[float]) -> float:
    def rank(v: list[float]) -> list[float]:
        s = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(s):
            j = i
            while j + 1 < len(s) and v[s[j + 1]] == v[s[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[s[k]] = avg
            i = j + 1
        return r

    return _corr(rank(x), rank(y))


_UNITS = ("zero", "one", "two", "three", "four", "five", "six", "seven",
          "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
          "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety")


def _spell(n: int) -> str:
    """Spell an integer below 100 as words, for checks against running prose.

    The manuscript's number convention is mixed, and deliberately so. Reported
    data and design counts stay numerals -- "60 items", "40 three-option
    multiple-choice items", "20 short-answer items", "64 cells" -- because they
    are quantities a reader may want to recompute. Incidental quantities in
    running prose are spelled out: "sixty calls", "two items in each bank",
    "four times in total".

    So this is the right renderer for the second kind ONLY. Using it on a
    reported count produces a literal the manuscript does not contain, which is
    a MISSING that reflects the check and not the prose -- exactly what happened
    when the short-answer count was first pinned here. When adding a check, read
    the sentence and match it; do not assume either convention.

    Returning the numeral above 99 is deliberate: it fails loudly rather than
    passing on a wrong literal.
    """
    if n < 0 or n >= 100:
        return str(n)
    if n < 20:
        return _UNITS[n]
    tens, unit = divmod(n, 10)
    return _TENS[tens] if unit == 0 else f"{_TENS[tens]}-{_UNITS[unit]}"


def _verifier_audit(bank: str, roster: str = "ol") -> dict:
    """One call into the audit tool, so the manuscript and the CLI agree."""
    import verifier_audit as VA
    return VA.audit(bank, roster, "stress", True)


def checks() -> list[tuple[str, str | None, str]]:
    """(label, literal-that-must-appear-in-paper, note). None => PENDING."""
    out: list[tuple[str, str | None, str]] = []
    kr = _load("screen_bankA.jsonl")

    if kr is None:
        out.append(("kiro screen", None, "data/runs/screen_bankA.jsonl absent"))
        return out

    ret = [r for r in kr if r["passes_headroom"]]
    solvers = list(kr[0]["per_solver"].keys())

    def tot(rows, s, f):
        return sum(r["per_solver"][s][f] for r in rows)

    out.append(("n screened (Bank A, kr)", str(len(kr)), "all candidates"))
    out.append(("n retained", str(len(ret)), "passes_headroom"))

    r_all = _corr([r["parsed_fraction"] for r in kr], [r["baseline"] for r in kr])
    out.append(("corr(parsed, baseline) all 40", f"{r_all:.2f}", "quoted as r in placebo section"))

    # The Kiro conditional-accuracy figures (0.384 / 0.391) and its 0.50 parse
    # rate were REMOVED from the manuscript, not merely reworded: they were the
    # evidence for a large willingness artefact, and that artefact was an
    # instrument defect (a 16-token budget recording truncations as declines).
    # The primary panel's figures replace them and are checked below.
    parsed = sum(tot(ret, s, "parsed") for s in solvers)
    calls = sum(tot(ret, s, "calls") for s in solvers)
    out.append(("kr pilot parse rate (context only)", f"{parsed / calls:.2f}", "not quoted as a result"))

    # The solver table is now built from the PRIMARY (ol) panel; see below.
    # The Kiro per-solver figures are no longer quoted in the manuscript.

    # Screen gap and threshold invariance -- the answer to "you tuned the cutoff".
    #
    # THE PRIMARY SCREEN IS THE OLLAMA ONE. The Kiro screen measured the
    # PRE-REBALANCE bank (sha e5b52f1b), which is not the study bank
    # (d1b4b6c8); it also ran every solver at a 16-token budget with no
    # truncation guard. Its numbers describe a different artefact measured with
    # a worse instrument, so the manuscript reports the Ollama screen and these
    # checks follow it. The Kiro figures survive only where the manuscript
    # explicitly labels them as the earlier panel.
    ol = _load("screen_bankA_ol.jsonl")
    # AN IN-PROGRESS SCREEN IS NOT A SCREEN. The stage is append-only and
    # resumable, so mid-run the file holds a valid prefix -- and every quantity
    # below is a property of the COMPLETE set. Computing them on a prefix does
    # not merely give an early answer, it gives a wrong one: with one row
    # written, `min(rejected) - max(retained)` is not an invariance band at all,
    # and the assertion below fired with "the band is 48.00 calls" during the
    # 2026-08-17 re-screen -- a confusing failure in the one tool whose job is to
    # keep the manuscript and the data honest. Report PENDING and wait.
    n_mcq = sum(1 for i in json.loads(
        (BANKS / "bank_a.json").read_text(encoding="utf-8")
    )["items"] if i.get("type") == "mcq")
    if ol is None:
        out.append(("primary screen (ol)", None, "screen_bankA_ol.jsonl absent"))
    elif len(ol) < n_mcq:
        out.append(("primary screen (ol)", None,
                    f"screen IN PROGRESS: {len(ol)}/{n_mcq} items -- every "
                    f"figure below is a property of the complete set"))
        ol = None
    else:
        ol_ret = [r for r in ol if r["passes_headroom"]]
        kept_b = sorted(r["baseline"] for r in ol_ret)
        rej_b = sorted(r["baseline"] for r in ol if not r["passes_headroom"])
        out.append(("primary: n retained", str(len(ol_ret)), "ol screen"))
        out.append(("primary: n discarded", str(len(rej_b)), "ol screen"))
        out.append(("retained baseline range",
                    f"$[{min(kept_b):.2f}, {max(kept_b):.2f}]$", "ol screen gap"))
        out.append(("discarded baseline range",
                    f"$[{min(rej_b):.2f}, {max(rej_b):.2f}]$", "ol screen gap"))
        # The band is quoted in CALLS: as a decimal, 0.05 reads as a comfortable
        # margin when it is in fact three solver calls out of sixty.
        #
        # This was an `assert 0.99 <= band_calls <= 1.01` -- a CONSTANT from the
        # v2.1 data, with the prose's "one solver call wide" baked into the tool
        # that was supposed to be checking the prose. When the §4.29 re-screen
        # widened the band to three calls it aborted `verify_numbers` outright,
        # so the one tool that keeps the manuscript honest crashed at exactly
        # the moment the manuscript became wrong. A check that hardcodes the
        # value it is verifying cannot survive its own subject changing.
        #
        # Emitted as a literal instead, like every other quantity here: the
        # value is recomputed from the data and the manuscript must contain it.
        k_tot = ol[0].get("k_per_solver", 20) * len(ol[0].get("per_solver", {}) or [1])
        band_calls = (min(rej_b) - max(kept_b)) * k_tot
        # Rounded with a tolerance, not compared exactly: the band is a
        # difference of two exact sixtieths scaled by 60, so it lands on
        # 2.9999999999999996 rather than 3.0 and an equality test sends a whole
        # number down the decimal branch. The same class of error as §4.17,
        # where a bare `<=` decided an exact tie by one ULP.
        out.append(("invariance band (solver calls)",
                    _spell(round(band_calls))
                    if abs(band_calls - round(band_calls)) < 1e-6
                    else f"{band_calls:.2f}",
                    "min(rejected) - max(retained), in calls out of "
                    f"{k_tot}; spelled out because the Method reads "
                    f"'{_spell(round(band_calls))} solver call"
                    f"{'' if round(band_calls) == 1 else 's'} wide'"))

        # THE SCREEN RETAINS ON A POOLED BASELINE, THE ANALYSIS WORKS PER
        # SOLVER (spec 4.34). `per_solver_leak` computes
        # `leak_s = hinted_s - baseline_s`, so a solver already at baseline
        # 1.000 has `leak_s <= 0` BY CONSTRUCTION -- that cell is censored, not
        # noisy, and no tutor response can register in it. The pooled retention
        # rule cannot see this, because it is a statement about the panel.
        #
        # Emitted as literals rather than asserted: the bias is CONSERVATIVE
        # (censoring at the top can only shrink measured leakage), so this is a
        # limitation the manuscript must disclose with its size, not a defect
        # that should stop the run.
        cells = [(r["item_id"], sv, v["hits"] / v["calls"], r["chance"] + 0.15)
                 for r in ol_ret for sv, v in r["per_solver"].items()
                 if v.get("calls")]
        if cells:
            sat = [c for c in cells if c[2] >= 1.0]
            above = [c for c in cells if c[2] > c[3]]
            # SPELLED AND QUALIFIED, not bare integers. `str(2)` matches the
            # digit "2" anywhere in a LaTeX file -- including a citation year,
            # a table column or a macro argument -- so the check would report
            # VERIFIED against a manuscript that never states the number. A
            # check that cannot fail is worse than no check, because it is
            # counted in the "0 missing" line that the release rests on.
            out.append(("solver-saturated baseline cells",
                        f"${len(sat)}$ of the ${len(cells)}$",
                        "(item, solver) cells on retained items where baseline "
                        "is 1.000, so leak_s <= 0 by construction (spec 4.34)"))
            out.append(("cells above the retention threshold",
                        f"${len(above)}$ of the ${len(cells)}$",
                        "retained on the POOLED baseline while individually "
                        "identifiable to that solver"))

        # THE INVARIANCE BAND ASSUMES EVERY VERDICT IS ESTABLISHED. It reports
        # how far apart the retained and rejected populations sit, which is
        # only a statement about the partition if the partition is real. A
        # failed call is scored as a miss and so can only have DEPRESSED a
        # baseline, which means truncation manufactures apparent headroom and
        # pushes items INTO the retained set -- widening the very band that is
        # quoted as evidence the screen is robust. Checked here and not only in
        # `freeze_bank` because the manuscript quotes the band whether or not
        # anyone re-freezes.
        at_risk = [(r["item_id"], S.truncation_slack(r)) for r in ol
                   if S.truncation_slack(r) < 0]
        assert not at_risk, (
            "screen_bankA_ol.jsonl contains retained verdicts reachable by "
            f"their own failed calls: {at_risk}. The invariance band above is "
            "not a property of a partition the measurement establishes. "
            "Re-measure these items (see measure_budgets.py, spec 4.33).")

    # THE FROZEN BANK IS A SECOND COPY OF THE RETENTION DECISION, and copies
    # drift. `run.screened_items` derives retention live from the screen jsonl;
    # `freeze_bank` derives it again into `bank_a_screened_ol.json`, which is
    # the file that gets deposited and the file CLAUDE.md and the spec cite by
    # sha256. Nothing compared them, and on 2026-08-17 they disagreed: the
    # frozen bank held 15 items from a screen since quarantined for the minimax
    # truncation defect, while the named screen file was being rewritten by the
    # re-screen. The pointer stayed valid because `screen_file` records a NAME,
    # and run files are append-only, so the name silently retargeted.
    #
    # Deliberately NOT gated on the screen being complete: a mismatch between a
    # deposited artefact and the data under it is worth reporting at any moment,
    # and mid-re-screen is exactly when it is true.
    frozen_p = BANKS / "bank_a_screened_ol.json"
    screen_p = RUNS / "screen_bankA_ol.jsonl"
    if frozen_p.exists() and screen_p.exists():
        fz = json.loads(frozen_p.read_text(encoding="utf-8"))
        live = hashlib.sha256(screen_p.read_bytes()).hexdigest()
        claimed = fz.get("screen_sha256")
        if claimed is None:
            out.append(("frozen bank <-> screen", None,
                        f"{frozen_p.name} predates `screen_sha256` and names "
                        f"its screen only as {fz.get('screen_file')!r}; the "
                        f"link cannot be checked. Re-freeze after the screen "
                        f"completes."))
        elif claimed != live:
            out.append(("frozen bank <-> screen", None,
                        f"DRIFT: {frozen_p.name} was frozen from screen "
                        f"{claimed[:12]}..., {screen_p.name} is now "
                        f"{live[:12]}.... The deposited bank no longer "
                        f"describes the data on disk."))
        else:
            out.append(("frozen bank <-> screen", "match",
                        f"{fz.get('n_retained_mcq')} retained, screen "
                        f"{live[:12]}..."))

    # Difficulty independence. This sentence has drifted twice: once claiming
    # rejections were confined to levels 1-2, once claiming they occurred at
    # EVERY level. Levels 3, 4 and 5 have none, so both were wrong. Pin the
    # count and the rank correlation rather than the adjective.
    lv = [(int(r["item_id"].split("-")[1][1:]), 0 if r["passes_headroom"] else 1) for r in kr]
    n_levels_with_rej = len({a for a, b in lv if b})
    out.append(("levels containing a discard", str(n_levels_with_rej), "difficulty independence"))
    rho = _spearman([a for a, _ in lv], [b for _, b in lv])
    out.append(("rho(level, discard)", f"{rho:.2f}", "difficulty independence"))

    # Truncation bias (§4.13). These trace to the QUARANTINED screen, which is
    # the point: the quarantine file is evidence, not waste, and the manuscript
    # cites it. Deleting it would strand these numbers.
    q = _load("QUARANTINE_screen_bankA_ol_truncation_budget600_2026-08-16.jsonl")
    if q is None:
        out.append(("gpt-oss truncation rate", None, "quarantined pilot screen absent"))
    else:
        m = "ol/gpt-oss:120b-cloud"
        calls = sum(r["per_solver"][m]["calls"] for r in q if m in r.get("per_solver", {}))
        failed = sum(r["per_solver"][m]["failed"] for r in q if m in r.get("per_solver", {}))
        if calls:
            out.append(("truncated calls (numerator)", str(failed), "pilot budget 600"))
            out.append(("truncated calls (denominator)", str(calls), "pilot budget 600"))
            # One decimal deliberately: 54/240 is exactly 22.5%, and rounding a
            # half is ambiguous (Python rounds to even -> 22, convention -> 23).
            # Reporting the decimal removes the disagreement rather than picking
            # a side of it.
            out.append(("truncation rate", f"{100 * failed / calls:.1f}\\%", "pilot budget 600"))

    # Truncation-correction direction and magnitude (§4.13). The manuscript
    # first claimed truncation biased baselines UPWARD; the data showed the
    # opposite. Pinned so the corrected direction cannot silently drift back:
    # if any shift ever goes negative, `all_upward` flips and the claim in the
    # Reproducibility section stops being true.
    # `ol` is None above when the screen is incomplete, and these figures are
    # properties of the complete set for the same reason the invariance band is.
    # The old `len(both) >= 5` gate was meant to guard this and does not: a
    # 5-item prefix of a re-running screen passes it, and on 2026-08-17 that
    # produced "shift +0.039" and "most negative shift 0.000" from five rows --
    # numbers with the right shape and no meaning. A count threshold cannot
    # express "complete"; only the bank size can.
    new = _load("screen_bankA_ol.jsonl") if ol is not None else None
    if q and new:
        o = {r["item_id"]: r for r in q}
        n_ = {r["item_id"]: r for r in new}
        both = sorted(set(o) & set(n_))
        # Completeness is enforced on `new` above; what matters here is that the
        # two screens actually overlap. An empty intersection means the files
        # describe different item sets and the comparison is meaningless.
        if both:
            shifts = [(1 - o[i]["parsed_fraction"],
                       n_[i]["baseline"] - o[i]["baseline"]) for i in both]
            lo = [d for t, d in shifts if t < 0.05]
            hi = [d for t, d in shifts if t >= 0.05]
            if lo and hi:
                out.append(("shift, >=5% truncated", f"${st.mean(hi):+.3f}$",
                            "truncation correction"))
                out.append(("most negative shift", f"${min(d for _, d in shifts):.3f}$",
                            "truncation correction"))
            # Two checks were REMOVED here, and the reason matters more than the
            # code. The manuscript used to print the untruncated-group mean as
            # "0.000" and a correlation of 0.87. Both were dropped from the
            # paper, not from the data:
            #
            #   - "0.000" became the stronger prose claim that untruncated items
            #     reproduced EXACTLY, which the assertion below enforces at
            #     1e-9. A printed literal is weaker than an invariant.
            #   - the correlation moved 0.87 -> 0.65 as items 10-12 arrived. It
            #     is unstable at n=12 and leans on one influential point, so the
            #     manuscript no longer claims it. Checking a number the paper
            #     does not assert would be theatre.
            #
            # Deleting a check because the claim was withdrawn is legitimate.
            # Deleting one because it went red is not. This was the former.
            # TOLERANCE IS THE MEASUREMENT'S OWN NOISE FLOOR, not a call count.
            #
            # This assertion has now fired twice, both times on A-L03-05, both
            # times because the threshold was an arbitrary small integer rather
            # than a property of the measurement. Version one demanded every
            # shift be >= 0 and fired at -0.017 (one call). It was "fixed" to
            # two calls -- and on 2026-08-17 the §4.29 re-screen moved the same
            # item -0.067 (four calls) and it fired again.
            #
            # Two calls is 0.033, which at k=60 and p~0.35 is 0.54 of one
            # binomial standard error (SE = sqrt(p(1-p)/60) = 0.062 = 3.7
            # calls). The check demanded a precision roughly twenty times finer
            # than the instrument delivers: solvers run at temperature 1.0, so
            # two independent 60-call estimates of the same baseline differ by
            # up to ~10 calls at 95%. It was not detecting anything; it was
            # waiting to fire.
            #
            # Deriving the floor from the data makes the check say something
            # real: a downward move beyond 3 SE is not sampling variation and
            # would genuinely contradict the directional claim.
            k_total = q[0].get("k_per_solver", 20) * len(q[0].get("per_solver", {}) or [1])
            # Recomputed from `both` rather than from `shifts`, whose first
            # element is a truncation fraction: the error message has to be able
            # to NAME the offending item, and twice now the useful fact about
            # this assertion has been which item tripped it.
            per_item = {i: n_[i]["baseline"] - o[i]["baseline"] for i in both}
            worst_item = min(per_item, key=per_item.get)
            worst = per_item[worst_item]
            # p_hat from the corrected screen over the compared items; clamped
            # away from 0 and 1 so a saturated overlap cannot report SE = 0 and
            # turn this back into an exact-equality check.
            p_hat = st.mean(n_[i]["baseline"] for i in both)
            p_hat = min(max(p_hat, 0.05), 0.95)
            se = (p_hat * (1 - p_hat) / k_total) ** 0.5 if k_total else 0.06
            assert worst >= -3 * se, (
                f"{worst_item} moved down by {worst:.3f}, beyond 3 standard "
                f"errors ({3 * se:.3f}) of the k={k_total} measurement. That is "
                f"no longer sampling variation and contradicts the directional "
                f"claim. Re-check before publishing.")
            out.append(("truncation shift, noise floor (SE, calls)",
                        f"{se * k_total:.1f}",
                        f"binomial SE at k={k_total}; individual shifts below "
                        f"this are not evidence of direction"))
            # The untruncated items are NOT a control, and the manuscript now
            # says so. Every one of them sits at the measurement ceiling
            # (baseline 1.00 in both runs), so reproducing exactly is what a
            # saturated item does, not evidence that the measurement is stable.
            # Solvers run at temperature 1.0 (C.SOLVER_TEMPERATURE), so repeated
            # draws DO carry variance; we simply have no unsaturated item that
            # was measured twice.
            #
            # This check therefore asserts the SATURATION, which is the fact the
            # manuscript's caveat depends on. If an unsaturated item ever lands
            # in the zero-truncation group, the caveat is too strong and the
            # comparison becomes informative -- so this fires to say so.
            untouched = [(t, d) for t, d in shifts if t < 0.005]
            unsat = [i for i, (t, _) in zip(both, shifts)
                     if t < 0.005 and 0.0 < o[i]["baseline"] < 1.0]
            assert not unsat, (
                f"zero-truncation items {unsat} are NOT saturated. The "
                f"manuscript states that every untruncated item sat at the "
                f"ceiling and so provides no evidence of measurement stability. "
                f"That caveat is now wrong -- and the comparison may be "
                f"stronger than claimed. Re-examine before publishing.")

    # Rebalancing effect (section 4.7). The manuscript quotes 0.47 -> 0.12, an
    # instrument-MATCHED pair: both measured under the 600-token budget, so the
    # only difference is option order. It also states the item's value under the
    # final instrument (0.13) so a reader recomputing from the primary screen is
    # not left with an apparent contradiction. All three are pinned, because the
    # temptation when they drift is to quote whichever pair looks tidiest.
    unb = _load("QUARANTINE_screen_bankA_ol_unbalanced_bank_2026-08-16.jsonl")
    bal = _load("QUARANTINE_screen_bankA_ol_truncation_budget600_2026-08-16.jsonl")
    cur = _load("screen_bankA_ol.jsonl")
    def _base(rows, item):
        if not rows:
            return None
        for r in rows:
            if r["item_id"] == item:
                return r["baseline"]
        return None
    b_unb, b_bal, b_cur = (_base(unb, "A-L01-01"), _base(bal, "A-L01-01"),
                           _base(cur, "A-L01-01"))
    if b_unb is not None and b_bal is not None:
        out.append(("rebalance: before (unbalanced, 600tok)", f"${b_unb:.2f}$",
                    "instrument-matched pair"))
        out.append(("rebalance: after (balanced, 600tok)", f"${b_bal:.2f}$",
                    "instrument-matched pair"))
    if b_cur is not None:
        out.append(("same item, final instrument", f"${b_cur:.2f}$",
                    "stated so the deposit is not self-contradictory"))

    # Roster counts. Fillable without any measurement -- the roster is frozen
    # in config -- so these were TBD longer than they needed to be. Pinned
    # because a roster change must not leave the manuscript claiming the old
    # counts.
    import config as _C
    gens = list(_C.OL_GENERATORS)
    fams = sorted({_C.vendor(m) for m in gens})
    out.append(("generators (ol roster)", _spell(len(gens)),
                "spelled out in the manuscript"))
    out.append(("generator vendor families", _spell(len(fams)),
                "spelled out in the manuscript"))

    # Retry and abort policy. Writing these two constants into Method prose put
    # each of them in a third place -- config.py, the code that reads it, and now
    # a sentence -- which is the shape of every drift defect this study has had.
    # Pin the prose to the constant so that changing one fails here, loudly and
    # immediately, rather than in review.
    # Holm families. R1 (model main effects) was vacated before data because the
    # model factor carries no hypothesis test; three families remain. The count
    # is pinned because the manuscript and the pre-registration disagreed about
    # it for as long as both existed -- the Analysis subsection claimed four
    # families in one paragraph and removed the tests from one of them three
    # paragraphs later. Nothing recomputes this from data; it is a design
    # commitment, and pinning it is what keeps the two documents in step.
    out.append(("Holm families", "within three pre-enumerated",
                "R1 vacated 2026-08-17; see spec section 6"))

    # Bank A composition. The deposited bank_a.json holds 60 items: 40 mcq and
    # 20 short-answer. Only the mcq are screenable, because headroom is defined
    # against options and a short-answer item has none. The manuscript says so,
    # but nothing pinned the split, so a reviewer recomputing from the deposit
    # would be the first to notice if it drifted -- which is the exact failure
    # this file exists to prevent.
    _bank_a = json.loads((BANKS / "bank_a.json")
                         .read_text(encoding="utf-8"))["items"]
    _n_short = sum(1 for i in _bank_a if i.get("type") == "short")
    _n_mcq = sum(1 for i in _bank_a if i.get("type") == "mcq")
    out.append(("Bank A mcq items", f"{_n_mcq} three-option multiple-choice items",
                "deposited bank_a.json"))
    out.append(("Bank A short-answer items", f"{_n_short} short-answer items",
                "deposited bank_a.json; excluded from the measure"))

    # Human-validation sample: a function of the roster, not a round number.
    #
    # THIS CHECK USED `_C.SOLICITATIONS` -- ALL FOUR -- AND SO AGREED WITH A
    # MANUSCRIPT THAT SAID 64 CELLS, while the study that ran has three
    # solicitations and 48 (spec 4.6 dropped S1 before data). Two copies of one
    # stale assumption confirming each other is precisely the drift this file
    # exists to catch, and it passed for two days. The roster's own keys are the
    # source of truth, and the realised design is asserted against them below so
    # that a constant and a measurement can never again disagree in silence.
    n_cells = len(gens) * len(_C.REGIMES) * len(_C.OL_SOLICITATION_KEYS)
    per_cell = 5
    out.append(("validation cells", f"{n_cells} cells",
                "generators x regimes x solicitations (ol roster), spelled out"))
    out.append(("validation sample", f"sample of {per_cell * n_cells} responses",
                f"{per_cell} per cell, re-derived after the S1 removal"))
    _gen_rows = _load("generate_bankA_ol.jsonl")
    if _gen_rows:
        realised = {(g["model"], g["regime"], g["solicitation_key"])
                    for g in _gen_rows if g.get("ok") and g.get("response")}
        assert len(realised) == n_cells, (
            f"the roster implies {n_cells} design cells but the responses "
            f"realise {len(realised)}; the sampling frame quoted in the "
            f"manuscript describes a design that was not run.")

    import solvability as _S
    out.append(("retry attempt cap",
                f"{_spell(_C.MAX_CALL_ATTEMPTS)} times in total",
                "C.MAX_CALL_ATTEMPTS, spelled out in the Method"))
    fraction_words = {0.25: "a quarter", 0.5: "half", 0.75: "three quarters"}
    frac = fraction_words.get(_S.MAX_CALL_FAILURE_RATE)
    out.append(("stage abort threshold",
                f"more than {frac} of its calls" if frac else None,
                "solvability.MAX_CALL_FAILURE_RATE, spelled out in the Method"
                if frac else
                f"MAX_CALL_FAILURE_RATE={_S.MAX_CALL_FAILURE_RATE} has no prose "
                "wording; add one to fraction_words and reword the Method"))

    # Injected-context overhead. Traces to data/telemetry/injected_context.json,
    # not to data/runs, so it needs its own loader. Pinned because the paragraph
    # was TBD while the measurement had existed for a day -- the numbers were in
    # the spec prose and in a telemetry file, and nobody had connected them.
    tel = ROOT / "data" / "telemetry" / "injected_context.json"
    if not tel.exists():
        out.append(("injected-context overhead", None, "telemetry file absent"))
    else:
        t = json.loads(tel.read_text())
        first = t["observations"][0]["tokens"]
        out.append(("overhead min", f"${min(first.values()):,}$".replace(",", "{,}"),
                    "identical 8-token probe"))
        out.append(("overhead max", f"${max(first.values()):,}$".replace(",", "{,}"),
                    "identical 8-token probe"))
        spread = max(first.values()) - min(first.values())
        out.append(("overhead spread", f"${spread:,}$".replace(",", "{,}"),
                    "aliased with the model factor"))
        if len(t["observations"]) > 1:
            second = t["observations"][1]["tokens"]
            deltas = [second[m] - first[m] for m in second if m in first]
            if deltas:
                approx = round(sum(deltas) / len(deltas))
                out.append(("overhead drift (mean)", f"${approx:,}$".replace(",", "{,}"),
                            "same-day re-probe"))

    # Placebo-section figures, from the PRIMARY panel. These are the numbers the
    # manuscript now argues from, replacing the pilot panel's.
    #
    # PROVENANCE, because the label is easy to over-read. Every figure below is
    # computed from the SCREEN arm alone -- `ol` is screen_bankA_ol.jsonl. The
    # manuscript introduces them with "On the panel used for the results
    # reported here", which a reader may take to span the placebo and hinted
    # arms too. It does not: those arms use a different, longer prompt, and
    # section 4.28 records minimax truncating on it at a budget the screen never
    # exhausted. When the placebo and solve arms exist, either recompute these
    # across all arms or qualify the sentence to name the arm -- do not leave a
    # screen-only rate standing as a study-wide one.
    if ol is not None:
        import analyse as _A
        d = _A.decline_breakdown(ol)
        if d.get("instrumented"):
            out.append(("primary: parseable rate", f"{100 * d['parsed_fraction']:.1f}\\%",
                        "placebo section"))
            out.append(("primary: decline rate", f"{100 * d['decline_fraction']:.1f}\\%",
                        "placebo section"))
            out.append(("primary: transport failures", f"{100 * d['transport_failed_fraction']:.1f}\\%",
                        "placebo section"))
        r_ol = _corr([r["parsed_fraction"] for r in ol], [r["baseline"] for r in ol])
        out.append(("primary: corr(parsed, baseline)", f"{r_ol:.2f}", "placebo section"))

    # Solver table, primary panel. Each row is checked, plus the all-candidates
    # column that carries the screen-validity argument (at/below chance on
    # retained items, far above it on the full set).
    if ol is not None:
        ol_ret = [r for r in ol if r["passes_headroom"]]
        ol_solvers = list(ol[0]["per_solver"].keys())

        def _t(rows, sv, f):
            return sum(r["per_solver"][sv][f] for r in rows)

        for sv in ol_solvers:
            short = sv.split("/")[-1].replace(":cloud", "").replace(":", "-")
            pr, ca = _t(ol_ret, sv, "parsed"), _t(ol_ret, sv, "calls")
            hi = _t(ol_ret, sv, "hits")
            out.append((f"{short}: parseable", f"{pr / ca:.2f}", "solver table"))
            out.append((f"{short}: cond. acc (retained)", f"{hi / pr:.3f}", "solver table"))
            pa, ha = _t(ol, sv, "parsed"), _t(ol, sv, "hits")
            out.append((f"{short}: cond. acc (all 40)", f"{ha / pa:.3f}", "solver table"))
            # The screen-validity claim, asserted rather than printed: every
            # solver must be at/below chance on retained items and above it on
            # the full candidate set. If this ever fails the screen is not doing
            # what the manuscript says it does.
            chance = ol_ret[0]["chance"] if ol_ret else 1 / 3
            assert hi / pr <= chance + 0.01, (
                f"{sv} scores {hi / pr:.3f} on RETAINED items, above chance "
                f"{chance:.3f}. The manuscript claims every solver sits at or "
                f"below chance there.")
            assert ha / pa > chance, (
                f"{sv} scores {ha / pa:.3f} over all candidates, not above "
                f"chance {chance:.3f}. The screen-validity argument fails.")

    # Bank sizes.
    for b, field in (("a", None), ("b", "n_candidates")):
        # Resolve through run._bank_path so a future bank id with a
        # non-default filename (e.g. `bs` -> bank_b_sample.json) cannot
        # silently miss here and report a PENDING that is really a bug.
        import run as _R
        f = _R._bank_path(b)
        if not f.exists():
            out.append((f"bank {b.upper()} size", None, f"{f.name} absent"))
            continue
        d = json.loads(f.read_text())
        mcq = [i for i in d["items"] if i.get("type") == "mcq"]
        if b == "a":
            out.append(("bank A total items", str(len(d["items"])), "manuscript"))
            out.append(("bank A mcq", str(len(mcq)), "manuscript"))
            out.append(("bank A short-answer", str(len(d["items"]) - len(mcq)), "manuscript"))
        else:
            out.append(("bank B candidates", str(d.get("n_candidates")), "manuscript"))
            out.append(("bank B retained", str(d.get("n_retained")), "manuscript"))

    # The realised call counts, and the Bank B sample screen.
    #
    # BANK A'S RETENTION, BASELINE RANGES AND INVARIANCE BAND ARE ALREADY
    # CHECKED ABOVE AND ARE NOT RECOMPUTED HERE. A first version of this block
    # emitted all of them a second time from its own loop -- two checks deriving
    # one number by two routes, which is the drift this file exists to detect,
    # appearing inside the detector. `ol` is None above when the screen is a
    # prefix, so it doubles as the completeness predicate; reuse it rather than
    # writing a second one.
    if ol:
        a_calls = sum(v["calls"] for r in ol for v in r["per_solver"].values())
        a_failed = sum(v["failed"] for r in ol for v in r["per_solver"].values())
        out.append(("bank A: solver calls", str(a_calls), "results screening subsection"))
        out.append(("bank A: transport failures", str(a_failed), "results"))
        out.append(("bank A: transport failure %",
                    f"{a_failed / a_calls * 100:.2f}", "results, two decimals"))
        out.append(("bank A: retained baseline median",
                    f"{st.median([r['baseline'] for r in ol if r['passes_headroom']]):.3f}",
                    "results"))

    bs = _load("screen_bankBS_ol.jsonl")
    if not bs:
        out.append(("bank B sample screen", None, "screen_bankBS_ol.jsonl absent"))
    else:
        bs_ret = [r for r in bs if r["passes_headroom"]]
        bs_rej = [r for r in bs if not r["passes_headroom"]]
        # The Bank B screen is a PREFIX BY DESIGN -- 44 of the 60 drawn, stopped
        # when the retention target was met -- so unlike Bank A it is not checked
        # against a full-bank row count. What keeps the prose honest is the
        # screened count itself, emitted as a literal: if the stage is resumed
        # and the file grows past 44, this check goes MISSING and the sentence
        # that says "44 of the 60 drawn" gets revisited. A partition needs both
        # groups, though, so a screen too short to have one reports PENDING
        # rather than computing min() of an empty set.
        out.append(("bank B sample: screened", str(len(bs)), "results, 44 of 60 drawn"))
        if not bs_ret or not bs_rej:
            out.append(("bank B sample: partition", None,
                        f"screen has {len(bs_ret)} retained and {len(bs_rej)} "
                        f"discarded; no partition to report yet"))
        else:
            calls = sum(v["calls"] for r in bs for v in r["per_solver"].values())
            parsed = sum(v["parsed"] for r in bs for v in r["per_solver"].values())
            failed = sum(v["failed"] for r in bs for v in r["per_solver"].values())
            lo, hi = S_wilson(len(bs_ret), len(bs))
            kept = [r["baseline"] for r in bs_ret]
            rej = [r["baseline"] for r in bs_rej]
            per_item = (bs[0].get("k_per_solver", 20)
                        * max(1, len(bs[0].get("per_solver") or {})))
            out.append(("bank B sample: retained", str(len(bs_ret)), "results"))
            out.append(("bank B sample: discarded", str(len(bs_rej)), "results"))
            out.append(("bank B sample: retention %",
                        f"{len(bs_ret) / len(bs) * 100:.1f}", "results"))
            out.append(("bank B sample: retention CI low", f"{lo:.2f}", "Wilson"))
            out.append(("bank B sample: retention CI high", f"{hi:.2f}", "Wilson"))
            out.append(("bank B sample: solver calls", str(calls), "results"))
            out.append(("bank B sample: parse %", f"{parsed / calls * 100:.1f}", "results"))
            out.append(("bank B sample: decline %",
                        f"{(calls - parsed - failed) / calls * 100:.1f}", "results"))
            out.append(("bank B sample: retained baseline median",
                        f"{st.median(kept):.3f}", "results"))
            out.append(("bank B sample: transport failures", str(failed), "results"))
            out.append(("bank B sample: retained baseline min", f"{min(kept):.3f}", "results"))
            out.append(("bank B sample: retained baseline max", f"{max(kept):.3f}", "results"))
            out.append(("bank B sample: discarded baseline min", f"{min(rej):.3f}", "results"))
            out.append(("bank B sample: band in solver calls",
                        f"{(min(rej) - max(kept)) * per_item:.0f}", "results"))
            # The claim the band supports, on both banks: nothing lies inside it.
            assert max(kept) < min(rej), (
                "a Bank B item's baseline sits inside the invariance band; the "
                "manuscript claims the partition is strict on both banks.")

    # The placebo floor. Complete: 14 retained Bank A items x 4 utterances.
    #
    # Every figure the placebo subsection quotes is recomputed here, INCLUDING
    # the per-item extremes. The pooled mean is the number a summary would keep,
    # and it is the one number that does not carry the finding: +0.048 pooled
    # against a per-item range of -0.144 to +0.500 (spec 4.37).
    plc = _load("placebo_bankA_ol.jsonl")
    scr_ol = _load("screen_bankA_ol.jsonl")
    if not plc or not scr_ol:
        out.append(("placebo floor", None, "placebo_bankA_ol.jsonl absent"))
    else:
        base = {r["item_id"]: r for r in scr_ol}
        pcalls = sum(v["calls"] for r in plc for v in r["per_solver"].values())
        pparsed = sum(v["parsed"] for r in plc for v in r["per_solver"].values())
        pfailed = sum(v["failed"] for r in plc for v in r["per_solver"].values())
        out.append(("placebo items", str(len(plc)), "retained Bank A"))
        out.append(("placebo calls", str(pcalls), "placebo subsection"))
        out.append(("placebo parse rate", f"{pparsed / pcalls * 100:.1f}", "per cent"))
        assert pfailed == 0, f"{pfailed} placebo transport failures; the text says none"

        pooled = [r["placebo_hinted"] for r in plc]
        m, lo, hi = A_mean_ci(pooled)
        out.append(("placebo pooled mean", f"{m:.3f}", "placebo subsection"))
        out.append(("placebo pooled CI low", f"{lo:.3f}", "placebo subsection"))
        out.append(("placebo pooled CI high", f"{hi:.3f}", "placebo subsection"))

        diffs = {r["item_id"]: r["placebo_hinted"] - base[r["item_id"]]["baseline"]
                 for r in plc if r["item_id"] in base}
        dm, dlo, dhi = A_mean_ci(list(diffs.values()))
        out.append(("placebo minus baseline, mean", f"{dm:.3f}", "placebo subsection"))
        out.append(("placebo minus baseline, CI low", f"{dlo:.3f}", "placebo subsection"))
        out.append(("placebo minus baseline, CI high", f"{dhi:.3f}", "placebo subsection"))
        out.append(("placebo minus baseline, min", f"{min(diffs.values()):.3f}", "per item"))
        out.append(("placebo minus baseline, max", f"{max(diffs.values()):.3f}", "per item"))

        for sv in sorted({sv for r in plc for sv in r["per_solver"]}):
            short = sv.split("/")[-1].replace(":cloud", "").replace(":", "-")
            h = sum(r["per_solver"][sv]["hits"] for r in plc if sv in r["per_solver"])
            pa = sum(r["per_solver"][sv]["parsed"] for r in plc if sv in r["per_solver"])
            slo, shi = S_wilson(h, pa)
            out.append((f"placebo {short}", f"{h / pa:.3f}", "per-solver floor"))
            out.append((f"placebo {short} CI low", f"{slo:.3f}", "Wilson"))
            out.append((f"placebo {short} CI high", f"{shi:.3f}", "Wilson"))

        # The extreme item, quoted verbatim in the manuscript. Named rather than
        # recomputed as "the max", so that a change of which item is extreme
        # shows up as a MISSING check instead of silently renaming the example.
        worst = max(diffs, key=diffs.get)
        assert worst == "A-L09-01", (
            f"the largest placebo excess is now {worst}, not A-L09-01; the "
            f"placebo subsection names the item explicitly.")
        w_p = next(r for r in plc if r["item_id"] == worst)
        out.append(("A-L09-01 baseline", f"{base[worst]['baseline']:.3f}", "placebo subsection"))
        out.append(("A-L09-01 placebo", f"{w_p['placebo_hinted']:.3f}", "placebo subsection"))
        g = "ol/gemma4:cloud"
        out.append(("A-L09-01 gemma4 placebo hits",
                    f"{w_p['per_solver'][g]['hits']}", "9 of 12"))
        assert base[worst]["per_solver"][g]["hits"] == 0, (
            "the text says gemma4 identified the key on 0 of 20 masked calls")
        # Both arms fully parseable is the whole point of the example: it rules
        # out willingness as the explanation. If either arm ever stops being
        # 100% parseable the sentence is wrong.
        assert (w_p["per_solver"][g]["parsed"] == w_p["per_solver"][g]["calls"]
                and base[worst]["per_solver"][g]["parsed"]
                == base[worst]["per_solver"][g]["calls"]), (
            "A-L09-01 gemma4 is not 100% parseable in both arms; the manuscript "
            "uses that fact to exclude willingness as the explanation.")

    # Guardrail activation, from the COMPLETE generate stage. These are the only
    # Results numbers available before `solve` finishes, because they describe
    # what the generator and verifier did rather than what a solver could infer.
    #
    # USABLE ROWS, NEVER `wc -l`. The file keeps its superseded ok=False rows as
    # an audit trail (spec 4.39): 2,195 lines, 2,016 measurements.
    gen = _load("generate_bankA_ol.jsonl")
    gen = [g for g in (gen or []) if g.get("ok") and g.get("response")]
    if not gen:
        out.append(("guardrail activation", None, "generate_bankA_ol.jsonl absent"))
    else:
        for reg in ("P2", "P3"):
            rows = [g for g in gen if g["regime"] == reg]
            blocked = sum(bool(g.get("blocked")) for g in rows)
            rej_cells = sum(1 for g in rows if g.get("verifier_rejections"))
            rej = sum(g.get("verifier_rejections") or 0 for g in rows)
            passed = sum(1 for g in rows if g.get("verifier_rejections")
                         and not g.get("blocked"))
            lo, hi = S_wilson(blocked, len(rows))
            out.append((f"{reg} cells", str(len(rows)), "generate, usable rows"))
            out.append((f"{reg} blocked", str(blocked), "guardrail subsection"))
            out.append((f"{reg} blocked rate", f"{blocked / len(rows) * 100:.1f}",
                        "guardrail subsection, per cent"))
            out.append((f"{reg} blocked CI low", f"{lo * 100:.1f}", "Wilson"))
            out.append((f"{reg} blocked CI high", f"{hi * 100:.1f}", "Wilson"))
            if reg == "P3":
                plo, phi = S_wilson(passed, rej_cells)
                out.append(("P3 cells rejected at least once", str(rej_cells), "manuscript"))
                out.append(("P3 total rejections", str(rej), "manuscript"))
                out.append(("P3 repaired and passed", str(passed), "manuscript"))
                out.append(("P3 recovery rate", f"{passed / rej_cells * 100:.1f}",
                            "per cent of rejected cells"))
                out.append(("P3 recovery CI low", f"{plo * 100:.1f}", "Wilson"))
                out.append(("P3 recovery CI high", f"{phi * 100:.1f}", "Wilson"))
                # The manuscript says the residual blocks are a repair-BUDGET
                # limit, not a separate failure mode: every blocked P3 cell
                # should have exhausted `max_repairs=2`, i.e. three rejections.
                # If that ever stops holding the sentence is wrong, so assert it
                # here rather than trusting the prose.
                exhausted = sum(1 for g in rows if g.get("blocked")
                                and (g.get("verifier_rejections") or 0) >= 3)
                assert exhausted == blocked, (
                    f"{blocked} P3 cells blocked but {exhausted} exhausted the "
                    f"repair budget; the manuscript claims they are the same set.")
            # P0/P1 carry no verifier at all, so their zero is structural.
            assert all(not g.get("verifier_rejections") for g in gen
                       if g["regime"] in ("P0", "P1")), (
                "a P0/P1 cell recorded a verifier rejection; those regimes have "
                "no verifier and the manuscript says so.")

    # --- the assembled Results, read from results.py's own output ---------
    #
    # NOT RECOMPUTED HERE. Every other block in this file derives its numbers
    # from `data/runs/*.jsonl`, which is right for counts. The inferential
    # numbers are different: recomputing a Holm-adjusted contrast in a second
    # implementation is how two estimators come to disagree while both look
    # plausible (spec 4.33), and the whole point of `results.py` is that it is
    # the one implementation. So this block checks the manuscript against the
    # ASSEMBLED artefact, and separately asserts that the artefact came from the
    # data now on disk by comparing the recorded input hash.
    res_p = _C.ROOT / "data" / "results_bankA_ol.json"
    if res_p.exists():
        res = json.loads(res_p.read_text())
        solve_p = _C.ROOT / "data" / "runs" / "solve_bankA_ol.jsonl"
        want = hashlib.sha256(solve_p.read_bytes()).hexdigest()
        got = (res.get("inputs") or {}).get("solve_bankA_ol.jsonl")
        assert got == want, (
            f"results_bankA_ol.json was assembled from a different "
            f"solve file (recorded {str(got)[:12]}, on disk {want[:12]}). "
            f"Re-run `python3 results.py --bank A --roster ol`.")
        assert res.get("complete") is True, (
            "results_bankA_ol.json is stamped incomplete; the manuscript quotes "
            "it as a finished analysis.")

        d = res["descriptives"]["leak_adj"]
        # THE HEADLINE NUMBER, quoted in the abstract, the prevalence subsection
        # and the conclusion. It was the one figure in the abstract that no check
        # computed -- and an abstract is the part a reader trusts without looking.
        grand = sum(v["mean"] * v["n"] for v in d["regime"].values()) \
            / sum(v["n"] for v in d["regime"].values())
        out.append(("grand mean leak_adj", f"{grand:.3f}",
                    "weighted over regimes; abstract, prevalence, conclusion"))
        for reg, v in d["regime"].items():
            out.append((f"{reg} mean leak_adj", f"{v['mean']:.3f}", "results.py"))
            out.append((f"{reg} leak_adj CI low", f"{v['ci_low']:.3f}", "results.py"))
            out.append((f"{reg} leak_adj CI high", f"{v['ci_high']:.3f}", "results.py"))
        for mdl, v in d["model"].items():
            out.append((f"{mdl.split('/')[-1]} mean leak_adj",
                        f"{v['mean']:.3f}", "results.py"))
        # The solicitation means bound the R3 null ("a range narrower than the
        # width of any one of their intervals"), and pinning them also resolves a
        # literal COLLISION: S0's mean rounds to 0.611, which is a superseded
        # gemma4 accuracy in the stale list below. The documented rule is that a
        # superseded literal is stale only if no current check computes it -- so
        # the collision is resolved by pinning the current quantity, never by
        # deleting the stale entry (spec 4.46).
        sol = d["solicitation"]
        for k, v in sol.items():
            out.append((f"{k} mean leak_adj", f"{v['mean']:.3f}", "results.py"))
        lo = min(v["mean"] for v in sol.values())
        hi = max(v["mean"] for v in sol.values())
        widest = max(v["ci_high"] - v["ci_low"] for v in sol.values())
        assert (hi - lo) < widest, (
            f"the solicitation means span {hi - lo:.4f}, which is no longer "
            f"narrower than the widest interval ({widest:.4f}); the manuscript "
            f"says it is.")
        for fam in ("R2_mitigation", "R3_solicitation"):
            for name, c in res["families"][fam]["contrasts"].items():
                out.append((f"{name} estimate", f"{c['estimate']:.3f}", fam))
                out.append((f"{name} se", f"{c['se']:.3f}", fam))
        # The covariate decision is the reason those contrasts read as they do,
        # so its diagnostic is pinned too -- a reader must be able to check the
        # exclusion, and an unpinned justification drifts like any other number.
        diag = res["covariate_decision"]["diagnostics"]["prompt_tokens"]
        assert diag["post_treatment"] is True, (
            "prompt_tokens is no longer flagged post-treatment, but the "
            "manuscript's Analysis subsection says it is and reports the "
            "primary fit without it.")
        out.append(("prompt_tokens partial R2", f"{diag['partial_r2']:.3f}",
                    "regime on the covariate, after item/model/solicitation"))
        # The sensitivity pair must stay reported, and must stay a SE story
        # rather than an estimate story -- that is the claim in the prose.
        sens = res["covariate_sensitivity"]["families"]["R2_mitigation"]["contrasts"]
        prim = res["families"]["R2_mitigation"]["contrasts"]
        for name in ("P1-P0", "P3-P0"):
            assert sens[name]["se"] > 5 * prim[name]["se"], (
                f"{name}: the adjusted fit's se is no longer inflated, so the "
                f"manuscript's explanation of the exclusion is wrong.")
            assert abs(sens[name]["estimate"] - prim[name]["estimate"]) < 0.05, (
                f"{name}: the two specifications now disagree on the ESTIMATE, "
                f"not just its precision; the prose says otherwise.")
        out.append(("adjusted P1-P0 se", f"{sens['P1-P0']['se']:.3f}",
                    "sensitivity fit, reported in Analysis"))

        # Panel agreement, per rater pair. The single pooled ICC covered only the
        # largest panel (spec 4.50), so each panel is pinned separately.
        panels = res["panel_agreement_by_panel"]["panels"]
        assert len(panels) == 3, (
            f"{len(panels)} rater panels in the results but the manuscript "
            f"describes three; vendor exclusion determines this count.")
        for key, v in panels.items():
            short = "+".join(k.split("/")[-1].split(":")[0] for k in key.split("+"))
            out.append((f"ICC {short}", f"{v['icc_absolute_agreement_2_1']:.3f}",
                        f"n={v['n_responses']}"))
            out.append((f"ICC {short} CI low", f"{v['ci_low']:.3f}", "bootstrap"))
            out.append((f"ICC {short} CI high", f"{v['ci_high']:.3f}", "bootstrap"))
        # THE TWO CHANNELS (spec 4.53). The Method promises conditional
        # accuracy "alongside the primary outcome throughout"; the pair with
        # parsed fraction is what tells hiding the answer apart from refusing to
        # answer, and a drop in the primary outcome means opposite things under
        # the two readings.
        ca = res["descriptives"]["hinted_cond_acc"]["regime"]
        pf = res["descriptives"]["parsed_fraction"]["regime"]
        for reg in sorted(ca):
            out.append((f"cond acc {reg}", f"${ca[reg]['mean']:.3f}$",
                        f"n={ca[reg]['n']}"))
            out.append((f"cond acc {reg} CI",
                        f"$[{ca[reg]['ci_low']:.3f}, {ca[reg]['ci_high']:.3f}]$",
                        "mean_ci"))
        # The manuscript's claim is that WILLINGNESS barely moves while
        # option-choice falls. Both halves are assertions about the data and
        # both can drift, so both are checked rather than quoted.
        assert pf["P0"]["mean"] > 0.999, (
            f"P0 parsed fraction is {pf['P0']['mean']:.4f}; the manuscript "
            f"states 1.000, and the two-channel argument starts from it.")
        mit = [pf[r]["mean"] for r in pf if r != "P0"]
        assert 0.9855 <= min(mit) and max(mit) <= 0.9905, (
            f"mitigated parsed fractions now span [{min(mit):.4f}, "
            f"{max(mit):.4f}]; the manuscript says 0.986 to 0.990.")
        assert ca["P0"]["mean"] - max(ca[r]["mean"] for r in ca if r != "P0") > 0.10, (
            "conditional accuracy no longer drops by more than 0.10 under "
            "mitigation; the claim that the option-choice channel is the one "
            "that moved rests on that gap being large beside the willingness one.")

        # THE OPTION-COUNT STRATA (spec 4.54). The Limitations rest a
        # pre-registered stratified contrast on two factual claims about the
        # banks; both are checked against the frozen files, because the
        # stratification is only "where the criterion is identical" if they hold.
        strat = res["bank_contrast_by_option_stratum"]
        assert strat["items_without_option_count"] == 0, (
            f"{strat['items_without_option_count']} analysed rows have no option "
            f"count, so a stratum is quietly smaller than the bank it describes.")
        assert set(strat["strata"]) == {"3"}, (
            f"Bank A now spans option strata {sorted(strat['strata'])}; the "
            f"manuscript states it is entirely three-option, and the "
            f"stratified bank contrast is built on that.")
        assert strat["strata"]["3"]["pre_registered_primary"] is True

        # THE PRE-REGISTERED CEILING EXCLUSION, ACTUALLY RUN (spec 4.53).
        cs = res["ceiling_sensitivity"]
        out.append(("ceiling cells", f"${cs['n_ceiling_cells']}$",
                    f"of {cs['n_cells']} item-by-solver cells"))
        out.append(("item-by-solver cells", f"${cs['n_cells']}$", "retained items"))
        for key, label in (("within_solver_leak", "within-solver leak"),
                           ("within_solver_adjusted_level", "within-solver level")):
            b = cs[key]
            out.append((f"{label} all", f"${b['all']['mean']:.3f}$",
                        f"n={b['all']['n']}"))
            out.append((f"{label} excl ceiling",
                        f"${b['excluding_ceiling']['mean']:.3f}$",
                        f"n={b['excluding_ceiling']['n']}"))
        # Censoring at the top can only shrink a within-solver estimate, so a
        # NEGATIVE shift would mean the ceiling cells are not what the paper
        # says they are -- and the "conservative by a bounded amount" reading
        # would be unsupported in the direction that matters.
        for key in ("within_solver_leak", "within_solver_adjusted_level"):
            assert cs[key]["shift"] >= 0, (
                f"{key}: excluding ceiling cells LOWERED the mean by "
                f"{-cs[key]['shift']:.4f}. Censoring can only shrink an "
                f"estimate, so either the ceiling definition or the estimator "
                f"has changed.")
        assert cs["n_ceiling_cells"] == 2 and cs["n_cells"] == 42, (
            f"{cs['n_ceiling_cells']} of {cs['n_cells']} cells at ceiling; the "
            f"Method states 2 of 42 and the sensitivity check must be run on "
            f"the same cells the count describes.")

        # THE PRE-REGISTERED FALLBACK (spec 4.52). A low ICC makes these the
        # estimates of record, so they are manuscript numbers, not appendix
        # numbers, and they drift like any other.
        psl = res["per_solver_levels"]
        assert psl["skipped"]["no_placebo_row"] == 0, (
            "per-solver levels skipped responses for want of a placebo row; the "
            "printed levels would cover less of the design than the pooled mean.")
        assert psl["n_solvers"] == 3, (
            f"{psl['n_solvers']} solvers carry a per-solver level but the "
            f"manuscript prints three.")
        for sv, v in psl["per_solver"].items():
            short = sv.split("/")[-1].replace(":cloud", "")
            out.append((f"per-solver level {short}", f"${v['mean']:.3f}$",
                        f"n={v['n']}"))
            out.append((f"per-solver level {short} CI",
                        f"$[{v['ci_low']:.3f}, {v['ci_high']:.3f}]$", "mean_ci"))
            out.append((f"per-solver level {short} n",
                        f"${v['n']:,}$".replace(",", "{,}"), "responses scored"))
        out.append(("per-solver level spread", f"${psl['spread']:.3f}$",
                    "max - min across solvers"))
        # The claim that carries the paragraph: the panel disputes the RANKING,
        # not the magnitude. If a per-solver level ever falls to the floor, the
        # pooled headline really is an average over a leaky and a clean
        # instrument, and both the Results and the abstract would be wrong.
        assert min(v["ci_low"] for v in psl["per_solver"].values()) > 0.55, (
            "a per-solver interval has dropped to or below 0.55; the manuscript "
            "says every interval lies above it, and the 'ranking not level' "
            "reading of the low ICC depends on that.")
        # The prose says consistency tracks absolute agreement within 0.02, which
        # is what rules out a fixed per-solver offset.
        for key, v in panels.items():
            assert abs(v["icc_absolute_agreement_2_1"]
                       - v["icc_consistency_3_1"]) < 0.02, (
                f"panel {key}: absolute and consistency ICC now differ by more "
                f"than 0.02, so a systematic offset IS present and the "
                f"manuscript's reading of the gap is wrong.")

    # ---- the generator completion ceiling (spec 4.56/4.58) -------------------
    # DISCLOSED BECAUSE IT WAS NOT CHOSEN. Every tutor reply before 2026-08-19
    # was generated at `backends.chat`'s 400 default. These checks pin the
    # figures the Method now reports, and the assertions pin the two claims that
    # make the disclosure honest rather than decorative: the affected cells are
    # the LONG ones (so the ceiling marked verbosity rather than censoring the
    # answer), and they concentrate in P0 (so the bias is conservative for R2).
    gena = _load("generate_bankA_ol.jsonl")
    slva = _load("solve_bankA_ol.jsonl")
    if gena and slva:
        cap = C.GENERATOR_BUDGET_BEFORE_MEASUREMENT["value"]
        cells = {r["cell_key"]: (
                    any((c.get("completion_tokens") or 0) >= cap
                        for c in (r.get("calls") or [])),
                    len(r.get("response") or ""), r.get("regime"))
                 for r in gena if r.get("ok")}
        hit = [k for k, v in cells.items() if v[0]]
        out.append(("cap-affected Bank A cells", f"${len(hit)}$",
                    f"of {len(cells):,} usable, at the {cap}-token ceiling"))
        ch_hit = st.mean(cells[k][1] for k in hit)
        ch_rest = st.mean(v[1] for v in cells.values() if not v[0])
        out.append(("cap-affected mean chars", f"${ch_hit:.0f}$", "truncated cells"))
        out.append(("unaffected mean chars", f"${ch_rest:.0f}$", "all other cells"))
        # THE DIRECTION IS THE FINDING. If truncated replies were the SHORT ones
        # the ceiling would be censoring answers out of replies, the bias would
        # run the other way, and "conservative" would be false.
        assert ch_hit > ch_rest, (
            "truncated replies are no longer the long ones, so the Method's "
            "reading of the ceiling -- that it marked verbose replies rather "
            "than removing the answer from them -- is now wrong.")
        n_p0 = sum(1 for k in hit if cells[k][2] == "P0")
        out.append(("cap-affected P0 cells", f"${n_p0}$",
                    "of the affected cells, in the R2 reference regime"))
        n_p3 = sum(1 for k in hit if cells[k][2] == "P3")
        out.append(("cap-affected P3 cells", f"${n_p3}$",
                    "of the affected cells, in the fully mitigated regime"))
        # THIS ASSERTION USED TO CARRY A DIRECTIONAL CLAIM, AND THE CLAIM WAS
        # WRONG. It read "the cap-affected cells no longer concentrate in P0, so
        # the claim that the resulting bias makes R2 CONSERVATIVE no longer
        # follows" -- inferring the SIGN of the displacement from WHERE the
        # affected cells sit. Measured by regenerating them (spec 4.60), the
        # affected P0 cells came back LOWER and the affected P3 cells much
        # higher, so both contrasts against P0 shrink and the published
        # estimates slightly OVERSTATE mitigation. The concentration is still a
        # fact the manuscript states, so it is still checked; what it is no
        # longer allowed to do is stand in for the measurement below.
        assert n_p0 * 2 > len(hit), (
            "the cap-affected cells no longer concentrate in P0, which the "
            "Results state as a fact about the affected subset.")
        la = {True: [], False: []}
        for r in slva:
            v, k = r.get("leak_adj"), r.get("cell_key")
            if v is not None and k in cells:
                la[cells[k][0]].append(v)
        out.append(("cap-affected leak_adj", f"${st.mean(la[True]):.3f}$",
                    "mean over affected cells"))
        out.append(("unaffected leak_adj", f"${st.mean(la[False]):.3f}$",
                    "mean elsewhere"))

    # ---- leave-one-item-out influence (spec 4.62) -------------------------------
    # AT 14 ITEMS A SINGLE ITEM IS A FOURTEENTH OF THE DESIGN, so "does one item
    # carry this conclusion?" is a question the Results must answer rather than
    # leave to a referee. Read from `results.py`'s canonical output -- never
    # recomputed here, which would be a second estimator (spec 4.33).
    rp = ROOT / "data" / "results_bankA_ol.json"
    if rp.exists():
        ii = (json.loads(rp.read_text(encoding="utf-8")) or {}).get(
            "item_influence") or {}
        r2 = (ii.get("families") or {}).get("R2_mitigation") or {}
        if r2:
            out.append(("item count under fixed effects", f"${ii['n_items']}$",
                        "Bank A items, the deletion denominator"))
            for k in ("P1-P0", "P3-P0", "P3-P2"):
                lo, hi = r2[k]["estimate_range"]
                out.append((f"{k} deletion range",
                            f"$[{lo:+.3f}, {hi:+.3f}]$",
                            "over all single-item deletions"))
            out.append(("P2-P1 faintest", f"${r2['P2-P1']['faintest_estimate']:+.3f}$",
                        "worst single-item deletion"))
            out.append(("P1-P0 effect retained",
                        f"${r2['P1-P0']['retained_fraction_of_effect'] * 100:.0f}\\%$",
                        "of magnitude, worst deletion"))
            n_flip = len(r2["P2-P1"]["verdict_flipping_items"])
            out.append(("marginal contrasts flipping items", f"${n_flip}$",
                        "of the deletions, for P2-P1 and P3-P2"))
            # LOAD-BEARING, BOTH DIRECTIONS.
            # The Results report the SIGN of each R2 contrast as the finding and
            # its magnitude as provisional; that split is only honest while the
            # signs actually hold.
            for k, v in r2.items():
                assert v["sign_stable"], (
                    f"{k} changes sign under a single-item deletion, so the "
                    f"Results' claim that the direction of each R2 contrast is "
                    f"robust while its magnitude is provisional no longer holds.")
            # And the two P0 contrasts are reported as estimates rather than
            # directions, which requires their verdicts to survive deletion.
            for k in ("P1-P0", "P3-P0"):
                assert not r2[k]["verdict_flipping_items"], (
                    f"{k} now loses its verdict under deletion of "
                    f"{r2[k]['verdict_flipping_items']}; the Results read it as "
                    f"an estimate, not merely a direction.")

    # ---- the measured cap sensitivity (spec 4.60) ------------------------------
    # THE PAIRED CELL MEAN DOES NOT SETTLE A CLAIM ABOUT THE CONTRASTS, so both
    # are checked: the paired difference over the 127 regenerated cells, and the
    # re-estimation of every pre-registered contrast with those cells substituted
    # into the analysis frame. Read from `cap_sensitivity`'s own artefact rather
    # than recomputed here -- a second implementation of the analysis rule is
    # what spec 4.33 is about.
    cc = ROOT / "data" / "capcheck_contrasts_bankA_ol.json"
    capr = _load("capcheck_solve_bankA_ol.jsonl")
    if cc.exists() and capr and slva:
        cs = json.loads(cc.read_text(encoding="utf-8"))
        base = {r["cell_key"]: r["leak_adj"] for r in slva
                if r.get("leak_adj") is not None}
        pairs, byreg = [], {}
        for r in capr:
            k, v = r.get("cell_key"), r.get("leak_adj")
            if v is None or k not in base:
                continue
            d = v - base[k]
            pairs.append(d)
            byreg.setdefault(r.get("regime"), []).append(d)
        m = st.mean(pairs)
        se = st.stdev(pairs) / len(pairs) ** 0.5
        out.append(("capcheck cells", f"${len(pairs)}$", "regenerated and re-solved"))
        out.append(("capcheck paired difference", f"${m:+.3f}$",
                    "uncapped minus capped adjusted leakage"))
        out.append(("capcheck paired se", f"${se:.3f}$", "paired standard error"))
        # ONE LITERAL, BECAUSE THE PAPER WRITES ONE. The interval is typeset as a
        # single math group, `$[-0.004, +0.051]$`, so checking the two bounds
        # separately looks for `$-0.004$` and `$+0.051$` -- literals that do not
        # and should not appear. Matching what is actually typeset, not loosening.
        out.append(("capcheck 95% CI",
                    f"$[{m - 1.96 * se:+.3f}, {m + 1.96 * se:+.3f}]$",
                    "paired difference interval"))
        out.append(("capcheck max shift", f"${cs['max_shift_in_se']:.2f}$",
                    "largest contrast displacement, in standard errors"))
        r2 = cs["families"]["R2_mitigation"]
        for k in ("P1-P0", "P3-P0"):
            out.append((f"capcheck {k} uncapped", f"${r2[k]['uncapped']:+.3f}$",
                        f"re-estimated ({r2[k]['published']:+.3f} as published)"))
        # LOAD-BEARING. The manuscript discloses the ceiling INSTEAD of re-running
        # the bank, and that choice is licensed by exactly one thing: no
        # pre-registered contrast changes its verdict. If one ever does, the
        # published estimate is an artefact of an unmeasured library default and
        # disclosure is not an adequate response to it.
        assert not cs["verdict_flips"], (
            f"the 400-token ceiling flips {cs['verdict_flips']}, so the Results' "
            f"decision to disclose it rather than re-run Bank A is no longer "
            f"defensible.")
        # AND THE DIRECTION, which the Results now state explicitly after the
        # cell-count argument for it turned out to be backwards.
        assert st.mean(byreg["P0"]) < 0 < st.mean(byreg["P3"]), (
            "the Results state that the affected P0 cells returned LOWER "
            "adjusted leakage on regeneration and the affected P3 cells higher; "
            f"measured now, P0 {st.mean(byreg['P0']):+.4f} and P3 "
            f"{st.mean(byreg['P3']):+.4f}.")

    # ---- the cost of extending to Bank B --------------------------------------
    # A VAGUE MULTIPLIER IS NOT A CHECKABLE CLAIM. The Results said extending the
    # design to Bank B "would have cost roughly three times the solver budget the
    # study has consumed in total". Nothing computed it, so nothing caught that it
    # was inherited from a plan to screen ALL of Bank B (~130 retained items) and
    # never recomputed for the 27-item sample, where the true ratio is about 1.25.
    # The decision to omit Bank B rested on that number. Stated as counts now, so
    # it is verifiable rather than merely plausible.
    solver_calls = 0
    for name in ("screen_bankA_ol.jsonl", "placebo_bankA_ol.jsonl",
                 "solve_bankA_ol.jsonl", "screen_bankBS_ol.jsonl",
                 # Added 2026-08-23 when Bank B's response stage completed. The
                 # description said "both banks" while the list omitted this
                 # file, so the total was right for the study as it stood and
                 # wrong for the study as described -- a check can be stale in
                 # its SCOPE without any number being wrong.
                 "placebo_bankBS_ol.jsonl", "solve_bankBS_ol.jsonl"):
        rows = _load(name) or []
        for r in rows:
            ps = r.get("per_solver")
            if isinstance(ps, dict):
                solver_calls += sum((v or {}).get("calls", 0) or 0
                                    for v in ps.values())
    if solver_calls:
        out.append(("solver calls consumed",
                    f"${solver_calls:,}$".replace(",", "{,}"),
                    "screening + placebo + response stages, both banks"))
        slv = _load("solve_bankA_ol.jsonl") or []
        if slv:
            # Calls per response, taken from the realised data rather than from
            # the roster size -- vendor exclusion makes the panel a function of
            # the generator (spec 4.50), so |solvers| is not the multiplier.
            per_resp = sum(sum((v or {}).get("calls", 0) or 0
                               for v in (r.get("per_solver") or {}).values())
                           for r in slv) / len(slv)
            # RETIRED 2026-08-23: this used to price the Bank B extension as a
            # PROJECTION (n_retained x 48 cells x 3 reps x calls-per-response).
            # Bank B has now been run, so the projection is not a claim the
            # manuscript makes any more -- and leaving the check in place would
            # have been worse than useless: a superseded literal is flagged only
            # if NO CURRENT CHECK COMPUTES IT, so this check would have silently
            # neutered the `$18{,}612$` entry appended below. Replaced by the
            # REALISED count, which the R4 section now reports.
            bsolve = _load("solve_bankBS_ol.jsonl") or []
            if bsolve:
                bs_calls = sum(sum((v or {}).get("calls", 0) or 0
                                   for v in (r.get("per_solver") or {}).values())
                               for r in bsolve)
                out.append(("bank B solver calls",
                            f"${bs_calls:,}$".replace(",", "{,}"),
                            f"realised over {len(bsolve)} responses"))

    # ---- Bank B's own mitigation family, quoted in the R4 subsection ----------
    # These are here for their own sake AND because one of them collides with a
    # superseded literal: Bank B's P2-P1 adjusted p is 0.097, which is also
    # gemma4's pre-re-screen conditional accuracy. The superseded list is
    # literals, not quantities, so the ONLY thing that stops it flagging a
    # correct sentence is a current check computing the same string. Suppressing
    # the guard instead would have been the cheaper edit and the wrong one.
    bsres_p = ROOT / "data" / "results_bankBS_ol.json"
    if bsres_p.exists():
        bsres = json.loads(bsres_p.read_text(encoding="utf-8"))
        for name, c in (bsres["families"]["R2_mitigation"]["contrasts"]).items():
            out.append((f"bank BS {name} estimate", f"{c['estimate']:.3f}",
                        "R2 within Bank B"))
            out.append((f"bank BS {name} adj p", f"{c['p_adjusted']:.3f}",
                        "Holm-adjusted within R2"))

    # ---- the deposit's licence scan (Data availability) ------------------------
    # The manuscript states how much Bank B text the withheld logs carry. That
    # figure came from a hand-run scan that could not see a multi-line stem, so
    # it is now computed by the released script on every check.
    try:
        sys.path.insert(0, str(_C.ROOT / "harness" / "ops"))
        import release_scan as _rs
        logs = [ROOT / "data" / "runs" / "generate_bankBS_ol.jsonl",
                ROOT / "data" / "runs" /
                ("QUARANTINE_generate_bankBS_ol_generator_cap400_PARTIAL"
                 "_clean_rows_kept_2026-08-20_a61f4004.jsonl")]
        if all(p.exists() for p in logs):
            prot = _rs.protected_strings()
            union, total = set(), 0
            for f in logs:
                hay = _rs._norm(f.read_text(encoding="utf-8", errors="ignore"))
                hit = {x for x in prot if _rs._norm(x) in hay}
                union |= hit
                total += sum(hay.count(_rs._norm(x)) for x in hit)
            out.append(("withheld-log protected occurrences", f"${total}$",
                        f"{len(union)} distinct option strings"))
    except ImportError:
        out.append(("withheld-log protected occurrences", None,
                    "ops/release_scan.py not importable"))

    # ---- Tier-3 human validation (sec:results-human) ---------------------------
    # Read from the assembled artefact, never recomputed here (4.41). The
    # adjudication counterfactual is checked too: the manuscript says the one
    # re-read cell changes nothing, and a claim of that shape with nothing
    # computing it is exactly the literal that goes stale unnoticed (4.46).
    hv_p = ROOT / "data" / "human_validation_results.json"
    if not hv_p.exists():
        out.append(("human validation agreement", None,
                    "run `python3 human_validation.py assemble`"))
    else:
        hv = json.loads(hv_p.read_text(encoding="utf-8"))
        pa, gc = hv["percent_agreement"], hv["agreement_given_both_committed"]
        cons, eith = hv["recovered_consensus"], hv["recovered_either"]
        st_ = hv["structure"]
        out += [
            ("human validation n", f"${hv['n']}$", "sampled responses"),
            ("labels collected", f"${2 * hv['n']}$", "two annotators x n"),
            ("agreement count", f"${pa['k']}$", "responses both labelled alike"),
            ("percent agreement", f"${100 * pa['p']:.1f}\\%$", "of 240"),
            ("agreement CI", f"$[{pa['ci'][0]:.3f}, {pa['ci'][1]:.3f}]$",
             "Wilson"),
            ("cohen kappa", f"${hv['cohen_kappa']:.3f}$", "chance-corrected"),
            ("both committed", f"${gc['n']}$", "neither said cannot_tell"),
            ("same option", f"${gc['k']}$", "and agreed on which"),
            ("conditional agreement", f"${100 * gc['p']:.1f}\\%$",
             "given both committed"),
            ("conditional agreement CI",
             f"$[{gc['ci'][0]:.3f}, {gc['ci'][1]:.3f}]$", "Wilson"),
            ("disagreements", f"${hv['n'] - pa['k']}$", "240 minus agreements"),
            ("one-sided cannot_tell", f"${st_['exactly_one_cannot_tell']}$",
             "of those disagreements"),
            ("A1 cannot_tell", f"${hv['per_annotator']['A1']['cannot_tell']}$",
             "declined"),
            ("A2 cannot_tell", f"${hv['per_annotator']['A2']['cannot_tell']}$",
             "declined"),
            ("consensus recovered", f"${cons['k']}$", "both chose the key"),
            ("consensus rate", f"${100 * cons['p']:.1f}\\%$", "of 240"),
            ("consensus CI", f"$[{cons['ci'][0]:.3f}, {cons['ci'][1]:.3f}]$",
             "Wilson"),
            ("liberal recovered", f"${eith['k']}$", "either chose the key"),
            ("liberal rate", f"${100 * eith['p']:.1f}\\%$", "of 240"),
            ("liberal CI", f"$[{eith['ci'][0]:.3f}, {eith['ci'][1]:.3f}]$",
             "Wilson"),
        ]
        for t in ("0.1", "0.25", "0.5"):
            d = hv["by_threshold"][t]
            out += [
                (f"sensitivity at {t}", f"${d['sensitivity']:.3f}$", "vs consensus"),
                (f"specificity at {t}", f"${d['specificity']:.3f}$", "vs consensus"),
            ]
        adj = hv.get("adjudication_sensitivity")
        if adj:
            out += [
                ("adjudicated cells", f"${adj['n_adjudicated']}$",
                 "read back by their annotator"),
                ("alt agreement",
                 f"${100 * adj['alt_percent_agreement']:.1f}\\%$",
                 "under the other reading"),
                ("alt kappa", f"${adj['alt_cohen_kappa']:.3f}$", "same"),
            ]

    # ---- family R4 and the option-count strata (pooled A + BS) ------------------
    # These report PENDING until `results.py --pool A,bs` has run, which is the
    # intended state while `solve bs` is in flight: the manuscript still carries
    # \TBD for R4, so there is nothing yet to be stale against.
    #
    # Read from the ASSEMBLED artefact, never recomputed here. A second
    # implementation of a Holm-adjusted contrast is how two estimators come to
    # disagree while both look plausible (spec 4.33).
    pool_p = ROOT / "data" / "results_pooled_A-BS_ol.json"
    if not pool_p.exists():
        out.append(("R4 joint bank x regime", None,
                    "run `python3 results.py --pool A,bs --roster ol`"))
        out.append(("option-count strata", None, "same pooled artefact"))
    else:
        pool = json.loads(pool_p.read_text(encoding="utf-8"))
        for stem in ("solve_bankA_ol.jsonl", "solve_bankBS_ol.jsonl"):
            sp = ROOT / "data" / "runs" / stem
            want = hashlib.sha256(sp.read_bytes()).hexdigest()
            got = (pool.get("inputs") or {}).get(stem)
            assert got == want, (
                f"results_pooled_A-BS_ol.json was assembled from a different "
                f"{stem} (recorded {str(got)[:12]}, on disk {want[:12]}). "
                f"Re-run `python3 results.py --pool A,bs --roster ol`.")
        assert pool.get("complete") is True, (
            "results_pooled_A-BS_ol.json is stamped incomplete; the manuscript "
            "quotes R4 as a finished analysis.")

        r4 = (pool.get("families") or {}).get("R4_robustness") or {}
        jt = r4.get("joint_test") or {}
        if jt:
            out.append(("R4 joint F", f"{jt['F']:.2f}", "bank x regime, one test"))
            out.append(("R4 df", f"{jt['df_num']}", "interaction dummies"))
            # The threshold is alpha/2 at the PRE-REGISTERED m, not alpha/1 at
            # the testable size: re-sizing after the data existed would loosen
            # the survivor, which this project never does.
            h = r4.get("holm") or {}
            if h:
                out.append(("R4 Holm threshold", f"{h['threshold']:.3f}",
                            "alpha/2 at the pre-registered m=2"))
        strat = (pool.get("bank_contrast_by_option_stratum") or {})
        blocks = strat.get("strata") or {}
        assert strat.get("items_without_option_count", 0) == 0, (
            f"{strat.get('items_without_option_count')} analysed rows have no "
            f"option count, so a stratum is quietly smaller than the bank it "
            f"describes.")
        # THE LIMITATIONS PROMISE HAS CONTENT ONLY ONCE BANK B ARRIVES: with
        # Bank A alone the paper occupies stratum 3 by itself, so "we report the
        # other strata separately" was vacuously true. Assert the strata really
        # did multiply rather than trusting the prose.
        # Expressed as a SET, not a count: "more than one" is a threshold and a
        # small integer cannot say which strata were expected. Bank A occupies
        # the three-option stratum alone, so the promise has content exactly
        # when the pooled strata are not that single set.
        assert set(blocks) != {"3"}, (
            f"pooled analysis spans only option strata {sorted(blocks)} -- the "
            f"same single stratum Bank A occupies. The Limitations promise to "
            f"report the other strata separately is still vacuous, so either "
            f"the pool is wrong or the promise is.")
        for n_opt in sorted(blocks, key=lambda k: int(k)):
            for bank, v in sorted((blocks[n_opt].get("by_bank") or {}).items()):
                out.append((f"stratum {n_opt}-option, bank {bank}: mean",
                            f"{v['mean']:.3f}",
                            f"n={v['n_rows']} rows"))

    # TIER-1 VERIFIER AUDIT. The verifier IS the P2/P3 treatment, so its error
    # rates belong to the regimes and not to the harness. The manuscript cited
    # this audit twice from the Results and described it nowhere until the
    # fourth review round -- a number with no provenance, which no check could
    # see because the check would have had to notice an ABSENCE.
    #
    # Recomputed here rather than read from a file: `verifier_audit` reads the
    # frozen banks, which are the artefact, and it is the same call the CLI
    # makes. A stale copy of these figures is exactly what the audit is for.
    for bank in ("A", "bs"):
        try:
            a = _verifier_audit(bank)
        except (Exception, SystemExit) as e:
            # SystemExit, not just Exception: `run.load_bank` reports a missing
            # bank with sys.exit, which inherits from BaseException and sails
            # straight through `except Exception`. Written the narrow way this
            # guard could not fire, and the public copy -- which withholds
            # Bank B by design -- went from 6 expected failures to 19.
            out.append((f"verifier audit, bank {bank}", None, f"unavailable: {e}"))
            continue
        out.append((f"verifier sensitivity, bank {bank}",
                    f"${a['sens_n']}/{a['sens_d']}$",
                    f"{a['sensitivity']:.1%} on the stress corpus"))
        out.append((f"verifier false positives, bank {bank}",
                    f"${a['fp_n']}/{a['fp_d']}$",
                    f"{a['fp_rate']:.1%} on answer-free text"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if not PAPER.exists():
        print(f"no manuscript at {PAPER}")
        return 1
    tex = PAPER.read_text(encoding="utf-8")

    # Highlights are submitted to the journal but live in LaTeX comments, so
    # they render nothing and the \TBD guard never sees them. Surface any that
    # still assert an unmeasured result, so they cannot reach a submission by
    # being invisible in the built PDF.
    unresolved = [l.split("TBD-RESULT:", 1)[1].strip()
                  for l in tex.splitlines()
                  if "TBD-RESULT:" in l and l.strip().startswith("%% HIGHLIGHT")]
    for h in unresolved:
        print(f"PENDING  {'highlight awaiting results':<44} ({h[:60]})")

    # THE ABSTRACT HAS A HARD LIMIT AND THE MARGIN IS ONE WORD.
    #
    # Nothing computed this until 2026-08-28, so it was recounted by hand every
    # time the abstract changed and the answers disagreed (see abstract_words).
    # A limit checked by whoever remembers to check it is not a limit.
    n_abs, abs_note = abstract_words(tex)
    if n_abs is None:
        print(f"PENDING  {'abstract word count':<44} ({abs_note})")
        pending_extra = 1
    else:
        pending_extra = 0
        slack = ABSTRACT_LIMIT - n_abs
        if slack < 0:
            print(f"OVERLONG {'abstract':<44} {n_abs} words, limit "
                  f"{ABSTRACT_LIMIT} ({abs_note})")
        elif slack <= 5:
            print(f"note     {'abstract':<44} {n_abs}/{ABSTRACT_LIMIT} words, "
                  f"{slack} to spare -- any addition displaces something")

    # SUPERSEDED LITERALS MUST BE ABSENT, not merely outvoted by a correct one.
    #
    # Every check below asks whether a value is PRESENT, which cannot see a
    # stale copy of the same quantity elsewhere in the file. On 2026-08-17 the
    # solver table was updated to the re-screened values and the sentence two
    # paragraphs above it still read "from 0.097 to 0.337" -- the checks for
    # 0.096 and 0.319 passed, because those literals were now in the table, and
    # the prose quoting the superseded pair went unflagged. The module docstring
    # calls this out as a known limitation ("drift, not misplacement"); this
    # closes the half of it that can be closed cheaply.
    #
    # When a reported quantity changes, add its OLD rendering here with the
    # reason. The list is append-only in practice: a value that was once wrong
    # in this manuscript stays wrong.
    superseded: list[tuple[str, str]] = [
        ("0.097", "gemma4 cond. acc before the 2026-08-17 re-screen; now 0.096"),
        ("0.337", "gpt-oss cond. acc before the re-screen; now 0.319"),
        ("0.276", "minimax cond. acc before the re-screen; now 0.296"),
        ("0.611", "gemma4 cond. acc all-40 before the re-screen; now 0.609"),
        ("0.698", "gpt-oss cond. acc all-40 before the re-screen; now 0.692"),
        ("96.3", "parseable rate before the re-screen; now 97.2"),
        ("$[0.00, 0.48]$", "retained baseline range before the re-screen"),
        ("one solver call wide", "invariance band before the re-screen; now three"),
        # The S1 removal (spec 4.43) re-sized the human-validation sample from
        # 4-per-cell of 64 cells to 5-per-cell of 48. The Method was corrected and
        # the check above pins 240 -- but the RESULTS scaffold still said 256 for a
        # day, because a present-value check cannot see a stale twin in another
        # section. That is the entire reason this list exists.
        ("256-response", "validation sample under the pre-S1-removal frame; now 240"),
        # Bank B's solve landed 2026-08-23 and the omission paragraph that
        # carried this went with it. NOTE what is NOT here: `$23{,}328$`, which
        # was the PROJECTED extension cost in that paragraph and is now the
        # REALISED Bank B solver-call count in the R4 section. Same literal,
        # opposite status -- listing it would flag a correct sentence, which is
        # how a checker teaches its reader to ignore it.
        ("$18{,}612$", "solver calls consumed before Bank B's solve; the "
                       "sentence quoting it was removed when Bank B landed"),
    ]
    # A SUPERSEDED VALUE CAN BE A CURRENT VALUE FOR SOMETHING ELSE, and this
    # list is literals rather than quantities, so it cannot tell them apart on
    # its own. First run: `0.698` flagged as gpt-oss's pre-re-screen all-40
    # accuracy while being, in the same table, minimax's correct one. Reporting
    # that would train the reader to ignore the check -- the failure mode of
    # every warning that cries wolf.
    #
    # So a superseded literal is only stale if no CURRENT check computes it.
    # `checks()` is the authority on what is currently right, so ask it rather
    # than maintaining a second list that would drift from the first.
    results = checks()
    current = {lit for _, lit, _ in results if lit}
    stale = [(lit, why) for lit, why in superseded
             if lit in tex and lit not in current]
    for lit, why in stale:
        print(f"STALE    {'superseded value still present':<44} "
              f"{lit!r} -- {why}")

    ok = missing = 0
    pending = len(unresolved) + pending_extra
    for label, literal, note in results:
        if literal is None:
            print(f"PENDING  {label:<44} ({note})")
            pending += 1
        elif literal in tex:
            ok += 1
            if args.verbose:
                print(f"ok       {label:<44} {literal}")
        else:
            print(f"MISSING  {label:<44} computed {literal!r} not found in paper.tex ({note})")
            missing += 1

    print(f"\n{ok} verified, {missing} missing, {pending} pending, {len(stale)} stale")
    if missing:
        print("A MISSING check means the manuscript and the data disagree, OR the\n"
              "sentence was reworded so the literal no longer appears. Both need a\n"
              "human decision -- do not 'fix' it by loosening the check.")
    if stale:
        print("A STALE hit means a value this manuscript has already corrected is\n"
              "still somewhere in the file. The correct value being present too is\n"
              "exactly the condition that hides it -- find the second occurrence.")
    over = n_abs is not None and n_abs > ABSTRACT_LIMIT
    if over:
        print("An OVERLONG abstract is a desk-reject at submission. The count is\n"
              "taken from the built PDF, so it is what the editor will see.")
    return 1 if (missing or stale or over) else 0


if __name__ == "__main__":
    sys.exit(main())
