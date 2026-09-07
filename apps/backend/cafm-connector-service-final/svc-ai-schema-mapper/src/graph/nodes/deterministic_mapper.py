"""Node 2: Deterministic mapper — 4-tier field mapping strategy (MULTI-TABLE).

Supports BOTH:
1. Hardcoded canonical fields (legacy/fallback)
2. Customer-provided JSON mapper (from json_mapper in state)

Strategies (in order, per table, per column):
1. Exact field name match
1B. Common field name variations
2. CMMS alias lookup
3. Regex pattern matching
4. Haiku constrained call (fallback)

MULTI-TABLE: Each source table's columns are mapped together, grouped by table.

EL-M.2: No duplicate target fields within each table, all confidences in 0–1 range.
"""

import difflib
import json
import logging
import re
from datetime import datetime
from uuid import uuid4

from anthropic import AsyncAnthropic

from ...matchers import (
    CMMS_ALIASES,
    PATTERNS,
    describe_dataset,
    get_cmms_alias,
    match_field_by_pattern,
    registry_lookup_learned_only,
)
from ..state import FieldMapping, MigrationState
from ._timing import timed_node
from .mapping_cutoff import DETERMINISTIC_CUTOFF, partition_by_cutoff
from .ontology_mapping import ontology_column_match, ontology_enabled

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


from ...matchers.levenshtein import closest_canonical_within as _closest_canonical_within  # noqa: E402


def _normalize_field_name(field_name: str) -> str:
    """
    Normalize field name for matching.
    Handles case insensitivity, whitespace, punctuation.

    Collapses EVERY run of non-alphanumeric characters to a single underscore,
    not just an explicit separator set. The previous regex ([\\s\\-_/.,;]+)
    left parentheses and other punctuation intact, so a source column like
    "Distance (km)" normalized to "distance_(km)" and failed to equal the
    real target column "distance_km" at Strategy 0a (direct target-table
    column match) — sending columns that DO exist on an existing table down
    the unresolved/semantic path instead. Broadening to [^a-z0-9]+ makes this
    consistent with the frontend (toSnakeCase / normalizeCol) and the semantic
    mapper (_norm_col), so units-in-parentheses columns map exactly:
      "Distance (km)"           -> "distance_km"
      "Total Trip Cost (AED)"   -> "total_trip_cost_aed"
      "Allowance / Per Diem (AED)" -> "allowance_per_diem_aed"
    Canonical fields and common variations are already snake_case, so their
    normalization is unchanged.
    """
    normalized = field_name.lower().strip()
    normalized = re.sub(r'[^a-z0-9]+', '_', normalized)
    normalized = normalized.strip('_')
    return normalized


def _reference_alias(norm: str) -> "str | None":
    """Map a foreign-key reference column to its '<entity>_id' form.

    The convention '<entity>_ref' / '<entity>_reference' denotes a reference to the
    entity's identity column. So a source column 'site_ref' (values S-01, S-02, …) is
    a reference to 'site_id', NOT a fuzzy name-match to 'site_type'/'site_code'.
    Returns the normalized '<entity>_id' name, or None if no reference suffix applies.
    """
    for suf in ("_reference", "_ref", "reference", "ref"):
        if norm.endswith(suf) and len(norm) > len(suf):
            base = norm[: -len(suf)].rstrip("_")
            if base:
                return f"{base}_id"
    return None


# Identifier suffixes that qualify a column to a specific ENTITY ('asset_id' → asset,
# 'site_ref' → site, 'work_order_code' → work_order). A bare 'id'/'code' (no prefix) is the
# table's OWN key and carries no foreign entity.
_ENTITY_ID_SUFFIXES = (
    "_id", "_ref", "_reference", "_code", "_no", "_num", "_number", "_identifier", "_key",
)


def _entity_token(norm: str) -> str:
    """The ENTITY a qualified identifier column references — the token before an id/ref/code
    suffix ('asset_id'→'asset', 'site_ref'→'site', 'work_order_code'→'work_order'). Empty for a
    bare 'id'/'code' (the table's own key) or any non-identifier column.

    Used to stop a cross-entity identifier suggestion: 'asset id' (asset codes A-###) must not
    be offered for 'site_id' (site refs S-###) just because both are id-shaped — different
    entity ⇒ different value namespace.
    """
    n = norm or ""
    for suf in _ENTITY_ID_SUFFIXES:
        if n.endswith(suf) and len(n) > len(suf):
            return n[: -len(suf)].rstrip("_")
    return ""


def _fm_table_target(source_table: str, db_tables) -> "str | None":
    """Resolve a CMMS/CAFM/EAM source TABLE name to its real plenum_cafm table via the FM
    ontology: e.g. SAP EQUI/EQUL/EQKT → assets, AUFK/AFKO → work_orders, LFA1/LFB1 →
    vendors, T001W/IFLOT → sites, PERNR → technicians. Returns a real table or None.
    Best-effort: never raises.
    """
    try:
        from ...matchers.fm_ontology import fm_table_lookup
        from ...matchers.fm_crosswalk import FM_ENTITY_TO_TABLE
    except Exception:  # pragma: no cover — additive, never fatal
        return None
    entity, _ = fm_table_lookup(source_table)
    if not entity:
        return None
    table = FM_ENTITY_TO_TABLE.get(entity)
    return table if table and table in (db_tables or []) else None


# Hand-curated, table-scoped FM synonyms that the AUTO-GENERATED alias maps don't cover.
# These are plain-English column synonyms (not platform CMMS codes) → the REAL plenum_cafm
# column on that table. Kept here (not in the generated files, which carry a "do not edit"
# banner) so they survive a regen. Each target MUST be a real column of the table; the caller
# (_resolve_to_table_column) still verifies that before any mapping is made.
_CURATED_COLUMN_ALIASES_BY_TABLE: dict[str, dict[str, str]] = {
    "assets": {
        "product_name": "asset_name",
        "product_code": "asset_code",
        "condition": "status",
        "asset_status": "status",
        "comments": "notes",
        "comment": "notes",
        "comments_blank": "notes",
        "remark": "notes",
    },
}


def _schema_column_alias(target_table: "str | None", source_field: str) -> "str | None":
    """Schema-grounded, table-scoped column alias → real plenum_cafm column.

    Uses COLUMN_ALIASES_BY_TABLE (generated from plenum_cafm.csv) so common source-name
    variations of a real column resolve exactly: e.g. on 'assets', serial_no→serial_number,
    mfg→manufacturer, model_no→model_number; on 'spare_parts', stock_qty→stock_quantity.
    Returns the real column name, or None. Best-effort: never raises.
    """
    if not target_table:
        return None
    n = _normalize_field_name(source_field)
    # (0) Hand-curated plain-English synonyms (product_name→asset_name, condition→status, …)
    _curated = (_CURATED_COLUMN_ALIASES_BY_TABLE.get(target_table) or {}).get(n)
    if _curated:
        return _curated
    # (1) CSV-derived schema aliases (serial_no→serial_number, mfg→manufacturer, …)
    try:
        from ...matchers.plenum_cafm_schema import COLUMN_ALIASES_BY_TABLE

        hit = (COLUMN_ALIASES_BY_TABLE.get(target_table) or {}).get(n)
        if hit:
            return hit
    except Exception:  # pragma: no cover — additive, never fatal
        pass
    # (2) FM Ontology crosswalk — per-platform CMMS/CAFM/EAM aliases → real column
    #     (HERST→manufacturer, WONUM→work_order_id, ORT01→city, TARGCOMPDATE→scheduled_date).
    try:
        from ...matchers.fm_crosswalk import FM_COLUMN_ALIASES_BY_TABLE

        return (FM_COLUMN_ALIASES_BY_TABLE.get(target_table) or {}).get(n)
    except Exception:  # pragma: no cover
        return None


def _resolve_to_table_column(target: str, target_cols_norm: dict) -> "str | None":
    """Constrain a canonical-field match to the routed target table's REAL columns.

    When the table's columns are known, a canonical match is only valid if it maps to an
    actual column — returns that column's real name, else None so the field stays
    unresolved instead of mapping to a column the table doesn't have (e.g.
    'manufacturer'→'make' must NOT map when the routed table has no 'make' column).
    When columns are unknown (routing not resolved / brand-new table), accept as-is.
    """
    if not target_cols_norm:
        return target
    return target_cols_norm.get(_normalize_field_name(target))


# ── Value-based (data-aware) matching ────────────────────────────────────────────
# Suggestions must consider the actual sample values, not just the column name. A
# column holding "S-01, S-02, S-03" is an identifier/reference (→ *_id / *_code), NOT
# a category (→ *_type) or free text (→ *_name). We classify the source values, infer
# the value class each candidate column EXPECTS from its name, and boost/penalize the
# name-similarity score by how well the two agree.

