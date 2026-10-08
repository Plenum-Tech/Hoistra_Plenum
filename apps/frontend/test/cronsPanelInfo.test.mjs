// Hoist Crons on Home, as information (Hussain with Aasim, 8 Oct 2026). The panel showed each job
// squeezed beside Run / Resume / × — the names came out as "C.", "W.", "M." — and under them the
// engines' whole activity feed, which is what Notifications is for. Crons are the checks a person
// set up for themselves: the panel says, for each of yours, what it watches, whether it is running
// successfully and what it found. Running, pausing and removing are on the Hoist Crons page.
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { cronsApi } = await import('../src/api/crons.js');

const real = { create: cronsApi.create, update: cronsApi.update, list: cronsApi.list, daily: cronsApi.daily, catalogue: cronsApi.catalogue };
let c = null;
afterEach(() => {
  Object.assign(cronsApi, real);
  // _bldRetry: the schedule card reads the Buildings register for its picker, and retries.
  if (c) { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._vpRetry); clearTimeout(c._qTimer); clearTimeout(c._homeRefresh); clearTimeout(c._bldRetry); }
  c = null;
});

const ME = 'hussain@plenum.example';
const ago = (min) => new Date(Date.now() - min * 60000).toISOString();
const later = (min) => new Date(Date.now() + min * 60000).toISOString();
const JOBS = [
  { id: 'j1', name: 'Compliance expiry scan', label: 'Compliance expiry scan', module: 'Compliance', enabled: true, status: 'ready',
    refresh_label: 'Every day at 07:00', next_run_at: later(600), created_by: { email: ME }, params: {},
    runs: [{ ok: true, finished_at: ago(120), summary: { certificates_checked: 70, lapsed: 11 } }] },
  { id: 'j2', name: 'Work-order SLA watch', label: 'Work-order SLA watch', module: 'Maintenance', enabled: true, status: 'error',
    refresh_label: 'Every 6 hours', next_run_at: later(240), created_by: { email: 'HUSSAIN@plenum.example' }, params: { building_name: 'Harbour Point' },
    runs: [{ ok: false, finished_at: ago(30), error: 'work-order service did not answer' }] },
  { id: 'j3', name: 'Morning brief', label: 'Ask a question', module: 'Orchestrator', enabled: false, status: 'paused',
    refresh_label: 'Every day at 08:00', created_by: { email: 'bala@plenum.example' }, params: { prompt: 'What needs my approval today?' }, runs: [] }
];
const boot = (state) => {
  c = new HoistraLogic({});
  // bldLive: the schedule card's building picker reads the register, and retries a failed read.
  c.setState(Object.assign({ signedIn: true, bldLive: [], account: { email: ME, role: 'admin' }, cronJobs: JOBS, cronLoadedAt: 1, cronCanManage: true,
    cronDaily: { j1: [{ date: '2026-10-07', status: 'ok', runs: 1, failed: 0 }] } }, state));
  return c;
};

test('a row is one plain sentence: what it found, when it ran, when it runs next — and no controls', () => {
  boot();
  const r = c.renderVals().cronRows.find((x) => x.id === 'j1');
  assert.equal(r.name, 'Compliance expiry scan');
  assert.equal(r.health, 'Healthy');
  assert.equal(r.line, '11 lapsed — ran 2 h ago, next in 10 h', 'what needs attention, not the 70 it looked at');
  assert.match(r.title, /Every day at 07:00/);
  for (const k of ['run', 'pause', 'pauseLabel', 'remove', 'removeArmed', 'running']) assert.equal(k in r, false, k + ' is a control, not information');
});

test('a failed run says so, and says why, in words a person reads', () => {
  boot();
  const r = c.renderVals().cronRows.find((x) => x.id === 'j2');
  assert.equal(r.health, 'Failed');
  assert.equal(r.line, 'Failed 30 min ago: work-order service did not answer');
  assert.equal(r.where, 'Harbour Point');
});

test('the figures a run reports read as plain words, attention first', async () => {
  const { findingsOf } = await import('../src/logic/crons.js');
  assert.equal(findingsOf({ blocks_set: 7, alerts_created: 29, vendor_scanned: 58, adversary_failed: 0, emailed_to: 'a@x.com' }),
    '7 vendors blocked, 29 alerts raised');
  assert.equal(findingsOf({ past_sla: 9, due_in_24h: 1, work_orders_checked: 140 }), '9 past SLA, 1 due within 24 h');
  assert.equal(findingsOf({ meters_scanned: 58, alerts_created: 0 }), 'nothing needs attention (58 meters scanned)');
  assert.equal(findingsOf({ tools: 4, rich: {} }), 'answered', 'a plain user cannot open it, so no "open it"');
  assert.equal(findingsOf({}), 'ran, with nothing to report');
});

