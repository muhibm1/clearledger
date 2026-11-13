-- Money still owed, per customer, by how late it is as of the as_of date. Each installment of an
-- invoice is aged by its own due date (a retainage term has a late installment and a later one), so
-- a part-late invoice is split across buckets. Open credit notes are not aged (a credit has no due
-- date that makes it late); they sit in open_credit_notes. Residues under the materiality floor
-- (rounding, exchange differences) are not late money; they sit in rounding_residue.
with open_dues as (
    select i.customer_key, i.customer_name, i.invoice_id, i.move_type, d.due_date, d.amount_open,
        '{{ var("as_of") }}'::date - d.due_date as days_late
    from {{ ref('int_invoice_dues') }} d
    join {{ ref('int_invoices') }} i using (invoice_id)
    where d.amount_open <> 0
),
material as (
    select *, (move_type = 'out_invoice' and abs(amount_open) >= {{ var("materiality") }}) as is_aged
    from open_dues
)
select
    customer_key,
    max(customer_name) as customer_name,
    sum(amount_open) filter (where is_aged and days_late <= 0) as not_yet_due,
    sum(amount_open) filter (where is_aged and days_late between 1 and 30) as late_1_30,
    sum(amount_open) filter (where is_aged and days_late between 31 and 60) as late_31_60,
    sum(amount_open) filter (where is_aged and days_late between 61 and 90) as late_61_90,
    sum(amount_open) filter (where is_aged and days_late > 90) as late_over_90,
    sum(amount_open) filter (where move_type = 'out_refund') as open_credit_notes,
    sum(amount_open) filter (where move_type = 'out_invoice' and not is_aged) as rounding_residue,
    sum(amount_open) as total_open,
    array_agg(distinct invoice_id order by invoice_id) as source_invoice_ids
from material
group by customer_key
