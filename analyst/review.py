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
                    "description": "Most material first. Each one sentence: the record references exactly as the "
                                   "books write them, the amount, and what it means for this figure. Empty when "
                                   "nothing bears on the answer."},
        "corrected": {"type": "string",
                      "description": "Empty, unless the issues show the books figure misstates what was asked: "
                                     "then one short sentence giving the corrected figure and what it leaves out, "
                                     "for example 'Excluding the test account: 33.' The books figure stays the "
                                     "answer; this sentence comes second."},
        "answer": {"type": "string", "description": "Leave empty."},
    },
    "required": ["caveats", "corrected", "answer"],
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
    "negative values, references that repeat). If the question asks whether to act (chase, send a "
    "reminder, bill), say what to confirm first. Say in plain words what the problem means for the "
    "figure, not only that it exists: a deposit is owed back, so it is not revenue; a write-off closed an "
    "invoice without cash; a date edited after the fact is unreliable; a record linked only by free text "
    "is not in its order's own list; an unpaid fee inflates what is open and late. "
    "Only an issue that changes or qualifies THIS figure counts: a mistyped due date bears on what is late, "
    "not on what is owed; tax inside totals bears on billed figures only when the question is about "
    "revenue, tax or a currency. "
    "RANKING: a [verified finding] was checked against the books' own rows; state every verified finding "
    "that touches a record inside this figure, largest first, before any problem you only suspect. For a "
    "company-wide figure (a total, a count, an average, a rate, a ranking), cover every verified finding "
    "inside it. A suspicion of your own needs a check of yours that returned rows showing it. "
    "NEVER CONTRADICT A MART: the marts' own figures and statuses (a deal's status, what is not yet "
    "invoiced, what is open) are computed from the records. Do not suggest that one of them is wrong, or "
    "offer a rival explanation of the figure, unless a check you ran returned rows showing it; then give "
    "those rows' references and amounts. "
    "EACH CAVEAT: the record references copied exactly as the books write them (invoice, payment and order "
    "numbers, deal and customer names), the amount, and what it means for this figure, in one sentence. "
    "Never paraphrase a reference away ('two invoices' is not enough; name them). Write at most "
    "{count} caveats, together under {room} characters, one per distinct problem, most material first, "
    "with no restating of the figure, plain and specific, the question's own subject first. "
    "THE HEADLINE: the books figure stays the only answer. Fill 'corrected' only when an issue shows the "
    "figure itself misleads and a caveat alone would leave a wrong headline (a test or internal account "
    "counted as a customer, money that belongs to another party, a deal marked won and lost, values in "
    "another currency added as dollars, a duplicate): one short sentence with the corrected figure worked "
    "out from the records and what it leaves out. It must differ from the books figure. Otherwise leave it "
    "empty. Never invent a problem: if nothing bears on the answer, return no caveats."
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


# Columns in a result that name the records behind a figure, by the kind of record they name.
ID_COLUMNS = {"invoice_id": "invoice", "payment_id": "payment", "order_id": "order", "deal_id": "deal",
              "customer_key": "customer"}
REF_COLUMNS = {"invoice_ref": "invoice", "payment_ref": "payment", "order_ref": "order", "deal_name": "deal",
               "customer_name": "customer", "first_posted_customer": "customer"}
ARRAY_COLUMNS = {"source_invoice_ids": "invoice", "invoice_ids": "invoice", "source_payment_ids": "payment",
                 "possible_unlinked_invoice_ids": "invoice", "cancelled_invoice_ids": "invoice"}
KINDS = ("invoice", "payment", "order", "deal", "customer")
FINDING_ID_COLUMNS = {"invoice": "invoice_ids", "payment": "payment_ids", "order": "order_ids", "deal": "deal_ids",
                      "customer": "customer_keys"}
