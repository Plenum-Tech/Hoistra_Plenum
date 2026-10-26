// taxonomyApi — every failure-mode, note, label, AI-suggestion and requirement request Trace Lab's page makes
// reaches its route under /backend/trace-lab/api/trace-lab with its method, the company goes only as a superadmin's
// View as (never one the caller names), ids are escaped into their path, and only these calls exist.
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
const { taxonomyApi } = await import('../taxonomyApi.js');
const { setActingOrg } = await import('../../api/client.js');
const P = '/backend/trace-lab/api/trace-lab';
const ORG = '11111111-1111-1111-1111-111111111111';

const M = '00000000-0000-4000-8000-000000000001';
const A = '00000000-0000-4000-8000-0000000000a1';
const PR = '00000000-0000-4000-8000-0000000000b1';
const R = '00000000-0000-4000-8000-0000000000c1';
const CALLS = [
  ['modes', [{ include_retired: true }], 'GET', '/modes'],
  ['mode', [M], 'GET', '/modes/' + M],
  ['createMode', [{ name: 'x' }], 'POST', '/modes'],
  ['editMode', [M, { base_version: 1 }], 'PATCH', '/modes/' + M],
  ['renameMode', [M, { name: 'y', base_version: 1 }], 'POST', '/modes/' + M + '/rename'],
  ['mergeModes', [{ source_ids: [M] }], 'POST', '/modes/merge'],
  ['splitMode', [M, { base_version: 1 }], 'POST', '/modes/' + M + '/split'],
  ['retireMode', [M, { reason: 'r', base_version: 1 }], 'POST', '/modes/' + M + '/retire'],
  ['restoreMode', [M, { base_version: 2 }], 'POST', '/modes/' + M + '/restore'],
  ['modeProposals', [{ status: 'proposed' }], 'GET', '/mode-proposals'],
  ['proposeMode', [{ name: 'x', created_from: [A] }], 'POST', '/mode-proposals'],
  ['acceptModeProposal', [M, { base_version: 2 }], 'POST', '/mode-proposals/' + M + '/accept'],
  ['rejectModeProposal', [M, { reason: 'Too vague.', base_version: 2 }], 'POST', '/mode-proposals/' + M + '/reject'],
  ['unfiledNotes', [{ limit: 50 }], 'GET', '/notes/unfiled'],
  ['fileNote', [A, { mode_id: M, base: null }], 'POST', '/notes/' + A + '/file'],
  ['disposeNote', [A, { outcome: 'no_mode_fits', note: 'vague' }], 'POST', '/notes/' + A + '/dispose'],
  ['undisposeNote', [A], 'POST', '/notes/' + A + '/undispose'],
  ['labelsMatrix', [{ state: 'missing' }], 'GET', '/labels/matrix'],
  ['turnLabels', ['turn-1'], 'GET', '/turns/turn-1/labels'],
  ['setLabel', ['turn-1', M, { present: true, base: null }], 'PUT', '/labels/turn-1/' + M],
  ['labelHistory', ['turn-1', M], 'GET', '/labels/turn-1/' + M + '/history'],
  ['proposalEstimate', [{ kind: 'cells' }], 'GET', '/proposals/estimate'],
  ['startProposalRun', [{ kind: 'cells', usd_cap: 2 }], 'POST', '/proposal-runs'],
  ['proposalRuns', [], 'GET', '/proposal-runs'],
  ['proposalRun', [R], 'GET', '/proposal-runs/' + R],
  ['resumeProposalRun', [R, { usd_cap: 3 }], 'POST', '/proposal-runs/' + R + '/resume'],
  ['pauseProposalRun', [R], 'POST', '/proposal-runs/' + R + '/pause'],
  ['proposals', [{ kind: 'depth_scan' }], 'GET', '/proposals'],
  ['decideProposal', [PR, { decision: 'accept' }], 'POST', '/proposals/' + PR + '/decide'],
  ['bulkCheck', [{ action: 'accept', proposal_ids: [PR], served_at: 's' }], 'POST', '/proposals/bulk/check'],
  ['bulkApprove', [{ action: 'accept', proposal_ids: [PR], served_at: 's', bases: {} }], 'POST', '/proposals/bulk/approve'],
  ['requirements', [{}], 'GET', '/requirements'],
  ['createRequirement', [{ area: 'GEN' }], 'POST', '/requirements'],
  ['reviseRequirement', ['GEN-1', { note: 'n' }], 'POST', '/requirements/GEN-1/revisions'],
  ['withdrawRequirement', ['GEN-1', { note: 'n' }], 'POST', '/requirements/GEN-1/withdraw'],
  ['seedRequirements', [], 'POST', '/requirements/seed']
];

