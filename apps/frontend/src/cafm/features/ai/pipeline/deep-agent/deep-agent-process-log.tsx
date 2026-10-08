// CAFM Web's process-log entry shape, as the migration screens emit it. Hoistra's Orchestrator
// has its own run trace; the vendored screens only need the type.
export type DeepAgentProcessLogInput = {
  id?: string;
  title: string;
  detail?: string;
  tone?: string;
  stage?: string;
  [key: string]: unknown;
};
