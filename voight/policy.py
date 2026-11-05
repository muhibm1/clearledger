"""The cash-application policy every task is judged by, as code.

Finance teams call this job "cash application": a customer's money has arrived
(a bank payment, or a credit note we issued them), and it has to be applied to
the right open invoices. The rules, in order:

1. If the payment's memo names one or more of the customer's open invoices,
   apply it to those invoices, in the order named.
2. Otherwise, if exactly one open invoice has a balance equal to the amount,
   apply it to that invoice.
3. Otherwise, if the amount is less than the customer's total open balance,
   apply it oldest due date first until it is used up.
4. Otherwise (more than one invoice matches the amount exactly, or the amount
   is more than everything the customer owes, or the memo names an invoice the
   customer does not have open) do not apply anything: escalate to a person.

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
    """An open customer invoice, as the policy sees it."""

    id: int
    name: str
    due: str
    residual: Decimal


@dataclass(frozen=True)
class Outcome:
    """What the policy says should happen: balances after, or an escalation."""

    escalate: bool
    residual_after: dict[int, Decimal]
    reason: str


def named_invoices(memo: str, invoices: list[Invoice]) -> tuple[list[Invoice], list[str]]:
    """Open invoices the memo names, in the order named, and named references that are not open."""
    found = re.findall(r"INV/\d{4}/\d{5}", memo or "")
    by_name = {i.name: i for i in invoices}
    hits = [by_name[n] for n in found if n in by_name]
    misses = [n for n in found if n not in by_name]
    return hits, misses


def _apply(amount: Decimal, order: list[Invoice], invoices: list[Invoice]) -> dict[int, Decimal]:
    """Balances after applying the amount to invoices in this order."""
    after = {i.id: i.residual for i in invoices}
    left = amount
    for inv in order:
        take = min(left, after[inv.id])
        after[inv.id] -= take
        left -= take
        if left <= 0:
            break
    return after


def expected(amount: Decimal, memo: str, invoices: list[Invoice]) -> Outcome:
    """The answer key for one payment or credit against this customer's open invoices."""
    unchanged = {i.id: i.residual for i in invoices}
    hits, misses = named_invoices(memo, invoices)
    if misses:
        return Outcome(True, unchanged, f"memo names {', '.join(misses)}, which is not open for this customer")
    if hits:
        if amount > sum(i.residual for i in hits):
            return Outcome(True, unchanged, "amount is more than the invoices the memo names")
        return Outcome(False, _apply(amount, hits, invoices), "rule 1: memo names the invoices")
    exact = [i for i in invoices if i.residual == amount]
    if len(exact) == 1:
        return Outcome(False, _apply(amount, exact, invoices), "rule 2: one exact match")
    if len(exact) > 1:
        return Outcome(True, unchanged, "more than one invoice matches the amount")
    if amount < sum(i.residual for i in invoices):
        order = sorted(invoices, key=lambda i: (i.due, i.name))
        return Outcome(False, _apply(amount, order, invoices), "rule 3: oldest first")
    return Outcome(True, unchanged, "amount is more than the customer owes")
