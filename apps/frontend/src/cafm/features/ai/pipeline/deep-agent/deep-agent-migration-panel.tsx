"use client";

import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronLeft, Database, FileSpreadsheet, History } from "lucide-react";
import { SkeletonPanel } from "@/components/ui";

import { collapseMigrationIdsForUpload } from "@/features/ai/deep-agents-api";
import { MIGRATION_POLL_INTERVAL_MS, useMigrationStatus } from "@/features/ai/chat-api";
import { createUdrRun, listUdrRuns, type UdrRunVersion } from "@/features/ai/udr-runs-api";
import MigrationContent from "@/features/ai/pipeline/migration/migration-content";
import type { DeepAgentProcessLogInput } from "./deep-agent-process-log";
import { cn } from "@/utils/cn";

export type DeepAgentMigrationContext = {
  migrationIds: string[];
  fileNames: string[];
};

type Props = {
  context: DeepAgentMigrationContext;
  onDismiss?: () => void;
  /** Rendered inside orchestrator right rail (~420px) — tighter layout. */
  embeddedRail?: boolean;
  /** Orchestrator session — enables saving/selecting migration versions. */
  sessionId?: string;
  /** Emit per-node field-mapping entries into the right-side Process log. */
  onProcessLog?: (entry: DeepAgentProcessLogInput) => void;
  /**
   * When the migration panel is opened from the UDR Saved Script panel, this
   * callback returns the user there. Renders a "Back to UDR" breadcrumb at the
   * top of the panel so context is never lost (Feature 4.6).
   */
  onBackToUdr?: () => void;
  /** Returns to the conversation. When set, a compact "Back to chat" control is
   *  rendered inline in the header row (so it no longer needs its own row above). */
  onBackToChat?: () => void;
};

