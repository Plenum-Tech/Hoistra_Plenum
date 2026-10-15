import type {
  MigrationFlaggedFieldItem,
  MigrationGateFieldMappingDecision,
  MigrationGateFieldMappingRequest,
  MigrationGateFieldMappingUnmappedDecision,
  MigrationStatusResponse,
} from "../../chat-api";
import { schemaMapperApi } from "../../chat-api";
import {
  isFieldMappingGateOpen,
  isPipelinePastFieldMappingGate,
  isSemanticMappingStepKey,
  normalizeMigrationGateType,
} from "./migration-gate-state";
import type { NewTableDef } from "./migration-new-tables-section";
import {
  emptyColumnOverrideDraft,
  type ColumnOverrideDraft,
} from "./migration-column-override";

export type FieldMappingDraftMeta = {
  canonicalTableBySource?: Record<string, string>;
  savedAt?: number;
};

export type FieldMappingDraftEnvelope = {
  body: MigrationGateFieldMappingRequest;
  meta?: FieldMappingDraftMeta;
};

export const FIELD_MAPPING_DRAFT_KEY = (migrationId: string) =>
  `plenum-migration-fm-draft:${migrationId}`;

export const SEMANTIC_DISMISSED_KEY = (migrationId: string) =>
  `plenum-migration-semantic-dismissed:${migrationId}`;

export const FIELD_MAPPING_DRAFT_EVENT = "plenum-migration-fm-draft-updated";

function draftSavedAt(envelope: FieldMappingDraftEnvelope | null | undefined): number {
  return envelope?.meta?.savedAt ?? 0;
}

export function parseEnvelope(raw: unknown): FieldMappingDraftEnvelope | null {
  if (!raw || typeof raw !== "object") return null;
  if ("body" in raw && (raw as FieldMappingDraftEnvelope).body) {
    return raw as FieldMappingDraftEnvelope;
  }
  return { body: raw as MigrationGateFieldMappingRequest };
}

/** Pick the newest draft between local sessionStorage and server copy. */
export function mergeDraftEnvelopes(
  local: FieldMappingDraftEnvelope | null,
  remote: FieldMappingDraftEnvelope | null,
): FieldMappingDraftEnvelope | null {
  if (!local) return remote;
  if (!remote) return local;
  return draftSavedAt(local) >= draftSavedAt(remote) ? local : remote;
}

export function notifyFieldMappingDraftUpdated(migrationId: string) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(FIELD_MAPPING_DRAFT_EVENT, { detail: { migrationId } }),
  );
}

function sourceFieldToTable(
  flaggedByTable: Record<string, MigrationFlaggedFieldItem[]>,
): Map<string, string> {
  const map = new Map<string, string>();
  for (const [tbl, items] of Object.entries(flaggedByTable)) {
    if (!Array.isArray(items)) continue;
    for (const item of items) {
      if (item.source_field?.trim()) map.set(item.source_field, tbl);
    }
  }
  return map;
}

/** Remap draft table keys (e.g. canonical `assets`) → source sheet keys (`data`). */
export function normalizeDraftForPayload(
  body: MigrationGateFieldMappingRequest,
  flaggedByTable: Record<string, MigrationFlaggedFieldItem[]>,
  unmappedByTable: Record<string, unknown[]> = {},
): MigrationGateFieldMappingRequest {
  const fieldToSourceTable = sourceFieldToTable(flaggedByTable);
  for (const [tbl, items] of Object.entries(unmappedByTable)) {
    if (!Array.isArray(items)) continue;
    for (const item of items) {
      const sf = (item as { source_field?: string }).source_field;
      if (sf?.trim() && !fieldToSourceTable.has(sf)) fieldToSourceTable.set(sf, tbl);
    }
  }

  const flagged: Record<string, MigrationGateFieldMappingDecision[]> = {};
  for (const [draftTbl, rows] of Object.entries(body.flagged ?? {})) {
    for (const row of rows) {
      const tbl = fieldToSourceTable.get(row.source_field) ?? draftTbl;
      flagged[tbl] = flagged[tbl] ?? [];
      flagged[tbl].push(row);
    }
  }

  const unmapped: Record<string, MigrationGateFieldMappingUnmappedDecision[]> = {};
  for (const [draftTbl, rows] of Object.entries(body.unmapped ?? {})) {
    if (draftTbl.startsWith("_new_table_")) {
      unmapped[draftTbl] = rows;
      continue;
    }
    for (const row of rows) {
      const tbl = fieldToSourceTable.get(row.source_field) ?? draftTbl;
      unmapped[tbl] = unmapped[tbl] ?? [];
      unmapped[tbl].push(row);
    }
  }

  return { flagged, unmapped };
}

