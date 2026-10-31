// judgesApi — every judge and code-check request Trace Lab's page makes reaches its route under
// /backend/trace-lab/api/trace-lab with its method, path, body and query; the failure report comes back as a
// Markdown file; the company goes only as a superadmin's View as (never one the caller names); ids are escaped into
// their path; and only these calls exist.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window = { location: { origin: 'http://test.local' } };
let calls = [];
globalThis.fetch = async (url, opts) => {
  const o = opts || {};
  calls.push({ url: new URL(String(url)), method: o.method || 'GET', body: o.body ? JSON.parse(o.body) : undefined });
  const md = String(url).includes('format=md');
  return {
    ok: true, status: 200, statusText: 'OK',
    text: async () => (md ? '# Failure report\n' : '{"ok":true}'),
    blob: async () => new Blob(['# Failure report\n'], { type: 'text/markdown' }),
    headers: { get: (h) => (md && /content-disposition/i.test(h) ? 'attachment; filename="failure-report.md"'
      : md && /content-type/i.test(h) ? 'text/markdown; charset=utf-8' : null) }
  };
};
const { judgesApi } = await import('../judgesApi.js');
const { setActingOrg } = await import('../../api/client.js');

const B = '/backend/trace-lab/api/trace-lab';
const CALLS = [
  ['checks', [], 'GET', '/checks'],
  ['turnChecks', ['turn/1'], 'GET', '/checks/turns/turn%2F1'],
  ['judgeReadiness', ['m-1'], 'GET', '/modes/m-1/judge-readiness'],
  ['createSplit', ['m-1', { override_min_labels: false }], 'POST', '/modes/m-1/splits'],
  ['activeSplit', ['m-1'], 'GET', '/modes/m-1/splits/active'],
  ['judges', [{ mode_id: 'm-1' }], 'GET', '/judges'],
  ['createJudge', [{ mode_id: 'm-1', kind: 'code', check_key: 'no_answer' }], 'POST', '/judges'],
  ['judge', ['j-1'], 'GET', '/judges/j-1'],
  ['previewJudge', ['j-1', 'turn-1'], 'POST', '/judges/j-1/preview'],
  ['estimateJudgeRun', ['j-1', 'test'], 'GET', '/judges/j-1/estimate'],
  ['startJudgeRun', ['j-1', { split: 'dev' }], 'POST', '/judges/j-1/runs'],
  ['judgeRun', ['r-1'], 'GET', '/judge-runs/r-1'],
  ['triageDisagreement', ['r-1', 'turn-1', { triage: 'judge_wrong', note: null, base: 2 }], 'PATCH', '/judge-runs/r-1/disagreements/turn-1'],
  ['freezeJudge', ['j-1', { stop_reason: 'Stable on dev twice.', base: 3 }], 'POST', '/judges/j-1/freeze'],
  ['setJudgeDecision', ['j-1', { decision: 'use', note: null, base: 4 }], 'PATCH', '/judges/j-1/decision'],
  ['setJudgeThreshold', ['j-1', { threshold: 0.15, base: 5 }], 'PATCH', '/judges/j-1/threshold'],
  ['failureReport', [], 'GET', '/failure-report']
];

beforeEach(() => { calls = []; setActingOrg(null); });

test('one call per judges route, each with its method and path; only these exist', async () => {
  assert.deepEqual(Object.keys(judgesApi).sort(), CALLS.map((c) => c[0]).sort());
  for (const [name, args, method, path] of CALLS) {
    calls = [];
    await judgesApi[name](...args);
    assert.equal(calls.length, 1, name);
    assert.equal(calls[0].method, method, name);
    assert.equal(calls[0].url.pathname, B + path, name);
  }
});

test('bodies and queries reach the route as given', async () => {
  await judgesApi.triageDisagreement('r-1', 'turn-1', { triage: 'label_wrong', note: 'The label was wrong.', base: 2 });
  assert.deepEqual(calls[0].body, { triage: 'label_wrong', note: 'The label was wrong.', base: 2 });
  calls = [];
  await judgesApi.previewJudge('j-1', 'turn-9');
  assert.deepEqual(calls[0].body, { turn_id: 'turn-9' });
  calls = [];
  await judgesApi.estimateJudgeRun('j-1', 'test');
  assert.equal(calls[0].url.searchParams.get('split'), 'test');
  calls = [];
  await judgesApi.estimateJudgeRun('j-1');
  assert.equal(calls[0].url.searchParams.get('split'), 'dev');
  calls = [];
  await judgesApi.judges({ mode_id: 'm-1' });
  assert.equal(calls[0].url.searchParams.get('mode_id'), 'm-1');
  calls = [];
  await judgesApi.failureReport();
  assert.equal(calls[0].url.searchParams.get('format'), 'json');
  calls = [];
  await judgesApi.createSplit('m-1');
  assert.deepEqual(calls[0].body, {}, 'a split with no options still sends a body');
  calls = [];
  await judgesApi.startJudgeRun('j-1', { split: 'test', usd_cap: 2.5 });
  assert.deepEqual(calls[0].body, { split: 'test', usd_cap: 2.5 });
});

test('the failure report downloads as a Markdown file', async () => {
  const file = await judgesApi.failureReport('md');
  assert.equal(calls[0].url.searchParams.get('format'), 'md');
  assert.equal(file.filename, 'failure-report.md');
  assert.equal(await file.blob.text(), '# Failure report\n');
});

test('a superadmin viewing a company sends it on every call; otherwise no company is sent, and a caller cannot name one', async () => {
  for (const [name, args] of CALLS) {
    setActingOrg('org-viewed');
    calls = [];
    await judgesApi[name](...args);
    assert.equal(calls[0].url.searchParams.get('organization_id'), 'org-viewed', name);
    setActingOrg(null);
    calls = [];
    await judgesApi[name](...args);
    assert.equal(calls[0].url.searchParams.has('organization_id'), false, name + ' sends no company of its own');
  }
  calls = [];
  await judgesApi.judges({ organization_id: 'org-other' });
  assert.equal(calls[0].url.searchParams.has('organization_id'), false, 'a named company is dropped');
  setActingOrg('org-viewed');
  calls = [];
  await judgesApi.judges({ organization_id: 'org-other' });
  assert.equal(calls[0].url.searchParams.get('organization_id'), 'org-viewed');
});

test('the file names the company only through the shared helper and every call is one routed line', () => {
  const src = readFileSync(new URL('../judgesApi.js', import.meta.url), 'utf8');
  assert.equal(/currentOrgId|ORG_ID/.test(src), false);
  const lines = src.split('\n').filter((l) => /^ {2}\w+: \(/.test(l));
  assert.equal(lines.length, CALLS.length);
  for (const l of lines) assert.ok(l.includes('call('), l);
});
