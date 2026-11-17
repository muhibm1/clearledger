"""marts.data_issues on a small invented company: each detector finds the record it is for, and
leaves the ordinary record next to it alone.

The detectors are general rules (a date out of order, a rate out of line with its currency, a payer
who is not the customer), so each case pairs a planted problem with a clean look-alike. Skipped without
a Postgres to make a scratch database in, or without dbt.
"""

import pytest
from fixture_world import World, build_world, drop_world

EUR = [2, "EUR"]


def build() -> World:
    w = World()
    for pid, name in [(1, "Acorn Freight"), (2, "Birch Clinics"), (3, "Cedar Labs"), (4, "Dune Capital"),
                      (5, "TEST - internal sandbox"), (6, "Elm Retail"), (7, "Fir Hotels"), (8, "Gale Foods")]:
        w.partner(pid, name)
    # Acorn: a normal 30-day history, then one invoice keyed due a year before it was issued.
    w.invoice(101, "INV/101", 1, "2026-03-01", "2026-03-31", 1000, 0)
    w.invoice(102, "INV/102", 1, "2026-04-01", "2026-05-01", 1000, 0)
    w.invoice(103, "INV/103", 1, "2026-09-02", "2025-10-02", 2000, 2000)
    # Euro invoices: three at the usual rate, one converted ten times too low.
    for iid, day in [(201, "05"), (202, "06"), (203, "07")]:
        w.invoice(iid, f"INV/{iid}", 2, f"2026-{day}-01", f"2026-{day}-28", 1100, 0, currency_id=EUR,
                  amount_total=1000, amount_total_signed=1100)
    w.invoice(204, "INV/204", 2, "2026-07-15", "2026-08-15", 104.5, 0, currency_id=EUR,
              amount_total=950, amount_total_signed=104.5)
    # Every clean invoice above is paid by its own customer.
    for n, (iid, partner, amt) in enumerate([(101, 1, 1000), (102, 1, 1000), (201, 2, 1100), (202, 2, 1100),
                                             (203, 2, 1100), (204, 2, 104.5)]):
        w.payment(9100 + n, partner, amt, "2026-09-01", [iid])
    # Tax inside a total.
    w.invoice(205, "INV/205", 2, "2026-08-01", "2026-08-31", 1200, 1200, amount_untaxed=1000, amount_tax=200)
    # Cedar: the same charge billed twice, the copy by hand with a telling reference; a zero invoice.
    w.order(301, None, 3, 500, invoices=[301])
    w.invoice(301, "INV/301", 3, "2026-07-31", "2026-08-30", 500, 0)
    w.payment(9301, 3, 500, "2026-08-20", [301])
    w.invoice(302, "INV/302", 3, "2026-08-04", "2026-09-03", 500, 500, ref="copy of INV/301")
    w.invoice(303, "INV/303", 3, "2026-09-01", "2026-10-01", 0, 0)
    # Dune Capital pays Elm Retail's invoice; Elm itself pays nothing.
    w.invoice(601, "INV/601", 6, "2026-06-01", "2026-07-01", 800, 0)
    w.payment(4001, 4, 800, "2026-06-20", [601])
    # A payment dated before its invoice; a draft payment; a receipt with no customer.
    w.invoice(701, "INV/701", 7, "2026-08-12", "2026-09-11", 900, 0)
    w.payment(7001, 7, 900, "2025-09-04", [701])
    w.invoice(702, "INV/702", 7, "2026-09-20", "2026-10-20", 300, 300)
    w.payment(7002, 7, 300, "2026-10-20", [], state="draft")
    w.invoice(801, "INV/801", 8, "2026-09-01", "2026-10-01", 450, 450)
    w.payment(8001, None, 450, "2026-10-19", [])
    # A short payment explained only in its memo.
    w.invoice(802, "INV/802", 8, "2026-08-01", "2026-08-31", 1000, 200)
    w.payment(8002, 8, 800, "2026-09-01", [802], ref="net of our credit for damaged goods")
    # Internal account billed like a customer.
    w.invoice(501, "INV/501", 5, "2026-03-10", "2026-04-09", 99, 99)
    # Deals: an order filed under another customer's deal; a deal archived as lost with a paid order.
    w.deal(11, 6, name="Elm expansion", revenue=5000, won=False)
    w.order(1101, 11, 7, 900, invoices=[701])
    w.deal(12, 1, name="Acorn renewal", revenue=1000, active=False)
    w.order(1201, 12, 1, 1000, invoices=[101])
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


def test_a_due_date_before_the_invoice_date_is_flagged_with_the_usual_term(db):
    got = issues(db, "due_date_before_invoice_date")
    assert set(got) == {"INV/103"}
    assert "30-day term it would be due 2026-10-02, 25 days past due" in got["INV/103"]


def test_an_exchange_rate_out_of_line_with_its_currency_is_flagged_and_the_usual_ones_are_not(db):
    got = issues(db, "exchange_rate_out_of_line")
    assert set(got) == {"INV/204"}
    assert "10.0 times off" in got["INV/204"]


def test_tax_inside_a_total_is_flagged(db):
    assert set(issues(db, "tax_inside_total")) == {"INV/205"}


def test_a_hand_made_copy_of_a_paid_invoice_is_a_possible_duplicate(db):
    got = issues(db, "possible_duplicate_invoice")
    assert set(got) == {"INV/302"}
    assert "INV/301 is paid" in got["INV/302"]
    assert "INV/302" in issues(db, "note_flags_a_problem")


def test_a_zero_invoice_is_flagged(db):
    assert set(issues(db, "zero_value_invoice")) == {"INV/303"}


def test_a_payer_who_is_not_the_customer_is_flagged(db):
    got = issues(db, "paid_by_another_party")
    assert len(got) == 1 and "paid by Dune Capital, not by Elm Retail" in next(iter(got.values()))


def test_payment_dates_and_states_that_cannot_be_cash_are_flagged(db):
    assert "PAY/7001" in issues(db, "payment_dated_before_invoice")
    assert "PAY/7001" in issues(db, "payment_dated_outside_books")
    draft = issues(db, "payment_not_posted")
    assert len(draft) == 1 and "INV/702" in next(iter(draft.values()))
    receipt = issues(db, "receipt_without_customer")
    assert set(receipt) == {"PAY/8001"} and "INV/801 (Gale Foods)" in receipt["PAY/8001"]


def test_a_short_payment_carries_its_memo(db):
    got = issues(db, "short_paid")
    assert set(got) == {"INV/802"} and "damaged goods" in got["INV/802"]


def test_an_internal_account_is_not_a_real_customer(db):
    got = issues(db, "not_a_real_customer")
    assert set(got) == {"TEST - internal sandbox"} and "INV/501" in got["TEST - internal sandbox"]


def test_an_order_on_another_customers_deal_and_a_lost_deal_with_sales_are_flagged(db):
    assert set(issues(db, "order_on_another_customers_deal")) == {"Elm expansion"}
    assert set(issues(db, "deal_status_contradicts_sales")) == {"Acorn renewal"}


def test_clean_customers_have_no_issues(db):
    rows = db.execute("select issue_type, entity_ref from marts.data_issues "
                      "where entity_ref in ('INV/101', 'INV/102', 'INV/201', 'INV/202', 'INV/203', 'INV/301')").fetchall()
    assert rows == []
