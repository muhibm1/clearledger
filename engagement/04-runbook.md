# Operating runbook

> **Simulated engagement.** The client and its team are invented; the schedule and commands are
> the real ones from [docs/runbook.md](../docs/runbook.md).

## In plain words

Two jobs run on their own: the pipeline every 15 minutes, and the collections agent once each
weekday morning. One person works the agent's queue each morning, which takes minutes. When
something breaks, an alert names the check, and the table below says who acts. The full commands,
with the reason behind each one, are in the main [runbook](../docs/runbook.md); this page is who
does what.

## The schedule

```cron
*/15 * * * *  cd /srv/clearledger && .venv/bin/python -m clearledger.cli run >> run.log 2>&1
30 7 * * 1-5  cd /srv/clearledger && REMIT_MODE=act .venv/bin/python -m remit daily >> remit.log 2>&1
```

`REMIT_MODE` is `shadow`, `suggest` or `act`, by rollout stage ([rollout](02-rollout.md)).

## Each weekday

| Time | Who | What | Command |
| --- | --- | --- | --- |
| 07:30 | the scheduler | Apply yesterday's approvals, plan today, run Remit's checks | `remit daily` |
| 08:00 | Luis | Work the queue: approve, edit or reject each proposal | `remit queue`, `remit show`, `remit approve`, `remit reject` |
| 08:00 | Luis | Handle flags: each one says why an invoice was held | approving a flag puts a to-do on the invoice in Odoo |
| any time | Luis or Maya | Stop contact with a customer | tag the customer "Collections hold" in Odoo |

## Each week

| Who | What | Command |
| --- | --- | --- |
| Maya | Read the week: proposals, approvals, edits, holds, checks that fired | `remit report` |
| Maya | Confirm the warehouse still matches Odoo | `clearledger monitor` (the `source_totals` line) |

## Before anyone changes a finance table

Run ChangeLens first. A change that reaches month-end close or the collections queue comes back BLOCK
and names finance as the team that must sign off.

```bash
.venv/bin/changelens sync
.venv/bin/changelens preflight --intent "<the change, in words>"
```

## When an alert fires

| Alert | Means | Who acts | First step |
| --- | --- | --- | --- |
| `last_run` (pipeline) | The newest load failed; usually Odoo was down | Ben | Check Odoo; the next good run catches up on its own |
| `freshness` | Nothing has loaded for over 2 hours | Ben | Check the scheduler, then `clearledger run` |
| `volume` | A table lost over 20% of its rows | Maya | Ask Ben whether records were archived on purpose before anyone uses the numbers |
| `source_totals` | The warehouse disagrees with Odoo | Maya | `clearledger extract --full`, then `clearledger run` |
| `schema` (warning) | A field was added or removed in Odoo | Ben | Confirm it was intended; the load carries on |
| `last_run` (Remit) | No Remit run for 26 hours | Ben | Check the scheduler and `remit.log`; run `remit daily` |
| `stalled_queue` (warning) | Proposals waited over 48 hours | Maya | Nobody is working the queue, so late customers hear nothing; cover it, or move Remit back to `shadow` |
| `stuck_apply` | A write started and never finished | Ben | `remit apply`; it checks Odoo first and never writes twice |
| `failed_apply` | Odoo refused a write | Ben | Read the reason in `remit.audit`; the next apply retries |

## Proven, not assumed

- A newcomer with only the docs fixed a broken morning (a failed load plus two invoices missing
  from the warehouse) in seven commands, about 40 seconds, without reading code. Six gaps they
  found were fixed in the runbook ([hand-off test](../docs/handoff-test.md)).
- The five pipeline drills and the live Odoo test for Remit run in CI on every push.
