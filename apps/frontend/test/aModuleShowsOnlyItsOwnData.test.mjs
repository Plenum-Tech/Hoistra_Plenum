// A module shows its own data and nothing else (Hussain, 2 Nov 2026: "Each module should separate
// the data specific to it. For example you cannot have compliance certificates shown in the
// OpsBrain. It is not part of the module").
//
// An instance listed its own reports in the navigator and drew its own Home cards, and nothing
// else knew which one was in view. So in Ops Brain — Buildings, Maintenance and Vendors — the
// top bar's "N Pending" counted every lapsed certificate on the compliance register, the Platform
// value card priced compliance, and the Sessions list carried conversations about certificates
// asked in the Compliance Engine. The orchestrator already refuses another module's question
// (svc-deepagents agents/spaces.py, 30 Oct 2026); these are the screens around it.
//
// The admin view and the full platform are left exactly as they were: an instance shapes the
// USER view only (instances.js header).
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, innerWidth: 1470 };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.window.addEventListener = () => {};
globalThis.window.removeEventListener = () => {};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { instanceValue, instanceOf, instanceShowsModule, instanceShowsSession } = await import('../src/logic/instances.js');

const ME = 'reader@plenum.example';
const at = new Date().toISOString();
// What the unified approvals queue answers: one compliance item (source A), one vendor item (B).
const APPROVALS = { items: [
  { id: 'a1', source_feature: 'A', summary: 'Fire Alarm System Service Certificate lapsed', item_type: 'certificate_expiry', severity: 'high', created_at: at },
  { id: 'b1', source_feature: 'B', summary: 'Invoice line held for Larkfield', item_type: 'invoice_dispute', severity: 'medium', created_at: at }
] };
const ANOMALIES = { anomalies: [{ id: 'e1', status: 'open', anomaly_type: 'baseload_drift', meter_id: 'M-1', detected_at: at }] };

let c;
const boot = (instance, role) => {
  c = new HoistraLogic();
  c.setState({
    signedIn: true, view: 'home', role: role || 'user', instance: instance, navOpen: true,
    account: { id: 'u1', email: ME, role: role || 'user', full_name: 'Reader' },
    homeRaw: { approvals: APPROVALS, anomalies: ANOMALIES },
    sessions: [
      { id: 's-cert', kind: 'chat', title: 'What certificates does Bishopsgate Tower hold?', label: 'What certificates does Bishopsgate Tower hold?', page: 'Home', domain: 'Compliance', owner: ME, at: 5, turns: [], calls: ['compliance_query'] },
      { id: 's-book', kind: 'task', title: 'Approve booking — Fire Alarm System Service Certificate', label: 'Approve booking — Fire Alarm System Service Certificate', page: 'Compliance', domain: 'Orchestrator', owner: ME, at: 4, turns: [], calls: [] },
      { id: 's-wo', kind: 'chat', title: 'Which work orders are overdue?', label: 'Which work orders are overdue?', page: 'Maintenance', domain: 'Work orders', owner: ME, at: 3, turns: [], calls: [] },
      { id: 's-sla', kind: 'chat', title: 'Is each vendor inside its SLA?', label: 'Is each vendor inside its SLA?', page: 'Vendors', domain: 'Vendors', owner: ME, at: 2, turns: [], calls: [] },
      { id: 's-energy', kind: 'chat', title: 'Why is meter M-1 spiking?', label: 'Why is meter M-1 spiking?', page: 'Home', domain: 'Energy', owner: ME, at: 1, turns: [], calls: [] }
    ]
  });
  return c;
};
const cleanup = () => { if (c) { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); } };
beforeEach(cleanup);

test('Ops Brain: the Pending queue holds no compliance or energy item', () => {
  boot('ops');
  const v = c.renderVals();
  const modules = v.queueItems.map((i) => i.module);
  assert.deepEqual(modules, ['Vendors'], 'the lapsed certificate and the meter anomaly are not Ops Brain\'s');
  assert.equal(v.queueCount, 1, 'the badge counts what this module has waiting');
  assert.deepEqual(v.queueFilterOpts.map((f) => f.label), ['All', 'Vendors', 'Maintenance', 'Platform'],
    'no Compliance or Energy chip in a module that has neither');
  cleanup();
});

test('the Compliance Engine: compliance and vendor items, no energy', () => {
  boot('comp');
  const v = c.renderVals();
  assert.deepEqual(v.queueItems.map((i) => i.module).sort(), ['Compliance', 'Vendors']);
  assert.deepEqual(v.queueFilterOpts.map((f) => f.label), ['All', 'Vendors', 'Compliance', 'Platform']);
  cleanup();
});

