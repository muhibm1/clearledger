"""Every field extracted from Odoo (clearledger/sources.py) reaches a staging model.

A field loaded into raw but dropped in staging is invisible to every question: the analyst can only
read the modeled schemas. This reads the staging SQL and checks that each extracted field of each
model is read from that model's raw table somewhere in staging.
"""

import re
from pathlib import Path

from clearledger.sources import SOURCES, table_name

STAGING = Path(__file__).resolve().parents[1] / "dbt" / "models" / "staging"
# How a staging model reads a field from a raw payload: payload->>'x', payload->'x', or a macro.
FIELD_READ = re.compile(r"""payload\s*->>?\s*'(\w+)'|(?:m2o_id|m2o_name|txt)\(\s*'(\w+)'\s*\)""")
RAW_TABLE = re.compile(r"""(?:latest|source)\(\s*'(?:odoo'\s*,\s*')?(\w+)'""")


def fields_read_per_table() -> dict[str, set[str]]:
    """Maps each raw table to every payload field the staging models read from it."""
    out: dict[str, set[str]] = {}
    for path in STAGING.glob("*.sql"):
        sql = path.read_text()
        tables = {t for t in RAW_TABLE.findall(sql) if t != "deleted"}
        fields = {a or b for a, b in FIELD_READ.findall(sql)}
        for t in tables:
            out.setdefault(t, set()).update(fields)
    return out


def test_every_source_field_reaches_staging():
    """No field in SOURCES is loaded and then dropped before staging."""
    read = fields_read_per_table()
    missing = {model: sorted(set(fields) - read.get(table_name(model), set()))
               for model, fields in SOURCES.items()}
    missing = {m: f for m, f in missing.items() if f}
    assert not missing, f"fields extracted but never read in staging: {missing}"


def test_payment_keeps_its_own_currency_amount():
    """The payment's amount in its own currency and the currency itself both reach staging."""
    sql = (STAGING / "stg_payments.sql").read_text()
    assert "payload->>'amount')::numeric as amount_in_payment_currency" in sql
    assert "m2o_name('currency_id')" in sql


def test_parser_sees_a_dropped_field():
    """The check would catch a field that no staging model reads."""
    read = fields_read_per_table()
    assert "not_a_field" not in read.get("account_payment", set())
    assert "amount" in read["account_payment"]
