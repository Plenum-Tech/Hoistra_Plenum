"""Feature 7 — Stage 7 reference-table auto-creation (pure).

Implements the Layer-2 remediation note + User Flow 7.8 AC4 + 7.9 AC6
("Fix and Retest"). A SHARED ATTRIBUTE (e.g. ``asset_make`` = "Siemens" /
"Trane" appearing across sheets but NOT a primary key) cannot be a valid chunk
anchor — Test 1 (7.9) rejects it because its value is not a PK anywhere. The fix
is to PROMOTE the attribute to a reference table whose PRIMARY KEY *is* the
attribute value, turning every occurrence of that value into a real foreign key
that resolves to a PK.

Pure functions over ``list[dict]`` records — no DB, no LLM. Reuses the
value-centric primitives so it stays consistent with PK detection / overlap.

Tables shape: {table_name: {"rows": list[dict], "columns": [..], "pk": [pk_cols]}}.
"""

from __future__ import annotations

from .primitives import _nonnull, column_values, distinct_values


# ── functional-dependency helper (descriptor carry-over) ────────────────────────
def _functional_descriptors(rows, attribute_name: str, candidate_cols: list[str]) -> dict[str, dict]:
    """Return {attribute_value: {descriptor_col: value}} for descriptor columns that
    are FUNCTIONALLY DEPENDENT on the attribute.

    A descriptor column qualifies only if, across every row sharing an attribute
    value, it carries a single consistent non-null value (same attribute value ->
    same descriptor). Columns that disagree for any attribute value are dropped —
    they are not real attributes of the reference entity.
    """
    # Collect the set of non-null descriptor values seen per (col, attribute_value).
    seen: dict[str, dict[str, set]] = {c: {} for c in candidate_cols}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        key = r.get(attribute_name)
        key = "" if key is None else str(key).strip()
        if key == "":
            continue
        for c in candidate_cols:
            v = r.get(c)
            v = "" if v is None else str(v).strip()
            if v == "":
                continue
            seen[c].setdefault(key, set()).add(v)

    # A column is functionally dependent only if NO attribute value maps to >1 value.
    good_cols = [
        c for c in candidate_cols
        if all(len(vals) <= 1 for vals in seen[c].values()) and seen[c]
    ]

    descriptors: dict[str, dict] = {}
    for c in good_cols:
        for key, vals in seen[c].items():
            descriptors.setdefault(key, {})[c] = next(iter(vals))
    return descriptors


# ── 7.8 AC4 / Layer-2 note: build one reference table for an attribute ──────────
def build_reference_table(
    tables: dict[str, dict],
    attribute_name: str,
    source_table=None,
    value_columns: list | None = None,
) -> dict:
    """Build a reference table whose PRIMARY KEY is the attribute's distinct values.

    - Reference table name = ``attribute_name`` (or ``f"{attribute_name}_ref"`` if
      that name collides with an existing table — 7.8 AC4 no-clobber rule).
    - ``pk_column`` = ``attribute_name``; one row per DISTINCT non-null value (the
      value itself becomes the PK, so it is unique + non-null).
    - ``value_columns``: explicit ``[(table, column)]`` to source the PK values from
      (used when the same shared attribute lives under DIFFERENT column names across
      tables — e.g. ``make`` and ``vendor``). When omitted, every column literally
      named ``attribute_name`` is scanned (backward-compatible default).
    - If ``source_table`` is given, any co-located descriptor column functionally
      dependent on the attribute (same value -> same descriptor) is carried onto the
      ref row as an extra column (Layer-2 enrichment note).

    Returns
        {"table", "pk": [attribute_name], "columns", "rows", "source_attribute"}.
    """
    # ── distinct PK values: from the explicit value columns, else any column named
    #    attribute_name (so differently-named shared columns are all promoted) ──
    pk_values: set[str] = set()
    if value_columns:
        for (t, c) in value_columns:
            meta = tables.get(t)
            if meta and c in (meta.get("columns") or []):
                pk_values |= distinct_values(meta.get("rows", []), c)
    else:
        for meta in tables.values():
            if attribute_name in (meta.get("columns") or []):
                pk_values |= distinct_values(meta.get("rows", []), attribute_name)

    # ── name, with collision avoidance against existing tables ──
    name = attribute_name
    if name in tables:
        name = f"{attribute_name}_ref"

    # ── descriptor columns functionally dependent on the attribute ──
    descriptors: dict[str, dict] = {}
    descriptor_cols: list[str] = []
    if source_table is not None and source_table in tables:
        src = tables[source_table]
        src_rows = src.get("rows", [])
        candidate_cols = [
            c for c in (src.get("columns") or [])
            if c != attribute_name and _nonnull(column_values(src_rows, c))
        ]
        descriptors = _functional_descriptors(src_rows, attribute_name, candidate_cols)
        # Preserve source column order for any descriptor that survived.
        present = {col for row in descriptors.values() for col in row}
        descriptor_cols = [c for c in candidate_cols if c in present]

    # ── assemble one ref row per distinct value (sorted for determinism) ──
    columns = [attribute_name] + descriptor_cols
    rows: list[dict] = []
    for value in sorted(pk_values):
        row = {attribute_name: value}
        for c in descriptor_cols:
            row[c] = descriptors.get(value, {}).get(c)
        rows.append(row)

    return {
        "table": name,
        "pk": [attribute_name],
        "columns": columns,
        "rows": rows,
        "source_attribute": attribute_name,
    }


