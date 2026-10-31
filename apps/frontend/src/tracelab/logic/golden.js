// golden — pure helpers and view models for Trace Lab's golden sets and golden runs: labels, rate and money
// formats, the run row, the start dialog's estimate lines, the upload report, the step decision, the fidelity
// caveats, and one view model per screen (Golden set, golden runs, the start dialog, one run, two runs compared,
// the expected outcome of a golden answer). No state and no requests: the screens draw what these return and call
// goldenActions.js.
import { fmtMs, fmtUsd, fmtWhen } from '../../logic/tracesPage.js';
import { PAGE_REASONS, noticeFor } from './shell.js';

export const GOLDEN_STEP_MS = 5000;
export const CASES_PAGE = 50;
export const KS = [1, 3, 5];
export const HARD_MAX_K = 20;
export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const STATUS_LABELS = Object.freeze({
  queued: 'Starting', running: 'Running', paused: 'Paused', interrupted: 'Interrupted', capped: 'Stopped at its cap',
  done: 'Done', failed: 'Failed', cancelled: 'Cancelled'
});
export const PAUSED_LABELS = Object.freeze({
  page_closed: 'Paused: the page was closed', paused: 'Paused by an admin', run_cap: 'Stopped at its spending cap',
  monthly_cap: "Stopped at this month's budget", restart: 'Paused: Trace Lab restarted'
});
export const OUTCOME_LABELS = Object.freeze({
  pending: 'Grading…', pass: 'Pass', fail: 'Fail', unsure: 'Unsure — a person decides',
  unvalidated: 'Unvalidated — the grader\'s verdict is a suggestion',
  degraded: 'Degraded — a step was refused', stale_reference: 'Stale reference — re-verify the expected answer',
  error: 'Error — not measured'
});
export const PURPOSE_LABELS = Object.freeze({ adhoc: 'Ad hoc', baseline: 'Baseline', release_check: 'Release check', repeat: 'Repeat' });
export const FLIP_LABELS = Object.freeze({
  regressed: '↓ now failing', fixed: '↑ now passing', still_failing: 'Still failing', now_measured: 'Now measured',
  not_measured: 'Not measured'
});
export const CASE_FLAGS = Object.freeze([['', 'All cases'], ['stale', 'Stale'], ['unverified', 'Not verified'],
  ['objective', 'Objective'], ['judgment', 'Judgment'], ['write_intent', 'Asks for an action']]);
export const PARAM_LABELS = Object.freeze({
  model: 'Chat model', judge_model: 'Grading model', grader_version: 'Grader version', git_sha: 'Build',
  context_default: 'Page context', set_hash: 'Case list', routing: 'Model routing', overlays: 'Skill tuning'
});
export const BUILDING_NOTE = 'A building is selected in the header. Choose "All buildings" in the building picker at the top of the page, then try again.';

// What a golden run cannot measure the way live chat runs (addendum §2.5), shown on every golden run.
// The same four sentences, word for word, as the service's runner.FIDELITY_CAVEATS (Phase 3 Task 7).
export const FIDELITY_CAVEATS = Object.freeze([
  'Company memories are not recalled in a test run.',
  "Follow-ups answered by a specialist engine may lack the previous answer's context.",
  'Approved instruction updates are loaded read-only.',
  "The contract analyst's and the embeddings' spend is not measured."
]);

const nil = (v) => v === null || v === undefined;
const plural = (n, one, many) => n + ' ' + (n === 1 ? one : many);
export const pct = (v) => (nil(v) || isNaN(Number(v)) ? '—' : Math.round(Number(v) * 100) + '%');
export const usd = (v) => (nil(v) ? '—' : fmtUsd(v));
export const signedPts = (d) => (nil(d) ? '' : (d > 0 ? '+' : d < 0 ? '−' : '±') + Math.abs(Math.round(d * 100)) + ' pts');
export const signedUsd = (d) => (nil(d) ? '' : (d > 0 ? '+' : d < 0 ? '−' : '±') + fmtUsd(Math.abs(d)));

export function paramLabel(key) {
  if (PARAM_LABELS[key]) return PARAM_LABELS[key];
  if (String(key).indexOf('flags.') === 0) return 'Chat setting';
  const s = String(key || '').replace(/[_.]+/g, ' ').trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : 'Setting';
}

