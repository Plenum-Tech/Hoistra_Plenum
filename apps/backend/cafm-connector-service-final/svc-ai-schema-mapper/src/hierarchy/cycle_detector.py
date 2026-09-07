"""Cycle detection in FK relationship graph using DFS.

Detects circular relationships (e.g., A→B→C→A) that would break containment hierarchy.
"""

import logging
from typing import Set, List, Tuple

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


def detect_cycles(validated_fks: list[dict]) -> List[List[str]]:
    """
    Detect cycles in the FK relationship graph using depth-first search.

    Args:
        validated_fks: List of validated FK relationships
                       Each: {source_table, target_table, ...}

    Returns:
        List of cycles, each cycle is a list of table names
        Example: [['assets', 'locations', 'assets'], ['wo', 'assets', 'wo']]

    Note: Self-referencing FKs (A→A) are NOT considered cycles as they represent
          tree structures (parent-child relationships within same table).
    """

    # Build adjacency list: source_table → [target_tables]
    # Skip self-referencing FKs (A→A) as they are tree structures, not cycles
    graph = {}
    for fk in validated_fks:
        source = fk["source_table"]
        target = fk["target_table"]

        # Skip self-references (tree structures, not circular dependencies)
        if source == target:
            logger.debug(f"[Cycle Detector] Skipping self-reference: {source} → {target}")
            continue

        if source not in graph:
            graph[source] = []
        graph[source].append(target)

    logger.info(f"[Cycle Detector] Graph has {len(graph)} tables with FKs")

    cycles = []
    visited = set()
    rec_stack = set()

    def dfs(node: str, path: List[str]) -> None:
        """
        DFS to detect cycles.

        Args:
            node: Current table node
            path: Current path from start node
        """

        visited.add(node)
        rec_stack.add(node)
        path.append(node)

        if node in graph:
            for neighbor in graph[node]:
                if neighbor not in visited:
                    dfs(neighbor, path[:])
                elif neighbor in rec_stack:
                    # Found a cycle
                    cycle_start_idx = path.index(neighbor)
                    cycle = path[cycle_start_idx:] + [neighbor]
                    cycles.append(cycle)
                    logger.warning(f"[Cycle Detector] Found cycle: {' → '.join(cycle)}")

        rec_stack.discard(node)

    # Run DFS from each unvisited node
    for node in graph:
        if node not in visited:
            dfs(node, [])

    logger.info(f"[Cycle Detector] Found {len(cycles)} cycles")
    return cycles


def has_cycles(validated_fks: list[dict]) -> bool:
    """Check if FK graph contains any cycles."""
    return len(detect_cycles(validated_fks)) > 0


def is_acyclic_subset(validated_fks: list[dict], excluded_tables: Set[str]) -> bool:
    """
    Check if FK graph is acyclic after excluding certain tables.

    Useful for removing tables that participate in cycles.

    Args:
        validated_fks: All validated FKs
        excluded_tables: Tables to remove from graph

    Returns:
        True if remaining graph is acyclic
    """

    filtered_fks = [
        fk for fk in validated_fks
        if fk["source_table"] not in excluded_tables and fk["target_table"] not in excluded_tables
    ]

    return len(detect_cycles(filtered_fks)) == 0
