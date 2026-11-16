-- Posted customer payments (and refunds paid out), each credited to the customer whose invoices it
-- settles. Cash is attributed to the owner of the invoices it was reconciled to, not to whoever
-- sent it: a parent company paying its subsidiary's invoices is cash for the subsidiary, not credit
-- on the parent. A payment matched to no invoice, or to invoices of several customers, stays with
-- the payer. Amounts are signed in the company currency: a refund paid out is negative.
-- possible_duplicate_of is set on a payment matched to nothing that repeats the amount of another
-- payment from the same customer within two weeks: likely sent twice, not a credit to spend.
with settles as (
    select p.payment_id, count(distinct c.customer_key) as owners, min(c.customer_key) as owner_key
    from {{ ref('stg_payments') }} p
    cross join lateral unnest(p.invoice_ids) as r(invoice_id)
    join {{ ref('stg_invoices') }} i on i.invoice_id = r.invoice_id
    join {{ ref('int_customers') }} c on c.partner_id = i.partner_id
    group by p.payment_id
),
base as (
    select p.payment_id, p.payment_ref, p.partner_id as payer_partner_id, payer.customer_key as payer_customer_key,
        case when s.owners = 1 then s.owner_key else payer.customer_key end as customer_key,
        p.payment_date, p.state, p.payment_type, p.currency, p.amount, p.invoice_ids, p.source_version
    from {{ ref('stg_payments') }} p
    join {{ ref('int_customers') }} payer on payer.partner_id = p.partner_id
    left join settles s on s.payment_id = p.payment_id
    where p.state in ('posted', 'paid', 'in_process')
)
select b.*,
    b.customer_key <> b.payer_customer_key as settles_another_customers_invoices,
    case when cardinality(b.invoice_ids) = 0 then (select min(q.payment_id) from base q
     where q.payment_id <> b.payment_id and q.customer_key = b.customer_key and q.amount = b.amount
       and q.payment_type = b.payment_type and abs(q.payment_date - b.payment_date) <= 14
       and (q.payment_date, q.payment_id) < (b.payment_date, b.payment_id)) end as possible_duplicate_of
from base b
