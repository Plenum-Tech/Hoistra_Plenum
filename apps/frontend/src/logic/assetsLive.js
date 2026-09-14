// assetsLive — the Assets page's live asset register, from svc-work-order-management
// (api/workOrder.js): GET /api/assets joined against GET /api/work-orders/.
//
// Two views over the same fetch:
//  - asLiveVals(s): the flat register (name/manufacturer/model/serial/status/open-WOs),
//    unchanged from before.
//  - asLiveGroups(s): buildings → assets, grouped on the real assets.building_id column.
//    This is a real building/asset tree, but it is NOT a like-for-like rebuild of the
//    condition-scan section above (logic/assets.js asVals): that section's per-section
//    EUI-vs-reference, anomaly-to-asset attribution and asset replacement-value curve have
//    no real backend behind them anywhere in this codebase (no section concept, no real
//    class/replace-value/wear-life register, anomalies are site/meter-scoped, not reliably
//    asset-scoped — see logic/energyLive.js). What IS real per asset: health_score,
//    criticality, installation_date, asset_code and category_name, plus per-building
//    cost-drivers and per-asset work history/certificates, both fetched lazily (eager
//    per-asset fetching would be hundreds of extra round trips on page load for data most
//    rows will never have opened).
//
// GET /api/assets is portfolio-wide — every asset the caller may see across all their
// buildings, no building_id required — and resolves category_id to category_name itself
// (services/asset_catalogue.py), so nothing here has to call a second service to turn a key
// into a word. health_score arrives as a float, so it is rounded for display rather than
// printed raw.
//
// opsApi.assetCriticalities() (the L1/L2/L3 approval register on svc-operations-intelligence)
// is deliberately NOT merged in here: that register's asset_id key-space against this
// service's is unverified (see logic/energyLive.js's equipment/asset key-space notes), and
// its L1/L2/L3 tiers are a different scale from assets.criticality's High/Medium/Low, which
// already sits on this exact row with no cross-service join needed.
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { workOrderApi } from '../api/workOrder.js';
import { energyApi } from '../api/energy.js';
import { complianceApi } from '../api/compliance.js';
import { isStaleScope } from '../api/client.js';
import { isUnallocated } from './auth.js';

const RETRY_MS = 30000;
const RETRY_MAX = 6;

const OPEN_STATUSES = new Set(['pending_approval', 'preparing', 'prepared', 'active']);

// assets: AssetResponse[]  workOrders: WorkOrderResponse[] → one row per asset, with its
// count of open (not completed/closed) work orders matched by name (case-insensitive).
export function shapeLiveAssetRows(assets, workOrders) {
  const openByName = {};
  (workOrders || []).forEach((w) => {
    if (!w || !w.asset || !OPEN_STATUSES.has(w.status)) return;
    const k = String(w.asset).trim().toLowerCase();
    openByName[k] = (openByName[k] || 0) + 1;
  });
  return (assets || []).map((a) => ({
    id: a.asset_id,
    name: a.asset_name,
    code: a.asset_code || '',
    manufacturer: a.manufacturer || '—',
    model: a.model || '—',
    serial: a.serial_number || '—',
    // `active` is derived server-side from the status string; `status` is that raw string
    // ("active", "retired", "decommissioned", whatever the import wrote). Show the real one
    // when there is one rather than flattening everything to active/inactive.
    status: a.status || (a.active === false ? 'inactive' : 'active'),
    openWorkOrders: openByName[String(a.asset_name || '').trim().toLowerCase()] || 0
  }));
}

// Health-score band → the same Threat/Watch/In-control language the condition-scan section
// uses, but driven from a real number instead of an energy-deviation rule. null = never
// scored (dormant grey), not "in control" — an unscored asset is not a known-good one.
export function healthBand(score) {
  if (score === null || score === undefined) return { cond: 'unscored', label: 'Not scored', tone: 'dormant' };
  if (score < 40) return { cond: 'threat', label: 'Threat', tone: 'risk' };
  if (score < 70) return { cond: 'watch', label: 'Watch', tone: 'warn' };
  return { cond: 'ok', label: 'In control', tone: 'ok' };
}

const TONE_COLOR = { risk: 'var(--st-risk)', warn: 'var(--st-warn)', ok: 'var(--st-ok)', dormant: 'var(--color-neutral-500)' };
const TONE_BG = { risk: 'var(--st-risk-bg, rgba(220,80,80,0.12))', warn: 'var(--st-warn-bg, rgba(210,160,50,0.12))', ok: 'var(--st-ok-bg, rgba(70,170,110,0.12))', dormant: 'var(--color-bg)' };

