# Feature Specification: Contract Performance Engine

**Feature Branch**: `001-contract-performance-engine`

**Created**: 2026-08-25

**Status**: Complete

**Input**: User description: "FEATURE B — CONTRACT PERFORMANCE ENGINE. Scoring input in v1.2: work order records ingested from FM company reports (CSV / XLS / PDF) through the Phase 1 UDR pipeline into the WorkOrder entity. The engine reads and scores this ingested data. Attendance timestamps, completion dates, and costs are as reported by the FM company — the platform's value is systematic scoring and cross-referencing of data the PM already receives but currently reviews manually. B1 — Contract Ingestion and Parameter Extraction. B2 — Vendor Performance Scoring. B3 — Invoice Verification."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Extract contract parameters the PM can correct (Priority: P1)

A property manager uploads an FM contract, framework agreement, purchase order, or invoice
through the Single Door Orchestrator. The system reads the document and presents the commercial
terms it found — SLA response and completion targets per priority band, PPM obligations, labour
and call-out rates, parts pricing, payment terms, penalty and bonus clauses — in a table the PM
can edit line by line before confirming. Anything the contract does not state is filled with a
platform default and labelled as such, so the PM can see at a glance which numbers came from
their contract and which did not.

**Why this priority**: Nothing else in this feature can run without extracted parameters. A
vendor score is meaningless without the SLA target it is measured against, and an invoice cannot
be checked without a contracted rate to check it against. This story also delivers value alone:
a structured, corrected record of contract terms is useful even if no scoring ever runs.

**Independent Test**: Upload a contract, confirm the extracted parameter table, and read the
stored Contract record back. Fully testable without any work order data present.

**Acceptance Scenarios**:

1. **Given** a PM uploads a contract stating a four-hour P1 response target, **When** extraction
   completes, **Then** the P1 response target reads four hours and is marked as contract-sourced.
2. **Given** a contract that is silent on overtime rate, **When** extraction completes, **Then**
   the overtime field carries the platform default and is labelled "Default — not
   contract-sourced".
3. **Given** the PM corrects an extracted labour day rate before confirming, **When** they
   confirm, **Then** the corrected value is stored, the original extracted value is retained, and
   the override is recorded with who made it and when.
4. **Given** extraction produced a value the PM has not yet reviewed, **When** they attempt to
   run scoring, **Then** the system blocks and names the unconfirmed parameters.
5. **Given** a contract that defines task criticality bands, **When** extraction completes,
   **Then** the contract's definitions are used; **Given** a contract that is silent, **Then** a
   standard band list is proposed for approval and remains editable.

---

### User Story 2 - Score a vendor against the contract the PM signed (Priority: P1)

Every ingested completed work order is scored against the SLA extracted for the vendor assigned
to it. On the first of each month a scorecard is produced for the preceding month and saved to
the Vendors Saved Space: an overall score, its trend against prior months, the component
breakdown, and a drill-down to the individual work orders behind every number.

**Why this priority**: This is the feature's reason to exist — turning reports the PM already
receives and reads manually into a systematic, comparable score. It is P1 alongside Story 1
because Story 1 has no purpose without it.

**Independent Test**: With a confirmed contract and a set of ingested work orders, run scoring
for one vendor and one month, then verify every component and the total against the underlying
records by hand.

**Acceptance Scenarios**:

1. **Given** a vendor with ingested completed work orders, **When** monthly scoring runs, **Then**
   an overall score is produced from SLA response met (25%), SLA completion met (25%), first fix
   rate (20%), recall rate (15%), and accreditation currency (15%).
2. **Given** a vendor whose accreditation is lapsed and whose block state is Blocked, **When**
   scoring runs, **Then** the overall score is capped at 60 however strong the SLA performance,
   and the cap is stated as the reason.
3. **Given** two work orders on the same asset raised eleven days apart for the same fault,
   **When** the recall rate is computed, **Then** the second counts as a recall.
4. **Given** a vendor with PPM obligations, **When** scoring runs, **Then** PPM compliance is
   reported separately as visits completed within ±7 days of schedule over visits scheduled, and
   is not folded into the overall score.
5. **Given** a vendor exceeding estimated cost by more than 15% on three or more jobs within one
   month, **When** scoring runs, **Then** a queue alert is raised naming those jobs.
