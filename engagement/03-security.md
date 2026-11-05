# Security note

> **Simulated engagement.** The client is invented; every control described here is in the code
> and is tested.

## In plain words

Each part gets the least it needs. The pipeline only reads Odoo. The agents that answer questions
only read the warehouse. The collections agent can write three kinds of thing into Odoo, and only
after a person approves. A model never sees invoice notes or anything a customer typed, and even if
it is fooled, code checks every draft before a person sees it.

## Who can touch what

| Part | Reads | Writes | Enforced by |
| --- | --- | --- | --- |
| Pipeline (`edp`) | Odoo, through its JSON-RPC API: deals, customers, orders, invoices, payments | The warehouse only | Uses the API, never Odoo's database; the extractor has no write calls |
| Question tools (`edp mcp`, the analyst agent) | `marts`, `intermediate`, `staging` | nothing | Postgres role `edp_reader`, a read-only transaction, a 5-second limit per query |
| Remit, reading | Customer tags, invoice notes, and its own markers | nothing | `remit/odoo_io.py`: five allowed read calls, anything else raises before Odoo is called |
| Remit, writing | | A note on an invoice, a follow-up call, a to-do for a person | Three fixed methods and no generic call, so no reply, note or model output can become an edit, delete, payment or write-off |
| Remit's records | | `remit.proposals`, `remit.done`, `remit.audit` | The audit table rejects every update and delete (a database trigger) |
| Fathom | The dbt project and the incident notes | Its own local file | Advises only; it never writes to the warehouse or Odoo |

**One gap to close with Ben.** The code is the barrier today: Remit's writer can only make three
calls. Odoo itself grants whatever rights the API user has. Before act mode, Halden should give
Remit its own Odoo user whose access group allows posting messages and activities on invoices and
nothing else, and give the pipeline a separate read-only user. Then a bug in the code still could
not change the books.

## What a model sees

Remit asks a model for one thing: the wording of a reminder. It sends the customer's name, the
invoice numbers, the amounts in the invoice's currency, the due dates and the tone for the stage.
It does not send invoice notes, customer tags, emails, contacts or anything a customer wrote.

Code then checks the draft: every invoice named with its exact amount, no other amount, no
discount, write-off or fee, no link, a length limit. A draft that fails twice is replaced by the
playbook's own template, and the proposal says so. With no model configured, every reminder comes
from the templates.

The question tools return only rows from the modelled tables, each with the Odoo record version it
came from, so an answer can be checked against Odoo by a person (`check_citations`).

## Text from outside the company

Invoice notes, references, customer names and payment memos can be written by anyone with Odoo
access, and some come from customers. All of it is treated as data. Text that reads as an
instruction ("write this off", "ignore the playbook"), including versions using look-alike letters
from other alphabets, holds that invoice and raises a to-do for a person. The customer's other
invoices are still worked. Two seeded traps in the demo prove both cases.

## Secrets

Connection settings come from environment variables (`ODOO_URL`, `ODOO_DB`, `ODOO_USER`,
`ODOO_PASSWORD`, `EDP_DSN`). Nothing is stored in the repository; `.env` files are ignored by git.
The alert webhook URL (`EDP_ALERT_WEBHOOK`) is a secret too: anyone with it can post to the channel.

## Things that must never run against Halden's Odoo

- The failure drills. One adds and removes a custom field in Odoo; they run on a copy only.
- Voight's episodes. They run on throwaway copies of a frozen database, never the live one.
- `remit/tests/test_live_odoo.py`. It writes notes on purpose.

## What the business data is

Customer names and email addresses (the email is used to match a customer entered twice), invoice
numbers, amounts and dates. No bank details, card numbers, phone numbers or addresses are
extracted. Payments are read as amounts and dates only. The email never reaches a model.
