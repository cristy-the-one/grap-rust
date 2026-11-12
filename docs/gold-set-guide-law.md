# Labelling guide — Romanian law gold set

**For:** the legal reviewer. No programming knowledge is needed.
**You will receive:** three spreadsheets and the source texts they refer to.
**Expected effort:** about 20 hours in total. Work in any order and in as many sittings as you like.

## Why this exists

We have software that reads Romanian legal texts and writes down what it thinks they say. We do
not know how often it is right. Your answers become the reference it is scored against, so they
must be **yours**: what a trained reader concludes from the text, with no knowledge of what the
software produced.

## Four rules that matter more than everything else

1. **Work from the text only.** Judge what this passage states. Do not use your knowledge of
   whether the provision is still in force, was later amended, or how courts apply it.
2. **Do not use an AI assistant** to help label, and do not paste the passages into one.
3. **"Unclear" is a valid answer.** If a competent lawyer could reasonably read the passage two
   ways, say so and write one line on why. A forced answer is worse than an honest "unclear".
4. **Do not skip the awkward ones.** Label every row you are given. If a passage is broken (cut
   mid-sentence, garbled), mark it `broken` and move on — do not replace it with a nicer one.

Please send the finished files only to the project owner. Part of your work is held back as a
sealed exam that the developers never see; you do not need to know which part.

---

## Sheet 1 — Statements (about 150 passages)

Each row shows one passage and the article it sits in. A passage may contain **one rule, several,
or none**. Add one row per rule you find (copy the passage ID). If there is none, fill one row
with kind `other`.

### Columns you fill in

| Column | What to write |
|---|---|
| **kind** | One of the eight kinds below, or `unclear`. |
| **subject** | Who or what the rule governs — **copied exactly** from the passage, all its words, diacritics included. Leave empty if the passage does not state it. |
| **conditions** | The words that limit *when* the rule applies ("în cazul în care…", "numai dacă…"), copied exactly. Empty if none. |
| **exceptions** | The words that carve something out ("cu excepţia…"), copied exactly. Empty if none. |
| **dates and amounts** | Any deadline, date, threshold or percentage the rule depends on, copied exactly. |
| **acts referred to** | Every act the passage names, as written ("Legea nr. 58/1998"). |
| **sure?** | `yes`, `fairly`, or `no`. |
| **note** | Anything we should know — especially if none of the kinds fits well. |

### The eight kinds

| Kind | Use it when the passage… | Typical wording |
|---|---|---|
| **obligation** | imposes a duty, or says what something must contain or reach | "este obligat", "trebuie", "va cuprinde", "vor asigura" |
| **prohibition** | forbids conduct — even if only under a condition | "se interzice", "este interzis", "nu pot" |
| **permission** | allows conduct without requiring it | "poate", "pot", "are dreptul să opteze" |
| **right** | gives a beneficiary an entitlement | "are dreptul la", "beneficiază de", "primeşte" |
| **definition** | assigns a meaning to a term | "prin … se înţelege", "în sensul prezentei legi" |
| **amendment** | changes, repeals or replaces another text | "se modifică", "se abrogă", "se completează", "va avea următorul cuprins" |
| **fact** | describes or establishes something without directing anyone: an office is set up, a body is subordinated, a scope is stated | "se înfiinţează", "funcţionează în subordinea", "se aplică" |
| **other** | asserts nothing: a heading, a signature block, a bare citation | — |

### Rules for the hard cases

- **The subject is who or what is governed, not when or where.** In "La înfiinţarea unei bănci,
  acţionarii vor vărsa…", the subject is *acţionarii*, not the founding of the bank.
- **Passive rules:** do not invent the responsible person. If the text says payments "se
  efectuează", the subject is the payments.
- **Copy the whole qualified subject.** "băncile autorizate şi băncile care au aprobarea de
  constituire" is one subject, not just "băncile".
- **A conditional prohibition is still a prohibition.** Put the condition in its column; do not
  downgrade the kind.
- **Receiving a benefit is a right, not an obligation** of the person receiving it.
- **"nu este obligat"** is not an obligation. Decide whether it is a permission or a fact.
- **Citing an act is not amending it.** `amendment` needs explicit changing words.
- **A list of required contents** ("registrul va cuprinde: a)… b)… c)…") is **one** obligation;
  keep the whole list in the passage, do not split it per item.
