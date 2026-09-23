// energyLive — the Energy module's live anomalies and meters from
// svc-operations-intelligence, joined against the Buildings table buildingsLive.js has
// already loaded (no second building fetch here).
//
// GET /api/energy/anomalies returns open anomalies keyed on raw site_id / meter_id / asset_id
// UUIDs, with no resolved names. This module joins site_id to the Buildings rows (on
// buildings.uuid — sites.site_uuid, the same key the Hoist Graph counts against) and meter_id
// to the meters list; asset_id is a UUID that answers plenum_cafm.equipment.equipment_id, not
// plenum_cafm.assets.id (that table's key is a short code like "A0000001") — resolving it reads
// svc-udr's generic SELECT endpoint for the handful of ids that actually carry one.
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { energyApi } from '../api/energy.js';
import { udrApi } from '../api/udr.js';
import { isStaleScope } from '../api/client.js';

const RETRY_MS = 30000;
const RETRY_MAX = 6;

// Every rule id in src/data/hoistra-energy.js now has a real detector behind it in
// engines/energy/anomalies.py / engines/energy/detectors.py (the original 3, plus the 10
// added alongside the energy-regulation engines) — kept as a map rather than "all of them"
// so a rule added to the frontend catalogue without backend support still reads honestly.
export const LIVE_ANOMALY_TYPES = {
  weekend_spike: "calendar", baseline_drift: "drift", asset_spike: "spike",
  nonocc_spike: "nonocc", schedule_mismatch: "schedule", baseload_creep: "baseload",
  peak_excursion: "peak", data_quality: "dataq", tou_misalignment: "tou",
  weather_residual: "weather", simultaneous_heating_cooling: "fight",
  post_works_regression: "regress", chiller_efficiency: "cop"
};
export const IMPLEMENTED_RULE_IDS = new Set(Object.values(LIVE_ANOMALY_TYPES));

const ANOM_LABEL = {
  weekend_spike: "Weekend / non-occupancy spike",
  baseline_drift: "Baseline drift",
  asset_spike: "Single-asset spike",
  nonocc_spike: "Non-occupancy spike",
  schedule_mismatch: "Schedule mismatch",
  baseload_creep: "Baseload creep",
  peak_excursion: "Peak demand excursion",
  data_quality: "Data-quality anomaly",
  tou_misalignment: "Time-of-use misalignment",
  weather_residual: "Weather-normalised residual",
  simultaneous_heating_cooling: "Simultaneous heating and cooling",
  post_works_regression: "Post-works regression",
  chiller_efficiency: "Chiller efficiency"
};
const ANOM_TONE = {
  weekend_spike: "warn", baseline_drift: "warn", asset_spike: "risk",
  nonocc_spike: "warn", schedule_mismatch: "warn", baseload_creep: "warn",
  peak_excursion: "risk", data_quality: "warn", tou_misalignment: "warn",
  weather_residual: "warn", simultaneous_heating_cooling: "risk",
  post_works_regression: "risk", chiller_efficiency: "risk"
};

// Currency symbols for the markets the platform prices in. A code we do not have a symbol
// for is printed as the code — "MXN 1.2k" reads correctly, "£1.2k" on a Mexican building
// does not.
const CURRENCY_SYMBOL = { GBP: "£", USD: "$", AED: "AED ", SGD: "S$", EUR: "€" };

// The API used to return only financial_gbp and this used to hardcode a pound sign, so a
// Dubai building's dirhams were printed as sterling. The row now carries its own currency.
export function money(n, currency) {
  const code = String(currency || "GBP").toUpperCase();
  const sym = CURRENCY_SYMBOL[code] || code + " ";
  const v = Math.round(Math.abs(n || 0));
  const s = sym + (v >= 1000 ? Math.round(v / 1000) + "k" : String(v));
  return (n || 0) < 0 ? "−" + s : s;
}

// Kept so existing callers do not change meaning: sterling, said explicitly.
export function moneyGBP(n) {
  return money(n, "GBP");
}

