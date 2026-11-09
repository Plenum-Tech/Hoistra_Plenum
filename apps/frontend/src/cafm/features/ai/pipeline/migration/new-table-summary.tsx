"use client";
import { useState } from "react";

/**
 * A table this migration creates, said in words: its name, its columns with their types, which
 * column is the key, and which source columns now point to it.
 *
 * Replaces the raw CREATE TABLE text that used to fill the screen under "Emitted DDL" (asked for on
 * 9 Nov 2026: "just give us as new table is created"). The columns are the ones the service
 * declared and rendered that SQL from, so this and the SQL cannot disagree; the SQL itself stays
 * one click away for anyone who needs it.
 *
 * One component on purpose: the B22.1 card, the 2.9 classification gate and the B14.1 column
 * mapping gate show the same tables, and three copies would drift.
 */

export type NewTableColumn = {
  name: string;
  type: string;
  is_primary_key?: boolean;
  nullable?: boolean;
  note?: string;
};

export type NewTable = {
  table_name: string;
  pk_column?: string;
  columns?: NewTableColumn[];
  unique_on?: string[];
  fk_rewrites?: Array<{ source: string; references: string }>;
  create_ddl?: string;
  fk_ddl?: string[];
  /** How many rows it will hold -- one per distinct value -- and the first few of them. */
  distinct_count?: number;
  sample_values?: string[];
};

/** "uuid", "text", "timestamptz" -- lower case, without a length or default clause. */
function shortType(t: string): string {
  return String(t || "").toLowerCase().split(/[\s(]/)[0] || "";
}

export function NewTableSummary({ table, showLinks = true }: { table: NewTable; showLinks?: boolean }) {
  const [sql, setSql] = useState(false);
  const cols = table.columns ?? [];
  const ddl = [table.create_ddl, ...(table.fk_ddl ?? [])].filter(Boolean).join("\n");
  const links = showLinks ? (table.fk_rewrites ?? []) : [];
  return (
    <div className="rounded-lg border border-emerald-200 bg-emerald-50/40 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center rounded-sm bg-emerald-100 px-1 text-[8.5px] font-semibold uppercase text-emerald-800">
          new table
        </span>
        <span className="font-mono text-[11.5px] font-semibold text-emerald-800">{table.table_name}</span>
        {cols.length ? (
          <span className="text-[10px] text-slate-500">
            {cols.length} column{cols.length === 1 ? "" : "s"}
          </span>
        ) : null}
        {ddl ? (
          <button
            type="button"
            onClick={() => setSql((v) => !v)}
            className="ml-auto text-[10px] text-slate-400 underline-offset-2 hover:text-slate-600 hover:underline"
          >
            {sql ? "Hide SQL" : "Show SQL"}
          </button>
        ) : null}
      </div>

      {cols.length ? (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {cols.map((c) => (
            <span
              key={c.name}
              title={c.note || undefined}
              className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[10px] ${
                c.is_primary_key
                  ? "border-emerald-300 bg-white text-emerald-800"
                  : "border-slate-200 bg-white text-slate-700"
              }`}
            >
              {c.is_primary_key ? (
                <span className="text-[8.5px] font-semibold uppercase tracking-wider text-emerald-700">key</span>
              ) : null}
              {c.name}
              <span className="text-slate-400">{shortType(c.type)}</span>
              {c.nullable === false && !c.is_primary_key ? (
                <span className="text-[8.5px] text-slate-400" title="always filled">required</span>
              ) : null}
            </span>
          ))}
        </div>
      ) : table.pk_column ? (
        <div className="mt-1 text-[10.5px] text-slate-600">
          Keyed on <span className="font-mono">{table.pk_column}</span>
        </div>
      ) : null}

      {table.unique_on?.length ? (
        <div className="mt-1 text-[10px] text-slate-500">
          One row per <span className="font-mono">{table.unique_on.join(" + ")}</span>
        </div>
      ) : null}

      {links.length ? (
        <div className="mt-1.5 text-[10px] text-slate-500">
          Now pointing to it:{" "}
          {links.map((rw, i) => (
            <span key={i} className="font-mono text-slate-600">
              {i ? ", " : ""}
              {rw.source}
            </span>
          ))}
        </div>
      ) : null}

      {sql && ddl ? (
        <pre className="mt-2 overflow-x-auto rounded bg-slate-900 p-2.5 text-[10px] leading-relaxed text-slate-100">
          <code>{ddl}</code>
        </pre>
      ) : null}
    </div>
  );
}

export function NewTablesSummary({
  tables,
  showLinks = true,
}: {
  tables: NewTable[] | undefined | null;
  showLinks?: boolean;
}) {
  const list = (tables ?? []).filter((t) => t && t.table_name);
  if (!list.length) return null;
  return (
    <div className="space-y-2">
      {list.map((t) => (
        <NewTableSummary key={t.table_name} table={t} showLinks={showLinks} />
      ))}
    </div>
  );
}