export function persistFieldMappingDraft(
  migrationId: string,
  body: MigrationGateFieldMappingRequest,
  meta?: FieldMappingDraftMeta,
) {
  try {
    const envelope: FieldMappingDraftEnvelope = {
      body,
      meta: { ...meta, savedAt: Date.now() },
    };
    sessionStorage.setItem(FIELD_MAPPING_DRAFT_KEY(migrationId), JSON.stringify(envelope));
    notifyFieldMappingDraftUpdated(migrationId);
    void syncFieldMappingDraftToServer(migrationId, envelope);
  } catch {
    /* ignore quota / private mode */
  }
}

const draftSyncTimers = new Map<string, ReturnType<typeof setTimeout>>();

/** Debounced PUT to backend so choices survive refresh / remount. */
export function syncFieldMappingDraftToServer(
  migrationId: string,
  envelope: FieldMappingDraftEnvelope,
  debounceMs = 400,
) {
  const prev = draftSyncTimers.get(migrationId);
  if (prev) clearTimeout(prev);
  draftSyncTimers.set(
    migrationId,
    setTimeout(() => {
      draftSyncTimers.delete(migrationId);
      void schemaMapperApi
        .putFieldMappingDraft(migrationId, envelope)
        .catch(() => {
          /* offline / column not migrated yet — sessionStorage still holds draft */
        });
    }, debounceMs),
  );
}

export function flushFieldMappingDraftToServer(
  migrationId: string,
  envelope: FieldMappingDraftEnvelope,
) {
  const prev = draftSyncTimers.get(migrationId);
  if (prev) clearTimeout(prev);
  draftSyncTimers.delete(migrationId);
  void schemaMapperApi.putFieldMappingDraft(migrationId, envelope).catch(() => {
    /* ignore */
  });
}

export async function fetchFieldMappingDraftFromServer(
  migrationId: string,
): Promise<FieldMappingDraftEnvelope | null> {
  try {
    const res = await schemaMapperApi.getFieldMappingDraft(migrationId);
    return parseEnvelope(res.draft);
  } catch {
    return null;
  }
}

export async function loadFieldMappingDraftEnvelopeMerged(
  migrationId: string,
): Promise<FieldMappingDraftEnvelope | null> {
  const local = loadFieldMappingDraftEnvelope(migrationId);
  const remote = await fetchFieldMappingDraftFromServer(migrationId);
  const merged = mergeDraftEnvelopes(local, remote);
  if (merged && merged !== local) {
    try {
      sessionStorage.setItem(FIELD_MAPPING_DRAFT_KEY(migrationId), JSON.stringify(merged));
    } catch {
      /* ignore */
    }
  }
  return merged;
}

