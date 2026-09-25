"""Emptying the data behind one company's Compliance, Contracts, Assets, Energy and
Maintenance pages, so a fresh ingest can be judged on its own.

The API form of ``db/tools/clear_asset_energy_maintenance.py``, narrowed twice. The script
empties whole tables, which is only right on a test database holding one tenant; this deletes
only the rows that belong to ONE organization. And it deletes page data only: an
administrator picks any of the five areas below, and nothing outside them is touched -
uploaded documents, users, buildings, sites, floors and the rest of the approvals queue stay.

Which rows are a company's is decided by :mod:`org_export`, the rules the "export everything
we hold" download already uses, so a reset and an export can never disagree about whose rows
are whose.

**The areas point at each other.** Work orders name an asset and a vendor; certificates name
an asset; a meter sits on an asset or a section. Clearing one area while keeping another is
handled by the database's own foreign keys, read live:

  - a kept row whose link to a deleted row may be empty has that link cleared (a certificate
    kept while Assets is cleared stays, naming no asset);
  - a kept row whose link may NOT be empty blocks the reset (a maintenance plan cannot exist
    without its asset), and the reset refuses, saying which rows block it, rather than
    deleting data the administrator asked to keep.

Links the schema never declared as foreign keys (a meter's asset, a certificate's vendor) are
cleared the same way where the column allows it, from the short list in ``_HINTS``.

**The company's buildings are read once, first.** The export finds a building through the
company's rows that point at it; deleting those rows as it goes would shrink that set mid-run.
So the ids are resolved before anything changes and bound as a fixed list. A row with no
organization set that sits on one of those buildings is the company's too.

One transaction: every delete waits at most 5 s for a lock, and if anything refuses, nothing
has changed. Every identifier formatted into SQL comes from the introspected schema or the
literals here and must match ``_SAFE``; ids are always bound parameters.
"""
from __future__ import annotations

import re
from typing import Any, NamedTuple

from . import org_export

_SAFE = re.compile(r"^[a-z_][a-z0-9_]*$")


class Area(NamedTuple):
    label: str
    #: Deleted children before parents. A table missing from the schema is skipped.
    tables: tuple[str, ...]
    #: Approval queue items this area raises: exact item types, or prefixes ending in "%".
    approvals: tuple[str, ...] = ()


#: The five pages, in the order they are cleared. Maintenance goes first because its rows
#: point at assets and vendors; assets last because nearly everything points at them.
AREAS: dict[str, Area] = {
    "maintenance": Area("Maintenance", (
        "work_order_tasks", "work_order_parts", "work_order_comments", "work_order_history",
        "work_order_attachments", "work_order_assets", "work_order_users",
        "work_order_resources", "misc_costs",
        "vendor_wo_scores", "maintenance_history", "work_orders",
        "ppm_visits", "scheduled_tasks", "scheduled_maintenance_assets",
        "scheduled_maintenance_parts", "maintenance_plans", "inspections",
        "inventory_transactions", "bom_group_parts", "receipt_line_items",
        "purchase_order_line_items", "receipts", "purchase_orders",
        "spare_parts", "technicians", "resources",
    ), ("work_order_%", "booking_request")),
    "energy": Area("Energy", (
        "meter_readings", "energy_anomalies", "eui_snapshots",
        "building_energy_profiles", "energy_ratings", "energy_meters", "meters",
    ), ("energy_%", "meter_reading_%")),
    "compliance": Area("Compliance", (
        "compliance_certificates", "compliance_risk_snapshots", "compliance_scan_runs",
    ), ("certificate_%", "verification_human", "forgery_alert", "remedial")),
    "contracts": Area("Contracts", (
        "invoice_verifications", "invoice_lines", "invoices",
        "vendor_monthly_scorecards",
        "contract_sla_parameters", "vendor_contacts", "vendor_contracts",
        "sla_policies", "vendors",
    ), ("contract_%", "overlapping_contracts_tie", "invoice_flag%", "vendor_risk",
        "vendor_email", "cost_variance_alert")),
    "assets": Area("Assets", (
        "asset_readings", "asset_documents", "asset_offline_log", "asset_warranties",
        "assets", "asset_reading_bands", "building_sections",
    ), ("asset_%",)),
}

#: Never deleted by this, whatever a rule would say.
KEPT = ("organizations", "users", "buildings", "sites", "locations", "floors",
        "documents", "ingestion_documents")

