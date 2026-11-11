-- 0014_eval_store.sql: the eval store. A scenario run kept the way Langfuse keeps a conversation: a SESSION (one
-- conversation, one scenario asked once), its TRACES (one per turn), each trace's OBSERVATIONS (the tree of spans and
-- generations, linked parent to child) and the SCORES people and evaluators give a trace at three levels (logic, ux,
-- domain). Beside them: the RUN TAG a batch is filed under (baseline_v0, then a tag per fix), the SCENARIOS with the
-- expected outcome each one was written with, the SECTIONS of the specification those outcomes were derived from, and
-- the log of pushes to another Trace Lab database.
--
-- Why new tables rather than the lab_* ones (11 Nov 2026 task plan, B1.3): the plan asks for Langfuse names in the
-- schema as well as on screen, and for local results to be copied to the development instance. The older tables are
-- numbered per database (run_no) and read their traces from the application database, so neither could be copied.
-- These hold everything a session needs to be shown, under keys that mean the same on every machine: a session id that
-- names its run tag and scenario, the turn id, the span id.
--
-- Append-only like every other table here: a correction is a new row (a new scenario version, a new score), so copying
-- is "insert what the other side does not have" and can never overwrite. Runs in Trace Lab OWN database
-- (hoist_tracelab), applied only by migrate.apply_pending. A correction to this file is a new numbered file.
SET LOCAL lock_timeout = '3s';

CREATE TABLE tracelab.run_tags (
    -- No double underscore: a session id is scn__<tag>__<scenario>__<attempt> and is read back by splitting on it.
    tag               text          PRIMARY KEY
                                    CHECK (tag ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$' AND tag !~ '__'),
    kind              text          NOT NULL CHECK (kind IN ('baseline', 'post_fix', 'adhoc')),
    spec_version      text          CHECK (spec_version IS NULL OR char_length(spec_version) <= 64),
    code_revision     text          CHECK (code_revision IS NULL OR char_length(code_revision) <= 64),
    -- The machine the tag was made on. A run copied in from another machine keeps saying where it was asked.
    origin            text          NOT NULL CHECK (char_length(origin) BETWEEN 1 AND 64),
    note              text          CHECK (note IS NULL OR char_length(note) <= 500),
    created_by        uuid,
    created_by_email  text          CHECK (created_by_email IS NULL OR char_length(created_by_email) <= 320),
    created_at        timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX ix_tl_run_tags_created ON tracelab.run_tags (created_at);

CREATE TRIGGER run_tags_append_only BEFORE UPDATE OR DELETE ON tracelab.run_tags
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();

CREATE TRIGGER run_tags_no_truncate BEFORE TRUNCATE ON tracelab.run_tags
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();

COMMENT ON TABLE tracelab.run_tags IS
    'The name a batch of scenario sessions is filed under, so two batches (baseline_v0 and the run after a fix) can be filtered and compared.';

CREATE TABLE tracelab.spec_sections (
    -- The first twelve hex of the sha256 of the whole specification, so two loads of the same text are one version.
    spec_version      text          NOT NULL CHECK (char_length(spec_version) BETWEEN 1 AND 64),
    section_id        text          NOT NULL CHECK (char_length(section_id) BETWEEN 1 AND 120),
    -- Where the section stands in the document, so the specification can be listed in its own order.
    position          integer       NOT NULL CHECK (position >= 1),
    title             text          NOT NULL CHECK (char_length(title) BETWEEN 1 AND 300),
    sha256            text          NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    body              text          NOT NULL,
    created_at        timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (spec_version, section_id)
);

CREATE INDEX ix_tl_spec_sections_created ON tracelab.spec_sections (created_at);

CREATE TRIGGER spec_sections_append_only BEFORE UPDATE OR DELETE ON tracelab.spec_sections
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();

CREATE TRIGGER spec_sections_no_truncate BEFORE TRUNCATE ON tracelab.spec_sections
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();

COMMENT ON TABLE tracelab.spec_sections IS
    'The specification as it was each time it was loaded, one row per section with the hash of its text. A scenario is out of date when a section it cites has a different hash now.';

CREATE TABLE tracelab.scenarios (
    scenario_id       text          NOT NULL
                                    CHECK (scenario_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$' AND scenario_id !~ '__'),
    -- A change to a scenario is the next version; the version a session was asked with stays readable for ever.
    version           integer       NOT NULL CHECK (version >= 1),
    -- The company whose records the scenario names. NULL for one that names none and may be asked anywhere.
    organization_id   uuid,
    type              text          NOT NULL
                                    CHECK (type IN ('single_domain', 'multi_domain', 'multi_turn', 'failure')),
    spec_sections     text[]        NOT NULL CHECK (cardinality(spec_sections) >= 1),
    role              text          CHECK (role IS NULL OR char_length(role) <= 64),
    space             text          CHECK (space IS NULL OR space IN ('ops', 'comp', 'assets', 'full')),
    -- Each turn is {message, expected}: what is typed, and the expected outcome written BEFORE it was asked.
    turns             jsonb         NOT NULL
                                    CHECK (jsonb_typeof(turns) = 'array' AND jsonb_array_length(turns) BETWEEN 1 AND 6),
    scope             jsonb         NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(scope) = 'object'),
    faults            jsonb         NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(faults) = 'array'),
    grounded_on       jsonb         NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(grounded_on) = 'array'),
    domain_rules      jsonb         NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(domain_rules) = 'array'),
    spec_version      text          CHECK (spec_version IS NULL OR char_length(spec_version) <= 64),
    -- {section id: sha256} of every cited section as it read when this version was written.
    spec_hashes       jsonb         NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(spec_hashes) = 'object'),
    content_sha256    text          NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    status            text          NOT NULL CHECK (status IN ('draft', 'approved', 'retired')),
    source            text          NOT NULL CHECK (source IN ('generator', 'import', 'manual')),
    created_by        uuid,
    created_by_email  text          CHECK (created_by_email IS NULL OR char_length(created_by_email) <= 320),
    created_at        timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (scenario_id, version)
);