export function DeepAgentMigrationPanel({
  context,
  onDismiss,
  embeddedRail = true,
  sessionId,
  onProcessLog,
  onBackToUdr,
  onBackToChat,
}: Props) {
  const queryClient = useQueryClient();
  const ids = collapseMigrationIdsForUpload(context.migrationIds);
  const [selectedMigrationId, setSelectedMigrationId] = useState(() => ids[0] ?? "");

  // Migration versions (reuses udr_run_versions): add a version, then select by ID.
  const [versions, setVersions] = useState<UdrRunVersion[]>([]);
  const [savingVersion, setSavingVersion] = useState(false);

  const reloadVersions = useCallback(async () => {
    if (!sessionId) return;
    try {
      setVersions(await listUdrRuns(sessionId, 10));
    } catch {
      /* keep prior list */
    }
  }, [sessionId]);

  useEffect(() => {
    void reloadVersions();
  }, [reloadVersions]);

  const handleSaveVersion = async () => {
    if (!sessionId || !selectedMigrationId) return;
    setSavingVersion(true);
    try {
      const nextNo = (versions[0]?.version_no ?? 0) + 1;
      const created = await createUdrRun({
        sessionId,
        customName: `Migration version ${nextNo}`,
        migrationIds: [selectedMigrationId],
      });
      // Archive the current run's snapshots under a version-scoped key so the
      // user can reopen this version later and see its completed steps. Without
      // this, the next "Restart from Node 1" wipes the live sessionStorage key
      // (because v1 / v2 / v3 share the same migration_id) and the saved
      // version becomes an empty shell — only the new run's first step shows.
      if (created?.id && typeof window !== "undefined") {
        try {
          const snapKey = `plenum-migration-snapshot-by-node:${selectedMigrationId}`;
          const preSemKey = `plenum-migration-pre-semantic-history:${selectedMigrationId}`;
          const snap = sessionStorage.getItem(snapKey);
          const presem = sessionStorage.getItem(preSemKey);
          if (snap) {
            sessionStorage.setItem(
              `plenum-migration-snapshot-by-node:version:${created.id}`,
              snap,
            );
          }
          if (presem) {
            sessionStorage.setItem(
              `plenum-migration-pre-semantic-history:version:${created.id}`,
              presem,
            );
          }
        } catch {
          /* ignore quota / private-mode storage errors */
        }
      }
      await reloadVersions();
    } catch {
      /* ignore */
    } finally {
      setSavingVersion(false);
    }
  };

  // Dropdown selection state — kept independent of selectedMigrationId so the
  // user's pick sticks visually even when v1/v2/v3 share the same migration_id
  // (which happens because "Restart from Node 1" rewinds the same migration
  // rather than creating a new one). Without this, deriving the dropdown value
  // from selectedMigrationId would always snap back to the newest version
  // because `find` returns the first match.
  // null  = nothing chosen yet → derive from selectedMigrationId for default
  // ""    = user picked "Current run"
  // <id>  = user picked a specific version
  const [pickedVersionId, setPickedVersionId] = useState<string | null>(null);

  const handleSelectVersion = (versionId: string) => {
    setPickedVersionId(versionId);
    if (!versionId) {
      // "Current run" picked — switch to the latest migration_id for this context.
      setSelectedMigrationId(ids[0] ?? "");
      return;
    }
    const mid = versions.find((v) => v.id === versionId)?.migration_ids?.[0];
    if (mid) setSelectedMigrationId(mid);
  };

  const currentVersionId =
    pickedVersionId !== null
      ? pickedVersionId
      : versions.find((v) => v.migration_ids?.[0] === selectedMigrationId)?.id ?? "";

  const selectedVersion = currentVersionId
    ? versions.find((v) => v.id === currentVersionId) ?? null
    : null;

  useEffect(() => {
    if (!ids.length) {
      setSelectedMigrationId("");
      return;
    }
    if (!ids.includes(selectedMigrationId)) {
      setSelectedMigrationId(ids[0] ?? "");
    }
  }, [ids, selectedMigrationId]);

  const selectedIdx = ids.indexOf(selectedMigrationId);
  const label =
    (selectedIdx >= 0 ? context.fileNames[selectedIdx] : null) ??
    selectedMigrationId.slice(0, 8);
  const [highlightTerms, setHighlightTerms] = useState<string[]>([]);
  /** Poll while pipeline is active; stop on stable step_paused / terminal states. */
  const [forcePollUntil, setForcePollUntil] = useState(0);

  const { data: migration, isLoading, refetch } = useMigrationStatus(selectedMigrationId, {
    enabled: !!selectedMigrationId,
    // Shared cadence with the Activity Log so the panel + log update in lockstep (was 3000ms).
    refetchInterval: MIGRATION_POLL_INTERVAL_MS,
    forceUntil: forcePollUntil,
  });

  useEffect(() => {
    if (!selectedMigrationId) return;
    const st = String(migration?.status ?? "").toLowerCase();
    const terminal =
      st === "complete" ||
      st === "failed" ||
      st === "ddl_failed" ||
      st === "error" ||
      st === "cancelled" ||
      st === "canceled";
    if (terminal) {
      setForcePollUntil(0);
      return;
    }
    if (st === "step_paused" || st === "awaiting_review") {
      setForcePollUntil(0);
      return;
    }
    if (st === "running") {
      setForcePollUntil(Date.now() + 5 * 60_000);
    }
  }, [selectedMigrationId, migration?.status]);

  function handleRefresh() {
    setForcePollUntil(Date.now() + 60_000);
    void refetch();
    void queryClient.invalidateQueries({
      queryKey: ["migration", "status", selectedMigrationId],
    });
  }

  return (
    <div
      className={cn(
        "flex h-full min-h-0 flex-col overflow-hidden bg-white shadow-sm",
        embeddedRail ? "rounded-2xl border border-slate-200" : "rounded-xl border border-slate-200/90",
      )}
    >
      <div className="shrink-0 border-b border-slate-200 px-3 py-2 bg-gradient-to-r from-emerald-50/80 to-white">
        {onBackToUdr ? (
          <button
            type="button"
            onClick={onBackToUdr}
            className="mb-1.5 inline-flex items-center gap-1 rounded-full bg-cyan-50 px-2 py-0.5 text-[10px] font-medium text-cyan-700 hover:bg-cyan-100 transition-colors"
            aria-label="Back to Saved UDR script"
          >
            <ChevronLeft size={11} />
            <Database size={10} />
            Back to UDR script
          </button>
        ) : null}
        {/* Compact single-row header: title + run-id (left) · version controls (right).
            The full "same gates" description moved to the run-id tooltip, and the
            redundant "Current run · <id>" line dropped (the dropdown already shows it). */}
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex min-w-0 items-center gap-1.5">
            {onBackToChat ? (
              <button
                type="button"
                onClick={onBackToChat}
                title="Back to chat"
                aria-label="Back to chat"
                className="-ml-1 inline-flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900"
              >
                <ArrowLeft size={12} />
                Chat
              </button>
            ) : null}
            <FileSpreadsheet size={13} className="text-emerald-600 shrink-0" />
            <span className="shrink-0 text-xs font-semibold text-slate-800">Migration ingest</span>
            <span
              className="truncate font-mono text-[10px] text-slate-400"
              title={`${label} — same gates as Migration Ingestor: pre-semantic, semantic edit, field mapping, hierarchy.`}
            >
              · {label}
            </span>
          </div>
          {sessionId ? (
            <div className="flex shrink-0 items-center gap-1.5">
              <History size={11} className="shrink-0 text-emerald-600" />
              <select
                value={currentVersionId}
                onChange={(e) => handleSelectVersion(e.target.value)}
                className="max-w-[12rem] rounded border border-slate-200 bg-white px-1.5 py-0.5 text-[10px]"
                title="Select a saved migration version by ID"
                aria-label="Migration version"
              >
                <option value="">Current run ({(selectedMigrationId || "").slice(0, 8)})</option>
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    v{v.version_no}
                    {v.custom_name ? ` · ${v.custom_name}` : ""}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => void handleSaveVersion()}
                disabled={savingVersion || !selectedMigrationId}
                className="rounded border border-emerald-200 bg-white px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 hover:bg-emerald-50 disabled:opacity-50"
                title="Save the current run as a new migration version"
              >
                {savingVersion ? "Saving…" : "Save version"}
              </button>
            </div>
          ) : null}
        </div>
        {highlightTerms.length > 0 ? (
          <p className="mt-1 truncate text-[10px] text-amber-800">
            Focus: {highlightTerms.join(", ")}
          </p>
        ) : null}
        {ids.length > 1 && context.migrationIds.length === ids.length ? (
          <div className="mt-1.5 flex flex-wrap gap-1" role="tablist" aria-label="Migration runs">
            {ids.map((id, idx) => {
              const active = id === selectedMigrationId;
              const tabLabel = context.fileNames[idx]?.trim() || `Migration ${idx + 1}`;
              return (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  title={id}
                  onClick={() => setSelectedMigrationId(id)}
                  className={`max-w-[9rem] truncate rounded-md px-2 py-0.5 text-[10px] font-medium border transition-colors ${
                    active
                      ? "bg-emerald-600 text-white border-emerald-600"
                      : "bg-white text-slate-600 border-slate-200 hover:border-emerald-300"
                  }`}
                >
                  {tabLabel}
                </button>
              );
            })}
          </div>
        ) : null}
        {/* Only surface a detail line when a SAVED version is picked — the current
            run's id is already visible in the dropdown above. */}
        {sessionId && selectedVersion ? (
          <p className="mt-1 text-[10px] text-slate-500">
            v{selectedVersion.version_no} ·{" "}
            {new Date(selectedVersion.created_at).toLocaleString()}
            {(() => {
              const samesId = selectedVersion.migration_ids?.[0];
              const otherSharing = samesId
                ? versions.filter((x) => x.migration_ids?.[0] === samesId).length - 1
                : 0;
              return otherSharing > 0
                ? ` · shares migration id with ${otherSharing} other version${otherSharing === 1 ? "" : "s"}`
                : "";
            })()}
          </p>
        ) : null}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-2 py-2">
        {context.migrationIds.length > ids.length ? (
          <p className="text-[10px] text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1 mb-2">
            Multiple migration IDs were collapsed to one workbook — all Excel sheets live in that single
            migration (sites, assets, work_orders, etc.).
          </p>
        ) : null}
        {ids.length > 1 ? (
          <p className="text-[10px] text-slate-500 mb-2">
            {ids.length} structured files — select a tab per uploaded file.
          </p>
        ) : null}
        {isLoading && !migration ? (
          <div className="space-y-3 p-2">
            <SkeletonPanel rows={3} />
            <SkeletonPanel rows={5} />
          </div>
        ) : selectedMigrationId ? (
          <MigrationContent
            // Remount cleanly on every migrationId OR viewing-version change.
            // Two reasons:
            //   (1) New file upload → new migrationId → fresh component, fresh
            //       state, fresh storage. Without this, the save effects fire
            //       with OLD state before the rehydrate's setState flushes,
            //       leaking previous-migration snapshots into the new key.
            //   (2) v1 / v2 / v3 of the same migration share migration_id
            //       (Restart from Node 1 rewinds rather than creating a new id).
            //       Without keying on the version pick too, switching the
            //       version dropdown after a restart shows the new live run's
            //       data — not v1's — because the component never remounts.
            key={`${selectedMigrationId}::${pickedVersionId ?? ""}`}
            migration={migration}
            migrationId={selectedMigrationId}
            // When the user picks a saved version from the dropdown, this is
            // its id. MigrationContent reads its snapshots from a version-
            // scoped key instead of the live key, and stops writing back —
            // so reviewing v1 doesn't trample the in-flight v2's progress.
            viewingVersionId={pickedVersionId || undefined}
            onRefresh={handleRefresh}
            onReset={onDismiss ?? handleRefresh}
            showCompletedHistory
            // Collapse completed steps only when reviewing an OLDER saved version; the
            // live/current run (ids[0]) always shows all its steps as they complete.
            collapseCompletedHistory={selectedMigrationId !== (ids[0] ?? "")}
            embeddedRail={embeddedRail}
            drivePipelineSteps
            onFieldFocus={(terms) => setHighlightTerms(terms)}
            onProcessLog={onProcessLog}
          />
        ) : (
          <p className="text-xs text-muted-foreground px-2">No migration session.</p>
        )}
      </div>
    </div>
  );
}
