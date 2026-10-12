// The vendor list itself (12 Oct 2026). A vendor hoisted with no certificate — Thamesmead, in the
// end-to-end pack — was in the database and on no screen: the Vendors directory and the Hoist
// vendors list were built from certificates, contracts and scorecards only. The vendor list is
// now read directly (cafm-connector's GET /api/v1/plenum/vendors, company-scoped for every
// signed-in caller). That route maps vendor ids as UUIDs, so a company holding older non-UUID ids
// can fail it; an admin then reads the same list through svc-udr's SELECT-only query, ids as
// text. A plain user is never sent there — it is an admin route.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets'); } };
const calls = [];
let handlers = {};
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const key = ((opts && opts.method) || 'GET') + ' ' + u.pathname;
  calls.push({ key: key, query: Object.fromEntries(u.searchParams), body: opts && opts.body ? JSON.parse(opts.body) : null });
  const h = handlers[key];
  if (!h) throw new TypeError('no network in tests: ' + key);
  const [status, body] = typeof h === 'function' ? h(u) : h;
  return { ok: status >= 200 && status < 300, status: status, statusText: String(status), text: async () => JSON.stringify(body) };
};
const settle = () => new Promise((r) => setTimeout(r, 30));

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { shapeVendorList } = await import('../src/logic/vendorMaster.js');

const CN = 'GET /backend/connector/api/v1/plenum/vendors';
const UDR = 'POST /backend/udr/api/tables/query/select';
const ORG = { organization_id: 'org-1', organization_name: 'Northbridge' };
const ADMIN = Object.assign({ id: 'u-2', email: 'ada@example.com', full_name: 'Ada', role: 'admin', status: 'active' }, ORG);
const USER = Object.assign({}, ADMIN, { id: 'u-1', email: 'sam@example.com', role: 'user' });
const THAMES = '5c1e2d3f-0000-4000-8000-00000000abcd';
const page = (rows, total) => [200, { total: total === undefined ? rows.length : total, limit: 500, offset: 0, data: rows }];

