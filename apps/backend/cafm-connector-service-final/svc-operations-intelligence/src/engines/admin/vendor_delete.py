"""Deleting a vendor, for good, from one company.

Hussain, 12 Oct 2026: a vendor with no history is REMOVED - its row goes. A vendor WITH history
is RETIRED - its row stays so the work orders, invoices and certificates it appears on still
resolve, its status becomes 'deleted' and its name carries " (deleted)", and from then on it is
gone from every live list, picker, scan, score, recommendation and email (``live_vendor_sql``).
Either way it is permanent, an admin's decision confirmed by typing the vendor's name, and it
is written together with its entry in the vendor audit trail or not at all: a delete nobody can
trace is the one thing this must never make.

What it does, by group, each inside the admin's company only:

    unassigned  current assignments cleared: assets, OPEN work orders, planned visits still to
                come, a part's preferred supplier, a maintenance plan's vendor. Each asset's own
                history says so (one audit_logs row per asset, written first).
    closed      pending approvals about the vendor - the maintenance crons' items naming it
                too - closed: never deleted (their sent emails point at them) and never
                "decided" (Home counts decided_at as a person's call).
    revoked     the vendor's shared passport links, expired now.
    ended       register documents linked to the vendor, ended - a link is never deleted.
    removed     what only the vendor owns: contacts, file links, report reminders, skill cards.
    archived    on retire, the vendor's own accreditation certificates.
    kept        history, never written: closed work, invoices, certificates, scores, contract
                terms, contracts, past visits, labour, purchase orders - in ANY company. One row
                of any of it, or of anything else naming the vendor (``other``), means retire.

**The schema is read every call.** The two databases disagree: production's vendors.id is
varchar with legacy ids ('V-01') beside uuids, most referencing columns are uuid, assets.vendor_id
is text, and production declares no foreign keys onto vendors while carrying vendor columns no
migration here creates. So ids are compared as text everywhere, a table or column that is not
there is skipped and said (``unavailable``), and every column named like a vendor reference -
or declared as one - that this module does not otherwise handle counts as history.

One transaction: every statement waits at most 5 s for a lock, a real delete locks the
vendor's row first (a dry run only reads it), and if anything refuses, nothing has changed. Every identifier formatted into SQL comes
from the introspected schema or the literals here and must match ``_SAFE``; ids and names are
always bound.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, NamedTuple
from uuid import UUID

from sqlalchemy import text

from ...core.logging import get_logger
from ...shared.approvals import PLATFORM_FEATURE, write_audit
from ...shared.vendor_identity import DELETED_STATUS, live_vendor_sql, mark_deleted_name
from ..auth import vendor_audit
from ..maintenance.watch import DONE_STATES

log = get_logger(__name__)

_SAFE = re.compile(r"^[a-z_][a-z0-9_]*$")
GROUPS = ("removed", "unassigned", "closed", "revoked", "ended", "archived", "kept", "other")
ID_MAX = 100
#: Whitespace and control characters: no vendor id carries them, and a pasted one that does
#: is refused rather than trimmed into some other vendor's id.
_BAD_ID = re.compile(r"[\s\x00-\x1f\x7f]")
#: Shown at most in the plan; the trail event keeps up to ``_EVENT_ITEMS``.
_PLAN_ITEMS = 50
_EVENT_ITEMS = 100
REASON_MAX = 500

_TEXT_TYPES = frozenset({"text", "character varying", "character"})
_JSON_TYPES = frozenset({"json", "jsonb"})
#: Column names that point at a vendor wherever they appear, declared or not.
_REFERENCE_NAMES = ("vendor_id", "supplier_id", "assigned_vendor", "contractor_id")
_ORGS = ("organization_id", "org_id")
_UUID_SHAPE = "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"
#: A planned visit in one of these is past, as a finished work order is.
_PPM_DONE = DONE_STATES + ("done", "missed")
#: A work order is open only in one of these states (lower-cased, trimmed, '_' and '-' read as
#: spaces) and with no finish date set. 12 Oct 2026 (review R1): "open" used to be "not in a
#: finished state", so an imported sheet's 'imported', 'Resolved' or 'Done' rows read as open,
#: lost their vendor, and a vendor with only that history was removed. Any status not listed
#: here - blank, 'imported', 'done', 'resolved', or one nobody foresaw - is history: kept,
#: forcing a retire, never unassigned.
OPEN_WO_STATES = ("open", "new", "raised", "requested", "assigned", "accepted", "acknowledged",
                  "dispatched", "scheduled", "in progress", "started", "on hold", "pending",
                  "awaiting parts", "waiting", "reopened", "en route")
#: Pending items the maintenance crons raise (maintenance/watch.py) that name their vendor only
#: by name, in payload.vendor.
_WATCH_ITEMS = ("wo_sla_breach", "ppm_due", "ppm_missed", "ppm_overdue", "maintenance_kpi",
                "part_reorder")


class Col(NamedTuple):
    data_type: str
    nullable: bool = True
    max_len: int | None = None


class Fk(NamedTuple):
    """A single-column foreign key onto plenum_cafm.vendors."""
    table: str
    column: str
    parent_column: str


class Schema(NamedTuple):
    columns: dict[str, dict[str, Col]]
    fks: tuple[Fk, ...] = ()


class Step(NamedTuple):
    group: str
    key: str
    label: str
    table: str
    count_sql: str
    #: None for history: counted, never written.
    apply_sql: str | None = None
    #: The rows the trail event names (assets, work orders, approvals), read before the write.
    items_sql: str | None = None
    #: Written first, over the same rows (each asset's own history entry).
    before_sql: str | None = None
    retire_only: bool = False


class Plan(NamedTuple):
    #: In the order they are applied; the vendor row itself is last and separate.
    writes: list[Step]
    #: kept + other: counted without the company filter, never written.
    references: list[Step]
    remove_sql: str
    #: None when the vendors table has no status: a retired vendor could not be told apart.
    retire_sql: str | None
    name_max: int | None
    unavailable: list[dict[str, str]]


#: What each part is called on screen. Never a table name.
LABELS = {
    ("unassigned", "assets"): "Assets",
    ("unassigned", "work_orders"): "Open work orders",
    ("unassigned", "ppm_visits"): "Upcoming planned maintenance visits",
    ("unassigned", "spare_parts"): "Spare parts it supplies",
    ("unassigned", "maintenance_plans"): "Maintenance plans",
    ("closed", "approvals_queue_items"): "Pending approvals",
    ("revoked", "approval_action_tokens"): "Shared passport links",
    ("ended", "document_links"): "Linked documents",
    ("ended", "vendor_buildings"): "Building assignments",
    ("removed", "vendor_contacts"): "Contacts",
    ("removed", "files"): "Attached files",
    ("removed", "fm_report_staleness"): "Report reminders",
    ("removed", "resource_skills"): "Operative skill cards",
    ("archived", "compliance_certificates"): "Accreditation certificates",
    ("kept", "work_orders"): "Closed work orders",
    ("kept", "invoice_verifications"): "Invoices checked",
    ("kept", "invoice_lines"): "Invoice lines",
    ("kept", "cost_variance_alerts"): "Cost alerts",
    ("kept", "compliance_certificates"): "Certificates",
    ("kept", "vendor_wo_scores"): "Work order scores",
    ("kept", "vendor_monthly_scorecards"): "Monthly scorecards",
    ("kept", "contract_sla_parameters"): "Contract terms",
    ("kept", "contract_documents"): "Contract documents",
    ("kept", "vendor_contracts"): "Contracts",
    ("kept", "ppm_visits"): "Past planned maintenance visits",
    ("kept", "resources"): "Labour records",
    ("kept", "purchase_orders"): "Purchase orders",
}
OTHER_LABEL = "Other records"
_ASSET_HISTORY = {"key": "asset_history", "label": "Asset history entries"}

#: History, in any company: (table, the columns naming the vendor). Work orders and visits
#: count here only once finished; open ones are current assignments.
_KEPT: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("work_orders", ("vendor_id", "assigned_vendor")),
    ("invoice_verifications", ("vendor_id",)),
    ("invoice_lines", ("vendor_id",)),
    ("cost_variance_alerts", ("vendor_id",)),
    ("compliance_certificates", ("vendor_id",)),
    ("vendor_wo_scores", ("vendor_id",)),
    ("vendor_monthly_scorecards", ("vendor_id",)),
    ("contract_sla_parameters", ("vendor_id",)),
    ("contract_documents", ("vendor_id",)),
    ("vendor_contracts", ("vendor_id",)),
    ("ppm_visits", ("vendor_id",)),
    ("resources", ("vendor_id",)),
    ("purchase_orders", ("supplier_id",)),
)


def _where(*parts: str | None) -> str:
    return " AND ".join(p for p in parts if p)


def _cast(col: Col, expr: str) -> str:
    """``expr`` (text) as the column's type, for the columns this writes that may be uuid or
    json on one database and text on the other. Text needs no cast."""
    if col.data_type == "uuid":
        return f"CAST({expr} AS uuid)"
    if col.data_type in _JSON_TYPES:
        return f"CAST({expr} AS {col.data_type})"
    return expr


def _clean(schema: Schema) -> Schema:
    """Only plain names survive: anything else is never formatted into SQL."""
    cols = {t: {c: v for c, v in cs.items() if _SAFE.match(c)}
            for t, cs in schema.columns.items() if _SAFE.match(t)}
    fks = tuple(f for f in schema.fks if all(_SAFE.match(x or "") for x in f))
    return Schema(cols, fks)


def _company(cols: dict[str, Col], schema: Schema, a: str = "t") -> str:
    """The admin's company's rows of a table, binding :org; "" for a table with no company
    of its own (reached through the vendor, or through a parent that is filtered).

    A row with no company set that names this vendor is about this company's vendor, so it is
    the company's - unless it sits on a building of another company."""
    orgs = [f"{a}.{c}::text" for c in _ORGS if c in cols]
    if not orgs:
        return ""
    owner = orgs[0] if len(orgs) == 1 else f"COALESCE({', '.join(orgs)})"
    nobody = f"{owner} IS NULL"
    if "building_id" in cols:
        b = schema.columns.get("buildings", {})
        if "building_id" in b and "organization_id" in b:
            nobody += (f" AND ({a}.building_id IS NULL OR {a}.building_id::text IN ("
                       "SELECT ob.building_id::text FROM plenum_cafm.buildings ob "
                       "WHERE ob.organization_id::text = :org))")
        else:
            nobody += f" AND {a}.building_id IS NULL"
    return f"({owner} = :org OR ({nobody}))"


