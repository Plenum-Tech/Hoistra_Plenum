"use client";

import { useMemo, type ReactNode } from "react";
import type { MigrationFlaggedFieldItem } from "../../chat-api";
import { useDbTableColumns } from "../../doc-rag-api";
import {
  buildMatchScoreRows,
  parseSuggestionOptions,
  resolveTargetConfidence,
  toPlenumTableSlug,
  type MatchScoreRow,
} from "./migration-mapping-utils";
import { useCanonicalFieldScores } from "./use-canonical-field-scores";

type Props = {
  item: MigrationFlaggedFieldItem;
  canonicalTable: string;
  topMatchLimit: number;
  activeTarget: string;
};

export function useFlaggedFieldMatchScores({ item, canonicalTable, topMatchLimit, activeTarget }: Props) {
  const parsedSuggestions = parseSuggestionOptions(
    Array.isArray(item.suggestions) ? (item.suggestions as unknown[]) : [],
  );
  const knownFields = useMemo(() => new Set(parsedSuggestions.map((s) => s.field)), [parsedSuggestions]);

  const tableForColumns = toPlenumTableSlug(canonicalTable.trim());
  const { data: dbColumns } = useDbTableColumns(tableForColumns, {
    enabled: !!tableForColumns,
  });

  const fieldsToScore = useMemo(() => {
    const names = new Set<string>();
    for (const c of dbColumns ?? []) {
      const n = c.name?.trim();
      if (n && !knownFields.has(n)) names.add(n);
    }
    const active = activeTarget.trim();
    if (active && !knownFields.has(active)) names.add(active);
    return Array.from(names);
  }, [dbColumns, knownFields, activeTarget]);

  const { data: scoreData, isLoading: scoresLoading } = useCanonicalFieldScores(
    item.source_field,
    item.sample_values,
    fieldsToScore,
    { enabled: fieldsToScore.length > 0 },
  );

  const extraScores = scoreData?.scores;
  const rows: MatchScoreRow[] = buildMatchScoreRows(item, {
    topLimit: topMatchLimit,
    activeTarget,
    extraScores,
  });
  const activeConfidence = resolveTargetConfidence(item, activeTarget, extraScores);

  return { rows, activeConfidence, extraScores, scoresLoading };
}

export function FlaggedFieldScoreSection({
  item,
  canonicalTable,
  topMatchLimit,
  activeTarget,
  children,
}: Props & {
  children: (ctx: {
    rows: MatchScoreRow[];
    activeConfidence: number | null;
    scoresLoading: boolean;
  }) => ReactNode;
}) {
  const ctx = useFlaggedFieldMatchScores({ item, canonicalTable, topMatchLimit, activeTarget });
  return <>{children(ctx)}</>;
}

export function MatchScoreTable({
  rows,
  scoresLoading,
  selectable = false,
  onSelectSuggested,
}: {
  rows: MatchScoreRow[];
  scoresLoading?: boolean;
  selectable?: boolean;
  onSelectSuggested?: (field: string) => void;
}) {
  if (!rows.length) return null;
  return (
    <div className="mb-2 rounded-lg border border-slate-200 overflow-hidden">
      {selectable ? (
        <p className="text-[10px] text-indigo-700 bg-indigo-50/80 px-2.5 py-1 border-b border-indigo-100">
          Click a suggested match row to set the override target
        </p>
      ) : null}
      <table className="w-full text-xs">
        <thead className="bg-slate-50">
          <tr>
            <th className="text-left font-medium text-slate-500 px-2.5 py-1.5">Source Column</th>
            <th className="text-left font-medium text-slate-500 px-2.5 py-1.5">Suggested Match</th>
            <th className="text-right font-medium text-slate-500 px-2.5 py-1.5">Score</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((row) => (
            <tr
              key={`${row.source}-${row.suggested}-${row.order}`}
              className={
                selectable && onSelectSuggested
                  ? "bg-white cursor-pointer hover:bg-indigo-50/80"
                  : "bg-white"
              }
              onClick={
                selectable && onSelectSuggested && row.suggested
                  ? () => onSelectSuggested(row.suggested)
                  : undefined
              }
              onKeyDown={
                selectable && onSelectSuggested && row.suggested
                  ? (e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelectSuggested(row.suggested);
                      }
                    }
                  : undefined
              }
              tabIndex={selectable && onSelectSuggested && row.suggested ? 0 : undefined}
              role={selectable && onSelectSuggested ? "button" : undefined}
            >
              <td className="px-2.5 py-1.5 font-mono text-slate-700">{row.source}</td>
              <td className="px-2.5 py-1.5 font-mono text-indigo-700">{row.suggested}</td>
              <td className="px-2.5 py-1.5 text-right font-mono text-slate-600">
                {row.score != null
                  ? `${Math.round(row.score * 100)}%`
                  : scoresLoading
                    ? "…"
                    : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
