-- Parity database only: make generated ids reproducible so two writers can be compared row for row.
CREATE SEQUENCE IF NOT EXISTS plenum_cafm.parity_uuid_seq;
CREATE OR REPLACE FUNCTION plenum_cafm.gen_random_uuid() RETURNS uuid LANGUAGE sql AS
  $$ SELECT ('00000000-0000-4000-8000-' || lpad(nextval('plenum_cafm.parity_uuid_seq')::text, 12, '0'))::uuid $$;
ALTER ROLE parity SET search_path = plenum_cafm, pg_catalog, public;
INSERT INTO plenum_cafm.organizations (id, name)
  SELECT '11111111-1111-4111-8111-111111111111', 'Parity Org'
  WHERE NOT EXISTS (SELECT 1 FROM plenum_cafm.organizations WHERE id::text = '11111111-1111-4111-8111-111111111111');
-- Column defaults written as pg_catalog.gen_random_uuid() bypass the search path; point them at the
-- reproducible function too, so rows either writer leaves to the default get the same ids.
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT table_schema, table_name, column_name FROM information_schema.columns
           WHERE table_schema = 'plenum_cafm' AND column_default LIKE '%gen_random_uuid()%'
  LOOP
    EXECUTE format('ALTER TABLE %I.%I ALTER COLUMN %I SET DEFAULT plenum_cafm.gen_random_uuid()',
                   r.table_schema, r.table_name, r.column_name);
  END LOOP;
END $$;
