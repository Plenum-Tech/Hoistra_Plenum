import type { MigrationStatusResponse } from "../../chat-api";

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

export type MigrationHitlGateType =
  | "pre_semantic"
  | "field_mapping"
  | "hierarchy"
  | "final_confirmation";

export type FieldMappingPayloadShape = {
  flagged_by_table: Record<string, Array<Record<string, unknown>>>;
  unmapped_by_table: Record<string, Array<Record<string, unknown>>>;
  existing_canonical_tables: string[];
  /** source_table → destination UDR table chosen at the pre-semantic gate. */
  table_routing: Record<string, string>;
};

function coerceTableRouting(v: unknown): Record<string, string> {
  if (!isRecord(v)) return {};
  const out: Record<string, string> = {};
  for (const [src, dest] of Object.entries(v)) {
    if (typeof dest === "string" && dest.trim()) out[src] = dest.trim();
  }
  return out;
}

/** GATE 1 (human_review_node) uses review_items_by_table / unmappable_items_by_table — normalize for UI. */
export function normalizeFieldMappingGatePayload(
  rec: Record<string, unknown>,
): FieldMappingPayloadShape {
  const flaggedRaw =
    (isRecord(rec.flagged_by_table) ? rec.flagged_by_table : null) ??
    (isRecord(rec.review_items_by_table) ? rec.review_items_by_table : null) ??
    (isRecord(rec.tier2_flagged_by_table) ? rec.tier2_flagged_by_table : null) ??
    {};
  const unmappedRaw =
    (isRecord(rec.unmapped_by_table) ? rec.unmapped_by_table : null) ??
    (isRecord(rec.unmappable_items_by_table) ? rec.unmappable_items_by_table : null) ??
    // Semantic step (Tier-2) carries unmappable fields under tier2_unmappable_by_table.
    // Without this, an unmappable-only semantic result counted 0 review items, so the
    // editable field-mapping gate (with "create new column" + data type) never opened
    // and the field showed read-only with just "Continue".
    (isRecord(rec.tier2_unmappable_by_table) ? rec.tier2_unmappable_by_table : null) ??
    {};

  const flagged_by_table: FieldMappingPayloadShape["flagged_by_table"] = {};
  for (const [tbl, items] of Object.entries(flaggedRaw)) {
    if (!Array.isArray(items)) continue;
    flagged_by_table[tbl] = items
      .filter((item): item is Record<string, unknown> => isRecord(item))
      .map((item) => {
        const target =
          (typeof item.target_field === "string" && item.target_field) ||
          (typeof item.suggested_target === "string" && item.suggested_target) ||
          null;
        return {
          ...item,
          source_table: typeof item.source_table === "string" ? item.source_table : tbl,
          source_field: String(item.source_field ?? ""),
          target_field: target,
          confidence:
            typeof item.confidence === "number" && Number.isFinite(item.confidence)
              ? item.confidence
              : null,
          tier: item.tier ?? "T2_semantic",
          suggestions: Array.isArray(item.suggestions) ? item.suggestions : [],
        };
      })
      .filter((item) => item.source_field.length > 0);
  }

  const unmapped_by_table: FieldMappingPayloadShape["unmapped_by_table"] = {};
  for (const [tbl, items] of Object.entries(unmappedRaw)) {
    if (!Array.isArray(items)) continue;
    const list: Array<Record<string, unknown>> = [];
    for (const item of items) {
      if (typeof item === "string" && item.trim()) {
        list.push({ source_field: item.trim(), source_table: tbl });
        continue;
      }
      if (!isRecord(item)) continue;
      const field =
        (typeof item.source_field === "string" && item.source_field) ||
        (typeof item.field_name === "string" && item.field_name) ||
        "";
      if (!field.trim()) continue;
      list.push({
        ...item,
        source_field: field.trim(),
        source_table: typeof item.source_table === "string" ? item.source_table : tbl,
        sample_values: Array.isArray(item.sample_values) ? item.sample_values : [],
      });
    }
    if (list.length) unmapped_by_table[tbl] = list;
  }

  const canonicalFromPayload = Array.isArray(rec.existing_canonical_tables)
    ? (rec.existing_canonical_tables as string[]).filter((t) => typeof t === "string" && t.trim())
    : [];

  return {
    flagged_by_table,
    unmapped_by_table,
    existing_canonical_tables: canonicalFromPayload,
    table_routing: coerceTableRouting(rec.table_routing),
  };
}

/** Coerce gate table buckets to arrays — backend may send a single object per table. */
export function normalizeGateTableRows(v: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(v)) {
    return v.filter((row): row is Record<string, unknown> => isRecord(row));
  }
  if (isRecord(v)) return [v];
  return [];
}

export function normalizeGateTablesByName(
  v: unknown,
): Record<string, Array<Record<string, unknown>>> {
  if (!isRecord(v)) return {};
  const out: Record<string, Array<Record<string, unknown>>> = {};
  for (const [tbl, rows] of Object.entries(v)) {
    const normalized = normalizeGateTableRows(rows);
    if (normalized.length) out[tbl] = normalized;
  }
  return out;
}

