// api — every Trace Lab request goes through the app's apiFetch to /backend/trace-lab/api/trace-lab/..., and
// names a company only while a superadmin is viewing as one (getActingOrg), never one a caller passes and never
// the build's default company. A reply that lands after a company switch is the app's own stale-scope error.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window = { location: { origin: 'http://test.local' } };
let calls = [];
let hold = null;
globalThis.fetch = async (url, opts) => {
  calls.push({ url: new URL(String(url)), method: (opts && opts.method) || 'GET', body: opts && opts.body, headers: (opts && opts.headers) || {} });
  if (hold) await hold;
  return { ok: true, status: 200, statusText: 'OK', text: async () => '{"ok":true}', headers: { get: () => null } };
};
const { tl, orgParams, BASE, PREFIX, isStaleScope } = await import('../api.js');
const { setActingOrg } = await import('../../api/client.js');

beforeEach(() => { calls = []; hold = null; setActingOrg(null); });

test('the base and prefix are the gateway mount and the service prefix', () => {
  assert.equal(BASE, '/backend/trace-lab');
  assert.equal(PREFIX, '/api/trace-lab');
});

test('a GET goes to /backend/trace-lab/api/trace-lab<path> with its parameters and no company of its own', async () => {
  const out = await tl.get('/conversations', { run: 'live', limit: 25 });
  assert.deepEqual(out, { ok: true });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'GET');
  assert.equal(calls[0].url.pathname, '/backend/trace-lab/api/trace-lab/conversations');
  assert.equal(calls[0].url.searchParams.get('run'), 'live');
  assert.equal(calls[0].url.searchParams.get('limit'), '25');
  assert.equal(calls[0].url.searchParams.has('organization_id'), false);
  assert.deepEqual(orgParams(), {});
});

test('a superadmin viewing as a company names it on every call, GET and POST', async () => {
  setActingOrg('org-viewed');
  assert.deepEqual(orgParams(), { organization_id: 'org-viewed' });
  await tl.get('/runs');
  await tl.post('/jobs', { kind: 'x' }, { dry: 1 });
  assert.equal(calls[0].url.searchParams.get('organization_id'), 'org-viewed');
  assert.equal(calls[1].method, 'POST');
  assert.equal(calls[1].url.pathname, '/backend/trace-lab/api/trace-lab/jobs');
  assert.equal(calls[1].url.searchParams.get('organization_id'), 'org-viewed');
  assert.equal(calls[1].url.searchParams.get('dry'), '1');
  assert.equal(calls[1].body, JSON.stringify({ kind: 'x' }));
});

test('a caller can never name a company: its organization_id is dropped, or replaced by the acting one', async () => {
  await tl.get('/runs', { organization_id: 'someone-else' });
  assert.equal(calls[0].url.searchParams.has('organization_id'), false);
  setActingOrg('org-viewed');
  await tl.get('/runs', { organization_id: 'someone-else' });
  assert.equal(calls[1].url.searchParams.get('organization_id'), 'org-viewed');
});

test('a reply that lands after the company changed is the app\'s stale-scope error', async () => {
  let release;
  hold = new Promise((r) => { release = r; });
  const pending = tl.get('/conversations');
  await new Promise((r) => setTimeout(r, 0));
  setActingOrg('org-2');
  release();
  await assert.rejects(pending, (e) => isStaleScope(e));
});

test('the module calls only apiFetch and never reaches for the default company', () => {
  const src = readFileSync(new URL('../api.js', import.meta.url), 'utf8');
  assert.equal(/currentOrgId|ORG_ID/.test(src), false);
  assert.equal(/\bfetch\(/.test(src.replace(/apiFetch\(/g, '')), false);
  assert.ok(src.includes("import { apiFetch, getActingOrg } from '../api/client.js';"));
});
