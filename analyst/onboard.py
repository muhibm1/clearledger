"""The onboarding pass: a first look at a new client's books, before anyone asks a question.

The detectors in `marts.data_issues` catch the kinds of mess that have been seen before. A new client's
books always hold some that have not: a site keyed as its own company, a fee billed by hand, a date
re-keyed after the fact. A person starting on new books reads them end to end first and writes down
what looks wrong; this does the same.

It reads the books only, never any question. The books are laid out in sections (customers, invoices,
payments, deals and orders), each row carrying the amounts of the records it links to (an order with
each of its invoices and their sum, a payment with what it settles, a deal with its orders' totals),
plus a section that code builds of reconciliation gaps: orders that differ from what was invoiced on
them, receipts that differ from what the invoices show as paid, payments applied to nothing or paid
out, records changed after the records linked to them, and parties whose names resemble the client's
own. The model reads each section in one call, with the detectors' own findings shown so it does not
repeat them.

Every finding is then verified. It must name records that exist, and with it the model writes one
read-only SQL check; code runs the check through the warehouse's read-only path and keeps the finding
only when the record references and amounts it states appear in the rows the check returns. A finding
that fails is rewritten once by the model from those rows and checked again, or dropped. Kept findings
carry typed record ids (invoice, payment, order, deal, customer) so the review step can join them to an
answer by the records behind its figure. Findings land in `marts.onboarding_findings` in the same shape
as `marts.data_issues`; the review step reads both. Nothing here changes a figure.

Run it once per warehouse after the models are built:
    python -m analyst.onboard [--model opus]
"""

import argparse
import json
import re
import sys

from analyst import domain as domains
from analyst import sql as sqltool
from analyst.model import CommandLineModel

TABLE = "marts.onboarding_findings"
AFFECTS = ["billed", "open", "past_due", "cash", "deals", "counts"]
# Record kinds whose references a check must return verbatim; names of parties are matched loosely.
STRICT_KINDS = ("invoice", "payment", "order", "deal")
CLIENT_PARTNER_ID = 1  # Odoo keeps the client company's own contact as partner 1
# Words in a company name too common to say two names resemble each other.
NAME_STOPWORDS = {"inc", "llc", "ltd", "limited", "corp", "corporation", "company", "group", "the", "and",
                  "holdings", "services", "service", "international", "partners", "gmbh", "sarl", "co",
                  "plc", "pty", "lp", "llp", "associates", "industries", "enterprises", "solutions"}

FINDING_PROPS = {
    "refs": {"type": "array", "items": {"type": "string"},
             "description": "The exact references of the records involved, copied from the books "
                            "(invoice or payment numbers, order refs, deal names, customer names)."},
    "customer": {"type": "string", "description": "The customer it concerns, exactly as named, or ''."},
    "affects": {"type": "array", "items": {"type": "string", "enum": AFFECTS},
                "description": "Which figures it can distort."},
    "amount": {"type": "number", "description": "The amount involved in USD, or 0."},
    "detail": {"type": "string",
               "description": "One or two plain sentences: what is wrong, the records and amounts exactly as "
                              "the rows give them, and what it means for a figure built on them."},
    "check_sql": {"type": "string",
                  "description": "One read-only PostgreSQL SELECT over the marts, intermediate and staging "
                                 "schemas that returns the rows proving this finding: every record it names, "
                                 "with the amounts and dates it states."},
}
SCHEMA = {
    "type": "object",
    "properties": {"findings": {"type": "array", "items": {"type": "object", "properties": FINDING_PROPS,
                                                           "required": list(FINDING_PROPS)}}},
    "required": ["findings"],
}
REWRITE_SCHEMA = {
    "type": "object",
    "properties": {"rewrites": {"type": "array", "items": {
        "type": "object",
        "properties": {"index": {"type": "integer"},
                       "keep": {"type": "boolean", "description": "false when the rows do not bear it out"},
                       **FINDING_PROPS},
        "required": ["index", "keep", *FINDING_PROPS]}}},
    "required": ["rewrites"],
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
    "Each row shows the amounts of the records it links to: compare a record with those amounts, never "
    "assume two linked records agree, and when two of them disagree say which record holds which amount. "
    "Only report what the rows show; never guess at data that is not here. Copy references and amounts "
    "exactly. Every finding comes with one SELECT that returns the records it names with the amounts it "
    "states; code runs it and drops a finding the rows do not bear out."
)

