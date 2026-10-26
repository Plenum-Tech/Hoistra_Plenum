-- 0002_golden.sql (Trace Lab Phase 3): golden sets, their cases, golden runs, their results, and reveals.
-- Runs in Trace Lab's OWN database (hoist_tracelab), never in the application database: golden answers
-- are stored here only. Applied only by migrate.apply_pending. Never edited once applied to a shared
-- database (SHIPPED_CHECKSUMS in trace_lab/migrate.py): a correction is a new numbered file.
SET LOCAL lock_timeout = '3s';

CREATE TABLE tracelab.golden_sets (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid        NOT NULL,
    name              text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    kind              text        NOT NULL DEFAULT 'scenario' CHECK (kind IN ('scenario','regression')),
    version           integer     NOT NULL CHECK (version >= 0),
    status            text        NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
    source_filename   text        CHECK (source_filename IS NULL OR length(source_filename) BETWEEN 1 AND 255),
    source_sha256     text        CHECK (source_sha256 IS NULL OR source_sha256 ~ '^[0-9a-f]{64}$'),
    source_kind       text        CHECK (source_kind IS NULL OR source_kind IN ('xlsx','csv')),
    sheet_name        text        CHECK (sheet_name IS NULL OR length(sheet_name) <= 200),
    set_hash          text        NOT NULL CHECK (set_hash ~ '^[0-9a-f]{12}$'),
    header_row        integer     NOT NULL DEFAULT 0 CHECK (header_row >= 0),
    case_count        integer     NOT NULL DEFAULT 0 CHECK (case_count BETWEEN 0 AND 2000),
    warnings          jsonb       NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(warnings) = 'array'),
    diff              jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(diff) = 'object'),
    note              text        CHECK (note IS NULL OR length(note) <= 500),
    created_by        text        NOT NULL CHECK (length(created_by) BETWEEN 1 AND 200),
    created_by_email  text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    retired_at        timestamptz,
    UNIQUE (organization_id, kind, version),
    CONSTRAINT golden_sets_kind_shape CHECK (
        (kind = 'regression') = (version = 0)
        AND (kind = 'regression' OR (source_filename IS NOT NULL AND source_sha256 IS NOT NULL AND case_count >= 1)))
);
CREATE UNIQUE INDEX ux_golden_sets_one_active_scenario ON tracelab.golden_sets (organization_id)
    WHERE kind = 'scenario' AND status = 'active';
CREATE UNIQUE INDEX ux_golden_sets_one_regression ON tracelab.golden_sets (organization_id) WHERE kind = 'regression';
COMMENT ON TABLE tracelab.golden_sets IS 'A company list of questions with known right answers. Each upload is a new version, and the previous active version is retired.';
COMMENT ON COLUMN tracelab.golden_sets.kind IS 'scenario = the uploaded sets (exactly one active per company). regression = the one Regression cases set Phase 7 creates on demand (version 0), never the default.';
COMMENT ON COLUMN tracelab.golden_sets.created_by IS 'The user id of the admin who uploaded it (as text), or system for a set created by Trace Lab itself.';

