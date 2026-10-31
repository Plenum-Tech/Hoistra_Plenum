// openuiView — the OpenUI view an orchestrator answer carries beside its Markdown.
//
// svc-deepagents (agents/openui_view.py) keeps `answer` Markdown for every reader of the text and
// sends the program the model wrote as one more card payload in the turn's tool calls:
// { tool: "openui_view", output: { v: 1, lang, tail? } }. `tail` is a note a later check added
// under the answer (drawn as Markdown beneath the view). The same entry comes back on a reopened
// thread (services/chat_threads.py keeps it beside the dashboard cards).
//
// 27 Oct 2026: an answer the server lays out itself (agents/answer_presenter.py) no longer waits
// for its layout. The view comes after the answer, on the same socket, as one `answer_view` event
// {session_id, view: {v, lang, tail?} | null, cost}; viewFromEvent reads that `view` into the same
// shape, so a laid-out answer and a general-loop answer that wrote its own are drawn alike.

const TOOL = 'openui_view';

// { v, lang, tail? } -> { lang, tail }, or null for anything this build cannot draw.
function readView(o) {
  if (!o || typeof o !== 'object' || Array.isArray(o)) return null;
  if (o.v !== undefined && o.v !== 1) return null; // a later format this build cannot draw
  if (typeof o.lang !== 'string' || !o.lang.trim()) return null;
  return { lang: o.lang, tail: typeof o.tail === 'string' ? o.tail : '' };
}

export function extractOpenUIView(toolCalls) {
  const hit = (Array.isArray(toolCalls) ? toolCalls : []).find((t) => t && typeof t === 'object' && t.tool === TOOL);
  return readView(hit && hit.output);
}

// The `view` of an `answer_view` event. null — the layout was not sent — and a malformed one are
// both no view: the answer keeps drawing as it was.
export function viewFromEvent(view) {
  return readView(view);
}

// The names of the tools behind an answer, as the bubble lists them. The view is how the answer
// is drawn, not work the orchestrator did, so it is not one of them.
export function callNames(calls) {
  return (Array.isArray(calls) ? calls : [])
    .map((t) => (typeof t === 'string' ? t : t && t.tool))
    .filter((n) => typeof n === 'string' && n && n !== TOOL);
}
