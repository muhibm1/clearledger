"""The collections playbook as code: stages, holds and the actions they lead to.

Everything here is a pure function of the overdue list and the evidence read
from Odoo, so the same inputs always give the same plan. docs/collections/playbook.md
is the same rules in words; a change to one is a change to the other.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal

from .screen import instructions_in

# (stage, name, first day past due, last day past due). Day 0 and earlier is
# "due within 3 days": the overdue list only holds invoices due by as_of + 3.
STAGES = [
    (0, "courtesy", -3, 0),
    (1, "friendly", 1, 7),
    (2, "firm", 8, 30),
    (3, "final", 31, 60),
    (4, "escalate", 61, 10**6),
]
STAGE_NAMES = {s: n for s, n, _, _ in STAGES}
# Days until the follow-up activity, by stage. Stage 3 names this as the hold date.
FOLLOW_UP_DAYS = {0: 3, 1: 7, 2: 7, 3: 14}
# A customer tag in Odoo that stops all customer contact. Finance sets it.
HOLD_TAG = "collections hold"


def stage_for(days_past_due: int) -> int:
    """The playbook stage for an invoice this many days past due."""
    for stage, _, first, last in STAGES:
        if first <= days_past_due <= last:
            return stage
    raise ValueError(f"not on the overdue list: {days_past_due} days past due")


@dataclass(frozen=True)
class Invoice:
    """One row of marts.overdue_invoices."""

    invoice_id: int
    invoice_ref: str
    customer_key: int
    customer_name: str
    partner_id: int
    due_date: dt.date
    days_past_due: int
    currency: str
    amount_open_invoice_currency: Decimal
    amount_open_usd: Decimal
    customer_open_credit_notes_usd: Decimal
    customer_unmatched_payments_usd: Decimal
    changed_after_posting: bool
    customer_has_duplicate_records: bool
    source_ref: str

    @property
    def stage(self) -> int:
        return stage_for(self.days_past_due)


@dataclass(frozen=True)
class Evidence:
    """What Remit read from Odoo for one customer. Free text is untrusted data."""

    customer_tags: tuple[str, ...] = ()
    invoice_notes: dict[int, str] = field(default_factory=dict)


@dataclass
class Action:
    """One thing Remit proposes. Nothing is written until it is approved and applied."""

    kind: str                 # reminder | follow_up | flag | escalate
    customer_key: int
    customer_name: str
    stage: int | None
    invoices: list[Invoice]
    target_invoice_id: int    # the Odoo invoice the note or activity is written on
    reason: str
    keys: list[str]           # what this action marks as done, one per invoice it covers
    follow_up_on: dt.date | None = None

    @property
    def idempotency_key(self) -> str:
        """The same action on the same invoice versions always has the same key."""
        return hashlib.sha256("|".join([self.kind, *sorted(self.keys)]).encode()).hexdigest()[:24]


@dataclass
class Plan:
    """Remit's decision for one customer: the actions, and what it held and why."""

    customer_key: int
    customer_name: str
    actions: list[Action]
    held: dict[int, str]      # invoice id -> reason
    notes: list[str]          # what a person should know at approval


def done_key(kind: str, invoice: Invoice, stage: int | None = None, reason: str = "") -> str:
    """The key that marks one kind of action on one invoice as done, forever."""
    if kind in ("reminder", "escalate"):
        return f"inv:{invoice.invoice_id}:stage:{stage}:{kind}"
    # A flag is raised again only when the invoice changes, so it carries the version.
    return f"inv:{invoice.invoice_id}:flag:{reason}@{invoice.source_ref}"