CREATE TABLE tracelab.golden_cases (
    id               uuid     PRIMARY KEY DEFAULT gen_random_uuid(),
    set_id           uuid     NOT NULL REFERENCES tracelab.golden_sets (id),
    organization_id  uuid     NOT NULL,
    case_key         text     NOT NULL CHECK (case_key ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
    key_source       text     NOT NULL DEFAULT 'id_column' CHECK (key_source IN ('id_column','hash')),
    case_version     text     NOT NULL CHECK (case_version ~ '^[0-9a-f]{12}$'),
    idx              integer  NOT NULL CHECK (idx >= 0),
    row_no           integer  CHECK (row_no IS NULL OR row_no >= 1),
    question         text     NOT NULL CHECK (length(question) BETWEEN 1 AND 2000),
    followups        jsonb    NOT NULL DEFAULT '[]'::jsonb
                              CHECK (CASE WHEN jsonb_typeof(followups) = 'array' THEN jsonb_array_length(followups) <= 5 ELSE false END),
    expected_answer  text     NOT NULL CHECK (length(expected_answer) BETWEEN 1 AND 6000),
    evaluation       text     NOT NULL CHECK (evaluation IN ('objective','judgment')),
    expected_values  jsonb    NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(expected_values) = 'array'),
    source           text     CHECK (source IS NULL OR length(source) <= 500),
    verified_on      date,
    page_context     text     CHECK (page_context IS NULL OR length(page_context) <= 4000),
    ask_as           text     NOT NULL DEFAULT 'company' CHECK (ask_as IN ('company')),
    requirement_ids  text[]   NOT NULL DEFAULT '{}' CHECK (cardinality(requirement_ids) <= 20),
    notes            text     CHECK (notes IS NULL OR length(notes) <= 2000),
    stale            boolean  NOT NULL DEFAULT false,
    flags            text[]   NOT NULL DEFAULT '{}',
    UNIQUE (set_id, case_key),
    UNIQUE (set_id, idx)
);
CREATE INDEX ix_golden_cases_org_key ON tracelab.golden_cases (organization_id, case_key);
CREATE TRIGGER golden_cases_rows BEFORE UPDATE OR DELETE ON tracelab.golden_cases
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();
CREATE TRIGGER golden_cases_truncate BEFORE TRUNCATE ON tracelab.golden_cases
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();
COMMENT ON TABLE tracelab.golden_cases IS 'The cases of one golden set version, never edited: a changed case is a new version of the set.';
COMMENT ON COLUMN tracelab.golden_cases.page_context IS 'NULL = ask with the run default page context, and an empty string = ask with no page context.';
COMMENT ON COLUMN tracelab.golden_cases.stale IS 'Verified on was older than the stale window when the set was uploaded. The runner checks again at run time.';
COMMENT ON COLUMN tracelab.golden_cases.flags IS 'Reader flags such as write_intent, unverified and evaluation_defaulted.';

CREATE TABLE tracelab.golden_runs (
    id                uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid          NOT NULL,
    set_id            uuid          NOT NULL REFERENCES tracelab.golden_sets (id),
    label             text          NOT NULL UNIQUE CHECK (length(label) BETWEEN 1 AND 120),
    purpose           text          NOT NULL DEFAULT 'adhoc' CHECK (purpose IN ('adhoc','baseline','release_check','repeat')),
    k                 integer       NOT NULL DEFAULT 1 CHECK (k BETWEEN 1 AND 20),
    status            text          NOT NULL DEFAULT 'queued'
                                    CHECK (status IN ('queued','running','paused','done','failed','capped','interrupted','cancelled')),
    job_id            uuid          REFERENCES tracelab.jobs (id),
    started_by        text          NOT NULL CHECK (length(started_by) BETWEEN 1 AND 200),
    started_by_email  text,
    started_at        timestamptz   NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    params            jsonb         NOT NULL DEFAULT '{}'::jsonb,
    estimate_usd      numeric(12,6) CHECK (estimate_usd IS NULL OR estimate_usd >= 0),
    usd_cap           numeric(12,6) CHECK (usd_cap IS NULL OR usd_cap >= 0),
    usd_spent         numeric(12,6) NOT NULL DEFAULT 0 CHECK (usd_spent >= 0),
    summary           jsonb         NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(summary) = 'object'),
    CONSTRAINT golden_runs_params_object CHECK (jsonb_typeof(params) = 'object'),
    CONSTRAINT golden_runs_params_no_secrets CHECK (NOT (params ?| ARRAY['authorization','bearer','token','access_token','refresh_token','api_key','password']))
);
CREATE INDEX ix_golden_runs_org_started ON tracelab.golden_runs (organization_id, started_at DESC);
COMMENT ON TABLE tracelab.golden_runs IS 'One golden run: every case of a set asked k times through the chat in the Trace Lab process, stepped by the browser.';
COMMENT ON COLUMN tracelab.golden_runs.params IS 'The like-for-like record: chat model, flags, build, default page context and case list. Never a bearer token or other credential.';
COMMENT ON COLUMN tracelab.golden_runs.status IS 'The same statuses as tracelab.jobs.';

