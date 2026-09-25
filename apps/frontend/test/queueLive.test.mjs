// The Decision queue ("N Pending") shaped from the live reads: pending approvals, open
// energy anomalies and maintenance decisions owed — ranked by consequence, seed until any
// read answers, each live card opening the record's own detail drawer.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { shapeLiveQueue, nextQueueDelay } = await import('../src/logic/queueLive.js');
const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { opsApi } = await import('../src/api/opsIntelligence.js');

const approval = (over) => Object.assign({
  id: 'a1', item_type: 'invoice_flag_high_value', summary: 'Invoice INV-2847 — 2 of 12 lines flagged',
  severity: 'high', source_feature: 'B', created_at: '2026-09-24T10:00:00Z',
  payload: { line: { delta_gbp: 230.25 } }
}, over || {});
const anomaly = (over) => Object.assign({
  id: 'n1', anomaly_type: 'weekend_spike', status: 'open', detected_at: '2026-09-25T02:00:00Z',
  metric_pct: 303, financial_gbp: 38400, meter_id: 'MPAN-1'
}, over || {});

test('not live until a read answers; live once one does', () => {
  const cold = shapeLiveQueue({});
  assert.equal(cold.live, false);
  assert.equal(cold.count, null);
  const warm = shapeLiveQueue({ approvals: { items: [] } });
  assert.equal(warm.live, true);
  assert.equal(warm.count, 0);
});

test('ranked by consequence: risk before warn, and priced before unpriced within a band', () => {
  const m = shapeLiveQueue({
    approvals: { items: [
      approval({ id: 'low', severity: 'medium', payload: {}, created_at: '2026-09-25T09:00:00Z' }),
      approval({ id: 'blocked', severity: 'critical', payload: {}, summary: 'Vendor blocked' })
    ] },
    anomalies: { anomalies: [anomaly()] },
    decisions: [{ id: 'WO-1', asset: 'Chiller 2', b: 'Harbour Point', vendor: 'Meridian',
      est: '£1.8k', estimated: 1800, state: 'Blocked', src: 'Vendors', trigger: 'x',
      detail: "Work order held at status 'Blocked'", due: '—', statutory: false, statutoryNote: '' }]
  });
  // Both risk items lead; within the band the priced one (the £1.8k blocked WO) outranks
  // the unpriced critical approval.
  assert.deepEqual(m.items.map((i) => i.kind), ['decision', 'approval', 'anomaly', 'approval']);
  assert.equal(m.items[0].tone, 'risk');
  assert.equal(m.items[0].money, '£1.8k estimate');
  assert.equal(m.items[2].money, '£38,400/yr');
  assert.equal(m.count, 4);
});

test('an acknowledged anomaly is not waiting on anyone, and no figure is invented', () => {
  const m = shapeLiveQueue({ anomalies: { anomalies: [
    anomaly({ status: 'acknowledged' }),
    anomaly({ id: 'n2', financial_gbp: null })
  ] } });
  assert.equal(m.items.length, 1);
  assert.equal(m.items[0].money, 'Unpriced');
  assert.equal(m.items[0].moneyValue, null);
});

test('the consequence tag prices the payload when it carries a figure, else says the severity', () => {
  const m = shapeLiveQueue({ approvals: { items: [
    approval(),
    approval({ id: 'a2', payload: {}, severity: 'medium', source_feature: 'A', item_type: 'verification_human' })
  ] } });
  const inv = m.items.find((i) => i.item.id === 'a1');
  const hum = m.items.find((i) => i.item.id === 'a2');
  assert.equal(inv.money, '£230 in dispute');
  assert.equal(inv.module, 'Vendors');
  assert.equal(hum.money, 'Medium severity');
  assert.equal(hum.module, 'Compliance');
  assert.match(hum.meta, /Verification human · raised/);
});

// ── the controller: seed until a read answers, then the live queue ──────────────────────

test('the drawer keeps its seed cards until a read answers, then shows the live queue', () => {
  const c = new HoistraLogic({});
  const seed = c.renderVals();
  assert.ok(seed.queueItems.length >= 5, 'seed cards until the backend answers');
  assert.equal(seed.queueEmptyShow, 'none');

  c.setState({ homeRaw: { approvals: { items: [approval()] }, anomalies: { anomalies: [anomaly()] } } });
  const v = c.renderVals();
  assert.equal(v.queueCount, 2);
  assert.equal(v.queueItems.length, 2);
  assert.equal(v.queueItems[0].module, 'Energy');            // £38,400/yr outranks £230
  assert.equal(v.queuePreview.length, 2);

  // A live card opens the record's own detail drawer and closes the queue.
  c.setState({ queueOpen: true });
  v.queueItems[1].click();
  assert.equal(c.state.queueOpen, false);
  assert.equal(c.state.detail.module, 'Vendor');
  assert.match(c.state.detail.title, /INV-2847/);
  clearTimeout(c._homeRetry); clearTimeout(c._vpRetry); clearTimeout(c._ccRetry); clearInterval(c._orchTick);
});

