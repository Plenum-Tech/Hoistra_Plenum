// api/compliance — svc-operations-intelligence, Feature A (Compliance Engine).
// Routes: apps/backend/.../svc-operations-intelligence/src/api/routes/compliance.py
// Mounted behind the gateway at /backend/ops-intelligence/ → router prefix /api/compliance.
//
// Only the routes the UI actually calls are wrapped here — the compliance router exposes far
// more (forensics, verification, approvals-by-token, country-pack seeding, and others that
// mutate a live register), and a client this app never calls does not belong in the bundle
// it ships. Unused wrappers were removed on 2026-09-09 rather than left dormant: `complianceApi`
// was a single imported object, so all 60 travelled in the bundle regardless of use, and a
// dozen were POST/PATCH — code one call away from mutating production that nothing here ever
// exercised or tested. The full router surface, including everything below, is recorded in
// docs/compliance-routes-not-wired.md so wiring one up later starts from a real reference,
// not a blank page.
//
// The transport is api/client.js — non-2xx throws ApiError, network failure throws a plain
// Error, and the caller decides how to fall back. Nothing in this file holds state or
// touches the store.
import { BASES, ORG_ID, apiFetch } from './client.js';

const B = BASES.opsIntelligence;
const withOrg = (q) => (ORG_ID ? Object.assign({ organization_id: ORG_ID }, q || {}) : (q || {}));
// Same for a POST/PATCH body: the tenant scope goes in the payload, not the query string.
const withOrgBody = (b) => (ORG_ID ? Object.assign({ organization_id: ORG_ID }, b || {}) : (b || {}));
const enc = encodeURIComponent;

// Writes that run a worker, an LLM extraction or an outbound register call need more than
// the client's 20s default.
const T_WORK = 90000;
const T_SCAN = 180000;
const T_DRAFT = 60000;

export const complianceApi = {
  health: () => apiFetch(B, '/health', { timeoutMs: 6000 }),

  // ── Register: certificates ──────────────────────────────────────────────
  // Every certificate (Building + Vendor scope) with the enrichment the dashboard reads:
  // certificate_type_name, vendor_name, vendor_block_state, risk_badge, forensics_*.
  // Filters the backend accepts: cert_scope, status, vendor_id, asset_id, site_id, site_ref,
  // risk_filter (blocked|high|medium|lapsed|active|expiring_90|expiring_30), draft,
  // certificate_type_code, trade_category, vendor_name, expiring_within_days,
  // expiry_month (YYYY-MM), include_archived, limit (≤1000).
  listCertificates: (query) =>
    apiFetch(B, '/api/compliance/certificates', { query: withOrg(Object.assign({ limit: 1000 }, query || {})) }),

  // ── Register: per-certificate actions ───────────────────────────────────
  // Remedial state on a certificate that came back with defects.
  setRemedialStatus: (certificateId, remedialStatus) =>
    apiFetch(B, '/api/compliance/certificates/' + enc(certificateId) + '/remedial-status', {
      method: 'PATCH', body: { remedial_status: remedialStatus }, timeoutMs: T_WORK
    }),

  // PM confirmation of a draft certificate. `link_targets` opts into linking as it confirms.
  confirmCertificate: (certificateId, body) =>
    apiFetch(B, '/api/compliance/certificates/' + enc(certificateId) + '/confirm', {
      method: 'POST', body: body || {}, timeoutMs: T_WORK
    }),

  // Drafts the renewal email and queues it on the approvals card (nothing is sent here).
  draftRenewalEmail: (certificateId) =>
    apiFetch(B, '/api/compliance/certificates/renewal-email', {
      method: 'POST', body: { certificate_id: certificateId }, timeoutMs: T_DRAFT
    }),

  // ── Coverage and the regulation pack ────────────────────────────────────
  // Per-building / per-vendor CountryPack coverage. `required` is pack completeness,
  // not a compliance score — the response says so in required_basis_note.
  buildingCoverage: (countryCode) =>
    apiFetch(B, '/api/compliance/coverage/buildings', { query: withOrg({ country_code: countryCode }) }),
  vendorCoverage: (countryCode, tradeCategory) =>
    apiFetch(B, '/api/compliance/coverage/vendors', {
      query: withOrg({ country_code: countryCode, trade_category: tradeCategory })
    }),

  // The regulation pack: certificate types by scope, names for the codes on certificates.
  countryPack: (countryCode, query) =>
    apiFetch(B, '/api/compliance/country-pack', { query: Object.assign({ country_code: countryCode }, query || {}) }),

  savedSpaceSummary: () => apiFetch(B, '/api/compliance/saved-space/summary', { query: withOrg() }),

  // ── A1 scan ─────────────────────────────────────────────────────────────
  // Building (A2) + Vendor (A3) workers, alert ladder, adversary check.
  runScan: (body) =>
    apiFetch(B, '/api/compliance/scan', {
      method: 'POST',
      body: Object.assign({ scope: 'all' }, ORG_ID ? { organization_id: ORG_ID } : {}, body || {}),
      timeoutMs: T_SCAN
    }),

  // ── CCC verification ────────────────────────────────────────────────────
  // Verification of one stored certificate against its register / public API.
  verifyCertificate: (certificateId) =>
    apiFetch(B, '/api/compliance/certificates/' + enc(certificateId) + '/verify', {
      method: 'POST', timeoutMs: T_WORK
    }),

  // ── Vendors: recommendation ──────────────────────────────────────────────
  // Contractors that hold a given accreditation and are not blocked — the real answer
  // behind "change contractor".
  recommendContractors: (requiredAccreditation, limit) =>
    apiFetch(B, '/api/compliance/contractors/recommend', {
      method: 'POST',
      body: withOrgBody({ required_accreditation: requiredAccreditation, limit: limit || 5 }),
      timeoutMs: T_WORK
    }),

  // ── Evidence pack ───────────────────────────────────────────────────────
  // Both evidence-pack routes answer `application/pdf` with a content-disposition
  // attachment, NOT JSON — they render the auditable bundle with ReportLab. So they do
  // not go through apiFetch (which parses the body as JSON); the URL is handed out for a
  // download, or the bytes are read as a Blob.
  evidencePackUrl: (query) => {
    const url = new URL(B + '/api/compliance/evidence-pack', window.location.origin);
    Object.entries(withOrg(query)).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v));
    });
    return url.toString();
  },

  // The same PDF as bytes, for callers that want to hold it rather than navigate to it.
  async evidencePackBlob(query) {
    const res = await fetch(complianceApi.evidencePackUrl(query), { headers: { Accept: 'application/pdf' } });
    if (!res.ok) throw new Error('evidence pack failed: ' + res.status + ' ' + res.statusText);
    return res.blob();
  }
};