export function normalizeFieldMappingPayload(
  payload: Record<string, unknown> | null | undefined,
): FieldMappingPayloadShape {
  const rec = payload && isRecord(payload) ? payload : {};
  const flaggedRaw =
    rec.flagged_by_table ?? rec.review_items_by_table ?? rec.tier2_flagged_by_table ?? {};
  const unmappedRaw =
    rec.unmapped_by_table ?? rec.unmappable_items_by_table ?? rec.tier2_unmappable_by_table ?? {};
  const canonicalFromPayload = Array.isArray(rec.existing_canonical_tables)
    ? (rec.existing_canonical_tables as string[])
    : [];
  return {
    flagged_by_table: normalizeGateTablesByName(flaggedRaw),
    unmapped_by_table: normalizeGateTablesByName(unmappedRaw),
    existing_canonical_tables: canonicalFromPayload,
    table_routing: coerceTableRouting(rec.table_routing),
  };
}

export function normalizeMigrationGateType(v: string | null | undefined): MigrationHitlGateType | null {
  if (!v) return null;
  if (v.startsWith("step_")) return null;
  if (v === "pre_semantic" || v === "field_mapping" || v === "hierarchy" || v === "final_confirmation") {
    return v;
  }
  const s = v.toLowerCase();
  if (s.includes("human") && s.includes("review")) return "field_mapping";
  if (s.includes("pre") && s.includes("semantic")) return "pre_semantic";
  if (s.includes("field") && s.includes("map")) return "field_mapping";
  if ((s.includes("table") && s.includes("structure")) || (s.includes("column") && s.includes("placement"))) {
    return "field_mapping";
  }
  if (s.includes("hier")) return "hierarchy";
  if (s === "write" || s.includes("write")) return "final_confirmation";
  if (s.includes("final")) return "final_confirmation";
  return null;
}

export function isPreSemanticPayload(v: unknown): boolean {
  if (!isRecord(v)) return false;
  return isRecord(v.review_items_by_table) && typeof v.total_reviewable === "number";
}

function unwrapRecordPayload(input: unknown): Record<string, unknown> | null {
  if (!isRecord(input)) return null;
  const candidates: unknown[] = [
    input,
    input.payload,
    input.gate_payload,
    input.pending_gate_payload,
    input.output,
    input.body,
  ];
  for (const c of candidates) {
    if (isPreSemanticPayload(c)) return c as Record<string, unknown>;
  }
  for (const c of candidates) {
    if (isRecord(c)) return c;
  }
  return null;
}

/** Pre-semantic gate payload from pending_gate or node output (when status lags). */
export function resolvePreSemanticGatePayload(
  migration: MigrationStatusResponse | null | undefined,
): Record<string, unknown> | null {
  if (!migration) return null;

  const pendingType = normalizeMigrationGateType(
    typeof migration.pending_gate_type === "string" ? migration.pending_gate_type : null,
  );
  const pending = unwrapRecordPayload(migration.pending_gate_payload);
  if (pending && isPreSemanticPayload(pending)) {
    if (pendingType === "pre_semantic" || pendingType == null) return pending;
  }

  // Node-output fallback (status lags the live gate) — but NOT while the run is actively "running":
  // during the Node-2 → pre-semantic transition the run is briefly "running" while Node-2's
  // (pre-semantic-shaped) output is still the latest node payload, so resolving the gate off that
  // flashed the "Step 1 — Confirm table routing" card with a stale, empty ("0 sheets") payload for a
  // few seconds before the real gate landed (it then vanished, reappeared → visible flicker). The
  // real gate always arrives as pending_gate_payload (handled above); until then, resolve nothing
  // while running so the renderer holds the single "Preparing Pre-Semantic Review…" card. This
  // mirrors the running-branch guard in isPreSemanticGatePending below.
  if (String(migration.status ?? "").toLowerCase() !== "running") {
    for (const nodeId of [3, 2]) {
      const node = (migration.nodes ?? []).find((n) => n.node_id === nodeId);
      const out = unwrapRecordPayload(node?.output);
      if (out && isPreSemanticPayload(out)) return out;
    }
  }

  return null;
}

/** True while Tier-1 pre-semantic review must be shown before semantic / field-mapping UI. */
export function isPreSemanticGatePending(
  migration: MigrationStatusResponse | null | undefined,
): boolean {
  if (!migration) return false;

  const st = String(migration.status ?? "").toLowerCase();
  const pendingType = normalizeMigrationGateType(
    typeof migration.pending_gate_type === "string" ? migration.pending_gate_type : null,
  );

  if (pendingType === "pre_semantic") return true;
  if (st === "awaiting_review" && resolvePreSemanticGatePayload(migration)) return true;

  if (st === "running") {
    // Trust ONLY the live gate payload here — the pre-semantic gate is genuinely open (with the
    // status field merely lagging) once pending_gate_payload carries the review. Do NOT fall back
    // to node output or a "node 3 is running" heuristic: during the Node-2 → pre-semantic
    // transition the run is briefly "running" while Node 2's (pre-semantic-shaped) output is still
    // the latest node output, so resolving the gate from that flashed "Step 1 — Confirm table
    // routing" with a stale, empty ("0 sheets") payload for a few seconds before the real gate
    // landed. The real gate always arrives as pending_gate_payload (status → awaiting_review),
    // handled by the branches above; until then the running card shows "Preparing Pre-Semantic
    // Review…" instead of a half-built gate.
    const livePending = unwrapRecordPayload(migration.pending_gate_payload);
    if (livePending && isPreSemanticPayload(livePending)) return true;
  }

  return false;
}

