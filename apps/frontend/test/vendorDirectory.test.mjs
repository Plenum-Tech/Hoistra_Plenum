// The Vendors directory is searched and filtered rather than scrolled (12 Oct 2026). With 27
// vendors a person scrolled the whole page past every one to find the vendor they wanted, then
// back up to read it. The directory now has a search and four filters, and each filter's count
// is the search narrowed by that filter alone, so pressing one never changes its own number.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets'); } };
globalThis.fetch = async () => { throw new TypeError('no network'); };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const card = (id, name, score) => ({ vendor_id: id, vendor_name: name, score_month: '2026-09-01', overall_score: score, delta: 0, component_breakdown: {} });
const RAW = {
  fetchedAt: '2026-10-12T08:00:00Z', errors: {},
  summary: { scorecards: [card('a', 'Apex Mechanical', 86), card('b', 'Aquilon Water Hygiene', 72), card('c', 'Kestamere Fire Protection', 91)] },
  contracts: { parameters: [] }, certificates: [],
  coverage: {}, packs: {}
};

let c;
beforeEach(() => {
  c = new HoistraLogic();
  ['loadLiveData', 'resetLiveData', 'authLoadScope', 'ccLoad', 'vpLoad', 'vmLoad', 'crEnsureRun'].forEach((k) => { c[k] = () => {}; });
  c.setState({ signedIn: true, role: 'user', instance: 'full', view: 'vp',
    account: { id: 'u', email: 'a@b.c', full_name: 'A', role: 'admin', status: 'active', organization_id: 'org-1' },
    vpRaw: RAW, vmRows: [{ id: 'd', name: 'Thamesmead Roofing Services Ltd' }] });
});
afterEach(() => clearInterval(c._orchTick));

const names = (v) => v.vpList.map((x) => x.name);

test('by default the directory lists every vendor, with its count', () => {
  const v = c.renderVals();
  assert.equal(v.vpList.length, 4);
  assert.equal(v.vpDirCount, '4 vendors');
});

test('search narrows by name, ignoring case', () => {
  c.setState({ vpDirQ: 'apex' });
  assert.deepEqual(names(c.renderVals()), ['Apex Mechanical']);
});

test('the filters: needs action (below 80), not scored, and their counts follow the search', () => {
  let v = c.renderVals();
  const chip = (label) => v.vpDirChips.find((x) => x.label === label);
  assert.equal(chip('All').count, 4);
  assert.equal(chip('Needs action').count, 1, 'Aquilon at 72');
  assert.equal(chip('Not scored').count, 1, 'Thamesmead');
  chip('Not scored').pick();
  v = c.renderVals();
  assert.deepEqual(names(v), ['Thamesmead Roofing Services Ltd']);
  assert.equal(v.vpDirChips.find((x) => x.label === 'Not scored').on, true);
  assert.equal(v.vpDirChips.find((x) => x.label === 'All').count, 4, 'a filter never changes the counts beside it');
});

test('nothing matching says so, with a way back', () => {
  c.setState({ vpDirQ: 'zzz' });
  const v = c.renderVals();
  assert.equal(v.vpList.length, 0);
  assert.match(v.vpDirEmpty, /No vendor matches/);
  v.vpDirClear();
  assert.equal(c.renderVals().vpList.length, 4);
});

// ── Buildings served: assigned buildings, and assigning from the page (12 Oct 2026) ─────
const { shapeLiveCompliance } = await import('../src/logic/complianceLive.js');
// A register with one building and no vendor: the assignment alone puts Thamesmead on it.
const REG = () => shapeLiveCompliance({ certificates: [], packs: {}, coverage: { UK: { vendors: [],
  buildings: [{ site_name: 'Kingsway House', country_code: 'UK', state: 'London', coverage_pct: 50, on_record: 1, required: 2, certificates_total: 1, gaps: [], linked: true }] } } });
test('Buildings served lists assigned buildings, ends one, and assigns another', async () => {
  const assigned = [];
  const ended = [];
  c.vbAssign = async (vid, ids, source) => { assigned.push([vid, ids, source]); return { ok: true, assigned: ['Harbour Point'], already: [] }; };
  c.vbUnassign = async (vid, bid) => { ended.push([vid, bid]); return { ok: true }; };
  c.iBuildingRows = () => [{ name: 'Kingsway House', id: 'b-kw' }, { name: 'Harbour Point', id: 'b-hp' }];
  c.setState({
    ccLive: REG(),
    vbRows: [{ vendor_id: 'd', vendor_name: 'Thamesmead Roofing Services Ltd', building_id: 'b-kw', building_name: 'Kingsway House', source: 'hoist' }],
    vpVendor: 'd', vpTab: 6
  });
  let v = c.renderVals();
  assert.deepEqual(v.vpServedRows.map((r) => [r.name, r.tag]), [['Kingsway House', 'Assigned']]);
  assert.equal(typeof v.vpServedRows[0].unassign, 'function');
  assert.deepEqual(v.vpAssign.opts, ['Harbour Point'], 'only buildings it is not on yet');
  v.vpAssign.pick({ target: { value: 'Harbour Point' } });
  await c.renderVals().vpAssign.run();
  assert.deepEqual(assigned, [['d', ['b-hp'], 'manual']]);
  await c.renderVals().vpServedRows[0].unassign();
  assert.deepEqual(ended, [['d', 'b-kw']]);
});

test('a plain user sees the assigned buildings but cannot change them', () => {
  c.setState({ account: Object.assign({}, c.state.account, { role: 'user' }), role: 'user',
    ccLive: REG(),
    vbRows: [{ vendor_id: 'd', vendor_name: 'Thamesmead Roofing Services Ltd', building_id: 'b-kw', building_name: 'Kingsway House' }],
    vpVendor: 'd', vpTab: 6 });
  const v = c.renderVals();
  assert.deepEqual(v.vpServedRows.map((r) => r.name), ['Kingsway House']);
  assert.equal(v.vpServedRows[0].unassign, null);
  assert.equal(v.vpAssign, null);
});

test('a vendor with nothing on file can be assigned from its Buildings served tab', () => {
  c.iBuildingRows = () => [{ name: 'Kingsway House', id: 'b-kw' }];
  c.setState({ ccLive: REG(), vbRows: [], vpVendor: 'd', vpTab: 6 });
  const v = c.renderVals();
  assert.ok(v.vpAssign, 'Assign is offered');
  assert.deepEqual(v.vpAssign.opts, ['Kingsway House']);
});

test('a vendor deleted from this tab is not brought back by its assignments', () => {
  c.setState({ ccLive: REG(), vdlGone: ['d'],
    vbRows: [{ vendor_id: 'd', vendor_name: 'Thamesmead Roofing Services Ltd', building_id: 'b-kw', building_name: 'Kingsway House' }] });
  assert.ok(!c.ccData().vendors.some((x) => x.id === 'd'));
});

test('ending an assignment uses the id it was stored with, and a second press waits', async () => {
  let calls = 0;
  let release;
  c.vbUnassign = (vid, bid) => { calls += 1; assert.equal(bid, 'b-kw-stored'); return new Promise((r) => { release = () => r({ ok: true }); }); };
  c.iBuildingRows = () => [];
  c.setState({ ccLive: REG(), vpVendor: 'd', vpTab: 6,
    vbRows: [{ vendor_id: 'd', vendor_name: 'Thamesmead Roofing Services Ltd', building_id: 'b-kw-stored', building_name: 'Kingsway House' }] });
  const row = c.renderVals().vpServedRows[0];
  const first = row.unassign();
  row.unassign();
  assert.equal(calls, 1);
  release();
  await first;
});