6. **Given** a produced scorecard, **When** the PM opens any component, **Then** they reach the
   individual work order records that produced it.
7. **Given** an L1-criticality asset and an L3-criticality asset each with one SLA failure,
   **When** scoring runs, **Then** the L1 failure carries three times the weight of the L3.

---

### User Story 3 - Check an invoice against what was actually done (Priority: P2)

The PM uploads a vendor invoice. Each line is matched against ingested work order records:
whether the work order exists and is reported complete, whether labour hours are within ±10% of
reported attendance duration, whether parts cost is within ±5% of the contract parts framework,
and whether the labour rate exceeds the contracted rate. Matched lines clear automatically.
Flagged lines reach the PM with a plain-English explanation, the pound difference, and a choice
to approve, challenge, or reject.

**Why this priority**: It depends on both prior stories — it needs contract rates from Story 1
and the work order matching that Story 2 establishes. It is the highest-value story
commercially, but it cannot be built first.

**Independent Test**: Upload an invoice against a known set of work orders and contract rates,
and verify the split between auto-cleared and flagged lines, and each flag's stated dispute
amount, by hand.

**Acceptance Scenarios**:

1. **Given** an invoice line whose work order exists, is complete, and whose hours, parts and
   rate are within tolerance, **When** verification runs, **Then** the line is auto-cleared.
2. **Given** an invoice line billing a labour rate above the contracted rate, **When**
   verification runs, **Then** the line is flagged with the overcharge stated in pounds and in
   plain English.
3. **Given** an invoice line referencing a work order that does not exist in the ingested
   records, **When** verification runs, **Then** the line is flagged as unmatched and is never
   auto-cleared.
4. **Given** a flagged line with a delta above £500, **When** it is prepared for the PM queue,
   **Then** the arithmetic is independently recomputed by the Quality/Adversary gate before the
   PM sees it, and a disagreement blocks the flag from reaching the queue as stated.
5. **Given** the PM rejects a flagged line, **When** they confirm, **Then** the decision and its
   reason are recorded against both the invoice line and the vendor.
6. **Given** a completed verification, **When** the vendor is next scored, **Then** the
   matched-to-flagged ratio contributes as an input signal.

---

### User Story 4 - Tune the scoring model without a code change (Priority: P3)

An administrator at the client adjusts component weightings, tolerance bands, the accreditation
score cap, the recall window, and the PPM tolerance from an admin section. Changes take effect
for subsequent scoring runs; previously produced scorecards keep the parameters they were
computed under.

**Why this priority**: Valuable for adoption across clients with different contracts, but the
engine is usable with defaults, so it follows working scoring rather than preceding it.

**Independent Test**: Change a weighting in the admin section, re-run scoring for one vendor, and
confirm the new weighting applied while the prior scorecard is unchanged.

**Acceptance Scenarios**:

1. **Given** an administrator changes the first fix weighting from 20% to 25%, **When** the next
   scoring run executes, **Then** it uses 25% and the previous month's scorecard still shows 20%.
2. **Given** component weightings that do not total 100%, **When** the administrator saves,
   **Then** the save is rejected with the total shown.
3. **Given** a parameter change, **When** it is saved, **Then** who changed it, when, and the
   previous value are recorded.

---

### User Story 5 - See where the money and the time are going (Priority: P3)

The PM reads an insight layer over the scored data: cost variance for service and parts against
contract or budget, usage variance for labour hours and duration against the agreement, and
vendor performance patterns drawn from the ingested reports.

**Why this priority**: Analysis over data the earlier stories already produce. Genuinely useful,
but nothing depends on it.

**Independent Test**: With several months of scored data present, open the insight layer and
verify each variance against the underlying records.

**Acceptance Scenarios**:

1. **Given** several months of scored work orders, **When** the PM opens insights, **Then** cost
   variance is shown against contract or estimate with the contributing jobs reachable.
2. **Given** a vendor whose average attendance duration exceeds the agreement, **When** insights
   are produced, **Then** the usage variance is stated with the jobs behind it.
3. **Given** no data for a requested period, **When** insights are produced, **Then** the system
   states there is no data rather than showing a zero.

---

