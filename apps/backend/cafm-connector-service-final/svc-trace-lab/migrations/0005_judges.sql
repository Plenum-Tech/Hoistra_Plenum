-- 0005_judges.sql (Trace Lab Phase 6): judge splits and their members, judge versions, the prediction cache and judge runs.
-- Runs in Trace Lab's OWN database (hoist_tracelab), never in the application database: no table here has a foreign key
-- to a chat table, a turn is named by id and its evidence is copied by snapshot when it is put in a split or judged.
-- Applied only by migrate.apply_pending. Never edited once applied to a shared database (SHIPPED_CHECKSUMS in
-- trace_lab/migrate.py): a correction is a new numbered file.
SET LOCAL lock_timeout = '3s';

CREATE TABLE tracelab.judge_splits (
    id                uuid PRIMARY KEY,
    mode_id           uuid NOT NULL REFERENCES tracelab.modes(id),
    version           integer NOT NULL CHECK (version >= 1),
    status            text NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
    fractions         numeric(4,3)[] NOT NULL CHECK (cardinality(fractions) = 3),
    seed              integer NOT NULL,
    min_per_class     integer NOT NULL CHECK (min_per_class >= 10),
    min_labels        integer NOT NULL CHECK (min_labels >= 1),
    override          jsonb,
    excluded          jsonb NOT NULL DEFAULT '[]'::jsonb,
    counts            jsonb NOT NULL,
    labels_sha        text NOT NULL CHECK (labels_sha ~ '^[0-9a-f]{64}$'),
    created_by        uuid,
    created_by_email  text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    retired_at        timestamptz,
    UNIQUE (mode_id, version)
);
CREATE UNIQUE INDEX ux_judge_splits_active ON tracelab.judge_splits (mode_id) WHERE status = 'active';
COMMENT ON TABLE tracelab.judge_splits IS 'One row per split version of a failure mode: a platform wide header with no company. The members live in judge_split_members.';
COMMENT ON COLUMN tracelab.judge_splits.override IS 'Set when the split was built below the label minimum: reason, fail, pass, by, by_email, at.';

CREATE FUNCTION tracelab.judge_splits_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        IF coalesce(current_setting('tracelab.allow_purge', true), '') = 'on' THEN
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NULL;
        END IF;
        RAISE EXCEPTION 'judge_splits: a split is never deleted (% refused)', TG_OP;
    END IF;
    IF OLD.status = 'active' AND NEW.status = 'retired'
       AND (to_jsonb(NEW) - 'status' - 'retired_at') = (to_jsonb(OLD) - 'status' - 'retired_at') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'judge_splits: a split is immutable; only active to retired is allowed';
END
$$;
CREATE TRIGGER judge_splits_guard BEFORE UPDATE OR DELETE ON tracelab.judge_splits
    FOR EACH ROW EXECUTE FUNCTION tracelab.judge_splits_guard();
CREATE TRIGGER judge_splits_no_truncate BEFORE TRUNCATE ON tracelab.judge_splits
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.judge_splits_guard();
COMMENT ON FUNCTION tracelab.judge_splits_guard() IS 'Guard for split headers. UPDATE only moves a split from active to retired. DELETE and TRUNCATE only after SET LOCAL tracelab.allow_purge = on (company offboarding).';

CREATE TABLE tracelab.judge_split_members (
    split_id           uuid NOT NULL REFERENCES tracelab.judge_splits(id),
    turn_id            text NOT NULL CHECK (length(turn_id) BETWEEN 1 AND 200),
    organization_id    uuid NOT NULL,
    conversation_key   text NOT NULL CHECK (length(conversation_key) BETWEEN 3 AND 300),
    split              text NOT NULL CHECK (split IN ('train','dev','test')),
    label_present      boolean NOT NULL,
    label_source       text NOT NULL CHECK (label_source IN ('human_structured_label','human_open_code',
                       'accepted_suggestion','rejected_suggestion','accepted_label_proposal')),
    evidence_snapshot  jsonb NOT NULL,
    PRIMARY KEY (split_id, turn_id)
);
CREATE INDEX ix_judge_split_members_turn ON tracelab.judge_split_members (turn_id);
CREATE TRIGGER judge_split_members_append_only BEFORE UPDATE OR DELETE ON tracelab.judge_split_members
    FOR EACH ROW EXECUTE FUNCTION tracelab.refuse_change();
