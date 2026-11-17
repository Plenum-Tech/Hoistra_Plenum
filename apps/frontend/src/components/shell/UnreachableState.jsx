// UnreachableState — one way for every page to say its data could not be read (17 Nov 2026, UI
// audit: "backend unreachable" was drawn four different ways and the Hoist Graph page was blank).
//
// The panel Vendors and Maintenance already used, made shared: what could not be read, in plain
// words; that nothing is shown in its place (never a 0 that claims "none"); the server's own
// message small underneath; and Retry. While a read is in flight it says so instead.
//
//   show     "block" | "none" (or a boolean) — the views keep their display-string convention
//   title    "Maintenance records couldn't be loaded"
//   note     the plain sentence under it
//   detail   the server's message, shown small (optional)
//   retryShow / onRetry   the Retry button (hidden while reading)
import React from 'react';

//   icon     a Phosphor class; plugs by default (could not be read), a spinner while reading
export default function UnreachableState({ show, title, note, detail, retryShow, onRetry, style, icon }) {
  const visible = show === true || show === "block" || show === "flex";
  if (!visible) return null;
  const canRetry = typeof onRetry === "function" && retryShow !== false && retryShow !== "none";
  return (
    <div role="status" style={{ marginTop: "16px", padding: "24px 22px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", ...(style || {}) }}>
      <div style={{ display: "flex", alignItems: "center", gap: "9px", fontSize: "14px" }}>
        <i className={"ph " + (icon || "ph-plugs")} style={{ fontSize: "16px", color: "var(--color-neutral-500)" }} aria-hidden="true"></i>
        <span>{title}</span>
      </div>
      {note ? (
        <p style={{ fontSize: "12.5px", color: "var(--color-neutral-400)", margin: "8px 0 0", maxWidth: "76ch", lineHeight: "1.55" }}>
          {note}
        </p>
      ) : null}
      {detail ? (
        <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "6px", fontFamily: "ui-monospace,monospace", overflowWrap: "anywhere" }}>
          {"Details: " + detail}
        </div>
      ) : null}
      {canRetry ? (
        <button type="button" className="hv4" onClick={onRetry}
          style={{ font: "inherit", display: "inline-flex", alignItems: "center", gap: "6px", marginTop: "14px", fontSize: "12px", padding: "6px 12px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", cursor: "pointer" }}>
          <i className="ph ph-arrow-clockwise" aria-hidden="true"></i>
          {"Retry"}
        </button>
      ) : null}
    </div>
  );
}
