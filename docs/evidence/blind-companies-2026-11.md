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
4. Run the analyst once on all 100 questions (Sonnet). Grade once with an Opus grader against the
   frozen keys.
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
| H, scored once, with the onboarding pass | 308e60e | 33/36 | 32/43 | 20/21 | **85** |

Without the onboarding pass, plain questions held at about 95% on every new company, but caveat
questions stayed under half. Fixed detectors catch the kinds of mess they were written for, and each
new company is required to bring new kinds.

The onboarding pass changed that. On G, as development data, it was the only change from the
frozen code, and it took G from 73 to 79. Showing its notes to the analyst while it drafts took G
to 83, and reading invoice notes for meaning took it to 87. On H, a company nobody had seen, the
frozen code scored 85.

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