export function isSemanticMappingStepKey(pending: string | null | undefined): boolean {
  if (!pending) return false;
  const s = pending.toLowerCase();
  return (
    s === "step_4_semantic" ||
    s === "step_4_semantic_mapping" ||
    s === "step_3_semantic" ||
    s === "step_3_semantic_mapping"
  );
}

export function isSemanticMappingPausePayload(v: unknown): boolean {
  if (!isRecord(v)) return false;
  const label = typeof v.label === "string" ? v.label.toLowerCase() : "";
  return (
    label.includes("semantic") ||
    typeof v.t2_auto_mapped === "number" ||
    typeof v.t2_flagged === "number" ||
    typeof v.tier2_auto_mapped === "number" ||
    typeof v.tier2_flagged === "number" ||
    Array.isArray(v.semantic_results)
  );
}

/** Backend logs semantic mapper as graph node 3; status API often stores output on node_id 4. */
export function findSemanticMappingNode(migration: MigrationStatusResponse) {
  const nodes = migration.nodes ?? [];
  const byId4 = nodes.find((n) => n.node_id === 4);
  if (byId4?.output && isSemanticNodeOutput(byId4.output)) return byId4;
  const byId3 = nodes.find((n) => n.node_id === 3);
  if (byId3?.output && isSemanticNodeOutput(byId3.output)) return byId3;
  return nodes.find((n) => {
    const name = String(n.node_name ?? "").toLowerCase();
    return name.includes("semantic") && !name.includes("pre");
  });
}

function isSemanticNodeOutput(output: unknown): boolean {
  if (!isRecord(output)) return false;
  return (
    typeof output.tier2_auto_mapped === "number" ||
    typeof output.tier2_flagged === "number" ||
    typeof output.unmappable === "number" ||
    Array.isArray(output.semantic_results) ||
    isRecord(output.tier2_unmappable_by_table) ||
    isRecord(output.tier2_flagged_by_table)
  );
}

export function isNodeSemanticMappingComplete(migration: MigrationStatusResponse): boolean {
  const node = findSemanticMappingNode(migration);
  if (!node) return false;
  const st = String(node.status ?? "").toLowerCase();
  if (st === "completed" || st === "complete" || st === "done") return true;
  return isSemanticNodeOutput(node.output);
}

/** True when Tier-2 flagged/unmapped items require the semantic review UI before Field Structure. */
export function needsSemanticReviewBeforeFieldMapping(
  migration: MigrationStatusResponse | null | undefined,
  semanticMappingDismissed: boolean,
): boolean {
  if (!migration || semanticMappingDismissed) return false;
  const payload = buildFieldMappingPayloadFromMigration(migration);
  return countFieldMappingReviewItems(payload) > 0;
}

/** Field Structure may proceed only after semantic dismiss when flagged T2 items exist. */
export function isSemanticReviewCompleteForFieldMapping(
  migration: MigrationStatusResponse | null | undefined,
  semanticMappingDismissed: boolean,
): boolean {
  if (semanticMappingDismissed) return true;
  if (needsSemanticReviewBeforeFieldMapping(migration, false)) return false;
  if (!migration) return true;
  return isNodeSemanticMappingComplete(migration);
}

function pushField(
  tableToFields: Map<string, Set<string>>,
  table: string,
  field: string,
) {
  const t = table.trim();
  const f = field.trim();
  if (!t || !f) return;
  if (!tableToFields.has(t)) tableToFields.set(t, new Set());
  tableToFields.get(t)?.add(f);
}

function ingestSemanticResults(
  tableToFlagged: Map<string, Array<Record<string, unknown>>>,
  tableToUnmapped: Map<string, Array<Record<string, unknown>>>,
  results: unknown[],
) {
  for (const raw of results) {
    if (!isRecord(raw)) continue;
    const table = String(raw.table ?? raw.source_table ?? "default").trim();
    const sourceField = String(raw.source_field ?? "").trim();
    if (!sourceField) continue;
    const status = String(raw.status ?? "flagged").toLowerCase();

    if (status === "unmappable") {
      const list = tableToUnmapped.get(table) ?? [];
      list.push({
        source_field: sourceField,
        source_table: table,
        sample_values: Array.isArray(raw.sample_values) ? raw.sample_values : [],
      });
      tableToUnmapped.set(table, list);
      continue;
    }

    if (status === "flagged") {
      const list = tableToFlagged.get(table) ?? [];
      const target = raw.target_field ?? raw.best_target ?? null;
      const confidence = typeof raw.confidence === "number" ? raw.confidence : raw.best_confidence ?? null;
      const suggestions: string[] = [];
      if (typeof target === "string" && target) suggestions.push(target);
      list.push({
        source_field: sourceField,
        source_table: table,
        target_field: target,
        confidence,
        tier: raw.tier ?? "T2_semantic",
        suggestions,
        sample_values: Array.isArray(raw.sample_values) ? raw.sample_values : [],
      });
      tableToFlagged.set(table, list);
    }
  }
}

