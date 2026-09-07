"""Node 2.5: Pre-Semantic Preprocessing — enrich unmapped fields before embedding.

Runs between Node 2 (deterministic) and Node 3 (semantic).

Purpose:
  Node 3 builds embedding text as:  "{field_name} | {description} | {data_type}"
  Richer, human-readable text → better cosine similarity → fewer fields land in
  tier2_flagged and more auto-accept at ≥ 0.85 confidence.

Steps:
  1. Normalize vendor field names   — strip Hungarian prefixes (Fiix), split
     camelCase / PascalCase, expand abbreviations
  2. Enrich descriptions            — build a meaningful description when none exists,
     incorporating table context, normalized name, and data-type hints
  3. Populate source_table          — fill in any missing source_table tags by
     reverse-looking up field_name in external_tables
  4. Deduplicate cross-table fields — fields whose normalized names appear in
     multiple tables get an extra disambiguation note in their description
  5. DB step-write                  — saves a lightweight progress record so the
     frontend can display "Pre-semantic preprocessing complete"

State consumed:  unmapped_after_t1, external_tables
State produced:  unmapped_after_t1  (enriched in-place), preprocessing_stats (new)
"""

import logging
import re
from typing import Dict, List, Optional

from ..schema_state import SchemaMappingState, SchemaMappingFieldInfo

from cafm_shared.logging import get_logger
logger = get_logger(__name__)

# ── Hungarian-notation prefix map (Fiix CMMS) ────────────────────────────────
# Maps the short prefix → human-readable type hint used in description building.
_HUNGARIAN_PREFIX: Dict[str, str] = {
    "str": "text",
    "int": "integer",
    "dbl": "decimal",
    "dtm": "datetime",
    "bol": "boolean",
    "qty": "quantity",
    "flt": "float",
    "lng": "long integer",
    "byt": "byte",
    "cur": "currency",
    "obj": "object",
}

# Minimum prefix length so we don't false-positive on field names like "status"
_MIN_PREFIX_LEN = 3

# Common CMMS abbreviation expansions used when building rich descriptions
_ABBREV_MAP: Dict[str, str] = {
    "id": "ID",
    "wo": "work order",
    "pm": "preventive maintenance",
    "sm": "scheduled maintenance",
    "fk": "foreign key",
    "num": "number",
    "qty": "quantity",
    "dt": "date",
    "dtm": "datetime",
    "desc": "description",
    "loc": "location",
    "cat": "category",
    "inv": "inventory",
    "po": "purchase order",
    "rfq": "request for quote",
    "bom": "bill of materials",
    "rca": "root cause analysis",
    "acct": "account",
    "addr": "address",
    "dept": "department",
    "grp": "group",
    "mgr": "manager",
    "org": "organization",
    "pct": "percentage",
    "ref": "reference",
    "seq": "sequence",
    "svc": "service",
    "sys": "system",
    "tech": "technician",
    "uom": "unit of measure",
    "usr": "user",
    "val": "value",
    "ver": "version",
    "wh": "warehouse",
    "wr": "work request",
    "xref": "cross reference",
}


# ── Public node entry point ───────────────────────────────────────────────────

