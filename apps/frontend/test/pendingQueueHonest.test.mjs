// The Pending pill and the Decision queue tell the truth (Hussain, 17 Nov 2026, from the UI
// audit: "The Pending pill can't be acted on … solve all this").
//
//   · Until a read answers there is no number and no card: the pill says "—" and the drawer says
//     it is reading, or that the queue could not be read, with Retry. Seed cards (LOLER, Gas Safe…)
//     read as the company's own and are gone.
//   · The pill says how many are urgent, and is loud only when something is.
//   · A card can be opened or snoozed for a day; a snoozed card leaves the count, so the pill can
//     be cleared. Snoozing is this browser's own, and says so.
//   · The Runs / Notify settings sit behind a settings button, not above the first decision.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.localStorage = { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { queueBadge, loadQueueSnoozes } = await import('../src/logic/queueLive.js');

const approval = (over) => Object.assign({
  id: 'a1', item_type: 'invoice_flag_high_value', summary: 'Invoice INV-2847 — 2 of 12 lines flagged',
  severity: 'high', source_feature: 'B', created_at: '2026-09-24T10:00:00Z', payload: { line: { delta_gbp: 230.25 } }
}, over || {});
const lapsed = approval({ id: 'a2', item_type: 'certificate_lapsed', summary: 'FRA lapsed — Bishopsgate Tower', severity: 'critical', source_feature: 'A' });
const anomaly = { id: 'n1', anomaly_type: 'weekend_spike', status: 'open', detected_at: '2026-09-25T02:00:00Z', metric_pct: 303, financial_gbp: 38400, meter_id: 'MPAN-1' };

let c;
beforeEach(() => { Object.keys(mem).forEach((k) => delete mem[k]); c = new HoistraLogic({}); });
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._qTimer); };
const live = (items, extra) => c.setState(Object.assign({ homeRaw: { approvals: { items: items }, anomalies: { anomalies: [anomaly] } } }, extra || {}));

// ── before any read answers ──────────────────────────────────────────────

test('before a read answers there is no seed card and no number', () => {
  const v = c.renderVals();
  assert.deepEqual(v.queueItems, [], 'no sample LOLER / Gas Safe cards');
  assert.deepEqual(v.queuePreview, []);
  assert.equal(v.queueCount, null);
  assert.equal(v.queueBadgeText, '— urgent');
  assert.equal(v.queueBadgeHot, false, 'nothing known is urgent, so the pill is not loud');
  cleanup();
});

test('the drawer says it is reading, then that it could not read, with Retry', () => {
  c.setState({ homeLoading: true });
  let v = c.renderVals();
  assert.equal(v.queueStateShow, 'block');
  assert.match(v.queueStateText, /Reading/);
  assert.equal(v.queueRetryShow, 'none');

  c.setState({ homeLoading: false, homeError: 'HTTP 503' });
  v = c.renderVals();
  assert.equal(v.queueStateShow, 'block');
  assert.match(v.queueStateText, /could not be read/i);
  assert.doesNotMatch(v.queueStateText, /503/, 'no HTTP code in the sentence');
  assert.equal(v.queueRetryShow, 'inline-flex');
  assert.equal(typeof v.queueRetry, 'function');
  assert.equal(v.queueEmptyShow, 'none', '"nothing is waiting" is never said about a queue that was not read');
  cleanup();
});

// ── the pill counts urgent; the drawer lists everything (17 Nov 2026, third pass) ───────
// Hussain: "show all the items but only show the urgent ones' count in the pending pill and the
// rest of them show inside". The drawer lists Urgent (the pill's number) then Everything else;
// the All chip counts the cards on screen, so it equals Urgent + Everything else.

test('every item is listed; the pill counts the urgent ones; the sections add up', () => {
  live([approval(), lapsed]);   // a high invoice, a critical lapse, and an anomaly (warn)
  const v = c.renderVals();
  assert.equal(v.queueItems.length, 3, 'all three are in the drawer');
  assert.equal(v.queueUrgentItems.length, 1);
  assert.match(v.queueUrgentItems[0].title, /FRA lapsed/);
  assert.equal(v.queueOtherItems.length, 2);
  assert.equal(v.queueBadgeText, '1 urgent', 'the pill counts only the urgent one');
  assert.equal(v.queueCount, 1);
  assert.equal(v.queueUrgentLabel, 'Urgent · 1', 'the Urgent section is the pill\'s number');
  assert.equal(v.queueOtherLabel, 'Everything else · 2');
  assert.equal(v.queueHeadLine, '1 urgent · 2 other');
  const all = v.queueFilterOpts.find((f) => f.label === 'All');
  assert.equal(all.n, '3', 'All = the cards on screen = urgent + everything else');
  const sum = v.queueFilterOpts.filter((f) => f.label !== 'All').reduce((a, f) => a + Number(f.n), 0);
  assert.equal(sum, 3, 'the module chips add up to All');
  cleanup();
});

