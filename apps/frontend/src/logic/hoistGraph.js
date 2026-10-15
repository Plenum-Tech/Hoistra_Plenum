// hoistGraph — the Compliance Graph report (12 Oct 2026).
//
// A permanent report in the Compliance Engine instance: in the navigator of the user view (an
// admin or a user) and of the admin view (Hussain, 12 Oct 2026). It is the prototype's hospital
// "Building & Hoist Graph" page (hoistra-frontend-changes/02-compliance-engine,
// hospital-pages.html), card for card, drawn from the live compliance register instead of the
// Rimaal hospital:
//   prototype                     here
//   floor (L3–L5, L2 …)           building
//   area (In-patient wards …)     regulated area — the pack's trade: Fire, Gas, Electrical …
//   obligation · standard         the pack's certificate type · its regulation
//   vendor                        the vendor named on the certificate that discharges it
// A certificate names a building and nothing below it — no floor, ward or plant room — so a
// physical area is not something the data holds; the regulated area is the real grouping.
//
// Left off on purpose: "Hoist a building" (Hussain asked), and the prototype's figures. Every
// number is counted from ccLive (complianceLive.js shapeLiveCompliance); before that answers
// the page shows dashes and says it is reading.
//
// The pure model is tested in test/hoistGraph.test.mjs; the methods are mixed into
// HoistraLogic.prototype and `this` is the controller.
import { accountCanIngest } from './auth.js';
import { instanceOf, usesInstances } from './instances.js';

const plural = (n, one, many) => n + ' ' + (n === 1 ? one : many);
const isBuilding = (b) => b && b.name && !/no building on certificate/i.test(b.name);
const RANK = { risk: 0, warn: 1, ok: 2, none: 3 };
export const HG_LABEL = { risk: 'Out of compliance', warn: 'At risk', ok: 'Compliant', none: 'No obligations on record' };
const TONE = {
  risk: { color: 'var(--st-risk)', bg: 'var(--st-risk-bg)' },
  warn: { color: 'var(--st-warn)', bg: 'var(--st-warn-bg)' },
  ok: { color: 'var(--st-ok)', bg: 'var(--st-ok-bg)' },
  none: { color: 'var(--color-neutral-500)', bg: 'var(--color-neutral-900)' }
};
export const HG_NAME = 'Compliance Graph';

// Whether the vendor who issued a certificate may do that work. The prototype's rule: a
// lapsed accreditation or a block makes the vendor not eligible, one expiring inside 30 days
// puts it at risk. A name that is not in the vendor list cannot be checked at all.
function vendorState(c, v) {
  // A retired vendor's certificate is history (12 Oct 2026): it stays on record, but nobody on
  // record renews it.
  if (c.vendorDeleted) return { ok: false, line: c.vendor + ' — deleted; no current vendor for this work' };
  if (!c.vendorOnRecord) return { ok: false, line: c.vendor + ' is not in your vendor list' };
  // On record but gone from the register's vendors: deleted from this tab (vendorDelete.js
  // vdlDone) before the re-read lands. Not a vendor to rely on (review, 12 Oct 2026).
  if (!v) return { ok: false, line: c.vendor + ' is no longer in your vendor list' };
  if (v.block === 'Blocked') return { ok: false, line: c.vendor + ' is not eligible: blocked' + (v.blockedType ? ' on ' + v.blockedType : '') };
  if (v.worst === 'Lapsed') return { ok: false, line: c.vendor + ' is not eligible: an accreditation has lapsed' };
  if (v.worst === '<30d') return { ok: false, line: c.vendor + ': an accreditation expires inside 30 days' };
  if (v.noAccreditations) return { ok: true, line: c.vendor + ': no accreditations on file' };
  return { ok: true, line: c.vendor + ': eligible' };
}