CREATE INDEX ix_tl_scenarios_created ON tracelab.scenarios (created_at);
CREATE INDEX ix_tl_scenarios_type ON tracelab.scenarios (type, scenario_id);

CREATE TRIGGER scenarios_append_only BEFORE UPDATE OR DELETE ON tracelab.scenarios
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();

CREATE TRIGGER scenarios_no_truncate BEFORE TRUNCATE ON tracelab.scenarios
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();

COMMENT ON TABLE tracelab.scenarios IS
    'A scenario and the expected outcome it was written with, one row per version. The highest version of an id is the current one.';
COMMENT ON COLUMN tracelab.scenarios.turns IS
    'One entry per turn: the message typed and the expected outcome (plan, failure handling, answer format), written before the scenario was asked and never taken from an answer.';
COMMENT ON COLUMN tracelab.scenarios.spec_hashes IS
    'The hash of each cited section of the specification when this version was written. A different hash today means the expected outcome is out of date.';

CREATE TABLE tracelab.sessions (
    id                text          PRIMARY KEY CHECK (char_length(id) BETWEEN 1 AND 120),
    organization_id   uuid          NOT NULL,
    run_tag           text          REFERENCES tracelab.run_tags (tag),
    scenario_id       text,
    scenario_version  integer,
    -- A scenario asked again under the same run tag (after a timeout, say) is the next attempt; the highest wins.
    attempt           integer       NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    origin            text          NOT NULL CHECK (char_length(origin) BETWEEN 1 AND 64),
    -- runner: asked by Trace Lab own orchestrator. chat: asked through the chat and read back from its records.
    source            text          NOT NULL CHECK (source IN ('runner', 'chat')),
    user_id           uuid,
    email             text          CHECK (email IS NULL OR char_length(email) <= 320),
    role              text          CHECK (role IS NULL OR char_length(role) <= 64),
    space             text          CHECK (space IS NULL OR char_length(space) <= 64),
    started_at        timestamptz   NOT NULL,
    created_at        timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT ck_tl_sessions_scenario CHECK ((scenario_id IS NULL) = (scenario_version IS NULL)),
    CONSTRAINT fk_tl_sessions_scenario FOREIGN KEY (scenario_id, scenario_version)
        REFERENCES tracelab.scenarios (scenario_id, version)
);

