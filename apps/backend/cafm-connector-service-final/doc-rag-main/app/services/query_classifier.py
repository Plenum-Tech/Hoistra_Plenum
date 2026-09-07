"""Query classifier.

Lightweight heuristic router that labels a query as one of:
  paragraph | table_lookup | row_grounding | comparison | summarization | lookup
"""
from __future__ import annotations

from app.core.logger import logger
from app.utils.entity_extraction import extract_keys
from app.utils.text_normalization import normalize_text

_TABLE_HINTS = ["how much", "how many", "what is the", "frequency", "rate", "credit",
                "percent", "threshold", "limit", "uptime", "response time"]
_COMPARE_HINTS = ["compare", "vs", "versus", "difference between", "across"]
_SUMMARY_HINTS = ["summarize", "summary of", "overview", "brief"]


class QueryClassifier:
    def classify(self, query: str) -> dict:
        norm = normalize_text(query)
        keys = extract_keys(query)

        query_type = "paragraph"
        hint_matched = None
        if any(h in norm for h in _COMPARE_HINTS):
            query_type = "comparison"
            hint_matched = next(h for h in _COMPARE_HINTS if h in norm)
        elif any(h in norm for h in _SUMMARY_HINTS):
            query_type = "summarization"
            hint_matched = next(h for h in _SUMMARY_HINTS if h in norm)
        elif keys:
            query_type = "row_grounding"
            hint_matched = f"entity_keys={keys}"
        elif any(h in norm for h in _TABLE_HINTS):
            query_type = "table_lookup"
            hint_matched = next(h for h in _TABLE_HINTS if h in norm)

        result = {
            "query_type": query_type,
            "entity_keys": keys,
            "table_bias": query_type in ("table_lookup", "row_grounding"),
            "row_bias": query_type == "row_grounding",
        }

        logger.info(
            "QueryClassifier | type={} | entity_keys={} | table_bias={} | "
            "row_bias={} | hint='{}' | q='{}'",
            query_type, keys, result["table_bias"], result["row_bias"],
            hint_matched, query[:80],
        )
        return result


query_classifier = QueryClassifier()
