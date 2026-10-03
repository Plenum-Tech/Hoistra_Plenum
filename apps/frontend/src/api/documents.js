// The document register (svc-operations-intelligence /api/documents) and "Index now"
// (svc-deepagents /api/documents/{id}/index). A building's documents however they reach it,
// their type and sensitivity, links to other buildings, and making their text searchable.
import { BASES, currentOrgId, apiFetch } from './client.js';
import { deepAgentsApi } from './deepAgents.js';

const B = BASES.opsIntelligence;
const D = BASES.deepAgents;
const enc = encodeURIComponent;
const withOrg = (q) => { const o = currentOrgId(); return o ? Object.assign({ organization_id: o }, q || {}) : (q || {}); };

export const documentsApi = {
  types: () => apiFetch(B, '/api/documents/types', { timeoutMs: 15000 }),
  list: (buildingId) => apiFetch(B, '/api/documents', { query: withOrg({ building_id: buildingId }), timeoutMs: 30000 }),
  // body: {doc_type?, personal?}
  classify: (documentId, body) => apiFetch(B, '/api/documents/' + enc(documentId), {
    method: 'PATCH', query: withOrg({}), body: body || {}, timeoutMs: 20000 }),
  link: (documentId, buildingId, relation) => apiFetch(B, '/api/documents/' + enc(documentId) + '/links', {
    method: 'POST', query: withOrg({}), body: { entity_type: 'building', entity_id: buildingId, relation: relation || null },
    timeoutMs: 20000 }),
  unlink: (documentId, buildingId) => apiFetch(B, '/api/documents/' + enc(documentId) + '/links', {
    method: 'DELETE', query: withOrg({ entity_type: 'building', entity_id: buildingId }), timeoutMs: 20000 }),
  indexNow: (documentId) => apiFetch(D, '/api/documents/' + enc(documentId) + '/index', { method: 'POST', timeoutMs: 120000 }),
  // Filed on the building with the type chosen; an identity type is filed personal and never indexed.
  upload: (file, buildingId, docType) => deepAgentsApi.runStatefulWithFiles(
    'File this document on the building.', 'docs-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
    null, [file], undefined, buildingId, { docType: docType })
};
