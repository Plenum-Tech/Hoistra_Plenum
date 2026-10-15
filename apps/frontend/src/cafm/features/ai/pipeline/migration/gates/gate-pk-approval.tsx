"use client";
import { useState } from "react";
import { CheckCircle } from "lucide-react";
import {
  useMigrationGatePkApproval,
  type MigrationPkApprovalGatePayload,
} from "../../../chat-api";
import { TableResolutionPanels } from "../migration-metadata-view";

interface Props {
  migrationId: string;
  payload: MigrationPkApprovalGatePayload;
  onSubmitted: () => void;
}

/**
 * Group A step 1 — PRIMARY-KEY IDENTIFICATION (B8.1). The user reviews/edits each table's primary
 * key and approves; the resume advances the graph to the SECOND Group-A gate (unique-table
 * identification), which shows the confirmed PK on each unique-table card.
 */
export default function GatePkApproval({ migrationId, payload, onSubmitted }: Props) {
  const pkConfirmation = payload?.pk_confirmation ?? {};
  const [pkOverrides, setPkOverrides] = useState<Record<string, string[]>>(() => {
    const init: Record<string, string[]> = {};
    for (const [tbl, info] of Object.entries(pkConfirmation)) {
      init[tbl] = (info?.detected_pk ?? []).filter((c) => c && c !== "_udr_id");
    }
    return init;
  });
  const [error, setError] = useState<string | null>(null);
  const { mutate, isPending } = useMigrationGatePkApproval({
    onSuccess: () => onSubmitted(),
    onError: (e) => setError(e instanceof Error ? e.message : "Failed to submit primary keys"),
  });

  const handleApprove = () => {
    setError(null);
    mutate({ migrationId, body: { pk_overrides: pkOverrides } });
  };

  return (
    <div className="max-w-6xl">
      <div className="mb-4">
        <div className="text-lg font-semibold text-slate-800">
          Confirm primary keys
        </div>
        <div className="text-sm text-slate-500">
          Review the detected primary key for each table and confirm or edit it. Approve to
          continue to unique-table identification.
        </div>
      </div>

      {/* Group A step 1 — B8.1 primary-key identification ONLY. Duplicate-table (B6.1),
          unique-table (B7.1) and column-merge (B7.2) cards are the SECOND gate, shown after the
          PK is approved. */}
      <TableResolutionPanels
        report={payload?.table_resolution ?? null}
        phase="pk_only"
        pkConfirmation={pkConfirmation}
        pkOverrides={pkOverrides}
        onPkChange={(tbl, cols) => setPkOverrides((prev) => ({ ...prev, [tbl]: cols }))}
      />

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
            Approve primary keys &amp; continue
          </>
        )}
      </button>
    </div>
  );
}
