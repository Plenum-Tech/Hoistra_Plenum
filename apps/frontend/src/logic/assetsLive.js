// assetsLive — the Assets page's live asset register, from svc-work-order-management
// (api/workOrder.js): GET /api/assets joined against GET /api/work-orders/.
//
// This is deliberately a separate block from the page's condition-scan section (asVals in
// logic/assets.js), not a replacement of it. That section's building → section → asset tree,
// EUI-vs-reference condition rules and asset replacement-value curve all key off
// plenum_cafm.sites/buildings (svc-operations-intelligence) and a class/value register that
// plenum_cafm.assets does not have — the two data models are not joined in the database, so
// forcing a merge would mean inventing a link that does not exist. The instrumented-assets
// (IoT/telemetry, failure model) section has no backend at all — no sensor ingestion, no
// readings table — and stays seed-only for the same reason: nothing to source it from yet.
//
// What plenum_cafm.assets actually carries: name, code, manufacturer, model, serial number,
// status. The join to a work order is by name (work_orders.asset is free text, not a
// foreign key) — the only key the two tables share.
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { workOrderApi } from '../api/workOrder.js';

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
    manufacturer: a.manufacturer || '—',
    model: a.model || '—',
    serial: a.serial_number || '—',
    status: a.active === false ? 'inactive' : 'active',
    openWorkOrders: openByName[String(a.asset_name || '').trim().toLowerCase()] || 0
  }));
}

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
      asLiveEmptyText: s.asLiveLoading ? 'Reading plenum_cafm.assets…' : s.asLiveError ? 'Unreachable — ' + s.asLiveError : 'No assets on record.',
      asLiveCount: rows.length + (rows.length === 1 ? ' asset' : ' assets') + ' · ' + rows.filter((r) => r.openWorkOrders > 0).length + ' with an open work order',
      asLiveSourceLabel: s.asLiveLoading ? 'Reading plenum_cafm.assets…'
        : s.asLiveError ? 'Unreachable — ' + s.asLiveError
        : on ? 'Live · svc-work-order-management'
        : 'Not loaded',
      asLiveSourceDot: s.asLiveError ? 'var(--st-risk)' : on ? 'var(--st-ok)' : 'var(--color-neutral-600)',
      asLiveRetryShow: !s.asLiveLoading && (!on || !!s.asLiveError) ? 'inline' : 'none',
      asLiveRetry: () => this.asLiveRetryNow()
    };
  }
};