async def schema_preprocess_node(state: SchemaMappingState) -> SchemaMappingState:
    """
    Node 2.5: Enrich unmapped fields before semantic embedding.

    This is a pure pre-processing step — no LLM calls, no external I/O.
    Modifies unmapped_after_t1 in-place with richer descriptions and
    normalised source_table tags.
    """

    schema_mapping_id = state.get("schema_mapping_id")
    unmapped_after_t1: List[SchemaMappingFieldInfo] = state.get("unmapped_after_t1", [])
    external_tables: dict = state.get("external_tables", {})

    logger.info(
        f"[Node 2.5] Pre-semantic preprocessing: {len(unmapped_after_t1)} unresolved fields"
    )

    if not unmapped_after_t1:
        logger.info("[Node 2.5] Nothing to preprocess — skipping")
        state["preprocessing_stats"] = {"fields_processed": 0, "descriptions_enriched": 0}
        state["status"] = "semantic"
        return state

    # ── Build field→table reverse lookup ────────────────────────────────────
    _field_to_tables: Dict[str, List[str]] = {}
    for table_name, table_info in external_tables.items():
        for col in table_info.get("columns", []):
            fn = col.get("field_name")
            if fn:
                _field_to_tables.setdefault(fn, []).append(table_name)

    # ── Collect normalized names to detect cross-table duplicates ────────────
    # key: normalized_name → list of (source_field, source_table) tuples
    normalized_index: Dict[str, List[tuple]] = {}

    enriched = 0
    source_table_filled = 0

    for field_info in unmapped_after_t1:
        source_field: str = field_info.get("field_name", "")
        if not source_field:
            continue

        # 1. Fill missing source_table ─────────────────────────────────────
        if not field_info.get("source_table"):
            tables_for_field = _field_to_tables.get(source_field, [])
            if tables_for_field:
                field_info["source_table"] = tables_for_field[0]
                source_table_filled += 1

        source_table: str = field_info.get("source_table", "")

        # 2. Normalize field name ──────────────────────────────────────────
        normalized_name = _normalize_field_name(source_field)
        field_info["normalized_field_name"] = normalized_name  # type: ignore[typeddict-unknown-key]

        # 3. Track for cross-table dedup detection ──────────────────────────
        normalized_index.setdefault(normalized_name, []).append((source_field, source_table))

        # 4. Enrich description if absent / too thin ────────────────────────
        existing_desc: Optional[str] = field_info.get("description") or ""
        data_type: str = field_info.get("data_type", "")

        if not existing_desc or len(existing_desc.strip()) < 10:
            rich_desc = _build_rich_description(
                source_field=source_field,
                normalized_name=normalized_name,
                data_type=data_type,
                source_table=source_table,
            )
            field_info["description"] = rich_desc
            enriched += 1
        else:
            # Append normalized name as suffix to existing descriptions so
            # the embedding includes both the original comment and the clean name
            if normalized_name.lower() not in existing_desc.lower():
                field_info["description"] = f"{existing_desc.strip()} | {normalized_name}"

    # 5. Disambiguate cross-table duplicates ─────────────────────────────────
    disambiguated = 0
    for norm_name, occurrences in normalized_index.items():
        if len(occurrences) <= 1:
            continue
        # Multiple tables share the same normalized name — add table context
        table_names = [t for _, t in occurrences if t]
        table_hint = ", ".join(sorted(set(table_names)))
        for field_info in unmapped_after_t1:
            if field_info.get("normalized_field_name") == norm_name:  # type: ignore[typeddict-item]
                existing = field_info.get("description", "")
                tbl = field_info.get("source_table", "")
                if tbl and tbl not in existing:
                    field_info["description"] = (
                        f"{existing} [table: {tbl}; also in: {table_hint}]"
                    )
                    disambiguated += 1

    stats = {
        "fields_processed": len(unmapped_after_t1),
        "descriptions_enriched": enriched,
        "source_tables_filled": source_table_filled,
        "cross_table_disambiguated": disambiguated,
    }

    logger.info(
        f"[Node 2.5] ✓ Preprocessing complete: "
        f"{enriched} descriptions enriched, "
        f"{source_table_filled} source_tables filled, "
        f"{disambiguated} cross-table fields disambiguated"
    )

    state["unmapped_after_t1"] = unmapped_after_t1
    state["preprocessing_stats"] = stats  # type: ignore[typeddict-unknown-key]
    state["status"] = "semantic"
    state["notes"] = state.get("notes", []) + [
        f"Pre-semantic preprocessing: {enriched} descriptions enriched, "
        f"{source_table_filled} source_tables filled, "
        f"{disambiguated} disambiguated"
    ]

    # ── Persist lightweight progress record ──────────────────────────────────
    if schema_mapping_id:
        try:
            from .schema_db_writer import schema_write_step_pause_auto
            payload = {
                "node": "2_5",
                "title": "Pre-Semantic Preprocessing Complete",
                **stats,
            }
            # node_num=2 (closest integer) — step_key is the canonical identifier
            await schema_write_step_pause_auto(
                schema_mapping_id, 2, "step_2_5_preprocess", payload
            )
        except Exception as _exc:
            logger.warning(f"[Node 2.5] DB step write failed (non-fatal): {_exc}")

    return state


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_hungarian_prefix(field_name: str) -> tuple[str, Optional[str]]:
    """
    Detect and strip a Hungarian-notation prefix from a field name.

    Returns (stripped_name, prefix_type_hint) or (original, None).

    Examples:
        "strCode"       → ("Code",     "text")
        "intAssetID"    → ("AssetID",  "integer")
        "dtmCreated"    → ("Created",  "datetime")
        "bolIsActive"   → ("IsActive", "boolean")
        "work_order_id" → ("work_order_id", None)   # not Hungarian
    """
    for prefix, type_hint in _HUNGARIAN_PREFIX.items():
        if len(field_name) > len(prefix) + _MIN_PREFIX_LEN and field_name.startswith(prefix):
            remainder = field_name[len(prefix):]
            # Confirm the char after the prefix is uppercase (proper Hungarian)
            if remainder and remainder[0].isupper():
                return remainder, type_hint
    return field_name, None