CREATE TRIGGER judge_split_members_no_truncate BEFORE TRUNCATE ON tracelab.judge_split_members
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.refuse_change();
COMMENT ON TABLE tracelab.judge_split_members IS 'The turns of a split, one row each with its split and its label as decided at split time. Never edited.';
COMMENT ON COLUMN tracelab.judge_split_members.turn_id IS 'The trace turn id (agent_trace_turns.turn_id in the application database). No foreign key across databases: the turn is copied into evidence_turns when it is put in a split.';
COMMENT ON COLUMN tracelab.judge_split_members.label_present IS 'True when the failure is present (the human label at split time).';
COMMENT ON COLUMN tracelab.judge_split_members.evidence_snapshot IS 'The judge input at split time: text, chars, sha, truncated. Fixed here so every run of every judge version reads the same input.';

CREATE TABLE tracelab.judges (
    id                   uuid PRIMARY KEY,
    mode_id              uuid NOT NULL REFERENCES tracelab.modes(id),
    version              integer NOT NULL CHECK (version >= 0),
    judge_key            text NOT NULL,
    kind                 text NOT NULL CHECK (kind IN ('llm','code')),
    split_id             uuid NOT NULL REFERENCES tracelab.judge_splits(id),
    model                text,
    prompt_text          text,
    prompt_parts         jsonb,
    check_key            text,
    check_version        integer,
    prompt_hash          text NOT NULL,
    status               text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','frozen')),
    change_note          text NOT NULL DEFAULT '',
    stop_reason          text,
    frozen_at            timestamptz,
    frozen_by            uuid,
    frozen_by_email      text,
    decision             text CHECK (decision IN ('use','do_not_use')),
    decision_note        text,
    monitor_threshold    numeric(5,4) CHECK (monitor_threshold > 0 AND monitor_threshold < 1),
    min_labels_override  jsonb,
    history              jsonb NOT NULL DEFAULT '[]'::jsonb,
    rev                  integer NOT NULL DEFAULT 1,
    created_by           uuid,
    created_by_email     text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (mode_id, version),
    CHECK ((kind = 'llm'  AND model IS NOT NULL AND prompt_text IS NOT NULL AND check_key IS NULL)
        OR (kind = 'code' AND check_key IS NOT NULL AND check_version IS NOT NULL AND prompt_text IS NULL)),
    CHECK (status = 'draft' OR (frozen_at IS NOT NULL AND stop_reason IS NOT NULL))
);
COMMENT ON TABLE tracelab.judges IS 'One row per judge version of a failure mode. The prompt is fixed at registration, freezing is one way, and a version is never deleted. Platform wide: no company column.';
COMMENT ON COLUMN tracelab.judges.prompt_hash IS 'LLM judge: sha256 of model, newline, prompt_text, first 12 hex. Code judge: sha256 of code:key@vN, first 12 hex.';
COMMENT ON COLUMN tracelab.judges.decision IS 'The platform owner verdict after the held-out test. Stays writable after the freeze, with the monitor threshold.';

CREATE FUNCTION tracelab.judges_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    hatch boolean := coalesce(current_setting('tracelab.allow_purge', true), '') = 'on';
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'judges: a judge version is never deleted (% refused)', TG_OP;
    END IF;
    IF NEW.model IS DISTINCT FROM OLD.model OR NEW.prompt_hash <> OLD.prompt_hash
       OR NEW.check_key IS DISTINCT FROM OLD.check_key OR NEW.check_version IS DISTINCT FROM OLD.check_version
       OR NEW.split_id <> OLD.split_id OR NEW.version <> OLD.version OR NEW.mode_id <> OLD.mode_id
       OR NEW.kind <> OLD.kind OR NEW.judge_key <> OLD.judge_key THEN
        RAISE EXCEPTION 'judges: % is immutable; register a new version', OLD.judge_key;
    END IF;
    IF NOT hatch AND (NEW.prompt_text IS DISTINCT FROM OLD.prompt_text
                      OR NEW.prompt_parts IS DISTINCT FROM OLD.prompt_parts) THEN
        RAISE EXCEPTION 'judges: the prompt of % is immutable; register a new version', OLD.judge_key;
    END IF;
    IF OLD.status = 'frozen' AND NEW.status <> 'frozen' THEN
        RAISE EXCEPTION 'judges: % is frozen; freezing is one-way', OLD.judge_key;
    END IF;
    IF OLD.status = 'frozen' AND (NEW.stop_reason IS DISTINCT FROM OLD.stop_reason
                                  OR NEW.frozen_at IS DISTINCT FROM OLD.frozen_at
                                  OR NEW.frozen_by IS DISTINCT FROM OLD.frozen_by
                                  OR NEW.frozen_by_email IS DISTINCT FROM OLD.frozen_by_email) THEN
        RAISE EXCEPTION 'judges: the freeze record of % never changes', OLD.judge_key;
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER judges_guard BEFORE UPDATE OR DELETE ON tracelab.judges
    FOR EACH ROW EXECUTE FUNCTION tracelab.judges_guard();
