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
//     Threat / Watch / In control    — GET /api/energy/condition/assets. The SERVER decides
//                                      the band, from the section's deviation against the
//                                      section's own reference and the anomalies attributed
//                                      to the asset, at thresholds held per organisation in
//                                      asset_condition_rules. This page used to decide it
//                                      here off the BUILDING's deviation and was banding
//                                      assets lower than the server as a result — see
//                                      bandFromServer().
//     anomaly attributed to an asset — energy_anomalies.asset_id (GET /api/energy/anomalies)
//     open work orders               — work_orders.asset_id, a real join since 47b949e
//     asset condition and its date   — health_score (integer), condition_score,
//                                      condition_updated_at, warranty_expiry
//     live readings                  — plenum_cafm.asset_readings
//
//   THE GAP THAT WAS LEFT, NOW CLOSED
//     which section an asset is in   — AssetResponse still does not return section_id, but
//                                      GET /api/energy/condition/assets carries it for every
//                                      asset in scope, so the link is made in bulk from that
//                                      read. The per-asset intelligence call remains the
//                                      fallback for anything that read did not cover, and
//                                      "not yet linked to a section" now means the condition
//                                      read did not place it — not that nothing could.
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

// ── the two steppers ─────────────────────────────────────────────────────────────────
// They are a control over a stored rule, not a slider over a browser copy. The rule lives
// in plenum_cafm.asset_condition_rules, one row per organisation, and it is what the engine
// bands every asset by — so a step has to reach the server and the bands have to be read
// back. Before this, bump() moved a number in state and nothing else happened: the page
// opened on the values below whatever the company's rule actually was, and moving a stepper
// re-coloured the sections while leaving every asset banded by the server's untouched rule.
//
// The range is the PAGE's, narrower than the route's (0–500% / 0–520 weeks): these are the
// values a facilities rule is plausibly written at, and a stepper that can walk to 500% in
// steps of 5 is not a stepper anybody would use.
export const RULE_BOUNDS = {
  asPct: { step: 5, lo: 5, hi: 40 },
  asWeeks: { step: 1, lo: 1, hi: 12 }
};

// Long enough that holding + does not fire a write per click — each one re-bands the whole
// portfolio for everyone in the company — short enough that the page is not lying for long
// about which rule is in force.
export const RULE_SAVE_MS = 400;

export function nextRuleValue(key, current, direction) {
  const b = RULE_BOUNDS[key];
  if (!b) return current;
  const n = Number(current);
  const from = Number.isFinite(n) ? n : b.lo;
  return Math.max(b.lo, Math.min(b.hi, from + b.step * (direction < 0 ? -1 : 1)));
}

// Which thresholds decided what is on screen. The engine's, whenever it gave them: it is
// the engine that banded the assets, and a page that colours its sections by the stepper
// while the assets above them were banded by the stored rule is showing two rules at once —
// which is exactly what it did. `pending` is the honest name for the gap between the two,
// open only while a step is in flight or after one failed to save.
export function rulesInForce(state) {
  const s = state || {};
  const r = s.asCondRules || null;
  const srvPct = r && typeof r.section_over_reference_pct === 'number' ? r.section_over_reference_pct : null;
  const srvWks = r && typeof r.anomaly_persistent_weeks === 'number' ? r.anomaly_persistent_weeks : null;
  const pct = srvPct === null ? s.asPct : srvPct;
  const weeks = srvWks === null ? s.asWeeks : srvWks;
  return {
    pct, weeks,
    fromServer: srvPct !== null || srvWks !== null,
    isDefault: !!(r && r.is_default),
    updatedAt: (r && r.updated_at) || null,
    pending: s.asPct !== pct || s.asWeeks !== weeks
  };
}

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

