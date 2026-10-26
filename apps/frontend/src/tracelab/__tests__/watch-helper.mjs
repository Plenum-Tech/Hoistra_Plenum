// A helper for the Watch tests (not a test file itself: node --test runs only *.test.mjs). Importing it sets the browser
// globals the module needs and a fetch that answers by "METHOD path" (404 for anything a test did not stub, so a stray
// request fails inside its own state instead of reaching a network). It hands back the store, the Watch state helpers, a way
// to put the store at a new seq with the Watch slice set as a test needs, and the replies the service sends (shaped like
// the Watch routes), which the actions, view-model and screen tests share. The actions and the view models are imported by
// each test itself, so a test can fail on a missing module on its own.
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
  const headers = out[2] || {};
  return { ok: out[0] >= 200 && out[0] < 300, status: out[0], statusText: String(out[0]), text: async () => payload,
    blob: async () => new Blob([payload]), headers: { get: (k) => headers[String(k).toLowerCase()] || null } };
};

export const { watchApi } = await import('../watchApi.js');
export const C = await import('../logic/watchCommon.js');
export const { taxonomyInitial } = await import('../logic/taxCommon.js');
export const { tlStore, initialState } = await import('../store.js');
export const { setActingOrg } = await import('../../api/client.js');
export const P = '/backend/trace-lab/api/trace-lab';

export const S = () => tlStore.get();
export const Wx = () => C.watchOf(S());
export const route = (method, path) => method + ' ' + P + path;
// setImmediate is not touched by mocked timers, so this settles reads even in a test that mocks setTimeout.
export const flush = async () => { for (let i = 0; i < 8; i += 1) await new Promise((r) => setImmediate(r)); };
export const reqs = () => net.requests.map((r) => r.method + ' ' + r.url.pathname.slice(P.length));

// Who the person is comes from the failure-mode list (its `is_owner`), as it does for the other tabs.
export const META = (isOwner) => ({ agents: [], levels: [], fixTest: '', fixTestText: '', groupingNote: '', isOwner: !!isOwner });

// A fresh store at a new seq with the Watch slice set as the test needs. `owner` (default true): the failure-mode list has
// been read and says the person is the platform team; false: a company admin. `owner: null`: not read yet. `over` sets any
// store key.
export function wfresh(watch, opts) {
  const o = opts || {};
  const seq = S().seq + 1;
  const tax = o.owner === null ? {} : { tlModesMeta: META(o.owner !== false), tlModesLoadedAt: 1 };
  tlStore.set(Object.assign(initialState(), { seq, tab: 'watch' }, o.over || {}, {
    taxonomy: Object.assign(taxonomyInitial(seq), tax, o.tax || {}),
    watch: Object.assign(C.watchInitial(seq), watch || {})
  }));
}

export const installDefaults = () => { setActingOrg(null); net.requests = []; net.handlers = {}; wfresh(); };

// ── replies shaped like the Watch routes ───────────────────────────────────────────────────────────────────────────
export const DUE = { period_label: '2026-W41', is_due: true };
export const BATCH_ROW = { id: 'b1', period_label: '2026-W41', status: 'done', scope: 'platform', usd: 1.234, crossed: true,
  results: { n_window: 412, n_random: 100, modes: [
    { mode_id: 'm1', mode_name: 'wrong_period', crossed: true, flagged: 3, corrected: 0.17, ci_low: 0.06, ci_high: 0.28, raw: 0.19, n_sample: 100, threshold: 0.15 },
    { mode_id: 'm2', mode_name: 'invented_codes', crossed: false, flagged: 0, corrected: 0.02, ci_low: 0, ci_high: 0.06, raw: 0.03, n_sample: 100, threshold: 0.15 }],
    code_checks: [{ label: 'Gives no answer', rate: 0.0218, flagged: 9, decided: 412, validated: true }] } };
export const BATCH_INTERRUPTED = { id: 'b0', period_label: '2026-W40', status: 'interrupted', scope: 'company', usd: 0.4, crossed: false, results: {} };
export const BATCHES = { ok: true, batches: [BATCH_ROW, BATCH_INTERRUPTED], due: DUE };
export const BATCHES_EMPTY = { ok: true, batches: [], due: DUE };
export const BATCH_DETAIL = { ok: true, batch: BATCH_ROW, flagged: [
  { turn_id: 't1', question: 'How many work orders in July?', mode_name: 'wrong_period', group: 'uniform sample', critique: 'Used June.' },
  { turn_id: 't2', question: 'List the overdue AHUs', mode_name: 'wrong_period', group: 'risk group', critique: '' }] };
