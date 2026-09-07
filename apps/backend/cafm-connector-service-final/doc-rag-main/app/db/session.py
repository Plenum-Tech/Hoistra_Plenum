"""Database session and engine management."""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.logger import logger
from app.db.embedding_utils import patch_embedding_column_types, register_pgvector_driver
from app.db.models import Base

# SQLite needs check_same_thread=False for FastAPI
_connect_args = {"check_same_thread": False} if settings.effective_use_sqlite_dev else {}

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)

register_pgvector_driver(engine)

try:
    patch_embedding_column_types()
except Exception as exc:
    logger.warning("Embedding column patch at engine init: {}", exc)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """Create all tables. Ensures pgvector extension on PostgreSQL."""
    logger.info("Initializing database | url={}", _safe_url(settings.database_url))
    if not settings.effective_use_sqlite_dev:
        from sqlalchemy import text

        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            logger.info("PostgreSQL extension 'vector' ready")
        except Exception as exc:
            logger.warning("Could not create pgvector extension: {}", exc)
    patch_embedding_column_types()
    Base.metadata.create_all(bind=engine)
    logger.info("Database schema ready ({} tables)", len(Base.metadata.tables))

    # Idempotent column additions for existing deployments.
    # SQLAlchemy's create_all only creates missing *tables*, not missing columns.
    _run_migrations()


def _run_migrations() -> None:
    """Add any columns that are new since the initial create_all.
    Each statement uses IF NOT EXISTS / DO NOTHING so it is safe to run
    on every startup against an already-migrated database.
    """
    from sqlalchemy import text

    stmts: list[str] = []

    if not settings.effective_use_sqlite_dev:
        # pk_column — stores which CMMS column was designated as the PK during indexing
        stmts.append(
            "ALTER TABLE row_semantic_index "
            "ADD COLUMN IF NOT EXISTS pk_column VARCHAR(128)"
        )
        # Align the shared plenum_cafm document tables with the doc-rag ORM:
        # add the columns the doc-rag write/read path needs (all nullable, non-destructive)
        # and relax NOT NULLs / add defaults for columns the doc-rag path does not fill.
        # svc-ingestion still passes those columns explicitly, so it is unaffected.
        stmts += [
            "ALTER TABLE plenum_cafm.ingestion_documents ADD COLUMN IF NOT EXISTS mime_type VARCHAR(128)",
            "ALTER TABLE plenum_cafm.ingestion_documents ADD COLUMN IF NOT EXISTS document_type VARCHAR(64)",
            "ALTER TABLE plenum_cafm.ingestion_documents ADD COLUMN IF NOT EXISTS source_uri VARCHAR(1024)",
            "ALTER TABLE plenum_cafm.ingestion_documents ADD COLUMN IF NOT EXISTS checksum VARCHAR(128)",
            "ALTER TABLE plenum_cafm.ingestion_documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now()",
            "ALTER TABLE plenum_cafm.ingestion_documents ALTER COLUMN source_type SET DEFAULT 'document'",
            "ALTER TABLE plenum_cafm.ingestion_documents ALTER COLUMN agent_id SET DEFAULT 'doc-rag'",
            "ALTER TABLE plenum_cafm.document_chunks ADD COLUMN IF NOT EXISTS page_start INTEGER",
            "ALTER TABLE plenum_cafm.document_chunks ADD COLUMN IF NOT EXISTS page_end INTEGER",
            "ALTER TABLE plenum_cafm.document_chunks ADD COLUMN IF NOT EXISTS block_type VARCHAR(32)",
            "ALTER TABLE plenum_cafm.document_chunks ADD COLUMN IF NOT EXISTS normalized_text TEXT",
            "ALTER TABLE plenum_cafm.document_chunks ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(128)",
            "ALTER TABLE plenum_cafm.document_chunks ALTER COLUMN source_filename DROP NOT NULL",
            # Parent-PK anchor (Feature 7.9 / Test 1) — set at doc↔row matching.
            "ALTER TABLE plenum_cafm.document_chunks ADD COLUMN IF NOT EXISTS source_table VARCHAR(128)",
            "ALTER TABLE plenum_cafm.document_chunks ADD COLUMN IF NOT EXISTS row_pk VARCHAR(128)",
            "ALTER TABLE plenum_cafm.document_chunks ADD COLUMN IF NOT EXISTS pk_column VARCHAR(128)",
        ]
    else:
        # SQLite doesn't support IF NOT EXISTS in ALTER TABLE; use PRAGMA check instead
        with engine.connect() as conn:
            cols = conn.execute(
                text("PRAGMA table_info(row_semantic_index)")
            ).fetchall()
            existing = {row[1] for row in cols}
            if "pk_column" not in existing:
                stmts.append(
                    "ALTER TABLE row_semantic_index ADD COLUMN pk_column VARCHAR(128)"
                )

    if stmts:
        with engine.begin() as conn:
            for stmt in stmts:
                try:
                    conn.execute(text(stmt))
                    logger.info("Migration applied: {}", stmt[:80])
                except Exception as exc:
                    logger.warning("Migration skipped ({}): {}", type(exc).__name__, stmt[:80])


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency — yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _safe_url(url: str) -> str:
    # Hide password in logs
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            creds, host = rest.split("@", 1)
            if ":" in creds:
                user, _ = creds.split(":", 1)
                return f"{scheme}://{user}:***@{host}"
    return url
