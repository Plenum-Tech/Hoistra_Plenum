# Shared final-answer synthesis: implementation and exact case outputs

The isolated active agent now uses a shared, tool-free model call for final wording. The final-answer system prompt contains no domain-specific prose. Domain skills still govern planning; Python still controls permissions, calculations and execution.

Verification:564 tests and65 subtests passed (18 warnings),11 representative genuine-provider candidate conversations across8 routing paths, unchanged four-role tool grants, denied General/Officer Energy access and denied foreign-user trace access. One final conversation turn is stored per request.

Tool results are preserved unchanged in the wrapper. An unassessed limitations field is omitted instead of being invented as an empty list. Oversized input fails explicitly. HOI history is paginated at the tool boundary using existing limit/offset fields without deleting events.

## Case 1

Exact question:

> Calculate Energy EUI for building_id hw1-building-a1, meter_id hw1-energy-m2-condition, period_start 2026-10-01, period_end 2026-10-31.

### Original answer

```text
Verified 1200 kWh across 1 readings for 2026-10-01 through 2026-10-31; latest source reading 2026-10-01T00:00:00+00:00. Period EUI is 12 kWh/m²; annualized EUI is 141.2903225806451612903225806 kWh/m²/year. TM46 indicative General office benchmark is 95 kWh/m²/year; deviation is 48.73%. Benchmark excess is 393.1506849315068493150684932 kWh. Its tariff-derived cost is £78.63.
```

### New answer — unedited model output

Answer<br>
The Energy EUI (Energy Use Intensity) for building_id `hw1-building-a1` and meter_id `hw1-energy-m2-condition` for the period from October 1, 2026, to October 31, 2026, is calculated as follows:

- **Consumption**: 1200 kWh
- **Floor Area**: 100 m²
- **Period Days**: 31 days

The Energy EUI is calculated using the formula:<br>
\[ \text{EUI} = \frac{\text{consumption kWh}}{\text{floor area m²}} \]<br>
\[ \text{EUI} = \frac{1200 \text{ kWh}}{100 \text{ m²}} = 12 \text{ kWh/m²} \]

The annualized EUI is calculated as:<br>
\[ \text{Annualized EUI} = \text{EUI} \times \frac{365}{\text{period days}} \]<br>
\[ \text{Annualized EUI} = 12 \text{ kWh/m²} \times \frac{365}{31} \approx 141.29 \text{ kWh/m²/year} \]

The benchmark for a general office is 95 kWh/m²/year, indicating a deviation of approximately 48.73%.

Evidence<br>
The data originates from a model execution that successfully calculated the Energy EUI based on the provided parameters. The source IDs include `hw1-energy-m2-condition-source`, `hw1-energy-m2-condition-r-source`, and `hw1-energy-m2-office-profile-source`. The fixtures are fictional test data. The request did not perform a lifecycle change.

Limits<br>
The coverage status is unknown due to the absence of hourly coverage measurement, which may affect the reliability of the EUI calculation. The completeness of the data cannot be fully established from the available evidence.

### Execution and synthesis trace

```json
{
  "authenticated_test_user": "hw1-property",
  "conversation_id": "a8af5244-c2d0-4bd3-b171-9d26b773f317",
  "trace_id": "5aa47292-0358-4e4f-863c-9a2cc5c20fe8",
  "selected_route": "energy",
  "actual_tools": [
    {
      "tool": "energy_eui",
      "succeeded": true,
      "execution_origin": "model",
      "arguments_capture": "exact_passed_arguments",
      "arguments": {
        "building_id": "hw1-building-a1",
        "period_start": "2026-10-01",
        "period_end": "2026-10-31",
        "meter_id": "hw1-energy-m2-condition"
      }
    }
  ],
  "executed_call_inputs_from_response": [
    {
      "tool": "energy_eui",
      "arguments": {
        "building_id": "hw1-building-a1",
        "period_start": "2026-10-01",
        "period_end": "2026-10-31",
        "meter_id": "hw1-energy-m2-condition"
      }
    }
  ],
  "planning": {
    "successful_responses": 2,
    "selected_tools": [
      "energy_eui"
    ],
    "provider_failed": false
  },
  "answer_generation": {
    "status": "generated",
    "prompt_sha256": "60972a89ba5035aeef44c8e3cd98f870b4004bd2baee5fb6fca7a85a4ebb1a9e",
    "tools_bound": [],
    "evidence_sha256": "3e4d136af043c31c28c8439f7104c50b1d73f8b9bf74587927641072c4fa8427",
    "successful_responses": 1,
    "token_usage": {
      "input_tokens": 1507,
      "output_tokens": 397,
      "total_tokens": 1904,
      "input_token_details": {
        "audio": 0,
        "cache_read": 0
      },
      "output_token_details": {
        "audio": 0,
        "reasoning": 0
      }
    }
  },
  "unified_policy_version": "hw1-unified-v1",
  "domain_policy_version": "hw1-energy-v1"
}
```

