"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
  type UseQueryOptions,
} from "@tanstack/react-query";

import { env } from "@/config";
// Hoistra: CAFM Web's requests go through Hoistra's authenticated client.
import { apiFetch as hoistraApiFetch } from "../../../api/client.js";
import { isUuid } from "./lib/coerce";
import { shouldKeepPollingForFieldMappingGate } from "./pipeline/migration/migration-gate-state";
import {
  schemaMappingPollIntervalMs,
  schemaMappingStatusNeedsPoll,
} from "./pipeline/schema/schema-gate-state";

// test

export class AiApiError extends Error {
  status: number;
  payload: unknown;

  constructor(input: { status: number; message: string; payload: unknown }) {
    super(input.message);
    this.status = input.status;
    this.payload = input.payload;
  }
}

export type AiRequestOptions<TBody> = {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  path?: string;
  query?: Record<string, string | number | boolean | null | undefined>;
  body?: TBody;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  basePath?: string;
};

export function getAiErrorMessage(err: unknown) {
  if (err instanceof AiApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Something went wrong";
}

function isAbsoluteUrl(v: string) {
  return v.startsWith("http://") || v.startsWith("https://");
}

function resolveBase(basePath: string) {
  const clean = basePath.replace(/\/+$/, "");
  if (isAbsoluteUrl(clean)) return clean;
  if (clean.startsWith("/api/ai/")) return clean;
  // Same-origin paths served by nginx in single-app / Azure deployments.
  if (clean.startsWith("/backend/")) return clean;

  const schemaMapperBase = env.schemaMapperBaseUrl.trim();
  if (!schemaMapperBase) {
    throw new Error("Missing NEXT_PUBLIC_SCHEMA_MAPPER_BASE_URL. Set it in .env.local and restart the dev server.");
  }
  const api = clean.startsWith("/") ? clean : `/${clean}`;
  return `${schemaMapperBase.replace(/\/+$/, "")}${api}`;
}
/** Download URL for a migration's FULL target tables (existing rows + newly-migrated). */
export function migrationFullExportUrl(migrationId: string, format: "csv" | "json" | "sql"): string {
  return buildAiUrl(`/${encodeURIComponent(migrationId)}/full-export`, { format }, "/api/migration");
}
/** Download URL for a migration's ORIGINAL uploaded source file (Excel/CSV/PDF/Word). */
export function migrationSourceUrl(migrationId: string): string {
  return buildAiUrl(`/${encodeURIComponent(migrationId)}/source`, undefined, "/api/migration");
}

export function buildAiUrl(
  path?: string,
  query?: AiRequestOptions<unknown>["query"],
  basePath = "/api/ai/schema-mapper",
) {
  const base = resolveBase(basePath);
  const p = path?.trim() ? (path.startsWith("/") ? path : `/${path}`) : "";
  if (isAbsoluteUrl(base)) {
    const url = new URL(`${base}${p}`);
    if (query) {
      for (const [k, v] of Object.entries(query)) {
        if (v === undefined || v === null) continue;
        url.searchParams.set(k, String(v));
      }
    }
    return url.toString();
  }

  const qs = new URLSearchParams();
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null) continue;
      qs.set(k, String(v));
    }
  }
  const suffix = qs.toString();
  return `${base}${p}${suffix ? `?${suffix}` : ""}`;
}

const SCHEMA_ARTIFACT_FILES = {
  json: "mapper_config.json",
  csv: "field_mappings.csv",
  sql: "schema_ddl_preview.sql",
} as const;

/** Turn API-relative or legacy blob URLs into a browser-downloadable URL. */
export function resolveSchemaArtifactDownloadUrl(
  sessionId: string,
  url?: string | null,
  artifact?: keyof typeof SCHEMA_ARTIFACT_FILES,
): string | undefined {
  let u = (url ?? "").trim();
  if (!u && artifact) {
    u = `/api/schema-mapping/${sessionId}/artifacts/${SCHEMA_ARTIFACT_FILES[artifact]}`;
  }
  if (!u || u.startsWith("blob://")) return undefined;

  if (u.startsWith("http://") || u.startsWith("https://")) {
    const blobMatch = u.match(
      /schema-mapping\/([^/]+)\/(mapper_config\.json|field_mappings\.csv|schema_ddl_preview\.sql)/,
    );
    if (blobMatch) {
      return buildAiUrl(
        `/artifacts/${blobMatch[2]}`,
        undefined,
        `/api/schema-mapping/${blobMatch[1]}`,
      );
    }
    return u;
  }

  if (u.startsWith("/api/schema-mapping/")) {
    const base = env.schemaMapperBaseUrl.replace(/\/+$/, "");
    return `${base}${u}`;
  }

  const path = u.startsWith("/") ? u : `/artifacts/${u}`;
  return buildAiUrl(path, undefined, `/api/schema-mapping/${sessionId}`);
}

// ── Hoistra ─────────────────────────────────────────────────────────────────────────
// The one change to CAFM Web's request core: it goes through Hoistra's apiFetch, so every
// call carries the signed-in user's bearer token and gets its refresh-on-expiry, exactly as
// the rest of Hoistra's reads do. The URL, method, body and the AiApiError it throws (status,
// message built from `detail`, payload) are CAFM Web's, so the screens behave identically.
function aiDetailMessage(payload: unknown): string | null {
  const detail =
    typeof payload === "object" && payload !== null && "detail" in payload
      ? (payload as Record<string, unknown>).detail
      : undefined;
  if (typeof detail === "string") {
    const t = detail.trim();
    return t ? t : null;
  }
  if (Array.isArray(detail)) {
    const parts: string[] = [];
    for (const it of detail) {
      if (typeof it === "string") {
        const t = it.trim();
        if (t) parts.push(t);
        continue;
      }
      if (it && typeof it === "object") {
        const rec = it as Record<string, unknown>;
        const msg = typeof rec.msg === "string" ? rec.msg.trim() : "";
        if (msg) parts.push(msg);
      }
    }
    return parts.length ? parts.join("\n") : null;
  }
  return null;
}

export async function aiRequest<TResponse = unknown, TBody = unknown>(
  opts: AiRequestOptions<TBody>,
): Promise<TResponse> {
  const method = opts.method ?? "POST";
  const url = buildAiUrl(opts.path, opts.query, opts.basePath ?? "/api/ai/schema-mapper");
  const b = opts.body as unknown;
  const isForm = typeof FormData !== "undefined" && b instanceof FormData;
  const sendsBody = method !== "GET" && method !== "DELETE" && opts.body !== undefined;
  try {
    const payload = await hoistraApiFetch("", url, {
      method,
      body: sendsBody && !isForm ? opts.body : undefined,
      form: sendsBody && isForm ? (b as FormData) : undefined,
      signal: opts.signal,
      // A status document can run to hundreds of KB and a gate answer can take a while.
      timeoutMs: 120000,
    });
    return payload as TResponse;
  } catch (e) {
    const err = e as { status?: number; body?: unknown; message?: string; cancelled?: boolean };
    if (err && typeof err.status === "number") {
      const msg = aiDetailMessage(err.body) ?? `AI request failed (${err.status})`;
      throw new AiApiError({ status: err.status, message: msg, payload: err.body ?? null });
    }
    throw e;
  }
}

export const SCHEMA_MAPPER_API_BASE_PATH = "/api";

// ── Node info (shared by both pipelines) ─────────────────────────────────────

export type NodeStatus = "pending" | "running" | "completed";

export type NodeInfo = {
  node_id: number;
  node_name: string;
  status: NodeStatus;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  output: Record<string, unknown> | null;
  logs: string[];
};

export type StepState = "waiting" | "running" | "paused" | "complete" | "error";

// ── Pipeline status unions ────────────────────────────────────────────────────

export type MigrationStatus =
  | "running"
  | "step_paused"
  | "awaiting_review"
  | "complete"
  | "failed"
  | "ddl_failed"
  | "cancelled";

export type SchemaMappingStatus =
  | "running"
  | "step_paused"
  | "awaiting_review"
  | "complete"
  | "error"
  | "ddl_failed"
  | "cancelled";

// ── Migration gate payload types ──────────────────────────────────────────────

export type MigrationMappingCandidate = {
  target_field: string;
  confidence: number;
  is_primary?: boolean;
};

export type MigrationPreSemanticReviewItem = {
  source_table: string;
  source_field: string;
  target_field: string;
  confidence: number;
  tier: string;
  rationale?: string;
  sample_values?: string[];
  /** Up to 3 alternative target fits, best-first (Feature 5 "multiple fits"). */
  candidates?: MigrationMappingCandidate[];
  /** Inferred SQL type of the source column, for the preview. */
  data_type?: string;
  /** Other source headers whose VALUES were identical to this one and were merged into it
   *  (e.g. ASSETNUM merged with asset_ref). */
  merged_source_fields?: string[];
  /** 4a column metadata — is this column the table's primary key (maps to id / identity-like)? */
  is_primary_key?: boolean;
  /** Step 2 ↔ B21.1 alignment: the destination-column-mapping panel demoted this tier-1 match to
   *  a NEW column (e.g. vendors.id holds asset codes → created as 'asset_id', not merged into the
   *  vendor PK). When set, target_field is the new column's canonical name. */
  b21_new_column?: boolean;
};

export type MigrationPreSemanticGatePayload = {
  gate: string;
  migration_id?: string;
  /** Split gate: "tables" = show ONLY the Confirm-table-routing step (pass 1, before B20/B14.1);
   *  "columns" = show ONLY the Column-matching step (pass 2, after B14.1). Absent = full gate. */
  locked_phase?: "tables" | "columns";
  total_reviewable: number;
  review_items_by_table: Record<string, MigrationPreSemanticReviewItem[]>;
  /** Deterministic mappings already auto-approved (identity/alias/table-alias), shown
   *  read-only so every mapped source column is visible (e.g. site_ref → id). */
  auto_approved_by_table?: Record<string, MigrationPreSemanticReviewItem[]>;
  /** Full plenum_cafm table list — populates the "Canonical target table" dropdown. */
  existing_canonical_tables?: string[];
  /** Each candidate target table → its column names. Drives live column
   *  re-matching when the user picks a different target table. */
  canonical_columns_by_table?: Record<string, string[]>;
  /** Mapper's suggested CAFM target table per source table (source → target). */
  suggested_target_by_table?: Record<string, string>;
  /** Per-table match confidence 0-1 for Step 1 (1.0 = exact name match, Haiku % for guesses). */
  table_match_confidence_by_table?: Record<string, number>;
  /** 7.4 AC6 — actionable routing suggestion per source table when no confident match. */
  table_routing_suggestion_by_table?: Record<string, MigrationTableRoutingSuggestion>;
  /** Best-guess auto-suggestion per UNMAPPED field: {table: {field: suggestion}}. */
  unresolved_suggestion_by_table?: Record<string, Record<string, MigrationUnresolvedSuggestion>>;
  /** Content-aware destination-table candidates per source sheet, ranked by column
   *  overlap — surfaced as one-click "best match" options to select the right table. */
  table_match_candidates_by_table?: Record<string, MigrationTableCandidate[]>;
  /** Challenge 1 — column pairs with partial value overlap (60-95%) flagged for the user
   *  to review/merge: {source_table: [{column_a, column_b, overlap, …}]}. */
  near_duplicate_columns_by_table?: Record<string, MigrationNearDuplicatePair[]>;
  /** B7.1→B12.1 — table-resolution report driving the Step-1 Migration-Analysis panels
   *  (unique table identification → PK detection → deterministic / RAG / semantic mapping). */
  table_resolution?: UdrTableResolution | null;
  /** B13.1→B21.1 — column-intelligence report (prefixing → metadata → FK → format/value gates →
   *  grouping → unified names → classification → destination column mapping). */
  column_intelligence?: UdrColumnIntelligence | null;
  /** Primary-key confirmation (HITL) — per-table detected PK + per-column unique/null stats so
   *  the user can approve or CHANGE the PK before hierarchy / FK detection runs. */
  pk_confirmation?: Record<string, MigrationPkConfirmation>;
  /** True when this gate opened ONLY to confirm primary keys (no field mappings to review). */
  pk_only?: boolean;
  instructions?: string;
};

/** Per-table primary-key proposal shown at the confirmation gate. */
export type MigrationPkConfirmation = {
  /** The auto-detected PK columns ([] / ["_udr_id"] ⇒ surrogate). */
  detected_pk: string[];
  kind?: string | null;
  confidence?: number | null;
  /** True when no natural PK was found and a surrogate key is proposed. */
  surrogate?: boolean;
  /** Every column with its uniqueness / null-rate so the user can re-pick the PK. */
  columns: Array<{ column: string; uniqueness: number; null_rate: number; qualifies: boolean }>;
  sampled?: boolean;
  sample_rows?: number;
};

export type MigrationNearDuplicatePair = {
  column_a: string;
  column_b: string;
  overlap: number;
  sample_a?: string[];
  sample_b?: string[];
};

export type MigrationTableCandidate = {
  table: string;
  mapped: number;
  total: number;
  pct: number;
};

export type MigrationUnresolvedSuggestion = {
  source_field: string;
  target_field?: string | null;
  confidence: number;
  candidates?: MigrationMappingCandidate[];
  sample_values?: string[];
  data_type?: string | null;
};

export type MigrationTableRoutingSuggestion = {
  action: "assign" | "create";
  target?: string | null;
  suggested_new_name?: string | null;
  reason?: string;
  confidence?: number;
};

/** #6 — per-component match score breakdown for a mapped column (display-only). */
export type ColumnScoreBreakdown = {
  source_column: string;
  target_column: string;
  semantic_score: number;
  keyword_score: number;
  datatype_score: number;
  numeric_pattern_score: number;
  ontology_score: number;
  final_score: number;
};

export type MigrationFlaggedFieldItem = {
  source_field: string;
  source_table: string;
  target_field: string | null;
  confidence: number;
  tier: string;
  rationale?: string | null;
  /** Top candidate target columns. Backend sends {target, confidence}; legacy paths may send plain strings. */
  suggestions?: Array<{ target: string; confidence: number } | string>;
  sample_values?: string[];
  /** #6 — match score breakdown incl. numeric pattern. */
  score_breakdown?: ColumnScoreBreakdown | null;
};

export type MigrationUnmappedFieldItem = {
  source_field: string;
  source_table: string;
  sample_values?: string[];
};

export type MigrationFieldMappingGatePayload = {
  flagged_by_table?: Record<string, MigrationFlaggedFieldItem[]>;
  unmapped_by_table?: Record<string, MigrationUnmappedFieldItem[]>;
  review_items_by_table?: Record<string, MigrationFlaggedFieldItem[]>;
  unmappable_items_by_table?: Record<string, MigrationUnmappedFieldItem[]>;
  existing_canonical_tables?: string[];
  /** Per destination table → its existing column names. Lets the Tier-2 unmappable
   *  review surface "use existing column" suggestions so two source tables routed
   *  to the same destination don't both create duplicate columns. */
  canonical_columns_by_table?: Record<string, string[]>;
  /** Source table → destination CAFM table the user CONFIRMED at the pre-semantic gate
   *  (Step 1 routing + Step 2 column matching). Tier-2 defaults its target-table
   *  dropdown to this so the user's Step-2 choice isn't silently replaced by the
   *  heuristic table-name guess. */
  table_routing?: Record<string, string>;
  /** Canonical column name resolved at the pre-semantic gate, keyed 'table.column'
   *  (e.g. 'Vendors.name' → 'vendor_name', 'WorkOrders_2.asset_no' → 'asset_id').
   *  Used by Tier-2 so the unmappable list shows the canonical instead of the raw
   *  Excel column, and the new-column input pre-fills the canonical name so DDL
   *  creates 'asset_id' on work_orders, not 'asset_no_2'. */
  column_canonical?: Record<string, string>;
  confidence_alert?: { message: string };
};

