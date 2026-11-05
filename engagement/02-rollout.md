# Rollout and rollback plan

> **Simulated engagement.** The client is invented; the stages, gates and commands are real.

## In plain words

Nothing goes live in one step. The warehouse comes first and only reads. The collections agent
then runs in the background for two weeks, proposing what it would do while a person compares.
Only after that may it draft for approval, and only after that may an approved reminder be written
into Odoo. Every stage has a condition for starting and a way back that takes minutes.

## The stages

| Week | Stage | What runs | Writes to Odoo | Starts when | Owner of the go |
| --- | --- | --- | --- | --- | --- |
| 1 | Discovery | Interviews, an Odoo API user | none | Kickoff | Maya |
| 2 to 3 | Data foundation | `edp run` every 15 minutes; 23 checks; monitors and alerts | none | API user works; Ben agrees the load times | Maya |
| 4 | Prove it fails safely | The five drills on a copy; hand-off test of the runbook | none, drills never run against Halden's Odoo | Warehouse matches Odoo's own totals for 5 working days | Maya |
| 5 to 6 | Remit in **shadow** | `remit daily` in shadow mode; Luis keeps working his list | none | All seeded traps pass on Halden's data | Maya |
| 7 to 8 | Remit in **suggest** | Drafts queue for Luis; a yes is recorded, nothing is written | none | Remit's picks match what Luis would do, for two weeks | Grace |
| 9 onward | Remit in **act** | Approved reminders and follow-ups written to Odoo, once each | 3 kinds: a note on the invoice, a follow-up call, a to-do for a person | Luis approves most drafts without editing; Grace signs off | Grace |
| 12 | Handover | Halden's team runs everything; we answer questions only | unchanged | Acceptance checks in the [handover](05-handover.md) pass | Maya |

Day 90 is the [review](06-90-day-review.md).

### Gates in numbers

- **Into shadow**: 12 of 12 seeded failures handled (`python -m engagement`), and the live Odoo
  test passes (a reminder approved, applied twice, and a write killed half way still leaves exactly
  one note in Odoo; `remit/tests/test_live_odoo.py`).
- **Into suggest**: over two weeks, every customer Remit would hold is one Luis also would have
  held, and every customer Luis contacted is one Remit proposed or held with a reason.
- **Into act**: Grace reviews ten drafts of each stage; no draft fails the checks; the
  `stalled_queue` check has not fired in a week (the queue is actually being worked).

## Rollback

Each stage can be undone without a developer. The commands are in [the runbook](04-runbook.md).

| What went wrong | Undo | Time | What remains |
| --- | --- | --- | --- |
| One customer should not be contacted (a dispute, a payment plan) | Tag the customer in Odoo with a tag starting "Collections hold" | next run | Nothing new is proposed for that customer |
| Drafts read badly, or the model misbehaves | Unset `REMIT_MODEL`; reminders come from the playbook's templates | next run | Queued drafts can be rejected |
| Remit should stop writing | Set `REMIT_MODE=suggest` (or `shadow`) in the scheduled job | next run | Approvals are recorded, nothing is applied |
| Remit should stop entirely | Remove the `remit daily` line from cron | immediate | Proposals, done keys and the audit log stay for review |
| A note was written that should not have been | Find it by its proposal marker in Odoo and delete it as an Odoo admin; mark the to-do or call as cancelled | minutes per note | The audit row stays; it cannot be changed or deleted |
| The warehouse is wrong | Stop the `edp run` cron; reports fall back to Odoo's own | immediate | Odoo is untouched; the warehouse only reads |
| A change to a finance table broke a report | Revert the dbt change; `edp run` rebuilds | one run | Raw history is append-only, so nothing is lost |

**What cannot be undone**: a reminder the customer has already read. That is why act mode starts
only after four weeks of shadow and suggest, why final notices are reviewed by Grace, and why
reminders go to the invoice's internal notes, not to customers' inboxes, until Halden decides
otherwise.

## Who is told what

| When | Who | What |
| --- | --- | --- |
| Before each stage | Maya, Grace, Luis | What changes for them tomorrow, and the undo |
| Every morning | Luis | The queue (`remit queue`) |
| On a failed check | Whoever the webhook reaches (a Slack or Teams channel) | The check, what it means, the runbook line |
| Weekly during rollout | Maya | Proposals, approvals, edits, holds and any check that fired (`remit report`) |
