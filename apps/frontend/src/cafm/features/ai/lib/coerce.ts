/** True when value looks like a UUID (backend org/schema IDs). */
export function isUuid(value: string | null | undefined): boolean {
  if (!value?.trim()) return false;
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.trim());
}

export function coerceStringArray(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter((x) => typeof x === "string");
  if (typeof value === "string" && value.trim()) return [value];
  return [];
}

export function coerceArray<T>(value: unknown, isItem: (v: unknown) => v is T): T[] {
  if (Array.isArray(value)) return value.filter(isItem);
  return [];
}

export function formatShortId(id: unknown, len = 8): string {
  const s = id == null ? "" : String(id);
  if (!s) return "—";
  return s.length > len ? `${s.slice(0, len)}…` : s;
}

export function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}
