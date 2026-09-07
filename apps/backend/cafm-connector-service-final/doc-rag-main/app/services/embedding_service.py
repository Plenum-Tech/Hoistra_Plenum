"""Embedding service.

Uses OpenAI embeddings when OPENAI_API_KEY is set. Otherwise falls back
to a deterministic hash-based pseudo-embedding so the whole pipeline
can be tested end-to-end with zero API calls or cost.
"""
from __future__ import annotations

import hashlib
import time

import numpy as np
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logger import logger


class EmbeddingService:
    def __init__(self) -> None:
        self.model = settings.openai_embedding_model
        self.dim = settings.openai_embedding_dim
        self.mock = settings.is_mock_mode
        self._client = None
        if not self.mock:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=settings.openai_api_key)
                logger.info("EmbeddingService initialized | model={} | dim={}",
                            self.model, self.dim)
            except Exception as e:
                logger.error("Failed to init OpenAI client, falling back to mock: {}", e)
                self.mock = True
        if self.mock:
            logger.warning("EmbeddingService running in MOCK mode (no OpenAI key)")

    def embed_text(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # Replace empty strings with a single space to avoid API errors.
        clean = [t if t and t.strip() else " " for t in texts]
        t0 = time.time()
        if self.mock:
            result = [self._mock_embed(t) for t in clean]
        else:
            try:
                result = self._embed_openai(clean)
            except Exception as e:
                logger.exception("OpenAI embedding failed, using mock fallback: {}", e)
                result = [self._mock_embed(t) for t in clean]
        logger.info(
            "EmbeddingService.embed_batch | count={} | mode={} | dim={} | ms={:.1f}",
            len(texts), "mock" if self.mock else "openai", self.dim,
            (time.time() - t0) * 1000,
        )
        return result

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        logger.debug("OpenAI embedding | count={} | model={}", len(texts), self.model)
        resp = self._client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in resp.data]

    def _mock_embed(self, text: str) -> list[float]:
        """Deterministic pseudo-embedding derived from token hashes.

        Each token contributes to a bucket; vector is L2-normalized.
        Same text → same vector. Similar texts share tokens and so share
        direction, giving a (very weak but working) semantic signal.
        """
        vec = np.zeros(self.dim, dtype=np.float32)
        for tok in text.lower().split():
            h = hashlib.md5(tok.encode("utf-8")).digest()
            bucket = int.from_bytes(h[:4], "little") % self.dim
            sign = 1.0 if h[4] % 2 == 0 else -1.0
            vec[bucket] += sign
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return vec.tolist()


embedding_service = EmbeddingService()
