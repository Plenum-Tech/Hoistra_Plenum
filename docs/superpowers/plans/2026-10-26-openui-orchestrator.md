# OpenUI Orchestrator Answers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The orchestrator writes its own final answers as OpenUI Lang. The chat renders them live as Hoistra-styled UI while they stream, with no lag. Every other reader of the answer still gets Markdown.

**Architecture:**
- The general-loop model gets an "Answer format" prompt section. That section is generated from the frontend's OpenUI component library.
- The server parses the program with a Python port of the OpenUI Lang subset and writes a Markdown rendition into `answer`.
- The program travels as a card payload: `{"tool":"openui_view","output":{"v":1,"lang":…,"tail":…}}`.
- The frontend renders the payload with `@openuidev/react-lang` and Hoistra components. It renders live from the `answer_delta` stream. Markdown answers fall back to `AnswerCards`.

**Tech Stack:** `@openuidev/react-lang` 1.0.1, zod 4 (exact pin), React 18.3, Vite 5, plain JSX. Python 3.12, LangGraph. Frontend tests run on node:test. Backend tests run with pytest inside the `hoistra-testenv-svc-deepagents` image.

**Spec:** this document. Hussain chose architecture A on 26 Oct 2026, then said "do it better and start". The Design section below records the calls made for him. Background: `memory/openui-orchestrator.md`.

## Design (decided)

**Look: "briefing dashboard".** This combines the three mockups.
- B's strong headline, with key figures marked by the reserved `--marker-tint` highlighter.
- A's tiles and cards. They use the `cardStyles.js` family (LABEL, CARD, tone stripe, mono values).
- B's numbered steps instead of heavy action cards.
- C's collapsible sections for long detail.
- Charts are hand-drawn SVG measured to their container, so text stays crisp in the dock. There is no chart library.
- Motion: one 180 ms fade when a block mounts, and nothing on later stream updates.
- Both themes use tokens only. There are no new colours.

**Component library.** Root is `Answer`. Props are positional in this order.

| Component | Props | Markdown rendition |
|---|---|---|
| `Answer` | `children: Block[]` | blocks joined by blank lines |
| `Headline` | `text: string` | paragraph |
| `Text` | `markdown: string` | as is |
| `Metrics` | `items: Metric[]` | one bullet per metric |
| `Metric` | `label: string, value: string\|number, note?: string, tone?: "risk"\|"warn"\|"ok"\|"neutral"` | `- {label}: {value}` + ` — {note}` |
| `Table` | `columns: string[], rows: (string\|number)[][], caption?: string` | pipe table + `_caption_` |
| `BarChart` | `title: string, labels: string[], values: number[], unit?: string` | `**title**` + 2-column table |
| `LineChart` | `title: string, labels: string[], series: Series[], unit?: string` | `**title**` + table, one column per series |
| `Series` | `name: string, values: number[]` | — |
| `Callout` | `tone: "risk"\|"warn"\|"ok"\|"info", text: string, title?: string` | `**{title}:** {text}` or `{text}` |
| `Details` | `title: string, fields: Field[], subtitle?: string` | `**title** — subtitle` + `- label: value` |
| `Field` | `label: string, value: string\|number` | — |
| `Section` | `title: string, children: Block[], summary?: string, collapsed?: boolean` | `## title` + summary + children |
| `Steps` | `items: Step[]` | `1. **title** — detail` |
| `Step` | `title: string, detail?: string, action?: Button` | — |
| `Buttons` | `items: Button[]` | omitted |
| `Button` | `label: string, action?: Action, variant?: "primary"\|"secondary"` | omitted |
| `FollowUps` | `questions: string[]` | omitted |
| `EmailDraft` | `subject: string, to: string, body: string, cc?: string` | the mandatory fenced `Subject:/To:/Cc:/Email body:` block |

`Block` = Headline | Text | Metrics | Table | BarChart | LineChart | Callout | Details | Section | Steps | Buttons | FollowUps | EmailDraft. A `Section`'s children are the same list minus `Section`.

**Actions.**
- `@ToAssistant(q)` asks `q` through `askScoped`. A click while answering shows the existing "Still answering…" flash.
- `@OpenUrl(u)`: a known in-app path (`/compliance`, `/energy`, `/vendors`, `/assets`, `/maintenance`, `/documents`) calls `openModule`. `http(s):` opens in a new tab with `noopener`. Anything else is ignored.
- Buttons are disabled while their answer is still streaming.

