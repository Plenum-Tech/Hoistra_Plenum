---
name: scenario-generation
description: Generate a batch of synthetic evaluation scenarios for the orchestrator — dimensions, tuples, grounded questions, expected answers — run them against the development instance, and leave them where Trace Lab's sync will find them.
audience: the coding agent at the keyboard
runtime: none
---

# Scenario generation

**This is not a runtime skill.** The eight skills under `svc-deepagents/skills/` are loaded by the
orchestrator and its sub-agents while they answer a question. This one is read by a coding agent and
by nobody else. It never reaches the product.

You are producing a batch of questions with known right answers, running them through the real
orchestrator on the development instance, and leaving the results where Trace Lab picks them up. A
consultant then annotates the traces. The questions must be realistic enough that their annotations
mean something, and varied enough that they find failures a hundred reworded versions of the same
question never would.

Two hundred and fifty scenarios per batch: **175 coverage, 75 challenge**.

---

## 1. Dimensions

A dimension is one way requests differ. A scenario is one value from each.

| Dimension | Values |
| --- | --- |
| Role | operative, building manager, vendor, consultant, superadmin |
| Goal | compliance status, planned maintenance, a work order, energy, vendor performance, document search, off topic |
| Subject | a building, an asset, a certificate, a contract, nothing specific |
| Rule in play | statutory, the company's own, none |
| Tool calls | none, one, several |
| Difficulty | clear, vague, missing information, sitting on a limit |
| Writing style | neutral, short, many typos, rambling, frustrated, pushy |

Take the values from the specification, not from imagination. When the orchestrator gains a domain,
this table gains a goal, and the batch after that covers it.

## 2. Sample the tuples — in code, not by asking a model

Write a script. A model asked for fifty tuples drifts toward the same handful and will not hold a
coverage guarantee.

The script must:

- emit every value of every dimension **at least once** across the batch;
- refuse the combinations listed below;
- be deterministic given a seed, so a batch can be regenerated exactly.

**Refuse a tuple only when a real person in that role would never type it.** That line matters,
because the interesting cases sit just on the other side of it:

- *Refuse:* tool calls `none` with a subject of `a certificate`. Nothing can answer it without
  reading data, so the tuple contradicts itself.
- *Refuse:* an operative asking for portfolio-wide vendor performance. That is not a question they
  would think to ask; it is a question nobody in that seat has a reason for.
- *Keep, as a challenge:* a vendor asking about a building that is not on their contract. They might
  well type it, and the agent must refuse. That is the test.

A combination someone would type and the agent must handle badly is a challenge scenario. A
combination nobody would type is noise. Do not confuse the two.

## 3. Ground each question in a real row

Pull a real row from the **development instance**, read-only, matching the tuple's subject: a
building, an asset, a certificate, a contract. The question then names something that exists.

Record, for every scenario:

| Field | What it holds |
| --- | --- |
| `scenario_id` | stable id for this scenario, used to find its trace later |
| `tuple` | one value per dimension |
| `category` | `coverage` or `challenge` |
| `question` | the text, as a person would type it |
| `grounded_on` | the table and primary key of every row the question refers to |
| `rule` | the statutory or company rule the answer turns on, and where it is written |
| `expected` | see the next step |

`grounded_on` and `rule` are what make the annotation checkable. Without them a reviewer is judging
the answer on impression; with them they are holding it against the facts it was built from.

## 4. Derive the expected answer from the spec, never from the model

Read the orchestrator's own instructions — `svc-deepagents/skills/*/SKILL.md`, the shared
`query-builder` skill, the system prompt, and the facilities-management lens — and work out what the
answer **should** be for this question and this row.

Never run the question first and write down what came back. That makes the expectation a record of
current behaviour and the comparison worthless.

When the answer and the expectation differ, one of two things is true, and they are different
findings:

- the model deviated from its spec, or
- the spec never covered this case.

Say which, in a note on the scenario, when you can tell.

**Expected answers are needed at the orchestrator level only.** A sub-agent's output is judged
through the answer the orchestrator finally gives. Writing an expectation per sub-agent costs real
effort and settles nothing.

## 5. Review before running

A generated set is always worse than it looks. Drop:

- **Duplicates** — two questions a reader could not tell apart, however different their tuples.
- **Template phrasing** — "Please provide the compliance status for building B-301 under the
  statutory requirement." Nobody types that. Real questions are shorter, vaguer and worse spelled.
- **Impossible pairings** the sampler let through.

This step is a person's, not a model's. Expect to lose a tenth of the batch and to be glad of it.

## 6. Run the batch against the development instance

Never production. Bulk evaluation runs on the development instance only.

- **Authenticate** the way the reports scheduler does: mint a session-less access token rather than
  trying to drive the one-time-password sign-in.
- **Set the session id prefix to `scenario-`.** This matters more than it looks. Trace Lab infers a
  turn's channel from its session id, and a session id starting `golden-` is excluded from the sync
  outright. A `scenario-` prefix lands as an ordinary chat turn, which the sync accepts, and gives
  you a marker to find the batch again.
- **Keep the turn attributable.** The sync skips any turn with no user id.
- **One trace per scenario.** Carry the `scenario_id` so the trace can be matched back to its
  expected answer and its grounding.

## 7. Hand the batch to Trace Lab

The sync reads the development instance's turns and creates a run with a question per turn,
deduplicated on turn id. Mark the run as **synthetic** in kind and **development** in instance;
those are separate axes, and a synthetic batch run on development is both.

Expected answers reach Trace Lab through the run import, keyed by question code (`R3-12`). Upload
them after the sync has created the questions, not before.

## What good looks like

A batch is ready when every dimension value appears, the challenge scenarios genuinely probe the
company boundary and the policy limits rather than being coverage questions with sharp words, every
scenario names the row and the rule it rests on, and a consultant opening a trace can tell within a
minute whether the answer was right.
