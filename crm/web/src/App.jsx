// The finance desk: every customer from both sides of the business in one grid, a sidebar per
// customer, ⌘K to jump to one, and a voice agent the screen follows as it looks things up.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Badge, Button, IconButton } from './ui.jsx';
import ThemeToggle from './ThemeToggle.jsx';
import CustomerSheet from './CustomerSheet.jsx';
import { useVoice } from './useVoice.js';
import { day, refsIn, usd } from './format.js';

const COLUMNS = [
  { key: 'customer_name', label: 'Customer', text: true },
  { key: 'pipeline', label: 'Open pipeline', side: 'CRM' },
  { key: 'won_deals', label: 'Deals won', side: 'CRM', count: true },
  { key: 'won_not_cash', label: 'Won, not yet cash', side: 'CRM → ERP', count: true },
  { key: 'billed', label: 'Billed', side: 'ERP' },
  { key: 'owed', label: 'Owed', side: 'ERP' },
  { key: 'late', label: 'Late', side: 'ERP' },
  { key: 'most_days_late', label: 'Oldest late', side: 'ERP', days: true },
];

// One cell's text.
function cell(row, col) {
  const v = row[col.key];
  if (col.text) return v;
  if (col.count) return v || '—';
  if (col.days) return v > 0 ? `${v} days` : '—';
  return usd(v);
}

