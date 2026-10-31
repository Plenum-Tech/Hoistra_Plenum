// watchApi — every Watch request Trace Lab's page makes (the Watch tab), in one place so the service's monitoring,
// eval-case and release-check routes are named once. Same rules as judgesApi.js, reviewApi.js and taxonomyApi.js: every
// call goes through the app's apiFetch (the bearer, the refresh-once retry, the stale-company check), and the company is
// named only for a superadmin's "View as" (api.js orgParams): a caller can never name a company itself. One call per
// route.
//
// Previewing, starting and resuming monitoring and opening a review batch from it are the platform team's (any superadmin):
// for the company being viewed (orgParams names it), or for every company viewing none. Eval cases, repeats, eval runs and
// release checks belong to one company. The reads of batches, charts, code checks and the automatic-vs-people trend are a
// company admin's (the company's own) and the platform team's. The service decides who may do what; the tab only calls what
// its reader may.
import { apiFetch } from '../api/client.js';
import { BASE, PREFIX, orgParams } from './api.js';

const enc = encodeURIComponent;
const batch = (id, tail) => '/monitor/batches/' + enc(id) + (tail || '');
const kase = (id, tail) => '/eval-cases/' + enc(id) + (tail || '');
const check = (id) => '/release-checks/' + enc(id);

function query(params) {
  const q = Object.assign({}, params || {});
  delete q.organization_id;
  return Object.assign(q, orgParams());
}

// `path` is relative to /api/trace-lab. apiFetch leaves out empty, null and undefined query values, and sends a body only
// when there is one. `raw` asks for a file back ({blob, filename, type}).
function call(method, path, o) {
  const opts = { method, query: query(o.params), timeoutMs: o.timeoutMs };
  if (o.body !== undefined) opts.body = o.body;
  if (o.raw) opts.raw = true;
  return apiFetch(BASE, PREFIX + path, opts);
}

export const watchApi = {
  monitorPlan: (q) => call('GET', '/monitor/plan', { params: q, timeoutMs: 60000 }),
  startMonitorBatch: (body) => call('POST', '/monitor/batches', { body: body || {}, timeoutMs: 60000 }),
  resumeMonitorBatch: (id) => call('POST', batch(id, '/resume'), { body: {}, timeoutMs: 20000 }),
  monitorBatches: (q) => call('GET', '/monitor/batches', { params: q, timeoutMs: 20000 }),
  monitorBatch: (id, q) => call('GET', batch(id), { params: q, timeoutMs: 20000 }),
  monitorChart: (modeId, periods) => call('GET', '/monitor/chart', { params: { mode_id: modeId, periods: periods || 12 }, timeoutMs: 20000 }),
  monitorCodeChecks: (periods) => call('GET', '/monitor/code-checks', { params: { periods: periods || 12 }, timeoutMs: 20000 }),
  automationTrend: (weeks) => call('GET', '/monitor/automation', { params: { weeks: weeks || 12 }, timeoutMs: 30000 }),
  openReviewBatch: (batchId, modeId) => call('POST', batch(batchId, '/review-batch'), { body: { mode_id: modeId }, timeoutMs: 20000 }),
  evalCases: (q) => call('GET', '/eval-cases', { params: q, timeoutMs: 20000 }),
  createEvalCase: (body) => call('POST', '/eval-cases', { body: body || {}, timeoutMs: 20000 }),
  evalCase: (id) => call('GET', kase(id), { timeoutMs: 20000 }),
  editEvalCase: (id, body) => call('PATCH', kase(id), { body: body || {}, timeoutMs: 20000 }),
  confirmEvalCase: (id, body) => call('POST', kase(id, '/confirm'), { body: body || {}, timeoutMs: 20000 }),
  retireEvalCase: (id, body) => call('POST', kase(id, '/retire'), { body: body || {}, timeoutMs: 20000 }),
  baselineEvalCase: (id, body) => call('POST', kase(id, '/baseline'), { body: body || {}, timeoutMs: 60000 }),
  repeatEvalCase: (id, body) => call('POST', kase(id, '/repeat'), { body: body || {}, timeoutMs: 60000 }),
  repeatTurn: (turnId, body) => call('POST', '/turns/' + enc(turnId) + '/repeat', { body: body || {}, timeoutMs: 60000 }),
  gradeEvalRun: (runId, body) => call('POST', '/eval-runs/' + enc(runId) + '/grade', { body: body || {}, timeoutMs: 20000 }),
  evalRun: (runId) => call('GET', '/eval-runs/' + enc(runId), { timeoutMs: 60000 }),
  startReleaseCheck: (body) => call('POST', '/release-checks', { body: body || {}, timeoutMs: 60000 }),
  releaseChecks: (q) => call('GET', '/release-checks', { params: q, timeoutMs: 20000 }),
  releaseCheck: (runId) => call('GET', check(runId), { params: { format: 'json' }, timeoutMs: 60000 }),
  // The report as a file to attach to a deploy: {blob, filename, type}. Any format but 'md' is the JSON report.
  releaseCheckFile: (runId, format, companyName) => call('GET', check(runId), { params: { format: format === 'md' ? 'md' : 'json', company_name: companyName || undefined }, raw: true, timeoutMs: 60000 })
};
