-- 0008_step_labels.sql: step labelling in the standalone Trace Lab app. lab_members holds who may label for a company and
-- with which Trace Lab role (domain expert, tester, admin, user); step_labels holds one person's Right / Wrong / Not sure and
-- note on one step of one answer. Runs in Trace Lab's OWN database (hoist_tracelab), never in the application database: no
-- table here has a foreign key to a chat table. Both tables are versions (an edit is a new row, a removal a tombstone row)
-- under the 0003 guard tracelab.supersede_only(). Applied only by migrate.apply_pending. A correction is a new numbered file.
SET LOCAL lock_timeout = '3s';

CREATE TABLE tracelab.lab_members (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid        NOT NULL,
    user_id           uuid        NOT NULL,
    email             text        CHECK (email IS NULL OR char_length(email) <= 320),
    lab_role          text        NOT NULL CHECK (lab_role IN ('domain_expert','tester','admin','user')),
    status            text        NOT NULL CHECK (status IN ('pending','active','removed')),
    decided_by        uuid,
    decided_by_email  text,
    supersedes        uuid        REFERENCES tracelab.lab_members (id),
    superseded_by     uuid,
    superseded_at     timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_tl_members_supersedes UNIQUE (supersedes)
);
CREATE UNIQUE INDEX ux_tl_members_head ON tracelab.lab_members (organization_id, user_id)
    WHERE superseded_by IS NULL;
CREATE INDEX ix_tl_members_org ON tracelab.lab_members (organization_id, created_at);
CREATE TRIGGER lab_members_supersede_only BEFORE UPDATE OR DELETE ON tracelab.lab_members
    FOR EACH ROW EXECUTE FUNCTION tracelab.supersede_only();
CREATE TRIGGER lab_members_no_truncate BEFORE TRUNCATE ON tracelab.lab_members
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.supersede_only();
COMMENT ON TABLE tracelab.lab_members IS 'Who may label in the Trace Lab app for one company, with which Trace Lab role, as versions: a first visit is a pending row, a company admin decision is a new row.';

CREATE TABLE tracelab.step_labels (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid        NOT NULL,
    conversation_key  text        NOT NULL CHECK (char_length(conversation_key) BETWEEN 1 AND 400),
    turn_id           text        NOT NULL CHECK (char_length(turn_id) BETWEEN 1 AND 200),
    step_key          text        NOT NULL CHECK (char_length(step_key) BETWEEN 1 AND 300),
    step_kind         text        NOT NULL CHECK (char_length(step_kind) BETWEEN 1 AND 40),
    step_title        text        CHECK (step_title IS NULL OR char_length(step_title) <= 300),
    question          text        CHECK (question IS NULL OR char_length(question) <= 2000),
    label             text        CHECK (label IN ('right','wrong','unsure')),
    note              text        CHECK (note IS NULL OR char_length(note) <= 2000),
    tombstone         boolean     NOT NULL DEFAULT false,
    reviewer_id       uuid        NOT NULL,
    reviewer_email    text,
    reviewer_role     text        NOT NULL CHECK (reviewer_role IN ('domain_expert','tester','admin','user','platform')),
    supersedes        uuid        REFERENCES tracelab.step_labels (id),
    superseded_by     uuid,
    superseded_at     timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_tl_step_labels_supersedes UNIQUE (supersedes),
    CONSTRAINT ck_tl_step_labels_live CHECK (tombstone OR label IS NOT NULL)
);
CREATE UNIQUE INDEX ux_tl_step_labels_head ON tracelab.step_labels (organization_id, turn_id, step_key, reviewer_id)
    WHERE superseded_by IS NULL;
CREATE INDEX ix_tl_step_labels_turn ON tracelab.step_labels (organization_id, turn_id)
    WHERE superseded_by IS NULL;
CREATE INDEX ix_tl_step_labels_org_time ON tracelab.step_labels (organization_id, created_at DESC);
CREATE TRIGGER step_labels_supersede_only BEFORE UPDATE OR DELETE ON tracelab.step_labels
    FOR EACH ROW EXECUTE FUNCTION tracelab.supersede_only();
CREATE TRIGGER step_labels_no_truncate BEFORE TRUNCATE ON tracelab.step_labels
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.supersede_only();
COMMENT ON TABLE tracelab.step_labels IS 'One person label (right, wrong or unsure) and note on one step of one answer, as versions, with who and the Trace Lab role at the time are kept on every row.';
