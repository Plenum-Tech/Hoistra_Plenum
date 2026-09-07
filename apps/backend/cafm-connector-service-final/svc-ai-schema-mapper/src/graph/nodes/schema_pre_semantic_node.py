"""Pre-Semantic Human Review Gate for schema mapper (between Node 2 and Node 2.5).

For every deterministic match produced by Node 2, EXCLUDING T1_alias
(alias matches are high-confidence and auto-approved without human review):

    T1_exact, T1_variation, T1_regex, T1_llm  →  shown to human for yes/no
    T1_alias                                   →  automatically approved, skipped

Per-field decision:
    "approve"  →  mapping is finalised; stays in tier1_mappings
    "semantic" →  mapping is rejected; source field is added back to
                  unmapped_after_t1 so Node 3 attempts a semantic match

After this gate Node 3 processes:
    • Fields that were already in unmapped_after_t1 after Node 2
    • Fields rejected ("semantic") at this gate

Approved T1 fields never touch Node 3 and flow straight through via the
existing _should_skip_semantic_mapper conditional edge.

Expected interrupt resume payload (from external API caller):
[
  {"source_table": "assets", "source_field": "EQUIP_NUM",   "decision": "approve"},
  {"source_table": "assets", "source_field": "FAULT_DESC",  "decision": "semantic"}
]

Omitted fields default to "approve".
"""

import logging
import re as _re_t
from datetime import datetime

from langgraph.types import interrupt

from ..schema_state import CanonicalFieldMapping, SchemaMappingFieldInfo, SchemaMappingState

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


# ── Fiix object → CAFM table name derivation (used by the Step-1 table router) ───
def schema_snake(obj: str) -> str:
    """Fiix object → snake_case, collapsing acronym single-letter runs (RFQ → rfq)."""
    s = _re_t.sub(r"(?<!^)(?=[A-Z])", "_", obj or "")
    s = _re_t.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    merged: list[str] = []
    buf = ""
    for p in s.split("_"):
        if len(p) == 1:
            buf += p
        else:
            if buf:
                merged.append(buf)
                buf = ""
            merged.append(p)
    if buf:
        merged.append(buf)
    return "_".join(merged) or "custom_table"


def schema_pluralize(base: str) -> str:
    """English pluralization of the LAST token (country→countries, status→statuses,
    account→accounts) — fixes the naive 'append s' (countrys / prioritys / move_status)."""
    if not base:
        return base
    head, _, last = base.rpartition("_")
    prefix = f"{head}_" if head else ""
    if last.endswith("y") and len(last) > 1 and last[-2] not in "aeiou":
        pl = last[:-1] + "ies"
    elif last.endswith(("s", "x", "z")) or last.endswith(("ch", "sh")):
        pl = last if last.endswith("es") else last + "es"
    else:
        pl = last + "s"
    return prefix + pl


def schema_suggest_table_name(obj: str) -> str:
    return schema_pluralize(schema_snake(obj))


def schema_candidate_table_names(obj: str) -> list[str]:
    """Ordered names to test against the EXISTING CAFM tables before creating a new one:
    proper plural, the bare snake form, and a simple +s — so Account→accounts,
    Country→countries, MoveStatus→move_statuses, RFQ→rfqs all match what already exists."""
    base = schema_snake(obj)
    out: list[str] = []
    for c in (schema_pluralize(base), base, base + "s"):
        if c and c not in out:
            out.append(c)
    return out

# Tiers that require human sign-off at this gate.
# T1_alias is intentionally absent — alias matches auto-pass.
_TIERS_REQUIRING_REVIEW: frozenset[str] = frozenset(
    {"T1_exact", "T1_table_exact", "T1_variation", "T1_regex", "T1_llm"}
)