export const assetsLiveMethods = {
  async asLiveLoad(opts) {
    if (this._asLiveLoading) return;
    this._asLiveLoading = true;
    clearTimeout(this._asLiveRetry);
    this.setState({ asLiveLoading: true });
    try {
      const [assets, workOrders] = await Promise.all([workOrderApi.assets({ limit: 200 }), workOrderApi.workOrders({ limit: 200 })]);
      this._asLiveAttempts = 0;
      this.setState({
        asLive: assets || [], asLiveWos: workOrders || [],
        asLiveLoading: false, asLiveError: '', asLiveLoadedAt: new Date().toISOString()
      });
      if (opts && opts.announce) this.flash('Asset register loaded — ' + (assets || []).length + ' assets');
    } catch (e) {
      // The company changed while this read was in flight: api/client.js disowned the
      // response, and the switch has already started a correctly-scoped read. Reporting
      // it would put a spurious error on a register that is loading perfectly well.
      if (isStaleScope(e)) return;
      const msg = (e && e.message) || String(e);
      this._asLiveAttempts = (this._asLiveAttempts || 0) + 1;
      this.setState({ asLiveLoading: false, asLiveError: msg });
      if (this._asLiveAttempts < RETRY_MAX) this._asLiveRetry = setTimeout(() => this.asLiveLoad(), RETRY_MS);
      if (opts && opts.announce) this.flash('Asset register unreachable — ' + msg);
    } finally {
      this._asLiveLoading = false;
    }
  },

  asLiveRetryNow() { this._asLiveAttempts = 0; return this.asLiveLoad({ announce: true }); },

  asLiveVals(s) {
    const on = !!(s.asLive && s.asLive.length);
    const rows = shapeLiveAssetRows(s.asLive || [], s.asLiveWos || []);
    return {
      asLiveOn: on,
      asLiveRows: rows,
      asLiveEmpty: on ? 'none' : 'block',
      // "No assets on record" is false for an account allocated to nothing: the register is
      // full, this caller just cannot see into it. Naming the real cause is the difference
      // between asking an admin for access and hunting a data bug that does not exist.
      asLiveEmptyText: s.asLiveLoading ? 'Reading plenum_cafm.assets…'
        : s.asLiveError ? 'Unreachable — ' + s.asLiveError
        : isUnallocated(s.account)
          ? 'You are not allocated to any building yet, so no assets are in scope for your account — the register itself may be full. Ask an admin to allocate you, or to change your role to admin.'
        : 'No assets on record.',
      asLiveCount: rows.length + (rows.length === 1 ? ' asset' : ' assets') + ' · ' + rows.filter((r) => r.openWorkOrders > 0).length + ' with an open work order',
      asLiveSourceLabel: s.asLiveLoading ? 'Reading plenum_cafm.assets…'
        : s.asLiveError ? 'Unreachable — ' + s.asLiveError
        : on ? 'Live · svc-work-order-management'
        : 'Not loaded',
      asLiveSourceDot: s.asLiveError ? 'var(--st-risk)' : on ? 'var(--st-ok)' : 'var(--color-neutral-600)',
      asLiveRetryShow: !s.asLiveLoading && (!on || !!s.asLiveError) ? 'inline' : 'none',
      asLiveRetry: () => this.asLiveRetryNow()
    };
  },

  // ── Buildings → assets, on real data ──────────────────────────────────────────────

  asLiveToggleBuilding(bId) {
    this.setState((p) => ({ asLiveOpenB: (p.asLiveOpenB || []).indexOf(bId) > -1 ? (p.asLiveOpenB || []).filter((x) => x !== bId) : (p.asLiveOpenB || []).concat([bId]) }));
    // Lazy: cost-drivers for a building only get read the first time its group opens.
    const already = (this.state.asLiveCost || {})[bId];
    if (!already) this.asLiveLoadCost(bId);
  },

  async asLiveLoadCost(bId) {
    this.setState((p) => ({ asLiveCost: Object.assign({}, p.asLiveCost, { [bId]: { loading: true } }) }));
    try {
      const res = await energyApi.costDrivers(bId);
      this.setState((p) => ({ asLiveCost: Object.assign({}, p.asLiveCost, { [bId]: res || {} }) }));
    } catch (e) {
      this.setState((p) => ({ asLiveCost: Object.assign({}, p.asLiveCost, { [bId]: { error: (e && e.message) || String(e) } }) }));
    }
  },

  // Opens the shared DetailDrawer for one asset, filled with what the row already has,
  // then fills in work-history and certificates once they answer (both lazy — see file header).
  asLiveOpenAsset(row) {
    const chain = [
      { a: 'building', t: row.buildingName || 'Unlinked' },
      { a: 'category', t: row.categoryText },
      { a: 'criticality', t: row.criticality || 'Not set' },
      { a: 'health', t: row.healthLabel },
      { a: 'installed', t: row.installedText },
      { a: 'status', t: row.status },
      { a: 'work history', t: 'Reading svc-operations-intelligence…' },
      { a: 'certificates', t: 'Reading the compliance register…' }
    ];
    this.setState({
      detail: {
        icon: 'ph-cube', color: TONE_COLOR[row.tone], module: 'Assets', title: row.name,
        meta: (row.code ? row.code + ' · ' : '') + (row.manufacturer || '—') + ' · ' + (row.model || '—') + ' · ' + (row.serial || '—'),
        body: row.healthLabel + (row.criticality ? ' · ' + row.criticality + ' criticality' : '') + '. ' + (row.openWorkOrders ? row.openWorkOrders + (row.openWorkOrders === 1 ? ' open work order' : ' open work orders') + ' matched by asset name (work_orders.asset is free text, not a foreign key).' : 'No open work orders matched by name.'),
        chain: chain, refinement: ''
      },
      detailFields: [], detailActions: []
    });
    this._asLiveDetailAssetId = row.id;
    Promise.all([
      energyApi.assetWorkHistory(row.id).catch((e) => ({ ok: false, error: (e && e.message) || String(e) })),
      complianceApi.listCertificates({ asset_id: row.id, limit: 20 }).catch((e) => ({ ok: false, error: (e && e.message) || String(e) }))
    ]).then(([hist, certs]) => {
      // The drawer may have moved on to a different record by the time these answer.
      if (this._asLiveDetailAssetId !== row.id || !this.state.detail || this.state.detail.title !== row.name) return;
      const wos = (hist && hist.ok !== false && Array.isArray(hist.work_orders)) ? hist.work_orders : [];
      const histText = hist && hist.error ? 'Unreachable — ' + hist.error
        : wos.length ? wos.slice(0, 5).map((w) => (w.wo_code || w.id || 'WO') + ' · billed ' + (w.billed != null ? w.billed : '—')).join('; ')
        : 'None on record for this asset.';
      const certList = (certs && certs.ok !== false && Array.isArray(certs.certificates)) ? certs.certificates : (Array.isArray(certs) ? certs : []);
      const certText = certs && certs.error ? 'Unreachable — ' + certs.error
        : certList.length ? certList.slice(0, 5).map((c) => (c.certificate_type_code || c.cert_type || 'Certificate') + (c.expiry_date ? ' · expires ' + c.expiry_date : '')).join('; ')
        : 'None on record for this asset.';
      this.setState((p) => {
        if (!p.detail || p.detail.title !== row.name) return {};
        const nextChain = p.detail.chain.map((c) => c.a === 'work history' ? { a: c.a, t: histText } : c.a === 'certificates' ? { a: c.a, t: certText } : c);
        return { detail: Object.assign({}, p.detail, { chain: nextChain }) };
      });
    });
  },

  // Buildings → assets, keyed on the real assets.building_id column, joined to the
  // buildings register buildingsLive.js already loaded (bldData()) for a display name.
  asLiveGroups(s) {
    // Open-work-order counts, computed once against the shaped rows (name-matched — see
    // shapeLiveAssetRows), then read back by asset_id rather than merged into the raw
    // objects — s.asLive and shapeLiveAssetRows' output share several key names
    // (manufacturer/model/status) with different values (raw vs. defaulted/derived), and
    // merging them would let the raw value silently win over the defaulted one.
    const openWoById = {};
    shapeLiveAssetRows(s.asLive || [], s.asLiveWos || []).forEach((r) => { openWoById[r.id] = r.openWorkOrders; });
    const bldByBuildingId = {};
    (this.bldData ? this.bldData() : []).forEach((b) => { if (b.buildingId) bldByBuildingId[b.buildingId] = b; });
    const cost = s.asLiveCost || {};
    const openB = s.asLiveOpenB || [];
    const byBuilding = {};
    const unlinked = [];
    (s.asLive || []).forEach((a) => {
      const bId = a.building_id;
      // health_score is NUMERIC on the table and arrives as a float — round for display so a
      // stored 72.5 does not render as "72.5/100", but band on the real value.
      const score = typeof a.health_score === 'number' ? a.health_score : null;
      const band = healthBand(score);
      const shaped = {
        id: a.asset_id, name: a.asset_name, code: a.asset_code || '',
        manufacturer: a.manufacturer || '—', model: a.model || '—', serial: a.serial_number || '—',
        status: a.status || (a.active === false ? 'inactive' : 'active'), openWorkOrders: openWoById[a.asset_id] || 0,
        criticality: a.criticality || null,
        healthScore: score,
        healthLabel: score === null ? 'Not scored' : 'Health score ' + Math.round(score) + '/100',
        // Resolved server-side by services/asset_catalogue.py, so a key never reaches the UI.
        categoryName: a.category_name || null,
        categoryText: a.category_name || (a.category_id ? 'Uncategorised name not on file' : 'No category set'),
        installedText: a.installation_date ? String(a.installation_date) : 'Not on record',
        tone: band.tone, condLabel: band.label,
        color: TONE_COLOR[band.tone], bg: TONE_BG[band.tone],
        buildingName: bId && bldByBuildingId[bId] ? bldByBuildingId[bId].name : (bId ? 'Building ' + String(bId).slice(0, 8) + '…' : 'Unlinked')
      };
      shaped.open = () => this.asLiveOpenAsset(shaped);
      if (!bId) { unlinked.push(shaped); return; }
      if (!byBuilding[bId]) byBuilding[bId] = [];
      byBuilding[bId].push(shaped);
    });
    const groups = Object.keys(byBuilding).map((bId) => {
      const assetsIn = byBuilding[bId];
      const bMeta = bldByBuildingId[bId];
      const name = bMeta ? bMeta.name : 'Building ' + bId.slice(0, 8) + '…';
      const nThreat = assetsIn.filter((a) => a.tone === 'risk').length;
      const nWatch = assetsIn.filter((a) => a.tone === 'warn').length;
      const nOk = assetsIn.filter((a) => a.tone === 'ok').length;
      const nUnscored = assetsIn.filter((a) => a.tone === 'dormant').length;
      const isOpen = openB.indexOf(bId) > -1;
      const c = cost[bId];
      const costLoading = !!(c && c.loading);
      const costError = c && c.error;
      const costAssets = c && Array.isArray(c.assets) ? c.assets : [];
      const costTotal = c && c.totals && typeof c.totals.billed === 'number' ? c.totals.billed : null;
      return {
        id: bId, name: name, open: isOpen, caret: isOpen ? 'ph-caret-down' : 'ph-caret-right',
        toggle: () => this.asLiveToggleBuilding(bId),
        counts: nThreat + ' threat · ' + nWatch + ' watch · ' + nOk + ' in control' + (nUnscored ? ' · ' + nUnscored + ' not scored' : ''),
        assetCount: assetsIn.length + (assetsIn.length === 1 ? ' asset' : ' assets'),
        costShow: isOpen ? 'block' : 'none',
        costLoading: costLoading,
        costError: costError ? ('Cost drivers unreachable — ' + costError) : '',
        costEmpty: !costLoading && !costError && !costAssets.length ? 'block' : 'none',
        costTotalText: costTotal !== null ? 'Billed against this building: ' + costTotal.toLocaleString('en-GB', { style: 'currency', currency: 'GBP', minimumFractionDigits: 0 }) : '',
        costRows: costAssets.slice(0, 5).map((ca) => ({
          name: ca.asset_name || ca.asset_code || 'Unnamed asset',
          billed: typeof ca.billed === 'number' ? ca.billed.toLocaleString('en-GB', { style: 'currency', currency: 'GBP', minimumFractionDigits: 0 }) : '—',
          overContract: typeof ca.over_contract === 'number' && ca.over_contract > 0 ? '+' + ca.over_contract.toLocaleString('en-GB', { style: 'currency', currency: 'GBP', minimumFractionDigits: 0 }) + ' over contract' : ''
        })),
        rows: assetsIn.sort((p, q) => (p.tone === 'risk' ? 0 : p.tone === 'warn' ? 1 : p.tone === 'dormant' ? 2 : 3) - (q.tone === 'risk' ? 0 : q.tone === 'warn' ? 1 : q.tone === 'dormant' ? 2 : 3))
      };
    }).sort((p, q) => p.name.localeCompare(q.name));
    return {
      asLiveTreeGroups: groups,
      asLiveTreeUnlinked: unlinked,
      asLiveTreeUnlinkedShow: unlinked.length ? 'flex' : 'none',
      asLiveTreeEmpty: groups.length || unlinked.length ? 'none' : 'block'
    };
  }
};
