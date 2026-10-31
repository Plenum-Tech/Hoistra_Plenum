# Hoist Trace Lab — analysis

**Date:** 2026-10-15
**What this is:** the detailed analysis behind `2026-10-15-hoist-trace-lab-design.md`. Part 1 studies Aasim's reference repo (`saasimz/cartwheel-homeworks`, Cartwheel Trace Lab) file by file. Part 2 studies Hoist's trace capture, the Hoist Traces page, the platform constraints, and the earlier eval build in `stash@{0}` (as lessons only), and ends with the concept map and the gap list.
**How it was produced:** 25 read-only agents read the repo, the Hoist code on branch `eval-2` (1684810) and the stash snapshot; no database was queried and no service was called. Counts were recomputed from committed files where possible.

---

## Part 1 - Cartwheel Trace Lab, analysed in detail

This part describes the reference repository as it actually is. The later parts can then say exactly what Hoist keeps, what it changes and what it adds.

**Citation conventions**
- Paths without a prefix are in the Cartwheel clone at HEAD `6579213`.
- `SKILL@3095893^` means `git show 3095893^:analysis/skill/SKILL.md`, the 313-line method file before it was replaced by a pointer.
- Hoist paths are relative to `Hoistra_Plenum` on branch `eval-2` (`1684810`). `DA/` means `apps/backend/cafm-connector-service-final/svc-deepagents/`.
- Every count below was recomputed from the committed files. Where a deep-read map disagreed with the files, the files win and the difference is noted.

### 1.1 What the repository is

#### The course, the agent, the spec

Cartwheel is the starter repository for the course "Evaluating and Improving AI Agents" (README.md:7). Students build a support agent in Module 1. They then use the same repository for trace analysis, automated evaluation, CI, adversarial testing and improvement work. The homework index lists HW1 to HW6 (homework/README.md). HW7, the monitoring homework, has not been released. The instructors commit as Shreya Shankar and Hamel Husain.

**The agent.**
- Roles: shopper, merchant and support.
- Tools (SPEC.md:67-77): six reads (`search_help_center`, `get_policy`, `search_products`, `get_order`, `list_my_orders`, `find_order`) and three writes (`issue_refund`, `cancel_order`, `escalate_to_human`).
- World: a deterministic SQLite seed with 20 stores, 800 products, 525 users, 10,000 orders and 18 policy documents, plus six planted data-quality defects (README.md:43-50).
- Model and tracing: the default model is gpt-5.5. Every turn is traced to self-hosted Langfuse through OpenLLMetry.

**SPEC.md** holds numbered requirements: PURPOSE-1, AUTH-1, SCOPE-1..2, TOOL-1..9, TOOL-R1, POLICY-1, DISPUTE-1, ESC-1..4 and RESP-1..9.
- It is a reference document, not loaded at runtime. SPEC.md:11-18 maps each kind of requirement to the code that implements it.
- Every failure mode must cite a SPEC id.
- Aasim added RESP-6..9, TOOL-R1, POLICY-1 and DISPUTE-1 during HW4 (commits f8302eb and 6579213). 5 of his 6 modes cite one of these additions.

**The golden set.** `scenarios/support_scenarios.jsonl` holds 250 planned conversations: 175 coverage and 75 challenge.
- 235 are objective cases. Each has an expected outcome and a typed evidence source: sql 80, policy_document 66, eligibility_function 59, data_quality_table 30.
- 15 are judgment cases that cite a specification criterion.
- Length: 197 conversations have one turn, 41 have two and 12 have three.

`scenarios/runner.py` plays the set as one batch with one run id. The expected outcome never reaches the agent; it is copied only into the result row.

#### Aasim's fork, in order

| Date (2026) | Commit(s) | Author | What it did |
|---|---|---|---|
| 2 Sep | cdf1964 | Shreya Shankar | Course codebase, including the demo judge fixtures (`analysis/state/judges/`, `splits.json`, `store_traces.json`) |
| 15 Sep | c24da61 | Shreya Shankar | Last instructor change to the review UI (`analysis/ui/index.html`, 891 lines) |
| 15 Sep | 3095893 | Shreya Shankar | Replaced the 313-line `analysis/skill/SKILL.md` with an 8-line pointer to the public course skills |
| 20 Sep | ac4fa38, 4155748, 3657a67, 1cf7390, ce27c1e, c0baa08 | saasimz | Trace Lab: live Langfuse sync and Runs view, cheaper refresh, review by scenario, gzip, model-call and reasoning cards, read retries |
| 24 Sep | c994a3d, 88bb23b | Aasim Shaik | HW3 scenario evaluation; session ids recorded in traces and scenario results |
| 29 Sep | f8302eb | Aasim Shaik | HW4 Part A review interface `analysis/review_app/` (+1757/-27 lines across 10 files) |
| 4 Oct | 6579213 | Aasim Shaik | HW4 taxonomy: `analysis/state/*` and `analysis/report/*` |

#### Three layers in one HTML file

| Layer | File | Lines | What it added |
|---|---|---|---|
| 1. Instructor UI (c24da61) | `git show c24da61:analysis/ui/index.html` | 891 | <ul><li>Trace, Map and Progress views</li><li>Role-coloured timeline; a tool call and its result share one card; results over 240 characters collapse</li><li>Select text, type a note, press Enter; yellow highlight plus margin note</li><li>Dashed agent suggestions with accept/dismiss</li><li>Mode treemap, bulk suggestion queue, PCA map</li><li>2 s polling, markdown-lite rendering, j/k and arrow navigation</li></ul> |
| 2. Aasim, 20 Sep | `analysis/ui/index.html` | 1453 | <ul><li>The "Trace Lab" name</li><li>Runs, Model calls and Labels tabs; run select kept in `?run=`</li><li>Sync now and a sync status line; id search</li><li>run::scenario grouping with turn/retry buttons</li><li>Collapsed expected-outcome panel; scenario comments; five-key legend</li><li>Model-call cards with four collapsed panels; chain-of-thought notice</li><li>GET retries, light/heavy poll split, server gzip</li></ul> |
| 3. Aasim, HW4 (f8302eb) | `analysis/review_app/index.html` | 1622 (+205/-36 against layer 2) | <ul><li>"Conversations" grouped by `session::<session_id>`, with a labelled legacy fallback</li><li>session_id on every annotation</li><li>Search opens the exact trace</li><li>Dismissed suggestions saved as label 0</li><li>Atomic bulk queue and a serialized save chain</li><li>"Review completion" block</li><li>Labels grid limited to human-reviewed traces, with dashed AI proposals</li></ul> |

The page Aasim's notes call the "reference" (`analysis/ui/index.html`) already contains layer 2. Only layer 1 is the instructor's work.

#### What each file Aasim named does

| File | Size | What it does |
|---|---|---|
| `analysis/review_app/index.html` | 1622 lines, 80 KB, no dependencies | The whole UI. Six tabs, annotation, labels and a polling client over the file API. All DOM is built with template strings and `innerHTML`. |
| `analysis/review_app/server.py` | 28 lines | Sets `trace_server.UI_DIR` to `review_app/` at import time (line 18), then calls the shared `main()`. |
| `analysis/review_app/README.md` | 22 lines | Run command: `CARTWHEEL_ANALYSIS_STATE=analysis/state/live uv run python -m analysis.review_app.server --host 127.0.0.1 --port 8021`. Also states the grouping rule and the label rule. |
| `analysis/server.py` | 521 lines | Standard-library `ThreadingHTTPServer`. Eight file-backed JSON endpoints plus `POST /api/sync`. Diffs annotations to find new labels, writes Langfuse scores and the label history. Has a `--replay` demo. Defaults to 127.0.0.1:8020 (line 431). |
| `analysis/live_sync.py` | 321 lines | `TraceSync` mirrors Langfuse into samples, runs, context, graph and sync-status files. It never writes review files. |
| `analysis/report/interface_comparison.md` | 143 lines | The main technical spec: 28 testable requirements (table in 1.4). |
| `analysis/report/review_summary.md` | 119 lines | HW4 results: sample, taxonomy table, stability check, revision, rejected suggestion, AgentDebug comparison and Langfuse reconciliation. |
| `analysis/skill/review-loop.md` | 93 lines | The coding agent's live duties: a 2 s watcher on annotations, grouping notes, depth scans, breadth batches, re-review prompts and the discovery-rate curve. Prose only. |
| `analysis/prepare_review.py` | 256 lines | Offline. Joins traces to scenarios (turn_index, attempt and is_retry, found by substring match). Builds the map projection: PCA by SVD over 5 standardized features, then k-means with k = min(8, n) and seed 7 (lines 179-221). |
| `analysis/helpers/normalization.py` | 522 lines | Turns a Langfuse trace into the UI record (messages, observations, features, meta). Also redaction, reasoning summaries and the session merge. |
| `analysis/helpers/langfuse_io.py` | 390 lines | `write_label_score` calls `create_score(name=mode, value, NUMERIC, comment)` with no score id (lines 324-359). Also read-back and queue helpers that nothing calls. |
| `analysis/helpers/tools.py` | 760 lines | The measurement API: `select_traces`, `next_to_label`, `split_labels`, `register_judge`, `run_judge`, `judge_alignment`, `iteration_log`, `freeze_judge`, `_prompt_hash`, `_wilson_interval`, `_load_labels`. |
| `analysis/helpers/guards.py` | 118 lines | `GuardViolation` plus five judge-lifecycle guards. |
| `analysis/helpers/reporting.py` | 330 lines | `corrected_prevalence` (Rogan-Gladen with bootstrap) and `failure_report`. |
| `analysis/helpers/selection.py` | 310 lines | k-means diversity picks, random picks, IQR outliers, and "enrich" by bag-of-words cosine. |
| `analysis/helpers/scale.py` | 263 lines | Batch judge as one DocETL map operation with strict decoding. |
| `analysis/helpers/_state.py` | 106 lines | State root, atomic JSON writes (temp file plus rename) and append-only JSONL. |
| `analysis/state/` | - | The persisted review state (section 1.5). |

#### The three test files

| File | Tests | What they pin |
|---|---|---|
| `tests/test_review_app.py` | 4 | <ul><li>Two string greps over `review_app/index.html`: the session grouping strings, and the Part A workflows (scenario comment, review progress, Accept/Dismiss selected, `structuredDecision`).</li><li>`_merge_multi_turn`: session id beats scenario; 5 traces become 4 rows.</li><li>A structured label is appended to `labels/<mode>.jsonl`, and `write_label_score` is called once.</li></ul> |
| `tests/test_trace_lab_sync.py` | 3 | <ul><li>`merge_samples` upserts by trace id, keeps old rows and keeps reviewer flags.</li><li>`build_runs` keeps recorded and inferred runs apart.</li><li>`TraceSync.run_once` writes the trace files and leaves `annotations.json` byte-identical.</li></ul> |
| `tests/test_trace_lab_ui.py` | 5 | <ul><li>Four string greps over `analysis/ui/index.html` (layer 2, not the HW4 file).</li><li>One functional test: gzip for bodies of 1 KB or more.</li></ul> |

Six of the 12 tests grep strings, and they read two HTML files that have since drifted apart. Hoist should port the intent as behaviour tests, not as greps.

### 1.2 The method end to end

**The one rule** (SKILL@3095893^:14-20): "The human notices and decides. You build, organize, compute, and scale."
- The AI never open-codes a fresh trace.
- It never adds a failure mode from its own reading.
- It never flips a human label.
- Every intermediate result persists, so work resumes instead of restarting.

The loop is a cycle, not a pipeline. Breadth (diverse and random reading to find new modes) alternates with depth (scan for one named mode everywhere, then re-review).

#### 1.2.1 Discovery: phases 1-5 (HW4)

| Phase | Who does what | Exact rule |
|---|---|---|
| 1. Understand the domain and the store | AI inventories; human confirms | Read whole traces and the underlying data, not a model's paraphrase. Keep a private list of plausible failures and never seed the taxonomy from it. |
| 2. Visual encoding | AI proposes; human approves before any code | One channel per dimension. Hue = message role. Opacity is reduced only for literally redundant content, never by role. Spacing: 4-8 px within a step, 16-24 px between steps, 32-48 px between sections. Monospace for JSON. A tool call and its result share a container. |
| 3. Review interface | AI builds from an approved proposal | Group traces by session in time order. Notes sit beside the content. Provide taxonomy, labels and progress views. AI suggestions are shown apart, with explicit accept/reject. No dropdowns, predefined labels or 1-5 scores during open coding. |
| 4. Select what to read | AI picks with a reason per trace; human reads | `diversity`: 2/3 cluster representatives and 1/3 random. k-means runs over turn_count, tool_call_count, distinct_tools, has_retrieval and tokens (seed 7, up to 25 iterations). `outlier`: 1.5 x IQR on tokens. A cluster or outlier is a prompt to read, never a failure mode. |
| 5. Open coding with live axial coding | Human open-codes; AI groups, scans and proposes | Output: 5-8 binary modes, each traceable to human notes (`created_from`). |

**The four review batches** (hw4.md:84-89):
1. 15 uniformly sampled traces plus 15 cluster representatives.
2. 30 traces spread over one product dimension, chosen before looking at outcomes.
3. 25 traces from depth searches, including close negatives.
4. 15 final uniformly sampled traces, as the stability check.

No trace counts in two batches. At least 100 distinct traces. Never select a trace only because a model or heuristic predicts a failure.

**First-failure open coding** (hw4.md:92). Read the conversation with its retrieval and tool activity until the first failure: "the earliest step to violate the product requirements or make a later failure materially more likely". Write a free-form note and stop. If the trace is clean, record "no failure observed", so a reviewed trace differs from an unreviewed one.

A good open code names its evidence: "the reply reports a completed refund even though the tool only queued it for approval". A label like `tool_problem` does not (hw4.md:94).

**Axial coding.** The test is always "does fixing one fix the other?" (review-loop.md:37).
- Merge two groups when one product change fixes both.
- Split a group when its examples need different fixes, even if the wording is similar (hw4.md:141).
- Keep the original open codes in history.

**Suggestions are retrieval signals, never labels** (hw4.md:147-149; review-loop.md:52).
- Favour recall: dismissing a false hit costs one click.
- Accept writes label 1 with source `accepted_suggestion`.
- Dismiss writes label 0 with source `rejected_suggestion`, which is kept as boundary evidence.
- The saved state must hold at least one rejected suggestion.
- Repeat the search after a definition changes.

**The mode contract** (hw4.md:130-139). A final mode is invalid without all eight fields:

| # | Field |
|---|---|
| 1 | A `snake_case` name for the observed failure |
| 2 | A binary definition another reviewer can apply |
| 3 | At least 3 confirmed positive traces |
| 4 | At least 3 close-negative traces (similar-looking traces without the failure), when the data allows |
| 5 | The human annotations it originated from (`created_from`) |
| 6 | A boundary against the nearest neighbouring mode |
| 7 | The likely evaluator type: code check or LLM judge |
| 8 | A requirement source: a SPEC id or a recorded spec revision |

**The complete matrix.** Apply every final mode to every reviewed trace as present or absent (hw4.md:151-154). Report counts as sample fractions, never as prevalence, because clustering and depth searches change the sample's make-up (hw4.md:156). First-failure coding undercounts modes that occur late in a trace. Report both the first-failure count and the any-instance count (review-loop.md:71).

**Stability, drift and saturation.**
- *Final-15 rule*: record how many of the final 15 uniform traces produced a new consequential mode. If several did, review another batch (hw4.md:158).
- *Criteria drift*: after new modes appear, prompt the reviewer to re-read earlier traces. The canonical example: a sycophantic opener noticed 20 traces in is "everywhere, including in traces already reviewed and marked clean" (review-loop.md:65-67).
- *Saturation*: report the discovery-rate curve, meaning recent traces that produced a brand-new mode versus a repeat. When it drops toward zero, stop naming. "Counting continues on new data forever; naming stops when new names stop appearing" (review-loop.md:79-80).

