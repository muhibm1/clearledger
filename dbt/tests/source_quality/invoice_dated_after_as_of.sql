-- Source problem: a posted invoice is dated after the as_of date. The books "as of" a date cannot hold
-- an invoice that does not exist yet, so every model leaves it out of billed, owed and the aging;
-- revenue_by_customer.invoiced_after_as_of shows the amount. Listed so it is not mistaken for a missing invoice.
{{ config(severity='warn') }}
select i.invoice_id, i.invoice_ref, i.partner_id, i.invoice_date, i.amount
from {{ ref('stg_invoices') }} i
where i.state = 'posted' and i.move_type in ('out_invoice', 'out_refund')
  and i.invoice_date > '{{ var("as_of") }}'::date
