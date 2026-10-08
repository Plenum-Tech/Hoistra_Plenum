// What the audit trail actually asks the server for.
//
// Every filter on this page used to run in the browser over one fetched page. That is fine
// while the register is small and quietly wrong once it is not: the page would narrow 200
// rows and tell the reader it had narrowed the trail. So the filters now go on the wire,
// and these tests are about the wire — which parameters are sent, which are deliberately
// NOT sent, and that the company is never one of them.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

let sent = [];
const PAYLOAD = {
  ok: true, count: 919,
  by_outcome: { accepted: 336, reassigned: 61, overridden: 5, rejected: 7 },
  actors: [{ user_id: 'u-1', name: 'Clara Novak', role: 'user', count: 58 }],
  entries: []
};
globalThis.fetch = async (url) => {
  const u = new URL(String(url));
  sent.push({ path: u.pathname, q: Object.fromEntries(u.searchParams) });
  return { ok: true, status: 200, statusText: 'OK', text: async () => JSON.stringify(PAYLOAD) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

let c;
beforeEach(() => {
  sent = [];
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'audit' });
});
const audit = () => sent.filter((s) => s.path.endsWith('/api/admin/ingestion-audit'));

test('a plain read asks for a page and names no company', async () => {
  await c.auLiveLoad();
  const q = audit()[0].q;
  assert.equal(q.limit, '200');
  assert.ok(!('organization_id' in q),
    'the company comes from the token; sending one is the superadmin act-as override and nothing a plain read should carry');
});

test('every filter the page offers reaches the server', async () => {
  c.setState({
    auFilter: 'Accepted', auQuery: 'lift', auBuilding: 'b-77',
    auPerson: 'Clara Novak', auPersonId: 'u-1', auRange: '7 days'
  });
  await c.auLiveLoad();
  const q = audit()[0].q;
  assert.equal(q.outcome, 'accepted', 'the chip is a server filter, lower-cased to the vocabulary');
  assert.equal(q.q, 'lift');
  assert.equal(q.building_id, 'b-77');
  assert.equal(q.actor_user_id, 'u-1');
  assert.ok(q.since, 'a range sends a lower bound');
  assert.ok(!('until' in q) || q.until, 'and either no upper bound or a real one');
});

test('the All chip and the All range are the absence of a filter, not a value', async () => {
  c.setState({ auFilter: 'All', auRange: 'All' });
  await c.auLiveLoad();
  const q = audit()[0].q;
  assert.ok(!('outcome' in q), 'All is every outcome — sending it as a token would match none');
  assert.ok(!('since' in q) && !('until' in q));
});

test('a role scope goes as a role, not as a name', async () => {
  c.setState({ auPeopleScope: 'admins' });
  await c.auLiveLoad();
  assert.equal(audit()[0].q.actor_role, 'admin');
  assert.ok(!('actor_user_id' in audit()[0].q));
});

test('the server\'s own tallies are kept, not recomputed from the page', async () => {
  await c.auLiveLoad();
  assert.equal(c.state.auTotal, 919);
  assert.deepEqual(c.state.auByOutcome, PAYLOAD.by_outcome);
  assert.deepEqual(c.state.auActors, PAYLOAD.actors);
});

test('changing a filter re-reads the register rather than narrowing what is already here', async () => {
  await c.auLiveLoad();
  const before = audit().length;
  await c.auApplyFilter({ auBuilding: 'b-9' });
  assert.equal(audit().length, before + 1, 'a filter change is a new question for the server');
  assert.equal(audit()[before].q.building_id, 'b-9');
  assert.equal(c.state.auBuilding, 'b-9', 'and the state moves with it');
});

test('typing asks the register once, when the typing stops', async () => {
  // Every keystroke going on the wire is a request per character and a page that reorders
  // itself under the cursor as the answers come back out of order. The state moves at once
  // so the box stays responsive; only the question waits.
  const { AU_QUERY_DEBOUNCE_MS } = await import('../src/logic/auditTrail.js');
  c.renderVals().auSetQuery('l');
  c.renderVals().auSetQuery('li');
  c.renderVals().auSetQuery('lift');
  assert.equal(c.state.auQuery, 'lift', 'the box shows what was typed immediately');
  assert.equal(audit().length, 0, 'and nothing has been asked yet');
  await new Promise((r) => setTimeout(r, AU_QUERY_DEBOUNCE_MS + 120));
  assert.equal(audit().length, 1, 'one question for three keystrokes');
  assert.equal(audit()[0].q.q, 'lift');
});
