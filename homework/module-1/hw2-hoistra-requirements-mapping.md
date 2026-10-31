# Homework 2 — Cartwheel-to-Hoistra Requirements Mapping

**Status:** Complete except for the explicitly deferred Homework 2 video

**Date:** 2026-10-31

**Implementation branch:** `homework-2-observability`

**Clean worktree:** `/root/Hoistra_Plenum-hw2`

## 1. Purpose

This document translates the Cartwheel Homework 2 handout into the equivalent
Hoistra implementation. It preserves the learning objective: prove which
server-authenticated identity reached each model and tool call, and make the full
request inspectable as one OpenTelemetry trace in Langfuse.

This mapping does not change Hoistra product behavior or `specifications.md`.
The owner approved these decisions before implementation began.

## 2. Existing Hoistra baseline

Hoistra already has several components that Cartwheel asks the student to build:

- `POST /api/homework/agent/ask` is the authenticated agent endpoint.
- `HomeworkIdentityMiddleware` authenticates a server-provisioned bearer token
  before the endpoint runs.
- `TestPrincipal` supplies trusted `user_id`, `role`, `organization_id`, and
  `building_ids`; the chat body cannot set or override these values.
- Conversations are created server-side and can be continued only by the same
  owner, role, and building scope.
- `homework_router.ask()` records immutable application execution metadata in
  PostgreSQL and returns a database `trace_id`.
- `/api/homework/traces` exports owner-scoped metadata without raw conversation
  content.

The existing PostgreSQL execution record is not an OpenTelemetry trace. Homework
2 will add OpenTelemetry/Langfuse alongside it, not replace it.

## 3. Requirement mapping

### Part A — authenticated identity and permission result on tool spans

**Cartwheel requirement:** Add `cartwheel.*` identity and permission fields to
the active tool span.

**Hoistra equivalent:** Wrap the actual tool execution boundary with a shared
OpenTelemetry helper. Do not instrument `homework_router.actual_tools()`, because
that function runs after the operation has finished.

Each tool span will contain:

| Standard/application field | Hoistra value |
| --- | --- |
| `gen_ai.operation.name` | `execute_tool` |
| `gen_ai.tool.name` | Registered tool name |
| tool arguments/result | Bounded, redacted JSON captured at the execution boundary |
| `hoistra.user_role` | Server-authenticated principal role |
| `hoistra.user_id` | Server-authenticated user ID as a string |
| `hoistra.organization_id` | Server-authenticated organization ID |
| `hoistra.building_ids` | Server-authenticated building scope as a string array |
| `hoistra.session_id` | Server-owned conversation ID |
| `hoistra.request_id` | Server-generated request ID |
| `hoistra.permission_denied` | Boolean |
| `hoistra.permission_denied.reason` | Present only for a denied operation |

Permission fields must come from the deterministic authorization result or
raised authorization error, never from model prose.

### Part B — authenticated session creation

**Cartwheel requirement:** Add a public session endpoint that validates a user
and returns a signed token.

**Hoistra equivalent:** Keep the existing, stronger server-owned identity design.
Do not add a public token-minting endpoint.

Hoistra already:

1. provisions random bearer tokens on the server;
2. stores only token hashes and expiry in the identity fixture;
3. stores plaintext development tokens separately in an owner-only file;
4. authenticates before route execution;
5. places the trusted principal in request state; and
6. prevents chat/request fields from changing identity or scope.

For Homework 2, a "fresh session" means a new server-owned conversation under an
existing authenticated development principal. Authentication tests will verify
the equivalent Cartwheel guarantees: identity claims in the body are rejected,
and a different principal cannot continue another owner's conversation.

### Part C — traced message endpoint

**Cartwheel requirement:** Create a root request span around the agent run and
record identity, prompt version, input, and output.

**Hoistra equivalent:** Instrument the existing
`POST /api/homework/agent/ask` path. Start the root span in
`agents/homework_router.py::ask()` after the owned conversation is resolved and
before dispatch begins. Keep it open through tool execution, answer synthesis,
and the final PostgreSQL execution record.

Root span name: `hoistra.session_message`

Root span attributes:

- `hoistra.user_role`
- `hoistra.user_id`
- `hoistra.organization_id`
- `hoistra.building_ids`
- `hoistra.session_id`
- `hoistra.request_id`
- `hoistra.scenario_id`, only when a nonempty optional scenario ID is supplied
- `hoistra.prompt_version`
- `hoistra.selected_route`
- `hoistra.outcome`
- `hoistra.execution_outcome`
- `hoistra.tool_count`
- `hoistra.permission_denied`
- `gen_ai.input.messages`, in serialized OTel GenAI message format
- `gen_ai.output.messages`, in serialized OTel GenAI message format