test('the full platform and the admin view keep every item', () => {
  boot('full');
  assert.equal(c.renderVals().queueItems.length, 3);
  boot('ops', 'admin');
  const v = c.renderVals();
  assert.equal(v.queueItems.length, 3, 'an instance shapes the user view only');
  assert.equal(v.queueFilterOpts.length, 6);
  cleanup();
});

test("Ops Brain's Platform value is maintenance and vendors, not compliance", () => {
  assert.deepEqual(instanceOf('ops').value, ['maintenance', 'vendors']);
  const pv = instanceValue({ year: 2026, ledger: { modules: [
    { key: 'maintenance', name: 'Maintenance', counted: true, detected: 100, saved: 0, items: [] },
    { key: 'vendors', name: 'Vendors', counted: true, detected: 50, saved: 0, items: [] },
    { key: 'compliance', name: 'Compliance', counted: true, detected: 1000, saved: 0, items: [] }
  ] } }, instanceOf('ops').value);
  assert.deepEqual(pv.rows.map((r) => r.key), ['maintenance', 'vendors']);
  assert.equal(pv.total, '£150');
});

test("Ops Brain's Sessions list leaves out conversations and tasks about compliance and energy", () => {
  boot('ops');
  const v = c.renderVals();
  const titles = v.navSessions.map((x) => x.label || x.title);
  assert.equal(titles.length, 2, JSON.stringify(titles));
  assert.ok(titles.some((t) => /work orders/.test(t)));
  assert.ok(titles.some((t) => /SLA/.test(t)));
  assert.ok(!titles.some((t) => /certificate|Fire Alarm|meter/i.test(t)), 'no certificate, booking or meter session');
  cleanup();
});

test("the Compliance Engine's Sessions list leaves out work orders and energy", () => {
  boot('comp');
  const titles = c.renderVals().navSessions.map((x) => x.label || x.title);
  assert.equal(titles.length, 3, JSON.stringify(titles));
  assert.ok(!titles.some((t) => /work orders|meter/i.test(t)));
  cleanup();
});

test('the full platform lists every session', () => {
  boot('full');
  assert.equal(c.renderVals().navSessions.length, 5);
  cleanup();
});

test('a label that names no module belongs to every module', () => {
  const ops = instanceOf('ops').reports;
  for (const l of ['Platform', 'Documents', 'Migration', 'Orchestrator', undefined]) assert.equal(instanceShowsModule(ops, l), true, String(l));
  assert.equal(instanceShowsModule(ops, 'Compliance'), false);
  assert.equal(instanceShowsModule(null, 'Compliance'), true, 'no instance in view, nothing hidden');
  assert.equal(instanceShowsSession(ops, { domain: 'Orchestrator', page: 'Compliance Graph' }), false, 'raised on a compliance page');
  assert.equal(instanceShowsSession(ops, { domain: 'Documents', page: 'Buildings' }), true);
});

// ── the pages Ops Brain shows (2 Nov 2026, Hussain's answers: keep a vendor's Blocked status and
// score, drop the certificate detail; hide decisions Compliance raised; no certificates on
// Buildings; the admin view unchanged) ──

const { scopeMaintenance } = await import('../src/logic/maintenanceLive.js');
const { jobsFor } = await import('../src/logic/crons.js');
const { platformNotices } = await import('../src/logic/platformNotices.js');

test("Ops Brain's Vendors page has no Coverage, Accreditations or Buildings served tab", () => {
  boot('ops');
  c.setState({ view: 'vp', vpTab: 5 });
  let v = c.renderVals();
  assert.deepEqual(v.vpTabs.map((t) => t.label), ['Scorecard', 'Contract terms', 'Evidence', 'Invoices']);
  assert.equal(v.vpPaneAccred, false, 'a tab left on Accreditations opens the Scorecard');
  assert.equal(v.vpPaneScore, true);
  assert.doesNotMatch(v.vpIntro, /compliance coverage/);
  assert.equal(v.vpDirFoot, '');
  assert.ok((v.vpList || []).every((r) => r.covShow === false && !/accreditation/i.test(r.meta)), 'no coverage column, no accreditation in a row');
  boot('comp');
  c.setState({ view: 'vp' });
  v = c.renderVals();
  assert.ok(v.vpTabs.some((t) => t.label === 'Accreditations'), 'the Compliance Engine keeps them');
  boot('ops', 'admin');
  c.setState({ view: 'vp' });
  assert.ok(c.renderVals().vpTabs.some((t) => t.label === 'Coverage'), 'the admin view is unchanged');
  cleanup();
});

