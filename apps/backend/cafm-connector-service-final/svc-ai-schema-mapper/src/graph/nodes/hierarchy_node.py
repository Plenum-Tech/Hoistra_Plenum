"""Node 6: Hierarchy detection — LLM-powered semantic understanding.

Steps:
1. Build complete schema summary (all tables, columns, sample data)
2. Send to Claude Sonnet for semantic data model understanding
3. Claude infers FK relationships and hierarchy levels
4. Validate inferred relationships against actual data
5. Detect cycles and resolve self-referencing trees
6. Classify relationships by type (CONTAINMENT/REFERENCE/OWNERSHIP)

EL-M.6: No cycles in containment graph
"""

import json
import logging
from datetime import datetime
from uuid import uuid4

from ...hierarchy import (
    build_default_hierarchy_for_single_table,
    detect_cycles,
    detect_implicit_hierarchies,
    is_single_table_import,
    resolve_self_referencing_trees,
    scan_foreign_keys,
    scan_self_referential_foreign_keys,
    validate_foreign_keys,
)
from ..state import HierarchyRelationship, MigrationState

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


async def hierarchy_node(state: MigrationState) -> MigrationState:
    """
    Node 6: Detect and classify hierarchical relationships in cleaned data.

    Args:
        state: MigrationState with cleaned_tables

    Returns:
        Updated state with fk_candidates, confirmed_hierarchies, containment_hierarchy,
        hierarchy_cycles, implicit_hierarchies
    """

    _node_started_at = datetime.utcnow()
    migration_id = state.get("migration_id")
    cleaned_tables = state.get("cleaned_tables", {})

    # ── Set up log capture ──────────────────────────────────────────────
    execution_logs = []

    def log(msg: str):
        execution_logs.append(f"[Node 6] {msg}")
        logger.info(f"[Node 6] {msg}")

    log(f"Starting hierarchy detection: migration_id={migration_id}")

    if not cleaned_tables:
        log("ERROR: No cleaned tables found")
        state["error_message"] = "No data for hierarchy detection"
        state["error_node"] = 6
        state["execution_logs"] = execution_logs
        return state

    try:
        # ── Step 1: Column-name FK scan (table + column patterns) ─────
        column_names_per_table: dict[str, list[str]] = {}
        schema_summary: dict[str, dict] = {}

        for table_name, records in cleaned_tables.items():
            if not records:
                continue
            columns = list(records[0].keys())
            column_names_per_table[table_name] = columns
            schema_summary[table_name] = {
                "columns": columns,
                "row_count": len(records),
                "sample_row": {k: str(v)[:50] for k, v in records[0].items()},
            }

        table_names = list(column_names_per_table.keys())
        log(f"Schema summary: {len(schema_summary)} tables")

        scan_candidates = scan_foreign_keys(table_names, column_names_per_table)
        scan_candidates.extend(scan_self_referential_foreign_keys(column_names_per_table))
        log(f"Column-name scan: {len(scan_candidates)} FK candidates")

        validated_scan = validate_foreign_keys(scan_candidates, cleaned_tables)
        confirmed_from_scan = [fk for fk in validated_scan if fk.get("validated")]
        log(f"Data-validated from column names: {len(confirmed_from_scan)} FKs")

        implicit_hierarchies = detect_implicit_hierarchies(cleaned_tables)
        if implicit_hierarchies:
            log(f"Implicit code hierarchies: {len(implicit_hierarchies)} column(s)")

        # ── Step 2: LLM semantic pass (optional enrichment) ─────────
        claude_fks: list[dict] = []
        hierarchy_levels: dict[str, int] = _levels_from_relationships(confirmed_from_scan)

        try:
            claude_fks, claude_levels = await _claude_infer_hierarchies(schema_summary, log)
            if claude_levels:
                hierarchy_levels = {**hierarchy_levels, **claude_levels}
            log(f"Claude inferred: {len(claude_fks)} FK relationships")
        except Exception as e:
            log(f"Claude semantic pass skipped (using column-name detection): {e}")

        claude_validated = _validate_inferred_fk_dicts(claude_fks, cleaned_tables, log)
        merged_fks = _merge_fk_dicts(confirmed_from_scan, claude_validated)

        single_table_meta: dict = {}
        if is_single_table_import(cleaned_tables):
            only_table = next(n for n, rows in cleaned_tables.items() if rows)
            merged_fks, hierarchy_levels, single_table_meta = build_default_hierarchy_for_single_table(
                only_table,
                column_names_per_table.get(only_table, []),
                merged_fks,
            )
            log(
                f"Single-table import '{only_table}': applied Plenum default hierarchy "
                f"(role={single_table_meta.get('import_table_plenum_role')})"
            )
        elif not hierarchy_levels:
            hierarchy_levels = _levels_from_relationships(merged_fks)

        # ── Collapse DUPLICATE source tables (work_order + workorders) to one representative ──
        # Two tables with the SAME column set are duplicates (same entity from two files). The
        # detected hierarchy / tree / relationships should show them ONCE (matching the ×2 collapse
        # in the analysis views), not as separate nodes. FKs are remapped to the representative and
        # deduped; intra-duplicate self-edges are dropped. Row data is untouched here — both files'
        # rows are still unioned into the single destination table at output (Node 9).
        # Detect duplicates two ways (a table matching EITHER is collapsed):
        #  1. Same ROUTED DESTINATION — work_order + workorders both route to work_orders and union
        #     into ONE table at write, so they are the same entity even if a column drifted. This is
        #     the robust signal (column sets can differ after per-table NaN/merge cleaning).
        #  2. Identical column SET — fallback for unrouted / new tables.
        _routing = state.get("table_routing") or {}
        _matches = state.get("cafm_table_matches") or {}

        def _dest_of(t: str) -> str:
            return _routing.get(t) or _matches.get(t) or t

        _by_key: dict = {}
        for _t, _rows in (cleaned_tables or {}).items():
            _d = _dest_of(_t)
            # Group by destination when the table is routed to a DIFFERENT table (a real merge
            # target); otherwise fall back to its column set so identical unrouted sheets still pair.
            if _d and _d != _t:
                _key = ("dest", _d)
            else:
                _cols = frozenset((_rows[0].keys() if _rows else []))
                _key = ("cols", _cols) if _cols else ("self", _t)
            _by_key.setdefault(_key, []).append(_t)
        _dup_rep: dict[str, str] = {}
        _dup_count: dict[str, int] = {}
        for _key, _ts in _by_key.items():
            if len(_ts) >= 2:
                _r = sorted(_ts)[0]
                for _t in _ts:
                    _dup_rep[_t] = _r
                _dup_count[_r] = len(_ts)
        if _dup_rep:
            def _rep(t):
                return _dup_rep.get(t, t)
            _collapsed: dict = {}
            for fk in merged_fks:
                st, tt = _rep(fk.get("source_table")), _rep(fk.get("target_table"))
                if st == tt:
                    continue  # self-edge created by collapsing a duplicate table
                key = (st, fk.get("source_column"), tt, fk.get("target_column"))
                if key not in _collapsed:
                    _collapsed[key] = {**fk, "source_table": st, "target_table": tt}
            merged_fks = list(_collapsed.values())
            hierarchy_levels = _levels_from_relationships(merged_fks)
            log(f"Collapsed duplicate tables in hierarchy: {sorted({t for t, r in _dup_rep.items() if t != r})} → representative")

        log(f"Merged FKs for review: {len(merged_fks)}")

        if not hierarchy_levels:
            hierarchy_levels = _levels_from_relationships(merged_fks)

        # ── Step 3: HierarchyRelationship list for GATE 2 ───────────
        confirmed_hierarchies: list[HierarchyRelationship] = []
        for fk in merged_fks:
            match_rate = float(fk.get("data_match_rate") or fk.get("confidence") or 0.0)
            reasoning = fk.get("reasoning") or ""
            if fk.get("system_default"):
                pass  # reasoning already set in default_plenum_hierarchy
            elif fk.get("pattern_matched"):
                reasoning = (
                    f"Detected from column name pattern ({fk['pattern_matched']}): "
                    f"{fk['source_table']}.{fk['source_column']} → "
                    f"{fk['target_table']}.{fk.get('target_column', 'id')}. {reasoning}"
                ).strip()
            confirmed_hierarchies.append(
                HierarchyRelationship(
                    source_table=fk["source_table"],
                    source_column=fk["source_column"],
                    target_table=fk["target_table"],
                    target_column=fk.get("target_column", "id"),
                    relationship_type=fk.get("relationship_type", "CONTAINMENT"),
                    confidence=float(fk.get("confidence") or match_rate),
                    data_match_rate=match_rate,
                    reasoning=reasoning,
                    customer_confirmed=False,
                    confirmed_at=None,
                    system_default=bool(fk.get("system_default")),
                    mapping_note=bool(fk.get("mapping_note")),
                )
            )

        # ── Step 3b (#7): supplement with FM ontology-implied parent→child edges ──
        # Combines FK scan + naming (scan) + Claude semantic (above) with the FM
        # ontology lens. Only edges backed by a REAL referencing column are added,
        # each relationship_type=ONTOLOGY so the customer confirms at Gate 2 (user
        # corrections feed back). Best-effort — never fatal.
        try:
            from ..fm_ontology_hierarchy import canon_key, derive_ontology_edges

            _routing = state.get("table_routing") or {}
            _src_to_canon = {t: canon_key(_routing.get(t) or t) for t in table_names}
            _existing = {
                (
                    str(h.get("source_table") or "").lower(),
                    str(h.get("source_column") or "").lower(),
                    str(h.get("target_table") or "").lower(),
                )
                for h in confirmed_hierarchies
            }
            _added = 0
            for e in derive_ontology_edges(_src_to_canon, column_names_per_table):
                k = (
                    str(e["source_table"]).lower(),
                    str(e["source_column"]).lower(),
                    str(e["target_table"]).lower(),
                )
                if k in _existing:
                    continue
                _existing.add(k)
                confirmed_hierarchies.append(
                    HierarchyRelationship(
                        source_table=e["source_table"],
                        source_column=e["source_column"],
                        target_table=e["target_table"],
                        target_column=e.get("target_column", "id"),
                        relationship_type=e["relationship_type"],
                        confidence=float(e.get("confidence") or 0.6),
                        data_match_rate=float(e.get("data_match_rate") or 0.0),
                        reasoning=e.get("reasoning", ""),
                        customer_confirmed=False,
                        confirmed_at=None,
                        system_default=False,
                        mapping_note=False,
                    )
                )
                _added += 1
            if _added:
                log(f"FM ontology: added {_added} ontology-implied relationship(s) for review")
        except Exception as _ont_err:  # pragma: no cover — supplement is additive
            log(f"FM ontology supplement skipped: {_ont_err}")

        # ── FINAL duplicate collapse over the COMPLETE relationship set ──────────────────────────
        # Sheets co-routed to the same destination (work_order + workorders → work_orders) are ONE
        # entity and must appear once in the Gate-2 tree. The earlier merged_fks collapse only
        # covered the scan/Claude edges; the FM-ontology edges above are appended afterwards and
        # were re-introducing the duplicate ("├── work_order └── workorders"). Collapsing here
        # catches every source, and is derived from the relationships themselves so it works even
        # when the earlier cleaned_tables-based grouping found nothing. Best-effort — never fatal.
        try:
            _fr = state.get("table_routing") or {}
            _fm = state.get("cafm_table_matches") or {}

            def _final_dest(t: str) -> str:
                return _fr.get(t) or _fm.get(t) or t

            _tables_in_rel: set[str] = set()
            for _h in confirmed_hierarchies:
                for _k in ("source_table", "target_table"):
                    _v = str(_h.get(_k) or "").strip()
                    if _v:
                        _tables_in_rel.add(_v)
            _dest_groups: dict[str, list[str]] = {}
            for _t in _tables_in_rel:
                _d = _final_dest(_t)
                if _d and _d != _t:  # only a real merge target groups two sheets together
                    _dest_groups.setdefault(_d, []).append(_t)
            _final_rep: dict[str, str] = {}
            for _d, _ts in _dest_groups.items():
                if len(_ts) >= 2:
                    _r = sorted(_ts)[0]
                    for _t in _ts:
                        _final_rep[_t] = _r
            if _final_rep:
                _seen_rel: set[tuple] = set()
                _out: list = []
                for _h in confirmed_hierarchies:
                    _st = _final_rep.get(str(_h.get("source_table") or ""), _h.get("source_table"))
                    _tt = _final_rep.get(str(_h.get("target_table") or ""), _h.get("target_table"))
                    if _st == _tt:
                        continue  # self-edge created by collapsing a duplicate sheet
                    _key = (_st, _h.get("source_column"), _tt, _h.get("target_column"))
                    if _key in _seen_rel:
                        continue
                    _seen_rel.add(_key)
                    _out.append({**_h, "source_table": _st, "target_table": _tt})
                _dropped = len(confirmed_hierarchies) - len(_out)
                confirmed_hierarchies = _out
                hierarchy_levels = _levels_from_relationships(confirmed_hierarchies)
                log(
                    f"Collapsed co-routed duplicate sheets in relationships: "
                    f"{sorted(t for t, r in _final_rep.items() if t != r)} → representative "
                    f"({_dropped} duplicate relationship(s) removed)"
                )
                # Expose the groups so the Gate-2 tree / UI can label "work_order ×2".
                state["hierarchy_duplicate_groups"] = [
                    {
                        "representative": _r,
                        "members": sorted(t for t, rr in _final_rep.items() if rr == _r),
                        "count": len([t for t, rr in _final_rep.items() if rr == _r]),
                    }
                    for _r in sorted(set(_final_rep.values()))
                ]
        except Exception as _dup_err:  # pragma: no cover — collapse is additive
            log(f"Duplicate relationship collapse skipped: {_dup_err}")

        # ── Step 4: Cycles → GATE 2 review (do not fail pipeline) ─
        hierarchy_cycles = detect_cycles(merged_fks)
        log(f"Found: {len(hierarchy_cycles)} cycle(s)")
        state["el_m6_passed"] = len(hierarchy_cycles) == 0
        if hierarchy_cycles:
            log("EL-M.6: Cycles detected — customer will resolve at Hierarchy Review (Gate 2)")

        # ── Step 5: Self-referential trees + containment ─────────────
        self_ref_fks = [fk for fk in merged_fks if fk.get("source_table") == fk.get("target_table")]
        tree_structures = (
            resolve_self_referencing_trees(cleaned_tables, self_ref_fks) if self_ref_fks else {}
        )
        if tree_structures:
            log(f"Resolved {len(tree_structures)} self-referencing trees")

        containment_hierarchy = _build_containment_hierarchy(
            confirmed_hierarchies, tree_structures, hierarchy_levels
        )

        # ── Update state ───────────────────────────────────────────
        # Duplicate-table groups collapsed above (work_order + workorders → work_order), exposed so
        # the hierarchy tree / relationships UI can label the representative "work_order ×2".
        # NB: do NOT clobber the groups the FINAL relationship collapse already published — that
        # pass runs on the complete set (incl. FM-ontology edges) and is the authority when the
        # earlier cleaned_tables-based grouping found nothing.
        if _dup_rep:
            state["hierarchy_duplicate_groups"] = [
                {"representative": _r, "members": sorted(t for t, r in _dup_rep.items() if r == _r),
                 "count": _dup_count.get(_r, 0)}
                for _r in sorted(set(_dup_rep.values()))
            ]
        else:
            state.setdefault("hierarchy_duplicate_groups", [])
        state["fk_candidates"] = scan_candidates + claude_fks
        state["confirmed_hierarchies"] = confirmed_hierarchies
        state["containment_hierarchy"] = containment_hierarchy
        state["hierarchy_cycles"] = hierarchy_cycles
        state["implicit_hierarchies"] = implicit_hierarchies
        state["hierarchy_levels"] = hierarchy_levels
        if single_table_meta:
            state["single_table_hierarchy_mode"] = True
            state["import_table_name"] = single_table_meta.get("import_table_name")
            state["import_table_plenum_role"] = single_table_meta.get("import_table_plenum_role")
            state["plenum_default_structure"] = single_table_meta.get("proposed_structure")
        state["execution_logs"] = execution_logs

        log(
            f"Complete: {len(confirmed_hierarchies)} relationships for Gate 2, "
            f"levels={hierarchy_levels}"
        )

        state["current_step"] = 6
        state["event_log"].append(
            {
                "timestamp": datetime.utcnow().isoformat(),
                "event": "node_complete",
                "node": 6,
                "detail": f"{len(confirmed_hierarchies)} hierarchies detected",
            }
        )

        migration_id = state.get("migration_id")
        if migration_id:
            from .db_writer import update_node_progress, write_step_pause
            await update_node_progress(migration_id, "6_hierarchy")
            await write_step_pause(
                migration_id,
                "step_7_hierarchy",
                {
                    "node": 7,
                    "label": "Hierarchy Detection",
                    "hierarchies": len(confirmed_hierarchies),
                    "relationships_preview": [
                        {
                            "source_table": h.get("source_table"),
                            "source_column": h.get("source_column"),
                            "target_table": h.get("target_table"),
                            "target_column": h.get("target_column"),
                            "relationship_type": h.get("relationship_type"),
                            "confidence": h.get("confidence"),
                        }
                        # No truncation — surface ALL detected FKs so the
                        # detection snapshot / migration history shows every
                        # relationship (requirement: display all FKs).
                        for h in confirmed_hierarchies
                    ],
                    "cycles": len(hierarchy_cycles),
                    "orphans": state.get("orphan_count", 0),
                    "hierarchy_levels": hierarchy_levels,
                    "implicit_hierarchy_count": len(implicit_hierarchies),
                    "single_table_import": bool(single_table_meta),
                    "system_default_hierarchy": bool(single_table_meta),
                    "proposed_structure": single_table_meta.get("proposed_structure") if single_table_meta else None,
                    "import_table_plenum_role": single_table_meta.get("import_table_plenum_role") if single_table_meta else None,
                },
            )
            from .schema_db_writer import migration_append_node_log_auto
            await migration_append_node_log_auto(
                migration_id, 7, "Hierarchy Detection", _node_started_at, datetime.utcnow(),
                output={"hierarchy_count": len(state.get("fk_candidates") or []) or len(confirmed_hierarchies),
                        "confirmed_count": len(confirmed_hierarchies),
                        "cycle_count": state.get("cycle_count", 0),
                        "orphan_count": state.get("orphan_count", 0),
                        "max_depth": max(hierarchy_levels.values()) if hierarchy_levels else 0,
                        "table_count": len(cleaned_tables)},
                logs=[f"Detected {len(state.get('fk_candidates') or [])} FK relationship candidate(s)"
                      f" ({len(confirmed_hierarchies)} auto-confirmed)",
                      f"Hierarchy levels: {hierarchy_levels}",
                      f"Cycles: {state.get('cycle_count', 0)}, Orphans: {state.get('orphan_count', 0)}",
                      f"EL-M.6: {'PASSED' if state.get('el_m6_passed') else 'FAILED'}"],
            )

        return state

    except Exception as e:
        log(f"Exception: {e}")
        state["error_message"] = str(e)
        state["error_node"] = 6
        state["error_timestamp"] = datetime.utcnow()
        state["status"] = "failed"
        state["execution_logs"] = execution_logs
        return state


