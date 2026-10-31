-- Hoist crons: engine jobs a company schedules from the chat (engines/crons/jobs.py).
--
-- A job is one entry from the catalogue (an energy anomaly scan, a compliance expiry scan, a
-- question asked of the orchestrator ...) on a cadence. It runs as the person who created it,
-- with a short-lived token minted for them, through the same route a person would call - so it
-- reads and writes exactly what that person may and nothing else. Every run is recorded, a
-- failed one too. The cadence has the report cards' three shapes (an interval, a time each day,
-- chosen days at a time) in the creator's zone.
CREATE TABLE IF NOT EXISTS plenum_cafm.hoist_cron_jobs (
    id              uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    owner_user_id   uuid NOT NULL,
    job_key         text NOT NULL,              -- the catalogue entry
    name            text NOT NULL,
    params          jsonb NOT NULL DEFAULT '{}'::jsonb,   -- e.g. {"prompt": "..."} for a question
    refresh         jsonb NOT NULL,             -- {"every_minutes": n} | {"daily_at": "HH:MM"} | {"days": [..], "time": "HH:MM"}
    timezone        text NOT NULL DEFAULT 'UTC',
    enabled         boolean NOT NULL DEFAULT true,
    status          text NOT NULL DEFAULT 'pending',      -- pending | running | ready | error | paused
    next_run_at     timestamptz,
    last_run_at     timestamptz,
    last_tried_at   timestamptz,
    last_error      text,
    last_summary    jsonb,
    source_session_id text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    removed_at      timestamptz
);
CREATE INDEX IF NOT EXISTS hoist_cron_jobs_org ON plenum_cafm.hoist_cron_jobs (organization_id) WHERE removed_at IS NULL;
CREATE INDEX IF NOT EXISTS hoist_cron_jobs_due ON plenum_cafm.hoist_cron_jobs (next_run_at) WHERE enabled AND removed_at IS NULL;

CREATE TABLE IF NOT EXISTS plenum_cafm.hoist_cron_runs (
    id          uuid PRIMARY KEY,
    job_id      uuid NOT NULL REFERENCES plenum_cafm.hoist_cron_jobs(id) ON DELETE CASCADE,
    organization_id uuid NOT NULL,
    trigger     text NOT NULL,                  -- schedule | manual
    started_at  timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    duration_ms integer NOT NULL,
    ok          boolean NOT NULL,
    summary     jsonb,                          -- the figures the job reported, a few keys
    answer      text,                           -- a question job's answer
    error       text
);
CREATE INDEX IF NOT EXISTS hoist_cron_runs_job ON plenum_cafm.hoist_cron_runs (job_id, finished_at DESC);
CREATE INDEX IF NOT EXISTS hoist_cron_runs_org ON plenum_cafm.hoist_cron_runs (organization_id, finished_at DESC);

-- Who asked. owner_user_id is whose rights a job runs with; these say who did what, and are
-- kept as the email too, so the record still reads after an account is renamed or removed.
ALTER TABLE plenum_cafm.hoist_cron_jobs ADD COLUMN IF NOT EXISTS created_by_user_id uuid;
ALTER TABLE plenum_cafm.hoist_cron_jobs ADD COLUMN IF NOT EXISTS created_by_email text;
ALTER TABLE plenum_cafm.hoist_cron_jobs ADD COLUMN IF NOT EXISTS updated_by_user_id uuid;
ALTER TABLE plenum_cafm.hoist_cron_jobs ADD COLUMN IF NOT EXISTS updated_by_email text;
ALTER TABLE plenum_cafm.hoist_cron_jobs ADD COLUMN IF NOT EXISTS removed_by_user_id uuid;
-- run_as: the job's owner, whose rights the run used. requested_by: a person's "Run now"; NULL = the schedule.
ALTER TABLE plenum_cafm.hoist_cron_runs ADD COLUMN IF NOT EXISTS run_as_user_id uuid;
ALTER TABLE plenum_cafm.hoist_cron_runs ADD COLUMN IF NOT EXISTS requested_by_user_id uuid;
ALTER TABLE plenum_cafm.hoist_cron_runs ADD COLUMN IF NOT EXISTS requested_by_email text;

-- Every action on a job, append-only: created, changed, paused, resumed, removed, run requested.
CREATE TABLE IF NOT EXISTS plenum_cafm.hoist_cron_events (
    id              uuid PRIMARY KEY,
    job_id          uuid NOT NULL,
    organization_id uuid NOT NULL,
    action          text NOT NULL,              -- created | changed | paused | resumed | removed | run_requested
    user_id         uuid,
    user_email      text,
    details         jsonb,
    at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS hoist_cron_events_job ON plenum_cafm.hoist_cron_events (job_id, at DESC);
CREATE INDEX IF NOT EXISTS hoist_cron_events_org ON plenum_cafm.hoist_cron_events (organization_id, at DESC);
