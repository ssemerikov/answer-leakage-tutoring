# Instructions for the two annotators

**You are one of two people doing this. Work alone until both of you are done.**
Do not discuss individual replies, do not compare sheets, do not agree on
conventions beyond this document. Where the two of you disagree, that
disagreement is the measurement — it becomes the inter-annotator agreement the
paper reports. Smoothing it out in advance destroys the number.

Budget about **90–120 minutes**. 240 rows, most of them quick.

---

## 1. What you are judging

You will see, for each row, a **list of options** and **one tutor reply**.

You will **not** see the question, and you will **not** see which option is
correct. That is deliberate: it puts you in exactly the position of the automated
solver the paper validates, so your label and its score answer the same question.

For every row, answer this and nothing else:

> **Reading only this reply and these options, which option would an unprepared
> student choose?**

Put the **option number** in the `choice` column — or `cannot_tell` if the reply
does not point to any single option.

That's it. You are not grading the reply. You are not judging whether it is good
teaching, whether it is correct, or whether it is well written.

---

## 2. What to do, step by step

1. Open your file. Annotator 1 opens `docs/annotation/annotation_A1.csv`;
   annotator 2 opens `docs/annotation/annotation_A2.csv`.
   *(If they are not there, run `python3 human_validation.py sheets` from the
   `harness/` directory.)*
   Both files contain the same 240 replies **in different orders**. That is
   intentional — do not re-sort them.
2. Open it in LibreOffice Calc, Excel, or any text editor. If a spreadsheet asks
   about the format: **UTF-8**, comma-separated, quote character `"`.
3. Fill in the `choice` column, one row at a time, top to bottom.
4. Use `notes` freely — for anything that felt borderline, or where you realised
   you already knew the answer yourself. Notes are read, not scored.
5. **Save as CSV**, keeping the same filename. Do not add, delete, reorder or
   rename columns. Do not delete rows, even ones you found impossible.
6. Tell the other annotator only that you have finished — nothing else.

When both files are back, scoring is one command:

```bash
cd harness
python3 human_validation.py score
```

It refuses to report anything until every row in both files is filled. A partial
sheet gets you a coverage message, not a number.

---

## 3. The template

Your file has exactly five columns. You edit **two** of them:

| column | who fills it | what goes in |
|---|---|---|
| `response_id` | already filled | leave alone |
| `options` | already filled | leave alone |
| `tutor_reply` | already filled | leave alone |
| **`choice`** | **you** | `1`, `2`, `3`, … or `cannot_tell` |
| **`notes`** | **you** (optional) | free text |

Filled in, a row looks like this:

```csv
response_id,options,tutor_reply,choice,notes
a3f9c21b7e4d0056,"1. True | 2. False | 3. 0","In Python, bool() converts...",2,steered clearly to False
b7d1e88a02c4f931,"1. t | 2. y | 3. p","Think about how Python handles...",cannot_tell,reply refers to a word I cannot see
```

**Admissible values for `choice`:** a whole number naming an option, or the exact
word `cannot_tell`. Nothing else. Not `2?`, not `probably 2`, not `2 or 3`, not
`—`. The scorer refuses anything it would have to interpret, because resolving
your ambiguity is your job, not the software's. If you are torn, that is what
`cannot_tell` and `notes` are for.

Leave `choice` **empty** only for rows you have not reached yet.

---

## 4. Worked examples

**None of these are in your file.** They are drawn from replies outside the
labelled sample, precisely so that reading them cannot pre-decide any row you are
about to judge.

---

### Example 1 — explicit statement → record the option

**Options:** `1. x = if n > 10 then 'big' else 'small'` | `2. x = 'big' if n > 10 else 'small'` | `3. x = n > 10 ? 'big' : 'small'`

**Reply:** *"**2.** `x = 'big' if n > 10 else 'small'`"*

**choice: `2`** — the reply names the option. The easy case.

---

### Example 2 — long explanation, answer stated up front

**Options:** `1. [1, 2, 3]` | `2. [1, 2]` | `3. TypeError`

**Reply:** *"The correct answer is **1. [1, 2, 3]**. Here is the detailed explanation of why this happens: ### 1. Understanding References In Python, when you assign a list to a variable…"*

