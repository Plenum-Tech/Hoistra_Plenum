// Instances (11 Oct 2026). After signing in, an admin or a user picks one of four instances;
// a superadmin goes straight to the platform console as before. The instance shapes the
// USER view only — its reports, spaces, home cards, placeholder and pinned questions — and
// leaves the admin view exactly as it was. An admin who switches to User view lands in the
// instance they picked.
//
// The real controller in Node. The live loaders are stubbed on the instance so a sign-in
// here makes no request at all; the registers the home cards read are set directly.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
const tabMem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: (k) => (k in tabMem ? tabMem[k] : null), setItem: (k, v) => { tabMem[k] = String(v); }, removeItem: (k) => { delete tabMem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.fetch = async (url) => { throw new TypeError('no network in tests: ' + url); };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { loadSession } = await import('../src/logic/session.js');
const { INSTANCES, instanceValue, complianceEngineCards } = await import('../src/logic/instances.js');

const USER = { id: 'u-1', email: 'sam@example.com', full_name: 'Sam Okafor', organization_id: 'org-1', organization_name: 'Northbridge', status: 'active', role: 'user' };
const ADMIN = Object.assign({}, USER, { id: 'u-2', email: 'ada@example.com', full_name: 'Ada Admin', role: 'admin' });
const SUPER = Object.assign({}, USER, { id: 'u-3', email: 'sadie@example.com', full_name: 'Sadie Super', role: 'superadmin' });
const resp = (user, n) => ({ ok: true, user, tokens: { access_token: 'acc-' + n, refresh_token: 'ref-' + n } });

// Every loader authEnter fires, stubbed on the instance: nothing here may reach a host.
const quiet = (x) => {
  ['loadLiveData', 'resetLiveData', 'authLoadScope', 'usLiveLoad', 'auLiveLoad', 'saLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'authStop', 'crEnsureRun']
    .forEach((k) => { x[k] = () => {}; });
  return x;
};
const fresh = () => quiet(new HoistraLogic());
const signIn = (x, user) => { x.authEnter(resp(user, 1)); return x; };
const pick = (x, key) => x.renderVals().instCards.find((c) => c.key === key).pick();
const labels = (list) => (list || []).map((x) => x.label || x.name || x.l);

// /api/value/summary, trimmed (engines/value_ledger.py).
const VALUE = {
  ok: true, year: 2026,
  ledger: {
    counted_modules: 3, total_detected: 149250.0,
    modules: [
      { key: 'energy', name: 'Energy', counted: true, detected: 41250.0, saved: 29000.0, items: [] },
      { key: 'vendors', name: 'Vendors', counted: true, detected: 13700.0, saved: 1450.0, items: [] },
      { key: 'maintenance', name: 'Maintenance', counted: false, detected: null, saved: null, note: 'No priced value yet', items: [] },
      { key: 'compliance', name: 'Compliance', counted: false, detected: null, saved: null, note: 'Not priced', items: [] },
      { key: 'assets', name: 'Assets', counted: true, detected: 94300.0, saved: 78550.0, items: [] }
    ]
  }
};

// The live compliance register as shapeLiveCompliance hands it to ccLive.
const REGISTER = {
  certs: [
    { kind: 'building', holder: 'Kingsway House', days: -12, auth: 'genuine' },
    { kind: 'building', holder: 'Kingsway House', days: 200, auth: 'suspect · 41' },
    { kind: 'building', holder: 'Harbour Point', days: 300, auth: 'genuine' },
    { kind: 'vendor', holder: 'AquaSafe', days: -3, auth: 'not checked' },
    { kind: 'vendor', holder: 'LiftCo', days: 20, auth: 'genuine' }
  ],
  buildings: [
    { name: 'Kingsway House', req: 6, on: 5, gaps: ['Legionella risk assessment'] },
    { name: 'Harbour Point', req: 6, on: 6, gaps: [] },
    { name: 'Ashgrove Court', req: 4, on: 4, gaps: [] },
    { name: 'No building on certificate', req: 0, on: 0, gaps: [] }
  ],
  vendors: [
    { name: 'AquaSafe', block: 'Blocked', worst: 'Blocked', serves: [], gaps: [] },
    { name: 'LiftCo', block: 'Clear', worst: '<30d', serves: [], gaps: [] },
    { name: 'Sparks Ltd', block: 'Clear', worst: 'OK', serves: [], gaps: [] }
  ]
};
// Laid over the register's full shape, so the console's other fields are present.
const register = (x) => Object.assign({}, x.ccData(), REGISTER);

let c;
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  Object.keys(tabMem).forEach((k) => { delete tabMem[k]; });
  c = fresh();
});
afterEach(() => {
  clearTimeout(c._tt); clearInterval(c._orchTick); clearTimeout(c._mgTimer);
});

