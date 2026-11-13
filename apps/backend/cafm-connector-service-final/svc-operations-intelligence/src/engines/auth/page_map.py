"""Which page owns each route's data. One row per route; this file is the whole answer.

A user with an access role may open only the pages an administrator ticked
(engines/auth/pages.py), and data owned by any other page is refused to them. URL prefixes
cannot decide that: /api/energy serves the buildings register, the building graph, asset
condition, the Home score and the energy register. So ownership is written down here.

The rule a row follows:

    A route is tagged with the page or pages whose OWN register it serves. A caller needs at
    least one of them. Being shown on another page does not add a tag.

A route that genuinely serves two registers lists both (which vendor serves which building is
the vendors' fact and compliance's). A response that mixes registers is tagged ``shared`` and
narrowed row by row inside the handler (engines/auth/page_filter.py), so the caller keeps the
rows they hold instead of losing the whole list.

Tags that are not a page:

    shared   every signed-in caller; any narrowing happens inside the handler
    ingest   adding data — governed by the can-ingest switch, not by pages
    admin    already administrator-only
    public   answers without the caller's pages mattering (sign-in, token links, health)

``NEEDS_ALL`` is for an action that writes into a second register: nobody creates a record in
a register they cannot see, so those need every page listed, not any one of them.

tests/unit/test_page_gate.py walks the application and fails, naming the route, for any route
without a row — and for any row whose route no longer exists.
"""
from __future__ import annotations

from .pages import ADMIN, INGEST, PUBLIC, SHARED

PAGE_MAP: dict[tuple[str, str], tuple[str, ...]] = {}

#: Routers that are administrator-only as a whole (tests/unit/test_admin_route_gating.py pins
#: that). A restricted caller is a plain user and never gets past their own gate.
_ADMIN_PREFIXES = ("/api/admin/", "/api/superadmin/")


def _own(tags: tuple[str, ...], prefix: str, *routes: str) -> None:
    for route in routes:
        method, path = route.split(" ", 1)
        key = (method, prefix + (path if path != "/" else ""))
        assert key not in PAGE_MAP, f"page_map: {key} listed twice"
        PAGE_MAP[key] = tags


B, C, E, V, O, A = "buildings", "compliance", "energy", "vendors", "ops", "assets"

# ── /api/energy — five owners under one prefix ────────────────────────────────────────
_E = "/api/energy"
_own((E,), _E,
     "POST /meters", "GET /meters", "POST /meters/pull", "GET /meters/{meter_id}/readings",
     "GET /buildings/{building_id}/meter-summary", "GET /gaps", "POST /gaps/process-retries",
     "GET /meters/link-report", "POST /gaps/detect", "GET /tm46", "POST /eui/compute",
     "POST /recommendations/cross-ref", "POST /occupancy/log", "POST /simulator/tick",
     "POST /simulator/enable", "POST /anomalies/scan", "POST /anomalies/scan-all",
     "GET /meters/by-floor", "GET /detection/coverage", "GET /anomalies",
     "GET /buildings/{building_id}/operating-hours", "GET /consumption/by-asset",
     "GET /anomalies/summary", "GET /anomalies/rollup", "POST /anomalies/{anomaly_id}/act",
     "POST /reports/monthly", "GET /reports/{report_id}/pdf", "GET /saved-space/summary",
     "GET /approvals", "POST /approvals/{item_id}/decide", "POST /ratings/compute",
     "GET /ratings", "GET /ratings/position", "GET /benchmarks/validation",
     "POST /benchmarks/validate", "GET /market-profiles", "POST /chillers/{asset_id}/design",
     "POST /chillers/{asset_id}/readings", "GET /chillers/{asset_id}/efficiency",
     "POST /chillers/scan", "GET /tariffs", "POST /price", "GET /statutory/{market}")
# The buildings register, its graph and its documents tree live here too.
_own((B,), _E,
     "POST /buildings", "PATCH /buildings/{building_id}", "DELETE /buildings/{building_id}",
     "POST /buildings/backfill-from-sites", "GET /buildings/next-code",
     "POST /buildings/link-locations", "POST /sites/uuid-migration", "GET /graph/shape",
     "GET /graph/tables", "GET /buildings/{building_id}/graph", "GET /buildings/{site_id}",
     "DELETE /documents/{document_id}")
# A building's energy profile is the building's fact and the energy register's input.
_own((B, E), _E, "POST /buildings/profile")
# Floors: the building's and the assets'. Which plant drives a building's spend is the
# asset register's alone - it is per-asset billed against contract.
_own((B, A), _E, "GET /sections")
_own((A,), _E, "GET /buildings/{building_id}/cost-drivers")
# The question bar answers the Assets page's questions and the Energy page's; the handler
# refuses a question whose page the caller does not hold.
_own((E, A), _E, "POST /ask", "GET /ask/suggestions")
# Asset condition, intelligence and investigation; an asset's own vendor and work history.
_own((A,), _E,
     "GET /assets/{asset_id}/work-history", "POST /condition/deduce",
     "POST /condition/deduce-from-vectors", "GET /assets/value-at-risk",
     "GET /assets/{asset_id}/intelligence", "GET /condition/summary", "GET /condition/assets",
     "GET /condition/rules", "PUT /condition/rules", "POST /condition/scan",
     "GET /condition/last-run",
     "GET /assets/{asset_id}/notes", "GET /assets/{asset_id}/investigate",
     "GET /assets/{asset_id}/vendor", "PATCH /assets/{asset_id}/vendor",
     "GET /assets/{asset_id}/bms-trend", "GET /assets/{asset_id}/utility-bill")