export function fmtCi(w) {
  return Array.isArray(w) && !nil(w[0]) && !nil(w[1]) ? Math.round(w[0] * 100) + '–' + Math.round(w[1] * 100) + '%' : '';
}

export function fmtRate(r) {
  if (!r) return '—';
  const n = r.graded !== undefined ? r.graded : r.measured;
  if (!n) return '—';
  return r.passes + '/' + n + ' pass (' + pct(r.rate) + ')';
}

export function rateTitle(r) {
  const ci = r ? fmtCi(r.wilson) : '';
  return ci ? '95% interval ' + ci : '';
}

export const isGoldenSession = (sid) => /^golden-/.test(String(sid || ''));
const isOwn = (run, own) => !!run && (run.mine === true || !!(own && own[run.id]));

// One golden run as the list and the run's header show it.
export function runRow(r, now, own) {
  const reason = r.paused_reason && PAUSED_LABELS[r.paused_reason];
  const status = (r.status === 'paused' || r.status === 'capped') && reason ? reason : (STATUS_LABELS[r.status] || '—');
  const set = r.set && !nil(r.set.version) ? (r.set.kind === 'regression' ? 'Regression cases' : 'Version ' + r.set.version) : '—';
  return {
    id: r.id, label: r.label || '—', set, purpose: PURPOSE_LABELS[r.purpose] || '—', k: 'k=' + (r.k || 1),
    cases: plural(Number(r.cases) || 0, 'case', 'cases'), pass: fmtRate(r.pass_rate), passTitle: rateTitle(r.pass_rate),
    cost: usd(r.usd_spent), status, progress: r.items_total ? (r.items_done || 0) + '/' + r.items_total : '—',
    when: fmtWhen(r.started_at, now), who: r.started_by_email || '—', mine: isOwn(r, own),
    running: r.status === 'running', resumable: ['paused', 'interrupted', 'capped'].indexOf(r.status) > -1
  };
}

// What the page does on the next tick of its step loop: step when the run is idle, read it while a case is being
// answered, stop when the run is not running or not this page's to step.
export function stepDecision(run, own) {
  if (!run || run.status !== 'running' || !isOwn(run, own)) return 'stop';
  return run.current ? 'poll' : 'step';
}

export function estimateLines(est) {
  if (!est) return [];
  const lines = [];
  if (!nil(est.cases)) {
    const k = est.k || 1;
    const asks = nil(est.messages) ? Number(est.cases) * k : Number(est.messages);
    lines.push(plural(Number(est.cases), 'case', 'cases') + ' × k=' + k + ' → ' + plural(asks, 'question', 'questions'));
  }
  if (!nil(est.est_total_usd)) {
    const parts = !nil(est.est_answers_usd) && !nil(est.est_grading_usd)
      ? ' (' + fmtUsd(est.est_answers_usd) + ' answers + ' + fmtUsd(est.est_grading_usd) + ' grading)' : '';
    lines.push('About ' + fmtUsd(est.est_total_usd) + parts);
  }
  if (!nil(est.est_minutes)) lines.push('About ' + Math.max(1, Math.round(Number(est.est_minutes) || 0)) + ' minutes');
  if (!nil(est.usd_cap_default)) lines.push('Spending cap ' + fmtUsd(est.usd_cap_default) + (nil(est.run_max_usd) ? '' : ' (at most ' + fmtUsd(est.run_max_usd) + ')'));
  if (!nil(est.month_spent_usd) && !nil(est.month_cap_usd)) lines.push('This month: ' + fmtUsd(est.month_spent_usd) + ' spent of ' + fmtUsd(est.month_cap_usd));
  return lines;
}

// Row errors and warnings as one line each: "Row 4, Question: The question is empty."
export function issueLines(list) {
  return (list || []).slice(0, 100).map((e) => {
    if (!e || typeof e !== 'object') return String(e || '');
    return (e.row ? 'Row ' + e.row + (e.column ? ', ' + e.column : '') + ': ' : (e.column ? e.column + ': ' : '')) + (e.message || '');
  });
}

