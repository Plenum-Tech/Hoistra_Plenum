# Trace Lab: writing runs, expected answers and labels from a coding agent

Trace Lab keeps its own records in the `hoist_tracelab` database, schema `tracelab` (migration 0011). A coding agent may
write there directly with plain `INSERT`s. Every table below is append-only: a change is a new row, and the newest row
wins. Nothing may be `UPDATE`d or `DELETE`d (the database refuses it).

## Codes

- A run's code is `R` followed by its number: `R1`, `R2` ...
- A question's code is its run's code, a hyphen and its number in the run: `R1-1`, `R1-2` ...
- `tracelab.question_id('R3-12')` returns the question's id, or null when there is no such question.

Live chat traffic gets its runs from Trace Lab itself ("Sync now"): one run per person per company per day. A coding
agent writes synthetic runs.

## 1. A synthetic run and its questions

```sql
-- The run: run_no is given by the database (the R number). Keep the returned id and run_no.
INSERT INTO tracelab.lab_runs (kind, title, source, created_by_email)
VALUES ('synthetic', 'support batch 22 Oct', 'coding_agent', 'agent@plenum-tech.com')
RETURNING id, run_no;

-- Its questions, numbered from 1 (seq_no). role, intent, followups and scenario_ref are optional.
INSERT INTO tracelab.lab_questions (run_id, seq_no, question, followups, role, intent, scenario_ref, source)
VALUES ('<run id>', 1, 'What is the current status of my order 3722? Include the recorded dates.',
        '["Please double-check the underlying record before giving me the final answer."]'::jsonb,
        'shopper', 'order status', 'support-0011', 'coding_agent');
```

Set `organization_id` on the run and its questions when the batch belongs to one company. Leave it null for a
platform-wide batch, which only the platform team and the consultant see.

## 2. Expected answers

The newest row per question is the one the page shows. Older rows stay as history.

```sql
INSERT INTO tracelab.lab_expected_answers (question_id, expected, expected_id, evidence, source, author_email)
VALUES (tracelab.question_id('R3-12'),
        'Report the order status as placed, ordered 2026-07-01, shipped None, delivered None, total $584.50.',
        'report_order_3722_placed',
        '{"reference": "SELECT order fields FROM orders WHERE id=3722", "type": "sql"}'::jsonb,
        'coding_agent', 'agent@plenum-tech.com');
```

## 3. Link a recorded answer to a question

After a synthetic question has been asked in Hoistra, link the recorded answer: its `turn_id` from
`plenum_cafm.agent_trace_turns`.

```sql
INSERT INTO tracelab.lab_question_traces (question_id, turn_id, organization_id, source, created_by_email)
VALUES (tracelab.question_id('R3-12'), 'turn-f01a3fc9f3c242e2', '<company id>', 'coding_agent', 'agent@plenum-tech.com');
```

## 4. Failure modes (the labels view's columns)

```sql
-- A new mode: lineage_id stays null, so the row starts its own lineage.
INSERT INTO tracelab.lab_failure_modes (name, definition, created_by_email)
VALUES ('missing_decision_rationale',
        'Present when the response states an outcome without the order fact and policy rule that caused it.',
        'agent@plenum-tech.com');

-- Change one: same lineage_id, new name or definition. Retire one: tombstone = true.
INSERT INTO tracelab.lab_failure_modes (lineage_id, tombstone, created_by_email)
VALUES ('<lineage id>', true, 'agent@plenum-tech.com');
```

A mode's lineage id is `coalesce(lineage_id, id)` of its first row.

## 5. Labels (positive or negative per question and mode)

One row per question, mode and author. The newest row per author is current, and a null `value` clears it. Write
the agent's own view under its own email. It shows beside the people's views and never replaces them.

```sql
INSERT INTO tracelab.lab_label_cells (question_id, mode_lineage_id, value, author_email, author_role, source)
VALUES (tracelab.question_id('R3-12'), '<mode lineage id>', 'positive', 'agent@plenum-tech.com', 'coding agent',
        'coding_agent');
```

## 6. Reading the notes (to build the taxonomy)

The free-text notes people write on steps live in `tracelab.step_labels`. The current ones are
`superseded_by IS NULL AND NOT tombstone`. To map each note to its question code:

```sql
SELECT 'R' || r.run_no || '-' || q.seq_no AS question_code, s.step_key, s.step_title, s.quote, s.note,
       s.reviewer_email, s.reviewer_role, s.created_at AS annotated_at
  FROM tracelab.step_labels s
  JOIN tracelab.lab_question_traces t ON t.turn_id = s.turn_id
  JOIN tracelab.lab_questions q ON q.id = t.question_id
  JOIN tracelab.lab_runs r ON r.id = q.run_id
 WHERE s.superseded_by IS NULL AND NOT s.tombstone
 ORDER BY r.run_no, q.seq_no, s.created_at;
```

`step_key` names the block the note is on:

| `step_key` | Block |
|---|---|
| a step's id | the step's input block |
| `<step id>#output` | the step's output block |
| `<turn id>:scenario` | the question as a whole |
| `<turn id>:orchestrator` | the orchestrator step, when routing ran as code |

## Excel or CSV import (the page)

The page's **Import** reads the first sheet that has a header row. Header names are matched loosely:

| Column | Header names accepted |
|---|---|
| question | `question`, `opening`, `opening message`, `input`, `prompt` |
| follow-ups | `follow-ups` (one per line, or a JSON list) |
| role | `role`, `persona` |
| intent | `intent` |
| expected answer | `expected`, `expected answer`, `expected outcome`, `expected output`, `outcome` |
| expected id | `expected id`, `outcome id` |
| evidence | `evidence`, `source`, `reference` (JSON, or plain text kept as `{"reference": "..."}`) |
| scenario id | `scenario`, `scenario id`, `id` |
| question code | `code`, `question code` |
| answer to link | `turn id`, `trace`, `trace id` |

A row with a question code adds an expected answer (and links an answer) to that existing question. A row with a
question becomes a new question, either of the run picked on the page or of a new synthetic run. Up to 2,000 rows
per file.
