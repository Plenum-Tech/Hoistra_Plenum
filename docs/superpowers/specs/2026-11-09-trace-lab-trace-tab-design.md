# Trace Lab: a Langfuse-style "Trace" tab on the question page

Date: 9 Nov 2026 · Asked for by Hussain (for Aasim and the reviewers) · Status: design, awaiting approval

## Why

Since 9 Nov every chat turn is also sent to the self-hosted Langfuse (`svc-deepagents/src/agents/langfuse_export.py`).
Reviewers like the way Langfuse shows one answer: a nested tree of every step on the left, and the selected step's
input, output and metadata on the right. Trace Lab's question page shows the same answer as a flat list of coloured
blocks built for writing notes, so finding "what did this tool get and return" means scrolling and reading JSON.

Hussain wants **exactly the Langfuse view** to open inside Trace Lab when he clicks a question: same tree, same
input/output, same numbers, so a reviewer never has to leave Trace Lab or hunt for the trace in Langfuse.

Success: open a question in Trace Lab, click **Trace**, and see the same tree, timings, costs, inputs and outputs that
Langfuse shows for that turn.

## Goals

1. A **Trace** tab next to the current answer view on the question page (`apps/frontend/src/lab/QuestionPage.jsx`).
   The current view (blocks, notes, expected answer, labels) stays exactly as it is, under its own tab.
2. **Header** (as Langfuse): turn id, total time, total cost, total tokens, session, user email, and chips for
   route and models. A failed turn shows its error. (No instance chip: Hoist Traces' turn rows don't store the
   instance; Langfuse gets it from the live chat process at send time.)
3. Three views, switched like Langfuse's **Tree / Timeline / Graph**:
   - **Tree**: every step nested under its parent: `chat` → `router`, `plan: 1 step`, agent (`maintenance`) →
     `model`, tool (`answer_from_records`) → `count`, `focus WorkOrder`, `include …`. Each row: type icon, name,
     time, cost (when there is one). Collapsible. Failed steps in red.
   - **Timeline**: one bar per step on a shared time axis from the turn's start, indented by depth, bar length =
     the step's time. Click a bar to select the step.
   - **Graph**: the steps as a flow of agents, model calls and tools with arrows in the order they ran. Click a node
     to select its step.
4. **Detail pane** for the selected step (the root is selected on open): type icon + name, start time to the
   millisecond, time, cost, model; tabs **Preview** (Input, Output, each collapsible) and **Metadata**; a
   **Formatted / Raw** switch (Formatted renders text and tables readably, Raw shows the JSON).
5. The tree, inputs and outputs follow **the same rules as the Langfuse export**, so the two never disagree:
   - a `router`/`llm` step is a model call (generation) only when it has a model, tokens or cost;
   - agent and turn steps are agents, tool steps are tools, everything else is a plain step;
   - every step has an input and an output, filled the way `langfuse_export.filled_io` fills them, and the
     metadata says where a filled value came from (`input_from` / `output_from`);
   - text cut by Hoist Traces' 16,000-character limit says so.

## Non-goals (this version)

- Notes and labels inside the Trace tab. They stay in the existing answer view.
- Reading anything from Langfuse. The data comes from Hoist Traces, which Trace Lab already reads.
- Sessions, scores, annotations, or a dark theme.
- Any change to the chat app, `svc-deepagents`, the main frontend, or Trace Lab's existing endpoints and components'
  behaviour.

## Anti-goals (failure even if it "works")

- Anything in the existing answer view, notes or labelling changing behaviour (EVAL GOLDEN RULE).
- Showing data Trace Lab's current view would mask: inputs/outputs must pass through the same redaction
  (`normalize.redact` / `redact_text`).
- A reviewer seeing a turn from a company they can't already see on the question page.
- Any database write, or slowing the question page down: the tab loads its data only when opened.
- The tree disagreeing with Langfuse for the same turn.

## Constraints

- Trace Lab is a separate module; work stays inside `svc-trace-lab/trace_lab/` and `apps/frontend/src/lab/`.
  Importing from `src.agents.langfuse_export` and `src.agents.trace` is fine (Trace Lab already imports
  `src.agents.trace`); **editing** them is not.
- Read-only access to the main database, through the same read path `conversations.get_turn` uses.
- No new npm or Python dependencies. `lucide-react` is already a frontend dependency (icons).
- Tests first (frontend: `node --test` in `src/lab/__tests__/`; backend: pytest in `svc-trace-lab/tests/`).
- Nothing is committed or pushed until Hussain asks.

