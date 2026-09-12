// maintenanceLive — the Maintenance page's live decisions grid and KPI tiles, from
// svc-work-order-management (api/workOrder.js). The seed dataset (src/data/hoistra-
// maintenance.js) stays as the fallback until this answers; once it does, mxVals() (logic/
// maintenance.js) reads live rows in exactly the shape the seed rows have, so it renders
// either without knowing which it holds — the same treatment energyLive.js and
// vendorsLive.js already gave the Energy and Vendors pages.
//
// Not sourced from this service — shown as "—" or left off rather than filled from the
// seed: estimated cost (WorkOrderResponse has no estimated_cost field, though the column
// exists on the table), the seed's Blocked/To raise/Deviation states (there is no such
// status on a real work order — see STATE_LABEL below for what a real one actually carries),
// and the PPM health table's visits-to-plan/missed/late/reports columns (plenum_cafm.
// maintenance_plans has no vendor, contract or visit-history columns at all).
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { workOrderApi } from '../api/workOrder.js';

const RETRY_MS = 30000;
const RETRY_MAX = 6;

// A real work order's plenum_cafm.work_orders.status, not the seed's Blocked/To raise/
// Deviation/Awaiting approval vocabulary — there is no field on the real table that carries
// those. "Awaiting approval" is the one honest overlap (pending_approval really does mean
// a human has not decided yet); the rest are named for what the status machine calls them.
export const STATE_LABEL = {
  pending_approval: 'Awaiting approval',
  preparing: 'Preparing',
  prepared: 'Prepared',
  active: 'Active'
};
// "Awaiting approval" is deliberately left out of these three — it is the one label the
// seed vocabulary already has (maintenance.js's own ST/order/GDESC), with the same meaning
// and the same priority; redefining it here would only fight that definition.
export const STATE_TONE = { Preparing: 'warn', Prepared: 'ok', Active: 'ok' };
export const STATE_ORDER = { Preparing: 4, Prepared: 5, Active: 6 };
export const STATE_DESC = {
  Preparing: 'vendor and schedule being confirmed',
  Prepared: 'ready to start, on the calendar',
  Active: 'in progress on site'
};

// One WorkOrderResponse → one Maintenance decisions-grid row (the shape maintenance.js's
// mxVals expects from MX.decisions: id, asset, b, vendor, est, state, src, trigger, detail,
// actions). `actions` is always empty — the seed's action labels (Approve, Reassign, …) are
// scripted flows with no real endpoint behind them yet; a live row offers none instead of a
// button that would do nothing.
export function shapeLiveWorkOrder(wo) {
  const w = wo || {};
  return {
    id: w.work_order_id,
    asset: w.asset || '—',
    b: w.location || '—',
    vendor: w.vendor || '—',
    est: '—',
    state: STATE_LABEL[w.status] || w.status || 'Unknown',
    src: 'Work order',
    trigger: [w.source, w.priority ? w.priority + ' priority' : null].filter(Boolean).join(' · ') || '—',
    detail: w.issue_description || '—',
    actions: [],
    createdAt: w.created_at || null
  };
}

// Every open (not completed/closed) work order, newest first — closed/completed work
// carries no decision left to make, so it does not belong in "decisions owed".
export function shapeLiveDecisions(workOrders) {
  return (workOrders || [])
    .filter((w) => w && w.status !== 'completed' && w.status !== 'closed')
    .map(shapeLiveWorkOrder)
    .sort((a, b) => (a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0));
}

// GET /api/dashboard/stats → the four mxCards tiles. Returns null (no live tiles) rather
// than zeros when there is nothing to shape, so the caller can fall back to the seed cards.
export function shapeDashboardTiles(stats) {
  if (!stats) return null;
  const byStatus = stats.by_status || {};
  const byPriority = stats.by_priority || {};
  const open = ['pending_approval', 'preparing', 'prepared', 'active'].reduce((q, k) => q + (byStatus[k] || 0), 0);
  const urgent = (byPriority.urgent || 0) + (byPriority.critical || 0);
  return [
    { l: 'Open work orders', v: String(open), s: (stats.total || 0) + ' total on record', tone: 'warn' },
    { l: 'Awaiting approval', v: String(byStatus.pending_approval || 0), s: 'drafted, waiting on a decision', tone: 'warn' },
    { l: 'Urgent / critical open', v: String(urgent), s: 'by priority, across all open statuses', tone: urgent ? 'risk' : 'ok' },
    { l: 'Created today', v: String(stats.created_today || 0), s: 'new work orders today', tone: 'ok' }
  ];
}

export const maintenanceLiveMethods = {
  async mxLiveLoad(opts) {
    if (this._mxLiveLoading) return;
    this._mxLiveLoading = true;
    clearTimeout(this._mxLiveRetry);
    this.setState({ mxLiveLoading: true });
    try {
      const [stats, workOrders] = await Promise.all([workOrderApi.dashboardStats(), workOrderApi.workOrders({ limit: 200 })]);
      this._mxLiveAttempts = 0;
      this.setState({
        mxStatsLive: stats || null, mxWosLive: workOrders || [],
        mxLiveLoading: false, mxLiveError: '', mxLiveLoadedAt: new Date().toISOString()
      });
      if (opts && opts.announce) this.flash('Work order register loaded — ' + ((workOrders || []).length) + ' on record');
    } catch (e) {
      const msg = (e && e.message) || String(e);
      this._mxLiveAttempts = (this._mxLiveAttempts || 0) + 1;
      this.setState({ mxLiveLoading: false, mxLiveError: msg });
      if (this._mxLiveAttempts < RETRY_MAX) this._mxLiveRetry = setTimeout(() => this.mxLiveLoad(), RETRY_MS);
      if (opts && opts.announce) this.flash('Work order register unreachable — ' + msg);
    } finally {
      this._mxLiveLoading = false;
    }
  },

  mxLiveRetryNow() { this._mxLiveAttempts = 0; return this.mxLiveLoad({ announce: true }); },

  mxLiveVals(s) {
    const stats = s.mxStatsLive || null;
    const wos = s.mxWosLive;
    const on = !!(stats || (wos && wos.length));
    return {
      mxLiveOn: on,
      mxLiveTiles: shapeDashboardTiles(stats),
      mxLiveDecisions: wos ? shapeLiveDecisions(wos) : null,
      mxLiveSourceLabel: s.mxLiveLoading ? 'Reading plenum_cafm.work_orders…'
        : s.mxLiveError ? 'Unreachable — ' + s.mxLiveError
        : on ? 'Live · svc-work-order-management'
        : 'Not loaded',
      mxLiveSourceDot: s.mxLiveError ? 'var(--st-risk)' : on ? 'var(--st-ok)' : 'var(--color-neutral-600)',
      mxLiveRetryShow: !s.mxLiveLoading && (!on || !!s.mxLiveError) ? 'inline' : 'none',
      mxLiveRetry: () => this.mxLiveRetryNow()
    };
  }
};
