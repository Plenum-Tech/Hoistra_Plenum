// sessions — the logic of the eval store's pages (11 Nov 2026, task plan Workstream B). Pure: no React, no requests.
//
// The words are Langfuse's (B1.3): a Session is one conversation, a Trace is one turn of it, a Span or a Generation is
// one step of a trace, a Score is a verdict on a trace. A scenario's expected outcome is written before it is asked;
// these helpers only turn what the service sends into what a person reads.

export const LEVELS = ['logic', 'ux', 'domain'];
export const LEVEL_TEXT = Object.freeze({ logic: 'Logic', ux: 'User experience', domain: 'Domain' });
export const TYPE_TEXT = Object.freeze({ single_domain: 'Single domain', multi_domain: 'Multi-domain', multi_turn: 'Multi-turn', failure: 'Failure' });
export const TYPE_OPTIONS = [['', 'All types']].concat(Object.keys(TYPE_TEXT).map((k) => [k, TYPE_TEXT[k]]));
export const STATUS_TEXT = Object.freeze({ ok: 'OK', failed: 'Failed' });
export const STATUS_OPTIONS = [['', 'Any status'], ['ok', 'OK'], ['failed', 'Failed']];
export const TRACE_STATUS_TEXT = Object.freeze({ ok: 'Completed', error: 'Error', timeout: 'Timed out', interrupted: 'Interrupted' });
export const FORMAT_TEXT = Object.freeze({ success: 'Success', partial_success: 'Partial success', denial: 'Denial', failure: 'Failure' });
export const FAILURE_TEXT = Object.freeze({ partial_failure: 'Partial failure', total_failure: 'Total failure',
  authorization_failure: 'Authorization failure', evidence_conflict: 'Evidence conflict', zero_results: 'Zero results',
  not_recorded: 'Not recorded', unavailable: 'Unavailable', tool_failed: 'Tool failed' });
const VERDICT_TEXT = Object.freeze({ pass: 'Pass', fail: 'Fail', not_evaluated: 'Not evaluated' });

const plural = (n, one, many) => (Number(n) || 0) + ' ' + (Number(n) === 1 ? one : many);

export function verdictText(v) {
  return VERDICT_TEXT[v] || '—';
}

// The list's filters as the query the service takes. Empty values are left out; a level filters only with a verdict.
export function sessionQuery(filters) {
  const f = filters || {};
  const q = {};
  if (f.runTag) q.run_tag = f.runTag;
  if (f.type) q.type = f.type;
  if (f.status) q.status = f.status;
  if (f.level && f.verdict) { q.level = f.level; q.verdict = f.verdict; }
  if (f.needsSme) q.needs_sme = 'true';
  if (f.stale) q.stale = 'true';
  if (f.allAttempts) q.all_attempts = 'true';
  return q;
}

// A session's traces as tabs, in the order the turns were asked.
export function turnTabs(traces) {
  return (Array.isArray(traces) ? traces.slice() : [])
    .sort((a, b) => (Number(a.turn_index) || 0) - (Number(b.turn_index) || 0))
    .map((t) => ({ key: 'turn-' + t.turn_index, label: 'Turn ' + t.turn_index, traceId: t.id, status: t.status || 'ok' }));
}

const at = (row) => Date.parse((row && row.created_at) || '') || 0;

// The current score at each level from a trace's score rows: the newest one a person gave, else the newest automatic one.
export function scoreSummary(scores) {
  const rows = Array.isArray(scores) ? scores : [];
  const out = {};
  LEVELS.forEach((level) => {
    const mine = rows.filter((r) => r && r.name === level);
    const newest = (list) => list.slice().sort((a, b) => at(b) - at(a))[0] || null;
    const pick = newest(mine.filter((r) => r.source === 'ANNOTATION')) || newest(mine.filter((r) => r.source === 'EVAL'));
    out[level] = pick ? { verdict: pick.string_value, value: pick.value == null ? null : Number(pick.value), source: pick.source,
      comment: pick.comment || '', author: pick.author_email || '', evaluator: pick.evaluator || '', details: pick.details || {},
      at: pick.created_at || null } : null;
  });
  return out;
}