export const CHART_M1 = { ok: true, mode: { id: 'm1', name: 'wrong_period' }, threshold: 0.15, y_max: 0.35, points: [
  { period_label: '2026-W40', raw: 0.12, corrected: 0.1, ci_low: 0.02, ci_high: 0.2, threshold: 0.15, crossed: false },
  { period_label: '2026-W41', raw: 0.19, corrected: 0.17, ci_low: 0.06, ci_high: 0.28, threshold: 0.15, crossed: true }] };
export const CHART_M2 = Object.assign({}, CHART_M1, { mode: { id: 'm2', name: 'invented_codes' }, points: [CHART_M1.points[0]] });
export const CHECKS = { ok: true, checks: [
  { evaluator_key: 'code:no_answer@v1', label: 'Gives no answer', validated: true, weeks: [
    { period_label: '2026-W40', n: 400, flagged: 8, rate: 0.02 }, { period_label: '2026-W41', n: 412, flagged: 9, rate: 0.0218 }] },
  { evaluator_key: 'code:expected_values@v1', label: 'Misses an expected figure', validated: false, weeks: [{ period_label: '2026-W41', n: 0, flagged: 0, rate: null }] }] };
export const AUTO = { ok: true, definitions: { auto_by_source: 'Automatic: golden-case checks and judges that passed their held-out test.' }, weeks: [
  { week: '2026-W41', checked_any: 5, auto_share: 0.4, people_share: 0.6, auto_by_source: { golden_auto: 2 }, people_by_source: { reviewer: 3 },
    modes_automated: 1, modes_confirmed: 3, golden_accuracy: { decided: 2, correct: 1, rate: 0.5, low: 0.1, high: 0.9 }, live_accuracy: { reviewed: 0 } },
  { week: '2026-W42', checked_any: 0, auto_share: null, people_share: null }] };
export const PLAN = { ok: true, period_label: '2026-W41', n_window: 412, n_random: 100, n_risk_only: 20, n_to_judge: 120, usd_estimate: 1.5,
  judges: [{ mode_name: 'wrong_period' }, { judge_key: 'invented_codes-v1' }], warnings: ['Two modes have no judge yet.'] };
export const CASE_PROPOSED = { id: 'c1', case_key: 'e-7f3a19c2', version: 1, status: 'proposed', kind: 'unclassified', rev: 2, golden_case_id: null,
  mode_name: 'wrong_period', input: { question: 'How many work orders in July?' }, latest_run: null };
export const CASE_DRAFT = { id: 'c2', case_key: 'e-aa11bb22', version: 1, status: 'draft', kind: 'unclassified', rev: 3, golden_case_id: 'g-2',
  mode_name: 'invented_codes', input: { question: 'Which AHUs are overdue?' }, latest_run: null };
export const CASE_ACTIVE = { id: 'c3', case_key: 'e-cc33dd44', version: 2, status: 'active', kind: 'regression', rev: 5, golden_case_id: 'g-3',
  mode_name: 'wrong_period', input: { question: 'How many PPMs were done in May?' }, baseline_pass_rate: 0.8,
  latest_run: { pass_at_1: 1, pass_at_3: 1, pass_at_5: 0.9, pass_hat_k: 0.6 } };
export const CASE_CAPABILITY = Object.assign({}, CASE_ACTIVE, { id: 'c4', case_key: 'e-ee55ff66', kind: 'capability', baseline_pass_rate: 0.4 });
export const CASES = { ok: true, cases: [CASE_PROPOSED, CASE_DRAFT, CASE_ACTIVE] };
export const RELEASES = { ok: true, checks: [
  { golden_run_id: '11111111-1111-4111-8111-111111111111', run_key: 'release-1', decision: 'pass', reason: '', k: 5, started_at: '2026-10-14T10:00:00+00:00' },
  { golden_run_id: '22222222-2222-4222-8222-222222222222', run_key: 'release-2', decision: 'block', reason: 'Two regression cases failed a run.', k: 5, started_at: '2026-10-15T10:00:00+00:00' },
  { golden_run_id: '33333333-3333-4333-8333-333333333333', run_key: 'release-3', decision: 'incomplete', reason: 'Still grading.', k: 3, started_at: null }] };
export const RUN_ID = '44444444-4444-4444-8444-444444444444';
export const GOLDEN_RUN = { id: RUN_ID, status: 'running', label: 'Baseline', k: 5, cases: 1, mine: true };
