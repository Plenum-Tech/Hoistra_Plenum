// goldenApi — every golden call reaches its route under /backend/trace-lab/api/trace-lab with its method, an upload
// is multipart (file, sheet, name, note; a check adds dry_run), a template comes back as a file, the company goes
// only as a superadmin's View as, and the closing page's pause is one keepalive request carrying the token and the
// company the run was started under.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window = { location: { origin: 'http://test.local' } };
let seen = [];
globalThis.fetch = async (url, init) => {
  const body = init && init.body;
  seen.push({ url: new URL(String(url)), method: (init && init.method) || 'GET', form: body instanceof FormData ? body : null,
    json: typeof body === 'string' ? JSON.parse(body) : undefined, headers: (init && init.headers) || {}, keepalive: !!(init && init.keepalive) });
  return { ok: true, status: 200, statusText: 'OK', text: async () => '{"ok":true}', blob: async () => new Blob(['ID,Question']),
    headers: { get: (h) => (/content-disposition/i.test(h) ? 'attachment; filename="golden-set-template.csv"' : 'text/csv') } };
};
const { goldenApi, pauseOnClose, TEMPLATE_PATHS } = await import('../goldenApi.js');
const { setActingOrg, configureAuth } = await import('../../api/client.js');
const P = '/backend/trace-lab/api/trace-lab';
const R = '6f1c2d3e-0000-4000-8000-000000000001';
const S = '7f1c2d3e-0000-4000-8000-000000000002';
const file = new File(['ID,Question,Expected answer\n1,q,a\n'], 'bishopsgate.csv', { type: 'text/csv' });

beforeEach(() => { seen = []; setActingOrg(null); configureAuth({ getToken: () => null }); });

const CALLS = [
  ['sets', [], 'GET', '/golden-sets'],
  ['upload', [file, { sheet: 'Golden set', name: 'Tower', note: 'v4', dryRun: true }], 'POST', '/golden-sets'],
  ['cases', [S, { q: 'floors', flag: 'stale', limit: 50, offset: 0 }], 'GET', '/golden-sets/' + S + '/cases'],
  ['activate', [S], 'POST', '/golden-sets/' + S + '/activate'],
  ['retire', [S], 'POST', '/golden-sets/' + S + '/retire'],
  ['template', ['plain'], 'GET', '/golden-sets/template.csv'],
  ['estimate', [{ k: 3 }], 'POST', '/golden-runs/estimate'],
  ['start', [{ k: 1, purpose: 'adhoc' }], 'POST', '/golden-runs'],
  ['runs', [{ limit: 50 }], 'GET', '/golden-runs'],
  ['run', [R], 'GET', '/golden-runs/' + R],
  ['step', [R], 'POST', '/golden-runs/' + R + '/step'],
  ['pause', [R], 'POST', '/golden-runs/' + R + '/pause'],
  ['resume', [R, { usd_cap: 8 }], 'POST', '/golden-runs/' + R + '/resume'],
  ['cancel', [R], 'POST', '/golden-runs/' + R + '/cancel'],
  ['regrade', [R, { only: 'all' }], 'POST', '/golden-runs/' + R + '/regrade'],
  ['compare', [R, S], 'GET', '/golden-runs/compare'],
  ['expected', ['turn-1'], 'GET', '/golden/turns/turn-1'],
  ['reveal', ['turn-1'], 'POST', '/golden/turns/turn-1/reveal']
];

test('every golden call reaches its route with its method, and only these exist', async () => {
  assert.deepEqual(Object.keys(goldenApi).sort(), CALLS.map((c) => c[0]).sort());
  for (const [name, args, method, path] of CALLS) {
    await goldenApi[name](...args);
    const last = seen.at(-1);
    assert.equal(last.method, method, name);
    assert.equal(last.url.pathname, P + path, name);
    assert.equal(last.url.searchParams.has('organization_id'), false, name + ' names no company of its own');
  }
});

test('an upload is multipart with the file, sheet, name and note; only a check sends dry_run', async () => {
  await goldenApi.upload(file, { sheet: 'Golden set', name: 'Tower', note: 'v4', dryRun: true });
  let last = seen.at(-1);
  assert.equal(last.url.searchParams.get('dry_run'), 'true');
  assert.equal(last.form.get('file').name, 'bishopsgate.csv');
  assert.equal(last.form.get('sheet'), 'Golden set');
  assert.equal(last.form.get('name'), 'Tower');
  assert.equal(last.form.get('note'), 'v4');
  assert.equal(last.headers['Content-Type'], undefined, 'the browser sets the multipart boundary');
  await goldenApi.upload(file, {});
  last = seen.at(-1);
  assert.equal(last.url.searchParams.has('dry_run'), false);
  assert.equal(last.form.get('sheet'), null);
});

test('bodies and queries: resume cap, compare base and head, case filters, the two templates', async () => {
  await goldenApi.resume(R, { usd_cap: 8 });
  assert.deepEqual(seen.at(-1).json, { usd_cap: 8 });
  await goldenApi.regrade(R);
  assert.deepEqual(seen.at(-1).json, { only: 'pending' });
  await goldenApi.compare(R, S);
  assert.equal(seen.at(-1).url.searchParams.get('base'), R);
  assert.equal(seen.at(-1).url.searchParams.get('head'), S);
  await goldenApi.cases(S, { q: 'floors', flag: 'stale', limit: 50, offset: 50 });
  assert.equal(seen.at(-1).url.searchParams.get('offset'), '50');
  const out = await goldenApi.template('plain');
  assert.equal(out.filename, 'golden-set-template.csv');
  assert.equal(seen.at(-1).headers.Accept, '*/*');
  await goldenApi.template('compliance');
  assert.equal(seen.at(-1).url.pathname, P + TEMPLATE_PATHS.compliance);
  assert.equal(TEMPLATE_PATHS.compliance, '/golden-sets/templates/compliance.csv');
});

test('a superadmin viewing as a company names it on every golden call', async () => {
  setActingOrg('11111111-1111-4111-8111-111111111111');
  for (const [name, args] of CALLS) {
    await goldenApi[name](...args);
    assert.equal(seen.at(-1).url.searchParams.get('organization_id'), '11111111-1111-4111-8111-111111111111', name);
  }
});

test('the closing page pauses its run with one keepalive request, the token and the run\'s own company', () => {
  configureAuth({ getToken: () => 'tok-1' });
  setActingOrg('org-now');
  assert.equal(pauseOnClose(R, 'org-at-start'), true);
  const last = seen.at(-1);
  assert.equal(last.method, 'POST');
  assert.equal(last.keepalive, true);
  assert.equal(last.url.pathname, P + '/golden-runs/' + R + '/pause');
  assert.equal(last.url.searchParams.get('organization_id'), 'org-at-start');
  assert.equal(last.headers.Authorization, 'Bearer tok-1');
  assert.deepEqual(last.json, { reason: 'page_closed' });
  assert.equal(pauseOnClose(R, null), true);
  assert.equal(seen.at(-1).url.searchParams.has('organization_id'), false);
  assert.equal(pauseOnClose('', null), false);
});

test('the file reuses only the app client and api.js, and never the default company', () => {
  const src = readFileSync(new URL('../goldenApi.js', import.meta.url), 'utf8');
  assert.equal(/currentOrgId|ORG_ID/.test(src), false);
  const imports = [...src.matchAll(/^import .* from '([^']+)';$/gm)].map((m) => m[1]);
  assert.deepEqual(imports, ['./api.js', '../api/client.js']);
  assert.equal((src.match(/\bfetch\(/g) || []).length, 1, 'one raw request: the keepalive pause');
});
