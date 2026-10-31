"use client";
import { CheckCircle, Download, RotateCcw, BarChart3, Database, ShieldCheck } from "lucide-react";
import type { MigrationStatusResponse } from "../../chat-api";
import { migrationFullExportUrl } from "../../chat-api";
import UdrTestsPanel from "./udr-tests-panel";
import MigrationAnalysis from "./migration-analysis";

interface Props {
  migration: MigrationStatusResponse;
  onReset?: () => void;
  /** Refetch the migration status after a quality-test remediation action. */
  onRefresh?: () => void;
  /** Hide the "New migration" action — used when shown read-only inside an archived run. */
  hideReset?: boolean;
}

export default function ResultsPanel({ migration, onReset, onRefresh, hideReset = false }: Props) {
  const totalMapped =
    migration.t1_mapped_count + migration.t2_auto_count + migration.t2_human_count;
  // Coverage can exceed 100% when the user adds new columns (T1_new_table /
  // T1_manual) that weren't in the source's `total_fields` denominator —
  // 29 mapped / 25 source fields = 116%. Clamp so the bar stays sane and
  // never reads as if the system is operating above the source's field count.
  const coveragePct =
    migration.total_fields > 0
      ? Math.min(100, Math.round((totalMapped / migration.total_fields) * 100))
      : 0;

  const downloads: { label: string; url: string | null; ext: string }[] = [
    { label: "JSON mapping",   url: migration.output_json_url,       ext: "json" },
    { label: "CSV report",     url: migration.output_csv_url,        ext: "csv" },
    { label: "SQL DDL",        url: migration.output_sql_url,        ext: "sql" },
    { label: "Migration PDF",  url: migration.migration_report_url,  ext: "pdf" },
    { label: "DB structure",   url: migration.output_structure_md_url ?? null, ext: "md" },
  ];

  return (
    <div className="max-w-2xl">
      {/* Success header */}
      <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-8 flex flex-col items-center text-center gap-4 mb-6">
        <div className="w-14 h-14 rounded-2xl bg-green-50 flex items-center justify-center">
          <CheckCircle size={32} className="text-green-500" />
        </div>
        <div>
          <h2 className="text-xl font-bold text-slate-900">Migration complete</h2>
          <p className="text-sm text-slate-500 mt-1">
            {migration.cmms_name} data has been mapped and ingested into Hoistra.
          </p>
        </div>
      </div>

      {/* Migration analysis — the transparent B7.1→B17.1 step-by-step view of how the
          engine reached its decisions, shown ahead of the bare mapping summary. */}
      <MigrationAnalysis
        report={migration.udr_table_resolution}
        columnIntelligence={migration.udr_column_intelligence}
        relationship={migration.udr_relationship_report}
        tests={migration.udr_test_results}
        summary={{
          t1Mapped: migration.t1_mapped_count,
          t2Auto: migration.t2_auto_count,
          t2Human: migration.t2_human_count,
          unmapped: migration.unmapped_count,
          totalFields: migration.total_fields,
          coveragePct,
        }}
      />

      {/* Stats */}
      <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-5 mb-5">
        <div className="flex items-center gap-2 mb-4">
          <BarChart3 size={16} className="text-indigo-600" />
          <h3 className="text-sm font-semibold text-slate-700">Mapping summary</h3>
        </div>
        <div className="grid grid-cols-2 gap-3 mb-4">
          {[
            { label: "T1 auto-mapped",   value: migration.t1_mapped_count,  color: "text-green-600" },
            { label: "T2 auto-mapped",   value: migration.t2_auto_count,    color: "text-indigo-600" },
            { label: "Human reviewed",   value: migration.t2_human_count,   color: "text-purple-600" },
            { label: "Unmapped",         value: migration.unmapped_count,   color: "text-slate-500" },
          ].map(({ label, value, color }) => (
            <div key={label} className="flex justify-between items-center py-1.5 border-b border-slate-100 last:border-0">
              <span className="text-sm text-slate-600">{label}</span>
              <span className={`font-mono font-bold ${color}`}>{value}</span>
            </div>
          ))}
        </div>

        {/* Coverage bar */}
        <div>
          <div className="flex justify-between text-sm mb-1.5">
            <span className="text-slate-600">Coverage</span>
            <span className="font-mono font-bold text-indigo-600">{coveragePct}%</span>
          </div>
          <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${
                coveragePct >= 80 ? "bg-green-500" : coveragePct >= 60 ? "bg-amber-500" : "bg-red-500"
              }`}
              style={{ width: `${coveragePct}%` }}
            />
          </div>
        </div>
      </div>

      {/* Download links */}
      {downloads.some((d) => d.url) && (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-5 mb-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3 flex items-center gap-2">
            <Download size={14} className="text-indigo-600" />
            Downloads
          </h3>
          <div className="grid grid-cols-2 gap-2">
            {downloads.filter((d) => d.url).map(({ label, url, ext }) => (
              <a
                key={ext}
                href={url!}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-slate-200 text-sm text-slate-700 hover:bg-slate-50 transition-colors"
              >
                <Download size={14} className="text-indigo-500" />
                {label}
              </a>
            ))}
          </div>
        </div>
      )}

      {/* F7 7.9/7.10 — quality tests (chunk→PK, overlap→FK) + remediation. Renders a
          neutral "runs during the UDR pass" state until the run produces results. */}
      {migration.udr_test_results ? (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-5 mb-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3 flex items-center gap-2">
            <ShieldCheck size={14} className="text-indigo-600" />
            Quality tests &amp; relationship integrity
          </h3>
          <UdrTestsPanel
            migrationId={migration.migration_id}
            results={migration.udr_test_results}
            onChanged={onRefresh}
            readOnly={hideReset}
          />
        </div>
      ) : null}

      {/* F7 7.12/7.13 — inferred-relationship review queue + sanctity flags. */}
      {migration.udr_relationship_report ? (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-5 mb-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-1 flex items-center gap-2">
            <ShieldCheck size={14} className="text-violet-600" />
            Relationship quality
          </h3>
          <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
            <span>schema relationships <span className="font-mono">{migration.udr_relationship_report.schema_relationship_count ?? "—"}</span></span>
            <span className="text-violet-700">inferred (review) <span className="font-mono">{migration.udr_relationship_report.inferred_count ?? 0}</span></span>
            <span className="text-amber-700">sanctity flags <span className="font-mono">{migration.udr_relationship_report.sanctity_flag_count ?? 0}</span></span>
          </div>

          {(migration.udr_relationship_report.inferred ?? []).length ? (
            <div className="mb-3">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-violet-600 mb-1">
                AI-suggested relationships — review to promote
              </p>
              <div className="space-y-1">
                {(migration.udr_relationship_report.inferred ?? []).slice(0, 25).map((r, i) => (
                  <div key={i} className="flex flex-wrap items-center gap-1.5 rounded-lg border border-violet-200 bg-violet-50/50 px-2 py-1 text-[11px]">
                    <span className="font-mono text-slate-700">{r.src_entity}{r.src_column ? `.${r.src_column}` : ""}</span>
                    <span className="text-slate-400">→</span>
                    <span className="font-mono text-slate-700">{r.dst_entity}{r.dst_column ? `.${r.dst_column}` : ""}</span>
                    {typeof r.confidence === "number" ? (
                      <span className="ml-auto font-mono text-slate-500">{Math.round(r.confidence * 100)}%</span>
                    ) : null}
                    {r.reason || r.evidence ? (
                      <span className="w-full text-[10px] text-slate-500">{r.reason ?? r.evidence}</span>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {(migration.udr_relationship_report.sanctity_flags ?? []).length ? (
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-600 mb-1">
                Sanctity flags — likely misallocations
              </p>
              <div className="space-y-1">
                {(migration.udr_relationship_report.sanctity_flags ?? []).slice(0, 25).map((f, i) => (
                  <div key={i} className="rounded-lg border border-amber-200 bg-amber-50/60 px-2 py-1 text-[11px]">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-slate-700">{f.wo_id ?? f.entity_id ?? "?"}</span>
                      <span className="text-slate-400">↔</span>
                      <span className="font-mono text-slate-700">{f.asset_id ?? "?"}</span>
                      {typeof f.confidence === "number" ? (
                        <span className="ml-auto font-mono text-red-600">{Math.round(f.confidence * 100)}%</span>
                      ) : null}
                    </div>
                    {f.reason ? <p className="text-[10px] text-slate-600">{f.reason}</p> : null}
                    {f.suggested ? <p className="text-[10px] text-emerald-700">suggested: {f.suggested}</p> : null}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p className="text-xs text-emerald-700 flex items-center gap-1">
              <CheckCircle size={12} /> No relationship sanctity issues flagged.
            </p>
          )}
        </div>
      ) : null}

      {/* Full target-table export — existing rows + the rows this migration added */}
      <div className="rounded-xl border border-emerald-200 bg-emerald-50/40 shadow-sm p-5 mb-5">
        <h3 className="text-sm font-semibold text-slate-700 mb-1 flex items-center gap-2">
          <Database size={14} className="text-emerald-600" />
          Full table data (old + new)
        </h3>
        <p className="text-xs text-slate-500 mb-3">
          Each target table in full — your existing rows plus the rows this migration added.
        </p>
        <div className="grid grid-cols-3 gap-2">
          {(["csv", "json", "sql"] as const).map((fmt) => (
            <a
              key={fmt}
              href={migrationFullExportUrl(migration.migration_id, fmt)}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-emerald-200 bg-white text-sm text-slate-700 hover:bg-emerald-50 transition-colors"
            >
              <Download size={14} className="text-emerald-500" />
              {fmt === "csv" ? "CSV (zip)" : fmt.toUpperCase()}
            </a>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-3 text-xs text-slate-400 mb-5">
        <span>ID: <code className="font-mono">{migration.migration_id.slice(0, 8)}…</code></span>
        {migration.completed_at && (
          <span>Completed: {new Date(migration.completed_at).toLocaleString()}</span>
        )}
      </div>

      {!hideReset ? (
        <button
          onClick={onReset}
          className="inline-flex items-center gap-2 px-4 py-2 bg-white border border-slate-200 text-slate-700 text-sm font-medium rounded-lg hover:bg-slate-50 transition-colors"
        >
          <RotateCcw size={14} />
          New migration
        </button>
      ) : null}
    </div>
  );
}