test('a compliance page reached in Ops Brain shows Home, not the console', () => {
  boot('ops');
  for (const view of ['cc', 'hg']) {
    c.setState({ view });
    const v = c.renderVals();
    assert.equal(v.isCC, false, view);
    assert.equal(v.isHome, true, view);
  }
  boot('comp');
  c.setState({ view: 'cc' });
  assert.equal(c.renderVals().isCC, true);
  cleanup();
});

test("the building tree has no Certificates branch in Ops Brain", () => {
  boot('ops');
  const docs = c.bgBranches({ counts: { documents: 2, certificates: 3 } }).find((b) => b.key === 'documents');
  assert.deepEqual(docs.children.map((x) => x.key), []);
  boot('full');
  assert.deepEqual(c.bgBranches({ counts: {} }).find((b) => b.key === 'documents').children.map((x) => x.key), ['certificates']);
  cleanup();
});

test('Maintenance without Compliance leaves out the decisions Compliance raised', () => {
  const d = (src, state, statutory) => ({ src, state, statutory, statutoryNote: statutory ? 'Gas Safety Certificate · 01 Dec 2026' : '' });
  const model = {
    live: true,
    cards: [{ k: 'decisions_owed', v: 3, s: '3 owed' }, { k: 'statutory', v: 2, s: 'two' }, { k: 'ppm_to_plan', v: 88 }],
    decisions: [d('Compliance', 'Blocked', true), d('Vendors', 'Blocked', true), d('Assets', 'To raise', false)],
    groups: [{ name: 'Compliance', n: 1, items: [] }, { name: 'Vendors', n: 1, blocked: 1, items: [d('Vendors', 'Blocked', true)] }],
    total: 3, count: 3, byState: { Blocked: 2, 'To raise': 1 }, bySource: { Compliance: 1, Vendors: 1, Assets: 1 },
    available: { state: ['Blocked', 'To raise'], source: ['Compliance', 'Vendors', 'Assets'] }
  };
  const m = scopeMaintenance(model, false);
  assert.deepEqual(m.decisions.map((x) => x.src), ['Vendors', 'Assets']);
  assert.equal(m.decisions[0].statutory, true, 'statutory work stays statutory');
  assert.equal(m.decisions[0].statutoryNote, 'Statutory work', 'without the certificate behind it');
  assert.equal(m.cards[0].v, 2);
  assert.equal(m.cards[1].v, 1);
  assert.equal(m.total, 2);
  assert.deepEqual(m.byState, { Blocked: 1, 'To raise': 1 });
  assert.deepEqual(m.available.source, ['Vendors', 'Assets'], 'no Compliance filter chip');
  assert.deepEqual(m.groups.map((g) => g.name), ['Vendors']);
  assert.equal(scopeMaintenance(model, true), model, 'with Compliance, untouched');
});

test('Ops Brain cannot schedule a compliance check', () => {
  boot('ops');
  const keys = jobsFor(c.state).map((j) => j.key);
  assert.ok(!keys.some((k) => /^compliance_/.test(k)), keys.join(','));
  assert.ok(keys.includes('maintenance_sla_watch') && keys.includes('question'));
  assert.ok(!keys.some((k) => /^energy_/.test(k)), 'nor an energy one: Ops Brain has no Energy');
  boot('full');
  assert.ok(jobsFor(c.state).some((j) => j.key === 'compliance_expiry_scan'));
  cleanup();
});

test('no platform notice names a certificate where there is no Compliance', () => {
  const ns = platformNotices({ score: { value: 40, gap: 'Certificates 65% is the weakest part' }, cronJobs: [
    { id: 'j1', name: 'Compliance expiry scan', enabled: true, jobs: ['compliance_expiry_scan'], runs: [{ ok: false, finished_at: new Date().toISOString() }] }
  ], me: 'a@b.c', canManageCrons: true, compliance: false });
  const text = ns.map((n) => n.title + ' ' + n.meta).join(' | ');
  assert.doesNotMatch(text, /certificat|complian/i, text);
  assert.ok(ns.some((n) => n.key === 'platform:score'), 'the score notice stays, without the certificate gap');
});

test("the orchestrator is told Ops Brain's vendors page without accreditations", () => {
  boot('ops');
  c.setState({ view: 'vp' });
  assert.doesNotMatch(c.chatContext(), /accreditation/i);
  cleanup();
});
