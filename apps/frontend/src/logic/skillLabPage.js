// skillLabPage — Administration › Skill lab: agent instructions measured on replayed questions
// (svc-deepagents agents/skill_lab.py). An admin starts a run - Measure replays this company's
// questions with and without self-managed context; Tune also writes and tests a rewrite of the
// context instructions - reads every replayed answer with what it lost and what it cost, and
// approves or rejects a rewrite that won. Nothing reaches the agents until approved.
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller. skillLabVals is pure
// over the controller's state and is tested in test/skillLabPage.test.mjs.
import { deepAgentsApi } from '../api/deepAgents.js';
import { isStaleScope } from '../api/client.js';
import { fmtAgo } from './memoriesPage.js';

const pct = (x) => (x === null || x === undefined ? '—' : Math.round(Number(x) * 100) + '%');
const tok = (x) => (x === null || x === undefined ? '—' : Math.round(Number(x)).toLocaleString('en-GB'));
const usd = (x) => (x === null || x === undefined ? '—' : '$' + Number(x).toFixed(3));

// The headline of a finished run, in words. Pure.
export function runHeadline(run) {
  const s = (run && run.summary) || {};
  if (!run) return '';
  if (run.status === 'running') return 'Replaying questions…';
  if (run.status === 'failed') return 'Failed: ' + (run.error || 'unknown error');
  if (run.kind === 'compare') {
    const ref = s.reference || {}, cur = s.current || {};
    if (!cur.questions) return 'No question could be replayed.';
    const saved = ref.input_tokens ? Math.round((1 - cur.input_tokens / ref.input_tokens) * 100) : null;
    return 'Self-managed context kept ' + pct(cur.score) + ' of what the full-context answers said'
      + (saved !== null ? ', on ' + (saved >= 0 ? saved + '% fewer' : (-saved) + '% more') + ' tokens' : '')
      + ' (' + cur.questions + ' questions; ' + cur.compactions + ' compactions, ' + cur.cut_to_fit + ' cut to fit).';
  }
  if (s.outcome) return s.outcome[0].toUpperCase() + s.outcome.slice(1) + '.';
  return (run.proposal_id ? 'Rewrite proposed - ' : 'No rewrite proposed - ') + (s.verdict || '');
}

export const skillLabMethods = {
  slOpen() {
    window.scrollTo(0, 0);
    this.setState({ view: 'skilllab', role: 'admin', navOpen: true, detail: null });
    this.slLoad();
  },
  async slLoad() {
    this.setState({ slLoading: true, slErr: '' });
    try {
      const [runs, props] = await Promise.all([deepAgentsApi.skillLabRuns(), deepAgentsApi.skillLabProposals()]);
      this.setState({ slLoading: false, slLoadedAt: Date.now(), slRuns: (runs && runs.runs) || [], slRunning: !!(runs && runs.running),
        slProposals: (props && props.proposals) || [], slCurrent: (props && props.current) || {} });
      // A run in progress is polled until it finishes.
      clearTimeout(this._slPoll);
      if (runs && runs.running) this._slPoll = setTimeout(() => { if (this.state.view === 'skilllab') this.slLoad(); }, 15000);
    } catch (e) {
      if (isStaleScope(e)) return;
      this.setState({ slLoading: false, slErr: (e && e.message) || String(e) });
    }
  },
  slSetSample(e) {
    const n = Math.max(1, Math.min(10, parseInt(e && e.target ? e.target.value : e, 10) || 6));
    this.setState({ slSample: n });
  },
  async slStart(kind) {
    if (this.state.slRunning) return this.flash('A run is already in progress.');
    this.setState({ slStarting: kind });
    try {
      await deepAgentsApi.skillLabStart({ kind: kind, sample: this.state.slSample || 6 });
      this.flash(kind === 'optimise' ? 'Tuning started - replays take a few minutes.' : 'Measuring started - replays take a few minutes.');
      this.setState({ slStarting: null });
      this.slLoad();
    } catch (e) {
      this.setState({ slStarting: null });
      this.flash('Could not start: ' + ((e && e.message) || e));
    }
  },
  async slOpenRun(id) {
    if (this.state.slRunId === id) { this.setState({ slRunId: null, slRun: null }); return; }
    this.setState({ slRunId: id, slRun: null });
    try {
      const out = await deepAgentsApi.skillLabRun(id);
      if (this.state.slRunId === id) this.setState({ slRun: out && out.run });
    } catch (e) {
      this.flash('Could not read the run: ' + ((e && e.message) || e));
    }
  },
  async slReview(id, decision) {
    try {
      if (decision === 'revert') await deepAgentsApi.skillLabRevert('query-builder', 'context-budget');
      else await deepAgentsApi.skillLabReview(id, decision);
      this.flash(decision === 'approve' ? 'Approved - the agents read the new instructions from now on.'
        : decision === 'reject' ? 'Rejected.' : 'Back to the shipped instructions.');
      this.slLoad();
    } catch (e) {
      this.flash('Could not ' + decision + ': ' + ((e && e.message) || e));
    }
  }
};

