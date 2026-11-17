// plainErrors — a failed request as a short plain phrase (17 Nov 2026, UI audit).
//
// Pages used to append the backend's own message to their sentence ("The jobs could not be read:
// HTTP 500 Internal Server Error: psycopg…"). The phrase says what happened in words a facilities
// manager can act on; the raw message stays available as `detail`, shown small or on hover.
// Lower-case, no full stop, so it reads inside a sentence: "The jobs could not be read — <text>."
import { scrubInternal } from './publicText.js';

export function plainFailure(e) {
  const msg = e == null ? "" : typeof e === "string" ? e : String(e.message || e);
  // The status, or the one a stored message carries ("404 Not Found", "HTTP 503") when only
  // the message was kept.
  const coded = /\b(?:HTTP\s*)?([45]\d\d)\b/.exec(msg);
  // Or the standard reason phrase alone ("Service Unavailable"), which is all some loaders keep.
  const PHRASE = { "bad gateway": 502, "service unavailable": 503, "gateway timeout": 504, "not found": 404,
    "unauthorized": 401, "forbidden": 403, "too many requests": 429 };
  const phrase = Object.keys(PHRASE).find((p) => msg.toLowerCase().indexOf(p) > -1);
  const st = e && typeof e === "object" && typeof e.status === "number" ? e.status
    : coded ? Number(coded[1]) : phrase ? PHRASE[phrase] : null;
  let text;
  if (/timed out/i.test(msg)) text = "the server didn't answer in time";
  else if (st === 502 || st === 503 || st === 504 || /ECONNREFUSED|ENOTFOUND|network|failed to fetch/i.test(msg)) text = "the server can't be reached right now";
  else if (st === 404) text = "that service isn't available right now";
  else if (st === 401) text = "you were signed out — sign in again";
  else if (st === 403) text = "your account doesn't have access to this";
  else if (st === 429) text = "the server is busy — try again in a moment";
  else text = "something went wrong on the server";
  return { text: text, detail: scrubInternal(msg) };
}
