"use client";

/**
 * Cross-panel "stage link" — connects the LEFT migration step cards to the RIGHT
 * Activity-Log processing-log entries so that scrolling a step card into view on the
 * left automatically reveals ("links to") its matching log entry on the right.
 *
 * Both sides speak the SAME stage vocabulary (the backend processing-log `stage` keys
 * from svc-ai-schema-mapper/src/udr/run_activity.py):
 *   preprocessing · unique_tables · deterministic · semantic · table_prefixing ·
 *   column_within_source · column_canonicalisation · column_to_destination · hierarchy …
 *
 * Wiring:
 *   • LEFT  — each step card carries `data-pipeline-stage="<stage>"`; a scroll-spy
 *             (`usePipelineStageScrollSpy`) links the topmost visible one as you scroll.
 *   • RIGHT — each processing-log row carries `data-stage="<stage>"`; a follower
 *             (`usePipelineLink`) scrolls the matching row into view + flashes it.
 *
 * A tiny module-level store (not React context) so the two panels — which live in
 * SEPARATE rails of the orchestrator shell — stay decoupled: either side just imports
 * this module. `nonce` lets the right side re-fire cheaply; the store only emits when the
 * linked stage actually CHANGES, so a fast scroll never spams the right panel.
 */
import { useEffect, useSyncExternalStore } from "react";

export type PipelineStageLink = { stage: string | null; nonce: number };

let _state: PipelineStageLink = { stage: null, nonce: 0 };
const _listeners = new Set<() => void>();

function _emit() {
  for (const l of _listeners) l();
}

/**
 * Link a stage — called by the LEFT scroll-spy when a new step card becomes the topmost
 * visible one. No-ops on a repeat of the SAME stage so the right panel is only nudged when
 * the active stage genuinely changes (avoids re-scrolling the log on every scroll tick).
 */
export function linkPipelineStage(stage: string | null): void {
  if (!stage || stage === _state.stage) return;
  _state = { stage, nonce: _state.nonce + 1 };
  _emit();
}

/**
 * Force a (re-)link of a stage even when it is already the active one — for EXPLICIT user
 * actions that should re-reveal + re-flash the matching log, e.g. changing a table's routing
 * target in "Step 1 — Confirm table routing" should re-surface the deterministic-mapping flow.
 * (`linkPipelineStage` no-ops on the same stage; this always bumps the nonce.)
 */
export function pulsePipelineStage(stage: string | null): void {
  if (!stage) return;
  _state = { stage, nonce: _state.nonce + 1 };
  _emit();
}

function _subscribe(cb: () => void): () => void {
  _listeners.add(cb);
  return () => {
    _listeners.delete(cb);
  };
}

function _snapshot(): PipelineStageLink {
  return _state;
}

/** Read the current linked stage (with `nonce` so repeat selections still notify). */
export function usePipelineLink(): PipelineStageLink {
  return useSyncExternalStore(_subscribe, _snapshot, _snapshot);
}

// ── Column-mapping gate ─────────────────────────────────────────────────────────────────
// The column-intelligence stages (table prefixing → column similarity → grouping →
// classification → unified names → destination mapping, i.e. B13→B21) are computed by the
// backend at the pre-semantic node, so they'd otherwise show in the Activity Log while the
// user is still on "Step 1 — Confirm table routing". These stages should only surface AFTER
// routing is confirmed (the gate advances to Step 2 — Column matching). The gate publishes
// that state here; the Activity Log hides these stages until it flips true.
export const COLUMN_MAPPING_STAGES: ReadonlySet<string> = new Set([
  "table_prefixing",
  "column_within_source",
  "column_grouping",
  "column_classification",
  "column_canonicalisation",
  "column_metadata",
  "column_to_destination",
]);

// Default HIDDEN (CAFM-001): before any migration center mounts and sets the real value, the
// activity log must NOT lead the panel by showing column-mapping stages the center hasn't revealed
// yet. The center (migration-content) reveals them once the run is past the pre-semantic gate, and
// the gate itself controls them during its Step-1/Step-2 phases; both restore to `true` on unmount.
let _columnStagesRevealed = false;
const _colListeners = new Set<() => void>();

/** Called by the pre-semantic gate: false while on Step 1 (routing), true once routing is
 *  confirmed (Step 2) or the gate unmounts, so column-mapping logs are gated behind confirm. */
export function setColumnStagesRevealed(revealed: boolean): void {
  if (_columnStagesRevealed === revealed) return;
  _columnStagesRevealed = revealed;
  for (const l of _colListeners) l();
}