## How it works (decided)

### Data: one new read-only endpoint

`GET /lab/q/{code}/tree?turn=<turn_id>` (in `routes_runs.py`, logic in a new module, e.g. `trace_tree.py`).

Access is the same as `GET /lab/q/{code}`: the caller must be an active member; the question is read with
`_question_row`; `turn` must be one of that question's linked answers (`tracelab.lab_question_traces`), defaulting to
the newest; the company view comes from `labelling.for_org(...)` exactly as `runs.get_question` does it. A turn the
caller can't see gives the same answer `get_question` gives (`missing`), never its data.

The turn row and its spans are read as `conversations.get_turn` reads them (read-only), then built into:

```
{ "ok": true,
  "turn": { "turn_id", "langfuse_trace_id", "session_id", "email", "route", "models": [...],
            "started_at", "ended_at", "latency_ms", "cost_usd", "cost_complete",
            "input_tokens", "output_tokens", "cache_read_tokens", "total_tokens", "ok", "error" },
  "nodes": [ { "id", "parent_id", "depth", "seq", "kind", "type": "agent|generation|tool|span",
               "name", "agent", "model", "started_at", "ended_at", "latency_ms", "cost_usd",
               "input_tokens", "output_tokens", "cache_read_tokens", "ok", "error", "level": "DEFAULT|ERROR",
               "input", "output", "filled": { "input_from"?, "output_from"? }, "truncated": bool } ] }
```

- `nodes` are in depth-first order (parent before children, siblings by `seq`), one root (`chat`, input = question,
  output = answer). A missing turn span gets a synthesized root; steps whose parent is missing hang from the root, as
  in `langfuse_export.otel_spans_for`.
- `type`, `input`/`output` and `filled` come from the export's own helpers (`_is_generation`, `OBSERVATION_TYPES`,
  `filled_io`, `_shown`) so the tab and Langfuse match by construction; then redacted.
- `langfuse_trace_id` = `langfuse_export.otel_trace_id(turn_id)`, shown in Metadata so a reviewer can find the same
  trace in Langfuse.

### Frontend

- `QuestionView` gets two tabs above the answer area: **Answer** (today's view, unchanged, default) and **Trace**.
  The choice is kept in the URL (`qtab=trace`) so a shared link opens the Trace tab, and remembered per browser
  (see "Calls I made").
- New `TraceTab.jsx` + `traceTab.css`; pure helpers (tree from nodes, timeline positions, graph layout, number and
  time formatting) in `logic/traceTree.js` so they can be tested without a browser.
- `labApi.qTree(code, turn)` calls the endpoint; loaded when the tab first opens and when the active answer changes.
- Icons from `lucide-react`, matching Langfuse's meaning: agent = bot, model call = sparkles, tool = wrench,
  step = left-right arrow.

## Left to the builder

- Exact layout of the Graph view (SVG, no library), as long as it reads as Langfuse's: agents, model calls and tools
  as nodes, arrows in run order, click selects.
- How Formatted renders text: reuse a Markdown/table renderer already in `apps/frontend/src` if one exists without
  changing it; otherwise readable pre-wrapped text, with JSON as an indented key/value view.
- A name search box above the tree (Langfuse has one): include it if cheap.
- Loading, empty ("This answer has no recorded steps") and error states in Trace Lab's existing style.

## Calls I made (Claude), for Hussain to check

- Trace Lab's light look with Langfuse's layout, not Langfuse's dark theme.
- The tab is **Answer | Trace**. Answer is the default the first time; after that the browser remembers the last tab
  chosen (localStorage, wrapped in try/catch), so once you pick Trace every question you click opens on Trace.
  `qtab=trace` in the URL wins over the remembered choice.
- The Trace tab follows the answer picked under "Supporting answers" (Answer 1, Answer 2…).
- Data comes from Hoist Traces, not from Langfuse, so it works locally and for turns Langfuse never received.
- The same redaction as the existing view, even where Langfuse shows the raw value. "Matches Langfuse" means the
  same steps, nesting, types, times, costs, tokens and where-filled-from notes; a value Trace Lab masks stays masked.
- No size cap beyond Hoist Traces' own 16,000 characters per value (a typical answer has 25 to 60 steps).
- The backend change needs the Trace Lab container rebuilt locally (it must contain today's `langfuse_export.py`);
  the frontend change is picked up by the :5174 dev server.
