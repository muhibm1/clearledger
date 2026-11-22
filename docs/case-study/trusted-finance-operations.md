# Trusted finance operations: agents tested on books they had never seen

## In plain words

A finance team will only hand work to an AI agent if two things are true. The agent has to be right on
the company in front of it, not just on the demo it was built on. And it has to stop itself before it
does harm: before it spends too much, before it obeys an instruction hidden in a customer's note, and
before it reports a number without saying what is wrong with it.

I built four pieces around that idea and measured each one on data it had never seen. The rule for
every headline number below is the same: the code is frozen first, the test data is written by someone
who never saw the code, and the test is scored once. A test set that has been scored becomes practice
data, and the next claim needs a new one.

| Piece | What it does | Headline, scored once on unseen data |
|---|---|---|
| ClearLedger analyst | Answers finance questions from a company's own books, and says what is wrong with them | **93 of 100** on a new company |
| ChangeLens review agent | Reads a data-warehouse change before it merges and says what it would break | **39 of 40** real bugs caught |
| Agent Harbor | Runs agents under a daily spending cap they cannot cross | spend never above **0.90** of the cap |
| Voight | A training ground for matching customer payments to invoices | small open model **133 of 139** |

## ClearLedger: answering questions about a company's books

### What it is

ClearLedger pulls a company's sales and accounting records out of Odoo (an open-source ERP, the system
that holds orders, invoices and payments), cleans them into a warehouse, and answers plain-English
questions with SQL. Every figure traces back to the records behind it. It also drafts collection
letters for overdue invoices, and a person approves each one before anything is written back.

### How it was tested

Each test company is a separate Odoo company with about 40 customers and ten months of deals, orders,
invoices and payments. A separate author who never sees ClearLedger's code builds it and plants 12 to
15 problems in the books, of kinds no earlier company had: a deposit booked as revenue, a payment
posted to the wrong customer, a duplicate invoice. The same author writes 100 questions and answer keys
from Odoo itself, and the set is locked with a hash before any run.

The 100 questions come in three kinds:

- **Plain (36):** the figure from the books.
- **Caveat (43):** the figure is right as booked, but a careful analyst must also say what is wrong
  with it. This is the hard part, and the part that matters to a finance team.
- **Cannot tell (21):** the books do not hold the answer, and the right reply is to say so.

An Opus grader scores the answers against the locked keys.

### Results

| Company | Plain | Caveat | Cannot tell | Total |
|---|---|---|---|---|
| E | 33/36 | 22/43 | 21/21 | 76 |
| F | 34/36 | 20/43 | 20/21 | 74 |
| G | 35/36 | 18/43 | 20/21 | 73 |
| H, after adding the onboarding pass | 33/36 | 32/43 | 20/21 | 85 |
| I | 36/36 | 20/43 | 17/21 | 73 |
| **J, after the review step checks the books itself** | **35/36** | **37/43** | **21/21** | **93** |

Each row is a different company, scored once on code frozen before that company was opened.

### What moved the number

Plain questions were never the problem: about 95% on every new company. Caveats were. Fixed checks
catch the kinds of mess they were written for, and every new company brought new kinds, so the caveat
score stayed under half.

Two changes fixed that, and both copy what a careful person does on a new client.

1. **Read the books before taking questions.** The onboarding pass (`analyst/onboard.py`) has a model
   read customers, invoices, payments and deals end to end and write down what looks wrong. Every
   finding must name records that exist; code checks each reference against the warehouse and drops
   any that names none. Findings never change a figure. They sit beside it as caveats. This took
   company H to 85.
2. **Check the books before answering.** Company I still scored 73, because its problems were new
   kinds again. So the review step was redesigned: before an answer goes out, it runs up to four
   read-only queries against the records behind it, compares each with the records it links to, and
   breaks a company-wide figure down by customer. It can also decline a question the books cannot
   answer instead of deriving a figure. On company J, which nobody had seen, the frozen code scored 93.

Earlier history, for honesty: on the first three test companies, before either change, the analyst
scored 77, 76 and 55, and named 1 of 43 caveats on the third. Those runs are why the work went where
it did.

