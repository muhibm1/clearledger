-- Sales orders with their amount in USD. ordered is in USD like every other amount: an order in another currency is converted at the rate its own
-- invoices used (so a deal is compared like for like), or at the currency's usual rate when it has none.
with order_rates as (
    select order_id, sum(amount) / nullif(sum(amount_in_invoice_currency), 0) as usd_per_unit
    from {{ ref('int_invoices') }} where order_id is not null and amount_in_invoice_currency <> 0 group by order_id
),
currency_rates as (
    select currency, percentile_cont(0.5) within group (order by amount / amount_in_invoice_currency) as usd_per_unit
    from {{ ref('int_invoices') }} where amount_in_invoice_currency <> 0 group by currency
)
select o.*, o.amount_total * case when o.currency = '{{ var("company_currency") }}' then 1
    else coalesce(r.usd_per_unit, cr.usd_per_unit::numeric, 1) end as amount_usd
from {{ ref('stg_orders') }} o
left join order_rates r on r.order_id = o.order_id
left join currency_rates cr on cr.currency = o.currency

