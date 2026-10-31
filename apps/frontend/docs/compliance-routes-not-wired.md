# Compliance router surface not wired into the frontend

`svc-operations-intelligence`'s `/api/compliance` router (`apps/backend/.../svc-operations-intelligence/src/api/routes/compliance.py`)
exposes more than `src/api/compliance.js` wraps. These were removed from that file on
2026-09-09 — not because the routes are wrong, but because an unused, imported wrapper is a
live call one line away, and a dozen of these are POST/PATCH against a production register
that nothing in this app has ever exercised or tested. Wiring one up should start from this
list and the router source, not from a wrapper that was already sitting in the bundle.

None of these are called from `src/`. `enc` is `encodeURIComponent`; `withOrgBody(b)`
merges `organization_id` into the body when `ORG_ID` is configured, `withOrg(q)` the same
into a query object; `T_WORK`/`T_SCAN`/`T_DRAFT` are the longer timeouts writes and worker
calls need over the client's 20s default.

## Register: certificates

```js
countCertificates: (query) =>
  apiFetch(B, '/api/compliance/certificates/count', { query: withOrg(query) }),

upsertCertificate: (body) =>
  apiFetch(B, '/api/compliance/certificates', { method: 'POST', body: withOrgBody(body), timeoutMs: T_WORK }),

// Vendors holding more than / at least / exactly `min_count` certificates.
vendorCertificateCounts: (query) =>
  apiFetch(B, '/api/compliance/vendors/certificate-counts', { query: withOrg(query) }),
```

## Register: per-certificate actions

```js
// Documents/sites/vendors this certificate could attach to, then the attach itself.
linkCandidates: (certificateId) =>
  apiFetch(B, '/api/compliance/certificates/' + enc(certificateId) + '/link-candidates', { timeoutMs: T_WORK }),
linkCertificate: (certificateId, body) =>
  apiFetch(B, '/api/compliance/certificates/' + enc(certificateId) + '/link', {
    method: 'POST', body: body || {}, timeoutMs: T_WORK
  }),

// Create the vendor a certificate names but the graph does not hold yet.
createVendorForCertificate: (certificateId, body) =>
  apiFetch(B, '/api/compliance/certificates/' + enc(certificateId) + '/create-vendor', {
    method: 'POST', body: body || {}, timeoutMs: T_WORK
  }),
extractVendorProfile: (certificateId) =>
  apiFetch(B, '/api/compliance/certificates/' + enc(certificateId) + '/extract-vendor-profile', {
    method: 'POST', timeoutMs: T_WORK
  }),

// Soft archive / restore. Both take a list, so a single row passes a one-element array.
archiveCertificates: (certificateIds, reason) =>
  apiFetch(B, '/api/compliance/certificates/archive', {
    method: 'POST',
    body: withOrgBody({ certificate_ids: [].concat(certificateIds), reason: reason || null }),
    timeoutMs: T_WORK
  }),
unarchiveCertificates: (certificateIds) =>
  apiFetch(B, '/api/compliance/certificates/unarchive', {
    method: 'POST', body: withOrgBody({ certificate_ids: [].concat(certificateIds) }), timeoutMs: T_WORK
  }),
```

## Coverage and the regulation pack

```js
// Attaches certificates to sites when the sites table is not UUID-keyed. Dry run by default.
backfillSiteLinks: (dryRun) =>
  apiFetch(B, '/api/compliance/coverage/backfill-site-links', {
    method: 'POST', query: withOrg({ dry_run: dryRun === false ? false : true }), timeoutMs: T_WORK
  }),

countryPackEntity: (countryCode, packVersion) =>
  apiFetch(B, '/api/compliance/country-pack/entity', { query: { country_code: countryCode, pack_version: packVersion } }),
seedCountryPack: (countryCode, force) => {
  const c = String(countryCode || 'all').toUpperCase();
  const path = c === 'UK' ? 'seed-uk' : c === 'UAE' || c === 'AE' ? 'seed-uae' : c === 'US' ? 'seed-us' : 'seed-all';
  return apiFetch(B, '/api/compliance/country-pack/' + path, { method: 'POST', query: { force: !!force }, timeoutMs: T_WORK });
},
loadCountryPack: (pack) =>
  apiFetch(B, '/api/compliance/country-pack/load', { method: 'POST', body: { pack: pack }, timeoutMs: T_WORK }),
updatePackThresholds: (certificateTypeCode, body) =>
  apiFetch(B, '/api/compliance/country-pack/types/' + enc(certificateTypeCode) + '/thresholds', {
    method: 'PATCH', body: withOrgBody(body), timeoutMs: T_WORK
  }),
notifyPackVersion: (body) =>
  apiFetch(B, '/api/compliance/country-pack/notify-version', { method: 'POST', body: withOrgBody(body), timeoutMs: T_WORK }),
// Turns a country pack on for one building.
activateBuildingPack: (body) =>
  apiFetch(B, '/api/compliance/buildings/activate-pack', { method: 'POST', body: withOrgBody(body), timeoutMs: T_WORK }),

// Everything the engine knows it can check — types, scopes, channels — in one read.
catalogue: () => apiFetch(B, '/api/compliance/catalogue'),
```

## A1 scan

```js
// A building changed materially — re-evaluate which obligations apply to it.
buildingChange: (siteId, changeDescription) =>
  apiFetch(B, '/api/compliance/building-change', {
    method: 'POST', body: withOrgBody({ site_id: siteId, change_description: changeDescription }), timeoutMs: T_WORK
  }),
```

## CCC verification

