# Instrumentation report

This report accompanies the article "When the hint is the answer: AI tutoring assistants on graded
multiple-choice items". It holds the implementation detail the article leaves out: how calls reached
the models, how failed calls were retried and classified, the completion budgets for every
(model, bank, arm) triple, the ceiling on tutor replies, how failures were handled, and how long runs
were supervised. The article's estimates do not depend on reading it. It exists so that a replication
meets the same instrument and the same pitfalls.

Every number below names the file it comes from. Paths are relative to the root of this repository.
Numbers marked *(check: "label")* are recomputed from the data by the check of that label in
`harness/verify_numbers.py`. The last section says how the others were computed.

**Terms.** The article says *reader model*, *tutor model*, *request style* and *condition*. The code
and data say `solver`, `generator`, `solicitation` and `regime`. Conditions (article name, code): no
safeguard, P0; instruction, P1 (an instruction not to reveal the answer); filter, P2 (the instruction
plus an answer-text filter that withholds a flagged reply); repair, P3 (the filter plus up to two
automatic rewrites before withholding). Request styles: stuck, S0; request for help, S2; demand, S3 (a
fourth, S1, was removed before any study reply existed). The design table in the article's Appendix B gives the same mapping.
Banks: `A` (written for the study) and `bs` (the 60-item sample drawn from Bank B).

**Bank B text is not in this repository.** Bank B comes from *Foundations of Python Programming*
(Runestone Interactive), licensed under the GNU Free Documentation License 1.3 with Invariant Sections.
We measure over it and do not redistribute it. The Bank B item files and the Bank B generation logs
(tutor replies quote options verbatim) are therefore absent; this report gives counts from them only.
`harness/banks/bank_b_locators.json` and `harness/banks/bank_b_sample_manifest.json` hold locators and
SHA-256 hashes, and `harness/build_bank_b.py` rebuilds the items from the public source.

---

## 1. Serving paths and routing

The harness can reach a model by two paths (`harness/config.py`: `PATH_HOSTED`, `PATH_CONTROLLED`).

- **Hosted path.** Requests pass through an aggregating router to a commercial provider. The pilot
  roster (`kr`) used it. It is superseded and contributes no reported estimate.
- **Controlled path.** Requests go directly to a model runtime, with a system prompt we author and
  record as a SHA-256 hash. The study roster (`ol`) uses it. Every estimate in the article comes from
  this path and this roster.

**Why the hosted path was abandoned.** Requests sent through the router arrive at the model carrying
vendor-supplied context that the calling application can neither inspect nor disable. We probed seven
models with an identical 8-token user message, no system message, `max_tokens = 8` and temperature 0
(`data/telemetry/injected_context.json`, first observation):

| Quantity | Value | Source / check |
|---|---|---|
| smallest overhead | 2,926 tokens | `data/telemetry/injected_context.json` → `observations[0].tokens` (min); *(check: "overhead min")* |
| largest overhead | 4,604 tokens | same, max; *(check: "overhead max")* |
| spread across the seven models | 1,678 tokens | max − min; *(check: "overhead spread")* |
| mean gain on re-probe hours later, same day, same string | 1,998 tokens | `observations[1]` − `observations[0]` over the 3 re-probed models; *(check: "overhead drift (mean)")* |

The spread is aliased with the model factor by construction, and the drift means a long run carries
different hidden context early and late, which shuffling the order of cells does not fix. Neither can
be adjusted away, so the study moved to the controlled path. The re-probed models kept their rank
order.

**One provider account.** The `ol` roster reaches all models through one provider account. Cross-vendor
independence is therefore nominal at the level of infrastructure, and a fault in that provider's
serving stack would appear as a property of every model at once. The article discloses this.

**Roster change.** The pilot provider's quota was exhausted during piloting and the roster was replaced.
Figures obtained on the pilot roster appear in this report only (Section 5), labelled as pilot figures,
and are never pooled with the study data.

---

## 2. What every call records

