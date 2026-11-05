"""Approval, once-only and crash recovery, on a real Postgres with a fake Odoo.

Each test gets a fresh database holding a small overdue list. Skipped when no
Postgres is reachable (set REMIT_TEST_ADMIN_DSN to point at one).
"""

import datetime as dt
import os
import uuid

import psycopg
import pytest

from remit.ledger import Ledger
from remit.odoo_io import Reader, Writer
from remit.run import _write, apply, run

from .helpers import FakeOdoo

ADMIN = os.environ.get("REMIT_TEST_ADMIN_DSN", "host=/tmp dbname=postgres user=odoo")
AS_OF = dt.date(2026, 10, 27)
ROWS = [  # invoice, ref, customer, name, partner, invoice date, due, days, currency, local, usd, cn, up, changed, dup
    (1, "INV/1", 7, "Larkspur Foods", 7, "2026-09-01", "2026-10-12", 15, "USD", 1000, 1000, 0, 0, False, False),
    (2, "INV/2", 8, "Nimbus Print", 8, "2026-09-01", "2026-10-20", 7, "USD", 500, 500, 0, 0, False, False),
    (3, "INV/3", 9, "Keystone Legal", 9, "2026-09-01", "2026-10-20", 7, "USD", 800, 800, 0, 0, True, False),
]


@pytest.fixture
def conn():
    name = f"remit_test_{uuid.uuid4().hex[:8]}"
    try:
        admin = psycopg.connect(ADMIN, autocommit=True, connect_timeout=3)
    except psycopg.Error:
        pytest.skip("no Postgres for the ledger tests")
    admin.execute(f"create database {name}")
    c = psycopg.connect(ADMIN.replace("dbname=postgres", f"dbname={name}"))
    c.execute("""create schema marts; create table marts.overdue_invoices (
        invoice_id int, invoice_ref text, customer_key int, customer_name text, partner_id int,
        invoice_date date, due_date date, days_past_due int, currency text,
        amount_open_invoice_currency numeric, amount_open_usd numeric,
        customer_open_credit_notes_usd numeric, customer_unmatched_payments_usd numeric,
        changed_after_posting bool, customer_has_duplicate_records bool, source_ref text)""")
    for r in ROWS:
        c.execute("insert into marts.overdue_invoices values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (*r, f"account.move/{r[0]}@v1"))
    c.commit()
    yield c
    c.close()
    admin.execute(f"drop database {name} with (force)")
    admin.close()


def pending(ledger, kind=None):
    return [p for p in ledger.proposals("pending") if kind is None or p["kind"] == kind]


def test_shadow_proposals_can_never_be_approved(conn):
    run(conn, Reader(FakeOdoo()), AS_OF, "shadow")
    ledger = Ledger(conn)
    shadow = ledger.proposals("shadow")
    assert shadow and not pending(ledger)
    with pytest.raises(ValueError):
        ledger.decide(shadow[0]["proposal_id"], True, "muhammad")


def test_suggest_mode_records_agreement_and_writes_nothing(conn):
    run(conn, Reader(FakeOdoo()), AS_OF, "suggest")
    ledger = Ledger(conn)
    r = pending(ledger, "reminder")[0]
    ledger.decide(r["proposal_id"], True, "muhammad")
    statuses = {p["kind"]: p["status"] for p in ledger.proposals() if p["customer_key"] == r["customer_key"]}
    assert statuses == {"reminder": "agreed", "follow_up": "agreed"}
    o = FakeOdoo()
    assert apply(conn, Writer(o), Reader(o))["applied"] == 0
    assert o.messages == [] and o.activities == []


def test_act_mode_applies_once_and_a_rerun_proposes_nothing_new(conn):
    run(conn, Reader(FakeOdoo()), AS_OF, "act")
    ledger = Ledger(conn)
    for p in pending(ledger, "reminder"):
        ledger.decide(p["proposal_id"], True, "muhammad")
    o = FakeOdoo()
    first = apply(conn, Writer(o), Reader(o))
    assert first["applied"] == 4  # two reminders and their follow-ups
    assert apply(conn, Writer(o), Reader(o))["applied"] == 0
    assert len(o.messages) == 2 and len(o.activities) == 2
    again = run(conn, Reader(FakeOdoo()), AS_OF, "act")
    assert again["proposed"] == 0


def test_held_invoice_gets_a_flag_not_a_reminder(conn):
    run(conn, Reader(FakeOdoo()), AS_OF, "act")
    keystone = [p for p in Ledger(conn).proposals() if p["customer_key"] == 9]
    assert [(p["kind"], p["reason"]) for p in keystone] == [("flag", "changed_after_posting")]


def test_rejecting_a_reminder_rejects_its_follow_up(conn):
    run(conn, Reader(FakeOdoo()), AS_OF, "act")
    ledger = Ledger(conn)
    r = pending(ledger, "reminder")[0]
    ledger.decide(r["proposal_id"], False, "muhammad", "customer called, paying Friday")
    left = [p for p in ledger.proposals() if p["customer_key"] == r["customer_key"]]
    assert {p["status"] for p in left} == {"rejected"}


def test_a_crash_after_the_write_does_not_write_twice(conn):
    run(conn, Reader(FakeOdoo()), AS_OF, "act")
    ledger = Ledger(conn)
    r = pending(ledger, "reminder")[0]
    ledger.decide(r["proposal_id"], True, "muhammad")
    o = FakeOdoo()
    ledger.claim(r["proposal_id"])
    _write(Writer(o), r)          # the note lands, then the process dies
    out = apply(conn, Writer(o), Reader(o))
    assert out["recovered"] == 1
    assert sum(1 for i, b in o.messages if r["proposal_id"] in b) == 1


def test_a_failed_write_is_not_counted_as_done(conn):
    run(conn, Reader(FakeOdoo()), AS_OF, "act")
    ledger = Ledger(conn)
    r = pending(ledger, "reminder")[0]
    ledger.decide(r["proposal_id"], True, "muhammad")

    class Down(FakeOdoo):
        def execute(self, model, method, *a, **k):
            if method == "message_post":
                raise ConnectionError("Odoo is down")
            return super().execute(model, method, *a, **k)
    o = Down()
    assert apply(conn, Writer(o), Reader(o))["failed"] == 1
    assert not any(k in ledger.done_keys() for k in r["done_keys"])
    again = run(conn, Reader(FakeOdoo()), AS_OF, "act")
    assert again["proposed"] >= 1  # the reminder can be proposed again


def test_two_approvals_of_the_same_action_write_once(conn):
    run(conn, Reader(FakeOdoo()), AS_OF, "act")
    ledger = Ledger(conn)
    r = pending(ledger, "reminder")[0]
    ledger.decide(r["proposal_id"], True, "muhammad")
    # A second copy of the same action, as if two people queued it, sharing its done keys.
    conn.execute("""insert into remit.proposals select 'p-copy', run_id, customer_key, customer_name, kind,
                    stage, reason, target_invoice_id, invoice_ids, source_refs, done_keys,
                    idempotency_key || '-copy', follow_up_on, subject, body, drafted_by, draft_problems,
                    notes, evidence, 'approved' from remit.proposals where proposal_id = %s""",
                 (r["proposal_id"],))
    conn.commit()
    o = FakeOdoo()
    out = apply(conn, Writer(o), Reader(o))
    assert out["skipped"] == 1
    assert len(o.messages) == 1


def test_the_audit_log_cannot_be_changed(conn):
    run(conn, Reader(FakeOdoo()), AS_OF, "shadow")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("delete from remit.audit")
    conn.rollback()
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("update remit.audit set actor = 'someone else'")
    conn.rollback()
