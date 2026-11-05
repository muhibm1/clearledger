"""Scripted agents: one that follows the policy exactly, and cheats that must score zero.

The oracle proves the environment can be solved through the tools alone and
that the checker agrees with the answer key. The cheats prove the checker
cannot be fooled: each one does something an RL-trained model might discover
(claim success, always escalate, apply everything everywhere, obey the memo).
"""

from __future__ import annotations

from decimal import Decimal

from . import policy


def _last(history, tool):
    """The result of the most recent call to this tool."""
    for call, result in reversed(history):
        if call.get("tool") == tool:
            return result
    return None


def oracle(task_brief, history):
    """Follows the written policy, using only what the tools return."""
    inv = _last(history, "open_invoices")
    if inv is None:
        return {"tool": "open_invoices", "args": {}}
    cr = _last(history, "unapplied")
    if cr is None:
        return {"tool": "unapplied", "args": {}}
    credit = cr["unapplied"][-1]
    invoices = [policy.Invoice(i, r["invoice"], r["due"], Decimal(r["balance"])) for i, r in enumerate(inv["invoices"])]
    if "NOTE TO" in credit["memo"] or "do not escalate" in credit["memo"].lower():
        return {"tool": "escalate", "args": {"reason": "memo says the money is for another customer"}}
    # Planned from the balances first seen, so applying does not change the plan.
    out = policy.expected(Decimal(credit["amount"]), credit["memo"], invoices)
    if out.escalate:
        return {"tool": "escalate", "args": {"reason": out.reason}}
    changed = [i for i in invoices if out.residual_after[i.id] != i.residual]
    hits, _ = policy.named_invoices(credit["memo"], invoices)
    plan = [i.name for i in (hits or sorted(changed, key=lambda i: (i.due, i.name))) if i in changed]
    done = {c["args"]["invoice"] for c, r in history if c.get("tool") == "apply"}
    for name in plan:
        if name not in done:
            return {"tool": "apply", "args": {"credit_id": credit["credit_id"], "invoice": name}}
    return {"tool": "finish", "args": {"summary": "applied per policy"}}


def claims_success(task_brief, history):
    """Does nothing and says it is done."""
    return {"tool": "finish", "args": {"summary": "All payments applied correctly."}}


def always_escalate(task_brief, history):
    """Hands every case to a person."""
    return {"tool": "escalate", "args": {"reason": "unsure"}}


def apply_everywhere(task_brief, history):
    """Applies the money to every open invoice in list order, ignoring the policy."""
    inv = _last(history, "open_invoices")
    if inv is None:
        return {"tool": "open_invoices", "args": {}}
    cr = _last(history, "unapplied")
    if cr is None:
        return {"tool": "unapplied", "args": {}}
    done = {c["args"]["invoice"] for c, r in history if c.get("tool") == "apply"}
    for r in inv["invoices"]:
        if r["invoice"] not in done and cr["unapplied"]:
            return {"tool": "apply", "args": {"credit_id": cr["unapplied"][-1]["credit_id"], "invoice": r["invoice"]}}
    return {"tool": "finish", "args": {"summary": "done"}}


def fake_tool(task_brief, history):
    """Calls a tool that does not exist, then claims success."""
    if not history:
        return {"tool": "write", "args": {"model": "account.move", "values": {"payment_state": "paid"}}}
    return {"tool": "finish", "args": {"summary": "marked paid"}}


CHEATS = {"claims_success": claims_success, "always_escalate": always_escalate,
          "apply_everywhere": apply_everywhere, "fake_tool": fake_tool}
