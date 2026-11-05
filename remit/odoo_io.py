"""Remit's only doors into Odoo: a reader with three read calls, a writer with three writes.

The writer is the allowlist. It has no generic `execute`: each allowed write is
its own method with fixed arguments, so no proposal, model reply or note can
turn into a `write`, `unlink`, payment or reconciliation call. Every note and
activity Remit writes carries a marker with the proposal id, which is how
`find_marker` tells, after a crash, whether a write already landed.
"""

from __future__ import annotations

import datetime as dt
import html

READS = {("res.partner", "read"), ("res.partner.category", "read"), ("account.move", "read"),
         ("mail.message", "search_count"), ("mail.activity", "search_count")}


class Refused(RuntimeError):
    """A call outside the allowlist was attempted."""


class Reader:
    """Read-only calls for gathering evidence. Anything else is refused."""

    def __init__(self, odoo):
        self._o = odoo

    def _call(self, model: str, method: str, *args, **kwargs):
        if (model, method) not in READS:
            raise Refused(f"{model}.{method} is not on Remit's read list")
        return self._o.execute(model, method, *args, **kwargs)

    def customer_tags(self, partner_ids: list[int]) -> list[str]:
        """The tag names on these Odoo partners and on their parent companies."""
        rows = self._call("res.partner", "read", partner_ids, ["category_id", "commercial_partner_id"])
        tag_ids = sorted({t for r in rows for t in r["category_id"]})
        parents = sorted({r["commercial_partner_id"][0] for r in rows if r["commercial_partner_id"]}
                         - set(partner_ids))
        if parents:
            tag_ids = sorted(set(tag_ids) | {t for r in self._call(
                "res.partner", "read", parents, ["category_id"]) for t in r["category_id"]})
        if not tag_ids:
            return []
        return [r["name"] for r in self._call("res.partner.category", "read", tag_ids, ["name"])]

    def invoice_notes(self, invoice_ids: list[int]) -> dict[int, str]:
        """The free text on each invoice: its terms note and its reference."""
        rows = self._call("account.move", "read", invoice_ids, ["narration", "ref"])
        out = {}
        for r in rows:
            parts = [p for p in (r.get("narration"), r.get("ref")) if p]
            out[r["id"]] = html.unescape(" ".join(str(p) for p in parts))
        return out

    def find_marker(self, invoice_id: int, marker: str, kind: str) -> bool:
        """Whether a note or activity carrying this marker is already on the invoice."""
        if kind == "note":
            return self._call("mail.message", "search_count", [
                ("model", "=", "account.move"), ("res_id", "=", invoice_id),
                ("body", "ilike", marker)]) > 0
        return self._call("mail.activity", "search_count", [
            ("res_model", "=", "account.move"), ("res_id", "=", invoice_id),
            ("summary", "ilike", marker)]) > 0


class Writer:
    """The three writes Remit may make. There is no other way for it to change Odoo."""

    def __init__(self, odoo):
        self._o = odoo

    def post_reminder(self, invoice_id: int, subject: str, body: str, marker: str) -> None:
        """Logs the approved reminder on the invoice's chatter, as an internal note."""
        # Plain text: over the API Odoo escapes the body itself, so markup would show as tags.
        self._o.execute("account.move", "message_post", [invoice_id],
                        body=f"Reminder sent by Remit {marker}\n\n{subject}\n\n{body}",
                        message_type="comment", subtype_xmlid="mail.mt_note")

    def schedule_follow_up(self, invoice_id: int, on: dt.date, summary: str, marker: str) -> None:
        """Schedules a follow-up call on the invoice."""
        self._activity(invoice_id, "mail.mail_activity_data_call", on, f"{summary} {marker}")

    def flag_for_person(self, invoice_id: int, on: dt.date, summary: str, marker: str) -> None:
        """Puts a to-do on the invoice for a person, saying why Remit held it."""
        self._activity(invoice_id, "mail.mail_activity_data_todo", on, f"{summary} {marker}")

    def _activity(self, invoice_id: int, xmlid: str, on: dt.date, summary: str) -> None:
        self._o.execute("account.move", "activity_schedule", [invoice_id],
                        act_type_xmlid=xmlid, date_deadline=on.isoformat(), summary=summary[:250])
