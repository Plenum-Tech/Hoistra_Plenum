"use client";

import { useMutation, useQuery, type UseMutationOptions } from "@tanstack/react-query";
import { env } from "@/config";
import { aiRequest, buildAiUrl } from "./chat-api";

/** Doc RAG API base — must match nginx `/backend/doc-rag/` (not bare `/doc-rag/`, which hits Next.js). */
function getDocRagBase(): string {
  const explicit = env.docRagBaseUrl.trim();
  if (explicit) return explicit.replace(/\/+$/, "");

  const sm = env.schemaMapperBaseUrl.trim();
  if (sm.startsWith("/backend/schema-mapper")) {
    return "/backend/doc-rag";
  }
  if (sm.startsWith("http://") || sm.startsWith("https://")) {
    if (sm.includes("/backend/schema-mapper")) {
      return sm.replace(/\/schema-mapper\/?$/, "/doc-rag");
    }
    return `${sm.replace(/\/+$/, "")}/doc-rag`;
  }

  return "/backend/doc-rag";
}

const DOC_RAG_BASE = getDocRagBase();

/** PostgreSQL schema for live CMMS tables (row-index db-tables / confirm-matches). */
export const PLENUM_CMMS_SCHEMA = "plenum_cafm";

export type DbTablesEnvelope = {
  schema_name: string;
  tables: DbTable[];
};

// ── Types ─────────────────────────────────────────────────────────────────────

export type DocStatus = "processing" | "extracting" | "indexed" | "error";

const DOC_INGEST_POLL_MS = 2500;
const DOC_INGEST_POLL_MAX_MS = 30 * 60 * 1000;

function sleep(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms));
}

export type DocOut = {
  id: string;
  file_name: string;
  status: DocStatus;
  num_pages: number;
  num_chunks: number;
  created_at: string;
  document_type?: string | null;
};

export type DocUploadResponse = {
  document_id: string;
  status: string;
  file_name: string;
  num_pages: number;
  num_chunks: number;
  processing_time_ms: number;
  document_type?: string | null;
};

export type ChunkPreview = {
  chunk_id: string;
  chunk_index: number;
  page_start: number | null;
  page_end: number | null;
  block_type: string;
  section_label?: string | null;
  text_content: string;
};

export type MatchDetails = {
  semantic_score: number;
  bm25_overlap: number;
  metadata_overlap: number;
  exact_key_match?: boolean;
  normalized_key_match?: boolean;
};

export type ChunkMatch = {
  chunk_id: string;
  chunk_index: number;
  page_number: number | null;
  confidence: number;
  semantic_score: number;
  bm25_score: number;
  metadata_score: number;
  matched_fields: string[];
  chunk_text_preview: string;
};

export type MatchedRow = {
  source_table: string;
  row_pk: string;
  confidence: number;
  match_method: string;
  row_data: Record<string, unknown>;
  evidence?: string | null;
  matched_metadata_fields?: string[];
  match_details?: MatchDetails;
  chunk_matches?: ChunkMatch[];
  chunk_count?: number;
};

/** PRD Test 1 result: every matched chunk must anchor to a real PRIMARY KEY. */
export type UdrTest1 = {
  test_name: string;
  total_checked: number;
  passed: number;
  failed: number;
  fail_rate: number;
  blocked: boolean;
  failing?: { chunk_id: string; value: string; entity_type: string }[];
};

export type MatchRowsResponse = {
  matched_rows: MatchedRow[];
  matched_rows_by_table?: Record<string, MatchedRow[]>;
  by_table: Record<string, number>;
  unique_rows_matched: number;
  total_chunks_analyzed: number;
  udr_test_1?: UdrTest1;
  latency_ms: number;
  source_file?: string;
};

export type ConfirmMatchRow = {
  source_table: string;
  row_pk: string;
};

export type ConfirmMatchesResponse = {
  document_id: string;
  rows_updated: number;
  rows_not_found: number;
  by_table: Record<string, number>;
  columns_created?: string[];
};

export type Citation = {
  document_id: string;
  file_name: string;
  page_start?: number | null;
  page_end?: number | null;
  section?: string | null;
  chunk_id: string;
  quote: string;
};