#### 1.2.2 Measurement: phases 6-11 (HW5)

**Code first, spec first** (SKILL@3095893^:228-237).
- Use a code check whenever the rule is objective: a reference value, a permission rule or a schema. It needs no alignment study when it compares against authoritative state.
- Use one binary evaluator per mode; never a compound score.
- Fix a specification failure (the prompt never asked for the behaviour) by editing the prompt, not by building an evaluator. The fix should show as a prevalence drop between prompt versions.

**Label minimums** differ by source:

| Source | Minimum |
|---|---|
| HW5 (hw5.md:60) | 30 Pass and 30 Fail from independent conversations; about 100 recommended |
| SKILL@3095893^ | 20 + 20 before writing a judge |
| SKILL@cdf1964 (production advice) | 30+ per class in each of dev and test |
| validate-evaluator skill | about 50/50 |

**Split rules** (tools.py:353-441; guards.py:91-118).
- One record per conversation, and one per group of duplicate runs or close variants.
- Stratified by label, seed 7, persisted, disjoint, and unchanged during development.
- Ratios: HW5 20/40/40 (hw5.md:112); helper default 15/42.5/42.5; public skill 10-20/40-45/40-45.
- The guard refuses thin classes:

```text
smallest = min(dev_frac, test_frac)
refuse if int(fail_total * smallest) < min_per_class or int(pass_total * smallest) < min_per_class   # min_per_class = 10
then re-check the actual dev and test class counts after partitioning
```

With 20/40/40, each class needs at least 25 labels: int(25 x 0.4) = 10. HW5's 30/30 gives 6/12/12 per class.

**Judge anatomy** (SKILL@3095893^:239-254; hw5.md:150-152). One judge covers one mode and has four parts:
1. A single task with one binary criterion.
2. Pass and Fail definitions taken from the axial definition.
3. Few-shot examples from the training split only: a clear pass, a clear fail and a borderline case. Two to four is typical, and gains plateau at four to eight.
4. Structured output: a critique first, then the result `Pass` or `Fail`.

The judge input carries only the evidence the decision needs. It never contains labels, notes or scenario metadata. The judge is told to ignore instructions quoted inside the trace. The input export is saved once and stays unchanged across versions. A different model family is preferred (the demo used claude-opus-4-6 to judge gpt-5.5). The DocETL wrapper appended to every frozen prompt (scale.py:236-248):

```text
{prompt_text}

--- Trace to evaluate ---
{{ input.content }}

First write a critique of the trace against the criterion. Use specific evidence from the provided trace. Then return result as exactly Pass when the named failure is absent, or Fail when present.
output schema: {critique: string, result: string}
```

**Iterate on dev.**
- `run_judge` caches predictions by (prompt_hash, trace_id) and saves after every batch of 10.
- `judge_alignment` returns TPR, TNR, Wilson intervals, agreement, confusion counts and disagreement ids, and logs an iteration row.
- Every disagreement is triaged:

| Triage | Action |
|---|---|
| Judge wrong | New prompt version |
| Label wrong | The human flips it (append-only, logged), then metrics are recomputed |
| Definition unclear | Clarify the boundary and recheck the affected labels |

HW5 allows a baseline plus at most two revisions. If progress stalls, try in this order: a stronger model, splitting the criterion, or better labels. Version identity:

```text
prompt_hash = sha256(model + "\n" + prompt_text)[:12]      # tools.py:49-54; also the prediction-cache key
judge_id    = "<mode>-v<N>"                                # every edit is a new version
```

**Metrics: Pass is the positive class** (hw5.md:156-172).

| | Human Pass | Human Fail |
|---|---|---|
| Judge Pass | TP | FP (missed failure) |
| Judge Fail | FN | TN |

```text
TPR = TP / (TP + FN)     # agreement with human Pass labels
TNR = TN / (TN + FP)     # agreement with human Fail labels
Wilson 95% (z = 1.96), tools.py:57-69:
  rate = k/n; denom = 1 + z^2/n; center = (rate + z^2/(2n)) / denom
  margin = z * sqrt(rate*(1-rate)/n + z^2/(4n^2)) / denom
  interval = [max(0, center - margin), min(1, center + margin)]
Example: 16 of 20 human Fail cases caught -> TNR 0.80, interval about 0.58-0.92
```

Overall agreement is never the metric. A judge that always says Pass scores 93% agreement on a mode present in 7 of 100 traces, with TNR 0.

**Freeze, then test once** (guards.py:41-88; tools.py:740-760).
- `freeze_judge` is one-way per version. Any fix is a new version, which re-locks the test split.
- The test split runs exactly once, with the same model, prompt, saved inputs, labels and split.
- A resume may only fill missing predictions. Never register, split or freeze again to resume. Recomputing metrics from saved predictions is allowed.
- In the demo, dev TNR 0.91 fell to 0.83 on test.

**Corrected prevalence** (reporting.py:29-78, 150-186). The judge's raw rate is corrected only with held-out test TPR/TNR, and only for a frozen judge:

```text
stored labels/predictions: 1 = failure present; flip to Pass space first: x -> 1 - x
tpr = mean(pred | label = Pass); tnr = mean(1 - pred | label = Fail)
pass_rate = clip((observed_pass + tnr - 1) / (tpr + tnr - 1), 0, 1)
failure_prevalence = 1 - pass_rate
CI: percentile bootstrap, 20,000 replicates, numpy default_rng(seed=7); each replicate resamples the
    test (label, pred) pairs AND the unlabeled predictions independently; NaN replicates dropped;
    bounds = quantiles 0.025 and 0.975
warning (estimate still returned) when tpr + tnr <= 1.05:
  "the correction is unstable and the interval will be very wide. Improve the judge before trusting this estimate."
```

Worked example from the demo state: the observed Pass rate is 0.82, TPR = 36/38 and TNR = 10/12. The corrected Pass rate is 0.8369, so failure prevalence = 0.1631.

`failure_report` emits one entry per confirmed or frozen mode. Each entry carries the definition, requirement source, judge id and prompt hash, test TPR/TNR with Wilson intervals and class counts, a use/reject decision, labelled counts, raw and corrected prevalence with CI, examples, and eval-case candidates. Those candidates are the handoff to Module 3.

#### 1.2.3 Module 3: eval cases, CI and monitoring

**Eval cases** (eval_cases/README.md; homework/module-3/hw6.md:61-189). Shape:

```text
{id: "e-001", mode, input{role, user_id, message, followups[]?},
 initial_state{world: "reseed", fixture: null, assumes},
 expected{assertions[], checks[{check, ...}], judges{<accepted frozen judge>: "pass"}},
 kind?: "regression"|"capability", baseline_pass_rate?}
```

- Code checks decide exact facts. Accepted frozen judges decide meaning. CI never uses a new judge. Assertions are prose and give no reward.
- Five baseline runs classify each case. 5/5 makes a regression case. Anything less makes a capability case with `baseline_pass_rate` in {0.0, 0.2, 0.4, 0.6, 0.8}.
- Never change a classification to get the mix you want.

**The CI rule** (tests/eval/passk.py:97-144).
- A regression case blocks on any failed run.
- A capability case never blocks and is reported with pass@1/3/5 and pass^5.
- Only infrastructure errors may be rerun. A completed failure is evidence.

```text
pass@k = 1 - C(n - c, k) / C(n, k)    (1.0 when n - c < k)     capability: can it ever succeed?
pass^k = C(c, k) / C(n, k)            (0.0 when c < k)         reliability: does it succeed every time?
n = 8, c = 6: pass@1 0.75, pass@2 0.964, pass@4 1.0; pass^2 0.536, pass^4 0.214, pass^8 0
```

Two consequences:
- A clean 5/5 bounds the per-run pass rate above 0.55 at 95% confidence.
- r regression cases with per-run reliability p all pass five runs with probability p^(5r). For example, 0.99^50 is about 0.61, so flaky regressions will block.

**Monitoring** (monitoring/__init__.py:1-14) runs on two tracks:
- Code checks on 100% of traffic, because they are free.
- Frozen judges, off the serving path, on a plan made of a uniform random sample plus risk groups.

Only the uniform sample estimates prevalence. Risk groups are for inspection only (monitoring/sample.py:16-27). The default risk groups are: store-override topic, above-threshold refund, and negative signal (thumbs-down, re-ask or escalation). Scores are written with stable ids, so a rerun updates instead of duplicating (monitoring/write_scores.py:17-52):

```text
per-turn verdict:   name "<mode>_verdict",               value 0/1, score_id = sha256("<mode>|verdict|<trace_id>")[:32]
per-batch estimate: name "<mode>_corrected_prevalence",  trace_id null,
                    comment "95% CI <lo>-<hi>, raw <r>, n=<N>", score_id = sha256("<mode>|prevalence|<batch_label>")[:32]
```

The chart (monitoring/chart.py) is a 720 x 360 SVG of corrected prevalence per period, with CI whiskers and a dashed threshold line; y_max = 1.25 x max(threshold, every ci_high, every corrected). Crossing the threshold means: pull the flagged traces, do error analysis and mint a new eval case. Pipeline hygiene: re-run error analysis after model switches, prompt rewrites, new features and incidents, and re-validate judges on fresh labels.

**The label-convention trap.** One bit has three conventions in the repo:
- Mode label files and Langfuse scores use 1 = failure present.
- Judge statistics use Pass as positive (`judge_alignment`, `reporting._tpr_tnr`).
- HW5 exports use 1 = Pass, and `_load_labels` flips them on load.

`monitoring/correct.py` describes failure-positive TPR. The corrected value is the same in either convention, but TPR and TNR swap names. Brief D8 fixes Hoist: store 1 = failure present, report TPR/TNR with Pass as positive. Every column and UI label must name its convention.

#### 1.2.4 The method mapped onto the user's loop

| User's step | Method source | Repo mechanism | Hoist home (naming contract) |
|---|---|---|---|
| Checked, automatically | Phase 6 (code first), HW6 cases, Module 3 | Code checks on all traffic; scenario expected outcomes; frozen judges on a sample | `checks.py`, `golden.py` + `runner.py` + `grading.py`, `judges.py`, `monitor.py` |
| Checked, by people | Phases 4-5 | Sampled batches (uniform, cluster, dimension, depth, final uniform) read by a person | `sampling.py`, `annotations.py` |
| Explained | Phase 5 open coding | First-failure note in the reviewer's own words, pinned to the text | `annotations` rows anchored by span_id + quote (D13) |
| Grouped by cause | Phase 5 axial coding; HW4 Parts D-E | "Does fixing one fix the other?"; mode contract; depth-scan suggestions; complete present/absent matrix | `taxonomy.py`, `labels.py`, `proposals.py`, `requirements.py` |
| Fixed | Phase 6 spec failures; phases 7-10; HW6 | Prompt edit for spec gaps; validated judge for generalization failures; regression cases gate releases | `eval_cases.py`, release check on golden runs (M6) |
| Watched | Phase 11, Module 3 | Uniform-sample judging, Rogan-Gladen with bootstrap CI, threshold chart, new eval case on crossing | `monitor.py`, `stats.py`, Watch view |
| Less checking by hand over time | Whole loop | Coverage grows through more golden cases, more frozen judges and code checks | Watch view "share checked automatically vs by people" (M6) |

### 1.3 Architecture

#### Process model

There is one Python standard-library process with no dependencies, no auth, no locks and no ETag.
- `python analysis/server.py` binds 127.0.0.1:8020.
- The HW4 app runs on :8021 against `analysis/state/live` (review_app/README.md).
- `CARTWHEEL_ANALYSIS_STATE` selects the state directory. `server.STATE_DIR` is fixed at import (server.py:60), while `_state.state_root()` reads the variable per call. A value set only in `.env` can therefore send the API and the helpers to different directories.
- The coding agent is a separate actor. It reads `annotations.json` every 2 s and POSTs patterns, suggestions and samples (review-loop.md:25).

#### Endpoints

The whole API is one table mapping paths to files (server.py:66-93).
- GET returns the file, or a fixed default if the file is missing or unparsable. GET never fails.
- POST replaces the whole file atomically through a temp file with a fixed name, then rename (server.py:123-128).
- JSON bodies of 1 KB or more are gzipped when the client accepts it (server.py:112-120).

| Method | Path | Backing file | Default | Behaviour |
|---|---|---|---|---|
| GET | `/`, `/index.html` | `UI_DIR/index.html` | 404 JSON | UI_DIR is swapped to review_app by review_app/server.py:18 |
| GET | `/ui/<asset>` | `UI_DIR/<asset>` | 404 | Path-traversal guarded. Serves the uncommitted `label_suggestions.json` |
| GET/POST | `/api/samples` | `samples.json` | `[]` | The coding agent pushes batches. In live mode it holds the full mirror (over 10 MB) |
| GET/POST | `/api/annotations` | `annotations.json` | `[]` | POST runs the label pipeline below |
| GET/POST | `/api/graph` | `graph.json` | `{nodes:[],clusters:[]}` | Written by sync (PCA + k-means) |
| GET/POST | `/api/patterns` | `patterns.json` | `{}` | Written by the coding agent; the UI only reads it |
| GET/POST | `/api/suggestions` | `suggestions.json` | `[]` | The UI re-posts the remaining queue after each decision |
| GET/POST | `/api/context` | `scenario_context.json` | `{scenarios:{},traces:{}}` | Missing from the server docstring |
| GET/POST | `/api/runs` | `runs.json` | `[]` | Newest first |
| GET/POST | `/api/sync-status` | `sync_status.json` | `{enabled:false, in_progress:false, message:...}` | - |
| POST | `/api/sync` | - | - | 202 `{ok, started:true}`; 200 `{started:false}` if already running; 503 if Langfuse is not configured |
| OPTIONS | `*` | - | - | 204 with `Access-Control-Allow-Origin: *` |

Error responses:
- Unknown GET: 404 `{error:"unknown path: ..."}`.
- Unknown POST: 404 `{error:"cannot POST to ..."}`.
- Empty or invalid body: 400.
- PUT, PATCH and DELETE: 501 (the base handler).
- Any client may overwrite the files that sync owns (graph, context, runs, sync-status).

#### The label pipeline: `POST /api/annotations`

The browser posts the entire annotation array on every save (server.py:204-249).
1. `_new_structured_labels` (267-284) compares incoming ids with the ids on disk. Every new item with a `mode` and a label of 0 or 1 counts as an accepted decision.
2. If the `LANGFUSE_*` settings exist, each new decision becomes `create_score(name=mode, value=label, trace_id, data_type="NUMERIC", comment=note)` followed by `flush()`. There is no score id.
3. The file is overwritten.
4. `_persist_label_history` (287-323) appends `{id, trace_id, label, source, note, ts}` to `labels/<safe_mode>.jsonl`. It marks the previous live row for that trace `superseded_by=<new id>` by rewriting the whole file.
5. The response is 200 `{ok, count, langfuse_scores_written?}`.

If step 2 raises, steps 3-4 still run and the server returns 502 `{error, cached_locally:true}`. Those label ids are now on disk, so later saves never resend them. There is no outbox. Free-text notes and scenario comments never leave the file.

#### The live sync algorithm (live_sync.py:197-321)

1. A daemon thread starts if `LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST` are set and `--no-sync` is absent. It runs one backfill immediately, then repeats every `--sync-interval` (default 60 s, minimum 5 s). Exceptions go into the status file.
2. `run_once` takes a non-blocking lock. An overlapping call writes "sync already in progress".
3. It lists up to `CARTWHEEL_TRACE_SYNC_LIMIT`=2000 summaries, 100 per page. Each summary's fingerprint is `sha256(sorted JSON)[:20]`.
4. It fetches the full trace only when the fingerprint changed or the trace is among the newest `CARTWHEEL_TRACE_SYNC_RECENT`=25, which catches late spans. That is one GET per trace. A trace that fails normalization is fingerprinted and skipped.
5. It keeps only traces whose meta has a `scenario_id` (line 255). Ad-hoc chats never reach Trace Lab.
6. `merge_samples` upserts by the immutable trace id. It never deletes, keeps reviewer `flags` and sorts by (timestamp, trace_id).
7. Only after a successful read does it rewrite `samples.json`, `runs.json`, `scenario_context.json` and `graph.json`.
8. It writes `sync_status {in_progress, last_started_at, last_success_at, last_failed_at, added, updated, trace_count, error}` with a merge, so `last_success_at` survives a failure.

