"""The caveat contract: room grows with verified findings inside the figure, references stay verbatim, and
a restated answer keeps the books figure."""

from analyst import review
from analyst.model import FakeModel


def test_the_budget_grows_with_verified_findings_up_to_eight_caveats_and_900_characters():
    """No verified finding keeps the old room; each one adds room, up to the cap."""
    assert review.budget(0) == (5, 470)
    assert review.budget(2) == (7, 690)
    assert review.budget(10) == (8, 900)


def test_merge_keeps_the_caveats_that_fit_the_room():
    """First sentence, then the caveats that fit the room it is given."""
    caveats = ["A" * 200, "B" * 200, "C" * 200]
    out = review.merge("Owed is 343,728.91. From the open column.", caveats, room=450)
    assert out.startswith("Owed is 343,728.91. Caveat: AAA")
    assert "BBB" in out and "CCC" not in out
    assert "CCC" in review.merge("Owed is 343,728.91.", caveats, room=900)
    assert review.merge("Plain.", []) == "Plain."


def test_the_rules_state_the_budget_and_how_verified_findings_count():
    """The reviewer is told its room, that verified findings count as proved, and to copy references."""
    found = {"issues": "- [verified finding, records: S1; amount 1.00] x", "wide": False, "records": "",
             "verified": 3}
    text = review.prompt("q", "select 1", [], 1, "e", found)
    assert "at most 8 caveats, together under 800 characters" in text
    assert "[verified finding] was checked against the books' own rows" in text
    assert "exactly as the books write it" in text


def test_a_restated_answer_must_keep_the_books_figure():
    """A restatement that drops the books figure is refused; one that keeps it is used."""
    assert review.revised(60735, "53,350 without PBNK1/2026/00061") is None
    assert review.revised(60735, "60,735 on the books; 53,350 without PBNK1/2026/00061") == \
        "60,735 on the books; 53,350 without PBNK1/2026/00061"


def test_the_review_node_uses_the_budget_and_keeps_the_books_figure_in_a_restated_answer(monkeypatch):
    """With verified findings the node keeps more caveats, and a restated answer still holds the books figure."""
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
                                                                    "verified": 3, "names": []})
    caveats = [f"PBNK1/2026/0006{i} (1,000) pays another company's invoice." for i in range(7)]
    model = FakeModel([{"decision": "query", "sql": "select 1", "reason": "r"},
                       {"status": "answer", "answer": 60735, "explanation": "Cash in was 60,735."},
                       {"caveats": caveats, "answer": "60,735 on the books; 53,350 without PBNK1/2026/00061"}])
    monkeypatch.setattr(graph.sqltool, "prepare", lambda d, c: None)
    monkeypatch.setattr(graph.sqltool, "run", lambda d, c, q: {"ok": True, "rows": [{"cash": 60735}]})
    app = graph.build(dom, model, context=False, with_review=True, max_lookups=0)
    out = graph.ask(app, "How much cash came in?", "t-contract")
    assert out["answer"] == "60,735 on the books; 53,350 without PBNK1/2026/00061"
    assert out["explanation"].startswith("Cash in was 60,735. Caveat: PBNK1/2026/00060")
    assert len(out["caveats"]) == 7
