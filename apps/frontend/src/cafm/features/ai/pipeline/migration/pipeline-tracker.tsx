"use client";
import {
  CheckCircle, Circle, AlertCircle, GitMerge, Loader,
} from "lucide-react";
import type { MigrationStatusResponse } from "../../chat-api";
import {
  isPreprocessStepPauseKey,
  normalizeMigrationGateType,
  requiresFieldMappingLatch,
  requiresSemanticMappingLatch,
  isSemanticMappingStepKey,
} from "./migration-gate-state";

interface Step {
  label: string;
  sublabel: string;
  isGate: boolean;
  gateKey?: string;
  stepKey?: string;
  nodeNum?: number;
}

/** User-facing 7-node pipeline aligned with product spec. */
const STEPS: Step[] = [
  { label: "File ingestion", sublabel: "Node 1 — overall summary", isGate: false, nodeNum: 1, stepKey: "step_1_ingest" },
  { label: "Deterministic mapping", sublabel: "Node 2 — column/table mapping", isGate: false, nodeNum: 2, stepKey: "step_2_deterministic" },
  { label: "Human review gate (Semantic)", sublabel: "Pre-semantic — approve or send to semantic", isGate: true, gateKey: "pre_semantic" },
  { label: "Semantic mapping", sublabel: "Node 3 — AI column/table resolution", isGate: false, nodeNum: 4, stepKey: "step_4_semantic" },
  { label: "Human review gate (Table structure)", sublabel: "Field mapping — flagged & unmapped fields", isGate: true, gateKey: "field_mapping" },
  { label: "Data preprocessing", sublabel: "Node 4 — dup / null / empty checks", isGate: false, nodeNum: 6, stepKey: "step_6_preprocess" },
  { label: "Hierarchy detection", sublabel: "Node 5 — FK & containment", isGate: false, nodeNum: 7, stepKey: "step_7_hierarchy" },
  { label: "Hierarchy confirmation gate", sublabel: "Confirm relationships", isGate: true, gateKey: "hierarchy" },
  { label: "Data artifacts", sublabel: "Node 6 — SQL, CSV, JSON export", isGate: false, nodeNum: 9, stepKey: "step_9_output" },
  { label: "Write to plenum_cafm DB", sublabel: "Node 7 — target database handoff", isGate: true, gateKey: "final_confirmation" },
];

type StepState = "waiting" | "running" | "paused" | "active" | "complete" | "error";

function normalizeStepPauseKey(v: string | null) {
  if (!v) return null;
  const s = v.toLowerCase();
  if (s === "step_3_semantic_mapping" || s === "step_4_semantic_mapping") return "step_4_semantic";
  if (s === "step_5_preprocess" || s === "step_5_preprocess_validate") return "step_6_preprocess";
  if (s === "step_6_data_preprocessing" || s === "step_6_preprocess") return "step_6_preprocess";
  if (s === "step_7_hierarchy_detection" || s === "step_7_hierarchy") return "step_7_hierarchy";
  if (s === "step_8_output_generation" || s === "step_9_output_generation") return "step_9_output";
  return v;
}

