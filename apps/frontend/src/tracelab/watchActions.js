// watchActions — everything the Watch tab does: read the monitoring batches (with their Due marker), the code-check trend,
// the automatic-vs-people trend and one corrected-failure-rate chart per failure mode (everyone's); for a company reader the
// eval cases and release checks; for the platform team (any superadmin) the plan, starting and resuming a monitoring batch and
// opening the flagged answers as a review batch, for the company being viewed or, viewing none, for every company (watchApi.js
// names the company the person views and no other); and the writes: confirm, baseline, repeat and retire an eval case, start
// a release check and download its report. Every request goes through watchApi.js. State lives in the store's `watch` slice
// (logic/watchCommon.js), tagged with the store's seq: a company switch (resetForCompany bumps the seq) leaves the old slice
// unread, a reply issued before the switch is dropped and writes nothing, a reply overtaken by a newer read of the same
// thing is dropped, and the app's stale-company error stays quiet. Nothing company-only (cases, release checks) or
// platform-only (plan, start, resume, review batch) is asked for before the service has said who the person is, and no
// write is sent by anyone it is not for (a company admin starts nothing): the service refuses them anyway, and nothing here
// asks it to.
//
// Every write goes through save(), so a failure stays on screen: an inline error with Retry for a network failure; the
// server's own sentence, with no Retry, for a rule it refused (over the cost cap, a case already baselined); and, for a stale
// base, "Changed in another tab" with a reload offered. Retry runs the whole press again (the action behind each button), so
// what the press does after a success (close the plan, read the lists again, open the run) happens on a retry too.
//
// A baseline, a repeat or a release check starts a golden run. The run is stepped from the browser (design D9), and the page
// that started it steps it: the run opens on Runs claimed by this page, exactly as a run started from the Golden set tab is.
// The pure helpers are in logic/watchChart.js and the view models in logic/watch.js; the buttons the view models carry call
// the actions below by name (the screen passes this module as `a`).
import { tlStore } from './store.js';
import { isStaleScope } from './api.js';
import { watchApi } from './watchApi.js';
import { setTab } from './actions.js';
import { openRun as openGoldenRun } from './goldenActions.js';
import { modesEnsure, openTurnById } from './taxonomyActions.js';
import { canStart, inCompany, watchOf, watchWrite } from './logic/watchCommon.js';
import { LIVE, latestModes } from './logic/watchChart.js';
import { UUID_RE } from './logic/golden.js';
import { saveFile } from './logic/judgesText.js';

const get = () => tlStore.get();
const W = () => watchOf(get());
const setW = (patch) => tlStore.set((s) => {
  const w = watchOf(s);
  return { watch: Object.assign({}, w, typeof patch === 'function' ? patch(w) : patch) };
});
const live = (seq) => seq === get().seq;
const msg = (e) => (e && e.message) || String(e);
const no = () => Promise.resolve({ ok: false });
const asks = { load: 0, auto: 0, batch: 0, plan: 0, cases: 0, releases: 0 };
const chartAsks = {};

export const RATE_TEXT = 'Sample rate is a number above 0 and up to 1, for example 0.25.';
export const CAP_TEXT = 'Cost cap is a number of dollars above 0 and up to 100.';
export const ANSWER_TEXT = 'Write what a right answer says before confirming.';
export const REPEAT_TEXT = 'Repeat an answer between 2 and 20 times.';
export const RETIRE_TEXT = 'Say why the case is retired.';
export const RELEASE_TEXT = 'A release check runs each case 1 to 10 times.';
const REASON_MAX = 500;
const ANSWER_MAX = 6000;

// What was typed into a number box: { value: null } when it is empty, { bad: true } when it is not a number in range.
function typed(raw, low, high, openLow) {
  const t = String(raw === undefined || raw === null ? '' : raw).trim();
  if (!t) return { value: null, bad: false };
  const n = Number(t);
  const inRange = isFinite(n) && (openLow ? n > low : n >= low) && n <= high;
  return inRange ? { value: n, bad: false } : { value: null, bad: true };
}

