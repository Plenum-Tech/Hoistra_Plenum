"use client";

import { useMemo } from "react";

const NEW_TABLE_VALUE = "__new_table__";

type Props = {
  sourceTable: string;
  canonicalTables: string[];
  value: string;
  isNewTable: boolean;
  newTableName: string;
  onChange: (next: { canonicalTable: string; isNewTable: boolean; newTableName: string }) => void;
  compact?: boolean;
};

export function MigrationCanonicalTableSelect({
  sourceTable,
  canonicalTables,
  value,
  isNewTable,
  newTableName,
  onChange,
  compact = false,
}: Props) {
  const options = useMemo(() => {
    const set = new Set(canonicalTables.filter((t) => t.trim()));
    if (value.trim() && !isNewTable) set.add(value.trim());
    if (newTableName.trim()) set.add(newTableName.trim());
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [canonicalTables, value, isNewTable, newTableName]);

  const selectCls = compact
    ? "w-full rounded-lg border border-slate-200 bg-white px-2 py-1 text-[11px] font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
    : "w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500";

  return (
    <div className="space-y-2">
      <label className={compact ? "text-[10px] font-medium text-slate-600" : "text-xs font-medium text-slate-600"}>
        Canonical target table
        <span className="text-slate-400 font-normal ml-1">(source: {sourceTable})</span>
      </label>
      <select
        className={selectCls}
        value={isNewTable ? NEW_TABLE_VALUE : value}
        onChange={(e) => {
          const v = e.target.value;
          if (v === NEW_TABLE_VALUE) {
            onChange({ canonicalTable: newTableName || "", isNewTable: true, newTableName });
          } else {
            onChange({ canonicalTable: v, isNewTable: false, newTableName });
          }
        }}
      >
        <option value="">— select table —</option>
        {options.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
        <option value={NEW_TABLE_VALUE}>+ New table…</option>
      </select>
      {isNewTable ? (
        <div>
          <label className={compact ? "text-[10px] font-medium text-slate-600" : "text-xs font-medium text-slate-600"}>
            New table name
          </label>
          <input
            className={`${selectCls} mt-1`}
            value={newTableName}
            onChange={(e) =>
              onChange({ canonicalTable: e.target.value, isNewTable: true, newTableName: e.target.value })
            }
            placeholder="e.g. custom_assets"
          />
          <p className="mt-1 text-[10px] text-slate-500">Table will be created in plenum_cafm when you add columns below.</p>
        </div>
      ) : null}
    </div>
  );
}

export function resolveCanonicalTable(
  sourceTable: string,
  bySource: Record<string, string>,
  newTableFlags: Record<string, boolean>,
  newTableNames: Record<string, string>,
): { table: string; isNewTable: boolean } {
  const isNew = newTableFlags[sourceTable] ?? false;
  const name = isNew
    ? (newTableNames[sourceTable]?.trim() || bySource[sourceTable]?.trim() || "")
    : (bySource[sourceTable]?.trim() || sourceTable);
  return { table: name, isNewTable: isNew };
}
