# UDR — Universal Database Reader

**Service:** `svc-udr`
**Port:** `8006`
**Location:** `cafm-connector-service-final/svc-udr/`
**Model:** `claude-haiku-4-5` (Anthropic)
**Schema target:** `plenum_cafm` (Azure PostgreSQL)

---

## 1. Purpose

The Universal Database Reader is a specialist sub-agent in the Plenum CAFM AI platform. Its sole responsibility is to own all database interactions on behalf of the DeepAgents orchestrator layer.

Every other sub-agent in the platform (Work Order Engine, Doc RAG, Schema Mapper) deals with a specific domain. UDR cuts across all of them. When the orchestrator needs to read work orders, look up an asset, check inventory, update a record, or run a custom JOIN across tables, it delegates that entirely to UDR.

UDR exposes two interfaces:

- **Agent interface** — A natural-language endpoint where the orchestrator can say "find all open Highest-priority work orders created in the last 7 days" and UDR figures out the schema, constructs the query, and returns the result.
- **Direct CRUD interface** — Structured REST endpoints for when the orchestrator already knows exactly which table and record it needs.

UDR has no domain logic of its own. It does not assess criticality, detect compliance, or score vendors. It is a clean, safe, auditable database gateway.

---

## 2. Position in the Platform Architecture

```
┌─────────────────────────────────────────────────┐
│         DeepAgents Orchestration Layer           │
│         (LangChain + LangGraph)                  │
│                                                  │
│  write_todos() → filesystem → task() → aggregate │
└──────┬──────────────┬───────────────┬────────────┘
       │              │               │
       ▼              ▼               ▼
  WO Engine      Doc RAG         Schema Mapper
  Sub-agent      Sub-agent       Sub-agent
       │
       ▼
  ┌─────────┐
  │   UDR   │  ◄─── All DB reads/writes route through here
  │ Sub-agent│
  └────┬────┘
       │
       ▼
  plenum_cafm schema (Azure PostgreSQL)
  50+ tables: work_orders, assets, locations,
  vendors, spare_parts, inspections, rca_*, ...
```

When the orchestrator spawns UDR via `task("read work orders for HVAC assets")`, UDR's internal agent runs a tool-use loop using Claude Haiku, calls the appropriate DB tools (`list_tables` → `describe_table` → `execute_select`), and returns a structured result back to the orchestrator.

---

## 3. Directory Structure

```
svc-udr/
├── .env.example                        ← Environment variable template
├── pyproject.toml                      ← Python project config + dependencies
│
├── docs/
│   └── UDR_MODULE.md                   ← This file
│
└── src/
    ├── __init__.py
    ├── app.py                          ← FastAPI application entry point
    ├── config.py                       ← Pydantic Settings (env vars)
    ├── db.py                           ← Async SQLAlchemy engine + session factory
    │
    ├── core/
    │   ├── __init__.py
    │   ├── logging.py                  ← structlog configuration
    │   └── exceptions.py              ← Typed exception hierarchy
    │
    ├── services/
    │   ├── __init__.py
    │   └── database_service.py        ← Core CRUD engine (raw SQL via text())
    │
    ├── agent/
    │   ├── __init__.py
    │   ├── prompts.py                  ← System prompt for the UDR agent
    │   ├── orchestrator.py            ← Anthropic tool-use loop
    │   └── tools/
    │       ├── __init__.py
    │       ├── definitions.py         ← 9 Anthropic tool schemas
    │       ├── executor.py            ← Routes tool_name → db_tools method
    │       └── db_tools.py            ← Wrappers over DatabaseService
    │
    └── api/
        ├── __init__.py
        ├── routes/
        │   ├── __init__.py
        │   ├── agent.py               ← POST /api/agent/query
        │   └── tables.py              ← Full CRUD REST endpoints
        └── schemas/
            ├── __init__.py
            └── database.py            ← Pydantic request/response models
```

---

## 4. Configuration

All configuration is loaded from environment variables (or a `.env` file) via Pydantic Settings.

