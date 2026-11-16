# Expected answers drafted from SPEC.md for live questions

Approved by Hussain, 14 Nov 2026 ("YES" to the short design).

## Intent

When someone asks Hoistra a question on the live platform, its trace in Trace Lab should show an expected answer
without anyone typing it: what a correct answer must do, according to the approved SPEC.md. The annotator still
decides: the draft counts only once a person accepts or edits it.

## Approach (chosen: A)

- A. Expected behaviour from the rules (chosen): one model call reads the approved SPEC.md and the question, and writes
  what a correct answer must do, citing the rules that apply. Always grounded in rules people approved.
- B. Expected values (rejected): would need Trace Lab to read the company's records itself, a second answer engine
  that can be wrong the same ways Hoistra is.

## Design

- Trigger: the automatic sync (every minute) and Sync now, after live questions are synced. Candidates: questions of
  live day runs (kind production), asked by hand (not scenario turns), asked in the last 7 days, with no expected
  answer of any source. At most 20 per round. Nothing is drafted when no SPEC.md version is approved.
- Draft: one call through the coding agent's model path (agent._ask, role tracelab_agent, model TRACE_LAB_AGENT_MODEL).
  Input: the approved SPEC.md (rules) and the question. Never the answer Hoistra gave. Output: the expected answer
  text and the rule codes it relies on; codes not in the approved spec are dropped. When no rule applies, the draft
  says so (stored, so the question is not drafted again every minute).
- Stored: tracelab.lab_expected_answers, source 'coding_agent', author trace-lab-agent, evidence
  {"drafted_from": "SPEC.md", "spec_version": n, "rules": [...]}. Written under the sync lock, re-checking that the
  question still has no expected answer. A model failure stores nothing and is tried next round.
- Shown: the session page, per turn: "Expected answer", the text, "Drafted from SPEC.md v<n> · rules S-1, S-4 · not
  accepted yet", with Accept (approves the proposal: POST /lab/proposals/expected_answer/{id}) and Edit (saves a
  person's version: PUT /lab/q/{code}/expected). A turn with none shows Add expected answer. Read through a new
  GET /lab/turns/{turn_id}/expected, bounded to the caller's company.
- Off switch: TRACE_LAB_AUTO_EXPECTED=false. The sync status says how many were drafted and any error.

## Non-goals

- Changing the live Hoistra app (Trace Lab only).
- Expected values from the company's records.
- Scoring or grading answers against the draft.
- Drafting for Codex scenario turns (their scenarios carry expected outcomes).
