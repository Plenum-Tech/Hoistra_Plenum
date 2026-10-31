"use client";

import { useMemo } from "react";
import { Loader2 } from "lucide-react";
import { useDbTableColumns } from "../../doc-rag-api";
import type { MigrationFlaggedFieldItem } from "../../chat-api";
import {
  getSuggestedTarget,
  parseSuggestionOptions,
  resolveTargetConfidence,
  toPlenumTableSlug,
} from "./migration-mapping-utils";
import { useCanonicalFieldScores } from "./use-canonical-field-scores";

export type ColumnOverrideMode = "existing" | "new_column";

export type ColumnOverrideDraft = {
  mode: ColumnOverrideMode;
  /** Selected or typed canonical column name (existing mode). */
  targetField: string;
  newColumnName: string;
  dataType: string;
  nullable: boolean;
};

export function emptyColumnOverrideDraft(suggested = ""): ColumnOverrideDraft {
  return {
    mode: "existing",
    targetField: suggested,
    // NOT pre-filled with `suggested`: the suggested match is a DIFFERENT existing column the
    // user is choosing between — naming a brand-new column after it (e.g. prepared_at) is wrong.
    // The new-column name is seeded from the SOURCE column when the user switches to DDL mode.
    newColumnName: "",
    dataType: "VARCHAR(255)",
    nullable: true,
  };
}

type Props = {
  item: MigrationFlaggedFieldItem;
  canonicalTable: string;
  draft: ColumnOverrideDraft;
  onChange: (patch: Partial<ColumnOverrideDraft>) => void;
  compact?: boolean;
};

const CUSTOM_FIELD_VALUE = "__custom__";