export type RetrievedChunk = {
  chunk_id: string;
  score: number;
  vector_score: number;
  bm25_score: number;
  block_type: string;
  file_name?: string;
  text_content: string;
};

export type RagQueryResponse = {
  query_id: string;
  query_type: string;
  answer: string;
  confidence: number;
  citations: Citation[];
  matched_rows: MatchedRow[];
  latency_ms: number;
  model_name: string;
  retrieved_chunks?: RetrievedChunk[];
  stages?: { vector_hits: number; bm25_hits: number; fused_reranked: number };
};

export type RowIndexTable = {
  source_table: string;
  row_count: number;
};

export type SuggestedTable = {
  table: string;
  confidence: number;
  semantic_score: number;
  name_match: boolean;
  indexed: boolean;
  sample_rows: string[];
  reason: string;
};

export type SuggestTablesResponse = {
  document_id: string;
  file_name: string;
  document_type?: string | null;
  summary: string;
  suggestions: SuggestedTable[];
  latency_ms: number;
};

export type RowIndexUploadResponse = {
  table_name: string;
  rows_inserted: number;
  rows_updated: number;
  total_rows_in_index: number;
  columns_detected: string[];
  pk_column: string;
};

export type RowIndexTableRow = {
  row_pk: string;
  row_data: Record<string, unknown>;
};

export type DbTable = {
  table_name: string;
  row_count?: number | null;
  schema_name?: string | null;
};

export type DbTableColumn = {
  name: string;
  type: string;
};

// ── API functions ─────────────────────────────────────────────────────────────

