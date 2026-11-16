-- Source problem: a won deal's order is not fully invoiced and the books hold a sign of why: an invoice
-- on the order that was cancelled, or a posted invoice for the same customer that no order lists
-- (a rebill or a hand-made invoice). deal_to_cash keeps the deal as it stands; a person links the
-- invoice to the order in Odoo, or bills the rest.
{{ config(severity='warn') }}
select deal_id, deal_name, customer_name, status, ordered, invoiced, not_yet_invoiced,
    cancelled_invoice_ids, possible_unlinked_invoice_ids
from {{ ref('deal_to_cash') }}
where status in ('ordered, not invoiced', 'paid, order not fully invoiced', 'partly paid', 'invoiced, unpaid')
  and not_yet_invoiced > 0.01
  and (cardinality(cancelled_invoice_ids) > 0 or cardinality(possible_unlinked_invoice_ids) > 0)