// ── who sees the picker ────────────────────────────────────────────────────────

test('a user signing in lands on the instance picker with the four instances in order', () => {
  signIn(c, USER);
  const v = c.renderVals();
  assert.equal(c.state.view, 'instances');
  assert.equal(v.isPicker, true);
  assert.equal(v.isHome, false, 'nothing behind the picker renders a page');
  assert.deepEqual(v.instCards.map((x) => x.n + ' ' + x.name), ['01 Ops Brain', '02 Compliance Engine', '03 Assets Operating System', '04 Full platform']);
  assert.equal(v.instCards[0].reports, 'Buildings & Hoist Graph · Maintenance · Vendors');
  assert.equal(v.instCards[1].reports, 'Buildings & Hoist Graph · Compliance Graph · Compliance · Vendors');
  assert.equal(v.instCards[2].reports, 'Buildings & Hoist Graph · Energy · Assets · Maintenance · Vendors');
  assert.equal(v.instCards[3].reports, 'Buildings & Hoist Graph · Compliance · Vendors · Energy · Assets · Maintenance');
  assert.match(v.pickerSignedIn, /Northbridge/, 'the picker names the real company, never a sample one');
});

test('an admin signing in lands on the picker too, still in admin view', () => {
  signIn(c, ADMIN);
  assert.equal(c.state.view, 'instances');
  assert.equal(c.state.role, 'admin');
  assert.equal(c.renderVals().isPicker, true);
});

test('a superadmin signing in goes straight to the console, never the picker', () => {
  signIn(c, SUPER);
  const v = c.renderVals();
  assert.equal(c.state.saOn, true);
  assert.notEqual(c.state.view, 'instances');
  assert.equal(v.isPicker, false);
  assert.equal(v.instShow, false, 'no instance chip for a superadmin');
});

test('the picker card scope never names a sample building', () => {
  signIn(c, USER);
  const text = JSON.stringify(c.renderVals().instCards.map((x) => [x.icp, x.scope, x.desc]));
  ['Bishopsgate', 'Rimaal', 'Jebel Ali', 'Business Bay', 'Westpark'].forEach((n) => assert.ok(text.indexOf(n) < 0, n + ' is a prototype sample, not this company'));
});

// ── picking ───────────────────────────────────────────────────────────────────

test('an admin who picks an instance lands in the admin view exactly as before', () => {
  // The admin view with no instance at all is the reference: picking one changes nothing in it.
  const ref = fresh();
  signIn(ref, ADMIN);
  ref.setState({ view: 'home', instance: null });
  const before = ref.renderVals();

  signIn(c, ADMIN);
  pick(c, 'ops');
  const v = c.renderVals();
  assert.equal(c.state.role, 'admin');
  assert.equal(c.state.view, 'home');
  assert.equal(c.state.instance, 'ops');
  assert.equal(v.isPicker, false);
  assert.equal(v.isInstHome, false, 'the admin home is not the instance home');
  // The one addition: the top bar names the instance and switches it (Hussain, 12 Oct 2026).
  assert.equal(v.instShow, true, 'the instance chip shows in admin view too');
  assert.equal(v.instName, 'Ops Brain');
  assert.deepEqual(labels(v.navAdmin), labels(before.navAdmin));
  assert.deepEqual(labels(v.navSections), labels(before.navSections));
  assert.deepEqual(labels(v.navSpaces), labels(before.navSpaces));
  assert.deepEqual(labels(v.acctItems), labels(before.acctItems), 'the admin account menu is unchanged');
  assert.deepEqual(labels(v.pinned), labels(before.pinned));
});

