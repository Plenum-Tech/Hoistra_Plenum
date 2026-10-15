"use client";
import { useMemo, useRef, useState, useEffect } from "react";
import {
  Brain,
  CheckCircle,
  XCircle,
  ChevronDown,
  ChevronUp,
  ArrowRight,
  Edit2,
  RotateCcw,
} from "lucide-react";
import {
  useMigrationGateFieldMapping,
  useMigrationGatePreSemantic,
  useMigrationAdvance,
  schemaMapperApi,
  type MigrationStatus,
  type MigrationFieldMappingGatePayload,
  type MigrationFlaggedFieldItem,
  type MigrationUnmappedFieldItem,
  type MigrationGateFieldMappingDecision,
  type MigrationGateFieldMappingUnmappedDecision,
  type MigrationPreSemanticGatePayload,
} from "../../../chat-api";
import { DEFAULT_PLENUM_CANONICAL_TABLES, normalizeMigrationGateType } from "../migration-gate-state";
import {
  getSuggestedTarget,
  resolveDefaultCanonicalTable,
  toPlenumTableSlug,
} from "../migration-mapping-utils";
import {
  MigrationColumnOverride,
  emptyColumnOverrideDraft,
  type ColumnOverrideDraft,
} from "../migration-column-override";
import { FlaggedFieldScoreSection, MatchScoreTable } from "../flagged-field-match-scores";
import {
  MigrationCanonicalTableSelect,
  resolveCanonicalTable,
} from "../migration-canonical-table-select";
import { MigrationNewTablesSection, type NewTableDef } from "../migration-new-tables-section";
import {
  persistFieldMappingDraft,
  markSemanticDismissed,
  clearFieldMappingDraft,
  waitForFieldMappingGate,
  loadFieldMappingDraftEnvelope,
  loadFieldMappingDraftEnvelopeMerged,
  restoreSemanticReviewFromDraft,
  flushFieldMappingDraftToServer,
  type FieldMappingDraftEnvelope,
} from "../migration-field-mapping-draft";

interface Props {
  migrationId: string;
  payload: MigrationFieldMappingGatePayload;
  onSubmitted: (opts?: { fieldMappingSubmitted?: boolean }) => void;
  onFieldFocus?: (terms: string[]) => void;
  pipelineStatus?: MigrationStatus | null;
  /** Pre-semantic snapshot for optional Tier-2 re-run (approve | semantic). */
  t1Snapshot?: {
    payload: MigrationPreSemanticGatePayload;
    decisions: Record<string, Array<{ source_field: string; decision: "approve" | "semantic" }>>;
  };
  /** Nested under SemanticMappingStep — compact chrome. */
  embedded?: boolean;
  /** Orchestrator rail — single column, smaller controls. */
  compact?: boolean;
  allowAdvanceOnly?: boolean;
  onAdvanceOnly?: () => void;
}

type Decision = "accept" | "reject" | "override";

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 85 ? "bg-green-500" : pct >= 65 ? "bg-amber-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-1.5 rounded-full bg-slate-100 overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span
        className={`text-xs font-mono font-semibold ${
          pct >= 85 ? "text-green-600" : pct >= 65 ? "text-amber-600" : "text-red-500"
        }`}
      >
        {pct}%
      </span>
    </div>
  );
}

/** Backend combined_mapping_view (human_review_node.build_combined_mapping_view) — the COMPLETE
 *  post-semantic state: Tier-1 approved + Tier-2 auto are auto_accepted, Tier-2 flagged needs
 *  review, Tier-2 unmappable has no destination. */
type CombinedMapRow = {
  source_table?: string;
  source_field?: string;
  target_table?: string | null;
  target_field?: string | null;
  confidence?: number | null;
  tier?: string;
};
type CombinedMappingView = {
  auto_accepted: CombinedMapRow[];
  flagged: CombinedMapRow[];
  unmappable: CombinedMapRow[];
};

type UnmappedAction = "skip" | "raw_metadata" | "custom";
type UnmappedRow = {
  sourceField: string;
  action: UnmappedAction;
  /** When action === "custom": the new column's name + SQL type (user-editable). */
  customColumnName?: string;
  dataType?: string;
};

const DDL_DATA_TYPES = [
  "VARCHAR(255)",
  "TEXT",
  "INTEGER",
  "BIGINT",
  "NUMERIC",
  "BOOLEAN",
  "DATE",
  "TIMESTAMPTZ",
  "UUID",
];

