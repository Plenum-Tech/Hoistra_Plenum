-- 0009_label_quotes.sql: a person may now give several labels on one step, each on the text they selected (a quote), or one
-- on the whole step. A label is a lineage of versions (lineage_id; a version written by 0008 is its own lineage), so the
-- one-head-per-person-per-step index of 0008 gives way to one head per lineage. Runs in Trace Lab's OWN database
-- (hoist_tracelab). Applied only by migrate.apply_pending. A correction is a new numbered file.
SET LOCAL lock_timeout = '3s';

ALTER TABLE tracelab.step_labels ADD COLUMN lineage_id uuid;

ALTER TABLE tracelab.step_labels ADD COLUMN quote text CHECK (quote IS NULL OR char_length(quote) BETWEEN 1 AND 2000);

DROP INDEX tracelab.ux_tl_step_labels_head;

CREATE UNIQUE INDEX ux_tl_step_labels_lineage_head ON tracelab.step_labels ((coalesce(lineage_id, id)))
    WHERE superseded_by IS NULL;

COMMENT ON COLUMN tracelab.step_labels.lineage_id IS 'The label this row is a version of (null: the row is its own first version, as written by 0008).';

COMMENT ON COLUMN tracelab.step_labels.quote IS 'The text the person selected in the step, or null for a label on the whole step.';
