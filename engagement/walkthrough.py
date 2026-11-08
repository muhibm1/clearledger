"""One finance question end to end, in four steps, each labelled for what actually ran.

    python -m engagement walkthrough
    python -m engagement walkthrough --json out.json
    python -m engagement walkthrough --via cli      # analyst through the local claude CLI (untested here)

LIVE      ran now against the real thing.
STUB      ran now, with a scripted stand-in for the language model.
RECORDED  read from a file an earlier real run wrote; the output says which file.
SKIPPED   not run, with the reason.

Exit code: 1 if a LIVE step or a STUB step fails (a STUB step runs real SQL, so its failure is real);
RECORDED and SKIPPED steps never fail the run. Configuration is by environment variables only: CLEARLEDGER_DSN,
CLEARLEDGER_ANALYST_PYTHON, AGENT_HARBOR_URL, AGENT_HARBOR_API_TOKEN.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .demo import BANNER, ROOT, changelens_guard

DSN = os.environ.get("CLEARLEDGER_DSN", "host=/tmp dbname=warehouse user=odoo")
QUESTION_TEMPLATE = "How much does {name} owe us that is more than 90 days late?"
FALLBACK_QUESTION = "How much does each customer owe us that is more than 90 days late?"
TRACE_RECORDING = ROOT / "docs/examples/trace-northwind.txt"
HARBOR_RECORDING = ROOT / "engagement/recorded/agent-harbor-cap.json"


def step(n, name, mode, reason, ok=True, **result):
    return {"step": n, "name": name, "mode": mode, "reason": reason, "ok": ok, "result": result}


# Connects to the warehouse within 3 s; returns (connection, None) or (None, why not).
def connect():
    try:
        import psycopg
    except ImportError:
        return None, "psycopg is not installed"
    try:
        return psycopg.connect(os.environ.get("CLEARLEDGER_DSN", DSN), connect_timeout=3), None
    except psycopg.Error as exc:
        return None, f"the warehouse at CLEARLEDGER_DSN did not connect within 3 s ({type(exc).__name__})"


# Step 1: trace a receivable with a balance more than 90 days late.
def trace_step(conn, why_not):
    if conn is None:
        text = TRACE_RECORDING.read_text()
        return step(1, "Trace one receivable", "RECORDED",
                    f"{why_not}. Showing docs/examples/trace-northwind.txt, a revenue_by_customer trace "
                    "recorded earlier (not a receivable).", text=text), None
    try:
        from clearledger.trace import trace
        # A name that no other customer's name contains, because clearledger trace looks a customer up by substring.
        row = conn.execute("select a.customer_name, a.late_over_90 from marts.receivables_aging a "
                           "where a.late_over_90 > 0 and (select count(*) from marts.receivables_aging b "
                           "where b.customer_name ilike '%' || a.customer_name || '%') = 1 "
                           "order by a.late_over_90 desc, a.customer_key limit 1").fetchone()
        if row is None:
            return step(1, "Trace one receivable", "LIVE", "no customer has a balance over 90 days late",
                        ok=False), None
        text = trace(conn, "receivables_aging", row[0])
    except (Exception, SystemExit) as exc:  # clearledger.trace raises SystemExit on an ambiguous name
        return step(1, "Trace one receivable", "LIVE", f"failed: {type(exc).__name__}: {exc}", ok=False), None
    ok = "DOES NOT ADD UP" not in text
    return step(1, "Trace one receivable", "LIVE",
                f"clearledger trace on the warehouse: {row[0]}, receivables_aging, over 90 days late {row[1]:,.2f} USD",
                ok=ok, customer=row[0], late_over_90=float(row[1]), text=text), row[0]


# Step 2: the analyst, in its own environment, as a subprocess.
def analyst_step(has_warehouse, why_not, via):
    name = "Analyst answers with citations"
    python = Path(os.environ.get("CLEARLEDGER_ANALYST_PYTHON", ROOT / ".venv-analyst/bin/python"))
    if not python.exists():
        return step(2, name, "SKIPPED", f"the analyst's environment is missing ({python}); see the CI step "
                    "'Analyst agent tests' for how to build .venv-analyst")
    if not has_warehouse:
        return step(2, name, "SKIPPED", f"{why_not}; the analyst needs it to run its SQL")
    cmd = [str(python), "-m", "engagement.analyst_step", "--json", "--via", via]
    r = None
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
        out = json.loads(r.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        err = getattr(exc, "stderr", None) or (r.stderr if r else "") or ""
        if isinstance(err, bytes):
            err = err.decode(errors="replace")
        tail = " | ".join(err.strip().splitlines()[-5:])
        return step(2, name, "STUB", f"the analyst subprocess failed: {type(exc).__name__}"
                    + (f": {tail}" if tail else ""), ok=False)
    if out.get("mode") == "SKIPPED":
        return step(2, name, "SKIPPED", out["reason"])
    mode = out["mode"]
    reason = out["model"] + "; SQL run on the live warehouse; " + (
        f"{out['citations']['checked']} citations checked, all ok" if out["citations"]["all_ok"]
        else "citations did NOT all check out")
    if not out["ok"]:
        reason += "; " + "; ".join(out["problems"])
    return step(2, name, mode, reason, ok=out["ok"], **{k: v for k, v in out.items()
                                                        if k not in ("mode", "ok", "model")})


# Step 3: the change guard, no database.
def guard_step():
    name = "Change guard on dropping late_over_90"
    ok, what = changelens_guard()
    if ok is None:
        return step(3, name, "SKIPPED", what.removeprefix("skipped: "))
    return step(3, name, "LIVE", "ChangeLens preflight on the dbt project, no database", ok=ok, summary=what)


# Step 4: Agent Harbor, the daily cost cap.
def harbor_step(question):
    name = "Agent Harbor stops further runs once the daily cap is spent (recorded drill)"
    url = os.environ.get("AGENT_HARBOR_URL", "").rstrip("/")
    if not url:
        rec = json.loads(HARBOR_RECORDING.read_text())
        s = rec["source"]
        return step(4, name, "RECORDED",
                    f"AGENT_HARBOR_URL is not set. Cap-refusal drill snapshot from {s['repo']} commit {s['commit']}, "
                    f"{s['path']}: the {rec['agent']} agent on a {rec['model']} model, not the analyst. "
                    "The stop happens after the cap is spent, at a step boundary: the recorded drill spent more than "
                    "its cap before refusing",
                    **{k: rec[k] for k in ("source", "agent", "model", "cap_usd", "observed", "refusal")})
    headers = {"content-type": "application/json"}
    if os.environ.get("AGENT_HARBOR_API_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["AGENT_HARBOR_API_TOKEN"]
    req = urllib.request.Request(url + "/runs", method="POST", headers=headers,
                                 data=json.dumps({"payload": {"question": question, "domain": "finance"}}).encode())
    try:
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status, body = resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            status, body = err.code, json.loads(err.read() or b"{}")
    except (OSError, ValueError) as exc:
        return step(4, name, "LIVE", f"POST {url}/runs failed: {type(exc).__name__}: {exc}", ok=False)
    if status == 429:
        return step(4, name, "LIVE", f"POST {url}/runs refused with 429: {body.get('reason')}: {body.get('detail')}",
                    http_status=429, refusal={"reason": body.get("reason"), "detail": body.get("detail")})
    ok = status in (200, 201) and "request" in body
    return step(4, name, "LIVE", f"POST {url}/runs answered {status}", ok=ok, http_status=status,
                request=body.get("request"), state=body.get("state"))


def show(s, say):
    say(f"\n{s['step']}. {s['name']}   [{s['mode']}]")
    say("-" * 80)
    say(f"  {s['mode']}: {s['reason']}")
    r = s["result"]
    if "text" in r:
        say("\n" + "\n".join("  " + line for line in r["text"].splitlines()))
    if s["step"] == 2 and "sql" in r:
        say(f"  question : {r['question']}")
        say(f"  sql      : {r['sql']}")
        say(f"  answer   : {r['answer']}  ({r['explanation']})")
        say(f"  checked  : {r['invoices']} invoices add to {r['invoices_total']:,.2f}; receivables_aging says "
            f"{r['receivables_aging_late_over_90']:,.2f}")
        say(f"  sources  : {', '.join(r['sources'] or [])}")
    if s["step"] == 3 and "summary" in r:
        say(f"  {r['summary']}")
    if s["step"] == 4:
        for line in r.get("observed", []):
            say(f"  {line}")
        if "request" in r:
            q = r["request"]
            say(f"  request: cost ${q.get('usd')} simulated={q.get('simulated')} latency {q.get('latency_ms')} ms "
                f"state {q.get('state')}")
        if r.get("refusal") and "observed" not in r:
            say(f"  refusal: {r['refusal']['reason']}: {r['refusal']['detail']}")
    if not s["ok"]:
        say("  FAILED")


# Runs the four steps, prints them, returns the result.
def run(via="stub", say=print) -> dict:
    say(BANNER)
    say("\nOne question, end to end: who owes us money that is more than 90 days late, and can we show why.")
    conn, why_not = connect()
    steps = []
    try:
        s1, customer = trace_step(conn, why_not)
    finally:
        if conn is not None:
            conn.close()
    steps.append(s1)
    question = QUESTION_TEMPLATE.format(name=customer) if customer else FALLBACK_QUESTION
    steps.append(analyst_step(conn is not None, why_not, via))
    steps.append(guard_step())
    steps.append(harbor_step(question))
    for s in steps:
        show(s, say)
    failed = [s["step"] for s in steps if not s["ok"]]
    say("\nSummary: " + ", ".join(f"{s['step']} {s['mode']}" for s in steps)
        + (f". FAILED: step {', '.join(map(str, failed))}." if failed else "."))
    return {"question": question, "steps": steps, "failed": failed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engagement walkthrough", description=__doc__.splitlines()[0])
    ap.add_argument("--json", help="also write each step's mode and result to this file")
    ap.epilog = "Exit 1 if a LIVE or a STUB step fails; RECORDED and SKIPPED never fail the run."
    ap.add_argument("--via", choices=("stub", "cli"), default="stub",
                    help="stub: scripted model (default); cli: the analyst calls the local claude command line")
    args = ap.parse_args(argv)
    result = run(args.via)
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2, default=str) + "\n")
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
