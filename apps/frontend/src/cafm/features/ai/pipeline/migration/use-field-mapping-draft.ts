"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { MigrationStatusResponse } from "../../chat-api";
import {
  FIELD_MAPPING_DRAFT_EVENT,
  FIELD_MAPPING_DRAFT_KEY,
  fetchFieldMappingDraftFromServer,
  loadFieldMappingDraftEnvelope,
  mergeDraftEnvelopes,
  parseEnvelope,
  type FieldMappingDraftEnvelope,
} from "./migration-field-mapping-draft";

function envelopeFromStatus(
  migration: MigrationStatusResponse | null | undefined,
): FieldMappingDraftEnvelope | null {
  const raw = migration?.field_mapping_draft;
  return parseEnvelope(raw);
}

export type UseFieldMappingDraftOptions = {
  /** When false, skip server GET (e.g. preprocess step pause). */
  fetchRemote?: boolean;
};

/** Live Tier-2 / field-mapping draft — sessionStorage + optional server + status poll. */
export function useFieldMappingDraft(
  migrationId: string,
  migration?: MigrationStatusResponse | null,
  opts?: UseFieldMappingDraftOptions,
) {
  const fetchRemote = opts?.fetchRemote !== false;
  const remoteLoadedRef = useRef<string | null>(null);

  const [envelope, setEnvelope] = useState<FieldMappingDraftEnvelope | null>(() =>
    migrationId ? loadFieldMappingDraftEnvelope(migrationId) : null,
  );

  const mergeLocalStatus = useCallback(() => {
    if (!migrationId) {
      setEnvelope(null);
      return;
    }
    const local = loadFieldMappingDraftEnvelope(migrationId);
    const fromStatus = envelopeFromStatus(migration);
    setEnvelope(mergeDraftEnvelopes(local, fromStatus));
  }, [migrationId, migration?.field_mapping_draft]);

  const loadRemoteOnce = useCallback(async () => {
    if (!migrationId || !fetchRemote) return;
    if (remoteLoadedRef.current === migrationId) return;
    remoteLoadedRef.current = migrationId;
    const local = loadFieldMappingDraftEnvelope(migrationId);
    const remote = await fetchFieldMappingDraftFromServer(migrationId);
    const fromStatus = envelopeFromStatus(migration);
    const merged = mergeDraftEnvelopes(mergeDraftEnvelopes(local, remote), fromStatus);
    if (merged) {
      try {
        sessionStorage.setItem(
          FIELD_MAPPING_DRAFT_KEY(migrationId),
          JSON.stringify(merged),
        );
      } catch {
        /* ignore */
      }
    }
    setEnvelope(merged);
  }, [migrationId, fetchRemote, migration?.field_mapping_draft]);

  useEffect(() => {
    remoteLoadedRef.current = null;
    mergeLocalStatus();
    void loadRemoteOnce();
  }, [migrationId, mergeLocalStatus, loadRemoteOnce]);

  useEffect(() => {
    mergeLocalStatus();
  }, [mergeLocalStatus]);

  useEffect(() => {
    if (!migrationId || typeof window === "undefined") return;
    const onUpdate = (ev: Event) => {
      const detail = (ev as CustomEvent<{ migrationId?: string }>).detail;
      if (detail?.migrationId && detail.migrationId !== migrationId) return;
      mergeLocalStatus();
    };
    window.addEventListener(FIELD_MAPPING_DRAFT_EVENT, onUpdate);
    return () => window.removeEventListener(FIELD_MAPPING_DRAFT_EVENT, onUpdate);
  }, [migrationId, mergeLocalStatus]);

  return envelope;
}