Each model call is stored with: model, path, temperature, prompt and completion token counts, latency,
HTTP status, outcome (`ok`), error kind and detail, the system-prompt hash where one was sent, and a
cache-hit flag (call records in `data/runs/generate_bankA_ol.jsonl`, field `calls`; the same
`CallResult` record from `harness/backends.py` is used by every stage). Failed calls are kept, never
dropped, so attrition is visible. Every output file is stamped with the study version
(`harness/config.py` → `STUDY_VERSION`), the item-bank hash and the measurement signature, and a stage
refuses to append to a file written against a different bank hash (`harness/run.py` →
`assert_bank_unchanged`).

Sampling settings (`harness/config.py`): tutor models and reader models both run at temperature 1.0
(`GEN_TEMPERATURE`, `SOLVER_TEMPERATURE`), so that repeated draws carry variance. Each design cell has 3
replies (`OL_GEN_REPLICATES`), and each reply is read 3 times by each of the two reader models eligible
for that reply, since a reader model never scores a reply from its own vendor family (`OL_SOLVER_K_HINT`).

---

## 3. Retries and failure classification

A recorded call is not necessarily one request. `harness/backends.py` → `classify_error` maps every
failure to a kind, and only some kinds are retried (`harness/config.py` → `RETRYABLE_ERROR_KINDS`).

| Kind | Trigger | Retried? | Why |
|---|---|---|---|
| `network` | no HTTP status (connection error) | yes | carries no information about the item |
| `transient` | HTTP 408, 429 (rate limit), 500, 502, 503, 504 (`TRANSIENT_STATUSES`) | yes | as above |
| `auth` | HTTP 401 | yes | on this backend usually a momentary token refresh; a dead credential still trips the stage abort below |
| `quota_exhausted` | HTTP 429 whose body reports a session or weekly usage limit | no | deterministic until the provider's window rolls over |
| `gated` | HTTP 403 | no | entitlement: permanent roster loss, reported as attrition |
| `withdrawn` | HTTP 410 | no | permanent roster loss |
| `invalid_model` | HTTP 400 naming an invalid model | no | permanent roster loss |
| `truncated_before_content` | the completion budget ran out before any answer was emitted | no | deterministic: the budget is too small for that model, and a retry returns the same truncation |
| `http_<status>` | any other HTTP status (the fallback, last line of `classify_error`) | no | unclassified; recorded as a failed call |