// One expected call as a line: "c2 · list_certificates (compliance) · after c1".
export function planLines(expected) {
  const plan = expected && Array.isArray(expected.plan) ? expected.plan : [];
  return plan.map((c) => {
    const what = c.tool && c.domain ? c.tool + ' (' + c.domain + ')' : (c.tool || c.domain || 'a call');
    const deps = Array.isArray(c.depends_on) && c.depends_on.length ? ' · after ' + c.depends_on.join(', ') : '';
    return c.call_id + ' · ' + what + deps;
  });
}

export function sessionTitle(session) {
  const s = session || {};
  return s.scenario_id || s.id || '—';
}

// One row of the sessions list, as it is read.
export function sessionRow(s) {
  const x = s || {};
  const scores = x.scores || {};
  return {
    id: x.id, title: sessionTitle(x), runTag: x.run_tag || '', origin: x.origin || '', attempt: Number(x.attempt) || 1,
    type: TYPE_TEXT[x.type] || '—', turns: plural(x.turns, 'turn', 'turns'),
    status: STATUS_TEXT[x.status] || '—', failed: x.status === 'failed', when: x.started_at || null,
    levels: LEVELS.map((key) => {
      const sc = scores[key] || null;
      return { key, name: LEVEL_TEXT[key], verdict: sc ? sc.verdict : null, text: verdictText(sc && sc.verdict), source: sc ? sc.source : null };
    })
  };
}

// ── scores as a person reads them ─────────────────────────────────────────────────────────────────────────

const CHECK_TEXT = Object.freeze({ completed: 'Completed', plan_coverage: 'Plan coverage', call_order: 'Call order',
  parallel: 'Parallel calls', scope: 'Scope', retries: 'Retries', context_passing: 'Context passing', evidence_state: 'Evidence state' });
const RESULT_TEXT = Object.freeze({ pass: 'pass', fail: 'fail', not_observable: 'could not be seen' });
const NOT_EVALUATED = Object.freeze({ no_scenario: 'this session is not linked to a scenario.',
  no_expected_for_turn: 'the scenario has no expected outcome for this turn.',
  no_domain_rules: 'this scenario has no domain rules to check against.' });
const DOMAIN_SCORERS = ['domain_expert', 'platform', 'consultant'];
const yesNo = (ok) => (ok ? 'yes' : 'no');

// The domain level is the subject-matter expert's: a domain expert, the platform team or the consultant.
export function canScoreDomain(me) {
  return !!(me && DOMAIN_SCORERS.includes(me.role));
}

// Why a score is what it is, as lines: each {text, tone} with tone 'ok' | 'bad' | 'quiet'.
export function scoreLines(level, score) {
  if (!score) return [];
  const d = score.details || {};
  if (score.verdict === 'not_evaluated') {
    return [{ text: 'Not evaluated: ' + (NOT_EVALUATED[d.reason] || 'there was nothing to check it against.'), tone: 'quiet' }];
  }
  const lines = [];
  if (level === 'logic') {
    (Array.isArray(d.checks) ? d.checks : []).forEach((c) => {
      if (!RESULT_TEXT[c.result]) return;                        // not_applicable: nothing to say
      const head = (CHECK_TEXT[c.key] || c.key) + ': ' + RESULT_TEXT[c.result];
      lines.push({ text: c.result === 'pass' || !c.note ? head : head + '. ' + c.note, tone: c.result === 'pass' ? 'ok' : c.result === 'fail' ? 'bad' : 'quiet' });
    });
  } else if (level === 'ux') {
    const c = d.criteria || {};
    const t = d.template || {};
    const missing = Array.isArray(d.missing) ? d.missing : [];
    const forbidden = Array.isArray(d.forbidden_present) ? d.forbidden_present : [];
    lines.push({ text: 'Concise: ' + yesNo(c.concise) + ' (' + (Number(d.answer_words) || 0) + ' words)', tone: c.concise ? 'ok' : 'bad' });
    lines.push({ text: 'Complete: ' + yesNo(c.complete) + (missing.length ? '. Missing: ' + missing.join(', ') : '')
      + (forbidden.length ? '. Must not include: ' + forbidden.join(', ') : ''), tone: c.complete ? 'ok' : 'bad' });
    lines.push({ text: 'Right template: ' + (c.template ? 'yes (' + (FORMAT_TEXT[t.found] || t.found || '—') + ')'
      : 'no (expected ' + (FORMAT_TEXT[t.expected] || t.expected || '—') + ', found ' + (FORMAT_TEXT[t.found] || 'none') + ')'), tone: c.template ? 'ok' : 'bad' });
    lines.push({ text: 'Clear wording on failures and denials: ' + yesNo(c.failure_wording), tone: c.failure_wording ? 'ok' : 'bad' });
  } else {
    (Array.isArray(d.violations) ? d.violations : []).forEach((v) => lines.push({ text: (v.rule || 'A rule') + ': ' + (v.why || ''), tone: 'bad' }));
    if (d.needs_sme && score.source !== 'ANNOTATION') lines.push({ text: 'Waiting for a domain expert to confirm.', tone: 'quiet' });
  }
  if (d.stale_expected) lines.push({ text: 'The expected outcome was out of date when this was scored.', tone: 'quiet' });
  return lines;
}

