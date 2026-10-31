"""Planning a vendor delete: which rows change, which are kept, and that nobody else's move.

Deleting a vendor (Hussain, 12 Oct 2026) removes one with no history and retires one with
history. Either way its current assignments are cleared, its pending approvals closed, its
shared passport links expired and its own rows removed, all inside the admin's company. Rows
that are history - closed work, invoices, certificates, scores - are never written, and any one
of them anywhere keeps the vendor's row (retire).

The plan is built from the schema read live, because the two databases disagree: production's
vendors.id is varchar with legacy ids, its referencing columns declare no foreign keys, and it
carries vendor columns no migration in this repo creates. So it is tested against both the
shipped schema (db/01_schema.sql, parsed) and a production-like variant built here.

Pure planning. No database.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.engines.admin import vendor_delete as vd

SCHEMA = Path(__file__).resolve().parents[6] / "db" / "01_schema.sql"
_SAFE = re.compile(r"^[a-z_][a-z0-9_]*$")
_TYPE = re.compile(r"(character varying|character|timestamp with(?:out)? time zone|double precision"
                   r"|[\w.]+)(\[\])?(?:\((\d+)(?:,\s*\d+)?\))?")
UUID_T, TEXT = vd.Col("uuid"), vd.Col("text")


def shipped() -> vd.Schema:
    """db/01_schema.sql as information_schema would describe it, plus what every deployed
    database adds on top: assets.vendor_id (TEXT, asset_intelligence_tables.sql), the
    buildings' company (access_control.sql) and the document register (document_links.sql)."""
    src = SCHEMA.read_text()
    tables: dict[str, dict[str, vd.Col]] = {}
    for m in re.finditer(r"^CREATE TABLE plenum_cafm\.(\w+) \(\n(.*?)^\);", src, re.M | re.S):
        cols: dict[str, vd.Col] = {}
        for line in m.group(2).splitlines():
            cm = re.match(r'\s+"?(\w+)"?\s+(.*)$', line)
            if not cm or cm.group(1) == "CONSTRAINT":
                continue
            tm = _TYPE.match(cm.group(2))
            dt = "USER-DEFINED" if "." in tm.group(1) else tm.group(1)
            dt = "ARRAY" if tm.group(2) else dt
            size = int(tm.group(3)) if tm.group(3) and dt.startswith("character") else None
            cols[cm.group(1)] = vd.Col(dt, "NOT NULL" not in cm.group(2), size)
        tables[m.group(1)] = cols
    fks = tuple(vd.Fk(t, c, p) for t, c, p in re.findall(
        r"ALTER TABLE ONLY plenum_cafm\.(\w+)\n\s+ADD CONSTRAINT \w+ FOREIGN KEY \((\w+)\) "
        r"REFERENCES plenum_cafm\.vendors\((\w+)\)", src))
    tables["assets"]["vendor_id"] = TEXT
    tables["buildings"]["organization_id"] = UUID_T
    tables["document_links"] = {
        "id": vd.Col("uuid", False), "document_id": vd.Col("uuid", False),
        "entity_type": vd.Col("text", False), "entity_id": vd.Col("text", False),
        "relation": TEXT, "organization_id": UUID_T, "created_by": UUID_T,
        "created_at": vd.Col("timestamp with time zone", False), "removed_by": UUID_T,
        "removed_at": vd.Col("timestamp with time zone")}
    return vd.Schema(tables, fks)


def production_like() -> vd.Schema:
    """Production's shape where it differs: varchar vendor ids, no foreign keys at all, and the
    vendor columns the migration engine and table editor added (map of 12 Oct 2026)."""
    base = shipped()
    cols = {t: dict(c) for t, c in base.columns.items()}
    cols["vendors"]["id"] = vd.Col("character varying", False, 50)
    cols["vendors"]["vendor_name"] = vd.Col("character varying", False, 200)
    cols["assets"]["vendor_id"] = TEXT
    cols["rfqs"] = {"id": UUID_T, "organization_id": UUID_T, "supplier_id": UUID_T}
    cols["work_order_businesses"] = {"id": UUID_T, "work_order_id": UUID_T,
                                     "vendor_id": vd.Col("character varying", True, 50)}
    cols["maintenance_plans"]["vendor_name"] = TEXT
    cols["work_orders"]["vendor"] = vd.Col("character varying", True, 255)
    cols["work_orders"]["vendor_name"] = TEXT
    cols["spare_parts"]["supplier"] = TEXT
    cols["ppm_visits"]["vendor_name"] = TEXT
    cols["ppm_visits"]["deferred_to"] = vd.Col("date")
    return vd.Schema(cols, ())


