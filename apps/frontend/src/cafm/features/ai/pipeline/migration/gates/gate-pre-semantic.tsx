"use client";
import { useState, useRef, useEffect } from "react";
import { CheckCircle, ChevronDown, ChevronRight, ChevronUp, ArrowRight, Search } from "lucide-react";
import {
  useMigrationGatePreSemantic,
  useMigrationAdvance,
  schemaMapperApi,
  type MigrationGatePreSemanticRequest,
  type MigrationPreSemanticGatePayload,
  type MigrationPreSemanticReviewItem,
  type UdrTableResolution,
  type UdrColumnIntelligence,
} from "../../../chat-api";
import { MigrationCanonicalTableSelect } from "../migration-canonical-table-select";
import {
  DocumentSummary,
  type DocumentInventory,
  TableResolutionPanels,
  ColumnIntelligencePanels,
} from "../migration-metadata-view";
import { usePipelineStageScrollSpy, pulsePipelineStage, setColumnStagesRevealed } from "../../pipeline-stage-link";
import { plainTierWord } from "../migration-mapping-utils";

/** SQL types offered for new-table columns. */
const DATA_TYPES = [
  "VARCHAR(255)", "TEXT", "INTEGER", "BIGINT", "NUMERIC", "BOOLEAN", "DATE", "TIMESTAMP",
];

/** Best-guess SQL type from a column name (editable in the dropdown). */
function inferDataType(field: string): string {
  const f = (field || "").toLowerCase();
  if (/(timestamp|_at\b|datetime|created|updated|modified)/.test(f)) return "TIMESTAMP";
  if (/(date|_dt\b|dob|dtm)/.test(f)) return "DATE";
  if (/(is_|^is\b|bool|flag|active|enabled|deactivated)/.test(f)) return "BOOLEAN";
  if (/(amount|price|cost|total|rate|latitude|longitude|lat\b|lon\b|balance|qty|quantity)/.test(f)) return "NUMERIC";
  if (/(_id\b|^id$|count|number|_no\b|^num)/.test(f)) return "INTEGER";
  return "VARCHAR(255)";
}

// Generic tokens that carry no entity meaning — a shared "id" / "code" must NOT
// make engineer_id look like a fit for organization_id. Ignored when scoring token
// overlap (but exact-name and substring matches still win regardless).
const GENERIC_COL_TOKENS = new Set(["id", "code", "ref", "no", "num", "key", "pk", "fk", "uid", "type"]);

