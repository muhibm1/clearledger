// Number and date formatting for the page: whole dollars, short dates.

const USD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const USD_CENTS = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 });

// Formats dollars rounded to the dollar, or a dash for nothing.
export function usd(n) {
  return n ? USD.format(n) : '—';
}

// Formats dollars with cents, for invoice and payment rows.
export function cents(n, currency = 'USD') {
  if (n == null) return '—';
  return currency === 'USD' ? USD_CENTS.format(n) : `${n.toLocaleString('en-US', { minimumFractionDigits: 2 })} ${currency}`;
}

// Formats an ISO date as "Oct 27, 2026".
export function day(iso) {
  if (!iso) return '—';
  return new Date(iso.slice(0, 10) + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

// Every source reference (account.move/181@2026-...) inside a tool result.
export function refsIn(value) {
  return new Set(JSON.stringify(value ?? '').match(/(account\.move|account\.payment|sale\.order)\/\d+@[\dT:-]+/g) || []);
}