RESOLVE_SQL = """
    select 'invoice', invoice_id, invoice_ref from staging.stg_invoices where lower(invoice_ref) = any(%(exact)s)
    union all select 'payment', payment_id, payment_ref from staging.stg_payments where lower(payment_ref) = any(%(exact)s)
    union all select 'order', order_id, order_ref from staging.stg_orders where lower(order_ref) = any(%(exact)s)
    union all select 'deal', deal_id, deal_name from staging.stg_deals
        where lower(deal_name) = any(%(exact)s) or lower(deal_name) like any(%(loose)s)
    union all select distinct 'customer', customer_key, customer_name from intermediate.int_customers
        where lower(customer_name) = any(%(exact)s) or lower(customer_name) like any(%(loose)s)"""
# One hop from each record to the records it is built on or settled by.
EXPAND_SQL = """
    select 'order', o.order_id from staging.stg_orders o where o.deal_id = any(%(deal)s)
       or o.invoice_ids && %(invoice)s::int[] or o.order_id = any(%(order)s)
    union all select 'deal', o.deal_id from staging.stg_orders o where o.order_id = any(%(order)s) and o.deal_id is not null
    union all select 'invoice', unnest(o.invoice_ids) from staging.stg_orders o
        where o.order_id = any(%(order)s) or o.deal_id = any(%(deal)s)
    union all select 'invoice', unnest(p.invoice_ids) from staging.stg_payments p where p.payment_id = any(%(payment)s)
    union all select 'payment', p.payment_id from staging.stg_payments p where p.invoice_ids && %(invoice)s::int[]"""
REFS_SQL = """
    select invoice_ref from staging.stg_invoices where invoice_id = any(%(invoice)s)
    union all select payment_ref from staging.stg_payments where payment_id = any(%(payment)s)
    union all select order_ref from staging.stg_orders where order_id = any(%(order)s)
    union all select deal_name from staging.stg_deals where deal_id = any(%(deal)s)
    union all select customer_name from intermediate.int_customers where customer_key = any(%(customer)s)"""


def _q(conn, sql: str, params: dict) -> list[tuple]:
    """Runs one lookup; a warehouse without one of its tables gives no rows instead of failing the answer."""
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    except Exception:
        if not conn.autocommit:
            conn.rollback()
        return []


def answer_records(conn, question: str, sql: str, rows: list[dict], wide: bool) -> dict:
    """The records behind an answer's figure, as ids by kind: the records its rows name (all rows), the
    records its filters and question name, and one hop out (a deal's orders and invoices, an invoice's
    order and payments, a payment's invoices). `refs` holds their references, lower-cased."""
    ids = {k: set() for k in KINDS}
    refs: set[str] = set()
    if not wide:
        for r in rows:
            for col, kind in ID_COLUMNS.items():
                v = r.get(col)
                if isinstance(v, int) and not isinstance(v, bool):
                    ids[kind].add(v)
            for col, kind in ARRAY_COLUMNS.items():
                for v in r.get(col) or [] if isinstance(r.get(col), list) else []:
                    if isinstance(v, int):
                        ids[kind].add(v)
            for col in REF_COLUMNS:
                v = r.get(col)
                if isinstance(v, str) and v.strip():
                    refs.add(v.strip().lower())
    names = _entities(conn, question, sql, rows, True)  # names from the question and the query's filters
    exact = sorted(refs | {n.lower() for n in names})
    loose = [f"%{n.lower()}%" for n in names if len(n) >= 4]
    if exact or loose:
        for kind, rid, _ in _q(conn, RESOLVE_SQL, {"exact": exact, "loose": loose or ["\x00"]}):
            ids[kind].add(rid)
    customer_level = set(ids["customer"])
    if any(ids[k] for k in ("invoice", "payment", "order", "deal")):
        params = {k: sorted(ids[k]) for k in KINDS}
        for kind, rid in _q(conn, EXPAND_SQL, params):
            if rid is not None:
                ids[kind].add(rid)
    params = {k: sorted(ids[k]) for k in KINDS}
    refs |= {r[0].lower() for r in _q(conn, REFS_SQL, params) if r[0]}
    return {"ids": ids, "customer_level": customer_level, "refs": refs}