function flagLines(flags) {
  const f = flags || {};
  const out = [];
  if (f.unverified) out.push(plural(f.unverified, 'case has', 'cases have') + ' no Verified on date.');
  if (f.write_intent) out.push(plural(f.write_intent, 'case asks', 'cases ask') + ' for an action: a golden run changes nothing, so those answers read as degraded.');
  if (f.evaluation_defaulted) out.push(plural(f.evaluation_defaulted, 'case has', 'cases have') + ' no Evaluation and will be graded as objective.');
  return out;
}

export function uploadReport(r, dryRun) {
  if (!r) return null;
  if (dryRun) {
    return {
      saved: false,
      lines: [plural(Number(r.cases) || 0, 'case read', 'cases read') + (r.sheet_name ? ' from the sheet "' + r.sheet_name + '"' : '') +
        (nil(r.header_row) ? '' : ', header on row ' + r.header_row) + '. Nothing is saved yet.'].concat(flagLines(r.flags)),
      preview: (r.preview || []).slice(0, 20).map((p) => ({ key: p.case_key, question: p.question || '—',
        evaluation: p.evaluation === 'judgment' ? 'Judgment' : 'Objective' }))
    };
  }
  const set = r.set || {};
  const d = r.diff || {};
  const diff = nil(d.previous_version) ? ''
    : 'Against version ' + d.previous_version + ': ' + (d.added || []).length + ' added, ' + (d.changed || []).length + ' changed, ' + (d.removed || []).length + ' removed.';
  return { saved: true, preview: [],
    lines: ['Saved as version ' + set.version + ' with ' + plural(Number(set.case_count) || 0, 'case', 'cases') + '.', diff].concat(flagLines(r.flags)).filter(Boolean) };
}

const refusalNotice = (part) => (part && PAGE_REASONS.indexOf(part.reason) > -1 ? noticeFor(part.reason, part.error) : '');

// ── the Golden set tab ────────────────────────────────────────────────────────────────────────────────────────
export function goldenSetView(s, now) {
  const g = s.golden;
  const data = g.sets.data;
  const versions = (data && data.versions) || [];
  const active = data && data.active;
  const cd = g.cases.data;
  const total = (cd && cd.total) || 0;
  const page = g.cases.page || 0;
  const up = g.upload;
  const notice = refusalNotice(g.sets);
  return {
    notice, loading: !!g.sets.loading && !g.sets.loadedAt, error: notice ? '' : (g.sets.error || ''), actionError: g.setError || '',
    empty: !!g.sets.loadedAt && !versions.length && !g.sets.error,
    active: active ? { id: active.id, version: active.version, name: active.name || '', cases: Number(active.case_count) || 0,
      stale: Number(active.stale_count) || 0, unverified: Number(active.unverified_count) || 0, by: active.created_by_email || '—',
      when: fmtWhen(active.created_at, now), file: active.source_filename || '—' } : null,
    versions: versions.map((v) => ({
      id: v.id, label: 'Version ' + v.version, name: v.name || '', status: v.status === 'active' ? 'Active' : 'Retired',
      cases: plural(Number(v.case_count) || 0, 'case', 'cases'), when: fmtWhen(v.created_at, now), by: v.created_by_email || '—',
      file: v.source_filename || '—', on: v.id === g.setId, busy: !!g.setBusy && g.setBusy.slice(g.setBusy.indexOf(':') + 1) === v.id,
      canActivate: v.status !== 'active', canRetire: v.status === 'active'
    })),
    upload: {
      busy: !!(up && up.busy), fileName: (up && up.fileName) || '', dryRun: !!(up && up.dryRun), error: (up && up.error) || '',
      errors: issueLines(up && up.errors),
      warnings: issueLines((up && up.report && up.report.warnings) || (up && up.warnings)).slice(0, 50),
      report: up && up.report ? uploadReport(up.report, up.dryRun) : null
    },
    cases: {
      loading: !!g.cases.loading && !cd, error: g.cases.error || '',
      rows: ((cd && cd.cases) || []).map((r) => {
        const shown = !!g.shown[r.id];
        return {
          id: r.id, key: r.case_key, question: r.question || '—', followups: (r.followups || []).length,
          evaluation: r.evaluation === 'judgment' ? 'Judgment' : 'Objective', verified: r.verified_on || 'Not verified',
          stale: !!r.stale, requirements: (r.requirement_ids || []).join(', ') || '—',
          writeIntent: (r.flags || []).indexOf('write_intent') > -1, shown,
          expected: shown ? (r.expected_answer || '—') : null,
          values: shown ? (r.expected_values || []).map((v) => (v && v.raw) || String((v && v.value) || '')).filter(Boolean) : null
        };
      }),
      total, totalLabel: plural(total, 'case', 'cases'),
      pageLabel: 'Page ' + (page + 1) + ' of ' + Math.max(1, Math.ceil(total / CASES_PAGE)),
      canPrev: page > 0, canNext: (page + 1) * CASES_PAGE < total, draft: g.casesDraft || '',
      flags: CASE_FLAGS.map(([key, label]) => ({ key, label, on: (g.cases.flag || '') === key }))
    }
  };
}

