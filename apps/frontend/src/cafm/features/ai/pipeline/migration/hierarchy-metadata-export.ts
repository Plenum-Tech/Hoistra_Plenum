"use client";

/**
 * Hierarchy Verification metadata export.
 *
 * Builds downloadable JSON from EXACTLY the data the Hierarchy Verification UI
 * renders (tables_metadata, document_inventory, FK/ontology relationships,
 * hierarchy tree, and the user's confirm/modify/reject overrides). Because the
 * inputs come from the migration's own gate payload, the export inherently
 * reflects the currently-opened version with no cross-version leakage.
 */
import type { DocumentInventory, TableMeta } from "./migration-metadata-view";

export type HierarchyRelationshipExport = {
  source_table: string;
  source_column: string;
  target_table: string | null;
  target_column: string | null;
  type: string;
  relationship_type: string | null;
  /** Detection confidence (semantic score). */
  confidence: number | null;
  /** Row-level data overlap (numeric match score). */
  data_match_rate: string | null;
  reasoning: string | null;
};

export type HierarchyEntityExport = {
  entity: string;
  parent: string | null;
  children: string[];
};

export type HierarchyUserOverride = {
  source_table: string;
  source_column: string;
  original_target_table: string | null;
  original_target_column: string | null;
  action: "confirm" | "reject" | "modify";
  modified_target_table?: string | null;
  modified_target_column?: string | null;
};

export type HierarchyExportInputs = {
  migrationId: string;
  scriptCode?: string | null;
  versionLabel?: string | null;
  generatedAt: string;
  tables: TableMeta[];
  documentInventory?: DocumentInventory | null;
  relationships: HierarchyRelationshipExport[];
  entities: HierarchyEntityExport[];
  userOverrides: HierarchyUserOverride[];
  hierarchyTree: unknown;
  proposedStructure?: unknown;
  validation: {
    total_hierarchies: number;
    total_cycles: number;
    total_orphans: number;
    review_count: number;
  };
};

function isOntology(rel: HierarchyRelationshipExport): boolean {
  return (rel.relationship_type ?? "").toUpperCase() === "ONTOLOGY";
}

/** Table-level metadata only (PK/FK, row/column counts, source document). */
export function buildTableMetadataExport(tables: TableMeta[]) {
  return {
    tables: tables.map((t) => ({
      table_name: t.table_name,
      source_document: t.source_file,
      row_count: t.row_count,
      column_count: t.column_count,
      primary_keys: t.primary_keys,
      foreign_keys: t.foreign_keys,
    })),
  };
}

/** Column-level metadata, flattened across all tables. */
export function buildColumnMetadataExport(tables: TableMeta[]) {
  return {
    columns: tables.flatMap((t) =>
      t.columns.map((c) => ({
        table: t.table_name,
        column_name: c.column_name,
        datatype: c.datatype,
        nullable: c.nullable,
        unique: c.unique,
        primary_key: t.primary_keys.includes(c.column_name),
        foreign_key: t.foreign_keys.includes(c.column_name),
        sample_values: c.sample_values,
      })),
    ),
  };
}

/** The verified hierarchy structure (tree + parent/child + relationships). */
export function buildHierarchyExport(i: HierarchyExportInputs) {
  return {
    hierarchy: i.hierarchyTree ?? null,
    proposed_structure: i.proposedStructure ?? null,
    relationships: i.relationships,
    entities: i.entities,
  };
}

/** The full audit/replay package — everything the UI knows about this version. */
export function buildFullPackageExport(i: HierarchyExportInputs) {
  return {
    migration_id: i.migrationId,
    script_code: i.scriptCode ?? null,
    version: i.versionLabel ?? i.migrationId,
    generated_at: i.generatedAt,
    documents: i.documentInventory?.files ?? [],
    tables: buildTableMetadataExport(i.tables).tables,
    columns: buildColumnMetadataExport(i.tables).columns,
    foreign_keys: i.relationships.filter((r) => !isOntology(r)),
    hierarchy: i.hierarchyTree ?? null,
    proposed_structure: i.proposedStructure ?? null,
    entities: i.entities,
    ontology_matches: i.relationships.filter(isOntology),
    // Per-relationship semantic confidence + numeric (data overlap) scores.
    semantic_mappings: i.relationships.map((r) => ({
      source: `${r.source_table}.${r.source_column}`,
      target: r.target_table ? `${r.target_table}.${r.target_column ?? ""}` : null,
      relationship_type: r.relationship_type,
      semantic_score: r.confidence,
      numeric_match_score: r.data_match_rate,
    })),
    user_overrides: i.userOverrides,
    validation_results: i.validation,
  };
}

export type ExportKind = "table_metadata" | "column_metadata" | "hierarchy" | "full_package";

/** `<ScriptId>_<Version>_<kind>_<timestamp>.json`, falling back to the migration id. */
export function metadataFileName(opts: {
  scriptCode?: string | null;
  versionLabel?: string | null;
  migrationId: string;
  kind: ExportKind;
}): string {
  const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const script = (opts.scriptCode || "").trim();
  const ver = (opts.versionLabel || "").trim();
  const base = script
    ? `${script}${ver ? `_${ver}` : ""}`
    : `migration_${opts.migrationId.slice(0, 8)}`;
  return `${base}_${opts.kind}_${ts}.json`;
}

/** Trigger a client-side download of `data` as pretty-printed JSON. */
export function downloadJson(filename: string, data: unknown): void {
  if (typeof window === "undefined") return;
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
