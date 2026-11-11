# When each part of the study protocol was written

This note accompanies `PROTOCOL.md`. It says when the protocol was deposited, how the date of each protocol passage is
known, and whether each "before X" statement in the article holds. The article is cited by section or appendix name, with
a short quotation.

## Plain statement

1. **The protocol was deposited after data collection.** All data were collected between 2026-08-15 and 2026-08-28.
   `PROTOCOL.md` was extracted in November 2026, while the article was being revised, and deposited then.
2. **The protocol was not registered with any external registry.** It was never submitted to OSF, AsPredicted, a
   registered-report track or any similar service. Where the protocol says "pre-registered", it means "fixed in this
   internal design document before the event named". The word has no other meaning here.
3. **The protocol is an extract from a working design document, which is not deposited.** That document was kept
   throughout the study and also holds private operations notes and a defect log. Passages in it were added and
   annotated over time, so the document has no single date. `PROTOCOL.md` reproduces the passages that specify the study,
   verbatim and with the document's own line numbers. Every omission is marked with its line range and the reason.
4. **The design document as extracted** has 4,389 lines and SHA-256
   `9167bf942462486ca9bbf06020f1e66efab0f404af2d40afdff5f8a2b4d40336`.
   The hash was taken before the extraction and again after it, and the two agree.
5. **There is no version-control history of the study itself.**
   - The project directory was never a git repository. The row field `harness_git_sha` reads `"not-a-git-repo"` on every
     generation row in `data/runs/generate_bankA_ol.jsonl`. It reads the same on every Bank B generation row, but those
     rows are not deposited.
   - The deposit's own repository begins with a commit dated 2026-08-23, after all replies were generated and scored.
6. **The design document cannot date its passages through file metadata.** On the authors' file system, its current file
   was created on 2026-08-18 at 09:30, which is consistent with a whole-file rewrite. It was last modified on 2026-11-11.

## How dates are known

Every date in the claims table below has one of two grades.

- **SELF-DATED.** The only evidence is the date the design document gives itself: a heading, a dated "found" or "fixed"
  line, or the revision notes at the top of the document. Those notes are extracted as design-document lines 3 and 9–45.
- **CORROBORATED.** At least one independent record agrees with the date. Three kinds of record are used, and they differ
  in who can check them:
  - **In the deposit**, so a reader can check them:
    - row fields in the run files, such as `bank_sha256`, `bank_measurement_sig` and `harness_git_sha`;
    - dates embedded in deposited artefacts, such as `generated_at` in `data/PARTIAL_results_bankA_ol.json`, `frozen`
      in `harness/banks/bank_a_screened_ol.json`, `drawn` in `harness/banks/bank_b_sample_manifest.json` and
      `rule_amendment.date` in `data/examples_bankA.json`;
    - the deposit repository's commit dates.
  - **The authors' file system.** These are file creation ("birth") and last-modification times on the machine that ran
    the study. Run rows carry no timestamp of their own, so these times date the runs.
    - A birth time marks the creation of a file's current inode. For an append-only run file that was never replaced,
      that is when its first row was written. A modification time marks the last write.
    - Git does not preserve these times, so a reader of the deposit cannot check them. They are reported as recorded.
  - **The project's dated working notes**, which are not deposited. These are time-stamped notes kept during the study.
    Each day's file was last written on that day or within an hour after midnight. They are cited only where nothing else corroborates a
    date.

All times are +0300 (EEST) unless they are marked Z (UTC).

## When the events happened