SHAPES = {"shipped": shipped, "production": production_like}


@pytest.fixture(scope="module", params=list(SHAPES))
def schema(request) -> vd.Schema:
    assert SCHEMA.exists(), f"schema not found at {SCHEMA}"
    return SHAPES[request.param]()


def step(plan: vd.Plan, group: str, key: str) -> vd.Step:
    found = [s for s in plan.writes + plan.references if s.group == group and s.key == key]
    assert found, f"no {group}:{key} in the plan"
    return found[0]


def keys(plan: vd.Plan, group: str) -> set[str]:
    return {s.key for s in plan.writes + plan.references if s.group == group}


def every_sql(plan: vd.Plan) -> list[str]:
    out = [plan.remove_sql] + ([plan.retire_sql] if plan.retire_sql else [])
    for s in plan.writes + plan.references:
        out += [q for q in (s.count_sql, s.apply_sql, s.items_sql, s.before_sql) if q]
    return out


def without(schema: vd.Schema, table: str, *columns: str) -> vd.Schema:
    cols = {t: dict(c) for t, c in schema.columns.items()}
    if columns:
        for c in columns:
            cols[table].pop(c, None)
    else:
        cols.pop(table, None)
    return vd.Schema(cols, schema.fks)


_VID, _ORG = re.compile(r":vid\b"), re.compile(r":org\b")


# ── nothing outside the vendor and the company is ever written ──────────────────────────

def test_every_write_names_the_vendor(schema):
    # A write that lost its vendor filter would clear every vendor's assets, close every
    # approval, expire every token. Each one binds the id, compared as text.
    plan = vd.plan_delete(schema)
    assert plan.writes
    for s in plan.writes:
        for sql in (s.apply_sql, s.before_sql):
            if sql:
                assert _VID.search(sql), f"{s.group}:{s.key} is not bound to the vendor:\n{sql}"
    assert _VID.search(plan.remove_sql) and _VID.search(plan.retire_sql)


def test_every_write_to_a_table_that_carries_a_company_names_the_company(schema):
    # Only the admin's company is ever written; another company's rows count as history.
    plan = vd.plan_delete(schema)
    for s in plan.writes:
        cols = schema.columns[s.table]
        if {"organization_id", "org_id"} & set(cols):
            assert _ORG.search(s.apply_sql), f"{s.group}:{s.key} writes every company's rows"
            if s.before_sql:
                assert _ORG.search(s.before_sql)
    assert _ORG.search(plan.remove_sql) and _ORG.search(plan.retire_sql)


def test_history_is_counted_in_every_company_and_never_written(schema):
    plan = vd.plan_delete(schema)
    assert plan.references
    for s in plan.references:
        assert s.group in ("kept", "other")
        assert s.apply_sql is None and s.before_sql is None
        assert s.count_sql.startswith("SELECT count(*) FROM plenum_cafm.")
        assert _VID.search(s.count_sql)
        if s.group == "kept":
            # Any reference anywhere keeps the row: another company's invoice naming this
            # vendor would otherwise point at a vendor that no longer exists.
            assert not _ORG.search(s.count_sql), f"kept {s.key} only counts this company's"


