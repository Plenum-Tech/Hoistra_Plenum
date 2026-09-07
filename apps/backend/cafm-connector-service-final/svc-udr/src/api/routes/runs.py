"""
UDR run versioning (WP-4).

Persists saved UDR "runs" so the last N versions survive across devices. Each save
creates a new version_no for the session; the FM can select a prior version and rename it.
Raw SQL (no ORM), consistent with the rest of svc-udr.
"""
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...config import settings
from ...core.logging import get_logger

router = APIRouter()
log = get_logger(__name__)

_SCHEMA = settings.db_schema
_COLS = (
    "id, session_id, organization_id, version_no, custom_name, phase, "
    "mapping_status, hierarchy_status, migration_ids, document_ids, batch_ids, snapshot, created_at"
)


# ── Schemas ───────────────────────────────────────────────────────────────────

class UdrRunCreateRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    organization_id: str | None = None
    custom_name: str | None = Field(None, max_length=200)
    phase: str | None = None
    mapping_status: str | None = None
    hierarchy_status: str | None = None
    migration_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    batch_ids: list[str] = Field(default_factory=list)
    snapshot: dict[str, Any] | None = None


class UdrRunRenameRequest(BaseModel):
    custom_name: str = Field(..., min_length=1, max_length=200)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _coerce_json(value: Any, default: Any) -> Any:
    """JSONB columns may arrive already-parsed or as a JSON string (driver-dependent)."""
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default
    return default


def _row_to_dict(row: Any) -> dict:
    m = row._mapping
    created = m["created_at"]
    return {
        "id": str(m["id"]),
        "session_id": m["session_id"],
        "organization_id": str(m["organization_id"]) if m["organization_id"] else None,
        "version_no": m["version_no"],
        "custom_name": m["custom_name"],
        "phase": m["phase"],
        "mapping_status": m["mapping_status"],
        "hierarchy_status": m["hierarchy_status"],
        "migration_ids": _coerce_json(m["migration_ids"], []),
        "document_ids": _coerce_json(m["document_ids"], []),
        "batch_ids": _coerce_json(m["batch_ids"], []),
        "snapshot": _coerce_json(m["snapshot"], None),
        "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/runs", status_code=status.HTTP_201_CREATED, summary="Save a new UDR run version")
async def create_run(body: UdrRunCreateRequest, session: AsyncSession = Depends(get_session)) -> dict:
    run_id = str(uuid.uuid4())

    next_no = (
        await session.execute(
            text(
                f"SELECT COALESCE(MAX(version_no), 0) + 1 AS n "
                f"FROM {_SCHEMA}.udr_run_versions WHERE session_id = :sid"
            ),
            {"sid": body.session_id},
        )
    ).scalar()
    version_no = int(next_no or 1)

    insert_sql = text(
        f"""
        INSERT INTO {_SCHEMA}.udr_run_versions ({_COLS})
        VALUES (
            CAST(:id AS UUID), :session_id, CAST(:organization_id AS UUID), :version_no,
            :custom_name, :phase, :mapping_status, :hierarchy_status,
            CAST(:migration_ids AS JSONB), CAST(:document_ids AS JSONB),
            CAST(:batch_ids AS JSONB), CAST(:snapshot AS JSONB), now()
        )
        RETURNING {_COLS}
        """
    )
    params = {
        "id": run_id,
        "session_id": body.session_id,
        "organization_id": body.organization_id,
        "version_no": version_no,
        "custom_name": (body.custom_name or "").strip() or f"Version {version_no}",
        "phase": body.phase,
        "mapping_status": body.mapping_status,
        "hierarchy_status": body.hierarchy_status,
        "migration_ids": json.dumps(body.migration_ids or []),
        "document_ids": json.dumps(body.document_ids or []),
        "batch_ids": json.dumps(body.batch_ids or []),
        "snapshot": json.dumps(body.snapshot) if body.snapshot is not None else None,
    }
    row = (await session.execute(insert_sql, params)).first()
    await session.commit()
    log.info("udr.run.created", run_id=run_id, session_id=body.session_id, version_no=version_no)
    return _row_to_dict(row)