function ingestTier2Tables(
  tableToFlagged: Map<string, Array<Record<string, unknown>>>,
  tableToUnmapped: Map<string, Array<Record<string, unknown>>>,
  flaggedByTable: Record<string, unknown> | undefined,
  unmappableByTable: Record<string, unknown> | undefined,
) {
  if (flaggedByTable) {
    for (const [tbl, items] of Object.entries(flaggedByTable)) {
      if (!Array.isArray(items)) continue;
      const list = tableToFlagged.get(tbl) ?? [];
      for (const item of items) {
        if (!isRecord(item)) continue;
        list.push({
          source_field: String(item.source_field ?? ""),
          source_table: tbl,
          target_field: item.target_field ?? null,
          confidence: item.confidence ?? null,
          tier: item.tier ?? "T2_semantic",
          suggestions: Array.isArray(item.suggestions)
            ? item.suggestions.map((s: unknown) =>
                isRecord(s) ? String(s.target ?? s) : String(s),
              )
            : [],
          sample_values: Array.isArray(item.sample_values) ? item.sample_values : [],
        });
      }
      if (list.length) tableToFlagged.set(tbl, list);
    }
  }

  if (unmappableByTable) {
    for (const [tbl, items] of Object.entries(unmappableByTable)) {
      if (!Array.isArray(items)) continue;
      const list = tableToUnmapped.get(tbl) ?? [];
      for (const item of items) {
        const field =
          typeof item === "string"
            ? item
            : isRecord(item)
              ? String(item.source_field ?? item.field_name ?? "")
              : "";
        if (!field) continue;
        list.push({ source_field: field, source_table: tbl });
      }
      if (list.length) tableToUnmapped.set(tbl, list);
    }
  }
}

/** Fallback plenum_cafm targets when the API omits existing_canonical_tables (streamlit defaults). */
export const DEFAULT_PLENUM_CANONICAL_TABLES = [
  "locations",
  "sites",
  "assets",
  "work_orders",
  "resources",
  "vendors",
  "parts",
  "users",
  "preventive_maintenance",
  "inspections",
];

function collectExistingCanonicalTables(
  migration: MigrationStatusResponse,
  flagged: Record<string, unknown[]>,
  unmapped: Record<string, unknown[]>,
): string[] {
  const tables = new Set<string>(DEFAULT_PLENUM_CANONICAL_TABLES);

  for (const node of migration.nodes ?? []) {
    const output = node.output;
    if (!isRecord(output)) continue;
    const tier1 = output.tier1_mappings_by_table;
    if (isRecord(tier1)) {
      for (const key of Object.keys(tier1)) tables.add(key);
    }
    const canonical = output.existing_canonical_tables;
    if (Array.isArray(canonical)) {
      for (const t of canonical) {
        if (typeof t === "string" && t.trim()) tables.add(t.trim());
      }
    }
  }

  const pending = migration.pending_gate_payload;
  if (isRecord(pending)) {
    const rec = pending as Record<string, unknown>;
    const canonical = rec.existing_canonical_tables;
    if (Array.isArray(canonical)) {
      for (const t of canonical) {
        if (typeof t === "string" && t.trim()) tables.add(t.trim());
      }
    }
  }

  if (!tables.size) {
    for (const key of [...Object.keys(flagged), ...Object.keys(unmapped)]) {
      if (key.trim()) tables.add(key.trim());
    }
  }

  return [...tables].sort();
}

