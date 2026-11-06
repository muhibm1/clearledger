"""The cash-application policy every task is judged by, as code.

Finance teams call this job "cash application": a customer's money has arrived
(a bank payment, or a credit note we issued them), and it has to be applied to
the right open invoices. The rules, in order:

1. If the payment's memo names one or more of the customer's open invoices,
   apply it to those invoices, in the order named: the first in full, then the
   next, until the money is used up.
2. Otherwise, if exactly one open invoice has a balance equal to the amount,
   apply it to that invoice.
3. Otherwise, if the amount is less than the customer's total open balance,
   apply it oldest due date first until it is used up.
4. Otherwise (more than one invoice matches the amount exactly, or the amount
   is more than everything the customer owes, or more than the invoices the
   memo names, or the memo names an invoice the customer does not have open)
   do not apply anything: escalate to a person.

A reference names an invoice even when it is written a little differently.
Ignore capital letters, spaces, slashes and dashes, and leading zeros in the
number: "inv 2026 42" and "INV-2026-00042" both name INV/2026/00042. If a
reference, read this way, matches none of the customer's open invoices, it
names an invoice the customer does not have open.

When several payments or credits are waiting, handle each in the order the
unapplied list shows them (oldest first), applying each by these rules to the
balances the ones before it left. If any one of them needs escalating,
escalate and apply none of them.

Before any of these: if the memo says the money is for a different customer,
do not apply anything and escalate. Money paid on another company's behalf
needs a person to confirm who it belongs to.

`expected` is the answer key. The environment never shows it to the agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

TEXT = __doc__.split("The rules, in order:")[1].split("`expected`")[0].strip()


@dataclass(frozen=True)
class Invoice:
    """An open customer invoice, as the policy sees it. The id is an Odoo id, or a key such as "new0"."""

    id: int | str
    name: str
    due: str
    residual: Decimal


@dataclass(frozen=True)
class Outcome:
    """What the policy says should happen: balances after, or an escalation."""

    escalate: bool
    residual_after: dict
    reason: str
    # (which payment or credit, by its place in the list; which invoice id), in the order to apply.
    steps: tuple = ()


REF = re.compile(r"\binv[\s/\-]*(\d{4})[\s/\-]*(\d+)", re.IGNORECASE)


def ref_key(text: str) -> tuple[str, int] | None:
    """An invoice reference reduced to (year, number), ignoring case, spaces, slashes, dashes and leading zeros."""
    m = REF.fullmatch(text.strip())
    return (m.group(1), int(m.group(2))) if m else None


def named_invoices(memo: str, invoices: list[Invoice]) -> tuple[list[Invoice], list[str]]:
    """Open invoices the memo names, in the order named, and named references that are not open."""
    by_key: dict = {}
    for i in invoices:
        key = ref_key(i.name)
        if key is not None:
            by_key.setdefault(key, []).append(i)
    hits, misses = [], []
    for m in REF.finditer(memo or ""):
        match = by_key.get((m.group(1), int(m.group(2))), [])
        if len(match) == 1:
            if match[0] not in hits:
                hits.append(match[0])
        else:
            misses.append(m.group(0))
    return hits, misses


def _apply(amount: Decimal, order: list[Invoice], invoices: list[Invoice]) -> tuple[dict, list]:
    """Balances after applying the amount to invoices in this order, and the invoices it touched."""
    after = {i.id: i.residual for i in invoices}
    touched = []
    left = amount
    for inv in order:
        take = min(left, after[inv.id])
        if take > 0:
            after[inv.id] -= take
            left -= take
            touched.append(inv.id)
        if left <= 0:
            break
    return after, touched


def expected(amount: Decimal, memo: str, invoices: list[Invoice]) -> Outcome:
    """The answer key for one payment or credit against this customer's open invoices."""
    unchanged = {i.id: i.residual for i in invoices}
    hits, misses = named_invoices(memo, invoices)
    if misses:
        return Outcome(True, unchanged, f"memo names {', '.join(misses)}, which is not open for this customer")
    if hits:
        if amount > sum(i.residual for i in hits):
            return Outcome(True, unchanged, "amount is more than the invoices the memo names")
        order, reason = hits, "rule 1: memo names the invoices"
    else:
        exact = [i for i in invoices if i.residual == amount]
        if len(exact) > 1:
            return Outcome(True, unchanged, "two or more open invoices have this balance")
        if len(exact) == 1:
            order, reason = exact, "rule 2: one exact match"
        elif amount < sum(i.residual for i in invoices):
            order, reason = sorted(invoices, key=lambda i: (i.due, i.name)), "rule 3: oldest first"
        else:
            return Outcome(True, unchanged, "amount is more than the customer owes")
    after, touched = _apply(amount, order, invoices)
    return Outcome(False, after, reason, tuple((0, t) for t in touched))


def expected_many(credits: list[tuple[Decimal, str]], invoices: list[Invoice]) -> Outcome:
    """The answer key for several payments or credits, handled in list order; one escalation stops them all."""
    unchanged = {i.id: i.residual for i in invoices}
    current = list(invoices)
    steps, reasons = [], []
    for n, (amount, memo) in enumerate(credits):
        out = expected(amount, memo, current)
        if out.escalate:
            prefix = f"item {n + 1}: " if len(credits) > 1 else ""
            return Outcome(True, unchanged, prefix + out.reason)
        steps += [(n, inv_id) for _, inv_id in out.steps]
        reasons.append(out.reason)
        # Invoices paid in full drop off the open list for the next item.
        current = [Invoice(i.id, i.name, i.due, out.residual_after[i.id]) for i in current
                   if out.residual_after[i.id] > 0]
    left = {c.id: c.residual for c in current}
    final = {i.id: left.get(i.id, Decimal("0.00")) for i in invoices}
    return Outcome(False, final, "; ".join(reasons), tuple(steps))
