# A collections engagement, start to finish

> **Simulated engagement.** Halden Industrial Supply, its people and its customers are invented.
> The Odoo they run on, the warehouse, the collections agent, the checks and every number in this
> folder are real runs on that invented company. Nothing here was used by a real client.

## What this is

A finance team was chasing late invoices by hand and could not say where its numbers came from.
This folder is the paper trail of fixing that the way a deployed engineer would: learn the job
first, build on the client's own system, roll out in stages that can be undone, measure the result
honestly, and hand it over so the client's team can run it without the builder.

The software lives in the rest of this repository. This folder adds the engagement around it.

**Run it first** (about 10 seconds after the install; no Odoo or database needed):

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m engagement
```

It plants twelve known failures and shows each one handled, then reads back the recorded runs on
the full stack. Expected last lines: `12 of 12` seeded failures, `5 of 5` drills.

## The short version

| | |
| --- | --- |
| Client (invented) | Halden Industrial Supply, a regional industrial distributor on Odoo 17 Community |
| Who asked | Maya Lindqvist, finance data lead |
| The problem | Late invoices are worked once a week, oldest first, by one person; nobody can trace a receivables number to its invoices |
| What was delivered | A finance warehouse that traces every number to Odoo records; Remit, a collections agent that drafts and waits for approval; a change guard on the finance tables; a runbook and a tested hand-off |
| What moved | A newly late invoice hears from Halden after 1 day instead of 5; the AR specialist makes about 18 fewer calls in 90 days (20 of 20 simulated runs) |
| What did not move | The late balance. Most of it was already more than 60 days late, and both ways hand that to the same person |
| Seeded failures | 12 of 12 handled live in the demo; 5 of 5 pipeline drills caught and recovered |

## Walkthrough

```
python -m engagement walkthrough [--json out.json] [--via cli]
```

Follows one finance question through four steps on one warehouse: trace a receivable that is more than 90 days late, have the analyst answer a related question with checked citations, run the change guard on dropping that column, and show the spend limit of Agent Harbor (Lodestar). Each step prints one label and the reason for it:

| Label | Meaning |
| --- | --- |
| LIVE | Ran now against the real thing: the warehouse at `EDP_DSN`, ChangeLens (Fathom), or the service at `LODESTAR_URL`. |
| STUB | Ran now with a scripted stand-in for the language model. The analyst's graph, SQL checks, execution on the warehouse and citation checks are real; only the model's two replies are scripted. |
| RECORDED | Read from a file an earlier real run wrote. The output names the file, and says what it is when it differs from the live step (the trace recording is a `revenue_by_customer` trace; the Agent Harbor snapshot is the cap-refusal drill on the echo agent with a stub model). |
| SKIPPED | Not run, with the reason (no warehouse, no analyst environment, no `dbt` or `fathom`). |

Step 2 runs in the analyst's own environment, `EDP_ANALYST_PYTHON` (default `.venv-analyst/bin/python`), because its pins break dbt. Set `LODESTAR_URL` to send the question to a running Agent Harbor service; otherwise step 4 reads `engagement/recorded/agent-harbor-cap.json`. `--via cli` has the analyst call the local `claude` command line instead of the scripted model. Exit code: 1 if a LIVE or a STUB step fails (a STUB step runs real SQL, so its failure is real); RECORDED and SKIPPED never fail the run.

Configuration is by environment variables only: `EDP_DSN` (warehouse, default as the tests), `EDP_ANALYST_PYTHON` (analyst interpreter), `LODESTAR_URL` (Agent Harbor service) and `LODESTAR_API_TOKEN` (sent as a bearer token when set). To run it on your own data, point `EDP_DSN` at a warehouse built by this repository's dbt models and create the analyst environment; the questions assume the `receivables_aging` mart.

## The pack, in the order an engagement produces it

1. [Discovery pack](01-discovery.md): who we met, the first-week questions, how the work is done
   today, and the success measures in business terms.
2. [Rollout and rollback plan](02-rollout.md): four stages, what lets each one start, and how to
   undo each one.
3. [Security note](03-security.md): what each part can read and write, what a model sees, and
   what happens to text from outside the company.
4. [Operating runbook](04-runbook.md): who does what each day, and where the commands are.
5. [Handover](05-handover.md): what Halden owns now, the acceptance checks, and what is still open.
6. [90-day review](06-90-day-review.md): the measured outcome, including the number that did not
   move.
7. [Product feedback memo](07-product-feedback.md): what this engagement taught the product team.
8. [90-second video script](08-video-script.md).

## What each part is, in the rest of the repository

| Part | Where | One line |
| --- | --- | --- |
| Pipeline and warehouse | `edp/`, `dbt/` | Reads Odoo through its API, keeps every version, builds finance tables with 23 checks |
| Trace | `edp trace` | Turns any finance number back into the invoices and payments behind it |
| Remit | `remit/` | The collections agent: plans, drafts, waits for a person, writes to Odoo once |
| Change guard | Fathom, `fathom/` | Says what a change to a finance table would break and who must sign off |
| Drills | `edp/drills.py` | Breaks the pipeline five ways on purpose and checks each is caught |
| Voight | `voight/` | A practice copy of the books for training finance agents; proposed as phase two |
