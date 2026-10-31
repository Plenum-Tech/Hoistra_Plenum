# Hoist Trace Lab — start here

**Date:** 2026-10-15. **Status:** plans for review. Nothing is implemented, committed, deployed or applied to any database.

Read in this order:

1. `docs/superpowers/specs/2026-10-15-hoist-trace-lab-design.md` — the design. Its head holds the decisions (D1–D15), the reconciliations (R1–R37), the milestones and the prerequisites (P1–P8). Every plan below defers to it.
2. `docs/superpowers/specs/2026-10-15-hoist-trace-lab-analysis.md` — the detailed analysis of Aasim's Cartwheel Trace Lab, of Hoist today, and of the earlier eval build (as lessons only).
3. The seven plans below, in order. Each one ships working, tested software and ends with a task for Hussain (migration, rebuild, manual checks).

| # | Plan | What you can do once it ships | Tasks |
|---|---|---|---|
| M0 | `2026-10-15-trace-lab-m0-foundation.md` | Safe base: versioned migrations that only verify at boot, resumable jobs, provenance on every turn, gate spans for answer rewrites, the eval-data guards | 31 |
| M1 | `2026-10-15-trace-lab-m1-conversations.md` | Read a chat as one conversation: User → Model → Tool → Model → Answer, with model-call cards | 21 |
| M2 | `2026-10-15-trace-lab-m2-open-coding.md` | Mark answers right or wrong with a note on the first failure; sampled review batches; Map and Progress | 21 |
| M3 | `2026-10-15-trace-lab-m3-golden-runs.md` | **Upload the Excel and have the system re-ask every question safely and grade the answers automatically.** The rest go to people | 44 |
| M4 | `2026-10-15-trace-lab-m4-taxonomy-labels.md` | Group the wrong answers into failure modes, label them, and let the AI propose groupings that people accept | 29 |
| M5 | `2026-10-15-trace-lab-m5-judges.md` | Code checks and validated AI judges per failure mode (train/dev/test, freeze, test once) | 21 |
| M6 | `2026-10-15-trace-lab-m6-watch.md` | Watch live traffic with corrected rates and thresholds, turn failures into regression cases, and gate a release on them | 22 |

The core idea (Excel of known answers checked automatically, people review the rest) works end to end after **M3**.

## Before you build anything (prerequisites from the spec)

- **P1.** hoist-f5's company-boundary fixes (only in `stash@{0}` today) must land before any rebuild of svc-deepagents, svc-udr or svc-operations-intelligence from this branch.
- **P2.** The three chat fixes (date binding, whole-day bounds, yes/no flags) must land before golden runs mean anything.
- **P3.** You review each migration file and apply it yourself in an interactive shell. No Claude session applies a migration.
- **P4.** The Anthropic key is present in svc-deepagents, and the cost caps are chosen.
- **P5.** Before about 31 Dec 2026, every deployment that writes `agent_trace_*` runs the keep-list purge (or sets a long `TRACE_RETENTION_DAYS`).

## How to execute

Pick one per plan:

- **Subagent-driven** (superpowers:subagent-driven-development): a fresh agent per task, a reviewer after each. Most thorough.
- **Native** (superpowers:executing-plans): one session works through the tasks. Cheapest.

Every task is test-first: write the failing test, see it fail, implement, see it pass, leave the work uncommitted. Nothing is committed until you name it.

## House rules the plans carry

No database writes or direct SQL from a Claude session. No git commit, stash, checkout or push unless you name it. Other Claude sessions share the working tree, so edits to shared files are exact-string replacements. No dummy data on screen. No internal names in the UI. Backend containers have no source mount, so a rebuild is needed to see a change, and you run it.
