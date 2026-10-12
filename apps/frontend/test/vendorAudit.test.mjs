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
    [['All', 3], ['Reassigned', 1], ['Blocked', 1], ['Cleared', 1], ['Deleted', 0], ['Buildings', 0], ['Hoisting', 0]]);  // Hoisting: test/vendorHoist.test.mjs
  v.vaFilters.find((f) => f.label === 'Blocked').pick();
  v = c.renderVals();
  assert.equal(v.vaInView, 1);
  assert.deepEqual(v.vaFilters.map((f) => f.count), [3, 1, 1, 1, 0, 0, 0], 'a chip is not narrowed by itself');
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

// ── deletions (12 Oct 2026) ──────────────────────────────────────────────────────────────
// An admin deleted a vendor (logic/vendorDelete.js): removed completely when it had no history,
// retired — history kept, marked (deleted) — when it had. The data reset deletes vendors too, in
// bulk, and logs one entry per run. Each is a person's act, on every building at once: never a
// "Block lifted", never credited to Compliance.
const DELETE_RETIRE = {
  id: 'o:9', kind: 'deleted', at: '2026-09-29T13:00:00', source: 'person',
  actor: { id: 'u2', name: 'Ada Admin', email: 'ada@example.com', role: 'admin' },
  vendor: { id: 'V-01', name: 'Acme Ltd', code: 'VEN-ACME-001' }, mode: 'retire', reason: 'Contract ended',
  effects: {
    removed: { vendor_contacts: 2 }, unassigned: { assets: 3, work_orders: 1, ppm_visits: 2 }, closed: { approvals: 2 },
    revoked: { approval_action_tokens: 1 }, ended: { document_links: 1 }, archived: { compliance_certificates: 4 },
    kept: { invoice_verifications: 12, vendor_monthly_scorecards: 6, 'rfqs.supplier_id': 1 }
  },
  assets: [{ id: 'a1', name: 'Boiler 1', code: 'B-301' }, { id: 'a2', name: 'Pump 2', code: 'P-2' }],
  work_orders: [{ id: 'w1', code: 'WO-17', title: 'Leak on level 3' }],
  from_vendor: null, to_vendor: null, asset: null, building: null, note: null, accreditation: null, certificate_id: null
};
const DELETE_REMOVE = Object.assign({}, DELETE_RETIRE, {
  id: 'o:10', at: '2026-09-29T12:00:00', mode: 'remove', reason: null, vendor: { id: 'v-7', name: 'Quiet Co', code: null },
  effects: { removed: {}, unassigned: {}, closed: {}, revoked: {}, ended: {}, archived: {}, kept: {} }, assets: [], work_orders: []
});
const DELETE_RESET = {
  id: 'o:11', kind: 'deleted', at: '2026-09-29T10:00:00', source: 'data_reset',
  actor: { id: 'u2', name: 'Ada Admin', email: 'ada@example.com', role: 'admin' },
  vendor: null, mode: 'reset', reason: null, count: 3,
  vendors: [{ id: 'v-1', name: 'Acme Ltd', code: 'ACME' }, { id: 'v-2', name: 'Beta Lifts', code: null }, { id: 'v-3', name: 'Gamma Gas', code: 'GG' }],
  from_vendor: null, to_vendor: null, asset: null, building: null, note: null, accreditation: null, certificate_id: null
};
const rowOf = async (ev) => {
  adminApi.vendorAudit = async () => ({ ok: true, events: [ev], unreadable: [] });
  const r = (await openVendors()).vaGroups[0].rows[0];
  r.toggle();
  return c.renderVals().vaGroups[0].rows[0];
};

test('a retired vendor reads as a deletion by the admin who made it, on every building, with its history kept', async () => {
  const r = await rowOf(DELETE_RETIRE);
  assert.equal(r.who, 'Ada Admin');
  assert.equal(r.role, 'Admin');
  assert.equal(r.vendor, 'Acme Ltd');
  assert.equal(r.change, 'Retired — history kept');
  assert.equal(r.building, 'Every building');
  assert.equal(r.badge, 'Deleted');
  assert.equal(r.fg, 'var(--st-risk)');
  assert.deepEqual(r.fields.map((f) => [f.l, f.v]), [
    ['Vendor', 'Acme Ltd'],
    ['Vendor code', 'VEN-ACME-001'],
    ['How', 'Retired — hidden everywhere, its history kept and marked (deleted)'],
    ['Removed', '2 contacts · 1 document link · 1 share link'],
    ['Unassigned', 'Assets: Boiler 1 · B-301, Pump 2 · P-2 and 1 more; Open work orders: WO-17 · Leak on level 3; 2 planned visits'],
    ['Pending decisions closed', '2'],
    ['Accreditations archived', '4'],
    ['History kept', '12 invoices · 6 monthly scorecards · 1 other record'],
    ['Why', 'Contract ended'],
    ['Deleted by', 'Ada Admin · ada@example.com · Admin'],
    ['When', '29 September 2026, 13:00']
  ]);
});

