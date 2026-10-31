-- 0003_review.sql (Trace Lab Phase 4): open coding (annotations, annotation_events) and sampling (sample_batches, sample_items).
-- Runs in Trace Lab's OWN database (hoist_tracelab), never in the application database: reviewer notes and the
-- batches drawn for review are stored here only. Applied only by migrate.apply_pending. Never edited once applied to
-- a shared database (SHIPPED_CHECKSUMS in trace_lab/migrate.py): a correction is a new numbered file.
SET LOCAL lock_timeout = '3s';

CREATE FUNCTION tracelab.supersede_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.superseded_by IS NULL AND NEW.superseded_by IS NOT NULL
           AND (to_jsonb(NEW) - 'superseded_by' - 'superseded_at') = (to_jsonb(OLD) - 'superseded_by' - 'superseded_at') THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'tracelab.% rows are never edited: only superseded_by and superseded_at may be set, once', TG_TABLE_NAME;
    END IF;
    IF coalesce(current_setting('tracelab.allow_purge', true), '') = 'on' THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'tracelab.% rows are never deleted: % refused', TG_TABLE_NAME, TG_OP;
END
$$;
COMMENT ON FUNCTION tracelab.supersede_only() IS 'Guard for versioned rows. UPDATE only sets superseded_by (and superseded_at) once and changes nothing else. DELETE and TRUNCATE only after SET LOCAL tracelab.allow_purge = on (company offboarding).';

CREATE TABLE tracelab.sample_batches (
    id                     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id        uuid        NOT NULL,
    plan_step              text        NOT NULL CHECK (plan_step IN ('initial_uniform','cluster_representatives','dimension','depth_search','final_uniform','extra')),
    strategy               text        NOT NULL CHECK (strategy IN ('uniform','diversity','dimension','outlier','enrich','monitor_flagged')),
    params                 jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(params) = 'object'),
    seed                   bigint      NOT NULL,
    window_since           timestamptz NOT NULL,
    window_until           timestamptz NOT NULL,
    channel                text        NOT NULL DEFAULT 'chat',
    requested              integer     NOT NULL CHECK (requested BETWEEN 1 AND 200),
    selected               integer     NOT NULL CHECK (selected >= 0),
    pool_size              integer     NOT NULL CHECK (pool_size >= 0),
    excluded_sampled       integer     NOT NULL DEFAULT 0 CHECK (excluded_sampled >= 0),
    counts_for_prevalence  boolean     NOT NULL,
    reviews_required       smallint    NOT NULL DEFAULT 1 CHECK (reviews_required BETWEEN 1 AND 3),
    feature_version        text,
    created_by             uuid        NOT NULL,
    created_by_email       text,
    created_at             timestamptz NOT NULL DEFAULT now(),
    withdrawn_at           timestamptz,
    withdrawn_by           uuid,
    withdrawn_reason       text        CHECK (withdrawn_reason IS NULL OR char_length(withdrawn_reason) <= 500),
    CONSTRAINT ck_tl_batches_prevalence CHECK (counts_for_prevalence = (strategy = 'uniform')),
    CONSTRAINT ck_tl_batches_window CHECK (window_until > window_since)
);
CREATE INDEX ix_tl_batches_org_time ON tracelab.sample_batches (organization_id, created_at DESC);
COMMENT ON TABLE tracelab.sample_batches IS 'One drawn review batch: strategy, seed, parameters and window. Only the withdrawn columns are ever updated, once.';
COMMENT ON COLUMN tracelab.sample_batches.counts_for_prevalence IS 'True exactly for uniform batches. Rates from any other batch are sample fractions.';

CREATE TABLE tracelab.sample_items (
    id               uuid             PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id         uuid             NOT NULL REFERENCES tracelab.sample_batches (id),
    organization_id  uuid             NOT NULL,
    turn_id          text             NOT NULL CHECK (length(turn_id) BETWEEN 1 AND 200),
    session_id       text,
    position         integer          NOT NULL CHECK (position >= 1),
    reason           text             NOT NULL CHECK (char_length(reason) <= 300),
    cluster          integer,
    dimension_value  text,
    score            double precision,
    features         jsonb,
    created_at       timestamptz      NOT NULL DEFAULT now(),
    CONSTRAINT ux_tl_items_turn UNIQUE (organization_id, turn_id),
    CONSTRAINT ux_tl_items_position UNIQUE (batch_id, position)
);
CREATE INDEX ix_tl_items_turn_only ON tracelab.sample_items (turn_id);
CREATE TRIGGER sample_items_append_only BEFORE UPDATE OR DELETE ON tracelab.sample_items
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();
CREATE TRIGGER sample_items_no_truncate BEFORE TRUNCATE ON tracelab.sample_items
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();
COMMENT ON TABLE tracelab.sample_items IS 'The turns drawn into a batch, never edited. A turn is drawn once per company.';
COMMENT ON COLUMN tracelab.sample_items.turn_id IS 'The trace turn id (agent_trace_turns.turn_id in the application database). No foreign key across databases: the turn is copied into evidence_turns when it is drawn.';

