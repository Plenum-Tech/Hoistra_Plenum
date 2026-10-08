"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { Loader, XCircle, RotateCcw, ChevronDown, ChevronRight, CheckCircle } from "lucide-react";
import type {
  MigrationStatusResponse,
  MigrationPreSemanticGatePayload,
  MigrationPkApprovalGatePayload,
  MigrationUniqueTableGatePayload,
  MigrationColumnMappingGatePayload,
  MigrationClassificationGatePayload,
  MigrationGateFieldMappingRequest,
  MigrationFlaggedFieldItem,
  MigrationFieldMappingGatePayload,
  MigrationHierarchyGatePayload,
  MigrationFinalGatePayload,
  NodeInfo,
} from "../../chat-api";
import { schemaMapperApi, useMigrationAdvance, useMigrationGateFinal } from "../../chat-api";
import type { DeepAgentProcessLogInput } from "../deep-agent/deep-agent-process-log";
import {
  MIGRATION_NODE_LABELS,
  migrationGateLabel,
  liveNodeState,
} from "../deep-agent/deep-agent-migration-progress";
import ResultsPanel from "./results-panel";
import { TableResolutionPanels, ColumnIntelligencePanels } from "./migration-metadata-view";
import { usePipelineStageScrollSpy, setTableStagesRevealed, setColumnStagesRevealed } from "../pipeline-stage-link";
import StepPause from "./step-pause";
import { MigrationStepSnapshot } from "./step-pause";
import SemanticMappingStep from "./semantic-mapping-step";
import GatePreSemantic from "./gates/gate-pre-semantic";
import GatePkApproval from "./gates/gate-pk-approval";
import GateUniqueTableApproval from "./gates/gate-unique-table-approval";
import GateColumnMappingApproval from "./gates/gate-column-mapping-approval";
import GateClassificationApproval from "./gates/gate-classification-approval";
import GateFieldMapping from "./gates/gate-field-mapping";
import GateHierarchy from "./gates/gate-hierarchy";
import GateFinal from "./gates/gate-final";
import {
  buildFieldMappingPayloadFromMigration,
  countFieldMappingReviewItems,
  fieldMappingSubmitBlockedReason,
  findSemanticMappingNode,
  isFieldMappingPayload as isFieldMappingGatePayload,
  needsSemanticReviewBeforeFieldMapping,
  resolveFieldMappingGateControls,
  isPreSemanticPayload,
  isPreSemanticGatePending,
  resolvePreSemanticGatePayload,
  isPrematurePreprocessPoll,
  isPreprocessPausePayload,
  isPipelinePastFieldMappingGate,
  isPreprocessStepPauseKey,
  isSemanticMappingPausePayload,
  isSemanticMappingStepKey,
  isStepPauseBlockingFieldMapping,
  normalizeFieldMappingGatePayload,
  normalizeMigrationGateType,
  requiresFieldMappingLatch,
  requiresSemanticMappingLatch,
} from "./migration-gate-state";
import {
  isSemanticDismissed,
  markSemanticDismissed,
  applyTier2FieldMappingContinue,
  clearFieldMappingDraft,
  clearSemanticDismissed,
} from "./migration-field-mapping-draft";
import { useFieldMappingDraft } from "./use-field-mapping-draft";
import type { WizardActive, WizardBridge, WizardContentState } from "@/hoistra-wizard-bridge";
import { preSemanticSub } from "@/hoistra-wizard-steps.js";

interface Props {
  migration: MigrationStatusResponse | null | undefined;
  migrationId: string;
  onRefresh: () => void;
  onReset: () => void;
  showCompletedHistory?: boolean;
  /** Collapse the completed-steps history by default — set when viewing an OLDER saved
   *  version so the live run shows all its steps while old versions stay tucked away. */
  collapseCompletedHistory?: boolean;
  onFieldFocus?: (terms: string[], nodeId?: number) => void;
  /** Orchestrator right rail — compact semantic / gate layout. */
  embeddedRail?: boolean;
  /** Auto-call /advance on non-HITL step_paused (mirrors run_migration in single-door). */
  drivePipelineSteps?: boolean;
  /** Emit per-node field-mapping entries into the right-side Process log. */
  onProcessLog?: (entry: DeepAgentProcessLogInput) => void;
  /**
   * Set when the user picked a saved version from the dropdown. Snapshots are
   * read from a version-scoped sessionStorage key and writes are suppressed,
   * so reviewing a saved version doesn't trample the live run's storage. When
   * undefined, the component operates on the live (migrationId-scoped) keys.
   */
  viewingVersionId?: string;
  /**
   * Hoistra's step-by-step card (src/cafm/hoistra-migration-wizard.tsx). When set, this panel
   * renders only the live step — no history, no restart header — and reports what it is showing
   * (which step, which gate, the kept step outputs) through the bridge, so the card can draw
   * its step rail and reopen finished steps. See hoistra-wizard-bridge.ts.
   */
  wizard?: WizardBridge;
}

/** step_paused keys that run_migration advances without a human StepPause screen. */
const ORCHESTRATOR_AUTO_ADVANCE_STEPS = new Set([
  "step_1_ingest",
  "step_2_deterministic",
  "step_2_deterministic_mapping",
  "step_7_hierarchy",
  "step_7_hierarchy_detection",
  "step_9_output",
  "step_9_output_generation",
  "step_9_write",
]);

function shouldOrchestratorAutoAdvanceStep(
  stepKey: string,
  semanticMappingDismissed: boolean,
): boolean {
  if (ORCHESTRATOR_AUTO_ADVANCE_STEPS.has(stepKey)) return true;
  const normalized = STEP_KEY_ALIASES[stepKey] ?? stepKey;
  if (ORCHESTRATOR_AUTO_ADVANCE_STEPS.has(normalized)) return true;
  if (
    semanticMappingDismissed &&
    (stepKey.includes("semantic") || normalized === "step_4_semantic")
  ) {
    return true;
  }
  return false;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

/** djb2 over a string — a cheap change detector for the wizard's report signature. */
function wizardHash(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i += 1) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return h;
}

function isPrimitive(v: unknown): v is string | number | boolean | null {
  return v === null || typeof v === "string" || typeof v === "number" || typeof v === "boolean";
}

function valuePreview(v: unknown) {
  if (v === null) return "null";
  if (typeof v === "string") return v.length > 60 ? `${v.slice(0, 60)}…` : v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return `${v.length} items`;
  if (isRecord(v)) return `${Object.keys(v).length} fields`;
  return String(v);
}

function payloadRichnessScore(input: unknown, depth = 0): number {
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

function GatePayloadViewer({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (isPrimitive(value)) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-mono bg-slate-100 text-slate-700">
        {value === null ? "null" : String(value)}
      </span>
    );
  }

  if (Array.isArray(value)) {
    const shown = value.slice(0, 30);
    return (
      <details className="rounded-lg border border-slate-200 bg-white overflow-hidden">
        <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-slate-600 bg-slate-50">
          {valuePreview(value)}
        </summary>
        <div className="px-3 py-2 space-y-2">
          {shown.length === 0 ? (
            <div className="text-xs text-slate-500">Empty</div>
          ) : (
            shown.map((it, idx) => (
              <div key={idx} className="flex items-start gap-2">
                <span className="text-[11px] font-mono text-slate-400 w-10 shrink-0 text-right">[{idx}]</span>
                <div className="min-w-0 flex-1">
                  <GatePayloadViewer value={it} depth={depth + 1} />
                </div>
              </div>
            ))
          )}
          {value.length > shown.length ? (
            <div className="text-[11px] text-slate-400">Showing first {shown.length} items</div>
          ) : null}
        </div>
      </details>
    );
  }

  if (isRecord(value)) {
    const entries = Object.entries(value).slice(0, 50);
    return (
      <div className="space-y-2">
        {entries.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[140px_1fr] gap-3 items-start">
            <div className="text-xs font-semibold text-slate-600 truncate">{k.replace(/_/g, " ")}</div>
            <div className="min-w-0">
              {isPrimitive(v) ? (
                <GatePayloadViewer value={v} depth={depth + 1} />
              ) : (
                <details className="rounded-lg border border-slate-200 bg-slate-50 overflow-hidden">
                  <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-slate-600">
                    {valuePreview(v)}
                  </summary>
                  <div className="px-3 py-2">
                    <GatePayloadViewer value={v} depth={depth + 1} />
                  </div>
                </details>
              )}
            </div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-mono bg-slate-100 text-slate-700">
      {String(value)}
    </span>
  );
}

// Live "Running process" log tail. Streams the currently-running node's log lines so the center
// running view keeps showing real processing output (ingest → deterministic → …) instead of only a
// spinner. Gate/step panels render their own content; this fills the plain running state where the
// panel would otherwise be a bare "Migration running…" card.
function LiveNodeLogCard({ nodes }: { nodes?: NodeInfo[] }) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const liveNode = useMemo<NodeInfo | null>(() => {
    const list = nodes ?? [];
    // The backend flips only some nodes to "running" (node 2 auto-advances while still "pending"),
    // so a node that has emitted logs is the strongest signal of the current work: prefer an
    // explicitly-running node that has logs, else the furthest node that has produced any logs.
    const running = list.find((n) => liveNodeState(n) === "running" && n.logs.length > 0);
    if (running) return running;
    return [...list].reverse().find((n) => n.logs.length > 0) ?? null;
  }, [nodes]);
  const lines = useMemo(
    () => (liveNode?.logs ?? []).filter((l) => typeof l === "string" && l.trim().length > 0),
    [liveNode],
  );
  // Keep the newest line in view as the append-only log streams, like a tailing console.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);
  if (!liveNode || lines.length === 0) return null;
  const label =
    MIGRATION_NODE_LABELS[liveNode.node_id] ?? liveNode.node_name ?? `Step ${liveNode.node_id}`;
  return (
    <div className="w-full max-w-xl rounded-lg border border-slate-200 bg-slate-50 text-left">
      <div className="flex items-center gap-2 border-b border-slate-200 px-3 py-2">
        <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-indigo-500" />
        <span className="text-[11px] font-semibold text-slate-600">Running process · {label}</span>
        <span className="ml-auto text-[10px] tabular-nums text-slate-400">
          {lines.length} line{lines.length === 1 ? "" : "s"}
        </span>
      </div>
      <div
        ref={scrollRef}
        className="max-h-56 space-y-0.5 overflow-y-auto px-3 py-2 font-mono text-[10px] leading-relaxed text-slate-600"
      >
        {lines.map((line, i) => (
          <div key={i} className="whitespace-pre-wrap break-all">
            {line}
          </div>
        ))}
      </div>
    </div>
  );
}

const NODE_LABELS: Record<number, string> = {
  1: "File Ingestion in progress…",
  2: "Deterministic Mapping in progress…",
  3: "Pre-Semantic Review in progress…",
  4: "Semantic Mapping in progress…",
  5: "Field Mapping Review in progress…",
  6: "Data Preprocessing in progress…",
  7: "Hierarchy Detection in progress…",
  8: "Hierarchy Verification in progress…",
  9: "Output Generation in progress…",
};

export const NODE_TITLES: Record<number, string> = {
  1: "File ingestion",
  2: "Deterministic mapping",
  3: "Pre-semantic review gate",
  4: "Semantic mapping",
  5: "Field mapping review",
  6: "Data preprocessing",
  7: "Hierarchy detection",
  8: "Hierarchy confirmation gate",
  9: "Data artifacts (SQL, CSV, JSON)",
};

const NODE_STEP_KEYS: Record<number, string> = {
  1: "step_1_ingest",
  2: "step_2_deterministic",
  3: "step_3_pre_semantic",
  4: "step_4_semantic",
  5: "step_5_field_mapping",
  6: "step_6_preprocess",
  7: "step_7_hierarchy",
  8: "step_8_hierarchy_gate",
  9: "step_9_output",
};

const STEP_KEY_ALIASES: Record<string, string> = {
  // Canonical backend variants per spec
  step_2_deterministic_mapping: "step_2_deterministic",
  // Backend pauses semantic at step_3_semantic_mapping (graph node 3; output often on node 4)
  step_3_semantic_mapping: "step_4_semantic",
  step_3_semantic: "step_4_semantic",
  step_4_semantic_mapping: "step_4_semantic",
  step_5_preprocess: "step_6_preprocess",
  step_5_preprocess_validate: "step_6_preprocess",
  step_6_data_preprocessing: "step_6_preprocess",
  step_7_hierarchy_detection: "step_7_hierarchy",
  step_9_output_generation: "step_9_output",
  // Legacy aliases (backward compat)
  step_4_field_mapping_review: "step_4_semantic",
  step_6_resolve_hierarchy: "step_6_preprocess",
  step_7_preprocess: "step_7_hierarchy",
  step_8_output: "step_9_output",
  step_8_output_generation: "step_9_output",
  step_9_output: "step_9_output",
  step_9_write: "step_9_output",
};

const STEP_KEY_TO_NODE = Object.entries(NODE_STEP_KEYS).reduce<Record<string, number>>((acc, [nodeId, stepKey]) => {
  acc[stepKey] = Number(nodeId);
  return acc;
}, {});

// PRODUCT_STEPS removed — was only used by UpcomingNodeSteps (now deleted).
// The pipeline tracker already shows the canonical step list.

// UpcomingNodeSteps removed — the static future-step list duplicated the
// pipeline tracker, took noticeable vertical space, and gave a false sense
// that those steps could be acted on individually. The workflow screen now
// focuses on current step + completed steps + pending action only.

export type NodeSnapshot = {
  nodeId: number;
  stepKey: string;
  payload: Record<string, unknown>;
  nodeName?: string;
};

export type PreSemanticSubmittedSnapshot = {
  id: string;
  payload: MigrationPreSemanticGatePayload;
  decisions: Record<
    string,
    Array<{ source_field: string; decision: "approve" | "semantic"; target_field?: string }>
  >;
};

const PRE_SEMANTIC_HISTORY_KEY = (migrationId: string) =>
  `plenum-migration-pre-semantic-history:${migrationId}`;

function loadPreSemanticHistory(migrationId: string): PreSemanticSubmittedSnapshot[] {
  if (!migrationId || typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(PRE_SEMANTIC_HISTORY_KEY(migrationId));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? (parsed as PreSemanticSubmittedSnapshot[]) : [];
  } catch {
    return [];
  }
}

function savePreSemanticHistory(migrationId: string, snapshots: PreSemanticSubmittedSnapshot[]) {
  if (!migrationId || typeof window === "undefined") return;
  try {
    if (snapshots.length === 0) {
      localStorage.removeItem(PRE_SEMANTIC_HISTORY_KEY(migrationId));
    } else {
      localStorage.setItem(PRE_SEMANTIC_HISTORY_KEY(migrationId), JSON.stringify(snapshots));
    }
  } catch {
    /* ignore quota / private mode */
  }
}

/**
 * Per-node payload snapshots persist across panel unmounts/remounts, backend
 * response variants, AND tab-close/reopen — so a completed run's per-step cards
 * stay reviewable later. localStorage (not sessionStorage) is deliberate: the
 * user reviews finished migrations across sessions, and the rich payload a node
 * emitted live is not always re-derivable from a later status poll. The backend
 * sometimes (a) doesn't echo old node outputs once a newer step runs and (b)
 * replaces a metric-rich payload with a log-heavy one for the same node. Either
 * case would zero out the rendered metrics. Persisting + merging keeps the
 * per-step data stable. Keyed by migrationId (or version:<id>), so no leakage.
 */
const SNAPSHOT_BY_NODE_KEY = (migrationId: string) =>
  `plenum-migration-snapshot-by-node:${migrationId}`;

function loadSnapshotByNode(migrationId: string): Record<number, NodeSnapshot> {
  if (!migrationId || typeof window === "undefined") return {};
  try {
    const raw = localStorage.getItem(SNAPSHOT_BY_NODE_KEY(migrationId));
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: Record<number, NodeSnapshot> = {};
    for (const [k, v] of Object.entries(parsed as Record<string, NodeSnapshot>)) {
      const n = Number(k);
      if (Number.isFinite(n) && v && typeof v === "object") out[n] = v;
    }
    return out;
  } catch {
    return {};
  }
}

function saveSnapshotByNode(migrationId: string, snapshots: Record<number, NodeSnapshot>) {
  if (!migrationId || typeof window === "undefined") return;
  try {
    if (Object.keys(snapshots).length === 0) {
      localStorage.removeItem(SNAPSHOT_BY_NODE_KEY(migrationId));
    } else {
      localStorage.setItem(SNAPSHOT_BY_NODE_KEY(migrationId), JSON.stringify(snapshots));
    }
  } catch {
    /* ignore quota / private mode */
  }
}

/**
 * A frozen copy of a run that was discarded by "Restart from Node 1".
 *
 * Restart reuses the SAME migration_id (it rewinds in place rather than
 * creating a new migration), so without archiving, the re-run overwrites the
 * live snapshot store and the discarded run's finished cards vanish. We snapshot
 * the run here BEFORE the wipe so it stays reviewable as a collapsed block.
 *
 * This is complementary to the version-archive mechanism (viewingVersionId):
 *   - version-archive  → explicit "Save as version", reopened via the dropdown
 *   - ArchivedRun      → automatic on every Restart-from-Node-1, shown inline
 */
/**
 * Read-only snapshot of a discarded run's "Migration complete" summary. The
 * ResultsPanel reads these straight off the live migration row (not from any
 * node output), so they are NOT recoverable from `snapshots` alone — we capture
 * them here at archive time so the archived run can show its final results.
 */
type ArchivedFinalResults = {
  cmmsName?: string;
  completedAt?: string | null;
  totalFields: number;
  t1Mapped: number;
  t2Auto: number;
  t2Human: number;
  unmapped: number;
};

type ArchivedRun = {
  id: string;
  archivedAt: number;
  snapshots: NodeSnapshot[];
  preSemantic: PreSemanticSubmittedSnapshot[];
  /** Present only when the discarded run had completed (or produced artifacts). */
  finalResults?: ArchivedFinalResults | null;
};

/** Capture the live migration's final-results summary, if it had one. */
function buildArchivedFinalResults(
  migration: MigrationStatusResponse | null | undefined,
): ArchivedFinalResults | null {
  if (!migration) return null;
  const st = (migration.status ?? "").toLowerCase();
  const hasArtifacts = !!(
    migration.output_json_url ||
    migration.output_csv_url ||
    migration.output_sql_url ||
    migration.migration_report_url
  );
  if (st !== "complete" && !hasArtifacts) return null;
  return {
    cmmsName: migration.cmms_name,
    completedAt: migration.completed_at ?? null,
    totalFields: migration.total_fields ?? 0,
    t1Mapped: migration.t1_mapped_count ?? 0,
    t2Auto: migration.t2_auto_count ?? 0,
    t2Human: migration.t2_human_count ?? 0,
    unmapped: migration.unmapped_count ?? 0,
  };
}

const ARCHIVED_RUNS_KEY = (migrationId: string) =>
  `plenum-migration-archived-runs:${migrationId}`;
/** Keep only the most recent N discarded runs to bound sessionStorage growth. */
const ARCHIVED_RUNS_CAP = 3;

function loadArchivedRuns(migrationId: string): ArchivedRun[] {
  if (!migrationId || typeof window === "undefined") return [];
  try {
    const raw = sessionStorage.getItem(ARCHIVED_RUNS_KEY(migrationId));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (r): r is ArchivedRun =>
        !!r &&
        typeof r === "object" &&
        Array.isArray((r as ArchivedRun).snapshots) &&
        Array.isArray((r as ArchivedRun).preSemantic),
    );
  } catch {
    return [];
  }
}

