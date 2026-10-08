// The Vendor audit trail — a second tab on the Audit trail page (Hussain, 29 Sep 2026): which
// vendor changed, how, when, and who or what changed it. Asset reassignments come from the
// vendor drawer with the admin who made them; compliance blocks and clears are automatic and
// say what made them. Everything shown is GET /api/admin/vendor-audit.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {} };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { adminApi } = await import('../src/api/admin.js');

const NOW = '2026-09-29T15:00:00';
const V = { id: 'v1', name: 'Coolair' };

const REASSIGN = {
  id: 'r:1', kind: 'reassigned', at: '2026-09-29T09:05:00', source: 'person',
  actor: { id: 'u1', name: 'Aasim Shaik', email: 'aasim@plenum-tech.com', role: 'admin' },
  vendor: V, from_vendor: { id: 'v0', name: 'Apex Mechanical' }, to_vendor: V,
  asset: { id: 'a1', name: 'Boiler 1', code: 'B-301-BOILER-01' },
  building: { id: 'b1', name: 'Bishopsgate Tower' }, note: 'contract moved',
  reason: null, accreditation: null, certificate_id: null
};
const BLOCK = {
  id: 'o:1', kind: 'blocked', at: '2026-09-29T11:30:00', source: 'compliance_scan', actor: null,
  vendor: V, from_vendor: null, to_vendor: null, asset: null, building: null, note: null,
  reason: 'Accreditation lapsed: Gas Safe', accreditation: 'Gas Safe', certificate_id: 'c-1'
};
const CLEAR = {
  id: 'o:2', kind: 'cleared', at: '2026-09-27T08:00:00', source: 'certificate_superseded', actor: null,
  vendor: { id: 'v2', name: 'Sparks Electrical' }, from_vendor: null, to_vendor: null, asset: null,
  building: null, note: null, reason: null, accreditation: 'NICEIC', certificate_id: 'c-9'
};
const TRAIL = { ok: true, events: [BLOCK, REASSIGN, CLEAR], unreadable: [] };

const settle = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); };
const real = adminApi.vendorAudit;
let c, reads;
beforeEach(() => {
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'module', module: 'audit', account: {}, vaNow: NOW });
  reads = 0;
  adminApi.vendorAudit = async () => { reads += 1; return TRAIL; };
});
afterEach(() => { adminApi.vendorAudit = real; clearTimeout(c._tt); });

async function openVendors() {
  const tab = c.renderVals().auTabs.find((t) => t.label === 'Vendor audit trail');
  assert.ok(tab, 'a Vendor audit trail tab');
  await tab.pick();
  await settle();
  return c.renderVals();
}

test('the audit trail page has the vendor trail as its second tab, read when opened', async () => {
  let v = c.renderVals();
  assert.deepEqual(v.auTabs.map((t) => [t.label, t.on]), [['Ingestion audit trail', true], ['Vendor audit trail', false]]);
  assert.equal(v.auTitle, 'Ingestion audit trail');
  assert.equal(reads, 0, 'nothing is read before the tab is opened');
  v = await openVendors();
  assert.equal(reads, 1);
  assert.equal(v.auIsVendors, true);
  assert.equal(v.auTitle, 'Vendor audit trail');
  assert.equal(v.auCrumb, 'Vendor audit trail');
});

test('changes are grouped by day, newest first', async () => {
  const v = await openVendors();
  assert.deepEqual(v.vaGroups.map((g) => [g.label, g.count]), [['Today', 2], ['Sunday', 1]]);
  assert.deepEqual(v.vaGroups[0].rows.map((r) => r.badge), ['Blocked', 'Reassigned']);
});

