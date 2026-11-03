// shell — pure helpers for the Trace Lab page frame: the tabs, the ?tl= link, the filters as a query, the
// 45-second update line and the page-level notices. No state of its own and no requests.
import { RANGES } from '../../logic/tracesPage.js';
import { TABS } from '../nav.js';
import { FILTER_DEFAULTS } from '../store.js';
import { humanize } from './timeline.js';

export { RANGES, TABS };
export const TAB_LABELS = Object.freeze({ conversations: 'Conversations', runs: 'Runs', golden: 'Golden set', models: 'Model calls', map: 'Map', progress: 'Progress', labels: 'Labels', judges: 'Judges', watch: 'Watch' });
export const POLL_MS = 45000;
export const POLLED_TABS = ['conversations', 'runs'];
export const PAGE_SIZE = 25;
export const CALLS_PAGE_SIZE = 50;
export const CHANNEL_LABELS = Object.freeze({
  chat: 'Asked in the chat', ui_action: 'From a page', support: 'Support', upload: 'Upload', correction: 'Correction',
  report: 'Report', workflow: 'Workflow step', golden: 'Golden run'
});
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const KEY_RE = /^[str]:[^\u0000-\u001f]{1,200}$/;
const TURN_RE = /^[\w.:-]{1,80}$/;
const LINK_KEYS = ['tl', 'run', 'conv', 'turn'];

export const isRunId = (v) => v === 'live' || UUID_RE.test(String(v || ''));

// The start of a period, as logic/tracesPage.js computes it for the Hoist Traces page (its own helper is not
// exported, so it is repeated here): today from midnight, else that many days back from now.
export function sinceFor(range, now) {
  const r = RANGES.find((x) => x.key === range) || RANGES[1];
  const d = new Date(now === undefined ? Date.now() : now);
  if (r.key === 'today') { d.setHours(0, 0, 0, 0); return d.toISOString(); }
  d.setDate(d.getDate() - r.days);
  return d.toISOString();
}

// ?tl=<tab>&run=<run>&conv=<key>&turn=<turn>, read strictly: anything malformed is dropped, an unknown tab is
// no link at all.
export function parseLink(search) {
  let p;
  try { p = new URLSearchParams(search || ''); } catch (e) { return null; }
  const tab = p.get('tl');
  if (!tab || TABS.indexOf(tab) < 0) return null;
  const run = p.get('run') || 'live';
  const conv = p.get('conv');
  const turn = p.get('turn');
  return { tab, run: isRunId(run) ? run : 'live', conv: conv && KEY_RE.test(conv) ? conv : null, turn: turn && TURN_RE.test(turn) ? turn : null };
}

// The address bar's query with Trace Lab's keys set from `st` (null takes them out); other keys are kept.
export function linkSearch(search, st) {
  const p = new URLSearchParams(search || '');
  LINK_KEYS.forEach((k) => p.delete(k));
  if (st) {
    p.set('tl', st.tab);
    p.set('run', st.run || 'live');
    if (st.tab === 'conversations' && st.convKey) p.set('conv', st.convKey);
    if (st.tab === 'conversations' && st.convKey && st.turnId) p.set('turn', st.turnId);
  }
  const s = p.toString();
  return s ? '?' + s : '';
}

// The filters as the list routes take them.
export function listQuery(s, now) {
  const f = Object.assign({}, FILTER_DEFAULTS, (s && s.filters) || {});
  const q = { since: sinceFor(f.range, now), run: (s && s.run) || 'live' };
  if (f.channel) q.channel = f.channel;
  if (f.status) q.status = f.status;
  if (f.q) q.q = f.q;
  return q;
}

export function tabsView(s) {
  return TABS.map((key) => ({ key, label: TAB_LABELS[key], on: key === s.tab }));
}

const hhmm = (ms) => { const d = new Date(ms); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); };

export function updateLine(s) {
  const p = (s && s.poll) || {};
  if (p.error) return "Couldn't refresh — retry" + (p.updatedAt ? ' · last updated ' + hhmm(p.updatedAt) : '');
  if (p.updatedAt) return 'Updated ' + hhmm(p.updatedAt) + (p.newTurns ? ' · ' + p.newTurns + ' new' : '');
  return '';
}

export function channelLabel(value) {
  return CHANNEL_LABELS[value] || humanize(value) || 'Unknown source';
}

// The Source filter's options: the channels the period actually has (the facets route), plus the one picked.
export function channelOptions(s) {
  const raw = (s && s.facets && Array.isArray(s.facets.channels)) ? s.facets.channels : [];
  const values = raw.map((c) => (c && typeof c === 'object' ? c.value : c)).filter((v) => typeof v === 'string' && v);
  const picked = s && s.filters && s.filters.channel;
  if (picked && values.indexOf(picked) < 0) values.push(picked);
  return [{ value: '', label: 'Every source' }].concat(values.map((v) => ({ value: v, label: channelLabel(v) })));
}

// Why a whole tab cannot be shown, from the service's refusal reason (src/tracelab never words the service's
// own messages: client.js has already scrubbed them).
const NOTICES = {
  name_a_company: 'Trace Lab reviews one company at a time. Open the Super Admin console, choose View as for a company, then open Trace Lab again.',
  reviewers_only: 'Trace Lab is for company admins.',
  bad_company: 'Trace Lab can only show your own company.'
};
export function noticeFor(reason, message) {
  if (reason && NOTICES[reason]) return NOTICES[reason];
  return message || '';
}
export const PAGE_REASONS = Object.keys(NOTICES);
