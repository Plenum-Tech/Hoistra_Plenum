"use client";
import { useState } from "react";
import { CheckCircle } from "lucide-react";
import {
  useMigrationGateUniqueTableApproval,
  type MigrationUniqueTableGatePayload,
} from "../../../chat-api";
import { TableResolutionPanels } from "../migration-metadata-view";

interface Props {
  migrationId: string;
  payload: MigrationUniqueTableGatePayload;
  onSubmitted: () => void;
}

/**
 * Group A step 2 — UNIQUE-TABLE IDENTIFICATION (B6.1 duplicate detection + B7.1 unique tables).
 * Runs AFTER the primary-key gate, so each unique-table card shows its CONFIRMED primary key. The
 * primary key was already decided at the previous gate, so this gate is a mandatory confirm (no
 * per-table edits). Approving advances the graph to the pre-semantic routing/mapping gate.
 */
export default function GateUniqueTableApproval({ migrationId, payload, onSubmitted }: Props) {
  const [error, setError] = useState<string | null>(null);
  const { mutate, isPending } = useMigrationGateUniqueTableApproval({
    onSuccess: () => onSubmitted(),
    onError: (e) => setError(e instanceof Error ? e.message : "Failed to submit approval"),
  });

  const handleApprove = () => {
    setError(null);
    mutate({ migrationId, body: { approved: true } });
  };

  return (
    <div className="max-w-6xl">
      <div className="mb-4">
        <div className="text-lg font-semibold text-slate-800">
          Confirm unique-table identification
        </div>
        <div className="text-sm text-slate-500">
          Review the identified unique tables — each shown with its confirmed primary key. Approve
          to continue to table routing / mapping.
        </div>
      </div>

      {/* Group A step 2 — B6.1 duplicate detection + B7.1 unique tables (with confirmed PK). */}
      <TableResolutionPanels report={payload?.table_resolution ?? null} phase="unique_only" />

      {error ? (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      <button
        type="button"
        onClick={handleApprove}
        disabled={isPending}
        className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-8 py-3 text-base font-medium text-white transition-colors hover:bg-indigo-700 disabled:opacity-50"
      >
        {isPending ? (
          <>
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
            Submitting…
          </>
        ) : (
          <>
            <CheckCircle size={18} />
            Approve unique tables &amp; continue
          </>
        )}
      </button>
    </div>
  );
}