/** Build field-mapping gate payload from status poll (backend may skip awaiting_review gate). */
export function buildFieldMappingPayloadFromMigration(
  migration: MigrationStatusResponse | null | undefined,
): FieldMappingPayloadShape | null {
  if (!migration) return null;

  const pendingPayload = migration.pending_gate_payload;
  if (isFieldMappingPayload(pendingPayload)) {
    const rec = pendingPayload as Record<string, unknown>;
    const normalized = normalizeFieldMappingGatePayload(rec);
    const { flagged_by_table: flagged, unmapped_by_table: unmapped } = normalized;
    return {
      flagged_by_table: flagged,
      unmapped_by_table: unmapped,
      existing_canonical_tables:
        normalized.existing_canonical_tables.length > 0
          ? normalized.existing_canonical_tables
          : collectExistingCanonicalTables(migration, flagged, unmapped),
      table_routing: normalized.table_routing,
    };
  }

  const tableToFlagged = new Map<string, Array<Record<string, unknown>>>();
  const tableToUnmapped = new Map<string, Array<Record<string, unknown>>>();

  const pendingRec: Record<string, unknown> | null =
    pendingPayload && typeof pendingPayload === "object" && !Array.isArray(pendingPayload)
      ? (pendingPayload as Record<string, unknown>)
      : null;
  const semanticResults = pendingRec?.semantic_results;
  if (
    migration.status === "step_paused" &&
    isSemanticMappingStepKey(
      typeof migration.pending_gate_type === "string" ? migration.pending_gate_type : null,
    ) &&
    Array.isArray(semanticResults)
  ) {
    ingestSemanticResults(tableToFlagged, tableToUnmapped, semanticResults);
  }

  // Tier-2 flagged/unmappable buckets live in the semantic step_pause payload itself
  // (not always echoed into the node_log output). Ingest them here so an
  // unmappable-only semantic result still builds a non-empty field-mapping payload —
  // otherwise the editable gate (create new column + data type) is skipped.
  if (pendingRec) {
    ingestTier2Tables(
      tableToFlagged,
      tableToUnmapped,
      pendingRec.tier2_flagged_by_table as Record<string, unknown> | undefined,
      pendingRec.tier2_unmappable_by_table as Record<string, unknown> | undefined,
    );
  }

  const semanticNode = findSemanticMappingNode(migration);
  if (semanticNode?.output && isRecord(semanticNode.output)) {
    const output = semanticNode.output;
    if (Array.isArray(output.semantic_results)) {
      ingestSemanticResults(tableToFlagged, tableToUnmapped, output.semantic_results);
    }
    ingestTier2Tables(
      tableToFlagged,
      tableToUnmapped,
      output.tier2_flagged_by_table as Record<string, unknown> | undefined,
      output.tier2_unmappable_by_table as Record<string, unknown> | undefined,
    );
  }

  const tableToFields = new Map<string, Set<string>>();
  let currentTable: string | null = null;
  for (const node of migration.nodes ?? []) {
    const logs = Array.isArray(node.logs) ? node.logs : [];
    for (const rawLine of logs) {
      const line = rawLine.trim();
      const tableMatch =
        line.match(/\[Node\s*\d+\]\s*(?:►\s*)?Processing table\s+(.+?):/i) ??
        line.match(/\[Node\s*\d+\]\s*►\s*Processing source table:\s*(.+)$/i);
      if (tableMatch?.[1]) {
        currentTable = tableMatch[1].trim();
        continue;
      }
      const noMatch = line.match(/\[Node\s*\d+\]\s*No matches for\s+(.+)$/i);
      if (noMatch?.[1]) {
        pushField(tableToFields, currentTable ?? "default", noMatch[1]);
        continue;
      }
      const unresolved = line.match(/\[Node\s*\d+\]\s*Unresolved:\s+(.+)$/i);
      if (unresolved?.[1]) {
        pushField(tableToFields, currentTable ?? "default", unresolved[1]);
      }
    }
  }

  for (const [table, fields] of tableToFields) {
    const list = tableToUnmapped.get(table) ?? [];
    for (const field of fields) {
      if (list.some((x) => x.source_field === field)) continue;
      list.push({ source_field: field, source_table: table });
    }
    if (list.length) tableToUnmapped.set(table, list);
  }

  const flagged = Object.fromEntries(tableToFlagged);
  const unmapped = Object.fromEntries(tableToUnmapped);
  if (!Object.keys(flagged).length && !Object.keys(unmapped).length) return null;

  return normalizeFieldMappingGatePayload({
    flagged_by_table: flagged,
    unmapped_by_table: unmapped,
    existing_canonical_tables: collectExistingCanonicalTables(migration, flagged, unmapped),
    table_routing: pendingRec?.table_routing,
  });
}

/** Submit/defer controls for Field Structure Review (matches nishil migration-ui-phase). */
export function resolveFieldMappingGateControls(
  migration: MigrationStatusResponse | null | undefined,
  fieldMappingDismissed: boolean,
  semanticMappingDismissed: boolean,
): {
  show: boolean;
  payload: FieldMappingPayloadShape | null;
  submitReady: boolean;
  deferProceed: boolean;
} {
  if (!migration || fieldMappingDismissed) {
    return { show: false, payload: null, submitReady: false, deferProceed: false };
  }

  if (isPreSemanticGatePending(migration)) {
    return { show: false, payload: null, submitReady: false, deferProceed: false };
  }

  const pendingType =
    typeof migration.pending_gate_type === "string" ? migration.pending_gate_type : null;
  const status = String(migration.status ?? "").toLowerCase();

  // While step_3_semantic_mapping pause is active, only StepPause — not field-mapping gate.
  if (status === "step_paused" && isSemanticMappingStepKey(pendingType) && !semanticMappingDismissed) {
    return { show: false, payload: null, submitReady: false, deferProceed: false };
  }

  if (isStepPauseBlockingFieldMapping(migration, semanticMappingDismissed)) {
    return { show: false, payload: null, submitReady: false, deferProceed: false };
  }

  const payload = buildFieldMappingPayloadFromMigration(migration);
  const reviewCount = countFieldMappingReviewItems(payload);
  const submitReady = isFieldMappingGateSubmitReady(migration);
  const normalized = normalizeMigrationGateType(pendingType);
  const semanticDone = isSemanticReviewCompleteForFieldMapping(migration, semanticMappingDismissed);

  if (!payload || reviewCount === 0) {
    if (submitReady && payload) {
      return { show: true, payload, submitReady: true, deferProceed: false };
    }
    return { show: false, payload: null, submitReady: false, deferProceed: false };
  }

  if (!semanticDone) {
    return { show: false, payload: null, submitReady: false, deferProceed: false };
  }

  if (submitReady) {
    return { show: true, payload, submitReady: true, deferProceed: false };
  }

  if (normalized === "field_mapping") {
    return { show: true, payload, submitReady, deferProceed: !submitReady };
  }

  if (reviewCount > 0) {
    return { show: true, payload, submitReady: false, deferProceed: true };
  }

  if (requiresFieldMappingLatch(migration, fieldMappingDismissed, semanticMappingDismissed)) {
    return { show: true, payload, submitReady, deferProceed: !submitReady };
  }

  return { show: false, payload: null, submitReady: false, deferProceed: false };
}