### Edge Cases

- A work order is ingested with no vendor assigned, or with a vendor name that matches no
  Contract record. It must not be silently dropped from or silently included in any vendor's
  score.
- A work order is reported complete before it is reported attended, or carries a completion
  timestamp earlier than its raise timestamp. The dates contradict each other and cannot be
  scored as they stand.
- A vendor has a confirmed contract but zero work orders in the scored month. A score of zero and
  "no activity" are different statements and must not be conflated.
- Two contracts cover the same vendor with overlapping dates, or a contract expires mid-month.
  Which SLA applies to a work order raised inside the overlap must be determined by rule, not by
  whichever record is found first.
- An invoice arrives for work orders spanning several months, or a work order is billed twice
  across two invoices.
- A vendor's accreditation lapses mid-month, so the score cap applies to part of the period only.
- An ingested report contains a work order that was already ingested from an earlier report with
  different values.
- A contract states an SLA in a unit the platform does not model — working days rather than
  hours, or a target measured from a time other than the raise time.
- An invoice line has no work order reference at all.
- Attendance duration is missing on a work order the invoice bills labour hours against.

## Requirements *(mandatory)*

### Functional Requirements

**Contract ingestion and parameters (B1)**

- **FR-001**: System MUST accept FM contracts, framework agreements, purchase orders and invoices
  uploaded through the Single Door Orchestrator.
- **FR-002**: System MUST build one-to-many mappings between contract document content and
  structured Contract fields.
- **FR-003**: System MUST extract SLA response and completion targets per priority band
  (P1/P2/P3/P4), PPM schedule obligations, labour day rate, overtime rate, call-out rate, parts
  pricing framework, payment terms, and KPI penalty and bonus clauses.
- **FR-004**: System MUST present every extracted parameter in an inline-editable table and allow
  the PM to override any field before confirmation.
- **FR-005**: System MUST record every override with the original extracted value, the new value,
  who changed it, and when.
- **FR-006**: System MUST apply a platform default where the contract is silent and label it
  "Default — not contract-sourced", visibly distinct from a contract-sourced value.
- **FR-007**: System MUST use the contract's task criticality definitions (L1/L2/L3) where
  present, and otherwise propose a standard editable list for PM approval.
- **FR-008**: System MUST classify asset criticality from UDR data using load dependence,
  function type, and sub-meter consumption where available.
- **FR-009**: System MUST require human approval of asset criticality, and MUST default
  unapproved assets to L2.
- **FR-010**: System MUST weight an SLA failure on an L1 asset three times that of an L3 asset.

**Vendor scoring (B2)**

- **FR-011**: System MUST score every ingested completed work order against the extracted SLA for
  the vendor assigned to it.
- **FR-012**: System MUST compute an overall score from SLA response met (25%), SLA completion met
  (25%), first fix rate (20%), recall rate (15%), and accreditation currency (15%).
- **FR-013**: System MUST treat a repeat fault on the same asset within 30 days as a recall.
- **FR-014**: System MUST cap the overall score at 60 for any vendor whose block state is Blocked
  through a lapsed accreditation, regardless of SLA performance, and MUST state the cap as the
  reason for the score.
- **FR-015**: System MUST report PPM compliance separately as visits completed within ±7 days of
  schedule divided by visits scheduled, expressed as a percentage.
- **FR-016**: System MUST compute cost variance of actual against estimated or contracted cost per
  work order.
- **FR-017**: System MUST raise a queue alert where a vendor exceeds 15% cost variance on three or
  more jobs within one month.
- **FR-018**: System MUST generate a monthly vendor scorecard on the first of the month for the
  preceding month and save it to the Vendors Saved Space.
- **FR-019**: Scorecards MUST carry the overall score, its trend, the component breakdown, and a
  drill-down to the underlying work order records.
- **FR-020**: System MUST make every score component traceable to the work orders that produced
  it.

**Invoice verification (B3)**

- **FR-021**: System MUST parse an uploaded invoice into line items and match each to ingested
  work order records.
- **FR-022**: System MUST verify per line that the work order exists and is reported complete,
  labour hours are within ±10% of reported attendance duration, parts cost is within ±5% of the
  contract parts framework, and the labour rate does not exceed the contracted rate.
