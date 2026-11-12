-- Source problem 1b: two Odoo companies look alike but the models keep them apart.
-- int_customers merges only partners with the same name and the same (or no) email, or the same
-- email and the same first word. Everything else that is close is listed here for a person: the same
-- name with different emails (two branches, or one company entered twice?) and one email shared by
-- companies with different names (a bookkeeper's mailbox, or one company renamed?).
{{ config(severity='warn') }}
with c as (
    select p.partner_id, p.partner_name, p.email,
        trim(regexp_replace(regexp_replace(lower(p.partner_name), '[^a-z0-9 ]', ' ', 'g'),
             '\m(inc|llc|ltd|co|corp|corporation|company)\M', '', 'g')) as name_norm,
        k.customer_key
    from {{ ref('stg_partners') }} p
    join {{ ref('int_customers') }} k using (partner_id)
    where p.partner_id = coalesce(p.commercial_partner_id, p.partner_id)
)
select a.partner_id as partner_a, b.partner_id as partner_b, a.partner_name as name_a, b.partner_name as name_b,
    case when regexp_replace(a.name_norm, '\s+', ' ', 'g') = regexp_replace(b.name_norm, '\s+', ' ', 'g')
         then 'same name, different email' else 'same email, different names' end as why
from c a join c b on a.partner_id < b.partner_id and a.customer_key <> b.customer_key
where regexp_replace(a.name_norm, '\s+', ' ', 'g') = regexp_replace(b.name_norm, '\s+', ' ', 'g')
   or (a.email is not null and a.email = b.email)