// ── golden runs and the start dialog ──────────────────────────────────────────────────────────────────────────
export function dialogView(s) {
  const g = s.golden;
  const d = g.dialog;
  if (!d) return null;
  const est = d.estimate;
  const pre = est && est.preflight;
  const maxK = Math.min(HARD_MAX_K, Number(est && est.max_k) || HARD_MAX_K);
  const versions = (g.sets.data && g.sets.data.versions) || [];
  const blocked = !est || !!(pre && pre.ok === false) || !!est.building_selected || Number(est.cases) === 0;
  return {
    ks: KS.filter((k) => k <= maxK).map((k) => ({ k, on: Number(d.k) === k })),
    purposes: ['adhoc', 'baseline'].map((p) => ({ key: p, label: PURPOSE_LABELS[p], on: d.purpose === p })),
    sets: versions.map((v) => ({ value: v.id, label: 'Version ' + v.version + (v.status === 'active' ? ' (active)' : '') })),
    setId: d.setId || '', note: d.note || '', usdCap: d.usdCap || '',
    estimating: !!d.estimating, lines: estimateLines(est), problems: (pre && pre.problems) || [],
    building: est && est.building_selected ? BUILDING_NOTE : '',
    noCases: !!est && Number(est.cases) === 0 ? 'The chosen version has no cases to ask.' : '',
    error: d.error || '', errorProblems: d.problems || [], starting: !!d.starting,
    canStart: !blocked && !d.starting && !d.estimating
  };
}

export function goldenRunsView(s, now) {
  const g = s.golden;
  const rows = g.runs.rows || [];
  const notice = refusalNotice(g.runs);
  return {
    notice, loading: !!g.runs.loading && !g.runs.loadedAt, error: notice ? '' : (g.runs.error || ''),
    empty: !!g.runs.loadedAt && !rows.length && !g.runs.error,
    rows: rows.map((r) => Object.assign(runRow(r, now, g.own), { picked: g.pick.indexOf(r.id) > -1, open: g.detail.runId === r.id })),
    canCompare: g.pick.length === 2, pickNote: g.pick.length === 1 ? 'Tick one more run to compare.' : '',
    dialog: dialogView(s)
  };
}

function repView(r) {
  const outcome = r.blind ? 'Hidden until revealed' : (OUTCOME_LABELS[r.outcome] || (r.state === 'in_flight' ? 'Answering…' : 'Not started'));
  return {
    rep: r.rep, outcome, tone: r.blind ? '' : (r.outcome === 'pass' ? 'pass' : r.outcome === 'fail' ? 'fail' : ''),
    cost: nil(r.usd) ? '—' : usd(r.usd), time: nil(r.ms) ? '—' : fmtMs(r.ms),
    refused: r.refused_count ? plural(r.refused_count, 'step refused', 'steps refused') : '',
    sessionId: r.session_id || null, turnId: r.turn_id || null
  };
}

function caseView(cs) {
  return {
    key: cs.case_key, question: cs.question || '—', evaluation: cs.evaluation === 'judgment' ? 'Judgment' : 'Objective',
    stale: !!cs.stale, tally: nil(cs.graded) || !cs.graded ? '' : cs.passes + '/' + cs.graded + ' pass',
    reps: (cs.reps || []).map(repView)
  };
}

