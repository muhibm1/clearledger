"""The review step: before an answer goes out, check the books behind it for anything that makes the
figure misleading, and say so in the answer.

A figure can be exactly what the books say and still mislead: an invoice due before it was issued,
a pound rate keyed ten times too high, a test account counted as a customer, a payment left in draft.
The warehouse lists such records in an issues table (`marts.data_issues` in the finance domain, built
by general detectors, not known cases). Code picks the issues that touch what the answer is about,
plus the raw records of a named customer, invoice or deal; the model then writes the caveats a
careful analyst would state, or none. The figure itself is never changed here.

Picking is by code so nothing is left to the model's memory:
- every customer, invoice, deal, order and payment named in the result rows or in the query's filters;
- issues whose text names one of those;
- for a company-wide figure (no single entity), every issue whose `affects` matches the measure the
  query reads, summarized by kind when there are many.
"""

import json
import re

MAX_ISSUES = 40
FINDINGS_TABLE = "marts.onboarding_findings"  # written by analyst/onboard.py
MAX_RECORDS = 40

ENTITY_COLUMNS = ("customer_name", "customer_key", "invoice_ref", "deal_name", "payment_ref", "order_ref",
                  "first_posted_customer", "entity_ref")
# The measure a query reads, from its column names, mapped to the issues' `affects` vocabulary.
MEASURES = [
    (r"past_due|late_|days_past|overdue|aging|due_date", "past_due"),
    (r"\bopen\b|amount_open|total_open|owed|residual", "open"),
    (r"cash|payment|paid", "cash"),
    (r"invoiced|amount\b|billed|amount_in_invoice_currency|avg\(|average", "billed"),
    (r"deal|order|won|lost|stage|expected_revenue", "deals"),
    (r"count\(|avg\(|average", "counts"),
]
# A filter on a name or reference names an entity; a filter on a date, a state or a type does not.
NAME_FILTER = re.compile(r"(?i)\b(\w*(?:name|ref))\s*(?:i?like|=|~\*?)\s*'%?([^'%]{3,})%?'")
ID_FILTER = re.compile(r"(?i)\b(customer_key|deal_id|invoice_id|partner_id|order_id|payment_id)\s*(?:=|in\s*\()\s*(\d+)")
ID_NAMES = {
    "customer_key": "select customer_name from intermediate.int_customers where customer_key = %s limit 1",
    "partner_id": "select partner_name from staging.stg_partners where partner_id = %s",
    "deal_id": "select deal_name from staging.stg_deals where deal_id = %s",
    "invoice_id": "select invoice_ref from staging.stg_invoices where invoice_id = %s",
    "order_id": "select order_ref from staging.stg_orders where order_id = %s",
    "payment_id": "select payment_ref from staging.stg_payments where payment_id = %s",
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "caveats": {"type": "array", "items": {"type": "string"},
                    "description": "At most 4 caveats, most material first, each one short sentence with its "
                                   "figures and record references. Empty when nothing bears on the answer."},
        "answer": {"type": "string",
                   "description": "Empty, unless an issue shows the answer misstates what was asked: then the "
                                  "answer restated with the books figure AND the corrected one, for example "
                                  "'34 on the books; 33 excluding the test account'."},
    },
    "required": ["caveats", "answer"],
}

REVIEW_RULES = (
    "You are checking an answer before it goes to a finance reader. The figure is what the books say; "
    "keep it. Your job is to state what makes it misleading, if anything. Read the issues found in the "
    "books and the records below. A caveat is needed when an issue touches a record that is counted in "
    "this answer or that the question is about: a figure that includes a test account, a duplicate, a "
    "mis-converted amount, a mistyped date, a payment not posted or with no customer, a payer who is not "
    "the customer, an order on another customer's deal, a record that contradicts another, invoices for "
    "zero. When the figure covers a period or a subset, use each issue's dates and amounts to decide "
    "whether its record is inside the figure, and say how much of the figure it is. Also look at the "
    "records yourself for anything inconsistent that the issue list missed (dates out of order, amounts "
    "or rates out of line, links that point to the wrong customer, statuses that disagree, zero or "
    "negative values, references that repeat). Give the record (invoice, payment, deal or customer), the "
    "amount, and what the figure would be without the problem when that is clear. If the question asks "
    "whether to act (chase, send a reminder), say what to confirm first. Say in plain words what the "
    "problem means for the figure, not only that it exists: a deposit is owed back, so it is not revenue; "
    "a write-off closed an invoice without cash; a date edited after the fact is unreliable; a record "
    "linked only by free text is not in its order's own list; an unpaid fee inflates what is open and "
    "late. For a company-wide figure, mention every material issue inside it, largest first, and prefer "
    "a problem the records prove (tax charged twice, a pro-forma, a fee) over one that is only possible. "
    "Only an issue that changes or qualifies THIS figure counts: a mistyped due date bears on what is late, "
    "not on what is owed; tax inside totals bears on billed figures only when the question is about "
    "revenue, tax or a currency. Write at most 4 caveats, each under 130 characters, plain and specific, "
    "the question's own subject first. "
    "Fill 'answer' only when the issues show the answer itself misleads (it counts a test or internal "
    "account as a customer, names a deal won that the CRM also marks lost, credits a customer with an "
    "order or payment that is another party's): restate it with the books figure kept and the corrected "
    "reading beside it. Otherwise leave it empty. Never invent a problem: if nothing bears on the answer, "
    "return no caveats."
)


