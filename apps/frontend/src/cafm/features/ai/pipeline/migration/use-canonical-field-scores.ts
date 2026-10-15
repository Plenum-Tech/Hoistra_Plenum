"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { schemaMapperApi } from "../../chat-api";

/** Fetch embedding similarity scores for canonical columns (e.g. override ``id``). */
export function useCanonicalFieldScores(
  sourceField: string,
  sampleValues: string[] | undefined,
  canonicalFields: string[],
  opts?: { enabled?: boolean; fieldDescription?: string },
) {
  const fieldKey = useMemo(
    () => [...canonicalFields].map((f) => f.trim()).filter(Boolean).sort().join("\0"),
    [canonicalFields],
  );

  return useQuery({
    queryKey: [
      "migration",
      "canonical-field-scores",
      sourceField,
      fieldKey,
      (sampleValues ?? []).join(","),
    ],
    enabled: (opts?.enabled ?? true) && !!sourceField.trim() && fieldKey.length > 0,
    staleTime: 120_000,
    queryFn: () =>
      schemaMapperApi.canonicalFieldScores({
        source_field: sourceField,
        field_description: opts?.fieldDescription,
        sample_values: sampleValues,
        canonical_fields: canonicalFields.filter((f) => f.trim().length > 0),
      }),
  });
}
