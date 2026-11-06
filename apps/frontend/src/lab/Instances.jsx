// Instances — which instance the lab is working in, kept for the browser tab.
//
// An instance is not a label on a run. agents/spaces.py decides which engines may answer at all,
// so the same question asked in Ops Brain and in the Compliance Engine is two different
// measurements, and a batch generated without one fills with questions the instance refuses.
// It is chosen with the Instance select on Synthetic; its own page was removed on 6 Nov 2026 (Bala).
//
// The four come from logic/instances.js — the same definitions the Hoistra picker shows — rather
// than a copy here, so the lab cannot accept an instance the product no longer has. The lab
// reads them; it never sets the app's active space (that is the chat's own, set per request).
import { INSTANCES } from '../logic/instances.js';

export const DEFAULT_INSTANCE = 'ops';
const KEY = 'lab.instance';

export function readInstance() {
  try {
    const v = window.sessionStorage.getItem(KEY);
    return INSTANCES.some((i) => i.key === v) ? v : DEFAULT_INSTANCE;
  } catch (e) {
    // Private windows and blocked site data throw rather than return null.
    return DEFAULT_INSTANCE;
  }
}

export function writeInstance(key) {
  try { window.sessionStorage.setItem(KEY, String(key)); } catch (e) { /* nothing to do */ }
}

