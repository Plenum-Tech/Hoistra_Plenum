-- 0006_watch.sql (Trace Lab Phase 7): monitoring batches, their per-answer verdicts, eval cases and their runs.
-- Runs in Trace Lab's OWN database (hoist_tracelab), never in the application database: no table here has a foreign key
-- to a chat table, a turn is named by id and its evidence is copied by snapshot when it is monitored or made a case.
-- Alters no earlier table: the Regression cases golden set (kind regression, version 0) and the 20 run caps are 0002,
-- the monitor_flagged sampling strategy is 0003. Applied only by migrate.apply_pending. Never edited once applied to a
-- shared database (SHIPPED_CHECKSUMS in trace_lab/migrate.py): a correction is a new numbered file.
SET LOCAL lock_timeout = '3s';

CREATE TABLE tracelab.monitor_batches (
    id                uuid PRIMARY KEY,
    organization_id   uuid,
    period_label      text NOT NULL CHECK (length(period_label) BETWEEN 3 AND 40),
    window_start      timestamptz NOT NULL,
    window_end        timestamptz NOT NULL,
    channel           text NOT NULL DEFAULT 'chat' CHECK (channel = 'chat'),
    sample_rate       numeric(5,4) NOT NULL CHECK (sample_rate > 0 AND sample_rate <= 1),
    seed              integer NOT NULL,
    risk_groups       text[] NOT NULL,
    judges            jsonb NOT NULL,
    checks            jsonb NOT NULL,
    plan              jsonb,
    results           jsonb,
    status            text NOT NULL CHECK (status IN ('queued','running','done','capped','failed','interrupted')),
    job_id            uuid REFERENCES tracelab.jobs (id),
    usd               numeric(12,6) NOT NULL DEFAULT 0 CHECK (usd >= 0),
    usd_cap           numeric(12,4) CHECK (usd_cap IS NULL OR usd_cap >= 0),
    started_by        uuid,
    started_by_email  text,
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    error             text CHECK (error IS NULL OR length(error) <= 2000),
    CONSTRAINT ck_monitor_batches_window CHECK (window_end > window_start)
);
CREATE UNIQUE INDEX ux_monitor_batches_active ON tracelab.monitor_batches
    (COALESCE(organization_id, '00000000-0000-0000-0000-000000000000'::uuid)) WHERE status IN ('queued','running');
CREATE INDEX ix_monitor_batches_period ON tracelab.monitor_batches (organization_id, period_label, started_at DESC);
CREATE INDEX ix_monitor_batches_started ON tracelab.monitor_batches (started_at DESC);
COMMENT ON TABLE tracelab.monitor_batches IS 'One monitoring run over a window of chat answers. organization_id NULL means platform scope: every company, started by the platform team.';
COMMENT ON COLUMN tracelab.monitor_batches.job_id IS 'The monitor_batch job that spent for this batch. Evaluation spend is read from tracelab.jobs.usd_spent.';

