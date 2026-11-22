"""Two comparison runs for a blind company: the same model the review step uses, with none of the analyst.

Both arms get the question, an honest instruction to name what is wrong behind a figure and to decline
what the records cannot answer, and a loop of read-only lookups. Neither gets the onboarding pass, the
review step, the retrieval notes or the domain file.

- `warehouse`: the model writes SQL against the warehouse this platform built (marts, intermediate,
  staging), through the same read-only executor the analyst uses. It sees table and column names only.
  This arm measures what the analyst adds on top of the warehouse.
- `odoo`: the model reads Odoo itself through `search_read` and `read_group`, limited to the models and
  fields the platform loads (`clearledger/sources.py`), so it sees no more than the platform does.
  This arm measures what the whole platform adds on top of the model.

The output has the analyst run's shape (`status`, `answer`, `explanation` per record), so the same
grader scores all three.

  python -m analyst.eval.plain warehouse|odoo <questions.jsonl> <out.json> [--model opus] [--limit N]
"""

import argparse
import concurrent.futures as cf
import json
import os
from pathlib import Path

from analyst.model import CommandLineModel, ModelError
from clearledger.sources import DOMAINS, SOURCES

AS_OF = "2026-10-27"
MAX_LOOKUPS = 10
ROWS_SHOWN = 80
CELL_CHARS = 6000

RULES = f"""You are a careful finance analyst answering one question about a company's books. Today is {AS_OF}.
Report money in US dollars unless the question asks otherwise.
Work in steps. In each step either run one read-only lookup or give the final answer. You may run at most
{MAX_LOOKUPS} lookups.
A good answer does three things:
1. It gives the figure the books show, computed from the records, not estimated.
2. It checks the records behind the figure, and if something in them makes the figure misleading or wrong
   (an error, a duplicate, a contradiction, an odd reference or note, anything a careful analyst would
   flag), it says so plainly and names the records.
3. If the records you can reach do not hold what the question needs, it says the question cannot be
   answered from these records and gives no made-up figure.
Final answers: status "answer" with the answer, or status "not_answerable" when the records cannot answer.
Keep the explanation under 700 characters and put the caveats first in it."""

STEP = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["lookup", "final"]},
        "lookup": {"type": "string", "description": "The lookup to run, when action is lookup."},
        "status": {"type": "string", "enum": ["answer", "not_answerable"]},
        "answer": {"type": "string", "description": "The final answer, when action is final."},
        "explanation": {"type": "string"},
    },
    "required": ["action"],
}


def _clip(value) -> str:
    text = json.dumps(value, default=str)
    return text if len(text) <= CELL_CHARS else text[:CELL_CHARS] + " ...(cut)"


class Warehouse:
    """Read-only SQL over the built warehouse, table and column names only."""

    def __init__(self):
        import psycopg

        from clearledger import ask

        self.ask = ask
        self.conn = psycopg.connect(os.environ["CLEARLEDGER_DSN"], autocommit=True)
        ask.ensure_reader(self.conn)
        rows = self.conn.execute(
            "select table_schema, table_name, string_agg(column_name || ' ' || data_type, ', ' order by ordinal_position)"
            " from information_schema.columns where table_schema in ('marts','intermediate','staging')"
            " group by 1, 2 order by 1, 2").fetchall()
        self.about = ("Each lookup is one PostgreSQL SELECT statement. Tables:\n"
                      + "\n".join(f"{s}.{t}({cols})" for s, t, cols in rows))

    def run(self, lookup: str):
        return self.ask.query(self.conn, lookup)


