-- One customer per real-world company. Odoo can hold the same company twice
-- (a second rep, a new spelling, "Inc." added), so partners are grouped by a
-- normalized email or a normalized name, and the oldest record is the key.
with p as (
    select s.*,
        coalesce(s.commercial_partner_id, s.partner_id) as company_id,
        trim(regexp_replace(regexp_replace(lower(s.partner_name), '[^a-z0-9 ]', ' ', 'g'),
             '\m(inc|llc|ltd|co|corp|corporation|company)\M', '', 'g')) as name_norm
    from {{ ref('stg_partners') }} s
),
companies as (
    select partner_id as company_id, partner_name, email,
        regexp_replace(name_norm, '\s+', ' ', 'g') as name_norm
    from p where partner_id = company_id
),
keyed as (
    select c.*,
        least(
            -- Same name is the same company only when the emails do not disagree: two
            -- restaurants called "Rustic Table" and "Rustic Table LLC" with different
            -- addresses stay two customers (and duplicate_customers lists them for a person).
            min(company_id) over (partition by name_norm, coalesce(email, '')),
            -- A missing email is not a match: Postgres would put every partner
            -- without one in the same window and merge them. A shared email alone is not
            -- a match either (a bookkeeper's mailbox serves several clients): the names must
            -- also start the same way.
            case when email is null then company_id
                 else min(company_id) over (partition by email, split_part(name_norm, ' ', 1)) end
        ) as customer_key
    from companies c
)
select
    p.partner_id,
    k.customer_key,
    key_row.partner_name as customer_name,
    count(*) over (partition by k.customer_key) > 1 and p.partner_id = p.company_id
        and k.customer_key <> p.partner_id as merged_into_other
from p
join keyed k on k.company_id = p.company_id
join companies key_row on key_row.company_id = k.customer_key
