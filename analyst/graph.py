"""The analyst agent: a LangGraph flow from a plain-English question to a checked answer.

    retrieve ─▶ draft ─▶ execute (check, then run) ─▶ answer ─▶ cite ─▶ remember ─▶ END
                  ▲            │                         │
                  ├── repair ──┘   (at most MAX_TRIES)    │
                  ├── lookup ──┘   (at most MAX_LOOKUPS)  │
                  └── redraft ───────────────────────────┘  (at most MAX_REDRAFTS)
                  │
                  └─▶ remember when the model decides the data cannot answer it

- retrieve: picks the tables, business terms and examples the question needs
  (`retrieve.py`). With `context=False` the model sees only bare table and
  column names, which is the ablation in the evaluation.
- draft: the model writes one query, or says the question cannot be answered
  from this data, or asks a clarifying question. It may first run a lookup: a
  small query to find how the data spells something the question names ("the
  Mint keyword" is `mint budget`). Lookup results come back to draft. It sees earlier turns of the
  conversation, and any failed attempt with its error.
- execute: code, not the model (`sql.py`). A query that fails either
  goes back to draft with the reason, up to MAX_TRIES times.
- answer: the model reads the rows and gives the answer as a value plus one
  sentence. If it judges the query was the wrong one, its reason goes back to
  draft for another try (at most MAX_REDRAFTS); otherwise it may decline.
- cite: code collects source references from the rows and checks they are real
  and current (finance domain, through `clearledger.ask.check_citations`).

The conversation is saved per thread by the checkpointer, so "and what about
last month?" works as a follow-up.
"""

import json
import operator
import re
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from . import answers
from . import review as reviewing
from . import sql as sqltool
from .domain import Domain, bare_schema
from .retrieve import Retriever

MAX_TRIES = 3
MAX_LOOKUPS = 2
MAX_REDRAFTS = 1
ROWS_SHOWN = 60
LOOKUP_ROWS_SHOWN = 30

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["query", "not_answerable", "clarify"]},
        "sql": {"type": "string", "description": "One PostgreSQL SELECT when decision is query, else empty."},
        "reason": {"type": "string", "description": "One sentence: why this query, or why not."},
    },
    "required": ["decision", "sql", "reason"],
}
LOOKUP_DRAFT_SCHEMA = {
    **DRAFT_SCHEMA,
    "properties": {
        **DRAFT_SCHEMA["properties"],
        "decision": {"type": "string", "enum": ["lookup", "query", "not_answerable", "clarify"]},
        "sql": {"type": "string", "description": "One PostgreSQL SELECT when decision is lookup or query, "
                                                 "else empty."},
    },
}
LOOKUP_RULE = (
    "Names in a question (a keyword, campaign, experiment, app or customer) often do not match how the "
    "data spells them. If you have not seen the exact value in the notes or in a lookup, first run a "
    "lookup: a small SELECT DISTINCT over the column that holds it, filtered loosely (lower(...) like "
    "'%word%'). Then write the final query with the exact values the lookup found. If nothing in the "
    "data matches what the question names, the question is not answerable."
)
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["answer", "not_answerable"]},
        "answer": {"description": "A number, a string, a list of strings, or null when not answerable.",
                   "anyOf": [{"type": "number"}, {"type": "string"},
                             {"type": "array", "items": {"type": "string"}}, {"type": "null"}]},
        "explanation": {"type": "string", "description": "One plain sentence for a business reader."},
    },
    "required": ["status", "answer", "explanation"],
}


class State(TypedDict, total=False):
    question: str
    history: Annotated[list, operator.add]   # earlier turns in this thread, saved by the checkpointer
    context: str
    retrieved: list
    attempts: list                           # every query tried this turn, with its error if any
    lookups: list                            # lookup queries this turn, with the rows they found
    redrafts: int                            # times the reading step sent the question back
    sql: str
    rows: list
    status: str                              # answer | not_answerable | clarify | failed
    answer: Any
    explanation: str
    sources: list
    citations: dict
    caveats: list                            # what the review step found that bears on the answer
    review_error: str
    review_checks: list                      # the reviewer's own read-only checks of the books
    hinted: bool                             # the draft was told once that a name it declined on exists