// ── reading ──────────────────────────────────────────────────────────────────────────────────────────────────────
// Opening the tab: who the person is arrives with the failure-mode list (read once, shared with the other tabs), and the
// batches, code checks and trend are everyone's. A batch that was open is read again; the company reader's cases and release
// checks are read by loadCompany() once the service has said who the person is.
export function openTab() {
  modesEnsure();
  const id = W().tlMonitorBatchId;
  if (id) openBatch(id);
  return load();
}

// "Refresh": everything the person can see, read again. Who the person is is read again too when its read failed (it is the
// only way the page learns it, and nothing owner-only or company-only is drawn until it arrives): modesEnsure() asks only
// when the list is neither read nor being read, so a good read is never repeated. Once it arrives, the screen reads a company
// reader's cases and release checks (its effect is keyed on who the person is).
export function reload() {
  modesEnsure();
  const id = W().tlMonitorBatchId;
  if (id) openBatch(id);
  return Promise.all([load(), loadCompany()]);
}

// The batches and the code-check trend (each says on its own if it failed), the trend of who checked what, and then one chart
// for each failure mode the batches estimated.
export async function load() {
  const seq = get().seq;
  const ask = (asks.load += 1);
  setW({ tlMonitorLoading: true, tlMonitorErr: '', tlMonitorChecksErr: '' });
  loadAuto();
  const [list, checks] = await Promise.allSettled([watchApi.monitorBatches({ limit: 20 }), watchApi.monitorCodeChecks(12)]);
  if (!live(seq) || ask !== asks.load) return;
  if ((list.status === 'rejected' && isStaleScope(list.reason)) || (checks.status === 'rejected' && isStaleScope(checks.reason))) return;
  const patch = { tlMonitorLoading: false };
  if (list.status === 'fulfilled') {
    patch.tlMonitorBatches = list.value || null;
    patch.tlMonitorLoadedAt = Date.now();
  } else patch.tlMonitorErr = msg(list.reason);
  if (checks.status === 'fulfilled') patch.tlMonitorChecks = (checks.value && checks.value.checks) || [];
  else patch.tlMonitorChecksErr = msg(checks.reason);
  setW(patch);
  if (list.status === 'fulfilled') latestModes((list.value && list.value.batches) || []).forEach((m) => loadChart(m.modeId));
}

export async function loadAuto() {
  const seq = get().seq;
  const ask = (asks.auto += 1);
  setW({ tlAutoLoading: true, tlAutoErr: '' });
  try {
    const out = await watchApi.automationTrend(12);
    if (!live(seq) || ask !== asks.auto) return;
    setW({ tlAutoLoading: false, tlAuto: out || null, tlAutoLoadedAt: Date.now() });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.auto) return;
    setW({ tlAutoLoading: false, tlAutoErr: msg(e) });
  }
}

export async function loadChart(modeId) {
  if (!modeId) return;
  const seq = get().seq;
  const ask = (chartAsks[modeId] = (chartAsks[modeId] || 0) + 1);
  const put = (patch) => setW((w) => ({ tlMonitorCharts: Object.assign({}, w.tlMonitorCharts, { [modeId]: Object.assign({}, (w.tlMonitorCharts || {})[modeId], patch) }) }));
  put({ loading: true, error: '' });
  try {
    const chart = await watchApi.monitorChart(modeId, 12);
    if (!live(seq) || ask !== chartAsks[modeId]) return;
    put({ loading: false, error: '', chart });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== chartAsks[modeId]) return;
    put({ loading: false, error: msg(e) });
  }
}

// A company reader's eval cases and release checks. The platform team, who names no company, reads neither (each read
// refuses to go out for anyone else).
export function loadCompany() {
  return Promise.all([loadCases(), loadReleases()]);
}

export async function loadCases() {
  if (!inCompany(get())) return;
  const seq = get().seq;
  const ask = (asks.cases += 1);
  setW({ tlCasesLoading: true, tlCasesErr: '' });
  try {
    const out = await watchApi.evalCases({});
    if (!live(seq) || ask !== asks.cases) return;
    setW({ tlCasesLoading: false, tlCases: (out && out.cases) || [], tlCasesLoadedAt: Date.now() });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.cases) return;
    setW({ tlCasesLoading: false, tlCasesErr: msg(e) });
  }
}

