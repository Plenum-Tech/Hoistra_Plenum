---
name: answer-spec
description: The orchestrator's answer specification (Workstream A, 11 Nov 2026) - how an answer is written from the tools' evidence, how each kind of result is said, the four answer forms and one worked example of each. Loaded by the planner's writer and the final answer step.
---

# How the orchestrator answers

This is the spec.md of Workstream A. The code that enforces each part is named beside it.

## A1 · One way out

A sub-agent's text is a draft. The answer the user reads is written by one final step from what the
tools returned, never from general knowledge, and every figure in it is traced to a tool result
(agents/final_answer.py; the planner's own synthesis for a planned turn).

Every question takes the same path: it lands on the orchestrator, which records its plan (the
planner's for a question in several parts, one step from the router or a keyword route otherwise);
the sub-agent reads the records and drafts; the draft comes back to the orchestrator, which writes
the short answer the question asked for. That includes the compliance and contract analysts
(12 Nov 2026): their text is the draft, their card keeps its data - the KPIs, the certificates, the
actions - and the chat shows one text, the orchestrator's. If the final step cannot write, the
analyst's own answer, which is checked against the rows in code, stands.

## A3 · What a result is

Every tool result has one state (agents/evidence.py): **OK**, **ZERO RESULTS** (the lookup worked and
found none - an answer), **NOT RECORDED** (the records exist but the field asked about is empty),
**UNAVAILABLE** (the source could not be reached or is not available here), **FAILED** (the lookup
itself failed) or **DENIED** (no access, or the sign-in expired). A failure carries a record in
business words: what happened, whether the user can act, and the next step. Where two sources
disagree about the same record, the answer says which says what.

An answer says a lookup failed or could not be read only when one did, and never speaks about the
tools themselves ("the only tool that ...", a tool's name): it speaks about the records. When no
lookup ran for a part and the previous answer already settles it - it found none of the things the
question is about - the answer comes from the previous answer and says so (12 Nov 2026).

## A4 · A conversation, and a complete answer

- **Continuity.** After every answer the thread keeps the records the answer showed - buildings,
  vendors, assets, work orders - taken from the tool results, never from the answer's wording, and
  the period. A follow-up ("those", "what about the vendors", "for each of them") reads inside that
  set without asking again (agents/thread_scope.py) - and is never asked which area it means, even
  when it names none ("which of those are overdue?"): the area is the one just answered.
- **One company.** Every lookup of a turn reads the same company: the asker's own, or the one a
  platform administrator named. No turn mixes companies or reads some lookups from one and some
  from another.
- **Completeness.** Every part of the question is answered, or said plainly to be unanswered and why.
  The writer is given the parts; an answer that leaves one out is rewritten once.
- **Form.** One of the four forms below, chosen from what the lookups did.

## Answer forms

### F-SUCCESS · every lookup worked

    <the direct answer, in one or two sentences, with its scope>
    <the detail the question asks for: a short list or one table>
    <only for a judgement question: **What to do:** one to three actions>

### F-PARTIAL · some lookups failed

    <the direct answer to the parts that worked, with its scope>
    <their detail>
    **Not answered:** <the part of the question> - <why, in plain words>. <its next step>

### F-DENIAL · a lookup was refused

    **You do not have access to <what was asked>.** <one sentence on why, in plain words>
    <anything the user could see, if it answers part of the question>
    **Next step:** <ask an administrator of your company for access / sign in again>

### F-FAILURE · nothing could be read

    **<what could not be done>** - <why, in plain words>.
    **Next step:** <what the user can do: ask again in a moment, narrow it down, tell an administrator>

## Examples

### E-1 · F-SUCCESS
Question: How many work orders are overdue at Bishopsgate Tower?
Results: OK - 3 overdue work orders (WO-1182 Chiller service, 21 days; WO-1190 Fire door repair, 9 days; WO-1201 Lift inspection, 4 days).
Answer:

    **3 work orders are overdue at Bishopsgate Tower.**
    | Work order | What | Days overdue |
    |---|---|---|
    | WO-1182 | Chiller service | 21 |
    | WO-1190 | Fire door repair | 9 |
    | WO-1201 | Lift inspection | 4 |

### E-2 · F-SUCCESS with no records
Question: Which vendors are blocked at Harbour Point?
Results: ZERO RESULTS - no blocked vendors at Harbour Point.
Answer:

    **No vendors are blocked at Harbour Point.** Every vendor working there is cleared to work.

### E-3 · F-PARTIAL
Question: How much energy did Kingsway House use last month, and which of its vendors are blocked?
Results: OK - Kingsway House used 41,250 kWh in October 2026. FAILED (the lookup took too long) - its vendors.
Answer:

    **Kingsway House used 41,250 kWh in October 2026.**
    **Not answered:** which of its vendors are blocked - the vendor lookup took too long. Ask again in a moment.

### E-4 · F-DENIAL
Question: Show me the invoices for Riverside Court.
Results: DENIED - no access to Riverside Court.
Answer:

    **You do not have access to Riverside Court's invoices.** Your account is not allocated to that building.
    **Next step:** ask an administrator of your company to give you access to Riverside Court.

### E-5 · F-FAILURE
Question: What is our compliance position across the portfolio?
Results: UNAVAILABLE - the compliance service is not responding.
Answer:

    **The compliance position could not be read** - the service that holds it is not responding.
    **Next step:** ask again in a few minutes; if it keeps happening, tell your administrator.

### E-6 · F-SUCCESS with a conflict and a field not recorded
Question: What did B-301 use in October, and what is its floor area?
Results: OK - the energy page gives B-301 41,250 kWh; the meter reads give 39,800 kWh. NOT RECORDED - floor area.
Answer:

    **B-301 used 41,250 kWh in October 2026 by the energy page, but 39,800 kWh by the meter reads** - the two sources disagree.
    Its floor area is not recorded, so its use per square metre cannot be worked out.
