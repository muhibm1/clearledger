# Checks

`dbt build` runs every check. Hard checks fail the build. Source-quality
checks warn: they list the records so a person can fix them in Odoo, while the
models already handle each case the safe way.

## Hard checks (fail the build)

| Check | What it proves |
| --- | --- |
| `marts_match_odoo_totals` | The finance tables bill the same total as the posted invoices in Odoo. |
| `aging_adds_up` | Every dollar owed sits in exactly one aging column (a bucket, open credit notes, or the rounding residue), and the columns add up to what is owed. Each installment of an invoice is aged by its own due date. |
| `unique`, `not_null`, `accepted_values` | One row per customer and per deal; every status is a known one. |

## Source-quality checks (warn, with the records)

| Check | The problem in Odoo | What the models do | On the demo data |
| --- | --- | --- | --- |
| `duplicate_customers` | The same company was entered twice ("Northwind Logistics" and "Northwind Logistics Inc."). | Merge them by normalized name or email (`int_customers`); a missing email never matches. | 1 merge |
| `invoice_changed_after_posting` | A posted invoice was reopened and its amount, customer, due date or invoice date changed. | Use the newest version, keep what it first said (`int_invoice_changes`), and flag it on the overdue list so the playbook holds it. | 1 invoice |
| `foreign_currency_invoices` | Invoices in euros. | Use Odoo's US dollar amount at the invoice date's rate. | 3 invoices |
| `won_deal_without_order` | A deal is won in the CRM with no order behind it. | Show it as "won, no order" in `deal_to_cash`. | 4 deals (3 from Odoo's own demo data) |
| `deleted_in_source` | A record was deleted in Odoo after it was loaded. | Leave it out of every model; raw keeps its last version. | 1 draft invoice |
| `invoiced_more_than_ordered` | An order was billed for more than it was sold for. Here, the edited invoice. | Show both amounts in `deal_to_cash`. | 1 deal |
| `installments_not_usable` | An invoice with money open whose installment lines (retainage and similar terms) are not loaded or do not add up. | Age it on the header due date, which is the last installment, and say so here. | none |
| `payments_match_invoices` | Payments recorded but not matched to an invoice, or exchange differences. | Nothing hidden: the check lists the gap per customer. | none |

## Monitors (after every run, outside dbt)

dbt checks can only compare the warehouse with itself. The monitors also ask
Odoo, so they catch what the warehouse cannot see about itself: rows that went
missing, a load that stopped, a source that changed shape. They run at the end
of every `clearledger run` and on their own with `clearledger monitor`.

| Monitor | Fails when | Caught in the drills |
| --- | --- | --- |
| `last_run` | The newest load failed, or has run for over an hour. | Odoo outage |
| `freshness` | No successful load for longer than `CLEARLEDGER_FRESHNESS_HOURS` (2). | Three hours stale |
| `volume` | A model lost more than `CLEARLEDGER_VOLUME_DROP` (20%) of its records since the last load. | |
| `schema` | A field the load reads was removed from Odoo (other changes warn). | Custom field added, then deleted |
| `source_totals` | The finance tables bill or owe a different total than Odoo's own sums. | Three invoices lost from the warehouse, which every dbt check missed (INC-004) |

## A check that found a real bug

The first version of `int_customers` matched partners by email with a window
function. Postgres puts every partner with no email in the same window, so
three unrelated records became one customer. `duplicate_customers` reported
two merges where one was expected. The fix and the rule that came out of it
are recorded as `changelens/incidents/INC-001.md` and `changelens/constraints/CON-102.yaml`.
