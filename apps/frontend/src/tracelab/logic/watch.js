// watch — the Watch tab's view models: watchVals(s, a, now, company) turns the store state `s` into plain data the screen
// (views/Watch.jsx) draws. `a` is watchActions.js (`import * as a`), whose actions the buttons these models carry call by
// name, so this file stays pure and there is no logic -> actions import cycle. `now` is the time, for "today 11:00";
// `company` is the name of the company being worked in, for the release report's heading and the scope line.
//
// Every number is the server's and nothing here estimates anything: a missing one reads "—" (never zero, never
// "undefined"), a rate is shown with its 95% interval, and a failure rate is the corrected one, with the share flagged in the
// sample beside it. Controls exist only for who may use them: Run now, Resume and the review batch are the platform team's
// (any superadmin: for the company being viewed, or for every company viewing none; never a company admin, who reads the
// company's own batches, Due marker and charts); eval cases and release checks are a company reader's. A batch is resumed
// from the scope it was started in, so across every company only a platform-wide one has Resume. Nothing on screen comes
// from anywhere but the state.
//
// What is returned (every key used by the screen starts `tlw`): the scope and who may do what (tlwKnown, tlwScope,
// tlwCanStart, tlwCanCases, tlwCompany, tlwCasesHint), the batches with the Due marker (tlwDue, tlwBatches, tlwBatch,
// tlwPlan), the charts and code checks (tlwCharts, tlwChecks), eval cases (tlwCases), release checks (tlwRelease), the
// automatic-vs-people trend (tlwAuto) and the writes' errors (tlwWrites). tlwIdentityError carries the failed read of who the
// person is (empty otherwise), so the page can say so and offer a retry.
import { fmtWhen } from '../../logic/tracesPage.js';
import { canStart, identityError, inCompany, isPlatform, known, viewsCompany, watchOf, watchWrite } from './watchCommon.js';
import { CASE_STATUS, DECISION_TEXT, LIVE, STATUS_TEXT, chartGeometry, dueLabel, has, latestModes, pctText, stackShares, usdText } from './watchChart.js';

export const EMPTY_TEXT = 'No batches yet. Monitoring has not run for this scope.';
export const CASES_HINT = 'Eval cases and release checks belong to one company: view as a company to work on them.';
export const ANSWER_HINT = 'View as a company to open this answer.';
export const IDENTITY_TEXT = 'Could not tell who you are — ';
export const IDENTITY_HINT = 'Not shown until the page can tell who you are.';
const show = (v) => (has(v) ? String(v) : '—');
// What an input hands its handler: the event, or the value itself.
const ev = (e) => (e && e.target ? e.target.value : e === undefined || e === null ? '' : String(e));
const tagOf = (x) => (x.validated ? 'validated' : 'raw rate only');

function batchVals(W, a, team, inCo) {
  const detail = W.tlMonitorBatch;
  const b = detail && detail.batch;
  const reviewIds = W.tlMonitorReviewIds || {};
  const writes = W.tlWatchWrites || {};
  return {
    loading: !!W.tlMonitorBatchLoading, error: W.tlMonitorBatchErr || '', period: b ? b.period_label : '',
    flagged: ((detail && detail.flagged) || []).map((f) => ({
      turnId: f.turn_id, question: f.question || '(no question recorded)', mode: f.mode_name || 'failure mode',
      critique: f.critique || '', group: f.group || '',
      openAnswer: inCo ? () => a.openAnswer(f.turn_id) : null, hint: inCo ? '' : ANSWER_HINT })),
    modes: ((b && b.results && b.results.modes) || []).map((m) => ({
      name: m.mode_name || 'failure mode', crossed: !!m.crossed, corrected: pctText(m.corrected),
      interval: has(m.ci_low) && has(m.ci_high) ? pctText(m.ci_low) + ' to ' + pctText(m.ci_high) : '—', raw: pctText(m.raw),
      nSample: show(m.n_sample), threshold: has(m.threshold) ? 'threshold ' + String(m.threshold) : '—',
      warning: m.validity_warning || '', reason: m.reason || '', canReview: team && Number(m.flagged) > 0,
      review: () => a.openReviewBatch(b.id, m.mode_id), reviewing: !!(writes[watchWrite.review(b.id, m.mode_id)] || {}).busy,
      reviewIds: reviewIds[b.id + ':' + m.mode_id] || m.review_batch_ids || [] })),
    checks: ((b && b.results && b.results.code_checks) || []).map((x) => ({
      label: x.label, rate: pctText(x.rate), flagged: x.flagged, decided: x.decided, tag: tagOf(x) })),
    close: () => a.closeBatch()
  };
}