export function countFieldMappingReviewItems(payload: unknown): number {
  if (!isRecord(payload)) return 0;
  const flaggedTable = isRecord(payload.flagged_by_table) ? payload.flagged_by_table : {};
  const unmappedRaw = payload.unmapped_by_table ?? payload.unmappable_items_by_table;
  const unmappedTable = isRecord(unmappedRaw) ? unmappedRaw : {};
  let flagged = 0;
  let unmapped = 0;
  for (const rows of Object.values(flaggedTable)) {
    if (Array.isArray(rows)) flagged += rows.length;
  }
  for (const rows of Object.values(unmappedTable)) {
    if (Array.isArray(rows)) unmapped += rows.length;
  }
  return flagged + unmapped;
}

/** User should review flagged/unmapped fields before preprocess (backend may skip Gate 1 when flagged=0). */
export function migrationNeedsFieldMappingReview(
  migration: MigrationStatusResponse | null | undefined,
  semanticMappingDismissed: boolean,
  fieldMappingDismissed: boolean,
): boolean {
  if (!migration || fieldMappingDismissed || !semanticMappingDismissed) return false;
  if (isFieldMappingGateSubmitReady(migration)) return true;
  const payload = buildFieldMappingPayloadFromMigration(migration);
  return countFieldMappingReviewItems(payload) > 0;
}

/** Hold Semantic Mapping (node 3) until the user clicks Continue on the step pause. */
export function requiresSemanticMappingLatch(
  migration: MigrationStatusResponse | null | undefined,
  semanticMappingDismissed: boolean,
): boolean {
  if (!migration || semanticMappingDismissed) return false;

  const st = String(migration.status ?? "").toLowerCase();
  if (st === "complete" || st === "failed" || st === "ddl_failed" || st === "error") return false;

  const pending = typeof migration.pending_gate_type === "string" ? migration.pending_gate_type : null;
  const normalized = normalizeMigrationGateType(pending);

  if (normalized === "field_mapping") return false;
  if (normalized === "pre_semantic") return false;

  if (st === "step_paused" && isSemanticMappingStepKey(pending)) return true;

  return false;
}

export function isFieldMappingPayload(v: unknown): v is Record<string, unknown> {
  if (!isRecord(v)) return false;
  const rec = v as Record<string, unknown>;
  if (isPreSemanticPayload(rec)) return false;
  if (isSemanticMappingPausePayload(rec)) return false;
  return (
    isRecord(rec.flagged_by_table) ||
    isRecord(rec.unmapped_by_table) ||
    isRecord(rec.unmappable_items_by_table) ||
    isRecord(rec.review_items_by_table) ||
    typeof rec.total_flagged === "number" ||
    Array.isArray(rec.tier2_flagged_mappings) ||
    Array.isArray(rec.tier2_unmappable) ||
    isRecord(rec.tier2_unmappable_by_table)
  );
}

export function isPreprocessStepPauseKey(pending: string | null | undefined): boolean {
  if (!pending) return false;
  const s = pending.toLowerCase();
  return (
    s.includes("preprocess") ||
    s === "step_5_preprocess" ||
    s === "step_5_preprocess_validate" ||
    s === "step_6_preprocess" ||
    s === "step_6_data_preprocessing"
  );
}

/** Active step_* pause — Field Mapping must not render until user clicks Continue on StepPause. */
export function isStepPauseBlockingFieldMapping(
  migration: MigrationStatusResponse | null | undefined,
  semanticMappingDismissed = true,
): boolean {
  if (!migration) return false;
  const st = String(migration.status ?? "").toLowerCase();
  if (st !== "step_paused") return false;
  const pending = typeof migration.pending_gate_type === "string" ? migration.pending_gate_type : null;
  if (!pending?.startsWith("step_")) return false;
  if (isSemanticMappingStepKey(pending) && !semanticMappingDismissed) return false;
  return true;
}

