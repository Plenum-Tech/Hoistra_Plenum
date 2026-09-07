"""Dataset semantic description via Claude Haiku.

Strategy 4 fallback: When Strategies 1-3 (exact/alias/regex) don't provide
sufficient confidence, Haiku provides semantic field descriptions.
"""

import json
import logging
import re
from typing import Optional

from anthropic import AsyncAnthropic

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


async def describe_dataset(
    df_head_str: str,
    column_names: list[str],
    client: AsyncAnthropic,
) -> dict[str, str]:
    """
    Generate semantic descriptions for each column using Claude Haiku.

    Single Haiku call with structured output. OTel span tagged with node1 + dataset_description.

    Args:
        df_head_str: String representation of first 5 rows (pandas.to_string())
        column_names: List of column names from the dataframe
        client: Async Anthropic client

    Returns:
        dict[column_name] = "semantic description" (max 20 words per column)
    """

    prompt = f"""Analyze this dataset and describe each column semantically.

Columns: {', '.join(column_names)}

Sample data (first 5 rows):
{df_head_str}

Return a JSON object with column names as keys and semantic descriptions (max 20 words) as values.
Focus on: what data type, what business meaning, common values.

Example:
{{
  "asset_code": "Unique identifier for equipment/assets",
  "open_work_orders": "Count of unresolved maintenance requests"
}}

Return ONLY valid JSON, no markdown, no explanation."""

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        )

        # Extract JSON from response
        if not response.content or not response.content[0]:
            logger.warning(f"Haiku returned empty response content")
            return _fallback_descriptions(column_names)

        response_text = response.content[0].text.strip() if hasattr(response.content[0], 'text') else ""

        if not response_text:
            logger.warning(f"Haiku returned empty text body (response.content[0].text is empty)")
            return _fallback_descriptions(column_names)

        logger.debug(f"Haiku raw response (first 200 chars): {response_text[:200]}")

        # Try to parse as JSON
        try:
            descriptions = json.loads(response_text)
        except json.JSONDecodeError as e:
            # Maybe it's wrapped in markdown code blocks
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
                descriptions = json.loads(response_text)
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()
                descriptions = json.loads(response_text)
            else:
                raise

        # Ensure all columns are in the result
        result = {}
        for col in column_names:
            result[col] = descriptions.get(col, "Unknown field type")

        logger.info(f"Dataset descriptions generated for {len(column_names)} columns")
        return result

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse Haiku response as JSON: {e}. Using fallback descriptions.")
        # Fallback: generic descriptions based on column name patterns
        return _fallback_descriptions(column_names)

    except Exception as e:
        logger.error(f"Failed to call Haiku for dataset description: {e}. Using fallback descriptions.")
        return _fallback_descriptions(column_names)


def _name_match_table(table_name: str, cafm_tables: list[str]) -> Optional[str]:
    """Match a source sheet/table name to a real plenum_cafm table.

    Singular/plural/space-or-underscore tolerant (e.g. "Work Orders" → work_orders,
    "sites" → sites). Returns the real CAFM table name, or None if no name match.
    """
    t = table_name.lower().strip()
    variants = {t, t.replace(" ", "_"), t.rstrip("s"), t.replace(" ", "_").rstrip("s")}
    for tgt in cafm_tables:
        tl = tgt.lower()
        if tl in variants or tl.rstrip("s") in variants:
            return tgt
    return None


def _norm_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")


def _column_pair_score(src_norm: str, dst_norm: str) -> float:
    """0..1 single-pair column similarity.

    1.0 exact normalised match, 0.8 strict token containment (one token-set is a
    subset of the other), 0 otherwise. Token containment catches the CMMS aliasing
    that Levenshtein and substring matching miss:
      tagnum   → asset_tag    (token 'tag' shared)? No — tokens are 'tagnum' vs 'asset','tag'.
      product_name → asset_name  (token 'name' shared in subset)
      condition → condition       (exact)
    """
    if not src_norm or not dst_norm:
        return 0.0
    if src_norm == dst_norm:
        return 1.0
    st = set(src_norm.split("_"))
    dt = set(dst_norm.split("_"))
    if not st or not dt:
        return 0.0
    if st.issubset(dt) or dt.issubset(st):
        return 0.8
    return 0.0


