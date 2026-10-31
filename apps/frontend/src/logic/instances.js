// instances — the four instances a person picks from after signing in (11 Oct 2026).
//
// An instance is the USER view set up for one kind of customer: which reports the navigator
// lists, which spaces sit under them, the four cards across the top of Home, the question in
// the ask bar and the questions pinned under it. Instance 04 is the full platform — the user
// view exactly as it was before instances existed.
//
// What an instance does NOT touch:
//   - the admin view, beyond two additions Hussain asked for (12 Oct 2026): the instance chip in
//     the top bar, and the Compliance Engine's Compliance Graph in the navigator. An admin picks
//     an instance at sign-in and lands in the admin view; the rest of the instance takes effect
//     once they switch to User view.
//   - the superadmin. A superadmin signs straight into the platform console and is never
//     shown the picker; their User view is the full platform, as before.
//   - the data. Every figure on an instance home is read from the same live registers the
//     rest of the app reads (value ledger, maintenance overview, vendor scorecards, the
//     compliance register, the condition scan). The prototype these instances come from
//     (hoistra-frontend-changes, 11 Oct 2026) shows sample estates — Bishopsgate Tower, a
//     Dubai hospital, three owned buildings — and none of those names reach this screen:
//     they are not this company's buildings.
//
// The prototype's data scoping (one building in Ops Brain, a hospital in the Compliance
// Engine) has no live equivalent: the building scope picker in the top bar already narrows
// every read to one building, and the instance does not second-guess it.
//
// The pure shapers here are tested in test/instances.test.mjs; the methods are mixed into
// HoistraLogic.prototype and `this` is the controller.
import { money } from './homeLive.js';
import { t } from './constants.js';

export const INSTANCES = [
  {
    key: 'ops', n: '01', name: 'Ops Brain', icon: 'ph-brain',
    icp: 'Property manager · UK',
    desc: 'Your daily operations, connected in real time and turned into intelligence that feeds your financial model, so you control your P&L instead of reading it after the fact.',
    reports: ['buildings', 'ops', 'vendors'],
    // The value ledger's module keys (engines/value_ledger.py) this instance answers for.
    value: ['maintenance', 'vendors', 'compliance'],
    placeholder: 'What needs my decision this week?',
    // [chip label, the question it asks]
    pinned: [
      ['What needs my decision this week?', 'What needs my approval today?'],
      ['Which vendor is costing me most?', 'Which vendor is costing me money?'],
      ['Which recommendations were never actioned?', 'Which inspection recommendations were never actioned?'],
      ['Is PPM running to plan?', 'Is planned maintenance running to plan?']
    ],
    asks: {
      buildings: ['What is open across my buildings this week?', 'What documents have I ingested?', 'Which assets are unaccounted for?'],
      vp: ['Is each vendor inside its SLA?', 'Which vendor is costing me money?', 'What service credit can I claim?']
    }
  },
  {
    key: 'comp', n: '02', name: 'Compliance Engine', icon: 'ph-shield-check',
    icp: 'Regulated estates · UAE hospital',
    desc: 'Every room, floor and regulated area provably compliant, every day, without a spreadsheet, a chase email or a surprise on inspection day.',
    // 'graph' is the Compliance Graph report (logic/hoistGraph.js), added 12 Oct 2026. It is the
    // one report this instance shows in the admin view as well.
    reports: ['buildings', 'graph', 'compliance', 'vendors'],
    // Left out on purpose: its value is fines and closures avoided, which nothing measures.
    value: null,
    placeholder: 'Which buildings are out of compliance today?',
    pinned: [
      ['Which buildings are out of compliance?', 'Which buildings are out of compliance today?'],
      ['Which vendors are not eligible?', 'Which vendors are not eligible to work here?'],
      ['What lapses in the next 30 days?', 'What certificates lapse in the next 30 days?'],
      ['Which certificates failed authenticity?', 'Which certificates failed authenticity checks?']
    ],
    asks: {
      buildings: ['Which buildings are out of compliance today?', 'What documents evidence each certificate?', 'Which certificates lapse next?'],
      vp: ['Which vendors are not eligible to work here?', 'Which approvals expire this month?', 'Who can replace a blocked vendor?']
    }
  },
  {
    key: 'assets', n: '03', name: 'Assets Operating System', icon: 'ph-cube',
    icp: 'Building owner · UAE and US',
    desc: 'An estate where no asset fails unannounced and every one runs to the end of its useful life, so capital is spent when the numbers say so and the value of what you own keeps rising.',
    reports: ['buildings', 'energy', 'assets', 'ops', 'vendors'],
    value: ['energy', 'assets', 'maintenance', 'vendors'],
    placeholder: 'Which of my assets are losing value fastest?',
    pinned: [
      ['Which assets are losing value?', 'Which assets are losing value fastest?'],
      ['What is energy telling me about asset health?', 'What is energy telling me about asset health?'],
      ['Which assets should I remediate first?', 'Which assets should I remediate first?'],
      ['What will break in the next 90 days?', 'What will break in the next 90 days?']
    ],
    asks: {
      buildings: ['Which building carries the most asset risk?', 'What documents are bound to each building?', 'Which assets are unaccounted for?'],
      vp: ['Is repair work holding?', 'Which vendor is capped and why?', 'What service credit can I claim?']
    }
  },
  {
    key: 'full', n: '04', name: 'Full platform', icon: 'ph-squares-four',
    icp: 'Every role · every market',
    desc: 'Your whole estate run from one place, where operations, compliance, energy, assets and vendors all add up to one P&L you control.',
    reports: ['buildings', 'compliance', 'vendors', 'energy', 'assets', 'ops'],
    value: null, placeholder: null, pinned: null, asks: null
  }
];
export const INSTANCE_KEYS = INSTANCES.map((i) => i.key);