**Precedence in a reply bubble.**
- When `m.rich` exists, `ComplianceAnswer` keeps its run panel, the functional parts that `ComplianceAnswer.jsx` already draws, and the validation note. The view replaces the narrative and the presentational cards (KPI tiles, groups, sections, actions, insights).
- Without `m.rich`, the view renders on its own.
- Without a view, the bubble renders exactly as today.

**Server.**
- `agents/openui_view.py` holds the parser, the rendition, `split_answer` and `reconcile`.
- The view is dropped whenever a later step changes the Markdown. Those steps are the EL-UDR evaluator and the contract composer. When a step only appends text, the appended text becomes `tail`, drawn as Markdown under the view.
- Programs over 60,000 chars are not attached.
- `OPENUI_ANSWERS=0` switches the prompt section and the parsing off.
- Old threads get the current system prompt through the context hook. The checkpointer otherwise keeps their first-turn prompt.

**Performance budget.**
- Per streamed frame: ≤ 2 ms of OpenUI work. Paint is batched to one per 40 ms on rAF.
- Finished answers never re-render on keystrokes. `OpenUIAnswer` and `AnswerCards` are memoised on their strings, and handlers are stable.
- The OpenUI chunk is lazy. It is preloaded on idle after sign-in and on every send.

## Global Constraints

- No `git commit`, merge, stash, reset or push. Hussain commits on request.
- No database writes. Never call the live orchestrator, which writes `chat_turns`, traces and usage, without asking first.
- Other sessions share this tree. Make surgical `Edit`s on shared files (`Chat.jsx`, `OrchestratorDock.jsx`, `ComplianceAnswer.jsx`, `complianceLive.js`, `sessions.js`, `renderVals.js`, `index.html`, `package.json`, `Dockerfile`s, `orchestrator.py`, `system_prompt.py`, `chat_threads.py`, `context_budget.py`). Never rewrite them whole.
- Frontend tooling needs Node 24: `export PATH="$HOME/.nvm/versions/node/v24.18.1/bin:$PATH"`.
- npm installs run with `OPENUI_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1` and `--ignore-scripts`. Pin exact versions.
- The dev-mode devtools CDN loader must stay disabled: `globalThis[Symbol.for("openui.devtools.autoMount")] = true` in `index.html`, before the app script.
- `<Renderer publishObservability={false}>` everywhere.
- UI copy follows `test/noInternalNames.test.mjs`. All model text goes through `scrubInternal`, via `inline`/`MarkdownBlocks`. Never use `dangerouslySetInnerHTML`.
- `answer` stays Markdown for every consumer.
- Backend tests use `docker run --rm` on `hoistra-testenv-svc-deepagents:latest` with `src` and `tests` mounted read-only. Never start the testenv stack in a way that touches the running service.

## Review Focus

1. **A malformed or partial program from the model** (named args, a number where a string is required, raw newlines, invented components, prose before the program). The answer must still read well. On the server, `split_answer` falls back to plain text extracted from the string literals and never returns raw DSL. In the chat, bad components are dropped and the bubble is never blank. Tests are in Task 4 and Task 5.
2. **The evaluator or composer rewrites the answer.** The UI never shows stale figures: the view is dropped, or keeps a `tail` when text was only appended. Tests are in Task 5 and Task 6.
3. **Old threads and reopened conversations.** An old thread answers in OpenUI after the hook refresh. A reopened conversation shows the stored view, not the Markdown. Tests are in Task 6 and Task 7.
4. **A big answer in the narrow dock** (a 200-row table, a 12-point chart, long labels). There is no horizontal page scroll, the table collapses to 8 rows with "Show all", and the chart stays readable. Tests are in Task 3.
5. **Clicks on actions while streaming or answering, and unsafe URLs** (`javascript:`, `data:`, protocol-relative `//`). Buttons are disabled while streaming, unsafe URLs never open, and in-app paths navigate inside the app. Tests are in Task 4.

---

### Task 1: Dependencies and safety rails

**Files:**
- Modify: `apps/frontend/package.json` (adds `@openuidev/react-lang` `1.0.1` and `zod` at an exact 4.x version)
- Modify: `apps/frontend/index.html` (devtools flag script before the module script)
- Modify: `apps/frontend/Dockerfile`, `deploy/allinone/Dockerfile` (`ENV OPENUI_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1` before any `npm ci`/`npm install`)
- Test: `apps/frontend/test/openuiSafety.test.mjs`

- [ ] Write a test that asserts the following, then run it (expect FAIL):
  - `package.json` pins `@openuidev/react-lang` to exactly `1.0.1` and `zod` to an exact `4.` version.
  - `index.html` contains `Symbol.for("openui.devtools.autoMount")] = true` before `type="module"`.
  - Both Dockerfiles set `OPENUI_TELEMETRY_DISABLED=1` before their first `npm`.
