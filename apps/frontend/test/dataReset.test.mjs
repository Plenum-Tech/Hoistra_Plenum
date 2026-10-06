// dataReset — the "Reset page data" panel on Users & access, against a mocked service.
//
// What matters is that deleting cannot happen by accident: nothing is chosen until a page is
// ticked, the preview always matches what is ticked, a blocked reset cannot be sent, and the
// button stays off until the company name is typed exactly.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

let calls, handlers;
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  const key = method + ' ' + u.pathname;
  calls.push({ key: key, body: opts && opts.body, query: u.searchParams });
  const h = handlers[key];
  if (!h) return { ok: false, status: 404, statusText: '404', text: async () => '{"detail":"not mocked"}' };
  const out = typeof h === 'function' ? await h(u, opts) : h;
  const status = out && out.__status ? out.__status : 200;
  return { ok: status < 300, status: status, statusText: String(status), text: async () => JSON.stringify(out.__body || out) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const client = await import('../src/api/client.js');
const settle = () => new Promise((r) => setTimeout(r, 30));
const PATH = '/backend/ops-intelligence/api/admin/data-reset';
const NAME = 'Northbridge Estates Ltd';

const plan = (areas, over) => Object.assign({
  ok: true, organization_name: NAME, confirm_with: NAME, buildings: 2, applied: false,
  areas: areas.map((a) => ({ area: a, label: a[0].toUpperCase() + a.slice(1), rows: 10, tables: [{ table: a + '_table', rows: 10 }] })),
  row_total: 10 * areas.length, links_cleared: [], blocked: [], skipped: {}, kept: []
}, over || {});

let c;
beforeEach(() => {
  calls = []; handlers = {};
  handlers['GET ' + PATH] = (u) => plan((u.searchParams.get('areas') || '').split(',').filter(Boolean));
  c = new HoistraLogic();
  c.setState({ signedIn: true, role: 'admin', account: { id: 'u1', email: 'a@b.c', role: 'admin', status: 'active' } });
});
afterEach(() => { clearTimeout(c._tt); clearInterval(c._orchTick); clearInterval(c._ccTick); client.setActingOrg(null); });

test('nothing is chosen or previewed until a page is ticked', () => {
  const v = c.drVals();
  assert.equal(v.drShow, true);
  assert.ok(v.drAreas.every((a) => !a.on));
  assert.equal(v.drHasPlan, false);
  assert.equal(v.drCanApply, false);
  assert.equal(calls.length, 0);
});

test('a plain user never sees the panel', () => {
  c.setState({ account: { id: 'u2', email: 'u@b.c', role: 'user', status: 'active' } });
  assert.equal(c.drVals().drShow, false);
});

test('ticking a page previews exactly the ticked pages', async () => {
  c.drToggle('energy');
  await settle();
  c.drToggle('compliance');
  await settle();
  const last = calls.filter((x) => x.key === 'GET ' + PATH).pop();
  assert.equal(last.query.get('areas'), 'compliance,energy');
  const v = c.drVals();
  assert.equal(v.drHasPlan, true);
  assert.match(v.drSummary, /20 rows would be deleted for Northbridge Estates Ltd/);
});

test('the button stays off until the company name is typed exactly', async () => {
  c.drToggle('energy');
  await settle();
  assert.equal(c.drVals().drCanApply, false);
  c.drVals().drSetConfirm({ target: { value: 'Northbridge' } });
  assert.equal(c.drVals().drCanApply, false);
  c.drVals().drSetConfirm({ target: { value: NAME } });
  assert.equal(c.drVals().drCanApply, true);
});

test('a blocked reset cannot be sent, and "Also clear" adds the page it needs', async () => {
  handlers['GET ' + PATH] = (u) => {
    const areas = (u.searchParams.get('areas') || '').split(',').filter(Boolean);
    return plan(areas, areas.includes('maintenance') ? {} : {
      blocked: [{ table: 'maintenance_plans', column: 'asset_id', rows: 12, needs_area: 'maintenance', because: 'each needs the assets row it names' }]
    });
  };
  c.drToggle('assets');
  await settle();
  c.drVals().drSetConfirm({ target: { value: NAME } });
  let v = c.drVals();
  assert.equal(v.drBlocked.length, 1);
  assert.equal(v.drBlocked[0].needs, 'Maintenance');
  assert.equal(v.drCanApply, false, 'a blocked reset is not sendable, name or no name');
  v.drBlocked[0].add();
  await settle();
  v = c.drVals();
  assert.equal(v.drBlocked.length, 0);
  assert.equal(calls.filter((x) => x.key === 'GET ' + PATH).pop().query.get('areas'), 'assets,maintenance');
});

test('the delete posts the ticked pages and the typed name, then reads the counts again', async () => {
  handlers['POST ' + PATH] = () => plan(['energy'], { applied: true, row_total: 182320 });
  c.drToggle('energy');
  await settle();
  c.drVals().drSetConfirm({ target: { value: NAME } });
  const before = calls.length;
  await c.drApply();
  await settle();
  const post = calls.find((x) => x.key === 'POST ' + PATH);
  assert.deepEqual(JSON.parse(post.body), { areas: ['energy'], confirm: NAME });
  assert.ok(calls.slice(before).some((x) => x.key === 'GET ' + PATH), 'counts are read again after the delete');
  assert.match(c.drVals().drDone, /Deleted 182,320 rows/);
  assert.equal(c.state.drConfirm, '', 'the name must be typed again for another reset');
});

test('a refusal from the service is shown, with the rows that block it', async () => {
  handlers['POST ' + PATH] = () => ({ __status: 409, __body: { detail: { ok: false, reason: 'reset_blocked', error: 'kept rows depend on these', blocked: [{ table: 'maintenance_plans', column: 'asset_id', rows: 12, needs_area: 'maintenance', because: 'x' }] } } });
  c.drToggle('energy');
  await settle();
  c.drVals().drSetConfirm({ target: { value: NAME } });
  await c.drApply();
  const v = c.drVals();
  assert.ok(v.drError);
  assert.equal(v.drBlocked.length, 1);
});

// ── one building ───────────────────────────────────────────────────────────────────────────
// The scope picker: every building (the company reset above) or one of them. One building is
// confirmed with its own name, and a preview for one scope never arms a delete for another.
const H = 'c343c566-0000-4000-8000-000000000001';
const G = '4a451a94-af44-486f-b660-8e19518cd19f';
const BLDS = [{ id: H, name: 'Harbour Point', building_code: 'B-101' }, { id: G, name: 'Ashgrove Court', building_code: 'B-102' }];

function scoped() {
  handlers['GET ' + PATH] = (u) => {
    const areas = (u.searchParams.get('areas') || '').split(',').filter(Boolean);
    const b = BLDS.find((x) => x.id === u.searchParams.get('building_id'));
    return plan(areas, b ? {
      building: b, building_id: b.id, confirm_with: b.name, buildings: 1,
      company_wide: [{ table: 'vendors', area: 'contracts', label: 'Contracts' }, { table: 'sla_policies', area: 'contracts', label: 'Contracts' }]
    } : { building: null, building_id: null, company_wide: [] });
  };
  c.setState({ axBldsLive: BLDS });
}

test('the scope starts at every building and lists the company buildings', () => {
  scoped();
  const v = c.drVals();
  assert.equal(v.drScopeShow, true);
  assert.equal(v.drScope, '');
  assert.deepEqual(v.drScopeOptions.map((o) => o.label), ['All buildings', 'Harbour Point · B-101', 'Ashgrove Court · B-102']);
});

test('with no buildings to choose from there is no picker', () => {
  assert.equal(c.drVals().drScopeShow, false);
});

test('choosing a building previews that building only, confirmed by its name', async () => {
  scoped();
  c.drToggle('energy');
  await settle();
  c.drVals().drSetScope({ target: { value: H } });
  await settle();
  const last = calls.filter((x) => x.key === 'GET ' + PATH).pop();
  assert.equal(last.query.get('building_id'), H);
  assert.equal(last.query.get('areas'), 'energy');
  let v = c.drVals();
  assert.equal(v.drConfirmName, 'Harbour Point');
  assert.match(v.drSummary, /10 rows would be deleted from Harbour Point \(B-101\)/);
  assert.equal(v.drBuildings, 'Harbour Point (B-101) only');
  assert.equal(v.drApplyLabel, 'Delete 10 rows from Harbour Point (B-101)');
  c.drVals().drSetConfirm({ target: { value: NAME } });
  assert.equal(c.drVals().drCanApply, false, 'the company name does not confirm a building reset');
  c.drVals().drSetConfirm({ target: { value: 'Harbour Point' } });
  v = c.drVals();
  assert.equal(v.drCanApply, true);
});

test('changing the scope clears the typed name and reads the counts again', async () => {
  scoped();
  c.drToggle('energy');
  await settle();
  c.drVals().drSetConfirm({ target: { value: NAME } });
  const before = calls.length;
  c.drVals().drSetScope({ target: { value: G } });
  assert.equal(c.state.drConfirm, '');
  await settle();
  assert.equal(calls.slice(before).filter((x) => x.key === 'GET ' + PATH).pop().query.get('building_id'), G);
  c.drVals().drSetScope({ target: { value: '' } });
  await settle();
  assert.equal(calls.filter((x) => x.key === 'GET ' + PATH).pop().query.get('building_id'), null);
  assert.equal(c.drVals().drConfirmName, NAME);
});

test('choosing a building before any page is ticked previews nothing', async () => {
  scoped();
  c.drVals().drSetScope({ target: { value: H } });
  await settle();
  assert.equal(calls.length, 0);
  c.drToggle('assets');
  await settle();
  assert.equal(calls.filter((x) => x.key === 'GET ' + PATH).pop().query.get('building_id'), H);
});

test('what serves every building is said to be kept', async () => {
  scoped();
  c.drToggle('contracts');
  await settle();
  assert.equal(c.drVals().drCompanyWide, '');
  c.drVals().drSetScope({ target: { value: H } });
  await settle();
  assert.equal(c.drVals().drCompanyWide, 'vendors, sla policies');
});

test('the building delete posts the building, and the done line names it', async () => {
  scoped();
  handlers['POST ' + PATH] = () => plan(['energy'], { applied: true, row_total: 35040, building: BLDS[0], building_id: H });
  c.drToggle('energy');
  await settle();
  c.drVals().drSetScope({ target: { value: H } });
  await settle();
  c.drVals().drSetConfirm({ target: { value: 'Harbour Point' } });
  await c.drApply();
  await settle();
  const post = calls.find((x) => x.key === 'POST ' + PATH);
  assert.deepEqual(JSON.parse(post.body), { areas: ['energy'], confirm: 'Harbour Point', building_id: H });
  assert.match(c.drVals().drDone, /Deleted 35,040 rows from Harbour Point \(B-101\)/);
});

test('a preview for another scope never arms the delete', async () => {
  scoped();
  c.drToggle('energy');
  await settle();                       // the all-buildings preview is on screen
  c.setState({ drBuilding: H });        // the scope moved, its preview has not landed
  c.drVals().drSetConfirm({ target: { value: NAME } });
  assert.equal(c.drVals().drCanApply, false);
  await c.drApply();
  assert.equal(calls.filter((x) => x.key === 'POST ' + PATH).length, 0);
});

// ── review, 6 Oct 2026 ─────────────────────────────────────────────────────────────────────
const PEND = {};
// Holds each preview until released, per scope ('' = every building).
function gated() {
  const plain = handlers['GET ' + PATH];
  handlers['GET ' + PATH] = (u) => new Promise((res) => {
    const k = u.searchParams.get('building_id') || '';
    (PEND[k] = PEND[k] || []).push(() => res(plain(u)));
  });
}
const release = async (k) => { const f = (PEND[k] || []).shift(); if (f) f(); await settle(); };
const posts = () => calls.filter((x) => x.key === 'POST ' + PATH);

test('a preview that lands after the scope moved is dropped, never armed', async () => {
  scoped();
  gated();
  c.drToggle('energy');                              // every building, in flight
  await settle();
  c.drVals().drSetScope({ target: { value: H } });   // Harbour Point, in flight
  await release('');                                 // the old scope's answer lands
  assert.equal(c.state.drPlan, null);
  c.setState({ drConfirm: NAME });
  assert.equal(c.drVals().drCanApply, false);
  await c.drApply();
  assert.equal(posts().length, 0);
  await release(H);
  assert.equal(c.drVals().drConfirmName, 'Harbour Point');
});

test('nothing is sent while the counts are being read', async () => {
  scoped();
  c.drToggle('energy');
  await settle();
  c.drVals().drSetScope({ target: { value: H } });
  await settle();
  gated();
  c.drToggle('compliance');                          // re-reading; the energy-only plan is stale
  c.setState({ drConfirm: 'Harbour Point' });
  handlers['POST ' + PATH] = () => plan(['energy'], { applied: true });
  await c.drApply();
  assert.equal(posts().length, 0);
});

test('a company switch puts the scope back to every building', async () => {
  scoped();
  c.drToggle('energy');
  await settle();
  c.drVals().drSetScope({ target: { value: H } });
  await settle();
  c.drVals().drSetConfirm({ target: { value: 'Harbour Point' } });
  client.setActingOrg('org-b');
  c.resetLiveData();
  assert.equal(c.state.drBuilding, '');
  assert.equal(c.state.drPlan, null);
  assert.equal(c.state.drConfirm, '');
  assert.deepEqual(c.state.drPicked, {});
  await c.drApply();
  assert.equal(posts().length, 0);
});

test('signing out forgets the chosen building', async () => {
  scoped();
  c.drToggle('energy');
  await settle();
  c.drVals().drSetScope({ target: { value: H } });
  await settle();
  c.authSignedOut('');
  assert.equal(c.state.drBuilding, '');
  assert.equal(c.state.drPlan, null);
});

test('a building no longer in the list cannot be deleted, and the picker says so', async () => {
  scoped();
  c.drToggle('energy');
  await settle();
  c.drVals().drSetScope({ target: { value: H } });
  await settle();
  c.drVals().drSetConfirm({ target: { value: 'Harbour Point' } });
  c.setState({ axBldsLive: [BLDS[1]] });             // the register re-read without Harbour Point
  const v = c.drVals();
  assert.equal(v.drCanApply, false);
  const shown = v.drScopeOptions.find((o) => o.id === H);
  assert.ok(shown, 'the select still has an option for its value, so "All buildings" can be chosen');
  assert.match(shown.label, /not in this company/i);
  assert.match(v.drScopeNote, /not in this company/i);
});

test('a building with no name is named by its code, never confirmed by the company name', async () => {
  const N = '7d1f0000-0000-4000-8000-000000000009';
  const nameless = { id: N, name: null, building_code: 'B-9' };
  handlers['GET ' + PATH] = (u) => plan((u.searchParams.get('areas') || '').split(',').filter(Boolean),
    u.searchParams.get('building_id') ? { building: nameless, building_id: N, confirm_with: null, buildings: 1, company_wide: [] } : {});
  c.setState({ axBldsLive: [nameless] });
  assert.equal(c.drVals().drScopeOptions[1].label, 'B-9');
  c.drToggle('energy');
  await settle();
  c.drVals().drSetScope({ target: { value: N } });
  await settle();
  const v = c.drVals();
  assert.match(v.drSummary, /deleted from B-9 across/);
  assert.notEqual(v.drConfirmName, NAME);
  c.drVals().drSetConfirm({ target: { value: NAME } });
  assert.equal(c.drVals().drCanApply, false, 'the company name must never confirm a building reset');
});

test('a refused delete drops the plan it was armed by and reads the counts again', async () => {
  scoped();
  handlers['POST ' + PATH] = () => ({ __status: 400, __body: { detail: { ok: false, reason: 'confirm_mismatch', error: 'Type the building name exactly to confirm the reset.', confirm_with: 'Harbour Point' } } });
  c.drToggle('energy');
  await settle();
  c.drVals().drSetScope({ target: { value: H } });
  await settle();
  c.drVals().drSetConfirm({ target: { value: 'Harbour Point' } });
  const before = calls.length;
  await c.drApply();
  await settle();
  assert.ok(calls.slice(before).some((x) => x.key === 'GET ' + PATH), 'counts read again');
  const v = c.drVals();
  assert.match(v.drError, /building name exactly/);
  assert.equal(v.drCanApply, false, 'the name is typed again before another try');
});

test('a company switch during a preview stays quiet', async () => {
  scoped();
  gated();
  c.drToggle('energy');
  await settle();
  client.setActingOrg('org-b');
  await release('');
  assert.equal(c.state.drError, '');
});

test('the scope note promises nothing the count has not shown', () => {
  scoped();
  c.setState({ drBuilding: H });
  assert.doesNotMatch(c.drVals().drScopeNote, /vendors|contracts|technicians/i);
});