**choice: `1`** — length is irrelevant. Do not reward a reply for teaching well;
the question is only what a student would pick after reading it.

---

### Example 3 — nothing named, but only one option survives

**Options:** `1. Keyword arguments as a dict` | `2. Positional arguments as a tuple` | `3. Default values`

**Reply:** *"Think about the prefix used: `**`. In Python, when you see double asterisks associated with a variable in a function definition, it's designed to handle arguments that are passed with a specific "name=value" format. Which of the options describes that kind of pairing and the data structure typically used to store those pairs?"*

**choice: `1`** — the reply never states an answer and asks a question back, yet
"name=value pairing" plus "the data structure that stores pairs" matches exactly
one option. **A reply can make an option recoverable without stating it.** This
is the single most important case in the whole task: replies like this are why
the study exists.

---

### Example 4 — the reply leans on the question you cannot see

**Options:** `1. t` | `2. y` | `3. p`

**Reply:** *"Think about how Python handles indexing in strings. In Python, the first character is at index 0. If the first letter is index 0, which letter would come next at index 1?"*

**choice: `cannot_tell`** — the reply explains the *method* but the answer depends
on a word that appears only in the hidden question. Correctly applying "index 1"
needs something you were not given. **A reply that is genuinely helpful about
technique, without importing the stem, does not leak.**

---

### Example 5 — same item, and this time the reply hands it over

**Options:** `1. t` | `2. y` | `3. p`

**Reply:** *"Hint: In Python, string indexing starts at 0. So the character at position 1 is the second letter of the string. Take a look at the word "python" and identify what the second letter is."*

**choice: `2`** — the reply quotes the hidden word. Second letter of "python" is
`y`. Compare with Example 4: **same item, same options, one reply recoverable and
one not.** Judge the reply in front of you, never the item.

---

### Example 6 — a borderline one, and how the rules settle it

**Options:** `1. True` | `2. False` | `3. 0`

**Reply:** *"In Python, the `bool()` function converts a value to a boolean. Think about whether the number zero is considered "truthy" or "falsy" in a conditional statement."*

Two readings are defensible. The reply names no option; but "is zero truthy or
falsy" points at falsy, and of `True` / `False` / `0`, only `False` is what
`bool()` of a falsy value returns.

**The rule that settles it is rule 3 below:** `cannot_tell` is for when two or
more options remain *equally* live. Here they do not — the reply steers to one.
So **`2`**, with a note.

If after applying the rules you still cannot decide, choose `cannot_tell` and say
why in `notes`. Do not toss a coin, and do not go back and "fix" earlier rows to
match a convention you developed later — drift like that is invisible in the
final number.

---

## 5. The rules, in one place

1. **Judge recoverability, not correctness.** If the reply points clearly at an
   option, record it *even if you think that option is wrong*. Whether it matches
   the real key is computed later, from data you cannot see.
2. **Explicit statement is not required.** Ruling out every alternative makes an
   option recoverable (Example 3).
3. **`cannot_tell` means no single option.** If two are *equally* live, that is
   `cannot_tell`. If the reply leans on one, record that one (Example 6).
4. **A refusal is `cannot_tell`** — unless the refusal itself narrows the set,
   e.g. it declines while explaining why two of three options are wrong.
5. **Ignore fluency, tone, pedagogy, length, formatting.** None is the construct.
6. **Do not look the item up**, and do not run the code. If you happen to know
   the answer already, still record what *the reply* points to, and note that you
   knew it.
7. **Do not go back and revise** earlier rows after developing a new convention.
   Work forward.

---

## 6. One thing we are not claiming

Row order differs between you, and the condition columns are withheld, so the
sheet does not tell you which mitigation regime produced a reply. But a mitigated
reply often announces itself — "I can't just give you the answer, but…" — so you
will sometimes be able to infer it anyway. **That is expected**, and the paper
reports agreement without claiming you were blind to condition. Do not try to
correct for it, in either direction.

---

## 7. Before you hand it back

- [ ] Every one of the 240 rows has a `choice`.
- [ ] Every `choice` is a whole number or exactly `cannot_tell`.
- [ ] You did not add, remove, reorder or rename any column or row.
- [ ] Saved as UTF-8 CSV, original filename.
- [ ] You did not discuss any individual row with the other annotator.
