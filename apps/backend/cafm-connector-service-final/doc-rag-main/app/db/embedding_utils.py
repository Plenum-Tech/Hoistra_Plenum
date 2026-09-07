"""Helpers for pgvector vs SQLite JSON embedding columns."""
from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.core.logger import logger

_pgvector_registered = False


def coerce_embedding(value: list[float] | None) -> Any:
    """Return a value psycopg/SQLAlchemy can bind to the embedding column."""
    if value is None:
        return None
    floats = [float(x) for x in value]
    if settings.effective_use_sqlite_dev:
        return floats
    try:
        from pgvector import Vector

        return Vector(floats)
    except ImportError:
        return floats


def patch_embedding_column_types() -> None:
    """Force ORM to use pgvector.Vector when connected to PostgreSQL."""
    if settings.effective_use_sqlite_dev:
        return
    try:
        from pgvector.sqlalchemy import Vector
    except ImportError as exc:
        raise RuntimeError(
            "pgvector package is required for PostgreSQL embeddings. "
            "Install pgvector in the schema-mapper / doc-rag image."
        ) from exc

    from app.db.models import Base, DocumentChunk, RowSemanticIndex

    vec_type = Vector(settings.openai_embedding_dim)
    for table_name in ("document_chunks", "row_semantic_index"):
        if table_name in Base.metadata.tables:
            Base.metadata.tables[table_name].c.embedding.type = vec_type
    if DocumentChunk.__table__ is not None:
        DocumentChunk.__table__.c.embedding.type = vec_type
    if RowSemanticIndex.__table__ is not None:
        RowSemanticIndex.__table__.c.embedding.type = vec_type
    logger.info(
        "Embedding columns patched for PostgreSQL | dim={} | dialect=vector",
        settings.openai_embedding_dim,
    )


def register_pgvector_driver(engine: Engine) -> None:
    """Register pgvector types on each psycopg connection."""
    global _pgvector_registered
    if settings.effective_use_sqlite_dev or _pgvector_registered:
        return

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _connection_record) -> None:
        try:
            from pgvector.psycopg import register_vector

            register_vector(dbapi_connection)
        except Exception as exc:
            logger.warning("pgvector register_vector on connect failed: {}", exc)

    _pgvector_registered = True
    logger.info("pgvector psycopg driver registration enabled")