def existing_entity(dom: Domain, sql: str) -> str:
    """The first name the query filters on that the domain's entity columns hold, or ''. Code looks it up,
    so a name the model never looked up still counts when the books hold it."""
    if not dom.entities or not sql:
        return ""
    names = {m.group(2).strip() for m in reviewing.NAME_FILTER.finditer(sql)}
    names = sorted((n for n in names if len(n) >= 3), key=len, reverse=True)  # the most specific name first
    if not names:
        return ""
    try:
        with dom.connect() as conn, conn.cursor() as cur:
            for name in names:
                for col in dom.entities:
                    schema, table, column = col.split(".")
                    cur.execute(f"select {column} from {schema}.{table} where {column} ilike %s limit 1",
                                (f"%{name}%",))
                    got = cur.fetchone()
                    if got and got[0]:
                        return str(got[0])
    except Exception:  # a lookup that fails proves nothing either way
        return ""
    return ""


GENERIC_WORDS = {"answerable", "between", "happened", "nearest", "written", "falling", "period", "reason",
                 "covers", "months", "record", "figure", "question", "warehouse", "loaded"}


def _names_missing_data(dom: Domain, reason: str) -> bool:
    """Whether a decline's reason points at something the warehouse does not hold (then it stands): it
    uses a word that only the not-loaded list uses, never the tables' own notes."""
    def stems(text):
        return {w.rstrip("s") for w in re.findall(r"[a-z]{6,}", text.lower())} - GENERIC_WORDS

    held = stems(json.dumps([dom.tables, dom.terms], default=str))
    words = {w for item in dom.not_loaded for w in stems(item)} - held
    return bool(words & stems(reason or ""))


def _system(dom: Domain) -> str:
    return (
        f"You are a data analyst answering questions about {dom.title}. You work only from the "
        f"PostgreSQL warehouse described below, never from memory. Always name tables with their schema "
        f"({', '.join(dom.schemas)}). Write the simplest query that answers exactly what was asked. "
        + (f"Dates are as of {dom.as_of}. " if dom.as_of else "")
        + "If the warehouse has no data that could answer the question, say it is not answerable "
          "rather than guessing. Ask to clarify only when two readings would give different numbers "
          "and nothing in the question or the notes settles it."
        + (" The warehouse does not hold: " + "; ".join(dom.not_loaded) + ". A question that needs any of "
           "these is not answerable: say what is missing, and give the nearest figure the warehouse does "
           "hold with its limit. Never answer 0 or 'none' because the data is silent." if dom.not_loaded else "")
        + (" HOW TO ANSWER IN THIS WAREHOUSE: " + " ".join(dom.rules) if dom.rules else "")
        + " For a question that ranks things (the most, the largest, the top N), return every row tied at "
          "the cut-off with the measure, never LIMIT alone, and say in the answer when there is a tie."
    )


