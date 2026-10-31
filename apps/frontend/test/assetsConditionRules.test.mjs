// assetsConditionRules — the two steppers at the top of the Assets page.
//
// They looked like a control and were not one. `bump()` moved a number in browser state and
// stopped there: no request left the page, so the organisation's real thresholds (held in
// plenum_cafm.asset_condition_rules, and the ones the engine actually bands against) were
// never read and never written. Two consequences, both visible on screen:
//
//   · the page always opened on 10% / 3 weeks — the values this build ships with — even for
//     a company whose rule is something else, so the row stated a threshold that had not
//     decided anything;
//   · moving a stepper changed nothing that mattered. Threat / Watch / In control and the
//     four cards come from GET /api/energy/condition/assets, which bands on the SERVER's
//     thresholds. Only the section rows re-coloured, on the local number — so the sections
//     and the assets above them were being judged by two different rules at once.
//
// The backend has had both ends of this since the condition engine landed: GET and PUT
// /api/energy/condition/rules. These tests pin the page to them. Fetch is mocked per route:
// PUT /condition/rules is a live write against production, so a test proves the request and
// the state transitions, never performs one.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
let calls, bodies;
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

let handlers;
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  calls.push(method + ' ' + u.pathname);
  if (opts && opts.body) bodies.push({ path: method + ' ' + u.pathname, body: JSON.parse(opts.body) });
  const h = handlers[method + ' ' + u.pathname] || handlers[u.pathname];
  if (!h) throw new TypeError('Failed to fetch: no handler for ' + method + ' ' + u.pathname);
  const [status, body] = typeof h === 'function' ? await h(u, opts) : h;
  return { ok: status >= 200 && status < 300, status, statusText: String(status),
           text: async () => JSON.stringify(body) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { shapeLiveBuilding } = await import('../src/logic/buildingsLive.js');
const { nextRuleValue, rulesInForce, RULE_BOUNDS, RULE_SAVE_MS } =
  await import('../src/logic/assetsCondition.js');

const OI = '/backend/ops-intelligence/api/energy';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const settle = () => sleep(RULE_SAVE_MS + 120);

// What GET /api/energy/condition/assets answers with: the engine's bands AND, in `rules`,
// the thresholds it banded them under.
const CONDITION = (pct, wks, extra) => [200, {
  ok: true, method: 'rule:section-over-reference+anomaly-attributed/v1',
  rules: Object.assign({ section_over_reference_pct: pct, anomaly_persistent_weeks: wks,
                         is_default: false, updated_at: '2026-09-20T10:00:00Z' }, extra || {}),
  count: 1, total: 1,
  assets: [{ asset_id: 'a1', asset_name: 'AHU-1', building_id: 'b1',
             section_id: 's1', section: 'Server room', band: 'watch',
             reasons: ['section_over_reference'], section_deviation_pct: 22,
             section_measured: true }]
}];

// The rule as the engine holds it for this company. A static fixture would have every
// re-read answer 10% / 3 weeks whatever was just written, which is the one thing a test of
// a write must not do — the page seeds its steppers from that answer.
let stored;

let c;
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  calls = []; bodies = [];
  stored = { pct: 10, wks: 3, isDefault: true };
  handlers = {
    '/backend/work-order/api/locations': [200, { ok: true, locations: [] }],
    [OI + '/anomalies']: [200, { ok: true, anomalies: [] }],
    [OI + '/sections']: [200, { ok: true, sections: [] }],
    [OI + '/assets/value-at-risk']: [200, { ok: true, value_at_risk: 0, assets: [] }],
    [OI + '/condition/assets']: () => CONDITION(stored.pct, stored.wks, { is_default: stored.isDefault }),
    [OI + '/condition/summary']: [200, { ok: true, summary: { threat: 0, watch: 1 }, buildings: [], last_run: null }],
    ['PUT ' + OI + '/condition/rules']: (u, o) => {
      const b = JSON.parse(o.body);
      stored = { pct: b.section_over_reference_pct, wks: b.anomaly_persistent_weeks, isDefault: false };
      return [200, { ok: true, section_over_reference_pct: stored.pct,
                     anomaly_persistent_weeks: stored.wks,
                     is_default: false, updated_at: '2026-09-21T12:00:00Z' }];
    }
  };
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'module', module: 'assets', role: 'user' });
});
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._asRuleTimer); };

