-- Every dollar still owed shows up in exactly one aging column: a bucket, the open credit notes, or
-- the rounding residue; and the columns add up to what the customer owes.
select r.customer_key, r.open, a.total_open
from {{ ref('revenue_by_customer') }} r
left join {{ ref('receivables_aging') }} a using (customer_key)
where abs(r.open - coalesce(a.total_open, 0)) > 0.01
   or abs(coalesce(a.total_open, 0) - (coalesce(a.not_yet_due, 0) + coalesce(a.late_1_30, 0)
       + coalesce(a.late_31_60, 0) + coalesce(a.late_61_90, 0) + coalesce(a.late_over_90, 0)
       + coalesce(a.open_credit_notes, 0) + coalesce(a.rounding_residue, 0))) > 0.01
