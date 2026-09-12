// api/docRag — the document index (doc-rag, served by svc-ai-schema-mapper under /doc-rag).
// Routes: apps/backend/.../doc-rag-main/app/routers/{documents,row_index}.py, mounted behind
// the gateway at /backend/doc-rag/.
//
// A document's binary (or its extracted text when the original was never stored) is served
// by svc-deepagents at GET /api/documents/{id}/download, which redirects to blob storage —
// `documentUrl` builds that link so the Documents section can open real files.
//
// A docRagApi wrapper for GET /documents and /row-index/db-tables lived here too, calling
// neither route: the Documents section reads document counts off the building graph
// (graphLive.js / buildingsGraph.js, via svc-operations-intelligence) instead. Removed
// rather than left dormant — a dead wrapper one edit away from being wired to routes
// nothing here actually reads from.
import { BASES } from './client.js';

export const documentUrl = (documentId) =>
  BASES.deepAgents + '/api/documents/' + encodeURIComponent(documentId) + '/download';