def _owned(cols: dict[str, Col], schema: Schema, a: str = "t") -> str:
    """The admin's company's rows, strictly: its own, or unowned on one of its buildings. ""
    for a table with no company column. 12 Oct 2026 (review R1/R2): a name, unlike an id, is
    not unique across companies, so an unowned row naming "Acme" may be another company's Acme
    - a name only ever clears a row this proves is the company's."""
    orgs = [f"{a}.{c}::text" for c in _ORGS if c in cols]
    if not orgs:
        return ""
    owner = orgs[0] if len(orgs) == 1 else f"COALESCE({', '.join(orgs)})"
    b = schema.columns.get("buildings", {})
    if "building_id" in cols and "building_id" in b and "organization_id" in b:
        return (f"({owner} = :org OR ({owner} IS NULL AND {a}.building_id::text IN ("
                "SELECT ob.building_id::text FROM plenum_cafm.buildings ob "
                "WHERE ob.organization_id::text = :org)))")
    return f"{owner} = :org"


def _wo_open(cols: dict[str, Col], a: str = "t") -> str:
    """An open work order: in an open state (``OPEN_WO_STATES``) with no finish date. Each part
    only where the column exists; a blank status is not open."""
    parts = []
    if "status" in cols:
        states = ", ".join(f"'{s}'" for s in OPEN_WO_STATES)
        parts.append(f"translate(lower(btrim({a}.status::text)), '_-', '  ') IN ({states})")
    parts += [f"{a}.{c} IS NULL" for c in ("completed_at", "closed_at") if c in cols]
    # Nothing to tell open from closed by: history, which keeps the vendor (retire) rather than
    # stripping every job of it (pre-push review, 12 Oct 2026).
    return " AND ".join(parts) or "FALSE"


