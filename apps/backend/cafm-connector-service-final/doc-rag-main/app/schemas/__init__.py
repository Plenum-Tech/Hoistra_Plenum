"""Pydantic request/response schemas."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------- Documents ----------
class DocumentOut(BaseModel):
    id: str
    file_name: str
    mime_type: str | None = None
    document_type: str | None = None
    status: str
    num_pages: int | None = None
    num_chunks: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentUploadResponse(BaseModel):
    document_id: str
    status: str
    file_name: str
    num_pages: int
    num_chunks: int
    document_type: str | None = None
    processing_time_ms: int


class ChunkPreview(BaseModel):
    chunk_index: int
    page_start: int | None
    page_end: int | None
    block_type: str
    section_label: str | None = None
    text_content: str
    meta: dict[str, Any] | None = None


# ---------- RAG query ----------
class RagQueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    filters: dict[str, Any] | None = None
    top_k: int = 8
    user_id: str | None = None
    session_id: str | None = None


class Citation(BaseModel):
    document_id: str
    file_name: str
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    chunk_id: str
    quote: str


class ChunkMatchDetail(BaseModel):
    """Details about a specific chunk that matched this row."""
    chunk_id: str
    chunk_index: int
    page_number: int | None
    confidence: float
    semantic_score: float = 0.0
    bm25_score: float = 0.0
    metadata_score: float = 0.0
    matched_fields: list[str]  # Which metadata fields matched in this chunk
    chunk_text_preview: str


class MatchedRow(BaseModel):
    """A database row from the enterprise system that matched a chunk citation."""
    source_table: str
    row_pk: str
    confidence: float
    match_method: str  # exact_key | normalized_key | semantic | bm25 | metadata_match | hybrid
    row_data: dict[str, Any]  # all columns from the source row
    evidence: str  # snippet from the chunk that triggered the match
    matched_metadata_fields: list[str] = []  # NEW: Which metadata fields matched
    match_details: dict[str, Any] = {}  # NEW: Score breakdown
    chunk_matches: list[ChunkMatchDetail] = []  # NEW: Details per chunk


class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    file_name: str
    score: float
    vector_score: float | None = None
    bm25_score: float | None = None
    block_type: str
    page_start: int | None = None
    page_end: int | None = None
    text_content: str
    meta: dict[str, Any] | None = None


class RagQueryResponse(BaseModel):
    model_config = {"protected_namespaces": ()}

    query_id: str
    query_type: str
    answer: str
    confidence: float
    citations: list[Citation]
    matched_rows: list[MatchedRow] = []  # full structured rows from enterprise DB
    latency_ms: int
    model_name: str


class RagDebugResponse(RagQueryResponse):
    model_config = {"protected_namespaces": ()}

    retrieved_chunks: list[RetrievedChunk]
    stages: dict[str, Any]


# ---------- Row index ----------
class RowIndexTable(BaseModel):
    source_table: str
    row_count: int


class RowIndexUploadResponse(BaseModel):
    table_name: str
    rows_inserted: int
    rows_updated: int
    total_rows_in_index: int
    columns_detected: list[str]
    pk_column: str


# ---------- Feedback ----------
class FeedbackRequest(BaseModel):
    query_id: str
    answer_id: str
    feedback_type: str  # helpful | not_helpful | wrong_citation | hallucination | ...
    rating: int | None = Field(None, ge=1, le=5)
    comment: str | None = None
    correction: dict[str, Any] | None = None


class FeedbackResponse(BaseModel):
    id: str
    status: str
