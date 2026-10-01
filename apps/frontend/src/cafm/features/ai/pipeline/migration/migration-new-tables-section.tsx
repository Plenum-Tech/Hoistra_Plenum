"use client";

import { useState } from "react";
import { PlusCircle, Trash2 } from "lucide-react";
import { MIGRATION_DATA_TYPES } from "./migration-mapping-utils";

export type NewTableColumn = { column_name: string; data_type: string; nullable: boolean };
export type NewTableDef = { id: string; table_name: string; pk_col: string; columns: NewTableColumn[] };

function emptyNewTable(): NewTableDef {
  return {
    id: `nt_${Date.now()}_${Math.random().toString(16).slice(2)}`,
    table_name: "",
    pk_col: "id",
    columns: [{ column_name: "", data_type: "VARCHAR(255)", nullable: true }],
  };
}

type Props = {
  tables: NewTableDef[];
  onChange: (tables: NewTableDef[]) => void;
  compact?: boolean;
};

export function MigrationNewTablesSection({ tables, onChange, compact = false }: Props) {
  const [open, setOpen] = useState(tables.length > 0);

  function patchTable(id: string, patch: Partial<NewTableDef>) {
    onChange(tables.map((t) => (t.id === id ? { ...t, ...patch } : t)));
  }

  return (
    <div className={`rounded-xl border border-slate-200 bg-white shadow-sm ${compact ? "text-xs" : ""}`}>
      <button
        type="button"
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-50 text-left"
        onClick={() => setOpen((v) => !v)}
      >
        <div>
          <div className={`font-semibold text-slate-800 ${compact ? "text-xs" : "text-sm"}`}>New canonical tables</div>
          <div className="text-[11px] text-slate-500">Create tables with multiple columns (DDL on submit)</div>
        </div>
        <span className="text-[11px] font-mono text-indigo-600">{tables.length}</span>
      </button>

      {open ? (
        <div className="border-t border-slate-100 px-4 py-3 space-y-3">
          <button
            type="button"
            onClick={() => onChange([...tables, emptyNewTable()])}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-700 px-3 py-1.5 rounded-lg"
          >
            <PlusCircle size={13} />
            Add table
          </button>

          {tables.length === 0 ? (
            <p className="text-[11px] text-slate-500">No new tables yet.</p>
          ) : (
            tables.map((t) => (
              <div key={t.id} className="rounded-lg border border-slate-200 overflow-hidden">
                <div className="px-3 py-2 bg-slate-50 flex items-start justify-between gap-2">
                  <div className="grid grid-cols-2 gap-2 flex-1">
                    <div>
                      <label className="block text-[10px] font-medium text-slate-600 mb-0.5">Table name</label>
                      <input
                        className="w-full rounded border border-slate-200 px-2 py-1 text-[11px] font-mono"
                        value={t.table_name}
                        onChange={(e) => patchTable(t.id, { table_name: e.target.value })}
                        placeholder="new_table"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] font-medium text-slate-600 mb-0.5">Primary key</label>
                      <input
                        className="w-full rounded border border-slate-200 px-2 py-1 text-[11px] font-mono"
                        value={t.pk_col}
                        onChange={(e) => patchTable(t.id, { pk_col: e.target.value })}
                        placeholder="id"
                      />
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => onChange(tables.filter((x) => x.id !== t.id))}
                    className="p-1 text-slate-400 hover:text-red-500"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
                <div className="px-3 py-2 space-y-1.5">
                  {t.columns.map((c, idx) => (
                    <div key={idx} className="flex flex-wrap gap-1.5 items-center">
                      <input
                        className="flex-1 min-w-[6rem] rounded border border-slate-200 px-2 py-1 text-[11px] font-mono"
                        value={c.column_name}
                        onChange={(e) => {
                          const cols = t.columns.map((col, i) =>
                            i === idx ? { ...col, column_name: e.target.value } : col,
                          );
                          patchTable(t.id, { columns: cols });
                        }}
                        placeholder="column_name"
                      />
                      <select
                        className="rounded border border-slate-200 px-1 py-1 text-[11px] font-mono"
                        value={c.data_type}
                        onChange={(e) => {
                          const cols = t.columns.map((col, i) =>
                            i === idx ? { ...col, data_type: e.target.value } : col,
                          );
                          patchTable(t.id, { columns: cols });
                        }}
                      >
                        {MIGRATION_DATA_TYPES.map((dt) => (
                          <option key={dt} value={dt}>
                            {dt}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        onClick={() => patchTable(t.id, { columns: t.columns.filter((_, i) => i !== idx) })}
                        className="text-slate-400 hover:text-red-500 p-0.5"
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={() =>
                      patchTable(t.id, {
                        columns: [...t.columns, { column_name: "", data_type: "VARCHAR(255)", nullable: true }],
                      })
                    }
                    className="text-[11px] text-indigo-600 hover:text-indigo-800"
                  >
                    + Add column
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}
