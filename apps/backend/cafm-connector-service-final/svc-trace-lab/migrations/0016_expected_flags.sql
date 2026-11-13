-- 0016_expected_flags.sql: a reviewer flags a turn's expected outcome as wrong (12 Nov 2026).
--
-- Expected outcomes are checked while reviewing rather than before asking: a reviewer reading a turn next to its
-- expected outcome can say the expected outcome itself is wrong, so the scenario needs correcting, rather than the
-- answer. A flagged turn is never counted against Hoistra: it is not scored and compare leaves its scenario out, until
-- a corrected scenario version is asked.
--
-- Append-only like every other table here: a person's newest row is their flag (flagged true or false), a change is a
-- new row, and a push to the development instance copies it like the other eval rows. Runs in Trace Lab OWN database
-- (hoist_tracelab), applied only by migrate.apply_pending. A correction to this file is a new numbered file.
SET LOCAL lock_timeout = '3s';

CREATE TABLE tracelab.expected_flags (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id          text        NOT NULL REFERENCES tracelab.traces (id),
    organization_id   uuid        NOT NULL,
    flagged           boolean     NOT NULL,
    comment           text        CHECK (comment IS NULL OR char_length(comment) <= 2000),
    author_id         uuid,
    author_email      text        CHECK (author_email IS NULL OR char_length(author_email) <= 320),
    author_role       text        CHECK (author_role IS NULL OR char_length(author_role) <= 40),
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_tl_expected_flags_author CHECK (author_id IS NOT NULL OR author_email IS NOT NULL)
);

CREATE INDEX ix_tl_expected_flags_trace ON tracelab.expected_flags (trace_id, created_at DESC);

CREATE TRIGGER expected_flags_append_only BEFORE UPDATE OR DELETE ON tracelab.expected_flags
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();

CREATE TRIGGER expected_flags_no_truncate BEFORE TRUNCATE ON tracelab.expected_flags
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();

COMMENT ON TABLE tracelab.expected_flags IS
    'A reviewer saying the expected outcome of one turn is wrong (flagged true) or no longer (false). Never changed: the newest row per person is their flag.';