def _fk_key(fk: dict) -> tuple[str, str, str, str]:
    return (
        str(fk.get("source_table", "")),
        str(fk.get("source_column", "")),
        str(fk.get("target_table", "")),
        str(fk.get("target_column", "id")),
    )


def _merge_fk_dicts(primary: list[dict], secondary: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str, str, str], dict] = {}
    for fk in primary + secondary:
        key = _fk_key(fk)
        prev = merged.get(key)
        if not prev or float(fk.get("data_match_rate") or 0) > float(prev.get("data_match_rate") or 0):
            merged[key] = fk
    return list(merged.values())


def _levels_from_relationships(fks: list[dict]) -> dict[str, int]:
    """Infer table depth from CONTAINMENT edges (target = parent)."""
    if not fks:
        return {}
    parents: dict[str, set[str]] = {}
    tables: set[str] = set()
    for fk in fks:
        st = fk.get("source_table")
        tt = fk.get("target_table")
        if not st or not tt or st == tt:
            tables.add(st or "")
            continue
        tables.add(st)
        tables.add(tt)
        if fk.get("relationship_type", "CONTAINMENT") == "CONTAINMENT":
            parents.setdefault(st, set()).add(tt)
    levels = {t: 0 for t in tables if t}
    changed = True
    while changed:
        changed = False
        for child, ps in parents.items():
            if not ps:
                continue
            parent_level = max(levels.get(p, 0) for p in ps)
            new_level = parent_level + 1
            if levels.get(child, 0) < new_level:
                levels[child] = new_level
                changed = True
    return levels