A retryable failure is attempted up to four times in total (`MAX_CALL_ATTEMPTS` = 4; *check: "retry
attempt cap"*), with exponentially increasing, fully jittered delays starting at 1.5 s and capped at
30 s (`RETRY_BASE_DELAY_S`, `RETRY_MAX_DELAY_S`). The record reports the outcome of the final attempt.

The boundary between the two classes matters for interpretation, not only for cost. Had a truncated
completion been classed as transient, the budget defect described in Section 5 would have been hidden
behind an apparently clean run.

---

## 4. Failure handling

**Three outcomes, never pooled.** A call can fail (every attempt failed), return no parseable option
(a decline), or return an option. The harness counts the three separately. In an earlier version a
failed call and a decline were mapped to the same missing value, and an outage of the serving backend
produced a complete, internally consistent set of records reporting that every reader model had
declined every call. Because the estimand counts declines as misses, that conflation inflates apparent
difficulty without leaving a trace. The records written during that fault are kept unaltered as an
audit trail (`data/runs/QUARANTINE_placebo_bankA_credfail_2026-08-15.jsonl`).

**Stage abort.** A stage stops once more than a quarter of its calls have failed
(`harness/solvability.py` → `MAX_CALL_FAILURE_RATE` = 0.25, raised as `BackendUnavailable`; *check:
"stage abort threshold"*). The rate is counted after retries, so it measures calls that could not be
made at all. A run that cannot reach the models stops rather than reports a number.

**Failed calls in the analysis.** A failed reader-model call is scored as a miss and bounded, never
ignored. For the screen, `harness/solvability.py` → `truncation_slack` asks whether any retention
verdict could be reversed by rescoring the item's own failed calls as hits; it reports none on either
bank. For the response study, `harness/results.py` → `failure_slack` gives the largest shift any
failed call could cause; the article reports those bounds (Appendix C). Failed calls by kind, from the
`integrity.error_kinds` field of the results files:

| Run | Failed / all reader-model calls | Kinds | Source |
|---|---|---|---|
| Bank A responses | 2 / 12,096 | `quota_exhausted` 2 | `data/results_bankA_ol.json` → `failure_slack`, `integrity` |
| Bank B responses | 15 / 23,328 | `quota_exhausted` 14, `auth` 1 | `data/results_bankBS_ol.json` → same keys |

**Superseded artefacts.** Run files in `data/runs/` are append-only. An artefact superseded by a
corrected run is renamed `QUARANTINE_*` and kept, never deleted. The name states why; for example
`QUARANTINE_screen_bankA_ol_truncation_budget600_2026-08-16.jsonl` is the screen run under the 600-token
pilot budget described next.

---

## 5. Completion budgets for reader models

### 5.1 Why budgets are measured

Two of the three reader models (`gpt-oss:120b`, `minimax-m3`) emit their answer only after an internal
reasoning trace. A completion budget smaller than that trace returns an empty response, which in the
recorded output looks like a reader model that declined to answer. A decline is a measurement; an
exhausted budget is the absence of one.

**The pilot budget.** Under a pilot budget of 600 completion tokens, `gpt-oss:120b` exhausted its budget
on 54 of its 240 screening calls (22.5%)
(`data/runs/QUARANTINE_screen_bankA_ol_truncation_budget600_2026-08-16.jsonl`, per-solver `failed` and
`calls`; *check: "truncated calls (numerator)", "truncated calls (denominator)", "truncation rate"*).

**Why the loss is not random.** Reasoning length grows with how ambiguous the masked item is, and an
exhausted budget enters the baseline as a call that missed the key. Truncation therefore depresses the
measured baseline most on the ambiguous items, which lie nearest the retention threshold, and biases
the screen towards retaining items that have no headroom.

**How large the correction was.** The pilot screen and the corrected screen cover the same items
(`QUARANTINE_screen_bankA_ol_truncation_budget600_2026-08-16.jsonl` against `screen_bankA_ol.jsonl`):

| Quantity | Value | Check label |
|---|---|---|
| mean shift of items with at least 5% of calls truncated | +0.052 | "shift, >=5% truncated" |
| most negative shift of any item | -0.067 | "most negative shift" |
| binomial standard error of a 60-call baseline at the shared items' mean baseline, in calls | 3.7 | "truncation shift, noise floor (SE, calls)" |

Per-item shifts smaller than the noise floor are not evidence of direction; the directional claim is
about the mean over affected items. Items with no truncation reproduced their baselines exactly, but
every one of them sat at the ceiling (all 60 calls correct in both runs), so they give no evidence that
the measurement is otherwise stable (5 of the 12 items the two screens share; `baseline` 1.0 in both files). The case for the fix rests on the mechanism, with which the
comparison is consistent.

**The tie item.** On an earlier complete screen of Bank A by the study panel, item `A-L05-03` sat
exactly on the threshold, 29 of 60 calls against an operative threshold of 0.4833, with 2 failed calls
(`data/runs/QUARANTINE_screen_bankA_ol_minimax_trunc_budget2000_2026-08-17.jsonl`). Re-measured with a
budget above the observed completion length, it scores 0.55 and is discarded
(`data/runs/screen_bankA_ol.jsonl`). The defect was manufacturing retained items.

**The decline rate, before and after.** On the pilot roster's screen of Bank A (`data/runs/screen_bankA.jsonl`,
pre-rebalance bank, hosted path), reader models returned a parseable option on about half of their
calls on retained items (0.50; *check: "kr pilot parse rate (context only)"*), and an item's parseable
share correlated with its baseline at r = 0.89 (*check: "corr(parsed, baseline) all 40"*). On the
study screen with measured budgets (`data/runs/screen_bankA_ol.jsonl`) that correlation is 0.20
(*check: "primary: corr(parsed, baseline)"*). The high pilot decline rate was largely an artefact of
the budget, not a property of the reader models.

### 5.2 The rule

