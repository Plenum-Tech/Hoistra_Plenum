// judges — the Judges tab's view models: judgesVals(s, a, now) turns the store state `s` into plain data the screen
// (views/Judges.jsx) draws. `a` is judgesActions.js (`import * as a`), whose actions the buttons these models carry
// call by name, so this file stays pure and there is no logic -> actions import cycle. `now` is the time, for "today
// 11:00". A missing rate reads "—" (never 0 or a guess); labelled counts are a sample fraction, never prevalence (the
// failure report's rule); controls exist only for the platform team; nothing on screen comes from anywhere but the state.
//
// What is returned: the judges table and the code checks (everyone's), and for the platform team the failure modes
// (tljModes), a mode's readiness card (tljReady), the new-version form (tljDraft), one judge's page (tljJudge), one
// run (tljRun) and the failure report (tljReport).
import { fmtUsd, fmtWhen } from '../../logic/tracesPage.js';
import { isOwner, judgeWrite, judgesOf } from './judgesCommon.js';
import {
  LIVE_RUN, TLJ_ROLES, TLJ_STATUS_LABELS, TLJ_STOP_REASON_MAX, TLJ_TEXT_MIN, TLJ_TRIAGE,
  exampleVerdict, len, num, shown, tljBelowMinimum, tljDevButton, tljDisagreementText, tljEstimateText, tljFloorText, tljFreezeState, tljLabelsText, tljMetric,
  tljNextStepText, tljProgressText, tljQuestionOf, tljRate, tljReadinessText, tljRunStatusText, tljSplitText, tljStopReasonCheck, tljTestButton
} from './judgesText.js';

const KIND_LABEL = Object.freeze({ llm: 'LLM judge', code: 'Code check' });
const SCOPE_LABEL = Object.freeze({ live: 'Every answer', case: 'Golden and eval cases' });
const DECISION_LABEL = Object.freeze({ use: 'Use for monitoring', do_not_use: 'Do not use' });
const ROLE_LABEL = Object.freeze({ clear_fail: 'Clear Fail', clear_pass: 'Clear Pass', borderline: 'Borderline' });
const TRIAGE_LABEL = Object.freeze({ judge_wrong: 'Judge wrong', label_wrong: 'Label wrong', definition_unclear: 'Definition unclear' });
export const READ_ONLY_NOTE = 'Read only. The platform team builds, runs and freezes judges; this list shows how each one scores.';
// What an input hands its handler: the event, or the value itself.
const ev = (e) => (e && e.target ? e.target.value : e === undefined || e === null ? '' : String(e));
const versionText = (v) => 'v' + shown(v);
const plural = (n, one, many) => n + ' ' + (n === 1 ? one : many);

function previewText(r) {
  if (!r) return '';
  const verdict = r.verdict === 'Fail' || r.verdict === 'Pass' ? r.verdict : 'No verdict';
  return verdict + (r.critique ? ' — ' + r.critique : '') + (num(r.usd) === null ? '' : ' (' + fmtUsd(r.usd) + ')')
    + (r.truncated ? ' · judged on cut evidence' : '');
}

function reportEntry(e) {
  const x = e.evaluator;
  const lc = e.labeled_counts || {};
  return {
    name: e.name || '—', definition: e.definition || '—',
    evaluator: x ? (x.type === 'llm' ? 'LLM judge ' : 'Code check ') + (x.judge_key || '') + ': held-out TPR '
      + tljMetric(x.test_tpr, x.test_tpr_interval) + ', TNR ' + tljMetric(x.test_tnr, x.test_tnr_interval)
      + (x.decision ? ' · ' + (DECISION_LABEL[x.decision] || x.decision) : '')
      : 'No evaluator yet: ' + (e.evaluator_reason || '—'),
    labelled: shown(lc.fail) + ' Fail · ' + shown(lc.pass) + ' Pass · sample fraction (not prevalence) ' + tljRate(e.sample_fraction),
    prevalence: e.prevalence ? 'Prevalence ' + tljMetric(e.prevalence.corrected, [e.prevalence.ci_low, e.prevalence.ci_high])
      : 'Prevalence not available: ' + (e.prevalence_reason || '—'),
    cases: (e.evaluation_case_candidates || []).length ? e.evaluation_case_candidates.join(', ') : '—'
  };
}

