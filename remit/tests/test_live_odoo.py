"""Remit against a real Odoo and the built warehouse: approve, apply once, survive a crash.

Runs only with REMIT_LIVE=1, after `python -m clearledger.demo` has built the company and
the warehouse (CI does this as its last step). It writes notes and activities
into that Odoo, so it never runs against a database someone works in.
"""

import datetime as dt
import os

import psycopg
import pytest

from clearledger.ask import AS_OF
from clearledger.odoo import Odoo
from remit.ledger import Ledger
from remit.odoo_io import Reader, Writer
from remit.run import _write, apply, run

pytestmark = pytest.mark.skipif(os.environ.get("REMIT_LIVE") != "1", reason="needs REMIT_LIVE=1")


@pytest.fixture(scope="module")
def odoo():
    return Odoo(os.environ.get("ODOO_URL", "http://localhost:8069"), os.environ.get("ODOO_DB", "acme"),
                os.environ.get("ODOO_USER", "admin"), os.environ.get("ODOO_PASSWORD", "admin"))


@pytest.fixture(scope="module")
def conn():
    c = psycopg.connect(os.environ.get("CLEARLEDGER_DSN", "host=/tmp dbname=warehouse user=odoo"))
    yield c
    c.close()


def notes(odoo, invoice_id, proposal_id):
    return odoo.execute("mail.message", "search_count", [
        ("model", "=", "account.move"), ("res_id", "=", invoice_id),
        ("body", "ilike", f"[remit:{proposal_id}]")])


def test_approve_apply_once_and_recover_from_a_crash(odoo, conn):
    out = run(conn, Reader(odoo), dt.date.fromisoformat(AS_OF), "act")
    assert out["invoices"] > 0
    ledger = Ledger(conn)
    reminders = [p for p in ledger.proposals("pending") if p["kind"] == "reminder"]
    assert len(reminders) >= 2, "the demo company should have customers to remind"
    first, second = reminders[:2]

    ledger.decide(first["proposal_id"], True, "ci")
    assert apply(conn, Writer(odoo), Reader(odoo))["applied"] == 2   # the reminder and its follow-up
    assert notes(odoo, first["target_invoice_id"], first["proposal_id"]) == 1
    assert apply(conn, Writer(odoo), Reader(odoo))["applied"] == 0
    assert notes(odoo, first["target_invoice_id"], first["proposal_id"]) == 1

    # The write lands, then the process dies before recording it.
    ledger.decide(second["proposal_id"], True, "ci")
    ledger.claim(second["proposal_id"])
    _write(Writer(odoo), second)
    assert apply(conn, Writer(odoo), Reader(odoo))["recovered"] == 1
    assert notes(odoo, second["target_invoice_id"], second["proposal_id"]) == 1

    # A new run the same day proposes nothing it already did or queued.
    again = run(conn, Reader(odoo), dt.date.fromisoformat(AS_OF), "act")
    assert again["proposed"] == 0