A budget belongs to a (model, bank, arm) triple, where the arm is `baseline` (masked item, no reply),
`placebo` (content-free reply), `hinted` (tutor reply) or `generate` (the tutor model's own reply).
Arms do not predict each other: a real tutor reply gives the reader something to work with, so on
Bank A `gpt-oss:120b` reasoned at most 881 tokens in the hinted arm against 1,890 in the placebo arm
(table below). Each budget is measured with `harness/measure_budgets.py` (wrapper `harness/ops/measure_arm.sh`)
at a 16,000-token probe ceiling and recorded in `data/runs/budget_<bank>_ol_<arm>.jsonl`. The policy
sets the ceiling at three times the observed maximum. A stage refuses to run (`harness/run.py` →
`_guard_arm_budget`) when the measurement file is not a measurement (fewer than 3 items or 12
successful calls: `MIN_BUDGET_ITEMS`, `MIN_BUDGET_CALLS`), when it was taken at another study version,
or when the configured ceiling is below the observed maximum; it warns when the ceiling is below three
times the maximum. A ceiling costs nothing on calls that do not reach it.

### 5.3 Measured maxima and configured ceilings

Observed maximum completion tokens over successful probe calls (`data/runs/budget_*_ol_*.jsonl`,
field `completion_tokens`; items = distinct items with at least one successful call):

| Bank, arm (file) | items | `gemma4` | `gpt-oss:120b` | `minimax-m3` |
|---|---|---|---|---|
| A, hinted (`budget_A_ol_hinted.jsonl`) | 9 | 2 | 881 | 958 |
| A, placebo (`budget_A_ol_placebo.jsonl`) | 6 | 2 | 1,890 | 2,263 |
| B, baseline (`budget_BS_ol_baseline.jsonl`, study version 2.2.0) | 8 | 2 | 2,933 | 7,770 |
| B, hinted (`budget_BS_ol_hinted.jsonl`) | 8 | 2 | 897 | 1,519 |
| B, placebo (`budget_BS_ol_placebo.jsonl`) | 8 | 2 | 4,412 | 8,308 |

`gemma4` does not reason; it answers with one or two tokens, and its budget is 16.

Ceilings in force for each run (`harness/config.py` → `MAX_TOKENS_SOLVER`, `SUPERSEDED_BUDGETS`, and
the `STUDY_VERSION` history comment; Bank B placebo and response rows also record their own
`solver_max_tokens`):

| Run file | Study version | `gemma4` | `gpt-oss:120b` | `minimax-m3` | Where the ceiling is recorded |
|---|---|---|---|---|---|
| `screen_bankA_ol.jsonl` | 2.2.0 | 16 | 2,400 | 8,000 | `STUDY_VERSION` comment (2.2 → 2.3 changes) |
| `screen_bankBS_ol.jsonl` | 2.3.0 | 16 | 8,800 | 36,000 | `SUPERSEDED_BUDGETS["2026-08-19"]` |
| `placebo_bankA_ol.jsonl`, `solve_bankA_ol.jsonl` | 2.3.0 | 16 | 8,800 | 36,000 | `SUPERSEDED_BUDGETS["2026-08-19"]` |
| `placebo_bankBS_ol.jsonl`, `solve_bankBS_ol.jsonl` | 2.3.0 | 16 | 13,236 | 36,000 | each row, `solver_max_tokens` |

Three notes on this table.

- `SUPERSEDED_BUDGETS["2026-08-19"]` lists `screen_bankA_ol.jsonl` among the runs completed under the
  8,800 table. The study-version history in the same file puts that screen at 2.2.0, before the
  2.2 → 2.3 change raised `gpt-oss:120b` from 2,400 to 8,800 and `minimax-m3` from 8,000 to 36,000. The
  two records disagree, and the rows carry no budget field. The 2.2.0 reading is consistent with every
  row's `study_version`. Either way, `truncation_slack` shows that no retention verdict on Bank A can be
  reversed by its 2 failed calls, so the partition does not depend on which budget applied.
- Bank A's screen ran at study version 2.2.0 and every later run file of either bank at 2.3.0. This is not
  a violation: pooling compares the versions on the response rows (all 2.3.0), and every reported
  estimate is referenced to the placebo, which ran at 2.3.0 on both banks. The version was deliberately
  not bumped when the `gpt-oss:120b` ceiling rose for Bank B, because a bump invalidates every budget
  measurement file and makes the banks unpoolable.
- `minimax-m3`'s 36,000 is not three times any maximum in the measured-maxima table above (the largest there is
  8,308). The comment on `MAX_TOKENS_SOLVER` in `harness/config.py` derives it from an earlier Bank B probe: one call
  exceeded the 16,000-token probe ceiling, a re-probe of that item at 40,000 tokens returned a longest terminating
  trace of 11,769 tokens, and 36,000 is about three times that. The probe file the comment names,
  `data/runs/budget_BS_ol.jsonl`, is in neither the study's `data/runs/` nor this repository, and the re-probe is
  recorded in no run file. The B baseline row above (`budget_BS_ol_baseline.jsonl`) holds the four longest
  terminating traces the comment lists (1,466, 4,253, 6,848 and 7,770 tokens) and its one call censored at the probe
  ceiling.

