"""Embedding utilities for semantic field mapping (Tier 2).

Uses OpenAI text-embedding-3-small for cosine similarity matching.
Caches canonical field embeddings at startup.
"""

import logging
from typing import Optional

import numpy as np
from openai import AsyncOpenAI

from cafm_shared.logging import get_logger
logger = get_logger(__name__)

# Module-level cache for canonical field embeddings
_CANONICAL_EMBEDDINGS_CACHE: dict[str, np.ndarray] = {}


async def initialize_canonical_embeddings(
    client: AsyncOpenAI,
    canonical_fields: dict[str, str],
) -> None:
    """
    Pre-compute embeddings for all canonical field descriptions.

    Called once at app startup (lifespan).

    Args:
        client: AsyncOpenAI client
        canonical_fields: dict[canonical_field_name] = "description"

    Example:
        canonical_fields = {
            "asset_code": "Unique identifier for equipment or asset",
            "wo_code": "Work order identifier",
            ...
        }
    """

    global _CANONICAL_EMBEDDINGS_CACHE

    if _CANONICAL_EMBEDDINGS_CACHE:
        logger.info("Canonical embeddings already cached")
        return

    logger.info(f"Initializing embeddings for {len(canonical_fields)} canonical fields...")

    try:
        # Prepare texts: "field_name | description"
        texts_to_embed = [
            f"{field_name} | {desc}" for field_name, desc in canonical_fields.items()
        ]

        # Call OpenAI embeddings API
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=texts_to_embed,
        )

        # Store embeddings in cache
        for i, embedding_data in enumerate(response.data):
            field_name = list(canonical_fields.keys())[i]
            embedding_array = np.array(embedding_data.embedding, dtype=np.float32)
            _CANONICAL_EMBEDDINGS_CACHE[field_name] = embedding_array

        logger.info(f"Cached {len(_CANONICAL_EMBEDDINGS_CACHE)} canonical field embeddings")

    except Exception as e:
        logger.error(f"Failed to initialize canonical embeddings: {e}")
        # Continue anyway — semantic mapper will fail gracefully if cache is empty


async def embed_text(client: AsyncOpenAI, text: str) -> Optional[np.ndarray]:
    """
    Embed a single text string.

    Args:
        client: AsyncOpenAI client
        text: Text to embed

    Returns:
        Embedding vector as numpy array, or None if failed
    """

    try:
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )

        if response.data:
            embedding = np.array(response.data[0].embedding, dtype=np.float32)
            return embedding

        return None

    except Exception as e:
        logger.warning(f"Failed to embed text: {e}")
        return None


async def embed_texts_batch(
    client: AsyncOpenAI,
    texts: list[str],
) -> dict[str, Optional[np.ndarray]]:
    """
    Embed multiple texts in a single API call (batch).

    MUCH more efficient than calling embed_text() in a loop.
    OpenAI API supports up to 2000 texts per request.

    Args:
        client: AsyncOpenAI client
        texts: List of texts to embed

    Returns:
        Dict mapping text → embedding vector (or None if that text failed)
    """

    if not texts:
        return {}

    try:
        logger.info(f"Embedding {len(texts)} texts in batch...")

        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=texts,
        )

        # Map texts to their embeddings
        embeddings_dict = {}
        for i, embedding_data in enumerate(response.data):
            if i < len(texts):
                text = texts[i]
                embedding = np.array(embedding_data.embedding, dtype=np.float32)
                embeddings_dict[text] = embedding

        logger.info(f"Successfully embedded {len(embeddings_dict)} texts")
        return embeddings_dict

    except Exception as e:
        logger.error(f"Batch embedding failed: {e}")
        # Return empty dict on failure
        return {}


def cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    """
    Compute cosine similarity between two embedding vectors.

    Args:
        vec1: First embedding vector
        vec2: Second embedding vector

    Returns:
        Cosine similarity score (0.0–1.0)
    """

    # Normalize vectors
    vec1_norm = vec1 / (np.linalg.norm(vec1) + 1e-8)
    vec2_norm = vec2 / (np.linalg.norm(vec2) + 1e-8)

    # Compute dot product (cosine similarity)
    similarity = np.dot(vec1_norm, vec2_norm)

    # Clamp to [0, 1]
    return max(0.0, min(1.0, float(similarity)))


def find_top_matches(
    source_embedding: np.ndarray,
    top_k: int = 3,
) -> list[tuple[str, float]]:
    """
    Find top-k canonical fields by cosine similarity.

    Uses cached canonical embeddings.

    Args:
        source_embedding: Embedding vector of source field
        top_k: Number of top matches to return

    Returns:
        List of (canonical_field_name, similarity_score) tuples, sorted by score descending
    """

    if not _CANONICAL_EMBEDDINGS_CACHE:
        logger.warning("Canonical embeddings cache is empty")
        return []

    similarities = {}
    for field_name, cached_embedding in _CANONICAL_EMBEDDINGS_CACHE.items():
        similarity = cosine_similarity(source_embedding, cached_embedding)
        similarities[field_name] = similarity

    # Sort by similarity descending
    sorted_matches = sorted(similarities.items(), key=lambda x: x[1], reverse=True)

    return sorted_matches[:top_k]


def score_canonical_fields(
    source_embedding: np.ndarray,
    field_names: list[str],
) -> dict[str, float]:
    """Cosine similarity from a source embedding to named canonical fields (cache lookup)."""
    if not _CANONICAL_EMBEDDINGS_CACHE or source_embedding is None:
        return {}

    scores: dict[str, float] = {}
    for name in field_names:
        key = (name or "").strip()
        if not key:
            continue
        cached = _CANONICAL_EMBEDDINGS_CACHE.get(key)
        if cached is None:
            continue
        scores[key] = cosine_similarity(source_embedding, cached)
    return scores


def get_cached_embeddings() -> dict[str, np.ndarray]:
    """Get reference to cached canonical embeddings (read-only)."""
    return _CANONICAL_EMBEDDINGS_CACHE.copy()
