// vendorMaster — the company's vendor list itself (12 Oct 2026).
//
// The Vendors directory and the Hoist vendors list were built from what the other registers say
// about vendors — scorecards, contracts, certificates — so a vendor with none of those (one just
// hoisted with no certificate) was in the database and on no screen. This reads the list.
//
// Source: cafm-connector's GET /api/v1/plenum/vendors, company-scoped for any signed-in caller
// (api/security.py guard), a superadmin's company named outright. That route maps vendor ids as
// UUIDs, and the vendors table holds older non-UUID ids on some companies, which fails the whole
// read; an admin then reads the same list through svc-udr's SELECT-only query, ids cast to text
// (svc-udr's own company-scoped views apply). A plain user is never sent there — it is an admin
// route — so for them a failed read just leaves the lists as the other registers make them.
//
// shapeVendorList() is pure; the methods are mixed into HoistraLogic.prototype.
import { connectorApi } from '../api/connector.js';
import { udrApi } from '../api/udr.js';
import { canAdmin } from './auth.js';
import { isStaleScope } from '../api/client.js';

const PAGE = 500;
const MAX = 5000;

// A deleted vendor with history stays in the table, retired: status 'deleted' (Hussain, 12 Oct
// 2026). It is no longer the company's vendor, so it is not in the list.
export function shapeVendorList(rows) {
  return (rows || []).filter((r) => r && r.id && String(r.vendor_name || '').trim() && String(r.status || '').trim().toLowerCase() !== 'deleted')
    .map((r) => ({ id: String(r.id), name: String(r.vendor_name).trim().replace(/\s+/g, ' ') }));
}

export const vendorMasterMethods = {
  async vmLoad() {
    if (this._vmLoading) return;
    const s = this.state;
    const org = s.viewOrgId || (s.account && s.account.organization_id) || null;
    if (!s.signedIn || !org) return;
    this._vmLoading = true;
    this.setState({ vmLoading: true });
    let rows = null, source = null, error = '';
    try {
      const all = [];
      for (let offset = 0; offset < MAX; offset += PAGE) {
        const r = await connectorApi.listVendors({ organization_id: org, limit: PAGE, offset: offset });
        const data = (r && r.data) || [];
        all.push.apply(all, data);
        if (data.length < PAGE || all.length >= Number(r.total || 0)) break;
      }
      rows = shapeVendorList(all);
      source = 'connector';
    } catch (e) {
      if (isStaleScope(e)) { this._vmLoading = false; return this.setState({ vmLoading: false }); }
      error = (e && e.message) || 'the vendor list could not be read';
      if (canAdmin(s.account)) {
        try {
          // Retired vendors are left out by the same rule as svc-operations-intelligence's
          // shared/vendor_identity.py live_vendor_sql, the source of truth for it — status read
          // through to_jsonb, so a vendors table without the column still reads (12 Oct 2026).
          const r = await udrApi.select('SELECT v.id::text AS id, v.vendor_name FROM plenum_cafm.vendors v WHERE v.organization_id::text = :org AND COALESCE(lower(to_jsonb(v)->>\'status\'),\'active\') <> \'deleted\' ORDER BY v.vendor_name LIMIT ' + MAX, { org: String(org) });
          rows = shapeVendorList((r && r.rows) || []);
          source = 'udr';
          error = '';
        } catch (e2) {
          if (isStaleScope(e2)) { this._vmLoading = false; return this.setState({ vmLoading: false }); }
          error = error + '; ' + ((e2 && e2.message) || 'the fallback read failed');
        }
      }
    }
    this._vmLoading = false;
    this.setState(rows ? { vmRows: rows, vmSource: source, vmError: '', vmLoading: false } : { vmError: error, vmLoading: false });
  }
};
