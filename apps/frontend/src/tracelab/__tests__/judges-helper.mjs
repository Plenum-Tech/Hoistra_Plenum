// A helper for the Judges tests (not a test file itself: node --test runs only *.test.mjs). Importing it sets the
// browser globals the module needs and a fetch that answers by "METHOD path" (404 for anything a test did not stub, so
// a stray request fails inside its own state instead of reaching a network). It hands back the store, the Judges state
// helpers, a way to put the store at a new seq with the Judges slice set as a test needs, and the replies the service
// sends (shaped like the judges routes), which the actions, view-model and screen tests share. The actions and the
// view models are imported by each test itself, so a test can fail on a missing module on its own.
globalThis.window = { location: { origin: 'http://test.local', pathname: '/', search: '' }, history: { replaceState: () => {} }, scrollTo: () => {} };
globalThis.document = { visibilityState: 'visible' };
export const net = { requests: [], handlers: {} };
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const o = opts || {};
  const method = o.method || 'GET';
  const body = typeof o.body === 'string' ? JSON.parse(o.body) : undefined;
  net.requests.push({ method, url: u, body });
  const h = net.handlers[method + ' ' + u.pathname];
  const out = typeof h === 'function' ? await h(u, body) : (h || [404, { detail: { reason: 'not_mocked', error: 'not mocked' } }]);
  const payload = typeof out[1] === 'string' ? out[1] : JSON.stringify(out[1]);
  return { ok: out[0] >= 200 && out[0] < 300, status: out[0], statusText: String(out[0]), text: async () => payload,
    blob: async () => new Blob([payload]), headers: { get: () => null } };
};

export const { judgesApi } = await import('../judgesApi.js');
export const C = await import('../logic/judgesCommon.js');
export const { taxonomyInitial } = await import('../logic/taxCommon.js');
export const { tlStore, initialState } = await import('../store.js');
export const { setActingOrg } = await import('../../api/client.js');
export const P = '/backend/trace-lab/api/trace-lab';

export const S = () => tlStore.get();
export const Jx = () => C.judgesOf(S());
export const route = (method, path) => method + ' ' + P + path;
// setImmediate is not touched by mocked timers, so this settles reads even in a test that mocks setTimeout.
export const flush = async () => { for (let i = 0; i < 8; i += 1) await new Promise((r) => setImmediate(r)); };
export const reqs = () => net.requests.map((r) => r.method + ' ' + r.url.pathname.slice(P.length));

// Who the person is comes from the failure-mode list (its `is_owner`), as it does for the other tabs.
export const META = (isOwner) => ({ agents: [], levels: [], fixTest: '', fixTestText: '', groupingNote: '', isOwner: !!isOwner });

// A fresh store at a new seq with the Judges slice set as the test needs. `owner` (default true): the failure-mode list
// has been read and says the person is the platform team; false: a company admin. `owner: null`: not read yet.
// `over` sets any store key.
export function jfresh(judges, opts) {
  const o = opts || {};
  const seq = S().seq + 1;
  const tax = o.owner === null ? {} : { tlModesMeta: META(o.owner !== false), tlModesLoadedAt: 1 };
  tlStore.set(Object.assign(initialState(), { seq, tab: 'judges' }, o.over || {}, {
    taxonomy: Object.assign(taxonomyInitial(seq), tax, o.tax || {}),
    judges: Object.assign(C.judgesInitial(seq), judges || {})
  }));
}

export const installDefaults = () => { setActingOrg(null); net.requests = []; net.handlers = {}; jfresh(); };

// ── replies shaped like the judges routes ──────────────────────────────────────────────────────────────────────────
export const SUMMARY_BRIEF = { id: 'j-1', mode_id: 'm-1', mode_name: 'invented_codes', version: 1, judge_key: 'invented_codes-v1', kind: 'llm',
  status: 'frozen', display_status: 'tested', decision: 'use', in_use: true, check_key: null, monitor_threshold: 0.15,
  dev: { tpr: 0.9487, tnr: 0.9091, tpr_interval: [0.8311, 0.9858], tnr_interval: [0.6226, 0.9838], n: 50, agreement: 0.94 },
  test: { tpr: 0.9474, tnr: 0.8333, tpr_interval: [0.8271, 0.9854], tnr_interval: [0.552, 0.953], n: 50, agreement: 0.92 },
  test_run: 'done', labels: { fail: 30, pass: 30 }, minimum: 30, min_labels_override: null, created_at: '2026-10-10T09:00:00+00:00' };
export const SUMMARY_FULL = Object.assign({}, SUMMARY_BRIEF, { model: 'claude-sonnet-5', prompt_hash: 'abc123def456', change_note: 'Added a borderline example.',
  stop_reason: 'Stable on dev twice.', frozen_at: '2026-10-12T09:00:00+00:00', frozen_by_email: 'team@plenum.example', decision_note: null,
  created_by_email: 'team@plenum.example', rev: 4, split_id: 's-1' });