// ── the step itself ──

test('a step moves by the stepper’s own increment and stops at its bounds', () => {
  assert.equal(nextRuleValue('asPct', 10, 1), 15);
  assert.equal(nextRuleValue('asPct', 10, -1), 5);
  assert.equal(nextRuleValue('asPct', RULE_BOUNDS.asPct.hi, 1), RULE_BOUNDS.asPct.hi, 'clamped at the top');
  assert.equal(nextRuleValue('asPct', RULE_BOUNDS.asPct.lo, -1), RULE_BOUNDS.asPct.lo, 'clamped at the bottom');
  assert.equal(nextRuleValue('asWeeks', 3, 1), 4);
  assert.equal(nextRuleValue('asWeeks', 1, -1), 1);
  cleanup();
});

// ── which thresholds are actually in force ──

test('the thresholds in force are the engine’s whenever the engine gave them', () => {
  const r = rulesInForce({ asPct: 10, asWeeks: 3,
    asCondRules: { section_over_reference_pct: 25, anomaly_persistent_weeks: 6 } });
  assert.equal(r.pct, 25);
  assert.equal(r.weeks, 6);
  assert.equal(r.fromServer, true);
  assert.equal(r.pending, true, 'the steppers and the engine disagree, and that must be sayable');
  cleanup();
});

test('with no answer from the engine the steppers are all there is, and that is not pending', () => {
  const r = rulesInForce({ asPct: 10, asWeeks: 3, asCondRules: null });
  assert.equal(r.pct, 10);
  assert.equal(r.weeks, 3);
  assert.equal(r.fromServer, false);
  assert.equal(r.pending, false);
  cleanup();
});

// ── reading the organisation's rule ──

test('the steppers open on the organisation’s stored rule, not on the value this build ships with', async () => {
  stored = { pct: 25, wks: 6, isDefault: false };
  await c.asCondLoad();
  assert.equal(c.state.asPct, 25, 'not the shipped 10');
  assert.equal(c.state.asWeeks, 6, 'not the shipped 3');
  assert.equal(c.renderVals().asPct, '25%');
  assert.equal(c.renderVals().asWeeks, '6');
  cleanup();
});

// ── writing it ──

test('moving a stepper writes the rule to the engine, carrying both thresholds', async () => {
  await c.asCondLoad();
  c.renderVals().asPctUp();
  assert.equal(c.renderVals().asPct, '15%', 'the number moves at once — the save is not what redraws it');
  await settle();
  const put = bodies.find((b) => b.path === 'PUT ' + OI + '/condition/rules');
  assert.ok(put, 'a PUT /condition/rules went out');
  assert.equal(put.body.section_over_reference_pct, 15);
  assert.equal(put.body.anomaly_persistent_weeks, 3, 'the untouched threshold is sent too, not dropped');
  cleanup();
});

test('a saved rule re-reads the bands, because it is the engine that bands, not the page', async () => {
  await c.asCondLoad();
  const before = calls.filter((x) => x === 'GET ' + OI + '/condition/assets').length;
  c.renderVals().asWkUp();
  await settle();
  const after = calls.filter((x) => x === 'GET ' + OI + '/condition/assets').length;
  assert.ok(after > before, 'the bands were re-read at the new threshold');
  assert.ok(calls.includes('GET ' + OI + '/condition/summary'), 'and so were the cards');
  cleanup();
});

test('three quick clicks are one write of the value the person stopped on', async () => {
  await c.asCondLoad();
  const v = c.renderVals();
  v.asPctUp(); c.renderVals().asPctUp(); c.renderVals().asPctUp();
  await settle();
  const puts = bodies.filter((b) => b.path === 'PUT ' + OI + '/condition/rules');
  assert.equal(puts.length, 1, 'one write, not three');
  assert.equal(puts[0].body.section_over_reference_pct, 25);
  cleanup();
});

