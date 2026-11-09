# Trace Lab: notes and annotation inside the Trace view (the Answer view goes)

Date: 9 Nov 2026 · Asked for by Hussain · Status: approved in chat ("yes") · Builds on
`2026-11-09-trace-lab-trace-tab-design.md` (the Trace tab, built and verified the same day)

## Why

The Trace tab shows an answer exactly as Langfuse does and Hussain likes it ("the trace u built is perfect"). The old
Answer view (coloured blocks) is now a second, worse way to look at the same answer, and it is the only place notes can
be written. Hussain wants one view: read the trace, annotate the trace.

## What changes

1. **The question page** keeps, unchanged: the header, the question card, the expected answer (with its history,
   proposals and decisions), the supporting-answers picker and Link an answer, the "In the browser" events strip, the
   comment box for the question as a whole (with its notes), and the next-step hint. **Removed:** the Answer | Trace tab
   bar, the colour legend, the block list (`BlockRow`) and the "Other notes" section under it. The trace is the view.
2. **Annotating in the trace** (detail pane of the selected step):
   - **Input** and **Output** each have an **Add note** button and their notes listed directly under them.
   - Selecting text inside Input or Output opens the note editor with that quote (exactly today's behaviour on the
     old blocks, kept rather than adding a separate button; Claude's call).
   - Double-clicking inside Input or Output opens the editor (no quote), as on the old blocks.
   - Notes: everyone's, the caller's first then newest, author and role, quote shown; edit and delete (with the
     confirm step) exactly as today, using the existing `NoteEditor` / `NoteList` and the page's existing handlers.
   - Each tree row and timeline row shows a small **note count** when its step has notes.
   - The root (`chat`) also lists notes that match no step ("Other notes").
3. **Look:** a design pass (impeccable) in Hoistra's own tokens: calmer tree, clearer icons, sharper detail pane,
   checked at desktop and phone widths. Light theme, existing CSS variables, no new dependencies.

## Keys (the contract that keeps everything else working)

Notes keep today's step keys, so Labels, Notes, Learning, Loop and Progress read them unchanged and old notes still land:

| Where in the trace | step_key saved | step_title saved |
|---|---|---|
| `chat` Input | the normalized record's `user` message id | `Input · chat` → kept as today's "User" title rule (see below) |
| `chat` Output | the `assistant` answer message id | today's "Final answer" title |
| an agent step's Input / Output | `<turn>:<span>:agent` / + `#output` | `Input · <name>` / `Output · <name>` |
| any other step's Input / Output | the first message of that span in `trace.messages` / + `#output` | same |
| comment on the question | `<turn>:scenario` (unchanged) | unchanged |

- Mapping a stored note back to a step: strip `#output` (output side), then find the message with that id → its
  `span_id` → the node; `<turn>:<span>:agent` → that span's node; the user / answer message → `chat` input / output;
  `<turn>:orchestrator` and anything else → "Other notes" on `chat`. `<turn>:scenario` → the question comment box.
- A step with no message of its own (a `turn` span) cannot take notes other than through `chat`.
- step_title for a new note is "Input · <step name>" / "Output · <step name>" (the old view used its block titles;
  downstream views show whatever title was stored, so both read fine).

## Non-goals

- No backend change: `save_label` already accepts message ids, agent keys, `#output`, scenario and orchestrator.
- No change to Labels / Notes / Learning / Loop / Progress / Runs list / People / Synthetic.
- No group labels, no Right / Wrong tags (the page never had them).

## Anti-goals

- An existing note disappearing from the question page.
- A new note saved under a key the other views can't place.
- Text a model wrote becoming markup (Formatted mode stays data-to-React).

## Left to the builder

The exact visual design inside Hoistra's tokens; how the selection button is positioned; empty-state wording.
