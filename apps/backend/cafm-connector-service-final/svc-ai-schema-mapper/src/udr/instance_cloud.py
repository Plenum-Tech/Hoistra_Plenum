"""Feature 7 — instance-level work-cloud traversal (PURE planning).

The schema-level work cloud (``build_work_cloud``) says which entity TYPES relate. This goes
one level deeper: for a SPECIFIC entity instance (e.g. asset ``A1``), traverse the persisted
FK edges into the actual data rows — the site it sits in, the work orders that reference it.

This module is the PURE planner: it turns the schema edges + the entity's primary key into a
traversal plan and validates every identifier (so the executor can build parameterized SQL
against ``plenum_cafm`` tables without injection risk). The async executor lives in
:mod:`udr.persistence` (it needs the live data tables).
"""

from __future__ import annotations

import re

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def is_safe_identifier(name) -> bool:
    """True only for a plain SQL identifier (letters/digits/underscore, no leading digit).
    Every table/column name interpolated into raw SQL MUST pass this."""
    return isinstance(name, str) and _IDENT_RE.fullmatch(name) is not None


def build_traversal_plan(edges, entity_type: str, pk_column: str | None = None) -> dict:
    """Turn the schema edges into an instance-traversal plan for ``entity_type``:

    - ``pk_column``  — the entity's primary key (inferred from inbound edges' ``dst_column``
      when not supplied),
    - ``outbound``   — ``[{via_column, related_table, related_pk_column}]``: this entity's FK
      columns and the parent rows they point at,
    - ``inbound``    — ``[{related_table, related_fk_column}]``: child rows that reference this
      entity's primary key.

    Only edges whose identifiers all pass :func:`is_safe_identifier` are kept.
    """
    edges = edges or []
    inbound_edges = [e for e in edges if e.get("dst_entity") == entity_type]
    outbound_edges = [e for e in edges if e.get("src_entity") == entity_type]

    if pk_column is None:
        for e in inbound_edges:
            dc = e.get("dst_column")
            if dc:
                pk_column = dc
                break

    outbound = []
    for e in outbound_edges:
        rt, vc, rpk = e.get("dst_entity"), e.get("src_column"), e.get("dst_column")
        if is_safe_identifier(rt) and is_safe_identifier(vc) and is_safe_identifier(rpk):
            outbound.append({"via_column": vc, "related_table": rt, "related_pk_column": rpk})

    inbound = []
    seen = set()
    for e in inbound_edges:
        rt, fkc = e.get("src_entity"), e.get("src_column")
        if is_safe_identifier(rt) and is_safe_identifier(fkc) and (rt, fkc) not in seen:
            seen.add((rt, fkc))
            inbound.append({"related_table": rt, "related_fk_column": fkc})

    return {
        "entity": entity_type,
        "pk_column": pk_column if is_safe_identifier(pk_column) else None,
        "outbound": outbound,
        "inbound": inbound,
    }