#: Links the schema never declared, cleared like a nullable foreign key when their target
#: goes: (table, column, target table).
_HINTS: tuple[tuple[str, str, str], ...] = (
    ("energy_meters", "asset_id", "assets"),
    ("energy_meters", "section_id", "building_sections"),
    ("compliance_certificates", "vendor_id", "vendors"),
    ("compliance_certificates", "asset_id", "assets"),
    ("assets", "vendor_id", "vendors"),
    ("assets", "section_id", "building_sections"),
    ("work_orders", "vendor_id", "vendors"),
    ("ppm_visits", "asset_id", "assets"),
    ("inspections", "asset_id", "assets"),
)

#: Primary key of a table, where it is not `id`.
_KEYS = {"building_sections": "section_id", "invoices": "invoice_id", "meters": "meter_id"}

_BIDS = "building_id::text = ANY(CAST(:bids AS text[]))"
_QUEUE = ("ops_email_log", "approval_action_tokens", "approvals_queue_items")


class Fk(NamedTuple):
    child: str
    column: str
    parent: str
    parent_column: str
    not_null: bool


class Step(NamedTuple):
    area: str
    table: str
    where: str        # binds :org and/or :bids
    scoped_by: str


class Detach(NamedTuple):
    table: str
    column: str
    target: str
    sql: str          # UPDATE ... SET column = NULL WHERE column IN (the target rows going)


class Block(NamedTuple):
    table: str
    column: str
    target: str
    count_sql: str


class Plan(NamedTuple):
    steps: list[Step]
    detaches: list[Detach]
    blocks: list[Block]
    skipped: dict[str, str]


def parse_areas(areas: list[str] | None) -> list[str]:
    """The chosen areas in clearing order. None or empty means all five."""
    if not areas:
        return list(AREAS)
    wanted = {str(a).strip().lower() for a in areas if str(a).strip()}
    unknown = sorted(wanted - set(AREAS))
    if unknown:
        raise ValueError(f"unknown area(s): {', '.join(unknown)}; choose from {', '.join(AREAS)}")
    return [a for a in AREAS if a in wanted]


def buildings_query(shape: dict[str, set[str]]) -> str:
    """SQL returning the company's building ids as text, binding :org."""
    parts: list[str] = []
    bcols = shape.get("buildings", set())
    org = next((c for c in ("organization_id", "org_id") if c in bcols), None)
    if org and "building_id" in bcols:
        parts.append(f"SELECT building_id::text AS b FROM plenum_cafm.buildings WHERE {org}::text = :org")
    derived = org_export._buildings_clause(shape)
    if derived:
        parts.append(f"SELECT building_id::text AS b FROM plenum_cafm.buildings WHERE {derived}")
    if not parts:
        return ""
    return "SELECT DISTINCT b FROM (" + " UNION ".join(parts) + ") AS org_buildings WHERE b IS NOT NULL"


def _approvals_where(patterns: tuple[str, ...]) -> str:
    """Literals from AREAS only, never from a request."""
    ors = []
    for p in patterns:
        if not re.match(r"^[a-z_%]+$", p):
            raise ValueError(f"bad approval pattern {p!r}")
        ors.append(f"item_type LIKE '{p}'" if "%" in p else f"item_type = '{p}'")
    return "(" + " OR ".join(ors) + ")"


