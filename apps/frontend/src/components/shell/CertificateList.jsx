// CertificateList — certificates as rows of three checks (Document, Register, Expiry), paged
// from the top. The Compliance building vault and a vendor's Accreditations tab on the Vendors
// page both draw it, from the same row model (logic/compliance.js ccCertRowVals), so the two
// can never disagree about a certificate.
import React, { useState } from 'react';

// Certificates per page. Rows arrive most urgent first (lapsed, then by expiry), so page 1 is
// always the ones that need someone; the pager sits above the list, never below it.
const PAGE = 4;

// Page buttons for a pager: every page while there are few, otherwise the first, the last and
// the current page's neighbours, with a gap marker between runs.
function pageList(count, at) {
  if (count <= 7) return Array.from({ length: count }, (_, i) => i);
  const keep = new Set([0, count - 1, at - 1, at, at + 1].filter((i) => i >= 0 && i < count));
  const out = [];
  Array.from(keep).sort((a, b) => a - b).forEach((i, j, all) => {
    if (j && i - all[j - 1] > 1) out.push("gap" + i);
    out.push(i);
  });
  return out;
}

const PAGER_BTN = { display: "inline-flex", alignItems: "center", justifyContent: "center", minWidth: "26px", height: "26px", padding: "0 7px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", fontFamily: "inherit", fontSize: "11.5px", fontVariantNumeric: "tabular-nums", color: "var(--color-neutral-400)", cursor: "pointer" };

