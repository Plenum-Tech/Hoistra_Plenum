// watchApi — every Watch request Trace Lab's page makes (monitoring batches, the chart and code-check reads, the
// automatic-vs-people trend, eval cases, repeats, eval runs and release checks) reaches its route under
// /backend/trace-lab/api/trace-lab with its method, path, body and query; the release report comes back as a file;
// the company goes only as a superadmin's View as (never one the caller names), on every call; ids are escaped into
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
    text: async () => (md ? '# Release check\n' : '{"ok":true}'),
    blob: async () => new Blob(['# Release check\n'], { type: 'text/markdown' }),
    headers: { get: (h) => (md && /content-disposition/i.test(h) ? 'attachment; filename="release-check-r1.md"'
      : md && /content-type/i.test(h) ? 'text/markdown; charset=utf-8' : null) }
  };
};
const { watchApi } = await import('../watchApi.js');
const { setActingOrg } = await import('../../api/client.js');

const B = '/backend/trace-lab/api/trace-lab';
// [name, args, method, path]: the 24 calls of the Watch tab.
const CALLS = [
  ['monitorPlan', [{ rate: 0.25 }], 'GET', '/monitor/plan'],
  ['startMonitorBatch', [{}], 'POST', '/monitor/batches'],
  ['resumeMonitorBatch', ['b1'], 'POST', '/monitor/batches/b1/resume'],
  ['monitorBatches', [{ limit: 20 }], 'GET', '/monitor/batches'],
  ['monitorBatch', ['b1'], 'GET', '/monitor/batches/b1'],
  ['monitorChart', ['m1', 12], 'GET', '/monitor/chart'],
  ['monitorCodeChecks', [12], 'GET', '/monitor/code-checks'],
  ['automationTrend', [12], 'GET', '/monitor/automation'],
  ['openReviewBatch', ['b1', 'm1'], 'POST', '/monitor/batches/b1/review-batch'],
  ['evalCases', [{}], 'GET', '/eval-cases'],
  ['createEvalCase', [{}], 'POST', '/eval-cases'],
  ['evalCase', ['c1'], 'GET', '/eval-cases/c1'],
  ['editEvalCase', ['c1', { base: 1 }], 'PATCH', '/eval-cases/c1'],
  ['confirmEvalCase', ['c1', { base: 1 }], 'POST', '/eval-cases/c1/confirm'],
  ['retireEvalCase', ['c1', { base: 1, reason: 'duplicate' }], 'POST', '/eval-cases/c1/retire'],
  ['baselineEvalCase', ['c1', {}], 'POST', '/eval-cases/c1/baseline'],
  ['repeatEvalCase', ['c1', { n: 10 }], 'POST', '/eval-cases/c1/repeat'],
  ['repeatTurn', ['t1', { n: 10, mode_ids: ['m1'] }], 'POST', '/turns/t1/repeat'],
  ['gradeEvalRun', ['r1', {}], 'POST', '/eval-runs/r1/grade'],
  ['evalRun', ['r1'], 'GET', '/eval-runs/r1'],
  ['startReleaseCheck', [{ k: 5 }], 'POST', '/release-checks'],
  ['releaseChecks', [{}], 'GET', '/release-checks'],
  ['releaseCheck', ['r1'], 'GET', '/release-checks/r1'],
  ['releaseCheckFile', ['r1', 'md', 'Northbridge Estates Ltd'], 'GET', '/release-checks/r1']
];

beforeEach(() => { calls = []; setActingOrg(null); });

test('one call per Watch route, each with its method and path; only these exist', async () => {
  assert.equal(CALLS.length, 24);
  assert.deepEqual(Object.keys(watchApi).sort(), CALLS.map((c) => c[0]).sort());
  for (const [name, args, method, path] of CALLS) {
    calls = [];
    await watchApi[name](...args);
    assert.equal(calls.length, 1, name);
    assert.equal(calls[0].method, method, name);
    assert.equal(calls[0].url.pathname, B + path, name);
  }
});

