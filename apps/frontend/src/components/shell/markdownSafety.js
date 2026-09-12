// markdownSafety — which link targets Markdown.jsx is willing to render as a real <a href>.
//
// The text Markdown.jsx renders comes from the orchestrator's answers, which reason over
// ingested documents (vendor PDFs, certificates, uploads) — so the content chain starts
// outside the organisation, not from a trusted author. React does not block a javascript:
// href; it renders it, and a click executes it in this app's own origin, which proxies
// /backend/* to services that, per buildingsCrud.js's own comment, "do not authorise these
// routes". One poisoned document is then one click from an unauthenticated write. Anything
// other than a confirmed http(s), mailto, same-origin-relative or in-page anchor link is
// refused, and the text renders plain rather than as a clickable link.
const SAFE_SCHEME = /^(https?:|mailto:|\/|#)/i;

export function isSafeHref(href) {
  return SAFE_SCHEME.test(String(href == null ? '' : href).trim());
}