SECTIONS = {
    "customers": """
        select c.customer_key, c.customer_name, p.partner_id, p.partner_name, p.email, p.country, p.is_active,
               p.commercial_partner_id as parent_id, c.merged_into_other,
               (select count(*) from intermediate.int_invoices i where i.partner_id = p.partner_id) as invoices,
               (select round(sum(amount), 2) from intermediate.int_invoices i where i.partner_id = p.partner_id) as billed_usd,
               (select round(sum(amount_open), 2) from intermediate.int_invoices i where i.partner_id = p.partner_id) as open_usd,
               (select string_agg(distinct currency, ',') from intermediate.int_invoices i where i.partner_id = p.partner_id) as currencies,
               (select round(sum(amount), 2) from staging.stg_payments y where y.partner_id = p.partner_id and y.state = 'posted') as payments_usd,
               (select count(*) from staging.stg_deals d where d.partner_id = p.partner_id and d.is_won) as won_deals,
               (select round(sum(expected_revenue), 2) from staging.stg_deals d where d.partner_id = p.partner_id and d.is_won) as won_deal_value,
               (select round(avg(expected_revenue), 2) from staging.stg_deals d where d.partner_id = p.partner_id and d.is_won) as avg_won_deal,
               (select count(*) from staging.stg_orders o where o.partner_id = p.partner_id and o.state in ('sale', 'done')) as orders,
               (select round(sum(amount_total), 2) from staging.stg_orders o where o.partner_id = p.partner_id and o.state in ('sale', 'done')) as order_total
        from staging.stg_partners p left join intermediate.int_customers c using (partner_id)
        order by c.customer_key, p.partner_id""",
    "invoices": """
        select s.invoice_ref as ref, s.state, s.move_type as type, s.partner_id, s.invoice_date as date, s.due_date as due,
               s.currency as cur, s.amount_in_invoice_currency as total, s.amount_untaxed_in_invoice_currency as untaxed,
               s.amount_tax_in_invoice_currency as tax, i.amount as usd, i.amount_open as open_usd, i.days_past_due as late,
               s.invoice_origin as origin, s.reference as note,
               (select string_agg(o.order_ref || ' total ' || o.amount_total || ' ' || coalesce(o.currency, ''), '; ')
                  from staging.stg_orders o where s.invoice_id = any(o.invoice_ids)) as on_order,
               (select string_agg(p.payment_ref || ' ' || coalesce(p.amount_in_payment_currency, p.amount) || ' '
                                  || coalesce(p.currency, ''), '; ')
                  from staging.stg_payments p where s.invoice_id = any(p.invoice_ids) and p.state = 'posted') as paid_by
        from staging.stg_invoices s left join intermediate.int_invoices i using (invoice_id)
        order by s.invoice_date, s.invoice_ref""",
    "payments": """
        select p.payment_ref as ref, p.state, p.payment_type as type, p.partner_id, pt.partner_name as party,
               p.payment_date as date, p.currency as cur, p.amount_in_payment_currency as paid, p.amount as usd, p.memo,
               (select string_agg(s.invoice_ref || ' ' || s.amount_in_invoice_currency || ' ' || coalesce(s.currency, '')
                                  || ' of partner ' || s.partner_id, '; ')
                  from staging.stg_invoices s where s.invoice_id = any(p.invoice_ids)) as settles,
               (select round(sum(s.amount_in_invoice_currency), 2) from staging.stg_invoices s
                 where s.invoice_id = any(p.invoice_ids)) as settled_invoices_total
        from staging.stg_payments p left join staging.stg_partners pt using (partner_id)
        order by p.payment_date, p.payment_ref""",
    "deals and orders": """
        select 'deal' as kind, d.deal_name as ref, d.partner_id, d.stage, d.is_won as won, d.is_lost as lost,
               d.expected_revenue as value, d.probability, d.created_at::date as created, d.closed_on as closed,
               null as cur,
               (select string_agg(o.order_ref || ' ' || o.state || ' ' || o.amount_total || ' ' || coalesce(o.currency, ''), '; ')
                  from staging.stg_orders o where o.deal_id = d.deal_id) as orders,
               (select round(sum(o.amount_total), 2) from staging.stg_orders o
                 where o.deal_id = d.deal_id and o.state in ('sale', 'done')) as orders_total,
               null as invoices, null as invoices_total
        from staging.stg_deals d
        union all
        select 'order', o.order_ref, o.partner_id, o.state, null, null, o.amount_total, null, o.order_date, null,
               o.currency,
               (select 'deal ' || deal_name from staging.stg_deals d where d.deal_id = o.deal_id), null,
               (select string_agg(s.invoice_ref || ' ' || s.move_type || ' ' || s.amount_in_invoice_currency || ' '
                                  || coalesce(s.currency, ''), '; ')
                  from staging.stg_invoices s where s.invoice_id = any(o.invoice_ids)),
               (select round(sum(case when s.move_type = 'out_refund' then -1 else 1 end * s.amount_in_invoice_currency), 2)
                  from staging.stg_invoices s where s.invoice_id = any(o.invoice_ids) and s.state = 'posted')
        from staging.stg_orders o
        order by 1, 2""",
}

