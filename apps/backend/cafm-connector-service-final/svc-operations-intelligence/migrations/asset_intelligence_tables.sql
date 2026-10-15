-- The four things the Assets page wanted and the schema could not answer.
--
-- Sections, asset value, the vendor who holds an asset, and reading bands with a failure
-- assessment. Each was a block on the page fed by fixture data because nothing behind it
-- existed. These are the tables and columns that let the same blocks be answered from the
-- register instead.
--
-- Every key that points at an asset or a vendor is TEXT, not uuid. plenum_cafm.assets.id and
-- plenum_cafm.vendors.id are uuid on one database and character varying on the other, so a
-- uuid column here would be unusable on one of them. Comparisons are ::text on both sides.
--
-- Idempotent throughout: CREATE TABLE IF NOT EXISTS, ADD COLUMN IF NOT EXISTS.

-- ── 6. Sections — the metered zone a building's load is read against ────────────────────
-- A section is not a floor and not a space. It is the thing a sub-meter measures and that
-- has a reference intensity of its own: a server room is not judged against an office
-- benchmark, and a car park is not judged against either. floor_id is optional because a
-- plant room in a basement and a run of tenant floors are both sections.
CREATE TABLE IF NOT EXISTS plenum_cafm.building_sections (
    section_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id       UUID,
    building_id           UUID NOT NULL,
    floor_id              UUID,
    name                  TEXT NOT NULL,
    section_type          TEXT NOT NULL DEFAULT 'general',
    gross_area_m2         NUMERIC(12, 2),
    -- The reference this section is read against, in kWh/m2/yr. Held per section precisely
    -- because the building's own pack benchmark is the wrong answer for most of them.
    reference_eui_kwh_m2  NUMERIC(10, 2),
    reference_source      TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_building_sections_building
    ON plenum_cafm.building_sections (building_id);

COMMENT ON TABLE plenum_cafm.building_sections
    IS 'A sub-metered zone with its own intensity reference. Not a floor and not a space: '
       'the thing a sub-meter measures and that is judged against a reference of its own.';

-- Which section a meter measures, and which section an asset sits in.
--
-- Guarded on the table existing. Migrations run in filename order and this one sorts near
-- the front, so on a database being built from nothing it can run before the table it
-- alters is there. A plain ALTER would fail and block startup; skipping is right, because
-- whatever creates the table later creates it with these columns.
DO $sections$
BEGIN
    IF to_regclass('plenum_cafm.energy_meters') IS NOT NULL THEN
        ALTER TABLE plenum_cafm.energy_meters ADD COLUMN IF NOT EXISTS section_id UUID;
        CREATE INDEX IF NOT EXISTS ix_energy_meters_section
            ON plenum_cafm.energy_meters (section_id);
    END IF;

    IF to_regclass('plenum_cafm.assets') IS NOT NULL THEN
        ALTER TABLE plenum_cafm.assets ADD COLUMN IF NOT EXISTS section_id UUID;
    END IF;
END
$sections$;

-- ── 7. Asset value — what "value at risk" is actually computed from ─────────────────────
-- Three numbers, none of them derivable from anything already on the table. Replacement
-- value is what it costs new today; design life is how long it should last; the wear
-- coefficient is how much faster it ages when it runs badly, which is what turns an energy
-- deviation into a number of pounds rather than a percentage.
DO $value$
BEGIN
    IF to_regclass('plenum_cafm.assets') IS NULL THEN
        RETURN;
    END IF;

    ALTER TABLE plenum_cafm.assets
        ADD COLUMN IF NOT EXISTS replacement_value    NUMERIC(14, 2),
        ADD COLUMN IF NOT EXISTS replacement_currency TEXT,
        ADD COLUMN IF NOT EXISTS design_life_years    NUMERIC(6, 2),
        ADD COLUMN IF NOT EXISTS wear_coefficient     NUMERIC(6, 3),
        -- 8. The vendor who holds the asset. TEXT because plenum_cafm.vendors.id is uuid on
        -- one database and varchar on the other.
        ADD COLUMN IF NOT EXISTS vendor_id            TEXT;

    CREATE INDEX IF NOT EXISTS ix_assets_vendor ON plenum_cafm.assets (vendor_id);
END
$value$;

COMMENT ON COLUMN plenum_cafm.assets.wear_coefficient
    IS 'How much faster the asset ages when running above its reference. 1.0 means a 30 per '
       'cent deviation ages it 30 per cent faster. Used to turn a deviation into a value.';

-- ── 8. The vendor who holds the asset ───────────────────────────────────────────────────
-- Added in the block above, alongside the other asset columns, so the whole set is written
-- under one guard rather than three.

COMMENT ON COLUMN plenum_cafm.assets.vendor_id
    IS 'The vendor responsible for this asset, as plenum_cafm.vendors.id in text form. '
       'Compare ::text on both sides — the vendors key is uuid on one database, varchar on '
       'the other.';

-- ── 9a. Reading bands — what makes a reading "out of band" ──────────────────────────────
-- A band belongs to a reading type, optionally narrowed to one asset category or one asset.
-- The most specific row wins: asset, then category, then the type's default.
CREATE TABLE IF NOT EXISTS plenum_cafm.asset_reading_bands (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID,
    reading_type    TEXT NOT NULL,
    asset_category  TEXT,
    asset_id        TEXT,
    unit            TEXT,
    lo              NUMERIC(14, 4),
    hi              NUMERIC(14, 4),
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_reading_bands_type
    ON plenum_cafm.asset_reading_bands (reading_type);

COMMENT ON TABLE plenum_cafm.asset_reading_bands
    IS 'Low and high limits per reading type. Most specific row wins: asset, then category, '
       'then the type default.';

-- ── 9b. Failure assessment — stated as what it is ───────────────────────────────────────
-- Deliberately NOT called a trained model, and deliberately carries no accuracy, precision
-- or recall. Those numbers mean something only when a model has been fitted against labelled
-- outcomes, and none has been. What this holds is a probability computed from signals that
-- are actually on record — condition grade, open anomalies, age against design life,
-- readings outside their band — with those signals stored beside it so the figure can be
-- argued with. `method` names the rule that produced it.
CREATE TABLE IF NOT EXISTS plenum_cafm.asset_failure_assessments (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id       UUID,
    asset_id              TEXT NOT NULL,
    building_id           UUID,
    probability           NUMERIC(5, 4),
    horizon_days          INTEGER NOT NULL DEFAULT 90,
    method                TEXT NOT NULL,
    drivers               JSONB NOT NULL DEFAULT '[]'::jsonb,
    remaining_life_months NUMERIC(8, 2),
    design_life_used_pct  NUMERIC(6, 2),
    computed_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_failure_assessments_asset
    ON plenum_cafm.asset_failure_assessments (asset_id);
CREATE INDEX IF NOT EXISTS ix_failure_assessments_building
    ON plenum_cafm.asset_failure_assessments (building_id);

COMMENT ON TABLE plenum_cafm.asset_failure_assessments
    IS 'A failure probability computed from recorded signals by a named rule, with those '
       'signals stored beside it. Not a fitted model: it carries no accuracy, precision or '
       'recall because none has been measured against labelled outcomes.';
