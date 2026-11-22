"""The onboarding pass keeps only findings that name records in the books."""

from analyst import onboard

KNOWN = {"inv/2026/00001", "acme ltd", "s00001"}


def test_a_finding_naming_a_real_record_is_kept_with_only_the_real_references():
    got = onboard.valid({"refs": ["INV/2026/00001", "INV/2026/09999"], "customer": "Acme Ltd",
                         "affects": ["open", "made_up"], "amount": "12.5", "detail": " Due before issued. "}, KNOWN)
    assert got == {"refs": ["INV/2026/00001"], "customer": "Acme Ltd", "affects": ["open"], "amount": 12.5,
                   "detail": "Due before issued."}


def test_a_finding_naming_nothing_in_the_books_is_dropped():
    assert onboard.valid({"refs": ["INV/2026/09999"], "customer": "", "affects": ["open"], "amount": 0,
                          "detail": "Looks odd."}, KNOWN) is None


def test_a_finding_with_no_detail_is_dropped_and_missing_affects_default_to_counts():
    assert onboard.valid({"refs": ["S00001"], "detail": ""}, KNOWN) is None
    assert onboard.valid({"refs": ["S00001"], "detail": "x"}, KNOWN)["affects"] == ["counts"]


def test_large_sections_are_read_in_parts():
    rows = ["x" * 1000] * 100
    parts = onboard._parts(rows)
    assert len(parts) > 1 and sum(len(p) for p in parts) == 100


# --- verified onboarding ---

from analyst.model import FakeModel  # noqa: E402

KNOWN_TYPED = {
    "inv/2026/00001": [{"kind": "invoice", "id": 11, "ref": "INV/2026/00001", "customer_key": 7}],
    "s00001": [{"kind": "order", "id": 3, "ref": "S00001", "customer_key": 7}],
    "acme ltd": [{"kind": "customer", "id": 7, "ref": "Acme Ltd", "customer_key": 7}],
    "p0001": [{"kind": "payment", "id": 5, "ref": "P0001", "customer_key": 9}],
}


def test_stated_amounts_leave_out_references_dates_years_and_percentages():
    """Only money amounts count: not record numbers, dates, years or shares."""
    text = ("Order S00001 (22,640.00) on 2026-07-20 billed 13,580 on INV/2026/00001 in 2026, 66% of it, 3 times, "
            "196 days late, a credit of 640.")
    assert onboard.stated_amounts(text, ["S00001", "INV/2026/00001"]) == [22640.0, 13580.0, 640.0]


def test_an_amount_is_supported_by_a_value_a_sum_a_difference_or_a_column_total():
    """Derived figures a finding states (a gap, a total) are borne out by the rows they come from."""
    nums = [22640.0, 13580.0]
    assert onboard.supported(13580, nums)
    assert onboard.supported(9060, nums)          # difference
    assert onboard.supported(36220, nums)         # sum
    assert onboard.supported(8335, [2310, 3145, 2880], [8335])  # column total
    assert not onboard.supported(5000, nums)


def test_verify_keeps_a_finding_whose_records_and_amounts_are_in_the_rows():
    """References and amounts present in the rows verify the finding."""
    rows = [{"order_ref": "S00001", "amount_total": 22640.0, "invoices": "INV/2026/00001 13580.0 USD"}]
    f = {"refs": ["S00001", "INV/2026/00001"], "amount": 9060, "detail": "S00001 is 22,640 but billed 13,580."}
    assert onboard.verify(f, rows, KNOWN_TYPED) == (True, [])


def test_verify_rejects_a_finding_stating_an_amount_the_rows_do_not_hold():
    """A misread amount (the invoice said to equal its order) fails."""
    rows = [{"order_ref": "S00001", "amount_total": 22640.0, "invoices": "INV/2026/00001 13580.0 USD"}]
    f = {"refs": ["S00001"], "amount": 0, "detail": "S00001 was billed 21,000 in full."}
    ok, missing = onboard.verify(f, rows, KNOWN_TYPED)
    assert not ok and missing == ["21,000.00"]


def test_verify_rejects_a_finding_whose_record_is_not_in_the_rows_but_not_for_a_party_name():
    """A record number must come back from the check; a customer's name need not."""
    rows = [{"payment_ref": "P0001", "amount": 500.0}]
    assert onboard.verify({"refs": ["INV/2026/00001"], "amount": 500, "detail": "x"}, rows, KNOWN_TYPED)[0] is False
    assert onboard.verify({"refs": ["P0001", "Acme Ltd"], "amount": 500, "detail": "x"}, rows, KNOWN_TYPED)[0]
    assert onboard.verify({"refs": ["P0001"], "amount": 0, "detail": "x"}, [], KNOWN_TYPED)[0] is False