## Case 2

Exact question:

> Show the work-order status summaries for my assigned building hw1-building-a1. Show two records and tell me whether more matching records remain.

### Original answer

```text
Showing 2 of 25 matching records in scope hw1-building-a1. Partial result; this is not a whole-scope summary. {"tool": "work_order_status", "scope": ["hw1-building-a1"], "request_id": "b91983d5-0512-4660-8ab0-24bb0e83c14e", "policy_version": "hw1-point2-v1", "records": [{"record_id": "hw1-wo-a1-00", "building_id": "hw1-building-a1", "work_order_id": "fixture-wo-0", "status": "open", "asset_id": "fixture-asset-a1", "due_at": "2026-11-01", "source_reference": "fixture:hw1-wo-a1-00"}, {"record_id": "hw1-wo-a1-01", "building_id": "hw1-building-a1", "work_order_id": "fixture-wo-1", "status": "open", "asset_id": "fixture-asset-a1", "due_at": "2026-11-01", "source_reference": "fixture:hw1-wo-a1-01"}], "total_matching": 25, "returned": 2, "offset": 0, "complete": false, "next_offset": 2, "data_source": "fictional_homework_permission_fixtures"}
```

### New answer — unedited model output

Answer<br>
The work-order status summaries for the assigned building **hw1-building-a1** are as follows:

| Record ID       | Work Order ID   | Status | Asset ID         | Due Date   |
|------------------|-----------------|--------|-------------------|------------|
| hw1-wo-a1-00     | fixture-wo-0    | open   | fixture-asset-a1  | 2026-11-01 |
| hw1-wo-a1-01     | fixture-wo-1    | open   | fixture-asset-a1  | 2026-11-01 |

A total of **25** matching records exist, and **more records remain** beyond the two returned.

Evidence<br>
The data originates from a fictional fixture as indicated by the source context. The records were retrieved from an external system report, and the request did not perform a lifecycle change. The records are managed by an external system. The source reference for the records includes "fixture:hw1-wo-a1-00" and "fixture:hw1-wo-a1-01".

Limits<br>
The total number of matching records is **25**, but only **2** records were returned in this request. The next offset for additional records is **2**, indicating that more records are available. Coverage cannot be established from the available evidence regarding the completeness of the remaining records.

### Execution and synthesis trace

```json
{
  "authenticated_test_user": "hw1-general",
  "conversation_id": "e47021fb-8969-4cea-b869-3231882a06f3",
  "trace_id": "2aae2dd5-f283-4fd7-a708-6e1638b7367a",
  "selected_route": "reads",
  "actual_tools": [
    {
      "tool": "work_order_status",
      "succeeded": true
    }
  ],
  "executed_call_inputs_from_response": [
    {
      "tool": "work_order_status",
      "arguments": {
        "building_ids": [
          "hw1-building-a1"
        ],
        "record_id": null,
        "limit": 2,
        "offset": 0
      }
    }
  ],
  "planning": {
    "successful_responses": 1,
    "selected_tools": [
      "work_order_status"
    ]
  },
  "answer_generation": {
    "status": "generated",
    "prompt_sha256": "60972a89ba5035aeef44c8e3cd98f870b4004bd2baee5fb6fca7a85a4ebb1a9e",
    "tools_bound": [],
    "evidence_sha256": "0ad20b846481697b9b936199117ef34777b7abf274b74b60c8c186ae4862216f",
    "successful_responses": 1,
    "token_usage": {
      "input_tokens": 1047,
      "output_tokens": 283,
      "total_tokens": 1330,
      "input_token_details": {
        "audio": 0,
        "cache_read": 0
      },
      "output_token_details": {
        "audio": 0,
        "reasoning": 0
      }
    }
  },
  "unified_policy_version": "hw1-unified-v1",
  "domain_policy_version": "hw1-point2-v1"
}
```

## Review limits and preserved history

All earlier session rows were preserved. An initial model answer with misleading unknown-coverage reassurance was retained, and a review correction was appended rather than changing its prior judgment. Final current-case results were appended as new records. There are9 JSONL rows including versions/review history; these represent2 reviewed manual cases, not9 completed Homework cases.

The final answers remain visible for review. Their arithmetic, scope, pagination and tool attribution match the recorded results. Some prose is still awkward: the model describes evidence as originating from model execution, while the underlying inputs are persisted fictional fixtures; the model retrieves/explains them rather than inventing measurements. Case2 adds a cautious sentence about the remaining records despite a verified matching count. These are retained verbatim for evaluation, not silently edited.

Representative checks do not prove universal semantic correctness. A separate historical same-evidence prompt replay is retained; the final deployment also includes the Python fix removing the misleading empty-limitations default.