def test_every_identifier_in_the_sql_is_a_plain_name():
    # Identifiers come from the schema read live; a name that is not plain is never formatted
    # into SQL, whatever role it would have played.
    base = shipped()
    cols = {t: dict(c) for t, c in base.columns.items()}
    cols["Rfq-Lines"] = {"id": UUID_T, "vendor_id": UUID_T}
    cols["quotes"] = {"id": UUID_T, "Vendor Ref": UUID_T}
    odd = vd.Schema(cols, base.fks + (vd.Fk("quotes", "Vendor Ref", "id"),
                                      vd.Fk("Rfq-Lines", "vendor_id", "id")))
    plan = vd.plan_delete(odd)
    assert plan.writes and plan.references
    assert "quotes" not in {s.table for s in plan.references}
    for sql in every_sql(plan):
        assert "Rfq-Lines" not in sql and "Vendor Ref" not in sql
        for name in re.findall(r"plenum_cafm\.(\S+?)[\s(]", sql + " "):
            assert _SAFE.match(name), name


# ── the categories, each only where the database has it ─────────────────────────────────

@pytest.mark.parametrize("group,key,table", [
    ("unassigned", "assets", "assets"),
    ("unassigned", "work_orders", "work_orders"),
    ("unassigned", "ppm_visits", "ppm_visits"),
    ("unassigned", "spare_parts", "spare_parts"),
    ("closed", "approvals_queue_items", "approvals_queue_items"),
    ("revoked", "approval_action_tokens", "approval_action_tokens"),
    ("ended", "document_links", "document_links"),
    ("removed", "vendor_contacts", "vendor_contacts"),
    ("removed", "files", "files"),
    ("removed", "fm_report_staleness", "fm_report_staleness"),
    ("removed", "resource_skills", "resource_skills"),
    ("archived", "compliance_certificates", "compliance_certificates"),
    ("kept", "invoice_lines", "invoice_lines"),
    ("kept", "vendor_contracts", "vendor_contracts"),
    ("kept", "purchase_orders", "purchase_orders"),
])
def test_a_category_is_planned_only_where_its_table_exists(schema, group, key, table):
    assert key in keys(vd.plan_delete(schema), group)
    plan = vd.plan_delete(without(schema, table))
    assert key not in keys(plan, group)
    assert table not in " ".join(s.apply_sql or "" for s in plan.writes if s.group == group)
    assert key in {u["key"] for u in plan.unavailable}, f"{table} missing but not reported"


def test_the_unavailable_list_says_what_in_plain_words(schema):
    plan = vd.plan_delete(without(schema, "document_links"))
    entry = next(u for u in plan.unavailable if u["key"] == "document_links")
    assert entry["label"] == "Linked documents"


def test_a_column_the_database_lacks_is_never_named(schema):
    plan = vd.plan_delete(without(schema, "work_orders", "assigned_vendor", "closed_at"))
    wo = step(plan, "unassigned", "work_orders")
    assert "assigned_vendor" not in wo.apply_sql and "closed_at" not in wo.apply_sql
    assert "t.vendor_id::text = :vid" in wo.apply_sql


def test_maintenance_plans_are_unassigned_only_where_they_name_a_vendor():
    # The shipped schema's plans carry no vendor at all; production's carry its name.
    assert "maintenance_plans" not in keys(vd.plan_delete(shipped()), "unassigned")
    mp = step(vd.plan_delete(production_like()), "unassigned", "maintenance_plans")
    assert "vendor_name = CASE WHEN" in mp.apply_sql


# ── current assignments: open work only ─────────────────────────────────────────────────

def test_open_work_orders_lose_the_vendor_and_closed_ones_keep_it(schema):
    plan = vd.plan_delete(schema)
    wo = step(plan, "unassigned", "work_orders")
    assert "t.completed_at IS NULL" in wo.apply_sql and "t.closed_at IS NULL" in wo.apply_sql
    assert "assigned_vendor = CASE WHEN t.assigned_vendor::text = :vid THEN NULL" in wo.apply_sql
    closed = step(plan, "kept", "work_orders")
    # The same "open" test, negated: a work order is one or the other, never both or neither.
    assert "IS NOT TRUE" in closed.count_sql
    assert "t.completed_at IS NULL" in closed.count_sql


