"use client";

import { useState } from "react";
import { CheckCircle2, ChevronRight, Layers } from "lucide-react";

import { cn } from "@/utils/cn";
import type {
  UdrColumnIntelligence,
  UdrRelationshipReport,
  UdrTableResolution,
  UdrTestResults,
} from "../../chat-api";
import { ColumnIntelligencePanels } from "./migration-metadata-view";
import { plainAliasSource, plainMethodLabel, plainPkList } from "./migration-mapping-utils";

/**
 * Migration Analysis — the transparent, step-by-step view of how the engine reached its
 * table/column/relationship decisions (B7.1 → B17.1), shown instead of jumping straight to
 * the bare "File Ingestion → Tables → Mapping" summary. B7.1–B12.1 are backed by the
 * structured `udr_table_resolution` report; B13.1–B17.1 render from the migration's existing
 * mapping / relationship / test data. Each step is independently expandable and persists
 * with the migration (the data lives on the migration status row + UDR node log).
 */

export type MigrationAnalysisSummary = {
  t1Mapped: number;
  t2Auto: number;
  t2Human: number;
  unmapped: number;
  totalFields: number;
  coveragePct: number;
};

type Tone = "thought" | "action";

const COT = "bg-violet-50 text-violet-700 ring-violet-200";
const COA = "bg-blue-50 text-blue-700 ring-blue-200";

function pct(x: number | null | undefined): string {
  return typeof x === "number" ? `${Math.round(x * 100)}%` : "—";
}
function two(x: number | null | undefined): string {
  return typeof x === "number" ? x.toFixed(2) : "—";
}
function confTone(x: number | null | undefined): string {
  if (typeof x !== "number") return "text-slate-500";
  return x >= 0.9 ? "text-emerald-700" : x >= 0.7 ? "text-amber-700" : "text-red-700";
}
function methodChip(method: string): string {
  const m = method.toLowerCase();
  if (/(exact|rag|alias|levenshtein)/.test(m)) return "bg-emerald-100 text-emerald-700";
  if (/(semantic|suggest)/.test(m)) return "bg-amber-100 text-amber-700";
  if (/(none|unresolved|review)/.test(m)) return "bg-slate-100 text-slate-500";
  return "bg-slate-100 text-slate-600";
}

