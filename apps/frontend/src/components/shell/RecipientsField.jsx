import React from "react";

// Who else each run's result is emailed to: one chip per address, typed into the box and
// turned into a chip on comma, space or Enter. Backspace on an empty box removes the last one.
export default function RecipientsField({ vals, id }) {
  const list = vals.cronRecipients || [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
      <label htmlFor={id} style={{ fontSize: "12px", color: "var(--color-neutral-400)" }}>
        {"Also email each result to"}
        <span style={{ color: "var(--color-neutral-500)" }}>{" — up to 10 addresses, from admin@hoistra.ai"}</span>
      </label>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", alignItems: "center", padding: "5px 7px", borderRadius: "7px",
        border: "1px solid var(--color-divider)", background: "var(--color-surface)", minHeight: "34px", boxSizing: "border-box" }}>
        {list.map((r) => (
          <span key={r.addr} style={{ display: "inline-flex", alignItems: "center", gap: "5px", padding: "3px 4px 3px 9px", borderRadius: "999px",
            background: "var(--color-accent-900)", border: "1px solid var(--color-accent)", fontSize: "12px", color: "var(--color-text)" }}>
            {r.addr}
            <button type="button" onClick={r.remove} aria-label={"Remove " + r.addr}
              style={{ border: "none", background: "transparent", cursor: "pointer", padding: "0 3px", color: "var(--color-neutral-400)", fontSize: "13px", lineHeight: "1" }}>
              {"×"}
            </button>
          </span>
        ))}
        {vals.cronRecipientsFull ? null : (
          <input id={id} type="email" multiple value={vals.cronRecipientDraft} onChange={vals.cronSetRecipientDraft}
            onKeyDown={vals.cronRecipientKey} onBlur={vals.cronRecipientBlur}
            placeholder={list.length ? "Add another" : "name@company.com, another@company.com"}
            style={{ flex: "1 1 180px", minWidth: "140px", border: "none", outline: "none", background: "transparent",
              fontSize: "12.5px", padding: "4px 2px", color: "var(--color-text)", fontFamily: "var(--font-body)" }} />
        )}
      </div>
    </div>
  );
}