// What a row leads with. Money where the rule could price the finding; the rule's own
// measure where it could not — simultaneous heating and cooling reports hours of plant
// fighting itself, and showing a dash there loses the only number it has.
export function impactLabel(a) {
  if (typeof a.financial_gbp === "number") return money(a.financial_gbp, a.currency);
  const im = a.impact || {};
  if (im.value !== null && im.value !== undefined) {
    return String(im.value) + (im.unit ? " " + im.unit : "");
  }
  return "—";
}

// Pure: one raw anomaly row + the already-loaded buildings/meters/equipment lookups → the
// shape the Energy page renders. Everything the seed shape carried (asset/building/type/
// impact/status/days/tone) is here; `live: true` tells investigate() and anomalyDetail() to
// take the real-orchestrator path rather than the scripted one.
export function shapeLiveAnomaly(a, buildingsByUuid, metersById, equipmentByUuid) {
  const b = a.site_id ? buildingsByUuid[a.site_id] : null;
  const meter = a.meter_id ? metersById[a.meter_id] : null;
  const equip = a.asset_id ? equipmentByUuid[a.asset_id] : null;
  const typeLabel = ANOM_LABEL[a.anomaly_type] || String(a.anomaly_type || "Anomaly").replace(/_/g, " ");
  const days = a.detected_at ? Math.max(0, Math.round((Date.now() - new Date(a.detected_at).getTime()) / 86400000)) : null;
  return {
    live: true,
    id: a.id,
    building: b ? b.name : (a.site_id ? "Unattributed site" : "Unattributed"),
    buildingUuid: a.site_id || null,
    // Which supply the finding was measured on. Several rules firing on one meter are
    // different readings of the same consumption, so the total has to know they share a
    // meter before it decides whether to add them.
    meterId: a.meter_id || null,
    cc: b ? b.cc : "—",
    // No asset resolved: name the meter instead of pretending the reading is whole-building
    // when it is not, and pretending it is whole-building when a meter is on record.
    asset: equip ? (equip.name || "Equipment " + String(a.asset_id).slice(0, 8))
      : (meter ? ((meter.meter_type || "meter") + " meter" + (meter.mpan ? " · " + meter.mpan : meter.mprn ? " · " + meter.mprn : "")) : "Whole building"),
    assetResolved: !!equip,
    meterType: meter ? meter.meter_type : null,
    type: typeLabel,
    anomalyType: a.anomaly_type,
    ruleId: LIVE_ANOMALY_TYPES[a.anomaly_type] || null,
    status: a.status === "open" ? "New" : (a.status ? a.status.charAt(0).toUpperCase() + a.status.slice(1) : "—"),
    impact: impactLabel(a),
    impactPriced: typeof a.financial_gbp === "number",
    currency: a.currency || "GBP",
    impactMeasure: (a.impact && a.impact.measure) || null,
    impactNote: (a.impact && a.impact.note) || null,
    // Detected on a simulated feed: the finding is about invented readings, and so is the
    // figure beside it.
    simulated: !!a.simulated,
    impactN: typeof a.financial_gbp === "number" ? a.financial_gbp : 0,
    excessKwh: typeof a.annualised_excess_kwh === "number" ? a.annualised_excess_kwh : null,
    metricPct: typeof a.metric_pct === "number" ? a.metric_pct : null,
    detectedAt: a.detected_at || null,
    days: days,
    tone: ANOM_TONE[a.anomaly_type] || "warn",
    pmAction: a.pm_action || null,
    pmReason: a.pm_reason || null
  };
}

