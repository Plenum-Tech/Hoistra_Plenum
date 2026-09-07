# UDR Module — Achieved Functionalities

**Service:** `svc-udr` — Universal Database Reader  
**Port:** 8006  
**Tested against:** Azure PostgreSQL (`plenum-agentic-ai.postgres.database.azure.com`) / schema: `plenum_cafm`  
**Date:** 2026-05-13

---

## 1. Live Schema Discovery

- Connects to the real database at query time and discovers every table in `plenum_cafm` dynamically via `information_schema.tables`
- Returns table name + live row estimate for every table — **134 tables** discovered in the current database state
- No hardcoded table list anywhere in the codebase — the count and names are whatever PostgreSQL reports at that moment
- `describe_table()` introspects columns, data types, nullable flags, defaults, primary keys, and foreign key relationships live from `information_schema`
- Any table added via a future Alembic migration is automatically available to UDR with no code change or redeployment

---

## 2. Full CRUD on Every Table

All operations work against any table in `plenum_cafm` without ORM models:

| Operation | Method | Endpoint |
|---|---|---|
| List all tables | GET | `/api/tables/` |
| Describe table schema | GET | `/api/tables/{table}/schema` |
| Read records (paginated) | GET | `/api/tables/{table}/records` |
| Get single record by PK | GET | `/api/tables/{table}/records/{id}` |
| Search records by text | POST | `/api/tables/{table}/records/search` |
| Create record | POST | `/api/tables/{table}/records` |
| Update record | PATCH | `/api/tables/{table}/records/{id}` |
| Delete record | DELETE | `/api/tables/{table}/records/{id}` |
| Execute custom SELECT | POST | `/api/tables/query/select` |

Verified: successfully inserted a new row (`AST-AHU-601` — Daikin Air Handling Unit) into the live `assets` table and read it back.

---

## 3. AI Agent Interface (Natural Language)

- `POST /api/agent/query` accepts a plain English request and returns structured data
- Powered by `claude-haiku-4-5` via the Anthropic SDK with a 9-tool loop
- Agent introspects the schema, selects the right table(s), and executes the appropriate DB operations autonomously
- Supports up to 10 tool-call iterations per query (configurable via `MAX_AGENT_ITERATIONS`)
- Tested queries:
  - "How many work orders are there and what are the top 3 highest priority ones?" → returned 529 WOs, top 3 critical all on Rooftop Unit #3
  - "List all spare parts where stock is below minimum" → found 3 parts including HVAC Bearing at 0 stock (urgent)
  - "What tables exist and which ones have data?" → 132 tables, 38 with data, ~4,159 total rows, answered in 1 tool call
  - "Read all assets" → 16 assets with key fields, answered in 1 tool call

---

## 4. Structured JSON Responses from the Agent

- Agent replies are clean JSON objects — no markdown, no escaped strings, no code fences
- Every reply follows a consistent schema:
  - `summary` — one plain English sentence
  - `count` — number of results
  - `records` — array with only the requested fields, nulls stripped
- The orchestrator strips markdown code fences and parses the JSON so `reply` is a native object in the API response, ready for downstream consumption without any string parsing
- The `AgentQueryResponse.reply` field is typed as `Any` to support both plain text (fallback) and structured JSON

---

## 5. Two-Gate Security on Every Identifier

All table and column names go through two layers of validation before any SQL executes:

1. **Regex gate** — `_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")` — same pattern as `table_customizer.py` in `cafm-connector-service`
2. **Existence gate** — name must exist in `information_schema.tables` or `information_schema.columns` in the live database

Non-SELECT statements sent to `execute_select` are rejected before execution. Semicolons (multi-statement injection) are also blocked. Verified: `DROP TABLE assets` was rejected with a clear error message.

---

## 6. Comprehensive Structured Logging

Every layer of the service emits structured key-value log events using `structlog`:

| Layer | Events logged |
|---|---|
| `database_service.py` | Every method entry (debug), result stats (info), security rejections (warning) |
| `agent/tools/db_tools.py` | Tool called (debug), success with result counts (info), all exceptions (error with traceback) |
| `agent/tools/executor.py` | Dispatch with tool name and input keys (debug), unknown tool warnings |
| `api/routes/tables.py` | Every route entry with key params (info), write completions (info), security/404 rejections (warning), unexpected errors (error) |
| `agent/orchestrator.py` | Per-iteration response, tool calls made, query completion |
| `api/routes/agent.py` | Errors with exception type and detail |

**Environment-aware renderer:**
- `DEBUG=true` → coloured `ConsoleRenderer` for local development
- `DEBUG=false` → `JSONRenderer` for production / Azure Monitor ingestion

Security events (unsafe identifier, non-SELECT attempt, table not found) are always logged at `warning` level — filterable separately from normal traffic in Grafana or Azure Monitor.

---

## 7. FastAPI Application Structure

- Lifespan-managed startup/shutdown with structured log events
- CORS configured for the React frontend (`localhost:3001`, `localhost:3000`)
- Request timing middleware on every HTTP request (logs method, path, status code, elapsed ms)
- Five centralised exception handlers: `RequestValidationError`, `HTTPException`, `SQLAlchemyError`, `ResponseValidationError`, and catch-all `Exception`
- `/health` liveness probe
- Full Swagger UI at `/docs` and ReDoc at `/redoc`
- Custom OpenAPI schema with tagged endpoint groups: Agent, Tables, CRUD, Health

---

## 8. Database Connection

- Async SQLAlchemy engine (`asyncpg` driver) with lazy initialisation — engine is not created until the first request, so a missing or invalid `DB_URL` fails with a clear startup error rather than a crash at import time
- Connection pool: 5 connections, 10 overflow
- All queries use `sqlalchemy.text()` with named parameters — no ORM models, no raw string interpolation, schema-agnostic
- Result rows serialised to JSON-safe primitives: `UUID → str`, `datetime → str`, `Decimal → float`, `bytes → str`

---

## 9. Configuration

All settings loaded from `.env` via `pydantic-settings`:

| Variable | Purpose |
|---|---|
| `DB_URL` / `DATABASE_URL` | PostgreSQL connection string (asyncpg) |
| `ANTHROPIC_API_KEY` | Anthropic API key for the agent |
| `ANTHROPIC_MODEL` | Model to use (default: `claude-haiku-4-5`) |
| `MAX_QUERY_ROWS` | Hard cap on result set size (default: 500) |
| `MAX_AGENT_ITERATIONS` | Max tool-call rounds per agent query (default: 10) |
| `DEBUG` | `true` = coloured logs + DEBUG level, `false` = JSON logs + INFO level |

---

## 10. Orchestrator Integration Readiness

The service is designed to be called as a subagent by the DeepAgents orchestration layer:

- **Agent interface** (`POST /api/agent/query`) — for natural language requests when the orchestrator does not know which table or operation is needed
- **Direct CRUD interface** (`/api/tables/...`) — for structured calls when the orchestrator already knows the exact table and record, bypassing the agent loop entirely for faster sub-second responses
- Responses are structured JSON objects with consistent keys (`summary`, `count`, `records`) that the orchestrator can consume without parsing