test('every Watch call carries the view-as company and only that', async () => {
  for (const [name, args] of CALLS) {
    setActingOrg('org-x');
    calls = [];
    await watchApi[name](...args);
    assert.equal(calls[0].url.searchParams.get('organization_id'), 'org-x', name);
    setActingOrg(null);
    calls = [];
    await watchApi[name](...args);
    assert.equal(calls[0].url.searchParams.has('organization_id'), false, name + ' sends no company of its own');
  }
  calls = [];
  await watchApi.monitorBatches({ organization_id: 'org-other', limit: 5 });
  assert.equal(calls[0].url.searchParams.has('organization_id'), false, 'a named company is dropped');
  assert.equal(calls[0].url.searchParams.get('limit'), '5');
  setActingOrg('org-x');
  calls = [];
  await watchApi.evalCases({ organization_id: 'org-other' });
  assert.equal(calls[0].url.searchParams.get('organization_id'), 'org-x');
});

test('bodies and queries reach the route as given, with the source defaults', async () => {
  await watchApi.monitorPlan({ rate: 0.25 });
  assert.equal(calls[0].url.searchParams.get('rate'), '0.25');
  calls = [];
  await watchApi.startMonitorBatch();
  assert.deepEqual(calls[0].body, {}, 'a start with no options still sends a body');
  calls = [];
  await watchApi.startMonitorBatch({ sample_rate: 0.5, usd_cap: 3 });
  assert.deepEqual(calls[0].body, { sample_rate: 0.5, usd_cap: 3 });
  calls = [];
  await watchApi.resumeMonitorBatch('b1');
  assert.deepEqual(calls[0].body, {});
  calls = [];
  await watchApi.monitorChart('m1');
  assert.equal(calls[0].url.searchParams.get('mode_id'), 'm1');
  assert.equal(calls[0].url.searchParams.get('periods'), '12', 'twelve weeks unless asked otherwise');
  calls = [];
  await watchApi.monitorChart('m1', 26);
  assert.equal(calls[0].url.searchParams.get('periods'), '26');
  calls = [];
  await watchApi.monitorCodeChecks();
  assert.equal(calls[0].url.searchParams.get('periods'), '12');
  calls = [];
  await watchApi.automationTrend();
  assert.equal(calls[0].url.searchParams.get('weeks'), '12');
  calls = [];
  await watchApi.openReviewBatch('b1', 'm1');
  assert.deepEqual(calls[0].body, { mode_id: 'm1' });
  calls = [];
  await watchApi.confirmEvalCase('c1', { base: 2, expected_answer: 'There were 786.', evaluation: 'objective' });
  assert.deepEqual(calls[0].body, { base: 2, expected_answer: 'There were 786.', evaluation: 'objective' });
  calls = [];
  await watchApi.repeatTurn('t1', { n: 10, mode_ids: ['m1'] });
  assert.deepEqual(calls[0].body, { n: 10, mode_ids: ['m1'] });
  calls = [];
  await watchApi.releaseCheck('r1');
  assert.equal(calls[0].url.searchParams.get('format'), 'json');
});

test('ids are escaped into their path', async () => {
  await watchApi.monitorBatch('a/b c');
  assert.equal(calls[0].url.pathname, B + '/monitor/batches/a%2Fb%20c');
  calls = [];
  await watchApi.evalCase('c/1');
  assert.equal(calls[0].url.pathname, B + '/eval-cases/c%2F1');
  calls = [];
  await watchApi.repeatTurn('t/1', { n: 2, mode_ids: ['m1'] });
  assert.equal(calls[0].url.pathname, B + '/turns/t%2F1/repeat');
});

test('the release report downloads as a file, in the format and under the company name asked for', async () => {
  const md = await watchApi.releaseCheckFile('r1', 'md', 'Northbridge Estates Ltd');
  assert.equal(calls[0].url.searchParams.get('format'), 'md');
  assert.equal(calls[0].url.searchParams.get('company_name'), 'Northbridge Estates Ltd');
  assert.equal(md.filename, 'release-check-r1.md');
  assert.equal(await md.blob.text(), '# Release check\n');
  calls = [];
  await watchApi.releaseCheckFile('r1', 'anything-else');
  assert.equal(calls[0].url.searchParams.get('format'), 'json', 'any format but md is the JSON report');
  assert.equal(calls[0].url.searchParams.has('company_name'), false, 'no company name, none sent');
});

test('the file names the company only through the shared helper and every call is one routed line', () => {
  const src = readFileSync(new URL('../watchApi.js', import.meta.url), 'utf8');
  assert.equal(/currentOrgId|ORG_ID/.test(src), false);
  const lines = src.split('\n').filter((l) => /^ {2}\w+: \(/.test(l));
  assert.equal(lines.length, CALLS.length);
  for (const l of lines) assert.ok(l.includes('call('), l);
});
