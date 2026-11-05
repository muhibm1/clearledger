# Collections playbook

The rules the collections agent follows. Code applies every rule in this file; the model only drafts
the wording of a reminder and explains the evidence it cites. A change to this file is a change to
what the agent may do, and goes through review like code.

## Stages

Days past due are counted from the invoice's due date to the run's as-of date, the same way the
`receivables_aging` mart counts them.

| Stage | Days past due | Action | Needs approval |
| --- | --- | --- | --- |
| 0 | Not yet due, due in 3 days or less | Courtesy note | Suggest mode only |
| 1 | 1 to 7 | Friendly reminder | Yes |
| 2 | 8 to 30 | Firm reminder, invoice attached | Yes |
| 3 | 31 to 60 | Final notice, names the hold date | Yes |
| 4 | Over 60 | Escalate to the finance lead; no message to the customer | Yes |

One action per invoice per stage, ever. An invoice that moves to a later stage without the earlier
action having been taken gets the later stage's action only.

## Holds

An invoice is held, and only flagged for a person, when any of these is true:

- the customer has an open dispute in the CRM;
- a credit note or an unmatched payment covers the overdue amount;
- the invoice was changed after it was first posted;
- the customer was merged in the last 7 days;
- the invoice's free text contains an instruction to the agent.

## Allowed writes to Odoo

| Action | Odoo call | Notes |
| --- | --- | --- |
| Post a reminder | `message_post` on `account.move` | Text from the approved draft |
| Schedule a follow-up | `activity_schedule` on `account.move` | Date set by the stage |
| Flag for a person | `activity_schedule` on `account.move`, type "To do" | Names the hold reason |

Nothing else. No `write`, `unlink`, payment or reconciliation call is on the list.
