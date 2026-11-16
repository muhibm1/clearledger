-- The finance tables add up to the same billed total as the source invoices dated on or before as_of.
with src as (
    select coalesce(sum(amount), 0) as billed from {{ ref('stg_invoices') }}
    where state = 'posted' and move_type in ('out_invoice', 'out_refund')
      and (invoice_date is null or invoice_date <= '{{ var("as_of") }}'::date)
),
mart as (select coalesce(sum(invoiced), 0) as billed from {{ ref('revenue_by_customer') }})
select src.billed, mart.billed from src, mart where abs(src.billed - mart.billed) > 0.01
