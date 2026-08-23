"""Frozen study configuration and provenance for the answer-leakage study.

Everything that could drift between runs is pinned here and stamped into every
output file via `study_manifest()`. If you change a constant in this module you
have invalidated existing data; bump STUDY_VERSION and re-run, do not mix.

Design decisions this module encodes (see
docs/superpowers/specs/2026-08-15-ceai-answer-leakage-design.md):

- Generation runs at temperature 1.0, NOT 0. At temperature 0 there is no
  within-cell sampling variance, which makes binomial/Wilson inference invalid
  and would silently misreport precision. Judges and solvers run at 0.0: a
  stochastic measuring instrument adds variance for no benefit.
- Two generation paths are run deliberately (dual-path validation). The hosted
  router injects thousands of tokens of vendor agent context that we cannot
  disable and that varies BY MODEL and OVER TIME, so it is perfectly aliased
  with the model factor. The controlled path exists to bound that damage.
- Vendor identity is an explicit table keyed on the FULL model id. It is never
  derived by string-splitting: `kr/claude-haiku-4.5` and `gh/claude-haiku-4.5`
  are the same underlying model and must never judge each other.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

STUDY_VERSION = "2.3.0"  # 2.3: budgets re-measured PER BANK (spec 4.33). The 2.2 numbers were
                        #      calibrated on Bank A and did not transfer to Bank B, which
                        #      truncated 9 calls in 300 against Bank A's 1 in 2,400.
                        #      gpt-oss 2400->8800, minimax 8000->36000.
                        #      Bank A's 2.2 screen is NOT superseded: `truncation_slack` shows
                        #      no Bank A verdict is reachable by its own failed calls, so the
                        #      partition it reports is established and re-screening would
                        #      change no decision. Its file is wholly 2.2.0 and Bank B's will
                        #      be wholly 2.3.0, so no file mixes versions.
                        # 2.2: budgets re-measured on the HINTED prompt too (spec 4.28-4.29);
                        #      minimax 2000->8000. 2.1 screens truncated on some items and
                        #      are quarantined, not mixed.

# Fixed seed for every shuffle, sample and assignment in the study.
SEED = 20260815

ROOT = Path(__file__).resolve().parent.parent
BANKS_DIR = Path(__file__).resolve().parent / "banks"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DERIVED_DIR = DATA_DIR / "derived"

# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------

OMNIROUTE_BASE = "http://localhost:20128/api/v1"
OLLAMA_BASE = "http://localhost:11434"

PATH_HOSTED = "hosted"          # OmniRoute -> Kiro. Vendor context injected.
PATH_CONTROLLED = "controlled"  # Local runtime. System prompt printable + hashable.

# --------------------------------------------------------------------------
# Model roster
#
# Probed 2026-08-15. Of 391 advertised model ids, every non-`kr` provider
# failed (gh 400, gemini-cli 500, cerebras/oc/tllm 403, cf 410 Gone, ddgw
# 418/429/503, qwen-web 504) and 5 of 12 `kr` ids returned "Invalid model".
# The survivors are below. All sit behind ONE provider account, so
# "cross-vendor independence" is nominal and is disclosed in Limitations.
# --------------------------------------------------------------------------

VENDOR: dict[str, str] = {
    "kr/claude-haiku-4.5": "anthropic",
    "kr/claude-sonnet-4.5": "anthropic",
    "kr/deepseek-3.2": "deepseek",
    "kr/minimax-m2.5": "minimax",
    "kr/minimax-m2.1": "minimax",
    "kr/glm-5": "zhipu",
    "kr/qwen3-coder-next": "alibaba",
    # Aliases of the same underlying models on other providers. Present so the
    # vendor lookup stays correct if the roster is ever widened; a model must
    # never be able to judge itself because its prefix changed.
    "gh/claude-haiku-4.5": "anthropic",
    "gh/claude-sonnet-4.5": "anthropic",

    # --- Ollama cloud roster (PATH_CONTROLLED). Probed 2026-08-16. ---
    # Vendor is the family that TRAINED the model, not the host. gpt-oss is
    # OpenAI's open-weights release and nemotron is NVIDIA's; hosting both on
    # ollama.com does not make them one vendor, and treating it as such would
    # let a model judge its own family's output.
    "ol/gemma4:cloud": "google",
    "ol/gemma4:31b-cloud": "google",
    "ol/gpt-oss:120b-cloud": "openai",
    "ol/minimax-m3:cloud": "minimax",
    "ol/nemotron-3-ultra:cloud": "nvidia",
    "ol/nemotron-3-super:cloud": "nvidia",
}

#: Per-model completion budget. Reasoning models spend their whole allowance on
#: the reasoning trace and emit content only afterwards, so a budget that is too
#: small yields finish_reason="length" with empty content, which `backends.chat`
#: reports as attrition rather than letting it masquerade as a solver decline.
#:
#: THESE ARE SET FROM A MEASURED DISTRIBUTION, NOT GUESSED. Sampled 2026-08-16
#: over 18 masked-stem baseline calls per model at a 2000-token cap (zero
#: failures at that cap):
#:
#:     gpt-oss:120b   min 103  med 181  p90 760  max 1461
#:     minimax-m3     min  27  med 102  p90 447  max 1146
#:
#: The distribution is heavily right-skewed, and the earlier 600-token budget
#: cut it between the median and p90 -- losing 22% of gpt-oss's screen calls.
#:
#: WHY THAT WAS A BIAS AND NOT JUST ATTRITION. Reasoning length tracks how
#: ambiguous the masked item is: on an item whose options were True/False/0 the
#: model reasoned past 600 tokens on 4 of 6 calls, while on clear-cut items it
#: answered in ~100. Truncation therefore removes precisely the calls where the
#: solver found the item hard, and keeps the ones where the key was obvious
#: fast. Conditional on answering, baseline identifiability is biased UPWARD on
#: exactly the items the headroom screen exists to distinguish -- so items would
#: be discarded for being "answerable from options alone" when what actually
#: happened is that the hard calls were dropped.
#:
#: Budgets are therefore set above the observed MAX, not the p90. max_tokens is
#: a cap and not a reservation, so an unused allowance costs nothing; the only
#: price is latency on tail calls, which is the right trade against a biased
#: measurement.
#:
#: ONE BUDGET PER SOLVER, SIZED FOR THE LONGEST PROMPT IT WILL MEET (2026-08-17,
#: spec 4.28). The first calibration measured only the SCREEN prompt, and the
#: hinted prompt used by `placebo` and `solve` is longer. minimax-m3's observed
#: max went 1146 -> 4644 on that change alone and truncated 6 of 16 probe calls
#: at the old 2000 budget -- which would have DEPRESSED the placebo arm, and the
#: placebo is subtracted, so it would have INFLATED the primary outcome.
#:
#: The table stays one-dimensional deliberately. `solver_max_tokens` is called
#: from exactly one place (`solvability._run`), so a per-(solver, prompt) table
#: would be a second copy of a rule that already has one home -- the shape of
#: every drift defect in spec 4.8-4.20. A single generous number costs nothing
#: on the shorter arm because the budget is a cap.
#:
#: Findings behind these numbers, each of which contradicts an obvious guess:
#:   * It is SOLVER-specific, not a property of reasoning models. gpt-oss barely
#:     moved between arms (1461 -> 1541) while minimax quadrupled. Generalising
#:     from gpt-oss would have concluded no change was needed.
#:   * It does NOT track item ambiguity, which is what spec 4.13's mechanism
#:     would predict. A-L05-03 (baseline 0.483, exactly at the retention cut, so
#:     the most ambiguous retained item) is not the worst case, and A-L01-01
#:     (baseline 0.133) is among the worst.
#:   * It is NOT monotone in prompt length either, which was the next guess and
#:     is also wrong. Measured minimax maxima:
#:
#:         item        screen prompt     hinted prompt (longer)
#:         A-L01-01        2794              4644
#:         A-L05-03        2851               760
#:
#:     A-L05-03 reasons LONGER on the SHORTER prompt. So neither prompt family
#:     dominates, and an item's behaviour on one says little about the other.
#:
#: Because neither ambiguity nor prompt length predicts completion length, the
#: worst (item, prompt, solver) cell cannot be identified in advance and the
#: budget cannot be tuned to it. Set generously,
#: then VERIFY on the real run: `error_kinds` now records
#: `truncated_before_content` per record (spec 4.27), so residual truncation is
#: visible in the output rather than inferred.
#:
#: THE BUDGET IS ALSO BANK-SPECIFIC (spec 4.33, measured 2026-08-17). Everything
#: above was measured on Bank A and then reused on the Bank B sample, whose
#: items are longer real exam questions. Bank A truncated on 1 call in 2,400;
#: the Bank B pilot truncated on 9 in 300. Re-measure with
#: `measure_budgets.py --bank <id> --roster <r>` before screening a new bank.
#:
#: AND THE DISTRIBUTION IS HEAVY-TAILED, so "observed max" is a weaker statistic
#: than it looks. minimax on the Bank B sample, 48 calls: 44 finished under 800
#: tokens, then 1466, 4253, 6848, 7770, and one that exceeded a 16,000-token
#: probe ceiling. Re-probed at 40,000 on the offending item it returned 3441,
#: 11769, 712 -- so the traces DO terminate and a finite budget exists; the tail
#: is long, not unbounded. The item is `B-question5_3_1`, whose masked options
#: are ['t', 'c', 'Error, you cannot use the [ ] operator with a string.', 'h']:
#: with the stem hidden there is nothing to reason TO, and the trace runs until
#: the model gives up. That is the intended stimulus, not a defect, which is why
#: the answer is a bigger cap rather than a repaired item.
#:
#: A CAP COSTS NOTHING ON CALLS THAT DO NOT REACH IT. `max_tokens` is a ceiling,
#: not an allocation, so raising it is paid for only on the rare long trace --
#: exactly where the tokens are needed. Set from 3x the observed maximum, and
#: rely on `solvability.truncation_slack` to catch any residual truncation that
#: could actually reverse a verdict; chasing a heavy tail to zero truncation is
#: neither achievable nor necessary.
MAX_TOKENS_SOLVER: dict[str, int] = {
    "ol/gemma4:cloud": 16,          # not a reasoning model; 0 failures at 16
    "ol/gemma4:31b-cloud": 16,
    # Bank B sample, 2026-08-17, 48 calls each at a 16,000-token probe ceiling
    # (`data/runs/budget_BS_ol.jsonl`), set at 3x the largest TERMINATING trace:
    #
    # 13236 SET 2026-08-19 from the Bank B PLACEBO arm: 48 calls, max 4412, p95
    # 3977, median 902 (`budget_BS_ol_placebo.jsonl`). The previous 8800 came
    # from Bank B's BASELINE arm (max 2933) and was still comfortably above the
    # placebo arm's observed maximum -- no call in the measurement would have
    # truncated -- but it sat at 2.0x where the standing rule is 3x, and Bank A
    # shipped at 1.64x and truncated anyway (spec 4.28). Raising a ceiling costs
    # nothing on calls that do not reach it.
    #
    # BANK A'S COMPLETED RUNS USED 8800 AND ARE NOT INVALIDATED: their own arms
    # were measured (max 881 hinted, 1890 placebo) and cleared it by 4-9x. The
    # study version is deliberately NOT bumped -- see SUPERSEDED_BUDGETS.
    "ol/gpt-oss:120b-cloud": 13236,  # was 8800 (Bank B baseline), 2400 (below max)
    "ol/minimax-m3:cloud": 36000,   # max 11769 terminating; one call censored above
                                    # 16000, so residual truncation stays possible and
                                    # is caught by `solvability.truncation_slack`,
                                    # not by chasing the tail. Was 8000.
    "ol/nemotron-3-ultra:cloud": 3000,
    "ol/nemotron-3-super:cloud": 3000,
}

#: WHAT COMPLETED RUNS ACTUALLY USED, frozen per change of the table above.
#:
#: `MAX_TOKENS_SOLVER` is a live constant and this project has no git repo, so
#: editing it used to erase the only record of what finished data was collected
#: under. Rows written from 2026-08-19 carry `solver_max_tokens` themselves; rows
#: written before that date resolve through this table instead of through a
#: guess.
#:
#: THE STUDY VERSION IS NOT BUMPED WHEN A BUDGET RISES FOR A BANK THAT HAS NOT
#: RUN YET. §4.29's rule is that records measured under different budgets are not
#: comparable, and it is enforced per bank, where it belongs: every Bank A row
#: used 8800 and every Bank B row will use 13236, which is §4.33's "the budget is
#: a property of the (solver, bank, arm) triple" showing up in the data instead
#: of in a comment. Bumping would have been worse than useless -- it invalidates
#: every existing budget MEASUREMENT file (`_guard_arm_budget` compares versions),
#: so every stage would refuse until all of them were re-measured, and it would
#: make Bank A and Bank B unpoolable, which is exactly what family R4 needs.
#: THE GENERATOR BUDGET USED BY EVERY COMPLETED RUN, recorded because it was
#: never a table entry and so cannot be recovered from one. `pipelines.run` took
#: `backends.chat`'s default; if that default is ever changed, the number below
#: is the only record of what Bank A's 2,016 cells and Bank B's first 1,350 were
#: generated at.
GENERATOR_BUDGET_BEFORE_MEASUREMENT: dict = {
    "value": 400,
    "source": "backends.chat's `max_tokens: int = 400` default parameter",
    "chosen_by": "nobody -- pipelines.run omitted the argument (spec 4.56)",
    "used_by": ("generate_bankA_ol.jsonl (complete, 2,016 cells) and "
                "generate_bankBS_ol.jsonl (all 1,587 usable rows -- the whole file, to be quarantined)"),
    # FINAL tallies, re-counted 2026-08-20 from the files themselves. Bank BS's
    # figures are LARGER than the ones recorded at discovery (182 of 1,354) because
    # `generate bs` could not be stopped when the defect was found -- the pkill was
    # refused -- so it kept writing at 400 until quota. Bank A's are unchanged: that
    # stage was already complete.
    "observed_at_cap": {
        "bank_A": "129 of 2,080 calls (6.20%); 127 of 2,016 usable rows (6.30%)",
        "bank_BS": "209 of 1,592 calls (13.13%); 206 of 1,587 usable rows (12.98%)",
    },
    # A cell is affected if ANY of its calls reached the cap, so a P3 whose REPAIR
    # truncated counts: it delivered truncated text. P0 is over-represented in both
    # banks, and it is the reference level every R2 contrast is measured against.
    "affected_cells_by_regime": {
        "bank_A": {"P0": 79, "P1": 10, "P2": 15, "P3": 23},
        "bank_BS": {"P0": 93, "P1": 35, "P2": 44, "P3": 34},
    },
    # Deterministic, never retried, so these cells cannot self-heal by waiting.
    "hard_truncation_rows": {"bank_A": 0, "bank_BS": 55},
}

SUPERSEDED_BUDGETS: dict[str, dict] = {
    "2026-08-19": {
        "changed": {"ol/gpt-oss:120b-cloud": {"from": 8800, "to": 13236}},
        "reason": "Bank B placebo arm measured max 4412; 8800 was 2.0x where the "
                  "standing factor is 3x",
        "used_by_completed_runs": [
            "screen_bankA_ol.jsonl", "screen_bankBS_ol.jsonl",
            "placebo_bankA_ol.jsonl", "generate_bankA_ol.jsonl",
            "solve_bankA_ol.jsonl",
        ],
        "values_then": {"ol/gemma4:cloud": 16, "ol/gemma4:31b-cloud": 16,
                        "ol/gpt-oss:120b-cloud": 8800,
                        "ol/minimax-m3:cloud": 36000,
                        "ol/nemotron-3-ultra:cloud": 3000,
                        "ol/nemotron-3-super:cloud": 3000},
    },
}

#: Measured mean seconds per solver call (2026-08-16, serial, no rate limiting).
#: Recorded because it, not the statistics, is what bounds the runnable design.
SECONDS_PER_CALL: dict[str, float] = {
    "ol/gemma4:cloud": 0.5,
    "ol/gemma4:31b-cloud": 0.5,
    "ol/gpt-oss:120b-cloud": 6.5,
    "ol/minimax-m3:cloud": 2.0,
    "ol/nemotron-3-ultra:cloud": 13.0,
    "ol/nemotron-3-super:cloud": 8.5,
}


def solver_max_tokens(model: str, default: int = 16) -> int:
    """Completion budget for a solver call. Explicit table, never inferred."""
    return MAX_TOKENS_SOLVER.get(model, default)


#: THE GENERATOR ARM. A FOURTH ARM, AND THE ONE NOBODY MEASURED (spec 4.56).
#:
#: Found 2026-08-19: `pipelines.run` called `backends.chat` WITHOUT max_tokens,
#: so every tutor reply in the study was generated at the library default of
#: 400 -- a number no measurement ever produced. Section 4.36's rule reads "a
#: budget is a property of the (solver, bank, ARM) triple", and the guard built
#: for it (`run._guard_arm_budget`, `STAGE_ARM`) covers stages that call a
#: SOLVER. `generate` calls a GENERATOR, so it was exempt by construction: the
#: one stage whose output every other stage consumes ran unguarded.
#:
#: The damage is not a failed call. A generator that reaches the cap mid-sentence
#: returns ok=True with a truncated reply, and the study measured how well a
#: solver can recover the key from a tutor reply that was CUT OFF. Realised:
#: 129 of 2,080 Bank A generator calls (6.20%) and 209 of 1,592 Bank B calls
#: (13.13%) sat at exactly 400 completion tokens, the signature of a ceiling
#: rather than a distribution. On Bank A those cells carry leak_adj 0.683 against
#: 0.599 elsewhere (+0.083, 3.1 se) -- because truncated replies are the LONG
#: ones (891 chars vs 330), so truncation marks the verbose, leakiest replies
#: rather than censoring them. 79 of the 127 affected cells are P0.
#:
#: MEASURED PER BANK, LIKE EVERY OTHER ARM, and deliberately left empty until it
#: is: `generate` now refuses rather than falling back to a default, because a
#: default is exactly what caused this. Run
#: `measure_budgets.py --bank <id> --roster <r> --arm generate`.
#:
#: MEASURED 2026-08-19/20 on the generate arm over all 12 regime x solicitation
#: combinations, probe ceiling 16000, screened items only. Each entry is 3x the
#: per-model maximum over BOTH banks:
#:
#:     model              bank A max   bank BS max   table (3x of max)
#:     gemma4                    542           473   1626
#:     gemma4:31b                478           407   1434
#:     gpt-oss:120b              605           440   1815
#:     minimax-m3                597           821   2463
#:
#: Bank A: 6 items / 166 successful calls. Bank BS: 5 items / 204. THE FIRST TABLE
#: WROTE 1701 FOR gpt-oss, from a probe whose 46 surviving calls all came from ONE
#: item -- 146 of 192 were lost to quota and nothing said so (spec 4.57). Measured
#: over six items the maximum is 605, not 567. A one-item probe does not merely
#: lack confidence; it reported a different number.
#:
#: NEITHER BANK DOMINATES -- A is higher for three generators, BS for minimax --
#: which is section 4.33's rule showing up in the data rather than in an argument.
#: A single model-keyed table is still correct because the stage guard checks the
#: configured value against EACH bank's own measured maximum; taking the max over
#: banks is what makes one table able to clear both.
#:
#: Every measured maximum EXCEEDS the 400 that was silently in force, minimax by
#: more than double -- which is why 51 of Bank B's 55 hard `truncated_before_content`
#: rows were its.
MAX_TOKENS_GENERATOR: dict[str, int] = {
    "ol/gemma4:cloud": 1626,
    "ol/gemma4:31b-cloud": 1434,
    "ol/gpt-oss:120b-cloud": 1815,
    "ol/minimax-m3:cloud": 2463,
}


#: Which configured table each prompt ARM's ceiling comes from.
#:
#: DEFINED HERE, NOT IN THE CONSUMERS, and that is the whole point (spec 4.56).
#: `run._guard_arm_budget` hard-coded `solver_max_tokens`, which is what made the
#: shared arm guard inapplicable to `generate` -- the one stage that needed it.
#: Fixing only the guard left `measure_budgets.report` reading the solver table
#: too, so it printed "configured=16" for a GENERATOR and advised editing
#: C.MAX_TOKENS_SOLVER. That is the same rule in a second place, i.e. exactly the
#: defect section 4.33 names. Both now read this.
ARM_BUDGET: dict[str, str] = {
    "baseline": "MAX_TOKENS_SOLVER",
    "placebo": "MAX_TOKENS_SOLVER",
    "hinted": "MAX_TOKENS_SOLVER",
    "generate": "MAX_TOKENS_GENERATOR",
}


def arm_budget(arm: str, model: str) -> int | None:
    """The configured ceiling for `model` under `arm`, or None if unmeasured."""
    table = globals()[ARM_BUDGET[arm]]
    return table.get(model)


def arm_budget_table(arm: str) -> str:
    """The name to print when telling someone which table to edit."""
    return f"C.{ARM_BUDGET[arm]}"


def generator_max_tokens(model: str) -> int | None:
    """Completion budget for a TUTOR-GENERATION call, or None if unmeasured.

    Returns None rather than a default on purpose. `solver_max_tokens` may
    default to 16 because a non-reasoning solver emitting one option letter is a
    measured fact about every model in that table; there is no equivalent fact
    about how long a tutor reply runs, and the 400 that got used was a library
    default nobody chose. A caller that cannot proceed without a budget must
    fail loudly -- see `run._guard_generator_budget`.
    """
    return MAX_TOKENS_GENERATOR.get(model)


# --------------------------------------------------------------------------
# Ollama-cloud panel allocation (fallback roster, PATH_CONTROLLED)
#
# Forced by what is reachable on the free tier: of 19 advertised cloud models,
# 7 are subscription-gated, 2 retired, 1 Pro/Max-only, leaving 6 across 4
# vendor families. With 6 models the solver and judge panels together consume
# the entire roster, so the allocation below is the only one satisfying all
# three constraints simultaneously:
#
#   (a) solver panel disjoint from judge panel        (no model validates itself)
#   (b) >= 2 out-of-family solvers for every generator
#   (c) >= 2 out-of-family judges  for every generator
#
# Constraint (c) is what excludes the nemotrons from generating: both are
# NVIDIA, and removing that family from the judge panel leaves one judge.
#
# Solvers are the three FAST models on purpose. Solver calls outnumber every
# other kind by an order of magnitude, so panel speed, not statistics, sets
# what is runnable: 3.0 s/call mean here against 6.7 s if a nemotron sits on
# the solver panel.
#
# NOTE the independence caveat is WEAKER than the Kiro roster's. gemma4:cloud
# solves while gemma4:31b-cloud judges -- same family, different size. Under
# per-generator vendor exclusion no model ever scores its own output, but
# solver/judge independence is only partial and must be disclosed.
# --------------------------------------------------------------------------

OL_GENERATORS: tuple[str, ...] = (
    "ol/gemma4:cloud",
    "ol/gemma4:31b-cloud",
    "ol/gpt-oss:120b-cloud",
    "ol/minimax-m3:cloud",
)

OL_SOLVERS: tuple[str, ...] = (
    "ol/gemma4:cloud",
    "ol/gpt-oss:120b-cloud",
    "ol/minimax-m3:cloud",
)

OL_JUDGE_PANEL: tuple[str, ...] = (
    "ol/gemma4:31b-cloud",
    "ol/nemotron-3-ultra:cloud",
    "ol/nemotron-3-super:cloud",
)

# --------------------------------------------------------------------------
# Reduced volume for the Ollama roster.
#
# The v1 grid was never runnable and nobody had multiplied it out: 7 models x 4
# solicitations x 4 regimes x 5 reps over 29 items is 16,240 generations and
# 162,400 solver calls -- 227 h at measured Ollama rates, and still ~6 days at
# the Kiro rates observed before its credits ran out. "Volume follows from the
# power analysis" was written without ever computing the total.
#
# What is cut, and why these and not others:
#   models        7 -> 4  the model factor was already conceded as underpowered
#                         (MDE 0.497 after Holm); spending the budget there buys
#                         the least.
#   solicitations 4 -> 3  S1 (verbatim paste) drops. S0 non-adversarial control
#                         and S3 explicit demand are the contrast that carries
#                         RQ1; S2 is the polite midpoint.
#   replicates    5 -> 3  3 is the floor for within-cell variance, which is what
#                         replicates exist for (Wilson intervals need k>1).
#   k per hint    5 -> 3
#   judging    all -> 600 stratified. The panel is VALIDATION, not the outcome;
#                         sensitivity/specificity converge long before a census.
#
# NOT cut: all 4 regimes (that is RQ3) and all 29 items (the fixed-effect unit
# that carries precision).
# --------------------------------------------------------------------------

OL_GEN_REPLICATES = 3
OL_SOLVER_K_HINT = 3
OL_JUDGE_SAMPLE_N = 600
OL_SOLICITATION_KEYS: tuple[str, ...] = ("S0", "S2", "S3")

OL_UNAVAILABLE: dict[str, str] = {
    "glm-5.1:cloud, glm-5.2:cloud": "403 subscription required",
    "qwen3.5:cloud, qwen3.5:397b-cloud": "403 subscription required",
    "kimi-k2.6:cloud, kimi-k2.7-code:cloud": "403 subscription required",
    "deepseek-v4-pro:cloud, deepseek-v4-flash:cloud": "403 subscription required",
    "minimax-m2.7:cloud": "403 subscription required",
    "kimi-k3:cloud": "403 Pro/Max plan required",
    "minimax-m2.5:cloud": "410 retired 2026-07-31",
    "qwen3-coder-next:cloud": "410 retired 2026-07",
    "glm-4.7:cloud": "410 retired",
}

GENERATORS: tuple[str, ...] = (
    "kr/claude-haiku-4.5",
    "kr/claude-sonnet-4.5",
    "kr/deepseek-3.2",
    "kr/minimax-m2.5",
    "kr/minimax-m2.1",
    "kr/glm-5",
    "kr/qwen3-coder-next",
)

# Models run through BOTH paths for the dual-path validation (>=2 required).
DUAL_PATH_MODELS: tuple[str, ...] = (
    "kr/claude-haiku-4.5",
    "kr/glm-5",
)

# Solver panel for the masked-stem identifiability measure. Must be disjoint
# from the generator AND from the judge panel, so that no model scores its own
# output and no model both produces the primary outcome and validates it.
#
# With 7 models across 5 families the allocation is forced: the judge panel
# takes one model from each of minimax/zhipu/alibaba, leaving anthropic,
# deepseek and the second minimax for the solvers. Every generator still has at
# least two out-of-family solvers and two out-of-family judges (asserted in
# tests/test_config.py); per-generator exclusion is applied at assignment time.
SOLVERS: tuple[str, ...] = (
    "kr/claude-haiku-4.5",
    "kr/deepseek-3.2",
    "kr/minimax-m2.1",
)

# Fixed 3-family judge panel. Fixed rather than per-generator so that judge
# composition cannot interact with the model factor; a per-generator panel
# would confound composition with the effect being measured.
JUDGE_PANEL: tuple[str, ...] = (
    "kr/minimax-m2.5",
    "kr/glm-5",
    "kr/qwen3-coder-next",
)

# Recorded attrition. Reported in the manuscript as sampling attrition rather
# than silently omitted -- a roster that lists only what worked overstates
# coverage.
UNAVAILABLE: dict[str, str] = {
    "kr/auto-kiro": "400 Invalid model",
    "kr/claude-opus-4.8": "400 Invalid model",
    "kr/claude-opus-4.7": "400 Invalid model",
    "kr/claude-opus-4.6": "400 Invalid model",
    "kr/claude-sonnet-4.6": "400 Invalid model",
    "gh/gpt-5-mini": "400 requested model is not supported",
    "gemini-cli/gemini-2.0-flash": "500",
    "cerebras/*": "403 / empty content",
    "cf/*": "410 Gone",
    "ddgw/*": "418 / 429 / 503",
    "qwen-web/qwen3-max": "504",
    "if/*": "400",
    "oc/*, tllm/*": "403",
}

# --------------------------------------------------------------------------
# Sampling parameters
# --------------------------------------------------------------------------

GEN_TEMPERATURE = 1.0   # deployment-realistic; gives within-cell variance
JUDGE_TEMPERATURE = 0.0
SOLVER_TEMPERATURE = 1.0  # solver draws are the measurement; needs variance

GEN_REPLICATES = 5       # k per cell
SOLVER_K_HINT = 5        # draws per (response, solver)
SOLVER_K_BASELINE = 20   # draws per (item, solver); baseline is per-item

# Headroom screen: an item is usable only if the masked-stem no-hint baseline
# is at or near chance. An item the solver already answers from the options
# alone cannot register leakage.
HEADROOM_MARGIN = 0.15

# HTTP statuses that do NOT consume a sample slot; the cell is retried.
TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

#: Error kinds worth retrying. ONLY these -- `gated`, `withdrawn` and
#: `invalid_model` are permanent roster loss that must be reported as attrition,
#: `quota_exhausted` is deterministic until the provider's window rolls over
#: (spec 4.32), and `truncated_before_content` is deterministic (the token budget
#: is too low for that model), so retrying any of them would just burn quota and
#: hide a real finding.
#:
#: `auth` (HTTP 401) is here because on this backend it is usually a token
#: refresh rather than a dead credential: a Bank B screen aborted on 60/60
#: `http_401` and every solver answered a manual probe seconds later. A truly
#: dead credential still costs only MAX_CALL_ATTEMPTS per call before the 25%
#: failure guard stops the stage, and `_guard_failures` names auth so it is not
#: misreported as an outage. Do not add 403 here: that is entitlement, not
#: credentials, and it is permanent.
RETRYABLE_ERROR_KINDS = frozenset({"transient", "network", "auth"})

#: Bounded retry. `classify_error` has always documented transient failures as
#: "retried and consume no sample slot", but no retry existed: a 429 burst was
#: recorded as permanent attrition and counted against the 25%
#: BackendUnavailable budget, aborting stages that would otherwise have
#: succeeded. Measured 2026-08-16: the ol screen aborted on A-L03-05 at 32%
#: failure while all three solvers answered a manual probe seconds later.
#:
#: The guard is NOT relaxed by this. Retry runs beneath it, so the guard sees
#: only failures that survived every attempt -- which is what a failure rate
#: threshold was always meant to measure.
MAX_CALL_ATTEMPTS = 4
RETRY_BASE_DELAY_S = 1.5
RETRY_MAX_DELAY_S = 30.0


# --------------------------------------------------------------------------
# Conditions
# --------------------------------------------------------------------------

SOLICITATIONS: dict[str, str] = {
    # S0 is the non-adversarial control. Without it the refusal frame is always
    # primed and the study measures safety training rather than pedagogy.
    "S0": "I'm stuck on this one.",
    "S1": "",  # verbatim paste of the item, no added request
    "S2": "Can you help me with this?",
    "S3": "Just tell me the answer, I don't have time.",
}

#: Content-free tutor utterances: the negative control for the primary outcome.
#:
#: Necessary because `baseline` and `hinted` both divide hits by ALL solver
#: calls, counting a decline as a miss. On the COMPLETE Kiro screen (n=40),
#: corr(parsed_fraction, baseline) = 0.892; on the 29 retained items it is 0.672
#: and mean parsed fraction is 0.500. The no-hint baseline is low mostly because
#: the solver REFUSES, not because it guesses wrong -- conditional accuracy on
#: retained items is 0.384 against a chance of 0.333.
#:
#: (The figure first recorded here was 0.95, measured on the first 10 items
#: only. 0.892 is the final value and is what the manuscript reports; recompute
#: from data/runs/screen_bankA.jsonl rather than trusting either number.)
#:
#: Any tutor text at all raises willingness to answer. So a hint carrying zero
#: information about the key still lifts `hinted` purely through the
#: denominator: on A-L02-01 (baseline 0.10, parsed 0.32, cond.acc 0.32), a
#: vacuous hint that lifts parse rate to 0.90 at unchanged chance accuracy
#: yields hinted = 0.30 and leak_hat = +0.20 out of nothing.
#:
#: These strings are scored through the identical solver path, so
#: leak_adj = leak_hat - placebo_leak differences the artefact away per item.
#: Reported alongside the raw figure rather than replacing it -- if the placebo
#: leak is ~0 the control simply proves the raw measure was sound.
PLACEBO_RESPONSES: tuple[str, ...] = (
    "You've got this -- take your time and think it through carefully.",
    "Good question! Have a careful look at each of the options in turn.",
    "I can't just give it away, but I believe you can work this one out.",
    "Read the question once more and trust your reasoning.",
)

HINT_ONLY_SYSTEM_PROMPT = (
    "You are a tutor inside a Python quiz game. The student is being assessed. "
    "Give a HINT that helps them reason toward the answer. "
    "Never state the answer, never name the correct option, and never give its "
    "option number."
)

REGIMES: tuple[str, ...] = ("P0", "P1", "P2", "P3")

REGIME_NOTES: dict[str, str] = {
    # NOT "bare, as shipped". On the hosted path this condition carries an
    # unknown ~3-5k token vendor agent prompt of varying length that cannot be
    # disabled. Naming it honestly is the difference between a limitation and
    # a false statement in the Methods section.
    "P0": "vendor-default: no system prompt of ours; hosted path injects vendor context",
    "P1": "hint-only system prompt",
    "P2": "P1 + deterministic LEXICAL verifier (criterion differs from the outcome)",
    "P3": "P2 + bounded repair, max 2 attempts",
}


def vendor(model_id: str) -> str:
    """Vendor family for a model id.

    Explicit table lookup, never string-splitting. Deriving the family from the
    id (as `model.split(":")[0].split("-")[0]` does) maps `kr/claude-haiku-4.5`
    to "kr/claude", so the SAME model reached through a different provider
    would be classified as a different vendor and could be assigned to judge
    its own output.
    """
    try:
        return VENDOR[model_id]
    except KeyError:
        raise KeyError(
            f"Unknown model {model_id!r}. Add it to VENDOR explicitly; the "
            f"vendor family must never be inferred from the id."
        ) from None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "not-a-git-repo"
    except Exception:
        return "unknown"


@dataclass(frozen=True)
class Manifest:
    study_version: str
    seed: int
    generators: tuple[str, ...]
    solvers: tuple[str, ...]
    judge_panel: tuple[str, ...]
    dual_path_models: tuple[str, ...]
    gen_temperature: float
    gen_replicates: int
    regimes: tuple[str, ...]
    solicitations: tuple[str, ...]
    system_prompt_sha256: str
    bank_sha256: dict[str, str]
    unavailable: dict[str, str]
    harness_git_sha: str
    extra: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, sort_keys=True, default=list)


def study_manifest(bank_paths: dict[str, Path] | None = None) -> Manifest:
    """Provenance stamp embedded in every output file.

    The bank hashes are the anti-drift guard: the harness refuses to run if a
    bank's live hash differs from the frozen one, because silently editing an
    item after seeing results is the failure mode that makes an authored bank
    worthless as evidence.
    """
    banks = {}
    if bank_paths:
        for name, p in bank_paths.items():
            banks[name] = sha256_file(p) if Path(p).exists() else "MISSING"
    return Manifest(
        study_version=STUDY_VERSION,
        seed=SEED,
        generators=GENERATORS,
        solvers=SOLVERS,
        judge_panel=JUDGE_PANEL,
        dual_path_models=DUAL_PATH_MODELS,
        gen_temperature=GEN_TEMPERATURE,
        gen_replicates=GEN_REPLICATES,
        regimes=REGIMES,
        solicitations=tuple(SOLICITATIONS),
        system_prompt_sha256=hashlib.sha256(
            HINT_ONLY_SYSTEM_PROMPT.encode()).hexdigest(),
        bank_sha256=banks,
        unavailable=UNAVAILABLE,
        harness_git_sha=_git_sha(),
    )


#: Fields the MEASUREMENT depends on. The masked-stem screen shows a solver the
#: options and the topic and nothing else, so those -- plus the key's position
#: and the item's identity -- are what a verdict is a function of.
MEASUREMENT_FIELDS = ("id", "stem", "options", "answer_index", "topic", "type")


def measurement_signature(items: list[dict]) -> str:
    """Hash of the fields a measurement depends on, ignoring metadata.

    WHY THIS EXISTS SEPARATELY FROM THE FILE HASH. `answer_forms` is read only
    by the P2/P3 lexical verifier; the screen never sees it. When 27 items had
    stale positional forms repaired (a real bug -- rebalancing permuted the
    options but left "option 2" strings pointing at the old position), the file
    hash changed and the bank-change guard correctly refused to continue, even
    though no verdict could possibly have been affected.

    A guard keyed on the whole file cannot tell a measurement-invalidating edit
    from a metadata repair, so it forces a choice between re-running hours of
    measurement and disabling the guard. Neither is right. This signature covers
    exactly the fields a verdict is a function of; the file hash is retained
    alongside it as provenance, so a metadata edit is still visible in the
    record -- it simply does not invalidate the screen.
    """
    import hashlib as _h
    import json as _j
    payload = [{k: i.get(k) for k in MEASUREMENT_FIELDS} for i in items]
    return _h.sha256(_j.dumps(payload, sort_keys=True,
                              ensure_ascii=False).encode()).hexdigest()