export type MigrationHierarchyRelationship = {
  source_table: string;
  source_column: string;
  target_table: string;
  target_column: string;
  relationship_type?: string;
  confidence?: number;
  data_match_rate?: string;
  reasoning?: string;
  system_default?: boolean;
  mapping_note?: boolean;
  read_only?: boolean;
};

export type MigrationHierarchyGatePayload = {
  hierarchies_to_review?: MigrationHierarchyRelationship[];
  proposed_structure?: string;
  single_table_import?: boolean;
  system_default_hierarchy?: boolean;
  import_table_name?: string | null;
  import_table_plenum_role?: string;
  total_hierarchies?: number;
  total_cycles?: number;
  total_orphans?: number;
  hierarchy_tree?: unknown;
  review_items?: unknown[];
};

/** One destination of the engine's dry run of the write (a Go-engine run's write gate). */
export type MigrationWritePlanTable = {
  source: string;
  dest: string;
  rows: number;
  merge_existing_assets: number;
  /** Rows already in the database: the write skips them. */
  already_present: number;
  /** Values the destination column cannot hold: written empty. */
  invalid_values: { column: string; count: number; dest_type: string; sample: string }[];
  rows_cannot_write: { reason: string; count: number }[];
  new_columns: string[];
  dropped_columns?: string[];
  widen_to_text?: string[];
  references_to_resolve?: Record<string, number>;
  creates_table?: boolean;
};

export type MigrationWritePlan = {
  tables: MigrationWritePlanTable[];
  ddl_statements?: number;
  input_hash?: string;
};

export type MigrationFinalGatePayload = {
  /** The engine's dry run of the write; absent on a Python-engine run. */
  plan?: MigrationWritePlan;
  summary: {
    total_fields: number;
    t1_mapped: number;
    t2_auto_mapped: number;
    t2_human_reviewed: number;
    skipped: number;
    mapping_coverage_pct: number;
    hierarchy?: string;
    rows_to_write?: number;
    overall_confidence?: number;
    source_filename?: string;
    source_type?: string;
    total_entities?: number;
    entity_counts?: Record<string, number>;
  };
};

export type MigrationGatePayload =
  | MigrationPreSemanticGatePayload
  | MigrationFieldMappingGatePayload
  | MigrationHierarchyGatePayload
  | MigrationFinalGatePayload
  | Record<string, unknown>;

// ── Schema Mapper gate payload types ─────────────────────────────────────────

export type SchemaPreSemanticItem = {
  source_table: string;
  source_field: string;
  target_field: string;
  confidence: number;
  tier: string;
  rationale?: string;
  sample_values?: string[];
};

export type SchemaPreSemanticGatePayload = {
  gate: string;
  schema_mapping_id?: string;
  total_reviewable: number;
  items_by_table: Record<string, SchemaPreSemanticItem[]>;
  /** Auto-approved (T1_alias) mappings per table — shown read-only so every mapped column is visible. */
  auto_approved_by_table?: Record<string, SchemaPreSemanticItem[]>;
  /** Source columns with NO deterministic match — shown so the complete column list is visible
   *  (each goes to Tier-2 semantic, or becomes a new column when the table is new). */
  unmapped_by_table?: Record<string, Array<{ source_table: string; source_field: string; data_type?: string; sample_values?: unknown[] }>>;
  /** Step-1 header counts. */
  matched_table_count?: number;
  new_table_count?: number;
  total_table_count?: number;
  /** Fiix source table → CAFM target table. */
  target_table_by_source?: Record<string, string>;
  /** Fiix source table → suggested NEW CAFM table name (when no existing table matches). */
  new_table_by_source?: Record<string, string>;
  /** All Fiix source tables (for the Step-1 table-routing list, incl. fully-new ones). */
  all_source_tables?: string[];
  /** Every Fiix object's full column list (name + source type) — for new-table columns. */
  source_columns_by_table?: Record<string, Array<{ field_name: string; data_type?: string }>>;
  /** CAFM table → its columns (for the per-field target-column dropdown). */
  canonical_columns_by_table?: Record<string, string[]>;
  /** All CAFM table names. */
  existing_canonical_tables?: string[];
  instructions?: string;
};

export type SchemaFlaggedMappingItem = {
  source_field: string;
  suggested_target?: string;
  confidence?: number;
  tier?: string;
  rationale?: string;
  suggestions?: string[];
};

export type SchemaUnmappedFieldGateItem = {
  source_field: string;
  data_type_hint?: string;
  nullable?: boolean;
  description?: string;
  actions_available?: string[];
  suggested_canonical_table?: string;
  suggest_new_table?: boolean;
};

/** A high-confidence auto-matched source column (shown read-only so the gate can
 *  display the COMPLETE per-table column inventory, not just the flagged subset). */
export type SchemaMatchedFieldItem = {
  source_field: string;
  target_field?: string;
  tier?: string;
  confidence?: number;
};

export type SchemaFieldMappingGatePayload = {
  schema_mapping_id?: string;
  total_flagged?: number;
  low_confidence_tier1?: Record<string, SchemaFlaggedMappingItem[]>;
  low_confidence_tier2?: Record<string, SchemaFlaggedMappingItem[]>;
  matched_fields?: Record<string, SchemaMatchedFieldItem[]>;
  unmapped_fields?: Record<string, SchemaUnmappedFieldGateItem[]>;
  unstructured_candidates?: Record<string, unknown[]>;
  existing_canonical_tables?: string[];
};

export type SchemaDetectedFk = {
  source_table: string;
  source_column: string;
  target_table: string;
  target_column: string;
  relationship_type?: string;
  confidence?: number;
  reasoning?: string;
};

export type SchemaHierarchyGatePayload = {
  detected_fks: SchemaDetectedFk[];
  hierarchy_levels?: Record<string, number>;
  structure?: string;
};

export type SchemaGateArtifactsSummary = {
  canonical_fields_count?: number;
  total_source_fields?: number;
  tier1_auto_mapped?: number;
  tier2_auto_mapped?: number;
  tier2_flagged?: number;
  unmappable?: number;
  mapping_coverage_pct?: number;
  detected_fk_count?: number;
  max_hierarchy_depth?: number;
  junction_table_count?: number;
};

export type SchemaGateArtifactsPayload = {
  gate?: string;
  schema_mapping_id?: string;
  suggested_schema_name?: string;
  external_cmms_name?: string;
  output_json_url?: string;
  output_csv_url?: string;
  output_sql_url?: string;
  summary?: SchemaGateArtifactsSummary;
  instructions?: string;
  action_required?: string;
};

export type SchemaMappingGateArtifactsReviewRequest = {
  new_schema_name: string;
};

export type SchemaMappingGatePayload =
  | SchemaPreSemanticGatePayload
  | SchemaFieldMappingGatePayload
  | SchemaHierarchyGatePayload
  | SchemaGateArtifactsPayload
  | Record<string, unknown>;

// ── Schema mapping stats ──────────────────────────────────────────────────────

export type SchemaMappingStats = {
  total_tables: number | null;
  total_fields: number | null;
  tier1_mapped: number | null;
  tier2_auto_mapped: number | null;
  tier2_flagged: number | null;
  unmapped: number | null;
  detected_fk_count: number | null;
  hierarchy_depth: number | null;
  mapping_coverage_pct: number | null;
};

// ── Schema gate decision types ────────────────────────────────────────────────

export type SchemaPreSemanticDecision = {
  source_table: string;
  source_field: string;
  decision: "approve" | "semantic";
  target_field?: string;
};

export type SchemaFieldMappingDecision =
  | { action: "accept"; source_field: string; source_table: string }
  | { action: "reject"; source_field: string; source_table: string }
  | { action: "override"; source_field: string; source_table: string; target_field: string; rationale?: string }
  | {
      action: "custom";
      source_field: string;
      source_table: string;
      target_table: string;
      custom_column_name: string;
      data_type: string;
      nullable?: boolean;
      is_new_table?: boolean;
      new_table_name?: string;
      new_table_pk?: string;
    }
  | { action: "raw_metadata"; source_field: string; source_table: string }
  | { action: "skip"; source_field: string; source_table: string };

export type SchemaHierarchyDecision = {
  source_table: string;
  source_column: string;
  target_table: string;
  target_column: string;
  confirmed: boolean;
};

// ── List / audit / mapping record types ──────────────────────────────────────

export type MigrationDownloadFormat = "json" | "csv" | "sql" | "pdf";

export type MigrationDownloadResponse = {
  download_url: string;
  expires_in_minutes: number;
};

export type MigrationListItem = {
  migration_id: string;
  cmms_name: string;
  status: MigrationStatus;
  progress_pct: number;
  started_at: string;
  completed_at: string | null;
  t1_mapped_count: number;
  t2_auto_count: number;
  total_fields: number;
};

export type MigrationListResponse = {
  migrations: MigrationListItem[];
  total: number;
};

export type SchemaMappingListItem = {
  schema_mapping_id: string;
  external_cmms_name: string;
  status: SchemaMappingStatus;
  progress_pct: number;
  started_at: string;
  completed_at: string | null;
  stats: SchemaMappingStats;
};

export type SchemaMappingListResponse = {
  sessions: SchemaMappingListItem[];
  total: number;
};

export type MappingRecord = {
  source_field: string;
  source_table?: string;
  target_field: string | null;
  confidence: number;
  tier: string;
  rationale?: string;
  mapped_at?: string;
};

export type MappingListResponse = {
  total_mappings: number;
  tier_breakdown: Record<string, number>;
  mappings: MappingRecord[];
};

export type SchemaUnmappedFieldItem = {
  source_table: string;
  source_field: string;
  data_type_hint?: string;
  sample_values?: string[];
  nullable?: boolean;
};

export type SchemaUnmappedResponse = {
  schema_mapping_id: string;
  unmapped_count: number;
  unmapped_fields: SchemaUnmappedFieldItem[];
};

export type AuditEntry = {
  timestamp: string;
  event: string;
  node?: number;
  details?: Record<string, unknown>;
};

export type SchemaAuditEntry = {
  timestamp: string;
  event: string;
  node?: number;
  gate_type?: string;
  details?: Record<string, unknown>;
};

export type ExtraFieldConfig = {
  source_field: string;
  source_table: string;
  storage_strategy: "custom" | "raw_metadata" | "skip";
  target_table?: string;
  custom_column_name?: string;
  data_type?: string;
  nullable?: boolean;
  user_approved: boolean;
};

export type SchemaCustomMappingRequest = {
  source_field: string;
  source_table: string;
  target_field: string;
  rationale?: string;
};

export type SchemaCustomMappingResponse = {
  tier: string;
  confidence: number;
  status: string;
};

export type ListMigrationsParams = {
  organization_id?: string;
  status?: string;
  limit?: number;
  offset?: number;
};

export type ListSchemaMappingsParams = {
  organization_id?: string;
  status?: string;
  limit?: number;
  offset?: number;
};

export type TestingArtifactParams = {
  migrationId: string;
  filename: string;
};

export type FiixPlatform = "fiix";

export type FiixCredentials = {
  app_key: string;
  access_key: string;
  secret: string;
};

export type Node4HumanReviewRequest = {
  migration_id: string;
  tier1_mappings: unknown[];
  tier2_flagged_mappings: unknown[];
  tier2_unmappable: unknown[];
  flagged_approvals: unknown[];
  custom_mappings: unknown[];
  intentionally_unmapped: unknown[];
};

export type Node4FinalMapping = {
  source_field: string;
  target_field: string;
  confidence: number;
  approval_status: string;
  source: string;
};

export type Node4MappingStats = {
  auto_approved: number;
  human_approved: number;
  custom_added: number;
  intentionally_unmapped: number;
  overall_confidence: number;
};

export type Node4HumanReviewResponse = {
  migration_id: string;
  total_source_fields: number;
  final_mappings: Node4FinalMapping[];
  intentionally_unmapped: string[];
  tier2_flagged_mappings: unknown[];
  tier2_unmappable_count: number;
  mapping_stats: Node4MappingStats;
  el_m4_passed: boolean;
  error_message: string | null;
  duration_ms: number;
  execution_logs: string[];
};

export type Node5PreprocessRequest = {
  migration_id: string;
  cleaned_tables: Record<string, unknown[]>;
  final_mappings: Node4FinalMapping[];
  table_names: string[];
};

export type Node5PreprocessResponse = {
  migration_id: string;
  cleaned_tables: Record<string, unknown[]>;
  total_original_rows: number;
  total_rows_post_dedup: number;
  total_dedup_drop_count: number;
  overall_dedup_ratio: number;
  table_metrics: unknown[];
  data_quality_warnings: unknown[];
  detected_fk_columns: Record<string, unknown>;
  el_m5_passed: boolean;
  error_message: string | null;
  duration_ms: number;
  execution_logs: string[];
};

export type Node6ResolveHierarchyRequest = {
  migration_id: string;
  cleaned_tables: Record<string, unknown[]>;
  final_mappings: Node4FinalMapping[];
};

export type Node6ResolveHierarchyResponse = {
  migration_id: string;
  fk_candidates_count: number;
  confirmed_fks_count: number;
  hierarchy_cycles_count: number;
  implicit_hierarchies_count: number;
  self_referencing_trees_count: number;
  fk_candidates: unknown[];
  confirmed_hierarchies: unknown[];
  hierarchy_cycles: unknown[];
  implicit_hierarchies: Record<string, unknown>;
  containment_hierarchy: Record<string, unknown>;
  el_m6_passed: boolean;
  error_message: string | null;
  duration_ms: number;
  execution_logs: string[];
};

export type Node7VerifyHierarchyRequest = {
  migration_id: string;
  confirmed_hierarchies: unknown[];
  hierarchy_cycles: unknown[];
  customer_corrections: unknown[];
};

export type Node7VerifyHierarchyResponse = {
  migration_id: string;
  hierarchies_approved: number;
  cycles_resolved: number;
  hierarchy_confirmed: boolean;
  confirmed_hierarchies: unknown[];
  containment_hierarchy: Record<string, unknown>;
  el_m7_passed: boolean;
  error_message: string | null;
  duration_ms: number;
  execution_logs: string[];
};

export type Node8GenerateOutputRequest = {
  migration_id: string;
  final_mappings: Node4FinalMapping[];
  cleaned_tables: Record<string, unknown[]>;
  hierarchy_relationships: unknown[];
};

