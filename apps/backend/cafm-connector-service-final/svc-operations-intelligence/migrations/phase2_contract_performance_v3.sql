-- Feature B gap-closure: signed_date + WO conflict columns
-- Idempotent (safe to re-run). Apply after phase2_contract_performance_v2.sql.

-- Gap 1: FR-035 — overlapping contract selection requires signing date
ALTER TABLE plenum_cafm.contract_sla_parameters
    ADD COLUMN IF NOT EXISTS signed_date DATE;
COMMENT ON COLUMN plenum_cafm.contract_sla_parameters.signed_date
    IS 'Date the contract was signed. Used to select governing contract when multiple confirmed rows exist for the same vendor (most recently signed wins, per FR-035).';

-- Gap 2: FR-039 — re-ingestion conflict detection
ALTER TABLE plenum_cafm.work_orders
    ADD COLUMN IF NOT EXISTS conflict_flag BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS conflict_payload JSONB;
COMMENT ON COLUMN plenum_cafm.work_orders.conflict_flag
    IS 'Set to TRUE when a work order is re-ingested with values that differ from the stored record. WO is excluded from scoring until PM resolves the conflict (FR-039).';
COMMENT ON COLUMN plenum_cafm.work_orders.conflict_payload
    IS 'JSONB snapshot of the incoming values that conflicted with the stored record, preserved for PM review (FR-039).';
