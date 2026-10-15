import type {
  SchemaFieldMappingGatePayload,
  SchemaFlaggedMappingItem,
  SchemaMappingStatusResponse,
  SchemaUnmappedFieldGateItem,
} from "../../chat-api";
import { DEFAULT_PLENUM_CANONICAL_TABLES } from "../migration/migration-gate-state";
import {
  normalizeSchemaGateType,
  normalizeStepKeyForHistory,
  resolveNodeOutputPayload,
  unwrapGatePayload,
} from "./schema-snapshot-utils";

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function asStringSuggestions(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.map((s) => (typeof s === "string" ? s : String(s)));
}

function isNodeCompleteStatus(status: string): boolean {
  const s = status.toLowerCase();
  return s.includes("complete") || s === "done";
}

export function isDeterministicStepKey(stepKey: string | null | undefined): boolean {
  if (!stepKey) return false;
  return normalizeStepKeyForHistory(stepKey) === "step_2_deterministic";
}

export function isNode2DeterministicComplete(session: SchemaMappingStatusResponse): boolean {
  const node = (session.nodes ?? []).find((n) => n.node_id === 2);
  if (!node) return false;
  if (isNodeCompleteStatus(String(node.status ?? ""))) return true;
  const output = resolveNodeOutputPayload(node.output);
  if (!output) return false;
  return (
    typeof output.tier1_mapped === "number" ||
    typeof output.t1_mapped === "number" ||
    typeof output.total_columns === "number" ||
    isRecord(output.mappings_by_table)
  );
}

export function isSemanticMappingStepKey(pending: string | null | undefined): boolean {
  if (!pending) return false;
  const s = pending.toLowerCase();
  return (
    s === "step_3_semantic" ||
    s === "step_3_semantic_mapping" ||
    s === "step_4_semantic" ||
    s === "step_4_semantic_mapping"
  );
}

export function isSemanticMappingPausePayload(v: unknown): boolean {
  if (!isRecord(v)) return false;
  const label = typeof v.label === "string" ? v.label.toLowerCase() : "";
  return (
    label.includes("semantic") ||
    typeof v.tier2_auto_mapped === "number" ||
    typeof v.tier2_flagged === "number" ||
    Array.isArray(v.semantic_results) ||
    isRecord(v.tier2_flagged_by_table) ||
    isRecord(v.tier2_unmappable_by_table)
  );
}

export function findSemanticMappingNode(session: SchemaMappingStatusResponse) {
  const nodes = session.nodes ?? [];
  const byId4 = nodes.find((n) => n.node_id === 4);
  if (byId4?.output && isSemanticNodeOutput(byId4.output)) return byId4;
  const byId3 = nodes.find((n) => n.node_id === 3);
  if (byId3?.output && isSemanticNodeOutput(byId3.output)) return byId3;
  return nodes.find((n) => {
    const name = String(n.node_name ?? "").toLowerCase();
    return name.includes("semantic") && !name.includes("pre");
  });
}

function isSemanticNodeOutput(output: unknown): boolean {
  if (!isRecord(output)) return false;
  return (
    typeof output.tier2_auto_mapped === "number" ||
    typeof output.tier2_flagged === "number" ||
    Array.isArray(output.semantic_results) ||
    isRecord(output.tier2_flagged_by_table) ||
    isRecord(output.tier2_unmappable_by_table)
  );
}

export function isNodeSemanticMappingComplete(session: SchemaMappingStatusResponse): boolean {
  const node = findSemanticMappingNode(session);
  if (!node) return false;
  const st = String(node.status ?? "").toLowerCase();
  if (st === "completed" || st === "complete" || st === "done") return true;
  return isSemanticNodeOutput(node.output);
}

