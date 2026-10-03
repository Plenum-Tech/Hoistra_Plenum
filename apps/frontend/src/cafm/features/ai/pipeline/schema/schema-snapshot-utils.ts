import { useEffect, useMemo, useState } from "react";
import type { SchemaMappingStatusResponse } from "../../chat-api";

export type SchemaNodeSnapshot = {
  nodeId: number;
  stepKey: string;
  payload: Record<string, unknown>;
  nodeName?: string;
};

export type SchemaGateSnapshot = {
  id: string;
  gateType: "pre_semantic" | "field_mapping" | "hierarchy" | "artifacts_review";
  payload: Record<string, unknown>;
};

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

export function payloadRichnessScore(input: unknown, depth = 0): number {
  if (input == null) return 0;
  if (depth > 4) return 1;
  if (Array.isArray(input)) {
    const items = input.slice(0, 50);
    return 1 + items.reduce((sum, item) => sum + payloadRichnessScore(item, depth + 1), 0);
  }
  if (isRecord(input)) {
    const keys = Object.keys(input);
    return keys.length + keys.slice(0, 50).reduce((sum, key) => sum + payloadRichnessScore(input[key], depth + 1), 0);
  }
  if (typeof input === "string") return input.length > 0 ? 1 : 0;
  return 1;
}

/** Aligns with schema-pipeline-tracker logNodeId (gates share a log node id). */
export const SCHEMA_NODE_META: Record<
  number,
  { title: string; stepKey?: string; gateType?: SchemaGateSnapshot["gateType"] }
> = {
  0: { title: "Canonical Schema", stepKey: "step_0_canonical" },
  1: { title: "Schema Ingestion", stepKey: "step_1_ingest" },
  2: { title: "Deterministic Mapping", stepKey: "step_2_deterministic" },
  3: { title: "Pre-Semantic Review", gateType: "pre_semantic" },
  4: { title: "Semantic Mapping", stepKey: "step_3_semantic" },
  5: { title: "Field Mapping Review", gateType: "field_mapping" },
  6: { title: "Hierarchy Detection", stepKey: "step_5_hierarchy" },
  7: { title: "Hierarchy Verification", gateType: "hierarchy" },
  8: { title: "Output Generation", stepKey: "step_7_output" },
  9: { title: "Artifacts Review", gateType: "artifacts_review" },
  10: { title: "Write to Database", stepKey: "step_8_write" },
};

export const SCHEMA_NODE_TITLES = Object.fromEntries(
  Object.entries(SCHEMA_NODE_META).map(([id, m]) => [Number(id), m.title]),
) as Record<number, string>;

export const SCHEMA_NODE_STEP_KEYS = Object.fromEntries(
  Object.entries(SCHEMA_NODE_META)
    .filter(([, m]) => m.stepKey)
    .map(([id, m]) => [Number(id), m.stepKey!]),
) as Record<number, string>;

export const SCHEMA_STEP_KEY_ALIASES: Record<string, string> = {
  step_2_5_preprocess: "step_2_deterministic",
  step_2_5_preprocess_validate: "step_2_deterministic",
  step_3_semantic_mapping: "step_3_semantic",
  step_4_semantic_mapping: "step_4_semantic",
  step_4_human_review: "step_4_semantic",
  step_6_hierarchy_detection: "step_6_hierarchy",
  step_6_verify_hierarchy: "step_6_hierarchy",
  step_8_output_generation: "step_8_output",
  step_8_write_output: "step_8_write",
  step_8_write_mappings: "step_8_write",
  step_8_write_to_db: "step_8_write",
  step_9_finalize: "step_9_finalize",
  step_9_finalise: "step_9_finalize",
  step_9_complete: "step_9_finalize",
};

export const SCHEMA_STEP_KEY_TO_NODE = Object.entries(SCHEMA_NODE_STEP_KEYS).reduce<Record<string, number>>(
  (acc, [nodeId, stepKey]) => {
    acc[stepKey] = Number(nodeId);
    return acc;
  },
  {},
);