test('live with nothing waiting says so instead of an empty drawer', () => {
  const c = new HoistraLogic({});
  c.setState({ homeRaw: { approvals: { items: [] } } });
  const v = c.renderVals();
  assert.equal(v.queueCount, 0);
  assert.deepEqual(v.queueItems, []);
  assert.equal(v.queueEmptyShow, 'block');
  assert.match(v.queueEmptyNote, /Nothing is waiting/);
  clearTimeout(c._homeRetry); clearTimeout(c._vpRetry); clearTimeout(c._ccRetry); clearInterval(c._orchTick);
});

test('a maintenance decision in the queue opens the same drawer the Maintenance grid opens', () => {
  const c = new HoistraLogic({});
  c.setState({ mxRaw: { decisions: { decisions: [{
    work_order: 'WO-B-101-32', asset: 'Chiller 2 — roof plant (standby)', building: 'Harbour Point',
    vendor: 'Meridian Mechanical Ltd', estimated_cost: 1800, currency: 'GBP', state: 'Blocked',
    source: 'Vendors', trigger: 'accreditation', detail: "Work order held at status 'Blocked'"
  }] } } });
  const v = c.renderVals();
  assert.equal(v.queueCount, 1);
  assert.match(v.queueItems[0].title, /WO-B-101-32/);
  c.setState({ queueOpen: true });
  v.queueItems[0].click();
  assert.equal(c.state.queueOpen, false);
  assert.equal(c.state.detail.module, 'Maintenance');
  assert.equal(c.state.detail.fields.find((f) => f.l === 'Work order').v, 'WO-B-101-32');
  clearTimeout(c._homeRetry); clearTimeout(c._vpRetry); clearTimeout(c._ccRetry); clearInterval(c._orchTick);
});


// ── one card per underlying thing ────────────────────────────────────────────────────────

test('an approvals echo of an anomaly the read returned is dropped; without the read it stays', () => {
  const echo = approval({ id: 'e1', item_type: 'energy_anomaly_asset_spike', source_feature: 'C',
    summary: 'Energy anomaly asset_spike on meter M: 253% · est GBP 38986/yr excess. Actions: …',
    payload: { anomaly_id: 'n1', financial_gbp: 38986 }, related_entity_type: 'energy_anomaly', related_entity_id: 'n1' });
  const both = shapeLiveQueue({ approvals: { items: [echo] }, anomalies: { anomalies: [anomaly()] } });
  assert.equal(both.items.length, 1, 'the anomaly row is the record; its echo says it twice');
  assert.equal(both.items[0].kind, 'anomaly');
  const alone = shapeLiveQueue({ approvals: { items: [echo] } });
  assert.equal(alone.items.length, 1, 'with no anomalies read, the echo is all there is');
  assert.equal(alone.items[0].kind, 'approval');
});

// ── the Runs chips arm a real cadence ────────────────────────────────────────────────────

test('nextQueueDelay: each cadence is real arithmetic, On demand schedules nothing', () => {
  const now = new Date(2026, 8, 25, 14, 0, 0);
  assert.equal(nextQueueDelay('On demand', {}, now), null);
  assert.equal(nextQueueDelay('30 min', {}, now), 30 * 60000);
  assert.equal(nextQueueDelay('Hourly', {}, now), 60 * 60000);
  assert.equal(nextQueueDelay('Nightly 02:00', {}, now), 12 * 3600000);   // 14:00 → 02:00 next day
  assert.equal(nextQueueDelay('Custom', { cFreq: 'Every 6 hours' }, now), 6 * 3600000);
  assert.equal(nextQueueDelay('Custom', { cFreq: 'Daily', cTime: '15:30' }, now), 90 * 60000);
});

test('picking a cadence arms the timer; On demand disarms it and refreshes on open instead', () => {
  const c = new HoistraLogic({});
  c.setState({ signedIn: true });
  c.queueSetFreq('Hourly');
  assert.ok(c._qTimer, 'a timer is armed');
  assert.equal(c.state.freq, 'Hourly');
  c.queueSetFreq('On demand');
  // Node keeps cleared handles truthy; the disarm is observable through nextQueueDelay + the
  // open-refresh below, so assert the state and behaviour rather than the handle.
  assert.equal(c.state.freq, 'On demand');
  let refreshed = 0;
  c.queueRefresh = async () => { refreshed += 1; };
  c.renderVals().toggleQueue();
  assert.equal(c.state.queueOpen, true);
  assert.equal(refreshed, 1, 'opening the drawer is the on-demand run');
  clearTimeout(c._qTimer); clearInterval(c._orchTick);
});