async def _claude_infer_hierarchies(
    schema_summary: dict[str, dict],
    log,
) -> tuple[list[dict], dict[str, int]]:
    from ...app import get_anthropic_client

    client = get_anthropic_client()
    schema_json = json.dumps(schema_summary, indent=2)
    prompt = f"""Analyze this CMMS data schema. Tables, column names, and sample rows:

{schema_json}

Detect parent-child relationships from COLUMN NAMES and table names:
- {{table}}_id / {{table}}_code / site_id / parent_id patterns
- parent_id on a table usually references that table's primary key (self-referential)
- site_id on assets references a sites table when present

Apply the canonical FM containment hierarchy when the data supports it:
Site -> Building -> Space (Floor / Zone / Room) -> Asset; an Asset is the parent of
its WorkOrders, PPM schedules and ComplianceCertificates; WorkOrders reference
Resources and Vendors. Prefer this ordering when assigning hierarchy_levels.

Return ONLY JSON:
{{
  "inferred_fks": [
    {{
      "source_table": "data",
      "source_column": "site_id",
      "target_table": "sites",
      "target_column": "id",
      "relationship_type": "CONTAINMENT",
      "confidence": 0.9,
      "reasoning": "column site_id references sites"
    }}
  ],
  "hierarchy_levels": {{ "sites": 0, "data": 1 }}
}}"""

    from ...matchers.fm_ontology import fm_skill_preamble

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        # FM Skill Context so hierarchy inference reasons in FM containment terms.
        system=fm_skill_preamble(),
        messages=[{"role": "user", "content": prompt}],
    )
    response_text = response.content[0].text.strip()
    if response_text.startswith("```"):
        response_text = response_text.split("```")[1]
        if response_text.startswith("json"):
            response_text = response_text[4:]
        response_text = response_text.rstrip("```").strip()
    parsed = json.loads(response_text)
    return parsed.get("inferred_fks", []), parsed.get("hierarchy_levels", {})