test('a user who picks an instance lands on its home', () => {
  signIn(c, USER);
  pick(c, 'comp');
  const v = c.renderVals();
  assert.equal(c.state.view, 'home');
  assert.equal(c.state.role, 'user');
  assert.equal(v.isHome, true);
  assert.equal(v.isInstHome, true);
  assert.equal(v.instShow, true);
  assert.equal(v.instName, 'Compliance Engine');
});

test('an admin who switches to User view lands in the instance they picked', () => {
  signIn(c, ADMIN);
  pick(c, 'assets');
  c.renderVals().acctItems.find((a) => a.label === 'User view').click();
  const v = c.renderVals();
  assert.equal(c.state.role, 'user');
  assert.equal(c.state.view, 'home');
  assert.equal(v.isInstHome, true);
  assert.equal(v.instName, 'Assets Operating System');
});

test('an admin with no instance yet who switches to User view is asked which one', () => {
  signIn(c, ADMIN);
  c.setState({ view: 'home', instance: null });
  c.renderVals().acctItems.find((a) => a.label === 'User view').click();
  assert.equal(c.state.role, 'user');
  assert.equal(c.state.view, 'instances');
});

test('switching back to Admin view from an instance is the admin view as before', () => {
  signIn(c, ADMIN);
  pick(c, 'ops');
  c.renderVals().acctItems.find((a) => a.label === 'User view').click();
  c.renderVals().acctItems.find((a) => a.label === 'Admin view').click();
  const v = c.renderVals();
  assert.equal(c.state.role, 'admin');
  assert.equal(c.state.view, 'buildings');
  assert.equal(v.instShow, true);
  assert.equal(v.navSections.length, 0);
});

test("the admin view's top bar switches instance, and the admin stays in admin view", () => {
  signIn(c, ADMIN);
  pick(c, 'ops');
  let v = c.renderVals();
  assert.equal(v.instShow, true);
  v.instSwitch();
  assert.equal(c.state.view, 'instances');
  assert.equal(c.renderVals().isPicker, true);
  pick(c, 'comp');
  v = c.renderVals();
  assert.equal(c.state.role, 'admin');
  assert.equal(c.state.view, 'home');
  assert.equal(v.instName, 'Compliance Engine');
  assert.deepEqual(labels(v.navSections), ['Compliance Graph']);
});

// ── a superadmin viewing as a company ─────────────────────────────────────────
// Viewing as a company, a superadmin is that company's admin for the duration: picking
// "User view" asks which instance, exactly as it does an admin.
const viewAs = (x, id, name) => { x.viewAsCompany(id, name); return x; };
const toUserView = (x) => x.renderVals().acctItems.find((a) => a.label === 'User view').click();

test('a superadmin viewing as a company is asked for an instance on the way into User view', () => {
  signIn(c, SUPER);
  viewAs(c, 'org-9', 'TechCorp');
  assert.equal(c.state.role, 'admin');
  toUserView(c);
  assert.equal(c.state.role, 'user');
  assert.equal(c.state.view, 'instances');
  assert.equal(c.renderVals().isPicker, true);
  assert.match(c.renderVals().pickerSignedIn, /TechCorp/, 'the picker names the company being viewed');
  pick(c, 'comp');
  const v = c.renderVals();
  assert.equal(c.state.role, 'user');
  assert.equal(v.isInstHome, true);
  assert.equal(v.instShow, true);
  assert.equal(v.instName, 'Compliance Engine');
  assert.deepEqual(labels(v.navSections), ['Buildings', 'Compliance Graph', 'Compliance', 'Vendors']);
  assert.ok(labels(v.acctItems).indexOf('Switch instance') > -1);
  v.instSwitch();
  assert.equal(c.state.view, 'instances');
});

