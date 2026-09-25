// assetsLive — loading the Assets page's real register, and the one band rule over it.
// GET /api/assets joined against GET /api/work-orders/ on svc-work-order-management
// (api/workOrder.js), plus the per-building billed cost drivers on
// svc-operations-intelligence.
//
// GET /api/assets is portfolio-wide — every asset the caller may see across all their
// buildings, no building_id required, already narrowed to their allocation — and resolves
// category_id to category_name itself (services/asset_catalogue.py), so nothing here has to
// call a second service to turn a key into a word.
//
// Shaping the register into the page lives in logic/assetsCondition.js, which also records
// which panels have no backend and are therefore left empty rather than invented.
//
// opsApi.assetCriticalities() (the L1/L2/L3 approval register on svc-operations-intelligence)
// is deliberately NOT merged in: that register's asset_id key-space against this service's is
// unverified (see logic/energyLive.js's equipment/asset key-space notes), and its L1/L2/L3
// tiers are a different scale from assets.criticality's High/Medium/Low, which already sits
// on this exact row with no cross-service join needed.
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { workOrderApi } from '../api/workOrder.js';
import { energyApi } from '../api/energy.js';
import { isStaleScope } from '../api/client.js';

const RETRY_MS = 30000;
const RETRY_MAX = 6;

// Health-score band → the Threat/Watch/In-control language the page speaks, from a real
// number. null = never scored (dormant grey), not "in control" — an unscored asset is not a
// known-good one. health_score is `integer` on plenum_cafm.assets with a 0-100 check
// constraint, so a fractional score should never arrive; banding tolerates one anyway.
export function healthBand(score) {
  if (score === null || score === undefined) return { cond: 'unscored', label: 'Not scored', tone: 'dormant' };
  if (score < 40) return { cond: 'threat', label: 'Threat', tone: 'risk' };
  if (score < 70) return { cond: 'watch', label: 'Watch', tone: 'warn' };
  return { cond: 'ok', label: 'In control', tone: 'ok' };
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

  // Lazy: a building's billed cost drivers are read the first time its group is opened.
  // Eager fetching would be one round trip per building on every page load, for money most
  // rows will never have opened.
  async asLiveLoadCost(bId) {
    this.setState((p) => ({ asLiveCost: Object.assign({}, p.asLiveCost, { [bId]: { loading: true } }) }));
    try {
      const res = await energyApi.costDrivers(bId);
      this.setState((p) => ({ asLiveCost: Object.assign({}, p.asLiveCost, { [bId]: res || {} }) }));
    } catch (e) {
      this.setState((p) => ({ asLiveCost: Object.assign({}, p.asLiveCost, { [bId]: { error: (e && e.message) || String(e) } }) }));
    }
  }
};
