"""The finance models on a small invented company: each case is one thing real books do.

The world is built from nothing but the rows in `build()`, loaded into a scratch Postgres database,
and the real models run over it (see fixture_world.py). Skipped without a Postgres to make a scratch
database in, or without dbt.
"""

import datetime as dt
from decimal import Decimal

import pytest
from fixture_world import World, build_world, drop_world

AS_OF = dt.date(2026, 10, 27)


def build() -> World:
    w = World()
    # Installments: a retainage-style term whose header due date is its last installment.
    w.partner(1, "Alpha Builders")
    w.partner(2, "Beta Supply")
    w.invoice(101, "INV/1", 1, "2026-05-01", "2027-02-01", 1000, 1000)
    w.line(1001, 101, "2026-08-01", 900, 900)
    w.line(1002, 101, "2027-02-01", 100, 100)
    # No installment lines loaded: the header due date stands in.
    w.invoice(102, "INV/2", 1, "2026-07-01", "2026-09-01", 500, 500)
    # Lines that do not add up to what the invoice says is open: the header stands in too.
    w.invoice(103, "INV/3", 1, "2026-07-01", "2026-09-01", 400, 400)
    w.line(1003, 103, "2027-01-01", 400, 100)
    # A residue under the materiality floor is not late money.
    w.invoice(104, "INV/4", 2, "2026-07-01", "2026-09-01", 300, 0.40)
    w.line(1004, 104, "2026-09-01", 300, 0.40)
    return w


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    conn = build_world(build(), tmp_path_factory.mktemp("dbt"))
    if conn is None:
        pytest.skip("no scratch Postgres or no dbt here")
    yield conn
    drop_world(conn)


def one(db, sql, *args):
    row = db.execute(sql, args).fetchone()
    return row


def d(x):
    return Decimal(str(x))


def test_each_installment_is_aged_by_its_own_due_date(db):
    row = one(db, "select late_61_90, late_31_60, not_yet_due, total_open from marts.receivables_aging "
                  "where customer_name = 'Alpha Builders'")
    # 900 fell due 2026-08-01 (87 days late); 100 is not due until 2027; the two invoices whose
    # installments cannot be used (none loaded, or lines that disagree) age on the header date.
    assert row == (d(900), d(900), d(100), d(1900))


def test_an_invoice_with_no_usable_installments_falls_back_to_its_header_due_date(db):
    days = one(db, "select days_past_due from intermediate.int_invoices where invoice_ref = 'INV/2'")[0]
    assert days == (AS_OF - dt.date(2026, 9, 1)).days
    basis = db.execute("select basis from intermediate.int_invoice_dues where invoice_id in (102, 103)").fetchall()
    assert {b for (b,) in basis} == {"invoice header"}


def test_the_overdue_list_dates_an_invoice_by_its_oldest_open_installment(db):
    row = one(db, "select due_date, days_past_due, amount_open_usd, amount_due_now_usd "
                  "from marts.overdue_invoices where invoice_ref = 'INV/1'")
    assert row == (dt.date(2026, 8, 1), 87, d(1000), d(900))


def test_a_residue_under_the_materiality_floor_is_not_late_and_is_not_chased(db):
    row = one(db, "select late_31_60, rounding_residue, total_open from marts.receivables_aging "
                  "where customer_name = 'Beta Supply'")
    assert row == (None, d("0.40"), d("0.40"))
    assert one(db, "select count(*) from marts.overdue_invoices where invoice_ref = 'INV/4'")[0] == 0
