"""Structure-aware chunker.

Produces three kinds of chunks per section 19 of the spec:
  - paragraph chunks (heading-aware, with overlap)
  - table_summary chunks (one per detected table)
  - table_row chunks (one per row, rendered as semantic text)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.logger import logger
from app.services.extraction_service import ExtractedDocument
from app.services.section_classifier import section_classifier
from app.utils.entity_extraction import extract_entities
from app.utils.text_normalization import normalize_text


@dataclass
class Chunk:
    chunk_index: int
    block_type: str  # paragraph | table_summary | table_row
    text_content: str
    page_start: int | None = None
    page_end: int | None = None
    section_label: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def normalized_text(self) -> str:
        return normalize_text(self.text_content)


# 7.8 AC1 — chunk size is admin-configurable but bounded to 256–1024 tokens.
MIN_CHUNK_TOKENS = 256
MAX_CHUNK_TOKENS = 1024


def clamp_chunk_tokens(requested: int) -> int:
    """Clamp a requested chunk-token budget into the supported 256–1024 range (7.8 AC1)."""
    try:
        v = int(requested)
    except (TypeError, ValueError):
        v = 512
    return max(MIN_CHUNK_TOKENS, min(MAX_CHUNK_TOKENS, v))


class Chunker:
    def __init__(
        self,
        chunk_size_tokens: int | None = None,
        overlap_tokens: int | None = None,
    ) -> None:
        # 7.8 AC1 — honour the admin/config value but never exceed the 256–1024 bound.
        self.chunk_size_tokens = clamp_chunk_tokens(chunk_size_tokens or settings.chunk_size_tokens)
        # Rough token budget — we use word count as a cheap proxy (~1.3 tok/word).
        self.chunk_size_words = int(self.chunk_size_tokens / 1.3)
        self.overlap_words = int((overlap_tokens or settings.chunk_overlap_tokens) / 1.3)

    def chunk(self, doc: ExtractedDocument) -> list[Chunk]:
        chunks: list[Chunk] = []
        idx = 0

        for page in doc.pages:
            # ---- Paragraph chunks ----
            for para_chunk in self._chunk_page_text(page.text):
                section, _ = section_classifier.classify(para_chunk)
                entities = extract_entities(para_chunk)
                chunks.append(Chunk(
                    chunk_index=idx,
                    block_type="paragraph",
                    text_content=para_chunk,
                    page_start=page.page_number,
                    page_end=page.page_number,
                    section_label=section,
                    meta={"entities": entities},
                ))
                idx += 1

            # ---- Table chunks ----
            for t_i, table in enumerate(page.tables):
                # Table summary chunk
                summary_text = self._render_table_summary(table, t_i + 1)
                chunks.append(Chunk(
                    chunk_index=idx,
                    block_type="table_summary",
                    text_content=summary_text,
                    page_start=page.page_number,
                    page_end=page.page_number,
                    meta={"table_index": t_i, "num_rows": len(table) - 1},
                ))
                idx += 1

                # Per-row chunks (skipping header row)
                if len(table) >= 2:
                    headers = table[0]
                    for row_i, row in enumerate(table[1:], start=1):
                        row_text = self._render_table_row(headers, row, t_i + 1, row_i)
                        entities = extract_entities(row_text)
                        chunks.append(Chunk(
                            chunk_index=idx,
                            block_type="table_row",
                            text_content=row_text,
                            page_start=page.page_number,
                            page_end=page.page_number,
                            meta={
                                "table_index": t_i,
                                "row_index": row_i,
                                "headers": headers,
                                "cells": dict(zip(headers, row, strict=False)),
                                "entities": entities,
                            },
                        ))
                        idx += 1

            # ---- Image chunks ----
            # Each image becomes a searchable chunk containing its OCR /
            # vision-extracted text plus metadata. If the image contained
            # a table, that table was ALREADY folded into page.tables
            # above and produced its own table_row chunks — this image
            # chunk then serves as a companion record for the figure.
            for img in page.images:
                ocr = (img.ocr_text or "").strip()
                method = getattr(img, "extraction_method", "none")
                vision_type = getattr(img, "vision_image_type", "")
                vision_raw = getattr(img, "vision_raw", "")
                injected_n = getattr(img, "injected_table_count", 0)
                if ocr:
                    text_content = (
                        f"[Image, page {page.page_number}, "
                        f"{img.width}x{img.height} {img.format}, "
                        f"extracted via {method}]\n{ocr}"
                    )
                else:
                    text_content = (
                        f"[Image, page {page.page_number}, "
                        f"{img.width}x{img.height} {img.format}] "
                        f"No text could be extracted."
                    )
                img_entities = getattr(img, "entities", None) or (
                    extract_entities(ocr) if ocr else {}
                )
                section, _ = section_classifier.classify(ocr) if ocr else (None, 0.0)
                chunks.append(Chunk(
                    chunk_index=idx,
                    block_type="image",
                    text_content=text_content,
                    page_start=page.page_number,
                    page_end=page.page_number,
                    section_label=section,
                    meta={
                        "image_index": img.image_index,
                        "width": img.width,
                        "height": img.height,
                        "format": img.format,
                        "image_url": img.url_path,
                        "image_source": img.source,
                        "extraction_method": method,
                        "vision_image_type": vision_type,
                        "vision_raw_response": vision_raw,
                        "vision_injected_table_count": injected_n,
                        "ocr_length": len(ocr),
                        "entities": img_entities,
                    },
                ))
                idx += 1

        logger.info(
            "Chunked | file={} | total_chunks={} | paragraph={} | "
            "table_summary={} | table_row={} | image={}",
            doc.file_name,
            len(chunks),
            sum(1 for c in chunks if c.block_type == "paragraph"),
            sum(1 for c in chunks if c.block_type == "table_summary"),
            sum(1 for c in chunks if c.block_type == "table_row"),
            sum(1 for c in chunks if c.block_type == "image"),
        )
        return chunks

    # ---------- helpers ----------
    def _chunk_page_text(self, text: str) -> list[str]:
        """Split page text into overlapping word-windowed chunks."""
        if not text or not text.strip():
            return []
        words = text.split()
        if len(words) <= self.chunk_size_words:
            return [text.strip()]

        chunks: list[str] = []
        step = max(1, self.chunk_size_words - self.overlap_words)
        for start in range(0, len(words), step):
            piece = " ".join(words[start : start + self.chunk_size_words])
            if piece.strip():
                chunks.append(piece.strip())
            if start + self.chunk_size_words >= len(words):
                break
        return chunks

    def _render_table_summary(self, table: list[list[str]], table_num: int) -> str:
        if not table:
            return ""
        headers = table[0]
        row_count = max(0, len(table) - 1)
        return (
            f"Table {table_num}. Columns: {', '.join(headers)}. "
            f"Total rows: {row_count}."
        )

    def _render_table_row(
        self, headers: list[str], row: list[str], table_num: int, row_num: int
    ) -> str:
        parts = [f"Table {table_num}. Row {row_num}."]
        for h, c in zip(headers, row, strict=False):
            if h and c:
                parts.append(f"{h} {c}.")
        return " ".join(parts)


chunker = Chunker()
