"use client";

/**
 * F7 7.9 / 7.10 — UDR quality tests results + remediation.
 *
 * Renders the Test 1 (every vector-chunk association references a primary key)
 * and Test 2 (every cross-table value overlap is explained by a foreign key)
 * reports produced during the UDR pass, with the spec's remediation actions:
 *   - Test 1 → a single "Fix and Retest" action (7.9-AC6) that promotes shared
 *     attributes to reference-table primary keys and re-runs Test 1.
 *   - Test 2 → a 3-option resolution per flagged pair (7.10-AC6): define FK,
 *     create reference table, or mark the overlap coincidental — applied + re-run.
 *
 * Reads results from the migration status (`udr_test_results`); shows a clean
 * "runs during the UDR pass" state until the backend produces them.
 */
import { useState } from "react";
import { AlertTriangle, CheckCircle2, RefreshCw, XCircle } from "lucide-react";

import {
  schemaMapperApi,
  type UdrTest2ResolutionAction,
  type UdrTest2Resolution,
  type UdrTestResults,
} from "../../chat-api";

const RESOLUTION_OPTIONS: { value: UdrTest2ResolutionAction; label: string }[] = [
  { value: "define_fk", label: "Define as FK" },
  { value: "create_reference_table", label: "Create reference table" },
  { value: "mark_coincidental", label: "Mark coincidental" },
];

function Pct({ v }: { v?: number }) {
  return <span className="font-mono">{typeof v === "number" ? `${Math.round(v * 100)}%` : "—"}</span>;
}