test('a superadmin viewing as a company keeps the admin view unchanged', () => {
  signIn(c, SUPER);
  viewAs(c, 'org-9', 'TechCorp');
  const v = c.renderVals();
  assert.equal(v.instShow, false);
  assert.ok(labels(v.acctItems).indexOf('Switch instance') < 0);
  assert.equal(v.isInstHome, false);
});

test('a superadmin on their own account is not offered instances in User view', () => {
  signIn(c, SUPER);
  c.setState({ saOn: false });
  toUserView(c);
  const v = c.renderVals();
  assert.equal(c.state.view, 'home');
  assert.equal(v.isPicker, false);
  assert.equal(v.instShow, false);
  assert.equal(v.isInstHome, false);
  assert.ok(labels(v.acctItems).indexOf('Switch instance') < 0);
});

test('each company viewed is asked afresh, and leaving the view drops the instance', () => {
  signIn(c, SUPER);
  viewAs(c, 'org-9', 'TechCorp');
  toUserView(c);
  pick(c, 'ops');
  viewAs(c, 'org-7', 'Plenum Tech');
  assert.equal(c.state.instance, null, 'another company starts with no instance');
  toUserView(c);
  assert.equal(c.state.view, 'instances');
  pick(c, 'assets');
  c.exitViewAsCompany();
  assert.equal(c.state.instance, null);
  assert.equal(c.renderVals().instShow, false);
});

test('a reload while viewing as a company keeps the instance', () => {
  signIn(c, SUPER);
  viewAs(c, 'org-9', 'TechCorp');
  toUserView(c);
  pick(c, 'ops');
  const again = fresh();
  assert.equal(again.state.viewOrgId, 'org-9');
  assert.equal(again.state.instance, 'ops');
  assert.equal(again.state.view, 'home');
  assert.equal(again.renderVals().instName, 'Ops Brain');
});

// ── switching ─────────────────────────────────────────────────────────────────

test('the instance chip and the account menu both return a user to the picker', () => {
  signIn(c, USER);
  pick(c, 'ops');
  let v = c.renderVals();
  assert.ok(labels(v.acctItems).indexOf('Switch instance') > -1);
  assert.match(v.instLine, /Ops Brain · Property manager · UK/);
  v.instSwitch();
  assert.equal(c.state.view, 'instances');
  assert.equal(c.state.role, 'user');
  pick(c, 'full');
  v = c.renderVals();
  v.acctItems.find((a) => a.label === 'Switch instance').click();
  assert.equal(c.state.view, 'instances');
});

test('an admin in User view who switches instance stays in User view', () => {
  signIn(c, ADMIN);
  pick(c, 'ops');
  c.renderVals().acctItems.find((a) => a.label === 'User view').click();
  c.renderVals().instSwitch();
  pick(c, 'comp');
  assert.equal(c.state.role, 'user');
  assert.equal(c.state.instance, 'comp');
  assert.equal(c.renderVals().isInstHome, true);
});

test('the admin account menu offers no Switch instance', () => {
  signIn(c, ADMIN);
  pick(c, 'ops');
  assert.ok(labels(c.renderVals().acctItems).indexOf('Switch instance') < 0);
});

// ── the navigator ─────────────────────────────────────────────────────────────

test('each instance lists only its own reports, in the navigator order', () => {
  signIn(c, USER);
  const want = {
    ops: ['Buildings', 'Vendors', 'Maintenance'],
    comp: ['Buildings', 'Compliance Graph', 'Compliance', 'Vendors'],
    assets: ['Buildings', 'Vendors', 'Energy', 'Assets', 'Maintenance'],
    full: ['Buildings', 'Compliance', 'Vendors', 'Energy', 'Assets', 'Maintenance']
  };
  Object.keys(want).forEach((k) => {
    c.setState({ view: 'instances' });
    pick(c, k);
    assert.deepEqual(labels(c.renderVals().navSections), want[k], k);
  });
});