@router.get("/runs", summary="List recent UDR run versions for a session")
async def list_runs(
    session_id: str = Query(..., min_length=1),
    limit: int = Query(3, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = (
        await session.execute(
            text(
                f"SELECT {_COLS} FROM {_SCHEMA}.udr_run_versions "
                f"WHERE session_id = :sid ORDER BY version_no DESC LIMIT :lim"
            ),
            {"sid": session_id, "lim": limit},
        )
    ).fetchall()
    return {"session_id": session_id, "versions": [_row_to_dict(r) for r in rows]}


def _snap_get(snapshot: Any, key: str, default: Any = None) -> Any:
    """Read a key from a (possibly None / non-dict) snapshot safely."""
    if isinstance(snapshot, dict):
        v = snapshot.get(key)
        return v if v is not None else default
    return default


@router.get("/scripts", summary="List UDR scripts (sessions with versions) for an org")
async def list_scripts(
    organization_id: str | None = Query(
        None, description="Filter to one org. Omit to list all (dev/global)."
    ),
    limit: int = Query(200, ge=1, le=1000, description="Max scripts to return."),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Group ``udr_run_versions`` by ``session_id`` — each session is one UDR
    "script" with an ordered version history. This is what the left-nav UDR
    Scripts panel queries (backend-driven, survives refresh / localStorage
    cleanup / device change). Completed migrations create a version (see the FE
    completion hook), so every completed migration surfaces here automatically.
    """
    where = "WHERE organization_id = CAST(:org AS UUID)" if organization_id else ""
    params: dict[str, Any] = {"org": organization_id} if organization_id else {}
    rows = (
        await session.execute(
            text(
                f"SELECT {_COLS} FROM {_SCHEMA}.udr_run_versions "
                f"{where} ORDER BY session_id, version_no DESC"
            ),
            params,
        )
    ).fetchall()

    by_session: dict[str, list[dict]] = {}
    for r in rows:
        d = _row_to_dict(r)
        by_session.setdefault(d["session_id"], []).append(d)

    scripts: list[dict] = []
    for sid, versions in by_session.items():
        versions.sort(key=lambda v: v["version_no"], reverse=True)
        latest = versions[0]
        snap = latest.get("snapshot")
        created = min(v["created_at"] for v in versions)
        updated = max(v["created_at"] for v in versions)
        migration_ids: list[str] = []
        for v in versions:
            for m in v.get("migration_ids") or []:
                if m not in migration_ids:
                    migration_ids.append(m)
        document_ids: list[str] = []
        for v in versions:
            for doc in v.get("document_ids") or []:
                if doc not in document_ids:
                    document_ids.append(doc)
        scripts.append(
            {
                "session_id": sid,
                "organization_id": latest.get("organization_id"),
                # Friendly code filled in below (UDR-YYYY-NNN), unless the
                # snapshot carries an explicit run code from the FE.
                "script_code": _snap_get(snap, "runCode") or _snap_get(snap, "run_code"),
                "name": latest.get("custom_name") or "UDR script",
                "version_count": len(versions),
                "latest_version_no": latest["version_no"],
                "phase": latest.get("phase"),
                "status": _snap_get(snap, "status") or latest.get("phase"),
                "created_at": created,
                "updated_at": updated,
                "migration_ids": migration_ids,
                "document_ids": document_ids,
                "source_file_names": _snap_get(snap, "sourceFileNames", []),
                "table_count": _snap_get(snap, "tableCount"),
                "column_count": _snap_get(snap, "columnCount"),
                "mapped_column_count": _snap_get(snap, "mappedColumnCount"),
                "mapping_coverage_pct": _snap_get(snap, "mappingCoveragePct"),
                "versions": versions,
            }
        )

    # Assign sequential, human-friendly codes (UDR-YYYY-NNN) by creation order,
    # leaving any FE-provided run code untouched.
    seq = 0
    for s in sorted(scripts, key=lambda s: s["created_at"]):
        seq += 1
        if not s["script_code"]:
            year = s["created_at"][:4] if isinstance(s["created_at"], str) else "0000"
            s["script_code"] = f"UDR-{year}-{seq:03d}"

    scripts.sort(key=lambda s: s["updated_at"], reverse=True)
    return {"scripts": scripts[:limit], "count": len(scripts)}


@router.patch("/runs/{run_id}", summary="Rename a UDR run version")
async def rename_run(
    run_id: str,
    body: UdrRunRenameRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = (
        await session.execute(
            text(
                f"UPDATE {_SCHEMA}.udr_run_versions SET custom_name = :name "
                f"WHERE id = CAST(:id AS UUID) RETURNING {_COLS}"
            ),
            {"id": run_id, "name": body.custom_name.strip()},
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="UDR run version not found")
    await session.commit()
    log.info("udr.run.renamed", run_id=run_id)
    return _row_to_dict(row)