| Event | Date and time | Evidence |
|---|---|---|
| Pilot tutor replies on a superseded hosted panel, including one item later retained in Bank A (`A-L01-01`), and the nine generations of the design review | 2026-08-15, by 19:09 | Modification time of `data/telemetry/pilot_endtoend.json` (deposited; the file has no internal date); design-document lines 9–29, in `PROTOCOL.md` |
| First stem-masked screen (Bank A, hosted panel; the file was never rewritten) | 2026-08-15, 22:19–23:23 | Birth and modification times of `data/runs/screen_bankA.jsonl` |
| Bank B corpus extracted (185 items) | 2026-08-16, 08:59 | Birth time of `harness/banks/bank_b.json` (not deposited: licence) |
| Bank B sample drawn (60 items) | 2026-08-16, 19:48 | `drawn` in `harness/banks/bank_b_sample_manifest.json`; birth time of the sample file (not deposited) |
| First Bank B measurement | 2026-08-16, 23:33 | Birth time of `data/runs/QUARANTINE_screen_bankBS_ol_minimax_trunc_budget2000_2026-08-17.jsonl`. Its rows carry `bank_sha256` `7db9c04508e11c0c…`, the draw hash that design-document line 1012 records |
| Reported Bank A screen | 2026-08-17, 08:25–11:40 | `data/runs/screen_bankA_ol.jsonl`; `frozen` = 2026-08-17 in `harness/banks/bank_a_screened_ol.json` |
| Reported Bank B screen, stopped after 44 of 60 items | 2026-08-17, 14:10–21:17 | `data/runs/screen_bankBS_ol.jsonl` |
| Bank A placebo | 2026-08-17 23:03 to 2026-08-18 00:42 | `data/runs/placebo_bankA_ol.jsonl` |
| **First study reply** (Bank A generation begins) | **2026-08-18, between 09:28:17 and 09:33:02** | Upper bound: birth time of `data/runs/generate_bankA_ol.jsonl`. Lower bound: every row records `bank_sha256` `0c7e3dc2…`, which is the hash of the deposited `harness/banks/bank_a.json`, and that file was last written at 09:28:17 |
| Bank A generation, last write | 2026-08-18, 11:26 | Modification time of `data/runs/generate_bankA_ol.jsonl` |
| Bank A scoring | 2026-08-18 11:48 to 2026-08-19 11:15 | `data/runs/solve_bankA_ol.jsonl` |
| First stored partial results (Bank A, 1,822 of 2,016 replies scored) | 2026-08-19T06:35:44Z | `generated_at` and `coverage` in `data/PARTIAL_results_bankA_ol.json` |
| Bank B placebo | 2026-08-19, 13:21–20:53:00 | `data/runs/placebo_bankBS_ol.jsonl` |
| Bank B generation | 2026-08-19 20:53:57 to 2026-08-20 13:29 | `data/runs/generate_bankBS_ol.jsonl` (not deposited: replies quote Bank B options) |
| Bank B scoring | 2026-08-21 11:07 to 2026-08-23 17:59 | `data/runs/solve_bankBS_ol.jsonl` |
| Human-validation sample drawn (240 replies) | 2026-08-23, 18:18 | Birth time of `data/human_validation_sample.json` |
| First completed annotation sheet returned | 2026-08-25, 08:02 | Modification time of `harness/docs/annotation/annotation_A2_completed_AS_RETURNED.csv` |

The article says the same about the pilots. Appendix B.2, "Departures from the protocol", opens with "Pilot replies while
the protocol was written" and states that "Where this article says that something came before any reply, it means
before any study reply."

## Claims and verdicts

