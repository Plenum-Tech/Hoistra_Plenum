// spacesLive — the navigator's Spaces: four built-in spaces with live figures, and the
// customer-named saved spaces from svc-udr.
//
// A space is what the Plenum AI shell calls one: a bucket on the left that a conversation
// files under. The built-ins are the four engines; their badges are the figures the Plenum
// saved-space panels read — lapsed certificates (compliance saved-space summary), open
// anomalies, vendors scored below 80 on their newest card, approvals waiting — taken from the
// reads the Home and Vendors pages already make (homeRaw, vpModel()). A figure whose source
// has not answered shows as "—", never as a seed number.
//
// Saved spaces come from GET /backend/udr/api/spaces (plenum_cafm.saved_spaces) and are
// created, renamed and deleted through the same router — the one place this navigator writes.
// Which sessions sit in a saved space is kept on the session record (svc-udr has no
// saved_space_item route).
//
// shapeSpaces() is pure and tested in test/spacesLive.test.mjs; the methods are mixed into
// HoistraLogic.prototype and `this` is the controller.
import { spacesApi } from '../api/spaces.js';
import { spaceKeyOf, BUILTIN_SPACE_KEYS } from './sessions.js';

const RETRY_MS = 30000;
const RETRY_MAX = 6;

export const BUILTIN_SPACES = [
  { key: 'compliance', name: 'Compliance', icon: 'ph-shield-check', domain: 'Compliance', view: 'cc', page: 'Compliance console' },
  { key: 'energy', name: 'Energy', icon: 'ph-lightning', domain: 'Energy', module: 'energy', page: 'Energy' },
  { key: 'vendors', name: 'Vendor performance', icon: 'ph-chart-line-up', domain: 'Vendors', view: 'vp', page: 'Vendors' },
  { key: 'ops', name: 'Vendor operations', icon: 'ph-wrench', domain: 'Work orders', module: 'ops', page: 'Work orders' }
];
// The keys must be the ones sessions.js files engines under.
if (BUILTIN_SPACES.some((b) => BUILTIN_SPACE_KEYS.indexOf(b.key) < 0)) throw new Error('spacesLive: built-in space keys drifted from sessions.js');

const num = (v) => (typeof v === 'number' && isFinite(v) ? v : null);
const n0 = (v) => num(v) || 0;
const gbp = (v) => '£' + Math.round(v).toLocaleString('en-GB');
const plural = (n, one, many) => n + ' ' + (n === 1 ? one : many);

const empty = (b) => ({ key: b.key, name: b.name, icon: b.icon, domain: b.domain, page: b.page, view: b.view || null, module: b.module || null,
  badge: '—', count: null, tone: 'none', kpis: [], live: false, custom: false, sessions: 0 });

function compliance(b, c) {
  const out = empty(b);
  if (!c || c.ok === false || !c.building_certificates || !c.vendor_certificates) return out;
  const bc = c.building_certificates, vc = c.vendor_certificates, rd = c.risk_dashboard || {};
  const lapsed = n0(bc.lapsed) + n0(vc.lapsed);
  out.count = lapsed;
  out.badge = lapsed + ' lapsed';
  out.tone = lapsed > 0 ? 'risk' : 'ok';
  out.live = true;
  out.kpis = [
    { label: 'Certificates on record', value: n0(bc.total) + n0(vc.total) },
    { label: 'Lapsed', value: lapsed },
    { label: 'Expiring in 30 days', value: n0(bc.expiring_lt_30) + n0(vc.expiring_lt_30) },
    { label: 'Drafts', value: n0(bc.drafts) + n0(vc.drafts) },
    { label: 'Vendors blocked', value: n0(rd.vendors_blocked) }
  ];
  return out;
}

function energy(b, a) {
  const out = empty(b);
  if (!a || a.ok === false) return out;
  const rows = Array.isArray(a.anomalies) ? a.anomalies : [];
  const n = num(a.count) !== null ? a.count : rows.length;
  const cost = rows.reduce((q, r) => q + n0(r.financial_gbp), 0);
  const sites = rows.map((r) => r.site_id).filter((x, i, arr) => x && arr.indexOf(x) === i).length;
  out.count = n;
  out.badge = plural(n, 'anomaly', 'anomalies');
  out.tone = n > 0 ? 'warn' : 'ok';
  out.live = true;
  out.kpis = [
    { label: 'Open anomalies', value: n },
    { label: 'Annualised exposure', value: gbp(cost) },
    { label: 'Sites affected', value: sites }
  ];
  return out;
}