export async function loadReleases() {
  if (!inCompany(get())) return;
  const seq = get().seq;
  const ask = (asks.releases += 1);
  setW({ tlReleaseLoading: true, tlReleaseErr: '' });
  try {
    const out = await watchApi.releaseChecks({ limit: 20 });
    if (!live(seq) || ask !== asks.releases) return;
    setW({ tlReleaseLoading: false, tlRelease: out || null, tlReleaseLoadedAt: Date.now() });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.releases) return;
    setW({ tlReleaseLoading: false, tlReleaseErr: msg(e) });
  }
}

// ── the plan and the start (the platform team's: a superadmin, for the company viewed or for every company) ─────────────────────────────────────────────────────────────────
export async function openPlan() {
  if (!canStart(get())) return;
  const rate = typed((W().tlMonitorPlanDraft || {}).rate, 0, 1, true);
  if (rate.bad) { setW({ tlMonitorPlanOpen: true, tlMonitorPlanLoading: false, tlMonitorPlanErr: RATE_TEXT }); return; }
  const seq = get().seq;
  const ask = (asks.plan += 1);
  setW({ tlMonitorPlanOpen: true, tlMonitorPlanLoading: true, tlMonitorPlanErr: '' });
  try {
    const out = await watchApi.monitorPlan(rate.value === null ? {} : { rate: rate.value });
    if (!live(seq) || ask !== asks.plan) return;
    setW({ tlMonitorPlanLoading: false, tlMonitorPlan: out || null });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.plan) return;
    setW({ tlMonitorPlanLoading: false, tlMonitorPlanErr: msg(e) });
  }
}

export function setPlanDraft(field, value) {
  setW((w) => ({ tlMonitorPlanDraft: Object.assign({}, w.tlMonitorPlanDraft, { [field]: String(value === undefined || value === null ? '' : value).slice(0, 12) }) }));
}

export function closePlan() { setW({ tlMonitorPlanOpen: false }); }

// Start: a batch for the typed sample rate and cost cap (empty: the service's defaults). A rate or cap that is not a number
// in range is said here and not sent.
export function start() {
  if (!canStart(get())) return no();
  const d = W().tlMonitorPlanDraft || {};
  const rate = typed(d.rate, 0, 1, true);
  const cap = typed(d.cap, 0, 100, true);
  if (rate.bad || cap.bad) { setW({ tlMonitorPlanErr: rate.bad ? RATE_TEXT : CAP_TEXT }); return no(); }
  const body = {};
  if (rate.value !== null) body.sample_rate = rate.value;
  if (cap.value !== null) body.usd_cap = cap.value;
  setW({ tlMonitorPlanErr: '' });
  return act(watchWrite.start, () => watchApi.startMonitorBatch(body), () => {
    setW({ tlMonitorPlanOpen: false });
    load();
  });
}

// An interrupted batch would block its scope for good; resuming it carries on from where it stopped. The service resumes a
// batch only from the scope it was started in (a company's by a superadmin viewing it, a platform-wide one by a superadmin
// viewing none), so the screen offers the button only where it works.
export function resume(batchId) {
  if (!canStart(get()) || !batchId) return no();
  return act(watchWrite.resume(batchId), () => watchApi.resumeMonitorBatch(batchId), () => { load(); });
}

// ── one batch ────────────────────────────────────────────────────────────────────────────────────────────────────
// The same batch read again keeps what is on screen while it reads; another batch starts from nothing.
export async function openBatch(batchId) {
  if (!batchId) return;
  const seq = get().seq;
  const ask = (asks.batch += 1);
  const same = W().tlMonitorBatchId === batchId && !!W().tlMonitorBatch;
  setW(Object.assign({ tlMonitorBatchId: batchId, tlMonitorBatchLoading: true, tlMonitorBatchErr: '' }, same ? {} : { tlMonitorBatch: null }));
  try {
    const out = await watchApi.monitorBatch(batchId);
    if (!live(seq) || ask !== asks.batch || W().tlMonitorBatchId !== batchId) return;
    setW({ tlMonitorBatchLoading: false, tlMonitorBatch: out || null });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.batch || W().tlMonitorBatchId !== batchId) return;
    setW({ tlMonitorBatchLoading: false, tlMonitorBatchErr: msg(e) });
  }
}

