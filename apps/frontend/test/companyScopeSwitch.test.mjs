// A superadmin switching company (superAdmin.js's viewAsCompany) while a live register's
// read is still in flight.
//
// Two separate faults made the Buildings table show one company's 619 buildings under
// another company's name, and each one alone is enough to do it again:
//
//   1. resetLiveData() cleared the state.*Loading mirrors but not the INSTANCE re-entry
//      guards (this._bldLoading and friends). loadLiveData() then returned at every guard
//      still set, so the switch fired no read at all for that register.
//   2. The read already in flight resolved afterwards and setState its rows — rows that
//      belong to the company the caller has just switched away from.
//
// Buildings is the register that showed it because its read carries the longest timeout
// (30s), so it is the one most often still in flight when the company changes. The bug is
// not specific to it, so the guard list in core.js is asserted here too.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const ok = (body) => ({ ok: true, status: 200, statusText: 'OK', text: async () => JSON.stringify(body) });
let calls = [];
let hold = null;                       // resolver for the first Buildings read
globalThis.fetch = (url) => {
  const u = String(url);
  calls.push(u);
  if (u.includes('/api/energy/buildings?')) {
    // The first Buildings read never settles on its own — the test lands it by hand,
    // after the switch, which is the whole point.
    if (!hold) return new Promise((res) => { hold = res; });
    return Promise.resolve(ok({ buildings: [] }));   // the company switched to has none
  }
  return Promise.resolve(ok({}));
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const OTHER_CO = Array.from({ length: 619 }, (_, i) => ({
  site_id: 'B-' + i, code: 'B-' + i, name: 'OtherCo Building ' + i, country_code: 'UK'
}));
const BALA = '1ff9670d-8aa7-4948-bd81-89eea083394b';
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
const buildingsReads = () => calls.filter((u) => u.includes('/api/energy/buildings?'));

// Every loader arms a retry on failure, and the stubbed backend answers most of them with
// an empty body — so without this the suite sits on a pile of 30s timers after the
// assertions have all passed. resetLiveData() already clears the register retries.
const cleanup = (c) => {
  c.resetLiveData();
  ['_tt', '_auLiveRetry', '_usLiveRetry', '_rpRetry'].forEach((k) => clearTimeout(c[k]));
  ['_orchTick', '_cronTimer'].forEach((k) => clearInterval(c[k]));
};

test('a company switch mid-read scopes the Buildings table to the new company', async () => {
  calls = []; hold = null;
  const c = new HoistraLogic();
  c.setState({
    signedIn: true, role: 'admin', view: 'buildings', accessToken: 'tok',
    account: { id: 'sa-1', role: 'superadmin', organization_id: '00000000-0000-0000-0000-000000000001' }
  });

  c.bldLoad();
  await tick(5);
  assert.equal(c._bldLoading, true, 'precondition: a Buildings read is in flight');

  const before = buildingsReads().length;
  c.viewAsCompany(BALA, 'bala');
  await tick(20);
  const fired = buildingsReads().slice(before);

  assert.ok(fired.length > 0,
    'the switch must re-read the Buildings table — the in-flight guard used to swallow it');
  assert.ok(fired.every((u) => u.includes('organization_id=' + BALA)),
    'the re-read must name the company being switched to: ' + JSON.stringify(fired));

  // The pre-switch read finally lands, carrying the previous company's rows.
  hold(ok({ buildings: OTHER_CO }));
  await tick(40);

  const rows = c.state.bldLive || [];
  assert.equal(rows.filter((r) => String(r.name).startsWith('OtherCo')).length, 0,
    'a response issued before the switch must not repopulate the table');
  assert.equal(c.state.bldError, '',
    'a disowned response is not a backend failure and must not be reported as one');
  assert.ok(!c._bldRetry, 'and must not arm a retry against the company just left');

  cleanup(c);
});

test('resetLiveData releases every in-flight guard the live loaders set', async () => {
  calls = []; hold = null;
  const c = new HoistraLogic();
  // Every guard named in core.js's IN_FLIGHT_GUARDS, set as if a read were in flight.
  const guards = ['_ccLoading', '_homeLoading', '_vpLoading', '_bldLoading', '_shapeLoading',
    '_enLoading', '_enPosLoading', '_asLiveLoading', '_mxLiveLoading', '_spLoading',
    '_glTablesLoading', '_usLiveLoading', '_rpLoading'];
  guards.forEach((g) => { c[g] = true; });

  c.resetLiveData();

  const stuck = guards.filter((g) => c[g]);
  assert.deepEqual(stuck, [],
    'a guard left set makes that register silently keep the previous company\'s rows');

  cleanup(c);
});