export type Node8GenerateOutputResponse = {
  migration_id: string;
  json_generated: boolean;
  csv_generated: boolean;
  sql_generated: boolean;
  report_generated: boolean;
  output_json_url: string | null;
  output_csv_url: string | null;
  output_sql_url: string | null;
  migration_report_url: string | null;
  intermediate_schema: Record<string, unknown>;
  intermediate_schema_valid: boolean;
  schema_validation_errors: unknown[];
  el_m8_passed: boolean;
  error_message: string | null;
  duration_ms: number;
  execution_logs: string[];
};

export type Node9WriteOutputRequest = {
  migration_id: string;
  intermediate_schema: Record<string, unknown>;
  customer_approval: boolean;
};

export type Node9WriteOutputResponse = {
  migration_id: string;
  handoff_complete: boolean;
  handoff_status: string | null;
  ingestion_service_url: string | null;
  ingestion_status: string | null;
  write_review_payload: Record<string, unknown>;
  el_m9_passed: boolean;
  error_message: string | null;
  duration_ms: number;
  execution_logs: string[];
};

export type MigrationStartUploadRequest = {
  file: File;
  cmms_name: string;
  organization_id: string;
};

export type MigrationStartUploadResponse = {
  migration_id: string;
  status: string;
  progress_pct: number;
  message: string;
};

export type MigrationGateFieldMappingDecision = {
  action: "accept" | "reject" | "override";
  source_field: string;
  target_field: string | null;
  rationale: string | null;
  /** Override → "New column (DDL)": the target column does NOT exist yet and must be CREATED on the
   *  destination table. Carries the DDL so the backend records it (and the writer runs ALTER TABLE)
   *  instead of silently dropping an unknown column on a core table. */
  is_new_column?: boolean;
  data_type?: string | null;
  nullable?: boolean;
};

export type MigrationGateFieldMappingUnmappedDecision = {
  action: "custom" | "raw_metadata" | "skip";
  source_field: string;
  target_table: string | null;
  custom_column_name: string | null;
  data_type: string | null;
  nullable?: boolean | null;
  is_new_table?: boolean | null;
  new_table_name?: string | null;
  new_table_pk?: string | null;
};

export type MigrationGateFieldMappingRequest = {
  flagged: Record<string, MigrationGateFieldMappingDecision[]>;
  unmapped: Record<string, MigrationGateFieldMappingUnmappedDecision[]>;
};

export type MigrationFieldMappingDraftEnvelope = {
  body: MigrationGateFieldMappingRequest;
  meta?: {
    canonicalTableBySource?: Record<string, string>;
    savedAt?: number;
  };
};

export type MigrationGateHierarchyRequest = {
  confirmed_hierarchies?: unknown[];
  hierarchy_corrections?: Record<string, unknown>;
  plenum_default_hierarchy_accepted?: boolean;
};

export type MigrationGatePreSemanticRequest = {
  decisions: Record<
    string,
    Array<{ source_field: string; decision: "approve" | "semantic"; target_field?: string; data_type?: string }>
  >;
  /** WP-5: rename a source table's target / create a new table at the pre-semantic gate. */
  table_overrides?: Record<string, { target_table: string; is_new_table: boolean }>;
  /** HITL canonical naming — user-pinned canonical names for B19.1 groups or
   *  individual columns. Keys are group_id ('G2') OR prefixed_key ('vendors.id');
   *  values are the canonical name the user picked. The backend applies these
   *  on every subsequent re-run of build_column_intelligence so the override
   *  propagates through B14/B17/B18/B19/B20/B21 and downstream nodes. */
  canonical_overrides?: Record<string, string>;
  /** Primary-key confirmation (HITL): the user-approved PK column per source table.
   *  {table: "col"} or {table: ["col", ...]}; "" / "__surrogate__" ⇒ surrogate key.
   *  Applied before hierarchy / FK detection and reflected in the B8.1 report. */
  pk_overrides?: Record<string, string | string[]>;
};

/** Group A gate (pk_approval) — the user's approved primary key per source table. */
export type MigrationGatePkApprovalRequest = {
  pk_overrides: Record<string, string | string[]>;
};

/** Group A step 2 gate (unique_table_approval) — a simple confirm; the PK was decided already. */
export type MigrationGateUniqueTableApprovalRequest = {
  approved?: boolean;
};

/** B14.1 column-mapping gate — per-column matched-destination approval. */
export type MigrationColumnMappingGatePayload = {
  gate: string;
  total_reviewable?: number;
  dest_mapping?: UdrColDestMap[];
  dest_columns_by_table?: Record<string, string[]>;
  instructions?: string;
};

/** B14.1 gate submit — {source_table: {source_field: "<dest_col>"|"__new__"}}; unlisted columns
 *  keep their matched destination. */
export type MigrationGateColumnMappingApprovalRequest = {
  overrides: Record<string, Record<string, string>>;
};

/** Payload the pk_approval gate (Group A) carries: duplicate/unique/PK report + PK proposal. */
export type MigrationPkApprovalGatePayload = {
  gate: string;
  total_reviewable?: number;
  pk_confirmation?: Record<string, MigrationPkConfirmation>;
  table_resolution?: UdrTableResolution | null;
  instructions?: string;
};

/** Group A step 2 — unique-table identification approval. Runs AFTER the PK gate; each unique
 *  table is shown with its confirmed PK. A simple confirm (the PK was decided at the prior gate). */
export type MigrationUniqueTableGatePayload = {
  gate: string;
  total_reviewable?: number;
  table_resolution?: UdrTableResolution | null;
  pk_confirmed_by_table?: Record<string, string[]>;
  instructions?: string;
};

/** B19.1 column-grouping sign-off gate payload. */
/** B20.1 classification gate — approve / re-classify / exclude the PK, FK and Shared assignments. */
export type MigrationClassificationGatePayload = {
  gate: string;
  total_reviewable?: number;
  /** Count of Primary-Key groups shown for observability (approved at the PK gate). */
  pk_count?: number;
  classification?: UdrGroupClassification[];
  shared_attribute_tables?: NonNullable<UdrColumnIntelligence["shared_attribute_tables"]>;
  fk_candidates?: UdrFkCandidate[];
  instructions?: string;
};

export type MigrationGateClassificationApprovalRequest = {
  /** group_ids the user DE-selected (nothing of theirs is applied). */
  rejected_groups: string[];
  /** Human re-classifications: {group_id: "fk" | "shared"} — a FK demoted to Shared Attribute
   *  gets a lookup table instead of enforcement; a Shared promoted to FK skips its lookup table. */
  verdict_overrides?: Record<string, "fk" | "shared">;
};

export type MigrationGateFinalRequest = {
  confirmed: boolean;
};

export type MigrationRetryDdlRequest = {
  extra_fields_config: unknown[];
};

export type MigrationStatusResponse = {
  migration_id: string;
  status: MigrationStatus;
  progress_pct: number;
  current_step: number;
  cmms_name: string;
  started_at: string | null;
  completed_at: string | null;
  t1_mapped_count: number;
  t2_auto_count: number;
  t2_human_count: number;
  unmapped_count: number;
  total_fields: number;
  /** Original uploaded source file — persisted to blob, downloadable post-migration. */
  source_filename?: string | null;
  /** Relative route to download the source file (prefix via migrationSourceUrl). */
  source_download_url?: string | null;
  output_json_url: string | null;
  output_csv_url: string | null;
  output_sql_url: string | null;
  migration_report_url: string | null;
  /** Recommended finalized DB-structure summary (.md) — Feature 4a. */
  output_structure_md_url?: string | null;
  pending_gate_type: "pre_semantic" | "field_mapping" | "hierarchy" | "final_confirmation" | string | null;
  pending_gate_payload: MigrationGatePayload | null;
  /** Server-persisted Tier-2 / field-mapping UI draft. */
  field_mapping_draft?: MigrationFieldMappingDraftEnvelope | MigrationGateFieldMappingRequest | null;
  /** F7 7.9/7.10 — Test 1 (chunk→PK) + Test 2 (overlap→FK) reports, when the run produced them. */
  udr_test_results?: UdrTestResults | null;
  /** F7 7.12/7.13 — inferred-relationship review queue + sanctity ('Relationship Quality') flags. */
  udr_relationship_report?: UdrRelationshipReport | null;
  /** B7.1→B12.1 — Migration-Analysis table-resolution report (cards · PK · per-method table mapping). */
  udr_table_resolution?: UdrTableResolution | null;
  /** B13.1→B21.1 — Column-Intelligence Pipeline (prefixing · grouping · unified names · classification · dest map). */
  udr_column_intelligence?: UdrColumnIntelligence | null;
  error_message: string | null;
  /** "go" when hoist-engine does the run's row-heavy steps; null on a Python-engine run. */
  engine?: string | null;
  /** While a Go step runs: where it is (written at most every half second, kept 120 s). */
  engine_progress?: MigrationEngineProgress | null;
  nodes: NodeInfo[];
};

export type MigrationEngineProgress = {
  engine: string;
  step: "parse" | "combine" | "preprocess" | "outputs" | "write_plan" | "write" | "udr" | string;
  stage?: string | null;
  table?: string | null;
  done: number;
  total: number;
  rate_per_s?: number;
  /** UTC, ISO 8601, no zone. */
  at: string;
};

// ── B7.1 → B12.1 — Migration-Analysis table-resolution report ──────────────────
export type UdrTableCardSample = { column: string; values: string[] };
export type UdrTableCard = {
  table: string;
  primary_key: string[];
  primary_key_kind?: string;
  column_count: number;
  row_count?: number;
  samples: UdrTableCardSample[];
};
export type UdrPairwisePair = {
  table_a?: string;
  table_b?: string;
  metadata_similarity?: number;
  name_similarity?: number;
};
export type UdrPairwiseVerdict = {
  highest_pair?: UdrPairwisePair | null;
  auto_consolidated?: string[][];
  candidates?: unknown[];
  verdict?: string;
};
export type UdrPkDetectionRow = {
  table: string;
  kind: string;
  primary_key: string[];
  uniqueness?: number;
  null_rate?: number;
  tie_break?: string;
  method?: string;
  confidence?: number;
};
export type UdrDeterministicRow = {
  source: string;
  method: string; // exact | levenshtein | none
  destination?: string | null;
  confidence?: number | null;
  note?: string;
};
export type UdrRagAliasRow = {
  source: string;
  alias_hit?: string | null;
  destination?: string | null;
  confidence?: number | null;
  alias_source?: string | null;
};
export type UdrSemanticRow = {
  source: string;
  signal?: string;
  destination?: string | null;
  confidence?: number | null;
  method?: string;
  band?: string; // suggested | review
};
export type UdrFinalDecisionRow = {
  source: string;
  destination?: string | null;
  method: string;
  confidence?: number | null;
};
/** One Node-1 column merge: columns whose values were row-for-row identical, collapsed to a
 *  single name before table mapping and primary-key detection. */
export type UdrMergedColumn = {
  table: string;
  /** The surviving column name (longest / most specific of the members). */
  kept: string;
  /** The redundant names removed from the dataset. */
  dropped: string[];
  /** Every column in the merge, in source order (kept + dropped). */
  members: string[];
  /** Always 100 — the merge only fires on exact row-for-row identity. */
  match_pct?: number;
  /** Rows compared when the merge was decided. */
  row_count?: number;
};
export type UdrDuplicateTableGroup = {
  /** The tables that look like duplicates of each other (share similar columns). */
  tables: string[];
  /** How many tables are in this duplicate group (e.g. 2). */
  count: number;
  /** Representative name for the group (e.g. work_order_1 + work_order_2 → "work_order"). */
  label?: string;
  /** Columns shared across every table in the group — the duplicate evidence. */
  shared_columns: string[];
  /** Highest pairwise column-overlap (Jaccard) within the group, 0-1. */
  similarity: number;
};

export type UdrTableResolution = {
  /** Pre-B7.1 — tables flagged as duplicates because they share similar columns, shown
   *  BEFORE unique-table identification. */
  duplicate_tables?: {
    groups?: UdrDuplicateTableGroup[];
    duplicate_count?: number;
    checked?: number;
  };
  /** Pre-B8.1 — columns merged in Node 1 because their values were row-for-row identical
   *  (works.tagnum == works.id → tagnum). Shown BEFORE primary-key detection, since the PK
   *  is chosen from the merged column list. */
  merged_columns?: UdrMergedColumn[];
  metadata_cards?: UdrTableCard[];
  pairwise?: UdrPairwiseVerdict;
  pk_detection?: UdrPkDetectionRow[];
  deterministic?: UdrDeterministicRow[];
  rag_alias?: UdrRagAliasRow[];
  semantic?: UdrSemanticRow[];
  final_decisions?: UdrFinalDecisionRow[];
  counts?: { tables?: number; deterministic?: number; rag_alias?: number; semantic?: number };
};

