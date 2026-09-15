// assetsCondition — the Assets page's condition tree and instrumented-asset block, on real
// data only. This replaced logic/assets.js, which produced the same screen out of a
// hardcoded fixture (src/data/hoistra-assets.js): nine buildings and nineteen assets that
// never came from a backend, so no allocation or company scope could reach them and every
// tenant saw the same portfolio.
//
// The design is unchanged. What changed is that a panel with no backend behind it now says
// so, instead of printing a number nobody can trace:
//
//   REAL, and what feeds it
//     building EUI vs its reference  — buildingsLive.bldData(): euiN, benchN, benchmark
//                                      standard, deviation (GET /api/energy/buildings)
//     sections and their own EUI     — GET /api/energy/sections. A section carries its OWN
//                                      reference, which is the point of it: a server room
//                                      read against an office benchmark looks like a
//                                      catastrophe and a car park like a triumph.
//                                      eui null + measured:false means NOT METERED, which
//                                      is a different claim from consumed nothing.
//     asset value at risk            — GET /api/energy/assets/value-at-risk, from
//                                      replacement_value / design_life_years /
//                                      wear_coefficient on the asset. Assets missing an
//                                      input are counted as not computable, never as zero.
//     the vendor who holds the asset — assets.vendor_id, resolved on the asset itself
//                                      (GET /api/energy/assets/{id}/intelligence)
//     reading bands                  — plenum_cafm.asset_reading_bands, served with each
//                                      reading as band_lo / band_hi / state
//     failure assessment             — plenum_cafm.asset_failure_assessments: a named RULE
//                                      over recorded signals with its drivers, NOT a fitted
//                                      model — is_fitted_model:false and null accuracy /
//                                      precision / recall, deliberately
//     anomaly attributed to an asset — energy_anomalies.asset_id (GET /api/energy/anomalies)
//     open work orders               — work_orders.asset_id, a real join since 47b949e
//     asset condition and its date   — health_score (integer), condition_score,
//                                      condition_updated_at, warranty_expiry
//     live readings                  — plenum_cafm.asset_readings
//
//   THE ONE GAP LEFT
//     which section an asset is in   — assets.section_id exists on the table but is not on
//                                      AssetResponse, so the link cannot be made in bulk;
//                                      only GET /api/energy/assets/{id}/intelligence knows
//                                      it, one asset at a time. Sections therefore render
//                                      with their real bars, and assets sit under "not yet
//                                      linked to a section" until that field is exposed.
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { healthBand } from './assetsLive.js';
import { workOrderApi } from '../api/workOrder.js';
import { energyApi } from '../api/energy.js';
import { isStaleScope } from '../api/client.js';
import { t } from './constants.js';

// What is still unknown says so. These are claims about the data, not decoration.
export const NOT_METERED = 'Not metered — no sub-meter or no area on record, which is not the same as no consumption';
export const NO_SECTION_LINK = 'Not yet linked to a section';
export const NO_SECTION_LINK_WHY = 'assets.section_id exists on the table but AssetResponse does not return it, so the link cannot be made in bulk';
export const NOT_COMPUTABLE = 'Not computable — needs a replacement value, a design life and an install date';
export const UNGRADED = 'Ungraded — no band defined for this reading type';
export const RULE_NOT_MODEL = 'A named rule over recorded signals, not a fitted model';

const OPEN_WO = new Set(['pending_approval', 'preparing', 'prepared', 'active']);
const DAY = 86400000;

// Open anomalies keyed by the asset they are attributed to. Rows with no asset_id are the
// common case (a meter with no asset link); they are dropped rather than spread over the
// assets in the building, which would invent an attribution the data does not make.
export function anomaliesByAsset(rows) {
  const by = {};
  (rows || []).forEach((r) => {
    if (!r || !r.asset_id || r.status === 'resolved' || r.status === 'closed') return;
    const k = String(r.asset_id);
    (by[k] = by[k] || []).push(r);
  });
  Object.keys(by).forEach((k) => by[k].sort((p, q) => new Date(p.detected_at || 0) - new Date(q.detected_at || 0)));
  return by;
}

export function anomalyAgeDays(row, now) {
  const d = row && row.detected_at ? new Date(row.detected_at) : null;
  if (!d || isNaN(d)) return null;
  return Math.max(0, Math.round(((now || Date.now()) - d.getTime()) / DAY));
}

// The condition rule, on real signals. Energy: the building is over its reference by more
// than the user's threshold, and an anomaly is attributed to this asset. Both together are
// a threat, one alone is a watch — the rule the page has always stated. health_score is a
// second, independent real signal, so it can raise a band but never lower one: an asset
// scored 20 is a threat whatever the energy says, and an asset with no score is not
// evidence of health.
export function conditionOf({ deviation, pct, anomaly, anomalyDays, weeks, healthScore }) {
  const over = typeof deviation === 'number' && deviation > pct;
  const persistent = !!anomaly && typeof anomalyDays === 'number' && anomalyDays / 7 >= weeks;
  const hb = healthBand(typeof healthScore === 'number' ? healthScore : null).cond;
  let cond = 'ok', kind = null;
  if (over && anomaly) { cond = 'threat'; kind = 'threat'; }
  else if (over) { cond = 'watch'; kind = 'zone'; }
  else if (persistent) { cond = 'watch'; kind = 'persist'; }
  if (hb === 'threat') { cond = 'threat'; kind = kind || 'health'; }
  else if (hb === 'watch' && cond === 'ok') { cond = 'watch'; kind = 'health'; }
  const unscored = hb === 'unscored';
  return { cond, kind, over, persistent, unscored };
}

// Which assets earn a per-asset intelligence call. Flagged first; then the ones a person
// would look at anyway — most critical, then worst scored — because a portfolio entirely
// under reference flags nothing, and that is exactly when someone still wants to see what
// their plant is doing. Capped: this is one round trip per asset.
const CRIT_RANK = { high: 0, medium: 1, low: 2 };
const COND_RANK = { threat: 0, watch: 1, ok: 2 };

export function seedOrder(rows, cap) {
  return (rows || []).slice().sort((p, q) => {
    const c = (COND_RANK[p.cond] ?? 3) - (COND_RANK[q.cond] ?? 3);
    if (c) return c;
    const pc = CRIT_RANK[String((p.a && p.a.criticality) || '').toLowerCase()] ?? 3;
    const qc = CRIT_RANK[String((q.a && q.a.criticality) || '').toLowerCase()] ?? 3;
    if (pc !== qc) return pc - qc;
    // An unscored asset is unknown, not worst — it sorts after anything with a real score.
    const ps = typeof (p.a && p.a.health_score) === 'number' ? p.a.health_score : Infinity;
    const qs = typeof (q.a && q.a.health_score) === 'number' ? q.a.health_score : Infinity;
    return ps - qs;
  }).slice(0, cap || 8).map((x) => x.a.asset_id).filter(Boolean);
}

const TONE_OF = { threat: 'risk', watch: 'warn', ok: 'ok' };
const LABEL_OF = { threat: 'Threat', watch: 'Watch', ok: 'In control' };

