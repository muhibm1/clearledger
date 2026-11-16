-- When each posted invoice's money falls due: one row per installment, with what is still open on
-- it. Odoo's own installment lines are used when they add up to the invoice's open amount; an
-- invoice whose lines are missing or disagree (not loaded yet, or an edit not picked up) falls
-- back to one row on its header due date, so no open dollar ever drops out of the aging.
with inv as (
    select invoice_id, due_date, amount_open from {{ ref('stg_invoices') }}
    where state = 'posted' and move_type in ('out_invoice', 'out_refund')
),
line_sums as (
    select invoice_id, sum(amount_open) as amount_open from {{ ref('stg_invoice_dues') }} group by invoice_id
),
usable as (
    select i.invoice_id from inv i join line_sums s using (invoice_id)
    where abs(s.amount_open - i.amount_open) <= 0.01
)
select d.invoice_id, d.due_date, d.amount_open, 'installment' as basis
from {{ ref('stg_invoice_dues') }} d where d.invoice_id in (select invoice_id from usable)
union all
select i.invoice_id, i.due_date, i.amount_open, 'invoice header' as basis
from inv i where i.invoice_id not in (select invoice_id from usable)