// The band as the server decided it, with the one signal the server's rule does not carry
// laid on top. GET /api/energy/condition/assets is the authority on Threat / Watch / In
// control: it reads the SECTION's deviation against the section's own reference, at
// thresholds held per organisation in asset_condition_rules that a browser copy cannot see.
//
// health_score is not in that rule, and it is a real signal, so it is applied here — under
// the same constraint it has always had in this page: it may RAISE a band, never lower one.
// An asset scored 20 is a threat whatever the energy says; a perfect score does not rescue an
// asset the server flagged.
export function bandFromServer(row, healthScore) {
  const SERVER_COND = { threat: 'threat', watch: 'watch', in_control: 'ok' };
  if (!row || !SERVER_COND[row.band]) return null;
  const reasons = row.reasons || [];
  const hb = healthBand(typeof healthScore === 'number' ? healthScore : null).cond;
  let cond = SERVER_COND[row.band];
  let kind = row.band === 'threat' ? 'threat'
    : reasons.indexOf('anomaly_persistent') >= 0 ? 'persist'
    : row.band === 'watch' ? 'zone' : null;
  if (hb === 'threat') { cond = 'threat'; kind = kind || 'health'; }
  else if (hb === 'watch' && cond === 'ok') { cond = 'watch'; kind = 'health'; }
  return {
    cond, kind, reasons,
    over: reasons.indexOf('section_over_reference') >= 0,
    persistent: reasons.indexOf('anomaly_persistent') >= 0,
    unscored: hb === 'unscored'
  };
}