- **Two rules in one sentence** joined by "iar" or a semicolon: two rows.

### Worked examples

> *Se interzice amplasarea de unităţi cu profil alimentar în spaţiile de locuit.*

kind `prohibition` · subject *amplasarea de unităţi cu profil alimentar în spaţiile de locuit* ·
conditions — · exceptions —

> *Procedura de negociere directa este aplicabilă în cazul în care licitaţia publică nu a condus
> la desemnarea unui câştigător.*

kind `fact` · subject *Procedura de negociere directa* · conditions *în cazul în care licitaţia
publică nu a condus la desemnarea unui câştigător*

> *Prevederile prezentei legi nu se aplică contractelor de proiectare, finanţare, construcţie…
> care se încheie potrivit procedurilor prevăzute în Ordonanţa Guvernului nr. 16/2002, aprobată
> cu modificări şi completări prin Legea nr. 470/2002…*

kind `fact` (it states scope) · subject *Prevederile prezentei legi* · acts referred to
*Ordonanţa Guvernului nr. 16/2002; Legea nr. 470/2002* — and **not** `amendment`: the passage
mentions that an act was approved with changes, it does not itself change anything.

---

## Sheet 2 — Same thing or not? (about 200 pairs)

Each row shows two names, each with the sentence it came from and its document. Decide whether
they refer to **the same real-world thing**.

| Answer | Meaning |
|---|---|
| **same** | One and the same act, institution, office, person or concept. Different spelling, grammatical case or abbreviation does not matter. |
| **different** | Two distinct things, however similar the names. |
| **same only here** | An abbreviation or short name that means this thing **in this document** because the document defines it, but could mean something else elsewhere. |
| **unclear** | The sentences shown are not enough to decide. Say what is missing. |

Guidance:

- **Ministry and minister are different.** An institution and its head are two things.
- **Acts are the same only if type, number and year all match** — and, where relevant, the issuer.
  "Legea nr. 58/1998" and "Legea nr. 101/1998" are different however close the wording.
- **"Guvernul", "Guvernul României", "GUVERNUL"** — same.
- **"prezenta lege", "prezenta hotărâre"** — the same as the act of the document it appears in, and
  nothing else. If the two rows come from different documents, they are different.
- **An act and one of its articles** are different.
- **An institution renamed over time**: `unclear` unless the text itself says it is the successor.

---

## Sheet 3 — Questions (about 100)

Here you **write** the questions. Each must be something a practitioner would really ask, and must
be answerable — or deliberately not answerable — from the documents in your packet alone.

| Column | What to write |
|---|---|
| **question** | In Romanian, as you would naturally ask it. |
| **type** | One of the five types below. |
| **answer** | Two or three sentences at most. |
| **where** | Document ID and article (and paragraph) of **every** passage needed for the answer. |
| **note** | Anything subtle: a trap, an exception that changes the answer. |

Please spread the questions roughly like this:

| Type | Share | Example shape |
|---|---:|---|
| **Direct** — one provision answers it | 30 | "Ce termen are… pentru…?" |
| **Conditional** — the answer turns on a condition or exception | 25 | "Poate… dacă…?" |
| **Combined** — needs two or more provisions, possibly in different acts | 20 | "Cine aprobă… şi în ce termen se publică?" |
| **Relation between acts** — which act amends, repeals, approves or implements which | 10 | "Ce act a modificat articolul… din…?" |
| **Not answerable** — sounds reasonable, but the packet does not contain the answer | 15 | write `not in the documents` as the answer |

Guidance:

- Do not reuse the wording of the provision in the question; ask it the way a client would.
- Avoid questions whose answer is a bare yes or no without a provision to point at.
- The "not answerable" ones matter as much as the others: they test whether the software admits it
  does not know.

---

## When you are done

Send the three files to the project owner. If, a week later, you can spare an hour, you will be
sent about fifteen of your own Sheet 1 rows again without your answers. Relabelling them tells us
how much a careful expert disagrees with themselves — which sets the ceiling for what any software
can be expected to reach.
