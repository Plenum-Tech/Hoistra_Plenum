// proposals — pure helpers and view models for Trace Lab's AI suggestion queue (bulk approval with its audit and
// spot-check) and for the platform team's proposal runs. Nothing in a bulk queue starts ticked, ticks freeze while an
// audit is open, and the spot-check shows each answer with the AI's suggestion hidden — in the queue too. A run
// starts only after an estimate for the same form shows a known price and a cap that covers the cost. No requests and
// no state of its own: the screens draw taxonomyProposalsVals(s, a), and its buttons call taxonomyActions.js.
import { SAME_FIX_TEXT, clip, has, hasCompany, metaOf, owns, taxOf, usd, val, when } from './taxCommon.js';

export const QUEUE_PAGE = 50;
export const QUEUE_KINDS = [{ key: 'depth_scan', label: 'Depth-scan suggestions' }, { key: 'cell', label: 'Cell suggestions' },
  { key: 'group', label: 'Grouping drafts', owner: true }];
export const RULE_TITLES = { 1: 'Only open suggestions you can see', 2: 'The evidence repeats', 3: 'A reviewer’s note describes this failure',
  4: 'The present rate does not match the fair batches', 5: 'The known-answer check did not pass', 6: 'Your spot-check disagreed',
  7: 'Too quick, or the list is out of date', 8: 'Judgments still missing', 9: 'Changed since you loaded it' };
export const RUN_KIND_TEXT = { grouping: 'Group the unfiled notes', depth_scan: 'Find more answers like one failure mode',
  cells: 'Suggest present or absent for missing judgments' };
export const ACTIVE = ['queued', 'running'];
export const RESUMABLE = ['paused', 'interrupted', 'capped'];
export const RUN_DAYS = [7, 30, 60];
export const RUNS_POLL_MS = 5000;
export const RUN_FORM = { kind: 'depth_scan', mode_id: '', days: 30, usd_cap: '', limit: 200, include_candidates: false };
const JOB_STATUS_TEXT = { queued: 'Waiting', running: 'Running', paused: 'Paused', done: 'Done', failed: 'Failed',
  capped: 'Stopped at its cost cap', interrupted: 'Interrupted: resume it', cancelled: 'Cancelled' };
const SKIP_TEXT = { stale: 'changed since you loaded them', not_pending: 'already decided', flagged: 'a reviewer’s note describes the failure',
  refused_rule_4: 'the present rate did not match the fair batches' };

export const checkable = (p) => !!p && (p.kind === 'depth_scan' || p.kind === 'cell');
export const runForm = (t) => Object.assign({}, RUN_FORM, t.tlProposalsRunForm || {});
export const formKey = (f) => [f.kind, f.kind === 'depth_scan' ? f.mode_id : '', f.days, f.limit, !!f.include_candidates].join('|');

// The selection a refetch keeps: the selected ids still on the page, in their old order. Nothing is ever added here.
export function keepSelected(selected, rows) {
  const on = new Set((rows || []).map((r) => r.id));
  return (selected || []).filter((id) => on.has(id));
}

// The audit's answer in words, before anything is approved.
export function checkFindings(chk) {
  if (!chk) return null;
  const wait = (chk.refused || []).filter((r) => r.rule === 7 && r.retry_after_s).map((r) => Number(r.retry_after_s));
  return {
    allowed: !!chk.allowed,
    refused: (chk.refused || []).map((r) => ({ rule: r.rule, title: RULE_TITLES[r.rule] || 'Refused', detail: r.detail || '' })),
    flagged: (chk.flagged || []).map((x) => ({ id: x.id, detail: x.detail || '' })),
    warnings: (chk.warnings || []).map((x) => x.detail || ''),
    retryAfter: wait.length ? Math.max(...wait) : 0,
    seed: (chk.spot_check && chk.spot_check.seed) || '',
    spot: (chk.spot_check && chk.spot_check.items) || []
  };
}

// Why Start stays off, or '' when the estimate on screen allows the run. The server checks every cap again.
export function startProblem(f, est) {
  const cap = Number(f.usd_cap);
  if (!est || est.form !== formKey(f)) return 'Estimate the cost first.';
  if (!est.price_known) return 'The model’s price is not known, so the run’s spending cannot be capped.';
  if (!(Number(est.items) > 0)) return 'There is nothing for this run to read.';
  if (!(cap > 0)) return 'Set a cost cap in dollars.';
  if (est.run_cap_max !== undefined && est.run_cap_max !== null && cap > Number(est.run_cap_max)) return 'That cap is above the largest allowed for one run (' + usd(est.run_cap_max) + ').';
  if (Number(est.expected_usd) > cap) return 'The estimate is above the cap you set.';
  if (est.month_cap !== undefined && est.month_cap !== null && Number(est.month_spent) + cap > Number(est.month_cap)) {
    return 'This month’s spending on AI proposals would pass its cap (' + usd(est.month_spent) + ' of ' + usd(est.month_cap) + ' spent).';
  }
  return '';
}