function vendors(b, vm) {
  const out = empty(b);
  if (!vm || !vm.live) return out;
  const scored = (vm.vendors || []).filter((v) => num(v.score) !== null);
  const below = scored.filter((v) => v.score < 80).length;
  const avg = scored.length ? Math.round(scored.reduce((q, v) => q + v.score, 0) / scored.length) : null;
  out.count = below;
  out.badge = below + ' below 80';
  out.tone = below > 0 ? 'warn' : 'ok';
  out.live = true;
  out.kpis = [
    { label: 'Vendors scored', value: scored.length },
    { label: 'Average score', value: avg === null ? '—' : avg },
    { label: 'Below 80', value: below },
    { label: 'Blocked', value: (vm.vendors || []).filter((v) => v.blocked).length }
  ];
  return out;
}

// The approvals route pages at 500; a count at the cap is "500+", not a total.
const APPROVALS_CAP = 500;
const sevOf = (s) => {
  const x = String(s || '').toLowerCase();
  if (/critical|lapsed|blocked/.test(x)) return 'Critical';
  if (/action/.test(x)) return 'Action required';
  if (/medium|high|overdue/.test(x)) return 'Medium';
  return 'Info';
};

function ops(b, ap) {
  const out = empty(b);
  if (!ap || ap.ok === false) return out;
  const items = Array.isArray(ap.items) ? ap.items : [];
  const n = num(ap.count) !== null ? ap.count : items.length;
  const by = { Critical: 0, 'Action required': 0, Medium: 0, Info: 0 };
  items.forEach((it) => { by[sevOf(it.severity)] += 1; });
  out.count = n;
  out.badge = n + (n >= APPROVALS_CAP ? '+' : '') + ' to approve';
  out.tone = by.Critical > 0 ? 'risk' : n > 0 ? 'warn' : 'ok';
  out.live = true;
  out.kpis = [{ label: 'Pending', value: n + (n >= APPROVALS_CAP ? '+' : '') }]
    .concat(Object.keys(by).map((k) => ({ label: k, value: by[k] })));
  return out;
}

// input: { home: homeRaw|null, vendors: vpModel(), saved: rows|null, sessions, savedError, savedLoading }
export function shapeSpaces(input) {
  const inp = input || {};
  const home = inp.home || {};
  const sessions = inp.sessions || [];
  const chats = sessions.filter((s) => s && s.kind === 'chat');
  const builtin = BUILTIN_SPACES.map((b) => {
    const e = b.key === 'compliance' ? compliance(b, home.compliance)
      : b.key === 'energy' ? energy(b, home.anomalies)
      : b.key === 'vendors' ? vendors(b, inp.vendors)
      : ops(b, home.approvals);
    e.sessions = chats.filter((s) => spaceKeyOf(s.domain) === b.key).length;
    return e;
  });
  const savedLive = Array.isArray(inp.saved);
  const custom = (savedLive ? inp.saved : []).slice()
    .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))
    .map((r) => ({
      id: String(r.id), name: r.name || 'Untitled space', createdAt: r.created_at || null, createdBy: r.created_by || null,
      icon: 'ph-folder-simple', custom: true, live: true, tone: 'none', kpis: [], badge: '', count: null,
      sessions: sessions.filter((s) => s && s.spaceId === String(r.id)).length
    }));
  const byKey = {};
  builtin.forEach((b) => { byKey[b.key] = b; });
  custom.forEach((c) => { byKey[c.id] = c; });
  return {
    builtin: builtin, custom: custom, byKey: byKey,
    savedLive: savedLive, savedError: inp.savedError || '', savedLoading: !!inp.savedLoading
  };
}

