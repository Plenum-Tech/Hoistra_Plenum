---
name: compliance-recipes
agent: compliance
title: Worked compliance requests using authorized evidence
description: Examples for selecting enabled scoped operations, preserving exact filters and reporting evidence honestly. Recipes do not grant capabilities or enable specialist execution.
---

# Compliance request recipes

Target policy: specification v0.37.0.

Follow the core/shared policies and reviewed Compliance instructions
when explicitly loaded. Use only tools offered for the request.

The current homework profile permits one guarded read-tool call.
Compliance specialist execution, advanced filtered adapters and
business actions remain unavailable until separately verified.

Examples below describe required behavior. They are not declarations
that every operation is implemented.

## 1. "Are we compliant?"

Establish the authorized building/portfolio and what the user means:
certificate dates, applicable requirements, missing evidence or
another specific compliance concern.

Use an enabled operation that supports that scope and meaning.
Do not retrieve every certificate by default or call a dashboard
merely because a legacy recipe requires it.

Distinguish in-date certificates, unresolved verification findings,
missing evidence and country-pack completeness.

Do not turn limited certificate records into a blanket statement
that the building is compliant or safe.

## 2. "Show this company's certificates"

Resolve the company through an enabled authorized adapter.
Use returned identity and relationship evidence.

Distinguish:
- Accreditations the company holds.
- Building certificates the company issued.

Retrieve the requested relationship, or both where needed and supported.
Do not automatically make two calls in the current one-call profile.

If several authorized candidates match, clarify.
If no match is established, do not retrieve the entire register,
choose a company by guesswork or expose unrestricted candidate names.

Report which relationship and population were actually queried.
Missing held accreditations do not prove the vendor is clear or blocked.

## 3. "Certificates for this building or trade"

Resolve the building within trusted scope. Use supported backend
filters for the requested building, trade and other conditions.

Do not assume that spelling variants or trade synonyms are supported.
Use verified adapter resolution or clarify the intended entity.

A name-only building reference does not establish permission.
Do not broaden an unresolved building into a portfolio-level query.

Report the applied filters, returned fields and completeness.
Never list unrelated rows to fill an otherwise empty section.

## 4. "How many certificates have an insurance risk flag?"

Use an enabled backend count with the requested risk flag and scope.

Distinguish an insurance flag, authenticity score, expiry classification
and approved vendor block. They are not interchangeable filters.

Use the returned exact count only for its stated population.
Do not sum overlapping calls or count one page as the entire dataset.

If that filtered count is unavailable, explain the limitation.
An unfiltered certificate total does not answer this question.

## 5. "Which vendors hold more than two certificates?"

Use an enabled grouped aggregation over authorized held accreditations.

"More than two" means a count greater than two.
Use only comparison arguments actually declared by the adapter.

Do not infer unique-vendor totals or complete groups from a partial
certificate list. Identify the reporting scope and completeness.

## 6. "Which fire certificates are lapsed at Tower A?"

Require the authorized resolved building, requested fire category
and expired/lapsed condition together in the backend operation.

Expired/lapsed means past the verified expiry/review-date anchor.
Day zero is "expires today", not expired/lapsed.

Return only matching authorized rows and the matching count.
Disclose pagination; total matches may exceed displayed rows.

Do not fetch an arbitrary page and filter it mentally into a supposedly
complete answer. If the exact operation is unavailable, say so.

## 7. "Which certificates expire within 30 days?"

For an upcoming inclusive window, use days 0–30.
Keep already-expired records separate unless requested.

Label 8–30 action-required, 1–7 critical and day zero expires today.
Missing dates remain unknown unless a documented review-date rule applies.

State the assessment date and date basis when returned.
Do not treat expiry detection as approval to renew or block a vendor.

## 8. "Is this a building certificate or vendor accreditation?"

Use an enabled versioned taxonomy/applicability source.

Do not infer taxonomy from the absence of documents on file.
State the returned scope and relevant applicability qualifications.

Use returned totals rather than fixed country-pack counts.
Homework fixture quantities are not statutory requirements.

## 9. "Prove it — show the source"

Use the authorized certificate-document or supporting-evidence read.

Return the source/version and page or section references when available.
General Users retain their permitted summaries and references;
these do not grant full-document access.

Full documents require authorization for all disclosed content.
A verified excerpt does not authorize the original download.

Generating an evidence pack, vendor passport or sharing link requires
a separately enabled scoped workflow. Do not share evidence merely
because the user asked to inspect it.

## 10. "Show forged or suspicious documents"

Identify the requested subject, authorized scope and assessment meaning.

Use enabled adapters supporting existing assessment-result filters.
Do not rerun forensics merely to list recorded findings.

Distinguish:
- Failed checks or detected editing.
- Suspect/review findings.
- Passed checks and their limited assessment scope.
- Missing or unavailable assessments.

Use the approved vocabulary. Do not turn an automated signal into
a broader verified forgery conclusion.

Show returned reasons, score/scale and provenance where available.
Keep building and vendor results distinct when both were requested.

If the profile cannot retrieve both parts or apply the requested
filters, explain the uncompleted portion instead of claiming success.

## 11. "Which vendors have suspicious documents?"

Use a verified grouping over authorized assessment results and
vendor relationships.

Distinguish held accreditations from documents issued for buildings.
Do not combine them silently or infer vendor block state.

State the assessment categories included and their meaning.
Rank only where returned comparable evidence supports the order.
Missing assessments are not proof that documents are genuine.

## 12. "Renew this certificate" or "Unblock this vendor"

Follow the approved renewal and approval rules only when their
guarded workflows are enabled.

Show evidence, eligible actor, proposed effects and confirmation.
Renewal never automatically unblocks a vendor.

Review all blocking reasons and preserve original documents.
A simulated request is not renewal, booking, unblock completion
or external work-order creation.

In the current read-only profile, explain that the business workflow
is not enabled. Do not simulate an approval or invoke a legacy path.

## 13. Report actual execution

Answer from matching tool results and authorized source evidence.
State unavailable fields, partial results and failures honestly.

A recipe mentioning a tool or specialist is not proof it executed.
Keep user intent, selected operation and actual calls separate.
If no tool executed, the executed-tool list is empty.

Do not claim a separate analyst/reviewer ran in the current homework
profile, which formats verified read results in Python.
