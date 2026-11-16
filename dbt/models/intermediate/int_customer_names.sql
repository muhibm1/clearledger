-- Every name a customer has been known by: the names of each Odoo partner merged into it, current
-- and former. Look a customer up here when the name asked for may be an old spelling.
select c.customer_key, n.partner_id, n.partner_name as name, n.is_current,
    n.is_current and n.partner_name = c.customer_name as is_customer_name
from {{ ref('stg_partner_names') }} n
join {{ ref('int_customers') }} c on c.partner_id = n.partner_id
