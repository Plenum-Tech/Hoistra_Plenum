"""SQLAlchemy ORM models.

Document + DocumentChunk are mapped onto the platform's `plenum_cafm` tables
(`plenum_cafm.ingestion_documents` + `plenum_cafm.document_chunks`) so document
ingestion is stored in plenum_cafm ONLY — there is no separate public.* document
store. Attribute names are kept stable (file_name, document_id, text_content, …)
and mapped to the underlying plenum_cafm column names, so every reader
(vector search, RAG, citations, doc-match) works unchanged.

The RAG audit tables (rag_queries/answers/feedback) and the row semantic index
stay in the default (public) schema — they are doc-rag internals, not documents.

When running in `USE_SQLITE_DEV=true` mode the pgvector column is replaced with a
JSON column and the schema/UUID specialisations fall back to portable types.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.config import settings


def _uuid_str() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# Postgres: documents live in the plenum_cafm schema and use uuid PKs.
# SQLite dev: no schemas, string PKs.
_USE_SQLITE = settings.effective_use_sqlite_dev
_PLENUM_SCHEMA = None if _USE_SQLITE else "plenum_cafm"


def _id_type():
    """uuid PK type on Postgres (accepts/returns str), String(36) on SQLite dev."""
    if _USE_SQLITE:
        return String(36)
    from sqlalchemy.dialects.postgresql import UUID

    return UUID(as_uuid=False)


def _plenum_args() -> dict:
    return {} if _USE_SQLITE else {"schema": "plenum_cafm"}


def _ingestion_fk():
    target = "ingestion_documents.id" if _USE_SQLITE else "plenum_cafm.ingestion_documents.id"
    return ForeignKey(target, ondelete="CASCADE")


def _embedding_column():
    """Postgres: native pgvector column. SQLite dev: JSON list[float]."""
    if _USE_SQLITE:
        return Column("embedding", JSON, nullable=True)
    from pgvector.sqlalchemy import Vector

    return Column("embedding", Vector(settings.openai_embedding_dim), nullable=True)


# ---------- Documents (→ plenum_cafm.ingestion_documents) ----------
class Document(Base):
    __tablename__ = "ingestion_documents"
    __table_args__ = _plenum_args()

    id: Mapped[str] = mapped_column(_id_type(), primary_key=True, default=_uuid_str)
    # plenum column is original_filename
    file_name: Mapped[str] = mapped_column("original_filename", String(512), nullable=False)
    mime_type: Mapped[str | None] = mapped_column("mime_type", String(128), nullable=True)
    document_type: Mapped[str | None] = mapped_column("document_type", String(64), nullable=True)
    source_uri: Mapped[str | None] = mapped_column("source_uri", String(1024), nullable=True)
    # URL of the ORIGINAL file stored in Azure Blob (view/download from chat).
    blob_url: Mapped[str | None] = mapped_column("blob_url", Text, nullable=True)
    checksum: Mapped[str | None] = mapped_column("checksum", String(128), nullable=True)
    status: Mapped[str] = mapped_column("status", String(32), nullable=False, default="uploaded")
    num_pages: Mapped[int | None] = mapped_column("page_count", Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column("uploaded_at", DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        "updated_at", DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = _plenum_args()

    id: Mapped[str] = mapped_column(_id_type(), primary_key=True, default=_uuid_str)
    # plenum column is ingestion_id
    document_id: Mapped[str] = mapped_column("ingestion_id", _id_type(), _ingestion_fk(), nullable=False)
    page_start: Mapped[int | None] = mapped_column("page_start", Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column("page_end", Integer, nullable=True)
    chunk_index: Mapped[int] = mapped_column("chunk_index", Integer, nullable=False)
    block_type: Mapped[str] = mapped_column("block_type", String(32), nullable=False, default="paragraph")
    # plenum column is heading
    section_label: Mapped[str | None] = mapped_column("heading", String(64), nullable=True)
    # plenum column is chunk_text
    text_content: Mapped[str] = mapped_column("chunk_text", Text, nullable=False)
    normalized_text: Mapped[str | None] = mapped_column("normalized_text", Text, nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    embedding = _embedding_column()
    embedding_model: Mapped[str | None] = mapped_column("embedding_model", String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column("created_at", DateTime(timezone=True), default=datetime.utcnow)
    # Parent-entity anchor (Feature 7.9 — Test 1): the structured row this chunk
    # is grounded to, by its PRIMARY KEY. Set at document↔row matching time so
    # Test 1 can enforce "every chunk association references a PK".
    source_table: Mapped[str | None] = mapped_column("source_table", String(128), nullable=True)
    row_pk: Mapped[str | None] = mapped_column("row_pk", String(128), nullable=True)
    pk_column: Mapped[str | None] = mapped_column("pk_column", String(128), nullable=True)

    document: Mapped[Document] = relationship(back_populates="chunks")


# ---------- Row semantic index (for DB row grounding) ----------
class RowSemanticIndex(Base):
    __tablename__ = "row_semantic_index"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    source_table: Mapped[str] = mapped_column(String(128), nullable=False)
    row_pk: Mapped[str] = mapped_column(String(128), nullable=False)
    pk_column: Mapped[str | None] = mapped_column(String(128), nullable=True)
    semantic_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    embedding = _embedding_column()
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------- Query audit + feedback ----------
class RagQuery(Base):
    __tablename__ = "rag_queries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    filters: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RagAnswer(Base):
    __tablename__ = "rag_answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    query_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rag_queries.id", ondelete="CASCADE"), nullable=False
    )
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    highlights: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RagFeedback(Base):
    __tablename__ = "rag_feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    query_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rag_queries.id", ondelete="CASCADE"), nullable=False
    )
    answer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rag_answers.id", ondelete="CASCADE"), nullable=False
    )
    feedback_type: Mapped[str] = mapped_column(String(64), nullable=False)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    correction: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
