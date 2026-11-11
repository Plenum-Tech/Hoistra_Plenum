-- 0015_trace_labels.sql: failure-mode labels on a session's traces (11 Nov 2026).
--
-- Runs are gone from Trace Lab's screens: a person reads a conversation on the session page and labels the turn in
-- front of them. Until now a failure-mode label could only sit on a run question (lab_label_cells.question_id), so a
-- session's trace could not carry one. This table holds the same thing keyed by the trace: whether a failure mode is
-- there (positive) or not (negative) in one trace, by one person, against the same failure modes the Labels page uses
-- (lab_failure_modes, by lineage). NULL value clears that person's value.
--
-- Append-only like every other table here: a person's newest row is their value, and a change is a new row. Not pushed
-- to the development instance yet: a label names its failure mode, and the modes are numbered per database and not
-- pushed. Runs in Trace Lab OWN database (hoist_tracelab), applied only by migrate.apply_pending. A correction to this
-- file is a new numbered file.
SET LOCAL lock_timeout = '3s';

CREATE TABLE tracelab.trace_label_cells (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id          text        NOT NULL REFERENCES tracelab.traces (id),
    organization_id   uuid        NOT NULL,
    mode_lineage_id   uuid        NOT NULL,
    value             text        CHECK (value IN ('positive','negative')),
    author_id         uuid,
    author_email      text        CHECK (author_email IS NULL OR char_length(author_email) <= 320),
    author_role       text        CHECK (author_role IS NULL OR char_length(author_role) <= 40),
    source            text        NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','coding_agent')),
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_tl_trace_label_cells_author CHECK (author_id IS NOT NULL OR author_email IS NOT NULL)
);

CREATE INDEX ix_tl_trace_label_cells_cell ON tracelab.trace_label_cells (trace_id, mode_lineage_id, created_at DESC);

CREATE TRIGGER trace_label_cells_append_only BEFORE UPDATE OR DELETE ON tracelab.trace_label_cells
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();

CREATE TRIGGER trace_label_cells_no_truncate BEFORE TRUNCATE ON tracelab.trace_label_cells
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();

COMMENT ON TABLE tracelab.trace_label_cells IS
    'Whether a failure mode is in one trace, by one person: positive, negative, or NULL to clear. Never changed: the newest row per person is their value.';
