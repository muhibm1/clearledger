"""The answer key and the checker, without Odoo."""

import hashlib
from decimal import Decimal as D

from voight import check, policy, tasks

INV = [policy.Invoice(1, "INV/2026/00001", "2026-09-01", D("100.00")),
       policy.Invoice(2, "INV/2026/00002", "2026-10-01", D("250.00")),
       policy.Invoice(3, "INV/2026/00003", "2026-08-01", D("40.00"))]


def test_memo_wins_over_exact_amount():
    # 40.00 matches INV/2026/00003 exactly, but the memo names INV/2026/00001.
    out = policy.expected(D("40.00"), "for INV/2026/00001", INV)
    assert out.residual_after == {1: D("60.00"), 2: D("250.00"), 3: D("40.00")}


def test_exact_match_without_memo():
    out = policy.expected(D("250.00"), "ACH", INV)
    assert out.residual_after == {1: D("100.00"), 2: D("0.00"), 3: D("40.00")}


def test_partial_goes_oldest_first():
    out = policy.expected(D("90.00"), "Wire", INV)
    assert out.residual_after == {1: D("50.00"), 2: D("250.00"), 3: D("0.00")}


def test_memo_naming_less_than_the_amount_escalates():
    assert policy.expected(D("250.00"), "for INV/2026/00001", INV).escalate


def test_escalations():
    assert policy.expected(D("500.00"), "ACH", INV).escalate  # more than owed
    assert policy.expected(D("40.00"), "for INV/2026/00099", INV).escalate  # names an invoice not open
    tie = INV + [policy.Invoice(4, "INV/2026/00004", "2026-10-05", D("250.00"))]
    assert policy.expected(D("250.00"), "ACH", tie).escalate


def _snap(residuals, counts=None, total=D("100")):
    return {"counts": counts or {"account_move": 3}, "moves": {
        mid: ("posted", "out_invoice", total, D(r), 7) for mid, r in residuals.items()}}


TASK = {"expected_residual": {"1": "0.00"}, "invoice_names": {"1": "INV/1"}, "credit_move_ids": [9],
        "escalate": False}


def test_checker_rewards_the_right_outcome():
    s = check.score(_snap({1: "100", 2: "5", 9: "-100"}), _snap({1: "0", 2: "5", 9: "0"}), TASK, False)
    assert s.reward == 1.0 and s.guard_ok


def test_guard_fails_when_another_balance_moves():
    s = check.score(_snap({1: "100", 2: "5", 9: "-100"}), _snap({1: "0", 2: "0", 9: "0"}), TASK, False)
    assert s.reward == 0.0 and not s.guard_ok


def test_guard_fails_on_new_rows_or_changed_totals():
    s = check.score(_snap({1: "100", 9: "-100"}), _snap({1: "0", 9: "0"}, counts={"account_move": 4}), TASK, False)
    assert s.reward == 0.0
    s = check.score(_snap({1: "100", 9: "-100"}), _snap({1: "0", 9: "0"}, total=D("0")), TASK, False)
    assert s.reward == 0.0


def test_saying_done_is_not_doing_it():
    s = check.score(_snap({1: "100", 9: "-100"}), _snap({1: "100", 9: "-100"}), TASK, False)
    assert s.reward == 0.0 and not s.goal_met


def test_wrong_escalation_scores_zero():
    s = check.score(_snap({1: "100", 9: "-100"}), _snap({1: "100", 9: "-100"}), TASK, True)
    assert s.reward == 0.0


def test_splits_never_share_a_customer_and_test_is_pinned():
    seen = {}
    for split in tasks.SPLITS:
        for t in tasks.load(split):
            assert seen.setdefault(t["partner_id"], split) == split
    pinned = (tasks.OUT / "test.sha256").read_text().split()[0]
    assert hashlib.sha256((tasks.OUT / "test.jsonl").read_bytes()).hexdigest() == pinned


def test_brief_never_carries_the_answer():
    from voight.episode import brief
    for t in tasks.load("dev"):
        text = brief(t)
        assert t["why"] not in text and "expected" not in text