| # | The article says (where) | Protocol (design-document lines) | Written | Grade | Precedes | Verdict |
|---|---|---|---|---|---|---|
| 1 | "The model, its contrasts and their families were pre-specified in the study protocol before any study reply existed" (Method, "Analysis") | 3671–3757; 9–45 | 2026-08-15 to 08-19, in parts (1a–1d below) | see 1a–1d | first study reply | holds as qualified by Appendix B.2 |
| 1a | Model formula, outcome and family R2 | 3671–3691, 3720–3723 | 2026-08-15 | SELF-DATED | first study reply, 2026-08-18 | holds |
| 1b | "The tutor-model family vacated before data" (Appendix B.2) | 3722, 3736–3750 | 2026-08-17 | CORROBORATED (working notes) | first study reply | holds |
| 1c | S1 removed "before any study reply existed … although the protocol's list of contrasts was corrected to match it only after Bank A's replies had been generated" (Appendix B.2) | 534–550; 3724–3726, 2632–2638, 2656–2658 | decision 2026-08-16; list corrected 2026-08-18 | CORROBORATED (deposit: no S1 row exists; working notes for the correction) | decision: first study reply | holds |
| 1d | "The protocol sized family R4 as two tests" (Appendix B.2) | 3727–3730, 2778–2781, 2811–2815 | by 2026-08-17; note 2026-08-19 | CORROBORATED (working notes) / SELF-DATED | R4 test, after 2026-08-23 | holds |
| 1e | "The protocol recorded per-call prompt length as a covariate" (Appendix B.2) | 405–410 | 2026-08-15 | SELF-DATED | exclusion, 2026-08-19 | holds |
| 2 | "Before any study reply existed … a rule fixed in the protocol beforehand" (Method, "Items and screening"); "The margin was fixed in the study configuration before the screen measured any item" (Appendix A.3, "The screen") | 193, 3379–3382, 1148–1165 | 2026-08-15 | CORROBORATED (deposit) | first screen, 2026-08-15; first study reply | holds |
| 3 | "Screening was stopped once the two banks together held 41 retained items, meeting a target of 40 fixed in advance" (Appendix A.3) | 1018, 1028–1045 | 2026-08-16 | CORROBORATED (working notes) | end of Bank B screen, 2026-08-17 21:17 | target holds; when the stopping decision was adopted cannot be evidenced |
| 4 | Bank B "selection rules applied before any measurement"; sample drawn "before any screening" (Appendix A.1, "The two banks") | 3628–3641, 1003–1015 | 2026-08-16 | CORROBORATED (deposit) | first Bank B measurement, 2026-08-16 23:33 | holds |
| 5 | "Before any study reply was scored, four content-free utterances per item went through the same reader models" (Method, "The recoverability measure") | 256–257 | 2026-08-15 | CORROBORATED (file system; working notes) | scoring: Bank A 2026-08-18, Bank B 2026-08-21 | holds |
| 6 | S1 "was removed before any study reply existed" (design table, Appendix B; Appendix B.2) | 498, 534–550 | 2026-08-16 | CORROBORATED (deposit) | first study reply | holds |
| 7 | 0.25 is "one cut on a grid from 0.10 to 0.50 fixed before either bank's results were complete; this cut was chosen after the results" (Method; Appendix B.2, "The reporting cut"; Appendix C.6, "Where the cut is placed") | 2560–2565; 193 | grid 2026-08-18 | SELF-DATED; the grid's existence by 2026-08-19 06:35Z is CORROBORATED (deposit) | Bank A scoring complete, 2026-08-19 11:15 | holds |
| 8 | Sample "sized by a precision rule fixed in the protocol before any study reply existed"; the stale frame was noticed "after Bank A's replies had been generated and before any was labelled" (Appendix B.4, "Human labels") | 1432–1460; 2662–2668 | 2026-08-17; re-sizing 2026-08-18 | CORROBORATED (working notes) | first study reply; first labels, 2026-08-25 | holds |
| 9 | "by a rule fixed in advance, levels are reported per reader model" (Method, "Validity evidence") | 3910–3913 | 2026-08-16 | partly CORROBORATED (working notes) | first panel ICC, 2026-08-19 | holds |
| 10 | "Before any study reply existed, the study protocol therefore recommended a sensitivity check that excludes ceiling cells" (Appendix C.2, "Ceiling cells") | 2202–2218 | 2026-08-17 | CORROBORATED (working notes) | first study reply; first computed 2026-08-19 | holds |
| 11 | Options permuted "Before screening"; "The protocol did not specify this step" (Appendix A.2, "Option order") | not extracted (a defect fix dated 2026-08-16) | 2026-08-16 | CORROBORATED (deposit) | reported screens, 2026-08-17 | holds for the reported screens |
| 12 | "Item-resampled intervals added" after "the first review of the results" (Appendix B.2) | not part of the protocol | 2026-11-09 | SELF-DATED | — | holds as disclosed |
| 13 | The rule for the example table, "which is not part of the protocol, was fixed in the analysis record before selection" (Appendix B.2; table "Bank A replies chosen by rules fixed before selection") | not part of the protocol; Annex A | rule 2026-11-03; amendment 2026-11-08 | amendment date CORROBORATED (deposit); rule date from working notes | selection, 2026-11-03 | holds |
| 14 | "The deposit made after the data": "Each bank was frozen and hashed before its replies were generated, and every run row records the bank's hash and a signature of the fields the measurement reads, a signature unchanged from screening to scoring" (Appendix B.2) | 3490–3493 | 2026-08-15 | — | — | holds (see 14) |

## Notes on individual claims

**1. The model and the families.** §6 of the protocol, "Analysis (pre-specified, before data exists)", was written on
2026-08-15 and later annotated in place:

- Family R1 was vacated on 2026-08-17, before any study reply.
- R3's contrast list was corrected on 2026-08-18, after Bank A's 2,016 replies had been generated, while their scoring
  was under way.