const docRagApi = {
  uploadDocument: async (file: File): Promise<DocUploadResponse> => {
    const form = new FormData();
    form.set("file", file);
    const accepted = await aiRequest<DocUploadResponse, FormData>({
      method: "POST",
      basePath: DOC_RAG_BASE,
      path: "/documents/upload",
      body: form,
    });

    if (accepted.status === "indexed") {
      return accepted;
    }

    const deadline = Date.now() + DOC_INGEST_POLL_MAX_MS;
    while (Date.now() < deadline) {
      await sleep(DOC_INGEST_POLL_MS);
      const doc = await aiRequest<DocOut>({
        method: "GET",
        basePath: DOC_RAG_BASE,
        path: `/documents/${encodeURIComponent(accepted.document_id)}`,
      });
      if (doc.status === "indexed") {
        return {
          document_id: doc.id,
          status: doc.status,
          file_name: doc.file_name,
          num_pages: doc.num_pages ?? 0,
          num_chunks: doc.num_chunks ?? 0,
          document_type: doc.document_type,
          processing_time_ms: 0,
        };
      }
      if (doc.status === "error") {
        throw new Error(`Ingestion failed for "${doc.file_name}"`);
      }
    }

    throw new Error(
      `Ingestion timed out for "${accepted.file_name}". Check the document list — processing may still be running.`,
    );
  },

  listDocuments: () =>
    aiRequest<DocOut[]>({ method: "GET", basePath: DOC_RAG_BASE, path: "/documents" }),

  getDocument: (documentId: string) =>
    aiRequest<DocOut>({
      method: "GET",
      basePath: DOC_RAG_BASE,
      path: `/documents/${encodeURIComponent(documentId)}`,
    }),

  deleteDocument: (documentId: string) =>
    aiRequest<Record<string, never>>({
      method: "DELETE",
      basePath: DOC_RAG_BASE,
      path: `/documents/${encodeURIComponent(documentId)}`,
    }),

  getDocumentChunks: (documentId: string, limit = 200) =>
    aiRequest<ChunkPreview[]>({
      method: "GET",
      basePath: DOC_RAG_BASE,
      path: `/documents/${encodeURIComponent(documentId)}/chunks`,
      query: { limit },
    }),

  matchRows: (
    documentId: string,
    params: { confidence_threshold?: number; source_table?: string; group_by_table?: boolean },
  ) =>
    aiRequest<MatchRowsResponse>({
      method: "POST",
      basePath: DOC_RAG_BASE,
      path: `/documents/${encodeURIComponent(documentId)}/match-rows`,
      query: {
        confidence_threshold: params.confidence_threshold,
        source_table: params.source_table ?? undefined,
        group_by_table: params.group_by_table ?? true,
      },
    }),

  suggestTables: (documentId: string, params?: { top_k?: number }) =>
    aiRequest<SuggestTablesResponse>({
      method: "GET",
      basePath: DOC_RAG_BASE,
      path: `/documents/${encodeURIComponent(documentId)}/suggest-tables`,
      query: { top_k: params?.top_k ?? 8 },
    }),

  matchRowsFromFile: (
    documentId: string,
    file: File,
    params?: {
      pk_column?: string | null;
      source_table?: string | null;
      confidence_threshold?: number;
      group_by_table?: boolean;
    },
  ) => {
    const form = new FormData();
    form.set("file", file);
    if (params?.pk_column) form.set("pk_column", params.pk_column);
    if (params?.source_table) form.set("source_table", params.source_table);
    if (params?.confidence_threshold != null) form.set("confidence_threshold", String(params.confidence_threshold));
    if (params?.group_by_table != null) form.set("group_by_table", String(params.group_by_table));
    return aiRequest<MatchRowsResponse, FormData>({
      method: "POST",
      basePath: DOC_RAG_BASE,
      path: `/documents/${encodeURIComponent(documentId)}/match-rows/from-file`,
      body: form,
    });
  },

  ragQuery: (body: { query: string; top_k?: number; session_id?: string }) =>
    aiRequest<RagQueryResponse>({
      method: "POST",
      basePath: DOC_RAG_BASE,
      path: "/rag/query",
      body,
    }),

  ragDebug: (body: { query: string; filters?: Record<string, unknown>; top_k?: number; user_id?: string; session_id?: string }) =>
    aiRequest<RagQueryResponse>({
      method: "POST",
      basePath: DOC_RAG_BASE,
      path: "/rag/debug",
      body,
    }),

  confirmMatches: (documentId: string, rows: ConfirmMatchRow[]) =>
    aiRequest<ConfirmMatchesResponse>({
      method: "POST",
      basePath: DOC_RAG_BASE,
      path: `/documents/${encodeURIComponent(documentId)}/confirm-matches`,
      body: { confirmed_rows: rows },
    }),

  listRowIndexTables: () =>
    aiRequest<RowIndexTable[]>({ method: "GET", basePath: DOC_RAG_BASE, path: "/row-index/tables" }),

  listDbTables: (opts?: { envelope?: boolean }) =>
    aiRequest<DbTable[] | DbTablesEnvelope>({
      method: "GET",
      basePath: DOC_RAG_BASE,
      path: "/row-index/db-tables",
      query: opts?.envelope ? { envelope: true } : undefined,
    }),

  getDbTableColumns: (tableName: string) =>
    aiRequest<DbTableColumn[]>({
      method: "GET",
      basePath: DOC_RAG_BASE,
      path: `/row-index/db-tables/${encodeURIComponent(tableName)}/columns`,
    }),

  uploadRowIndex: (file: File, tableName: string, pkColumn: string) => {
    const form = new FormData();
    form.set("file", file);
    form.set("table_name", tableName);
    form.set("pk_column", pkColumn);
    return aiRequest<RowIndexUploadResponse, FormData>({
      method: "POST",
      basePath: DOC_RAG_BASE,
      path: "/row-index/upload",
      body: form,
    });
  },

  deleteRowIndexTable: (tableName: string) =>
    aiRequest<Record<string, never>>({
      method: "DELETE",
      basePath: DOC_RAG_BASE,
      path: `/row-index/tables/${encodeURIComponent(tableName)}`,
    }),

  listRowIndexTableRows: (tableName: string, params?: { limit?: number; offset?: number }) =>
    aiRequest<RowIndexTableRow[] | { rows: RowIndexTableRow[] }>({
      method: "GET",
      basePath: DOC_RAG_BASE,
      path: `/row-index/tables/${encodeURIComponent(tableName)}/rows`,
      query: {
        limit: params?.limit,
        offset: params?.offset,
      },
    }),

  // form-urlencoded endpoint — use fetch directly
  importDbTable: async (tableName: string, pkColumn: string, rowLimit?: number): Promise<RowIndexUploadResponse> => {
    const url = buildAiUrl("/row-index/import-db-table", undefined, DOC_RAG_BASE);
    const body = new URLSearchParams({ table_name: tableName, pk_column: pkColumn });
    if (rowLimit != null && Number.isFinite(rowLimit)) body.set("row_limit", String(rowLimit));
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(text || `Import failed (${res.status})`);
    }
    return res.json() as Promise<RowIndexUploadResponse>;
  },
};