function Mini({ columns, rows }: { columns: string[]; rows: React.ReactNode[][] }) {
  if (!rows.length) {
    return <p className="px-1 py-2 text-[11px] text-slate-400">No rows for this step.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[11px]">
        <thead>
          <tr>
            {columns.map((c, i) => (
              <th
                key={i}
                className="border border-slate-200 bg-[#F1EFE8] px-2 py-1 text-left font-medium text-slate-600"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri} className="even:bg-slate-50/40">
              {r.map((cell, ci) => (
                <td key={ci} className="border border-slate-200 px-2 py-1 align-top text-slate-700">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Step({
  code,
  title,
  tone,
  done,
  defaultOpen = false,
  children,
}: {
  code: string;
  title: string;
  tone: Tone;
  done: boolean;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-slate-50/60"
      >
        <ChevronRight
          size={14}
          className={cn("shrink-0 text-slate-400 transition-transform", open && "rotate-90")}
        />
        {done ? (
          <CheckCircle2 size={14} className="shrink-0 text-emerald-500" />
        ) : (
          <span className="h-3.5 w-3.5 shrink-0 rounded-full border border-slate-300" />
        )}
        <span className="font-mono text-[10px] text-slate-400">{code}</span>
        <span className="text-[12.5px] font-medium text-slate-800">{title}</span>
        <span
          className={cn(
            "ml-auto shrink-0 rounded-full px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide ring-[0.5px]",
            tone === "thought" ? COT : COA,
          )}
        >
          {tone === "thought" ? "Analysis" : "Action"}
        </span>
      </button>
      {open ? <div className="border-t border-slate-100 px-3 py-3">{children}</div> : null}
    </div>
  );
}

function CardGrid({ cards }: { cards: NonNullable<UdrTableResolution["metadata_cards"]> }) {
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {cards.map((c) => (
        <div key={c.table} className="rounded-lg border border-slate-200 bg-slate-50/40 p-2.5">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate font-mono text-[12px] font-semibold text-slate-800">{c.table}</span>
            <span className="shrink-0 text-[10px] text-slate-400">cols {c.column_count}</span>
          </div>
          <div className="mt-0.5 text-[10px] text-slate-500">
            PK <span className="font-mono text-slate-700">{plainPkList(c.primary_key) || "—"}</span>
            {c.primary_key_kind ? <span className="ml-1 text-slate-400">· {c.primary_key_kind}</span> : null}
          </div>
          <div className="mt-1.5 space-y-0.5">
            {(c.samples ?? []).slice(0, 3).map((s) => (
              <div key={s.column} className="truncate text-[10px]">
                <span className="font-mono text-slate-600">{s.column}</span>{" "}
                <span className="text-slate-400">{(s.values ?? []).slice(0, 3).join(", ")}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function MigrationAnalysis({
  report,
  columnIntelligence,
  relationship,
  tests,
  summary,
}: {
  report?: UdrTableResolution | null;
  columnIntelligence?: UdrColumnIntelligence | null;
  relationship?: UdrRelationshipReport | null;
  tests?: UdrTestResults | null;
  summary?: MigrationAnalysisSummary | null;
}) {
  const r = report ?? {};
  const cards = r.metadata_cards ?? [];
  const pk = r.pk_detection ?? [];
  const det = r.deterministic ?? [];
  const rag = r.rag_alias ?? [];
  const sem = r.semantic ?? [];
  const final = r.final_decisions ?? [];
  const hasReport = cards.length > 0 || final.length > 0;

  // B16 — validation derived from the quality tests + blocking state.
  const t1 = tests?.test_1;
  const t2 = tests?.test_2;
  const t1Pass = t1 ? !t1.blocked : null;
  const t2Pass = t2 ? !t2.blocked : null;

  // B17 — final UDR structure = the distinct destination tables.
  const finalTables = Array.from(
    new Set(final.map((f) => f.destination).filter((d): d is string => !!d)),
  );

  // B14 / B15 — schema + inferred relationships from the relationship report.
  const inferred = relationship?.inferred ?? [];

  const hasColumnIntel = !!(columnIntelligence && (columnIntelligence.groups?.length || columnIntelligence.metadata?.length));

  if (!hasReport && !tests && !relationship && !hasColumnIntel) {
    return null;
  }

  const Validation = ({ label, ok }: { label: string; ok: boolean | null }) => (
    <div className="flex items-center justify-between rounded-md border border-slate-200 px-2.5 py-1.5 text-[11px]">
      <span className="text-slate-600">{label}</span>
      {ok == null ? (
        <span className="text-slate-400">runs during the mapping pass</span>
      ) : ok ? (
        <span className="inline-flex items-center gap-1 font-medium text-emerald-700">
          <CheckCircle2 size={12} /> Passed
        </span>
      ) : (
        <span className="font-medium text-red-700">Review required</span>
      )}
    </div>
  );

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-5 mb-5">
      <div className="mb-3 flex items-center gap-2">
        <Layers size={16} className="text-indigo-600" />
        <h3 className="text-sm font-semibold text-slate-700">Migration analysis</h3>
        <span className="ml-auto text-[11px] text-slate-400">
          how the engine reached every decision · {report?.counts?.tables ?? cards.length} source tables
        </span>
      </div>

      <div className="space-y-2">
        {/* B7.1 — Unique table identification */}
        <Step code="B7.1" title="Unique table identification" tone="thought" done={hasReport} defaultOpen>
          <p className="mb-2 text-[11px] leading-relaxed text-slate-500">
            Per-table metadata — primary key · column names · 3 samples · column count (no FKs yet),
            then a pairwise compare on name + metadata.
          </p>
          {cards.length ? <CardGrid cards={cards} /> : <p className="text-[11px] text-slate-400">No metadata cards.</p>}
          {r.pairwise?.verdict ? (
            <p className="mt-2 rounded-md bg-slate-50 px-2.5 py-1.5 text-[11px] text-slate-600">
              {r.pairwise.verdict}
            </p>
          ) : null}
        </Step>

        {/* B8.1 — Primary key detection */}
        <Step code="B8.1" title="Primary-key detection" tone="thought" done={pk.length > 0}>
          <p className="mb-2 text-[11px] leading-relaxed text-slate-500">
            Single-column null + unique tests → natural PK; else smallest jointly-unique composite;
            else surrogate.
          </p>
          <Mini
            columns={["Table", "Kind", "PK", "Uniqueness", "Null-rate", "Tie-break"]}
            rows={pk.map((p) => [
              <span className="font-mono">{p.table}</span>,
              <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">{p.kind}</span>,
              <span className="font-mono">{plainPkList(p.primary_key)}</span>,
              two(p.uniqueness),
              two(p.null_rate),
              <span className="text-slate-500">{p.tie_break}</span>,
            ])}
          />
        </Step>

        {/* B9.1 — Deterministic table mapping */}
        <Step code="B9.1" title="Deterministic table mapping" tone="action" done={det.length > 0}>
          <p className="mb-2 text-[11px] leading-relaxed text-slate-500">
            Exact match (case/space/plural tolerant), then Levenshtein ≤ 2. Auto-resolve ≥ 0.95.
          </p>
          <Mini
            columns={["Source", "Method", "Destination", "Conf.", "Notes"]}
            rows={det.map((d) => [
              <span className="font-mono">{d.source}</span>,
              <span className={cn("rounded-full px-1.5 py-0.5 text-[10px]", methodChip(d.method))}>{plainMethodLabel(d.method)}</span>,
              d.destination ? <span className="font-mono text-indigo-700">{d.destination}</span> : <span className="text-slate-400">—</span>,
              <span className={confTone(d.confidence)}>{two(d.confidence)}</span>,
              <span className="text-slate-500">{d.note}</span>,
            ])}
          />
        </Step>

        {/* B10.1 — RAG / alias mapping */}
        <Step code="B10.1" title="Known-term matching" tone="action" done={rag.length > 0}>
          <p className="mb-2 text-[11px] leading-relaxed text-slate-500">
            Facilities-management vocabulary + terms learned from earlier imports: native CMMS/CAFM terms → the Hoistra data model. Auto-resolve ≥ 0.95.
          </p>
          <Mini
            columns={["Source", "Alias hit", "Destination", "Conf.", "Source of alias"]}
            rows={rag.map((d) => [
              <span className="font-mono">{d.source}</span>,
              d.alias_hit ? (
                <span className="rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] text-emerald-700">{d.alias_hit}</span>
              ) : (
                <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-400">none</span>
              ),
              d.destination ? <span className="font-mono text-indigo-700">{d.destination}</span> : <span className="text-slate-400">—</span>,
              <span className={confTone(d.confidence)}>{two(d.confidence)}</span>,
              <span className="text-slate-500">{plainAliasSource(d.alias_source)}</span>,
            ])}
          />
        </Step>

        {/* B11.1 — Semantic table mapping */}
        <Step code="B11.1" title="Semantic table mapping" tone="action" done={sem.length > 0 || final.length > 0}>
          <p className="mb-2 text-[11px] leading-relaxed text-slate-500">
            AI matcher with facilities-management knowledge — name + metadata (column names) as the signal; returns
            target + 0–100% confidence. ≥ 70% is suggested for one-click confirm or override.
          </p>
          <Mini
            columns={["Source", "Signal (columns)", "Destination", "Conf.", "Band"]}
            rows={sem.map((d) => [
              <span className="font-mono">{d.source}</span>,
              <span className="text-slate-500">{d.signal}</span>,
              d.destination ? <span className="font-mono text-indigo-700">{d.destination}</span> : <span className="text-slate-400">—</span>,
              <span className={confTone(d.confidence)}>{pct(d.confidence)}</span>,
              <span
                className={cn(
                  "rounded-full px-1.5 py-0.5 text-[10px]",
                  d.band === "suggested" ? "bg-amber-100 text-amber-700" : "bg-slate-100 text-slate-500",
                )}
              >
                {d.band === "suggested" ? "suggested — confirm" : "review required"}
              </span>,
            ])}
          />
        </Step>

        {/* B12.1 — Final mapping decisions */}
        <Step code="B12.1" title="Final table decisions" tone="action" done={final.length > 0}>
          <Mini
            columns={["Source", "Destination", "Method", "Confidence"]}
            rows={final.map((d) => [
              <span className="font-mono">{d.source}</span>,
              d.destination ? <span className="font-mono text-indigo-700">{d.destination}</span> : <span className="text-slate-400">—</span>,
              <span className={cn("rounded-full px-1.5 py-0.5 text-[10px]", methodChip(d.method))}>{plainMethodLabel(d.method)}</span>,
              <span className={confTone(d.confidence)}>{two(d.confidence)}</span>,
            ])}
          />
        </Step>

        {/* B13.1 → B21.1 — the full Column-Intelligence Pipeline (prefixing · metadata · FK
            candidates · format + value-pattern grouping · unified canonical names · classification ·
            destination column mapping). Same shared panels the pre-semantic gate renders, so the
            post-write view shows the identical column flow. */}
        {hasColumnIntel ? <ColumnIntelligencePanels report={columnIntelligence} /> : null}

        {/* Column mapping coverage summary (high-level counts; per-column detail is above) */}
        <Step code="B13.1" title="Column mapping coverage" tone="action" done={!!summary}>
          {summary ? (
            <>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[
                  ["T1 auto", summary.t1Mapped, "text-emerald-700"],
                  ["T2 auto", summary.t2Auto, "text-indigo-700"],
                  ["Human reviewed", summary.t2Human, "text-violet-700"],
                  ["Unmapped", summary.unmapped, "text-slate-500"],
                ].map(([label, value, color]) => (
                  <div key={String(label)} className="rounded-lg border border-slate-200 px-2.5 py-2 text-center">
                    <div className={cn("font-mono text-base font-bold", color as string)}>{value as number}</div>
                    <div className="text-[10px] text-slate-500">{label as string}</div>
                  </div>
                ))}
              </div>
              <div className="mt-2">
                <div className="mb-1 flex justify-between text-[11px]">
                  <span className="text-slate-600">Coverage</span>
                  <span className="font-mono font-semibold text-indigo-600">{summary.coveragePct}%</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className={cn(
                      "h-full rounded-full",
                      summary.coveragePct >= 80 ? "bg-emerald-500" : summary.coveragePct >= 60 ? "bg-amber-500" : "bg-red-500",
                    )}
                    style={{ width: `${summary.coveragePct}%` }}
                  />
                </div>
              </div>
              <p className="mt-2 text-[10px] text-slate-400">
                Per-column source → destination detail (exact / semantic / numeric / vocabulary scores) is
                recorded in the run's mapping decisions and the Activity Log.
              </p>
            </>
          ) : (
            <p className="text-[11px] text-slate-400">Column mapping runs during the mapping pass.</p>
          )}
        </Step>

        {/* B14.1 — Foreign-key detection */}
        <Step code="B14.1" title="Foreign-key detection" tone="action" done={!!relationship}>
          {relationship ? (
            <>
              <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-600">
                <span>schema FK relationships <span className="font-mono">{relationship.schema_relationship_count ?? "—"}</span></span>
                <span className="text-violet-700">inferred (review) <span className="font-mono">{relationship.inferred_count ?? 0}</span></span>
              </div>
              {inferred.length ? (
                <div className="space-y-1">
                  {inferred.slice(0, 12).map((e, i) => (
                    <div key={i} className="flex flex-wrap items-center gap-1.5 rounded-md border border-violet-200 bg-violet-50/40 px-2 py-1 text-[11px]">
                      <span className="font-mono text-slate-700">{e.src_entity}{e.src_column ? `.${e.src_column}` : ""}</span>
                      <span className="text-slate-400">→</span>
                      <span className="font-mono text-slate-700">{e.dst_entity}{e.dst_column ? `.${e.dst_column}` : ""}</span>
                      {typeof e.confidence === "number" ? (
                        <span className="ml-auto font-mono text-slate-500">{Math.round(e.confidence * 100)}%</span>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-[11px] text-emerald-700">All foreign keys resolved from schema metadata.</p>
              )}
            </>
          ) : (
            <p className="text-[11px] text-slate-400">Foreign-key detection runs during the mapping pass.</p>
          )}
        </Step>

        {/* B15.1 — Hierarchy generation (relationship view) */}
        <Step code="B15.1" title="Hierarchy generation" tone="action" done={!!relationship}>
          {inferred.length || (relationship?.schema_relationship_count ?? 0) > 0 ? (
            <>
              <p className="mb-2 text-[11px] text-slate-500">Relationship view — parent ← child edges across the resolved tables.</p>
              <div className="space-y-1">
                {inferred.slice(0, 12).map((e, i) => (
                  <div key={i} className="flex items-center gap-1.5 text-[11px]">
                    <span className="font-mono text-slate-700">{e.dst_entity}</span>
                    <span className="text-slate-400">└──</span>
                    <span className="font-mono text-slate-600">{e.src_entity}</span>
                  </div>
                ))}
                {!inferred.length ? (
                  <p className="text-[11px] text-slate-400">
                    {relationship?.schema_relationship_count} schema relationships form the hierarchy (tree view in the relationship graph).
                  </p>
                ) : null}
              </div>
            </>
          ) : (
            <p className="text-[11px] text-slate-400">Hierarchy generation runs during the mapping pass.</p>
          )}
        </Step>

        {/* B16.1 — Validation */}
        <Step code="B16.1" title="Validation" tone="action" done={!!tests}>
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            <Validation label="Test 1 — chunk → primary key" ok={t1Pass} />
            <Validation label="Test 2 — overlap → foreign key" ok={t2Pass} />
            <Validation label="FK validation" ok={tests ? t1Pass !== false && t2Pass !== false : null} />
            <Validation label="Hierarchy validation" ok={relationship ? true : null} />
          </div>
        </Step>

        {/* B17.1 — Final UDR structure */}
        <Step code="B17.1" title="Final data structure" tone="thought" done={finalTables.length > 0}>
          {finalTables.length ? (
            <div className="flex flex-wrap gap-1.5">
              {finalTables.map((t) => (
                <span key={t} className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 font-mono text-[11px] text-emerald-700">
                  {t}
                </span>
              ))}
            </div>
          ) : (
            <p className="text-[11px] text-slate-400">The final table set is assembled after all prior steps.</p>
          )}
        </Step>
      </div>
    </div>
  );
}
