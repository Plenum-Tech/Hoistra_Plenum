# Manifest and prompt assembler — implementation checkpoint

Implemented on `homework-1-work-order-agent`, 2026-10-25 (Asia/Dubai). Specification v0.14.0 and policy `hw1-point2-v1` remain unchanged. This checkpoint implements the first approved architecture step only; Point 3 has not started.

## What changed

| File | Change |
|---|---|
| skills/prompt-manifest.json | Explicit read-only profile, versions, ordered instruction IDs/kinds/paths/SHA-256 hashes, one-tool limit declaration and role/tool inventory. The inventory must exactly match Python permissions; it does not grant them. |
| src/agents/prompt_assembly.py | Strict manifest validation, required file/checksum/order validation, guarded paths, immutable assembled prompt and version/hash metadata. Reads reviewed files on each assembly without silently using a stale cached body. |
| skills/orchestrator/SKILL.md | Existing read-only coordinator instructions extracted unchanged. The manifest marks it core; it declares no legacy agent and creates no new routable specialist. |
| skills/policies/permissions.md | Existing identity, role, building and document-read instructions extracted unchanged. |
| skills/policies/actions-and-evidence.md | Existing no-fallback and one-read boundary instructions extracted unchanged. It does not yet contain every future action/approval procedure. |
| skills/policies/responses-and-audit.md | Existing grounding, partial-result, truthful outcome and fictional-data instructions extracted unchanged. |
| src/agents/homework_read_prompt.py | Replaced duplicated Python instruction literal with thin assembly/string-compatibility wrappers. |
| src/services/homework_reads.py: plan_and_read() | Uses assembled prompt; returns additive prompt_metadata showing loaded instructions, hashes, versions and bound tools. Invalid assembly stops before any model/tool execution. Tool schemas, guards and execution limit are unchanged. |
| src/api/main.py | Validates prompt configuration at startup in homework identity mode. Missing/drifted required instructions prevent startup. |

The four instruction fragments are concatenated in manifest order, then the same trusted identity context is appended. Golden hashes prove the resulting prompt is byte-for-byte identical for all four roles. Therefore this is a structural extraction, not a new policy or a full specification-to-skills conversion.

## Active request flow

```text
Authenticated request
  → validate manifest against the Python capability registry
  → read core + permissions + action boundaries + response rules
  → append server-owned role/organization/buildings
  → model chooses one of the existing permitted read tools
  → unchanged dispatcher / scoped SQL / audit enforcement
  → deterministic verified-result formatter
  → answer plus prompt_metadata
```

The model receives instruction text, not an instruction to browse arbitrary Markdown paths. No domain skill or legacy shortcut is enabled. The seven existing routable specialists and legacy shared query-builder selection are unchanged and remain blocked in homework execution. There is still no second answer-writing model call.

## Verification

- **57 automated tests passed:** the existing 33 Point 1/2 tests plus 24 assembly tests.
- The four role-specific pre-change prompt hashes match. The combined instruction body hash also matches.
- Exact pre-change source hashes match for nine capability/schema/SQL/audit declarations, including the dispatcher and repository classes.
- Strict failures tested: missing/invalid manifest, duplicate keys/entries/tools, unsupported version, extra fields, role/tool escalation or removal, wrong order, missing/empty/modified files, traversal/symlink escape and accidental domain enablement.
- Invalid assembly is verified to stop before the model is called.
- **44 live Point 2 checks passed again**, including scope, document/role limits, real PostgreSQL permissions, audit outage/recovery and a real OpenRouter read.
- Live response metadata identifies four loaded instruction files, zero selected domain skills and policy `hw1-point2-v1`.
- The example rendered prompt matches the live request's prompt hash. It contains only fictional context and no session credential.
- Earlier Point 2 evidence remains untouched; the new regression evidence uses separate assembly filenames.

Evidence: [test log](hw1-prompt-assembly-tests.txt), [live checks](hw1-prompt-assembly-live-checks.json), [receipt](hw1-prompt-assembly-receipt.json), [rendered example](hw1-prompt-assembly-example.txt), [code diff](hw1-prompt-assembly.patch).

## Deliberate limits

The manifest/assembler currently supports the reviewed core/shared read-only profile. Adding a domain skill requires a separately reviewed profile/loader extension and verified tool integration; merely adding a Markdown file does not enable it. The existing eight domain/shared skill files and thirteen supporting compliance documents have not been rewritten. HOI, usage and communications domain skills are not yet created.

Checksums detect drift, not whether natural-language policy is semantically correct. Instruction changes still require review, new receipts and regression tests. The response metadata is useful request evidence; it is not a completed Langfuse/OTel/Trace Lab integration. Authorization remains in Python/database checks.

No new business permission, mutation, database schema, source adapter, email delivery, scheduler, stateful/resume path or UI change is introduced. No Git commit created. Awaiting review before the next architecture step.