export function isPreprocessPausePayload(v: unknown): boolean {
  if (!isRecord(v)) return false;
  if (isFieldMappingPayload(v)) return false;
  const rec = v as Record<string, unknown>;
  return (
    Array.isArray(rec.tables) ||
    typeof rec.rows_cleaned === "number" ||
    typeof rec.warnings === "number" ||
    (typeof rec.label === "string" && rec.label.toLowerCase().includes("preprocess")) ||
    (typeof rec.label === "string" && rec.label.toLowerCase().includes("validate"))
  );
}

export function isFieldMappingGateNodeActive(migration: MigrationStatusResponse): boolean {
  return (migration.nodes ?? []).some((n) => {
    const name = String(n.node_name ?? "").toLowerCase();
    const isGate =
      (name.includes("field") && name.includes("mapping")) ||
      (name.includes("gate") && name.includes("field") && name.includes("map"));
    if (!isGate) return false;
    const st = String(n.status ?? "").toLowerCase();
    return st === "running" || st === "pending" || st === "paused" || st === "in_progress";
  });
}

export function unwrapGatePayload(input: unknown): Record<string, unknown> | null {
  if (!isRecord(input)) return null;
  if (isFieldMappingPayload(input)) return input;
  const rec = input as Record<string, unknown>;
  const nested = [rec.payload, rec.data, rec.gate_payload, rec.pending_gate_payload, rec.output];
  for (const c of nested) {
    if (isRecord(c) && isFieldMappingPayload(c)) return c;
  }
  return rec;
}

/** True when UI must hold Field Mapping Review until the user submits or acknowledges. */
export function requiresFieldMappingLatch(
  migration: MigrationStatusResponse | null | undefined,
  fieldMappingDismissed: boolean,
  semanticMappingDismissed = true,
): boolean {
  if (!migration || fieldMappingDismissed) return false;
  if (requiresSemanticMappingLatch(migration, semanticMappingDismissed)) return false;
  if (isStepPauseBlockingFieldMapping(migration, semanticMappingDismissed)) return false;

  if (migrationNeedsFieldMappingReview(migration, semanticMappingDismissed, fieldMappingDismissed)) {
    return true;
  }

  const st = String(migration.status ?? "").toLowerCase();
  if (st === "complete" || st === "failed" || st === "ddl_failed" || st === "error") return false;

  const pending = typeof migration.pending_gate_type === "string" ? migration.pending_gate_type : null;
  const normalized = normalizeMigrationGateType(pending);

  if (normalized === "field_mapping") return true;

  const pendingPayload = unwrapGatePayload(migration.pending_gate_payload);
  if (pendingPayload && isFieldMappingPayload(pendingPayload)) return true;

  const node5 = (migration.nodes ?? []).find((n) => n.node_id === 5);
  const node5Payload = unwrapGatePayload(node5?.output);
  if (node5Payload && isFieldMappingPayload(node5Payload)) return true;

  const atFieldMappingStage = (migration.current_step ?? 0) >= 5;

  if (st === "running" && atFieldMappingStage && isFieldMappingGateNodeActive(migration)) {
    return true;
  }

  return false;
}

export function isPrematurePreprocessPoll(
  migration: MigrationStatusResponse | null | undefined,
  fieldMappingDismissed: boolean,
  semanticMappingDismissed = true,
): boolean {
  if (!migration || fieldMappingDismissed) return false;
  if (isStepPauseBlockingFieldMapping(migration, semanticMappingDismissed)) return false;
  if (requiresSemanticMappingLatch(migration, semanticMappingDismissed)) return false;
  if (migrationNeedsFieldMappingReview(migration, semanticMappingDismissed, fieldMappingDismissed)) {
    return false;
  }
  const pending = typeof migration.pending_gate_type === "string" ? migration.pending_gate_type : null;
  if (!pending?.startsWith("step_") && !isPreprocessStepPauseKey(pending)) return false;
  return (
    requiresFieldMappingLatch(migration, false, semanticMappingDismissed) &&
    isPreprocessStepPauseKey(pending) &&
    isPreprocessPausePayload(migration.pending_gate_payload)
  );
}

export function migrationHasSemanticUnmappedSignals(migration: MigrationStatusResponse): boolean {
  const payload = buildFieldMappingPayloadFromMigration(migration);
  if (payload && countFieldMappingReviewItems(payload) > 0) return true;
  return (migration.nodes ?? []).some((n) => {
    const output = (n.output ?? {}) as Record<string, unknown>;
    const unresolved = typeof output.unresolved === "number" ? output.unresolved : 0;
    const unmappable = typeof output.unmappable === "number" ? output.unmappable : 0;
    const flagged = typeof output.tier2_flagged === "number" ? output.tier2_flagged : 0;
    if (unresolved > 0 || unmappable > 0 || flagged > 0) return true;
    return (n.logs ?? []).some((line) => {
      const s = String(line).toLowerCase();
      return s.includes("no matches for") || s.includes("unmappable") || s.includes("unresolved");
    });
  });
}

export function isFieldMappingGateSubmitReady(migration: MigrationStatusResponse | null | undefined): boolean {
  if (!migration) return false;
  const st = String(migration.status ?? "").toLowerCase();
  const normalized = normalizeMigrationGateType(
    typeof migration.pending_gate_type === "string" ? migration.pending_gate_type : null,
  );

  if (normalized === "field_mapping") {
    return st === "awaiting_review" || st === "running";
  }

  if (st !== "awaiting_review") return false;
  return isFieldMappingPayload(migration.pending_gate_payload);
}

