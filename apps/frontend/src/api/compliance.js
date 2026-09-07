// api/compliance — svc-operations-intelligence, Feature A (Compliance Engine).
// Routes: apps/backend/.../svc-operations-intelligence/src/api/routes/compliance.py
// Mounted behind the gateway at /backend/ops-intelligence/ → router prefix /api/compliance.
import { BASES, ORG_ID, apiFetch } from './client.js';

const B = BASES.opsIntelligence;
const withOrg = (q) => (ORG_ID ? Object.assign({ organization_id: ORG_ID }, q || {}) : (q || {}));

export const complianceApi = {
  health: () => apiFetch(B, '/health', { timeoutMs: 6000 }),

  // Every certificate (Building + Vendor scope) with the enrichment the dashboard reads:
  // certificate_type_name, vendor_name, vendor_block_state, risk_badge, forensics_*.
  listCertificates: (query) =>
    apiFetch(B, '/api/compliance/certificates', { query: withOrg(Object.assign({ limit: 1000 }, query || {})) }),

  // Per-building / per-vendor CountryPack coverage. `required` is pack completeness,
  // not a compliance score — the response says so in required_basis_note.
  buildingCoverage: (countryCode) =>
    apiFetch(B, '/api/compliance/coverage/buildings', { query: withOrg({ country_code: countryCode }) }),
  vendorCoverage: (countryCode) =>
    apiFetch(B, '/api/compliance/coverage/vendors', { query: withOrg({ country_code: countryCode }) }),

  // The regulation pack: certificate types by scope, names for the codes on certificates.
  countryPack: (countryCode) =>
    apiFetch(B, '/api/compliance/country-pack', { query: { country_code: countryCode } }),

  savedSpaceSummary: () => apiFetch(B, '/api/compliance/saved-space/summary', { query: withOrg() }),

  // A1 scan: Building (A2) + Vendor (A3) workers, alert ladder, adversary check.
  runScan: (body) =>
    apiFetch(B, '/api/compliance/scan', {
      method: 'POST',
      body: Object.assign({ scope: 'all' }, ORG_ID ? { organization_id: ORG_ID } : {}, body || {}),
      timeoutMs: 180000
    }),

  // CCC verification of one stored certificate against its register / public API.
  verifyCertificate: (certificateId) =>
    apiFetch(B, '/api/compliance/certificates/' + encodeURIComponent(certificateId) + '/verify', {
      method: 'POST', timeoutMs: 90000
    }),

  // Drafts the renewal email and queues it on the approvals card (nothing is sent here).
  draftRenewalEmail: (certificateId) =>
    apiFetch(B, '/api/compliance/certificates/renewal-email', {
      method: 'POST', body: { certificate_id: certificateId }, timeoutMs: 60000
    }),

  approvals: () => apiFetch(B, '/api/compliance/approvals', { query: withOrg() })
};
