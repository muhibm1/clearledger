// Shared controls: Button, IconButton, Segmented, Tabs and Badge. Styles live in ui.css and read
// only the design tokens, so every control looks and moves the same in both themes.

import { useLayoutEffect, useRef, useState } from 'react';

// A text button. variant: 'default' | 'ghost' | 'primary'; size: 'md' | 'sm'.
export function Button({ variant = 'default', size = 'md', className = '', ...rest }) {
  return <button type="button" className={['ui-btn', variant !== 'default' && variant, size === 'sm' && 'sm', className].filter(Boolean).join(' ')} {...rest} />;
}

// A square (or round) button holding one icon; label is required for screen readers.
export function IconButton({ label, variant = 'default', size = 'md', round = false, className = '', children, ...rest }) {
  return (
    <button type="button" aria-label={label} title={label} className={['ui-icon-btn', variant !== 'default' && variant, size === 'sm' && 'sm', round && 'ui-round', className].filter(Boolean).join(' ')} {...rest}>
      {children}
    </button>
  );
}

// Measures the chosen child of a container so a thumb or underline can slide to it.
function useIndicator(value) {
  const ref = useRef(null);
  const [box, setBox] = useState(null);
  useLayoutEffect(() => {
    const el = ref.current?.querySelector('[data-on="true"]');
    if (!el) return setBox(null);
    const measure = () => setBox({ x: el.offsetLeft, w: el.offsetWidth });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [value]);
  return [ref, box];
}

// A segmented choice; options are [{ value, label }].
export function Segmented({ value, options, onChange, label }) {
  const [ref, box] = useIndicator(value);
  return (
    <div className="ui-seg" role="radiogroup" aria-label={label} ref={ref}>
      {box && <span className="ui-seg-thumb" style={{ width: box.w, transform: `translateX(${box.x}px)` }} />}
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={o.value === value} data-on={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

// Text tabs with a sliding underline; tabs are [{ id, label }].
export function Tabs({ value, tabs, onChange, label }) {
  const [ref, box] = useIndicator(value);
  return (
    <div className="ui-tabs" role="tablist" aria-label={label} ref={ref}>
      {tabs.map((t) => (
        <button key={t.id} type="button" role="tab" id={'tab-' + t.id} aria-controls={'panel-' + t.id} aria-selected={t.id === value} data-on={t.id === value} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
      {box && <span className="ui-tabs-line" style={{ width: box.w, transform: `translateX(${box.x}px)` }} />}
    </div>
  );
}

// A small status pill. tone: 'neutral' | 'live' | 'ok' | 'warn' | 'bad'.
export function Badge({ tone = 'neutral', dot = false, pulse = false, children }) {
  return (
    <span className={'ui-badge' + (tone !== 'neutral' ? ' ' + tone : '')}>
      {dot && <span className={'ui-dot' + (pulse ? ' pulse' : '')} />}
      <span className="ui-badge-text">{children}</span>
    </span>
  );
}