const REPORT_LABEL = { buildings: 'Buildings & Hoist Graph', graph: 'Compliance Graph', compliance: 'Compliance', vendors: 'Vendors', energy: 'Energy', assets: 'Assets', ops: 'Maintenance' };
// The navigator's report keys (renderVals navSections) → the instance's report keys.
const NAV_REPORT = { buildings_user: 'buildings', compliance: 'compliance', vendors: 'vendors', energy: 'energy', assets: 'assets', ops: 'ops' };
// The built-in spaces (spacesLive.js BUILTIN_SPACES) → the report each one belongs to.
const SPACE_REPORT = { compliance: 'compliance', energy: 'energy', vendors: 'vendors', ops: 'ops' };

const num = (v) => (typeof v === 'number' && isFinite(v) ? v : null);
const plural = (n, one, many) => n + ' ' + (n === 1 ? one : many);
const isBuilding = (b) => b && b.name && !/no building on certificate/i.test(b.name);

export function instanceOf(key) { return INSTANCES.find((i) => i.key === key) || null; }

// An admin or a user picks an instance. So does a superadmin while viewing as a company
// (superAdmin.js viewAsCompany, `viewOrgId`) — for that view they are the company's admin.
// A superadmin's own sign-in goes to the console, and their own company app underneath it
// is the full platform, as it always was.
export function usesInstances(account, viewOrgId) {
  if (!account) return false;
  if (account.role === 'user' || account.role === 'admin') return true;
  return account.role === 'superadmin' && !!viewOrgId;
}

// The instance shaping the screen right now: a chosen, scoped instance in User view.
// null for the admin view, the superadmin, the full platform and before one is picked.
export function scopedInstance(s) {
  if (!s || s.role === 'admin' || !usesInstances(s.account, s.viewOrgId)) return null;
  const c = instanceOf(s.instance);
  return c && c.key !== 'full' ? c : null;
}

// The instance's own slice of the value ledger (GET /api/value/summary). The headline is
// the detected sum over the modules the instance answers for, the same figure the full
// platform's card headlines. A module the server could not price stays "—"; when none of
// them is priced the headline is "—" too, never a £0 nobody derived.
export function instanceValue(value, modules) {
  if (!value || !value.ledger) return { answered: false };
  const want = modules || [];
  const rows = (value.ledger.modules || []).filter((m) => want.indexOf(m.key) > -1);
  const counted = rows.filter((m) => m.counted && num(m.detected) !== null);
  return {
    answered: true,
    year: value.year,
    total: counted.length ? money(counted.reduce((q, m) => q + m.detected, 0)) : '—',
    rows: want.map((k) => rows.find((m) => m.key === k)).filter(Boolean).map((m) => ({
      key: m.key, head: m.name, counted: !!m.counted,
      detected: m.counted ? money(m.detected) : '—',
      saved: m.counted ? money(m.saved) : '—',
      note: m.note || '',
      items: (m.items || []).map((i) => ({
        what: i.what, action: i.action, detected: money(i.detected),
        saved: i.saved === null || i.saved === undefined ? '—' : money(i.saved),
        basis: i.basis || '', est: /estimated/i.test(i.basis || '')
      }))
    }))
  };
}

