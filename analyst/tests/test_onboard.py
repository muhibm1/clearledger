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
