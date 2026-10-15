"""One building, and everything hanging off it, in the shape of the graph itself.

    buildings
      ├──< floors ──< spaces
      ├──< assets ──< equipment / meters
      ├──< documents ──< certificates
      └──< contracts ──< work_orders / invoices

The building row already carries ``graph_counts``, which answers "how many". This answers
"which", and it is a separate call on purpose: a table of forty buildings does not need four
hundred child rows to draw, and a person opening one building does.

Three distinctions the caller cannot make for itself, so they are made here.

**Empty is not the same as unavailable.** A branch with nothing in it is a fact about the
building — "no meter on record" explains why its EUI is a recorded figure rather than a
reading. A branch this database cannot join at all is a fact about the deployment. Rendering
both as a blank tells the reader neither, and the drawer that consumes this had exactly that
bug before it was given something better to read.

**Truncated is not complete.** Each branch reports its own total beside the rows returned, so
twenty-five rows out of two hundred says so rather than looking like the whole list.

**Introspected, not assumed.** Column names differ between deployments and the graph is
migrated in stages, so every branch is read from what the table actually has.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from .building_rollup import graph_shape

log = get_logger(__name__)

#: Rows returned per branch. Beyond this the count still tells the truth, and a caller that
#: wants the rest can ask for the branch on its own.
BRANCH_LIMIT = 25

#: What each branch is called, and what its emptiness means. The second half is the point:
#: an empty branch that explains itself is a finding, and one that does not is a blank.
BRANCH_META: dict[str, dict[str, str]] = {
    "floors": {"label": "Floors",
               "empty": "No floors on record — the floor count on the row is surveyed, not counted."},
    "spaces": {"label": "Spaces",
               "empty": "No spaces recorded, so the floor area cannot be counted from them."},
    "assets": {"label": "Assets",
               "empty": "No plant recorded here. A work order raised against this building "
                        "has no asset to inherit its location from."},
    "equipment": {"label": "Equipment", "empty": "No equipment rows."},
    "meters": {"label": "Meters",
               "empty": "No meter on record — any EUI shown is a recorded figure, not a reading."},
    "documents": {"label": "Documents", "empty": "Nothing filed against this building yet."},
    "compliance_certificates": {"label": "Certificates",
                                "empty": "No certificates filed — this building contributes "
                                         "nothing to compliance coverage."},
    "work_orders": {"label": "Work orders", "empty": "No work orders raised here."},
    "contracts": {"label": "Contracts", "empty": "No contract covers this building."},
    "invoices": {"label": "Invoices", "empty": "Nothing billed against this building."},
}

#: Per branch: candidate columns for a readable label, and for the detail line beside it.
#: First match wins, so a deployment missing one spelling still renders.
_BRANCHES: dict[str, dict[str, Any]] = {
    "floors": {"label": ("name", "level"), "detail": ("level", "gross_area_sqft")},
    "spaces": {"label": ("name",), "detail": ("space_type", "gross_area_sqft")},
    "assets": {"label": ("asset_name", "name", "asset_code"), "detail": ("asset_code", "status")},
    "equipment": {"label": ("name", "equipment_code"), "detail": ("equipment_code",)},
    # mpan_mprn is the canonical column: one field holding either number, because a meter
    # has an MPAN (electricity) or an MPRN (gas) and never both. It was missing from this
    # list, so on the canonical shape no candidate matched, label_expr fell to NULL and
    # every meter rendered as its raw uuid — the one identifier a person reading the drawer
    # cannot use. The separate mpan/mprn spellings stay for deployments that have them.
    "meters": {"label": ("meter_ref", "name", "mpan_mprn", "mpan", "mprn"),
               "detail": ("meter_type", "unit")},
    # file_name first: a document is identified by the file it is. `title` here is the
    # certificate type code, which every document of that type shares — so with title
    # leading, five real PDFs with distinct filenames all rendered as "EICR" and the panel
    # had nothing left to tell them apart but a truncated uuid.
    "documents": {"label": ("file_name", "title"), "detail": ("doc_type",)},
    # doc_ref: the document this row was read from. Two things are built on it — a real
    # link, which needs the id, and the answer to "is there a document here at all", which
    # a certificate with no document_id answers by its absence. A certificate like that has
    # nothing to open because nothing was ever filed against it: a gap in the register,
    # not a gap in storage, and the two read identically until the row says which.
    "compliance_certificates": {"label": ("certificate_type_code", "certificate_number"),
                                "detail": ("certificate_number", "expiry_date", "status"),
                                "doc_ref": ("document_id", "source_document_id")},
    "work_orders": {"label": ("title", "wo_code"), "detail": ("wo_code", "status")},
    "contracts": {"label": ("contract_ref",), "detail": ("status", "start_date"),
                  "doc_ref": ("document_id",)},
    "invoices": {"label": ("invoice_ref",), "detail": ("amount", "status"),
                 "doc_ref": ("document_id",)},
}

#: The tree as the graph is written. Children are read by their own building link rather
#: than through their parent: every space in the building belongs under Floors, and asking
#: per floor would be twenty-four queries to draw one drawer.
_TREE: list[tuple[str, list[str]]] = [
    ("floors", ["spaces"]),
    ("assets", ["equipment", "meters"]),
    ("documents", ["compliance_certificates"]),
    ("contracts", ["work_orders", "invoices"]),
]

_LINK = "building_id"


def _first(cols: set[str], names: tuple[str, ...]) -> str | None:
    return next((c for c in names if c in cols), None)


#: Whether this deployment has the tables the download route reads. A deployment property,
#: not a request one, so it is resolved once and kept.
_RETRIEVAL: dict[str, bool] | None = None


async def _retrieval(session: AsyncSession) -> dict[str, bool]:
    global _RETRIEVAL
    if _RETRIEVAL is not None:
        return _RETRIEVAL
    try:
        rows = (
            await session.execute(
                text(
                    """SELECT table_name, column_name FROM information_schema.columns
                        WHERE table_schema = 'plenum_cafm'
                          AND (table_name = 'ingestion_documents'
                               OR (table_name = 'document_chunks'
                                   AND column_name = 'ingestion_id'))"""
                )
            )
        ).all()
    except Exception as exc:  # noqa: BLE001 — introspection must never break a drawer
        log.warning("building_tree.retrieval_shape_failed", error=str(exc)[:200])
        return {"ingestion": False, "chunks": False}
    have = {str(t) for t, _ in rows}
    # The chunk fallback is claimed only where document_chunks is keyed on ingestion_id,
    # because that is the column the download route joins on. Two unrelated tables carry
    # this name across deployments — production keys chunks on ingestion_id and stores
    # chunk_text; the demo schema keys them on document_id and stores content — and the
    # route can only read the first. Asserting a fallback the route cannot serve would put
    # a live control on a row that answers 404, which is the thing this flag exists to
    # prevent. Testing for the table alone is not testing for the thing.
    _RETRIEVAL = {"ingestion": "ingestion_documents" in have,
                  "chunks": "document_chunks" in have}
    return _RETRIEVAL


def _openable(
    table: str, spec: dict[str, Any], cols: set[str], key: str,
    shape: dict[str, Any], retrieval: dict[str, bool],
) -> tuple[str, str]:
    """SQL for the document this row points at, and for "is there anything to open".

    The second half deliberately mirrors ``GET /api/documents/{id}/download``, which is what
    the link the client builds will call: the stored original if there is one, the extracted
    text if there is not. A flag that answers a different question than the endpoint would
    put a live-looking control on a row that 404s, or grey out one that works — and which of
    those you get depends only on which database you are pointed at, because production
    keeps its files in ``ingestion_documents`` and the test portfolio keeps them in
    ``documents``. Both are asked.

    Returns ``("NULL", "NULL")`` where the question does not apply. NULL is a third answer,
    not a default: a branch that cannot name a document does not know, and a caller must not
    read that as "no". Only a branch that looked and found nothing says false.

    Every identifier here comes from the literal spec above, filtered by what
    information_schema reports — none of it from a request.
    """
    if table == "documents":
        # The row IS the document; there is nothing to follow.
        doc_expr = f"t.{key}::text"
        parts = ([f"(NULLIF(t.blob_url::text, '') IS NOT NULL)"]
                 if "blob_url" in cols else [])
    else:
        refs = [c for c in (spec.get("doc_ref") or ()) if c in cols]
        if not refs:
            return "NULL", "NULL"
        doc_expr = "COALESCE(" + ", ".join(f"t.{c}::text" for c in refs) + ")"
        parts = []
        info = shape.get("documents") or {}
        if info.get("exists") and "blob_url" in (info.get("columns") or set()):
            dkey = info.get("key") or "document_id"
            parts.append(
                f"EXISTS (SELECT 1 FROM plenum_cafm.documents f "
                f"WHERE f.{dkey}::text = {doc_expr} "
                f"AND NULLIF(f.blob_url::text, '') IS NOT NULL)")

    if retrieval.get("ingestion"):
        chunks = (
            " OR EXISTS (SELECT 1 FROM plenum_cafm.document_chunks ch "
            "WHERE ch.ingestion_id::text = i.id::text)"
            if retrieval.get("chunks") else ""
        )
        parts.append(
            f"EXISTS (SELECT 1 FROM plenum_cafm.ingestion_documents i "
            f"WHERE i.id::text = {doc_expr} "
            f"AND (NULLIF(i.blob_url::text, '') IS NOT NULL{chunks}))")

    return doc_expr, ("(" + " OR ".join(parts) + ")" if parts else "NULL")


async def _branch(
    session: AsyncSession, table: str, building_id: str, shape: dict[str, Any],
    retrieval: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """One branch: its total, and up to ``BRANCH_LIMIT`` rows."""
    meta = BRANCH_META.get(table, {"label": table, "empty": "Nothing here."})
    spec = _BRANCHES.get(table) or {}
    info = shape.get(table) or {}
    out: dict[str, Any] = {
        "table": table, "label": meta["label"], "count": 0, "rows": [],
        "truncated": False, "available": False, "empty_reason": meta["empty"],
    }

    if not info.get("exists"):
        out["empty_reason"] = f"plenum_cafm.{table} is not in this database."
        return out
    cols: set[str] = info.get("columns") or set()
    if _LINK not in cols:
        out["empty_reason"] = (
            f"plenum_cafm.{table} has no {_LINK} — this branch cannot be tied to a building "
            "until the graph migration has run."
        )
        return out
    out["available"] = True

    key = info.get("key") or "id"
    # COALESCE across every candidate the table has, not the first one that EXISTS.
    #
    # _first() picks one column for the whole branch, so a table that HAS the column wins
    # even on rows where it is null — and the row then fell through to `r["id"]` below and
    # rendered a raw uuid. documents is the case that showed it: file_name is set on the
    # 18 records ingested from a real PDF and null on the rest, so whichever order the two
    # candidates were listed in, one group or the other was labelled with a uuid. The
    # choice is per row, not per table.
    label_cols = [c for c in (spec.get("label") or ()) if c in cols]
    label_expr = (
        "COALESCE(" + ", ".join(f"NULLIF({c}::text, '')" for c in label_cols) + ")"
        if label_cols else "NULL"
    )
    # The primary candidate still drops out of the detail line, so a label is not repeated
    # beside itself; the later candidates stay, because they are the fallbacks, not the label.
    primary = label_cols[0] if label_cols else None
    detail_cols = [c for c in (spec.get("detail") or ()) if c in cols and c != primary]
    doc_expr, open_expr = _openable(
        table, spec, cols, key, shape, retrieval or {"ingestion": False, "chunks": False})
    select = [f"{key}::text AS id", f"{label_expr} AS label"]
    select += [f"{c}::text AS d{i}" for i, c in enumerate(detail_cols)]
    select.append(f"{doc_expr} AS docref")
    select.append(f"{open_expr} AS has_file")
    order = f"{label_expr} NULLS LAST" if label_cols else key

    try:
        async with session.begin_nested():
            total = (
                await session.execute(
                    text(f"SELECT count(*) FROM plenum_cafm.{table} WHERE {_LINK}::text = :b"),
                    {"b": building_id},
                )
            ).scalar()
            rows = (
                await session.execute(
                    text(
                        f"SELECT {', '.join(select)} FROM plenum_cafm.{table} t "
                        f"WHERE t.{_LINK}::text = :b ORDER BY {order} LIMIT {BRANCH_LIMIT}"
                    ),
                    {"b": building_id},
                )
            ).mappings().all()
    except Exception as exc:  # noqa: BLE001 — one branch must never lose the whole drawer
        log.warning("building_tree.branch_failed", table=table, error=str(exc)[:200])
        out["available"] = False
        out["empty_reason"] = f"Could not read plenum_cafm.{table}."
        return out

    out["count"] = int(total or 0)
    out["rows"] = [{
        "id": r["id"],
        "label": r["label"] or r["id"],
        "detail": " · ".join(
            str(r[f"d{i}"]) for i in range(len(detail_cols)) if r.get(f"d{i}")
        ) or None,
        # The document behind this row, if the table has a column for one — None for a
        # table with no such column, not merely absent, so a caller can tell "nothing
        # filed" apart from "this table cannot say".
        "document_id": r.get("docref"),
        # true: something will come back if this is opened. false: the row names a document
        # and neither a stored file nor extracted text exists for it. null: this branch
        # cannot name a document, so the question does not apply — never read that as "no".
        "has_file": (None if r["has_file"] is None else bool(r["has_file"])),
    } for r in rows]
    out["truncated"] = out["count"] > len(out["rows"])
    return out


async def building_tree(session: AsyncSession, building_id: str) -> dict[str, Any]:
    """The building and everything hanging off it, nested as the graph is."""
    bid = str(building_id or "").strip()
    if not bid:
        return {"ok": False, "error": "building_id is required."}

    shape = await graph_shape(session)
    if not shape["buildings"]["exists"]:
        return {"ok": False, "error": "plenum_cafm.buildings is absent — run the migration."}

    have = shape["buildings"]["columns"]
    key = shape["buildings"]["key"]
    name_col = _first(have, ("name", "building_name"))
    cols = [f"{key}::text AS id",
            (f"{name_col}::text AS name" if name_col else "NULL AS name"),
            ("building_code::text AS code" if "building_code" in have else "NULL AS code"),
            ("site_id::text AS site_id" if "site_id" in have else "NULL AS site_id")]

    row = (
        await session.execute(
            text(f"SELECT {', '.join(cols)} FROM plenum_cafm.buildings WHERE {key}::text = :b"),
            {"b": bid},
        )
    ).mappings().first()
    if not row:
        return {"ok": False, "error": f"No building {bid}.", "status": 404}

    retrieval = await _retrieval(session)
    branches: list[dict[str, Any]] = []
    for parent, children in _TREE:
        node = await _branch(session, parent, bid, shape, retrieval)
        node["children"] = [
            await _branch(session, c, bid, shape, retrieval) for c in children
        ]
        branches.append(node)

    flat = [b for br in branches for b in [br] + br["children"]]
    return {
        "ok": True,
        "building_id": bid,
        "name": row["name"],
        "building_code": row["code"],
        "site_id": row["site_id"],
        "total_records": sum(b["count"] for b in flat),
        "branches": branches,
        # Named rather than left as a silent zero. A branch the database cannot join is a
        # different thing from a branch with nothing in it, and only one is about the
        # building — a caller that cannot tell them apart will say "nothing billed here"
        # about a building that has invoices.
        "unavailable": [b["table"] for b in flat if not b["available"]],
        "row_limit": BRANCH_LIMIT,
    }


async def graph_shape_stats(session: AsyncSession) -> dict[str, Any]:
    """What the graph actually is in this database: tables, columns, relationships.

    The Hoist Graph panel showed five figures, three from a constant compiled into the
    frontend and two typed in by hand. A diagram of the schema that does not read the schema
    is a drawing, and it goes stale the first time a migration runs — silently, because
    nothing checks a hardcoded number against anything.

    Counted from ``information_schema`` and ``pg_constraint``, restricted to the tables the
    graph is actually made of, so an unrelated table added to the schema does not inflate it.
    """
    shape = await graph_shape(session)
    tables = sorted(t for t, i in shape.items() if i.get("exists"))
    if not tables:
        return {"ok": False, "error": "No graph tables in this database."}

    try:
        cols = (
            await session.execute(
                text(
                    """SELECT table_name, count(*) AS n
                       FROM information_schema.columns
                       WHERE table_schema = 'plenum_cafm' AND table_name = ANY(:t)
                       GROUP BY table_name"""
                ),
                {"t": tables},
            )
        ).mappings().all()
        rels = (
            await session.execute(
                text(
                    """SELECT count(*) FROM pg_constraint c
                       JOIN pg_class r ON r.oid = c.conrelid
                       JOIN pg_namespace n ON n.oid = r.relnamespace
                       WHERE c.contype = 'f' AND n.nspname = 'plenum_cafm'
                         AND r.relname = ANY(:t)"""
                ),
                {"t": tables},
            )
        ).scalar()
    except Exception as exc:  # noqa: BLE001 — a panel must not break a page
        log.warning("building_tree.shape_stats_failed", error=str(exc)[:200])
        return {"ok": False, "error": str(exc)[:200]}

    by_table = {r["table_name"]: int(r["n"]) for r in cols}
    return {
        "ok": True,
        "tables": len(tables),
        "columns": sum(by_table.values()),
        "relationships": int(rels or 0),
        "table_names": tables,
        "columns_by_table": by_table,
        # Named so a partly-migrated deployment shows a smaller graph rather than a wrong one.
        "absent": sorted(t for t, i in shape.items() if not i.get("exists")),
    }


#: The snapshots the export panel offers, as cutoffs rather than stored builds.
#:
#: Nothing in this platform records a table's size on a past day. What every graph table
#: does carry is ``created_at``, and "rows whose created_at is at or before midnight last
#: Tuesday" is a real figure read from real rows — not a stored snapshot, but not invented
#: either. The one thing it cannot see is deletion: a row created last month and deleted
#: yesterday is absent from both today's count and every historical one, so a past figure
#: is a floor, not an exact size. That caveat travels with the payload rather than being
#: left for the reader to work out.
_HISTORY: list[tuple[str, str, str]] = [
    ("Current — live", "now()", "every row in the table right now"),
    ("Yesterday's close", "date_trunc('day', now())",
     "rows created before midnight this morning"),
    ("Two days back", "date_trunc('day', now()) - interval '1 day'",
     "rows created before midnight yesterday"),
    ("Last week's close", "date_trunc('day', now()) - interval '6 days'",
     "rows created before midnight six days ago"),
]


async def graph_tables(session: AsyncSession) -> dict[str, Any]:
    """Every graph table: how many rows it holds, how many columns, and its history.

    The export panel named a table, a row count and four dated builds. The row count was a
    formula over a seed building and the builds were four hardcoded percentages of it, which
    is why every table in the portfolio lost the same 2.4% overnight. These are counted.
    """
    shape = await graph_shape(session)
    tables = sorted(t for t, i in shape.items() if i.get("exists"))
    if not tables:
        return {"ok": False, "error": "No graph tables in this database."}

    try:
        cols = (
            await session.execute(
                text(
                    """SELECT table_name, count(*) AS n
                       FROM information_schema.columns
                       WHERE table_schema = 'plenum_cafm' AND table_name = ANY(:t)
                       GROUP BY table_name"""
                ),
                {"t": tables},
            )
        ).mappings().all()
    except Exception as exc:  # noqa: BLE001 — a panel must not break a page
        log.warning("building_tree.graph_tables_failed", error=str(exc)[:200])
        return {"ok": False, "error": str(exc)[:200]}
    by_table = {r["table_name"]: int(r["n"]) for r in cols}

    out: list[dict[str, Any]] = []
    for table in tables:
        info = shape.get(table) or {}
        dated = "created_at" in (info.get("columns") or set())
        entry: dict[str, Any] = {
            "table": table,
            "columns": by_table.get(table, 0),
            "rows": None,
            "history_available": dated,
            "versions": [],
            # Said here rather than left for the caller to infer from a false flag: a table
            # with no history and a table whose history is all zeroes look the same on a
            # screen, and only one of them is a fact about the rows.
            "why": None if dated else
                   f"plenum_cafm.{table} has no created_at, so only its live count can be read.",
        }
        # One statement per table, one branch per cutoff. Each in its own savepoint: a
        # table that cannot be read loses its own row, not the whole panel.
        picks = _HISTORY if dated else _HISTORY[:1]
        selects = ", ".join(
            (f"count(*) AS v{i}" if expr == "now()"
             else f"count(*) FILTER (WHERE created_at <= {expr}) AS v{i}")
            for i, (_, expr, _) in enumerate(picks)
        )
        try:
            async with session.begin_nested():
                row = (
                    await session.execute(
                        text(f"SELECT {selects} FROM plenum_cafm.{table}")
                    )
                ).mappings().first()
        except Exception as exc:  # noqa: BLE001
            log.warning("building_tree.table_count_failed", table=table, error=str(exc)[:200])
            entry["error"] = f"Could not count plenum_cafm.{table}."
            out.append(entry)
            continue

        entry["rows"] = int(row["v0"] or 0)
        for i, (label, _, note) in enumerate(picks):
            n = int(row[f"v{i}"] or 0)
            entry["versions"].append({
                "label": label,
                "rows": n,
                "note": note,
                "current": i == 0,
                # The delta against the count before it, so the panel does not recompute
                # it from percentages and get a different answer.
                "delta": None if i == 0 else n - int(row[f"v{i - 1}"] or 0),
            })
        out.append(entry)

    return {
        "ok": True,
        "tables": out,
        "counted_at": datetime.now(timezone.utc).isoformat(),
        # Said once, plainly, so a reader does not take a historical figure for a snapshot.
        "history_basis": (
            "Historical figures are counted from each row's created_at, not from a stored "
            "snapshot — nothing on this platform records past table sizes. Rows deleted "
            "since are absent from every figure, so a past count is a floor, not the exact "
            "size the table was."
        ),
        "no_history": sorted(e["table"] for e in out if not e["history_available"]),
    }