- A note keeping R4 at m = 2 was added on 2026-08-19, during Bank A scoring. It changes neither the tests nor m.

The article states the R3 timing in Appendix B.2 and confines "before any reply" to study replies, so the claim now
matches the record.

**1c. S1.** The decision to remove S1 is dated 2026-08-16 (design-document lines 534–550). The deposited generation rows
corroborate it: `solicitation_key` takes only the values S0, S2 and S3 on every row, so no S1 reply was ever generated.

**2. The screening rule.** The first screen, `data/runs/screen_bankA.jsonl` (2026-08-15, never rewritten), records
`chance` and `passes_headroom` on every row. All 40 verdicts equal `baseline <= chance + 0.15`, ties passing. Any margin
inside that screen's empty band would give the same verdicts, so the file shows the rule was in force but cannot single
out 0.15. The design document writes 0.15 on 2026-08-15 (lines 193 and 3379–3382).

**3. Stopping the Bank B screen.** The target of 40 retained items dates from 2026-08-16 (lines 1018 and 1028–1045), and
dated working notes from 2026-08-17 corroborate it. The design document records the choice between stopping at the target
and screening all 60 drawn items as a decision left to the authors. No surviving record dates its adoption, and the
authors do not recall the date. The article says only that "Screening was stopped once" the target was met. It does not
claim that the stopping decision was fixed in advance.

**4. The Bank B draw.** Every row of every Bank B screen file records `bank_sha256` `7db9c04508e11c0c…`, the draw hash in
design-document line 1012. That includes the earliest rows, written 2026-08-16 at 23:33. The later Bank B files (placebo,
generation, scoring) record `bfea4dfa…`, because the sample file was edited in place on 2026-08-18 at 09:28. The 60 item
identifiers did not change: `sampled_ids` in the deposited manifest matches the current sample file.

