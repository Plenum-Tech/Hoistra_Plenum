"use client";
import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle,
  ChevronDown,
  ChevronUp,
  Search,
  Edit3,
  FileJson,
  FileText,
  Database,
  FileBarChart,
  XCircle,
} from "lucide-react";
import { useMigrationAdvance, type NodeInfo } from "../../chat-api";
import { parseEnvelope } from "./migration-field-mapping-draft";
import { plainTierWord } from "./migration-mapping-utils";

interface Props {
  migrationId: string;
  stepKey: string;
  payload: Record<string, unknown>;
  onAdvanced: () => void;
  allNodes?: NodeInfo[];
  /** The user's persisted field-mapping decisions (shown at the preprocess step). */
  fieldMappingDraft?: unknown;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function readNumber(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/**
 * Read a count from a backend field that can be EITHER a numeric counter
 * (e.g. `fk_count: 3`) OR a list of items where length implies the count
 * (e.g. `fks: [{...}, {...}, {...}]`). Returns null for any other shape so
 * the caller can fall through to the next candidate.
 */
function readCount(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (Array.isArray(v)) return v.length;
  return null;
}

function readString(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

function readRecord(v: unknown): Record<string, unknown> | null {
  return isRecord(v) ? v : null;
}

function readStringArray(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x) => typeof x === "string");
}

function readRecordArray(v: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(v)) return [];
  const out: Array<Record<string, unknown>> = [];
  for (const it of v) {
    const r = readRecord(it);
    if (r) out.push(r);
  }
  return out;
}

function Metric({ label, value, accent }: { label: string; value: unknown; accent: string }) {
  const colors: Record<string, string> = {
    indigo: "text-indigo-600 bg-indigo-50",
    green:  "text-green-600 bg-green-50",
    amber:  "text-amber-600 bg-amber-50",
    red:    "text-red-600 bg-red-50",
  };
  // Format-aware: numbers render as locale strings; null/undefined fall back
  // to "—" so the tile never silently displays a fake 0 when the underlying
  // field was missing from the response.
  const display =
    typeof value === "number" && Number.isFinite(value)
      ? value.toLocaleString()
      : value == null || value === ""
        ? "—"
        : String(value);
  return (
    <div className={`rounded-lg px-4 py-3 ${colors[accent] ?? colors.indigo}`}>
      <div className="text-2xl font-bold font-mono">{display}</div>
      <div className="text-xs font-medium mt-0.5 opacity-80">{label}</div>
    </div>
  );
}

const NODE_LABELS: Record<string, string> = {
  step_1_ingest:               "File Ingestion",
  step_2_deterministic:        "Deterministic Mapping",
  step_2_deterministic_mapping:"Deterministic Mapping",
  step_3_pre_semantic:         "Pre-Semantic Review",
  step_4_semantic:             "Semantic Mapping",
  step_4_semantic_mapping:     "Semantic Mapping",
  step_5_field_mapping:        "Field Mapping Review",
  step_6_preprocess:           "Data Preprocessing",
  step_6_data_preprocessing:   "Data Preprocessing",
  step_7_hierarchy:            "Hierarchy Detection",
  step_7_hierarchy_detection:  "Hierarchy Detection",
  step_8_hierarchy_gate:       "Hierarchy Verification",
  step_9_output:               "Output Generation",
  step_9_output_generation:    "Output Generation",
  // Legacy keys (backward compat)
  step_3_semantic:             "Semantic Mapping",
  step_4_field_mapping:        "Semantic Mapping",
  step_5_preprocess:           "Data Preprocessing",
  step_6_hierarchy:            "Hierarchy Detection",
  step_6_verify_hierarchy:     "Hierarchy Verification",
  step_8_output:               "Output Generation",
  step_8_output_generation:    "Output Generation",
  step_9_write:                "Output Generation",
};

// Normalized step keys the pipeline advances without human input — the StepPause for these hides
// the Continue button and auto-advances (File Ingestion → Deterministic Mapping → pre-semantic gate).
const AUTO_ADVANCE_STEP_KEYS = new Set<string>([
  "step_1_ingest",
  "step_2_deterministic",
  "step_2_deterministic_mapping",
]);

/**
 * "Null / NaN values cleaned" — the first data-quality step's report, surfaced in
 * the central Query Space. Backend (Node 1 ingest → nan_scan) detects null/NaN/empty
 * cells across every column; they're filled during preprocessing (Node 5). Shows the
 * per-table columns + counts and a sample of the affected rows.
 */