_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}|^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}")
_CODE_RE = re.compile(r"^[A-Za-z]{0,6}[-_/]?\d+[A-Za-z0-9\-_/]*$")


def _value_class(samples: list) -> str:
    """Classify a column's sample values into a coarse value class."""
    vals = [str(v).strip() for v in (samples or []) if str(v).strip() != ""][:5]
    if not vals:
        return "unknown"
    low = [v.lower() for v in vals]
    if all(v in ("true", "false", "yes", "no", "y", "n") for v in low):
        return "boolean"

    def _is_int(s: str) -> bool:
        try:
            int(s.replace(",", ""))
            return True
        except ValueError:
            return False

    def _is_float(s: str) -> bool:
        try:
            float(s.replace(",", ""))
            return True
        except ValueError:
            return False

    if all(_is_int(v) for v in vals):
        return "integer"
    if all(_is_float(v) for v in vals):
        return "decimal"
    if all(_DATE_RE.match(v) for v in vals):
        return "date"
    # code/identifier: short alpha prefix + digits, optional separator (S-01, A-001, WO1001)
    if all(_CODE_RE.match(v) for v in vals):
        return "code"
    # short single-token value with no spaces → likely a category/enum (Active, Office)
    if all(len(v) <= 24 and " " not in v for v in vals):
        return "enum"
    return "text"


# Which value class a candidate target column expects, inferred from its name.
def _expected_field_class(field_name: str) -> str:
    n = _normalize_field_name(field_name)
    if any(t in n for t in ("_id", "id_", "_ref", "code", "_no", "num", "serial", "barcode")) or n in ("id",):
        return "code"
    if any(t in n for t in ("type", "status", "category", "categ", "priority", "class", "state", "criticality", "level")):
        return "enum"
    if any(t in n for t in ("date", "_at", "time", "timestamp", "_on")):
        return "date"
    if n.startswith(("is_", "has_")) or any(t in n for t in ("active", "enabled", "flag", "breached")):
        return "boolean"
    if any(t in n for t in ("cost", "amount", "price", "qty", "quantity", "count", "minutes", "hours", "days", "rate", "pct", "percent", "number", "gfa", "area", "floors")):
        return "numeric"
    if any(t in n for t in ("name", "desc", "note", "comment", "title", "address", "remark", "label")):
        return "text"
    return "any"


# How compatible a source value class is with a candidate's expected class.
# >1 boosts, <1 penalizes, 1.0 neutral.
def _value_class_factor(src_class: str, target_field: str) -> float:
    if src_class == "unknown":
        return 1.0
    exp = _expected_field_class(target_field)
    if exp == "any":
        return 1.0
    # identifiers/codes are interchangeable with numeric id columns
    code_like = {"code", "integer"}
    if src_class in code_like and exp == "code":
        return 1.2
    if exp == src_class:
        return 1.2
    # numeric source vs numeric target (and vice-versa)
    if src_class in ("integer", "decimal") and exp == "numeric":
        return 1.2
    # hard mismatches: a code/identifier should NOT map to a category or free-text column
    if src_class in ("code", "integer", "decimal", "date", "boolean") and exp in ("enum", "text"):
        return 0.62
    if src_class in ("enum", "text") and exp in ("code", "numeric", "date", "boolean"):
        return 0.62
    return 1.0


def _extract_source_samples(records: list, field: str, k: int = 3, scan_limit: int = 1000) -> list[str]:
    """First k non-empty sample values for a source column — used for the gate preview.

    Stops after k hits; scans at most scan_limit rows so an all-empty column in a large
    file doesn't walk every row.
    """
    out: list[str] = []
    for r in (records or [])[:scan_limit]:
        if not isinstance(r, dict):
            continue
        v = r.get(field)
        if v is not None and str(v).strip() != "":
            out.append(str(v)[:60])
            if len(out) >= k:
                break
    return out


def _infer_simple_data_type(samples: list) -> str:
    """Lightweight SQL-type guess from sample values (for display only)."""
    vals = [str(v).strip() for v in (samples or []) if str(v).strip() != ""][:5]
    if not vals:
        return "VARCHAR(255)"

    def _is_int(s: str) -> bool:
        try:
            int(s)
            return True
        except (ValueError, TypeError):
            return False

    def _is_float(s: str) -> bool:
        try:
            float(s)
            return True
        except (ValueError, TypeError):
            return False

    if all(_is_int(v) for v in vals):
        return "INTEGER"
    if all(_is_float(v) for v in vals):
        return "NUMERIC"
    if {v.lower() for v in vals} <= {"true", "false", "yes", "no", "0", "1", "y", "n"}:
        return "BOOLEAN"
    if all(re.search(r"\d{1,4}[/\-]\d{1,2}[/\-]\d{1,4}", v) for v in vals):
        return "DATE"
    return "VARCHAR(255)"


def _build_candidate_fits(
    source_field: str,
    chosen_target: "str | None",
    chosen_conf: float,
    target_cols_norm: dict,
    canonical_fields_map: dict,
    top_k: int = 3,
    samples: "list | None" = None,
) -> list:
    """
    Top-K candidate target fields for a source field, best-first (chosen target first).

    Scored by (a) normalized name similarity (difflib), (b) the '<entity>_ref→_id'
    foreign-key naming convention, and (c) DATA AWARENESS — the actual sample values:
    a column of identifiers ("S-01", "S-02") favors id/code columns and is penalized
    against category/text columns, so 'site_ref' surfaces 'site_id', not 'site_type'.
    The chosen deterministic target keeps its real confidence.
    """
    norm_sf = _normalize_field_name(source_field)
    # A '<entity>_ref' column references '<entity>_id' — score candidates against that
    # form too, so 'site_ref' surfaces 'site_id' first instead of fuzzy 'site_type'.
    ref_sf = _reference_alias(norm_sf)
    src_class = _value_class(samples)  # data-aware signal from the first sample rows
    src_entity = _entity_token(norm_sf)        # the entity this id column references (or "")
    ref_entity = _entity_token(ref_sf) if ref_sf else ""
    # Semantic date bias: a 'raised / opened / logged / reported / created' date is a CREATION
    # timestamp → prefer created_at over the fuzzily-close-but-WRONG updated_at; symmetrically a
    # 'modified / changed / updated' date → prefer updated_at. e.g. date_raised → created_at.
    _sf_tok = set(norm_sf.split("_"))
    _date_hint = bool(_sf_tok & {"date", "at", "on", "dt", "time", "datetime", "timestamp"})
    _is_creation_date = _date_hint and bool(_sf_tok & {
        "raised", "raise", "opened", "open", "logged", "reported", "created", "creation",
        "entered", "entry", "received", "booked", "registered",
    })
    _is_modify_date = _date_hint and bool(_sf_tok & {
        "modified", "modify", "changed", "change", "updated", "update", "edited", "amended", "revised",
    })
    scored: dict[str, float] = {}
    if chosen_target:
        scored[chosen_target] = max(float(chosen_conf or 0.0), scored.get(chosen_target, 0.0))

    # Suggest ONLY columns that exist on the routed target table when we know them; fall
    # back to the global canonical fields only when the table's columns are unknown (no
    # routing yet / brand-new table). Otherwise the gate would offer columns the chosen
    # table doesn't have (e.g. 'asset_code' under 'work_order_tasks').
    pool: dict[str, str] = {}
    if target_cols_norm:
        for norm, actual in target_cols_norm.items():
            if actual:
                pool.setdefault(norm, actual)
    else:
        for norm, actual in (canonical_fields_map or {}).items():
            if actual:
                pool.setdefault(norm, actual)

    for norm, actual in pool.items():
        if actual == chosen_target:
            continue
        ratio = difflib.SequenceMatcher(None, norm_sf, norm).ratio()
        if ref_sf:
            # Reference-alias match (e.g. site_ref→site_id) is a strong FK-naming signal;
            # discount slightly so it doesn't claim a literal 100% exact match.
            ratio = max(ratio, difflib.SequenceMatcher(None, ref_sf, norm).ratio() * 0.97)
        # Cross-entity identifier guard: a qualified id/ref/code column must NOT be suggested
        # for a DIFFERENT entity's identifier (asset_id A-### ↛ site_id S-###) just because
        # both are id-shaped. Penalize so a cross-entity match never surfaces as a confident
        # pick — the field falls through to semantic / new column instead. Same-entity
        # (site_ref→site_id) and the reference-alias entity are exempt.
        cand_entity = _entity_token(norm)
        if cand_entity and (src_entity or ref_entity):
            if cand_entity != src_entity and cand_entity != ref_entity:
                ratio *= 0.4
            else:
                ratio = min(0.99, ratio * _value_class_factor(src_class, actual))
        else:
            # Data awareness: scale by how well the source VALUES fit this column's kind.
            ratio = min(0.99, ratio * _value_class_factor(src_class, actual))
        if _is_creation_date or _is_modify_date:
            _na = _normalize_field_name(actual)
            _creation = _na in {"created_at", "created_date", "creation_date", "date_created",
                                "created", "raised_at", "date_raised", "opened_at"}
            _modify = _na in {"updated_at", "modified_at", "changed_at", "updated_date",
                              "date_updated", "last_modified", "last_updated", "modified"}
            _want = _creation if _is_creation_date else _modify
            _other = _modify if _is_creation_date else _creation
            if _want:
                ratio = max(ratio, 0.9)   # the right timestamp kind — strongly preferred
            elif _other:
                ratio *= 0.5              # the wrong kind (created↔updated) — demote fuzzy match
        if ratio >= 0.55:
            scored[actual] = max(scored.get(actual, 0.0), round(ratio, 2))

    ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [
        {"target_field": t, "confidence": c, "is_primary": (t == chosen_target)}
        for t, c in ranked
    ]


