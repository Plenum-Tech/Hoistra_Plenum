-- One row per finished migration whose workbook the platform has read after the write: the
-- contract terms, invoices and plant telemetry a single end-to-end workbook carries
-- (engines/contract_performance/workbook_extras_runner.py).
--
-- The row is also the claim. A run is read once, by whichever gets there first - the
-- in-service sweep, the Migration page, or the chat - and the others see its status instead
-- of reading it again. Ids are text because migration_jobs.id and .organization_id are not
-- the same type on every database.
CREATE TABLE IF NOT EXISTS plenum_cafm.workbook_extras_runs (
    migration_id    text PRIMARY KEY,
    organization_id text,
    status          text NOT NULL,              -- running | done | none | failed | predates
    trigger         text,                       -- sweep | page | chat | backfill
    attempts        integer NOT NULL DEFAULT 1,
    summary         jsonb,
    error           text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz
);
CREATE INDEX IF NOT EXISTS workbook_extras_runs_org_started
    ON plenum_cafm.workbook_extras_runs (organization_id, started_at DESC);

-- Runs that finished before the sweep existed are not read again on its first tick: their
-- terms and invoices were read by hand or not at all, and re-reading an old upload over a
-- company's newer data is not the sweep's call to make. The page's "read again" still can.
-- The cutoff is fixed, not "now": startup runs this file on every restart, and a run that
-- finished a minute before a restart must still be swept.
INSERT INTO plenum_cafm.workbook_extras_runs (migration_id, organization_id, status, trigger, finished_at)
SELECT m.id::text, m.organization_id::text, 'predates', 'backfill', now()
  FROM plenum_cafm.migration_jobs m
 WHERE m.status = 'complete' AND m.completed_at < TIMESTAMPTZ '2026-09-29 11:00:00+00'
ON CONFLICT (migration_id) DO NOTHING;