CREATE TABLE tracelab.annotations (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    lineage_id        uuid        NOT NULL,
    version           integer     NOT NULL CHECK (version >= 1),
    supersedes        uuid        REFERENCES tracelab.annotations (id),
    superseded_by     uuid,
    superseded_at     timestamptz,
    kind              text        NOT NULL CHECK (kind IN ('verdict','comment')),
    organization_id   uuid        NOT NULL,
    conversation_key  text        NOT NULL,
    turn_id           text        CHECK (turn_id IS NULL OR length(turn_id) BETWEEN 1 AND 200),
    reviewer_id       uuid        NOT NULL,
    reviewer_email    text,
    tombstone         boolean     NOT NULL DEFAULT false,
    verdict           text        CHECK (verdict IN ('right','wrong')),
    note              text        CHECK (char_length(note) <= 2000),
    span_id           uuid,
    field             text        CHECK (field IN ('question','answer','input','output')),
    quote             text        CHECK (char_length(quote) <= 2000),
    quote_occurrence  integer     CHECK (quote_occurrence >= 1),
    quote_start       integer     CHECK (quote_start >= 0),
    span_name         text,
    span_kind         text,
    span_agent        text,
    blind             boolean,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_tl_ann_supersedes UNIQUE (supersedes),
    CONSTRAINT ck_tl_ann_verdict_turn CHECK (kind <> 'verdict' OR turn_id IS NOT NULL),
    CONSTRAINT ck_tl_ann_live_verdict CHECK (tombstone OR kind <> 'verdict' OR verdict IS NOT NULL),
    CONSTRAINT ck_tl_ann_wrong CHECK (tombstone OR verdict IS DISTINCT FROM 'wrong'
        OR (btrim(coalesce(note, '')) <> '' AND quote IS NOT NULL AND field IS NOT NULL
            AND quote_occurrence IS NOT NULL)),
    CONSTRAINT ck_tl_ann_right CHECK (verdict IS DISTINCT FROM 'right'
        OR (quote IS NULL AND span_id IS NULL AND field IS NULL)),
    CONSTRAINT ck_tl_ann_comment CHECK (kind <> 'comment' OR verdict IS NULL),
    CONSTRAINT ck_tl_ann_comment_note CHECK (tombstone OR kind <> 'comment' OR btrim(coalesce(note, '')) <> ''),
    CONSTRAINT ck_tl_ann_span_field CHECK (span_id IS NULL OR field IN ('input','output')),
    CONSTRAINT ck_tl_ann_turn_field CHECK (span_id IS NOT NULL OR field IS NULL OR field IN ('question','answer'))
);
CREATE UNIQUE INDEX ux_tl_ann_verdict_head ON tracelab.annotations (turn_id, reviewer_id)
    WHERE kind = 'verdict' AND superseded_by IS NULL;
CREATE UNIQUE INDEX ux_tl_ann_lineage_head ON tracelab.annotations (lineage_id)
    WHERE superseded_by IS NULL;
CREATE INDEX ix_tl_ann_turn ON tracelab.annotations (turn_id);
CREATE INDEX ix_tl_ann_conv_head ON tracelab.annotations (organization_id, conversation_key)
    WHERE superseded_by IS NULL;
CREATE INDEX ix_tl_ann_org_time ON tracelab.annotations (organization_id, created_at);
CREATE TRIGGER annotations_supersede_only BEFORE UPDATE OR DELETE ON tracelab.annotations
    FOR EACH ROW EXECUTE FUNCTION tracelab.supersede_only();
CREATE TRIGGER annotations_no_truncate BEFORE TRUNCATE ON tracelab.annotations
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.supersede_only();
COMMENT ON TABLE tracelab.annotations IS 'Reviewer verdicts and conversation comments as versions: an edit is a new row, a delete is a tombstone row.';
COMMENT ON COLUMN tracelab.annotations.turn_id IS 'The trace turn id (agent_trace_turns.turn_id in the application database). No foreign key across databases: the turn is copied into evidence_turns when it is annotated.';
COMMENT ON COLUMN tracelab.annotations.quote IS 'Verbatim text of the redacted block. span_name, span_kind and span_agent are copied so the note outlives the span purge.';
COMMENT ON COLUMN tracelab.annotations.blind IS 'True when the reviewer could not see other verdicts on the turn when saving. Agreement uses version 1 rows with blind true.';

CREATE TABLE tracelab.annotation_events (
    seq               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id   uuid        NOT NULL,
    conversation_key  text,
    turn_id           text        CHECK (turn_id IS NULL OR length(turn_id) BETWEEN 1 AND 200),
    lineage_id        uuid,
    annotation_id     uuid,
    action            text        NOT NULL CHECK (action IN ('create','edit','delete','reveal','dispose','undispose')),
    detail            jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(detail) = 'object'),
    actor_id          uuid        NOT NULL,
    actor_email       text,
    at                timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ix_tl_ann_events_turn ON tracelab.annotation_events (turn_id, at);
CREATE INDEX ix_tl_ann_events_lineage ON tracelab.annotation_events (lineage_id, at);
CREATE TRIGGER annotation_events_append_only BEFORE UPDATE OR DELETE ON tracelab.annotation_events
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();
CREATE TRIGGER annotation_events_no_truncate BEFORE TRUNCATE ON tracelab.annotation_events
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();
COMMENT ON TABLE tracelab.annotation_events IS 'Append-only who-did-what log of annotations: create, edit, delete, reveal, dispose and undispose.';