# Reconciliation gaps, built by code: places where two records that should agree do not.
GAPS = {
    "order differs from what was invoiced on it": """
        with inv as (
            select o.order_id,
                   round(sum(case when s.move_type = 'out_refund' then -1 else 1 end * s.amount_in_invoice_currency)
                         filter (where s.state = 'posted' and s.currency = o.currency), 2) as invoiced,
                   string_agg(s.invoice_ref || ' ' || s.amount_in_invoice_currency || ' ' || coalesce(s.currency, '')
                              || ' dated ' || coalesce(s.invoice_date::text, '?'), '; ') as invoices,
                   max(s.source_version) as invoices_last_changed
            from staging.stg_orders o left join staging.stg_invoices s on s.invoice_id = any(o.invoice_ids)
            group by o.order_id)
        select o.order_ref, pt.partner_name as party, o.order_date, o.currency, o.amount_total as order_total,
               coalesce(inv.invoiced, 0) as invoiced, o.amount_total - coalesce(inv.invoiced, 0) as not_invoiced,
               inv.invoices,
               (inv.invoices_last_changed is not null and o.source_version > inv.invoices_last_changed) as order_changed_after_its_invoices
        from staging.stg_orders o join inv using (order_id) left join staging.stg_partners pt using (partner_id)
        where o.state in ('sale', 'done') and abs(o.amount_total - coalesce(inv.invoiced, 0)) > 0.01
        order by o.order_ref""",
    "cash received differs from what the invoices show as paid": """
        select customer_name, invoiced, paid, cash_received, cash_refunded, open,
               round(cash_received - paid, 2) as cash_not_on_invoices
        from marts.revenue_by_customer
        where abs(coalesce(cash_received, 0) - coalesce(paid, 0)) > 1
        order by abs(coalesce(cash_received, 0) - coalesce(paid, 0)) desc""",
    "payment applied to no invoice, or paid out": """
        select p.payment_ref, pt.partner_name as party, p.payment_type, p.state, p.payment_date, p.currency,
               p.amount_in_payment_currency as paid, p.amount as usd, p.memo,
               cardinality(p.invoice_ids) = 0 as applied_to_nothing
        from staging.stg_payments p left join staging.stg_partners pt using (partner_id)
        where cardinality(p.invoice_ids) = 0 or p.payment_type = 'outbound'
        order by p.payment_date""",
    "deal value differs from its confirmed orders": """
        select d.deal_name, pt.partner_name as party, d.stage, d.is_won, d.is_lost, d.expected_revenue as deal_value,
               round(sum(o.amount_usd), 2) as orders_total_usd,
               string_agg(o.order_ref || ' ' || o.amount_total || ' ' || o.currency, '; ') as orders
        from staging.stg_deals d join intermediate.int_orders o on o.deal_id = d.deal_id and o.state in ('sale', 'done')
        left join staging.stg_partners pt on pt.partner_id = d.partner_id
        group by d.deal_id, d.deal_name, pt.partner_name, d.stage, d.is_won, d.is_lost, d.expected_revenue
        having abs(sum(o.amount_usd) - d.expected_revenue) > greatest(1, 0.02 * abs(d.expected_revenue))
               or not d.is_won
        order by d.deal_name""",
    "invoice changed after it was posted": """
        select invoice_ref, customer_name, amount, changed_fields, first_posted_due_date, due_date, first_posted_customer
        from intermediate.int_invoices where changed_after_posting order by invoice_ref""",
}

