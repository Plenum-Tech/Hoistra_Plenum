-- An anomaly's money figure says which currency it is in.
--
-- plenum_cafm.energy_anomalies.financial_gbp has always held whatever the site's own tariff
-- was quoted in — sterling for the UK buildings, dirhams for the 603 in the UAE — under a
-- column named for one of them. The number was never wrong; the label was, and a portfolio
-- spanning four countries cannot sum a column whose unit changes row by row.
--
-- The column keeps its name (callers read it, and renaming it breaks them for no gain) and
-- `currency` now says what it means. Existing rows are backfilled from the country of the
-- building their meter is on, which is the same rule new rows follow — not a guess, and not
-- a blanket GBP that would relabel the dirham rows as sterling.
--
-- The country is read off plenum_cafm.sites. plenum_cafm.buildings has no country_code on
-- this deployment — a building inherits its address from its site — and naming a column
-- that is not there fails the whole migration rather than falling back.
ALTER TABLE plenum_cafm.energy_anomalies ADD COLUMN IF NOT EXISTS currency VARCHAR(3);

UPDATE plenum_cafm.energy_anomalies a
   SET currency = CASE upper(coalesce(s.country_code, ''))
                    WHEN 'AE' THEN 'AED'
                    WHEN 'US' THEN 'USD'
                    WHEN 'SG' THEN 'SGD'
                    WHEN 'UK' THEN 'GBP'
                    WHEN 'GB' THEN 'GBP'
                    ELSE 'GBP'
                  END
  FROM plenum_cafm.energy_meters m
  LEFT JOIN plenum_cafm.buildings b ON b.building_id = m.site_id
  LEFT JOIN plenum_cafm.sites s ON s.site_id = b.site_id OR s.id = b.site_id
 WHERE a.currency IS NULL AND m.id = a.meter_id;

-- Anything with no meter to trace takes the default, so the column is never half-null.
UPDATE plenum_cafm.energy_anomalies SET currency = 'GBP' WHERE currency IS NULL;
