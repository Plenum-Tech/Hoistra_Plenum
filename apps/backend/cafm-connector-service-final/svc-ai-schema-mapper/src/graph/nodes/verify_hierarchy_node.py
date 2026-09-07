"""Node 7: Verify hierarchy — GATE 2 HITL for customer confirmation of detected relationships.

HITL Gate 2 logic:
1. Prepare review payload with detected hierarchies + visual tree representations + orphaned records
2. Customer confirms/corrects/adds relationships
3. EL-M.7: Validate no unresolved cycles after corrections
4. Track all corrections in hierarchy_corrections
5. Resume graph execution

Dual logging: execution_logs for Streamlit display + logger for Docker monitoring
"""

import json
import logging
from datetime import datetime
from typing import Optional

from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt

from ...hierarchy import filter_confirmed_hierarchies_for_output
from ..state import HierarchyRelationship, MigrationState

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


async def verify_hierarchy_node(state: MigrationState) -> MigrationState:
    """
    Node 7: GATE 2 HITL — Customer confirms/corrects/adds detected hierarchical relationships.

    Entry conditions:
    - confirmed_hierarchies is non-empty, OR
    - hierarchy_cycles exist (must be resolved)
    - orphaned_records exist (notify customer)

    Execution:
    1. Prepare review payload with:
       - Visual hierarchy tree representation
       - All detected relationships with LLM rationale
       - Orphaned records grouped by table
       - Cycle warnings (severity: error)
       - Implicit hierarchy suggestions
    2. Call interrupt() to pause graph execution
    3. Wait for external resume with customer confirmations/corrections/additions
    4. Process customer decisions: confirm/reject/modify/add_new
    5. Track all corrections in hierarchy_corrections list
    6. Update confirmed_hierarchies with customer_confirmed flags
    7. Validate no unresolved cycles remain (EL-M.7)
    8. Emit execution logs to both Streamlit and Docker
    9. Continue to Node 8

    Args:
        state: MigrationState with confirmed_hierarchies, hierarchy_cycles, orphaned_records

    Returns:
        Updated state with customer confirmations and corrections applied
    """

    migration_id = state.get("migration_id")
    confirmed_hierarchies = state.get("confirmed_hierarchies", [])
    hierarchy_cycles = state.get("hierarchy_cycles", [])
    implicit_hierarchies = state.get("implicit_hierarchies", {})
    orphaned_records = state.get("orphaned_records", {})

    # ── Set up dual logging (Streamlit + Docker) ──────────────────
    execution_logs = []

    def log(msg: str):
        """Helper to append logs and also log to docker"""
        execution_logs.append(f"[Node 7] {msg}")
        logger.info(f"[Node 7] {msg}")

    log(f"GATE 2 HITL: Hierarchy verification for migration {migration_id}")

    # ── Already answered this run? Never re-open a decided gate ─────────
    # Same guard as the pre-semantic / field-mapping gates: the confirmations were applied and stored
    # in state on the first pass, so a second entry to this node must proceed straight through instead
    # of interrupt()-ing again and briefly re-showing the gate the user just answered. NOT set on the
    # cycle-remaining re-interrupt path below, so unresolved cycles still re-prompt. Cleared at the
    # start of deterministic_mapper_node, so a deliberate restart re-opens it.
    if state.get("hierarchy_reviewed"):
        logger.info(
            "[Node 7] Hierarchy gate already answered in this run — skipping re-interrupt; "
            "proceeding with the confirmations applied earlier."
        )
        return state

    # Check entry conditions — always review when column-detected FKs or code hierarchies exist
    has_implicit = bool(implicit_hierarchies)
    if not confirmed_hierarchies and not hierarchy_cycles and not orphaned_records and not has_implicit:
        log("No hierarchies, orphans, or implicit codes to verify; skipping Gate 2")
        state["execution_logs"] = execution_logs
        state["current_step"] = 7
        state["hierarchy_reviewed"] = True  # nothing to verify == answered; don't re-open
        return state

    try:
        # ── Prepare review payload ────────────────────────────────
        review_items = []

        log(f"Preparing hierarchy review: {len(confirmed_hierarchies)} FKs, {len(hierarchy_cycles)} cycles, {len(orphaned_records)} orphan tables")

        # ── Build visual hierarchy tree representation ──────────────
        # Group relationships by containment to show hierarchy structure
        hierarchy_tree = _build_hierarchy_tree(confirmed_hierarchies)
        tree_visual = _render_tree_visual(hierarchy_tree)
        log(f"Hierarchy tree: {tree_visual}")

        # Add cycle warnings (highest severity)
        if hierarchy_cycles:
            log(f"Found {len(hierarchy_cycles)} cycles requiring resolution")
            for i, cycle in enumerate(hierarchy_cycles, 1):
                cycle_tables = _coerce_cycle_tables(cycle)
                if not cycle_tables:
                    continue
                review_items.append(
                    {
                        "type": "cycle_alert",
                        "id": f"cycle_{i}",
                        "message": f"🔴 CYCLE {i}: {' → '.join(cycle_tables)} → (back to start)",
                        "severity": "error",
                        "cycle": cycle_tables,
                        "instruction": "Select which relationship(s) to remove to break this cycle",
                    }
                )

        # Add orphaned records (medium severity)
        if orphaned_records:
            log(f"Found orphaned records in {len(orphaned_records)} table(s)")
            for table_name, orphan_rows in orphaned_records.items():
                orphan_count = len(orphan_rows) if isinstance(orphan_rows, list) else orphan_rows
                review_items.append(
                    {
                        "type": "orphaned_records",
                        "table": table_name,
                        "count": orphan_count,
                        "message": f"⚠️  {orphan_count} orphaned record(s) in {table_name} — no parent relationship found",
                        "severity": "warning",
                        "instruction": "Review if these should have a relationship to another table",
                        "sample_rows": orphan_rows[:5] if isinstance(orphan_rows, list) else [],  # Show first 5 for review
                    }
                )

        # Add hierarchies for confirmation
        log(f"Adding {len(confirmed_hierarchies)} relationships for customer confirmation")
        single_table_mode = bool(state.get("single_table_hierarchy_mode"))
        for i, hierarchy in enumerate(confirmed_hierarchies, 1):
            is_template = bool(hierarchy.get("system_default"))
            is_mapping = bool(hierarchy.get("mapping_note"))
            item = {
                "type": "system_default" if is_template or is_mapping else "hierarchy",
                "id": f"hierarchy_{i}",
                "source_table": hierarchy.get("source_table"),
                "source_column": hierarchy.get("source_column"),
                "target_table": hierarchy.get("target_table"),
                "target_column": hierarchy.get("target_column"),
                "relationship_type": hierarchy.get("relationship_type"),
                "confidence": hierarchy.get("confidence"),
                "data_match_rate": hierarchy.get("data_match_rate"),
                "reasoning": hierarchy.get("reasoning"),
                "system_default": is_template,
                "mapping_note": is_mapping,
                "read_only": is_template or is_mapping,
                "confirmed": False,
                "action": None,  # confirm | reject | modify
                "notes": "",
            }
            review_items.append(item)

        # Add implicit hierarchies for awareness
        if implicit_hierarchies:
            log(f"Found {len(implicit_hierarchies)} implicit hierarchies (code-based)")
            for col_name, hierarchy_info in implicit_hierarchies.items():
                if not isinstance(hierarchy_info, dict):
                    continue
                levels_label = _format_implicit_hierarchy_levels(hierarchy_info.get("levels"))
                examples = hierarchy_info.get("examples")
                example_text = ""
                if isinstance(examples, (list, tuple)) and examples:
                    example_text = str(examples[0])
                elif examples is not None:
                    example_text = str(examples)
                review_items.append(
                    {
                        "type": "implicit_hierarchy",
                        "column": col_name,
                        "levels": hierarchy_info.get("levels"),
                        "separator": hierarchy_info.get("separator"),
                        "examples": hierarchy_info.get("examples"),
                        "confidence": hierarchy_info.get("confidence"),
                        "message": (
                            f"📊 Implicit hierarchy in {col_name}: {levels_label}"
                            + (f" (e.g., {example_text})" if example_text else "")
                        ),
                        "instruction": "Confirm if this represents a real hierarchy",
                    }
                )

        hierarchies_to_review = [
            {
                "source_table": h.get("source_table"),
                "source_column": h.get("source_column"),
                "target_table": h.get("target_table"),
                "target_column": h.get("target_column"),
                "relationship_type": h.get("relationship_type"),
                "confidence": h.get("confidence"),
                "data_match_rate": h.get("data_match_rate"),
                "reasoning": h.get("reasoning"),
                "system_default": bool(h.get("system_default")),
                "mapping_note": bool(h.get("mapping_note")),
                "read_only": bool(h.get("system_default") or h.get("mapping_note")),
            }
            for h in confirmed_hierarchies
        ]

        # #9/#10 — full table+column metadata + document inventory (best-effort,
        # never fatal). FKs are known at this gate, so primary/foreign keys populate.
        try:
            from ..migration_metadata import build_document_inventory, build_tables_metadata
            _tables_meta = build_tables_metadata(state)
            _doc_inventory = build_document_inventory(state, _tables_meta)
        except Exception as exc:  # pragma: no cover — additive
            log(f"[Verify Hierarchy] metadata build failed: {exc}")
            _tables_meta, _doc_inventory = [], None

        review_payload = {
            "migration_id": migration_id,
            "hierarchy_tree": state.get("plenum_default_structure") or tree_visual,
            "tables_metadata": _tables_meta,
            "document_inventory": _doc_inventory,
            "hierarchies_to_review": hierarchies_to_review,
            "single_table_import": single_table_mode,
            "system_default_hierarchy": single_table_mode,
            "import_table_name": state.get("import_table_name") if single_table_mode else None,
            "import_table_plenum_role": state.get("import_table_plenum_role"),
            "proposed_structure": state.get("plenum_default_structure"),
            "total_hierarchies": len(confirmed_hierarchies),
            "total_cycles": len(hierarchy_cycles),
            "total_orphans": sum(len(rows) if isinstance(rows, list) else rows
                                for rows in orphaned_records.values()),
            "review_items": review_items,
            "instructions": (
                "1. CYCLES (red): Select relationships to remove to break cycles\n"
                "2. ORPHANS (yellow): Review and optionally add relationships\n"
                "3. RELATIONSHIPS (blue): Confirm, modify, or reject each FK\n"
                "4. IMPLICIT (gray): Confirm if code-based hierarchies are valid\n"
                "5. NEW (optional): Add any missing relationships you detect"
                + (
                    "\n6. SINGLE TABLE: Purple rows are the Plenum reference model — "
                    "confirm column hints on your import table; accept the default structure to continue."
                    if single_table_mode
                    else ""
                )
            ),
        }

        log(
            f"Interrupting for GATE 2: {len(confirmed_hierarchies)} relationships, "
            f"{len(hierarchy_cycles)} cycles, {len(orphaned_records)} orphan tables"
        )
        state["hierarchy_review_payload"] = review_payload

        # ── Write gate payload to DB so frontend can render the review UI ──
        migration_id = state.get("migration_id")
        if migration_id:
            from .db_writer import write_gate_payload
            await write_gate_payload(migration_id, "hierarchy", review_payload)

        # ── Interrupt graph execution ──────────────────────────────
        _gate_started_at = datetime.utcnow()
        customer_decisions = interrupt(review_payload)

        # ── Clear gate payload now that we have decisions ──────────────────
        if migration_id:
            from .db_writer import clear_gate_payload
            await clear_gate_payload(migration_id)

        # ── Process customer decisions ────────────────────────────
        if (
            isinstance(customer_decisions, dict)
            and customer_decisions.get("approve_all")
            and "confirmed_hierarchies" not in customer_decisions
        ):
            # Bulk / auto-drive: {"approve_all": True} means "confirm every detected relationship".
            # Without this the sentinel fell through to _apply_hierarchy_gate_resume (no
            # "confirmed_hierarchies" key → returns nothing), so approve_all silently DROPPED all
            # detected FKs. Confirm the detected set as-is (skip virtual template/mapping rows).
            _now = datetime.utcnow()
            updated_hierarchies = [
                {**h, "customer_confirmed": True, "confirmed_at": _now}
                for h in confirmed_hierarchies
                if isinstance(h, dict) and not (h.get("system_default") or h.get("mapping_note"))
            ]
            hierarchy_corrections = []
            log(f"approve_all — auto-confirming {len(updated_hierarchies)} detected relationship(s)")
        else:
            updated_hierarchies, hierarchy_corrections = _apply_hierarchy_gate_resume(
                customer_decisions,
                confirmed_hierarchies,
                state,
                log,
            )

        if state.get("single_table_hierarchy_mode"):
            cleaned = state.get("cleaned_tables") or {}
            updated_hierarchies = filter_confirmed_hierarchies_for_output(
                updated_hierarchies,
                cleaned,
                single_table_mode=True,
            )
            if customer_decisions and isinstance(customer_decisions, dict):
                if customer_decisions.get("plenum_default_hierarchy_accepted"):
                    state["plenum_default_hierarchy_accepted"] = True
            log(
                f"Single-table mode: {len(updated_hierarchies)} relationship(s) kept after "
                "filtering virtual Plenum template edges"
            )

        # Legacy per-item decision loop (streamlit / explicit hierarchy_confirmation list)
        if not updated_hierarchies and not hierarchy_corrections:
            if isinstance(customer_decisions, dict):
                customer_decisions = [customer_decisions]
            elif not isinstance(customer_decisions, list):
                if isinstance(customer_decisions, (list, tuple)):
                    customer_decisions = list(customer_decisions)
                elif isinstance(customer_decisions, dict):
                    customer_decisions = [customer_decisions]
                else:
                    customer_decisions = []

            log(f"Resumed with {len(customer_decisions)} legacy decision item(s)")

        for _raw_decision in (
            customer_decisions
            if isinstance(customer_decisions, list)
            and updated_hierarchies == []
            and hierarchy_corrections == []
            else []
        ):
            # Defensive: ARQ/Redis/LangGraph checkpointer can double-serialize list
            # elements so dicts arrive as JSON strings. Parse them back if needed.
            if isinstance(_raw_decision, str):
                try:
                    decision = json.loads(_raw_decision)
                except (json.JSONDecodeError, ValueError):
                    log(f"WARNING: skipping unparseable decision string: {_raw_decision!r}")
                    continue
            else:
                decision = _raw_decision

            if not isinstance(decision, dict):
                log(f"WARNING: skipping non-dict decision: {type(decision).__name__!r}")
                continue

            decision_type = decision.get("type")
            decision_id = decision.get("id")

            if decision_type == "hierarchy_confirmation":
                # Customer confirmed/rejected/modified a hierarchy
                source_table = decision.get("source_table")
                target_table = decision.get("target_table")
                action = decision.get("action")  # confirm, reject, modify

                # Find original hierarchy
                original = next(
                    (h for h in confirmed_hierarchies
                     if h.get("source_table") == source_table and h.get("target_table") == target_table),
                    None,
                )

                if not original:
                    log(f"WARNING: Original hierarchy not found: {source_table} → {target_table}")
                    continue

                if action == "confirm":
                    # Mark as confirmed
                    original["customer_confirmed"] = True
                    original["confirmed_at"] = datetime.utcnow()
                    updated_hierarchies.append(original)
                    log(f"✅ CONFIRMED: {source_table}.{decision.get('source_column')} → {target_table}")

                elif action == "reject":
                    # Skip this hierarchy (don't add to updated list)
                    hierarchy_corrections.append({
                        "type": "rejected",
                        "source_table": source_table,
                        "target_table": target_table,
                        "reason": decision.get("notes", "Rejected by customer"),
                    })
                    log(f"❌ REJECTED: {source_table} → {target_table}")

                elif action == "modify":
                    # Customer provided modifications
                    modified_type = decision.get("relationship_type", original.get("relationship_type"))
                    original["relationship_type"] = modified_type
                    original["customer_confirmed"] = True
                    original["confirmed_at"] = datetime.utcnow()
                    updated_hierarchies.append(original)
                    hierarchy_corrections.append({
                        "type": "modified",
                        "source_table": source_table,
                        "target_table": target_table,
                        "old_type": original.get("relationship_type"),
                        "new_type": modified_type,
                        "notes": decision.get("notes", ""),
                    })
                    log(f"✏️  MODIFIED: {source_table} → {target_table} (type: {modified_type})")

            elif decision_type == "add_new_relationship":
                # Customer added a NEW relationship not detected by Node 6
                source_table = decision.get("source_table")
                source_column = decision.get("source_column")
                target_table = decision.get("target_table")
                target_column = decision.get("target_column", "id")
                rel_type = decision.get("relationship_type", "REFERENCE")

                new_hierarchy = HierarchyRelationship(
                    source_table=source_table,
                    source_column=source_column,
                    target_table=target_table,
                    target_column=target_column,
                    relationship_type=rel_type,
                    confidence=decision.get("confidence", 0.75),  # Customer-provided mappings get 0.75
                    data_match_rate=0.0,  # Not validated by algorithm
                    reasoning=f"Customer-added relationship: {decision.get('rationale', 'N/A')}",
                    customer_confirmed=True,
                )
                updated_hierarchies.append(new_hierarchy)
                hierarchy_corrections.append({
                    "type": "added",
                    "source_table": source_table,
                    "source_column": source_column,
                    "target_table": target_table,
                    "target_column": target_column,
                    "relationship_type": rel_type,
                    "rationale": decision.get("rationale", ""),
                })
                log(f"➕ ADDED: {source_table}.{source_column} → {target_table}.{target_column} ({rel_type})")

            elif decision_type == "cycle_resolution":
                # Customer resolved a cycle by removing relationships
                removed_rels = decision.get("removed_relationships", [])
                log(f"🔧 Resolving cycle: removing {len(removed_rels)} relationship(s)")

                # Remove specified relationships from updated_hierarchies
                for rel_to_remove in removed_rels:
                    src = rel_to_remove.get("source_table")
                    tgt = rel_to_remove.get("target_table")
                    updated_hierarchies = [
                        h for h in updated_hierarchies
                        if not (h.get("source_table") == src and h.get("target_table") == tgt)
                    ]
                    hierarchy_corrections.append({
                        "type": "cycle_broken",
                        "source_table": src,
                        "target_table": tgt,
                        "reason": "Removed to break cycle",
                    })
                    log(f"   Removed: {src} → {tgt}")

        # ── EL-M.7 Validation ────────────────────────────────────
        # After customer decisions, validate no unresolved cycles remain
        remaining_cycles = _detect_cycles_in_hierarchies(updated_hierarchies)

        if remaining_cycles:
            log(f"❌ EL-M.7 FAILED: {len(remaining_cycles)} unresolved cycle(s) remain")
            for cycle in remaining_cycles:
                log(f"   Cycle: {' → '.join(cycle)}")
            state["el_m7_passed"] = False
            state["el_m7_error"] = f"{len(remaining_cycles)} unresolved cycles"
            # Re-interrupt to ask customer to resolve remaining cycles
            log("Re-interrupting for further cycle resolution...")
            state["hierarchy_review_payload"] = review_payload  # Re-show for correction
            state["execution_logs"] = execution_logs
            return state  # Will trigger re-interrupt
        else:
            log(f"✅ EL-M.7 PASSED: No cycles in confirmed hierarchy")
            state["el_m7_passed"] = True

        # ── Update state with customer confirmations and corrections ─
        state["confirmed_hierarchies"] = updated_hierarchies
        state["hierarchy_confirmed"] = True
        state["hierarchy_corrections"] = hierarchy_corrections
        # Gate answered & EL-M.7 passed — mark so a re-entry can't re-open it (see guard at top).
        # Reached only when no cycles remain; the cycle-remaining branch above returns first.
        state["hierarchy_reviewed"] = True

        log(
            f"Hierarchy verification complete: "
            f"{len(updated_hierarchies)} confirmed, "
            f"{len(hierarchy_corrections)} corrections, "
            f"{len(hierarchy_cycles) - len(remaining_cycles)} cycles resolved"
        )

        state["current_step"] = 7
        state["execution_logs"] = execution_logs

        if "event_log" in state and isinstance(state["event_log"], list):
            state["event_log"].append(
                {
                    "timestamp": datetime.utcnow().isoformat(),
                    "event": "node_complete",
                    "node": 7,
                    "detail": f"{len(updated_hierarchies)} hierarchies confirmed, {len(hierarchy_corrections)} corrections",
                }
            )

        if migration_id:
            from .db_writer import update_node_progress
            await update_node_progress(migration_id, "7_hierarchy_review")

        if migration_id:
            from .schema_db_writer import migration_append_node_log_auto

            # Rebuild the visual tree from the CONFIRMED relationships so the history snapshot
            # shows the same parent→child structure the reviewer approved (reflecting any
            # corrections applied at the gate, and the co-routed duplicate collapse). Without this
            # the snapshot carried only the flat FK list, so "Previous run — click to review" had
            # no graph structure. Best-effort — never block the gate on a rendering failure.
            try:
                _final_tree_visual = _render_tree_visual(_build_hierarchy_tree(updated_hierarchies))
            except Exception:  # pragma: no cover — display only
                _final_tree_visual = None

            await migration_append_node_log_auto(
                migration_id, 8, "Verify Hierarchy (Gate 2)", _gate_started_at, datetime.utcnow(),
                output={"confirmed_hierarchy_count": len(updated_hierarchies),
                        "corrections_applied": len(hierarchy_corrections),
                        "hierarchy_confirmed": state.get("el_m7_passed", False),
                        # Parent→child tree of the confirmed hierarchy, for the history snapshot.
                        "hierarchy_tree": _final_tree_visual,
                        "hierarchy_duplicate_groups": state.get("hierarchy_duplicate_groups") or [],
                        # Persist the FULL confirmed FK list so the verification
                        # snapshot / migration history can display every FK
                        # (not just counts). No truncation.
                        "confirmed_hierarchies": [
                            {
                                "source_table": h.get("source_table"),
                                "source_column": h.get("source_column"),
                                "target_table": h.get("target_table"),
                                "target_column": h.get("target_column"),
                                "relationship_type": h.get("relationship_type"),
                                "confidence": h.get("confidence"),
                            }
                            for h in updated_hierarchies
                            if isinstance(h, dict)
                        ]},
                logs=[f"Gate 2: hierarchy verification",
                      f"{len(updated_hierarchies)} hierarchies confirmed",
                      f"{len(hierarchy_corrections)} corrections applied by user",
                      f"EL-M.7: {'PASSED' if state.get('el_m7_passed') else 'FAILED'}"],
            )

        return state

    except GraphInterrupt:
        raise

    except Exception as e:
        log(f"❌ ERROR: {str(e)}")
        logger.exception(f"[Node 7] Unhandled exception: {e}")
        state["error_message"] = str(e)
        state["error_node"] = 7
        state["error_timestamp"] = datetime.utcnow()
        state["status"] = "failed"
        state["execution_logs"] = execution_logs
        return state


