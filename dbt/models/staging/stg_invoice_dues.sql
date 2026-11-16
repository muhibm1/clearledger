-- Installments of customer invoices: one row per receivable line, with its own due date and open
-- amount. An invoice's header carries only the last due date, so a retainage or deposit term
-- (90% due in 60 days, 10% in 240) looks not yet due until its very last installment.
with l as ({{ latest('account_move_line', 'account.move.line') }})
select
    odoo_id as line_id,
    {{ m2o_id('move_id') }} as invoice_id,
    ({{ txt('date_maturity') }})::date as due_date,
    (payload->>'balance')::numeric as amount,
    (payload->>'amount_residual')::numeric as amount_open,
    write_date as source_version
from l
where payload->>'display_type' = 'payment_term' and payload->>'parent_state' = 'posted'
