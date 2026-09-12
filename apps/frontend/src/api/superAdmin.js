// api/superAdmin — svc-operations-intelligence's /api/superadmin: onboard companies and
// observe them, deliberately nothing else. Routes: apps/backend/.../svc-operations-
// intelligence/src/api/routes/superadmin.py. Mounted behind the gateway at
// /backend/ops-intelligence/. Superadmin role required — anyone below gets 403
// {reason: "forbidden", required_role: "superadmin"}; every failure is {detail: {ok, error, reason}}.
//
// No withOrg: these routes are the platform-operator surface, above any one company.
// The company being acted on is named in the path, never in a query param.
import { BASES, apiFetch } from './client.js';

const B = BASES.opsIntelligence;

export const superAdminApi = {
  // Every company with lifecycle (created|onboarding|active), country_code (UK|US|AE|SG)
  // and credits_this_month — ordered by credits, plus buildings_without_company.
  // admin_email is NOT here; it rides on the detail card's company object.
  listCompanies: () => apiFetch(B, '/api/superadmin/companies'),

  // 201 → {organization_id, lifecycle, admin_invitation}. body: {name, country_code?,
  // admin_email?, admin_name?, industry?, timezone?}. country_code takes UK | US | AE | SG
  // (names like "UAE"/"Singapore" resolve server-side; an unmapped name is 400 bad_country);
  // with admin_email the administrator is invited in the same call. Duplicate name → 409.
  createCompany: (body) => apiFetch(B, '/api/superadmin/companies', { method: 'POST', body }),

  // One company's usage card: buildings_created, hoist_graphs, last_activity,
  // udr_data_bytes (raw bytes — format client-side), compliance_certificates,
  // certificate_countries[], api_requests_30d, credits_this_month/_total (floats),
  // users{total,active,invited,can_ingest}, pending_invitations, tariff, counted_from.
  company: (organizationId) =>
    apiFetch(B, '/api/superadmin/companies/' + encodeURIComponent(organizationId)),

  // Send (or idempotently re-send) the administrator invitation. body: {email, full_name?}.
  // → the invite receipt: user_id, expires_at, email_sent, accept_url when undelivered.
  // Bumps lifecycle created→onboarding. An already-active address is 409 already_active.
  inviteAdmin: (organizationId, body) =>
    apiFetch(B, '/api/superadmin/companies/' + encodeURIComponent(organizationId) + '/invite-admin',
      { method: 'POST', body }),

  // The bars: {month_total, tariff, companies: [{organization_id, name, credits_this_month}]}.
  credits: () => apiFetch(B, '/api/superadmin/credits')
};
