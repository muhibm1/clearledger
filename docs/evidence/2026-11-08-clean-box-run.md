# Clean-box run

Date: 2026-11-08. System: ClearLedger, run from the README on a machine that had nothing installed.

In plain words: we started from an empty Linux machine, followed the README, and ran everything
it describes. Everything ran. The numbers below are what each command printed. Nothing here
spent any model money: no model API was called.

## Label

LIVE, on this box: Python 3.11, Postgres 16, Odoo 17 (installed with `scripts/odoo-local.sh`,
HTTP 200 on its login page). Two items are not live and are marked: the engagement demo's five
drill replays are RECORDED, and the analyst tests use a scripted stand-in for the model.

## Results

| Step | Result | Of | Label |
| --- | --- | --- | --- |
| `python -m clearledger.demo` (3 min 11 s) | dbt build: 29 pass, 6 warn, 0 error, 0 skip | 35 | LIVE |
| `clearledger.cli trace revenue_by_customer northwind` | billed 49,490.00, open 26,835.00, paid 22,655.00; each equals the sum of its records | 3 totals, one customer | LIVE |
| `pytest -q` (11 s) | 134 passed, 22 skipped, 0 failed, at the commit this was run on | 156 tests | LIVE |
| analyst tests (`ANALYST_TESTS=1 ANALYST_TESTS_NEED_DB=1`, after `python -m analyst.ads.simulate`) | 55 passed | 55 | LIVE, scripted model |
| `python -m clearledger.drills` (about 2 min) | 5 passed: odoo_outage, overlapping_runs, schema_change, lost_records, stale_data | 5 drills | LIVE |
| `clearledger.cli run` | monitors OK | 4 of 4 named in the log (see below) | LIVE |
| `python -m engagement` (3.3 s) | seeded failures handled | 12 of 12 | LIVE for the 12; the 5 recorded drills in the same run are RECORDED |

The 22 skipped tests need things this box did not have: Odoo 19 for the voight episodes,
`REMIT_LIVE=1` for the live remit test, and the voice package. The test count is for that
commit; the repository has moved on since, so a later CI run reports a different number.

## What the counts count

- **35 in the dbt build** is every node dbt runs: 23 checks (dbt tests) plus 12 models. The
  README's "23 checks" counts only the checks. 6 of the 23 are source-quality checks that warn,
  so the build's 29 passes are 17 passing checks plus 12 models.
- **Monitors** are checks outside dbt that run after every build. The code defines five:
  `last_run`, `freshness`, `volume`, `schema` and `source_totals`. The `run` log above names
  four of them; I did not verify why `last_run` is not in that list, so the denominator there is
  "4 named in the log", not a count of defined monitors.

## Gaps found on the way

The README's run section did not say that a Postgres socket in `/tmp` and a role named `odoo`
are assumed, that the analyst tests need `python -m analyst.ads.simulate` first, or that Odoo
installs about 1.2 GB.
