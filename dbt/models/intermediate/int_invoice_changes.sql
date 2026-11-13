-- Posted invoices whose amount, customer, due date or invoice date changed after they were first
-- posted (reopened and edited, re-pointed to another customer, or given a new due date). The newest
-- version is what every total uses; this keeps what the invoice first said, so a collections step
-- that depends on the original terms can be held for a person.
with posted as (
    select * from {{ ref('stg_invoice_versions') }} where state = 'posted'
),
changed as (
    select invoice_id,
        array_remove(array[
            case when count(distinct amount) > 1 then 'amount' end,
            case when count(distinct partner_id) > 1 then 'customer' end,
            case when count(distinct due_date) > 1 then 'due date' end,
            case when count(distinct invoice_date) > 1 then 'invoice date' end], null) as changed_fields,
        count(*) as posted_versions
    from posted group by invoice_id
    having count(distinct (amount, partner_id, due_date, invoice_date)) > 1
),
first_posted as (
    select distinct on (invoice_id) * from posted order by invoice_id, version_no
)
select
    c.invoice_id, c.changed_fields, c.posted_versions,
    f.amount as first_posted_amount, f.due_date as first_posted_due_date,
    f.invoice_date as first_posted_invoice_date, f.partner_id as first_posted_partner_id,
    k.customer_name as first_posted_customer, f.source_version as first_posted_at
from changed c
join first_posted f using (invoice_id)
left join {{ ref('int_customers') }} k on k.partner_id = f.partner_id