- [ ] Install: `OPENUI_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1 npm install --save-exact --ignore-scripts @openuidev/react-lang@1.0.1 zod@<latest 4.x>`. Add the flag and the ENV lines.
- [ ] Run the test (expect PASS). Run `npm run build` (expect success, and `check-bundle` clean).

### Task 2: Component spec and prompt generator

**Files:**
- Create: `apps/frontend/src/openui/spec.js`
- Create: `apps/frontend/scripts/openui-prompt.mjs`
- Create: `apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/openui_prompt.md` (generated, committed)
- Test: `apps/frontend/test/openuiSpec.test.mjs`

**Interfaces:**
- `spec.js` is React-free:
  - `export const ROOT = "Answer"`
  - `export function makeLibrary(api, z, renderers = {})` returns the library. `api` is react-lang or lang-core (`defineComponent`, `createLibrary`, the action schema helper).
  - `export const promptOptions = { preamble, additionalRules, examples }`
  - `export const BLOCKS` is the ordered Block name list.
- `node scripts/openui-prompt.mjs [--check]` writes `openui_prompt.md`. With `--check` it exits 1 when the committed file differs.

- [ ] Write the tests, then run them (expect FAIL):
  - The library root is `Answer`, and the component names equal the Design table.
  - `library.prompt(promptOptions)` contains `Answer(children` and each component's signature line.
  - The generated prompt equals the committed `openui_prompt.md` byte for byte (the drift guard).
  - No component name is reserved (`Query`, `Mutation`, `Action`).
- [ ] Implement `spec.js` and the script, and generate the prompt file.
  - The additional rules cover: start with `root = Answer([...])` and then define children top-down in reading order; positional args only; double quotes with `\n`, never raw newlines or triple backticks in strings; figures only from tool results; ≤ 12 table rows, with the remainder named in the caption; doc links `[name](doc:<id>)` inside `Text`; in-app pages through `@OpenUrl("/compliance")`; end with `FollowUps` when there is a natural next question; a clarifying question is a `Headline` plus `FollowUps` with the choices; when a tool says the UI renders its cards, answer with a `Headline` only; never use OpenUI before or between tool calls.
  - Include two worked examples: a dashboard answer and a clarifying question.
- [ ] Run the tests (expect PASS).

### Task 3: Hoistra renderers

**Files:**
- Create: `apps/frontend/src/openui/library.jsx` (`export const library = makeLibrary(reactLang, z, renderers)` at module scope)
- Create: `apps/frontend/src/openui/components/{Layout,Content,Data,Charts,Actions}.jsx`
- Create: `apps/frontend/src/openui/openui.css` (classes `oui-*`, tokens only, container queries for the dock)
- Create: `apps/frontend/src/openui/useWidth.js` (ResizeObserver width, rAF-throttled)
- Test: `apps/frontend/test/openuiRender.test.mjs` (esbuild bundle plus `renderToStaticMarkup`, the same pattern as `vendorDeleteDialog.test.mjs`)

**Interfaces:**
- Consumes `makeLibrary` and `BLOCKS` from Task 2.
- Produces `library`.
- Renderers read actions through `OpenUIHostContext` from Task 4: `{ ask(q), open(url) }`.

- [ ] Write the tests, then run them (expect FAIL). Each one renders the full sample program from the spec examples and asserts:
  - Headline bold becomes `<mark class="oui-fig">`.
  - Metric tone `risk` becomes class `oui-tone-risk`.
  - A 30-row table renders 8 rows and a "Show all 30 rows" button.
  - Numeric columns get `oui-num`.
  - The bar chart has one `<rect>` per value, and the line chart has one `<polyline>` per series.
  - A collapsed `Section` renders its summary and hides its children.
  - Text `[x](javascript:alert(1))` renders no `href`.
  - Text naming an internal service renders the scrubbed wording.
  - `EmailDraft` renders subject, to and body and a Copy button.
- [ ] Implement the renderers to the Design look: text through `MarkdownBlocks`/`inline`; `React.memo` charts with a values comparator; `Table` client-side "Show all" state; long cells wrap; the table scrolls inside its own box when wider.
- [ ] Run the tests (expect PASS).

### Task 4: OpenUIAnswer, detection and actions