CREATE INDEX ix_tl_sessions_created ON tracelab.sessions (created_at);
CREATE INDEX ix_tl_sessions_tag ON tracelab.sessions (run_tag, scenario_id, attempt DESC);
CREATE INDEX ix_tl_sessions_org ON tracelab.sessions (organization_id, started_at DESC);

CREATE TRIGGER sessions_append_only BEFORE UPDATE OR DELETE ON tracelab.sessions
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();

CREATE TRIGGER sessions_no_truncate BEFORE TRUNCATE ON tracelab.sessions
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();

COMMENT ON TABLE tracelab.sessions IS
    'One conversation: a scenario asked once, every turn in it. Written only when the whole conversation has been asked, so a session row means the scenario is finished for its run tag.';

CREATE TABLE tracelab.traces (
    -- The turn id the chat gave the turn. The same id in Langfuse is the sha256 of it, kept in header.
    id                text          PRIMARY KEY CHECK (char_length(id) BETWEEN 1 AND 200),
    session_id        text          NOT NULL REFERENCES tracelab.sessions (id),
    turn_index        integer       NOT NULL CHECK (turn_index >= 1),
    organization_id   uuid          NOT NULL,
    name              text          NOT NULL DEFAULT 'chat' CHECK (char_length(name) BETWEEN 1 AND 200),
    input             text,
    output            text,
    status            text          NOT NULL CHECK (status IN ('ok', 'error', 'timeout', 'interrupted')),
    -- The turn as the trace view heads it: times, cost, tokens, models, route.
    header            jsonb         NOT NULL CHECK (jsonb_typeof(header) = 'object'),
    metadata          jsonb         NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    started_at        timestamptz,
    ended_at          timestamptz,
    created_at        timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT ux_tl_traces_turn UNIQUE (session_id, turn_index)
);

CREATE INDEX ix_tl_traces_created ON tracelab.traces (created_at);

CREATE TRIGGER traces_append_only BEFORE UPDATE OR DELETE ON tracelab.traces
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();

CREATE TRIGGER traces_no_truncate BEFORE TRUNCATE ON tracelab.traces
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();

COMMENT ON TABLE tracelab.traces IS
    'One turn of a session: the question in, the answer out, and how it ended. Turn 1, Turn 2 and so on are its turn_index.';

CREATE TABLE tracelab.observations (
    trace_id               text          NOT NULL REFERENCES tracelab.traces (id),
    id                     text          NOT NULL CHECK (char_length(id) BETWEEN 1 AND 200),
    -- The step this one ran inside. NULL only for the root, which is the turn itself.
    parent_observation_id  text,
    seq                    integer,
    depth                  integer       NOT NULL CHECK (depth >= 0),
    type                   text          NOT NULL CHECK (type IN ('span', 'generation', 'agent', 'tool', 'event')),
    name                   text          NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
    agent                  text,
    model                  text,
    start_time             timestamptz,
    end_time               timestamptz,
    latency_ms             integer,
    level                  text          NOT NULL CHECK (level IN ('DEFAULT', 'WARNING', 'ERROR')),
    status_message         text,
    input                  jsonb,
    output                 jsonb,
    usage                  jsonb,
    cost_usd               numeric(12,6),
    metadata               jsonb         NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    created_at             timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (trace_id, id)
);