| Variable | Default | Description |
|---|---|---|
| `DB_URL` / `DATABASE_URL` | _(required)_ | PostgreSQL asyncpg connection string |
| `ANTHROPIC_API_KEY` | _(required)_ | Anthropic API key for Claude Haiku |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5` | Claude model for the agent loop |
| `MAX_QUERY_ROWS` | `500` | Hard cap on result set size per query |
| `MAX_AGENT_ITERATIONS` | `10` | Max tool-use rounds per agent query |
| `DEBUG` | `false` | Enables full traceback logging |

**Connection string format:**
```
postgresql+asyncpg://user:password@plenum-agentic-ai.postgres.database.azure.com:5432/plenum_agent
```

---

## 5. The Agent Layer

### 5.1 How it works

The UDR agent runs inside `src/agent/orchestrator.py`. It uses the Anthropic SDK's tool-use pattern with `claude-haiku-4-5`.

When a query arrives at `POST /api/agent/query`:

```
1. UDROrchestrator.query(message) is called
2. A messages list is initialised with the user message
3. The agent loop begins (max MAX_AGENT_ITERATIONS rounds):
   a. Send messages to Claude Haiku with TOOL_DEFINITIONS
   b. If stop_reason == "end_turn" → extract reply text, return result
   c. If stop_reason == "tool_use":
      - Append assistant response to messages
      - For each tool_use block:
          - Call ToolExecutor.execute(tool_name, tool_input)
          - Append tool_result to messages
      - Loop back to step a
