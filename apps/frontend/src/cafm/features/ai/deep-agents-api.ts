// From CAFM Web's features/ai/deep-agents-api.ts — the one helper the migration panel imports.
export function collapseMigrationIdsForUpload(migrationIds: string[]): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const id of migrationIds) {
    const t = id.trim();
    if (!t || seen.has(t)) continue;
    seen.add(t);
    ordered.push(t);
  }
  if (ordered.length <= 1) return ordered;
  return [ordered[0]];
}