function saveArchivedRuns(migrationId: string, runs: ArchivedRun[]) {
  if (!migrationId || typeof window === "undefined") return;
  try {
    if (runs.length === 0) {
      sessionStorage.removeItem(ARCHIVED_RUNS_KEY(migrationId));
    } else {
      sessionStorage.setItem(ARCHIVED_RUNS_KEY(migrationId), JSON.stringify(runs));
    }
  } catch {
    /* ignore quota / private mode */
  }
}

/** Merge a new payload over an old one, preserving keys that the new one drops. */
function mergeSnapshotPayloads(
  prev: Record<string, unknown> | undefined,
  next: Record<string, unknown>,
): Record<string, unknown> {
  if (!prev) return next;
  const merged: Record<string, unknown> = { ...prev };
  for (const [k, v] of Object.entries(next)) {
    // Don't let the new payload null-out a previously populated field.
    if (v == null) continue;
    if (Array.isArray(v) && v.length === 0 && Array.isArray(merged[k]) && (merged[k] as unknown[]).length > 0) continue;
    if (typeof v === "object" && !Array.isArray(v) && Object.keys(v as object).length === 0 && merged[k] && typeof merged[k] === "object") continue;
    merged[k] = v;
  }
  return merged;
}

type ResolvedGateType = "pre_semantic" | "field_mapping" | "hierarchy" | "final_confirmation";

type StickyGateState = {
  type: ResolvedGateType;
  payload: Record<string, unknown>;
};

function normalizeStepKeyForHistory(stepKey: string) {
  return STEP_KEY_ALIASES[stepKey] ?? stepKey;
}

function extractIngestPayloadFromLogs(logs: string[]) {
  let rows: number | null = null;
  let cols: number | null = null;
  const tables: string[] = [];

  for (const line of logs) {
    const sheetMatch = line.match(/Sheet\s+([^:]+):\s*(\d+)\s*rows\s*[x×]\s*(\d+)\s*columns/i);
    if (sheetMatch) {
      const table = sheetMatch[1]?.trim();
      const r = Number(sheetMatch[2]);
      const c = Number(sheetMatch[3]);
      if (table) tables.push(table);
      if (Number.isFinite(r)) rows = Math.max(rows ?? 0, r);
      if (Number.isFinite(c)) cols = Math.max(cols ?? 0, c);
      continue;
    }

    const completeMatch = line.match(/complete:\s*(\d+)\s*rows,\s*(\d+)\s*columns/i);
    if (completeMatch) {
      const r = Number(completeMatch[1]);
      const c = Number(completeMatch[2]);
      if (Number.isFinite(r)) rows = Math.max(rows ?? 0, r);
      if (Number.isFinite(c)) cols = Math.max(cols ?? 0, c);
    }
  }

  return {
    row_count: rows ?? 0,
    column_count: cols ?? 0,
    tables: Array.from(new Set(tables)),
  };
}

function isFieldMappingPayload(v: unknown): v is Record<string, unknown> {
  return isFieldMappingGatePayload(v);
}

function isHierarchyPayload(v: unknown): v is Record<string, unknown> {
  if (!isRecord(v)) return false;
  return Array.isArray(v.hierarchies_to_review) || Array.isArray(v.review_items);
}

function isFinalPayload(v: unknown): v is Record<string, unknown> {
  if (!isRecord(v)) return false;
  return isRecord(v.summary);
}

function inferGateTypeFromPayload(v: unknown): "pre_semantic" | "field_mapping" | "hierarchy" | "final_confirmation" | null {
  if (isPreSemanticPayload(v)) return "pre_semantic";
  if (isFieldMappingPayload(v)) return "field_mapping";
  if (isHierarchyPayload(v)) return "hierarchy";
  if (isFinalPayload(v)) return "final_confirmation";
  return null;
}