export const assetsConditionMethods = {

  // Locations and open anomalies — the two reads the condition tree needs that the live
  // register does not already hold. Both are optional: the tree degrades to buildings →
  // "Location not set" and to no anomaly attribution rather than failing.
  async asCondLoad() {
    if (this._asCondLoading) return;
    this._asCondLoading = true;
    this.setState({ asCondLoading: true });
    // A read disowned by a company switch (api/client.js stamps staleScope) must NOT be
    // softened into an "unreachable" row: doing that writes empty arrays and a false error
    // over the registers the correctly-scoped read is about to fill. It is rethrown so the
    // catch below can abandon the whole load, which is what resetLiveData() expects.
    let stale = null;
    const soft = (p) => p.catch((e) => {
      if (isStaleScope(e)) { stale = e; return { __stale: true }; }
      return { __err: (e && e.message) || String(e) };
    });
    try {
      const [locs, anoms, sections, var_] = await Promise.all([
        soft(workOrderApi.locations({ limit: 500 })),
        soft(energyApi.anomalies({ limit: 500 })),
        soft(energyApi.sections()),
        soft(energyApi.assetValueAtRisk())
      ]);
      if (stale) throw stale;
      const list = (v, ...keys) => Array.isArray(v) ? v
        : keys.map((k) => v && Array.isArray(v[k]) ? v[k] : null).find(Boolean)
        || (v && Array.isArray(v.data) ? v.data : []);
      this.setState({
        asLocations: list(locs, 'locations'), asLocationsError: locs && locs.__err ? locs.__err : '',
        asAnoms: list(anoms, 'anomalies'), asAnomsError: anoms && anoms.__err ? anoms.__err : '',
        asSections: list(sections, 'sections'), asSectionsSummary: (sections && sections.summary) || null,
        asSectionsError: sections && sections.__err ? sections.__err : '',
        asVar: var_ && !var_.__err ? var_ : null, asVarError: var_ && var_.__err ? var_.__err : '',
        asCondLoading: false, asCondLoadedAt: new Date().toISOString()
      });
    } catch (e) {
      if (isStaleScope(e)) return;
      this.setState({ asCondLoading: false, asCondError: (e && e.message) || String(e) });
    } finally {
      this._asCondLoading = false;
    }
  },

  // The instrumented-asset block is seeded from the flagged assets, through the SCOPED
  // per-asset route. It used to come from the connector's GET /asset-readings, which is
  // dropped for two independent reasons: its model maps asset_readings.unit_id, a column the
  // live table does not have (500 on every call, killing every route through that model),
  // and the route carries no scope dependency at all — it answered an unauthenticated
  // request, where the ops-intelligence routes correctly return 401. Bounded to the assets
  // a person is actually looking at rather than one call per asset in the portfolio.
  async asCondSeedIntel(ids) {
    const todo = (ids || []).filter((id) => id && !(this.state.asIntel || {})[id]).slice(0, 8);
    if (!todo.length) return;
    await Promise.all(todo.map((id) => this.asCondLoadIntel(id)));
  },

  // One asset's full intelligence, read when its drawer opens: section, vendor, the
  // value-at-risk arithmetic with its basis in words, readings against their bands, and the
  // failure assessment with its drivers. One call per asset, so it is never eager.
  async asCondLoadIntel(assetId) {
    if (!assetId || (this.state.asIntel || {})[assetId]) return;
    this.setState((p) => ({ asIntel: Object.assign({}, p.asIntel, { [assetId]: { loading: true } }) }));
    try {
      const res = await energyApi.assetIntelligence(assetId);
      this.setState((p) => ({ asIntel: Object.assign({}, p.asIntel, { [assetId]: res || {} }) }));
    } catch (e) {
      this.setState((p) => ({ asIntel: Object.assign({}, p.asIntel, { [assetId]: { error: (e && e.message) || String(e) } }) }));
    }
  },

  /* Asset condition. Two thresholds the user owns, applied to real numbers: how far over
     its reference a building must be, and how long an anomaly must have been open. */
  asVals(s) {
    const pct = s.asPct, wks = s.asWeeks;
    const assets = s.asLive || [];
    const now = Date.now();
    const bldByEui = {};
    // A building row carries up to three keys and which one is filled depends on whether
    // the register is rooted on `buildings` or on `sites`: buildingId is null for a
    // site-rooted row, and the energy tables key on the site UUID where the CMMS tables key
    // on site_id. assets.building_id can match any of them, so all three are indexed —
    // keying on building_id alone left in-scope buildings rendering as "Building 95649fde…",
    // a name nobody has ever seen, on a building the caller does own.
    (this.bldData ? this.bldData() : []).forEach((b) => {
      [b.buildingId, b.uuid, b.siteId, b.id].forEach((k) => { if (k && !bldByEui[String(k)]) bldByEui[String(k)] = b; });
    });
    // One canonical key per building. Assets, sections and the register each carry whichever
    // of the three keys their own table holds, so grouping on the raw id splits one building
    // into several buckets — a building showing "No sections on record" while its sections
    // sit one bucket away under a different key for the same place. An id the register does
    // not know keys on itself, which keeps it visible instead of silently merging it.
    // A building the register does not return is not nameless: the locations register and the
    // sections response both carry the building's name beside its id. Naming it from those is
    // the difference between a row a person recognises and a truncated uuid — while its EUI
    // and benchmark stay honestly absent, since only the register has those.
    // The caller's own scope, and ONLY when the page is answering for their own company.
    // Viewing as another company (superAdmin.js sets viewOrgId) makes the account's
    // allocation a statement about a different tenant, so it is withheld entirely rather
    // than applied to rows it does not describe.
    const viewingOther = !!s.viewOrgId;
    const acct = s.account || {};
    const myScope = viewingOther
      ? { buildings: [], buildingIds: null, allBuildings: true }
      : { buildings: Array.isArray(acct.buildings) ? acct.buildings : [],
          buildingIds: acct.building_ids, allBuildings: !!acct.all_buildings };

    const nameById = {};
    // GET /me's own list — for a restricted caller, exactly the buildings they hold, with
    // names the buildings register did not return.
    //
    // NOT while viewing as another company. resetLiveData() clears every register on a
    // company switch but leaves the account alone, because the signed-in person has not
    // changed — so the viewer's own allocation is still in state, describing a different
    // tenant. Using it here would print the viewer's building names over the viewed
    // company's rows wherever an id collided.
    (myScope.buildings).forEach((b) => {
      if (b && typeof b === 'object' && b.id && b.name) nameById[String(b.id)] = b.name;
    });
    (s.asLocations || []).forEach((l) => { if (l && l.building_id && l.building) nameById[String(l.building_id)] = l.building; });
    (s.asSections || []).forEach((x) => { if (x && x.building_id && x.building) nameById[String(x.building_id)] = x.building; });

    const canonKey = (id) => {
      if (!id) return '__none';
      const b = bldByEui[String(id)];
      return b ? String(b.buildingId || b.uuid || b.siteId || b.id) : String(id);
    };
    const locById = {};
    (s.asLocations || []).forEach((l) => { if (l && l.location_id) locById[String(l.location_id)] = l; });
    const anomBy = anomaliesByAsset(s.asAnoms);
    // work_orders.asset_id is on WorkOrderResponse since 47b949e, so this is a real join.
    // The name fallback stays for rows imported before the column was populated; it is the
    // approximation the whole page used to run on, now the exception rather than the rule.
    const openWo = {}, openWoByName = {};
    (s.asLiveWos || []).forEach((w) => {
      if (!w || !OPEN_WO.has(w.status)) return;
      if (w.asset_id) openWo[String(w.asset_id)] = (openWo[String(w.asset_id)] || 0) + 1;
      else if (w.asset) { const k = String(w.asset).trim().toLowerCase(); openWoByName[k] = (openWoByName[k] || 0) + 1; }
    });
    const woCount = (a) => (openWo[String(a.asset_id)] || 0) + (openWoByName[String(a.asset_name || '').trim().toLowerCase()] || 0);

    const evalA = (a) => {
      const b = a.building_id ? bldByEui[String(a.building_id)] : null;
      const deviation = b && typeof b.deviation === 'number' ? b.deviation
        : (b && typeof b.euiN === 'number' && typeof b.benchN === 'number' && b.benchN)
          ? Math.round(((b.euiN - b.benchN) / b.benchN) * 100) : null;
      const anoms = anomBy[String(a.asset_id)] || [];
      const anomaly = anoms[0] || null;
      const anomalyDays = anomaly ? anomalyAgeDays(anomaly, now) : null;
      const score = typeof a.health_score === 'number' ? a.health_score : null;
      const c = conditionOf({ deviation, pct, anomaly, anomalyDays, weeks: wks, healthScore: score });
      return { a, b, deviation, anomaly, anomalyDays, score, ...c };
    };

    // The caller's own allocation from GET /me decides what this page shows. It is the only
    // answer that comes from the account rather than from a register that has disagreed with
    // the asset read all day.
    //
    // Presentation, not security: an over-returning API has already put the rows on the wire
    // before anything here runs. So the number withheld is stated, never swallowed — a
    // non-zero count IS the evidence that /api/assets and /me disagree for one caller.
    const allowed = (!myScope.allBuildings && Array.isArray(myScope.buildingIds))
      ? new Set(myScope.buildingIds.map((id) => canonKey(id)))
      : null;
    const everything = assets.map(evalA);
    const all = allowed ? everything.filter((x) => allowed.has(canonKey(x.a.building_id))) : everything;
    const withheld = everything.length - all.length;
    const threats = all.filter((x) => x.cond === 'threat');
    const watches = all.filter((x) => x.cond === 'watch');
    const unscored = all.filter((x) => x.unscored);

    const f = s.filter;
    const shown = all.filter((x) => f === 'Threat' ? x.cond === 'threat'
      : f === 'Watch' ? x.cond === 'watch'
      : f === 'In control' ? x.cond === 'ok'
      : f === 'Not scored' ? x.unscored
      : f === 'Open work order' ? woCount(x.a) > 0
      : true);

    // Per-asset value at risk, keyed off the portfolio read. An asset absent from that list
    // is one the engine could not compute, not one worth nothing.
    const intel = s.asIntel || {};
    const varRows = {};
    ((s.asVar && s.asVar.assets) || []).forEach((r) => { if (r && r.asset_id) varRows[String(r.asset_id)] = r; });
    const varOf = (a) => varRows[String(a.asset_id)] || null;
    const money = (n) => typeof n === 'number'
      ? '£' + (Math.abs(n) >= 1000 ? (n / 1000).toFixed(Math.abs(n) >= 10000 ? 0 : 1) + 'k' : Math.round(n))
      : '—';

    const why = (x) => {
      const bits = [];
      if (x.deviation === null) bits.push('No EUI on record for this building, so the energy rule cannot run on it.');
      else if (x.over) bits.push('Building ' + (x.deviation > 0 ? '+' : '') + x.deviation + '% against its reference, over the ' + pct + '% threshold.');
      else bits.push('Building ' + (x.deviation > 0 ? '+' : '') + x.deviation + '% against its reference, inside the ' + pct + '% threshold.');
      if (x.anomaly) bits.push('An anomaly is attributed to this asset (' + (x.anomaly.anomaly_type || 'anomaly') + ', open ' + (x.anomalyDays === null ? 'unknown' : x.anomalyDays + ' days') + ').');
      else bits.push('No anomaly is attributed to this asset; energy_anomalies.asset_id is set only where a meter names the asset.');
      bits.push(x.score === null ? 'No health score on record — not the same as healthy.' : 'Health score ' + Math.round(x.score) + '/100.');
      return bits.join(' ');
    };

    const row = (x) => {
      const a = x.a, tone = t(TONE_OF[x.cond]);
      const loc = a.location_id ? locById[String(a.location_id)] : null;
      const nWo = woCount(a);
      return {
        id: a.asset_id, name: a.asset_name,
        cls: a.category_name || (a.category_id ? 'Category name not on file' : 'No category set'),
        vendor: (intel[a.asset_id] && intel[a.asset_id].asset && intel[a.asset_id].asset.vendor) || 'Open the asset to read its vendor',
        meta: [a.asset_code || null, a.category_name || null,
               a.installation_date ? 'installed ' + String(a.installation_date) : 'install date not on record',
               a.criticality ? a.criticality + ' criticality' : 'criticality not set',
               // An undated score reads as current when it may be years old, so the date
               // travels with it rather than being an extra somewhere else.
               a.condition_updated_at ? 'condition read ' + String(a.condition_updated_at).slice(0, 10) : 'condition never dated',
               a.warranty_expiry ? 'warranty to ' + String(a.warranty_expiry) : null,
               a.serial_number ? 'serial ' + a.serial_number : null].filter(Boolean).join(' · '),
        cond: LABEL_OF[x.cond], color: tone.color, bg: tone.bg,
        rail: x.cond === 'ok' ? 'var(--color-divider)' : tone.color,
        anomText: x.anomaly
          ? (x.anomaly.anomaly_type || 'Anomaly') + (typeof x.anomaly.financial_gbp === 'number' ? ' · £' + Math.round(x.anomaly.financial_gbp).toLocaleString('en-GB') : '')
            + (x.anomalyDays === null ? '' : ' · open ' + (x.anomalyDays / 7).toFixed(1) + ' wks')
          : 'No anomaly attributed',
        anomColor: x.anomaly ? 'var(--color-text)' : 'var(--color-neutral-500)',
        why: why(x),
        // Straight-line value against a wear-adjusted line; the gap between them today is
        // the loss the deviation is causing. Only assets carrying a replacement value, a
        // design life and an install date can be computed — the rest say so rather than
        // reporting a zero, which would read as "worth nothing" instead of "not priced".
        valNow: varOf(a) ? money(varOf(a).straight_line_value) : '—',
        valAfter: varOf(a) ? money(varOf(a).adjusted_value) : '—',
        valLoss: varOf(a) ? '−' + money(varOf(a).value_at_risk) : '—',
        valLossColor: varOf(a) && varOf(a).value_at_risk > 0 ? t('risk').color : 'var(--color-neutral-500)',
        valLine: varOf(a) ? (varOf(a).basis || (varOf(a).design_life_used_pct != null ? Math.round(varOf(a).design_life_used_pct) + '% of design life used' : '')) : NOT_COMPUTABLE,
        hist: [], histShow: 'none',
        invShow: x.anomaly ? 'inline-flex' : 'none',
        investigate: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.asCondOpenAnomaly(x.anomaly); },
        // assets.vendor_id now names who holds the asset, so an order has a recipient. The
        // vendor arrives with the per-asset intelligence, so the action reads it first.
        woPrimary: x.cond === 'threat', inspectPrimary: x.cond !== 'threat',
        wo: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.asCondAction(x, 'wo'); },
        inspect: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.asCondAction(x, 'inspect'); },
        woLabel: 'Raise work order', inspectLabel: 'Request inspection',
        openWorkOrders: nWo,
        locName: loc ? (loc.name || [loc.floor, loc.zone].filter(Boolean).join(' · ') || 'Unnamed location') : 'Location not set',
        open: () => this.asCondOpenAsset(x, loc, nWo)
      };
    };

    // Buildings → locations → assets. The location tier is real (assets.location_id); what
    // is missing there is energy, not identity, so its bar is an empty state.
    const byB = {};
    shown.forEach((x) => { const k = canonKey(x.a.building_id); (byB[k] = byB[k] || []).push(x); });
    const rank = { threat: 0, watch: 1, ok: 2 };

    // Billed against the building, from svc-operations-intelligence. Only the total goes on
    // the row; the per-asset lines stay in the drawer where there is room for them.
    const cost = s.asLiveCost || {};
    const costLine = (bk) => {
      const c = cost[bk];
      if (!c) return 'Open to read billed cost drivers';
      if (c.loading) return 'Reading cost drivers…';
      if (c.error) return 'Cost drivers unreachable — ' + c.error;
      const total = c.totals && typeof c.totals.billed === 'number' ? c.totals.billed : null;
      return total === null ? 'No billed lines attributed to plant on this building'
        : 'Billed against this building: ' + total.toLocaleString('en-GB', { style: 'currency', currency: 'GBP', minimumFractionDigits: 0 });
    };

    // Real sections, each judged against ITS OWN reference — the point of the record. A
    // section with no sub-meter or no area comes back with a null intensity and
    // measured:false, which means not metered, not zero consumption.
    const secsByB = {};
    (s.asSections || []).forEach((x) => { if (x && x.building_id) { const k = canonKey(x.building_id); (secsByB[k] = secsByB[k] || []).push(x); } });
    // AssetResponse does not return section_id, but the per-asset intelligence read does, so
    // an asset placed in a real section is one whose intelligence has come back. The rest
    // stay on the location row: an unread section is unknown, not absent.
    const sectionOfAsset = (a) => {
      const i = intel[a.asset_id];
      return i && i.asset && i.asset.section_id ? String(i.asset.section_id) : null;
    };
    const secsOf = (bk, xs) => (secsByB[bk] || []).map((sc) => {
      const mine = (xs || []).filter((x) => sectionOfAsset(x.a) === String(sc.section_id))
        .sort((p, q) => rank[p.cond] - rank[q.cond]);
      const measured = sc.measured !== false && typeof sc.eui_kwh_per_m2 === 'number';
      const ref = typeof sc.reference_eui_kwh_m2 === 'number' ? sc.reference_eui_kwh_m2 : null;
      const d = measured && typeof sc.deviation_pct === 'number' ? Math.round(sc.deviation_pct) : null;
      const over = d !== null && d > pct;
      const sk = bk + '|sec|' + sc.section_id;
      const sOpen = (s.asOpenS || []).indexOf(sk) > -1;
      return {
        name: sc.name + (sc.section_type ? ' · ' + sc.section_type : ''),
        eui: measured && ref !== null ? sc.eui_kwh_per_m2 + ' vs ' + ref + ' kWh/m²/yr' : 'Not metered',
        delta: d === null ? '—' : (d > 0 ? '+' : '') + d + '%',
        meter: measured
          ? (sc.meters ? sc.meters + (sc.meters === 1 ? ' sub-meter' : ' sub-meters') : 'no sub-meter')
            + (sc.reference_source ? ' · ' + sc.reference_source : '')
          : NOT_METERED,
        d: d === null ? -1e9 : d,
        open: sOpen, caret: sOpen ? 'ph-caret-down' : 'ph-caret-right',
        toggle: () => this.setState((p) => ({ asOpenS: (p.asOpenS || []).indexOf(sk) > -1 ? (p.asOpenS || []).filter((y) => y !== sk) : (p.asOpenS || []).concat([sk]) })),
        flags: (mine.length ? mine.length + (mine.length === 1 ? ' asset · ' : ' assets · ') : '')
          + (measured ? (over ? 'over its own reference' : 'within its own reference') : 'not metered'),
        state: !measured ? 'not metered' : over ? 'over reference' : 'in control',
        stColor: !measured ? 'var(--color-neutral-500)' : over ? t('risk').color : t('ok').color,
        stBg: !measured ? 'var(--color-bg)' : over ? t('risk').bg : t('ok').bg,
        pct: measured && ref ? Math.min(100, Math.round((sc.eui_kwh_per_m2 / (ref * 1.5)) * 100)) + '%' : '0%',
        refPct: Math.round(100 / 1.5) + '%',
        barColor: !measured ? 'var(--color-divider)' : over ? t('risk').color : t('ok').color,
        rows: mine.map(row)
      };
    });

    const groups = Object.keys(byB).map((bk) => {
      const xs = byB[bk];
      const b = bk === '__none' ? null : bldByEui[bk];
      // A building_id that matches none of the register's keys is a real condition worth
      // naming — the asset is in scope but its building is not in the list this account can
      // see. Printing a truncated uuid as if it were a building name hid that entirely.
      const named = !!(b || nameById[bk]);
      const name = b ? b.name
        : bk === '__none' ? 'Not linked to a building'
        : nameById[bk] || ('Building ' + bk.slice(0, 8) + '…');
      const allIn = all.filter((x) => canonKey(x.a.building_id) === bk);
      const bt = allIn.filter((x) => x.cond === 'threat').length;
      const bw = allIn.filter((x) => x.cond === 'watch').length;
      const bu = allIn.filter((x) => x.unscored).length;
      const hasEui = !!(b && typeof b.euiN === 'number' && typeof b.benchN === 'number' && b.benchN);
      const d = hasEui ? (typeof b.deviation === 'number' ? b.deviation : Math.round(((b.euiN - b.benchN) / b.benchN) * 100)) : null;
      const over = hasEui && d > 0;
      const bOpen = (s.asOpenB || []).indexOf(bk) > -1;
      const locKeys = [];
      xs.forEach((x) => { const lk = x.a.location_id ? String(x.a.location_id) : '__none'; if (locKeys.indexOf(lk) < 0) locKeys.push(lk); });
      return {
        key: bk, name: name, unnamed: !named && bk !== '__none', open: bOpen, caret: bOpen ? 'ph-caret-down' : 'ph-caret-right',
        // Opening a building lazily reads its billed cost drivers — real, and the only
        // money on this page. Eager fetching would be one round trip per building on load.
        toggle: () => {
          this.setState((p) => ({ asOpenB: (p.asOpenB || []).indexOf(bk) > -1 ? (p.asOpenB || []).filter((y) => y !== bk) : (p.asOpenB || []).concat([bk]) }));
          if (b && b.buildingId && !(this.state.asLiveCost || {})[bk]) this.asLiveLoadCost(bk);
        },
        eui: hasEui ? b.euiN + ' vs ' + b.benchN + ' kWh/m²/yr' : 'No EUI on record',
        delta: hasEui ? (d > 0 ? '+' : '') + d + '%' : '—',
        // Named from locations or sections, but absent from the buildings register — so there
        // is no benchmark for it, and no EUI. Say which, rather than "no regulation pack",
        // which would imply the building is known and merely unregulated.
        std: b && b.std ? b.std : (!b && bk !== '__none' && nameById[bk])
          ? 'Not in the buildings register — no EUI or benchmark for it'
          : 'no regulation pack',
        pct: hasEui ? Math.min(100, Math.round((b.euiN / (b.benchN * 1.5)) * 100)) + '%' : '0%',
        refPct: Math.round(100 / 1.5) + '%',
        barColor: !hasEui ? 'var(--color-divider)' : d > 10 ? t('risk').color : over ? t('warn').color : t('ok').color,
        state: !hasEui ? 'no EUI on record' : over ? 'above reference' : 'under reference',
        stColor: !hasEui ? 'var(--color-neutral-500)' : over ? t(d > 10 ? 'risk' : 'warn').color : t('ok').color,
        stBg: !hasEui ? 'var(--color-bg)' : over ? t(d > 10 ? 'risk' : 'warn').bg : t('ok').bg,
        counts: bt + ' threat · ' + bw + ' watch · ' + (allIn.length - bt - bw) + ' in control' + (bu ? ' · ' + bu + ' not scored' : ''),
        secLine: (() => {
          // A read that failed is not a read that came back empty. Reporting "none on record"
          // when the service never answered states a fact about the data with nothing behind it.
          if (s.asSectionsError) return 'Sections unreachable — ' + s.asSectionsError;
          if (s.asCondLoading) return 'Reading sections…';
          const scs = secsByB[bk] || [];
          const meas = scs.filter((x) => x.measured !== false && typeof x.eui_kwh_per_m2 === 'number');
          const over = meas.filter((x) => typeof x.deviation_pct === 'number' && x.deviation_pct > pct).length;
          return scs.length
            ? over + ' of ' + meas.length + ' metered sections over reference · ' + scs.length + ' on record'
            : 'No sections on record for this building';
        })(),
        actions: costLine(bk),
        actColor: 'var(--color-neutral-500)',
        sections: secsOf(bk, xs).concat(locKeys.map((lk) => {
          const sx = xs.filter((x) => !sectionOfAsset(x.a) && (x.a.location_id ? String(x.a.location_id) : '__none') === lk)
            .sort((p, q) => rank[p.cond] - rank[q.cond]);
          const loc = lk === '__none' ? null : locById[lk];
          const st = sx.filter((x) => x.cond === 'threat').length, sw = sx.filter((x) => x.cond === 'watch').length;
          const sk = bk + '|' + lk;
          const sOpen = (s.asOpenS || []).indexOf(sk) > -1;
          return {
            name: (loc ? (loc.name || [loc.floor, loc.zone].filter(Boolean).join(' · ') || 'Unnamed location') : 'Location not set')
              + ' · ' + NO_SECTION_LINK,
            // These assets are not sectionless — their section is unread. AssetResponse does
            // not carry section_id, so it takes a per-asset intelligence call to know.
            eui: '—', delta: '—', meter: NO_SECTION_LINK_WHY, d: -1e8,
            open: sOpen, caret: sOpen ? 'ph-caret-down' : 'ph-caret-right',
            toggle: () => this.setState((p) => ({ asOpenS: (p.asOpenS || []).indexOf(sk) > -1 ? (p.asOpenS || []).filter((y) => y !== sk) : (p.asOpenS || []).concat([sk]) })),
            hide: sx.length === 0,
            flags: (st || sw) ? [st ? st + ' threat' : null, sw ? sw + ' watch' : null].filter(Boolean).join(' · ') : sx.length + (sx.length === 1 ? ' asset' : ' assets'),
            state: 'no section link', stColor: 'var(--color-neutral-500)', stBg: 'var(--color-bg)',
            pct: '0%', refPct: Math.round(100 / 1.5) + '%', barColor: 'var(--color-divider)',
            rows: sx.map(row)
          };
        })).filter((x) => !x.hide).sort((p, q) => q.d - p.d)
      };
    }).sort((p, q) => {
      const pd = p.delta === '—' ? -1e9 : parseInt(p.delta, 10);
      const qd = q.delta === '—' ? -1e9 : parseInt(q.delta, 10);
      return qd - pd;
    });

    // A building no source can name is not a building we can put in the portfolio. Either
    // the register is incomplete or the asset read is over-returning; until that is settled,
    // these assets cannot be attributed to this account. They collapse into one row so the
    // SIZE of the discrepancy leads — eleven near-identical rows hide it, one row stating
    // "53 assets" does not. Nothing is removed: each building is a tier beneath.
    const identified = groups.filter((g) => !g.unnamed);
    const unknown = groups.filter((g) => g.unnamed);
    if (unknown.length) {
      const nAssets = unknown.reduce((n, g) => n + g.sections.reduce((m, sc) => m + sc.rows.length, 0), 0);
      const uOpen = (s.asOpenB || []).indexOf('__unidentified') > -1;
      identified.push({
        key: '__unidentified', unidentified: true,
        name: unknown.length + (unknown.length === 1 ? ' building' : ' buildings') + ' not in your buildings register',
        open: uOpen, caret: uOpen ? 'ph-caret-down' : 'ph-caret-right',
        toggle: () => this.setState((p) => ({ asOpenB: (p.asOpenB || []).indexOf('__unidentified') > -1 ? (p.asOpenB || []).filter((y) => y !== '__unidentified') : (p.asOpenB || []).concat(['__unidentified']) })),
        eui: 'No EUI on record', delta: '—',
        std: 'Their building_id matches nothing in the buildings register, the sections response or the locations register',
        pct: '0%', refPct: Math.round(100 / 1.5) + '%', barColor: 'var(--color-divider)',
        state: 'unidentified', stColor: 'var(--color-neutral-500)', stBg: 'var(--color-bg)',
        counts: nAssets + (nAssets === 1 ? ' asset' : ' assets') + ' that cannot be attributed to a building you hold',
        // The two counts are the evidence: for ONE caller, how many buildings each route
        // returned. That is what says whether the register is short or the asset read is long.
        secLine: 'For this account the buildings register returned '
          + (this.bldData ? this.bldData() : []).length
          + ((this.bldData ? this.bldData() : []).length === 1 ? ' building' : ' buildings')
          + ', while the assets name ' + Object.keys(byB).filter((k) => k !== '__none').length
          + '. Either the register is incomplete, or the asset read is returning beyond your allocation.',
        actions: '', actColor: 'var(--color-neutral-500)',
        sections: unknown.map((g) => Object.assign({}, g, {
          name: g.name, eui: '—', delta: '—', meter: 'building_id ' + g.key, d: 0,
          flags: g.counts, state: 'unidentified', stColor: 'var(--color-neutral-500)', stBg: 'var(--color-bg)',
          pct: '0%', refPct: Math.round(100 / 1.5) + '%', barColor: 'var(--color-divider)',
          rows: g.sections.reduce((acc, sc) => acc.concat(sc.rows), [])
        }))
      });
    }
    const outGroups = identified;

    // Seed the instrumented block without being asked: flagged assets first, then the most
    // critical and worst-scored. Telemetry only appears for those that actually have it.
    if (!this._asCondSeeded && all.length && !s.asCondLoading) {
      this._asCondSeeded = true;
      const seeds = seedOrder(all, 8);
      if (seeds.length) Promise.resolve().then(() => this.asCondSeedIntel(seeds));
    }

    const bump = (k, d, lo, hi) => () => this.setState((p) => ({ [k]: Math.max(lo, Math.min(hi, p[k] + d)) }));
    const vaR = s.asVar || null;
    const anomCovered = all.filter((x) => x.anomaly).length;
    const euiCovered = all.filter((x) => x.deviation !== null).length;
    return {
      asThreatN: threats.length,
      asAll: all,
      asCards: [
        { l: 'Threat', v: String(threats.length), s: 'building over reference + anomaly on asset, or health under 40', color: t('risk').color },
        { l: 'Watch', v: String(watches.length), s: all.filter((x) => x.kind === 'zone').length + ' shared building · ' + all.filter((x) => x.kind === 'persist').length + ' persistent anomaly', color: t('warn').color },
        // assets_not_computable is shown, never folded in as zero: a total that quietly
        // counts unpriced assets as worthless is a smaller number that looks like a real one.
        { l: 'Est. asset value at risk', v: vaR ? money(vaR.value_at_risk) : '—',
          s: vaR
            ? (vaR.assets_contributing || 0) + ' of ' + (vaR.assets_counted || 0) + ' assets contributing'
              + (vaR.assets_not_computable ? ' · ' + vaR.assets_not_computable + ' not computable' : '')
              // The engine ages an asset on the worst ANOMALY attributed to it, not on the
              // building EUI the condition rule reads. Without saying so, a portfolio under
              // reference everywhere reads as "in control" and "money at risk" at once.
              + ' · from each asset\u2019s worst anomaly, not the building EUI'
            : (s.asVarError ? 'Value engine unreachable — ' + s.asVarError : s.asCondLoading ? 'Reading…' : NOT_COMPUTABLE),
          color: t('risk').color },
        { l: 'In control', v: (all.length - threats.length - watches.length) + ' of ' + all.length, s: euiCovered + ' with a building EUI · ' + anomCovered + ' with an anomaly attributed', color: t('ok').color }
      ],
      asPct: pct + '%', asWeeks: String(wks), asWeeksUnit: wks === 1 ? 'week' : 'weeks',
      asPctDown: bump('asPct', -5, 5, 40), asPctUp: bump('asPct', 5, 5, 40),
      asWkDown: bump('asWeeks', -1, 1, 12), asWkUp: bump('asWeeks', 1, 1, 12),
      asGroups: outGroups,
      asWithheldShow: withheld ? 'block' : 'none',
      asWithheldText: withheld + (withheld === 1 ? ' asset was' : ' assets were') + ' returned by the asset register but sit'
        + ' outside the buildings your account holds, so they are not shown. Hiding them here does not stop the API'
        + ' returning them — a count above zero means /api/assets is scoping differently from your login.',
      asEmpty: groups.length ? 'none' : 'block',
      asEmptyText: s.asLiveLoading ? 'Reading plenum_cafm.assets…'
        : s.asLiveError ? 'Unreachable — ' + s.asLiveError
        : (s.asLive || []).length ? 'No asset in scope matches the “' + (f || 'All') + '” filter.'
        : 'No assets are in scope for your account.',
      asSummary: (() => {
        const idn = groups.filter((g) => !g.unnamed).length;
        const unk = groups.filter((g) => g.unnamed);
        const unkAssets = unk.reduce((n, g) => n + g.sections.reduce((m, sc) => m + sc.rows.length, 0), 0);
        return idn + (idn === 1 ? ' building · ' : ' buildings · ') + shown.length + ' of ' + all.length + ' assets'
          + (unk.length ? ' · ' + unkAssets + ' in ' + unk.length + ' buildings not in your register' : '')
          + (f === 'All' ? '' : ' · filter: ' + f)
          + ' · ' + (s.asSectionsError ? 'sections unreachable (' + s.asSectionsError + ')'
              : (s.asSections || []).length + ' sections on record, ' + (s.asSections || []).filter((x) => x.measured !== false && typeof x.eui_kwh_per_m2 === 'number').length + ' metered')
          + ' · buildings ranked by EUI against reference';
      })()
    };
  },

  asCondOpenAnomaly(an) {
    if (!an) return;
    this.setState({
      detail: {
        icon: 'ph-lightning', color: t('risk').color, module: 'Assets',
        title: an.anomaly_type || 'Anomaly', meta: 'energy_anomalies · ' + (an.id || ''),
        body: 'Attributed to this asset through energy_anomalies.asset_id, which svc-operations-intelligence sets from meters.asset_id.',
        chain: [
          { a: 'detected', t: an.detected_at || 'not recorded' },
          { a: 'status', t: an.status || 'open' },
          { a: 'excess', t: typeof an.annualised_excess_kwh === 'number' ? Math.round(an.annualised_excess_kwh).toLocaleString('en-GB') + ' kWh/yr' : 'not priced' },
          { a: 'cost', t: typeof an.financial_gbp === 'number' ? '£' + Math.round(an.financial_gbp).toLocaleString('en-GB') : 'not priced' }
        ],
        refinement: ''
      },
      detailFields: [], detailActions: []
    });
  },

  // `detail` is a snapshot in state, not a live view, so filling it from asIntel at open
  // time and firing the read without awaiting bakes "Reading…" into every field for good.
  // The read therefore rebuilds it, exactly as the work-history patch below already does.
  asCondOpenAsset(x, loc, nWo) {
    const a = x.a;
    const already = (this.state.asIntel || {})[a.asset_id];
    if (!already) {
      this.asCondLoadIntel(a.asset_id).then(() => {
        // The drawer may have moved on to another record by the time this lands.
        if (this._asCondDetailId === a.asset_id && this.state.detail && this.state.detail.title === a.asset_name) {
          this.asCondOpenAsset(x, loc, nWo);
        }
      });
    }
    const i = (this.state.asIntel || {})[a.asset_id] || {};
    const ia = i.asset || {}, iv = i.value || {}, fa = i.failure_assessment || null;
    const band = (r) => r.state === 'out_of_band' ? 'out of band' : r.state === 'in_band' ? 'in band' : 'ungraded';
    this.setState({
      detail: {
        icon: 'ph-cube', color: t(TONE_OF[x.cond]).color, module: 'Assets',
        title: a.asset_name, meta: (a.asset_code ? a.asset_code + ' · ' : '') + [a.manufacturer, a.model, a.serial_number].filter(Boolean).join(' · '),
        body: x.why || '',
        chain: [
          { a: 'building', t: x.b ? x.b.name : 'Unlinked' },
          { a: 'section', t: ia.section || (i.loading ? 'Reading…' : NO_SECTION_LINK) },
          { a: 'vendor', t: ia.vendor || (i.loading ? 'Reading…' : i.error ? 'Unreachable — ' + i.error : 'None on the asset record') },
          { a: 'health', t: x.score === null ? 'Not scored' : Math.round(x.score) + '/100' },
          // An undated score reads as current when it may be years old.
          { a: 'condition', t: a.condition_score == null ? 'Not graded'
              : a.condition_score + '/5 · ' + (a.condition_updated_at ? 'read ' + String(a.condition_updated_at).slice(0, 10) : 'never dated') },
          { a: 'criticality', t: a.criticality || 'Not set' },
          { a: 'installed', t: a.installation_date ? String(a.installation_date) : 'Not on record' },
          { a: 'warranty', t: a.warranty_expiry ? 'to ' + String(a.warranty_expiry) : 'Not on record' },
          { a: 'open work orders', t: String(nWo || 0) + ' · joined on work_orders.asset_id' },
          { a: 'value at risk', t: iv.value_at_risk != null ? '£' + Math.round(iv.value_at_risk).toLocaleString('en-GB') + ' — ' + (iv.basis || '') : (i.loading ? 'Reading…' : NOT_COMPUTABLE) },
          { a: 'remaining life', t: iv.remaining_life_months != null ? iv.remaining_life_months + ' months' : (i.loading ? 'Reading…' : 'Not computable') },
          // A named rule, not a fitted model: quoting accuracy without one invents evidence.
          { a: 'failure risk', t: fa
              ? Math.round((fa.probability || 0) * 1000) / 10 + '% · ' + (fa.method || '') + ' · ' + (fa.is_fitted_model ? 'fitted model' : RULE_NOT_MODEL)
              : (i.loading ? 'Reading…' : 'No assessment on record') },
          { a: 'drivers', t: fa && fa.drivers && fa.drivers.length
              ? fa.drivers.map((d) => d.signal + ' ' + d.value + ' (+' + Math.round((d.contribution || 0) * 1000) / 10 + '%)').join('; ')
              : (i.loading ? 'Reading…' : '—') },
          { a: 'readings', t: (i.readings && i.readings.length)
              ? i.readings.slice(0, 6).map((r) => r.reading_type + ' ' + r.value + (r.unit ? ' ' + r.unit : '') + ' · ' + band(r)).join('; ')
              : (i.loading ? 'Reading…' : 'None on file') },
          { a: 'work history', t: 'Reading svc-operations-intelligence…' }
        ],
        refinement: ''
      },
      detailFields: [], detailActions: []
    });
    this._asCondDetailId = a.asset_id;
    energyApi.assetWorkHistory(a.asset_id).then((hist) => {
      if (this._asCondDetailId !== a.asset_id || !this.state.detail || this.state.detail.title !== a.asset_name) return;
      const wos = (hist && Array.isArray(hist.work_orders)) ? hist.work_orders : [];
      const text = wos.length ? wos.slice(0, 5).map((w) => (w.wo_code || w.id || 'WO') + ' · billed ' + (w.billed != null ? w.billed : '—')).join('; ') : 'None on record for this asset.';
      this.setState((p) => p.detail && p.detail.title === a.asset_name
        ? { detail: Object.assign({}, p.detail, { chain: p.detail.chain.map((c) => c.a === 'work history' ? { a: c.a, t: text } : c) }) } : {});
    }).catch((e) => {
      if (this._asCondDetailId !== a.asset_id) return;
      this.setState((p) => p.detail && p.detail.title === a.asset_name
        ? { detail: Object.assign({}, p.detail, { chain: p.detail.chain.map((c) => c.a === 'work history' ? { a: c.a, t: 'Unreachable — ' + ((e && e.message) || e) } : c) }) } : {});
    });
  },

  // Raising an order or an inspection now has a recipient: assets.vendor_id names who holds
  // the asset. The vendor arrives with the per-asset intelligence, so read it, then draft.
  async asCondAction(x, kind) {
    const a = x.a;
    await this.asCondLoadIntel(a.asset_id);
    const i = (this.state.asIntel || {})[a.asset_id] || {};
    const vendor = (i.asset && i.asset.vendor) || null;
    if (!vendor) return this.flash('No vendor on this asset record — nothing to send to.');
    const isWo = kind === 'wo';
    const iv = i.value || {}, an = i.anomaly || {};
    const evidence = [
      // iotVals' remediate/replace actions carry no deviation, so this is guarded: a draft
      // addressed to a vendor must never read "(null%)".
      x.b ? '• Building ' + x.b.name + ' at ' + (x.b.euiN != null ? x.b.euiN : '—') + ' against a reference of '
          + (x.b.benchN != null ? x.b.benchN : '—') + ' kWh/m²/yr'
          + (typeof x.deviation === 'number' ? ' (' + (x.deviation > 0 ? '+' : '') + x.deviation + '%)' : ' (deviation not on record)') : null,
      (i.asset && i.asset.section) ? '• Section ' + i.asset.section + (i.asset.section_reference_eui != null ? ', reference ' + i.asset.section_reference_eui + ' kWh/m²/yr' : '') : null,
      an.open ? '• ' + an.open + ' open finding' + (an.open === 1 ? '' : 's') + ' attributed to this asset, worst ' + (an.worst_pct != null ? Math.round(an.worst_pct) + '%' : '—') + ', running ' + (an.weeks != null ? an.weeks + ' weeks' : 'unknown') : '• No anomaly attributed to this asset',
      iv.value_at_risk != null ? '• Value at risk £' + Math.round(iv.value_at_risk).toLocaleString('en-GB') + ' — ' + (iv.basis || '') : '• ' + NOT_COMPUTABLE,
      '• Asset ' + a.asset_id + (a.asset_code ? ' · ' + a.asset_code : '') + (a.installation_date ? ' · installed ' + a.installation_date : '')
    ].filter(Boolean).join('\n');
    this.setState({ detail: null });
    this.orchWith(isWo ? 'Raise work order' : 'Request inspection', a.asset_name + ' · ' + (x.b ? x.b.name : 'Unlinked'), 'email', {
      emKind: isWo ? 'wo' : 'inspect',
      emKicker: isWo ? 'Work order request · draft' : 'Inspection request · draft',
      emTo: '', emSubject: (isWo ? 'Work order request — ' : 'Inspection request — ') + a.asset_name,
      emBody: 'Hello ' + vendor + ' team,\n\n' + (isWo
        ? 'Please raise a predictive work order on ' + a.asset_name + '. The engine has both the building and the asset out of pattern, so we are not waiting for a fault report.'
        : 'Please inspect ' + a.asset_name + '. The engine has flagged this asset for a condition check ahead of any fault.')
        + '\n\nWhat the record shows:\n' + evidence + '\n\nRegards,\nPlanum Technologies · Hoistra',
      fSubject: a.asset_name, fVendor: vendor
    });
  },

  /* Instrumented assets. plenum_cafm.asset_readings is real telemetry and nothing else is:
     there is no band, no failure model and no remaining-life table in the schema, so those
     panels name the gap. An asset appears here only because it has readings on file. */
  iotVals(s) {
    if (!(s.view === 'module' && s.module === 'assets')) return { iotCards: [] };
    // An asset is "instrumented" here because its intelligence came back carrying readings.
    const intelAll = s.asIntel || {};
    const byAsset = {};
    Object.keys(intelAll).forEach((aid) => {
      const rs = intelAll[aid] && Array.isArray(intelAll[aid].readings) ? intelAll[aid].readings : [];
      if (rs.length) byAsset[aid] = rs.map((r) => ({ asset_id: aid, reading_type: r.reading_type, value: r.value, unit: r.unit, recorded_at: r.recorded_at }));
    });
    const assetById = {};
    (s.asLive || []).forEach((a) => { assetById[String(a.asset_id)] = a; });
    const bldById = {};
    (this.bldData ? this.bldData() : []).forEach((b) => { if (b.buildingId) bldById[b.buildingId] = b; });
    const intel = s.asIntel || {};
    const pctS = (n) => Math.round((n || 0) * 1000) / 10 + '%';

    const spark = (vals) => {
      const w = 84, h = 22;
      if (vals.length < 2) return { pts: '', hiY: '0' };
      const min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
      const rng = (max - min) || 1;
      return { pts: vals.map((v, i) => (i / (vals.length - 1) * w).toFixed(1) + ',' + (h - ((v - min) / rng) * h).toFixed(1)).join(' '), hiY: '0' };
    };

    const cards = Object.keys(byAsset).map((aid) => {
      const a = assetById[aid];
      const rows = byAsset[aid].slice().sort((p, q) => new Date(p.recorded_at || 0) - new Date(q.recorded_at || 0));
      const byType = {};
      rows.forEach((r) => { (byType[r.reading_type] = byType[r.reading_type] || []).push(r); });
      const open = s.iotOpen === aid;
      const latest = rows[rows.length - 1];
      const b = a && a.building_id ? bldById[String(a.building_id)] : null;
      const i = intel[aid] || {};
      const fa = i.failure_assessment || null, iv = i.value || {};
      // Bands come with the reading from the intelligence read; a reading type with no band
      // is UNGRADED, which is not the same as in band.
      const bandOf = {};
      (i.readings || []).forEach((r) => { bandOf[r.reading_type] = r; });

      const readings = Object.keys(byType).map((k) => {
        const series = byType[k];
        const last = series[series.length - 1];
        const nums = series.map((r) => Number(r.value)).filter((n) => !isNaN(n));
        const sp = spark(nums.slice(-24));
        const g = bandOf[k] || null;
        const state = g ? g.state : 'unknown';
        const tone = state === 'out_of_band' ? 'risk' : state === 'in_band' ? 'ok' : null;
        return {
          k: k, v: (isNaN(Number(last.value)) ? String(last.value) : String(Number(Number(last.value).toFixed(2)))) + (last.unit ? ' ' + last.unit : ''),
          band: g && g.band_lo != null && g.band_hi != null ? g.band_lo + '–' + g.band_hi + (last.unit ? ' ' + last.unit : '') : UNGRADED,
          color: tone ? t(tone).color : 'var(--color-neutral-300)',
          dot: tone ? t(tone).color : 'var(--color-neutral-500)',
          note: (g && g.note ? g.note + ' · ' : '') + series.length + (series.length === 1 ? ' reading on file' : ' readings on file'),
          noteShow: 'block', pts: sp.pts, hiY: sp.hiY,
          state: state === 'out_of_band' ? 'out of band' : state === 'in_band' ? 'in band' : 'ungraded'
        };
      });
      const nOut = readings.filter((r) => r.state === 'out of band').length;
      const nUngraded = readings.filter((r) => r.state === 'ungraded').length;
      const pTone = fa ? (fa.probability >= 0.5 ? 'risk' : fa.probability >= 0.25 ? 'warn' : 'ok') : null;

      return {
        id: aid, name: a ? a.asset_name : 'Asset ' + aid.slice(0, 8) + '…',
        cls: (i.asset && i.asset.section) || (a && a.category_name) || 'No category set',
        b: b ? b.name : 'Unlinked', sec: (i.asset && i.asset.section) || NO_SECTION_LINK,
        feed: 'plenum_cafm.asset_readings', sensors: String(readings.length),
        vendor: (i.asset && i.asset.vendor) || 'Open to read the vendor',
        open: open, caret: open ? 'ph-caret-down' : 'ph-caret-right',
        toggle: () => { this.setState((p) => ({ iotOpen: p.iotOpen === aid ? null : aid })); if (s.iotOpen !== aid) this.asCondLoadIntel(aid); },
        live: '1', last: latest && latest.recorded_at ? String(latest.recorded_at) : 'undated',
        outLine: i.loading ? 'Reading bands…'
          : nOut + ' of ' + readings.length + ' readings out of band' + (nUngraded ? ' · ' + nUngraded + ' ungraded' : ''),
        outColor: nOut ? t('risk').color : nUngraded ? 'var(--color-neutral-500)' : t('ok').color,
        readings: readings,
        // A named rule over recorded signals. is_fitted_model is false and accuracy,
        // precision and recall are null on purpose — no model has been fitted against
        // labelled failures, so quoting those numbers would invent evidence. The drivers
        // are what make the number usable, so they are rendered rather than a bare percent.
        pFail: fa ? pctS(fa.probability) : (i.loading ? '…' : '—'),
        pColor: pTone ? t(pTone).color : 'var(--color-neutral-500)',
        pBg: pTone ? t(pTone).bg : 'var(--color-bg)',
        horizon: fa ? (fa.method || RULE_NOT_MODEL) : 'open to read the assessment',
        acc: fa && fa.accuracy != null ? fa.accuracy + '%' : 'n/a',
        prec: fa && fa.precision != null ? fa.precision + '%' : 'n/a',
        rec: fa && fa.recall != null ? fa.recall + '%' : 'n/a',
        trained: fa ? (fa.is_fitted_model ? 'fitted model' : (fa.why_no_metrics || RULE_NOT_MODEL)) : RULE_NOT_MODEL,
        drivers: fa && fa.drivers && fa.drivers.length
          ? fa.drivers.map((d) => ({ t: d.signal + ' — ' + d.value + ' (+' + pctS(d.contribution) + ')' + (d.note ? ' · ' + d.note : '') }))
          : [{ t: i.loading ? 'Reading…' : 'Open the asset to read its drivers' }],
        rul: fa && fa.remaining_life_months != null ? fa.remaining_life_months + ' months'
          : iv.remaining_life_months != null ? iv.remaining_life_months + ' months' : '—',
        rulRange: fa && fa.design_life_used_pct != null ? Math.round(fa.design_life_used_pct) + '% of design life used' : NOT_COMPUTABLE,
        age: iv.age_years != null ? iv.age_years + ' of ' + iv.design_life_years + ' yrs'
          : a && a.installation_date ? 'installed ' + String(a.installation_date) : 'install date not on record',
        lifePct: iv.design_life_used_pct != null ? Math.min(100, Math.round(iv.design_life_used_pct)) + '%' : '0%',
        lifeColor: iv.design_life_used_pct > 80 ? t('risk').color : iv.design_life_used_pct > 60 ? t('warn').color : t('ok').color,
        hours: a && a.warranty_expiry ? 'warranty to ' + String(a.warranty_expiry) : 'Run hours not on record',
        book: iv.adjusted_value != null ? '£' + Math.round(iv.adjusted_value).toLocaleString('en-GB') : '—',
        replaceCost: iv.replacement_value != null ? '£' + Math.round(iv.replacement_value).toLocaleString('en-GB') : '—',
        remCost: iv.value_at_risk != null ? '£' + Math.round(iv.value_at_risk).toLocaleString('en-GB') : '—',
        remWhat: iv.basis || (i.loading ? 'Reading…' : NOT_COMPUTABLE),
        remGain: iv.value_at_risk != null ? 'Closes £' + Math.round(iv.value_at_risk).toLocaleString('en-GB') + ' of value at risk' : NOT_COMPUTABLE,
        repGain: iv.replacement_value != null ? 'Resets the life clock on a £' + Math.round(iv.replacement_value).toLocaleString('en-GB') + ' asset' : NOT_COMPUTABLE,
        when: iv.remaining_life_months != null ? iv.remaining_life_months + ' months of useful life left on the worn line' : NOT_COMPUTABLE,
        remEdge: iv.value_at_risk != null && iv.design_life_used_pct < 60 ? 'var(--color-accent)' : 'var(--color-divider)',
        repEdge: iv.design_life_used_pct >= 60 ? 'var(--color-accent)' : 'var(--color-divider)',
        remTag: iv.value_at_risk != null && iv.design_life_used_pct < 60 ? 'Recommended' : '',
        repTag: iv.design_life_used_pct >= 60 ? 'Recommended' : '',
        remediate: (e) => { if (e && e.stopPropagation) e.stopPropagation(); const a2 = assetById[aid]; if (a2) this.asCondAction({ a: a2, b: b, deviation: null }, 'wo'); },
        replace: (e) => { if (e && e.stopPropagation) e.stopPropagation(); const a2 = assetById[aid]; if (a2) this.asCondAction({ a: a2, b: b, deviation: null }, 'inspect'); },
        invShow: 'none', investigate: () => {}
      };
    }).sort((p, q) => p.name.localeCompare(q.name));

    return {
      iotCards: cards,
      iotEmpty: cards.length ? 'none' : 'block',
      iotEmptyText: s.asCondLoading ? 'Reading asset intelligence…'
        : Object.keys(intelAll).length
          ? 'None of the assets read so far has readings on file. Open an asset to read its telemetry.'
          : 'Open an asset to read its telemetry, bands and failure assessment.',
      iotNote: cards.length
        ? 'Live values from plenum_cafm.asset_readings, graded against plenum_cafm.asset_reading_bands. Open an asset to read its bands, its value-at-risk arithmetic and its failure assessment — a named rule over recorded signals, not a fitted model, so it carries drivers rather than an accuracy figure.'
        : ''
    };
  }
};