CREATE TABLE tracelab.golden_results (
    id               uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id           uuid          NOT NULL REFERENCES tracelab.golden_runs (id),
    organization_id  uuid          NOT NULL,
    case_id          uuid          NOT NULL REFERENCES tracelab.golden_cases (id),
    case_key         text          NOT NULL,
    case_version     text          NOT NULL,
    rep              integer       NOT NULL CHECK (rep BETWEEN 1 AND 20),
    msg_idx          integer       NOT NULL DEFAULT 0 CHECK (msg_idx BETWEEN 0 AND 5),
    session_id       text          NOT NULL CHECK (length(session_id) BETWEEN 1 AND 200),
    turn_id          text          CHECK (turn_id IS NULL OR length(turn_id) BETWEEN 1 AND 200),
    question         text          NOT NULL,
    answer           text,
    outcome          text          NOT NULL DEFAULT 'pending'
                                   CHECK (outcome IN ('pending','pass','fail','unsure','degraded','stale_reference','error')),
    grade            jsonb         CHECK (grade IS NULL OR jsonb_typeof(grade) = 'object'),
    refused          jsonb         NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(refused) = 'array'),
    usd              numeric(12,6) CHECK (usd IS NULL OR usd >= 0),
    ms               integer       CHECK (ms IS NULL OR ms >= 0),
    error            text          CHECK (error IS NULL OR length(error) <= 2000),
    graded_at        timestamptz,
    created_at       timestamptz   NOT NULL DEFAULT now(),
    UNIQUE (run_id, case_key, rep, msg_idx)
);
CREATE INDEX ix_golden_results_turn ON tracelab.golden_results (turn_id);
CREATE INDEX ix_golden_results_org_created ON tracelab.golden_results (organization_id, created_at);
CREATE INDEX ix_golden_results_graded ON tracelab.golden_results (graded_at) WHERE graded_at IS NOT NULL;
COMMENT ON TABLE tracelab.golden_results IS 'One row per message of a case rep. The grade and the outcome that counts sit on the last message of the rep.';
COMMENT ON COLUMN tracelab.golden_results.outcome IS 'pending until graded. A refused request makes it degraded, never fail. error means the chat could not be asked.';
COMMENT ON COLUMN tracelab.golden_results.grade IS 'The grader record: verdict, critique, mismatches, value check, grader version, model, usd and reading.';
COMMENT ON COLUMN tracelab.golden_results.refused IS 'Requests the Trace Lab proxy refused while this message was answered.';

CREATE TABLE tracelab.golden_reveals (
    organization_id  uuid        NOT NULL,
    turn_id          text        NOT NULL CHECK (length(turn_id) BETWEEN 1 AND 200),
    reviewer_id      text        NOT NULL CHECK (length(reviewer_id) BETWEEN 1 AND 200),
    reviewer_email   text,
    revealed_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (turn_id, reviewer_id)
);
CREATE INDEX ix_golden_reveals_org ON tracelab.golden_reveals (organization_id, revealed_at DESC);
CREATE TRIGGER golden_reveals_rows BEFORE UPDATE OR DELETE ON tracelab.golden_reveals
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();
CREATE TRIGGER golden_reveals_truncate BEFORE TRUNCATE ON tracelab.golden_reveals
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();
COMMENT ON TABLE tracelab.golden_reveals IS 'A reviewer chose to see the expected outcome of a golden answer. Until then it stays closed for that reviewer.';
