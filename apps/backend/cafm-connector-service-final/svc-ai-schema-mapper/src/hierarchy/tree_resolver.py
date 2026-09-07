"""Resolve self-referencing FK trees (parent-child relationships).

Example: assets.parent_asset_id references assets.asset_code
Builds nested tree structure showing parent-child containment.
"""

import logging
from typing import Any, Dict, List, Optional

from cafm_shared.logging import get_logger
logger = get_logger(__name__)

# Guard against deep chains / malformed parent pointers blowing the stack.
MAX_TREE_DEPTH = 128


def resolve_self_referencing_trees(
    cleaned_tables: dict[str, list[dict]],
    self_ref_fks: list[dict],
) -> dict[str, dict]:
    """
    Build nested tree structures for self-referencing FKs.

    Args:
        cleaned_tables: dict[table_name] = [row_dicts]
        self_ref_fks: FKs where source_table == target_table
                      Each: {source_table, source_column, target_column}

    Returns:
        dict[table_name] = nested tree dict
    """

    trees: dict[str, dict] = {}

    for fk in self_ref_fks:
        table_name = fk["source_table"]

        if table_name not in cleaned_tables:
            logger.warning(f"[Tree Resolver] Table {table_name} not found")
            continue

        records = cleaned_tables[table_name]
        if not records:
            logger.warning(f"[Tree Resolver] Table {table_name} is empty")
            continue

        pk_col = fk["target_column"]
        parent_col = fk["source_column"]

        record_map: dict[str, dict] = {}
        for record in records:
            pk = record.get(pk_col)
            if pk is not None:
                record_map[str(pk).lower().strip()] = record

        children_by_parent = _index_children(record_map, parent_col)

        roots = []
        for record in records:
            parent_id = record.get(parent_col)
            if parent_id is None or str(parent_id).strip() == "":
                roots.append(record)

        logger.info(
            f"[Tree Resolver] {table_name}: {len(roots)} roots, "
            f"{len(record_map)} total records"
        )

        tree_structure: dict[str, dict] = {}
        for root in roots:
            root_id = root.get(pk_col)
            if root_id:
                visited: set[str] = set()
                tree_structure[str(root_id)] = _build_subtree(
                    root,
                    pk_col,
                    children_by_parent,
                    visited,
                    depth=0,
                )

        trees[table_name] = tree_structure

    return trees


def _index_children(record_map: dict[str, dict], parent_col: str) -> dict[str, list[dict]]:
    """Map parent_pk → list of child records (O(n) once per table)."""
    children_by_parent: dict[str, list[dict]] = {}
    for record in record_map.values():
        parent_id = str(record.get(parent_col, "")).lower().strip()
        if parent_id:
            children_by_parent.setdefault(parent_id, []).append(record)
    return children_by_parent


def _build_subtree(
    record: dict,
    pk_col: str,
    children_by_parent: dict[str, list[dict]],
    visited: set[str],
    depth: int,
) -> dict:
    """Iteratively safe subtree build with cycle and depth guards."""

    pk = str(record.get(pk_col, "")).lower().strip()

    if pk in visited or depth >= MAX_TREE_DEPTH:
        if depth >= MAX_TREE_DEPTH:
            logger.warning(f"[Tree Resolver] Max depth {MAX_TREE_DEPTH} at {pk}")
        else:
            logger.warning(f"[Tree Resolver] Circular reference detected at {pk}")
        return {"id": pk, "data": record, "children": []}

    visited.add(pk)

    children: list[dict] = []
    for child_record in children_by_parent.get(pk, ()):
        child_pk = str(child_record.get(pk_col, "")).lower().strip()
        if child_pk in visited:
            continue
        children.append(
            _build_subtree(
                child_record,
                pk_col,
                children_by_parent,
                visited,
                depth + 1,
            )
        )

    visited.discard(pk)

    return {
        "id": pk,
        "data": record,
        "children": children,
    }


def flatten_tree(tree: dict) -> List[dict]:
    """
    Flatten a nested tree into a list of node dicts.

    Each node includes: id, parent_id, depth, is_leaf.
    """

    nodes: list[dict] = []

    def traverse(node: dict, parent_id: Optional[str] = None, depth: int = 0) -> None:
        node_id = node.get("id")
        children = node.get("children", [])

        nodes.append(
            {
                "id": node_id,
                "parent_id": parent_id,
                "depth": depth,
                "is_leaf": len(children) == 0,
                "child_count": len(children),
            }
        )

        for child in children:
            traverse(child, parent_id=node_id, depth=depth + 1)

    traverse(tree)
    return nodes
