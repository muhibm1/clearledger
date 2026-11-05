"""The playbook's stages, holds and once-only rule."""

import datetime as dt

import pytest

from remit.playbook import Evidence, plan_all, plan_customer, stage_for

from .helpers import AS_OF, inv


@pytest.mark.parametrize("days,stage", [(-3, 0), (0, 0), (1, 1), (7, 1), (8, 2), (30, 2), (31, 3),
                                        (60, 3), (61, 4), (400, 4)])
def test_stage_boundaries(days, stage):
    assert stage_for(days) == stage


def test_not_on_the_list_is_refused():
    with pytest.raises(ValueError):
        stage_for(-4)


def test_one_reminder_per_customer_at_its_worst_stage():
    plan = plan_customer([inv(1, days=5), inv(2, days=20)], Evidence(), AS_OF, set())
    kinds = [a.kind for a in plan.actions]
    assert kinds == ["reminder", "follow_up"]
    reminder = plan.actions[0]
    assert reminder.stage == 2
    assert [i.invoice_id for i in reminder.invoices] == [2, 1]  # oldest due first
    assert reminder.target_invoice_id == 2
    assert plan.actions[1].follow_up_on == AS_OF + dt.timedelta(days=7)


def test_never_twice_for_the_same_invoice_and_stage():
    first = plan_customer([inv(1, days=5)], Evidence(), AS_OF, set())
    done = set(first.actions[0].keys)
    again = plan_customer([inv(1, days=6)], Evidence(), AS_OF, done)
    assert again.actions == []
    later = plan_customer([inv(1, days=9)], Evidence(), AS_OF, done)  # moved to stage 2
    assert [a.kind for a in later.actions] == ["reminder", "follow_up"]
    assert later.actions[0].stage == 2


def test_same_action_same_key():
    a = plan_customer([inv(1, days=5)], Evidence(), AS_OF, set()).actions[0]
    b = plan_customer([inv(1, days=5)], Evidence(), AS_OF, set()).actions[0]
    assert a.idempotency_key == b.idempotency_key


def test_over_60_days_escalates_to_a_person_and_does_not_message_the_customer():
    plan = plan_customer([inv(1, days=75), inv(2, days=3)], Evidence(), AS_OF, set())
    assert [a.kind for a in plan.actions] == ["escalate"]
    assert {i.invoice_id for i in plan.actions[0].invoices} == {1, 2}


def test_customers_ordered_by_money_late():
    plans = plan_all([inv(1, amount="100", customer=1), inv(2, amount="9000", customer=2)],
                     {}, AS_OF, set())
    assert [p.customer_key for p in plans] == [2, 1]


def test_mixed_customers_refused():
    with pytest.raises(ValueError):
        plan_customer([inv(1, customer=1), inv(2, customer=2)], Evidence(), AS_OF, set())


def test_a_flag_is_raised_once_per_invoice_version():
    rows = [inv(1, days=10, changed=True)]
    first = plan_customer(rows, Evidence(), AS_OF, set())
    assert [a.kind for a in first.actions] == ["flag"]
    done = set(first.actions[0].keys)
    assert plan_customer(rows, Evidence(), AS_OF, done).actions == []
    changed_again = [inv(1, days=10, changed=True, version="v2")]
    assert [a.kind for a in plan_customer(changed_again, Evidence(), AS_OF, done).actions] == ["flag"]