CREATE TRIGGER judges_no_truncate BEFORE TRUNCATE ON tracelab.judges
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.judges_guard();
COMMENT ON FUNCTION tracelab.judges_guard() IS 'Guard for judge versions. Never deleted. Model, hash, check, split and key never change. The prompt changes only after SET LOCAL tracelab.allow_purge = on (scrubbing a removed company). Frozen stays frozen with its freeze record.';

CREATE FUNCTION tracelab.judges_examples_from_train() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    ex jsonb;
    n integer := 0;
    seen text[] := '{}';
BEGIN
    IF NEW.kind <> 'llm' THEN
        RETURN NEW;
    END IF;
    IF jsonb_typeof(coalesce(NEW.prompt_parts -> 'examples', '[]'::jsonb)) <> 'array' THEN
        RAISE EXCEPTION 'judges: the examples of an LLM judge are a list';
    END IF;
    FOR ex IN SELECT value FROM jsonb_array_elements(coalesce(NEW.prompt_parts -> 'examples', '[]'::jsonb)) LOOP
        n := n + 1;
        IF (ex ->> 'turn_id') = ANY (seen) THEN
            RAISE EXCEPTION 'judges: example % is used twice', ex ->> 'turn_id';
        END IF;
        seen := seen || coalesce(ex ->> 'turn_id', '');
        PERFORM 1 FROM tracelab.judge_split_members m
                 WHERE m.split_id = NEW.split_id AND m.turn_id = ex ->> 'turn_id' AND m.split = 'train'
                   AND m.label_present = ((ex ->> 'verdict') = 'Fail');
        IF NOT FOUND THEN
            RAISE EXCEPTION 'judges: example % is not a train member with that label', ex ->> 'turn_id';
        END IF;
    END LOOP;
    IF n <> 3 THEN
        RAISE EXCEPTION 'judges: an LLM judge has exactly three examples, not %', n;
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER judges_examples_from_train BEFORE INSERT ON tracelab.judges
    FOR EACH ROW EXECUTE FUNCTION tracelab.judges_examples_from_train();
COMMENT ON FUNCTION tracelab.judges_examples_from_train() IS 'An LLM judge cites exactly three different few-shot examples and each is a train member of its split with the verdict that matches its label.';

CREATE TABLE tracelab.judge_predictions (
    prompt_hash         text NOT NULL,
    turn_id             text NOT NULL CHECK (length(turn_id) BETWEEN 1 AND 200),
    judge_id            uuid NOT NULL REFERENCES tracelab.judges(id),
    organization_id     uuid NOT NULL,
    status              text NOT NULL CHECK (status IN ('decided','error','undecidable')),
    failure_present     smallint CHECK (failure_present IN (0,1)),
    critique            text,
    input_sha           text,
    evidence_chars      integer,
    evidence_truncated  boolean NOT NULL DEFAULT false,
    attempts            integer NOT NULL DEFAULT 1,
    error               text,
    model               text,
    usd                 numeric(12,6),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (prompt_hash, turn_id),
    CHECK ((status = 'decided') = (failure_present IS NOT NULL))
);
CREATE INDEX ix_judge_predictions_turn ON tracelab.judge_predictions (turn_id);
COMMENT ON TABLE tracelab.judge_predictions IS 'The prediction cache: one decision per prompt_hash and turn. A decided row never changes and no row is deleted.';
COMMENT ON COLUMN tracelab.judge_predictions.turn_id IS 'The trace turn id (agent_trace_turns.turn_id in the application database). No foreign key across databases: the turn is copied into evidence_turns when it is judged.';

CREATE FUNCTION tracelab.judge_predictions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE')
       AND coalesce(current_setting('tracelab.allow_purge', true), '') = 'on' THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NULL;
    END IF;
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'judge_predictions: TRUNCATE refused';
    END IF;
    IF TG_OP = 'DELETE' OR OLD.status = 'decided' THEN
        RAISE EXCEPTION 'judge_predictions: a decided prediction is never changed (% / %)', OLD.prompt_hash, OLD.turn_id;
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER judge_predictions_guard BEFORE UPDATE OR DELETE ON tracelab.judge_predictions
    FOR EACH ROW EXECUTE FUNCTION tracelab.judge_predictions_guard();