// Is this row in the chip's set? The engine answered the band chip over every asset it
// holds, so its list decides membership. The one exception is an asset this page raised on
// health_score, a signal the engine's rule does not carry: it cannot be in the engine's
// Threat list because the engine never called it a Threat, and dropping it would hide the
// row the raise exists to surface. So a raised row falls back to the page's own test.
export function inChipSet(row, filterLabel, serverIds, localTest) {
  if (!serverIds) return localTest(row);
  if (row && row.source === 'server' && row.kind === 'health') return localTest(row);
  return serverIds.has(String(row && row.a && row.a.asset_id));
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
  // The promise is kept, not just the flag: a caller that needs the bands re-read after it
  // changed the rule (asRuleSave) has to be able to WAIT for a read already in flight. With
  // only the flag, it returned undefined at the guard and the save finished while the page
  // still showed bands decided under the old threshold — the exact failure this control
  // exists to avoid.
  asCondLoad() {
    if (this._asCondLoading) return this._asCondLoadP || Promise.resolve();
    this._asCondLoadP = this.asCondLoadNow();
    return this._asCondLoadP;
  },

  async asCondLoadNow() {
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
      const [locs, anoms, sections, var_, cond, csum, floors] = await Promise.all([
        soft(workOrderApi.locations({ limit: 500 })),
        soft(energyApi.anomalies({ limit: 500 })),
        soft(energyApi.sections()),
        soft(energyApi.assetValueAtRisk()),
        soft(energyApi.conditionAssets()),
        soft(energyApi.conditionSummary()),
        // The sub-meters that sit on an asset, with their 30-day kWh and share of the
        // incoming supply. A section's intensity comes from the section's meter; an asset's
        // consumption comes from its own, and until this read the page never showed it — ten
        // metered chillers, boilers and lifts sat under "not metered" sections and looked
        // unmetered themselves.
        soft(energyApi.metersByFloor())
      ]);
      if (stale) throw stale;
      const list = (v, ...keys) => Array.isArray(v) ? v
        : keys.map((k) => v && Array.isArray(v[k]) ? v[k] : null).find(Boolean)
        || (v && Array.isArray(v.data) ? v.data : []);
      // The steppers show the ORGANISATION's rule, which arrives on this read under
      // `rules`. Seeding them is the difference between a row that states the threshold the
      // bands beneath it were decided by and a row that states the number this build was
      // compiled with. Skipped while a step of the user's own is unsaved or in flight —
      // that is the one moment the stepper is deliberately ahead of the server, and
      // overwriting it here would yank the control back under the hand that moved it.
      const rule = (cond && !cond.__err && cond.rules) || null;
      const seed = (rule && !this._asRulePending) ? {
        asPct: typeof rule.section_over_reference_pct === 'number' ? rule.section_over_reference_pct : this.state.asPct,
        asWeeks: typeof rule.anomaly_persistent_weeks === 'number' ? rule.anomaly_persistent_weeks : this.state.asWeeks
      } : {};
      // Keyed by asset id: every sub-meter that names the asset (an asset can carry one per
      // fuel). Built here so row() below is a lookup, not a scan.
      const assetMeters = {};
      if (floors && !floors.__err && Array.isArray(floors.buildings)) {
        floors.buildings.forEach((b) => (b.assets || []).forEach((m) => {
          if (m.asset_id) (assetMeters[String(m.asset_id)] = assetMeters[String(m.asset_id)] || []).push(m);
        }));
      }
      this.setState(Object.assign(seed, {
        asAssetMeters: assetMeters, asAssetMetersDays: floors && !floors.__err ? (floors.days || 30) : null,
        asAssetMetersError: floors && floors.__err ? floors.__err : '',
        asLocations: list(locs, 'locations'), asLocationsError: locs && locs.__err ? locs.__err : '',
        asAnoms: list(anoms, 'anomalies'), asAnomsError: anoms && anoms.__err ? anoms.__err : '',
        asSections: list(sections, 'sections'), asSectionsSummary: (sections && sections.summary) || null,
        asSectionsError: sections && sections.__err ? sections.__err : '',
        asVar: var_ && !var_.__err ? var_ : null, asVarError: var_ && var_.__err ? var_.__err : '',
        // The server's bands, keyed by asset. asCondBandsError is not cosmetic: when this
        // read fails the page falls back to deciding bands itself off the BUILDING's
        // deviation, which is a different and coarser answer, so the page has to be able to
        // say that is what it is showing.
        asCondBands: list(cond, 'assets'), asCondRules: (cond && cond.rules) || null,
        asCondBandsError: cond && cond.__err ? cond.__err : '',
        // count is what came back after any filter, total is every asset the engine banded.
        // They differ when the limit bit, and a page that filters its own copy in that state
        // is filtering a truncated list — so the difference is kept rather than discarded.
        asCondTotal: cond && typeof cond.total === 'number' ? cond.total : null,
        asCondSummary: csum && !csum.__err ? (csum.summary || null) : null,
        asCondBuildings: csum && !csum.__err && Array.isArray(csum.buildings) ? csum.buildings : [],
        asCondLastRun: csum && !csum.__err ? (csum.last_run || null) : null,
        asCondSummaryError: csum && csum.__err ? csum.__err : '',
        asCondLoading: false, asCondLoadedAt: new Date().toISOString()
      }));
    } catch (e) {
      if (isStaleScope(e)) return;
      this.setState({ asCondLoading: false, asCondError: (e && e.message) || String(e) });
    } finally {
      this._asCondLoading = false;
    }
  },

  // ── moving a stepper ───────────────────────────────────────────────────────────────
  // A step is a WRITE, and the page cannot honour it on its own: Threat / Watch / In
  // control, the four cards and the section lines are all the engine's answer, banded on
  // the rule stored for the organisation. So the number moves at once (the person pressed
  // a button and must see it), the rule is written, and the bands are read back at it.
  //
  // Debounced because holding + would otherwise write — and re-band the whole portfolio,
  // for everyone in the company — once per click.
  asRuleStep(key, direction) {
    if (!RULE_BOUNDS[key]) return;
    const next = nextRuleValue(key, this.state[key], direction);
    if (next === this.state[key]) return;   // already at the bound; nothing to write
    // Disowns a write already on the wire. Without this, that write's answer comes back
    // while a newer step is armed, clears the pending flag and re-seeds the steppers from
    // the value it wrote — pulling the number back out from under the hand still pressing
    // the button. Its row is still written server-side; the newer write replaces it.
    this._asRuleToken = (this._asRuleToken || 0) + 1;
    this._asRulePending = true;
    this.setState({ [key]: next, asRuleSaving: true, asRuleError: '' });
    clearTimeout(this._asRuleTimer);
    this._asRuleTimer = setTimeout(() => this.asRuleSave(), RULE_SAVE_MS);
  },

  async asRuleSave() {
    clearTimeout(this._asRuleTimer);
    const token = (this._asRuleToken = (this._asRuleToken || 0) + 1);
    const pct = Number(this.state.asPct), weeks = Number(this.state.asWeeks);
    // What the engine holds right now — where the steppers go back to if the write is
    // refused. Falling back to the shipped defaults instead would replace one wrong number
    // with another; when the engine has never said, the steppers are all there is and they
    // are left where they are.
    const held = rulesInForce(this.state);
    this.setState({ asRuleSaving: true, asRuleError: '' });
    try {
      const out = await energyApi.setConditionRules({
        section_over_reference_pct: pct, anomaly_persistent_weeks: weeks });
      // A later click already owns the rule; this answer is about a value nobody is on.
      if (token !== this._asRuleToken) return;
      this._asRulePending = false;
      this.setState({
        asRuleSaving: false, asRuleError: '',
        asCondRules: out && typeof out.section_over_reference_pct === 'number'
          ? { section_over_reference_pct: out.section_over_reference_pct,
              anomaly_persistent_weeks: out.anomaly_persistent_weeks,
              is_default: !!out.is_default, updated_at: out.updated_at || null }
          : { section_over_reference_pct: pct, anomaly_persistent_weeks: weeks,
              is_default: false, updated_at: null }
      });
      // Re-read rather than re-decide. The page cannot re-band from here — it does not hold
      // the section deviations the engine bands on for every asset, and deciding it twice
      // in two places is how the sections and the assets came to disagree in the first place.
      //
      // A read already in flight was issued under the OLD rule, so joining it would answer
      // with the bands this write was meant to change. It is waited out, then a fresh one
      // goes at the new threshold.
      if (this._asCondLoading) { try { await this._asCondLoadP; } catch (e) { /* its own catch reports it */ } }
      await this.asCondLoad();
      const cf = this.state.asCondFilter;
      if (cf && cf.band) {
        const LABEL = { threat: 'Threat', watch: 'Watch', in_control: 'In control' };
        await this.asCondSetFilter(LABEL[cf.band]);
      }
    } catch (e) {
      if (token !== this._asRuleToken) return;
      // The company changed under the write: the correctly-scoped page is already loading
      // and will seed its own steppers. Reverting to this company's rule would fight it.
      if (isStaleScope(e)) { this._asRulePending = false; return; }
      this._asRulePending = false;
      this.setState(Object.assign(
        { asRuleSaving: false, asRuleError: (e && e.message) || String(e) },
        held.fromServer ? { asPct: held.pct, asWeeks: held.weeks } : {}
      ));
    }
  },

  // A band chip re-asks the engine rather than sifting the copy already in the page.
  //
  // At today's portfolio size the two give the same rows, because the unfiltered read holds
  // every asset. They stop being the same the moment the read is capped: filtering a
  // truncated list silently answers "of the ones I happened to load" while looking like it
  // answered "of all of them". ?band= is counted by the engine over everything it holds, so
  // the chip's count is the portfolio's count at any size.
  //
  // Not scored and Open work order stay local on purpose — health_score and open work orders
  // are not signals this endpoint carries, so there is nothing on it to ask.
  async asCondSetFilter(f) {
    const BAND = { Threat: 'threat', Watch: 'watch', 'In control': 'in_control' };
    const band = BAND[f];
    if (!band) { this.setState({ asCondFilter: null }); return; }
    const token = (this._asCondFilterToken = (this._asCondFilterToken || 0) + 1);
    this.setState({ asCondFilter: { band, loading: true, ids: null, count: null, error: '' } });
    try {
      const res = await energyApi.conditionAssets({ band });
      // A chip clicked twice while the first answer was in flight would otherwise let the
      // slower response overwrite the faster one, leaving the list showing the wrong band.
      if (token !== this._asCondFilterToken) return;
      const rows = (res && Array.isArray(res.assets)) ? res.assets : [];
      this.setState({ asCondFilter: {
        band, loading: false, error: '',
        ids: rows.map((r) => r && r.asset_id).filter(Boolean).map(String),
        count: typeof res.count === 'number' ? res.count : rows.length,
        total: typeof res.total === 'number' ? res.total : null } });
    } catch (e) {
      if (token !== this._asCondFilterToken) return;
      if (isStaleScope(e)) { this.setState({ asCondFilter: null }); return; }
      // Falling back to the local sift is right; pretending it was the engine's answer is
      // not, so the error rides along and the summary line says which one is on screen.
      this.setState({ asCondFilter: { band, loading: false, ids: null, count: null,
                                      error: (e && e.message) || String(e) } });
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
    // Two different numbers, and the difference matters. `pct`/`wks` are where the STEPPERS
    // stand — what the person last pressed, which may be a step that has not landed yet.
    // `inForce` is the rule the ENGINE banded by, which is what every band, card and section
    // line on this page was actually decided against. Anything describing what is on screen
    // must quote inForce; only the steppers themselves show pct/wks.
    const pct = s.asPct, wks = s.asWeeks;
    const inForce = rulesInForce(s);
    const thrPct = inForce.pct, thrWks = inForce.weeks;
    const serverRules = s.asCondRules || null;
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

    // GET /api/energy/condition/assets decides the band. The page used to decide it here,
    // and the two answers were not the same: measured over hoistra_test on 15 Sep 2026 they
    // agreed on 44 of 54 assets, and every one of the ten disagreements ran the same way —
    // this page banding LOWER than the server, two Threats shown as Watch and eight Watches
    // as In control. One cause explained all ten: the line below used to read the BUILDING's
    // deviation, where the server reads the SECTION's. On that data no building was over its
    // reference at all (as low as -51.3%) while ten assets sat in sections over by 18-46% —
    // averaged across a building, an over-consuming plant room disappears into the floors
    // around it, which is the whole reason sections carry their own reference.
    const bandRow = {};
    (s.asCondBands || []).forEach((r) => { if (r && r.asset_id) bandRow[String(r.asset_id)] = r; });

    const evalA = (a) => {
      const b = a.building_id ? bldByEui[String(a.building_id)] : null;
      const buildingDeviation = b && typeof b.deviation === 'number' ? b.deviation
        : (b && typeof b.euiN === 'number' && typeof b.benchN === 'number' && b.benchN)
          ? Math.round(((b.euiN - b.benchN) / b.benchN) * 100) : null;
      const anoms = anomBy[String(a.asset_id)] || [];
      const anomaly = anoms[0] || null;
      const anomalyDays = anomaly ? anomalyAgeDays(anomaly, now) : null;
      const score = typeof a.health_score === 'number' ? a.health_score : null;
      const sv = bandRow[String(a.asset_id)];
      const svc = bandFromServer(sv, score);

      if (svc) {
        return {
          a, b, source: 'server',
          // `deviation` is the section's here — the figure the band was actually decided on,
          // and the same number the section header shows, because both come from one
          // function on the server rather than from two derivations that can drift.
          deviation: typeof sv.section_deviation_pct === 'number' ? sv.section_deviation_pct : null,
          buildingDeviation, sectionMeasured: sv.section_measured !== false,
          sectionId: sv.section_id ? String(sv.section_id) : null, sectionName: sv.section || null,
          explanation: sv.explanation || '',
          anomaly, anomalyDays, score, ...svc
        };
      }

      // Fallback: the condition read failed, or this asset is outside what it returned. The
      // band is then decided here off the BUILDING's deviation, which is the coarser answer
      // — so it is marked as such and the page says so rather than presenting it as equal.
      const c = conditionOf({ deviation: buildingDeviation, pct: thrPct, anomaly, anomalyDays, weeks: thrWks, healthScore: score });
      return { a, b, source: 'page', deviation: buildingDeviation, buildingDeviation,
               sectionMeasured: null, sectionId: null, sectionName: null,
               reasons: [], explanation: '', anomaly, anomalyDays, score, ...c };
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
    // The engine's answer to the band chip when it gave one, this page's own sift otherwise.
    // `serverFiltered` is what the summary line reports, because "filtered by the engine over
    // every asset" and "filtered here over the ones loaded" are different claims.
    const cf = s.asCondFilter || null;
    const BAND_OF = { Threat: 'threat', Watch: 'watch', 'In control': 'in_control' };
    const fromServer = cf && !cf.loading && !cf.error && Array.isArray(cf.ids)
      && cf.band === BAND_OF[f] ? new Set(cf.ids) : null;
    const serverFiltered = !!fromServer;

    const localShown = (x) => f === 'Threat' ? x.cond === 'threat'
      : f === 'Watch' ? x.cond === 'watch'
      : f === 'In control' ? x.cond === 'ok'
      : f === 'Not scored' ? x.unscored
      : f === 'Open work order' ? woCount(x.a) > 0
      : true;

    const shown = all.filter((x) => inChipSet(x, f, fromServer, localShown));

    // Per-asset value at risk, keyed off the portfolio read. An asset absent from that list
    // is one the engine could not compute, not one worth nothing.
    const intel = s.asIntel || {};
    const varRows = {};
    ((s.asVar && s.asVar.assets) || []).forEach((r) => { if (r && r.asset_id) varRows[String(r.asset_id)] = r; });
    const varOf = (a) => varRows[String(a.asset_id)] || null;
    const money = (n) => typeof n === 'number'
      ? '£' + (Math.abs(n) >= 1000 ? (n / 1000).toFixed(Math.abs(n) >= 10000 ? 0 : 1) + 'k' : Math.round(n))
      : '—';

    // Says which figure the band was actually decided on, because "over reference" means a
    // different thing for a section than for the building around it.
    const why = (x) => {
      const bits = [];
      const thr = thrPct;
      if (x.source === 'server') {
        if (x.sectionMeasured === false) {
          bits.push('The section this asset sits in has no sub-meter, so it was banded on the anomaly signal alone — one of the two things that would have been checked could not be read. That is not the same as checked and clean.');
        } else if (x.deviation === null) {
          bits.push('No intensity on record for the section this asset sits in, so the energy rule could not run on it.');
        } else {
          bits.push('Section ' + (x.deviation > 0 ? '+' : '') + x.deviation + '% against its own reference, '
            + (x.over ? 'over' : 'inside') + ' the ' + thr + '% threshold.'
            + (typeof x.buildingDeviation === 'number' && x.buildingDeviation !== x.deviation
               ? ' The building around it is ' + (x.buildingDeviation > 0 ? '+' : '') + x.buildingDeviation
                 + '%, which is why the building figure alone would not have shown this.' : ''));
        }
      } else if (x.deviation === null) {
        bits.push('No EUI on record for this building, so the energy rule cannot run on it.');
      } else {
        bits.push('Building ' + (x.deviation > 0 ? '+' : '') + x.deviation + '% against its reference, '
          + (x.over ? 'over' : 'inside') + ' the ' + thrPct + '% threshold. Banded in the page on the building figure, '
          + 'not on the section, because the condition read did not come back.');
      }
      if (x.anomaly) bits.push('An anomaly is attributed to this asset (' + (x.anomaly.anomaly_type || 'anomaly') + ', open ' + (x.anomalyDays === null ? 'unknown' : x.anomalyDays + ' days') + ').');
      else bits.push('No anomaly is attributed to this asset; energy_anomalies.asset_id is set only where a meter names the asset.');
      bits.push(x.score === null ? 'No health score on record — not the same as healthy.' : 'Health score ' + Math.round(x.score) + '/100.');
      return bits.join(' ');
    };

    // What the asset's own sub-meter read over the trailing window. kWh with its share of the
    // building's incoming supply on that fuel, so a chiller's 12% reads beside a lift's 1.5%.
    const meterLine = (a) => {
      const ms = (s.asAssetMeters || {})[String(a.asset_id)] || [];
      if (!ms.length) {
        return { text: s.asAssetMetersError ? 'Sub-meter read unreachable — ' + s.asAssetMetersError
                        : 'No sub-meter on this asset — its energy is inside the section figure',
                 color: 'var(--color-neutral-500)', metered: false };
      }
      const days = s.asAssetMetersDays || 30;
      const parts = ms.map((m) => (m.fuel === 'gas' ? 'Gas ' : 'Electricity ')
        + (typeof m.kwh === 'number' ? Math.round(m.kwh).toLocaleString('en-GB') + ' kWh' : '—')
        + (typeof m.share_pct === 'number' ? ' · ' + m.share_pct + '% of supply' : '')
        + (typeof m.cost === 'number' ? ' · £' + Math.round(m.cost).toLocaleString('en-GB') : ''));
      const anoms = ms.reduce((n, m) => n + (m.open_anomalies || 0), 0);
      return {
        text: parts.join(' · ') + ' · last ' + days + ' days' + (anoms ? ' · ' + anoms + (anoms === 1 ? ' open anomaly on the meter' : ' open anomalies on the meter') : ''),
        color: anoms ? t('warn').color : 'var(--color-text)', metered: true,
      };
    };

    const row = (x) => {
      const a = x.a, tone = t(TONE_OF[x.cond]);
      const loc = a.location_id ? locById[String(a.location_id)] : null;
      const nWo = woCount(a);
      const mt = meterLine(a);
      x.meterText = mt.text;
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
        meterText: mt.text, meterColor: mt.color, metered: mt.metered,
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
    // GET /api/energy/condition/assets carries section_id for every asset in scope, which is
    // what closes the gap this page used to carry: AssetResponse does not return the column,
    // so the link could only be made one asset at a time through the intelligence read, and
    // most assets sat under "not yet linked to a section" as a result. The intelligence read
    // stays as the fallback for anything the condition read did not cover.
    const sectionOfAsset = (a) => {
      const sv = bandRow[String(a.asset_id)];
      if (sv && sv.section_id) return String(sv.section_id);
      const i = intel[a.asset_id];
      return i && i.asset && i.asset.section_id ? String(i.asset.section_id) : null;
    };
    const secsOf = (bk, xs) => (secsByB[bk] || []).map((sc) => {
      const mine = (xs || []).filter((x) => sectionOfAsset(x.a) === String(sc.section_id))
        .sort((p, q) => rank[p.cond] - rank[q.cond]);
      const measured = sc.measured !== false && typeof sc.eui_kwh_per_m2 === 'number';
      const ref = typeof sc.reference_eui_kwh_m2 === 'number' ? sc.reference_eui_kwh_m2 : null;
      // Assets in this section that carry a sub-meter of their own. A plant room with no
      // meter on the room but a meter on every chiller is metered in a different sense, and
      // the header should not read "no sub-meter" over six metered machines.
      const assetMetered = mine.filter((x) => ((s.asAssetMeters || {})[String(x.a.asset_id)] || []).length).length;
      const d = measured && typeof sc.deviation_pct === 'number' ? Math.round(sc.deviation_pct) : null;
      const over = d !== null && d > thrPct;
      const sk = bk + '|sec|' + sc.section_id;
      const sOpen = (s.asOpenS || []).indexOf(sk) > -1;
      return {
        name: sc.name + (sc.section_type ? ' · ' + sc.section_type : ''),
        eui: measured && ref !== null ? sc.eui_kwh_per_m2 + ' vs ' + ref + ' kWh/m²/yr' : 'Not metered',
        delta: d === null ? '—' : (d > 0 ? '+' : '') + d + '%',
        meter: (measured
          ? (sc.meters ? sc.meters + (sc.meters === 1 ? ' sub-meter' : ' sub-meters') : 'no sub-meter')
            + (sc.reference_source ? ' · ' + sc.reference_source : '')
          : NOT_METERED)
          + (assetMetered ? ' · ' + assetMetered + ' of ' + mine.length + ' assets sub-metered' : ''),
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
          const over = meas.filter((x) => typeof x.deviation_pct === 'number' && x.deviation_pct > thrPct).length;
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

    const vaR = s.asVar || null;
    const anomCovered = all.filter((x) => x.anomaly).length;
    const euiCovered = all.filter((x) => x.deviation !== null).length;
    const cSum = s.asCondSummary || null;
    // Assets this page put a band on that the engine did not — health_score is the page's
    // own signal. Stated rather than absorbed, so the cards and the engine can be reconciled.
    const raisedByHealth = all.filter((x) => x.source === 'server' && x.kind === 'health').length;
    return {
      asThreatN: threats.length,
      asAll: all,
      asCards: [
        // Counts are over the rows on screen, not the engine's summary, so a card can never
        // disagree with the list beneath it — health_score can raise a band here and the
        // engine's rule does not carry that signal. Where the two differ, the card says so
        // rather than quietly showing whichever number is larger.
        { l: 'Threat', v: String(threats.length),
          s: 'section over its own reference + anomaly attributed'
            + (raisedByHealth ? ' · ' + raisedByHealth + ' raised here on health score' : '')
            + (cSum && cSum.threat !== threats.length - raisedByHealth
               ? ' · engine reports ' + cSum.threat : ''),
          color: t('risk').color },
        { l: 'Watch', v: String(watches.length),
          // The engine splits Watch into its two causes, which the page cannot derive: an
          // asset sharing an over-reference section, and one whose own anomaly has outlasted
          // the threshold while its section is fine. Different findings, different actions.
          s: cSum
            ? (cSum.watch_shares_section || 0) + ' shared section · '
              + (cSum.watch_persistent_anomaly || 0) + ' persistent anomaly'
            : all.filter((x) => x.kind === 'zone').length + ' shared section · '
              + all.filter((x) => x.kind === 'persist').length + ' persistent anomaly',
          color: t('warn').color },
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
        // "In control" is the card most able to mislead. On the deployed database most assets
        // sit in a section with no sub-meter, so they were banded on the anomaly signal alone
        // — one of the two things that would have been checked could not be read. That is not
        // a clean bill of health and the card must not read as one. Same for an asset
        // carrying an anomaly that has not yet persisted far enough to count: found something
        // too small to act on is a different answer from found nothing.
        { l: 'In control', v: (all.length - threats.length - watches.length) + ' of ' + all.length,
          s: cSum
            ? [
                (cSum.section_not_measured || 0) + ' banded on one signal — section not metered',
                (cSum.in_control_anomaly_under_threshold || 0) + ' with an anomaly under threshold',
                anomCovered + ' with an anomaly attributed'
              ].join(' · ')
            : (s.asCondSummaryError
               ? 'Band summary unreachable — ' + s.asCondSummaryError + ' · '
               : '') + euiCovered + ' with a section intensity · ' + anomCovered + ' with an anomaly attributed',
          color: t('ok').color }
      ],
      asPct: pct + '%', asWeeks: String(wks), asWeeksUnit: wks === 1 ? 'week' : 'weeks',
      asPctDown: () => this.asRuleStep('asPct', -1), asPctUp: () => this.asRuleStep('asPct', 1),
      asWkDown: () => this.asRuleStep('asWeeks', -1), asWkUp: () => this.asRuleStep('asWeeks', 1),
      asRuleSaving: !!s.asRuleSaving,
      // What the row has to be able to say, in order of how much it matters: a write in
      // flight, a write that was refused (and therefore which rule the bands on screen were
      // really decided by), whose rule this is, and — when the engine never answered — that
      // these steppers are describing the page's own fallback rather than a stored rule.
      asRuleNote: s.asRuleSaving ? 'Saving…'
        : s.asRuleError
          ? 'Not saved — ' + s.asRuleError
            + (inForce.fromServer
               ? ' · the bands on screen are still the engine\u2019s ' + inForce.pct + '% / '
                 + inForce.weeks + (inForce.weeks === 1 ? ' week' : ' weeks')
               : '')
        : !inForce.fromServer
          ? (s.asCondBandsError
             ? 'Engine unreachable — these thresholds are the page\u2019s own fallback, not a stored rule'
             : '')
        : inForce.isDefault ? 'Platform default — not yet set for your company'
        : 'Set for your company'
          + (inForce.updatedAt ? ' · ' + String(inForce.updatedAt).slice(0, 16).replace('T', ' ') : ''),
      asRuleNoteTone: s.asRuleError ? 'risk' : s.asRuleSaving ? 'warn' : 'muted',
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
          + (f === 'All' ? '' : ' · filter: ' + f
              + (cf && cf.loading ? ' (asking the engine…)'
                 : serverFiltered ? ' (engine, over all ' + (cf.total === null || cf.total === undefined ? all.length : cf.total) + ')'
                 : cf && cf.error ? ' (filtered in the page — engine unreachable: ' + cf.error + ')'
                 : ''))
          + (typeof s.asCondTotal === 'number' && s.asCondTotal > (s.asCondBands || []).length
             ? ' · engine holds ' + s.asCondTotal + ' banded assets, ' + (s.asCondBands || []).length + ' read here' : '')
          + ' · ' + (s.asSectionsError ? 'sections unreachable (' + s.asSectionsError + ')'
              : (s.asSections || []).length + ' sections on record, ' + (s.asSections || []).filter((x) => x.measured !== false && typeof x.eui_kwh_per_m2 === 'number').length + ' metered')
          + ' · buildings ranked by EUI against reference'
          // A stamp nobody can point at is worse than none. null means no scan has been
          // recorded, which is not the same as a scan that found nothing.
          + (s.asCondLastRun && s.asCondLastRun.started_at
             ? ' · last scan ' + String(s.asCondLastRun.started_at).slice(0, 16).replace('T', ' ')
             : ' · no condition scan recorded');
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
          { a: 'metered', t: (x.meterText || (this.asVals ? '' : '')) || 'No sub-meter on this asset' },
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
