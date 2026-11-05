"""Remit's command line.

    python -m remit run --mode shadow|suggest|act   plan every late customer, queue proposals
    python -m remit queue                            what is waiting for a decision
    python -m remit show <proposal>                  one proposal with its draft and evidence
    python -m remit approve <proposal> --by <name> [--body-file edited.txt]
    python -m remit reject <proposal> --by <name> --note "why"
    python -m remit apply                            write approved proposals to Odoo, once each
    python -m remit report [--run <run>]             counts by status and kind

The warehouse is EDP_DSN; Odoo is ODOO_URL, ODOO_DB, ODOO_USER, ODOO_PASSWORD, as for edp.
"""

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import psycopg

from edp.ask import AS_OF
from edp.odoo import Odoo

from .ledger import Ledger
from .odoo_io import Reader, Writer
from .run import apply, run


def _odoo() -> Odoo:
    return Odoo(os.environ.get("ODOO_URL", "http://localhost:8069"), os.environ.get("ODOO_DB", "acme"),
                os.environ.get("ODOO_USER", "admin"), os.environ.get("ODOO_PASSWORD", "admin"))


def _conn():
    return psycopg.connect(os.environ.get("EDP_DSN", "host=/tmp dbname=warehouse user=odoo"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="remit", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--mode", choices=["shadow", "suggest", "act"], required=True)
    r.add_argument("--as-of", default=AS_OF)
    sub.add_parser("queue")
    s = sub.add_parser("show")
    s.add_argument("proposal")
    a = sub.add_parser("approve")
    a.add_argument("proposal")
    a.add_argument("--by", required=True)
    a.add_argument("--note", default="")
    a.add_argument("--body-file")
    j = sub.add_parser("reject")
    j.add_argument("proposal")
    j.add_argument("--by", required=True)
    j.add_argument("--note", required=True)
    sub.add_parser("apply")
    rep = sub.add_parser("report")
    rep.add_argument("--run")
    args = ap.parse_args(argv)

    with _conn() as conn:
        ledger = Ledger(conn)
        if args.cmd == "run":
            odoo = _odoo()
            out = run(conn, Reader(odoo), dt.date.fromisoformat(args.as_of), args.mode)
        elif args.cmd == "queue":
            out = [{k: p[k] for k in ("proposal_id", "customer_name", "kind", "stage", "reason")}
                   for p in ledger.proposals("pending")]
        elif args.cmd == "show":
            found = [p for p in ledger.proposals() if p["proposal_id"] == args.proposal]
            if not found:
                print(f"no proposal {args.proposal}", file=sys.stderr)
                return 1
            out = found[0]
        elif args.cmd in ("approve", "reject"):
            body = Path(args.body_file).read_text() if getattr(args, "body_file", None) else None
            ledger.decide(args.proposal, args.cmd == "approve", args.by, args.note, body)
            out = {"proposal": args.proposal, "decision": args.cmd}
        elif args.cmd == "apply":
            odoo = _odoo()
            out = apply(conn, Writer(odoo), Reader(odoo))
        else:
            sql = ("select kind, status, count(*) from remit.proposals "
                   + ("where run_id = %s " if args.run else "") + "group by 1, 2 order by 1, 2")
            out = [{"kind": k, "status": s, "count": n}
                   for k, s, n in conn.execute(sql, [args.run] if args.run else []).fetchall()]
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