// The Compliance Engine's four cards, from the live compliance register (ccLive). Nothing is
// counted from the sample register that stands in before the read answers.
//   Buildings compliant  no pack type missing and no building certificate lapsed
//   Lapsed               building and vendor certificates past expiry
//   Failed checks        certificates whose authenticity check flagged them
//   Vendors not eligible blocked vendors; the sub-line counts the next to expire
export function complianceEngineCards(reg, opts) {
  const o = opts || {};
  if (!reg) {
    const s = o.loading ? 'Reading the compliance register…' : 'The compliance register has not answered yet';
    return ['Buildings compliant', 'Lapsed', 'Failed checks', 'Vendors not eligible'].map((l) => ({ l: l, v: '—', s: s, tone: null }));
  }
  const certs = reg.certs || [];
  const buildings = (reg.buildings || []).filter(isBuilding);
  const vendors = reg.vendors || [];
  const lapsedAt = {};
  certs.forEach((c) => { if (c.kind === 'building' && num(c.days) !== null && c.days < 0) lapsedAt[c.holder] = true; });
  const ok = buildings.filter((b) => !(b.gaps || []).length && !lapsedAt[b.name]).length;
  const obligations = buildings.reduce((q, b) => q + (num(b.req) || 0), 0);
  const lapsed = certs.filter((c) => num(c.days) !== null && c.days < 0);
  const failed = certs.filter((c) => /suspect/.test(String(c.auth || '')));
  const blocked = vendors.filter((v) => v.block === 'Blocked');
  const soon = vendors.filter((v) => v.block !== 'Blocked' && v.worst === '<30d').length;
  return [
    { l: 'Buildings compliant', v: buildings.length ? ok + ' of ' + buildings.length : '0',
      s: plural(obligations, 'obligation', 'obligations') + ' mapped to buildings',
      tone: ok < buildings.length ? 'warn' : 'ok' },
    { l: 'Lapsed', v: String(lapsed.length),
      s: lapsed.length ? lapsed.filter((c) => c.kind === 'building').length + ' building · ' + lapsed.filter((c) => c.kind === 'vendor').length + ' vendor' : 'nothing past expiry',
      tone: lapsed.length ? 'risk' : 'ok' },
    { l: 'Failed checks', v: String(failed.length), s: 'authenticity or registration',
      tone: failed.length ? 'risk' : 'ok' },
    { l: 'Vendors not eligible', v: blocked.length + ' of ' + vendors.length,
      s: soon + ' more expire inside 30 days',
      tone: blocked.length ? 'warn' : 'ok' }
  ];
}

