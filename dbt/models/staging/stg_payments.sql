with p as ({{ latest('account_payment', 'account.payment') }})
select
    odoo_id as payment_id,
    payload->>'name' as payment_ref,
    {{ m2o_id('partner_id') }} as partner_id,
    (payload->>'date')::date as payment_date,
    payload->>'state' as state,
    payload->>'payment_type' as payment_type,
    payload->>'partner_type' as partner_type,
    {{ m2o_name('currency_id') }} as currency,
    (payload->>'amount_company_currency_signed')::numeric as amount,
    -- The amount as paid, in the payment's own currency (currency), unsigned as Odoo keeps it.
    (payload->>'amount')::numeric as amount_in_payment_currency,
    {{ txt('ref') }} as memo,
    array(select jsonb_array_elements_text(payload->'reconciled_invoice_ids')::int) as invoice_ids,
    write_date as source_version
from p