/** Build editable field-mapping gate payload from semantic node output (Feature 4). */
export function buildSchemaFieldMappingPayloadFromSession(
  session: SchemaMappingStatusResponse | null | undefined,
): SchemaFieldMappingGatePayload | null {
  if (!session) return null;

  const pending = unwrapGatePayload(session.pending_gate_payload);
  if (pending && isFieldMappingGatePayload(pending)) {
    return pending as SchemaFieldMappingGatePayload;
  }

  const low_confidence_tier2: Record<string, SchemaFlaggedMappingItem[]> = {};
  const unmapped_fields: Record<string, SchemaUnmappedFieldGateItem[]> = {};

  const ingestSemantic = (results: unknown[]) => {
    for (const raw of results) {
      if (!isRecord(raw)) continue;
      const table = String(raw.table ?? raw.source_table ?? "default").trim();
      const sourceField = String(raw.source_field ?? "").trim();
      if (!sourceField) continue;
      const status = String(raw.status ?? "flagged").toLowerCase();
      if (status === "unmappable") {
        const list = unmapped_fields[table] ?? [];
        list.push({
          source_field: sourceField,
          data_type_hint: typeof raw.data_type_hint === "string" ? raw.data_type_hint : undefined,
          nullable: typeof raw.nullable === "boolean" ? raw.nullable : undefined,
        });
        unmapped_fields[table] = list;
        continue;
      }
      const list = low_confidence_tier2[table] ?? [];
      list.push({
        source_field: sourceField,
        suggested_target:
          typeof raw.target_field === "string"
            ? raw.target_field
            : typeof raw.best_target === "string"
              ? raw.best_target
              : undefined,
        confidence: typeof raw.confidence === "number" ? raw.confidence : undefined,
        tier: typeof raw.tier === "string" ? raw.tier : "T2_semantic",
        suggestions: asStringSuggestions(raw.suggestions),
      });
      low_confidence_tier2[table] = list;
    }
  };

  const semanticNode = findSemanticMappingNode(session);
  if (semanticNode?.output && isRecord(semanticNode.output)) {
    const output = semanticNode.output;
    if (Array.isArray(output.semantic_results)) ingestSemantic(output.semantic_results);
    const flagged = output.tier2_flagged_by_table as Record<string, unknown> | undefined;
    if (flagged && isRecord(flagged)) {
      for (const [tbl, items] of Object.entries(flagged)) {
        if (!Array.isArray(items)) continue;
        for (const item of items) {
          if (!isRecord(item)) continue;
          const list = low_confidence_tier2[tbl] ?? [];
          list.push({
            source_field: String(item.source_field ?? ""),
            suggested_target:
              typeof item.target_field === "string" ? item.target_field : undefined,
            confidence: typeof item.confidence === "number" ? item.confidence : undefined,
            tier: "T2_semantic",
            suggestions: asStringSuggestions(item.suggestions),
          });
          low_confidence_tier2[tbl] = list;
        }
      }
    }
    const unmappable = output.tier2_unmappable_by_table as Record<string, unknown> | undefined;
    if (unmappable && isRecord(unmappable)) {
      for (const [tbl, items] of Object.entries(unmappable)) {
        if (!Array.isArray(items)) continue;
        for (const item of items) {
          const field =
            typeof item === "string"
              ? item
              : isRecord(item)
                ? String(item.source_field ?? "")
                : "";
          if (!field) continue;
          const list = unmapped_fields[tbl] ?? [];
          list.push({ source_field: field });
          unmapped_fields[tbl] = list;
        }
      }
    }
  }

  if (
    !Object.keys(low_confidence_tier2).length &&
    !Object.keys(unmapped_fields).length
  ) {
    return null;
  }

  return {
    schema_mapping_id: session.schema_mapping_id,
    low_confidence_tier2,
    unmapped_fields,
    existing_canonical_tables: [...DEFAULT_PLENUM_CANONICAL_TABLES],
  };
}

export function countSchemaFieldMappingReviewItems(payload: SchemaFieldMappingGatePayload): number {
  let n = 0;
  for (const items of Object.values(payload.low_confidence_tier2 ?? {})) n += items?.length ?? 0;
  for (const items of Object.values(payload.unmapped_fields ?? {})) n += items?.length ?? 0;
  return n;
}

export function getSemanticPausePayload(session: SchemaMappingStatusResponse): Record<string, unknown> {
  const pending = session.pending_gate_payload;
  if (
    session.status === "step_paused" &&
    isSemanticMappingStepKey(
      typeof session.pending_gate_type === "string" ? session.pending_gate_type : null,
    ) &&
    isRecord(pending)
  ) {
    return pending;
  }
  const semanticNode = findSemanticMappingNode(session);
  if (semanticNode?.output && isRecord(semanticNode.output)) {
    return semanticNode.output;
  }
  return { label: "Semantic Mapping", tier2_auto_mapped: 0, tier2_flagged: 0, unmappable: 0 };
}

/** Block Field Mapping Review until the user has continued past Semantic Mapping (node 4). */
/** Active step_* pause — Field Mapping must not render until user clicks Continue on StepPause. */
export function isSchemaStepPauseBlockingFieldMapping(
  session: SchemaMappingStatusResponse | null | undefined,
  semanticMappingDismissed = true,
): boolean {
  if (!session) return false;
  const st = String(session.status ?? "").toLowerCase();
  if (st !== "step_paused") return false;
  const pending = typeof session.pending_gate_type === "string" ? session.pending_gate_type : null;
  if (!pending?.startsWith("step_")) return false;
  if (isSemanticMappingStepKey(pending) && !semanticMappingDismissed) return false;
  return true;
}

const ORCHESTRATOR_AUTO_ADVANCE_STEPS = new Set([
  "step_1_ingest",
  "step_2_deterministic",
  "step_2_deterministic_mapping",
  "step_5_hierarchy",
  "step_6_hierarchy",
  "step_6_hierarchy_detection",
  "step_8_output",
  "step_8_output_generation",
  "step_8_write",
  "step_9_finalize",
]);

