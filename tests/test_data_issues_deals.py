"""marts.data_issues, third small company: deals whose stage or value says something the flags do not,
and records that only make sense once matched to another (cash not applied, an invoice reversed, an order
copied).

Each planted problem sits next to a clean look-alike that must stay quiet. Skipped without a Postgres
to make a scratch database in, or without dbt.
"""

import pytest
from fixture_world import World, build_world, drop_world


def build() -> World:
    w = World()
    for pid, name in [(1, "Alder Builders"), (2, "Birch Maple Ltd"), (3, "Cove Drilling"), (4, "Delta Farms")]:
        w.partner(pid, name)
    for sid, name, won in [(1, "Won", True), (2, "New", False), (3, "Closed - Lost", False), (4, "Signed", False)]:
        w.add("crm_stage", sid, {"name": name, "is_won": won, "sequence": sid})

    def deal(did, partner, name, revenue, stage, won=False):
        w.add("crm_lead", did, {"name": name, "type": "opportunity", "partner_id": [partner, "x"],
                                "stage_id": [stage, "s"], "expected_revenue": revenue,
                                "date_closed": "2026-03-01 00:00:00" if won else False,
                                "active": True, "create_date": "2026-01-01 00:00:00"})

    # Two open deals: one parked in a stage named lost, one ordinary.
    deal(11, 1, "Alder dead job", 5000, 3)
    deal(12, 1, "Alder live job", 6000, 2)

    # Birch Maple is billed in Canadian dollars at about 0.73: one deal keyed in CAD, one in USD.
    deal(21, 2, "Birch CAD-keyed", 10000, 1, won=True)
    deal(22, 2, "Birch USD-keyed", 7300, 1, won=True)
    for oid, did, iid, cad in [(201, 21, 2001, 10000), (202, 22, 2002, 10000)]:
        w.order(oid, did, 2, cad, invoices=[iid], currency_id=[2, "CAD"])
        w.invoice(iid, f"INV/{iid}", 2, "2026-03-05", "2026-04-05", 0, 0, currency_id=[2, "CAD"],
                  amount_total=cad, amount_total_signed=cad * 0.73, origin=f"S{oid:05d}")
        w.payment(5000 + iid, 2, cad * 0.73, "2026-03-20", [iid])

    # Cove: an advance not applied to the invoice it is for; a second customer's unrelated invoice is too small.
    w.invoice(3001, "INV/3001", 3, "2026-08-01", "2026-08-31", 9000, 9000)
    w.payment(5301, 3, 4000, "2026-07-20", [])
    w.invoice(3002, "INV/3002", 3, "2026-08-02", "2026-09-01", 1000, 1000)

    # Delta: an invoice reversed by a credit note and rebilled; an order copied on the same deal; and a
    # deal still in a signed stage although its order was confirmed and invoiced.
    deal(41, 4, "Delta gala", 3000, 1, won=True)
    w.order(401, 41, 4, 3000, invoices=[4001], date="2026-02-01 00:00:00")
    w.order(402, 41, 4, 3000, date="2026-02-01 00:00:00")
    w.invoice(4001, "INV/4001", 4, "2026-02-10", "2026-03-10", 3000, 0, origin="S00401")
    w.payment(5401, 4, 3000, "2026-03-01", [4001])
    w.invoice(4002, "INV/4002", 4, "2026-02-20", "2026-03-20", 2500, 0, payment_state="reversed")
    w.invoice(4003, "RINV/4003", 4, "2026-02-25", "2026-02-25", 2500, 0, kind="out_refund")
    deal(42, 4, "Delta signed job", 1200, 4)
    w.order(403, 42, 4, 1200, invoices=[4004], date="2026-03-01 00:00:00")
    w.invoice(4004, "INV/4004", 4, "2026-03-05", "2026-04-05", 1200, 0, origin="S00403")
    w.payment(5404, 4, 1200, "2026-03-25", [4004])
    # Alder: one order billed with two digits swapped, one billed exactly.
    w.order(501, None, 1, 18450, invoices=[5001], date="2026-04-01 00:00:00")
    w.invoice(5001, "INV/5001", 1, "2026-04-05", "2026-05-05", 14850, 0, origin="S00501")
    w.payment(5501, 1, 14850, "2026-04-25", [5001])
    w.order(502, None, 1, 2000, invoices=[5002], date="2026-04-01 00:00:00")
    w.invoice(5002, "INV/5002", 1, "2026-04-05", "2026-05-05", 2000, 0, origin="S00502")
    w.payment(5502, 1, 2000, "2026-04-25", [5002])
    return w


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    conn = build_world(build(), tmp_path_factory.mktemp("dbt"))
    if conn is None:
        pytest.skip("no scratch Postgres or no dbt here")
    yield conn
    drop_world(conn)


def issues(db, kind):
    return {r[0]: r[1] for r in db.execute(
        "select entity_ref, detail from marts.data_issues where issue_type = %s", (kind,)).fetchall()}


def test_an_open_deal_in_a_stage_named_lost_is_flagged_and_an_ordinary_open_deal_is_not(db):
    got = issues(db, "deal_stage_says_closed")
    assert set(got) == {"Alder dead job"}
    assert "Closed - Lost" in got["Alder dead job"] and "open pipeline" in got["Alder dead job"]


def test_a_deal_value_keyed_in_the_orders_currency_is_flagged_and_one_keyed_in_dollars_is_not(db):
    got = issues(db, "deal_value_in_order_currency")
    assert set(got) == {"Birch CAD-keyed"}
    assert "CAD" in got["Birch CAD-keyed"] and "7,300.00" in got["Birch CAD-keyed"]


def test_unapplied_cash_names_the_open_invoice_it_fits(db):
    detail = issues(db, "payment_not_matched")["PAY/5301"]
    assert "INV/3001" in detail and "INV/3002" not in detail


def test_a_reversed_invoice_reads_as_not_paid(db):
    detail = issues(db, "settled_without_payment")["INV/4002"]
    assert "reversed" in detail and "NOT paid" in detail


def test_an_uninvoiced_copy_of_an_invoiced_order_reads_as_a_duplicate(db):
    detail = issues(db, "order_not_invoiced")["S00402"]
    assert "copy of order S00401" in detail and "cancelled, not billed" in detail


def test_an_open_deal_with_an_invoiced_order_says_it_was_really_won(db):
    detail = issues(db, "deal_status_contradicts_sales")["Delta signed job"]
    assert "really won" in detail and "pipeline" in detail


def test_an_order_billed_with_its_digits_swapped_is_flagged_and_one_billed_exactly_is_not(db):
    got = issues(db, "invoice_digits_transposed")
    assert set(got) == {"INV/5001"}
    assert "under-billed" in got["INV/5001"] and "3,600.00" in got["INV/5001"]