def test_doing_nothing_earns_no_partial_credit_on_a_customer_with_many_invoices():
    task = {**TASK, "expected_residual": {"1": "0.00", "2": "5", "3": "7"},
            "invoice_names": {"1": "INV/1", "2": "INV/2", "3": "INV/3"}}
    before = _snap({1: "100", 2: "5", 3: "7", 9: "-100"})
    s = check.score(before, _snap({1: "100", 2: "5", 3: "7", 9: "-100"}), task, False)
    assert s.reward == 0.0


def test_partial_credit_only_for_moving_the_right_invoices():
    task = {**TASK, "expected_residual": {"1": "0.00", "2": "0.00", "3": "7"},
            "invoice_names": {"1": "INV/1", "2": "INV/2", "3": "INV/3"}}
    before = _snap({1: "100", 2: "5", 3: "7", 9: "-105"})
    half = check.score(before, _snap({1: "0", 2: "5", 3: "7", 9: "-5"}), task, False)
    assert half.reward == 0.25
    wrong_one = check.score(before, _snap({1: "0", 2: "5", 3: "2", 9: "0"}), task, False)
    assert wrong_one.reward == 0.0


def test_a_loosely_written_reference_still_names_the_invoice():
    for memo in ["Payment for INV 2026 00002", "inv/2026/2", "inv-2026-00002", "Re INV2026-002 thanks"]:
        hits, misses = policy.named_invoices(memo, INV)
        assert [i.id for i in hits] == [2] and not misses, memo
    out = policy.expected(D("40.00"), "for inv 2026 2", INV)
    assert out.residual_after == {1: D("100.00"), 2: D("210.00"), 3: D("40.00")}


def test_a_loose_reference_to_no_open_invoice_escalates():
    assert policy.expected(D("40.00"), "for inv 2026 99", INV).escalate
    assert policy.expected(D("40.00"), "for INV-2025-00002", INV).escalate  # another year


def test_short_payment_on_two_named_invoices_goes_in_the_order_named():
    # INV 2 is named first although INV 1 is older: 2 in full, then the rest to 1.
    out = policy.expected(D("300.00"), "INV/2026/00002 and INV/2026/00001", INV)
    assert out.residual_after == {1: D("50.00"), 2: D("0.00"), 3: D("40.00")}
    assert [inv for _, inv in out.steps] == [2, 1]


def test_mixed_reference_to_an_open_and_a_paid_invoice_escalates():
    assert policy.expected(D("100.00"), "INV/2026/00001 and INV/2026/00050", INV).escalate


def test_several_items_are_handled_in_order_on_the_balances_left():
    # The credit note takes 60 off INV 1, leaving 40, a tie with INV 3: the payment of 40 must escalate.
    tie = policy.expected_many([(D("60.00"), "Credit for INV/2026/00001"), (D("40.00"), "ACH")], INV)
    assert tie.escalate
    # The credit note takes 25 off INV 1; the payment of 250 then matches INV 2 exactly.
    out = policy.expected_many([(D("25.00"), "Credit for INV/2026/00001"), (D("250.00"), "ACH")], INV)
    assert not out.escalate
    assert out.residual_after == {1: D("75.00"), 2: D("0.00"), 3: D("40.00")}
    assert list(out.steps) == [(0, 1), (1, 2)]


def test_one_item_needing_escalation_stops_them_all():
    out = policy.expected_many([(D("25.00"), "Credit for INV/2026/00001"), (D("900.00"), "ACH")], INV)
    assert out.escalate and out.reason.startswith("item 2")
    assert out.residual_after == {1: D("100.00"), 2: D("250.00"), 3: D("40.00")}
    assert out.steps == ()


def test_an_invoice_paid_by_the_first_item_is_closed_for_the_second():
    # The first payment clears INV 3 (40); the second, generic and 90, goes oldest first over what is still open.
    out = policy.expected_many([(D("40.00"), "for INV/2026/00003"), (D("90.00"), "ACH")], INV)
    assert out.residual_after == {1: D("10.00"), 2: D("250.00"), 3: D("0.00")}
    assert list(out.steps) == [(0, 3), (1, 1)]