def _ppm_open(cols: dict[str, Col], a: str = "t") -> str:
    """A visit still to come: not completed, due today or later (its deferred date where it has
    one), and not in a finished state. 12 Oct 2026 (review R1): a past visit never completed -
    deferred, imported, or scheduled and never marked missed - is the vendor's history."""
    parts = []
    if "status" in cols:
        states = ", ".join(f"'{s}'" for s in _PPM_DONE)
        parts.append(f"lower(COALESCE({a}.status::text, '')) NOT IN ({states})")
    if "completed_date" in cols:
        parts.append(f"{a}.completed_date IS NULL")
    due = [f"{a}.{c}" for c in ("deferred_to", "scheduled_date") if c in cols]
    if due:
        when = due[0] if len(due) == 1 else f"COALESCE({', '.join(due)})"
        parts.append(f"{when} >= CURRENT_DATE")
    return " AND ".join(parts) or "FALSE"


#: A name only clears a row when the vendor has one, and no other live vendor of the company
#: answers to it - twins exist in the register, and the twin's work must keep its name.
_NAME_OK = ("btrim(CAST(:name AS text)) <> '' AND NOT EXISTS ("
            "SELECT 1 FROM plenum_cafm.vendors twin WHERE twin.id::text <> :vid "
            "AND to_jsonb(twin)->>'organization_id' = :org "
            "AND lower(btrim(to_jsonb(twin)->>'vendor_name')) = lower(btrim(CAST(:name AS text))) "
            f"AND {live_vendor_sql('twin')})")


def _name_match(col: str, id_cols: list[str], owned: str, a: str = "t") -> str:
    """A text column naming the vendor: by its name, on a row the company owns outright
    (``owned``) whose id columns name no other vendor - or by its id, which some importers
    write there."""
    guards = [f"({a}.{c} IS NULL OR {a}.{c}::text = :vid)" for c in id_cols]
    named = _where(f"lower(btrim({a}.{col}::text)) = lower(btrim(CAST(:name AS text)))", _NAME_OK,
                   owned, *guards)
    return f"({named}) OR {a}.{col}::text = :vid"