```js
// Ad-hoc verify of details that are not (yet) a stored certificate.
verify: (body) =>
  apiFetch(B, '/api/compliance/verify', { method: 'POST', body: withOrgBody(body), timeoutMs: T_WORK }),
// Verify from a type + accreditation number, without a certificate record at all.
verifyNow: (body) =>
  apiFetch(B, '/api/compliance/verify-now', { method: 'POST', body: body || {}, timeoutMs: T_WORK }),
// Re-run verification over certificates already checked once.
reverify: (body) =>
  apiFetch(B, '/api/compliance/reverify', { method: 'POST', body: withOrgBody(body), timeoutMs: T_SCAN }),
// Verify every certificate whose register is machine-readable.
autoVerify: (body) =>
  apiFetch(B, '/api/compliance/auto-verify', { method: 'POST', body: withOrgBody(body), timeoutMs: T_SCAN }),

// Which channel each certificate type verifies through: public API, weekly dump,
// register bot, or a Verify-now link.
verificationSources: (query) => apiFetch(B, '/api/compliance/verification-sources', { query: query }),
seedVerificationSources: () =>
  apiFetch(B, '/api/compliance/verification-sources/seed', { method: 'POST', timeoutMs: T_WORK }),
verificationRegisters: () => apiFetch(B, '/api/compliance/verification-registers'),

// Registers that publish a bulk dump rather than a per-record API.
ingestVerificationDump: (body) =>
  apiFetch(B, '/api/compliance/verification-dumps/ingest', { method: 'POST', body: body || {}, timeoutMs: T_SCAN }),
runVerificationDumpCron: () =>
  apiFetch(B, '/api/compliance/verification-dumps/run-cron', { method: 'POST', timeoutMs: T_SCAN }),

// Free-text search against a named public register (ukas | bpca | basis | hse | sia).
registerSearch: (query) => apiFetch(B, '/api/compliance/register-search', { query: query, timeoutMs: T_WORK }),
registerSearchPost: (body) =>
  apiFetch(B, '/api/compliance/register-search', { method: 'POST', body: body || {}, timeoutMs: T_WORK }),

// Is this vendor actually on the register it claims?
vendorRegistrationCheck: (body) =>
  apiFetch(B, '/api/compliance/vendor-registration-check', { method: 'POST', body: withOrgBody(body), timeoutMs: T_WORK }),
```

## Document forensics and extraction

```js
// Authenticity: PDF metadata, signature, text-layer and page-count signals → verdict.
forensics: (body) =>
  apiFetch(B, '/api/compliance/forensics', { method: 'POST', body: body || {}, timeoutMs: T_WORK }),
// Adversary check — the engine arguing against its own conclusion.
adversary: (checkType, payload) =>
  apiFetch(B, '/api/compliance/adversary', {
    method: 'POST', body: withOrgBody({ check_type: checkType, payload: payload || {} }), timeoutMs: T_WORK
  }),
// Field extraction from certificate text for one type.
extract: (body) =>
  apiFetch(B, '/api/compliance/extract', { method: 'POST', body: body || {}, timeoutMs: T_WORK }),
ingestBatch: (body) =>
  apiFetch(B, '/api/compliance/ingest-batch', { method: 'POST', body: withOrgBody(body), timeoutMs: T_SCAN }),
// Which pack type does this document look like?
tableMatch: (body) =>
  apiFetch(B, '/api/compliance/table-match', { method: 'POST', body: body || {}, timeoutMs: T_WORK }),
// Keep or remove a document from a certificate's evidence set.
documentMembership: (documentId, body) =>
  apiFetch(B, '/api/compliance/documents/' + enc(documentId) + '/membership', {
    method: 'POST', body: withOrgBody(body), timeoutMs: T_WORK
  }),
```

## Approvals

```js
// The queue the alert ladder writes into. Nothing leaves the platform until decided.
approvals: (query) => apiFetch(B, '/api/compliance/approvals', { query: withOrg(query) }),
decideApproval: (itemId, body) =>
  apiFetch(B, '/api/compliance/approvals/' + enc(itemId) + '/decide', {
    method: 'POST', body: body || {}, timeoutMs: T_DRAFT
  }),
// One-click approval links carried in the ladder emails.
approvalByToken: (token) => apiFetch(B, '/api/compliance/approvals/one-click/' + enc(token)),
peekApprovalToken: (token) => apiFetch(B, '/api/compliance/approvals/one-click/' + enc(token) + '/peek'),
redeemApprovalToken: (token) =>
  apiFetch(B, '/api/compliance/approvals/one-click/' + enc(token) + '/redeem', { method: 'POST', timeoutMs: T_DRAFT }),
```

## Vendors: skills and passport

```js
resourceSkills: (vendorId, limit) =>
  apiFetch(B, '/api/compliance/resource-skills', { query: { vendor_id: vendorId, limit: limit } }),
upsertResourceSkill: (body) =>
  apiFetch(B, '/api/compliance/resource-skills', { method: 'POST', body: withOrgBody(body), timeoutMs: T_WORK }),

// Pre-engagement vendor passport: accreditations + insurance + posture, and a
// time-limited link to share it outside the platform.
vendorPassport: (vendorId) =>
  apiFetch(B, '/api/compliance/vendors/' + enc(vendorId) + '/passport', { query: withOrg(), timeoutMs: T_WORK }),
shareVendorPassport: (vendorId, body) =>
  apiFetch(B, '/api/compliance/vendors/' + enc(vendorId) + '/passport/share', {
    method: 'POST', body: withOrgBody(body), timeoutMs: T_WORK
  }),
passportByToken: (token) => apiFetch(B, '/api/compliance/passport/share/' + enc(token)),
```
