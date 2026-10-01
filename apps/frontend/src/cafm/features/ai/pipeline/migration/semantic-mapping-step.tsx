"use client";

import { useEffect } from "react";
import { CheckCircle } from "lucide-react";
import type {
  MigrationFieldMappingGatePayload,
  MigrationPreSemanticGatePayload,
  NodeInfo,
} from "../../chat-api";
import { countFieldMappingReviewItems, type FieldMappingPayloadShape } from "./migration-gate-state";
import { setColumnStagesRevealed } from "../pipeline-stage-link";
import GateSemanticReview from "./gates/gate-semantic-review";
import { MigrationStepSnapshot } from "./step-pause";

type Props = {
  migrationId: string;
  stepKey: string;
  pausePayload: Record<string, unknown>;
  reviewPayload: FieldMappingPayloadShape;
  onSubmitted: (opts?: { fieldMappingSubmitted?: boolean }) => void;
  onFieldFocus?: (terms: string[]) => void;
  t1Snapshot?: {
    payload: MigrationPreSemanticGatePayload;
    decisions: Record<string, Array<{ source_field: string; decision: "approve" | "semantic" }>>;
  };
  allNodes?: NodeInfo[];
  embeddedRail?: boolean;
};

function toGatePayload(shape: FieldMappingPayloadShape): MigrationFieldMappingGatePayload {
  return {
    review_items_by_table: shape.flagged_by_table as MigrationFieldMappingGatePayload["review_items_by_table"],
    flagged_by_table: shape.flagged_by_table as MigrationFieldMappingGatePayload["flagged_by_table"],
    unmappable_items_by_table: shape.unmapped_by_table as MigrationFieldMappingGatePayload["unmappable_items_by_table"],
    unmapped_by_table: shape.unmapped_by_table as MigrationFieldMappingGatePayload["unmapped_by_table"],
    existing_canonical_tables: shape.existing_canonical_tables,
  };
}

/** Semantic Mapping step pause with editable table/column review (UDR Tier-2). */
export default function SemanticMappingStep({
  migrationId,
  stepKey,
  pausePayload,
  reviewPayload,
  onSubmitted,
  onFieldFocus,
  t1Snapshot,
  allNodes,
  embeddedRail = false,
}: Props) {
  // By the time this semantic-mapping gate is open, table routing is already CONFIRMED, so the
  // column-intelligence stages (B13.1→B21.1 / steps 2.6–2.12) and the "Mapping decisions" log
  // are legitimate context that must stay VISIBLE on the right — they line up 1:1 (same sequence)
  // with the center analysis cards. Ensure they're revealed here in case an earlier gate hid them.
  useEffect(() => {
    setColumnStagesRevealed(true);
  }, []);
  const gatePayload = {
    ...toGatePayload(reviewPayload),
    // Carry the COMPLETE-state combined view + counts from the step-pause payload so the embedded
    // review screen's "Auto accepted" reflects Tier-1 approved + Tier-2 auto (not the 0 it showed
    // when only the Tier-2 flagged/unmapped subset reached the gate).
    combined_mapping_view: pausePayload.combined_mapping_view,
    auto_accepted_count: pausePayload.auto_accepted_count,
    flagged_count: pausePayload.flagged_count,
    unmappable_count: pausePayload.unmappable_count,
  } as MigrationFieldMappingGatePayload;
  const reviewItemCount = countFieldMappingReviewItems(reviewPayload);
  const flaggedCount = Object.values(reviewPayload.flagged_by_table).reduce((n, rows) => n + rows.length, 0);
  const unmappedCount = Object.values(reviewPayload.unmapped_by_table).reduce((n, rows) => n + rows.length, 0);
  const flagged = pausePayload.flagged ?? flaggedCount;
  const t2Auto = pausePayload.auto_accepted_count ?? pausePayload.t2_auto ?? pausePayload.tier2_auto_mapped ?? 0;
  const unmappable = pausePayload.unmappable ?? 0;

  return (
    <div className={embeddedRail ? "w-full min-w-0" : "max-w-6xl"}>
      {!embeddedRail ? (
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-xl bg-blue-100 flex items-center justify-center shrink-0">
            <CheckCircle size={20} className="text-blue-600" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-slate-900">Semantic Mapping (Tier 2) — Complete</h2>
            <p className="text-sm text-slate-500">
              Review Tier-2 results, change canonical tables or columns, then continue or submit decisions.
            </p>
          </div>
        </div>
      ) : (
        <div className="mb-3 rounded-lg border border-blue-100 bg-blue-50/80 px-3 py-2">
          <p className="text-xs font-semibold text-blue-900">Tier-2 semantic — review required</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5 text-[10px] font-mono">
            <span className="rounded-full bg-emerald-100 text-emerald-800 px-2 py-0.5">auto {String(t2Auto)}</span>
            <span className="rounded-full bg-amber-100 text-amber-800 px-2 py-0.5">flagged {String(flagged)}</span>
            <span className="rounded-full bg-red-100 text-red-800 px-2 py-0.5">unmappable {String(unmappable)}</span>
          </div>
        </div>
      )}

      {!embeddedRail ? (
        <div className="mb-6">
          <MigrationStepSnapshot stepKey={stepKey} payload={pausePayload} allNodes={allNodes} />
        </div>
      ) : null}

      {reviewItemCount > 0 ? (
        <GateSemanticReview
          migrationId={migrationId}
          payload={gatePayload}
          onSubmitted={(opts) => onSubmitted(opts)}
          onFieldFocus={onFieldFocus}
          t1Snapshot={t1Snapshot}
          allowAdvanceOnly
          onAdvanceOnly={() => onSubmitted({ fieldMappingSubmitted: false })}
          embedded
          compact={embeddedRail}
        />
      ) : (
        <p className={`text-slate-500 mb-4 ${embeddedRail ? "text-xs" : "text-sm"}`}>
          No semantic fields to review
          {unmappedCount > 0 ? ` (${unmappedCount} unmappable in snapshot)` : ""}. Use Continue below to proceed.
        </p>
      )}
    </div>
  );
}
