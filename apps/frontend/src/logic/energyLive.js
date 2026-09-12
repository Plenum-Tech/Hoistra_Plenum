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

export function moneyGBP(n) {
  const v = Math.round(Math.abs(n || 0));
  const s = "£" + (v >= 1000 ? Math.round(v / 1000) + "k" : String(v));
  return (n || 0) < 0 ? "−" + s : s;
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
    impact: typeof a.financial_gbp === "number" ? moneyGBP(a.financial_gbp) : "—",
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
      const [anomRes, meterRes] = await Promise.all([energyApi.anomalies(), energyApi.meters()]);
      const anomalies = (anomRes && anomRes.anomalies) || [];
      const meters = (meterRes && meterRes.meters) || [];
      // Equipment resolution only runs when an anomaly actually carries an asset_id — in
      // this database that is rare (site- and meter-scoped anomalies are the common case),
      // so most loads skip the extra round trip entirely.
      const assetIds = Array.from(new Set(anomalies.map((a) => a.asset_id).filter(Boolean)));
      const equipmentByUuid = assetIds.length ? await this.enResolveEquipment(assetIds) : {};
      this._enAttempts = 0;
      this.setState({
        enAnomLive: anomalies, enMetersLive: meters, enEquip: equipmentByUuid,
        enLoading: false, enError: "", enLoadedAt: new Date().toISOString()
      });
      if (opts && opts.announce) this.flash("Energy anomalies loaded — " + anomalies.length + " open, " + meters.length + " meters");
    } catch (e) {
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
