"""Row index management endpoints.

Allows uploading CSV or Excel files to populate the row_semantic_index table
that backs document-to-row matching. Each uploaded file becomes a named
"table" in the index that documents can be matched against.

POST /row-index/upload              — Upload CSV/Excel → upsert rows
GET  /row-index/tables              — List all source_tables with row counts
DELETE /row-index/tables/{t}        — Remove all rows for a given table
GET  /row-index/tables/{t}/rows     — List rows for a given table (paginated)
GET  /row-index/db-tables           — List real DB tables available to import
GET  /row-index/db-tables/{t}/columns — List columns of a DB table
POST /row-index/import-db-table     — Import a DB table directly into the index
"""
from __future__ import annotations

import io
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, inspect as sa_inspect, text
from sqlalchemy.orm import Session

from app.core.logger import logger
from app.db.embedding_utils import coerce_embedding
from app.db.models import RowSemanticIndex
from app.db.session import engine, get_db
from app.schemas import RowIndexTable, RowIndexUploadResponse
from app.services.embedding_service import embedding_service

# Tables that belong to the doc-rag service itself — never exposed for import
_INTERNAL_TABLES = frozenset({
    "documents", "document_chunks", "row_semantic_index", "alembic_version",
})

def _cmms_schema() -> str:
    from app.core.config import settings as _cfg

    return (_cfg.plenum_cmms_schema or "plenum_cafm").strip() or "plenum_cafm"

router = APIRouter(prefix="/row-index", tags=["row-index"])

_SUPPORTED_EXTS = {".csv", ".xlsx", ".xls"}


def _serialize(v: Any) -> str:
    """Serialize a DB value to string, using JSON for list/dict (ARRAY / JSONB)."""
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return str(v)


def _read_file(filename: str, content: bytes) -> "Any":
    """Parse CSV or Excel bytes into a pandas DataFrame."""
    try:
        import pandas as pd
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="pandas is not installed — run `pip install pandas openpyxl`.",
        ) from exc

    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in _SUPPORTED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Supported: {sorted(_SUPPORTED_EXTS)}",
        )

    try:
        if suffix == ".csv":
            for enc in ("utf-8", "latin-1", "cp1252"):
                try:
                    df = pd.read_csv(io.BytesIO(content), encoding=enc, dtype=str)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise HTTPException(status_code=400, detail="Could not decode CSV — try UTF-8 or Latin-1.")
        else:
            df = pd.read_excel(io.BytesIO(content), dtype=str)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {exc}") from exc

    df = df.fillna("")
    return df


def _build_semantic_text(row: dict[str, str]) -> str:
    """Produce human-readable text for BM25 + semantic matching."""
    parts = [f"{k}: {v}" for k, v in row.items() if v and str(v).strip()]
    return ". ".join(parts)


# ── Upload ─────────────────────────────────────────────────────────────────────