MAX_SECTION = 40_000  # characters of rows per call; a larger section is read in parts
CHECK_ROWS_SHOWN = 40


def _rows(conn, sql: str, params=None) -> list[str]:
    """Each row of a query as one compact JSON line, with empty values left out."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        out = []
        for r in cur.fetchall():
            row = {c: v for c, v in zip(cols, r) if v not in (None, "", False)}
            out.append(json.dumps(row, default=str, separators=(",", ":")))
        return out


def _parts(rows: list[str]) -> list[list[str]]:
    """Splits a section's rows into parts small enough for one call."""
    parts, cur, size = [], [], 0
    for r in rows:
        if cur and size + len(r) > MAX_SECTION:
            parts.append(cur)
            cur, size = [], 0
        cur.append(r)
        size += len(r) + 1
    return parts + ([cur] if cur else [])


def client_name(conn) -> str:
    """The client company's own name, from its own contact in the books, or ''."""
    with conn.cursor() as cur:
        cur.execute("select partner_name from staging.stg_partners where partner_id = %s", (CLIENT_PARTNER_ID,))
        got = cur.fetchone()
    return (got[0] or "") if got else ""


def name_words(name: str) -> list[str]:
    """The distinctive words of a company name: four letters or more, legal and generic words left out."""
    return [w for w in re.findall(r"[a-z]{4,}", (name or "").lower()) if w not in NAME_STOPWORDS]


def _client_lookalikes(conn) -> list[str]:
    """Parties, emails, memos and notes that carry a distinctive word of the client's own name."""
    own = client_name(conn)
    words = name_words(own)
    if not words:
        return []
    pats = [f"%{w}%" for w in words]
    sql = """
        select 'party' as what, partner_name as text, email, partner_id from staging.stg_partners
         where partner_id <> %(me)s and (lower(partner_name) like any(%(p)s) or lower(coalesce(email, '')) like any(%(p)s))
        union all
        select 'payment ' || payment_ref || ' ' || coalesce(amount_in_payment_currency, amount) || ' '
               || coalesce(currency, '') || ' from partner ' || coalesce(partner_id::text, 'none'), memo, null, partner_id
          from staging.stg_payments where lower(coalesce(memo, '')) like any(%(p)s)
        union all
        select 'invoice ' || invoice_ref || ' ' || amount_in_invoice_currency || ' ' || coalesce(currency, ''),
               reference, null, partner_id
          from staging.stg_invoices where lower(coalesce(reference, '')) like any(%(p)s)"""
    rows = _rows(conn, sql, {"me": CLIENT_PARTNER_ID, "p": pats})
    return [json.dumps({"client_company": own})] + rows if rows else []