function certState(c) {
  if (c.days < 0) return { tone: 'risk', line: 'Lapsed ' + Math.abs(Math.round(c.days)) + 'd ago' };
  if (/suspect/i.test(String(c.auth || ''))) return { tone: 'risk', line: 'Failed its authenticity check' };
  if (c.days <= 30) return { tone: 'warn', line: 'Expires in ' + Math.round(c.days) + 'd' + (c.exp ? ' (' + c.exp + ')' : '') };
  return { tone: 'ok', line: isFinite(c.days) ? 'In date to ' + c.exp : 'No expiry date' };
}

// Pure: the live register (ccLive) → the graph. null → { answered: false }.
export function hoistGraphModel(reg) {
  if (!reg) return { answered: false };
  const certs = reg.certs || [];
  const types = reg.packTypes || {};
  const packs = reg.packBuilding || {};
  const vByName = {};
  (reg.vendors || []).forEach((v) => { vByName[v.name] = v; });
  const bCerts = certs.filter((c) => c.kind === 'building');

  const buildings = (reg.buildings || []).filter(isBuilding).map((b) => {
    const own = bCerts.filter((c) => c.holder === b.name);
    // The certificate that discharges a type is the one that runs longest; an older issue of
    // the same type is superseded by it, not a second obligation.
    const best = {};
    own.forEach((c) => { if (!best[c.code] || c.days > best[c.code].days) best[c.code] = c; });
    const packCodes = packs[b.cc] && packs[b.cc].length ? packs[b.cc] : null;
    const codes = (packCodes || b.gapCodes || []).slice();
    Object.keys(best).forEach((k) => { if (codes.indexOf(k) < 0) codes.push(k); });

    const rows = codes.map((code, i) => {
      const ty = types[code] || {};
      const c = best[code] || null;
      const name = ty.name || (c && c.nm) || String(code).replace(/_/g, ' ');
      const base = { code: code, i: i, area: ty.trade || (c && c.trade) || 'Other', t: ty.reg ? name + ' · ' + ty.reg : name };
      if (!c) {
        return Object.assign(base, { tone: 'risk', vendor: null, held: false, v: 'No certificate',
          title: 'No certificate on record' + (ty.issuer ? ' · Issued by: ' + ty.issuer : '') });
      }
      const cs = certState(c);
      const vs = c.vendor ? vendorState(c, vByName[c.vendor]) : null;
      const tone = cs.tone === 'ok' && vs && !vs.ok ? 'warn' : cs.tone;
      return Object.assign(base, { tone: tone, vendor: c.vendor || null, held: true, v: c.vendor || '—',
        title: [(c.number ? 'Certificate ' + c.number : 'Certificate on record') + (c.draft ? ' (draft, awaiting confirmation)' : ''), cs.line, vs ? vs.line : 'No vendor named on the certificate'].join(' · ') });
    });

    // The building's regulated areas, worst first; inside one, the pack's own order.
    const byArea = {};
    rows.forEach((r) => { (byArea[r.area] = byArea[r.area] || []).push(r); });
    const areas = Object.keys(byArea).map((name) => {
      const rs = byArea[name];
      const vendors = {};
      rs.forEach((r) => { if (r.vendor) vendors[r.vendor] = true; });
      const state = rs.some((r) => r.tone === 'risk') ? 'risk' : rs.some((r) => r.tone === 'warn') ? 'warn' : 'ok';
      const held = rs.filter((r) => r.held).length;
      return {
        name: name, state: state, label: HG_LABEL[state], rows: rs, first: rs[0].i, held: held, total: rs.length,
        use: held + ' of ' + rs.length + ' on record',
        sub: plural(rs.length, 'obligation', 'obligations') + ' · ' + plural(Object.keys(vendors).length, 'vendor', 'vendors')
      };
    }).sort((a, z) => RANK[a.state] - RANK[z.state] || a.first - z.first);

    const vendors = {};
    own.forEach((c) => { if (c.vendor) vendors[c.vendor] = true; });
    return {
      name: b.name, areas: areas, packMissing: !packCodes, vendors: Object.keys(vendors), certs: own.length,
      obligations: rows.length,
      state: areas.some((a) => a.state === 'risk') ? 'risk' : areas.some((a) => a.state === 'warn') ? 'warn' : areas.length ? 'ok' : 'none'
    };
  }).sort((a, z) => a.name.localeCompare(z.name));

  const inGraph = {};
  buildings.forEach((b) => { inGraph[b.name] = true; });
  const graphCerts = bCerts.filter((c) => inGraph[c.holder]);
  const vend = {};
  // A retired vendor is not one of the company's vendors, as Home and Compliance count them.
  graphCerts.forEach((c) => { if (c.vendor && !c.vendorDeleted) vend[c.vendor] = true; });
  const accreditations = certs.filter((c) => c.kind === 'vendor' && vend[c.holder]).length;
  return {
    answered: true,
    buildings: buildings,
    orphans: bCerts.length - graphCerts.length,
    stats: [
      ['Areas', buildings.reduce((q, b) => q + b.areas.length, 0)],
      ['Obligations', buildings.reduce((q, b) => q + b.obligations, 0)],
      ['Certificates', graphCerts.length],
      ['Vendors', Object.keys(vend).length],
      ['Accreditations', accreditations]
    ].map((x) => ({ l: x[0], v: String(x[1]) }))
  };
}

