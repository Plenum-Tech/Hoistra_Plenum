-- What the inspection-intelligence cards and the PPM health table need and could not have.
--
-- Most of both panels is derivable from records that already exist — a recommendation that
-- never became an order is a join, an anomaly corroborated by an earlier report is a date
-- comparison, an asset graded poor is a column. Those needed no schema at all.
--
-- Four things did, and they are the only columns added here. Each is a fact nobody records
-- anywhere today, so no amount of joining produces it:
--
--   * whether a PPM visit was deferred, and why. "6 deferrals" is on the page and there is no
--     deferral concept in the schema at all.
--   * what a contract covers and where. The table shows "Heating and gas · UK" over
--     "Boilers, DHW, gas safety"; vendor_contracts has a name and nothing else.
--   * how long a part is warranted, and when it was fitted. Warranty lives on the asset, not
--     the part, so "findings on parts still under warranty" cannot be asked.
--   * what a fitting was invoiced at, so warranted work can be said to be claimable.
--
-- Every ALTER is guarded on the table existing: migrations run in filename order and this one
-- sorts early, so on a database being built from nothing it can run before the tables it
-- alters exist. Skipping is right — whatever creates them later creates them with these
-- columns.
--
-- Idempotent throughout.

-- ── PPM visits: deferral ────────────────────────────────────────────────────────────────
-- A deferred visit is not a missed one. It was agreed to move, which is a different fact
-- about the contract and the reason the page counts them apart.
DO $ppm$
BEGIN
    IF to_regclass('plenum_cafm.ppm_visits') IS NULL THEN
        RETURN;
    END IF;

    ALTER TABLE plenum_cafm.ppm_visits
        ADD COLUMN IF NOT EXISTS deferred        BOOLEAN NOT NULL DEFAULT false,
        ADD COLUMN IF NOT EXISTS deferred_to     DATE,
        ADD COLUMN IF NOT EXISTS deferral_reason TEXT,
        -- Which report, if any, came off this visit. The page prints "10 / 12 reports":
        -- visits done against reports actually filed, and the gap is the point.
        ADD COLUMN IF NOT EXISTS inspection_id   UUID;

    CREATE INDEX IF NOT EXISTS ix_ppm_visits_contract
        ON plenum_cafm.ppm_visits (contract_id);
    CREATE INDEX IF NOT EXISTS ix_ppm_visits_deferred
        ON plenum_cafm.ppm_visits (deferred) WHERE deferred;
END
$ppm$;

COMMENT ON COLUMN plenum_cafm.ppm_visits.deferred
    IS 'The visit was agreed to move rather than missed. Counted apart from missed, because '
       'an agreed deferral and a visit nobody turned up for are different facts.';

-- ── Contracts: what they cover, and where ───────────────────────────────────────────────
DO $contract$
BEGIN
    IF to_regclass('plenum_cafm.vendor_contracts') IS NULL THEN
        RETURN;
    END IF;

    ALTER TABLE plenum_cafm.vendor_contracts
        -- "Heating and gas · UK" — the country decides which rule book applies, the same way
        -- it does everywhere else in this platform.
        ADD COLUMN IF NOT EXISTS country_code   TEXT,
        -- "Boilers, DHW, gas safety" — what the contract is for, in the words the page prints.
        ADD COLUMN IF NOT EXISTS service_scope  TEXT,
        -- How many visits a year the contract commits to, where that is agreed rather than
        -- inferred from how many happened to be booked.
        ADD COLUMN IF NOT EXISTS visits_per_year INTEGER;
END
$contract$;

COMMENT ON COLUMN plenum_cafm.vendor_contracts.service_scope
    IS 'What the contract covers, as the contract itself words it. Shown under the contract '
       'name on the PPM health table.';

