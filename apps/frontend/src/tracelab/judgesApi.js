// judgesApi — every judge and code-check request Trace Lab's page makes (the Judges tab), in one place so the
// service's judges routes are named once. Same rules as reviewApi.js and taxonomyApi.js: every call goes through the
// app's apiFetch (the bearer, the refresh-once retry, the stale-company check), and the company is named only for a
// superadmin's "View as" (api.js orgParams): a caller can never name a company itself. One call per route.
//
// Readiness, splits, a judge's page, runs, triage, freeze, the decision, the threshold and the failure report answer
// 403 to anyone but the platform team, and the Judges tab only calls them for that team. The table and the code checks
// are everyone's.
import { apiFetch } from '../api/client.js';
import { BASE, PREFIX, orgParams } from './api.js';

const enc = encodeURIComponent;
const mode = (id, tail) => '/modes/' + enc(id) + tail;
const judge = (id, tail) => '/judges/' + enc(id) + (tail || '');
const run = (id, tail) => '/judge-runs/' + enc(id) + (tail || '');

function query(params) {
  const q = Object.assign({}, params || {});
  delete q.organization_id;
  return Object.assign(q, orgParams());
}

// `path` is relative to /api/trace-lab. apiFetch leaves out empty, null and undefined query values, and sends a body
// only when there is one. `raw` asks for a file back ({blob, filename, type}).
function call(method, path, o) {
  const opts = { method, query: query(o.params), timeoutMs: o.timeoutMs };
  if (o.body !== undefined) opts.body = o.body;
  if (o.raw) opts.raw = true;
  return apiFetch(BASE, PREFIX + path, opts);
}

export const judgesApi = {
  checks: () => call('GET', '/checks', { timeoutMs: 15000 }),
  turnChecks: (turnId) => call('GET', '/checks/turns/' + enc(turnId), { timeoutMs: 20000 }),
  judgeReadiness: (modeId) => call('GET', mode(modeId, '/judge-readiness'), { timeoutMs: 20000 }),
  createSplit: (modeId, body) => call('POST', mode(modeId, '/splits'), { body: body || {}, timeoutMs: 60000 }),
  activeSplit: (modeId) => call('GET', mode(modeId, '/splits/active'), { timeoutMs: 20000 }),
  judges: (q) => call('GET', '/judges', { params: q, timeoutMs: 20000 }),
  createJudge: (body) => call('POST', '/judges', { body: body || {}, timeoutMs: 30000 }),
  judge: (judgeId) => call('GET', judge(judgeId), { timeoutMs: 20000 }),
  previewJudge: (judgeId, turnId) => call('POST', judge(judgeId, '/preview'), { body: { turn_id: turnId }, timeoutMs: 90000 }),
  estimateJudgeRun: (judgeId, split) => call('GET', judge(judgeId, '/estimate'), { params: { split: split || 'dev' }, timeoutMs: 20000 }),
  startJudgeRun: (judgeId, body) => call('POST', judge(judgeId, '/runs'), { body: body || {}, timeoutMs: 30000 }),
  judgeRun: (runId) => call('GET', run(runId), { timeoutMs: 20000 }),
  triageDisagreement: (runId, turnId, body) => call('PATCH', run(runId, '/disagreements/' + enc(turnId)), { body: body || {}, timeoutMs: 15000 }),
  freezeJudge: (judgeId, body) => call('POST', judge(judgeId, '/freeze'), { body: body || {}, timeoutMs: 15000 }),
  setJudgeDecision: (judgeId, body) => call('PATCH', judge(judgeId, '/decision'), { body: body || {}, timeoutMs: 15000 }),
  setJudgeThreshold: (judgeId, body) => call('PATCH', judge(judgeId, '/threshold'), { body: body || {}, timeoutMs: 15000 }),
  // format 'md': the report as a file to save ({blob, filename, type}); anything else: the JSON entries.
  failureReport: (format) => call('GET', '/failure-report', format === 'md' ? { params: { format: 'md' }, raw: true, timeoutMs: 60000 } : { params: { format: 'json' }, timeoutMs: 60000 })
};