export default function GateSemanticReview({
  migrationId,
  payload,
  onSubmitted,
  onFieldFocus,
  pipelineStatus: _pipelineStatus,
  t1Snapshot,
  embedded = false,
  compact = false,
  allowAdvanceOnly = false,
  onAdvanceOnly,
}: Props) {
  const STEP_PAUSED_STATUS_RE = /status\s*:?\s*step_paused/i;
  const FAILED_STATUS_RE = /status\s*:?\s*(failed|ddl_failed)/i;
  const REVIEW_READY_STATUS_RE = /status\s*:?\s*(awaiting_review|running)/i;
  const GATE_MISMATCH_RE = /gate mismatch/i;

  const reviewByTable: Record<string, MigrationFlaggedFieldItem[]> =
    (payload.review_items_by_table as Record<string, MigrationFlaggedFieldItem[]> | undefined) ??
    (payload.flagged_by_table as Record<string, MigrationFlaggedFieldItem[]> | undefined) ??
    {};

  const unmappedByTable: Record<string, MigrationUnmappedFieldItem[]> =
    (payload.unmappable_items_by_table as Record<string, MigrationUnmappedFieldItem[]> | undefined) ??
    (payload.unmapped_by_table as Record<string, MigrationUnmappedFieldItem[]> | undefined) ??
    {};

  const canonicalTables = useMemo(() => {
    const set = new Set<string>(DEFAULT_PLENUM_CANONICAL_TABLES);
    for (const t of payload.existing_canonical_tables ?? []) {
      if (typeof t === "string" && t.trim()) set.add(t.trim());
    }
    return Array.from(set).sort();
  }, [payload.existing_canonical_tables]);

  const allTables = useMemo(
    () => [...new Set([...Object.keys(reviewByTable), ...Object.keys(unmappedByTable)])].sort(),
    [reviewByTable, unmappedByTable],
  );

  const [unmappedRows, setUnmappedRows] = useState<Record<string, UnmappedRow[]>>(() => {
    // Default action: "custom" (New column) — the user wants Tier-2 unmappable
    // fields to be CREATED on the destination by default (using the canonical
    // name pre-filled from column_canonical), not silently skipped. They can
    // still flip individual rows to skip/raw_metadata if they don't want them.
    const out: Record<string, UnmappedRow[]> = {};
    for (const [tbl, items] of Object.entries(unmappedByTable)) {
      out[tbl] = items.map((item) => ({
        sourceField: item.source_field,
        action: "custom" as UnmappedAction,
      }));
    }
    return out;
  });

  // Hydrate column_canonical + table_routing from CURRENT migration state.
  // Paused gates carry a payload constructed at interrupt time — if that
  // happened before column_canonical was added to the gate payload, the
  // payload doesn't have it. This GET runs against live state and lets the
  // gate pick up the canonical names + user-confirmed routing without
  // forcing the user to restart the whole migration.
  const [hydratedCanonicals, setHydratedCanonicals] = useState<Record<string, string> | null>(null);
  const [hydratedRouting, setHydratedRouting] = useState<Record<string, string> | null>(null);
  const [hydratedGroups, setHydratedGroups] = useState<
    Array<{ group_id?: string; canonical_name?: string; members?: string[]; pinned?: boolean }> | null
  >(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await schemaMapperApi.getMigrationStateCanonicals(migrationId);
        if (cancelled) return;
        if (res?.column_canonical) setHydratedCanonicals(res.column_canonical);
        if (res?.table_routing) setHydratedRouting(res.table_routing);
        if (res?.canonical_groups) setHydratedGroups(res.canonical_groups);
      } catch {
        // Best-effort — gate still works with whatever's in the payload.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [migrationId]);
  // Once we receive a routing map from state, ALWAYS override the heuristic
  // default. state.table_routing is the source of truth — it's the routing
  // the user CONFIRMED at the pre-semantic gate. The earlier conditional
  // override (only-when-source-matches-name) missed cases where the heuristic
  // had already produced something plausible-looking but wrong (e.g.
  // 'work_orders_2' for WorkOrders_2 even though the user routed it to
  // 'work_orders'). Track which keys came from state so the user's manual
  // edits after mount aren't stomped on.
  const overriddenFromStateRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!hydratedRouting || Object.keys(hydratedRouting).length === 0) return;
    setCanonicalTableBySource((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const [src, tgt] of Object.entries(hydratedRouting)) {
        if (!tgt) continue;
        if (overriddenFromStateRef.current.has(src) && next[src] === tgt) continue;
        if (next[src] !== tgt) {
          next[src] = tgt;
          overriddenFromStateRef.current.add(src);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [hydratedRouting]);

  const savedEnvelope = loadFieldMappingDraftEnvelope(migrationId);
  const restored = savedEnvelope
    ? restoreSemanticReviewFromDraft(savedEnvelope, reviewByTable)
    : null;

  const [canonicalTableBySource, setCanonicalTableBySource] = useState<Record<string, string>>(() => {
    if (restored?.canonicalTableBySource && Object.keys(restored.canonicalTableBySource).length) {
      return restored.canonicalTableBySource;
    }
    // Carry the user's Step-2 destination choice through into Tier-2. The
    // pre-semantic gate stores the user-confirmed source→target table mapping
    // in state.table_routing; without this the Tier-2 gate would silently
    // re-derive the target by name and stomp on the user's pick (e.g.
    // WorkOrders_2 routed to work_orders in Step 2 would default back to
    // 'WorkOrders_2' here because it has no canonical-name match).
    const routing = (payload as MigrationFieldMappingGatePayload).table_routing ?? {};
    const init: Record<string, string> = {};
    for (const tbl of allTables) {
      const pinnedTarget = routing[tbl] || routing[tbl.toLowerCase()];
      init[tbl] = pinnedTarget && pinnedTarget.trim()
        ? pinnedTarget.trim()
        : resolveDefaultCanonicalTable(tbl, canonicalTables);
    }
    return init;
  });

  const [decisions, setDecisions] = useState<Record<string, Decision>>(() => {
    if (restored?.decisions && Object.keys(restored.decisions).length) {
      return restored.decisions;
    }
    const init: Record<string, Decision> = {};
    for (const [tbl, items] of Object.entries(reviewByTable)) {
      for (const item of items) {
        init[`${tbl}.${item.source_field}`] = "accept";
      }
    }
    return init;
  });

  const [focusedTerm, setFocusedTerm] = useState<string | null>(null);
  const [columnDrafts, setColumnDrafts] = useState<Record<string, ColumnOverrideDraft>>(
    () => restored?.columnDrafts ?? {},
  );
  const [newTableFlags, setNewTableFlags] = useState<Record<string, boolean>>({});
  const [newTableNames, setNewTableNames] = useState<Record<string, string>>({});
  const [newTableDefs, setNewTableDefs] = useState<NewTableDef[]>(() => restored?.newTableDefs ?? []);
  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set(allTables));
  const [rerunSemanticFields, setRerunSemanticFields] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [isPreflighting, setIsPreflighting] = useState(false);
  const [isRerunning, setIsRerunning] = useState(false);
  const lastSubmitRef = useRef<{
    flagged: Record<string, MigrationGateFieldMappingDecision[]>;
    unmapped: Record<string, MigrationGateFieldMappingUnmappedDecision[]>;
  } | null>(null);
  const submitAfterAdvanceRef = useRef(false);

  const { mutate: submitGate, isPending } = useMigrationGateFieldMapping({
    onSuccess: () => {
      clearFieldMappingDraft(migrationId);
      markSemanticDismissed(migrationId);
      onSubmitted({ fieldMappingSubmitted: true });
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

  const { mutate: submitPreSemantic } = useMigrationGatePreSemantic({
    onSuccess: () => onSubmitted(),
    onError: (err: unknown) => {
      setError(err instanceof Error ? err.message : "Pre-semantic re-run failed");
    },
  });

  const { mutate: advance, isPending: isAdvancing } = useMigrationAdvance({
    onSuccess: () => {
      setError(null);
      if (!submitAfterAdvanceRef.current || !lastSubmitRef.current) return;
      const body = lastSubmitRef.current;
      submitAfterAdvanceRef.current = false;
      void (async () => {
        setError("Applying mapping decisions — syncing with pipeline…");
        const wait = await waitForFieldMappingGate(migrationId, 25, 1500);
        if (wait.outcome === "field_mapping_open") {
          setError(null);
          submitGate({ migrationId, body });
          return;
        }
        persistFieldMappingDraft(migrationId, body, {
          canonicalTableBySource: { ...canonicalTableBySource },
        });
        markSemanticDismissed(migrationId);
        setError(null);
        onSubmitted();
      })();
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

  function _submitTableKey(sourceTable: string): string {
    return canonicalTableBySource[sourceTable]?.trim() || sourceTable;
  }

  function fieldKey(sourceTable: string, sourceField: string) {
    return `${sourceTable}.${sourceField}`;
  }

  function toggleTable(tbl: string) {
    setExpandedTables((prev) => {
      const next = new Set(prev);
      if (next.has(tbl)) next.delete(tbl);
      else next.add(tbl);
      return next;
    });
  }

  function handleTermClick(term: string) {
    const next = focusedTerm === term ? null : term;
    setFocusedTerm(next);
    onFieldFocus?.(next ? [term] : []);
  }

  function setDecision(tbl: string, field: string, d: Decision, item?: MigrationFlaggedFieldItem) {
    const key = fieldKey(tbl, field);
    setDecisions((prev) => ({ ...prev, [key]: d }));
    if (d === "override" && item && !columnDrafts[key]) {
      const suggested = getSuggestedTarget(item);
      setColumnDrafts((prev) => ({ ...prev, [key]: emptyColumnOverrideDraft(suggested) }));
    }
  }

  function patchColumnDraft(key: string, patch: Partial<ColumnOverrideDraft>) {
    setColumnDrafts((prev) => ({ ...prev, [key]: { ...(prev[key] ?? emptyColumnOverrideDraft()), ...patch } }));
  }

  function toggleRerunField(tbl: string, field: string) {
    const key = fieldKey(tbl, field);
    setRerunSemanticFields((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function acceptAll() {
    setDecisions((prev) => {
      const next = { ...prev };
      for (const [tbl, items] of Object.entries(reviewByTable)) {
        for (const item of items) next[fieldKey(tbl, item.source_field)] = "accept";
      }
      return next;
    });
  }

  function buildFieldMappingBody(opts?: { skipValidation?: boolean }): {
    body: {
      flagged: Record<string, MigrationGateFieldMappingDecision[]>;
      unmapped: Record<string, MigrationGateFieldMappingUnmappedDecision[]>;
    };
    error: string | null;
  } {
    const skipValidation = opts?.skipValidation ?? false;
    const flagged: Record<string, MigrationGateFieldMappingDecision[]> = {};
    const unmapped: Record<string, MigrationGateFieldMappingUnmappedDecision[]> = {};
    const missing: string[] = [];

    for (const [sourceTbl, items] of Object.entries(reviewByTable)) {
      const { table: canonicalTarget, isNewTable } = resolveCanonicalTable(
        sourceTbl,
        canonicalTableBySource,
        newTableFlags,
        newTableNames,
      );

      flagged[sourceTbl] = (items ?? []).map((item): MigrationGateFieldMappingDecision => {
        const key = fieldKey(sourceTbl, item.source_field);
        const decision = decisions[key] ?? "accept";
        const draft = columnDrafts[key] ?? emptyColumnOverrideDraft(getSuggestedTarget(item));

        if (decision === "override" && draft.mode === "new_column") {
          const col = draft.newColumnName.trim();
          if (!col) missing.push(`${sourceTbl}.${item.source_field}: new column name required`);
          if (!canonicalTarget) missing.push(`${sourceTbl}.${item.source_field}: target table required`);

          const list = unmapped[sourceTbl] ?? [];
          list.push({
            action: "custom",
            source_field: item.source_field,
            target_table: canonicalTarget,
            custom_column_name: col || null,
            data_type: draft.dataType || "VARCHAR(255)",
            nullable: draft.nullable,
            is_new_table: isNewTable,
            new_table_name: isNewTable ? canonicalTarget : null,
            new_table_pk: isNewTable ? "id" : null,
          });
          unmapped[sourceTbl] = list;

          return {
            action: "reject",
            source_field: item.source_field,
            target_field: null,
            rationale: "User defined new column via DDL",
          };
        }

        if (decision === "override") {
          const target =
            draft.mode === "existing" ? draft.targetField.trim() : draft.newColumnName.trim();
          if (!target) missing.push(`${sourceTbl}.${item.source_field}: override target required`);
          return {
            action: "override",
            source_field: item.source_field,
            target_field: target || null,
            rationale: null,
          };
        }

        return {
          action: decision === "reject" ? "reject" : "accept",
          source_field: item.source_field,
          target_field: decision === "accept" ? item.target_field ?? null : null,
          rationale: null,
        };
      });
    }

    for (const [sourceTbl, rows] of Object.entries(unmappedRows)) {
      const { table: canonicalTarget, isNewTable } = resolveCanonicalTable(
        sourceTbl,
        canonicalTableBySource,
        newTableFlags,
        newTableNames,
      );
      const list: MigrationGateFieldMappingUnmappedDecision[] = [];
      for (const row of rows) {
        if (!row.sourceField.trim()) continue;
        if (row.action === "custom") {
          const colName = (row.customColumnName ?? row.sourceField).trim() || row.sourceField;
          list.push({
            action: "custom",
            source_field: row.sourceField,
            target_table: canonicalTarget || sourceTbl,
            custom_column_name: colName,
            data_type: row.dataType || "VARCHAR(255)",
            nullable: true,
            is_new_table: isNewTable,
            new_table_name: isNewTable ? canonicalTarget : null,
            new_table_pk: isNewTable ? "id" : null,
          });
        } else {
          list.push({
            action: row.action,
            source_field: row.sourceField,
            target_table: null,
            custom_column_name: null,
            data_type: null,
          });
        }
      }
      if (list.length) unmapped[sourceTbl] = list;
    }

    for (const nt of newTableDefs) {
      if (!nt.table_name.trim()) continue;
      const key = `_new_table_${nt.table_name}`;
      unmapped[key] = nt.columns
        .filter((c) => c.column_name.trim())
        .map((c) => ({
          action: "custom" as const,
          source_field: c.column_name,
          target_table: nt.table_name,
          custom_column_name: c.column_name,
          data_type: c.data_type,
          nullable: c.nullable,
          is_new_table: true,
          new_table_name: nt.table_name,
          new_table_pk: nt.pk_col || "id",
        }));
    }

    if (missing.length && !skipValidation) {
      return { body: { flagged, unmapped }, error: missing.slice(0, 3).join("; ") + (missing.length > 3 ? "…" : "") };
    }
    return { body: { flagged, unmapped }, error: null };
  }

  function saveDraftToStorage() {
    const built = buildFieldMappingBody({ skipValidation: true });
    const meta = { canonicalTableBySource: { ...canonicalTableBySource } };
    persistFieldMappingDraft(migrationId, built.body, meta);
    return { body: built.body, meta: { ...meta, savedAt: Date.now() } } satisfies FieldMappingDraftEnvelope;
  }

  function flushDraftNow() {
    const envelope = saveDraftToStorage();
    flushFieldMappingDraftToServer(migrationId, envelope);
  }

  useEffect(() => {
    setUnmappedRows((prev) => {
      const next: Record<string, UnmappedRow[]> = { ...prev };
      for (const [tbl, items] of Object.entries(unmappedByTable)) {
        if (next[tbl]?.length) continue;
        next[tbl] = items.map((item) => ({
          sourceField: item.source_field,
          action: "skip" as UnmappedAction,
        }));
      }
      return next;
    });
  }, [migrationId, unmappedByTable]);

  useEffect(() => {
    const timer = setTimeout(() => {
      const built = buildFieldMappingBody({ skipValidation: true });
      const meta = { canonicalTableBySource: { ...canonicalTableBySource } };
      try {
        const envelope = {
          body: built.body,
          meta: { ...meta, savedAt: Date.now() },
        };
        sessionStorage.setItem(
          `plenum-migration-fm-draft:${migrationId}`,
          JSON.stringify(envelope),
        );
      } catch {
        /* ignore */
      }
    }, 800);
    return () => clearTimeout(timer);
  }, [
    migrationId,
    decisions,
    columnDrafts,
    canonicalTableBySource,
    newTableFlags,
    newTableNames,
    newTableDefs,
    unmappedRows,
  ]);

  const reviewFingerprint = useMemo(
    () =>
      JSON.stringify(
        Object.entries(reviewByTable).map(([tbl, items]) => [
          tbl,
          (items ?? []).map((i) => i.source_field),
        ]),
      ),
    [reviewByTable],
  );

  useEffect(() => {
    let cancelled = false;
    void loadFieldMappingDraftEnvelopeMerged(migrationId).then((envelope) => {
      if (cancelled || !envelope) return;
      const applied = restoreSemanticReviewFromDraft(envelope, reviewByTable);
      if (Object.keys(applied.decisions).length) setDecisions((prev) => ({ ...prev, ...applied.decisions }));
      if (Object.keys(applied.columnDrafts).length) {
        setColumnDrafts((prev) => ({ ...prev, ...applied.columnDrafts }));
      }
      if (Object.keys(applied.canonicalTableBySource).length) {
        setCanonicalTableBySource((prev) => ({ ...prev, ...applied.canonicalTableBySource }));
      }
      if (applied.newTableDefs.length) setNewTableDefs(applied.newTableDefs);
    });
    return () => {
      cancelled = true;
    };
  }, [migrationId, reviewFingerprint]);

  function handleSubmit() {
    const built = buildFieldMappingBody();
    if (built.error) {
      setError(built.error);
      return;
    }
    lastSubmitRef.current = built.body;
    flushDraftNow();
    submitAfterAdvanceRef.current = true;
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
          setError(`Cannot submit semantic review yet. Current migration status is: ${latestStatus}`);
          return;
        }
        const gateType = String(latest.pending_gate_type ?? "").toLowerCase();
        const isFieldMappingGate =
          normalizeMigrationGateType(latest.pending_gate_type) === "field_mapping" ||
          (gateType.includes("field") && gateType.includes("map")) ||
          (gateType.includes("human") && gateType.includes("review")) ||
          (gateType.includes("table") && gateType.includes("structure")) ||
          (gateType.includes("column") && gateType.includes("placement"));
        if (!isFieldMappingGate) {
          setError(
            `Review step changed to '${latest.pending_gate_type ?? "unknown"}'. Please submit in the active gate.`,
          );
          onSubmitted();
          return;
        }
        submitGate({ migrationId, body: built.body });
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

  function handleAdvanceOnly() {
    flushDraftNow();
    setError(null);
    submitAfterAdvanceRef.current = false;
    lastSubmitRef.current = null;
    advance(
      { migrationId },
      {
        onSuccess: () => {
          markSemanticDismissed(migrationId);
          const advanceResult = onAdvanceOnly?.();
          if (advanceResult == null) onSubmitted();
        },
        onError: (err: unknown) => {
          setError(err instanceof Error ? err.message : "Failed to advance");
        },
      },
    );
  }

  /** Re-run Tier-2 via pre-semantic gate (same pattern as GatePreSemantic / streamlit). */
  function handleRerunSemantic() {
    if (!t1Snapshot?.payload?.review_items_by_table) {
      setError("No pre-semantic snapshot — use Override to change target column, or start a new migration to re-run from Tier-1.");
      return;
    }
    const review = t1Snapshot.payload.review_items_by_table ?? {};
    const body: Record<string, Array<{ source_field: string; decision: "approve" | "semantic" }>> = {};

    for (const [tbl, items] of Object.entries(review)) {
      body[tbl] = items.map((item) => {
        const key = fieldKey(tbl, item.source_field);
        const forceSemantic =
          rerunSemanticFields.has(key) ||
          reviewByTable[tbl]?.some((f) => f.source_field === item.source_field && rerunSemanticFields.has(fieldKey(tbl, f.source_field)));
        return {
          source_field: item.source_field,
          decision: forceSemantic ? "semantic" : "approve",
        };
      });
    }

    for (const [tbl, items] of Object.entries(reviewByTable)) {
      if (!body[tbl]) body[tbl] = [];
      const existing = new Set(body[tbl].map((d) => d.source_field));
      for (const item of items) {
        if (existing.has(item.source_field)) continue;
        const key = fieldKey(tbl, item.source_field);
        if (rerunSemanticFields.has(key)) {
          body[tbl].push({ source_field: item.source_field, decision: "semantic" });
        }
      }
    }

    setIsRerunning(true);
    setError(null);
    schemaMapperApi
      .getMigrationStatus(migrationId)
      .then((latest) => {
        const gateType = String(latest.pending_gate_type ?? "").toLowerCase();
        const isPreSemantic = gateType.includes("pre") && gateType.includes("semantic");
        if (!isPreSemantic) {
          setError(
            "Pre-semantic gate is no longer active (pipeline is past Tier-1 review). " +
              "Change canonical table/column with Override, then Submit — or start a new migration to re-run semantic from the beginning.",
          );
          return;
        }
        if (latest.status === "step_paused") {
          advance({ migrationId });
          return;
        }
        submitPreSemantic({ migrationId, body: { decisions: body } });
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to check status for semantic re-run");
      })
      .finally(() => {
        setIsRerunning(false);
      });
  }

  const totalFlagged = Object.values(reviewByTable).flat().length;
  const totalUnmapped = Object.values(unmappedByTable).flat().length;
  const totalItems = totalFlagged + totalUnmapped;
  const countAccepted = Object.values(decisions).filter((v) => v === "accept").length;
  const countRejected = Object.values(decisions).filter((v) => v === "reject").length;
  const countOverridden = Object.values(decisions).filter((v) => v === "override").length;
  const rerunCount = rerunSemanticFields.size;
  // New columns that will be CREATED on submit (DDL): flagged fields overridden to a brand-new
  // column + unmappable fields kept as "new column". The summary showed new TABLES but never the
  // new-column count — surface it so the user sees how many columns get created.
  const countNewColumns =
    Object.entries(reviewByTable).reduce((n, [tbl, items]) => {
      for (const item of items as Array<{ source_field: string }>) {
        const key = fieldKey(tbl, item.source_field);
        if (decisions[key] === "override" && columnDrafts[key]?.mode === "new_column") n += 1;
      }
      return n;
    }, 0) +
    Object.values(unmappedRows).reduce(
      (n, rows) => n + rows.filter((r) => r.action === "custom").length,
      0,
    );

  // ── Complete mapping state (backend combined_mapping_view) ──
  // The review screen must represent the FULL picture: Tier-1 approved + Tier-2 auto are
  // already "auto accepted" — not just the unresolved Tier-2 subset that previously made the
  // header read "Auto Accepted: 0" even though 24 were approved. Falls back to the count
  // fields / the flagged+unmapped totals when an older backend payload omits the combined view.
  const combinedView = (payload as unknown as { combined_mapping_view?: CombinedMappingView })
    .combined_mapping_view;
  const autoAcceptedRows = combinedView?.auto_accepted ?? [];
  const autoAcceptedCount =
    (payload as unknown as { auto_accepted_count?: number }).auto_accepted_count ?? autoAcceptedRows.length;
  const flaggedCount =
    (payload as unknown as { flagged_count?: number }).flagged_count ?? totalFlagged;
  const unmappableCount =
    (payload as unknown as { unmappable_count?: number }).unmappable_count ?? totalUnmapped;

  if (totalItems === 0) {
    return (
      <div className="max-w-4xl rounded-xl border border-slate-200 bg-white shadow-sm p-8 text-center">
        <Brain size={32} className="text-indigo-400 mx-auto mb-3" />
        <p className="text-slate-700 font-semibold">No semantic mappings to review</p>
        <p className="text-sm text-slate-400 mt-1">All fields were deterministically matched or will be handled downstream.</p>
        <button
          onClick={() => onSubmitted()}
          className="mt-5 inline-flex items-center gap-2 px-6 py-2.5 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 transition-colors"
        >
          Continue
        </button>
      </div>
    );
  }

  const pad = compact ? "px-3 py-2.5" : "px-5 py-3.5";
  const textSm = compact ? "text-xs" : "text-sm";

  return (
    <div className={embedded ? "w-full min-w-0" : "max-w-6xl"}>
      {!embedded ? (
        <div className="flex items-start gap-4 mb-6">
          <div className="w-10 h-10 rounded-xl bg-indigo-100 flex items-center justify-center shrink-0">
            <Brain size={20} className="text-indigo-600" />
          </div>
          <div className="flex-1">
            <h2 className="text-lg font-bold text-slate-900">Semantic Mapping Review</h2>
            <p className="text-sm text-slate-500 mt-0.5">
              Review Tier-2 semantic matches. Change canonical table or column, accept, reject, or override before continuing.
            </p>
          </div>
          <div className="text-right shrink-0">
            <div className="text-2xl font-bold font-mono text-slate-800">{totalItems}</div>
            <div className="text-xs text-slate-500">fields to review</div>
          </div>
        </div>
      ) : (
        <div
          className={`mb-3 rounded-lg border border-indigo-100 bg-indigo-50 text-indigo-800 ${compact ? "px-3 py-2 text-[11px]" : "px-4 py-3 text-sm"}`}
        >
          <span className="font-semibold">Edit mappings</span>
          <span className="text-indigo-700">
            {" "}
            — change target table, override column, submit or continue.
          </span>
        </div>
      )}

      {/* Complete mapping state — Tier-1 approved + Tier-2 auto are ALREADY accepted; only
          flagged + unmappable need attention here. Fixes the screen previously reading
          "Auto Accepted: 0" while 24 Tier-1 mappings were already approved. */}
      <div className={`grid gap-2 mb-4 ${compact ? "grid-cols-3" : "grid-cols-3 gap-3"}`}>
        {[
          { label: "Auto accepted", value: autoAcceptedCount, color: "text-green-600" },
          { label: "Flagged", value: flaggedCount, color: "text-amber-600" },
          { label: "Unmappable", value: unmappableCount, color: "text-red-500" },
        ].map(({ label, value, color }) => (
          <div
            key={label}
            className={`rounded-lg border border-slate-200 bg-white shadow-sm text-center ${compact ? "p-2" : "p-4 rounded-xl"}`}
          >
            <div className={`font-bold font-mono ${color} ${compact ? "text-base" : "text-xl"}`}>{value}</div>
            <div className={`text-slate-500 mt-0.5 ${compact ? "text-[10px]" : "text-xs"}`}>{label}</div>
          </div>
        ))}
      </div>

      {autoAcceptedRows.length ? (
        <details className="mb-4 rounded-xl border border-green-200 bg-green-50/40">
          <summary
            className={`cursor-pointer select-none font-medium text-green-800 ${compact ? "px-3 py-2 text-xs" : "px-4 py-3 text-sm"}`}
          >
            {autoAcceptedRows.length} mapping{autoAcceptedRows.length === 1 ? "" : "s"} already accepted
            <span className="font-normal text-green-700"> (Tier-1 approved + Tier-2 auto)</span>
          </summary>
          <div className="max-h-64 overflow-y-auto px-2 pb-2">
            <table className="w-full text-[11px]">
              <thead className="text-slate-400">
                <tr>
                  <th className="px-2 py-1 text-left font-medium">Source</th>
                  <th className="px-2 py-1 text-left font-medium">→ Destination</th>
                  <th className="px-2 py-1 text-right font-medium">Conf</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-green-100">
                {autoAcceptedRows.map((r, i) => (
                  <tr key={`${r.source_table}.${r.source_field}.${i}`}>
                    <td className="px-2 py-1 font-mono text-slate-700">
                      {r.source_table}.{r.source_field}
                    </td>
                    <td className="px-2 py-1 font-mono text-green-800">
                      {r.target_table && r.target_field
                        ? `${r.target_table}.${r.target_field}`
                        : r.target_field ?? "—"}
                    </td>
                    <td className="px-2 py-1 text-right font-mono text-slate-500">
                      {typeof r.confidence === "number" ? `${Math.round(r.confidence * 100)}%` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}

      {/* Previous workflow decisions — Tier-2 is a CONTINUATION of the earlier stages, not a
          fresh start. Surface the Table Mapping (source → destination table) and the Unified
          Column Names (canonical groups, with pins) the user already approved, hydrated from
          the latest workflow state via /state-canonicals so they survive resume / refresh /
          rerun. Read-only here — editing still happens through the decision grid below. */}
      {(hydratedRouting && Object.keys(hydratedRouting).length > 0) ||
      (hydratedGroups && hydratedGroups.length > 0) ? (
        <details className="mb-4 rounded-xl border border-slate-200 bg-slate-50/60">
          <summary
            className={`cursor-pointer select-none font-medium text-slate-700 ${compact ? "px-3 py-2 text-xs" : "px-4 py-3 text-sm"}`}
          >
            Previous mapping decisions
            <span className="font-normal text-slate-500"> (table mapping + unified column names)</span>
          </summary>
          <div className={`space-y-4 ${compact ? "px-2 pb-2" : "px-3 pb-3"}`}>
            {hydratedRouting && Object.keys(hydratedRouting).length > 0 ? (
              <div>
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                  Table mapping
                </div>
                <div className="max-h-48 overflow-y-auto">
                  <table className="w-full text-[11px]">
                    <thead className="text-slate-400">
                      <tr>
                        <th className="px-2 py-1 text-left font-medium">Source table</th>
                        <th className="px-2 py-1 text-left font-medium">→ Destination</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {Object.entries(hydratedRouting).map(([src, tgt]) => (
                        <tr key={src}>
                          <td className="px-2 py-1 font-mono text-slate-700">{src}</td>
                          <td className="px-2 py-1 font-mono text-indigo-700">{tgt}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}
            {hydratedGroups && hydratedGroups.length > 0 ? (
              <div>
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                  Unified column names
                </div>
                <div className="max-h-64 overflow-y-auto space-y-1.5">
                  {hydratedGroups.map((g, i) => (
                    <div
                      key={g.group_id ?? `grp-${i}`}
                      className="rounded-lg border border-slate-200 bg-white px-2 py-1.5"
                    >
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[12px] font-semibold text-slate-800">
                          {g.canonical_name || "—"}
                        </span>
                        {g.pinned ? (
                          <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-amber-700">
                            Pinned
                          </span>
                        ) : null}
                      </div>
                      {g.members && g.members.length > 0 ? (
                        <div className="mt-0.5 font-mono text-[10px] text-slate-500">
                          {g.members.join(" · ")}
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </details>
      ) : null}

      <div
        className={`grid gap-2 mb-4 ${compact ? "grid-cols-2" : "grid-cols-2 sm:grid-cols-5 gap-3 mb-5"}`}
      >
        {[
          { label: "Accepted", value: countAccepted, color: "text-green-600" },
          { label: "Rejected", value: countRejected, color: "text-red-500" },
          { label: "Overridden", value: countOverridden, color: "text-amber-600" },
          { label: "New columns", value: countNewColumns, color: "text-indigo-600" },
          { label: "Re-run marked", value: rerunCount, color: "text-violet-600" },
        ].map(({ label, value, color }) => (
          <div
            key={label}
            className={`rounded-lg border border-slate-200 bg-white shadow-sm text-center ${compact ? "p-2" : "p-4 rounded-xl"}`}
          >
            <div className={`font-bold font-mono ${color} ${compact ? "text-base" : "text-xl"}`}>{value}</div>
            <div className={`text-slate-500 mt-0.5 ${compact ? "text-[10px]" : "text-xs"}`}>{label}</div>
          </div>
        ))}
      </div>

      <div className={`flex flex-wrap justify-end gap-2 mb-3 ${compact ? "flex-col sm:flex-row" : ""}`}>
        <button
          type="button"
          onClick={acceptAll}
          className={`inline-flex items-center gap-2 bg-white border border-slate-200 text-slate-700 font-medium rounded-lg hover:bg-slate-50 transition-colors ${compact ? "px-3 py-1.5 text-xs w-full sm:w-auto justify-center" : "px-4 py-2 text-sm"}`}
        >
          <CheckCircle size={14} />
          Accept all
        </button>
        {t1Snapshot ? (
          <button
            type="button"
            onClick={handleRerunSemantic}
            disabled={isRerunning || rerunCount === 0}
            className={`inline-flex items-center gap-2 bg-violet-600 text-white font-medium rounded-lg hover:bg-violet-700 disabled:opacity-50 transition-colors ${compact ? "px-3 py-1.5 text-xs w-full sm:w-auto justify-center" : "px-4 py-2 text-sm"}`}
          >
            <RotateCcw size={14} className={isRerunning ? "animate-spin" : ""} />
            Re-run semantic ({rerunCount})
          </button>
        ) : null}
      </div>

      <div className="space-y-3 mb-6">
        {allTables.map((tbl) => {
          const items: MigrationFlaggedFieldItem[] = reviewByTable[tbl] ?? [];
          const unmappedItems: MigrationUnmappedFieldItem[] = unmappedByTable[tbl] ?? [];
          const unmappedList = unmappedRows[tbl] ?? [];
          const isOpen = expandedTables.has(tbl);
          const tableDecisions = items.map((i) => decisions[fieldKey(tbl, i.source_field)] ?? "accept");
          const allAccepted = tableDecisions.every((d) => d === "accept");
          const canonicalTarget = canonicalTableBySource[tbl] ?? tbl;
          const tableIsNew = newTableFlags[tbl] ?? false;
          const tableNewName = newTableNames[tbl] ?? "";

          return (
            <div key={tbl} className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
              <button
                type="button"
                className={`w-full flex items-center justify-between hover:bg-slate-50 transition-colors text-left ${pad}`}
                onClick={() => toggleTable(tbl)}
              >
                <div className="flex items-center gap-3 min-w-0 flex-wrap">
                  <span className={`font-semibold text-slate-800 font-mono ${textSm}`}>{tbl}</span>
                  <ArrowRight size={12} className="text-slate-300 shrink-0" />
                  <span className="text-xs font-mono text-indigo-700 bg-indigo-50 px-2 py-0.5 rounded">
                    {canonicalTarget}
                  </span>
                  {items.length > 0 ? (
                    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-700">
                      {items.length} T2
                    </span>
                  ) : null}
                  {unmappedItems.length > 0 ? (
                    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700">
                      {unmappedItems.length} unmappable
                    </span>
                  ) : null}
                  {allAccepted && (
                    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
                      All accepted
                    </span>
                  )}
                </div>
                {isOpen ? (
                  <ChevronUp size={16} className="text-slate-400 shrink-0" />
                ) : (
                  <ChevronDown size={16} className="text-slate-400 shrink-0" />
                )}
              </button>

              {isOpen && (
                <div className="border-t border-slate-100">
                  <div
                    className={`bg-slate-50 border-b border-slate-100 ${compact ? "px-3 py-2" : "px-5 py-3"}`}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <MigrationCanonicalTableSelect
                      sourceTable={tbl}
                      canonicalTables={canonicalTables}
                      value={tableIsNew ? tableNewName : canonicalTarget}
                      isNewTable={tableIsNew}
                      newTableName={tableNewName}
                      compact={compact}
                      onChange={({ canonicalTable, isNewTable, newTableName: nt }) => {
                        setCanonicalTableBySource((prev) => ({ ...prev, [tbl]: canonicalTable }));
                        setNewTableFlags((prev) => ({ ...prev, [tbl]: isNewTable }));
                        setNewTableNames((prev) => ({ ...prev, [tbl]: nt }));
                      }}
                    />
                  </div>

                  <div className="divide-y divide-slate-100">
                    {items.map((item, idx) => {
                      const key = fieldKey(tbl, item.source_field);
                      const decision = decisions[key] ?? "accept";
                      const isOverriding = decision === "override";
                      const markedRerun = rerunSemanticFields.has(key);
                      const effectiveCanonical = tableIsNew ? tableNewName : canonicalTarget;
                      const draft = columnDrafts[key] ?? emptyColumnOverrideDraft(getSuggestedTarget(item));

                      return (
                        <div key={idx} className={pad}>
                          <div className={`flex gap-3 ${compact ? "flex-col" : "flex-row items-start gap-4"}`}>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 flex-wrap">
                                <button
                                  type="button"
                                  onClick={() => handleTermClick(item.source_field)}
                                  className={`font-mono text-xs px-2 py-0.5 rounded transition-colors cursor-pointer ${
                                    focusedTerm === item.source_field
                                      ? "bg-amber-200 text-slate-900 ring-1 ring-amber-400"
                                      : "bg-slate-100 text-slate-700 hover:bg-amber-100"
                                  }`}
                                >
                                  {item.source_field}
                                </button>
                                <ArrowRight size={11} className="text-slate-300 shrink-0" />
                                {!isOverriding ? (
                                  <button
                                    type="button"
                                    onClick={() =>
                                      item.target_field ? handleTermClick(item.target_field) : undefined
                                    }
                                    className={`font-mono text-xs px-2 py-0.5 rounded transition-colors ${
                                      item.target_field && focusedTerm === item.target_field
                                        ? "bg-amber-200 text-slate-900 ring-1 ring-amber-400"
                                        : decision === "reject"
                                          ? "bg-red-50 text-red-400 line-through cursor-default"
                                          : "bg-indigo-50 text-indigo-700 hover:bg-amber-100 cursor-pointer"
                                    }`}
                                  >
                                    {item.target_field ?? "—"}
                                  </button>
                                ) : (
                                  <span className="text-[11px] text-indigo-600 font-medium">Override active</span>
                                )}
                              </div>
                              {/* Feature: top-3 candidate target columns + match score.
                                  Selectable when overriding — click a row to set the target. */}
                              <FlaggedFieldScoreSection
                                item={item}
                                canonicalTable={effectiveCanonical || canonicalTarget}
                                topMatchLimit={3}
                                activeTarget={
                                  draft.mode === "existing" && draft.targetField
                                    ? draft.targetField
                                    : item.target_field ?? ""
                                }
                              >
                                {({ rows, scoresLoading }) => (
                                  <div className="mt-2">
                                    <MatchScoreTable
                                      rows={rows}
                                      scoresLoading={scoresLoading}
                                      selectable={isOverriding}
                                      onSelectSuggested={(field) => {
                                        setDecision(tbl, item.source_field, "override", item);
                                        patchColumnDraft(key, { mode: "existing", targetField: field });
                                      }}
                                    />
                                  </div>
                                )}
                              </FlaggedFieldScoreSection>
                              {isOverriding ? (
                                <div className="mt-2">
                                  <MigrationColumnOverride
                                    item={item}
                                    canonicalTable={
                                      toPlenumTableSlug(effectiveCanonical || canonicalTarget) ||
                                      effectiveCanonical ||
                                      canonicalTarget
                                    }
                                    draft={draft}
                                    onChange={(p) => patchColumnDraft(key, p)}
                                    compact={compact}
                                  />
                                </div>
                              ) : null}
                              <div className="flex items-center gap-3 mt-1.5 flex-wrap">
                                <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-violet-100 text-violet-700">
                                  {item.tier ?? "T2"}
                                </span>
                                <ConfidenceBar value={item.confidence ?? 0} />
                                {t1Snapshot ? (
                                  <button
                                    type="button"
                                    onClick={() => toggleRerunField(tbl, item.source_field)}
                                    className={`text-[11px] font-medium px-2 py-0.5 rounded-full border transition-colors ${
                                      markedRerun
                                        ? "bg-violet-600 text-white border-violet-600"
                                        : "bg-white text-violet-700 border-violet-200 hover:bg-violet-50"
                                    }`}
                                  >
                                    {markedRerun ? "Marked for re-run" : "Mark re-run"}
                                  </button>
                                ) : null}
                              </div>
                              {item.rationale ? (
                                <p className="text-xs text-slate-400 mt-1 truncate">{item.rationale}</p>
                              ) : null}
                            </div>

                            <div
                              className={`flex gap-1.5 flex-wrap ${compact ? "w-full justify-stretch" : "shrink-0 pt-0.5 justify-end"}`}
                            >
                              <button
                                type="button"
                                onClick={() => setDecision(tbl, item.source_field, "accept", item)}
                                className={`inline-flex items-center gap-1 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors ${
                                  decision === "accept"
                                    ? "bg-green-600 text-white"
                                    : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                                }`}
                              >
                                <CheckCircle size={12} />
                                Accept
                              </button>
                              <button
                                type="button"
                                onClick={() => setDecision(tbl, item.source_field, "override", item)}
                                className={`inline-flex items-center gap-1 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors ${
                                  decision === "override"
                                    ? "bg-amber-500 text-white"
                                    : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                                }`}
                              >
                                <Edit2 size={12} />
                                Override
                              </button>
                              <button
                                type="button"
                                onClick={() => setDecision(tbl, item.source_field, "reject", item)}
                                className={`inline-flex items-center gap-1 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors ${
                                  decision === "reject"
                                    ? "bg-red-600 text-white"
                                    : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                                }`}
                              >
                                <XCircle size={12} />
                                Reject
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {unmappedList.length > 0 ? (
                    <div className="border-t border-red-100 bg-red-50/30">
                      <p
                        className={`font-medium text-red-800 ${compact ? "px-3 py-1.5 text-[10px]" : "px-5 py-2 text-xs"}`}
                      >
                        Unmappable fields — choose skip, raw_metadata, or new column
                      </p>
                      <div className="divide-y divide-red-100">
                        {unmappedList.map((row, uidx) => {
                          // Canonical name resolved at the pre-semantic gate. The user's
                          // pinned/derived canonical (asset_id for asset_no, vendor_name
                          // for name) flows in via payload.column_canonical. Fall back to
                          // hydratedCanonicals (fetched live from migration state) for
                          // gates that were paused BEFORE column_canonical was added to
                          // the payload — then raw source when nothing else applies.
                          // Merge payload canonicals with hydrated ones — hydrated wins
                          // on conflict because it's read live from current state (which
                          // reflects all overrides applied so far), while the payload was
                          // frozen at gate-interrupt time.
                          const colCanonical: Record<string, string> = {
                            ...(payload.column_canonical || {}),
                            ...(hydratedCanonicals || {}),
                          };
                          const canonicalKey = `${tbl}.${row.sourceField}`;
                          const canonicalName =
                            colCanonical[canonicalKey]
                            ?? colCanonical[canonicalKey.toLowerCase()]
                            ?? Object.entries(colCanonical).find(([k]) => k.toLowerCase() === canonicalKey.toLowerCase())?.[1]
                            ?? null;
                          const displayCol = canonicalName || row.sourceField;
                          const renamed = !!canonicalName && canonicalName !== row.sourceField;
                          return (
                          <div
                            key={`${row.sourceField}-${uidx}`}
                            className={`flex flex-wrap items-center gap-2 ${compact ? "px-3 py-2" : "px-5 py-2.5"}`}
                          >
                            <div className="flex flex-col leading-tight shrink-0">
                              <span
                                className={`font-mono text-xs ${
                                  renamed ? "text-indigo-700" : "text-slate-800"
                                }`}
                                title={renamed ? `Canonical name from Step 2 — original column: ${row.sourceField}` : undefined}
                              >
                                {displayCol}
                              </span>
                              {renamed ? (
                                <span className="font-mono text-[9.5px] text-slate-400 mt-0.5">was {row.sourceField}</span>
                              ) : null}
                            </div>
                            <div className="flex gap-1 flex-wrap ml-auto">
                              {(["skip", "raw_metadata", "custom"] as const).map((act) => (
                                <button
                                  key={act}
                                  type="button"
                                  onClick={() =>
                                    setUnmappedRows((prev) => ({
                                      ...prev,
                                      [tbl]: (prev[tbl] ?? []).map((r, i) =>
                                        i === uidx ? { ...r, action: act } : r,
                                      ),
                                    }))
                                  }
                                  className={`rounded-full px-2 py-0.5 text-[10px] font-medium border ${
                                    row.action === act
                                      ? act === "skip"
                                        ? "bg-red-600 text-white border-red-600"
                                        : act === "raw_metadata"
                                          ? "bg-slate-600 text-white border-slate-600"
                                          : "bg-indigo-600 text-white border-indigo-600"
                                      : "bg-white text-slate-600 border-slate-200"
                                  }`}
                                >
                                  {act === "custom" ? "New column" : act}
                                </button>
                              ))}
                            </div>
                            {row.action === "custom" ? (() => {
                              // Existing columns on the chosen destination table (empty for
                              // brand-new tables). Used to (a) offer a "use existing column"
                              // dropdown suggesting columns that already exist on the dest,
                              // and (b) warn when the user's typed name would collide with
                              // an existing column.
                              const destTbl = newTableFlags[tbl]
                                ? null
                                : (canonicalTableBySource[tbl] || tbl);
                              const destColsByTable = payload.canonical_columns_by_table || {};
                              const destCols = destTbl
                                ? (destColsByTable[destTbl]
                                    ?? destColsByTable[destTbl.toLowerCase()]
                                    ?? Object.entries(destColsByTable).find(([k]) => k.toLowerCase() === destTbl.toLowerCase())?.[1]
                                    ?? [])
                                : [];
                              const _norm = (s: string) => (s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
                              // Default the new column name to the CANONICAL (e.g.
                              // 'asset_id' for WorkOrders_2.asset_no) so the DDL creates
                              // the right column on the destination — not the raw Excel
                              // column. User can still edit the input if they want.
                              const typed = row.customColumnName ?? displayCol;
                              const collidesWith = destCols.find((c) => _norm(c) === _norm(typed));
                              return (
                                <div className="w-full mt-1 flex flex-wrap items-center gap-1.5 pl-1">
                                  <span className="text-[10px] text-slate-500">→ create column</span>
                                  <input
                                    value={typed}
                                    onChange={(e) =>
                                      setUnmappedRows((prev) => ({
                                        ...prev,
                                        [tbl]: (prev[tbl] ?? []).map((r, i) =>
                                          i === uidx ? { ...r, customColumnName: e.target.value } : r,
                                        ),
                                      }))
                                    }
                                    placeholder="column name"
                                    spellCheck={false}
                                    className={`rounded border bg-white px-1.5 py-0.5 text-[11px] font-mono w-36 focus:outline-none focus:ring-2 ${
                                      collidesWith
                                        ? "border-amber-400 focus:ring-amber-400"
                                        : "border-indigo-200 focus:ring-indigo-400"
                                    }`}
                                  />
                                  <select
                                    value={row.dataType ?? "VARCHAR(255)"}
                                    onChange={(e) =>
                                      setUnmappedRows((prev) => ({
                                        ...prev,
                                        [tbl]: (prev[tbl] ?? []).map((r, i) =>
                                          i === uidx ? { ...r, dataType: e.target.value } : r,
                                        ),
                                      }))
                                    }
                                    className="rounded border border-slate-200 bg-white px-1.5 py-0.5 text-[11px]"
                                  >
                                    {DDL_DATA_TYPES.map((t) => (
                                      <option key={t} value={t}>
                                        {t}
                                      </option>
                                    ))}
                                  </select>
                                  <span className="text-[9px] text-slate-400">
                                    in{" "}
                                    {newTableFlags[tbl]
                                      ? `${newTableNames[tbl] || "new table"} (new)`
                                      : canonicalTableBySource[tbl] || tbl}
                                  </span>
                                  {destCols.length > 0 ? (
                                    <div className="basis-full mt-1 flex flex-wrap items-center gap-1.5">
                                      <span className="text-[10px] text-slate-500">
                                        or use existing column:
                                      </span>
                                      <select
                                        value=""
                                        onChange={(e) => {
                                          const pick = e.target.value;
                                          if (!pick) return;
                                          setUnmappedRows((prev) => ({
                                            ...prev,
                                            [tbl]: (prev[tbl] ?? []).map((r, i) =>
                                              i === uidx ? { ...r, customColumnName: pick } : r,
                                            ),
                                          }));
                                        }}
                                        className="rounded border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] font-mono"
                                      >
                                        <option value="">— pick from {destTbl} —</option>
                                        {destCols.map((c) => (
                                          <option key={c} value={c}>{c}</option>
                                        ))}
                                      </select>
                                    </div>
                                  ) : null}
                                  {collidesWith ? (
                                    <div className="basis-full mt-0.5 text-[10px] text-amber-700">
                                      ⚠ <code className="font-mono">{collidesWith}</code> already exists on{" "}
                                      <code className="font-mono">{destTbl}</code> — picking it from the list above
                                      will map this field to the existing column instead of creating a duplicate.
                                    </div>
                                  ) : null}
                                </div>
                              );
                            })() : null}
                          </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="mb-4">
        <MigrationNewTablesSection tables={newTableDefs} onChange={setNewTableDefs} compact={compact} />
      </div>

      {error ? (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          <div>{error}</div>
          <button
            type="button"
            onClick={() => setError(null)}
            className="mt-2 inline-flex items-center gap-1 rounded bg-red-600 px-2 py-1 text-xs font-medium text-white hover:bg-red-700"
          >
            Dismiss
          </button>
        </div>
      ) : null}

      <div className={`flex gap-2 ${compact ? "flex-col" : "flex-wrap items-center gap-3"}`}>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={isPending || isAdvancing || isPreflighting}
          className={`inline-flex items-center justify-center gap-2 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors ${compact ? "px-4 py-2.5 text-sm w-full" : "px-8 py-3 text-base"}`}
        >
          {isPending || isAdvancing || isPreflighting ? (
            <>
              <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              {isPreflighting ? "Checking status…" : "Submitting…"}
            </>
          ) : (
            <>
              <CheckCircle size={18} />
              Submit mappings — {countAccepted} accepted · {countRejected} rejected · {countOverridden}{" "}
              overridden
            </>
          )}
        </button>
        {allowAdvanceOnly ? (
          <button
            type="button"
            onClick={handleAdvanceOnly}
            disabled={isAdvancing || isPending}
            className={`inline-flex items-center justify-center gap-2 bg-white border border-slate-300 text-slate-700 font-medium rounded-lg hover:bg-slate-50 disabled:opacity-50 transition-colors ${compact ? "px-4 py-2.5 text-sm w-full" : "px-6 py-3 text-base"}`}
          >
            {compact ? "Continue only" : "Continue without submitting"}
          </button>
        ) : null}
      </div>
    </div>
  );
}