export function MigrationColumnOverride({ item, canonicalTable, draft, onChange, compact = false }: Props) {
  const suggested = getSuggestedTarget(item);
  const suggestionOptions = parseSuggestionOptions(
    Array.isArray(item.suggestions) ? (item.suggestions as unknown[]) : [],
  );

  const tableForColumns = toPlenumTableSlug(canonicalTable.trim());
  const { data: dbColumns, isLoading: columnsLoading, isError: columnsError } = useDbTableColumns(
    tableForColumns,
    { enabled: draft.mode === "existing" && !!tableForColumns },
  );

  const columnOptions = useMemo(() => {
    const names = new Set<string>();
    if (suggested) names.add(suggested);
    for (const s of suggestionOptions) names.add(s.field);
    for (const c of dbColumns ?? []) {
      if (c.name?.trim()) names.add(c.name.trim());
    }
    return Array.from(names).sort((a, b) => a.localeCompare(b));
  }, [dbColumns, suggested, suggestionOptions]);

  const knownSuggestionFields = useMemo(
    () => new Set(suggestionOptions.map((s) => s.field)),
    [suggestionOptions],
  );
  const fieldsToScore = useMemo(
    () => columnOptions.filter((n) => !knownSuggestionFields.has(n)),
    [columnOptions, knownSuggestionFields],
  );
  const { data: canonicalScores } = useCanonicalFieldScores(
    item.source_field,
    item.sample_values,
    fieldsToScore,
    { enabled: draft.mode === "existing" && !!tableForColumns && fieldsToScore.length > 0 },
  );
  const extraScores = canonicalScores?.scores;

  function optionLabel(field: string): string {
    const fromSuggestion = suggestionOptions.find((s) => s.field === field);
    if (fromSuggestion?.confidence != null) {
      return `${field} (${Math.round(fromSuggestion.confidence * 100)}%)`;
    }
    const resolved = resolveTargetConfidence(item, field, extraScores);
    if (resolved != null) {
      return `${field} (${Math.round(resolved * 100)}%)`;
    }
    return field;
  }

  const selectValue =
    draft.targetField && columnOptions.includes(draft.targetField)
      ? draft.targetField
      : draft.targetField
        ? CUSTOM_FIELD_VALUE
        : "";

  const labelCls = compact ? "text-[10px] font-medium text-slate-600" : "text-xs font-medium text-slate-600";
  const inputCls = compact
    ? "w-full rounded-lg border border-slate-200 bg-white px-2 py-1 text-[11px] font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
    : "w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500";

  return (
    <div className={`rounded-lg border border-indigo-200 bg-indigo-50/60 space-y-2 ${compact ? "p-2" : "p-3"}`}>
      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          onClick={() => onChange({ mode: "existing", targetField: draft.targetField || suggested })}
          className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium border transition-colors ${
            draft.mode === "existing"
              ? "bg-indigo-600 text-white border-indigo-600"
              : "bg-white text-slate-600 border-slate-200 hover:border-indigo-300"
          }`}
        >
          Existing column
        </button>
        <button
          type="button"
          onClick={() =>
            onChange({
              mode: "new_column",
              // Name a NEW column after the SOURCE column (e.g. responded_at), not the suggested
              // existing match (e.g. prepared_at) which the user is declining. Preserve a name the
              // user already typed.
              newColumnName: draft.newColumnName || item.source_field || draft.targetField || suggested,
            })
          }
          className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium border transition-colors ${
            draft.mode === "new_column"
              ? "bg-indigo-600 text-white border-indigo-600"
              : "bg-white text-slate-600 border-slate-200 hover:border-indigo-300"
          }`}
        >
          New column (DDL)
        </button>
      </div>

      {draft.mode === "existing" ? (
        <div className="space-y-2">
          <div>
            <label className={labelCls}>Map to column on {tableForColumns || "—"}</label>
            {columnsLoading ? (
              <div className="mt-1 flex items-center gap-1.5 text-[11px] text-slate-500">
                <Loader2 size={12} className="animate-spin" />
                Loading database columns…
              </div>
            ) : null}
            {columnsError ? (
              <p className="mt-1 text-[11px] text-amber-700">
                Could not load DB columns — type the column name below.
              </p>
            ) : null}
            <select
              className={`${inputCls} mt-1`}
              value={selectValue}
              onChange={(e) => {
                const v = e.target.value;
                if (v === CUSTOM_FIELD_VALUE) {
                  onChange({ targetField: draft.targetField || suggested });
                } else {
                  onChange({ targetField: v });
                }
              }}
            >
              <option value="">— select column —</option>
              {suggestionOptions.map((s) => (
                <option key={`sug-${s.field}`} value={s.field}>
                  {optionLabel(s.field)}
                </option>
              ))}
              {columnOptions
                .filter((n) => !suggestionOptions.some((s) => s.field === n))
                .map((n) => (
                  <option key={`db-${n}`} value={n}>
                    {optionLabel(n)}
                  </option>
                ))}
              <option value={CUSTOM_FIELD_VALUE}>Other — type below</option>
            </select>
          </div>
          {selectValue === CUSTOM_FIELD_VALUE || (draft.targetField && !columnOptions.includes(draft.targetField)) ? (
            <div>
              <label className={labelCls}>Custom column name</label>
              <input
                className={`${inputCls} mt-1`}
                list={`cols-${tableForColumns}`}
                value={draft.targetField}
                onChange={(e) => onChange({ targetField: e.target.value })}
                placeholder="canonical_field_name"
              />
              <datalist id={`cols-${tableForColumns}`}>
                {columnOptions.map((n) => (
                  <option key={n} value={n} />
                ))}
              </datalist>
            </div>
          ) : null}
        </div>
      ) : (
        <div className={`grid gap-2 ${compact ? "grid-cols-1" : "grid-cols-1 sm:grid-cols-2"}`}>
          <div className={compact ? "" : "sm:col-span-2"}>
            <label className={labelCls}>New column name</label>
            <input
              className={`${inputCls} mt-1`}
              value={draft.newColumnName}
              onChange={(e) => onChange({ newColumnName: e.target.value })}
              placeholder="e.g. asset_code"
            />
          </div>
          <div>
            <label className={labelCls}>Data type</label>
            <select
              className={`${inputCls} mt-1`}
              value={draft.dataType}
              onChange={(e) => onChange({ dataType: e.target.value })}
            >
              {["VARCHAR(255)", "VARCHAR(100)", "TEXT", "INTEGER", "BIGINT", "DECIMAL(10,2)", "BOOLEAN", "TIMESTAMPTZ", "DATE", "JSONB", "UUID"].map(
                (t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ),
              )}
            </select>
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={draft.nullable}
                onChange={(e) => onChange({ nullable: e.target.checked })}
                className="rounded border-slate-300"
              />
              <span className={labelCls}>Nullable</span>
            </label>
          </div>
          <p className={`text-slate-500 ${compact ? "text-[10px] sm:col-span-2" : "text-xs sm:col-span-2"}`}>
            Creates{" "}
            <code className="font-mono bg-white px-1 rounded">{tableForColumns || "…"}.{draft.newColumnName || "…"}</code>{" "}
            on submit.
          </p>
        </div>
      )}
    </div>
  );
}