@router.post("/upload", response_model=RowIndexUploadResponse)
async def upload_row_index(
    file: UploadFile = File(...),
    table_name: str = Form(...),
    pk_column: str = Form(...),
    db: Session = Depends(get_db),
):
    """Upload a CSV or Excel file and upsert its rows into the match index.

    - **table_name**: logical name for this dataset (e.g. `assets`, `equipment`).
    - **pk_column**: column whose value uniquely identifies each row (e.g. `asset_code`).

    Rows are upserted on `(table_name, pk_value)` so re-uploading the same
    file with corrections will update existing rows without creating duplicates.
    Embeddings are generated in batches for semantic matching.
    """
    table_name = table_name.strip()
    pk_column = pk_column.strip()

    if not table_name:
        raise HTTPException(status_code=400, detail="table_name must not be empty.")
    if not pk_column:
        raise HTTPException(status_code=400, detail="pk_column must not be empty.")

    content = await file.read()
    filename = file.filename or "upload"
    logger.info("Row index upload | file={} | table={} | pk={}", filename, table_name, pk_column)

    df = _read_file(filename, content)

    if pk_column not in df.columns:
        raise HTTPException(
            status_code=400,
            detail=f"pk_column '{pk_column}' not found. Available columns: {list(df.columns)}",
        )

    columns = list(df.columns)
    rows_data: list[dict[str, str]] = df.to_dict(orient="records")  # type: ignore[arg-type]
    if not rows_data:
        raise HTTPException(status_code=400, detail="File is empty — no rows to index.")

    logger.info("Parsed {} rows | columns={}", len(rows_data), columns)

    # Build semantic texts and meta for all rows
    semantic_texts: list[str] = []
    metas: list[dict[str, str]] = []
    pks: list[str] = []

    for row in rows_data:
        pk_val = str(row.get(pk_column, "")).strip()
        if not pk_val:
            continue  # skip rows where PK is empty
        pks.append(pk_val)
        metas.append({k: str(v) for k, v in row.items()})
        semantic_texts.append(_build_semantic_text(row))

    if not pks:
        raise HTTPException(
            status_code=400,
            detail=f"All rows had an empty pk_column '{pk_column}'. Nothing to index.",
        )

    # Embed in batch
    logger.info("Embedding {} rows for table='{}'", len(semantic_texts), table_name)
    embeddings = embedding_service.embed_batch(semantic_texts)

    # Upsert into row_semantic_index
    rows_inserted = 0
    rows_updated = 0
    rows_deleted = 0

    existing = {
        r.row_pk: r
        for r in db.query(RowSemanticIndex)
        .filter(RowSemanticIndex.source_table == table_name)
        .all()
    }

    for pk_val, sem_text, meta, emb in zip(pks, semantic_texts, metas, embeddings):
        if pk_val in existing:
            rec = existing[pk_val]
            rec.semantic_text = sem_text
            rec.meta = meta
            rec.embedding = coerce_embedding(emb)
            rec.pk_column = pk_column
            rec.embedding_model = (
                embedding_service.model if not embedding_service.mock else "mock"
            )
            rows_updated += 1
        else:
            db.add(RowSemanticIndex(
                id=str(uuid.uuid4()),
                source_table=table_name,
                row_pk=pk_val,
                pk_column=pk_column,
                semantic_text=sem_text,
                meta=meta,
                embedding=coerce_embedding(emb),
                embedding_model=(
                    embedding_service.model if not embedding_service.mock else "mock"
                ),
            ))
            rows_inserted += 1

    # Keep index table in sync with source snapshot by removing stale rows
    # that are no longer present in the latest import payload.
    current_pks = set(pks)
    stale_pks = [pk for pk in existing.keys() if pk not in current_pks]
    if stale_pks:
        rows_deleted = (
            db.query(RowSemanticIndex)
            .filter(
                RowSemanticIndex.source_table == table_name,
                RowSemanticIndex.row_pk.in_(stale_pks),
            )
            .delete(synchronize_session=False)
        )

    db.commit()

    total = db.query(func.count(RowSemanticIndex.id)).filter(
        RowSemanticIndex.source_table == table_name
    ).scalar() or 0

    logger.info(
        "Row index upload complete | table={} | inserted={} | updated={} | deleted={} | total={}",
        table_name, rows_inserted, rows_updated, rows_deleted, total,
    )

    return RowIndexUploadResponse(
        table_name=table_name,
        rows_inserted=rows_inserted,
        rows_updated=rows_updated,
        total_rows_in_index=total,
        columns_detected=columns,
        pk_column=pk_column,
    )


# ── List tables ────────────────────────────────────────────────────────────────

@router.get("/tables", response_model=list[RowIndexTable])
def list_tables(db: Session = Depends(get_db)):
    """Return all source_tables in the index with their row counts."""
    rows = (
        db.query(RowSemanticIndex.source_table, func.count(RowSemanticIndex.id))
        .group_by(RowSemanticIndex.source_table)
        .order_by(RowSemanticIndex.source_table)
        .all()
    )
    return [RowIndexTable(source_table=t, row_count=c) for t, c in rows]


# ── Delete table ───────────────────────────────────────────────────────────────

@router.delete("/tables/{table_name}")
def delete_table(table_name: str, db: Session = Depends(get_db)):
    """Delete all rows for a given source_table from the index."""
    deleted = (
        db.query(RowSemanticIndex)
        .filter(RowSemanticIndex.source_table == table_name)
        .delete(synchronize_session=False)
    )
    db.commit()
    if deleted == 0:
        raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found in index.")
    logger.info("Row index table deleted | table={} | rows_removed={}", table_name, deleted)
    return {"status": "deleted", "table_name": table_name, "rows_removed": deleted}


# ── List rows for a table ──────────────────────────────────────────────────────