CREATE INDEX ix_tl_observations_created ON tracelab.observations (created_at);

CREATE TRIGGER observations_append_only BEFORE UPDATE OR DELETE ON tracelab.observations
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();

CREATE TRIGGER observations_no_truncate BEFORE TRUNCATE ON tracelab.observations
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();

COMMENT ON TABLE tracelab.observations IS
    'Every step of a trace as Langfuse names it: a generation is a model call, a tool is a tool call, an agent is a sub-agent, a span is any other step. Each row names the step it ran inside.';

CREATE TABLE tracelab.scores (
    id                uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id          text          NOT NULL REFERENCES tracelab.traces (id),
    session_id        text          NOT NULL REFERENCES tracelab.sessions (id),
    observation_id    text,
    -- The level: is the logic right, is the answer good to read, is it right for the domain.
    name              text          NOT NULL CHECK (name IN ('logic', 'ux', 'domain')),
    -- ANNOTATION: a person. EVAL: an automatic check or a model judge. A person newest score wins over an EVAL.
    source            text          NOT NULL CHECK (source IN ('ANNOTATION', 'EVAL')),
    string_value      text          NOT NULL CHECK (string_value IN ('pass', 'fail', 'not_evaluated')),
    value             numeric(4,3)  CHECK (value IS NULL OR value BETWEEN 0 AND 1),
    comment           text          CHECK (comment IS NULL OR char_length(comment) <= 4000),
    details           jsonb         NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
    evaluator         text          CHECK (evaluator IS NULL OR char_length(evaluator) <= 64),
    author_id         uuid,
    author_email      text          CHECK (author_email IS NULL OR char_length(author_email) <= 320),
    author_role       text          CHECK (author_role IS NULL OR char_length(author_role) <= 64),
    created_at        timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT ck_tl_scores_author CHECK ((source = 'ANNOTATION') = (author_id IS NOT NULL))
);

CREATE INDEX ix_tl_scores_created ON tracelab.scores (created_at);
CREATE INDEX ix_tl_scores_trace ON tracelab.scores (trace_id, name, created_at DESC);
CREATE INDEX ix_tl_scores_session ON tracelab.scores (session_id);

CREATE TRIGGER scores_append_only BEFORE UPDATE OR DELETE ON tracelab.scores
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();

CREATE TRIGGER scores_no_truncate BEFORE TRUNCATE ON tracelab.scores
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();

COMMENT ON TABLE tracelab.scores IS
    'A verdict on one trace at one level, by a person or by an evaluator. Never changed: a second opinion is a new row, and the newest one from a person is the current score.';

CREATE TABLE tracelab.sync_log (
    id                uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    -- host/database of the other Trace Lab database. Never its address with a login in it.
    peer              text          NOT NULL CHECK (char_length(peer) BETWEEN 1 AND 300),
    dry_run           boolean       NOT NULL DEFAULT false,
    status            text          NOT NULL CHECK (status IN ('done', 'failed', 'refused')),
    -- When the push started. The next push sends rows made after the newest watermark of a push that ended done.
    watermark         timestamptz,
    pushed            jsonb         NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(pushed) = 'object'),
    error             text          CHECK (error IS NULL OR length(error) <= 2000),
    started_by        uuid,
    started_by_email  text          CHECK (started_by_email IS NULL OR char_length(started_by_email) <= 320),
    started_at        timestamptz   NOT NULL,
    finished_at       timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX ix_tl_sync_log_finished ON tracelab.sync_log (finished_at DESC);

CREATE TRIGGER sync_log_append_only BEFORE UPDATE OR DELETE ON tracelab.sync_log
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();

CREATE TRIGGER sync_log_no_truncate BEFORE TRUNCATE ON tracelab.sync_log
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();

COMMENT ON TABLE tracelab.sync_log IS
    'Each push of this database eval rows to another Trace Lab database: what was sent, to where, and how it ended. Kept here only and never pushed itself.';
