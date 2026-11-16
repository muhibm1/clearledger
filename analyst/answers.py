"""Rules about the shape of an answer that code can check, so the model need not remember them.

- A ranking question ("which customer has the most...") must not be answered with a query that cuts
  at LIMIT: rows tied at the cut-off vanish and the answer names one winner of several.
- A question that asks for an identifier ("and on which invoice?", "who was it to?") is not answered
  by the amount alone: the identifier in the rows must appear in the answer. Which questions ask for
  which identifier is the domain's knowledge (its `identifiers`), not this module's.
"""

import re

SUPERLATIVE = re.compile(
    r"\b(most|least|fewest|largest|smallest|biggest|highest|lowest|greatest|top|bottom|oldest|newest|"
    r"latest|earliest|longest|shortest|best|worst)\b", re.IGNORECASE)
ASKS_FOR_ONE = re.compile(r"\b(which|who|whom|whose|what|name)\b", re.IGNORECASE)
TIE_SAFE = re.compile(r"(?i)\b(rank|dense_rank|with\s+ties)\b|\bmax\s*\(|\bmin\s*\(")
LIMIT = re.compile(r"(?i)\blimit\s+(\d+)\b|\bfetch\s+first\s+(\d+)\b")


def ranking_advice(question: str, sql: str) -> str | None:
    """Why a query that cuts a ranking at LIMIT is not run yet, or None when it is fine."""
    if not (SUPERLATIVE.search(question) and ASKS_FOR_ONE.search(question)):
        return None
    cut = LIMIT.search(sql)
    if not cut or TIE_SAFE.search(sql):
        return None
    return ("This is a ranking question and LIMIT can hide ties: if two rows share the top value, one of "
            "them is silently dropped. Rewrite it so every row tied at the cut-off is returned (for example "
            "rank() over (order by the measure desc) <= N, or the measure = the maximum), and select the "
            "measure so the answer can state a tie.")


def missing_identifiers(identifiers: dict, question: str, rows: list[dict], answer) -> dict:
    """Identifier columns the question asks for whose values are in the rows but not in the answer.

    `identifiers` maps a column name to regular expressions that mark a question as asking for it
    ({"invoice_ref": ["\\b(which|what)\\b[^?.]*\\binvoice\\b"]}). Only small results are checked: a
    long list is its own answer."""
    if not identifiers or not rows or len(rows) > 5:
        return {}
    shown = str(answer).lower()
    out = {}
    for col, patterns in identifiers.items():
        if col not in rows[0] or not any(re.search(p, question, re.IGNORECASE) for p in patterns):
            continue
        values = [str(r[col]) for r in rows if r.get(col) not in (None, "")]
        lacking = [v for v in dict.fromkeys(values) if v.lower() not in shown]
        if lacking:
            out[col] = lacking
    return out


def with_identifiers(answer, missing: dict):
    """The answer with the missing identifiers added, as a list of strings."""
    parts = [] if answer is None else (list(answer) if isinstance(answer, list) else [answer])
    return [str(p) for p in parts] + [v for vals in missing.values() for v in vals]