// ── controller methods ──────────────────────────────────────────────────
export const instancesMethods = {
  // Picking keeps the role: an admin goes on to the admin view as it was, a user (or an admin
  // who opened the picker from User view) to the instance's home.
  instPick(key) {
    if (!instanceOf(key)) return;
    if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
    this.setState({ instance: key, view: 'home', navOpen: true, detail: null, queueOpen: false, orchOpen: false, acctOpen: false, query: '' });
  },

  instSwitch() {
    if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
    this.setState({ view: 'instances', detail: null, queueOpen: false, orchOpen: false, acctOpen: false });
  },

  // The four cards across an instance home. A card whose read has not answered says so.
  instHomeCards(c, s) {
    const go = (patch) => () => {
      if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
      this.setState(Object.assign({ navOpen: true, detail: null }, patch));
    };
    let cards = [];
    if (c.key === 'ops') {
      const mx = this.mxModel();
      const card = (k) => (mx.cards || []).find((x) => x.k === k) || {};
      const owed = card('decisions_owed'), rec = card('recommendations_unconverted');
      const waiting = s.mxLiveLoading ? 'Reading the maintenance records…' : 'The maintenance records have not answered yet';
      const vm = this.vpModel();
      const scored = (vm.vendors || []).filter((v) => num(v.score) !== null);
      const below = scored.filter((v) => v.score < 80);
      cards = [
        { l: 'Decisions owed', v: owed.v === null || owed.v === undefined ? '—' : String(owed.v),
          s: mx.live ? (owed.s || 'across your buildings') : waiting, tone: 'risk', click: () => this.openModule('ops') },
        { l: 'Recommendations open', v: rec.answerable === false || rec.v === null || rec.v === undefined ? '—' : String(rec.v),
          s: !mx.live ? waiting : rec.answerable === false ? (rec.reason || 'The inspection reports cannot answer this yet') : (rec.s || 'from inspection reports'),
          tone: 'warn', click: () => this.openInsp('Which recommendations were never converted to orders?') },
        { l: 'Vendors below 80', v: vm.live ? below.length + ' of ' + scored.length : '—',
          s: !vm.live ? (s.vpLoading ? 'Reading the vendor scorecards…' : 'The vendor scorecards have not answered yet')
            : below.length ? below.map((v) => v.name + ' ' + Math.round(v.score)).join(' · ') : 'every scored vendor at 80 or above',
          tone: below.length ? 'warn' : 'ok',
          click: go({ view: 'vp', vpVendor: (below[0] || scored[0] || {}).id || s.vpVendor }) }
      ];
    } else if (c.key === 'assets') {
      // Counted over the live asset register. Before it answers, "0 threats" would claim
      // there are none, so each card says it is still reading instead.
      const read = Array.isArray(s.asLive);
      cards = (this.asVals(s).asCards || []).slice(0, 3).map((x) => ({
        l: x.l, v: read ? x.v : '—',
        s: read ? x.s : (s.asLiveLoading ? 'Reading the asset register…' : 'The asset register has not answered yet'),
        color: x.color, click: () => this.openModule('assets')
      }));
    } else if (c.key === 'comp') {
      // A card opens the page on its list, as the page's own tile does (tile and list together).
      const clicks = [go({ view: 'cc', ccTile: null, ccQueue: null }), go({ view: 'cc', ccTile: 'lapsed', ccQueue: 'lapsed', ccQueueOpenId: null }), go({ view: 'cc', ccTile: 'auth', ccQueue: 'auth', ccQueueOpenId: null }), go({ view: 'vp' })];
      cards = complianceEngineCards(s.ccLive, { loading: s.ccLoading }).map((x, i) => Object.assign(x, { click: clicks[i] }));
    }
    if (c.value) {
      const pv = instanceValue(s.homeRaw && s.homeRaw.value, c.value);
      cards = [{
        l: 'Platform value · ' + (pv.answered ? pv.year : new Date().getFullYear()),
        v: pv.answered ? pv.total : '—',
        s: pv.answered
          ? 'detected · ' + pv.rows.map((r) => r.head + ' ' + r.detected).join(' · ')
          : (s.homeLoading ? 'Reading the value ledger…' : 'The value ledger has not answered yet'),
        tone: 'ok',
        click: () => this.orchWith('Platform value ledger · ' + (pv.answered ? pv.year : new Date().getFullYear()), c.name, 'value', {})
      }].concat(cards.slice(0, 3));
    }
    // A dash is no verdict, so it carries no status colour: a green "—" reads as all clear.
    return cards.map((x) => Object.assign({}, x, {
      color: x.v === '—' ? 'var(--color-neutral-500)' : (x.color || (x.tone ? t(x.tone).color : 'var(--color-text)'))
    }));
  },

  // The instance layer over the user view. Returned keys override the full-platform values;
  // in the admin view, for a superadmin and in the full platform it changes only what the
  // picker and the chip need.
  instVals(s, vals) {
    const a = s.account || null;
    const on = usesInstances(a, s.viewOrgId);
    const chosen = instanceOf(s.instance);
    const userView = s.role !== 'admin';
    const c = scopedInstance(s);
    const hm = this.homeModel();
    const nB = hm.hero.buildings;
    const out = {
      isPicker: !!s.signedIn && s.view === 'instances',
      pickerSignedIn: 'Signed in' + (vals.tenant ? ' · ' + vals.tenant : ''),
      pickerSignOut: () => this.authSignOut(),
      // A superadmin viewing as a company reaches the picker straight from the console
      // (superAdmin.js viewAsCompany). The picker hides the top bar that holds "Exit", so it
      // carries its own way back - the same as the account menu's "Super Admin console".
      pickerExit: a && a.role === 'superadmin' && s.viewOrgId ? () => {
        this.exitViewAsCompany();
        this.setState({ saOn: true }, () => { if (typeof this.saLiveLoad === 'function') this.saLiveLoad(); });
      } : null,
      // Opened from the chip with an instance already picked: a way back without picking again.
      pickerBack: on && chosen ? () => this.setState({ view: 'home' }) : null,
      instCards: INSTANCES.map((x) => ({
        key: x.key, n: x.n, name: x.name, icon: x.icon, icp: x.icp,
        // The full platform's scope is the company's own estate, counted; the other three
        // describe who they are for and nothing else.
        scope: x.key === 'full' ? 'All modules' + (nB !== null ? ' · ' + plural(nB, 'building', 'buildings') : '') : '',
        desc: x.desc,
        reports: x.reports.map((r) => REPORT_LABEL[r]).join(' · '),
        current: s.instance === x.key,
        pick: () => this.instPick(x.key)
      })),
      // The chip in the top bar (and its line in the account menu) names the instance and
      // switches it, in the user view and the admin view alike (Hussain, 12 Oct 2026). Before an
      // instance is picked there is nothing to name.
      instShow: on && !!chosen,
      instName: chosen ? chosen.name : '',
      instLine: chosen ? chosen.name + ' · ' + chosen.icp : '',
      instSwitch: () => this.instSwitch(),
      isInstHome: !!c,
      navReportAdd: !c,
      homePh: c ? c.placeholder : 'Which buildings put me at risk this month?'
    };
    // The Compliance Engine's admin view keeps everything it had and adds its Compliance Graph
    // (Hussain, 12 Oct 2026). The admin view lists no other user-view report.
    if (on && !userView && chosen && chosen.reports.indexOf('graph') > -1) out.navSections = [this.hgNavItem(s)];
    if (!c) return out;

    const reports = c.reports;
    out.navSections = (vals.navSections || []).filter((n) => reports.indexOf(NAV_REPORT[n.key]) > -1);
    // The Compliance Graph is this instance's own report, not one of the full platform's, so it
    // is placed here, after Buildings.
    if (reports.indexOf('graph') > -1) {
      const at = out.navSections.findIndex((n) => n.key === 'buildings_user');
      out.navSections.splice(at + 1, 0, this.hgNavItem(s));
    }
    out.navSpaces = (vals.navSpaces || []).filter((sp) => !SPACE_REPORT[sp.key] || reports.indexOf(SPACE_REPORT[sp.key]) > -1);
    out.navReports = [];
    out.pinned = c.pinned.map((p) => ({ label: p[0], run: () => this.askScoped(p[1]) }));
    out.instKicker = 'Instance ' + c.n + ' · ' + c.name + ' · ' + c.icp;
    // The line under the title: the company's own estate, counted, with the figure this
    // instance is about in the middle. Before the register answers it is left empty — the
    // full platform's sample portfolio ("24 buildings hoisted · 1.84m ft²") never stands in.
    if (vals.heroLive) {
      const vm = this.vpModel();
      const mid = c.key === 'ops'
        ? (vm.live ? plural(vm.vendors.length, 'vendor', 'vendors') + ' on scorecard' : null)
        : c.key === 'comp'
          ? plural(hm.hero.certificates || 0, 'certificate', 'certificates') + ' on record'
          : (Array.isArray(s.asLive) ? plural(s.asLive.length, 'asset', 'assets') + ' on the register' : null);
      out.instTitleLine = [vals.heroStats[0], mid, vals.heroStats[2]].filter(Boolean);
    } else {
      out.instTitleLine = [];
    }
    out.instHomeCards = this.instHomeCards(c, s);
    if (c.value) {
      const pv = instanceValue(s.homeRaw && s.homeRaw.value, c.value);
      // Before the ledger answers the panel says so: the full platform's sample figures
      // (renderVals pvTotal "£970k", VALUE_LEDGER) never stand in on an instance (review, 12 Oct).
      if (!pv.answered) {
        out.pvTotal = '—';
        out.pvLedger = [];
        out.pvNote = s.homeLoading ? 'Reading the value ledger…' : 'The value ledger has not answered yet.';
      }
      if (pv.answered) {
        out.pvTotal = pv.total;
        out.pvLedger = pv.rows.map((r) => ({
          mod: r.head, saved: r.saved, detected: r.detected,
          items: (r.items.length ? r.items : [{
            what: r.counted ? 'Nothing priced this year' : 'No figure claimed',
            action: r.counted ? 'The engines recorded no priced row' : 'Not counted',
            detected: r.detected, saved: '', basis: r.note, est: false
          }]).map((i) => ({ what: i.what, action: i.action, detected: i.detected, saved: i.saved, basis: i.basis, est: i.est ? 'est.' : '', estShow: i.est ? 'inline' : 'none' }))
        }));
      }
    }
    // The Buildings and Vendors ask bars, in the instance's words.
    if (s.view === 'buildings' && c.asks) {
      out.abPh = 'Ask anything about your ' + (nB !== null ? plural(nB, 'building', 'buildings') : 'buildings') + ' — schema, documents, open risk';
      out.abChips = c.asks.buildings.map((q) => ({ label: q, run: () => this.askScoped(q) }));
    }
    if (s.view === 'vp' && c.asks) out.abChips = c.asks.vp.map((q) => ({ label: q, run: () => this.askScoped(q) }));
    return out;
  }
};
