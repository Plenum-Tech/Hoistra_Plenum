# Homework 1 — first observed conversation

- Provider: OpenRouter, via the existing OpenAI-compatible client.
- Model: `openai/gpt-4o-mini`.
- Endpoint: `http://127.0.0.1:18008/api/workflow/run` on the VPS.
- Both isolated containers were healthy before the question.
- Request: “Help me with my data.”
- Result: four-way clarification (CSV/Excel, Word/PDF, live Fiix, existing CMMS data).
- Actual tool calls: none. No business workflow was started.
- Session: `hw1-c01-0d1f6e8f4c0b`.
- Record: `hw1-session.jsonl`; raw response: ignored `runtime/hw1-c01-0d1f6e8f4c0b.json`.
- Student assessment remains pending; this is not an authorization or downstream-domain test.

## Observed issue to investigate

The response metadata and workspace state say `fiix_sync` and saved space `schema`, even though the response was only a generic clarification with no tools. Do not infer that a Fiix sync occurred. Check whether route classification is accidentally matching the assistant's offered options rather than the user's request. This is a hypothesis, not an established cause. Preserve the original evidence before changing routing code.

## Setup correction

The import smoke check initially failed because the service declared `sqlalchemy` without its async extra. The homework branch now declares `sqlalchemy[asyncio]>=2.0`; the rebuilt image imports successfully. No routing or system-prompt edits were made for this conversation.