def _id_overlap(issue: dict, behind: dict) -> str:
    """How an issue joins the answer's records: 'record' by a shared invoice, payment, order or deal,
    'customer' by a customer the answer is about, or '' when it does not."""
    ids = behind["ids"]
    for kind in ("invoice", "payment", "order", "deal"):
        if set(issue.get(FINDING_ID_COLUMNS[kind]) or []) & ids[kind]:
            return "record"
    ref = str(issue.get("entity_ref") or "").lower()
    if ref and any(r in ref for r in behind["refs"] if len(r) >= 5 and not r.isdigit()) and \
            issue.get("issue_type") != "onboarding_finding":
        return "record"
    keys = set(issue.get("customer_keys") or []) | ({issue["customer_key"]} if issue.get("customer_key") is not None else set())
    if keys & (behind["customer_level"] or ids["customer"]):
        return "customer"
    return ""


def _line(i: dict) -> str:
    """One issue as a line for the reviewer; a verified finding keeps its record references verbatim."""
    if i.get("verified"):
        return (f"- [verified finding, records: {i.get('entity_ref')}; amount {float(i.get('amount') or 0):,.2f}] "
                f"{i['detail']}")
    return f"- [{i['issue_type']}] {i['detail']}"


def gather(conn, issues_table: str, question: str, sql: str, rows: list[dict]) -> dict:
    """The issues and records that bear on one answer, as text for the model.

    Onboarding findings join the answer by the record ids behind its figure (`answer_records`); detector
    rows join by those records' references, by customer, or by a name the question or query filters on.
    For a company-wide figure, verified findings on the measure come first, largest amount first, ahead
    of detector rows."""
    # A question declined before any query ran has no figure to qualify: only what it names is checked.
    wide = bool(sql) and _company_wide(sql, rows)
    names = _entities(conn, question, sql, rows, wide)
    measures = _measures(sql)
    behind = answer_records(conn, question, sql, rows, wide)
    cols_sql = "issue_type, entity_kind, entity_ref, customer_key, customer_name, event_date, amount, affects, detail"
    with conn.cursor() as cur:
        cur.execute(f"select {cols_sql} from {issues_table}")
        cols = [d.name for d in cur.description]
        issues = [dict(zip(cols, r)) for r in cur.fetchall()]
        # What the onboarding pass wrote on its first read of these books, when it has run.
        cur.execute("select to_regclass(%s)", (FINDINGS_TABLE,))
        findings = []
        if cur.fetchone()[0]:
            cur.execute(f"select * from {FINDINGS_TABLE}")
            fcols = [d.name for d in cur.description]
            findings = [dict(zip(fcols, r)) for r in cur.fetchall()]
    low = [n.lower() for n in names]

    def touches(i):
        text = " ".join(str(i.get(k) or "") for k in ("entity_ref", "customer_name", "detail")).lower()
        return any(n in text for n in low)

    def on_measure(i):
        return not measures or bool(set(i.get("affects") or []) & measures)

    def size(i):
        return -abs(float(i.get("amount") or 0))

    typed = [f for f in findings if f.get("records") is not None]
    untyped = [f for f in findings if f.get("records") is None]  # an older findings table: names only
    by_record = sorted([f for f in typed if _id_overlap(f, behind) == "record"], key=size)
    by_customer = sorted([f for f in typed if f not in by_record and _id_overlap(f, behind) == "customer"
                          and on_measure(f)], key=size)
    picked_findings = by_record + by_customer
    if wide:
        picked_findings += sorted([f for f in typed if f not in picked_findings and on_measure(f)], key=size)
    picked = [i for i in issues + untyped if _id_overlap(i, behind) or touches(i)]
    if wide:
        picked += [i for i in issues + untyped if i not in picked and on_measure(i)]
    # Many detector rows of one kind (tax on every euro invoice, a run of zero invoices) read as one list.
    by_type: dict = {}
    for i in picked:
        by_type.setdefault(i["issue_type"], []).append(i)
    lines = [_line(f) for f in picked_findings]
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
            lines += [_line(i) for i in items]
    return {"issues": "\n".join(lines[:MAX_ISSUES]), "names": sorted(names), "wide": bool(wide),
            "verified": len(picked_findings),
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


def budget(verified: int) -> tuple[int, int]:
    """How many caveats, and how many characters in all, an answer may carry: more verified findings
    inside the figure earn more room, up to MAX_CAVEATS and MAX_ROOM."""
    n = max(0, int(verified or 0))
    return min(MAX_CAVEATS, BASE_CAVEATS + n), min(MAX_ROOM, CAVEAT_ROOM + PER_FINDING_ROOM * n)


def prompt(question: str, sql: str, rows: list[dict], answer, explanation: str, found: dict) -> str:
    """The reviewer's prompt: the answer, the issues and records behind it, and the rules with its budget."""
    shown = json.dumps(rows[:30], default=str)
    return (f"QUESTION\n{question}\n\nQUERY\n{sql}\n\nRESULT ({len(rows)} rows)\n{shown}\n\n"
            f"ANSWER GIVEN\n{json.dumps(answer, default=str)}\n{explanation}\n\n"
            f"ISSUES FOUND IN THE BOOKS ({'company-wide figure: all issues inside the measure' if found['wide'] else 'touching what the answer names'})\n"
            f"{found['issues'] or '(none)'}\n\n"
            + (f"RECORDS BEHIND THE ANSWER\n{found['records']}\n\n" if found.get("records") else "")
            + REVIEW_RULES.format(count=budget(found.get("verified", 0))[0],
                                  room=budget(found.get("verified", 0))[1]))


CAVEAT_ROOM = 470  # the room with no verified finding inside the figure
PER_FINDING_ROOM = 110  # more room for each verified finding inside the figure
MAX_ROOM = 900
BASE_CAVEATS = 5
MAX_CAVEATS = 8
CAVEAT_MAX = 300  # one caveat longer than this is a runaway, not a caveat


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


NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _numbers(value) -> list[float]:
    """Every number written in a value, thousands separators ignored."""
    text = re.sub(r"(?<=\d),(?=\d{3})", "", json.dumps(value, default=str) if not isinstance(value, str) else value)
    return [float(x) for x in NUMBER.findall(text)]


def corrected_sentence(answer, corrected: str) -> str:
    """The reviewer's corrected figure as a labelled second sentence, kept only when it states a figure
    that differs from every figure in the books answer; otherwise ''."""
    corrected = (corrected or "").strip()
    if not corrected:
        return ""
    headline = _numbers(answer)
    figures = [x for x in _numbers(corrected) if abs(x) >= 1 and not (1990 <= x <= 2100 and x.is_integer())]
    if not figures or all(any(abs(x - h) < 0.006 for h in headline) for x in figures):
        return ""
    text = corrected if corrected.endswith((".", "!", "?")) else corrected + "."
    return text if re.match(r"(?i)^corrected\b", text) else "Corrected: " + text


def merge(explanation: str, caveats: list[str], room: int = CAVEAT_ROOM, corrected: str = "") -> str:
    """The explanation's first sentence, then the labelled corrected figure if any, then the caveats, kept
    short enough to be read whole: caveats come most material first, and one that would push past the
    room is left out."""
    caveats = [c.strip() for c in caveats or [] if c and c.strip() and len(c.strip()) <= CAVEAT_MAX]
    first = re.split(r"(?<=[.!?])\s+", (explanation or "").strip(), maxsplit=1)[0][:220]
    if not caveats and not corrected:
        return explanation
    kept, used = [], 0
    for c in caveats:
        if kept and used + len(c) > room:
            break
        kept.append(c)
        used += len(c) + 1
    return " ".join(x for x in (first, corrected, ("Caveat: " + " ".join(kept)) if kept else "") if x)


# The investigating review: the reviewer may check the books itself before it writes the caveats. A
# new client's books always hold kinds of mess no detector was written for, so the reviewer looks at
# the records behind the answer the way a careful analyst would, with a few read-only queries.
MAX_CHECKS = 4
CHECK_ROWS_SHOWN = 40

INVESTIGATE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["check", "final"]},
        "sql": {"type": "string", "description": "One read-only PostgreSQL SELECT when action is check, else empty."},
        "why": {"type": "string", "description": "What this check looks for, or empty."},
        "status": {"type": "string", "enum": ["keep", "answer", "not_answerable"],
                   "description": "final only: keep the drafted status; 'answer' when the drafted reply declined "
                                  "but the books hold the figure; 'not_answerable' when the question needs "
                                  "something the warehouse does not hold."},
        "caveats": REVIEW_SCHEMA["properties"]["caveats"],
        "corrected": REVIEW_SCHEMA["properties"]["corrected"],
        "answer": {"type": "string",
                   "description": "final only: empty, unless status is 'answer' (the drafted reply declined): "
                                  "then the answer itself."},
    },
    "required": ["action", "sql", "why", "status", "caveats", "corrected", "answer"],
}

