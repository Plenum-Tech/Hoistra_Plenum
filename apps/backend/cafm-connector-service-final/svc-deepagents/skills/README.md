# Agent skills

One `SKILL.md` per sub-agent. Each file is both the **routing entry** the orchestrator matches
a question against, and the **system prompt** that agent runs with once it is chosen. Keeping
them on one file is the point: a routing table in code and a prompt in a string drift apart, a
routing table derived from the prompt cannot.

```
user question
   │
   ▼
Orchestrator ── select_skill(question) ─► skills/*/SKILL.md front matter
   │                                       (triggers → primary_agent + also_relevant)
   ▼
task("compliance", …)   task("wo_engine", …)      ← one per domain, in parallel
   │                        │
   │  each sub-agent runs with:  query-builder/SKILL.md + its own SKILL.md
   ▼                        ▼
        rows from one or more plenum_cafm tables
   │
   ▼
Orchestrator writes ONE summary for the user
```

## The files

| File | Agent | Covers |
|------|-------|--------|
| `query-builder/SKILL.md` | *(shared)* | The RESOLVE → LOCATE → PULL → JOIN → SUMMARISE loop, the live-schema warning, the cross-domain join key map, and the answer contract. Prepended to every agent. Never routed to. |
| `udr/SKILL.md` | `udr` | Any plenum_cafm table no engine owns, and any question needing two or more tables joined. The fallback. |
| `wo-engine/SKILL.md` | `wo_engine` | Work order lifecycle, approvals, PPM schedules, dashboard counts. |
| `compliance/SKILL.md` | `compliance` | Building certificates, vendor accreditations, expiry, blocked vendors, country packs, coverage. |
| `contract-performance/SKILL.md` | `contract_performance` | Vendor SLA scoring, scorecards, PPM completion, invoice verification. |
| `energy-intelligence/SKILL.md` | `energy_intelligence` | Meters, EUI vs TM46, anomalies, condition cross-reference, monthly report. |
| `doc-rag/SKILL.md` | `doc_rag` | What an indexed document says, with citations. |
| `migration/SKILL.md` | `migration` | CSV/Excel migration and live Fiix schema mapping and sync. |

## Front matter

```yaml
---
name: compliance-engine          # skill id, shown in the orchestrator's registry
agent: compliance                # the task() target — must match a key in meta_tools
description: …                   # one paragraph; this is what the orchestrator reads to route
triggers:                        # phrases matched against the user's own words
  - certificate
  - lapsed
  - blocked vendor
---
```

Everything below the front matter is the agent's prompt body.

## How a question is scored

`src/agents/skills.py` normalises the question and each trigger to whole, plural-folded words,
then scores every skill by the **word count of the triggers it hit** — so `"baseline drift"`
(2) outweighs `"due"` (1) and a specific phrase beats a word that appears in several domains.

Two rules keep it honest:

- **UDR never outranks a specialist that scored.** It is the fallback for what no engine owns;
  a generic opener like *"show me"* must not pull a certificate question away from Compliance.
- **Nothing matched ⇒ `clarify_first: true`.** The orchestrator asks which of the four data
  capabilities the user means rather than guessing a pipeline.

## Editing a skill

Edit the `.md` — no code change is needed. The body reaches the agent on the next process
start and the triggers reach the router at the same time.

- Prefer a **specific phrase** over a bare word. `"first fix"` routes; `"fix"` collides.
- A word that means different things in two domains needs qualifying on both sides —
  `"gas safety"` is compliance, `"gas consumption"` is energy, and neither should own `"gas"`.
- Run `pytest tests/test_skill_routing.py` after editing. It asserts every agent owns a skill,
  every skill routes its own worked examples, and cross-domain questions still name both
  domains.
- `SKILLS_DIR` overrides the directory for tests and containers.
