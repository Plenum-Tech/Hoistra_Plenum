# Orchestrator and Trace Lab: Task Plan

Source: task plan from Aasim, pasted by Hussain on 11 Nov 2026 after the 11 Nov call. Kept verbatim in
substance; only the table layout was restored. Ownership: **Workstream A = Bala. Workstream B = Hussain.**
The implementation plan for B is `docs/superpowers/plans/2026-11-11-trace-lab-eval-pipeline.md`.

The plan has two workstreams. A fixes the orchestrator and writes spec.md. B builds the Trace Lab and eval
pipeline. Tonight's run of 250 scenarios is the baseline, so we can measure the orchestrator fixes against it.

## Priority 0: Tonight's 250-scenario baseline run

| # | Task | Done when |
|---|---|---|
| 0.1 | Make sure the local cloned repo runs end to end with the orchestrator agent | One scenario completes and shows up in Trace Lab |
| 0.2 | Generate 250 scenarios from the current spec, covering single-domain, multi-domain, multi-turn and failure cases | Scenario file has a stable scenario_id for each case |
| 0.3 | Run a batch job that resumes after a crash and skips scenarios that already finished | 250 runs logged locally with no duplicates |
| 0.4 | Tag the run as baseline_v0 | Results can be filtered by run tag |

## Workstream A: Orchestrator and spec.md (Bala)

### A1. Fix the main orchestrator bug (highest priority)

| # | Task | Done when |
|---|---|---|
| A1.1 | Send sub-agent output back to the orchestrator instead of straight to the user | No path exists where a sub-agent's response reaches the user directly |
| A1.2 | Add a final synthesis step that uses only the evidence returned by tools | Every answer goes through synthesis; answer length drops against the baseline |
| A1.3 | Set synthesis rules: concise, grounded only in tool evidence, nothing invented | Eval: no claims without supporting evidence |

### A2. Planning and running tool calls

| Area | spec.md definition | Build task | Acceptance test |
|---|---|---|---|
| Multi-read planning | How one request is split into separate tool calls | Planner outputs a structured plan with one entry per call: {call_id, tool, params, depends_on} | A request covering 3 domains produces 3 or more planned calls |
| Dependency order | When calls run in parallel, in sequence, or a mix of both | Run calls as a dependency graph: independent calls in parallel, dependent calls in sequence | The trace shows parallel spans overlapping in time |
| Context passing | How entities from one call (building ID, vendor, asset) become inputs to the next | Map one call's output fields to the next call's parameters, and record where each value came from | The input of call 2 shows which output of call 1 it came from |
| Scope preservation | Filters, roles, buildings, vendors and reporting periods stay the same across calls | Keep one scope object for the plan and check every call against it | Eval: no call goes outside the user's scope |
| Retry rules | Retry only temporary errors (timeout, 5xx, rate limit), at most N times, with the same parameters. Never retry authorization or validation errors | Add a retry wrapper that logs each attempt as its own span | No retry with changed parameters appears in any trace |

### A3. Handling failures and missing evidence

| Area | spec.md definition | Build task | Acceptance test |
|---|---|---|---|
| Partial failure | Answer from the calls that worked and say clearly which parts failed | Synthesis input includes {succeeded[], failed[]} | The answer names the failed sub-question |
| Total failure | Give the final model a structured record of what failed, so it can write a useful explanation | Failure record format: tool, reason category, whether the user can act on it | The answer gives a next step, not a raw error |
| Authorization failure | Explain a denial in business terms, with no internal messages | Map system error codes to business wording | No stack traces, internal codes or raw messages reach the user |
| Missing evidence | Four different states: zero_results, not_recorded, unavailable, tool_failed | Tools return one of these status values | Eval: the answer's wording matches the state |
| Evidence conflicts | Rule for tool outputs that disagree: source priority, most recent record, or flag the conflict to the user | Detect conflicts before synthesis | Eval: conflicts are pointed out, not quietly merged |

### A4. Final answer quality

| Area | spec.md definition | Build task | Acceptance test |
|---|---|---|---|
| Multi-turn continuity | Carry the verified set of entities and scope between related turns | Store this verified set per session | A follow-up turn reuses scope without asking again |
| Answer completeness | Confirm every sub-question was answered, or say why it wasn't | Synthesis checks off each sub-question in the plan | Eval: every sub-question is covered |
| Output format | Four templates: success, partial success, denial, failure | Templates in the synthesis prompt | Format check passes for all four cases |
| Few-shot examples | At least one example for each scenario type above | Add the examples to spec.md and the prompt | Examples are referenced in the eval scenarios |

## Workstream B: Trace Lab and eval pipeline (Hussain)

### B1. Trace structure (keep LangFuse names)

```
Session (multi-turn conversation)
 └─ Trace: Turn 1 | Turn 2 | … (shown as tabs)
     ├─ Span: plan
     ├─ Span: tool_call[1..n]   (input, output, status, retries, duration)
     │    └─ Span: retry_attempt (if any)
     ├─ Span: context_passing / scope_check
     └─ Generation: final_synthesis (model, prompt, tokens, output)
     └─ Scores (logic / UX / domain, human + auto)
```

| # | Task | Done when |
|---|---|---|
| B1.1 | Group all turns of a conversation under one session ID, with a tab per turn | A multi-turn scenario opens as one session with Turn 1, Turn 2 tabs |
| B1.2 | Record the plan, tool inputs and outputs, failures and synthesis as linked child spans | The full span tree shows for every trace |
| B1.3 | Use LangFuse terms throughout (Session, Trace, Span, Generation, Score) | The UI and DB schema use the same names |

### B2. Scenario generation and expected outcomes

| # | Task | Done when |
|---|---|---|
| B2.1 | Generate scenarios at scale for each spec area: multi-read, partial failure, authorization failure, conflicts, multi-turn and so on | Every scenario is tagged with the spec section it tests |
| B2.2 | Work out the expected outcome for each scenario from spec.md: expected plan, call order, failure handling, answer format | Every scenario has an expected outcome stored with it |
| B2.3 | Version scenarios together with spec.md | A change to the spec flags which expected outcomes are now out of date |

### B3. Three levels of evaluation

| Level | What it checks | How |
|---|---|---|
| Logic | Correct plan, call order, scope kept, retries follow the rules, context passed correctly | Automatic, by comparing the trace with the expected outcome |
| User experience | Concise, complete, right template, clear wording on failures and denials | LLM judge plus human annotation in Trace Lab |
| Domain | Correct against domain rules (for example UK regulations and compliance) | Domain rubric plus SME review |

### B4. Annotation in Trace Lab

| # | Task | Done when |
|---|---|---|
| B4.1 | Score each trace at each level: pass or fail, a score, and comments | Annotations are saved to the trace |
| B4.2 | Filter by run tag, scenario type, failure status and score | The baseline and post-fix runs can be compared side by side |

### B5. Syncing local results to the dev database

| # | Task | Done when |
|---|---|---|
| B5.1 | Read the dev DB URL from an environment variable (for example DEV_DB_URL) | No credentials in code |
| B5.2 | Delta-sync job that upserts records by ID and tracks the last sync time | Running the sync twice creates no duplicates |
| B5.3 | Add a Sync button in Trace Lab, plus a CLI command | A manual sync shows how many records were pushed |
| B5.4 | Sync sessions, traces, spans, scores and annotations | The 250 local runs appear on the dev instance |
