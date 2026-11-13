-- Every version of every customer invoice the loader has seen, oldest first.
select
    odoo_id as invoice_id,
    write_date as source_version,
    payload->>'state' as state,
    {{ m2o_id('partner_id') }} as partner_id,
    ({{ txt('invoice_date') }})::date as invoice_date,
    ({{ txt('invoice_date_due') }})::date as due_date,
    (payload->>'amount_total_signed')::numeric as amount,
    row_number() over (partition by odoo_id order by write_date) as version_no
from {{ source('odoo', 'account_move') }}
where payload->>'move_type' in ('out_invoice', 'out_refund')