def test_a_work_order_is_open_only_in_a_status_known_to_be_open(schema):
    # 12 Oct 2026 (review R1): "open" was "not in a finished state", so an imported sheet's
    # 'imported', 'Resolved' or 'Done' rows read as open - their vendor was erased and a vendor
    # with only that history was REMOVED. Open is now a positive list; anything else, a blank
    # status included, is history: kept, forces retire, never unassigned.
    plan = vd.plan_delete(schema)
    norm = "translate(lower(btrim(t.status::text)), '_-', '  ')"
    for s in (step(plan, "unassigned", "work_orders").apply_sql,
              step(plan, "kept", "work_orders").count_sql):
        assert f"{norm} IN (" in s
        for state in vd.OPEN_WO_STATES:
            assert f"'{state}'" in s, state
        assert "NOT IN" not in s, "a status nobody listed must not read as open"
    for history in ("imported", "done", "resolved", "completed", "closed", "finished"):
        assert history not in vd.OPEN_WO_STATES
    for state in ("in progress", "on hold", "awaiting parts", "en route", "reopened"):
        assert state in vd.OPEN_WO_STATES


def test_planned_visits_still_to_come_lose_the_vendor_and_past_ones_keep_it(schema):
    plan = vd.plan_delete(schema)
    ppm = step(plan, "unassigned", "ppm_visits")
    assert "t.completed_date IS NULL" in ppm.apply_sql
    assert "'missed'" in ppm.apply_sql and "'done'" in ppm.apply_sql
    assert "IS NOT TRUE" in step(plan, "kept", "ppm_visits").count_sql


def test_a_past_visit_never_completed_is_history_not_upcoming(schema):
    # 12 Oct 2026 (review R1): a past 'deferred' or 'imported' visit with no completed date was
    # unassigned as "upcoming", detaching the vendor's missed-visit history. Only a visit whose
    # (deferred) date is today or later is still to come; the kept count mirrors it.
    plan = vd.plan_delete(schema)
    due = ("COALESCE(t.deferred_to, t.scheduled_date) >= CURRENT_DATE"
           if "deferred_to" in schema.columns["ppm_visits"] else "t.scheduled_date >= CURRENT_DATE")
    assert due in step(plan, "unassigned", "ppm_visits").apply_sql
    assert due in step(plan, "kept", "ppm_visits").count_sql


def test_unassigned_visits_also_lose_the_vendors_contract(schema):
    # 12 Oct 2026 (review R3): the PPM health table kept planning upcoming visits against a
    # retired vendor's contract. Only a contract that is this vendor's is cleared.
    sql = step(vd.plan_delete(schema), "unassigned", "ppm_visits").apply_sql
    assert ("contract_id = CASE WHEN t.contract_id::text IN (SELECT c.id::text FROM "
            "plenum_cafm.vendor_contracts c WHERE c.vendor_id::text = :vid) THEN NULL "
            "ELSE t.contract_id END") in sql
    plan = vd.plan_delete(without(schema, "vendor_contracts"))
    assert "contract_id" not in step(plan, "unassigned", "ppm_visits").apply_sql


def test_names_are_cleared_only_on_the_production_columns_that_hold_them():
    plan = vd.plan_delete(production_like())
    wo = step(plan, "unassigned", "work_orders")
    assert ", vendor = CASE WHEN (lower(btrim(t.vendor::text))" in wo.apply_sql
    assert "vendor_name = CASE WHEN (lower(btrim(t.vendor_name::text))" in wo.apply_sql
    assert "lower(btrim(t.vendor_name::text)) = lower(btrim(CAST(:name AS text)))" in wo.apply_sql
    assert "supplier = CASE WHEN (lower(btrim(t.supplier::text))" in step(
        plan, "unassigned", "spare_parts").apply_sql
    assert "vendor_name = CASE WHEN (lower(btrim(t.vendor_name::text))" in step(
        plan, "unassigned", "ppm_visits").apply_sql
    # The shipped schema has no name columns: nothing there is matched by name.
    assert ":name" not in step(vd.plan_delete(shipped()), "unassigned", "work_orders").apply_sql