def plan_delete(schema: Schema) -> Plan:
    """Every statement a delete of the vendor bound as :vid, in the company bound as :org, would
    run - and what it would count first. Pure: the schema in, SQL out."""
    schema = _clean(schema)
    columns = schema.columns
    writes: list[Step] = []
    references: list[Step] = []
    unavailable: list[dict[str, str]] = []
    covered: set[tuple[str, str]] = set()
    unassigned_ids: set[tuple[str, str]] = set()

    def missing(group: str, key: str) -> None:
        if key not in {u["key"] for u in unavailable}:
            unavailable.append({"key": key, "label": LABELS[(group, key)]})

    def elsewhere(table: str, column: str, match: str, company: str, extra: str = "") -> None:
        """Rows of another company naming the vendor are never written; they are history."""
        if company:
            references.append(Step(
                "other", f"{table}.{column}", OTHER_LABEL, table,
                f"SELECT count(*) FROM plenum_cafm.{table} t WHERE "
                + _where(extra, match, f"({company}) IS NOT TRUE")))

    # ── unassigned: current assignments ─────────────────────────────────────────────────
    wo = columns.get("work_orders", {})
    ppm = columns.get("ppm_visits", {})
    current = (
        ("assets", ("vendor_id",), (), ""),
        ("work_orders", ("vendor_id", "assigned_vendor"), ("vendor", "vendor_name"), _wo_open(wo)),
        ("ppm_visits", ("vendor_id",), ("vendor_name",), _ppm_open(ppm)),
        ("spare_parts", ("supplier_id",), ("supplier", "vendor_name"), ""),
        ("maintenance_plans", ("vendor_id",), ("vendor_name",), ""),
    )
    for table, id_names, text_names, still_open in current:
        cols = columns.get(table)
        if cols is None:
            missing("unassigned", table)
            continue
        # A column that cannot be emptied is left to the reference count below: the vendor is
        # then retired, never half-unassigned by a refused statement.
        ids = [c for c in id_names if c in cols and cols[c].nullable]
        ids += [c for c in text_names
                if c in cols and cols[c].nullable and cols[c].data_type not in _TEXT_TYPES]
        names = [c for c in text_names
                 if c in cols and cols[c].nullable and cols[c].data_type in _TEXT_TYPES]
        owned = _owned(cols, schema)
        if names and not owned:
            # Nothing says whose a row is, so no name can be matched to this company's vendor.
            unavailable.append({"key": f"{table}_by_name",
                                "label": LABELS[("unassigned", table)] + " named only in text"})
            names = []
        if not ids and not names:
            missing("unassigned", table)
            continue
        company = _company(cols, schema)
        matches = [f"t.{c}::text = :vid" for c in ids] + [
            f"({_name_match(c, ids, owned)})" for c in names]
        where = _where(still_open, company, "(" + " OR ".join(matches) + ")")
        sets = [f"{c} = CASE WHEN t.{c}::text = :vid THEN NULL ELSE t.{c} END" for c in ids]
        sets += [f"{c} = CASE WHEN {_name_match(c, ids, owned)} THEN NULL ELSE t.{c} END"
                 for c in names]
        vc = columns.get("vendor_contracts", {})
        if (table == "ppm_visits" and "contract_id" in cols and cols["contract_id"].nullable
                and {"id", "vendor_id"} <= set(vc)):
            # 12 Oct 2026 (review R3): a visit losing its vendor also loses that vendor's
            # contract, or the PPM health table keeps planning it against a contract nobody holds.
            sets.append("contract_id = CASE WHEN t.contract_id::text IN (SELECT c.id::text FROM "
                        "plenum_cafm.vendor_contracts c WHERE c.vendor_id::text = :vid) THEN NULL "
                        "ELSE t.contract_id END")
        if "updated_at" in cols:
            sets.append("updated_at = now()")
        items = before = None
        if table == "assets":
            items = ("SELECT t.id::text AS id, to_jsonb(t)->>'asset_name' AS name, "
                     "to_jsonb(t)->>'asset_code' AS code FROM plenum_cafm.assets t "
                     f"WHERE {where} ORDER BY 2 NULLS LAST, 1 LIMIT {_EVENT_ITEMS}")
            before = _asset_history(schema, where)
            if before is None:
                unavailable.append(dict(_ASSET_HISTORY))
        elif table == "work_orders":
            items = ("SELECT t.id::text AS id, to_jsonb(t)->>'wo_code' AS code, "
                     "to_jsonb(t)->>'title' AS title FROM plenum_cafm.work_orders t "
                     f"WHERE {where} ORDER BY 2 NULLS LAST, 1 LIMIT {_EVENT_ITEMS}")
        writes.append(Step(
            "unassigned", table, LABELS[("unassigned", table)], table,
            f"SELECT count(*) FROM plenum_cafm.{table} t WHERE {where}",
            f"UPDATE plenum_cafm.{table} t SET {', '.join(sets)} WHERE {where}",
            items_sql=items, before_sql=before))
        for c in ids:
            covered.add((table, c))
            unassigned_ids.add((table, c))
            elsewhere(table, c, f"t.{c}::text = :vid", company,
                      still_open if still_open != "TRUE" else "")

    # ── closed: pending approvals about the vendor ──────────────────────────────────────
    certs = columns.get("compliance_certificates", {})
    archivable = ({"id", "vendor_id", "raw_metadata"} <= set(certs)
                  and certs["raw_metadata"].data_type == "jsonb")

    def archive_where(a: str) -> str:
        return _where(f"{a}.vendor_id::text = :vid",
                      f"lower(COALESCE(to_jsonb({a})->>'cert_scope', '')) <> 'building'",
                      f"COALESCE({a}.raw_metadata->>'archived', 'false') NOT IN ('true', '1')",
                      _company(certs, schema, a))

    q = columns.get("approvals_queue_items", {})
    subject = {"related_entity_type", "related_entity_id"} <= set(q)
    payload = "payload" in q and q["payload"].data_type in _JSON_TYPES
    about = []
    if subject:
        about.append("(t.related_entity_type = 'vendor' AND t.related_entity_id::text = :vid)")
    if payload:
        about.append("t.payload->>'vendor_id' = :vid")
    if "item_type" in q and "related_entity_id" in q:
        # An invoice flag names its vendor as the subject id, whatever its subject type says.
        about.append("(t.item_type LIKE 'invoice_flag%' AND t.related_entity_id::text = :vid)")
    csp = columns.get("contract_sla_parameters", {})
    if subject and {"id", "vendor_id"} <= set(csp):
        about.append("(t.related_entity_type = 'contract_sla_parameters' AND "
                     "t.related_entity_id::text IN (SELECT p.id::text FROM "
                     "plenum_cafm.contract_sla_parameters p WHERE p.vendor_id::text = :vid))")
    if payload and "item_type" in q:
        owned = _owned(q, schema)
        if owned:
            # 12 Oct 2026 (review R3): the maintenance crons name the vendor only by name; the
            # same twin guard as a cleared name, and only the company's own items.
            kinds = ", ".join(f"'{k}'" for k in _WATCH_ITEMS)
            about.append(f"(t.item_type IN ({kinds}) AND {owned} AND "
                         "lower(btrim(t.payload->>'vendor')) = lower(btrim(CAST(:name AS text))) "
                         f"AND {_NAME_OK})")
    if subject and archivable:
        about.append("(t.related_entity_type = 'compliance_certificate' AND "
                     "t.related_entity_id::text IN (SELECT c.id::text FROM "
                     f"plenum_cafm.compliance_certificates c WHERE {archive_where('c')}))")
    if {"id", "status"} <= set(q) and about:
        where = _where("t.status = 'pending'", _company(q, schema), "(" + " OR ".join(about) + ")")
        sets = ["status = 'closed'"]
        if "pm_notes" in q:
            # A note a PM already left is kept in front of the reason (scoring._close_cost_alerts).
            sets.append("pm_notes = CASE WHEN COALESCE(btrim(t.pm_notes), '') = '' "
                        "THEN CAST(:pm_note AS text) "
                        "ELSE t.pm_notes || ' ' || CAST(:pm_note AS text) END")
        if "updated_at" in q:
            sets.append("updated_at = now()")
        writes.append(Step(
            "closed", "approvals_queue_items", LABELS[("closed", "approvals_queue_items")],
            "approvals_queue_items",
            f"SELECT count(*) FROM plenum_cafm.approvals_queue_items t WHERE {where}",
            f"UPDATE plenum_cafm.approvals_queue_items t SET {', '.join(sets)} WHERE {where}",
            items_sql=(f"SELECT t.id::text AS id FROM plenum_cafm.approvals_queue_items t "
                       f"WHERE {where} ORDER BY 1 LIMIT {_EVENT_ITEMS}")))
    else:
        missing("closed", "approvals_queue_items")

    # ── revoked: the vendor's shared passport links ────────────────────────────────────
    tk = columns.get("approval_action_tokens", {})
    q_about = (["q.related_entity_id::text = :vid"] if "related_entity_id" in q else []) + (
        ["q.payload->>'vendor_id' = :vid"] if payload else [])
    if {"queue_item_id", "expires_at"} <= set(tk) and {"id", "item_type"} <= set(q) and q_about:
        shares = ("SELECT q.id::text FROM plenum_cafm.approvals_queue_items q WHERE "
                  + _where("q.item_type = 'passport_share'", "(" + " OR ".join(q_about) + ")",
                           _company(q, schema, "q")))
        where = _where("t.expires_at > now()", "t.used_at IS NULL" if "used_at" in tk else "",
                       f"t.queue_item_id::text IN ({shares})")
        writes.append(Step(
            "revoked", "approval_action_tokens", LABELS[("revoked", "approval_action_tokens")],
            "approval_action_tokens",
            f"SELECT count(*) FROM plenum_cafm.approval_action_tokens t WHERE {where}",
            f"UPDATE plenum_cafm.approval_action_tokens t SET expires_at = now() WHERE {where}"))
    else:
        missing("revoked", "approval_action_tokens")

    # ── ended: register documents linked to the vendor ──────────────────────────────────
    dl = columns.get("document_links", {})
    if {"entity_type", "entity_id", "removed_at"} <= set(dl):
        company = _company(dl, schema)
        match = "t.entity_type = 'vendor' AND t.entity_id::text = :vid"
        where = _where(match, "t.removed_at IS NULL", company)
        sets = ["removed_at = now()"]
        if "removed_by" in dl:
            sets.append(f"removed_by = {_cast(dl['removed_by'], 'CAST(:uid AS text)')}")
        writes.append(Step(
            "ended", "document_links", LABELS[("ended", "document_links")], "document_links",
            f"SELECT count(*) FROM plenum_cafm.document_links t WHERE {where}",
            f"UPDATE plenum_cafm.document_links t SET {', '.join(sets)} WHERE {where}"))
        elsewhere("document_links", "entity_id", match, company)
    else:
        missing("ended", "document_links")

    # ── ended: the buildings the vendor was assigned to (compliance/vendor_buildings.py) ─────
    # Ended, not deleted: who assigned it where stays readable. A database without the table has
    # nothing assigned, so it is not reported as skipped.
    vb = columns.get("vendor_buildings", {})
    if {"vendor_id", "removed_at"} <= set(vb):
        company = _company(vb, schema)
        match = "t.vendor_id::text = :vid"
        where = _where(match, "t.removed_at IS NULL", company)
        sets = ["removed_at = now()"] + (["removed_by = CAST(:uid AS text)"] if "removed_by" in vb else [])
        writes.append(Step(
            "ended", "vendor_buildings", LABELS[("ended", "vendor_buildings")], "vendor_buildings",
            f"SELECT count(*) FROM plenum_cafm.vendor_buildings t WHERE {where}",
            f"UPDATE plenum_cafm.vendor_buildings t SET {', '.join(sets)} WHERE {where}"))
        covered.add(("vendor_buildings", "vendor_id"))
        elsewhere("vendor_buildings", "vendor_id", match, company)
    elif vb:
        missing("ended", "vendor_buildings")

    # ── removed: what only the vendor owns ──────────────────────────────────────────────
    owned = (
        ("vendor_contacts", "vendor_id", ""),
        # The link row only: the document and its blob stay where they are.
        ("files", "entity_id", "t.entity_type = 'vendor'"),
        ("fm_report_staleness", "vendor_id", ""),
        ("resource_skills", "vendor_id", ""),
    )
    for table, column, kind in owned:
        cols = columns.get(table, {})
        if column not in cols or (kind and "entity_type" not in cols):
            missing("removed", table)
            continue
        company = _company(cols, schema)
        match = _where(kind, f"t.{column}::text = :vid")
        where = _where(match, company)
        writes.append(Step(
            "removed", table, LABELS[("removed", table)], table,
            f"SELECT count(*) FROM plenum_cafm.{table} t WHERE {where}",
            f"DELETE FROM plenum_cafm.{table} t WHERE {where}"))
        covered.add((table, column))
        elsewhere(table, column, match, company)

    # ── archived (retire only): the vendor's own accreditation ──────────────────────────
    if archivable:
        where = archive_where("t")
        stamp = ("raw_metadata = COALESCE(t.raw_metadata, '{}'::jsonb) || jsonb_strip_nulls("
                 "jsonb_build_object('archived', true, 'archived_at', CAST(:at AS text), "
                 "'archive_reason', CAST(:archive_reason AS text), "
                 "'archived_by', CAST(:uid AS text), 'archived_with_vendor', CAST(:vid AS text)))")
        sets = [stamp] + (["updated_at = now()"] if "updated_at" in certs else [])
        writes.append(Step(
            "archived", "compliance_certificates", LABELS[("archived", "compliance_certificates")],
            "compliance_certificates",
            f"SELECT count(*) FROM plenum_cafm.compliance_certificates t WHERE {where}",
            f"UPDATE plenum_cafm.compliance_certificates t SET {', '.join(sets)} WHERE {where}",
            retire_only=True))
    else:
        missing("archived", "compliance_certificates")

    # ── kept: history, in any company ───────────────────────────────────────────────────
    for table, names in _KEPT:
        cols = columns.get(table, {})
        present = [c for c in names if c in cols]
        if not present:
            missing("kept", table)
            continue
        finished = ""
        if table == "work_orders":
            finished = f"({_wo_open(cols)}) IS NOT TRUE"
        elif table == "ppm_visits":
            finished = f"({_ppm_open(cols)}) IS NOT TRUE"
        match = "(" + " OR ".join(f"t.{c}::text = :vid" for c in present) + ")"
        references.append(Step(
            "kept", table, LABELS[("kept", table)], table,
            f"SELECT count(*) FROM plenum_cafm.{table} t WHERE {_where(match, finished)}"))
        for c in present:
            # Finished work alone does not account for the column: open rows that could not be
            # unassigned are still counted, by the sweep below.
            if not finished or (table, c) in unassigned_ids:
                covered.add((table, c))

    # ── other: anything else naming the vendor, declared or by its name ─────────────────
    vendors = columns.get("vendors", {})
    declared = {(f.table, f.column): f.parent_column for f in schema.fks
                if f.parent_column in vendors}
    for table in sorted(columns):
        if table == "vendors":
            continue
        for col in sorted(columns[table]):
            if (table, col) in covered:
                continue
            if col not in _REFERENCE_NAMES and (table, col) not in declared:
                continue
            parent = declared.get((table, col), "id")
            match = (f"t.{col}::text = :vid" if parent == "id" else
                     f"t.{col}::text IN (SELECT v.{parent}::text FROM plenum_cafm.vendors v "
                     "WHERE v.id::text = :vid)")
            references.append(Step("other", f"{table}.{col}", OTHER_LABEL, table,
                                   f"SELECT count(*) FROM plenum_cafm.{table} t WHERE {match}"))

    # ── the vendor row ──────────────────────────────────────────────────────────────────
    mine = "t.id::text = :vid AND to_jsonb(t)->>'organization_id' = :org"
    retire_sql = None
    if "status" in vendors:
        sets = [f"status = '{DELETED_STATUS}'"]
        if "vendor_name" in vendors:
            sets.append("vendor_name = CAST(:marked AS text)")
        if "vendor_code" in vendors and vendors["vendor_code"].nullable:
            # Frees uq(organization_id, vendor_code) for a supplier taken on again.
            sets.append("vendor_code = NULL")
        if "updated_at" in vendors:
            sets.append("updated_at = now()")
        retire_sql = f"UPDATE plenum_cafm.vendors t SET {', '.join(sets)} WHERE {mine}"
    name_col = vendors.get("vendor_name")
    return Plan(writes, references, f"DELETE FROM plenum_cafm.vendors t WHERE {mine}", retire_sql,
                name_col.max_len if name_col else None, unavailable)


