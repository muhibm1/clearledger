"""The only things an agent can do in an episode: five tools, each a fixed Odoo call.

Like Remit's writer, there is no generic `execute`. Reads are scoped to the
task's customer. The one write, `apply`, is Odoo's own "add outstanding credit
to this invoice" action, the same thing a bookkeeper clicks in the invoice
screen, so Odoo's accounting rules still hold. A tool never raises: a refusal
comes back as an `error` the agent can read and recover from.
"""

from __future__ import annotations

from decimal import Decimal

from edp.odoo import OdooError

SPECS = [
    {"name": "open_invoices", "args": {},
     "doc": "The customer's open invoices: reference, due date, balance owed, currency."},
    {"name": "unapplied", "args": {},
     "doc": "The customer's payments and credit notes not yet applied: credit_id, kind, amount, date, memo."},
    {"name": "find_invoice", "args": {"invoice": "an invoice reference such as INV/2026/00012"},
     "doc": "Any invoice by reference, open or not: customer, state, total, balance."},
    {"name": "apply", "args": {"credit_id": "from unapplied", "invoice": "an open invoice reference"},
     "doc": "Applies as much of that payment or credit as the invoice's balance takes."},
    {"name": "escalate", "args": {"reason": "one sentence"},
     "doc": "Hands the case to a person without applying anything further. Ends the episode."},
    {"name": "finish", "args": {"summary": "one sentence"},
     "doc": "Says the work is done. Ends the episode."},
]
NAMES = {s["name"] for s in SPECS}
ENDING = {"escalate", "finish"}


def _money(x) -> str:
    """A balance as a plain two-decimal string."""
    return f"{Decimal(str(x)).quantize(Decimal('0.01'))}"


class Tools:
    """Binds the five tools to one episode's Odoo copy and one customer."""

    def __init__(self, odoo, partner_id: int):
        self._o = odoo
        self.partner_id = partner_id

    def call(self, name: str, args: dict) -> dict:
        """Runs one tool. Unknown tools and bad arguments come back as errors."""
        if name not in NAMES:
            return {"error": f"no tool named {name!r}"}
        try:
            return getattr(self, f"_{name}")(**(args or {}))
        except TypeError as exc:
            return {"error": f"bad arguments for {name}: {exc}"}
        except OdooError as exc:
            return {"error": f"Odoo refused: {str(exc)[:300]}"}

    def _open(self) -> list[dict]:
        """Raw open invoices of this customer."""
        return self._o.execute("account.move", "search_read", [
            ("move_type", "=", "out_invoice"), ("state", "=", "posted"),
            ("payment_state", "in", ["not_paid", "partial"]),
            ("commercial_partner_id", "=", self.partner_id)],
            fields=["name", "invoice_date_due", "amount_residual", "currency_id"], order="invoice_date_due, name")

    def _open_invoices(self) -> dict:
        return {"invoices": [{"invoice": r["name"], "due": r["invoice_date_due"],
                              "balance": _money(r["amount_residual"]), "currency": r["currency_id"][1]}
                             for r in self._open()]}

    def _credits(self) -> list[dict]:
        """Raw unreconciled credit lines on this customer's receivable account."""
        return self._o.execute("account.move.line", "search_read", [
            ("partner_id.commercial_partner_id", "=", self.partner_id),
            ("account_id.account_type", "=", "asset_receivable"), ("reconciled", "=", False),
            ("parent_state", "=", "posted"), ("amount_residual", "<", 0)],
            fields=["move_id", "amount_residual", "amount_residual_currency", "date", "move_name", "ref",
                    "name", "currency_id"], order="date, id")

    def _unapplied(self) -> dict:
        out = []
        for r in self._credits():
            move = self._o.execute("account.move", "read", [r["move_id"][0]], ["move_type", "ref", "payment_reference"])[0]
            kind = "credit note" if move["move_type"] == "out_refund" else "payment"
            memo = " ".join(p for p in (move.get("ref"), move.get("payment_reference"), r.get("name")) if p)
            out.append({"credit_id": r["id"], "kind": kind,
                        "amount": _money(-r["amount_residual_currency"]), "currency": r["currency_id"][1],
                        "date": r["date"], "memo": memo})
        return {"unapplied": out}

    def _find_invoice(self, invoice: str) -> dict:
        rows = self._o.execute("account.move", "search_read", [("name", "=", invoice),
                                                               ("move_type", "in", ["out_invoice", "out_refund"])],
                               fields=["commercial_partner_id", "state", "payment_state", "amount_total",
                                       "amount_residual", "invoice_date_due"])
        if not rows:
            return {"error": f"no invoice {invoice!r}"}
        r = rows[0]
        mine = r["commercial_partner_id"][0] == self.partner_id
        return {"invoice": invoice, "customer": r["commercial_partner_id"][1] if mine else "another customer",
                "state": r["state"], "payment_state": r["payment_state"], "total": _money(r["amount_total"]),
                "balance": _money(r["amount_residual"]), "due": r["invoice_date_due"]}

    def _apply(self, credit_id, invoice: str) -> dict:
        try:
            credit_id = int(credit_id)
        except (TypeError, ValueError):
            return {"error": "credit_id must be a number from unapplied"}
        if credit_id not in {r["id"] for r in self._credits()}:
            return {"error": f"credit {credit_id} is not an unapplied payment or credit of this customer"}
        target = [r for r in self._open() if r["name"] == invoice]
        if not target:
            return {"error": f"{invoice} is not an open invoice of this customer"}
        self._o.execute("account.move", "js_assign_outstanding_line", [target[0]["id"]], credit_id)
        after = self._o.execute("account.move", "read", [target[0]["id"]], ["amount_residual"])[0]
        return {"applied": True, "invoice": invoice, "balance_now": _money(after["amount_residual"])}

    def _escalate(self, reason: str) -> dict:
        return {"escalated": True, "reason": reason}

    def _finish(self, summary: str) -> dict:
        return {"finished": True, "summary": summary}
