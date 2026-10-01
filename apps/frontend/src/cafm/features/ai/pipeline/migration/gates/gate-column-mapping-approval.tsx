"use client";
import { useMemo, useState } from "react";
import { CheckCircle } from "lucide-react";
import {
  useMigrationGateColumnMappingApproval,
  type MigrationColumnMappingGatePayload,
  type UdrColDestMap,
} from "../../../chat-api";

interface Props {
  migrationId: string;
  payload: MigrationColumnMappingGatePayload;
  onSubmitted: () => void;
}

/** Per-column decision: keep the matched destination, re-target it, or create it as a new column. */
type Decision = { kind: "keep" | "retarget" | "new"; target?: string };

/**
 * B14.1 gate — HITL per-column approval of the matched source→destination column mapping. For
 * every source column the user keeps the detected destination, RE-TARGETS it to a different
 * destination column, or EXCLUDES it (created as a NEW column instead of merged). The pipeline is
 * paused here and does NOT proceed to semantic mapping / write until the user approves. On submit,
 * only the CHANGED columns are sent as overrides: `{source_table: {source_field: dest|"__new__"}}`.
 */
export default function GateColumnMappingApproval({ migrationId, payload, onSubmitted }: Props) {
  const rows = payload.dest_mapping ?? [];
  const destCols = payload.dest_columns_by_table ?? {};
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [error, setError] = useState<string | null>(null);
  const { mutate, isPending } = useMigrationGateColumnMappingApproval({
    onSuccess: () => onSubmitted(),
    onError: (e) => setError(e instanceof Error ? e.message : "Failed to submit column mapping"),
  });

  const byTable = useMemo(() => {
    const m = new Map<string, UdrColDestMap[]>();
    for (const r of rows) {
      const tbl = r.source.split(".")[0] ?? "—";
      if (!m.has(tbl)) m.set(tbl, []);
      m.get(tbl)!.push(r);
    }
    return [...m.entries()];
  }, [rows]);

  const setDecision = (key: string, d: Decision) =>
    setDecisions((prev) => ({ ...prev, [key]: d }));

  const changedCount = Object.values(decisions).filter(
    (d) => d.kind === "new" || (d.kind === "retarget" && d.target),
  ).length;

  const handleApprove = () => {
    setError(null);
    const overrides: Record<string, Record<string, string>> = {};
    for (const r of rows) {
      const col = r.source_column ?? r.source.split(".").slice(1).join(".");
      // Fan the decision out to EVERY source table that fed this (deduped) column, so a change on
      // the collapsed work_order/workorders card applies to both source sheets.
      const tables = r.source_tables?.length ? r.source_tables : [r.source.split(".")[0] ?? ""];
      const d = decisions[r.source];
      if (!d) continue;
      let value: string | null = null;
      if (d.kind === "new") value = "__new__";
      else if (d.kind === "retarget" && d.target && d.target !== r.matched_column) value = d.target;
      if (value) {
        for (const tbl of tables) (overrides[tbl] ??= {})[col] = value;
      }
    }
    mutate({ migrationId, body: { overrides } });
  };

  const chip = (key: string, current: Decision["kind"], value: Decision["kind"], label: string, onPick: () => void) => (
    <button
      type="button"
      onClick={onPick}
      className={`rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors ${
        current === value
          ? value === "new"
            ? "bg-slate-700 text-white"
            : "bg-indigo-600 text-white"
          : "bg-slate-100 text-slate-600 hover:bg-slate-200"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="max-w-6xl">
      <div className="mb-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[11px] text-slate-400">B14.1</span>
          <span className="text-lg font-semibold text-slate-800">Confirm column mapping</span>
          <span className="inline-flex items-center rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700">CoA</span>
          <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800">Human approval required</span>
          <span className="ml-auto text-[11px] text-slate-400">{rows.length} columns</span>
        </div>
        <div className="mt-1 text-sm text-slate-500">
          Review the matched destination column for every source column. Keep the match, re-target
          it to a different destination column, or exclude it (created as a NEW column instead of
          merged). The pipeline is paused until you approve.
        </div>
      </div>

      <div className="space-y-3">
        {byTable.map(([tbl, cols]) => {
          // Every source sheet folded into this (deduped) card — >1 when duplicate sheets were
          // co-routed to the same destination (work_order + workorders → work_orders).
          const srcTables = [...new Set(cols.flatMap((r) => r.source_tables ?? [tbl]))];
          const destTbl = cols.find((r) => r.dest_table)?.dest_table ?? null;
          return (
          <div key={tbl} className="rounded-xl border border-slate-200 overflow-hidden">
            <div className="flex flex-wrap items-center gap-2 bg-slate-50 px-4 py-2 text-[12px]">
              <span className="font-mono font-semibold text-slate-800">{tbl}</span>
              {srcTables.length > 1 ? (
                <span
                  className="rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] font-bold text-white"
                  title={`duplicate sheets routed together: ${srcTables.join(", ")}`}
                >
                  ×{srcTables.length}
                </span>
              ) : null}
              {destTbl ? (
                <span className="text-[10px] text-slate-400">→ <span className="font-mono text-slate-600">{destTbl}</span></span>
              ) : null}
              <span className="rounded-full bg-slate-200 px-2 py-0.5 text-[10px] text-slate-600">{cols.length} columns</span>
            </div>
            <div className="divide-y divide-slate-100">
              {cols.map((r) => {
                const col = r.source.split(".").slice(1).join(".");
                const d = decisions[r.source] ?? { kind: "keep" as const };
                const options = destCols[String(r.dest_table ?? "").toLowerCase()]
                  ?? destCols[String(r.dest_table ?? "")]
                  ?? [];
                const isNew = !r.matched_column;
                return (
                  <div key={r.source} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5 font-mono text-[11.5px]">
                        <span className="text-indigo-700">{col}</span>
                        <span className="text-slate-300">→</span>
                        {d.kind === "new" ? (
                          <span className="text-slate-500">new column</span>
                        ) : d.kind === "retarget" && d.target ? (
                          <span className="text-emerald-700">{d.target}</span>
                        ) : r.matched_column ? (
                          <span className="text-emerald-700">{r.matched_column}</span>
                        ) : (
                          <span className="text-slate-500">new column</span>
                        )}
                        {r.dest_table ? (
                          <span className="text-[10px] text-slate-400">on {r.dest_table}</span>
                        ) : null}
                      </div>
                      {r.outcome ? (
                        <div className="text-[10px] text-slate-400">{r.outcome}{isNew ? " · no existing match" : ""}</div>
                      ) : null}
                      {/* B14.1 four dimensions: Dest UDR table (shown above as "on X") · PK? ·
                          cell-value format · sample values. */}
                      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px]">
                        {r.is_primary_key ? (
                          <span className="rounded bg-emerald-100 px-1.5 py-0.5 font-semibold text-emerald-700">PK</span>
                        ) : r.classification ? (
                          <span className="rounded bg-slate-100 px-1.5 py-0.5 font-medium text-slate-600">{r.classification}</span>
                        ) : null}
                        {r.format ? (
                          <span className="text-slate-500">format: <span className="font-mono text-slate-600">{r.format}</span></span>
                        ) : null}
                        {r.samples?.length ? (
                          <span className="min-w-0 truncate text-slate-400">
                            samples: <span className="font-mono text-slate-500">{r.samples.slice(0, 5).join(", ")}</span>
                          </span>
                        ) : null}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      {chip(r.source, d.kind, "keep", "Keep", () => setDecision(r.source, { kind: "keep" }))}
                      {options.length ? (
                        <select
                          value={d.kind === "retarget" ? (d.target ?? "") : ""}
                          onChange={(e) =>
                            setDecision(r.source, e.target.value ? { kind: "retarget", target: e.target.value } : { kind: "keep" })
                          }
                          className="rounded-md border border-slate-300 bg-white px-1.5 py-1 text-[11px] text-slate-600 focus:border-indigo-400 focus:outline-none"
                        >
                          <option value="">Re-target…</option>
                          {options.map((c) => (
                            <option key={c} value={c}>{c}</option>
                          ))}
                        </select>
                      ) : null}
                      {chip(r.source, d.kind, "new", "New column", () => setDecision(r.source, { kind: "new" }))}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
          );
        })}
      </div>

      {error ? (
        <div className="my-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
      ) : null}

      <button
        type="button"
        onClick={handleApprove}
        disabled={isPending}
        className="mt-4 inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-8 py-3 text-base font-medium text-white transition-colors hover:bg-indigo-700 disabled:opacity-50"
      >
        {isPending ? (
          <>
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
            Submitting…
          </>
        ) : (
          <>
            <CheckCircle size={18} />
            {changedCount ? `Apply ${changedCount} override${changedCount === 1 ? "" : "s"} & continue` : "Approve all & continue"}
          </>
        )}
      </button>
    </div>
  );
}
