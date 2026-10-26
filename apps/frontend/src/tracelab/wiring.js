// wiring — what ties Phase 4's open coding, Map and Progress to the Trace Lab page without editing actions.js: the
// reads the page makes when a tab or a conversation opens (the Map and Progress figures, the open answer's
// verdicts and comments), the r / w keys, and one attach() whose cleanup stops what it started. TraceLabApp.jsx
// calls these from effects; nothing here keeps state of its own.
import { shortcut, loadAnnotations, stopAnnotationPoll } from './annotateActions.js';
import { loadMapTab, loadProgressTab } from './samplingActions.js';

// The Map tab reads the plan, the batches and the map; Progress reads the plan and the figures. Any other tab,
// nothing. (The period and the block size are the sampling slice's own.)
export function loadSamplingFor(s) {
  if (!s) return;
  if (s.tab === 'map') loadMapTab();
  else if (s.tab === 'progress') loadProgressTab();
}

// The open conversation's verdicts, comments and block text, once the conversation itself is on screen (before
// that there is no timeline to mark up, and while it is being read again the answer shown may be about to change)
// and only on Conversations, for the answer shown.
export function loadAnnotationsFor(s) {
  if (!s || s.tab !== 'conversations' || !s.convKey) return;
  const data = s.conv && s.conv.data;
  if (!data || data.key !== s.convKey || s.conv.loading) return;
  loadAnnotations({ key: s.convKey, turnId: s.turnId });
}

// Text fields take keys for themselves. A radio button, a checkbox or a button does not: the reviewer has just
// clicked Right or Wrong and the next key should still work.
const NON_TEXT = ['radio', 'checkbox', 'button', 'submit', 'reset'];
function typing(t) {
  if (!t) return false;
  if (t.isContentEditable) return true;
  const tag = String(t.tagName || '').toUpperCase();
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return tag === 'INPUT' && NON_TEXT.indexOf(String(t.type || 'text').toLowerCase()) < 0;
}

// r = Right, w = Wrong, on the open answer of the Conversations tab. Not while typing, with a modifier, on a key
// repeat, or when something else already took the key. true when the key was taken (and its default prevented).
export function reviewKeyHandler(e) {
  if (!e || e.defaultPrevented || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return false;
  if (e.key !== 'r' && e.key !== 'w') return false;
  if (typing(e.target)) return false;
  if (!shortcut(e.key)) return false;
  e.preventDefault();
  return true;
}

// Start listening for the verdict keys; the returned function stops that and the 30-second annotation read.
export function attach() {
  const doc = typeof document !== 'undefined' ? document : null;
  if (doc && doc.addEventListener) doc.addEventListener('keydown', reviewKeyHandler);
  return function detach() {
    if (doc && doc.removeEventListener) doc.removeEventListener('keydown', reviewKeyHandler);
    stopAnnotationPoll();
  };
}
