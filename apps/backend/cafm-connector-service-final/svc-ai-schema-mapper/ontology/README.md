# UDR Ontology & Semantic-Mapping RAG Foundation (Phase I-B)

Ontology-driven knowledge base that powers the UDR semantic-mapping engine:
table mapping, column mapping, hierarchy detection, entity resolution, FK
detection, and cross-system normalization. Source of truth for the target UDR
canonical model. See `docs/UDR_CONSOLIDATED_IMPLEMENTATION_PLAN.md` §5.

## Files

| File | Purpose |
|------|---------|
| `canonical_entities.json` | **Single source of truth** — every canonical entity with PK, hierarchy level, standard columns (datatype, classification, sample values, aliases). Aligns 1:1 with `migrations/udr_phase1_schema.sql`. |
| `synonyms.json` | CMMS/CAFM-native term → canonical table/column (Maximo, Fiix, SAP PM, Archibus, Planon, Concept, MRI, Yardi). Drives the Step-1b deterministic-RAG pass (≥95% auto-resolve). |
| `relationships.json` | Canonical FK / relationship-graph edges (`provenance=schema_defined`) + documented coincidental overlaps. |
| `hierarchy.json` | Canonical target hierarchy (primary spine + branches) for the "recommended finalised structure" appended to each UDR script. |
| `mapping_dictionaries/deterministic_aliases.json` | Tier-1 exact / Levenshtein≤2 alias maps (table + column). |
| `mapping_dictionaries/regex_patterns.json` | Cell-value FORMAT patterns + column-name regex hints. |
| `build_rag_chunks.py` | Generator — derives the RAG chunks + manifest from the JSON above. Stdlib only. |
| `rag/chunks/*.md` | Embedding-ready chunks (one per entity + one per mapping concept). Generated — do not hand-edit. |
| `rag/manifest.json` | Chunk index: `chunk_key → {concept, entity, intent_tags, file}`. |

## Regenerate chunks (after editing any JSON)
```bash
cd svc-ai-schema-mapper/ontology
python build_rag_chunks.py        # rewrites rag/chunks/*.md + rag/manifest.json
```

## Apply to the database (run against YOUR DB — not auto-applied to prod)
These steps touch the database / OpenAI; they are intentionally **not** run by
the tooling. Apply them against the target DB after review.
```bash
# 1. Schema (idempotent DDL)
psql "$DB_URL" -f ../../cafm-connector-service/migrations/udr_phase1_schema.sql

# 2. Seed the GLOBAL catalog (canonical_table / canonical_column / synonym)
python seed_udr_catalog.py                      # -> seed_udr_catalog.sql
psql "$DB_URL" -f seed_udr_catalog.sql

# 3. Embed the RAG chunks into udr_ontology_chunk (needs OPENAI_API_KEY)
python embed_chunks.py            # dry-run (default, no API/DB)
python embed_chunks.py --apply    # embeds + upserts
```
`relationships.json` is **not** seeded as global rows — it is read at runtime by
`src/ontology_loader.py` and written per-run into `udr_relationship`
(`provenance=schema_defined`).

## Runtime access (the integration seam)
`src/ontology_loader.py` is the read-only accessor the LangGraph mapper calls:
`resolve_table(term)`, `resolve_column(term, dest_table)`, `canonical_table_names()`,
`relationships()`, `hierarchy()`. It layers deterministic alias (conf 1.0) →
CMMS RAG synonym (its confidence); unresolved terms fall through to the existing
semantic mapper. Self-test: `python src/ontology_loader.py`.

## Seed sources already in the codebase (extend, don't duplicate)
- `src/matchers/canonical_field_registry.json` — existing learned field registry.
- `src/matchers/cmms_aliases.py` — existing CMMS alias dict (Maximo/Fiix/SAP/Archibus).
- `src/matchers/regex_patterns.py` — existing regex tier.
These are the embryonic version of this foundation; migrate them into the JSON here over time so there is one ontology source.

## Mapping thresholds (single source of truth — plan §10)
deterministic/RAG ≥95% auto · semantic 70–95% Suggested · <70% Review ·
column-merge ≥95% auto / 60–94% flag · FK requires ≥95% referential integrity ·
Test 1 chunk→PK >1% fail blocks · Test 2 overlap→FK ≥1% unexplained blocks.