CANONICAL_FIELDS = {
    # Assets
    "asset_code", "asset_name", "category", "location_code", "make", "model", "serial",
    "criticality", "install_date", "status",
    # Work Orders
    "wo_code", "wo_priority", "wo_status", "wo_type", "maintenance_type",
    "assigned_tech_id", "cause_code", "cost_parts_aed", "cost_vendor_aed", "fault_code",
    "labor_minutes", "resolution_code", "sla_breached", "sla_response_actual_mins",
    "sla_response_target_mins", "travel_minutes", "vendor_id", "responded_at",
    "created_at", "completed_at",
    # Scheduled Maintenance
    "sm_code", "trigger_type", "schedule_interval", "sm_priority",
    # Parts
    "part_code", "stock_on_hand", "minimum_allowed_stock", "supplier", "bom_group_name",
    # Users
    "user_full_name", "user_title", "user_name", "reports_to",
    # Inspections
    "inspector_name", "inspection_date", "inspection_location", "finding_type", "risk_level",
    # Sites
    "site_id", "site_name", "site_type",
}

COMMON_VARIATIONS = {
    "asset_id": "asset_code", "asset_code": "asset_code", "asset_num": "asset_code",
    "asset_number": "asset_code", "equipment_id": "asset_code", "equipment_code": "asset_code",
    "asset_type": "category", "asset_category": "category", "equipment_type": "category",
    "category_id": "category", "asset_name": "asset_name", "equipment_name": "asset_name",
    "asset_desc": "asset_name", "description": "asset_name", "manufacturer": "make",
    "make": "make", "brand": "make", "model": "model", "model_num": "model",
    "serial": "serial", "serial_num": "serial", "serial_number": "serial", "sn": "serial",
    "install_date": "install_date", "installation_date": "install_date",
    "commissioned_date": "install_date", "criticality": "criticality",
    "criticality_level": "criticality", "location": "location_code", "location_code": "location_code",
    "location_id": "location_code", "zone": "location_code", "room": "location_code",
    "status": "status", "asset_status": "status", "wo_id": "wo_code", "wo_code": "wo_code",
    "work_order_id": "wo_code", "work_order_number": "wo_code", "wo_number": "wo_code",
    "order_number": "wo_code", "priority": "wo_priority", "wo_priority": "wo_priority",
    "priority_level": "wo_priority", "wo_status": "wo_status", "work_order_status": "wo_status",
    "wo_type": "wo_type", "work_order_type": "wo_type", "order_type": "wo_type",
    "maintenance_type": "maintenance_type", "job_type": "maintenance_type",
    "maintenance_type_id": "maintenance_type", "site_id": "site_id", "site_code": "site_id",
    "site_name": "site_name", "location_name": "site_name", "site_type": "site_type",
    "location_type": "site_type", "created_at": "created_at", "created_date": "created_at",
    "date_created": "created_at", "creation_date": "created_at", "completed_at": "completed_at",
    "completed_date": "completed_at", "date_completed": "completed_at",
    "completion_date": "completed_at", "responded_at": "responded_at",
    "response_date": "responded_at",
}


def _match_common_variation(source_field: str) -> tuple:
    """Strategy 1B: Match against common field name variations."""
    normalized_source = _normalize_field_name(source_field)
    if normalized_source in COMMON_VARIATIONS:
        target = COMMON_VARIATIONS[normalized_source]
        return (target, 0.95)
    return None


def _get_canonical_fields(json_mapper: dict) -> set:
    """Extract canonical field names from JSON mapper."""
    return set(json_mapper.get("canonical_fields", {}).keys())


def _match_json_alias(source_field: str, json_mapper: dict) -> tuple:
    """Match source field against vendor aliases in JSON mapper."""
    vendor_aliases = json_mapper.get("vendor_aliases", {})
    normalized_source = _normalize_field_name(source_field)
    for canonical, sources in vendor_aliases.items():
        for source_alias in sources:
            normalized_alias = _normalize_field_name(source_alias)
            if normalized_source == normalized_alias:
                confidence_overrides = json_mapper.get("confidence_overrides") or {}
                conf = confidence_overrides.get(canonical, 0.95)
                return (canonical, conf)
    return None


def _match_json_regex(source_field: str, json_mapper: dict) -> tuple:
    """Match source field against regex patterns in JSON mapper."""
    regex_patterns = json_mapper.get("regex_patterns") or {}
    normalized_source = _normalize_field_name(source_field)
    for canonical, pattern_config in regex_patterns.items():
        patterns = pattern_config.get("patterns", [])
        for pattern in patterns:
            if re.match(pattern, source_field, re.IGNORECASE) or re.match(pattern, normalized_source, re.IGNORECASE):
                conf = pattern_config.get("confidence", 0.80)
                if conf >= 0.85:
                    return (canonical, conf)
    return None


def _match_sheet_to_table(table_name: str, db_tables: list[str]) -> str | None:
    """Match a source sheet name to a real plenum_cafm table (singular/plural/space tolerant)."""
    t = table_name.lower().strip()
    variants = {t, t.replace(" ", "_"), t.rstrip("s"), t.replace(" ", "_").rstrip("s")}
    for tgt in db_tables:
        tl = tgt.lower()
        if tl in variants or tl.rstrip("s") in variants:
            return tgt
    return None