test('module chips with nothing in them are not offered', () => {
  live([approval(), lapsed]);
  const v = c.renderVals();
  assert.deepEqual(v.queueFilterOpts.map((f) => f.label).sort(), ['All', 'Compliance', 'Energy', 'Vendors']);
  cleanup();
});

test('nothing urgent: a calm pill, the other items still listed, and the Urgent section says so', () => {
  live([approval()]);
  const v = c.renderVals();
  assert.equal(v.queueBadgeText, 'Nothing urgent');
  assert.equal(v.queueBadgeHot, false);
  assert.equal(v.queueItems.length, 2);
  assert.equal(v.queueNoUrgentShow, 'block');
  assert.equal(v.queueEmptyShow, 'none');
  cleanup();
});

test('queueBadge (pure): unread, nothing urgent, and only urgent non-info items counted', () => {
  assert.deepEqual(queueBadge({ live: false, items: [] }, {}, 0), { total: null, urgent: null, snoozed: 0, text: '— urgent', hot: false });
  assert.equal(queueBadge({ live: true, items: [{ key: 'b', tone: 'warn' }] }, {}, 0).text, 'Nothing urgent');
  const items = [{ key: 'a', tone: 'risk' }, { key: 'b', tone: 'warn' }, { key: 'c', tone: 'risk', info: true }, { key: 'd', tone: 'risk' }];
  assert.equal(queueBadge({ live: true, items }, {}, 0).text, '2 urgent');
  assert.equal(queueBadge({ live: true, items }, { a: 10 }, 0).text, '1 urgent', 'a snoozed item leaves the count');
});

// ── cards: open and snooze ───────────────────────────────────────────────

test('every card has Open and Snooze; Open is the card click', () => {
  live([approval(), lapsed]);
  const card = c.renderVals().queueItems[0];
  assert.equal(typeof card.open, 'function');
  assert.equal(typeof card.snooze, 'function');
  assert.equal(card.open, card.click);
  cleanup();
});

test('a snoozed card leaves the list, the chips and the pill, in this browser, until tomorrow', () => {
  live([approval(), lapsed, approval({ id: 'a3', summary: 'Vendor blocked — Apex Lifts', severity: 'critical', source_feature: 'A' })]);
  let v = c.renderVals();
  assert.equal(v.queueBadgeText, '2 urgent');
  assert.equal(v.queueFilterOpts.find((f) => f.label === 'All').n, '4');
  v.queueItems.find((i) => /FRA lapsed/.test(i.title)).snooze();
  v = c.renderVals();
  assert.equal(v.queueItems.length, 3);
  assert.equal(v.queueBadgeText, '1 urgent');
  assert.equal(v.queueUrgentLabel, 'Urgent · 1');
  assert.equal(v.queueFilterOpts.find((f) => f.label === 'All').n, '3', 'the chip moves with the list');
  assert.match(v.queueSnoozedNote, /1 snoozed until tomorrow/);
  assert.match(v.queueSnoozedNote, /this browser/i);
  assert.equal(v.queueSnoozedShow, 'flex');
  const saved = loadQueueSnoozes(Date.now());
  assert.equal(Object.keys(saved).length, 1);
  assert.ok(Object.keys(loadQueueSnoozes(Date.now() + 25 * 3600 * 1000)).length === 0, 'gone after a day');
  cleanup();
});

test('snoozed cards are shown in their own section, outside every count, and can be put back', () => {
  live([approval(), lapsed]);
  c.renderVals().queueUrgentItems[0].snooze();
  c.renderVals().queueToggleSnoozed();
  let v = c.renderVals();
  assert.equal(v.queueItems.length, 2, 'the counted list stays as counted');
  assert.equal(v.queueSnoozedItems.length, 1, 'snoozed shown apart');
  assert.equal(v.queueCount, 0);
  assert.equal(v.queueFilterOpts.find((f) => f.label === 'All').n, '2');
  v.queueSnoozedItems[0].unsnooze();
  v = c.renderVals();
  assert.equal(v.queueCount, 1);
  assert.equal(v.queueItems.length, 3);
  assert.equal(v.queueSnoozedShow, 'none');
  cleanup();
});

test('a new company starts with no snoozes from another', () => {
  live([lapsed]);
  c.renderVals().queueUrgentItems[0].snooze();
  assert.equal(c.renderVals().queueCount, 0);
  c.setState({ viewOrgId: 'other-co' });
  assert.equal(c.renderVals().queueCount, 1, 'snoozes are kept per company');
  cleanup();
});

// ── settings behind a button ─────────────────────────────────────────────

test('Runs and Notify sit behind the settings button', () => {
  live([approval()]);
  let v = c.renderVals();
  assert.equal(v.queueSettingsShow, 'none');
  v.queueSettingsToggle();
  v = c.renderVals();
  assert.equal(v.queueSettingsShow, 'flex');
  assert.equal(v.queueSettingsPressed, true);
  cleanup();
});