export const SCHEMA_GATE_LABELS: Record<string, string> = {
  pre_semantic: "Pre-Semantic Review",
  field_mapping: "Field Mapping Review",
  hierarchy: "Hierarchy Verification",
  artifacts_review: "Artifacts Review",
};

export function normalizeSchemaGateType(v: string | null | undefined) {
  if (!v) return null;
  if (v === "pre_semantic" || v === "field_mapping" || v === "hierarchy" || v === "artifacts_review") return v;
  const s = v.toLowerCase();
  if (s.includes("pre") && s.includes("semantic")) return "pre_semantic";
  if (s.includes("human") && s.includes("review")) return "field_mapping";
  if (s.includes("field") && s.includes("map")) return "field_mapping";
  if (s.includes("verify") && s.includes("hier")) return "hierarchy";
  if (s.includes("hier")) return "hierarchy";
  if (s.includes("artifact")) return "artifacts_review";
  return v;
}

export function normalizeStepKeyForHistory(stepKey: string) {
  return SCHEMA_STEP_KEY_ALIASES[stepKey] ?? stepKey;
}

export function unwrapGatePayload(input: unknown): Record<string, unknown> | null {
  const candidates: unknown[] = [
    input,
    isRecord(input) ? input.payload : null,
    isRecord(input) ? input.data : null,
    isRecord(input) ? input.gate_payload : null,
    isRecord(input) ? input.pending_gate_payload : null,
    isRecord(input) ? input.result : null,
    isRecord(input) ? input.output : null,
  ];
  for (const c of candidates) {
    if (isRecord(c)) return c;
  }
  return null;
}