def _entities(conn, question: str, sql: str, rows: list[dict], wide: bool) -> set[str]:
    names = set()
    if not wide:
        for r in rows[:20]:
            for c in ENTITY_COLUMNS:
                v = r.get(c)
                if v not in (None, "") and not isinstance(v, (list, dict)):
                    names.add(str(v))
    for m in NAME_FILTER.finditer(sql or ""):
        names.add(m.group(2).strip())
    with conn.cursor() as cur:
        for m in ID_FILTER.finditer(sql or ""):
            try:
                cur.execute(ID_NAMES[m.group(1).lower()], (int(m.group(2)),))
                got = cur.fetchone()
                if got and got[0]:
                    names.add(str(got[0]))
            except Exception:
                pass
    for m in re.finditer(r"\b[A-Z]{2,6}/\d{4}/\d{3,6}\b|\bS\d{4,6}\b", question or ""):
        names.add(m.group(0))
    for m in re.finditer(r"'([^']{4,})'", question or ""):
        names.add(m.group(1))
    return {n for n in names if len(n) >= 3 and not re.fullmatch(r"[\d\-:./ ]+", n)}


def _measures(sql: str) -> set[str]:
    s = (sql or "").lower()
    return {m for pat, m in MEASURES if re.search(pat, s)}


def _company_wide(sql: str, rows: list[dict]) -> bool:
    filtered = NAME_FILTER.search(sql or "") or ID_FILTER.search(sql or "")
    return not filtered or len(rows) > 5


def gather(conn, issues_table: str, question: str, sql: str, rows: list[dict]) -> dict:
    """The issues and records that bear on one answer, as text for the model."""
    # A question declined before any query ran has no figure to qualify: only what it names is checked.
    wide = bool(sql) and _company_wide(sql, rows)
    names = _entities(conn, question, sql, rows, wide)
    measures = _measures(sql)
    cols_sql = "issue_type, entity_kind, entity_ref, customer_key, customer_name, event_date, amount, affects, detail"
    with conn.cursor() as cur:
        cur.execute(f"select {cols_sql} from {issues_table}")
        cols = [d.name for d in cur.description]
        issues = [dict(zip(cols, r)) for r in cur.fetchall()]
        # What the onboarding pass wrote on its first read of these books, when it has run.
        cur.execute("select to_regclass(%s)", (FINDINGS_TABLE,))
        if cur.fetchone()[0]:
            cur.execute(f"select {cols_sql} from {FINDINGS_TABLE}")
            issues += [dict(zip(cols, r)) for r in cur.fetchall()]
    low = [n.lower() for n in names]

    def touches(i):
        text = " ".join(str(i.get(k) or "") for k in ("entity_ref", "customer_name", "detail")).lower()
        return any(n in text for n in low)

    picked = [i for i in issues if touches(i)]
    if wide:
        picked += [i for i in issues if i not in picked and (not measures or set(i["affects"] or []) & measures)]
    # Many issues of one kind (tax on every euro invoice, a run of zero invoices) read as one list.
    by_type: dict = {}
    for i in picked:
        by_type.setdefault(i["issue_type"], []).append(i)
    lines = []
    for kind, items in by_type.items():
        if len(items) > 8:
            for i in [i for i in items if touches(i)][:3]:
                lines.append(f"- [{kind}] {i['detail']}")
            total = sum(float(i["amount"] or 0) for i in items)
            # Each record keeps the start of its own wording: a note's meaning differs record by record.
            each = "; ".join(f"{i['entity_ref']} {i['customer_name'] or ''} {i['event_date'] or ''} "
                             f"{float(i['amount'] or 0):,.2f} ({(i['detail'] or '')[:150]})" for i in items[:25])
            lines.append(f"- [{kind}] {len(items)} records, amounts totalling {total:,.2f} (record, customer, "
                         f"date, amount, detail): {each}")
        else:
            lines += [f"- [{kind}] {i['detail']}" for i in items]
    return {"issues": "\n".join(lines[:MAX_ISSUES]), "names": sorted(names), "wide": bool(wide),
            "records": _records(conn, names) if names and not wide else ""}