test('Save schedule arms the custom cadence itself — no words to the chat', () => {
  const c = new HoistraLogic({});
  const flashes = []; c.flash = (m) => flashes.push(m);
  c.orch = () => { throw new Error('the schedule must not be a chat message'); };
  c.setState({ signedIn: true, cFreq: 'Every 15 min', cTime: '02:00' });
  c.renderVals().saveCustom();
  assert.equal(c.state.freq, 'Custom');
  assert.ok(c._qTimer);
  assert.match(flashes[0], /Schedule saved — Every 15 min/);
  clearTimeout(c._qTimer); clearInterval(c._orchTick);
});

// ── the Notify-on chips are honest about what exists ────────────────────────────────────

test('Email and SMS say no channel is connected and stay off; In-platform toggles', () => {
  const c = new HoistraLogic({});
  const flashes = []; c.flash = (m) => flashes.push(m);
  assert.deepEqual(c.state.channels, ['In-platform']);
  c.queueToggleChannel('Email');
  c.queueToggleChannel('SMS');
  assert.deepEqual(c.state.channels, ['In-platform'], 'nothing would send, so they stay off');
  assert.match(flashes[0], /No email notification channel/);
  assert.match(flashes[1], /No SMS notification channel/);
  c.queueToggleChannel('In-platform');
  assert.deepEqual(c.state.channels, []);
  clearInterval(c._orchTick);
});

test('Push asks the browser first and stays off when notifications are unsupported', () => {
  const c = new HoistraLogic({});
  const flashes = []; c.flash = (m) => flashes.push(m);
  c.queueToggleChannel('Push');
  assert.deepEqual(c.state.channels, ['In-platform']);
  assert.match(flashes[0], /does not support notifications/);
  clearInterval(c._orchTick);
});

test('a scheduled refresh names what arrived, through the channels that are on', async () => {
  const c = new HoistraLogic({});
  const flashes = []; c.flash = (m) => flashes.push(m);
  c.setState({ homeRaw: { approvals: { items: [approval()] } } });
  const realA = opsApi.approvals, realN = opsApi.anomalies;
  opsApi.approvals = async () => ({ items: [approval(), approval({ id: 'a9', summary: 'Two confirmed contracts share a signed date', payload: {} })] });
  opsApi.anomalies = async () => ({ anomalies: [] });
  let homeLoads = 0; c.homeLoad = async () => { homeLoads += 1; };
  try {
  await c.queueRefresh();
  assert.equal(homeLoads, 0, 'the tick reads the two queue sources, not the whole Home page');
  assert.equal(flashes.length, 1);
  assert.match(flashes[0], /1 new decision — Two confirmed contracts/);
  flashes.length = 0;
  await c.queueRefresh();                                   // nothing new second time round
  assert.deepEqual(flashes, []);
  } finally { opsApi.approvals = realA; opsApi.anomalies = realN; }
  clearInterval(c._orchTick);
});


// ── review fixes ────────────────────────────────────────────────────────────────────────

test('Weekly and Monthly are anchored on the start date, so re-arming never drifts', () => {
  const now = new Date(2026, 8, 25, 14, 0, 0);                  // Fri 25 Sep 2026 14:00
  const weekly = nextQueueDelay('Custom', { cFreq: 'Weekly', cDate: '2026-09-02', cTime: '02:00' }, now);
  assert.equal(new Date(now.getTime() + weekly).getDate(), 30);  // Wed 2 Sep + 4 weeks → Wed 30 Sep
  const monthly = nextQueueDelay('Custom', { cFreq: 'Monthly', cDate: '2026-01-31', cTime: '02:00' }, now);
  const m = new Date(now.getTime() + monthly);
  assert.deepEqual([m.getMonth(), m.getDate()], [8, 30]);        // the 31st clamps to 30 Sep
  // Re-planning a moment later lands on the same instant, not 27 days further out.
  const later = new Date(now.getTime() + 24 * 3600000);
  assert.equal(later.getTime() + nextQueueDelay('Custom', { cFreq: 'Monthly', cDate: '2026-01-31', cTime: '02:00' }, later), m.getTime());
});

test('a delay beyond a day never reaches setTimeout — it waits a day and re-plans', () => {
  const c = new HoistraLogic({});
  c.setState({ signedIn: true, freq: 'Custom', cFreq: 'Monthly', cDate: '2026-01-31', cTime: '02:00' });
  let refreshed = 0;
  c.queueRefresh = async () => { refreshed += 1; };
  const real = globalThis.setTimeout; const delays = [];
  globalThis.setTimeout = (fn, ms) => { delays.push(ms); return real(() => {}, 0); };
  try { c.queueSchedule(); } finally { globalThis.setTimeout = real; }
  assert.equal(delays.at(-1), 24 * 3600000);
  assert.equal(refreshed, 0);
  clearInterval(c._orchTick);
});