export function skillLabVals(c, now) {
  const s = c.state;
  const runs = s.slRuns || [];
  const props = s.slProposals || [];
  const latest = runs.find((r) => r.status === 'done' && r.kind === 'compare');
  const lc = (latest && latest.summary) || {};
  const ref = lc.reference || {}, cur = lc.current || {};
  const active = props.find((p) => p.status === 'active');
  const run = s.slRun;
  return {
    isSkillLab: s.signedIn && s.view === 'skilllab',
    slLoading: !!s.slLoading && !s.slLoadedAt,
    slError: s.slErr || '',
    slReload: () => c.slLoad(),
    slRunning: !!s.slRunning,
    slSample: s.slSample || 6,
    slSetSample: (e) => c.slSetSample(e),
    slMeasure: () => c.slStart('compare'),
    slTune: () => c.slStart('optimise'),
    slStarting: s.slStarting || null,
    slTiles: [
      { value: pct(cur.score), label: 'Completeness', hint: latest ? 'of what full-context answers said' : 'no measurement yet', tone: 'var(--color-text)' },
      { value: tok(cur.input_tokens), label: 'Tokens per question', hint: latest ? 'self-managed, vs ' + tok(ref.input_tokens) + ' full' : '—', tone: 'var(--color-text)' },
      { value: tok(cur.peak_prompt_tokens), label: 'Largest prompt', hint: latest ? 'vs ' + tok(ref.peak_prompt_tokens) + ' full' : '—', tone: 'var(--color-text)' },
      { value: usd(cur.usd), label: 'Cost per question', hint: latest ? 'vs ' + usd(ref.usd) + ' full' : '—', tone: 'var(--color-text)' },
      { value: active ? 'Tuned' : 'Shipped', label: 'Context instructions', hint: active ? 'approved ' + fmtAgo(active.reviewed_at, now) : 'the text in the repo', tone: active ? 'var(--color-accent)' : 'var(--color-text)' }
    ],
    slRuns: runs.map((r) => ({
      id: r.id, kind: r.kind === 'optimise' ? 'Tune' : 'Measure', status: r.status, who: r.created_by || '',
      when: fmtAgo(r.started_at, now), sample: r.sample, headline: runHeadline(r),
      open: s.slRunId === r.id, toggle: () => c.slOpenRun(r.id),
      tone: r.status === 'failed' ? 'var(--st-risk)' : r.status === 'running' ? 'var(--st-warn)' : 'var(--color-text)'
    })),
    slRunRows: run && s.slRunId === run.id ? (run.results || []).map((x, i) => ({
      key: i, q: x.question, split: x.split === 'held_out' ? 'held out' : 'train', variant: x.variant,
      score: x.variant === 'reference' ? '—' : pct(x.score), tokens: tok(x.input_tokens), peak: tok(x.peak_prompt_tokens),
      cost: usd(x.usd), compactions: (x.context && x.context.compactions) || 0, cut: (x.context && x.context.shrunk) || 0,
      lost: ((x.judge && x.judge.missing) || []).concat(((x.judge && x.judge.contradicted) || []).map((m) => 'changed: ' + m)).join('; '),
      error: x.error || ''
    })) : [],
    slRunLoading: !!s.slRunId && !run,
    slProposals: props.filter((p) => p.status === 'proposed').map((p) => ({
      id: p.id, doc: p.skill + '/' + p.doc, when: fmtAgo(p.created_at, now),
      verdict: (p.evidence && p.evidence.verdict) || '', rationale: (p.evidence && p.evidence.rationale) || '',
      content: p.content, current: (s.slCurrent || {})[p.skill + '/' + p.doc] || '',
      approve: () => c.slReview(p.id, 'approve'), reject: () => c.slReview(p.id, 'reject')
    })),
    slActive: active ? { doc: active.skill + '/' + active.doc, by: active.reviewed_by || '', when: fmtAgo(active.reviewed_at, now),
      revert: () => c.slReview(active.id, 'revert') } : null,
    slEmpty: !runs.length
  };
}
