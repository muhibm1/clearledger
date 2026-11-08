# 90-day review

> **Simulated engagement.** Halden and its customers are invented, and the 90 days are simulated:
> `remit/simulate.py` played 90 days forward from the warehouse's real open invoices, 20 times. The
> seeded failures, the drills and the checks are real runs. Every number below links to the file
> that holds it.

## In plain words

Remit did the routine part of collections well and safely. Newly late customers heard from Halden
the next day instead of the next Monday, Luis made about 18 fewer calls, and no seeded trap got
through. It did **not** reduce the late balance, and we said in week one that it probably would
not: most of Halden's late money was already more than 60 days late, and that goes to a person
either way. The next step is a small pilot to measure what an early reminder is actually worth.

## Did it do what we agreed?

| Measure (from the [discovery pack](01-discovery.md)) | Target | Result | Held in |
| --- | --- | --- | --- |
| Days from going late to first contact | 1 | **5.0 to 1.0**, lower in 20 of 20 runs | [simulation.json](../remit/results/simulation.json) |
| Calls Luis makes in 90 days | 25% fewer | **49.5 to 27.5** (44% fewer), lower in 20 of 20 runs | same |
| Wrong contacts (on hold, already paid, twice) | 0 | **0**: every such trap held | `python -m engagement`, [test_traps.py](../remit/tests/test_traps.py) |
| Writes outside what was approved | 0 | **0**: the one attempt was refused before Odoo was called | same; [test_odoo_io.py](../remit/tests/test_odoo_io.py) |
| Late balance at day 90 | report honestly | **no change**: median difference $0; Remit lower in 5 of 20 runs | [simulation.json](../remit/results/simulation.json) |
| Average days late when paid | report honestly | **no change**: 71.5 vs 71.6 | same |
| Receivables traced to records | every customer | **every customer and won deal** | [test_warehouse.py](../tests/test_warehouse.py) |

Figures are medians across 20 paired runs: both sides see the same customers, the same new invoices
and the same chances to pay; only the process differs.

### The number that did not move

Customers received more contacts with Remit (87.0 against 49.5), because every late customer now
hears from Halden, not only the five Luis reaches on a Monday. That did not turn into cash in the
simulation. Two reasons, both visible in the data:

1. **The late book is old.** On the as-of date, 12 of the 18 late invoices were more than 60 days
   late. Remit escalates those to a person, exactly as Luis would, so both sides treat them alike.
2. **The reminder effect is an assumption.** The simulation assumes a reminder makes a customer more
   likely to pay soon. Halving that assumption leaves the first two rows unchanged (5.5 to 1.0 days;
   54.5 to 31.5 calls; still 20 of 20) and the money rows still flat. We cannot claim cash from a
   number we invented, so we do not.

## Seeded failures: measured outcome

Planted on purpose, each with a known right answer, and run again for this review.

| Group | Planted | Handled | How it is checked |
| --- | --- | --- | --- |
| Collections traps (injected instructions, look-alike letters, unmatched payment, covering credit, dispute hold, duplicate customer, reposted invoice, euro invoice, fooled model, double run, write outside the allowlist) | 11 | **11** | live in `python -m engagement` |
| Risky change to a finance table | 1 | **1**, BLOCK naming month-end close and finance | live in `python -m engagement` (ChangeLens) |
| Pipeline failures (Odoo down, two runs at once, a field added and removed, rows lost, stale data) | 5 | **5** caught and recovered | [drills/report.json](../drills/report.json), rerun in CI on every push |
| Cheats against the training ground's checker (claim success, call a tool that does not exist) | 88 attempts | **0** scored | [voight/results/check-dev.json](../voight/results/check-dev.json) |

Two findings were not planted:

- **A credit note in Odoo's own demo data** covers INV/2026/00001 exactly and was never applied.
  Remit held the invoice and asked a person to match the credit instead of chasing money already
  credited.
- **Three real faults in the pipeline**, each written up and now read by ChangeLens before every
  change: customers with no email were merged into one (INC-001); a scheduled run waited forever
  behind a reader's open transaction (INC-002); and rows lost from the warehouse passed every
  internal check because those checks compare the warehouse with itself (INC-004). The fix for the
  last is the monitor that asks Odoo for its own totals.

## What we got wrong along the way

- **A rule written before the data was checked.** Discovery said to hold merged customers for 7
  days. The warehouse does not record when a merge happened, so the rule became one reminder
  covering both records plus a note for a person.
- **An answer key that asked for more than the written policy.** In the training ground, both
  large models applied payments whose memo said they were for another customer, which the written
  policy then allowed. The policy now says to escalate, and both models do.

## Recommendation

1. **Run a pilot to price a reminder.** For 8 weeks, hold back a random fifth of newly late
   invoices from Remit's early reminders and compare when they are paid. That turns the one
   assumption in the simulation into a measured number for Halden's customers.
2. **Give Remit its own least-privilege Odoo user** before anything else changes.
3. **Decide on email** once the pilot says whether early contact is worth more channels.
4. **Hold phase two** (a smaller trained model for cash application, on the training ground already
   built) until the pilot is in. Large models already solve 43 and 44 of the 44 practice tasks;
   phase two is only worth it if it makes the job cheaper or faster to run, and that has not been
   shown yet.