INVESTIGATE_RULES = (
    "Before you write, you may CHECK the books yourself with up to {left} more read-only SELECT queries "
    "(action 'check'), over the same schemas. Use them to look at the records behind this answer for "
    "anything that qualifies it, including kinds of problem no issue above names: compare each record "
    "with the ones it links to (a payment with its invoice: same currency, an amount that fits, a memo "
    "that agrees; a credit note with its invoice: date order, currency; an order with its invoices and its "
    "deal: value, currency, count; a deal's stage with its flags and probability), and with records of "
    "its own kind (one far out of line, one repeated, one customer's records that differ from everyone "
    "else's). For a company-wide figure (a total, a count, an average, a rate, a ranking), spend one check "
    "breaking it down by customer (and by currency where amounts are involved) to see what drives it: one "
    "customer whose records behave unlike everyone else's (many tiny deals, an amount far out of line, a "
    "different currency) qualifies the figure, and the answer should give it with and without that part. "
    "Read references and memos for what they say. Check only what bears on this answer. When "
    "you have seen enough, give action 'final'. "
    "Status: keep the drafted status unless the checks show otherwise. Use 'answer' only when the drafted "
    "reply declined but the warehouse holds the figure (give it in 'answer' with its caveats); use "
    "'not_answerable' when the question asks for something the warehouse does not hold at all (what "
    "actually reached the bank, what a customer intended, a date or rate no field records, a reason no note "
    "states): then say in the caveats what is missing and what the books do show."
)