export const DRAFT = Object.assign({}, SUMMARY_FULL, { id: 'j-2', version: 2, judge_key: 'invented_codes-v2', status: 'draft', display_status: 'dev',
  decision: null, in_use: false, monitor_threshold: null, test: null, test_run: 'none', stop_reason: null, frozen_at: null, frozen_by_email: null,
  rev: 3, change_note: 'Sharper Pass rule.', prompt_text: 'You are evaluating one answer from Hoist.', check_version: null,
  prompt_parts: { task: '', fail_rule: 'The answer names a code that no step returned.', pass_rule: 'Every code in the answer was returned by a step.',
    edge_cases: 'A code the asker typed counts as returned.',
    examples: [
      { turn_id: 'tr-f', role: 'clear_fail', critique: 'Names AHU-09, which no step returned.', verdict: 'Fail', excerpt: 'QUESTION\nWhich AHUs are overdue?\n\nANSWER THE USER SAW\nAHU-09 is overdue.' },
      { turn_id: 'tr-p', role: 'clear_pass', critique: 'Every code came from the query.', verdict: 'Pass', excerpt: 'QUESTION\nWhich pumps failed?\n\nANSWER THE USER SAW\nP-2 failed.' },
      { turn_id: 'tr-b', role: 'borderline', critique: 'The code is only implied.', verdict: 'Fail', excerpt: 'QUESTION\nWhat about the chiller?\n\nANSWER THE USER SAW\nThe usual one is fine.' }] } });
export const RUN_DEV = { id: 'r-dev', judge_id: 'j-2', split: 'dev', status: 'done', n: 24, n_error: 0, n_undecidable: 0, n_truncated: 1, tp: 12, fn: 0, tn: 11, fp: 1,
  tpr: 1, tnr: 0.9167, tpr_interval: [0.7575, 1], tnr_interval: [0.6461, 0.9851], agreement: 0.9583, label_flips: 0, usd: 0.29, usd_cap: 5, rev: 2,
  started_at: '2026-10-15T11:00:00+00:00', finished_at: '2026-10-15T11:02:00+00:00', error: null, items: 24, results: [],
  disagreements: [{ turn_id: 'turn-9', label: 1, failure_present: 0, kind: 'missed_failure', triage: null, note: null, by: null, by_email: null,
    at: null, history: [], excerpt: 'QUESTION\nWhich AHUs are overdue?\n\nANSWER THE USER SAW\nAHU-11 is overdue.' }],
  job: { status: 'done', progress: { done: 24, total: 24, errors: 0, usd: 0.29 }, usd_spent: 0.29, usd_cap: 5 } };
export const RUN_ROW_DEV = { id: 'r-dev', judge_id: 'j-2', split: 'dev', status: 'done', n: 24, tpr: 1, tnr: 0.9167, rev: 2,
  started_at: '2026-10-15T11:00:00+00:00', disagreement_count: 1, untriaged: 1, tpr_interval: [0.7575, 1], tnr_interval: [0.6461, 0.9851] };
export const ITERATIONS = [
  { version: 1, judge_key: 'invented_codes-v1', prompt_hash: 'abc123def456', change_note: 'Added a borderline example.', dev_runs: 2,
    dev_tpr: 0.9487, dev_tnr: 0.9091, dev_tpr_interval: [0.8311, 0.9858], dev_tnr_interval: [0.6226, 0.9838], n_dev: 50, disagreements: 3,
    untriaged: 0, label_flips: 0, input_changed: 0, frozen: true,
    test: { tpr: 0.9474, tnr: 0.8333, tpr_interval: [0.8271, 0.9854], tnr_interval: [0.552, 0.953] } },
  { version: 2, judge_key: 'invented_codes-v2', prompt_hash: 'fed654cba321', change_note: 'Sharper Pass rule.', dev_runs: 1, dev_tpr: 1,
    dev_tnr: 0.9167, dev_tpr_interval: [0.7575, 1], dev_tnr_interval: [0.6461, 0.9851], n_dev: 24, disagreements: 1, untriaged: 1,
    label_flips: 1, input_changed: 0, frozen: false, test: null }];
export const DETAIL = { ok: true, judge: DRAFT, runs: [RUN_ROW_DEV], iterations: ITERATIONS, warnings: [],
  test_companies: [{ organization_id: 'org-1', fail: 12, pass: 12 }], preview_turn_ids: ['td-1', 'td-2', 'td-3', 'td-4', 'td-5'] };
export const DETAIL_TRIAGED = Object.assign({}, DETAIL, { runs: [Object.assign({}, RUN_ROW_DEV, { untriaged: 0 })] });
export const FROZEN = Object.assign({}, DRAFT, { status: 'frozen', display_status: 'frozen', rev: 4, stop_reason: 'Dev agreement is high; no third revision.',
  frozen_at: '2026-10-15T11:30:00+00:00', frozen_by_email: 'team@plenum.example' });
