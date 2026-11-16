-- Source problem 2: an invoice was posted, then reopened and changed: its amount, its customer, its
-- due date or its invoice date. Totals use the newest version; the first posted terms are kept in
-- int_invoice_changes, and raw keeps every version.
{{ config(severity='warn') }}
select c.invoice_id, c.changed_fields,
    array_agg(v.amount order by v.version_no) as posted_amounts,
    array_agg(v.due_date order by v.version_no) as posted_due_dates,
    array_agg(v.partner_id order by v.version_no) as posted_partners,
    array_agg(v.source_version order by v.version_no) as versions
from {{ ref('int_invoice_changes') }} c
join {{ ref('stg_invoice_versions') }} v on v.invoice_id = c.invoice_id and v.state = 'posted'
group by c.invoice_id, c.changed_fields
