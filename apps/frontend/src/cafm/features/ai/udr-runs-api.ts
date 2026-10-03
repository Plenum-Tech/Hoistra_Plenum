"use client";

import { env } from "@/config";
import { accessToken as hoistraAccessToken } from "../../../api/client.js";

/**
 * Client for svc-udr run versioning (WP-4).
 * Talks to nginx `/backend/udr/` → svc-udr; the FE persists the last N versions
 * server-side so a saved UDR run survives across devices.
 */
function getUdrBase(): string {
  const explicit = (env.udrBaseUrl ?? "").trim();
  if (explicit) return explicit.replace(/\/+$/, "");

  const da = env.deepAgentsBaseUrl.trim();
  if (da.startsWith("http://") || da.startsWith("https://")) {
    if (da.includes("/backend/deep-agents")) return da.replace(/\/deep-agents\/?$/, "/udr");
    return `${da.replace(/\/+$/, "")}/udr`;
  }
  return "/backend/udr";
}

const UDR_BASE = getUdrBase();

export type UdrRunVersion = {
  id: string;
  session_id: string;
  organization_id: string | null;
  version_no: number;
  custom_name: string | null;
  phase: string | null;
  mapping_status: string | null;
  hierarchy_status: string | null;
  migration_ids: string[];
  document_ids: string[];
  batch_ids: string[];
  snapshot: Record<string, unknown> | null;
  created_at: string;
};

export type CreateUdrRunPayload = {
  sessionId: string;
  organizationId?: string | null;
  customName?: string | null;
  phase?: string | null;
  mappingStatus?: string | null;
  hierarchyStatus?: string | null;
  migrationIds?: string[];
  documentIds?: string[];
  batchIds?: string[];
  snapshot?: Record<string, unknown> | null;
};

async function udrFetch<T>(path: string, init?: RequestInit): Promise<T> {
  // Hoistra: the signed-in user's bearer token, as every Hoistra read sends it.
  const token = hoistraAccessToken();
  const res = await fetch(`${UDR_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = `UDR request failed (${res.status})`;
    try {
      const body = (await res.json()) as {
        errors?: { message?: string }[];
        detail?: string;
      };
      detail = body?.errors?.[0]?.message ?? body?.detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

/**
 * A UDR "script" = one chat session's ordered version history, as grouped by
 * the backend. This is what the left-nav UDR Scripts panel lists (backend-driven
 * so completed migrations appear cross-device + survive localStorage cleanup).
 */
export type UdrScriptSummary = {
  session_id: string;
  organization_id: string | null;
  script_code: string;
  name: string;
  version_count: number;
  latest_version_no: number;
  phase: string | null;
  status: string | null;
  created_at: string;
  updated_at: string;
  migration_ids: string[];
  document_ids: string[];
  source_file_names: string[];
  table_count: number | null;
  column_count: number | null;
  mapped_column_count: number | null;
  mapping_coverage_pct: number | null;
  versions: UdrRunVersion[];
};

export async function listUdrScripts(
  organizationId?: string | null,
  limit = 200,
): Promise<UdrScriptSummary[]> {
  const qs = new URLSearchParams();
  if (organizationId) qs.set("organization_id", organizationId);
  qs.set("limit", String(limit));
  const data = await udrFetch<{ scripts: UdrScriptSummary[] }>(
    `/api/udr/scripts?${qs.toString()}`,
  );
  return data.scripts ?? [];
}

export async function listUdrRuns(sessionId: string, limit = 3): Promise<UdrRunVersion[]> {
  if (!sessionId) return [];
  const data = await udrFetch<{ versions: UdrRunVersion[] }>(
    `/api/udr/runs?session_id=${encodeURIComponent(sessionId)}&limit=${limit}`,
  );
  return data.versions ?? [];
}

export async function createUdrRun(payload: CreateUdrRunPayload): Promise<UdrRunVersion> {
  return udrFetch<UdrRunVersion>(`/api/udr/runs`, {
    method: "POST",
    body: JSON.stringify({
      session_id: payload.sessionId,
      organization_id: payload.organizationId ?? null,
      custom_name: payload.customName ?? null,
      phase: payload.phase ?? null,
      mapping_status: payload.mappingStatus ?? null,
      hierarchy_status: payload.hierarchyStatus ?? null,
      migration_ids: payload.migrationIds ?? [],
      document_ids: payload.documentIds ?? [],
      batch_ids: payload.batchIds ?? [],
      snapshot: payload.snapshot ?? null,
    }),
  });
}

export async function renameUdrRun(runId: string, customName: string): Promise<UdrRunVersion> {
  return udrFetch<UdrRunVersion>(`/api/udr/runs/${encodeURIComponent(runId)}`, {
    method: "PATCH",
    body: JSON.stringify({ custom_name: customName }),
  });
}