export function loadFieldMappingDraftEnvelope(migrationId: string): FieldMappingDraftEnvelope | null {
  try {
    const raw = sessionStorage.getItem(FIELD_MAPPING_DRAFT_KEY(migrationId));
    if (!raw) return null;
    return parseEnvelope(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function loadFieldMappingDraft(migrationId: string): MigrationGateFieldMappingRequest | null {
  return loadFieldMappingDraftEnvelope(migrationId)?.body ?? null;
}

export type ApplyTier2Result = { ok: true } | { ok: false; error: string };

/**
 * Save Tier-2 draft, dismiss semantic latch, then submit field-mapping gate when the
 * pipeline is ready (or resume past a semantic step pause).
 */
export async function applyTier2FieldMappingContinue(
  migrationId: string,
  body: MigrationGateFieldMappingRequest,
  flaggedByTable: Record<string, MigrationFlaggedFieldItem[]>,
  unmappedByTable: Record<string, unknown[]> = {},
  meta?: FieldMappingDraftMeta,
): Promise<ApplyTier2Result> {
  const normalized = normalizeDraftForPayload(body, flaggedByTable, unmappedByTable);
  const envelope: FieldMappingDraftEnvelope = {
    body: normalized,
    meta: { ...meta, savedAt: Date.now() },
  };
  persistFieldMappingDraft(migrationId, normalized, meta);
  flushFieldMappingDraftToServer(migrationId, envelope);
  markSemanticDismissed(migrationId);

  const tryGateSubmit = async (): Promise<ApplyTier2Result> => {
    try {
      await schemaMapperApi.gateFieldMapping(migrationId, normalized);
      clearFieldMappingDraft(migrationId);
      return { ok: true };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      if (/gate mismatch|409/i.test(msg)) {
        return { ok: false, error: msg };
      }
      return { ok: false, error: msg || "Field mapping submit failed" };
    }
  };

  let latest = await schemaMapperApi.getMigrationStatus(migrationId);
  let st = String(latest.status ?? "").toLowerCase();
  let gate = normalizeMigrationGateType(
    typeof latest.pending_gate_type === "string" ? latest.pending_gate_type : null,
  );

  if (gate === "field_mapping" && (st === "awaiting_review" || st === "running")) {
    const result = await tryGateSubmit();
    if (result.ok) return result;
  }

  if (st === "step_paused") {
    const pendingKey =
      typeof latest.pending_gate_type === "string" ? latest.pending_gate_type : null;
    if (isSemanticMappingStepKey(pendingKey)) {
      try {
        await schemaMapperApi.advanceMigration(migrationId);
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        return {
          ok: false,
          error: `Could not advance past semantic step: ${msg}`,
        };
      }
    }
  }

  const wait = await waitForFieldMappingGate(migrationId, 30, 1500);
  if (wait.outcome === "field_mapping_open") {
    const result = await tryGateSubmit();
    if (result.ok) return result;
  }
  if (wait.outcome === "skipped" || wait.outcome === "timeout") {
    latest = wait.migration ?? (await schemaMapperApi.getMigrationStatus(migrationId));
    if (isPipelinePastFieldMappingGate(latest)) {
      return { ok: true };
    }
  }

  latest = await schemaMapperApi.getMigrationStatus(migrationId);
  st = String(latest.status ?? "").toLowerCase();
  gate = normalizeMigrationGateType(
    typeof latest.pending_gate_type === "string" ? latest.pending_gate_type : null,
  );

  if (isPipelinePastFieldMappingGate(latest)) {
    return { ok: true };
  }

  if (gate === "field_mapping" && (st === "awaiting_review" || st === "running")) {
    return tryGateSubmit();
  }

  if (st === "running" && !latest.pending_gate_type) {
    const result = await tryGateSubmit();
    if (result.ok) return result;
  }

  return {
    ok: false,
    error:
      "Your Tier-2 choices are saved, but the field-mapping gate is not open yet. " +
      "Wait a few seconds and click Apply Tier-2 choices again, or ask the orchestrator to resume this migration.",
  };
}

export type FieldMappingGateWaitResult = {
  outcome: "field_mapping_open" | "skipped" | "timeout" | "terminal";
  migration: MigrationStatusResponse | null;
};

export function waitForFieldMappingGate(
  migrationId: string,
  maxAttempts = 30,
  intervalMs = 1500,
): Promise<FieldMappingGateWaitResult> {
  return new Promise((resolve) => {
    let attempt = 0;
    const tick = async () => {
      attempt += 1;
      try {
        const latest = await schemaMapperApi.getMigrationStatus(migrationId);
        const st = String(latest.status ?? "").toLowerCase();
        if (st === "failed" || st === "ddl_failed" || st === "cancelled" || st === "complete") {
          resolve({ outcome: "terminal", migration: latest });
          return;
        }
        if (isFieldMappingGateOpen(latest)) {
          resolve({ outcome: "field_mapping_open", migration: latest });
          return;
        }
        if (isPipelinePastFieldMappingGate(latest)) {
          resolve({ outcome: "skipped", migration: latest });
          return;
        }
      } catch {
        /* retry */
      }
      if (attempt >= maxAttempts) {
        resolve({ outcome: "timeout", migration: null });
        return;
      }
      setTimeout(() => void tick(), intervalMs);
    };
    void tick();
  });
}

export function clearFieldMappingDraft(migrationId: string) {
  try {
    sessionStorage.removeItem(FIELD_MAPPING_DRAFT_KEY(migrationId));
    notifyFieldMappingDraftUpdated(migrationId);
    void schemaMapperApi.deleteFieldMappingDraft(migrationId).catch(() => {
      /* ignore */
    });
  } catch {
    /* ignore */
  }
}

export function isSemanticDismissed(migrationId: string): boolean {
  try {
    return sessionStorage.getItem(SEMANTIC_DISMISSED_KEY(migrationId)) === "1";
  } catch {
    return false;
  }
}

export function markSemanticDismissed(migrationId: string) {
  try {
    sessionStorage.setItem(SEMANTIC_DISMISSED_KEY(migrationId), "1");
  } catch {
    /* ignore */
  }
}

export function clearSemanticDismissed(migrationId: string) {
  try {
    sessionStorage.removeItem(SEMANTIC_DISMISSED_KEY(migrationId));
  } catch {
    /* ignore */
  }
}

type FlaggedDecision = { action: "accept" | "reject" | "override"; overrideTarget: string };

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

type UnmappedRow = {
  action: "custom" | "raw_metadata" | "skip";
  sourceField: string;
  ddl: CustomDDL | null;
};

function decisionsBySourceField(
  body: MigrationGateFieldMappingRequest,
): Map<string, { flagged?: (typeof body.flagged)[string][number]; unmapped?: MigrationGateFieldMappingUnmappedDecision }> {
  const map = new Map<
    string,
    { flagged?: (typeof body.flagged)[string][number]; unmapped?: MigrationGateFieldMappingUnmappedDecision }
  >();

  for (const decisions of Object.values(body.flagged ?? {})) {
    for (const d of decisions) {
      const prev = map.get(d.source_field) ?? {};
      map.set(d.source_field, { ...prev, flagged: d });
    }
  }

  for (const rows of Object.values(body.unmapped ?? {})) {
    for (const u of rows) {
      const prev = map.get(u.source_field) ?? {};
      map.set(u.source_field, { ...prev, unmapped: u });
    }
  }

  return map;
}

/** Merge Tier-2 semantic draft into Field Mapping gate UI state. */
export function applyFieldMappingDraftToUiState(
  body: MigrationGateFieldMappingRequest,
  flaggedByTable: Record<string, MigrationFlaggedFieldItem[]>,
  unmappedByTable: Record<string, unknown[]> = {},
  meta?: FieldMappingDraftMeta,
): {
  flaggedDecisions: Record<string, FlaggedDecision[]>;
  unmappedRows: Record<string, UnmappedRow[]>;
  newTableDefs: NewTableDef[];
  canonicalTableBySource: Record<string, string>;
} {
  const normalized = normalizeDraftForPayload(body, flaggedByTable, unmappedByTable);
  const byField = decisionsBySourceField(normalized);
  const flaggedDecisions: Record<string, FlaggedDecision[]> = {};
  const unmappedRows: Record<string, UnmappedRow[]> = {};
  const newTableDefsMap = new Map<string, NewTableDef>();

  for (const [tbl, items] of Object.entries(flaggedByTable)) {
    if (!Array.isArray(items)) continue;
    flaggedDecisions[tbl] = (items as MigrationFlaggedFieldItem[]).map((item) => {
      const entry = byField.get(item.source_field);
      const flagged = entry?.flagged;
      const unmapped = entry?.unmapped;

      if (unmapped?.action === "custom") {
        const tableName = unmapped.new_table_name ?? unmapped.target_table ?? "";
        if (unmapped.is_new_table && tableName) {
          let def = newTableDefsMap.get(tableName);
          if (!def) {
            def = {
              id: `nt_draft_${tableName}`,
              table_name: tableName,
              pk_col: unmapped.new_table_pk ?? "id",
              columns: [],
            };
            newTableDefsMap.set(tableName, def);
          }
          def.columns.push({
            column_name: unmapped.custom_column_name ?? item.source_field,
            data_type: unmapped.data_type ?? "VARCHAR(255)",
            nullable: unmapped.nullable ?? true,
          });
        }
        return {
          action: "override" as const,
          overrideTarget: unmapped.custom_column_name ?? item.target_field ?? "",
        };
      }

      if (flagged?.action === "override" && flagged.target_field) {
        return { action: "override" as const, overrideTarget: flagged.target_field };
      }
      if (flagged?.action === "reject") {
        return { action: "reject" as const, overrideTarget: "" };
      }
      if (flagged?.action === "accept") {
        return {
          action: "accept" as const,
          overrideTarget: flagged.target_field ?? item.target_field ?? "",
        };
      }

      return {
        action: (item.target_field ? "accept" : "override") as FlaggedDecision["action"],
        overrideTarget: item.target_field ?? "",
      };
    });
  }

  for (const [tbl, items] of Object.entries(unmappedByTable)) {
    if (!Array.isArray(items)) continue;
    unmappedRows[tbl] = items.map((item) => {
      const rec = item as { source_field?: string };
      const sf = rec.source_field ?? "";
      const entry = byField.get(sf);
      const u = entry?.unmapped;
      if (u?.action === "custom") {
        const tableName = u.new_table_name ?? u.target_table ?? "";
        return {
          action: "custom" as const,
          sourceField: sf,
          ddl: {
            sourceField: sf,
            targetTable: tableName,
            colName: u.custom_column_name ?? sf,
            dataType: u.data_type ?? "VARCHAR(255)",
            nullable: u.nullable ?? true,
            isNewTable: !!u.is_new_table,
            newTableName: tableName,
            newTablePk: u.new_table_pk ?? "id",
          },
        };
      }
      return { action: "raw_metadata" as const, sourceField: sf, ddl: null };
    });
  }

  for (const [key, rows] of Object.entries(normalized.unmapped ?? {})) {
    if (!key.startsWith("_new_table_")) continue;
    const tableName = key.replace(/^_new_table_/, "");
    if (!tableName || newTableDefsMap.has(tableName)) continue;
    newTableDefsMap.set(tableName, {
      id: `nt_draft_${tableName}`,
      table_name: tableName,
      pk_col: rows[0]?.new_table_pk ?? "id",
      columns: rows
        .filter((c) => c.custom_column_name?.trim())
        .map((c) => ({
          column_name: c.custom_column_name ?? "",
          data_type: c.data_type ?? "VARCHAR(255)",
          nullable: c.nullable ?? true,
        })),
    });
  }

  return {
    flaggedDecisions,
    unmappedRows,
    newTableDefs: Array.from(newTableDefsMap.values()),
    canonicalTableBySource: buildCanonicalTableBySource(
      flaggedByTable,
      unmappedByTable,
      meta?.canonicalTableBySource,
    ),
  };
}

/** Merge saved canonical table picks with defaults for every source table. */
export function buildCanonicalTableBySource(
  flaggedByTable: Record<string, MigrationFlaggedFieldItem[]>,
  unmappedByTable: Record<string, unknown[]> = {},
  saved: Record<string, string> | undefined = {},
  routing: Record<string, string> | undefined = {},
): Record<string, string> {
  const out: Record<string, string> = { ...(saved ?? {}) };
  const allTables = new Set([
    ...Object.keys(flaggedByTable),
    ...Object.keys(unmappedByTable),
  ]);
  for (const tbl of allTables) {
    if (out[tbl]?.trim()) continue;
    // Default to the destination table chosen at the pre-semantic gate
    // (table_routing) so this gate reflects the user's selection — fall back
    // to the source name only when no routing exists.
    const routed = (routing ?? {})[tbl]?.trim();
    if (routed) out[tbl] = routed;
    else if (tbl.trim()) out[tbl] = tbl;
  }
  return out;
}

/** Restore Tier-2 semantic review UI state from a saved draft envelope. */
export function restoreSemanticReviewFromDraft(
  envelope: FieldMappingDraftEnvelope,
  reviewByTable: Record<string, MigrationFlaggedFieldItem[]>,
): {
  decisions: Record<string, "accept" | "reject" | "override">;
  columnDrafts: Record<string, ColumnOverrideDraft>;
  canonicalTableBySource: Record<string, string>;
  newTableDefs: NewTableDef[];
} {
  const applied = applyFieldMappingDraftToUiState(
    envelope.body,
    reviewByTable,
    {},
    envelope.meta,
  );
  const decisions: Record<string, "accept" | "reject" | "override"> = {};
  const columnDrafts: Record<string, ColumnOverrideDraft> = {};

  for (const [tbl, items] of Object.entries(reviewByTable)) {
    if (!Array.isArray(items)) continue;
    (items as MigrationFlaggedFieldItem[]).forEach((item, idx) => {
      const key = `${tbl}.${item.source_field}`;
      const fd = applied.flaggedDecisions[tbl]?.[idx];
      if (!fd) return;
      decisions[key] = fd.action;
      if (fd.action === "override" && fd.overrideTarget.trim()) {
        columnDrafts[key] = {
          ...emptyColumnOverrideDraft(fd.overrideTarget),
          targetField: fd.overrideTarget,
          mode: "existing",
        };
      }
    });
  }

  return {
    decisions,
    columnDrafts,
    canonicalTableBySource: applied.canonicalTableBySource,
    newTableDefs: applied.newTableDefs,
  };
}
