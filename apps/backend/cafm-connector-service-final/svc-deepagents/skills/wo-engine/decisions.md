---
name: decisions
description: The Maintenance page — decisions owed and their four states, where each one came from, what "statutory" means, PPM against plan, and reading the inspection reports together.
---

# Maintenance — decisions owed, not work raised

A work order here is **not raised by the FM operative**. It arrives from a trigger — a vendor
blocked, a certificate expiring, an asset flagged, an anomaly priced — and waits for a decision.
That inversion is the whole shape of this domain: the question is almost never "raise me an
order", it is "what is waiting on me, and why".

---

## 1. The four states are four different problems

`list_maintenance_decisions(state=…)`

| state | what it means | what unblocks it |
|---|---|---|
| **Blocked** | cannot proceed until a vendor or certificate is fixed | somebody else's action |
| **To raise** | another module says an order should exist, and none does | your decision to create it |
| **Awaiting approval** | drafted, waiting on a person | an approval |
| **Deviation** | a live order drifting off SLA or certificate | attention now |

**They are not one backlog.** "Ten decisions owed" is a useful headline only when the split
follows it, because a blocked order needs a vendor chased, a "to raise" needs a judgement, and an
approval needs thirty seconds from one named person.

**A "To raise" row has no work order yet — `work_order` is NULL.** Never report it as an order
that exists, never quote its reference (there isn't one), and never add its estimate to committed
spend. It is a recommendation with a price on it.

## 1a. `count` is the page. `total` is the answer.

The payload carries four numbers and they are not the same:

| field | means |
|---|---|
| `count` | how many rows came back — **the page size, not a finding** |
| `matched` | how many matched the filter |
| `total` | how many there are |
| `total_is_capped` | true when `total` is a floor, because more exist than were read |

At the default `limit=200` this estate returns **`count` 200 and `total` 374**. Reading `count`
and reporting "200 decisions owed" is wrong by 174, and it is wrong in the direction that looks
plausible — it is a round number that happens to be the limit.

**`by_state` and `by_source` are computed over every decision, not over the page.** So the split
(Blocked 69 · Deviation 55 · Awaiting approval 175 · To raise 75) is correct even when the list
is truncated. Answer counting questions from those, never by counting the rows you were handed.

Where `total_is_capped` is true, say "at least" — a capped figure presented as the total is how
a screen ends up reading "134 of 134" while showing 134 of two thousand.

## 2. Source is where the trigger came from

Compliance · Vendors · Assets · Energy · Maintenance.

**"Which decisions are statutory?"** is the compliance-sourced ones — a certificate lapsed or
inside 30 days. Answer it with `source="Compliance"` and name the certificate and its date, not
just the count: "3 statutory" tells nobody which building loses its cover first.

A decision sourced from Energy came from a priced anomaly; one from Assets came from a condition
flag. Saying which module raised it is half the answer, because it tells the reader which team
already has evidence.

**Measured on this estate: Maintenance 175, Vendors 124, Compliance 75 — and Assets 0, Energy 0.**
The two filters exist and produce nothing today, so "no decisions from the energy engine" is a
statement about wiring, not about the estate being clean. Say which sources are populated rather
than reporting an empty filter as an absence of problems.

## 3. PPM is measured against plan, and a report is the proof

`get_ppm_contracts()` — visits done against planned, missed, late, reports on file, deferrals,
next visit, state.

**A visit without a report counts as done but UNVERIFIED.** This is the most important line on
that panel. "12 of 12 visits" with "10 of 12 reports" is not a contract in good standing — it is
two visits nobody can evidence. Always give visits and reports together; a single percentage to
plan hides it.

A contract at **blocked** is blocked by something in compliance — an accreditation lapse — and
its missed visits keep accruing while it stays that way. So the PPM figure and the blocked
decision are the same fact seen twice, and should be reported as one story rather than two.

Deferrals are not misses. A deferred visit was moved by agreement; a missed one was not. Keep
them apart.

## 4. The inspection reports are read together

`get_inspection_intelligence()` — every report attached to a completed order, read as a corpus
with the warranty documents, so a question is answered across all of them rather than one file at
a time.

What that makes answerable, and what each is worth saying:

- **Recommendations never converted to orders.** An inspector wrote it down and nothing happened.
  Where the same asset is now flagged by the energy engine, say so — *the inspector saw it first*
  is the finding, not the recommendation on its own.
- **Anomalies corroborated by an earlier report.** A cause named in a report dated *before*
  detection is independent confirmation, and it is the difference between a detector's guess and
  a thing two sources agree on.
- **Findings on parts still under warranty.** That is invoiced work that may be claimable, and
  it is money rather than a note.
- **Condition grades.** An asset graded poor by an inspector is evidence a condition score does
  not have.

The panel returns **`unanswerable`** — questions the corpus cannot settle. Report those as
unanswerable. *"We looked and there are none"* and *"we could not look"* are different claims,
and only the second needs a person to go and find the document.

## 5. Shape of an answer about one decision

Lead with what it is waiting on, because that is what the reader can act on:

> **WO-4512 — Boiler-22, Town Hall.** P2, blocked. Meridian Heating cannot be allocated gas work
> until the register shows them current — their Gas Safe registration lapsed 31 Mar 2026. A
> statutory CP12 is due. Estimated £640. Two alternatives are on the framework.

Then the fields, each with its source. Where a field is unrecorded, say unrecorded — see
`work-orders.md` for which columns actually carry data, because most of that table is empty and
a confident "none" drawn from a null column is the easiest wrong answer in this domain.

**Changing the assigned vendor re-runs the accreditation check.** Say that when proposing a swap:
it is not a field edit, it is a re-evaluation that can unblock the order or block it again.

## 6. Never

- Never report the four states as one backlog.
- Never treat a "To raise" row as an existing order, or quote a reference for it.
- Never give a PPM percentage without the report count beside it.
- Never count a deferral as a miss.
- Never answer a corpus question without reporting what was `unanswerable`.
- Never say "no statutory decisions" from an empty `source` filter without checking the field is
  populated — see `work-orders.md` §5.
- Never describe a check the system did not run: `resource_skills` and `work_order_tasks` are
  both empty, so no availability check and no task list happened.
