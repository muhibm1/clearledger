"""marts.data_issues, second small company: what the notes on a record mean, and records that contradict
what sales and banking say (a lead counted as pipeline, a quote billed by hand, an order never billed,
a receipt keyed as money paid out, tax at the wrong rate).

Each planted problem sits next to a clean look-alike that must stay quiet. Skipped without a Postgres
to make a scratch database in, or without dbt.
"""

import pytest
from fixture_world import World, build_world, drop_world


def build() -> World:
    w = World()
    for pid, name in [(1, "Alder Builders"), (2, "Bay Events"), (3, "Cove Drilling"), (4, "Delta Farms"),
                      (5, "Echo Media")]:
        w.partner(pid, name)
    w.add("crm_stage", 2, {"name": "New", "is_won": False, "sequence": 0})

    def open_deal(did, partner, name, revenue, kind="opportunity"):
        w.add("crm_lead", did, {"name": name, "type": kind, "partner_id": [partner, "x"] if partner else False,
                                "stage_id": [2, "New"], "expected_revenue": revenue, "date_closed": False,
                                "active": True, "create_date": "2026-01-01 00:00:00"})

    # Pipeline: four ordinary opportunities, one keyed a thousand times too big, one lead with money on it,
    # and one lead with none.
    for did, rev in [(21, 9000), (22, 11000), (23, 10000), (24, 12000)]:
        open_deal(did, 1, f"Alder job {did}", rev)
    open_deal(25, 4, "Delta silo", 10_500_000)
    open_deal(26, None, "Trade show enquiry", 60000, kind="lead")
    open_deal(27, None, "Cold call", 0, kind="lead")

    # Alder: orders billed about ten days after they are confirmed, every one paid. Tax at 8.25% on
    # three invoices; the fourth carries it twice.
    for n, (oid, day) in enumerate([(101, "02"), (102, "03"), (103, "04"), (104, "05")]):
        iid = 1000 + oid
        w.order(oid, None, 1, 1082.5, invoices=[iid], date=f"2026-{day}-01 00:00:00")
        tax = 165 if oid == 104 else 82.5
        w.invoice(iid, f"INV/{iid}", 1, f"2026-{day}-11", f"2026-{day}-28", 1000 + tax, 0,
                  amount_untaxed=1000, amount_tax=tax, origin=f"S{oid:05d}")
        w.payment(5000 + n, 1, 1000 + tax, f"2026-{day}-20", [iid])
    # Two more confirmed orders with nothing billed: one months old, one from last week.
    w.order(105, None, 1, 2000, date="2026-05-01 00:00:00")
    w.order(106, None, 1, 2000, date="2026-10-22 00:00:00")

    # Bay Events: a job billed by hand against a quotation that was never confirmed; a refundable deposit.
    w.order(201, None, 2, 3000, state="draft")
    w.invoice(2001, "INV/2001", 2, "2026-06-10", "2026-07-10", 3000, 0, origin="S00201")
    w.payment(5201, 2, 3000, "2026-07-01", [2001])
    w.invoice(2002, "INV/2002", 2, "2026-06-12", "2026-07-12", 500, 500,
              ref="Refundable equipment deposit - returned at off-hire less any damage")

    # Cove Drilling: a receipt keyed as money paid out, then the real receipt three days later; and an
    # ordinary refund paid out against a credit note.
    w.invoice(3001, "INV/3001", 3, "2026-08-01", "2026-08-31", 4000, 0)
    w.payment(5301, 3, 4000, "2026-08-28", [], kind="outbound", ref="Wire received from Cove Drilling")
    w.payment(5302, 3, 4000, "2026-08-31", [3001])
    w.invoice(3002, "INV/3002", 3, "2026-08-05", "2026-09-05", 300, 0)
    w.payment(5303, 3, 300, "2026-08-20", [3002])
    w.invoice(3003, "RINV/3003", 3, "2026-08-25", "2026-08-25", 300, 0, kind="out_refund")
    w.payment(5304, 3, 300, "2026-08-26", [3003], kind="outbound")

    # Echo Media: the memo pays the newer invoice and disputes the older, but the cash went on the older.
    w.invoice(4001, "INV/2026/04001", 5, "2026-07-01", "2026-07-31", 900, 900)
    w.invoice(4002, "INV/2026/04002", 5, "2026-08-01", "2026-08-31", 900, 0)
    w.payment(5401, 5, 900, "2026-09-02", [4002], ref="Remittance for INV/2026/04001 only. INV/2026/04002 withheld: disputed")
    # Fern Clinics: a site keyed as its own company on the parent's mail domain; a lookalike on a public
    # mail domain stays apart. Gorse LLP: recorded in the US, mails from .co.uk, billed only in pounds.
    w.partner(6, "Fern Clinics Group", email="ap@fernclinics.example.com")
    w.partner(7, "Fern Clinics - Lakeside", email="lakeside@fernclinics.example.com")
    w.partner(8, "Hazel Cafe", email="hazel@gmail.com")
    w.partner(9, "Ivy Bakery", email="ivy@gmail.com")
    w.add("res_partner", 10, {"name": "Gorse LLP", "email": "ap@gorse.example.co.uk", "is_company": True, "active": True,
                              "commercial_partner_id": [10, "Gorse LLP"], "country_id": [233, "United States"]})
    for iid, partner in [(6001, 6), (6002, 7), (6003, 8), (6004, 9)]:
        w.invoice(iid, f"INV/{iid}", partner, "2026-09-01", "2026-10-01", 100, 0)
        w.payment(iid + 1000, partner, 100, "2026-09-15", [iid])
    w.invoice(6005, "INV/6005", 10, "2026-09-01", "2026-10-01", 200, 0, currency_id=[3, "GBP"],
              amount_total=160, amount_total_signed=200)
    w.payment(7005, 10, 200, "2026-09-15", [6005])
    # One invoice listed by two orders.
    w.order(301, None, 4, 600, invoices=[6101], date="2026-09-01 00:00:00")
    w.order(302, None, 4, 400, invoices=[6101], date="2026-09-01 00:00:00")
    w.invoice(6101, "INV/6101", 4, "2026-09-05", "2026-10-05", 1000, 0)
    w.payment(7101, 4, 1000, "2026-09-20", [6101])
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


