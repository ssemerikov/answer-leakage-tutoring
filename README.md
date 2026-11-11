# When the hint is the answer: instrument, data and analysis

Replication package for the article *When the hint is the answer: AI tutoring assistants on graded
multiple-choice items* (under review).

A language-model tutor beside a graded multiple-choice item can make the key recoverable without ever
naming it. This repository holds the instrument that measures that, the per-call logs it produced, the
analysis that turns them into the article's numbers, the study protocol with a note on when each part
of it was written, and an instrumentation report.

**No human participants were involved at any stage.** The study measures model behaviour. The only
human judgement in the design is two of the authors labelling a sample of tutor replies to validate the
instrument (below), which is not human-subjects research.

## The measure

A reply *gives the key away* when reader models that never see the question stem pick the key from the
reply, the options and the topic, beyond what a **content-free reply** to the same item already buys:

```
leak_adj(reply) = hinted(reply) − placebo(item)
```

The article calls `leak_adj` *adjusted recoverability*. The placebo is measured per item, not assumed
to be zero.

## What is here, in the article's words

| Article | Code and data |
|---|---|
| reader model, tutor model | `solver`, `generator` |
| request style: stuck, request for help, demand | `solicitation`: S0, S2, S3 (S1 was removed before any study reply existed) |
| condition: no safeguard, instruction, filter, repair | `regime`: P0, P1, P2, P3 |
| Bank A (written for the study), Bank B (an open textbook) | `A`, `bs` (the 60-item sample drawn from Bank B) |

41 items were analysed (14 from Bank A, 27 from Bank B), with 5,904 tutor replies: 2,016 on Bank A
and 3,888 on Bank B. Every number in the article is recomputed from the files below by
`harness/verify_numbers.py`; the results themselves are in `data/results_*.json` and
`data/exploratory_*.json`, not repeated here.

## Layout

```
protocol/PROTOCOL.md         the study protocol: a verbatim extract of the design document
protocol/DATING.md           when each part of the protocol was written, and the evidence for each date
protocol/INSTRUMENTATION.md  serving paths, retries, budgets, ceilings, supervision (left out of the article)
harness/banks/               Bank A, pre- and post-screen; Bank B as locators and hashes only
data/runs/*.jsonl            per-call logs, one row per unit of work (append-only; QUARANTINE_* superseded)
data/results_*.json          the pre-specified analysis, per bank and pooled
data/exploratory_*.json      descriptive shares, flagged vs given, item-resampled intervals
data/examples_bankA.json     the annotated Bank A replies printed in the article
data/human_validation_*.json the human-labelled sample and its results
data/telemetry/pilot_endtoend.json  the design-stage pilot (superseded panel; see protocol/DATING.md)
harness/*.py                 the instrument and the analysis; harness/tests/ needs no network
docs/revision-2026-11/figures/  the coordinates of the article's result figures (make_result_figures.py)
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
answer check's per-item answer-form table, some of whose entries *are* option
text. `author_forms.py` says so and stops when it cannot find it.

Every other artefact in this repository passed that scan, which is released as
`harness/ops/release_scan.py` rather than described. It needs Bank B to build
its rule from, so it cannot be re-run here — see below.

## Reproducing

```bash
cd harness
python3 -m pytest tests/ -q                      # no network; see the note below
python3 results.py --bank A   --roster ol
python3 results.py --bank bs  --roster ol
python3 results.py --pool A,bs --roster ol       # the pooled model and the bank comparison
python3 human_validation.py assemble
python3 exploratory.py && python3 make_result_figures.py
```

**Some tests fail in this public copy, and that is expected.** In this copy 1,464 tests pass,
28 skip, and 12 fail and 24 error. Every failure and error is a missing file that cannot be
published: the Bank B generation log `data/runs/generate_bankBS_ol.jsonl` (29), the Bank B sample
`harness/banks/bank_b_sample.json` (2), the manuscript source (2) and the project's working notes (1).
They are left in place rather than deleted or skipped, because a suite that passes by having its
failing assertions removed tells a reader less than one that names the artefact it is missing.
`exploratory.py` needs the Bank B generation log for the same reason, so its Bank B output is
deposited as computed rather than re-computable here.

`verify_numbers.py` and `promise_audit.py` check the manuscript source, which is not deposited.

`verify_refs.py` checks the bibliography against Crossref. Set `VERIFY_REFS_MAILTO` to your own
address if you want Crossref's polite pool; the deposit carries no address.

Re-running the model calls needs a roster of served models and is not needed to reproduce any
published number: the logs are complete.

## Known limitations

They are stated in the article (Discussion and Appendices B–D) and in `protocol/DATING.md`, which
lists every departure from the protocol and every claim about timing that the files cannot evidence.

## The human-validation package

`data/human_validation_sample.json` is the frozen 240-response sample — five from
each of the 48 design cells, drawn in hash order with **no seed**, so there is no
seed that could have been chosen after the responses existed.

- `harness/docs/ANNOTATORS-START-HERE.md` — step-by-step instructions, the CSV
  template, and six worked examples drawn from replies **outside** the sample.
- `harness/docs/annotation_codebook.md` — the labelling rules.
- `python3 human_validation.py sheets` — regenerates the two blind sheets.
- `python3 human_validation.py score` — agreement, plus sensitivity/specificity
  against the article's threshold grid. It refuses to report anything
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