def test_a_name_never_clears_a_row_another_vendor_holds_by_id():
    # Two vendors in one company may share a name (the register holds such twins). A work order
    # whose id columns name the twin, or a company with a live twin of that name, keeps its text.
    wo = step(vd.plan_delete(production_like()), "unassigned", "work_orders").apply_sql
    assert "(t.vendor_id IS NULL OR t.vendor_id::text = :vid)" in wo
    assert "(t.assigned_vendor IS NULL OR t.assigned_vendor::text = :vid)" in wo
    assert "NOT EXISTS (SELECT 1 FROM plenum_cafm.vendors twin" in wo
    assert "btrim(CAST(:name AS text)) <> ''" in wo, "a vendor with no name would match blanks"


def test_a_name_clears_only_rows_the_company_owns_outright():
    # 12 Oct 2026 (review R1/R2): a row with no company set was the company's for a name match
    # too, so deleting A's "Acme" blanked B's unowned visit naming B's own Acme. An id is unique
    # and may widen to unowned rows; a name only clears a row owned by the company, or unowned
    # on one of its buildings.
    plan = vd.plan_delete(production_like())
    ppm = step(plan, "unassigned", "ppm_visits").apply_sql
    named = ppm.split("vendor_name = CASE WHEN ", 1)[1].split(" THEN NULL", 1)[0]
    assert "t.organization_id::text = :org" in named
    assert "IS NULL)" not in named.replace("t.vendor_id IS NULL OR", "")
    wo = step(plan, "unassigned", "work_orders").apply_sql
    named = wo.split("vendor_name = CASE WHEN ", 1)[1].split(" THEN NULL", 1)[0]
    assert ("(t.organization_id::text = :org OR (t.organization_id::text IS NULL AND "
            "t.building_id::text IN (SELECT ob.building_id::text") in named


def test_a_table_with_no_company_gets_no_name_clears_and_says_so():
    cols = {t: dict(c) for t, c in production_like().columns.items()}
    cols["ppm_visits"].pop("organization_id")
    plan = vd.plan_delete(vd.Schema(cols, ()))
    ppm = step(plan, "unassigned", "ppm_visits").apply_sql
    assert ":name" not in ppm and "vendor_name" not in ppm
    assert "t.vendor_id::text = :vid" in ppm
    assert "ppm_visits_by_name" in {u["key"] for u in plan.unavailable}


def test_maintenance_watch_items_naming_the_vendor_are_closed(schema):
    # 12 Oct 2026 (review R3): the maintenance crons name a vendor by name in payload.vendor;
    # those pending items kept asking the PM to chase a deleted vendor.
    sql = step(vd.plan_delete(schema), "closed", "approvals_queue_items").apply_sql
    assert ("t.item_type IN ('wo_sla_breach', 'ppm_due', 'ppm_missed', 'ppm_overdue', "
            "'maintenance_kpi', 'part_reorder')") in sql
    assert "lower(btrim(t.payload->>'vendor')) = lower(btrim(CAST(:name AS text)))" in sql
    watch = sql.split("t.item_type IN ('wo_sla_breach'", 1)[1]
    assert "NOT EXISTS (SELECT 1 FROM plenum_cafm.vendors twin" in watch
    assert "t.organization_id::text = :org" in watch.split(" OR (t.", 1)[0]


def test_the_asset_history_is_written_before_the_assets_are_unassigned(schema):
    assets = step(vd.plan_delete(schema), "unassigned", "assets")
    assert assets.before_sql.lstrip().startswith("INSERT INTO plenum_cafm.audit_logs")
    assert "SELECT gen_random_uuid()" in assets.before_sql
    assert "FROM plenum_cafm.assets t" in assets.before_sql
    assert "CAST(:unassigned_action AS text)" in assets.before_sql
    # audit_logs.entity_id is uuid: only an asset whose id is shaped like one can be recorded.
    assert "t.id::text ~*" in assets.before_sql
    # The same rows the update then clears.
    assert assets.apply_sql.split(" WHERE ", 1)[1] in assets.before_sql