Evidence: [docs/evidence/blind-companies-2026-11.md](../evidence/blind-companies-2026-11.md), and every
company's questions, keys, runs and freeze record in the project files under `finance-90/company-<x>/`.

## ChangeLens: catching warehouse bugs before they merge

### What it is

ChangeLens reviews a change to a dbt or SQL data warehouse before it merges. Four reviewer models each
read the change with one job (row counts; values and casts; columns and references; NULLs, date
boundaries and the conventions neighbouring models share) and open the files around it. A fifth,
skeptical model re-checks each finding against the repository and keeps only what holds up. It only
reads a copy of the repository at that commit, with no history, so it cannot see the later fix.

### How it was tested

Each bug is a real defect from a public repository, found from a later fix and traced back to the
commit that introduced it. A separate labeller that never ran ChangeLens wrote the labels, and each set
was committed before it was scored. ChangeLens also reviewed ordinary commits with no known bug, to
count false alarms.

### Results

| Set | Bugs | Caught |
|---|---|---|
| The earlier fixed pattern rules, two unseen sets | 24 and 23 | 3 and 2 (about 10%) |
| Agent, fresh set 1 (13 repositories) | 31 | 23 (74%) |
| Agent, fresh set 2 (12 new repositories) | 29 | 25 (86%) |
| **Agent, fresh set 3 (15 new repositories)** | **40** | **39 (98%)** |

On set 3 a stricter mechanical score, which needs the finding on the labelled lines, agrees on 37 of 40.

The honest costs: set 3 is heavy on broken references and SQL that does not compile (27 of 40), which
the agent almost always catches, and holds only one duplicated or lost rows bug, which was the one miss.
On ordinary commits, 6 of 45 (13%) got warnings with nothing real behind them, up from 1 of 36 before
the fourth reviewer was added. Most of those were deliberate changes the agent read as mistakes.

Evidence: [muhibm1/changelens, docs/evidence/warehouse-agent-real-bugs.md](https://github.com/muhibm1/changelens/blob/main/docs/evidence/warehouse-agent-real-bugs.md).

## Agent Harbor: a spending cap an agent cannot cross

Agent Harbor runs agents behind a daily dollar cap. Each model call reserves its worst-case cost before
it goes out, so many calls at once cannot overshoot together.

| Test | Result |
|---|---|
| Two copies on one database, 20 parallel runs, three tiny caps | spend never above 0.90 of the cap |
| 20 real analyst questions, cap set | all 20 finished at 0.31 of the cap |
| 20 questions, cap far too small | 19 refused before any call |

Evidence: [muhibm1/agent-harbor, docs/evidence/](https://github.com/muhibm1/agent-harbor/tree/main/docs/evidence).

## Voight: a training ground for payment matching

Voight matches incoming payments to invoices inside a real Odoo, with traps: ties, typos in references,
and instructions hidden in payment memos (a memo is written by the customer, so an attacker can write
there). A small open model (1.5 billion parameters) was trained there, first with supervised
fine-tuning, then with a short reinforcement-learning step (GRPO).

| Model, 139 held-out tasks, scored once each | Solved |
|---|---|
| Small model, fine-tuned | 128 (92%) |
| Small model, plus 10 reinforcement-learning steps | 133 (96%) |
| Same recipe repeated with a new seed | 132 (95%) |
| Claude Haiku | 126 (91%) |
| Claude Sonnet | 138 (99%) |

On a second round of 96 attacks using eight tricks it never trained on (fake tool calls, look-alike
letters, chat markers and others), the trained model was fooled 17 times, down from 22 before the
reinforcement step. Nothing an attacker did moved money outside the customer's own account: the tools
refuse other customers' invoices.

Evidence: [voight/README.md](../../voight/README.md). The repeat run is in pull request 31.

## What I would do next

- **ClearLedger:** score one more fresh company to show 93 holds, and give the collections agent the
  same onboarding notes so its letters mention what is wrong with an account.
- **ChangeLens:** read the intent of a commit (its message and linked issue) to cut warnings on
  deliberate changes, and build a fresh set heavier on duplicated and lost rows.
- **Voight:** train on the attack kinds that still win, and serve the small model behind Agent Harbor
  with a router that sends uncertain cases to a large model.
