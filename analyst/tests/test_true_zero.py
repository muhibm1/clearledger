"""An entity the books hold with an empty measure is a true zero, not a decline."""

from analyst import graph
from analyst.domain import Domain
from analyst.model import FakeModel


class _Cur:
    """A cursor that finds names from a fixed set."""

    def __init__(self, names):
        self.names, self.got = names, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        pat = params[0].strip("%").lower()
        self.got = next(((n,) for n in self.names if pat in n.lower()), None)

    def fetchone(self):
        return self.got


class _Conn:
    """A connection over a fixed set of names."""

    def __init__(self, names):
        self.names = names

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _Cur(self.names)


def _dom(monkeypatch, names):
    """A domain whose entity lookup sees only `names` and whose queries return no rows."""
    dom = Domain(name="t", title="t", dsn="", executor="test", schemas=["marts"], as_of=None,
                 entities=["intermediate.int_customers.customer_name"],
                 not_loaded=["payment journals and methods"])
    monkeypatch.setattr(Domain, "connect", lambda self: _Conn(names))
    monkeypatch.setattr(graph.sqltool, "prepare", lambda d, c: None)
    monkeypatch.setattr(graph.sqltool, "run", lambda d, c, q: {"ok": True, "rows": []})
    return dom


SQL = "select sum(open) from marts.revenue_by_customer where customer_name ilike '%timber%'"


def test_an_existing_customer_found_by_code_is_reported_to_the_answer_step(monkeypatch):
    """Code finds the name the query filters on, so the prompt says the empty result is a true zero."""
    dom = _dom(monkeypatch, ["Timberline Lodge Holdings"])
    assert graph.existing_entity(dom, SQL) == "Timberline Lodge Holdings"
    model = FakeModel([{"decision": "query", "sql": SQL, "reason": "r"},
                       {"status": "answer", "answer": 0, "explanation": "Nothing owed."}])
    out = graph.ask(graph.build(dom, model, context=False, with_review=False, max_lookups=0), "Owed?", "z1")
    assert "true zero" in model.prompts[1] and out["answer"] == 0


def test_a_decline_on_an_existing_customer_becomes_zero(monkeypatch):
    """The model declining for a customer the books hold is overruled: the figure is 0."""
    dom = _dom(monkeypatch, ["Timberline Lodge Holdings"])
    model = FakeModel([{"decision": "query", "sql": SQL, "reason": "r"},
                       {"status": "not_answerable", "answer": None, "explanation": "No invoices, cannot tell."}])
    out = graph.ask(graph.build(dom, model, context=False, with_review=False, max_lookups=0), "Owed?", "z2")
    assert out["status"] == "answer" and out["answer"] == 0 and "Timberline Lodge Holdings" in out["explanation"]


def test_a_decline_naming_data_the_warehouse_does_not_hold_stands(monkeypatch):
    """A true decline (the data is not loaded) is not turned into a zero."""
    dom = _dom(monkeypatch, ["Timberline Lodge Holdings"])
    model = FakeModel([{"decision": "query", "sql": SQL, "reason": "r"},
                       {"status": "not_answerable", "answer": None, "explanation": "Payment methods are not held."},
                       {"decision": "not_answerable", "sql": "", "reason": "Payment methods are not held."}])
    out = graph.ask(graph.build(dom, model, context=False, with_review=False, max_lookups=0), "By card?", "z3")
    assert out["status"] == "not_answerable"


def test_a_name_the_books_do_not_hold_still_cannot_be_told(monkeypatch):
    """No entity found: the old rule (decline, the name could not be found) is kept."""
    dom = _dom(monkeypatch, ["Someone Else"])
    assert graph.existing_entity(dom, SQL) == ""
    model = FakeModel([{"decision": "query", "sql": SQL, "reason": "r"},
                       {"status": "not_answerable", "answer": None, "explanation": "Not found."},
                       {"decision": "not_answerable", "sql": "", "reason": "Not found."}])
    out = graph.ask(graph.build(dom, model, context=False, with_review=False, max_lookups=0), "Owed?", "z4")
    assert "could not be found" in model.prompts[1] and out["status"] == "not_answerable"
