"""Against the episode Odoo server. Set VOIGHT_LIVE=1 (CI does, after clearledger.demo and voight freeze)."""

import os

import pytest

from voight import check, env, episode, scripted, tasks

pytestmark = pytest.mark.skipif(os.environ.get("VOIGHT_LIVE") != "1", reason="needs the episode Odoo server")
SLOT = int(os.environ.get("VOIGHT_TEST_SLOT", "5"))


@pytest.fixture(scope="module", autouse=True)
def server():
    env.start_server()


def _one_of_each(split="train"):
    picked = {}
    for t in tasks.load(split):
        picked.setdefault(t["kind"], t)
    return list(picked.values())


@pytest.mark.parametrize("task", _one_of_each(), ids=lambda t: t["kind"])
def test_oracle_solves_every_kind(task):
    r = episode.run(task, scripted.oracle, slot=SLOT)
    assert r["score"]["reward"] == 1.0, r["score"]["problems"]


def test_claiming_success_scores_zero():
    t = next(t for t in tasks.load("train") if not t["escalate"])
    assert episode.run(t, scripted.claims_success, slot=SLOT)["score"]["reward"] == 0.0


def test_guard_catches_books_changed_outside_the_task():
    """Does the job right, then also reopens another customer's invoice: the guard must fail it."""
    t = next(t for t in tasks.load("train") if t["kind"] == "exact")
    other = next(x for x in tasks.load("dev"))
    copy = env.Copy(SLOT)
    try:
        moves = tasks.plant(copy.odoo, t)
        with copy.conn() as conn:
            before = check.snapshot(conn)
        from voight.tools import Tools
        tools = Tools(copy.odoo, t["partner_id"])
        credit = tools.call("unapplied", {})["unapplied"][0]["credit_id"]
        name = next(t["invoice_names"][k] for k, v in t["expected_residual"].items() if v == "0.00")
        assert tools.call("apply", {"credit_id": credit, "invoice": name})["applied"]
        copy.odoo.execute("account.move", "button_draft", [int(next(iter(other["expected_residual"])))])
        with copy.conn() as conn:
            after = check.snapshot(conn)
        s = check.score(before, after, {**t, "credit_move_ids": moves}, False)
        assert s.balances_right == s.balances_total  # the job itself was done
        assert not s.guard_ok and s.reward == 0.0
    finally:
        copy.drop()