// ── two run tags side by side ─────────────────────────────────────────────────────────────────────────────

export const CHANGE_TEXT = Object.freeze({ fixed: 'Fixed', regressed: 'Regressed', same: 'Same', changed: 'Changed', new: 'New', missing: 'Not asked' });

export function compareView(out) {
  if (!out || !out.summary) return { summary: [], rows: [] };
  const s = out.summary;
  const n = (v) => Number(v) || 0;
  const summary = LEVELS.map((level) => {
    const c = s[level] || {};
    return LEVEL_TEXT[level] + ': ' + n(c.fixed) + ' fixed, ' + n(c.regressed) + ' regressed, ' + n(c.same) + ' the same'
      + (n(c.changed) ? ', ' + n(c.changed) + ' changed in another way' : '');
  });
  const words = s.answer_words || {};
  const count = s.sessions || {};
  const any = s.logic || {};
  summary.push('Median answer length: ' + n(words.base) + ' words in ' + out.base + ', ' + n(words.head) + ' words in ' + out.head);
  summary.push(n(count.base) + ' scenarios in ' + out.base + ', ' + n(count.head) + ' in ' + out.head + '; ' + n(any.new) + ' only in ' + out.head
    + ', ' + n(any.missing) + ' only in ' + out.base);
  const rows = (Array.isArray(out.rows) ? out.rows : []).map((r) => ({
    id: r.scenario_id, type: TYPE_TEXT[r.type] || '—',
    levels: LEVELS.map((key) => ({ key, name: LEVEL_TEXT[key], base: r.base ? verdictText(r.base.verdicts[key]) : '—',
      head: r.head ? verdictText(r.head.verdicts[key]) : '—', change: CHANGE_TEXT[(r.change || {})[key]] || '—', raw: (r.change || {})[key] })),
    words: { base: r.base ? n(r.base.answer_words) : null, head: r.head ? n(r.head.answer_words) : null },
    baseSession: r.base ? r.base.session_id : null, headSession: r.head ? r.head.session_id : null
  }));
  return { summary, rows };
}

// ── scenarios ─────────────────────────────────────────────────────────────────────────────────────────────

const SCENARIO_STATUS = Object.freeze({ draft: 'Draft', approved: 'Approved', retired: 'Retired' });

export function scenarioRow(s) {
  const x = s || {};
  const stale = Array.isArray(x.stale) ? x.stale : [];
  return { id: x.scenario_id, version: x.version, type: TYPE_TEXT[x.type] || '—', sections: Array.isArray(x.spec_sections) ? x.spec_sections : [],
    status: SCENARIO_STATUS[x.status] || '—', turns: plural(Array.isArray(x.turns) ? x.turns.length : 0, 'turn', 'turns'),
    stale: stale.length > 0, staleText: stale.length ? 'Out of date: ' + stale.join(', ') + ' changed' : '',
    opening: Array.isArray(x.turns) && x.turns[0] ? x.turns[0].message || '' : '' };
}

// Which levels a person may evaluate. The user-experience and domain judges call a model and cost money, so they are
// run by the platform team and the consultant; a company admin runs the logic checks, which cost nothing.
export function evaluateLevels(me) {
  return me && me.all_companies ? LEVELS.slice() : ['logic'];
}