def gap_rows(conn, say=print) -> list[str]:
    """The reconciliation-gaps section: one line per gap, each labelled with the check that found it."""
    out = []
    for label, sql in GAPS.items():
        try:
            rows = _rows(conn, sql)
        except Exception as exc:  # a warehouse without one of these tables still gets the others
            say(f"  gap check '{label}' skipped ({type(exc).__name__})")
            if not conn.autocommit:
                conn.rollback()
            continue
        out += [json.dumps({"gap": label, **json.loads(r)}, separators=(",", ":")) for r in rows]
    for r in _client_lookalikes(conn):
        out.append(json.dumps({"gap": "resembles the client company's own name", **json.loads(r)},
                              separators=(",", ":")))
    return out


KNOWN_SQL = """
    select 'invoice', s.invoice_id, s.invoice_ref, c.customer_key from staging.stg_invoices s
      left join intermediate.int_customers c using (partner_id)
    union all select 'payment', p.payment_id, p.payment_ref, c.customer_key from staging.stg_payments p
      left join intermediate.int_customers c using (partner_id)
    union all select 'order', o.order_id, o.order_ref, c.customer_key from staging.stg_orders o
      left join intermediate.int_customers c using (partner_id)
    union all select 'deal', d.deal_id, d.deal_name, c.customer_key from staging.stg_deals d
      left join intermediate.int_customers c using (partner_id)
    union all select 'partner', p.partner_id, p.partner_name, c.customer_key from staging.stg_partners p
      left join intermediate.int_customers c using (partner_id)
    union all select distinct 'customer', customer_key, customer_name, customer_key from intermediate.int_customers"""


def _known(conn) -> dict[str, list[dict]]:
    """Every reference a finding may name, lower-cased, with the typed records it stands for."""
    with conn.cursor() as cur:
        cur.execute(KNOWN_SQL)
        out: dict[str, list[dict]] = {}
        for kind, rid, ref, key in cur.fetchall():
            if ref:
                out.setdefault(ref.strip().lower(), []).append({"kind": kind, "id": rid, "ref": ref, "customer_key": key})
        return out


def valid(finding: dict, known) -> dict | None:
    """The finding with only the references that exist in the books, or None when it names none."""
    refs = [r for r in (finding.get("refs") or []) if isinstance(r, str) and r.strip().lower() in known]
    detail = (finding.get("detail") or "").strip()
    if not refs or not detail:
        return None
    affects = [a for a in (finding.get("affects") or []) if a in AFFECTS] or ["counts"]
    out = {"refs": refs, "customer": (finding.get("customer") or "").strip(), "affects": affects,
           "amount": float(finding.get("amount") or 0), "detail": detail}
    if (finding.get("check_sql") or "").strip():
        out["check_sql"] = finding["check_sql"].strip()
    return out


def typed_records(refs: list[str], known: dict) -> list[dict]:
    """The typed records (kind, id, ref) behind a finding's references, without repeats."""
    out, seen = [], set()
    for r in refs:
        for rec in known.get(r.strip().lower(), []) if isinstance(known, dict) else []:
            k = (rec["kind"], rec["id"])
            if k not in seen:
                seen.add(k)
                out.append(rec)
    return out


# --- verification: the finding's references and amounts must appear in the rows its check returns ---

AMOUNT = re.compile(r"(?<![\w/.-])-?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d{1,2}|\d{3,})(?![\w%/-])")
# A number followed by one of these is a count or a span, not money.
NOT_MONEY = re.compile(r"(?i)^\s*(?:%|x\b|times|percent|days?|weeks?|months?|years?|hours?|invoices?|deals?|orders?|"
                       r"payments?|customers?|records?|rows?|lines?|installments?)")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def stated_amounts(text: str, refs: list[str] = ()) -> list[float]:
    """The money amounts a finding states, leaving out record numbers, dates, years and percentages."""
    t = text or ""
    for r in sorted(refs or [], key=len, reverse=True):
        t = re.sub(re.escape(r), " ", t, flags=re.I)
    t = DATE.sub(" ", t)
    t = re.sub(r"\b[A-Z]{2,}[A-Z0-9]*/\d{4}/\d+\b|\b[A-Z]{1,3}\d{4,}\b|\b[A-Z]+-\d+\b", " ", t)
    out = []
    for m in AMOUNT.finditer(t):
        tok = m.group(0)
        v = float(tok.replace(",", ""))
        if "," not in tok and "." not in tok and 1990 <= v <= 2100:
            continue  # a year
        if NOT_MONEY.match(t[m.end():]):
            continue
        out.append(v)
    return out


