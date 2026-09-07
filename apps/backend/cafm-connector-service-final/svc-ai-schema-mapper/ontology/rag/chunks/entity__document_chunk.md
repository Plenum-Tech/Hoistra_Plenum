# Canonical entity: Document Chunk (table `document_chunks`)

- Hierarchy level: documents
- Primary key: `id`
- Description: Layer-2 vector store. Each chunk is anchored to a structured entity via that entity's PRIMARY KEY only (Test 1).
- Table aliases / synonyms: chunk, vector chunk, embedding

## Columns
- `id` · type=uuid · class=primary_key
- `source_entity_type` · type=string · class=standard · samples=asset, manufacturer, site
- `source_entity_id` · type=string · class=foreign_key · -> (dynamic by source_entity_type) · samples=MOB-AHU-001
- `document_type` · type=string · class=standard · samples=warranty, inspection report, lease, certificate
- `page_reference` · type=string · class=standard · samples=p. 12
