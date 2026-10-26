// taxonomyApi — every failure-mode, failure-mode proposal, unfiled-note, label, AI-suggestion and requirement request
// Trace Lab's page makes, in one place so the service's taxonomy routes are named once. Same rules as reviewApi.js:
// every call goes through the app's apiFetch (the bearer, the refresh-once retry, the stale-company check), and the
// company is named only for a superadmin's "View as" (api.js orgParams): a caller can never name a company itself. The
// platform team works with no company named, and a company admin's calls carry none. These routes also PUT, PATCH and
// POST without a body, which api.js's `tl` helper does not do, so the one local helper below does what `tl` does for
// every verb.
import { apiFetch } from '../api/client.js';
import { BASE, PREFIX, orgParams } from './api.js';

const enc = encodeURIComponent;
const mode = (id, tail) => '/modes/' + enc(id) + (tail || '');
const modeProposal = (id, tail) => '/mode-proposals/' + enc(id) + tail;
const note = (id, tail) => '/notes/' + enc(id) + tail;
const label = (turnId, modeId, tail) => '/labels/' + enc(turnId) + '/' + enc(modeId) + (tail || '');
const run = (id, tail) => '/proposal-runs/' + enc(id) + (tail || '');
const requirement = (code, tail) => '/requirements/' + enc(code) + tail;

function query(params) {
  const q = Object.assign({}, params || {});
  delete q.organization_id;
  return Object.assign(q, orgParams());
}

// `path` is relative to /api/trace-lab. apiFetch leaves out empty, null and undefined query values, and sends a
// body only when there is one.
function call(method, path, o) {
  const opts = { method, query: query(o.params), timeoutMs: o.timeoutMs };
  if (o.body !== undefined) opts.body = o.body;
  return apiFetch(BASE, PREFIX + path, opts);
}

export const taxonomyApi = {
  modes: (q) => call('GET', '/modes', { params: q, timeoutMs: 20000 }),
  mode: (modeId) => call('GET', mode(modeId), { timeoutMs: 20000 }),
  createMode: (body) => call('POST', '/modes', { body, timeoutMs: 20000 }),
  editMode: (modeId, body) => call('PATCH', mode(modeId), { body, timeoutMs: 20000 }),
  renameMode: (modeId, body) => call('POST', mode(modeId, '/rename'), { body, timeoutMs: 20000 }),
  mergeModes: (body) => call('POST', '/modes/merge', { body, timeoutMs: 20000 }),
  splitMode: (modeId, body) => call('POST', mode(modeId, '/split'), { body, timeoutMs: 20000 }),
  retireMode: (modeId, body) => call('POST', mode(modeId, '/retire'), { body, timeoutMs: 20000 }),
  restoreMode: (modeId, body) => call('POST', mode(modeId, '/restore'), { body, timeoutMs: 20000 }),
  modeProposals: (q) => call('GET', '/mode-proposals', { params: q, timeoutMs: 20000 }),
  proposeMode: (body) => call('POST', '/mode-proposals', { body, timeoutMs: 20000 }),
  acceptModeProposal: (modeId, body) => call('POST', modeProposal(modeId, '/accept'), { body: body || {}, timeoutMs: 20000 }),
  rejectModeProposal: (modeId, body) => call('POST', modeProposal(modeId, '/reject'), { body, timeoutMs: 20000 }),
  unfiledNotes: (q) => call('GET', '/notes/unfiled', { params: q, timeoutMs: 20000 }),
  fileNote: (annotationId, body) => call('POST', note(annotationId, '/file'), { body, timeoutMs: 20000 }),
  disposeNote: (annotationId, body) => call('POST', note(annotationId, '/dispose'), { body, timeoutMs: 20000 }),
  undisposeNote: (annotationId) => call('POST', note(annotationId, '/undispose'), { timeoutMs: 20000 }),
  labelsMatrix: (q) => call('GET', '/labels/matrix', { params: q, timeoutMs: 30000 }),
  turnLabels: (turnId) => call('GET', '/turns/' + enc(turnId) + '/labels', { timeoutMs: 20000 }),
  setLabel: (turnId, modeId, body) => call('PUT', label(turnId, modeId), { body, timeoutMs: 20000 }),
  labelHistory: (turnId, modeId) => call('GET', label(turnId, modeId, '/history'), { timeoutMs: 20000 }),
  proposalEstimate: (q) => call('GET', '/proposals/estimate', { params: q, timeoutMs: 60000 }),
  startProposalRun: (body) => call('POST', '/proposal-runs', { body, timeoutMs: 60000 }),
  proposalRuns: () => call('GET', '/proposal-runs', { timeoutMs: 20000 }),
  proposalRun: (runId) => call('GET', run(runId), { timeoutMs: 20000 }),
  resumeProposalRun: (runId, body) => call('POST', run(runId, '/resume'), { body: body || {}, timeoutMs: 30000 }),
  pauseProposalRun: (runId) => call('POST', run(runId, '/pause'), { timeoutMs: 20000 }),
  proposals: (q) => call('GET', '/proposals', { params: q, timeoutMs: 20000 }),
  decideProposal: (proposalId, body) => call('POST', '/proposals/' + enc(proposalId) + '/decide', { body, timeoutMs: 20000 }),
  bulkCheck: (body) => call('POST', '/proposals/bulk/check', { body, timeoutMs: 30000 }),
  bulkApprove: (body) => call('POST', '/proposals/bulk/approve', { body, timeoutMs: 60000 }),
  requirements: (q) => call('GET', '/requirements', { params: q, timeoutMs: 20000 }),
  createRequirement: (body) => call('POST', '/requirements', { body, timeoutMs: 20000 }),
  reviseRequirement: (code, body) => call('POST', requirement(code, '/revisions'), { body, timeoutMs: 20000 }),
  withdrawRequirement: (code, body) => call('POST', requirement(code, '/withdraw'), { body, timeoutMs: 20000 }),
  seedRequirements: () => call('POST', '/requirements/seed', { timeoutMs: 30000 })
};