def test_a_lead_with_expected_revenue_is_not_pipeline(db):
    got = issues(db, "lead_not_opportunity")
    assert set(got) == {"Trade show enquiry"}
    assert "60,000.00" in got["Trade show enquiry"]


def test_an_open_deal_far_above_every_other_is_flagged(db):
    assert set(issues(db, "deal_value_out_of_line")) == {"Delta silo"}


def test_a_job_billed_on_a_quotation_never_confirmed_is_flagged(db):
    got = issues(db, "billed_on_unconfirmed_order")
    assert set(got) == {"INV/2001"}
    assert "S00201" in got["INV/2001"] and "draft" in got["INV/2001"]


def test_an_old_confirmed_order_with_no_invoice_is_flagged_and_a_recent_one_is_not(db):
    assert set(issues(db, "order_not_invoiced")) == {"S00105"}


def test_a_receipt_keyed_as_money_paid_out_is_flagged_and_a_real_refund_is_not(db):
    got = issues(db, "outbound_payment_unsupported")
    assert set(got) == {"PAY/5301"}
    assert "coming in" in got["PAY/5301"] and "PAY/5302" in got["PAY/5301"]


def test_tax_at_twice_the_usual_rate_is_flagged(db):
    assert set(issues(db, "tax_rate_out_of_line")) == {"INV/1104"}


def test_a_refundable_deposit_reads_as_a_deposit_not_a_dispute_or_a_credit(db):
    detail = issues(db, "note_flags_a_problem")["INV/2002"]
    assert "not revenue" in detail
    assert "dispute" not in detail.lower() and "A credit" not in detail


def test_a_memo_naming_another_invoice_than_the_one_paid_is_flagged(db):
    got = issues(db, "payment_memo_names_other_invoice")
    assert set(got) == {"PAY/5401"}
    assert "INV/2026/04002" in got["PAY/5401"]


def test_clean_records_have_no_issues(db):
    rows = db.execute("select issue_type, entity_ref from marts.data_issues where entity_ref in "
                      "('INV/1101', 'INV/1102', 'INV/1103', 'S00106', 'Cold call', 'Alder job 21', 'PAY/5304', "
                      "'PAY/5302', 'INV/3002', 'RINV/3003') and issue_type not in ('tax_inside_total')").fetchall()
    assert rows == []


def test_a_site_keyed_as_its_own_company_on_the_parents_domain_is_flagged(db):
    got = issues(db, "related_customer_not_linked")
    assert set(got) == {"Fern Clinics - Lakeside"}
    assert "Fern Clinics Group" in got["Fern Clinics - Lakeside"]


def test_a_country_its_mail_domain_and_currency_contradict_is_flagged(db):
    got = issues(db, "customer_country_out_of_line")
    assert set(got) == {"Gorse LLP"}
    assert "GBP" in got["Gorse LLP"]


def test_an_invoice_listed_by_two_orders_is_flagged_and_its_orders_are_not_unbilled(db):
    got = issues(db, "invoice_on_several_orders")
    assert set(got) == {"INV/6101"}
    assert "S00301" in got["INV/6101"] and "S00302" in got["INV/6101"]
    assert not {"S00301", "S00302"} & set(issues(db, "order_not_invoiced"))