def build(dom: Domain, model, *, context: bool = True, retriever: Retriever | None = None, checkpointer=None,
          max_tries: int = MAX_TRIES, max_lookups: int = MAX_LOOKUPS, max_redrafts: int = MAX_REDRAFTS,
          with_review: bool | None = None, review_model=None):
    """Compile the graph for one domain. `model(system, prompt, schema) -> dict`.

    `max_lookups=0, max_redrafts=0` is the agent as first scored: one query, repaired on error."""
    from .domain import documents

    # The review reads the domain's issues table, which is domain knowledge: the no-context ablation
    # goes without it unless asked.
    with_review = context if with_review is None else with_review
    if review_model is None and dom.review_model:
        from .model import CommandLineModel
        if isinstance(model, CommandLineModel):  # the domain's stronger reviewer, on the same route
            review_model = CommandLineModel(dom.review_model, usage_log=getattr(model, "usage_log", None))
    if context and retriever is None:
        retriever = Retriever(documents(dom))
    system = _system(dom) + (" " + LOOKUP_RULE if max_lookups else "")
    fresh = {"attempts": [], "lookups": [], "redrafts": 0, "sources": [], "citations": {}, "caveats": [],
             "review_error": "", "hinted": False}

    def retrieve(state: State) -> dict:
        if not context:
            return {"context": "TABLES (name and columns only):\n" + bare_schema(dom), "retrieved": [], **fresh}
        picked = retriever.select(state["question"])
        text = "\n\n".join(d["text"] for d in picked)
        others = [t for t in dom.columns if t not in {d["key"] for d in picked if d["kind"] == "table"}]
        if others:
            text += "\n\nOTHER TABLES (ask for nothing else; these exist if needed): " + ", ".join(others)
        return {"context": text, "retrieved": [d["key"] for d in picked], **fresh}

    def draft(state: State) -> dict:
        parts = [f"WAREHOUSE NOTES\n{state['context']}"]
        if state.get("history"):
            turns = "\n".join(f"- Q: {h['question']}\n  SQL: {h.get('sql') or '(none)'}\n  A: {h.get('answer')}"
                              for h in state["history"][-4:])
            parts.append(f"EARLIER IN THIS CONVERSATION\n{turns}")
        if state.get("lookups"):
            found = "\n".join(f"- {x['sql']}\n  FOUND: {x['found']}" for x in state["lookups"])
            parts.append(f"YOUR LOOKUPS SO FAR\n{found}")
        failed = [a for a in state.get("attempts", []) if a["error"]]
        if failed:
            tried = "\n".join(f"- {a['sql']}\n  FAILED: {a['error']}" for a in failed)
            parts.append(f"YOUR EARLIER ATTEMPTS AT THIS QUESTION\n{tried}\nFix the problem and try again.")
        left = max_lookups - len(state.get("lookups", []))
        if max_lookups:
            parts.append(f"You may run {left} more lookup(s) before the final query." if left > 0
                         else "No lookups are left: write the final query, or say it is not answerable.")
        parts.append(f"QUESTION\n{state['question']}")
        out = model(system, "\n\n".join(parts), LOOKUP_DRAFT_SCHEMA if left > 0 else DRAFT_SCHEMA)
        if out.get("decision") == "lookup" and left > 0:
            return {"sql": out.get("sql", ""), "status": "lookup"}
        if (out.get("decision") == "not_answerable" and not state.get("hinted")
                and not _names_missing_data(dom, out.get("reason", ""))):
            # A decline after looking a name up: if the books do hold that name, say so once and let the
            # draft write the query, since a party with nothing of this kind is a true zero.
            tried = " ".join([x["sql"] for x in state.get("lookups", [])] + [a["sql"] for a in state.get("attempts", [])])
            found = existing_entity(dom, tried)
            if found:
                note = (f"NOT RUN. Before declining: the books do hold '{found}', a name your queries looked for. "
                        "Query it by that exact name; if it has nothing of the kind asked, the answer is 0 (or "
                        "None, or No). Decline only if the question needs something the warehouse does not hold.")
                return {"status": "hint", "hinted": True,
                        "attempts": state.get("attempts", []) + [{"sql": out.get("sql") or "(declined)", "error": note}]}
        if out.get("decision") not in ("query", "lookup"):
            status = "clarify" if out.get("decision") == "clarify" else "not_answerable"
            return {"status": status, "sql": "", "answer": None, "explanation": out.get("reason", "")}
        return {"sql": out.get("sql", ""), "status": "running"}

    def execute(state: State) -> dict:
        advice = None if state["status"] == "lookup" else answers.ranking_advice(state["question"], state["sql"])
        if advice and not any(a.get("advice") for a in state.get("attempts", [])):
            # Said once per question: a model that insists on LIMIT after being told is taken at its word.
            return {"attempts": state.get("attempts", []) + [{"sql": state["sql"], "error": "NOT RUN. " + advice,
                                                              "advice": True}],
                    "rows": [], "status": "repair"}
        with dom.connect() as conn:
            sqltool.prepare(dom, conn)
            result = sqltool.run(dom, conn, state["sql"])
        if state["status"] == "lookup":
            found = (json.dumps(result["rows"][:LOOKUP_ROWS_SHOWN], default=str) or "[]") if result["ok"] \
                else f"ERROR: {result['error']}"
            return {"lookups": state.get("lookups", []) + [{"sql": state["sql"], "found": found}],
                    "status": "looked"}
        if not result["ok"]:
            attempts = state.get("attempts", []) + [{"sql": state["sql"], "error": result["error"]}]
            failed = len([a for a in attempts if not a.get("advice")]) >= max_tries
            return {"attempts": attempts, "rows": [], "status": "failed" if failed else "repair",
                    "explanation": result["error"] if failed else ""}
        return {"rows": result["rows"], "status": "ran",
                "attempts": state.get("attempts", []) + [{"sql": state["sql"], "error": None}]}

    def answer(state: State) -> dict:
        rows = state["rows"]
        shown = json.dumps(rows[:ROWS_SHOWN], default=str)
        more = f"\n({len(rows) - ROWS_SHOWN} more rows not shown)" if len(rows) > ROWS_SHOWN else ""
        nothing = not rows or (len(rows) == 1 and all(v is None for v in rows[0].values()))
        looked = "".join(f"\n- {x['sql']}\n  FOUND: {x['found'][:600]}" for x in state.get("lookups", []))
        prompt = (f"QUESTION\n{state['question']}\n\n"
                  + (f"YOUR LOOKUPS (what they found exists in the data){looked}\n\n" if looked else "")
                  + f"QUERY YOU RAN\n{state['sql']}\n\n"
                  f"RESULT ({len(rows)} rows)\n{shown}{more}\n\n"
                  "Give the answer. A single amount or count is a number (dollars as a plain number, "
                  "no symbols); a list is a list of strings. If the question asks for more than one thing "
                  "(an amount and which invoice, a customer and a figure; what was ordered, invoiced and unpaid), "
                  "the answer is one string that states every part with its figure, naming the order, invoice, "
                  "customer or deal from the rows; an amount on a record in another currency is given in that "
                  "currency as well as in USD. Nothing asked for may sit only in the explanation. If several rows share the top value, say they "
                  "are tied and name them all. Decline only when the question asks for something this data "
                  "does not hold, or the query was the wrong one; when you decline, say in one sentence what "
                  "is missing and give the nearest figure the warehouse does hold with its limit.")
        found = existing_entity(dom, state["sql"]) if nothing else ""
        if nothing and found:
            prompt += (f" The query found nothing, and the books do hold '{found}', the name it filters on. "
                       "So the empty result is a true zero: answer 0 (or [] for a list, No for a yes or no "
                       "question) and say that it exists but has nothing of this kind. Decline only if the "
                       "question needs something the warehouse does not hold.")
        elif nothing:
            prompt += (" The query found nothing (no rows, or only NULL). That is a true 0 (or []) only when "
                       "what the question names exists and simply has nothing of this kind. If it names a "
                       "customer, deal or invoice that the notes or a lookup have not shown to exist, you "
                       "cannot tell: decline, and say the name could not be found. A name a lookup above found "
                       "does exist. For a yes or no question (is it late, are there any, did it), a correct query "
                       "that finds nothing answers No.")
        out = model(system, prompt, ANSWER_SCHEMA)
        status = out.get("status") if out.get("status") in ("answer", "not_answerable") else "answer"
        if status == "not_answerable" and found and not _names_missing_data(dom, out.get("explanation", "")):
            # The entity exists and the measure is empty: a true zero, not a decline.
            return {"status": "answer", "answer": 0,
                    "explanation": f"The books hold {found}, but it has nothing of this kind, so the figure is 0."}
        if status == "not_answerable" and state.get("redrafts", 0) < max_redrafts:
            # The query ran, yet the reader declined: usually the query was the wrong one, or a name
            # it filtered on was spelled differently. Send the reason back to draft instead of stopping.
            attempts = state.get("attempts", [])[:-1] + [
                {"sql": state["sql"], "error": "You ran this and judged it does not answer the question: "
                                               + out.get("explanation", "")}]
            return {"status": "redraft", "attempts": attempts, "redrafts": state.get("redrafts", 0) + 1}
        got = out.get("answer")
        if status == "answer":
            # The question asked for an identifier the rows have and the answer left out: add it.
            missing = answers.missing_identifiers(dom.identifiers, state["question"], rows, got)
            if missing:
                got = answers.with_identifiers(got, missing)
        return {"status": status, "answer": got, "explanation": out.get("explanation", "")}

    def review(state: State) -> dict:
        """Check the books behind the answer and add the caveats a careful analyst would state."""
        if state.get("status") not in ("answer", "not_answerable") or not dom.issues:
            return {}
        try:
            with dom.connect() as conn:
                found = reviewing.gather(conn, dom.issues, state["question"], state.get("sql", ""),
                                         state.get("rows", []))
                if dom.investigate:
                    def run_check(q):
                        sqltool.prepare(dom, conn)
                        return sqltool.run(dom, conn, q)
                    out = reviewing.investigate(
                        conn, dom, review_model or model, system, state["question"], state.get("sql", ""),
                        state.get("rows", []), state.get("answer"), state.get("explanation", ""),
                        state["status"], found, dom.not_loaded, run_check)
            if not dom.investigate:
                if not found["issues"] and not found["records"]:
                    return {}
                out = (review_model or model)(system, reviewing.prompt(
                    state["question"], state.get("sql", ""), state.get("rows", []), state.get("answer"),
                    state.get("explanation", ""), found), reviewing.REVIEW_SCHEMA)
        except Exception as exc:  # a failed review never loses the answer; it says so
            return {"caveats": [], "review_error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        count, room = reviewing.budget(found.get("verified", 0))
        caveats = [c for c in out.get("caveats") or [] if isinstance(c, str)][:count]
        verdict = out.get("status") or "keep"
        # The books figure stays the headline; a corrected figure is a labelled second sentence, kept only
        # when it differs from the books figure.
        corrected = (reviewing.corrected_sentence(state.get("answer"), out.get("corrected"))
                     if state["status"] == "answer" and verdict != "not_answerable" else "")
        update = {"caveats": caveats, "review_checks": out.get("checks", []),
                  "explanation": reviewing.merge(state.get("explanation", ""), caveats, room, corrected)}
        if verdict == "not_answerable" and state["status"] == "answer":
            update.update(status="not_answerable", answer=None)
        elif verdict == "answer" and state["status"] == "not_answerable" and (out.get("answer") or "").strip():
            update.update(status="answer", answer=out["answer"].strip())
        return update

    def cite(state: State) -> dict:
        refs = sorted({str(r[c]) for r in state.get("rows", []) for c in r
                       if c == "source_ref" and r[c]})
        result = {}
        if refs and dom.citations == "clearledger":
            with dom.connect() as conn:
                result = sqltool._clearledger().check_citations(conn, refs)
        return {"sources": refs, "citations": result}

    def remember(state: State) -> dict:
        return {"history": [{"question": state["question"], "sql": state.get("sql"),
                             "answer": state.get("answer"), "status": state.get("status")}]}

    g = StateGraph(State)
    for name, fn in [("retrieve", retrieve), ("draft", draft), ("execute", execute), ("answer", answer),
                     ("review", review), ("cite", cite), ("remember", remember)]:
        g.add_node(name, fn)
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "draft")
    g.add_conditional_edges("draft", lambda s: "execute" if s["status"] in ("running", "lookup")
                            else "draft" if s["status"] == "hint"
                            else ("review" if with_review and s["status"] == "not_answerable" else "remember"))
    g.add_conditional_edges("execute", lambda s: {"repair": "draft", "looked": "draft",
                                                  "failed": "remember"}.get(s["status"], "answer"))
    g.add_conditional_edges("answer", lambda s: "draft" if s["status"] == "redraft"
                            else ("review" if with_review else "cite"))
    g.add_edge("review", "cite")
    g.add_edge("cite", "remember")
    g.add_edge("remember", END)
    return g.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())


