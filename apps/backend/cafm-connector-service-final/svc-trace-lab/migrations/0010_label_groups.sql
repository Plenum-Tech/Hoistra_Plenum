-- 0010_label_groups.sql: a label may say what should happen in the step (expected) and when (only_if, the condition
-- under which it should happen), and several steps may be labelled together: each step keeps its own row, version
-- history and expected/only_if, and the rows labelled together share a group_id. Runs in Trace Lab's OWN database
-- (hoist_tracelab). Applied only by migrate.apply_pending. A correction is a new numbered file.
SET LOCAL lock_timeout = '3s';

ALTER TABLE tracelab.step_labels ADD COLUMN group_id uuid;

ALTER TABLE tracelab.step_labels ADD COLUMN expected text CHECK (expected IS NULL OR char_length(expected) BETWEEN 1 AND 2000);

ALTER TABLE tracelab.step_labels ADD COLUMN only_if text CHECK (only_if IS NULL OR char_length(only_if) BETWEEN 1 AND 2000);

CREATE INDEX ix_tl_step_labels_group ON tracelab.step_labels (organization_id, group_id)
    WHERE group_id IS NOT NULL AND superseded_by IS NULL;

COMMENT ON COLUMN tracelab.step_labels.group_id IS 'Steps labelled together share this id (null: a label on one step).';

COMMENT ON COLUMN tracelab.step_labels.expected IS 'What should happen in this step, as the reviewer put it (null: not said).';

COMMENT ON COLUMN tracelab.step_labels.only_if IS 'The condition under which the expected behaviour applies, e.g. only if the question names a vendor (null: always).';
