# Hoistra orchestrator specification

The canonical specification is [specifications.md](specifications.md).

This pointer supports the Cartwheel homework convention of looking for `SPEC.md` without maintaining a second copy. Read the canonical document and [homework/architecture.md](homework/architecture.md) before changing prompts, tools or evaluation expectations. Neither Markdown file is loaded as the running system prompt.

## Expected-outcome control

The canonical specification governs evaluation correctness; individual prompts
and `SKILL.md` files describe runtime implementation and do not override it.
Before an expected outcome is written or approved, follow
[the Homework 1 expected-outcome workflow](homework/module-1/hw1-expected-outcome-workflow.md):

1. identify the applicable canonical requirement IDs;
2. ground record-specific values in fixtures, tool output or immutable evidence;
3. record the case, expectation and IDs in
   `homework/module-1/hw1-case-spec-mapping.json`; and
4. run `python3 homework/module-1/validate_hw1_spec_evidence.py`.

If no accepted requirement decides the behavior, record a specification gap and
obtain an owner decision before freezing the expected outcome. Update accepted
policy in `specifications.md` first, then align prompts, skills and deterministic
code. Committing this pointer alone does not load the specification into the
model or change runtime behavior.