let c;
const make = (account, role) => {
  c = new HoistraLogic();
  ['loadLiveData', 'resetLiveData', 'authLoadScope', 'usLiveLoad', 'auLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'crEnsureRun', 'ccLoad', 'vpLoad'].forEach((k) => { c[k] = () => {}; });
  c.setState({ signedIn: true, account: account, role: role, instance: 'full', accessToken: 'tok' });
  return c;
};
beforeEach(() => { calls.length = 0; handlers = {}; Object.keys(mem).forEach((k) => { delete mem[k]; }); });
afterEach(() => { if (c) clearInterval(c._orchTick); });

test('shapeVendorList reads either source into {id, name}, dropping rows with no name', () => {
  assert.deepEqual(shapeVendorList([{ id: THAMES, vendor_name: 'Thamesmead Roofing Services Ltd' }, { id: 'v-x', vendor_name: '' }]),
    [{ id: THAMES, name: 'Thamesmead Roofing Services Ltd' }]);
});

test('the vendor list is read from the connector, for the company in view', async () => {
  make(ADMIN, 'user');
  handlers[CN] = page([{ id: THAMES, organization_id: 'org-1', vendor_name: 'Thamesmead Roofing Services Ltd', address: null, created_at: '2026-10-12T08:00:00Z' }]);
  await c.vmLoad();
  assert.equal(calls[0].key, CN);
  assert.equal(calls[0].query.organization_id, 'org-1');
  assert.deepEqual(c.state.vmRows, [{ id: THAMES, name: 'Thamesmead Roofing Services Ltd' }]);
  assert.equal(c.state.vmSource, 'connector');
});

test('every page is read', async () => {
  make(ADMIN, 'user');
  handlers[CN] = (u) => {
    const off = Number(u.searchParams.get('offset') || 0);
    return off === 0
      ? [200, { total: 501, limit: 500, offset: 0, data: Array.from({ length: 500 }, (_, i) => ({ id: 'id-' + i, vendor_name: 'Vendor ' + i })) }]
      : [200, { total: 501, limit: 500, offset: 500, data: [{ id: 'id-500', vendor_name: 'Vendor 500' }] }];
  };
  await c.vmLoad();
  assert.equal(c.state.vmRows.length, 501);
});

test('when the connector cannot read the list, an admin reads it through the SELECT-only query, ids as text', async () => {
  make(ADMIN, 'user');
  handlers[CN] = [500, { detail: 'invalid UUID' }];
  handlers[UDR] = [200, { rows: [{ id: 'V-01', vendor_name: 'Legacy Vendor' }, { id: THAMES, vendor_name: 'Thamesmead Roofing Services Ltd' }], count: 2 }];
  await c.vmLoad();
  const q = calls.find((x) => x.key === UDR);
  assert.match(q.body.sql, /^SELECT v\.id::text AS id, v\.vendor_name FROM plenum_cafm\.vendors v WHERE v\.organization_id::text = :org/);
  assert.deepEqual(q.body.params, { org: 'org-1' });
  assert.equal(c.state.vmRows.length, 2);
  assert.equal(c.state.vmSource, 'udr');
});

test('a plain user is never sent to the admin-only query; the list is simply not read', async () => {
  make(USER, 'user');
  handlers[CN] = [500, { detail: 'invalid UUID' }];
  await c.vmLoad();
  assert.equal(calls.some((x) => x.key === UDR), false);
  assert.equal(c.state.vmRows, null);
  assert.match(c.state.vmError, /.+/);
});

test('a vendor only in the vendor list is on Hoist vendors and in the Vendors directory', async () => {
  make(ADMIN, 'user');
  handlers[CN] = page([{ id: THAMES, vendor_name: 'Thamesmead Roofing Services Ltd' }]);
  await c.vmLoad();
  c.setState({ ccLive: Object.assign({}, c.ccData(), { certs: [], vendors: [], buildings: [] }),
    vpRaw: { fetchedAt: '2026-10-12T08:00:00Z', errors: {}, summary: { scorecards: [] }, contracts: { parameters: [] }, certificates: [] } });
  c.setState({ view: 'vh' });
  let v = c.renderVals();
  const row = v.vhHoisted.find((x) => x.name === 'Thamesmead Roofing Services Ltd');
  assert.ok(row);
  assert.equal(row.local, false);
  assert.equal(row.status, 'No certificates');
  c.setState({ view: 'vp' });
  v = c.renderVals();
  assert.ok(v.vpList.some((x) => x.name === 'Thamesmead Roofing Services Ltd'));
});

test('a certificate naming a vendor already in the vendor list is not "detected"', async () => {
  make(ADMIN, 'user');
  handlers[CN] = page([{ id: THAMES, vendor_name: 'Thamesmead Roofing Services Ltd' }]);
  await c.vmLoad();
  c.setState({ view: 'vh', ccLive: Object.assign({}, c.ccData(), { vendors: [], buildings: [], certs: [
    { id: 'c9', kind: 'building', nm: 'Roof inspection', building: 'Bishopsgate Tower', holder: 'Bishopsgate Tower', vendor: 'Thamesmead Roofing Services Ltd', vendorId: null, vendorOnRecord: false, exp: '01 Jan 2028' }
  ] }) });
  assert.equal(c.renderVals().vhDet.length, 0);
});

test('when the list cannot be read, Hoist vendors says so', async () => {
  make(USER, 'user');
  handlers[CN] = [500, { detail: 'invalid UUID' }];
  await c.vmLoad();
  c.setState({ role: 'user', account: ADMIN, view: 'vh', ccLive: Object.assign({}, c.ccData(), { certs: [], vendors: [], buildings: [] }) });
  assert.match(c.renderVals().vhListNote, /could not be read/);
});

// Delete vendor (12 Oct 2026): a retired vendor stays in the table with status 'deleted'. The
// SELECT-only fallback leaves it out by the rule the service uses (live_vendor_sql), so an admin
// whose list comes through it does not see a deleted vendor back in Hoist vendors or the directory.
test('the fallback read leaves retired vendors out', async () => {
  make(ADMIN, 'user');
  handlers[CN] = [500, { detail: 'invalid UUID' }];
  handlers[UDR] = [200, { rows: [{ id: 'V-01', vendor_name: 'Legacy Vendor' }], count: 1 }];
  await c.vmLoad();
  const q = calls.find((x) => x.key === UDR);
  // live_vendor_sql's rule exactly: status read through to_jsonb, so a vendors table without the
  // column (it is added by the connector's schema expansion) still reads (12 Oct 2026).
  assert.match(q.body.sql, /WHERE v\.organization_id::text = :org AND COALESCE\(lower\(to_jsonb\(v\)->>'status'\),'active'\) <> 'deleted' ORDER BY v\.vendor_name/);
});
