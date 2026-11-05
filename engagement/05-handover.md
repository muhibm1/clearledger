# Handover

> **Simulated engagement.** The client and its team are invented; the acceptance checks are the
> real commands and tests in this repository.

## In plain words

At week 12 Halden's own team runs everything. This page lists what they now own, who owns each
part, the checks that had to pass before we stepped back, and what is still open, so nobody
discovers a gap by accident.

## What Halden owns

| Part | Owner | Where |
| --- | --- | --- |
| Pipeline schedule and alerts | Ben | cron on the warehouse server; `alerts.jsonl` and the webhook |
| Finance tables and their checks | Maya | `dbt/`; the checks are listed in [docs/checks.md](../docs/checks.md) |
| Collections playbook (stages, holds, wording) | Maya, with Grace's sign-off | [docs/collections/playbook.md](../docs/collections/playbook.md) and `remit/playbook.py`, kept in step |
| The daily queue | Luis | `remit queue` |
| Change rules on finance tables | Grace | `fathom/constraints/` |
| Incident notes | Maya | `fathom/incidents/`; Fathom reads them before every change |

## Acceptance checks

Each passed before we stepped back. Each can be rerun by Halden at any time.

| Check | Command or evidence | Result on the simulated company |
| --- | --- | --- |
| Seeded failures handled | `python -m engagement` | 12 of 12 |
| Warehouse matches Odoo | `edp monitor`, `source_totals` | billed and open totals equal |
| Every number traces | `pytest tests/test_warehouse.py` | every customer and every won deal adds up to its records |
| Pipeline fails loudly and recovers | `edp.drills` (on a copy) | 5 of 5 ([report](../drills/report.json)) |
| Remit writes once, even after a crash | `REMIT_LIVE=1 pytest remit/tests/test_live_odoo.py` (on a copy) | exactly one note in Odoo each time |
| Drafts pass the checks | `remit/drafts_eval.py` | 24 of 24 with each model tried |
| A newcomer can run it | [hand-off test](../docs/handoff-test.md) | broken morning fixed in seven commands |
| Changes are guarded | `fathom preflight` on dropping an aging column | BLOCK, names month-end close and finance |

## Known limits, said out loud

- **Reminders are internal notes.** They are posted on the invoice in Odoo. Sending email is one
  more call on Remit's allowlist and a decision for Grace.
- **The effect of a reminder on payment is not measured.** The 90-day result comes from a
  simulation that assumes it (see the [review](06-90-day-review.md)). A pilot measures it.
- **Odoo-side rights.** Remit's limits are in code. Halden should still give it an Odoo user that
  can only post messages and activities ([security note](03-security.md)).
- **Some Odoo links arrive one load late.** A few Odoo fields are computed and do not update the
  record's change time; a `--full` load catches up.
- **Aging is as of a set date** in `dbt/dbt_project.yml`. In production it should be the run date.

## Open items

| Item | Owner | Next step |
| --- | --- | --- |
| Separate, least-privilege Odoo users for the pipeline and for Remit | Ben | Create, then swap the environment variables |
| Decide on email | Grace | If yes: add the call to the allowlist, rerun the traps, two more weeks in suggest |
| Pilot to measure what a reminder is worth | Maya | Hold back a random fifth of newly late invoices for 8 weeks (see the review) |
| Phase two proposal: a trained, smaller model for cash application | Maya | Decision after the pilot; the training ground (Voight) is built |

## After handover

Questions answered for 30 days. No changes are made by us after week 12 unless Halden asks; any
change goes through Fathom's preflight and the same tests as before.
