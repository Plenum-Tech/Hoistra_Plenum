// host — what an OpenUI answer needs from the chat around it, and the loader for its code.
//
// OpenUIHostContext carries the chat's own actions (ask a question in this conversation, open a
// page) to every answer without threading them through each bubble's props, so the bubbles can
// stay memoised on their text alone.
//
// The chat's host (renderVals `openuiHost`) also has email({ to, cc, subject, body }): it opens
// that draft in the app's email card to edit and Approve & send (logic/emailHistory.js
// emOpenDraft, 28 Oct 2026). It is optional — the default below has none, and a component checks
// `typeof host.email === 'function'` before offering it — so an answer drawn anywhere else (the
// harness, a report) never shows a control that has nothing behind it.
//
// The renderer and its components are one lazily loaded chunk. preloadOpenUI starts fetching it
// early (on idle after sign-in, and on every question sent), so by the time an answer streams in
// the code is already there and nothing flashes.
import { createContext } from 'react';

export const OpenUIHostContext = createContext({ ask() {}, open() {} });

let loading = null;
export function preloadOpenUI() {
  if (!loading) {
    loading = import('./OpenUIAnswer.jsx').catch((e) => {
      loading = null; // a later call tries again (a stale tab after a deploy, a dropped connection)
      throw e;
    });
  }
  return loading;
}
