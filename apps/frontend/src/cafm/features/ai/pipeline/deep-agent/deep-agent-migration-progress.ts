/**
 * Shared migration progress helpers (used by both the ActiveMigrationCard and
 * the workflow-queue chat card so they stay in lockstep).
 *
 * Lifted out of ActiveMigrationCard so the workflow-queue card can derive the
 * same "Pre-semantic review · Step 5 of 9 · 44%" line that was previously only
 * visible on the duplicate sticky banner.
 */
import type { MigrationStatusResponse, NodeInfo } from "@/features/ai/chat-api";

export const MIGRATION_NODE_LABELS: Record<number, string> = {
  1: "File ingestion",
  2: "Deterministic mapping",
  3: "Pre-semantic review",
  4: "Semantic mapping",
  5: "Field mapping review",
  6: "Data preprocessing",
  7: "Hierarchy detection",
  8: "Hierarchy confirmation",
  9: "Data artifacts",
};

export const MIGRATION_GATE_LABELS: Record<string, string> = {
  pre_semantic: "Pre-semantic review",
  field_mapping: "Field mapping review",
  hierarchy: "Hierarchy confirmation",
  final_confirmation: "Final confirmation",
};

export type MigrationProgress = {
  pct: number | null;
  completed: number;
  total: number;
  step: number | null;
  gateLabel: string | null;
};

export function migrationGateLabel(migration: MigrationStatusResponse): string | null {
  const pending = migration.pending_gate_type;
  if (pending && MIGRATION_GATE_LABELS[pending]) return MIGRATION_GATE_LABELS[pending];
  const step = migration.current_step;
  if (typeof step === "number" && MIGRATION_NODE_LABELS[step]) return MIGRATION_NODE_LABELS[step];
  return null;
}

/**
 * Trust order for progress derivation (the migration backend often leaves
 * ``progress_pct`` at 0 even when nodes are progressing):
 *   1. nodes[].status === "complete" / nodes.length   (most reliable)
 *   2. current_step / total node count                (when nodes[] is empty)
 *   3. server-reported progress_pct                   (only if > 0)
 */
export function computeMigrationProgress(migration: MigrationStatusResponse): MigrationProgress {
  const nodes = migration.nodes ?? [];
  const total = nodes.length;
  const completed = nodes.filter(
    (n) => String(n.status ?? "").toLowerCase() === "complete",
  ).length;

  const step =
    typeof migration.current_step === "number" && migration.current_step > 0
      ? migration.current_step
      : null;
  const gateLabel = migrationGateLabel(migration);

  if (total > 0) {
    return {
      pct: Math.round((completed / total) * 100),
      completed,
      total,
      step,
      gateLabel,
    };
  }
  if (typeof migration.progress_pct === "number" && migration.progress_pct > 0) {
    return { pct: Math.round(migration.progress_pct), completed: 0, total: 0, step, gateLabel };
  }
  return { pct: null, completed: 0, total: 0, step, gateLabel };
}

// ── Live-run sharing between the Activity Log (event feed) and the Process Log
// (progress tracker). Both read the same polled migration status, so the helpers
// that interpret a node/run live state live here, in one place. ──

/** The migration currently loaded / in flight. The Activity Log turns each finished
 *  node into an event card; the Process Log renders it as the live progress tracker. */
export type ActivityLiveRun = {
  /** Active migration id — binds to the current run, not a cached one. */
  migrationId: string;
  /** Source file name for the header. */
  fileName?: string;
  /** The live polled migration status (nodes + progress + gate). */
  status: MigrationStatusResponse;
};

/** Coalesce the backend's node-status spellings into the 3 timeline states.
 *  (Finished nodes from node_logs carry "complete"; the NodeInfo type says
 *  "completed" — accept both, plus "running"/"started" for the in-flight node.) */
export function liveNodeState(n: NodeInfo): "done" | "running" | "pending" {
  const s = String(n.status ?? "").toLowerCase();
  if (s === "complete" || s === "completed" || s === "done") return "done";
  if (s === "running" || s === "started" || s === "in_progress") return "running";
  return "pending";
}

export function fmtNodeDuration(ms: number | null): string | null {
  if (ms == null || ms < 0) return null;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`;
}

/** Run-level status → pill label + classes (Process Log tracker header). */
export function liveRunStatusMeta(status: MigrationStatusResponse): {
  label: string;
  cls: string;
  pulse: boolean;
} {
  const s = String(status.status ?? "").toLowerCase();
  const gate = status.pending_gate_type;
  if (s === "complete")
    return { label: "Completed", cls: "bg-emerald-50 text-emerald-700 border-emerald-200", pulse: false };
  if (s === "failed" || s === "ddl_failed")
    return { label: "Failed", cls: "bg-red-50 text-red-700 border-red-200", pulse: false };
  if (s === "cancelled" || s === "canceled")
    return { label: "Cancelled", cls: "bg-slate-50 text-slate-600 border-slate-200", pulse: false };
  if (s === "awaiting_review" || s === "step_paused" || gate)
    return { label: "Pending human input", cls: "bg-amber-50 text-amber-700 border-amber-200", pulse: true };
  return { label: "Running", cls: "bg-blue-50 text-blue-700 border-blue-200", pulse: true };
}
