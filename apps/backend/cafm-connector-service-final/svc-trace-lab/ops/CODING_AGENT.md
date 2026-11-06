# Trace Lab: writing runs, expected answers and labels from a coding agent

Trace Lab keeps its own records in the `hoist_tracelab` database, schema `tracelab` (migrations 0011 and 0013). A coding
agent may write there directly with plain `INSERT`s. Every table below is append-only: a change is a new row, and the
newest row wins. Nothing may be `UPDATE`d or `DELETE`d (the database refuses it).

**People decide; the agent proposes (3 Nov 2026, migration 0013).** A failure mode, a label, an expected answer or a
specification version that the agent writes is a PROPOSAL. It shows on the page's Loop tab and counts only after a person
approves it there (a row in `tracelab.lab_reviews`, one final decision per proposal). The agent never writes
`lab_reviews`, never writes notes (`step_labels`), and never sets `created_by` (that marks a row a person wrote).

The loop: traces, then people's notes, then failure modes, then the specification (SPEC.md), then the prompt, skill or
code correction, then a regression run, then a rerun.

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

The newest row per question that COUNTS is the one the page shows: one a person wrote or imported, or the agent's once a
person approved it. Older rows stay as history.

An expected answer is DERIVED, never copied: from a rule of the approved specification (section 7) and the company's
own records. Its `evidence` must name both, or it cannot be approved: `spec_rule` (for example `"S-3"`) and `data` (the
query or record ids it was read from); `values` (optional) lists the figures, dates or codes an answer must carry, which
is what a rerun is checked against. Trace Lab refuses an expected answer that repeats an answer the model gave for the
question.

```sql
INSERT INTO tracelab.lab_expected_answers (question_id, expected, expected_id, evidence, source, author_email)
VALUES (tracelab.question_id('R3-12'),
        'Report the order status as placed, ordered 2026-07-01, shipped None, delivered None, total $584.50.',
        'report_order_3722_placed',
        '{"spec_rule": "S-3", "data": "SELECT status, ordered_at, total FROM orders WHERE id = 3722",
          "values": ["2026-07-01", "$584.50"]}'::jsonb,
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

A mode's lineage id is `coalesce(lineage_id, id)` of its first row. A mode, a change or a retirement the agent writes is
a proposal: the mode becomes a column of the labels view only once a person approves it, and a change leaves the
approved version in force until then. Group people's notes (section 6) into modes; never invent one no note supports.

## 5. Labels (positive or negative per question and mode)

One row per question, mode and author. The newest row per author is current, and a null `value` clears it. Write
the agent's own view under its own email. It shows beside the people's views as a SUGGESTION, never counts in progress
or regression runs, and never replaces a person's value: a person accepts it by setting the same value themselves.

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

## 7. The specification (SPEC.md)

The specification in force is the newest version a person approved on the Loop tab; it downloads as `SPEC.md`
(`GET /api/trace-lab/lab/spec.md`). Its rules are numbered `S-1`, `S-2` ... and each names the failure mode it guards
against. The page drafts the next version from the confirmed modes; the agent may also propose one:

```sql
INSERT INTO tracelab.lab_spec_versions (intro, rules, body, source, created_by_email)
VALUES ('Answers about buildings and their work orders.',
        '[{"code": "S-1", "mode_lineage_id": "<lineage id>", "title": "wrong_floor_count",
           "text": "A floor count comes from the buildings register, never from a document or a guess."}]'::jsonb,
        '<the SPEC.md text>', 'coding_agent', 'agent@plenum-tech.com');
```

Keep rule codes stable from version to version. After approval, commit `SPEC.md` to the repository unchanged.

## 8. Corrections

Each prompt, skill, code or data change made for a failure mode or a rule is recorded, so the loop shows what fixed it:

```sql
INSERT INTO tracelab.lab_corrections (mode_lineage_id, spec_rule, kind, reference, description, source, created_by_email)
VALUES ('<lineage id>', 'S-1', 'skill', 'svc-deepagents/skills/buildings.md @ 5c653a4',
        'Floors are read from the buildings register.', 'coding_agent', 'agent@plenum-tech.com');
```

`kind` is one of `prompt`, `skill`, `code`, `data`, `spec`; `reference` is a commit, pull request or file.

## 9. Regression runs and the rerun

A person makes a regression run of a confirmed mode on the Loop tab once its rule is in the approved specification: the
questions people marked positive for it, each with its derived expected answer and the answer that was marked. "Ask
again" asks them in the chosen instance after the correction, and "Compare" checks the old and new answers against the
expected values (fixed, still failing, regressed, passing).

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