4. Return {"success": True, "reply": str, "tool_calls_made": int}
```

### 5.2 System prompt

The system prompt (in `src/agent/prompts.py`) tells the agent:

- Its role as a database gateway sub-agent
- The categories of tables available in `plenum_cafm`
- The strategy: always call `list_tables` or `describe_table` before operating on an unknown table
- When to use `read_records` vs `execute_select` (simple equality filters vs complex JOINs)
- Safety rules: no DDL, always parameterized queries, confirm before mass deletes

### 5.3 The 9 tools

All tool definitions live in `src/agent/tools/definitions.py` in Anthropic tool-use format.

| Tool | Purpose | Key inputs |
|---|---|---|
| `list_tables` | List all tables in `plenum_cafm` with row estimates | — |
| `describe_table` | Get columns, types, PKs, FKs for a table | `table` |
| `read_records` | SELECT with equality filters, sorting, pagination | `table`, `filters?`, `columns?`, `limit?`, `offset?`, `order_by?`, `order_dir?` |
| `get_record` | Fetch one record by primary key | `table`, `record_id`, `id_column?` |
| `search_records` | ILIKE text search across specified columns | `table`, `search_term`, `search_columns`, `limit?`, `offset?` |
| `create_record` | INSERT a new row, returns created record | `table`, `data` |
| `update_record` | UPDATE a row by PK, returns updated record | `table`, `record_id`, `data`, `id_column?` |
| `delete_record` | DELETE a row by PK | `table`, `record_id`, `id_column?` |
| `execute_select` | Run any SELECT query with named params | `sql`, `params?` |

The executor (`src/agent/tools/executor.py`) routes `tool_name` to the corresponding method on `DBTools`. The `DBTools` class (`src/agent/tools/db_tools.py`) wraps `DatabaseService` and normalises all exceptions into `{"success": False, "error": "..."}` dicts so the agent can read and act on errors rather than crashing.

---

## 6. The DatabaseService

`src/services/database_service.py` is the authoritative SQL layer. All queries go through here. It uses `sqlalchemy.text()` for parameterized raw SQL — no ORM models, because UDR is schema-agnostic and must work with any table including new ones added in future migrations.

### 6.1 Identifier safety

Every table name and column name is validated through two gates before any SQL executes:

**Gate 1 — Regex:**
```python
_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
```
This is the same guard used by `cafm-connector-service/table_customizer.py`. It rejects any identifier that could be used for SQL injection via identifier quoting tricks.

**Gate 2 — information_schema allow-list:**
```python
SELECT 1 FROM information_schema.tables
WHERE table_schema = :schema AND table_name = :table AND table_type = 'BASE TABLE'
```
If the table does not exist in `information_schema`, a `TableNotFoundError` is raised before any DML runs.

### 6.2 Methods

#### `list_tables() → list[dict]`
Queries `information_schema.tables` joined with `pg_stat_user_tables` for live row estimates. Returns a list of `{"table": str, "row_estimate": int}` dicts, sorted alphabetically.

#### `describe_table(table) → dict`
Returns:
```json
{
  "table": "work_orders",
  "schema": "plenum_cafm",
  "columns": [
    {"name": "id", "type": "uuid", "nullable": false, "default": "gen_random_uuid()", ...},
    {"name": "title", "type": "character varying", "nullable": false, ...},
    ...
  ],
  "primary_keys": ["id"],
  "foreign_keys": [
    {"column": "organization_id", "references_table": "organizations", "references_column": "id"}
  ]
}
```

#### `read_records(table, filters?, columns?, limit?, offset?, order_by?, order_dir?) → dict`
Builds a parameterized `SELECT` dynamically. Filters are equality-only (`col = :val`). Identifiers are quoted to handle reserved words. Result includes `{"rows": [...], "total": int, "has_more": bool}`.

#### `get_record(table, record_id, id_column="id") → dict`
Fetches a single row by PK. Raises `RecordNotFoundError` if not found.

#### `search_records(table, search_term, search_columns, limit?, offset?) → dict`
Builds `WHERE col1::text ILIKE :term OR col2::text ILIKE :term`. Casts all columns to `text` to handle UUID, integer, and timestamp columns uniformly.

#### `create_record(table, data) → dict`
`INSERT ... RETURNING *`. Commits immediately. Returns the created row including all DB-generated fields (UUID id, `created_at`, etc.).

#### `update_record(table, record_id, data, id_column="id") → dict`
`UPDATE ... SET col = :val WHERE id_col = :rid RETURNING *`. Raises `RecordNotFoundError` if the PK is not found. Commits immediately.

#### `delete_record(table, record_id, id_column="id") → bool`
`DELETE ... WHERE id_col = :rid RETURNING id_col`. Returns `True` if a row was deleted, `False` if nothing matched.

#### `execute_select(sql, params?) → list[dict]`
Validates the SQL starts with `SELECT` (case-insensitive) and contains no semicolons (preventing statement stacking). Fetches at most `MAX_QUERY_ROWS` rows. This is the escape hatch for JOINs, aggregations, date ranges, and subqueries that `read_records` cannot handle.

### 6.3 Type serialization

`_serialize_row()` converts non-JSON-safe Python types from SQLAlchemy result rows into safe primitives before returning them to the agent or API caller:

| Python type | Serialized as |
|---|---|
| `UUID` | `str` |
| `datetime` / `date` / `time` | `str` (ISO 8601) |
| `Decimal` | `float` |
| `bytes` | `str` (UTF-8 decoded) |
| All others | unchanged |

---

## 7. The REST API

### 7.1 Agent endpoint

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/agent/query` | Send a natural-language request to the UDR agent |

**Request:**
```json
{
  "message": "Find all work orders with Highest priority that are still open, show me the title, asset, and created date"
}
```

**Response:**
```json
{
  "success": true,
  "reply": "Found 4 open Highest-priority work orders: ...",
  "tool_calls_made": 3
}
```

### 7.2 Schema introspection endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/tables/` | List all tables with row estimates |
| `GET` | `/api/tables/{table}/schema` | Describe columns, PKs, FKs for a table |

### 7.3 CRUD endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/tables/{table}/records` | Read records (paginated, sortable) |
| `POST` | `/api/tables/{table}/records/search` | Text search across columns |
| `GET` | `/api/tables/{table}/records/{record_id}` | Get a single record by PK |
| `POST` | `/api/tables/{table}/records` | Create a new record (returns 201) |
| `PATCH` | `/api/tables/{table}/records/{record_id}` | Update a record |
| `DELETE` | `/api/tables/{table}/records/{record_id}` | Delete a record |
| `POST` | `/api/tables/query/select` | Execute a custom SELECT query |

### 7.4 Health

| Method | Path | Response |
|---|---|---|
| `GET` | `/health` | `{"status": "ok", "service": "svc-udr"}` |

Full interactive docs available at `/docs` (Swagger UI) and `/redoc`.

---

## 8. Exception Hierarchy

