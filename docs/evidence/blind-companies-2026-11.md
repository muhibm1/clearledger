# Blind companies: how the analyst does on books it has never seen

Each test company is a separate Odoo 17 company with about 40 customers and ten months of deals,
orders, invoices and payments. It is built by a separate author who never sees this code, and it has
12 to 15 planted problems of kinds no earlier company had. Its 100 questions and answer keys are
written from Odoo itself and frozen with a hash before scoring:

- 36 plain questions: the figure from the books.
- 43 caveat questions: the figure is right as booked, but a careful analyst must also say what is
  wrong with it, such as a deposit counted as revenue or a payment booked to the wrong customer.
- 21 questions the loaded data cannot answer: the right reply is to say so.

## Protocol

1. Freeze the code and push it.
2. Run the platform on the new company: load from Odoo, build the warehouse, check that its totals
   match Odoo.
3. Run the onboarding pass (`python -m analyst.onboard`). It reads the books only, never the
   questions.
4. Run the analyst once on all 100 questions (Sonnet drafts and reads; from company J on, the review
   step runs on Opus). Grade once with an Opus grader against the frozen keys.
5. A company scored once becomes development data. Its misses feed the next round, and the next
   score comes from a new company.

Runs cut off by the subscription's usage limit were set aside unread and rerun in full with the same
frozen code. Each company's `runs/FREEZE.txt` records this.

## Results

| Company | Code | Plain | Caveat | Cannot tell | Total |
|---|---|---|---|---|---|
| E, scored once | cc03d7d | 33/36 | 22/43 | 21/21 | 76 |
| F, scored once | f10a5b2 | 34/36 | 20/43 | 20/21 | 74 |
| G, scored once | e6d7bd3 | 35/36 | 18/43 | 20/21 | 73 |
| H, scored once, with the onboarding pass | 308e60e | 33/36 | 32/43 | 20/21 | 85 |
| I, scored once, Opus review | 2cf9643 | 36/36 | 20/43 | 17/21 | 73 |
| J, scored once, review that checks the books itself | 361515b | 35/36 | 37/43 | 21/21 | **93** |

Without the onboarding pass, plain questions held at about 95% on every new company, but caveat
questions stayed under half. Fixed detectors catch the kinds of mess they were written for, and each
new company is required to bring new kinds.

The onboarding pass changed that. On G, as development data, it was the only change from the
frozen code, and it took G from 73 to 79. Showing its notes to the analyst while it drafts took G
to 83, and reading invoice notes for meaning took it to 87. On H, a company nobody had seen, the
frozen code scored 85.

After H, fixes to H's misses took the development companies to 90 to 95, yet fresh company I scored
73: its messes were new kinds again, and the caveats missed them. So the review step was redesigned.
It now runs on Opus and checks the books itself before an answer goes out: up to four read-only queries
of the records behind the answer, comparing each with the records it links to and breaking a
company-wide figure down by customer. It can also decline a question that needs something the books
do not record, rather than derive a figure. On I, as development data, this took 73 to 85. On J, a
company nobody had seen, the frozen code scored 93.

## Confirming the result: two more fresh companies (K and L), 2026-11-22 and 23

J's 93 was one company. Two questions remained: would a second unseen company confirm it, and how
much of the score comes from the design rather than the model? So two more companies were built
blind, by separate authors, with 12 new kinds of mess each: K, an environmental testing laboratory,
and L, a commercial beekeeping and pollination business.

**Comparison run.** Each company was also answered by Opus, the strongest model the analyst uses,
with read-only SQL on the same warehouse and none of the analyst's design: no onboarding pass, no
review step, no domain notes (`analyst/eval/plain.py`). It was written and pushed before K's
questions existed. On K a third run gave Opus read-only access to Odoo itself, limited to the
fields the platform loads.

**Grader fixed before L.** Reading K's results showed the first grader cut answers at 800
characters, which mostly hurt the longer plain-Opus answers, and gave one overall verdict from
one vote. The new grader (`eval/blind/grade_v2.py`) reads the whole answer, judges the figure and
each must-mention point separately, takes a majority of three votes, and never sees which run
wrote the answer. It was frozen, with its hash in L's `FREEZE.txt`, before L was scored. J and K
were regraded with it from their stored answers. A code check (`eval/blind/check_refs.py`) is
reported beside it: for each caveat point, the author listed record strings (an invoice number, a
customer, an amount) that any answer naming the problem would contain.

**Rework between K and L.** K's misses were studied (K then became development data). All 12 of
K's planted problems had been found by the onboarding pass; the losses came from findings that
misread a record, caveats too short to name the records, and two bugs (a customer with nothing
owed was declined instead of answered as zero, and payments lost their original currency in
staging). The changes, all general: every onboarding finding is checked against the records with
a read-only query before it is kept; findings reach an answer through the records behind its
figure, not by name; the caveat budget grows with the verified findings involved; every extracted
field reaches staging. A separate "corrected figure" sentence was tried and dropped: it cost up to
12 caveat points on development companies. Frozen at d001b3e.

| Company | Code | Grader | Plain | Caveat | Cannot tell | Total | Caveat, code check |
|---|---|---|---|---|---|---|---|
| J, analyst | 361515b | v2 (regraded) | 35/36 | 36/43 | 21/21 | 92 | not built for J |
| K, analyst | c684848 | v2 (regraded) | 34/36 | 34/43 | 21/21 | 89 | 34/43 |
| K, Opus with the warehouse | c684848 | v2 (regraded) | 34/36 | 23/43 | 21/21 | 78 | 30/43 |
| K, Opus with Odoo | c684848 | v1 | 35/36 | 25/43 | 21/21 | 81 | 26/43 |
| L, analyst | d001b3e | v2 | 35/36 | 30/43 | 21/21 | 86 | 36/43 |
| L, Opus with the warehouse | d001b3e | v2 | 36/36 | 28/43 | 18/21 | 82 | 33/43 |

K under the first grader: analyst 90, Opus with the warehouse 81.

**What it adds up to.** Over three companies nobody had seen, each scored once on frozen code, the
analyst got **267 of 300 (89%)**: 104/108 plain, 100/129 caveat, 63/63 cannot tell. One company's
score moves about 3 to 4 points between runs of the same code, so J's 93, K's 89 and L's 86 are one
result, not a trend. Opus given the same warehouse scored 78 and 82; the design adds 4 to 11 points,
almost all of it on caveats and on knowing when the books cannot answer. Scoring more fresh
companies until one lands above 93 would only pick a lucky draw, so the pooled figure is the result.

Where the gap is: naming what is wrong behind a figure on a new kind of mess. The two graders also
disagree most there (on L, 30 of 43 by the model grader, 36 of 43 by the code check), so part of
the remaining gap is in how a caveat is judged, not only in the answers.

## What the onboarding pass is

`analyst/onboard.py` does what a person starting on a new client's books does: it reads them end to
end and writes down what looks wrong before anyone asks a question. A model reads customers,
invoices, payments, and deals with orders, one section at a time. It also sees the detectors' own
list, so it can add what a flagged record means instead of repeating it.

Guardrails:

- Every finding must name records that exist in the books. Code checks each reference against the
  warehouse and drops any finding that names none.
- Findings go to `marts.onboarding_findings`. The review step reads them next to
  `marts.data_issues`, and the analyst's retrieval reads them as documents.
- Nothing changes a figure. The figure is always what the books say, and caveats sit beside it.
- The pass calls the model about five times per company, on the subscription.

## Files

The companies, questions, keys and every run are in the project folder `finance-90/company-<x>/`,
with `runs/FREEZE.txt` in each.