beforeEach(() => { seen = []; setActingOrg(null); });

test('every taxonomy call reaches its route with its method and sends no company of its own; only these exist', async () => {
  assert.deepEqual(Object.keys(taxonomyApi).sort(), CALLS.map((c) => c[0]).sort());
  for (const [name, args, method, path] of CALLS) {
    await taxonomyApi[name](...args);
    const last = seen.at(-1);
    assert.equal(last.method, method, name);
    assert.equal(last.url.pathname, P + path, name);
    assert.equal(last.url.searchParams.has('organization_id'), false, name + ' names no company of its own');
  }
  assert.equal(seen.find((x) => x.url.pathname.endsWith('/labels/matrix')).url.searchParams.get('state'), 'missing');
  assert.equal(seen.find((x) => x.url.pathname.endsWith('/modes')).url.searchParams.get('include_retired'), 'true');
  assert.equal(seen.find((x) => x.url.pathname.endsWith('/mode-proposals')).url.searchParams.get('status'), 'proposed');
  assert.deepEqual(seen.find((x) => x.url.pathname.endsWith('/mode-proposals/' + M + '/reject')).json, { reason: 'Too vague.', base_version: 2 });
  assert.deepEqual(seen.find((x) => x.method === 'PUT').json, { present: true, base: null });
  assert.deepEqual(seen.find((x) => x.url.pathname.endsWith('/resume')).json, { usd_cap: 3 });
  assert.equal(seen.find((x) => x.url.pathname.endsWith('/pause')).json, undefined, 'a pause sends no body');
});

test('a superadmin viewing as a company sends that company on every call, and a caller cannot name another', async () => {
  setActingOrg(ORG);
  for (const [name, args] of CALLS) {
    await taxonomyApi[name](...args);
    assert.equal(seen.at(-1).url.searchParams.get('organization_id'), ORG, name);
  }
  seen = [];
  await taxonomyApi.modes({ organization_id: '22222222-2222-2222-2222-222222222222' });
  assert.equal(seen[0].url.searchParams.get('organization_id'), ORG);
  setActingOrg(null);
  await taxonomyApi.modes({ organization_id: '22222222-2222-2222-2222-222222222222' });
  assert.equal(seen.at(-1).url.searchParams.has('organization_id'), false);
});

test('ids are escaped into their path', async () => {
  await taxonomyApi.turnLabels('a/b c');
  assert.equal(seen[0].url.pathname, P + '/turns/a%2Fb%20c/labels');
  await taxonomyApi.reviseRequirement('GEN 1', { note: 'n' });
  assert.equal(seen[1].url.pathname, P + '/requirements/GEN%201/revisions');
});

test('the file names the company only through the shared helper, never the build default, and each call is one routed line', () => {
  const src = readFileSync(new URL('../taxonomyApi.js', import.meta.url), 'utf8');
  assert.equal(/currentOrgId|ORG_ID/.test(src), false);
  const lines = src.split('\n').filter((l) => /^ {2}\w+: \(/.test(l));
  assert.equal(lines.length, CALLS.length);
  for (const l of lines) assert.ok(l.includes('call('), l);
});
