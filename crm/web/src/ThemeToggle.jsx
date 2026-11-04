// The header's light and dark switch, the same control as the portfolio site's. It follows the
// system setting until pressed, then remembers the choice (localStorage "crm-theme").
// index.html applies the saved choice before first paint; this keeps <html data-theme> in step.

import { useSyncExternalStore } from 'react';

const KEY = 'crm-theme';
const DARK = '(prefers-color-scheme: dark)';
const EVENT = 'crm-theme';

// Tells React when the theme may have changed: the button or the system setting.
function subscribe(onChange) {
  const q = window.matchMedia?.(DARK);
  q?.addEventListener?.('change', onChange);
  window.addEventListener(EVENT, onChange);
  return () => {
    q?.removeEventListener?.('change', onChange);
    window.removeEventListener(EVENT, onChange);
  };
}

// Reads whether the dark theme is showing.
export function isDarkTheme() {
  const chosen = document.documentElement.getAttribute('data-theme');
  return chosen ? chosen === 'dark' : Boolean(window.matchMedia?.(DARK).matches);
}

// Returns true while the dark theme is showing, and re-renders when it changes.
export function useDarkTheme() {
  return useSyncExternalStore(subscribe, isDarkTheme, () => false);
}

export default function ThemeToggle() {
  const dark = useDarkTheme();

  // Flips the theme with one cross-fade, and saves the choice when storage allows.
  const toggle = () => {
    const root = document.documentElement;
    const next = dark ? 'light' : 'dark';
    const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (!still) root.classList.add('theme-switching');
    root.setAttribute('data-theme', next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // Storage refused: the choice lasts for this visit.
    }
    window.dispatchEvent(new Event(EVENT));
    if (!still) setTimeout(() => root.classList.remove('theme-switching'), 360);
  };

  return (
    <button type="button" className="ui-icon-btn ui-round" onClick={toggle} aria-pressed={dark} aria-label="Dark theme" title={dark ? 'Switch to light theme' : 'Switch to dark theme'}>
      <svg viewBox="0 0 20 20" width="17" height="17" aria-hidden="true" focusable="false">
        <circle cx="10" cy="10" r="7.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
        <path d="M10 2.8a7.2 7.2 0 0 1 0 14.4z" fill="currentColor" />
      </svg>
    </button>
  );
}