// ── B13.1 → B21.1 — Column-Intelligence Pipeline report ─────────────────────────
export type UdrColPrefix = {
  source_table: string;
  column: string;
  prefixed_key: string;
  /** B13.1 — prefix built from the CONFIRMED destination table (falls back to source when a table
   *  wasn't routed). This is what B13.1 displays now that routing is confirmed. */
  dest_prefixed_key?: string;
  /** Every source table feeding this destination column. Longer than 1 when duplicate sheets
   *  were routed to the same destination (WorkOrders + WorkOrders_2 → work_orders), in which
   *  case B13.1 shows ONE row for the column rather than one per source. */
  source_tables?: string[];
  dest_table?: string | null;
  source_column?: string;
  canonical_name?: string | null;
  canonical_prefixed_key?: string;
  /** PK | FK | Shared — surfaced so B13.1 prefixing can pin the PK badge. */
  classification?: string;
  /** True when the canonical override was suppressed because applying it would
   *  collide with another column already in the same source table. */
  canonical_suppressed?: boolean;
};
export type UdrColMeta = {
  prefixed_key: string;
  /** {table}.{canonical_name} — the unified identifier used as the row's display key. */
  canonical_prefixed_key?: string;
  source_table?: string;
  column?: string;
  source_column?: string;
  dest_table?: string | null;
  classification: string; // PK | FK | Shared
  format: string;
  samples: string[];
  canonical_name?: string | null;
  /** True when the canonical override was suppressed within this table (collision). */
  canonical_suppressed?: boolean;
};
export type UdrFkCandidate = {
  src_table: string; src_column: string;
  dst_table?: string | null; dst_column?: string | null;
  ri?: number | null; confirmed?: boolean; reason?: string;
};
export type UdrFormatPair = {
  col_a: string; col_b: string;
  /** Canonical-overridden forms of col_a/col_b (e.g. 'vendors.asset_id'). Falls back to col_a/col_b. */
  canonical_col_a?: string; canonical_col_b?: string;
  format_a: string; format_b: string; score: number; pass: boolean;
  /** 0–100 display percentage (mirrors backend score_pct). */
  score_pct?: number;
  threshold_pct?: number;
  scope?: "cross_table" | "within_table";
  table?: string | null;
  /** Value-pattern result carried inline (present iff the format gate passed) so the pairwise
   *  gate table can show the value check without joining the separately-capped value_pattern list. */
  value_score_pct?: number;
  value_decision?: string;
};
export type UdrValuePair = {
  col_a: string; col_b: string;
  canonical_col_a?: string; canonical_col_b?: string;
  score: number; decision: string; overlap?: number; overlap_kind?: string;
  score_pct?: number;
  overlap_pct?: number;
  threshold_pct?: number;
  scope?: "cross_table" | "within_table";
  table?: string | null;
};
export type UdrWithinTableGroup = {
  group_id: string;
  table: string;
  format?: string;
  members: string[];
  canonical_members?: string[];
  member_keys?: { table: string; column: string }[];
};
export type UdrWithinTableSimilarity = {
  format_gate?: UdrFormatPair[];
  value_pattern?: UdrValuePair[];
  groups?: UdrWithinTableGroup[];
  summary?: {
    pairs_scored?: number;
    format_survivors?: number;
    value_survivors?: number;
    groups?: number;
    format_threshold_pct?: number;
    value_threshold_pct?: number;
  };
};
export type UdrColGroup = {
  group_id: string;
  canonical_name: string;
  format?: string;
  basis?: string;
  members: string[];
  /** Canonical-overridden form of `members` (e.g. ['vendors.asset_id', ...]), order-aligned with members. */
  canonical_members?: string[];
  member_keys?: { table: string; column: string }[];
  /** Classification + joining-signal per member, indexed in the same order as `members`.
   *  joined_by='name' means the member's normalised column name equals the group's
   *  canonical (joined via column-name match); 'value' means it joined purely via
   *  cell value / format similarity (its name didn't match). */
  member_classes?: {
    prefixed_key: string;
    /** Canonical-overridden form of prefixed_key (vendors.asset_id from vendors.id). */
    canonical_prefixed_key?: string;
    classification: string;
    joined_by?: "name" | "value";
    /** Match % — PK parent = 100; FK = referential integrity (its values found in the group PK). */
    match_pct?: number;
  }[];
};
export type UdrGroupClassification = {
  group_id: string;
  canonical_name?: string;
  members: string[];
  has_pk?: string | null;
  fk_column?: string | null;
  /** Canonical-overridden forms of has_pk / fk_column for consistent display. */
  canonical_has_pk?: string | null;
  canonical_fk_column?: string | null;
  ri?: number | null;
  verdict: string;
};
export type UdrColDestMap = {
  source: string;
  /** Canonical-overridden form of `source` (e.g. 'vendors.asset_id' from 'vendors.id').
   *  Falls back to `source` when no override applies. */
  canonical_source?: string;
  canonical_name?: string | null;
  dest_table?: string | null;
  matched_column?: string | null;
  confidence?: number | null;
  outcome: string;
  /** Combined-scorer breakdown (present only when the destination had sample values to
   *  value-check against): name · value · ontology · context · final, each 0..1 (null = the
   *  dimension couldn't be evaluated). Drives the auto-resolve/suggest/new-column decision. */
  scores?: {
    name?: number | null;
    value?: number | null;
    ontology?: number | null;
    context?: number | null;
    final?: number | null;
  } | null;
  /** Bare source column name (the part after the source-table prefix in `source`). */
  source_column?: string;
  /** Every source table that contributed this column — >1 when duplicate sheets were co-routed
   *  to the same destination (work_order + workorders → work_orders). A gate decision fans out
   *  to all of them. */
  source_tables?: string[];
  /** B14.1 four dimensions carried per row so the column-mapping gate can show them inline. */
  is_primary_key?: boolean;
  classification?: string;
  format?: string;
  samples?: string[];
};
export type UdrColumnIntelligence = {
  /** Marker for the canonical-naming/grouping engine that produced this report — lets a
   *  deployed payload be checked against the source to confirm a rebuild took effect. */
  engine_version?: string;
  prefixing?: UdrColPrefix[];
  metadata?: UdrColMeta[];
  metadata_total?: number;
  fk_candidates?: UdrFkCandidate[];
  format_gate?: UdrFormatPair[];
  value_pattern?: UdrValuePair[];
  /** Duplicate-table groups (e.g. work_order + workorders) so the UI can collapse the raw
   *  format_gate pairs (which, unlike the grouped output, are not duplicate-collapsed). */
  duplicate_groups?: Array<{ tables: string[]; representative: string; label: string; count: number }>;
  /** B22.1 — lookup tables synthesized from shared attributes: the shared attribute becomes the PK
   *  of a new appended table, rows = its distinct values (e.g. asset_type → asset_types table). */
  shared_attribute_tables?: Array<{
    table_name: string;
    pk_column: string;
    from_group: string;
    source_columns: string[];
    distinct_count: number;
    sample_values: string[];
    /** Source column → new lookup PK it now references. */
    fk_rewrites?: Array<{ source: string; references: string }>;
    /** Emitted DDL: CREATE TABLE for the lookup, and ALTER … FOREIGN KEY per source column. */
    create_ddl?: string;
    fk_ddl?: string[];
  }>;
  /** B17.1 — per source column, its Top-3 cross-table matches that passed the ≥80% format gate,
   *  each with format % + value-pattern % and `both` (passed BOTH → this is what groups). */
  top_format_matches?: Array<{
    source: string;
    format: string;
    matches: Array<{ target: string; format_pct: number; value_pct: number; both: boolean }>;
  }>;
  /** B17.1 format grouping (server-built) — columns that share a cell-value format, grouped
   *  ONCE per table (within-table) and across all tables (cross-table). */
  format_groups?: {
    within_table?: Array<{ table: string; format: string; columns: string[]; accuracy_pct?: number }>;
    cross_table?: Array<{ format: string; columns: string[] }>;
  };
  /** B18.1 value-shape grouping (server-built) — columns sharing a dominant value skeleton. */
  value_pattern_groups?: {
    within_table?: Array<{ table: string; value_shape: string; columns: string[] }>;
    cross_table?: Array<{ value_shape: string; columns: string[] }>;
  };
  /** Same-table column pairs scored on format + value pattern (B17.1/B18.1). */
  within_table_similarity?: UdrWithinTableSimilarity;
  groups?: UdrColGroup[];
  classification?: UdrGroupClassification[];
  column_canonical?: Record<string, string>;
  dest_mapping?: UdrColDestMap[];
  summary?: {
    columns_analyzed?: number;
    pk_groups?: number;
    fk_groups?: number;
    shared_groups?: number;
    columns_mapped?: number;
    groups?: number;
    confidence?: number | null;
    format_pairs_scored?: number;
    format_survivors?: number;
    value_survivors?: number;
    format_threshold_pct?: number;
    value_threshold_pct?: number;
    within_table_pairs_scored?: number;
    within_table_format_survivors?: number;
    within_table_value_survivors?: number;
    within_table_groups?: number;
    cross_table_groups?: number;
  };
};

// ── F7 7.9 / 7.10 — UDR quality test reports + resolution actions ───────────────
export type UdrTest1Failure = {
  chunk_id?: string;
  association_value?: string;
  found_in_table?: string;
  found_in_column?: string;
};
export type UdrTest1Report = {
  test_name?: string;
  skipped?: boolean;
  reason?: string;
  blocked?: boolean;
  total?: number;
  passing?: number;
  failing?: number;
  fail_rate?: number;
  failures?: UdrTest1Failure[];
};
export type UdrTest2Pair = {
  table_a?: string;
  column_a?: string;
  table_b?: string;
  column_b?: string;
  overlap?: number;
  explained?: boolean;
};
export type UdrTest2Report = {
  test_name?: string;
  skipped?: boolean;
  reason?: string;
  blocked?: boolean;
  total_pairs?: number;
  fail_rate?: number;
  flagged?: UdrTest2Pair[];
};
export type UdrTestResults = {
  test_1?: UdrTest1Report;
  test_2?: UdrTest2Report;
};
export type UdrTest2ResolutionAction = "define_fk" | "create_reference_table" | "mark_coincidental";
export type UdrTest2Resolution = {
  table_a: string;
  column_a: string;
  table_b: string;
  column_b: string;
  action: UdrTest2ResolutionAction;
};
export type UdrTest2ResolveRequest = { resolutions: UdrTest2Resolution[] };

// ── F7 7.12 / 7.13 — relationship report (inferred review queue + sanctity flags) ──
export type UdrInferredRelationship = {
  src_entity?: string;
  src_column?: string;
  dst_entity?: string;
  dst_column?: string;
  confidence?: number;
  evidence?: string;
  reason?: string;
};
export type UdrSanctityFlag = {
  entity_id?: string;
  asset_id?: string;
  wo_id?: string;
  confidence?: number;
  reason?: string;
  excerpt?: string;
  suggested?: string;
};
export type UdrRelationshipReport = {
  schema_relationship_count?: number;
  inferred_count?: number;
  inferred?: UdrInferredRelationship[];
  sanctity_flag_count?: number;
  sanctity_flags?: UdrSanctityFlag[];
};

export type SchemaMappingConnectorType = "fiix" | "upload";

export type SchemaMappingStartRequest = {
  connector_type: SchemaMappingConnectorType;
  external_cmms_name: string;
  organization_id: string;
  fiix_subdomain?: string;
  fiix_app_key?: string;
  fiix_access_key?: string;
  fiix_secret_key?: string;
  schema_content?: string;
  schema_source?: string;
  schema_format?: string;
};

export type SchemaMappingStartResponse = {
  schema_mapping_id: string;
  status: string;
  progress_pct?: number;
  message?: string;
};

export type SchemaComparisonSide = {
  label: string;
  table_count: number;
  column_count: number;
  canonical_field_count?: number;
};

export type SchemaComparisonPayload = {
  fiix: SchemaComparisonSide;
  plenum_cafm: SchemaComparisonSide;
  markdown?: string;
};

export type SchemaMappingStatusResponse = {
  schema_mapping_id: string;
  status: SchemaMappingStatus;
  current_node?: number;
  progress_pct?: number;
  external_cmms_name?: string;
  started_at?: string | null;
  completed_at?: string | null;
  schema_comparison?: SchemaComparisonPayload | null;
  stats?: SchemaMappingStats | null;
  pending_gate_type?: "pre_semantic" | "field_mapping" | "hierarchy" | "artifacts_review" | string | null;
  pending_gate_payload?: SchemaMappingGatePayload | null;
  output_json_url?: string | null;
  output_csv_url?: string | null;
  output_sql_url?: string | null;
  error_message?: string | null;
  nodes?: NodeInfo[];
};

// Covers both pre_semantic gate (SchemaPreSemanticDecision[]) and
// field_mapping gate (SchemaFieldMappingDecision[]) — same /gate/field-mapping endpoint
export type SchemaMappingGateFieldMappingRequest = {
  decisions: SchemaPreSemanticDecision[] | SchemaFieldMappingDecision[];
};

export type SchemaMappingGatePreSemanticRequest = {
  decisions: SchemaPreSemanticDecision[];
  /** Step-1 table routing: Fiix source table → chosen existing CAFM table. */
  table_overrides?: Record<string, string>;
  /** Step-1: Fiix source table → NEW CAFM table name to create. */
  new_tables?: Record<string, string>;
  /** Step-2: new-table columns to CREATE with explicit SQL types. */
  new_columns?: Record<
    string,
    Array<{ source_field: string; column_name: string; data_type: string }>
  >;
};

export type SchemaMappingGateHierarchyRequest = {
  approved_hierarchies: SchemaHierarchyDecision[];
  rejected_hierarchies: SchemaHierarchyDecision[];
};

// ── Feature 4 — Activity Log (Section 1) ────────────────────────────────────
export type ActivityTrigger = "query" | "external_input" | "threshold" | "scheduled";
export type ActivityStatus = "completed" | "pending_human_input" | "failed" | "escalated";
export type ActivityNotifColor = "green" | "orange" | "red";

export type ActivitySummaryEntry = {
  id: string;
  created_at: string | null;
  trigger: ActivityTrigger | string;
  trigger_detail?: string | null;
  outcome: string;
  status: ActivityStatus | string;
  notif_color: ActivityNotifColor | string;
  read: boolean;
  refs?: Record<string, unknown> | null;
};

export type ActivityUnreadSummary = {
  count: number;
  worst_color: ActivityNotifColor | null;
};

export type ActivityMappingDecision = {
  source_table?: string;
  source_column?: string;
  confidence?: number;
  dest_table?: string;
  dest_column?: string;
};
export type ActivityProcessingStage = { stage: string; label: string; summary?: string };
export type ActivityStepTable = { columns: string[]; rows: string[][] };
export type ActivityStepActionRef = { id: string; label: string };
export type ActivityReasoningStep = {
  /** "thought" = chain-of-thought (planning/decision), "action" = chain-of-action. */
  kind?: "thought" | "action";
  stage?: string;
  label?: string;
  text?: string;
  /** Timestamp (ISO or "HH:MM:SS") for the step. */
  at?: string;
  /** Elapsed time this stage took, in milliseconds. */
  duration_ms?: number;
  /** 0..1 — rendered as a confidence bar on the step. */
  confidence?: number;
  /** Small status/metric badges under the step (e.g. "2 auto-merged", "1 flagged"). */
  chips?: string[];
  /** Optional inline result table (table mapping, column mapping, Test 2…). */
  table?: ActivityStepTable;
  /** Optional reference to a Section-3 action item. */
  action_ref?: ActivityStepActionRef;
  status?: string;
  from_agent?: string;
  to_agent?: string;
  context?: string;
  // ── Per-stage execution metrics (AL.6 point 12) — the audit record for this stage. ──
  /** Which agent executed this stage (e.g. "Deterministic Mapper"). */
  agent?: string;
  /** The concrete tool/strategy the stage used (e.g. "exact + Levenshtein + RAG/alias"). */
  tool?: string;
  /** Items the stage consumed (e.g. source columns in). */
  input_count?: number;
  /** Items the stage produced (e.g. columns mapped out). */
  output_count?: number;
  /** Structured failure info on a `run_failed` (or blocked) step — renders the rich error card. */
  error?: ActivityStepError;
};
/** Structured, user-facing failure (backend `udr/errors.classify_migration_error`) — WHAT failed,
 *  WHY, whether it's recoverable, and WHAT TO DO next. Never a raw stack trace. */
export type ActivityStepError = {
  step?: string;
  category?: string;
  code?: string;
  user_message?: string;
  technical_reason?: string;
  impact?: string;
  recoverable?: boolean;
  retry_supported?: boolean;
  suggested_actions?: { label: string; action: string }[];
};
export type ActivityProcessingLog = {
  stages?: ActivityProcessingStage[];
  mapping_decisions?: ActivityMappingDecision[];
  /** Ordered CoT/CoA timeline (preferred — renders the full Section-2 sample). */
  steps?: ActivityReasoningStep[];
  /** chain of thought (decomposition, decision logic, confidence) — back-compat. */
  thought?: ActivityReasoningStep[];
  /** chain of action (data retrieved, engines called, handoffs, intermediate outputs). */
  action?: ActivityReasoningStep[];
};
export type ActivityDetailEntry = ActivitySummaryEntry & {
  processing_log?: ActivityProcessingLog | null;
};