All UDR-specific exceptions extend `UDRError`. The REST layer maps these to appropriate HTTP status codes automatically.

| Exception | HTTP Status | When raised |
|---|---|---|
| `UnsafeIdentifierError` | 400 | Table/column name fails `_SAFE_IDENT` regex |
| `UnsafeQueryError` | 400 | `execute_select` receives a non-SELECT statement or semicolon |
| `TableNotFoundError` | 404 | Table not found in `information_schema` |
| `ColumnNotFoundError` | 404 | Column not found in the table |
| `RecordNotFoundError` | 404 | PK lookup returns no row |
| `SQLAlchemyError` | 500 | Caught by the global handler in `app.py` |

---

## 9. Live Schema Discovery

UDR has **no hardcoded table list**. It asks the database at runtime.

This is the architectural point of the service: the actual number of tables in `plenum_cafm` is whatever PostgreSQL reports the moment a query arrives. Every Alembic migration, every new ingestion table, every Sprint 2 addition is automatically visible to UDR without a code change or a redeployment.

### How it works

**`list_tables()`** — called at the start of every agent session or whenever the orchestrator needs to know what exists:

```sql
SELECT
    t.table_name,
    s.n_live_tup AS row_estimate
FROM information_schema.tables t
LEFT JOIN pg_stat_user_tables s
    ON s.relname = t.table_name
    AND s.schemaname = t.table_schema
WHERE t.table_schema = 'plenum_cafm'
  AND t.table_type  = 'BASE TABLE'
ORDER BY t.table_name;
```

Example response (what the agent actually sees at runtime — the real count and names come from the live database):

```json
{
  "tables": [
    { "table_name": "asset_meters",        "row_estimate": 0 },
    { "table_name": "assets",              "row_estimate": 60 },
    { "table_name": "audit_logs",          "row_estimate": 4821 },
    { "table_name": "ingestion_documents", "row_estimate": 142 },
    { "table_name": "spare_parts",         "row_estimate": 38 },
    { "table_name": "work_orders",         "row_estimate": 74 }
    // ... every other table in plenum_cafm at this moment
  ],
  "count": "<whatever PostgreSQL reports right now>"
}
```

**`describe_table(table_name)`** — similarly live, never hardcoded:

```sql
SELECT
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'plenum_cafm'
  AND table_name   = :table
ORDER BY ordinal_position;
```

Foreign key relationships are also resolved live from `information_schema.key_column_usage` and `information_schema.referential_constraints`.

### The two-gate safety check

Before any SQL touches a table, `_assert_table_exists()` re-queries `information_schema.tables` to verify the table exists in the schema. A table name that passes the `_SAFE_IDENT` regex but does not appear in `information_schema` is rejected with `TableNotFoundError`. This means:

1. No SQL injection — identifiers that fail the regex are rejected before any DB round-trip.
2. No phantom tables — identifiers that pass the regex but do not exist in the live schema are rejected before any SQL runs.

### Consequence for maintenance

Adding a table to `plenum_cafm` via Alembic migration immediately makes it available to UDR. No UDR code change. No redeployment. The next `list_tables()` call returns the new table and the agent can read, write, and describe it like any other.

---

## 10. Integration with DeepAgents Orchestrator

When the DeepAgents orchestration layer spawns UDR as a subagent, the typical call pattern is:

```python
# Orchestrator calls UDR via task() in the DeepAgents framework
result = task(
    agent="udr",
    message="Get all spare parts where stock_on_hand is below minimum_allowed_stock, "
            "include part_code, part_name, stock_on_hand, minimum_allowed_stock, and supplier"
)
```

Internally, UDR's agent loop will:
1. Call `list_tables()` to verify `spare_parts` exists
2. Call `describe_table("spare_parts")` to see column names
3. Call `execute_select()` with:
   ```sql
   SELECT part_code, part_name, stock_on_hand, minimum_allowed_stock, supplier
   FROM plenum_cafm."spare_parts"
   WHERE stock_on_hand < minimum_allowed_stock
   ORDER BY stock_on_hand ASC
   ```
4. Return the rows to the orchestrator with a natural-language summary

