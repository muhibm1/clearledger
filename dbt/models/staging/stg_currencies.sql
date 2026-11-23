-- Currencies known to Odoo, with their symbol and whether they are in use.
with c as ({{ latest('res_currency', 'res.currency') }})
select
    odoo_id as currency_id,
    payload->>'name' as currency_name,
    {{ txt('symbol') }} as symbol,
    coalesce((payload->>'active')::boolean, true) as is_active,
    write_date as source_version
from c