def test_without_an_audit_table_assets_are_still_unassigned_and_the_gap_is_said(schema):
    plan = vd.plan_delete(without(schema, "audit_logs"))
    assert step(plan, "unassigned", "assets").before_sql is None
    assert "asset_history" in {u["key"] for u in plan.unavailable}


# ── what is closed, expired and ended is never deleted ──────────────────────────────────

def test_approvals_are_closed_not_deleted_and_not_decided(schema):
    plan = vd.plan_delete(schema)
    sql = step(plan, "closed", "approvals_queue_items").apply_sql
    assert sql.startswith("UPDATE plenum_cafm.approvals_queue_items t SET status = 'closed'")
    # A system close is not a decision: Home counts decided_at as "approvals decided".
    assert "decided_at" not in sql and "decided_by" not in sql
    assert "t.status = 'pending'" in sql
    # A note a PM already left is kept in front of the reason.
    assert "t.pm_notes || ' ' || CAST(:pm_note AS text)" in sql
    for about in ("t.related_entity_type = 'vendor'", "t.payload->>'vendor_id' = :vid",
                  "t.item_type LIKE 'invoice_flag%'", "plenum_cafm.contract_sla_parameters",
                  "plenum_cafm.compliance_certificates"):
        assert about in sql, about
    assert not any("DELETE FROM plenum_cafm.approvals_queue_items" in q for q in every_sql(plan))


def test_shared_passport_links_are_expired_not_deleted(schema):
    plan = vd.plan_delete(schema)
    sql = step(plan, "revoked", "approval_action_tokens").apply_sql
    assert sql.startswith("UPDATE plenum_cafm.approval_action_tokens t SET expires_at = now()")
    assert "t.expires_at > now()" in sql and "t.used_at IS NULL" in sql
    assert "q.item_type = 'passport_share'" in sql
    assert not any("DELETE FROM plenum_cafm.approval_action_tokens" in q for q in every_sql(plan))


def test_document_links_are_ended_not_deleted(schema):
    plan = vd.plan_delete(schema)
    sql = step(plan, "ended", "document_links").apply_sql
    assert "SET removed_at = now(), removed_by = CAST(CAST(:uid AS text) AS uuid)" in sql
    assert "t.entity_type = 'vendor'" in sql and "t.removed_at IS NULL" in sql
    assert not any("DELETE FROM plenum_cafm.document_links" in q for q in every_sql(plan))


def test_only_the_vendors_own_rows_are_deleted(schema):
    plan = vd.plan_delete(schema)
    deleted = {re.match(r"DELETE FROM plenum_cafm\.(\w+)", q).group(1)
               for q in every_sql(plan) if q.startswith("DELETE FROM")}
    assert deleted == {"vendor_contacts", "files", "fm_report_staleness", "resource_skills",
                       "vendors"}
    # A file row is the link; the document and its blob stay.
    assert "t.entity_type = 'vendor'" in step(plan, "removed", "files").apply_sql


def test_only_vendor_accreditation_is_archived_and_only_on_retire(schema):
    arch = step(vd.plan_delete(schema), "archived", "compliance_certificates")
    assert arch.retire_only
    assert "lower(COALESCE(to_jsonb(t)->>'cert_scope', '')) <> 'building'" in arch.apply_sql
    for k in ("'archived', true", "'archived_at'", "'archive_reason'", "'archived_by'",
              "'archived_with_vendor'"):
        assert k in arch.apply_sql, k
    # Not twice: an already archived certificate keeps the reason it was archived for.
    assert "NOT IN ('true', '1')" in arch.apply_sql


# ── references nothing names as such ────────────────────────────────────────────────────

def test_production_references_no_foreign_key_declares_still_keep_the_vendor():
    other = keys(vd.plan_delete(production_like()), "other")
    assert {"rfqs.supplier_id", "work_order_businesses.vendor_id"} <= other


