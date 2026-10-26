// reviewApi — every open-coding and sampling request Trace Lab's page makes reaches its route under
// /backend/trace-lab/api/trace-lab with its method, the company goes only as a superadmin's View as (never one
// the caller names), ids are escaped into their path, and the bodies and query strings are the ones the service reads.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window = { location: { origin: 'http://test.local' } };
let seen = [];
globalThis.fetch = async (url, init) => {
  const body = init && init.body;
  seen.push({ url: new URL(String(url)), method: (init && init.method) || 'GET', json: typeof body === 'string' ? JSON.parse(body) : undefined });
  return { ok: true, status: 200, statusText: 'OK', text: async () => '{"ok":true}', headers: { get: () => null } };
};
const { reviewApi } = await import('../reviewApi.js');
const { setActingOrg } = await import('../../api/client.js');
const P = '/backend/trace-lab/api/trace-lab';
const ORG = '11111111-1111-1111-1111-111111111111';

beforeEach(() => { seen = []; setActingOrg(null); });

const CALLS = [
  ['annotations', [{ conversation_key: 's:a', turn_id: 't1' }], 'GET', '/annotations'],
  ['saveVerdict', ['t1', { verdict: 'right', base: null }], 'PUT', '/turns/t1/verdict'],
  ['deleteVerdict', ['t1', 'v1'], 'DELETE', '/turns/t1/verdict'],
  ['annotationHistory', ['t1'], 'GET', '/turns/t1/annotation-history'],
  ['addComment', [{ conversation_key: 's:a', note: 'x' }], 'POST', '/comments'],
  ['editComment', ['l1', { note: 'y', base: 'c1' }], 'PUT', '/comments/l1'],
  ['deleteComment', ['l1', 'c2'], 'DELETE', '/comments/l1'],
  ['reviewQueue', [{ limit: 20 }], 'GET', '/queue'],
  ['samplingPlan', [{}], 'GET', '/sampling/plan'],
  ['samplingPreview', [{ strategy: 'uniform' }], 'GET', '/sampling/preview'],
  ['createBatch', [{ strategy: 'uniform' }], 'POST', '/sampling/batches'],
  ['batches', [{ include_withdrawn: true }], 'GET', '/sampling/batches'],
  ['batch', ['b1'], 'GET', '/sampling/batches/b1'],
  ['withdrawBatch', ['b1', 'wrong period'], 'POST', '/sampling/batches/b1/withdraw'],
  ['manifest', [], 'GET', '/sampling/manifest'],
  ['mapData', [{ since: 'a', until: 'b' }], 'GET', '/map'],
  ['progress', [{ block: 10 }], 'GET', '/progress']
];

test('every review call reaches its route with its method, and only these exist', async () => {
  assert.deepEqual(Object.keys(reviewApi).sort(), CALLS.map((c) => c[0]).sort());
  for (const [name, args, method, path] of CALLS) {
    await reviewApi[name](...args);
    const last = seen.at(-1);
    assert.equal(last.method, method, name);
    assert.equal(last.url.pathname, P + path, name);
    assert.equal(last.url.searchParams.has('organization_id'), false, name + ' names no company of its own');
  }
});

test('a superadmin viewing as a company sends that company on every call', async () => {
  setActingOrg(ORG);
  for (const [name, args] of CALLS) {
    await reviewApi[name](...args);
    assert.equal(seen.at(-1).url.searchParams.get('organization_id'), ORG, name);
  }
});

test('a caller can never name a company: a query\'s organization_id is dropped, and View as wins', async () => {
  await reviewApi.annotations({ conversation_key: 's:a', organization_id: 'someone-else' });
  assert.equal(seen.at(-1).url.searchParams.has('organization_id'), false);
  setActingOrg(ORG);
  await reviewApi.reviewQueue({ limit: 5, organization_id: 'someone-else' });
  assert.equal(seen.at(-1).url.searchParams.get('organization_id'), ORG);
  assert.equal(seen.at(-1).url.searchParams.get('limit'), '5');
});

test('queries and bodies are the ones the service reads', async () => {
  await reviewApi.annotations({ conversation_key: 's:abc', turn_id: 't9' });
  let q = seen.at(-1).url.searchParams;
  assert.deepEqual([q.get('conversation_key'), q.get('turn_id')], ['s:abc', 't9']);
  const verdict = { verdict: 'wrong', note: 'Counted July.', anchor: { span_id: null, field: 'answer', quote: '758', occurrence: 2 }, base: 'v1' };
  await reviewApi.saveVerdict('t9', verdict);
  assert.deepEqual(seen.at(-1).json, verdict);
  await reviewApi.deleteVerdict('t9', 'v7');
  assert.equal(seen.at(-1).url.searchParams.get('base'), 'v7');
  assert.equal(seen.at(-1).json, undefined, 'a delete sends no body');
  await reviewApi.deleteComment('l1', 'c3');
  assert.equal(seen.at(-1).url.searchParams.get('base'), 'c3');
  await reviewApi.editComment('l1', { note: 'y', base: 'c1' });
  assert.deepEqual(seen.at(-1).json, { note: 'y', base: 'c1' });
  await reviewApi.withdrawBatch('b1', 'wrong period');
  assert.deepEqual(seen.at(-1).json, { reason: 'wrong period' });
  await reviewApi.batches({ include_withdrawn: true });
  assert.equal(seen.at(-1).url.searchParams.get('include_withdrawn'), 'true');
  await reviewApi.samplingPreview({ strategy: 'dimension', dimension: 'page', k: 30, seed: 7, since: 'a', until: 'b' });
  q = seen.at(-1).url.searchParams;
  assert.deepEqual([q.get('strategy'), q.get('dimension'), q.get('k'), q.get('seed')], ['dimension', 'page', '30', '7']);
  await reviewApi.createBatch({ strategy: 'uniform', k: 15 });
  assert.deepEqual(seen.at(-1).json, { strategy: 'uniform', k: 15 });
  await reviewApi.progress({ since: 'a', until: 'b', block: 20 });
  assert.equal(seen.at(-1).url.searchParams.get('block'), '20');
});

test('ids are escaped into their path', async () => {
  await reviewApi.saveVerdict('a/b c', { verdict: 'right', base: null });
  assert.equal(seen.at(-1).url.pathname, P + '/turns/a%2Fb%20c/verdict');
  await reviewApi.editComment('x?y', { note: 'n', base: null });
  assert.equal(seen.at(-1).url.pathname, P + '/comments/x%3Fy');
  await reviewApi.batch('../b');
  assert.equal(seen.at(-1).url.pathname, P + '/sampling/batches/..%2Fb');
});

test('the file reuses only the app client and api.js, makes no raw request, and never reaches for the default company', () => {
  const src = readFileSync(new URL('../reviewApi.js', import.meta.url), 'utf8');
  assert.equal(/currentOrgId|ORG_ID|getActingOrg/.test(src), false);
  const imports = [...src.matchAll(/^import .* from '([^']+)';$/gm)].map((m) => m[1]);
  assert.deepEqual(imports, ['../api/client.js', './api.js']);
  assert.equal(/\bfetch\(/.test(src.replace(/apiFetch\(/g, '')), false);
});