def plan_reset(
    shape: dict[str, set[str]],
    areas: list[str] | None = None,
    fks: list[Fk] | tuple[Fk, ...] = (),
    nullable: dict[str, set[str]] | None = None,
) -> Plan:
    """The ordered deletes for one organization's chosen areas, the links to clear first, the
    kept rows that would block it, and why any listed table is not deleted."""
    chosen = parse_areas(areas)
    included, excluded = org_export.plan_export(shape)
    derived = org_export._buildings_clause(shape)
    nullable = nullable or {}

    def fixed(where: str) -> str:
        return where.replace(derived, _BIDS) if derived else where

    steps: list[Step] = []
    skipped: dict[str, str] = {}
    for area in chosen:
        for table in AREAS[area].tables:
            if table not in shape:
                continue
            if table in KEPT or not _SAFE.match(table):
                skipped[table] = "Kept."
                continue
            rule = included.get(table)
            if rule is None:
                skipped[table] = excluded.get(
                    table, "No organization link found, so its rows cannot be told apart by company.")
                continue
            where = fixed(rule.where)
            if rule.kind == "direct" and "building_id" in shape[table] and derived:
                where = f"({where} OR ({rule.column} IS NULL AND {_BIDS}))"
            scoped = rule.kind if rule.kind != "parent" else f"parent: {rule.parent}"
            steps.append(Step(area, table, where, scoped))
        # The approval items this area raised, with the tokens and emails that hang off them.
        patterns = AREAS[area].approvals
        q = shape.get("approvals_queue_items", set())
        if patterns and {"id", "item_type"} <= q:
            org = next((c for c in ("organization_id", "org_id") if c in q), None)
            if org:
                items = f"{org}::text = :org AND {_approvals_where(patterns)}"
                sub = f"SELECT id::text FROM plenum_cafm.approvals_queue_items WHERE {items}"
                for child in ("ops_email_log", "approval_action_tokens"):
                    if "queue_item_id" in shape.get(child, set()):
                        steps.append(Step(area, child, f"queue_item_id::text IN ({sub})",
                                          "parent: approvals_queue_items"))
                steps.append(Step(area, "approvals_queue_items", items, "direct: raised by this area"))

    steps = _children_first(steps)
    deleted = {s.table for s in steps if s.table not in _QUEUE}
    where_of = {s.table: s.where for s in steps}

    detaches: list[Detach] = []
    blocks: list[Block] = []
    seen: set[tuple[str, str]] = set()
    links = [(f.child, f.column, f.parent, f.parent_column, f.not_null, True) for f in fks]
    links += [(c, col, p, _KEYS.get(p, "id"), col not in nullable.get(c, {col}), False)
              for c, col, p in _HINTS]
    for child, col, parent, pcol, not_null, declared in links:
        if parent not in deleted or child in deleted or child in _QUEUE:
            continue
        if child not in shape or col not in shape[child] or pcol not in shape.get(parent, set()):
            continue
        if not all(_SAFE.match(x) for x in (child, col, parent, pcol)):
            continue
        if (child, col) in seen:
            continue
        seen.add((child, col))
        going = f"SELECT {pcol}::text FROM plenum_cafm.{parent} WHERE {where_of[parent]}"
        if not_null:
            if declared:  # an undeclared NOT NULL link cannot make a delete fail; leave it
                blocks.append(Block(child, col, parent,
                                    f"SELECT count(*) FROM plenum_cafm.{child} WHERE {col}::text IN ({going})"))
            continue
        detaches.append(Detach(child, col, parent,
                               f"UPDATE plenum_cafm.{child} SET {col} = NULL WHERE {col}::text IN ({going})"))
    return Plan(steps, detaches, blocks, skipped)


def _children_first(steps: list[Step]) -> list[Step]:
    """Move any step scoped through a parent to before that parent. Scoped by company, a
    child is matched against its parent's rows, so the parent must still be there."""
    out = list(steps)
    moved = True
    while moved:
        moved = False
        pos = {s.table: i for i, s in enumerate(out)}
        for i, s in enumerate(out):
            if not s.scoped_by.startswith("parent: "):
                continue
            j = pos.get(s.scoped_by.split(": ", 1)[1])
            if j is not None and j < i:
                out.insert(j, out.pop(i))
                moved = True
                break
    return out


async def read_schema(session: Any) -> tuple[dict[str, set[str]], dict[str, set[str]], list[Fk]]:
    """plenum_cafm's base tables and columns, which columns may be empty, and its foreign keys."""
    from sqlalchemy import text

    rows = (await session.execute(text(
        """SELECT c.table_name, c.column_name, c.is_nullable
             FROM information_schema.columns c
             JOIN information_schema.tables t
               ON t.table_schema = c.table_schema AND t.table_name = c.table_name
            WHERE c.table_schema = 'plenum_cafm' AND t.table_type = 'BASE TABLE'"""
    ))).all()
    shape: dict[str, set[str]] = {}
    nullable: dict[str, set[str]] = {}
    for table, column, is_nullable in rows:
        shape.setdefault(str(table), set()).add(str(column))
        if str(is_nullable).upper() == "YES":
            nullable.setdefault(str(table), set()).add(str(column))
    fk_rows = (await session.execute(text(
        """SELECT cl.relname, a.attname, pc.relname, pa.attname, a.attnotnull
             FROM pg_constraint k
             JOIN pg_class cl ON cl.oid = k.conrelid
             JOIN pg_class pc ON pc.oid = k.confrelid
             JOIN pg_namespace n ON n.oid = cl.relnamespace
             JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
             JOIN pg_attribute pa ON pa.attrelid = k.confrelid AND pa.attnum = k.confkey[1]
            WHERE k.contype = 'f' AND n.nspname = 'plenum_cafm'
              AND array_length(k.conkey, 1) = 1"""
    ))).all()
    fks = [Fk(str(c), str(col), str(p), str(pc), bool(nn)) for c, col, p, pc, nn in fk_rows]
    return shape, nullable, fks


