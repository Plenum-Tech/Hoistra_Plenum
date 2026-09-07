-- Hourly labour rate on the contract parameters.
--
-- The invoice rate check derived its threshold as labour_day_rate / 8. A contract priced
-- by the hour has no day rate to divide, so it fell to the £350 system default and checked
-- every line against £43.75/h — flagging a whole £62/h invoice and burying the lines that
-- genuinely overcharge. This column holds the rate as the contract states it; the day rate
-- still applies where a contract really is priced per day.
ALTER TABLE plenum_cafm.contract_sla_parameters
    ADD COLUMN IF NOT EXISTS labour_hour_rate NUMERIC(12,2);