The route response will continue to return the existing answer,
`conversation_id`, PostgreSQL `trace_id`, and prompt metadata. It will also expose
the effective `prompt_version` and the 32-hex `otel_trace_id` needed to find the
same run in Langfuse.

The PostgreSQL `unified_trace` record will retain its UUID and add
`otel_trace_id`, `prompt_version`, and optional `scenario_id` for correlation.

Model calls will be instrumented through the LangChain/OpenLLMetry integration.
Manual tool execution will use the Part A wrapper so model and tool spans share
the root trace ID. The tool-free answer-synthesis model call must also be a model
span.

### Prompt-version definition

Hoistra uses multiple manifest-protected system prompts rather than one
`SYSTEM_PROMPT_TEMPLATE`. Therefore, `hoistra.prompt_version` will be the SHA-256
of canonical JSON containing only the active static prompt sources:

```json
{
  "schema": "hoistra-effective-prompt-v1",
  "unified_manifest_sha256": "...",
  "selected_profile": "energy",
  "selected_profile_manifest_sha256": "...",
  "answer_instruction_sha256": "..."
}
```

Rules:

- Keys are sorted and compact JSON separators are fixed before hashing.
- A component that was not used is `null`.
- User text, identity, model name, database state, tool results, current date,
  and `specifications.md` are excluded.
- Exact rendered-prompt hashes remain separate model-span attributes for
  debugging; they are not the stable root prompt version.
- A prompt edit must pass the existing manifest/checksum builder. An untracked
  edit continues to fail closed.

This makes the version change when an active system instruction changes without
making it change for different users, evidence, or dates.

### Part D — authentication and instrumentation tests

Create:

`apps/backend/cafm-connector-service-final/svc-deepagents/tests/test_observability.py`

Planned tests:

1. Invalid bearer token returns 401 before dispatcher/model execution.
2. Body identity fields such as `role`, `user_id`, `organization_id`, or
   `building_ids` are rejected with 422.
3. Principal B cannot continue Principal A's conversation; response is 404 and
   no model/tool runs.
4. An in-memory exporter records one `hoistra.session_message` root span with
   identity, input/output messages, scenario ID, and prompt version.
5. An allowed tool span records authenticated scope and
   `hoistra.permission_denied=false`.
6. A denied tool span records `hoistra.permission_denied=true` and a reason.
7. Prompt version is deterministic for the same active prompts and changes when
   the answer-synthesis prompt hash changes.
8. Tracing disabled or unconfigured is a safe no-op; tests need no Docker,
   Langfuse, or provider key.

Existing identity, conversation-ownership, trace-export, and prompt-manifest
tests remain in force.

### Part E — run and inspect five traces

Use a separate Langfuse project named **Hoistra Homework 2**. The existing local
Langfuse stack can be reused, but Homework 1 trace data and the custom PostgreSQL
trace store remain unchanged.

Tracing configuration will be environment-based. No keys will be hardcoded.
Content capture may be enabled only for the fictional Homework data, with bounded
and redacted tool arguments/results. Bearer tokens and provider keys must never
be captured.

Five requests drawn from the committed `homework/module-1/hw1-session.jsonl`:

| # | Homework 1 case | Authenticated role/user | Expected route/evidence |
| --- | --- | --- | --- |
| 1 | `HW1-ENERGY-01-FINAL-AUTHENTICATED` | Property manager / `hw1-property` | `energy`; `energy_eui` |
| 2 | `HW1-WO-02-MODEL-SYNTHESIS-e47021fb` | General user / `hw1-general` | `reads`; `work_order_status` |
| 3 | `HW1-WO-MULTI-READ-FAIL-01` | Portfolio manager / `hw1-portfolio` | Preserve and inspect the current multi-read failure/clarification |
| 4 | `HW1-COMPLIANCE-MULTI-TOOL-FAIL-01` | Compliance officer / `hw1-compliance` | Preserve and inspect current communications-routing failure |
| 5 | `HW1-ENERGY-01-FINAL-AUTHENTICATED` (fresh repeat) | Property manager / `hw1-property` | A second energy run in a fresh conversation |

The fifth request is a repeatability sample because the committed Homework 1
file contains four distinct authenticated request texts. The handout requires
five requests, not five unique texts.

For each trace, inspect:

- one root request span;
- every model span, including final answer synthesis;
- ordered tool spans and their arguments/results;
- shared trace ID and parent/child relationships;
- trusted identity/scope fields;
- permission-decision fields; and
- requested/returned model and token metadata when the provider supplies it.

### Part F — compare two prompt versions

Use case `HW1-ENERGY-01-FINAL-AUTHENTICATED` for the controlled comparison:

1. run it in a fresh conversation with the current prompts;
2. record the trace and `hoistra.prompt_version`;
3. make one harmless wording-only change to the active
   `skills/orchestrator/answer-synthesis.md`;