export function closeBatch() { setW({ tlMonitorBatchId: null, tlMonitorBatch: null, tlMonitorBatchLoading: false, tlMonitorBatchErr: '' }); }

// The answers a batch flagged for one failure mode, put in front of reviewers as review batches.
export function openReviewBatch(batchId, modeId) {
  if (!canStart(get()) || !batchId || !modeId) return no();
  return act(watchWrite.review(batchId, modeId), () => watchApi.openReviewBatch(batchId, modeId), (out) => {
    setW((w) => ({ tlMonitorReviewIds: Object.assign({}, w.tlMonitorReviewIds, { [batchId + ':' + modeId]: (out && out.review_batch_ids) || [] }) }));
  });
}

// A flagged answer opens in its conversation, for someone reading inside one company; the platform team names no company, so
// the conversation cannot be read for it.
export function openAnswer(turnId) {
  if (!turnId || !inCompany(get())) return Promise.resolve(false);
  return openTurnById(turnId);
}

// A golden run, on Runs. `claim`: this page started it, so this page steps it.
export function openRun(runId, opts) {
  if (!runId || !UUID_RE.test(String(runId))) return Promise.resolve(null);
  if (opts && opts.claim) tlStore.set((s) => ({ golden: Object.assign({}, s.golden, { own: Object.assign({}, s.golden.own, { [runId]: true }) }) }));
  setTab('runs');
  return openGoldenRun(runId);
}

// ── eval cases (a company's) ─────────────────────────────────────────────────────────────────────────────────────
const findCase = (id) => (W().tlCases || []).find((k) => k.id === id) || null;
const draftOf = (id) => (W().tlCasesDrafts || {})[id] || {};
const runOf = (out) => (out && out.golden_run && out.golden_run.id) || null;
const startedRun = (out) => { const id = runOf(out); if (id) openRun(id, { claim: true }); };

export function setCaseDraft(caseId, field, value) {
  const v = typeof value === 'string' ? value.slice(0, ANSWER_MAX) : value;
  setW((w) => ({ tlCasesDrafts: Object.assign({}, w.tlCasesDrafts, { [caseId]: Object.assign({}, (w.tlCasesDrafts || {})[caseId], { [field]: v }) }) }));
}

// A proposed case is confirmed as a real failure. Without a golden twin it needs the written expected answer first.
export function confirmCase(caseId) {
  const k = findCase(caseId);
  if (!inCompany(get()) || !k || k.status !== 'proposed') return no();
  const d = draftOf(caseId);
  const answer = String(d.expected_answer || '').trim();
  if (!k.golden_case_id && !answer) { setW({ tlCasesErr: ANSWER_TEXT }); return no(); }
  setW({ tlCasesErr: '' });
  const body = { base: k.rev, expected_answer: answer || null, evaluation: d.evaluation || 'objective' };
  return act(watchWrite.confirm(caseId), () => watchApi.confirmEvalCase(caseId, body), () => { loadCases(); });
}

// The baseline: five runs of a draft case decide whether it is a regression case or a capability case.
export function baseline(caseId) {
  const k = findCase(caseId);
  if (!inCompany(get()) || !k || k.status !== 'draft') return no();
  setW({ tlCasesErr: '' });
  return act(watchWrite.baseline(caseId), () => watchApi.baselineEvalCase(caseId, {}), startedRun);
}

export function repeat(caseId) {
  const k = findCase(caseId);
  if (!inCompany(get()) || !k || !LIVE.includes(k.status) || !k.golden_case_id) return no();
  const d = draftOf(caseId);
  const n = Math.round(Number(d.n === undefined ? 10 : d.n));
  if (!(n >= 2 && n <= 20)) { setW({ tlCasesErr: REPEAT_TEXT }); return no(); }
  setW({ tlCasesErr: '' });
  return act(watchWrite.repeat(caseId), () => watchApi.repeatEvalCase(caseId, { n }), startedRun);
}