export const DETAIL_FROZEN = Object.assign({}, DETAIL_TRIAGED, { judge: FROZEN });
export const RUN_TEST = { id: 'r-test', judge_id: 'j-2', split: 'test', status: 'done', n: 24, n_error: 0, n_undecidable: 0, n_truncated: 0, tp: 11, fn: 1, tn: 10, fp: 2,
  tpr: 0.9167, tnr: 0.8333, tpr_interval: [0.6461, 0.9851], tnr_interval: [0.552, 0.953], agreement: 0.875, label_flips: null, usd: 0.3, usd_cap: 5, rev: 1,
  started_at: '2026-10-15T11:40:00+00:00', finished_at: '2026-10-15T11:42:00+00:00', error: null, items: 24, results: [],
  disagreements: [{ turn_id: 'turn-t1', label: 0, failure_present: 1, kind: 'false_alarm', triage: null, note: null, by: null, by_email: null,
    at: null, history: [], excerpt: 'QUESTION\nWhich lifts are out?\n\nANSWER THE USER SAW\nLift 2 is out.' }],
  job: { status: 'done', progress: { done: 24, total: 24, errors: 0, usd: 0.3 }, usd_spent: 0.3, usd_cap: 5 } };
export const DETAIL_TESTED = Object.assign({}, DETAIL_FROZEN, { judge: Object.assign({}, FROZEN, { display_status: 'tested', test_run: 'done' }),
  runs: [{ id: 'r-test', judge_id: 'j-2', split: 'test', status: 'done', n: 24, rev: 1, started_at: '2026-10-15T11:40:00+00:00', untriaged: 1 },
    Object.assign({}, RUN_ROW_DEV, { untriaged: 0 })] });
export const SPLIT = { id: 's-1', mode_id: 'm-1', version: 1, status: 'active', badge: null,
  counts: { train: { fail: 6, pass: 6 }, dev: { fail: 12, pass: 12 }, test: { fail: 12, pass: 12 } },
  members: [
    { turn_id: 'tr-f', split: 'train', label_present: true, excerpt: 'QUESTION\nWhich AHUs are overdue?\n\nANSWER THE USER SAW\nAHU-09 is overdue.' },
    { turn_id: 'tr-p', split: 'train', label_present: false, excerpt: 'QUESTION\nWhich pumps failed?\n\nANSWER THE USER SAW\nP-2 failed.' },
    { turn_id: 'tr-b', split: 'train', label_present: true, excerpt: 'QUESTION\nWhat about the chiller?\n\nANSWER THE USER SAW\nThe usual one is fine.' },
    { turn_id: 'td-1', split: 'dev', label_present: false, excerpt: 'QUESTION\nWhich boilers passed?\n\nANSWER THE USER SAW\nB-1 passed.' }] };
export const READY = { ok: true, mode: { id: 'm-1', name: 'invented_codes', status: 'confirmed', level: 'technical',
  definition: 'Present when the answer names a code no step returned.', boundary: '' },
  labels: { fail: 31, pass: 40, groups_fail: 31, groups_pass: 40, excluded: 2, train_only: 3 }, minimum: { per_class: 30, eval_per_class: 10 },
  split: { id: 's-1', mode_id: 'm-1', version: 1, status: 'active', counts: SPLIT.counts, badge: null },
  judges: [SUMMARY_FULL, DRAFT], next_step: 'triage' };
export const READY_THIN = Object.assign({}, READY, { labels: { fail: 29, pass: 40, groups_fail: 29, groups_pass: 40, excluded: 0, train_only: 0 },
  split: null, judges: [], next_step: 'label_more' });
export const CHECKS = [
  { key: 'no_answer', version: 1, evaluator_key: 'code:no_answer@v1', label: 'Gives no answer', description: 'Fails when the answer is empty.', scope: 'live' },
  { key: 'expected_values', version: 1, evaluator_key: 'code:expected_values@v1', label: 'Misses an expected figure',
    description: 'Fails when a golden figure is missing from the answer.', scope: 'case' }];
export const REPORT = { ok: true, generated_at: '2026-10-15T12:00:00+00:00', modes: [{ name: 'invented_codes',
  definition: 'Present when the answer names a code no step returned.', boundary: '', requirement_ids: ['REQ-1'],
  evaluator: { type: 'llm', judge_key: 'invented_codes-v1', test_tpr: 0.9474, test_tnr: 0.8333, test_tpr_interval: [0.8271, 0.9854],
    test_tnr_interval: [0.552, 0.953], decision: 'use' },
  evaluator_reason: null, labeled_counts: { fail: 12, pass: 38 }, sample_fraction: 0.24, prevalence: null,
  prevalence_reason: 'Monitoring has not run for this mode yet.', examples: ['turn-a'], evaluation_case_candidates: ['21', '7'] }] };
