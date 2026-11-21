"""The onboarding pass: a first look at a new client's books, before anyone asks a question.

The detectors in `marts.data_issues` catch the kinds of mess that have been seen before. A new client's
books always hold some that have not: a site keyed as its own company, a fee billed by hand, a date
re-keyed after the fact. A person starting on new books reads them end to end first and writes down
what looks wrong; this does the same.

It reads the books only, never any question. The books are laid out in four sections (customers,
invoices, payments, deals and orders), each read by the model in one call, with the detectors' own
findings shown so it does not repeat them. Every finding must name records that exist: code checks each
reference against the warehouse and drops a finding that names none, so nothing invented reaches an
answer. Findings land in `marts.onboarding_findings` in the same shape as `marts.data_issues`; the review
step reads both. Nothing here changes a figure.

Run it once per warehouse after the models are built:
    python -m analyst.onboard [--model opus]
"""

import argparse
import json
import re
import sys

from analyst import domain as domains
from analyst.model import CommandLineModel

TABLE = "marts.onboarding_findings"
AFFECTS = ["billed", "open", "past_due", "cash", "deals", "counts"]

SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "refs": {"type": "array", "items": {"type": "string"},
                             "description": "The exact references of the records involved, copied from the books "
                                            "(invoice or payment numbers, order refs, deal names, customer names)."},
                    "customer": {"type": "string", "description": "The customer it concerns, exactly as named, or ''."},
                    "affects": {"type": "array", "items": {"type": "string", "enum": AFFECTS},
                                "description": "Which figures it can distort."},
                    "amount": {"type": "number", "description": "The amount involved in USD, or 0."},
                    "detail": {"type": "string",
                               "description": "One or two plain sentences: what is wrong, the records and amounts, and "
                                              "what it means for a figure built on them."},
                },
                "required": ["refs", "customer", "affects", "amount", "detail"],
            },
        },
    },
    "required": ["findings"],
}

SYSTEM = (
    "You are a finance analyst starting on a new client's books. You read them end to end before anyone asks "
    "a question, and write down every record that looks wrong, odd or contradictory, so that later answers "
    "can carry the right caveat. Look for anything a careful person would query: records that contradict "
    "each other or the records they link to; amounts, dates, rates, terms or values out of line with the "
    "rest; a status that does not fit what happened; a party, country, currency or name that does not fit; "
    "notes and references that say something is not what it looks like; one thing counted twice or not at "
    "all; links that are missing or point at the wrong record. Say what it means for a figure (billed, owed, "
    "late, cash, deals, counts). Skip what the detectors already list unless you can add what it means. "
    "Only report what the rows show; never guess at data that is not here. Copy references exactly."
)

SECTIONS = {
    "customers": """
        select c.customer_key, c.customer_name, p.partner_name, p.email, p.country, p.is_active,
               p.commercial_partner_id as parent_id, c.merged_into_other,
               (select count(*) from intermediate.int_invoices i where i.partner_id = p.partner_id) as invoices,
               (select round(sum(amount), 2) from intermediate.int_invoices i where i.partner_id = p.partner_id) as billed_usd,
               (select round(sum(amount_open), 2) from intermediate.int_invoices i where i.partner_id = p.partner_id) as open_usd,
               (select string_agg(distinct currency, ',') from intermediate.int_invoices i where i.partner_id = p.partner_id) as currencies,
               (select count(*) from staging.stg_deals d where d.partner_id = p.partner_id and d.is_won) as won_deals,
               (select round(avg(expected_revenue), 2) from staging.stg_deals d where d.partner_id = p.partner_id and d.is_won) as avg_won_deal,
               (select count(*) from staging.stg_orders o where o.partner_id = p.partner_id and o.state in ('sale', 'done')) as orders
        from staging.stg_partners p left join intermediate.int_customers c using (partner_id)
        order by c.customer_key, p.partner_id""",
    "invoices": """
        select s.invoice_ref as ref, s.state, s.move_type as type, s.partner_id, s.invoice_date as date, s.due_date as due,
               s.currency as cur, s.amount_in_invoice_currency as total, s.amount_untaxed_in_invoice_currency as untaxed,
               s.amount_tax_in_invoice_currency as tax, i.amount as usd, i.amount_open as open_usd, i.days_past_due as late,
               s.invoice_origin as origin, s.reference as note,
               (select string_agg(o.order_ref, ',') from staging.stg_orders o where s.invoice_id = any(o.invoice_ids)) as listed_by
        from staging.stg_invoices s left join intermediate.int_invoices i using (invoice_id)
        order by s.invoice_date, s.invoice_ref""",
    "payments": """
        select p.payment_ref as ref, p.state, p.payment_type as type, p.partner_id, p.payment_date as date,
               p.amount as usd, p.memo,
               (select string_agg(s.invoice_ref, ',') from staging.stg_invoices s where s.invoice_id = any(p.invoice_ids)) as pays
        from staging.stg_payments p order by p.payment_date, p.payment_ref""",
    "deals and orders": """
        select 'deal' as kind, d.deal_name as ref, d.partner_id, d.stage, d.is_won as won, d.is_lost as lost,
               d.expected_revenue as value, d.created_at::date as created, d.closed_on as closed, null as cur,
               null as invoices
        from staging.stg_deals d
        union all
        select 'order', o.order_ref, o.partner_id, o.state, null, null, o.amount_total, o.order_date, null, o.currency,
               (select string_agg(s.invoice_ref, ',') from staging.stg_invoices s where s.invoice_id = any(o.invoice_ids))
               || coalesce(' deal ' || (select deal_name from staging.stg_deals d where d.deal_id = o.deal_id), '')
        from staging.stg_orders o
        order by 1, 2""",
}