CREATE TRIGGER judge_predictions_no_truncate BEFORE TRUNCATE ON tracelab.judge_predictions
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.judge_predictions_guard();
COMMENT ON FUNCTION tracelab.judge_predictions_guard() IS 'Guard for the prediction cache. A decided row never changes and nothing is deleted. A failed or undecidable row is retried in place. DELETE and TRUNCATE only after SET LOCAL tracelab.allow_purge = on (company offboarding).';

CREATE TABLE tracelab.judge_runs (
    id                uuid PRIMARY KEY,
    judge_id          uuid NOT NULL REFERENCES tracelab.judges(id),
    split             text NOT NULL CHECK (split IN ('dev','test')),
    job_id            uuid REFERENCES tracelab.jobs(id),
    status            text NOT NULL CHECK (status IN ('queued','running','done','incomplete','capped','failed','interrupted')),
    items             jsonb NOT NULL,
    results           jsonb,
    n                 integer,
    n_error           integer,
    n_undecidable     integer,
    n_truncated       integer,
    tp                integer,
    fn                integer,
    tn                integer,
    fp                integer,
    tpr               numeric(6,4),
    tnr               numeric(6,4),
    tpr_low           numeric(6,4),
    tpr_high          numeric(6,4),
    tnr_low           numeric(6,4),
    tnr_high          numeric(6,4),
    agreement         numeric(6,4),
    disagreements     jsonb NOT NULL DEFAULT '[]'::jsonb,
    label_flips       integer,
    usd               numeric(12,6) NOT NULL DEFAULT 0,
    usd_cap           numeric(12,4),
    rev               integer NOT NULL DEFAULT 1,
    started_by        uuid,
    started_by_email  text,
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    error             text
);
CREATE UNIQUE INDEX ux_judge_runs_one_test ON tracelab.judge_runs (judge_id) WHERE split = 'test';
CREATE INDEX ix_judge_runs_judge ON tracelab.judge_runs (judge_id, started_at DESC);
COMMENT ON TABLE tracelab.judge_runs IS 'One run of a judge version over its dev or test split. The held-out test runs once per version and only after the freeze. Platform wide: no company column.';
COMMENT ON COLUMN tracelab.judge_runs.items IS 'The items at start: turn id and label, label 1 = failure present.';
COMMENT ON COLUMN tracelab.judge_runs.results IS 'Turn, label, failure_present, status per item. For a finished test run these are the frozen pairs that monitoring corrects with.';
COMMENT ON COLUMN tracelab.judge_runs.tpr IS 'Pass is the positive class.';
COMMENT ON COLUMN tracelab.judge_runs.job_id IS 'The judge_run job that spent for this run. Evaluation spend is read from tracelab.jobs.usd_spent.';

CREATE FUNCTION tracelab.judge_runs_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    judge_status text;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT status INTO judge_status FROM tracelab.judges WHERE id = NEW.judge_id;
        IF NEW.split = 'test' AND judge_status IS DISTINCT FROM 'frozen' THEN
            RAISE EXCEPTION 'judge_runs: the held-out test needs a frozen judge';
        END IF;
        IF NEW.split = 'dev' AND judge_status = 'frozen' THEN
            RAISE EXCEPTION 'judge_runs: dev runs stop when the judge is frozen';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        IF coalesce(current_setting('tracelab.allow_purge', true), '') = 'on' THEN
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NULL;
        END IF;
        RAISE EXCEPTION 'judge_runs: runs are kept (% refused)', TG_OP;
    END IF;
    IF NEW.judge_id <> OLD.judge_id OR NEW.split <> OLD.split OR NEW.items IS DISTINCT FROM OLD.items THEN
        RAISE EXCEPTION 'judge_runs: the judge, split and items of a run never change';
    END IF;
    IF OLD.split = 'test' AND OLD.status = 'done' THEN
        RAISE EXCEPTION 'judge_runs: a finished held-out test is never changed';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER judge_runs_guard BEFORE INSERT OR UPDATE OR DELETE ON tracelab.judge_runs
    FOR EACH ROW EXECUTE FUNCTION tracelab.judge_runs_guard();
CREATE TRIGGER judge_runs_no_truncate BEFORE TRUNCATE ON tracelab.judge_runs
    FOR EACH STATEMENT EXECUTE FUNCTION tracelab.judge_runs_guard();
COMMENT ON FUNCTION tracelab.judge_runs_guard() IS 'Guard for judge runs. A test run needs a frozen judge, a dev run needs a draft one, the judge split and items never change, a finished test run is never changed. DELETE and TRUNCATE only after SET LOCAL tracelab.allow_purge = on (company offboarding).';
