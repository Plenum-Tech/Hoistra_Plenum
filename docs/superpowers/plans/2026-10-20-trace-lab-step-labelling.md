# Trace Lab step labelling — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** In the standalone Trace Lab app (`/lab/`), people from a company (Hoistra users added by a Hoistra admin) see how each
answer was made, step by step, label any step Right / Wrong / Not sure with a note, and admins see every label collected in one
place with who did it. All other Trace Lab screens are hidden.

**Approved by Hussain, 20 Oct 2026** (senior's request): sign in with a Hoistra account; Trace Lab roles domain expert, tester,
admin, user set inside Trace Lab; one company per person; see who did what; hide the other screens.

**Calls made here (check):** a plain Hoistra user who signs in waits as "pending" until a company admin gives them a Trace Lab
role (otherwise any building-restricted Hoistra user would see every answer of the company); everyone active sees every person's
labels on a step (the request is "see who did what"); company admins and the platform team (acting for a company) are always
Trace Lab admins.

## Global constraints
- Golden Rule: only new files in `svc-trace-lab/` and `src/lab/` + the tracelab module; Trace Lab's own database only.
- Label values: `right`, `wrong`, `unsure` (shown Right / Wrong / Not sure). Note up to 2,000 characters.
- Trace Lab roles: `domain_expert`, `tester`, `admin`, `user`. Member status: `pending`, `active`, `removed`.
- Versioned rows (edit = new row, clear = tombstone row) with the existing `tracelab.supersede_only()` guard.
- A label stores who (user id, email, Trace Lab role at the time) and when, plus the question and step title as copied text.

## Review focus
1. A pending or removed person must get no question text, no labels and no member list.
2. A person of company A must never read or label company B's answers.
3. Two people labelling the same step keep two labels; one person's edit replaces only their own.
4. A label on a step of a turn that is not the company's is refused (404), never stored.
5. CSV export must neutralise cells starting with `=`, `+`, `-`, `@` (spreadsheet formulas).

### Task 1: Migration 0008 (members, step labels)
`migrations/0008_step_labels.sql`: `tracelab.lab_members` and `tracelab.step_labels`, versioned, head unique indexes, supersede
guard; regenerate `catalog.json` on the scratch DB; file-rule test `tests/test_trace_lab_0008_file.py`.

### Task 2: `trace_lab/labelling.py` + `trace_lab/routes_labelling.py`
`access(principal, org) -> Member` (platform / company admin / member lookup, pending row on first visit);
members list and decide; questions list and one question (company read with an admin view of the company);
step labels for a turn, save (base check -> 409 stale), clear; collected list with filters (admins all, others own) and CSV.
Routes under `/api/trace-lab/lab/...`, registered in `app.ROUTERS`. Tests: pure rules + scratch-DB story.

### Task 3: Frontend
`src/lab/labApi.js`, `src/lab/logic/labelling.js` (pure view models), `src/lab/Questions.jsx` (list + flow + step labels),
`src/lab/AllLabels.jsx` (table, filters, CSV), `src/lab/People.jsx` (roles), `LabApp.jsx` (pending screen, nav: Questions,
All labels, People; other screens hidden). `Timeline.jsx` exports its step body so details reuse the existing rendering. Tests.
