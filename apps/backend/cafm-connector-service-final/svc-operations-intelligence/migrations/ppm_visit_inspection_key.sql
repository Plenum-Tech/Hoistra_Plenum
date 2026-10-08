-- A visit could not point at the report that came off it, because the two keys are different
-- types on the two databases.
--
-- plenum_cafm.inspections.id is a uuid on one database and an integer sequence on the other.
-- plenum_cafm.ppm_visits.inspection_id was uuid on both, so on the integer database the link
-- could not be written at all: a uuid column cannot hold a sequence value, and casting one
-- into the other stores a number no lookup could follow. The PPM health table therefore read
-- "0 reports" against every contract on that database, which is a different and wrong
-- complaint from "these visits filed nothing".
--
-- The answer is the one this schema already reached for the same problem elsewhere. Asset and
-- vendor keys are TEXT throughout `asset_intelligence_tables.sql` for exactly this reason —
-- plenum_cafm.assets.id and plenum_cafm.vendors.id are uuid on one database and character
-- varying on the other — and comparisons are ::text on both sides. This brings the visit's
-- report key into line with that.
--
-- Safe to run repeatedly and safe on data:
--
--   * A uuid casts to text without loss, so any link already written survives verbatim.
--   * Nothing references the column: there is no foreign key onto inspections and no index
--     on inspection_id on either database, checked before writing this.
--   * The ALTER is skipped entirely where the column is already text, so a second run is a
--     no-op rather than a rewrite of the table.

DO $ppm_visit_inspection_key$
DECLARE
    current_type text;
BEGIN
    IF to_regclass('plenum_cafm.ppm_visits') IS NULL THEN
        RETURN;
    END IF;

    SELECT data_type INTO current_type
      FROM information_schema.columns
     WHERE table_schema = 'plenum_cafm'
       AND table_name = 'ppm_visits'
       AND column_name = 'inspection_id';

    IF current_type IS NULL THEN
        -- The column is not there at all yet; add it in the shape it should have been.
        ALTER TABLE plenum_cafm.ppm_visits ADD COLUMN inspection_id TEXT;
        RAISE NOTICE 'ppm_visits.inspection_id added as text';
    ELSIF current_type <> 'text' THEN
        -- USING ::text keeps every link already written. A uuid renders as its canonical
        -- form, which is what a text key holding a uuid should look like.
        ALTER TABLE plenum_cafm.ppm_visits
            ALTER COLUMN inspection_id TYPE TEXT USING inspection_id::text;
        RAISE NOTICE 'ppm_visits.inspection_id widened from % to text', current_type;
    END IF;
END
$ppm_visit_inspection_key$;

CREATE INDEX IF NOT EXISTS ix_ppm_visits_inspection
    ON plenum_cafm.ppm_visits (inspection_id);

COMMENT ON COLUMN plenum_cafm.ppm_visits.inspection_id
    IS 'The report that came off this visit, as plenum_cafm.inspections.id in text form. '
       'TEXT rather than uuid because that key is a uuid on one database and an integer '
       'sequence on the other; compare ::text on both sides.';