test('an item the page\'s own refresh already loaded is still reported once, then never again', async () => {
  const c = new HoistraLogic({});
  const flashes = []; c.flash = (m) => flashes.push(m);
  c.setState({ homeRaw: { approvals: { items: [approval()] } } });
  c.queueSeenInit();                                              // what the reader had
  // The Home loader's own 15-min cycle lands the new row BEFORE the queue tick.
  const both = { items: [approval(), approval({ id: 'a9', summary: 'Two contracts tie', payload: {} })] };
  c.setState({ homeRaw: { approvals: both } });
  const realA = opsApi.approvals, realN = opsApi.anomalies;
  opsApi.approvals = async () => both; opsApi.anomalies = async () => ({ anomalies: [] });
  try {
    await c.queueRefresh();
    assert.equal(flashes.length, 1);
    assert.match(flashes[0], /Two contracts tie/);
    await c.queueRefresh();
    assert.equal(flashes.length, 1);
  } finally { opsApi.approvals = realA; opsApi.anomalies = realN; }
  clearInterval(c._orchTick);
});

test('signing out stops the queue timer; it does not arm while signed out', () => {
  const c = new HoistraLogic({});
  c.setState({ signedIn: true, freq: 'Hourly' });
  c.queueSchedule();
  assert.ok(c._qTimer);
  c.authSignedOut('');
  assert.equal(c._qTimer, null);
  c.queueSchedule();
  assert.equal(c._qTimer, null, 'no timer on the gate');
  clearInterval(c._orchTick);
});

// ── the "Show" filter ────────────────────────────────────────────────────────────────────

test('the Show chips filter the drawer by module; the badge keeps the full count', () => {
  const c = new HoistraLogic({});
  c.setState({ homeRaw: {
    approvals: { items: [
      approval(),                                                                    // Vendors
      approval({ id: 'c1', source_feature: 'A', item_type: 'certificate_confirm', summary: 'Lapsed: Fire Alarm', payload: {} })  // Compliance
    ] },
    anomalies: { anomalies: [anomaly(), anomaly({ id: 'n2', meter_id: 'M2' })] }    // Energy ×2
  } });
  let v = c.renderVals();
  assert.deepEqual(v.queueFilterOpts.map((o) => o.label + ' ' + o.n),
    ['All 4', 'Energy 2', 'Vendors 1', 'Compliance 1', 'Maintenance 0']);
  v.queueFilterOpts.find((o) => o.label === 'Energy').pick();
  v = c.renderVals();
  assert.equal(v.queueItems.length, 2);
  assert.ok(v.queueItems.every((i) => i.module === 'Energy'));
  assert.equal(v.queueCount, 4, 'the badge is what is waiting, not what the view shows');
  v.queueFilterOpts.find((o) => o.label === 'Maintenance').pick();
  v = c.renderVals();
  assert.deepEqual(v.queueItems, []);
  assert.equal(v.queueEmptyShow, 'block');
  assert.equal(v.queueEmptyNote, 'Nothing in Maintenance is waiting on you.');
  v.queueFilterOpts.find((o) => o.label === 'All').pick();
  assert.equal(c.renderVals().queueItems.length, 4);
  clearInterval(c._orchTick);
});

test('an unknown filter is refused, and the chosen one survives a reload', () => {
  const store = {};
  globalThis.localStorage = { getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; } };
  try {
    const c = new HoistraLogic({});
    c.queueSetFilter('Nonsense');
    assert.equal(c.state.queueFilter, 'All');
    c.queueSetFilter('Vendors');
    const d = new HoistraLogic({});
    d.queueBoot();
    assert.equal(d.state.queueFilter, 'Vendors');
    clearInterval(c._orchTick); clearInterval(d._orchTick); clearTimeout(d._qTimer);
  } finally { delete globalThis.localStorage; }
});

test('a company switch mid-refresh drops the tick instead of showing the old company\'s queue', async () => {
  const c = new HoistraLogic({});
  const realA = opsApi.approvals, realN = opsApi.anomalies;
  opsApi.approvals = async () => ({ items: [approval({ id: 'A-company-item' })] });
  opsApi.anomalies = async () => { const e = new Error('the company changed while this request was in flight'); e.staleScope = true; throw e; };
  try {
    c.setState({ homeRaw: null });                            // resetLiveData() for company B
    await c.queueRefresh({ silent: true });
    assert.equal(c.state.homeRaw, null, 'company A\'s approvals never land in B\'s state');
  } finally { opsApi.approvals = realA; opsApi.anomalies = realN; }
  clearInterval(c._orchTick);
});
