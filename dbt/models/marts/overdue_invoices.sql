-- Remit's work list: every customer invoice with an installment still owed that is due within 3 days
-- or already late, with what the collections playbook needs to decide the next step.
-- One row per invoice. due_date and days_past_due are those of its oldest open installment due by
-- then (the header's due date is only the last installment), and amount_due_now_usd is what is open on
-- the installments due by then; amount_open_usd is everything still open on the invoice. Credit a customer could use against it (open credit notes
-- and payments not matched to any invoice) is shown per customer, because Odoo
-- has not yet said which invoice it pays. An invoice whose amount, customer or due date changed after
-- posting is flagged, with what it first said (first_posted_*), so the playbook can hold it.
with due_now as (
    select invoice_id, min(due_date) as first_due_date, sum(amount_open) as amount_due_now
    from {{ ref('int_invoice_dues') }}
    where amount_open > 0 and due_date <= '{{ var("as_of") }}'::date + 3
    group by invoice_id
),
inv as (
    select i.*, n.first_due_date, n.amount_due_now
    from {{ ref('int_invoices') }} i
    join due_now n using (invoice_id)
    where i.move_type = 'out_invoice' and i.amount_open > 0 and n.amount_due_now >= {{ var("materiality") }}
),
credit_notes as (
    select customer_key, -sum(amount_open) as amount
    from {{ ref('int_invoices') }}
    where move_type = 'out_refund' and amount_open < 0
    group by customer_key
),
-- Cash a customer sent that no invoice has absorbed: payments matched to nothing at all, and the
-- part of an overpayment left after the invoices it settled. Cash received minus what the customer's
-- invoices show as settled, kept only above one dollar so currency rounding is not read as credit.
cash as (
    select c.customer_key, sum(p.amount) as amount
    from {{ ref('stg_payments') }} p
    join {{ ref('int_customers') }} c on c.partner_id = p.partner_id
    where p.state in ('posted', 'paid', 'in_process') and p.payment_type = 'inbound'
    group by c.customer_key
),
settled as (
    select customer_key, sum(amount_paid) as amount
    from {{ ref('int_invoices') }} where move_type = 'out_invoice'
    group by customer_key
),
unmatched_payments as (
    select cash.customer_key, cash.amount - coalesce(settled.amount, 0) as amount
    from cash left join settled using (customer_key)
    where cash.amount - coalesce(settled.amount, 0) >= 1
),
partners_per_customer as (
    select customer_key, count(*) as n from {{ ref('int_customers') }}
    where partner_id in (select partner_id from {{ ref('stg_partners') }}
                         where partner_id = coalesce(commercial_partner_id, partner_id))
    group by customer_key
)
select
    i.invoice_id, i.invoice_ref, i.customer_key, i.customer_name, i.partner_id,
    i.invoice_date, i.first_due_date as due_date,
    ('{{ var("as_of") }}'::date - i.first_due_date) as days_past_due,
    i.currency,
    i.amount_open_in_invoice_currency as amount_open_invoice_currency,
    i.amount_open as amount_open_usd,
    i.amount_due_now as amount_due_now_usd,
    coalesce(cn.amount, 0) as customer_open_credit_notes_usd,
    coalesce(up.amount, 0) as customer_unmatched_payments_usd,
    i.changed_after_posting,
    i.changed_fields,
    i.first_posted_due_date,
    i.days_past_first_posted_due,
    i.first_posted_customer,
    coalesce(ppc.n, 1) > 1 as customer_has_duplicate_records,
    i.source_ref
from inv i
left join credit_notes cn using (customer_key)
left join unmatched_payments up using (customer_key)
left join partners_per_customer ppc using (customer_key)
