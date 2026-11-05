"""The allowlist: Remit's Odoo doors cannot make any call outside their lists."""

import datetime as dt

import pytest

from remit.odoo_io import READS, Reader, Refused, Writer

from .helpers import FakeOdoo


def test_writer_has_exactly_three_writes_and_no_generic_execute():
    public = sorted(n for n in dir(Writer) if not n.startswith("_"))
    assert public == ["flag_for_person", "post_reminder", "schedule_follow_up"]


def test_writes_reach_only_message_post_and_activity_schedule():
    o = FakeOdoo()
    w = Writer(o)
    w.post_reminder(5, "Subject", "Body <b>", "[remit:p-1]")
    w.schedule_follow_up(5, dt.date(2026, 11, 3), "Call", "[remit:p-2]")
    w.flag_for_person(5, dt.date(2026, 10, 27), "Look", "[remit:p-3]")
    assert {c for c in o.calls} == {("account.move", "message_post"), ("account.move", "activity_schedule")}
    assert o.messages[0][1].startswith("Reminder sent by Remit [remit:p-1]")


def test_reader_refuses_anything_off_its_list():
    r = Reader(FakeOdoo())
    with pytest.raises(Refused):
        r._call("account.move", "write", [1], {"amount_residual": 0})
    with pytest.raises(Refused):
        r._call("account.move", "unlink", [1])
    assert all(m in ("read", "search_count") for _, m in READS)


def test_reader_reads_tags_and_notes():
    o = FakeOdoo(tags={7: [3]}, categories={3: "Collections hold: dispute"}, notes={1: "<p>Net 30</p>"})
    r = Reader(o)
    assert r.customer_tags([7]) == ["Collections hold: dispute"]
    assert r.invoice_notes([1]) == {1: "<p>Net 30</p>"}
