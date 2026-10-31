-- Per-trade / hourly labour rates on a contract.
--
-- WHY
-- ---
-- contract_sla_parameters has one scalar for labour, `labour_day_rate`. Real FM contracts
-- are often not priced that way. Read against its source PDF on 17 Sep 2026,
-- 04_WKU_Facilities-Management-SLA-2022.pdf carries a full rate card in Appendix B —
-- twelve trades, each with a straight and an overtime HOURLY figure, in US dollars:
--
--     Area Technicians  44.82 / 67.23      Plumbers    51.71 / 77.56
--     Electricians      48.20 / 73.29      HVAC        51.40 / 77.10
--     Painters          34.12 / 51.17      Custodial   18.18 / 27.27   (+6 more)
--
-- None of it could be stored, so the contract was ingested showing £350/day marked
-- "default" while telling the reader it "did not state" a rate. Invoice checking for that
-- vendor compares against that £350 — a number nobody agreed to, in the wrong currency.
--
-- SHAPE
-- -----
--     {"currency": "USD", "basis": "hour", "source": "Appendix B",
--      "lines": [{"trade": "HVAC", "straight": 51.40, "overtime": 77.10}, ...]}
--
-- JSONB rather than a contract_rate_lines table: a card belongs to exactly one contract and
-- is always read whole, nothing queries across cards, and every other structured term on
-- this row (parts_pricing_json, kpi_clauses_json, task_criticality_json) is already JSONB.
--
-- SAFETY
-- ------
-- ADD COLUMN with no default and no NOT NULL. On PostgreSQL 11+ this does not rewrite the
-- table: it is a catalogue change, and the lock is held for a moment rather than for the
-- length of a scan. Still an ACCESS EXCLUSIVE lock, so run it when the table is quiet — a
-- pending exclusive lock blocks every later reader, which is exactly how an ALTER on
-- ingestion_documents stopped all traffic to that table for 34 minutes on 17 Sep 2026.
--
-- The application does NOT require this column. src/engines/contract_performance/rate_card.py
-- probes information_schema once per process and falls back to current behaviour when the
-- column is absent, so the code is already deployed and inert. Running this turns it on;
-- there is no second deployment and no window where the service expects a column it lacks.
--
-- REVERSING
-- ---------
--     ALTER TABLE plenum_cafm.contract_sla_parameters DROP COLUMN IF EXISTS rate_card_json;
--
-- Dropping loses any card extracted since, which is recoverable by re-ingesting the
-- contracts. Nothing else references the column.

ALTER TABLE plenum_cafm.contract_sla_parameters
    ADD COLUMN IF NOT EXISTS rate_card_json JSONB;

COMMENT ON COLUMN plenum_cafm.contract_sla_parameters.rate_card_json IS
    'Labour rate card as stated by the contract: {currency, basis, source, lines[{trade, straight, overtime}]}. '
    'NULL when the contract prices no trades. Read via engines/contract_performance/rate_card.py, '
    'which tolerates this column being absent.';