// ── Section 3 — human-in-the-loop inline actions (AL.3/AL.4) ──
export type ActivityActionStatus = "pending" | "resolved" | "timed_out";
export type ActivityActionOption = { id: string; label: string; value?: unknown };
export type ActivityActionItem = {
  id: string;
  entry_id: string;
  kind: string;
  label: string;
  options: ActivityActionOption[];
  status: ActivityActionStatus;
  chosen_option_id?: string | null;
  resolution_note?: string | null;
  deadline_at?: string | null;
  resolved_at?: string | null;
  created_at?: string | null;
  /** CAFM-004/006 — a gate-review row that's resolved IN the migration panel (not by picking an
   *  inline option). The FE renders a "Review in migration panel" CTA instead of option buttons. */
  route_only?: boolean;
  /** Longer description for a route_only gate row (the gate's "what's needed" copy). */
  description?: string | null;
  /** The migration/run the gate row routes to when its CTA is clicked. */
  migration_id?: string | null;
};

// ── AL.5 — refinements & follow-ups (central query space) ──
export type ActivitySuggestion = {
  id: string;
  kind: "refinement" | "follow_up";
  label: string;
  prompt: string;
  requires_approval: boolean;
};
export type ActivitySuggestions = {
  refinements: ActivitySuggestion[];
  follow_ups: ActivitySuggestion[];
};

/** Pull the AL.5 suggestions an outcome entry carries in its ``refs`` (or null). */
export function activitySuggestionsOf(
  entry?: { refs?: Record<string, unknown> | null } | null,
): ActivitySuggestions | null {
  const raw = entry?.refs && typeof entry.refs === "object"
    ? (entry.refs as Record<string, unknown>)["suggestions"]
    : null;
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as { refinements?: ActivitySuggestion[]; follow_ups?: ActivitySuggestion[] };
  const refinements = obj.refinements ?? [];
  const follow_ups = obj.follow_ups ?? [];
  if (refinements.length === 0 && follow_ups.length === 0) return null;
  return { refinements, follow_ups };
}

// ── Feature 7 F7-3 — entity 'work cloud' (relationships from the persisted UDR graph) ──
export type EntityWorkCloudEdge = {
  direction: "in" | "out";
  via_column: string | null;
  related_entity: string;
  related_column: string | null;
  rel_type: string;
  provenance: string;
  confidence: number | null;
};
export type EntityWorkCloud = {
  entity: string;
  entity_id?: string;
  related_entity_types: string[];
  relationships: EntityWorkCloudEdge[];
};

// instance-level traversal — the related DATA ROWS for one entity instance
export type EntityInstanceRelated = {
  related_entity: string;
  direction: "in" | "out";
  via_column: string;
  rows: Record<string, unknown>[];
};
export type EntityInstanceCloud = {
  entity: string;
  entity_id: string;
  found: boolean;
  related: EntityInstanceRelated[];
  reason?: string;
};

