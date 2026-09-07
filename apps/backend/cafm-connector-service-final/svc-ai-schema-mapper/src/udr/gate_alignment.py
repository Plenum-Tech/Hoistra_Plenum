"""Step 2 (pre-semantic gate) ↔ B21.1 (destination column mapping) alignment.

The pre-semantic gate shows the deterministic *tier-1* target per column. B21.1
(``column_intelligence.dest_mapping``) is the ENTITY-AWARE authority that can DEMOTE a tier-1
match to a NEW column — e.g. ``vendors.id`` holds asset codes (canonical ``asset_id``), so it must
NOT merge into the vendor UUID PK. When the two disagree, Step 2 is aligned TO B21: the column is
retargeted to B21's canonical (created as a new column).

Pure (stdlib only) so it is unit-testable in isolation and reused by the gate node.
"""

from __future__ import annotations


def _ncol(s) -> str:
    """Loose column-name normalisation for equality checks ('asset id' == 'asset_id')."""
    return str(s or "").strip().lower().replace(" ", "_")


def b21_new_column_index(dest_mapping) -> dict[tuple[str, str], str]:
    """Index B21 'new column' outcomes by ``(source_table_lower, source_column) -> canonical``.

    ``dest_mapping[*]["source"]`` is ``_prefixed(source_table, col)`` = ``"{source_table.lower()}.{col}"``
    (the SOURCE table name lowercased — e.g. ``works.id`` even when works→assets), and
    ``canonical_name`` is the name the new column is created under (e.g. ``asset_id``).
    """
    idx: dict[tuple[str, str], str] = {}
    for row in dest_mapping or []:
        if not isinstance(row, dict) or row.get("outcome") != "new column":
            continue
        src = str(row.get("source") or "")
        if "." not in src:
            continue
        dest, col = src.split(".", 1)
        canon = str(row.get("canonical_name") or "").strip()
        if col and canon:
            idx[(dest.lower(), col)] = canon
    return idx


def align_mapping_to_b21(table: str, mapping: dict, index: dict[tuple[str, str], str]) -> bool:
    """Retarget a tier-1-mapped column to B21's new-column canonical, in place.

    DEMOTE-only: acts when B21 says the column is a NEW column but the deterministic mapper
    pointed it at a (real) destination column. Returns True if the mapping was changed.
    Idempotent (skips a mapping already flagged ``b21_new_column``).
    """
    if not isinstance(mapping, dict):
        return False
    sf = mapping.get("source_field")
    tf = (mapping.get("target_field") or "").strip()
    if not sf or not tf or mapping.get("b21_new_column"):
        return False
    canon = index.get((str(table).lower(), sf))
    if not canon or _ncol(canon) == _ncol(tf):
        return False
    mapping["target_field"] = canon
    mapping["b21_new_column"] = True
    mapping["rationale"] = (
        f"Aligned with destination-column mapping (B21.1): '{sf}' carries another entity's "
        f"values — created as new column '{canon}', not merged into '{tf}'."
    )
    return True


def b21_resolved_targets(dest_mapping) -> dict[tuple[str, str], dict]:
    """Index B21's RESOLVED matches by ``(source_table_lower, source_column) -> {...}``.

    A resolved row is one B21 landed on a real destination column (``matched_column`` set and the
    outcome is not "new column" / "review"). Used to surface B21's decision back in Step 2 for
    columns the deterministic mapper left unresolved — e.g. B21's canonical-driven resolution
    ``property_ref -> site_id`` (found via cross-table grouping) that Step 2's name matcher missed.
    Returns ``{(table, col): {"matched_column", "confidence", "outcome"}}``.
    """
    idx: dict[tuple[str, str], dict] = {}
    for row in dest_mapping or []:
        if not isinstance(row, dict):
            continue
        matched = str(row.get("matched_column") or "").strip()
        outcome = str(row.get("outcome") or "").strip().lower()
        if not matched or outcome in ("new column", "review"):
            continue
        src = str(row.get("source") or "")
        if "." not in src:
            continue
        tbl, col = src.split(".", 1)
        if col:
            idx[(tbl.lower(), col)] = {
                "matched_column": matched,
                "confidence": row.get("confidence"),
                "outcome": outcome,
            }
    return idx


def b21_suggestions_for_unresolved(
    dest_mapping,
    tier1_mappings_by_table,
    is_pk=None,
) -> dict[str, dict]:
    """Build Step-2 suggestion entries for columns B21 RESOLVED but the mapper left UNRESOLVED.

    So Step 2's dropdown shows the SAME destination B21.1 shows (one-click approvable), instead of
    the column reading as "unresolved -> semantic". Columns already in a tier-1 mapping are left
    alone (those are handled by :func:`align_buckets_to_b21`). ``is_pk(table, col) -> bool`` is an
    optional real-PK predicate so the suggestion carries the correct PK flag.

    Returns ``{table: {source_field: {target_field, confidence, candidates, is_primary_key}}}`` —
    ready to merge into ``unresolved_suggestions_by_table`` (missing keys are additive; existing
    suggestions are NOT overwritten, so a stronger name suggestion still wins).
    """
    resolved = b21_resolved_targets(dest_mapping)
    if not resolved:
        return {}
    mapped: set[tuple[str, str]] = set()
    for tbl, maps in (tier1_mappings_by_table or {}).items():
        for m in maps or []:
            if isinstance(m, dict) and m.get("source_field"):
                mapped.add((str(tbl).lower(), m["source_field"]))

    out: dict[str, dict] = {}
    seen_tbl_case: dict[str, str] = {}
    for tbl, maps in (tier1_mappings_by_table or {}).items():
        seen_tbl_case[str(tbl).lower()] = tbl
    for (tbl_lc, col), info in resolved.items():
        if (tbl_lc, col) in mapped:
            continue  # already a tier-1 mapping — not an unresolved column
        tbl = seen_tbl_case.get(tbl_lc, tbl_lc)
        conf = info.get("confidence")
        entry = {
            "source_field": col,
            "target_field": info["matched_column"],
            "confidence": conf,
            "candidates": [{"target_field": info["matched_column"], "confidence": conf}],
            "is_primary_key": bool(is_pk(tbl, col)) if callable(is_pk) else False,
            "source": "b21",
        }
        out.setdefault(tbl, {})[col] = entry
    return out


def align_buckets_to_b21(dest_mapping, *mapping_buckets) -> int:
    """Align every mapping in the given ``{table: [mapping, ...]}`` buckets to B21, in place.

    The FIRST bucket is treated as the authoritative tier-1 set (its change count is returned —
    that's what drives the write path); later buckets are display copies kept in sync. Returns the
    number of tier-1 mappings changed.
    """
    index = b21_new_column_index(dest_mapping)
    if not index:
        return 0
    changed = 0
    for pos, bucket in enumerate(mapping_buckets):
        for table, mappings in (bucket or {}).items():
            for m in (mappings or []):
                if align_mapping_to_b21(table, m, index) and pos == 0:
                    changed += 1
    return changed
