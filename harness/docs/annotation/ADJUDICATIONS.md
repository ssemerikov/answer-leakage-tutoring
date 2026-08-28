# Adjudicated cells in the returned annotation sheets

The sheets in this directory are what two people handed back. Anything changed
after the fact is recorded here, with the raw return preserved beside it, so the
labels the statistics were computed from can always be traced to a person.

`annotation_A2_completed_AS_RETURNED.csv` is A2's file exactly as returned
(UTF-8 with BOM, semicolon-delimited, with the spreadsheet's `Column1..Column5`
banner row). `annotation_A2_completed.csv` is that file with the single cell
below corrected. A1's file was returned unchanged and needed no adjudication.

## 1 cell of 480

| sheet | response_id | returned | read as | adjudicated by |
|---|---|---|---|---|
| A2 | `e5194ca9cd8c11b9` | `5` | `1` | the author who annotated as A2, 2026-08-28 |

The item's options are `1. 5 | 2. 4 | 3. 6` and the reply reasons to the value
five, so the entry recorded the answer's **value** where the codebook asks for
its **option number**. `read_labels` refused the file rather than resolving this
itself; the reading above came from the annotator, not from the toolchain.

**It changes nothing that is reported.** Under the alternative reading
(`cannot_tell`) percent agreement is 0.6875 rather than 0.6917, Cohen's kappa
0.5505 rather than 0.5564, and no sensitivity or specificity in the reported
grid moves by more than 0.004. The correction was made because a printed number
must come from a real label, not because the choice was consequential.
