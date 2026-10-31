// A report is saved under the company it was asked in (7 Oct 2026). A superadmin viewing as
// Plenum Technologies asked "Which vendors are blocked right now?" and the chat named three;
// the report made from that conversation said the blocked filter came back empty. The chat
// sends the company being viewed (deepAgents.js orgOverride); the reports client sent none,
// so operations-intelligence stored the card under the caller's home company — the platform
// company — and every refresh read that company's register instead.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

let sent;
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  sent.push({ method, path: u.pathname, org: u.searchParams.get('organization_id') });
  const out = method === 'POST' && u.pathname.endsWith('/api/reports/cards')
    ? { ok: true, report: { id: 'rep-1' }, card: { id: 'card-1' } }
    : u.pathname.endsWith('/run') ? { ok: true } : { ok: true, reports: [] };
  return { ok: true, status: 200, statusText: '200', text: async () => JSON.stringify(out) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { setActingOrg } = await import('../src/api/client.js');
const { reportsApi } = await import('../src/api/reports.js');
const { makeSession } = await import('../src/logic/sessions.js');

const OWNER = 'sa@plenum.example';
const COMPANY = 'ad88d1ed-4376-447c-8196-e4e5b81d5af6';
let c = null;
beforeEach(() => { sent = []; });
afterEach(() => {
  setActingOrg(null);
  if (c) { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._vpRetry); clearTimeout(c._qTimer); clearTimeout(c._homeRefresh); }
  c = null;
});

const boot = (viewOrgId) => {
  c = new HoistraLogic({});
  c.setState({
    signedIn: true, refreshToken: 'ref-test',
    account: { id: 'u-1', email: OWNER, role: 'superadmin' },
    viewOrgId: viewOrgId || null, viewOrgName: viewOrgId ? 'Plenum Technologies' : null,
    sessions: [makeSession({ id: 'th-1', title: 'Which vendors are blocked right now?', page: 'Home', owner: OWNER, viewOrgId: viewOrgId || null, at: 1 })]
  });
  setActingOrg(viewOrgId || null);
  return c;
};

test('a report made while viewing as a company is saved under that company', async () => {
  boot(COMPANY);
  c.rpOpenBuilder('th-1');
  await c.rpCreate();
  const create = sent.find((r) => r.method === 'POST' && r.path.endsWith('/api/reports/cards'));
  assert.ok(create, 'the card was created');
  assert.equal(create.org, COMPANY, 'the card names the company the question was asked in');
});

test('a report made in your own company names no company — nothing to override', async () => {
  boot(null);
  c.rpOpenBuilder('th-1');
  await c.rpCreate();
  const create = sent.find((r) => r.method === 'POST' && r.path.endsWith('/api/reports/cards'));
  assert.ok(create);
  assert.equal(create.org, null);
});

test('a new report, like a new card, belongs to the company being viewed', async () => {
  setActingOrg(COMPANY);
  await reportsApi.create('Weekly');
  assert.equal(sent[0].org, COMPANY);
});