# Weather is reference data both the energy register and an asset investigation read.
_own((E, A), _E, "GET /weather/degree-days")
_own((INGEST,), _E,
     "POST /readings/ingest", "POST /readings/ingest/csv", "POST /weather/degree-days",
     "POST /bms/trends")
# Building names are labels every page uses: the list answers everyone, with the register
# detail only for a caller who holds Buildings (page_filter.building_rows). Resolving a typed
# name to a building returns a label. The Hoist Score is a composite (page_filter.hoist_score).
_own((SHARED,), _E,
     "GET /buildings", "GET /buildings/resolve", "POST /buildings/resolve-batch",
     "GET /hoist-score")

# ── /api/contract-performance ─────────────────────────────────────────────────────────
_V = "/api/contract-performance"
_own((V,), _V,
     "GET /workbook-extras/latest", "GET /contracts", "GET /contracts/{parameters_id}",
     "PATCH /contracts/{parameters_id}", "POST /contracts/{parameters_id}/confirm",
     "POST /contracts/{parameters_id}/reopen", "GET /admin/weights", "PUT /admin/weights",
     "POST /score/work-orders", "POST /score/from-udr", "GET /udr/work-order-buckets",
     "POST /scorecards/monthly", "POST /scorecards/monthly-all", "GET /scorecards",
     "GET /wo-scores", "GET /saved-space/summary", "POST /fm-staleness", "GET /invoices",
     "POST /invoices/verify", "POST /invoices/{verification_id}/lines/decide",
     "GET /approvals", "POST /approvals/{item_id}/decide",
     "POST /work-orders/{wo_code}/resolve-conflict", "GET /insights")
# How critical an asset is: the asset's fact, kept by the engine that scores vendors on it.
_own((V, A), _V,
     "POST /asset-criticality", "POST /asset-criticality/{criticality_id}/approve",
     "POST /asset-criticality/propose-from-udr", "GET /asset-criticality")
_own((INGEST,), _V,
     "POST /contracts/extract", "POST /contracts/ingest", "POST /migration/reconcile",
     "POST /migration/{migration_id}/workbook-extras",
     "GET /migration/{migration_id}/workbook-extras", "POST /invoices/extract-verify")

# ── /api/compliance ───────────────────────────────────────────────────────────────────
_C = "/api/compliance"
_own((C,), _C,
     "POST /certificates", "GET /certificates", "GET /certificates/count",
     "GET /vendors/certificate-counts",
     "PATCH /certificates/{certificate_id}/remedial-status",
     "POST /certificates/{certificate_id}/confirm",
     "GET /certificates/{certificate_id}/link-candidates",
     "POST /certificates/{certificate_id}/link",
     "POST /certificates/{certificate_id}/create-vendor",
     "POST /certificates/{certificate_id}/extract-vendor-profile",
     "POST /certificates/archive", "POST /certificates/unarchive",
     "POST /certificates/renewal-email", "POST /building-change", "POST /scan",
     "GET /saved-space/summary", "GET /country-pack", "GET /country-pack/entity",
     "POST /country-pack/seed-uk", "POST /country-pack/seed-uae", "POST /country-pack/seed-us",
     "POST /country-pack/seed-all", "POST /country-pack/load",
     "PATCH /country-pack/types/{certificate_type_code}/thresholds",
     "GET /verification-sources", "POST /verification-sources/seed", "GET /catalogue",
     "POST /verify", "GET /certificates/{certificate_id}/download",
     "POST /certificates/{certificate_id}/human-verification",
     "POST /certificates/{certificate_id}/verify", "POST /verification-dumps/ingest",
     "POST /verification-dumps/run-cron", "GET /register-search", "POST /register-search",
     "POST /reverify", "POST /auto-verify", "GET /verification-registers", "POST /verify-now",
     "GET /approvals", "POST /approvals/{item_id}/decide", "POST /buildings/activate-pack",
     "POST /country-pack/notify-version", "POST /resource-skills", "GET /resource-skills",
     "POST /contractors/recommend", "GET /coverage/buildings",
     "POST /coverage/backfill-site-links", "POST /coverage/backfill-building-links",
     "GET /evidence-pack", "POST /evidence-pack",
     "GET /vendors/{vendor_id}/passport", "POST /vendors/{vendor_id}/passport/share",
     "GET /mees", "POST /filings", "GET /filings", "GET /filings/position")
