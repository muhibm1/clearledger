# Product feedback memo

> **Simulated engagement.** Written from the field to the team that builds the pipeline, Remit and
> Fathom. The client is invented; every item below is something that actually happened while
> building and running this repository, with the file that shows it.

**To:** product and engineering
**From:** the deployed engineer on the Halden collections engagement
**Ask:** prioritise items 1 to 3 before the next deployment

## In plain words

The software did its job. What slowed the engagement down, or nearly misled the client, was in the
gaps between parts: a cash number we could not honestly promise, a check that compared the
warehouse with itself, and a first run that failed with a stack trace. Each item says what
happened, what it cost, and what to build.

## 1. Remit needs a built-in way to measure its own effect

**What happened.** The 90-day result could only say "no change in the late balance", because how
much a reminder speeds up payment was an assumption inside our own simulation
([review](06-90-day-review.md)).
**Cost.** The client's first question at the review was "so what is it worth?", and the honest
answer was "we do not know yet".
**Build.** A holdout mode: Remit leaves a random share of newly late invoices to the manual process
for a set period, records which, and `remit report` compares time to payment. Every deployment then
ends with a measured number instead of a simulated one.

## 2. Comparing totals with the source system should be a default, not a fix

**What happened.** A drill deleted three invoices from the warehouse. Every one of the 23 dbt checks
still passed, because they compare the warehouse with itself (INC-004). Only a monitor that asks
Odoo for its own totals caught it.
**Cost.** Without the drill, a short warehouse would have reached month-end close.
**Build.** Ship the source totals monitor with every new source adapter, on by default, and refuse
to call a deployment "monitored" without it.

## 3. Fathom's first run should not end in a stack trace

**What happened.** On a fresh clone, `fathom sync` fails with a Python traceback when the dbt
manifest does not exist yet. The fix is one command (`dbt parse`), named only at the bottom. Also,
incident notes that name dbt sources (`source:raw.load_runs`, `source:raw.account_move`) are
reported as unresolved, so two of the four incidents are not linked to the assets they hit.
**Cost.** A newcomer's first experience of the guard is an error. Two incidents never surface in a
preflight for those sources.
**Build.** Run `dbt parse` when the manifest is missing, or print one line that says to. Resolve
`source:` ids from the manifest's sources.

## 4. Least privilege should be set in the client's system, not only in our code

**What happened.** Remit's writer can make three calls and nothing else. Odoo still grants the API
user whatever rights it has ([security note](03-security.md)).
**Build.** A setup step that creates a dedicated Odoo user and access group for Remit (post
messages and activities on invoices only), and a check in `remit watch` that fails if the user can
do more.

## 5. The playbook's rules and its test answers should come from one source

**What happened.** In the training ground, the answer key expected an escalation that the written
policy did not require. Both large models followed the written policy and were marked wrong until
the policy was fixed. Remit's playbook is kept in step with its document by hand.
**Build.** Generate the playbook document and the trap answers from the same rule table, and fail
CI when they disagree.

## 6. Rules that depend on history need the history recorded

**What happened.** Discovery asked to hold merged customers for 7 days. The warehouse knows that a
customer exists twice, but not when they were merged, so the rule had to change.
**Build.** Record the first time a source-quality check sees each problem (a duplicate, a reopened
invoice), so time-based rules become possible.

## 7. Upstream note for the Odoo adapter

Some Odoo fields are computed and not stored, such as an order's invoice list. Changing them does
not always change the record's `write_date`, so an incremental load sees the link one run late.
Today the runbook says to run a `--full` load. A cheap fix: on each run, re-read the computed links
for records whose parents changed.

## What worked and should be kept

- **The hand-off test.** A newcomer with only the docs fixed a broken morning in seven commands, and
  their six complaints improved the runbook ([write-up](../docs/handoff-test.md)). Make it a
  standard step before every handover.
- **Code makes the decisions; the model only words them.** Not one trap depended on the model
  behaving. A model talked into offering a discount was caught by the draft checks and replaced by
  the template.
- **Holds with reasons.** The unmatched credit note in Odoo's demo data was found because "credit
  covers the balance" is a hold, not a silent skip.