def _column_overlap_score(source_cols: list[str], dest_cols: list[str]) -> float:
    """0..1 average best-match column similarity between a source sheet's columns
    and a candidate destination table's columns. Each source column scores its
    BEST match against the destination set; the mean across source columns is the
    table-level overlap score.

    Catches the 'works → assets vs work_orders' mis-routing: source
    (tagnum, id, product_name, make, property_ref, condition) scores ~0.6 against
    assets (id, asset_tag, asset_name, manufacturer, model, site_id, status,
    condition, ...) but only ~0.17 against work_orders (id, asset_id,
    description, priority, status, raised_date) — the LLM never gets fooled by
    the name 'works' ↔ 'work_orders' similarity.
    """
    if not source_cols or not dest_cols:
        return 0.0
    dest_norm = [_norm_col(c) for c in dest_cols if c]
    if not dest_norm:
        return 0.0
    total = 0.0
    for sc in source_cols:
        sn = _norm_col(sc)
        if not sn:
            continue
        total += max((_column_pair_score(sn, dn) for dn in dest_norm), default=0.0)
    return round(total / max(1, len([c for c in source_cols if c])), 4)


# A column-overlap pre-check is accepted ONLY when the winner has a meaningful
# lead over the runner-up AND its absolute score is non-trivial. Otherwise we
# still pass to the LLM with both candidates visible. Lead is measured as a
# RATIO (winner ≥ 1.6 × runner-up) so a clear 2x signal counts even when the
# absolute margin is small — e.g. assets 0.33 vs work_orders 0.17 for source
# 'works' (margin 0.16 but ratio 1.94x → confidently accept assets).
_OVERLAP_ACCEPT_MIN_ABS = 0.30   # winner must score at least this on its own
_OVERLAP_ACCEPT_RATIO = 1.6      # winner must score this many × the runner-up
_OVERLAP_ACCEPT_MARGIN = 0.15    # OR beat runner-up by this absolute amount