- **FR-023**: System MUST auto-clear lines passing every check and flag those that do not.
- **FR-024**: Each flagged line MUST state the discrepancy in plain English and the difference in
  pounds.
- **FR-025**: Users MUST be able to approve, challenge, or reject each flagged line, and the
  decision MUST be recorded.
- **FR-026**: System MUST route any flagged line with a delta above £500 through the
  Quality/Adversary gate, which independently recomputes the arithmetic before the line reaches
  the PM queue.
- **FR-027**: System MUST record the matched-to-flagged ratio per vendor and feed it into the
  vendor score as an input signal.
- **FR-028**: System MUST produce insights covering cost variance for service and parts against
  contract or budget, usage variance for labour and duration against the agreement, and vendor
  performance patterns from ingested reports.

**Administration and integrity**

- **FR-029**: Administrators MUST be able to change component weightings, tolerance bands, the
  score cap, the recall window, and the PPM tolerance from the client admin section.
- **FR-030**: System MUST reject a weighting set that does not total 100%.
- **FR-031**: A scorecard MUST retain the parameters it was computed under; a later parameter
  change MUST NOT alter an already-produced scorecard.
- **FR-032**: System MUST NOT invent, infer, or interpolate an attendance timestamp, completion
  date, or cost that the ingested report does not contain. A missing input is reported as missing.
- **FR-033**: System MUST exclude a work order from scoring where the data needed to score it is
  absent or self-contradictory, and MUST report the exclusion and its reason on the scorecard
  rather than omitting it silently.
- **FR-034**: System MUST record who approved or overrode every classification, parameter, and
  invoice decision, in an insert-only audit record.

*Requirements needing clarification before planning:*

- **FR-035**: Where two contracts cover the same vendor with overlapping date ranges, the System
  MUST apply the terms of the most recently signed contract to any work order raised inside the
  overlap. The signing date is the determining field; ties (same signing date) MUST be flagged to
  the PM for manual resolution before scoring proceeds.
- **FR-036**: A work order counts as a first fix when no follow-up visit to the same asset occurs
  within the recall window (30 days, per FR-013). The same window governs both the first fix rate
  and the recall rate, so a single configurable threshold covers both metrics.
- **FR-037**: The SLA response clock MUST start at the work order raise time as recorded in the
  WorkOrder entity. This is the earliest FM-company-reported timestamp and requires no additional
  notification-logging infrastructure.
- **FR-038**: Where a vendor's accreditation lapses mid-month, the 60-point score cap MUST apply
  only to work orders raised on or after the lapse date. Work orders completed before the lapse
  are scored without the cap. The lapse date is taken from the accreditation expiry record
  supplied by Feature A3.
- **FR-039**: When a work order is re-ingested with values that differ from the stored record,
  the System MUST flag the conflict to the PM rather than overwriting silently. Both the stored
  and the incoming values MUST be preserved and presented for PM selection. The PM's choice and
  its timestamp MUST be recorded in the insert-only audit trail (FR-034). The work order MUST NOT
  be included in any scoring run until the conflict is resolved.
- **FR-040**: System MUST express SLA targets stated in working days
  [NEEDS CLARIFICATION: working calendar and hours not specified — needed for any non-hourly SLA.]

### Key Entities *(include if feature involves data)*

- **Contract**: A commercial agreement with a vendor. Holds extracted and PM-corrected parameters
  — SLA targets per priority band, PPM obligations, rate card, parts framework, payment terms,
  penalty and bonus clauses — each carrying its source (contract-sourced or default) and its
  override history. Covers a vendor for a date range.
- **ContractParameter**: One extracted term. Holds the extracted value, the current value, the
  source label, and who changed it when. Kept distinct from Contract so overrides are auditable
  per field rather than per document.
- **WorkOrder**: An ingested FM work order record. Holds raise, attendance and completion
  timestamps, assigned vendor, asset, priority band, reported cost, and reported attendance
  duration, all as reported by the FM company. Provided by the existing Phase 1 UDR pipeline; this
  feature reads it and does not write to it.
- **AssetCriticality**: An L1/L2/L3 classification of an asset with its evidence, its approval
  state, and who approved it. Unapproved means L2.