export function runDetailView(s, now) {
  const g = s.golden;
  const id = g.detail.runId;
  if (!id) return null;
  const d = g.detail.data;
  const run = d && d.run;
  if (!run || run.id !== id) return { loading: !g.detail.error, error: g.detail.error || '', run: null };
  const row = runRow(run, now, g.own);
  const tiles = [
    { label: 'Pass rate (fresh cases)', value: fmtRate(run.pass_rate), title: rateTitle(run.pass_rate) },
    { label: 'Including stale cases', value: fmtRate(run.pass_rate_incl_stale), title: rateTitle(run.pass_rate_incl_stale) },
    { label: 'Cases passing every rep', value: fmtRate(run.case_pass_rate), title: rateTitle(run.case_pass_rate) }
  ];
  if (Number(run.k) > 1) {
    tiles.push({ label: 'pass^' + run.k + ' (every rep passes)', value: pct(run.pass_hat_k), title: '' });
    tiles.push({ label: 'pass@' + run.k + ' (any rep passes)', value: pct(run.pass_at_k), title: '' });
  }
  tiles.push({ label: 'Spent', value: usd(run.usd_spent) + ' of ' + usd(run.usd_cap), title: nil(run.estimate_usd) ? '' : 'Estimate ' + usd(run.estimate_usd) });
  const cur = run.current;
  const loopErr = g.loop.error;
  const params = run.params && typeof run.params === 'object' ? run.params : {};
  return {
    loading: false, error: g.detail.error || '',
    run: { id: run.id, label: row.label, status: row.status, purpose: row.purpose, k: row.k, set: row.set, who: row.who, when: row.when,
      note: run.note || '', error: run.error || '', mine: row.mine },
    banner: row.running ? (row.mine ? 'Keep this page open while the run is going. Closing it pauses the run; Resume carries on.'
      : 'The page of the person who started this run keeps it going.') : '',
    progress: (run.items_done || 0) + ' of ' + (run.items_total || 0) + ' answered' +
      (cur && cur.case_key ? ' · now case ' + cur.case_key + (Number(run.k) > 1 && cur.rep ? ', rep ' + cur.rep : '') : ''),
    tiles,
    outcomes: Object.keys(run.outcomes || {}).filter((k) => run.outcomes[k]).map((k) => (OUTCOME_LABELS[k] || k) + ': ' + run.outcomes[k]),
    graderNote: run.grader_validated === false ? 'Judgment cases read as unvalidated until the grader agrees with people often enough.' : '',
    params: Object.keys(params).filter((k) => !nil(params[k]) && typeof params[k] !== 'object').map((k) => ({ label: paramLabel(k), value: String(params[k]).slice(0, 60) })),
    actions: {
      canPause: row.running, canResume: row.resumable, canCancel: ['queued', 'running', 'paused', 'interrupted', 'capped'].indexOf(run.status) > -1,
      canRegrade: ['done', 'capped', 'paused', 'interrupted', 'failed', 'cancelled'].indexOf(run.status) > -1,
      busy: g.action.busy || '', error: g.action.error || '', resumeCap: g.resumeCap || ''
    },
    loop: {
      stepping: g.loop.runId === id, note: g.loop.runId === id ? (g.loop.note || '') : '',
      error: loopErr ? loopErr.message || '' : '', hint: loopErr && loopErr.reason === 'building_selected' ? BUILDING_NOTE : '',
      resumable: !!(loopErr && loopErr.resumable)
    },
    cases: (d.cases || []).map(caseView)
  };
}

// ── two runs compared ─────────────────────────────────────────────────────────────────────────────────────────
function kValue(x) {
  return !x || nil(x.value) ? '—' : pct(x.value) + ' (k=' + x.k + ')';
}