export function shouldOrchestratorAutoAdvanceSchemaStep(
  stepKey: string,
  semanticMappingDismissed: boolean,
): boolean {
  if (ORCHESTRATOR_AUTO_ADVANCE_STEPS.has(stepKey)) return true;
  const normalized = normalizeStepKeyForHistory(stepKey);
  if (ORCHESTRATOR_AUTO_ADVANCE_STEPS.has(normalized)) return true;
  if (
    semanticMappingDismissed &&
    (stepKey.includes("semantic") || normalized === "step_4_semantic" || normalized === "step_3_semantic")
  ) {
    return true;
  }
  return false;
}

export function requiresSemanticMappingLatch(
  session: SchemaMappingStatusResponse | null | undefined,
  semanticMappingDismissed: boolean,
): boolean {
  if (!session || semanticMappingDismissed) return false;

  const st = String(session.status ?? "").toLowerCase();
  if (st === "complete" || st === "failed" || st === "ddl_failed" || st === "error") return false;

  const pending = typeof session.pending_gate_type === "string" ? session.pending_gate_type : null;
  const normalized = normalizeSchemaGateType(pending);

  if (normalized === "pre_semantic") return false;
  if (normalized === "hierarchy" || normalized === "artifacts_review") return false;

  // Backend may set field_mapping pending before semantic is finished — hold Semantic Mapping first.
  if (normalized === "field_mapping") {
    if (!isNodeSemanticMappingComplete(session)) return true;
    return false;
  }

  if (st === "step_paused" && isSemanticMappingStepKey(pending)) return true;

  if (!isNodeSemanticMappingComplete(session)) {
    const cn = session.current_node ?? 0;
    if (cn >= 4) return true;
  }

  return false;
}

export function requiresFieldMappingLatch(
  session: SchemaMappingStatusResponse | null | undefined,
  fieldMappingGateDismissed: boolean,
  semanticMappingDismissed: boolean,
): boolean {
  if (!session || fieldMappingGateDismissed) return false;
  if (requiresSemanticMappingLatch(session, semanticMappingDismissed)) return false;

  const semanticDone = semanticMappingDismissed || isNodeSemanticMappingComplete(session);
  if (!semanticDone) return false;

  const st = String(session.status ?? "").toLowerCase();
  if (st === "complete" || st === "failed" || st === "ddl_failed" || st === "error") return false;

  const pending = typeof session.pending_gate_type === "string" ? session.pending_gate_type : null;
  const normalized = normalizeSchemaGateType(pending);

  if (normalized === "field_mapping") return true;

  const pendingPayload = unwrapGatePayload(session.pending_gate_payload);
  if (pendingPayload && isFieldMappingGatePayload(pendingPayload)) return true;

  const node5 = (session.nodes ?? []).find((n) => n.node_id === 5);
  const node5Payload = unwrapGatePayload(node5?.output);
  if (node5Payload && isFieldMappingGatePayload(node5Payload) && (session.current_node ?? 0) >= 5) {
    return true;
  }

  return false;
}

export function isFieldMappingGatePayload(v: unknown): boolean {
  if (!isRecord(v)) return false;
  return (
    isRecord(v.flagged_by_table) ||
    isRecord(v.unmapped_by_table) ||
    isRecord(v.unmappable_items_by_table) ||
    isRecord(v.review_items_by_table)
  );
}

/** Gates at this node id must not appear in history until pipeline has reached that step. */
export const SCHEMA_GATE_NODE_IDS: Record<string, number> = {
  pre_semantic: 3,
  field_mapping: 5,
  hierarchy: 7,
  artifacts_review: 9,
};

export function gateNodeIdForType(gateType: string): number | undefined {
  return SCHEMA_GATE_NODE_IDS[gateType];
}

export function pipelineProgressNode(session: SchemaMappingStatusResponse): number {
  const cn = session.current_node ?? 0;
  if (cn > 0) return cn;
  const running = (session.nodes ?? []).find((n) =>
    String(n.status ?? "").toLowerCase().includes("running"),
  );
  return running?.node_id ?? 0;
}

export function isSchemaGateReachable(
  gateType: keyof typeof SCHEMA_GATE_NODE_IDS,
  session: SchemaMappingStatusResponse,
): boolean {
  const gateNode = SCHEMA_GATE_NODE_IDS[gateType];
  return pipelineProgressNode(session) >= gateNode;
}

export function isSchemaGatePayloadActive(
  gateType: keyof typeof SCHEMA_GATE_NODE_IDS,
  session: SchemaMappingStatusResponse,
): boolean {
  const normalized = normalizeSchemaGateType(
    typeof session.pending_gate_type === "string" ? session.pending_gate_type : null,
  );
  if (normalized !== gateType) return false;
  const st = String(session.status ?? "").toLowerCase();
  return st === "awaiting_review" || st === "step_paused";
}