# A vendor's Blocked state stays wherever the vendor shows (2 Nov 2026), so the Vendors page
# reads this too. Without Compliance it carries the state and no certificate detail
# (page_filter.vendor_block_states).
_own((V, C), _C, "GET /coverage/vendors")
# Which vendor serves which building: the vendors' fact and compliance's.
_own((V, C), _C,
     "GET /vendor-buildings", "POST /vendors/{vendor_id}/buildings",
     "DELETE /vendors/{vendor_id}/buildings/{building_id}")
# The upload pipeline: what a file is, whether it is genuine, where its rows go.
_own((INGEST,), _C,
     "POST /documents/validate", "POST /documents/ingest",
     "POST /documents/{document_id}/membership", "POST /forensics", "POST /adversary",
     "POST /extract", "POST /ingest-batch", "POST /table-match",
     "POST /vendor-registration-check")
# Answered by the token in the link, not by who is signed in.
_own((PUBLIC,), _C,
     "GET /passport/share/{token}", "GET /approvals/one-click/{token}",
     "GET /approvals/one-click/{token}/peek", "POST /approvals/one-click/{token}/redeem")

# ── mixed lists: open to every caller, narrowed inside the handler ────────────────────
_own((SHARED,), "/api/approvals",
     "GET /", "GET /sent-emails", "POST /send-email", "POST /{item_id}/decide")
# A vendor's address, for a draft to them: for the pages that write to vendors.
_own((V, C, O), "/api/approvals", "GET /vendor-contact")
_own((SHARED,), "/api/value", "GET /summary", "GET /savings")
_own((SHARED,), "/api/crons",
     "GET /catalogue", "GET /", "GET /daily", "GET /{job_id}/runs", "GET /{job_id}/events",
     "POST /", "PATCH /{job_id}", "DELETE /{job_id}", "POST /{job_id}/run")
_own((SHARED,), "/api/reports",
     "GET /", "POST /", "PATCH /{report_id}", "DELETE /{report_id}", "GET /refresh-options",
     "POST /cards", "GET /cards/{card_id}", "PATCH /cards/{card_id}",
     "DELETE /cards/{card_id}", "POST /cards/{card_id}/run", "GET /cards/{card_id}/runs")
_own((SHARED,), "/api/documents",
     "GET /search", "GET /types", "GET /", "PATCH /{document_id}",
     "POST /{document_id}/links", "DELETE /{document_id}/links")

# ── the smaller routers ───────────────────────────────────────────────────────────────
# The spare-parts reorder is a maintenance job (engines/crons/jobs.CATALOGUE files it under
# Maintenance), so it follows the maintenance page with the other four.
_own((O,), "/api/maintenance",
     "POST /sla-watch", "POST /ppm-due", "POST /ppm-missed", "POST /monthly-summary",
     "POST /parts-reorder")
_own((A,), "/api/assets", "GET /replacement-candidates")
_own((O,), "/api/work-orders", "GET /blockers")
_own((INGEST,), "/api/ingestion",
     "POST /validate", "GET /cases", "GET /cases/{case_id}", "POST /cases/{case_id}/clarify",
     "POST /cases/{case_id}/reassign", "POST /cases/{case_id}/decide", "GET /extraction-rules",
     "POST /extraction-plan", "POST /import-plan", "POST /import",
     "GET /extraction-rules/validate", "GET /audit")
_own((B,), "/api/ingestion", "GET /buildings/{building_id}/ontology")

# ── identity ──────────────────────────────────────────────────────────────────────────
_own((PUBLIC,), "/api/auth",
     "POST /register", "POST /verify-email", "POST /resend-code", "POST /login",
     "POST /refresh", "POST /password/forgot", "POST /password/reset", "GET /config",
     "POST /invitations/accept")
_own((SHARED,), "/api/auth",
     "POST /logout", "GET /me", "PATCH /me/selected-building", "POST /password/change",
     "GET /roles")
_own((ADMIN,), "/api/auth", "GET /users", "POST /users/{user_id}/role")
_own((PUBLIC,), "", "GET /health", "GET /metrics")

#: Actions that create or change a record in a second register. Every page listed is needed.
NEEDS_ALL: dict[tuple[str, str], tuple[str, ...]] = {
    # Creating a vendor from a certificate writes the vendor master.
    ("POST", "/api/compliance/certificates/{certificate_id}/create-vendor"): (C, V),
    # An asset's utility bill is its building's supply-meter consumption: the energy
    # register's figures under the asset's name, so both pages are needed.
    ("GET", "/api/energy/assets/{asset_id}/utility-bill"): (A, E),
}


def tags_for(method: str, path: str | None) -> tuple[str, ...] | None:
    """The tags of one route, by its method and path TEMPLATE, or None if it has no row."""
    if not path:
        return None
    method = "GET" if method.upper() == "HEAD" else method.upper()
    tags = PAGE_MAP.get((method, path))
    if tags is None and path.startswith(_ADMIN_PREFIXES):
        return (ADMIN,)
    return tags