CREATE TABLE tracelab.monitor_verdicts (
    id                uuid PRIMARY KEY,
    evaluator_key     text NOT NULL CHECK (evaluator_key ~ '^(judge:[0-9a-f-]{36}|code:[a-z][a-z0-9_]*@v[0-9]+)$'),
    turn_id           text NOT NULL CHECK (length(turn_id) BETWEEN 1 AND 200),
    organization_id   uuid NOT NULL,
    turn_started_at   timestamptz NOT NULL,
    judge_id          uuid REFERENCES tracelab.judges (id),
    mode_id           uuid REFERENCES tracelab.modes (id),
    status            text NOT NULL CHECK (status IN ('decided','error','undecidable','not_applicable')),
    failure_present   smallint CHECK (failure_present IN (0,1)),
    detail            jsonb,
    first_batch_id    uuid NOT NULL REFERENCES tracelab.monitor_batches (id),
    attempts          integer NOT NULL DEFAULT 1 CHECK (attempts >= 1),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_monitor_verdicts_evaluator_turn UNIQUE (evaluator_key, turn_id),
    CONSTRAINT ck_monitor_verdicts_decided CHECK ((status = 'decided') = (failure_present IS NOT NULL)),
    CONSTRAINT ck_monitor_verdicts_judge CHECK ((evaluator_key LIKE 'judge:%') = (judge_id IS NOT NULL))
);
CREATE INDEX ix_monitor_verdicts_week ON tracelab.monitor_verdicts (evaluator_key, turn_started_at);
CREATE INDEX ix_monitor_verdicts_turn ON tracelab.monitor_verdicts (turn_id);
CREATE INDEX ix_monitor_verdicts_org_time ON tracelab.monitor_verdicts (organization_id, updated_at);
COMMENT ON TABLE tracelab.monitor_verdicts IS 'One verdict per evaluator and answer, shared by overlapping batches. A decided verdict never changes.';
COMMENT ON COLUMN tracelab.monitor_verdicts.turn_id IS 'The trace turn id (agent_trace_turns.turn_id in the application database). No foreign key across databases: the turn is copied into evidence_turns when it is monitored.';
COMMENT ON COLUMN tracelab.monitor_verdicts.evaluator_key IS 'judge:<judge id> for a judge, code:<check key>@v<version> for a code check.';
COMMENT ON COLUMN tracelab.monitor_verdicts.failure_present IS 'The failure flag: 1 means the failure is present. Set exactly when the status is decided.';

CREATE FUNCTION tracelab.monitor_verdicts_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        IF coalesce(current_setting('tracelab.allow_purge', true), '') = 'on' THEN
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NULL;
        END IF;
        RAISE EXCEPTION 'monitor_verdicts: rows are removed only with company data (% refused)', TG_OP;
    END IF;
    IF OLD.status = 'decided' THEN
        RAISE EXCEPTION 'monitor_verdicts: a decided verdict is never changed (% / %)', OLD.evaluator_key, OLD.turn_id;
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER monitor_verdicts_guard BEFORE UPDATE OR DELETE ON tracelab.monitor_verdicts
    FOR EACH ROW EXECUTE FUNCTION tracelab.monitor_verdicts_guard();
CREATE TRIGGER monitor_verdicts_no_truncate BEFORE TRUNCATE ON tracelab.monitor_verdicts
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.monitor_verdicts_guard();
COMMENT ON FUNCTION tracelab.monitor_verdicts_guard() IS 'Guard for monitor verdicts. A decided verdict never changes. An error or undecidable one is retried in place. DELETE and TRUNCATE only after SET LOCAL tracelab.allow_purge = on (company offboarding).';

CREATE TABLE tracelab.eval_cases (
    id                     uuid PRIMARY KEY,
    organization_id        uuid NOT NULL,
    case_key               text NOT NULL CHECK (case_key ~ '^e-[0-9a-f]{8}$'),
    version                integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    status                 text NOT NULL CHECK (status IN ('proposed','draft','baselining','active','retired','superseded')),
    kind                   text NOT NULL DEFAULT 'unclassified' CHECK (kind IN ('unclassified','regression','capability')),
    baseline_pass_rate     numeric(3,2) CHECK (baseline_pass_rate IN (0.00, 0.20, 0.40, 0.60, 0.80)),
    baseline_run_id        uuid REFERENCES tracelab.golden_runs (id),
    mode_id                uuid REFERENCES tracelab.modes (id),
    source_type            text NOT NULL CHECK (source_type IN ('live_turn','monitor','golden_case')),
    source_turn_id         text CHECK (source_turn_id IS NULL OR length(source_turn_id) BETWEEN 1 AND 200),
    source_batch_id        uuid REFERENCES tracelab.monitor_batches (id),
    source_golden_case_id  uuid REFERENCES tracelab.golden_cases (id),
    golden_case_id         uuid REFERENCES tracelab.golden_cases (id),
    input                  jsonb NOT NULL,
    initial_state          jsonb NOT NULL DEFAULT '{}'::jsonb,
    expected               jsonb NOT NULL,
    supersedes             uuid REFERENCES tracelab.eval_cases (id),
    retired_reason         text CHECK (retired_reason IS NULL OR length(retired_reason) <= 500),
    rev                    integer NOT NULL DEFAULT 1 CHECK (rev >= 1),
    created_by             uuid,
    created_by_email       text,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_eval_cases_key_version UNIQUE (organization_id, case_key, version),
    CONSTRAINT ck_eval_cases_capability_rate CHECK ((kind = 'capability') = (baseline_pass_rate IS NOT NULL)),
    CONSTRAINT ck_eval_cases_active_classified CHECK (status <> 'active' OR kind <> 'unclassified'),
    CONSTRAINT ck_eval_cases_twin CHECK (golden_case_id IS NOT NULL OR status IN ('proposed','retired')),
    CONSTRAINT ck_eval_cases_golden_source CHECK ((source_type = 'golden_case') = (source_golden_case_id IS NOT NULL)),
    CONSTRAINT ck_eval_cases_turn_source CHECK (source_type = 'golden_case' OR source_turn_id IS NOT NULL),
    CONSTRAINT ck_eval_cases_monitor_source CHECK (source_type <> 'monitor' OR source_batch_id IS NOT NULL)
);
CREATE UNIQUE INDEX ux_eval_cases_current ON tracelab.eval_cases (organization_id, case_key)
    WHERE status <> 'superseded';