def plan_customer(invoices: list[Invoice], evidence: Evidence, as_of: dt.date,
                  done: set[str]) -> Plan:
    """Apply the playbook to one customer's overdue invoices."""
    if not invoices:
        raise ValueError("a customer plan needs at least one invoice")
    first = invoices[0]
    key, name = first.customer_key, first.customer_name
    if any(i.customer_key != key for i in invoices):
        raise ValueError("invoices from more than one customer")
    actions: list[Action] = []
    held: dict[int, str] = {}
    notes: list[str] = []

    def flag(inv: Invoice, reason: str) -> None:
        held[inv.invoice_id] = reason
        k = done_key("flag", inv, reason=reason)
        if k not in done:
            actions.append(Action("flag", key, name, None, [inv], inv.invoice_id, reason, [k]))

    # Customer-wide holds: no contact at all.
    tags = [t.lower() for t in evidence.customer_tags]
    customer_hold = next((t for t in tags if t.startswith(HOLD_TAG)), None)
    late = [i for i in invoices if i.days_past_due > 0]
    late_usd = sum((i.amount_open_usd for i in late), Decimal(0))
    credit = first.customer_open_credit_notes_usd + first.customer_unmatched_payments_usd
    if customer_hold:
        for inv in invoices:
            flag(inv, "customer_on_hold")
        notes.append(f"Odoo tag on the customer: {customer_hold}.")
        return Plan(key, name, actions, held, notes)
    if credit > 0 and late and credit >= late_usd:
        for inv in invoices:
            flag(inv, "credit_covers_balance")
        notes.append(f"Unused credit of ${credit:,.2f} covers the ${late_usd:,.2f} that is late. "
                     "Match it in Odoo instead of reminding.")
        return Plan(key, name, actions, held, notes)
    if credit > 0:
        notes.append(f"Unused credit of ${credit:,.2f} could be matched to these invoices.")
    if first.customer_has_duplicate_records:
        notes.append("This customer exists twice in Odoo. One reminder covers both records.")

    # Invoice holds.
    open_ = []
    for inv in invoices:
        if inv.changed_after_posting:
            flag(inv, "changed_after_posting")
        elif instructions_in(evidence.invoice_notes.get(inv.invoice_id, "")):
            flag(inv, "instruction_in_invoice_text")
        else:
            open_.append(inv)

    # The next step for what is left: one action per customer, at its most serious stage.
    todo = [(inv, inv.stage) for inv in open_
            if done_key("escalate" if inv.stage == 4 else "reminder", inv, inv.stage) not in done]
    if not todo:
        return Plan(key, name, actions, held, notes)
    stage = max(s for _, s in todo)
    covered = sorted((inv for inv, _ in todo), key=lambda i: (i.due_date, i.invoice_id))
    target = covered[0].invoice_id
    if stage == 4:
        actions.append(Action("escalate", key, name, 4, covered, target,
                              "over 60 days past due", [done_key("escalate", i, 4) if i.stage == 4
                                                        else done_key("reminder", i, i.stage)
                                                        for i in covered]))
    else:
        keys = [done_key("reminder", i, i.stage) for i in covered]
        # The reminder carries the follow-up date too: a final notice names it as the hold date.
        on = as_of + dt.timedelta(days=FOLLOW_UP_DAYS[stage])
        actions.append(Action("reminder", key, name, stage, covered, target,
                              f"{STAGE_NAMES[stage]} reminder", keys, follow_up_on=on))
        actions.append(Action("follow_up", key, name, stage, covered, target,
                              f"follow up on the {STAGE_NAMES[stage]} reminder",
                              [k + ":follow_up" for k in keys], follow_up_on=on))
    return Plan(key, name, actions, held, notes)


def plan_all(invoices: list[Invoice], evidence: dict[int, Evidence], as_of: dt.date,
             done: set[str]) -> list[Plan]:
    """Plans for every customer on the overdue list, most money late first."""
    by_customer: dict[int, list[Invoice]] = {}
    for inv in invoices:
        by_customer.setdefault(inv.customer_key, []).append(inv)
    plans = [plan_customer(rows, evidence.get(k, Evidence()), as_of, done)
             for k, rows in by_customer.items()]
    return sorted(plans, key=lambda p: -sum(
        (i.amount_open_usd for a in p.actions for i in a.invoices), Decimal(0)))