// One pass of the page's evaluate run, folded into what it has done so far: how many scores were written, which
// traces to pass over from now on (their judge call failed, so the next pass moves on to the traces after them), how
// many scores are left that a pass could still write, and why to stop, if it should: done, capped (the run tag has
// used its spending limit) or stuck (a pass got nowhere, so the page never spins).
export function evaluateStep(state, out) {
  const s = state || {};
  const o = out || {};
  const sum = (m) => Object.values(m || {}).reduce((a, n) => a + (Number(n) || 0), 0);
  const scores = (Number(s.scores) || 0) + sum(o.scored) + sum(o.not_evaluated);
  const skip = Array.from(new Set((s.skip || []).concat(Array.isArray(o.failed_traces) ? o.failed_traces : [])));
  const left = Math.max(0, (Number(o.remaining) || 0) - (Number(o.left_for_later) || 0));
  const failed = Array.isArray(o.failed_traces) && o.failed_traces.length > 0;
  let stop = null;
  if (!left) stop = 'done';
  else if (o.capped) stop = 'capped';
  else if (!Number(o.traces) && !failed) stop = 'stuck';
  return { scores, skip, left, stop };
}

// What the page says about an evaluate run, while it runs and when it ends.
export function evaluateText(st) {
  const s = st || {};
  const skipped = (s.skip || []).length;
  if (s.stop === 'done') {
    return 'Done: ' + plural(s.scores, 'score', 'scores') + ' written under ' + s.tag + '.'
      + (skipped ? ' The judge could not score ' + plural(skipped, 'trace', 'traces') + '; evaluate again later to retry them.' : '');
  }
  if (s.stop === 'capped') {
    return 'Stopped: ' + s.tag + ' has used its spending limit for the model judges, with ' + plural(s.left, 'score', 'scores') + ' still to do.';
  }
  if (s.stop === 'stuck') {
    return 'Nothing more could be scored; ' + plural(s.left, 'score is', 'scores are') + ' still to do. Try again later.';
  }
  return 'Scored ' + (Number(s.scores) || 0) + ' so far; ' + (Number(s.left) || 0) + ' to go.';
}

// What reading recorded conversations in did: what is new, what waits for its scenario, what was not read because its
// session id is another company's session, and what failed.
export function ingestText(out) {
  const o = out || {};
  const n = (key) => Number(o[key]) || 0;
  const parts = [n('sessions') || n('traces')
    ? 'Read in ' + plural(n('sessions'), 'new session', 'new sessions') + ' and ' + plural(n('traces'), 'new trace', 'new traces') + '.'
    : 'Nothing new to read.'];
  const waiting = n('waiting');
  if (waiting) {
    parts.push(waiting === 1 ? '1 conversation waits for its scenario to be stored; read again once it is.'
      : waiting + ' conversations wait for their scenarios to be stored; read again once they are.');
  }
  const other = n('other_company');
  if (other) {
    parts.push(plural(other, 'conversation uses', 'conversations use') + ' a session id another company\'s session already has, and '
      + (other === 1 ? 'was' : 'were') + ' not read.');
  }
  const failed = n('failed');
  if (failed) parts.push(plural(failed, 'conversation', 'conversations') + ' could not be read; read again to retry.');
  return parts.join(' ');
}

// The failure modes of one trace as the session page reads them (GET /eval/traces/{id}/labels): each live mode, the
// reader's own value (positive, negative or null), everyone else's, and how many people said each.
export function modeRows(data) {
  const d = data || {};
  const cells = d.cells || {};
  return (Array.isArray(d.modes) ? d.modes : []).map((m) => {
    const all = (Array.isArray(cells[m.lineage_id]) ? cells[m.lineage_id] : []).filter((c) => c && (c.value === 'positive' || c.value === 'negative'));
    const mine = all.find((c) => c.mine) || null;
    return {
      lineage: m.lineage_id, name: m.name || '', definition: m.definition || '', mine: mine ? mine.value : null,
      others: all.filter((c) => !c.mine).map((c) => ({ who: c.author_email || 'Someone', value: c.value })),
      pos: all.filter((c) => c.value === 'positive').length, neg: all.filter((c) => c.value === 'negative').length
    };
  });
}

// Whether the session page should read a turn's tree, notes or labels now: when it has nothing yet, or its last read
// failed, or (`always`, for notes, which other people and other pages change) every time the turn is opened. Never
// while a read is under way.
export function needsRead(entry, always) {
  if (!entry) return true;
  if (entry.loading || entry.refreshing) return false;
  if (entry.error) return true;
  return !!always;
}
