// Sessions — every conversation with the orchestrator and every orchestrator task, from the
// navigator's "All sessions": the whole history, read page by page from the server's threads
// and cached in this browser (logic/sessions.js). Searchable; the Spaces panel beside the list
// filters it and takes drops — drag a session, or the ones ticked, onto a space to file them
// there (7 Oct 2026). `vals` is the view model from useHoistra(); the page reads vals.sessionsPage.
import React from 'react';
import SessionList, { SpaceTargets } from '../components/shell/SessionList.jsx';

export default function Sessions({ vals }) {
  const p = vals.sessionsPage || { chips: [], groups: [] };
  return (
    <div style={{ flex: "1", display: "flex", justifyContent: "center", padding: "0 32px 80px" }}>
      <div style={{ width: "100%", maxWidth: "1180px", animation: "fadeUp 0.28s ease both" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "24px 0 0" }}>
          <div className="hv6" onClick={vals.goHome} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
            <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>
            <span>{"Home"}</span>
          </div>
          <span style={{ fontSize: "12px", color: "var(--color-neutral-600)" }}>{"/"}</span>
          <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Sessions"}</span>
        </div>

        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: "24px", marginTop: "18px" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: "7px" }}>
            <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>{"Sessions"}</div>
            <h1 style={{ fontSize: "31px", margin: "0", lineHeight: "1.1" }}>{"Every conversation, in order"}</h1>
            <div style={{ fontSize: "12.5px", color: "var(--color-neutral-400)", maxWidth: "76ch", lineHeight: "1.5" }}>{p.count}</div>
          </div>
          <div className="btn btn-primary" onClick={p.newQuery} style={{ fontSize: "12px", padding: "7px 13px", cursor: "pointer", whiteSpace: "nowrap", flexShrink: "0" }}>
            {"New query"}
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "11px", padding: "10px 14px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", marginTop: "20px" }}>
          <i className="ph ph-magnifying-glass" style={{ fontSize: "15px", color: "var(--color-neutral-500)", flexShrink: "0" }}></i>
          <input className="input" value={p.query} onChange={p.setQuery} placeholder="Search sessions…" style={{ flex: "1", minWidth: "0", background: "transparent", border: "none", outline: "none", fontFamily: "var(--font-body)", fontSize: "13.5px", color: "var(--color-text)" }} />
        </div>
        <div className="ss-layout">
          <div style={{ minWidth: "0" }}>
            <SessionList groups={p.groups} empty={p.empty} emptyText={p.emptyText} spaces={(p.spaces || []).filter((t) => !t.all && !t.noDrop)} onFile={p.fileMany} onDelete={p.deleteMany} />
          </div>
          <SpaceTargets spaces={p.spaces} unfiledCount={p.unfiledCount} onFile={p.fileMany} newSpace={p.newSpace} />
        </div>
      </div>
    </div>
  );
}
