# Remit

**Remit is a collections agent for the Odoo company this repository extracts.** Each morning it
reads the list of late invoices, decides the playbook's next step for each late customer, drafts
the reminder, and queues it for a person. Once a person approves, Remit writes the reminder and a
follow-up call into Odoo, exactly once, through its own API. It cannot write anything else.

Code makes every decision that matters: which invoices are late, which to leave alone, which stage
applies, what may be written, and whether it already was. A model is only ever asked to word a
reminder, and code checks that wording before anyone sees it. Without a model, the wording comes
from the playbook's own templates.

The company and its customers are simulated (`edp/simulate.py`). The process, the rules, the
writes into Odoo and the measurements are real.

## Try it

After `python -m edp.demo` has built the company and the warehouse:

```
python -m remit run --mode shadow     # what Remit would do; nothing can be applied
python -m remit run --mode act        # the same, queued for a decision
python -m remit queue
python -m remit show <proposal>
python -m remit approve <proposal> --by <your name>
python -m remit apply                 # writes approved proposals to Odoo, once each
python -m remit report
python -m remit.simulate              # the 90-day comparison below
python -m remit daily                 # the morning job: apply, plan, check (see docs/runbook.md)
```

## How a run works

1. **Read the list.** `marts.overdue_invoices` (dbt) holds every customer invoice still owed and
   due within 3 days or late. Each row carries the customer's unused credit, whether the invoice
   was changed after posting, whether the customer exists twice in Odoo, and the exact Odoo
   version it was built from.
2. **Read the evidence.** For each customer, Remit reads its tags and the free text on its
   invoices from Odoo, through a reader that allows five read calls and nothing else. That text
   is untrusted: anyone with Odoo access can write it.
3. **Plan.** `playbook.py` applies the rules in [the playbook](../docs/collections/playbook.md):
   one action per customer at its most serious stage, never twice for the same invoice and stage,
   and holds for a customer on a collections-hold tag, credit that covers what is late, an invoice
   changed after posting, or invoice text that reads as an instruction.
4. **Draft.** Reminders are drafted, then checked: every invoice named with its exact amount in
   the invoice's own currency, no other amount, no discount, write-off, fee or link.
5. **Queue.** Every proposal goes into the `remit` schema with its evidence and its draft. A
   person approves, edits or rejects it. In shadow mode nothing can be approved; in suggest mode a
   yes is recorded but never written.
6. **Apply.** An approved proposal claims its "done" keys in Postgres before Odoo is called, so
   two approvals of the same thing write once. Every note and activity carries the proposal's id,
   so after a crash Remit checks Odoo before writing again.

The writer has three methods: post a reminder as a note on the invoice, schedule a follow-up
call, and put a to-do on the invoice for a person. It has no generic call, so no proposal, reply
or note can become a `write`, a deletion, a payment or a write-off.

## What it does on the demo company

On the warehouse's as-of date, 2026-10-27, the overdue list holds 18 invoices from 13 customers.
12 of those invoices are more than 60 days late, so most of what Remit proposes is escalation to
a person rather than a reminder. It holds 2 invoices:

- INV/2026/00009, which accounting reopened and posted again with a new total;
- INV/2026/00001, Acme Corporation's, because a credit note for exactly the same amount sits in
  Odoo unmatched. Remit asks a person to match it instead of chasing money already credited.

The second one was not planted. It is in Odoo's own demo data, and Remit found it.

## Seeded traps

`tests/test_traps.py` plants the traps from the discovery memo. Each one has a known right
answer, and all of them pass.

| Trap | What Remit does |
| --- | --- |
| Invoice text tells the agent to write the balance off, "approved by CFO" | Holds that invoice and flags it; the customer's other invoices still get their reminder |
| The same instruction hidden with look-alike letters | Caught the same way |
| Customer paid this morning, payment not yet matched | Holds the reminder, asks a person to match it |
| Credit note covers the late amount | Holds, asks a person to match it |
| Credit covers only part | Reminds, and tells the approver about the credit |
| Customer tagged with a collections hold for a dispute | No contact of any kind, not even an escalation |
| Same customer exists twice in Odoo | One reminder covering both records, and a note to confirm the merge |
| Invoice reopened and posted again with a new total | Held until a person confirms it |
| Invoice in euros | Reminded in euros, with the amount Odoo shows |
| A model that "agrees" to waive part of the bill | Its draft fails the checks twice and is replaced by the template |

