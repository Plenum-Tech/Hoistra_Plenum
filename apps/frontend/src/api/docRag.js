// api/docRag — the document index (doc-rag, served by svc-ai-schema-mapper under /doc-rag).
// Routes: apps/backend/.../doc-rag-main/app/routers/{documents,row_index}.py, mounted behind
// the gateway at /backend/doc-rag/. Reads only.
//
// A document's binary (or its extracted text when the original was never stored) is served
// by svc-deepagents at GET /api/documents/{id}/download, which redirects to blob storage —
// `documentUrl` builds that link so the Documents section can open real files.
import { BASES, apiFetch } from './client.js';

const B = BASES.docRag;

export const docRagApi = {
  // Every ingested document: file_name, mime_type, document_type, status
  // (indexed | extracting | error), num_pages, num_chunks, created_at.
  documents: () => apiFetch(B, '/documents', { timeoutMs: 60000 }),
  // The plenum_cafm tables with row counts, as the index sees them.
  dbTables: () => apiFetch(B, '/row-index/db-tables', { query: { envelope: true } })
};

export const documentUrl = (documentId) =>
  BASES.deepAgents + '/api/documents/' + encodeURIComponent(documentId) + '/download';
