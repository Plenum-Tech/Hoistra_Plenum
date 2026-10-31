-- 0004_taxonomy.sql (Trace Lab Phase 5): failure modes, their history, the requirement register, AI proposals and labels.
-- Runs in Trace Lab's OWN database (hoist_tracelab), never in the application database: no table here has a foreign key
-- to a chat table, a turn is named by id and its evidence is copied by snapshot when it is labelled or proposed for.
-- Applied only by migrate.apply_pending. Never edited once applied to a shared database (SHIPPED_CHECKSUMS in
-- trace_lab/migrate.py): a correction is a new numbered file.
SET LOCAL lock_timeout = '3s';

CREATE TABLE tracelab.modes (
    id                       uuid PRIMARY KEY,
    name                     text NOT NULL CHECK (name ~ '^[a-z][a-z0-9_]{2,63}$'),
    definition               text NOT NULL CHECK (char_length(definition) BETWEEN 10 AND 1000),
    boundary                 text NOT NULL DEFAULT '' CHECK (char_length(boundary) <= 1000),
    nearest_mode_id          uuid REFERENCES tracelab.modes(id),
    status                   text NOT NULL DEFAULT 'candidate'
                             CHECK (status IN ('proposed','candidate','confirmed','frozen','retired','rejected')),
    evaluator_type           text CHECK (evaluator_type IN ('code','judge')),
    level                    text NOT NULL CHECK (level IN ('technical','experience','domain','data')),
    agent                    text NOT NULL CHECK (agent IN ('orchestrator','wo_engine','compliance',
                             'contract_performance','energy_intelligence','udr','doc_rag','migration')),
    requirement_ids          text[] NOT NULL DEFAULT '{}',
    created_from             uuid[] NOT NULL CHECK (cardinality(created_from) >= 1),
    positive_turn_ids        text[] NOT NULL DEFAULT '{}',
    close_negative_turn_ids  text[] NOT NULL DEFAULT '{}',
    close_negatives_waiver   text CHECK (char_length(close_negatives_waiver) <= 500),
    merged_into              uuid REFERENCES tracelab.modes(id),
    split_from               uuid REFERENCES tracelab.modes(id),
    proposer_organization_id uuid,
    rejected_reason          text CHECK (char_length(rejected_reason) BETWEEN 1 AND 500),
    decided_by               uuid,
    decided_by_email         text,
    decided_at               timestamptz,
    rescoped_at              timestamptz NOT NULL DEFAULT now(),
    absorbed_at              timestamptz,
    confirmed_at             timestamptz,
    frozen_at                timestamptz,
    retired_at               timestamptz,
    version                  integer NOT NULL DEFAULT 1,
    created_by               uuid NOT NULL,
    created_by_email         text,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_tl_modes_merged_retired CHECK (merged_into IS NULL OR status = 'retired'),
    CONSTRAINT ck_tl_modes_not_self CHECK (merged_into IS DISTINCT FROM id AND split_from IS DISTINCT FROM id),
    CONSTRAINT ck_tl_modes_proposer CHECK (status <> 'proposed' OR proposer_organization_id IS NOT NULL),
    CONSTRAINT ck_tl_modes_rejected CHECK ((status = 'rejected') = (rejected_reason IS NOT NULL))
);
CREATE UNIQUE INDEX ux_tl_modes_live_name ON tracelab.modes (name) WHERE status NOT IN ('retired','rejected');
CREATE INDEX ix_tl_modes_merged_into ON tracelab.modes (merged_into) WHERE merged_into IS NOT NULL;
CREATE INDEX ix_tl_modes_split_from ON tracelab.modes (split_from) WHERE split_from IS NOT NULL;
CREATE INDEX ix_tl_modes_proposals ON tracelab.modes (proposer_organization_id, created_at) WHERE status = 'proposed';
COMMENT ON TABLE tracelab.modes IS 'Failure modes, platform wide. Only the platform team confirms one. A company admin proposes one and it waits as proposed until accepted or rejected with a reason.';
COMMENT ON COLUMN tracelab.modes.level IS 'technical, experience, domain or data. A data fault goes to the data owner and stays out of prompt fixes and judges.';
COMMENT ON COLUMN tracelab.modes.proposer_organization_id IS 'The company whose admin proposed this mode. NULL for a mode the platform team created. Kept after the mode is accepted.';
COMMENT ON COLUMN tracelab.modes.rejected_reason IS 'Why the platform team rejected a proposed mode. Set exactly when status is rejected. The row is kept, never deleted.';