function caseVals(W, a, k) {
  const d = (W.tlCasesDrafts || {})[k.id] || {};
  const lr = k.latest_run || {};
  const writes = W.tlWatchWrites || {};
  const busy = [watchWrite.confirm, watchWrite.baseline, watchWrite.repeat, watchWrite.retire].some((key) => (writes[key(k.id)] || {}).busy);
  return {
    id: k.id, key: k.case_key + ' v' + k.version, mode: k.mode_name || '—', kind: k.kind,
    kindText: k.kind === 'regression' ? 'Regression' : k.kind === 'capability' ? 'Capability (baseline ' + pctText(k.baseline_pass_rate) + ')' : 'Not baselined',
    status: k.status, statusText: CASE_STATUS[k.status] || k.status, question: (k.input && k.input.question) || '',
    passAt1: pctText(lr.pass_at_1), passAt3: pctText(lr.pass_at_3), passAt5: pctText(lr.pass_at_5), passHat: pctText(lr.pass_hat_k),
    passHatTitle: has(lr.k) && Number(lr.k) > 0 ? 'pass^' + lr.k + ': every one of ' + lr.k + ' runs right' : '',
    canConfirm: k.status === 'proposed', canBaseline: k.status === 'draft',
    canRepeat: LIVE.includes(k.status) && !!k.golden_case_id, canRetire: LIVE.includes(k.status),
    answer: d.expected_answer || '', setAnswer: (e) => a.setCaseDraft(k.id, 'expected_answer', ev(e)),
    evaluation: d.evaluation || 'objective', setEvaluation: (e) => a.setCaseDraft(k.id, 'evaluation', ev(e)),
    n: d.n === undefined ? '10' : String(d.n), setN: (e) => a.setCaseDraft(k.id, 'n', ev(e)),
    reason: d.reason || '', setReason: (e) => a.setCaseDraft(k.id, 'reason', ev(e)),
    confirm: () => a.confirmCase(k.id), baseline: () => a.baseline(k.id), repeat: () => a.repeat(k.id), retire: () => a.retire(k.id), busy
  };
}

