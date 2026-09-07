# 🤖 Claude Code Prompt: LangChain DeepAgent Flow Documentation

## Purpose
Use this prompt with Claude Code to automatically generate a comprehensive Markdown documentation file for the LangChain DeepAgent workflow system covering all 5 agents, 31+ tools, orchestration patterns, and HITL gates.

---

## 🎯 MASTER PROMPT FOR CLAUDE CODE

Copy and paste the following prompt directly into Claude Code:

```
Create a comprehensive Markdown documentation file at /docs/DEEPAGENT_FLOW.md that documents 
the complete LangChain DeepAgent agentic workflow system. The file should be a definitive 
technical reference covering architecture, agents, tools, orchestration patterns, and 
implementation details.

Structure the document with these exact sections:

# 1. EXECUTIVE SUMMARY (200-300 words)
- What is DeepAgent and why we're using it
- Business value: 70% code reduction, automatic planning, context management
- Comparison: Traditional LangChain (200+ lines) vs DeepAgents (3 lines)
- Key capabilities: planning, subagents, filesystem, memory
- Production readiness indicators

# 2. SYSTEM ARCHITECTURE
## 2.1 High-Level Architecture
Include an ASCII diagram showing:
- User → API Gateway → DeepAgent Orchestrator → 5 Specialized Agents → Tools → Database
- Use box drawing characters (┌─┐│└─┘├┤├┴┬)
- Show data flow with arrows (→ ←)
- Include external services (OpenAI, Claude, Redis, PostgreSQL, Azure Blob)

## 2.2 Component Breakdown
Table with columns: Component | Technology | Purpose | Lines of Code
Include rows for:
- Main Orchestrator (LangChain DeepAgents 0.1.0)
- 5 Specialized Agents (LangChain Tools)
- FastAPI Backend
- PostgreSQL Database
- Redis Cache
- Azure Blob Storage

## 2.3 Technology Stack
Comprehensive stack table with versions:
- Python 3.11+
- FastAPI 0.108+
- LangChain 0.1.0+
- DeepAgents 0.1.0+
- PostgreSQL 15
- Redis 7
- OpenAI (gpt-4o-mini, text-embedding-3-small)
- Claude (Haiku 4.5, Sonnet 4)

# 3. DEEPAGENT CORE CONCEPTS
## 3.1 What Makes DeepAgents Different
- Built-in planning with write_todos()
- Filesystem for context offloading (read_file/write_file)
- Subagent spawning with task()
- Persistent memory via LangGraph Store
- Native HITL support with interrupt()

## 3.2 Orchestration Strategies
Detailed explanation of when to use each strategy:

### Strategy 1: Direct Tool Calls (Simple Requests)
- When: Single domain, < 3 tool calls
- Example: "Look up user ID-1234"
- Code example showing direct tool invocation

### Strategy 2: Planning with Todos (Complex Requests)
- When: Multi-step, multi-tool workflows
- Example: "Process this work order end-to-end"
- Code example with write_todos()

### Strategy 3: Subagent Spawning (Domain Separation)
- When: Multiple specialized agents needed
- Example: "Migrate CSV and create work orders for each row"
- Code example with task() calls

### Strategy 4: Filesystem Offloading (Large Context)
- When: Context > 10K tokens
- Example: Processing large documents
- Code example with write_file()/read_file()

## 3.3 HITL (Human-in-the-Loop) Patterns
- When to use interrupt() for human approval
- 3 standard gates: Mapping Approval, Hierarchy Verification, Final Confirmation
- Resume patterns using LangGraph checkpointing
- WebSocket events for real-time gate notifications

# 4. SPECIALIZED AGENTS (5 Agents, 31+ Tools)

## 4.1 Migration Agent (6 Tools)
**Purpose:** Handle CSV/Excel data migration to database

For each tool, include:
- Tool signature with type hints
- Description (1-2 sentences)
- Input parameters with types and descriptions
- Return type with example output
- When to use it
- Example invocation
- Error handling notes

### Tools:
1. parse_csv_file(file_path: str) -> dict
2. validate_data_schema(data: dict, schema: dict) -> dict
3. map_columns_to_db(columns: list, mapping: dict) -> dict
4. import_records(data: list, table_name: str) -> dict
5. check_duplicates(data: list, key_fields: list) -> dict
6. get_migration_status(migration_id: str) -> dict

Include a Python code block showing all 6 tools with @tool decorator.

## 4.2 Doc RAG Agent (6 Tools)
**Purpose:** Document indexing, semantic search, and retrieval

### Tools:
1. index_document(file_path: str, metadata: dict) -> dict
2. embed_text(text: str) -> dict
3. query_docs(query: str, top_k: int) -> dict
4. semantic_search(query: str, filters: dict) -> dict
5. get_procedures(category: str) -> dict
6. chunk_document(file_path: str, chunk_size: int) -> dict

Include OpenAI embeddings integration example.

## 4.3 Work Order Engine Agent (15 Tools - Full Workflow)
**Purpose:** Complete 15-step work order creation workflow

Show as a step-by-step flow diagram, then detail each tool:

### Steps 1-5: Discovery & Assessment
1. source_identify - Generate WO ID
2. collect_workspace - Gather workspace data
3. assess_criticality - AI-powered urgency (Claude Haiku)
4. analyze_safety - Safety requirements
5. check_compliance - Regulatory compliance

### Steps 6-10: Validation & Planning
6. validate_location - Location accessibility
7. gather_asset_intel - Asset history
8. check_site_clearance - Permits & access
9. generate_warranty_intel - Parts & duration
10. check_spare_parts - Inventory check

### Steps 11-15: Resource & Schedule
11. suggest_vendors - Vendor recommendations
12. allocate_resource - Technician assignment
13. optimize_schedule - Time slot optimization
14. create_workspace_pin - Tracking PIN
15. create_journey_log - Lifecycle log

For each tool include the same detailed format as Migration Agent.

## 4.4 Compliance Agent (2 Tools)
**Purpose:** Regulatory and document compliance

### Tools:
1. check_requirements(asset_type: str, work_type: str) -> dict
2. verify_documents(doc_ids: list) -> dict

## 4.5 UDR Agent (2 Tools)
**Purpose:** User and Data Repository operations

### Tools:
1. lookup_user(user_id: str) -> dict
2. query_table(table_name: str, filters: dict) -> list

# 5. ORCHESTRATION FLOWS
## 5.1 Simple Work Order Creation Flow
Sequence diagram showing:
User → Orchestrator → WO Engine Agent → Tools (1-15) → Database → Response

Include:
- Request example: "Process HVAC-301 with grinding noise"
- Expected response time: ~2.5 seconds
- Token usage: ~4,200 tokens
- Tools called: 15
- Output: Work order JSON with PIN and Log ID

## 5.2 CSV Migration Flow
Sequence diagram showing:
User → Orchestrator → Migration Agent → Tools (parse, validate, map, import) → Database

Include HITL gate for mapping approval.

## 5.3 Multi-Agent Coordination Flow
Show a complex scenario:
"Process work order, check compliance, find procedures, and notify user"
- Orchestrator spawns 4 subagents
- Each handles their domain
- Results aggregated and returned

Include parallel execution diagram.

## 5.4 HITL Gate Flow
Detailed flow showing:
- Agent encounters uncertainty
- interrupt() called
- WebSocket event emitted
- User reviews in UI
- User approves/rejects
- Graph resumes with checkpoint

# 6. SYSTEM PROMPT ENGINEERING
## 6.1 Master System Prompt
Include the complete system prompt that defines:
- Agent identity and capabilities
- Tool registry (all 31 tools listed)
- Orchestration strategy rules
- Output formatting requirements
- Error handling guidelines

## 6.2 Per-Agent Instructions
Sub-prompts for each specialized agent.

## 6.3 Output Format Templates
Examples of expected response structures.

# 7. CODE IMPLEMENTATION
## 7.1 Project Structure
Complete file tree:
```
deepagent-workflow/
├── src/
│   ├── agents/
│   │   ├── orchestrator.py
│   │   ├── migration_agent.py
│   │   ├── doc_rag_agent.py
│   │   ├── wo_engine_agent.py
│   │   ├── compliance_agent.py
│   │   ├── udr_agent.py
│   │   └── system_prompt.py
│   ├── api/
│   │   ├── main.py
│   │   └── routes/
│   ├── database/
│   │   ├── connection.py
│   │   └── models.py
│   └── config/
│       └── settings.py
├── tests/
├── docker-compose.yml
└── requirements.txt
```

## 7.2 Orchestrator Implementation
Complete Python code for DeepAgentOrchestrator class showing:
- Model initialization (gpt-4o-mini)
- Tool registration (all 31 tools)
- DeepAgent creation with create_deep_agent()
- process_request() method
- Error handling
- Logging integration

## 7.3 Tool Definition Pattern
Standard template for creating new tools with:
- @tool decorator
- Type hints
- Docstring format
- Error handling
- Logging
- Return structure

## 7.4 FastAPI Integration
Show the API layer:
- POST /api/v1/workflow/process endpoint
- WebSocket /ws/workflow/{id} for real-time
- Request/response Pydantic models
- Lifespan management

# 8. DATABASE SCHEMA
## 8.1 Tables
Show SQLAlchemy models for:
- work_orders (id, work_order_id, status, criticality, etc.)
- execution_logs (tool calls audit trail)
- migration_jobs
- migration_field_mappings
- mapping_templates

Include relationships and indexes.

## 8.2 Common Queries
SQL examples for:
- Get work order with execution history
- Aggregate tool usage stats
- Find longest-running workflows

# 9. API CONTRACTS
## 9.1 REST Endpoints
For each endpoint show:
- Method and path
- Request schema (JSON)
- Response schema (JSON)
- Status codes
- Error responses
- Example curl command

Include:
- POST /api/v1/workflow/process
- GET /api/v1/workflow/status/{id}
- POST /api/v1/migration/upload
- POST /api/v1/migration/approve
- GET /api/v1/workflow/tools

## 9.2 WebSocket Events
List all real-time events:
- tool_started
- tool_completed
- agent_switch
- gate_interrupt
- workflow_completed
- error

Include payload schemas for each.

# 10. EXAMPLES & USE CASES

## 10.1 Example 1: Simple Work Order
Full request/response example:

Request:
```json
{
  "message": "Process HVAC-301 work order with grinding noise on Floor 5"
}
```

Response with all 15 steps shown.

## 10.2 Example 2: Complex Multi-Agent
"Migrate sensors.csv to assets table, then check compliance for each new asset"

## 10.3 Example 3: HITL Gate
Mapping approval scenario with user interaction.

## 10.4 Example 4: Error Handling
Tool failure and recovery example.

# 11. PERFORMANCE & MONITORING

## 11.1 Performance Metrics
- Average response times per agent
- Token usage statistics
- Tool execution latencies
- Throughput (requests/second)

## 11.2 Cost Analysis
Per-request cost breakdown:
- OpenAI GPT-4o calls
- Claude Haiku calls
- Claude Sonnet calls
- Database operations
- Total ~$0.05-0.15 per work order

## 11.3 Monitoring Setup
- Prometheus metrics
- LangSmith integration
- Custom dashboards
- Alert rules

# 12. DEPLOYMENT

## 12.1 Local Development
- Setup commands
- Environment configuration
- Running locally

## 12.2 Docker Deployment
Complete docker-compose.yml with explanations

## 12.3 Production (Kubernetes)
- Resource requirements
- Scaling guidelines
- Health checks
- Rolling updates

# 13. TROUBLESHOOTING

Common issues and solutions:
- Tool not found errors
- Context window exceeded
- Database connection issues
- WebSocket disconnections
- HITL timeout handling

# 14. BEST PRACTICES

## 14.1 Tool Design
- Keep tools focused (single responsibility)
- Always include type hints
- Comprehensive error handling
- Detailed docstrings
- Idempotent where possible

## 14.2 Prompt Engineering
- Clear role definition
- Explicit tool descriptions
- Example-driven instructions
- Output format specification

## 14.3 Production Patterns
- Rate limiting per user
- Tool execution timeouts
- Circuit breakers
- Retry strategies
- Graceful degradation

# 15. ROADMAP & EXTENSIONS

Future enhancements:
- Adding new agents
- Multi-modal support (images, video)
- Voice interface
- Mobile SDKs
- Advanced HITL workflows

# 16. APPENDIX

## A. Complete Tool Reference (Alphabetical Index)
Quick lookup table for all 31 tools.

## B. Environment Variables
Complete list with descriptions.

## C. Glossary
Define all technical terms:
- DeepAgent
- HITL
- Subagent
- Checkpoint
- Tool call
- etc.

## D. Related Documentation
Links to:
- LangChain Documentation
- DeepAgents GitHub
- API Documentation
- Architecture Diagrams

---

FORMATTING REQUIREMENTS:
- Use proper Markdown syntax
- Include emoji icons for visual hierarchy (🤖 🔧 📊 etc.)
- Add code blocks with language specifiers (```python, ```yaml, ```sql)
- Use tables for structured data
- Include ASCII diagrams for architecture
- Add sequence diagrams using mermaid syntax where appropriate
- Use callout boxes for important notes (> **Note:**, > **⚠️ Warning:**)
- Include badges at top (version, license, status)
- Add table of contents with anchor links
- Use horizontal rules (---) between major sections
- Make it ~3000-5000 lines for completeness
- Include working code examples that can be copy-pasted
- All examples should be production-ready