test('a reassignment says what moved, from whom to whom, and which admin moved it', async () => {
  const v = await openVendors();
  const r = v.vaGroups[0].rows[1];
  assert.equal(r.clock, '09:05');
  assert.equal(r.who, 'Aasim Shaik');
  assert.equal(r.role, 'Admin');
  assert.equal(r.vendor, 'Apex Mechanical → Coolair');
  assert.equal(r.change, 'Boiler 1 · B-301-BOILER-01');
  assert.equal(r.building, 'Bishopsgate Tower');
  assert.equal(r.badge, 'Reassigned');
  r.toggle();
  const open = c.renderVals().vaGroups[0].rows[1];
  assert.equal(open.open, true);
  assert.deepEqual(open.fields.map((f) => [f.l, f.v]), [
    ['Asset', 'Boiler 1 · B-301-BOILER-01'],
    ['From vendor', 'Apex Mechanical'],
    ['To vendor', 'Coolair'],
    ['Building', 'Bishopsgate Tower'],
    ['Changed by', 'Aasim Shaik · aasim@plenum-tech.com · Admin'],
    ['Why', 'contract moved'],
    ['When', '29 September 2026, 09:05']
  ]);
});

test('an automatic block says what made it and why, in the risk colour', async () => {
  const v = await openVendors();
  const r = v.vaGroups[0].rows[0];
  assert.equal(r.who, 'Compliance scan');
  assert.equal(r.role, 'Automatic');
  assert.equal(r.vendor, 'Coolair');
  assert.equal(r.change, 'Accreditation lapsed: Gas Safe');
  assert.equal(r.building, 'Every building it serves');
  assert.equal(r.fg, 'var(--st-risk)');
  const clear = v.vaGroups[1].rows[0];
  assert.equal(clear.who, 'Newer certificate');
  assert.equal(clear.change, 'Block lifted — NICEIC');
  assert.equal(clear.badge, 'Cleared');
  assert.equal(clear.fg, 'var(--st-ok)');
});

test('the chips narrow by kind and keep their own counts', async () => {
  let v = await openVendors();
  assert.deepEqual(v.vaFilters.map((f) => [f.label, f.count]),
    [['All', 3], ['Reassigned', 1], ['Blocked', 1], ['Cleared', 1]]);
  v.vaFilters.find((f) => f.label === 'Blocked').pick();
  v = c.renderVals();
  assert.equal(v.vaInView, 1);
  assert.deepEqual(v.vaFilters.map((f) => f.count), [3, 1, 1, 1], 'a chip is not narrowed by itself');
  assert.equal(v.vaFilters.find((f) => f.label === 'Blocked').on, true);
});

test('search finds a vendor, an asset or a person', async () => {
  await openVendors();
  for (const [q, n] of [['apex', 1], ['boiler', 1], ['aasim', 1], ['coolair', 2], ['nobody', 0]]) {
    c.renderVals().vaSetQuery(q);
    assert.equal(c.renderVals().vaInView, n, q);
  }
});

test('the tiles describe what is in view', async () => {
  const v = await openVendors();
  assert.equal(v.vaInView, 3);
  assert.equal(v.vaVendorsAffected, 3, 'Apex Mechanical, Coolair, Sparks Electrical');
  assert.equal(v.vaBlocks, 1);
});

test('the range narrows by calendar day', async () => {
  await openVendors();
  c.renderVals().vaRangePicks.find((r) => r.label === 'Today').pick();
  assert.equal(c.renderVals().vaInView, 2);
  c.renderVals().vaRangePicks.find((r) => r.label === 'All').pick();
  assert.equal(c.renderVals().vaInView, 3);
});

test('an empty trail says why it is empty and since when changes are kept', async () => {
  adminApi.vendorAudit = async () => ({ ok: true, events: [], unreadable: [] });
  const v = await openVendors();
  assert.equal(v.vaGroups.length, 0);
  assert.match(v.vaEmpty, /No vendor changes recorded yet/);
  assert.match(v.vaEmpty, /28 Sep 2026/);
  assert.match(v.vaEmpty, /29 Sep 2026/);
});

