"use client";
import { useState, useRef, useEffect, useMemo } from "react";
import {
  CheckCircle,
  XCircle,
  Edit3,
  Archive,
  PlusCircle,
  Trash2,
  ChevronDown,
  ChevronUp,
  AlertTriangle,
  Wand2,
} from "lucide-react";
import { cn } from "@/utils/cn";
import {
  useMigrationAdvance,
  useMigrationGateFieldMapping,
  schemaMapperApi,
  type MigrationStatus,
  type MigrationFieldMappingGatePayload,
  type MigrationFlaggedFieldItem,
  type MigrationUnmappedFieldItem,
  type MigrationGateFieldMappingDecision,
  type MigrationGateFieldMappingUnmappedDecision,
  type MigrationGateFieldMappingRequest,
  type MigrationPreSemanticGatePayload,
} from "../../../chat-api";
import { normalizeFieldMappingGatePayload } from "../migration-gate-state";
import {
  applyFieldMappingDraftToUiState,
  buildCanonicalTableBySource,
  clearFieldMappingDraft,
  loadFieldMappingDraftEnvelope,
  normalizeDraftForPayload,
  waitForFieldMappingGate,
  type FieldMappingDraftEnvelope,
} from "../migration-field-mapping-draft";
import {
  MigrationColumnOverride,
  emptyColumnOverrideDraft,
  type ColumnOverrideDraft,
} from "../migration-column-override";
import { MigrationCanonicalTableSelect } from "../migration-canonical-table-select";
import { getSuggestedTarget } from "../migration-mapping-utils";
import { ScoreBreakdownChips } from "../migration-metadata-view";
import { FlaggedFieldScoreSection, MatchScoreTable } from "../flagged-field-match-scores";

const DATA_TYPES = [
  "VARCHAR(255)", "VARCHAR(100)", "VARCHAR(50)",
  "TEXT", "INTEGER", "BIGINT", "DECIMAL(10,2)",
  "BOOLEAN", "TIMESTAMPTZ", "DATE", "JSONB", "UUID",
];

interface Props {
  migrationId: string;
  payload: MigrationFieldMappingGatePayload;
  /** Tier-2 semantic draft from parent (sessionStorage). */
  fieldMappingDraft?: FieldMappingDraftEnvelope | null;
  onSubmitted: () => void;
  /** Refetch migration status without closing the gate (no dismiss). */
  onReloadStatus?: () => void;
  pipelineStatus?: MigrationStatus | null;
  submitReady?: boolean;
  submitBlockedReason?: string | null;
  /** When the server skipped field_mapping gate, allow continue via advance instead of POST gate. */
  deferUntilAwaitingReview?: boolean;
  onDeferredProceed?: () => void;
  /** Apply saved Tier-2 draft: persist, open gate, submit (preferred over advance-only). */
  onApplyTier2Continue?: (
    body: MigrationGateFieldMappingRequest,
  ) => void | Promise<void>;
  t1Snapshot?: {
    payload: MigrationPreSemanticGatePayload;
    decisions: Record<string, Array<{ source_field: string; decision: "approve" | "semantic" }>>;
  };
  /** Orchestrator right rail — compact controls, full-width. */
  embeddedRail?: boolean;
}

type FlaggedAction = "accept" | "reject" | "override";
type UnmappedAction = "custom" | "raw_metadata" | "skip";

type FlaggedDecision = { action: FlaggedAction; overrideTarget: string };

type CustomDDL = {
  sourceField: string;
  targetTable: string;
  colName: string;
  dataType: string;
  isNewTable: boolean;
  newTableName: string;
  newTablePk: string;
  nullable: boolean;
};

type UnmappedRow = { action: UnmappedAction; sourceField: string; ddl: CustomDDL | null };

type NewTableColumn = { column_name: string; data_type: string; nullable: boolean };
type NewTableDef = { id: string; table_name: string; pk_col: string; columns: NewTableColumn[] };

function defaultTargetTable(sourceTable: string, canonicalTables: string[]): string {
  const key = sourceTable.trim();
  if (!key) return canonicalTables[0] ?? "";
  if (canonicalTables.includes(key)) return key;
  return key || (canonicalTables[0] ?? "");
}

/**
 * snake_case a source column name into a clean SQL identifier. Mirrors
 * gate-pre-semantic.tsx's `toSnakeCase` so a column landing at this gate gets
 * the same default name it would have gotten at the prior gate.
 *
 * Examples:
 *   "Trip ID"               → "trip_id"
 *   "Travel Date"           → "travel_date"
 *   "Distance (km)"         → "distance_km"
 *   "Total Trip Cost (AED)" → "total_trip_cost_aed"
 *   "Allowance / Per Diem (AED)" → "allowance_per_diem_aed"
 *
 * Without this, parenthesised units and slashes leak straight into the SQL
 * column name (e.g. "allowance_/_per_diem_(aed)"), which PostgreSQL rejects
 * and which is also what showed up in the create-column input as the raw
 * "Allowance / Per Diem (AED" text — that's the parenthesis-in-column-name
 * bug the user flagged.
 */
function snakeCaseFieldName(s: string): string {
  const raw = (s ?? "").toString();
  // Lift parenthesised tokens into the name ("Distance (km)" → "Distance km")
  const lifted = raw.replace(/\(([^)]+)\)/g, " $1 ");
  // CamelCase / lowerUpper boundary
  const split = lifted.replace(/([a-z0-9])([A-Z])/g, "$1_$2");
  return (
    split
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/_+/g, "_")
      .replace(/^_+|_+$/g, "") || "col"
  );
}

function emptyCustomDDL(
  sourceField: string,
  canonicalTables: string[],
  sourceTable = "",
): CustomDDL {
  return {
    sourceField,
    targetTable: defaultTargetTable(sourceTable, canonicalTables),
    colName: snakeCaseFieldName(sourceField),
    dataType: "VARCHAR(255)",
    isNewTable: false,
    newTableName: "",
    newTablePk: "id",
    nullable: true,
  };
}

/** Best-guess SQL type for an unmapped column, from its name + sample values. */
function inferColumnType(field: string, samples?: string[]): string {
  const f = (field || "").toLowerCase();
  if (/(timestamp|_at\b|datetime|created|updated|modified)/.test(f)) return "TIMESTAMP";
  if (/(date|_dt\b|dob)/.test(f)) return "DATE";
  if (/(is_|^is\b|bool|flag|active|enabled)/.test(f)) return "BOOLEAN";
  if (/(amount|price|cost|total|rate|qty|quantity|km|hrs|aed|distance|duration|balance|number|count)/.test(f))
    return "NUMERIC";
  if (/(_id\b|^id$)/.test(f)) return "INTEGER";
  const s = (samples || []).map((v) => String(v ?? "").trim()).filter(Boolean);
  if (s.length && s.every((v) => /^-?\d+(\.\d+)?$/.test(v))) return "NUMERIC";
  return "VARCHAR(255)";
}

function emptyNewTable(): NewTableDef {
  return {
    id: `nt_${Date.now()}_${Math.random().toString(16).slice(2)}`,
    table_name: "",
    pk_col: "id",
    columns: [{ column_name: "", data_type: "VARCHAR(255)", nullable: true }],
  };
}