- **TaskCriticality**: An L1/L2/L3 band for a task type, sourced from the contract where defined
  and from the approved standard list otherwise.
- **VendorScorecard**: A vendor's score for one month. Holds the overall score, each component,
  the parameter set used, the cap applied and why, PPM compliance, exclusions with reasons, and
  the work orders behind each component.
- **Invoice** and **InvoiceLine**: An uploaded invoice and its lines. Each line holds its matched
  work order or the absence of one, every check outcome, the pound delta, the plain-English
  discrepancy, the adversary-gate verdict where applicable, and the PM decision.
- **ScoringParameterSet**: The weightings, tolerances, cap, recall window and PPM tolerance in
  force. Versioned so a scorecard can reference the set it was computed under.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A PM confirms the extracted parameters of a standard FM contract in under ten
  minutes.
- **SC-002**: At least 80% of commercial parameters in a standard contract are extracted without
  correction; every remaining field is visibly labelled as a default rather than presented as
  contract-sourced.
- **SC-003**: Every figure on a vendor scorecard reaches its underlying work order records in at
  most two interactions.
- **SC-004**: A month of vendor scoring that currently takes a PM a working day of manual review
  completes in under five minutes, and its results agree with a hand-scored control set for a
  full month of work orders.
- **SC-005**: No work order is silently included in or excluded from a score: exclusions appear on
  the scorecard with a stated reason, and excluded plus included equals the ingested total.
- **SC-006**: Invoice verification identifies every discrepancy present in a seeded control
  invoice, with no line auto-cleared that should have been flagged.
- **SC-007**: No flagged line above £500 reaches a PM without independent arithmetic recomputation.
- **SC-008**: An administrator changes a scoring weighting and sees it applied to the next run
  without a deployment, while previously issued scorecards remain byte-identical.
- **SC-009**: Every score, flag and classification presented to a PM traces to ingested records;
  a spot-check of any twenty values finds zero without a source.
- **SC-010**: Every parameter override, criticality approval and invoice decision is attributable
  to a person and a time.

## Assumptions

- Work orders reach the WorkOrder entity through the existing Phase 1 UDR pipeline. This feature
  reads that data; ingestion of the source CSV, XLS and PDF reports is out of scope here.
- Attendance timestamps, completion dates and costs are taken as reported by the FM company. The
  platform does not verify them against an independent source; its value is systematic scoring and
  cross-referencing of what the PM already receives.
- Vendor accreditation state and `block_state` come from Feature A3 and are read, not computed
  here.
- The Single Door Orchestrator handles document upload and routing; this feature consumes its
  output.
- The Quality/Adversary gate exists as a platform capability and is invoked for high-value flags
  rather than being built here.
- Currency is pounds sterling throughout v1.2; multi-currency contracts are out of scope.
- Monthly scoring runs on the first for the preceding calendar month; the client's financial
  calendar is not modelled in v1.2.
- Scoring is retrospective, over completed work orders. Live in-flight SLA tracking is out of
  scope.
- One vendor maps to one active contract at a time in the common case; where coverage dates
  overlap, the most recently signed contract governs (FR-035).

## Clarifications

### Session 2026-08-25

- Q: From which moment should the SLA response clock start when a work order is raised? → A: Work order raise time (earliest FM-company-reported timestamp, already on the WorkOrder entity; no notification-logging infrastructure required).
- Q: How should "first fix" be defined when computing the first fix rate component of the vendor score? → A: No follow-up visit on the same asset within the 30-day recall window (FR-013); one configurable threshold governs both first fix rate and recall rate.
- Q: When a vendor's accreditation lapses mid-month, from which point should the 60-point score cap apply? → A: Cap applies only to work orders raised on or after the lapse date; pre-lapse work orders scored without cap; lapse date sourced from Feature A3 accreditation expiry record.
- Q: When a work order is re-ingested from a later report with different values, which values should the system use? → A: Flag the conflict to the PM; both values preserved; PM selects which to accept; decision recorded in audit trail; work order excluded from scoring until resolved.
- Q: When two contracts cover the same vendor with overlapping date ranges, which contract's SLA terms should govern a work order raised inside the overlap? → A: Most recently signed contract takes precedence; same-signing-date ties flagged to PM before scoring proceeds.