**7. The reporting cut.** The protocol's binary outcome was `leak_adj ≥ 0.5` together with a baseline condition (line
193). The design document replaced it with the grid on 2026-08-18, while Bank A was being scored (lines 2560–2565: "No
single leakage cutoff"). That date is self-dated. The deposited `data/PARTIAL_results_bankA_ol.json`
(`generated_at` 2026-08-19T06:35:44Z, 1,822 of 2,016 replies scored) already holds all six cuts, 0.10 to 0.50.
Appendix B.2, "The reporting cut", now states both the replacement and that 0.25 "was chosen after the results".

**8. Validation sizing.** The precision rule and N = 256 date from 2026-08-17 (§4.21: "It is fixed now, while no response
exists"), and that date is corroborated by dated working notes. The re-sizing to 240 dates from 2026-08-18 (§4.43). The
sample was drawn on 2026-08-23, and the first completed sheet was returned on 2026-08-25. One figure in the protocol is
wrong: line 2666 gives "±0.113 at a 15% minority", while a Wilson interval at p = 0.85 with 36 replies gives ±0.116. The
article prints ±0.116, which is correct. The protocol's figure is reproduced verbatim.

**9. Per-reader-model reporting.** The rule is pre-commitment 3 of §10a, dated 2026-08-16. Dated working notes from
2026-08-17 cite that numbered list of pre-commitments, so the list existed before the first study reply. The wording of
item 3 itself is self-dated.

**10. Ceiling cells.** The passage (§4.34, dated 2026-08-17) sits under a heading marked "OPEN, authors' call". It
recommends a ceiling-cell exclusion "pre-registered as a sensitivity analysis". The check was first computed on
2026-08-19. The article now says the protocol "recommended" the check, which matches the passage.

**11. Option order.** Bank A was screened twice before its options were rebalanced:

- the hosted-panel screen of 2026-08-15, whose rows record `bank_sha256` `e5b52f1b…`;
- a screen of 2026-08-16, deposited as `data/runs/QUARANTINE_screen_bankA_ol_unbalanced_bank_2026-08-16.jsonl`.

Both were superseded. Every reported screen, and every Bank B screen, used rebalanced options. Appendix A.2 reports one
baseline from the earlier settings and labels it as such.

**13. The example-selection rule.** The rule is restated in Annex A. It was recorded on 2026-11-03, in working notes that
are not deposited, before the selection was run. Its amendment carries `rule_amendment.date` = 2026-11-08 in the deposited
`data/examples_bankA.json`. Both postdate all data and results, and the article says the rule is not part of the protocol.

**14. The deposit.** The protocol planned to deposit each frozen bank, with a persistent identifier, before the first
study reply (lines 3490–3493). Appendix B.2, "The deposit made after the data", discloses that this was not done. It says
that "every run row records the bank's hash and a signature of the fields the measurement reads, a signature
unchanged from screening to scoring". In detail:

- Run rows record two fields. `bank_sha256` is the hash of the item-bank file in use. `bank_measurement_sig` is a
  signature of the fields the measurement reads.
- Bank A's screen and placebo rows record `bank_sha256` `14bdbab4…`, which `source_bank_sha256` in the frozen
  `harness/banks/bank_a_screened_ol.json` records.
- Bank A's generation and scoring rows record `0c7e3dc2…`, the hash of the deposited `harness/banks/bank_a.json`. That
  file's answer forms, which only the filter reads, were edited on 2026-08-18 at 09:28, before generation.
- Bank A's `bank_measurement_sig` (`4297e10a…`) is identical across screen, placebo, generation and scoring.
- Bank B's screen rows record the draw hash `7db9c045…`; its placebo, generation and scoring rows record `bfea4dfa…`.
  Its `bank_measurement_sig` (`14a66cca…`) is the same on screen, placebo, generation and scoring.
- The hash of the frozen bank file itself does not appear in the rows.

What the rows do show is that the bank content the measurement reads did not change between screening and scoring.

## Protocol passages not carried out as written

Each of these is reproduced verbatim in `PROTOCOL.md`.

**Disclosed in the article (Appendix B.2):**

- the pilots;
- the reporting cut;
- the deposit made after the data;
- the R3 correction;
- the vacated R1 family;
- the R4 main effect;
- the covariate;
- the judge tier;
- the item-resampled intervals.

**Superseded inside the protocol itself** (the later passage is also extracted):

- §3.4's 200 responses (now 240; §4.21, §4.43).
- §4.1's seven models and four request styles (§4.6).
- The flagged preamble of §3.2c.
- §6's "conditional logit"; the article notes that a linear model was fitted.

**Overtaken by the licence decision:** lines 1009 and 1044 say the unsampled Bank B items "stay in the released bank".
Bank B text is never redistributed. The deposit carries locators and hashes instead (`harness/banks/bank_b_locators.json`).

## Bank B text

The design document quotes one Bank B option verbatim, at line 2245, which lies in an omitted span. `PROTOCOL.md`
contains no Bank B text, and none needed redacting. `harness/ops/release_scan.py` reports both files CLEAN. A stricter
search, covering options of 15 or more characters and 30-character windows of every stem, also finds nothing.

## Annex A. The example-selection rule

This rule chose the Bank A replies printed in the article's table "Bank A replies chosen by rules fixed before
selection". It is not part of the study protocol.

**Population.** All 2,016 Bank A replies, each with its adjusted recoverability.

**When a reply "states the key".** The answer check (the lexical check that the filter runs) flags the delivered reply
against the current Bank A item table.

**Four categories.** Each category has its own selection condition.

1. *Gives the key without stating it:* condition P1 (instruction), not blocked, does not state the key, and adjusted
   recoverability of at least 0.75.
2. *Repaired but still gives the key:* condition P3 (repair), rejected by the filter at least once, not blocked, and
   adjusted recoverability of at least 0.50.
3. *Help that does not give the key:* any condition, not blocked, does not state the key, and adjusted recoverability of
   at most 0.10.
4. *States the key without safeguards:* condition P0 (no safeguard), and states the key.

**Order.** Within each category, candidates are sorted by the SHA-256 of the reply's cell identifier (UTF-8), ascending,
and the first is chosen. Nothing else enters the choice: not length, tutor model, item or wording. One item may appear
in more than one category.

**Date.** The rule was recorded on 2026-11-03, before the selection was run.

**Amendment 1 (2026-11-08), applied to every category.** Exclude any reply whose delivering call reached the tutor
model's completion limit. Reaching the limit means that the call's completion tokens were at least the budget it ran
under, which was 400 for every Bank A reply. A blocked reply never counts as reaching the limit.

- The amendment replaced one pick, in the category "help that does not give the key".
- The deposited `data/examples_bankA.json` records three things: the order (`order`); the amendment, with its date, the
  superseded pick and the candidate counts before and after (`rule_amendment`); and each category's pick.
- The article's table prints three of the four categories.
