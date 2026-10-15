// vendorBuildings — vendor ↔ building assignments (12 Oct 2026).
//
// "Buildings served" used to be only what certificates said: a vendor just hoisted served
// nowhere, however many buildings the admin named. An assignment is now stored
// (ops-intelligence compliance/vendor_buildings.py): made from Hoist vendors' buildings
// question (source 'hoist') or the vendor's Buildings served tab ('manual'), admin only, ended
// rather than deleted, and listed in the vendor trail.
//
// The assignments are merged into the compliance register where it is read (complianceLive.js
// ccData), so Vendor impact, Buildings served and Hoist vendors all see them. A database not set
// up for them yet answers {available: false}; nothing then is merged, and an assign says why.
//
// Tested in test/vendorHoist.test.mjs; the methods are mixed into HoistraLogic.prototype.
import { complianceApi } from '../api/compliance.js';

const low = (s) => String(s || '').trim().toLowerCase();
const errOf = (e) => {
  const d = e && (e.body || e.data || e.detail);
  const inner = d && (d.detail || d);
  return String((inner && inner.error) || (e && e.message) || 'the vendor service could not be reached').replace(/[.\s]+$/, '');
};
const reasonOf = (e) => {
  const d = e && (e.body || e.data || e.detail);
  const inner = d && (d.detail || d);
  return (inner && inner.reason) || null;
};

// Pure: the register with each live assignment added to its vendor's `serves` (and listed in
// `assigned`, so a screen can say which buildings were assigned rather than evidenced). A vendor
// the register does not list — nothing on file yet — is added as one with no accreditations.
// The register passed in is not changed.
export function withAssignments(reg, rows) {
  if (!reg || !Array.isArray(rows) || !rows.length) return reg;
  const vendors = (reg.vendors || []).map((v) => Object.assign({}, v, { serves: (v.serves || []).slice(), assigned: (v.assigned || []).slice(), assignedIds: Object.assign({}, v.assignedIds || {}) }));
  const byId = {}, byName = {};
  vendors.forEach((v) => { if (v.id) byId[low(v.id)] = v; byName[low(v.name)] = v; });
  rows.forEach((r) => {
    const b = r && (r.building_name || null);
    if (!r || !r.vendor_id || !b) return;
    let v = byId[low(r.vendor_id)] || (r.vendor_name ? byName[low(r.vendor_name)] : null);
    if (!v) {
      if (!r.vendor_name) return;
      v = { name: r.vendor_name, cc: null, id: r.vendor_id, cov: 0, on: 0, req: 1, block: 'Clear', worst: 'None on file', sev: 'none',
        serves: [], assigned: [], assignedIds: {}, gaps: [], certs: 0, noDoc: 0, spec: '—', noAccreditations: true };
      vendors.push(v);
      byId[low(v.id)] = v; byName[low(v.name)] = v;
    }
    if (v.serves.indexOf(b) < 0) v.serves.push(b);
    if (v.assigned.indexOf(b) < 0) v.assigned.push(b);
    // The building's own id, kept so ending the assignment never looks a building up by name.
    if (r.building_id) v.assignedIds[b] = r.building_id;
  });
  return Object.assign({}, reg, { vendors: vendors });
}

export const vendorBuildingsMethods = {
  // The company's assignments. [] where the database is not set up for them (vbAvailable false).
  async vbLoad() {
    // A read asked for while one is running is run after it, so an assign or unassign that
    // lands mid-read is never left out of the rows (second review).
    if (this._vbLoading) { this._vbAgain = true; return; }
    this._vbLoading = true;
    try {
      const r = await complianceApi.vendorBuildings();
      const available = !(r && r.available === false);
      this.setState({ vbRows: available ? ((r && r.assignments) || []) : [], vbAvailable: available, vbError: '' });
    } catch (e) {
      this.setState({ vbError: errOf(e) });
    } finally {
      this._vbLoading = false;
      if (this._vbAgain) { this._vbAgain = false; this.vbLoad(); }
    }
  },

  // Assigns; never throws. {ok, assigned: [names], already: [names]} or {ok: false, error, notSetUp}.
  async vbAssign(vendorId, buildingIds, source) {
    const ids = (buildingIds || []).filter(Boolean);
    if (!vendorId || !ids.length) return { ok: false, error: 'no building to assign' };
    try {
      const r = await complianceApi.assignVendorBuildings(vendorId, ids, source);
      if (r && r.ok === false) return { ok: false, error: r.error || 'the assignment was refused', notSetUp: r.reason === 'not_set_up' };
      this.vbLoad();
      return { ok: true, assigned: ((r && r.assigned) || []).map((b) => b.name), already: ((r && r.already) || []).map((b) => b.name) };
    } catch (e) {
      return { ok: false, error: errOf(e), notSetUp: reasonOf(e) === 'not_set_up' };
    }
  },

  async vbUnassign(vendorId, buildingId) {
    try {
      await complianceApi.unassignVendorBuilding(vendorId, buildingId);
      this.vbLoad();
      return { ok: true };
    } catch (e) {
      return { ok: false, error: errOf(e) };
    }
  }
};
