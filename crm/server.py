"""The finance CRM: one page over the warehouse, with the voice agent on it.

    .venv/bin/python -m crm.server            # http://127.0.0.1:4320

It shows every customer from both sides of the business: the CRM side (deals in the
pipeline, deals won) and the ERP side (orders, invoices, payments, what is late). Nothing
here writes; changes still happen in Odoo.

The same server answers the voice agent's tool calls from the page (`POST /api/tools/<name>`,
through `voice/bridge.py`), and, when `ELEVENLABS_API_KEY` is set, hands the page a short-lived
signed address for one conversation. The key itself never reaches the browser.
"""

import argparse
import json
import mimetypes
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg

from edp import ask
from voice.bridge import Bridge, ToolError

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
IDS = HERE.parent / "voice" / "agent-ids.json"
RUNS = HERE.parent / "voice" / "runs"

CUSTOMERS_SQL = """
with deals as (
    select c.customer_key,
           count(*) filter (where not d.is_won and not d.is_lost) as open_deals,
           coalesce(sum(d.expected_revenue) filter (where not d.is_won and not d.is_lost), 0) as pipeline
    from staging.stg_deals d join intermediate.int_customers c using (partner_id)
    group by c.customer_key),
won as (
    select customer_key, count(*) as won_deals,
           count(*) filter (where status <> 'paid') as won_not_cash
    from marts.deal_to_cash group by customer_key),
late as (
    select customer_key, max(days_past_due) filter (where amount_open <> 0) as most_days_late
    from intermediate.int_invoices group by customer_key)
select r.customer_key, r.customer_name, r.invoiced as billed, r.paid, r.open as owed,
       -- Late money still owed: a credit note can cancel a late invoice, so never more than owed.
       greatest(least(coalesce(a.late_1_30, 0) + coalesce(a.late_31_60, 0) + coalesce(a.late_61_90, 0)
           + coalesce(a.late_over_90, 0), r.open), 0) as late,
       coalesce(a.late_over_90, 0) as late_over_90, greatest(l.most_days_late, 0) as most_days_late,
       coalesce(d.open_deals, 0) as open_deals, coalesce(d.pipeline, 0) as pipeline,
       coalesce(w.won_deals, 0) as won_deals, coalesce(w.won_not_cash, 0) as won_not_cash
from marts.revenue_by_customer r
left join marts.receivables_aging a using (customer_key)
left join deals d using (customer_key)
left join won w using (customer_key)
left join late l using (customer_key)
order by r.open desc, r.customer_name
"""

PIPELINE_SQL = """
select d.deal_id, d.deal_name, d.stage, d.expected_revenue, d.created_at, d.is_lost
from staging.stg_deals d join intermediate.int_customers c using (partner_id)
where c.customer_key = %s and not d.is_won
order by d.is_lost, d.created_at desc
"""

PAYMENTS_SQL = f"""
select p.payment_ref, p.payment_date, p.currency, p.amount, {ask.PAYMENT_REF} as source_ref
from staging.stg_payments p join intermediate.int_customers c using (partner_id)
where c.customer_key = %s
order by p.payment_date
"""


def _read(dsn: str, sql: str, args=()) -> list[dict]:
    """Rows from one read-only query, run as the warehouse's read-only role."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.transaction():
            conn.execute("set transaction read only")
            conn.execute(f"set local role {ask.READER}")
            return ask._rows(conn, sql, args)


def customers(dsn: str) -> dict:
    """Every customer with both sides: pipeline and won deals, billed, paid, owed and late."""
    return {"aged_as_of": ask.AS_OF, "customers": _read(dsn, CUSTOMERS_SQL)}


def customer(dsn: str, key: int) -> dict | None:
    """One customer's page: summary, won deals followed to cash, open pipeline, invoices, payments."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        summary = ask.customer_summary(conn, key)
        if not summary["found"]:
            return None
        partners = ask._rows(conn, """select c.partner_id || ': ' || p.partner_name as partner
                                      from intermediate.int_customers c join staging.stg_partners p using (partner_id)
                                      where c.customer_key = %s order by c.partner_id""", (key,))
        return {
            "summary": summary,
            "partners": [p["partner"] for p in partners],
            "won_deals": ask.list_deals(conn, key)["won_deals"],
            "invoices": ask.list_invoices(conn, key)["invoices"],
            "pipeline": _read(dsn, PIPELINE_SQL, (key,)),
            "payments": _read(dsn, PAYMENTS_SQL, (key,)),
        }


def voice_session() -> dict:
    """A signed address for one voice conversation, or why voice is not set up yet."""
    if not os.environ.get("ELEVENLABS_API_KEY"):
        return {"ready": False, "reason": "ELEVENLABS_API_KEY is not set where the server runs."}
    if not IDS.exists():
        return {"ready": False, "reason": "The agent is not deployed yet (python -m voice.deploy)."}
    from elevenlabs.client import ElevenLabs

    agent_id = json.loads(IDS.read_text())["agent_id"]
    client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
    return {"ready": True, "signed_url": client.conversational_ai.conversations.get_signed_url(agent_id=agent_id).signed_url}


class Handler(BaseHTTPRequestHandler):
    dsn = ""
    bridge: Bridge

    def log_message(self, *args):
        pass

    def _send(self, status: int, body: bytes, kind: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data, default=str).encode())

    def _local(self) -> bool:
        """True when the request names this machine. A page on another site that points its own
        domain at 127.0.0.1 (DNS rebinding) sends its domain here, and is refused."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host in ("127.0.0.1", "localhost"):
            return True
        self._json(403, {"error": "local requests only"})
        return False

    def do_GET(self):
        if not self._local():
            return
        path = self.path.split("?", 1)[0]
        if path == "/api/customers":
            return self._json(200, customers(self.dsn))
        if m := re.fullmatch(r"/api/customers/(\d+)", path):
            found = customer(self.dsn, int(m.group(1)))
            return self._json(200, found) if found else self._json(404, {"error": "no such customer"})
        if path == "/api/voice/session":
            return self._json(200, voice_session())
        file = (DIST / path.lstrip("/")).resolve()
        if path == "/" or not file.is_file() or DIST.resolve() not in file.parents:
            file = DIST / "index.html"
        if not file.is_file():
            return self._send(503, b"The page is not built: cd crm && npm ci && npm run build", "text/plain")
        self._send(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "application/octet-stream")

    def do_POST(self):
        if not self._local():
            return
        m = re.fullmatch(r"/api/tools/(\w+)", self.path)
        if not m:
            return self._json(404, {"error": "not found"})
        try:
            params = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            return self._send(200, self.bridge.call(m.group(1), params).encode())
        except (ToolError, json.JSONDecodeError) as exc:
            return self._json(400, {"error": str(exc)})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dsn", default=os.environ.get("EDP_DSN", "host=/tmp dbname=warehouse user=odoo"))
    p.add_argument("--port", type=int, default=4320)
    args = p.parse_args()
    with psycopg.connect(args.dsn, autocommit=True) as conn:
        ask.ensure_reader(conn)
    Handler.dsn = args.dsn
    Handler.bridge = Bridge(args.dsn, RUNS / f"crm-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.jsonl")
    print(f"Finance CRM on http://127.0.0.1:{args.port}")
    # Only this machine: the page can run the warehouse tools, so it is never served to the network.
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