Save the final file as /docs/DEEPAGENT_FLOW.md with proper formatting and structure.
```

---

## 🎯 ALTERNATIVE SHORTER PROMPT (For Quick Generation)

If you need a more focused, faster generation:

```
Create /docs/DEEPAGENT_FLOW.md - a comprehensive technical documentation file for our 
LangChain DeepAgent agentic workflow system.

Include these sections with full details:

1. **Overview**: What is DeepAgent, why use it (70% code reduction vs vanilla LangChain)

2. **Architecture Diagram** (ASCII): Show User → API → Orchestrator → 5 Agents → Tools → DB

3. **5 Specialized Agents** with complete tool listings:
   - Migration Agent (6 tools): parse_csv_file, validate_data_schema, map_columns_to_db, 
     import_records, check_duplicates, get_migration_status
   - Doc RAG Agent (6 tools): index_document, embed_text, query_docs, semantic_search, 
     get_procedures, chunk_document
   - WO Engine Agent (15 tools): source_identify, collect_workspace, assess_criticality, 
     analyze_safety, check_compliance, validate_location, gather_asset_intel, 
     check_site_clearance, generate_warranty_intel, check_spare_parts, suggest_vendors, 
     allocate_resource, optimize_schedule, create_workspace_pin, create_journey_log
   - Compliance Agent (2 tools): check_requirements, verify_documents
   - UDR Agent (2 tools): lookup_user, query_table