def _records(conn, names: set[str]) -> str:
    """Raw rows of the customers, invoices and deals the answer names, for the model to look over."""
    pats = [f"%{n.lower()}%" for n in names][:12]
    out = []
    queries = [
        ("INVOICES", "select i.invoice_ref, i.move_type, i.customer_name, i.invoice_date, i.due_date, i.currency, "
                     "i.amount_in_invoice_currency, i.amount as usd, i.amount_open as open_usd, i.days_past_due, "
                     "i.changed_after_posting, o.order_ref, s.reference, s.amount_tax_in_invoice_currency as tax "
                     "from intermediate.int_invoices i left join staging.stg_orders o using (order_id) "
                     "left join staging.stg_invoices s using (invoice_id) "
                     "where lower(i.customer_name) like any(%s) or lower(i.invoice_ref) like any(%s) "
                     "order by i.invoice_date limit 40"),
        ("PAYMENTS", "select p.payment_ref, p.state, p.payment_date, p.amount as usd, p.memo, "
                     "coalesce(c.customer_name, '(no customer)') as payer, "
                     "(select string_agg(i.invoice_ref || ' of ' || i.customer_name, ', ') from staging.stg_invoices si "
                     " join intermediate.int_invoices i using (invoice_id) where si.invoice_id = any(p.invoice_ids)) as pays "
                     "from staging.stg_payments p left join intermediate.int_customers c using (partner_id) "
                     "where lower(coalesce(c.customer_name, '')) like any(%s) or lower(coalesce(p.payment_ref, '')) like any(%s) "
                     "or exists (select 1 from intermediate.int_invoices i where i.invoice_id = any(p.invoice_ids) "
                     "and (lower(i.customer_name) like any(%s) or lower(i.invoice_ref) like any(%s))) "
                     "order by p.payment_date limit 30"),
        ("DEALS", "select d.deal_name, c.customer_name, d.stage, d.is_won, d.is_lost, d.expected_revenue, d.closed_on, "
                  "(select string_agg(o.order_ref || ' ' || o.state || ' ' || o.amount_total || ' ' || o.currency || ' for ' || "
                  " coalesce(oc.customer_name, '?'), '; ') from staging.stg_orders o left join intermediate.int_customers oc "
                  " on oc.partner_id = o.partner_id where o.deal_id = d.deal_id) as orders "
                  "from staging.stg_deals d left join intermediate.int_customers c using (partner_id) "
                  "where lower(coalesce(c.customer_name, '')) like any(%s) or lower(d.deal_name) like any(%s) limit 20"),
    ]
    with conn.cursor() as cur:
        for title, q in queries:
            try:
                cur.execute(q, [pats] * q.count("%s"))
            except Exception as exc:  # a missing table in another warehouse shape: skip, never fail the answer
                conn.rollback() if not conn.autocommit else None
                out.append(f"{title}: unavailable ({type(exc).__name__})")
                continue
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            if rows:
                out.append(f"{title}\n" + "\n".join(json.dumps(r, default=str) for r in rows[:MAX_RECORDS]))
    return "\n\n".join(out)


def prompt(question: str, sql: str, rows: list[dict], answer, explanation: str, found: dict) -> str:
    shown = json.dumps(rows[:30], default=str)
    return (f"QUESTION\n{question}\n\nQUERY\n{sql}\n\nRESULT ({len(rows)} rows)\n{shown}\n\n"
            f"ANSWER GIVEN\n{json.dumps(answer, default=str)}\n{explanation}\n\n"
            f"ISSUES FOUND IN THE BOOKS ({'company-wide figure: all issues inside the measure' if found['wide'] else 'touching what the answer names'})\n"
            f"{found['issues'] or '(none)'}\n\n"
            + (f"RECORDS BEHIND THE ANSWER\n{found['records']}\n\n" if found.get("records") else "")
            + REVIEW_RULES)


CAVEAT_ROOM = 480


def revised(answer, proposed: str):
    """The reviewer's restated answer, used only when it keeps every number the original answer gave."""
    proposed = (proposed or "").strip()
    if not proposed:
        return None
    flat = re.sub(r"(?<=\d),(?=\d{3})", "", proposed)
    numbers = re.findall(r"-?\d+(?:\.\d+)?", re.sub(r"(?<=\d),(?=\d{3})", "", json.dumps(answer, default=str)))
    for n in numbers:
        v = float(n)
        if v.is_integer() and abs(v) < 1000 and len(numbers) > 3:
            continue
        if not any(abs(float(x) - v) < 0.006 for x in re.findall(r"-?\d+(?:\.\d+)?", flat)):
            return None
    return proposed


def merge(explanation: str, caveats: list[str]) -> str:
    """The explanation's first sentence with the caveats after it, kept short enough to be read whole:
    caveats come most material first, and one that would push past the room is left out."""
    caveats = [c.strip() for c in caveats or [] if c and c.strip()]
    if not caveats:
        return explanation
    kept, used = [], 0
    for c in caveats:
        if kept and used + len(c) > CAVEAT_ROOM:
            break
        kept.append(c)
        used += len(c) + 1
    first = re.split(r"(?<=[.!?])\s+", (explanation or "").strip(), maxsplit=1)[0][:220]
    return first + " Caveat: " + " ".join(kept)