function ActionBtn({
  label, icon, active, activeColor, onClick,
}: {
  label: string; icon: React.ReactNode; active: boolean; activeColor: string; onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex items-center gap-1 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors ${
        active ? `${activeColor} text-white` : "bg-slate-100 text-slate-600 hover:bg-slate-200"
      }`}
    >
      {icon}{label}
    </button>
  );
}

function ConfidencePill({ confidence }: { confidence: number }) {
  const pct = Math.round(confidence * 100);
  const color = pct >= 85 ? "text-green-700 bg-green-100" : pct >= 65 ? "text-amber-700 bg-amber-100" : "text-red-600 bg-red-100";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-mono ${color}`}>
      {pct}%
    </span>
  );
}

function CustomDDLForm({
  ddl,
  canonicalTables,
  onChange,
}: {
  ddl: CustomDDL;
  canonicalTables: string[];
  onChange: (p: Partial<CustomDDL>) => void;
}) {
  const effectiveTable = ddl.isNewTable ? (ddl.newTableName || "…") : (ddl.targetTable || "…");
  const col = ddl.colName || "…";
  const nullStr = ddl.nullable ? "" : " NOT NULL";
  const sqlPreview = ddl.isNewTable
    ? `CREATE TABLE ${effectiveTable} (\n  ${ddl.newTablePk || "id"} UUID PRIMARY KEY DEFAULT gen_random_uuid(),\n  ${col} ${ddl.dataType}${nullStr}\n);`
    : `ALTER TABLE ${effectiveTable}\n  ADD COLUMN ${col} ${ddl.dataType}${nullStr};`;

  return (
    <div className="rounded-xl bg-indigo-50 border border-indigo-200 p-4 space-y-3 mt-1">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-indigo-700 flex items-center gap-1.5">
          <PlusCircle size={12} />Column definition
        </p>
        <label className="flex items-center gap-2 cursor-pointer">
          <span className="text-xs text-slate-600">{ddl.isNewTable ? "New table" : "Existing table"}</span>
          <div
            className={`relative w-9 h-5 rounded-full transition-colors cursor-pointer ${ddl.isNewTable ? "bg-indigo-600" : "bg-slate-300"}`}
            onClick={() => onChange({ isNewTable: !ddl.isNewTable })}
          >
            <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${ddl.isNewTable ? "translate-x-4" : "translate-x-0.5"}`} />
          </div>
        </label>
      </div>

      <div className="grid grid-cols-2 gap-3">
        {ddl.isNewTable ? (
          <>
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">New table name</label>
              <input
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-mono transition-colors hover:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                value={ddl.newTableName}
                onChange={(e) => onChange({ newTableName: e.target.value })}
                placeholder="my_new_table"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">Primary key column</label>
              <input
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-mono transition-colors hover:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                value={ddl.newTablePk}
                onChange={(e) => onChange({ newTablePk: e.target.value })}
                placeholder="id"
              />
            </div>
          </>
        ) : (
          <div className="col-span-2">
            <label className="block text-xs font-medium text-slate-700 mb-1">Target table</label>
            {canonicalTables.length ? (
              <select
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-mono transition-colors hover:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                value={ddl.targetTable}
                onChange={(e) => onChange({ targetTable: e.target.value })}
              >
                <option value="">— select —</option>
                {canonicalTables.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            ) : (
              <input
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-mono transition-colors hover:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                value={ddl.targetTable}
                onChange={(e) => onChange({ targetTable: e.target.value })}
                placeholder="assets"
              />
            )}
          </div>
        )}
        <div>
          <label className="block text-xs font-medium text-slate-700 mb-1">Column name</label>
          <input
            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-mono transition-colors hover:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            value={ddl.colName}
            onChange={(e) => onChange({ colName: e.target.value })}
            placeholder="my_column"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-700 mb-1">Data type</label>
          <select
            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-mono transition-colors hover:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            value={ddl.dataType}
            onChange={(e) => onChange({ dataType: e.target.value })}
          >
            {DATA_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      </div>

      <label className="flex items-center gap-3 cursor-pointer">
        <div
          className={`relative w-9 h-5 rounded-full transition-colors ${ddl.nullable ? "bg-indigo-600" : "bg-slate-300"}`}
          onClick={() => onChange({ nullable: !ddl.nullable })}
        >
          <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${ddl.nullable ? "translate-x-4" : "translate-x-0.5"}`} />
        </div>
        <span className="text-xs font-medium text-slate-700">Nullable</span>
      </label>

      <pre className="rounded-lg bg-white border border-indigo-200 px-3 py-2 font-mono text-xs text-slate-600 leading-relaxed overflow-auto">
        {sqlPreview}
      </pre>
    </div>
  );
}