class ResetFailed(RuntimeError):
    """A delete was refused. Nothing was changed: the whole reset is one transaction."""

    def __init__(self, table: str, error: str) -> None:
        super().__init__(f"{table}: {error}")
        self.table = table
        self.error = error


class ResetBlocked(RuntimeError):
    """Kept rows depend on rows the reset would delete. Nothing was changed."""

    def __init__(self, blocked: list[dict[str, Any]]) -> None:
        super().__init__("kept rows depend on rows this reset would delete")
        self.blocked = blocked


async def run_reset(session: Any, organization_id: str, *, areas: list[str] | None = None,
                    apply: bool) -> dict[str, Any]:
    """Count - and with ``apply``, delete - one organization's rows in the chosen areas.

    A dry run does the counting in a transaction that is rolled back, so its figures are the
    ones a real run would change. Kept rows that would block the reset are reported in the
    dry run and raise :class:`ResetBlocked` in a real one, before anything changes."""
    from sqlalchemy import text

    chosen = parse_areas(areas)
    shape, nullable, fks = await read_schema(session)
    plan = plan_reset(shape, chosen, fks, nullable)
    org = str(organization_id)
    total = 0
    try:
        await session.execute(text("SET LOCAL lock_timeout = '5s'"))
        await session.execute(text("SET LOCAL statement_timeout = '600s'"))
        bq = buildings_query(shape)
        bids = [r[0] for r in (await session.execute(text(bq), {"org": org})).all()] if bq else []
        params = {"org": org, "bids": bids}

        blocked = []
        for b in plan.blocks:
            n = (await session.execute(text(b.count_sql), params)).scalar() or 0
            if n:
                blocked.append({"table": b.table, "column": b.column, "rows": int(n),
                                "needs_area": next((a for a, ar in AREAS.items() if b.table in ar.tables), None),
                                "because": f"each needs the {b.target.replace('_', ' ')} row it names ({b.table}.{b.column}), which this reset deletes"})
        if blocked and apply:
            raise ResetBlocked(blocked)

        detached = []
        for d in plan.detaches:
            if apply:
                n = (await session.execute(text(d.sql), params)).rowcount or 0
            else:
                n = (await session.execute(text(d.sql.replace(
                    f"UPDATE plenum_cafm.{d.table} SET {d.column} = NULL WHERE",
                    f"SELECT count(*) FROM plenum_cafm.{d.table} WHERE", 1)), params)).scalar() or 0
            if n:
                detached.append({"table": d.table, "column": d.column, "rows": int(n),
                                 "because": f"its {d.target} is being cleared; the row is kept"})

        by_area: dict[str, list[dict[str, Any]]] = {}
        for step in plan.steps:
            try:
                if apply:
                    n = (await session.execute(
                        text(f"DELETE FROM plenum_cafm.{step.table} WHERE {step.where}"), params,
                    )).rowcount or 0
                else:
                    n = (await session.execute(
                        text(f"SELECT count(*) FROM plenum_cafm.{step.table} WHERE {step.where}"),
                        params)).scalar() or 0
            except Exception as exc:  # noqa: BLE001 - reported as the table that refused
                raise ResetFailed(step.table, str(exc).split("\n")[0][:300]) from exc
            if n:
                by_area.setdefault(step.area, []).append(
                    {"table": step.table, "rows": int(n), "scoped_by": step.scoped_by})
                total += int(n)
        if apply:
            await session.commit()
        else:
            await session.rollback()
    except Exception:
        await session.rollback()
        raise
    return {
        "organization_id": org,
        "applied": apply,
        "areas": [{"area": a, "label": AREAS[a].label,
                   "rows": sum(t["rows"] for t in by_area.get(a, [])),
                   "tables": by_area.get(a, [])} for a in chosen],
        "row_total": total,
        "buildings": len(bids),
        "links_cleared": detached,
        "blocked": blocked,
        "skipped": dict(sorted(plan.skipped.items())),
        "kept": list(KEPT),
        "available_areas": {a: ar.label for a, ar in AREAS.items()},
    }