test('a vendor with no history reads as removed completely', async () => {
  const r = await rowOf(DELETE_REMOVE);
  assert.equal(r.change, 'Removed completely');
  const f = Object.fromEntries(r.fields.map((x) => [x.l, x.v]));
  assert.equal(f['How'], 'Removed completely — it had no history');
  assert.equal(f['Vendor code'], '—');
  assert.equal(f['Removed'], 'Nothing else');
  assert.equal(f['Unassigned'], 'Nothing');
  assert.equal(f['Pending decisions closed'], 'None');
  assert.equal(f['Accreditations archived'], 'None');
  assert.equal(f['History kept'], 'None — it had no history');
  assert.equal(f['Why'], 'No reason given');
});

test('a data reset that deleted vendors reads as one entry naming them', async () => {
  const r = await rowOf(DELETE_RESET);
  assert.equal(r.who, 'Ada Admin');
  assert.equal(r.vendor, 'Acme Ltd, Beta Lifts and 1 more');
  assert.equal(r.change, 'Deleted by a data reset — 3 vendors');
  assert.equal(r.building, 'Every building');
  assert.equal(r.badge, 'Deleted');
  const f = Object.fromEntries(r.fields.map((x) => [x.l, x.v]));
  assert.equal(f['Vendor'], 'Acme Ltd, Beta Lifts, Gamma Gas');
  assert.equal(f['Vendor code'], 'ACME, GG');
  assert.equal(f['How'], 'Deleted by a data reset — 3 vendors');
  assert.equal(f['Deleted by'], 'Ada Admin · ada@example.com · Admin');
});

test('a deletion is never read as a block lifted or credited to Compliance, even with its admin gone', async () => {
  const r = await rowOf(Object.assign({}, DELETE_RESET, { actor: null }));
  assert.equal(r.who, 'A user no longer on record');
  const by = r.fields.find((f) => f.l === 'Deleted by').v;
  assert.equal(by, 'A user no longer on record');
  for (const f of r.fields) {
    assert.doesNotMatch(String(f.v), /Block lifted|Compliance — automatic|automatic/);
  }
  assert.doesNotMatch(r.change, /Block lifted/);
});

test('deletions have their own chip, and count in the vendors affected', async () => {
  adminApi.vendorAudit = async () => ({ ok: true, events: [BLOCK, REASSIGN, CLEAR, DELETE_RETIRE, DELETE_RESET], unreadable: [] });
  let v = await openVendors();
  assert.deepEqual(v.vaFilters.map((f) => [f.label, f.count]),
    [['All', 5], ['Reassigned', 1], ['Blocked', 1], ['Cleared', 1], ['Deleted', 2], ['Buildings', 0], ['Hoisting', 0]]);
  v.vaFilters.find((f) => f.label === 'Deleted').pick();
  v = c.renderVals();
  assert.equal(v.vaInView, 2);
  assert.equal(v.vaVendorsAffected, 3, 'Acme Ltd, Beta Lifts, Gamma Gas');
  assert.equal(v.vaBlocks, 0);
});

test('a deletion is found by the vendor\'s name or code, and by the admin', async () => {
  adminApi.vendorAudit = async () => ({ ok: true, events: [BLOCK, REASSIGN, CLEAR, DELETE_RETIRE], unreadable: [] });
  await openVendors();
  for (const [q, n] of [['acme', 1], ['ven-acme-001', 1], ['ada admin', 1], ['ada@example.com', 1], ['contract ended', 1]]) {
    c.renderVals().vaSetQuery(q);
    assert.equal(c.renderVals().vaInView, n, q);
  }
});

test('the empty trail says since when deletions are kept', async () => {
  adminApi.vendorAudit = async () => ({ ok: true, events: [], unreadable: [] });
  const v = await openVendors();
  assert.match(v.vaEmpty, /deletions from 12 Oct 2026/);
});

test('a building assignment reads as one, with its buildings, under the Buildings filter', async () => {
  const { vendorRow } = await import('../src/logic/vendorAudit.js');
  const r = vendorRow({ id: 'o:1', kind: 'assigned', at: '2026-10-12T12:00:00Z', source: 'hoist',
    actor: { id: 'u-1', name: 'Ada', email: 'ada@example.com', role: 'admin' },
    vendor: { id: 'v-1', name: 'Harbourside Lift Engineering Ltd' },
    buildings: [{ id: 'b-1', name: 'Bishopsgate Tower' }, { id: 'b-2', name: 'Manchester Town Hall' }] });
  assert.equal(r.badge, 'Assigned');
  assert.equal(r.group, 'buildings');
  assert.equal(r.building, 'Bishopsgate Tower, Manchester Town Hall');
  assert.match(r.change, /when it was hoisted/);
  const off = vendorRow({ id: 'o:2', kind: 'unassigned', at: '2026-10-12T12:05:00Z', actor: null,
    vendor: { id: 'v-1', name: 'Harbourside Lift Engineering Ltd' }, buildings: [{ id: 'b-1', name: 'Bishopsgate Tower' }] });
  assert.equal(off.badge, 'Unassigned');
  assert.equal(off.change, 'Taken off Bishopsgate Tower');
});