export function watchVals(s, a, now, company) {
  const W = watchOf(s);
  const platform = isPlatform(s);
  const team = canStart(s);
  const viewing = viewsCompany(s);
  const inCo = inCompany(s);
  const body = W.tlMonitorBatches || null;
  const batches = (body && body.batches) || [];
  const charts = W.tlMonitorCharts || {};
  const plan = W.tlMonitorPlan;
  const draft = W.tlMonitorPlanDraft || {};
  const writes = W.tlWatchWrites || {};
  const cases = W.tlCases || [];
  const releases = (W.tlRelease && W.tlRelease.checks) || [];
  const rdraft = Object.assign({ k: 5, include_capability: true }, W.tlReleaseDraft || {});
  const weeks = stackShares((W.tlAuto && W.tlAuto.weeks) || []);
  const name = String(company || '').trim();
  return {
    tlwKnown: known(s),
    // Why the person is not known, when the read that says so failed: shown with Refresh in place of an endless "Reading…".
    tlwIdentityError: identityError(s) ? IDENTITY_TEXT + identityError(s) : '',
    tlwIdentityHint: IDENTITY_HINT,
    tlwReload: () => a.reload(),
    tlwLoading: !!W.tlMonitorLoading && !W.tlMonitorLoadedAt,
    tlwError: W.tlMonitorErr || '',
    tlwChecksError: W.tlMonitorChecksErr || '',
    // The inline errors of Watch writes: a network failure has Retry, a stale base a reload, and a rule the server refused neither.
    tlwWrites: Object.keys(writes).filter((k) => writes[k] && writes[k].error)
      .map((k) => ({ key: k, error: writes[k].error, retry: writes[k].canRetry ? () => a.retry(k) : null, reload: writes[k].conflict ? () => a.reload() : null })),
    tlwScope: !known(s) ? '' : platform ? 'All companies' : 'Company: ' + (name || 'this company'),
    tlwCanStart: team,
    tlwCanCases: inCo,
    tlwCompany: inCo,
    tlwCasesHint: platform ? CASES_HINT : '',
    tlwDue: dueLabel(body && body.due),
    tlwRunNow: () => a.openPlan(),
    tlwPlan: W.tlMonitorPlanOpen ? {
      loading: !!W.tlMonitorPlanLoading, error: W.tlMonitorPlanErr || '',
      data: plan ? { period: plan.period_label || '—', nWindow: show(plan.n_window), nRandom: show(plan.n_random), nRiskOnly: show(plan.n_risk_only),
        nToJudge: show(plan.n_to_judge), cost: usdText(plan.usd_estimate), judges: (plan.judges || []).map((j) => j.mode_name || j.judge_key).filter(Boolean),
        warnings: plan.warnings || [] } : null,
      rate: draft.rate === undefined ? '' : String(draft.rate), cap: draft.cap === undefined ? '' : String(draft.cap),
      setRate: (e) => a.setPlanDraft('rate', ev(e)), setCap: (e) => a.setPlanDraft('cap', ev(e)),
      scopeText: viewing ? 'Covers ' + (name || 'this company') + ' only.' : 'Covers every company.',
      refresh: () => a.openPlan(), start: () => a.start(), starting: !!(writes[watchWrite.start] || {}).busy, close: () => a.closePlan()
    } : null,
    tlwBatches: batches.map((b) => ({
      id: b.id, period: b.period_label || '—', status: b.status, statusText: STATUS_TEXT[b.status] || b.status || '—',
      scope: b.scope === 'platform' ? 'All companies' : 'One company',
      nWindow: show(b.results && b.results.n_window), nRandom: show(b.results && b.results.n_random),
      cost: usdText(b.usd), crossed: !!b.crossed, on: W.tlMonitorBatchId === b.id, open: () => a.openBatch(b.id),
      resume: team && b.status === 'interrupted' && (viewing || b.scope === 'platform') ? () => a.resume(b.id) : null, resuming: !!(writes[watchWrite.resume(b.id)] || {}).busy })),
    tlwBatchesEmpty: !!W.tlMonitorLoadedAt && !batches.length && !W.tlMonitorErr,
    tlwEmptyText: EMPTY_TEXT,
    tlwBatch: W.tlMonitorBatchId ? batchVals(W, a, team, inCo) : null,
    tlwCharts: latestModes(batches).map((m) => {
      const ch = charts[m.modeId] || {};
      return { modeId: m.modeId, name: m.name, crossed: m.crossed, loading: !!ch.loading && !ch.chart, error: ch.error || '', geometry: chartGeometry(ch.chart) };
    }),
    tlwChecks: (W.tlMonitorChecks || []).map((x) => ({
      key: x.evaluator_key || x.label, label: x.label, tag: tagOf(x),
      weeks: (x.weeks || []).slice(-6).map((w) => ({ period: w.period_label, rate: pctText(w.rate), n: w.n })) })),
    tlwCases: cases.map((k) => caseVals(W, a, k)),
    tlwCasesLoading: !!W.tlCasesLoading && !W.tlCasesLoadedAt,
    tlwCasesError: W.tlCasesErr || '',
    tlwCasesEmpty: !!W.tlCasesLoadedAt && !cases.length,
    tlwRelease: {
      list: releases.map((r) => ({
        id: r.golden_run_id, label: r.run_key, decision: r.decision, decisionText: DECISION_TEXT[r.decision] || r.decision || '—',
        tone: r.decision === 'pass' ? 'ok' : r.decision === 'block' ? 'risk' : 'warn', reason: r.reason || '', k: show(r.k),
        when: fmtWhen(r.started_at, now), downloadJson: () => a.downloadRelease(r.golden_run_id, 'json', name),
        downloadMd: () => a.downloadRelease(r.golden_run_id, 'md', name), openRun: () => a.openRun(r.golden_run_id) })),
      loading: !!W.tlReleaseLoading && !W.tlReleaseLoadedAt, error: W.tlReleaseErr || '',
      empty: !!W.tlReleaseLoadedAt && !releases.length,
      k: String(rdraft.k), setK: (e) => a.setReleaseDraft('k', ev(e)),
      includeCapability: rdraft.include_capability !== false,
      toggleCapability: () => a.setReleaseDraft('include_capability', rdraft.include_capability === false),
      start: () => a.startRelease(), starting: !!(writes[watchWrite.release] || {}).busy
    },
    tlwAuto: {
      loading: !!W.tlAutoLoading && !W.tlAutoLoadedAt, error: W.tlAutoErr || '', weeks,
      empty: !!W.tlAutoLoadedAt && !weeks.some((w) => !w.empty), note: (W.tlAuto && W.tlAuto.definitions && W.tlAuto.definitions.auto_by_source) || ''
    }
  };
}
