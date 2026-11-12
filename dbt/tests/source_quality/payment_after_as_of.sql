-- Source problem: a payment is dated after the as_of date. The marts use balances as Odoo holds them
-- now, so an invoice settled after as_of shows as settled in an aging that says "as of" an earlier day.
-- Listed so the aging is not read as a historical snapshot.
{{ config(severity='warn') }}
select payment_id, payment_ref, partner_id, payment_date, amount
from {{ ref('stg_payments') }}
where state in ('posted', 'paid', 'in_process') and payment_type = 'inbound'
  and payment_date > '{{ var("as_of") }}'::date