CREATE INDEX ix_eval_cases_org ON tracelab.eval_cases (organization_id, status, kind);
CREATE INDEX ix_eval_cases_source_turn ON tracelab.eval_cases (source_turn_id);
COMMENT ON TABLE tracelab.eval_cases IS 'A company eval case: a question with its expected outcome, made from a live answer, a monitoring batch or a golden case. A changed case is a new version.';
COMMENT ON COLUMN tracelab.eval_cases.golden_case_id IS 'The runnable twin in the company Regression cases golden set. NULL only while proposed or retired.';
COMMENT ON COLUMN tracelab.eval_cases.source_turn_id IS 'The trace turn id (agent_trace_turns.turn_id in the application database). No foreign key across databases: the turn is copied into evidence_turns when the case is proposed.';
COMMENT ON COLUMN tracelab.eval_cases.baseline_pass_rate IS 'Set for a capability case: the pass rate of its 5 baseline runs. NULL for a regression or unclassified case.';

CREATE TABLE tracelab.eval_case_runs (
    id              uuid PRIMARY KEY,
    eval_case_id    uuid NOT NULL REFERENCES tracelab.eval_cases (id),
    golden_run_id   uuid NOT NULL REFERENCES tracelab.golden_runs (id),
    purpose         text NOT NULL CHECK (purpose IN ('baseline','release_check','repeat')),
    k               integer NOT NULL CHECK (k BETWEEN 1 AND 20),
    n               integer NOT NULL DEFAULT 0 CHECK (n >= 0),
    passes          integer NOT NULL DEFAULT 0 CHECK (passes >= 0),
    degraded        integer NOT NULL DEFAULT 0 CHECK (degraded >= 0),
    pending         integer NOT NULL DEFAULT 0 CHECK (pending >= 0),
    reps            jsonb NOT NULL DEFAULT '[]'::jsonb,
    graders         jsonb NOT NULL DEFAULT '{}'::jsonb,
    pass_at_1       numeric(6,4),
    pass_at_3       numeric(6,4),
    pass_at_5       numeric(6,4),
    pass_hat_k      numeric(6,4),
    summary         jsonb,
    decision        text NOT NULL DEFAULT 'incomplete' CHECK (decision IN ('pass','block','incomplete')),
    reason          text NOT NULL DEFAULT 'running',
    computed_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_eval_case_runs_case_run UNIQUE (eval_case_id, golden_run_id),
    CONSTRAINT ck_eval_case_runs_counts CHECK (passes <= n)
);
CREATE INDEX ix_eval_case_runs_run ON tracelab.eval_case_runs (golden_run_id);
COMMENT ON TABLE tracelab.eval_case_runs IS 'One row per eval case per golden run. Company scope comes through the case.';
