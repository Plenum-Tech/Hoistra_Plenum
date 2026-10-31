# Unified agent milestone — specification 0.37.0

This implementation checkpoint supplements historical policy mappings. It does not
relabel earlier receipts or certify full Homework readiness/active deployment.

| Required acceptance | Enforcement/instructions | Verification |
|---|---|---|
| hash_checked_enabled_domain_manifest | `unified_prompt.py`, `skills/unified-manifest.json`, `orchestrator/unified.md`; pins seven reviewed profiles and selected domain assembly, request-local trusted principal/date | Live status/local hash parity; actual provider prompt registry hash; drift/unsafe path/duplicate-key regressions |
| role_aware_routing_and_permitted_clarification | Central router checks capability before planning; role-aware enabled help, mixed permitted-domain clarification, explicit outside-platform refusals | Actual General/Officer help and mixed forbidden requests; manager mixed request/no join; outside-platform WO refusal |
| shared_source_compatibility_and_no_mental_joins | Typed source adapter checks trusted building, immutable digest/version and explicit entity/period/unit; missing/contradictory metadata cannot join | Actual accepted same-metadata pair; bad unit/period/entity and contract/certificate missing metadata; denied roles/scopes; no source edits |
| bounded_tools_without_model_commit_or_legacy_bypass | Per-profile1/2/4 budgets; every model loop checks bound tools; human decisions remain separate APIs; read facade unchanged outside unified context | API SQL/commit/legacy denials; malicious model human-action regression; actual provider tools match typed bound lists/budgets |
| owned_conversations_and_safe_trace_export | Central ownership for read/clarification/domain turns, stale-assignment check, request-local prompt context, central actual-tool traces and safe owned export/evaluation metadata | Actual cross-user continuation/read/export404; no message/answer/source bodies in export; selected versus executed operations remain separate |
| cross_domain_stale_concurrent_and_failure_cases | Existing domain commit locks/previews/versions; centralized truthful partial/failed outcome and failed-read observation | Actual concurrent correction selects one representation; stale competing preview409; Energy/Contract invalid periods422; source/history preserved |
| at_least_ten_real_provider_hoistra_conversation_evaluations | Genuine configured OpenRouter; no mock/fallback-only pass; explicit source-backed expectations for each scenario | Gate checks thirteen conversations across four roles, seven domain/read profiles plus compatibility, tool result arithmetic/projection, unified hashes and owned trace parity |

`build-unified-profile.py` pins existing domain manifests; it does not rewrite their
permission policies. Domain assemblers append unified instructions only in a central
request context, then hash the actual rendered prompt. The old direct read facade
retains its original text/tool permissions. Existing startup domain validators remain
active and the central registry additionally rejects any unpinned enabled manifest.

Central traces supersede child traces for one request when exported. Measured usage
also deduplicates central/child observations, retaining both immutable source IDs.
Provider calls, selected names and actual operations are distinct. A failed delegate
never becomes empty success; known failed read attempts retain safe trace metadata.
No trace export contains private conversation messages or raw document/tool bodies.

A source ID is not access or compatibility proof. The shared comparison is a diagnostic
only. It does not invent an entity/date/unit join or perform cross-domain arithmetic;
verified domain adapters retain responsibility for their own calculations. Model text,
previous turns and documents cannot alter the principal or confirm a business action.

Actual reference materials read from `/root/cartwheel-homeworks` are mapped in
`hw1-hoistra-evaluation-mapping.md`; that reference project is not modified. Private
raw evaluation records are owner-only under the runtime attempt directory; public
`unified-agent-evaluations.jsonl` contains safe scenario/tool/status/hash facts only.
The gate receipt identifies the current fresh run, not an accumulation of stale passes.

Run `setup-unified-fixtures.py`, compile the registry, rebuild only the dedicated
candidate, and execute `milestones/gates/06-unified-agent.py`. The gate returns nonzero
for a failed check and fingerprints retained original sources under the non-owner app
role. Final readiness must still recheck all prior gates and perform authorized isolated
activation/rollback verification; this milestone does not activate18008.
