"""Builders for invoices and fakes for Odoo, shared by Remit's tests."""

import datetime as dt
from decimal import Decimal

from remit.playbook import Invoice

AS_OF = dt.date(2026, 10, 27)


def inv(invoice_id=1, days=10, amount="1000.00", customer=7, name="Larkspur Foods", currency="USD",
        local=None, credit="0", unmatched="0", changed=False, dup=False, partner=None, version="v1"):
    """One overdue invoice, `days` past due on AS_OF."""
    amt = Decimal(amount)
    return Invoice(invoice_id, f"INV/2026/{invoice_id:05d}", customer, name, partner or customer,
                   AS_OF - dt.timedelta(days=days), days, currency,
                   Decimal(local) if local else amt, amt, Decimal(credit), Decimal(unmatched),
                   changed, dup, f"account.move/{invoice_id}@{version}")


class FakeOdoo:
    """Records every call, answers reads from fixed data."""

    def __init__(self, tags=None, notes=None, categories=None):
        self.calls = []
        self.tags = tags or {}             # partner id -> tag ids
        self.categories = categories or {}  # tag id -> name
        self.notes = notes or {}           # invoice id -> narration
        self.messages = []                 # (invoice id, body)
        self.activities = []               # (invoice id, summary)

    def execute(self, model, method, *args, **kw):
        self.calls.append((model, method))
        if (model, method) == ("res.partner", "read"):
            return [{"id": p, "category_id": self.tags.get(p, []), "commercial_partner_id": [p, "x"]}
                    for p in args[0]]
        if (model, method) == ("res.partner.category", "read"):
            return [{"id": t, "name": self.categories[t]} for t in args[0]]
        if (model, method) == ("account.move", "read"):
            return [{"id": i, "narration": self.notes.get(i, False), "ref": False} for i in args[0]]
        if (model, method) == ("account.move", "message_post"):
            self.messages.append((args[0][0], kw["body"]))
            return 1
        if (model, method) == ("account.move", "activity_schedule"):
            self.activities.append((args[0][0], kw["summary"]))
            return 1
        if (model, method) == ("mail.message", "search_count"):
            inv_id = args[0][1][2]
            marker = args[0][2][2]
            return sum(1 for i, b in self.messages if i == inv_id and marker in b)
        if (model, method) == ("mail.activity", "search_count"):
            inv_id = args[0][1][2]
            marker = args[0][2][2]
            return sum(1 for i, s in self.activities if i == inv_id and marker in s)
        raise AssertionError(f"unexpected call {model}.{method}")
