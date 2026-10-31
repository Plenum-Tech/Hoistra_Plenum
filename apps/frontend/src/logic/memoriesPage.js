// memoriesPage — Administration › Chat memory: what the chat has learned for this company
// (svc-deepagents services/chat_memories.py), who told it, how often it has been used, and
// Forget.
//
// Facts and corrections are the company's (every colleague gets them, signed with who said
// them); preferences are each person's own, so an admin sees only their own preferences here —
// nobody's else's. "Figures" are never memories: counts, costs and statuses are re-read live.
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller. memoriesPageVals is
// pure over the controller's state and is tested in test/memoriesPage.test.mjs.
import { deepAgentsApi } from '../api/deepAgents.js';
import { isStaleScope } from '../api/client.js';

export const MEMORY_FILTERS = ['All', 'Facts', 'Corrections', 'My preferences'];
const KIND_OF = { Facts: 'fact', Corrections: 'correction', 'My preferences': 'preference' };
const KIND_LABEL = { fact: 'Fact', correction: 'Correction', preference: 'Preference' };

// Elapsed time in the page's bands. Pure.
export function fmtAgo(iso, now) {
  const t = iso ? Date.parse(iso) : NaN;
  if (isNaN(t)) return '';
  const s = Math.max(0, Math.round(((now === undefined ? Date.now() : now) - t) / 1000));
  if (s < 90) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return m + ' min ago';
  const h = Math.round(m / 60);
  if (h < 24) return h + (h === 1 ? ' hour ago' : ' hours ago');
  const d = Math.round(h / 24);
  return d + (d === 1 ? ' day ago' : ' days ago');
}

export const memoriesPageMethods = {
  mpOpen() {
    window.scrollTo(0, 0);
    this.setState({ view: 'memories', role: 'admin', navOpen: true, detail: null, mpArmed: null });
    this.mpLoad();
  },
  async mpLoad() {
    this.setState({ mpLoading: true, mpErr: '' });
    try {
      const out = await deepAgentsApi.memories();
      this.setState({ mpLoading: false, mpRows: (out && out.memories) || [], mpAvailable: !out || out.available !== false, mpLoadedAt: Date.now(),
        mpCanManage: !!(out && out.can_manage) });
    } catch (e) {
      if (isStaleScope(e)) return;
      this.setState({ mpLoading: false, mpErr: (e && e.message) || String(e) });
    }
  },
  mpSetFilter(f) { this.setState({ mpFilter: f, mpArmed: null }); },
  // An admin's decision on a teaching that waits: approve puts it in front of the chat from
  // now on; reject hides it. One click - a wrong decision is undone by Forget or by teaching again.
  async mpReview(id, decision) {
    this.setState({ mpRows: (this.state.mpRows || []).map((m) => (m.id === id ? Object.assign({}, m, { busy: true }) : m)) });
    try {
      await deepAgentsApi.reviewMemory(id, decision);
      this.setState((p) => ({ mpRows: (p.mpRows || []).map((m) => (m.id !== id ? m : Object.assign({}, m, { busy: false, status: decision === 'approve' ? 'active' : 'rejected' })))
        .filter((m) => m.status !== 'rejected') }));
      this.flash(decision === 'approve' ? 'Approved - the chat recalls it from now on.' : 'Rejected - it will not be recalled.');
    } catch (e) {
      this.setState((p) => ({ mpRows: (p.mpRows || []).map((m) => (m.id === id ? Object.assign({}, m, { busy: false }) : m)) }));
      this.flash('Could not ' + decision + ': ' + ((e && e.message) || e));
    }
  },
  mpSetSearch(e) { this.setState({ mpSearch: e.target.value }); },
  // Forget is two clicks: the first arms the row, the second removes it. Quiet if the server
  // refuses (a colleague's fact an admin may remove, a non-admin may not).
  async mpForget(id) {
    if (this.state.mpArmed !== id) { this.setState({ mpArmed: id }); return; }
    this.setState({ mpArmed: null, mpRows: (this.state.mpRows || []).map((m) => (m.id === id ? Object.assign({}, m, { busy: true }) : m)) });
    try {
      await deepAgentsApi.forgetMemory(id);
      this.setState((p) => ({ mpRows: (p.mpRows || []).filter((m) => m.id !== id) }));
      this.flash('Forgotten.');
    } catch (e) {
      this.setState((p) => ({ mpRows: (p.mpRows || []).map((m) => (m.id === id ? Object.assign({}, m, { busy: false }) : m)) }));
      this.flash('Could not forget that: ' + ((e && e.message) || e));
    }
  }
};

