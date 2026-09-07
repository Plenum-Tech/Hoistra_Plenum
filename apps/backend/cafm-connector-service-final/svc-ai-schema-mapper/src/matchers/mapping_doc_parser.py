"""Parse customer mapping documents (JSON or unstructured text).

Customers may provide mapping guidance in:
1. JSON format: {raw_field: canonical_field, ...}
2. Unstructured text: "Asset Code → asset_code, WO Priority → wo_priority, ..."
"""

import json
import logging
import re
from typing import Optional

from anthropic import AsyncAnthropic

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


def parse_mapping_doc(content: str) -> dict[str, str]:
    """
    Parse a mapping document (JSON or unstructured text).

    Args:
        content: File content (string) uploaded by customer

    Returns:
        dict[source_field] = canonical_field mapping

    Note:
        If JSON parsing fails, falls back to Haiku LLM extraction.
    """

    # Try JSON first
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            # Normalize keys to lowercase
            return {k.lower().strip(): v.lower().strip() for k, v in parsed.items()}
    except json.JSONDecodeError:
        pass

    # Try simple key:value or → pairs (unstructured text)
    result = _parse_text_mapping(content)
    if result:
        return result

    # If both fail, return empty dict (no mapping guidance)
    logger.warning("Could not parse mapping document; treating as empty mapping")
    return {}


def _parse_text_mapping(text: str) -> Optional[dict[str, str]]:
    """
    Parse unstructured mapping text.

    Patterns:
    - "Asset Code → asset_code"
    - "Asset Code: asset_code"
    - "Asset Code = asset_code"
    - "Asset Code maps to asset_code"
    """

    result = {}

    # Try → delimiter (most common in docs)
    lines = text.split("\n")
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Try → arrow
        if "→" in line:
            parts = line.split("→")
            if len(parts) == 2:
                source = parts[0].strip().lower()
                target = parts[1].strip().lower()
                if source and target:
                    result[source] = target
                    continue

        # Try other delimiters
        for delim in [": ", " = ", ": ", " -> ", " maps to "]:
            if delim in line:
                parts = line.split(delim, 1)
                if len(parts) == 2:
                    source = parts[0].strip().lower()
                    target = parts[1].strip().lower()
                    if source and target:
                        result[source] = target
                    break

    return result if result else None


async def parse_mapping_doc_with_llm(
    content: str,
    client: AsyncAnthropic,
) -> dict[str, str]:
    """
    Parse mapping document using Claude Haiku (if text parsing fails).

    Called only if _parse_text_mapping returns None or empty.

    Args:
        content: File content string
        client: AsyncAnthropic client

    Returns:
        dict[source_field] = canonical_field mapping
    """

    prompt = f"""Extract the field mapping from this document.
Return a JSON object: {{"source_field": "canonical_field", ...}}

Document:
{content}

Return ONLY valid JSON, no markdown."""

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        )

        response_text = response.content[0].text.strip()
        parsed = json.loads(response_text)

        if isinstance(parsed, dict):
            return {k.lower().strip(): v.lower().strip() for k, v in parsed.items()}

        return {}

    except Exception as e:
        logger.warning(f"LLM mapping document parsing failed: {e}")
        return {}