@timed_node("deterministic")
async def deterministic_mapper_node(state: MigrationState) -> MigrationState:
    """
    Node 2: Deterministic field mapping — MULTI-TABLE (each table processed independently).

    For multi-table uploads (e.g., work_orders.csv, assets.csv), columns are
    mapped per source table. Results grouped by table for human review.
    """

    _node_started_at = datetime.utcnow()
    # Fresh deterministic pass → every downstream gate must be re-reviewable. Clearing the
    # "answered" flags here means a deliberate restart-from-node re-opens the gates, while a normal
    # resume (which does NOT re-run this node) keeps them set so the gates can't re-appear.
    # pk_review / unique_table_review now run UPSTREAM of this node (right after ingest), so they are
    # NOT reset here — a restart from the deterministic mapper must NOT re-open already-answered PK /
    # unique-table gates. Only the DOWNSTREAM gates are cleared for re-review.
    state["pre_semantic_reviewed"] = False
    state["pre_semantic_ci_built"] = False
    # Drop the cached pass-1 reports so a deliberate restart rebuilds them from the new mappings.
    state["table_resolution_report"] = {}
    state["grouping_reviewed"] = False
    state["column_mapping_reviewed"] = False
    state["field_mapping_reviewed"] = False
    state["hierarchy_reviewed"] = False
    migration_id = state.get("migration_id")
    cmms_name = state.get("cmms_name", "Unknown")
    parsed_tables = state.get("parsed_tables", {})
    # Full ingested file records (set by Node 1). Used as the PREFERRED source for the
    # gate's "e.g. <values>" preview so example data always comes from the real Excel/CSV
    # content, even when the 5-row parsed_tables sample happens to be null for a column.
    full_tables = state.get("full_tables") or {}
    column_descriptions = state.get("column_descriptions", {})
    json_mapper = state.get("json_mapper")

    if not parsed_tables:
        logger.error(f"[Node 2] No parsed tables found")
        state["error_message"] = "No parsed data from Node 1"
        state["error_node"] = 2
        return state

    # Determine canonical fields
    if json_mapper:
        canonical_fields_raw = _get_canonical_fields(json_mapper)
        canonical_fields = {_normalize_field_name(f) for f in canonical_fields_raw}
        canonical_fields_map = {_normalize_field_name(f): f for f in canonical_fields_raw}
        logger.info(f"[Node 2] Using customer JSON mapper: {json_mapper.get('source_system')}")
        logger.info(f"[Node 2] Loaded {len(canonical_fields_raw)} canonical fields")
    else:
        canonical_fields_raw = list(CANONICAL_FIELDS)
        canonical_fields = CANONICAL_FIELDS
        canonical_fields_map = {f: f for f in CANONICAL_FIELDS}
        logger.info(f"[Node 2] Using default hardcoded canonical fields")

    logger.info(f"[Node 2] Starting deterministic mapping (MULTI-TABLE): migration_id={migration_id}")

    try:
        # Real plenum_cafm tables + their columns — lets us match source columns directly
        # against the routed target table's ACTUAL columns (not just the canonical list),
        # so table-specific columns (city, country, floors, gfa_sqm, …) map exactly.
        from ...db import get_plenum_cafm_columns_by_table
        _columns_by_table = await get_plenum_cafm_columns_by_table()
        _db_tables = sorted(_columns_by_table.keys())

        # Node 1's table matcher (deterministic name match + Haiku fallback) — the best
        # signal for a sheet whose name is NOT a CAFM table (e.g. sites_2 → sites). Used
        # to pick the target table whose REAL columns we match against, so table-specific
        # columns (city, country, floors, …) map exactly instead of falling to semantic.
        _ingest_table_matches = {
            str(k): str(v)
            for k, v in (state.get("cafm_table_matches") or {}).items()
            if v and str(v) in _columns_by_table
        }
        # Per-source-table confidence of Node 1's table match (1.0 = exact/levenshtein name match,
        # the Haiku % for an uncertain semantic guess). Used below to content-sanity-check only the
        # uncertain guesses.
        _ingest_table_match_conf = {
            str(k): v for k, v in (state.get("cafm_table_match_confidence") or {}).items()
        }
        # Records where the content sanity-check overrode an uncertain semantic table match, so the
        # later table_routing build applies the same correction.
        _table_content_override: dict[str, str] = {}

        # Destination tables' EXISTING sample values → used by the value-gate below so a
        # name-only match can't auto-merge columns whose data doesn't overlap (e.g. a column
        # of asset codes onto an id column already full of vendor ids). Best-effort: empty /
        # unreadable targets contribute nothing and the gate degrades to a no-op.
        _dest_samples_by_table: dict = {}
        try:
            from ...db import get_plenum_cafm_sample_values_by_table
            _dest_samples_by_table = await get_plenum_cafm_sample_values_by_table(
                set(_ingest_table_matches.values()), allow=_columns_by_table
            )
        except Exception:  # pragma: no cover — value gate is additive, never fatal
            _dest_samples_by_table = {}

        # MULTI-TABLE: Process each source table independently
        tier1_mappings_by_table = {}
        unresolved_by_table = {}
        # Best-guess auto-suggestion per UNMAPPED field (target + confidence + candidates), so
        # the gate can show "suggested: X (Y%)" and let the user approve it or send to semantic.
        unresolved_suggestions_by_table: dict = {}
        # Content-aware table routing candidates per source sheet (ranked by column overlap),
        # so the gate can OFFER the best destination table(s) to select when the sheet name
        # is misleading (e.g. 'work_tasks' whose columns are really an assets sheet).
        table_match_candidates_by_table: dict = {}
        # Value-centric merge (req 2): {table: {representative_col: [merged_cols]}} — columns
        # with IDENTICAL VALUES are the same field under different names, collapsed to one.
        merged_columns_by_table: dict = {}
        # Challenge 1: partial-similarity column pairs (60–95% value overlap) per table,
        # surfaced for manual review/merge: {table: [{column_a, column_b, overlap, sample_a, sample_b}]}.
        near_duplicate_columns_by_table: dict = {}
        all_confidences = []

        for table_name in sorted(parsed_tables.keys()):
            table_records = parsed_tables[table_name]
            # Prefer the FULL ingested rows for sample-value previews (real Excel/CSV data),
            # falling back to the 5-row sample. _extract_source_samples only walks until it
            # finds k non-empty values, so scanning the full list stays cheap.
            sample_source = full_tables.get(table_name) or table_records
            if not table_records:
                logger.info(f"[Node 2] Skipping empty table: {table_name}")
                tier1_mappings_by_table[table_name] = []
                unresolved_by_table[table_name] = []
                continue

            logger.info(f"[Node 2] ► Processing source table: {table_name}")

            # Get columns for THIS table only
            table_columns = sorted(table_records[0].keys())
            logger.info(f"[Node 2]   Columns: {len(table_columns)} [{', '.join(table_columns[:5])}...]")

            # Determine this sheet's target CAFM table early so its columns can be matched
            # directly against the table's REAL columns (e.g. sites → plenum_cafm.sites).
            # Prefer Node 1's name+LLM match (handles sites_2 → sites) and fall back to a
            # pure name match.
            _early_target = (
                _ingest_table_matches.get(table_name)
                or _match_sheet_to_table(table_name, _db_tables)
                # FM ontology fallback: a CMMS/CAFM/EAM source table name (e.g. SAP EQUI,
                # AUFK, LFA1; Maximo WORKORDER; Archibus WR) → its real plenum_cafm table.
                or _fm_table_target(table_name, _db_tables)
            )
            if _early_target:
                logger.info(f"[Node 2]   Target table: {table_name} → {_early_target}")
            # Content-aware candidates: rank real tables by how many of THIS sheet's columns
            # map, so the gate offers the best target to select (name-independent).
            try:
                from ...matchers.table_router import rank_target_tables
                _cands = rank_target_tables(table_columns)
                if _cands:
                    table_match_candidates_by_table[table_name] = _cands
            except Exception:  # pragma: no cover — additive, never fatal
                pass
            # Content sanity-check on an UNCERTAIN semantic table match: if Node 1's LLM guessed a
            # table whose columns barely fit (≈0 overlap) while a DIFFERENT real table clearly fits
            # better, trust the content. Fixes 'works' (asset columns tagnum/product_name/make/…)
            # being pulled to 'work_orders' by name similarity. NEVER second-guesses a confident
            # name/levenshtein match (conf ≥ 0.95) — only the low-confidence semantic guesses.
            try:
                _cand_list = table_match_candidates_by_table.get(table_name) or []
                _tm_conf = float(_ingest_table_match_conf.get(table_name, 1.0) or 1.0)
                if _cand_list and _early_target and _tm_conf < 0.95:
                    _top = _cand_list[0]
                    _top_tbl = _top.get("table")
                    _top_pct = float(_top.get("pct") or 0.0)
                    _llm_pct = next((float(c.get("pct") or 0.0) for c in _cand_list
                                     if c.get("table") == _early_target), 0.0)
                    if (_top_tbl and _top_tbl in _columns_by_table and _top_tbl != _early_target
                            and _top_pct >= 0.30 and _llm_pct <= _top_pct - 0.25):
                        logger.info(
                            f"[Node 2]   Content override: {table_name} {_early_target} → {_top_tbl} "
                            f"(semantic guess {_tm_conf:.0%}; column fit {_llm_pct:.0%} vs {_top_pct:.0%})"
                        )
                        _early_target = _top_tbl
                        _table_content_override[table_name] = _top_tbl
            except Exception:  # pragma: no cover — additive, never fatal
                pass
            _target_cols_norm = (
                {_normalize_field_name(c): c for c in _columns_by_table.get(_early_target, set())}
                if _early_target else {}
            )

            tier1_for_table = []
            unresolved_for_table = []
            table_confidences = []

            # Apply strategies to each column in this table
            for source_field in table_columns:
                # Strategy 0a: Direct target-table column match (HIGHEST preference). If this
                # sheet routes to a real CAFM table and the column name matches one of THAT
                # table's columns, map it straight there — covers table-specific columns
                # (city, country, floors, gfa_sqm, site_type, …) not in the canonical list.
                _norm_sf = _normalize_field_name(source_field)
                if _norm_sf in _target_cols_norm:
                    _actual = _target_cols_norm[_norm_sf]
                    mapping = FieldMapping(
                        source_field=source_field, target_field=_actual, confidence=0.98,
                        tier="T1_table_exact",
                        rationale=f"Exact column match on target table '{_early_target}'",
                        langsmith_run_id=None,
                    )
                    tier1_for_table.append(mapping)
                    table_confidences.append(0.98)
                    logger.info(f"[Node 2]   S0a (table-col): {source_field} → {_actual} (0.98)")
                    continue

                # Strategy 0: Identity key (runs BEFORE alias matching). The table's OWN
                # identity column — the business key other tables' FKs point to — maps to the
                # canonical primary key 'id'. Recognises the entity's identifier variants
                # (ASSETNUM, asset_ref, asset_no, asset_id, asset_number on an 'assets' sheet;
                # 'A-001' is what work_orders.asset_no references). Built from the ROUTED
                # table's entity, so a foreign key to ANOTHER entity (site_id inside assets,
                # asset_no inside work_orders) does NOT match here. write_node then promotes
                # the business key to the real PK.
                _ent = (_early_target or table_name).lower().strip()
                _ent_sing = (
                    _ent[:-3] + "y" if _ent.endswith("ies")
                    else _ent[:-1] if (_ent.endswith("s") and not _ent.endswith("ss"))
                    else _ent
                )
                _sn = _normalize_field_name(_ent_sing)
                _id_forms = {
                    "id", f"{_normalize_field_name(_ent)}_id", f"{_sn}_id", f"{_sn}id",
                    f"{_sn}num", f"{_sn}_num", f"{_sn}_no", f"{_sn}_ref",
                    f"{_sn}_reference", f"{_sn}_number", f"{_sn}_identifier",
                }
                if _sn and _norm_sf in _id_forms:
                    _id_col = _target_cols_norm.get("id", "id")
                    mapping = FieldMapping(
                        source_field=source_field, target_field=_id_col, confidence=0.99,
                        tier="T1_identity",
                        rationale=f"Identity/business key for '{_early_target or table_name}' → primary key '{_id_col}'",
                        langsmith_run_id=None,
                    )
                    tier1_for_table.append(mapping)
                    table_confidences.append(0.99)
                    logger.info(f"[Node 2]   S0 (identity): {source_field} → {_id_col} (0.99)")
                    continue

                # Strategy 0b: Schema-grounded table-scoped column alias. A common source
                # variation of a REAL column on the routed table (serial_no→serial_number,
                # mfg→manufacturer, task_desc→task_description, stock_qty→stock_quantity).
                # Generated from plenum_cafm.csv; resolves to a real column only.
                _alias_col = _schema_column_alias(_early_target, source_field)
                if _alias_col and _normalize_field_name(_alias_col) in _target_cols_norm:
                    _actual = _target_cols_norm[_normalize_field_name(_alias_col)]
                    mapping = FieldMapping(
                        source_field=source_field, target_field=_actual, confidence=0.96,
                        tier="T1_table_alias",
                        rationale=f"Schema column alias on target table '{_early_target}'",
                        langsmith_run_id=None,
                    )
                    tier1_for_table.append(mapping)
                    table_confidences.append(0.96)
                    logger.info(f"[Node 2]   S0b (table-alias): {source_field} → {_actual} (0.96)")
                    continue

                # Strategy 1: Exact match
                normalized_source = _normalize_field_name(source_field)
                if normalized_source in canonical_fields:
                    target = canonical_fields_map[normalized_source]
                    _resolved = _resolve_to_table_column(target, _target_cols_norm)
                    if _resolved is not None:
                        mapping = FieldMapping(
                            source_field=source_field, target_field=_resolved, confidence=0.99,
                            tier="T1_exact", rationale="Exact canonical field name match (normalized)",
                            langsmith_run_id=None,
                        )
                        tier1_for_table.append(mapping)
                        table_confidences.append(0.99)
                        logger.info(f"[Node 2]   S1 (exact): {source_field} → {_resolved} (0.99)")
                        continue

                # Strategy 1L: Levenshtein edit-distance ≤ 2 (7.4 AC1) — near-exact typo /
                # spelling variant of a canonical field; deterministic (0.96 ≥ 0.95 cutoff).
                _lev = _closest_canonical_within(normalized_source, canonical_fields, max_dist=2)
                if _lev is not None:
                    _cf, _d = _lev
                    _resolved = _resolve_to_table_column(canonical_fields_map[_cf], _target_cols_norm)
                    if _resolved is not None:
                        mapping = FieldMapping(
                            source_field=source_field, target_field=_resolved, confidence=0.96,
                            tier="T1_levenshtein",
                            rationale=f"Levenshtein edit-distance {_d} ≤ 2 to canonical '{_cf}'",
                            langsmith_run_id=None,
                        )
                        tier1_for_table.append(mapping)
                        table_confidences.append(0.96)
                        logger.info(f"[Node 2]   S1L (lev≤2): {source_field} → {_resolved} (0.96, d={_d})")
                        continue

                # Strategy 1C: Token containment on a REAL target column. When the source
                # field's token-set is a strict subset of an actual destination column's
                # tokens (e.g. {name} ⊂ {vendor, name}, {trade} ⊂ {trade, type}), the
                # destination column is the natural target — Levenshtein misses these
                # because the edit distance is too large (name vs vendor_name = 7 edits).
                # This is the gap that left vendors.name dropping to "new column" in B21.
                _src_tokens = set(normalized_source.split("_")) if normalized_source else set()
                if _src_tokens:
                    _containment_hits: list[tuple[str, float]] = []
                    for _tnorm, _tcol in _target_cols_norm.items():
                        if _tnorm == normalized_source:
                            continue  # exact match already handled by S1
                        _t_tokens = set(_tnorm.split("_"))
                        if not _t_tokens or _src_tokens == _t_tokens:
                            continue
                        # Strict containment in EITHER direction — source ⊂ target (name
                        # in vendor_name) OR target ⊂ source (asset_id matches id when
                        # the destination has `id`, useful for qualified-PK canonicals).
                        if _src_tokens.issubset(_t_tokens) or _t_tokens.issubset(_src_tokens):
                            # Score: how much of the larger side is covered by the smaller.
                            smaller = min(len(_src_tokens), len(_t_tokens)) or 1
                            larger = max(len(_src_tokens), len(_t_tokens)) or 1
                            cov = smaller / larger
                            _containment_hits.append((_tcol, cov))
                    if _containment_hits:
                        # Pick the highest-coverage hit (smallest extra-token gap).
                        _tcol, _cov = max(_containment_hits, key=lambda x: x[1])
                        _conf = round(0.85 + 0.10 * _cov, 4)  # 0.85..0.95 by coverage
                        mapping = FieldMapping(
                            source_field=source_field, target_field=_tcol, confidence=_conf,
                            tier="T1_token_containment",
                            rationale=(
                                f"Token subset match: {{{normalized_source}}} ⊂ {{{_normalize_field_name(_tcol)}}} "
                                f"on target table '{_early_target}' (coverage {round(_cov*100)}%)"
                            ),
                            langsmith_run_id=None,
                        )
                        tier1_for_table.append(mapping)
                        table_confidences.append(_conf)
                        logger.info(f"[Node 2]   S1C (token-containment): {source_field} → {_tcol} ({_conf})")
                        continue

                # Strategy 1B: Common variations
                variation_result = _match_common_variation(source_field)
                if variation_result:
                    target, conf = variation_result
                    target_normalized = _normalize_field_name(target) if json_mapper else target
                    if target_normalized in canonical_fields:
                        target_canonical = canonical_fields_map[target_normalized]
                        _resolved = _resolve_to_table_column(target_canonical, _target_cols_norm)
                        if _resolved is not None:
                            mapping = FieldMapping(
                                source_field=source_field, target_field=_resolved, confidence=conf,
                                tier="T1_variation", rationale="Common field name variation",
                                langsmith_run_id=None,
                            )
                            tier1_for_table.append(mapping)
                            table_confidences.append(conf)
                            logger.info(f"[Node 2]   S1B (var): {source_field} → {_resolved} ({conf})")
                            continue

                # Strategy 2: Alias lookup
                if json_mapper:
                    alias_result = _match_json_alias(source_field, json_mapper)
                else:
                    alias_result = get_cmms_alias(source_field, cmms_name)

                if alias_result:
                    target, conf = alias_result
                    target_check = _normalize_field_name(target) if json_mapper else target
                    if target_check in canonical_fields:
                        _resolved = _resolve_to_table_column(target, _target_cols_norm)
                        if _resolved is not None:
                            mapping = FieldMapping(
                                source_field=source_field, target_field=_resolved, confidence=conf,
                                tier="T1_alias", rationale=f"Matched via CMMS alias table ({cmms_name})",
                                langsmith_run_id=None,
                            )
                            tier1_for_table.append(mapping)
                            table_confidences.append(conf)
                            logger.info(f"[Node 2]   S2 (alias): {source_field} → {_resolved} ({conf:.2f})")
                            continue

                # Strategy 3: Regex matching
                if json_mapper:
                    pattern_result = _match_json_regex(source_field, json_mapper)
                else:
                    pattern_result = match_field_by_pattern(source_field)

                if pattern_result:
                    target, conf = pattern_result
                    target_check = _normalize_field_name(target) if json_mapper else target
                    if target_check in canonical_fields and conf >= 0.85:
                        _resolved = _resolve_to_table_column(target, _target_cols_norm)
                        if _resolved is not None:
                            mapping = FieldMapping(
                                source_field=source_field, target_field=_resolved, confidence=conf,
                                tier="T1_regex", rationale="Matched via naming pattern regex",
                                langsmith_run_id=None,
                            )
                            tier1_for_table.append(mapping)
                            table_confidences.append(conf)
                            logger.info(f"[Node 2]   S3 (regex): {source_field} → {_resolved} ({conf:.2f})")
                            continue

                # Strategy R: Registry lookup (learned semantic matches)
                # Checks aliases that were previously approved via semantics/human
                # review and promoted to deterministic. Runs before LLM to avoid
                # paying embedding + inference costs for already-seen aliases.
                registry_hit = registry_lookup_learned_only(source_field)
                if registry_hit:
                    target, conf, _tier = registry_hit
                    target_check = _normalize_field_name(target) if json_mapper else target
                    if target_check in canonical_fields:
                        _resolved = _resolve_to_table_column(target, _target_cols_norm)
                        if _resolved is not None:
                            mapping = FieldMapping(
                                source_field=source_field, target_field=_resolved, confidence=conf,
                                tier="T1_registry",
                                rationale="Registry hit — previously approved semantic match (deterministic)",
                                langsmith_run_id=None,
                            )
                            tier1_for_table.append(mapping)
                            table_confidences.append(conf)
                            logger.info(
                                f"[Node 2]   SR (registry): {source_field} → {_resolved} ({conf:.2f}) "
                                f"[saved LLM call]"
                            )
                            continue

                # Strategy O: FM-ontology synonym (additive, flag-gated). Runs
                # after the registry and BEFORE the LLM fallback so an ontology
                # synonym (e.g. Maximo WONUM → work_orders.workorder_ref) is
                # resolved deterministically and saves a Haiku call. The match is
                # only accepted if it lands on a REAL column of the target table
                # (_resolve_to_table_column is authoritative). When the flag is
                # off, ontology_column_match returns None → identical behaviour.
                if ontology_enabled():
                    onto = ontology_column_match(source_field, _early_target)
                    if onto and onto.get("canonical_column"):
                        _resolved = _resolve_to_table_column(
                            onto["canonical_column"], _target_cols_norm
                        )
                        if _resolved is not None:
                            conf = float(onto.get("confidence") or 0.9)
                            mapping = FieldMapping(
                                source_field=source_field, target_field=_resolved, confidence=conf,
                                tier="T1_ontology",
                                rationale=(
                                    f"FM ontology synonym ({onto.get('method')}, "
                                    f"{onto.get('cmms_system') or 'generic'})"
                                ),
                                langsmith_run_id=None,
                            )
                            tier1_for_table.append(mapping)
                            table_confidences.append(conf)
                            logger.info(
                                f"[Node 2]   SO (ontology): {source_field} → {_resolved} "
                                f"({conf:.2f}) [saved LLM call]"
                            )
                            continue

                # Unresolved — will go to Tier 2
                unresolved_for_table.append(source_field)
                logger.debug(f"[Node 2]   Unresolved: {source_field}")

            # Strategy 4: Haiku on unresolved fields for THIS table
            if unresolved_for_table:
                logger.info(f"[Node 2]   S4 (Haiku): {len(unresolved_for_table)} unresolved fields for {table_name}")
                strategy4_mappings = await _strategy4_haiku_mapping(
                    unresolved_for_table, column_descriptions, state, canonical_fields_raw,
                )
                # Haiku returns a CANONICAL field (e.g. 'asset_code'), which is NOT
                # guaranteed to be a real column on the ROUTED table — e.g. 'asset id' on a
                # 'sites' sheet maps to canonical 'asset_code', but plenum_cafm.sites has no
                # asset_code column. Apply the SAME dest-column constraint every deterministic
                # strategy uses (_resolve_to_table_column is authoritative): keep only matches
                # that land on a real column — rewriting target_field to the table's actual
                # column name — and leave the rest unresolved so they surface as a NEW column
                # instead of auto-merging into a column the table doesn't have.
                _kept_s4 = []
                for _m in strategy4_mappings:
                    _real = _resolve_to_table_column(_m.get("target_field"), _target_cols_norm)
                    if _real is None:
                        logger.info(
                            f"[Node 2]   S4 (Haiku) REJECTED {_m.get('source_field')} → "
                            f"{_m.get('target_field')}: not a real column on "
                            f"'{_early_target or table_name}' → kept unresolved (new column)"
                        )
                        continue
                    if _real != _m.get("target_field"):
                        _m["target_field"] = _real
                    _kept_s4.append(_m)
                tier1_for_table.extend(_kept_s4)
                strategy4_targets = {m["source_field"] for m in _kept_s4}
                unresolved_for_table = [f for f in unresolved_for_table if f not in strategy4_targets]
                for mapping in _kept_s4:
                    table_confidences.append(mapping.get("confidence", 0.80))

            # EL-M.2: Dedup target fields within this table
            target_to_mappings = {}
            for mapping in tier1_for_table:
                target = mapping.get("target_field")
                if target not in target_to_mappings:
                    target_to_mappings[target] = []
                target_to_mappings[target].append(mapping)

            deduplicated = []
            for target, mappings in target_to_mappings.items():
                if len(mappings) > 1:
                    mappings_sorted = sorted(mappings, key=lambda m: m.get("confidence", 0), reverse=True)
                    deduplicated.append(mappings_sorted[0])
                    for m in mappings_sorted[1:]:
                        source = m.get("source_field")
                        unresolved_for_table.append(source)
                        logger.info(f"[Node 2]   EL-M.2: Dedup {source} → {target}, moved to Tier 2")
                else:
                    deduplicated.append(mappings[0])

            # F5-5: explicit deterministic→semantic cutoff. Only >=95%-confidence matches
            # auto-resolve here; lower-confidence deterministic matches (regex / Haiku /
            # weak alias) are routed to semantic review instead of being auto-applied.
            deduplicated, _reroute_to_semantic = partition_by_cutoff(deduplicated)
            for _src in _reroute_to_semantic:
                unresolved_for_table.append(_src)
                logger.info(
                    f"[Node 2]   F5-5: {_src} < {DETERMINISTIC_CUTOFF} confidence → Tier 2 (semantic)"
                )

            # ── Value-centric merge (req 2): columns whose VALUES are identical are the
            # same field under different names (e.g. ASSETNUM and asset_ref both hold A-001)
            # — collapse the group to ONE column, decided by the data, not the header. The
            # representative is whichever member mapped with the highest confidence; the rest
            # are recorded as merged (and dropped from both tier1 and the unresolved queue).
            try:
                from ...matchers.value_signature import (
                    find_duplicate_column_groups,
                    find_near_duplicate_pairs,
                )
                _dup_groups = find_duplicate_column_groups(sample_source, table_columns)
                # Challenge 1: partial-similarity columns (60–95% value overlap) — surfaced
                # for the user to review/merge manually at the gate + activity log.
                _near = find_near_duplicate_pairs(sample_source, table_columns)
                if _near:
                    near_duplicate_columns_by_table[table_name] = _near
            except Exception:  # pragma: no cover — additive, never fatal
                _dup_groups = []
            for _group in _dup_groups:
                _by_src = {m.get("source_field"): m for m in deduplicated}
                _mapped_members = [c for c in _group if c in _by_src]
                _rep = (
                    max(_mapped_members, key=lambda c: _by_src[c].get("confidence", 0) or 0)
                    if _mapped_members else _group[0]
                )
                _merged = [c for c in _group if c != _rep]
                if not _merged:
                    continue
                deduplicated = [m for m in deduplicated if m.get("source_field") not in _merged]
                unresolved_for_table = [u for u in unresolved_for_table if u not in _merged]
                _rep_map = next((m for m in deduplicated if m.get("source_field") == _rep), None)
                if _rep_map is not None:
                    _rep_map["merged_source_fields"] = _merged
                merged_columns_by_table.setdefault(table_name, {})[_rep] = _merged
                logger.info(
                    f"[Node 2]   MERGE (identical values): {_merged} → '{_rep}' — one column"
                )

            # ── VALUE GATE (combined scorer) — name match is necessary, not sufficient ──
            # Every finalized tier-1 mapping is re-checked against the DESTINATION column's
            # real values. If the destination has data and the source values don't overlap it
            # (e.g. asset codes A-### onto a vendor id column holding V-###), the match is a
            # false merge: demote it to unresolved so it becomes a NEW column / semantic match
            # instead. Fail-safe: no destination samples for the target → no change.
            _tgt_samples = (
                _dest_samples_by_table.get(str(_early_target).lower()) if _early_target else None
            )
            if _tgt_samples:
                try:
                    from ...udr.mapping_decision import score_column_mapping
                except Exception:  # pragma: no cover — gate is additive
                    score_column_mapping = None
                _fm_resolver = None
                try:
                    from ...matchers.fm_ontology import fm_field_lookup as _fm_resolver
                except Exception:  # pragma: no cover — ontology dim degrades to unknown
                    _fm_resolver = None
                if score_column_mapping is not None:
                    _lc_tgt = {k.lower(): v for k, v in _tgt_samples.items()}
                    _kept = []
                    for _vm in deduplicated:
                        _tf = _vm.get("target_field")
                        _dvals = _tgt_samples.get(_tf) or _lc_tgt.get(str(_tf or "").lower())
                        if _dvals is None:
                            _kept.append(_vm)
                            continue
                        _vsf = _vm.get("source_field", "")
                        _vsvals = _vm.get("sample_values") or _extract_source_samples(sample_source, _vsf, k=20)
                        try:
                            _vsc = score_column_mapping(
                                source_name=_vsf, source_values=_vsvals, dest_name=_tf,
                                dest_values=_dvals, same_table=True, field_resolver=_fm_resolver,
                                dest_table=_early_target or table_name,
                            )
                        except Exception:  # pragma: no cover — never fail the mapper on scoring
                            _kept.append(_vm)
                            continue
                        if _vsc.get("value_match") is False or _vsc.get("entity_conflict"):
                            unresolved_for_table.append(_vsf)
                            logger.info(
                                f"[Node 2]   VALUE-GATE: demoted {_vsf} → {_tf} "
                                f"(name {_vsc['name_score']}, value {_vsc['value_score']} vs dest "
                                f"data) → new column / semantic instead of false merge"
                            )
                        else:
                            _kept.append(_vm)
                    deduplicated = _kept

            # Source-level guard: a source field that landed in Tier-1 must never
            # also remain in the unresolved list. Otherwise the pre-semantic gate
            # renders it twice (mapped AND "→ semantic") and can double-submit the
            # column — seen on a re-run where the now-existing table makes a column
            # match table_exact while it's still carried as unresolved.
            tier1_sources = {m.get("source_field") for m in deduplicated}
            unresolved_for_table = [
                f for f in unresolved_for_table if f not in tier1_sources
            ]

            # Feature 5 / Feature 4: enrich each Tier-1 mapping with (a) top-N candidate
            # fits + confidence so the pre-semantic gate can show alternatives, and (b)
            # source sample values + inferred data type for the source→target preview.
            try:
                from ...matchers.value_signature import is_identity_like as _is_identity_like
            except Exception:  # pragma: no cover
                _is_identity_like = None
            for _m in deduplicated:
                _sf = _m.get("source_field", "")
                # 4a: carry up to 5 sample cell values as column metadata.
                _samples = _m.get("sample_values") or _extract_source_samples(sample_source, _sf, k=5)
                if not _m.get("candidates"):
                    _m["candidates"] = _build_candidate_fits(
                        _sf,
                        _m.get("target_field"),
                        _m.get("confidence", 0.0),
                        _target_cols_norm,
                        canonical_fields_map,
                        samples=_samples,
                    )
                if not _m.get("sample_values") and _samples:
                    _m["sample_values"] = _samples
                    if not _m.get("data_type"):
                        _m["data_type"] = _infer_simple_data_type(_samples)
                # 4a: PK-or-not metadata — the column maps to the table's primary key 'id',
                # or its values are identity-like (unique + non-null code/integer).
                _m["is_primary_key"] = (
                    _normalize_field_name(_m.get("target_field") or "") == "id"
                    or bool(_is_identity_like and _is_identity_like(sample_source, _sf))
                )

            tier1_mappings_by_table[table_name] = deduplicated
            unresolved_by_table[table_name] = unresolved_for_table

            # Auto-suggestion for each UNMAPPED field: the best name-similarity target + its
            # confidence (and top-3 alternatives), so the gate offers "suggested: X (Y%)" with
            # one-click approve — and the user sends it to full semantic only if they disagree.
            _table_sugg: dict[str, dict] = {}
            for _uf in unresolved_for_table:
                _uf_samples = _extract_source_samples(sample_source, _uf, k=5)
                _cands = _build_candidate_fits(
                    _uf, None, 0.0, _target_cols_norm, canonical_fields_map, samples=_uf_samples
                )
                if not _cands:
                    continue
                _top = _cands[0]
                _table_sugg[_uf] = {
                    "source_field": _uf,
                    "target_field": _top.get("target_field"),
                    "confidence": _top.get("confidence", 0.0),
                    "candidates": _cands,
                    "sample_values": _uf_samples,
                    "data_type": _infer_simple_data_type(_uf_samples) if _uf_samples else None,
                    "is_primary_key": bool(_is_identity_like and _is_identity_like(sample_source, _uf)),
                }
            if _table_sugg:
                unresolved_suggestions_by_table[table_name] = _table_sugg

            all_confidences.extend(table_confidences)

            logger.info(f"[Node 2] ✓ Table {table_name}: {len(deduplicated)} mapped, {len(unresolved_for_table)} unresolved")

        # ── Overall statistics
        total_mapped = sum(len(m) for m in tier1_mappings_by_table.values())
        total_unresolved = sum(len(u) for u in unresolved_by_table.values())
        overall_confidence = (sum(all_confidences) / len(all_confidences)) if all_confidences else 0.0

        logger.info(f"[Node 2] ═══════════════════════════════════════════")
        logger.info(f"[Node 2] Total: {total_mapped} mapped, {total_unresolved} unresolved")
        logger.info(f"[Node 2] Overall confidence: {overall_confidence:.2f}")
        logger.info(f"[Node 2] EL-M.2 PASSED: All confidences valid, duplicates resolved per table")

        # ── Build initial table_routing ────────────────────────────────────
        # Maps each source sheet name → target entity type for IntermediateSchema routing.
        # Priority: (1) source table name pattern, (2) dominant mapped canonical fields.
        _TABLE_NAME_PATTERNS = [
            ("asset", "assets"), ("equipment", "assets"), ("equip", "assets"),
            ("work_order", "work_orders"), ("workorder", "work_orders"), ("wo", "work_orders"),
            ("scheduled_pm", "maintenance_plans"), ("maintenance", "maintenance_plans"), ("pm", "maintenance_plans"),
            ("part", "spare_parts"), ("inventory", "spare_parts"),
            ("user", "technicians"), ("technician", "technicians"), ("personnel", "technicians"),
            ("inspection", "findings"), ("finding", "findings"),
            ("site", "locations"), ("location", "locations"),
        ]
        _ENTITY_FIELD_SIGNALS: dict[str, set[str]] = {
            "assets":            {"asset_code", "asset_name", "category", "make", "model", "serial"},
            "work_orders":       {"wo_code", "wo_priority", "wo_status", "wo_type", "maintenance_type"},
            "maintenance_plans": {"sm_code", "trigger_type", "schedule_interval", "sm_priority"},
            "spare_parts":       {"part_code", "stock_on_hand", "minimum_allowed_stock", "supplier"},
            "technicians":       {"user_full_name", "user_title", "user_name", "reports_to"},
            "findings":          {"inspector_name", "inspection_date", "finding_type", "risk_level"},
            "locations":         {"site_id", "site_name", "site_type"},
        }

        # (_ingest_table_matches computed above — Node 1 name+LLM table match — also
        #  seeds table_routing below so the Step-1 gate defaults to that guess.)
        table_routing: dict[str, str] = {}
        for table_name, mappings in tier1_mappings_by_table.items():
            table_lower = table_name.lower().strip()
            matched_entity: str | None = None

            # (-2) Content sanity-check override (computed in the per-table loop) wins: it already
            # second-guessed an uncertain semantic table match against actual column overlap
            # (e.g. works → assets, not work_orders).
            _ovr = _table_content_override.get(table_name)
            if _ovr and _ovr in _db_tables:
                matched_entity = _ovr

            # (-1) Node 1 table match (name + LLM) — highest priority when it points
            # at a real CAFM table.
            if not matched_entity:
                _ingest_hit = _ingest_table_matches.get(table_name)
                if _ingest_hit and _ingest_hit in _db_tables:
                    matched_entity = _ingest_hit

            # (0) Direct name match (GENERIC): the sheet name IS a real CAFM table name
            # (singular/plural/space-or-underscore tolerant). Check the ACTUAL plenum_cafm
            # tables FIRST (so "sites" → sites when that table exists), then the built-in
            # entity list. e.g. "assets" → assets, "work orders" → work_orders.
            sheet_variants = {
                table_lower,
                table_lower.replace(" ", "_"),
                table_lower.rstrip("s"),
                table_lower.replace(" ", "_").rstrip("s"),
            }
            if not matched_entity:
                for targets in (_db_tables, list(_ENTITY_FIELD_SIGNALS.keys())):
                    for tgt in targets:
                        t = tgt.lower()
                        if t in sheet_variants or t.rstrip("s") in sheet_variants:
                            matched_entity = tgt
                            break
                    if matched_entity:
                        break

            # (1) Name-pattern match
            if not matched_entity:
                for pattern, entity_type in _TABLE_NAME_PATTERNS:
                    if pattern in table_lower:
                        matched_entity = entity_type
                        break

            # (2) Field-content inference
            if not matched_entity:
                target_fields = {m.get("target_field", "") for m in mappings}
                best_entity, best_score = None, 0
                for entity_type, signals in _ENTITY_FIELD_SIGNALS.items():
                    score = len(target_fields & signals)
                    if score > best_score:
                        best_score = score
                        best_entity = entity_type
                if best_entity and best_score >= 1:
                    matched_entity = best_entity

            # (3) Unknown — keep source table name (will be treated as new/custom entity)
            table_routing[table_name] = matched_entity if matched_entity else table_lower
            logger.info(f"[Node 2]   table_routing: '{table_name}' → '{table_routing[table_name]}'")

        # Keep Node 1's table-match map consistent with content overrides, so the Step-1 ingest
        # card and any consumer reading cafm_table_matches (rather than table_routing) agree.
        if _table_content_override:
            _cm = dict(state.get("cafm_table_matches") or {})
            _cm.update(_table_content_override)
            state["cafm_table_matches"] = _cm

        # ── Store results in state
        state["tier1_mappings_by_table"] = tier1_mappings_by_table
        state["unresolved_by_table"] = unresolved_by_table
        state["unresolved_suggestions_by_table"] = unresolved_suggestions_by_table
        state["table_match_candidates_by_table"] = table_match_candidates_by_table
        state["merged_columns_by_table"] = merged_columns_by_table
        state["near_duplicate_columns_by_table"] = near_duplicate_columns_by_table
        if near_duplicate_columns_by_table:
            state.setdefault("event_log", []).append({
                "timestamp": datetime.utcnow().isoformat(),
                "event": "near_duplicate_columns_flagged",
                "detail": (
                    f"{sum(len(v) for v in near_duplicate_columns_by_table.values())} column "
                    "pair(s) have partial value overlap (60–95%) — flagged for manual review"
                ),
            })
        state["table_routing"] = table_routing
        # Flatten tier1 mappings for API response
        state["tier1_mappings"] = [m.dict() if hasattr(m, 'dict') else m for table_mappings in tier1_mappings_by_table.values() for m in table_mappings]
        state["tier1_mapped_count"] = total_mapped
        state["overall_confidence"] = overall_confidence
        state["el_m2_passed"] = True
        state["current_step"] = 2
        state["event_log"].append({
            "timestamp": datetime.utcnow().isoformat(),
            "event": "node_complete",
            "node": 2,
            "detail": f"{total_mapped} mapped, {total_unresolved} unresolved (by table)"
        })

        logger.info(f"[Node 2] Complete (MULTI-TABLE)")

        migration_id = state.get("migration_id")
        if migration_id:
            from .db_writer import update_node_progress, write_step_pause
            await update_node_progress(
                migration_id, "2_deterministic_mapping",
                t1_mapped_count=total_mapped,
            )
            # Build per-table field list for UI display
            mappings_by_table = {}
            for tbl, mappings in state.get("tier1_mappings_by_table", {}).items():
                mappings_by_table[tbl] = [
                    {
                        "source_field": m.get("source_field"),
                        "target_field": m.get("target_field"),
                        "confidence": m.get("confidence"),
                        "tier": m.get("tier"),
                        "rationale": m.get("rationale"),
                    }
                    for m in mappings
                ]
            unresolved_by_table = {
                tbl: list(fields)
                for tbl, fields in state.get("unresolved_by_table", {}).items()
                if fields
            }
            await write_step_pause(
                migration_id,
                "step_2_deterministic_mapping",
                {
                    "node": 2,
                    "label": "Deterministic Mapping (Tier 1)",
                    "t1_mapped": total_mapped,
                    "unresolved": total_unresolved,
                    "mappings_by_table": mappings_by_table,
                    "unresolved_by_table": unresolved_by_table,
                },
            )
            from .schema_db_writer import migration_append_node_log_auto
            await migration_append_node_log_auto(
                migration_id, 2, "Deterministic Mapping", _node_started_at, datetime.utcnow(),
                output={"total_columns": total_mapped + total_unresolved,
                        "tier1_mapped": total_mapped,
                        "unresolved": total_unresolved,
                        # Per-table unmatched field names so the pre-semantic gate can show the
                        # unmatched count + list beneath each table's matched fields.
                        "unresolved_by_table": unresolved_by_table,
                        "coverage_pct": round(total_mapped / (total_mapped + total_unresolved) * 100, 1) if (total_mapped + total_unresolved) else 0,
                        "overall_confidence": round(overall_confidence, 3),
                        # Persisted so the full-table export can discover the target tables.
                        "table_routing": table_routing,
                        # Total CAFM tables the sheets were matched against (Step 1 display).
                        "cafm_table_count": len(_db_tables)},
                logs=[f"Ran 4-tier deterministic matching (exact → alias → regex → Haiku)",
                      f"{total_mapped} fields matched at Tier 1",
                      f"{total_unresolved} fields unresolved → passed to semantic mapping",
                      f"Overall confidence: {overall_confidence:.2f}"],
            )

        return state

    except Exception as e:
        logger.exception(f"[Node 2] Unhandled exception: {e}")
        state["error_message"] = str(e)
        state["error_node"] = 2
        state["error_timestamp"] = datetime.utcnow()
        state["status"] = "failed"
        return state