def _row_values(rows: list[dict]) -> tuple[list[float], str]:
    """Every number in the rows (cells and numbers written inside text cells), and the rows as text."""
    nums, text = [], []
    for r in rows:
        for v in r.values():
            if isinstance(v, bool) or v is None:
                continue
            if isinstance(v, (int, float)) or type(v).__name__ == "Decimal":
                nums.append(float(v))
            else:
                s = str(v)
                text.append(s)
                nums += [float(x.replace(",", "")) for x in re.findall(r"-?\d[\d,]*\.?\d*", DATE.sub(" ", s))
                         if re.search(r"\d", x) and x.replace(",", "").replace("-", "").replace(".", "").isdigit()]
        text.append(json.dumps(r, default=str))
    return nums, "\n".join(text).lower()


def _close(a: float, b: float) -> bool:
    """Two amounts agree to the cent, or within 0.2% for a rounded figure."""
    return abs(a - b) <= max(0.011, 0.002 * abs(a))


def supported(amount: float, nums: list[float], columns: list[float] = ()) -> bool:
    """An amount is borne out by the rows: a value in them, a column total, or the sum or difference of two."""
    a = abs(amount)
    if a < 0.005:
        return True
    pool = [abs(x) for x in nums] + [abs(x) for x in columns]
    if any(_close(a, x) for x in pool):
        return True
    cents = {round(x * 100) for x in pool}
    for x in pool:
        for target in (a - x, x - a, a + x):
            c = round(target * 100)
            if c > 0 and any(k in cents for k in (c - 1, c, c + 1)) and abs(target) > 0.005:
                if target != a:
                    return True
    return False


def verify(finding: dict, rows: list[dict], known=None) -> tuple[bool, list[str]]:
    """Whether the rows a finding's check returned bear out its record references and amounts, and what
    is missing when they do not."""
    if not rows:
        return False, ["the check returned no rows"]
    nums, text = _row_values(rows)
    numeric_cols: dict = {}
    for r in rows:
        for k, v in r.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) or type(v).__name__ == "Decimal":
                numeric_cols[k] = numeric_cols.get(k, 0.0) + float(v)
    missing = []
    for ref in finding.get("refs") or []:
        kinds = {rec["kind"] for rec in (known or {}).get(ref.strip().lower(), [])} if isinstance(known, dict) else set()
        strict = not kinds or bool(kinds & set(STRICT_KINDS))
        if strict and ref.strip().lower() not in text:
            missing.append(ref)
    amounts = stated_amounts(finding.get("detail", ""), finding.get("refs") or [])
    if finding.get("amount"):
        amounts.append(float(finding["amount"]))
    for a in amounts:
        if not supported(a, nums, list(numeric_cols.values())):
            missing.append(f"{a:,.2f}")
    return not missing, missing


def _shown(rows: list[dict]) -> str:
    """A check's rows as text for the model."""
    return json.dumps(rows[:CHECK_ROWS_SHOWN], default=str)


def check_all(conn, dom, findings: list[dict], known) -> tuple[list[dict], list[dict]]:
    """Runs each finding's check; returns the verified findings and the failed ones with their rows."""
    ok, failed = [], []
    for f in findings:
        rows, error = [], ""
        if f.get("check_sql"):
            sqltool.prepare(dom, conn)
            got = sqltool.run(dom, conn, f["check_sql"])
            rows, error = (got["rows"], "") if got["ok"] else ([], str(got["error"])[:300])
        else:
            error = "no check was written"
        good, missing = verify(f, rows, known) if rows else (False, [error or "no rows"])
        (ok if good else failed).append({**f, "rows": rows, "missing": missing, "error": error})
    return ok, failed


