-- Source problem: an invoice with money still open whose installment lines are missing from the
-- warehouse, or do not add up to its open amount. Its aging then uses the header due date, which
-- is the LAST installment, so a term with early installments reads as less late than it is.
-- Usually a load that has not yet seen the lines; `clearledger extract --full` fixes that.
{{ config(severity='warn') }}
select d.invoice_id, i.invoice_ref, i.due_date, i.amount_open
from {{ ref('int_invoice_dues') }} d
join {{ ref('int_invoices') }} i using (invoice_id)
where d.basis = 'invoice header' and i.amount_open <> 0