export function retire(caseId) {
  const k = findCase(caseId);
  if (!inCompany(get()) || !k || !LIVE.includes(k.status)) return no();
  const reason = String(draftOf(caseId).reason || '').trim().slice(0, REASON_MAX);
  if (reason.length < 3) { setW({ tlCasesErr: RETIRE_TEXT }); return no(); }
  setW({ tlCasesErr: '' });
  return act(watchWrite.retire(caseId), () => watchApi.retireEvalCase(caseId, { base: k.rev, reason }), () => { loadCases(); });
}

// ── release checks (a company's) ─────────────────────────────────────────────────────────────────────────────────
export function setReleaseDraft(field, value) {
  setW((w) => ({ tlReleaseDraft: Object.assign({ k: 5, include_capability: true }, w.tlReleaseDraft, { [field]: typeof value === 'string' ? value.slice(0, 4) : value }) }));
}

export function startRelease() {
  if (!inCompany(get())) return no();
  const d = Object.assign({ k: 5, include_capability: true }, W().tlReleaseDraft);
  const k = Math.round(Number(d.k));
  if (!(k >= 1 && k <= 10)) { setW({ tlReleaseErr: RELEASE_TEXT }); return no(); }
  setW({ tlReleaseErr: '' });
  const body = { k, include_capability: d.include_capability !== false };
  return act(watchWrite.release, () => watchApi.startReleaseCheck(body), startedRun);
}

// The report as a file to attach to a deploy, under the company's name in the Markdown heading. Returns whether a file was
// offered to the browser.
export async function downloadRelease(runId, format, company) {
  if (!runId || !inCompany(get())) return false;
  const seq = get().seq;
  setW({ tlReleaseErr: '' });
  try {
    const file = await watchApi.releaseCheckFile(runId, format, company);
    if (!live(seq)) return false;
    return saveFile(file, 'release-check-' + runId + (format === 'md' ? '.md' : '.json'));
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return false;
    setW({ tlReleaseErr: msg(e) });
    return false;
  }
}

// ── writes ───────────────────────────────────────────────────────────────────────────────────────────────────────
const saves = {};
// A reply that says another tab changed the thing: the base the write sent is behind.
const staleReason = (e) => !e.reason || e.reason === 'stale_base' || e.reason === 'stale';

// Runs one write. Resolves to {ok: true, out}, {ok: false, stale: true} (the company changed meanwhile: nothing is shown) or
// {ok: false, error, conflict, reason?}; never throws. The state of the write is kept under tlWatchWrites[key] —
// { busy, error, conflict, canRetry } — until it succeeds. `again` is the press that made it.
export async function save(key, fn, again) {
  saves[key] = { fn, again };
  const seq = get().seq;
  const put = (w) => setW((p) => ({ tlWatchWrites: Object.assign({}, p.tlWatchWrites, { [key]: w }) }));
  const drop = () => setW((p) => { const w = Object.assign({}, p.tlWatchWrites); delete w[key]; return { tlWatchWrites: w }; });
  put({ busy: true, error: '', conflict: false, canRetry: false });
  try {
    const out = await Promise.resolve().then(fn);
    if (!live(seq)) { delete saves[key]; drop(); return { ok: false, stale: true }; }
    delete saves[key];
    drop();
    return { ok: true, out };
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) { delete saves[key]; drop(); return { ok: false, stale: true }; }
    if (e && e.status === 409) {
      const conflict = staleReason(e);
      const error = conflict ? 'Changed in another tab — reload to see the latest.' : msg(e);
      put({ busy: false, error, conflict, canRetry: false });
      return { ok: false, error, conflict, reason: e.reason || '' };
    }
    const error = 'Could not save — ' + msg(e);
    put({ busy: false, error, conflict: false, canRetry: true });
    return { ok: false, error, conflict: false };
  }
}

export function retry(key) {
  const w = saves[key];
  if (!w) return Promise.resolve({ ok: false });
  return w.again ? Promise.resolve(w.again()) : save(key, w.fn);
}

// One write and what follows it: `after(out)` runs on success, and Retry runs all of it again. A press while the same write
// is still in flight sends nothing more.
function act(key, fn, after) {
  const go = async () => {
    const mine = W().tlWatchWrites[key];
    if (mine && mine.busy) return { ok: false, busy: true };
    const res = await save(key, fn, go);
    if (res.ok && after) after(res.out);
    return res;
  };
  return go();
}
