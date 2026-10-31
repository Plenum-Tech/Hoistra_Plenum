// The contract between CAFM Web's migration panel (features/ai/pipeline/migration/
// migration-content.tsx, which decides what the run is waiting for and renders it) and
// Hoistra's step-by-step card (hoistra-migration-wizard.tsx, which draws the step rail and
// reopens finished steps). The panel reports; the card never re-derives the pipeline state.
import type { MigrationStatusResponse } from "@/features/ai/chat-api";
import type { NodeSnapshot, PreSemanticSubmittedSnapshot } from "@/features/ai/pipeline/migration/migration-content";

export type WizardActive = {
  kind: "loading" | "running" | "gate" | "pause" | "complete" | "failed" | "cancelled" | "restarting";
  /** Pipeline step 1–10 (10 = the write). */
  node?: number;
  /** The human stop inside the step: pk · unique · routing · classification · column_mapping · columns · semantic_review · unknown. */
  sub?: string;
  /** A step pause the pipeline continues on its own. */
  auto?: boolean;
};

export type WizardContentState = {
  active: WizardActive;
  /** Finished steps whose output the panel still holds, in pipeline order. */
  snapshots: NodeSnapshot[];
  /** The submitted pre-semantic decision sets (table routing, column matching). */
  preSemantic: PreSemanticSubmittedSnapshot[];
  udrTable: MigrationStatusResponse["udr_table_resolution"] | null;
  udrColumn: MigrationStatusResponse["udr_column_intelligence"] | null;
  /** The panel's own re-run notice ("Re-running from node 1…"). */
  rerunMsg: string | null;
};

export type WizardActions = {
  restartFromNode1: () => void;
  rerunFromNode: (node: number) => void;
};

export type WizardBridge = {
  onState: (state: WizardContentState) => void;
  register?: (actions: WizardActions) => void;
};