def ask(app, question: str, thread: str = "default") -> dict:
    """One turn. Returns the final state: status, answer, explanation, sql, attempts, sources."""
    return app.invoke({"question": question}, config={"configurable": {"thread_id": thread}})


def one_shot(dom: Domain, model, question: str) -> dict:
    """The baseline: one call with the bare schema, one query, its result taken as the answer."""
    prompt = f"TABLES (name and columns only):\n{bare_schema(dom)}\n\nQUESTION\n{question}"
    out = model(_system(dom), prompt, DRAFT_SCHEMA)
    if out.get("decision") != "query":
        return {"status": "not_answerable", "answer": None, "sql": "", "explanation": out.get("reason", "")}
    with dom.connect() as conn:
        sqltool.prepare(dom, conn)
        result = sqltool.run(dom, conn, out.get("sql", ""))
    if not result["ok"]:
        return {"status": "failed", "answer": None, "sql": out.get("sql"), "explanation": result["error"]}
    return {"status": "answer", "answer": value_of(result["rows"]), "sql": out.get("sql"), "explanation": ""}


def value_of(rows: list[dict]):
    """A result read the way a dashboard would: one cell is the answer, otherwise the first column."""
    if not rows:
        return []
    if len(rows) == 1 and len(rows[0]) == 1:
        v = next(iter(rows[0].values()))
        return v if not isinstance(v, list) else [str(x) for x in v]
    return [str(next(iter(r.values()))) for r in rows]