function getStepState(step: Step, migration: MigrationStatusResponse): StepState {
  const { status, current_step, pending_gate_type, pending_gate_payload } = migration;
  const normalizedGateType = normalizeMigrationGateType(pending_gate_type);
  const normalizedPauseKey = normalizeStepPauseKey(pending_gate_type);

  if (status === "failed" || status === "ddl_failed") return "error";
  if (status === "complete") return "complete";

  const semanticMappingLatch = requiresSemanticMappingLatch(migration, false);
  const fieldMappingLatch = requiresFieldMappingLatch(migration, false, false);

  if (semanticMappingLatch && step.stepKey === "step_4_semantic") return "paused";
  if (
    semanticMappingLatch &&
    status === "step_paused" &&
    isSemanticMappingStepKey(pending_gate_type)
  ) {
    return "paused";
  }

  if (fieldMappingLatch && step.isGate && step.gateKey === "field_mapping") return "active";
  if (pending_gate_payload && normalizedGateType && step.isGate && step.gateKey === normalizedGateType) {
    return "active";
  }
  if (
    status === "step_paused" &&
    step.stepKey &&
    normalizeStepPauseKey(step.stepKey) === normalizedPauseKey &&
    !(fieldMappingLatch && isPreprocessStepPauseKey(pending_gate_type))
  ) {
    return "paused";
  }

  const node = current_step ?? 0;

  if (step.isGate) {
    const gatePassedNode: Record<string, number> = {
      pre_semantic: 3,
      field_mapping: 6,
      hierarchy: 8,
      final_confirmation: 9,
    };
    const passedAt = gatePassedNode[step.gateKey ?? ""] ?? 999;
    if (fieldMappingLatch && step.gateKey === "field_mapping") return "active";
    if (node > passedAt && !fieldMappingLatch) return "complete";
    return "waiting";
  }

  const n = step.nodeNum ?? 999;
  if (node > n) return "complete";
  if (node === n && status === "running") return "running";
  if (node === n && status === "step_paused") return "paused";
  return "waiting";
}

type PhaseState = "waiting" | "running" | "gate" | "complete" | "error";

interface Phase {
  id: string;
  label: string;
  sublabel: string;
  stepIndices: number[];
  isHitlGate: boolean;
}

const PHASES: Phase[] = [
  { id: "ingestion", label: "File ingestion", sublabel: "Parse file, detect columns, overall summary", stepIndices: [0], isHitlGate: false },
  { id: "deterministic", label: "Deterministic mapping", sublabel: "Rule-based table/column mapping", stepIndices: [1, 2], isHitlGate: false },
  { id: "semantic", label: "Semantic mapping", sublabel: "AI mapping + table structure gate", stepIndices: [3, 4], isHitlGate: false },
  { id: "preprocess", label: "Data preprocessing", sublabel: "Dedup, null handling, validation", stepIndices: [5], isHitlGate: false },
  { id: "hierarchy", label: "Hierarchy", sublabel: "Detection + confirmation gate", stepIndices: [6, 7], isHitlGate: false },
  { id: "artifacts", label: "Data artifacts", sublabel: "SQL, CSV, JSON export files", stepIndices: [8], isHitlGate: false },
  { id: "write", label: "Write to DB", sublabel: "Hand off to plenum_cafm target database", stepIndices: [9], isHitlGate: true },
];

function getPhaseState(phase: Phase, migration: MigrationStatusResponse): PhaseState {
  if (migration.status === "failed" || migration.status === "ddl_failed") return "error";
  const stepStates = phase.stepIndices.map((i) => getStepState(STEPS[i], migration));
  if (stepStates.every((s) => s === "complete")) return "complete";
  if (stepStates.some((s) => s === "active")) return "gate";
  if (stepStates.some((s) => s === "running" || s === "paused")) return "running";
  return "waiting";
}

function PhaseIcon({ state, isHitlGate }: { state: PhaseState; isHitlGate: boolean }) {
  if (state === "complete") return <CheckCircle size={18} className="text-green-500" />;
  if (state === "error") return <AlertCircle size={18} className="text-red-500" />;
  if (state === "running") return <Loader size={18} className="text-indigo-500 animate-spin" />;
  if (state === "gate") return isHitlGate
    ? <GitMerge size={18} className="text-amber-500" />
    : <GitMerge size={18} className="text-blue-500" />;
  return <Circle size={18} className="text-slate-300" />;
}

interface Props {
  migration: MigrationStatusResponse;
  compact?: boolean;
  /** Click a step to scroll to / view it (nodeNum undefined for gate-only steps). */
  onSelectStep?: (nodeNum?: number) => void;
  /** Re-run the pipeline from a completed step (checkpoint rewind). */
  onRerunStep?: (nodeNum?: number) => void;
}

