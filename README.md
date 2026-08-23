# Answer leakage in LLM tutoring replies — instrument, data and analysis

Replication package for *Answer leakage in LLM tutoring replies beside graded
items* (submitted to **Computers and Education: Artificial Intelligence**).

An LLM assistant embedded in a learning game will be asked for help on graded
items, and a helpful explanation can make the keyed option recoverable without
ever stating it. This repository holds the instrument that measures that, the
per-call logs it produced, and the analysis that turns them into the paper's
numbers.

**No human participants were involved at any stage.** The study measures model
behaviour. The only human judgement in the design is authors labelling a sample
of model outputs (Tier 3, below), which is not human-subjects research.

## The measure

A response *leaks* when a solver that never sees the question stem can pick the
key from the tutor's reply and the option set alone — above what the options and
a **content-free utterance** already buy:

```
leak_adj(response) = hinted(response) − placebo(item, solvers)
```

The placebo term is per item and per solver panel, not a global constant. That is
load-bearing rather than decorative: pooled it is only +0.048, but per item it
ranges −0.144 to +0.500, and on one item a content-free utterance takes a solver
from 0/20 to 9/12 correct with parse rates of 1.000 in both arms.

## Headline results

| | Bank A (authored) | Bank B (independent) |
|---|---|---|
| retained items | 14 of 40 | 27 of 44 screened |
| responses | 2,016 | 3,888 |
| mean `leak_adj` | 0.605 | 0.494 |
| P1−P0 (instruction) | −0.127 | −0.198 |
| P3−P2 (repair vs verifier) | **+0.031** (rejects) | +0.020 (holds) |

Prompt-level instruction is reliable but insufficient — it removes about a fifth
of the baseline. A bounded **repair** step made leakage measurably *worse* than
the verifier alone on Bank A: a rewrite optimised against a string detector is
not a rewrite that hides the answer. Mitigation is **not bank-invariant**
(bank × regime *F* = 7.50, *p* = 5.3×10⁻⁵), and works better on the bank we did
not write.

Whole study: **41,940 solver calls**, 5,904 analysed responses, 41 items.

## Layout

```
harness/banks/  bank_a*.json         Bank A, pre- and post-screen (authored here)
                bank_b_locators.json Bank B pointers only -- see Licence below
data/runs/      *.jsonl              per-call logs, one row per unit of work
data/           results_*.json       assembled results, with input hashes
harness/        *.py                 the instrument and the analysis
harness/tests/                       the test suite, no network
harness/ops/                         supervision and progress scripts
```

## Licence — and why Bank B is only pointers

Code: **MIT** (`LICENSE`). Bank A, all measurements and all analysis output:
**CC BY 4.0** (`DATA-LICENCE.md`).

**Bank B's item text is not here and will not be.** It comes from a textbook
published under **GFDL 1.3 with Invariant Sections**, whose terms bind
*distribution* of copies or modified versions. Querying a model with an item and
recording the reply distributes nothing, so measuring over Bank B is fine;
redistributing it is not. What is deposited instead is
`banks/bank_b_locators.json` — source file, directive identifier, option count,
key index, and a SHA-256 of each item's text — plus every measurement we took.
Obtain the textbook, re-run the extraction, and the hashes confirm the items we
measured are byte-identical to the published ones.

For the same reason two files are **absent by design**, not by oversight:
`generate_bankBS_ol.jsonl` and its quarantined predecessor. Tutor replies to
Bank B items sometimes quote option text verbatim — a mechanical scan found 9
such spans — so those logs would redistribute the protected text indirectly.
Every other artefact in this repository passed that scan.

## Reproducing

```bash
cd harness
python3 -m pytest tests/ -q          # no network required; see note below
python3 results.py --bank A   --roster ol
python3 results.py --bank bs  --roster ol
python3 results.py --pool A,bs --roster ol      # family R4
python3 verify_numbers.py                       # every manuscript number vs the data
python3 promise_audit.py                        # does the paper keep what the Method promises?
```

**Six tests fail in this public copy, and that is expected.** The suite is 682
tests; here 666 pass, 10 skip and 6 fail, every one of them because it asserts
against a file that cannot be published: Bank B's item text (`test_checker`,
`test_size_bank_b`), the manuscript source (`test_promise_audit`), and the
project's own working notes (`test_claude_md_cites_real_scripts`). They are left
in place rather than deleted or marked skip, because a suite that passes by
having had its failing assertions removed tells a reader less than one that says
which artefact is missing.

`verify_numbers.py` recomputes each quantity the manuscript states and reports
`MISSING` when the paper and the data disagree. `promise_audit.py` checks a
different failure: a Method sentence promising something the Results never
deliver leaves *no number to mismatch*, so it is invisible to every other check.

Re-running the model calls needs a roster of served models and is not required to
reproduce any published number — the logs are complete.

## Known limitations, stated here as in the paper

- **The panel disagrees with itself.** ICC(2,1) is 0.620, 0.245 and 0.175 across
  three vendor-disjoint rater pairs. Per-solver levels span only 0.087, so the
  panel disputes *which* replies leak more than *how many*. Per-response uses
  (filtering, training) are affected more than the prevalence claim.
- **A generator token cap of 400 was a library default, not a choice.** It
  affected 127 Bank A cells. Re-generating exactly those at the measured budget
  moves the paired mean by +0.024 (95% CI [−0.004, +0.051]) and flips no verdict.
  Disclosed rather than re-run, and the direction is the opposite of what cell
  counts suggested.
- **Tier 2 (an automated judge panel) was specified and not run.** The reachable
  roster leaves half the generators two judges from one vendor family, which
  would inflate agreement. `run.py judge` refuses on that roster.
- **Tier 3 human labels are pending.** The sample, the codebook and the scorer
  are here; the labels are not, and nothing in this repository will produce them.

## Citing

Citation details follow acceptance. Until then, cite the repository URL.