export function memoriesPageVals(c, now) {
  const s = c.state;
  const rows = s.mpRows || [];
  const filter = s.mpFilter || 'All';
  const q = String(s.mpSearch || '').trim().toLowerCase();
  const keep = (m) => (filter === 'All' || m.kind === KIND_OF[filter])
    && (!q || String(m.text || '').toLowerCase().indexOf(q) > -1 || String(m.subject || '').toLowerCase().indexOf(q) > -1
      || String(m.created_by_email || '').toLowerCase().indexOf(q) > -1);
  const pending = rows.filter((m) => m.status === 'pending');
  const active = rows.filter((m) => m.status !== 'pending');
  const shared = active.filter((m) => !m.user_id);
  const mine = active.filter((m) => m.user_id);
  const used = rows.reduce((a, m) => a + (Number(m.use_count) || 0), 0);
  return {
    isMemories: s.signedIn && s.view === 'memories',
    mpLoading: !!s.mpLoading && !s.mpLoadedAt,
    mpError: s.mpErr || '',
    mpUnavailable: s.mpAvailable === false,
    mpReload: () => c.mpLoad(),
    mpTiles: [
      { value: String(shared.length), label: 'Company memories', hint: shared.filter((m) => m.kind === 'fact').length + ' facts · ' + shared.filter((m) => m.kind === 'correction').length + ' corrections', tone: 'var(--color-text)' },
      { value: String(mine.length), label: 'Your preferences', hint: 'yours alone; colleagues keep their own', tone: 'var(--color-text)' },
      { value: String(used), label: 'Times recalled', hint: 'put in front of the chat on a question', tone: used ? 'var(--color-accent)' : 'var(--color-text)' },
      { value: String(new Set(shared.map((m) => m.created_by_email).filter(Boolean)).size), label: 'Contributors', hint: 'colleagues whose words were kept', tone: 'var(--color-text)' },
      { value: String(pending.length), label: 'Awaiting approval', hint: s.mpCanManage ? 'teachings from colleagues - yours to approve' : 'not recalled until an admin approves', tone: pending.length ? 'var(--st-warn)' : 'var(--color-text)' }
    ],
    mpCanManage: !!s.mpCanManage,
    // Teachings that wait for an admin: above the table, with the decision beside each.
    mpPending: pending.filter(keep).map((m) => ({
      id: m.id, text: m.text, kind: KIND_LABEL[m.kind] || m.kind, kindKey: m.kind, subject: m.subject || '',
      who: m.created_by_email || 'a colleague', when: fmtAgo(m.created_at, now), busy: !!m.busy,
      approve: () => c.mpReview(m.id, 'approve'), reject: () => c.mpReview(m.id, 'reject')
    })),
    mpFilters: MEMORY_FILTERS.map((x) => ({ label: x, on: filter === x, pick: () => c.mpSetFilter(x) })),
    mpSearch: s.mpSearch || '', mpSetSearch: (e) => c.mpSetSearch(e),
    mpRows: active.filter(keep).map((m) => ({
      id: m.id, text: m.text, kind: KIND_LABEL[m.kind] || m.kind, kindKey: m.kind, subject: m.subject || '',
      scope: m.user_id ? 'You' : 'Company',
      who: m.user_id ? 'you' : (m.created_by_email || 'a colleague'),
      when: fmtAgo(m.created_at, now), used: Number(m.use_count) || 0,
      lastUsed: m.last_used_at ? fmtAgo(m.last_used_at, now) : 'never',
      busy: !!m.busy, armed: s.mpArmed === m.id,
      approvedBy: m.reviewed_by_email ? 'approved by ' + m.reviewed_by_email : '',
      forget: (e) => { if (e && e.stopPropagation) e.stopPropagation(); c.mpForget(m.id); },
      forgetLabel: s.mpArmed === m.id ? 'Click again to forget' : 'Forget'
    })),
    mpEmpty: !rows.length,
    mpNoMatch: rows.length > 0 && !rows.filter(keep).length && !pending.filter(keep).length
  };
}
