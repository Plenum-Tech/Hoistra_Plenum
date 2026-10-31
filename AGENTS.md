# Hoistra repository instructions

## Canonical requirements and expected outcomes

`SPEC.md` is the discovery entry point. `specifications.md` is the canonical
behavior specification. Before drafting, changing or approving an expected
outcome, read both files and follow
`homework/module-1/hw1-expected-outcome-workflow.md`.

- Identify the applicable canonical requirement IDs before writing the outcome.
- Ground fixture-specific facts in database records, tool output or immutable
  evidence. Do not infer implementation availability from an approved policy.
- Treat prompts and `SKILL.md` files as runtime implementation guidance. They
  cannot override the canonical specification or silently create new policy.
- If the specification does not decide the behavior, record a specification gap
  and obtain an owner decision before freezing the expected outcome.
- Record every Homework 1 evidence row in
  `homework/module-1/hw1-case-spec-mapping.json`.
- Run `python3 homework/module-1/validate_hw1_spec_evidence.py` before committing
  specification, expected-outcome, prompt or Homework 1 evidence changes.

Change accepted product policy in `specifications.md` first. Then update the
runtime prompt, skill or deterministic code that implements it and refresh the
mapping and validation evidence in the same change.
