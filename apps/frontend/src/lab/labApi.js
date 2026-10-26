// labApi — the standalone app's requests (svc-trace-lab routes_labelling.py and routes_runs.py, under
// /api/trace-lab/lab). Every call goes through the shared request client, so the lab's own bearer and the refresh-once
// retry are applied the same way as on every other Trace Lab request. A call about one question names that question's
// company (`org`), which wins over any company kept in the browser: the platform team and the consultant see every
// company and act for the company of what they open.
import { apiFetch } from '../api/client.js';
import { BASE, PREFIX, orgParams } from '../tracelab/api.js';

const enc = encodeURIComponent;

function call(method, path, opts) {
  const o = opts || {};
  const query = Object.assign({}, orgParams(), o.query || {});
  if (o.org) query.organization_id = o.org;
  const rest = Object.assign({}, o);
  delete rest.org;
  return apiFetch(BASE, PREFIX + path, Object.assign({ method, timeoutMs: 20000 }, rest, { query }));
}

export const labApi = {
  me: () => call('GET', '/lab/me'),
  members: () => call('GET', '/lab/members'),
  member: (userId) => call('GET', '/lab/members/' + enc(userId)),
  decide: (userId, body) => call('PUT', '/lab/members/' + enc(userId), { body }),
  questions: (query) => call('GET', '/lab/questions', { query }),
  question: (key, turn) => call('GET', '/lab/questions/' + enc(key), { query: turn ? { turn } : {} }),
  turnLabels: (turnId, org) => call('GET', '/lab/turns/' + enc(turnId) + '/step-labels', { org }),
  saveLabel: (turnId, body, org) => call('PUT', '/lab/turns/' + enc(turnId) + '/step-labels', { body, org }),
  clearLabel: (turnId, lineageId, base, org) => call('DELETE', '/lab/turns/' + enc(turnId) + '/step-labels',
    { query: { lineage_id: lineageId, base }, org }),
  saveGroup: (turnId, body, org) => call('PUT', '/lab/turns/' + enc(turnId) + '/step-label-groups', { body, org }),
  clearGroup: (turnId, groupId, org) => call('DELETE', '/lab/turns/' + enc(turnId) + '/step-label-groups/' + enc(groupId),
    { org }),
  all: (query) => call('GET', '/lab/step-labels', { query }),
  csv: (query) => call('GET', '/lab/step-labels.csv', { query, raw: true, timeoutMs: 60000 }),

  // Runs (22 Oct 2026): R1, R2 ... and their questions R1-1, R1-2 ...
  runs: (query) => call('GET', '/lab/runs', { query }),
  run: (code) => call('GET', '/lab/runs/' + enc(code)),
  createRun: (body) => call('POST', '/lab/runs', { body }),
  syncRuns: () => call('POST', '/lab/runs/sync', { timeoutMs: 60000 }),
  importRuns: (file, fields) => {
    const form = new FormData();
    form.append('file', file);
    Object.keys(fields || {}).forEach((k) => { if (fields[k]) form.append(k, fields[k]); });
    return call('POST', '/lab/runs/import', { form, timeoutMs: 120000 });
  },
  runQuestions: (code) => call('GET', '/lab/runs/' + enc(code) + '/questions'),
  runLabels: (code) => call('GET', '/lab/runs/' + enc(code) + '/labels'),
  progress: (run) => call('GET', '/lab/progress', { query: run ? { run } : {} }),
  q: (code, turn) => call('GET', '/lab/q/' + enc(code), { query: turn ? { turn } : {} }),
  setExpected: (code, body) => call('PUT', '/lab/q/' + enc(code) + '/expected', { body }),
  linkTrace: (code, turnId) => call('POST', '/lab/q/' + enc(code) + '/traces', { body: { turn_id: turnId } }),
  answerCandidates: (code, q) => call('GET', '/lab/q/' + enc(code) + '/candidates', { query: q ? { q } : {} }),
  setCell: (code, mode, value) => call('PUT', '/lab/q/' + enc(code) + '/cells', { body: { mode, value } }),
  modes: () => call('GET', '/lab/modes'),
  addMode: (body) => call('POST', '/lab/modes', { body }),
  editMode: (lineage, body) => call('PUT', '/lab/modes/' + enc(lineage), { body }),
  retireMode: (lineage) => call('DELETE', '/lab/modes/' + enc(lineage)),
  platformMembers: () => call('GET', '/lab/platform-members'),
  decidePlatformMember: (body) => call('PUT', '/lab/platform-members', { body })
};