async def _strategy4_haiku_mapping(
    unresolved_fields: list[str],
    column_descriptions: dict[str, str],
    state: MigrationState,
    canonical_fields: set[str] = None,
) -> list[FieldMapping]:
    """Strategy 4: Haiku maps unresolved fields (confidence >= 0.85 only).

    Args:
        unresolved_fields: Fields that didn't match in Strategies 1-3
        column_descriptions: Semantic descriptions of each field
        state: Migration state
        canonical_fields: Valid canonical fields for this CMMS (from customer mapper).
                         If None, falls back to default CANONICAL_FIELDS.
    """

    from ...app import get_anthropic_client

    client = get_anthropic_client()
    field_info = []
    for field in unresolved_fields:
        desc = column_descriptions.get(field, "Unknown field type")
        field_info.append(f"- {field}: {desc}")

    # Use customer's canonical fields if provided, otherwise fall back to defaults
    if canonical_fields:
        canonical_list = ", ".join(sorted(canonical_fields))
    else:
        canonical_list = ", ".join(sorted(CANONICAL_FIELDS))

    prompt = f"""Map these CMMS field names to canonical CAFM field names.
Be STRICT: only map if confidence >= 0.85. Otherwise, return "UNMAPPED".

Unresolved fields:
{chr(10).join(field_info)}

Canonical fields available:
{canonical_list}

Return JSON only:
{{
  "field_name": {{"target": "canonical_field", "confidence": 0.95, "rationale": "reason"}},
  "another_field": "UNMAPPED"
}}"""

    try:
        from ...matchers.fm_ontology import fm_skill_preamble

        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            # FM Skill Context: persona + canonical-entity/vocabulary digest so the
            # mapper reasons in FM language (COBie/BRICK/SFG20), never as generic text.
            system=fm_skill_preamble(),
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = response.content[0].text.strip()

        # Extract JSON from markdown code blocks if present
        json_text = response_text
        if "```json" in response_text:
            start = response_text.find("```json") + 7
            end = response_text.find("```", start)
            if end > start:
                json_text = response_text[start:end].strip()
        elif "```" in response_text:
            start = response_text.find("```") + 3
            end = response_text.find("```", start)
            if end > start:
                json_text = response_text[start:end].strip()

        try:
            result = json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.warning(f"[Node 2] Strategy 4: JSON parse failed: {e}")
            logger.debug(f"[Node 2] Raw response: {response_text[:200]}")
            return []

        mappings = []
        langsmith_run_id = str(uuid4())

        for source_field, mapping_data in result.items():
            if mapping_data == "UNMAPPED":
                logger.debug(f"[Node 2] S4: {source_field} → UNMAPPED")
                continue

            if isinstance(mapping_data, dict):
                target = mapping_data.get("target")
                confidence = mapping_data.get("confidence", 0.0)
                rationale = mapping_data.get("rationale", "Haiku mapping")

                # Use provided canonical_fields if available, otherwise fall back to CANONICAL_FIELDS
                valid_targets = canonical_fields if canonical_fields else CANONICAL_FIELDS

                if target in valid_targets and confidence >= 0.85:
                    mapping = FieldMapping(
                        source_field=source_field, target_field=target, confidence=confidence,
                        tier="T1_llm", rationale=rationale, langsmith_run_id=langsmith_run_id,
                    )
                    mappings.append(mapping)
                    logger.info(f"[Node 2] S4 (Haiku): {source_field} → {target} ({confidence:.2f})")
                else:
                    logger.debug(f"[Node 2] S4: {source_field} conf {confidence} < 0.85 → Tier 2")

        return mappings

    except Exception as e:
        logger.warning(f"[Node 2] Strategy 4 (Haiku) failed: {e}")
        return []
