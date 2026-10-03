import type { MigrationFlaggedFieldItem } from "../../chat-api";
import { DEFAULT_PLENUM_CANONICAL_TABLES } from "./migration-gate-state";

export type SuggestionOption = {
  field: string;
  confidence: number | null;
};

/** Map sheet/display names (e.g. "Work Orders") to plenum_cafm table slugs (work_orders). */
export function toPlenumTableSlug(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return trimmed;
  const slug = trimmed
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/[^a-zA-Z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .toLowerCase();
  if (DEFAULT_PLENUM_CANONICAL_TABLES.includes(slug)) return slug;
  const compact = slug.replace(/_/g, "");
  for (const known of DEFAULT_PLENUM_CANONICAL_TABLES) {
    if (known === slug) return known;
    if (known.replace(/_/g, "") === compact) return known;
  }
  return slug;
}

export function resolveDefaultCanonicalTable(sourceTable: string, canonicalTables: string[]): string {
  const slug = toPlenumTableSlug(sourceTable);
  if (canonicalTables.includes(slug)) return slug;
  for (const t of canonicalTables) {
    if (toPlenumTableSlug(t) === slug) return t;
  }
  return slug || (canonicalTables[0] ?? "");
}

export function getSuggestedTarget(item: MigrationFlaggedFieldItem): string {
  const rec = item as unknown as Record<string, unknown>;
  const suggested = typeof rec.suggested_target === "string" ? rec.suggested_target : null;
  const direct = item.target_field ?? null;
  if (direct && direct.trim().length) return direct;
  if (suggested && suggested.trim().length) return suggested;
  if (Array.isArray(item.suggestions) && item.suggestions.length) {
    const first = parseSuggestionOptions(item.suggestions)[0]?.field ?? "";
    if (first.trim().length) return first;
  }
  return "";
}

export function parseSuggestionOptions(input: unknown[]): SuggestionOption[] {
  const bestByField = new Map<string, SuggestionOption>();

  for (const it of input) {
    let field = "";
    let confidence: number | null = null;

    if (typeof it === "string") {
      field = it;
    } else if (it && typeof it === "object") {
      const r = it as Record<string, unknown>;
      field =
        (typeof r.field === "string" && r.field) ||
        (typeof r.target_field === "string" && r.target_field) ||
        (typeof r.target === "string" && r.target) ||
        (typeof r.value === "string" && r.value) ||
        (typeof r.name === "string" && r.name) ||
        "";

      const rawConf =
        (typeof r.confidence === "number" && Number.isFinite(r.confidence) && r.confidence) ||
        (typeof r.score === "number" && Number.isFinite(r.score) && r.score) ||
        (typeof r.similarity === "number" && Number.isFinite(r.similarity) && r.similarity) ||
        null;
      if (rawConf != null) {
        confidence = rawConf > 1 ? rawConf / 100 : rawConf;
      }
    } else if (it != null) {
      field = String(it);
    }

    if (!field.trim().length) continue;

    const next: SuggestionOption = { field, confidence };
    const prev = bestByField.get(field);
    if (!prev) {
      bestByField.set(field, next);
      continue;
    }
    const prevConf = prev.confidence ?? -1;
    const nextConf = next.confidence ?? -1;
    if (nextConf > prevConf) bestByField.set(field, next);
  }

  return Array.from(bestByField.values()).sort((a, b) => (b.confidence ?? -1) - (a.confidence ?? -1));
}

function normalizeConfidence(raw: number | null | undefined): number | null {
  if (raw == null || !Number.isFinite(raw)) return null;
  return raw > 1 ? raw / 100 : raw;
}

/** Merge scores from suggestions, alternatives, target_field, and optional API lookup. */
export function collectMatchScoresFromItem(
  item: MigrationFlaggedFieldItem,
  extraScores?: Record<string, number | null | undefined>,
): Map<string, number> {
  const map = new Map<string, number>();
  const rec = item as unknown as Record<string, unknown>;

  const add = (field: string, conf: number | null | undefined) => {
    const n = normalizeConfidence(conf);
    if (!field.trim() || n == null) return;
    const prev = map.get(field);
    if (prev == null || n > prev) map.set(field, n);
  };

  const suggestionInputs: unknown[] = [];
  if (Array.isArray(item.suggestions)) suggestionInputs.push(...(item.suggestions as unknown[]));
  if (Array.isArray(rec.alternatives)) suggestionInputs.push(...(rec.alternatives as unknown[]));
  if (Array.isArray(rec.top_matches)) suggestionInputs.push(...(rec.top_matches as unknown[]));

  for (const s of parseSuggestionOptions(suggestionInputs)) {
    add(s.field, s.confidence);
  }

  if (item.target_field) add(item.target_field, item.confidence);
  if (typeof rec.suggested_target === "string") add(rec.suggested_target, item.confidence);

  if (extraScores) {
    for (const [field, conf] of Object.entries(extraScores)) add(field, conf);
  }

  return map;
}

export function resolveTargetConfidence(
  item: MigrationFlaggedFieldItem,
  targetField: string,
  extraScores?: Record<string, number | null | undefined>,
): number | null {
  const key = targetField.trim();
  if (!key) return null;
  return collectMatchScoresFromItem(item, extraScores).get(key) ?? null;
}

export type MatchScoreRow = {
  source: string;
  suggested: string;
  score: number | null;
  order: number;
};

/** Score table rows; always includes activeTarget when a score is known. */
export function buildMatchScoreRows(
  item: MigrationFlaggedFieldItem,
  opts: {
    topLimit?: number;
    activeTarget?: string;
    extraScores?: Record<string, number | null | undefined>;
  } = {},
): MatchScoreRow[] {
  const map = collectMatchScoresFromItem(item, opts.extraScores);
  const sorted = Array.from(map.entries())
    .map(([field, score]) => ({ field, score }))
    .sort((a, b) => (b.score ?? -1) - (a.score ?? -1));

  const limit = Math.max(1, opts.topLimit ?? 3);
  const rows = sorted.slice(0, limit);

  const active = opts.activeTarget?.trim();
  if (active && !rows.some((r) => r.field === active)) {
    const score = map.get(active) ?? null;
    if (score != null) {
      rows.push({ field: active, score });
      rows.sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
    }
  }

  return rows.map((s, order) => ({
    source: item.source_field,
    suggested: s.field,
    score: s.score,
    order,
  }));
}

export const MIGRATION_DATA_TYPES = [
  "VARCHAR(255)",
  "VARCHAR(100)",
  "VARCHAR(50)",
  "TEXT",
  "INTEGER",
  "BIGINT",
  "DECIMAL(10,2)",
  "BOOLEAN",
  "TIMESTAMPTZ",
  "DATE",
  "JSONB",
  "UUID",
] as const;