export default function GateFieldMapping({
  migrationId,
  payload,
  fieldMappingDraft = null,
  onSubmitted,
  onReloadStatus,
  pipelineStatus: _pipelineStatus,
  submitReady = true,
  submitBlockedReason = null,
  deferUntilAwaitingReview = false,
  onDeferredProceed,
  onApplyTier2Continue,
  t1Snapshot,
  embeddedRail = false,
}: Props) {
  const compact = embeddedRail;
  const STEP_PAUSED_STATUS_RE = /status\s*:?\s*step_paused/i;
  const FAILED_STATUS_RE = /status\s*:?\s*(failed|ddl_failed)/i;
  const REVIEW_READY_STATUS_RE = /status\s*:?\s*(awaiting_review|running)/i;
  const GATE_MISMATCH_RE = /gate mismatch/i;
  const payloadRec = payload as unknown as Record<string, unknown>;
  const configuredTopMatches =
    (typeof payloadRec.top_match_count === "number" && Number.isFinite(payloadRec.top_match_count) && payloadRec.top_match_count) ||
    (typeof payloadRec.suggestion_limit === "number" && Number.isFinite(payloadRec.suggestion_limit) && payloadRec.suggestion_limit) ||
    3;
  const topMatchLimit = Math.min(5, Math.max(1, Math.round(configuredTopMatches)));
  const normalized = normalizeFieldMappingGatePayload(payloadRec);
  const flaggedByTable = normalized.flagged_by_table as Record<string, MigrationFlaggedFieldItem[]>;
  const unmappedByTable = normalized.unmapped_by_table as Record<string, MigrationUnmappedFieldItem[]>;
  const canonicalTables =
    normalized.existing_canonical_tables.length > 0
      ? normalized.existing_canonical_tables
      : (payload.existing_canonical_tables ?? []);
  // Target tables the user chose at the pre-semantic gate → default this gate's
  // comparison to them instead of the source-sheet name (retain the selection).
  const tableRouting = normalized.table_routing;

  const flaggedTables = Object.keys(flaggedByTable);
  const unmappedTables = Object.keys(unmappedByTable);

  const flaggedFingerprint = useMemo(
    () =>
      JSON.stringify(
        Object.entries(flaggedByTable).map(([tbl, items]) => [
          tbl,
          (items ?? []).map((i) => i.source_field),
        ]),
      ),
    [flaggedByTable],
  );

  const draftEnvelope = fieldMappingDraft ?? loadFieldMappingDraftEnvelope(migrationId);
  const savedDraft = draftEnvelope?.body ?? null;
  const draftMeta = draftEnvelope?.meta;
  const draftUi = savedDraft
    ? applyFieldMappingDraftToUiState(savedDraft, flaggedByTable, unmappedByTable, draftMeta)
    : null;

  const initialCanonicalTables = buildCanonicalTableBySource(
    flaggedByTable,
    unmappedByTable,
    draftUi?.canonicalTableBySource,
    tableRouting,
  );

  const [canonicalTableBySource, setCanonicalTableBySource] = useState<Record<string, string>>(
    () => initialCanonicalTables,
  );

  const [flaggedDecisions, setFlaggedDecisions] = useState<Record<string, FlaggedDecision[]>>(() => {
    if (draftUi?.flaggedDecisions) return draftUi.flaggedDecisions;
    const out: Record<string, FlaggedDecision[]> = {};
    for (const [tbl, items] of Object.entries(flaggedByTable)) {
      if (!Array.isArray(items)) continue;
      out[tbl] = (items as MigrationFlaggedFieldItem[]).map((item) => {
        const suggestedTarget = getSuggestedTarget(item);
        // Honor a carried target_field (a target already chosen at the prior gate, e.g.
        // asset_id → id) so the previously-selected value shows instead of resetting to
        // the fresh suggestion. Mirrors the submit logic (item.target_field ?? suggested).
        const carried = (item.target_field ?? "").trim();
        const target = carried || suggestedTarget;
        return {
          action: target ? "accept" : "override",
          overrideTarget: target,
        };
      });
    }
    return out;
  });

  const [unmappedRows, setUnmappedRows] = useState<Record<string, UnmappedRow[]>>(() => {
    if (draftUi?.unmappedRows) return draftUi.unmappedRows;
    const out: Record<string, UnmappedRow[]> = {};
    for (const [tbl, items] of Object.entries(unmappedByTable)) {
      if (!Array.isArray(items)) continue;
      // Default unmapped fields to "create new column" (custom DDL) with a snake_case
      // name + inferred SQL type, so nothing is silently dropped. The user can still
      // switch any field to raw_metadata or skip.
      out[tbl] = (items as MigrationUnmappedFieldItem[]).map((item) => ({
        action: "custom" as const,
        sourceField: item.source_field,
        ddl: {
          ...emptyCustomDDL(item.source_field, canonicalTables, tbl),
          // Default to the destination table chosen at the pre-semantic gate.
          targetTable: initialCanonicalTables[tbl] || defaultTargetTable(tbl, canonicalTables),
          dataType: inferColumnType(item.source_field, item.sample_values),
        },
      }));
    }
    return out;
  });

  // Hydrate the pin-aware canonical column names from live workflow state (e.g. Vendors.id →
  // asset_id when the user pinned group G2 → asset_id) and default each unmapped field's NEW-column
  // name to its canonical instead of the raw source field. Only patches rows the user hasn't
  // renamed (still on the snake_case source default), so an explicit edit / restored draft wins.
  const [hydratedCanonicals, setHydratedCanonicals] = useState<Record<string, string> | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await schemaMapperApi.getMigrationStateCanonicals(migrationId);
        if (!cancelled && res?.column_canonical) setHydratedCanonicals(res.column_canonical);
      } catch {
        // Best-effort — the form still works with the source-field default name.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [migrationId]);
  useEffect(() => {
    if (!hydratedCanonicals) return;
    setUnmappedRows((prev) => {
      let changed = false;
      const next: Record<string, UnmappedRow[]> = {};
      for (const [tbl, rows] of Object.entries(prev)) {
        next[tbl] = rows.map((row) => {
          const canon = hydratedCanonicals[`${tbl}.${row.sourceField}`];
          const canonCol = canon ? snakeCaseFieldName(canon) : "";
          if (
            row.ddl &&
            canonCol &&
            canonCol !== row.ddl.colName &&
            row.ddl.colName === snakeCaseFieldName(row.sourceField)
          ) {
            changed = true;
            return { ...row, ddl: { ...row.ddl, colName: canonCol } };
          }
          return row;
        });
      }
      return changed ? next : prev;
    });
  }, [hydratedCanonicals]);

  const [newTableDefs, setNewTableDefs] = useState<NewTableDef[]>(() => draftUi?.newTableDefs ?? []);

  const [expandedFlagged, setExpandedFlagged] = useState<Set<string>>(new Set(flaggedTables));
  const [expandedUnmapped, setExpandedUnmapped] = useState<Set<string>>(new Set(unmappedTables));
  const [columnDrafts, setColumnDrafts] = useState<Record<string, ColumnOverrideDraft>>({});
  const [activeTab, setActiveTab] = useState<"flagged" | "unmapped">("flagged");
  const [error, setError] = useState<string | null>(null);
  const [isPreflighting, setIsPreflighting] = useState(false);
  const lastSubmitRef = useRef<{ flagged: Record<string, MigrationGateFieldMappingDecision[]>; unmapped: Record<string, MigrationGateFieldMappingUnmappedDecision[]> } | null>(
    savedDraft ?? null,
  );
  const autoSubmitDraftRef = useRef(false);

  const draftFingerprint = useMemo(
    () => JSON.stringify(draftEnvelope ?? null),
    [draftEnvelope],
  );

  useEffect(() => {
    const envelope = fieldMappingDraft ?? loadFieldMappingDraftEnvelope(migrationId);
    const draft = envelope?.body;
    if (!draft) return;
    const applied = applyFieldMappingDraftToUiState(
      draft,
      flaggedByTable,
      unmappedByTable,
      envelope?.meta,
    );
    lastSubmitRef.current = normalizeDraftForPayload(draft, flaggedByTable, unmappedByTable);
    setFlaggedDecisions(applied.flaggedDecisions);
    setUnmappedRows(applied.unmappedRows);
    if (applied.newTableDefs.length) setNewTableDefs(applied.newTableDefs);
    setCanonicalTableBySource(
      buildCanonicalTableBySource(flaggedByTable, unmappedByTable, applied.canonicalTableBySource),
    );
  }, [migrationId, flaggedFingerprint, unmappedByTable, fieldMappingDraft, draftFingerprint]);

  const { mutate: advance, isPending: isAdvancing } = useMigrationAdvance({
    onSuccess: () => {
      setError(null);
      if (lastSubmitRef.current) {
        submitGate({ migrationId, body: lastSubmitRef.current });
      }
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

  const { mutate: submitGate, isPending } = useMigrationGateFieldMapping({
    onSuccess: () => {
      clearFieldMappingDraft(migrationId);
      autoSubmitDraftRef.current = false;
      onSubmitted();
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Submission failed";
      autoSubmitDraftRef.current = false;
      if (GATE_MISMATCH_RE.test(msg)) {
        setError(
          "The server is not at the field-mapping gate yet. Your Tier-2 choices are saved — submit again when the gate opens.",
        );
        return;
      }
      setError(msg);
      if (STEP_PAUSED_STATUS_RE.test(msg)) advance({ migrationId });
      if (FAILED_STATUS_RE.test(msg)) onSubmitted();
    },
  });

  useEffect(() => {
    const envelope = fieldMappingDraft ?? loadFieldMappingDraftEnvelope(migrationId);
    const draft = envelope?.body;
    if (!draft || !submitReady || autoSubmitDraftRef.current) return;
    autoSubmitDraftRef.current = true;
    setError(null);
    const body = normalizeDraftForPayload(draft, flaggedByTable, unmappedByTable);
    submitGate({ migrationId, body });
  }, [migrationId, submitReady, submitGate, flaggedFingerprint, flaggedByTable, unmappedByTable, fieldMappingDraft]);

  function setFlaggedAction(tbl: string, idx: number, action: FlaggedAction) {
    setFlaggedDecisions((prev) => {
      const rows = [...(prev[tbl] ?? [])];
      rows[idx] = { ...rows[idx], action };
      return { ...prev, [tbl]: rows };
    });
  }

  function flaggedFieldKey(tbl: string, sourceField: string) {
    return `${tbl}.${sourceField}`;
  }

  function setOverrideTarget(tbl: string, idx: number, val: string) {
    setFlaggedDecisions((prev) => {
      const rows = [...(prev[tbl] ?? [])];
      rows[idx] = { ...rows[idx], overrideTarget: val };
      return { ...prev, [tbl]: rows };
    });
  }

  function patchColumnDraft(key: string, patch: Partial<ColumnOverrideDraft>) {
    setColumnDrafts((prev) => ({
      ...prev,
      [key]: { ...(prev[key] ?? emptyColumnOverrideDraft()), ...patch },
    }));
    if (patch.targetField !== undefined) {
      const [tbl, ...rest] = key.split(".");
      const field = rest.join(".");
      const items = flaggedByTable[tbl] ?? [];
      const idx = items.findIndex((i) => i.source_field === field);
      if (idx >= 0) setOverrideTarget(tbl, idx, patch.targetField);
    }
    if (patch.newColumnName !== undefined && patch.mode === "new_column") {
      const [tbl, ...rest] = key.split(".");
      const field = rest.join(".");
      const items = flaggedByTable[tbl] ?? [];
      const idx = items.findIndex((i) => i.source_field === field);
      if (idx >= 0) setOverrideTarget(tbl, idx, patch.newColumnName);
    }
  }

  function setUnmappedAction(tbl: string, idx: number, action: UnmappedAction) {
    setUnmappedRows((prev) => {
      const rows = [...(prev[tbl] ?? [])];
      const row = rows[idx];
      rows[idx] = {
        ...row,
        action,
        ddl: action === "custom" ? (row.ddl ?? emptyCustomDDL(row.sourceField, canonicalTables, tbl)) : null,
      };
      return { ...prev, [tbl]: rows };
    });
  }

  function patchDDL(tbl: string, idx: number, patch: Partial<CustomDDL>) {
    setUnmappedRows((prev) => {
      const rows = [...(prev[tbl] ?? [])];
      const row = rows[idx];
      rows[idx] = {
        ...row,
        ddl: { ...(row.ddl ?? emptyCustomDDL(row.sourceField, canonicalTables, tbl)), ...patch },
      };
      return { ...prev, [tbl]: rows };
    });
  }

  function addExtraCol(tbl: string) {
    setUnmappedRows((prev) => ({
      ...prev,
      [tbl]: [...(prev[tbl] ?? []), { action: "custom", sourceField: "", ddl: emptyCustomDDL("", canonicalTables, tbl) }],
    }));
    setExpandedUnmapped((prev) => new Set([...prev, tbl]));
  }

  function removeExtraRow(tbl: string, idx: number) {
    setUnmappedRows((prev) => {
      const rows = [...(prev[tbl] ?? [])];
      rows.splice(idx, 1);
      return { ...prev, [tbl]: rows };
    });
  }

  function patchSourceField(tbl: string, idx: number, val: string) {
    setUnmappedRows((prev) => {
      const rows = [...(prev[tbl] ?? [])];
      const row = rows[idx];
      const nextRow: UnmappedRow = { ...row, sourceField: val };
      if (nextRow.ddl) {
        nextRow.ddl = { ...nextRow.ddl, sourceField: val };
      }
      rows[idx] = nextRow;
      return { ...prev, [tbl]: rows };
    });
  }

  function autoAddColumnForAll() {
    setUnmappedRows((prev) => {
      const next: Record<string, UnmappedRow[]> = { ...prev };
      for (const tbl of Object.keys(next)) {
        next[tbl] = next[tbl].map((r) => ({
          ...r,
          action: "custom",
          ddl: r.ddl ?? emptyCustomDDL(r.sourceField, canonicalTables, tbl),
        }));
      }
      return next;
    });
  }

  function addNewTable() {
    setNewTableDefs((prev) => [...prev, emptyNewTable()]);
  }

  function removeNewTable(id: string) {
    setNewTableDefs((prev) => prev.filter((t) => t.id !== id));
  }

  function patchNewTable(id: string, patch: Partial<NewTableDef>) {
    setNewTableDefs((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)));
  }

  function addNewTableColumn(id: string) {
    setNewTableDefs((prev) => prev.map((t) => (
      t.id === id ? { ...t, columns: [...t.columns, { column_name: "", data_type: "VARCHAR(255)", nullable: true }] } : t
    )));
  }

  function updateNewTableColumn(id: string, idx: number, patch: Partial<NewTableColumn>) {
    setNewTableDefs((prev) => prev.map((t) => {
      if (t.id !== id) return t;
      const cols = t.columns.map((c, i) => (i === idx ? { ...c, ...patch } : c));
      return { ...t, columns: cols };
    }));
  }

  function removeNewTableColumn(id: string, idx: number) {
    setNewTableDefs((prev) => prev.map((t) => {
      if (t.id !== id) return t;
      return { ...t, columns: t.columns.filter((_, i) => i !== idx) };
    }));
  }

  function buildSubmissionBody(): {
    body: { flagged: Record<string, MigrationGateFieldMappingDecision[]>; unmapped: Record<string, MigrationGateFieldMappingUnmappedDecision[]> };
    error: string | null;
  } {
    const missingTargets: Array<{ table: string; source_field: string }> = [];
    const missingOverrides: Array<{ table: string; source_field: string }> = [];
    const invalidCustom: Array<{ table: string; source_field: string }> = [];

    const flagged: Record<string, MigrationGateFieldMappingDecision[]> = {};
    for (const [tbl, items] of Object.entries(flaggedByTable)) {
      flagged[tbl] = (items as MigrationFlaggedFieldItem[]).map((item, idx) => {
        const suggestedTarget = getSuggestedTarget(item);
        const effectiveSuggested = item.target_field ?? suggestedTarget;
        const d = flaggedDecisions[tbl]?.[idx] ?? { action: effectiveSuggested ? "accept" : "override", overrideTarget: suggestedTarget };
        const draft =
          columnDrafts[flaggedFieldKey(tbl, item.source_field)] ??
          emptyColumnOverrideDraft(d.overrideTarget || suggestedTarget);
        const overrideTarget =
          d.action === "override"
            ? draft.mode === "new_column"
              ? draft.newColumnName.trim()
              : draft.targetField.trim() || d.overrideTarget
            : d.overrideTarget;

        if (d.action === "accept" && !effectiveSuggested) {
          if (!overrideTarget) missingTargets.push({ table: tbl, source_field: item.source_field });
        }
        if (d.action === "override" && !overrideTarget) {
          missingOverrides.push({ table: tbl, source_field: item.source_field });
        }

        const shouldForceOverride = d.action === "accept" && !effectiveSuggested;
        // "Override → New column (DDL)" — the chosen target is a column that does NOT exist yet.
        // Carry the DDL so the backend creates it on the destination table (otherwise the writer
        // drops it as an unknown column on a core table and the data is lost).
        const isNewColumn = d.action === "override" && draft.mode === "new_column" && !!overrideTarget;
        return {
          action: shouldForceOverride ? "override" : d.action,
          source_field: item.source_field,
          target_field: d.action === "override" || shouldForceOverride
            ? (overrideTarget || null)
            : (effectiveSuggested || null),
          rationale: null,
          ...(isNewColumn
            ? { is_new_column: true, data_type: draft.dataType || "VARCHAR(255)", nullable: draft.nullable }
            : {}),
        };
      });
    }

    const unmappedReq: Record<string, MigrationGateFieldMappingUnmappedDecision[]> = {};
    const allTables = new Set([...unmappedTables, ...Object.keys(unmappedRows)]);
    for (const tbl of allTables) {
      const baseRows = unmappedRows[tbl] ?? [];
      unmappedReq[tbl] = baseRows
        .filter((r) => r.sourceField.trim().length > 0)
        .map((r) => {
          if (r.action === "custom" && r.ddl) {
            const ddl = r.ddl;
            const effectiveTarget = ddl.isNewTable ? ddl.newTableName : ddl.targetTable;
            if (!effectiveTarget || !ddl.colName || !ddl.dataType) invalidCustom.push({ table: tbl, source_field: r.sourceField });
            return {
              action: "custom",
              source_field: r.sourceField,
              target_table: effectiveTarget || null,
              custom_column_name: ddl.colName || null,
              data_type: ddl.dataType || null,
              nullable: ddl.nullable,
              is_new_table: ddl.isNewTable,
              new_table_name: ddl.isNewTable ? (ddl.newTableName || null) : null,
              new_table_pk: ddl.isNewTable ? (ddl.newTablePk || null) : null,
            };
          }
          return {
            action: r.action,
            source_field: r.sourceField,
            target_table: null,
            custom_column_name: null,
            data_type: null,
          };
        });
    }

    for (const nt of newTableDefs) {
      if (!nt.table_name.trim()) continue;
      const key = `_new_table_${nt.table_name}`;
      unmappedReq[key] = nt.columns
        .filter((c) => c.column_name.trim())
        .map((c) => ({
          action: "custom",
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

    if (missingTargets.length || missingOverrides.length || invalidCustom.length) {
      const lines: string[] = [];
      if (missingTargets.length) {
        const sample = missingTargets.slice(0, 4).map((x) => `${x.table}.${x.source_field}`).join(", ");
        lines.push(`Select a target field for: ${sample}${missingTargets.length > 4 ? "…" : ""}`);
      }
      if (missingOverrides.length) {
        const sample = missingOverrides.slice(0, 4).map((x) => `${x.table}.${x.source_field}`).join(", ");
        lines.push(`Override target is required for: ${sample}${missingOverrides.length > 4 ? "…" : ""}`);
      }
      if (invalidCustom.length) {
        const sample = invalidCustom.slice(0, 4).map((x) => `${x.table}.${x.source_field}`).join(", ");
        lines.push(`Fill target table + column name + data type for: ${sample}${invalidCustom.length > 4 ? "…" : ""}`);
      }
      return { body: { flagged, unmapped: unmappedReq }, error: lines.join(" • ") };
    }

    return { body: { flagged, unmapped: unmappedReq }, error: null };
  }

  function handleSubmit() {
    const built = buildSubmissionBody();
    if (built.error) {
      setError(built.error);
      return;
    }

    if (!submitReady && onApplyTier2Continue) {
      const body = normalizeDraftForPayload(built.body, flaggedByTable, unmappedByTable);
      lastSubmitRef.current = body;
      setError(null);
      setIsPreflighting(true);
      void Promise.resolve(onApplyTier2Continue(body))
        .catch((err: unknown) => {
          setError(err instanceof Error ? err.message : "Failed to apply Tier-2 choices");
        })
        .finally(() => setIsPreflighting(false));
      return;
    }

    if (!submitReady && deferUntilAwaitingReview && onDeferredProceed) {
      lastSubmitRef.current = built.body;
      setError(null);
      onDeferredProceed();
      return;
    }

    if (!submitReady && savedDraft) {
      setError(null);
      setIsPreflighting(true);
      const body = normalizeDraftForPayload(savedDraft, flaggedByTable, unmappedByTable);
      lastSubmitRef.current = body;
      void waitForFieldMappingGate(migrationId, 20, 1500).then((wait) => {
        setIsPreflighting(false);
        if (wait.outcome === "field_mapping_open") {
          submitGate({ migrationId, body });
          return;
        }
        if (
          (wait.outcome === "skipped" || wait.outcome === "timeout") &&
          onDeferredProceed
        ) {
          onDeferredProceed();
          return;
        }
        setError(
          "Gate not open yet — your Tier-2 choices are saved. Click Apply Tier-2 choices again in a few seconds.",
        );
      });
      return;
    }

    if (!submitReady) {
      return;
    }

    lastSubmitRef.current = built.body;
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
          setError(`Cannot submit field-mapping decisions yet. Current migration status is: ${latestStatus}`);
          return;
        }
        const gateType = String(latest.pending_gate_type ?? "").toLowerCase();
        const isFieldMappingGate =
          (gateType.includes("field") && gateType.includes("map")) ||
          (gateType.includes("human") && gateType.includes("review")) ||
          (gateType.includes("table") && gateType.includes("structure")) ||
          (gateType.includes("column") && gateType.includes("placement"));
        if (!isFieldMappingGate) {
          setError(
            `Review step changed to '${latest.pending_gate_type ?? "unknown"}'. Please submit in the active gate.`,
          );
          return;
        }
        submitGate({ migrationId, body: built.body });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Failed to verify migration status before submit";
        setError(`${msg}. Please retry to continue safely.`);
      })
      .finally(() => {
        setIsPreflighting(false);
      });
  }

  const totalFlagged = flaggedTables.reduce((s, t) => s + (flaggedByTable[t]?.length ?? 0), 0);
  const totalUnmapped = unmappedTables.reduce((s, t) => s + (unmappedByTable[t]?.length ?? 0), 0);
  const handleReloadStatus = () => {
    setError(null);
    onReloadStatus?.();
  };

  const canDeferredProceed = deferUntilAwaitingReview && !!onDeferredProceed;
  const canApplyTier2 = !submitReady && !!onApplyTier2Continue;
  const hasPendingDraft = !!savedDraft;
  const submitDisabled =
    isPending ||
    isAdvancing ||
    isPreflighting ||
    (!submitReady && !canDeferredProceed && !canApplyTier2 && !hasPendingDraft);
  return (
    <div
      className={cn(embeddedRail ? "w-full min-w-0" : "max-w-6xl")}
    >
      {!submitReady && submitBlockedReason ? (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 flex items-start gap-2">
          <AlertTriangle size={16} className="shrink-0 mt-0.5 text-amber-600" />
          <div>
            <div className="font-medium">Submit not ready yet</div>
            <p className="mt-0.5 text-xs text-amber-800/90">
              {submitBlockedReason} You can still edit Accept / Override / Reject below, then use{" "}
              <strong>Apply Tier-2 choices</strong> to continue.
            </p>
          </div>
        </div>
      ) : null}
      {savedDraft ? (
        <div className="mb-4 rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-3 text-sm text-indigo-900">
          <div className="font-medium">Tier-2 choices loaded</div>
          <p className="mt-0.5 text-xs text-indigo-800/90">
            Overrides from semantic review are shown below
            {Object.entries(canonicalTableBySource).map(([src, canon]) => (
              <span key={src} className="block font-mono mt-1">
                {src} → {canon}
              </span>
            ))}
            {submitReady ? " — submitting automatically…" : " — click Apply Tier-2 choices when ready."}
          </p>
        </div>
      ) : null}
      {/* T1 deterministic results — shown when coming from pre-semantic stage */}
      {t1Snapshot?.payload && (() => {
        const t1Review = t1Snapshot.payload.review_items_by_table ?? {};
        const t1Tables = Object.keys(t1Review);
        const t1Total = t1Tables.reduce((s, tbl) => s + (t1Review[tbl]?.length ?? 0), 0);
        const t1Approved = t1Tables.reduce(
          (s, tbl) => s + (t1Snapshot.decisions[tbl] ?? []).filter((d) => d.decision === "approve").length,
          0,
        );
        const t1Semantic = t1Total - t1Approved;
        return (
          <details className="mb-5 rounded-xl border border-indigo-200 bg-indigo-50 overflow-hidden">
            <summary className="px-5 py-3 cursor-pointer flex items-center gap-3 list-none">
              <ChevronDown size={14} className="text-indigo-500 shrink-0 transition-transform in-[[open]]:rotate-180" />
              <span className="text-sm font-semibold text-indigo-800">Deterministic Mapping Results</span>
              <span className="text-xs text-indigo-600 ml-auto tabular-nums">
                {t1Approved} approved · {t1Semantic} → semantic · {t1Total} fields
              </span>
            </summary>
            <div className="border-t border-indigo-200 divide-y divide-indigo-100">
              {t1Tables.map((tbl) => {
                const items = t1Review[tbl] ?? [];
                const decsByField = new Map((t1Snapshot.decisions[tbl] ?? []).map((d) => [d.source_field, d.decision]));
                return (
                  <div key={tbl} className="px-5 py-3">
                    <p className="text-xs font-semibold text-indigo-700 mb-2">{tbl}</p>
                    <div className="space-y-1.5">
                      {items.map((item, i) => {
                        const dec = decsByField.get(item.source_field) ?? "approve";
                        return (
                          <div key={i} className="flex items-center gap-2 text-xs">
                            <span className="font-mono text-slate-700 bg-slate-100 px-1.5 py-0.5 rounded">{item.source_field}</span>
                            <span className="text-slate-300">→</span>
                            <span className="font-mono text-indigo-700 bg-white px-1.5 py-0.5 rounded border border-indigo-100">{item.target_field}</span>
                            <span className={`ml-auto px-1.5 py-0.5 rounded-full font-medium text-[10px] ${dec === "approve" ? "bg-green-100 text-green-700" : "bg-blue-100 text-blue-700"}`}>
                              {dec === "approve" ? "approved" : "→ semantic"}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </details>
        );
      })()}

      {/* Header — title removed per request; keep the guidance line only. */}
      <div className="flex items-start gap-4 mb-6">
        <div className="w-10 h-10 rounded-xl bg-amber-100 flex items-center justify-center shrink-0">
          <Edit3 size={20} className="text-amber-600" />
        </div>
        <div>
          <p className="text-sm text-slate-500 mt-1.5">
            Review flagged field mappings and decide how to handle unmapped fields.
          </p>
        </div>
      </div>

      {payload.confidence_alert?.message ? (
        <div className="mb-5 flex items-start gap-3 rounded-xl bg-amber-50 border border-amber-200 px-4 py-3">
          <AlertTriangle size={16} className="text-amber-600 mt-0.5 shrink-0" />
          <p className="text-sm text-amber-800">{payload.confidence_alert.message}</p>
        </div>
      ) : null}

      {/* Counters */}
      <div className="grid grid-cols-2 gap-4 mb-5">
        <div className="rounded-xl border border-slate-200 bg-amber-50 shadow-sm p-4 flex items-center gap-3">
          <AlertTriangle size={18} className="text-amber-600 shrink-0" />
          <div>
            <div className="text-lg font-bold font-mono text-slate-800">{totalFlagged}</div>
            <div className="text-xs text-slate-500">Flagged — accept / override / reject</div>
          </div>
        </div>
        <div className="rounded-xl border border-slate-200 bg-red-50 shadow-sm p-4 flex items-center gap-3">
          <XCircle size={18} className="text-red-500 shrink-0" />
          <div>
            <div className="text-lg font-bold font-mono text-slate-800">{totalUnmapped}</div>
            <div className="text-xs text-slate-500">Unmapped — DDL / raw_metadata / skip</div>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-5 border-b border-slate-200">
        {[
          { key: "flagged" as const,  label: `Flagged (${totalFlagged})` },
          { key: "unmapped" as const, label: `Unmapped (${totalUnmapped})` },
        ].map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              activeTab === tab.key
                ? "border-indigo-500 text-indigo-600"
                : "border-transparent text-slate-500 hover:text-slate-700"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "unmapped" && (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-5 mb-6">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-semibold text-slate-800">New tables</div>
              <div className="text-xs text-slate-500 mt-0.5">Create brand-new tables with multiple columns</div>
            </div>
            <button
              type="button"
              onClick={addNewTable}
              className="inline-flex items-center gap-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 px-4 py-2 rounded-lg transition-colors"
            >
              <PlusCircle size={14} />
              Add table
            </button>
          </div>

          {newTableDefs.length ? (
            <div className="mt-4 space-y-3">
              {newTableDefs.map((t) => (
                <div key={t.id} className="rounded-xl border border-slate-200 overflow-hidden">
                  <div className="px-4 py-3 bg-slate-50 flex items-center justify-between gap-3">
                    <div className="grid grid-cols-2 gap-3 flex-1">
                      <div>
                        <label className="block text-xs font-medium text-slate-700 mb-1">Table name</label>
                        <input
                          className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-mono transition-colors hover:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          value={t.table_name}
                          onChange={(e) => patchNewTable(t.id, { table_name: e.target.value })}
                          placeholder="new_table"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-slate-700 mb-1">Primary key</label>
                        <input
                          className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-mono transition-colors hover:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          value={t.pk_col}
                          onChange={(e) => patchNewTable(t.id, { pk_col: e.target.value })}
                          placeholder="id"
                        />
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => removeNewTable(t.id)}
                      className="p-2 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                  <div className="px-4 py-4 space-y-2">
                    {t.columns.map((c, idx) => (
                      <div key={idx} className="grid grid-cols-[1fr_160px_120px_40px] gap-2 items-end">
                        <div>
                          <label className="block text-xs font-medium text-slate-700 mb-1">Column</label>
                          <input
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-mono transition-colors hover:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                            value={c.column_name}
                            onChange={(e) => updateNewTableColumn(t.id, idx, { column_name: e.target.value })}
                            placeholder="column_name"
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-medium text-slate-700 mb-1">Type</label>
                          <select
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-mono transition-colors hover:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                            value={c.data_type}
                            onChange={(e) => updateNewTableColumn(t.id, idx, { data_type: e.target.value })}
                          >
                            {DATA_TYPES.map((dt) => <option key={dt} value={dt}>{dt}</option>)}
                          </select>
                        </div>
                        <div className="flex items-center gap-2 pb-1">
                          <input
                            type="checkbox"
                            checked={c.nullable}
                            onChange={(e) => updateNewTableColumn(t.id, idx, { nullable: e.target.checked })}
                          />
                          <span className="text-xs text-slate-600">Nullable</span>
                        </div>
                        <button
                          type="button"
                          onClick={() => removeNewTableColumn(t.id, idx)}
                          className="p-2 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    ))}
                    <button
                      type="button"
                      onClick={() => addNewTableColumn(t.id)}
                      className="inline-flex items-center gap-2 text-xs font-medium text-indigo-600 hover:text-indigo-800 transition-colors"
                    >
                      <PlusCircle size={14} />
                      Add column
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="mt-4 text-sm text-slate-500">No new tables added.</div>
          )}
        </div>
      )}

      {/* ── FLAGGED TAB ── */}
      {activeTab === "flagged" && (
        <div className="space-y-3 mb-6">
          {flaggedTables.length === 0 ? (
            <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6 text-center text-slate-500 text-sm">
              No flagged fields to review.
            </div>
          ) : (
            flaggedTables.map((tbl) => {
              const items: MigrationFlaggedFieldItem[] = flaggedByTable[tbl] ?? [];
              const isOpen = expandedFlagged.has(tbl);
              return (
                <div key={tbl} className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
                  <button
                    className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
                    onClick={() => setExpandedFlagged((prev) => {
                      const n = new Set(prev);
                      if (n.has(tbl)) n.delete(tbl);
                      else n.add(tbl);
                      return n;
                    })}
                  >
                    <div className="flex items-center gap-3 flex-wrap">
                      <span className="font-semibold text-slate-800 text-sm font-mono">{tbl}</span>
                      {canonicalTableBySource[tbl] ? (
                        <span className="text-[11px] font-mono text-indigo-700 bg-indigo-50 px-2 py-0.5 rounded">
                          → {canonicalTableBySource[tbl]}
                        </span>
                      ) : null}
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-700">
                        {items.length} flagged
                      </span>
                    </div>
                    {isOpen ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
                  </button>

                  {isOpen && (
                    <div className="border-t border-slate-100">
                      <div className="px-5 py-2 bg-slate-50 border-b border-slate-100">
                        <MigrationCanonicalTableSelect
                          sourceTable={tbl}
                          canonicalTables={canonicalTables}
                          value={
                            canonicalTableBySource[tbl]?.trim() ||
                            defaultTargetTable(tbl, canonicalTables)
                          }
                          isNewTable={false}
                          newTableName=""
                          onChange={({ canonicalTable }) =>
                            setCanonicalTableBySource((prev) => ({ ...prev, [tbl]: canonicalTable }))
                          }
                        />
                      </div>
                      <div className="divide-y divide-slate-100">
                      {items.map((item, idx) => {
                        const suggestedTarget = getSuggestedTarget(item);
                        const effectiveSuggested = item.target_field ?? suggestedTarget;
                        const d = flaggedDecisions[tbl]?.[idx] ?? { action: effectiveSuggested ? "accept" : ("override" as FlaggedAction), overrideTarget: suggestedTarget };
                        const activeTarget =
                          d.action === "override" ? (d.overrideTarget || "") : (effectiveSuggested || "");
                        const rowCanonicalTable =
                          canonicalTableBySource[tbl]?.trim() ||
                          defaultTargetTable(tbl, canonicalTables);
                        return (
                          <div
                            key={idx}
                            className={`px-5 py-4 transition-colors ${
                              d.action === "accept" ? "bg-green-50/40" : d.action === "reject" ? "bg-red-50/40" : "bg-blue-50/40"
                            }`}
                          >
                            <div className={`flex items-start gap-4 ${compact ? "flex-col" : ""}`}>
                              <div className="flex-1 min-w-0">
                                <FlaggedFieldScoreSection
                                  item={item}
                                  canonicalTable={rowCanonicalTable}
                                  topMatchLimit={topMatchLimit}
                                  activeTarget={activeTarget}
                                >
                                  {({ rows, activeConfidence, scoresLoading }) => (
                                    <>
                                <div className="flex items-center gap-2 flex-wrap mb-1">
                                  <span className="font-mono text-xs bg-slate-100 text-slate-700 px-2 py-0.5 rounded">{item.source_field}</span>
                                  <span className="text-slate-300 text-xs">→</span>
                                  <span className={`font-mono text-xs px-2 py-0.5 rounded ${
                                    d.action === "reject" ? "bg-red-100 text-red-500 line-through" : "bg-indigo-50 text-indigo-700"
                                  }`}>
                                    {d.action === "override" ? (d.overrideTarget || "—") : (effectiveSuggested || "—")}
                                  </span>
                                  {activeConfidence != null ? (
                                    <ConfidencePill confidence={activeConfidence} />
                                  ) : item.confidence != null ? (
                                    <ConfidencePill confidence={item.confidence} />
                                  ) : null}
                                </div>
                                <ScoreBreakdownChips breakdown={item.score_breakdown} />
                                {(() => {
                                  const r = (item as unknown as Record<string, unknown>).rationale;
                                  return typeof r === "string" && r.trim().length ? (
                                    <p className="text-xs text-slate-400 mb-2">{r}</p>
                                  ) : null;
                                })()}
                                <MatchScoreTable
                                  rows={rows}
                                  scoresLoading={scoresLoading}
                                  selectable={d.action === "override"}
                                  onSelectSuggested={(field) => {
                                    setFlaggedAction(tbl, idx, "override");
                                    const dk = flaggedFieldKey(tbl, item.source_field);
                                    patchColumnDraft(dk, {
                                      mode: "existing",
                                      targetField: field,
                                    });
                                  }}
                                />
                                    </>
                                  )}
                                </FlaggedFieldScoreSection>
                                {d.action === "override" ? (
                                  <div className="mt-2" onClick={(e) => e.stopPropagation()}>
                                    <MigrationColumnOverride
                                      item={item}
                                      canonicalTable={rowCanonicalTable}
                                      draft={
                                        columnDrafts[flaggedFieldKey(tbl, item.source_field)] ??
                                        emptyColumnOverrideDraft(d.overrideTarget || suggestedTarget)
                                      }
                                      onChange={(patch) =>
                                        patchColumnDraft(flaggedFieldKey(tbl, item.source_field), patch)
                                      }
                                      compact={compact}
                                    />
                                  </div>
                                ) : null}
                                {d.action === "reject" && <p className="text-xs text-red-500 mt-1">Field will be discarded</p>}
                              </div>
                              <div className="flex gap-1.5 shrink-0">
                                <ActionBtn
                                  label="Accept"
                                  icon={<CheckCircle size={11} />}
                                  active={d.action === "accept"}
                                  activeColor="bg-green-600"
                                  onClick={() => setFlaggedAction(tbl, idx, effectiveSuggested ? "accept" : "override")}
                                />
                                <ActionBtn label="Override" icon={<Edit3 size={11} />} active={d.action === "override"} activeColor="bg-blue-600" onClick={() => {
                                  setFlaggedAction(tbl, idx, "override");
                                  const initial = d.overrideTarget || suggestedTarget || item.target_field || "";
                                  if (!d.overrideTarget) setOverrideTarget(tbl, idx, initial);
                                  patchColumnDraft(flaggedFieldKey(tbl, item.source_field), {
                                    ...emptyColumnOverrideDraft(initial),
                                    targetField: initial,
                                  });
                                }} />
                                <ActionBtn label="Reject"   icon={<XCircle size={11} />}      active={d.action === "reject"}   activeColor="bg-red-600"   onClick={() => setFlaggedAction(tbl, idx, "reject")} />
                              </div>
                            </div>
                          </div>
                        );
                      })}
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      )}

      {/* ── UNMAPPED TAB ── */}
      {activeTab === "unmapped" && (
        <>
          <div className="flex items-center justify-between mb-4 px-4 py-3 bg-indigo-50 rounded-xl border border-indigo-200">
            <div>
              <p className="text-sm font-semibold text-indigo-800">Batch action</p>
              <p className="text-xs text-indigo-600">Apply the same decision to all unmapped fields</p>
            </div>
            <button
              type="button"
              onClick={autoAddColumnForAll}
              className="inline-flex items-center gap-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 px-4 py-2 rounded-lg transition-colors"
            >
              <Wand2 size={14} />
              Auto-select "New column" for all
            </button>
          </div>

          <div className="space-y-3 mb-6">
          {unmappedTables.length === 0 ? (
            <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6 text-center text-slate-500 text-sm">
              No unmapped fields.
            </div>
          ) : (
            unmappedTables.map((tbl) => {
              const items: MigrationUnmappedFieldItem[] = unmappedByTable[tbl] ?? [];
              const rows = unmappedRows[tbl] ?? [];
              const sourceCount = items.length;
              const isOpen = expandedUnmapped.has(tbl);
              return (
                <div key={tbl} className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
                  <button
                    className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
                    onClick={() => setExpandedUnmapped((prev) => {
                      const n = new Set(prev);
                      if (n.has(tbl)) n.delete(tbl);
                      else n.add(tbl);
                      return n;
                    })}
                  >
                    <div className="flex items-center gap-3">
                      <span className="font-semibold text-slate-800 text-sm font-mono">{tbl}</span>
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-600">
                        {rows.length} fields
                      </span>
                    </div>
                    {isOpen ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
                  </button>

                  {isOpen && (
                    <div className="border-t border-slate-100">
                      {rows.map((row, idx) => {
                        const isExtra = idx >= sourceCount;
                        return (
                          <div key={idx} className="border-b border-slate-50 last:border-b-0">
                            <div className="flex items-center gap-4 px-5 py-3">
                              {isExtra ? (
                                <input
                                  className="w-full max-w-60 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-mono transition-colors hover:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 flex-1"
                                  value={row.sourceField}
                                  onChange={(e) => patchSourceField(tbl, idx, e.target.value)}
                                  placeholder="source_field_name"
                                />
                              ) : (
                                <span className="font-mono text-xs text-slate-700 flex-1 truncate">{row.sourceField}</span>
                              )}
                              <div className="flex gap-1.5 shrink-0">
                                <ActionBtn label="New column"   icon={<PlusCircle size={11} />} active={row.action === "custom"}       activeColor="bg-indigo-600" onClick={() => setUnmappedAction(tbl, idx, "custom")} />
                                <ActionBtn label="raw_metadata" icon={<Archive size={11} />}     active={row.action === "raw_metadata"} activeColor="bg-slate-600"  onClick={() => setUnmappedAction(tbl, idx, "raw_metadata")} />
                                <ActionBtn label="Skip"         icon={<XCircle size={11} />}     active={row.action === "skip"}         activeColor="bg-red-600"    onClick={() => setUnmappedAction(tbl, idx, "skip")} />
                                {isExtra ? (
                                  <button
                                    type="button"
                                    onClick={() => removeExtraRow(tbl, idx)}
                                    className="p-1.5 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                                  >
                                    <Trash2 size={13} />
                                  </button>
                                ) : null}
                              </div>
                            </div>
                            {row.action === "custom" && row.ddl ? (
                              <div className="px-5 pb-4">
                                <CustomDDLForm ddl={row.ddl} canonicalTables={canonicalTables} onChange={(p) => patchDDL(tbl, idx, p)} />
                              </div>
                            ) : null}
                            {row.action === "raw_metadata" ? (
                              <div className="px-5 pb-3">
                                <p className="text-xs text-slate-400">Stored in <code className="bg-slate-100 px-1 rounded">raw_metadata</code> JSONB — no schema changes.</p>
                              </div>
                            ) : null}
                          </div>
                        );
                      })}
                      <div className="px-5 py-3 bg-slate-50 border-t border-slate-100">
                        <button
                          type="button"
                          onClick={() => addExtraCol(tbl)}
                          className="inline-flex items-center gap-2 text-xs font-medium text-indigo-600 hover:text-indigo-800 transition-colors"
                        >
                          <PlusCircle size={14} />
                          Add column to {tbl}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )}
          </div>
        </>
      )}

      {error ? (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          <div>{error}</div>
          {onReloadStatus ? (
            <button
              type="button"
              onClick={handleReloadStatus}
              className="mt-2 inline-flex items-center gap-1 rounded border border-red-300 bg-white px-2 py-1 text-xs font-medium text-red-800 hover:bg-red-50"
            >
              Check status again
            </button>
          ) : null}
        </div>
      ) : null}

      <button
        type="button"
        onClick={handleSubmit}
        disabled={submitDisabled}
        title={submitBlockedReason ?? undefined}
        className="inline-flex items-center gap-2 px-8 py-3 bg-indigo-600 text-white text-base font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {isPending || isAdvancing || isPreflighting ? (
          <>
            <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
            {isPreflighting ? "Checking status…" : "Submitting…"}
          </>
        ) : !submitReady && (canApplyTier2 || hasPendingDraft) ? (
          <>Apply Tier-2 choices</>
        ) : !submitReady && canDeferredProceed ? (
          <>Continue pipeline</>
        ) : !submitReady ? (
          <>Waiting for review gate…</>
        ) : (
          <>
            <CheckCircle size={18} />
            Submit field mapping decisions
          </>
        )}
      </button>
    </div>
  );
}
