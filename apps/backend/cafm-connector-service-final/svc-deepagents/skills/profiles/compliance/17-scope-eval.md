---
name: compliance-scope-eval
agent: compliance
title: Evaluating question coverage and disclosure scope
description: Checks whether an answer matches the requested authorized population and evidence; does not enable a machine review or impose frontend layout.
---

# Compliance scope evaluation

Target policy: specification v0.37.0.

This is guidance for an explicitly enabled evaluation/review path.
The current homework read profile does not run a separate Compliance
reviewer. Follow core/shared policies and reviewed Compliance vocabulary.

## 1. Evidence boundary

Judge the question, candidate answer, authorized source facts and actual
execution metadata together. Establish scope, filters, period, denominator,
source version and completeness before judging a count or comparison.
Do not assume backend facts cover an entire register or supersede permissions.

Flag restricted content in prose, IDs, counts, comparisons, links or
attachments. Officer compliance access does not permit other domains;
General User summaries do not permit full documents or HOI.

## 2. Did it answer each requested part?

Require the supported answer early and account for every part. A forbidden,
unavailable or failed part may be explained safely instead of answered.
Do not demand additional retrieval through unenabled tools or a bypass.

For taxonomy-only questions, avoid unsolicited held/missing status. For a
gap question, supported missing types per authorized owner are part of the
answer, not irrelevant detail. Do not invent missing types from memory.
A count question does not require a list of every source record.

## 3. Does population and arithmetic match?

Check the exact compound filter, subject, units and period. A broader total
is not a valid substitute for a filtered count. A partial page cannot prove
a whole-population count, ranking or absence of a condition.

Total matches may exceed listed rows; do not flag correct pagination as
an arithmetic error or force evidence-ID lists to match a complete total.
Overlapping categories cannot be summed as disjoint ones. Held+missing
requires a backend-defined disjoint/exhaustive denominator.

For minimum/maximum questions, all ties on the requested measure must be
acknowledged. A named secondary measure may distinguish priority without
pretending the primary tie disappeared. If only partial evidence exists,
require an explicit limited comparison rather than an estate-wide winner.

## 4. Are meanings and conclusions supported?

Use the approved expiry labels including explicit day zero and unknown.
Separate expiry, risk, draft, assessment verdict and approved block state.
An in-date row does not prove a building compliant; an automated failed
check does not prove a broader fraud allegation.

Coverage must state its denominator. Pack completeness is not compliance,
safety or HOI. Fixed fixture counts are not statutory duties.

For risk/priority questions, check source-backed consequence-first reasoning
and disclosed applicability, authenticity findings, duration and context.
Do not accept a fabricated numerical score or hardcoded example as evidence.

## 5. Are action and execution claims true?

Distinguish proposal, human approval, execution and follow-through.
Renewal is not an approved unblock; a simulated email is not real delivery
or external WO creation. Eligible self-approval does not require a second
human review. No count/query may silently mutate state.

Intent, selected instructions and actual executed calls must be separate.
A referenced analyst/reviewer/specialist is not evidence it ran.
Evaluate answer correctness and trace-label accuracy separately.

## 6. Return useful review findings

Use the enabled path's declared schema. Identify each material defect,
its evidence, smallest correction and correct content to preserve.
Do not invent output fields or require UI groups, panel IDs or fixed
numbers of actions for backend-only Homework acceptance.

Preserve scope/evidence while correcting errors; do not expand disclosure,
duplicate entries or remove requested gap details to satisfy a style rule.
Mark unassessable evidence as such under the path's contract, not a fabricated
PASS. Preserve historical traces and human decisions without relabelling.