def test_a_foreign_key_under_any_name_is_a_reference():
    base = shipped()
    cols = {t: dict(c) for t, c in base.columns.items()}
    cols["tender_bids"] = {"id": UUID_T, "bidder": UUID_T, "bidder_code": TEXT}
    odd = vd.Schema(cols, base.fks + (vd.Fk("tender_bids", "bidder", "id"),
                                      vd.Fk("tender_bids", "bidder_code", "vendor_code")))
    plan = vd.plan_delete(odd)
    assert "t.bidder::text = :vid" in step(plan, "other", "tender_bids.bidder").count_sql
    # A key onto another vendors column is matched through the vendor's own row.
    assert ("SELECT v.vendor_code::text FROM plenum_cafm.vendors v WHERE v.id::text = :vid"
            in step(plan, "other", "tender_bids.bidder_code").count_sql)


def test_another_companys_rows_naming_the_vendor_count_as_history(schema):
    # Never written, never silently left dangling: they keep the vendor's row.
    other = {s.key: s for s in vd.plan_delete(schema).references if s.group == "other"}
    for key in ("assets.vendor_id", "work_orders.vendor_id", "resource_skills.vendor_id",
                "document_links.entity_id"):
        assert key in other, key
        assert "IS NOT TRUE" in other[key].count_sql and _ORG.search(other[key].count_sql)


def test_a_column_that_cannot_be_emptied_is_kept_as_a_reference(schema):
    cols = {t: dict(c) for t, c in schema.columns.items()}
    cols["spare_parts"]["supplier_id"] = vd.Col("uuid", False)
    plan = vd.plan_delete(vd.Schema(cols, schema.fks))
    assert "supplier_id" not in (step(plan, "unassigned", "spare_parts").apply_sql
                                 if "spare_parts" in keys(plan, "unassigned") else "")
    assert "spare_parts.supplier_id" in keys(plan, "other")


# ── the vendor row ──────────────────────────────────────────────────────────────────────

def test_the_vendor_row_is_deleted_or_marked(schema):
    plan = vd.plan_delete(schema)
    assert plan.remove_sql.startswith("DELETE FROM plenum_cafm.vendors t WHERE t.id::text = :vid")
    assert "status = 'deleted'" in plan.retire_sql
    assert "vendor_name = CAST(:marked AS text)" in plan.retire_sql
    # Frees uq(organization_id, vendor_code) for a supplier taken on again.
    assert "vendor_code = NULL" in plan.retire_sql


def test_a_vendors_table_without_status_cannot_retire():
    # The status is the authority every live list filters on; without it a "retired" vendor
    # would still be offered everywhere, so retiring is refused rather than half done.
    plan = vd.plan_delete(without(shipped(), "vendors", "status"))
    assert plan.retire_sql is None
    assert plan.remove_sql.startswith("DELETE FROM plenum_cafm.vendors")


@pytest.mark.parametrize("shape,size", [("shipped", 255), ("production", 200)])
def test_the_deleted_mark_fits_the_name_column(shape, size):
    plan = vd.plan_delete(SHAPES[shape]())
    assert plan.name_max == size
    name = "Northbridge Mechanical & Electrical Building Services " * 5
    b = vd.binds({"id": "V-01", "vendor_name": name}, organization_id="o", user_id=None,
                 role="admin", reason=None, name_max=plan.name_max, at="2026-10-12T10:00:00+00:00")
    assert len(b["marked"]) <= size and b["marked"].endswith(" (deleted)")
    assert b["name"] == name.strip()


def test_the_reason_is_carried_into_the_notes():
    b = vd.binds({"id": "V-01", "vendor_name": "Acme"}, organization_id="o", user_id=None,
                 role="admin", reason="  Duplicate of Acme Ltd  ", name_max=255, at="t")
    assert b["pm_note"] == "Closed: vendor deleted — Duplicate of Acme Ltd"
    assert b["archive_reason"] == "Vendor deleted — Duplicate of Acme Ltd"
    plain = vd.binds({"id": "V-01", "vendor_name": "Acme"}, organization_id="o", user_id=None,
                     role="admin", reason=None, name_max=255, at="t")
    assert plain["pm_note"] == "Closed: vendor deleted"
    assert plain["archive_reason"] == "Vendor deleted"
