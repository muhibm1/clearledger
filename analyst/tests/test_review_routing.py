"""The review step joins onboarding findings to an answer by the record ids behind its figure, and ranks
verified findings ahead of detector rows for a company-wide figure. A small fake connection stands in
for the warehouse."""

from analyst import review

ISSUE_COLS = ["issue_type", "entity_kind", "entity_ref", "customer_key", "customer_name", "event_date", "amount",
              "affects", "detail"]
FINDING_COLS = ISSUE_COLS + ["records", "invoice_ids", "payment_ids", "order_ids", "deal_ids", "customer_keys",
                             "verified"]


def _finding(ref, detail, amount, affects, invoice_ids=(), order_ids=(), payment_ids=(), deal_ids=(), keys=()):
    """One onboarding finding row with typed record ids."""
    return ("onboarding_finding", "order", ref, None, "", None, amount, list(affects), detail, [{"kind": "x"}],
            list(invoice_ids), list(payment_ids), list(order_ids), list(deal_ids), list(keys), True)


class _Cur:
    """A cursor that answers each query from a handler."""

    def __init__(self, handler):
        self.handler, self.description, self._rows = handler, [], []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        cols, rows = self.handler(sql, params)
        self.description = [type("D", (), {"name": c}) for c in cols]
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    """A connection whose cursors share one handler."""

    autocommit = True

    def __init__(self, handler):
        self.handler = handler

    def cursor(self):
        return _Cur(self.handler)


def _world(findings, issues=(), resolve=(), expand=()):
    """A handler serving the issues table, the findings table, and the id lookups."""
    def handler(sql, params):
        if "to_regclass" in sql:
            return ["r"], [("marts.onboarding_findings",)]
        if "from marts.onboarding_findings" in sql:
            return FINDING_COLS, list(findings)
        if "from marts.data_issues" in sql:
            return ISSUE_COLS, list(issues)
        if "where lower(invoice_ref) = any" in sql:
            return ["k", "id", "ref"], list(resolve)
        if "o.deal_id = any(%(deal)s)" in sql and "union all select 'deal'" in sql:
            return ["k", "id"], list(expand)
        return ["x"], []
    return _Conn(handler)


def test_a_finding_joins_an_answer_by_the_order_behind_it_not_by_its_wording():
    """The finding about S00010's invoice comes in through the order id, though its text names neither row."""
    conn = _world(
        findings=[_finding("S00010", "Order totals 22,640 but its invoice bills 13,580.", 9060, ["billed"],
                           order_ids=[10], invoice_ids=[126]),
                  _finding("S00099", "Unrelated order.", 5, ["billed"], order_ids=[99])],
        resolve=[("order", 10, "S00010")])
    found = review.gather(conn, "marts.data_issues", "Has order S00010 been fully invoiced?",
                          "select * from marts.deal_to_cash where order_ref = 'S00010'",
                          [{"deal_name": "Riverside survey", "ordered": 22640}])
    assert "22,640 but its invoice bills 13,580" in found["issues"]
    assert "Unrelated order" not in found["issues"]
    assert found["verified"] == 1


def test_an_id_in_any_result_row_counts_not_only_the_first_twenty():
    """Records behind a figure are read from every row."""
    rows = [{"invoice_id": i} for i in range(1, 26)]
    conn = _world(findings=[_finding("INV/2026/00025", "Paid by card, never recorded.", 700, ["open"],
                                     invoice_ids=[25])])
    behind = review.answer_records(conn, "q", "select invoice_id from x where customer_key = 3", rows, False)
    assert 25 in behind["ids"]["invoice"]


def test_a_company_wide_figure_lists_verified_findings_on_its_measure_largest_first_before_detector_rows():
    """Verified findings rank by amount ahead of the detectors' rows; findings off the measure stay out."""
    conn = _world(
        findings=[_finding("P1", "Small unapplied receipt.", 500, ["cash"], payment_ids=[1]),
                  _finding("INV/1", "Billed after the contract ended.", 11700, ["open"], invoice_ids=[1]),
                  _finding("INV/2", "Paid by card, never recorded.", 10006, ["open"], invoice_ids=[2]),
                  _finding("D1", "Deal value is three years.", 99000, ["deals"], deal_ids=[1])],
        issues=[("short_paid", "invoice", "INV/9", 4, "Acme", None, 50000, ["open"], "Detector row.")])
    found = review.gather(conn, "marts.data_issues", "How much do customers owe us in total?",
                          "select sum(open) from marts.revenue_by_customer", [{"sum": 343728.91}])
    lines = found["issues"].splitlines()
    assert "Billed after the contract ended" in lines[0] and "Paid by card" in lines[1]
    assert "Detector row" in lines[2]
    assert "three years" not in found["issues"] and "unapplied" not in found["issues"]
    assert found["verified"] == 2 and found["wide"]


def test_a_verified_finding_keeps_its_record_references_and_amount():
    """The line the reviewer reads carries the references verbatim."""
    line = review._line({"verified": True, "entity_ref": "PBNK1/2026/00061, INV/2026/00011", "amount": 7385,
                         "detail": "Pays another company's invoice."})
    assert "PBNK1/2026/00061, INV/2026/00011" in line and "7,385.00" in line


def test_a_finding_on_the_answers_customer_joins_only_on_the_measure():
    """A customer-level answer takes that customer's findings on the measure it reads."""
    behind = {"ids": {"invoice": set(), "payment": set(), "order": set(), "deal": set(), "customer": {7}},
              "customer_level": {7}, "refs": set()}
    assert review._id_overlap({"customer_keys": [7]}, behind) == "customer"
    assert review._id_overlap({"customer_keys": [8]}, behind) == ""
    assert review._id_overlap({"invoice_ids": [3], "customer_keys": []},
                              {**behind, "ids": {**behind["ids"], "invoice": {3}}}) == "record"
