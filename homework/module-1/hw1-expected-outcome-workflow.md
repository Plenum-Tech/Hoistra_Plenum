# Homework 1 expected-outcome workflow

This procedure makes `specifications.md` the operational source of truth for
Homework 1 evaluation without placing the entire specification in every model
request.

## Authority order

1. `specifications.md` contains accepted product behavior and stable requirement
   IDs.
2. Trusted fixtures, database records, tool results and immutable evidence supply
   the case-specific facts.
3. Prompts and `SKILL.md` files implement relevant requirements at runtime. They
   are not independent policy sources.
4. Observed model output is evidence to evaluate, never the source of the
   expected outcome.

When sources conflict, stop and record the conflict. Do not select the most
convenient source or invent a rule.

## Authoring an expected outcome

1. Read `SPEC.md` and the relevant sections of the full canonical specification.
2. Select one primary requirement and every related requirement needed to judge
   permission, scope, workflow, calculation, evidence and response behavior.
3. Confirm case-specific values against trusted evidence. Keep policy statements
   separate from fixture facts.
4. Write the expected outcome before reviewing the model answer.
5. Add or update the corresponding row in `hw1-case-spec-mapping.json`. Preserve
   historical evidence; use explicit supersession fields instead of rewriting an
   observed record.
6. If the canonical document does not decide the behavior, mark the case as a
   specification gap and obtain an owner-approved specification change first.
7. Run the validator and relevant tests before committing.

## Runtime changes

Do not load the full specification into each model request. Translate relevant
model-decision rules into concise reviewed prompts or skills. Enforce identity,
authorization, arithmetic, approval and state transitions in deterministic code.
Every accepted policy change follows this order:

1. update `specifications.md` and its version or decision history;
2. update the affected prompt, skill or code;
3. add regression coverage and evidence;
4. update the case mapping and readiness register; and
5. run validation.

## Validation

From the repository root:

```bash
python3 homework/module-1/validate_hw1_spec_evidence.py
```

The validator fails when the canonical hash drifts, an expected outcome lacks a
case mapping, a cited requirement is unknown, source requirements are omitted,
the JSONL is invalid, or supersession and trace references are inconsistent.