# ── Helper Functions ──────────────────────────────────────────────────────


def _format_implicit_hierarchy_levels(levels_raw: object) -> str:
    """implicit_hierarchy stores level *count* as int; older payloads may use a list."""
    if isinstance(levels_raw, int):
        return f"{levels_raw} level(s)"
    if isinstance(levels_raw, (list, tuple)):
        return " > ".join(map(str, levels_raw)) if levels_raw else "unknown levels"
    if levels_raw is None:
        return "unknown levels"
    return str(levels_raw)


def _coerce_cycle_tables(cycle: object) -> list[str]:
    if isinstance(cycle, (list, tuple)):
        return [str(t) for t in cycle if t is not None and str(t).strip()]
    if isinstance(cycle, str) and cycle.strip():
        return [cycle.strip()]
    return []


def _apply_hierarchy_gate_resume(
    customer_decisions: object,
    confirmed_hierarchies: list,
    state: MigrationState,
    log,
) -> tuple[list, list]:
    """
    Normalize frontend gate body ``{confirmed_hierarchies, hierarchy_corrections}``
    into state updates. Returns empty lists when legacy per-item decisions are used.
    """
    if not isinstance(customer_decisions, dict):
        return [], []

    if "confirmed_hierarchies" not in customer_decisions:
        return [], []

    submitted = customer_decisions.get("confirmed_hierarchies") or []
    corrections_raw = customer_decisions.get("hierarchy_corrections")
    corrections: list = (
        list(corrections_raw.values())
        if isinstance(corrections_raw, dict)
        else (corrections_raw if isinstance(corrections_raw, list) else [])
    )

    if state.get("single_table_hierarchy_mode"):
        state["plenum_default_hierarchy_accepted"] = bool(
            customer_decisions.get("plenum_default_hierarchy_accepted", True)
        )

    updated: list = []
    now = datetime.utcnow()
    for rel in submitted:
        if not isinstance(rel, dict):
            continue
        if rel.get("system_default") or rel.get("mapping_note"):
            continue
        row = dict(rel)
        row["customer_confirmed"] = True
        row["confirmed_at"] = now
        updated.append(row)

    log(f"Frontend hierarchy gate: {len(updated)} relationship(s) confirmed")
    return updated, corrections