MAX_SECTION = 40_000  # characters of rows per call; a larger section is read in parts


def _rows(conn, sql: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [d.name for d in cur.description]
        out = []
        for r in cur.fetchall():
            row = {c: v for c, v in zip(cols, r) if v not in (None, "", False)}
            out.append(json.dumps(row, default=str, separators=(",", ":")))
        return out


def _parts(rows: list[str]) -> list[list[str]]:
    parts, cur, size = [], [], 0
    for r in rows:
        if cur and size + len(r) > MAX_SECTION:
            parts.append(cur)
            cur, size = [], 0
        cur.append(r)
        size += len(r) + 1
    return parts + ([cur] if cur else [])


def _known_refs(conn) -> set[str]:
    """Every reference a finding may name: invoices, payments, orders, deals, customers and partners."""
    q = """select invoice_ref from staging.stg_invoices union select payment_ref from staging.stg_payments
           union select order_ref from staging.stg_orders union select deal_name from staging.stg_deals
           union select partner_name from staging.stg_partners union select customer_name from intermediate.int_customers"""
    with conn.cursor() as cur:
        cur.execute(q)
        return {r[0].strip().lower() for r in cur.fetchall() if r[0]}


def valid(finding: dict, known: set[str]) -> dict | None:
    """The finding with only the references that exist in the books, or None when it names none."""
    refs = [r for r in (finding.get("refs") or []) if isinstance(r, str) and r.strip().lower() in known]
    detail = (finding.get("detail") or "").strip()
    if not refs or not detail:
        return None
    affects = [a for a in (finding.get("affects") or []) if a in AFFECTS] or ["counts"]
    return {"refs": refs, "customer": (finding.get("customer") or "").strip(), "affects": affects,
            "amount": float(finding.get("amount") or 0), "detail": detail}


def run(dom, model, say=print) -> list[dict]:
    """Reads every section of the books once and writes what looks wrong to the findings table."""
    with dom.connect() as conn:
        known = _known_refs(conn)
        detected = "\n".join(r for r in _rows(conn, f"select issue_type, left(detail, 160) as detail from {dom.issues} "
                                                     "order by issue_type") [:150]) if dom.issues else ""
        findings: list[dict] = []
        for name, sql in SECTIONS.items():
            for n, part in enumerate(_parts(_rows(conn, sql)), 1):
                prompt = (f"THE BOOKS: {name.upper()} (part {n}), one record per line, as of the as-of date\n"
                          + "\n".join(part)
                          + "\n\nWHAT THE DETECTORS ALREADY LIST (do not repeat; add meaning if you can)\n"
                          + (detected or "(none)")
                          + "\n\nList what looks wrong in this section. Empty when nothing does.")
                try:
                    out = model(SYSTEM, prompt, SCHEMA)
                except Exception as exc:  # one section failing must not lose the others
                    say(f"  {name} part {n}: failed ({type(exc).__name__})")
                    continue
                kept = [f for f in (valid(f, known) for f in out.get("findings") or []) if f]
                say(f"  {name} part {n}: {len(kept)} findings kept of {len(out.get('findings') or [])}")
                findings += kept
        with conn.cursor() as cur:
            cur.execute(f"drop table if exists {TABLE}")
            cur.execute(f"""create table {TABLE} (issue_id serial, issue_type text, entity_kind text, entity_ref text,
                            customer_key int, customer_name text, event_date date, amount numeric, affects text[],
                            detail text)""")
            for f in findings:
                cur.execute(f"insert into {TABLE} (issue_type, entity_kind, entity_ref, customer_name, amount, affects, detail) "
                            "values ('onboarding_finding', 'record', %s, %s, %s, %s, %s)",
                            (", ".join(f["refs"]), f["customer"], f["amount"], f["affects"], f["detail"]))
        if not conn.autocommit:
            conn.commit()
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--domain", default="finance")
    ap.add_argument("--model", default="opus")
    args = ap.parse_args(argv)
    dom = domains.load(args.domain)
    findings = run(dom, CommandLineModel(args.model))
    print(f"{len(findings)} findings written to {TABLE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
