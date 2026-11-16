"""Trace a number in a finance table back to the Odoo records behind it.

Every mart row carries the Odoo ids it was built from. This expands them into
the records themselves, with the exact version each total used, and checks
that the records add up to the number. A trace that does not add up says so.
"""

from decimal import Decimal

TOL = Decimal("0.01")


def _rows(conn, sql, args=()):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _money(x) -> str:
    return "-" if x is None else f"{x:,.2f}"


def _table(rows, cols) -> str:
    if not rows:
        return "  (none)"
    cells = [[str(r[c]) if not isinstance(r[c], Decimal) else _money(r[c]) for c in cols] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
    line = lambda vals: "  " + "  ".join(v.ljust(w) for v, w in zip(vals, widths))
    return "\n".join([line(cols), line(["-" * w for w in widths]), *map(line, cells)])


INVOICE_SQL = """
select i.invoice_ref as invoice, i.invoice_date as dated, i.due_date as due,
       i.days_past_due as days_late, i.currency, i.amount_in_invoice_currency as in_currency,
       i.amount as usd, i.amount_paid as paid, i.amount_open as open,
       o.order_ref as "order", d.deal_name as deal, i.source_ref
from intermediate.int_invoices i
left join staging.stg_orders o on o.order_id = i.order_id
left join staging.stg_deals d on d.deal_id = i.deal_id
where i.invoice_id = any(%s) order by i.invoice_date, i.invoice_id
"""

PAYMENT_SQL = """
select p.payment_ref as payment, p.payment_date as dated, p.payment_type as kind, p.currency, p.amount as usd,
       array_to_string(p.invoice_ids, ',') as invoice_ids,
       case when p.settles_another_customers_invoices then 'sent by ' || payer.customer_name
            when p.possible_duplicate_of is not null then 'possible duplicate of payment ' || p.possible_duplicate_of
            when cardinality(p.invoice_ids) = 0 then 'matched to no invoice' else '' end as note,
       'account.payment/' || p.payment_id || '@' || to_char(p.source_version, 'YYYY-MM-DD"T"HH24:MI:SS') as source_ref
from intermediate.int_payments p
join intermediate.int_customers payer on payer.partner_id = p.payer_partner_id
where p.payment_id = any(%s) order by p.payment_date, p.payment_id
"""

INV_COLS = ["invoice", "dated", "due", "currency", "in_currency", "usd", "paid", "open", "order", "deal", "source_ref"]


def _check(label, claimed, parts) -> str:
    total = sum((p or Decimal(0)) for p in parts)
    ok = abs((claimed or Decimal(0)) - total) <= TOL
    return f"  {label}: {_money(claimed)} = sum of the records above ({_money(total)}) {'OK' if ok else 'DOES NOT ADD UP'}"


def _customer(conn, table, key):
    """The one customer row `key` names: an exact name (current or former) wins over a part of a name."""
    names = _rows(conn, "select customer_key, name, is_current from intermediate.int_customer_names "
                        "where name ilike %s", (f"%{key}%",))
    exact = {n["customer_key"] for n in names if n["name"].lower() == key.strip().lower()}
    keys = sorted(exact or {n["customer_key"] for n in names})
    found = _rows(conn, f"select * from marts.{table} where customer_key = any(%s) order by customer_name", (keys,))
    if len(found) != 1:
        listed = ", ".join(r["customer_name"] for r in found) or "no match"
        raise SystemExit(f"'{key}' matches {len(found)} customers in {table}: {listed}")
    # Say so when the name asked for is one the customer no longer uses.
    old = sorted({n["name"] for n in names if n["customer_key"] == found[0]["customer_key"]
                  and n["name"] != found[0]["customer_name"]})
    mine = [n["name"] for n in names if n["customer_key"] == found[0]["customer_key"]
            and n["name"] == found[0]["customer_name"]]
    found[0]["_found_as"] = old[0] if old and not mine else None
    return found[0]


def trace(conn, table: str, key: str) -> str:
    out = []
    if table == "revenue_by_customer":
        r = _customer(conn, table, key)
        out.append(f"{r['customer_name']}: billed {_money(r['invoiced'])}, paid {_money(r['paid'])}, "
                   f"open {_money(r['open'])} (USD)")
        if r.get("_found_as"):
            out.append(f"  found under its former name '{r['_found_as']}'")
        merged = _rows(conn, "select partner_id from intermediate.int_customers where customer_key = %s "
                             "order by 1", (r["customer_key"],))
        if len(merged) > 1:
            out.append(f"  merged from Odoo partners {[m['partner_id'] for m in merged]}")
        invs = _rows(conn, INVOICE_SQL, (r["source_invoice_ids"],))
        out += ["", "Invoices", _table(invs, INV_COLS)]
        pays = _rows(conn, PAYMENT_SQL, (r["source_payment_ids"] or [],))
        out += ["", "Payments", _table(pays, ["payment", "dated", "kind", "currency", "usd", "invoice_ids", "note",
                                              "source_ref"]), ""]
        out.append(_check("billed", r["invoiced"], [i["usd"] for i in invs]))
        out.append(_check("open", r["open"], [i["open"] for i in invs]))
        # `paid` is what the invoices show as settled, so it is checked against the invoices. The cash that
        # came in is checked against the payments. The two differ for ordinary reasons, which is said, not
        # flagged: a payment that is not a settlement is not a number that fails to add up.
        out.append(_check("paid", r["paid"], [i["paid"] for i in invs]))
        out.append(_check("cash received", r["cash_received"], [p["usd"] for p in pays]))
        gap = (r["cash_received"] or Decimal(0)) - (r["paid"] or Decimal(0))
        if abs(gap) > TOL:
            out.append(f"  cash received is {_money(gap)} {'above' if gap > 0 else 'below'} what the invoices show as "
                       "settled: " + ("an overpayment, a payment matched to no invoice, or a duplicate."
                                      if gap > 0 else "a write-off or an exchange difference."))
    elif table == "receivables_aging":
        r = _customer(conn, table, key)
        out.append(f"{r['customer_name']}: owes {_money(r['total_open'])} (USD) as of the aging date")
        out.append("  not yet due {}, 1-30 late {}, 31-60 {}, 61-90 {}, over 90 {}".format(
            *(_money(r[c]) for c in ["not_yet_due", "late_1_30", "late_31_60", "late_61_90", "late_over_90"])))
        invs = _rows(conn, INVOICE_SQL, (r["source_invoice_ids"],))
        out += ["", "Open invoices", _table(invs, ["invoice", "due", "days_late", "usd", "paid", "open", "order", "source_ref"]), ""]
        out.append(_check("owed", r["total_open"], [i["open"] for i in invs]))
    else:
        found = _rows(conn, "select * from marts.deal_to_cash where deal_id = %s", (int(key),))
        if not found:
            raise SystemExit(f"no won deal with id {key}")
        r = found[0]
        out.append(f"Deal {r['deal_id']} '{r['deal_name']}' for {r['customer_name']}: {r['status']}")
        out.append(f"  expected {_money(r['expected_revenue'])}, ordered {_money(r['ordered'])}, "
                   f"invoiced {_money(r['invoiced'])}, paid {_money(r['paid'])}, open {_money(r['open'])}")
        orders = _rows(conn, "select order_ref as \"order\", order_date as dated, state, amount_total as amount, "
                             "'sale.order/' || order_id as source_ref from staging.stg_orders "
                             "where order_id = any(%s) order by 1", (r["source_order_ids"],))
        out += ["", "Orders", _table(orders, ["order", "dated", "state", "amount", "source_ref"])]
        if r["source_cancelled_order_ids"]:
            cancelled = _rows(conn, "select order_ref as \"order\", order_date as dated, state, amount_total as amount, "
                                    "'sale.order/' || order_id as source_ref from staging.stg_orders "
                                    "where order_id = any(%s) order by 1", (r["source_cancelled_order_ids"],))
            out += ["", "Cancelled orders (not counted in ordered)",
                    _table(cancelled, ["order", "dated", "state", "amount", "source_ref"])]
        if r["possible_unlinked_invoice_ids"]:
            out += ["", f"Possibly this deal's, but no order lists them (not counted): invoices {r['possible_unlinked_invoice_ids']}"]
        if r["cancelled_invoice_ids"]:
            out += [f"Cancelled invoices on its orders: {r['cancelled_invoice_ids']}"]
        invs = _rows(conn, INVOICE_SQL, (r["source_invoice_ids"],))
        out += ["", "Invoices", _table(invs, ["invoice", "dated", "due", "usd", "paid", "open", "source_ref"]), ""]
        out.append(_check("ordered", r["ordered"], [o["amount"] for o in orders]))
        out.append(_check("invoiced", r["invoiced"], [i["usd"] for i in invs]))
    return "\n".join(out)
