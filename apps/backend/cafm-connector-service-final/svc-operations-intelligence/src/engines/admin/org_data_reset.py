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

**Or one building.** Given a building, every delete keeps its company filter and adds that
building (bound as ``:building``, which must be one of the company's). A row is on a building
by its own ``building_id``, or — naming none — by the first link in ``_BUILDING_LINKS`` it has
set (a reading by its meter, a task by its work order). A table with neither path serves the
whole company and is kept, listed as ``company_wide`` — and ``COMPANY_WIDE`` is, whatever
columns it grows. The company's rows on other buildings that point at what goes are cleared
or block, as above, even when their own page is being cleared too; another company's rows
are left to their foreign keys, exactly as a company reset leaves them.

One transaction: every delete waits at most 5 s for a lock, and if anything refuses, nothing
has changed. Every identifier formatted into SQL comes from the introspected schema or the
literals here and must match ``_SAFE``; ids are always bound parameters.
"""
from __future__ import annotations

import re
import uuid
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
    # Not booking_request: a renewal booking names a certificate, so it goes with Compliance.
    # Listed here as well, a Maintenance-only reset deleted the bookings of certificates it
    # kept (review, 28 Sep 2026).
    ), ("work_order_%",)),
    "energy": Area("Energy", (
        "meter_readings", "energy_anomalies", "eui_snapshots",
        "building_energy_profiles", "energy_ratings", "energy_meters", "meters",
    ), ("energy_%", "meter_reading_%")),
    # An item goes with the area that deletes what it is about. The scan's lapse alerts,
    # renewal bookings and adversary flags name a certificate; its blocks, block lifts and
    # passport shares name a vendor, and vendors go with Contracts. Missing, they outlived a
    # reset and sat pending over an empty register (28 Sep 2026).
    "compliance": Area("Compliance", (
        "compliance_certificates", "compliance_risk_snapshots", "compliance_scan_runs",
    ), ("certificate_%", "verification_human", "forgery_alert", "remedial",
        "alert", "booking_request", "adversary_gate")),
    "contracts": Area("Contracts", (
        "invoice_verifications", "invoice_lines", "invoices",
        "vendor_monthly_scorecards",
        "contract_sla_parameters", "vendor_contacts", "vendor_contracts",
        "sla_policies", "vendors",
    ), ("contract_%", "overlapping_contracts_tie", "invoice_flag%", "vendor_risk",
        "vendor_email", "cost_variance_alert", "block_ack", "block_lift", "passport_share")),
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

#: Records every building of a company shares. A single-building reset keeps them even if a
#: building_id column appears on one (the migration engine and the table editor add columns):
#: deleting one building's vendors would clear every other building's work orders' links.
COMPANY_WIDE = frozenset({
    "vendors", "vendor_contacts", "vendor_contracts", "sla_policies", "vendor_monthly_scorecards",
    "contract_sla_parameters", "spare_parts", "technicians", "resources", "inventory_transactions",
    "bom_group_parts", "purchase_orders", "purchase_order_line_items", "receipts",
    "receipt_line_items",
})

#: Bulk tables no approval item is ever about; left out of the match so it stays cheap.
_NOT_SUBJECTS = ("meter_readings", "asset_readings")

#: How a row naming no building of its own reaches one: (its column, a table that is on a
#: building), tried in order — the first link the row has set decides. Anything not reachable
#: here or by its own building_id belongs to the whole company.
_WO = (("work_order_id", "work_orders"),)
_ASSET = (("asset_id", "assets"),)
_BUILDING_LINKS: dict[str, tuple[tuple[str, str], ...]] = {
    "work_orders": _ASSET,
    "work_order_tasks": _WO, "work_order_parts": _WO, "work_order_comments": _WO,
    "work_order_history": _WO, "work_order_attachments": _WO, "work_order_assets": _WO,
    "work_order_users": _WO, "work_order_resources": _WO, "misc_costs": _WO,
    "vendor_wo_scores": _WO + _ASSET,
    "maintenance_history": _ASSET + _WO,
    "ppm_visits": _ASSET + _WO,
    "maintenance_plans": _ASSET,
    "scheduled_tasks": _ASSET + (("maintenance_plan_id", "maintenance_plans"),),
    "scheduled_maintenance_assets": _ASSET,
    "scheduled_maintenance_parts": (("maintenance_plan_id", "maintenance_plans"),),
    "inspections": _ASSET,
    "meter_readings": (("meter_id", "energy_meters"),),
    "energy_anomalies": (("meter_id", "energy_meters"),) + _ASSET,
    "compliance_certificates": _ASSET,
    "invoice_lines": (("invoice_verification_id", "invoice_verifications"),),
    "asset_readings": _ASSET, "asset_documents": _ASSET, "asset_offline_log": _ASSET,
    "asset_warranties": _ASSET, "asset_reading_bands": _ASSET,
}

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
    elsewhere: bool = False   # rows of a table being cleared, kept because on another building


class Block(NamedTuple):
    table: str
    column: str
    target: str
    count_sql: str
    elsewhere: bool = False


class Plan(NamedTuple):
    steps: list[Step]
    detaches: list[Detach]
    blocks: list[Block]
    skipped: dict[str, str]
    #: Chosen-page tables a single-building reset keeps because they serve every building:
    #: table -> area.
    company_wide: dict[str, str]


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


def building_scopes(shape: dict[str, set[str]]) -> dict[str, tuple[str, set[str]]]:
    """Per table that can be on one building: a WHERE binding :building, and the tables that
    WHERE reads (which must still be there when it runs)."""
    memo: dict[str, tuple[str, set[str]] | None] = {}

    def resolve(table: str, path: frozenset[str]) -> tuple[str, set[str]] | None:
        if table in memo:
            return memo[table]
        cols = shape.get(table)
        if not cols or not _SAFE.match(table) or table in path or table in COMPANY_WIDE:
            return None
        parts: list[tuple[str, str, str]] = []   # (column, test, table it reads)
        if "building_id" in cols:
            parts.append(("building_id", "building_id::text = :building", ""))
        for col, parent in _BUILDING_LINKS.get(table, ()):
            key = _KEYS.get(parent, "id")
            if col not in cols or key not in shape.get(parent, set()) or not _SAFE.match(col):
                continue
            up = resolve(parent, path | {table})
            if up is None:
                continue
            parts.append((col, f"{col}::text IN (SELECT {key}::text FROM plenum_cafm.{parent} "
                               f"WHERE {up[0]})", parent))
        if not parts:
            memo[table] = None
            return None
        # The first link a row has set decides: each later test applies only when every
        # earlier column is empty, so a row filed on building B never goes with A by a link.
        tests, empty = [], []
        for col, test, _ in parts:
            tests.append("(" + " AND ".join(empty + [test]) + ")" if empty else test)
            empty.append(f"{col} IS NULL")
        where = tests[0] if len(tests) == 1 else "(" + " OR ".join(tests) + ")"
        memo[table] = (where, {p for _, _, p in parts if p})
        return memo[table]

    out: dict[str, tuple[str, set[str]]] = {}
    for table in sorted(shape):
        got = resolve(table, frozenset())
        if got is not None:
            out[table] = got
    return out


def plan_reset(
    shape: dict[str, set[str]],
    areas: list[str] | None = None,
    fks: list[Fk] | tuple[Fk, ...] = (),
    nullable: dict[str, set[str]] | None = None,
    *,
    building: bool = False,
) -> Plan:
    """The ordered deletes for one organization's chosen areas, the links to clear first, the
    kept rows that would block it, and why any listed table is not deleted. With
    ``building``, only the rows on the building bound as ``:building``."""
    chosen = parse_areas(areas)
    included, excluded = org_export.plan_export(shape)
    derived = org_export._buildings_clause(shape)
    nullable = nullable or {}
    on_building = building_scopes(shape) if building else {}

    def fixed(where: str) -> str:
        return where.replace(derived, _BIDS) if derived else where

    steps: list[Step] = []
    skipped: dict[str, str] = {}
    company_wide: dict[str, str] = {}
    reads: dict[str, set[str]] = {}
    company_where: dict[str, str] = {}   # each step's company filter, before the building's
    front: list[Step] = []   # a building's approval items, read before anything goes
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
            if building:
                scope = on_building.get(table)
                if scope is None:
                    company_wide[table] = area
                    continue
                company_where[table] = where
                where = f"({where}) AND ({scope[0]})"
                reads[table] = scope[1]
            scoped = rule.kind if rule.kind != "parent" else f"parent: {rule.parent}"
            steps.append(Step(area, table, where, scoped))
        # The approval items this area raised, with the tokens and emails that hang off them.
        # One building's are placed once every page's rows are known, below.
        if not building:
            _approvals(area, shape, steps)
    if building:
        # One building's items are the ones about a row this reset deletes, on any page being
        # cleared. Without a link to such a row, an item cannot be placed on a building, so it
        # stays. Read first, while those rows are still there to be matched against.
        about = [f"SELECT {_KEYS.get(s.table, 'id')}::text FROM plenum_cafm.{s.table} "
                 f"WHERE {s.where}" for s in steps
                 if s.table not in _NOT_SUBJECTS and _KEYS.get(s.table, "id") in shape[s.table]]
        if about and "related_entity_id" in shape.get("approvals_queue_items", set()):
            for area in chosen:
                _approvals(area, shape, front,
                           f"related_entity_id::text IN ({' UNION '.join(about)})")

    steps = _children_first(front + steps, reads)
    deleted = {s.table for s in steps if s.table not in _QUEUE}
    where_of = {s.table: s.where for s in steps}

    detaches: list[Detach] = []
    blocks: list[Block] = []
    seen: set[tuple[str, str]] = set()
    links = [(f.child, f.column, f.parent, f.parent_column, f.not_null, True) for f in fks]
    links += [(c, col, p, _KEYS.get(p, "id"), col not in nullable.get(c, {col}), False)
              for c, col, p in _HINTS]
    for child, col, parent, pcol, not_null, declared in links:
        if parent not in deleted or child in _QUEUE:
            continue
        # A company reset takes every row of a cleared table. One building's leaves the rows
        # on other buildings, and they may point at what goes.
        elsewhere = child in deleted
        if elsewhere and not building:
            continue
        if child not in shape or col not in shape[child] or pcol not in shape.get(parent, set()):
            continue
        if not all(_SAFE.match(x) for x in (child, col, parent, pcol)):
            continue
        if (child, col) in seen:
            continue
        seen.add((child, col))
        going = f"SELECT {pcol}::text FROM plenum_cafm.{parent} WHERE {where_of[parent]}"
        # Elsewhere means the company's own rows that this reset keeps; another company's
        # rows are left to the foreign key, which refuses the reset as a company reset does.
        kept = (f" AND ({company_where[child]}) AND ({where_of[child]}) IS NOT TRUE"
                if elsewhere else "")
        rule = included.get(child)
        if elsewhere and rule is not None and rule.kind == "parent" and rule.column == col:
            # The row is the company's only through this link: cleared, it would be nobody's,
            # out of reach of any later reset or export. It blocks instead.
            blocks.append(Block(child, col, parent,
                                f"SELECT count(*) FROM plenum_cafm.{child} "
                                f"WHERE {col}::text IN ({going}){kept}", elsewhere))
            continue
        if not_null:
            if declared:  # an undeclared NOT NULL link cannot make a delete fail; leave it
                blocks.append(Block(child, col, parent,
                                    f"SELECT count(*) FROM plenum_cafm.{child} "
                                    f"WHERE {col}::text IN ({going}){kept}", elsewhere))
            continue
        detaches.append(Detach(child, col, parent,
                               f"UPDATE plenum_cafm.{child} SET {col} = NULL "
                               f"WHERE {col}::text IN ({going}){kept}", elsewhere))
    return Plan(steps, detaches, blocks, skipped, company_wide)


def _approvals(area: str, shape: dict[str, set[str]], into: list[Step], about: str = "") -> None:
    """The approval items ``area`` raised (narrowed by ``about`` when given), with the tokens
    and emails that hang off them, appended to ``into`` children first."""
    patterns = AREAS[area].approvals
    q = shape.get("approvals_queue_items", set())
    if not patterns or not {"id", "item_type"} <= q:
        return
    org = next((c for c in ("organization_id", "org_id") if c in q), None)
    if not org:
        return
    items = f"{org}::text = :org AND {_approvals_where(patterns)}"
    if about:
        items += f" AND {about}"
    sub = f"SELECT id::text FROM plenum_cafm.approvals_queue_items WHERE {items}"
    for child in ("ops_email_log", "approval_action_tokens"):
        if "queue_item_id" in shape.get(child, set()):
            into.append(Step(area, child, f"queue_item_id::text IN ({sub})",
                             "parent: approvals_queue_items"))
    into.append(Step(area, "approvals_queue_items", items, "direct: about rows this reset deletes"
                     if about else "direct: raised by this area"))


def _children_first(steps: list[Step], reads: dict[str, set[str]] | None = None) -> list[Step]:
    """Move any step matched against another table's rows to before that table's delete: a
    child scoped through its parent, or a row placed on a building through what it hangs
    off. Its WHERE reads those rows, so they must still be there."""
    reads = reads or {}
    out = list(steps)
    for _ in range(len(out) ** 2 + 1):
        pos = {s.table: i for i, s in enumerate(out)}
        for i, s in enumerate(out):
            needs = set(reads.get(s.table, ()))
            if s.scoped_by.startswith("parent: "):
                needs.add(s.scoped_by.split(": ", 1)[1])
            j = min((pos[p] for p in needs if p in pos and p != s.table), default=None)
            if j is not None and j < i:
                out.insert(j, out.pop(i))
                break
        else:
            return out
    raise ValueError("reset steps read each other in a cycle")


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


class ResetForeignBuilding(ValueError):
    """The building named is not one of this company's. Nothing was changed."""


class ResetBlocked(RuntimeError):
    """Kept rows depend on rows the reset would delete. Nothing was changed."""

    def __init__(self, blocked: list[dict[str, Any]]) -> None:
        super().__init__("kept rows depend on rows this reset would delete")
        self.blocked = blocked


async def run_reset(session: Any, organization_id: str, *, areas: list[str] | None = None,
                    apply: bool, building_id: str | None = None) -> dict[str, Any]:
    """Count - and with ``apply``, delete - one organization's rows in the chosen areas, or
    only those on ``building_id``.

    A dry run does the counting in a transaction that is rolled back, so its figures are the
    ones a real run would change. Kept rows that would block the reset are reported in the
    dry run and raise :class:`ResetBlocked` in a real one, before anything changes. A building
    that is not the company's raises :class:`ResetForeignBuilding`, also before anything."""
    from sqlalchemy import text

    chosen = parse_areas(areas)
    building: str | None = None
    if building_id is not None:
        # Fail closed: an empty or malformed id is refused, never read as "every building".
        try:
            building = str(uuid.UUID(str(building_id)))
        except ValueError as exc:
            raise ResetForeignBuilding(f"{building_id!r} is not a building id") from exc
    shape, nullable, fks = await read_schema(session)
    plan = plan_reset(shape, chosen, fks, nullable, building=building is not None)
    org = str(organization_id)
    total = 0
    try:
        await session.execute(text("SET LOCAL lock_timeout = '5s'"))
        await session.execute(text("SET LOCAL statement_timeout = '600s'"))
        bq = buildings_query(shape)
        bids = [r[0] for r in (await session.execute(text(bq), {"org": org})).all()] if bq else []
        if building is not None and building not in bids:
            raise ResetForeignBuilding(f"building {building} is not one of this company's")
        params = {"org": org, "bids": bids, "building": building}

        blocked = []
        for b in plan.blocks:
            n = (await session.execute(text(b.count_sql), params)).scalar() or 0
            if n:
                target = b.target.replace("_", " ")
                needs = (f"needs the {target} row it names ({b.table}.{b.column}), "
                         "which this reset deletes")
                blocked.append({"table": b.table, "column": b.column, "rows": int(n),
                                # Already being cleared here: adding the page cannot help.
                                "needs_area": None if b.elsewhere else next(
                                    (a for a, ar in AREAS.items() if b.table in ar.tables), None),
                                "because": ("each is on another building and " if b.elsewhere
                                            else "each ") + needs})
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
                why = f"its {d.target} is being cleared; the row is kept"
                detached.append({"table": d.table, "column": d.column, "rows": int(n),
                                 "because": ("it is on another building and " + why
                                             if d.elsewhere else why)})

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
        "building_id": building,
        "buildings": 1 if building is not None else len(bids),
        "company_wide": [{"table": t, "area": a, "label": AREAS[a].label}
                         for t, a in plan.company_wide.items()],
        "links_cleared": detached,
        "blocked": blocked,
        "skipped": dict(sorted(plan.skipped.items())),
        "kept": list(KEPT),
        "available_areas": {a: ar.label for a, ar in AREAS.items()},
    }
