"""Step 2 of the engagement walkthrough, on the finance warehouse. No model is called (FakeModel)."""

import os

import psycopg
import pytest

from analyst import domain as domains
from engagement import analyst_step


@pytest.fixture(scope="module")
def finance_up():
    dom = domains.load("finance")
    try:
        with psycopg.connect(dom.dsn, connect_timeout=3) as conn:
            conn.execute("select 1 from marts.receivables_aging limit 1")
    except psycopg.Error:
        if os.environ.get("ANALYST_TESTS_NEED_DB") == "1":
            raise
        pytest.skip("no built finance warehouse")


def test_the_scripted_analyst_answers_with_citations_that_check_out(finance_up):
    out = analyst_step.run()
    assert out["mode"] == "STUB" and out["ok"], out["problems"]
    assert out["status"] == "answer" and out["invoices"] >= 1
    assert out["citations"]["checked"] == out["invoices"] and out["citations"]["all_ok"]
    assert abs(out["invoices_total"] - out["receivables_aging_late_over_90"]) < 0.01
