-- Source problem: a payment matched to no invoice repeats the amount of another payment from the same
-- customer within two weeks. Probably sent twice. The collections list reports it as a possible
-- duplicate, not as credit the customer can spend; a person confirms and refunds or applies it.
{{ config(severity='warn') }}
select p.payment_id, p.payment_ref, k.customer_name, p.payment_date, p.amount, p.possible_duplicate_of
from {{ ref('int_payments') }} p
join {{ ref('int_customers') }} k on k.partner_id = p.payer_partner_id
where p.possible_duplicate_of is not null