def test_typed_records_give_each_reference_its_kind_and_id():
    """A finding's references become typed record ids for the review step."""
    got = onboard.typed_records(["INV/2026/00001", "Acme Ltd", "INV/2026/00001"], KNOWN_TYPED)
    assert [(r["kind"], r["id"]) for r in got] == [("invoice", 11), ("customer", 7)]


def test_name_words_keep_only_the_distinctive_words_of_a_company_name():
    """Legal and generic words do not make two names alike."""
    assert onboard.name_words("Larch Analytical Group, Inc.") == ["larch", "analytical"]
    assert onboard.name_words("") == []


class _Dom:
    executor = "test"


def _fake_run(results):
    """A stand-in for the read-only executor that returns scripted rows per SQL text."""
    def run(dom, conn, sql):
        return results.get(sql, {"ok": False, "error": "unknown"})
    return run


def test_read_section_keeps_verified_findings_rewrites_a_failed_one_and_drops_a_wrong_one(monkeypatch):
    """Verified as written, rewritten once from the rows, or dropped."""
    monkeypatch.setattr(onboard.sqltool, "prepare", lambda dom, conn: None)
    monkeypatch.setattr(onboard.sqltool, "run", _fake_run({
        "q1": {"ok": True, "rows": [{"invoice_ref": "INV/2026/00001", "amount": 1200.0}]},
        "q2": {"ok": True, "rows": [{"order_ref": "S00001", "amount_total": 900.0, "invoiced": 400.0}]},
        "q3": {"ok": True, "rows": [{"payment_ref": "P0001", "amount": 75.0}]},
    }))
    first = {"findings": [
        {"refs": ["INV/2026/00001"], "customer": "Acme Ltd", "affects": ["open"], "amount": 1200,
         "detail": "Open 1,200.", "check_sql": "q1"},
        {"refs": ["S00001"], "customer": "", "affects": ["billed"], "amount": 0,
         "detail": "S00001 was invoiced 850 in full.", "check_sql": "q2"},
        {"refs": ["P0001"], "customer": "", "affects": ["cash"], "amount": 750,
         "detail": "P0001 of 750 paid nothing.", "check_sql": "q3"},
    ]}
    rewrites = {"rewrites": [
        {"index": 0, "keep": True, "refs": ["S00001"], "customer": "", "affects": ["billed"], "amount": 500,
         "detail": "S00001 totals 900 but 400 was invoiced, 500 not billed.", "check_sql": "q2"},
        {"index": 1, "keep": False, "refs": ["P0001"], "customer": "", "affects": ["cash"], "amount": 0,
         "detail": "", "check_sql": ""},
    ]}
    model = FakeModel([first, rewrites])
    kept, counts = onboard.read_section(None, _Dom(), model, "orders", ["{}"], 1, "", KNOWN_TYPED, say=lambda *_: None)
    assert [f["detail"] for f in kept] == ["Open 1,200.", "S00001 totals 900 but 400 was invoiced, 500 not billed."]
    assert counts == {"written": 3, "verified": 1, "rewritten": 1}
    assert "NOT IN THE ROWS" in model.prompts[1] and "[1] FINDING" in model.prompts[1]


def test_a_rewrite_that_still_misstates_the_rows_is_dropped(monkeypatch):
    """A rewrite gets the same verification as the first finding."""
    monkeypatch.setattr(onboard.sqltool, "prepare", lambda dom, conn: None)
    monkeypatch.setattr(onboard.sqltool, "run", _fake_run({
        "q": {"ok": True, "rows": [{"order_ref": "S00001", "amount_total": 900.0}]}}))
    first = {"findings": [{"refs": ["S00001"], "customer": "", "affects": ["billed"], "amount": 0,
                           "detail": "S00001 is 950.", "check_sql": "q"}]}
    again = {"rewrites": [{"index": 0, "keep": True, "refs": ["S00001"], "customer": "", "affects": ["billed"],
                           "amount": 0, "detail": "S00001 is 960.", "check_sql": "q"}]}
    kept, _ = onboard.read_section(None, _Dom(), FakeModel([first, again]), "orders", ["{}"], 1, "", KNOWN_TYPED,
                                   say=lambda *_: None)
    assert kept == []