---

## 6. The ceiling on tutor replies

**What happened.** The tutor-generation call originally passed no completion budget, so it inherited
a library default of 400 tokens that no measurement had produced
(`harness/config.py` → `GENERATOR_BUDGET_BEFORE_MEASUREMENT`). A tutor model that reaches such a
ceiling does not fail: it returns a well-formed reply cut off mid-sentence, recorded as a success.

**Bank A.** All 2,016 Bank A replies were generated at 400 (`data/runs/generate_bankA_ol.jsonl`).
Of 2,080 successful generation calls, 129 stopped at exactly 400 tokens, and 127 of the 2,016 usable
replies contain at least one such call: 79 under P0, 10 under P1, 15 under P2 and 23 under P3. The
article discloses this rather than re-running the bank, after measuring its effect: the 127 replies
were regenerated at the measured ceilings and re-scored (`data/runs/capcheck_generate_bankA_ol.jsonl`,
`data/runs/capcheck_solve_bankA_ol.jsonl`, `data/capcheck_contrasts_bankA_ol.json`, script
`harness/cap_sensitivity.py`). No contrast verdict changes (Appendix C of the article).

**Measured ceilings.** The generate arm was then measured over all twelve condition × request-style
combinations on screened items, at a 16,000-token probe ceiling. Each tutor model's ceiling is three
times its maximum over both banks (`harness/config.py` → `MAX_TOKENS_GENERATOR`;
`data/runs/budget_A_ol_generate.jsonl`, `data/runs/budget_BS_ol_generate.jsonl`):

| Tutor model | Bank A max (6 items) | Bank B max (5 items) | Ceiling |
|---|---|---|---|
| `gemma4` | 542 | 473 | 1,626 |
| `gemma4:31b` | 478 | 407 | 1,434 |
| `gpt-oss:120b` | 605 | 440 | 1,815 |
| `minimax-m3` | 597 | 821 | 2,463 |

Every measured maximum exceeds the 400 that was in force. Generation now refuses to run for a tutor
model with no measured ceiling (`harness/pipelines.py` → `run`; `harness/run.py` → `_guard_arm_budget`
covers the generate arm), the repair call under P3 uses the same ceiling as the first call, and each
new generation row records its ceiling in `generator_max_tokens`.

**Bank B.** Bank B's first generation run also used 400 tokens. That file is kept whole but not
deposited (it contains Bank B text):
`QUARANTINE_generate_bankBS_ol_generator_cap400_PARTIAL_clean_rows_kept_2026-08-20_a61f4004.jsonl`.
Of its 1,587 usable replies, 206 reached 400 tokens, and 55 further attempts failed as
`truncated_before_content`. A ceiling truncates; it does not steer sampling, so a reply that finished
below the ceiling is a valid draw that the ceiling never touched. The live file
(`generate_bankBS_ol.jsonl`, not deposited) therefore keeps the 1,381 usable replies that never reached
the ceiling (their longest call: 398 tokens) and generated the other 2,507 at the measured ceilings: 250 redraws of cells the 400-token run had cut off or left
without a usable reply, and 2,257 cells that run never reached.
Regenerated rows record their ceiling in `generator_max_tokens`; carried-over rows lack the field.
Each kept reply is a valid draw, but keeping the short first draws and redrawing only the long ones selects on
length. In 251 cells the 400-token run produced a draw that the ceiling cut off: the 206 above, and 45 more whose
attempts failed as `truncated_before_content` (44 had no usable reply and were regenerated; 1 kept a shorter second
attempt). On redraw 191 of the 251 came back above 400 tokens (154 of the 206), so these cells hold 60 fewer long
replies than their first draws did (1.5% of the 3,888; 108 of the 251 are P0). The shortfall is small, and its
effect on the Bank B estimates was not measured.