test('spaces are filtered to the instance; Support and saved spaces stay', () => {
  signIn(c, USER);
  c.setState({ spaces: [{ id: 's1', name: 'Board pack', created_by: USER.email, created_at: '2026-10-01' }] });
  const builtin = (v) => labels(v.navSpaces).filter((n) => n !== 'Support' && n !== 'Board pack');
  pick(c, 'ops');
  let v = c.renderVals();
  assert.deepEqual(builtin(v), ['Vendor performance', 'Vendor operations']);
  assert.ok(labels(v.navSpaces).indexOf('Support') > -1);
  assert.ok(labels(v.navSpaces).indexOf('Board pack') > -1);
  c.setState({ view: 'instances' }); pick(c, 'comp');
  assert.deepEqual(builtin(c.renderVals()), ['Compliance', 'Vendor performance']);
  c.setState({ view: 'instances' }); pick(c, 'assets');
  assert.deepEqual(builtin(c.renderVals()), ['Energy', 'Vendor performance', 'Vendor operations']);
  c.setState({ view: 'instances' }); pick(c, 'full');
  assert.deepEqual(builtin(c.renderVals()), ['Compliance', 'Energy', 'Vendor performance', 'Vendor operations']);
});

test('custom reports show only in the full platform', () => {
  signIn(c, USER);
  c.setState({ reportsOwner: USER.email, reports: [{ id: 'r1', name: 'Risky Buildings', cards: [{ id: 'k1', name: 'Risky Buildings', prompt: 'Which buildings put me at risk?', runs: [] }] }] });
  pick(c, 'ops');
  let v = c.renderVals();
  assert.equal((v.navReports || []).length, 0);
  assert.equal(v.navReportAdd, false, 'no + for a report this instance would not show');
  c.setState({ view: 'instances' }); pick(c, 'full');
  v = c.renderVals();
  assert.equal(v.navReportAdd, true);
});

// ── the home ──────────────────────────────────────────────────────────────────

test('an instance home has its kicker, placeholder and pinned questions', () => {
  signIn(c, USER);
  pick(c, 'ops');
  let v = c.renderVals();
  assert.equal(v.instKicker, 'Instance 01 · Ops Brain · Property manager · UK');
  assert.equal(v.homePh, 'What needs my decision this week?');
  assert.deepEqual(labels(v.pinned), ['What needs my decision this week?', 'Which vendor is costing me most?', 'Which recommendations were never actioned?', 'Is PPM running to plan?']);
  c.setState({ view: 'instances' }); pick(c, 'comp');
  v = c.renderVals();
  assert.equal(v.instKicker, 'Instance 02 · Compliance Engine · Regulated estates · UAE hospital');
  assert.deepEqual(labels(v.pinned), ['Which buildings are out of compliance?', 'Which vendors are not eligible?', 'What lapses in the next 30 days?', 'Which certificates failed authenticity?']);
});

test('a pinned question is asked in the orchestrator, not opened as a page', () => {
  signIn(c, USER);
  pick(c, 'ops');
  let asked = null;
  c.askScoped = (q) => { asked = q; };
  c.renderVals().pinned[0].run();
  assert.equal(asked, 'What needs my approval today?');
});

test('the full platform home is the home as it was', () => {
  signIn(c, USER);
  pick(c, 'full');
  const v = c.renderVals();
  assert.equal(v.isInstHome, false);
  assert.equal(v.homePh, 'Which buildings put me at risk this month?');
  assert.ok(labels(v.pinned).indexOf('What needs my approval today?') > -1);
});

