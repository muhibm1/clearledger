"""A tiny invented company, loaded straight into raw tables of a scratch database, for model tests.

The models are real SQL over real Postgres: a test builds a world out of a few partners, invoices,
payments and orders with `World`, runs `dbt run` over it, and reads the marts. Nothing here knows
any real company. `build_world` returns None (the test skips) when there is no Postgres to make a
scratch database in, or no dbt next to the running Python.
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("CLEARLEDGER_DSN", "host=/tmp dbname=warehouse user=odoo")
WHEN = "2026-10-01 09:00:00"


def m2o(i, name=""):
    return [i, name] if i else False


class World:
    """Rows for the raw tables, in Odoo's JSON shape."""

    def __init__(self):
        self.rows: dict[str, list] = {}

    def add(self, table, odoo_id, payload, write_date=WHEN):
        self.rows.setdefault(table, []).append((odoo_id, write_date, payload))

    def partner(self, pid, name, email=False, commercial=None, active=True, write_date=WHEN):
        self.add("res_partner", pid, {"name": name, "email": email, "is_company": True, "active": active,
                                      "commercial_partner_id": m2o(commercial or pid), "country_id": False},
                 write_date)

    def invoice(self, iid, name, partner, date, due, amount, open_=0.0, state="posted", kind="out_invoice",
                origin=False, write_date=WHEN, partner_name=""):
        sign = -1 if kind == "out_refund" else 1
        self.add("account_move", iid, {
            "name": name, "move_type": kind, "state": state, "partner_id": m2o(partner, partner_name),
            "invoice_date": date, "invoice_date_due": due, "currency_id": [1, "USD"],
            "amount_total": amount, "amount_total_signed": sign * amount,
            "amount_residual": open_, "amount_residual_signed": sign * open_,
            "payment_state": "paid" if not open_ else "not_paid", "invoice_origin": origin}, write_date)

    def line(self, lid, invoice, due, balance, open_, state="posted"):
        self.add("account_move_line", lid, {
            "move_id": m2o(invoice, "x"), "date_maturity": due, "balance": balance, "amount_residual": open_,
            "parent_state": state, "display_type": "payment_term", "partner_id": False})

    def payment(self, pid, partner, amount, date, invoices=(), state="posted", kind="inbound"):
        self.add("account_payment", pid, {
            "name": f"PAY/{pid}", "partner_id": m2o(partner, "x"), "date": date, "state": state,
            "payment_type": kind, "currency_id": [1, "USD"],
            "amount_company_currency_signed": amount if kind == "inbound" else -amount,
            "reconciled_invoice_ids": list(invoices)})

    def order(self, oid, deal, partner, amount, state="sale", invoices=(), date="2026-03-01 00:00:00"):
        self.add("sale_order", oid, {
            "name": f"S{oid:05d}", "partner_id": m2o(partner, "x"), "opportunity_id": m2o(deal, "d"),
            "state": state, "date_order": date, "amount_total": amount, "currency_id": [1, "USD"],
            "invoice_ids": list(invoices)})

    def deal(self, did, partner, name="Job", revenue=0.0, won=True):
        self.add("crm_stage", 1, {"name": "Won", "is_won": True, "sequence": 1})
        self.add("crm_lead", did, {"name": name, "type": "opportunity", "partner_id": m2o(partner, "x"),
                                   "stage_id": [1, "Won"], "expected_revenue": revenue, "date_closed": "2026-03-01 00:00:00",
                                   "active": True, "create_date": "2026-01-01 00:00:00"})


def _dbt():
    exe = Path(sys.executable).parent / "dbt"
    return str(exe) if exe.exists() else None


def build_world(world: World, tmp: Path, select: str | None = None):
    """Loads `world` into a new scratch database, runs the models, and returns an open connection
    to it. Returns None when there is no way to do that here."""
    dbt = _dbt()
    if dbt is None:
        return None
    base = conninfo_to_dict(DSN)
    name = "cl_fixture_" + uuid.uuid4().hex[:10]
    try:
        admin = psycopg.connect(make_conninfo(**{**base, "dbname": "postgres"}), autocommit=True, connect_timeout=3)
        admin.execute(f'create database "{name}"')
    except psycopg.Error:
        return None
    admin.close()
    conn = psycopg.connect(make_conninfo(**{**base, "dbname": name}), autocommit=True)
    sys.path.insert(0, str(ROOT))
    from clearledger.load import ensure_schema
    ensure_schema(conn)
    for table, rows in world.rows.items():
        for odoo_id, write_date, payload in rows:
            conn.execute(f"insert into raw.{table} (odoo_id, write_date, payload, run_id) values (%s, %s, %s, 'fixture') "
                         "on conflict do nothing", (odoo_id, write_date, json.dumps(payload)))
    env = {**os.environ, "CLEARLEDGER_PGDATABASE": name,
           "CLEARLEDGER_PGHOST": base.get("host", "/tmp"), "CLEARLEDGER_PGPORT": str(base.get("port", 5432)),
           "CLEARLEDGER_PGUSER": base.get("user", "odoo"), "CLEARLEDGER_PGPASSWORD": base.get("password", ""),
           "DBT_TARGET_PATH": str(tmp / "target"), "DBT_LOG_PATH": str(tmp / "logs")}
    cmd = [dbt, "run", "--project-dir", str(ROOT / "dbt"), "--profiles-dir", str(ROOT / "dbt")]
    out = subprocess.run(cmd + (["--select", select] if select else []), capture_output=True, text=True, env=env, check=False)
    if out.returncode != 0:
        conn.close()
        raise RuntimeError("dbt run failed in the fixture world:\n" + out.stdout[-2000:])
    conn.name = name
    conn.admin_base = base
    return conn


def drop_world(conn):
    """Drops the scratch database behind `conn`."""
    name, base = conn.name, conn.admin_base
    conn.close()
    with psycopg.connect(make_conninfo(**{**base, "dbname": "postgres"}), autocommit=True) as admin:
        admin.execute(f'drop database if exists "{name}" with (force)')
