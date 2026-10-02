// api/docRag — opening a document.
//
// A document is served by svc-deepagents at GET /api/documents/{id}/download, and only through a
// SIGNED link: POST /api/documents/{id}/link checks the signed-in caller may see it (their
// company and buildings; a personal one, admins only and logged) and returns a link valid for
// five minutes. The bare /download URL - and every one already sitting in an old chat answer -
// is refused on its own (2 Oct 2026: it had answered anyone, and redirected to a public blob).
//
// So nothing builds a /download URL to hand to the browser any more. `openDocument(id)` asks for
// the link and opens it; `documentIdFromHref` recognises a document link in an answer (old or
// new, `doc:<id>` or a /download URL) so the chat can open it the same way.
import { BASES, apiFetch } from './client.js';

const ID = '([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})';
const HREF_RES = [new RegExp('^doc:' + ID + '$'), new RegExp('/api/documents/' + ID + '/download(?:[?#].*)?$')];

export function documentIdFromHref(href) {
  const h = String(href || '').trim();
  for (const re of HREF_RES) {
    const m = re.exec(h);
    if (m) return m[1];
  }
  return null;
}

export const documentLink = (documentId) =>
  apiFetch(BASES.deepAgents, '/api/documents/' + encodeURIComponent(documentId) + '/link', { method: 'POST', timeoutMs: 20000 });

// Opens the tab synchronously (inside the click, so no popup blocker stops it), then points it at
// the signed link once it arrives. A refusal closes the tab and reports why.
export async function openDocument(documentId, onError) {
  const tab = typeof window !== 'undefined' ? window.open('', '_blank') : null;
  try {
    const out = await documentLink(documentId);
    const url = BASES.deepAgents + ((out && out.url) || '');
    if (tab) { tab.opener = null; tab.location.href = url; } else if (typeof window !== 'undefined') window.location.href = url;
  } catch (e) {
    if (tab) tab.close();
    if (onError) onError(e);
  }
}