def _validate_inferred_fk_dicts(
    inferred_fks: list[dict],
    cleaned_tables: dict[str, list[dict]],
    log,
) -> list[dict]:
    """Validate LLM-inferred FKs (same rules as legacy hierarchy_node)."""
    confirmed: list[dict] = []
    for fk in inferred_fks:
        source_table = fk.get("source_table")
        source_column = fk.get("source_column")
        target_table = fk.get("target_table")
        target_column = fk.get("target_column", "id")
        if not source_table or not source_column or not target_table:
            continue
        if source_table not in cleaned_tables or target_table not in cleaned_tables:
            continue
        source_records = cleaned_tables[source_table]
        target_records = cleaned_tables[target_table]
        if not source_records or not target_records:
            continue
        if source_column not in source_records[0] or target_column not in target_records[0]:
            continue
        source_values = {
            str(record.get(source_column)).lower().strip()
            for record in source_records
            if record.get(source_column) is not None and str(record.get(source_column)).strip()
        }
        target_values = {
            str(record.get(target_column)).lower().strip()
            for record in target_records
            if record.get(target_column) is not None and str(record.get(target_column)).strip()
        }
        if not source_values or not target_values:
            continue
        match_rate = len(source_values & target_values) / len(source_values)
        if match_rate >= 0.70:
            fk = {**fk, "data_match_rate": match_rate, "target_column": target_column, "validated": True}
            confirmed.append(fk)
            log(
                f"Claude VALIDATED: {source_table}.{source_column} → "
                f"{target_table}.{target_column}: {match_rate:.1%}"
            )
    return confirmed


def _build_containment_hierarchy(
    hierarchies: list[dict],
    tree_structures: dict,
    hierarchy_levels: dict,
) -> dict:
    """
    Build a nested containment hierarchy from classified relationships.

    Args:
        hierarchies: List of confirmed hierarchies
        tree_structures: Self-referencing tree structures
        hierarchy_levels: Table hierarchy levels from Claude

    Returns:
        Nested dict representing containment hierarchy
    """

    # Find CONTAINMENT relationships
    containment_rels = [
        h for h in hierarchies
        if h.get("relationship_type") == "CONTAINMENT"
    ]

    # Find root table (level 0)
    root_table = next((t for t, level in hierarchy_levels.items() if level == 0), None)

    containment = {
        "relationships": containment_rels,
        "trees": tree_structures,
        "hierarchy_levels": hierarchy_levels,
        "root_table": root_table,
        "structure": " → ".join(
            t for t, _ in sorted(hierarchy_levels.items(), key=lambda x: x[1])
        ) if hierarchy_levels else "unknown",
    }

    return containment
