"use client";
import { BarChart2, CheckCircle, Database, FileText, RotateCcw } from "lucide-react";
import {
  useMigrationGateFinal,
  useMigrationAdvance,
  schemaMapperApi,
  type MigrationFinalGatePayload,
  type MigrationStatus,
} from "../../../chat-api";
import { useRef, useState } from "react";
import GateFinalPlan from "./gate-final-plan";

interface Props {
  migrationId: string;
  payload: MigrationFinalGatePayload;
  onSubmitted: () => void;
  onReset: () => void;
  migrationName?: string;
  pipelineStatus?: MigrationStatus | null;
}

export default function GateFinal({ migrationId, payload, onSubmitted, onReset, migrationName, pipelineStatus: _pipelineStatus }: Props) {
  const STEP_PAUSED_STATUS_RE = /status\s*:?\s*step_paused/i;
  const FAILED_STATUS_RE = /status\s*:?\s*(failed|ddl_failed)/i;
  const REVIEW_READY_STATUS_RE = /status\s*:?\s*(awaiting_review|running)/i;
  const GATE_MISMATCH_RE = /gate mismatch/i;
  const summary = payload.summary ?? {};
  const [error, setError] = useState<string | null>(null);
  const [isPreflighting, setIsPreflighting] = useState(false);
  const lastSubmitRef = useRef<{ confirmed: boolean } | null>(null);

  const { mutate: submitGate, isPending } = useMigrationGateFinal({
    onSuccess: () => {
      if (lastSubmitRef.current?.confirmed === false) {
        onReset();
        return;
      }
      onSubmitted();
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Submission failed";
      if (GATE_MISMATCH_RE.test(msg)) {
        onSubmitted();
        return;
      }
      setError(msg);
      if (STEP_PAUSED_STATUS_RE.test(msg)) advance({ migrationId });
      if (FAILED_STATUS_RE.test(msg)) onSubmitted();
    },
  });

  const { mutate: advance, isPending: isAdvancing } = useMigrationAdvance({
    onSuccess: () => {
      setError(null);
      if (lastSubmitRef.current) submitGate({ migrationId, body: lastSubmitRef.current });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "unknown";
      if (REVIEW_READY_STATUS_RE.test(msg) && lastSubmitRef.current) {
        submitGate({ migrationId, body: lastSubmitRef.current });
        return;
      }
      setError(`Pipeline advance failed: ${msg}`);
    },
  });

  const overallConfidenceRaw = typeof summary.overall_confidence === "number" ? summary.overall_confidence : null;
  const coveragePctRaw = typeof summary.mapping_coverage_pct === "number" ? summary.mapping_coverage_pct : 0;
  const confidencePct = Math.max(
    0,
    Math.min(
      100,
      Math.round(
        overallConfidenceRaw != null && Number.isFinite(overallConfidenceRaw)
          ? (overallConfidenceRaw <= 1 ? overallConfidenceRaw * 100 : overallConfidenceRaw)
          : coveragePctRaw
      )
    )
  );
  const confidenceColor =
    confidencePct >= 85 ? "text-green-600" : confidencePct >= 70 ? "text-amber-600" : "text-red-600";
  const confidenceBg =
    confidencePct >= 85
      ? "bg-green-50 border-green-200"
      : confidencePct >= 70
        ? "bg-amber-50 border-amber-200"
        : "bg-red-50 border-red-200";
  const barColor =
    confidencePct >= 85 ? "bg-green-500" : confidencePct >= 70 ? "bg-amber-500" : "bg-red-500";

  const totalEntities =
    typeof summary.total_entities === "number" && Number.isFinite(summary.total_entities)
      ? summary.total_entities
      : typeof summary.rows_to_write === "number" && Number.isFinite(summary.rows_to_write)
        ? summary.rows_to_write
        : null;

  const entityCounts = (summary.entity_counts ?? {}) as Record<string, number>;
  const entityCountEntries = Object.entries(entityCounts).filter(([, v]) => typeof v === "number" && Number.isFinite(v));
  const rawFilename = typeof summary.source_filename === "string" && summary.source_filename.trim().length > 0 && summary.source_filename.toLowerCase() !== "unknown" ? summary.source_filename : null;
  const sourceFilename = rawFilename ?? migrationName ?? null;
  const sourceType = summary.source_type ?? null;
  const handleRefreshGate = () => {
    setError(null);
    onSubmitted();
  };

  function submitFinalDecision(confirmed: boolean) {
    const body = { confirmed };
    lastSubmitRef.current = body;
    setError(null);
    setIsPreflighting(true);
    schemaMapperApi
      .getMigrationStatus(migrationId)
      .then((latest) => {
        const latestStatus = latest.status;
        if (latestStatus === "failed" || latestStatus === "ddl_failed" || latestStatus === "cancelled") {
          onSubmitted();
          return;
        }
        if (latestStatus === "step_paused") {
          advance({ migrationId });
          return;
        }
        if (latestStatus !== "awaiting_review") {
          setError(`Cannot submit final confirmation yet. Current migration status is: ${latestStatus}`);
          return;
        }
        submitGate({ migrationId, body });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Failed to verify migration status before submit";
        setError(`${msg}. Please retry to continue safely.`);
        onSubmitted();
      })
      .finally(() => {
        setIsPreflighting(false);
      });
  }

  return (
    <div className="max-w-2xl">
      <div className="flex items-start gap-4 mb-6">
        <div className="w-10 h-10 rounded-xl bg-green-100 flex items-center justify-center shrink-0">
          <Database size={20} className="text-green-600" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-slate-900">Final confirmation</h2>
          <p className="text-sm text-slate-500 mt-0.5">
            Review the migration summary before writing to the platform. This action cannot be undone.
          </p>
        </div>
      </div>

      <div className={`rounded-xl border px-5 py-4 mb-6 ${confidenceBg}`}>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Overall confidence</p>
            <div className={`text-4xl font-bold font-mono mt-1 ${confidenceColor}`}>{confidencePct}%</div>
          </div>
          <BarChart2 size={40} className={`opacity-20 ${confidenceColor}`} />
        </div>
        <div className="mt-3 h-2 bg-white/60 rounded-full overflow-hidden">
          <div className={`h-full rounded-full transition-all duration-700 ${barColor}`} style={{ width: `${confidencePct}%` }} />
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6 mb-6 space-y-5">
        {(sourceFilename || sourceType) && (
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <div className="w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center shrink-0">
                <FileText size={16} className="text-slate-500" />
              </div>
              <div className="min-w-0">
                <p className="text-xs text-slate-500">Source file</p>
                <p className="text-sm font-semibold text-slate-800 truncate">{sourceFilename ?? "—"}</p>
              </div>
            </div>
            {sourceType && <div className="text-xs font-mono text-slate-500">{sourceType}</div>}
          </div>
        )}

        {totalEntities != null && (
          <div>
            <p className="text-xs font-medium text-slate-500 mb-3">Entities ready to write</p>
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-lg bg-indigo-50 px-4 py-3">
                <div className="text-2xl font-bold font-mono text-indigo-700">{totalEntities.toLocaleString()}</div>
                <div className="text-xs text-indigo-600 mt-0.5">Total entities</div>
              </div>
              {entityCountEntries.map(([k, v]) => (
                <div key={k} className="rounded-lg bg-slate-50 px-4 py-3">
                  <div className="text-xl font-bold font-mono text-slate-700">{v.toLocaleString()}</div>
                  <div className="text-xs text-slate-500 mt-0.5">{k}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          {[
            { label: "Total fields", value: summary.total_fields },
            { label: "T1 auto-mapped", value: summary.t1_mapped },
            { label: "T2 auto-mapped", value: summary.t2_auto_mapped },
            { label: "Human reviewed", value: summary.t2_human_reviewed },
            { label: "Skipped", value: summary.skipped },
          ]
            .filter((it) => it.value != null)
            .map(({ label, value }) => (
              <div key={label} className="flex justify-between items-center py-1.5 border-b border-slate-100 last:border-0">
                <span className="text-sm text-slate-600">{label}</span>
                <span className="font-mono font-bold text-slate-800">{String(value)}</span>
              </div>
            ))}
          {summary.hierarchy && (
            <div className="col-span-2 flex justify-between items-center py-1.5 border-b border-slate-100">
              <span className="text-sm text-slate-600">Hierarchy</span>
              <span className="font-mono text-sm text-indigo-600">{summary.hierarchy}</span>
            </div>
          )}
        </div>
      </div>

      <GateFinalPlan plan={payload.plan} />

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          <div>{error}</div>
          <button
            type="button"
            onClick={handleRefreshGate}
            className="mt-2 inline-flex items-center gap-1 rounded bg-red-600 px-2 py-1 text-xs font-medium text-white hover:bg-red-700"
          >
            Continue / Refresh gate
          </button>
        </div>
      )}

      <div className="flex gap-3">
        <button
          onClick={() => submitFinalDecision(true)}
          disabled={isPending || isAdvancing || isPreflighting}
          className="inline-flex items-center justify-center gap-2 flex-1 px-8 py-3 bg-indigo-600 text-white text-base font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
        >
          {isPending || isAdvancing || isPreflighting ? (
            <>
              <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              {isPreflighting ? "Checking status…" : "Writing to platform…"}
            </>
          ) : (
            <>
              <CheckCircle size={18} />
              Confirm &amp; Write to Platform
            </>
          )}
        </button>
      </div>

      <p className="mt-3 text-xs text-slate-500">
        Final handoff uses confirm-only flow to keep migrations on the successful completion path.
      </p>

      <button
        onClick={onReset}
        disabled={isPending || isPreflighting}
        className="mt-4 inline-flex items-center gap-2 px-4 py-2 bg-white border border-slate-200 text-slate-700 text-sm font-medium rounded-lg hover:bg-slate-50 disabled:opacity-50 transition-colors"
      >
        <RotateCcw size={14} />
        New migration
      </button>
    </div>
  );
}
