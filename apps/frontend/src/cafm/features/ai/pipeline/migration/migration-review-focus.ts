/** Focus state for migration pipeline row / sidebar highlight (mirrors schema review-focus). */
export type MigrationReviewFocus = {
  scope: "deterministic" | "semantic" | "field_mapping";
  sourceTable?: string;
  sourceField?: string;
  targetField?: string | null;
  nodeHint?: number | null;
};

export function migrationReviewTerms(focus: MigrationReviewFocus | null): string[] {
  if (!focus) return [];
  const terms: string[] = [];
  if (focus.sourceField) terms.push(focus.sourceField);
  if (focus.targetField) terms.push(focus.targetField);
  if (focus.sourceTable) terms.push(focus.sourceTable);
  return terms;
}
