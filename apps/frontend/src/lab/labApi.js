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
  // Synthetic batches: the instances a batch can be generated for, the batch itself, and asking
  // a few of them for real. Generation calls no model; the run calls one per question, which is
  // why it waits minutes rather than seconds.
  companies: () => call('GET', '/lab/companies'),
  spaces: () => call('GET', '/scenarios/spaces'),
  generate: (body) => call('POST', '/scenarios/generate', { body, timeoutMs: 30000 }),
  // org: asking reads one company's records, so the company travels with the request.
  runScenarios: (body, org) => call('POST', '/scenarios/run', { body, org, timeoutMs: 600000 }),
  // What reviewers have left, drafted. `run` narrows it to one run; without it the draft
  // covers every run this company has annotated.
  learningDraft: (run) => call('GET', '/learning/draft', { query: run ? { run } : {} }),
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
  decidePlatformMember: (body) => call('PUT', '/lab/platform-members', { body }),

  // The correction loop (3 Nov 2026): traces -> notes -> failure modes -> SPEC.md -> correction -> regression -> rerun.
  // What a coding agent proposes waits here for a person's decision; the specification in force is the newest approved
  // version; a regression run of a failure mode is asked again after a correction and compared with what was answered.
  syncStatus: () => call('GET', '/lab/sync-status'),
  proposals: () => call('GET', '/lab/proposals'),
  decideProposal: (kind, id, body) => call('POST', '/lab/proposals/' + enc(kind) + '/' + enc(id), { body }),
  spec: () => call('GET', '/lab/spec'),
  specDraft: () => call('GET', '/lab/spec/draft'),
  proposeSpec: (body) => call('POST', '/lab/spec', { body }),
  specFile: () => call('GET', '/lab/spec.md', { raw: true, timeoutMs: 60000 }),
  corrections: (mode) => call('GET', '/lab/corrections', { query: mode ? { mode } : {} }),
  addCorrection: (body) => call('POST', '/lab/corrections', { body }),
  makeRegression: (lineage, body) => call('POST', '/lab/modes/' + enc(lineage) + '/regression', { body, timeoutMs: 60000 }),
  rerun: (code, body) => call('POST', '/lab/runs/' + enc(code) + '/rerun', { body, timeoutMs: 600000 }),
  compare: (code) => call('GET', '/lab/runs/' + enc(code) + '/compare', { timeoutMs: 60000 }),
  loop: () => call('GET', '/lab/loop'),

  // The agent, inside the Trace Lab container (Bala, 5 Nov 2026). It only proposes: failure modes and labels from
  // people's notes, and changes to the instruction documents for a mode with an approved rule. A person approves a
  // change (it goes under test: only reruns here read it), makes it live (every agent reads it, no rebuild), and
  // downloads the live documents for the commit once testing is complete.
  // run (R40): only the notes of the run the person has open.
  proposeFromNotes: (run) => call('POST', '/lab/agent/propose-from-notes', { query: run ? { run } : {}, timeoutMs: 120000 }),
  draftFix: (lineage) => call('POST', '/lab/modes/' + enc(lineage) + '/draft-fix', { timeoutMs: 120000 }),
  // One click: a confirmed failure mode's rule goes into SPEC.md as a new approved version, under test (6 Nov 2026).
  addRule: (lineage) => call('POST', '/lab/modes/' + enc(lineage) + '/rule', { timeoutMs: 60000 }),
  changes: () => call('GET', '/lab/changes'),
  approveChange: (id) => call('POST', '/lab/changes/' + enc(id) + '/approve'),
  rejectChange: (id) => call('POST', '/lab/changes/' + enc(id) + '/reject'),
  liveChange: (id) => call('POST', '/lab/changes/' + enc(id) + '/live'),
  exportChanges: () => call('GET', '/lab/changes/export.zip', { raw: true, timeoutMs: 60000 })
};