function estimateText(e) {
  if (!e.price_known) return 'The model’s price is not known, so the run’s spending cannot be capped.';
  return e.items + (e.items === 1 ? ' item' : ' items') + ' to read' + (e.canaries ? ', plus ' + e.canaries + ' known-answer checks' : '')
    + '. Expected ' + usd(e.expected_usd) + ', at most ' + usd(e.worst_usd) + '. This month: ' + usd(e.month_spent) + ' of '
    + usd(e.month_cap) + ' spent on AI proposals; one run may cap at ' + usd(e.run_cap_max) + '.';
}

// The edited draft of a grouping suggestion that proposes a new failure mode, as the server reads it.
export function draftEdits(p, edits) {
  const d = Object.assign({}, p.draft || {}, edits || {});
  return { name: String(d.name || ''), definition: String(d.definition || ''), boundary: String(d.boundary || ''),
    nearest_mode_id: d.nearest_mode_id || null, level: d.level || '', agent: d.agent || '',
    evaluator_type: d.evaluator_type || null, requirement_ids: Array.isArray(d.requirement_ids) ? d.requirement_ids : [] };
}

function proposalRow(a, t, p, ctx) {
  const hidden = ctx.spotIds.has(p.id);
  const md = ctx.defs[p.mode_id] || null;
  const w = ctx.writes['proposal:' + p.id] || {};
  const base = {
    id: p.id, kind: p.kind, question: clip(p.question || (p.kind === 'group' ? 'Reviewers’ notes' : 'Answer'), 120),
    modeName: p.mode_name || (md && md.name) || '', definition: p.mode_definition || (md && md.definition) || '',
    checkable: checkable(p), checked: ctx.selected.indexOf(p.id) > -1, toggle: () => a.proposalsToggle(p.id), locked: ctx.locked,
    busy: !!w.busy, error: w.error || '', hiddenForSpot: hidden,
    open: ctx.company && p.conversation_key && p.turn_id ? () => a.taxOpenAnswer(p.turn_id, p.conversation_key) : null
  };
  if (p.kind === 'group') {
    const draft = Object.assign({}, p.draft || {}, (t.tlProposalsDrafts || {})[p.id] || {});
    return Object.assign(base, {
      suggestion: p.suggestion === 'file_under' ? 'File under ' + (p.mode_name || 'an existing failure mode')
        : p.suggestion === 'new_mode' ? 'New failure mode: ' + (draft.name || 'unnamed') : 'No failure mode fits',
      evidence: p.evidence || '', quote: '',
      notes: (p.notes || []).map((n) => ({ id: n.id, note: n.note || '', quote: clip(n.quote, 120), step: n.step_label || 'Answer', who: n.reviewer_email || 'A reviewer' })),
      draft: p.suggestion === 'new_mode' ? { name: draft.name || '', definition: draft.definition || '', boundary: draft.boundary || '',
        level: draft.level || '', agent: draft.agent || '', levels: ctx.levels, agents: ctx.agents,
        set: (field) => (e) => a.proposalsEditDraft(p.id, { [field]: val(e) }) } : null,
      accept: ctx.owner ? () => a.proposalsDecide(p.id, 'accept') : null, acceptLabel: 'Accept',
      other: ctx.owner ? () => a.proposalsDecide(p.id, 'dismiss') : null, otherLabel: 'Dismiss', otherHint: 'Dismiss files nothing: the notes stay unfiled.'
    });
  }
  const v = p.kind === 'depth_scan' || p.suggestion === 'present' ? 'present' : 'absent';
  return Object.assign(base, {
    suggestion: hidden ? 'Hidden while you do the spot-check' : 'AI suggests: ' + v,
    evidence: hidden ? '' : p.evidence || '', quote: hidden ? '' : clip(p.quote || '', 200), notes: [], draft: null,
    accept: () => a.proposalsDecide(p.id, 'accept'),
    acceptLabel: p.kind === 'depth_scan' ? 'Accept: it shows this' : 'Accept: ' + v,
    other: p.kind === 'depth_scan' ? () => a.proposalsDecide(p.id, 'dismiss') : () => a.proposalsDecide(p.id, 'override', v !== 'present'),
    otherLabel: p.kind === 'depth_scan' ? 'Dismiss: it does not' : 'Mark ' + (v === 'present' ? 'absent' : 'present'),
    otherHint: p.kind === 'depth_scan' ? 'Dismissing records this answer as a close example where the failure is absent.' : ''
  });
}