// ── Hooks ─────────────────────────────────────────────────────────────────────

export function useDocUpload(
  mutationOptions?: UseMutationOptions<DocUploadResponse, unknown, File>,
) {
  return useMutation<DocUploadResponse, unknown, File>({
    mutationFn: (file) => docRagApi.uploadDocument(file),
    ...(mutationOptions ?? {}),
  });
}

export function useDocList(opts?: { enabled?: boolean; refetchInterval?: number | false }) {
  return useQuery<DocOut[], unknown>({
    queryKey: ["doc-rag", "documents"],
    enabled: opts?.enabled ?? true,
    refetchInterval: opts?.refetchInterval,
    queryFn: () => docRagApi.listDocuments(),
  });
}

export function useDocDelete(
  mutationOptions?: UseMutationOptions<Record<string, never>, unknown, string>,
) {
  return useMutation<Record<string, never>, unknown, string>({
    mutationFn: (id) => docRagApi.deleteDocument(id),
    ...(mutationOptions ?? {}),
  });
}

export function useDocChunks(documentId: string, opts?: { enabled?: boolean }) {
  return useQuery<ChunkPreview[], unknown>({
    queryKey: ["doc-rag", "chunks", documentId],
    enabled: (opts?.enabled ?? true) && !!documentId,
    queryFn: () => docRagApi.getDocumentChunks(documentId),
  });
}

export function useDocMatchRows(
  mutationOptions?: UseMutationOptions<
    MatchRowsResponse,
    unknown,
    { documentId: string; confidence_threshold?: number; source_table?: string }
  >,
) {
  return useMutation<
    MatchRowsResponse,
    unknown,
    { documentId: string; confidence_threshold?: number; source_table?: string }
  >({
    mutationFn: ({ documentId, ...params }) => docRagApi.matchRows(documentId, params),
    ...(mutationOptions ?? {}),
  });
}

export function useDocMatchRowsFromFile(
  mutationOptions?: UseMutationOptions<
    MatchRowsResponse,
    unknown,
    {
      documentId: string;
      file: File;
      pk_column?: string | null;
      source_table?: string | null;
      confidence_threshold?: number;
      group_by_table?: boolean;
    }
  >,
) {
  return useMutation<
    MatchRowsResponse,
    unknown,
    {
      documentId: string;
      file: File;
      pk_column?: string | null;
      source_table?: string | null;
      confidence_threshold?: number;
      group_by_table?: boolean;
    }
  >({
    mutationFn: ({ documentId, file, ...params }) => docRagApi.matchRowsFromFile(documentId, file, params),
    ...(mutationOptions ?? {}),
  });
}

export function useRagQuery(
  mutationOptions?: UseMutationOptions<RagQueryResponse, unknown, { query: string; top_k?: number }>,
) {
  return useMutation<RagQueryResponse, unknown, { query: string; top_k?: number }>({
    mutationFn: (body) => docRagApi.ragQuery(body),
    ...(mutationOptions ?? {}),
  });
}

export function useRagDebugQuery(
  mutationOptions?: UseMutationOptions<RagQueryResponse, unknown, { query: string; top_k?: number }>,
) {
  return useMutation<RagQueryResponse, unknown, { query: string; top_k?: number }>({
    mutationFn: (body) => docRagApi.ragDebug(body),
    ...(mutationOptions ?? {}),
  });
}

export function useRowIndexTables(opts?: { enabled?: boolean }) {
  return useQuery<RowIndexTable[], unknown>({
    queryKey: ["doc-rag", "row-index-tables"],
    enabled: opts?.enabled ?? true,
    queryFn: () => docRagApi.listRowIndexTables(),
  });
}