function NanReportSection({ report }: { report: Record<string, unknown> | null }) {
  if (!report) return null;
  const totalCells = readNumber(report.total_nan_cells) ?? 0;
  const totalRows = readNumber(report.total_rows_with_nan) ?? 0;
  const colsWithNan = readNumber(report.columns_with_nan) ?? 0;
  const tablesRec = readRecord(report.tables) ?? {};

  if (totalCells === 0) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-xs text-green-700">
        <CheckCircle size={14} className="shrink-0" />
        <span>Null / NaN check: no null, NaN or empty values found in any column.</span>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50/50 overflow-hidden">
      <div className="flex items-center gap-2 border-b border-amber-200 px-3 py-2">
        <AlertTriangle size={14} className="shrink-0 text-amber-600" />
        <span className="text-xs font-semibold text-amber-800">Null / NaN values cleaned</span>
        <span className="ml-auto text-[11px] tabular-nums text-amber-700">
          {totalCells.toLocaleString()} value{totalCells === 1 ? "" : "s"} ·{" "}
          {totalRows.toLocaleString()} row{totalRows === 1 ? "" : "s"} · {colsWithNan} column
          {colsWithNan === 1 ? "" : "s"}
        </span>
      </div>
      <div className="space-y-3 px-3 py-2">
        <p className="text-[11px] leading-relaxed text-amber-700/90">
          Null / NaN / empty cells were detected across the columns below and are filled during
          preprocessing (numeric → 0, text → empty).
        </p>
        {/* Show EVERY table — a clean table gets a green "no null/NaN (0)" badge so it's clear it
            was checked, not skipped. */}
        {Object.entries(tablesRec).map(([tableName, raw]) => {
          const t = readRecord(raw);
          if (!t) return null;
          const nanCells = readNumber(t.nan_cells) ?? 0;
          const rowsWithNan = readNumber(t.rows_with_nan) ?? 0;
          const columns = readRecord(t.columns) ?? {};
          const clean = nanCells === 0;
          return (
            <div key={tableName} className="rounded-lg border border-slate-200 bg-white overflow-hidden">
              <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-3 py-2">
                <span className="text-xs font-semibold text-slate-700">{tableName}</span>
                {clean ? (
                  <span className="rounded-full bg-green-100 px-1.5 py-0.5 text-[10px] font-medium text-green-700">
                    no null/NaN (0)
                  </span>
                ) : (
                  <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
                    {rowsWithNan.toLocaleString()} row{rowsWithNan === 1 ? "" : "s"} with null/NaN
                  </span>
                )}
              </div>
              {/* Per-column null/NaN counts only — no per-row detail. Omitted for clean tables. */}
              {clean ? null : (
                <div className="flex flex-wrap gap-1.5 px-3 py-2">
                  {Object.entries(columns).map(([col, count]) => (
                    <span
                      key={col}
                      className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-mono text-slate-600"
                    >
                      {col}
                      <span className="text-slate-400">×{readNumber(count) ?? 0}</span>
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Node1Ingest({ payload }: { payload: Record<string, unknown> }) {
  const overallSummary = readRecord(payload.overall_summary);
  const rows =
    readNumber(payload.rows) ??
    readNumber(payload.row_count) ??
    readNumber(overallSummary?.total_rows) ??
    null;
  const cols =
    readNumber(payload.columns) ??
    readNumber(payload.column_count) ??
    readNumber(overallSummary?.total_columns) ??
    null;
  const format =
    readString(payload.format) ??
    readString(payload.detected_format) ??
    readString(overallSummary?.detected_format) ??
    null;

  // Only the dataset-level totals are shown for File ingestion — the Tables list, the
  // Excel→CAFM table-mapping card, and the per-table completeness cards were removed per
  // request (table routing + per-table detail live in the later mapping steps).
  const nanReport = readRecord(payload.nan_report);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Metric label="Total rows" value={rows} accent="indigo" />
        <Metric label="Total columns" value={cols} accent="indigo" />
        <Metric label="Format" value={format} accent="indigo" />
      </div>
      <NanReportSection report={nanReport} />
    </div>
  );
}

function Node2Deterministic({ payload }: { payload: Record<string, unknown> }) {
  const mapped =
    readNumber(payload.t1_mapped) ??
    readNumber(payload.t1_mapped_count) ??
    readNumber(payload.tier1_mapped) ??
    readNumber(payload.tier1_mapped_count) ??
    null;
  const unresolvedCount =
    readNumber(payload.unresolved) ??
    readNumber(payload.unresolved_count) ??
    readNumber(payload.tier1_unresolved) ??
    null;

  const mappingsByTableRaw = readRecord(payload.mappings_by_table);
  const unresolvedByTableRaw = readRecord(payload.unresolved_by_table);

  const tableNamesFromPayload = readStringArray(payload.table_names);
  const defaultTable = tableNamesFromPayload[0] ?? "data";

  // Source → target table routing (overlaid from the pre-semantic gate) — still
  // consulted by the new-table heuristic below to tell a semantically-routed
  // existing table apart from a genuinely new one.
  const tableRouting = readRecord(payload.table_routing) ?? {};

  const mappingsByTable: Record<string, Array<Record<string, unknown>>> = {};
  if (mappingsByTableRaw) {
    for (const [tbl, list] of Object.entries(mappingsByTableRaw)) {
      mappingsByTable[tbl] = readRecordArray(list);
    }
  } else {
    const fieldMappings = readRecordArray(payload.field_mappings);
    if (fieldMappings.length) mappingsByTable[defaultTable] = fieldMappings;
  }

  const unresolvedByTable: Record<string, string[]> = {};
  if (unresolvedByTableRaw) {
    for (const [tbl, list] of Object.entries(unresolvedByTableRaw)) {
      unresolvedByTable[tbl] = readStringArray(list);
    }
  } else {
    const byTable = readRecord(payload.tier2_unmappable_by_table);
    if (byTable) {
      for (const [tbl, list] of Object.entries(byTable)) {
        unresolvedByTable[tbl] = readStringArray(list);
      }
    } else {
      const list = readStringArray(payload.tier2_unmappable);
      if (list.length) unresolvedByTable[defaultTable] = list;
    }
  }

  // ── New-table detection ──────────────────────────────────────────────────
  // A source table is treated as "becoming a new table" when:
  //   1. The migration state already promoted it (payload.new_tables /
  //      payload.new_source_tables / payload.source_is_new — backend
  //      forward-compat hooks), OR
  //   2. Zero columns deterministically matched and every field is unresolved
  //      — in that case "Sent to semantic" is a misleading status because the
  //      semantic mapper has no target table to score against. The fields
  //      will end up as new columns under a new target table, so we surface
  //      that intent immediately instead of an action the user didn't take.
  const explicitNewTables = new Set<string>([
    ...readStringArray(payload.new_tables),
    ...readStringArray(payload.new_source_tables),
  ]);
  // Tables that already exist in plenum_cafm — a source routed to one of these is NEVER a new
  // table: its columns are matched against / added to that existing table (ALTER ADD COLUMN),
  // never CREATE TABLE. Safe no-op if the payload doesn't carry the list.
  const existingCanonicalTables = new Set<string>(
    readStringArray(payload.existing_canonical_tables).map((t) => t.toLowerCase()),
  );
  const isNewTableSource = (tbl: string): boolean => {
    if (explicitNewTables.has(tbl)) return true;
    // Routed to a real existing table (e.g. works → assets, WorkOrders_2 → work_orders) ⇒ not new.
    // Without this the zero-deterministic-match heuristic below mislabels a semantically-routed
    // table's columns as "new table column" even though they map into an existing table.
    const routedTarget = String(tableRouting[tbl] ?? "").trim().toLowerCase();
    if (routedTarget && existingCanonicalTables.has(routedTarget)) return false;
    const mappedCount = (mappingsByTable[tbl] ?? []).length;
    const unresolvedCountForTbl = (unresolvedByTable[tbl] ?? []).length;
    return mappedCount === 0 && unresolvedCountForTbl > 0;
  };

  const tierCounts: Record<string, number> = {};
  for (const row of Object.values(mappingsByTable).flat()) {
    const tier = readString(row.tier) ?? "unknown";
    tierCounts[tier] = (tierCounts[tier] ?? 0) + 1;
  }

  const TIER_META: Record<string, string> = {
    T1_exact: "bg-green-100 text-green-800",
    T1_variation: "bg-teal-100 text-teal-800",
    T1_alias: "bg-blue-100 text-blue-800",
    T1_regex: "bg-purple-100 text-purple-800",
    T1_registry: "bg-teal-100 text-teal-800",
    T1_llm: "bg-indigo-100 text-indigo-800",
  };

  function tierLabel(tier: string) {
    if (tier.startsWith("T1_")) return plainTierWord(tier.slice(3).replace(/_/g, " "));
    return tier.replace(/_/g, " ");
  }

  // Split unresolved fields into "new-column" (those under new-table sources)
  // and "true semantic" (those under existing target tables). Surfaces the
  // distinction in the top-line metric instead of mislabelling new columns as
  // "Sent to semantic", which is the user-visible bug being fixed here.
  let newColumnTotal = 0;
  let semanticTotal = 0;
  for (const [tbl, list] of Object.entries(unresolvedByTable)) {
    if (isNewTableSource(tbl)) newColumnTotal += list.length;
    else semanticTotal += list.length;
  }
  // Preserve the original `unresolvedCount` as a fallback when per-table data
  // isn't available.
  const fallbackSemantic =
    Object.keys(unresolvedByTable).length === 0 && typeof unresolvedCount === "number"
      ? unresolvedCount
      : null;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Metric label="T1 mapped" value={mapped} accent="green" />
        <Metric
          label="Sent to semantic"
          value={fallbackSemantic ?? semanticTotal}
          accent="amber"
        />
        {newColumnTotal > 0 ? (
          <Metric label="New columns" value={newColumnTotal} accent="indigo" />
        ) : null}
      </div>

      {Object.keys(tierCounts).length ? (
        <div className="flex flex-wrap gap-2">
          {Object.entries(tierCounts)
            .sort((a, b) => b[1] - a[1])
            .map(([tier, count]) => (
              <span
                key={tier}
                className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium ${
                  TIER_META[tier] ?? "bg-slate-100 text-slate-600"
                }`}
              >
                {tierLabel(tier)}: {count}
              </span>
            ))}
        </div>
      ) : null}
    </div>
  );
}

type SemanticFilter = "all" | "auto" | "flagged" | "unmappable" | "new_column";

type SemanticResult = {
  table: string;
  source_field: string;
  target_field: string | null;
  confidence: number | null;
  status: "auto" | "flagged" | "unmappable" | "new_column";
  best_target?: string | null;
  best_confidence?: number | null;
  tier?: string | null;
  rationale?: string | null;
};

/**
 * Build a lookup of fields the user later approved as new columns / new-table
 * columns at the field-mapping gate (or pre-semantic gate's "+ New Table"
 * decision). When a Node 4 semantic-mapping snapshot is rendered AFTER those
 * decisions have been made, we use this to relabel the previously-"unmappable"
 * fields to "approved as new column" — otherwise the snapshot misleadingly
 * shows them as unresolved.
 */
type NewColumnLookup = {
  newTablesSet: Set<string>;
  newSourceTablesSet: Set<string>;
  newColumnFields: Set<string>;    // keys "<source_table>.<source_field>"
  customColumnNames: Map<string, string>; // same key → custom_column_name
  customTargetTables: Map<string, string>;
};

function buildNewColumnLookup(allNodes?: NodeInfo[]): NewColumnLookup {
  const newTablesSet = new Set<string>();
  const newSourceTablesSet = new Set<string>();
  const newColumnFields = new Set<string>();
  const customColumnNames = new Map<string, string>();
  const customTargetTables = new Map<string, string>();
  if (!allNodes?.length) {
    return { newTablesSet, newSourceTablesSet, newColumnFields, customColumnNames, customTargetTables };
  }

  // Two-pass walk. We can't resolve newSourceTablesSet inline because the routing
  // entry might live on one node's output and the matching new_tables entry on
  // another's — if we processed them in iteration order, a routing entry seen
  // before its target name was registered would be silently dropped.
  const pendingRoutings: Array<[string, string]> = [];

  for (let i = allNodes.length - 1; i >= 0; i -= 1) {
    const out = allNodes[i]?.output;
    if (!out || typeof out !== "object") continue;
    const o = out as Record<string, unknown>;

    if (Array.isArray(o.new_tables)) {
      for (const t of o.new_tables) {
        if (typeof t === "string" && t.trim()) newTablesSet.add(t.trim());
      }
    }
    const routing = o.table_routing;
    if (routing && typeof routing === "object" && !Array.isArray(routing)) {
      for (const [src, tgt] of Object.entries(routing as Record<string, unknown>)) {
        if (typeof tgt === "string" && tgt.trim()) {
          pendingRoutings.push([src, tgt.trim()]);
        }
      }
    }
    if (Array.isArray(o.extra_fields_config)) {
      for (const entry of o.extra_fields_config) {
        if (!entry || typeof entry !== "object") continue;
        const e = entry as Record<string, unknown>;
        const srcTable = typeof e.source_table === "string" ? e.source_table : "";
        const srcField = typeof e.source_field === "string" ? e.source_field : "";
        const tgtTable = typeof e.target_table === "string" ? e.target_table : "";
        const customName = typeof e.custom_column_name === "string" ? e.custom_column_name : "";
        const storage = typeof e.storage_strategy === "string" ? e.storage_strategy : "";
        const isNewTbl = e.is_new_table === true;
        if (isNewTbl && tgtTable) newTablesSet.add(tgtTable);
        if (storage === "custom" && srcTable && srcField) {
          const key = `${srcTable}.${srcField}`;
          newColumnFields.add(key);
          if (customName) customColumnNames.set(key, customName);
          if (tgtTable) customTargetTables.set(key, tgtTable);
        }
        if (isNewTbl && srcTable) newSourceTablesSet.add(srcTable);
      }
    }
    // T1_new_table / T1_manual rows stamped by the pre-semantic node already
    // count as approved — collect them so we don't show them as unmappable.
    const tier1 = o.tier1_mappings_by_table ?? o.tier1_approved_by_table;
    if (tier1 && typeof tier1 === "object" && !Array.isArray(tier1)) {
      for (const [tbl, mappings] of Object.entries(tier1 as Record<string, unknown>)) {
        if (!Array.isArray(mappings)) continue;
        for (const m of mappings) {
          if (!m || typeof m !== "object") continue;
          const mm = m as Record<string, unknown>;
          const tier = typeof mm.tier === "string" ? mm.tier : "";
          if (tier !== "T1_new_table" && tier !== "T1_manual") continue;
          const sf = typeof mm.source_field === "string" ? mm.source_field : "";
          const tf = typeof mm.target_field === "string" ? mm.target_field : "";
          if (sf) {
            const key = `${tbl}.${sf}`;
            newColumnFields.add(key);
            // Mark the table as a new-source so any other field on the same
            // table also relabels — even if its specific tier1 entry didn't
            // get persisted in the slim payload.
            newSourceTablesSet.add(tbl);
            if (tf) customColumnNames.set(key, tf);
          }
        }
      }
    }
  }

  // Resolve routing now that newTablesSet has accumulated across all nodes.
  for (const [src, tgt] of pendingRoutings) {
    if (newTablesSet.has(tgt)) newSourceTablesSet.add(src);
  }

  return { newTablesSet, newSourceTablesSet, newColumnFields, customColumnNames, customTargetTables };
}

function Node3Semantic({
  payload,
  allNodes,
}: {
  payload: Record<string, unknown>;
  allNodes?: NodeInfo[];
}) {
  const resultsRaw = readRecordArray(payload.semantic_results);
  const tableNamesFromPayload = readStringArray(payload.table_names);
  const defaultTable = tableNamesFromPayload[0] ?? "data";

  // Cross-reference snapshot with user's later decisions (new tables / new
  // columns approved at later gates). Without this the historical snapshot
  // keeps flagging those fields as "unmappable" even though they've since
  // been approved as new columns / new-table columns.
  const newCols = buildNewColumnLookup(allNodes);

  // A source routed to an existing plenum_cafm table is never a "new table" — its columns are
  // matched into / added to that table (ALTER ADD COLUMN), never CREATE TABLE. Guards against
  // works → assets / WorkOrders_2 → work_orders being relabelled "new table column". Derived
  // from the columns map + explicit list; safe no-op if neither is in the payload.
  const reclassifyRouting = readRecord(payload.table_routing) ?? {};
  const existingCanonicalTables = new Set<string>(
    [
      ...Object.keys(readRecord(payload.canonical_columns_by_table) ?? {}),
      ...readStringArray(payload.existing_canonical_tables),
    ].map((t) => t.toLowerCase()),
  );
  const routesToExistingTable = (tbl: string): boolean => {
    const tgt = String(reclassifyRouting[tbl] ?? "").trim().toLowerCase();
    return tgt.length > 0 && existingCanonicalTables.has(tgt);
  };
  // A field whose target is a REAL existing column on its routed table is a match INTO that
  // column, not a new column — even when the backend stamped tier "T1_manual" (the manual-resolve
  // path uses that tier for matches to existing columns too). Without this, property_ref → site_id
  // (a real assets column) shows "new column" while its siblings show "auto". Needs
  // canonical_columns_by_table in the payload (carried since the semantic step-pause was
  // extended); safe no-op when that map is absent.
  const canonicalColumnsByTable = readRecord(payload.canonical_columns_by_table) ?? {};
  const targetFieldIsExistingColumn = (tbl: string, targetField?: string | null): boolean => {
    const tf = String(targetField ?? "").trim().toLowerCase();
    if (!tf) return false;
    const tgt = String(reclassifyRouting[tbl] ?? "").trim();
    const cols = canonicalColumnsByTable[tgt] ?? canonicalColumnsByTable[tgt.toLowerCase()];
    return Array.isArray(cols) && cols.some((c) => String(c).trim().toLowerCase() === tf);
  };

  function reclassify(tbl: string, sf: string, base: SemanticResult): SemanticResult {
    const key = `${tbl}.${sf}`;
    // Whole-table "new source" only when it is NOT routed to an existing table. A specific field
    // can still be a new column on an existing table — but only when its target is NOT already a
    // real column on the routed table (else it is a match into that existing column, e.g.
    // property_ref → site_id on assets).
    const isNewSource = newCols.newSourceTablesSet.has(tbl) && !routesToExistingTable(tbl);
    const isNewColumn =
      newCols.newColumnFields.has(key) && !targetFieldIsExistingColumn(tbl, base.target_field);
    if (!isNewSource && !isNewColumn) return base;
    const customName = newCols.customColumnNames.get(key);
    return {
      ...base,
      status: "new_column",
      target_field: customName ?? base.target_field,
      rationale: isNewSource
        ? "Auto-approved column on a user-created new table"
        : "Approved as a new column at the field-mapping gate",
      tier: isNewSource ? "T1_new_table" : "T1_manual",
      confidence: 1,
    };
  }

  const results: SemanticResult[] = [];

  if (resultsRaw.length) {
    for (const r of resultsRaw) {
      const statusRaw = readString(r.status) ?? "flagged";
      const status =
        statusRaw === "auto" || statusRaw === "flagged" || statusRaw === "unmappable" || statusRaw === "new_column"
          ? (statusRaw as SemanticResult["status"])
          : "flagged";
      const tbl = readString(r.table) ?? readString(r.source_table) ?? defaultTable;
      const sf = readString(r.source_field) ?? "—";
      results.push(
        reclassify(tbl, sf, {
          table: tbl,
          source_field: sf,
          target_field: readString(r.target_field),
          confidence: readNumber(r.confidence),
          status,
          best_target: readString(r.best_target),
          best_confidence: readNumber(r.best_confidence),
          tier: readString(r.tier),
          rationale: readString(r.rationale),
        }),
      );
    }
  } else {
    const auto = readRecordArray(payload.tier2_auto_mappings);
    const flagged = readRecordArray(payload.tier2_flagged_mappings);

    for (const r of auto) {
      const tbl = readString(r.source_table) ?? defaultTable;
      const sf = readString(r.source_field) ?? "—";
      results.push(
        reclassify(tbl, sf, {
          table: tbl,
          source_field: sf,
          target_field: readString(r.target_field),
          confidence: readNumber(r.confidence),
          status: "auto",
          tier: readString(r.tier),
          rationale: readString(r.rationale),
        }),
      );
    }
    for (const r of flagged) {
      const tbl = readString(r.source_table) ?? defaultTable;
      const sf = readString(r.source_field) ?? "—";
      results.push(
        reclassify(tbl, sf, {
          table: tbl,
          source_field: sf,
          target_field: readString(r.target_field),
          confidence: readNumber(r.confidence),
          status: "flagged",
          tier: readString(r.tier),
          rationale: readString(r.rationale),
        }),
      );
    }

    const unmappableByTable = readRecord(payload.tier2_unmappable_by_table);
    if (unmappableByTable) {
      for (const [tbl, list] of Object.entries(unmappableByTable)) {
        for (const f of readStringArray(list)) {
          results.push(
            reclassify(tbl, f, {
              table: tbl,
              source_field: f,
              target_field: null,
              confidence: null,
              status: "unmappable",
            }),
          );
        }
      }
    } else {
      for (const f of readStringArray(payload.tier2_unmappable)) {
        results.push(
          reclassify(defaultTable, f, {
            table: defaultTable,
            source_field: f,
            target_field: null,
            confidence: null,
            status: "unmappable",
          }),
        );
      }
    }
  }

  // When the lookup has any "new column / new table" decisions, prefer the
  // re-classified counts derived from the array — the payload-supplied counts
  // are frozen from the moment Node 4 paused and don't reflect the later
  // user decisions that moved fields from "unmappable" to "new_column".
  const hasNewColumnRelabels =
    newCols.newColumnFields.size > 0 || newCols.newSourceTablesSet.size > 0;

  // "Auto accepted" = Tier-1 approved + Tier-2 auto (backend auto_accepted_count). Prefer it so
  // the count reflects the COMPLETE state, not just the Tier-2 auto subset (which read 0 here
  // even though the Tier-1 deterministic mappings were already approved).
  const t2Auto = hasNewColumnRelabels
    ? results.filter((r) => r.status === "auto").length
    : (readNumber(payload.auto_accepted_count) ??
       readNumber(payload.t2_auto) ??
       readNumber(payload.t2_auto_count) ??
       readNumber(payload.tier2_auto) ??
       readNumber(payload.tier2_auto_mapped) ??
       readNumber(payload.tier2_auto_count) ??
       results.filter((r) => r.status === "auto").length);
  const flaggedCount = hasNewColumnRelabels
    ? results.filter((r) => r.status === "flagged").length
    : (readNumber(payload.flagged) ??
       readNumber(payload.flagged_count) ??
       readNumber(payload.tier2_flagged) ??
       readNumber(payload.tier2_flagged_count) ??
       results.filter((r) => r.status === "flagged").length);
  const unmappableCount = hasNewColumnRelabels
    ? results.filter((r) => r.status === "unmappable").length
    : (readNumber(payload.unmappable) ??
       readNumber(payload.unmappable_count) ??
       readNumber(payload.tier2_unmappable) ??
       readNumber(payload.tier2_unmappable_count) ??
       results.filter((r) => r.status === "unmappable").length);
  const newColumnCount = results.filter((r) => r.status === "new_column").length;

  const [filter, setFilter] = useState<SemanticFilter>("all");

  const autoItems = results.filter((r) => r.status === "auto");
  const flaggedItems = results.filter((r) => r.status === "flagged");
  const unmappableItems = results.filter((r) => r.status === "unmappable");
  const newColumnItems = results.filter((r) => r.status === "new_column");

  const visible =
    filter === "all"
      ? results
      : filter === "auto"
        ? autoItems
        : filter === "flagged"
          ? flaggedItems
          : filter === "new_column"
            ? newColumnItems
            : unmappableItems;

  const byTable: Record<string, SemanticResult[]> = {};
  for (const r of visible) {
    const tbl = r.table || defaultTable;
    if (!byTable[tbl]) byTable[tbl] = [];
    byTable[tbl].push(r);
  }

  const showNewColumnTile = newColumnCount > 0;

  return (
    <div className="space-y-4">
      <div className={`grid grid-cols-1 ${showNewColumnTile ? "sm:grid-cols-4" : "sm:grid-cols-3"} gap-4`}>
        <Metric label="Auto accepted" value={t2Auto} accent="green" />
        <Metric label="Flagged for review" value={flaggedCount} accent="amber" />
        <Metric label="Unmappable" value={unmappableCount} accent="red" />
        {showNewColumnTile ? (
          <Metric label="New columns approved" value={newColumnCount} accent="indigo" />
        ) : null}
      </div>

      {results.length ? (
        <>
          <div className="flex flex-wrap gap-2">
            {[
              { key: "all" as const, label: `All (${results.length})` },
              { key: "auto" as const, label: `Auto (${autoItems.length})` },
              { key: "flagged" as const, label: `Flagged (${flaggedItems.length})` },
              { key: "unmappable" as const, label: `Unmappable (${unmappableItems.length})` },
              ...(showNewColumnTile
                ? [{ key: "new_column" as const, label: `New columns (${newColumnItems.length})` }]
                : []),
            ].map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => setFilter(t.key)}
                className={`text-xs font-medium px-3 py-1.5 rounded-lg transition-colors ${
                  filter === t.key ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div className="space-y-3">
            {Object.entries(byTable).map(([tbl, fields]) => {
              // Show where this source table routes to (works → assets, WorkOrders_2 → work_orders)
              // so the header reflects the destination, not just the source sheet name.
              const routedTarget = String(reclassifyRouting[tbl] ?? "").trim();
              return (
              <div key={tbl} className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
                <div className="px-5 py-3 bg-slate-50 border-b border-slate-100">
                  <span className="font-semibold text-slate-700 text-sm">{tbl}</span>
                  {routedTarget ? (
                    <>
                      <span className="mx-1.5 text-slate-400">→</span>
                      <span className="font-semibold text-indigo-600 text-sm">{routedTarget}</span>
                    </>
                  ) : null}
                  <span className="text-xs text-slate-400 ml-2">
                    {fields.length} field{fields.length > 1 ? "s" : ""}
                  </span>
                </div>
                <div>
                  {fields.map((r, i) => (
                    <SemanticRow key={`${r.table}.${r.source_field}.${i}`} result={r} />
                  ))}
                </div>
              </div>
              );
            })}
          </div>
        </>
      ) : (
        // 0 results here is the SUCCESS case, not an empty/error state: Tier-2 semantic mapping
        // only runs on fields left unresolved after deterministic mapping, and the backend
        // early-returns when there are none (semantic_mapper_node: total_unresolved == 0). Make
        // that explicit so "0 · 0 · 0" doesn't read like something went wrong.
        <div className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-6 text-center">
          <p className="text-sm font-medium text-emerald-800">No fields needed semantic matching</p>
          <p className="text-xs text-slate-500 mt-1">
            Every column was already resolved by deterministic mapping — or approved as a new column
            at the pre-semantic gate — so there was nothing left for Tier-2 semantic mapping. This is
            expected.
          </p>
        </div>
      )}
    </div>
  );
}

function SemanticRow({ result }: { result: SemanticResult }) {
  const isUnmappable = result.status === "unmappable";
  const isNewColumn = result.status === "new_column";
  const rawConf =
    isUnmappable && !isNewColumn ? (result.best_confidence ?? result.confidence) : result.confidence;
  const confPct = rawConf != null ? Math.round(rawConf * 100) : null;

  const displayTarget = isUnmappable
    ? (result.best_target ?? null)
    : (result.target_field ?? null);

  const statusConfig =
    result.status === "auto"
      ? { rowBg: "hover:bg-green-50/30", icon: <CheckCircle size={13} className="text-green-500 shrink-0" />, badge: "bg-green-100 text-green-700", label: "auto" }
      : result.status === "flagged"
        ? { rowBg: "hover:bg-amber-50/30", icon: <AlertTriangle size={13} className="text-amber-500 shrink-0" />, badge: "bg-amber-100 text-amber-700", label: "flagged" }
        : result.status === "new_column"
          ? {
              rowBg: "hover:bg-indigo-50/30",
              icon: <CheckCircle size={13} className="text-indigo-600 shrink-0" />,
              badge: "bg-indigo-100 text-indigo-700",
              label: result.tier === "T1_new_table" ? "new table column" : "new column",
            }
          : { rowBg: "hover:bg-red-50/30", icon: <AlertTriangle size={13} className="text-red-400 shrink-0" />, badge: "bg-red-100 text-red-600", label: "unmappable" };

  return (
    <div className={`flex items-center gap-3 px-5 py-2.5 border-b border-slate-50 last:border-b-0 transition-colors ${statusConfig.rowBg}`}>
      {statusConfig.icon}
      <span className="font-mono text-xs text-slate-700 w-44 truncate shrink-0" title={result.source_field}>
        {result.source_field}
      </span>
      <span className="text-slate-300 text-xs shrink-0">→</span>

      <span
        className={`font-mono text-xs flex-1 truncate ${
          isNewColumn
            ? "text-indigo-700"
            : isUnmappable && displayTarget
              ? "text-slate-400 line-through"
              : "text-indigo-700"
        }`}
        title={displayTarget ?? undefined}
      >
        {displayTarget ?? <span className="text-slate-400">—</span>}
      </span>

      {isUnmappable && !isNewColumn && displayTarget ? (
        <span className="text-xs text-slate-400 italic shrink-0">best attempt</span>
      ) : null}
      {isNewColumn ? (
        <span className="text-xs text-indigo-500 italic shrink-0">approved</span>
      ) : null}

      {confPct != null && !isNewColumn ? (
        <div className="flex items-center gap-2 shrink-0 w-24">
          <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${confPct >= 85 ? "bg-green-500" : confPct >= 65 ? "bg-amber-500" : "bg-red-400"}`}
              style={{ width: `${confPct}%` }}
            />
          </div>
          <span className={`text-xs font-mono w-8 text-right ${confPct >= 85 ? "text-green-600" : confPct >= 65 ? "text-amber-600" : "text-red-500"}`}>
            {confPct}%
          </span>
        </div>
      ) : null}

      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium shrink-0 ${statusConfig.badge}`}>
        {statusConfig.label}
      </span>
    </div>
  );
}

type FmDecisionKind = "mapped" | "override" | "rejected" | "new" | "raw" | "skipped";
type FmDecisionRow = { table: string; sourceField: string; target: string; kind: FmDecisionKind };

/** Flatten the user's persisted field-mapping decisions into per-column rows. */
function buildFieldMappingDecisions(draft: unknown): FmDecisionRow[] {
  const env = parseEnvelope(draft);
  const body = env?.body;
  if (!body) return [];
  const rows: FmDecisionRow[] = [];
  for (const [table, decisions] of Object.entries(body.flagged ?? {})) {
    for (const d of decisions ?? []) {
      const sourceField = String(d.source_field ?? "").trim();
      if (!sourceField) continue;
      if (d.action === "override")
        rows.push({ table, sourceField, target: String(d.target_field ?? ""), kind: "override" });
      else if (d.action === "reject")
        rows.push({ table, sourceField, target: "", kind: "rejected" });
      else rows.push({ table, sourceField, target: String(d.target_field ?? ""), kind: "mapped" });
    }
  }
  for (const [table, decisions] of Object.entries(body.unmapped ?? {})) {
    for (const u of decisions ?? []) {
      const sourceField = String(u.source_field ?? "").trim();
      if (!sourceField) continue;
      if (u.action === "custom")
        rows.push({ table, sourceField, target: String(u.custom_column_name ?? sourceField), kind: "new" });
      else if (u.action === "raw_metadata")
        rows.push({ table, sourceField, target: "raw_metadata", kind: "raw" });
      else rows.push({ table, sourceField, target: "", kind: "skipped" });
    }
  }
  return rows;
}

const FM_DECISION_META: Record<FmDecisionKind, { label: string; cls: string }> = {
  mapped: { label: "Semantic match", cls: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  override: { label: "User override", cls: "bg-blue-50 text-blue-700 ring-blue-200" },
  new: { label: "New column", cls: "bg-indigo-50 text-indigo-700 ring-indigo-200" },
  raw: { label: "Raw metadata", cls: "bg-amber-50 text-amber-700 ring-amber-200" },
  skipped: { label: "Skipped", cls: "bg-slate-100 text-slate-500 ring-slate-200" },
  rejected: { label: "Rejected", cls: "bg-rose-50 text-rose-600 ring-rose-200" },
};

/** Shows what the user decided at the column-mapping step (semantic target / new
 *  column / raw / skipped), so the preprocess output is explained by its decisions. */
function ColumnMappingDecisions({ draft }: { draft: unknown }) {
  const rows = buildFieldMappingDecisions(draft);
  if (rows.length === 0) return null;
  const byTable = new Map<string, FmDecisionRow[]>();
  for (const r of rows) {
    const list = byTable.get(r.table) ?? [];
    list.push(r);
    byTable.set(r.table, list);
  }
  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Edit3 size={14} className="text-indigo-600" />
        <span className="text-sm font-semibold text-slate-800">Your column-mapping decisions</span>
        <span className="text-xs text-slate-400">({rows.length})</span>
      </div>
      <p className="text-[11px] text-slate-400 -mt-1">
        What you chose for each reviewed column in the previous step — reflected in the data below.
      </p>
      {[...byTable.entries()].map(([table, list]) => (
        <div key={table} className="space-y-1">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{table}</div>
          <div className="rounded-lg border border-slate-100 divide-y divide-slate-100">
            {list.map((r, i) => {
              const meta = FM_DECISION_META[r.kind];
              return (
                <div key={`${r.sourceField}-${i}`} className="flex items-center gap-2 px-2.5 py-1.5 text-xs">
                  <span className="font-mono text-slate-700 truncate min-w-0 flex-1">{r.sourceField}</span>
                  {r.target ? (
                    <>
                      <ArrowRight size={11} className="shrink-0 text-slate-300" />
                      <span className="font-mono text-slate-600 truncate min-w-0 flex-1">{r.target}</span>
                    </>
                  ) : (
                    <span className="min-w-0 flex-1" />
                  )}
                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ${meta.cls}`}>
                    {meta.label}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function Node5Preprocess({
  payload,
  fieldMappingDraft,
}: {
  payload: Record<string, unknown>;
  fieldMappingDraft?: unknown;
}) {
  // Each metric resolves to `null` if no known key exists in the payload —
  // the Metric tile shows "—" rather than a misleading 0. A genuine zero from
  // the backend (e.g. warnings=0 after a clean run) still renders as 0.
  const rowsCleaned =
    readNumber(payload.rows_cleaned) ??
    readNumber(payload.rows_after_dedup) ??
    readNumber(payload.total_cleaned_rows) ??
    readNumber(payload.cleaned_rows) ??
    readNumber(payload.rows_after_clean) ??
    null;
  const rowsOriginal =
    readNumber(payload.total_original_rows) ??
    readNumber(payload.original_rows) ??
    readNumber(payload.rows_before) ??
    null;
  const dedupRatioRaw =
    readNumber(payload.dedup_ratio) ??
    readNumber(payload.dedupe_ratio) ??
    null;
  const dedupRatioPct =
    dedupRatioRaw != null
      ? Math.round((dedupRatioRaw <= 1 ? dedupRatioRaw : dedupRatioRaw / 100) * 100)
      : null;
  const warnings =
    readCount(payload.warnings) ??
    readCount(payload.warning_count) ??
    readCount(payload.warning_messages) ?? // array of warning strings
    readCount(payload.quality_warnings) ??
    readCount(payload.quality_warning_count) ??
    null;
  const warningMessages = (Array.isArray(payload.warning_messages) ? payload.warning_messages : [])
    .filter((x) => typeof x === "string") as string[];

  const previewsRaw = readRecord(payload.table_previews) ?? readRecord(payload.table_previews_by_table) ?? {};
  const tableNames = Object.keys(previewsRaw);
  const [openTable, setOpenTable] = useState<string | null>(null);
  const activeTable = openTable ?? tableNames[0] ?? null;

  const activePreview = activeTable ? readRecord(previewsRaw[activeTable]) : null;
  const columnsAll = readStringArray(activePreview?.columns);
  const totalRows = readNumber(activePreview?.total_rows) ?? 0;
  const rowsRaw = Array.isArray(activePreview?.rows) ? activePreview?.rows : [];

  const previewRowsAll: string[][] = [];
  for (const r of rowsRaw) {
    if (Array.isArray(r)) previewRowsAll.push(r.map((c) => String(c ?? "")));
    else if (isRecord(r) && columnsAll.length) previewRowsAll.push(columnsAll.map((c) => String(r[c] ?? "")));
  }
  const shownRowsAll = previewRowsAll.slice(0, 3);

  // Collapse columns that are EXACT duplicates by value (e.g. `id` and
  // `asset_ref` both holding the asset code) so the same data isn't shown twice.
  // Conservative: only drop a later column when an earlier one has identical
  // values across every previewed row AND there are ≥2 distinct non-empty values
  // — so genuine id/code duplicates collapse, but coincidentally-equal columns
  // (e.g. a `status` that's "Active" in all 3 rows) are never hidden.
  const keepIdx: number[] = [];
  for (let c = 0; c < columnsAll.length; c++) {
    const colVals = shownRowsAll.map((r) => r[c] ?? "");
    const distinct = new Set(colVals.filter((v) => v !== ""));
    const isDuplicate =
      distinct.size >= 2 &&
      keepIdx.some((kc) => shownRowsAll.every((r, i) => (r[kc] ?? "") === colVals[i]));
    if (!isDuplicate) keepIdx.push(c);
  }
  const columns = keepIdx.map((i) => columnsAll[i]);
  const shownRows = shownRowsAll.map((r) => keepIdx.map((i) => r[i] ?? ""));
  const hiddenDuplicateCols = columnsAll.length - columns.length;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Metric label="Rows after dedup" value={rowsCleaned} accent="green" />
        {rowsOriginal != null ? (
          <Metric label="Rows before dedup" value={rowsOriginal} accent="indigo" />
        ) : dedupRatioPct != null ? (
          <Metric label="Dedup ratio" value={`${dedupRatioPct}%`} accent="indigo" />
        ) : null}
        <Metric
          label="Quality warnings"
          value={warnings}
          accent={warnings != null && warnings > 0 ? "amber" : "green"}
        />
      </div>

      {warningMessages.length ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 space-y-1">
          <p className="text-xs font-semibold text-amber-700 mb-1">Quality notes</p>
          {warningMessages.slice(0, 30).map((w, i) => (
            <p key={`${i}-${w}`} className="text-xs text-amber-700 flex gap-2">
              <span className="shrink-0 text-amber-400">•</span>
              <span className="min-w-0">{w}</span>
            </p>
          ))}
        </div>
      ) : null}

      <ColumnMappingDecisions draft={fieldMappingDraft} />

      {tableNames.length && activeTable ? (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
          <div className="px-4 pt-4">
            <div className="flex flex-wrap gap-2">
              {tableNames.map((t) => {
                const p = readRecord(previewsRaw[t]);
                const tr = readNumber(p?.total_rows) ?? 0;
                const isActive = t === activeTable;
                return (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setOpenTable(t)}
                    className={`text-xs font-medium px-3 py-1.5 rounded-lg transition-colors ${
                      isActive ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                    }`}
                  >
                    {t}
                    {tr ? <span className="ml-2 opacity-80">{tr.toLocaleString()}r</span> : null}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="p-4">
            {columns.length && shownRows.length ? (
              <div className="rounded-xl border border-slate-200 overflow-auto">
                <table className="min-w-full text-xs">
                  <thead className="bg-slate-50 border-b border-slate-200">
                    <tr>
                      {columns.map((c) => (
                        <th key={c} className="text-left px-3 py-2 text-slate-600 font-semibold whitespace-nowrap">
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {shownRows.map((r, idx) => (
                      <tr key={`${activeTable}-${idx}`} className="hover:bg-slate-50">
                        {columns.map((c, j) => (
                          <td key={`${c}-${j}`} className="px-3 py-2 text-slate-700 whitespace-nowrap">
                            {r[j] || "—"}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-sm text-slate-500">No preview rows available.</div>
            )}

            {totalRows ? (
              <div className="mt-2 text-xs text-slate-400 italic">
                Showing first {shownRows.length} row{shownRows.length === 1 ? "" : "s"} of {totalRows.toLocaleString()} total
                {hiddenDuplicateCols > 0
                  ? ` · ${hiddenDuplicateCols} duplicate column${hiddenDuplicateCols === 1 ? "" : "s"} hidden`
                  : ""}
              </div>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-5">
          <div className="text-sm font-semibold text-slate-800">Preprocess results</div>
          <div className="mt-1 text-sm text-slate-500">Preview data is available in the right sidebar.</div>
        </div>
      )}
    </div>
  );
}

/**
 * Pull the actual FK / hierarchy relationship list out of a node payload,
 * trying every shape the backend uses across detection (relationships_preview),
 * verification (confirmed_hierarchies) and gate (hierarchies_to_review/review_items).
 * Returns [] when none present. No truncation — every detected FK is returned.
 */
function readFkList(payload: Record<string, unknown>): Array<Record<string, unknown>> {
  for (const key of [
    "relationships_preview",
    "confirmed_hierarchies",
    "hierarchies_to_review",
    "fk_candidates",
    "confirmed_fks",
    "foreign_keys",
    "fks",
    "relationships",
    "hierarchies",
    "review_items",
  ]) {
    const arr = readRecordArray(payload[key]);
    // review_items mixes FK rows with cycle/orphan alerts — keep only FK-like rows.
    const fkRows =
      key === "review_items"
        ? arr.filter((r) => readString(r.source_table) && readString(r.target_table))
        : arr;
    if (fkRows.length > 0) return fkRows;
  }
  return [];
}

/** Render a full, non-truncated FK relationship list (source.col → target.col). */
function HierarchyFkList({ items }: { items: Array<Record<string, unknown>> }) {
  if (!items.length) return null;
  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      <div className="px-4 py-2.5 border-b border-slate-100 text-xs font-semibold text-slate-500 uppercase tracking-wider">
        Foreign keys ({items.length})
      </div>
      <div className="divide-y divide-slate-100 max-h-80 overflow-auto">
        {items.map((it, idx) => {
          const st = readString(it.source_table) ?? "";
          const sc = readString(it.source_column) ?? "";
          const tt = readString(it.target_table) ?? "";
          const tc = readString(it.target_column) ?? "";
          const rel = readString(it.relationship_type);
          const confRaw = readNumber(it.confidence);
          const confPct = confRaw != null ? `${Math.round(confRaw * 100)}%` : null;
          const readOnly = it.read_only === true || it.system_default === true || it.mapping_note === true;
          return (
            <div
              key={`${st}.${sc}->${tt}.${tc}#${idx}`}
              className="px-4 py-2 flex items-center justify-between gap-3 text-xs"
            >
              <div className="min-w-0 font-mono text-slate-700 truncate">
                <span>{st}{sc ? `.${sc}` : ""}</span>
                <span className="mx-2 text-slate-300">→</span>
                <span className="text-indigo-700">{tt}{tc ? `.${tc}` : ""}</span>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {rel ? (
                  <span className="uppercase font-mono text-[10px] text-slate-400">{rel}</span>
                ) : null}
                {readOnly ? (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-purple-100 text-purple-700">
                    reference
                  </span>
                ) : null}
                {confPct ? (
                  <span className={`font-mono ${confRaw != null && confRaw >= 0.8 ? "text-green-600" : "text-amber-600"}`}>
                    {confPct}
                  </span>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Node6Hierarchy({ payload }: { payload: Record<string, unknown> }) {
  // Each line first tries the explicit numeric counter, then the same name
  // as an array (in case the backend ships the list of items and we infer
  // the count from .length). Order matters — most explicit counter first.
  const fk =
    readCount(payload.confirmed_fks_count) ??
    readCount(payload.confirmed_fks) ??
    readCount(payload.fk_count) ??
    readCount(payload.fks_count) ??
    readCount(payload.fks) ??
    readCount(payload.foreign_keys_count) ??
    readCount(payload.foreign_key_count) ??
    readCount(payload.foreign_keys) ??
    readCount(payload.fk_candidates_count) ??
    readCount(payload.fk_candidates) ??
    readCount(payload.total_fks_count) ??
    readCount(payload.total_fks) ??
    readCount(payload.canonical_backed_fks) ??
    readCount(payload.hierarchy_count) ??
    readCount(payload.hierarchies_count) ??
    readCount(payload.hierarchies) ??
    null;
  const cycles =
    readCount(payload.cycles_count) ??
    readCount(payload.cycles) ??
    readCount(payload.hierarchy_cycles_count) ??
    readCount(payload.hierarchy_cycles) ??
    readCount(payload.cycle_count) ??
    null;
  const orphans =
    readCount(payload.orphans) ??
    readCount(payload.orphans_count) ??
    readCount(payload.orphan_count) ??
    readCount(payload.orphaned_records) ??
    readCount(payload.orphaned_records_count) ??
    readCount(payload.orphan_tables) ??
    null;
  const isolated =
    readCount(payload.isolated_table_count) ??
    readCount(payload.isolated_tables_count) ??
    readCount(payload.isolated_tables) ??
    readCount(payload.isolated_count) ??
    null;

  const cyclesAccent = cycles != null && cycles > 0 ? "red" : "green";
  const orphansAccent = orphans != null && orphans > 0 ? "amber" : "green";
  const showIsolated = isolated != null;

  return (
    <div className="space-y-4">
      <div className={`grid grid-cols-1 ${showIsolated ? "sm:grid-cols-4" : "sm:grid-cols-3"} gap-4`}>
        <Metric label="FK relationships" value={fk} accent="indigo" />
        <Metric label="Cycles detected" value={cycles} accent={cyclesAccent} />
        <Metric label="Orphaned records" value={orphans} accent={orphansAccent} />
        {showIsolated ? (
          <Metric label="Isolated tables" value={isolated} accent="indigo" />
        ) : null}
      </div>

      <HierarchyFkList items={readFkList(payload)} />

      {readNumber(payload.implicit_hierarchies_count) ? (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-4">
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Notes</div>
          <div className="text-sm text-slate-600">
            Detected {Number(payload.implicit_hierarchies_count).toLocaleString()} implicit hierarchies.
          </div>
        </div>
      ) : null}
    </div>
  );
}

// Node 3 — Pre-Semantic Review (Gate 0 completion snapshot).
// Shown only when the gate auto-passed without recorded human decisions (e.g.
// all-alias matches); otherwise the submitted decisions render via the
// pre-semantic decision history. node_log output carries approved /
// sent_to_semantic / new_tables. Without this it fell through to "Step complete".
function Node3PreSemanticGate({ payload }: { payload: Record<string, unknown> }) {
  const approved =
    readCount(payload.approved) ??
    readCount(payload.approved_count) ??
    null;
  const sentToSemantic =
    readCount(payload.sent_to_semantic) ??
    readCount(payload.flagged_for_semantic) ??
    null;
  const newTables = readCount(payload.new_tables) ?? null;

  const metrics: Array<{ label: string; value: unknown; accent: string }> = [
    { label: "Approved (Tier-1)", value: approved, accent: "green" },
    { label: "Sent to semantic", value: sentToSemantic, accent: "indigo" },
  ];
  if (newTables != null && newTables > 0) {
    metrics.push({ label: "New tables", value: newTables, accent: "amber" });
  }
  const cols = metrics.length === 3 ? "sm:grid-cols-3" : "sm:grid-cols-2";
  return (
    <div className="space-y-4">
      <div className={`grid grid-cols-1 ${cols} gap-4`}>
        {metrics.map((m) => (
          <Metric key={m.label} label={m.label} value={m.value} accent={m.accent} />
        ))}
      </div>
    </div>
  );
}

// Node 5 — Field Mapping Review (Gate 1 completion snapshot).
// Two possible shapes: the rich review payload (flagged/unmapped/auto-mapped by
// table) while the gate is open, or the post-submit completion summary written
// to node_logs (decisions_processed / extra_fields_config_count /
// overall_confidence). Render whichever arrived.
function Node5FieldMappingGate({
  payload,
  allNodes,
}: {
  payload: Record<string, unknown>;
  allNodes?: NodeInfo[];
}) {
  const hasReviewShape =
    isRecord(payload.flagged_by_table) ||
    isRecord(payload.review_items_by_table) ||
    isRecord(payload.unmapped_by_table) ||
    isRecord(payload.auto_mapped_by_table);
  if (hasReviewShape) return <Node4FieldMapping payload={payload} allNodes={allNodes} />;

  const decisions =
    readCount(payload.decisions_processed) ??
    readCount(payload.tier2_human_count) ??
    null;
  const newColumns =
    readCount(payload.extra_fields_config_count) ??
    readCount(payload.extra_fields_config) ??
    null;
  const confidenceRaw = readNumber(payload.overall_confidence);
  const confidencePct = confidenceRaw != null ? `${Math.round(confidenceRaw * 100)}%` : null;

  const metrics: Array<{ label: string; value: unknown; accent: string }> = [
    { label: "Decisions processed", value: decisions, accent: "indigo" },
    { label: "New columns added", value: newColumns, accent: "amber" },
  ];
  if (confidencePct != null) {
    metrics.push({ label: "Overall confidence", value: confidencePct, accent: "green" });
  }
  const cols = metrics.length === 3 ? "sm:grid-cols-3" : "sm:grid-cols-2";
  // Per-column DETAIL emitted by human_review_node — which source column was matched to which
  // destination, and which columns were CREATED as new. Counts alone didn't say what happened.
  const matched = Array.isArray(payload.matched_columns)
    ? (payload.matched_columns as Array<Record<string, unknown>>)
    : [];
  const created = Array.isArray(payload.new_columns)
    ? (payload.new_columns as Array<Record<string, unknown>>)
    : [];
  return (
    <div className="space-y-4">
      <div className={`grid grid-cols-1 ${cols} gap-4`}>
        {metrics.map((m) => (
          <Metric key={m.label} label={m.label} value={m.value} accent={m.accent} />
        ))}
      </div>
      {matched.length ? (
        <ColumnOutcomeList title="Matched columns" rows={matched} tone="emerald" />
      ) : null}
      {created.length ? (
        <ColumnOutcomeList title="New columns created" rows={created} tone="amber" />
      ) : null}
    </div>
  );
}

/** Parent→child hierarchy tree for the Gate-2 history snapshot. The snapshot previously carried
 *  only the flat foreign-key list, so "Previous run — click to review" had no graph structure.
 *  Rendered from the ASCII tree the backend builds off the CONFIRMED relationships. */
function HierarchyTreeBlock({ payload }: { payload: Record<string, unknown> }) {
  const raw = payload.hierarchy_tree;
  const tree = typeof raw === "string" ? raw.trim() : "";
  if (!tree) return null;
  const dupGroups = Array.isArray(payload.hierarchy_duplicate_groups)
    ? (payload.hierarchy_duplicate_groups as Array<Record<string, unknown>>)
    : [];
  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Hierarchy structure
        </span>
        <span className="text-[11px] text-slate-400">parent → child, from the confirmed relationships</span>
      </div>
      <pre className="overflow-x-auto rounded-lg bg-slate-50 px-3 py-2 font-mono text-[11.5px] leading-relaxed text-slate-700">
        {tree}
      </pre>
      {dupGroups.length ? (
        <div className="mt-2 text-[10.5px] text-slate-500">
          Duplicate sheets collapsed:{" "}
          {dupGroups
            .map((g) => {
              const members = Array.isArray(g.members) ? (g.members as unknown[]) : [];
              return `${String(g.representative ?? "")} ×${members.length || g.count || 1}`;
            })
            .join(", ")}
        </div>
      ) : null}
      <p className="mt-2 text-[10.5px] text-slate-400">
        A table appears under each of its parents — e.g. a work order with site, asset and vendor
        keys is listed under all three. That is three relationships, not duplicates.
      </p>
    </div>
  );
}

/** B14.1 completed-step card — the CONFIRMED per-column mapping after the user approves the
 *  column-mapping gate. Previously the gate left no record, so once approved there was nothing
 *  showing which column was kept, re-targeted, or created as a new column. */
function NodeColumnMappingConfirmed({ payload }: { payload: Record<string, unknown> }) {
  const rows = Array.isArray(payload.confirmed_columns)
    ? (payload.confirmed_columns as Array<Record<string, unknown>>)
    : [];
  const overridden = readCount(payload.columns_overridden) ?? 0;
  const newCols = readCount(payload.new_columns) ?? 0;
  const confirmed = readCount(payload.columns_confirmed) ?? rows.length;
  const userChanged = rows.filter((r) => String(r.outcome ?? "").includes("(user)"));
  const autoRows = rows.filter((r) => !String(r.outcome ?? "").includes("(user)"));
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Metric label="Columns confirmed" value={confirmed} accent="indigo" />
        <Metric label="Overridden by you" value={overridden} accent="amber" />
        <Metric label="New columns" value={newCols} accent="green" />
      </div>
      {userChanged.length ? (
        <ColumnOutcomeList title="Your overrides" rows={userChanged} tone="amber" />
      ) : null}
      {autoRows.length ? (
        <ColumnOutcomeList title="Confirmed mappings" rows={autoRows} tone="emerald" />
      ) : null}
    </div>
  );
}

/** Shared per-column outcome list for the completed-step cards (field-mapping review and the
 *  B14.1 column-mapping confirmation): source column → destination column, with the outcome. */
export function ColumnOutcomeList({
  title,
  rows,
  tone,
}: {
  title: string;
  rows: Array<Record<string, unknown>>;
  tone: "emerald" | "amber" | "indigo";
}) {
  const str = (v: unknown) => (typeof v === "string" && v.trim() ? v : null);
  const toneCls =
    tone === "emerald" ? "text-emerald-700" : tone === "amber" ? "text-amber-700" : "text-indigo-700";
  return (
    <details className="rounded-lg border border-slate-200 bg-white" open>
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2 text-[12px] font-semibold text-slate-700 hover:bg-slate-50">
        <span className="text-slate-400">▸</span>
        {title}
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500">
          {rows.length}
        </span>
      </summary>
      <div className="max-h-72 overflow-y-auto border-t border-slate-100">
        {rows.map((r, i) => {
          const srcTbl = str(r.source_table);
          const srcCol = str(r.source_field);
          const tgt = str(r.target_field);
          const destTbl = str(r.dest_table) ?? str(r.target_table);
          const outcome = str(r.outcome);
          const fmt = str(r.format);
          return (
            <div
              key={i}
              className="flex flex-wrap items-center gap-x-2 gap-y-0.5 border-b border-slate-50 px-4 py-1.5 text-[11.5px] last:border-b-0"
            >
              <span className="font-mono text-slate-500">{srcTbl ? `${srcTbl}.` : ""}</span>
              <span className="font-mono text-indigo-700">{srcCol ?? "—"}</span>
              <span className="text-slate-300">→</span>
              <span className={`font-mono ${toneCls}`}>{tgt ?? "new column"}</span>
              {destTbl ? <span className="text-[10px] text-slate-400">on {destTbl}</span> : null}
              {r.is_primary_key ? (
                <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[9.5px] font-semibold text-emerald-700">
                  PK
                </span>
              ) : null}
              {fmt ? <span className="text-[10px] text-slate-400">{fmt}</span> : null}
              {outcome ? <span className="ml-auto text-[10px] text-slate-400">{outcome}</span> : null}
            </div>
          );
        })}
      </div>
    </details>
  );
}

// Node 8 — Hierarchy Verification (Gate 2 completion snapshot).
// The verify-hierarchy node logs output={confirmed_hierarchy_count,
// corrections_applied, hierarchy_confirmed}; older/testing payloads also carry
// hierarchies_approved / cycles_resolved / confirmed_hierarchies. Without this
// renderer the step fell through to NodeGeneric "Step complete" and the user
// lost the hierarchy confirmation summary in both live history and archived runs.
function Node7HierarchyGate({ payload }: { payload: Record<string, unknown> }) {
  const confirmed =
    readCount(payload.confirmed_hierarchy_count) ??
    readCount(payload.confirmed_hierarchies) ??
    readCount(payload.hierarchies_approved) ??
    readCount(payload.total_hierarchies) ??
    null;
  const corrections =
    readCount(payload.corrections_applied) ??
    readCount(payload.hierarchy_corrections) ??
    readCount(payload.corrections) ??
    null;
  const cyclesResolved =
    readCount(payload.cycles_resolved) ??
    readCount(payload.resolved_cycles) ??
    null;
  const confirmedFlag =
    typeof payload.hierarchy_confirmed === "boolean" ? payload.hierarchy_confirmed : null;

  const metrics: Array<{ label: string; value: unknown; accent: string }> = [
    { label: "Hierarchies confirmed", value: confirmed, accent: "indigo" },
    { label: "Corrections applied", value: corrections, accent: "amber" },
  ];
  if (cyclesResolved != null) {
    metrics.push({ label: "Cycles resolved", value: cyclesResolved, accent: "green" });
  }
  const cols = metrics.length === 3 ? "sm:grid-cols-3" : "sm:grid-cols-2";

  return (
    <div className="space-y-4">
      <div className={`grid grid-cols-1 ${cols} gap-4`}>
        {metrics.map((m) => (
          <Metric key={m.label} label={m.label} value={m.value} accent={m.accent} />
        ))}
      </div>
      <HierarchyFkList items={readFkList(payload)} />
      <HierarchyTreeBlock payload={payload} />
      {confirmedFlag != null ? (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-4">
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Status</div>
          <div className="text-sm text-slate-600">
            {confirmedFlag
              ? "Hierarchy confirmed by reviewer — EL-M.7 passed (no unresolved cycles)."
              : "Hierarchy verification recorded — EL-M.7 did not pass."}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Node8Output({
  payload,
  allNodes,
}: {
  payload: Record<string, unknown>;
  allNodes?: NodeInfo[];
}) {
  // tables: backend can return a list of table names, a counter, or both.
  // readCount() collapses both shapes; we walk candidates from most specific
  // to least so an explicit counter wins over inferred length.
  const tablesFromPayload =
    readCount(payload.tables_exported) ??
    readCount(payload.tables_count) ??
    // Backend output_generator_node writes `table_count` (singular) in the
    // node-9 log output. The plural `tables_count` is not what we receive
    // unless something else emits it. This was the root cause of the
    // "Tables exported: —" empty tile.
    readCount(payload.table_count) ??
    readCount(payload.tables_written) ??
    readCount(payload.tables_total) ??
    readCount(payload.exported_tables_count) ??
    readCount(payload.exported_tables) ??
    readCount(payload.exported_table_names) ??
    readCount(payload.canonical_tables_touched) ??
    readCount(payload.total_tables) ??
    readCount(payload.tables_in_output) ??
    readCount(payload.target_tables) ??
    readCount(payload.target_table_count) ??
    readCount(payload.cafm_tables_written) ??
    readCount(payload.mapping_table_count) ??
    readCount(payload.tables) ?? // last — works both for numbers and lists
    null;

  // Fallback chain when the Node 9 step-paused payload doesn't carry the
  // table count: walk upstream nodes' outputs. Node 1 (ingestion) records
  // the parsed table count; later nodes echo `table_routing` which is keyed
  // by source table; the migration mapper records `tier1_mappings_by_table`
  // whose keys are source tables. Any of these are a strict upper bound.
  function tablesFromUpstreamNodes(): number | null {
    if (!allNodes?.length) return null;
    for (let i = allNodes.length - 1; i >= 0; i -= 1) {
      const out = allNodes[i]?.output;
      if (!out || typeof out !== "object") continue;
      const o = out as Record<string, unknown>;
      const direct =
        readCount(o.tables_exported) ??
        readCount(o.tables_count) ??
        readCount(o.table_count) ??
        readCount(o.tables_total) ??
        readCount(o.total_tables) ??
        readCount(o.target_tables) ??
        readCount(o.target_table_count) ??
        readCount(o.canonical_tables_touched) ??
        readCount(o.tables);
      if (direct != null) return direct;
      // Keys-of-record fallback (table_routing, tier1_mappings_by_table, etc.)
      for (const key of [
        "table_routing",
        "tier1_mappings_by_table",
        "tier1_approved_by_table",
        "unresolved_by_table",
      ]) {
        const v = o[key];
        if (v && typeof v === "object" && !Array.isArray(v)) {
          const n = Object.keys(v as Record<string, unknown>).length;
          if (n > 0) return n;
        }
      }
    }
    return null;
  }

  const tables = tablesFromPayload ?? tablesFromUpstreamNodes();

  const artifacts =
    readCount(payload.artifacts_uploaded) ??
    readCount(payload.artifacts_count) ??
    readCount(payload.artifacts_written) ??
    readCount(payload.artifact_count) ??
    readCount(payload.artifacts) ??
    readCount(payload.uploaded_artifacts) ??
    readCount(payload.output_artifacts) ??
    readCount(payload.new_tables_created) ??
    null;

  const jsonUrl =
    readString(payload.json_url) ??
    readString(payload.output_json_url) ??
    readString(payload.mapping_json_url) ??
    null;
  const csvUrl =
    readString(payload.csv_url) ??
    readString(payload.output_csv_url) ??
    null;
  const sqlUrl =
    readString(payload.sql_url) ??
    readString(payload.output_sql_url) ??
    null;
  const reportUrl =
    readString(payload.report_url) ??
    readString(payload.migration_report_url) ??
    readString(payload.pdf_url) ??
    null;

  const links = [
    { label: "JSON", url: jsonUrl, icon: <FileJson size={14} /> },
    { label: "CSV", url: csvUrl, icon: <FileText size={14} /> },
    { label: "SQL", url: sqlUrl, icon: <Database size={14} /> },
    { label: "PDF Report", url: reportUrl, icon: <FileBarChart size={14} /> },
  ].filter((l) => !!l.url);

  // CAFM-013 — the outputs are COMBINED across all tables (1 JSON + N CSV + 1 SQL + 1 PDF), not
  // 3-per-table. Show the per-type breakdown so the count reads clearly instead of as a miscount.
  const byType = (payload.artifacts_by_type ?? null) as Record<string, unknown> | null;
  const artifactBreakdown = byType
    ? (["json", "csv", "sql", "pdf"] as const)
        .map((k) => [k, readCount(byType[k])] as const)
        .filter(([, n]) => typeof n === "number" && n > 0)
        .map(([k, n]) => `${n} ${k.toUpperCase()}`)
        .join(" · ")
    : null;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Metric label="Tables exported" value={tables} accent="indigo" />
        <Metric label="Output files uploaded" value={artifacts} accent="green" />
      </div>
      {artifactBreakdown ? (
        <p className="-mt-2 text-[11px] leading-relaxed text-slate-500">
          Combined outputs across {typeof tables === "number" ? `${tables} table${tables === 1 ? "" : "s"}` : "all tables"} — {artifactBreakdown} (one JSON, SQL and PDF for the whole migration, one CSV per table).
        </p>
      ) : null}

      {links.length > 0 ? (
        <div>
          <p className="text-xs font-medium text-slate-500 mb-2">Download outputs</p>
          <div className="flex flex-wrap gap-2">
            {links.map((l) => (
              <a
                key={l.label}
                href={l.url!}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1.5 text-xs font-medium text-indigo-600 hover:text-indigo-800 bg-indigo-50 hover:bg-indigo-100 px-3 py-1.5 rounded-lg transition-colors"
              >
                {l.icon}
                {l.label}
              </a>
            ))}
          </div>
        </div>
      ) : (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-5">
          <div className="text-sm font-semibold text-slate-800">Outputs are being prepared</div>
          <div className="mt-1 text-sm text-slate-500">Download links will appear here when available.</div>
        </div>
      )}
    </div>
  );
}

function NodeGeneric({ title }: { title: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-5">
      <div className="text-sm font-semibold text-slate-800">{title}</div>
      <div className="mt-1 text-sm text-slate-500">Detailed output is available in the right sidebar.</div>
    </div>
  );
}

function Node4FieldMapping({
  payload,
  allNodes,
}: {
  payload: Record<string, unknown>;
  allNodes?: NodeInfo[];
}) {
  const flaggedByTable = readRecord(payload.flagged_by_table) ?? readRecord(payload.review_items_by_table) ?? {};
  const unmappedByTable = readRecord(payload.unmapped_by_table) ?? readRecord(payload.unmappable_items_by_table) ?? {};
  const autoMappedByTable = readRecord(payload.auto_mapped_by_table) ?? {};

  const totalFlagged = Object.values(flaggedByTable).reduce<number>((s, v) => s + readRecordArray(v).length, 0);
  const totalUnmapped = Object.values(unmappedByTable).reduce<number>((s, v) => s + readRecordArray(v).length, 0);
  const totalAuto = Object.values(autoMappedByTable).reduce<number>((s, v) => s + readRecordArray(v).length, 0);

  const allTableNames = Array.from(
    new Set([
      ...Object.keys(flaggedByTable),
      ...Object.keys(unmappedByTable),
      ...Object.keys(autoMappedByTable),
    ]),
  );

  const [expandedTable, setExpandedTable] = useState<string | null>(allTableNames[0] ?? null);
  const [localReview, setLocalReview] = useState<Record<string, "accept" | "reject" | "edit">>({});

  // Node 2 output for "previously matched columns" reference panel
  const node2 = allNodes?.find((n) => n.node_id === 2);
  const node2MappingsByTable = node2?.output
    ? (readRecord(node2.output.mappings_by_table) ?? {})
    : {};

  const TIER_META: Record<string, string> = {
    T1_exact: "bg-green-100 text-green-800",
    T1_variation: "bg-teal-100 text-teal-800",
    T1_alias: "bg-blue-100 text-blue-800",
    T1_regex: "bg-purple-100 text-purple-800",
    T1_registry: "bg-teal-100 text-teal-800",
    T1_llm: "bg-indigo-100 text-indigo-800",
    T2_semantic: "bg-amber-100 text-amber-800",
  };

  return (
    <div className="space-y-4">
      {/* Summary metrics */}
      <div className="grid grid-cols-3 gap-3">
        <Metric label="Flagged fields" value={totalFlagged} accent="amber" />
        <Metric label="Unmapped fields" value={totalUnmapped} accent="red" />
        <Metric label="Auto-mapped" value={totalAuto} accent="green" />
      </div>

      {/* Previous node columns reference (Node 2) */}
      {Object.keys(node2MappingsByTable).length > 0 && (
        <div className="rounded-xl border border-indigo-200 bg-indigo-50/50 p-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="h-5 w-5 rounded-full bg-indigo-200 text-indigo-700 flex items-center justify-center text-[10px] font-bold shrink-0">
              2
            </span>
            <span className="text-xs font-semibold text-indigo-700">
              Deterministic Matches (for comparison)
            </span>
          </div>
          <div className="space-y-2">
            {Object.entries(node2MappingsByTable).map(([tbl, mappings]) => {
              const rows = readRecordArray(mappings);
              return (
                <div key={tbl}>
                  <div className="text-[11px] font-semibold text-slate-600 mb-1">{tbl}</div>
                  <div className="flex flex-wrap gap-1">
                    {rows.slice(0, 6).map((r, i) => {
                      const sf = readString(r.source_field) ?? "—";
                      const tf = readString(r.target_field) ?? "—";
                      const conf = readNumber(r.confidence);
                      const confPct = conf != null ? Math.round(conf * 100) : null;
                      return (
                        <span
                          key={i}
                          className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-mono bg-white border border-indigo-200 text-slate-700"
                        >
                          {sf}
                          <span className="text-slate-400">→</span>
                          {tf}
                          {confPct != null && (
                            <span className={confPct >= 85 ? "text-green-600" : "text-amber-600"}>
                              {confPct}%
                            </span>
                          )}
                        </span>
                      );
                    })}
                    {rows.length > 6 && (
                      <span className="text-[10px] text-indigo-400">+{rows.length - 6} more</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Per-table field mapping — table first, then columns */}
      {allTableNames.length > 0 ? (
        <div className="space-y-3">
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
            Table-by-Table Field Mapping
          </div>
          {allTableNames.map((tbl) => {
            const flaggedRows = readRecordArray(flaggedByTable[tbl]);
            const unmappedRows = readRecordArray(unmappedByTable[tbl]);
            const autoRows = readRecordArray(autoMappedByTable[tbl]);
            const totalFields = flaggedRows.length + unmappedRows.length + autoRows.length;
            const isOpen = expandedTable === tbl;

            return (
              <div
                key={tbl}
                className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden"
              >
                <button
                  type="button"
                  className="w-full flex items-center justify-between px-5 py-3 hover:bg-slate-50 transition-colors text-left"
                  onClick={() => setExpandedTable(isOpen ? null : tbl)}
                >
                  <div className="flex items-center gap-2 flex-wrap min-w-0">
                    <span className="font-semibold text-slate-800 text-sm">{tbl}</span>
                    {autoRows.length > 0 && (
                      <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
                        {autoRows.length} auto
                      </span>
                    )}
                    {flaggedRows.length > 0 && (
                      <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-700">
                        {flaggedRows.length} flagged
                      </span>
                    )}
                    {unmappedRows.length > 0 && (
                      <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700">
                        {unmappedRows.length} unmapped
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-xs text-slate-400 tabular-nums">{totalFields} fields</span>
                    {isOpen ? (
                      <ChevronUp size={16} className="text-slate-400" />
                    ) : (
                      <ChevronDown size={16} className="text-slate-400" />
                    )}
                  </div>
                </button>

                {isOpen && (
                  <div className="border-t border-slate-100 divide-y divide-slate-50">
                    {/* Auto-mapped */}
                    {autoRows.map((row, i) => {
                      const sf = readString(row.source_field) ?? "—";
                      const tf = readString(row.target_field) ?? "—";
                      const conf = readNumber(row.confidence);
                      const confPct = conf != null ? Math.round(conf * 100) : null;
                      const tier = readString(row.tier) ?? "";
                      return (
                        <div key={`auto_${i}`} className="px-5 py-2.5 flex items-center gap-3 bg-green-50/30">
                          <CheckCircle size={14} className="text-green-500 shrink-0" />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="font-mono text-xs text-slate-700">{sf}</span>
                              <ArrowRight size={11} className="text-slate-300" />
                              <span className="font-mono text-xs text-indigo-700">{tf}</span>
                              {tier && (
                                <span
                                  className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${TIER_META[tier] ?? "bg-slate-100 text-slate-600"}`}
                                >
                                  {plainTierWord(tier.replace(/^T\d_/, ""))}
                                </span>
                              )}
                            </div>
                          </div>
                          {confPct != null && (
                            <div className="flex items-center gap-1.5 shrink-0">
                              <div className="w-14 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                                <div
                                  className={`h-full rounded-full ${confPct >= 85 ? "bg-green-500" : confPct >= 65 ? "bg-amber-500" : "bg-red-400"}`}
                                  style={{ width: `${confPct}%` }}
                                />
                              </div>
                              <span
                                className={`text-xs font-mono ${confPct >= 85 ? "text-green-600" : confPct >= 65 ? "text-amber-600" : "text-red-500"}`}
                              >
                                {confPct}%
                              </span>
                            </div>
                          )}
                        </div>
                      );
                    })}

                    {/* Flagged — with confidence bars, alternatives, and review CTAs */}
                    {flaggedRows.map((row, i) => {
                      const sf = readString(row.source_field) ?? "—";
                      const tf =
                        readString(row.target_field) ??
                        readString((row as Record<string, unknown>).suggested_target) ??
                        "—";
                      const conf = readNumber(row.confidence);
                      const confPct = conf != null ? Math.round(conf * 100) : null;
                      const key = `${tbl}.${sf}`;
                      const rv = localReview[key];

                      const altsRaw =
                        Array.isArray(row.suggestions)
                          ? (row.suggestions as unknown[])
                          : Array.isArray(row.alternatives)
                            ? (row.alternatives as unknown[])
                            : Array.isArray(row.variations)
                              ? (row.variations as unknown[])
                              : [];
                      const alts = altsRaw
                        .slice(0, 3)
                        .map((a) => ({
                          field:
                            typeof a === "string"
                              ? a
                              : isRecord(a)
                                ? (readString(a.target_field) ?? readString(a.field) ?? "")
                                : String(a),
                          conf: isRecord(a) ? readNumber(a.confidence) : null,
                        }))
                        .filter((a) => a.field.trim().length > 0);

                      return (
                        <div
                          key={`flagged_${i}`}
                          className={`px-5 py-3 ${rv === "accept" ? "bg-green-50/40" : rv === "reject" ? "bg-red-50/40" : rv === "edit" ? "bg-blue-50/40" : ""}`}
                        >
                          <div className="flex items-start gap-3">
                            <AlertTriangle size={14} className="text-amber-500 shrink-0 mt-0.5" />
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 flex-wrap mb-1">
                                <span className="font-mono text-xs text-slate-700">{sf}</span>
                                <ArrowRight size={11} className="text-slate-300" />
                                <span
                                  className={`font-mono text-xs ${rv === "reject" ? "text-red-400 line-through" : "text-indigo-700"}`}
                                >
                                  {tf}
                                </span>
                                {confPct != null && (
                                  <span
                                    className={`text-xs font-mono font-semibold ${confPct >= 85 ? "text-green-600" : confPct >= 65 ? "text-amber-600" : "text-red-500"}`}
                                  >
                                    {confPct}%
                                  </span>
                                )}
                              </div>
                              {confPct != null && (
                                <div className="flex items-center gap-1.5 mb-1.5">
                                  <div className="w-24 h-1 bg-slate-100 rounded-full overflow-hidden">
                                    <div
                                      className={`h-full rounded-full ${confPct >= 85 ? "bg-green-500" : confPct >= 65 ? "bg-amber-500" : "bg-red-400"}`}
                                      style={{ width: `${confPct}%` }}
                                    />
                                  </div>
                                </div>
                              )}
                              {/* Top 2-3 alternatives with scores */}
                              {alts.length > 0 && (
                                <div className="flex flex-wrap items-center gap-1">
                                  <span className="text-[10px] text-slate-400">Alt:</span>
                                  {alts.map((alt, ai) => (
                                    <span
                                      key={ai}
                                      className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-mono bg-slate-100 text-slate-500"
                                    >
                                      {alt.field}
                                      {alt.conf != null && (
                                        <span className="text-slate-400 ml-0.5">
                                          {Math.round(alt.conf * 100)}%
                                        </span>
                                      )}
                                    </span>
                                  ))}
                                </div>
                              )}
                              {rv === "edit" && (
                                <div className="mt-1 text-[11px] text-blue-700">
                                  Marked for edit in Gate 1 review.
                                </div>
                              )}
                            </div>
                            {/* Review CTAs */}
                            <div className="flex gap-1 shrink-0">
                              <button
                                type="button"
                                onClick={() =>
                                  setLocalReview((p) => ({ ...p, [key]: "accept" }))
                                }
                                className={`inline-flex items-center gap-0.5 text-xs font-medium px-2 py-1.5 rounded-lg transition-colors ${rv === "accept" ? "bg-green-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}
                              >
                                <CheckCircle size={11} />
                                Approve
                              </button>
                              <button
                                type="button"
                                onClick={() =>
                                  setLocalReview((p) => ({ ...p, [key]: "edit" }))
                                }
                                className={`inline-flex items-center gap-0.5 text-xs font-medium px-2 py-1.5 rounded-lg transition-colors ${rv === "edit" ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}
                              >
                                <Edit3 size={11} />
                                Edit
                              </button>
                              <button
                                type="button"
                                onClick={() =>
                                  setLocalReview((p) => ({ ...p, [key]: "reject" }))
                                }
                                className={`inline-flex items-center gap-0.5 text-xs font-medium px-2 py-1.5 rounded-lg transition-colors ${rv === "reject" ? "bg-red-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}
                              >
                                <XCircle size={11} />
                                Reject
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    })}

                    {/* Unmapped */}
                    {unmappedRows.map((row, i) => {
                      const sf = readString(row.source_field) ?? "—";
                      const samples = readStringArray(row.sample_values).slice(0, 2);
                      return (
                        <div
                          key={`unmapped_${i}`}
                          className="px-5 py-2.5 flex items-center gap-3 bg-red-50/30"
                        >
                          <Search size={14} className="text-red-400 shrink-0" />
                          <span className="font-mono text-xs text-slate-700">{sf}</span>
                          <span className="text-xs font-medium text-red-600 bg-red-100 px-2 py-0.5 rounded-full">
                            unmapped
                          </span>
                          {samples.length > 0 && (
                            <span className="text-[10px] text-slate-400 truncate">
                              {samples.join(", ")}
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <NodeGeneric title="Data Pre processing" />
      )}

      <div className="rounded-xl border border-indigo-100 bg-indigo-50 px-4 py-3 text-xs text-indigo-700">
        Click <strong>Continue</strong> below to proceed to the Human review gate (Table Structure Confirmation) where
        you can submit your final approve / reject / override decisions.
      </div>
    </div>
  );
}

export function MigrationStepSnapshot({
  stepKey,
  payload,
  allNodes,
  fieldMappingDraft,
}: {
  stepKey: string;
  payload: Record<string, unknown>;
  allNodes?: NodeInfo[];
  fieldMappingDraft?: unknown;
}) {
  // Node 1 — File Ingestion
  if (stepKey === "step_1_ingest") return <Node1Ingest payload={payload} />;
  // Node 2 — Deterministic Mapping
  if (stepKey === "step_2_deterministic" || stepKey === "step_2_deterministic_mapping") return <Node2Deterministic payload={payload} />;
  // B14.1 — column-mapping CONFIRMED snapshot. Emitted on the node-3 channel, so it is matched on
  // its payload marker (not stepKey) to avoid being rendered as a pre-semantic gate summary.
  if (payload.column_mapping_confirmed === true) return <NodeColumnMappingConfirmed payload={payload} />;
  // Node 3 — Pre-Semantic Review (Gate 0 completion)
  if (stepKey === "step_3_pre_semantic" || stepKey === "step_3_pre_semantic_review") return <Node3PreSemanticGate payload={payload} />;
  // Node 4 — Semantic Mapping (spec canonical keys + legacy aliases)
  if (
    stepKey === "step_4_semantic" || stepKey === "step_4_semantic_mapping" ||
    stepKey === "step_3_semantic" || stepKey === "step_3_semantic_mapping"
  ) return <Node3Semantic payload={payload} allNodes={allNodes} />;
  // Node 5 — Field Mapping Review (Gate 1) — completion summary or review payload
  if (stepKey === "step_5_field_mapping" || stepKey === "step_5_field_mapping_review") return <Node5FieldMappingGate payload={payload} allNodes={allNodes} />;
  // Legacy: field-mapping review shown as a pre-gate step pause
  if (stepKey === "step_4_field_mapping" || stepKey === "step_4_field_mapping_review") return <Node4FieldMapping payload={payload} allNodes={allNodes} />;
  // Node 6 — Data Preprocessing
  if (
    stepKey === "step_6_preprocess" || stepKey === "step_6_data_preprocessing" ||
    stepKey === "step_5_preprocess" || stepKey === "step_5_preprocess_validate"
  ) return <Node5Preprocess payload={payload} fieldMappingDraft={fieldMappingDraft} />;
  // Node 7 — Hierarchy Detection
  if (
    stepKey === "step_7_hierarchy" || stepKey === "step_7_hierarchy_detection" ||
    stepKey === "step_6_hierarchy" || stepKey === "step_6_resolve_hierarchy"
  ) return <Node6Hierarchy payload={payload} />;
  // Node 8 — Hierarchy Verification (Gate 2 completion)
  if (
    stepKey === "step_8_hierarchy_gate" || stepKey === "step_8_hierarchy" ||
    stepKey === "step_6_verify_hierarchy"
  ) return <Node7HierarchyGate payload={payload} />;
  // Node 9 — Output Generation
  if (
    stepKey === "step_9_output" || stepKey === "step_9_output_generation" ||
    stepKey === "step_8_output" || stepKey === "step_8_output_generation" ||
    stepKey === "step_9_write"
  ) return <Node8Output payload={payload} allNodes={allNodes} />;
  return <NodeGeneric title="Step complete" />;
}

export default function StepPause({ migrationId, stepKey, payload, onAdvanced, allNodes, fieldMappingDraft }: Props) {
  const [error, setError] = useState<string | null>(null);

  const { mutate: advance, isPending } = useMigrationAdvance({
    onSuccess: () => onAdvanced(),
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Failed to advance";
      // Stale-state race: the migration-status poll stops at a gate, so this step-pause view can
      // lag behind a run the backend has already advanced (to running or a HITL review gate).
      // When the advance is rejected for that reason, re-sync instead of stranding the user on
      // "Migration is not paused at a step … use the gate endpoints" — the refetch re-renders the
      // correct view (running card or the actual gate) without a manual page refresh.
      if (/not paused at a step|gate endpoints/i.test(msg)) {
        onAdvanced();
        return;
      }
      setError(msg);
    },
  });

  const payloadLabel = readString(payload.label) ?? readString(payload.title);
  const nodeTitle = payloadLabel ?? (NODE_LABELS[stepKey] ?? stepKey);

  // File Ingestion (Node 1) and Deterministic Mapping (Node 2) are non-HITL steps the pipeline
  // advances on its own — a manual Continue here is redundant, so hide the button and auto-advance
  // once on mount, letting the run flow straight to the pre-semantic gate. Idempotent: the /advance
  // CAS guard makes a concurrent orchestrator advance safe. On error we fall back to the Continue
  // button so the user is never stranded. (stepKey arrives normalized from the caller.)
  const isAutoAdvanceStep = AUTO_ADVANCE_STEP_KEYS.has(stepKey);
  const autoAdvancedRef = useRef(false);
  useEffect(() => {
    if (!isAutoAdvanceStep || autoAdvancedRef.current || isPending || error) return;
    autoAdvancedRef.current = true;
    advance({ migrationId });
  }, [isAutoAdvanceStep, isPending, error, migrationId, advance]);

  return (
    <div className="max-w-6xl">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <div className="w-10 h-10 rounded-xl bg-blue-100 flex items-center justify-center shrink-0">
          <CheckCircle size={20} className="text-blue-600" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-slate-900">{nodeTitle} — Complete</h2>
          <p className="text-sm text-slate-500">
            {isAutoAdvanceStep
              ? "Output below — continuing automatically to the next step."
              : "Review the output below, then continue to the next step."}
          </p>
        </div>
      </div>

      <div className="mb-6">
        <MigrationStepSnapshot
          stepKey={stepKey}
          payload={payload}
          allNodes={allNodes}
          fieldMappingDraft={fieldMappingDraft}
        />
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{error}</div>
      )}

      {isAutoAdvanceStep && !error ? (
        <div className="inline-flex items-center gap-2 text-sm text-slate-500">
          <span className="w-4 h-4 border-2 border-slate-300 border-t-transparent rounded-full animate-spin" />
          Continuing automatically…
        </div>
      ) : (
        <button
          onClick={() => advance({ migrationId })}
          disabled={isPending}
          className="inline-flex items-center gap-2 px-8 py-3 bg-indigo-600 text-white text-base font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
        >
          {isPending ? (
            <>
              <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              Processing…
            </>
          ) : (
            <>
              Continue
              <ArrowRight size={18} />
            </>
          )}
        </button>
      )}
    </div>
  );
}