export const energyLiveMethods = {
  enAnomIsLive() { return !!this.state.enAnomLive; },

  async energyLoad(opts) {
    if (this._enLoading) return;
    this._enLoading = true;
    clearTimeout(this._enRetry);
    this.setState({ enLoading: true });
    try {
      // The market-profile table rides along: it is small, it is scoped the same way, and
      // a page that shows live anomalies against a hardcoded benchmark table is telling two
      // different stories. It is optional — a failure here must not take the anomalies down.
      const [anomRes, meterRes, profRes, rollRes] = await Promise.all([
        energyApi.anomalies(), energyApi.meters(),
        energyApi.marketProfiles().catch(() => null),
        // The per-meter rollup. Optional: without it the page falls back to its own
        // arithmetic, which adds findings that overlap — so the fallback is marked.
        energyApi.anomalyRollup().catch(() => null)
      ]);
      const anomalies = (anomRes && anomRes.anomalies) || [];
      const meters = (meterRes && meterRes.meters) || [];
      const profiles = profRes && profRes.ok && profRes.markets ? profRes : null;
      // Equipment resolution only runs when an anomaly actually carries an asset_id — in
      // this database that is rare (site- and meter-scoped anomalies are the common case),
      // so most loads skip the extra round trip entirely.
      const assetIds = Array.from(new Set(anomalies.map((a) => a.asset_id).filter(Boolean)));
      const equipmentByUuid = assetIds.length ? await this.enResolveEquipment(assetIds) : {};
      this._enAttempts = 0;
      this.setState({
        enAnomLive: anomalies, enMetersLive: meters, enEquip: equipmentByUuid,
        enProfilesLive: profiles,
        // Keyed by building so the row can read its own headline without scanning.
        enRollup: rollRes && rollRes.ok && Array.isArray(rollRes.buildings)
          ? rollRes.buildings.reduce((m, b) => { if (b.building_id) m[String(b.building_id)] = b; return m; }, {})
          : null,
        enLoading: false, enError: "", enLoadedAt: new Date().toISOString()
      });
      if (opts && opts.announce) this.flash("Energy anomalies loaded — " + anomalies.length + " open, " + meters.length + " meters");
    } catch (e) {
      // The company changed while this read was in flight: api/client.js disowned the
      // response, and the switch has already started a correctly-scoped read. Reporting
      // it would put a spurious error on a register that is loading perfectly well.
      if (isStaleScope(e)) return;
      const msg = (e && e.message) || String(e);
      this._enAttempts = (this._enAttempts || 0) + 1;
      this.setState({ enLoading: false, enError: msg });
      if (this._enAttempts < RETRY_MAX) this._enRetry = setTimeout(() => this.energyLoad(), RETRY_MS);
      if (opts && opts.announce) this.flash("Energy backend unreachable — " + msg);
    } finally {
      this._enLoading = false;
    }
  },

  enRetryNow() { this._enAttempts = 0; return this.energyLoad({ announce: true }); },

  // "Run energy scan" runs the scan. It used to hand the words to the orchestrator and let
  // a model choose a tool, which is the right shape for a question and the wrong one for a
  // fixed verb: the rules are deterministic, the button has one meaning, and routing it
  // through a model added failure modes with nothing in return. With an invalid key it did
  // nothing at all, and said nothing about why. The Assets and Operations buttons already
  // call their own reads directly; this one now matches them.
  //
  // It sweeps a year by default, because a scan that reports only on the last five weeks of
  // a year of readings reads as "the year was quiet".
  async enRunScan(opts) {
    if (this.state.enScanning) return false;
    const days = (opts && opts.historyDays) || 365;
    this.setState({ enScanning: true });
    this.flash('Energy scan running — every rule, every meter, across ' + days + ' days');
    try {
      const res = await energyApi.scanAll(days);
      const made = (res && typeof res.created === 'number') ? res.created : null;
      const meters = (res && (res.meters_swept || res.meters_scanned)) || 0;
      this.flash(
        made === null
          ? 'Energy scan complete — ' + meters + ' meter(s) examined'
          : 'Energy scan complete — ' + made + ' new finding(s) across ' + meters + ' meter(s)'
      );
      // The register on screen is now out of date by exactly what the scan just wrote.
      await this.energyLoad({ announce: false });
      if (typeof this.enPositionLoad === 'function') this.enPositionLoad();
      return true;
    } catch (e) {
      this.flash('Energy scan failed — ' + ((e && e.message) || String(e)));
      return false;
    } finally {
      this.setState({ enScanning: false });
    }
  },

  // The ratings-and-duties tiles (MEES/EPCs, LL97/Energy Star/LL84, BCA/Green Mark, the AE
  // rolling benchmark and chiller position) are assembled server-side now
  // (GET /api/energy/ratings/position) from real records — an EPC's energy_rating column, a
  // filing row, a chiller reading, twelve months of consumption. All four markets load once,
  // eagerly, same as every other dataset this app loads at mount — there is no page-scoped
  // lazy fetch elsewhere in this codebase, so this does not invent one.
  async enPositionLoad(opts) {
    if (this._enPosLoading) return;
    this._enPosLoading = true;
    clearTimeout(this._enPosRetry);
    const only = (opts && opts.only) || ["UK", "US", "SG", "AE"];
    this.setState((p) => {
      const next = Object.assign({}, p.enPosByCc);
      only.forEach((cc) => { next[cc] = Object.assign({ tiles: [] }, next[cc], { loading: true }); });
      return { enPosByCc: next };
    });
    const failed = [];
    await Promise.all(only.map(async (cc) => {
      try {
        const res = await energyApi.ratingsPosition(cc);
        this._enPosAttempts = Object.assign({}, this._enPosAttempts, { [cc]: 0 });
        this.setState((p) => ({ enPosByCc: Object.assign({}, p.enPosByCc, {
          [cc]: { loading: false, error: "", tiles: (res && res.tiles) || [], buildings: res && res.buildings, loadedAt: new Date().toISOString() }
        }) }));
      } catch (e) {
        // Not a failure of this country's read — the company moved underneath it, so it
        // must not be pushed onto `failed` and retried against the new one.
        if (isStaleScope(e)) return;
        failed.push(cc);
        const msg = (e && e.message) || String(e);
        this.setState((p) => ({ enPosByCc: Object.assign({}, p.enPosByCc, {
          [cc]: { loading: false, error: msg, tiles: (p.enPosByCc[cc] || {}).tiles || [] }
        }) }));
      }
    }));
    this._enPosLoading = false;
    // Retried on its own — the same "the token wasn't ready yet" race this file's
    // componentDidMount fix addresses can still recur (a token that expires mid-session,
    // a slow refresh), and this card had no way back from a failure before.
    if (failed.length) {
      this._enPosAttempts = this._enPosAttempts || {};
      const stillRetryable = failed.filter((cc) => (this._enPosAttempts[cc] || 0) < RETRY_MAX);
      failed.forEach((cc) => { this._enPosAttempts[cc] = (this._enPosAttempts[cc] || 0) + 1; });
      if (stillRetryable.length) this._enPosRetry = setTimeout(() => this.enPositionLoad({ only: stillRetryable }), RETRY_MS);
    }
  },

  enPosRetryNow() { this._enPosAttempts = {}; return this.enPositionLoad(); },

  // energy_anomalies.asset_id is a uuid; plenum_cafm.assets.id is a short code
  // ("A0000001") — a different key space entirely. plenum_cafm.equipment sits between the
  // two (equipment_id uuid, asset_id the short code) and carries its own name, so a name
  // resolves off equipment alone without a second join to assets.
  async enResolveEquipment(ids) {
    try {
      const params = {};
      const placeholders = ids.map((id, i) => { const k = "id" + i; params[k] = id; return ":" + k; }).join(",");
      const res = await udrApi.select(
        "SELECT equipment_id, asset_id, name FROM plenum_cafm.equipment WHERE equipment_id IN (" + placeholders + ")",
        params
      );
      const out = {};
      ((res && res.rows) || []).forEach((r) => { out[r.equipment_id] = r; });
      return out;
    } catch (e) {
      return {};
    }
  },

  // Shaped anomalies, joined against the Buildings table already loaded by buildingsLive.js.
  enAnomalies() {
    const s = this.state;
    const byUuid = {};
    this.bldData().forEach((b) => { if (b.uuid) byUuid[b.uuid] = b; });
    const metersById = {};
    (s.enMetersLive || []).forEach((m) => { metersById[m.id] = m; });
    return (s.enAnomLive || []).map((a) => shapeLiveAnomaly(a, byUuid, metersById, s.enEquip || {}));
  }
};