CREATE TABLE tracelab.mode_events (
    seq          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mode_id      uuid NOT NULL REFERENCES tracelab.modes(id),
    action       text NOT NULL CHECK (action IN ('create','edit','rename','merge','split','retire','restore',
                 'propose','accept','reject')),
    detail       jsonb NOT NULL DEFAULT '{}'::jsonb,
    actor_id     uuid NOT NULL,
    actor_email  text,
    at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_tl_mode_events_mode ON tracelab.mode_events (mode_id, at);
CREATE TRIGGER mode_events_append_only BEFORE UPDATE OR DELETE ON tracelab.mode_events
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();
CREATE TRIGGER mode_events_no_truncate BEFORE TRUNCATE ON tracelab.mode_events
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();
COMMENT ON TABLE tracelab.mode_events IS 'Append-only who-did-what log of failure modes: create, edit, rename, merge, split, retire, restore, propose, accept and reject.';

CREATE TABLE tracelab.requirements (
    id                   uuid PRIMARY KEY,
    code                 text NOT NULL CHECK (code ~ '^(GEN|COMP|VEN|UDR|EN|WO|DOC)-[0-9]{1,3}$'),
    revision             integer NOT NULL CHECK (revision >= 1),
    statement            text NOT NULL CHECK (char_length(statement) BETWEEN 10 AND 600),
    area                 text NOT NULL CHECK (area IN ('GEN','COMP','VEN','UDR','EN','WO','DOC')),
    level                text NOT NULL CHECK (level IN ('technical','experience','domain','data')),
    status               text NOT NULL CHECK (status IN ('draft','active','superseded','withdrawn')),
    implementations      jsonb NOT NULL DEFAULT '[]'::jsonb,
    revision_of          uuid REFERENCES tracelab.requirements(id),
    motivating_turn_ids  text[] NOT NULL DEFAULT '{}',
    origin               text NOT NULL CHECK (origin IN ('seed','ui')),
    note                 text CHECK (char_length(note) <= 1000),
    created_by           uuid,
    created_by_email     text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_tl_req_code_revision UNIQUE (code, revision),
    CONSTRAINT ck_tl_req_area CHECK (split_part(code, '-', 1) = area)
);
CREATE UNIQUE INDEX ux_tl_req_active ON tracelab.requirements (code) WHERE status = 'active';

CREATE TABLE tracelab.proposal_runs (
    id                uuid PRIMARY KEY,
    job_id            uuid NOT NULL REFERENCES tracelab.jobs(id),
    kind              text NOT NULL CHECK (kind IN ('grouping','depth_scan','cells')),
    organization_id   uuid,
    mode_id           uuid REFERENCES tracelab.modes(id),
    model             text NOT NULL,
    prompt_version    text NOT NULL,
    prompt_hash       text NOT NULL,
    params            jsonb NOT NULL DEFAULT '{}'::jsonb,
    estimate_usd      numeric(10,4) NOT NULL,
    worst_usd         numeric(10,4) NOT NULL,
    items_planned     integer NOT NULL,
    canary_total      integer NOT NULL DEFAULT 0,
    canary_agree      integer NOT NULL DEFAULT 0,
    created_by        uuid NOT NULL,
    created_by_email  text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_tl_runs_depth_mode CHECK (kind <> 'depth_scan' OR mode_id IS NOT NULL)
);
CREATE INDEX ix_tl_runs_job ON tracelab.proposal_runs (job_id);

CREATE TABLE tracelab.proposals (
    id                uuid PRIMARY KEY,
    run_id            uuid NOT NULL REFERENCES tracelab.proposal_runs(id),
    kind              text NOT NULL CHECK (kind IN ('group','depth_scan','cell')),
    cache_key         text NOT NULL,
    organization_id   uuid,
    turn_id           text,
    mode_id           uuid REFERENCES tracelab.modes(id),
    suggestion        text NOT NULL CHECK (suggestion IN ('present','absent','unsure',
                      'file_under','new_mode','no_mode_fits')),
    annotation_ids    uuid[] NOT NULL DEFAULT '{}',
    draft             jsonb,
    quote             text CHECK (char_length(quote) <= 2000),
    span_id           uuid,
    field             text CHECK (field IN ('question','answer','input','output')),
    basis             text,
    evidence          text CHECK (char_length(evidence) <= 600),
    status            text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','dismissed','closed')),
    closed_reason     text CHECK (closed_reason IN ('human_decision_exists','mode_changed','superseded','unsure',
                                                    'quote_not_verbatim','canary','turn_unavailable')),
    is_canary         boolean NOT NULL DEFAULT false,
    canary_label_id   uuid,
    flags             jsonb NOT NULL DEFAULT '{}'::jsonb,
    usd               numeric(10,6),
    decided_by        uuid,
    decided_by_email  text,
    decided_at        timestamptz,
    label_id          uuid,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_tl_prop_run_key UNIQUE (run_id, cache_key),
    CONSTRAINT ck_tl_prop_cell_target CHECK (kind = 'group'
        OR (turn_id IS NOT NULL AND organization_id IS NOT NULL AND mode_id IS NOT NULL)),
    CONSTRAINT ck_tl_prop_group_notes CHECK (kind <> 'group' OR cardinality(annotation_ids) >= 1),
    CONSTRAINT ck_tl_prop_closed_reason CHECK ((status = 'closed') = (closed_reason IS NOT NULL)),
    CONSTRAINT ck_tl_prop_org_turn CHECK ((organization_id IS NULL) = (turn_id IS NULL))
);
CREATE UNIQUE INDEX ux_tl_prop_pending_cell ON tracelab.proposals (kind, turn_id, mode_id)
    WHERE status = 'pending' AND kind IN ('depth_scan','cell');
CREATE INDEX ix_tl_prop_queue ON tracelab.proposals (kind, status, mode_id, created_at);
CREATE INDEX ix_tl_prop_org_queue ON tracelab.proposals (organization_id, kind, status, created_at);
CREATE INDEX ix_tl_prop_cache ON tracelab.proposals (cache_key);
CREATE INDEX ix_tl_prop_turn ON tracelab.proposals (turn_id) WHERE turn_id IS NOT NULL;
CREATE INDEX ix_tl_prop_run ON tracelab.proposals (run_id, status);
COMMENT ON COLUMN tracelab.proposals.turn_id IS 'The trace turn id (agent_trace_turns.turn_id in the application database). No foreign key across databases: the turn is copied into evidence_turns when it is proposed for.';

CREATE TABLE tracelab.labels (
    id                uuid PRIMARY KEY,
    organization_id   uuid NOT NULL,
    turn_id           text NOT NULL,
    session_id        text,
    mode_id           uuid NOT NULL REFERENCES tracelab.modes(id),
    present           boolean,
    tombstone         boolean NOT NULL DEFAULT false,
    source            text NOT NULL CHECK (source IN ('human_structured_label','accepted_suggestion',
                      'rejected_suggestion','accepted_label_proposal','human_open_code')),
    note              text CHECK (char_length(note) <= 2000),
    quote             text CHECK (char_length(quote) <= 2000),
    span_id           uuid,
    span_name         text,
    span_kind         text,
    span_agent        text,
    proposal_id       uuid REFERENCES tracelab.proposals(id),
    annotation_id     uuid REFERENCES tracelab.annotations(id),
    approval          text NOT NULL DEFAULT 'single' CHECK (approval IN ('single','bulk')),
    basis             jsonb NOT NULL DEFAULT '{}'::jsonb,
    decided_by        uuid NOT NULL,
    decided_by_email  text,
    supersedes        uuid REFERENCES tracelab.labels(id),
    superseded_by     uuid,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_tl_labels_supersedes UNIQUE (supersedes),
    CONSTRAINT ck_tl_labels_value CHECK (tombstone OR present IS NOT NULL),
    CONSTRAINT ck_tl_labels_tombstone CHECK (NOT tombstone OR (present IS NULL AND source = 'human_structured_label')),
    CONSTRAINT ck_tl_labels_open_code CHECK (source <> 'human_open_code' OR (annotation_id IS NOT NULL AND present)),
    CONSTRAINT ck_tl_labels_from_proposal CHECK (source NOT IN ('accepted_suggestion','rejected_suggestion',
                                                 'accepted_label_proposal') OR proposal_id IS NOT NULL),
    CONSTRAINT ck_tl_labels_accepted CHECK (source <> 'accepted_suggestion' OR present),
    CONSTRAINT ck_tl_labels_rejected CHECK (source <> 'rejected_suggestion' OR NOT present),
    CONSTRAINT ck_tl_labels_bulk CHECK (approval = 'single'
        OR source IN ('accepted_suggestion','rejected_suggestion','accepted_label_proposal'))
);
CREATE UNIQUE INDEX ux_tl_labels_head ON tracelab.labels (turn_id, mode_id) WHERE superseded_by IS NULL;
CREATE INDEX ix_tl_labels_turn ON tracelab.labels (turn_id);
CREATE INDEX ix_tl_labels_org_mode_head ON tracelab.labels (organization_id, mode_id)
    WHERE superseded_by IS NULL;
CREATE INDEX ix_tl_labels_mode_head ON tracelab.labels (mode_id) WHERE superseded_by IS NULL;
CREATE INDEX ix_tl_labels_annotation ON tracelab.labels (annotation_id) WHERE annotation_id IS NOT NULL;
CREATE TRIGGER labels_supersede_only BEFORE UPDATE OR DELETE ON tracelab.labels
    FOR EACH ROW EXECUTE FUNCTION tracelab.supersede_only();
CREATE TRIGGER labels_no_truncate BEFORE TRUNCATE ON tracelab.labels
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.supersede_only();
COMMENT ON TABLE tracelab.labels IS 'A reviewer decision that a failure mode is present or absent on a turn, as versions: a change is a new row and a clear is a tombstone row.';
COMMENT ON COLUMN tracelab.labels.turn_id IS 'The trace turn id (agent_trace_turns.turn_id in the application database). No foreign key across databases: the turn is copied into evidence_turns when it is labelled.';
COMMENT ON COLUMN tracelab.labels.approval IS 'single = decided one at a time, bulk = approved through the audited bulk path and never used in a judge dev or test split.';