function _subCol(cb: () => void): () => void {
  _colListeners.add(cb);
  return () => {
    _colListeners.delete(cb);
  };
}

function _snapCol(): boolean {
  return _columnStagesRevealed;
}

/** Read whether column-mapping stages should be shown (true unless the routing gate hid them). */
export function useColumnStagesRevealed(): boolean {
  return useSyncExternalStore(_subCol, _snapCol, _snapCol);
}

// ── Table-mapping gate (deterministic / semantic) ─────────────────────────────────────────
// The center shows the deterministic / semantic table-mapping RESULT only at the pre-semantic
// gate (B9.1/B11.1). During the earlier RUNNING phase the right-rail activity log would emit the
// "Deterministic table mapping" entry as soon as the backend finished node 2 — getting AHEAD of
// the center, which still reads "Deterministic Mapping in progress…". The center hides these
// stages in the log until it has reached the point where it shows them itself.
export const TABLE_MAPPING_STAGES: ReadonlySet<string> = new Set(["deterministic", "semantic"]);

// Default HIDDEN (CAFM-001): same rationale as _columnStagesRevealed — the migration center sets
// the real value on mount (revealed once the deterministic result exists / at a gate / terminal),
// and restores `true` on unmount so non-migration views are unaffected.
let _tableStagesRevealed = false;
const _tblListeners = new Set<() => void>();

/** Set by the migration center: false while the pipeline is RUNNING before the pre-semantic gate
 *  (so the log doesn't show deterministic/semantic ahead of the center), true once the gate is
 *  reached — and everywhere else (completed runs, etc.). */
export function setTableStagesRevealed(revealed: boolean): void {
  if (_tableStagesRevealed === revealed) return;
  _tableStagesRevealed = revealed;
  for (const l of _tblListeners) l();
}

function _subTbl(cb: () => void): () => void {
  _tblListeners.add(cb);
  return () => {
    _tblListeners.delete(cb);
  };
}

function _snapTbl(): boolean {
  return _tableStagesRevealed;
}

/** Read whether table-mapping stages (deterministic/semantic) should be shown in the log. */
export function useTableStagesRevealed(): boolean {
  return useSyncExternalStore(_subTbl, _snapTbl, _snapTbl);
}

/**
 * LEFT-panel scroll-spy. Mount with a ref to the container that holds the step cards; it
 * watches every `[data-pipeline-stage]` descendant and links the one sitting in the top
 * band of the viewport as the user scrolls. Re-scans on DOM changes so it picks up cards
 * that reveal progressively (SequentialReveal) or arrive on later polls.
 */
export function usePipelineStageScrollSpy(
  rootRef: { current: HTMLElement | null },
): void {
  useEffect(() => {
    const root = rootRef.current;
    if (!root || typeof IntersectionObserver === "undefined") return;

    const intersecting = new Set<Element>();
    let raf = 0;
    const flush = () => {
      raf = 0;
      // Among the elements currently in the top band, the TOPMOST (smallest viewport `top`)
      // is the one the user is reading — link its stage. (for..of, not forEach, so TS narrows
      // `bestEl` correctly after the loop instead of collapsing it to `never`.)
      let bestEl: HTMLElement | null = null;
      let bestTop = Number.POSITIVE_INFINITY;
      for (const el of intersecting) {
        const htmlEl = el as HTMLElement;
        const top = htmlEl.getBoundingClientRect().top;
        if (top < bestTop) {
          bestTop = top;
          bestEl = htmlEl;
        }
      }
      const stage = bestEl ? bestEl.dataset.pipelineStage ?? null : null;
      linkPipelineStage(stage);
    };

    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) intersecting.add(e.target);
          else intersecting.delete(e.target);
        }
        if (!raf) raf = requestAnimationFrame(flush);
      },
      // Effective root = a thin band ~8–18% down from the top of the viewport, so the
      // "active" card is the one crossing that line as you scroll (classic scroll-spy).
      { root: null, rootMargin: "-8% 0px -82% 0px", threshold: 0 },
    );

    const observed = new WeakSet<Element>();
    const scan = () => {
      root.querySelectorAll("[data-pipeline-stage]").forEach((el) => {
        if (!observed.has(el)) {
          observed.add(el);
          io.observe(el);
        }
      });
    };
    scan();
    const mo = new MutationObserver(scan);
    mo.observe(root, { childList: true, subtree: true });

    return () => {
      io.disconnect();
      mo.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
  }, [rootRef]);
}