export function isSchemaGatePayloadReady(session: SchemaMappingStatusResponse | undefined): boolean {
  const payload = session?.pending_gate_payload;
  return !!payload && typeof payload === "object" && Object.keys(payload).length > 0;
}

/** awaiting_review with a gate payload the center panel knows how to render. */
export function isSchemaAwaitingReviewReadyForUi(
  session: SchemaMappingStatusResponse | undefined,
): boolean {
  if (!session) return false;
  const st = String(session.status ?? "").toLowerCase();
  if (st !== "awaiting_review") return false;
  if (!isSchemaGatePayloadReady(session)) return false;
  const norm = normalizeSchemaGateType(
    typeof session.pending_gate_type === "string" ? session.pending_gate_type : null,
  );
  return (
    norm === "pre_semantic" ||
    norm === "field_mapping" ||
    norm === "hierarchy" ||
    norm === "artifacts_review"
  );
}

function isSchemaPipelineBusyStatus(st: string): boolean {
  return (
    st === "running" ||
    st === "processing" ||
    st === "pending" ||
    st === "ingest" ||
    st === "advancing" ||
    st.includes("fetch") ||
    st === "step_paused" ||
    st.includes("paused") ||
    st === "awaiting_review"
  );
}

/**
 * Status poll policy (aligned with migration):
 * - running / ingest / processing: poll until next pause or gate
 * - step_paused: stop (user clicks Continue → /advance)
 * - awaiting_review: poll until gate payload is ready for the center panel
 * - complete / error / ddl_failed: stop
 */
export function schemaMappingStatusNeedsPoll(
  session: SchemaMappingStatusResponse | undefined,
  options?: { forceUntil?: number },
): boolean {
  if (!session) return true;

  const st = String(session.status ?? "").toLowerCase();
  const forceUntil = options?.forceUntil;

  if (
    st === "complete" ||
    st === "error" ||
    st === "ddl_failed" ||
    st === "failed" ||
    st === "cancelled" ||
    st === "canceled"
  ) {
    return false;
  }

  if (st === "awaiting_review") {
    return !isSchemaAwaitingReviewReadyForUi(session);
  }

  if (st === "step_paused" || st.includes("paused")) {
    if (!isSchemaGatePayloadReady(session)) return true;
    return false;
  }

  const keep =
    st === "running" ||
    st === "processing" ||
    st === "pending" ||
    st === "ingest" ||
    st === "advancing" ||
    st.includes("fetch") ||
    !st;

  if (!keep && typeof forceUntil === "number" && Date.now() < forceUntil && isSchemaPipelineBusyStatus(st)) {
    return true;
  }

  return keep;
}

export function schemaMappingPollIntervalMs(
  session: SchemaMappingStatusResponse | undefined,
  baseInterval = 3000,
): number {
  const st = String(session?.status ?? "").toLowerCase();
  if (st === "awaiting_review" || st === "step_paused" || st.includes("paused")) return 2500;
  if (
    st === "running" ||
    st === "processing" ||
    st === "ingest" ||
    st === "advancing" ||
    st.includes("fetch")
  ) {
    return baseInterval;
  }
  return 4000;
}

/**
 * Backend sometimes reports `status: "running"` while the terminal pipeline node
 * has actually finished (e.g. the hierarchy gate stays "running" with an embedded
 * Interrupt() in its logs, but Output Generation + Write to Database have already
 * completed with artifacts and DDL executed). Treat those sessions as complete so
 * the results panel renders instead of the perpetual spinner.
 */
export function isSchemaEffectivelyComplete(
  session: SchemaMappingStatusResponse | null | undefined,
): boolean {
  if (!session) return false;
  const status = String(session.status ?? "").toLowerCase();
  if (status === "complete") return true;
  // Hard signals the run is done: a target schema name + at least one artifact URL.
  const hasArtifacts =
    !!session.output_json_url || !!session.output_csv_url || !!session.output_sql_url;
  if (!hasArtifacts) return false;
  // Don't short-circuit while a real gate is awaiting input.
  if (session.pending_gate_type && session.pending_gate_payload) return false;
  // Terminal node must be complete (the highest-numbered node by id, typically
  // "Write to Database" or similar). Guards against a stale `status: running`
  // returned mid-flight before the last node actually started.
  const nodes = session.nodes ?? [];
  if (!nodes.length) return false;
  const terminal = nodes.reduce(
    (acc, n) => (n.node_id > (acc?.node_id ?? -1) ? n : acc),
    nodes[0],
  );
  const terminalStatus = String(terminal?.status ?? "").toLowerCase();
  return terminalStatus === "complete";
}