export function fieldMappingSubmitBlockedReason(
  migration: MigrationStatusResponse | null | undefined,
  semanticMappingDismissed = true,
): string | null {
  if (!migration) return "Loading migration status…";
  if (isFieldMappingGateSubmitReady(migration)) return null;

  const payload = buildFieldMappingPayloadFromMigration(migration);
  const reviewCount = countFieldMappingReviewItems(payload);

  const st = String(migration.status ?? "").toLowerCase();
  const pending = typeof migration.pending_gate_type === "string" ? migration.pending_gate_type : null;

  const semanticDone = isSemanticReviewCompleteForFieldMapping(migration, semanticMappingDismissed);
  if (reviewCount > 0 && semanticDone && !isFieldMappingGateSubmitReady(migration)) {
    return (
      "The pipeline skipped the server field-mapping gate because there were no medium-confidence matches. " +
      "Review unmappable and flagged fields below, then use Continue to advance the pipeline (or Submit when unlocked). " +
      "Fields marked skip will not be migrated."
    );
  }

  if (st === "running" || st === "step_paused") {
    if (isPreprocessStepPauseKey(pending) || isPreprocessPausePayload(migration.pending_gate_payload)) {
      return "The pipeline is still preparing field mapping review. Submit will unlock automatically in a few seconds.";
    }
    return "The pipeline is still running. Submit will unlock when this review step is ready.";
  }

  if (st === "awaiting_review") {
    return `Waiting for the field mapping gate (current: ${pending ?? "unknown"}).`;
  }

  return `Submit is not available while status is ${migration.status}.`;
}

/** True when the server field-mapping HITL gate is open for submit. */
export function isFieldMappingGateOpen(
  migration: MigrationStatusResponse | null | undefined,
): boolean {
  if (!migration) return false;
  const st = String(migration.status ?? "").toLowerCase();
  if (st !== "awaiting_review" && st !== "running") return false;
  return normalizeMigrationGateType(migration.pending_gate_type) === "field_mapping";
}

/**
 * Pipeline continued without (or already past) the field-mapping gate — e.g. preprocess
 * step pause or Node 6 logs present while UI still shows semantic review.
 */
export function isPipelinePastFieldMappingGate(
  migration: MigrationStatusResponse | null | undefined,
): boolean {
  if (!migration) return false;

  const st = String(migration.status ?? "").toLowerCase();
  const pending =
    typeof migration.pending_gate_type === "string" ? migration.pending_gate_type : null;
  const gate = normalizeMigrationGateType(pending);

  if (gate === "hierarchy" || gate === "final_confirmation") return true;

  if (isPreprocessStepPauseKey(pending) && isPreprocessPausePayload(migration.pending_gate_payload)) {
    return true;
  }

  if ((migration.current_step ?? 0) >= 6) return true;

  for (const n of migration.nodes ?? []) {
    if (n.node_id !== 6) continue;
    const nodeSt = String(n.status ?? "").toLowerCase();
    if (nodeSt === "completed" || nodeSt === "complete" || nodeSt === "done") return true;
    if (n.output && isRecord(n.output)) return true;
    for (const line of n.logs ?? []) {
      if (/\[Node\s*6\].*rows after cleaning/i.test(String(line))) return true;
      if (/\[Node\s*6\].*Per table mapping dicts/i.test(String(line))) return true;
    }
  }

  if (st === "step_paused" && pending && !isSemanticMappingStepKey(pending) && gate !== "field_mapping") {
    if (gate === "pre_semantic") return false;
    if (pending.startsWith("step_") && !isSemanticMappingStepKey(pending)) return true;
  }

  if (st === "running" && (migration.current_step ?? 0) >= 5 && gate !== "field_mapping") {
    if (!isSemanticMappingStepKey(pending) && gate !== "pre_semantic") {
      const node6 = (migration.nodes ?? []).find((n) => n.node_id === 6);
      if (node6 && (node6.logs?.length || node6.output)) return true;
    }
  }

  return false;
}

export function shouldKeepPollingForFieldMappingGate(data: MigrationStatusResponse | undefined) {
  if (!data) return false;
  const st = String(data.status ?? "").toLowerCase();
  if (st !== "step_paused" && st !== "running") return false;

  const pending = String(data.pending_gate_type ?? "").toLowerCase();
  if (normalizeMigrationGateType(data.pending_gate_type) === "field_mapping") return false;
  if (isFieldMappingPayload(data.pending_gate_payload)) return false;

  // User is reviewing preprocess output — Continue advances the pipeline; no background poll storm.
  if (isPreprocessStepPauseKey(pending) && isPreprocessPausePayload(data.pending_gate_payload)) {
    return false;
  }

  if (!isPreprocessStepPauseKey(pending)) return false;

  return migrationHasSemanticUnmappedSignals(data) || isFieldMappingGateNodeActive(data);
}