const STAT_LABELS = ['Areas', 'Obligations', 'Certificates', 'Vendors', 'Accreditations'];

// The Compliance Engine is the instance that carries this report, in either view.
function hgAllowed(s) {
  const inst = instanceOf(s.instance);
  return !!s.signedIn && usesInstances(s.account, s.viewOrgId) && !!inst && inst.reports.indexOf('graph') > -1;
}

// ── controller methods ──────────────────────────────────────────────────
export const hoistGraphMethods = {
  // One model per register: the navigator's count and the page read the same one.
  hgModel() {
    const reg = this.state.ccLive || null;
    if (!this._hgMemo || this._hgMemo.reg !== reg) this._hgMemo = { reg: reg, m: hoistGraphModel(reg) };
    return this._hgMemo.m;
  },

  hgOpen() {
    if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
    this.setState({ view: 'hg', navOpen: true, detail: null, queueOpen: false });
  },

  // The navigator entry, placed by the Compliance Engine (instances.js instVals) in both views.
  // Its count is the buildings with an area out of compliance, once the register has answered.
  hgNavItem(s) {
    const m = this.hgModel();
    const n = m.answered ? m.buildings.filter((b) => b.state === 'risk').length : 0;
    const active = s.view === 'hg';
    return {
      key: 'hoist_graph', label: HG_NAME, icon: 'ph-graph',
      count: n ? String(n) : '', show: n ? 'flex' : 'none',
      color: active ? 'var(--color-accent)' : 'var(--color-neutral-300)',
      chip: active ? 'var(--color-accent-900)' : 'transparent',
      click: () => this.hgOpen()
    };
  },

  // Map an area to its obligations: the question goes to the orchestrator, answered in the
  // dock beside the page, from the same register.
  hgAskMap() {
    const q = 'Map each regulated area of my buildings to its compliance obligations: for every obligation the country pack attaches, name the certificate that discharges it, the vendor who issued it and whether that vendor is eligible, and list what is missing.';
    this.ccChatReset();
    this.setState({ flow: null, flowDone: '' });
    return this.ccAsk(q);
  },

  // Update the graph: the real document upload. A certificate filed against a building is
  // what adds a certificate, a vendor and its accreditations to the graph. Recorded as
  // "Ingest documents" so Recent tasks re-opens the upload, not the older scripted
  // "Update the graph" panel (renderVals ug), which reads sample buildings.
  hgUpdate() {
    this.ccChatReset();
    this.orchWith('Ingest documents', HG_NAME, 'ingest', { declFor: '', ingExpect: '' });
  },

  // Laid out to be read without scrolling (Hussain, 12 Oct 2026: "I have to scroll too much"):
  // the whole estate as one table — a row per building, a column per area, each cell the
  // certificates held of those required — and under it the prototype's area cards for the one
  // building picked. The first building is open to start with.
  hgVals(s) {
    if (!(s.view === 'hg' && hgAllowed(s))) return { isHG: false };
    const m = this.hgModel();
    const all = m.answered ? m.buildings : [];
    const picked = all.find((b) => b.name === s.hgBuilding) || all[0] || null;
    // The columns: every area any building has, in the pack's order.
    const firstOf = {};
    all.forEach((b) => b.areas.forEach((a) => { if (firstOf[a.name] === undefined || a.first < firstOf[a.name]) firstOf[a.name] = a.first; }));
    const cols = Object.keys(firstOf).sort((a, z) => firstOf[a] - firstOf[z] || a.localeCompare(z));
    const outOf = (b) => b.areas.filter((a) => a.state === 'risk').length;
    const card = (a) => ({
      name: a.name, use: a.use, label: a.label, color: TONE[a.state].color, bg: TONE[a.state].bg, sub: a.sub,
      focus: !!picked && s.hgArea === a.name,
      rows: a.rows.map((r) => ({ t: r.t, v: r.v, color: TONE[r.tone].color, title: r.title }))
    });
    const can = accountCanIngest(s);
    return {
      isHG: true,
      hgCrumb: (s.viewOrgId && s.viewOrgName) || (s.account && s.account.organization_name) || 'Your estate',
      hgTitle: HG_NAME,
      hgBlurb: 'Every regulated area of your buildings as a node, with the obligations its regulations attach to it, the certificate that discharges each one, and the vendor who issued it.',
      hgActions: [
        can ? { label: 'Update the graph', run: () => this.hgUpdate() } : null,
        { label: 'Map an area to its obligations', run: () => this.hgAskMap() }
      ].filter(Boolean),
      hgStats: m.answered ? m.stats : STAT_LABELS.map((l) => ({ l: l, v: '—' })),
      hgStatus: m.answered ? ''
        : s.ccLoading ? 'Reading the compliance register…'
          : s.ccError ? 'The compliance register could not be read: ' + s.ccError
            : 'The compliance register has not answered yet.',
      hgRetryShow: !m.answered && !s.ccLoading,
      hgRetry: () => this.ccLoad(),
      hgNone: m.answered && !all.length
        ? 'No building has a certificate or an active compliance pack yet.' + (can ? ' Upload a certificate against a building to start its graph.' : '')
        : '',
      hgMatrix: all.length ? {
        cols: cols,
        rows: all.map((b) => {
          const out = outOf(b);
          return {
            label: b.name, on: !!picked && picked.name === b.name,
            out: out ? out + ' out' : 'Clear', outColor: out ? 'var(--st-risk)' : 'var(--st-ok)',
            pick: () => this.setState({ hgBuilding: b.name, hgArea: null }),
            cells: cols.map((col) => {
              const a = b.areas.find((x) => x.name === col);
              if (!a) return { v: '—', color: 'var(--color-neutral-600)', bg: 'transparent', title: col + ': no obligation at ' + b.name, pick: null };
              return {
                v: a.held + '/' + a.total, color: TONE[a.state].color, bg: TONE[a.state].bg,
                title: col + ' at ' + b.name + ': ' + a.label.toLowerCase() + ' · ' + a.use,
                pick: () => this.setState({ hgBuilding: b.name, hgArea: col })
              };
            })
          };
        })
      } : null,
      hgPicked: picked ? {
        label: picked.name,
        line: plural(picked.areas.length, 'area', 'areas') + ' · ' + (outOf(picked) ? outOf(picked) + ' out of compliance' : 'none out of compliance'),
        cards: picked.areas.map(card)
      } : null,
      hgOrphans: m.answered && m.orphans
        ? plural(m.orphans, 'building certificate names', 'building certificates name') + ' no building, so ' + (m.orphans === 1 ? 'it sits' : 'they sit') + ' outside the graph.'
        : '',
      hgFoot: 'Graph: building → area → obligation → certificate → vendor → accreditation. An area\'s obligations come from its building\'s compliance pack (fire, gas, electrical, water…), so a new building inherits them the moment its pack is active. Certificates name a building, not a floor or a room, so each building\'s areas are its regulated areas.'
    };
  }
};
