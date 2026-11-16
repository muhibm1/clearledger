-- Every won deal, followed through order, invoice and payment.
-- ordered is the value of its confirmed orders; a deal whose only orders were cancelled is
-- 'order cancelled' (its invoices and refunds still show), not 'won, no order'. A deal paid in full on
-- less than it ordered is 'paid, order not fully invoiced': not_yet_invoiced says how much is left.
-- possible_unlinked_invoice_ids lists posted invoices of the same customer that no order lists and
-- that fit in what is not yet invoiced: a rebill or a hand-made invoice for this order, to be linked
-- in Odoo by a person. They are not counted in invoiced.
with won as (select * from {{ ref('stg_deals') }} where is_won),
orders as (
    select deal_id,
        array_agg(order_id order by order_id) filter (where state in ('sale', 'done')) as order_ids,
        sum(amount_total) filter (where state in ('sale', 'done')) as ordered,
        array_agg(order_id order by order_id) filter (where state = 'cancel') as cancelled_order_ids,
        min(order_date) as first_order_date
    from {{ ref('stg_orders') }} group by deal_id
),
inv as (
    select deal_id, array_agg(invoice_id order by invoice_id) as invoice_ids,
        sum(amount) as invoiced, sum(amount_paid) as paid, sum(amount_open) as open
    from {{ ref('int_invoices') }} where deal_id is not null group by deal_id
),
cancelled_invoices as (
    select o.deal_id, array_agg(distinct i.invoice_id order by i.invoice_id) as invoice_ids
    from {{ ref('stg_orders') }} o
    cross join lateral unnest(o.invoice_ids) as r(invoice_id)
    join {{ ref('stg_invoices') }} i on i.invoice_id = r.invoice_id
    where i.state = 'cancel' group by o.deal_id
),
base as (
    select w.deal_id, w.deal_name, c.customer_key, c.customer_name, w.expected_revenue,
        coalesce(o.ordered, 0) as ordered, coalesce(i.invoiced, 0) as invoiced,
        coalesce(i.paid, 0) as paid, coalesce(i.open, 0) as open,
        o.order_ids, o.cancelled_order_ids, o.first_order_date, i.deal_id as invoiced_deal, i.invoice_ids
    from won w
    left join {{ ref('int_customers') }} c on c.partner_id = w.partner_id
    left join orders o on o.deal_id = w.deal_id
    left join inv i on i.deal_id = w.deal_id
),
unlinked as (
    select u.customer_key, u.invoice_id, u.amount, u.invoice_date,
        -- An invoice that fills exactly what one of the customer's deals has left belongs to that deal
        -- alone; only when none fits exactly is it offered to every deal it could fit in.
        exists (select 1 from base b2 where b2.customer_key = u.customer_key
                  and abs(b2.ordered - b2.invoiced - u.amount) <= 0.01) as fits_exactly
    from {{ ref('int_invoices') }} u where u.order_id is null and u.move_type = 'out_invoice' and u.amount > 0
)
select
    b.deal_id, b.deal_name, b.customer_key, b.customer_name, b.expected_revenue,
    b.ordered, b.invoiced, b.paid, b.open,
    greatest(b.ordered - b.invoiced, 0) as not_yet_invoiced,
    case
        when b.order_ids is null and b.cancelled_order_ids is null then 'won, no order'
        when b.order_ids is null then 'order cancelled'
        when b.invoiced_deal is null then 'ordered, not invoiced'
        when b.open = 0 and b.invoiced < b.ordered - 0.01 then 'paid, order not fully invoiced'
        when b.open = 0 then 'paid'
        when b.paid > 0 then 'partly paid'
        else 'invoiced, unpaid'
    end as status,
    coalesce(b.order_ids, '{}') as source_order_ids,
    coalesce(b.cancelled_order_ids, '{}') as source_cancelled_order_ids,
    coalesce(b.invoice_ids, '{}') as source_invoice_ids,
    coalesce(ci.invoice_ids, '{}') as cancelled_invoice_ids,
    coalesce((select array_agg(u.invoice_id order by u.invoice_id) from unlinked u
              where u.customer_key = b.customer_key and b.ordered - b.invoiced > 0.01
                and (abs(b.ordered - b.invoiced - u.amount) <= 0.01
                     or (not u.fits_exactly and u.amount <= b.ordered - b.invoiced))
                and u.invoice_date >= b.first_order_date), '{}') as possible_unlinked_invoice_ids
from base b
left join cancelled_invoices ci on ci.deal_id = b.deal_id
