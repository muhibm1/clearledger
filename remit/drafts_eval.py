"""How often a model's reminder passes Remit's checks first time, and what it gets wrong.

Builds a fixed set of reminder actions (every stage, one and several invoices,
euros, a customer name with punctuation, large and odd amounts), asks the model
to draft each one, and runs `check_draft` on the reply. Nothing is sent and
nothing reaches Odoo. A draft that fails is what Remit would have replaced with
its template, so the pass rate is how often the model's wording is used at all.

    python -m remit.drafts_eval --model haiku
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from decimal import Decimal
from pathlib import Path

from .draft import SCHEMA, SYSTEM, check_draft, prompt_for
from .playbook import Evidence, Invoice, plan_customer

AS_OF = dt.date(2026, 10, 27)


def _inv(i, days, amount, name="Larkspur Foods", customer=7, currency="USD", local=None):
    amt = Decimal(amount)
    return Invoice(i, f"INV/2026/{i:05d}", customer, name, customer, AS_OF - dt.timedelta(days=days), days,
                   currency, Decimal(local) if local else amt, amt, Decimal(0), Decimal(0), False, False,
                   f"account.move/{i}@v1")


CASES = {
    "courtesy, one invoice": [_inv(1, -2, "4200.00")],
    "friendly, one invoice": [_inv(2, 3, "9600.00")],
    "friendly, three invoices": [_inv(3, 2, "1800.00"), _inv(4, 5, "950.00"), _inv(5, 6, "12000.00")],
    "firm, one invoice": [_inv(6, 18, "19250.00")],
    "firm, two invoices, cents": [_inv(7, 12, "7425.37"), _inv(8, 25, "19410.05")],
    "firm, euros": [_inv(9, 18, "4830.92", name="Juniper Architecture", currency="EUR", local="6200.00")],
    "final, one invoice": [_inv(10, 45, "40610.00", name="Maple Street Bakery")],
    "final, mixed stages": [_inv(11, 40, "11380.00"), _inv(12, 9, "2500.00"), _inv(13, 2, "640.00")],
    "name with punctuation": [_inv(14, 10, "5460.00", name="O'Neil & Sons, Inc.")],
    "small amount": [_inv(15, 4, "12.50")],
    "large amount": [_inv(16, 20, "1250000.00", name="Granite State Credit Union")],
    "final, euros, two invoices": [_inv(17, 50, "1000.00", currency="EUR", local="920.00",
                                        name="Juniper Architecture"),
                                   _inv(18, 33, "2000.00", currency="EUR", local="1840.00",
                                        name="Juniper Architecture")],
}


def main(argv=None) -> int:
    from analyst.model import CommandLineModel, ModelError
    ap = argparse.ArgumentParser(prog="python -m remit.drafts_eval")
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    model = CommandLineModel(model=args.model, usage_log="remit/results/usage.jsonl")
    rows = []
    for name, invoices in CASES.items():
        action = plan_customer(invoices, Evidence(), AS_OF, set()).actions[0]
        for rep in range(args.repeats):
            t = time.time()
            try:
                draft = model(SYSTEM, prompt_for(action), SCHEMA)
                problems = check_draft(draft, action)
            except ModelError as exc:
                draft, problems = {}, [f"model error: {exc}"]
            rows.append({"case": name, "repeat": rep, "passed": not problems, "problems": problems,
                         "seconds": round(time.time() - t, 1), "draft": draft})
            print(f"{'PASS' if not problems else 'FAIL'}  {name} #{rep + 1}  {problems[:2]}", flush=True)
    passed = sum(r["passed"] for r in rows)
    out = {"model": args.model, "as_of": AS_OF.isoformat(), "cases": len(CASES), "repeats": args.repeats,
           "passed": passed, "drafts": len(rows), "rows": rows}
    path = Path(args.out or f"remit/results/drafts-{args.model}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"{passed} of {len(rows)} drafts passed first time -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