export default function PipelineTracker({ migration, compact, onSelectStep, onRerunStep }: Props) {
  const progressPct = Math.round(migration.progress_pct ?? 0);

  return (
    <div className={compact ? "p-3" : "p-4"}>
      {!compact ? (
        <div className="mb-4">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Migration pipeline</p>
          <p className="text-sm font-semibold text-slate-800 mt-0.5 truncate">{migration.cmms_name}</p>
        </div>
      ) : null}

      <div className="mb-4">
        <div className="flex justify-between text-xs text-slate-500 mb-1">
          <span>Progress</span><span>{progressPct}%</span>
        </div>
        <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-indigo-500 rounded-full transition-all duration-500"
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      <ol className="relative space-y-1">
        {PHASES.map((phase, i) => {
          const state = getPhaseState(phase, migration);
          const reNode = STEPS[phase.stepIndices[0]]?.nodeNum;
          return (
            <li key={phase.id} className="relative">
              {i < PHASES.length - 1 ? (
                <div className="absolute left-[17px] top-9 bottom-0 w-px bg-slate-200 z-0" />
              ) : null}
              <div
                className={`relative z-10 flex gap-3 rounded-xl px-3 py-2.5 transition-colors hover:bg-slate-50 ${
                  state === "gate" ? "bg-amber-50 border border-amber-200" :
                  state === "running" ? "bg-indigo-50 border border-indigo-200" :
                  ""
                }`}
              >
                <button
                  type="button"
                  aria-label={`View ${phase.label}`}
                  title="View this step"
                  onClick={() => onSelectStep?.(STEPS[phase.stepIndices[0]]?.nodeNum)}
                  className="absolute inset-0 z-20 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-300"
                />
                <div className="shrink-0 mt-0.5">
                  <PhaseIcon state={state} isHitlGate={phase.isHitlGate} />
                </div>
                <div className={`flex-1 min-w-0 ${state === "waiting" ? "opacity-40" : ""}`}>
                  <div className="flex items-center gap-2 flex-wrap">
                    <p
                      className={`text-xs font-semibold leading-tight ${
                        state === "gate" ? "text-amber-800" :
                        state === "running" ? "text-indigo-800" :
                        state === "complete" ? "text-slate-700" :
                        state === "error" ? "text-red-700" :
                        "text-slate-500"
                      }`}
                    >
                      {phase.label}
                    </p>
                    {state === "gate" ? (
                      <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-700">
                        Review required
                      </span>
                    ) : null}
                    {state === "running" ? (
                      <span className="text-xs text-indigo-500 font-medium">Processing…</span>
                    ) : null}
                  </div>
                  <p
                    className={`text-xs mt-0.5 ${
                      state === "gate" ? "text-amber-600" :
                      state === "running" ? "text-indigo-500" :
                      "text-slate-400"
                    }`}
                  >
                    {phase.sublabel}
                  </p>
                </div>
                {onRerunStep && state === "complete" && reNode != null ? (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onRerunStep(reNode);
                    }}
                    title="Re-run from this step (re-opens its gate to edit)"
                    className="relative z-30 self-start shrink-0 rounded-md border border-indigo-200 bg-white px-2 py-0.5 text-[10px] font-medium text-indigo-700 hover:bg-indigo-50"
                  >
                    Re-run
                  </button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>

      {!compact ? (
        <div className="mt-5 border-t border-slate-100 pt-4 space-y-2">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Field coverage</p>
          {[
            { label: "T1 mapped", value: migration.t1_mapped_count, color: "text-green-600" },
            { label: "T2 auto", value: migration.t2_auto_count, color: "text-blue-600" },
            { label: "Human", value: migration.t2_human_count, color: "text-amber-600" },
            { label: "Unmapped", value: migration.unmapped_count, color: "text-red-500" },
            { label: "Total", value: migration.total_fields, color: "text-slate-700" },
          ].map(({ label, value, color }) => (
            <div key={label} className="flex justify-between text-xs">
              <span className="text-slate-500">{label}</span>
              <span className={`font-mono font-semibold ${color}`}>{value}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