async def match_tables_to_cafm(
    source_tables: dict[str, list[str]],
    cafm_tables: list[str],
    client: AsyncAnthropic,
    cafm_columns_by_table: Optional[dict[str, list[str]]] = None,
) -> tuple[dict[str, Optional[str]], dict[str, float]]:
    """Map each source sheet/table to the best-fitting plenum_cafm table.

    Two-pass, mirrors the deterministic mapper's table routing:
      1. Deterministic name match (singular/plural/space tolerant).
      2. One Haiku call for the leftovers (e.g. "sites_2" → "sites"), using the
         sheet's column names as the signal — Haiku also returns a 0-100 match %.

    Only ever returns a table that actually exists in ``cafm_tables`` (or None),
    so the LLM can't invent a target. Non-fatal: on any error the unmatched
    sheets resolve to None and the card simply shows "no match".

    Args:
        source_tables: {sheet_name: [column names]}
        cafm_tables:   real plenum_cafm table names
        client:        Async Anthropic client

    Returns:
        (matches, confidences) where
          matches      = {sheet_name: cafm_table_name | None}
          confidences  = {sheet_name: 0.0-1.0}  (1.0 = exact name match, Haiku %
                         for semantic guesses, 0.0 when unmatched)
    """
    matches: dict[str, Optional[str]] = {}
    confidences: dict[str, float] = {}
    cafm_by_lower = {t.lower(): t for t in cafm_tables}

    unmatched: dict[str, list[str]] = {}
    for tname, cols in source_tables.items():
        hit = _name_match_table(tname, cafm_tables)
        if hit:
            matches[tname] = hit
            confidences[tname] = 1.0  # exact/normalized name match
        else:
            unmatched[tname] = cols

    if not unmatched or not cafm_tables:
        for tname in unmatched:
            matches.setdefault(tname, None)
            confidences.setdefault(tname, 0.0)
        return matches, confidences

    # ── Pre-pass: deterministic column-overlap pre-check ─────────────────────
    # Catches cases where the source NAME misleads (e.g. 'works' looks like
    # 'work_orders' but its columns are an asset master). For each unmatched
    # source, score every candidate destination by mean best-column-match. Accept
    # the winner only when it has a meaningful lead — otherwise still send to the
    # LLM so it can use semantic judgement on ambiguous cases.
    if cafm_columns_by_table:
        _cafm_cols_lc = {k.lower(): v for k, v in cafm_columns_by_table.items()}
        still_unmatched: dict[str, list[str]] = {}
        for tname, cols in unmatched.items():
            scores = sorted(
                (
                    (_column_overlap_score(cols, _cafm_cols_lc.get(dest.lower(), [])), dest)
                    for dest in cafm_tables
                ),
                key=lambda kv: kv[0],
                reverse=True,
            )
            best_score, best_dest = scores[0] if scores else (0.0, None)
            second_score = scores[1][0] if len(scores) > 1 else 0.0
            margin = best_score - second_score
            ratio = best_score / second_score if second_score > 0 else float("inf")
            accept = (
                best_dest is not None
                and best_score >= _OVERLAP_ACCEPT_MIN_ABS
                and (ratio >= _OVERLAP_ACCEPT_RATIO or margin >= _OVERLAP_ACCEPT_MARGIN)
            )
            if accept:
                matches[tname] = best_dest
                confidences[tname] = round(min(0.95, 0.60 + best_score * 0.4), 4)
                logger.info(
                    f"match_tables_to_cafm: column-overlap routed "
                    f"{tname!r} → {best_dest!r} "
                    f"(score={best_score}, margin={round(margin,4)}, ratio={round(ratio,2)})"
                )
            else:
                still_unmatched[tname] = cols
        unmatched = still_unmatched

    if not unmatched:
        return matches, confidences

    sheets_block = "\n".join(
        f"- {name}: {', '.join(cols[:25]) or '(no columns)'}"
        for name, cols in unmatched.items()
    )
    prompt = f"""You are matching spreadsheet sheets to the closest existing database table.

Target database tables (choose ONLY from this list, or null if none fit):
{', '.join(cafm_tables)}

Sheets to match (name + sample columns):
{sheets_block}

CRITICAL — the sheet NAME can be misleading:
  • 'works' is often an ASSET MASTER (columns like tagnum, product_name, make,
    model, manufacturer, location) — NOT a work-order table. Route by columns,
    not by 'works' ↔ 'work_orders' name similarity.
  • 'inventory' may be assets, materials, or stock — route by columns.
  • 'tickets' may be work_orders, incidents, or requests — route by columns.

For each sheet, pick the single best-fitting target table BY THE MEANING OF THE
COLUMNS. The columns are the truth; the sheet name is just a hint and may be
misleading. Give a match confidence from 0 to 100. If nothing fits, use null
with confidence 0.

Return ONLY a JSON object mapping each sheet name to an object
{{"table": <target table or null>, "confidence": <0-100>}}.
Example: {{"sites_2": {{"table": "sites", "confidence": 92}}, "misc_notes": {{"table": null, "confidence": 0}}}}"""

    try:
        from .fm_ontology import FM_PERSONA

        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=FM_PERSONA,  # FM domain personality for table-to-entity matching
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = (
            response.content[0].text.strip()
            if response.content and hasattr(response.content[0], "text")
            else ""
        )
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
        parsed = json.loads(response_text) if response_text else {}
    except Exception as e:  # noqa: BLE001 — non-fatal, fall back to no match
        logger.warning(f"match_tables_to_cafm: Haiku table match failed ({e}); leaving unmatched")
        parsed = {}

    for tname in unmatched:
        raw = parsed.get(tname)
        # Backward/robustness: accept either {"table","confidence"} or a bare string.
        if isinstance(raw, dict):
            tgt_raw = raw.get("table")
            conf_raw = raw.get("confidence")
        else:
            tgt_raw, conf_raw = raw, None
        target = cafm_by_lower.get(str(tgt_raw).lower()) if tgt_raw else None
        matches[tname] = target
        if target:
            try:
                conf = float(conf_raw) / 100.0 if conf_raw is not None else 0.85
            except (ValueError, TypeError):
                conf = 0.85
            confidences[tname] = max(0.0, min(1.0, conf))
        else:
            confidences[tname] = 0.0

    return matches, confidences


def _fallback_descriptions(column_names: list[str]) -> dict[str, str]:
    """
    Fallback: Generate generic descriptions based on column name patterns.

    Used if Haiku call fails or returns unparseable JSON.
    """
    result = {}
    for col in column_names:
        lower = col.lower()
        if any(x in lower for x in ["code", "id", "num", "number"]):
            result[col] = "Unique identifier or reference code"
        elif any(x in lower for x in ["name", "description", "desc", "title"]):
            result[col] = "Text description or human-readable name"
        elif any(x in lower for x in ["date", "time", "datetime", "ts", "created", "due"]):
            result[col] = "Date or timestamp value"
        elif any(x in lower for x in ["qty", "quantity", "count", "number", "amount"]):
            result[col] = "Numeric quantity or count"
        elif any(x in lower for x in ["status", "state", "priority", "level", "type"]):
            result[col] = "Categorical value (enum)"
        else:
            result[col] = "Text or numeric field"
    return result