4. **For each tool include**:
   - Function signature with types
   - One-line description
   - Input parameters
   - Output schema
   - Example invocation

5. **Orchestration Patterns**:
   - Direct tool calls (simple)
   - Planning with write_todos (complex)
   - Subagent spawning with task()
   - Filesystem offloading for large context
   - HITL gates with interrupt()

6. **Complete System Prompt** for the orchestrator

7. **Full Python code example** showing:
   - DeepAgentOrchestrator class
   - Tool registration
   - create_deep_agent() usage
   - process_request() method

8. **API Endpoints**:
   - POST /api/v1/workflow/process
   - WebSocket /ws/workflow/{id}
   - With request/response examples

9. **3 End-to-End Examples**:
   - Simple work order creation
   - CSV migration with HITL
   - Multi-agent compliance check

10. **Performance & Cost**: 
    - Avg 2.5s response time
    - ~4200 tokens per work order
    - $0.05-0.15 per request

Use Markdown formatting with emoji headers, code blocks (python/yaml/sql), 
ASCII diagrams, tables, and clear sectioning. Make it ~2000 lines, 
production-ready, and copy-pasteable for developers.
```

---

## 🎯 MICRO-PROMPTS (Section-by-Section)

If you want to generate the documentation section by section, use these focused prompts:

### Prompt 1: Architecture Section
```
Create /docs/sections/01_architecture.md documenting our DeepAgent system architecture:
- ASCII diagram with all components
- Technology stack table
- Component breakdown
- Data flow diagram
- External integrations
Focus on visual clarity. Include a Mermaid diagram for the flow.
```

### Prompt 2: Agents & Tools Section
```
Create /docs/sections/02_agents_and_tools.md with complete documentation for:
- 5 specialized agents
- All 31+ tools with signatures, descriptions, examples
- Python code with @tool decorator
- Usage patterns for each tool
Make it a reference guide developers can grep through.
```

### Prompt 3: Orchestration Section
```
Create /docs/sections/03_orchestration.md explaining:
- 4 orchestration strategies (direct, planning, subagents, filesystem)
- HITL gate patterns
- Multi-agent coordination
- Sequence diagrams
- Real code examples
Include decision tree for choosing strategy.
```

### Prompt 4: API Section
```
Create /docs/sections/04_api_reference.md with:
- All REST endpoints
- WebSocket events
- Request/response schemas
- curl examples
- TypeScript types
- Error codes
Make it usable as an API reference.
```

### Prompt 5: Examples Section
```
Create /docs/sections/05_examples.md with 10 detailed end-to-end examples:
1. Simple work order
2. CSV migration
3. Multi-agent workflow
4. HITL gate scenario
5. Error recovery
6. Compliance check
7. Document search
8. User lookup
9. Parallel execution
10. Complex orchestration