def _camel_to_words(name: str) -> str:
    """
    Split camelCase / PascalCase / underscored names into space-separated words.

    Examples:
        "AssetID"         → "Asset ID"
        "workOrderStatus" → "work Order Status"
        "is_primary_key"  → "is primary key"
        "WOPriority"      → "WO Priority"
    """
    # Replace underscores/hyphens with spaces first
    name = re.sub(r"[_\-]+", " ", name)
    # Insert space before sequences of uppercase letters followed by lowercase
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
    # Insert space between a lowercase letter and an uppercase letter
    name = re.sub(r"([a-z\d])([A-Z])", r"\1 \2", name)
    return name.strip()


def _expand_abbreviations(words: str) -> str:
    """Replace known CMMS abbreviations with their full forms (case-insensitive)."""
    tokens = words.split()
    result = []
    for token in tokens:
        expanded = _ABBREV_MAP.get(token.lower())
        result.append(expanded if expanded else token)
    return " ".join(result)


def _normalize_field_name(field_name: str) -> str:
    """
    Produce a clean human-readable name from any vendor field name.

    Pipeline:
      1. Strip Hungarian prefix (Fiix: str, int, dtm, bol, …)
      2. Split camelCase / PascalCase
      3. Expand common CMMS abbreviations
      4. Lowercase and clean whitespace
    """
    stripped, _ = _strip_hungarian_prefix(field_name)
    words = _camel_to_words(stripped)
    expanded = _expand_abbreviations(words)
    return expanded.lower().strip()


def _build_rich_description(
    source_field: str,
    normalized_name: str,
    data_type: str,
    source_table: str,
) -> str:
    """
    Construct a rich, embedding-friendly description for a field with no
    (or too-thin) existing description.

    The result is designed to maximise cosine similarity with canonical field
    embeddings in Node 3.
    """
    parts: List[str] = []

    # Human-readable normalized name
    if normalized_name:
        parts.append(normalized_name.capitalize())

    # Data type hint
    _, hungarian_type = _strip_hungarian_prefix(source_field)
    if hungarian_type:
        parts.append(f"{hungarian_type} value")
    elif data_type:
        # Map SQL types to human-readable forms
        dt_lower = data_type.lower()
        if any(t in dt_lower for t in ("int", "bigint", "smallint", "serial")):
            parts.append("integer value")
        elif any(t in dt_lower for t in ("varchar", "text", "char", "string")):
            parts.append("text value")
        elif any(t in dt_lower for t in ("timestamp", "datetime", "date")):
            parts.append("date/time value")
        elif any(t in dt_lower for t in ("bool", "bit")):
            parts.append("boolean flag")
        elif any(t in dt_lower for t in ("numeric", "decimal", "float", "double", "real")):
            parts.append("numeric value")
        else:
            parts.append(f"{data_type} field")

    # Table context
    if source_table:
        # Convert table names like "WorkOrder" or "work_order" to "work order"
        readable_table = _normalize_field_name(source_table).replace("_", " ")
        parts.append(f"from {readable_table}")

    return " | ".join(parts) if parts else f"Field {source_field}"
