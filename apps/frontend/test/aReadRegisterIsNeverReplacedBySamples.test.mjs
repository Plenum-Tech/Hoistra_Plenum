// A register that has been read once is never replaced by sample rows.
//
// The three fallbacks (companies, users, audit) filled the seed whenever the list was EMPTY
// at the moment a read failed. A platform read once and genuinely empty — zero companies,
// zero users, an empty trail — then met a refresh failure and was replaced by invented
// rows, and the banner in that state said "refresh failed" with no word about samples. The
// rule is "never read", not "nothing held": a successful answer, even an empty one, is the
// truth and stays. And the labels say "showing sample data" only when that is what is shown.
// The real controller in Node with a dead backend.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const READ_AT = '2026-09-23T10:00:00Z';

let c;
beforeEach(() => { Object.keys(mem).forEach((k) => { delete mem[k]; }); c = new HoistraLogic(); c.setState({ signedIn: true, role: 'admin' }); });

test('an empty platform that has been read stays empty when a refresh fails', async () => {
  c.setState({ saOn: true, saLiveLoadedAt: READ_AT, saCompanies: [], saSel: null });
  await c.saLiveLoad();
  clearTimeout(c._saLiveRetry);
  assert.equal(c.state.saCompanies.length, 0, 'a read-once empty platform was replaced by samples');
  const v = c.renderVals();
  assert.match(v.saLiveError, /refresh failed/);
  assert.doesNotMatch(v.saLiveError, /sample/i);
});

test('a never-read platform still gets the labelled samples on failure', async () => {
  c.setState({ saOn: true, saLiveLoadedAt: null, saCompanies: [] });
  await c.saLiveLoad();
  clearTimeout(c._saLiveRetry);
  assert.ok(c.state.saCompanies.length > 0);
  assert.match(c.renderVals().saLiveError, /Showing sample data/);
});

test('an empty user list that has been read stays empty, and the label does not claim samples', async () => {
  c.setState({ view: 'users', usLiveLoadedAt: READ_AT, users: [] });
  await c.usLiveLoad();
  clearTimeout(c._usLiveRetry);
  assert.equal(c.state.users.length, 0, 'a read-once empty table was replaced by sample people');
  const v = c.renderVals();
  assert.match(v.usLiveSourceLabel, /^Unreachable — /);
  assert.doesNotMatch(v.usLiveSourceLabel, /sample/i);
});

test('an empty audit trail that has been read stays empty, and the label does not claim samples', async () => {
  c.setState({ view: 'audit', auLiveLoadedAt: READ_AT, audit: [] });
  await c.auLiveLoad();
  clearTimeout(c._auLiveRetry);
  assert.equal(c.state.audit.length, 0, 'a read-once empty trail was replaced by sample entries');
  const v = c.renderVals();
  assert.match(v.auLiveSourceLabel, /^Unreachable — /);
  assert.doesNotMatch(v.auLiveSourceLabel, /sample/i);
});

test('the nav badges count nothing until the reads have answered', () => {
  const badge = (label) => (c.renderVals().navAdmin.find((n) => n.label === label) || {}).badge;
  assert.equal(badge('Users & access'), '…');
  assert.equal(badge('Audit trail'), '…');
  // One row in the shape shapeLiveUser() produces — the users page reads u.buildings.length.
  const one = { id: 'u1', name: 'Amara Osei', email: 'amara@plenum.co', title: '', buildings: [], allB: false, buildingCount: 0, ingest: false, status: 'Active', role: 'user', queries: 0, ingests: 0, last: '—', live: true };
  c.setState({ usLiveLoadedAt: READ_AT, users: [one], auLiveLoadedAt: READ_AT, audit: [] });
  assert.equal(badge('Users & access'), '1');
  assert.equal(badge('Audit trail'), '0');
});