def _asset_history(schema: Schema, where: str) -> str | None:
    """One audit_logs row per asset losing the vendor, as the vendor drawer writes for a
    reassignment (energy/asset_vendor.py), over exactly the rows the update clears. None when
    the table cannot hold one."""
    al = schema.columns.get("audit_logs", {})
    if not {"id", "organization_id", "action", "entity_type", "entity_id", "metadata"} <= set(al):
        return None
    meta = ("json_build_object('asset', json_build_object('id', t.id::text, "
            "'name', to_jsonb(t)->>'asset_name', 'code', to_jsonb(t)->>'asset_code'), "
            "'from', json_build_object('id', CAST(:vid AS text), 'name', CAST(:name AS text)), "
            "'to', NULL, 'note', 'Vendor deleted', 'cause', 'vendor_deleted', "
            "'by_role', CAST(:role AS text))::text")
    values = {
        "id": "gen_random_uuid()" if al["id"].data_type == "uuid" else "gen_random_uuid()::text",
        "organization_id": _cast(al["organization_id"], "CAST(:org AS text)"),
        "user_id": _cast(al["user_id"], "CAST(:uid AS text)") if "user_id" in al else None,
        "action": "CAST(:unassigned_action AS text)",
        "entity_type": "'asset'",
        "entity_id": _cast(al["entity_id"], "t.id::text"),
        "metadata": _cast(al["metadata"], meta),
        "created_at": "now()" if "created_at" in al else None,
    }
    names = [c for c, v in values.items() if v is not None]
    # audit_logs.entity_id is uuid: an asset whose id is not shaped like one cannot be named.
    shape = f" AND t.id::text ~* {_UUID_SHAPE}" if al["entity_id"].data_type == "uuid" else ""
    return (f"INSERT INTO plenum_cafm.audit_logs ({', '.join(names)}) "
            f"SELECT {', '.join(values[c] for c in names)} "
            f"FROM plenum_cafm.assets t WHERE {where}{shape}")