export const schemaMapperApi = {
  testConnection: (platform: FiixPlatform) =>
    aiRequest<unknown>({
      method: "GET",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: `/platforms/${platform}/test-connection`,
    }),

  startSchemaMappingSession: (body: SchemaMappingStartRequest) =>
    aiRequest<SchemaMappingStartResponse, SchemaMappingStartRequest>({
      method: "POST",
      basePath: "/api/schema-mapping",
      body,
    }),

  getSchemaMappingStatus: (schemaMappingId: string) =>
    aiRequest<SchemaMappingStatusResponse>({
      method: "GET",
      basePath: "/api/schema-mapping",
      path: `/${encodeURIComponent(schemaMappingId)}/status`,
    }),

  advanceSchemaMapping: (schemaMappingId: string) =>
    aiRequest<SchemaMappingStatusResponse>({
      method: "POST",
      basePath: "/api/schema-mapping",
      path: `/${encodeURIComponent(schemaMappingId)}/advance`,
      body: {},
    }),

  gateSchemaMappingFieldMapping: (schemaMappingId: string, body: SchemaMappingGateFieldMappingRequest) =>
    aiRequest<SchemaMappingStatusResponse, SchemaMappingGateFieldMappingRequest>({
      method: "POST",
      basePath: "/api/schema-mapping",
      path: `/${encodeURIComponent(schemaMappingId)}/gate/field-mapping`,
      body,
    }),

  gateSchemaMappingPreSemantic: (schemaMappingId: string, body: SchemaMappingGatePreSemanticRequest) =>
    aiRequest<SchemaMappingStatusResponse, SchemaMappingGatePreSemanticRequest>({
      method: "POST",
      basePath: "/api/schema-mapping",
      path: `/${encodeURIComponent(schemaMappingId)}/gate/pre-semantic`,
      body,
    }),

  gateSchemaMappingHierarchy: (schemaMappingId: string, body: SchemaMappingGateHierarchyRequest) =>
    aiRequest<SchemaMappingStatusResponse, SchemaMappingGateHierarchyRequest>({
      method: "POST",
      basePath: "/api/schema-mapping",
      path: `/${encodeURIComponent(schemaMappingId)}/gate/hierarchy`,
      body,
    }),

  gateSchemaMappingArtifactsReview: (schemaMappingId: string, body: SchemaMappingGateArtifactsReviewRequest) =>
    aiRequest<SchemaMappingStatusResponse, SchemaMappingGateArtifactsReviewRequest>({
      method: "POST",
      basePath: "/api/schema-mapping",
      path: `/${encodeURIComponent(schemaMappingId)}/gate/artifacts-review`,
      body,
    }),

  startMigrationWithUpload: (body: MigrationStartUploadRequest) => {
    const form = new FormData();
    form.set("file", body.file);
    form.set("cmms_name", body.cmms_name);
    form.set("organization_id", body.organization_id);
    return aiRequest<MigrationStartUploadResponse, FormData>({
      method: "POST",
      basePath: "/api/migration",
      path: "/start-with-upload",
      body: form,
    });
  },

  getMigrationStatus: (migrationId: string) =>
    aiRequest<MigrationStatusResponse>({
      method: "GET",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/status`,
      // Hoistra: CAFM Web's schema-mapper returns every node's output and the UDR reports on
      // /status; Hoistra's omits any over 16 KB unless asked. These screens build their
      // completed-steps history, the ingest report and the Table & Column Analysis from them,
      // so the full record is asked for — the response CAFM Web's screens were written against.
      query: { include_output: true },
    }),

  // ── Feature 4 — Activity Log (Section 1) ──────────────────────────────────
  listActivity: (params?: { organizationId?: string; sessionId?: string; limit?: number }) =>
    aiRequest<ActivitySummaryEntry[]>({
      method: "GET",
      basePath: "/api",
      path: "/activity-log",
      query: {
        organization_id: params?.organizationId,
        session_id: params?.sessionId,
        limit: params?.limit,
      },
    }),
  getActivityUnread: (params?: { organizationId?: string }) =>
    aiRequest<ActivityUnreadSummary>({
      method: "GET",
      basePath: "/api",
      path: "/activity-log/unread",
      query: { organization_id: params?.organizationId },
    }),
  markActivityRead: (id: string) =>
    aiRequest<{ ok: boolean }>({
      method: "POST",
      basePath: "/api",
      path: `/activity-log/${encodeURIComponent(id)}/read`,
    }),
  getActivity: (id: string) =>
    aiRequest<ActivityDetailEntry>({
      method: "GET",
      basePath: "/api",
      path: `/activity-log/${encodeURIComponent(id)}`,
    }),
  listActivityActions: (entryId: string) =>
    aiRequest<ActivityActionItem[]>({
      method: "GET",
      basePath: "/api",
      path: `/activity-log/${encodeURIComponent(entryId)}/actions`,
    }),
  resolveActivityAction: (actionId: string, body: { option_id: string; note?: string }) =>
    aiRequest<{ ok: boolean; action: ActivityActionItem }, { option_id: string; note?: string }>({
      method: "POST",
      basePath: "/api",
      path: `/activity-actions/${encodeURIComponent(actionId)}/resolve`,
      body,
    }),
  getEntityWorkCloud: (
    entityType: string,
    entityId: string,
    params?: { runId?: string; organizationId?: string },
  ) =>
    aiRequest<EntityWorkCloud>({
      method: "GET",
      basePath: "/api",
      path: `/entity/${encodeURIComponent(entityType)}/${encodeURIComponent(entityId)}/relationships`,
      query: { run_id: params?.runId, organization_id: params?.organizationId },
    }),
  getEntityInstanceCloud: (
    entityType: string,
    entityId: string,
    params?: { runId?: string; organizationId?: string; limit?: number },
  ) =>
    aiRequest<EntityInstanceCloud>({
      method: "GET",
      basePath: "/api",
      path: `/entity/${encodeURIComponent(entityType)}/${encodeURIComponent(entityId)}/instances`,
      query: { run_id: params?.runId, organization_id: params?.organizationId, limit: params?.limit },
    }),

  gateFieldMapping: (migrationId: string, body: MigrationGateFieldMappingRequest) =>
    aiRequest<MigrationStatusResponse, MigrationGateFieldMappingRequest>({
      method: "POST",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/gate/field-mapping`,
      body,
    }),

  getFieldMappingDraft: (migrationId: string) =>
    aiRequest<{ migration_id: string; draft: MigrationFieldMappingDraftEnvelope | null }>({
      method: "GET",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/gate/field-mapping/draft`,
    }),

  putFieldMappingDraft: (migrationId: string, draft: MigrationFieldMappingDraftEnvelope) =>
    aiRequest<
      { migration_id: string; draft: MigrationFieldMappingDraftEnvelope | null },
      { draft: MigrationFieldMappingDraftEnvelope }
    >({
      method: "PUT",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/gate/field-mapping/draft`,
      body: { draft },
    }),

  canonicalFieldScores: (body: {
    source_field: string;
    field_description?: string | null;
    sample_values?: string[];
    canonical_fields: string[];
  }) =>
    aiRequest<{ scores: Record<string, number> }>({
      method: "POST",
      basePath: "/api/migration",
      path: "/canonical-field-scores",
      body,
    }),

  deleteFieldMappingDraft: (migrationId: string) =>
    aiRequest<{ migration_id: string; status: string }>({
      method: "DELETE",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/gate/field-mapping/draft`,
    }),

  gateHierarchy: (migrationId: string, body: MigrationGateHierarchyRequest) =>
    aiRequest<MigrationStatusResponse, MigrationGateHierarchyRequest>({
      method: "POST",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/gate/hierarchy`,
      body,
    }),

  gatePreSemantic: (migrationId: string, body: MigrationGatePreSemanticRequest) =>
    aiRequest<MigrationStatusResponse, MigrationGatePreSemanticRequest>({
      method: "POST",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/gate/pre-semantic`,
      body,
    }),

  gatePkApproval: (migrationId: string, body: MigrationGatePkApprovalRequest) =>
    aiRequest<MigrationStatusResponse, MigrationGatePkApprovalRequest>({
      method: "POST",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/gate/pk-approval`,
      body,
    }),

  gateUniqueTableApproval: (migrationId: string, body: MigrationGateUniqueTableApprovalRequest) =>
    aiRequest<MigrationStatusResponse, MigrationGateUniqueTableApprovalRequest>({
      method: "POST",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/gate/unique-table-approval`,
      body,
    }),

  gateColumnMappingApproval: (migrationId: string, body: MigrationGateColumnMappingApprovalRequest) =>
    aiRequest<MigrationStatusResponse, MigrationGateColumnMappingApprovalRequest>({
      method: "POST",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/gate/column-mapping-approval`,
      body,
    }),

  gateClassificationApproval: (migrationId: string, body: MigrationGateClassificationApprovalRequest) =>
    aiRequest<MigrationStatusResponse, MigrationGateClassificationApprovalRequest>({
      method: "POST",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/gate/classification-approval`,
      body,
    }),

  gateFinal: (migrationId: string, body: MigrationGateFinalRequest) =>
    aiRequest<MigrationStatusResponse, MigrationGateFinalRequest>({
      method: "POST",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/gate/final`,
      body,
    }),

  /** Read column_canonical + table_routing from the migration's CURRENT state.
   *  Lets the Tier-2 gate hydrate canonical names from the column-intelligence
   *  result even when the paused gate's payload was constructed before
   *  column_canonical was added (older paused migrations). */
  getMigrationStateCanonicals: (migrationId: string) =>
    aiRequest<{
      migration_id: string;
      column_canonical: Record<string, string>;
      table_routing: Record<string, string>;
      /** Compact canonical groups (group → canonical name → members + pin flag) so Tier-2 can
       *  show the "Unified column names" decisions made earlier as a read-only summary. */
      canonical_groups?: Array<{
        group_id?: string;
        canonical_name?: string;
        members?: string[];
        pinned?: boolean;
      }>;
    }>({
      method: "GET",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/state-canonicals`,
    }),

  /** Re-run build_column_intelligence with the supplied canonical overrides against
   *  the currently-paused pre-semantic gate state. Returns ONLY the updated
   *  column_intelligence payload — does NOT advance the gate. Used by the
   *  'Re-match now' button on the pin banner so B14/B19/B20/B21 refresh instantly. */
  previewCanonicalOverrides: (
    migrationId: string,
    body: { canonical_overrides: Record<string, string>; table_overrides?: Record<string, string> },
  ) =>
    aiRequest<
      {
        migration_id: string;
        column_intelligence: UdrColumnIntelligence;
        applied_overrides: Record<string, string>;
        applied_table_overrides?: Record<string, string>;
      },
      { canonical_overrides: Record<string, string>; table_overrides?: Record<string, string> }
    >({
      method: "POST",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/preview-canonical-overrides`,
      body,
    }),

  advanceMigration: (migrationId: string) =>
    aiRequest<MigrationStatusResponse>({
      method: "POST",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/advance`,
      body: {},
    }),

  rerunMigrationFromNode: (migrationId: string, nodeNum: number) =>
    aiRequest<MigrationStatusResponse>({
      method: "POST",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/rerun-from/${nodeNum}`,
      body: {},
    }),

  retryMigrationDdl: (migrationId: string, body: MigrationRetryDdlRequest) =>
    aiRequest<MigrationStatusResponse, MigrationRetryDdlRequest>({
      method: "POST",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/retry-ddl`,
      body,
    }),

  getMigrationAudit: (migrationId: string) =>
    aiRequest<unknown>({
      method: "GET",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/audit`,
    }),

  // F7 7.9-AC6 — promote shared attributes to reference-table PKs and re-run Test 1.
  test1FixAndRetest: (migrationId: string) =>
    aiRequest<UdrTestResults>({
      method: "POST",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/udr/test1/fix-and-retest`,
      body: {},
    }),

  // F7 7.10-AC6/AC7 — apply Test-2 flagged-pair resolutions and re-run Test 2.
  test2Resolve: (migrationId: string, body: UdrTest2ResolveRequest) =>
    aiRequest<UdrTestResults, UdrTest2ResolveRequest>({
      method: "POST",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/udr/test2/resolve`,
      body,
    }),

  testConnectionWithCredentials: (platform: FiixPlatform, body: FiixCredentials) =>
    aiRequest<unknown, FiixCredentials>({
      method: "POST",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: `/platforms/${platform}/test-connection`,
      body,
    }),

  fetchSchema: (platform: FiixPlatform) =>
    aiRequest<unknown>({
      method: "GET",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: `/platforms/${platform}/fetch-schema`,
    }),

  testingUpload: (body: FormData) =>
    aiRequest<unknown, FormData>({
      method: "POST",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: "/testing/upload",
      body,
    }),

  testingIngestWithMapper: (body: FormData) =>
    aiRequest<unknown, FormData>({
      method: "POST",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: "/testing/ingest-with-mapper",
      body,
    }),

  testingIngestWithSemantic: (body: FormData) =>
    aiRequest<unknown, FormData>({
      method: "POST",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: "/testing/ingest-with-semantic",
      body,
    }),

  testingHumanReview: <TBody = unknown>(body: TBody) =>
    aiRequest<unknown, TBody>({
      method: "POST",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: "/testing/human-review",
      body,
    }),

  testingHumanReviewNode4: (body: Node4HumanReviewRequest) =>
    aiRequest<Node4HumanReviewResponse, Node4HumanReviewRequest>({
      method: "POST",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: "/testing/human-review",
      body,
    }),

  testingPreprocess: <TBody = unknown>(body: TBody) =>
    aiRequest<unknown, TBody>({
      method: "POST",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: "/testing/preprocess",
      body,
    }),

  testingPreprocessNode5: (body: Node5PreprocessRequest) =>
    aiRequest<Node5PreprocessResponse, Node5PreprocessRequest>({
      method: "POST",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: "/testing/preprocess",
      body,
    }),

  testingResolveHierarchy: <TBody = unknown>(body: TBody) =>
    aiRequest<unknown, TBody>({
      method: "POST",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: "/testing/resolve-hierarchy",
      body,
    }),

  testingResolveHierarchyNode6: (body: Node6ResolveHierarchyRequest) =>
    aiRequest<Node6ResolveHierarchyResponse, Node6ResolveHierarchyRequest>({
      method: "POST",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: "/testing/resolve-hierarchy",
      body,
    }),

  testingVerifyHierarchy: <TBody = unknown>(body: TBody) =>
    aiRequest<unknown, TBody>({
      method: "POST",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: "/testing/verify-hierarchy",
      body,
    }),

  testingVerifyHierarchyNode7: (body: Node7VerifyHierarchyRequest) =>
    aiRequest<Node7VerifyHierarchyResponse, Node7VerifyHierarchyRequest>({
      method: "POST",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: "/testing/verify-hierarchy",
      body,
    }),

  testingGenerateOutput: <TBody = unknown>(body: TBody) =>
    aiRequest<unknown, TBody>({
      method: "POST",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: "/testing/generate-output",
      body,
    }),

  testingGenerateOutputNode8: (body: Node8GenerateOutputRequest) =>
    aiRequest<Node8GenerateOutputResponse, Node8GenerateOutputRequest>({
      method: "POST",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: "/testing/generate-output",
      body,
    }),

  testingWriteOutput: <TBody = unknown>(body: TBody) =>
    aiRequest<unknown, TBody>({
      method: "POST",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: "/testing/write-output",
      body,
    }),

  testingWriteOutputNode9: (body: Node9WriteOutputRequest) =>
    aiRequest<Node9WriteOutputResponse, Node9WriteOutputRequest>({
      method: "POST",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: "/testing/write-output",
      body,
    }),

  testingArtifactsByMigrationId: (migrationId: string) =>
    aiRequest<unknown>({
      method: "GET",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: `/testing/artifacts/${encodeURIComponent(migrationId)}`,
    }),

  testingDownloadArtifact: (params: TestingArtifactParams) =>
    aiRequest<string>({
      method: "GET",
      basePath: SCHEMA_MAPPER_API_BASE_PATH,
      path: `/testing/artifacts/${encodeURIComponent(params.migrationId)}/${encodeURIComponent(params.filename)}`,
    }),

  // ── Migration: list / delete / read-only ────────────────────────────────────

  listMigrations: (params: ListMigrationsParams = {}) =>
    aiRequest<MigrationListResponse>({
      method: "GET",
      basePath: "/api/migration",
      query: {
        organization_id: params.organization_id,
        status: params.status,
        limit: params.limit,
        offset: params.offset,
      },
    }),

  deleteMigration: (migrationId: string) =>
    aiRequest<{ status: string; message: string }>({
      method: "DELETE",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}`,
    }),

  getMigrationMappings: (migrationId: string, tier?: string) =>
    aiRequest<MappingListResponse>({
      method: "GET",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/mappings`,
      query: { tier },
    }),

  getMigrationHierarchy: (migrationId: string) =>
    aiRequest<unknown>({
      method: "GET",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/hierarchy`,
    }),

  getMigrationDownload: (migrationId: string, format: MigrationDownloadFormat) =>
    aiRequest<MigrationDownloadResponse>({
      method: "GET",
      basePath: "/api/migration",
      path: `/${encodeURIComponent(migrationId)}/download/${encodeURIComponent(format)}`,
    }),

  // ── Schema Mapping: list / delete / read-only ────────────────────────────────

  listSchemaMappings: (params: ListSchemaMappingsParams = {}) =>
    aiRequest<SchemaMappingListResponse>({
      method: "GET",
      basePath: "/api/schema-mapping",
      query: {
        organization_id: params.organization_id,
        status: params.status,
        limit: params.limit,
        offset: params.offset,
      },
    }),

  deleteSchemaMapping: (schemaMappingId: string) =>
    aiRequest<{ status: string; message: string }>({
      method: "DELETE",
      basePath: "/api/schema-mapping",
      path: `/${encodeURIComponent(schemaMappingId)}`,
    }),

  getSchemaMappings: (schemaMappingId: string, tier?: string) =>
    aiRequest<MappingListResponse>({
      method: "GET",
      basePath: "/api/schema-mapping",
      path: `/${encodeURIComponent(schemaMappingId)}/mappings`,
      query: { tier },
    }),

  getSchemaUnmapped: (schemaMappingId: string) =>
    aiRequest<SchemaUnmappedResponse>({
      method: "GET",
      basePath: "/api/schema-mapping",
      path: `/${encodeURIComponent(schemaMappingId)}/unmapped`,
    }),

  getSchemaMappingAuditTrail: (schemaMappingId: string) =>
    aiRequest<MappingListResponse | SchemaAuditEntry[]>({
      method: "GET",
      basePath: "/api/schema-mapping",
      path: `/${encodeURIComponent(schemaMappingId)}/audit-trail`,
    }),

  submitSchemaCustomMapping: (schemaMappingId: string, body: SchemaCustomMappingRequest) =>
    aiRequest<SchemaCustomMappingResponse, SchemaCustomMappingRequest>({
      method: "POST",
      basePath: "/api/schema-mapping",
      path: `/${encodeURIComponent(schemaMappingId)}/custom-mapping`,
      body,
    }),

  retrySchemaMappingDdl: (schemaMappingId: string, body: { extra_fields_config: ExtraFieldConfig[] }) =>
    aiRequest<SchemaMappingStatusResponse, { extra_fields_config: ExtraFieldConfig[] }>({
      method: "POST",
      basePath: "/api/schema-mapping",
      path: `/${encodeURIComponent(schemaMappingId)}/retry-ddl`,
      body,
    }),
};

export function useSchemaMapperTestConnection(
  mutationOptions?: UseMutationOptions<unknown, unknown, { platform: FiixPlatform }>,
) {
  return useMutation<unknown, unknown, { platform: FiixPlatform }>({
    mutationFn: ({ platform }) => schemaMapperApi.testConnection(platform),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperTestConnectionWithCredentials(
  mutationOptions?: UseMutationOptions<unknown, unknown, { platform: FiixPlatform; body: FiixCredentials }>,
) {
  return useMutation<unknown, unknown, { platform: FiixPlatform; body: FiixCredentials }>({
    mutationFn: ({ platform, body }) => schemaMapperApi.testConnectionWithCredentials(platform, body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperFetchSchema(
  opts: { platform: FiixPlatform; enabled?: boolean },
  queryOptions?: Omit<UseQueryOptions<unknown, unknown>, "queryKey" | "queryFn" | "enabled">,
) {
  return useQuery<unknown, unknown>({
    queryKey: ["schema-mapper", "fetch-schema", opts.platform],
    enabled: opts.enabled ?? true,
    queryFn: ({ signal }) =>
      aiRequest<unknown>({
        method: "GET",
        basePath: SCHEMA_MAPPER_API_BASE_PATH,
        path: `/platforms/${opts.platform}/fetch-schema`,
        signal,
      }),
    ...(queryOptions ?? {}),
  });
}

export function useSchemaMapperTestingUpload(
  mutationOptions?: UseMutationOptions<unknown, unknown, FormData>,
) {
  return useMutation<unknown, unknown, FormData>({
    mutationFn: (body) => schemaMapperApi.testingUpload(body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperTestingIngestWithMapper(
  mutationOptions?: UseMutationOptions<unknown, unknown, FormData>,
) {
  return useMutation<unknown, unknown, FormData>({
    mutationFn: (body) => schemaMapperApi.testingIngestWithMapper(body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperTestingIngestWithSemantic(
  mutationOptions?: UseMutationOptions<unknown, unknown, FormData>,
) {
  return useMutation<unknown, unknown, FormData>({
    mutationFn: (body) => schemaMapperApi.testingIngestWithSemantic(body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperTestingHumanReview<TBody = unknown>(
  mutationOptions?: UseMutationOptions<unknown, unknown, TBody>,
) {
  return useMutation<unknown, unknown, TBody>({
    mutationFn: (body) => schemaMapperApi.testingHumanReview<TBody>(body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperTestingHumanReviewNode4(
  mutationOptions?: UseMutationOptions<Node4HumanReviewResponse, unknown, Node4HumanReviewRequest>,
) {
  return useMutation<Node4HumanReviewResponse, unknown, Node4HumanReviewRequest>({
    mutationFn: (body) => schemaMapperApi.testingHumanReviewNode4(body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperTestingPreprocess<TBody = unknown>(
  mutationOptions?: UseMutationOptions<unknown, unknown, TBody>,
) {
  return useMutation<unknown, unknown, TBody>({
    mutationFn: (body) => schemaMapperApi.testingPreprocess<TBody>(body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperTestingPreprocessNode5(
  mutationOptions?: UseMutationOptions<Node5PreprocessResponse, unknown, Node5PreprocessRequest>,
) {
  return useMutation<Node5PreprocessResponse, unknown, Node5PreprocessRequest>({
    mutationFn: (body) => schemaMapperApi.testingPreprocessNode5(body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperTestingResolveHierarchy<TBody = unknown>(
  mutationOptions?: UseMutationOptions<unknown, unknown, TBody>,
) {
  return useMutation<unknown, unknown, TBody>({
    mutationFn: (body) => schemaMapperApi.testingResolveHierarchy<TBody>(body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperTestingResolveHierarchyNode6(
  mutationOptions?: UseMutationOptions<Node6ResolveHierarchyResponse, unknown, Node6ResolveHierarchyRequest>,
) {
  return useMutation<Node6ResolveHierarchyResponse, unknown, Node6ResolveHierarchyRequest>({
    mutationFn: (body) => schemaMapperApi.testingResolveHierarchyNode6(body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperTestingVerifyHierarchy<TBody = unknown>(
  mutationOptions?: UseMutationOptions<unknown, unknown, TBody>,
) {
  return useMutation<unknown, unknown, TBody>({
    mutationFn: (body) => schemaMapperApi.testingVerifyHierarchy<TBody>(body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperTestingVerifyHierarchyNode7(
  mutationOptions?: UseMutationOptions<Node7VerifyHierarchyResponse, unknown, Node7VerifyHierarchyRequest>,
) {
  return useMutation<Node7VerifyHierarchyResponse, unknown, Node7VerifyHierarchyRequest>({
    mutationFn: (body) => schemaMapperApi.testingVerifyHierarchyNode7(body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperTestingGenerateOutput<TBody = unknown>(
  mutationOptions?: UseMutationOptions<unknown, unknown, TBody>,
) {
  return useMutation<unknown, unknown, TBody>({
    mutationFn: (body) => schemaMapperApi.testingGenerateOutput<TBody>(body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperTestingGenerateOutputNode8(
  mutationOptions?: UseMutationOptions<Node8GenerateOutputResponse, unknown, Node8GenerateOutputRequest>,
) {
  return useMutation<Node8GenerateOutputResponse, unknown, Node8GenerateOutputRequest>({
    mutationFn: (body) => schemaMapperApi.testingGenerateOutputNode8(body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperTestingWriteOutput<TBody = unknown>(
  mutationOptions?: UseMutationOptions<unknown, unknown, TBody>,
) {
  return useMutation<unknown, unknown, TBody>({
    mutationFn: (body) => schemaMapperApi.testingWriteOutput<TBody>(body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperTestingWriteOutputNode9(
  mutationOptions?: UseMutationOptions<Node9WriteOutputResponse, unknown, Node9WriteOutputRequest>,
) {
  return useMutation<Node9WriteOutputResponse, unknown, Node9WriteOutputRequest>({
    mutationFn: (body) => schemaMapperApi.testingWriteOutputNode9(body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMapperTestingDownloadArtifact(
  opts: { migrationId: string; filename: string; enabled?: boolean },
  queryOptions?: Omit<UseQueryOptions<string, unknown>, "queryKey" | "queryFn" | "enabled">,
) {
  return useQuery<string, unknown>({
    queryKey: ["schema-mapper", "testing", "artifact", opts.migrationId, opts.filename],
    enabled: opts.enabled ?? true,
    queryFn: ({ signal }) =>
      aiRequest<string>({
        method: "GET",
        basePath: SCHEMA_MAPPER_API_BASE_PATH,
        path: `/testing/artifacts/${encodeURIComponent(opts.migrationId)}/${encodeURIComponent(opts.filename)}`,
        signal,
      }),
    ...(queryOptions ?? {}),
  });
}

export function useAiQuery<TResponse = unknown>(
  key: unknown[],
  opts: Omit<AiRequestOptions<never>, "method" | "body"> & { enabled?: boolean },
  queryOptions?: Omit<UseQueryOptions<TResponse, unknown>, "queryKey" | "queryFn" | "enabled">,
) {
  return useQuery<TResponse, unknown>({
    queryKey: key,
    enabled: opts.enabled ?? true,
    queryFn: ({ signal }) => aiRequest<TResponse>({ ...opts, method: "GET", signal }),
    ...(queryOptions ?? {}),
  });
}

export function useAiMutation<TResponse = unknown, TBody = unknown>(
  mutationOptions?: UseMutationOptions<TResponse, unknown, AiRequestOptions<TBody>>,
) {
  return useMutation<TResponse, unknown, AiRequestOptions<TBody>>({
    mutationFn: (opts) => aiRequest<TResponse, TBody>(opts),
    ...(mutationOptions ?? {}),
  });
}

export function useMigrationStartUpload(
  mutationOptions?: UseMutationOptions<MigrationStartUploadResponse, unknown, MigrationStartUploadRequest>,
) {
  return useMutation<MigrationStartUploadResponse, unknown, MigrationStartUploadRequest>({
    mutationFn: (body) => schemaMapperApi.startMigrationWithUpload(body),
    ...(mutationOptions ?? {}),
  });
}

function shouldPauseMigrationPollingForImplicitGate(data: MigrationStatusResponse | undefined) {
  if (!data) return false;
  const st = typeof data.status === "string" ? data.status.toLowerCase() : "";
  if (st !== "running") return false;
  if (data.pending_gate_type || data.pending_gate_payload) return false;
  // Keep polling through pre-semantic / semantic stages until awaiting_review gate is written.
  if (typeof data.current_step === "number" && data.current_step > 0 && data.current_step < 5) {
    return false;
  }

  const nodes = Array.isArray(data.nodes) ? data.nodes : [];
  if (!nodes.length) return false;

  const hasActiveGateNode = nodes.some((n) => {
    const name = String(n.node_name ?? "").toLowerCase();
    const nodeStatus = String(n.status ?? "").toLowerCase();
    const isGateName = name.includes("gate") || name.includes("review");
    const isNotCompleted =
      nodeStatus === "running" ||
      nodeStatus === "pending" ||
      (n.started_at != null && n.completed_at == null);
    return isGateName && isNotCompleted;
  });
  if (!hasActiveGateNode) return false;

  const hasReviewSignals = nodes.some((n) =>
    (n.logs ?? []).some((line) => {
      const s = String(line).toLowerCase();
      return (
        s.includes("no matches for") ||
        s.includes("unmappable") ||
        s.includes("unresolved") ||
        s.includes("human review") ||
        s.includes("table structure")
      );
    }),
  );

  const progressedToGateStage =
    (typeof data.current_step === "number" && data.current_step >= 5) ||
    nodes.some((n) => {
      const nodeStatus = String(n.status ?? "").toLowerCase();
      const isCompleted = nodeStatus === "complete" || nodeStatus === "completed" || nodeStatus === "done";
      return n.node_id >= 4 && isCompleted;
    }) ||
    nodes.some((n) => {
      const name = String(n.node_name ?? "").toLowerCase();
      const isGateName = name.includes("gate") || name.includes("review");
      return isGateName && (!!n.output || (n.logs?.length ?? 0) > 0);
    });

  if (!progressedToGateStage) return false;
  return hasActiveGateNode || hasReviewSignals;
}

// ── Feature 4 — Activity Log hooks. Graceful when the backend endpoint isn't up
//    yet (return empty/zero rather than surfacing an error state). ──
export function useActivityUnread(opts?: {
  organizationId?: string;
  enabled?: boolean;
  refetchInterval?: number;
}) {
  return useQuery<ActivityUnreadSummary>({
    queryKey: ["activity-log", "unread", opts?.organizationId ?? ""],
    queryFn: async () => {
      try {
        return await schemaMapperApi.getActivityUnread({ organizationId: opts?.organizationId });
      } catch {
        return { count: 0, worst_color: null };
      }
    },
    // org scope is mandatory — without it the badge stays empty (no cross-tenant scan).
    enabled: (opts?.enabled ?? true) && !!opts?.organizationId,
    refetchInterval: opts?.refetchInterval ?? 15000,
    retry: false,
  });
}

export function useActivityLog(params?: {
  organizationId?: string;
  sessionId?: string;
  limit?: number;
  enabled?: boolean;
  /** Poll interval (ms) — set by the Activity Log panel so the backend-owned per-run row's
   *  status transitions appear live without a manual refresh. Omitted = no polling. */
  refetchInterval?: number;
}) {
  return useQuery<ActivitySummaryEntry[]>({
    queryKey: [
      "activity-log",
      "list",
      params?.organizationId ?? "",
      params?.sessionId ?? "",
      params?.limit ?? 50,
    ],
    queryFn: async () => {
      // Do NOT swallow failures into []: a caught error returned as an empty array looks like a
      // successful empty response to React Query, which then REPLACES the good cache with empty and
      // the rendered logs vanish. Letting it throw makes React Query keep the last successful data
      // (stale) + surface the error, so a transient API failure/stall never wipes the logs; the
      // poll interval retries it. Empty is only ever a genuine "no activity" response now.
      return await schemaMapperApi.listActivity({
        organizationId: params?.organizationId,
        sessionId: params?.sessionId,
        limit: params?.limit,
      });
    },
    // require a scope (org or session) — matches the backend's no-unscoped-list guard.
    enabled: (params?.enabled ?? true) && (!!params?.organizationId || !!params?.sessionId),
    refetchInterval: params?.refetchInterval,
    retry: 2,
  });
}

export function useActivity(id: string | null, opts?: { enabled?: boolean; refetchInterval?: number }) {
  return useQuery<ActivityDetailEntry | null>({
    queryKey: ["activity-log", "detail", id ?? ""],
    queryFn: async () => {
      if (!id) return null;
      // Don't swallow failures into null — that would blank the Processing Log (Section 2) on a
      // single stalled poll. Throw so React Query keeps the last good detail (stale) + retries.
      return await schemaMapperApi.getActivity(id);
    },
    enabled: (opts?.enabled ?? true) && !!id,
    // Poll while an in-flight run row is open so the Processing-Log (Section 2) keeps pace
    // with the backend appending stages — without it the timeline freezes at its first fetch
    // while the summary list (which polls) advances the stage count. Omitted = no polling.
    refetchInterval: opts?.refetchInterval,
    retry: 2,
  });
}

export function useActivityActions(
  entryId: string | null,
  opts?: { enabled?: boolean; refetchInterval?: number },
) {
  return useQuery<ActivityActionItem[]>({
    queryKey: ["activity-log", "actions", entryId ?? ""],
    queryFn: async () => {
      if (!entryId) return [];
      // Don't swallow failures into [] — that would drop the Section-3 HITL cards on a single
      // stalled poll. Throw so React Query keeps the last good actions (stale) + retries.
      return await schemaMapperApi.listActivityActions(entryId);
    },
    enabled: (opts?.enabled ?? true) && !!entryId,
    refetchInterval: opts?.refetchInterval,
    retry: 2,
  });
}

export function useResolveActivityAction() {
  const qc = useQueryClient();
  return useMutation<
    { ok: boolean; action: ActivityActionItem },
    unknown,
    { actionId: string; optionId: string; note?: string }
  >({
    mutationFn: ({ actionId, optionId, note }) =>
      schemaMapperApi.resolveActivityAction(actionId, { option_id: optionId, note }),
    onSuccess: () => {
      // refresh the inline actions + the entry feed + the unread badge after a decision
      void qc.invalidateQueries({ queryKey: ["activity-log"] });
    },
  });
}

export function useEntityWorkCloud(
  entityType: string | null,
  entityId: string | null,
  opts?: { runId?: string; organizationId?: string; enabled?: boolean },
) {
  return useQuery<EntityWorkCloud | null>({
    queryKey: [
      "udr",
      "work-cloud",
      entityType ?? "",
      entityId ?? "",
      opts?.runId ?? "",
      opts?.organizationId ?? "",
    ],
    queryFn: async () => {
      if (!entityType || !entityId) return null;
      try {
        return await schemaMapperApi.getEntityWorkCloud(entityType, entityId, {
          runId: opts?.runId,
          organizationId: opts?.organizationId,
        });
      } catch {
        return null;
      }
    },
    enabled: (opts?.enabled ?? true) && !!entityType && !!entityId,
    retry: false,
  });
}

export function useEntityInstanceCloud(
  entityType: string | null,
  entityId: string | null,
  opts?: { runId?: string; organizationId?: string; limit?: number; enabled?: boolean },
) {
  return useQuery<EntityInstanceCloud | null>({
    queryKey: [
      "udr",
      "instance-cloud",
      entityType ?? "",
      entityId ?? "",
      opts?.runId ?? "",
      opts?.organizationId ?? "",
    ],
    queryFn: async () => {
      if (!entityType || !entityId) return null;
      try {
        return await schemaMapperApi.getEntityInstanceCloud(entityType, entityId, {
          runId: opts?.runId,
          organizationId: opts?.organizationId,
          limit: opts?.limit,
        });
      } catch {
        return null;
      }
    },
    enabled: (opts?.enabled ?? true) && !!entityType && !!entityId,
    retry: false,
  });
}

/**
 * Single source of truth for the live migration poll cadence. The Migration Panel status poll AND
 * the Activity Log feed/detail/actions polls all use this, so the two surfaces update in LOCKSTEP
 * (they were 2000 / 3000 / 2500 ms before — hence the "log updates a beat before/after the panel"
 * drift). Matches the "Auto-refreshing every 2 seconds…" copy shown in the panel.
 */
export const MIGRATION_POLL_INTERVAL_MS = 2000;

export function useMigrationStatus(
  migrationId: string,
  opts?: {
    enabled?: boolean;
    refetchInterval?: number;
    forceUntil?: number;
    /** Orchestrator rail — never stop polling while status is running (until gate/step_paused). */
    keepPollingWhileRunning?: boolean;
  },
  queryOptions?: Omit<UseQueryOptions<MigrationStatusResponse, unknown>, "queryKey" | "queryFn" | "enabled">,
) {
  return useQuery<MigrationStatusResponse, unknown>({
    queryKey: ["migration", "status", migrationId],
    enabled: opts?.enabled ?? true,
    queryFn: () => schemaMapperApi.getMigrationStatus(migrationId),
    refetchInterval: (q) => {
      const d = q.state.data as MigrationStatusResponse | undefined;
      const st = typeof d?.status === "string" ? d.status.toLowerCase() : "";
      const shouldForce = typeof opts?.forceUntil === "number" && Date.now() < opts.forceUntil;
      if (
        st === "complete" ||
        st === "error" ||
        st === "failed" ||
        st === "ddl_failed" ||
        st === "cancelled" ||
        st === "canceled"
      )
        return false;
      // Backend can emit step_5_preprocess (or running+preprocess payload) before field_mapping gate is ready.
      if (shouldKeepPollingForFieldMappingGate(d)) return opts?.refetchInterval ?? MIGRATION_POLL_INTERVAL_MS;
      if (st === "step_paused" && !shouldKeepPollingForFieldMappingGate(d)) {
        if (shouldForce) return opts?.refetchInterval ?? MIGRATION_POLL_INTERVAL_MS;
        return false;
      }
      if (st === "awaiting_review") {
        const pending = String(d?.pending_gate_type ?? "").toLowerCase();
        const readyForFieldMappingSubmit =
          pending === "field_mapping" ||
          (pending.includes("field") && pending.includes("map")) ||
          (pending.includes("human") && pending.includes("review"));
        if (!readyForFieldMappingSubmit) return opts?.refetchInterval ?? MIGRATION_POLL_INTERVAL_MS;
        // Ready-for-field-mapping normally stops polling (we're waiting on the user). BUT when a
        // decision was JUST submitted the caller opens a forceUntil window — keep polling then so
        // we catch the backend advancing past the gate. Without this the panel froze on "Pending
        // human input" after submit and only recovered on a manual page refresh.
        if (shouldForce) return opts?.refetchInterval ?? MIGRATION_POLL_INTERVAL_MS;
        return false;
      }
      if ((st.includes("paused") || st.includes("review")) && !shouldKeepPollingForFieldMappingGate(d)) {
        if (shouldForce) return opts?.refetchInterval ?? MIGRATION_POLL_INTERVAL_MS;
        return false;
      }
      if (
        !shouldForce &&
        !opts?.keepPollingWhileRunning &&
        shouldPauseMigrationPollingForImplicitGate(d)
      ) {
        return false;
      }
      return opts?.refetchInterval ?? MIGRATION_POLL_INTERVAL_MS;
    },
    ...(queryOptions ?? {}),
  });
}

export function useSchemaMappingStart(
  mutationOptions?: UseMutationOptions<SchemaMappingStartResponse, unknown, SchemaMappingStartRequest>,
) {
  return useMutation<SchemaMappingStartResponse, unknown, SchemaMappingStartRequest>({
    mutationFn: (body) => schemaMapperApi.startSchemaMappingSession(body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMappingStatus(
  schemaMappingId: string,
  opts?: { enabled?: boolean; refetchInterval?: number; forceUntil?: number },
  queryOptions?: Omit<UseQueryOptions<SchemaMappingStatusResponse, unknown>, "queryKey" | "queryFn" | "enabled">,
) {
  const baseInterval = opts?.refetchInterval ?? 3000;
  return useQuery<SchemaMappingStatusResponse, unknown>({
    queryKey: ["schema-mapping", "status", schemaMappingId],
    enabled: opts?.enabled ?? true,
    staleTime: 0,
    refetchOnWindowFocus: true,
    queryFn: () => schemaMapperApi.getSchemaMappingStatus(schemaMappingId),
    refetchInterval: (q) => {
      const d = q.state.data as SchemaMappingStatusResponse | undefined;
      if (!schemaMappingStatusNeedsPoll(d, { forceUntil: opts?.forceUntil })) return false;
      return schemaMappingPollIntervalMs(d, baseInterval);
    },
    ...(queryOptions ?? {}),
  });
}

export function useSchemaMappingAdvance(
  mutationOptions?: UseMutationOptions<SchemaMappingStatusResponse, unknown, { schemaMappingId: string }>,
) {
  return useMutation<SchemaMappingStatusResponse, unknown, { schemaMappingId: string }>({
    mutationFn: ({ schemaMappingId }) => schemaMapperApi.advanceSchemaMapping(schemaMappingId),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMappingGateFieldMapping(
  mutationOptions?: UseMutationOptions<
    SchemaMappingStatusResponse,
    unknown,
    { schemaMappingId: string; body: SchemaMappingGateFieldMappingRequest }
  >,
) {
  return useMutation<
    SchemaMappingStatusResponse,
    unknown,
    { schemaMappingId: string; body: SchemaMappingGateFieldMappingRequest }
  >({
    mutationFn: ({ schemaMappingId, body }) => schemaMapperApi.gateSchemaMappingFieldMapping(schemaMappingId, body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMappingGatePreSemantic(
  mutationOptions?: UseMutationOptions<
    SchemaMappingStatusResponse,
    unknown,
    { schemaMappingId: string; body: SchemaMappingGatePreSemanticRequest }
  >,
) {
  return useMutation<
    SchemaMappingStatusResponse,
    unknown,
    { schemaMappingId: string; body: SchemaMappingGatePreSemanticRequest }
  >({
    mutationFn: async ({ schemaMappingId, body }) => {
      try {
        return await schemaMapperApi.gateSchemaMappingPreSemantic(schemaMappingId, body);
      } catch (e: unknown) {
        if (e instanceof AiApiError && (e.status === 404 || e.status === 405)) {
          return schemaMapperApi.gateSchemaMappingFieldMapping(schemaMappingId, body);
        }
        throw e;
      }
    },
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMappingGateHierarchy(
  mutationOptions?: UseMutationOptions<
    SchemaMappingStatusResponse,
    unknown,
    { schemaMappingId: string; body: SchemaMappingGateHierarchyRequest }
  >,
) {
  return useMutation<
    SchemaMappingStatusResponse,
    unknown,
    { schemaMappingId: string; body: SchemaMappingGateHierarchyRequest }
  >({
    mutationFn: ({ schemaMappingId, body }) => schemaMapperApi.gateSchemaMappingHierarchy(schemaMappingId, body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMappingGateArtifactsReview(
  mutationOptions?: UseMutationOptions<
    SchemaMappingStatusResponse,
    unknown,
    { schemaMappingId: string; body: SchemaMappingGateArtifactsReviewRequest }
  >,
) {
  return useMutation<
    SchemaMappingStatusResponse,
    unknown,
    { schemaMappingId: string; body: SchemaMappingGateArtifactsReviewRequest }
  >({
    mutationFn: ({ schemaMappingId, body }) => schemaMapperApi.gateSchemaMappingArtifactsReview(schemaMappingId, body),
    ...(mutationOptions ?? {}),
  });
}

export function useMigrationAdvance(
  mutationOptions?: UseMutationOptions<MigrationStatusResponse, unknown, { migrationId: string }>,
) {
  return useMutation<MigrationStatusResponse, unknown, { migrationId: string }>({
    mutationFn: ({ migrationId }) => schemaMapperApi.advanceMigration(migrationId),
    ...(mutationOptions ?? {}),
  });
}

export function useMigrationGatePreSemantic(
  mutationOptions?: UseMutationOptions<
    MigrationStatusResponse,
    unknown,
    { migrationId: string; body: MigrationGatePreSemanticRequest }
  >,
) {
  return useMutation<
    MigrationStatusResponse,
    unknown,
    { migrationId: string; body: MigrationGatePreSemanticRequest }
  >({
    mutationFn: ({ migrationId, body }) => schemaMapperApi.gatePreSemantic(migrationId, body),
    ...(mutationOptions ?? {}),
  });
}

export function useMigrationGatePkApproval(
  mutationOptions?: UseMutationOptions<
    MigrationStatusResponse,
    unknown,
    { migrationId: string; body: MigrationGatePkApprovalRequest }
  >,
) {
  return useMutation<
    MigrationStatusResponse,
    unknown,
    { migrationId: string; body: MigrationGatePkApprovalRequest }
  >({
    mutationFn: ({ migrationId, body }) => schemaMapperApi.gatePkApproval(migrationId, body),
    ...(mutationOptions ?? {}),
  });
}

export function useMigrationGateUniqueTableApproval(
  mutationOptions?: UseMutationOptions<
    MigrationStatusResponse,
    unknown,
    { migrationId: string; body: MigrationGateUniqueTableApprovalRequest }
  >,
) {
  return useMutation<
    MigrationStatusResponse,
    unknown,
    { migrationId: string; body: MigrationGateUniqueTableApprovalRequest }
  >({
    mutationFn: ({ migrationId, body }) => schemaMapperApi.gateUniqueTableApproval(migrationId, body),
    ...(mutationOptions ?? {}),
  });
}

export function useMigrationGateColumnMappingApproval(
  mutationOptions?: UseMutationOptions<
    MigrationStatusResponse,
    unknown,
    { migrationId: string; body: MigrationGateColumnMappingApprovalRequest }
  >,
) {
  return useMutation<
    MigrationStatusResponse,
    unknown,
    { migrationId: string; body: MigrationGateColumnMappingApprovalRequest }
  >({
    mutationFn: ({ migrationId, body }) => schemaMapperApi.gateColumnMappingApproval(migrationId, body),
    ...(mutationOptions ?? {}),
  });
}

export function useMigrationGateClassificationApproval(
  mutationOptions?: UseMutationOptions<
    MigrationStatusResponse,
    unknown,
    { migrationId: string; body: MigrationGateClassificationApprovalRequest }
  >,
) {
  return useMutation<
    MigrationStatusResponse,
    unknown,
    { migrationId: string; body: MigrationGateClassificationApprovalRequest }
  >({
    mutationFn: ({ migrationId, body }) => schemaMapperApi.gateClassificationApproval(migrationId, body),
    ...(mutationOptions ?? {}),
  });
}

export function useMigrationGateFieldMapping(
  mutationOptions?: UseMutationOptions<
    MigrationStatusResponse,
    unknown,
    { migrationId: string; body: MigrationGateFieldMappingRequest }
  >,
) {
  return useMutation<
    MigrationStatusResponse,
    unknown,
    { migrationId: string; body: MigrationGateFieldMappingRequest }
  >({
    mutationFn: ({ migrationId, body }) => schemaMapperApi.gateFieldMapping(migrationId, body),
    ...(mutationOptions ?? {}),
  });
}

export function useMigrationGateHierarchy(
  mutationOptions?: UseMutationOptions<
    MigrationStatusResponse,
    unknown,
    { migrationId: string; body: MigrationGateHierarchyRequest }
  >,
) {
  return useMutation<
    MigrationStatusResponse,
    unknown,
    { migrationId: string; body: MigrationGateHierarchyRequest }
  >({
    mutationFn: ({ migrationId, body }) => schemaMapperApi.gateHierarchy(migrationId, body),
    ...(mutationOptions ?? {}),
  });
}

export function useMigrationGateFinal(
  mutationOptions?: UseMutationOptions<
    MigrationStatusResponse,
    unknown,
    { migrationId: string; body: MigrationGateFinalRequest }
  >,
) {
  return useMutation<
    MigrationStatusResponse,
    unknown,
    { migrationId: string; body: MigrationGateFinalRequest }
  >({
    mutationFn: ({ migrationId, body }) => schemaMapperApi.gateFinal(migrationId, body),
    ...(mutationOptions ?? {}),
  });
}

// ── Migration: list / delete / read-only hooks ────────────────────────────────

export function useMigrations(
  params?: ListMigrationsParams,
  queryOptions?: Omit<UseQueryOptions<MigrationListResponse, unknown>, "queryKey" | "queryFn">,
) {
  return useQuery<MigrationListResponse, unknown>({
    queryKey: ["migrations", params],
    queryFn: () => schemaMapperApi.listMigrations(params),
    ...(queryOptions ?? {}),
  });
}

export function useDeleteMigration(
  mutationOptions?: UseMutationOptions<{ status: string; message: string }, unknown, { migrationId: string }>,
) {
  return useMutation<{ status: string; message: string }, unknown, { migrationId: string }>({
    mutationFn: ({ migrationId }) => schemaMapperApi.deleteMigration(migrationId),
    ...(mutationOptions ?? {}),
  });
}

export function useMigrationMappings(
  migrationId: string,
  opts?: { tier?: string; enabled?: boolean },
  queryOptions?: Omit<UseQueryOptions<MappingListResponse, unknown>, "queryKey" | "queryFn" | "enabled">,
) {
  return useQuery<MappingListResponse, unknown>({
    queryKey: ["migration", "mappings", migrationId, opts?.tier],
    enabled: opts?.enabled ?? true,
    queryFn: () => schemaMapperApi.getMigrationMappings(migrationId, opts?.tier),
    ...(queryOptions ?? {}),
  });
}

export function useMigrationHierarchy(
  migrationId: string,
  opts?: { enabled?: boolean },
  queryOptions?: Omit<UseQueryOptions<unknown, unknown>, "queryKey" | "queryFn" | "enabled">,
) {
  return useQuery<unknown, unknown>({
    queryKey: ["migration", "hierarchy", migrationId],
    enabled: opts?.enabled ?? true,
    queryFn: () => schemaMapperApi.getMigrationHierarchy(migrationId),
    ...(queryOptions ?? {}),
  });
}

export function useMigrationDownload(
  migrationId: string,
  format: MigrationDownloadFormat,
  opts?: { enabled?: boolean },
  queryOptions?: Omit<UseQueryOptions<MigrationDownloadResponse, unknown>, "queryKey" | "queryFn" | "enabled">,
) {
  return useQuery<MigrationDownloadResponse, unknown>({
    queryKey: ["migration", "download", migrationId, format],
    enabled: opts?.enabled ?? false,
    queryFn: () => schemaMapperApi.getMigrationDownload(migrationId, format),
    ...(queryOptions ?? {}),
  });
}

export function useMigrationRetryDdl(
  mutationOptions?: UseMutationOptions<MigrationStatusResponse, unknown, { migrationId: string; body: MigrationRetryDdlRequest }>,
) {
  return useMutation<MigrationStatusResponse, unknown, { migrationId: string; body: MigrationRetryDdlRequest }>({
    mutationFn: ({ migrationId, body }) => schemaMapperApi.retryMigrationDdl(migrationId, body),
    ...(mutationOptions ?? {}),
  });
}

// ── Schema Mapping: list / delete / read-only hooks ───────────────────────────

export function useSchemaMappingsList(
  params?: ListSchemaMappingsParams,
  queryOptions?: Omit<UseQueryOptions<SchemaMappingListResponse, unknown>, "queryKey" | "queryFn">,
) {
  return useQuery<SchemaMappingListResponse, unknown>({
    queryKey: ["schema-mappings", params],
    queryFn: () => schemaMapperApi.listSchemaMappings(params),
    ...(queryOptions ?? {}),
  });
}

export function useDeleteSchemaMapping(
  mutationOptions?: UseMutationOptions<{ status: string; message: string }, unknown, { schemaMappingId: string }>,
) {
  return useMutation<{ status: string; message: string }, unknown, { schemaMappingId: string }>({
    mutationFn: ({ schemaMappingId }) => schemaMapperApi.deleteSchemaMapping(schemaMappingId),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMappingMappings(
  schemaMappingId: string,
  opts?: { tier?: string; enabled?: boolean },
  queryOptions?: Omit<UseQueryOptions<MappingListResponse, unknown>, "queryKey" | "queryFn" | "enabled">,
) {
  return useQuery<MappingListResponse, unknown>({
    queryKey: ["schema-mapping", "mappings", schemaMappingId, opts?.tier],
    enabled: opts?.enabled ?? true,
    queryFn: () => schemaMapperApi.getSchemaMappings(schemaMappingId, opts?.tier),
    ...(queryOptions ?? {}),
  });
}

export function useSchemaMappingUnmapped(
  schemaMappingId: string,
  opts?: { enabled?: boolean },
  queryOptions?: Omit<UseQueryOptions<SchemaUnmappedResponse, unknown>, "queryKey" | "queryFn" | "enabled">,
) {
  return useQuery<SchemaUnmappedResponse, unknown>({
    queryKey: ["schema-mapping", "unmapped", schemaMappingId],
    enabled: opts?.enabled ?? true,
    queryFn: () => schemaMapperApi.getSchemaUnmapped(schemaMappingId),
    ...(queryOptions ?? {}),
  });
}

export function useSchemaMappingAuditTrail(
  schemaMappingId: string,
  opts?: { enabled?: boolean },
  queryOptions?: Omit<UseQueryOptions<MappingListResponse, unknown>, "queryKey" | "queryFn" | "enabled">,
) {
  return useQuery<MappingListResponse, unknown>({
    queryKey: ["schema-mapping", "audit-trail", schemaMappingId],
    enabled: opts?.enabled ?? true,
    queryFn: async () => {
      const raw = await schemaMapperApi.getSchemaMappingAuditTrail(schemaMappingId);
      if (Array.isArray(raw)) {
        return { mappings: raw as unknown as MappingRecord[], total_mappings: raw.length, tier_breakdown: {} };
      }
      if (raw && typeof raw === "object" && "mappings" in raw) {
        return raw as MappingListResponse;
      }
      return { mappings: [], total_mappings: 0, tier_breakdown: {} };
    },
    ...(queryOptions ?? {}),
  });
}

export function useSchemaMappingCustomMapping(
  mutationOptions?: UseMutationOptions<
    SchemaCustomMappingResponse,
    unknown,
    { schemaMappingId: string; body: SchemaCustomMappingRequest }
  >,
) {
  return useMutation<SchemaCustomMappingResponse, unknown, { schemaMappingId: string; body: SchemaCustomMappingRequest }>({
    mutationFn: ({ schemaMappingId, body }) => schemaMapperApi.submitSchemaCustomMapping(schemaMappingId, body),
    ...(mutationOptions ?? {}),
  });
}

export function useSchemaMappingRetryDdl(
  mutationOptions?: UseMutationOptions<
    SchemaMappingStatusResponse,
    unknown,
    { schemaMappingId: string; body: { extra_fields_config: ExtraFieldConfig[] } }
  >,
) {
  return useMutation<
    SchemaMappingStatusResponse,
    unknown,
    { schemaMappingId: string; body: { extra_fields_config: ExtraFieldConfig[] } }
  >({
    mutationFn: ({ schemaMappingId, body }) => schemaMapperApi.retrySchemaMappingDdl(schemaMappingId, body),
    ...(mutationOptions ?? {}),
  });
}

// ── Fiix Data Ingestion ───────────────────────────────────────────────────────

export type FiixIngestionStartRequest = {
  organization_id?: string;
  created_by?: string;
  schema_mapping_id?: string | null;
};

export type FiixIngestionStartResponse = {
  ingestion_id: string;
  status: string;
  schema_mapping_id?: string | null;
  message?: string;
};

export type FiixIngestionStatus =
  | "pending"
  | "fetching"
  | "preprocessing"
  | "writing"
  | "complete"
  | "failed";

export type FiixIngestionStatusResponse = {
  ingestion_id: string;
  organization_id?: string;
  created_by?: string;
  status: FiixIngestionStatus;
  current_step?: string | null;
  progress_pct?: number | null;
  // Node 1 — Fetch
  total_records_fetched?: number | null;
  fetch_stats?: Record<string, number> | null;
  fetch_errors?: string[] | null;
  // Node 2 — Preprocess
  total_records_preprocessed?: number | null;
  preprocess_stats?: Record<string, unknown> | null;
  // Node 3 — Write
  total_records_written?: number | null;
  write_results?: Record<string, { inserted: number; skipped: number; errors: number }> | null;
  write_errors?: string[] | null;
  // Meta
  error_message?: string | null;
  error_node?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at?: string | null;
};

const fiixIngestionApi = {
  start: (body: FiixIngestionStartRequest) =>
    aiRequest<FiixIngestionStartResponse>({
      method: "POST",
      basePath: "/api/fiix-ingestion",
      path: "",
      query: {
        // Backend rejects non-UUID org ids (e.g. numeric "1"); resolve org from schema_mapping_id instead.
        ...(body.organization_id && isUuid(body.organization_id)
          ? { organization_id: body.organization_id }
          : {}),
        created_by: body.created_by ?? "system",
        ...(body.schema_mapping_id ? { schema_mapping_id: body.schema_mapping_id } : {}),
      },
    }),

  getStatus: (ingestionId: string) =>
    aiRequest<FiixIngestionStatusResponse>({
      method: "GET",
      basePath: "/api/fiix-ingestion",
      path: `/${encodeURIComponent(ingestionId)}`,
    }),
};

export function useFiixIngestionStart(
  mutationOptions?: UseMutationOptions<FiixIngestionStartResponse, unknown, FiixIngestionStartRequest>,
) {
  return useMutation<FiixIngestionStartResponse, unknown, FiixIngestionStartRequest>({
    mutationFn: (body) => fiixIngestionApi.start(body),
    ...(mutationOptions ?? {}),
  });
}

export function useFiixIngestionStatus(
  ingestionId: string,
  opts?: { enabled?: boolean; refetchInterval?: number },
) {
  const baseInterval = opts?.refetchInterval ?? 3000;
  return useQuery<FiixIngestionStatusResponse, unknown>({
    queryKey: ["fiix-ingestion", "status", ingestionId],
    enabled: opts?.enabled ?? true,
    staleTime: 0,
    refetchOnWindowFocus: true,
    queryFn: () => fiixIngestionApi.getStatus(ingestionId),
    refetchInterval: (q) => {
      const st = (q.state.data as FiixIngestionStatusResponse | undefined)?.status ?? "";
      if (st === "complete" || st === "failed") return false;
      return baseInterval;
    },
  });
}
