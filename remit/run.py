"""One Remit run: read the overdue list, gather evidence, plan, draft, and queue or apply.

Modes, in the order a deployment moves through them:

- shadow: proposals are recorded with status shadow and can never be applied.
- suggest: proposals wait for a person, who approves, edits or rejects them;
  nothing is written to Odoo, so a finance lead can see what Remit would do.
- act: the same queue, and `apply` writes each approved proposal to Odoo once.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from . import draft as drafting
from .ledger import Ledger
from .playbook import Evidence, Invoice, plan_all

COLUMNS = [f for f in Invoice.__dataclass_fields__]


def overdue(conn) -> list[Invoice]:
    """Every row of marts.overdue_invoices."""
    rows = conn.execute(f"select {', '.join(COLUMNS)} from marts.overdue_invoices "
                        "order by customer_key, due_date, invoice_id").fetchall()
    return [Invoice(*r) for r in rows]


def gather(reader, invoices: list[Invoice]) -> dict[int, Evidence]:
    """Reads tags and invoice text from Odoo for every customer on the list."""
    out: dict[int, Evidence] = {}
    by_customer: dict[int, list[Invoice]] = {}
    for inv in invoices:
        by_customer.setdefault(inv.customer_key, []).append(inv)
    for key, rows in by_customer.items():
        partners = sorted({r.partner_id for r in rows})
        out[key] = Evidence(tuple(reader.customer_tags(partners)),
                            reader.invoice_notes([r.invoice_id for r in rows]))
    return out


def run(conn, reader, as_of: dt.date, mode: str, model=None) -> dict:
    """Plans every customer and records the proposals. Returns a summary."""
    if mode not in ("shadow", "suggest", "act"):
        raise ValueError(mode)
    ledger = Ledger(conn)
    invoices = overdue(conn)
    evidence = gather(reader, invoices) if reader else {}
    with conn.transaction():
        run_id = ledger.start_run(as_of, mode, "model" if model else "template")
    plans = plan_all(invoices, evidence, as_of, ledger.done_keys())
    summary = {"run_id": run_id, "mode": mode, "customers": len(plans), "invoices": len(invoices),
               "proposed": 0, "already_live": 0, "held": 0, "late_usd": "0"}
    late = Decimal(0)
    for plan in plans:
        summary["held"] += len(plan.held)
        ev = evidence.get(plan.customer_key, Evidence())
        for action in plan.actions:
            body, by, problems = None, None, []
            if action.kind == "reminder":
                body, by, problems = drafting.write(action, model)
            shown = {"tags": list(ev.customer_tags),
                     "invoice_text": {str(i.invoice_id): ev.invoice_notes.get(i.invoice_id, "")
                                      for i in action.invoices}}
            pid = ledger.propose(run_id, mode, action, plan, body, by, problems, shown)
            summary["proposed" if pid else "already_live"] += 1
            late += sum((i.amount_open_usd for i in action.invoices if i.days_past_due > 0
                         and action.kind in ("reminder", "escalate")), Decimal(0))
    conn.commit()
    summary["late_usd"] = f"{late:.2f}"
    return summary


def apply(conn, writer, reader, actor: str = "remit") -> dict:
    """Writes every approved proposal to Odoo, once each. Safe to run again after a crash."""
    ledger = Ledger(conn)
    out = {"applied": 0, "skipped": 0, "failed": 0, "recovered": 0}
    # A proposal left in applying by a crash: check Odoo for its marker before trying again.
    for p in ledger.proposals("applying"):
        if _landed(reader, p):
            ledger.finish(p["proposal_id"], True, "found in Odoo after a restart", recovered=True)
            out["recovered"] += 1
        else:
            _write(writer, p)
            ledger.finish(p["proposal_id"], True, "written after a restart", recovered=True)
            out["recovered"] += 1
    for p in ledger.proposals("approved"):
        if ledger.claim(p["proposal_id"]) is None:
            out["skipped"] += 1
            continue
        try:
            _write(writer, p)
        except Exception as exc:  # noqa: BLE001 - any failed write is recorded, then reported
            ledger.finish(p["proposal_id"], False, f"{type(exc).__name__}: {exc}")
            out["failed"] += 1
            continue
        ledger.finish(p["proposal_id"], True)
        out["applied"] += 1
    return out


def marker(p: dict) -> str:
    return f"[remit:{p['proposal_id']}]"


def _landed(reader, p: dict) -> bool:
    kind = "note" if p["kind"] == "reminder" else "activity"
    return reader.find_marker(p["target_invoice_id"], marker(p), kind)


def _write(writer, p: dict) -> None:
    """The one place a proposal turns into an Odoo write."""
    m, inv = marker(p), p["target_invoice_id"]
    today = dt.date.today()
    if p["kind"] == "reminder":
        writer.post_reminder(inv, p["subject"], p["body"], m)
    elif p["kind"] == "follow_up":
        writer.schedule_follow_up(inv, p["follow_up_on"], f"Remit: {p['reason']}", m)
    elif p["kind"] == "escalate":
        writer.flag_for_person(inv, today, f"Remit: escalate {p['customer_name']}, over 60 days late", m)
    elif p["kind"] == "flag":
        writer.flag_for_person(inv, today, f"Remit held this invoice: {p['reason'].replace('_', ' ')}", m)
    else:
        raise ValueError(p["kind"])