async def schema_pre_semantic_node(state: SchemaMappingState) -> SchemaMappingState:
    """
    Pre-Semantic Human Review Gate — interrupts for per-field approve/semantic.

    Logic:
    1. Partition tier1_mappings into:
       - reviewable (T1_exact / T1_variation / T1_regex / T1_llm)
       - auto_pass  (T1_alias and any unknown tier)
    2. If nothing is reviewable, skip gate entirely (no interrupt).
    3. Otherwise write payload to DB → interrupt → wait for external decisions.
    4. Process decisions:
       - approve  → mapping stays in tier1_mappings
       - semantic → mapping removed; field added back to unmapped_after_t1
    5. Store updated tier1_mappings and unmapped_after_t1 in state.
    """

    schema_mapping_id = state.get("schema_mapping_id")
    tier1_mappings: list[CanonicalFieldMapping] = list(state.get("tier1_mappings", []))
    unmapped_after_t1: list[SchemaMappingFieldInfo] = list(state.get("unmapped_after_t1", []))
    external_tables: dict = state.get("external_tables", {})

    # ── Partition mappings ─────────────────────────────────────────────
    # EVERY deterministic mapping is reviewable — the user explicitly approves each one or
    # sends it to semantic; nothing (incl. alias / identity matches) is silently
    # auto-approved at this gate. Default decision stays "approve", so a one-click
    # "Approve all" keeps the fast path while still allowing per-field override.
    reviewable: list[CanonicalFieldMapping] = list(tier1_mappings)
    auto_pass: list[CanonicalFieldMapping] = []

    total_reviewable = len(reviewable)

    # ── Fast-path: nothing to review ──────────────────────────────────
    if total_reviewable == 0:
        logger.info(
            "[Schema Pre-Semantic Gate] No reviewable T1 mappings (all alias or none). "
            "Gate skipped — all deterministic mappings auto-approved."
        )
        state["pre_semantic_review_payload"] = None
        state["event_log"] = list(state.get("event_log", [])) + [{
            "timestamp": datetime.utcnow().isoformat(),
            "event": "gate_skipped",
            "gate": "schema_pre_semantic",
            "detail": "No reviewable T1 mappings; gate bypassed",
        }]
        return state

    # ── Build interrupt payload (grouped by table for UI) ────────────
    # Group reviewable mappings by source_table for easier frontend rendering
    items_by_table: dict[str, list[dict]] = {}
    for m in reviewable:
        src_table = m.get("source_table", "unknown")
        items_by_table.setdefault(src_table, []).append({
            "source_table": src_table,
            "source_field": m.get("source_field"),
            "target_field": m.get("target_field"),
            "confidence": m.get("confidence"),
            "tier": m.get("tier"),
            "rationale": m.get("rationale"),
            "auto_mappable": m.get("auto_mappable", False),
            "sample_values": _get_sample_values(external_tables, src_table, m.get("source_field")),
        })

    # Auto-approved deterministic mappings (T1_alias / unknown tiers) — shown read-only so
    # the gate displays EVERY mapped column, not only the ones needing review.
    auto_approved_by_table: dict[str, list[dict]] = {}
    for m in auto_pass:
        st = m.get("source_table", "unknown")
        auto_approved_by_table.setdefault(st, []).append({
            "source_table": st,
            "source_field": m.get("source_field"),
            "target_field": m.get("target_field"),
            "confidence": m.get("confidence"),
            "tier": m.get("tier"),
        })

    # Source columns with NO deterministic match (neither reviewable nor auto) — surface
    # them so the gate shows the COMPLETE column list per table; each can be sent to
    # semantic (Tier-2) or created as a new column (mirrors the migration pre-semantic gate).
    matched_fields_by_table: dict[str, set] = {}
    for m in tier1_mappings:
        matched_fields_by_table.setdefault(m.get("source_table"), set()).add(m.get("source_field"))
    unmapped_by_table: dict[str, list[dict]] = {}
    for tname, tinfo in external_tables.items():
        matched = matched_fields_by_table.get(tname, set())
        unmatched = [
            c for c in (tinfo.get("columns") or [])
            if c.get("field_name") and c.get("field_name") not in matched
        ]
        if unmatched:
            unmapped_by_table[tname] = [
                {
                    "source_table": tname,
                    "source_field": c.get("field_name"),
                    "data_type": c.get("data_type"),
                    "sample_values": _get_sample_values(external_tables, tname, c.get("field_name")),
                }
                for c in unmatched
            ]

    # CAFM target table for each Fiix source table + that table's columns, so the
    # gate can show "Target table: X" and offer a column dropdown (Fiix → CAFM
    # table & columns), mirroring the migration flow.
    canonical_tables: dict = state.get("canonical_tables", {})
    _table_overrides: dict = state.get("table_overrides") or {}
    _new_tables_req: dict = state.get("new_tables_requested") or {}
    target_table_by_source: dict[str, str] = {}
    canonical_columns_by_table: dict[str, list[str]] = {}
    new_table_by_source: dict[str, str] = {}

    try:
        from ...connectors.fiix_plenum_mappings import plenum_table_for_fiix_object
        # Route EVERY Fiix object (not just ones with reviewable columns) so the Step-1
        # table list is complete — fully-new tables (all columns unmapped) appear too.
        for src_table in external_tables:
            # A user override (Step-1 table re-routing) wins over the default mapping.
            override = _table_overrides.get(src_table)
            tgt = override or plenum_table_for_fiix_object(src_table)
            if tgt and tgt in canonical_tables:
                target_table_by_source[src_table] = tgt
                continue
            # No explicit mapping to an existing table. Before defaulting to 'create new',
            # auto-match the object name (incl. proper plural forms) to an EXISTING CAFM
            # table — so an object whose target already exists (Account→accounts,
            # Country→countries, MaintenanceType→maintenance_types) is reused, not recreated.
            if not override:
                match = next(
                    (c for c in schema_candidate_table_names(src_table) if c in canonical_tables),
                    None,
                )
                if match:
                    target_table_by_source[src_table] = match
                    continue
            # Genuinely new (or a user-specified new target) → suggest a well-formed name.
            new_table_by_source[src_table] = _new_tables_req.get(src_table) or schema_suggest_table_name(src_table)
        for tname, tinfo in canonical_tables.items():
            cols = [c.get("field_name") for c in tinfo.get("columns", []) if c.get("field_name")]
            if cols:
                canonical_columns_by_table[tname] = sorted(cols)
    except Exception as exc:  # pragma: no cover - best-effort enrichment
        logger.warning(f"[Schema Pre-Semantic Gate] Could not enrich target tables: {exc}")

    review_payload = {
        "schema_mapping_id": schema_mapping_id,
        "gate": "schema_pre_semantic",
        "total_reviewable": total_reviewable,
        "items_by_table": items_by_table,
        # Auto-approved (read-only) + unmapped (→ semantic / create-new) columns, so Step 2
        # shows the COMPLETE column list per table, not only the reviewable T1 matches.
        "auto_approved_by_table": auto_approved_by_table,
        "unmapped_by_table": unmapped_by_table,
        # Fiix source table → CAFM target table, and CAFM table → its columns.
        "target_table_by_source": target_table_by_source,
        "new_table_by_source": new_table_by_source,
        # Step-1 header counts: how many Fiix objects matched an EXISTING CAFM table vs
        # will create a NEW one (matched + new == total source objects).
        "matched_table_count": len(target_table_by_source),
        "new_table_count": len(new_table_by_source),
        "total_table_count": len(external_tables),
        "all_source_tables": sorted(external_tables.keys()),
        # Every Fiix object's full column list (name + source type), so Step 2 can show
        # creatable new columns for a table routed to a NEW CAFM table.
        "source_columns_by_table": {
            tname: [
                {"field_name": c.get("field_name"), "data_type": c.get("data_type")}
                for c in (tinfo.get("columns") or [])
                if c.get("field_name")
            ]
            for tname, tinfo in external_tables.items()
        },
        "canonical_columns_by_table": canonical_columns_by_table,
        "existing_canonical_tables": sorted(canonical_tables.keys()),
        "instructions": (
            "Review each deterministically matched field. "
            "Decision options per field:\n"
            "  'approve'  — accept the mapping as final (skips semantic matching)\n"
            "  'semantic' — reject and run through semantic matching instead\n"
            "Alias-matched fields have already been auto-approved and are not shown."
        ),
    }

    logger.info(
        f"[Schema Pre-Semantic Gate] Interrupting for review: {total_reviewable} fields "
        f"across {len(items_by_table)} tables. schema_mapping_id={schema_mapping_id}"
    )
    state["pre_semantic_review_payload"] = review_payload

    # ── Write payload to DB so frontend can read it ────────────────────
    if schema_mapping_id:
        from .schema_db_writer import schema_write_gate_payload_auto
        await schema_write_gate_payload_auto(schema_mapping_id, "pre_semantic", review_payload)

    # ── Interrupt — resume with decisions list ──────────────────────────
    # decisions: list[{source_table, source_field, decision}]
    _gate_started_at = datetime.utcnow()
    resumed = interrupt(review_payload)

    # Structured resume {"decisions": [...], "table_overrides": {...}} (Step-1 table
    # re-routing) OR legacy flat list of decisions.
    if isinstance(resumed, dict) and "decisions" in resumed:
        decisions: list[dict] = resumed.get("decisions") or []
        table_overrides: dict = resumed.get("table_overrides") or {}
        new_tables: dict = resumed.get("new_tables") or {}
        new_columns: dict = resumed.get("new_columns") or {}
    else:
        decisions = resumed if isinstance(resumed, list) else []
        table_overrides = {}
        new_tables = {}
        new_columns = {}

    if table_overrides:
        merged = {**(state.get("table_overrides") or {}), **{
            str(k): str(v) for k, v in table_overrides.items() if v
        }}
        state["table_overrides"] = merged
        logger.info(f"[Schema Pre-Semantic Gate] Table overrides applied: {merged}")

    if new_tables:
        merged_new = {**(state.get("new_tables_requested") or {}), **{
            str(k): str(v) for k, v in new_tables.items() if v
        }}
        state["new_tables_requested"] = merged_new
        logger.info(f"[Schema Pre-Semantic Gate] New tables requested: {merged_new}")

    # New-table columns with explicit SQL types (Step 2). Emit one custom is_new_table
    # extra-field entry per column so the write node CREATEs the table with them, and
    # mark each as deterministically approved so it never goes to semantic.
    _new_cols_approved: list = []
    _new_cols_handled: set = set()
    if new_columns:
        _nt = state.get("new_tables_requested") or {}
        _extra = list(state.get("extra_fields_config") or [])
        for src_table, cols in new_columns.items():
            tgt = (_nt.get(src_table) or "").strip()
            if not tgt or not isinstance(cols, list):
                continue
            for col in cols:
                if not isinstance(col, dict):
                    continue
                name = (col.get("column_name") or "").strip()
                if not name:
                    continue
                dtype = (col.get("data_type") or "VARCHAR(255)").strip() or "VARCHAR(255)"
                src_field = col.get("source_field") or name
                _new_cols_handled.add((src_table, src_field))
                _new_cols_approved.append(CanonicalFieldMapping(
                    source_field=src_field,
                    source_table=src_table,
                    target_field=name,
                    confidence=1.0,
                    tier="T1_new_table",
                    rationale=f"New column created on new table '{tgt}'",
                    auto_mappable=True,
                    human_review_needed=False,
                ))
                if any(e.get("target_table") == tgt and e.get("custom_column_name") == name for e in _extra):
                    continue
                _extra.append({
                    "source_field": src_field,
                    "source_table": src_table,
                    "storage_strategy": "custom",
                    "target_table": tgt,
                    "custom_column_name": name,
                    "data_type": dtype,
                    "is_new_table": True,
                    "new_table_pk": "id",
                    "nullable": True,
                    "user_approved": True,
                })
        state["extra_fields_config"] = _extra
        logger.info(
            f"[Schema Pre-Semantic Gate] New-table columns: "
            f"{sum(len(v) for v in new_columns.values() if isinstance(v, list))} typed columns"
        )

    # ── Clear gate payload now that we have decisions ──────────────────
    if schema_mapping_id:
        from .schema_db_writer import schema_clear_gate_payload_auto
        await schema_clear_gate_payload_auto(schema_mapping_id)

    logger.info(
        f"[Schema Pre-Semantic Gate] Resumed with {len(decisions)} decisions"
    )

    # ── Build decision lookup: (source_table, source_field) → decision ──
    decision_map: dict[tuple[str, str], str] = {
        (d.get("source_table", ""), d.get("source_field", "")): d.get("decision", "approve")
        for d in decisions
        if d.get("source_field")
    }
    # Optional per-field target-column override (from the gate's column dropdown).
    rename_map: dict[tuple[str, str], str] = {
        (d.get("source_table", ""), d.get("source_field", "")): (d.get("target_field") or "").strip()
        for d in decisions
        if d.get("source_field") and (d.get("target_field") or "").strip()
    }

    # ── Process decisions ──────────────────────────────────────────────
    approved_mappings: list[CanonicalFieldMapping] = []
    total_approved = 0
    total_sent_to_semantic = 0

    for m in reviewable:
        src_table = m.get("source_table", "")
        src_field = m.get("source_field", "")
        decision = decision_map.get((src_table, src_field), "approve")

        if decision == "approve":
            # Apply a user column override from the gate's dropdown.
            _tf = rename_map.get((src_table, src_field))
            if _tf and _tf != m.get("target_field"):
                m = {**m, "target_field": _tf}
            approved_mappings.append(m)
            total_approved += 1
            logger.info(
                f"[Schema Pre-Semantic Gate]   ✓ APPROVED  {src_table}.{src_field} "
                f"→ {m.get('target_field')} ({m.get('confidence', 0):.2f})"
            )
        else:  # "semantic"
            # Reconstruct a SchemaMappingFieldInfo stub so Node 3 can pick it up
            field_info = _build_field_info_stub(external_tables, src_table, src_field)
            unmapped_after_t1.append(field_info)
            total_sent_to_semantic += 1
            logger.info(
                f"[Schema Pre-Semantic Gate]   → SEMANTIC  {src_table}.{src_field} "
                f"(was {m.get('tier')})"
            )

    # Final tier1_mappings = alias auto-pass + human-approved + new-table columns
    # (created → APPROVED at the deterministic level).
    updated_tier1 = auto_pass + approved_mappings + _new_cols_approved

    # New-table columns are created, not matched — drop them from the semantic queue.
    if _new_cols_handled:
        unmapped_after_t1 = [
            f for f in unmapped_after_t1
            if (f.get("source_table"), f.get("field_name") or f.get("source_field")) not in _new_cols_handled
        ]

    # ── Update state ───────────────────────────────────────────────────
    state["tier1_mappings"] = updated_tier1
    state["tier1_mapped_count"] = len(updated_tier1)
    state["unmapped_after_t1"] = unmapped_after_t1
    state["pre_semantic_review_payload"] = None

    logger.info(
        f"[Schema Pre-Semantic Gate] ══ Summary ══ "
        f"approved={total_approved}, sent_to_semantic={total_sent_to_semantic}"
    )

    state["event_log"] = list(state.get("event_log", [])) + [{
        "timestamp": datetime.utcnow().isoformat(),
        "event": "gate_complete",
        "gate": "schema_pre_semantic",
        "detail": (
            f"{total_approved} approved, {total_sent_to_semantic} sent to semantic matching"
        ),
    }]

    if schema_mapping_id:
        from .schema_db_writer import schema_append_node_log_auto
        await schema_append_node_log_auto(
            schema_mapping_id, 3, "Pre-Semantic Review", _gate_started_at, datetime.utcnow(),
            output={
                "approved": total_approved,
                "sent_to_semantic": total_sent_to_semantic,
                "updated_tier1_count": len(updated_tier1),
                "total_reviewable": total_reviewable,
            },
            logs=[
                "Gate: schema pre-semantic human review",
                f"{total_approved} T1 mappings approved",
                f"{total_sent_to_semantic} mappings rejected → sent to semantic matching",
                f"Alias auto-passed: {len(auto_pass)}",
            ],
        )

    return state


def _get_sample_values(
    external_tables: dict,
    source_table: str,
    source_field: str,
) -> list:
    """Pull sample_values from external_tables for a given field, or return empty list."""
    table_info = external_tables.get(source_table, {})
    columns: list = table_info.get("columns", [])
    for col in columns:
        if col.get("field_name") == source_field:
            return col.get("sample_values") or []
    return []


def _build_field_info_stub(
    external_tables: dict,
    source_table: str,
    source_field: str,
) -> SchemaMappingFieldInfo:
    """
    Build a SchemaMappingFieldInfo for a field being sent back to semantic mapping.

    Tries to look up the full column info from external_tables. Falls back to a
    minimal stub if not found.
    """
    table_info = external_tables.get(source_table, {})
    columns: list = table_info.get("columns", [])
    for col in columns:
        if col.get("field_name") == source_field:
            return col  # type: ignore[return-value]

    # Fallback: minimal stub
    return SchemaMappingFieldInfo(
        field_name=source_field,
        data_type="unknown",
        nullable=True,
        is_primary_key=False,
        is_foreign_key=False,
        fk_target_table=None,
        fk_target_column=None,
        description=None,
        sample_values=None,
        avg_char_length=None,
        max_char_length=None,
    )