@router.get("/tables/{table_name}/rows")
def list_table_rows(
    table_name: str,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """List rows for a given source_table — reads live from plenum_cafm schema."""
    from app.core.config import settings as _cfg

    # SQLite dev mode: fall back to index snapshot
    if _cfg.effective_use_sqlite_dev:
        total = (
            db.query(func.count(RowSemanticIndex.id))
            .filter(RowSemanticIndex.source_table == table_name)
            .scalar() or 0
        )
        rows = (
            db.query(RowSemanticIndex)
            .filter(RowSemanticIndex.source_table == table_name)
            .order_by(RowSemanticIndex.row_pk)
            .offset(offset)
            .limit(limit)
            .all()
        )
        return {
            "table_name": table_name,
            "total": total,
            "offset": offset,
            "limit": limit,
            "rows": [
                {"row_pk": r.row_pk, "meta": r.meta, "created_at": r.created_at.isoformat()}
                for r in rows
            ],
        }

    # Validate the table exists in plenum_cafm (prevents arbitrary SQL)
    try:
        inspector = sa_inspect(engine)
        all_tables = inspector.get_table_names(schema=_cmms_schema())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Schema inspection failed: {exc}") from exc

    if table_name not in all_tables:
        raise HTTPException(
            status_code=404,
            detail=f"Table '{table_name}' not found in schema '{_cmms_schema()}'.",
        )

    # Resolve pk_column from the index (set at import time)
    pk_rec = (
        db.query(RowSemanticIndex.pk_column)
        .filter(RowSemanticIndex.source_table == table_name)
        .first()
    )
    pk_column: str | None = pk_rec[0] if pk_rec else None

    # Query live from plenum_cafm
    with engine.connect() as conn:
        total = conn.execute(
            text(f'SELECT COUNT(*) FROM {_cmms_schema()}."{table_name}"')  # noqa: S608
        ).scalar() or 0

        result = conn.execute(
            text(f'SELECT * FROM {_cmms_schema()}."{table_name}" LIMIT :lim OFFSET :off'),  # noqa: S608
            {"lim": limit, "off": offset},
        )
        keys = list(result.keys())
        raw_rows = result.fetchall()

    rows_out = []
    for row in raw_rows:
        row_dict = {k: _serialize(v) for k, v in zip(keys, row)}
        rows_out.append({
            "row_pk": row_dict.get(pk_column, "") if pk_column else "",
            "meta": row_dict,
            "created_at": row_dict.get("created_at", ""),
        })

    return {
        "table_name": table_name,
        "total": total,
        "offset": offset,
        "limit": limit,
        "rows": rows_out,
    }


# ── List real DB tables available for import ───────────────────────────────────

class DbTablesResponse(BaseModel):
    schema_name: str
    tables: list[dict[str, Any]]


@router.get("/db-tables")
def list_db_tables(envelope: bool = Query(False, description="Return {schema_name, tables} wrapper")):
    """Return all user tables in the plenum_cafm CMMS schema (raw Postgres connection).

    These are the tables available to import directly into the row index
    without needing a CSV upload — useful when CMMS data is already in the
    same PostgreSQL instance as schema mapper / migration.

    Query ``envelope=true`` for ``{schema_name, tables}``; default remains a
    plain array for backward compatibility.
    """
    from app.core.config import settings as _cfg

    schema = _cmms_schema()
    if _cfg.effective_use_sqlite_dev:
        payload: list[dict[str, Any]] = []
        if envelope:
            return DbTablesResponse(schema_name=schema, tables=payload)
        return payload

    try:
        inspector = sa_inspect(engine)
        all_tables = inspector.get_table_names(schema=schema)
    except Exception as exc:
        logger.warning("list_db_tables | could not inspect schema {}: {}", schema, exc)
        payload = []
        if envelope:
            return DbTablesResponse(schema_name=schema, tables=payload)
        return payload

    user_tables = sorted(t for t in all_tables if t not in _INTERNAL_TABLES)

    result: list[dict[str, Any]] = []
    with engine.connect() as conn:
        for t in user_tables:
            try:
                row = conn.execute(
                    text(f'SELECT COUNT(*) FROM {schema}."{t}"')  # noqa: S608
                ).scalar()
                result.append({"table_name": t, "row_count": int(row or 0), "schema_name": schema})
            except Exception:
                result.append({"table_name": t, "row_count": None, "schema_name": schema})

    if envelope:
        return DbTablesResponse(schema_name=schema, tables=result)
    return result


# ── Get columns of a DB table ──────────────────────────────────────────────────

@router.get("/db-tables/{table_name}/columns")
def get_db_table_columns(table_name: str):
    """Return column names and types for a given table in the plenum_cafm schema."""
    from app.core.config import settings as _cfg
    if _cfg.effective_use_sqlite_dev:
        raise HTTPException(
            status_code=501,
            detail="DB table import is only available when connected to PostgreSQL.",
        )

    try:
        inspector = sa_inspect(engine)
        all_tables = inspector.get_table_names(schema=_cmms_schema())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Schema inspection failed: {exc}") from exc

    if table_name not in all_tables:
        raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found in schema '{_cmms_schema()}'.")
    cols = inspector.get_columns(table_name, schema=_cmms_schema())
    return [{"name": c["name"], "type": str(c["type"])} for c in cols]


# ── Import a DB table directly into the row index ─────────────────────────────

@router.post("/import-db-table", response_model=RowIndexUploadResponse)
def import_db_table(
    table_name: str = Form(...),
    pk_column: str = Form(...),
    row_limit: int = Form(default=10000),
    db: Session = Depends(get_db),
):
    """Read rows from an existing database table and upsert them into the row index.

    - **table_name**: name of the database table to import from.
    - **pk_column**: column that uniquely identifies each row (e.g. `asset_code`).
    - **row_limit**: maximum rows to import (default 10 000).
    """
    from app.core.config import settings as _cfg
    if _cfg.effective_use_sqlite_dev:
        raise HTTPException(
            status_code=501,
            detail="DB table import is only available when connected to PostgreSQL.",
        )

    table_name = table_name.strip()
    pk_column = pk_column.strip()

    if not table_name:
        raise HTTPException(status_code=400, detail="table_name must not be empty.")
    if table_name in _INTERNAL_TABLES:
        raise HTTPException(status_code=400, detail=f"'{table_name}' is an internal table and cannot be imported.")

    try:
        inspector = sa_inspect(engine)
        all_tables = inspector.get_table_names(schema=_cmms_schema())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Schema inspection failed: {exc}") from exc

    if table_name not in all_tables:
        raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found in schema '{_cmms_schema()}'.")

    col_names = [c["name"] for c in inspector.get_columns(table_name, schema=_cmms_schema())]
    if pk_column not in col_names:
        raise HTTPException(
            status_code=400,
            detail=f"pk_column '{pk_column}' not found. Available columns: {col_names}",
        )

    logger.info("DB table import | schema={} | table={} | pk={} | limit={}", _cmms_schema(), table_name, pk_column, row_limit)

    with engine.connect() as conn:
        result = conn.execute(
            text(f'SELECT * FROM {_cmms_schema()}."{table_name}" LIMIT :lim'),  # noqa: S608
            {"lim": row_limit},
        )
        keys = list(result.keys())
        raw_rows = result.fetchall()

    if not raw_rows:
        raise HTTPException(status_code=400, detail=f"Table '{table_name}' returned no rows.")

    # Convert to string dicts (same format as CSV upload)
    rows_data = [
        {k: _serialize(v) for k, v in zip(keys, row)}
        for row in raw_rows
    ]

    semantic_texts: list[str] = []
    metas: list[dict[str, str]] = []
    pks: list[str] = []

    for row in rows_data:
        pk_val = row.get(pk_column, "").strip()
        if not pk_val:
            continue
        pks.append(pk_val)
        metas.append(row)
        semantic_texts.append(_build_semantic_text(row))

    if not pks:
        raise HTTPException(
            status_code=400,
            detail=f"All rows had an empty pk_column '{pk_column}'. Nothing to index.",
        )

    logger.info("Embedding {} rows for table='{}'", len(semantic_texts), table_name)
    embeddings = embedding_service.embed_batch(semantic_texts)

    existing = {
        r.row_pk: r
        for r in db.query(RowSemanticIndex)
        .filter(RowSemanticIndex.source_table == table_name)
        .all()
    }

    rows_inserted = 0
    rows_updated = 0
    rows_deleted = 0

    for pk_val, sem_text, meta, emb in zip(pks, semantic_texts, metas, embeddings):
        if pk_val in existing:
            rec = existing[pk_val]
            rec.semantic_text = sem_text
            rec.meta = meta
            rec.embedding = coerce_embedding(emb)
            rec.pk_column = pk_column
            rec.embedding_model = (
                embedding_service.model if not embedding_service.mock else "mock"
            )
            rows_updated += 1
        else:
            db.add(RowSemanticIndex(
                id=str(uuid.uuid4()),
                source_table=table_name,
                row_pk=pk_val,
                pk_column=pk_column,
                semantic_text=sem_text,
                meta=meta,
                embedding=coerce_embedding(emb),
                embedding_model=(
                    embedding_service.model if not embedding_service.mock else "mock"
                ),
            ))
            rows_inserted += 1

    # Keep index table in sync with source DB table by pruning stale row_pks.
    current_pks = set(pks)
    stale_pks = [pk for pk in existing.keys() if pk not in current_pks]
    if stale_pks:
        rows_deleted = (
            db.query(RowSemanticIndex)
            .filter(
                RowSemanticIndex.source_table == table_name,
                RowSemanticIndex.row_pk.in_(stale_pks),
            )
            .delete(synchronize_session=False)
        )

    db.commit()

    total = db.query(func.count(RowSemanticIndex.id)).filter(
        RowSemanticIndex.source_table == table_name
    ).scalar() or 0

    logger.info(
        "DB import complete | table={} | inserted={} | updated={} | deleted={} | total={}",
        table_name, rows_inserted, rows_updated, rows_deleted, total,
    )

    return RowIndexUploadResponse(
        table_name=table_name,
        rows_inserted=rows_inserted,
        rows_updated=rows_updated,
        total_rows_in_index=total,
        columns_detected=keys,
        pk_column=pk_column,
    )