// rows     — row models from ccCertRowVals
// pageKey  — whose list this is (a building, a vendor); a new key starts on page 1
// intro    — the line above the rows, beside the pager
// emptyText — said when there are no rows
export default function CertificateList({ rows, pageKey, intro, emptyText }) {
  const [pageState, setPageState] = useState({ for: null, at: 0 });
  const list = rows || [];
  const pages = Math.max(1, Math.ceil(list.length / PAGE));
  const at = pageState.for === pageKey ? Math.min(pageState.at, pages - 1) : 0;
  const shown = list.slice(at * PAGE, at * PAGE + PAGE);
  const go = (i) => setPageState({ for: pageKey, at: Math.max(0, Math.min(pages - 1, i)) });
  return (
    <div>
      <div style={{ padding: "10px 16px", borderBottom: "1px solid var(--color-divider)", display: list.length ? "flex" : "none", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "8px 16px" }}>
        <span style={{ flex: "1 1 260px", fontSize: "11.5px", lineHeight: "1.45", color: "var(--color-neutral-500)" }}>
          {intro}
        </span>
        {pages > 1 ? (
          <nav aria-label="Certificate pages" style={{ display: "flex", alignItems: "center", gap: "4px", flexShrink: 0 }}>
            <span style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", fontVariantNumeric: "tabular-nums", marginRight: "6px", whiteSpace: "nowrap" }}>
              {(at * PAGE + 1) + "–" + Math.min(list.length, at * PAGE + PAGE) + " of " + list.length}
            </span>
            <button type="button" className={at > 0 ? "hv6" : undefined} onClick={() => go(at - 1)} disabled={at === 0} aria-label="Previous page"
              style={{ ...PAGER_BTN, padding: "0", color: at === 0 ? "var(--color-ink-decorative)" : PAGER_BTN.color, cursor: at === 0 ? "default" : "pointer" }}>
              <i className="ph ph-caret-left" aria-hidden="true" style={{ fontSize: "12px" }}></i>
            </button>
            {pageList(pages, at).map((i) => typeof i === "string" ? (
              <span key={i} aria-hidden="true" style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", padding: "0 2px" }}>{"…"}</span>
            ) : (
              <button key={i} type="button" className={i === at ? undefined : "hv6"} onClick={() => go(i)} aria-label={"Page " + (i + 1)} aria-current={i === at ? "page" : undefined}
                style={{ ...PAGER_BTN, borderColor: i === at ? "var(--color-accent)" : "var(--color-divider)", background: i === at ? "var(--color-accent-900)" : PAGER_BTN.background, color: i === at ? "var(--color-accent-600)" : PAGER_BTN.color, fontWeight: i === at ? 600 : 400, cursor: i === at ? "default" : "pointer" }}>
                {String(i + 1)}
              </button>
            ))}
            <button type="button" className={at < pages - 1 ? "hv6" : undefined} onClick={() => go(at + 1)} disabled={at >= pages - 1} aria-label="Next page"
              style={{ ...PAGER_BTN, padding: "0", color: at >= pages - 1 ? "var(--color-ink-decorative)" : PAGER_BTN.color, cursor: at >= pages - 1 ? "default" : "pointer" }}>
              <i className="ph ph-caret-right" aria-hidden="true" style={{ fontSize: "12px" }}></i>
            </button>
          </nav>
        ) : null}
      </div>
      {shown.map((c, $index) => (
        <React.Fragment key={$index}>
          <div className="cc-cert" style={{ padding: "11px 16px 12px", borderBottom: c.hvOpen ? "none" : "1px solid var(--color-divider)", display: "flex", flexDirection: "column", gap: "8px" }}>
            {/* The certificate: what it is and who issues it, with its action on the right. */}
            <div style={{ display: "flex", alignItems: "flex-start", flexWrap: "wrap", gap: "8px 12px" }}>
              <div style={{ flex: "1 1 220px", minWidth: "0" }}>
                <div title={c.nm} style={{ fontSize: "13px", fontWeight: 500, lineHeight: "1.35", color: "var(--color-text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {c.nm}
                </div>
                {c.issuer ? (
                  <div title={c.issuer} style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {c.issuer}
                  </div>
                ) : null}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", flexShrink: 0 }}>
                {c.dlShow ? (
                  <span className="hv23" onClick={c.dl} title={c.dlTitle} aria-label={c.dlLabel} role="button" style={{ display: "flex", alignItems: "center", justifyContent: "center", width: "28px", height: "28px", borderRadius: "6px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: c.dlBusy ? "wait" : "pointer" }}>
                    <i className={c.dlBusy ? "ph ph-circle-notch" : "ph ph-download-simple"} style={{ fontSize: "14px" }}></i>
                  </span>
                ) : null}
                <span className="hv23" onClick={c.act} role="button" style={{ display: "flex", alignItems: "center", height: "28px", fontSize: "11.5px", padding: "0 12px", borderRadius: "6px", border: `1px solid ${c.actBorder}`, color: c.actFg, cursor: "pointer", whiteSpace: "nowrap" }}>
                  {c.actLabel}
                </span>
              </div>
            </div>
            {/* The three checks, side by side: the document itself, the public register,
                and the expiry. A check that needs someone sits on its status tint. */}
            <div role="list" aria-label={"Checks on " + c.nm} className="cc-checks">
              {(c.checks || []).map((k) => (
                <div role="listitem" key={k.key} title={k.title} style={{ background: k.bg, padding: "9px 12px 10px", display: "flex", flexDirection: "column", gap: "3px", minWidth: "0" }}>
                  <span style={{ fontSize: "9.5px", letterSpacing: "0.08em", textTransform: "uppercase", color: k.detailFg }}>
                    {k.label}
                  </span>
                  <span style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12.5px", fontWeight: 500, lineHeight: "1.3", color: k.fg, minWidth: "0" }}>
                    <i className={"ph " + k.icon} aria-hidden="true" style={{ fontSize: "15px", flexShrink: 0, color: k.iconFg }}></i>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {k.status}
                    </span>
                  </span>
                  <span style={{ fontSize: "11px", lineHeight: "1.4", color: k.detailFg, fontVariantNumeric: "tabular-nums" }}>
                    {k.detail}
                  </span>
                  {k.key === "reg" ? (
                    <span style={{ display: "flex", alignItems: "center", gap: "5px 14px", flexWrap: "wrap", marginTop: "5px" }}>
                      {c.verifyUrl ? (
                        <a className="hv6" href={c.verifyUrl} target="_blank" rel="noopener noreferrer" title={c.verifyTitle} style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "11.5px", fontWeight: 500, color: "var(--color-accent)", textDecoration: "none", whiteSpace: "nowrap" }}>
                          {"Verify on register"}
                          <i className="ph ph-arrow-square-out" aria-hidden="true" style={{ fontSize: "12px" }}></i>
                        </a>
                      ) : (
                        <span title={"This certificate type has no public register in the country pack"} style={{ fontSize: "11px", color: k.detailFg, whiteSpace: "nowrap" }}>
                          {"No public register"}
                        </span>
                      )}
                      {c.hvShow ? (
                        <span className="hv6" onClick={c.hvToggle} title={c.hvTitle} role="button" aria-expanded={c.hvOpen} style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "11.5px", color: c.hvOpen ? "var(--color-text)" : "var(--color-neutral-400)", cursor: "pointer", whiteSpace: "nowrap" }}>
                          <i className="ph ph-pencil-simple-line" aria-hidden="true" style={{ fontSize: "12px" }}></i>
                          {c.hvLabel === "Re-check" ? "Re-check result" : "Record result"}
                        </span>
                      ) : null}
                    </span>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
            {c.hvOpen ? (
            <div role="group" aria-label={"Record the register result for " + c.nm} style={{ padding: "12px 16px 14px", borderBottom: "1px solid var(--color-divider)", background: "var(--color-bg)", fontSize: "11.5px", display: "flex", flexDirection: "column", gap: "9px" }}>
              <span style={{ fontSize: "12.5px", fontWeight: 500, color: "var(--color-text)" }}>
                {"What did the register show?"}
              </span>
              <span style={{ color: "var(--color-neutral-500)", lineHeight: "1.5", maxWidth: "70ch" }}>
                {c.hvHelp}
                {c.verifyUrl ? (
                  <>
                    {" "}
                    <a className="hv6" href={c.verifyUrl} target="_blank" rel="noopener noreferrer" style={{ display: "inline-flex", alignItems: "center", gap: "3px", color: "var(--color-accent)", textDecoration: "none", fontWeight: 500 }}>
                      {"Open the register"}
                      <i className="ph ph-arrow-square-out" aria-hidden="true" style={{ fontSize: "12px" }}></i>
                    </a>
                  </>
                ) : null}
              </span>
              <span style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                {c.hvOutcomes.map((o, $i) => (
                  <span key={$i} className="hv23" onClick={o.pick} style={{ padding: "4px 10px", borderRadius: "6px", border: `1px solid ${o.border}`, color: o.fg, cursor: "pointer", whiteSpace: "nowrap" }}>
                    {o.label}
                  </span>
                ))}
              </span>
              <input value={c.hvNote} onChange={c.hvNoteSet} placeholder={c.hvPlaceholder} style={{ padding: "6px 9px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface, transparent)", color: "inherit", fontSize: "11.5px" }} />
              {c.hvError ? (
                <span style={{ color: "var(--st-risk)" }}>
                  {c.hvError}
                </span>
              ) : null}
              <span style={{ display: "flex", gap: "8px" }}>
                <span className="hv23" onClick={c.hvSave} style={{ padding: "4px 12px", borderRadius: "6px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: c.hvBusy ? "wait" : "pointer" }}>
                  {c.hvBusy ? "Saving…" : "Save result"}
                </span>
                <span className="hv23" onClick={c.hvCancel} style={{ padding: "4px 12px", borderRadius: "6px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
                  {"Cancel"}
                </span>
              </span>
            </div>
          ) : null}
        </React.Fragment>
      ))}
      <div style={{ padding: "14px", fontSize: "11.5px", color: "var(--color-neutral-500)", display: list.length ? "none" : "block" }}>
        {emptyText}
      </div>
    </div>
  );
}
