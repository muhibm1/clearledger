"""The review step's code: which answers count as company-wide, how a restated answer is checked, and
how caveats are joined to the explanation. The model's part is exercised by the scored runs."""

from analyst import review


def test_a_date_or_status_filter_is_not_an_entity_so_the_figure_is_company_wide():
    sql = ("select sum(amount) from intermediate.int_invoices where invoice_date >= '2026-01-01' "
           "and move_type = 'out_invoice'")
    assert review._company_wide(sql, [{"sum": 1}])


def test_a_name_or_id_filter_makes_the_figure_about_one_entity():
    assert not review._company_wide("select open from marts.revenue_by_customer "
                                    "where customer_name ilike '%fernhill%'", [{"open": 1}])
    assert not review._company_wide("select sum(amount) from intermediate.int_invoices where deal_id = 11",
                                    [{"sum": 1}])


def test_many_rows_are_company_wide_even_with_a_filter():
    rows = [{"customer_name": f"C{i}"} for i in range(8)]
    assert review._company_wide("select * from x where customer_name ilike '%a%'", rows)


def test_a_restated_answer_must_keep_the_books_figure():
    assert review.revised(34, "34 on the books; 33 excluding the test account") is not None
    assert review.revised(2931235.04, "2,931,235.04 on the books; 2,920,886.04 without it") is not None
    assert review.revised(34, "33 real customers") is None
    assert review.revised(34, "") is None


def test_caveats_follow_the_first_sentence_and_stop_before_they_run_long():
    out = review.merge("Total owed is 473,010.88. It comes from the open column.",
                       ["Includes a likely duplicate INV/2026/00118 (11,596.20).", "x" * 600])
    assert out.startswith("Total owed is 473,010.88. Caveat: Includes a likely duplicate")
    assert "xxxx" not in out
    assert review.merge("Plain answer.", []) == "Plain answer."