Sync never writes annotations, labels, patterns or suggestions. That separation is the guarantee that a refresh cannot erase review work (live_sync.py:3-6), and it is tested.

#### The normalizer record (normalization.py:370-457)

```text
{id, trace_id, timestamp, models[], input, output,
 observations[ObservationRecord], trace[Message], text,
 features{turn_count, tool_call_count, distinct_tools, has_retrieval, tokens},
 meta{role, store, session_id, prompt_version, scenario_id, run_id, session_inferred?},
 segments{}, metadata (attributes merged, redacted), permalink}
Message =  {role:"user"|"assistant", text}
        |  {role:"tool_call", name, arguments} | {role:"tool_result", name, content}
        |  {role:"model_call", label, name, input, output, model_call: ObservationRecord}
        |  {role:"reasoning_summary", label:"published reasoning summary", text, observation_id}
        |  {role:"observation", label, text}
ObservationRecord = {id, trace_id, parent_observation_id, type, name, start_time, end_time, model, provider,
                     operation, model_parameters (redacted), input, output, usage_details, cost_details,
                     total_cost, latency_seconds, time_to_first_token_seconds, status, status_message,
                     completion_start_time, metadata (redacted)}
```

Mapping rules (`_observation_message`, lines 227-276):
- Container spans (`cartwheel.session_message`, type AGENT, "agent workflow") emit nothing.
- A name containing "tool", or type TOOL, emits `tool_call` then `tool_result`.
- Model spans emit `model_call` plus any reasoning summaries. A model span is type generation or model, a name containing "openai.response" or "model", or a `gen_ai.request.model` whose operation is not `execute_tool`.
- Anything else with output emits `observation`.

Timeline order is the trace input as the user message, then observations sorted by `start_time` as strings, then assistant rows. `parent_observation_id` is carried but not used for ordering. `features.tokens` falls back to a word count when there is no trace-level usage.

#### Session and run identity

| Concept | Recorded value | Fallback (always labelled) |
|---|---|---|
| Conversation | `cartwheel.session_id`, a server-minted uuid4 at `POST /sessions` (server/app.py:145) | UI key `legacy::<run_id or "unassigned">::<scenario_id>`, shown as "legacy grouping: run + scenario" (index.html:548-554) |
| Turn | One Langfuse trace per user turn: root span `cartwheel.session_message` carrying session_id, user_role, user_id, prompt_version (sha256 of the system-prompt template, 12 hex), scenario_id, run_id (server/app.py:203-211) | Turn index and retry come only from `prepare_review` (substring match). Live sync never writes them |
| Run | `run-<UTC %Y%m%dT%H%M%SZ>-<uuid4 hex[:8]>`, made once per batch and reused on `--resume` (scenarios/runner.py:127-144) | `inferred-<namespace>`: no scenario -> manual; `pilot-` -> pilot; `support-` -> support; otherwise the scenario id minus its last segment. Named "<ns> (inferred)" (live_sync.py:92-151) |

The spec keeps 507 pre-session historical traces visibly inferred (interface_comparison.md:141-143).

#### Redaction and the chain-of-thought boundary

**Redaction** replaces the value of any key containing `authorization`, `api_key`, `apikey`, `password`, `secret`, `token` or `cookie` with "[redacted]" (normalization.py:15-30). It works only on metadata and model_parameters.
- It misses secret values in strings (Bearer tokens).
- It misses keys like `access_key`, `x-api-key` and `private_key`.
- It misses attributes stored as JSON strings.
- It never touches inputs, outputs or tool arguments.
- It wrongly hides `max_tokens` and `*_tokens`.

**Reasoning.** Only items of type `reasoning` with a provider-published summary become `reasoning_summary` rows (lines 85-116). Encrypted or raw reasoning is ignored. The spec requires the UI to state that private chain-of-thought is not shown, "so an absent reasoning transcript is not mistaken for a missing trace span" (interface_comparison.md:131-135).

### 1.4 The UI, view by view

#### Shell and header

All six tabs render from one in-memory `state`; no view fetches its own data. Each render rebuilds the view with `innerHTML`.

The sticky header (z-index 50) contains, left to right:
- `Cartwheel Trace Lab`
- Tabs: Conversations, Runs, Model calls, Map, Progress, Labels. The active tab is black with white text.
- A run select: "All runs" plus "<name> · <n> scenarios".
- `Sync now` and an 11 px status line.
- A 150 px search box, "session, scenario, or trace id".
- `←  i / n conversations  →`, shown on Conversations only.
- Counts: "<c> conversations · <t> traces · <notes> notes · <modes> modes · <s> suggestions" (index.html:353-373).

The status line reads one of:
- "offline cache"
- "syncing Langfuse…"
- "sync retry: <error>"
- "synced <time> · +<added> / ↻<updated>"
- "waiting for first sync"

Navigation (index.html:1373-1424):
- j, ArrowDown and ArrowRight go to the next conversation; k, ArrowUp and ArrowLeft go to the previous one.
- Search on Enter matches a substring of the session, scenario or trace id and opens that exact trace.
- `gotoTrace` works from every view and toasts "that trace is not in the current sample" when the trace is missing.

#### Conversations (the trace view)

Grouping: conversations are keyed by session, or by the legacy fallback. Inside a group, traces sort by turn_index, then attempt, then timestamp. Groups sort by scenario id with numeric-aware comparison, then by run id (index.html:555-582).

| Region | Content |
|---|---|
| Chip strip (max 980 px) | <ul><li>Scenario id in bold</li><li>`session: <id>` mono chip, or an amber flag "legacy grouping: run + scenario"</li><li>"<n> supporting traces"; "active trace: <id>"</li><li>role, store, prompt; the timestamp</li><li>`turn i/n`; `attempt N`, or a red `retry N`</li><li>One chip per model and one mono chip per tool; flags</li><li>A grey "Extra metadata:" box</li></ul> |
| Scenario card | <ul><li>"<id> · <scenario_group> · <role>"</li><li>Lines "Opening: …" and "Follow-up N: …"</li><li>A closed `<details>` "Reveal expected outcome and evidence": a red left-border box with the bold expected outcome (default "human judgment"), the reason, and the evidence JSON (default "No structured evidence source")</li><li>"Supporting evidence:" with one button per trace ("Turn N" or "Trace N", plus " · retry N"; the active one is indigo)</li><li>Without scenario context, only the id and the buttons show</li></ul> |
| Conversation comment | A textarea "Add a comment about this scenario as a whole…" and the button "Save scenario comment". It saves `{id:"s…", scenario_id, run_id, session_id, trace_id, note, source:"scenario_comment", ts}`. Saved comments are listed on #fffbeb with an amber border and a delete button. |
| Legend | Model decision · Published reasoning summary · Tool call · Tool output · Final answer |
| Timeline + margin | A 720 px timeline beside a 240 px margin column |

**Timeline blocks** (renderTrace and renderMsg, index.html:717-809).
- Each message is a `.step` card: 1 px border, 8 px radius, 20 px gap.
- A `tool_call` and the immediately following `tool_result` share one card, split by a hairline.
- Each message has a 4 px left border in its role hue and an 11 px uppercase role label.

| Role | Content |
|---|---|
| tool_call | Bold `name()` and a collapsed "arguments" JSON panel |
| tool_result | Pretty JSON. Collapsed behind "result (N chars)" when longer than 240 characters |
| model_call | <ul><li>Head: model; provider, or "provider unavailable"; operation; latency "X.XXs" or "latency unavailable"</li><li>"model request → published response · <status>"</li><li>Four collapsed panels: observable input, observable output, usage and request metadata (input/output/total tokens, cost, model_parameters, response_id, parent_observation_id), raw metadata</li><li>Labelled "model · tool decision" until a tool result has appeared, then "model · answer synthesis"</li></ul> |
| reasoning_summary | "Provider-published summary — not raw chain-of-thought", then the text |
| others | Markdown-lite text (bold, italic, code, newline to `<br>`). System messages at 55% opacity; boilerplate at 50% |

Only user, assistant, observation, system and reasoning text, the tool_result pre and the model "observable output" pre can be annotated. Tool arguments and model inputs cannot.