class OdooBooks:
    """Read-only calls on Odoo, limited to the models and fields the platform loads."""

    def __init__(self):
        from clearledger.odoo import Odoo

        self.odoo = Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"], os.environ.get("ODOO_USER", "admin"),
                         os.environ.get("ODOO_PASSWORD", "admin"))
        listing = "\n".join(f"{m}: id, {', '.join(f)}" for m, f in SOURCES.items())
        self.about = (
            "Each lookup is one JSON object calling Odoo 17, one of:\n"
            '{"method": "search_read", "model": "<model>", "domain": [...], "fields": [...], "limit": 80, "order": "id"}\n'
            '{"method": "read_group", "model": "<model>", "domain": [...], "fields": ["amount_total_signed:sum"], "groupby": ["partner_id"]}\n'
            '{"method": "search_count", "model": "<model>", "domain": [...]}\n'
            "Only these models and fields exist for you:\n" + listing
            + "\nOn account.move only customer invoices and credit notes (move_type out_invoice, out_refund) are in scope;"
              " on account.payment only customer payments.")

    def run(self, lookup: str):
        try:
            call = json.loads(lookup)
        except json.JSONDecodeError as exc:
            return {"error": f"the lookup is not JSON: {exc}"}
        model, method = call.get("model"), call.get("method")
        if model not in SOURCES or method not in ("search_read", "read_group", "search_count"):
            return {"error": "only search_read, read_group and search_count on the listed models are allowed"}
        allowed = set(SOURCES[model]) | {"id"}
        domain = list(call.get("domain") or [])
        used = {str(t[0]).split(".")[0].split(":")[0] for t in domain if isinstance(t, (list, tuple))}
        used |= {str(f).split(":")[0] for f in call.get("fields") or []}
        used |= {str(g).split(":")[0] for g in call.get("groupby") or []}
        if used - allowed:
            return {"error": f"fields not loaded: {sorted(used - allowed)}"}
        domain = domain + [list(t) for t in DOMAINS.get(model, [])]
        try:
            if method == "search_count":
                return {"count": self.odoo.execute(model, "search_count", domain)}
            if method == "read_group":
                rows = self.odoo.execute(model, "read_group", domain, call.get("fields") or [],
                                         call.get("groupby") or [], lazy=False)
                return {"rows": rows[:ROWS_SHOWN], "total_rows": len(rows)}
            rows = self.odoo.execute(model, "search_read", domain, call.get("fields") or sorted(allowed),
                                     limit=min(int(call.get("limit") or ROWS_SHOWN), 200),
                                     order=call.get("order") or "id")
            return {"rows": rows, "total_rows": self.odoo.execute(model, "search_count", domain)}
        except Exception as exc:  # a bad domain or field: the model sees the error and can retry
            return {"error": str(exc)[:400]}


def answer(make_books, model, question: str) -> dict:
    """One question: lookups until a final answer or the limit."""
    books = make_books()
    system = RULES + "\n\n" + books.about
    log, steps = [], []
    for used in range(MAX_LOOKUPS + 1):
        left = MAX_LOOKUPS - used
        prompt = (f"Question: {question}\n\n" + "".join(log)
                  + (f"\nLookups left: {left}." if left else "\nNo lookups left: give the final answer now."))
        try:
            out = model(system, prompt, STEP)
        except ModelError as exc:
            return {"status": "failed", "answer": None, "explanation": str(exc), "steps": steps}
        if out.get("action") == "final" or not left:
            return {"status": out.get("status") or "answer", "answer": out.get("answer"),
                    "explanation": out.get("explanation") or "", "steps": steps}
        lookup = out.get("lookup") or ""
        result = books.run(lookup)
        steps.append({"lookup": lookup, "error": result.get("error") if isinstance(result, dict) else None})
        log.append(f"Lookup {used + 1}: {lookup}\nResult: {_clip(result)}\n")
    return {"status": "failed", "answer": None, "explanation": "no final answer", "steps": steps}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("arm", choices=["warehouse", "odoo"])
    p.add_argument("questions")
    p.add_argument("out")
    p.add_argument("--model", default="opus")
    p.add_argument("--limit", type=int)
    a = p.parse_args()
    out = Path(a.out)
    rows = [json.loads(line) for line in open(a.questions)][: a.limit]
    model = CommandLineModel(a.model, usage_log=out.with_suffix(".usage.jsonl"))
    make_books = Warehouse if a.arm == "warehouse" else OdooBooks

    def one(row: dict) -> dict:
        return {"id": row["id"], "question": row["question"], **answer(make_books, model, row["question"])}

    with cf.ThreadPoolExecutor(4) as pool:
        records = list(pool.map(one, rows))
    out.write_text(json.dumps({"model": a.model, "condition": f"plain-{a.arm}", "records": records},
                              indent=1, default=str))
    print("wrote", out, len(records))


if __name__ == "__main__":
    main()