**Files:**
- Create: `apps/frontend/src/openui/OpenUIAnswer.jsx`
- Create: `apps/frontend/src/openui/detect.js`
- Create: `apps/frontend/src/openui/actions.js`
- Create: `apps/frontend/src/openui/host.js` (`OpenUIHostContext`, `preloadOpenUI()`, `LazyOpenUIAnswer`)
- Test: `apps/frontend/test/openuiAnswer.test.mjs`

**Interfaces:**
- `isOpenUI(text: string): boolean`. A prefilter plus a parse with root `Answer`.
- `looksLikeOpenUIStart(text: string): boolean`. True when the first non-blank line starts with `root = Answer(`. This is the cheap check for the live draft.
- `routeAction(event, host) => "ask" | "open" | "ignored"`. It maps `continue_conversation` to `host.ask(humanFriendlyMessage)`. It maps `open_url` to an in-app module, a safe `http(s)` URL, or nothing.
- `OpenUIAnswer({ text, tail, streaming })` is `React.memo`, compared on those three. It calls `<Renderer response={text} library={library} isStreaming={!!streaming} publishObservability={false} onAction={stable} onError={…}>` and renders `tail` with `MarkdownBlocks`.
- When nothing renders and it is not streaming, it falls back to `<AnswerCards text={plainTextOf(text)}/>`. `plainTextOf` comes from detect.js and returns the string literals joined, so the bubble is never blank.

- [ ] Write the tests, then run them (expect FAIL):
  - `isOpenUI`: true for the sample; false for Markdown containing `Total = 42`, for `Overdue = Metric(7)` and for empty input; true for a fenced program.
  - `looksLikeOpenUIStart("root = Answer([h")` is true, and it is false for prose.
  - `routeAction`: `continue_conversation` calls ask with the message; `/compliance` calls open with an in-app target; `https://x` opens; `javascript:` and `//evil` are ignored.
  - A garbage program renders the fallback text, not an empty element.
  - Buttons render disabled when `streaming`.
- [ ] Implement them.
- [ ] Run the tests (expect PASS).

### Task 5: Python parser and Markdown rendition

**Files:**
- Create: `apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/openui_view.py`
- Create: `apps/frontend/scripts/openui-fixtures.mjs` (writes `svc-deepagents/tests/fixtures/openui/*.json`: program, plus the lang-core parse summary of root type and props, unresolved and orphans)
- Test: `apps/backend/cafm-connector-service-final/svc-deepagents/tests/test_openui_view.py`

**Interfaces:**
- `parse(program: str) -> Node | None`. `Node` is a dataclass of `type` and `props` (dict in positional key order). Only the component schema in `SCHEMA` (names to prop names, copied from spec.js by the fixtures script into `tests/fixtures/openui/schema.json` and asserted equal to `SCHEMA`) is used to map positional args.
- `render_markdown(node: Node) -> str`, per the Design table.
- `plain_text(program: str) -> str`. The string literals, in order, as paragraphs.
- `split_answer(text: str) -> tuple[str, View | None]`. `View` is a dataclass of `program` and `markdown`. Markdown in, Markdown out with `None`. A parse failure returns `(plain_text, None)`.
- `reconcile(view: View | None, final_answer: str) -> dict | None`. It returns `{"v":1,"lang":program}`, or the same with `"tail"`, or `None`.
- `payload_entry(out: dict) -> dict`. It returns `{"tool":"openui_view","input":{},"output":out}`.
- `enabled() -> bool`. False when env `OPENUI_ANSWERS` is `0`, `false` or `off`.

- [ ] Write the tests, then run them in the container (expect FAIL):
  - Each fixture's Python summary equals lang-core's. Cover forward references, positional `null` skips, optional commas, comments, escapes, single quotes, `@ToAssistant`/`@OpenUrl`, fences, unresolved references and duplicate names.
  - The rendition of the sample equals the expected Markdown.
  - `split_answer` on Markdown returns it unchanged with `None`.
  - Garbage returns plain text with `None`, and that text never contains `root =`.
  - `reconcile` covers: equal gives the lang; appended gives a tail; changed gives `None`; over 60,000 chars gives `None`.
- [ ] Implement a recursive-descent parser for section 1.6 of the spec. Evaluate only literals, arrays, objects, references, component calls and `@ToAssistant`/`@OpenUrl` calls. Anything else evaluates to `None`, following drop-and-render: a component missing a required prop is dropped.
- [ ] Run the tests (expect PASS). Container command: `docker run --rm -v "$PWD/src:/app/src:ro" -v "$PWD/tests:/app/tests:ro" hoistra-testenv-svc-deepagents:latest python -m pytest -q tests/test_openui_view.py`

### Task 6: Orchestrator wiring