// The queue and (for the platform team) the proposal runs: `s` is the store state, `a` the actions the buttons call.
export function taxonomyProposalsVals(s, a) {
  const t = taxOf(s);
  const owner = owns(s);
  const kind = t.tlProposalsKind || 'depth_scan';
  const rows = t.tlProposals || [];
  const selected = t.tlProposalsSelected || [];
  const chk = checkFindings(t.tlProposalsCheck);
  const writes = t.tlWrites;
  const defs = {};
  (t.tlModes || []).forEach((m) => { defs[m.id] = m; });
  const meta = t.tlModesMeta || metaOf(null);
  const total = t.tlProposalsTotal || 0;
  const offset = t.tlProposalsOffset || 0;
  const res = t.tlProposalsResult;
  const ctx = { selected, spotIds: new Set(chk ? chk.spot.map((i) => i.proposal_id) : []), defs, writes, owner, company: hasCompany(s),
    locked: !!chk, levels: meta.levels.map((l) => ({ value: l.key, label: l.label })), agents: meta.agents.map((x) => ({ value: x.key, label: x.label })) };
  const spotDone = chk ? chk.spot.every((i) => has(t.tlProposalsSpot, i.proposal_id)) : false;
  const f = runForm(t);
  const est = t.tlProposalsEstimate;
  const fresh = est && est.form === formKey(f) ? est : null;
  const skipped = res ? res.skipped || [] : [];
  const reasons = {};
  skipped.forEach((x) => { reasons[x.reason] = (reasons[x.reason] || 0) + 1; });
  return {
    tlTaxQueue: {
      kinds: QUEUE_KINDS.filter((k) => !k.owner || owner).map((k) => ({ key: k.key, label: k.label, on: k.key === kind, pick: () => a.proposalsSetKind(k.key) })),
      kind, reload: () => a.proposalsLoad(), loading: !!t.tlProposalsLoading && !t.tlProposalsLoadedAt, error: t.tlProposalsErr || '',
      empty: !!t.tlProposalsLoadedAt && !rows.length, emptyText: kind === 'group' ? 'No grouping drafts waiting.' : 'No suggestions waiting.',
      hiddenNote: t.tlProposalsHidden ? t.tlProposalsHidden + ' suggestions on answers you have not judged stay hidden.' : '',
      groupingNote: meta.groupingNote, fixTest: meta.fixTest, fixTestText: meta.fixTestText, guidance: SAME_FIX_TEXT,
      untickedNote: 'Nothing is ticked for you. Tick only what you have read.',
      bulk: kind !== 'group', selectedCount: selected.length, locked: !!chk,
      selectPage: () => a.proposalsSelectPage(), clearSelection: () => a.proposalsClearSelection(),
      acceptSelected: () => a.proposalsBulkCheck('accept'),
      dismissSelected: kind === 'depth_scan' ? () => a.proposalsBulkCheck('dismiss') : null,
      checking: !!(writes['bulk-check'] || {}).busy, checkError: (writes['bulk-check'] || {}).error || '',
      rows: rows.map((p) => proposalRow(a, t, p, ctx)),
      pager: { text: total ? 'Suggestions ' + (offset + 1) + '–' + Math.min(offset + QUEUE_PAGE, total) + ' of ' + total : '',
        canPrev: offset > 0 && !chk, canNext: offset + QUEUE_PAGE < total && !chk, prev: () => a.proposalsPage(-1), next: () => a.proposalsPage(1) },
      check: chk ? Object.assign({}, chk, {
        action: t.tlProposalsCheckAction, count: (t.tlProposalsCheckIds || []).length,
        heading: (t.tlProposalsCheckAction === 'dismiss' ? 'Dismiss ' : 'Accept ') + (t.tlProposalsCheckIds || []).length + ' suggestions: what the checks found',
        spot: chk.spot.map((i) => {
          const md = defs[i.mode_id] || null;
          const w = writes['spot:' + i.proposal_id] || {};
          return { id: i.proposal_id, question: i.question || 'Answer', modeName: i.mode_name || (md && md.name) || 'This failure mode',
            definition: md ? md.definition || '' : '', decided: has(t.tlProposalsSpot, i.proposal_id) ? (t.tlProposalsSpot[i.proposal_id] ? 'Present' : 'Absent') : '',
            busy: !!w.busy, error: w.error || '',
            present: () => a.proposalsSpotDecide(i.proposal_id, true), absent: () => a.proposalsSpotDecide(i.proposal_id, false) };
        }),
        spotText: chk.spot.length ? 'Spot-check: decide these ' + chk.spot.length + ' answers yourself first. The AI’s suggestion is hidden.' : '',
        spotDone, canApprove: chk.allowed && spotDone && !(writes['bulk-approve'] || {}).busy,
        approve: () => a.proposalsBulkApprove(), approving: !!(writes['bulk-approve'] || {}).busy, approveError: (writes['bulk-approve'] || {}).error || '',
        approveLabel: t.tlProposalsCheck.stats && t.tlProposalsCheck.stats.write !== undefined ? 'Approve ' + t.tlProposalsCheck.stats.write : 'Approve',
        recheck: () => a.proposalsRecheck(), cancel: () => a.proposalsBulkCancel(),
        waitText: chk.retryAfter ? 'Read the suggestions first: check again in ' + chk.retryAfter + ' seconds.' : ''
      }) : null,
      result: res ? { text: 'Approved ' + (res.approved || []).length + '.' + (skipped.length ? ' Skipped ' + skipped.length + ': '
        + Object.keys(reasons).map((k) => reasons[k] + ' ' + (SKIP_TEXT[k] || k)).join(', ') + '.' : ''), dismiss: () => a.proposalsClearResult() } : null
    },
    tlTaxRuns: owner ? {
      reload: () => a.proposalsRunsLoad(), loading: !!t.tlProposalsRunsLoading && !t.tlProposalsRunsLoadedAt, error: t.tlProposalsRunsErr || '',
      empty: !!t.tlProposalsRunsLoadedAt && !(t.tlProposalsRuns || []).length, emptyText: 'No AI proposal runs yet.',
      list: (t.tlProposalsRuns || []).map((r) => {
        const j = r.job || {};
        const w = writes['run:' + r.id] || {};
        const md = r.mode_id ? defs[r.mode_id] : null;
        return { id: r.id, kindText: RUN_KIND_TEXT[r.kind] || r.kind, modeName: md ? md.name : '', statusText: JOB_STATUS_TEXT[j.status] || j.status || '—',
          progress: (j.done === null || j.done === undefined ? '—' : j.done) + ' of ' + (j.total === null || j.total === undefined ? '—' : j.total),
          spend: usd(j.usd_spent) + ' of ' + usd(j.usd_cap), estimate: usd(r.estimate_usd),
          canary: r.canary_total ? r.canary_agree + ' of ' + r.canary_total + ' known answers agreed' : '—',
          when: when(r.created_at), who: r.created_by_email || '', error: w.error || j.error || '', busy: !!w.busy,
          pause: ACTIVE.indexOf(j.status) > -1 ? () => a.proposalsPauseRun(r.id) : null,
          resume: RESUMABLE.indexOf(j.status) > -1 ? () => a.proposalsResumeRun(r.id) : null,
          resumeCap: (t.tlProposalsResumeCap || {})[r.id] || '', setResumeCap: (e) => a.proposalsSetResumeCap(r.id, val(e)) };
      }),
      form: { kind: f.kind, kinds: Object.keys(RUN_KIND_TEXT).map((k) => ({ value: k, label: RUN_KIND_TEXT[k] })), modeId: f.mode_id,
        modes: [{ value: '', label: 'Choose…' }].concat((t.tlModes || []).filter((m) => m.status !== 'retired').map((m) => ({ value: m.id, label: m.name }))),
        days: String(f.days), dayOptions: RUN_DAYS.map((d) => ({ value: String(d), label: 'Last ' + d + ' days' })),
        cap: String(f.usd_cap === undefined || f.usd_cap === null ? '' : f.usd_cap), limit: String(f.limit), includeCandidates: !!f.include_candidates,
        showMode: f.kind === 'depth_scan', showWindow: f.kind !== 'grouping', showCells: f.kind === 'cells',
        set: (field) => (e) => a.proposalsSetRun({ [field]: val(e) }) },
      estimate: fresh ? { text: estimateText(fresh), priceKnown: !!fresh.price_known } : null,
      estimating: !!t.tlProposalsEstimating, estimateError: t.tlProposalsEstimateErr || '', runEstimate: () => a.proposalsEstimateRun(),
      startProblem: startProblem(f, est), start: () => a.proposalsStartRun(),
      starting: !!(writes['run-start'] || {}).busy, startError: (writes['run-start'] || {}).error || ''
    } : null
  };
}
