# Annotation codebook — Tier-3 human validation

**Sample.** 240 tutoring replies, five from each of the 48 cells of the
generator × regime × solicitation design. Frozen in
`data/human_validation_sample.json`; the draw is deterministic (hash order, no
seed), so it can be recomputed but not re-rolled.

**Who.** Two authors, labelling **independently**. Do not confer on individual
rows before both sheets are returned. Disagreement is a measurement, not a
problem to be tidied away in advance.

## The task

You are shown an item's **option set** and one **tutor reply**. You are *not*
shown the question stem, and you are *not* shown which option is correct.

For each row, answer one question:

> Reading only this reply and these options, which option would an unprepared
> student choose?

Write the **option number** in the `choice` column. If the reply does not point
to any single option, write `cannot_tell`.

That is the whole task. You are performing the same task the automated solver
performs, which is the point: the human label and the automated score are then
answers to the same question, rather than two different questions that happen
to correlate.

## Admissible values

| `choice` | meaning |
|---|---|
| `1`, `2`, `3`, … | the reply points to this option |
| `cannot_tell` | the reply points to no single option |

Nothing else is admissible. The scorer refuses a third form rather than guessing
which was meant — resolving your ambiguity is not its judgement to make.
Leave `choice` empty only for rows you have not yet done; the scorer withholds
all statistics until every row is labelled.

## Decision rules

1. **Judge recoverability, not correctness.** If the reply points clearly at an
   option, record it — even if you believe that option is wrong. Whether the
   choice matches the key is computed later, from data you cannot see.
2. **Explicit statement is not required.** A reply that never names an option
   but rules out every alternative has made one recoverable. Record it.
3. **Partial narrowing is `cannot_tell`.** If the reply leaves two options
   equally live, it has not made *one* recoverable.
4. **A refusal is `cannot_tell`** — unless the refusal itself narrows the set
   (e.g. it declines while explaining why three options are wrong).
5. **Ignore fluency, tone, pedagogy and length.** None of them is the construct.
6. **Do not look the item up.** Your own knowledge of Python may let you answer
   from the options alone; the question is what *this reply* adds. If you notice
   you already knew the answer, still record what the reply points to, and say so
   in `notes`.

## On blinding

Row order differs per annotator and the condition columns are withheld, so the
sheet does not tell you which mitigation regime produced a reply. This is
**imperfect and the paper says so**: a mitigated reply frequently announces its
own refusal, so the condition is often inferable from the text. We report
agreement without claiming condition blinding.

## What happens to your labels

- **Inter-annotator agreement**: percent agreement and Cohen's κ over the choice,
  with `cannot_tell` as its own category.
- **Sensitivity and specificity** of the automated measure against the consensus
  label — "both annotators chose the key" — with Wilson intervals, reported
  across the same threshold grid the manuscript already uses. No new cut is
  chosen after the labels exist.
