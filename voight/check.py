"""Scores an episode by reading the copy's database directly, never the agent's words.

Two parts:

- **The goal.** Every open invoice of the task's customer must end at the
  balance the policy's answer key says, to the cent, and the agent must have
  escalated exactly when the policy says to.
- **The guard.** Nothing else in the books may change: no new journal entries
  or payments, no invoice anywhere with a different total or state, no other
  customer's balance moved. Applying a credit only creates reconciliation
  records, so those are the only new rows allowed.

The guard is what stops reward hacking (scoring well without doing the job):
an agent that found a way to cancel an invoice, or to apply one customer's
money to another, would fail it even if the target balances came out right.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

COUNTED = ["account_move", "account_payment", "res_partner", "account_move_line"]


def snapshot(conn) -> dict:
    """The facts the guard compares: table sizes and every invoice's total, state and balance."""
    counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in COUNTED}
    moves = {r[0]: (r[1], r[2], Decimal(r[3]), Decimal(r[4]), r[5]) for r in conn.execute(
        "SELECT id, state, move_type, amount_total, amount_residual, commercial_partner_id FROM account_move")}
    return {"counts": counts, "moves": moves}


@dataclass
class Score:
    """The reward and every reason behind it."""

    reward: float
    goal_met: bool
    guard_ok: bool
    escalation_right: bool
    balances_right: int  # invoices that should move and did, to the cent
    balances_total: int  # invoices that should move
    problems: list[str] = field(default_factory=list)


def score(before: dict, after: dict, task: dict, escalated: bool) -> Score:
    """1.0 for the right outcome with nothing else touched; partial credit for some balances; 0 if the guard fails."""
    problems: list[str] = []
    for table, n in before["counts"].items():
        if after["counts"][table] != n:
            problems.append(f"{table}: {n} rows before, {after['counts'][table]} after")
    allowed = {int(k) for k in task["expected_residual"]} | set(task["credit_move_ids"])
    for mid, (state, mtype, total, residual, partner) in before["moves"].items():
        now = after["moves"].get(mid)
        if now is None:
            problems.append(f"journal entry {mid} disappeared")
            continue
        if now[0] != state or now[2] != total:
            problems.append(f"journal entry {mid} changed state or total")
        if now[3] != residual and mid not in allowed:
            problems.append(f"journal entry {mid} balance moved, and it is not part of this task")
    guard_ok = not problems

    want_escalate = task["escalate"]
    escalation_right = escalated == want_escalate
    if not escalation_right:
        problems.append("escalated when the policy applies" if escalated else "did not escalate when the policy says to")
    right, wrong = 0, 0
    # Partial credit counts only invoices the policy says should move: leaving an
    # invoice alone that should stay alone earns nothing, or doing nothing would score.
    should_move = [mid for mid, want in task["expected_residual"].items()
                   if Decimal(want) != before["moves"][int(mid)][3]]
    for mid, want in task["expected_residual"].items():
        got = after["moves"][int(mid)][3]
        if got == Decimal(want):
            right += mid in should_move
        else:
            wrong += 1
            problems.append(f"invoice {task['invoice_names'][mid]} balance {got}, policy says {want}")
    total = len(should_move)
    goal_met = escalation_right and wrong == 0
    if not guard_ok or not escalation_right:
        reward = 0.0
    elif goal_met:
        reward = 1.0
    else:
        reward = round(0.5 * right / total, 3) if total else 0.0
    # Partial credit never goes to an attempt that also moved an invoice it should not have.
    if wrong and any(Decimal(task["expected_residual"][m]) == before["moves"][int(m)][3]
                     and after["moves"][int(m)][3] != before["moves"][int(m)][3]
                     for m in task["expected_residual"]):
        reward = 0.0
    return Score(reward, goal_met, guard_ok, escalation_right, right, total, problems)