function parseJsonRecord(input: unknown): Record<string, unknown> | null {
  if (typeof input !== "string") return null;
  const raw = input.trim();
  if (!raw || (raw[0] !== "{" && raw[0] !== "[")) return null;
  try {
    const parsed = JSON.parse(raw) as unknown;
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function unwrapGatePayload(input: unknown): Record<string, unknown> | null {
  const candidates: unknown[] = [
    input,
    parseJsonRecord(input),
    isRecord(input) ? input.payload : null,
    isRecord(input) ? input.data : null,
    isRecord(input) ? input.gate_payload : null,
    isRecord(input) ? input.pending_gate_payload : null,
    isRecord(input) ? input.result : null,
    isRecord(input) ? input.response : null,
    isRecord(input) ? input.output : null,
    isRecord(input) ? input.body : null,
  ];

  for (const c of candidates) {
    if (!isRecord(c)) continue;
    if (inferGateTypeFromPayload(c)) return c;
  }
  for (const c of candidates) {
    if (isRecord(c)) return c;
  }
  return null;
}

// Display sequence number per pipeline node, shown as a badge on each step card so the run reads
// as an ordered 1..N list. Deterministic mapping (node 2) has no card — slot 2 is the Table &
// Column Analysis section — so the visible steps number cleanly 1,2,3,… without a gap.
const SEQ_NUMBER_BY_NODE: Record<number, number> = { 1: 1, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9 };
const SEQ_ANALYSIS = 2;
// Right-rail Activity-Log `stage` key per migration node — lets a center step card publish its
// `data-pipeline-stage` so the cross-panel scroll-spy reveals + flashes the matching log row on the
// right (parity with the deterministic / column-analysis cards). Gate nodes (3/5/8) map to their
// review stage key. Keep in sync with run_activity.py's _emit_node stages + STAGE_SUBSTEP.
const PIPELINE_STAGE_BY_NODE: Record<number, string> = {
  3: "semantic_review",
  4: "semantic_tier2",
  5: "field_mapping_review",
  6: "preprocess_validate",
  7: "hierarchy",
  8: "hierarchy_confirmation",
  9: "data_artifacts",
};
const SEQ_PRE_SEMANTIC = 3;

function StepNumberBadge({ n }: { n: number }) {
  return (
    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-[10px] font-bold text-white">
      {n}
    </span>
  );
}

function CenterNodeHistory({
  snapshots,
  allNodes,
  headerless = false,
}: {
  snapshots: NodeSnapshot[];
  /**
   * The full NodeInfo[] from the live migration status. Forwarded to each
   * snapshot renderer so cross-snapshot lookups work — e.g. Node3Semantic
   * relabels "unmappable" → "new column" when a later gate approved the
   * field, and Node8Output falls back to upstream nodes for the table count
   * when its own payload omits it. Without this, those fallbacks short-circuit
   * to null and the user sees "—" / stale labels.
   */
  allNodes?: NodeInfo[];
  /**
   * When true: skip the "Completed Steps" header AND the per-card DOM id.
   * Used inside an archived (discarded) run block, where the header text
   * ("…while the next node runs") would be misleading, and where the
   * `migration-node-N` ids would otherwise collide with the live history's
   * (duplicate ids break the pipeline tracker's scroll-to-node).
   */
  headerless?: boolean;
}) {
  if (!snapshots.length) return null;

  return (
    <div className="space-y-4 mb-6">
      {headerless ? null : (
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Completed Steps</div>
          <div className="mt-1 text-xs text-slate-600">
            Earlier step outputs stay visible while the next step runs.
          </div>
        </div>
      )}

      {snapshots.map((snap) => {
        const stepKey = snap.stepKey;
        // Deterministic mapping (Node 2) is intentionally NOT shown as a panel snapshot card — its
        // result (T1-mapped / sent-to-semantic / new columns) is already surfaced in the run flow +
        // Activity Log; the standalone card here was redundant (per request).
        if (stepKey === "step_2_deterministic" || stepKey === "step_2_deterministic_mapping") {
          return null;
        }
        return (
          <div
            key={`hist_${snap.nodeId}_${stepKey}`}
            id={headerless ? undefined : `migration-node-${snap.nodeId}`}
            // Cross-panel scroll link: scrolling this completed step card into view reveals + flashes
            // its matching Activity-Log row on the right (same behaviour as the mapping-phase cards).
            data-pipeline-stage={PIPELINE_STAGE_BY_NODE[snap.nodeId]}
            className="rounded-xl border border-slate-200 bg-white shadow-sm p-5 scroll-mt-6"
          >
            <div className="mb-4">
              <div className="flex items-center gap-2">
                <StepNumberBadge n={SEQ_NUMBER_BY_NODE[snap.nodeId] ?? snap.nodeId} />
                <span className="text-sm font-semibold text-slate-800">
                  {snap.nodeName ?? "Step complete"}
                </span>
              </div>
              <div className="mt-0.5 text-xs text-slate-500">Status: completed snapshot</div>
            </div>
            <MigrationStepSnapshot stepKey={stepKey} payload={snap.payload} allNodes={allNodes} />
          </div>
        );
      })}
    </div>
  );
}

export function CenterPreSemanticHistory({
  snapshots,
}: {
  snapshots: PreSemanticSubmittedSnapshot[];
}) {
  if (!snapshots.length) return null;
  return (
    <div className="space-y-4 mb-6">
      {snapshots.map((snap, idx) => {
        // Render the SUBMITTED decisions (includes auto-approved new-table columns),
        // not just the reviewable items — so a new table shows ALL its columns with
        // their final (snake_case) target names.
        const decisionsByTable = snap.decisions ?? {};
        // Unmapped / unmappable columns are NOT in the approve/semantic decision set —
        // they live in the gate payload's unmapped bucket. Count them so a table's
        // "unmatched" total reflects columns that found no target (e.g. Assets/Vendors),
        // not just the ones sent to semantic. Schema (Fiix) uses unmapped_by_table;
        // the CSV/Excel migration uses unresolved_suggestion_by_table (field → guess).
        const _pp = snap.payload as
          | {
              unmapped_by_table?: Record<string, unknown[]>;
              unresolved_suggestion_by_table?: Record<string, Record<string, unknown>>;
            }
          | undefined;
        const unmappedCountByTable: Record<string, number> = {};
        for (const [t, arr] of Object.entries(_pp?.unmapped_by_table ?? {})) {
          unmappedCountByTable[t] = (unmappedCountByTable[t] ?? 0) + (Array.isArray(arr) ? arr.length : 0);
        }
        for (const [t, fields] of Object.entries(_pp?.unresolved_suggestion_by_table ?? {})) {
          const n = fields && typeof fields === "object" ? Object.keys(fields).length : 0;
          unmappedCountByTable[t] = (unmappedCountByTable[t] ?? 0) + n;
        }
        const targetLookup = new Map<string, string>();
        for (const [tbl, rows] of Object.entries(snap.payload?.review_items_by_table ?? {})) {
          for (const r of rows) targetLookup.set(`${tbl}.${r.source_field}`, r.target_field);
        }
        let approved = 0;
        let semantic = 0;
        let total = 0;
        for (const rows of Object.values(decisionsByTable)) {
          for (const r of rows) {
            total += 1;
            if (r.decision === "approve") approved += 1;
            else if (r.decision === "semantic") semantic += 1;
          }
        }
        return (
          <div
            key={snap.id}
            // Cross-panel scroll link → flashes the "3 · Schema Analysis — Table & Column Mapping" log row.
            data-pipeline-stage="semantic_review"
            className="rounded-xl border border-slate-200 bg-white shadow-sm p-5"
          >
            <div className="flex items-center justify-between gap-3 mb-3">
              <div className="flex items-center gap-2">
                <StepNumberBadge n={SEQ_PRE_SEMANTIC} />
                <div>
                  <div className="text-sm font-semibold text-slate-800">Schema Analysis — Table &amp; Column Mapping</div>
                  <div className="text-xs text-slate-500">Submitted decision set #{idx + 1}</div>
                </div>
              </div>
              <div className="text-xs text-slate-500 tabular-nums">
                {approved} approved · {semantic} semantic · {total} fields
              </div>
            </div>
            <div className="space-y-2">
              {Object.entries(decisionsByTable).map(([tbl, rows]) => {
                const tblMatched = rows.filter((r) => r.decision === "approve").length;
                const tblUnmatched =
                  rows.filter((r) => r.decision !== "approve").length +
                  (unmappedCountByTable[tbl] ?? 0);
                return (
                <div key={tbl} className="rounded-lg border border-slate-200 overflow-hidden">
                  <div className="px-3 py-2 bg-slate-50 text-xs font-semibold text-slate-700 flex items-center gap-2 flex-wrap">
                    <span>{tbl}</span>
                    <span className="inline-flex items-center gap-1.5">
                      <span className="rounded-full bg-emerald-100 text-emerald-700 px-1.5 py-0.5 text-[10px] font-medium">
                        {tblMatched} matched
                      </span>
                      {tblUnmatched > 0 ? (
                        <span className="rounded-full bg-amber-100 text-amber-700 px-1.5 py-0.5 text-[10px] font-medium">
                          {tblUnmatched} unmatched
                        </span>
                      ) : null}
                    </span>
                  </div>
                  <div className="divide-y divide-slate-100">
                    {rows.map((r, i) => {
                      const target =
                        (r.target_field && r.target_field.trim()) ||
                        targetLookup.get(`${tbl}.${r.source_field}`) ||
                        r.source_field;
                      return (
                        <div key={`${tbl}_${r.source_field}_${i}`} className="px-3 py-2 flex items-center justify-between gap-3 text-xs">
                          <div className="min-w-0">
                            <span className="font-mono text-slate-700">{r.source_field}</span>
                            <span className="mx-2 text-slate-300">→</span>
                            <span className="font-mono text-indigo-700">{target}</span>
                          </div>
                          <span
                            className={`inline-flex items-center px-2 py-0.5 rounded-full font-medium ${
                              r.decision === "approve"
                                ? "bg-green-100 text-green-700"
                                : "bg-blue-100 text-blue-700"
                            }`}
                          >
                            {r.decision}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Read-only "Migration complete" summary for a discarded run (no live actions). */
function ArchivedFinalResults({ results }: { results: ArchivedFinalResults }) {
  const totalMapped = results.t1Mapped + results.t2Auto + results.t2Human;
  const coveragePct =
    results.totalFields > 0
      ? Math.min(100, Math.round((totalMapped / results.totalFields) * 100))
      : 0;
  const rows = [
    { label: "T1 auto-mapped", value: results.t1Mapped, color: "text-green-600" },
    { label: "T2 auto-mapped", value: results.t2Auto, color: "text-indigo-600" },
    { label: "Human reviewed", value: results.t2Human, color: "text-purple-600" },
    { label: "Unmapped", value: results.unmapped, color: "text-slate-500" },
  ];
  return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50/40 shadow-sm p-5">
      <div className="flex items-center gap-2 mb-1">
        <span className="h-2 w-2 rounded-full bg-emerald-500" />
        <span className="text-sm font-semibold text-slate-800">Migration complete (archived)</span>
      </div>
      <div className="text-xs text-slate-500 mb-4">
        {results.cmmsName ? `${results.cmmsName} — ` : ""}read-only summary of the discarded run
        {results.completedAt ? ` · completed ${new Date(results.completedAt).toLocaleString()}` : ""}
      </div>
      <div className="grid grid-cols-2 gap-3 mb-4">
        {rows.map(({ label, value, color }) => (
          <div
            key={label}
            className="flex justify-between items-center py-1.5 border-b border-slate-100 last:border-0"
          >
            <span className="text-sm text-slate-600">{label}</span>
            <span className={`font-mono font-bold ${color}`}>{value}</span>
          </div>
        ))}
      </div>
      <div>
        <div className="flex justify-between text-sm mb-1.5">
          <span className="text-slate-600">Coverage</span>
          <span className="font-mono font-bold text-indigo-600">{coveragePct}%</span>
        </div>
        <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full ${
              coveragePct >= 80 ? "bg-green-500" : coveragePct >= 60 ? "bg-amber-500" : "bg-red-500"
            }`}
            style={{ width: `${coveragePct}%` }}
          />
        </div>
      </div>
    </div>
  );
}

/**
 * One discarded run, rendered as a collapsed block. Expands to the same frozen
 * step cards the live history uses (before-semantic nodes → pre-semantic
 * decisions → after-semantic nodes → final results), via the headerless
 * CenterNodeHistory so the cards don't duplicate the live history's DOM ids or
 * its (now-misleading) "while the next node runs" header.
 *
 * The `allNodes` passed to each card is reconstructed from THIS run's own
 * snapshots — never the live migration — so cross-node fallbacks (e.g.
 * Node8Output's upstream table-count lookup) resolve against the archived run's
 * data and an archived run never reads live migration state.
 */
function ArchivedRunBlock({ run }: { run: ArchivedRun }) {
  const [open, setOpen] = useState(false);
  const hasPreSem = run.preSemantic.length > 0;
  const before = run.snapshots
    .filter((s) => s.nodeId <= 2)
    .sort((a, b) => a.nodeId - b.nodeId);
  const after = run.snapshots
    .filter((s) => (hasPreSem ? s.nodeId > 3 : s.nodeId > 2))
    .sort((a, b) => a.nodeId - b.nodeId);
  const archivedNodes: NodeInfo[] = run.snapshots
    .slice()
    .sort((a, b) => a.nodeId - b.nodeId)
    .map((s) => ({
      node_id: s.nodeId,
      node_name: s.nodeName ?? "",
      status: "completed",
      started_at: null,
      completed_at: null,
      duration_ms: null,
      output: s.payload,
      logs: [],
    }));
  const when = new Date(run.archivedAt).toLocaleString();
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/60">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-4 py-2.5 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider hover:text-slate-700 transition-colors"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        Previous run — discarded {when} · click to review
      </button>
      {open ? (
        <div className="space-y-4 px-3 pb-3">
          <CenterNodeHistory snapshots={before} allNodes={archivedNodes} headerless />
          <CenterPreSemanticHistory snapshots={run.preSemantic} />
          <CenterNodeHistory snapshots={after} allNodes={archivedNodes} headerless />
          {run.finalResults ? <ArchivedFinalResults results={run.finalResults} /> : null}
        </div>
      ) : null}
    </div>
  );
}

/** Stack of discarded-run blocks, newest first, shown above the live history. */
function ArchivedRunsHistory({ runs }: { runs: ArchivedRun[] }) {
  if (!runs.length) return null;
  return (
    <div className="mb-6 space-y-3">
      {[...runs].reverse().map((run) => (
        <ArchivedRunBlock key={run.id} run={run} />
      ))}
    </div>
  );
}

/** Pull "source → target (conf)" lines out of a node snapshot payload for the Process log. */
function summarizeMappingPayload(payload: Record<string, unknown> | null | undefined): string[] {
  if (!payload || typeof payload !== "object") return [];
  const lines: string[] = [];
  const pushMap = (m: unknown, prefix = "") => {
    if (!m || typeof m !== "object") return;
    const r = m as Record<string, unknown>;
    const sf = typeof r.source_field === "string" ? r.source_field : "";
    if (!sf) return;
    const tf = typeof r.target_field === "string" && r.target_field ? r.target_field : "?";
    const conf = typeof r.confidence === "number" ? ` (${r.confidence.toFixed(2)})` : "";
    lines.push(`${prefix}${sf} → ${tf}${conf}`);
  };
  const ribt = payload.review_items_by_table;
  if (ribt && typeof ribt === "object") {
    for (const [tbl, items] of Object.entries(ribt as Record<string, unknown>)) {
      if (Array.isArray(items)) for (const it of items) pushMap(it, `${tbl}.`);
    }
  }
  for (const key of ["final_mappings", "mappings", "tier1_mappings"]) {
    const arr = payload[key];
    if (Array.isArray(arr)) for (const m of arr) pushMap(m);
  }
  return lines;
}

export default function MigrationContent({
  migration,
  migrationId,
  onRefresh,
  onReset,
  showCompletedHistory = true,
  collapseCompletedHistory = false,
  onFieldFocus,
  embeddedRail = false,
  drivePipelineSteps = false,
  onProcessLog,
  viewingVersionId,
  wizard,
}: Props) {
  // When viewing a saved version, snapshots come from the version-scoped key
  // archived by handleSaveVersion in the parent panel. Otherwise the live
  // (migrationId-scoped) key is used. The two-key strategy is what lets the
  // user reopen v1 after "Restart from Node 1" wiped the live key for v2 —
  // v1's archived snapshots remain intact under its own version id.
  const snapshotStorageKey = viewingVersionId
    ? `version:${viewingVersionId}`
    : migrationId;
  const isViewingArchivedVersion = !!viewingVersionId;
  const [snapshotByNode, setSnapshotByNode] = useState<Record<number, NodeSnapshot>>(() =>
    loadSnapshotByNode(snapshotStorageKey),
  );
  const [preSemanticSnapshots, setPreSemanticSnapshots] = useState<PreSemanticSubmittedSnapshot[]>(() =>
    loadPreSemanticHistory(snapshotStorageKey),
  );
  // Discarded runs (from "Restart from Node 1") — always keyed by the live
  // migrationId, not the version key, since restarting is a live-run action.
  const [archivedRuns, setArchivedRuns] = useState<ArchivedRun[]>(() =>
    loadArchivedRuns(migrationId),
  );
  const [stickyGate, setStickyGate] = useState<StickyGateState | null>(null);
  const [fieldMappingGateDismissed, setFieldMappingGateDismissed] = useState(false);
  const [semanticMappingDismissed, setSemanticMappingDismissed] = useState(() =>
    snapshotStorageKey ? isSemanticDismissed(snapshotStorageKey) : false,
  );
  const [autoAdvanceError, setAutoAdvanceError] = useState<string | null>(null);
  const [autoFinalizeError, setAutoFinalizeError] = useState<string | null>(null);
  const [rerunMsg, setRerunMsg] = useState<string | null>(null);
  // Gate-transition overlay state.
  // When a gate handler fires (Continue / Approve / Confirm / etc.) the
  // backend mutation often resolves faster than the next status poll,
  // creating a 2–5s window where the gate's button has stopped spinning
  // but the panel is still showing the old gate. This state bridges that
  // gap with an immediate "Transitioning to next step…" overlay so users
  // never see a blank-feeling moment. Cleared automatically when the
  // polled migration reflects a new gate / step (the comparison snapshot
  // is captured at the moment the gate was submitted).
  type GateTransitionMark = {
    label: string;
    startedAt: number;
    fromStep: number;
    fromGate: string;
    fromStatus: string;
  };
  const [gateTransition, setGateTransition] = useState<GateTransitionMark | null>(null);
  // Hard cap on how long the transition overlay may show before we fall back to the real migration
  // state. Legit advances clear it far sooner via movedPastGate; this only rescues a stuck/re-opened
  // gate. Above the ~2-3s table-routing re-emit window, so it never truncates the anti-flash guard.
  const GATE_TRANSITION_SAFETY_MS = 12_000;

  // Restart-from-Node-1 transition guard.
  // After the user clicks Restart from Node 1 the backend rewinds the migration
  // asynchronously. Until the next poll lands we'd otherwise keep rendering
  // the cached `ResultsPanel` (the "Migration complete" card with downloads)
  // because the migration response still says status==="complete". This flag
  // masks that panel and the on-disk snapshots until the polled status
  // genuinely drops back to a non-terminal state (or the rewind times out).
  const [restartingPipeline, setRestartingPipeline] = useState(false);
  // Track the migration "version key" we restarted from so we know when the
  // backend has produced a fresh terminal-state reset.
  const restartStartedAtRef = useRef<number>(0);
  // Completed-steps history is collapsed by default only when viewing an OLDER saved
  // version (so the live run shows all its steps). The user can toggle it any time.
  const [historyCollapsed, setHistoryCollapsed] = useState(collapseCompletedHistory);
  const lastAutoAdvanceKeyRef = useRef<string | null>(null);
  const lastAutoFinalizeKeyRef = useRef<string | null>(null);
  // Bring the active step/gate into view as the pipeline advances — but only the
  // minimum amount ("nearest"), so the completed/previous node cards stay visible
  // above instead of being pushed off-screen.
  const activeStepRef = useRef<HTMLDivElement | null>(null);
  // Cross-panel stage link for the PERSISTENT "Table & Column Analysis" section shown above every
  // step so the B7.1→B21.1 panels stay visible (and keep auto-linking) instead of vanishing.
  // Scroll-spy root = the WHOLE migration panel (attached to wrapPipelineStep's outer div below),
  // so the cross-panel link fires for EVERY step card — the Table & Column Analysis section AND the
  // completed snapshot cards for nodes 3-9 — not just the analysis section. (Previously rooted on
  // the analysis card alone, so the log stopped following the panel after step 3.)
  const panelStepsRef = useRef<HTMLDivElement | null>(null);
  usePipelineStageScrollSpy(panelStepsRef);
  // Keep the right-rail deterministic/semantic table-mapping log from running AHEAD of the center:
  // hide those stages while the pipeline is RUNNING before the pre-semantic gate (the center only
  // shows the deterministic/semantic RESULT at that gate). Reveal once the gate is reached — or in
  // any non-running state — and restore on unmount so other views aren't affected.
  useEffect(() => {
    if (!migration) return;
    const st = String(migration.status ?? "").toLowerCase();
    // Robust signal: the center produces the deterministic RESULT at node 3 (pre-semantic). Once
    // that data exists — or we're paused at a gate / past it / terminal — ALWAYS reveal, so the
    // log is never left hidden at a LATER step (preprocess, hierarchy, output). Only hide while
    // still RUNNING before that result exists (node 1–2), where the center hasn't shown it yet.
    const tr = migration.udr_table_resolution;
    const hasDeterministic = Array.isArray(tr?.deterministic) && tr.deterministic.length > 0;
    setTableStagesRevealed(hasDeterministic || st !== "running");
    // Column-mapping stages (2.6-2.12) mirror the pre-semantic gate's Step-2 "column matching". While
    // the run is parked AT that gate, gate-pre-semantic owns their reveal (phase tables->columns), so
    // we don't touch the flag here. When NOT at that gate, reveal iff the run is PAST node 3
    // (current_step > 3, which includes completed runs) — so a fresh load / tab-switch into a later
    // step shows them read-only, while a run at/before the gate keeps them hidden. Paired with the
    // default-false module flag, this closes the pre-mount "log leads the panel" leak (CAFM-001).
    if (!isPreSemanticGatePending(migration)) {
      setColumnStagesRevealed((migration.current_step ?? 0) > 3);
    }
    // NOTE: no cleanup here. This effect re-runs on every poll (deps: migration); a cleanup that
    // reset the flags would fire each poll and re-REVEAL the column-mapping stages that
    // gate-pre-semantic hid on Step 1 — its effect only re-runs on `phase`, not per poll, so it
    // wouldn't re-hide them. The unmount-restore lives in its own []-deps effect below so it runs
    // ONCE on unmount, never between polls.
  }, [migration]);
  // Restore the gated flags to visible on UNMOUNT only — so a completed run / other view is never
  // left with the table/column-mapping stages hidden. []-deps → runs once on unmount, not per poll.
  useEffect(() => {
    return () => {
      setTableStagesRevealed(true);
      setColumnStagesRevealed(true);
    };
  }, []);
  useEffect(() => {
    const t = setTimeout(() => {
      // Bring the ACTIVE/new step to the top of the view; completed steps end up
      // above it, so the user scrolls UP to review them. Also re-run when the transition
      // loader toggles, so the "Migration running…" card is brought into view the instant it
      // appears instead of leaving the user parked on a blank stretch of the panel.
      activeStepRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 80);
    return () => clearTimeout(t);
  }, [migration?.pending_gate_type, migration?.status, migration?.current_step, gateTransition]);

  // Click a pipeline step to scroll to / view it (completed snapshot or the live step).
  // Rewind the pipeline to a completed step and re-run forward (re-fires its gate to edit).
  const handleRerunStep = async (nodeNum?: number) => {
    if (nodeNum == null) return;
    setRerunMsg(`Re-running from step ${nodeNum}… the step's review will re-open for edits.`);
    if (nodeNum === 1) {
      // Hard reset: hide the complete panel, wipe the in-memory snapshots so
      // the new run starts clean, and lock the restart guard until the next
      // poll confirms a non-terminal state. The 60s timeout is a safety net
      // for the rare case where the rewind never lands.
      setRestartingPipeline(true);
      restartStartedAtRef.current = Date.now();
      // Archive the run we're about to discard so its finished cards stay
      // reviewable as a collapsed "Previous run" block, instead of being
      // silently overwritten by the re-run (which reuses this migration_id).
      const archivedAt = restartStartedAtRef.current;
      const snapsToArchive = Object.values(snapshotByNode);
      const finalResults = buildArchivedFinalResults(migration);
      if (snapsToArchive.length > 0 || preSemanticSnapshots.length > 0 || finalResults) {
        setArchivedRuns((prev) => {
          const entry: ArchivedRun = {
            id: `run_${archivedAt}`,
            archivedAt,
            snapshots: snapsToArchive,
            preSemantic: preSemanticSnapshots,
            finalResults,
          };
          const next = [...prev, entry].slice(-ARCHIVED_RUNS_CAP);
          saveArchivedRuns(migrationId, next);
          return next;
        });
      }
      setSnapshotByNode({});
      setPreSemanticSnapshots([]);
      setStickyGate(null);
      setFieldMappingGateDismissed(false);
      // Restart begins a fresh LIVE run — expand the completed-steps history
      // so each new node's output card is visible the moment it lands.
      // Forcing collapsed here was wrong: collapsed is only the right default
      // when reviewing an older saved version, not when watching a live run
      // unfold from scratch. Users reported "not showing all node details"
      // because every new snapshot was hidden behind the collapse toggle.
      setHistoryCollapsed(false);
      // Also clear the per-migration sessionStorage flags that drive the
      // semantic / field-mapping gate skip logic. Without this, the new run
      // would silently skip the pre-semantic gate (because the prior run had
      // dismissed it) and the user would see the renderer fall through to a
      // blank state at Node 2.
      setSemanticMappingDismissed(false);
      clearSemanticDismissed(migrationId);
      clearFieldMappingDraft(migrationId);
    }
    try {
      await schemaMapperApi.rerunMigrationFromNode(migrationId, nodeNum);
      onRefresh();
      setTimeout(() => setRerunMsg(null), 5000);
    } catch (e) {
      setRerunMsg(e instanceof Error ? e.message : "Re-run failed.");
      if (nodeNum === 1) {
        // Roll back the guard if the backend rejected the restart so we don't
        // strand the user on a permanent "restarting" screen.
        setRestartingPipeline(false);
      }
    }
  };

  // Clear the restart guard once the backend confirms a fresh non-terminal
  // status (the rewind has taken effect). 60s safety timeout falls through.
  useEffect(() => {
    if (!restartingPipeline) return;
    const st = (migration?.status ?? "").toLowerCase();
    const terminal = st === "complete" || st === "failed" || st === "ddl_failed" || st === "cancelled";
    if (!terminal && st) {
      setRestartingPipeline(false);
      return;
    }
    const elapsed = Date.now() - restartStartedAtRef.current;
    if (elapsed > 60_000) {
      setRestartingPipeline(false);
    }
  }, [restartingPipeline, migration?.status, migration?.current_step]);

  useEffect(() => {
    // Rehydrate from localStorage instead of wiping — survives panel unmount, tab
    // switch AND tab close/reopen (so e.g. "Data preprocessing" metrics stay
    // populated when the backend's later responses no longer echo the older node
    // outputs, and a completed run's per-step cards stay reviewable next session).
    setSnapshotByNode(loadSnapshotByNode(snapshotStorageKey));
    setPreSemanticSnapshots(loadPreSemanticHistory(snapshotStorageKey));
    // Archived (discarded) runs are tied to the live migrationId regardless of
    // which version is being viewed.
    setArchivedRuns(loadArchivedRuns(migrationId));
    setStickyGate(null);
    setFieldMappingGateDismissed(false);
    setHistoryCollapsed(collapseCompletedHistory);
    // Version-scoped: an archived version reads its OWN dismissed flag (under
    // version:<id>), not the live migration's, so review state never leaks across
    // versions. For the live run snapshotStorageKey === migrationId (no change).
    setSemanticMappingDismissed(isSemanticDismissed(snapshotStorageKey));
    setAutoFinalizeError(null);
  }, [snapshotStorageKey, migrationId, collapseCompletedHistory]);

  useEffect(() => {
    // Writes are suppressed when the user is reviewing a saved version — the
    // archived snapshots under `version:<id>` are immutable once the user
    // saved that version, and writing to the live key here would let an
    // archived view's stale state trample the in-flight run.
    if (isViewingArchivedVersion) return;
    savePreSemanticHistory(migrationId, preSemanticSnapshots);
  }, [migrationId, preSemanticSnapshots, isViewingArchivedVersion]);

  useEffect(() => {
    if (isViewingArchivedVersion) return;
    saveSnapshotByNode(migrationId, snapshotByNode);
  }, [migrationId, snapshotByNode, isViewingArchivedVersion]);

  useEffect(() => {
    // Archived runs persist by live migrationId. Safe to write while viewing a
    // version (it's a distinct key and the list only changes on restart).
    saveArchivedRuns(migrationId, archivedRuns);
  }, [migrationId, archivedRuns]);

  useEffect(() => {
    if (!migration || semanticMappingDismissed) return;
    if (isPipelinePastFieldMappingGate(migration)) {
      markSemanticDismissed(migrationId);
      setSemanticMappingDismissed(true);
    }
  }, [migration, migrationId, semanticMappingDismissed]);

  useEffect(() => {
    if (!migration) return;
    // While a Restart-from-Node-1 is in flight the backend may still echo the
    // pre-restart terminal state for a poll or two. Capturing it here would
    // re-populate the snapshots we just wiped (and resurrect the discarded
    // run's node cards in the new run's live history). Skip until the rewind
    // lands and `restartingPipeline` clears — then this effect re-runs.
    if (restartingPipeline) return;

    setSnapshotByNode((prev) => {
      const next: Record<number, NodeSnapshot> = { ...prev };
      let changed = false;
      const upsertSnapshot = (incoming: NodeSnapshot) => {
        const prevSnap = next[incoming.nodeId];
        if (!prevSnap) {
          next[incoming.nodeId] = incoming;
          changed = true;
          return;
        }
        // Merge payloads — when the backend re-emits a node with a sparser
        // payload (e.g. only logs/metadata), keep the previously-populated
        // metric fields rather than replacing them with nulls/zeros.
        const mergedPayload = mergeSnapshotPayloads(prevSnap.payload, incoming.payload);
        const isSame =
          prevSnap.stepKey === incoming.stepKey &&
          prevSnap.nodeName === incoming.nodeName &&
          JSON.stringify(prevSnap.payload) === JSON.stringify(mergedPayload);
        if (isSame) return;
        // Only commit if the merged result is at least as rich as what we had.
        const prevScore = payloadRichnessScore(prevSnap.payload);
        const mergedScore = payloadRichnessScore(mergedPayload);
        if (mergedScore >= prevScore) {
          next[incoming.nodeId] = {
            ...incoming,
            payload: mergedPayload,
            nodeName: incoming.nodeName ?? prevSnap.nodeName,
          };
          changed = true;
        }
      };

      for (const n of migration.nodes ?? []) {
        const stepKey = NODE_STEP_KEYS[n.node_id];
        if (!stepKey) continue;

        let payload: Record<string, unknown> | null = null;
        if (n.output && isRecord(n.output)) {
          payload = n.output;
        } else if (n.node_id === 1 && Array.isArray(n.logs) && n.logs.length) {
          payload = extractIngestPayloadFromLogs(n.logs);
        }

        if (!payload) continue;

        const incoming: NodeSnapshot = {
          nodeId: n.node_id,
          stepKey,
          payload,
          nodeName: NODE_TITLES[n.node_id] ?? n.node_name,
        };
        upsertSnapshot(incoming);
      }

      if (
        migration.status === "step_paused" &&
        typeof migration.pending_gate_type === "string" &&
        isRecord(migration.pending_gate_payload) &&
        !isStepPauseBlockingFieldMapping(migration, semanticMappingDismissed)
      ) {
        const normalizedStepKey = normalizeStepKeyForHistory(migration.pending_gate_type);
        const nodeId = STEP_KEY_TO_NODE[normalizedStepKey];
        if (typeof nodeId === "number") {
          const incoming: NodeSnapshot = {
            nodeId,
            stepKey: normalizedStepKey,
            payload: migration.pending_gate_payload,
            nodeName:
              NODE_TITLES[nodeId] ??
              (migration.nodes ?? []).find((n) => n.node_id === nodeId)?.node_name,
          };
          upsertSnapshot(incoming);
        }
      }

      return changed ? next : prev;
    });
  }, [migration, fieldMappingGateDismissed, semanticMappingDismissed, restartingPipeline]);

  // Stream per-node field-mapping decisions into the right-side Process log, tagged to
  // the migration flow — "<table>.<source> → <target> (conf)" for each mapped field.
  const loggedNodeKeysRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!onProcessLog || !migration) return;
    const nodes = Array.isArray(migration.nodes) ? migration.nodes : [];
    for (const n of nodes) {
      const rawLogs = Array.isArray(n.logs)
        ? n.logs.filter((l): l is string => typeof l === "string" && l.trim().length > 0)
        : [];
      // Prefer the node's own raw log lines; fall back to a mapping summary from its snapshot.
      const lines = rawLogs.length ? rawLogs : summarizeMappingPayload(snapshotByNode[n.node_id]?.payload);
      if (!lines.length) continue;
      const label = NODE_TITLES[n.node_id] ?? n.node_name ?? `Step ${n.node_id}`;
      const key = `${migrationId}:${n.node_id}:${lines.length}`;
      if (loggedNodeKeysRef.current.has(key)) continue;
      loggedNodeKeysRef.current.add(key);
      onProcessLog({
        phase: "completed",
        tool: "migration_node",
        toolLabel: label,
        status: "success",
        title: label,
        detail: `${lines.length} log line${lines.length === 1 ? "" : "s"}`,
        output: lines.join("\n"),
        spaceTag: "migration",
      });
    }
  }, [migration, snapshotByNode, migrationId, onProcessLog]);

  // Is the Step-1 "Confirm table routing" gate the one currently open?
  const routingGatePending = (() => {
    if (String(migration?.pending_gate_type ?? "").toLowerCase() !== "pre_semantic") return false;
    const p = isRecord(migration?.pending_gate_payload)
      ? (migration.pending_gate_payload as Record<string, unknown>)
      : null;
    if (!p) return false;
    return (
      String(p.locked_phase ?? "") === "tables" || String(p.gate_step ?? "") === "table_routing"
    );
  })();

  const historySnapshots = useMemo(
    () =>
      Object.values(snapshotByNode)
        .filter((s) => {
          // The pre-semantic PASS 1 (table-routing gate) emits a node-3 snapshot ONLY to carry the
          // analysis so the 2.x cards stay visible — it is NOT the completed pre-semantic step, so
          // hide it as a "Pre-semantic review gate — 0 Approved / 0 Sent" card. The real completed
          // snapshot arrives at pass 2 (after column matching).
          const p = s.payload as { pre_semantic_routing_pass?: boolean } | undefined;
          if (s.nodeId === 3 && p?.pre_semantic_routing_pass === true) return false;
          // Node 2 is deterministic TIER-1 COLUMN matching. Until the destination table is
          // confirmed, presenting it as a completed step puts column results ahead of the routing
          // decision they depend on. Hide it while the routing gate is open — it returns to history
          // the moment routing is approved (the pipeline re-matches any re-routed table's columns).
          if (routingGatePending && s.nodeId === 2) return false;
          return true;
        })
        .sort((a, b) => a.nodeId - b.nodeId),
    [snapshotByNode, routingGatePending],
  );
  const pendingGateTypeRaw = typeof migration?.pending_gate_type === "string" ? migration.pending_gate_type : null;
  const statusRaw = migration?.status ?? null;
  const normalizedPausedStepKey =
    pendingGateTypeRaw ? normalizeStepKeyForHistory(pendingGateTypeRaw) : null;
  const shouldAutoAdvanceNode2 =
    statusRaw === "step_paused" &&
    normalizedPausedStepKey === "step_2_deterministic";
  const shouldAutoAdvanceHierarchyDetection =
    statusRaw === "step_paused" &&
    normalizedPausedStepKey === "step_7_hierarchy";

  const shouldOrchestratorAutoAdvanceStepPause =
    drivePipelineSteps &&
    statusRaw === "step_paused" &&
    !!pendingGateTypeRaw &&
    !isStepPauseBlockingFieldMapping(migration, semanticMappingDismissed) &&
    shouldOrchestratorAutoAdvanceStep(pendingGateTypeRaw, semanticMappingDismissed) &&
    !shouldAutoAdvanceNode2 &&
    !shouldAutoAdvanceHierarchyDetection;

  const { mutate: advanceAfterFieldReview, isPending: _isAdvancingAfterFieldReview } = useMigrationAdvance({
    onSuccess: () => {
      setFieldMappingGateDismissed(true);
      setStickyGate(null);
      setAutoAdvanceError(null);
      onRefresh();
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Failed to continue pipeline";
      setAutoAdvanceError(msg);
    },
  });

  const { mutate: autoAdvanceMigration, isPending: isAutoAdvancingNode2 } = useMigrationAdvance({
    onSuccess: () => {
      setAutoAdvanceError(null);
      onRefresh();
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Failed to continue after deterministic mapping";
      setAutoAdvanceError(msg);
    },
  });

  useEffect(() => {
    if (!shouldAutoAdvanceNode2 && !shouldAutoAdvanceHierarchyDetection && !shouldOrchestratorAutoAdvanceStepPause) {
      return;
    }
    const dedupeKey = `${migrationId}:${pendingGateTypeRaw ?? ""}:${statusRaw ?? ""}`;
    if (lastAutoAdvanceKeyRef.current === dedupeKey) return;
    lastAutoAdvanceKeyRef.current = dedupeKey;
    setAutoAdvanceError(null);
    autoAdvanceMigration({ migrationId });
  }, [
    shouldAutoAdvanceNode2,
    shouldAutoAdvanceHierarchyDetection,
    shouldOrchestratorAutoAdvanceStepPause,
    migrationId,
    pendingGateTypeRaw,
    statusRaw,
    autoAdvanceMigration,
  ]);

  const status = migration?.status ?? null;
  const pending_gate_type = migration?.pending_gate_type ?? null;
  const pending_gate_payload = migration?.pending_gate_payload ?? null;
  const hasOutputArtifacts = !!(
    migration?.output_json_url ||
    migration?.output_csv_url ||
    migration?.output_sql_url ||
    migration?.migration_report_url
  );
  const isGate3RejectFalseFailure =
    status === "failed" &&
    typeof migration?.error_message === "string" &&
    migration.error_message.toLowerCase().includes("customer rejected handoff at gate 3") &&
    hasOutputArtifacts;
  const isEffectivelyComplete = status === "complete" || isGate3RejectFalseFailure;
  // A cancelled migration is terminal: it must NOT fall through to a gate
  // (which would offer Resume/Approve). It keeps pending_gate_type, so this
  // flag is checked explicitly before any gate branch renders.
  const isCancelled = status === "cancelled";
  // On completion, tuck the run's full results + steps into the collapsible
  // "Previous run — click to review" section so the active view stays clean
  // (workflow request). One-shot per completion; the user can still expand it.
  const autoCollapsedOnCompleteRef = useRef(false);
  useEffect(() => {
    if (isEffectivelyComplete) {
      if (!autoCollapsedOnCompleteRef.current) {
        autoCollapsedOnCompleteRef.current = true;
        setHistoryCollapsed(true);
      }
    } else {
      autoCollapsedOnCompleteRef.current = false;
    }
  }, [isEffectivelyComplete]);
  const normalizedGateType = normalizeMigrationGateType(typeof pending_gate_type === "string" ? pending_gate_type : null);
  // Hoistra: the write is never confirmed on its own. CAFM Web auto-POSTs {confirmed: true} the
  // moment the service reaches its "write" gate; here that gate shows CAFM's own Gate 3 — Final
  // Confirmation screen (GateFinal, below), and rows are written only when a person presses
  // "Confirm & Write to Platform". The only intended difference from CAFM Web's screens.
  const shouldAutoFinalizeWriteGate = false as boolean;

  const { mutate: autoFinalizeMigration, isPending: isAutoFinalizing } = useMigrationGateFinal({
    onSuccess: () => {
      setAutoFinalizeError(null);
      onRefresh();
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Failed to auto-confirm final handoff";
      setAutoFinalizeError(msg);
    },
  });

  useEffect(() => {
    if (!shouldAutoFinalizeWriteGate) return;
    const dedupeKey = `${migrationId}:${String(pending_gate_type ?? "")}:${String(status ?? "")}`;
    if (lastAutoFinalizeKeyRef.current === dedupeKey) return;
    lastAutoFinalizeKeyRef.current = dedupeKey;
    setAutoFinalizeError(null);
    autoFinalizeMigration({ migrationId, body: { confirmed: true } });
  }, [shouldAutoFinalizeWriteGate, migrationId, pending_gate_type, status, autoFinalizeMigration]);
  const syntheticFieldMappingPayload = migration
    ? (buildFieldMappingPayloadFromMigration(migration) as MigrationFieldMappingGatePayload | null)
    : null;
  const _fieldMappingReviewItemCount = countFieldMappingReviewItems(syntheticFieldMappingPayload);
  const shouldPreferSyntheticFieldMappingGate =
    !fieldMappingGateDismissed &&
    !!syntheticFieldMappingPayload &&
    status === "running" &&
    !pending_gate_type &&
    !pending_gate_payload &&
    (migration?.current_step ?? 0) >= 4;
  const syntheticFieldMappingGate =
    status === "running" &&
    (migration?.current_step ?? 0) <= 5 &&
    !pending_gate_type &&
    !pending_gate_payload &&
    !!syntheticFieldMappingPayload;

  const semanticMappingLatchActive = requiresSemanticMappingLatch(migration, semanticMappingDismissed);
  const fieldMappingLatchActive = requiresFieldMappingLatch(
    migration,
    fieldMappingGateDismissed,
    semanticMappingDismissed,
  );
  const prematurePreprocessPoll = isPrematurePreprocessPoll(
    migration,
    fieldMappingGateDismissed,
    semanticMappingDismissed,
  );
  const semanticMappingPausePayload = (() => {
    if (!migration) return null;
    if (
      status === "step_paused" &&
      isSemanticMappingStepKey(typeof pending_gate_type === "string" ? pending_gate_type : null) &&
      isRecord(pending_gate_payload)
    ) {
      return pending_gate_payload as Record<string, unknown>;
    }
    const semanticNode = findSemanticMappingNode(migration);
    if (semanticNode?.output && isRecord(semanticNode.output)) return semanticNode.output;
    const snap4 = snapshotByNode[semanticNode?.node_id ?? 4]?.payload;
    if (snap4 && isSemanticMappingPausePayload(snap4)) return snap4;
    if (snap4) return snap4;
    return { label: "Semantic Mapping", tier2_auto_mapped: 0, tier2_flagged: 0, unmappable: 0 };
  })();
  const fieldMappingControls = useMemo(
    () => resolveFieldMappingGateControls(migration, fieldMappingGateDismissed, semanticMappingDismissed),
    [migration, fieldMappingGateDismissed, semanticMappingDismissed],
  );
  const fieldMappingSubmitReady = fieldMappingControls.submitReady;
  const fieldMappingSubmitBlocked = fieldMappingSubmitBlockedReason(migration, semanticMappingDismissed);
  const fieldMappingDeferUntilGate = fieldMappingControls.deferProceed;
  const directPendingPayload = unwrapGatePayload(pending_gate_payload);
  const directPendingIsPreprocess =
    prematurePreprocessPoll ||
    (isPreprocessStepPauseKey(typeof pending_gate_type === "string" ? pending_gate_type : null) &&
      isPreprocessPausePayload(pending_gate_payload));
  const draftFetchRemote =
    !directPendingIsPreprocess &&
    !(statusRaw === "step_paused" && isPreprocessStepPauseKey(pendingGateTypeRaw));
  const fieldMappingDraftEnvelope = useFieldMappingDraft(migrationId, migration, {
    fetchRemote: draftFetchRemote,
  });

  const effectiveGatePayload = (() => {
    if (!migration) return null;

    const preSemanticPayload = resolvePreSemanticGatePayload(migration);
    if (preSemanticPayload && isPreSemanticGatePending(migration)) {
      return preSemanticPayload;
    }

    if (fieldMappingLatchActive) {
      if (shouldPreferSyntheticFieldMappingGate && syntheticFieldMappingPayload) {
        return syntheticFieldMappingPayload;
      }
      if (directPendingPayload && isFieldMappingGatePayload(directPendingPayload)) {
        return directPendingPayload;
      }
      const node5 = (migration.nodes ?? []).find((n) => n.node_id === 5);
      const node5Payload = unwrapGatePayload(node5?.output);
      if (node5Payload && isFieldMappingGatePayload(node5Payload)) return node5Payload;
      if (stickyGate?.type === "field_mapping") return stickyGate.payload;
      if (syntheticFieldMappingPayload) return syntheticFieldMappingPayload;
      const snap5 = snapshotByNode[5]?.payload;
      if (snap5 && isFieldMappingGatePayload(snap5)) return snap5;
    }

    if (shouldPreferSyntheticFieldMappingGate && syntheticFieldMappingPayload) return syntheticFieldMappingPayload;
    if (directPendingPayload && !directPendingIsPreprocess) return directPendingPayload;
    if (syntheticFieldMappingPayload) return syntheticFieldMappingPayload;
    if (!normalizedGateType || typeof pending_gate_type !== "string") return null;

    const normalizedStepKey = normalizeStepKeyForHistory(pending_gate_type);
    const expectedNodeId = STEP_KEY_TO_NODE[normalizedStepKey];
    if (typeof expectedNodeId === "number") {
      const expectedNode = (migration.nodes ?? []).find((n) => n.node_id === expectedNodeId);
      const expectedOutput = unwrapGatePayload(expectedNode?.output);
      if (expectedOutput && !(fieldMappingLatchActive && isPreprocessPausePayload(expectedOutput))) {
        return expectedOutput;
      }
      const expectedSnapshot = snapshotByNode[expectedNodeId];
      const expectedSnapshotPayload = unwrapGatePayload(expectedSnapshot?.payload);
      if (
        expectedSnapshotPayload &&
        !(fieldMappingLatchActive && isPreprocessPausePayload(expectedSnapshotPayload))
      ) {
        return expectedSnapshotPayload;
      }
    }

    for (const n of [...(migration.nodes ?? [])].reverse()) {
      const payload = unwrapGatePayload(n.output);
      if (!payload) continue;
      if (fieldMappingLatchActive && isPreprocessPausePayload(payload)) continue;
      if (inferGateTypeFromPayload(payload) === "field_mapping" || !fieldMappingLatchActive) return payload;
    }
    return null;
  })();
  const inferredGateType = inferGateTypeFromPayload(effectiveGatePayload);
  const resolvedGateType = isPreSemanticGatePending(migration)
    ? "pre_semantic"
    : fieldMappingLatchActive
      ? "field_mapping"
      : normalizedGateType ??
        inferredGateType ??
        (shouldPreferSyntheticFieldMappingGate || syntheticFieldMappingGate ? "field_mapping" : null);
  const liveGateType = resolvedGateType as ResolvedGateType | null;
  const liveGatePayload = effectiveGatePayload ?? null;
  const displayGateType = liveGateType ?? stickyGate?.type ?? null;
  const displayGatePayload = liveGateType ? liveGatePayload : (stickyGate?.payload ?? null);

  useEffect(() => {
    if (liveGateType && liveGatePayload) {
      setStickyGate((prev) => {
        if (
          fieldMappingLatchActive &&
          prev?.type === "field_mapping" &&
          liveGateType !== "field_mapping"
        ) {
          return prev;
        }
        if (!prev) return { type: liveGateType, payload: liveGatePayload };
        if (prev.type !== liveGateType) return { type: liveGateType, payload: liveGatePayload };
        if (JSON.stringify(prev.payload) !== JSON.stringify(liveGatePayload)) {
          return { type: liveGateType, payload: liveGatePayload };
        }
        return prev;
      });
      return;
    }
    if (isEffectivelyComplete || isCancelled || status === "failed" || status === "ddl_failed") {
      setStickyGate(null);
    }
  }, [liveGateType, liveGatePayload, status, isEffectivelyComplete, isCancelled, fieldMappingLatchActive]);

  // Hoistra's step-by-step card: each render branch below names the step it shows (the third
  // argument to wrapPipelineStep), wrapPipelineStep records it with the kept step outputs, and
  // this effect hands the record over once per change. No deps: it runs after every render and
  // compares a small signature, so the card only re-renders when something it shows changed.
  const wizardReportRef = useRef<WizardContentState | null>(null);
  const wizardSentRef = useRef<string>("");
  useEffect(() => {
    if (!wizard) return;
    const r = wizardReportRef.current;
    if (!r) return;
    // Content hashes, not presence or length: the column report is rebuilt after a canonical
    // re-match and a snapshot can change without changing size, and a reopened step must show
    // the version the run actually has.
    const sig = JSON.stringify([
      r.active,
      r.snapshots.map((s) => `${s.nodeId}:${s.stepKey}:${wizardHash(JSON.stringify(s.payload))}`),
      r.preSemantic.length,
      wizardHash(JSON.stringify(r.udrTable ?? null)),
      wizardHash(JSON.stringify(r.udrColumn ?? null)),
      r.rerunMsg,
    ]);
    if (sig === wizardSentRef.current) return;
    wizardSentRef.current = sig;
    wizard.onState(r);
  });
  useEffect(() => {
    wizard?.register?.({
      restartFromNode1: () => void handleRerunStep(1),
      rerunFromNode: (n: number) => void handleRerunStep(n),
    });
  });

  if (!migration) {
    wizardReportRef.current = {
      active: { kind: "loading" },
      snapshots: [],
      preSemantic: [],
      udrTable: null,
      udrColumn: null,
      rerunMsg: null,
    };
    return (
      <div className="flex items-center justify-center h-64">
        <div className="flex items-center gap-3 text-slate-500">
          <Loader size={20} className="animate-spin" />
          <span className="text-sm">Loading migration status…</span>
        </div>
      </div>
    );
  }

  const activeStepKey =
    status === "step_paused" && typeof pending_gate_type === "string"
      ? normalizeStepKeyForHistory(pending_gate_type)
      : null;
  const activeNodeId = activeStepKey ? STEP_KEY_TO_NODE[activeStepKey] : undefined;
  const visibleHistorySnapshots =
    typeof activeNodeId === "number"
      ? historySnapshots.filter((snap) => snap.nodeId !== activeNodeId)
      : historySnapshots;
  const orderedHistorySnapshots =
    typeof activeNodeId === "number"
      ? visibleHistorySnapshots.filter((snap) => snap.nodeId < activeNodeId)
      : visibleHistorySnapshots;
  const hasPreSemanticSnapshot = preSemanticSnapshots.length > 0;
  const historyBeforeSemanticGate = orderedHistorySnapshots.filter((snap) => snap.nodeId <= 2);
  const historyAfterSemanticGate = orderedHistorySnapshots.filter((snap) =>
    hasPreSemanticSnapshot ? snap.nodeId > 3 : snap.nodeId > 2,
  );
  // Latch the latest non-empty analysis payload (udr_table_resolution / udr_column_intelligence) so
  // the "Table & Column Analysis" section does NOT flicker/disappear when a mid-run poll transiently
  // omits these LIVE fields during the rapid auto-advance loading phase (the backend can echo a
  // sparse status between the metric-rich ones). Keep the last populated copy and render from it;
  // reset when the migration changes. node_logs is append-only so the data returns — this just
  // bridges the between-poll gaps so completed content stays visible read-only throughout the run.
  const lastUdrTableRef = useRef<MigrationStatusResponse["udr_table_resolution"]>(null);
  const lastUdrColumnRef = useRef<MigrationStatusResponse["udr_column_intelligence"]>(null);
  const analysisLatchMigRef = useRef<string | null>(null);
  if (analysisLatchMigRef.current !== migrationId) {
    analysisLatchMigRef.current = migrationId;
    lastUdrTableRef.current = null;
    lastUdrColumnRef.current = null;
  }
  if (migration?.udr_table_resolution) lastUdrTableRef.current = migration.udr_table_resolution;
  if (migration?.udr_column_intelligence) lastUdrColumnRef.current = migration.udr_column_intelligence;
  const persistedUdrTable = lastUdrTableRef.current;
  const persistedUdrColumn = lastUdrColumnRef.current;

  // Step 2 — the Table & Column Analysis (B7.1→B21.1), rendered from the latched analysis payload.
  // Placed IN the completed-steps flow so the sequence reads top-to-bottom in order.
  //  - PK / unique-table gate open → HIDDEN (those gates render Group A themselves).
  //  - Pre-semantic gate open → show ONLY Group A (phase="pk"); the gate shows Group B (B9.1-B11.1),
  //    so nothing duplicates and the Group-A cards stay visible after PK approval.
  //  - No gate open           → show everything.
  const _gateTypeLc = String(migration?.pending_gate_type ?? "").toLowerCase();
  const isPkApprovalGateOpen =
    _gateTypeLc === "pk_approval" || _gateTypeLc === "unique_table_approval";
  const analysisPhase: "pk" | undefined = isPreSemanticGatePending(migration) ? "pk" : undefined;

  // 2.9 — the classification-approval gate renders INLINE inside the Column-Intelligence panels
  // (right under the B20.1 PK / FK / Shared cards it refers to), NOT as a separate card after the
  // pre-semantic step, which read as an unrelated step-3 confirmation.
  const classificationGatePayload =
    String(migration?.pending_gate_type ?? "").toLowerCase() === "classification_approval" &&
    isRecord(migration?.pending_gate_payload)
      ? (migration.pending_gate_payload as unknown as MigrationClassificationGatePayload)
      : null;
  const classificationGateNode = classificationGatePayload ? (
    <div data-active-gate="classification_approval">
      <GateClassificationApproval
        migrationId={migrationId}
        payload={classificationGatePayload}
        onSubmitted={onRefresh}
      />
    </div>
  ) : null;

  // 2.11 — the LIVE backend column-mapping-approval gate (B14.1). Like the 2.9 classification gate,
  // it renders INLINE inside the Column-Intelligence panels — in the B14.1 · B15.1 "Column metadata
  // + matched destination column" slot — INSTEAD of as a separate "Confirm column mapping" card
  // below the analysis (which read as a duplicate B14.1). The read-only metadata card is suppressed
  // while this gate is open so only ONE B14.1 card shows, with the Keep / Re-target / New column
  // controls in place.
  const columnMappingGatePayload =
    String(migration?.pending_gate_type ?? "").toLowerCase() === "column_mapping_approval" &&
    isRecord(migration?.pending_gate_payload)
      ? (migration.pending_gate_payload as unknown as MigrationColumnMappingGatePayload)
      : null;
  const columnMappingGateNode = columnMappingGatePayload ? (
    <div data-active-gate="column_mapping_approval">
      <GateColumnMappingApproval
        migrationId={migrationId}
        payload={columnMappingGatePayload}
        onSubmitted={onRefresh}
      />
    </div>
  ) : null;

  // The Table & Column Analysis is HISTORY — it must only appear once Group A (PK + unique-table
  // gates) is complete, otherwise the table_resolution latches during the running window BEFORE the
  // PK gate opens and flashes the unique-table cards, which then vanish when the gate takes over.
  // Column intelligence (persistedUdrColumn) is built at the pre-semantic stage, AFTER both Group A
  // gates, so its presence (or the pre-semantic gate being open) is the signal that Group A is done.
  const groupAComplete = !!persistedUdrColumn || isPreSemanticGatePending(migration);
  const analysisSection =
    !isPkApprovalGateOpen &&
    groupAComplete &&
    (persistedUdrTable || persistedUdrColumn) ? (
      <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-5 mb-6">
        <div className="mb-3 flex items-center gap-2">
          <StepNumberBadge n={SEQ_ANALYSIS} />
          <span className="text-sm font-semibold text-slate-800">Table &amp; Column Analysis</span>
        </div>
        {persistedUdrTable ? (
          <TableResolutionPanels report={persistedUdrTable} phase={analysisPhase} />
        ) : null}
        {persistedUdrColumn && !isPreSemanticGatePending(migration) ? (
          <ColumnIntelligencePanels
            report={persistedUdrColumn}
            classificationGate={classificationGateNode}
            columnMappingGate={columnMappingGateNode}
          />
        ) : null}
      </div>
    ) : null;
  const historyBlock = showCompletedHistory ? (
    <>
      {/* Every completed step before the semantic gate (File ingestion + Deterministic mapping)
          stays read-only in history — including while the pre-semantic gate is open. Previously
          the gate hid the Deterministic-mapping card, so it vanished the moment the gate opened. */}
      <CenterNodeHistory snapshots={historyBeforeSemanticGate} allNodes={migration.nodes} />
      {/* While the 2.9 classification gate is open the analysis section is the ACTIVE step (it
          carries the inline approval), so it is rendered in the active panel instead of here —
          otherwise the approve button ends up inside collapsible history and can't be reached. */}
      {classificationGatePayload ? null : analysisSection}
      <CenterPreSemanticHistory snapshots={preSemanticSnapshots} />
      <CenterNodeHistory snapshots={historyAfterSemanticGate} allNodes={migration.nodes} />
    </>
  ) : null;

  // The Table & Column Analysis (B7.1→B21.1) renders INSIDE historyBlock, so it must count as
  // visible history too. Without it the analysis cards vanish whenever the snapshot arrays are
  // momentarily empty — e.g. right after PK approval, before the next gate's snapshot lands.
  const hasVisibleHistory =
    showCompletedHistory &&
    (historyBeforeSemanticGate.length > 0 ||
      preSemanticSnapshots.length > 0 ||
      historyAfterSemanticGate.length > 0 ||
      analysisSection !== null);

  // Live sub-label for the transition overlay. The mark's `label` is the submit-time INTENT
  // ("Applying pre-semantic decisions…") which drifts out of sync as the run advances (or bounces
  // back), so it stops matching the Process Log's current stage. Prefer the ACTUAL current stage:
  // a running node's name, else the current gate/step label. While the run is still sitting exactly
  // where the decision was submitted (backend hasn't picked up the resume yet) keep the reassuring
  // intent label; once it moves, track the real stage so the message always aligns with the process.
  const liveTransitionLabel = ((): string | null => {
    if (!gateTransition) return null;
    const nowStep = migration.current_step ?? 0;
    const nowGate = String(migration.pending_gate_type ?? "");
    if (nowStep === gateTransition.fromStep && nowGate === gateTransition.fromGate) return null;
    const running = (migration.nodes ?? []).find((n) => liveNodeState(n) === "running");
    if (running) {
      const label = MIGRATION_NODE_LABELS[running.node_id] || running.node_name;
      if (label) return `${label}…`;
    }
    const stage = migrationGateLabel(migration);
    return stage ? `${stage}…` : null;
  })();

  const wrapPipelineStep = (panel: React.ReactNode, className?: string, active?: WizardActive) => {
    if (wizard) {
      const chosen: WizardActive = active ?? { kind: "running", node: Math.max(1, migration.current_step ?? 1) };
      wizardReportRef.current = {
        // While the transition loader covers the panel the run is moving on, whatever the
        // last poll still says.
        active: gateTransition ? { kind: "running", node: chosen.node, sub: chosen.sub } : chosen,
        snapshots: historySnapshots,
        preSemantic: preSemanticSnapshots,
        udrTable: persistedUdrTable ?? null,
        udrColumn: persistedUdrColumn ?? null,
        rerunMsg,
      };
    }
    return (
    <div ref={panelStepsRef} className={className ?? (embeddedRail ? "w-full min-w-0" : "w-full min-w-0")}>
      {wizard ? null : (
      <div className="mb-3 flex items-center justify-end">
        <button
          type="button"
          onClick={() => void handleRerunStep(1)}
          title="Discard all progress and re-run the whole migration from step 1 (file ingestion)"
          className="inline-flex shrink-0 items-center gap-1 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-700 hover:bg-amber-100"
        >
          <RotateCcw size={12} /> Restart from step 1
        </button>
      </div>
      )}
      {!wizard && rerunMsg ? (
        <div className="mb-3 rounded-md border border-indigo-100 bg-indigo-50 px-3 py-2 text-[11px] text-indigo-800">
          {rerunMsg}
        </div>
      ) : null}
      {!wizard && showCompletedHistory && archivedRuns.length > 0 ? (
        <ArchivedRunsHistory runs={archivedRuns} />
      ) : null}
      {!wizard && (hasVisibleHistory || (isEffectivelyComplete && showCompletedHistory)) ? (
        <div className="mb-4 pb-4 border-b border-slate-200 space-y-4">
          <button
            type="button"
            onClick={() => setHistoryCollapsed((v) => !v)}
            className="flex items-center gap-1.5 text-xs font-semibold text-slate-500 uppercase tracking-wider hover:text-slate-700 transition-colors"
          >
            {historyCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
            {historyCollapsed
              ? "Previous run — click to review"
              : isEffectivelyComplete
                ? "Previous run — click to collapse"
                : "Completed steps — scroll up to review"}
          </button>
          {!historyCollapsed ? (
            <>
              {historyBlock}
              {/* On completion the run's final outputs (mapping summary,
                  downloads, full table data) live here too, so the active
                  view above stays a slim "Migration complete" banner. */}
              {isEffectivelyComplete ? (
                <ResultsPanel migration={migration} onReset={onReset} hideReset />
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}
      <div id="migration-active-step" ref={activeStepRef} className="scroll-mt-6 relative">
        {/* Optimistic transition loader (UX audit fix). Bridges the gap
            between a gate decision being submitted and the polled state
            reflecting the new step — without this users see ~2-5s of
            apparent inactivity after the button stops spinning. */}
        {gateTransition ? (
          // Render the compact "Migration running…" card IN PLACE OF the (often tall) step panel —
          // NOT as a full-height absolute overlay on top of it. Overlaying inset-0 on a tall gate
          // form vertically-centred the loader, so the user scrolled through a big blank white area
          // to reach it. Rendering it instead keeps the panel out of the layout, so the loader is
          // compact and immediately visible at the top of the step; the just-submitted gate still
          // never flashes because the panel isn't mounted during the transition. Same visual as the
          // running-state card.
          <div
            role="status"
            aria-live="polite"
            className="flex flex-col items-center justify-center gap-4 rounded-xl bg-white border border-slate-200 px-6 py-10 text-center animate-in fade-in duration-200"
          >
            <div className="w-14 h-14 rounded-2xl bg-indigo-50 flex items-center justify-center">
              <Loader size={28} className="text-indigo-500 animate-spin" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-slate-800">Migration running…</h2>
              <p className="text-sm text-slate-500 mt-1">{liveTransitionLabel ?? gateTransition.label}</p>
            </div>
            <div className="w-full max-w-sm h-2 bg-slate-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-indigo-500 rounded-full transition-all duration-500"
                style={{ width: `${migration.progress_pct}%` }}
              />
            </div>
            <p className="text-xs text-slate-400">Auto-refreshing every 2 seconds…</p>
          </div>
        ) : (
          <div>{panel}</div>
        )}
      </div>
    </div>
    );
  };

  const node2OutputPayload = snapshotByNode[2]?.payload ?? null;

  // Stamp a transition mark immediately after a gate decision is submitted.
  // The label is shown verbatim in the overlay so the user sees something
  // workflow-specific (not a generic spinner).
  const beginGateTransition = (label: string) => {
    setGateTransition({
      label,
      startedAt: Date.now(),
      fromStep: migration.current_step ?? 0,
      fromGate: String(migration.pending_gate_type ?? ""),
      fromStatus: String(migration.status ?? ""),
    });
  };

  // Auto-clear the transition overlay when the polled migration genuinely LEAVES the gate we
  // just submitted. A 25s safety timeout falls through so the user is never stranded if the
  // backend silently swallows the advance.
  useEffect(() => {
    if (!gateTransition) return;
    const nowGate = String(migration.pending_gate_type ?? "");
    const nowStatus = String(migration.status ?? "").toLowerCase();
    const nowStep = migration.current_step ?? 0;
    const terminal =
      nowStatus === "complete" ||
      nowStatus === "failed" ||
      nowStatus === "ddl_failed" ||
      nowStatus === "cancelled" ||
      nowStatus === "canceled";
    // For a HITL gate: clear only once the run has advanced to a LATER step, a DIFFERENT gate
    // opened, or it finished. Do NOT clear on a mere status flip or a step going BACKWARD — a
    // table-routing override re-runs deterministic mapping (an earlier step) and briefly
    // re-emits the SAME pre-semantic gate, which would otherwise flash "Step 1 — Confirm table
    // routing" for ~2-3s between clearing this overlay and the run moving on. A generic
    // step-pause (no HITL gate) has no such re-emit, so any step/status change means advanced.
    const movedPastGate = gateTransition.fromGate
      ? nowGate !== ""
        // A gate is pending: we've moved past ONLY if it's a DIFFERENT gate. The SAME gate
        // reappearing is the LangGraph gate node re-executing on resume (write_gate_payload →
        // awaiting_review runs again just before clear_gate_payload flips to running) — a transient
        // re-emit, NOT a real re-open. Keeping the overlay up here is what stops "Step 1 — Confirm
        // table routing" from flashing back in for ~2-3s before Semantic Mapping / the next gate.
        // A genuinely stuck re-open still surfaces via the 12s safety timer below.
        ? nowGate !== gateTransition.fromGate
        // No gate pending (running): moved past once the run advanced to a LATER step.
        : nowStep > gateTransition.fromStep
      : nowStep !== gateTransition.fromStep ||
        nowStatus !== String(gateTransition.fromStatus).toLowerCase();
    if (movedPastGate || terminal) {
      setGateTransition(null);
      // We've genuinely advanced past (or finished) the gate we submitted — also drop any sticky
      // copy of it. Otherwise `displayGateType = liveGateType ?? stickyGate?.type` falls back to the
      // lingering sticky gate and the just-answered gate (e.g. pre-semantic "Step 1 — Confirm table
      // routing") flashes back in for a poll or two before the next step's pause payload arrives —
      // the reported flash. The next gate, if any, re-populates stickyGate from liveGateType.
      setStickyGate(null);
    }
  }, [
    gateTransition,
    migration.current_step,
    migration.pending_gate_type,
    migration.status,
  ]);

  // Safety net — a REAL timer, independent of the poll. The clear effect above only re-runs when
  // current_step / status / pending_gate_type CHANGE. If the backend re-opens the same gate (or
  // stalls), every 2s poll returns identical values, that effect never re-runs, and an inline
  // timeout would never fire — so the "Migration running…" overlay could spin forever (exactly the
  // observed stuck state). This timer fires regardless, falling back to rendering the actual current
  // migration state (e.g. the re-opened gate, which the user can then act on) instead of an infinite
  // loader. Legit transitions clear via movedPastGate in <3s, well before this fires, so it never
  // interferes with the anti-flash behaviour for table-routing re-emits.
  useEffect(() => {
    if (!gateTransition) return;
    const remaining = Math.max(0, GATE_TRANSITION_SAFETY_MS - (Date.now() - gateTransition.startedAt));
    const t = setTimeout(() => setGateTransition(null), remaining);
    return () => clearTimeout(t);
  }, [gateTransition]);

  const handlePreSemanticSubmitted = (snapshot?: {
    gate: "pre_semantic";
    payload: MigrationPreSemanticGatePayload;
    decisions: Record<string, Array<{ source_field: string; decision: "approve" | "semantic" }>>;
  }) => {
    if (snapshot) {
      setPreSemanticSnapshots((prev) => {
        const key = JSON.stringify(snapshot.decisions);
        if (prev.some((p) => JSON.stringify(p.decisions) === key)) return prev;
        return [
          ...prev,
          {
            id: `pre_semantic_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
            payload: snapshot.payload,
            decisions: snapshot.decisions,
          },
        ];
      });
    }
    setStickyGate(null);
    setAutoAdvanceError(null);
    // Phase-agnostic: the pre-semantic gate is now split — the routing gate (pass 1) advances to
    // B20/B14.1, the column-matching gate (pass 2) advances to semantic. "preparing the next step"
    // is correct for both.
    beginGateTransition("Applying decisions… preparing the next step");
    onRefresh();
  };

  const handleFieldMappingSubmitted = () => {
    setFieldMappingGateDismissed(true);
    setStickyGate(null);
    setAutoAdvanceError(null);
    beginGateTransition("Applying field-mapping decisions… preparing the next step");
    onRefresh();
  };

  const handleFieldMappingDeferredProceed = () => {
    setAutoAdvanceError(null);
    advanceAfterFieldReview({ migrationId });
  };

  const handleApplyTier2Continue = async (body: MigrationGateFieldMappingRequest) => {
    setAutoAdvanceError(null);
    const payloadShape = fieldMappingControls.payload ?? buildFieldMappingPayloadFromMigration(migration);
    const flaggedByTable =
      (payloadShape?.flagged_by_table as Record<string, MigrationFlaggedFieldItem[]>) ?? {};
    const unmappedRaw = payloadShape?.unmapped_by_table ?? {};
    const unmappedByTable = isRecord(unmappedRaw) ? unmappedRaw : {};
    const meta = fieldMappingDraftEnvelope?.meta;

    const result = await applyTier2FieldMappingContinue(
      migrationId,
      body,
      flaggedByTable,
      unmappedByTable,
      meta,
    );

    if (result.ok) {
      setSemanticMappingDismissed(true);
      handleFieldMappingSubmitted();
      return;
    }

    setAutoAdvanceError(result.error);
    onRefresh();
  };

  const handleSemanticMappingContinued = (opts?: { fieldMappingSubmitted?: boolean }) => {
    markSemanticDismissed(migrationId);
    setSemanticMappingDismissed(true);
    if (opts?.fieldMappingSubmitted) {
      clearFieldMappingDraft(migrationId);
      setFieldMappingGateDismissed(true);
    }
    setStickyGate(null);
    setAutoAdvanceError(null);
    beginGateTransition(
      opts?.fieldMappingSubmitted
        ? "Applying field-mapping decisions… preparing data preprocessing"
        : "Continuing semantic mapping… preparing the next step",
    );
    onRefresh();
  };

  const handleHierarchySubmitted = () => {
    setStickyGate(null);
    setAutoAdvanceError(null);
    beginGateTransition("Confirming hierarchy… preparing data artefacts");
    onRefresh();
  };

  const handleFinalSubmitted = () => {
    setStickyGate(null);
    setAutoAdvanceError(null);
    beginGateTransition("Finalising migration… writing to your database");
    onRefresh();
  };

  // Group A gates — show the optimistic "Migration running…" loader the instant the user approves,
  // so they don't stare at the old gate for a full poll cycle (~60s) before the next step loads.
  const handlePkSubmitted = () => {
    beginGateTransition("Primary keys confirmed… preparing unique-table identification");
    onRefresh();
  };
  const handleUniqueTableSubmitted = () => {
    beginGateTransition("Unique tables confirmed… preparing table routing / mapping");
    onRefresh();
  };

  // ── Error ──────────────────────────────────────────────────────────────────
  // ── Cancelled (terminal) ────────────────────────────────────────────────────
  // Render a cancelled card BEFORE any gate branch so the user can't resume a
  // cancelled run. Restart from Node 1 (in wrapPipelineStep's header) and New
  // migration are the only forward actions — both start a fresh execution.
  if (isCancelled && !restartingPipeline) {
    return wrapPipelineStep(
      <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
        <div className="flex items-start gap-4">
          <div className="w-10 h-10 rounded-xl bg-slate-100 flex items-center justify-center shrink-0">
            <XCircle size={20} className="text-slate-500" />
          </div>
          <div className="flex-1">
            <h2 className="text-lg font-bold text-slate-900 mb-1">Migration cancelled</h2>
            <p className="text-sm text-slate-500">
              This migration was cancelled and will not continue. Start a new run with
              <span className="font-medium text-slate-700"> New migration</span>, or rewind with
              <span className="font-medium text-slate-700"> Restart from step 1</span> above — both
              begin a fresh execution.
            </p>
            <button
              onClick={onReset}
              className="inline-flex items-center gap-2 mt-4 px-4 py-2 bg-white border border-slate-200 text-slate-700 text-sm font-medium rounded-lg hover:bg-slate-50 transition-colors"
            >
              <RotateCcw size={14} />
              New migration
            </button>
          </div>
        </div>
      </div>,
      "max-w-2xl",
      { kind: "cancelled", node: Math.min(10, Math.max(1, migration.current_step ?? 1)) },
    );
  }

  if ((status === "failed" || status === "ddl_failed") && !isGate3RejectFalseFailure) {
    // Which step failed = the FURTHEST-progressed node (current_step, else the last node that
    // emitted logs). Everything before it succeeded (green), it is the failure point (red), and
    // later steps were never reached (muted) — the panel highlights exactly where the run stopped
    // instead of a bare "Migration failed" with a raw stack trace.
    const failedNode = (() => {
      const fromStep = migration.current_step ?? 0;
      const lastWithLogs = [...(migration.nodes ?? [])]
        .reverse()
        .find((n) => (n.logs?.length ?? 0) > 0);
      return Math.max(fromStep, lastWithLogs?.node_id ?? 0) || 1;
    })();
    const failedName = NODE_TITLES[failedNode] ?? `step ${failedNode}`;
    const totalNodes = 9;
    // The backend /rerun-from/{node} only rewinds to CHECKPOINT nodes {1,2,4,6,7,9} (not gate nodes
    // 3/5/8). If the failure was at a gate, retry from the nearest checkpoint at or before it — that
    // re-runs forward and re-opens the gate — so "Retry from step" is never a dead 400 button.
    const RERUNNABLE_NODES = [1, 2, 4, 6, 7, 9];
    const retryFromNode = RERUNNABLE_NODES.filter((n) => n <= failedNode).pop() ?? 1;
    const retryFromName = NODE_TITLES[retryFromNode] ?? `step ${retryFromNode}`;
    return wrapPipelineStep(
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
          <div className="flex items-start gap-4">
            <div className="w-10 h-10 rounded-xl bg-red-100 flex items-center justify-center shrink-0">
              <XCircle size={20} className="text-red-600" />
            </div>
            <div className="flex-1 min-w-0">
              <h2 className="text-lg font-bold text-slate-900 mb-0.5">
                Migration {status === "ddl_failed" ? "DDL error" : "failed"} at {failedName}
              </h2>
              <p className="text-sm text-slate-500">
                The pipeline stopped at step {failedNode} of {totalNodes}. Earlier steps are
                preserved — retry from the failed step, or restart from the beginning.
              </p>

              {/* Failed-step highlight: completed → failed → not-reached */}
              <ol className="mt-4 space-y-1.5">
                {Array.from({ length: totalNodes }, (_, i) => i + 1).map((n) => {
                  const state = n < failedNode ? "done" : n === failedNode ? "failed" : "pending";
                  return (
                    <li key={n} className="flex items-center gap-2 text-[13px]">
                      {state === "done" ? (
                        <CheckCircle size={14} className="shrink-0 text-emerald-500" />
                      ) : state === "failed" ? (
                        <XCircle size={14} className="shrink-0 text-red-600" />
                      ) : (
                        <span className="h-2.5 w-2.5 shrink-0 rounded-full border border-slate-300" />
                      )}
                      <span
                        className={
                          state === "failed"
                            ? "font-semibold text-red-700"
                            : state === "done"
                              ? "text-slate-700"
                              : "text-slate-400"
                        }
                      >
                        {NODE_TITLES[n] ?? `Step ${n}`}
                      </span>
                      {state === "failed" ? (
                        <span className="rounded-full bg-red-50 px-1.5 py-0.5 text-[10px] font-medium text-red-700 ring-[0.5px] ring-red-200">
                          Failed here
                        </span>
                      ) : null}
                    </li>
                  );
                })}
              </ol>

              {migration.error_message && (
                <pre className="text-sm text-red-700 bg-red-50 rounded-lg p-4 mt-4 overflow-auto whitespace-pre-wrap">
                  {migration.error_message}
                </pre>
              )}

              <div className="mt-4 flex flex-wrap gap-2">
                {retryFromNode > 1 ? (
                  <button
                    onClick={() => void handleRerunStep(retryFromNode)}
                    className="inline-flex items-center gap-2 px-4 py-2 bg-red-600 text-white text-sm font-medium rounded-lg hover:bg-red-700 transition-colors"
                  >
                    <RotateCcw size={14} />
                    Retry from {retryFromName}
                  </button>
                ) : null}
                <button
                  onClick={() => void handleRerunStep(1)}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-white border border-slate-200 text-slate-700 text-sm font-medium rounded-lg hover:bg-slate-50 transition-colors"
                >
                  <RotateCcw size={14} />
                  Restart from step 1
                </button>
                <button
                  onClick={onReset}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-white border border-slate-200 text-slate-700 text-sm font-medium rounded-lg hover:bg-slate-50 transition-colors"
                >
                  New migration
                </button>
              </div>
              {rerunMsg ? <p className="mt-2 text-xs text-slate-500">{rerunMsg}</p> : null}
            </div>
          </div>
        </div>,
      "max-w-2xl",
      { kind: "failed", node: status === "ddl_failed" ? 10 : failedNode },
    );
  }

  // ── Restart in flight ──────────────────────────────────────────────────────
  // Explicit transient panel shown after Restart-from-Node-1 until the polled
  // status drops back to a non-terminal value. Otherwise the user would see
  // either the old complete card or — when later branches don't match the
  // resetting state — a flash of blank canvas while the new run spins up.
  if (restartingPipeline) {
    return wrapPipelineStep(
      <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-8 flex flex-col items-center text-center gap-4">
        <div className="w-14 h-14 rounded-2xl bg-amber-50 flex items-center justify-center">
          <RotateCcw size={28} className="text-amber-600 animate-spin" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-slate-800">Restarting the migration pipeline…</h2>
          <p className="text-sm text-slate-500 mt-1">
            Rewinding to step 1 (file ingestion). The first step will open once the server has reset the run.
          </p>
        </div>
      </div>,
      undefined,
      { kind: "restarting", node: 1 },
    );
  }

  // ── Complete ───────────────────────────────────────────────────────────────
  if (isEffectivelyComplete && !showCompletedHistory) {
    // Embedded/read-only context with no collapsible history — keep the full
    // results card so the outputs are never hidden.
    return wrapPipelineStep(<ResultsPanel migration={migration} onReset={onReset} />, undefined, { kind: "complete", node: 10 });
  }
  if (isEffectivelyComplete) {
    // Active view stays a slim completion banner; the full results (mapping
    // summary, downloads, full table data) + steps are tucked into the
    // collapsible "Previous run — click to review" section above (workflow
    // request to reduce clutter once a run finishes).
    const totalMapped =
      (migration.t1_mapped_count ?? 0) +
      (migration.t2_auto_count ?? 0) +
      (migration.t2_human_count ?? 0);
    const coveragePct =
      (migration.total_fields ?? 0) > 0
        ? Math.min(100, Math.round((totalMapped / migration.total_fields) * 100))
        : 0;
    return wrapPipelineStep(
      <div className="max-w-2xl rounded-xl border border-emerald-200 bg-white shadow-sm p-6">
        <div className="flex items-start gap-3">
          <div className="w-10 h-10 rounded-xl bg-green-50 flex items-center justify-center shrink-0">
            <CheckCircle size={20} className="text-green-500" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold text-slate-900">Migration complete</h2>
            <p className="text-sm text-slate-500 mt-0.5">
              {migration.cmms_name ? `${migration.cmms_name} ` : ""}data mapped and ingested into
              Hoistra · {coveragePct}% coverage · {totalMapped} fields mapped.
            </p>
            <p className="text-xs text-slate-400 mt-2">
              Mapping summary, downloads and full table data are under{" "}
              <span className="font-medium text-slate-500">Previous run — click to review</span>{" "}
              above to keep this view clean.
            </p>
            <div className="mt-3">
              <button
                onClick={onReset}
                className="inline-flex items-center gap-2 px-3 py-1.5 bg-white border border-slate-200 text-slate-700 text-sm font-medium rounded-lg hover:bg-slate-50 transition-colors"
              >
                <RotateCcw size={14} /> New migration
              </button>
            </div>
          </div>
        </div>
      </div>,
      undefined,
      { kind: "complete", node: 10 },
    );
  }

  // Group A step 1 — PK-approval gate (PRIMARY-KEY IDENTIFICATION, B8.1). Runs first; render the
  // focused editable-PK confirmation view.
  if (String(migration?.pending_gate_type ?? "").toLowerCase() === "pk_approval") {
    const pkGatePayload = isRecord(migration?.pending_gate_payload)
      ? (migration.pending_gate_payload as unknown as MigrationPkApprovalGatePayload)
      : null;
    if (pkGatePayload) {
      return wrapPipelineStep(
        <div data-active-gate="pk_approval">
          <GatePkApproval
            migrationId={migrationId}
            payload={pkGatePayload}
            onSubmitted={handlePkSubmitted}
          />
        </div>,
        undefined,
        { kind: "gate", node: 2, sub: "pk" },
      );
    }
  }

  // Group A step 2 — unique-table-approval gate (UNIQUE-TABLE IDENTIFICATION, B7.1 with the
  // confirmed PK). Runs after the PK gate; a mandatory confirm before routing/mapping.
  if (String(migration?.pending_gate_type ?? "").toLowerCase() === "unique_table_approval") {
    const utGatePayload = isRecord(migration?.pending_gate_payload)
      ? (migration.pending_gate_payload as unknown as MigrationUniqueTableGatePayload)
      : null;
    if (utGatePayload) {
      return wrapPipelineStep(
        <div data-active-gate="unique_table_approval">
          <GateUniqueTableApproval
            migrationId={migrationId}
            payload={utGatePayload}
            onSubmitted={handleUniqueTableSubmitted}
          />
        </div>,
        undefined,
        { kind: "gate", node: 2, sub: "unique" },
      );
    }
  }

  // B14.1 column-mapping approval gate — per-column matched-destination approval, runs after B20.
  // The gate has no card of its own: it is injected INLINE into the 2.11 "Column metadata + matched
  // destination column" slot of ColumnIntelligencePanels (see columnMappingGateNode above), so a
  // single B14.1 card shows with the Keep / Re-target / New column controls — no duplicate. If the
  // column-intelligence report is missing there is no such slot, so the gate is appended after
  // whatever analysis exists (never instead of it, or the analysis cards would vanish).
  if (columnMappingGatePayload) {
    return wrapPipelineStep(
      wizard ? (
        // One stop on its own screen: the lookup tables, the column mapping with its gate, and
        // the destination mapping. The analysis that led here is under step 3 in the rail.
        persistedUdrColumn ? (
          <ColumnIntelligencePanels report={persistedUdrColumn} section="mapping" columnMappingGate={columnMappingGateNode} />
        ) : (
          columnMappingGateNode
        )
      ) : (
      <>
        {analysisSection}
        {persistedUdrColumn ? null : columnMappingGateNode}
      </>
      ),
      undefined,
      { kind: "gate", node: 3, sub: "column_mapping" },
    );
  }

  // Classification-approval gate. The gate has no card of its own: it is injected into the 2.9
  // slot of ColumnIntelligencePanels (see classificationGateNode above), between the B20.1 PK /
  // FK / Shared cards and 2.10 Unified column names. The whole analysis section is promoted to
  // the ACTIVE panel here (and suppressed from history) so the approval is always on screen.
  // The gate normally renders in the 2.9 slot INSIDE the column panels. If the
  // column-intelligence report is missing there is no such slot, so it is appended after
  // whatever analysis exists — never instead of it, or the table cards would vanish.
  if (classificationGatePayload) {
    return wrapPipelineStep(
      wizard ? (
        // The keys / shared-attribute decision on its own screen. How the groups were found
        // (B13.1–B19.1) is one click away above it, closed by default so the decision leads.
        <>
          {persistedUdrColumn ? (
            <details className="group mb-4">
              <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[12px] font-medium text-slate-600 hover:text-slate-900 [&::-webkit-details-marker]:hidden">
                <ChevronRight size={14} className="transition-transform group-open:rotate-90" aria-hidden="true" />
                How these groups were found — the column analysis
              </summary>
              <div className="mt-3">
                <ColumnIntelligencePanels report={persistedUdrColumn} section="analysis" />
              </div>
            </details>
          ) : null}
          {persistedUdrColumn ? (
            <ColumnIntelligencePanels report={persistedUdrColumn} section="classification" classificationGate={classificationGateNode} />
          ) : (
            classificationGateNode
          )}
        </>
      ) : (
      <>
        {analysisSection}
        {persistedUdrColumn ? null : classificationGateNode}
      </>
      ),
      undefined,
      { kind: "gate", node: 3, sub: "classification" },
    );
  }

  const preSemanticGatePayload = resolvePreSemanticGatePayload(migration);
  if (preSemanticGatePayload && isPreSemanticGatePending(migration)) {
    return wrapPipelineStep(
      // data-active-gate lets the Activity-Log "Review in migration panel" CTA scroll straight to
      // this blocking review (Confirm table routing) instead of just the panel top (CAFM-007).
      <div data-active-gate="pre_semantic">
        <GatePreSemantic
          migrationId={migrationId}
          payload={preSemanticGatePayload as MigrationPreSemanticGatePayload}
          onSubmitted={handlePreSemanticSubmitted}
          onFieldFocus={(terms) => onFieldFocus?.(terms, 2)}
          node2Output={node2OutputPayload ?? undefined}
          wizard={!!wizard}
        />
      </div>,
      undefined,
      { kind: "gate", node: 3, sub: preSemanticSub(preSemanticGatePayload as Record<string, unknown>) },
    );
  }

  // Semantic Mapping (Tier 2) — must complete before Field Structure when flagged items exist.
  const semanticReviewPayloadForStep = buildFieldMappingPayloadFromMigration(migration);
  const semanticFlaggedCountForStep = countFieldMappingReviewItems(semanticReviewPayloadForStep);
  const needsSemanticStep =
    !isPipelinePastFieldMappingGate(migration) &&
    needsSemanticReviewBeforeFieldMapping(migration, semanticMappingDismissed) &&
    !!semanticReviewPayloadForStep &&
    semanticFlaggedCountForStep > 0;

  if (needsSemanticStep) {
    const semanticStepKey =
      status === "step_paused" &&
      typeof pending_gate_type === "string" &&
      isSemanticMappingStepKey(pending_gate_type)
        ? normalizeStepKeyForHistory(pending_gate_type)
        : "step_4_semantic";
    const lastPreSemantic =
      preSemanticSnapshots.length > 0 ? preSemanticSnapshots[preSemanticSnapshots.length - 1] : undefined;
    const pausePayload =
      semanticMappingPausePayload && Object.keys(semanticMappingPausePayload).length
        ? semanticMappingPausePayload
        : (semanticReviewPayloadForStep as Record<string, unknown>);

    return wrapPipelineStep(
      <SemanticMappingStep
        migrationId={migrationId}
        stepKey={semanticStepKey}
        pausePayload={pausePayload}
        reviewPayload={semanticReviewPayloadForStep}
        onSubmitted={handleSemanticMappingContinued}
        onFieldFocus={(terms) => onFieldFocus?.(terms, 4)}
        t1Snapshot={lastPreSemantic}
        allNodes={migration.nodes}
        embeddedRail={embeddedRail}
      />,
      undefined,
      { kind: "gate", node: 4, sub: "semantic_review" },
    );
  }

  // Semantic step pause with no flagged items — simple continue.
  if (semanticMappingLatchActive && semanticMappingPausePayload) {
    return wrapPipelineStep(
      <StepPause
        migrationId={migrationId}
        stepKey={
          status === "step_paused" &&
          typeof pending_gate_type === "string" &&
          isSemanticMappingStepKey(pending_gate_type)
            ? normalizeStepKeyForHistory(pending_gate_type)
            : "step_4_semantic"
        }
        payload={(semanticMappingPausePayload ?? {}) as Record<string, unknown>}
        onAdvanced={() => handleSemanticMappingContinued()}
        allNodes={migration.nodes}
        fieldMappingDraft={migration.field_mapping_draft}
      />,
      undefined,
      { kind: "pause", node: 4, auto: false },
    );
  }

  // Step pause (e.g. step_5_preprocess) — must finish before Field Structure Review.
  if (
    isStepPauseBlockingFieldMapping(migration, semanticMappingDismissed) &&
    typeof pending_gate_type === "string" &&
    pending_gate_payload
  ) {
    return wrapPipelineStep(
      <StepPause
        migrationId={migrationId}
        stepKey={pending_gate_type}
        payload={(pending_gate_payload ?? {}) as Record<string, unknown>}
        onAdvanced={
          isSemanticMappingStepKey(pending_gate_type)
            ? handleSemanticMappingContinued
            : onRefresh
        }
        allNodes={migration.nodes}
        fieldMappingDraft={migration.field_mapping_draft}
      />,
      undefined,
      {
        kind: "pause",
        node: STEP_KEY_TO_NODE[normalizeStepKeyForHistory(pending_gate_type)] ?? 6,
        // StepPause continues ingestion / deterministic mapping on its own; every later pause waits.
        auto: normalizedPausedStepKey === "step_1_ingest" || normalizedPausedStepKey === "step_2_deterministic",
      },
    );
  }

  // Field Structure Review (GATE 1) — after semantic Continue or awaiting_review.
  if (fieldMappingControls.show && fieldMappingControls.payload) {
    const fmPayload = (
      displayGatePayload && displayGateType === "field_mapping"
        ? displayGatePayload
        : fieldMappingControls.payload
    ) as MigrationFieldMappingGatePayload;
    return wrapPipelineStep(
      <>
        {/* New-column coverage. The column-mapping step (node 3) is an EARLIER estimate; this
            review is the final, more-refined pass, so its "unmapped (create column)" count can be
            lower — some new-column candidates later got a suggested match and moved to Flagged.
            Counts are read the SAME way the gate does (normalizeFieldMappingGatePayload) so they
            match the tabs, and the note is factual (no "N missing" false alarm). */}
        {(() => {
          const dm = migration.udr_column_intelligence?.dest_mapping ?? [];
          if (!dm.length) return null;
          const cmNew = dm.filter((r) => /new/i.test(r.outcome ?? "")).length;
          const cmSuggest = dm.filter((r) => /suggest/i.test(r.outcome ?? "")).length;
          const norm = normalizeFieldMappingGatePayload(
            fmPayload as unknown as Record<string, unknown>,
          );
          const countRows = (byTable?: Record<string, unknown[]>) =>
            Object.values(byTable ?? {}).reduce((n, a) => n + (Array.isArray(a) ? a.length : 0), 0);
          const reviewUnmapped = countRows(norm.unmapped_by_table as Record<string, unknown[]>);
          // New-column candidates NOT in the Unmapped tab now carry a suggested match → they're in
          // Flagged, where reject/override turns them into a new column.
          const inFlagged = Math.max(0, cmNew - reviewUnmapped);
          if (cmNew === 0) return null;
          return (
            <div className="mb-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
              <span className="font-medium">New-column coverage:</span> the column mapping identified{" "}
              {cmNew} new-column candidate{cmNew === 1 ? "" : "s"}. This review offers a
              create-column option for <b>{reviewUnmapped}</b> in the <b>Unmapped</b> tab
              {inFlagged > 0 ? (
                <>
                  ; the other <b>{inFlagged}</b> later matched an existing column and sit in{" "}
                  <b>Flagged</b> — use <b>reject</b> / <b>override</b> there to create them as new
                  columns instead.
                </>
              ) : (
                <> — all accounted for.</>
              )}
              {cmSuggest > 0 ? ` (${cmSuggest} suggested — confirm.)` : ""}
            </div>
          );
        })()}
        <GateFieldMapping
          key={`${migrationId}:${fieldMappingDraftEnvelope?.meta?.savedAt ?? "pending-draft"}`}
          migrationId={migrationId}
          payload={fmPayload}
          fieldMappingDraft={fieldMappingDraftEnvelope}
          onSubmitted={handleFieldMappingSubmitted}
          onReloadStatus={onRefresh}
          pipelineStatus={status}
          submitReady={fieldMappingControls.submitReady}
          submitBlockedReason={fieldMappingSubmitBlocked}
          deferUntilAwaitingReview={fieldMappingControls.deferProceed}
          onDeferredProceed={handleFieldMappingDeferredProceed}
          onApplyTier2Continue={handleApplyTier2Continue}
          embeddedRail={embeddedRail}
          t1Snapshot={
            preSemanticSnapshots.length > 0 ? preSemanticSnapshots[preSemanticSnapshots.length - 1] : undefined
          }
        />
        {autoAdvanceError ? (
          <div className="mt-4 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">
            {autoAdvanceError}
            <button
              type="button"
              onClick={() => void handleApplyTier2Continue(
                fieldMappingDraftEnvelope?.body ??
                  ({ flagged: {}, unmapped: {} } as MigrationGateFieldMappingRequest),
              )}
              className="ml-3 inline-flex items-center gap-1 px-2 py-1 rounded bg-red-600 text-white hover:bg-red-700"
            >
              Retry Apply Tier-2
            </button>
          </div>
        ) : null}
      </>,
      undefined,
      { kind: "gate", node: 5 },
    );
  }

  // Node 2 flow should not require an extra "Next node" pause screen before semantic gate.
  // If pre-semantic review payload is ready, render the gate directly as a single step.
  // Not while the run is RUNNING: after the table-routing answer the service spends minutes
  // building column intelligence (141 s on a 280k-row workbook, 30 Sep 2026) before the next
  // gate, and the sticky copy of the answered gate would otherwise come back once the
  // transition cover expires — as "Confirm column matching" with a red "cannot submit yet"
  // box. A running service shows the running card.
  if (displayGatePayload && displayGateType === "pre_semantic" && status !== "running") {
    return wrapPipelineStep(
        <GatePreSemantic
          migrationId={migrationId}
          payload={effectiveGatePayload as MigrationPreSemanticGatePayload}
          onSubmitted={handlePreSemanticSubmitted}
          onFieldFocus={(terms) => onFieldFocus?.(terms, 2)}
          node2Output={node2OutputPayload ?? undefined}
          wizard={!!wizard}
        />,
        undefined,
        { kind: "gate", node: 3, sub: preSemanticSub(effectiveGatePayload as Record<string, unknown>) },
    );
  }

  // Auto-advance transition states (hide separate pause screen).
  // The Deterministic-mapping (node 2) auto-advance no longer renders its OWN loading card — that
  // deterministic-labelled card alternated with the generic "Migration running…" card across polls
  // and read as a 2-3× flicker. On the happy path it falls through to the single running card below;
  // only a genuine auto-advance ERROR (needs the retry button) still shows a card here.
  if (shouldAutoAdvanceHierarchyDetection || (shouldAutoAdvanceNode2 && autoAdvanceError)) {
    const transitionTitle = shouldAutoAdvanceHierarchyDetection
      ? "Loading hierarchy verification…"
      : "Preparing the review gate…";
    const transitionSubtitle = shouldAutoAdvanceHierarchyDetection
      ? "Hierarchy detection complete — preparing verification gate…"
      : "Preparing the pre-semantic review gate…";
    const retryLabel = shouldAutoAdvanceHierarchyDetection
      ? "Retry continue to verification"
      : "Retry continue";
    return wrapPipelineStep(
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
          <div className="flex items-center gap-3">
            <Loader size={18} className="animate-spin text-indigo-500" />
            <div>
              <div className="text-sm font-semibold text-slate-800">{transitionTitle}</div>
              <div className="text-xs text-slate-500 mt-0.5">{transitionSubtitle}</div>
            </div>
          </div>
          {autoAdvanceError ? (
            <div className="mt-4 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">
              {autoAdvanceError}
              <button
                type="button"
                onClick={() => autoAdvanceMigration({ migrationId })}
                disabled={isAutoAdvancingNode2}
                className="ml-3 inline-flex items-center gap-1 px-2 py-1 rounded bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
              >
                {retryLabel}
              </button>
            </div>
          ) : null}
        </div>,
        undefined,
        { kind: "running", node: shouldAutoAdvanceHierarchyDetection ? 7 : 2 },
    );
  }

  if (shouldAutoFinalizeWriteGate) {
    return wrapPipelineStep(
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
          <div className="flex items-center gap-3">
            <Loader size={18} className="animate-spin text-indigo-500" />
            <div>
              <div className="text-sm font-semibold text-slate-800">Finalizing migration handoff…</div>
              <div className="text-xs text-slate-500 mt-0.5">Auto-confirming final write gate to keep pipeline moving.</div>
            </div>
          </div>
          {autoFinalizeError ? (
            <div className="mt-4 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">
              {autoFinalizeError}
              <button
                type="button"
                onClick={() => autoFinalizeMigration({ migrationId, body: { confirmed: true } })}
                disabled={isAutoFinalizing}
                className="ml-3 inline-flex items-center gap-1 px-2 py-1 rounded bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
              >
                Retry final handoff
              </button>
            </div>
          ) : null}
        </div>,
        undefined,
        { kind: "running", node: 10 },
    );
  }

  // ── Step pause (node finished, user reviews output) ────────────────────────
  // Step keys always start with "step_" (e.g. step_4_semantic). Gate keys are
  // pre_semantic / field_mapping / hierarchy / final_confirmation.
  // Do NOT use displayGateType here — it may be incorrectly inferred from
  // the step_paused payload structure.
  const showHitlGate =
    !!displayGatePayload &&
    !!displayGateType &&
    (status === "awaiting_review" || (fieldMappingLatchActive && displayGateType === "field_mapping"));

  if (fieldMappingLatchActive && displayGateType === "field_mapping" && displayGatePayload) {
    return wrapPipelineStep(
      <>
        <GateFieldMapping
          key={`${migrationId}:${fieldMappingDraftEnvelope?.meta?.savedAt ?? "pending-draft"}`}
          migrationId={migrationId}
          payload={displayGatePayload as MigrationFieldMappingGatePayload}
          fieldMappingDraft={fieldMappingDraftEnvelope}
          onSubmitted={handleFieldMappingSubmitted}
          onReloadStatus={onRefresh}
          pipelineStatus={status}
          submitReady={fieldMappingSubmitReady}
          submitBlockedReason={fieldMappingSubmitBlocked}
          deferUntilAwaitingReview={fieldMappingDeferUntilGate}
          onDeferredProceed={handleFieldMappingDeferredProceed}
          onApplyTier2Continue={handleApplyTier2Continue}
          embeddedRail={embeddedRail}
          t1Snapshot={preSemanticSnapshots.length > 0 ? preSemanticSnapshots[preSemanticSnapshots.length - 1] : undefined}
        />
        {autoAdvanceError ? (
          <div className="mt-4 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">
            {autoAdvanceError}
            <button
              type="button"
              onClick={() => void handleApplyTier2Continue(
                fieldMappingDraftEnvelope?.body ??
                  ({ flagged: {}, unmapped: {} } as MigrationGateFieldMappingRequest),
              )}
              className="ml-3 inline-flex items-center gap-1 px-2 py-1 rounded bg-red-600 text-white hover:bg-red-700"
            >
              Retry Apply Tier-2
            </button>
          </div>
        ) : null}
      </>,
      undefined,
      { kind: "gate", node: 5 },
    );
  }

  if (showHitlGate) {
    if (displayGateType === "pre_semantic") {
      return wrapPipelineStep(
        <GatePreSemantic
          migrationId={migrationId}
          payload={displayGatePayload as MigrationPreSemanticGatePayload}
          onSubmitted={handlePreSemanticSubmitted}
          onFieldFocus={(terms) => onFieldFocus?.(terms, 2)}
          node2Output={node2OutputPayload ?? undefined}
          wizard={!!wizard}
        />,
        undefined,
        { kind: "gate", node: 3, sub: preSemanticSub(displayGatePayload as Record<string, unknown>) },
      );
    }
    if (displayGateType === "field_mapping") {
      return wrapPipelineStep(
        <GateFieldMapping
          key={`${migrationId}:${fieldMappingDraftEnvelope?.meta?.savedAt ?? "pending-draft"}`}
          migrationId={migrationId}
          payload={displayGatePayload as MigrationFieldMappingGatePayload}
          fieldMappingDraft={fieldMappingDraftEnvelope}
          onSubmitted={handleFieldMappingSubmitted}
          onReloadStatus={onRefresh}
          pipelineStatus={status}
          submitReady={fieldMappingSubmitReady}
          submitBlockedReason={fieldMappingSubmitBlocked}
          deferUntilAwaitingReview={fieldMappingDeferUntilGate}
          onDeferredProceed={handleFieldMappingDeferredProceed}
          onApplyTier2Continue={handleApplyTier2Continue}
          embeddedRail={embeddedRail}
          t1Snapshot={preSemanticSnapshots.length > 0 ? preSemanticSnapshots[preSemanticSnapshots.length - 1] : undefined}
        />,
        undefined,
        { kind: "gate", node: 5 },
      );
    }
    if (displayGateType === "hierarchy") {
      // detectionSnapshot used to be passed here so the gate could render the
      // step-7 snapshot inline, but that produced a duplicate of the
      // "Hierarchy detection" card already shown in the Completed Steps block
      // above the gate. The gate now focuses on the confirm/reject UI.
      return wrapPipelineStep(
        <GateHierarchy
          migrationId={migrationId}
          payload={displayGatePayload as MigrationHierarchyGatePayload}
          onSubmitted={handleHierarchySubmitted}
          pipelineStatus={status}
        />,
        undefined,
        { kind: "gate", node: 8 },
      );
    }
    if (displayGateType === "final_confirmation") {
      return wrapPipelineStep(
        <GateFinal
          migrationId={migrationId}
          payload={displayGatePayload as MigrationFinalGatePayload}
          onSubmitted={handleFinalSubmitted}
          onReset={onReset}
          migrationName={migration.cmms_name}
          pipelineStatus={status}
        />,
        undefined,
        { kind: "gate", node: 10 },
      );
    }
  }

  if (status === "awaiting_review" && pending_gate_type) {
    return wrapPipelineStep(
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
          <h2 className="text-base font-bold text-slate-800 mb-2">
            Awaiting review:{" "}
            <code className="font-mono text-indigo-600">{pending_gate_type}</code>
          </h2>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <GatePayloadViewer value={pending_gate_payload} />
          </div>
        </div>,
        undefined,
        { kind: "gate", node: Math.min(10, Math.max(1, migration.current_step ?? 1)), sub: "unknown" },
    );
  }

  const displayCurrentStep = (() => {
    // Track the FURTHEST-PROGRESSED evidence so the running hint never lags the Activity Log, which
    // derives its stages from the node logs. `current_step` can trail a node that has already
    // started/emitted logs; a running node or a node that already produced logs is stronger evidence
    // of the true stage. Take the max so the panel's stage matches the log's granularity (this is
    // the fix for "log shows Deterministic Mapping while the panel still says File Ingestion").
    const fromStep = migration.current_step ?? 0;
    const runningNode = (migration.nodes ?? []).find((n) =>
      String(n.status ?? "").toLowerCase().includes("running"),
    );
    const lastWithLogs = [...(migration.nodes ?? [])].reverse().find((n) => (n.logs?.length ?? 0) > 0);
    return Math.max(fromStep, runningNode?.node_id ?? 0, lastWithLogs?.node_id ?? 0);
  })();

  if (prematurePreprocessPoll) {
    return wrapPipelineStep(
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
          <div className="flex items-center gap-3">
            <Loader size={18} className="animate-spin text-indigo-500" />
            <div>
              <div className="text-sm font-semibold text-slate-800">Preparing field mapping review…</div>
              <div className="text-xs text-slate-500 mt-0.5">
                The pipeline reported preprocess early — waiting for the field mapping gate before you can continue.
              </div>
            </div>
          </div>
        </div>,
        undefined,
        { kind: "running", node: 5 },
    );
  }

  const pendingSemanticDraft = fieldMappingDraftEnvelope?.body ?? null;
  // The Tier-2 "Applying decisions…" banner is supposed to flash for the
  // brief window between the pre-semantic gate and the field-mapping gate.
  // We also exit early when the pipeline has already produced terminal
  // artefacts or moved past the field-mapping gate — otherwise the banner
  // sticks around even though the work is done (the bug user reported).
  //   - hasOutputArtifacts / isEffectivelyComplete are computed near the
  //     top of the renderer (around line 893–904)
  //   - past Node 6 (preprocess) the field-mapping gate has already opened
  //     or been auto-submitted, so there's nothing more to "apply"
  const pipelinePastFieldMapping = (migration.current_step ?? 0) >= 6;
  if (
    status === "running" &&
    semanticMappingDismissed &&
    pendingSemanticDraft &&
    !fieldMappingGateDismissed &&
    !isEffectivelyComplete &&
    !hasOutputArtifacts &&
    !pipelinePastFieldMapping
  ) {
    return wrapPipelineStep(
      <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
        <div className="flex items-center gap-3">
          <Loader size={18} className="animate-spin text-indigo-500" />
          <div>
            <div className="text-sm font-semibold text-slate-800">Applying Tier-2 mapping decisions…</div>
            <div className="text-xs text-slate-500 mt-0.5">
              Your table and column choices are saved. The pipeline will open Field Mapping review shortly and submit
              them automatically.
            </div>
          </div>
        </div>
      </div>,
      undefined,
      { kind: "running", node: 5 },
    );
  }

  if (
    status === "step_paused" &&
    typeof pending_gate_type === "string" &&
    pending_gate_payload &&
    !shouldAutoAdvanceNode2 &&
    !shouldAutoAdvanceHierarchyDetection &&
    !shouldOrchestratorAutoAdvanceStepPause &&
    // Node 2 (Deterministic mapping) ALWAYS auto-advances — its only card is the error overlay
    // above. Suppress the StepPause "Deterministic Mapping — Continuing automatically…" card
    // UNCONDITIONALLY, not just when shouldAutoAdvanceNode2 is currently true: the backend re-emits
    // the Node-2 step_paused during the resume/CAS cycle, so on a poll where that re-emit lands
    // before shouldAutoAdvanceNode2 re-latches, this branch would mount the deterministic card for
    // one poll then drop it — the 2-3× flicker. Latching on the normalized step key kills it.
    normalizedPausedStepKey !== "step_2_deterministic" &&
    !isStepPauseBlockingFieldMapping(migration, semanticMappingDismissed)
  ) {
    return wrapPipelineStep(
      <StepPause
        migrationId={migrationId}
        stepKey={normalizeStepKeyForHistory(pending_gate_type)}
        payload={(pending_gate_payload ?? {}) as Record<string, unknown>}
        onAdvanced={onRefresh}
        allNodes={migration.nodes}
        fieldMappingDraft={migration.field_mapping_draft}
      />,
      undefined,
      {
        kind: "pause",
        node: STEP_KEY_TO_NODE[normalizedPausedStepKey ?? ""] ?? Math.max(1, migration.current_step ?? 1),
        // StepPause continues these two on its own (AUTO_ADVANCE_STEP_KEYS in step-pause.tsx).
        auto: normalizedPausedStepKey === "step_1_ingest" || normalizedPausedStepKey === "step_2_deterministic",
      },
    );
  }

  // ── Running ────────────────────────────────────────────────────────────────
  // Which step the service is executing while "running". The backend records current_step
  // only when a node COMPLETES, in its own numbering (1 ingest · 2 deterministic · 3 semantic ·
  // 4 field mapping · 5 preprocess · 6 hierarchy · 7 hierarchy review · 8 output · 9 complete;
  // the pre-semantic block writes nothing), so the node under way is the panel step AFTER the
  // one last completed. The last node with logs is not it: a gate node keeps its logs long
  // after it was answered, which had the rail one step behind through every automatic node.
  const runningNode = (() => {
    const EXECUTING_AFTER_STEP: Record<number, number> = { 0: 1, 1: 2, 2: 3, 3: 5, 4: 6, 5: 7, 6: 8, 7: 9, 8: 10, 9: 10 };
    const step = Math.max(0, Math.min(9, Math.trunc(migration.current_step ?? 0)));
    let n = EXECUTING_AFTER_STEP[step] ?? 1;
    // The pre-semantic block ends with its column-matching pass; once that answer is in, the
    // service is on semantic mapping although current_step still reads 2.
    const pass2Answered = preSemanticSnapshots.some((s) => {
      const p = s.payload as { locked_phase?: unknown; gate_step?: unknown } | undefined;
      return String(p?.locked_phase ?? "") === "columns" || String(p?.gate_step ?? "") === "column_matching";
    });
    if (n === 3 && pass2Answered) n = 4;
    // Field mapping review only opens when semantic mapping flagged something or its overall
    // confidence fell below 0.80 (graph _route_after_semantic); otherwise preprocessing is
    // already under way.
    if (n === 5) {
      const sem = (migration.nodes ?? []).find((x) => x.node_id === 4)?.output as Record<string, unknown> | null | undefined;
      const flaggedRaw = sem?.tier2_flagged;
      const flaggedByTable = sem?.tier2_flagged_by_table;
      const flagged =
        typeof flaggedRaw === "number"
          ? flaggedRaw
          : isRecord(flaggedByTable)
            ? Object.values(flaggedByTable).reduce<number>((acc, v) => acc + (Array.isArray(v) ? v.length : 0), 0)
            : 0;
      const confidence = typeof sem?.overall_confidence === "number" ? sem.overall_confidence : 1;
      if (!(flagged > 0) && confidence >= 0.8) n = 6;
    }
    return Math.min(10, Math.max(1, n));
  })();

  const runningHint = (() => {
    // A real gate is about to open → show the gate-preparation reassurance so the user knows a
    // review is coming (the pre-semantic Tier-1 form).
    if (isPreSemanticGatePending(migration)) {
      return "Preparing Pre-Semantic Review — the Tier-1 review form will open when the gate is ready.";
    }
    // Otherwise NAME THE ACTUAL RUNNING STAGE so the panel stays in step with the Activity Log
    // (File Ingestion → Semantic Mapping → …). EXCEPTION: Deterministic mapping (node 2) is not
    // surfaced in the panel (its card was removed per request) and it auto-advances straight into the
    // pre-semantic gate, so its "in progress" window shows the gate-prep reassurance instead of a
    // "Deterministic Mapping" label — otherwise the panel flickered a deterministic card 2-3 times.
    if (runningNode === 10) return "Writing to the database…";
    const label = runningNode === 2 ? undefined : NODE_LABELS[runningNode];
    if (label) return label;
    if (runningNode <= 3) {
      return "Preparing Pre-Semantic Review — the Tier-1 review form will open when the gate is ready.";
    }
    // No stage label (e.g. between nodes near the hierarchy gate) — keep a sensible fallback.
    if ((migration.current_step ?? 0) >= 7) {
      return "Preparing Hierarchy Review — detected relationships will appear when the gate is ready.";
    }
    return `Processing… · ${Math.round(migration.progress_pct)}% complete`;
  })();

  // The B7.1→B21.1 analysis cards are rendered by the PERSISTENT "Table & Column Analysis" section
  // in wrapPipelineStep (above), on every step including this running one — so the running view never
  // duplicates them. It DOES tail the live node logs below the spinner (LiveNodeLogCard) so the
  // ongoing processing output stays visible during the run instead of a bare "Migration running…".

  return wrapPipelineStep(
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-8 flex flex-col items-center text-center gap-4">
      <div className="w-14 h-14 rounded-2xl bg-indigo-50 flex items-center justify-center">
        <Loader size={28} className="text-indigo-500 animate-spin" />
      </div>
      <div>
        <h2 className="text-lg font-semibold text-slate-800">Migration running…</h2>
        <p className="text-sm text-slate-500 mt-1">{runningHint}</p>
      </div>
      <div className="w-full max-w-sm h-2 bg-slate-100 rounded-full overflow-hidden">
        <div
          className="h-full bg-indigo-500 rounded-full transition-all duration-500"
          style={{ width: `${migration.progress_pct}%` }}
        />
      </div>
      {/* <LiveNodeLogCard nodes={migration.nodes} /> */}
      <p className="text-xs text-slate-400">Auto-refreshing every 2 seconds…</p>
    </div>,
    undefined,
    { kind: "running", node: runningNode },
  );
}
