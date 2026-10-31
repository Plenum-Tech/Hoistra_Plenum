-- The condition engine: the rule, the verdict it reaches, and the run that reached it.
--
-- The Assets page bands every asset Threat, Watch or In control from two signals that already
-- exist — whether its section is over its own reference, and whether an anomaly is attributed
-- to it. Nothing stored the band, the thresholds the band was decided by, or when the scan
-- last ran, so the page held all three as fixture data.
--
-- Three things are worth being deliberate about here.
--
-- The thresholds are a *row*, not a constant. "Over reference by more than 10 per cent" and
-- "persistent for 3 weeks" are the two steppers on the page, and a verdict reached under one
-- pair of thresholds means nothing once somebody moves them — so every verdict records the
-- thresholds it was reached under, and every run records them too.
--
-- A verdict stores its inputs beside its answer. A band on its own is an assertion; a band
-- with the section deviation, the anomaly count and the persistence that produced it can be
-- argued with, which is the only kind of automated judgement worth showing a person.
--
-- Asset keys are TEXT throughout: plenum_cafm.assets.id is uuid on one database and character
-- varying on the other, so comparisons are ::text on both sides.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, ADD COLUMN IF NOT EXISTS, guarded ALTERs.

-- ── the rule ────────────────────────────────────────────────────────────────────────────
-- One row per organisation. The defaults are the values the page ships with, so an
-- organisation that has never touched the steppers still scans against a stated rule rather
-- than against numbers buried in code.
CREATE TABLE IF NOT EXISTS plenum_cafm.asset_condition_rules (
    organization_id            UUID PRIMARY KEY,
    -- A section counts as over reference once it exceeds its own reference by this much.
    section_over_reference_pct NUMERIC(6, 2) NOT NULL DEFAULT 10.0,
    -- An anomaly counts as persistent once it has been open this long.
    anomaly_persistent_weeks   NUMERIC(6, 2) NOT NULL DEFAULT 3.0,
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by                 UUID
);

COMMENT ON TABLE plenum_cafm.asset_condition_rules
    IS 'The two thresholds the condition engine bands against, per organisation. Defaults '
       'match the values the Assets page ships with.';

-- ── the run ─────────────────────────────────────────────────────────────────────────────
-- What "LAST RUN 02:14 today" reads. Also the audit trail: a run records the thresholds it
-- ran under, so a verdict can always be traced back to the rule that produced it.
CREATE TABLE IF NOT EXISTS plenum_cafm.asset_condition_runs (
    run_id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id            UUID,
    started_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at                TIMESTAMPTZ,
    assets_scanned             INTEGER NOT NULL DEFAULT 0,
    threat                     INTEGER NOT NULL DEFAULT 0,
    watch                      INTEGER NOT NULL DEFAULT 0,
    in_control                 INTEGER NOT NULL DEFAULT 0,
    buildings                  INTEGER NOT NULL DEFAULT 0,
    section_over_reference_pct NUMERIC(6, 2),
    anomaly_persistent_weeks   NUMERIC(6, 2),
    -- Which buildings the run covered, so a scan narrowed to one building is not mistaken
    -- for a portfolio scan that found nothing anywhere else.
    scope                      TEXT NOT NULL DEFAULT 'portfolio',
    error                      TEXT
);

CREATE INDEX IF NOT EXISTS ix_condition_runs_started
    ON plenum_cafm.asset_condition_runs (organization_id, started_at DESC);

COMMENT ON TABLE plenum_cafm.asset_condition_runs
    IS 'One row per condition scan: when it ran, what it found, and the thresholds it used.';

-- ── the verdict ─────────────────────────────────────────────────────────────────────────
-- One row per asset per run. Kept rather than overwritten so a band can be compared with what
-- it was last week, which is the question anybody looking at a trend will ask next.
CREATE TABLE IF NOT EXISTS plenum_cafm.asset_condition_verdicts (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                     UUID,
    organization_id            UUID,
    asset_id                   TEXT NOT NULL,
    building_id                UUID,
    section_id                 UUID,
    -- threat | watch | in_control
    band                       TEXT NOT NULL,
    -- Why, in the words the page shows. Machine-readable reasons, not a rendered sentence.
    reasons                    JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- The inputs, stored beside the answer.
    section_deviation_pct      NUMERIC(8, 2),
    section_over_reference     BOOLEAN NOT NULL DEFAULT false,
    anomalies_open             INTEGER NOT NULL DEFAULT 0,
    anomaly_weeks              NUMERIC(8, 2),
    anomaly_persistent         BOOLEAN NOT NULL DEFAULT false,
    anomaly_annual_cost        NUMERIC(14, 2),
    currency                   TEXT,
    -- The thresholds this verdict was reached under.
    section_over_reference_pct NUMERIC(6, 2),
    anomaly_persistent_weeks   NUMERIC(6, 2),
    computed_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_condition_verdicts_run
    ON plenum_cafm.asset_condition_verdicts (run_id);
CREATE INDEX IF NOT EXISTS ix_condition_verdicts_asset
    ON plenum_cafm.asset_condition_verdicts (asset_id, computed_at DESC);
CREATE INDEX IF NOT EXISTS ix_condition_verdicts_building
    ON plenum_cafm.asset_condition_verdicts (building_id, computed_at DESC);

COMMENT ON TABLE plenum_cafm.asset_condition_verdicts
    IS 'One banding per asset per scan, with the signals and thresholds that produced it. '
       'Kept per run rather than overwritten, so a band can be compared with last week.';

COMMENT ON COLUMN plenum_cafm.asset_condition_verdicts.band
    IS 'threat: section over reference AND an anomaly attributed to this asset. '
       'watch: section over but no anomaly here (it shares the load), or a persistent anomaly '
       'with the section not over. in_control: neither.';
