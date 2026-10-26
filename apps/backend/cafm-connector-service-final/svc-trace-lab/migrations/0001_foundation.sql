-- 0001_foundation.sql (Trace Lab Phase 2): schema, ledger, the append-only guard, jobs, evidence.
-- Runs in Trace Lab's OWN database (hoist_tracelab), never in the application database.
-- Applied only by migrate.apply_pending. Never edited once applied to a shared database
-- (SHIPPED_CHECKSUMS in trace_lab/migrate.py): a correction is a new numbered file.
SET LOCAL lock_timeout = '3s';

CREATE SCHEMA tracelab;
COMMENT ON SCHEMA tracelab IS 'Hoist Trace Lab evaluation data, owned by svc-trace-lab. Separate from the application database.';

CREATE TABLE tracelab.schema_migrations (
    version     integer     PRIMARY KEY CHECK (version > 0),
    checksum    text        NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    applied_at  timestamptz NOT NULL DEFAULT now(),
    applied_by  text        NOT NULL CHECK (length(applied_by) BETWEEN 1 AND 200)
);
COMMENT ON COLUMN tracelab.schema_migrations.checksum IS 'sha256 hex of the file bytes after dropping a UTF-8 BOM and turning CRLF or CR into LF.';
COMMENT ON COLUMN tracelab.schema_migrations.applied_by IS 'startup:<host> in apply mode, cli:<name> from the CLI, pytest or snapshot on a scratch database.';

CREATE FUNCTION tracelab.refuse_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE')
       AND coalesce(current_setting('tracelab.allow_purge', true), '') = 'on' THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'tracelab.% is append-only: % refused', TG_TABLE_NAME, TG_OP;
END
$$;
COMMENT ON FUNCTION tracelab.refuse_change() IS 'Append-only guard. UPDATE always refused. DELETE and TRUNCATE only after SET LOCAL tracelab.allow_purge = on (company offboarding).';

CREATE TABLE tracelab.jobs (
    id                uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    kind              text          NOT NULL CHECK (kind IN ('golden_run','golden_grade','judge_run','proposal_grouping',
                                                             'proposal_depth_scan','proposal_cells','monitor_batch','company_delete')),
    status            text          NOT NULL DEFAULT 'queued'
                                    CHECK (status IN ('queued','running','paused','done','failed','capped','interrupted','cancelled')),
    organization_id   uuid,
    scope_key         text          NOT NULL CHECK (length(scope_key) BETWEEN 3 AND 200),
    params            jsonb         NOT NULL DEFAULT '{}'::jsonb,
    total             integer       CHECK (total IS NULL OR total >= 0),
    done              integer       NOT NULL DEFAULT 0 CHECK (done >= 0),
    progress          jsonb         NOT NULL DEFAULT '{}'::jsonb,
    usd_estimate      numeric(12,6) CHECK (usd_estimate IS NULL OR usd_estimate >= 0),
    usd_cap           numeric(12,6) CHECK (usd_cap IS NULL OR usd_cap >= 0),
    usd_spent         numeric(12,6) NOT NULL DEFAULT 0 CHECK (usd_spent >= 0),
    usd_reserved      numeric(12,6) NOT NULL DEFAULT 0 CHECK (usd_reserved >= 0),
    stop_requested    boolean       NOT NULL DEFAULT false,
    worker            text,
    heartbeat_at      timestamptz,
    error             text          CHECK (error IS NULL OR length(error) <= 2000),
    created_by        uuid,
    created_by_email  text,
    created_at        timestamptz   NOT NULL DEFAULT now(),
    started_at        timestamptz,
    finished_at       timestamptz,
    updated_at        timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT jobs_params_object CHECK (jsonb_typeof(params) = 'object'),
    CONSTRAINT jobs_params_no_secrets CHECK (NOT (params ?| ARRAY['authorization','bearer','token','access_token','refresh_token','api_key','password'])),
    CONSTRAINT jobs_model_spend_capped CHECK (kind = 'company_delete' OR usd_cap IS NOT NULL)
);
CREATE UNIQUE INDEX ux_jobs_one_open_per_scope ON tracelab.jobs (scope_key)
    WHERE status IN ('queued','running','paused','interrupted');
CREATE INDEX ix_jobs_org_kind_created ON tracelab.jobs (organization_id, kind, created_at DESC);
CREATE INDEX ix_jobs_running_heartbeat ON tracelab.jobs (heartbeat_at) WHERE status = 'running';
COMMENT ON COLUMN tracelab.jobs.organization_id IS 'NULL = platform scope: a judge run, monitoring across companies, or a company offboarding.';
COMMENT ON COLUMN tracelab.jobs.scope_key IS 'One open job per key, e.g. golden_run:<org>, judge_run:<judge>:<split>, company_delete:<org>.';
COMMENT ON COLUMN tracelab.jobs.params IS 'Inputs to resume the job. Never a bearer token or other credential: golden runs are browser-stepped for that reason.';
COMMENT ON COLUMN tracelab.jobs.usd_reserved IS 'Spend reserved for calls in flight. A call is refused when usd_spent + usd_reserved + its estimate exceeds usd_cap.';
COMMENT ON COLUMN tracelab.jobs.heartbeat_at IS 'Set while the job runs. A running job whose heartbeat is older than 120 s is marked interrupted at boot.';
COMMENT ON COLUMN tracelab.jobs.worker IS 'hostname:pid of the process running the job.';

CREATE TABLE tracelab.evidence_turns (
    turn_id          text        PRIMARY KEY CHECK (length(turn_id) BETWEEN 1 AND 200),
    organization_id  uuid        NOT NULL,
    captured_at      timestamptz NOT NULL DEFAULT now(),
    turn             jsonb       NOT NULL CHECK (jsonb_typeof(turn) = 'object'),
    reason           text        CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 40)
);
CREATE INDEX ix_evidence_turns_org_captured ON tracelab.evidence_turns (organization_id, captured_at DESC);
CREATE TRIGGER evidence_turns_rows BEFORE UPDATE OR DELETE ON tracelab.evidence_turns
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();
CREATE TRIGGER evidence_turns_truncate BEFORE TRUNCATE ON tracelab.evidence_turns
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();
COMMENT ON TABLE tracelab.evidence_turns IS 'A copy of a chat turn row taken when Trace Lab first relies on it, so the 90-day span purge can never remove its evidence.';
COMMENT ON COLUMN tracelab.evidence_turns.turn_id IS 'The trace turn id (agent_trace_turns.turn_id in the application database). No foreign key across databases.';
COMMENT ON COLUMN tracelab.evidence_turns.reason IS 'Why it was copied first: sampled, annotated, labelled, judged or golden.';

CREATE TABLE tracelab.evidence_spans (
    turn_id  text    NOT NULL REFERENCES tracelab.evidence_turns (turn_id),
    span_id  text    NOT NULL CHECK (length(span_id) BETWEEN 1 AND 200),
    seq      integer NOT NULL CHECK (seq >= 0),
    span     jsonb   NOT NULL CHECK (jsonb_typeof(span) = 'object'),
    PRIMARY KEY (turn_id, span_id)
);
CREATE INDEX ix_evidence_spans_turn_seq ON tracelab.evidence_spans (turn_id, seq);
CREATE TRIGGER evidence_spans_rows BEFORE UPDATE OR DELETE ON tracelab.evidence_spans
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();
CREATE TRIGGER evidence_spans_truncate BEFORE TRUNCATE ON tracelab.evidence_spans
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();
COMMENT ON TABLE tracelab.evidence_spans IS 'The spans of an evidence turn, copied with it. Insert the turn first.';
