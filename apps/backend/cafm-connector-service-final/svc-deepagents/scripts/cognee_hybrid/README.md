# Memorilla + cognee hybrid — run it locally (Qwen or OpenAI)

The orchestrator side lives in `src/services/cognee_graph.py` (design:
`docs/superpowers/specs/2026-11-20-memorilla-cognee-hybrid-design.md`). Memorilla decides which
memories bear on a question; the cognee graph adds the memories it links to the question that
similarity missed (e.g. AHU-3 → "the vendor for AHU-3 is Apex Mechanical" → "Apex Mechanical
must not be instructed … CHAS").

**Why a separate environment:** cognee 1.6 needs `openai>=2.20`; svc-deepagents pins
`langchain-openai<0.3` (`openai<2`), so cognee cannot be installed into the service yet. These
two scripts run the service's own memory code (`chat_memories`, `memorilla`, `cognee_graph`) in
their own Python environment, without the orchestrator.

## 1. Environment

```bash
cd apps/backend/cafm-connector-service-final/svc-deepagents
uv venv .venv-cognee --python 3.12
uv pip install --python .venv-cognee/bin/python "cognee[postgres]==1.6.10" \
    pydantic-settings structlog sqlalchemy httpx fastapi uvicorn openai
docker compose -f ../../../../docker-compose.cognee.yml up -d        # cognee's own Postgres :5462
```

`cognee[postgres]` builds psycopg2: on macOS `brew install libpq`, on Debian `libpq-dev gcc`.

## 2. Settings

Local Qwen (Ollama: `ollama pull qwen3:8b && ollama pull qwen3-embedding:0.6b`; start it with
`OLLAMA_CONTEXT_LENGTH=8192 ollama serve` — the 2048 default truncates cognee's graph prompts):

```bash
export OPENAI_API_BASE=http://localhost:11434/v1  OPENAI_MODEL=qwen3:8b  OPENAI_API_KEY=
export COGNEE_LLM_REASONING_EFFORT=none           # Qwen3 thinking off: ~10x fewer tokens
export COGNEE_EMBEDDING_PROVIDER=ollama  COGNEE_EMBEDDING_MODEL=qwen3-embedding:0.6b
export COGNEE_EMBEDDING_DIMENSIONS=1024  COGNEE_EMBEDDING_ENDPOINT=http://localhost:11434/api/embed
export COGNEE_EMBEDDING_TOKENIZER=Qwen/Qwen3-Embedding-0.6B
export COGNEE_DB_URL=postgresql://cognee:cognee@localhost:5462/cognee
export PLENUM_MEMORY_URL=http://127.0.0.1:8000    # the plenum-memory server (memorilla-search)
```

OpenAI instead: leave `OPENAI_API_BASE` and the `COGNEE_EMBEDDING_*` / reasoning settings unset
and set `OPENAI_API_KEY` and `OPENAI_MODEL`. A graph built with one embedding model cannot be read
with another: use a separate `COGNEE_DB_URL` database per model.

## 3. Load a company and open the page

The input is the chat memories export, one JSON object per line:
`{"org", "kind", "text", "subject"}` (`plenum_cafm.chat_memories`, active rows).

```bash
.venv-cognee/bin/python scripts/cognee_hybrid/load.py --memories chat_memories.jsonl \
    --org <org uuid> --memorilla-out memorilla_docs.jsonl
plenum-memory add --user hoistra-<first 8 of org>-memories --no-chunk memorilla_docs.jsonl
plenum-memory serve --port 8000            # with your encoder / store options

HYBRID_ORG=<org uuid> HYBRID_MEMORIES=chat_memories.jsonl \
    .venv-cognee/bin/python scripts/cognee_hybrid/serve.py
# http://127.0.0.1:8004/
```

Measured 20 Nov on a 16 GB M4: building the graph on local Qwen3-8B takes ~30 s per fact (3 facts:
82 s; use `--limit` to try a few first); on OpenAI ~0.4 s per fact. A graph read takes 0.1–0.8 s
warm; the first read in a process is cold (the page warms up at start).

## In the orchestrator

Off unless `COGNEE_MEMORY_COMPANIES` names the company and `COGNEE_DB_URL` is set (never the main
database). `COGNEE_MEMORY_MODE=shadow` (default) traces what the graph would add ("graph recall"
span) without changing answers; `live` adds the linked memories to recall. Needs cognee installed
in the service — blocked by the openai conflict above until the next version runs cognee as its
own service.
