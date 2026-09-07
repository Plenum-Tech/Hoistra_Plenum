#!/usr/bin/env python3
"""Generate embedding-ready RAG chunks for the UDR semantic-mapping engine.

Single source of truth = the ontology JSON files in this directory. Re-run this
whenever canonical_entities.json / synonyms.json / relationships.json /
hierarchy.json change, so the chunks never drift from the schema.

Outputs:
  rag/chunks/entity__<entity>.md   (one per canonical entity)
  rag/chunks/concept__<concept>.md (table/column mapping, hierarchy, FK, entity-res, normalization)
  rag/manifest.json                (chunk_key -> {concept, entity, intent_tags, file})

Stdlib only. Usage:  python build_rag_chunks.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CHUNK_DIR = os.path.join(HERE, "rag", "chunks")


def _load(name):
    with open(os.path.join(HERE, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_chunk(key, text):
    os.makedirs(CHUNK_DIR, exist_ok=True)
    with open(os.path.join(CHUNK_DIR, key + ".md"), "w", encoding="utf-8") as fh:
        fh.write(text.strip() + "\n")


def build():
    entities = _load("canonical_entities.json")["entities"]
    syn = _load("synonyms.json")
    rels = _load("relationships.json")["relationships"]
    hier = _load("hierarchy.json")

    manifest = []

    # group synonyms by canonical table
    tbl_syn = {}
    for s in syn["table_synonyms"]:
        tbl_syn.setdefault(s["canonical_table"], []).append(s)
    col_syn = {}
    for s in syn["column_synonyms"]:
        col_syn.setdefault((s["canonical_table"], s.get("canonical_column")), []).append(s)

    # ---- per-entity chunks ----
    for ent in entities:
        e = ent["entity"]
        ct = ent["canonical_table"]
        lines = [f"# Canonical entity: {ent.get('entity').replace('_',' ').title()} (table `{ct}`)"]
        lines.append("")
        lines.append(f"- Hierarchy level: {ent.get('hierarchy_level')}")
        lines.append(f"- Primary key: `{ent.get('primary_key')}`"
                     + ("  · REFERENCE TABLE" if ent.get("is_reference_table") else ""))
        lines.append(f"- Description: {ent.get('description','')}")
        if ent.get("aliases"):
            lines.append(f"- Table aliases / synonyms: {', '.join(ent['aliases'])}")
        if tbl_syn.get(ct):
            lines.append("- CMMS/CAFM table synonyms: "
                         + "; ".join(f"{s['cmms_system']}:{s['source_term']}(@{s['confidence']})"
                                     for s in tbl_syn[ct]))
        lines.append("")
        lines.append("## Columns")
        for c in ent.get("standard_columns", []):
            bits = [f"`{c['name']}`", f"type={c.get('datatype')}", f"class={c.get('classification')}"]
            if c.get("references_table"):
                bits.append(f"-> {c['references_table']}")
            if c.get("sample_values"):
                bits.append("samples=" + ", ".join(str(v) for v in c["sample_values"][:5]))
            if c.get("aliases"):
                bits.append("aliases=" + ", ".join(c["aliases"]))
            cs = col_syn.get((ct, c["name"]), [])
            if cs:
                bits.append("cmms=" + "; ".join(f"{s['cmms_system']}:{s['source_term']}" for s in cs))
            lines.append("- " + " · ".join(bits))
        # relationships touching this table
        related = [r for r in rels if r["from_table"] == ct or r["to_table"] == ct]
        if related:
            lines.append("")
            lines.append("## Relationships")
            for r in related:
                lines.append(f"- {r['from_table']}.{r.get('from_column')} —{r['rel_type']}({r['cardinality']})→ "
                             f"{r['to_table']}.{r.get('to_column')}")
        key = f"entity__{e}"
        _write_chunk(key, "\n".join(lines))
        manifest.append({
            "chunk_key": key, "concept": "entity", "entity": e,
            "intent_tags": ["table_mapping", "column_mapping", "entity_resolution", "fk_detection"],
            "file": f"rag/chunks/{key}.md",
        })

    # ---- concept chunks ----
    concepts = {
        "table_mapping": (
            "# Concept: Table mapping (source table -> canonical UDR table)\n\n"
            "Three-step process. (1) Deterministic: exact / Levenshtein<=2 on the table name "
            "using mapping_dictionaries/deterministic_aliases.json; >=95% confidence auto-resolves. "
            "(2) RAG: match against synonyms.json table_synonyms; >=95% auto-resolves. "
            "(3) Semantic NLP: multi-dimension match on name + metadata (PK identity, column names, "
            "3 sample values/col, column count). 70-95% = Suggested (user confirms); <70% = Requires Review. "
            "Unmapped -> assign to existing or create new canonical table. The destination UDR must NOT "
            "morph into the source; unmappable concepts are appended to the canonical structure to enrich it."
        ),
        "column_mapping": (
            "# Concept: Column mapping (source column -> canonical UDR column)\n\n"
            "After table prefixing (every source column carries its destination-table metadata). "
            "Identical 3-step process as table mapping (deterministic >=95%, RAG >=95%, semantic NLP). "
            "Column metadata = {destination table, PK/FK/Shared classification, cell value format, 5 sample values}. "
            ">=70% Suggested, <70% Requires Review, no match -> auto-create column (flagged Auto-created). "
            "100% of source columns must have a destination assignment before the script finalises."
        ),
        "hierarchy_detection": (
            "# Concept: Hierarchy detection (Layer 3 relationship graph)\n\n"
            "Recommended finalised structure: " + hier["recommended_structure_md"] + ".\n"
            "Primary spine: " + " -> ".join(s["table"] for s in hier["primary_spine"]) + ".\n"
            "Branches: " + "; ".join(f"{k}: {' -> '.join(v)}" for k, v in hier["branches"].items()) + ".\n"
            "Edges are built from confirmed PK/FK pairs (see relationships.json) and stored in "
            "udr_relationship (provenance=schema_defined). Reference tables: "
            + ", ".join(hier["reference_tables"]) + "."
        ),
        "fk_detection": (
            "# Concept: Foreign-key detection & classification\n\n"
            "A column is a FOREIGN KEY only if (a) it is the PRIMARY KEY of another table AND "
            "(b) referential integrity >= 95% (>=95% of its values exist in the referenced PK). "
            "Otherwise it is a SHARED ATTRIBUTE (threshold hard-coded, not user-adjustable). "
            "Test 2: any cross-table column value overlap >= 30% must be explained by a defined FK; "
            "unexplained overlap >= 1% of column pairs blocks the UDR. Resolution: define FK, create a "
            "reference table (promote the shared value to a PK), or document as coincidental "
            "(e.g. sites.postcode vs vendors.postcode in a single-city portfolio)."
        ),
        "entity_resolution": (
            "# Concept: Entity resolution & vector-chunk anchoring (Test 1)\n\n"
            "Every unstructured chunk (<=512 tokens) must be anchored to a structured entity via that "
            "entity's PRIMARY KEY only — never a foreign key or shared attribute (0% tolerance). "
            "If only a shared attribute is available (e.g. asset_make 'Siemens 012'), auto-create a "
            "reference table (manufacturers) with that value as PK, then re-tag. Test 1 verifies every "
            "chunk's source_entity_id exists as a PK in some table; >1% fail blocks the UDR. "
            "Normalisation: 'Siemens 012' / 'siemens 012' / 'Siemens012' resolve to one manufacturer PK."
        ),
        "normalization": (
            "# Concept: Cross-system data normalization\n\n"
            "CMMS/CAFM-native terms map to canonical UDR tables/columns via synonyms.json "
            "(Maximo WONUM->work_orders.workorder_ref, ASSETNUM->assets.asset_code; Fiix asset_id->asset_code; "
            "SAP PM EQUI->assets, EQUNR->asset_code; Archibus eq->assets; Planon Order->work_orders; "
            "MRI/Yardi Unit->spaces, Lease->leases). Value normalisation unifies case/spacing/format variants "
            "and promotes recurring shared values to reference-table primary keys."
        ),
    }
    intent_map = {
        "table_mapping": ["table_mapping"],
        "column_mapping": ["column_mapping"],
        "hierarchy_detection": ["hierarchy_detection"],
        "fk_detection": ["fk_detection", "foreign_key"],
        "entity_resolution": ["entity_resolution", "chunk_anchoring"],
        "normalization": ["normalization", "cross_system"],
    }
    for concept, body in concepts.items():
        key = f"concept__{concept}"
        _write_chunk(key, body)
        manifest.append({
            "chunk_key": key, "concept": concept, "entity": None,
            "intent_tags": intent_map[concept], "file": f"rag/chunks/{key}.md",
        })

    with open(os.path.join(HERE, "rag", "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"$schema": "udr-rag-manifest/v1", "chunk_count": len(manifest),
                   "chunks": manifest}, fh, indent=2)
    return len(manifest)


if __name__ == "__main__":
    n = build()
    print(f"Generated {n} RAG chunks -> {os.path.relpath(CHUNK_DIR, HERE)} + manifest.json")