export function useSuggestTables(documentId: string, opts?: { enabled?: boolean; top_k?: number }) {
  return useQuery<SuggestTablesResponse, unknown>({
    queryKey: ["doc-rag", "suggest-tables", documentId, opts?.top_k ?? 8],
    enabled: (opts?.enabled ?? true) && !!documentId,
    queryFn: () => docRagApi.suggestTables(documentId, { top_k: opts?.top_k }),
  });
}

export function useDbTables(opts?: { enabled?: boolean }) {
  return useQuery<{ schemaName: string; tables: DbTable[] }, unknown>({
    queryKey: ["doc-rag", "db-tables"],
    enabled: opts?.enabled ?? true,
    queryFn: async () => {
      const raw = await docRagApi.listDbTables({ envelope: true });
      if (raw && typeof raw === "object" && "tables" in raw && Array.isArray((raw as DbTablesEnvelope).tables)) {
        const env = raw as DbTablesEnvelope;
        return {
          schemaName: env.schema_name || PLENUM_CMMS_SCHEMA,
          tables: env.tables,
        };
      }
      const list = Array.isArray(raw) ? raw : [];
      return { schemaName: PLENUM_CMMS_SCHEMA, tables: list };
    },
  });
}

export function useDbTableColumns(tableName: string, opts?: { enabled?: boolean }) {
  return useQuery<DbTableColumn[], unknown>({
    queryKey: ["doc-rag", "db-columns", tableName],
    enabled: (opts?.enabled ?? true) && !!tableName,
    queryFn: () => docRagApi.getDbTableColumns(tableName),
  });
}

export function useImportDbTable(
  mutationOptions?: UseMutationOptions<
    RowIndexUploadResponse,
    unknown,
    { tableName: string; pkColumn: string; rowLimit?: number }
  >,
) {
  return useMutation<RowIndexUploadResponse, unknown, { tableName: string; pkColumn: string; rowLimit?: number }>({
    mutationFn: ({ tableName, pkColumn, rowLimit }) => docRagApi.importDbTable(tableName, pkColumn, rowLimit),
    ...(mutationOptions ?? {}),
  });
}

export function useUploadRowIndex(
  mutationOptions?: UseMutationOptions<RowIndexUploadResponse, unknown, { file: File; tableName: string; pkColumn: string }>,
) {
  return useMutation<RowIndexUploadResponse, unknown, { file: File; tableName: string; pkColumn: string }>({
    mutationFn: ({ file, tableName, pkColumn }) => docRagApi.uploadRowIndex(file, tableName, pkColumn),
    ...(mutationOptions ?? {}),
  });
}

export function useDeleteRowIndexTable(
  mutationOptions?: UseMutationOptions<Record<string, never>, unknown, { tableName: string }>,
) {
  return useMutation<Record<string, never>, unknown, { tableName: string }>({
    mutationFn: ({ tableName }) => docRagApi.deleteRowIndexTable(tableName),
    ...(mutationOptions ?? {}),
  });
}

export function useRowIndexTableRows(
  mutationOptions?: UseMutationOptions<
    RowIndexTableRow[] | { rows: RowIndexTableRow[] },
    unknown,
    { tableName: string; limit?: number; offset?: number }
  >,
) {
  return useMutation<
    RowIndexTableRow[] | { rows: RowIndexTableRow[] },
    unknown,
    { tableName: string; limit?: number; offset?: number }
  >({
    mutationFn: ({ tableName, limit, offset }) => docRagApi.listRowIndexTableRows(tableName, { limit, offset }),
    ...(mutationOptions ?? {}),
  });
}

export function useConfirmMatches(
  mutationOptions?: UseMutationOptions<
    ConfirmMatchesResponse,
    unknown,
    { documentId: string; rows: ConfirmMatchRow[] }
  >,
) {
  return useMutation<ConfirmMatchesResponse, unknown, { documentId: string; rows: ConfirmMatchRow[] }>({
    mutationFn: ({ documentId, rows }) => docRagApi.confirmMatches(documentId, rows),
    ...(mutationOptions ?? {}),
  });
}