def rewrite(conn, dom, model, failed: list[dict], known, say=print, schema: str = "") -> list[dict]:
    """One call: the model rewrites each failed finding from the rows its check returned, or gives it up;
    a rewrite is kept only when it passes the same verification."""
    if not failed:
        return []
    shown = "\n\n".join(
        f"[{n}] FINDING: {json.dumps({k: f[k] for k in ('refs', 'customer', 'amount', 'detail')}, default=str)}\n"
        f"CHECK: {f.get('check_sql') or '(none)'}\n"
        + (f"CHECK FAILED: {f['error']}\n" if f.get("error") else "")
        + f"ROWS: {_shown(f['rows'])}\nNOT IN THE ROWS: {', '.join(f['missing'])}"
        for n, f in enumerate(failed))
    prompt = ("Each finding below was checked against the books, and the rows its check returned do not bear "
              "out every reference or amount it states. Rewrite each one from the rows: state only the "
              "records and amounts the rows show, and say what they mean. If the rows show the finding is "
              "wrong, set keep false. If the check itself failed or missed the records, give a corrected "
              "check_sql over the tables listed.\n\n" + shown
              + (f"\n\nTABLES YOU CAN CHECK (name and columns; nothing else exists)\n{schema}" if schema else ""))
    try:
        out = model(SYSTEM, prompt, REWRITE_SCHEMA)
    except Exception as exc:
        say(f"  rewrite failed ({type(exc).__name__})")
        return []
    kept = []
    for rw in out.get("rewrites") or []:
        i = rw.get("index")
        if not isinstance(i, int) or not 0 <= i < len(failed) or not rw.get("keep"):
            continue
        f = valid(rw, known)
        if not f:
            continue
        rows = failed[i]["rows"]
        sql = f.get("check_sql") or failed[i].get("check_sql")
        if sql and (not rows or sql != failed[i].get("check_sql")):
            sqltool.prepare(dom, conn)
            got = sqltool.run(dom, conn, sql)
            rows = got["rows"] if got["ok"] else []
        if rows and verify(f, rows, known)[0]:
            kept.append({**f, "check_sql": sql, "rows": rows})
    return kept


def read_section(conn, dom, model, name: str, part: list[str], n: int, detected: str, known, say=print,
                 trace: list | None = None, schema: str = "", source: str = ""):
    """One section part: the model's findings, each verified by its own check, rewritten once or dropped."""
    prompt = (f"THE BOOKS: {name.upper()} (part {n}), one record per line, as of the as-of date\n"
              + "\n".join(part)
              + "\n\nWHAT THE DETECTORS ALREADY LIST (do not repeat; add meaning if you can)\n"
              + (detected or "(none)")
              + (f"\n\nTHESE ROWS COME FROM\n{source.strip()}" if source else "")
              + (f"\n\nTABLES YOU CAN CHECK (name and columns; nothing else exists)\n{schema}" if schema else "")
              + "\n\nList what looks wrong in this section, each with its check over those tables. "
                "Empty when nothing does.")
    try:
        out = model(SYSTEM, prompt, SCHEMA)
    except Exception as exc:  # one section failing must not lose the others
        say(f"  {name} part {n}: failed ({type(exc).__name__})")
        return [], {"written": 0, "verified": 0, "rewritten": 0}
    written = [f for f in (valid(f, known) for f in out.get("findings") or []) if f]
    ok, failed = check_all(conn, dom, written, known)
    fixed = rewrite(conn, dom, model, failed, known, say, schema)
    if trace is not None:
        trace.append({"section": name, "part": n,
                      "failed": [{k: f.get(k) for k in ("refs", "detail", "amount", "check_sql", "missing", "error")}
                                 | {"rows": f.get("rows", [])[:10]} for f in failed],
                      "rewritten": [{k: f.get(k) for k in ("refs", "detail")} for f in fixed]})
    say(f"  {name} part {n}: {len(ok) + len(fixed)} findings kept of {len(out.get('findings') or [])} "
        f"({len(ok)} verified, {len(fixed)} rewritten and verified, {len(failed) - len(fixed)} dropped)")
    return ok + fixed, {"written": len(out.get("findings") or []), "verified": len(ok), "rewritten": len(fixed)}