test('Ops Brain cards: platform value over its own modules, decisions, recommendations, vendors', () => {
  signIn(c, USER);
  pick(c, 'ops');
  c.setState({
    homeRaw: { value: VALUE },
    mxRaw: { overview: { cards: {
      decisions_owed: { value: 5, caption: '2 blocked or not yet raised' },
      statutory: { value: 1 },
      recommendations_unconverted: { value: 3, caption: 'from 5 inspection reports' },
      ppm_to_plan: { value: 88 }
    } } }
  });
  const empty = c.vpModel();
  c.vpModel = () => Object.assign({}, empty, { live: true, V: {}, vendors: [
    { id: 'v1', name: 'Apex', score: 46.6, blocked: false },
    { id: 'v2', name: 'LiftCo', score: 91, blocked: false },
    { id: 'v3', name: 'Sparks', score: 72, blocked: false }
  ] });
  const cards = c.renderVals().instHomeCards;
  assert.deepEqual(cards.map((x) => x.l), ['Platform value · 2026', 'Decisions owed', 'Recommendations open', 'Vendors below 80']);
  // Maintenance and Compliance are not priced in this ledger; Vendors is the only figure.
  assert.equal(cards[0].v, '£14k');
  assert.match(cards[0].s, /Maintenance — · Vendors £14k · Compliance —/);
  assert.equal(cards[1].v, '5');
  assert.equal(cards[1].s, '2 blocked or not yet raised');
  assert.equal(cards[2].v, '3');
  assert.equal(cards[3].v, '2 of 3');
  assert.match(cards[3].s, /Apex 47 · Sparks 72/);
});

test('Assets OS platform value sums Energy, Assets, Maintenance and Vendors only', () => {
  signIn(c, USER);
  pick(c, 'assets');
  c.setState({ homeRaw: { value: VALUE } });
  const v = c.renderVals();
  assert.equal(v.instHomeCards[0].l, 'Platform value · 2026');
  assert.equal(v.instHomeCards[0].v, '£149k');
  assert.deepEqual(v.instHomeCards.slice(1).map((x) => x.l), ['Threat', 'Watch', 'Est. asset value at risk']);
  // The ledger the card opens is the instance's own modules, in the instance's order.
  assert.deepEqual(v.pvLedger.map((m) => m.mod), ['Energy', 'Assets', 'Maintenance', 'Vendors']);
  assert.equal(v.pvTotal, '£149k');
});

test('the Compliance Engine has no platform value card; its cards read the live register', () => {
  signIn(c, USER);
  pick(c, 'comp');
  c.setState({ ccLive: register(c), homeRaw: { value: VALUE } });
  const cards = c.renderVals().instHomeCards;
  assert.deepEqual(cards.map((x) => x.l), ['Buildings compliant', 'Lapsed', 'Failed checks', 'Vendors not eligible']);
  // Harbour Point and Ashgrove: no gap, nothing lapsed. Kingsway has a gap and a lapse.
  assert.equal(cards[0].v, '2 of 3');
  assert.equal(cards[1].v, '2');
  assert.equal(cards[2].v, '1');
  assert.equal(cards[3].v, '1 of 3');
  assert.match(cards[3].s, /1 more expire inside 30 days/);
});

test('before the register answers, the Compliance Engine cards say so instead of a number', () => {
  const cards = complianceEngineCards(null, { loading: true });
  cards.forEach((x) => { assert.equal(x.v, '—'); assert.match(x.s, /Reading/); });
});

test('a card whose read has not answered is a grey dash, never a status colour', () => {
  signIn(c, USER);
  pick(c, 'ops');
  const cards = c.renderVals().instHomeCards;
  cards.forEach((x) => { assert.equal(x.v, '—', x.l); assert.equal(x.color, 'var(--color-neutral-500)', x.l); });
});

test('no sample data: before the registers answer, the title line is empty and asset cards read as reading', () => {
  signIn(c, USER);
  pick(c, 'assets');
  const v = c.renderVals();
  assert.deepEqual(v.instTitleLine, [], 'the sample portfolio never stands in');
  v.instHomeCards.slice(1).forEach((x) => { assert.equal(x.v, '—', x.l); assert.match(x.s, /asset register/); });
});

test('instanceValue: nothing priced in the instance modules is a dash, never £0', () => {
  const none = instanceValue({ year: 2026, ledger: { modules: [{ key: 'maintenance', name: 'Maintenance', counted: false }] } }, ['maintenance', 'vendors']);
  assert.equal(none.answered, true);
  assert.equal(none.total, '—');
  assert.equal(instanceValue(null, ['vendors']).answered, false);
});

