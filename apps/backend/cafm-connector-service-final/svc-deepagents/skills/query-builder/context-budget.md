---
name: context-budget
description: How an agent keeps its working context under budget by replacing tool results it has finished with short notes. Read on every model step when the context passes half its budget; the skill lab tunes this text.
---

# Keeping the working context small

Your working context has a token budget. Every tool result you have read stays in it until you
release it. When the context is getting full you will see a list of results with a label, the
tool that produced it and its size, for example `r3 list_building_certificates (9,800 tokens)`.

## When to compact

- Compact as soon as the notice says the context is past half its budget, before reading more.
- Compact a result once you have taken what the question needs from it. A result you have not
  used yet, or will compare against another result, stays.
- Never release the result you are about to quote from in the answer.

## How to compact

Call `compact_context` with:

- `release`: the labels of the results you are done with, largest first.
- `notes`: what you still need from them, and nothing else. Keep every figure, date, code, name
  and status you may state in the answer, exactly as the tool returned it, with the label it
  came from. Drop rows the question does not ask about, repeated fields and formatting.

Good notes for a certificate list read for "which fire certificates have lapsed":

    r3 list_building_certificates: lapsed fire certs = FRA Bishopsgate Tower (expired 2026-06-29,
    vendor Pennard Fire, BAFE lapsed); EICR B-301 L2 (expired 2026-08-14). 41 other certs current,
    not needed.

The notes must be shorter than what they replace, or the compaction is refused.

## Rules

- A figure you state in the answer must come from a result still in context or from your notes.
  If you released it and need it again, call the tool again rather than guess.
- Notes record what the tool returned. They are not the answer and add no judgement.
- Compacting is not a step towards the answer. Do it in one call, then carry on with the task.