The orchestrator receives a structured `{"success": true, "reply": "...", "tool_calls_made": 3}` response and continues its planning cycle.

---

## 11. Adding to docker-compose.yml

Add this block to the existing `docker-compose.yml` at `cafm-connector-service-final/docker-compose.yml`:

```yaml
svc-udr:
  build:
    context: ./svc-udr
    dockerfile: Dockerfile
  command: >
    uvicorn src.app:app --host 0.0.0.0 --port 8006 --reload
  environment:
    DB_URL: postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@postgres:5432/plenum_agent
    ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
    ANTHROPIC_MODEL: claude-haiku-4-5
    MAX_QUERY_ROWS: "500"
    DEBUG: "false"
  ports:
    - "8006:8006"
  depends_on:
    postgres:
      condition: service_healthy
  restart: unless-stopped
```

For local development without Docker:
```bash
cd svc-udr
pip install -e ".[dev]"
cp .env.example .env
# Fill in DB_URL and ANTHROPIC_API_KEY in .env
uvicorn src.app:app --host 0.0.0.0 --port 8006 --reload
```

---

## 12. Design Decisions

### Why raw SQL instead of ORM models?
UDR is a universal reader — it must handle any table in the schema, including tables added by future migrations. Binding it to specific ORM models would require code changes every time the schema evolves. Raw SQL via `sqlalchemy.text()` with parameterized queries gives full flexibility while maintaining safety through the identifier validation layer.

The ORM models continue to live in `cafm-connector-service` and are used by services that own a specific domain. UDR deliberately does not duplicate them.

### Why Claude Haiku and not GPT?
The platform's primary AI provider is Anthropic (Sonnet for extraction/orchestration, Haiku for routing/classification). UDR's tool-use decisions are relatively straightforward — "which table, which columns, what filter?" — making Haiku the right cost/speed choice. The `ANTHROPIC_MODEL` setting can be changed to Sonnet if a query requires more complex multi-step reasoning.

### Why two interfaces (agent + direct CRUD)?
The agent interface handles ambiguous or exploratory requests where the orchestrator doesn't know the exact schema. The direct CRUD endpoints handle high-frequency structured calls where the orchestrator already knows the exact table and record — bypassing the agent loop entirely for lower latency and zero token cost.

### Why not extend the existing table_customizer.py?
`table_customizer.py` in `cafm-connector-service` is a UI-serving endpoint — it has DDL support (ADD/DROP COLUMN), is mounted as a sub-app with its own CORS policy, and is owned by the connector service. UDR is a separate AI sub-agent with its own lifecycle, its own agent loop, and no DDL. Merging them would violate the single-responsibility boundary between services.

---

## 13. Security Constraints

1. **No DDL.** UDR cannot `CREATE`, `ALTER`, or `DROP` any database object. Only `SELECT`, `INSERT`, `UPDATE`, `DELETE`.
2. **Identifier allow-listing.** Every table and column name passes through `_SAFE_IDENT` regex AND an `information_schema` existence check before touching SQL.
3. **Parameterized queries only.** `execute_select` requires named `:param_name` placeholders. String interpolation into SQL is never done anywhere in the codebase.
4. **SELECT-only for `execute_select`.** The method validates the statement starts with `SELECT` and rejects semicolons. It cannot be used for write operations.
5. **Row cap.** All result sets are capped at `MAX_QUERY_ROWS` (default 500) to prevent runaway queries.
6. **Agent iteration cap.** The tool-use loop terminates after `MAX_AGENT_ITERATIONS` (default 10) rounds to prevent infinite loops.

---

## 14. Future Enhancements

The following can be added without changing the existing interface contracts:

- **Query audit log** — write every agent query to `plenum_cafm.query_audit_log` (table already exists from Sprint 2)
- **Read replica routing** — point `read_records` / `execute_select` to a PostgreSQL read replica for SELECT-heavy workloads
- **Redis caching** — cache `list_tables` and `describe_table` results with a short TTL (schema rarely changes)
- **Streaming results** — add a `GET /api/tables/{table}/records/stream` endpoint using SSE for large exports
- **Row-level permissions** — inject `organization_id` filters based on a caller identity header to enforce multi-tenancy at the query level