test('a read that fails says so and can be retried; a part that failed is named', async () => {
  adminApi.vendorAudit = () => Promise.reject(Object.assign(new Error('timed out'), { status: 504 }));
  let v = await openVendors();
  assert.equal(v.vaError, 'The vendor trail could not be read — timed out.');
  adminApi.vendorAudit = async () => ({ ok: true, events: [REASSIGN], unreadable: ['blocks and clears'] });
  await v.vaRetry();
  await settle();
  v = c.renderVals();
  assert.equal(v.vaError, '');
  assert.equal(v.vaUnreadableNote, 'Could not be read just now: blocks and clears.');
  assert.equal(v.vaInView, 1);
});

test('switching company empties the vendor trail and reads it again on the next open', async () => {
  await openVendors();
  c.resetLiveData();
  assert.equal(c.state.vaEvents, null);
  await openVendors();
  assert.equal(reads, 2);
});

// 29 Sep 2026: Hussain changed a vendor and did not see it. Coming back to the tab must read
// the trail again — a change made in the vendor drawer since the last read is the point.
test('coming back to the tab reads the trail again, keeping the rows on screen meanwhile', async () => {
  await openVendors();
  c.renderVals().auTabs.find((t) => t.label === 'Ingestion audit trail').pick();
  let release;
  adminApi.vendorAudit = () => new Promise((r) => { reads += 1; release = r; });
  const tab = c.renderVals().auTabs.find((t) => t.label === 'Vendor audit trail');
  const pending = tab.pick();
  const v = c.renderVals();
  assert.equal(v.vaLoading, false, 'no "Reading…" flash over rows already shown');
  assert.equal(v.vaInView, 3);
  release({ ok: true, events: [REASSIGN, BLOCK, CLEAR, Object.assign({}, REASSIGN, { id: 'r:2', at: '2026-09-29T14:59:00' })], unreadable: [] });
  await pending;
  await settle();
  assert.equal(reads, 2);
  assert.equal(c.renderVals().vaInView, 4, 'the new change is there');
});

// ── pre-push review, 29 Sep 2026 ─────────────────────────────────────────────────────────

test('coming back to the page from the navigator reads the vendor trail again', async () => {
  c.setState({ role: 'admin' });
  await openVendors();
  c.setState({ view: 'module', module: 'assets' });
  const nav = c.renderVals().navAdmin.find((n) => n.label === 'Audit trail');
  nav.click();
  await settle();
  assert.equal(reads, 2, 'the navigator open is a visit too');
});

test('a reset puts the page back on the ingestion tab rather than a trail that never reads', async () => {
  await openVendors();
  c.resetLiveData();
  const v = c.renderVals();
  assert.equal(v.auIsVendors, false);
  assert.equal(v.auTitle, 'Ingestion audit trail');
});

test('a reassignment whose admin is no longer on record is not credited to Compliance', async () => {
  const gone = Object.assign({}, REASSIGN, { id: 'r:9', actor: { id: 'u9', name: null, email: null, role: 'admin' } });
  const nobody = Object.assign({}, REASSIGN, { id: 'r:8', actor: null });
  adminApi.vendorAudit = async () => ({ ok: true, events: [gone, nobody], unreadable: [] });
  const rows = (await openVendors()).vaGroups[0].rows;
  assert.deepEqual(rows.map((r) => [r.who, r.role]), [['A user no longer on record', 'Admin'], ['Unknown person', '—']]);
  rows[0].toggle();
  const by = c.renderVals().vaGroups[0].rows[0].fields.find((f) => f.l === 'Changed by').v;
  assert.equal(by, 'A user no longer on record · Admin');
});

test('"View audit trail" after an upload opens the ingestion record, whichever tab was left open', async () => {
  await openVendors();
  c.setState({ ingOn: true, ingPhase: 'done', ingOutcome: { outcome: 'Accepted', msg: 'filed' }, view: 'module' });
  const act = (c.renderVals().ingActions || []).find((a) => a.label === 'View audit trail');
  assert.ok(act, 'the button is offered');
  act.click();
  assert.equal(c.state.view, 'audit');
  assert.equal(c.state.auTab, 'ingestion');
});
