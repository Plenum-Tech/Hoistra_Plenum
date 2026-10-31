// Hoistra's stand-in for CAFM Web's `@/config`. The vendored migration screens read only
// these base paths; they are Hoistra's gateway routes (api/client.js BASES), the same
// services CAFM Web calls.
import { BASES } from '../../api/client.js';

export const env = {
  apiBaseUrl: '',
  schemaMapperBaseUrl: BASES.schemaMapper,
  docRagBaseUrl: BASES.docRag,
  deepAgentsBaseUrl: BASES.deepAgents,
  udrBaseUrl: BASES.udr,
  woBaseUrl: BASES.workOrder || '/backend/work-order',
  opsIntelligenceBaseUrl: BASES.opsIntelligence || '/backend/ops-intelligence',
} as const;