def _build_hierarchy_tree(hierarchies: list[dict]) -> dict:
    """
    Build a tree structure from flat hierarchy relationships.

    source_table has the FK column → it is the CHILD.
    target_table is referenced        → it is the PARENT.

    Returns: {table_name: {children: [...], rel_type: ...}}
    """
    tree: dict = {}

    for hierarchy in hierarchies:
        src_table = hierarchy.get("source_table")  # child (has FK column)
        tgt_table = hierarchy.get("target_table")  # parent (referenced table)

        if not src_table or not tgt_table:
            continue

        # Ensure both nodes exist in tree
        if tgt_table not in tree:
            tree[tgt_table] = {"children": [], "type": "parent"}
        if src_table not in tree:
            tree[src_table] = {"children": [], "type": "child"}

        # Add source (child) under target (parent)
        if src_table not in tree[tgt_table]["children"]:
            tree[tgt_table]["children"].append(src_table)

    return tree


def _render_tree_visual(tree: dict) -> str:
    """
    Render hierarchy tree as visual ASCII tree string.

    Returns string like:
    ```
    organization
    ├── site
    │   ├── location
    │   │   └── asset
    │   └── equipment
    └── user
    ```
    """
    if not tree:
        return "(no containment hierarchy)"

    # Find root nodes (tables that are not children of any other table)
    all_children = set()
    for children_list in [t.get("children", []) for t in tree.values()]:
        all_children.update(children_list)

    root_nodes = [t for t in tree.keys() if t not in all_children]

    def render_node(node: str, prefix: str = "", is_last: bool = True) -> str:
        """Recursively render a node and its children."""
        connector = "└── " if is_last else "├── "
        result = prefix + connector + node + "\n"

        children = tree.get(node, {}).get("children", [])
        if not children:
            return result

        extension = "    " if is_last else "│   "
        for i, child in enumerate(children):
            is_last_child = (i == len(children) - 1)
            result += render_node(child, prefix + extension, is_last_child)

        return result

    # Render all roots
    output = ""
    for i, root in enumerate(root_nodes):
        is_last = (i == len(root_nodes) - 1)
        output += render_node(root, "", is_last)

    return output.rstrip()


def _detect_cycles_in_hierarchies(hierarchies: list) -> list[list[str]]:
    """
    Detect cycles in hierarchy relationships using DFS.

    Returns list of cycles, where each cycle is a list of table names
    that form a circular reference chain.

    Example: [["asset", "location", "asset"]] means asset → location → asset
    """
    from typing import Set, List

    # Build adjacency list: source_table → [target_tables]
    graph: dict[str, List[str]] = {}
    for rel in hierarchies:
        src = rel.get("source_table")
        tgt = rel.get("target_table")
        if src and tgt:
            if src not in graph:
                graph[src] = []
            graph[src].append(tgt)

    if not graph:
        return []

    # DFS to detect cycles
    cycles = []
    visited: Set[str] = set()
    rec_stack: Set[str] = set()
    path: List[str] = []

    def dfs(node: str) -> None:
        visited.add(node)
        rec_stack.add(node)
        path.append(node)

        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                dfs(neighbor)
            elif neighbor in rec_stack:
                # Found a cycle
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                cycles.append(cycle)

        path.pop()
        rec_stack.remove(node)

    # Run DFS from all unvisited nodes
    for node in graph.keys():
        if node not in visited:
            dfs(node)

    return cycles