test('the Buildings ask bar counts the buildings and asks the instance questions', () => {
  signIn(c, USER);
  pick(c, 'assets');
  c.setState({ view: 'buildings', ccLive: register(c) });
  const v = c.renderVals();
  assert.match(v.abPh, /^Ask anything about your 3 buildings/);
  assert.equal(v.abChips[0].label, 'Which building carries the most asset risk?');
});

// ── a reload ──────────────────────────────────────────────────────────────────

test('the instance and the picker survive a reload', () => {
  signIn(c, USER);
  pick(c, 'assets');
  assert.equal(loadSession().instance, 'assets');
  c.renderVals().instSwitch();
  assert.equal(loadSession().view, 'instances');
});

test('a session that never picked an instance gets the picker on its next load; one that picked stays put', () => {
  signIn(c, USER);
  c.setState({ view: 'vp', instance: null });
  assert.equal(fresh().state.view, 'instances', 'a user who never picked is asked');
  c.setState({ view: 'vp', instance: 'ops' });
  assert.equal(fresh().state.view, 'vp', 'a reload with an instance never moves the page');
  // An admin restored in the admin view without an instance is asked too, and keeps admin.
  const a = fresh();
  signIn(a, ADMIN);
  a.setState({ view: 'home', instance: null, role: 'admin' });
  const back = fresh();
  assert.equal(back.state.view, 'instances');
  assert.equal(back.state.role, 'admin');

  Object.keys(mem).forEach((k) => { delete mem[k]; });
  Object.keys(tabMem).forEach((k) => { delete tabMem[k]; });
  const sa = fresh();
  signIn(sa, SUPER);
  sa.setState({ saOn: false, role: 'user', view: 'home', instance: null });
  assert.notEqual(fresh().state.view, 'instances');
});

test('a stored instance that is not one of the four is dropped', () => {
  signIn(c, USER);
  pick(c, 'ops');
  const k = Object.keys(tabMem)[0];
  tabMem[k] = JSON.stringify(Object.assign(JSON.parse(tabMem[k]), { instance: 'hospital' }));
  assert.equal(loadSession().instance, undefined);
});

test('signing out forgets the instance', () => {
  signIn(c, USER);
  pick(c, 'ops');
  c.authSignedOut('');
  assert.equal(c.state.instance, null);
});

test('the config has four instances with distinct keys', () => {
  assert.deepEqual(INSTANCES.map((i) => i.key), ['ops', 'comp', 'assets', 'full']);
});

// ── pre-push review, 12 Oct 2026 ──────────────────────────────────────────────

test('an instance home never shows the sample value ledger before the real one answers', () => {
  signIn(c, USER);
  pick(c, 'ops');
  c.setState({ homeRaw: null, homeLoading: false });
  const v = c.renderVals();
  assert.equal(v.pvTotal, '—');
  assert.deepEqual(v.pvLedger, []);
  assert.match(v.pvNote, /has not answered yet/);
});

test("the Compliance Engine's Lapsed and Failed checks cards open their lists", () => {
  signIn(c, USER);
  pick(c, 'comp');
  c.renderVals().instHomeCards[1].click();
  assert.equal(c.state.ccQueue, 'lapsed');
  assert.equal(c.state.ccTile, 'lapsed');
  c.renderVals().instHomeCards[2].click();
  assert.equal(c.state.ccQueue, 'auth');
});

test('a superadmin viewing as a company stays in the admin view across a reload', () => {
  signIn(c, SUPER);
  viewAs(c, 'org-9', 'TechCorp');
  c.setState({ view: 'users' });
  const again = fresh();
  // The constructor reads the stored session: the same superadmin, viewing TechCorp, admin view.
  assert.equal(again.state.account.role, 'superadmin');
  assert.equal(again.state.viewOrgId, 'org-9');
  assert.equal(again.state.role, 'admin');
  assert.equal(again.state.view, 'users');
});

test('the picker opened from the chip has a way back', () => {
  signIn(c, ADMIN);
  pick(c, 'ops');
  c.renderVals().instSwitch();
  const v = c.renderVals();
  assert.equal(typeof v.pickerBack, 'function');
  v.pickerBack();
  assert.equal(c.state.view, 'home');
  assert.equal(c.state.instance, 'ops');
});
