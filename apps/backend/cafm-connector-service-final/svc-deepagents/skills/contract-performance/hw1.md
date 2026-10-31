# Homework Contract Performance

Target: canonical Hoistra specification 0.37.0. Use only the typed tools offered to the authenticated role. Python and the isolated store enforce permissions, scope, arithmetic, persistence and confirmation; this skill does not grant access.

## Scope and evidence

- Property Managers may read and score linked vendors within their one assigned building. Portfolio Managers may do the same within assigned portfolio buildings. General Users and Compliance Officers have no maintenance-performance or invoice access. Only Portfolio Managers may administer scoring settings across their own organization; that exception does not expose other portfolios' records.
- Work orders and reports originate in external systems. Read only verified imported reports. Never create, assign, approve, dispatch, transition or close an operational work order.
- Every contract, report, accreditation, block-period and invoice diagnostic must cite an immutable source identifier and version. If the source reference, scope, digest, dates or compatible unit is missing or contradictory, report the result as unavailable or excluded with its reason. Never fill a missing timestamp, cost, target or linkage from guesswork.
- Apply an elapsed-hour SLA only when the source gives hours or an authoritative hour target. Working-day targets without an accepted calendar are unassessable; do not convert them.
- Resolve overlapping contracts by the latest signed date. Same-date ties require manual resolution before scoring. Only contract terms effective on the work-order raise date apply.

## Performance scoring

- Score completed, source-backed work-order reports in the requested complete calendar month. Preserve the exact reporting period, generation time, excluded records/reasons, source IDs and current scoring-configuration version/values in each immutable scorecard snapshot.
- Use exactly five weighted components: response SLA, completion SLA, first fix, recall, and accreditation currency. The configured weights must total 100%. Invoice results are a separate diagnostic; never add them as a sixth component or let them silently change a performance score.
- Repeat faults on the same asset use the configured recall window for both recall and first-fix calculations. A missing/contradictory required report value excludes that work order with a visible reason rather than silently dropping it.
- The recall search includes source reports after the scorecard month, up to the exact configured elapsed-day boundary. Asset identity includes its building. Missing accreditation expiry or inconsistent lapse evidence is unassessable; it is never treated as current or silently uncapped.
- Apply the configured score cap only to individual work-order contributions whose raise date falls inside source-evidenced accreditation lapse/block history. Expiry day remains current; lapse begins the following day. Do not retroactively cap earlier work orders or cap a whole mixed-period vendor month because one job qualifies. Disclose each capped job and evidence reference. Current blocked state alone does not prove historical cap applicability.
- Report PPM compliance separately: completed visits within the configured inclusive plus/minus day tolerance divided by scheduled visits. Show the tolerance and denominator. Missing schedule or completion dates are not compliant zeroes; identify them as unavailable.
- Preserve exact parameter snapshots. A later configuration change cannot rewrite an earlier scorecard.

## Invoice diagnostic

- Keep invoice verification separate from vendor score. Report invoice period and source, line count, matched-to-work-order count, cleared count, flagged count and unmatched count with definitions. Matched and flagged populations may overlap; do not describe them as an exclusive partition.
- A line is cleared only when it is matched and all applicable checks pass. Use an explicit hourly rate first; otherwise day rate divided by eight; if neither is sourced, do not run a rate check. Any discrepancy arithmetic must use source values and be independently reproducible.
- Invoice-to-report matches require the same building and vendor. Return all input evidence references and versions, and persist an immutable diagnostic snapshot. An independent arithmetic pass does not approve a discrepancy or authorize payment.
- Invoice approval/challenge/rejection, payment authorization and Compliance Officer access are not offered by this Homework tool slice.

## Scoring configuration

- Only a Portfolio Manager may request an organization-wide settings preview. Show current and proposed values, organization impact, reason, exact version and preview hash. The dedicated confirmation endpoint requires the human to confirm that exact preview.
- Validate declared fields and constraints, including five weights totaling 100%. Record actual authenticated actor, role, organization, before/after, reason, timestamp and outcome. Reject stale versions and duplicate/changed previews. Configuration does not alter HOI, certificate policy, contract source evidence or other undeclared defaults.

## Answering and routing

Use returned data only. State source freshness and unavailable values. Distinguish proposal, calculation, persisted snapshot and confirmation. A prepared settings preview does not change configuration. Never claim email delivery, payment approval or work-order creation from these tools. If a user asks for a workflow outside this profile, say it is unavailable and identify any safe read or diagnostic that was actually performed.
