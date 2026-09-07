-- Feature C gap-close: site occupancy change log (baseline-drift guard)

CREATE TABLE IF NOT EXISTS plenum_cafm.site_occupancy_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    site_id             UUID NOT NULL,
    occupancy_state     VARCHAR(40) NOT NULL,  -- occupied | unoccupied | reduced | event
    changed_at          TIMESTAMPTZ NOT NULL,
    notes               TEXT,
    source              VARCHAR(40) NOT NULL DEFAULT 'manual',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_sol_site_at
    ON plenum_cafm.site_occupancy_logs (site_id, changed_at);