Each example: Request → Tool sequence → Response → Explanation
```

---

## 🎯 BONUS: Generate Companion Files

### Generate Sequence Diagrams
```
Create /docs/diagrams/sequence_diagrams.md with Mermaid sequence diagrams for:
1. Simple work order flow
2. CSV migration with HITL
3. Multi-agent coordination
4. Error handling
5. WebSocket event flow

Use mermaid syntax:
```mermaid
sequenceDiagram
    User->>API: POST /workflow/process
    API->>Orchestrator: process_request()
    ...
```
```

### Generate API Documentation
```
Create /docs/api/openapi.yaml - complete OpenAPI 3.0 specification for all endpoints.
Include all request/response schemas, examples, error responses, and authentication.
Make it valid YAML that can be used with Swagger UI.
```

### Generate Postman Collection
```
Create /docs/api/postman_collection.json - a complete Postman collection with:
- All API endpoints
- Example requests
- Environment variables
- Authentication setup
- Tests for status codes

Make it directly importable into Postman.
```

---

## 📊 USAGE INSTRUCTIONS

### Step 1: Choose Your Prompt
- **Master Prompt** → Complete documentation (one-shot, ~5000 lines)
- **Short Prompt** → Focused docs (~2000 lines)
- **Micro-Prompts** → Section by section (better control)

### Step 2: Open Claude Code
```bash
cd /path/to/your/project
claude code
```

### Step 3: Paste the Prompt
Copy your chosen prompt and paste it into Claude Code.

### Step 4: Review & Iterate
- Review the generated MD file
- Use follow-up prompts to enhance specific sections
- Add more examples if needed

### Step 5: Combine (if using Micro-Prompts)
```bash
# Combine all sections into one file
cat docs/sections/*.md > docs/DEEPAGENT_FLOW.md
```

---

## 🎯 ENHANCEMENT PROMPTS

After generating the main doc, use these to add more detail:

### Add More Code Examples
```
@docs/DEEPAGENT_FLOW.md Add 5 more comprehensive code examples for:
1. Custom tool creation
2. Multi-step workflow with error recovery
3. WebSocket client implementation
4. Database transaction handling
5. Async tool execution

Each example should be 30-50 lines, production-ready, with comments.
```

### Add Troubleshooting Section
```
@docs/DEEPAGENT_FLOW.md Expand the troubleshooting section with 15 common issues:
- Tool registration errors
- Context window exceeded
- Async/await mistakes
- Database connection pool exhaustion
- Memory leaks
- API rate limits
- WebSocket disconnections
- HITL timeouts
- Token usage spikes
- Slow tool execution
- Authentication issues
- CORS errors
- Deployment problems
- Docker networking
- Monitoring gaps

For each: symptom, cause, solution, prevention.
```

### Add Visual Diagrams
```
@docs/DEEPAGENT_FLOW.md Add Mermaid diagrams for:
1. System architecture (graph diagram)
2. Sequence diagrams for each flow
3. State machine for HITL gates
4. Class diagram for tool relationships
5. ER diagram for database

Replace ASCII diagrams where appropriate.
```

### Add Performance Benchmarks
```
@docs/DEEPAGENT_FLOW.md Add detailed performance benchmarks section:
- Tool execution times (P50, P95, P99)
- End-to-end latency by workflow type
- Token usage statistics
- Cost per operation
- Throughput limits
- Scalability characteristics

Include actual numbers and graphs (as markdown tables).
```

---

## 💡 PRO TIPS

### 1. Iterative Refinement
Don't try to get perfect docs in one shot. Generate, review, refine:
```
First prompt → Get basic structure
Second prompt → Add code examples  
Third prompt → Add diagrams
Fourth prompt → Polish and proofread
```

### 2. Use Existing Code as Context
If you have the actual implementation files, reference them:
```
@workspace Look at our existing files in /src/agents/ and create 
/docs/DEEPAGENT_FLOW.md that accurately documents what's actually 
implemented. Include real code snippets from the actual files.
```

### 3. Generate Multiple Versions
```
Create 3 versions of the documentation:
- /docs/DEEPAGENT_FLOW_TECHNICAL.md (for developers)
- /docs/DEEPAGENT_FLOW_BUSINESS.md (for stakeholders)
- /docs/DEEPAGENT_FLOW_QUICKSTART.md (for new team members)
```

### 4. Validate the Output
After generation, verify:
- All 31 tools are documented
- Code examples are syntactically correct
- Links work
- Tables render properly
- Code blocks have language specifiers

### 5. Generate Supplementary Files
```
Also create:
- /docs/CONTRIBUTING.md - How to add new agents/tools
- /docs/CHANGELOG.md - Version history
- /docs/MIGRATION_GUIDE.md - Upgrading versions
- /docs/SECURITY.md - Security best practices
```

---

## 📦 EXPECTED OUTPUT STRUCTURE

After running the master prompt, you should get a file like this:

```
docs/
└── DEEPAGENT_FLOW.md (3000-5000 lines)
    ├── 1. Executive Summary
    ├── 2. System Architecture
    ├── 3. DeepAgent Core Concepts
    ├── 4. Specialized Agents (5 agents, 31 tools)
    ├── 5. Orchestration Flows
    ├── 6. System Prompt Engineering
    ├── 7. Code Implementation
    ├── 8. Database Schema
    ├── 9. API Contracts
    ├── 10. Examples & Use Cases
    ├── 11. Performance & Monitoring
    ├── 12. Deployment
    ├── 13. Troubleshooting
    ├── 14. Best Practices
    ├── 15. Roadmap & Extensions
    └── 16. Appendix
```

---

## ✅ Quality Checklist

After Claude Code generates the file, verify:

- [ ] All 5 agents documented (Migration, Doc RAG, WO Engine, Compliance, UDR)
- [ ] All 31+ tools have full documentation
- [ ] ASCII architecture diagram present
- [ ] Mermaid sequence diagrams included
- [ ] Complete Python code for orchestrator
- [ ] All API endpoints documented
- [ ] WebSocket events listed
- [ ] 3+ end-to-end examples provided
- [ ] Database schema with SQLAlchemy models
- [ ] System prompt included
- [ ] Performance metrics provided
- [ ] Cost analysis included
- [ ] Troubleshooting guide present
- [ ] Best practices section
- [ ] Glossary of terms
- [ ] Table of contents with anchors
- [ ] Code blocks have language tags
- [ ] Tables are properly formatted
- [ ] Emoji icons for visual hierarchy
- [ ] File is 3000-5000 lines
- [ ] All sections are detailed (not just headers)

---

## 🚀 Ready to Use!

1. **Copy the Master Prompt** from above
2. **Open Claude Code** in your project directory
3. **Paste the prompt**
4. **Wait for generation** (~2-3 minutes)
5. **Review the output**
6. **Use enhancement prompts** to refine

You'll have a complete, production-ready documentation file that explains your entire DeepAgent system! 🎉

---

**Created:** May 13, 2026  
**Purpose:** Generate comprehensive LangChain DeepAgent documentation via Claude Code  
**Estimated output:** 3000-5000 line Markdown file  
**Time to generate:** 2-5 minutes per section
