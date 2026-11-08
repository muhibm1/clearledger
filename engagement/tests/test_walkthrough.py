"""The walkthrough's labels and exit code. The first tests need no database; the last needs a built warehouse."""

import json
import subprocess
import sys

import psycopg
import pytest

from engagement import walkthrough


@pytest.fixture
def no_stack(monkeypatch, tmp_path):
    monkeypatch.setenv("EDP_DSN", "host=127.0.0.1 port=1 dbname=none user=none")
    monkeypatch.setenv("EDP_ANALYST_PYTHON", str(tmp_path / "no-such-python"))
    monkeypatch.delenv("LODESTAR_URL", raising=False)


def test_without_a_stack_steps_are_recorded_or_skipped(no_stack, capsys, tmp_path):
    out = tmp_path / "walk.json"
    code = walkthrough.main(["--json", str(out)])
    text = capsys.readouterr().out
    result = json.loads(out.read_text())
    modes = {s["step"]: s["mode"] for s in result["steps"]}
    assert modes[1] == "RECORDED" and modes[2] == "SKIPPED" and modes[4] == "RECORDED"
    assert modes[3] in ("LIVE", "SKIPPED")
    assert "SIMULATED ENGAGEMENT" in text
    assert all(s["reason"] for s in result["steps"])
    assert code == (1 if result["failed"] else 0) and not [s for s in result["steps"] if s["mode"] != "LIVE" and not s["ok"]]


def test_recorded_steps_say_where_they_came_from(no_stack):
    conn, why = walkthrough.connect()
    assert conn is None
    s1, _ = walkthrough.trace_step(conn, why)
    s4 = walkthrough.harbor_step("q")
    assert "revenue_by_customer" in s1["reason"] and "trace-northwind" in s1["reason"]
    assert "7770b72" in s4["reason"] and "echo" in s4["reason"] and "stub" in s4["reason"]
    assert s4["result"]["refusal"]["http_status"] == 429


def test_a_live_step_that_fails_makes_the_exit_code_one(no_stack, monkeypatch):
    monkeypatch.setattr(walkthrough, "fathom_guard", lambda: (False, "BLOCK missing"))
    assert walkthrough.main([]) == 1


def test_a_skipped_guard_does_not_fail_the_run(no_stack, monkeypatch):
    monkeypatch.setattr(walkthrough, "fathom_guard", lambda: (None, "skipped: dbt or fathom is not installed"))
    assert walkthrough.main([]) == 0


def test_no_subcommand_still_runs_the_original_demo_cli():
    r = subprocess.run([sys.executable, "-m", "engagement", "--help"], capture_output=True, text=True, cwd=walkthrough.ROOT)
    assert r.returncode == 0 and "--json" in r.stdout and "walkthrough" not in r.stdout.split("options")[0]


def test_step_one_is_live_on_a_built_warehouse():
    try:
        c = psycopg.connect(walkthrough.DSN, connect_timeout=3)
        c.execute("select 1 from marts.receivables_aging limit 1")
        c.close()
    except psycopg.Error:
        pytest.skip("no built warehouse")
    conn, why = walkthrough.connect()
    try:
        s1, customer = walkthrough.trace_step(conn, why)
    finally:
        conn.close()
    assert s1["mode"] == "LIVE" and s1["ok"] and customer
    assert "DOES NOT ADD UP" not in s1["result"]["text"] and "Open invoices" in s1["result"]["text"]


def test_an_ambiguous_customer_name_is_a_failed_step_not_the_end_of_the_run(monkeypatch):
    class Conn:
        def execute(self, *a, **k):
            class R:
                def fetchone(self):
                    return ("Northwind", 10.0)
            return R()

    def ambiguous(conn, table, key):
        raise SystemExit("'Northwind' matches 2 customers in receivables_aging: Northwind, Northwind East")

    monkeypatch.setattr("edp.trace.trace", ambiguous)
    s1, customer = walkthrough.trace_step(Conn(), None)
    assert s1["mode"] == "LIVE" and not s1["ok"] and "matches 2 customers" in s1["reason"] and customer is None