**Annotation flow** (index.html:911-989).
1. On mouseup, a selection inside one annotatable block is wrapped in a pending highlight (#fef08a) with `range.surroundContents`. A selection that crosses elements aborts silently.
2. A 280 px popover opens: "what went wrong? (Enter to save)", with the hint "first failure only, in your own words".
3. Enter saves. An empty note cancels, as do Escape and any click outside.
4. The saved record is `{id:"a"+Date.now()+rand, trace_id, scenario_id, run_id, session_id, quote, idx, note, ts}`. The toast says "saved · the agent is grouping your notes".

**Highlights and margin notes** (index.html:824-906).
- Each item is found by the first `textContent.indexOf(quote)` inside the block whose index equals `idx`. Overlapping ranges are skipped.
- Human highlights are #fde68a. Suggestions are #ddd6fe with a 2 px dashed #7c3aed underline.
- Margin cards are absolutely positioned at the highlight's top, sorted, and stacked with an 8 px gap.
- A human card shows the italic quote (80 characters), the note and a delete button on hover.
- A suggestion card shows the "agent · <mode>" tag and green accept / red dismiss buttons.
- Hovering a card outlines its highlight, and hovering a highlight rings its card.

#### Runs

Title "Scenario runs" (index.html:1218-1245). Help text: "Each batch remains available. Inferred runs contain older traces recorded before run IDs were added."

Each card shows:
- the name
- a red "inferred metadata" chip or an indigo "recorded run ID" chip
- the status (always "synchronized")
- "<started> → <ended>" ("start unavailable" or "end unavailable" when missing)
- "<n> scenarios · <n> traces · <models or 'model unavailable'>"

Clicking a card calls `selectRun(run, true)`. It writes `?run=` with `history.replaceState`, re-renders every view and opens Conversations. The empty state is "No synchronized runs yet." Trace Lab never scores or compares runs.

#### Model calls

Built from the visible samples' model_call messages (index.html:1247-1286).
- Tiles: model calls, input tokens, output tokens, cost (`$x.xxxx`), errors. Missing values show "unavailable".
- A pager shows 100 per page ("Page X of Y · N calls").
- Each card has a scenario/trace button, model, provider, latency, "<operation> · parent <id>", and collapsed "input and output" and "metadata" panels.
- Only the run filter applies.

#### Map

A 620 px canvas scatter of the PCA nodes (index.html:1319-1368).
- 8 cluster hues; annotated traces in orange #f97316.
- Traces in the visible sample get radius 6 with a black stroke; others radius 3.5.
- A click opens the nearest sampled node within about 14 px.
- Legend: trace / in sample / annotated. Empty state: "No projection yet (GET /api/graph is empty)."

#### Progress

| Block | Content |
|---|---|
| Review completion (HW4) | <ul><li>Four tiles: "<reviewed> reviewed traces / N", "conversation groups", "taxonomy modes", and "<done>/<required> structured judgments" or "not started"</li><li>Up to 25 rows "<scenario> · <trace12>" with "<k> incomplete", then "…and N more traces with incomplete judgments."</li><li>reviewed = traces with any annotation whose source is not `human_structured_label`; required = reviewed x modes (index.html:1047-1091)</li></ul> |
| Failure modes treemap | <ul><li>Blocks sorted by count, `flex-grow = max(1, count)`, min width 210 px</li><li>Each block: mono name, status pill (candidate amber, confirmed green, frozen indigo), count, definition, up to 6 clickable quotes or trace ids</li><li>Accepts the live map `{name:{…}}` or the final `{modes:[…]}`</li></ul> |
| Agent suggestions queue | <ul><li>Every row starts checked: mode in violet mono, quote clipped to 110 characters</li><li>Toolbar: "select all" (checked), "Accept selected", "Dismiss selected", "<n> pending · favor recall, prune the rest"</li><li>`bulkQueue` writes all decisions, then saves annotations once and suggestions once</li><li>`queueBusy` disables the buttons and pauses polling (index.html:1136-1194)</li></ul> |

#### Labels

Title "Structured labeling" (index.html:1452-1519).
- Rows are human-reviewed traces; columns are every mode in patterns, candidates included.
- Each cell is a segmented `absent | present` toggle.
- A human decision (the latest annotation for this trace and mode) shows solid: green #d1fae5 for absent, red #fee2e2 for present.
- With no human decision but an AI proposal `{trace_id, mode, suggested_label, evidence}`, the proposed button is dashed and reads "AI absent" or "AI present". The tooltip is "AI proposal — Present|Absent: <evidence>".
- A click appends `{id:"l…", source:"human_structured_label", mode, label, note:"<mode> present|absent", …}` and toasts "saved <mode>: present|absent".
- The row header is the scenario button, the trace id clipped to 16 characters, and " · legacy group" when inferred.
- Empty states: "No finalized modes yet. Open-code traces first; this table appears after your taxonomy has modes." and "No human-reviewed traces are available for structured labeling yet."

#### Visual grammar, feedback and polling

| Role | Hue | Background |
|---|---|---|
| user | #2563eb | #eff6ff |
| assistant (final answer) | #059669 | #fdf2f8 |
| tool_call | #b45309 | #ecfeff |
| tool_result | #7c3aed | #f0fdf4 |
| model_call | #db2777 | #f5f3ff |
| reasoning_summary | #c2410c label, #ea580c border | #fff7ed |
| observation / retrieval | #0891b2 | - |
| system | #6b7280 at 55% opacity | - |

Feedback is a black toast pill at the bottom right, shown for 2.6 s. There is no persistent error banner.

Polling (index.html:1527-1580):
- Every 2 s the page fetches suggestions, patterns, annotations, runs and sync-status.
- Every 15th poll, or when `last_success_at` changes, it also fetches samples, context and graph.
- A JSON signature decides whether to re-render every view. It includes `pollNumber` on heavy polls, so every view is rebuilt at least twice every 30 s.
- Remote annotations are merged add-only by id.
- GETs retry three times with 250 ms then 500 ms backoff. At boot, each of the nine reads has its own fallback.

#### The spec's 28 requirements and their build status

| REQ | Requirement (interface_comparison.md) | Status in review_app |
|---|---|---|
| 01 | Role colours, call+result container, long results collapse, highlights with margin notes | Built |
| 02-03 | Group by server-owned session; labelled run+scenario fallback | Built |
| 04 | Turn/retry badges, models and tools in the header; scenario conversation above the timeline | Partial: turn/retry exist only where `prepare_review` ran |
| 05 | Expected outcome collapsed | Built |
| 06-07 | Labels view gated on modes; one decision per pair; progress shows missing decisions | Built, but candidate modes are included and cells cannot be cleared |
| 08 | Append-only JSONL history plus Langfuse scores | Partial: writer exists; committed files are compacted; 225 scores went missing |
| 09-11 | Backfill and poll; upsert by trace id; sync keeps review work | Built and tested |
| 12 | A failed read never empties the cache | Mostly: `sync_index.json` is written before samples |
| 13-15 | Status display, Sync now, works offline | Built; a 503 is mis-toasted as "sync already running" |
| 16 | Only review data flows back | Built |
| 17-18 | Every run kept; one stable run_id per batch | Built (committed result files predate run_id) |
| 19-20 | Runs view with scenario source; run filter in the URL; "All traces" | Partial: no scenario_source; the option says "All runs"; `gotoTrace` leaves a stale `?run=` |
| 21, 28 | Inferred runs and 507 historical traces labelled | Built |
| 22 | Expected only from scenario JSONL; missing context marked unavailable | Partial: missing context is not marked |
| 23-24 | Handshake order from parent/child ids; model-call cards and view | Partial: order comes from `start_time` strings |
| 25 | Card fields incl. finish reason and response id; gen_ai vs app fields apart; raw panel; no secrets | Partial: no finish_reason; response_id shows the observation id; fields not separated |
| 26 | Model calls filters (run, scenario, role, model, status, time) and latency summary | Not built: run filter only |
| 27 | State the chain-of-thought boundary | Partial: shown only when a summary exists |

### 1.5 Persisted state and Aasim's actual results

#### Whose work is in `analysis/state/`

| File | Owner | Content |
|---|---|---|
| `annotations.json` | Aasim | 946 records over 143 traces, 2026-09-20 to 2026-10-04 |
| `labels/<6 modes>.jsonl` | Aasim | 133 rows each, 798 rows in total, compacted (no superseded rows) |
| `patterns.json` | Aasim | 6 modes, `human_signoff`, AgentDebug reconciliation |
| `sample_manifest.json` | Aasim | Batches with a reason per trace |
| `suggestions.json` | Aasim | `[]` at submission |
| `judges/`, `splits.json`, `store_traces.json`, `labels/unsupported_policy_claim.jsonl`, `demo_annotations.json` | Course seed (cdf1964, 2 Sep) | Synthetic demo of HW5; not Aasim's results |
| `samples.json`, `runs.json`, `scenario_context.json`, `graph.json`, `sync_*.json`, `label_suggestions.json`, `langfuse_label_sync.json`, `analysis/state/live/` | Runtime | Not committed. The live UI state cannot be reproduced from the repo |

Aasim's work stops at HW4. HW5 (judges), HW6 (eval cases) and HW7 (monitoring) are not done in this repo.

#### The reviewed sample

There are 133 traces:
- By role: 69 shopper, 34 merchant, 30 support.
- By group: 79 coverage, 54 challenge.
- By run: 130 from inferred pilot/support runs and 3 from a session-verification run.

| Batch (sample_manifest.json) | Required | Status | Method |
|---|---|---|---|
| cluster_representatives | 15 | verified | Original sampler's cluster representatives |
| initial_uniform | 15 | verified | 13 original uniform picks plus 2 fresh |
| role_dimension | 30 | reconstructed | 10 each of shopper, merchant, support |
| depth_search | 25 | reconstructed | Dispute, product-search and cancellation searches |
| final_uniform_stability | 15 | verified | Uniform without replacement, seed 4042026, 384 eligible after excluding 126 reviewed; found 1 new mode |
| outside any batch | 33 | - | Reviewed but unassigned |

The open codes number 136 free-text notes: 129 from the UI and 7 typed in chat. They cover 121 of the 133 traces.
- Only 6 notes say "no failure observed".
- 11 notes have an empty or "-" quote.
- The other 12 traces entered the matrix only through depth-scan decisions (9 accepted, 3 rejected).
- `session_id` is set on 2 of the 946 records.

#### The six modes

| Mode | Requirement | Evaluator | Present / 133 | Sample fraction |
|---|---|---|---|---|
| `non_customer_facing_policy_or_tool_details` | RESP-5, RESP-7 | LLM judge | 47 | 35.34% |
| `missing_decision_rationale` | RESP-5 | LLM judge | 28 | 21.05% |
| `redundant_tool_calls` | TOOL-R1 | LLM judge | 28 | 21.05% |
| `dispute_workflow_mishandled` | SCOPE-1, TOOL-2, TOOL-7, ESC-3, DISPUTE-1 | Code check | 10 | 7.52% |
| `store_policy_override_ignored` | POLICY-1 | LLM judge | 7 | 5.26% |
| `response_scope_mismatch` | RESP-9 | LLM judge | 7 | 5.26% |

Every mode has a definition, a boundary, 5-18 positive examples and 3 human-confirmed close negatives. In all, 36 close-negative candidates were recorded and 18 confirmed. For example, `store_policy_override_ignored` is "Present when a store-specific policy applies but the response evaluates or explains the case using Cartwheel's platform default instead of the store override". Its boundary sends a correct store-policy answer with a missing public link to the first mode.

Taxonomy history (`patterns.json` human_signoff):
- Merged: `missing_public_policy_link` + `internal_tool_details_exposed` became the first mode; `refund_tool_used_for_dispute` + `dispute_window_not_enforced` became `dispute_workflow_mishandled`.
- Removed: `ambiguous_product_not_disambiguated`.
- Clarified: `wrong_tool_for_intent` became `refund_tool_used_for_dispute`.

The final batch surfaced `response_scope_mismatch`, written first as spec revision RESP-9. Its first three proposed close negatives turned out to be positives and were replaced (review_summary.md:83-88).

The AgentDebug comparison (arXiv 2509.25370) records 5 decisions and one omission that was not added. Its `created_from` lists 7 ids that do not exist in `annotations.json`; nothing validates provenance.

#### The 798 labels and where they came from

| Source of the final decision | Cells | Detail |
|---|---|---|
| `accepted_label_suggestion` (agent proposal approved in chat) | 723 | <ul><li>openrouter/openai/gpt-5.5: 515 (463 absent, 52 present), all stamped 2026-09-30T18:33:18Z, 505 distinct evidence sentences</li><li>codex-taxonomy-review: 123, all absent, one shared sentence</li><li>codex-stability-audit: 85 (76 absent, 9 present), 8 distinct sentences</li></ul> |
| `human_structured_label`, taxonomy backfill of confirmed positives | 47 | Written from the mode examples |
| `accepted_suggestion` (depth scan) | 12 | All `redundant_tool_calls` |
| `human_structured_label`, confirmed in chat | 10 | - |
| `rejected_suggestion` | 3 | All `redundant_tool_calls` |
| `human_structured_label`, close-negative backfill | 2 | 10 more such backfills sit on traces outside the 133 |
| `human_structured_label`, a click in the Labels grid | 1 | Three clicks within 4 s on one cell (b68ff907…: present, absent, absent) |

There are 127 present cells. 723/798 (90.6%) of the final decisions are AI proposals approved in bulk. The Labels grid decided one cell. Clicking cell by cell did not scale to 798 cells. There is no proposer code anywhere in the repo; the proposals came from an outside coding agent, identified only by the `model` field.

#### Audit findings and reconciliation

The stability audit (`patterns.json` `human_signoff.stability_gap`):
- Detected 2026-10-04 08:45:39.
- Found 85 missing decisions for the original five modes on 17 newer traces.
- Found 18 bulk-approved `response_scope_mismatch` absences that "conflict with RESP-9 evidence".
- The 85 proposals were approved at 08:48:55, 196 s after detection. The label rows equal the proposals, so none were changed.
- The 18 absences were reviewed and all confirmed absent. The gap was marked resolved at 08:57:39.

The Langfuse reconciliation found 573 matching scores, wrote the 225 missing ones and read back 798. The receipt `langfuse_label_sync.json` is not committed. Seven notes carry `decision_origin: "human_provided_after_ui_sync_failure"`.

Only `non_customer_facing_policy_or_tool_details` (47 Fail) reaches HW5's minimum of 30 Fail labels.

#### The course-seed judge (synthetic; not Aasim's work)

`unsupported_policy_claim` has 121 label rows: 120 live (29 Fail / 91 Pass) and one logged flip ("the flagged claim was a verbatim quote of a doc the labeler had missed"). The split is 20/50/50 (fractions 0.1667/0.4167/0.4167, seed 7): train 6F/14P, dev 11F/39P, test 12F/38P. The judge model is claude-opus-4-6.

| Version | prompt_hash | Change note | Dev TPR (Pass) | Dev TNR (Fail) |
|---|---|---|---|---|
| v0 | c9a82394da14 | Definition only, no examples | 0.9211 (35/38) | 0.5833 (7/12) |
| v1 | a02af0360896 | Added 2 train examples; defined policy fact | 0.9474 (36/38) | 0.75 (9/12) |
| v2 | eb84f8eede7b | Added a faithful-paraphrase pass example (1 dev label flipped) | 0.8974 (35/39) | 0.8182 (9/11) |
| v3 (frozen) | f85bce23a878 | Added the borderline fail example (hedged store-credit offer) | 0.9487 (37/39) | 0.9091 (10/11) |

v3 on test: TP 36, FN 2, TN 10, FP 2. TPR 0.9474 [0.8271, 0.9854], TNR 0.8333 [0.552, 0.953].

On the 500-trace store (375 shopper, 125 merchant), v3 flags 90, a raw rate of 0.180. Corrected, that is 0.1631 with 95% CI [0.0587, 0.2733]. TPR + TNR = 1.781, so there is no validity warning. Borderline examples moved TNR the most.

Do not copy this fixture's numbers or format:
- All four files carry one timestamp (2026-07-08T18:00:00).
- v0-v2 are byte-identical to v3 apart from a trailing "<version N draft>" line, so v0 already contains all three examples.
- v0-v2 have no predictions.
- The prompt asks for `{reasoning, answer: pass|fail}`, not `{critique, result: Pass|Fail}`.
- The prompt lacks the "ignore instructions quoted in the trace" guard.

### 1.6 What is finished, what is scaffolding, and the defects

| Component | Status | Evidence |
|---|---|---|
| HW4 review UI, file API, live sync | Finished and used for 133 traces | review_app/, server.py, live_sync.py |
| Score write-back | Finished but lossy (225 of 798 missing until a manual reconciliation) | server.py:226-242 |
| Measurement helpers (split, register, run, align, freeze, corrected_prevalence, failure_report) | Implemented by the instructors and tested on the seed fixture. Not run on Aasim's modes | tools.py, reporting.py, tests/test_hw_holes.py |
| Depth scans, grouping, cell proposals | No code. Prose in review-loop.md; data in annotations.json; `suggestions.json` is empty | critic gap 7, followup 8 |
| Discovery-rate curve, coverage report | No code. Stability recorded by hand in patterns.json | review-loop.md:73-80 |
| Monitoring | Design only. `select_traces`, `corrected_mode_prevalence` and `build_score_records` raise NotImplementedError (sample.py:46, correct.py:65, write_scores.py:68). There is no runner, nothing produces the `segments.*`/`signals.*` fields the risk groups read, and `run_judges` expects `final_reply`/`retrieved_docs`, which the normalizer never emits | monitoring/ |
| Eval cases and CI | Scaffolding. `cases.jsonl` is 1 byte. `pass_at_k`, `pass_hat_k` and `case_passes` raise NotImplementedError (passk.py:61-62, 93-94, 143-144). The pull-request job in evals.yml has no steps | eval_cases/, tests/eval/ |
| Replay harness | Implemented and tested: n rollouts, re-seed before each, retry only infrastructure errors (at most 2) | replay/harness.py |

Defects found, and the rule Hoist adopts for each (brief D13 unless stated):

| # | Defect | Evidence | Effect | Hoist rule |
|---|---|---|---|---|
| 1 | Heavy poll re-renders every view with `innerHTML` | index.html:1557-1578 | Closes open `<details>`, wipes a half-typed comment, re-checks unchecked queue rows | Targeted updates; never wipe open panels or unsaved text |
| 2 | Keyboard guard checks only INPUT | index.html:1421 | j/k in the comment textarea jumps conversation and loses the draft | Ignore INPUT, TEXTAREA, SELECT and contenteditable |
| 3 | POST failures are silent | `api.post` ignores `r.ok` (index.html:474-478); 502 cached_locally (server.py:235-241) | 225 scores missing; labels never retried | Every failed write is shown and retryable; one DB transaction per write |
| 4 | Anchors are message index + first `indexOf(quote)` | index.html:824-851 | Breaks when messages re-order; repeated phrases hit the first match; highlighting strips markdown and line breaks; cross-element selections abort silently | Anchor = span_id + quote, computed from source text |
| 5 | Every annotation becomes a margin note | index.html:855-906 | Labels and comments pile up at the top of the margin | Only span-anchored notes go in the margin |
| 6 | A dismissed suggestion renders as a yellow human highlight | index.html:1017-1031, 844 | Looks like a confirmed failure | Distinct style for rejected suggestions |
| 7 | Bulk approval with a pre-checked queue | index.html:1139-1146; 723/798 | 18 conflicting absences and 85 missing decisions found late | No pre-selection; per-suggestion evidence; bulk-approval audit (M4) |
| 8 | Whole-array POST, add-only merge | index.html:513-527, 1548-1555 | Last writer wins; deletes come back; unsafe for two reviewers | Per-row writes, append-only events, 409 on a stale base (D12) |
| 9 | Redaction by key substring only | normalization.py:15-30 | Secrets in values, inputs and tool arguments pass through; token counts are hidden | Hoist redactor over question, answer and every span input/output |
| 10 | No auth, CORS `*`, every path writable | server.py:148-152, 217-249 | Any open page can read traces and write labels | `current_principal` + `_act`; service CORS allowlist |
| 11 | "Reviewed" = any non-structured annotation | index.html:1054-1058 | Accepting a suggestion or a chat approval puts a trace in the matrix | Reviewed = has a human annotation row |
| 12 | Labels columns include candidate modes; cells cannot be cleared | index.html:1454, 1502-1505 | Premature labelling | Confirmed/frozen modes only; a clear action that writes an event |
| 13 | Order by `start_time` strings; "tool decision" vs "answer synthesis" by position | normalization.py:305-319; index.html:724-729 | Spec REQ-23/24 not met | Order by seq + parent_id; label from data (tool_calls present) |
| 14 | `sync_index.json` written before samples; one failed trace fetch aborts the run; scenario_id filter | live_sync.py:249-258 | Silent gaps | No sync layer (D4) |
| 15 | Test lock covers only `judge_alignment("test")`, not `run_judge("test")`; `require_not_frozen_for_iteration` unused; `split_labels` reshuffles on rerun | tools.py:536-539; guards.py:68-76; tools.py:402-416 | Test predictions can be produced before freeze; reruns leak dev into test | Enforce lock, freeze and persisted split in the DB (M5) |
| 16 | Frozen-judge parse fails open | replay/rollout.py:413-417 | Unparseable output counts as Pass unless the word "fail" appears | Strict JSON schema; an unparseable verdict is an error |

### 1.7 What carries over to Hoist

#### Carries over unchanged

- The one rule, and the AI's role as proposer only.
- First-failure open coding with "no failure observed".
- The four batch kinds and the final-15 stability check.
- "Does fixing one fix the other?".
- The eight-field mode contract.
- The complete present/absent matrix.
- Suggestions as retrieval signals, with rejected ones kept.
- Sample fraction versus prevalence.
- Label minimums 30/30, and the split by conversation 20/40/40 refused below 10 per class.
- Train-only few-shot examples; critique before verdict; judge input hygiene.
- Pass-positive TPR/TNR with Wilson intervals; never agreement alone.
- `prompt_hash = sha256(model + "\n" + prompt)[:12]`.
- Freeze one-way, then test once.
- Rogan-Gladen with a 20,000-replicate bootstrap (seed 7) and the 1.05 validity warning; correction only with test rates.
- Five baseline runs; regression versus capability; pass@k and pass^k.
- Two-track monitoring; estimation from the uniform sample only; stable verdict ids; threshold crossing leads to a new eval case.
- The visual grammar, the collapsed expected outcome, the chain-of-thought notice and "unavailable" for missing provider fields.

#### Changes because the store is Postgres inside svc-deepagents

| Cartwheel mechanism | Hoist replacement | Brief | Hoist integration point |
|---|---|---|---|
| Langfuse canonical; `live_sync` mirror into JSON files | Read-through normalizer over `plenum_cafm.agent_trace_turns` / `agent_trace_spans` | D4 | DA/src/agents/trace.py:44-109 (DDL), :846-860 (`get_turn`) |
| Trace id; late spans upsert | `turn_id` (`"turn-"` + 16 hex) is immutable. The tree is written once at flush; a re-flush updates only the turn totals and inserts a second span set | D4 | trace.py:298, :737-776 |
| Sync now, last sync, added/updated, retryable error | Auto-refresh poll, "Updated hh:mm - N new", Refresh control, visible error state | D4 | - |
| `session::<cartwheel.session_id>` (server uuid4) | `agent_trace_turns.session_id`. The browser mints it; report cards use `report-<card>-<epoch>`; Correct and re-run sends the shared literal `rerun`. Synthetic and shared ids are flagged and grouped explicitly | D4 | apps/frontend/src/logic/sessions.js:53-57; apps/frontend/src/logic/corrections.js:258 |
| run_id span attribute; inferred runs | `golden_runs` rows plus `turn_meta.golden_run_id/golden_case_key/rep`; live traffic shown as "All live traffic"; no inferred runs | D6, D13 | - |
| `scenarios/*.jsonl` expected outcomes | `golden_sets` / `golden_cases` from an Excel/CSV upload, joined by case key | D2, M3 | - |
| `annotations.json` whole-array POST | `annotations` + `annotation_events`, 409 on a stale base | D12, M2 | - |
| `labels/*.jsonl` + Langfuse score | `labels` rows with a `superseded_by` chain, in one transaction | D12, M4 | - |
| `patterns.json` + prose `human_signoff` | `modes` + `mode_events` (create, edit, merge, split, retire, restore) | M4 | - |
| `suggestions.json`, `/ui/label_suggestions.json` | `proposal_runs` + `proposals` with accept/dismiss | M4 | - |
| `sample_manifest.json` | `sample_batches` + `sample_items` (reason and seed per item) | M2 | - |
| `judges/*.json`, `splits.json` | `judges`, `judge_splits`, `judge_predictions`, `judge_runs` | M5 | - |
| Monitoring Langfuse scores | `monitor_batches` + `monitor_verdicts` | M6 | - |
| stdlib server, no auth, CORS `*` | FastAPI routes under `/api/trace-lab`, `current_principal` + `_act`, service CORS allowlist | D7 | DA/src/api/main.py:40, :192-198, :255; DA/src/api/routes/traces.py:30 |
| DocETL / LiteLLM judge runner | AsyncAnthropic with a JSON schema; model from `TRACE_LAB_JUDGE_MODEL`, else `COMPLIANCE_SUMMARY_MODEL` | D8 | DA/src/agents/skill_lab.py:241-246; DA/src/config.py:28, :110 |
| Background thread plus a file lock | `jobs` rows with heartbeat, resumable; golden runs stepped from the browser | D9 | - |
| Startup DDL pattern | Versioned migrations with verify/apply | D3 | Contrast trace.py:117-131 (CREATE IF NOT EXISTS on every boot) |
| numpy bootstrap and PCA | svc-deepagents does not declare numpy | - | DA/pyproject.toml:7-33 (open issue) |

Two map recommendations are overruled by the brief:
- C1b and C4 suggested dropping or deferring the Map view. D2 and D13 keep it.
- C5 and C6 suggested failure-positive reporting. D8 keeps Pass-positive reporting with 1 = failure storage.

#### What Hoist needs that Cartwheel never had

| Need | Why Cartwheel never needed it | Hoist fact (verified) | Brief |
|---|---|---|---|
| Golden-run identity | The runner stamped run/scenario ids on spans, and rollouts re-seeded a world | The only safe replay writes no trace at all: `trace.begin` returns None under REPLAY (trace.py:292-300) | D6(d), M3 |
| Several reviewers at once | One local reviewer; last writer wins | Company admins review in parallel | D12 |
| Evidence that outlives the 90-day purge | Langfuse kept everything | Every service start deletes spans older than `TRACE_RETENTION_DAYS` (trace.py:133-148, main.py:114-121). Payloads are cut at 16,000 characters (trace.py:46-47) | D5 |
| Company scoping | Single user, localhost | `_scope_sql` (trace.py:779-791), `_act` (routes/traces.py:30) | D7 |
| A grader against expected answers | Judges detect one failure mode; the Runs view only groups traces | Precedent: `skill_lab.JUDGE_PROMPT` (skill_lab.py:205) | D2, M3 |
| Safe replay | Each rollout re-seeds SQLite | <ul><li>REPLAY refuses every non-GET, read-only POSTs included (http_client.py:231-233; replay_guard.py:25)</li><li>It does not stop LangGraph checkpoints (`hitl_enabled = True`, config.py:243; main.py:126-139; orchestrator.py:872-876)</li><li>It does not stop memory use-count UPDATEs (chat_memories.py:275-279)</li></ul> | D10 |
| Provenance and channel | Sync kept only scenario-tagged traces | Organic chat is mixed with templated UI prompts, report cards and re-runs | D6(a) |
| Gate rewrites made visible | No post-loop rewriting | EL-UDR and contract compose rewrite the answer after the loop and discard the verdict (orchestrator.py:7409-7425, 8333) | D6(c) |
| Billing exclusion | No billing | Platform cost sums every turn row (svc-operations-intelligence/src/engines/platform_cost.py:99-105) | D10 |
| The asker's own words | The question was the user message | A later turn-input row overwrites `question` (trace.py:321-329) | D6(b) |
| Eval data hidden from the chat | The agent could not read the review files | svc-udr `DENIED_TABLES` lists only 4 auth tables (svc-udr/src/services/scope.py:99-104) | D15 |
| Live data drift | Fixed seeded world | Golden answers age on a live database | M3 "Verified on" column |

### 1.8 Terms used in the rest of this document

| Term | Meaning |
|---|---|
| Golden scenario set | An uploaded set of cases with a known right answer, an evaluation kind (objective or judgment) and an evidence source |
| Golden case / case key | One row of the set; the key joins answers to expectations, never fuzzy matching |
| Golden run | One batch of a golden set at k reps under one run id; one session per case, follow-ups scripted |
| Open code | A reviewer's free-text note on the first failure, or "no failure observed" |
| First failure | The earliest step that violates a requirement or makes a later failure materially more likely |
| Failure mode | A binary, named rule that meets the eight-field mode contract |
| Close negative | A trace that looks like a positive but does not have the failure |
| Suggestion / proposal | An AI retrieval result; becomes a label only when a person accepts it |
| Sample fraction | A mode's share within a reviewed, non-uniform sample; never called prevalence |
| Corrected prevalence | A judge's raw rate on a uniform sample, corrected by Rogan-Gladen with held-out TPR/TNR, reported with a bootstrap CI |
| Risk group | A stratum judged for inspection only; never used for estimation |
| Criteria drift / saturation | The reviewer's standard shifting over time / the point where new traces stop producing new modes |
| Regression / capability case | An eval case that passed 5/5 at baseline and blocks releases / a case that passed fewer and is only tracked |
| Pass-positive | Reporting convention: TPR = agreement on Pass, TNR = agreement on Fail; stored flags use 1 = failure present |

---

## Part 2 - Hoist today, the earlier attempt, and the gap

This part records what exists on branch `eval-2` (commit 1684810, clean working tree) before any Trace Lab code is written. It then covers what the earlier attempt in `stash@{0}` taught us, how each Trace Lab concept lands in Hoist under the brief, and what is missing. The gaps are ordered by how they block the loop "checked -> explained -> grouped by cause -> fixed -> watched". Every Hoist line number was checked on `eval-2` on 15 Oct 2026. Facts taken from memory notes or demo material are marked as such.

| Prefix | Means |
|---|---|
| `DA/` | `apps/backend/cafm-connector-service-final/svc-deepagents/src/` |
| `OI/` | `apps/backend/cafm-connector-service-final/svc-operations-intelligence/src/` |
| `UDR/` | `apps/backend/cafm-connector-service-final/svc-udr/src/` |
| `FE/` | `apps/frontend/src/` (tests live in `apps/frontend/test/`) |
| `SNAP:` | The earlier build in `git stash@{0}`, read from the scratch snapshot. Reference only. |
| `DEMO/` | `/Users/hussain/Desktop/hoist/eval-demo/` |
| MEM | Hussain's memory notes. These are recorded facts, not re-checked in code. |

### 2.1 Trace capture today

#### 2.1.1 How a turn becomes a span tree

`DA/agents/trace.py` records one span tree per orchestrator turn. The agents never call it. It listens where data already flows, and it writes once, off the answer path, when the turn's closing activity row arrives. Every listener is wrapped in try/except, so recording never breaks an answer. If `ensure_tables()` failed at startup, `_ready` is False and nothing is recorded. The API then answers `available: false` (`DA/agents/trace.py:117-130`).

| Input | Hook | What it adds | Evidence |
|---|---|---|---|
| Activity rows | `activity_log.fire()` calls `trace.on_activity()` synchronously, before it schedules the DB insert | `orchestrator/turn` input opens the Turn. The next `orchestrator/turn` output or error row closes the root and flushes. Tool rows pair by name. Other agent/stage rows become `router`, `llm` or `stage` spans under an automatic `agent` group per agent. `fire_exchange` model calls carry `{system_prompt, user_message, params}` in and `model_output` out. | `DA/agents/activity_log.py:87-123, 336-366`; `DA/agents/trace.py:315-368` |
| Cost ledger | `llm_cost.Ledger.record()` calls `trace.on_llm_cost()` | Dollars and tokens on the matching span. A `sub_agent` entry (a whole engine run) lands on that engine's group span. It fires only when `llm_cost.begin_turn()` opened a ledger. | `DA/agents/llm_cost.py:141-158, 195-213`; `DA/agents/trace.py:409-453` |
| LangChain events | The WebSocket stream's `on_chat_model_start/end`, plus `trace.callback_handler()` passed to the REST loop (`_invoke`, group "orchestrator loop") and to every sub-agent or engine run | `llm` spans named `model`. Their input is a summary, `{messages, last_human_message, tool_results_since_last_human}`, and their output is `{content, tool_calls:[{name,args}]}`. Tool spans come from the callbacks. | `DA/agents/orchestrator.py:7372-7374, 8278-8307`; `DA/agents/meta_tools.py:44-49, 518, 536`; `DA/agents/trace.py:504-551, 601-682` |
| Explicit hooks | `on_prompt`, `on_plan`, `on_step_open/close`, `on_sql` | A `prompt assembled` stage span (system prompt, conversation, working set, memories, page context). The plan span, which also sets `route = '<mode>:<targets>'`. Plan steps, `check sN`, `step gates`, `verify claims` and `verify figures`. `db` spans with the SQL, params and the first 50 rows. | `DA/agents/trace.py:456-501, 554-598`; `DA/agents/orchestrator.py:952, 6415-6437`; `DA/agents/planner.py:501-565`; `DA/agents/udr_agent.py:253, 259`; `DA/agents/ontology_qa.py:1657-1660` |

The lifecycle, in order:

1. Identity is fixed when the Turn is built. `organization_id` is `caller_organization_id` (the acting company, including a superadmin's view-as), else the principal's company. `user_id` and `email` come from `caller_principal` (`DA/agents/trace.py:221-242`). `turn_id` is the activity turn id, `'turn-' + 16 hex`, started once per HTTP or WebSocket request (`DA/agents/activity_log.py:63-77`).
2. A second input row on the same open turn rewrites `question` and the root input (`DA/agents/trace.py:327-329`). This causes defect DF1.
3. The output or error row closes the root with `ok`, `error` and the latency from the row, then calls `_flush` (`DA/agents/trace.py:330-336`). Rows that arrive after the flush are dropped.
4. `_flush` ends each open group at its last child, closes every other open span, sorts by `seq`, and schedules `_write` as an unreferenced asyncio task (`DA/agents/trace.py:708-734`).
5. `_write` runs one transaction. It first runs `INSERT … ON CONFLICT (turn_id) DO UPDATE` for the turn, touching only the answer, timing, counts, cost, `ok` and `error`. Then it runs one `INSERT` per span (`DA/agents/trace.py:737-774`). Any failure loses the whole turn and only logs `trace.write_failed`.
6. Totals. `llm_calls` counts `llm` and `router` spans; `tool_calls` counts `tool` spans. When a ledger ran, `cost_usd = max(ledger usd, span sum)`, and `cost_complete` needs both to be complete (`DA/agents/trace.py:685-705`). An `agent` group span carries the whole sub-agent run's cost. Never sum cost over all spans: use `kind IN ('llm','router')` or the turn total.

#### 2.1.2 The two tables (verbatim, `DA/agents/trace.py:52-110`, with `{TURNS}`/`{SPANS}` substituted)

```sql
CREATE TABLE IF NOT EXISTS plenum_cafm.agent_trace_turns (
    turn_id TEXT PRIMARY KEY, session_id TEXT, organization_id UUID, user_id UUID, email TEXT,
    question TEXT, answer TEXT, route TEXT, started_at TIMESTAMPTZ NOT NULL, ended_at TIMESTAMPTZ,
    latency_ms INTEGER, llm_calls INTEGER NOT NULL DEFAULT 0, tool_calls INTEGER NOT NULL DEFAULT 0,
    cost_usd NUMERIC(12,6), cost_complete BOOLEAN, input_tokens INTEGER, output_tokens INTEGER,
    cache_read_tokens INTEGER, models JSONB, tools JSONB, ok BOOLEAN NOT NULL DEFAULT TRUE, error TEXT,
    feedback_rating TEXT, feedback_comment TEXT, feedback_by UUID, feedback_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS plenum_cafm.agent_trace_spans (
    id UUID PRIMARY KEY, turn_id TEXT NOT NULL, session_id TEXT, organization_id UUID, parent_id UUID,
    seq INTEGER NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, agent TEXT, model TEXT,
    started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ, latency_ms INTEGER, input JSONB, output JSONB,
    input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cost_usd NUMERIC(12,6),
    ok BOOLEAN NOT NULL DEFAULT TRUE, error TEXT);
CREATE INDEX IF NOT EXISTS ix_trace_turns_org_time ON plenum_cafm.agent_trace_turns (organization_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_trace_turns_user_time ON plenum_cafm.agent_trace_turns (user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_trace_spans_turn ON plenum_cafm.agent_trace_spans (turn_id, seq);
```

- Both tables are created at every svc-deepagents start. There is no migration file and no foreign key.
- There is no index on `agent_trace_turns.session_id`, and none on `agent_trace_spans.started_at`.
- The turn row is written once and never deleted. Only `feedback_*` changes later. That single slot is the only label store today.
- `seq` can repeat or leave gaps when ledger-only spans are absorbed (`DA/agents/trace.py:381-406`).
- In `hoistra_test` only, `agent_trace_turns` also has the columns `channel` and `asker_scope`, added by the earlier build (MEM). eval-2 code never reads or writes them, and they exist nowhere else.

#### 2.1.3 Span kinds

`_kind_for` (`DA/agents/trace.py:303-310`) decides the kind of activity-row spans. The listeners create the rest.

| kind | Created by | Parent | Payload |
|---|---|---|---|
| `turn` | First turn-input row | None (seq 0) | in `{message, mode}`, where mode is `stream`, `rest`, `rerun` or null; out `{answer}` |
| `agent` | Automatic group per agent name; engine groups from callbacks | Root, or the tool/step open at the time | No payload. Cost is the whole sub-agent run. |
| `router` | Agent name contains `router`, or stage is `router` or `classify_engine` | Root | Exchange: `{system_prompt, user_message, params}` -> `model_output` |
| `llm` | Loop and sub-agent model calls (`model`); exchanges with a model or a stage in analyst/planner/plan/review/summary/prompt/classify_document/extract_document; ledger-only calls | Agent group, or the open tool/step | Loop: summary in, `{content, tool_calls}` out. Exchange: prompt in, `model_output` out. |
| `tool` | Tool activity rows (stream: agent `tool:<domain>`, on the root); callbacks | Root or group | in = args; out = tool output, JSON-parsed |
| `stage` | Other activity stages; `prompt assembled` | Group or root | The stage payload |
| `db` | `on_sql` | Open tool/step, else root | `{sql, params, label}` -> `{row_count, rows[:50], truncated}` |
| `plan` | `on_plan` | Root | `{source, question}` -> `{plan, rejected}` |
| `step` | `on_step_open` | The plan span | `{kind, target, ask, depends_on, why}` -> `{output, tool_calls, ms}`; also `check sN`, `step gates`, `verify claims`, `verify figures` |

#### 2.1.4 Bounds

| What | Limit | Evidence |
|---|---|---|
| Span input or output | `BOUND = 16000` JSON characters. Dict strings over 4,000 are cut first; if still over, the value becomes `{'_truncated': first 16000}`. | `DA/agents/trace.py:47, 155-176` |
| Question / answer | 4,000 / 20,000 characters | `DA/agents/trace.py:328, 756` |
| Span name | 200; db, step and tool names 120 | `DA/agents/trace.py:492, 580, 612, 768` |
| Rows kept on a db span | 50 (`SQL_ROWS`) | `DA/agents/trace.py:477-498` |
| Card outputs kept whole, in memory only | 20 per turn | `DA/agents/trace.py:274-283` |
| Feedback comment | 2,000 | `DA/agents/trace.py:919` |
| Activity payload | `ACTIVITY_LOG_PAYLOAD_CHARS`, default 64,000 | `DA/agents/activity_log.py:133` |

#### 2.1.5 Read scope and API

`DA/api/routes/traces.py` resolves the acting company with `_act` (`:30-36`). A non-superadmin who names another company gets 403. With no company named, the caller's own company applies. The scope SQL (`DA/agents/trace.py:779-791`):

```python
if org:
    parts.append("organization_id = CAST(:org AS uuid)")
elif principal.role != "superadmin":
    parts.append("FALSE")
if not principal.is_admin:
    parts.append("user_id = CAST(:uid AS uuid)")
return (" AND ".join(parts) or "TRUE"), params
```

So an admin reads the company's turns, and a superadmin reads the company they act for, or every company when not acting. A plain user reads only their own turns.

The routes are:
- `GET /api/traces/turns`: limit 1..500 plus offset, newest first, 300-character previews, no total.
- `GET /api/traces/turns/{id}`: one turn with all its spans.
- `GET /api/traces/stats`
- `POST /api/traces/turns/{id}/feedback`
- `POST /api/traces/turns/{id}/rerun`
- `GET /api/traces/export`: NDJSON, admins only.

The chat depends on the list shape. A reopened chat calls `traceTurns({session_id, limit: 100})` and pairs runs to answers by order and question (`FE/logic/sessions.js:258, 564-565`). Trace Lab must not change that response.

#### 2.1.6 Retention, and the 31 Dec 2026 deadline

`purge()` runs once per service start, right after `ensure_tables()`. It deletes every span with `started_at < now() - TRACE_RETENTION_DAYS`, default 90 days, and spares nothing. Turn rows stay forever (`DA/agents/trace.py:133-146`; `DA/api/main.py:117-121`). No compose or env file sets `TRACE_RETENTION_DAYS`. The DELETE has no `started_at` index, so it scans the span table at every boot.

`trace.py` first landed on 2 Oct 2026 (commit b3b1ab7). Spans from 2 Oct become deletable at the first service start on or after about 31 Dec 2026, or earlier if the retention is lowered. This holds for every deployment that runs eval-2 or older code against the same database. That includes the two Azure Container Apps that share hoistra_test (MEM).

Any Trace Lab note, label or golden result that cites an October turn loses its span evidence then, unless both of these hold:
- every writer runs the D5 keep-list;
- Trace Lab rows copy the evidence they cite (D5).

#### 2.1.7 What is not captured, compared with Trace Lab

| Trace Lab field | Hoist today | Evidence |
|---|---|---|
| Full request of each model call | Loop and sub-agent calls keep only a summary (message count, last human message, number of tool results). Exchanges keep `{system_prompt, user_message, params}`. The nearest substitute is the `prompt assembled` span, which exists on general-loop turns only, with strings cut to 4,000 when the dict exceeds 16,000. | `DA/agents/trace.py:456-473, 504-523`; `DA/agents/orchestrator.py:952` |
| finish_reason, response id, provider, model parameters, time to first token | Not stored. `on_model_end` reads only usage, model name, content and tool calls. Parameters exist only inside exchange `params`. | `DA/agents/trace.py:526-551` |
| tool_call_id | Not stored. A model's tool request is paired with its tool span by name and order. | `DA/agents/trace.py:545, 340-352` |
| Cache-write and reasoning tokens | Not stored; only `cache_read_tokens` | `DA/agents/trace.py:52-106` |
| Prompt version | None | — |
| Asker role and building scope | Not stored. `Principal` has them, but the Turn keeps only org, user and email. | `DA/agents/trace.py:235-241`; `DA/services/principal.py:38-47` |
| Channel, origin, page | None. Page context survives only in the purged `prompt assembled` span and in `agent_activity_log` turn-input payloads (stream: the full `extra_context`; REST: the first 300 characters). That log is never purged. | `DA/agents/orchestrator.py:7521-7522, 7922-7925` |
| run_id, scenario id, repetition | None | — |
| Retry or rerun link | `rerun_of` exists only in the activity payload. A rerun is recognisable only by the question prefix `Re-run with corrections (` or root `input.mode = 'rerun'`. | `DA/agents/orchestrator.py:6517-6518`; `DA/agents/rerun.py:211-214` |
| Turn index in its session | Not stored. `chat_turns.turn_no` exists, but `chat_turns` has no `turn_id`. | `DA/services/chat_threads.py:72-87` |
| Answer cards | `answer` holds the prose only. Card payloads live only in tool spans (bounded, then purged) and in `chat_turns.tools` (the first `compliance_response` and `compliance_pipeline`, ≤256 KB). `_write` never stores the turn's `tool_calls` list. | `DA/agents/trace.py:737-774`; `DA/services/chat_threads.py:243-258` |
| Correctness | `ok` means "did not raise". A failed turn stores the error summary as its answer, and a HITL stop stores `gate_interrupt`. | `DA/agents/trace.py:330-336`; `DA/agents/orchestrator.py:8310-8318` |
| Reasoning | No chain-of-thought is stored anywhere. gpt-5.6 runs with `reasoning_effort="none"`, and spans hold content plus tool calls only. | `DA/llm_factory.py:147-178`; `DA/agents/trace.py:543-545` |

#### 2.1.8 Defects on eval-2 (trace capture)

| Id | Defect | Effect | Evidence |
|---|---|---|---|
| DF1 | The question is overwritten on REST turns. `run_stateful` opens the turn with the asker's words. When the general loop answers, `_invoke` fires a second input row whose `latest_user_message` is the wrapped prompt (page context, runtime, conversation, memories, then `**Current user message:**`). | `question` holds up to 4,000 characters of prompt for report cards, crons, page-asked questions and the REST fallback. Old rows stay polluted. The asker's words survive in the activity payload `message`. | `DA/agents/orchestrator.py:899-914, 928-947, 7363-7370, 7521-7522`; `DA/agents/trace.py:327-329` |
| DF2 | `/resume` turns have no owner. The route has no principal dependency, and `resume()` calls `_invoke(Command(...))`. | The question is `''` and `organization_id`/`user_id` are NULL. Company admins cannot see these turns; only a superadmin who is not acting can. | `DA/api/routes/workflow.py:891-919`; `DA/agents/orchestrator.py:606-615, 7894` |
| DF3 | A phantom `llm` span on compliance planning. Two `stage='plan'` input rows fire, and the output row closes only the second. | The first span is closed empty at the flush, unpriced: `llm_calls` +1 and `cost_complete` false. Inferred from code, not seen on stored data. | `DA/agents/orchestrator.py:2857-2860, 2974-2978`; `DA/agents/trace.py:358-359` |
| DF4 | Chat tools read every company's traces. `query_table` reads `plenum_cafm.<any table>` with no organization filter. `table_building_clause` adds nothing for admins, nor for tables without building, vendor, asset, site or meter columns. | Every admin, and any user allocated to at least one building, can read up to 100 rows of all companies' questions, answers and emails. `get_schema` lists the tables. Separately, svc-udr's admin-only custom SELECT rewrites only `plenum_cafm.` names, so a future `plenum_tracelab.*` table would be readable across companies. Its deny list holds 4 auth tables. | `DA/agents/udr_agent.py:23, 41-90, 188-245`; `DA/services/principal.py:275-313`; `UDR/services/scope.py:99-104, 241-252`; `UDR/api/routes/tables.py:35-37` |
| DF5 | Post-loop answer rewrites are untraced. REST `_invoke` runs `contract_answer.compose` and then `evaluate_udr_response`, and stream runs EL-UDR. Both run before the output row, and `answer, _ =` drops the verdict. | `answer` is the post-rewrite text, and no span shows the draft, the verdict or the change. compose calls `AsyncAnthropic` with no `llm_cost`, and EL-UDR calls `self._llm.ainvoke` with no callbacks, so their cost is missing too. | `DA/agents/orchestrator.py:7410-7426, 8332-8338`; `DA/agents/contract_answer.py:425`; `DA/agents/udr_response_evaluator.py:266, 310` |
| DF6 | Session ids are not server-owned. The browser mints `randomUUID()` or `hs-…`. Report cards use `report-<card8>-<epoch>`, uploads use `docs-…`, and Correct -> Re-run sends the literal `rerun` when no session is open. | Re-runs from different users share one session id and one `chat_threads` row owned by its first creator. API callers can pass any id. | `FE/logic/sessions.js:53-56`; `OI/engines/reports/cards.py:456`; `FE/api/documents.js:26`; `FE/logic/corrections.js:258` |
| DF7 | One engine run twice in a turn | The group span is cached by name, so the second run's spans hang under the first group. | `DA/agents/trace.py:251-258, 512-519, 608-611` |
| DF8 | Fragile write | The write is an unreferenced `create_task`. One failed INSERT loses the whole turn, writes in flight are lost at shutdown, and each span is a separate INSERT. A Trace Lab insert added to this transaction would roll back the trace on failure. | `DA/agents/trace.py:732, 737-774` |
| DF9 | REST general-loop turns run without a ledger. `begin_turn` is called only at `DA/agents/orchestrator.py:4972, 6514, 6978, 8049`. | Ledger-only costs (sub-agent totals, exchanges) are lost, and `cost_complete` can read false. | `DA/agents/llm_cost.py:206-213` |

### 2.2 The Hoist Traces page today

#### 2.2.1 What it shows

The page lives at Administration > Hoist Traces, view `traces`.

**List view**
- Range chips (Today/7d/30d/90d, default 7d), status chips and a search box.
- Five tiles: Turns, LLM cost, Latency p50, Failed %, Rated up/down.
- Cost-by-day bars, a by-model table and a top-10 tools table.
- A "Runs" table of up to 200 turns. Each row is one answer, not a batch run.

**Run view**
- Header metrics.
- "Was this answer right?" thumbs, with a comment that saves on blur.
- A waterfall tree built by `buildTree`.
- The selected span's input and output. A db span shows its SQL, params and rows.
- The final answer.

Sources: `FE/screens/Traces.jsx:66-154, 156-291`; `FE/logic/tracesPage.js:42-63, 146-215`.

The page's only entry is the Navigator's admin list, which exists only while `state.role === 'admin'` (`FE/logic/integrations.js:62, 88-95`). The page itself has no role check: `isTraces` is `signedIn && view === 'traces'` (`FE/logic/tracesPage.js:159`). Server scope is the only gate.

#### 2.2.2 Frontend architecture Trace Lab must follow

**Controller and mixins**
- `HoistraLogic` holds one flat state.
- Domain method mixins are `Object.assign`ed onto its prototype; `tracesPageMethods` is one of them (`FE/logic/HoistraLogic.js:383`).
- Later mixins win on a name clash.

**Vals**
- `renderVals()` spreads each page's pure `*Vals(c)` into one flat `vals` object (`FE/logic/renderVals.js:1993`).
- Screens are pure functions of `vals`, mounted by flags (`FE/App.jsx:85`).
- There is no URL router. The only deep-link precedent is the one-shot `?cron=` read at boot.
- Each page needs a unique key prefix. `tl` is free: no `tl[A-Z]` key exists in `FE/`.

**Persistence**
- `setState` is the single choke point. It saves the reload slice on every change (`FE/logic/HoistraLogic.js:367-380`).
- The slice has a `VIEWS` allow-list, which includes `traces` (`FE/logic/session.js:45`).
- Each persisted sub-key must be validated in `readSlice` and written in `buildSlice` (`FE/logic/session.js:84, 313`).
- No `tp*` key is persisted.

**Loaders**
- Every loader has the same shape: set `xLoading`; then set the data plus `xLoadedAt`, `xAvailable` and `xCanManage`.
- On error: `if (isStaleScope(e)) return;`, then `xErr` (`FE/logic/tracesPage.js:79-92`).
- The vals expose loading only before the first load, so a refresh keeps the old rows (`FE/logic/tracesPage.js:160`).
- Company-scoped keys must be cleared in `resetLiveData` (`FE/logic/core.js:135`) and in `authSignedOut` (`FE/logic/auth.js`).

**API client**
- `apiFetch` (`FE/api/client.js:137`) adds the bearer, refreshes and retries once on 401, and rejects replies issued for an earlier company (`staleScope`). It scrubs internal names from errors and supports `raw` downloads and `form` uploads.
- Every svc-deepagents call appends `orgOverride()`, which sends `organization_id` only during a superadmin's view-as (`FE/api/deepAgents.js:53`).

**Styling**
- Inline style objects built from `tokens.css` variables only.
- Warm paper ground. The orange accent `#FF5A1F` is used only for the live, the actionable and the primary button.
- Status colours are never decorative. Figures use `ui-monospace` with tabular numerals.
- Cards use `--color-surface`, `--shadow-sm` and a 10px radius. Tabs are a 2px underline (`apps/frontend/docs/visual-system.md:9-17`).
- `--marker` (`#FFD400`) and `--marker-tint` already exist "as a marker fill only … Never as type" (`FE/styles/tokens.css:73-76`). These are the right tokens for Trace Lab's yellow highlights.
- CSS classes are only for stateful, hover or motion surfaces (`runtrace.css`, `hover.css`).
- Icons are Phosphor, from a CDN. Dark tokens exist, but nothing sets `data-theme`.

**Tests**
- `npm test` runs `node --test "test/**/*.test.mjs"` (149 files). `npm run build` runs `check-bundle` (`apps/frontend/package.json:8, 10`).
- Pure vals are tested with a fake controller (`test/tracesPage.test.mjs:43-49`).
- Controller tests stub `window`, `fetch` and `WebSocket`.

**noInternalNames**
- The guard scans `src` with comments stripped. It fails on `svc-*`, `plenum_cafm`, raw `GET /api` routes, `UDR`, `RAG`, `doc-rag`, `deep-agents`, `SKILL.md` and `arq`.
- `tracesPage.js` and `Traces.jsx` are on its SKIP list as "super-admin-only surfaces" (`test/noInternalNames.test.mjs:24-43`). In fact any company admin can open the page. D7 makes company admins reviewers.

#### 2.2.3 Defects on the page

| Id | Defect | Evidence |
|---|---|---|
| DF10 | A reload on Traces shows an empty page: zero tiles, "No runs in this range", nav badge "…". `componentDidMount` has no traces branch, and `tpLoad` runs only from `tpOpen`, the filters or Refresh. | `FE/logic/core.js:19-63`; `FE/logic/tracesPage.js:74-97`; `FE/logic/session.js:45` |
| DF11 | `tp*` state survives a company switch and sign-out. Neither `resetLiveData` nor `auth.js` names a `tp*` key. Because loading shows only before the first load, the old company's rows render while the new read runs, and they stay if it fails. | `FE/logic/core.js:135`; `FE/logic/tracesPage.js:160` |
| DF12 | Admin thumbs overwrite the asker's rating and auto-teach a company memory. The run view's thumbs and comment (which saves on blur) POST `/feedback`. That endpoint is one overwrite-in-place slot, writable by anyone in scope. A thumbs-down with a comment of 12+ characters that is not a one-word verdict becomes a company-wide `correction` memory. `tpRate` also silently saves a draft comment. | `FE/screens/Traces.jsx:87-94`; `FE/logic/tracesPage.js:116-135`; `DA/agents/trace.py:914-926`; `DA/api/routes/traces.py:82-89`; `DA/agents/auto_teach.py:24-26, 60-66` |
| DF13 | The chat's thumbs wipe the stored reason, because `crRate` sends `comment=null`. Chat ratings are never seeded from storage. | `FE/logic/corrections.js:303-311` |
| DF14 | The list is capped at 200 rows with no paging, while the tiles count the whole range. Rows are 300-character previews with no total. | `FE/logic/tracesPage.js:85`; `DA/agents/trace.py:836-840` |
| DF15 | `tpOpenTurn` has no request token and no stale-scope check, so a slow reply for run A can overwrite run B. | `FE/logic/tracesPage.js:98-111` |
| DF16 | Export uses a raw `fetch`: no 401 refresh, no stale-company guard, and unscrubbed errors. The server loads up to 20,000 turns and runs one span query per turn, all on one connection. | `FE/api/deepAgents.js:96-106`; `DA/agents/trace.py:929-954` |

### 2.3 Platform facts that constrain the build

| Fact | Evidence | What it forces |
|---|---|---|
| **Store pattern.** Each store has a `TABLE`, a `_DDL` list (`CREATE … IF NOT EXISTS`, with columns added only through `ALTER … ADD COLUMN IF NOT EXISTS`), `ensure_tables()` in one `_get_engine().begin()`, and a module `_ready` flag. Writers no-op when not ready. Startup order: ingest batches, activity log, chat_threads, chat_memories, trace + purge, checkpointer, orchestrator. | `DA/api/main.py:95-147`; `DA/services/chat_threads.py:55-113` (ALTER at `:88`) | Trace Lab keeps the `_ready` contract (stores down -> 503, chat unaffected). DDL moves to versioned files (D3). |
| **No flag disables startup DDL or the purge.** `docker-compose.azure-safe.yml` has no svc-deepagents block. Local `DB_URL` is `${PLENUM_DB_DSN}?sslmode=require`. `skill_overlays.refresh` runs DDL lazily on the first `run_stateful` turn in each process. | `docker-compose.single-url.local.yml:221`; `DA/services/skill_overlays.py:63-91` | Every rebuild is DDL on a shared DB. Default `TRACE_LAB_SCHEMA=verify` (D3). |
| **One engine.** Raw `text()` SQL through one asyncpg engine (pool 5 + overflow 10, pre-ping, recycle 1800 s), shared with live chat, with `CAST(:p AS uuid/jsonb)` binds. The checkpointer has its own psycopg pool. | `DA/database.py:34-50` | `db.py` reuses `_get_engine`. Batches run sequentially or with low concurrency. The D10 tripwire hooks this one engine. |
| **Identity.** No users table. `current_principal` forwards the bearer to OI `/api/auth/me` and caches the answer for 60 s per token. `Principal(user_id, email, organization_id, role, can_ingest, building_ids, buildings)`. Roles are user, admin and superadmin; `is_admin` = admin or superadmin. | `DA/services/principal.py:1-12, 34, 38-51, 91-132` | D7 maps onto these three roles. "Platform" means a superadmin who is not acting. |
| **Acting company.** `traces._act` sets the named org or the caller's own; anyone but a superadmin who names another org gets 403. `workflow._resolve_acting_org` returns None for the caller's own org. | `DA/api/routes/traces.py:30-36`; `DA/api/routes/workflow.py:318-330` | Every Trace Lab route uses the `_act` style. Platform-wide views are named explicitly. |
| **LLM clients.** The chat uses OpenAI through `llm_factory.create_chat_model(OPENAI_MODEL)`. The Settings and compose default is `gpt-4o-mini`; the local `.env` uses `gpt-5.6-terra` (MEM). Anthropic is called directly: `skill_lab._ask_json` uses `AsyncAnthropic` with `output_config {effort: low, format: json_schema}` and records cost through `llm_cost.record`. The model is an env override, else `COMPLIANCE_SUMMARY_MODEL`, default `claude-sonnet-5` ($2/$10 per MTok). | `DA/config.py:28, 45, 110-115`; `docker-compose.single-url.local.yml:222`; `DA/agents/skill_lab.py:235-254`; `DA/agents/llm_cost.py:30` | D8 judges reuse `_ask_json`, a different model family from the chat. |
| **Background work.** In-process asyncio tasks only, with no worker in svc-deepagents. The skill lab's one-run lock is per process. Access tokens last 30 minutes, and svc-deepagents cannot mint one. The gateway cuts responses idle for 300 s (MEM). | `DA/agents/skill_lab.py:107, 431-456`; `OI/config.py:322-325`; `DA/services/principal.py:1-6` | D9: a jobs table with heartbeats, and browser-stepped golden runs that return 202 and poll. |
| **HITL default on.** `hitl_enabled = True`, and nothing but `.env.example:61` sets it, so the Postgres checkpointer saves every loop thread. | `DA/config.py:243`; `DA/api/main.py:127-139`; `DA/agents/orchestrator.py:872-878, 894-896` | Replays write checkpoints unless a MemorySaver twin graph is used (D10). |
| **Databases.** Local svc-deepagents writes the database named by `PLENUM_DB_DSN`: hoistra_test. Two Azure Container Apps running older code share it (MEM). The repo calls `plenum_agent` PRODUCTION. eval-2 contains no `plenum_eval` code (`git grep`: 0 files). | `OI/db/tools/close_orphaned_approvals.py:31`; followup 3 | hoistra_test is the pilot; plenum_agent is a separate, later approval. Azure's purge is outside our code. |
| **The earlier build's tables.** 21 `plenum_eval` tables (20 `eval_*` plus `trace_deleted`) and the `channel`/`asker_scope` columns already exist in hoistra_test from its startup DDL. No approval is recorded for `eval_tries` or `trace_deleted`. | MEM (eval-loop-feasibility-8-oct) | D1: not used and not touched. New schema `plenum_tracelab`; no name reuse. |
| **Cost per answer.** A chat answer costs about $0.065-0.09. The earlier Excel judge cost about $0.007 per answer (7 cases for $0.048), and its check judge about $0.002. One pass of the 29-row Bishopsgate set is about $2.10-2.70 plus judging and takes 30-60 minutes; k=3 is about $7. | `DA/agents/skill_lab.py:27` ("measured $0.09 per question"); MEM; followup 6 | D11 estimates before a run, plus caps. |
| **Dependencies.** svc-deepagents has no openpyxl, pandas, numpy or scikit-learn. python-multipart is present. | `apps/backend/cafm-connector-service-final/svc-deepagents/pyproject.toml:7-33` | `.xlsx` parsing, PCA + k-means and a 20,000-replicate bootstrap are pure Python, or new dependencies. |
| **Tests.** svc-deepagents tests on eval-2 have no `conftest.py` and no env-gated SQL tests. `asyncio_mode = "auto"`. | `svc-deepagents/pyproject.toml:50-51` | The `TRACE_LAB_TEST_DB_URL` harness is new (D14). |

#### 2.3.1 The answer paths

| Path | Entry | Users | How the turn is recorded | Planner | Post-loop gates | Ledger |
|---|---|---|---|---|---|---|
| `stream()` `DA/agents/orchestrator.py:7896` | WS `/api/workflow/ws/{session_id}` | Chat UI | Input row with mode `stream` and the full `extra_context` (`:7922-7925`). `workflow_completed` carries `turn_id` (`DA/agents/session_workspace.py:1042`). | Yes; only here (`:8100`) | EL-UDR (`:8332-8338`); compose only on the contract engine path (`:7291`) | `:8049` |
| `run_stateful()` `:7499` | `/run-stateful`, `/run-stateful-with-files` | REST fallback, report cards, crons, skill lab | Mode `rest`, `context[:300]` (`:7521-7522`). The question is overwritten when the loop answers (DF1). The REST response has no `turn_id` (`DA/api/routes/workflow.py:136-173`). | No | compose then EL-UDR in `_invoke` (`:7410-7426`) | None on the loop path |
| `run()` `:7473` | `/run` | API callers | Via `_invoke`, mode null, raw message; a fresh LangGraph thread per call | No | compose + EL-UDR | None |
| `rerun_turn()` `:6490` | `POST /api/traces/turns/{id}/rerun` | Correction drawer | Mode `rerun`, question `Re-run with corrections (…): <q>`, `rerun_of` in the payload (`:6517-6518`). Writes `chat_turns` with route `rerun`. The route auto-teaches every correction (`DA/api/routes/traces.py:126-130`). | Replays the recorded plan | Depends on the path | `:6514` |

`resume()` (`:7866`) is a fifth, unowned path (DF2).

A golden run that wants the answers people actually see must drive `stream()`. Only `stream()` plans multi-part questions, and only it returns `turn_id`.

#### 2.3.2 What `replay_guard` does on eval-2

`REPLAY` is one ContextVar, `READ_METHODS = {GET, HEAD, OPTIONS}` (`DA/agents/replay_guard.py:22-25`).

| Writer | Under REPLAY | Evidence |
|---|---|---|
| `trace.begin` | Blocked. No turn row, no spans, no Platform cost. | `DA/agents/trace.py:292-297` |
| `chat_threads.record_question/answer` | Blocked. So a replayed follow-up has no `last_turn` for the router or planner. The checkpointer still feeds the loop its earlier messages. | `DA/services/chat_threads.py:133-134, 173-174`; `DA/agents/orchestrator.py:4004-4042` |
| `chat_memories.learn_soon` | Blocked | `DA/services/chat_memories.py:413-417` |
| `activity_log.record` | Blocked | `DA/agents/activity_log.py:268-270` |
| `http_client.request`, non-GET | Refused with `ReplayRefusedWrite`. There is no allow-list and no record of what was refused. | `DA/http_client.py:230-233` |
| LangGraph checkpoints | Not blocked | `DA/config.py:243`; `DA/agents/orchestrator.py:872-878` |
| `chat_memories.recall` counters | Not blocked. It UPDATEs `use_count` and `last_used_at` on up to 6 memories per answer. | `DA/services/chat_memories.py:270-275` |
| The `forget …` shortcut | Not blocked. It soft-deletes memories, and "forget everything" loops over every visible memory. Reached through `run_stateful`'s preflight. | `DA/services/chat_memories.py:476-488`; `DA/agents/orchestrator.py:1024` |
| Raw httpx writers | Not blocked: migration, schema-mapper and Fiix tools POST around `http_client`. | `DA/agents/migration_agent.py:103-107`; `DA/agents/schema_mapper_agent.py:186-206, 286-290`; `DA/agents/fiix_agent.py` |
| `skill_overlays` DDL | Not blocked | `DA/services/skill_overlays.py:63-91` |

Reads that are wrongly refused:
- svc-udr `/api/tables/{t}/records/search` (`DA/agents/udr_agent.py:664-666`);
- `/api/tables/query/select` (`:745-747`);
- schema-mapper `/api/graph/expand-query` (`DA/agents/udr_hybrid_tools.py:57-59`). When it is refused, the tool silently falls back to vector search.
- several ops-intel read POSTs (followup 1).

svc-udr `/api/agent/query` (`DA/agents/udr_agent.py:483-485`) runs CRUD and must stay denied.

Replayed answers to data questions therefore come out worse than live ones. This is the gap D10 closes.

### 2.4 The earlier attempt (`stash@{0}`), as lessons only

#### 2.4.1 What it built

It was built 8-14 Oct 2026 on branch `eval` and never committed. It used Hoist's own trace tables and no Langfuse.

| Store (`SNAP:DA/evals/`) | Tables (`plenum_eval`) | What it did |
|---|---|---|
| `reviews.py` | `eval_reviews`, `eval_review_events`, `eval_hidden` | One review per (turn, reviewer): correct or incorrect, with a note required on incorrect. An optional span pin. `picked` = queue, second, chosen or reference. A `base` timestamp gives 409 on a stale write. Blind tabs: To review, Second look, From the Excel, Disputed. |
| `references.py` | `eval_reference_sets`, `eval_references`, `eval_reference_runs`, `eval_reference_results` | Excel/CSV upload. One Anthropic call matched a live answer to a row and judged it. The match was stored as a review by a fixed pseudo-reviewer. |
| `tries.py` | `eval_tries` | Re-answered a wrong answer with the reviewer's fixes through `rerun_turn`, under REPLAY plus a two-path read-POST allow-list |
| `modes.py` | `eval_failure_modes`, `eval_mode_labels`, `eval_mode_events`, `eval_label_events`, `eval_dispositions` | A taxonomy with merge chains, rescope and dispositions |
| `checks.py` | `eval_checks`, `eval_check_runs`, `eval_check_results` | Versioned judge and code checks; a 70/30 dev/test split by conversation hash; TPR/TNR |
| `monitor.py` | `eval_watch_runs`, `eval_watch_results` | A "Watch now" sample and a weekly Rogan-Gladen estimate with a Wilson-corner range |
| `lessons.py` | `eval_lessons`, `eval_lesson_events` | Prompt lessons behind `EVAL_LESSONS_ENABLED` |
| `trace.py` changes | `trace_deleted`, plus `channel` and `asker_scope` columns | Delete from Traces, a keep-list purge, and the "asker's own words win" fix |

Size:
- about 5,080 lines of backend eval code and routes (47 new routes);
- about 3,480 lines of frontend;
- 17 frontend test files with 195 tests;
- on 13 Oct the suites were green: 1,683 backend and 2,145 frontend tests (MEM).

`stash@{0}` also holds hoist-f5's company-boundary work, three chat fixes and other sessions' edits (followup 2).

#### 2.4.2 What proved solid (keep the idea; write the code fresh)

| Idea | Where it was | How Trace Lab keeps it |
|---|---|---|
| Per-reviewer rows | `UNIQUE (turn_id, reviewer_id)` on `eval_reviews` | `annotations` per reviewer (D12) |
| Append-only histories enforced by the database | `plenum_eval.append_only()` raises `'<table> is append-only'` on UPDATE or DELETE (`SNAP:DA/evals/reviews.py:131-149`). TRUNCATE is not blocked. | Every `*_events` table gets the trigger (D12) |
| Stale-write guard | `base` = the `updated_at` the form read; a mismatch gives 409 with the current row (`SNAP:DA/evals/reviews.py:210-222, 338-354`) | `base` = the last version seen -> 409 (D12) |
| Parser safety caps | 5 MB, 2,000 rows; DOCTYPE/ENTITY refused; zip parts ≤50 MB; `.xls` refused (`SNAP:DA/evals/references.py:50-61, 187-307`) | `golden.py` keeps the caps |
| Judge contract | JSON-schema output, strict reply validation. A bad reply is an error, never a verdict, and is retried next run (`SNAP:DA/evals/references.py:327-410`; `checks.py:292-304`). | `grading.py` and `judges.py` (D8), with critique before verdict |
| SQL test harness | Opt-in `EVAL_TEST_DB_URL`, a guard on the local DB name, TRUNCATE between tests | `TRACE_LAB_TEST_DB_URL` (D14) |
| Company-scope guard tests | A frontend contract test that every trace/eval request names the acting company; backend reads through `trace._scope_sql` | The same contract tests for `api/traceLab.js` and the `trace_lab_*` routes |
| Blind second look and fair-sample provenance | Others' verdicts hidden until you save yours; `picked` plus a per-reviewer md5 order | Blind until saved; sampling provenance in `sample_items` |

#### 2.4.3 What failed or was missing

1. **The Excel judge over-matched live answers and was never validated.**
   - One call both matched and judged. It saw every reference answer while matching, was told to read follow-ups with the earlier conversation, and had no way to abstain (`SNAP:DA/evals/references.py:312-325, 373-386`).
   - Live on 14 Oct it matched:
     - C2 "When does the Apex Lifts contract end?" -> row 9;
     - D3 "Draft an email to Ostley…" -> row 8;
     - E2 "When does their contract end?" and a terse "all of them" -> row 14.
   - Sources: MEM; `DEMO/FULL-TEST-PLAN.md:116`.
   - Its only accuracy signal was "Disputed", which people saw non-blind.
   - D2 removes the matching step entirely: golden runs are joined by case id.
2. **Machine verdicts were stored as human reviews.** `picked='reference'` rows in `eval_reviews`:
   - took answers out of To review;
   - counted as Reviewed;
   - fed the failure-mode worklist;
   - became implicit negatives for every check (`SNAP:DA/evals/checks.py:467-468`).

   In Trace Lab, machine grades live in `golden_results` and `judge_predictions`. They reach `annotations` or `labels` only through a person's acceptance.
3. **It never re-ran the golden questions.** The Excel judged only organic answers in the period on screen. There was no regression run and no Runs view.
4. **In-process jobs were lost on restart.** Reference runs, checks, watches and tries were asyncio tasks that read "interrupted" after 30-60 minutes (`SNAP:DA/evals/references.py:61`).
5. **Startup DDL ran on a shared DB.**
   - 21 tables were created by `CREATE IF NOT EXISTS` on every boot.
   - `ALTER TABLE … ADD COLUMN IF NOT EXISTS source_file` ran on every boot with no `lock_timeout` (`SNAP:DA/evals/references.py:94`).
   - A runtime ALTER of `agent_trace_turns` was retried every 300 s.
6. **Tries were untraced.** REPLAY suppresses `trace.begin`, and a try kept only `{applied, tools, plan}` (`SNAP:DA/evals/tries.py:262-278`). "Why did turn 2 say 238?" could not be answered.
7. **The Trace Lab views were missing:**
   - a chronological timeline (it had a tree plus one span);
   - highlights and margin notes (it had one note plus one buried span pin);
   - a present/absent label grid;
   - suggestions;
   - batch Runs;
   - a Model calls view;
   - the chain-of-thought notice.
8. **It departed from the HW5 method:**
   - no train split;
   - a reversible "in use" toggle instead of a one-way freeze;
   - the verdict came before the evidence (`failure_present` is listed first in `VERDICT_SCHEMA`);
   - no label minimums;
   - dev rates could be used for the correction;
   - failure-positive TPR;
   - Wilson corners instead of the bootstrap.
9. **It had three ways to change live answers:** auto-teach on thumbs-down, "Teach the chat" from a try, and approved lessons. The brief adds no lesson or teach path.

#### 2.4.4 Real-world findings it surfaced

| Finding | What happened | State on eval-2 | Consequence for Trace Lab |
|---|---|---|---|
| Date binding | ISO days and timestamps were bound as TEXT, and asyncpg refused them. Every "in July 2026" question failed. | The fix (`_as_day`/`_as_time`) is only in the stash and the running local image. eval-2 still binds strings (`DA/agents/ontology_qa.py:1219-1230`). | Golden runs on eval-2 will fail these questions for a known reason (P2) |
| Whole-day bounds | A plain day used as the upper bound on a timestamp dropped the last day: 758 August work orders instead of 759. | Stash only | As above |
| 30-attribute cap | `MAX_ATTRIBUTES = 30` ranked yes/no flags last, which hid `first_fix` and `recall`, so the first-time fix count (603) could not be computed. | Stash only; eval-2 `DA/agents/ontology_qa.py:149` | As above |
| Stale Excel answers vs the DB | July work orders: 786 in the DB, 783 in the workbook. The blocked fire vendor is Pennard (added 29 Sep), not ProudCastle (row 8). `vendor_contracts`: 66 rows, 22 distinct, £787k, against the Excel's £463k across 15. | n/a (data) | Golden cases carry `Verified on` and `Source`. A failed case may mean the expected answer is wrong. |
| Data and engine defects | The contracts view has no end dates. The CP17 certificate has no building link. Work-order status-track returns 500 on a wo_code. The energy engine cannot total a month. Adding sub-meters to the incoming meter gives about 1.97 GWh instead of 717,338 kWh. Demo site B-001 has a NULL company and leaked "34 floors" (hoist-f5). | Mixed | Many "wrong answers" are data faults, not prompt faults (see open issues) |
| Time-bound rows | LOLER is due 18 Oct and F-gas expires 29 Oct 2026; several rows go stale within weeks. | n/a | `Verified on` plus re-validation |

### 2.5 Concept map: Trace Lab -> Hoist Trace Lab

| Trace Lab concept | In the reference repo | In Hoist Trace Lab (per the brief) |
|---|---|---|
| Trace | One Langfuse trace per user turn (root span `cartwheel.session_message`) | A `plenum_cafm.agent_trace_turns` row. `turn_id` is the immutable trace id. Read through `trace_lab/normalize.py` with no copy (D4). Provenance lives in `plenum_tracelab.turn_meta` (D6a, M0). |
| Observation | Langfuse spans and generations, ordered by start time | `agent_trace_spans` rows (`parent_id` + `seq`), turned into timeline entries by `normalize.py`. New kind `gate` (D6c). Fields Hoist lacks are shown as "unavailable" (D6). Conversations and Model calls views (M1). |
| session_id | Server-owned `cartwheel.session_id` (uuid4); legacy grouping flagged as inferred | `agent_trace_turns.session_id`, which the browser mints (DF6). Synthetic ids (`rerun`, `report-…`, `docs-…`, `golden-…`) are flagged and grouped explicitly, never shown as user sessions (D4, `conversations.py`). |
| run_id | `run-<UTC>-<8hex>`, one per batch, reused on resume; inferred runs labelled | `plenum_tracelab.golden_runs` plus `turn_meta.golden_run_id` and `rep`. Live traffic shows as "All live traffic" in Runs (D13, M3, `runner.py`). |
| Scenario + expected outcome | `scenarios/*.jsonl` with an answer key, evidence source, objective vs judgment | `golden_sets` and `golden_cases` from an Excel/CSV upload (`golden.py`). Joined by `turn_meta.golden_case_key`, never fuzzy (D2). The expected-outcome panel stays collapsed until revealed (D13, M3). |
| Annotation / open code | Free-text notes in `annotations.json`, anchored by quote plus message index | `annotations` + `annotation_events`: right/wrong + why, first failure, anchored by span_id + quote. They copy the evidence they cite (D5). Edit = new row, delete = tombstone, stale write = 409 (D12, M2, `annotations.py`). |
| Taxonomy mode | `patterns.json`: definition, boundary, status, ≥3 positives, ≥3 close negatives, created_from, requirement source, evaluator type | `modes` + `mode_events` + `requirements` (`taxonomy.py`, `requirements.py`, M4). Owned by the platform superadmin (D7). |
| Suggestion | `suggestions.json`, shown dashed with accept/dismiss; a dismissal is a label-0 close negative. Produced by an outside coding agent; no proposer code in the repo. | `proposal_runs` + `proposals` (`proposals.py`, M4): in-product Anthropic calls (D8) with cost caps (D11) and a bulk-approval audit |
| Label | `labels/<mode>.jsonl`, 1 = failure present, with a `superseded_by` chain | `labels`: present/absent per turn × mode, with source provenance and a superseded chain. The latest decision wins (D12, M4, `labels.py`). Labels view. |
| Sample manifest | `sample_manifest.json`: batches, a reason per trace, seeds | `sample_batches` + `sample_items` (`sampling.py`, M2). Map and Progress views. |
| Judge version | `<mode>-v<N>`; `prompt_hash = sha256(model+"\n"+prompt)[:12]`; one-way freeze; test once | `judges` (`judges.py`, M5, D8) |
| Split | Stratified per trace, 20/40/40, seed 7; refuses below 10 per class | `judge_splits`, by conversation (M5) |
| Prediction cache | One prediction per (hash, trace), saved per batch of 10 | `judge_predictions` keyed by `prompt_hash` + `turn_id`. Jobs checkpoint after every item (D9, M5). |
| Corrected prevalence | Rogan-Gladen, 20,000-replicate bootstrap, seed 7, from test rates | `stats.py`, written to `monitor_batches` (M6) |
| Monitoring score | `{mode}_verdict` per trace and `{mode}_corrected_prevalence` per batch as Langfuse scores. The HW7 code is unimplemented. | `monitor_verdicts` (turn × mode) and `monitor_batches` (prevalence, CI, threshold). Code checks (`checks.py`) run on all chat traffic in a window; frozen judges run on a uniform sample plus risk groups (M6, `monitor.py`). |
| Eval case | `eval_cases/cases.jsonl`: kind regression or capability from 5 baseline runs. The file is empty. | `eval_cases` + `eval_case_runs` (`eval_cases.py`, M6): 5 baseline runs, pass@1/3/5, pass^5 |
| CI gate | `tests/eval/passk.py` stubs, a Harbor adapter, `evals.yml` whose PR job has no steps | The release check: regression cases must pass k/k on a golden run (M6). It is not a CI job in this build. |
| Replay harness | One input replayed n times on a re-seeded world; only infrastructure errors retried; bootstrap interval | Repeat-n replay of one turn under the safe replay contract (D10, `replay_safety.py`, M6). A refusal marks the sample degraded. The live shared DB cannot be re-seeded, so replays read current data. |
| GEPA | `optimize/gepa_adapter.py`; unfinished (one replay per unit, null prices, unenforced allow-list) | **Not built.** The brief's loop ends at eval cases and the release check. The skill lab stays Hoist's only replay-based prompt tuner. |
| Langfuse sync | `live_sync.py`: backfill, 60 s poll, fingerprints, upsert by trace id | **Not built.** Traces already live in the same Postgres (D3, D4). Hoist writes each turn once, so there are no late spans. |
| Sync now | `POST /api/sync`, `sync_status.json` | An auto-refresh poll of new turns, "Updated hh:mm - N new", a Refresh control and a visible error state (D4) |
| Scores write-back | Accepted labels written as Langfuse NUMERIC scores. Failed writes were never retried; 225 of 798 were later found missing. | **Not built.** The `labels` row is the record of truth. Failed writes are shown and retryable (D13). |
| Prompt version | sha256[:12] of the prompt template, stamped on spans | Not captured, and not among `turn_meta`'s columns (see open issues) |
| Chain-of-thought boundary | Only provider reasoning summaries, labelled "not raw chain-of-thought" | Hoist stores no reasoning. The D13 notice says what is and is not shown. |
| Map / Progress | `graph.json` (PCA + k-means); a treemap; review completion; the discovery-rate curve exists in prose only | Map view (PCA 2-D + k-means) and Progress view (M2); coverage and saturation (M4) |

### 2.6 The gap list, ordered by what blocks the loop

Prerequisites outside this build come first, because they gate any image rebuild or any trust in golden results.

| Id | Prerequisite | Evidence |
|---|---|---|
| P1 | hoist-f5's company-boundary work must land before any svc-deepagents, svc-udr or ops-intel image is rebuilt from this branch (D1). Rebuilding from clean eval-2 brings back the cross-company "34 floors" read. | followup 2; MEM |
| P2 | The three chat fixes (date binding, whole-day bounds, yes/no flags) exist only in `stash@{0}` and the running local image. A rebuild from eval-2 drops them, and golden runs will re-find them. | `DA/agents/ontology_qa.py:149, 1219-1230` |
| P3 | Azure writers run old code: a purge with no keep-list, no `turn_meta`, the question overwrite. | MEM; §2.1.6 |
| P4 | Several routes are unauthenticated: `/api/workflow/activity*`, `/resume`, `/status` and `/workspace`. `POST /activity` can write rows that look like orchestrator rows. | `DA/api/routes/workflow.py:891-981, 1128-1196` |

| # | Gap | Why it blocks the loop | Evidence | Closed by |
|---|---|---|---|---|
| G1 | No migration runner, ledger or verify mode; all store DDL runs at every boot | Nothing can be stored without production DDL on every rebuild | `DA/api/main.py:95-121` | M0: `migrate.py`, `schema_migrations`, `TRACE_LAB_SCHEMA` (D3) |
| G2 | The span purge spares nothing (deadline about 31 Dec 2026) | Labels and notes lose their evidence | `DA/agents/trace.py:133-146` | M0 keep-list plus evidence copies (D5); P3 |
| G3 | The chat reads every company's traces, and svc-udr SELECT would read `plenum_tracelab` | The chat under test could read expected answers and other companies' notes | DF4 | M0 `guards.py` (D15) |
| G4 | Wrong question on REST turns; `/resume` turns unowned; no channel, origin, page or asker scope | The review sample would mix wrapped prompts, UI templates, report cards, support and uploads in with real questions | DF1, DF2 | M0 `provenance.py`, `turn_meta`, question fix, classifier for old rows (D6a, D6b) |
| G5 | EL-UDR and contract compose rewrite answers untraced | Reviewers cannot see the draft or pin a first failure to the gate; cost is undercounted | DF5 | M0 gate spans (D6c) |
| G6 | No conversation view: a flat list capped at 200, a tree plus one span, no session grouping or turn index; reload shows empty; state leaks across companies | People cannot read a follow-up in its context | DF10, DF11, DF14-DF16 | M1 `normalize.py`, `conversations.py`, `TraceLab.jsx`, the Conversations and Model calls views |
| G7 | No review store. The only label is one overwrite-in-place slot that auto-teaches. | "Right or wrong, and why" cannot be recorded without corrupting the asker's thumbs and changing live answers | DF12, DF13 | M2 `annotations` + `annotation_events` (D12); Overview thumbs (open issue) |
| G8 | No sampling, no manifest, no progress view | Answers get picked by eye, so there is no unbiased failure rate | — | M2 `sample_batches`, `sample_items`, Map and Progress |
| G9 | No durable jobs or spend caps | Every batch dies on restart and spends without limit | `DA/agents/skill_lab.py:107, 431-456` | M0 `jobs.py`; `costs.py` (D9, D11) |
| G10 | No golden set and no safe re-ask. Missing pieces: no `.xlsx` parser, no run or case id, REPLAY means no trace, the guard is both leaky and over-blocking, a reused `turn_id` overwrites (`ON CONFLICT`), the trace write is fire-and-forget, and tokens last 30 minutes. | Automatic checking is impossible | `DA/agents/trace.py:296, 732, 752`; §2.3.2 | M3 `golden.py`, `replay_safety.py`, `runner.py`, `POST /api/trace-lab/golden-runs/{id}/step` (D9, D10) |
| G11 | No grader whose agreement with people is measured | Automatic verdicts cannot count, and the earlier judge over-matched | §2.4.3 | M3 `grading.py` (objective: a deterministic check first; judgment: unvalidated until measured); M5 |
| G12 | Golden turns would count as company traffic | Platform cost would bill them; Overview stats and export would include them; the skill-lab test set would read them | `OI/engines/platform_cost.py:99-105, 120-125, 228-232`; `DA/agents/skill_lab.py:147-156` | M3 exclusion by `turn_meta.golden_run_id` and the session flag (D4, D10) |
| G13 | No taxonomy, label grid, proposals or requirement register | "Grouped by cause" cannot happen | — | M4 |
| G14 | No validated judges | Checks cannot scale past the golden set | — | M5 |
| G15 | No monitoring of live traffic | "Watched" cannot happen | — | M6 `monitor.py`, `checks.py`, `stats.py` |
| G16 | No eval cases, release check or repeat-n replay | No one can show that a fix works and keeps working | — | M6 `eval_cases.py` |
| G17 | No "share checked automatically vs by people" trend | The user's own success metric is missing | — | M6 Watch view |
