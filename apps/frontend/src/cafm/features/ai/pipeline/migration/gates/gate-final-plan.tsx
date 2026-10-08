import type { MigrationWritePlan, MigrationWritePlanTable } from "../../../chat-api";

const n = (v: number | null | undefined) => (typeof v === "number" ? v.toLocaleString("en-GB") : "0");

function hasDetails(t: MigrationWritePlanTable) {
  return (t.invalid_values?.length ?? 0) > 0 || (t.rows_cannot_write?.length ?? 0) > 0 || (t.new_columns?.length ?? 0) > 0;
}

function Details({ t }: { t: MigrationWritePlanTable }) {
  const invalid = t.invalid_values ?? [];
  const cannot = t.rows_cannot_write ?? [];
  const added = t.new_columns ?? [];
  const values = invalid.reduce((s, v) => s + (v.count || 0), 0);
  const rows = cannot.reduce((s, v) => s + (v.count || 0), 0);
  const parts = [
    invalid.length ? `${n(values)} ${values === 1 ? "value that does not fit" : "values that do not fit"}` : null,
    cannot.length ? `${n(rows)} ${rows === 1 ? "row that cannot be written" : "rows that cannot be written"}` : null,
    added.length ? `${added.length} new column${added.length === 1 ? "" : "s"}` : null,
  ].filter(Boolean);
  return (
    <details className="group rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
      <summary className="cursor-pointer text-[12px] font-medium text-slate-700">
        <span className="font-mono">{t.dest}</span> · {parts.join(" · ")}
      </summary>
      <div className="mt-2 space-y-3 text-[12px] text-slate-600">
        {invalid.length > 0 && (
          <div className="overflow-x-auto">
            <p className="mb-1 font-medium text-slate-700">Values that do not fit — written empty</p>
            <table className="w-full min-w-[22rem] text-left">
              <thead>
                <tr className="text-slate-500">
                  <th className="py-1 pr-3 font-medium">Column</th>
                  <th className="py-1 pr-3 font-medium text-right">Count</th>
                  <th className="py-1 pr-3 font-medium">Example</th>
                  <th className="py-1 font-medium">Destination type</th>
                </tr>
              </thead>
              <tbody>
                {invalid.map((v) => (
                  <tr key={`${v.column}:${v.dest_type}`} className="border-t border-slate-200">
                    <td className="py-1 pr-3 font-mono">{v.column}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{n(v.count)}</td>
                    <td className="py-1 pr-3 font-mono break-all">{v.sample}</td>
                    <td className="py-1 font-mono">{v.dest_type}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {cannot.length > 0 && (
          <div>
            <p className="mb-1 font-medium text-slate-700">Rows that cannot be written</p>
            <ul className="space-y-0.5">
              {cannot.map((r) => (
                <li key={r.reason} className="flex justify-between gap-3">
                  <span>{r.reason}</span>
                  <span className="tabular-nums">{n(r.count)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {added.length > 0 && (
          <p>
            <span className="font-medium text-slate-700">New columns: </span>
            <span className="font-mono">{added.join(", ")}</span>
          </p>
        )}
      </div>
    </details>
  );
}

/** What the write will do, from the engine's dry run (a Go-engine run's write gate). Renders
 *  nothing without a plan, so a Python-engine run's gate looks exactly as before. */
export default function GateFinalPlan({ plan }: { plan?: MigrationWritePlan | null }) {
  const tables = plan && Array.isArray(plan.tables) ? plan.tables : [];
  if (!tables.length) return null;
  const ddl = plan?.ddl_statements ?? 0;
  const withDetails = tables.filter(hasDetails);
  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-5 mb-6">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">What the write will do</p>
        {ddl > 0 && (
          <span className="text-[11px] text-slate-500">
            {ddl} schema change{ddl === 1 ? "" : "s"}
          </span>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[20rem] text-left text-sm">
          <thead>
            <tr className="text-[11px] text-slate-500">
              <th className="py-1.5 pr-3 font-medium">Destination</th>
              <th className="py-1.5 pr-3 font-medium text-right">Rows</th>
              <th className="py-1.5 pr-3 font-medium text-right">Will merge</th>
              <th className="py-1.5 font-medium text-right">Already on file</th>
            </tr>
          </thead>
          <tbody>
            {tables.map((t) => (
              <tr key={`${t.source}:${t.dest}`} className="border-t border-slate-100">
                <td className="py-1.5 pr-3">
                  <span className="font-mono text-slate-800">{t.dest}</span>
                  {t.creates_table && (
                    <span className="ml-1.5 rounded bg-indigo-50 px-1.5 py-0.5 text-[10.5px] font-medium text-indigo-700">new table</span>
                  )}
                  {t.source && t.source !== t.dest && <span className="ml-1.5 text-[11px] text-slate-500">from {t.source}</span>}
                </td>
                <td className="py-1.5 pr-3 text-right font-mono tabular-nums text-slate-800">{n(t.rows)}</td>
                <td className="py-1.5 pr-3 text-right font-mono tabular-nums text-slate-600">{n(t.merge_existing_assets)}</td>
                <td className="py-1.5 text-right font-mono tabular-nums text-slate-600" title="Skipped: these rows are already in the database">
                  {n(t.already_present)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {withDetails.length > 0 && (
        <div className="mt-3 space-y-2">
          {withDetails.map((t) => (
            <Details key={`d:${t.source}:${t.dest}`} t={t} />
          ))}
        </div>
      )}
    </div>
  );
}
