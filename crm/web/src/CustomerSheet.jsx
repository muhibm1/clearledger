// The customer sidebar: both sides of one customer. The CRM side (open pipeline, deals won)
// and the ERP side (each won deal followed to cash, invoices, payments, what is late).
// The slide-in, focus handling and Escape come from Auspex's DetailSheet. Records the voice
// agent just cited are lit up.

import { useEffect, useRef } from 'react';
import { Badge, IconButton } from './ui.jsx';
import { cents, day, usd } from './format.js';

const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

const DEAL_TONE = { paid: 'ok', 'partly paid': 'warn', 'invoiced, unpaid': 'warn', 'ordered, not invoiced': 'neutral', 'won, no order': 'bad' };
const BUCKETS = [
  ['not_yet_due', 'Not due', 'var(--ok)'],
  ['late_1_30', '1–30 days', 'var(--warn-line)'],
  ['late_31_60', '31–60', 'var(--warn)'],
  ['late_61_90', '61–90', 'var(--bad-line)'],
  ['late_over_90', 'Over 90', 'var(--bad)'],
];

// The aging bar: how much of what is owed sits in each lateness bucket.
function Aging({ aging, asOf }) {
  const parts = BUCKETS.map(([k, label, color]) => ({ label, color, v: Math.max(aging?.[k] || 0, 0) })).filter((p) => p.v > 0);
  const total = parts.reduce((s, p) => s + p.v, 0);
  if (!total) return <p className="crm-muted">Nothing owed.</p>;
  return (
    <div className="crm-aging">
      <div className="crm-aging-bar" role="img" aria-label="Amount owed by days late">
        {parts.map((p) => <span key={p.label} style={{ flexGrow: p.v, background: p.color }} />)}
      </div>
      <ul className="crm-aging-key">
        {parts.map((p) => <li key={p.label}><i style={{ background: p.color }} />{p.label} <b>{usd(p.v)}</b></li>)}
      </ul>
      <p className="crm-muted crm-small">Aged as of {day(asOf)}.</p>
    </div>
  );
}

// A record's row class: lit when the agent cited it in this call.
const lit = (lights, ref) => (ref && lights.has(ref) ? 'crm-lit' : undefined);

