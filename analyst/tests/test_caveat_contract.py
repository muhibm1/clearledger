"""The caveat contract: room grows with verified findings inside the figure, the books figure stays the
headline, and a corrected figure is a labelled second sentence only when it differs."""

from analyst import review
from analyst.model import FakeModel


def test_the_budget_grows_with_verified_findings_up_to_eight_caveats_and_900_characters():
    """No verified finding keeps the old room; each one adds room, up to the cap."""
    assert review.budget(0) == (5, 470)
    assert review.budget(2) == (7, 690)
    assert review.budget(10) == (8, 900)


def test_a_corrected_figure_is_kept_only_when_it_differs_from_the_books_figure():
    """The same number restated is dropped; a different one is labelled."""
    assert review.corrected_sentence(60735, "Excluding the receipt for another company: 53,350.") == \
        "Corrected: Excluding the receipt for another company: 53,350."
    assert review.corrected_sentence(60735, "Books figure 60,735.") == ""
    assert review.corrected_sentence(34, "") == ""
    assert review.corrected_sentence(34, "Corrected: 33 excluding the test account") == \
        "Corrected: 33 excluding the test account."


def test_merge_puts_the_corrected_sentence_second_and_caveats_after_within_the_room():
    """First sentence, then the corrected figure, then the caveats that fit."""
    out = review.merge("Owed is 343,728.91. From the open column.", ["A" * 200, "B" * 200, "C" * 200],
                       room=450, corrected="Corrected: 320,000 without the card payments.")
    assert out.startswith("Owed is 343,728.91. Corrected: 320,000 without the card payments. Caveat: AAA")
    assert "BBB" in out and "CCC" not in out
    assert review.merge("Plain.", [], corrected="Corrected: 3.") == "Plain. Corrected: 3."


def test_the_rules_state_the_budget_and_rank_verified_findings_first():
    """The reviewer is told its room and that verified findings outrank its own hunches."""
    found = {"issues": "- [verified finding, records: S1; amount 1.00] x", "wide": False, "records": "",
             "verified": 3}
    text = review.prompt("q", "select 1", [], 1, "e", found)
    assert "at most 8 caveats, together under 800 characters" in text
    assert "verified finding" in text and "NEVER CONTRADICT A MART" in text


def test_the_review_node_keeps_the_books_answer_and_adds_the_corrected_sentence(monkeypatch):
    """A reviewer's restated answer never replaces the figure; its corrected figure goes second."""
    from analyst import graph
    from analyst.domain import Domain

    dom = Domain(name="t", title="t", dsn="", executor="test", schemas=["marts"], as_of=None,
                 issues="marts.data_issues", investigate=False)

    class _C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(Domain, "connect", lambda self: _C())
    monkeypatch.setattr(graph.reviewing, "gather", lambda *a, **k: {"issues": "- x", "records": "", "wide": True,
                                                                    "verified": 1, "names": []})
    model = FakeModel([{"decision": "query", "sql": "select 1", "reason": "r"},
                       {"status": "answer", "answer": 60735, "explanation": "Cash in was 60,735."},
                       {"caveats": ["PBNK1/2026/00061 (7,385) pays another company's invoice."],
                        "corrected": "Without PBNK1/2026/00061: 53,350.", "answer": "53,350 corrected"}])
    monkeypatch.setattr(graph.sqltool, "prepare", lambda d, c: None)
    monkeypatch.setattr(graph.sqltool, "run", lambda d, c, q: {"ok": True, "rows": [{"cash": 60735}]})
    app = graph.build(dom, model, context=False, with_review=True, max_lookups=0)
    out = graph.ask(app, "How much cash came in?", "t-contract")
    assert out["answer"] == 60735
    assert out["explanation"].startswith("Cash in was 60,735. Corrected: Without PBNK1/2026/00061: 53,350. Caveat:")
