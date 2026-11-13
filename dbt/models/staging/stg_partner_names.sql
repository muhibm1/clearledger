-- Every name each Odoo partner has had, from every saved version, with the newest marked current.
-- A renamed customer stays findable under the name people used before.
with v as (
    select odoo_id as partner_id, payload->>'name' as partner_name,
        min(write_date) as first_seen, max(write_date) as last_seen
    from {{ source('odoo', 'res_partner') }}
    where payload->>'name' is not null
    group by odoo_id, payload->>'name'
)
select partner_id, partner_name, first_seen, last_seen,
    last_seen = max(last_seen) over (partition by partner_id) as is_current
from v
