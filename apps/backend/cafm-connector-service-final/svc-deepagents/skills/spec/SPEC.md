# Hoistra answer specification

Draft

Written from the failure modes people confirmed in Hoist Trace Lab. Expected answers are derived from these rules and the company's records, never from an answer the model gave.

## Rules

### S-1 · overdue_reported_as_none

When a certificate or inspection is past its due date, the answer states the due date and how many days overdue, and names any work order already raised for it. Never "nothing is due".

Failure mode: overdue_reported_as_none (`d6ea971a-7b48-4e9d-98b9-c6945f6b4e4d`)

### S-2 · for_each_missing_gaps

A statutory inspection is covered only by a work order for that inspection. A repair or other work order on the same asset never counts as the inspection booked.

Failure mode: for_each_missing_gaps (`2fceab1c-c1b4-4a95-9750-6f6f30f828b4`)

### S-3 · wrong_work_order_counted

If any engine in the instance holds the answer (e.g. contract renewals in contract performance), answer it from that engine; never say the data is unavailable.

Failure mode: wrong_work_order_counted (`a72e863c-470b-4cde-83f0-f36b98d82253`)

### S-4 · list_answer_as_paragraph

An answer must not show this failure: A which/list question answered as one long paragraph instead of a short list (item, reason, since when), and slow to arrive. From note on R33-12.

Failure mode: list_answer_as_paragraph (`0ee4dcdd-4ad5-4c3b-83d5-fa0001b57bfa`)

### S-5 · blocked_and_at_risk_conflated

An answer must not show this failure: Mixes vendors explicitly blocked (block flag, as on the Vendors page) with vendors holding a lapsed accreditation, giving two different blocked counts or pushing the user to check the list themselves. From note on R33-12.

Failure mode: blocked_and_at_risk_conflated (`a029200d-f04f-4e5a-9cc0-25f0bee96928`)

### S-6 · draft_treated_as_block

An answer must not show this failure: Counts an unconfirmed draft certificate or authorisation awaiting sign-off as a lapse or a block, instead of as a task awaiting confirmation. From note on R33-12.

Failure mode: draft_treated_as_block (`f9587bfc-49ae-430f-8382-20ca5f7e748f`)

### S-7 · grouping_read_as_filter

When the question asks to list or group by something ("by building", "per vendor"), group the answer by it — never use it as a filter. List every record the question covers; put records with no building under "No building on record".

Failure mode: grouping_read_as_filter (`2c591f86-5dd8-4bb1-af4b-096ad3a27590`)

### S-8 · next_due_date_ignored

A certificate is due in a window when its expiry date — or, when it has no expiry date, its next-due date — falls in that window.

Failure mode: next_due_date_ignored (`d3d2c6b6-51a8-4026-8667-ea2a35b0b8e5`)

### S-9 · redundant_filter_matches_all

"Lapsed or expiring within N days" is one condition: days to expiry ≤ N (a negative number means lapsed). Never join an expiry window with OR, and never add a "not yet expired" lower bound when lapsed certificates are asked for.

Failure mode: redundant_filter_matches_all (`2818d755-5f36-44c5-b61a-b3d273d6bf41`)
