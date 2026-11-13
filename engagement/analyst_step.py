"""Step 2 of the walkthrough: the analyst answers one finance question, SQL run on the live warehouse.

    .venv-analyst/bin/python -m engagement.analyst_step --json

Runs in the analyst's own environment (its pins break dbt, see the CI step), so the
walkthrough starts it as a subprocess. The model is `analyst.model.FakeModel` with two
scripted replies (a query, then the answer), so no model is called; the graph, the SQL
checks, the read-only execution and the citation check are the real ones.
`--via cli` swaps in the local `claude` command-line route instead (needs its sign-in).
"""

from __future__ import annotations

import argparse
import json
import sys

from clearledger.ask import AS_OF


def pick_customer(conn) -> tuple[str, str, float]:
    """The customer with the largest balance more than 90 days late: (key, name, amount)."""
    row = conn.execute("select customer_key, customer_name, late_over_90 from marts.receivables_aging "
                       "where late_over_90 > 0 order by late_over_90 desc, customer_key limit 1").fetchone()
    if row is None:
        raise LookupError("no customer has a balance more than 90 days late")
    return str(row[0]), row[1], float(row[2])


def run(via: str = "stub") -> dict:
    import psycopg

    from analyst import domain as domains
    from analyst.graph import ask, build
    from analyst.model import CommandLineModel, FakeModel

    dom = domains.load("finance")
    try:
        with psycopg.connect(dom.dsn, connect_timeout=3) as conn:
            key, name, expected = pick_customer(conn)
        with dom.connect() as conn:
            domains.read_columns(dom, conn)
    except psycopg.Error as exc:
        return {"mode": "SKIPPED", "reason": f"the warehouse is not reachable: {type(exc).__name__}"}

    question = f"How much does {name} owe us that is more than 90 days late?"
    # Each installment of an invoice is aged by its own due date, and credit notes are not aged, so the
    # amounts are the open installments of invoices that are more than 90 days past due.
    query = ("select i.invoice_ref, sum(d.amount_open) as amount_open, i.source_ref "
             "from intermediate.int_invoice_dues d join intermediate.int_invoices i using (invoice_id) "
             f"where i.customer_key = '{key}' and i.move_type = 'out_invoice' and abs(d.amount_open) >= 1 "
             f"and date '{AS_OF}' - d.due_date > 90 group by i.invoice_ref, i.source_ref order by i.invoice_ref")
    if via == "cli":
        model, label = CommandLineModel("sonnet"), "LIVE model through the local claude command-line route"
    else:
        model = FakeModel([
            {"decision": "query", "sql": query, "reason": "Open invoices more than 90 days late for this customer."},
            {"status": "answer", "answer": expected,
             "explanation": f"{name} has this much open on invoices more than 90 days past due."},
        ])
        label = "STUB model, scripted SQL"
    out = ask(build(dom, model, context=False), question, "walkthrough")
    rows = out.get("rows") or []
    total = round(sum(float(r["amount_open"]) for r in rows), 2)
    cites = out.get("citations") or {}
    problems = []
    if out.get("status") != "answer":
        problems.append(f"status {out.get('status')}: {out.get('explanation')}")
    if not cites.get("all_ok"):
        problems.append("citations did not all check out")
    if via != "cli" and abs(total - round(expected, 2)) > 0.01:
        problems.append(f"the invoices add to {total} but receivables_aging says {expected}")
    return {"mode": "STUB" if via != "cli" else "LIVE", "model": label, "question": question,
            "sql": out.get("sql"), "status": out.get("status"), "answer": out.get("answer"),
            "explanation": out.get("explanation"), "invoices": len(rows), "invoices_total": total,
            "receivables_aging_late_over_90": expected, "sources": out.get("sources"),
            "citations": {"checked": cites.get("checked", 0), "all_ok": bool(cites.get("all_ok"))},
            "ok": not problems, "problems": problems}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engagement.analyst_step")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    ap.add_argument("--via", choices=("stub", "cli"), default="stub")
    args = ap.parse_args(argv)
    result = run(args.via)
    print(json.dumps(result, indent=2, default=str) if args.json else
          "\n".join(f"{k}: {v}" for k, v in result.items()))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