**Files:**
- Modify: `svc-deepagents/src/agents/system_prompt.py`. `build_system_prompt` appends `# Answer Format — OpenUI` plus the contents of `openui_prompt.md` when `openui_view.enabled()`. The section states that it supersedes rule 5 and rule 13 of Output Format Rules for the final answer.
- Modify: `svc-deepagents/src/agents/orchestrator.py`, in `stream()` after the event loop and in `_invoke()` after `_extract_answer`. Run `split_answer`, then the existing compose and evaluator, then `reconcile`, then append `payload_entry` after `_loop_cards`.
- Modify: `svc-deepagents/src/agents/context_budget.py`. The hook replaces a leading `SystemMessage` whose content lacks the OpenUI marker with the current `build_system_prompt()`, in what is sent only.
- Modify: `svc-deepagents/src/services/chat_threads.py`. `_card_payloads` keeps the first `openui_view`, on its own budget (`OPENUI_MAX_BYTES = 64_000`), so it never evicts compliance cards.
- Test: `svc-deepagents/tests/test_openui_wiring.py`

- [ ] Write the tests, then run them (expect FAIL):
  - The prompt contains the section when enabled and not when `OPENUI_ANSWERS=0`.
  - The hook swaps a stale `SystemMessage` and leaves a current one alone.
  - `_card_payloads` keeps both compliance cards and the view, and drops only an oversized view.
  - A stubbed `stream()` final answer in OpenUI yields `workflow_completed` with a Markdown `answer` and one `openui_view` entry.
  - A stubbed evaluator rewrite drops it.
- [ ] Implement.
- [ ] Run the new tests plus the existing tests for `chat_threads`, `system_prompt`, `context_budget` and the stream completion (expect PASS, no new failures against the baseline).

### Task 7: Frontend wiring

**Files:**
- Create: `apps/frontend/src/logic/openuiView.js` (`extractOpenUIView(toolCalls) -> {lang, tail} | null`)
- Modify: `apps/frontend/src/logic/complianceLive.js`. The message gets `view`. `answer_delta` paints are batched (rAF, ≥ 40 ms apart). `preloadOpenUI()` runs on send.
- Modify: `apps/frontend/src/logic/sessions.js` (reopen: `view` from stored tools)
- Modify: `apps/frontend/src/logic/renderVals.js` (`orchChat[].view`, `orchChat[].viewTail`, `orchChat[].streamed`)
- Modify: `apps/frontend/src/screens/Chat.jsx` and `apps/frontend/src/components/shell/OrchestratorDock.jsx` (render branch, `OpenUIHostContext` provider with stable `ask`/`open`)
- Modify: `apps/frontend/src/components/shell/ComplianceAnswer.jsx` (`view`/`tail` props, the live narrative via `looksLikeOpenUIStart`)
- Modify: `apps/frontend/src/components/shell/AnswerCards.jsx` (`React.memo` plus `useMemo`)
- Modify: `apps/frontend/src/App.jsx` (idle preload after sign-in)
- Test: `apps/frontend/test/openuiWiring.test.mjs`

- [ ] Write the tests, then run them (expect FAIL):
  - `extractOpenUIView` returns `{lang, tail}` from calls, and `null` when the entry is missing or malformed.
  - The reopen builder carries `view` from stored tools.
  - `ccStreamTurn` coalesces 50 deltas within one frame into one paint, with a fake rAF and clock.
  - The source assertions hold:
    - `Chat.jsx` and the dock render `LazyOpenUIAnswer` for `m.view`.
    - `ComplianceAnswer` routes a live OpenUI narrative to `OpenUIAnswer` with `streaming`.
    - `AnswerCards` is memoised.
- [ ] Implement.
- [ ] Run `npm test` (expect only the known pre-existing timezone failures).

### Task 8: Verify end to end

- [ ] Run `npm run build` and record the size of the lazy OpenUI chunk.
- [ ] Run the browser check.
  - Start Vite on Node 24 against a throwaway stub server. The stub answers auth and the deepagents WebSocket and replays a recorded OpenUI answer as `answer_delta` chunks, then `workflow_completed` with the Markdown and `openui_view`.
  - Use playwright-core with the installed Chrome to take screenshots of the chat page and the dock, light and dark, mid-stream and final.
  - Record a performance trace while streaming: no long tasks over 50 ms.
- [ ] Ask Hussain before any live-model check. Offer the alternative: a direct OpenAI call with the real system prompt and synthetic tool results, which writes nothing to the database.
- [ ] Update `memory/openui-orchestrator.md` with what shipped and how to switch it off.
