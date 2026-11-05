"""Drafts: the template always passes its own checks, and the checks catch what they should."""

import pytest

from remit import draft
from remit.playbook import Evidence, plan_customer

from .helpers import AS_OF, inv


@pytest.mark.parametrize("days", [-2, 4, 15, 45])
def test_template_passes_the_checks_at_every_stage(days):
    action = plan_customer([inv(1, days=days), inv(2, days=days, amount="250.5")], Evidence(), AS_OF,
                           set()).actions[0]
    d = draft.template(action)
    assert draft.check_draft(d, action) == []


def test_final_notice_names_the_hold_date():
    action = plan_customer([inv(1, days=45)], Evidence(), AS_OF, set()).actions[0]
    assert action.follow_up_on.isoformat() in draft.template(action)["body"]


def _action():
    return plan_customer([inv(1, days=10, amount="1234.50")], Evidence(), AS_OF, set()).actions[0]


@pytest.mark.parametrize("body,problem", [
    ("Please pay.", "does not name invoice"),
    ("INV/2026/00001 is overdue.", "does not state $1,234.50"),
    ("INV/2026/00001 $1,234.50, or $1,000.00 if paid today.", "not owed"),
    ("INV/2026/00001 $1,234.50. Pay now to avoid a late fee.", "forbidden"),
    ("INV/2026/00001 $1,234.50. Pay at https://pay.example.com", "link"),
])
def test_checks(body, problem):
    problems = draft.check_draft({"subject": "x", "body": body}, _action())
    assert any(problem in p for p in problems), problems


def test_a_good_model_draft_is_kept():
    def good(system, prompt, schema):
        assert "narration" not in prompt.lower()
        return {"subject": "Reminder", "body": "Hi, INV/2026/00001 for $1,234.50 is past due. Thanks."}
    d, by, problems = draft.write(_action(), good)
    assert by == "model" and problems == []


def test_only_reminders_are_drafted():
    action = plan_customer([inv(1, days=90)], Evidence(), AS_OF, set()).actions[0]
    with pytest.raises(ValueError):
        draft.write(action)