def _store(conn, findings: list[dict], known) -> None:
    """Writes the verified findings, with their typed record ids, to the findings table."""
    with conn.cursor() as cur:
        cur.execute(f"drop table if exists {TABLE}")
        cur.execute(f"""create table {TABLE} (issue_id serial, issue_type text, entity_kind text, entity_ref text,
                        customer_key int, customer_name text, event_date date, amount numeric, affects text[],
                        detail text, records jsonb, invoice_ids int[], payment_ids int[], order_ids int[],
                        deal_ids int[], customer_keys int[], check_sql text, verified boolean)""")
        for f in findings:
            recs = typed_records(f["refs"], known)
            ids = {k: sorted({r["id"] for r in recs if r["kind"] == k}) for k in STRICT_KINDS}
            keys = sorted({r["customer_key"] for r in recs if r.get("customer_key") is not None})
            kinds = sorted({r["kind"] for r in recs if r["kind"] in STRICT_KINDS})
            cur.execute(
                f"insert into {TABLE} (issue_type, entity_kind, entity_ref, customer_key, customer_name, amount, "
                "affects, detail, records, invoice_ids, payment_ids, order_ids, deal_ids, customer_keys, check_sql, "
                "verified) values ('onboarding_finding', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)",
                (",".join(kinds) or "record", ", ".join(f["refs"]), keys[0] if len(keys) == 1 else None,
                 f["customer"], f["amount"], f["affects"], f["detail"],
                 json.dumps([{k: r[k] for k in ("kind", "id", "ref")} for r in recs]),
                 ids["invoice"], ids["payment"], ids["order"], ids["deal"], keys, f.get("check_sql")))


def run(dom, model, say=print, trace_path: str | None = None) -> list[dict]:
    """Reads every section of the books once, verifies each finding, and writes the kept ones."""
    with dom.connect() as conn:
        known = _known(conn)
        domains.read_columns(dom, conn)
        schema = domains.bare_schema(dom)
        detected = "\n".join(r for r in _rows(conn, f"select issue_type, left(detail, 160) as detail from {dom.issues} "
                                                     "order by issue_type") [:150]) if dom.issues else ""
        sections = {name: _rows(conn, sql) for name, sql in SECTIONS.items()}
        sections["reconciliation gaps"] = gap_rows(conn, say)
        findings: list[dict] = []
        totals = {"written": 0, "verified": 0, "rewritten": 0}
        trace: list = []
        for name, rows in sections.items():
            for n, part in enumerate(_parts(rows), 1):
                kept, counts = read_section(conn, dom, model, name, part, n, detected, known, say, trace, schema,
                                            SECTIONS.get(name, "code-built reconciliation checks, one gap per row"))
                findings += kept
                for k in totals:
                    totals[k] += counts[k]
        _store(conn, findings, known)
        if trace_path:
            with open(trace_path, "w") as fh:
                json.dump(trace, fh, indent=1, default=str)
        if not conn.autocommit:
            conn.commit()
    say(f"  written {totals['written']}, verified {totals['verified']}, rewritten and verified {totals['rewritten']}")
    return findings


def main(argv=None) -> int:
    """Command line: onboard one domain's warehouse."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--domain", default="finance")
    ap.add_argument("--model", default="opus")
    ap.add_argument("--trace", help="write the findings that failed verification, with their rows, to this file")
    args = ap.parse_args(argv)
    dom = domains.load(args.domain)
    findings = run(dom, CommandLineModel(args.model), trace_path=args.trace)
    print(f"{len(findings)} findings written to {TABLE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