export function judgesVals(s, a, now) {
  const J = judgesOf(s);
  const owner = isOwner(s);
  const writes = J.tlJudgeWrites || {};
  const ws = (key) => {
    const w = writes[key];
    if (!w) return { busy: false, error: '', retry: null, reload: null };
    return { busy: !!w.busy, error: w.error || '', retry: w.canRetry ? () => a.retry(key) : null, reload: w.conflict ? () => a.reloadOpen() : null };
  };

  const rows = (J.tlJudgesRows || []).map((r) => ({
    id: r.id, mode: r.mode_name || '—', kind: KIND_LABEL[r.kind] || '—', version: versionText(r.version),
    status: TLJ_STATUS_LABELS[r.display_status] || '—', inUse: !!r.in_use, decision: r.decision ? DECISION_LABEL[r.decision] || '' : '',
    devTpr: tljMetric(r.dev && r.dev.tpr, r.dev && r.dev.tpr_interval), devTnr: tljMetric(r.dev && r.dev.tnr, r.dev && r.dev.tnr_interval),
    testTpr: tljMetric(r.test && r.test.tpr, r.test && r.test.tpr_interval), testTnr: tljMetric(r.test && r.test.tnr, r.test && r.test.tnr_interval),
    labels: tljLabelsText(r.labels, r.minimum), belowMinimum: tljBelowMinimum(r.labels, r.minimum), badge: r.min_labels_override || '',
    open: owner ? () => a.openJudge(r.id) : null, on: J.tlJudgeId === r.id
  }));
  const checks = (J.tlJudgesChecks || []).map((x) => ({ key: x.key, label: x.label || '—', description: x.description || '',
    runsOn: SCOPE_LABEL[x.scope] || '—', version: versionText(x.version) }));
  const modes = owner ? (J.tlJudgesModes || []).map((m) => ({ id: m.id, name: m.name || '—', status: m.status === 'frozen' ? 'Frozen' : 'Confirmed',
    open: () => a.openMode(m.id), on: J.tlJudgeModeId === m.id })) : [];

  const ready = J.tlJudgeReady;
  const versions = (ready && ready.judges) || [];
  const nextVersion = versions.length ? Math.max(...versions.map((j) => Number(j.version) || 0)) + 1 : 0;

  let card = null;
  if (owner && J.tlJudgeModeId) {
    const r = ready;
    const f = J.tlJudgeSplitForm || {};
    const sw = ws('judges:split:' + J.tlJudgeModeId);
    const thin = !!r && r.next_step === 'label_more';
    const listed = modes.find((m) => m.id === J.tlJudgeModeId);
    const excluded = r && r.labels ? num(r.labels.excluded) || 0 : 0;
    const trainOnly = r && r.labels ? num(r.labels.train_only) || 0 : 0;
    card = {
      loading: !!J.tlJudgeReadyLoading && !J.tlJudgeReadyLoadedAt, error: J.tlJudgeReadyErr || '',
      name: (r && r.mode && r.mode.name) || (listed && listed.name) || '—', definition: (r && r.mode && r.mode.definition) || '',
      labels: tljReadinessText(r), floor: r ? tljFloorText(r.minimum) : '',
      excluded: excluded ? plural(excluded, 'labelled answer', 'labelled answers')
        + ' left out: another answer from the same conversation already counts, or the answer can no longer be read.' : '',
      trainOnly: trainOnly ? plural(trainOnly, 'label', 'labels') + ' approved in bulk can join the train answers only.' : '',
      nextStep: r ? tljNextStepText(r.next_step, r.minimum && r.minimum.per_class) : '—',
      split: r && r.split ? { text: tljSplitText(r.split), badge: r.split.badge || '' } : null,
      canSplit: !!r && !r.split, splitBelowMin: thin, splitOverride: !!f.override, splitReason: f.reason || '',
      setSplitOverride: (e) => a.setSplitForm({ override: !!(e && e.target ? e.target.checked : e) }),
      setSplitReason: (e) => a.setSplitForm({ reason: ev(e) }),
      splitReady: !!r && !r.split && !sw.busy && (r.next_step === 'create_split' || (thin && !!f.override && len(f.reason) > 0)),
      createSplit: () => a.createSplit(), splitBusy: sw.busy, splitError: sw.error, splitRetry: sw.retry,
      canRegister: !!r && !!r.split, nextVersion, startLlm: () => a.startDraft('llm'), startCode: () => a.startDraft('code'),
      versions: versions.map((j) => ({ id: j.id, open: () => a.openJudge(j.id), on: J.tlJudgeId === j.id,
        label: versionText(j.version) + ' · ' + (KIND_LABEL[j.kind] || '—') + ' · ' + (TLJ_STATUS_LABELS[j.display_status] || '—') })),
      warnings: (J.tlJudgeWarnings || []).map((w) => w.message).filter(Boolean),
      reload: () => a.openMode(J.tlJudgeModeId), close: () => a.closeMode()
    };
  }

  let draft = null;
  if (owner && J.tlJudgeDraft) {
    const d = J.tlJudgeDraft;
    const train = ((J.tlJudgeSplit && J.tlJudgeSplit.members) || []).filter((m) => m.split === 'train');
    const examples = TLJ_ROLES.map((role) => {
      const e = (d.examples || {})[role.key] || {};
      const m = train.find((x) => x.turn_id === e.turn_id);
      const fits = (x) => role.key === 'borderline' || (role.key === 'clear_fail' ? !!x.label_present : !x.label_present);
      return { role: role.key, label: role.label, value: e.turn_id || '', critique: e.critique || '',
        verdict: exampleVerdict(role.key, m) || '—', source: m ? m.excerpt || '' : '',
        options: train.filter(fits).map((x) => ({ value: x.turn_id, label: (x.label_present ? 'Fail · ' : 'Pass · ') + tljQuestionOf(x.excerpt) })),
        setTurn: (v) => a.setExample(role.key, { turn_id: ev(v) }), setCritique: (v) => a.setExample(role.key, { critique: ev(v) }) };
    });
    const picked = examples.map((x) => x.value).filter(Boolean);
    const rw = ws('judges:register:' + J.tlJudgeModeId);
    const noteNeeded = nextVersion > 0;
    const llmOk = d.kind === 'llm' && len(d.fail_rule) >= TLJ_TEXT_MIN && len(d.pass_rule) >= TLJ_TEXT_MIN && picked.length === 3
      && new Set(picked).size === 3 && examples.every((x) => x.verdict !== '—' && len(x.critique) >= TLJ_TEXT_MIN);
    const codeOk = d.kind === 'code' && !!d.check_key;
    draft = {
      isLlm: d.kind !== 'code', title: 'Version ' + nextVersion + ' · ' + KIND_LABEL[d.kind === 'code' ? 'code' : 'llm'],
      task: d.task || '', failRule: d.fail_rule || '', passRule: d.pass_rule || '', edgeCases: d.edge_cases || '', changeNote: d.change_note || '',
      set: (field) => (v) => a.setDraft({ [field]: ev(v) }), noteNeeded, examples,
      splitLoading: !!J.tlJudgeSplitLoading, splitError: J.tlJudgeSplitErr || '',
      trainEmpty: d.kind !== 'code' && !J.tlJudgeSplitLoading && !!J.tlJudgeSplit && !train.length,
      checks: (J.tlJudgesChecks || []).filter((x) => x.scope === 'live').map((x) => ({ value: x.key, label: x.label })), checkKey: d.check_key || '',
      canSubmit: (llmOk || codeOk) && (!noteNeeded || len(d.change_note) > 0) && !rw.busy,
      submit: () => a.register(), busy: rw.busy, error: rw.error, retry: rw.retry, cancel: () => a.cancelDraft()
    };
  }

  let judge = null;
  if (owner && J.tlJudgeId) {
    const out = J.tlJudge;
    const j = (out && out.judge) || null;
    const runs = (out && out.runs) || [];
    const parts = (j && j.prompt_parts) || null;
    const dev = tljDevButton(j, runs);
    const test = tljTestButton(j, runs);
    const fz = tljFreezeState(j, runs);
    const jid = (j && j.id) || J.tlJudgeId;
    const rw = ws(judgeWrite.run(jid));
    const fw = ws(judgeWrite.freeze(jid));
    const dw = ws(judgeWrite.decision(jid));
    const tw = ws(judgeWrite.threshold(jid));
    const est = J.tlJudgeEstimate;
    const freeze = J.tlJudgeFreeze;
    const check = freeze ? tljStopReasonCheck(freeze.reason) : null;
    const testDone = runs.some((r) => r.split === 'test' && r.status === 'done');
    const dd = J.tlJudgeDecisionDraft || {};
    const th = String(J.tlJudgeThresholdDraft || '').trim();
    const thOk = th !== '' && isFinite(Number(th)) && Number(th) > 0 && Number(th) < 1;
    const checkRow = j && j.kind === 'code' ? (J.tlJudgesChecks || []).find((x) => x.key === j.check_key) : null;
    judge = {
      loading: !!J.tlJudgeLoading && !J.tlJudgeLoadedAt, error: J.tlJudgeErr || '',
      title: j ? (j.mode_name || '—') + ' · ' + versionText(j.version) + ' · ' + (KIND_LABEL[j.kind] || '—') : '—',
      status: j ? TLJ_STATUS_LABELS[j.display_status] || '—' : '—', inUse: !!(j && j.in_use),
      frozenText: j && j.status === 'frozen' ? 'Frozen' + (j.frozen_at ? ' ' + fmtWhen(j.frozen_at, now) : '')
        + (j.frozen_by_email ? ' by ' + j.frozen_by_email : '') + (j.stop_reason ? ' — ' + j.stop_reason : '') : '',
      model: (j && j.model) || '', changeNote: (j && j.change_note) || '',
      warnings: ((out && out.warnings) || []).map((w) => w.message).filter(Boolean),
      parts: parts ? [['Task', parts.task], ['Fail when', parts.fail_rule], ['Pass when', parts.pass_rule], ['Edge cases', parts.edge_cases]]
        .filter((p) => len(p[1])).map((p) => ({ label: p[0], text: String(p[1]) })) : [],
      examples: ((parts && parts.examples) || []).map((e) => ({ turnId: e.turn_id, role: ROLE_LABEL[e.role] || '—', verdict: e.verdict || '—',
        critique: e.critique || '', source: e.excerpt || '' })),
      promptText: (j && j.prompt_text) || '',
      check: j && j.kind === 'code' ? { label: (checkRow && checkRow.label) || '—', version: versionText(j.check_version) } : null,
      preview: ((out && out.preview_turn_ids) || []).map((t, i) => {
        const w = ws(judgeWrite.preview(jid, t));
        return { turnId: t, label: 'Dev answer ' + (i + 1), run: () => a.preview(t), busy: w.busy, error: w.error, retry: w.retry,
          result: previewText((J.tlJudgePreview || {})[t]) };
      }),
      dev: { label: dev.label, enabled: dev.enabled && !rw.busy, note: dev.note, start: () => a.estimate('dev') },
      test: { label: test.label, enabled: test.enabled && !rw.busy, note: test.note, start: () => a.estimate('test') },
      freeze: j && j.status !== 'frozen' ? { can: fz.can, note: fz.note, open: () => a.freezeOpen() } : null,
      freezeDialog: freeze ? { reason: freeze.reason || '', setReason: (v) => a.setFreezeReason(ev(v)),
        hint: check.ok ? (TLJ_STOP_REASON_MAX - len(freeze.reason)).toLocaleString('en-GB') + ' characters left' : check.text,
        canConfirm: check.ok && !fw.busy, confirm: () => a.freeze(), cancel: () => a.freezeClose(), busy: fw.busy, error: fw.error,
        reload: fw.reload } : null,
      estimate: est ? { split: est.split, title: est.split === 'test' ? 'Run the held-out test' : 'Run on dev', loading: !!est.loading,
        error: est.error || '', text: tljEstimateText(est.data, est.split), cap: est.cap || '', setCap: (v) => a.setCap(ev(v)),
        canStart: !!est.data && !rw.busy, start: () => a.startRun(), busy: rw.busy, startError: rw.error, retry: rw.retry,
        close: () => a.closeEstimate() } : null,
      runs: runs.map((r) => ({ id: r.id, status: tljRunStatusText(r), open: () => a.openRun(r.id), on: J.tlJudgeRunId === r.id,
        label: (r.split === 'test' ? 'Held-out test' : 'Dev run') + (r.started_at ? ' · ' + fmtWhen(r.started_at, now) : '') })),
      decision: testDone ? { current: j && j.decision ? DECISION_LABEL[j.decision] || '' : '', note: (j && j.decision_note) || '',
        options: [['use', 'Use for monitoring'], ['do_not_use', 'Do not use']].map(([k, l]) => ({ key: k, label: l, on: dd.decision === k,
          pick: () => a.setDecisionDraft({ decision: k }) })),
        noteDraft: dd.note || '', setNote: (v) => a.setDecisionDraft({ note: ev(v) }),
        canSave: (dd.decision === 'use' || dd.decision === 'do_not_use') && !dw.busy, save: () => a.decide(),
        busy: dw.busy, error: dw.error, reload: dw.reload } : null,
      threshold: j && j.in_use ? { current: num(j.monitor_threshold) === null ? 'not set' : Number(j.monitor_threshold).toFixed(2),
        draft: J.tlJudgeThresholdDraft || '', setDraft: (v) => a.setThresholdDraft(ev(v)),
        invalid: th !== '' && !thOk ? 'The threshold is a share between 0 and 1, for example 0.15.' : '',
        canSave: thOk && !tw.busy, save: () => a.saveThreshold(), busy: tw.busy, error: tw.error, reload: tw.reload } : null,
      revise: j ? () => a.revise() : null,
      iterations: ((out && out.iterations) || []).map((it) => ({ version: versionText(it.version), change: it.change_note || '—',
        devRuns: shown(it.dev_runs), devTpr: tljMetric(it.dev_tpr, it.dev_tpr_interval), devTnr: tljMetric(it.dev_tnr, it.dev_tnr_interval),
        disagreements: num(it.disagreements) === null ? '—' : it.disagreements + (num(it.untriaged) ? ' (' + it.untriaged + ' to triage)' : ''),
        flips: shown(it.label_flips), inputChanged: shown(it.input_changed), frozen: it.frozen ? 'Yes' : 'No',
        test: it.test ? tljMetric(it.test.tpr, it.test.tpr_interval) + ' / ' + tljMetric(it.test.tnr, it.test.tnr_interval) : '—' })),
      reload: () => a.openJudge(J.tlJudgeId), close: () => a.closeJudge()
    };
  }

  let run = null;
  if (owner && J.tlJudgeRunId) {
    const r = J.tlJudgeRun;
    const triageable = !!r && r.split === 'dev' && r.status === 'done';
    const list = (r && r.disagreements) || [];
    const live = !!r && LIVE_RUN.has(r.status);
    const nErr = r ? num(r.n_error) || 0 : 0;
    const nCut = r ? num(r.n_truncated) || 0 : 0;
    run = {
      loading: !!J.tlJudgeRunLoading && !J.tlJudgeRunLoadedAt, error: J.tlJudgeRunErr || '',
      title: r ? (r.split === 'test' ? 'Held-out test' : 'Dev run') + (r.started_at ? ' · ' + fmtWhen(r.started_at, now) : '') : '—',
      status: tljRunStatusText(r), live, progress: live ? tljProgressText(r) : '',
      tpr: tljMetric(r && r.tpr, r && r.tpr_interval), tnr: tljMetric(r && r.tnr, r && r.tnr_interval),
      agreement: tljRate(r && r.agreement), n: shown(r && r.n), cost: r && num(r.usd) !== null ? fmtUsd(r.usd) : '—',
      counts: r && num(r.tp) !== null ? 'TP ' + r.tp + ' · FN ' + r.fn + ' · TN ' + r.tn + ' · FP ' + r.fp : '',
      errors: nErr ? (nErr === 1 ? '1 answer could not be judged.' : nErr + ' answers could not be judged.') : '',
      truncated: nCut ? (nCut === 1 ? '1 answer was judged on cut evidence.' : nCut + ' answers were judged on cut evidence.') : '',
      finalNote: r && r.split === 'test' && r.status === 'done' ? 'Held-out numbers are final for this version.' : '',
      untriaged: triageable ? list.filter((d) => !d.triage).length : 0,
      disagreements: list.map((d) => {
        const w = ws(judgeWrite.triage(r.id, d.turn_id));
        return { turnId: d.turn_id, kind: tljDisagreementText(d), excerpt: d.excerpt || '', triage: d.triage || '',
          triageLabel: d.triage ? TRIAGE_LABEL[d.triage] || '—' : (triageable ? 'Not triaged' : ''), note: d.note || '', by: d.by_email || '',
          noteDraft: (J.tlJudgeTriageNotes || {})[d.turn_id] || '', setNote: (v) => a.setTriageNote(d.turn_id, ev(v)),
          choices: triageable ? TLJ_TRIAGE.map((t) => ({ key: t.key, label: t.label, on: d.triage === t.key, pick: () => a.triage(d.turn_id, t.key) })) : [],
          busy: w.busy, error: w.error, retry: w.retry, reload: w.reload };
      }),
      emptyText: r && r.status === 'done' && !list.length ? 'The judge agreed with people on every answer in this run.' : ''
    };
  }

  const rep = J.tlJudgeReport;
  const fileW = ws('judges:report-file');
  const report = owner ? {
    loading: !!J.tlJudgeReportLoading, error: J.tlJudgeReportErr || '', loaded: !!J.tlJudgeReportLoadedAt,
    generated: rep && rep.generated_at ? fmtWhen(rep.generated_at, now) : '',
    entries: ((rep && rep.modes) || []).map(reportEntry), empty: !!J.tlJudgeReportLoadedAt && !((rep && rep.modes) || []).length,
    load: () => a.loadReport(), download: () => a.downloadReport(), downloadBusy: fileW.busy, downloadError: fileW.error
  } : null;

  return {
    tljOwner: owner,
    tljReadOnlyNote: owner ? '' : READ_ONLY_NOTE,
    tljLoading: !!J.tlJudgesLoading && !J.tlJudgesLoadedAt, tljError: J.tlJudgesErr || '', tljReload: () => a.load(),
    tljRows: rows, tljEmpty: !!J.tlJudgesLoadedAt && !rows.length,
    tljEmptyText: owner ? 'No judges yet. Pick a failure mode below to start one.' : 'No judges yet.',
    tljChecks: checks, tljChecksError: J.tlJudgesChecksErr || '',
    tljChecksLoading: J.tlJudgesChecks === null && !J.tlJudgesChecksErr && !!J.tlJudgesLoading,
    tljChecksEmpty: Array.isArray(J.tlJudgesChecks) && !J.tlJudgesChecks.length,
    tljModes: modes, tljModesEmpty: owner && !!J.tlJudgesLoadedAt && !modes.length,
    tljReady: card, tljDraft: draft, tljJudge: judge, tljRun: run, tljReport: report
  };
}
