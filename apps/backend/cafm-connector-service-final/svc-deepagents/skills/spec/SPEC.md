# Hoistra answer specification

Draft

Written from the failure modes people confirmed in Hoist Trace Lab. Expected answers are derived from these rules and the company's records, never from an answer the model gave.

## Rules

### S-1 · overdue_reported_as_none

When a certificate or inspection is past its due date, the answer states the due date and how many days overdue, and names any work order already raised for it. Never "nothing is due".

### S-2 · for_each_missing_gaps

A statutory inspection is covered only by a work order for that inspection. A repair or other work order on the same asset never counts as the inspection booked.

### S-3 · wrong_work_order_counted

If any engine in the instance holds the answer (e.g. contract renewals in contract performance), answer it from that engine; never say the data is unavailable.

### S-4 · list_answer_as_paragraph

An answer must not show this failure: A which/list question answered as one long paragraph instead of a short list (item, reason, since when), and slow to arrive. From note on R33-12.

### S-5 · blocked_and_at_risk_conflated

An answer must not show this failure: Mixes vendors explicitly blocked (block flag, as on the Vendors page) with vendors holding a lapsed accreditation, giving two different blocked counts or pushing the user to check the list themselves. From note on R33-12.

### S-6 · draft_treated_as_block

An answer must not show this failure: Counts an unconfirmed draft certificate or authorisation awaiting sign-off as a lapse or a block, instead of as a task awaiting confirmation. From note on R33-12.

### S-7 · grouping_read_as_filter

When the question asks to list or group by something ("by building", "per vendor"), group the answer by it — never use it as a filter. List every record the question covers; put records with no building under "No building on record".

### S-8 · next_due_date_ignored

A certificate is due in a window when its expiry date — or, when it has no expiry date, its next-due date — falls in that window.

### S-9 · redundant_filter_matches_all

"Lapsed or expiring within N days" is one condition: days to expiry ≤ N (a negative number means lapsed). Never join an expiry window with OR, and never add a "not yet expired" lower bound when lapsed certificates are asked for.

### S-10 · stale_status_field_trusted

"Due for renewal" is the platform's renewal band: 31–60 days to expiry. Filter on days to expiry (gte 31, lte 60), never on the stored status — the scan that sets it may not have run.

### S-11 · incorrect_date_range_filtering

An answer must not show this failure: The answer fails to restrict results to the specific date range (e.g., exact month and year) requested, instead matching on a broader or incorrect range such as any year.

Failure mode: incorrect_date_range_filtering (`dc8ff89e-36e2-4195-b224-7c1c5258a741`)
