// reviewApi — every open-coding and sampling request Trace Lab's page makes (a reviewer's verdicts and
// comments, the review queue, batch sampling, the map and the progress figures), in one place so the service's
// review routes are named once. Same rules as goldenApi.js: every call goes through the app's apiFetch (the
// bearer, the refresh-once retry, the stale-company check), and the company is named only for a superadmin's
// "View as" (api.js orgParams): a caller can never name a company itself. api.js's `tl` helper only has GET and
// POST, and these routes also PUT and DELETE, so the one local helper below does what `tl` does for all four.
import { apiFetch } from '../api/client.js';
import { BASE, PREFIX, orgParams } from './api.js';

const enc = encodeURIComponent;
const turn = (id, tail) => '/turns/' + enc(id) + tail;
const comment = (id) => '/comments/' + enc(id);
const batchPath = (id, tail) => '/sampling/batches/' + enc(id) + (tail || '');

function query(params) {
  const q = Object.assign({}, params || {});
  delete q.organization_id;
  return Object.assign(q, orgParams());
}

// `path` is relative to /api/trace-lab. apiFetch leaves out empty, null and undefined query values.
function call(method, path, o) {
  const opts = { method, query: query(o.params), timeoutMs: o.timeoutMs };
  if (o.body !== undefined) opts.body = o.body;
  return apiFetch(BASE, PREFIX + path, opts);
}

export const reviewApi = {
  // One conversation's verdict states and comments (and the text of the open answer's annotatable blocks).
  annotations: (q) => call('GET', '/annotations', { params: q, timeoutMs: 15000 }),
  saveVerdict: (turnId, body) => call('PUT', turn(turnId, '/verdict'), { body, timeoutMs: 15000 }),
  deleteVerdict: (turnId, base) => call('DELETE', turn(turnId, '/verdict'), { params: { base }, timeoutMs: 15000 }),
  annotationHistory: (turnId) => call('GET', turn(turnId, '/annotation-history'), { timeoutMs: 15000 }),
  addComment: (body) => call('POST', '/comments', { body, timeoutMs: 15000 }),
  editComment: (lineageId, body) => call('PUT', comment(lineageId), { body, timeoutMs: 15000 }),
  deleteComment: (lineageId, base) => call('DELETE', comment(lineageId), { params: { base }, timeoutMs: 15000 }),
  reviewQueue: (q) => call('GET', '/queue', { params: q, timeoutMs: 15000 }),
  samplingPlan: (q) => call('GET', '/sampling/plan', { params: q, timeoutMs: 20000 }),
  samplingPreview: (q) => call('GET', '/sampling/preview', { params: q, timeoutMs: 45000 }),
  createBatch: (body) => call('POST', '/sampling/batches', { body, timeoutMs: 45000 }),
  batches: (q) => call('GET', '/sampling/batches', { params: q, timeoutMs: 15000 }),
  batch: (batchId) => call('GET', batchPath(batchId), { timeoutMs: 15000 }),
  withdrawBatch: (batchId, reason) => call('POST', batchPath(batchId, '/withdraw'), { body: { reason }, timeoutMs: 15000 }),
  manifest: () => call('GET', '/sampling/manifest', { timeoutMs: 20000 }),
  mapData: (q) => call('GET', '/map', { params: q, timeoutMs: 30000 }),
  progress: (q) => call('GET', '/progress', { params: q, timeoutMs: 30000 })
};
