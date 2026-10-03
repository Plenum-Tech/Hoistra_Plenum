"use client";
import { useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/utils/cn";
import {
  DocumentSummary,
  TablesMetadataPanel,
  type DocumentInventory,
  type TableMeta,
} from "../migration-metadata-view";
import { CheckCircle, XCircle, GitBranch, ArrowRight, Edit3, AlertTriangle, Users, Download } from "lucide-react";
import {
  buildTableMetadataExport,
  buildColumnMetadataExport,
  buildHierarchyExport,
  buildFullPackageExport,
  metadataFileName,
  downloadJson,
  type ExportKind,
  type HierarchyExportInputs,
} from "../hierarchy-metadata-export";
import {
  useMigrationGateHierarchy,
  useMigrationAdvance,
  schemaMapperApi,
  type MigrationStatus,
  type MigrationHierarchyGatePayload,
  type MigrationHierarchyRelationship,
} from "../../../chat-api";
interface Props {
  migrationId: string;
  payload: MigrationHierarchyGatePayload;
  onSubmitted: () => void;
  pipelineStatus?: MigrationStatus | null;
}

export default function GateHierarchy({ migrationId, payload, onSubmitted, pipelineStatus: _pipelineStatus }: Props) {
  const STEP_PAUSED_STATUS_RE = /status\s*:?\s*step_paused/i;
  const FAILED_STATUS_RE = /status\s*:?\s*(failed|ddl_failed)/i;
  const REVIEW_READY_STATUS_RE = /status\s*:?\s*(awaiting_review|running)/i;
  const GATE_MISMATCH_RE = /gate mismatch/i;
  type ReviewItem = {
    id: string;
    type: string;
    source_table: string;
    source_column: string;
    target_table: string | null;
    target_column: string | null;
    relationship_type?: string;
    confidence?: number;
    data_match_rate?: string;
    description?: string;
    reasoning?: string;
    read_only?: boolean;
    system_default?: boolean;
    mapping_note?: boolean;
    suggested_action?: "confirm" | "reject" | "modify";
  };

  const items: ReviewItem[] = useMemo(() => {
    const raw = payload as unknown as {
      review_items?: unknown[];
      hierarchies_to_review?: MigrationHierarchyRelationship[];
    };

    if (Array.isArray(raw.review_items)) {
      return raw.review_items
        .map((v, idx) => {
          const obj = v as Record<string, unknown>;
          const sourceTable = typeof obj.source_table === "string" ? obj.source_table : "";
          const sourceColumn = typeof obj.source_column === "string" ? obj.source_column : "";
          const targetTable = typeof obj.target_table === "string" ? obj.target_table : null;
          const targetColumn = typeof obj.target_column === "string" ? obj.target_column : null;
          const id =
            typeof obj.id === "string" && obj.id.trim().length > 0
              ? obj.id
              : `${sourceTable}.${sourceColumn}→${targetTable ?? ""}.${targetColumn ?? ""}#${idx}`;
          const type = typeof obj.type === "string" ? obj.type : "fk";
          const confidence = typeof obj.confidence === "number" ? obj.confidence : undefined;
          const relationshipType = typeof obj.relationship_type === "string" ? obj.relationship_type : undefined;
          const dataMatchRate = typeof obj.data_match_rate === "string" ? obj.data_match_rate : undefined;
          const reasoning = typeof obj.reasoning === "string" ? obj.reasoning : undefined;
          const description = typeof obj.description === "string" ? obj.description : undefined;
          const suggestedAction = typeof obj.suggested_action === "string" ? obj.suggested_action : undefined;
          const readOnly = obj.read_only === true || type === "system_default";
          const systemDefault = obj.system_default === true;
          const mappingNote = obj.mapping_note === true;

          return {
            id,
            type,
            source_table: sourceTable,
            source_column: sourceColumn,
            target_table: targetTable,
            target_column: targetColumn,
            relationship_type: relationshipType,
            confidence,
            data_match_rate: dataMatchRate,
            reasoning,
            description,
            read_only: readOnly,
            system_default: systemDefault,
            mapping_note: mappingNote,
            suggested_action:
              suggestedAction === "reject" || suggestedAction === "modify" || suggestedAction === "confirm"
                ? suggestedAction
                : readOnly
                  ? "confirm"
                  : undefined,
          } satisfies ReviewItem;
        })
        .filter((it) => it.source_table.length > 0 && it.source_column.length > 0);
    }

    const rels = Array.isArray(raw.hierarchies_to_review) ? raw.hierarchies_to_review : [];
    return rels.map((r, idx) => {
      const readOnly = Boolean(r.read_only || r.system_default || r.mapping_note);
      return {
        id: `${r.source_table}.${r.source_column}→${r.target_table}.${r.target_column}#${idx}`,
        type: readOnly ? "system_default" : "hierarchy",
        source_table: r.source_table,
        source_column: r.source_column,
        target_table: r.target_table,
        target_column: r.target_column,
        relationship_type: r.relationship_type,
        confidence: r.confidence,
        data_match_rate: r.data_match_rate,
        reasoning: r.reasoning,
        description: undefined,
        read_only: readOnly,
        system_default: r.system_default,
        mapping_note: r.mapping_note,
        suggested_action: readOnly ? "confirm" : (r.confidence ?? 1) >= 0.7 ? "confirm" : "reject",
      };
    });
  }, [payload]);

  const singleTableImport = Boolean(
    (payload as MigrationHierarchyGatePayload).single_table_import ??
      (payload as MigrationHierarchyGatePayload).system_default_hierarchy,
  );
  const importTableRole = (payload as MigrationHierarchyGatePayload).import_table_plenum_role;
  const importTableName = (payload as MigrationHierarchyGatePayload).import_table_name;

  const referenceModelItems = useMemo(
    () => items.filter((it) => it.read_only || it.type === "system_default" || it.mapping_note),
    [items],
  );
  const reviewableItems = useMemo(
    () => items.filter((it) => !it.read_only && it.type !== "system_default" && !it.mapping_note),
    [items],
  );

  type HierarchyAction = "confirm" | "reject" | "modify";
  type RowDecision = { action: HierarchyAction; modifyTargetTable: string; modifyTargetColumn: string };

  const [decisions, setDecisions] = useState<RowDecision[]>(
    reviewableItems.map((it) => ({
      action: it.suggested_action === "reject" ? "reject" : "confirm",
      modifyTargetTable: it.target_table ?? "",
      modifyTargetColumn: it.target_column ?? "",
    }))
  );
  const [error, setError] = useState<string | null>(null);
  const [treeExpanded, setTreeExpanded] = useState(true);
  const [isPreflighting, setIsPreflighting] = useState(false);
  type HierarchyBody = {
    confirmed_hierarchies: Array<Record<string, unknown>>;
    hierarchy_corrections: Record<string, unknown>;
  };
  const lastSubmitRef = useRef<HierarchyBody | null>(null);

  useEffect(() => {
    setDecisions(
      reviewableItems.map((it) => ({
        action: it.suggested_action === "reject" ? "reject" : "confirm",
        modifyTargetTable: it.target_table ?? "",
        modifyTargetColumn: it.target_column ?? "",
      }))
    );
    setError(null);
    setTreeExpanded(true);
  }, [migrationId, reviewableItems]);

  const { mutate: submitGate, isPending } = useMigrationGateHierarchy({
    onSuccess: () => onSubmitted(),
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

  function setAction(idx: number, action: HierarchyAction) {
    setDecisions((prev) =>
      prev.map((d, i) =>
        i === idx
          ? {
              ...d,
              action,
              modifyTargetTable: d.modifyTargetTable.length > 0 ? d.modifyTargetTable : reviewableItems[idx]?.target_table ?? "",
              modifyTargetColumn: d.modifyTargetColumn.length > 0 ? d.modifyTargetColumn : reviewableItems[idx]?.target_column ?? "",
            }
          : d
      )
    );
  }

  function setModifyTargetTable(idx: number, val: string) {
    setDecisions((prev) => prev.map((d, i) => (i === idx ? { ...d, modifyTargetTable: val } : d)));
  }

  function setModifyTargetColumn(idx: number, val: string) {
    setDecisions((prev) => prev.map((d, i) => (i === idx ? { ...d, modifyTargetColumn: val } : d)));
  }

  function handleSubmit() {
    const confirmed_hierarchies: Array<Record<string, unknown>> = [];
    const hierarchy_corrections: Record<string, unknown> = {};

    reviewableItems.forEach((it, idx) => {
      const d = decisions[idx];
      const action: HierarchyAction = d?.action ?? "confirm";
      
      const baseHierarchy = {
        source_table: it.source_table,
        source_column: it.source_column,
        target_table: it.target_table,
        target_column: it.target_column,
        relationship_type: it.relationship_type,
        confidence: it.confidence,
        data_match_rate: it.data_match_rate,
        reasoning: it.reasoning,
      };

      if (action === "reject") {
        // Rejected hierarchies are not sent to the backend
        return;
      }

      if (action === "modify") {
        const newTargetTable = d.modifyTargetTable.trim();
        const newTargetColumn = d.modifyTargetColumn.trim();
        const corrected = {
          ...baseHierarchy,
          target_table: newTargetTable.length > 0 ? newTargetTable : it.target_table,
          target_column: newTargetColumn.length > 0 ? newTargetColumn : it.target_column,
          customer_confirmed: true,
        };
        confirmed_hierarchies.push(corrected);
        hierarchy_corrections[`${it.source_table}.${it.source_column}`] = corrected;
        return;
      }

      // action === "confirm"
      confirmed_hierarchies.push({
        ...baseHierarchy,
        customer_confirmed: true,
      });
    });

    // Match the backend API spec: use "confirmed_hierarchies" and "hierarchy_corrections"
    const body: HierarchyBody & { plenum_default_hierarchy_accepted?: boolean } = {
      confirmed_hierarchies,
      hierarchy_corrections,
      ...(singleTableImport ? { plenum_default_hierarchy_accepted: true } : {}),
    };
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
          setError(`Cannot submit hierarchy yet. Current migration status is: ${latestStatus}`);
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

  const confirmedCount = decisions.filter((d) => d.action === "confirm").length;
  const modifiedCount = decisions.filter((d) => d.action === "modify").length;
  const rejectedCount = decisions.filter((d) => d.action === "reject").length;

  const meta = payload as unknown as {
    total_hierarchies?: number;
    total_cycles?: number;
    total_orphans?: number;
    hierarchy_tree?: unknown;
    tables_metadata?: TableMeta[];
    document_inventory?: DocumentInventory;
  };
  const totalHierarchies = typeof meta.total_hierarchies === "number" ? meta.total_hierarchies : items.length;
  const reviewCount = reviewableItems.length;
  const totalCycles = typeof meta.total_cycles === "number" ? meta.total_cycles : 0;
  const totalOrphans = typeof meta.total_orphans === "number" ? meta.total_orphans : 0;
  const hierarchyTree = meta.hierarchy_tree;
  // Requirement #8 — Tree / Relationship / Raw-JSON views over the detected hierarchy.
  const [hierView, setHierView] = useState<"tree" | "rel" | "json">("tree");
  const relView = useMemo(() => {
    const parents: Record<string, string> = {};
    const children: Record<string, Set<string>> = {};
    const tables = new Set<string>();
    for (const it of items) {
      if (!it.source_table || !it.target_table) continue;
      tables.add(it.source_table);
      tables.add(it.target_table);
      // FK: source_table references target_table → target is the parent.
      parents[it.source_table] = it.target_table;
      (children[it.target_table] ??= new Set<string>()).add(it.source_table);
    }
    return [...tables].sort().map((t) => ({
      entity: t,
      parent: parents[t] ?? null,
      children: [...(children[t] ?? new Set<string>())].sort(),
    }));
  }, [items]);
  const hierJson = useMemo(
    () =>
      JSON.stringify(
        {
          proposed_structure: payload.proposed_structure ?? null,
          hierarchy_tree: hierarchyTree ?? null,
          relationships: items.map((it) => ({
            source_table: it.source_table,
            source_column: it.source_column,
            target_table: it.target_table,
            target_column: it.target_column,
            relationship_type: it.relationship_type ?? null,
          })),
          entities: relView,
        },
        null,
        2,
      ),
    [payload.proposed_structure, hierarchyTree, items, relView],
  );
  const handleRefreshGate = () => {
    setError(null);
    onSubmitted();
  };

  // Assemble export inputs from EXACTLY the data this screen renders. Built at
  // click-time so generated_at is the download moment; the migration id is the
  // version identifier, so each opened version exports its own data.
  const makeExportInputs = (): HierarchyExportInputs => ({
    migrationId,
    scriptCode: null,
    versionLabel: migrationId.slice(0, 8),
    generatedAt: new Date().toISOString(),
    tables: meta.tables_metadata ?? [],
    documentInventory: meta.document_inventory ?? null,
    relationships: items.map((it) => ({
      source_table: it.source_table,
      source_column: it.source_column,
      target_table: it.target_table,
      target_column: it.target_column,
      type: it.type,
      relationship_type: it.relationship_type ?? null,
      confidence: it.confidence ?? null,
      data_match_rate: it.data_match_rate ?? null,
      reasoning: it.reasoning ?? null,
    })),
    entities: relView,
    userOverrides: reviewableItems.map((it, i) => ({
      source_table: it.source_table,
      source_column: it.source_column,
      original_target_table: it.target_table,
      original_target_column: it.target_column,
      action: decisions[i]?.action ?? "confirm",
      modified_target_table: decisions[i]?.modifyTargetTable ?? null,
      modified_target_column: decisions[i]?.modifyTargetColumn ?? null,
    })),
    hierarchyTree,
    proposedStructure: payload.proposed_structure ?? null,
    validation: {
      total_hierarchies: totalHierarchies,
      total_cycles: totalCycles,
      total_orphans: totalOrphans,
      review_count: reviewCount,
    },
  });

  const handleDownloadMetadata = (kind: ExportKind) => {
    const inputs = makeExportInputs();
    const data =
      kind === "table_metadata"
        ? buildTableMetadataExport(inputs.tables)
        : kind === "column_metadata"
          ? buildColumnMetadataExport(inputs.tables)
          : kind === "hierarchy"
            ? buildHierarchyExport(inputs)
            : buildFullPackageExport(inputs);
    downloadJson(
      metadataFileName({ migrationId, versionLabel: inputs.versionLabel, kind }),
      data,
    );
  };

  return (
    <div className="max-w-6xl">
      {/* "Hierarchy Detection Results" lived here previously, but the same
         step-7 snapshot is already rendered in the Completed Steps history
         block above this gate — keeping it here produced an identical
         duplicate card. The gate now focuses on the confirm/reject UI. */}

      {/* Header */}
      <div className="flex items-start gap-4 mb-6">
        <div className="w-10 h-10 rounded-xl bg-purple-100 flex items-center justify-center shrink-0">
          <GitBranch size={20} className="text-purple-600" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-slate-900">Hierarchy Verification</h2>
          <p className="text-sm text-slate-500 mt-0.5">
            {singleTableImport
              ? "Your file has one table — review the Plenum default hierarchy and any column references on your data."
              : "Confirm or reject detected FK relationships and hierarchy structure."}
          </p>
        </div>
      </div>

      {singleTableImport && (
        <div className="mb-6 rounded-xl border border-purple-200 bg-purple-50 px-5 py-4">
          <p className="text-sm font-semibold text-purple-900">Single-table import</p>
          <p className="text-sm text-purple-800 mt-1">
            Cross-table FK detection is not available for a flat file. The system shows the default Plenum CAFM
            hierarchy
            {importTableName ? (
              <>
                {" "}
                and maps your table <span className="font-mono font-semibold">{importTableName}</span>
              </>
            ) : null}
            {importTableRole ? (
              <>
                {" "}
                to the <span className="font-mono font-semibold">{importTableRole}</span> tier.
              </>
            ) : (
              "."
            )}
          </p>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <StatCard label="FK relationships" value={totalHierarchies} color="text-indigo-600" bg="bg-indigo-50" />
        <StatCard
          label="Cycles detected"
          value={totalCycles}
          color={totalCycles > 0 ? "text-red-600" : "text-green-600"}
          bg={totalCycles > 0 ? "bg-red-50" : "bg-green-50"}
        />
        <StatCard
          label="Orphaned records"
          value={totalOrphans}
          color={totalOrphans > 0 ? "text-amber-600" : "text-green-600"}
          bg={totalOrphans > 0 ? "bg-amber-50" : "bg-green-50"}
        />
        <StatCard label="To review" value={reviewCount} color="text-slate-700" bg="bg-slate-100" />
      </div>

      <div className="grid grid-cols-3 gap-3 mb-6">
        <div className="rounded-lg bg-green-50 border border-green-200 px-4 py-3 text-center">
          <div className="text-lg font-bold font-mono text-green-700">{confirmedCount}</div>
          <div className="text-xs text-green-600">Confirmed</div>
        </div>
        <div className="rounded-lg bg-blue-50 border border-blue-200 px-4 py-3 text-center">
          <div className="text-lg font-bold font-mono text-blue-700">{modifiedCount}</div>
          <div className="text-xs text-blue-600">Modified</div>
        </div>
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-center">
          <div className="text-lg font-bold font-mono text-red-700">{rejectedCount}</div>
          <div className="text-xs text-red-600">Rejected</div>
        </div>
      </div>

      {/* #10 document summary + #9 full table/column metadata (untruncated) */}
      <DocumentSummary inventory={meta.document_inventory} />
      <TablesMetadataPanel tables={meta.tables_metadata} />

      {/* Download metadata JSON — exports exactly the data shown above for the
         currently-opened migration version (table/column metadata, PK/FK,
         hierarchy tree + relationships, ontology matches, semantic & numeric
         scores, user overrides, validation results). */}
      <div className="mb-6 rounded-lg border border-slate-200 bg-slate-50/60 px-4 py-3">
        <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          <Download size={12} /> Download metadata JSON
        </div>
        <div className="flex flex-wrap gap-2">
          {(
            [
              ["table_metadata", "Table metadata"],
              ["column_metadata", "Column metadata"],
              ["hierarchy", "Hierarchy JSON"],
              ["full_package", "Full metadata package"],
            ] as [ExportKind, string][]
          ).map(([kind, label]) => (
            <button
              key={kind}
              type="button"
              onClick={() => handleDownloadMetadata(kind)}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
                kind === "full_package"
                  ? "border-indigo-300 bg-indigo-600 text-white hover:bg-indigo-700"
                  : "border-slate-300 bg-white text-slate-700 hover:bg-slate-100",
              )}
              title={`Download ${label} for this version as JSON`}
            >
              <Download size={12} />
              {label}
            </button>
          ))}
        </div>
        <p className="mt-1.5 text-[10px] text-slate-400">
          Reflects the currently-opened version ({migrationId.slice(0, 8)}). The full package
          bundles documents, tables, columns, PK/FK, hierarchy, ontology matches, semantic &amp;
          numeric scores, user overrides, and validation results.
        </p>
      </div>

      {(payload.proposed_structure || singleTableImport) && (
        <div className="mb-6 rounded-lg bg-indigo-50 border border-indigo-200 px-4 py-3">
          <p className="text-xs font-semibold text-indigo-700 mb-1">
            {singleTableImport ? "System default Plenum hierarchy" : "Proposed structure"}
          </p>
          <p className="text-sm text-indigo-800 font-mono">
            {payload.proposed_structure ?? "sites → locations → assets → work_orders → tasks"}
          </p>
        </div>
      )}

      {referenceModelItems.length > 0 && (
        <div className="rounded-xl border border-purple-200 bg-purple-50/60 shadow-sm overflow-hidden mb-6">
          <div className="px-5 py-3.5 border-b border-purple-200">
            <span className="text-sm font-semibold text-purple-900">Plenum reference model (informational)</span>
            <p className="text-xs text-purple-700 mt-0.5">
              These relationships describe how data is organized in Plenum — they are not in your file.
            </p>
          </div>
          <div className="divide-y divide-purple-100">
            {referenceModelItems.map((it) => (
              <div key={it.id} className="px-5 py-3 text-sm">
                <div className="flex items-center gap-2 flex-wrap font-mono text-purple-900">
                  <TypeBadge type="system_default" />
                  <span>
                    {it.source_table}.{it.source_column}
                  </span>
                  <ArrowRight size={12} className="text-purple-400 shrink-0" />
                  <span>
                    {it.target_table}.{it.target_column}
                  </span>
                  {it.relationship_type && (
                    <span className="text-xs uppercase text-purple-600">{it.relationship_type}</span>
                  )}
                </div>
                {it.reasoning && <p className="text-xs text-purple-700 mt-1">{it.reasoning}</p>}
              </div>
            ))}
          </div>
        </div>
      )}

      {(hierarchyTree != null || items.length > 0) && (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden mb-6">
          <div className="w-full flex flex-wrap items-center justify-between gap-2 px-5 py-3 border-b border-slate-100">
            <div className="flex items-center gap-2">
              <Users size={16} className="text-slate-500" />
              <span className="text-sm font-semibold text-slate-700">
                {singleTableImport ? "Default hierarchy" : "Detected hierarchy"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <div className="inline-flex rounded-lg bg-slate-100 p-0.5 text-[11px]">
                {(
                  [
                    ["tree", "Tree"],
                    ["rel", "Relationships"],
                    ["json", "Raw JSON"],
                  ] as const
                ).map(([k, label]) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => setHierView(k)}
                    className={cn(
                      "rounded-md px-2 py-0.5 font-medium transition-colors",
                      hierView === k ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800",
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <button type="button" onClick={() => setTreeExpanded((v) => !v)} className="text-xs text-indigo-600">
                {treeExpanded ? "Hide" : "Show"}
              </button>
            </div>
          </div>
          {treeExpanded && (
            <div className="border-t border-slate-100">
              {hierView === "tree" ? (
                <pre className="text-xs text-slate-600 bg-slate-50 p-5 overflow-auto max-h-72 leading-relaxed font-mono">
                  {hierarchyTree == null
                    ? "(no tree available — switch to Relationships)"
                    : typeof hierarchyTree === "string"
                      ? hierarchyTree
                      : JSON.stringify(hierarchyTree, null, 2)}
                </pre>
              ) : hierView === "json" ? (
                <pre className="text-xs text-slate-600 bg-slate-50 p-5 overflow-auto max-h-72 leading-relaxed font-mono">
                  {hierJson}
                </pre>
              ) : (
                <div className="max-h-72 space-y-2 overflow-auto p-4">
                  {relView.length === 0 ? (
                    <p className="text-xs text-slate-400">No relationships detected.</p>
                  ) : (
                    relView.map((r) => (
                      <div key={r.entity} className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2">
                        <div className="flex items-center gap-2 text-xs">
                          <span className="font-mono font-semibold text-slate-800">{r.entity}</span>
                          {r.parent ? (
                            <span className="text-slate-500">
                              parent: <span className="font-mono text-indigo-700">{r.parent}</span>
                            </span>
                          ) : (
                            <span className="text-[10px] uppercase tracking-wide text-slate-400">root</span>
                          )}
                        </div>
                        {r.children.length > 0 ? (
                          <div className="mt-1 text-[11px] text-slate-500">
                            children:{" "}
                            {r.children.map((c) => (
                              <span key={c} className="mr-1.5 font-mono text-emerald-700">
                                {c}
                              </span>
                            ))}
                          </div>
                        ) : null}
                        <pre className="mt-1 font-mono text-[10px] text-slate-400">
                          {JSON.stringify({ entity: r.entity, parent: r.parent, children: r.children })}
                        </pre>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Relationship review list */}
      {reviewableItems.length > 0 ? (
        <div className="space-y-3 mb-6">
          {reviewableItems.map((it, idx) => {
            const decision = decisions[idx]?.action ?? "confirm";
            const isCycle = it.type === "cycle";
            const isOrphan = it.type === "orphan";
            const confPct = it.confidence != null ? Math.round(it.confidence * 100) : null;

          return (
              <div
                key={it.id}
                className={`rounded-xl border bg-white shadow-sm p-5 ${
                  isCycle ? "border-red-200" : isOrphan ? "border-amber-200" : "border-slate-200"
                }`}
              >
                <div className="flex items-start gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-2 flex-wrap">
                      <TypeBadge type={it.type} />
                      <span className="font-mono text-sm font-semibold text-slate-800">
                        {it.source_table}.{it.source_column}
                      </span>
                      <ArrowRight size={12} className="text-slate-400 shrink-0" />
                      <span className="font-mono text-sm text-slate-700">{it.target_table ?? ""}</span>
                      {it.target_column && (
                        <code className="text-xs font-mono text-indigo-600 bg-indigo-50 px-1.5 py-0.5 rounded">
                          .{it.target_column}
                        </code>
                      )}
                      {it.relationship_type && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-600 uppercase font-mono">
                          {it.relationship_type}
                        </span>
                      )}
                      {confPct != null && (
                        <span className={`text-xs font-mono ${confPct >= 80 ? "text-green-600" : "text-amber-600"}`}>
                          {confPct}%
                        </span>
                      )}
                      {it.data_match_rate && <span className="text-xs text-slate-400">{it.data_match_rate}</span>}
                    </div>

                    {it.description && <p className="text-xs text-slate-500 mb-2">{it.description}</p>}
                    {it.reasoning && <p className="text-xs text-slate-400">{it.reasoning}</p>}

                    {isCycle && (
                      <div className="flex items-center gap-1.5 text-xs text-red-700 bg-red-50 px-3 py-1.5 rounded-lg mt-2 w-fit">
                        <AlertTriangle size={12} />
                        Circular reference detected — consider rejecting
                      </div>
                    )}

                    {decision === "modify" && (
                      <div className="mt-3 grid grid-cols-2 gap-3 max-w-xl">
                        <div>
                          <label className="text-xs font-medium text-slate-600 block mb-1">Override target table</label>
                          <input
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-mono text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-200"
                            value={decisions[idx]?.modifyTargetTable ?? ""}
                            onChange={(e) => setModifyTargetTable(idx, e.target.value)}
                            placeholder="new_target_table"
                          />
                        </div>
                        <div>
                          <label className="text-xs font-medium text-slate-600 block mb-1">Override target column</label>
                          <input
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-mono text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-200"
                            value={decisions[idx]?.modifyTargetColumn ?? ""}
                            onChange={(e) => setModifyTargetColumn(idx, e.target.value)}
                            placeholder="new_target_column"
                          />
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="flex gap-1.5 shrink-0">
                    {(["confirm", "modify", "reject"] as const).map((a) => (
                      <button
                        key={a}
                        onClick={() => setAction(idx, a)}
                        className={`inline-flex items-center gap-1 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors ${
                          decision === a
                            ? a === "confirm"
                              ? "bg-green-600 text-white"
                              : a === "modify"
                                ? "bg-blue-600 text-white"
                                : "bg-red-600 text-white"
                            : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                        }`}
                      >
                        {a === "confirm" && <CheckCircle size={11} />}
                        {a === "modify" && <Edit3 size={11} />}
                        {a === "reject" && <XCircle size={11} />}
                        {a.charAt(0).toUpperCase() + a.slice(1)}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
          );
          })}
        </div>
      ) : (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6 text-center text-slate-500 text-sm mb-6">
          {singleTableImport
            ? "No column-level references need review. Accept the default Plenum hierarchy to continue."
            : "No relationships require manual review. All hierarchies were auto-detected with high confidence."}
        </div>
      )}

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

      <button
        onClick={handleSubmit}
        disabled={isPending || isAdvancing || isPreflighting}
        className="inline-flex items-center gap-2 px-8 py-3 bg-indigo-600 text-white text-base font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
      >
        {isPending || isAdvancing || isPreflighting ? (
          <>
            <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
            {isPreflighting ? "Checking status…" : "Submitting…"}
          </>
        ) : (
          <>
            <CheckCircle size={18} />
            Confirm hierarchy ({confirmedCount} confirmed, {modifiedCount} modified, {rejectedCount} rejected)
          </>
        )}
      </button>
    </div>
  );
}

function StatCard({
  label,
  value,
  color,
  bg,
}: {
  label: string;
  value: number;
  color: string;
  bg: string;
}) {
  return (
    <div className={`rounded-xl border border-slate-200 bg-white shadow-sm p-4 text-center ${bg}`}>
      <div className={`text-2xl font-bold font-mono ${color}`}>{value}</div>
      <div className="text-xs text-slate-500 mt-0.5">{label}</div>
    </div>
  );
}

function TypeBadge({ type }: { type: string }) {
  const map: Record<string, string> = {
    fk: "bg-indigo-100 text-indigo-700",
    hierarchy: "bg-indigo-100 text-indigo-700",
    cycle: "bg-red-100 text-red-700",
    orphan: "bg-amber-100 text-amber-700",
    implicit: "bg-purple-100 text-purple-700",
    system_default: "bg-purple-100 text-purple-800",
  };
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium uppercase font-mono ${
        map[type] ?? "bg-slate-100 text-slate-600"
      }`}
    >
      {type}
    </span>
  );
}