function parseJsonRecord(input: unknown): Record<string, unknown> | null {
  if (typeof input !== "string") return null;
  try {
    const parsed: unknown = JSON.parse(input);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/** Prefer the richest object shape (full step UI needs tables/stats, not thin wrappers). */
export function resolveNodeOutputPayload(raw: unknown): Record<string, unknown> | null {
  const candidates: Array<Record<string, unknown>> = [];
  const parsed = parseJsonRecord(raw);
  if (parsed) candidates.push(parsed);
  if (isRecord(raw)) candidates.push(raw);
  const unwrapped = unwrapGatePayload(raw);
  if (unwrapped) candidates.push(unwrapped);

  let best: Record<string, unknown> | null = null;
  let bestScore = -1;
  for (const c of candidates) {
    const score = payloadRichnessScore(c);
    if (score > bestScore) {
      best = c;
      bestScore = score;
    }
  }
  return best;
}

function readStepKeyFromPayload(payload: Record<string, unknown>, fallback: string) {
  const fromPayload =
    typeof payload.step_key === "string"
      ? payload.step_key
      : typeof payload.stepKey === "string"
        ? payload.stepKey
        : null;
  return fromPayload ?? fallback;
}

function isNodeCompleteStatus(status: string) {
  const s = status.toLowerCase();
  return s.includes("complete") || s === "done";
}

export function isNodeSemanticMappingCompleteInSession(session: SchemaMappingStatusResponse): boolean {
  const node = (session.nodes ?? []).find((n) => n.node_id === 4);
  if (!node) return false;
  const st = String(node.status ?? "").toLowerCase();
  if (st === "completed" || st === "complete" || st === "done") return true;
  const output = resolveNodeOutputPayload(node.output);
  if (!output) return false;
  return (
    typeof output.tier2_auto_mapped === "number" ||
    typeof output.tier2_flagged === "number" ||
    Array.isArray(output.semantic_results) ||
    !!output.tier2_flagged_by_table ||
    !!output.tier2_unmappable_by_table
  );
}

export function buildSnapshotsFromSession(session: SchemaMappingStatusResponse): {
  nodeSnapshots: SchemaNodeSnapshot[];
  gateSnapshots: SchemaGateSnapshot[];
} {
  const nodeById: Record<number, SchemaNodeSnapshot> = {};

  const upsertNode = (incoming: SchemaNodeSnapshot) => {
    const prev = nodeById[incoming.nodeId];
    if (!prev) {
      nodeById[incoming.nodeId] = incoming;
      return;
    }
    if (payloadRichnessScore(incoming.payload) >= payloadRichnessScore(prev.payload)) {
      nodeById[incoming.nodeId] = incoming;
    }
  };

  for (const n of session.nodes ?? []) {
    const meta = SCHEMA_NODE_META[n.node_id];
    if (!meta?.stepKey) continue;
    const output = resolveNodeOutputPayload(n.output);
    if (!output) continue;
    upsertNode({
      nodeId: n.node_id,
      stepKey: readStepKeyFromPayload(output, meta.stepKey),
      payload: output,
      nodeName: SCHEMA_NODE_TITLES[n.node_id] ?? n.node_name,
    });
  }

  if (
    session.status === "step_paused" &&
    typeof session.pending_gate_type === "string" &&
    session.pending_gate_type.startsWith("step_") &&
    isRecord(session.pending_gate_payload)
  ) {
    const pendingKey = session.pending_gate_type;
    const normalized = normalizeStepKeyForHistory(pendingKey);
    const nodeId = SCHEMA_STEP_KEY_TO_NODE[normalized] ?? SCHEMA_STEP_KEY_TO_NODE[pendingKey];
    if (typeof nodeId === "number") {
      upsertNode({
        nodeId,
        stepKey: pendingKey,
        payload: session.pending_gate_payload,
        nodeName:
          SCHEMA_NODE_TITLES[nodeId] ??
          (session.nodes ?? []).find((node) => node.node_id === nodeId)?.node_name,
      });
    }
  }

  const progressNode = (() => {
    const cn = session.current_node ?? 0;
    if (cn > 0) return cn;
    const running = (session.nodes ?? []).find((node) =>
      String(node.status ?? "").toLowerCase().includes("running"),
    );
    return running?.node_id ?? cn;
  })();

  const gateById = new Map<string, SchemaGateSnapshot>();
  const activeGate = normalizeSchemaGateType(session.pending_gate_type ?? null);
  const st = String(session.status ?? "").toLowerCase();

  const semanticDone = isNodeSemanticMappingCompleteInSession(session);

  for (const n of session.nodes ?? []) {
    const meta = SCHEMA_NODE_META[n.node_id];
    if (!meta?.gateType) continue;
    if (meta.gateType === "field_mapping" && !semanticDone) continue;
    if (n.node_id > progressNode) continue;
    if (
      activeGate === meta.gateType &&
      (st === "awaiting_review" || st === "step_paused")
    ) {
      continue;
    }
    if (!isNodeCompleteStatus(String(n.status ?? ""))) continue;
    const output = resolveNodeOutputPayload(n.output);
    if (!output) continue;
    const id = `node_${n.node_id}_${meta.gateType}`;
    gateById.set(id, { id, gateType: meta.gateType, payload: output });
  }

  if (
    activeGate &&
    (activeGate !== "field_mapping" || semanticDone) &&
    (st === "awaiting_review" || st === "step_paused") &&
    isRecord(session.pending_gate_payload)
  ) {
    const gateNodeId = Number(
      Object.entries(SCHEMA_NODE_META).find(([, m]) => m.gateType === activeGate)?.[0] ?? NaN,
    );
    if (Number.isFinite(gateNodeId) && gateNodeId <= progressNode) {
      const id = `node_${gateNodeId}_${activeGate}`;
      gateById.set(id, {
        id,
        gateType: activeGate as SchemaGateSnapshot["gateType"],
        payload: session.pending_gate_payload as Record<string, unknown>,
      });
    }
  }

  return {
    nodeSnapshots: Object.values(nodeById).sort((a, b) => a.nodeId - b.nodeId),
    gateSnapshots: Array.from(gateById.values()).sort((a, b) => {
      const nodeA = Number(a.id.match(/^node_(\d+)_/)?.[1] ?? 999);
      const nodeB = Number(b.id.match(/^node_(\d+)_/)?.[1] ?? 999);
      return nodeA - nodeB;
    }),
  };
}

export function resolveSchemaHistoryPivot(session: SchemaMappingStatusResponse) {
  const normalizedGateType = normalizeSchemaGateType(session.pending_gate_type ?? null);
  const activeStepKey =
    session.status === "step_paused" &&
    typeof session.pending_gate_type === "string" &&
    session.pending_gate_type.startsWith("step_")
      ? normalizeStepKeyForHistory(session.pending_gate_type)
      : null;
  const activeNodeId = activeStepKey ? SCHEMA_STEP_KEY_TO_NODE[activeStepKey] : undefined;
  let activeGateNodeId =
    normalizedGateType && (session.status === "awaiting_review" || session.status === "step_paused")
      ? Number(
          Object.entries(SCHEMA_NODE_META).find(([, m]) => m.gateType === normalizedGateType)?.[0] ?? NaN,
        )
      : undefined;

  if (
    normalizedGateType === "field_mapping" &&
    !isNodeSemanticMappingCompleteInSession(session) &&
    Number.isFinite(activeGateNodeId)
  ) {
    activeGateNodeId = 4;
  }

  let historyPivot: number | null =
    typeof activeNodeId === "number"
      ? activeNodeId
      : Number.isFinite(activeGateNodeId)
        ? activeGateNodeId!
        : null;

  // Keep completed nodes above the active panel while running or after a failure.
  if (historyPivot == null) {
    const status = String(session.status ?? "").toLowerCase();
    const cn = session.current_node;
    if (typeof cn === "number" && cn >= 0) {
      if (
        status.includes("running") ||
        status === "processing" ||
        status === "pending" ||
        status === "error" ||
        status === "ddl_failed"
      ) {
        historyPivot = cn;
      }
    }
  }

  return { normalizedGateType, historyPivot };
}

export function filterHistoryForChat(
  session: SchemaMappingStatusResponse,
  nodeSnapshots: SchemaNodeSnapshot[],
  gateSnapshots: SchemaGateSnapshot[],
) {
  const { normalizedGateType, historyPivot } = resolveSchemaHistoryPivot(session);

  const pivot =
    historyPivot ??
    (() => {
      const cn = session.current_node ?? 0;
      if (cn > 0) return cn;
      return null;
    })();

  const gateHistory = gateSnapshots.filter((g) => {
    const gateNodeId = Number(g.id.match(/^node_(\d+)_/)?.[1] ?? 999);
    if (pivot != null && gateNodeId >= pivot) return false;
    if (
      normalizedGateType &&
      (session.status === "awaiting_review" || session.status === "step_paused") &&
      g.gateType === normalizedGateType
    ) {
      return false;
    }
    return true;
  });

  const visibleNodes =
    pivot == null ? nodeSnapshots : nodeSnapshots.filter((snap) => snap.nodeId !== pivot);
  const orderedNodes =
    pivot == null ? visibleNodes : visibleNodes.filter((snap) => snap.nodeId < pivot);

  return { gateHistory, orderedNodes };
}

export function schemaActiveScrollKey(session: SchemaMappingStatusResponse | null | undefined) {
  if (!session) return "";
  const gate = normalizeSchemaGateType(session.pending_gate_type ?? null);
  return `${session.status ?? ""}:${session.pending_gate_type ?? ""}:${gate ?? ""}:${session.current_node ?? ""}`;
}

export type SchemaHistoryChatItem =
  | { kind: "gate"; snapshot: SchemaGateSnapshot; nodeId: number }
  | { kind: "node"; snapshot: SchemaNodeSnapshot };

/** Completed steps in pipeline order for the center panel history block. */
export function buildOrderedSchemaHistoryItems(
  session: SchemaMappingStatusResponse,
  source?: {
    nodeById?: Record<number, SchemaNodeSnapshot>;
    gateById?: Map<string, SchemaGateSnapshot>;
  },
): SchemaHistoryChatItem[] {
  const built = buildSnapshotsFromSession(session);
  const nodeSnapshots = source?.nodeById
    ? Object.values(source.nodeById).sort((a, b) => a.nodeId - b.nodeId)
    : built.nodeSnapshots;
  const gateSnapshots = source?.gateById
    ? Array.from(source.gateById.values()).sort((a, b) => {
        const nodeA = Number(a.id.match(/^node_(\d+)_/)?.[1] ?? 999);
        const nodeB = Number(b.id.match(/^node_(\d+)_/)?.[1] ?? 999);
        return nodeA - nodeB;
      })
    : built.gateSnapshots;
  const { gateHistory, orderedNodes } = filterHistoryForChat(session, nodeSnapshots, gateSnapshots);
  const items: SchemaHistoryChatItem[] = [];
  const sortedIds = Object.keys(SCHEMA_NODE_META)
    .map(Number)
    .sort((a, b) => a - b);

  for (const nodeId of sortedIds) {
    const meta = SCHEMA_NODE_META[nodeId];
    if (meta.gateType) {
      const snap = gateHistory.find((g) => g.id.startsWith(`node_${nodeId}_`));
      if (snap) items.push({ kind: "gate", snapshot: snap, nodeId });
    }
    if (meta.stepKey) {
      const snap = orderedNodes.find((n) => n.nodeId === nodeId);
      if (snap) items.push({ kind: "node", snapshot: snap });
    }
  }
  return items;
}

const SCHEMA_NODE_BY_ID_KEY = (sessionId: string) =>
  `plenum-schema-node-by-id:${sessionId}`;
const SCHEMA_GATE_BY_ID_KEY = (sessionId: string) =>
  `plenum-schema-gate-by-id:${sessionId}`;

function loadNodeById(sessionId: string): Record<number, SchemaNodeSnapshot> {
  if (!sessionId || typeof window === "undefined") return {};
  try {
    const raw = window.sessionStorage.getItem(SCHEMA_NODE_BY_ID_KEY(sessionId));
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: Record<number, SchemaNodeSnapshot> = {};
    for (const [k, v] of Object.entries(parsed as Record<string, SchemaNodeSnapshot>)) {
      const n = Number(k);
      if (Number.isFinite(n) && v && typeof v === "object") out[n] = v;
    }
    return out;
  } catch {
    return {};
  }
}

function saveNodeById(sessionId: string, nodes: Record<number, SchemaNodeSnapshot>) {
  if (!sessionId || typeof window === "undefined") return;
  try {
    if (Object.keys(nodes).length === 0) {
      window.sessionStorage.removeItem(SCHEMA_NODE_BY_ID_KEY(sessionId));
    } else {
      window.sessionStorage.setItem(SCHEMA_NODE_BY_ID_KEY(sessionId), JSON.stringify(nodes));
    }
  } catch {
    /* ignore */
  }
}

function loadGateById(sessionId: string): Map<string, SchemaGateSnapshot> {
  if (!sessionId || typeof window === "undefined") return new Map();
  try {
    const raw = window.sessionStorage.getItem(SCHEMA_GATE_BY_ID_KEY(sessionId));
    if (!raw) return new Map();
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return new Map();
    const out = new Map<string, SchemaGateSnapshot>();
    for (const entry of parsed) {
      if (Array.isArray(entry) && entry.length === 2 && typeof entry[0] === "string" && entry[1]) {
        out.set(entry[0] as string, entry[1] as SchemaGateSnapshot);
      }
    }
    return out;
  } catch {
    return new Map();
  }
}

function saveGateById(sessionId: string, gates: Map<string, SchemaGateSnapshot>) {
  if (!sessionId || typeof window === "undefined") return;
  try {
    if (gates.size === 0) {
      window.sessionStorage.removeItem(SCHEMA_GATE_BY_ID_KEY(sessionId));
    } else {
      window.sessionStorage.setItem(
        SCHEMA_GATE_BY_ID_KEY(sessionId),
        JSON.stringify(Array.from(gates.entries())),
      );
    }
  } catch {
    /* ignore */
  }
}

function mergeSchemaPayloads(
  prev: Record<string, unknown> | undefined,
  next: Record<string, unknown>,
): Record<string, unknown> {
  if (!prev) return next;
  const merged: Record<string, unknown> = { ...prev };
  for (const [k, v] of Object.entries(next)) {
    if (v == null) continue;
    if (Array.isArray(v) && v.length === 0 && Array.isArray(merged[k]) && (merged[k] as unknown[]).length > 0) continue;
    if (
      typeof v === "object" &&
      !Array.isArray(v) &&
      Object.keys(v as object).length === 0 &&
      merged[k] &&
      typeof merged[k] === "object"
    ) continue;
    merged[k] = v;
  }
  return merged;
}

/** Retains rich step/gate payloads across polls (same pattern as migration center history). */
export function useAccumulatedSchemaSnapshots(
  session: SchemaMappingStatusResponse | null | undefined,
  sessionId: string,
) {
  const [nodeById, setNodeById] = useState<Record<number, SchemaNodeSnapshot>>(() =>
    loadNodeById(sessionId),
  );
  const [gateById, setGateById] = useState<Map<string, SchemaGateSnapshot>>(() =>
    loadGateById(sessionId),
  );

  useEffect(() => {
    // Rehydrate from sessionStorage on sessionId change — survives panel unmount.
    setNodeById(loadNodeById(sessionId));
    setGateById(loadGateById(sessionId));
  }, [sessionId]);

  useEffect(() => {
    saveNodeById(sessionId, nodeById);
  }, [sessionId, nodeById]);

  useEffect(() => {
    saveGateById(sessionId, gateById);
  }, [sessionId, gateById]);

  useEffect(() => {
    if (!session) return;

    const built = buildSnapshotsFromSession(session);

    setNodeById((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const snap of built.nodeSnapshots) {
        const prevSnap = next[snap.nodeId];
        if (!prevSnap) {
          next[snap.nodeId] = snap;
          changed = true;
          continue;
        }
        const mergedPayload = mergeSchemaPayloads(prevSnap.payload, snap.payload);
        const mergedScore = payloadRichnessScore(mergedPayload);
        if (mergedScore >= payloadRichnessScore(prevSnap.payload)) {
          next[snap.nodeId] = { ...snap, payload: mergedPayload };
          changed = true;
        }
      }
      return changed ? next : prev;
    });

    setGateById((prev) => {
      const next = new Map(prev);
      let changed = false;
      const semanticDone = isNodeSemanticMappingCompleteInSession(session);
      for (const snap of built.gateSnapshots) {
        if (snap.gateType === "field_mapping" && !semanticDone) {
          if (next.delete(snap.id)) changed = true;
          continue;
        }
        const existing = next.get(snap.id);
        if (!existing) {
          next.set(snap.id, snap);
          changed = true;
          continue;
        }
        const mergedPayload = mergeSchemaPayloads(existing.payload, snap.payload);
        const mergedScore = payloadRichnessScore(mergedPayload);
        if (mergedScore >= payloadRichnessScore(existing.payload)) {
          next.set(snap.id, { ...snap, payload: mergedPayload });
          changed = true;
        }
      }
      if (!semanticDone) {
        for (const [id, snap] of next) {
          if (snap.gateType === "field_mapping" && next.delete(id)) changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [session]);

  const historyItems = useMemo(
    () =>
      session
        ? buildOrderedSchemaHistoryItems(session, { nodeById, gateById })
        : [],
    [session, nodeById, gateById],
  );

  return { historyItems, nodeById, gateById };
}