test('paused and not-yet-run say what that means', () => {
  boot({ cronJobs: [
    Object.assign({}, JOBS[0], { id: 'p1', enabled: false, status: 'paused' }),
    Object.assign({}, JOBS[0], { id: 'n1', runs: [], next_run_at: later(240) })
  ], cronScope: 'all' });
  const rows = c.renderVals().cronRows;
  assert.equal(rows.find((r) => r.id === 'p1').line, 'Paused — last run 2 h ago found 11 lapsed');
  assert.equal(rows.find((r) => r.id === 'n1').line, 'First run in 4 h');
});

test('the panel shows your own crons, with the company\'s one switch away', () => {
  boot();
  let v = c.renderVals();
  assert.deepEqual(v.cronRows.map((r) => r.id), ['j1', 'j2'], 'yours — the email matched whatever its case');
  assert.deepEqual(v.cronScopeOpts.map((o) => o.label + ' ' + o.n), ['Yours 2', 'Company 3']);
  assert.equal(v.cronSummary, '1 of 2 failed');
  assert.match(v.cronSummaryFull, /1 of 2 failed its last run: Work-order SLA watch/);
  v.cronScopeOpts[1].pick();
  v = c.renderVals();
  assert.deepEqual(v.cronRows.map((r) => r.id), ['j1', 'j2', 'j3']);
  const brief = v.cronRows.find((r) => r.id === 'j3');
  assert.equal(brief.health, 'Paused');
  assert.equal(brief.line, 'Paused before its first run');
  assert.match(brief.title, /What needs my approval today\?/);
});

test('a row opens that job on the Hoist Crons page; a plain user\'s rows are not links', () => {
  boot();
  c.renderVals().cronRows[0].open();
  assert.equal(c.state.view, 'crons');
  assert.equal(c.state.cpJobId, 'j1');
  boot({ cronCanManage: false, account: { email: ME, role: 'user' } });
  assert.equal(c.renderVals().cronRows[0].open, null);
});

test('the empty states say what is true', () => {
  // "Yours" picked by hand: with none of your own the panel would open on the company's.
  boot({ cronJobs: [JOBS[2]], cronScope: 'mine' });
  let v = c.renderVals();
  assert.equal(v.cronRows.length, 0);
  assert.match(v.cronEmpty, /None of yours yet/);
  boot({ cronJobs: [] });
  assert.match(c.renderVals().cronEmpty, /No Hoist Crons yet/);
  boot({ cronJobs: [], cronLoadedAt: null });
  assert.match(c.renderVals().cronEmpty, /Reading/);
  boot({ cronJobs: [], cronLoadedAt: null, cronLoadErr: 'timed out' });
  assert.match(c.renderVals().cronEmpty, /could not be read/);
});

// ── the 8 Oct 2026 review ─────────────────────────────────────────────────────────────
test('someone with no crons of their own opens on the company\'s, not an empty "Yours"', () => {
  // A plain user cannot create crons, and a super-admin viewing as a company did not create
  // that company's: "Yours" would always be empty for them.
  boot({ cronJobs: [JOBS[2]], cronCanManage: false, account: { email: ME, role: 'user' } });
  const v = c.renderVals();
  assert.deepEqual(v.cronRows.map((r) => r.id), ['j3']);
  assert.equal(v.cronScopeOpts.find((o) => o.on).key, 'all');
  boot();
  assert.equal(c.renderVals().cronScopeOpts.find((o) => o.on).key, 'mine', 'with crons of your own, Yours first');
});

test('a run whose email did not go says so — delivery is the point of an "email me" job', () => {
  const j = Object.assign({}, JOBS[0], { runs: [{ ok: true, finished_at: ago(20),
    summary: { meters_scanned: 58, emailed_to: 'ops@x.com', email_status: 'failed', email_failed: 'ops@x.com' } }] });
  boot({ cronJobs: [j] });
  const r = c.renderVals().cronRows[0];
  assert.equal(r.health, 'Email failed');
  assert.match(r.line, /email to ops@x\.com failed/);
});

test('a building already in the job\'s name is not said twice', () => {
  const j = Object.assign({}, JOBS[1], { name: 'Work-order SLA watch · Harbour Point' });
  boot({ cronJobs: [j] });
  assert.equal(c.renderVals().cronRows[0].where, '');
});

test('a run timed a moment in the future (clock skew) reads "just now", not "in 0 min"', () => {
  const j = Object.assign({}, JOBS[0], { runs: [{ ok: true, finished_at: later(0.2), summary: { lapsed: 1 } }] });
  boot({ cronJobs: [j] });
  assert.match(c.renderVals().cronRows[0].line, /ran just now/);
});