export function compareView(s) {
  const x = s.golden.compare;
  if (!x) return null;
  const runs = s.golden.runs.rows || [];
  const label = (id) => { const r = runs.find((y) => y.id === id); return r ? r.label || '—' : '—'; };
  const base = { loading: !!x.loading, error: x.error || '', base: label(x.base), head: label(x.head) };
  const out = x.data;
  if (!out) return base;
  const flips = [];
  ['regressed', 'fixed', 'still_failing', 'now_measured', 'not_measured'].forEach((kind) => ((out.flips || {})[kind] || []).forEach((f) => {
    const first = f.head && Array.isArray(f.head.results) ? f.head.results[0] : null;
    flips.push({ kind, label: FLIP_LABELS[kind], danger: kind === 'regressed', key: f.case_key, question: f.question || '—',
      sessionId: (first && first.session_id) || null, turnId: (first && first.turn_id) || null });
  }));
  const pr = out.pass_rate || {};
  const rates = [{ label: 'Pass rate (fresh cases)', base: fmtRate(pr.base), head: fmtRate(pr.head), baseTitle: rateTitle(pr.base),
    headTitle: rateTitle(pr.head), delta: signedPts(pr.delta) }];
  const phk = out.pass_hat_k;
  if (phk && ((phk.base && phk.base.k > 1) || (phk.head && phk.head.k > 1))) {
    rates.push({ label: 'pass^k (every rep passes)', base: kValue(phk.base), head: kValue(phk.head), baseTitle: '', headTitle: '',
      delta: phk.k_differs ? 'different k' : signedPts(nil(phk.head && phk.head.value) || nil(phk.base && phk.base.value) ? null : phk.head.value - phk.base.value) });
  }
  const cost = out.cost || {};
  rates.push({ label: 'Cost', base: usd(cost.base_usd), head: usd(cost.head_usd), baseTitle: '', headTitle: '', delta: signedUsd(cost.delta_usd) });
  const diff = out.params_diff || {};
  return Object.assign(base, {
    comparable: out.comparable !== false, warnings: out.warnings || [], rates, flips,
    stillPassing: Number(out.still_passing) || 0,
    differs: Object.keys(diff).map((k) => ({ label: paramLabel(k), base: nil(diff[k][0]) ? '—' : String(diff[k][0]).slice(0, 40),
      head: nil(diff[k][1]) ? '—' : String(diff[k][1]).slice(0, 40) }))
  });
}

// ── the expected outcome of a golden answer (Conversations) ───────────────────────────────────────────────────
// Null unless the open conversation is a golden one. Closed (nothing of the expected answer or the grade in the
// view) until the reviewer reveals it; the service records the reveal.
export function expectedView(s) {
  const conv = s.conv && s.conv.data;
  if (!conv || conv.key !== s.convKey || !isGoldenSession(conv.session_id)) return null;
  const turnId = s.turnId || (conv.active && conv.active.id) || null;
  if (!turnId) return null;
  const x = s.golden.expected[turnId] || null;
  const p = x && x.data;
  const gd = (p && p.golden) || {};
  const revealed = !!(p && p.revealed);
  const view = {
    turnId, loading: !!(x && x.loading), revealing: !!(x && x.revealing), error: (x && x.error) || '',
    revealed, open: revealed && !(x && x.hidden), unavailable: p && p.available === false ? 'Expected outcome: unavailable for this answer.' : '',
    run: gd.run && gd.run.label ? gd.run.label : '', caseKey: gd.case_key || '', rep: gd.rep || null
  };
  if (!view.open) return view;
  const cs = gd.case || {};
  const res = gd.result || {};
  const grade = res.grade || {};
  const vc = grade.value_check && Array.isArray(grade.value_check.items) ? grade.value_check.items : [];
  return Object.assign(view, {
    finalNote: gd.is_final === false && !nil(gd.msg_idx) ? 'This case is graded on its last answer: this is answer ' + (gd.msg_idx + 1) + ' of ' + (gd.messages_total || '—') + '.' : '',
    question: cs.question || '', followups: cs.followups || [], expected: cs.expected_answer || '—',
    values: (cs.expected_values || []).map((v) => (v && v.raw) || String((v && v.value) || '')).filter(Boolean),
    evaluation: cs.evaluation === 'judgment' ? 'Judgment' : 'Objective', source: cs.source || '—',
    verified: cs.verified_on || 'Not verified', stale: !!cs.stale, requirements: (cs.requirement_ids || []).join(', '),
    outcome: OUTCOME_LABELS[res.outcome] || '—', critique: grade.critique ? String(grade.critique) : '',
    unvalidated: cs.evaluation === 'judgment' && grade.validated === false ? 'The grader is not yet validated for judgment cases: read this verdict as a suggestion.' : '',
    checks: vc.map((it) => (it.ok === true ? '✓ ' : it.ok === false ? '✗ ' : '? ') + (it.raw || '') + (it.ok === false && it.nearest ? ' (closest: ' + it.nearest + ')' : '')),
    refused: Array.isArray(res.refused) ? res.refused : [],
    refusedLine: Array.isArray(res.refused) && res.refused.length ? plural(res.refused.length, 'step that would have changed data was', 'steps that would have changed data were') + ' refused, so this answer reads as degraded.' : ''
  });
}