4. rebuild its manifest/checksum and restart the service;
5. run the exact same request with the same user, model, and database state in a
   second fresh conversation;
6. verify the prompt versions differ;
7. restore the prompt byte-for-byte, rebuild its checksum, and restart; and
8. verify the repository is restored before committing.

The comparison validates version capture. It is not a quality comparison and
must not alter business data.

### Completion evidence — 2026-10-31

- Implementation commit: `e228427` (`Add Homework 2 observability tracing`).
- Two-trace evidence commit: `86f5e41` (`Add Homework 2 comparison traces`).
- Final validation: 17 focused observability tests and the complete 582-test
  service suite passed; the full run also completed 65 subtests.
- Part E traces in the separate **Hoistra Homework 2** Langfuse project:
  1. `a54650761cab9bdc83d9f8febfe8b15d`
  2. `7eebffc96e7b4ddb430d7843ccc2e532`
  3. `a84f3b591e6b8b761b03bfc43b0bcc47`
  4. `9b2d2a8bfba6a65aadd44256d96e82fd`
  5. `22fb242d31295711563155d6e92cc6ea`
- The controlled Part F baseline was trace
  `22fb242d31295711563155d6e92cc6ea`, prompt version
  `00e5a2e64767c7f5135b89cc1e8f45da298bdbeb58bcd3fbc00f4fa2ae0e170d`.
- The temporary changed-prompt trace was
  `605b3ed7ece27d62f2477ec7c976cf96`, prompt version
  `c09ecd324dfb5f5034bae597bff36da677790d2ae6c5d28e695f0f3101750559`.
- The temporary wording-only edit was:

  ```diff
  -You write the final answer from one backend-provided evidence envelope.
  +You write the final answer from a backend-provided evidence envelope.
  ```

- The original prompt, manifest checksum, repository state, and committed
  service image were restored after the comparison.
- Repository-root `hw2-traces.json` contains exactly the two selected comparison
  traces and their required correlation fields.
- The Homework 2 video is deliberately deferred until Homeworks 1–3 are ready
  for recording together.

## 4. OpenTelemetry/Langfuse implementation locations

Step 3 uses these implementation locations:

- new shared setup/helpers under
  `apps/backend/cafm-connector-service-final/svc-deepagents/src/observability/`;
- one-time setup and shutdown in `src/api/main.py` lifespan;
- root span and correlation fields in `src/agents/homework_router.py`;
- optional `scenario_id` validation in `src/api/routes/homework_agent.py`;
- tool wrappers at the real `service.execute(...)` or
  `dispatcher.execute(...)` boundaries in the enabled Homework agents;
- direct dependencies in `svc-deepagents/pyproject.toml`; and
- environment wiring in `homework/module-1/docker-compose.hw1.yml` and its
  documented environment example.

The implementation will use a no-op tracer when disabled. If tracing is enabled,
incomplete configuration must fail clearly rather than silently exporting to an
unexpected destination.

Container networking will use an explicit Langfuse endpoint reachable from the
agent container; `localhost:3000` inside that container is not the host's
Langfuse service.

## 5. Deliverables

Homework 2 deliverables:

- observability setup/helper module(s);
- the traced Hoistra endpoint and model/tool hooks;
- `apps/backend/cafm-connector-service-final/svc-deepagents/tests/test_observability.py`;
- repository-root `hw2-traces.json`, containing exactly two selected trace
  objects with:
  - `trace_id`
  - `permalink`
  - `prompt_version`
  - `user_role`
  - `user_id`
  - ordered `tool_order`
  - `final_status` (`completed` or `error`)
- this approved mapping document; and
- the video later, after Homeworks 1–3, as requested.

An optional non-secret run manifest may list all five case IDs and trace IDs, but
it will not replace the strict two-object `hw2-traces.json`.

## 6. Explicit non-goals and safety constraints

- Do not add a public session/token creation endpoint.
- Do not accept role, user, organization, or building scope from chat text.
- Do not weaken 404 cross-owner behavior or 409 assignment-drift behavior.
- Do not replace or expose the existing owner-scoped PostgreSQL trace store.
- Do not load the full `specifications.md` into the model prompt.
- Do not capture bearer tokens, API keys, or nonfictional sensitive content.
- Do not allow telemetry failure to broaden access or fabricate success.
- Do not make the temporary prompt comparison change permanent.
- Do not record the video in this step.

## 7. Approval gate

Implementation starts only after approval of these four decisions:

1. Preserve server-provisioned bearer identity; do not add public session
   creation.
2. Use `hoistra.*` application attributes plus standard `gen_ai.*` fields.
3. Use the manifest-based effective prompt-version rule above.
4. Reuse the local Langfuse deployment but create a separate **Hoistra Homework
   2** project.
