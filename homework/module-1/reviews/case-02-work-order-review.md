# Case 2 General User work-order summaries

Question: Show the work-order status summaries for my assigned building hw1-building-a1. Show two records and tell me whether more matching records remain.

## Exact answer

Showing 2 of 25 matching records in scope hw1-building-a1. Partial result; this is not a whole-scope summary. {"tool": "work_order_status", "scope": ["hw1-building-a1"], "request_id": "b91983d5-0512-4660-8ab0-24bb0e83c14e", "policy_version": "hw1-point2-v1", "records": [{"record_id": "hw1-wo-a1-00", "building_id": "hw1-building-a1", "work_order_id": "fixture-wo-0", "status": "open", "asset_id": "fixture-asset-a1", "due_at": "2026-11-01", "source_reference": "fixture:hw1-wo-a1-00"}, {"record_id": "hw1-wo-a1-01", "building_id": "hw1-building-a1", "work_order_id": "fixture-wo-1", "status": "open", "asset_id": "fixture-asset-a1", "due_at": "2026-11-01", "source_reference": "fixture:hw1-wo-a1-01"}], "total_matching": 25, "returned": 2, "offset": 0, "complete": false, "next_offset": 2, "data_source": "fictional_homework_permission_fixtures"}

## Checks

- Assigned-building summary access: PASS
- A1-only returned scope: PASS
- Count25 and page fields match independent stored-fixture query: PASS
- Two of25,partial page,next offset2: PASS
- Permitted summary fields/source references: PASS
- No operational work-order tool executed: PASS
- Plain-language external-management explanation: PRESENTATION GAP
- Plain-language fictional data and readable summaries: PRESENTATION GAP
- Exact execution input/output retained with trace: PASS
- Report date honesty: PASS

Trace a6c05f53-1a21-436e-8a14-dc883a919524
Conversation df7d7be3-d631-4e3b-94cb-f9616b0fa00f

Raw JSON rendering and missing plain-language context belong in Python. No runtime changes made. Original records preserved. Assistant assessment pending human review.