def clean_reason(reason: str | None) -> str | None:
    s = str(reason or "").strip()[:REASON_MAX].strip()
    return s or None


def binds(vendor: dict[str, Any], *, organization_id: Any, user_id: Any, role: str | None,
          reason: str | None, name_max: int | None, at: str) -> dict[str, Any]:
    """The values every statement of the plan is bound with."""
    name = str(vendor.get("vendor_name") or "").strip()
    why = clean_reason(reason)
    suffix = f" — {why}" if why else ""
    return {
        "vid": str(vendor["id"]), "org": str(organization_id), "name": name,
        "marked": mark_deleted_name(name, name_max),
        "uid": str(user_id) if user_id else None, "role": role,
        "unassigned_action": vendor_audit.UNASSIGNED,
        "pm_note": "Closed: vendor deleted" + suffix,
        "archive_reason": "Vendor deleted" + suffix,
        "at": at,
    }


# ── running it ──────────────────────────────────────────────────────────────────────────


class VendorDeleteError(Exception):
    """A delete refused. Nothing was changed: the whole delete is one transaction."""


class VendorDeleteBadId(VendorDeleteError, ValueError):
    """Not something a vendor id can be."""


class VendorNotFound(VendorDeleteError, LookupError):
    """No such vendor in this company - the same answer for another company's, so a delete
    can never be used to learn which ids exist elsewhere."""


class VendorBusy(VendorDeleteError):
    """The vendor's row is locked by another change; it did not come free within 5 s."""


class VendorConfirmMismatch(VendorDeleteError):
    def __init__(self, confirm_with: str) -> None:
        super().__init__("the confirmation does not match the vendor")
        self.confirm_with = confirm_with


class VendorPlanChanged(VendorDeleteError):
    """What the delete would do is no longer what the admin was shown. ``plan`` is the plan as
    it stands now (None if it could not be read)."""

    def __init__(self, plan: dict[str, Any] | None = None) -> None:
        super().__init__("the delete plan changed since it was shown")
        self.plan = plan


class VendorDeleteFailed(VendorDeleteError):
    """A statement was refused; ``step`` names the part of the plan it was."""

    def __init__(self, step: str, label: str, error: str) -> None:
        super().__init__(f"{step}: {error}")
        self.step, self.label, self.error = step, label, error