# ── 7.8 AC4: promote a shared attribute to a ref table + FK rewrites ─────────────
def promote_shared_attribute(tables: dict[str, dict], attribute_columns: list) -> dict:
    """Promote a shared attribute (held in several columns) to a reference table.

    ``attribute_columns`` is a list of ``(table, column)`` pairs that all carry the
    SAME shared attribute value (e.g. ``[("assets", "make"), ("orders", "make")]``).
    The reference table is built from the first occurrence with the RICHEST set of
    co-located functionally-dependent descriptors (so the PK carries the best
    enrichment). Every ``(table, column)`` then becomes a foreign key pointing at
    the ref PK.

    Returns
        {"reference_table": <build_reference_table result>, "fk_rewrites": [...]}.
    """
    pairs = [(t, c) for (t, c) in attribute_columns]
    if not pairs:
        return {"reference_table": None, "fk_rewrites": []}

    # The attribute name is the column name shared by the occurrences.
    attribute_name = pairs[0][1]

    # ── pick the source table whose occurrence carries the most descriptors ──
    best_source = None
    best_score = -1
    for (t, c) in pairs:
        if c != attribute_name or t not in tables:
            continue
        meta = tables[t]
        cand = [
            col for col in (meta.get("columns") or [])
            if col != attribute_name and _nonnull(column_values(meta.get("rows", []), col))
        ]
        # Score by how many distinct descriptor columns survive the FD check.
        descriptors = _functional_descriptors(meta.get("rows", []), attribute_name, cand)
        present = {col for row in descriptors.values() for col in row}
        score = len(present)
        if score > best_score:
            best_score, best_source = score, t

    # Union PK values across ALL pairs (covers occurrences under different column
    # names, e.g. assets.make + orders.vendor sharing one reference table).
    ref = build_reference_table(
        tables, attribute_name, source_table=best_source, value_columns=pairs
    )

    # ── FK rewrites: each occurrence now references the ref PK ──
    fk_rewrites = [
        {
            "table": t,
            "column": c,
            "references_table": ref["table"],
            "references_column": ref["pk"][0],
        }
        for (t, c) in pairs
    ]
    return {"reference_table": ref, "fk_rewrites": fk_rewrites}


# ── 7.9 AC6: remediate Test-1 failures ("Fix and Retest") ───────────────────────
def remediate_test1_failures(tables: dict[str, dict], test1_report: dict) -> dict:
    """Turn Test-1 (7.9) chunk-PK failures into reference tables + FK rewrites.

    ``test1_report`` is the dict from :func:`validation.run_test1_chunk_pk`; each
    entry in ``report["failures"]`` carries ``association_value`` and ``found_in``
    (the ``[{table, column}]`` shared-attribute locations the value was wrongly
    anchored on). The remediation:

    1. Groups every failing ``association_value`` by the ``(table, column)`` shared
       attribute it was found in.
    2. For each such attribute builds a reference table (whose PK is the attribute
       value) plus FK rewrites — so the value now resolves to a real PK.
    3. Returns a ``retag`` map of ``{old_value: old_value}`` for the values that were
       actually located in a shared-attribute column (the value is unchanged but now
       resolves to the ref PK). Values with NO ``found_in`` location are genuinely
       dangling — they cannot be promoted, so they are reported under ``unresolved``
       and are NOT retagged (re-running Test 1 would still fail them).

    Returns
        {"reference_tables": [...], "fk_rewrites": [...], "retag": {value: value},
         "unresolved": [value, ...]}.
    """
    # ── group failing values by the (table, column) they were found in ──
    by_attribute: dict[tuple[str, str], list[str]] = {}
    failing_values: set[str] = set()
    located_values: set[str] = set()
    for f in test1_report.get("failures", []) or []:
        value = f.get("association_value")
        if value is None or str(value).strip() == "":
            continue
        value = str(value).strip()
        failing_values.add(value)
        for loc in f.get("found_in", []) or []:
            t, c = loc.get("table"), loc.get("column")
            if t is None or c is None:
                continue
            located_values.add(value)
            by_attribute.setdefault((t, c), [])
            if value not in by_attribute[(t, c)]:
                by_attribute[(t, c)].append(value)

    # ── one reference table + FK rewrite per distinct attribute name ──
    # Group the located (table, column) pairs by their column name (the shared
    # attribute) so columns spanning multiple tables share a single ref table.
    by_name: dict[str, list[tuple[str, str]]] = {}
    for (t, c) in by_attribute:
        by_name.setdefault(c, [])
        if (t, c) not in by_name[c]:
            by_name[c].append((t, c))

    reference_tables: list[dict] = []
    fk_rewrites: list[dict] = []
    for _attribute_name, pairs in by_name.items():
        promotion = promote_shared_attribute(tables, pairs)
        if promotion["reference_table"] is not None:
            reference_tables.append(promotion["reference_table"])
            fk_rewrites.extend(promotion["fk_rewrites"])

    # ── retag ONLY values that were located in a shared attribute (and thus
    #    promoted to a PK). Dangling values (no found_in) cannot be resolved. ──
    retag = {v: v for v in sorted(located_values)}
    unresolved = sorted(failing_values - located_values)

    return {
        "reference_tables": reference_tables,
        "fk_rewrites": fk_rewrites,
        "retag": retag,
        "unresolved": unresolved,
    }