function colTokens(s: string): string[] {
  return (s || "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
}

/**
 * Heuristic similarity between a source column name and a candidate target column
 * (0..1). Used to PROPOSE target-table columns the instant the user routes a
 * source table to an existing CAFM table — so e.g. Resources→vendors suggests
 * full_name→vendor_name and email→email instead of dumping everything to semantic.
 *   exact (normalized) ............... 1.00   (full_name == full_name)
 *   substring (both ≥4, ≥60% len) .... 0.85   (vendor_name ⊃ name is rejected by len ratio)
 *   meaningful-token Jaccard ......... 0.50–0.95 (full_name↔vendor_name share "name")
 *   no meaningful overlap ............ 0      (engineer_id↔organization_id → semantic)
 */
function scoreColumnFit(source: string, target: string): number {
  const a = (source || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  const b = (target || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  if (!a || !b) return 0;
  if (a === b) return 1;
  if (a.length >= 4 && b.length >= 4 && (a.includes(b) || b.includes(a))) {
    const lo = Math.min(a.length, b.length);
    const hi = Math.max(a.length, b.length);
    if (lo / hi >= 0.6) return 0.85;
  }
  const ta = colTokens(source).filter((t) => !GENERIC_COL_TOKENS.has(t));
  const tb = colTokens(target).filter((t) => !GENERIC_COL_TOKENS.has(t));
  if (ta.length && tb.length) {
    const sa = new Set(ta);
    const sb = new Set(tb);
    let inter = 0;
    for (const t of sa) if (sb.has(t)) inter++;
    if (inter > 0) {
      const jac = inter / (sa.size + sb.size - inter);
      return Math.min(0.95, 0.5 + 0.45 * jac);
    }
  }
  return 0;
}

/**
 * Table-NAME similarity 0..1 (exact → 1, plural-tolerant → 0.97, else character-bigram Dice).
 * Used to rank the top-3 canonical target tables for the "Deterministic exact matching" step.
 */
function tableNameSimilarity(a: string, b: string): number {
  const na = (a || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  const nb = (b || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  if (!na || !nb) return 0;
  if (na === nb) return 1;
  const sa = na.endsWith("s") && na.length > 3 ? na.slice(0, -1) : na;
  const sb = nb.endsWith("s") && nb.length > 3 ? nb.slice(0, -1) : nb;
  if (sa === sb) return 0.97;
  const bigrams = (s: string) => {
    const g = new Set<string>();
    for (let i = 0; i < s.length - 1; i++) g.add(s.slice(i, i + 2));
    return g;
  };
  const ga = bigrams(na);
  const gb = bigrams(nb);
  if (!ga.size || !gb.size) return 0;
  let inter = 0;
  for (const x of ga) if (gb.has(x)) inter++;
  return (2 * inter) / (ga.size + gb.size);
}

/** Top-N canonical tables ranked by {@link tableNameSimilarity} (above a floor). */
function rankTableNameMatches(source: string, canonical: string[], n = 3, floor = 0.34) {
  return canonical
    .map((t) => ({ table: t, pct: tableNameSimilarity(source, t) }))
    .filter((m) => m.pct >= floor)
    .sort((x, y) => y.pct - x.pct)
    .slice(0, n);
}

/**
 * Top-N target columns ranked by {@link scoreColumnFit} (positive scores only).
 * A source column with no real match in the target table (e.g. `trade` against
 * `vendors`) returns [] — so no 0% "fit" chips are shown; the user picks from the
 * full target-column dropdown instead.
 */
function rankColumnFits(source: string, targetCols: string[], n = 3): Array<{ target_field: string; confidence: number }> {
  return targetCols
    .map((c) => ({ target_field: c, confidence: scoreColumnFit(source, c) }))
    .filter((x) => x.confidence > 0)
    .sort((x, y) => y.confidence - x.confidence)
    .slice(0, n);
}

/** A column scoring at/above this is auto-proposed (approve + retarget); below routes to semantic. */
const COLUMN_FIT_THRESHOLD = 0.6;

interface Props {
  migrationId: string;
  payload: MigrationPreSemanticGatePayload;
  onSubmitted: (snapshot?: {
    gate: "pre_semantic";
    payload: MigrationPreSemanticGatePayload;
    decisions: Record<string, Array<{ source_field: string; decision: "approve" | "semantic" }>>;
  }) => void;
  onFieldFocus?: (terms: string[]) => void;
  node2Output?: Record<string, unknown>;
  /** Hoistra's step-by-step card: the table analysis already has its own step there, so the
   *  column-matching pass shows only its decisions, with the column analysis one click away. */
  wizard?: boolean;
}

function TierBadge({ tier }: { tier: string }) {
  const map: Record<string, string> = {
    T1_exact:               "bg-green-100 text-green-800",
    T1_alias:               "bg-blue-100 text-blue-800",
    T1_regex:               "bg-purple-100 text-purple-800",
    T1_registry:            "bg-teal-100 text-teal-800",
    T1_llm:                 "bg-indigo-100 text-indigo-800",
    T1_token_containment:   "bg-amber-100 text-amber-800",
  };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${map[tier] ?? "bg-slate-100 text-slate-600"}`}>
      {plainTierWord(tier.replace("T1_", ""))}
    </span>
  );
}

export default function GatePreSemantic({ migrationId, payload, onSubmitted, onFieldFocus, node2Output, wizard = false }: Props) {
  // Cross-panel stage link: scrolling a step card here reveals its matching Activity-Log
  // entry on the right (see pipeline-stage-link.ts). The spy watches this panel's cards.
  const panelsRef = useRef<HTMLDivElement>(null);
  usePipelineStageScrollSpy(panelsRef);
  const reviewByTable = payload?.review_items_by_table ?? {};
  const allTables = Object.keys(reviewByTable);
  // Target dropdown lists ALL plenum_cafm tables (from the backend), not just the
  // source tables under review. Fall back to source tables if the list is empty.
  const canonicalTargetTables = (() => {
    const base =
      (payload?.existing_canonical_tables?.length ?? 0) > 0
        ? [...(payload?.existing_canonical_tables ?? [])]
        : [...allTables];
    // Also offer any FM-ontology "assign" routing-suggestion targets — existing CAFM tables the
    // backend matched semantically (e.g. technicians → technicians). Without this, such a sheet's
    // suggested target was neither selectable NOR defaulted (init gates on this list), so the
    // dropdown showed "— select table —" despite a 100% suggested match.
    for (const sug of Object.values(payload?.table_routing_suggestion_by_table ?? {})) {
      const t = (sug?.action === "assign" ? (sug?.target ?? "") : "").trim();
      if (t && !base.includes(t)) base.push(t);
    }
    return base;
  })();

  // Auto-canonical name per source column from the column-intelligence grouping
  // (e.g. "works.property_ref" → "site_id"). Lets an UNRESOLVED field default to a
  // canonical-matched destination column over a weak fuzzy suggestion — so Step 2 agrees
  // with B21's canonical-driven resolution (property_ref → site_id, not document_ids).
  // Read directly off the payload (not effectiveCanonical, which is defined later) so it's
  // safe to use in the counters / submit that run before that const.
  const autoCanonicalByKey: Record<string, string> =
    ((payload?.column_intelligence as { column_canonical?: Record<string, string> } | undefined)
      ?.column_canonical) ?? {};
  const canonicalColMatch = (tbl: string, field: string, cols: string[]): string => {
    const canon = autoCanonicalByKey[`${tbl}.${field}`];
    if (!canon) return "";
    const n = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
    return cols.find((c) => n(c) === n(canon)) ?? "";
  };

  // Unresolved fields from node 2 (automatically going to semantic, not in T1 review)
  const unresolvedByTable: Record<string, string[]> = (() => {
    const out: Record<string, string[]> = {};
    // Prefer the node-2 output; fall back to the gate payload (step_pause also carries
    // unresolved_by_table) so the unmatched count/list always shows even if node2Output lags.
    const raw =
      node2Output?.unresolved_by_table ??
      node2Output?.tier2_unmappable_by_table ??
      (payload as Record<string, unknown> | undefined)?.unresolved_by_table;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return out;
    for (const [tbl, list] of Object.entries(raw as Record<string, unknown>)) {
      if (!Array.isArray(list)) continue;
      // De-dupe against the T1 review items. On a RE-RUN the now-existing table makes
      // the deterministic matcher match a column (table_exact → T1), but the same
      // field can still be carried in unresolved_by_table — which rendered the row
      // TWICE (mapped AND "→ semantic") and could double-submit the column. A field
      // already shown as a T1 review item must not also appear as unresolved.
      // De-dup unresolved against BOTH the T1 review items AND the auto-approved matches, so a
      // field already counted as approved (review or auto) is never ALSO counted as "Auto → Semantic"
      // — the column totals must reconcile to the distinct source-column set (CAFM-012).
      const approvedFields = new Set<string>([
        ...(reviewByTable[tbl] ?? []).map((i) => i.source_field),
        ...(payload?.auto_approved_by_table?.[tbl] ?? []).map((a) => a.source_field),
      ]);
      out[tbl] = (list as unknown[]).filter(
        (x): x is string => typeof x === "string" && !approvedFields.has(x),
      );
    }
    return out;
  })();
  const totalAutoSemantic = Object.values(unresolvedByTable).reduce((s, arr) => s + arr.length, 0);
  // Every source sheet (T1-reviewable and/or unresolved) — Step 1 must let the user
  // confirm a target table for ALL of them, not just the ones with T1 matches.
  const allSourceTables = Array.from(new Set([...allTables, ...Object.keys(unresolvedByTable)]));

  // Duplicate table groups (work_order + workorders share wo_id) — used to collapse the Step-1
  // routing list to ONE card per group and to route every member together.
  const dupGroups = payload?.table_resolution?.duplicate_tables?.groups ?? [];
  const dupGroupByTable = new Map<string, (typeof dupGroups)[number]>();
  for (const g of dupGroups) for (const t of g.tables) dupGroupByTable.set(t, g);
  // Collapsed routing list: one representative per duplicate group (first-seen member), plus all
  // non-duplicate sheets — so Step 1 shows "work_order ×2" once instead of two routing cards.
  const routingTables = (() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const t of allSourceTables) {
      const g = dupGroupByTable.get(t);
      if (g) {
        const key = g.tables.join("|");
        if (seen.has(key)) continue;
        seen.add(key);
      }
      out.push(t);
    }
    return out;
  })();

  // Per-source-table top-3 candidate destinations, split by method — surfaced BOTH in Step 1
  // and in the B11.1 "Semantic table mapping" report card (passed into TableResolutionPanels).
  const tableTopMatches: Record<
    string,
    { exact: Array<{ table: string; pct: number }>; semantic: Array<{ table: string; pct: number; mapped?: number; total?: number }> }
  > = {};
  for (const t of allSourceTables) {
    const exact = rankTableNameMatches(t, canonicalTargetTables, 3);
    let semantic: Array<{ table: string; pct: number; mapped?: number; total?: number }> = (
      payload?.table_match_candidates_by_table?.[t] ?? []
    )
      .filter((c) => c.table && canonicalTargetTables.includes(c.table))
      .slice(0, 3)
      .map((c) => ({ table: c.table, pct: c.pct ?? 0, mapped: c.mapped, total: c.total }));
    // Column-overlap can be empty for a table that matched by NAME but whose columns use a
    // different vocabulary than the canonical schema (e.g. technicians: tech_id / primary_skill /
    // shift). Fall back to its resolved destination + match confidence so the row isn't blank.
    if (semantic.length === 0) {
      const routed = (payload?.suggested_target_by_table?.[t] ?? "").trim();
      const conf = payload?.table_match_confidence_by_table?.[t];
      if (routed && canonicalTargetTables.includes(routed) && typeof conf === "number" && conf > 0) {
        semantic = [{ table: routed, pct: conf }];
      } else if (exact.length) {
        semantic = [exact[0]]; // last resort — the top name match
      }
    }
    tableTopMatches[t] = { exact, semantic };
  }

  const TIER_COLORS: Record<string, string> = {
    T1_exact:    "bg-green-100 text-green-800",
    T1_alias:    "bg-blue-100 text-blue-800",
    T1_variation:"bg-teal-100 text-teal-800",
    T1_regex:    "bg-purple-100 text-purple-800",
    T1_registry: "bg-teal-100 text-teal-800",
    T1_llm:      "bg-indigo-100 text-indigo-800",
  };
  const tierCounts: Record<string, number> = {};
  for (const items of Object.values(reviewByTable)) {
    for (const item of items) {
      const t = item.tier ?? "other";
      tierCounts[t] = (tierCounts[t] ?? 0) + 1;
    }
  }

  const [focusedTerm, setFocusedTerm] = useState<string | null>(null);

  function handleTermClick(term: string) {
    const next = focusedTerm === term ? null : term;
    setFocusedTerm(next);
    onFieldFocus?.(next ? [next] : []);
  }

  // Every reviewable mapping is PRE-SELECTED as "approve" (the match is already chosen) —
  // the user can flip any to "semantic" to send it to the AI instead. Nothing is hidden or
  // forced; approve is just the default the user can change.
  const [decisions, setDecisions] = useState<Record<string, "approve" | "semantic">>(() => {
    const init: Record<string, "approve" | "semantic"> = {};
    for (const [tbl, items] of Object.entries(reviewByTable)) {
      for (const item of items) {
        init[`${tbl}.${item.source_field}`] = "approve";
      }
    }
    return init;
  });
  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set(allTables));
  const [error, setError] = useState<string | null>(null);
  const [isPreflighting, setIsPreflighting] = useState(false);
  // Primary keys are confirmed in the separate PK-approval gate (Group A) BEFORE this gate, so
  // this (Group B) gate always shows routing immediately.
  const showRouting = true;
  // WP-5 Node 2: rename/create the target table per source table, and rename target columns.
  // A source table is classified as NEW unless the mapper's suggested target is a
  // real existing canonical table. Defaulting unmatched tables to isNew=false used
  // to route them through column-matching against nonexistent schema (e.g. a source
  // "Transport" was forced through "match Transport columns against the canonical
  // Transport table" even though no such canonical table existed).
  // Default routing target for one source sheet — an existing CAFM table when the backend matched
  // one (deterministically OR via an FM-ontology "assign" suggestion), else a new table named for
  // the FM entity, else the source sheet name. Shared by the initial state AND the sync effect
  // below, so a sheet that only appears in a LATER poll still gets its suggested target defaulted
  // instead of rendering "— select table —" (the reported technicians bug).
  const computeDefaultTarget = (tbl: string): { target: string; isNew: boolean } => {
    const guess = (payload?.suggested_target_by_table?.[tbl] ?? "").trim();
    if (guess.length > 0 && canonicalTargetTables.includes(guess)) {
      return { target: guess, isNew: false }; // existing entity → schema comparison + column mapping
    }
    // Fall back to the FM/semantic routing suggestion (7.4 AC6): assign an existing suggested CAFM
    // table (Resources → technicians), else create a new table named for the FM entity, else the
    // source sheet name — never the (non-existent) source-name table as a bogus canonical.
    const sug = payload?.table_routing_suggestion_by_table?.[tbl];
    if (sug?.action === "assign" && sug.target && canonicalTargetTables.includes(sug.target)) {
      return { target: sug.target, isNew: false };
    }
    if (sug?.action === "create" && (sug.suggested_new_name ?? "").trim()) {
      return { target: toSnakeCase(sug.suggested_new_name as string), isNew: true };
    }
    return { target: toSnakeCase(tbl), isNew: true };
  };
  const [tableTargets, setTableTargets] = useState<Record<string, { target: string; isNew: boolean }>>(() => {
    const init: Record<string, { target: string; isNew: boolean }> = {};
    for (const tbl of allSourceTables) init[tbl] = computeDefaultTarget(tbl);
    return init;
  });
  // The gate payload can arrive incrementally (a sheet appears on a later poll). The one-time
  // init above misses those, leaving their dropdown at "— select table —" despite a suggested
  // match. Fill in defaults for any source sheet MISSING from tableTargets — never overriding a
  // target the user already chose.
  const _sourceTablesKey = allSourceTables.join("|");
  useEffect(() => {
    setTableTargets((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const tbl of allSourceTables) {
        if (next[tbl] !== undefined) continue;
        next[tbl] = computeDefaultTarget(tbl);
        changed = true;
      }
      return changed ? next : prev;
    });
  }, [_sourceTablesKey]);
  const [fieldRenames, setFieldRenames] = useState<Record<string, string>>({});
  // HITL canonical-name overrides keyed by B19.1 group_id ('G2') or column
  // prefixed_key ('vendors.id'). Sent to the backend on gate advance so every
  // downstream step (B14/B17/B18/B19/B20/B21 + writer / RAG / work-cloud) reads
  // the user's pinned canonical instead of the auto-derived one.
  const [canonicalOverrides, setCanonicalOverrides] = useState<Record<string, string>>({});
  const handleCanonicalChange = (groupId: string, newName: string) => {
    setCanonicalOverrides((prev) => {
      const next = { ...prev };
      if (newName) next[groupId] = newName;
      else delete next[groupId];
      return next;
    });
  };
  // SQL type chosen for each NEW-table column. key `${tbl}.${field}` → data type.
  const [newColumnTypes, setNewColumnTypes] = useState<Record<string, string>>({});
  // Manual target-column assignment for unresolved ("left-out") source fields.
  // key `${tbl}.${field}` → leftover column name, or "" to send to semantic.
  const [unresolvedAssign, setUnresolvedAssign] = useState<Record<string, string>>({});

  // Columns of every candidate target table — lets us re-match a source table's
  // columns the moment the user picks a different target table.
  const canonicalColumnsByTable = payload?.canonical_columns_by_table ?? {};
  // remapByTable[srcTable][sourceField] = { target column in the chosen table, matched? }.
  // Present only for tables whose target the user changed to another EXISTING table.
  const [remapByTable, setRemapByTable] = useState<
    Record<string, Record<string, { target: string; matched: boolean }>>
  >({});
  // Re-ranked column fits against the chosen target table, per source field —
  // replaces the backend's stale `item.candidates` (computed against the original
  // routing) so the "fits" chips show real columns of the NEW target table.
  const [remapCandidatesByTable, setRemapCandidatesByTable] = useState<
    Record<string, Record<string, Array<{ target_field: string; confidence: number }>>>
  >({});

  function normalizeCol(s: string): string {
    return (s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  }

  /**
   * snake_case a source column name for use as the target column of a NEW
   * table. Strips parenthesised units, converts CamelCase / space-separated
   * to underscores, collapses repeats, removes leading/trailing underscores.
   *   "Trip ID"               → "trip_id"
   *   "Travel Date"           → "travel_date"
   *   "Distance (km)"         → "distance_km"
   *   "Total Trip Cost (AED)" → "total_trip_cost_aed"
   *   "Driver-Name"           → "driver_name"
   */
  function toSnakeCase(s: string): string {
    const raw = (s ?? "").toString();
    // Lift parenthesised tokens into the name ("Distance (km)" → "Distance km")
    const lifted = raw.replace(/\(([^)]+)\)/g, " $1 ");
    // CamelCase / lowerUpper boundary
    const split = lifted.replace(/([a-z0-9])([A-Z])/g, "$1_$2");
    return split
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/_+/g, "_")
      .replace(/^_+|_+$/g, "") || "col";
  }

  /**
   * Apply a target-table choice for one source table. When the target is an
   * existing table we re-match each reviewed source column against THAT table's
   * columns: exact (normalized) hits stay "approve" and retarget to the real
   * column name; columns with no counterpart flip to "semantic" so they get
   * resolved downstream. If the table's columns aren't known (or it's a new
   * table), we keep the existing matches untouched.
   */
  /**
   * Re-match a source table's reviewed columns against the columns of `target`
   * (an existing CAFM table). Exact (normalized) names map 1:1; otherwise the
   * closest column by {@link scoreColumnFit} is PROPOSED when it clears
   * {@link COLUMN_FIT_THRESHOLD}, else the field routes to semantic. Returns the
   * remap, rename/decision updates, and the top-3 fits per field for display.
   * Pure — callers apply the state. Returns null when columns are unknown.
   */
  function computeTableRemap(tbl: string, target: string) {
    const key =
      Object.keys(canonicalColumnsByTable).find((k) => k.toLowerCase() === target.toLowerCase()) ?? "";
    const cols = canonicalColumnsByTable[key] ?? [];
    if (cols.length === 0) return null;
    const byNorm = new Map(cols.map((c) => [normalizeCol(c), c]));

    const items = reviewByTable[tbl] ?? [];
    const remap: Record<string, { target: string; matched: boolean }> = {};
    const renameUpdates: Record<string, string> = {};
    const decisionUpdates: Record<string, "approve" | "semantic"> = {};
    const candidates: Record<string, Array<{ target_field: string; confidence: number }>> = {};
    for (const it of items) {
      const exact = byNorm.get(normalizeCol(it.source_field));
      const fits = rankColumnFits(it.source_field, cols, 3);
      candidates[it.source_field] = fits;
      // Exact name wins; else the best fuzzy fit above threshold; else semantic.
      const best = exact ?? (fits[0] && fits[0].confidence >= COLUMN_FIT_THRESHOLD ? fits[0].target_field : "");
      remap[it.source_field] = { target: best, matched: !!best };
      renameUpdates[`${tbl}.${it.source_field}`] = best || it.source_field;
      decisionUpdates[`${tbl}.${it.source_field}`] = best ? "approve" : "semantic";
    }
    return { remap, renameUpdates, decisionUpdates, candidates };
  }

  // Route a duplicate group together: applying a target to work_order also applies it to
  // workorders, so the same-entity sheets always share a destination.
  function applyTableTarget(tbl: string, target: string, isNew: boolean) {
    // Changing a routing target re-derives the table-resolution flow (buildGateTableResolution
    // below reads tableTargets), so the B9.1 deterministic + B12.1 final-decision cards update
    // live. Pulse the link so the RIGHT-hand "Deterministic table mapping" log is re-revealed +
    // flashed too — the user SEES the matching logic flow refresh right after the change.
    pulsePipelineStage("deterministic");
    const g = dupGroupByTable.get(tbl);
    const members = g ? g.tables : [tbl];
    for (const m of members) applyTableTargetSingle(m, target, isNew);
  }

  function applyTableTargetSingle(tbl: string, target: string, isNew: boolean) {
    setTableTargets((prev) => ({ ...prev, [tbl]: { target, isNew } }));

    const clearRemap = () => {
      setRemapByTable((prev) => {
        if (!prev[tbl]) return prev;
        const next = { ...prev };
        delete next[tbl];
        return next;
      });
      setRemapCandidatesByTable((prev) => {
        if (!prev[tbl]) return prev;
        const next = { ...prev };
        delete next[tbl];
        return next;
      });
    };

    if (isNew || !target) {
      clearRemap();
      return;
    }
    const computed = computeTableRemap(tbl, target);
    if (!computed) {
      // Columns unknown for this target — don't destroy existing matches.
      clearRemap();
      return;
    }
    setRemapByTable((prev) => ({ ...prev, [tbl]: computed.remap }));
    setRemapCandidatesByTable((prev) => ({ ...prev, [tbl]: computed.candidates }));
    setFieldRenames((prev) => ({ ...prev, ...computed.renameUpdates }));
    setDecisions((prev) => ({ ...prev, ...computed.decisionUpdates }));
  }

  // Did the user override a table's routing target? The backend column-intelligence /
  // table-resolution report (B12/B14/B21) is built with the ORIGINAL routing, so when the user
  // re-points a source table we must swap it for the live gate-derived version (buildGate…, which
  // reads tableTargets) — otherwise only Step 2 reflects the change while B12/B14/B21 stay stale.
  // tableTargets ONLY changes via applyTableTarget (user dropdown/click), never an effect, so the
  // first render captures the true backend-suggested baseline.
  const initialTableTargetsRef = useRef<Record<string, string> | null>(null);
  if (initialTableTargetsRef.current === null) {
    initialTableTargetsRef.current = Object.fromEntries(
      Object.entries(tableTargets).map(([k, v]) => [k, (v.target ?? "").trim()]),
    );
  }
  const tableOverrideActive = Object.entries(tableTargets).some(
    ([k, v]) => (v.target ?? "").trim() !== (initialTableTargetsRef.current?.[k] ?? (v.target ?? "").trim()),
  );

  // On first render, propose target columns for every source table the backend
  // already routed to an EXISTING CAFM table (e.g. Resources→vendors). Without
  // this the fits/remap only appear after the user manually re-picks the table,
  // leaving each column showing its source name with "→ semantic". Runs once.
  const didInitRemapRef = useRef(false);
  useEffect(() => {
    if (didInitRemapRef.current) return;
    if (!Object.keys(canonicalColumnsByTable).length) return;
    didInitRemapRef.current = true;
    const remaps: Record<string, Record<string, { target: string; matched: boolean }>> = {};
    const cands: Record<string, Record<string, Array<{ target_field: string; confidence: number }>>> = {};
    const renames: Record<string, string> = {};
    const decisionUpdates: Record<string, "approve" | "semantic"> = {};
    for (const [tbl, t] of Object.entries(tableTargets)) {
      if (t.isNew || !t.target) continue;
      // Only auto-remap when routed AWAY from the source-named table — i.e. the
      // chosen target differs from the source sheet, so the original per-column
      // matches don't apply. Same-name targets keep the backend's matches.
      if (normalizeCol(t.target) === normalizeCol(tbl)) continue;
      const key =
        Object.keys(canonicalColumnsByTable).find((k) => k.toLowerCase() === t.target.toLowerCase()) ?? "";
      const targetColSet = new Set((canonicalColumnsByTable[key] ?? []).map(normalizeCol));
      if (!targetColSet.size) continue;
      const items = reviewByTable[tbl] ?? [];
      if (!items.length) continue;
      // If the backend ALREADY mapped most columns to real columns of this target,
      // it routed here deterministically — keep its matches, don't second-guess.
      const alreadyValid = items.filter((it) => targetColSet.has(normalizeCol(it.target_field))).length;
      if (alreadyValid >= Math.ceil(items.length * 0.5)) continue;
      const computed = computeTableRemap(tbl, t.target);
      if (!computed) continue;
      remaps[tbl] = computed.remap;
      cands[tbl] = computed.candidates;
      Object.assign(renames, computed.renameUpdates);
      Object.assign(decisionUpdates, computed.decisionUpdates);
    }
    if (Object.keys(remaps).length) {
      setRemapByTable((prev) => ({ ...remaps, ...prev }));
      setRemapCandidatesByTable((prev) => ({ ...cands, ...prev }));
      setFieldRenames((prev) => ({ ...renames, ...prev }));
      setDecisions((prev) => ({ ...prev, ...decisionUpdates }));
    }
  }, [canonicalColumnsByTable]);

  const lastSubmitRef = useRef<{
    body: MigrationGatePreSemanticRequest;
    snapshot: { gate: "pre_semantic"; payload: MigrationPreSemanticGatePayload; decisions: Record<string, Array<{ source_field: string; decision: "approve" | "semantic" }>> };
  } | null>(null);

  const { mutate: submitGate, isPending } = useMigrationGatePreSemantic({
    onSuccess: () => onSubmitted(),
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Submission failed";
      setError(msg);
      if (/status:\s*(failed|ddl_failed)/i.test(msg)) onSubmitted();
    },
  });
  const { mutate: advance, isPending: isAdvancing } = useMigrationAdvance({
    onSuccess: () => {
      setError(null);
      const pending = lastSubmitRef.current;
      if (pending) submitGate({ migrationId, body: pending.body }, { onSuccess: () => onSubmitted(pending.snapshot) });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Pipeline advance failed";
      setError(`Pipeline advance failed: ${msg}`);
    },
  });

  function toggleTable(tbl: string) {
    setExpandedTables((prev) => {
      const next = new Set(prev);
      if (next.has(tbl)) next.delete(tbl);
      else next.add(tbl);
      return next;
    });
  }

  function setDecision(tbl: string, field: string, action: "approve" | "semantic") {
    setDecisions((prev) => {
      const next = { ...prev, [`${tbl}.${field}`]: action };
      // A collapsed duplicate group shows only its representative; keep the hidden members'
      // per-field decisions in sync so both sheets are written with the same verdict.
      for (const m of dupMembersByRep.get(tbl) ?? []) {
        if (m !== tbl) next[`${m}.${field}`] = action;
      }
      return next;
    });
  }

  function approveAll() {
    setDecisions((prev) => {
      const next = { ...prev };
      for (const [tbl, items] of Object.entries(reviewByTable)) {
        for (const item of items) next[`${tbl}.${item.source_field}`] = "approve";
      }
      return next;
    });
  }

  function handleSubmit() {
    const decisionsBody: MigrationGatePreSemanticRequest["decisions"] = {};
    for (const [tbl, items] of Object.entries(reviewByTable)) {
      const isNewTbl = !!tableTargets[tbl]?.isNew;
      decisionsBody[tbl] = items.map((item) => {
        const k = `${tbl}.${item.source_field}`;
        if (isNewTbl) {
          // NEW TABLE: deterministic — every source column becomes an
          // approved snake_cased column on the new table. The backend's T1
          // target_field (against an unrelated canonical) is discarded, and
          // semantic is never an option here. The user can still rename the
          // target via fieldRenames; otherwise we snake_case the source name.
          const explicit = (fieldRenames[k] ?? "").trim();
          return {
            source_field: item.source_field,
            decision: "approve" as const,
            target_field: explicit || toSnakeCase(item.source_field),
            data_type: newColumnTypes[k] ?? inferDataType(item.source_field),
          };
        }
        const renamed = (fieldRenames[k] ?? "").trim();
        return {
          source_field: item.source_field,
          decision: decisions[k] ?? "approve",
          ...(renamed && renamed !== item.target_field ? { target_field: renamed } : {}),
        };
      });
    }
    // Unresolved ("left-out") source fields.
    //   - Existing table: only emit if the user manually assigned a leftover
    //     target column; otherwise drop and let Tier 2 semantic find a match.
    //   - NEW table: auto-approve every unresolved field with its snake_cased
    //     name. Skipping them here would push them into semantic Tier 2 against
    //     a non-existent target, which is exactly the bug we're fixing.
    //
    // The "exactSug" fallback below mirrors the render-side default in the
    // unresolved-field dropdown (line ~849): when a leftover canonical column
    // has the same normalised name as the source field, the dropdown defaults
    // to that column AND shows the green "assigned" badge. Without the same
    // fallback here, a user who left the auto-default in place would see
    // "assigned" but the field would be SILENTLY dropped from the submission —
    // landing in unresolved_by_table and surfacing as "unmappable" at the
    // semantic step. This is the parenthesis-in-column-name bug ("Distance (km)"
    // normCol-matches a "distance_km" leftover column → looks assigned → was
    // never sent).
    const normColSubmit = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
    for (const [tbl, fields] of Object.entries(unresolvedByTable)) {
      const isNewTbl = !!tableTargets[tbl]?.isNew;
      // Compute the same leftoverCols set the render uses for this table so
      // the exactSug fallback resolves identically here.
      const confirmedTargetSubmit = tableTargets[tbl]?.target ?? tbl;
      const targetColsKeySubmit = Object.keys(canonicalColumnsByTable).find(
        (k) => k.toLowerCase() === confirmedTargetSubmit.toLowerCase(),
      );
      const targetColsSubmit = targetColsKeySubmit
        ? canonicalColumnsByTable[targetColsKeySubmit]
        : [];
      const itemsSubmit = reviewByTable[tbl] ?? [];
      const usedTargetsSubmit = new Set(
        itemsSubmit.map((it) =>
          normColSubmit(fieldRenames[`${tbl}.${it.source_field}`] ?? it.target_field),
        ),
      );
      const _leftoverColsSubmit = targetColsSubmit.filter(
        (c) => !usedTargetsSubmit.has(normColSubmit(c)),
      );
      for (const field of fields) {
        if (isNewTbl) {
          const explicit = (fieldRenames[`${tbl}.${field}`] ?? "").trim();
          (decisionsBody[tbl] ??= []).push({
            source_field: field,
            decision: "approve",
            target_field: explicit || toSnakeCase(field),
            data_type: newColumnTypes[`${tbl}.${field}`] ?? inferDataType(field),
          });
          continue;
        }
        // Existing table: approve ONLY on a DELIBERATE user assignment. An untouched unresolved
        // field is NOT auto-mapped by exact/canonical name here — it falls through to the Tier-2
        // semantic step, matching the counter + the row default (all three agree). A re-run's
        // exact-name columns are re-matched by Tier-2 semantic mapping rather than silently
        // auto-approved at this gate.
        const rawAssign = unresolvedAssign[`${tbl}.${field}`];
        const assigned = typeof rawAssign === "string" ? rawAssign.trim() : "";
        if (!assigned) continue;
        (decisionsBody[tbl] ??= []).push({
          source_field: field,
          decision: "approve",
          target_field: assigned,
        });
      }
    }
    // Build table_overrides from ALL source sheets (allSourceTables), not just the
    // ones with T1 review items (allTables). A brand-new table with zero T1 matches
    // — every field unresolved — lives only in unresolvedByTable, so iterating
    // allTables would silently drop its is_new_table override. Without it the backend
    // never flags the table as new: the create-table trigger is skipped (columns are
    // never created) and its fields fall through to the semantic step as unmapped.
    const tableOverrides: NonNullable<MigrationGatePreSemanticRequest["table_overrides"]> = {};
    for (const tbl of allSourceTables) {
      const t = tableTargets[tbl];
      const target = (t?.target ?? "").trim();
      if (!target) continue;
      if (t?.isNew || target !== tbl) {
        // A new table's name is snake_cased to match the column convention and to
        // be a safe unquoted Postgres identifier; an existing canonical target is
        // left exactly as-is (it already matches a real table name).
        tableOverrides[tbl] = {
          target_table: t?.isNew ? toSnakeCase(target) : target,
          is_new_table: !!t?.isNew,
        };
      }
    }
    // Primary keys were confirmed in the separate PK-approval gate (Group A); not sent here.
    const body: MigrationGatePreSemanticRequest = {
      decisions: decisionsBody,
      ...(Object.keys(tableOverrides).length ? { table_overrides: tableOverrides } : {}),
      ...(Object.keys(canonicalOverrides).length ? { canonical_overrides: canonicalOverrides } : {}),
    };
    const snapshot = { gate: "pre_semantic" as const, payload, decisions: decisionsBody };
    lastSubmitRef.current = { body, snapshot };
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
          setError(`Cannot submit pre-semantic decisions yet. Current migration status is: ${latestStatus}`);
          return;
        }
        const gateType = String(latest.pending_gate_type ?? "").toLowerCase();
        const isPreSemanticGate = gateType.includes("pre") && gateType.includes("semantic");
        if (!isPreSemanticGate) {
          setError(
            `Review step changed to '${latest.pending_gate_type ?? "unknown"}'. Please submit in the active gate.`,
          );
          return;
        }
        submitGate({ migrationId, body }, { onSuccess: () => onSubmitted(snapshot) });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Failed to verify migration status before submit";
        setError(`${msg}. Please retry to continue safely.`);
      })
      .finally(() => {
        setIsPreflighting(false);
      });
  }

  // Count decisions, but treat every column under an isNew table as approved
  // regardless of the in-memory `decisions` map state — those columns are
  // deterministically approved at submit time (T1_new_table) and never reach
  // semantic. Mirroring that here keeps the counters honest.
  let countApproved = 0;
  let countSemantic = 0;
  for (const [tbl, items] of Object.entries(reviewByTable)) {
    const isNewTbl = !!tableTargets[tbl]?.isNew;
    for (const it of items) {
      const k = `${tbl}.${it.source_field}`;
      if (isNewTbl) {
        countApproved += 1;
        continue;
      }
      const v = decisions[k] ?? "approve";
      if (v === "approve") countApproved += 1;
      else if (v === "semantic") countSemantic += 1;
    }
  }
  // Unresolved fields under new tables become auto-approved new columns. For existing tables, an
  // unresolved field is approved ONLY if the user DELIBERATELY assigns a leftover target column.
  // Exact / canonical name auto-matches now DEFAULT to Tier-2 semantic review, so an untouched
  // unresolved field stays counted ONCE — in the "→ semantic" total + its per-table badge — instead
  // of being double-counted as approved. Mirrors handleSubmit + the row default below (all agree).
  {
    for (const [tbl, fields] of Object.entries(unresolvedByTable)) {
      if (tableTargets[tbl]?.isNew) {
        countApproved += fields.length;
        continue;
      }
      for (const field of fields) {
        if ((unresolvedAssign[`${tbl}.${field}`] ?? "").trim()) countApproved += 1;
      }
    }
  }
  // CAFM-012 — auto-approved deterministic matches are approved T1 columns too. They were omitted
  // from the stat cards, so the column totals summed to one short of the analyzed count. Count them
  // (deduped against the review items, which they never overlap) so T1 Approved + → Semantic +
  // Auto → Semantic reconciles to the full distinct source-column set.
  for (const [tbl, items] of Object.entries(payload?.auto_approved_by_table ?? {})) {
    const reviewFields = new Set((reviewByTable[tbl] ?? []).map((i) => i.source_field));
    for (const it of items ?? []) {
      if (it?.source_field && !reviewFields.has(it.source_field)) countApproved += 1;
    }
  }
  const totalItems =
    Object.values(reviewByTable).flat().length +
    Object.entries(unresolvedByTable).reduce(
      (acc, [tbl, fields]) => acc + (tableTargets[tbl]?.isNew ? fields.length : 0),
      0,
    );
  const handleRefreshGate = () => {
    setError(null);
  };

  // Two-phase gate: Step 1 confirms every sheet → CAFM table; Step 2 reviews the columns of each
  // confirmed table. When the backend SPLIT this gate (locked_phase), lock to that single phase:
  // "tables" = the routing gate (pass 1, before B20/B14.1), "columns" = the column-matching gate
  // (pass 2, after B14.1). Absent = the legacy single gate that toggles client-side.
  const lockedPhase = payload?.locked_phase;
  const [phase, setPhase] = useState<"tables" | "columns">(lockedPhase ?? "tables");

  // Gate the column-mapping (B13→B21) Activity-Log stages behind "Confirm routing": HIDDEN while on
  // Step 1 (routing, phase==="tables"), revealed once the user advances to Step 2 (column matching,
  // phase==="columns"). This keeps the log from rendering column stages before the panel's Step-2
  // cards — the CAFM-001 execution-sequence requirement, chosen over CAFM-007's "show at Step 1" per
  // manager ruling. (CAFM-002's "sub-cards not loading" is thereby resolved AT Step 2, not Step 1.)
  // The module flag defaults false so nothing leaks before the gate mounts; reveal on unmount so the
  // gating never leaks into other views (completed runs, etc.).
  useEffect(() => {
    setColumnStagesRevealed(phase === "columns");
    return () => setColumnStagesRevealed(true);
  }, [phase]);

  // How each sheet arrived at its current target: exact name match, semantic
  // guess (name differs but routed to an existing table), auto-classified as a
  // brand-new table (no canonical match), or fully unset.
  function tableMatchType(tbl: string): "exact" | "semantic" | "new" | "none" {
    const t = tableTargets[tbl];
    if (t?.isNew) return "new";
    const target = (t?.target ?? "").trim();
    if (!target) return "none";
    const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
    const a = norm(tbl);
    const b = norm(target);
    const exact = a === b || a.replace(/s$/, "") === b.replace(/s$/, "");
    return exact ? "exact" : "semantic";
  }
  const routingComplete = allSourceTables.every((t) => (tableTargets[t]?.target ?? "").trim().length > 0);

  // B7.1 → B12.1 — the full table-resolution report driving the Step-1 panels. Prefer the
  // backend `table_resolution` (richest: real FM RAG/alias + PK diagnostics over the full
  // rows); otherwise derive an equivalent report from the gate payload so every step still
  // renders with its real per-table metadata, PK, samples and routing — no backend rebuild
  // required. (Client-side can't reproduce the FM-ontology RAG hits, so B10.1 lists the
  // tables that fell through deterministic with "no alias → next stage" until the server
  // report lands.)
  const buildGateTableResolution = (): UdrTableResolution => {
    const inv: Record<string, { rows: number; cols: number }> = {};
    for (const f of (payload as unknown as { document_inventory?: DocumentInventory }).document_inventory?.files ?? []) {
      for (const t of f.tables ?? []) inv[t.table_name] = { rows: t.row_count, cols: t.column_count };
    }
    const colsOf = (src: string) => {
      const items = [
        ...(payload.review_items_by_table?.[src] ?? []),
        ...(payload.auto_approved_by_table?.[src] ?? []),
      ];
      const seen = new Set<string>();
      const out: { name: string; samples: string[]; isPk: boolean }[] = [];
      for (const it of items) {
        const name = it.source_field;
        if (!name || seen.has(name)) continue;
        seen.add(name);
        out.push({ name, samples: (it.sample_values ?? []).slice(0, 3).map(String), isPk: !!it.is_primary_key });
      }
      for (const name of Object.keys(payload.unresolved_suggestion_by_table?.[src] ?? {})) {
        if (!seen.has(name)) { seen.add(name); out.push({ name, samples: [], isPk: false }); }
      }
      return out;
    };
    const cards: NonNullable<UdrTableResolution["metadata_cards"]> = [];
    const pk: NonNullable<UdrTableResolution["pk_detection"]> = [];
    const det: NonNullable<UdrTableResolution["deterministic"]> = [];
    const rag: NonNullable<UdrTableResolution["rag_alias"]> = [];
    const sem: NonNullable<UdrTableResolution["semantic"]> = [];
    const final: NonNullable<UdrTableResolution["final_decisions"]> = [];
    for (const src of allSourceTables) {
      const cols = colsOf(src);
      const pkName = cols.find((c) => c.isPk)?.name;
      const meta = inv[src];
      cards.push({
        table: src,
        primary_key: pkName ? [pkName] : [],
        primary_key_kind: pkName ? "natural" : "surrogate",
        column_count: meta?.cols ?? cols.length,
        row_count: meta?.rows ?? 0,
        samples: cols.map((c) => ({ column: c.name, values: c.samples })),
      });
      pk.push({
        table: src,
        kind: pkName ? "natural" : "surrogate",
        primary_key: pkName ? [pkName] : ["_udr_id"],
        uniqueness: 1,
        null_rate: 0,
        tie_break: pkName
          ? /(^|_)id$/i.test(pkName)
            ? "name rank *_id"
            : /(code|key|no|number)$/i.test(pkName)
              ? "name rank code/key"
              : "only candidate"
          : "no natural key — surrogate",
      });
      const target = (tableTargets[src]?.target ?? "").trim();
      const mt = tableMatchType(src);
      const backendConf = Number(payload.table_match_confidence_by_table?.[src] ?? 0);
      const conf = mt === "exact" ? 1 : mt === "none" ? 0 : backendConf > 0 ? backendConf : 0.85;
      if (mt === "exact") {
        det.push({ source: src, method: "exact", destination: target, confidence: 1, note: "exact / plural-tolerant name match" });
      } else {
        det.push({ source: src, method: "none", destination: null, confidence: null, note: target ? "no exact name match → next stage" : "no destination" });
      }
      if (mt !== "exact" && mt !== "new") {
        rag.push({ source: src, alias_hit: null, destination: null, confidence: null, alias_source: "no alias → AI matching (known-term matches are resolved on the server)" });
      }
      if (mt === "semantic") {
        sem.push({ source: src, signal: cols.map((c) => c.name).slice(0, 6).join(", "), destination: target || null, confidence: conf, method: "semantic LLM", band: conf >= 0.7 ? "suggested" : "review" });
      }
      final.push({
        source: src,
        destination: target || null,
        method: mt === "exact" ? "exact" : mt === "new" ? "new table" : mt === "semantic" ? "semantic LLM" : "unresolved",
        confidence: target ? conf : null,
      });
    }
    return {
      metadata_cards: cards,
      pairwise: { highest_pair: null, auto_consolidated: [], candidates: [], verdict: `${cards.length} source tables — name + metadata compared pairwise; all kept distinct.` },
      pk_detection: pk,
      deterministic: det,
      rag_alias: rag,
      semantic: sem,
      final_decisions: final,
      counts: { tables: cards.length, deterministic: det.filter((d) => d.method !== "none").length, rag_alias: 0, semantic: sem.length },
    };
  };
  // Available in BOTH phases — the table-resolution panels (B7–B12) stay visible after the
  // user confirms routing; Step 2 appends the Column Intelligence Pipeline below them.
  // Prefer the backend report (full-row), but once the user overrides a table's routing, use the
  // gate-derived version so the "Final per-source-table decisions" reflect the new target.
  const tableResolution: UdrTableResolution | null =
    (tableOverrideActive ? buildGateTableResolution() : payload?.table_resolution) ??
    (payload ? buildGateTableResolution() : null);

  // ── Table/column reconciliation against B7.1 (unique table identification) ────────────────
  // The review must account for EVERY table and column identified up front. The summary "Tables"
  // count used allTables (review-items only), so an all-semantic sheet like `technicians` (no T1
  // rows) was dropped from the count — "4 tables" showed as 3. Reconcile against the identified
  // set so the table count, and the per-table cards, always cover the full initial set.
  const identifiedTables = (tableResolution?.metadata_cards ?? [])
    .map((c) => c.table)
    .filter((t): t is string => !!t);
  // Every table that should appear = source tables under review ∪ everything B7.1 identified.
  const allShownTables = Array.from(new Set([...allSourceTables, ...identifiedTables]));

  // Collapse duplicate sheets (work_order + workorders) into ONE Step-2 section labelled ×N, the
  // same way Step-1 routing collapses them — they share identical columns and route together, so
  // showing both as separate tables is misleading. The representative is the first-seen member; the
  // hidden members' per-field decisions are mirrored from the representative (setDecision below).
  const dupRepByTable = new Map<string, string>();   // member table -> representative table
  const dupMembersByRep = new Map<string, string[]>(); // representative -> [all member tables]
  {
    const repOfGroup = new Map<string, string>();    // group key -> representative
    for (const t of allShownTables) {
      const g = dupGroupByTable.get(t);
      if (!g) continue;
      const key = g.tables.join("|");
      if (!repOfGroup.has(key)) repOfGroup.set(key, t);
      const rep = repOfGroup.get(key)!;
      dupRepByTable.set(t, rep);
      if (!dupMembersByRep.has(rep)) dupMembersByRep.set(rep, []);
      dupMembersByRep.get(rep)!.push(t);
    }
  }
  // Display list: non-duplicate tables + one representative per duplicate group.
  const shownTables = allShownTables.filter((t) => (dupRepByTable.get(t) ?? t) === t);
  // Identified column count per table (B7.1), for the per-card and global reconciliation.
  const identifiedColsByTable: Record<string, string[]> = {};
  for (const c of tableResolution?.metadata_cards ?? []) {
    if (c.table) identifiedColsByTable[c.table] = (c.samples ?? []).map((s) => s.column);
  }
  const totalIdentifiedCols = Object.values(identifiedColsByTable).reduce((n, cs) => n + cs.length, 0);
  // Global column reconciliation: how many identified columns are accounted for across ALL tables
  // (T1 review + auto-approved + unresolved + value-merged). Anything left is a column that was
  // dropped upstream — surfaced so Approved/semantic totals always reconcile to the initial count.
  const _accounted = new Set<string>();
  for (const tbl of allShownTables) {
    for (const it of reviewByTable[tbl] ?? []) {
      _accounted.add(`${tbl}.${it.source_field}`);
      for (const m of it.merged_source_fields ?? []) _accounted.add(`${tbl}.${m}`);
    }
    if (!tableTargets[tbl]?.isNew) {
      for (const a of payload?.auto_approved_by_table?.[tbl] ?? []) _accounted.add(`${tbl}.${a.source_field}`);
    }
    for (const u of unresolvedByTable[tbl] ?? []) _accounted.add(`${tbl}.${u}`);
  }
  let totalMissingCols = 0;
  for (const [tbl, cols] of Object.entries(identifiedColsByTable)) {
    for (const c of cols) if (c && !_accounted.has(`${tbl}.${c}`)) totalMissingCols += 1;
  }
  const tablesReconcile = allShownTables.length === identifiedTables.length || identifiedTables.length === 0;

  // B13.1→B21.1 — column-intelligence report for Step 2. Prefer the backend report (computed
  // over the FULL rows); otherwise derive an equivalent from the gate's per-column data
  // (review_items + auto_approved carry source_field · 5 samples · PK flag · target column ·
  // confidence) so every step renders without waiting on a backend rebuild. Grouping / format
  // gates are computed over the 5 sample values, so they approximate the server's full-row run.
  const buildGateColumnIntelligence = (): UdrColumnIntelligence => {
    type Col = { table: string; col: string; samples: string[]; isPk: boolean; dest?: string; conf?: number };
    const cols: Col[] = [];
    for (const src of allSourceTables) {
      const items = [
        ...(payload.review_items_by_table?.[src] ?? []),
        ...(payload.auto_approved_by_table?.[src] ?? []),
      ];
      const seen = new Set<string>();
      for (const it of items) {
        const name = it.source_field;
        if (!name || seen.has(name)) continue;
        seen.add(name);
        cols.push({
          table: src,
          col: name,
          samples: (it.sample_values ?? []).slice(0, 5).map(String),
          isPk: !!it.is_primary_key,
          // CAFM-007 — reflect the user's reroute/override (fieldRenames, keyed `${table}.${source_field}`)
          // so the B21 preview shows the NEW target's matched column, not the stale one carried in the
          // original payload; falls back to the payload's target_field when the user hasn't overridden.
          dest: ((fieldRenames[`${src}.${name}`] ?? it.target_field) || "").trim() || undefined,
          conf: typeof it.confidence === "number" ? it.confidence : undefined,
        });
      }
      // CAFM-012 — the unresolved (Auto → Semantic) fields are analyzed source columns too, so they
      // must be counted in columns_analyzed. They carry no samples / target yet (headed to semantic),
      // so they stay unmapped and never join a value/format group — just counted. Deduped (via `seen`)
      // against the review + auto-approved fields already added for this sheet.
      for (const name of unresolvedByTable[src] ?? []) {
        if (!name || seen.has(name)) continue;
        seen.add(name);
        cols.push({ table: src, col: name, samples: [], isPk: false });
      }
    }
    const destTable = (t: string) => (tableTargets[t]?.target ?? "").trim() || null;
    const cellFormat = (v: string): string => {
      const s = (v ?? "").trim();
      if (!s) return "empty";
      if (/^[-+]?\d{1,3}(,\d{3})+$/.test(s) || /^[-+]?\d+$/.test(s)) return "integer";
      if (/^[-+]?\d*\.\d+$/.test(s)) return "decimal";
      if (/^\d{4}-\d{2}-\d{2}/.test(s) || /\b\d{1,4}[/\-]\d{1,2}[/\-]\d{1,4}\b/.test(s)) return "date";
      if (/^(true|false|yes|no|y|n)$/i.test(s)) return "boolean";
      if (/^[A-Za-z]{1,8}[-_ ]?\d{2,}$/.test(s)) return "id_code";
      if (s.length <= 40 && !s.includes(" ")) return "categorical";
      return "free_text";
    };
    const distOf = (c: Col): Record<string, number> => {
      const counts: Record<string, number> = {};
      for (const v of c.samples) counts[cellFormat(v)] = (counts[cellFormat(v)] ?? 0) + 1;
      const t = c.samples.length || 1;
      const out: Record<string, number> = {};
      for (const k of Object.keys(counts)) out[k] = counts[k] / t;
      return out;
    };
    const dominant = (c: Col): string => {
      const d = distOf(c);
      let best = "empty";
      let n = -1;
      for (const k of Object.keys(d)) if (d[k] > n) { best = k; n = d[k]; }
      return best;
    };
    const formatSim = (a: Col, b: Col): number => {
      const da = distOf(a);
      const db = distOf(b);
      if ((da.empty && Object.keys(da).length === 1) || (db.empty && Object.keys(db).length === 1)) return 0;
      let s = 0;
      for (const k of Object.keys(da)) if (k in db) s += Math.min(da[k], db[k]);
      return s;
    };
    // VALUE-SHAPE skeleton — mirrors the backend's value_shape: runs of digit / upper /
    // lower collapse to a class char + count, punctuation kept literal. 'A-001' → 'A-#3',
    // 'S-01' → 'A-#2' — so two id_codes with different shapes no longer collide at B18.
    const valueShape = (value: string): string => {
      const s = (value ?? "").trim().slice(0, 60);
      if (!s) return "";
      let out = "";
      let i = 0;
      const cls = (c: string) => (c >= "0" && c <= "9" ? "#" : c >= "A" && c <= "Z" ? "A" : c >= "a" && c <= "z" ? "a" : null);
      while (i < s.length) {
        const k = cls(s[i]);
        if (k === null) { out += s[i]; i++; continue; }
        let j = i;
        while (j < s.length && cls(s[j]) === k) j++;
        const run = j - i;
        out += k + (run > 1 ? String(run) : "");
        i = j;
      }
      return out;
    };
    const shapeDist = (c: Col): Record<string, number> => {
      const vals = c.samples.filter((v) => (v ?? "").trim() !== "");
      if (!vals.length) return { "": 1 };
      const counts: Record<string, number> = {};
      for (const v of vals) { const sh = valueShape(v); counts[sh] = (counts[sh] ?? 0) + 1; }
      const out: Record<string, number> = {};
      for (const k of Object.keys(counts)) out[k] = counts[k] / vals.length;
      return out;
    };
    const dominantShape = (c: Col): string => {
      const d = shapeDist(c);
      let best = "";
      let n = -1;
      for (const k of Object.keys(d)) if (k && d[k] > n) { best = k; n = d[k]; }
      return best;
    };
    const valueShapeSim = (a: Col, b: Col): number => {
      const da = shapeDist(a);
      const db = shapeDist(b);
      if ((da[""] && Object.keys(da).length === 1) || (db[""] && Object.keys(db).length === 1)) return 0;
      let s = 0;
      for (const k of Object.keys(da)) if (k in db) s += Math.min(da[k], db[k]);
      return s;
    };
    const distinctVals = (c: Col): Set<string> =>
      new Set(c.samples.map((v) => (v ?? "").trim().toLowerCase()).filter(Boolean));
    const jaccard = (a: Col, b: Col): number => {
      const sa = distinctVals(a), sb = distinctVals(b);
      if (!sa.size || !sb.size) return 0;
      let inter = 0; sa.forEach((v) => { if (sb.has(v)) inter++; });
      const union = sa.size + sb.size - inter;
      return union ? inter / union : 0;
    };
    const containment = (a: Col, b: Col): number => {
      const sa = distinctVals(a), sb = distinctVals(b);
      if (!sa.size || !sb.size) return 0;
      let inter = 0; sa.forEach((v) => { if (sb.has(v)) inter++; });
      return Math.max(inter / sa.size, inter / sb.size);
    };
    // Single source of truth — mirrors backend column_pair_grouping: format gate (B17),
    // value-shape gate (B18), then a value-overlap gate so same-shape-but-disjoint domains
    // (free_text → Jaccard, id/number/date → FK→PK containment) stay apart.
    const VARIABLE_SHAPE = new Set(["free_text", "categorical"]);
    const OVERLAP_REQUIRED = new Set(["id_code", "integer", "decimal", "date"]);
    const pairGroup = (a: Col, b: Col) => {
      const fa = dominant(a), fb = dominant(b);
      const fmt = formatSim(a, b);
      const res = { fa, fb, fmt, formatPass: fmt >= 0.8, value: 0, group: false, overlap: null as number | null, overlapKind: null as string | null };
      if (!res.formatPass) return res;
      const v = valueShapeSim(a, b);
      res.value = v;
      if (v < 0.8) return res;
      if (VARIABLE_SHAPE.has(fa) && VARIABLE_SHAPE.has(fb)) {
        const ov = jaccard(a, b); res.overlap = ov; res.overlapKind = "jaccard"; res.group = ov >= 0.5;
      } else if (OVERLAP_REQUIRED.has(fa) && OVERLAP_REQUIRED.has(fb)) {
        const ov = containment(a, b); res.overlap = ov; res.overlapKind = "containment"; res.group = ov >= 0.5;
      } else {
        res.group = true;
      }
      return res;
    };
    // Match backend _prefixed(): lowercase table, dot-separated (e.g. 'sites.id').
    const pk = (c: Col) => `${c.table.toLowerCase()}.${c.col}`;
    // B17/B18 — cross-table + within-table pairwise gates + union-find edges
    const formatGate: UdrColumnIntelligence["format_gate"] = [];
    const valuePattern: UdrColumnIntelligence["value_pattern"] = [];
    const withinFormatGate: NonNullable<UdrColumnIntelligence["within_table_similarity"]>["format_gate"] = [];
    const withinValuePattern: NonNullable<UdrColumnIntelligence["within_table_similarity"]>["value_pattern"] = [];
    const edges: [number, number][] = [];
    let scored = 0;
    let withinScored = 0;
    let fmtSurvivors = 0;
    let withinFmtSurvivors = 0;
    let grouped = 0;
    let withinGrouped = 0;
    const pct = (n: number) => Math.round(n * 100);
    const pushPair = (
      i: number, j: number, sameTable: boolean,
      fmtOut: typeof formatGate, valOut: typeof valuePattern,
    ) => {
      const a = cols[i], b = cols[j];
      const g = pairGroup(a, b);
      fmtOut.push({
        col_a: pk(a), col_b: pk(b),
        format_a: g.fa, format_b: g.fb,
        score: Math.round(g.fmt * 10000) / 10000,
        score_pct: pct(g.fmt),
        pass: g.formatPass,
        threshold_pct: 80,
        scope: sameTable ? "within_table" : "cross_table",
        table: sameTable ? a.table : undefined,
      });
      if (g.formatPass) {
        if (sameTable) withinFmtSurvivors++; else fmtSurvivors++;
        if (g.group) {
          if (sameTable) withinGrouped++; else { grouped++; edges.push([i, j]); }
        }
        valOut.push({
          col_a: pk(a), col_b: pk(b),
          score: Math.round(g.value * 10000) / 10000,
          score_pct: pct(g.value),
          decision: g.group ? "group together" : "separate",
          threshold_pct: 80,
          scope: sameTable ? "within_table" : "cross_table",
          table: sameTable ? a.table : undefined,
          ...(g.overlap !== null ? { overlap: Math.round(g.overlap * 10000) / 10000, overlap_pct: pct(g.overlap), overlap_kind: g.overlapKind ?? undefined } : {}),
        });
      }
    };
    for (let i = 0; i < cols.length; i++) {
      for (let j = i + 1; j < cols.length; j++) {
        const sameTable = cols[i].table === cols[j].table;
        if (sameTable) {
          withinScored++;
          pushPair(i, j, true, withinFormatGate, withinValuePattern);
        } else {
          scored++;
          pushPair(i, j, false, formatGate, valuePattern);
        }
      }
    }
    formatGate.sort((a, b) => b.score - a.score);
    valuePattern.sort((a, b) => b.score - a.score);
    withinFormatGate.sort((a, b) => b.score - a.score);
    withinValuePattern.sort((a, b) => b.score - a.score);
    // Within-table groups (same-table union-find on value-pattern survivors).
    const withinTableGroups: NonNullable<UdrColumnIntelligence["within_table_similarity"]>["groups"] = [];
    const colsByTable = new Map<string, number[]>();
    cols.forEach((c, idx) => {
      const arr = colsByTable.get(c.table) ?? [];
      arr.push(idx);
      colsByTable.set(c.table, arr);
    });
    let wtGi = 0;
    for (const [tbl, idxs] of colsByTable) {
      if (idxs.length < 2) continue;
      const wParent = Object.fromEntries(idxs.map((i) => [i, i]));
      const wFind = (x: number): number => {
        while (wParent[x] !== x) { wParent[x] = wParent[wParent[x]]; x = wParent[x]; }
        return x;
      };
      const wUnion = (a: number, b: number) => { wParent[wFind(a)] = wFind(b); };
      for (const p of withinValuePattern) {
        if (p.decision !== "group together" || p.table !== tbl) continue;
        const ia = idxs.find((i) => pk(cols[i]) === p.col_a);
        const ib = idxs.find((i) => pk(cols[i]) === p.col_b);
        if (ia != null && ib != null) wUnion(ia, ib);
      }
      const wBuckets = new Map<number, number[]>();
      for (const i of idxs) {
        const r = wFind(i);
        if (!wBuckets.has(r)) wBuckets.set(r, []);
        wBuckets.get(r)!.push(i);
      }
      for (const gIdxs of wBuckets.values()) {
        if (gIdxs.length < 2) continue;
        wtGi += 1;
        withinTableGroups.push({
          group_id: `WT-G${wtGi}`,
          table: tbl,
          format: dominant(cols[gIdxs[0]]),
          members: gIdxs.map((i) => pk(cols[i])),
          member_keys: gIdxs.map((i) => ({ table: cols[i].table, column: cols[i].col })),
        });
      }
    }
    // B19 — union-find groups
    const parent = cols.map((_, i) => i);
    const find = (x: number): number => { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; };
    for (const [a, b] of edges) parent[find(a)] = find(b);
    const byRoot = new Map<number, number[]>();
    cols.forEach((_, i) => { const r = find(i); if (!byRoot.has(r)) byRoot.set(r, []); byRoot.get(r)!.push(i); });
    const multi = [...byRoot.values()].filter((g) => g.length > 1);
    const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
    const consensus = (names: string[]): string => {
      const c: Record<string, number> = {};
      for (const n of names) c[n] = (c[n] ?? 0) + 1;
      return Object.keys(c).sort((a, b) => c[b] - c[a] || a.length - b.length)[0] ?? "";
    };
    // Canonical naming aligned with backend canonical_name_for_group: PK (parent in
    // multi-PK groups) → generic-PK qualified by the DESTINATION entity → non-generic
    // name consensus → neutral synthetic. (FM-ontology aliasing is server-only, so the
    // fallback stops at the qualified name — the backend report supersedes it on rebuild.)
    const GENERIC_PK = new Set(["id", "code", "key", "no", "number", "pk"]);
    const singular = (t: string): string => {
      const snake = t.replace(/(?!^)([A-Z])/g, "_$1").toLowerCase();
      return snake.endsWith("s") && !snake.endsWith("ss") ? snake.slice(0, -1) : snake;
    };
    const distinctSamples = (i: number): Set<string> =>
      new Set(cols[i].samples.map((v) => (v ?? "").trim().toLowerCase()).filter(Boolean));
    const qualifyGeneric = (table: string, name: string): string =>
      GENERIC_PK.has(name) ? `${singular(destTable(table) || table)}_${name}` : name;
    const canonicalFor = (idxs: number[], gid?: string): string => {
      // Tier 1 — a PK names the group; the parent PK wins in multi-PK groups.
      const pkIdxs = idxs.filter((i) => cols[i].isPk);
      const pkTables = new Set(pkIdxs.map((i) => cols[i].table));
      let chosen: number[] | null = null;
      let chosenTable: string | null = null;
      if (pkIdxs.length && pkTables.size === 1) {
        chosen = pkIdxs; chosenTable = cols[pkIdxs[0]].table;
      } else if (pkIdxs.length > 1) {
        const union = new Set<string>();
        for (const i of idxs) distinctSamples(i).forEach((v) => union.add(v));
        const parents = pkIdxs.filter((i) => {
          const dv = distinctSamples(i);
          return union.size > 0 && [...union].every((v) => dv.has(v));
        });
        const parentTables = new Set(parents.map((i) => cols[i].table));
        if (parentTables.size === 1) { chosen = parents; chosenTable = cols[parents[0]].table; }
      }
      if (chosen && chosenTable) {
        const pkName = consensus(chosen.map((i) => norm(cols[i].col)));
        // A descriptive name the group already shares (≥2 members) beats qualifying a
        // generic PK by table (asset_id over work_id) and is independent of dest routing.
        if (GENERIC_PK.has(pkName)) {
          const ngShared = idxs.map((i) => norm(cols[i].col)).filter((n) => !GENERIC_PK.has(n));
          const bestShared = consensus(ngShared);
          if (bestShared && ngShared.filter((n) => n === bestShared).length > 1) return bestShared;
        }
        return qualifyGeneric(chosenTable, pkName);
      }
      // Tier 3 — non-generic name consensus (≥2 members share a descriptive name).
      const ng = idxs.map((i) => norm(cols[i].col)).filter((n) => !GENERIC_PK.has(n));
      if (ng.length) {
        const bestNg = consensus(ng);
        if (ng.filter((n) => n === bestNg).length > 1) return bestNg;
      }
      const all = idxs.map((i) => norm(cols[i].col));
      const best = consensus(all);
      if (all.filter((n) => n === best).length > 1) return best;
      if (idxs.length === 1) return best || norm(cols[idxs[0]].col);
      // Tier 5 — heterogeneous group with no anchor: neutral synthetic keyed by format.
      const fmt = dominant(cols[idxs[0]]);
      return gid ? `${fmt}_attr_${gid}` : `${fmt}_attr`;
    };
    const columnCanonical: Record<string, string> = {};
    const groups: NonNullable<UdrColumnIntelligence["groups"]> = [];
    multi.forEach((idxs, gi) => {
      const name = canonicalFor(idxs, `G${gi + 1}`);
      const fmt = dominant(cols[idxs[0]]);
      for (const i of idxs) columnCanonical[`${cols[i].table}.${cols[i].col}`] = name;
      groups.push({
        group_id: `G${gi + 1}`,
        canonical_name: name,
        format: fmt,
        basis: `${fmt} format + value pattern`,
        members: idxs.map((i) => pk(cols[i])),
        member_keys: idxs.map((i) => ({ table: cols[i].table, column: cols[i].col })),
      });
    });
    cols.forEach((c, i) => { const k = `${c.table}.${c.col}`; if (!(k in columnCanonical)) columnCanonical[k] = canonicalFor([i]); });
    // B20 — classify groups
    const classification: NonNullable<UdrColumnIntelligence["classification"]> = [];
    let pkG = 0, fkG = 0, shG = 0;
    multi.forEach((idxs, gi) => {
      const pkIdx = idxs.find((i) => cols[i].isPk);
      const fkIdx = idxs.find((i) => !cols[i].isPk && pkIdx != null && cols[i].table !== cols[pkIdx!].table);
      let verdict = "Shared Attribute";
      if (pkIdx != null && fkIdx != null) { verdict = "Foreign Key relationship"; fkG++; }
      else if (pkIdx != null) { verdict = "Primary Key group"; pkG++; }
      else shG++;
      classification.push({
        group_id: `G${gi + 1}`,
        canonical_name: groups[gi]?.canonical_name,
        members: idxs.map((i) => pk(cols[i])),
        has_pk: pkIdx != null ? pk(cols[pkIdx]) : null,
        fk_column: fkIdx != null ? pk(cols[fkIdx]) : null,
        ri: null,
        verdict,
      });
    });
    // B15 — FK candidates (a non-PK column grouped with a PK in another table)
    const fkCandidates: NonNullable<UdrColumnIntelligence["fk_candidates"]> = [];
    multi.forEach((idxs) => {
      const pkIdx = idxs.find((i) => cols[i].isPk);
      if (pkIdx == null) return;
      for (const i of idxs) {
        if (i === pkIdx || cols[i].isPk || cols[i].table === cols[pkIdx].table) continue;
        const childVals = new Set(cols[i].samples.map((v) => v.trim()).filter(Boolean));
        const parentVals = new Set(cols[pkIdx].samples.map((v) => v.trim()).filter(Boolean));
        const overlap = childVals.size ? [...childVals].filter((v) => parentVals.has(v)).length / childVals.size : 0;
        fkCandidates.push({
          src_table: cols[i].table, src_column: cols[i].col,
          dst_table: cols[pkIdx].table, dst_column: cols[pkIdx].col,
          ri: overlap, confirmed: overlap >= 0.95,
          reason: `grouped with PK ${cols[pkIdx].table}.${cols[pkIdx].col} · sample-value overlap ${Math.round(overlap * 100)}%`,
        });
      }
    });
    // Per-member class + joining signal (mirrors backend member_classes): 'name' when the
    // member's normalised column name equals the group's canonical, else 'value' (joined via
    // cell value / format alone) — surfaces coincidental value-only matches in B19.1.
    const classOfCol = (table: string, col: string): string => {
      const c = cols.find((x) => x.table === table && x.col === col);
      if (c?.isPk) return "PK";
      if (fkCandidates.some((f) => f.src_table === table && f.src_column === col)) return "FK";
      return "Shared";
    };
    for (const g of groups) {
      const canonNorm = norm(g.canonical_name);
      g.member_classes = (g.member_keys ?? []).map((mk) => ({
        prefixed_key: `${mk.table.toLowerCase()}.${mk.column}`,
        classification: classOfCol(mk.table, mk.column),
        joined_by: norm(mk.column) === canonNorm ? ("name" as const) : ("value" as const),
      }));
    }
    // B13 prefixing · B14 metadata · B21 dest mapping. Routing is confirmed → prefix with the
    // (overridden) destination table; source falls back only when a table wasn't routed.
    const prefixing = cols.map((c) => {
      const dt = destTable(c.table);
      return {
        source_table: c.table,
        column: c.col,
        prefixed_key: pk(c),
        dest_table: dt,
        dest_prefixed_key: `${(dt ?? c.table).toLowerCase()}.${c.col}`,
      };
    });
    const metadata = cols.map((c) => ({
      prefixed_key: pk(c), source_table: c.table, column: c.col, dest_table: destTable(c.table),
      classification: c.isPk ? "PK" : fkCandidates.some((f) => f.src_table === c.table && f.src_column === c.col) ? "FK" : "Shared",
      format: dominant(c), samples: c.samples, canonical_name: columnCanonical[`${c.table}.${c.col}`],
    }));
    const destMapping = cols.map((c) => ({
      source: pk(c), canonical_name: columnCanonical[`${c.table}.${c.col}`], dest_table: destTable(c.table),
      matched_column: c.dest ?? null, confidence: c.conf ?? null,
      outcome: c.dest ? (typeof c.conf === "number" && c.conf >= 0.95 ? "auto-resolved" : "matched") : "review",
    }));
    const confs = cols.map((c) => c.conf).filter((v): v is number => typeof v === "number");
    // B17.1 / B18.1 display groups (mirrors server format_groups / value_pattern_groups).
    const withinFmtByTable = new Map<string, Map<string, string[]>>();
    const crossFmtBy = new Map<string, string[]>();
    const withinShapeByTable = new Map<string, Map<string, string[]>>();
    const crossShapeBy = new Map<string, string[]>();
    for (const c of cols) {
      const fmt = dominant(c);
      const shape = dominantShape(c);
      const wFmt = withinFmtByTable.get(c.table) ?? new Map<string, string[]>();
      wFmt.set(fmt, [...(wFmt.get(fmt) ?? []), c.col]);
      withinFmtByTable.set(c.table, wFmt);
      crossFmtBy.set(fmt, [...(crossFmtBy.get(fmt) ?? []), pk(c)]);
      if (shape) {
        const wSh = withinShapeByTable.get(c.table) ?? new Map<string, string[]>();
        wSh.set(shape, [...(wSh.get(shape) ?? []), c.col]);
        withinShapeByTable.set(c.table, wSh);
        crossShapeBy.set(shape, [...(crossShapeBy.get(shape) ?? []), pk(c)]);
      }
    }
    const formatGroups = {
      within_table: [...withinFmtByTable.entries()].flatMap(([table, byFmt]) =>
        [...byFmt.entries()]
          .filter(([, colList]) => colList.length >= 2)
          .map(([format, columns]) => ({ table, format, columns })),
      ),
      cross_table: [...crossFmtBy.entries()]
        .filter(([, colList]) => colList.length >= 2)
        .map(([format, columns]) => ({ format, columns })),
    };
    const valuePatternGroups = {
      within_table: [...withinShapeByTable.entries()].flatMap(([table, byShape]) =>
        [...byShape.entries()]
          .filter(([, colList]) => colList.length >= 2)
          .map(([value_shape, columns]) => ({ table, value_shape, columns })),
      ),
      cross_table: [...crossShapeBy.entries()]
        .filter(([, colList]) => colList.length >= 2)
        .map(([value_shape, columns]) => ({ value_shape, columns })),
    };
    return {
      prefixing, metadata, metadata_total: cols.length, fk_candidates: fkCandidates,
      format_gate: formatGate, value_pattern: valuePattern,
      format_groups: formatGroups,
      value_pattern_groups: valuePatternGroups,
      within_table_similarity: {
        format_gate: withinFormatGate,
        value_pattern: withinValuePattern,
        groups: withinTableGroups,
        summary: {
          pairs_scored: withinScored,
          format_survivors: withinFmtSurvivors,
          value_survivors: withinGrouped,
          groups: withinTableGroups.length,
          format_threshold_pct: 80,
          value_threshold_pct: 80,
        },
      },
      groups, classification,
      column_canonical: columnCanonical, dest_mapping: destMapping,
      summary: {
        columns_analyzed: cols.length, pk_groups: pkG, fk_groups: fkG, shared_groups: shG,
        columns_mapped: cols.filter((c) => c.dest).length, groups: multi.length,
        confidence: confs.length ? confs.reduce((a, b) => a + b, 0) / confs.length : null,
        format_pairs_scored: scored, format_survivors: fmtSurvivors, value_survivors: grouped,
        format_threshold_pct: 80, value_threshold_pct: 80,
        within_table_pairs_scored: withinScored,
        within_table_format_survivors: withinFmtSurvivors,
        within_table_value_survivors: withinGrouped,
        within_table_groups: withinTableGroups.length,
        cross_table_groups: multi.length,
      },
    };
  };
  // Live preview of column_intelligence after a 'Re-match now' click. When set,
  // it replaces payload.column_intelligence in every panel (B14/B17/B18/B19/B20/B21)
  // so the user sees the re-matched destinations without advancing the gate.
  const [previewedCi, setPreviewedCi] = useState<UdrColumnIntelligence | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  // True while "Confirm routing" is re-running the column mapping against changed table targets.
  const [routingConfirming, setRoutingConfirming] = useState(false);
  // Diff summary surfaced on the success banner so the user can SEE what re-match
  // actually changed (vs the silent 'no changes' case when a pin matches the
  // auto-derived canonical).
  const [previewChangeCount, setPreviewChangeCount] = useState<number | null>(null);
  // Bumped on each successful preview to force the CollapsibleStepCard tree to
  // remount — every card defaults back to its initial open state, so the user
  // immediately sees the re-matched B14 / B21 instead of having to expand them.
  const [previewKey, setPreviewKey] = useState(0);
  // previewedCi (canonical-name preview) wins; then prefer the backend report UNLESS the user
  // overrode a table's routing, in which case use the gate-derived version so B14 (column
  // metadata) and B21 (destination column mapping) re-map to the new target like Step 2 does.
  const columnIntelligence: UdrColumnIntelligence | null =
    phase === "columns" && payload
      ? (previewedCi ??
          (tableOverrideActive ? buildGateColumnIntelligence() : payload?.column_intelligence) ??
          buildGateColumnIntelligence())
      : null;
  async function handleRematch() {
    if (!Object.keys(canonicalOverrides).length) return;
    setPreviewing(true);
    setPreviewError(null);
    try {
      const baselineCi = payload?.column_intelligence;
      const result = await schemaMapperApi.previewCanonicalOverrides(migrationId, {
        canonical_overrides: canonicalOverrides,
      });
      const newCi = result.column_intelligence ?? null;
      // Compute how many B14 / B21 rows actually changed (canonical or dest match).
      // 0 = pin matched what the algorithm already produced; no visible change.
      let changes = 0;
      if (newCi && baselineCi) {
        const oldMeta = new Map<string, string | null | undefined>(
          (baselineCi.metadata ?? []).map((m) => [m.prefixed_key, m.canonical_name]),
        );
        for (const m of newCi.metadata ?? []) {
          if (oldMeta.get(m.prefixed_key) !== m.canonical_name) changes += 1;
        }
        const oldDest = new Map<string, string | null | undefined>(
          (baselineCi.dest_mapping ?? []).map((r) => [r.source, r.matched_column]),
        );
        for (const r of newCi.dest_mapping ?? []) {
          if (oldDest.get(r.source) !== r.matched_column) changes += 1;
        }
      }
      setPreviewChangeCount(changes);
      setPreviewedCi(newCi);
      setPreviewKey((k) => k + 1);  // remount panels so collapsed cards open to defaults
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Re-match failed";
      setPreviewError(msg);
    } finally {
      setPreviewing(false);
    }
  }
  // Clear the preview whenever the user edits a pin again — they'll need to re-run.
  const _pinsKey = JSON.stringify(canonicalOverrides);
  useEffect(() => {
    setPreviewedCi(null);
    setPreviewChangeCount(null);
    // Editing a pin invalidates the previous confirmation — the user has to
    // re-confirm to re-reveal B14 / B21. (When no pins exist there's nothing
    // to confirm; the gate auto-confirms.)
    setCanonicalsConfirmed(Object.keys(canonicalOverrides).length === 0);
  }, [_pinsKey]);
  // Canonical-names confirmation gate. Until the user clicks the button under the
  // Unified column names panel, B14.1 + B21.1 stay hidden. Default: confirmed when
  // there are no pins (no review needed); unconfirmed once a pin is set.
  const [canonicalsConfirmed, setCanonicalsConfirmed] = useState<boolean>(
    Object.keys(canonicalOverrides).length === 0,
  );
  async function handleConfirmCanonicals() {
    // If pins exist and we haven't already previewed, run the backend re-match
    // so B14/B21 below show the updated dest matches.
    if (Object.keys(canonicalOverrides).length > 0 && !previewedCi) {
      await handleRematch();
    }
    setCanonicalsConfirmed(true);
  }

  // "Confirm routing" (Step 1 → Step 2). If the user re-pointed any sheet to a different CAFM
  // table, CALL the column-mapping API so the columns are matched against the NEW table's
  // columns — instead of showing the precomputed mapping built for the original routing. When
  // nothing changed, the precomputed column_intelligence is already correct, so we just advance.
  async function handleConfirmRouting() {
    const tableOverrides: Record<string, string> = {};
    for (const [src, v] of Object.entries(tableTargets)) {
      const tgt = (v.target ?? "").trim();
      const orig = (initialTableTargetsRef.current?.[src] ?? "").trim();
      // Real re-points only: an existing-table target that differs from the backend's original.
      if (tgt && !v.isNew && tgt !== orig) tableOverrides[src] = tgt;
    }
    // SPLIT routing gate (pass 1): submit the routing to the backend, which captures it and moves
    // on to B20 → B14.1 → the column-matching gate. Send the structured table_overrides (target +
    // is_new flag) with no column decisions — those come at the pass-2 column-matching gate.
    if (lockedPhase === "tables") {
      const structured: Record<string, { target_table: string; is_new_table: boolean }> = {};
      for (const [src, v] of Object.entries(tableTargets)) {
        const tgt = (v.target ?? "").trim();
        if (tgt) structured[src] = { target_table: tgt, is_new_table: !!v.isNew };
      }
      submitGate(
        { migrationId, body: { decisions: {}, table_overrides: structured } },
        { onSuccess: () => onSubmitted() },
      );
      return;
    }
    if (Object.keys(tableOverrides).length > 0) {
      setRoutingConfirming(true);
      setPreviewError(null);
      try {
        const result = await schemaMapperApi.previewCanonicalOverrides(migrationId, {
          canonical_overrides: canonicalOverrides,
          table_overrides: tableOverrides,
        });
        setPreviewedCi(result.column_intelligence ?? null);
        setPreviewKey((k) => k + 1); // remount panels so the re-matched B14/B21 show immediately
      } catch (err) {
        // Non-fatal: fall back to the client-derived mapping (buildGateColumnIntelligence),
        // which already reflects the new targets from the gate payload's per-column samples.
        setPreviewError(err instanceof Error ? err.message : "Column mapping failed");
      } finally {
        setRoutingConfirming(false);
      }
    }
    setPhase("columns");
  }

  // Effective canonical map = backend payload's column_canonical overlaid with
  // the user's pending pins. Built once and used by every panel + Step 2 row so
  // a pinned rename shows everywhere BEFORE the gate is advanced and the
  // backend re-runs build_column_intelligence with the override applied.
  // Keys are lowercase 'table.col'; values are the canonical column name.
  const effectiveCanonical: Record<string, string> = (() => {
    const out: Record<string, string> = {};
    const raw = (columnIntelligence?.column_canonical ?? {}) as Record<string, string>;
    for (const [k, v] of Object.entries(raw)) {
      if (!k || !v) continue;
      const [t, c] = k.split(".");
      if (!t || !c) continue;
      out[`${t.toLowerCase()}.${c}`] = v;
    }
    const groupsList = columnIntelligence?.groups ?? [];
    for (const [overrideKey, newName] of Object.entries(canonicalOverrides)) {
      if (!newName) continue;
      const grp = groupsList.find((g) => g.group_id === overrideKey);
      if (grp) {
        // Group-level override — apply to every member
        for (const m of grp.members) out[m.toLowerCase()] = newName;
      } else {
        // Treat as column prefixed_key
        out[overrideKey.toLowerCase()] = newName;
      }
    }
    return out;
  })();
  const effectiveCanonicalFor = (table: string, column: string): string | null => {
    return effectiveCanonical[`${table.toLowerCase()}.${column}`] ?? null;
  };
  // Mirror of the backend's entity_prefix_conflict (and the same helper in
  // migration-metadata-view.tsx): a pinned canonical that contains a known
  // entity prefix (asset, vendor, site, work_order, …) only fits a destination
  // table whose singular matches that prefix. Pinning 'asset_id' for vendors.id
  // is an entity mismatch — after re-match it MUST become a new column on
  // vendors, not a merge with vendors.id. Used so Step 2 shows the eventual
  // outcome upfront instead of misleading the user with the stale 'existing
  // column' badge from the pre-pin deterministic match.
  const _ENTITY_PREFIXES = new Set([
    "asset", "vendor", "site", "work", "workorder", "wo", "resource", "customer", "employee",
    "engineer", "technician", "supplier", "product", "contact", "location", "building",
    "department", "user", "team", "company", "client", "tenant", "lease", "contract",
    "invoice", "task",
  ]);
  const _normTok = (s: string) =>
    (s || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const _singularTok = (t: string) => {
    const s = (t || "").replace(/(?!^)([A-Z])/g, "_$1").toLowerCase();
    return s.endsWith("s") && !s.endsWith("ss") ? s.slice(0, -1) : s;
  };
  const _entityPrefixConflict = (
    srcName: string,
    destName: string | null | undefined,
    destTable: string | null | undefined,
  ): boolean => {
    if (!destTable || !destName) return false;
    const st = new Set(_normTok(srcName).split("_").filter(Boolean));
    const dt = new Set(_normTok(destName).split("_").filter(Boolean));
    if (!st.size || !dt.size) return false;
    const stArr = [...st], dtArr = [...dt];
    const subset = stArr.every((x) => dt.has(x)) || dtArr.every((x) => st.has(x));
    if (!subset) return false;
    const [larger, smaller] = st.size >= dt.size ? [st, dt] : [dt, st];
    const extra = [...larger].filter((x) => !smaller.has(x));
    const destSing = _singularTok(destTable);
    return extra.some((tok) => tok && tok !== destSing && _ENTITY_PREFIXES.has(tok));
  };

  // Auto-flip entity-mismatched rows to "semantic": when the pinned canonical
  // belongs to a different entity than the destination table (e.g. asset_id
  // pinned on vendors.id), the deterministic 'id → id' match is invalid —
  // the user shouldn't have to manually toggle each row to Semantic. Default
  // those to semantic so the AI re-evaluates. User can still flip back to
  // Approve if they want to create a new column instead. Only fires when
  // pins change so we don't undo a deliberate user choice on every render.
  useEffect(() => {
    if (Object.keys(canonicalOverrides).length === 0) return;
    const flips: Record<string, "semantic"> = {};
    for (const [tbl, items] of Object.entries(reviewByTable)) {
      const effectiveTbl = tableTargets[tbl]?.target ?? tbl;
      if (tableTargets[tbl]?.isNew) continue;
      for (const it of items) {
        const pinned = effectiveCanonicalFor(tbl, it.source_field);
        if (!pinned || pinned === it.source_field) continue;
        if (_entityPrefixConflict(pinned, it.target_field, effectiveTbl)) {
          flips[`${tbl}.${it.source_field}`] = "semantic";
        }
      }
    }
    if (Object.keys(flips).length) {
      setDecisions((prev) => ({ ...prev, ...flips }));
    }
    // _pinsKey already captures canonicalOverrides changes
  }, [_pinsKey]);

  // (Primary-key confirmation is now its OWN gate — GatePkApproval — that runs before this one.
  // The pre-semantic gate is Group B only: routing + column mapping.)

  return (
    <div ref={panelsRef} className="max-w-6xl">
      {/* #10 — document/table inventory, shown before mapping begins */}
      {wizard && phase === "columns" ? null : (
      <DocumentSummary
        inventory={(payload as unknown as { document_inventory?: DocumentInventory } | null)?.document_inventory}
      />
      )}
      {/* B7.1 → B11.1 + B12.1 — the full five-step table-resolution breakdown (only on
          Step 1): unique table identification → PK detection → deterministic → RAG/alias →
          semantic + final decisions. Driven by the backend report when present, else derived
          from the gate payload so every step renders without waiting on a backend rebuild. */}
      {/* B7.1 → B12.1 — table-resolution panels. Shown on BOTH steps so the table-level
          analysis (unique table id → PK → deterministic / RAG / semantic) stays visible
          after the user confirms routing; Step 2 appends the column pipeline below. */}
      {/* B9.1 → B11.1 report (Group B, mapping only — primary keys were confirmed in the prior
          PK-approval gate, so phase="mapping" hides B6.1/B7.1/B8.1 here). */}
      {tableResolution && !(wizard && phase === "columns") ? (
        <TableResolutionPanels
          report={tableResolution}
          tableTopMatches={tableTopMatches}
          phase="mapping"
          // B12.1 "Table routing — confirmed" is the OUTCOME of this gate: hide it while Step 1 is
          // still asking for confirmation, show it once routing has been approved.
          routingConfirmed={phase !== "tables"}
        />
      ) : null}
      {/* B13.1 → B21.1 — the Column Intelligence Pipeline, shown on Step 2 (column matching),
          ABOVE the column-matching UI: prefix → format/value gates → group → classify →
          unify → metadata. Backend report when present (full rows), else derived from the
          gate's per-column samples so the steps render without a backend rebuild. */}
      {phase === "columns" && columnIntelligence ? (() => {
        // Bumping `key` on every successful re-match forces the panel tree to
        // remount with fresh defaults — collapsed B14/B21 cards re-open so the
        // user sees the new content immediately.
        const panels = (
          <ColumnIntelligencePanels
            key={`ci-${previewKey}`}
            report={columnIntelligence}
            canonicalOverrides={canonicalOverrides}
            onCanonicalChange={handleCanonicalChange}
            effectiveCanonical={effectiveCanonical}
            canonicalsConfirmed={canonicalsConfirmed}
            onConfirmCanonicals={handleConfirmCanonicals}
            confirmReMatchPending={Object.keys(canonicalOverrides).length > 0 && !previewedCi}
            confirmInProgress={previewing}
            confirmChangeCount={canonicalsConfirmed ? previewChangeCount : null}
          />
        );
        // Hoistra's card: the decisions lead; the analysis behind them opens on request.
        return wizard ? (
          <details className="group mb-4">
            <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[12px] font-medium text-slate-600 hover:text-slate-900 [&::-webkit-details-marker]:hidden">
              <ChevronRight size={14} className="transition-transform group-open:rotate-90" aria-hidden="true" />
              How the columns were analysed and matched
            </summary>
            <div className="mt-3">{panels}</div>
          </details>
        ) : (
          panels
        );
      })() : null}
      {/* Header */}
      {/* Minimal error surface for re-match failures — the action button now
          lives in the ColumnIntelligencePanels under the Unified column names
          panel, with the change-count receipt shown inline above B14. */}
      {phase === "columns" && previewError ? (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50/60 px-4 py-2 text-[11.5px] text-red-800">
          Re-match failed: {previewError}
        </div>
      ) : null}

      {/* Step-1 routing header — hidden until primary keys are approved (Step 2 always shows). */}
      {phase !== "tables" || showRouting ? (
      <div
        className="flex items-start gap-4 mb-4"
        // The "Step 1 — Confirm table routing" header + description auto-link to the
        // deterministic-mapping log; Step 2 links to the destination-column-mapping log.
        data-pipeline-stage={phase === "tables" ? "deterministic" : "column_to_destination"}
      >
        <div className="w-10 h-10 rounded-xl bg-indigo-100 flex items-center justify-center shrink-0">
          <CheckCircle size={20} className="text-indigo-600" />
        </div>
        <div className="flex-1">
          <h2 className="text-lg font-bold text-slate-900">
            {phase === "tables" ? "Confirm table routing" : "Confirm column matching"}
          </h2>
          <p className="text-sm text-slate-500 mt-0.5">
            {phase === "tables"
              ? "Each Excel sheet is matched to a CAFM table. Sheets with no exact name match get a semantic guess — change any target, then confirm to start column matching."
              : "Fields matched against the confirmed table. Approve confident matches or send uncertain ones to semantic search. Rename a target column if needed — changes carry through to the database write."}
          </p>
        </div>
        <div className="text-right shrink-0">
          <div className="text-2xl font-bold font-mono text-slate-800">
            {phase === "tables" ? routingTables.length : totalItems}
          </div>
          <div className="text-xs text-slate-500">{phase === "tables" ? "sheets" : "fields to review"}</div>
        </div>
      </div>
      ) : null}

      {phase === "tables" ? (
        showRouting ? (
        <div className="mb-4" data-pipeline-stage="deterministic">
          <div className="rounded-xl border border-slate-200 bg-white shadow-sm divide-y divide-slate-100">
            {routingTables.map((tbl) => {
              const mt = tableMatchType(tbl);
              const dupGroup = dupGroupByTable.get(tbl);
              return (
                <div key={dupGroup ? dupGroup.tables.join("|") : tbl} className="px-5 py-3">
                  <div className="flex items-center gap-2 mb-2 flex-wrap">
                    <span className="font-mono text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-700">
                      {dupGroup ? (dupGroup.label || tbl) : tbl}
                    </span>
                    {dupGroup ? (
                      <span
                        className="shrink-0 rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] font-bold text-white"
                        title={`duplicate sheets routed together: ${dupGroup.tables.join(", ")}`}
                      >
                        ×{dupGroup.count}
                      </span>
                    ) : null}
                    <ArrowRight size={12} className="text-slate-300 shrink-0" />
                    {mt === "exact" ? (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
                        exact name match
                      </span>
                    ) : mt === "semantic" ? (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-700">
                        semantic guess
                      </span>
                    ) : mt === "new" ? (
                      <span
                        className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-700"
                        title="No existing CAFM table matched. This sheet will be created as a new table — columns are auto-generated from the source."
                      >
                        new table
                      </span>
                    ) : (
                      <>
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-600">
                          pick a table
                        </span>
                        {(() => {
                          // 7.4 AC6 — no confident match: offer an actionable suggestion
                          // (assign an existing FM table, or create a new one) in one click.
                          const sug = payload?.table_routing_suggestion_by_table?.[tbl];
                          if (!sug) return null;
                          const isAssign = sug.action === "assign" && !!sug.target;
                          const name = isAssign ? sug.target : sug.suggested_new_name;
                          if (!name) return null;
                          return (
                            <button
                              type="button"
                              title={
                                isAssign
                                  ? `Assign this sheet to the existing CAFM table '${name}'`
                                  : `No CAFM table matched — create a new table '${name}' (suggested from facilities-management vocabulary)`
                              }
                              onClick={() => applyTableTarget(tbl, name as string, !isAssign)}
                              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-50 text-indigo-700 border border-indigo-200 hover:bg-indigo-100"
                            >
                              <Search size={11} />
                              {isAssign ? `Suggested: ${name}` : `Create new table '${name}'`}
                            </button>
                          );
                        })()}
                      </>
                    )}
                    {(() => {
                      const conf = payload?.table_match_confidence_by_table?.[tbl];
                      if (mt === "new" || conf == null || conf <= 0) return null;
                      const pct = Math.round(conf * 100);
                      return (
                        <span
                          title="Table match confidence (exact name = 100%, otherwise the AI match %)"
                          className={`text-xs font-mono font-semibold ${
                            pct >= 90 ? "text-green-600" : pct >= 70 ? "text-amber-600" : "text-red-500"
                          }`}
                        >
                          {pct}% match
                        </span>
                      );
                    })()}
                  </div>
                  <MigrationCanonicalTableSelect
                    sourceTable={tbl}
                    canonicalTables={canonicalTargetTables}
                    // Never fall back to the source-table name (e.g. "Resources"): it is NOT a
                    // canonical target, and the select would otherwise inject it as a bogus
                    // option. An unrouted sheet shows the "— select table —" placeholder so the
                    // user picks the suggested existing table (technicians) or "+ New table…".
                    value={tableTargets[tbl]?.isNew ? "" : (tableTargets[tbl]?.target ?? "")}
                    isNewTable={tableTargets[tbl]?.isNew ?? false}
                    newTableName={tableTargets[tbl]?.isNew ? (tableTargets[tbl]?.target ?? "") : ""}
                    onChange={({ canonicalTable, isNewTable, newTableName }) =>
                      applyTableTarget(tbl, isNewTable ? newTableName : canonicalTable, isNewTable)
                    }
                  />
                  {(() => {
                    // Top-3 candidate destination tables — ONE merged ranking (best score per
                    // table across deterministic name-match + semantic column-fit). Avoids showing
                    // the same data twice (Step 1 vs B12.1) and gives a single decision to pick from.
                    const chosen = tableTargets[tbl]?.isNew ? "" : (tableTargets[tbl]?.target ?? "");
                    const merged = new Map<string, number>();
                    for (const m of rankTableNameMatches(tbl, canonicalTargetTables, 5)) {
                      merged.set(m.table, Math.max(merged.get(m.table) ?? 0, m.pct));
                    }
                    for (const c of payload?.table_match_candidates_by_table?.[tbl] ?? []) {
                      if (!c.table || !canonicalTargetTables.includes(c.table)) continue;
                      merged.set(c.table, Math.max(merged.get(c.table) ?? 0, c.pct ?? 0));
                    }
                    const top = [...merged.entries()]
                      .map(([table, pct]) => ({ table, pct }))
                      .sort((a, b) => b.pct - a.pct)
                      .slice(0, 3);
                    if (!top.length) return null;
                    const chip = ({ table, pct }: { table: string; pct: number }) => {
                      const p = Math.round(pct * 100);
                      const active = chosen === table;
                      return (
                        <button
                          key={table}
                          type="button"
                          title={`route ${tbl} → ${table}`}
                          onClick={() => applyTableTarget(tbl, table, false)}
                          className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-mono transition-colors ${
                            active
                              ? "border-indigo-400 bg-indigo-50 text-indigo-700"
                              : "border-slate-200 bg-white text-slate-600 hover:border-indigo-300"
                          }`}
                        >
                          {table}
                          <span className={p >= 70 ? "text-green-600" : p >= 40 ? "text-amber-600" : "text-slate-400"}>
                            {p}%
                          </span>
                        </button>
                      );
                    };
                    return (
                      <div className="mt-2 flex items-start gap-2 flex-wrap">
                        <span className="mt-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                          Top 3 matches
                        </span>
                        <div className="flex flex-wrap gap-1.5">{top.map(chip)}</div>
                      </div>
                    );
                  })()}
                </div>
              );
            })}
          </div>
          <div className="flex justify-end mt-4">
            <button
              onClick={handleConfirmRouting}
              disabled={!routingComplete || routingConfirming || isPending}
              className="inline-flex items-center gap-2 px-6 py-2.5 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
            >
              {isPending
                ? "Submitting…"
                : routingConfirming
                  ? "Matching columns…"
                  : lockedPhase === "tables"
                    ? "Approve table routing & continue"
                    : "Confirm routing"}
              <ArrowRight size={16} />
            </button>
          </div>
        </div>
        ) : null
      ) : (
      <>
      {/* "Back to table routing" only makes sense in the legacy single gate — in the SPLIT
          column-matching gate (locked_phase="columns") routing was a separate earlier gate. */}
      {lockedPhase ? null : (
      <div className="mb-4">
        <button
          type="button"
          onClick={() => setPhase("tables")}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-slate-700"
        >
          <ArrowRight size={13} className="rotate-180" />
          Back to table routing
        </button>
      </div>
      )}

      {/* Summary */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        {[
          { label: "T1 Approved",      value: countApproved,    color: "text-green-600" },
          { label: "→ Semantic (you)", value: countSemantic,    color: "text-blue-600" },
          { label: "Auto → Semantic",  value: totalAutoSemantic, color: "text-amber-600" },
          // Count EVERY identified/source table (incl. all-semantic sheets with no T1 rows), so it
          // matches the 4 tables from unique-table identification instead of the review-only 3.
          { label: "Tables",           value: allShownTables.length, color: "text-slate-700" },
        ].map(({ label, value, color }) => (
          <div key={label} className="rounded-xl border border-slate-200 bg-white shadow-sm p-4 text-center">
            <div className={`text-xl font-bold font-mono ${color}`}>{value}</div>
            <div className="text-xs text-slate-500 mt-0.5">{label}</div>
          </div>
        ))}
      </div>

      {/* Reconciliation against B7.1 (unique table identification): tables + columns must all be
          accounted for. Green when everything reconciles; amber listing the shortfall otherwise. */}
      {identifiedTables.length > 0 && (
        <div
          className={`mb-4 rounded-lg border px-3 py-2 text-xs ${
            tablesReconcile && totalMissingCols === 0
              ? "border-emerald-200 bg-emerald-50/60 text-emerald-800"
              : "border-amber-200 bg-amber-50/70 text-amber-900"
          }`}
        >
          {tablesReconcile && totalMissingCols === 0 ? (
            <>
              ✓ Reconciled — all {identifiedTables.length} identified table
              {identifiedTables.length === 1 ? "" : "s"} and {totalIdentifiedCols} column
              {totalIdentifiedCols === 1 ? "" : "s"} are accounted for.
            </>
          ) : (
            <>
              ⚠ Count mismatch vs unique-table identification:{" "}
              {!tablesReconcile && (
                <b>
                  {identifiedTables.length} tables identified, {allShownTables.length} shown.{" "}
                </b>
              )}
              {totalMissingCols > 0 && (
                <b>
                  {totalMissingCols} of {totalIdentifiedCols} columns not in the mapping
                </b>
              )}
              {" "}— see the per-table “not mapped” flags below. Columns removed in pre-processing
              (empty/duplicate) carry no data; if a flagged column has real data, re-check the source.
            </>
          )}
        </div>
      )}

      {/* Tier breakdown */}
      {Object.keys(tierCounts).length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-5">
          {Object.entries(tierCounts).sort((a, b) => b[1] - a[1]).map(([tier, count]) => (
            <span key={tier} className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium ${TIER_COLORS[tier] ?? "bg-slate-100 text-slate-600"}`}>
              {plainTierWord(tier.replace("T1_", ""))}: {count}
            </span>
          ))}
        </div>
      )}

      {/* Bulk action */}
      {totalItems > 0 && (
        <div className="flex justify-end mb-4">
          <button
            onClick={approveAll}
            className="inline-flex items-center gap-2 px-4 py-2 bg-white border border-slate-200 text-slate-700 text-sm font-medium rounded-lg hover:bg-slate-50 transition-colors"
          >
            <CheckCircle size={14} />
            Approve all
          </button>
        </div>
      )}

      {/* Table cards */}
      <div className="space-y-3 mb-6">
        {shownTables.map((tbl) => {
          const items: MigrationPreSemanticReviewItem[] = reviewByTable[tbl] ?? [];
          // Duplicate sheets collapsed into this representative (e.g. work_order + workorders).
          const dupMembers = dupMembersByRep.get(tbl) ?? [];
          // Deterministic mappings already auto-approved (identity / alias) — shown read-only
          // so every mapped source column is visible (e.g. site_ref → id). Hidden for a
          // new table (there every column becomes a fresh column, nothing is "auto-approved").
          const autoApproved: MigrationPreSemanticReviewItem[] =
            (tableTargets[tbl]?.isNew ? [] : payload?.auto_approved_by_table?.[tbl]) ?? [];
          const unresolved = unresolvedByTable[tbl] ?? [];
          const isOpen = expandedTables.has(tbl);
          // Column-count reconciliation: EVERY column identified in B7.1 (unique table
          // identification) must appear somewhere in this table's review. A column present in
          // none of the T1 / auto-approved / unresolved / value-merged buckets was dropped
          // upstream (an empty or duplicate column removed in pre-processing). Surface it so the
          // shown count always matches what was identified — instead of e.g. showing 8 of 9.
          const identifiedCols = identifiedColsByTable[tbl] ?? [];
          const shownCols = new Set<string>();
          for (const it of items) {
            shownCols.add(it.source_field);
            for (const m of it.merged_source_fields ?? []) shownCols.add(m);
          }
          for (const a of autoApproved) shownCols.add(a.source_field);
          for (const u of unresolved) shownCols.add(u);
          const missingCols = identifiedCols.filter((c) => c && !shownCols.has(c));
          // Columns of the confirmed target table — the per-field target becomes a
          // dropdown of these so the user can remap a column to any real column.
          const confirmedTarget = tableTargets[tbl]?.target ?? tbl;
          const targetColsKey = Object.keys(canonicalColumnsByTable).find(
            (k) => k.toLowerCase() === confirmedTarget.toLowerCase(),
          );
          const targetCols = targetColsKey ? canonicalColumnsByTable[targetColsKey] : [];
          // Suggest a leftover (not-yet-mapped) target column for each unresolved
          // source field — shown as a hint while the field still routes to semantic.
          const normCol = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
          const usedTargets = new Set(
            items.map((it) =>
              normCol(fieldRenames[`${tbl}.${it.source_field}`] ?? it.target_field),
            ),
          );
          const leftoverCols = targetCols.filter((c) => !usedTargets.has(normCol(c)));
          // UF4.2-AC6 — duplicate-name validation: count how many columns in this
          // table resolve to each target so a row mapped to an already-used target
          // can flag it within the same render (no debounce needed).
          const targetCounts = new Map<string, number>();
          for (const it of items) {
            const t = normCol(fieldRenames[`${tbl}.${it.source_field}`] ?? it.target_field);
            if (t) targetCounts.set(t, (targetCounts.get(t) ?? 0) + 1);
          }
          const isNewTbl = !!tableTargets[tbl]?.isNew;
          const allApproved =
            isNewTbl
              ? items.length + unresolved.length > 0
              : items.length > 0 && items.every(
                  (item) => (decisions[`${tbl}.${item.source_field}`] ?? "approve") === "approve"
                );

          return (
            <div key={tbl} className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
              <button
                className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
                onClick={() => toggleTable(tbl)}
              >
                <div className="flex items-center gap-3 flex-wrap">
                  <span className="font-semibold text-slate-800 text-sm">{tbl}</span>
                  {dupMembers.length > 1 ? (
                    <span
                      className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-amber-500 text-white"
                      title={`Duplicate sheets merged (identical columns, routed together): ${dupMembers.join(", ")}`}
                    >
                      ×{dupMembers.length}
                    </span>
                  ) : null}
                  {isNewTbl ? (
                    <>
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-700">
                        new table
                      </span>
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
                        {items.length + unresolved.length} T1_new_table
                      </span>
                    </>
                  ) : (
                    <>
                      {items.length > 0 && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
                          {items.length} T1
                        </span>
                      )}
                      {autoApproved.length > 0 && (
                        <span
                          className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200"
                          title="Identity/alias matches — auto-approved, shown for visibility"
                        >
                          {autoApproved.length} auto
                        </span>
                      )}
                      {unresolved.length > 0 && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-700">
                          {unresolved.length} → semantic
                        </span>
                      )}
                    </>
                  )}
                  {missingCols.length > 0 && (
                    <span
                      className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-red-50 text-red-700 border border-red-200"
                      title={`Identified in unique-table identification but not in the mapping (removed in pre-processing — empty or duplicate column): ${missingCols.join(", ")}`}
                    >
                      {missingCols.length} not mapped
                    </span>
                  )}
                  {allApproved && (
                    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
                      All approved
                    </span>
                  )}
                </div>
                {isOpen
                  ? <ChevronUp size={16} className="text-slate-400 shrink-0" />
                  : <ChevronDown size={16} className="text-slate-400 shrink-0" />}
              </button>

              {isOpen && (
                <div className="border-t border-slate-100 divide-y divide-slate-100">
                  {missingCols.length > 0 && (
                    <div className="px-5 py-2.5 bg-red-50/60 text-xs text-red-800">
                      <span className="font-medium">
                        {missingCols.length} of {identifiedCols.length} identified column
                        {identifiedCols.length === 1 ? "" : "s"} not in the mapping:
                      </span>{" "}
                      <span className="font-mono">{missingCols.join(", ")}</span>
                      <div className="mt-0.5 text-[11px] text-red-700/80">
                        These were removed in pre-processing (empty or duplicate columns) — they
                        carry no data to map. If that&apos;s unexpected, re-check the source sheet.
                      </div>
                    </div>
                  )}
                  {items.length > 0 && (
                    <div className="px-5 py-2.5 bg-slate-50 flex items-center gap-2 text-xs">
                      <span className="text-slate-500">Target table:</span>
                      <span className="font-mono px-2 py-0.5 rounded bg-white border border-slate-200 text-slate-700">
                        {tableTargets[tbl]?.target || tbl}
                      </span>
                      {tableTargets[tbl]?.isNew && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-indigo-100 text-indigo-700">
                          new table
                        </span>
                      )}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setPhase("tables");
                        }}
                        className="ml-auto text-indigo-600 hover:text-indigo-800 font-medium"
                      >
                        Change
                      </button>
                    </div>
                  )}
                  {tableTargets[tbl]?.isNew && (items.length > 0 || unresolved.length > 0) && (
                    <div className="px-5 py-3 bg-indigo-50/60 text-xs text-indigo-900 border-b border-indigo-100">
                      <div className="font-medium mb-0.5">New table — no column matching needed.</div>
                      <div className="text-indigo-800/80">
                        The target schema doesn&rsquo;t exist in CAFM yet. Source columns
                        below will be created as-is on the new table; you can still rename
                        any target column. No matches against an unrelated existing table
                        will be applied.
                      </div>
                    </div>
                  )}
                  {(() => {
                    // Challenge 1: partial-similarity column pairs (60-95% value overlap) —
                    // prompt the user to review/merge or keep separate.
                    const pairs = payload?.near_duplicate_columns_by_table?.[tbl] ?? [];
                    if (!pairs.length) return null;
                    return (
                      <div className="px-5 py-3 bg-amber-50/70 border-b border-amber-100 text-xs text-amber-900">
                        <div className="font-medium mb-1">
                          ⚠ Review — these column pairs have similar (but not identical) values. Decide whether to merge or keep separate:
                        </div>
                        <ul className="space-y-0.5">
                          {pairs.map((p, i) => (
                            <li key={i} className="font-mono">
                              {p.column_a} ↔ {p.column_b}{" "}
                              <span className="text-amber-700">({Math.round((p.overlap ?? 0) * 100)}% overlap)</span>
                              {p.sample_a && p.sample_a.length ? (
                                <span className="text-amber-700/70"> · e.g. {p.sample_a.slice(0, 2).join(", ")}</span>
                              ) : null}
                            </li>
                          ))}
                        </ul>
                        <div className="mt-1 text-amber-800/80">
                          To merge, point both columns at the same target (rename one in its dropdown); otherwise leave them as separate columns.
                        </div>
                      </div>
                    );
                  })()}
                  {items.length === 0 && unresolved.length === 0 && autoApproved.length === 0 && (
                    <div className="px-5 py-4 text-sm text-slate-400">
                      {tableTargets[tbl]?.isNew
                        ? "This new table will be created with the columns detected in the source."
                        : "No fields in this table."}
                    </div>
                  )}
                  {items.map((item, idx) => {
                    const key = `${tbl}.${item.source_field}`;
                    const isNewTbl = !!tableTargets[tbl]?.isNew;
                    // NEW table: target defaults to snake_case(source); the
                    // backend's canonical match (against an unrelated table) is
                    // discarded. EXISTING table: keep the backend's target_field.
                    const newTableTarget = isNewTbl ? toSnakeCase(item.source_field) : item.target_field;
                    const defaultTarget = isNewTbl ? newTableTarget : item.target_field;
                    const action = isNewTbl ? "approve" : (decisions[key] ?? "approve");
                    const confPct = Math.round((item.confidence ?? 0) * 100);
                    // When the user re-targeted this table, the row reflects the
                    // match against the NEW table instead of the original tier/conf.
                    const remap = isNewTbl ? undefined : remapByTable[tbl]?.[item.source_field];
                    const effectiveTarget = tableTargets[tbl]?.target ?? tbl;

                    return (
                      <div key={idx} className="px-5 py-3 flex items-start gap-4">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            {(() => {
                              // Show the CANONICAL name when the Column Intelligence panel
                              // has resolved one (backend column_canonical + any user pins);
                              // otherwise fall back to the raw source column. This keeps
                              // Step 2 labels consistent with B14 / B19 / B21 — when the
                              // unified name is 'asset_id', this row says 'asset_id' too,
                              // with the original column shown as a tiny 'was …' subtitle.
                              const canonical = effectiveCanonicalFor(tbl, item.source_field);
                              const displayName =
                                canonical && canonical !== item.source_field ? canonical : item.source_field;
                              const renamed = displayName !== item.source_field;
                              return (
                                <div className="flex flex-col leading-tight">
                                  <button
                                    type="button"
                                    onClick={() => handleTermClick(item.source_field)}
                                    className={`font-mono text-xs px-2 py-0.5 rounded transition-colors cursor-pointer self-start ${
                                      focusedTerm === item.source_field
                                        ? "bg-amber-200 text-slate-900 ring-1 ring-amber-400"
                                        : renamed
                                        ? "bg-indigo-50 text-indigo-700 hover:bg-amber-100"
                                        : "bg-slate-100 text-slate-700 hover:bg-amber-100"
                                    }`}
                                    title={renamed ? `Canonical name — original column: ${item.source_field}` : undefined}
                                  >
                                    {displayName}
                                  </button>
                                  {renamed ? (
                                    <span className="font-mono text-[9.5px] text-slate-400 mt-0.5 pl-1">
                                      was {item.source_field}
                                    </span>
                                  ) : null}
                                </div>
                              );
                            })()}
                            {item.is_primary_key ? (
                              <span
                                className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-100 text-amber-800 border border-amber-300"
                                title="Primary key — the table's identity column"
                              >
                                PK
                              </span>
                            ) : null}
                            {item.merged_source_fields && item.merged_source_fields.length > 0 ? (
                              <span
                                className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-purple-50 text-purple-700 border border-purple-200"
                                title={`Identical values — merged into one column: ${item.merged_source_fields.join(", ")}`}
                              >
                                + merged: {item.merged_source_fields.join(", ")}
                              </span>
                            ) : null}
                            <ArrowRight size={11} className="text-slate-300 shrink-0" />
                            {!isNewTbl && targetCols.length ? (
                              remap ? (
                                // Rerouted table: the dropdown must offer ONLY the NEW
                                // target table's columns (plus "→ semantic"), and default
                                // to the re-matched column or semantic — never the stale
                                // column carried over from the previous target.
                                (() => {
                                  const reCur =
                                    fieldRenames[key] ?? (remap.matched ? remap.target : "");
                                  const value = targetCols.includes(reCur) ? reCur : "";
                                  return (
                                    <select
                                      value={value}
                                      onChange={(e) =>
                                        setFieldRenames((prev) => ({ ...prev, [key]: e.target.value }))
                                      }
                                      title="Target column — pick a column on the target table, or send to semantic"
                                      className={`font-mono text-xs px-2 py-1 rounded border w-44 cursor-pointer transition-colors hover:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 ${
                                        value
                                          ? "border-indigo-100 bg-indigo-50 text-indigo-700"
                                          : "border-slate-200 bg-white text-slate-600"
                                      }`}
                                    >
                                      <option value="">→ semantic (let AI match)</option>
                                      {targetCols.map((c) => (
                                        <option key={c} value={c}>
                                          {c}
                                        </option>
                                      ))}
                                    </select>
                                  );
                                })()
                              ) : (
                                <select
                                  value={fieldRenames[key] ?? item.target_field}
                                  onChange={(e) =>
                                    setFieldRenames((prev) => ({ ...prev, [key]: e.target.value }))
                                  }
                                  title="Target column — pick any column on the target table"
                                  className={`font-mono text-xs px-2 py-1 rounded border w-44 cursor-pointer transition-colors hover:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 ${
                                    (fieldRenames[key] ?? item.target_field) !== item.target_field
                                      ? "border-amber-300 bg-amber-50 text-amber-800"
                                      : "border-indigo-100 bg-indigo-50 text-indigo-700"
                                  }`}
                                >
                                  {(() => {
                                    const cur = fieldRenames[key] ?? item.target_field;
                                    const opts = targetCols.includes(cur) ? targetCols : [cur, ...targetCols];
                                    return opts.map((c) => (
                                      <option key={c} value={c}>
                                        {c}
                                      </option>
                                    ));
                                  })()}
                                </select>
                              )
                            ) : (
                              <input
                                value={fieldRenames[key] ?? defaultTarget}
                                onChange={(e) =>
                                  setFieldRenames((prev) => ({ ...prev, [key]: e.target.value }))
                                }
                                onFocus={() => handleTermClick(defaultTarget)}
                                spellCheck={false}
                                title={isNewTbl ? "New column name — defaults to snake_case of the source" : "Target column — edit to rename"}
                                className={`font-mono text-xs px-2 py-0.5 rounded border w-40 focus:outline-none focus:ring-2 focus:ring-indigo-400 ${
                                  isNewTbl
                                    ? "border-indigo-200 bg-indigo-50 text-indigo-700"
                                    : (fieldRenames[key] ?? item.target_field) !== item.target_field
                                      ? "border-amber-300 bg-amber-50 text-amber-800"
                                      : "border-indigo-100 bg-indigo-50 text-indigo-700"
                                }`}
                              />
                            )}
                            {isNewTbl ? (
                              <>
                                <select
                                  value={newColumnTypes[`${tbl}.${item.source_field}`] ?? inferDataType(item.source_field)}
                                  onChange={(e) =>
                                    setNewColumnTypes((prev) => ({ ...prev, [`${tbl}.${item.source_field}`]: e.target.value }))
                                  }
                                  title="SQL type for the new column"
                                  className="font-mono text-xs px-2 py-1 rounded border border-indigo-200 bg-white text-indigo-700 w-32 cursor-pointer transition-colors hover:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                                >
                                  {DATA_TYPES.map((t) => (
                                    <option key={t} value={t}>{t}</option>
                                  ))}
                                </select>
                                <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-700">
                                  T1_new_table
                                </span>
                                <span className="inline-flex items-center gap-1 text-xs font-medium text-green-700">
                                  <CheckCircle size={12} />Auto-approved
                                </span>
                              </>
                            ) : remap ? (
                              remap.matched ? (
                                (() => {
                                  // Show the match score (like the standard rows show
                                  // a %), not the word "exact" — a remap is often a
                                  // fuzzy fit (e.g. full_name → vendor_name 65%).
                                  const matchPct = Math.round(
                                    ((remapCandidatesByTable[tbl]?.[item.source_field] ?? []).find(
                                      (c) => c.target_field === remap.target,
                                    )?.confidence ?? 0) * 100,
                                  );
                                  return (
                                    <>
                                      <TierBadge tier="table_exact" />
                                      <span
                                        className={`text-xs font-mono font-semibold ${
                                          matchPct >= 95
                                            ? "text-green-600"
                                            : matchPct >= 85
                                              ? "text-amber-600"
                                              : "text-red-500"
                                        }`}
                                      >
                                        {matchPct}%
                                      </span>
                                    </>
                                  );
                                })()
                              ) : null
                            ) : item.b21_new_column ? (
                              // Aligned to B21.1 (destination column mapping): rendered as a new
                              // column below — suppress the stale tier/% that referenced the
                              // now-replaced destination column (avoids a contradictory badge).
                              null
                            ) : (() => {
                              const pinned = effectiveCanonicalFor(tbl, item.source_field);
                              const hasPin =
                                Object.keys(canonicalOverrides).length > 0
                                && !!pinned
                                && pinned !== item.source_field;
                              // Entity mismatch: pinned canonical carries an entity prefix
                              // (asset_id) that doesn't match the destination table's
                              // singular (vendor). The deterministic 'id → id' match shown
                              // by tier+conf is stale — after re-match this row becomes a
                              // new column, NOT a 98% existing-column auto-resolve.
                              const targetCol = item.target_field;
                              const entityMismatch =
                                hasPin && _entityPrefixConflict(pinned!, targetCol, effectiveTarget);
                              return (
                                <>
                                  <TierBadge tier={item.tier} />
                                  <span className={`text-xs font-mono font-semibold ${
                                    entityMismatch
                                      ? "text-slate-400 line-through"
                                      : confPct >= 95 ? "text-green-600"
                                      : confPct >= 85 ? "text-amber-600"
                                      : "text-red-500"
                                  }`}>{confPct}%</span>
                                  {hasPin && entityMismatch ? (
                                    <span
                                      className="inline-flex items-center gap-1 rounded-full bg-red-50 text-red-700 border border-red-200 px-2 py-0.5 text-[10px] font-medium"
                                      title={`The pinned canonical '${pinned}' belongs to a different entity than '${effectiveTarget}'. The 'id → id' name match above is stale — after re-match this column will be created as a NEW column on ${effectiveTarget}, not merged with an existing one.`}
                                    >
                                      ✗ entity mismatch — will be new column
                                    </span>
                                  ) : hasPin ? (
                                    <span
                                      className="inline-flex items-center gap-1 rounded-full bg-amber-50 text-amber-700 border border-amber-200 px-2 py-0.5 text-[10px] font-medium"
                                      title={`A canonical was pinned for this column ('${pinned}'). It will re-match against destination columns when you advance the gate.`}
                                    >
                                      ⟳ pin pending re-match
                                    </span>
                                  ) : null}
                                </>
                              );
                            })()}
                            {(() => {
                              // Step-2 ↔ B21 alignment: does this column map to an EXISTING
                              // destination column (data populates it) or will it be CREATED
                              // as a NEW column on the target table? Computed live from the
                              // current target so it updates as the user edits the mapping.
                              if (isNewTbl) {
                                return (
                                  <span
                                    className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-indigo-100 text-indigo-700 border border-indigo-200"
                                    title={`'${effectiveTarget}' is a new table — every column is created.`}
                                  >
                                    new column
                                  </span>
                                );
                              }
                              const act = decisions[key] ?? "approve";
                              const curTgt = remap
                                ? remap.matched
                                  ? (fieldRenames[key] ?? remap.target)
                                  : (fieldRenames[key] ?? "")
                                : (fieldRenames[key] ?? item.target_field);
                              if (act === "semantic" || !curTgt) return null;
                              // Entity-mismatch suppression: same check as above —
                              // if the pinned canonical clashes with the destination
                              // entity, the row WILL become a new column after re-match,
                              // so don't badge the stale match as 'existing column'.
                              const _pinNow = effectiveCanonicalFor(tbl, item.source_field);
                              const _hasPinNow =
                                Object.keys(canonicalOverrides).length > 0
                                && !!_pinNow
                                && _pinNow !== item.source_field;
                              const _entityMismatchNow =
                                _hasPinNow && _entityPrefixConflict(_pinNow!, curTgt, effectiveTarget);
                              const exists = targetCols.some((c) => normCol(c) === normCol(curTgt));
                              return exists && !_entityMismatchNow ? (
                                <span
                                  className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-green-50 text-green-700 border border-green-200"
                                  title={`Maps to the existing '${curTgt}' column on '${effectiveTarget}' — that column is populated, not created.`}
                                >
                                  existing column
                                </span>
                              ) : (
                                <span
                                  className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-indigo-100 text-indigo-700 border border-indigo-200"
                                  title={
                                    _entityMismatchNow
                                      ? `Entity mismatch: pinned canonical '${_pinNow}' belongs to a different entity than '${effectiveTarget}'. The 'id → ${curTgt}' name match is stale — after re-match this column becomes a NEW column on ${effectiveTarget}, not a merge.`
                                      : `'${curTgt}' is not a column on '${effectiveTarget}' — it will be created as a new column.`
                                  }
                                >
                                  new column
                                </span>
                              );
                            })()}
                          </div>
                          {(() => {
                            // UF4.3-AC3 / UF4.2-AC4 — flag a user-overridden mapping
                            // and show the original system suggestion for reference.
                            const autoTarget = remap
                              ? remap.matched
                                ? remap.target
                                : ""
                              : item.target_field;
                            const userModified =
                              !isNewTbl &&
                              fieldRenames[key] !== undefined &&
                              fieldRenames[key] !== autoTarget;
                            if (!userModified) return null;
                            return (
                              <p className="mt-0.5 text-[10px] font-medium text-amber-700">
                                User-modified
                                {autoTarget
                                  ? ` — system suggested '${autoTarget}'`
                                  : " — system routed to semantic"}
                              </p>
                            );
                          })()}
                          {(() => {
                            // UF4.2-AC6 — duplicate-name validation: alert when this
                            // column's target is already used by another column.
                            const myTarget = normCol(fieldRenames[key] ?? item.target_field);
                            if (!isNewTbl && myTarget && (targetCounts.get(myTarget) ?? 0) > 1) {
                              return (
                                <p className="mt-0.5 text-[10px] font-medium text-red-600">
                                  ⚠ Duplicate target — another column also maps to this column.
                                </p>
                              );
                            }
                            return null;
                          })()}
                          {remap ? (
                            remap.matched ? (
                              <p className="text-xs text-slate-400 mt-1 truncate">
                                {`Exact column match on target table '${effectiveTarget}'`}
                              </p>
                            ) : null
                          ) : item.rationale ? (
                            <p className="text-xs text-slate-400 mt-1 truncate">{item.rationale}</p>
                          ) : null}
                          {(() => {
                            // When the table was retargeted to an existing CAFM table,
                            // show the fits re-ranked against THAT table's columns;
                            // otherwise fall back to the backend's deterministic candidates.
                            const fitList = remapCandidatesByTable[tbl]?.[item.source_field] ?? item.candidates;
                            // B21 demoted this to a new column — the old fits pointed at the
                            // destination column we're deliberately NOT using; hide them.
                            return !isNewTbl && !item.b21_new_column && Array.isArray(fitList) && fitList.length > 0 ? (
                            <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
                              <span className="text-[10px] text-slate-400">fits:</span>
                              {fitList.slice(0, 3).map((c) => {
                                const cpct = Math.round((c.confidence ?? 0) * 100);
                                // Match the dropdown's remap-aware selection so the
                                // active chip reflects the NEW target, not the stale one.
                                const cur =
                                  fieldRenames[key] ??
                                  (remap ? (remap.matched ? remap.target : "") : item.target_field);
                                const active = cur === c.target_field;
                                return (
                                  <button
                                    key={c.target_field}
                                    type="button"
                                    onClick={() => {
                                      setFieldRenames((prev) => ({ ...prev, [key]: c.target_field }));
                                      // Picking a real target column means it maps — approve it.
                                      setDecisions((prev) => ({ ...prev, [key]: "approve" }));
                                    }}
                                    title={("is_primary" in c && c.is_primary) ? "Best deterministic fit" : "Alternative fit — click to use"}
                                    className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-mono transition-colors ${
                                      active
                                        ? "border-indigo-400 bg-indigo-50 text-indigo-700"
                                        : "border-slate-200 bg-white text-slate-600 hover:border-indigo-300"
                                    }`}
                                  >
                                    {c.target_field}
                                    <span
                                      className={
                                        cpct >= 90
                                          ? "text-green-600"
                                          : cpct >= 70
                                            ? "text-amber-600"
                                            : "text-red-500"
                                      }
                                    >
                                      {cpct}%
                                    </span>
                                  </button>
                                );
                              })}
                            </div>
                            ) : null;
                          })()}
                          {/* Hide the type/sample line on rows routing to semantic
                              (no target column chosen) — keeps those rows clean. */}
                          {!(remap && !remap.matched) &&
                          (item.data_type || (item.sample_values && item.sample_values.length > 0)) ? (
                            <div className="mt-1 flex items-center gap-2 flex-wrap text-[10px] text-slate-400">
                              {item.data_type ? (
                                <span className="font-mono">type: {item.data_type}</span>
                              ) : null}
                              {item.sample_values && item.sample_values.length > 0 ? (
                                <span className="font-mono truncate">
                                  e.g. {item.sample_values.slice(0, 5).join(", ")}
                                </span>
                              ) : null}
                            </div>
                          ) : null}
                        </div>
                        {isNewTbl ? null : (
                          <div className="flex gap-1.5 shrink-0 pt-0.5">
                            <button
                              onClick={() => setDecision(tbl, item.source_field, "approve")}
                              className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors ${
                                action === "approve" ? "bg-green-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                              }`}
                            >
                              <CheckCircle size={12} />Approve
                            </button>
                            <button
                              onClick={() => setDecision(tbl, item.source_field, "semantic")}
                              className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors ${
                                action === "semantic" ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                              }`}
                            >
                              <Search size={12} />Semantic
                            </button>
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {/* Auto-approved deterministic mappings (identity / alias) — read-only, so
                      every mapped source column is visible (e.g. site_ref → id). */}
                  {autoApproved.map((item) => {
                    return (
                    <div key={`auto-${item.source_field}`} className="px-5 py-3 flex items-center gap-3 bg-emerald-50/30">
                      <div className="flex-1 min-w-0 flex items-center gap-2 flex-wrap">
                        {(() => {
                          // Auto-approved rows use the SAME canonical-aware label as the
                          // review rows above — pinned/derived canonical first, raw
                          // column name shown as a 'was …' subtitle.
                          const canonical = effectiveCanonicalFor(tbl, item.source_field);
                          const displayName =
                            canonical && canonical !== item.source_field ? canonical : item.source_field;
                          const renamed = displayName !== item.source_field;
                          return (
                            <div className="flex flex-col leading-tight">
                              <span
                                className={`font-mono text-xs px-2 py-0.5 rounded self-start ${
                                  renamed ? "bg-indigo-50 text-indigo-700" : "bg-slate-100 text-slate-600"
                                }`}
                                title={renamed ? `Canonical name — original column: ${item.source_field}` : undefined}
                              >
                                {displayName}
                              </span>
                              {renamed ? (
                                <span className="font-mono text-[9.5px] text-slate-400 mt-0.5 pl-1">
                                  was {item.source_field}
                                </span>
                              ) : null}
                            </div>
                          );
                        })()}
                        <ArrowRight size={11} className="text-slate-300 shrink-0" />
                        <span className="font-mono text-xs px-2 py-0.5 rounded bg-emerald-100 text-emerald-800">{item.target_field}</span>
                        {item.tier ? (
                          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-slate-100 text-slate-500">
                            {item.tier === "T1_identity" ? "identity → id" : plainTierWord((item.tier || "").replace(/^T1_/, ""))}
                          </span>
                        ) : null}
                        {(() => {
                          // Step-2 ↔ B21 alignment: identity/alias auto-matches resolve to a
                          // real column on the target table (existing); flag the rare case
                          // where the target isn't a known column as a new column.
                          const exists = targetCols.some((c) => normCol(c) === normCol(item.target_field));
                          return exists ? (
                            <span
                              className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-green-50 text-green-700 border border-green-200"
                              title={`Maps to the existing '${item.target_field}' column on '${confirmedTarget}' — populated, not created.`}
                            >
                              existing column
                            </span>
                          ) : (
                            <span
                              className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-indigo-100 text-indigo-700 border border-indigo-200"
                              title={`'${item.target_field}' is not a column on '${confirmedTarget}' — it will be created as a new column.`}
                            >
                              new column
                            </span>
                          );
                        })()}
                        {item.sample_values && item.sample_values.length > 0 ? (
                          <span className="font-mono text-[10px] text-slate-400 truncate">
                            e.g. {item.sample_values.slice(0, 5).join(", ")}
                          </span>
                        ) : null}
                      </div>
                      <span className="inline-flex items-center gap-1 text-xs font-medium px-2.5 py-1.5 rounded-lg bg-emerald-50 text-emerald-700 border border-emerald-200 shrink-0">
                        <CheckCircle size={12} />Auto-approved
                      </span>
                    </div>
                    );
                  })}
                  {/* Unresolved ("left-out") source fields. Offer a dropdown of the
                      leftover target columns so the user can assign one, else semantic. */}
                  {unresolved.map((field) => {
                    const k = `${tbl}.${field}`;
                    // Backend best-guess auto-suggestion for this unmapped field (target + %).
                    const sug = payload?.unresolved_suggestion_by_table?.[tbl]?.[field];
                    const sugTarget = (sug?.target_field ?? "").trim();
                    const sugInLeftover = !!sugTarget && leftoverCols.includes(sugTarget);
                    const exactSug = leftoverCols.find((c) => normCol(c) === normCol(field)) ?? "";
                    // A leftover column whose name matches this field's CANONICAL (site_id for
                    // property_ref) — preferred over the weak fuzzy suggestion (document_ids).
                    const canonSug = canonicalColMatch(tbl, field, leftoverCols);
                    // Unresolved fields default to Semantic (blue) — NO exact/canonical/fuzzy
                    // auto-map. Only the user's explicit pick assigns a target, so the row, the
                    // counter, and handleSubmit all agree. The user can still one-click Approve to
                    // the exact/canonical/suggested target (defaultAssign below keeps those).
                    const assigned = unresolvedAssign[k] ?? "";
                    const isNewTbl = !!tableTargets[tbl]?.isNew;
                    if (isNewTbl) {
                      // NEW table: every unresolved field is auto-approved as
                      // a new column on the new table. No semantic option, no
                      // canonical leftover dropdown — the column is deterministic.
                      const renameKey = `${tbl}.${field}`;
                      const newColName = (fieldRenames[renameKey] ?? "").trim() || toSnakeCase(field);
                      return (
                        <div key={field} className="px-5 py-3 flex items-center gap-3">
                          <div className="flex-1 min-w-0 flex items-center gap-2 flex-wrap">
                            <span className="font-mono text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-500">{field}</span>
                            <ArrowRight size={11} className="text-slate-300 shrink-0" />
                            <input
                              value={newColName}
                              onChange={(e) =>
                                setFieldRenames((prev) => ({ ...prev, [renameKey]: e.target.value }))
                              }
                              spellCheck={false}
                              title="New column name — defaults to snake_case of the source"
                              className="font-mono text-xs px-2 py-0.5 rounded border w-40 border-indigo-200 bg-indigo-50 text-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                            />
                            <select
                              value={newColumnTypes[renameKey] ?? inferDataType(field)}
                              onChange={(e) =>
                                setNewColumnTypes((prev) => ({ ...prev, [renameKey]: e.target.value }))
                              }
                              title="SQL type for the new column"
                              className="font-mono text-xs px-2 py-1 rounded border border-indigo-200 bg-white text-indigo-700 w-32 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                            >
                              {DATA_TYPES.map((t) => (
                                <option key={t} value={t}>{t}</option>
                              ))}
                            </select>
                            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-700">
                              T1_new_table
                            </span>
                            <span
                              className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-indigo-100 text-indigo-700 border border-indigo-200"
                              title="New table — this column is created."
                            >
                              new column
                            </span>
                          </div>
                          <span className="inline-flex items-center gap-1 text-xs font-medium px-2.5 py-1.5 rounded-lg bg-green-50 text-green-700 border border-green-200 shrink-0">
                            <CheckCircle size={12} />Auto-approved
                          </span>
                        </div>
                      );
                    }
                    return (
                      <div key={field} className="px-5 py-3 flex items-center gap-3">
                        <div className="flex-1 min-w-0 flex items-center gap-2 flex-wrap">
                          <span className="font-mono text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-500">{field}</span>
                          <ArrowRight size={11} className="text-slate-300 shrink-0" />
                          {leftoverCols.length ? (
                            <select
                              value={assigned}
                              onChange={(e) =>
                                setUnresolvedAssign((prev) => ({ ...prev, [k]: e.target.value }))
                              }
                              title="Assign a leftover column on the target table, or send to semantic"
                              className={`font-mono text-xs px-2 py-1 rounded border w-44 cursor-pointer transition-colors hover:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 ${
                                assigned ? "border-amber-300 bg-amber-50 text-amber-800" : "border-slate-200 bg-white text-slate-500"
                              }`}
                            >
                              <option value="">→ semantic (let AI match)</option>
                              {leftoverCols.map((c) => (
                                <option key={c} value={c}>
                                  {c}
                                </option>
                              ))}
                            </select>
                          ) : (
                            <span className="text-xs text-slate-400 italic">no remaining column</span>
                          )}
                          {assigned ? (
                            <span className="text-[10px] text-slate-400">on {confirmedTarget}</span>
                          ) : null}
                          {/* Step-2 ↔ B21 alignment: an assigned leftover is always a real
                              column of the target table (existing); otherwise → semantic. */}
                          {assigned ? (
                            <span
                              className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-green-50 text-green-700 border border-green-200"
                              title={`Maps to the existing '${assigned}' column on '${confirmedTarget}' — populated, not created.`}
                            >
                              existing column
                            </span>
                          ) : (
                            <span
                              className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-50 text-amber-700 border border-amber-200"
                              title="No destination column assigned — this field is sent to semantic matching."
                            >
                              → semantic
                            </span>
                          )}
                          {/* Example data for the source column, so the user sees what they're
                              mapping when picking a target (mirrors the matched-row preview). */}
                          {sug?.sample_values && sug.sample_values.length > 0 ? (
                            <span className="font-mono text-[10px] text-slate-400 truncate">
                              type: {sug.data_type || inferDataType(field)} · e.g.{" "}
                              {sug.sample_values.slice(0, 5).join(", ")}
                            </span>
                          ) : null}
                          {/* Top-3 auto-suggestions with mapping %, best-first. Click one to
                              assign it (when it's a free target column), else send to semantic. */}
                          {Array.isArray(sug?.candidates) && sug.candidates.length > 0 ? (
                            <span className="inline-flex items-center gap-1.5 flex-wrap">
                              <span className="text-[10px] text-slate-400">suggested:</span>
                              {sug.candidates.slice(0, 3).map((c) => {
                                const cpct = Math.round((c.confidence ?? 0) * 100);
                                const tgt = (c.target_field ?? "").trim();
                                const free = !!tgt && leftoverCols.includes(tgt);
                                const active = assigned === tgt;
                                return (
                                  <button
                                    key={tgt}
                                    type="button"
                                    disabled={!free}
                                    onClick={() =>
                                      free && setUnresolvedAssign((prev) => ({ ...prev, [k]: tgt }))
                                    }
                                    title={
                                      free
                                        ? `Use '${tgt}' (${cpct}% name match) — or send to semantic if none fit`
                                        : `'${tgt}' (${cpct}%) isn't a free column here`
                                    }
                                    className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-mono transition-colors ${
                                      active
                                        ? "border-indigo-400 bg-indigo-50 text-indigo-700"
                                        : free
                                          ? "border-slate-200 bg-white text-slate-600 hover:border-indigo-300"
                                          : "border-slate-100 bg-slate-50 text-slate-400 cursor-default"
                                    }`}
                                  >
                                    {tgt}
                                    <span
                                      className={
                                        cpct >= 90 ? "text-green-600" : cpct >= 70 ? "text-amber-600" : "text-red-500"
                                      }
                                    >
                                      {cpct}%
                                    </span>
                                  </button>
                                );
                              })}
                            </span>
                          ) : null}
                        </div>
                        {/* Same Approve / Semantic choice as the matched rows. The mapping is
                            already selected (Approve, green) when a target column is assigned —
                            the user can flip it to Semantic to send the field to the AI. */}
                        {(() => {
                          const defaultAssign = exactSug || canonSug || (sugInLeftover ? sugTarget : "");
                          const canApprove = !!assigned || !!defaultAssign;
                          return (
                            <div className="flex gap-1.5 shrink-0 pt-0.5">
                              <button
                                type="button"
                                disabled={!canApprove}
                                title={canApprove ? "Approve — map to the assigned column" : "Pick a target column above, or send to semantic"}
                                onClick={() =>
                                  setUnresolvedAssign((prev) => ({ ...prev, [k]: (prev[k] ?? assigned) || defaultAssign }))
                                }
                                className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors disabled:opacity-40 ${
                                  assigned ? "bg-green-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                                }`}
                              >
                                <CheckCircle size={12} />Approve
                              </button>
                              <button
                                type="button"
                                onClick={() => setUnresolvedAssign((prev) => ({ ...prev, [k]: "" }))}
                                className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors ${
                                  !assigned ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                                }`}
                              >
                                <Search size={12} />Semantic
                              </button>
                            </div>
                          );
                        })()}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

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
            {/* Surface BOTH semantic buckets like the summary cards do: the user's own "semantic"
                decisions AND the auto-routed unresolved fields (Auto → Semantic). Before, the
                button showed only `countSemantic`, reading "0 → semantic" even while 19 fields were
                being auto-sent to semantic — the reported wrong count. Mirrors the schema gate's
                "· N Tier-2 semantic" segment. */}
            Confirm — {countApproved} approved · {countSemantic} → semantic
            {totalAutoSemantic > 0 ? ` · ${totalAutoSemantic} auto → semantic` : ""}
          </>
        )}
      </button>
      </>
      )}
    </div>
  );
}
