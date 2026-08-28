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

For the same reason two log files are **absent by design**, not by oversight:
`generate_bankBS_ol.jsonl` and its quarantined predecessor. Tutor replies to
Bank B items sometimes quote option text verbatim — a mechanical scan found 9
such spans, occurring 372 times between the two logs — so those logs would
redistribute the protected text indirectly. One more file goes with the bank
rather than the code for the same reason: `banks/bank_b_answer_forms.json`, the
Tier-1 verifier's per-item answer-form table, some of whose entries *are* option
text. `author_forms.py` says so and stops when it cannot find it.

Every other artefact in this repository passed that scan, which is released as
`harness/ops/release_scan.py` rather than described. It needs Bank B to build
its rule from, so it cannot be re-run here — see below.

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

**Six tests fail in this public copy, and that is expected.** The suite is 704
tests; here 680 pass, 18 skip and 6 fail, every one of them because it asserts
against a file that cannot be published: Bank B's item text (`test_checker`,
`test_size_bank_b`), the manuscript source (`test_promise_audit`), and the
project's own working notes (`test_claude_md_cites_real_scripts`). They are left
in place rather than deleted or marked skip, because a suite that passes by
having had its failing assertions removed tells a reader less than one that says
which artefact is missing. The 18 skips are tests that need an artefact this
copy does not carry and say so — the built manuscript, or a roster of served
models — rather than asserting against its absence.

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

## Tier 3: the human-validation package

`data/human_validation_sample.json` is the frozen 240-response sample — five from
each of the 48 design cells, drawn in hash order with **no seed**, so there is no
seed that could have been chosen after the responses existed.

- `harness/docs/ANNOTATORS-START-HERE.md` — step-by-step instructions, the CSV
  template, and six worked examples drawn from replies **outside** the sample.
- `harness/docs/annotation_codebook.md` — the labelling rules.
- `python3 human_validation.py sheets` — regenerates the two blind sheets.
- `python3 human_validation.py score` — agreement, plus sensitivity/specificity
  against the manuscript's existing threshold grid. It refuses to report anything
  until every row in both sheets is filled.
- `python3 human_validation.py assemble` — writes
  `data/human_validation_results.json`, which is what the manuscript quotes.

The annotator is given the option set and the reply but **neither the stem nor the
key**, and performs the solver's own task: pick the option the reply points to, or
`cannot_tell`. The human label and the automated score are then answers to the
same question rather than two questions that happen to correlate.

**The labels are in.** Both authors labelled all 240 responses; the returned
sheets are `harness/docs/annotation/annotation_A{1,2}_completed.csv`. They agreed
on 166 of 240 (69.2%, Cohen's κ 0.556), and the disagreement is almost entirely
about *whether to commit* rather than about what the reply says: on the 158 where
both named an option they named the same one 139 times (88.0%), and 55 of the 74
disagreements are one annotator answering `cannot_tell`.

One entry of the 480 came back as an option's text rather than its number and was
read back by its annotator. The raw return is preserved as
`annotation_A2_completed_AS_RETURNED.csv`, the reading and its alternative are
recorded in `annotation/adjudications.json`, and `assemble` re-scores the whole
sample under the alternative so the claim that it changes nothing is computed
rather than asserted (it moves agreement by 0.004 and no rate by 0.005).

## The licence boundary is a script

`harness/ops/release_scan.py` builds every long verbatim span from Bank B — stems
of 40+ characters, options of 25+ — and refuses any file containing one. It is
what admitted the files here, and running it as a script rather than by hand is
what caught two option strings that had reached the *code*: a verifier table
(now shipped beside the bank, not with the code) and an explanatory comment. It
compares with whitespace collapsed and JSON escapes undone, because a stem stored
in a `.jsonl` has its line breaks written as escape sequences and a raw substring
test cannot see it.

It self-tests with no arguments. In this public copy both of its modes stop for
the same reason — there is no Bank B here to build a rule from, which is the
point — but with different exit codes: the self-test prints `SKIPPED` and exits
`0`, because proving nothing is a fair outcome, while scanning a tree exits `1`,
because certifying nothing as clean is not.

## Citing

Citation details follow acceptance. Until then, cite the repository URL.