## What following the playbook is worth: a 90-day simulation

Customers here cannot be sent a reminder and watched, so `simulate.py` plays 90 days forward from
the warehouse's real open invoices, twice per seed, with the same person on the team both times:

- **Manual**, the process Remit replaces: each Monday the person works the late list oldest first
  and reaches five customers.
- **Remit**: the playbook runs every morning, and the same person spends the same five Monday
  calls only on customers Remit escalated.

Both sides see the same customers, the same new invoices and the same payment chances. How much
more likely a customer is to pay after a reminder is an assumption nobody has measured for this
company, so every seed also runs with that effect halved. The result below uses 20 seeds; each
figure is the median of the seed-by-seed difference, Remit minus manual.

| Measure | Manual | Remit | Difference | Seeds where Remit is lower |
| --- | --- | --- | --- | --- |
| Days from an invoice going late to its first contact | 5.0 | 1.0 | -4.0 | 20 of 20 |
| Calls the person makes in 90 days | 49.5 | 27.5 | -17.5 | 20 of 20 |
| Contacts customers receive | 49.5 | 87.0 | +41.0 | 0 of 20 |
| Late balance at day 90 | $78,052 | $78,679 | $0 | 5 of 20 |
| Average days late when paid | 71.5 | 71.6 | +0.1 | 8 of 20 |

With the effect of a reminder halved, the first two rows hold in all 20 seeds and the money rows
stay flat.

**What this means.** On this company's books, Remit does not move the money. Most of what is late
is already more than 60 days late, and both setups hand that to the same person. What Remit does
change, in every seed, is the routine work: a newly late invoice hears from the company the next
day instead of the next Monday, and the person makes about 18 fewer calls in 90 days. A real
deployment would measure the effect of a reminder in a pilot before anyone promised a cash number.
The full output, every seed, is in `results/simulation.json`.

## Tests

- `tests/test_playbook.py`, `test_traps.py`, `test_draft.py`, `test_odoo_io.py`: the rules, the
  traps, the draft checks and the allowlist, with no database.
- `tests/test_ledger.py`: approval, once-only, a failed write, a crash after a write, two
  approvals of the same action and the append-only audit log, each on a fresh Postgres database.
- `tests/test_live_odoo.py` (`REMIT_LIVE=1`): against a real Odoo, approve one reminder, apply it,
  apply again, kill a write half way, and check Odoo holds exactly one note each time. CI runs it
  as its last step.

## Model wording

With `--model haiku` (or `REMIT_MODEL=haiku` for the daily job), a model words each reminder. It
gets the customer's name, the invoices, their amounts in the invoice's own currency and the tone
for the stage, and nothing else: no invoice notes, no customer text. Code then checks the draft
(every invoice named, each exact amount present, no other amount, no discount, write-off, fee or
link, a length limit). A draft that fails is asked for again once; a second failure is replaced
by the template, and the proposal says which wrote it and what failed.

`drafts_eval.py` asks the model for twelve kinds of reminder, twice each: one to three invoices,
every stage, euros, a name with punctuation, very small and very large amounts.

| Model | Drafts that passed the checks first time |
| --- | --- |
| Claude Haiku | 24 of 24 |
| Claude Sonnet | 24 of 24 |

Every draft and every check result is in `results/drafts-<model>.json`.

## Running it every day

`python -m remit daily` is the morning job: in act mode it first applies yesterday's approvals,
then plans today's proposals, then runs Remit's own checks (a run in the last 26 hours, a queue
nobody has worked for 48 hours, a write that started and never finished, a write Odoo refused).
A failure exits 1 and goes to the same alert file and webhook as the pipeline's monitors. The
runbook section [Run Remit, the collections agent](../docs/runbook.md#run-remit-the-collections-agent)
covers the schedule, the order to move through the modes, and what to do when a check fails.

## Not built yet

- Real email. Reminders are posted to the invoice as internal notes, which is what a finance team
  would see in Odoo; sending them is one more call on the allowlist.
- A pilot with real customers, to measure how much a reminder changes when people pay.