// The ⌘K box: type part of a name, arrows to move, Enter to open.
function Jump({ open, customers, onPick, onClose }) {
  const [q, setQ] = useState('');
  const [at, setAt] = useState(0);
  const input = useRef(null);
  useEffect(() => { if (open) { setQ(''); setAt(0); setTimeout(() => input.current?.focus(), 0); } }, [open]);
  const hits = useMemo(() => customers.filter((c) => c.customer_name.toLowerCase().includes(q.toLowerCase())).slice(0, 8), [customers, q]);
  if (!open) return null;
  const key = (e) => {
    if (e.key === 'Escape') onClose();
    else if (e.key === 'ArrowDown') { e.preventDefault(); setAt((a) => Math.min(a + 1, hits.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setAt((a) => Math.max(a - 1, 0)); }
    else if (e.key === 'Enter' && hits[at]) onPick(hits[at].customer_key);
  };
  return (
    <div className="crm-jump-back" onMouseDown={onClose}>
      <div className="crm-jump" role="dialog" aria-label="Jump to a customer" onMouseDown={(e) => e.stopPropagation()}>
        <input ref={input} value={q} onChange={(e) => { setQ(e.target.value); setAt(0); }} onKeyDown={key} placeholder="Jump to a customer…" aria-label="Customer name" />
        <ul role="listbox">
          {hits.map((c, i) => (
            <li key={c.customer_key} role="option" aria-selected={i === at} onMouseEnter={() => setAt(i)} onClick={() => onPick(c.customer_key)}>
              <span>{c.customer_name}</span><span className="crm-muted">{c.owed ? `owes ${usd(c.owed)}` : 'nothing owed'}</span>
            </li>
          ))}
          {!hits.length && <li className="crm-muted">No customer by that name.</li>}
        </ul>
      </div>
    </div>
  );
}

// The call panel: what was said, and each lookup the agent made.
function CallPanel({ voice }) {
  const end = useRef(null);
  useEffect(() => end.current?.scrollIntoView({ block: 'end' }), [voice.lines.length]);
  if (!voice.live && !voice.lines.length && !voice.error) return null;
  return (
    <section className="crm-call" aria-label="Voice call">
      <header className="crm-call-head">
        <span className="ui-caps">Call</span>
        <Badge tone={voice.live ? 'live' : voice.error ? 'bad' : 'neutral'} dot={voice.live} pulse={voice.status === 'speaking'}>
          {voice.error && !voice.live ? 'Not connected' : { connecting: 'Connecting', listening: 'Listening', speaking: 'Speaking' }[voice.status] || 'Ended'}
        </Badge>
        {!voice.live && (
          <IconButton label="Close" variant="ghost" size="sm" onClick={voice.dismiss}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </IconButton>
        )}
      </header>
      <ol className="crm-call-lines">
        {voice.lines.map((l, i) => (
          <li key={i} className={'crm-line ' + l.who}>
            {l.who === 'tool'
              ? <span className="mono">{l.error ? '✕' : '↳'} {l.name}({Object.entries(l.args).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(', ')}){l.ms != null && <span className="crm-muted"> · {l.ms} ms</span>}</span>
              : <><b>{l.who === 'you' ? 'You' : 'Agent'}</b> {l.text}</>}
          </li>
        ))}
        {voice.error && <li className="crm-line err">{voice.error}</li>}
        <li ref={end} aria-hidden="true" />
      </ol>
    </section>
  );
}

export default function App() {
  const [data, setData] = useState(null);
  const [sort, setSort] = useState({ key: 'owed', dir: -1 });
  const [openKey, setOpenKey] = useState(null);
  const [detail, setDetail] = useState(null);
  const [lights, setLights] = useState(new Set());
  const [jump, setJump] = useState(false);

  useEffect(() => { fetch('/api/customers').then((r) => r.json()).then(setData); }, []);

  // Opens a customer's sidebar and loads their page.
  const openCustomer = useCallback((key) => {
    setJump(false);
    setOpenKey(key);
    fetch(`/api/customers/${key}`).then((r) => r.json()).then(setDetail);
  }, []);

  // Follows the agent: open the customer it is talking about, light the records it cited.
  const onTool = useCallback((name, args, result) => {
    const key = args.customer_key ?? (name === 'find_customers' && result.customers?.length === 1 ? result.customers[0].customer_key : null);
    if (key != null) openCustomer(key);
    const refs = refsIn(result);
    if (refs.size) setLights(refs);
  }, [openCustomer]);
  const voice = useVoice({ onTool });

  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setJump((j) => !j); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const rows = useMemo(() => {
    const list = [...(data?.customers || [])];
    list.sort((a, b) => (typeof a[sort.key] === 'string' ? a[sort.key].localeCompare(b[sort.key]) : a[sort.key] - b[sort.key]) * sort.dir);
    return list;
  }, [data, sort]);

  const totals = useMemo(() => {
    const c = data?.customers || [];
    const sum = (k) => c.reduce((t, r) => t + (r[k] || 0), 0);
    return { owed: sum('owed'), late: sum('late_over_90'), pipeline: sum('pipeline'), notCash: sum('won_not_cash'), won: sum('won_deals') };
  }, [data]);

  const sheetOpen = openKey != null && detail?.summary?.customer_key === openKey;
  return (
    <div className="crm-app">
      <header className="crm-head">
        <div className="crm-brand">
          <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="5.5" fill="var(--ink)" /><path d="M7 15.5v-3M10.5 15.5v-6M14 15.5v-4.5M17.5 15.5v-8" stroke="var(--page)" strokeWidth="2.2" strokeLinecap="round" /></svg>
          <span>Finance desk</span>
          {data && <span className="crm-muted crm-small crm-asof">Aged as of {day(data.aged_as_of)}</span>}
        </div>
        <div className="crm-head-right">
          <Button variant="ghost" onClick={() => setJump(true)} aria-keyshortcuts="Meta+K">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="6" /><path d="M20 20l-4.5-4.5" /></svg>
            <span className="crm-hide-s">Jump to</span><kbd className="crm-kbd crm-hide-s">⌘K</kbd>
          </Button>
          <Button variant={voice.live ? 'default' : 'primary'} onClick={voice.live ? voice.stop : voice.start}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>
            {voice.live ? 'End call' : 'Talk'}
          </Button>
          <ThemeToggle />
        </div>
      </header>

      <main className="crm-main">
        <dl className="crm-totals">
          <div><dt>Owed to us</dt><dd>{usd(totals.owed)}</dd></div>
          <div><dt>Over 90 days late</dt><dd className="crm-bad">{usd(totals.late)}</dd></div>
          <div><dt>Won deals not yet paid in full</dt><dd>{totals.notCash} <span className="crm-muted crm-small">of {totals.won}</span></dd></div>
          <div><dt>Open pipeline</dt><dd>{usd(totals.pipeline)}</dd></div>
        </dl>

        <div className="crm-grid-wrap">
          <table className="crm-grid">
            <thead>
              <tr>
                {COLUMNS.map((col) => (
                  <th key={col.key} className={col.text ? undefined : 'num'} aria-sort={sort.key === col.key ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none'}>
                    <button type="button" onClick={() => setSort((s) => ({ key: col.key, dir: s.key === col.key ? -s.dir : col.text ? 1 : -1 }))}>
                      {col.side && <span className="crm-side">{col.side}</span>}
                      {col.label}{sort.key === col.key ? (sort.dir > 0 ? ' ↑' : ' ↓') : ''}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.customer_key} aria-current={openKey === r.customer_key || undefined} onClick={() => openCustomer(r.customer_key)}>
                  {COLUMNS.map((col) => (
                    <td key={col.key} className={(col.text ? 'crm-name' : 'num') + (col.key === 'late' && r.late_over_90 > 0 ? ' crm-bad' : '')}>
                      {col.text ? <button type="button" onClick={(e) => { e.stopPropagation(); openCustomer(r.customer_key); }}>{cell(r, col)}</button> : cell(r, col)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="crm-muted crm-small crm-foot">
          Simulated company on a real Odoo 17. Deals come from Odoo's CRM; orders, invoices and payments from its accounting. Every figure traces to the Odoo records behind it.
        </p>
      </main>

      <CallPanel voice={voice} />
      <CustomerSheet open={sheetOpen} data={detail} lights={lights} onClose={() => setOpenKey(null)} />
      <Jump open={jump} customers={data?.customers || []} onPick={openCustomer} onClose={() => setJump(false)} />
    </div>
  );
}