class VendorNotRecorded(VendorDeleteError):
    """The trail event could not be written, so the delete was rolled back."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


_COLUMNS = """
    SELECT c.table_name, c.column_name, c.data_type, c.is_nullable, c.character_maximum_length
      FROM information_schema.columns c
      JOIN information_schema.tables t
        ON t.table_schema = c.table_schema AND t.table_name = c.table_name
     WHERE c.table_schema = 'plenum_cafm' AND t.table_type = 'BASE TABLE'"""

#: Single-column foreign keys onto vendors, from any plenum_cafm table.
_FKS = """
    SELECT cl.relname, a.attname, pa.attname
      FROM pg_constraint k
      JOIN pg_class cl ON cl.oid = k.conrelid
      JOIN pg_class pc ON pc.oid = k.confrelid
      JOIN pg_namespace n ON n.oid = cl.relnamespace
      JOIN pg_namespace pn ON pn.oid = pc.relnamespace
      JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
      JOIN pg_attribute pa ON pa.attrelid = k.confrelid AND pa.attnum = k.confkey[1]
     WHERE k.contype = 'f' AND n.nspname = 'plenum_cafm' AND pn.nspname = 'plenum_cafm'
       AND pc.relname = 'vendors' AND array_length(k.conkey, 1) = 1"""

#: The company's vendor, and only its own: another company's row is never read, so never
#: locked, and always answers not found (12 Oct 2026, review R2). A real delete adds
#: ``_LOCK``: nothing can then link to it, block it or rename it while the plan is counted and
#: applied. A dry run only reads - its figures are re-counted under the lock on apply.
_VENDOR = """
    SELECT v.id::text AS id, to_jsonb(v)->>'vendor_name' AS vendor_name,
           to_jsonb(v)->>'vendor_code' AS vendor_code, to_jsonb(v)->>'trade' AS trade,
           to_jsonb(v)->>'block_state' AS block_state, to_jsonb(v)->>'status' AS status,
           to_jsonb(v)->>'organization_id' AS organization_id
      FROM plenum_cafm.vendors v WHERE v.id::text = :vid
       AND lower(to_jsonb(v)->>'organization_id') = lower(:org)"""
_LOCK = "\n       FOR UPDATE"

_USER = """
    SELECT to_jsonb(u)->>'full_name' AS name, to_jsonb(u)->>'email' AS email,
           to_jsonb(u)->>'role' AS role
      FROM plenum_cafm.users u WHERE u.id::text = :uid LIMIT 1"""


async def read_schema(session: Any) -> Schema:
    """plenum_cafm's base tables and columns, as they are now, and the keys onto vendors. Read
    every call, never cached: this decides what destructive statements match on."""
    cols: dict[str, dict[str, Col]] = {}
    for table, column, data_type, nullable, size in (await session.execute(text(_COLUMNS))).all():
        cols.setdefault(str(table), {})[str(column)] = Col(
            str(data_type), str(nullable).upper() == "YES", int(size) if size is not None else None)
    fks = tuple(Fk(str(t), str(c), str(p)) for t, c, p in (await session.execute(text(_FKS))).all())
    return _clean(Schema(cols, fks))


def _vendor_id(raw: Any) -> str:
    vid = "" if raw is None else str(raw)
    if not vid or len(vid) > ID_MAX or _BAD_ID.search(vid):
        raise VendorDeleteBadId(f"{vid[:40]!r} is not a vendor id")
    return vid


def normalise(s: Any) -> str:
    """What the admin typed, as it is compared: one space between words and one encoding of
    each accent, so "Café  Ltd" typed on a Mac matches the name on record. Case still counts."""
    return unicodedata.normalize("NFC", " ".join(str(s or "").split()))


def _lock_timeout(exc: BaseException) -> bool:
    """lock_timeout expired (SQLSTATE 55P03), however the driver wrapped it."""
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if "55P03" in (getattr(e, "sqlstate", None), getattr(e, "pgcode", None)):
            return True
        if "lock timeout" in str(e).lower():
            return True
        e = getattr(e, "orig", None) or e.__cause__
    return False


def _failed(step: Step, exc: BaseException) -> VendorDeleteFailed:
    error = str(exc).split("\n")[0][:300]
    log.warning("vendor_delete.step_failed", step=step.key, group=step.group, error=error)
    return VendorDeleteFailed(step.key, step.label, error)


async def actor_snapshot(session: Any, user_id: Any, role: str | None) -> dict[str, Any]:
    """Who deleted it, as they are now: the trail entry's ``by``. Their name and email are a
    courtesy to the reader of the trail: a read that fails leaves them empty, and never stops
    the delete. ``role`` is the caller's; without one, the user's company role. A data reset
    takes its snapshot here too (12 Oct 2026)."""
    by = {"id": str(user_id) if user_id else None, "name": None, "email": None, "role": role}
    if not user_id:
        return by
    try:
        async with session.begin_nested():
            row = (await session.execute(text(_USER), {"uid": str(user_id)})).mappings().first()
        if row:
            by.update(name=row.get("name"), email=row.get("email"),
                      role=role or row.get("role"))
    except Exception as exc:  # noqa: BLE001 — see docstring
        log.warning("vendor_delete.actor_unreadable", error=str(exc)[:200])
    return by


async def run(session: Any, *, organization_id: Any, vendor_id: Any, apply: bool,
              user_id: Any = None, role: str | None = None, confirm: str | None = None,
              expect: str | None = None, reason: str | None = None) -> dict[str, Any]:
    """Plan - and with ``apply``, carry out - the delete of one of the company's vendors.

    A dry run counts inside a transaction that is rolled back, so its figures are the ones a
    real delete would act on now. A real one needs ``confirm`` (the vendor's name as shown in
    ``confirm_with``) and ``expect`` (the mode the admin was shown); either one off refuses
    before anything is written. Every refusal is a typed exception and leaves nothing changed."""
    vid = _vendor_id(vendor_id)
    org = str(organization_id)
    try:
        return await _run(session, org, vid, apply=apply, user_id=user_id, role=role,
                          confirm=confirm, expect=expect, reason=reason)
    except VendorPlanChanged as exc:
        await session.rollback()
        if exc.plan is None:
            exc.plan = await _fresh_plan(session, org, vid)
        raise
    except Exception:
        await session.rollback()
        raise


async def _fresh_plan(session: Any, org: str, vid: str) -> dict[str, Any] | None:
    """The plan as it stands now, for an admin whose view went stale."""
    try:
        return await run(session, organization_id=org, vendor_id=vid, apply=False)
    except Exception as exc:  # noqa: BLE001 — the refusal stands; only the fresh view is lost
        log.warning("vendor_delete.replan_failed", vendor_id=vid, error=str(exc)[:200])
        return None


async def _run(session: Any, org: str, vid: str, *, apply: bool, user_id: Any, role: str | None,
               confirm: str | None, expect: str | None, reason: str | None) -> dict[str, Any]:
    await session.execute(text("SET LOCAL lock_timeout = '5s'"))
    await session.execute(text("SET LOCAL statement_timeout = '120s'"))
    try:
        row = (await session.execute(text(_VENDOR + (_LOCK if apply else "")),
                                     {"vid": vid, "org": org})).mappings().first()
    except Exception as exc:
        if _lock_timeout(exc):
            raise VendorBusy("the vendor is being changed by someone else") from exc
        raise
    # Another company's vendor, and one with no company at all, are answered exactly like a
    # vendor that does not exist.
    if row is None or str(row.get("organization_id") or "").lower() != org.lower():
        raise VendorNotFound(vid)
    v = dict(row)
    name = str(v.get("vendor_name") or "").strip()
    vendor = {"id": v["id"], "name": name or None, "code": v.get("vendor_code"),
              "trade": v.get("trade"), "block_state": v.get("block_state")}
    confirm_with = normalise(name) or normalise(v.get("vendor_code")) or str(v["id"])
    base = {"ok": True, "applied": False, "vendor": vendor, "confirm_with": confirm_with}

    if str(v.get("status") or "").strip().lower() == DELETED_STATUS:
        await session.rollback()
        return {**base, "already": True, "mode": "retire", "groups": {g: [] for g in GROUPS},
                "unavailable": []}
    if apply and normalise(confirm) != confirm_with:
        raise VendorConfirmMismatch(confirm_with)

    plan = plan_delete(await read_schema(session))
    params = binds(v, organization_id=org, user_id=user_id, role=role, reason=reason,
                   name_max=plan.name_max, at=datetime.now(timezone.utc).isoformat())

    async def count(s: Step) -> int:
        try:
            return int((await session.execute(text(s.count_sql), params)).scalar() or 0)
        except Exception as exc:
            raise _failed(s, exc) from exc

    found: dict[str, list[dict[str, Any]]] = {g: [] for g in GROUPS}
    for s in plan.references:
        n = await count(s)
        if n:
            found[s.group].append({"key": s.key, "label": s.label, "rows": n})
    mode = "retire" if found["kept"] or found["other"] else "remove"
    if apply and expect != mode:
        raise VendorPlanChanged()
    if mode == "retire" and plan.retire_sql is None:
        raise VendorDeleteFailed("vendor", "the vendor record",
                                 "this database keeps no vendor status, so it cannot be retired")

    listed: dict[str, list[Any]] = {"assets": [], "work_orders": [], "approvals": []}
    for s in plan.writes:
        if s.retire_only and mode != "retire":
            continue
        try:
            # The rows the trail names, read before the write that stops them matching.
            items = ([dict(r) for r in (await session.execute(text(s.items_sql), params))
                      .mappings().all()] if s.items_sql else [])
            if apply:
                if s.before_sql:
                    await session.execute(text(s.before_sql), params)
                n = (await session.execute(text(s.apply_sql), params)).rowcount or 0
            else:
                n = (await session.execute(text(s.count_sql), params)).scalar() or 0
        except Exception as exc:
            raise _failed(s, exc) from exc
        if n and int(n) > 0:
            entry: dict[str, Any] = {"key": s.key, "label": s.label, "rows": int(n)}
            if items:
                entry["items"] = items[:_PLAN_ITEMS]
            found[s.group].append(entry)
        if s.key in ("assets", "work_orders"):
            listed[s.key] = items[:_EVENT_ITEMS]
        elif s.key == "approvals_queue_items":
            listed["approvals"] = [r.get("id") for r in items][:_EVENT_ITEMS]

    out = {**base, "applied": apply, "mode": mode, "groups": found,
           "unavailable": plan.unavailable}
    if not apply:
        await session.rollback()
        return out

    step = Step("vendor", "vendor", "the vendor record", "vendors", "")
    try:
        n = (await session.execute(
            text(plan.remove_sql if mode == "remove" else plan.retire_sql), params)).rowcount
    except Exception as exc:
        raise _failed(step, exc) from exc
    if n != 1:
        raise VendorDeleteFailed("vendor", step.label, "the vendor's row was not changed")
    if mode == "remove":
        # Anything that came to name the vendor since it was counted would now point at nothing.
        for s in plan.references:
            if await count(s):
                raise VendorPlanChanged()

    detail = {
        "vendor": vendor, "mode": mode, "reason": clean_reason(reason),
        "by": await actor_snapshot(session, user_id, role),
        "effects": {g: {e["key"]: e["rows"] for e in found[g]} for g in GROUPS},
        "assets": listed["assets"], "work_orders": listed["work_orders"],
        "approvals": listed["approvals"], "source": "person",
        # What could not be checked here, so the trail says what the delete did not see.
        "unavailable": [u.get("label") or u.get("key") for u in plan.unavailable],
    }
    try:
        event = await write_audit(
            session, actor=str(user_id) if user_id else "hoistra-ui",
            action_type=vendor_audit.DELETED, source_feature=PLATFORM_FEATURE,
            organization_id=UUID(org), detail=detail)
    except Exception as exc:  # noqa: BLE001 — a delete nobody can trace must never happen
        log.error("vendor_delete.not_recorded", vendor_id=vid, error=str(exc)[:200])
        raise VendorNotRecorded(str(exc).split("\n")[0][:300]) from exc
    try:
        await session.commit()
    except Exception as exc:
        raise _failed(Step("vendor", "commit", "saving the changes", "", ""), exc) from exc
    log.info("vendor_delete.done", vendor_id=vid, organization_id=org, mode=mode,
             audit_id=str(event.id))
    return {**out, "audit_id": str(event.id)}