---

## 7. Run supervision

Stages are quota-limited and ran for days. Scripts in `harness/ops/`:

- `supervise.sh <stage> <bank> <target> [roster]` relaunches a stage after a quota stop, waiting `WAIT`
  seconds (default 300) between launches. A launch that adds no rows while the provider still reports a usage
  limit is a stall and is retried, up to `MAX_STALL` consecutive cycles (default 144, so 144 × 300 s = 12 hours);
  refused credentials are retried up to `MAX_AUTH` cycles (default 12, one hour). It aborts on a hard failure
  (a traceback found by `failure_since.py`), on a launch that adds no rows without a quota reason (the next unit
  of work is then failing reproducibly, and looping cannot help), or when either bound runs out.
- `watch_stage.sh` prints one progress line per check. Liveness comes from the supervisor's pidfile,
  never from matching process command lines; death is reported only when the pidfile is gone and no
  new rows arrived.
- `count_rows.py` counts progress as successful rows (records with `ok` true and a non-empty reply),
  never as file lines: generation files keep failed attempts as an audit trail. For example,
  `generate_bankBS_ol.jsonl` has 4,605 lines and 3,888 usable rows.
- `failure_since.py` detects failures by parsing whole tracebacks, because a traceback is one event
  spread over many lines and a line-wise grep misreports routine quota stops as hard failures.
- `stage_paths.sh` gives the supervisor and the watcher one shared definition of each stage's files.
- `release_scan.py` checks a deposit for Bank B text before anything is added; it matches permissively
  on purpose, because here a false negative publishes protected text.

Quota stops on the study roster were HTTP 429 usage-limit responses (`quota_exhausted`); they cleared on
their own as the provider's window rolled over. The failed attempts kept in the live generation files
are all of this kind: 179 in `generate_bankA_ol.jsonl` and 717 in the Bank B file.

---

## 8. Run scale

Reader-model calls per stage, summed over `per_solver.calls` (*check: "solver calls consumed"*):

| Stage | Bank A | Bank B sample |
|---|---|---|
| screen | 2,400 (`screen_bankA_ol.jsonl`) | 2,640 (`screen_bankBS_ol.jsonl`) |
| placebo | 504 (`placebo_bankA_ol.jsonl`) | 972 (`placebo_bankBS_ol.jsonl`) |
| responses | 12,096 (`solve_bankA_ol.jsonl`) | 23,328 (`solve_bankBS_ol.jsonl`) |
| **total, both banks** | | **41,940** |

---

## 9. How the unchecked numbers were computed

The numbers above that carry no check label were computed read-only from the files named beside them.
The revision scripts that computed them are kept with the manuscript sources, not in this repository;
each step is short enough to repeat by hand from the named file and field:

- budget maxima: maximum of `completion_tokens` over rows with `ok` true, per model, per budget file;
- generator ceiling counts: calls with `completion_tokens` equal to 400 among successful calls of usable
  rows (`ok` true, non-empty `response`);
- Bank B provenance: usable rows with and without the `generator_max_tokens` field;
- Bank B redraws: cells of the quarantined 400-token file with a usable row whose successful call reached 400
  tokens, or with an attempt that failed as `truncated_before_content`, matched by `cell_key` to the live file's
  usable row, which counts as long when a successful call exceeds 400 tokens (both files are Bank B generation logs
  and are not deposited); cells never reached: live usable cells absent from the quarantined file;
- supervision defaults: `WAIT`, `MAX_STALL` and `MAX_AUTH` at the top of `harness/ops/supervise.sh`;
- failed attempts by kind: `error_kind` over rows with `ok` false;
- run scale: sum of `per_solver.*.calls` per run file.