export default function UdrTestsPanel({
  migrationId,
  results,
  onChanged,
  readOnly = false,
}: {
  migrationId: string;
  results?: UdrTestResults | null;
  /** Called after a remediation action so the caller can refetch the migration status. */
  onChanged?: () => void;
  readOnly?: boolean;
}) {
  const t1 = results?.test_1;
  const t2 = results?.test_2;
  const [busy, setBusy] = useState<null | "t1" | "t2">(null);
  const [error, setError] = useState<string | null>(null);
  // Per-flagged-pair chosen resolution action, keyed by pair index.
  const [choices, setChoices] = useState<Record<number, UdrTest2ResolutionAction>>({});

  const hasResults = !!(t1 || t2);
  const flagged = (t2?.flagged ?? []).filter((p) => p.explained === false || p.explained === undefined);

  async function runFixRetest() {
    setBusy("t1");
    setError(null);
    try {
      await schemaMapperApi.test1FixAndRetest(migrationId);
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fix and Retest failed");
    } finally {
      setBusy(null);
    }
  }

  async function applyResolutions() {
    const resolutions: UdrTest2Resolution[] = flagged
      .map((p, i) => ({ p, action: choices[i] }))
      .filter((x): x is { p: typeof flagged[number]; action: UdrTest2ResolutionAction } => !!x.action)
      .map(({ p, action }) => ({
        table_a: p.table_a ?? "",
        column_a: p.column_a ?? "",
        table_b: p.table_b ?? "",
        column_b: p.column_b ?? "",
        action,
      }))
      .filter((r) => r.table_a && r.column_a && r.table_b && r.column_b);
    if (!resolutions.length) {
      setError("Choose a resolution for at least one flagged pair.");
      return;
    }
    setBusy("t2");
    setError(null);
    try {
      await schemaMapperApi.test2Resolve(migrationId, { resolutions });
      setChoices({});
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Resolution failed");
    } finally {
      setBusy(null);
    }
  }

  if (!hasResults) {
    return (
      <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50/70 p-4">
        <p className="text-sm font-semibold text-slate-700">Quality tests</p>
        <p className="mt-1 text-xs text-slate-500">
          Test 1 (chunk → primary key) and Test 2 (overlap → foreign key) run automatically during
          the mapping pass. Results and remediation actions appear here once the run reaches them.
        </p>
      </div>
    );
  }

  function statusPill(report: { skipped?: boolean; blocked?: boolean } | undefined) {
    if (!report) return null;
    if (report.skipped)
      return <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500">skipped</span>;
    if (report.blocked)
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-[10px] font-medium text-red-700">
          <XCircle size={10} /> blocks the import
        </span>
      );
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
        <CheckCircle2 size={10} /> passed
      </span>
    );
  }

  return (
    <div className="space-y-3">
      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>
      ) : null}

      {/* Test 1 */}
      {t1 ? (
        <div className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-semibold text-slate-800">Test 1 — chunk → primary key</p>
            {statusPill(t1)}
          </div>
          {!t1.skipped ? (
            <>
              <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
                <span>total <span className="font-mono">{t1.total ?? "—"}</span></span>
                <span className="text-emerald-700">passing <span className="font-mono">{t1.passing ?? "—"}</span></span>
                <span className="text-red-600">failing <span className="font-mono">{t1.failing ?? 0}</span></span>
                <span>fail rate <Pct v={t1.fail_rate} /></span>
              </div>
              {(t1.failures ?? []).length ? (
                <div className="mt-2 max-h-40 overflow-y-auto rounded-lg border border-slate-100">
                  <table className="w-full text-[11px]">
                    <thead className="bg-slate-50 text-slate-500">
                      <tr><th className="px-2 py-1 text-left">chunk</th><th className="px-2 py-1 text-left">value</th><th className="px-2 py-1 text-left">found in</th></tr>
                    </thead>
                    <tbody>
                      {(t1.failures ?? []).slice(0, 50).map((f, i) => (
                        <tr key={i} className="border-t border-slate-100">
                          <td className="px-2 py-1 font-mono text-slate-700">{f.chunk_id ?? "—"}</td>
                          <td className="px-2 py-1 font-mono text-slate-700">{f.association_value ?? "—"}</td>
                          <td className="px-2 py-1 font-mono text-slate-500">
                            {f.found_in_table ?? "—"}{f.found_in_column ? `.${f.found_in_column}` : ""}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
              {!readOnly && (t1.failing ?? 0) > 0 ? (
                <button
                  onClick={runFixRetest}
                  disabled={busy === "t1"}
                  className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-60"
                >
                  <RefreshCw size={12} className={busy === "t1" ? "animate-spin" : ""} />
                  Fix and Retest
                </button>
              ) : null}
            </>
          ) : (
            <p className="mt-1 text-xs text-slate-400">{t1.reason ?? "No document chunks in this run."}</p>
          )}
        </div>
      ) : null}

      {/* Test 2 */}
      {t2 ? (
        <div className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-semibold text-slate-800">Test 2 — column overlap → foreign key</p>
            {statusPill(t2)}
          </div>
          {!t2.skipped ? (
            <>
              <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
                <span>pairs tested <span className="font-mono">{t2.total_pairs ?? "—"}</span></span>
                <span className="text-amber-700">flagged <span className="font-mono">{flagged.length}</span></span>
                <span>fail rate <Pct v={t2.fail_rate} /></span>
              </div>
              {flagged.length ? (
                <div className="mt-2 space-y-1.5">
                  {flagged.slice(0, 50).map((p, i) => (
                    <div key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50/50 px-2 py-1.5 text-[11px]">
                      <AlertTriangle size={11} className="shrink-0 text-amber-500" />
                      <span className="font-mono text-slate-700">{p.table_a}.{p.column_a}</span>
                      <span className="text-slate-400">↔</span>
                      <span className="font-mono text-slate-700">{p.table_b}.{p.column_b}</span>
                      <span className="text-slate-500">(<Pct v={p.overlap} /> overlap)</span>
                      {!readOnly ? (
                        <select
                          value={choices[i] ?? ""}
                          onChange={(e) =>
                            setChoices((prev) => ({ ...prev, [i]: e.target.value as UdrTest2ResolutionAction }))
                          }
                          className="ml-auto rounded border border-slate-200 bg-white px-1.5 py-0.5 text-[11px]"
                        >
                          <option value="">Resolve…</option>
                          {RESOLUTION_OPTIONS.map((o) => (
                            <option key={o.value} value={o.value}>{o.label}</option>
                          ))}
                        </select>
                      ) : null}
                    </div>
                  ))}
                  {!readOnly ? (
                    <button
                      onClick={applyResolutions}
                      disabled={busy === "t2"}
                      className="mt-1 inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-60"
                    >
                      <RefreshCw size={12} className={busy === "t2" ? "animate-spin" : ""} />
                      Apply resolutions &amp; re-test
                    </button>
                  ) : null}
                </div>
              ) : (
                <p className="mt-1 flex items-center gap-1 text-xs text-emerald-700">
                  <CheckCircle2 size={12} /> All column overlaps are explained by a foreign key.
                </p>
              )}
            </>
          ) : (
            <p className="mt-1 text-xs text-slate-400">{t2.reason ?? "No cleaned tables in this run."}</p>
          )}
        </div>
      ) : null}
    </div>
  );
}
