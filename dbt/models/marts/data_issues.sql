-- Everything in the books that looks wrong, odd or contradictory, one row per record, in plain words.
-- The figures in the other marts are what Odoo says; this table says where Odoo itself may be wrong,
-- so an answer built on those figures can carry the caveat. Every detector is general (a date out of
-- order, a rate out of line with its currency, a payer who is not the customer, a record that
-- contradicts the one it links to), not a list of known cases. A row never changes a figure.
--
-- affects: which kinds of figure the issue can distort: billed, open, past_due, cash, deals, counts.
{% set as_of = "'" ~ var('as_of') ~ "'::date" %}
{% set cur = "'" ~ var('company_currency') ~ "'" %}
with inv as (select * from {{ ref('int_invoices') }}),
pay as (select * from {{ ref('stg_payments') }}),
cust as (select distinct customer_key, customer_name from {{ ref('int_customers') }}),
partner_cust as (select partner_id, customer_key, customer_name from {{ ref('int_customers') }}),
pay_inv as (
    select p.payment_id, p.payment_ref, p.partner_id, p.payment_date, p.state, p.amount, p.memo, r.invoice_id
    from pay p cross join lateral unnest(p.invoice_ids) as r(invoice_id)
),
fx as (
    select i.*, abs(i.amount) / nullif(abs(i.amount_in_invoice_currency), 0) as usd_per_unit
    from inv i where i.currency <> {{ cur }} and i.amount_in_invoice_currency <> 0
),
fx_norm as (
    select currency, percentile_cont(0.5) within group (order by usd_per_unit) as usual, count(*) as n
    from fx group by currency
),
books as (select min(invoice_date) as first_invoice_date from inv),
-- Leads are kept out of stg_deals (it holds opportunities only), so read them here.
leads as (
    select l.odoo_id as deal_id, l.payload->>'name' as deal_name,
        (case when jsonb_typeof(l.payload->'partner_id') = 'array' then (l.payload->'partner_id'->>0)::int end) as partner_id,
        (case when jsonb_typeof(l.payload->'stage_id') = 'array' then l.payload->'stage_id'->>1 end) as stage,
        coalesce((l.payload->>'expected_revenue')::numeric, 0) as expected_revenue
    from ({{ latest('crm_lead', 'crm.lead') }}) l
    where l.payload->>'type' = 'lead' and coalesce((l.payload->>'active')::boolean, true)
),
issues as (

-- An invoice due before the date it was issued: its days late are counted from a date that cannot be right.
-- The customer's usual term (from its other invoices) says when it would really have been due.
select 'due_date_before_invoice_date' as issue_type, 'invoice' as entity_kind, i.invoice_ref as entity_ref,
    i.customer_key, i.customer_name, i.invoice_date as event_date, i.amount_open as amount,
    array['past_due'] as affects,
    format('%s (%s) is due %s, before its own invoice date %s; its %s days past due come from that date and are almost certainly a keying error (open %s %s).%s',
        i.invoice_ref, i.customer_name, i.due_date, i.invoice_date, i.days_past_due,
        to_char(i.amount_open, 'FM999,999,990.00'), {{ cur }},
        coalesce(format(' On the usual %s-day term it would be due %s, %s.', t.term, i.invoice_date + t.term,
            case when {{ as_of }} > i.invoice_date + t.term then ({{ as_of }} - (i.invoice_date + t.term)) || ' days past due'
                 else 'not yet due' end), '')) as detail
from inv i
left join lateral (
    select coalesce(
        (select percentile_disc(0.5) within group (order by o.due_date - o.invoice_date)
         from inv o where o.customer_key = i.customer_key and o.invoice_id <> i.invoice_id and o.due_date >= o.invoice_date),
        (select percentile_disc(0.5) within group (order by o.due_date - o.invoice_date)
         from inv o where o.move_type = 'out_invoice' and o.due_date >= o.invoice_date)) as term
) t on true
where i.due_date < i.invoice_date

union all
-- Due far later than any normal term.
select 'due_date_far_out', 'invoice', i.invoice_ref, i.customer_key, i.customer_name, i.invoice_date, i.amount_open,
    array['past_due'],
    format('%s (%s) is due %s, %s days after its invoice date %s; that is far beyond a normal term, so it shows as not late.',
        i.invoice_ref, i.customer_name, i.due_date, i.due_date - i.invoice_date, i.invoice_date)
from inv i where i.move_type = 'out_invoice' and i.due_date - i.invoice_date > 200 and i.amount_open > 0

union all
-- An invoice whose conversion to dollars is far from the rate its currency's other invoices used.
select 'exchange_rate_out_of_line', 'invoice', f.invoice_ref, f.customer_key, f.customer_name, f.invoice_date, f.amount,
    array['billed', 'open', 'cash', 'past_due'],
    format('%s (%s) converts %s %s to %s %s, %s %s per %s, against about %s on the other %s invoices (%s times off); its dollar figure is likely mis-converted and would be about %s at the usual rate. Cash received against it will not match its billed dollars.',
        f.invoice_ref, f.customer_name, to_char(f.amount_in_invoice_currency, 'FM999,999,990.00'), f.currency,
        to_char(f.amount, 'FM999,999,990.00'), {{ cur }}, round(f.usd_per_unit, 4), {{ cur }}, f.currency,
        round(n.usual::numeric, 4), f.currency,
        round(greatest(f.usd_per_unit / n.usual, n.usual / f.usd_per_unit)::numeric, 1),
        to_char(f.amount_in_invoice_currency * n.usual * sign(f.amount) * sign(f.amount_in_invoice_currency), 'FM999,999,990.00'))
from fx f join fx_norm n using (currency)
where n.n >= 3 and (f.usd_per_unit > n.usual * 1.5 or f.usd_per_unit < n.usual / 1.5)

union all
-- Posted invoices for nothing.
select 'zero_value_invoice', 'invoice', i.invoice_ref, i.customer_key, i.customer_name, i.invoice_date, 0,
    array['counts'],
    format('%s (%s, %s) is a posted invoice for 0.00: it counts as an invoice but bills nothing, so it inflates invoice counts and lowers averages.',
        i.invoice_ref, i.customer_name, i.invoice_date)
from inv i where i.move_type = 'out_invoice' and i.amount = 0

union all
-- Tax (VAT, GST, sales tax) inside invoice totals: billed figures include money owed to a tax authority.
select 'tax_inside_total', 'invoice', s.invoice_ref, c.customer_key, c.customer_name, s.invoice_date,
    round(s.amount * s.amount_tax_in_invoice_currency / nullif(s.amount_in_invoice_currency, 0), 2),
    array['billed'],
    format('%s (%s) includes tax of %s %s inside its %s %s total (untaxed %s %s); the billed figure includes that tax, about %s %s.',
        s.invoice_ref, c.customer_name, to_char(s.amount_tax_in_invoice_currency, 'FM999,999,990.00'), s.currency,
        to_char(s.amount_in_invoice_currency, 'FM999,999,990.00'), s.currency,
        to_char(s.amount_untaxed_in_invoice_currency, 'FM999,999,990.00'), s.currency,
        to_char(s.amount * s.amount_tax_in_invoice_currency / nullif(s.amount_in_invoice_currency, 0), 'FM999,999,990.00'), {{ cur }})
from {{ ref('stg_invoices') }} s join partner_cust c on c.partner_id = s.partner_id
where s.state = 'posted' and s.move_type in ('out_invoice', 'out_refund') and coalesce(s.amount_tax_in_invoice_currency, 0) <> 0
  and s.invoice_date <= {{ as_of }}

union all
-- A customer whose name says it is not a real customer.
select 'not_a_real_customer', 'customer', c.customer_name, c.customer_key, c.customer_name, min(i.invoice_date),
    sum(i.amount), array['billed', 'open', 'past_due', 'cash', 'counts'],
    format('"%s" looks like an internal, test or demo account, not a real customer: %s invoice(s) billed %s %s, %s %s still open (%s %s past due), cash %s %s. Totals and customer counts include it. Its invoices: %s.',
        c.customer_name, count(i.invoice_id), to_char(sum(i.amount), 'FM999,999,990.00'), {{ cur }},
        to_char(sum(i.amount_open), 'FM999,999,990.00'), {{ cur }},
        to_char(sum(i.amount_past_due), 'FM999,999,990.00'), {{ cur }},
        to_char(coalesce((select sum(p.amount) from {{ ref('int_payments') }} p where p.customer_key = c.customer_key), 0), 'FM999,999,990.00'), {{ cur }},
        string_agg(i.invoice_ref || ' ' || i.invoice_date || ' ' || to_char(i.amount, 'FM999,999,990.00')
            || case when i.amount_open <> 0 then ' (open ' || to_char(i.amount_open, 'FM999,999,990.00') || ', ' || i.days_past_due || ' days late)' else '' end,
            '; ' order by i.invoice_date))
from cust c left join inv i on i.customer_key = c.customer_key
where c.customer_name ~* '(\m(test|testing|demo|internal|sandbox|dummy|sample|training|staff|employee|do not use|dont use|placeholder|unknown|misc|miscellaneous|cash customer|walk-?in)\M)|^(x|zz|tmp)\M'
group by c.customer_key, c.customer_name

union all
-- Two invoices to the same customer for the same amount close together, one of them made outside any order:
-- likely the same charge billed twice.
select 'possible_duplicate_invoice', 'invoice', b.invoice_ref, b.customer_key, b.customer_name, b.invoice_date, b.amount_open,
    array['open', 'past_due', 'billed', 'counts'],
    format('%s (%s, %s %s, %s, %s) repeats the amount of %s (%s, %s); it looks like the same charge billed twice. %s is %s and %s is %s. Confirm before chasing it; it probably needs a credit note.',
        b.invoice_ref, b.customer_name, to_char(b.amount_in_invoice_currency, 'FM999,999,990.00'), b.currency, b.invoice_date,
        case when b.order_id is null then 'no order' else 'on an order' end,
        a.invoice_ref, a.invoice_date, case when a.order_id is null then 'no order' else 'on an order' end,
        a.invoice_ref, case when a.amount_open = 0 then 'paid' else 'open ' || to_char(a.amount_open, 'FM999,999,990.00') end,
        b.invoice_ref, case when b.amount_open = 0 then 'paid' else 'open ' || to_char(b.amount_open, 'FM999,999,990.00') || ', ' || b.days_past_due || ' days past due' end)
from inv a join inv b on a.customer_key = b.customer_key and a.invoice_id < b.invoice_id
    and a.move_type = 'out_invoice' and b.move_type = 'out_invoice' and a.currency = b.currency
    and a.amount_in_invoice_currency = b.amount_in_invoice_currency and a.amount_in_invoice_currency > 0
    -- Close together (a monthly fee repeats a month apart, which is not a duplicate), or one says it copies the other.
    and (abs(b.invoice_date - a.invoice_date) <= 20 or position(a.invoice_ref in coalesce(b.reference, '')) > 0)
    and (a.order_id is null or b.order_id is null or a.order_id = b.order_id)

union all
-- Unusually large against the customer's own history: a possible extra zero.
select 'amount_out_of_line', 'invoice', i.invoice_ref, i.customer_key, i.customer_name, i.invoice_date, i.amount,
    array['billed', 'open'],
    format('%s (%s) is %s %s, %s times the median of this customer''s other invoices (%s); check it is not a keying error.',
        i.invoice_ref, i.customer_name, to_char(i.amount, 'FM999,999,990.00'), {{ cur }},
        round(i.amount / m.med, 1), to_char(m.med, 'FM999,999,990.00'))
from inv i join (
    select a.invoice_id, percentile_cont(0.5) within group (order by b.amount)::numeric as med, count(*) as n
    from inv a join inv b on b.customer_key = a.customer_key and b.invoice_id <> a.invoice_id and b.amount > 0
    group by a.invoice_id) m on m.invoice_id = i.invoice_id
where m.n >= 3 and i.amount > 8 * m.med and i.amount > 1000

union all
-- Paid part of the way: the open rest may be a dispute, a deduction or a set-off, not money late.
select 'short_paid', 'invoice', i.invoice_ref, i.customer_key, i.customer_name, i.invoice_date, i.amount_open,
    array['open', 'past_due'],
    format('%s (%s) was paid %s of %s %s and %s is still open%s%s. A short payment can be a deduction, a set-off or a dispute; confirm before chasing the rest.',
        i.invoice_ref, i.customer_name, to_char(i.amount_paid, 'FM999,999,990.00'), to_char(i.amount, 'FM999,999,990.00'), {{ cur }},
        to_char(i.amount_open, 'FM999,999,990.00'),
        case when i.days_past_due > 0 then ', ' || i.days_past_due || ' days past due' else '' end,
        coalesce('; payment memo: ' || (select string_agg(distinct pi.memo, ' / ') from pay_inv pi
                  where pi.invoice_id = i.invoice_id and pi.memo is not null and pi.memo <> i.invoice_ref), ''))
from inv i where i.move_type = 'out_invoice' and i.amount_paid > 0.01 and i.amount_open > 1
  and exists (select 1 from pay_inv pi where pi.invoice_id = i.invoice_id)

union all
-- Overpaid invoice (a negative balance on an invoice).
select 'invoice_overpaid', 'invoice', i.invoice_ref, i.customer_key, i.customer_name, i.invoice_date, i.amount_open,
    array['open', 'cash'],
    format('%s (%s) has a negative open balance of %s %s: more was applied to it than it billed.',
        i.invoice_ref, i.customer_name, to_char(i.amount_open, 'FM999,999,990.00'), {{ cur }})
from inv i where i.move_type = 'out_invoice' and i.amount_open < -0.01

union all
-- Changed after it was posted.
select 'invoice_changed_after_posting', 'invoice', i.invoice_ref, i.customer_key, i.customer_name, i.invoice_date, i.amount,
    array['billed', 'open', 'past_due'],
    format('%s (%s) was changed after it was posted (%s)%s%s.', i.invoice_ref, i.customer_name, i.changed_fields,
        coalesce('; it was first posted to ' || i.first_posted_customer, ''),
        coalesce('; first due ' || i.first_posted_due_date || ', which makes it ' || i.days_past_first_posted_due || ' days late', ''))
from inv i where i.changed_after_posting

union all
-- A payment dated before the invoice it pays, or outside the period the books cover.
select 'payment_dated_before_invoice', 'payment', pi.payment_ref, i.customer_key, i.customer_name, pi.payment_date, pi.amount,
    array['cash'],
    format('Payment %s (%s %s) is dated %s, %s days before the invoice it pays, %s dated %s (%s); the payment date is almost certainly mistyped (a wrong year or month), so days-to-pay and cash by period are wrong for it.',
        pi.payment_ref, to_char(pi.amount, 'FM999,999,990.00'), {{ cur }}, pi.payment_date, i.invoice_date - pi.payment_date,
        i.invoice_ref, i.invoice_date, i.customer_name)
from pay_inv pi join inv i on i.invoice_id = pi.invoice_id
where pi.state = 'posted' and pi.payment_date < i.invoice_date

union all
select 'payment_dated_outside_books', 'payment', p.payment_ref, c.customer_key, c.customer_name, p.payment_date, p.amount,
    array['cash'],
    format('Payment %s (%s, %s %s) is dated %s, %s; cash counted by date puts it in the wrong period.',
        p.payment_ref, coalesce(c.customer_name, 'no customer'), to_char(p.amount, 'FM999,999,990.00'), {{ cur }}, p.payment_date,
        case when p.payment_date > {{ as_of }} then 'after the as-of date ' || {{ as_of }}
             else 'before the first invoice in the books (' || b.first_invoice_date || ')' end)
from pay p cross join books b left join partner_cust c on c.partner_id = p.partner_id
where p.state = 'posted' and (p.payment_date < b.first_invoice_date - 31 or p.payment_date > {{ as_of }})

union all
-- Someone other than the customer paid the invoice.
select 'paid_by_another_party', 'payment', pi.payment_ref, i.customer_key, i.customer_name, pi.payment_date, pi.amount,
    array['cash'],
    format('%s (%s) was paid by %s, not by %s itself (payment %s, %s %s, %s). The books do not record a link between the two, so cash "from %s" under its own name is 0.00 while the invoice is settled; the warehouse credits the cash to the invoice''s customer.',
        i.invoice_ref, i.customer_name, payer.customer_name, i.customer_name, pi.payment_ref,
        to_char(pi.amount, 'FM999,999,990.00'), {{ cur }}, pi.payment_date, i.customer_name)
from pay_inv pi join inv i on i.invoice_id = pi.invoice_id
join partner_cust payer on payer.partner_id = pi.partner_id
where pi.state = 'posted' and payer.customer_key <> i.customer_key

union all
-- A payment recorded but never posted: not cash in the books, though it may well have arrived.
select 'payment_not_posted', 'payment', coalesce(p.payment_ref, 'payment id ' || p.payment_id), c.customer_key, c.customer_name,
    p.payment_date, p.amount, array['cash', 'open', 'past_due'],
    format('A %s payment of %s %s from %s dated %s is in state "%s", not posted, so it is not counted as cash and its invoice stays open%s.',
        p.payment_type, to_char(abs(p.amount), 'FM999,999,990.00'), {{ cur }}, coalesce(c.customer_name, 'no customer'), p.payment_date, p.state,
        coalesce(' (open invoices of the same amount: ' || (select string_agg(i.invoice_ref, ', ') from inv i
            where i.customer_key = c.customer_key and abs(i.amount_open - abs(p.amount)) <= 0.01) || ')', ''))
from pay p left join partner_cust c on c.partner_id = p.partner_id
where p.state not in ('posted', 'paid', 'in_process', 'cancel', 'canceled')

union all
select 'payment_cancelled', 'payment', coalesce(p.payment_ref, 'payment id ' || p.payment_id), c.customer_key, c.customer_name,
    p.payment_date, p.amount, array['cash', 'open'],
    format('Payment %s of %s %s from %s dated %s was cancelled; it is not cash in the books.',
        coalesce(p.payment_ref, 'id ' || p.payment_id), to_char(abs(p.amount), 'FM999,999,990.00'), {{ cur }},
        coalesce(c.customer_name, 'no customer'), p.payment_date)
from pay p left join partner_cust c on c.partner_id = p.partner_id
where p.state in ('cancel', 'canceled')

union all
-- Money in with no customer on it.
select 'receipt_without_customer', 'payment', p.payment_ref, null, null, p.payment_date, p.amount,
    array['cash', 'open', 'past_due'],
    format('Receipt %s of %s %s on %s has no customer on it, so no customer''s cash or balance reflects it%s. It is in company-wide cash by date.',
        p.payment_ref, to_char(p.amount, 'FM999,999,990.00'), {{ cur }}, p.payment_date,
        coalesce('; open invoices of exactly that amount: ' || (select string_agg(i.invoice_ref || ' (' || i.customer_name || ')', ', ')
            from inv i where abs(i.amount_open - p.amount) <= 0.01) || ', so it may be that payment but nothing links them', ''))
from pay p where p.partner_id is null and p.state in ('posted', 'paid', 'in_process')

union all
-- Cash matched to no invoice.
select 'payment_not_matched', 'payment', p.payment_ref, c.customer_key, c.customer_name, p.payment_date, p.amount,
    array['cash', 'open'],
    format('Payment %s from %s (%s %s, %s) is not matched to any invoice, so the invoices it may be for still show as open.',
        p.payment_ref, c.customer_name, to_char(p.amount, 'FM999,999,990.00'), {{ cur }}, p.payment_date)
from pay p join partner_cust c on c.partner_id = p.partner_id
where p.state in ('posted', 'paid', 'in_process') and cardinality(p.invoice_ids) = 0 and p.payment_type = 'inbound'

union all
-- An order filed under someone else's deal.
select 'order_on_another_customers_deal', 'deal', d.deal_name, dc.customer_key, dc.customer_name, o.order_date, o.amount_total,
    array['deals', 'billed'],
    format('Order %s (%s %s) for %s is linked to deal "%s", which belongs to %s (stage %s%s). The order and its invoices are %s''s, not %s''s.',
        o.order_ref, to_char(o.amount_total, 'FM999,999,990.00'), o.currency, oc.customer_name, d.deal_name, dc.customer_name, d.stage,
        case when d.is_lost then ', archived as lost' end, oc.customer_name, dc.customer_name)
from {{ ref('stg_orders') }} o join {{ ref('stg_deals') }} d on d.deal_id = o.deal_id
join partner_cust oc on oc.partner_id = o.partner_id join partner_cust dc on dc.partner_id = d.partner_id
where o.state in ('sale', 'done') and oc.customer_key <> dc.customer_key

union all
-- A won deal with nothing behind it, when the same customer has an order on a deal that is not its own.
select 'won_deal_without_order', 'deal', w.deal_name, w.customer_key, w.customer_name, null::date, w.expected_revenue,
    array['deals'],
    format('Won deal "%s" (%s) has no confirmed order%s.', w.deal_name, w.customer_name,
        coalesce('; ' || w.customer_name || '''s order ' || (select string_agg(o.order_ref || ' (' || to_char(o.amount_total, 'FM999,999,990.00') || ')', ', ')
            from {{ ref('stg_orders') }} o join partner_cust oc on oc.partner_id = o.partner_id
            join {{ ref('stg_deals') }} d2 on d2.deal_id = o.deal_id join partner_cust dc on dc.partner_id = d2.partner_id
            where oc.customer_key = w.customer_key and dc.customer_key <> w.customer_key and o.state in ('sale', 'done'))
            || ' sits on another customer''s deal instead', ''))
from {{ ref('deal_to_cash') }} w where w.status = 'won, no order'

union all
-- A deal marked lost (or still open) whose order was confirmed and billed: the CRM and the books disagree.
select 'deal_status_contradicts_sales', 'deal', d.deal_name, c.customer_key, c.customer_name, d.closed_on, s.invoiced,
    array['deals', 'counts'],
    format('Deal "%s" (%s) is %s in the CRM, yet its order %s was confirmed and invoiced %s %s, %s paid. The two records contradict each other.',
        d.deal_name, c.customer_name,
        case when d.is_lost and d.is_won then 'in the ' || d.stage || ' stage but archived as lost'
             when d.is_lost then 'archived as lost' else 'still open (stage ' || d.stage || ')' end,
        s.order_refs, to_char(s.invoiced, 'FM999,999,990.00'), {{ cur }}, to_char(s.paid, 'FM999,999,990.00'))
from {{ ref('stg_deals') }} d join partner_cust c on c.partner_id = d.partner_id
join (select o.deal_id, string_agg(distinct o.order_ref, ', ') as order_refs, sum(i.amount) as invoiced, sum(i.amount_paid) as paid,
             bool_and(oc.customer_key = dc.customer_key) as same_customer
      from {{ ref('stg_orders') }} o join inv i on i.order_id = o.order_id
      join partner_cust oc on oc.partner_id = o.partner_id
      join {{ ref('stg_deals') }} dd on dd.deal_id = o.deal_id join partner_cust dc on dc.partner_id = dd.partner_id
      where o.state in ('sale', 'done') group by o.deal_id) s on s.deal_id = d.deal_id
where (d.is_lost or not d.is_won) and s.same_customer

union all
-- An archived (inactive) customer still being billed or owing money.
select 'archived_customer_active', 'customer', c.customer_name, c.customer_key, c.customer_name, max(i.invoice_date), sum(i.amount_open),
    array['open', 'billed'],
    format('%s is archived in Odoo but has %s invoice(s), the latest %s, with %s %s open.', c.customer_name, count(*), max(i.invoice_date),
        to_char(sum(i.amount_open), 'FM999,999,990.00'), {{ cur }})
from {{ ref('stg_partners') }} p join partner_cust c on c.partner_id = p.partner_id join inv i on i.partner_id = p.partner_id
where not p.is_active group by c.customer_key, c.customer_name

union all
-- One company in two records.
select 'duplicate_customer_record', 'customer', ic.customer_name, ic.customer_key, ic.customer_name, null::date, null::numeric,
    array['counts'],
    format('%s is entered more than once in Odoo (%s); the warehouse merges them into one customer.', ic.customer_name,
        string_agg(p.partner_name, ', '))
from {{ ref('int_customers') }} ic join {{ ref('stg_partners') }} p on p.partner_id = ic.partner_id
group by ic.customer_key, ic.customer_name having count(*) > 1 and count(*) filter (where p.is_company) > 1

union all
-- Posted invoices dated after the as-of date.
select 'invoice_after_as_of', 'invoice', s.invoice_ref, c.customer_key, c.customer_name, s.invoice_date, s.amount,
    array['billed', 'open'],
    format('%s (%s, %s %s) is dated %s, after the as-of date %s; it is left out of billed and open as of that date.',
        s.invoice_ref, c.customer_name, to_char(s.amount, 'FM999,999,990.00'), {{ cur }}, s.invoice_date, {{ as_of }})
from {{ ref('stg_invoices') }} s join partner_cust c on c.partner_id = s.partner_id
where s.state = 'posted' and s.move_type in ('out_invoice', 'out_refund') and s.invoice_date > {{ as_of }}

union all
-- The invoice and the order it bills disagree on who the customer is, the currency, or the dates.
select 'invoice_disagrees_with_order', 'invoice', i.invoice_ref, i.customer_key, i.customer_name, i.invoice_date, i.amount,
    array['billed', 'deals', 'open'],
    format('%s (%s, %s %s, %s) bills order %s, but %s.', i.invoice_ref, i.customer_name, to_char(i.amount, 'FM999,999,990.00'), {{ cur }},
        case when abs(i.amount_open) < 0.01 then 'paid' else 'open ' || to_char(i.amount_open, 'FM999,999,990.00')
             || case when i.days_past_due > 0 then ', ' || i.days_past_due || ' days past due' else ', not yet due' end end,
        o.order_ref,
        concat_ws('; ',
            case when oc.customer_key <> i.customer_key then 'the order is ' || oc.customer_name || '''s, so this balance sits under '
                || i.customer_name || ', not ' || oc.customer_name end,
            case when o.currency <> i.currency then 'the order is in ' || o.currency || ' and the invoice in ' || i.currency
                || ' for the same number, probably a currency keyed wrong on the invoice' end,
            case when i.invoice_date < o.order_date - 1 then 'the invoice is dated ' || i.invoice_date || ', before the order (' || o.order_date
                || '); the order date was probably keyed or changed after billing' end))
from inv i join {{ ref('stg_orders') }} o on o.order_id = i.order_id join partner_cust oc on oc.partner_id = o.partner_id
where oc.customer_key <> i.customer_key or o.currency <> i.currency or i.invoice_date < o.order_date - 1

union all
-- What people typed says something is wrong: a reference or memo that mentions a copy, a duplicate, an error,
-- a dispute, a set-off, a test.
select 'note_flags_a_problem', 'invoice', i.invoice_ref, i.customer_key, i.customer_name, i.invoice_date, i.amount,
    array['billed', 'open', 'past_due', 'counts'],
    format('%s (%s, %s, %s %s, open %s) carries the reference "%s".%s', i.invoice_ref, i.customer_name, i.invoice_date,
        to_char(i.amount, 'FM999,999,990.00'), {{ cur }}, to_char(i.amount_open, 'FM999,999,990.00'), i.reference,
        coalesce(' ' || {{ note_meaning('i.reference') }}, ''))
from inv i
where i.reference ~* '(dup|copy|re-?issu|re-?bill|void|cancel|wrong|error|mistake|correct|test|demo|do not|don''t|disput|hold|offset|set-?off|net of|deduct|short|write.?off|bad debt|refund|reversal|replace|internal|free|trial|sample|estimate|pro-?forma|quot|draft|deposit|\mfees?\M|interest|penalt|surcharge|plan|instal|agreement|grant|not a request|withh|damage)'

union all
select 'note_flags_a_problem', 'payment', p.payment_ref, c.customer_key, coalesce(c.customer_name, 'no customer'), p.payment_date, p.amount,
    array['cash', 'open'],
    format('Payment %s (%s %s, %s, %s) carries the memo "%s".%s', p.payment_ref, p.payment_type, coalesce(c.customer_name, 'no customer'),
        to_char(p.amount, 'FM999,999,990.00'), p.payment_date, p.memo, coalesce(' ' || {{ note_meaning('p.memo') }}, ''))
from pay p left join partner_cust c on c.partner_id = p.partner_id
where p.memo ~* '(dup|twice|again|copy|void|cancel|wrong|error|mistake|correct|test|do not|disput|hold|offset|set-?off|net of|less|deduct|short|partial|write.?off|refund|revers|credit|on behalf|for |advance|deposit|prepay|unapplied|unknown|overpa|plan|instal|agreement|withh|received|only)'
  and p.state <> 'cancel'

union all
-- A payment whose memo names an invoice other than the one it was matched to.
select 'payment_memo_names_other_invoice', 'payment', p.payment_ref, c.customer_key, c.customer_name, p.payment_date, p.amount,
    array['cash', 'open', 'past_due'],
    format('Payment %s (%s %s, %s) says "%s" but is matched to %s; the cash may sit on the wrong invoice.%s',
        p.payment_ref, to_char(p.amount, 'FM999,999,990.00'), {{ cur }}, coalesce(c.customer_name, 'no customer'), p.memo,
        coalesce((select string_agg(i.invoice_ref, ', ') from inv i where i.invoice_id = any(p.invoice_ids)), 'no invoice'),
        coalesce((select string_agg(format(' %s is in state "%s" (%s %s), not a posted invoice, so it is in no billed or open figure although the customer paid it.',
                     s.invoice_ref, s.state, to_char(s.amount, 'FM999,999,990.00'), s.currency), '')
                  from {{ ref('stg_invoices') }} s where s.state <> 'posted' and position(s.invoice_ref in p.memo) > 0), ''))
from pay p left join partner_cust c on c.partner_id = p.partner_id
where p.state in ('posted', 'paid', 'in_process') and p.memo ~ '[A-Z]{2,6}/\d{4}/\d{3,6}'
  -- Some invoice the memo names is not one the payment is matched to.
  and exists (select 1 from regexp_matches(p.memo, '[A-Z]{2,6}/\d{4}/\d{3,6}', 'g') m(r)
              where not exists (select 1 from inv i where i.invoice_id = any(p.invoice_ids) and i.invoice_ref = m.r[1]))

union all
-- Settled without any cash: a credit note, write-off or set-off closed it.
select 'settled_without_payment', 'invoice', i.invoice_ref, i.customer_key, i.customer_name, i.invoice_date, i.amount_paid,
    array['cash', 'open'],
    format('%s (%s) shows %s %s settled, but no customer payment is matched to it: it was closed by a credit note, write-off or set-off, not cash.',
        i.invoice_ref, i.customer_name, to_char(i.amount_paid, 'FM999,999,990.00'), {{ cur }})
from inv i where i.move_type = 'out_invoice' and i.amount_paid > 0.01
  and not exists (select 1 from pay_inv pi where pi.invoice_id = i.invoice_id)

union all
-- A negative invoice instead of a credit note.
select 'negative_invoice', 'invoice', i.invoice_ref, i.customer_key, i.customer_name, i.invoice_date, i.amount,
    array['billed', 'counts', 'open'],
    format('%s (%s) is an invoice for a negative amount (%s %s); a reversal booked as an invoice, not a credit note.',
        i.invoice_ref, i.customer_name, to_char(i.amount, 'FM999,999,990.00'), {{ cur }})
from inv i where i.move_type = 'out_invoice' and i.amount < 0

union all
-- Credit not yet used.
select 'credit_note_unapplied', 'invoice', i.invoice_ref, i.customer_key, i.customer_name, i.invoice_date, i.amount_open,
    array['open', 'cash'],
    format('Credit note %s (%s, %s) still has %s %s unapplied: it lowers what %s owes but no invoice has used it.',
        i.invoice_ref, i.customer_name, i.invoice_date, to_char(i.amount_open, 'FM999,999,990.00'), {{ cur }}, i.customer_name)
from inv i where i.move_type = 'out_refund' and i.amount_open < -1

union all
-- A payment that repeats another one.
select 'possible_duplicate_payment', 'payment', p.payment_ref, p.customer_key, c.customer_name, p.payment_date, p.amount,
    array['cash', 'open'],
    format('Payment %s (%s, %s %s, %s) matches no invoice and repeats payment id %s of the same amount within two weeks: probably sent twice, not credit to spend.',
        p.payment_ref, c.customer_name, to_char(p.amount, 'FM999,999,990.00'), {{ cur }}, p.payment_date, p.possible_duplicate_of)
from {{ ref('int_payments') }} p left join cust c on c.customer_key = p.customer_key where p.possible_duplicate_of is not null

union all
-- Two won deals that look like the same deal.
select 'possible_duplicate_deal', 'deal', b.deal_name, c.customer_key, c.customer_name, b.closed_on, b.expected_revenue,
    array['deals', 'counts'],
    format('Deals "%s" and "%s" (%s) are both %s with the same expected revenue %s; they may be one deal entered twice.',
        a.deal_name, b.deal_name, c.customer_name, case when b.is_won then 'won' else 'open' end, to_char(b.expected_revenue, 'FM999,999,990.00'))
from {{ ref('stg_deals') }} a join {{ ref('stg_deals') }} b on a.deal_id < b.deal_id and a.partner_id = b.partner_id
    and a.expected_revenue = b.expected_revenue and a.expected_revenue > 0 and a.is_won = b.is_won and not a.is_lost and not b.is_lost
    and (lower(a.deal_name) = lower(b.deal_name) or (not a.is_won and not b.is_won))
join partner_cust c on c.partner_id = b.partner_id

union all
-- Deal dates that cannot be right.
select 'deal_dates_out_of_order', 'deal', d.deal_name, c.customer_key, c.customer_name, d.closed_on, d.expected_revenue,
    array['deals'],
    format('Deal "%s" (%s) is closed on %s, %s.', d.deal_name, c.customer_name, d.closed_on,
        'after the as-of date ' || {{ as_of }})
from {{ ref('stg_deals') }} d join partner_cust c on c.partner_id = d.partner_id
where d.closed_on > {{ as_of }}

union all
-- Won with nothing expected, or expected far from what was ordered.
select 'deal_value_out_of_line', 'deal', w.deal_name, w.customer_key, w.customer_name, null::date, w.ordered,
    array['deals'],
    format('Won deal "%s" (%s) expected %s %s but its orders total %s %s.', w.deal_name, w.customer_name,
        to_char(w.expected_revenue, 'FM999,999,990.00'), {{ cur }}, to_char(w.ordered, 'FM999,999,990.00'), {{ cur }})
from {{ ref('deal_to_cash') }} w
where w.ordered > 0 and (w.expected_revenue <= 0 or w.ordered > 5 * w.expected_revenue or w.ordered * 5 < w.expected_revenue)

union all
-- An open opportunity whose value is far out of line with every other deal: a likely unit or keying error.
select 'deal_value_out_of_line', 'deal', d.deal_name, c.customer_key, coalesce(c.customer_name, 'no customer'), null::date, d.expected_revenue,
    array['deals'],
    format('Open deal "%s" (%s) expects %s %s, %s times the median deal (%s); it likely has a wrong unit or extra digits and dominates any pipeline total.',
        d.deal_name, coalesce(c.customer_name, 'no customer'), to_char(d.expected_revenue, 'FM999,999,990.00'), {{ cur }},
        round(d.expected_revenue / m.med), to_char(m.med, 'FM999,999,990.00'))
from {{ ref('stg_deals') }} d left join partner_cust c on c.partner_id = d.partner_id
cross join (select percentile_cont(0.5) within group (order by expected_revenue)::numeric as med from {{ ref('stg_deals') }}
            where expected_revenue > 0) m
where not d.is_won and not d.is_lost and d.expected_revenue > 20 * m.med

union all
-- A lead (not yet a qualified opportunity) carrying expected revenue: it is not pipeline.
select 'lead_not_opportunity', 'deal', d.deal_name, c.customer_key, coalesce(c.customer_name, 'no customer'), null::date, d.expected_revenue,
    array['deals', 'counts'],
    format('"%s" is a lead (record type lead, not an opportunity)%s, expecting %s %s in stage %s; leads are not qualified pipeline, so they are left out of the deals table, pipeline totals and deal counts.',
        d.deal_name, case when c.customer_name is null then ' with no customer' else ' for ' || c.customer_name end,
        to_char(d.expected_revenue, 'FM999,999,990.00'), {{ cur }}, coalesce(d.stage, 'none'))
from leads d left join partner_cust c on c.partner_id = d.partner_id
where d.expected_revenue > 0

union all
-- Billed against an order that was never confirmed (a quotation), linked only by the origin text.
select 'billed_on_unconfirmed_order', 'invoice', i.invoice_ref, i.customer_key, i.customer_name, i.invoice_date, i.amount,
    array['billed', 'deals'],
    format('%s (%s, %s %s, %s) bills order %s, which is still in state "%s" (never confirmed); the invoice is tied to it only by its origin text, not in the order''s invoice list. The deal shows no confirmed order although the job was billed.',
        i.invoice_ref, i.customer_name, to_char(i.amount, 'FM999,999,990.00'), {{ cur }},
        case when i.amount_open = 0 then 'paid in full' else 'open ' || to_char(i.amount_open, 'FM999,999,990.00') end, o.order_ref, o.state)
from inv i join {{ ref('stg_invoices') }} si on si.invoice_id = i.invoice_id
join {{ ref('stg_orders') }} o on o.order_ref = si.invoice_origin
where o.state not in ('sale', 'done') and i.move_type = 'out_invoice'

union all
-- A confirmed order with nothing billed long after the usual gap between order and invoice.
select 'order_not_invoiced', 'deal', o.order_ref, c.customer_key, c.customer_name, o.order_date, o.amount_total,
    array['deals', 'billed'],
    format('Order %s (%s, %s %s) was confirmed on %s, %s days before the as-of date, and has no invoice; orders here are usually invoiced within about %s days, so it is probably unbilled.',
        o.order_ref, c.customer_name, to_char(o.amount_total, 'FM999,999,990.00'), o.currency, o.order_date,
        {{ as_of }} - o.order_date, g.usual)
from {{ ref('stg_orders') }} o join partner_cust c on c.partner_id = o.partner_id
cross join (select greatest(percentile_disc(0.9) within group (order by f.gap), 7) as usual
            from (select o2.order_id, min(i.invoice_date - o2.order_date) as gap
                  from {{ ref('stg_orders') }} o2 join inv i on i.order_id = o2.order_id
                  where i.invoice_date >= o2.order_date and o2.state in ('sale', 'done') group by o2.order_id) f) g
where o.state in ('sale', 'done') and o.amount_total > 0
  and not exists (select 1 from inv i where i.order_id = o.order_id) and cardinality(o.invoice_ids) = 0
  and {{ as_of }} - o.order_date > 2 * g.usual

union all
-- Money paid out with no credit note behind it, or whose memo says money came in.
select 'outbound_payment_unsupported', 'payment', p.payment_ref, c.customer_key, c.customer_name, p.payment_date, p.amount,
    array['cash', 'open'],
    format('Payment %s is money paid OUT to %s (%s %s, %s)%s%s%s. Cash net of refunds is reduced by it.',
        p.payment_ref, coalesce(c.customer_name, 'no customer'), to_char(abs(p.amount), 'FM999,999,990.00'), {{ cur }}, p.payment_date,
        case when cardinality(p.invoice_ids) = 0 then ' and is matched to no credit note' else '' end,
        coalesce('; its memo says "' || p.memo || '"' || case when p.memo ~* '(received|receipt|from )' then ', which describes money coming in: likely a receipt keyed in the wrong direction' else '' end, ''),
        coalesce('; ' || (select string_agg(format('an inbound payment %s of the same amount followed on %s', q.payment_ref, q.payment_date), ', ')
            from pay q where q.partner_id = p.partner_id and q.payment_type = 'inbound' and q.state = 'posted'
              and abs(abs(q.amount) - abs(p.amount)) <= 0.01 and abs(q.payment_date - p.payment_date) <= 14), ''))
from pay p left join partner_cust c on c.partner_id = p.partner_id
where p.payment_type = 'outbound' and p.state in ('posted', 'paid', 'in_process')
  and (cardinality(p.invoice_ids) = 0 or p.memo ~* '(received|receipt)')

union all
-- Tax at a rate far from the rate the same currency's other invoices carry (charged twice, or not at all).
select 'tax_rate_out_of_line', 'invoice', s.invoice_ref, c.customer_key, c.customer_name, s.invoice_date,
    round(s.amount * s.amount_tax_in_invoice_currency / nullif(s.amount_in_invoice_currency, 0), 2),
    array['billed', 'open', 'past_due'],
    format('%s (%s) charges tax at %s%% of its untaxed amount (%s %s tax), against %s%% on the other taxed %s invoices; it looks like tax charged twice or at the wrong rate.',
        s.invoice_ref, c.customer_name, round(100 * s.amount_tax_in_invoice_currency / s.amount_untaxed_in_invoice_currency, 2),
        to_char(s.amount_tax_in_invoice_currency, 'FM999,999,990.00'), s.currency, round((100 * r.usual)::numeric, 2), s.currency)
from {{ ref('stg_invoices') }} s join partner_cust c on c.partner_id = s.partner_id
join (select currency, percentile_cont(0.5) within group (order by amount_tax_in_invoice_currency / amount_untaxed_in_invoice_currency) as usual, count(*) as n
      from {{ ref('stg_invoices') }} where state = 'posted' and amount_tax_in_invoice_currency > 0 and amount_untaxed_in_invoice_currency > 0
      group by currency) r on r.currency = s.currency
where s.state = 'posted' and s.amount_tax_in_invoice_currency > 0 and s.amount_untaxed_in_invoice_currency > 0 and r.n >= 3
  and abs(s.amount_tax_in_invoice_currency / s.amount_untaxed_in_invoice_currency - r.usual::numeric) > greatest(0.25 * r.usual::numeric, 0.005)

union all
-- One invoice listed by more than one order: summing invoiced per order counts it more than once.
select 'invoice_on_several_orders', 'invoice', i.invoice_ref, i.customer_key, i.customer_name, i.invoice_date, i.amount,
    array['billed', 'deals'],
    format('%s (%s, %s %s) is listed by %s orders (%s); a per-order or per-deal invoiced total counts it %s times, so do not add those totals up.',
        i.invoice_ref, i.customer_name, to_char(i.amount, 'FM999,999,990.00'), {{ cur }}, count(*),
        string_agg(o.order_ref, ', ' order by o.order_ref), count(*))
from inv i join {{ ref('stg_orders') }} o on i.invoice_id = any(o.invoice_ids)
group by i.invoice_ref, i.customer_key, i.customer_name, i.invoice_date, i.amount
having count(*) > 1

union all
-- Two customers that look like one organisation (same mail domain, or one name starting with the other's
-- first words) but are not linked as parent and site.
select 'related_customer_not_linked', 'customer', b.customer_name, b.customer_key, b.customer_name, null::date, bt.billed,
    array['billed', 'open', 'past_due', 'counts'],
    format('%s (billed %s %s, open %s) looks like part of %s (%s) but is a separate customer, not linked to it as a site; totals for %s leave it out.',
        b.customer_name, to_char(coalesce(bt.billed, 0), 'FM999,999,990.00'), {{ cur }}, to_char(coalesce(bt.open, 0), 'FM999,999,990.00'),
        a.customer_name, case when a.domain = b.domain then 'same mail domain ' || a.domain else 'same name' end, a.customer_name)
from (select distinct c.customer_key, c.customer_name, split_part(p.email, '@', 2) as domain,
          lower(split_part(c.customer_name, ' ', 1) || ' ' || split_part(c.customer_name, ' ', 2)) as stem
      from partner_cust c join {{ ref('stg_partners') }} p on p.partner_id = c.customer_key) a
join (select distinct c.customer_key, c.customer_name, split_part(p.email, '@', 2) as domain,
          lower(split_part(c.customer_name, ' ', 1) || ' ' || split_part(c.customer_name, ' ', 2)) as stem
      from partner_cust c join {{ ref('stg_partners') }} p on p.partner_id = c.customer_key) b
  on a.customer_key < b.customer_key
 and ((a.domain = b.domain and a.domain <> '' and a.domain !~* '^(gmail|yahoo|outlook|hotmail|icloud|aol|proton)')
      or (length(a.stem) > 8 and a.stem = b.stem))
join (select customer_key, sum(amount) as billed, sum(amount_open) as open from inv group by 1) bt on bt.customer_key = b.customer_key
where exists (select 1 from inv where inv.customer_key = a.customer_key)

union all
-- A country that the customer's own mail domain or billing currency contradicts.
select 'customer_country_out_of_line', 'customer', c.customer_name, c.customer_key, c.customer_name, null::date, null::numeric,
    array['counts', 'billed'],
    format('%s is recorded in %s, but %s; the country is probably keyed wrong, so by-country figures may misplace it.',
        c.customer_name, p.country, concat_ws(' and ',
            case when t.tld_country is not null and t.tld_country <> p.country then 'its mail domain ends .' || t.tld || ' (' || t.tld_country || ')' end,
            case when p.country = 'United States' and cur.currencies is not null and not ('USD' = any(cur.currencies))
                 then 'it is billed only in ' || array_to_string(cur.currencies, ', ') end))
from partner_cust c join {{ ref('stg_partners') }} p on p.partner_id = c.customer_key
left join lateral (select substring(p.email from '\.([a-z]{2})$') as tld) e on true
left join (values ('uk', 'United Kingdom'), ('ca', 'Canada'), ('de', 'Germany'), ('nl', 'Netherlands'), ('fr', 'France'),
                  ('mx', 'Mexico'), ('au', 'Australia'), ('ie', 'Ireland'), ('es', 'Spain'), ('it', 'Italy'), ('be', 'Belgium'),
                  ('ch', 'Switzerland'), ('jp', 'Japan'), ('in', 'India'), ('br', 'Brazil')) t(tld, tld_country) on t.tld = e.tld
left join (select customer_key, array_agg(distinct currency) as currencies from inv group by 1) cur on cur.customer_key = c.customer_key
where c.partner_id = c.customer_key and p.country is not null
  and ((t.tld_country is not null and t.tld_country <> p.country)
       or (p.country = 'United States' and cur.currencies is not null and not ('USD' = any(cur.currencies))))

union all
-- Deals closed before they were created, or many created on one day: the create dates are an import, not opening dates.
select 'deal_dates_out_of_order', 'deal', d.deal_name, c.customer_key, c.customer_name, d.closed_on, d.expected_revenue,
    array['deals'],
    format('Deal "%s" (%s) was closed on %s but created on %s%s; its create date is not when it opened, so cycle times from it are wrong.',
        d.deal_name, c.customer_name, d.closed_on, d.created_at::date,
        case when b.n >= 10 then format(', one of %s deals created that same day (a bulk entry or import)', b.n) else '' end)
from {{ ref('stg_deals') }} d join partner_cust c on c.partner_id = d.partner_id
left join (select created_at::date as day, count(*) as n from {{ ref('stg_deals') }} group by 1) b on b.day = d.created_at::date
where d.closed_on < d.created_at::date - 1 and d.created_at::date <= {{ as_of }}

union all
select 'deals_created_in_bulk', 'deal', 'created ' || b.day, null::int, null, b.day, b.value,
    array['deals', 'counts'],
    format('%s of %s deals (%s %s expected) carry the create date %s, %s of them closed before that date: a bulk entry or import. Create dates on or before that day are not opening dates, so counts of deals created by month and deal cycle times are unreliable.',
        b.n, t.n, to_char(b.value, 'FM999,999,990.00'), {{ cur }}, b.day, b.closed_before)
from (select created_at::date as day, count(*) as n, sum(expected_revenue) as value,
             count(*) filter (where closed_on < created_at::date) as closed_before
      from {{ ref('stg_deals') }} where created_at::date <= {{ as_of }} group by 1) b
cross join (select count(*) as n from {{ ref('stg_deals') }}) t
where b.n >= 10 and b.n >= 0.2 * t.n and b.closed_before > 0

union all
-- A customer known under another name.
select 'customer_renamed', 'customer', c.customer_name, c.customer_key, c.customer_name, null::date, null::numeric,
    array['counts'],
    format('%s was also recorded as %s in Odoo.', c.customer_name,
        string_agg(distinct '"' || n.partner_name || '"', ', '))
from {{ ref('stg_partner_names') }} n join partner_cust c on c.partner_id = n.partner_id
where n.partner_name <> c.customer_name and not n.is_current
group by c.customer_key, c.customer_name
)
select row_number() over (order by issue_type, event_date, entity_ref) as issue_id, *
from issues