-- ── Parts: warranty on the part, not only on the asset ──────────────────────────────────
-- An asset installed in 2009 is long out of warranty while a compressor contactor fitted last
-- month is not. Until the warranty is on the fitting, "findings on parts still under warranty"
-- has no answer, and neither does the money claimable against them.
DO $parts$
BEGIN
    IF to_regclass('plenum_cafm.spare_parts') IS NOT NULL THEN
        ALTER TABLE plenum_cafm.spare_parts
            ADD COLUMN IF NOT EXISTS warranty_months INTEGER;
    END IF;

    IF to_regclass('plenum_cafm.work_order_parts') IS NOT NULL THEN
        ALTER TABLE plenum_cafm.work_order_parts
            -- When this part went in. Warranty runs from fitting, not from purchase.
            ADD COLUMN IF NOT EXISTS fitted_at        DATE,
            -- Its own expiry where the supplier gave one, overriding the part's default term.
            ADD COLUMN IF NOT EXISTS warranty_expiry  DATE,
            -- What the fitting was invoiced at, which is what becomes claimable if the part
            -- fails inside its term.
            ADD COLUMN IF NOT EXISTS invoiced_value   NUMERIC(14, 2),
            ADD COLUMN IF NOT EXISTS currency         TEXT;

        CREATE INDEX IF NOT EXISTS ix_work_order_parts_warranty
            ON plenum_cafm.work_order_parts (warranty_expiry);
    END IF;
END
$parts$;

COMMENT ON COLUMN plenum_cafm.work_order_parts.invoiced_value
    IS 'What this fitting was invoiced at. Becomes the claimable figure when a finding lands '
       'on the part inside its warranty term.';

-- ── Inspections: the columns one database has and the other does not ────────────────────
-- inspections is 14 columns on one database and 9 on the other. The engines probe for what is
-- there rather than assuming, but the three that carry real meaning are added where missing so
-- both databases can answer the same questions.
DO $insp$
BEGIN
    IF to_regclass('plenum_cafm.inspections') IS NULL THEN
        RETURN;
    END IF;

    ALTER TABLE plenum_cafm.inspections
        ADD COLUMN IF NOT EXISTS corrective_action BOOLEAN DEFAULT false,
        ADD COLUMN IF NOT EXISTS findings_jsonb    JSONB,
        ADD COLUMN IF NOT EXISTS section           VARCHAR(10),
        ADD COLUMN IF NOT EXISTS source_file       TEXT,
        -- The order this report came off, so a recommendation can be traced to the work that
        -- produced it and then to whether anything followed.
        ADD COLUMN IF NOT EXISTS work_order_id     TEXT,
        -- Set when a recommendation in this report became an order. Null while it has not.
        ADD COLUMN IF NOT EXISTS converted_work_order_id TEXT,
        ADD COLUMN IF NOT EXISTS recommendation    TEXT;

    CREATE INDEX IF NOT EXISTS ix_inspections_asset
        ON plenum_cafm.inspections (asset_id);
    CREATE INDEX IF NOT EXISTS ix_inspections_date
        ON plenum_cafm.inspections (inspection_date DESC);
END
$insp$;

COMMENT ON COLUMN plenum_cafm.inspections.converted_work_order_id
    IS 'The order a recommendation in this report became. Null means it never became one, '
       'which is the whole point of the "recommendations never converted" figure.';

-- ── Re-reading the reports: the run behind "LAST RUN 02:14 today" ───────────────────────
-- The Maintenance screen has a Re-read inspection reports button and a last-run stamp beside
-- it, and nothing recorded either. Same shape as the condition engine's run table and for the
-- same reason: the reads compute live, so this is not what the panel reads from — it is the
-- timestamp somebody can point at, and the record of what a read found so this week's can be
-- compared with last week's.
CREATE TABLE IF NOT EXISTS plenum_cafm.inspection_read_runs (
    run_id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID,
    started_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at             TIMESTAMPTZ,
    reports_read            INTEGER NOT NULL DEFAULT 0,
    assets_covered          INTEGER NOT NULL DEFAULT 0,
    recommendations_open    INTEGER NOT NULL DEFAULT 0,
    unconverted             INTEGER NOT NULL DEFAULT 0,
    corroborated_anomalies  INTEGER NOT NULL DEFAULT 0,
    warranted_findings      INTEGER NOT NULL DEFAULT 0,
    poorly_graded           INTEGER NOT NULL DEFAULT 0,
    -- Cards this database could not answer at all, so a run that found nothing is told apart
    -- from a run that could not look.
    unanswerable            JSONB NOT NULL DEFAULT '[]'::jsonb,
    scope                   TEXT NOT NULL DEFAULT 'portfolio',
    error                   TEXT
);

CREATE INDEX IF NOT EXISTS ix_inspection_read_runs_started
    ON plenum_cafm.inspection_read_runs (organization_id, started_at DESC);

COMMENT ON TABLE plenum_cafm.inspection_read_runs
    IS 'One row per re-read of the inspection reports: when it ran and what it found. The '
       'panel computes live; this is the last-run stamp and the history.';
