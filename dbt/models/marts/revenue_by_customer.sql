-- What each customer was billed and has paid, in USD, with the invoices and
-- payments behind every figure. paid is what the invoices show as settled; cash_received is the money
-- that actually came in, net of refunds paid back (cash_refunded shows those separately).
with inv as (select * from {{ ref('int_invoices') }}),
pay as (
    select customer_key, payment_id, payment_type, amount from {{ ref('int_payments') }}
)
select
    inv.customer_key,
    max(inv.customer_name) as customer_name,
    sum(inv.amount) as invoiced,
    sum(inv.amount_paid) as paid,
    coalesce((select sum(amount) from pay where pay.customer_key = inv.customer_key), 0) as cash_received,
    coalesce((select -sum(amount) from pay where pay.customer_key = inv.customer_key
              and pay.payment_type = 'outbound'), 0) as cash_refunded,
    sum(inv.amount_open) as open,
    count(*) as invoice_count,
    array_agg(inv.invoice_id order by inv.invoice_id) as source_invoice_ids,
    (select array_agg(payment_id order by payment_id) from pay where pay.customer_key = inv.customer_key) as source_payment_ids
from inv
group by inv.customer_key