def investigate(conn, dom, model, system: str, question: str, sql: str, rows: list[dict], answer, explanation: str,
                status: str, found: dict, not_loaded: list[str], run) -> dict:
    """The review with checks: up to MAX_CHECKS read-only queries, then the final caveats, answer and status.
    `run(sql) -> {"ok", "rows", "error"}` executes a check through the warehouse's read-only path."""
    checks = []
    base = prompt(question, sql, rows, answer, explanation, found)
    if status == "not_answerable":
        base = base.replace("ANSWER GIVEN", "ANSWER GIVEN (the draft DECLINED this question)", 1)
    missing = ("\n\nTHE WAREHOUSE DOES NOT HOLD: " + "; ".join(not_loaded)) if not_loaded else ""
    for _ in range(MAX_CHECKS + 1):
        left = MAX_CHECKS - len(checks)
        done = "".join(f"\n- {c['why']}\n  SQL: {c['sql']}\n  FOUND: {c['found']}" for c in checks)
        text = (base + missing + ("\n\nYOUR CHECKS SO FAR" + done if done else "") + "\n\n"
                + (INVESTIGATE_RULES.format(left=left) if left > 0 else
                   "No checks are left: give action 'final' now."))
        out = model(system, text, INVESTIGATE_SCHEMA)
        if out.get("action") == "check" and left > 0 and (out.get("sql") or "").strip():
            got = run(out["sql"])
            found_text = (json.dumps(got["rows"][:CHECK_ROWS_SHOWN], default=str) if got["ok"]
                          else "ERROR: " + str(got["error"])[:300])
            checks.append({"sql": out["sql"], "why": out.get("why", ""), "found": found_text[:4000]})
            continue
        out["checks"] = checks
        return out
    return {"action": "final", "caveats": [], "answer": "", "status": "keep", "checks": checks}
