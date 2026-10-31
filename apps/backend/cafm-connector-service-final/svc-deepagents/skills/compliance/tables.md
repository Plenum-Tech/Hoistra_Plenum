---
name: compliance-tables
agent: compliance
title: Validated compliance data contracts
description: Documents source categories and identity/provenance boundaries; table names are backend implementation references, not schema or permission grants.
---

# Compliance data contracts

Target policy: specification v0.37.0.

Follow the core/shared policies and reviewed Compliance instructions.
This file is not a tool registry or live-schema dump. A source table in
legacy code is not evidence that it is connected to Homework 1.
Backend adapters own schema/key validation and data-access predicates.

## 1. Source categories and adapter responsibilities

| Source category | Legacy implementation references | Required adapter contract |
|---|---|---|
| Certificates and accreditations | compliance_certificates | Verified subject/holder, authorized building/vendor relationship, accepted version, date anchor, status and source |
| Country packs and assignments | country_certificate_packs; building_country_packs | Pack/version, subject, returned counts and applicability basis |
| Issuer/register checks | compliance_verification_sources | Check source, scope, assessment time and actual outcome; distinguish unassessed/unavailable |
| Vendor eligibility | vendors and retained blocking evidence | Approved state, relevant reasons, organization and decision provenance; no state inferred from dates alone |
| Proposals and decisions | approvals_queue_items | Action-specific eligibility, exact approved proposal, before/after values, actor and atomic resolution |
| Detection history | compliance_scan_runs; compliance_risk_snapshots | Scope/time/version and actual findings/events; scan existence does not prove authorized state changes |
| Supporting documents | Authorized document/version sources | Full-content scope or verified excerpt, immutable originals and source/page references |
| Operative credentials | resource_skills where supported | Only permitted certificate evidence; unrelated personnel/account details excluded |

These are implementation references to validate, not guaranteed tables,
column names, UUID types or offered tool arguments. Do not expose schema,
raw rows or arbitrary SQL because this table names a source.

## 2. Building and vendor identity

Legacy data may use site_id, site_ref, building_reference or building_name.
A backend-validated relationship must establish the authorized entity.
Do not use a normalized name as an access grant or invent a type conversion.

Preserve source identifiers and mapping provenance. Ambiguous, absent or
unverified links require clarification or withholding; do not broaden an
unresolved record into a portfolio/organization bucket to disclose it.

Managers require explicit assigned-building vendor links for vendor actions.
An Officer's organization-wide access remains compliance-function-only.
Shared documents/vendors do not automatically disclose every linked building.

## 3. Field and query semantics

The adapter declares available fields, filter meanings and supported
operations. Reject unknown arguments instead of accepting arbitrary
organization, SQL, table or runtime-context overrides.

Keep expiry/date anchor, risk assessment, approved block state, draft/
acceptance, forensic findings and source version as distinct concepts.
Unsupported values must not become invented defaults or status enums.

Apply role, organization, building/entity and information scope before
retrieving records, candidate names, counts or document contents.
Use identical filters/population for count/page queries and disclose
completeness. Unknown schema is unavailable, not empty successful data.

## 4. Provenance and versions

Retain original evidence, prior versions and approved current representation.
A correction changes authorized derived records, not the original source.
Projection, metadata, links and download endpoints require the same checks.
Do not expose standalone vector/source deletion or silently remove history.

## 5. Coverage interpretation

Return the denominator basis and pack/version for coverage. A country-pack
completeness denominator is not automatically the building's applicable
legal obligations. Missing applicability is unknown, not permission to
claim compliance or exemption.

Do not substitute coverage for the ten-group HOI Score or give HOI to
General Users/Compliance Officers. Fixed test fixture counts are separate
from both schema discovery and statutory applicability.
