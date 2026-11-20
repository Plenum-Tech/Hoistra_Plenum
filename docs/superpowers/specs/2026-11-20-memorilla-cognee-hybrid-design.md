# Memorilla + cognee hybrid memory — design (short)

Approved by Hussain 20 Nov 2026, relaying Bala: "use both memorilla and cognee — cognee for the
nodes and the graph, memorilla for the remembering — combine both and derive the answer".
Built on Bala's `memorilla-recall` branch (3346c01), which already makes the local Memorilla
server rank chat memories and recall past plans.

## Intent

Memorilla decides *which* memories and *which past plan* bear on a question (Bala's branch,
unchanged). cognee adds *how those facts connect*: the knowledge-graph edges around the question,
so a link similarity misses ("fire door repairs" → lapsed FRA → blocked BAFE vendor) reaches the
model. The two are combined into one prompt block.

Evidence (20 Nov cognee demo on the 18 Nov export, graded against the source memories): 7/7
correct with memory, recall 14/14, isolation 7/7, multi-hop linked in 5/5 runs.

## Goals / non-goals / anti-goals

Goals
- One combined recall block: "What I remember" (Memorilla's memories, verbatim) + "How it
  connects" (cognee edges anchored on the question or the recalled memories).
- `COGNEE_MEMORY_MODE=shadow` (default): graph recall is traced only; `live`: it joins the prompt
  and the recall that sub-agents receive (`turn_recall`).
- Either source down → the other alone; both down → today's behaviour.

Non-goals
- No change to Memorilla's ranking, plan recall or tool replay.
- cognee does not answer (context-only retrieval: no LLM call at question time).
- Personal preferences and plans are not put in the graph.

Anti-goals
- Crossing companies: one cognee dataset per organisation, own database (per-dataset handlers).
- Surfacing what people forgot or have not had approved: only active company facts/corrections
  enter the graph; forget/reject removes them.
- Notes treated as records: the block keeps the rule that live records win.
- cognee touching the main database: refused unless COGNEE_DB_URL names a different database.

## Approaches considered

| Approach | Verdict |
|---|---|
| Memorilla ranks + cognee graph edges in one block (orchestrator answers) | **Chosen** — Bala's split, fits the orchestrator, no extra LLM call |
| Memorilla's decoder answers with graph text + latent tokens | Not in the orchestrator (it answers with OpenAI); kept for the offline comparison |
| Both answer, a judge merges | Two 8B models; rejected |

## Design

- `services/cognee_graph.py` (ported from the shadow pilot): gating (flag + own DB, main-DB guard),
  cognee 1.6 config (Postgres relational/pgvector/graph, per-dataset handlers, CACHING off,
  text-embedding-3-small), dataset `org_<org>`.
- Ingest: in `chat_memories.store()` beside Memorilla's add, and on approval in `review()`: active,
  non-personal fact/correction texts, fire-and-forget. `forget()` and a rejection remove the text
  from the graph (cognee.forget by the stored text's data id).
- Read: `graph_context(question, org)` — cognee GRAPH_COMPLETION, `only_context=True`, capped
  (3 s), parsed into edges `(source, relation, target, description)`.
- Combine (pure): anchors = entity words of the question and of the recalled memories (subjects,
  building/asset codes, proper names); keep entity→entity edges that touch an anchor, drop
  document "contains" edges and edges a recalled memory already states, cap 12.
- Orchestrator `_build_stateful_input`: Memorilla recall and graph context run together; the
  combined block replaces the memories block in `live`; a "graph recall" memory span records
  anchors, kept/dropped edges and mode either way.

## Changed by the offline comparison (20 Nov, hybrid-test/compare_hybrid.py)

The edge block ("How it connects") was replaced. On Plenum's export (9 questions, 3 multi-hop, 3
runs, answers by gpt-5.6-terra) the graph returned 1-5 entity-to-entity edges per question, mostly
trivial ("ahu-3 located_in bishopsgate tower"), and adding them changed little (Memorilla alone
7/9 each run; + edges 7-8/9). The graph's value is which *stored facts* it links to the question's
entities and to the recalled memories' entities — the second hop (AHU-3 -> "the vendor for AHU-3 is
Apex Mechanical" -> "Apex Mechanical must not be instructed ... CHAS").

So `linked_memories()` maps the graph's linked fact nodes back to `chat_memories.visible()` rows
the caller may see, skips ones Memorilla already recalled, caps at 4, and they join "What I
remember" verbatim through `format_recall`. A stale graph can therefore never surface a forgotten,
pending or another person's memory. Recall coverage: Memorilla alone 8/9, with graph-linked
memories 9/9; answers with the shipped code 9/9 vs 8/9.

## Changed by review (20 Nov)

- Shadow mode reads the graph in a background task (`shadow_soon`): the turn never waits for a
  read whose result it does not use. Live mode reads it together with Memorilla's recall.
- The 3 s cap holds even if a read ignores cancellation; cognee is imported and configured in a
  worker thread, so its first import cannot stop the event loop.
- Graph writes for one company run one at a time, in order, so a forget never overtakes the add of
  the same fact.
- Only company facts and corrections are linked, matched by their whole graph wording — a person's
  preference can never ride in on a fact that shares a sentence with it.
- cognee's backend access control (one database per company) is forced on.
- The main-database guard refuses the same host + port + database under any login or
  localhost alias.

## Local models and running it locally

cognee follows the orchestrator's model and `OPENAI_API_BASE`, so a local OpenAI-compatible server
(Ollama) works; embeddings are set with `COGNEE_EMBEDDING_*` (blank = OpenAI's
text-embedding-3-small). Verified 21 Nov on local Qwen3-8B + qwen3-embedding:0.6b: graph built,
read in 0.1-0.2 s warm, two-hop link found, forget emptied the graph. Because of the openai
conflict below, run it outside the orchestrator with `svc-deepagents/scripts/cognee_hybrid/`
(README: environment, Qwen settings, load a company, side-by-side page with the full trace).

## Known limits

- **Cannot be installed in svc-deepagents yet.** cognee 1.6 needs openai>=2.20 (through litellm);
  svc-deepagents pins langchain-openai<0.3, which needs openai<2 (`uv pip compile` proves it
  unsatisfiable). All tests run with cognee faked, and the live checks ran cognee in its own
  environment. Next version: cognee as its own service (like the Memorilla server), called over HTTP.

- The first graph read after a restart is cold (> 3 s), so that turn gets Memorilla's recall alone.
- A company's existing memories are not loaded into the graph when the flag is turned on; only
  memories saved or approved afterwards are. (Backfill: next version.)
- A graph forget that fails is not retried; recall stays safe because links only resolve to
  memories `chat_memories.visible()` returns.
- Linked memories are not marked used (use_count) the way recalled ones are.

## Left to the builder

Exact anchor tokenisation, edge cap tuning, and the offline comparison harness (Memorilla alone vs
combined, same question set, answers by the orchestrator's model).