// ── controller ───────────────────────────────────────────────────────────────
export const spacesMethods = {
  // Shaped once per change of its inputs — renderVals runs on every keystroke.
  spModel() {
    const s = this.state;
    const vm = this.vpModel();
    const key = [s.homeRaw, vm, s.spaces, s.sessions, s.spError, s.spLoading];
    const m = this._spMemo;
    if (m && m.key.every((k, i) => k === key[i])) return m.model;
    const model = shapeSpaces({ home: s.homeRaw, vendors: vm, saved: s.spaces, sessions: s.sessions || [], savedError: s.spError, savedLoading: s.spLoading });
    this._spMemo = { key: key, model: model };
    return model;
  },

  spaceEntry(key) { return key ? (this.spModel().byKey[key] || null) : null; },

  async spLoad() {
    if (this._spLoading) return;
    this._spLoading = true;
    clearTimeout(this._spRetry);
    this.setState({ spLoading: true });
    try {
      const r = await spacesApi.list();
      this._spAttempts = 0;
      this.setState({ spaces: Array.isArray(r && r.spaces) ? r.spaces : [], spLoading: false, spError: '' });
    } catch (e) {
      const msg = (e && e.message) || String(e);
      this._spAttempts = (this._spAttempts || 0) + 1;
      this.setState({ spLoading: false, spError: msg });
      if (this._spAttempts < RETRY_MAX) this._spRetry = setTimeout(() => this.spLoad(), RETRY_MS);
    } finally {
      this._spLoading = false;
    }
  },

  // The navigator's "+": an inline name field. Enter saves through svc-udr.
  spNewToggle() { this.setState((p) => ({ spNew: !p.spNew, spNewName: '' })); },
  spNewCancel() { this.setState({ spNew: false, spNewName: '' }); },
  spNewSet(v) { this.setState({ spNewName: v }); },

  async spCreate() {
    const name = String(this.state.spNewName || '').trim();
    if (!name) return;
    if (this.state.spBusy) return;
    if (!Array.isArray(this.state.spaces)) return this.flash('Saved spaces are unavailable — svc-udr did not answer.');
    this.setState({ spBusy: true });
    try {
      const row = await spacesApi.create(name, this.state.email || null);
      this.setState((p) => ({ spaces: [row].concat((p.spaces || []).filter((x) => x.id !== row.id)), spNew: false, spNewName: '', spBusy: false }));
      this.flash('Space “' + name + '” saved.');
      this.openSpace(String(row.id));
    } catch (e) {
      this.setState({ spBusy: false });
      this.flash('Could not create the space — ' + ((e && e.message) || e));
    }
  },

  async spRename(id, name) {
    const nm = String(name || '').trim();
    if (!id || !nm || this.state.spBusy) return;
    this.setState({ spBusy: true });
    try {
      const row = await spacesApi.rename(id, nm);
      this.setState((p) => ({ spaces: (p.spaces || []).map((x) => (String(x.id) === String(id) ? row : x)), spBusy: false, spRenaming: false, spRenameText: '' }));
      this.flash('Space renamed.');
    } catch (e) {
      this.setState({ spBusy: false });
      this.flash('Could not rename the space — ' + ((e && e.message) || e));
    }
  },

  async spDelete(id) {
    if (!id || this.state.spBusy) return;
    this.setState({ spBusy: true });
    try {
      await spacesApi.remove(id);
      this.setState((p) => ({
        spaces: (p.spaces || []).filter((x) => String(x.id) !== String(id)),
        // Sessions filed there go back to the general list.
        sessions: (p.sessions || []).map((s) => (s.spaceId === id ? Object.assign({}, s, { spaceId: null }) : s)),
        spBusy: false
      }));
      this.flash('Space deleted.');
      if (this.state.view === 'space' && this.state.spaceKey === id) this.openSessions(null);
    } catch (e) {
      this.setState({ spBusy: false });
      this.flash('Could not delete the space — ' + ((e && e.message) || e));
    }
  },

  openSpace(key) {
    this.setState({ view: 'space', spaceKey: key, spRenaming: false, spRenameText: '', navOpen: true, detail: null, queueOpen: false, paletteOpen: false });
    if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
  },

  // The space page's ask bar: a question starts a conversation filed in this space (see
  // sessionEnsure). An empty bar just opens the conversation page.
  spaceAskRun() {
    const q = String(this.state.spaceAsk || '').trim();
    if (!q) return this.ccOpenChat();
    this.setState({ spaceAsk: '' });
    this.askScoped(q);
  },

  // A built-in space's page: the live console where one exists, the module otherwise.
  openSpacePage(key) {
    const b = BUILTIN_SPACES.find((x) => x.key === key);
    if (!b) return;
    if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
    if (b.view) return this.setState({ view: b.view, navOpen: true, detail: null });
    return this.openModule(b.module);
  }
};
