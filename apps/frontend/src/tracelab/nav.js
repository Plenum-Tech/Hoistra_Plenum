// nav — Trace Lab's entry in the Administration navigator (attachment A2: logic/integrations.js spreads
// traceLabNav(s, this) after its own items and sets isTraceLab from traceLabActive(s)). With the flag off both
// are inert: no item, never active, nothing scheduled. This file imports only flag.js, so the app's main bundle
// gains nothing else from it.
import { ON } from './flag.js';

export const VIEW = 'tracelab';
// The tabs, in the order the tab bar shows them; a ?tl= link must name one of them.
export const TABS = ['conversations', 'runs', 'golden', 'models', 'map', 'progress', 'labels', 'judges', 'watch'];

export function traceLabActive(s) {
  return ON && !!(s && s.signedIn) && s.view === VIEW;
}

// The item itself, in Navigator.jsx's shape ({label, icon, badge, color, chip, click}; Navigator prefixes the
// icon with "ph "). Built without looking at the flag so it can be tested; traceLabNav decides whether it shows.
export function navItem(s, c) {
  const on = !!s && s.view === VIEW;
  return {
    label: 'Trace Lab', icon: 'ph-flask', badge: '',
    color: on ? 'var(--color-accent)' : 'var(--color-neutral-300)',
    chip: on ? 'var(--color-accent-900)' : 'transparent',
    click: () => {
      if (typeof window !== 'undefined' && typeof window.scrollTo === 'function') window.scrollTo(0, 0);
      c.setState({ view: VIEW, role: 'admin', navOpen: true, detail: null });
    }
  };
}

// A reload on Trace Lab lands on Home (the app's session store does not know this page), with the ?tl= link
// still in the address bar. The first signed-in render in Admin view on Home opens the page again; the page
// then reads the rest of the link itself. Pure: the patch to apply, or null.
export function linkOpenPatch(s, search) {
  if (!s || !s.signedIn || s.role !== 'admin' || s.view !== 'home') return null;
  let tab = null;
  try { tab = new URLSearchParams(search || '').get('tl'); } catch (e) { return null; }
  if (!tab || TABS.indexOf(tab) < 0) return null;
  return { view: VIEW, role: 'admin', navOpen: true, detail: null };
}

let linkChecked = false;
function openLinkOnce(s, c) {
  if (linkChecked || !s.signedIn) return;
  linkChecked = true;
  let search = '';
  try { search = window.location.search || ''; } catch (e) { return; }
  const patch = linkOpenPatch(s, search);
  if (!patch) return;
  // Never during a render: the controller is told after this one finishes, and only if it is still on Home.
  setTimeout(() => { if (c.state && c.state.view === 'home') c.setState(patch); }, 0);
}

export function traceLabNav(s, c) {
  if (!ON || !s || s.role !== 'admin') return [];
  openLinkOnce(s, c);
  return [navItem(s, c)];
}
