// ChoiceCards — the option cards a reply offers (logic/choiceCards.js), one button each.
// Used by the chat page and the dock; `compact` is the dock's narrower size.
export default function ChoiceCards({ intro, cards, compact }) {
  const pad = compact ? "10px 11px" : "13px 15px";
  return (
    <div>
      {intro ? <div style={{ marginBottom: compact ? "8px" : "10px" }}>{intro}</div> : null}
      <div style={{ display: "grid", gridTemplateColumns: compact ? "1fr" : "repeat(auto-fit,minmax(210px,1fr))", gap: compact ? "7px" : "10px" }}>
        {(cards || []).map((c) => (
          <button key={c.key} type="button" className="hv13" onClick={c.pick}
            style={{ textAlign: "left", font: "inherit", color: "inherit", cursor: "pointer", padding: pad, borderRadius: "10px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", display: "flex", flexDirection: "column", gap: "6px", minWidth: "0" }}>
            <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <span style={{ width: compact ? "20px" : "24px", height: compact ? "20px" : "24px", borderRadius: "6px", background: "var(--color-accent-900)", color: "var(--color-accent)", display: "inline-flex", alignItems: "center", justifyContent: "center", fontFamily: "ui-monospace,monospace", fontSize: compact ? "10.5px" : "11.5px", flex: "none" }}>{c.n}</span>
              <i className={"ph " + c.icon} style={{ fontSize: compact ? "14px" : "16px", color: "var(--color-accent)" }}></i>
              <span style={{ fontWeight: 600, fontSize: compact ? "11.5px" : "13px" }}>{c.title}</span>
            </span>
            {c.detail ? <span style={{ fontSize: compact ? "10.5px" : "11.5px", color: "var(--color-neutral-400)", lineHeight: "1.45" }}>{c.detail}</span> : null}
            <span style={{ marginTop: "auto", display: "inline-flex", alignItems: "center", gap: "5px", fontSize: compact ? "10.5px" : "11.5px", color: "var(--color-accent)" }}>
              {c.cta}<i className="ph ph-arrow-right" style={{ fontSize: "11px" }}></i>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