export default function CustomerSheet({ open, data, lights, onClose }) {
  const root = useRef(null);
  const last = useRef(null);
  if (data) last.current = data;
  const shown = data || last.current;

  // Focuses the sheet on open, restores focus on close, traps Tab and closes on Escape.
  useEffect(() => {
    if (!open) return undefined;
    const before = document.activeElement;
    root.current?.focus({ preventScroll: true });
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose?.();
      } else if (e.key === 'Tab' && root.current) {
        const items = [...root.current.querySelectorAll(FOCUSABLE)].filter((el) => !el.disabled);
        if (!items.length) return;
        const first = items[0];
        const lastEl = items[items.length - 1];
        if (!root.current.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
        else if (e.shiftKey && (document.activeElement === first || document.activeElement === root.current)) { e.preventDefault(); lastEl.focus(); }
        else if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      if (before && before.isConnected) before.focus({ preventScroll: true });
    };
  }, [open, onClose]);

  const s = shown?.summary;
  const openDeals = shown?.pipeline.filter((d) => !d.is_lost) || [];
  return (
    <aside ref={root} className={'sheet crm-sheet' + (open ? ' open' : '')} tabIndex={-1} role="dialog" aria-label={s ? s.customer_name : 'Customer'} aria-hidden={!open} inert={!open}>
      {s && (
        <>
          <header className="sheet-head crm-sheet-head">
            <div className="crm-sheet-titles">
              <h2 className="crm-sheet-name">{s.customer_name}</h2>
              <span className="crm-muted crm-small">
                {shown.partners.length > 1 ? `${shown.partners.length} Odoo partners merged: ${shown.partners.join(', ')}` : `Odoo partner ${shown.partners[0]}`}
              </span>
            </div>
            <IconButton label="Close" variant="ghost" size="sm" onClick={onClose}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
            </IconButton>
          </header>
          <div className="sheet-body">
            <dl className="crm-stats">
              <div><dt>Billed</dt><dd>{usd(s.billed_usd)}</dd></div>
              <div><dt>Paid</dt><dd>{usd(s.paid_usd)}</dd></div>
              <div><dt>Owed</dt><dd className={s.owed_usd > 0 ? 'crm-owed' : undefined}>{usd(s.owed_usd)}</dd></div>
              <div><dt>Open pipeline</dt><dd>{usd(openDeals.reduce((t, d) => t + d.expected_revenue, 0))}</dd></div>
            </dl>

            <section className="crm-sec">
              <h3 className="ui-caps">What is owed, by days late</h3>
              <Aging aging={s.aging_usd} asOf={s.aged_as_of} />
            </section>

            <section className="crm-sec">
              <h3 className="ui-caps">Won deals, followed to cash <span className="crm-tag">CRM → ERP</span></h3>
              {shown.won_deals.length ? (
                <table className="crm-mini">
                  <thead><tr><th>Deal</th><th className="num">Won</th><th className="num">Invoiced</th><th className="num">Paid</th><th>Status</th></tr></thead>
                  <tbody>
                    {shown.won_deals.map((d) => (
                      <tr key={d.deal_id} className={d.sources.some((r) => lights.has(r)) ? 'crm-lit' : undefined}>
                        <td>{d.deal_name}</td>
                        <td className="num">{usd(d.expected_revenue)}</td>
                        <td className="num">{usd(d.invoiced)}</td>
                        <td className="num">{usd(d.paid)}</td>
                        <td><Badge tone={DEAL_TONE[d.status] || 'neutral'}>{d.status}</Badge></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <p className="crm-muted">No deals won yet.</p>}
            </section>

            <section className="crm-sec">
              <h3 className="ui-caps">Open pipeline <span className="crm-tag">CRM</span></h3>
              {openDeals.length ? (
                <table className="crm-mini">
                  <thead><tr><th>Deal</th><th>Stage</th><th className="num">Expected</th><th>Opened</th></tr></thead>
                  <tbody>
                    {openDeals.map((d) => (
                      <tr key={d.deal_id}><td>{d.deal_name}</td><td>{d.stage}</td><td className="num">{usd(d.expected_revenue)}</td><td>{day(d.created_at)}</td></tr>
                    ))}
                  </tbody>
                </table>
              ) : <p className="crm-muted">No open deals.</p>}
            </section>

            <section className="crm-sec">
              <h3 className="ui-caps">Invoices and credit notes <span className="crm-tag">ERP</span></h3>
              <table className="crm-mini">
                <thead><tr><th>Invoice</th><th>Due</th><th className="num">Days late</th><th className="num">Amount</th><th className="num">Open</th></tr></thead>
                <tbody>
                  {shown.invoices.map((i) => (
                    <tr key={i.source_ref} className={lit(lights, i.source_ref)} title={i.source_ref}>
                      <td className="mono">{i.invoice_ref}</td>
                      <td>{day(i.due_date)}</td>
                      <td className={'num' + (i.open_usd > 0 && i.days_past_due > 90 ? ' crm-bad' : '')}>{i.open_usd > 0 && i.days_past_due > 0 ? i.days_past_due : '—'}</td>
                      <td className="num">{cents(i.amount_in_invoice_currency, i.currency)}</td>
                      <td className="num">{i.open_usd ? cents(i.open_usd) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            <section className="crm-sec">
              <h3 className="ui-caps">Payments <span className="crm-tag">ERP</span></h3>
              {shown.payments.length ? (
                <table className="crm-mini">
                  <thead><tr><th>Payment</th><th>Date</th><th className="num">Amount</th></tr></thead>
                  <tbody>
                    {shown.payments.map((p) => (
                      <tr key={p.source_ref} className={lit(lights, p.source_ref)} title={p.source_ref}>
                        <td className="mono">{p.payment_ref}</td><td>{day(p.payment_date)}</td><td className="num">{cents(p.amount, p.currency)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <p className="crm-muted">No payments.</p>}
            </section>
            <p className="crm-muted crm-small">Read-only. Changes are made in Odoo. Hover a row for the exact Odoo record version it came from.</p>
          </div>
        </>
      )}
    </aside>
  );
}
