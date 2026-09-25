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
  const out = typeof h === 'function' ? h(u, opts) : h;
  const status = out && out.__status ? out.__status : 200;
  return { ok: status < 300, status: status, statusText: String(status), text: async () => JSON.stringify(out.__body || out) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
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
afterEach(() => { clearTimeout(c._tt); clearInterval(c._orchTick); clearInterval(c._ccTick); });

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