test('a step made while a write is in flight is not pulled back by that write’s answer', async () => {
  await c.asCondLoad();
  let release;
  const gate = new Promise((r) => { release = r; });
  handlers['PUT ' + OI + '/condition/rules'] = async (u, o) => {
    await gate;
    const b = JSON.parse(o.body);
    stored = { pct: b.section_over_reference_pct, wks: b.anomaly_persistent_weeks, isDefault: false };
    return [200, { ok: true, section_over_reference_pct: stored.pct,
                   anomaly_persistent_weeks: stored.wks,
                   is_default: false, updated_at: '2026-09-21T12:00:00Z' }];
  };
  c.renderVals().asPctUp();                 // 15, and the write is held open
  await settle();
  c.renderVals().asPctUp();                 // 20, while the 15 is still on the wire
  release();
  await settle();
  assert.equal(c.state.asPct, 20, 'the stepper stayed where the person put it');
  const puts = bodies.filter((b) => b.path === 'PUT ' + OI + '/condition/rules');
  assert.equal(puts[puts.length - 1].body.section_over_reference_pct, 20,
    'and the last thing written is that same value');
  cleanup();
});

test('a refused write puts the steppers back to what the engine actually holds, and says so', async () => {
  await c.asCondLoad();
  handlers['PUT ' + OI + '/condition/rules'] =
    [400, { detail: { ok: false, error: 'Thresholds are set per organisation and you have none.' } }];
  c.renderVals().asPctUp();
  await settle();
  assert.equal(c.state.asPct, 10, 'reverted to the engine’s threshold rather than left showing a lie');
  assert.match(c.renderVals().asRuleNote, /not saved/i);
  assert.match(c.renderVals().asRuleNote, /you have none/);
  cleanup();
});

test('a save landing while a read is in flight still re-reads at the new threshold', async () => {
  await c.asCondLoad();
  // Hold the next condition read open, so the save arrives while one is mid-flight.
  let release;
  const held = new Promise((r) => { release = r; });
  let served = 0;
  handlers[OI + '/condition/assets'] = () => { served += 1; return CONDITION(stored.pct, stored.wks); };
  const slow = handlers[OI + '/sections'];
  handlers[OI + '/sections'] = async () => { await held; return slow; };
  assert.equal(served, 0, 'the hold is armed before anything is counted');
  const inFlight = c.asCondLoad();          // this one is issued under the OLD rule
  c.renderVals().asPctUp();
  await settle();
  release();
  await inFlight;
  await sleep(60);
  assert.ok(served >= 2,
    'the read issued before the write is not mistaken for the answer to it');
  assert.equal(c.state.asRuleSaving, false);
  cleanup();
});

// ── the page must not judge sections by one rule and assets by another ──

test('a section is over reference by the threshold the engine banded on, not by the stepper', async () => {
  handlers[OI + '/sections'] = [200, { ok: true, sections: [
    { section_id: 's1', building_id: 'b1', name: 'Server room', measured: true,
      eui_kwh_per_m2: 240, reference_eui_kwh_m2: 200, deviation_pct: 20, meters: 1 }
  ] }];
  // The engine bands at 30%: a section 20% over its own reference is NOT over for these bands.
  stored = { pct: 30, wks: 3, isDefault: false };
  c.setState({
    bldLive: [shapeLiveBuilding({ building_id: 'b1', site_id: 'b1', code: 'B-01',
      name: 'Server House', country_code: 'UK', site_type: 'Commercial' }, 0)],
    asLive: [{ asset_id: 'a1', asset_name: 'AHU-1', building_id: 'b1', health_score: 90 }],
    asLiveWos: []
  });
  await c.asCondLoad();
  c.setState({ asPct: 10 }); // as if the stepper had been left behind on the shipped value
  const line = JSON.stringify(c.renderVals().asGroups);
  assert.match(line, /metered sections over reference/, 'the section line is on screen at all');
  assert.ok(!/1 of 1 metered sections over reference/.test(line),
    'the section must be judged at the engine\u2019s 30%, not at the stepper\u2019s 10%');
  cleanup();
});
