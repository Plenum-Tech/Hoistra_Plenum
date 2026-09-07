-- CCC 8.8 - US verification sources (18 pack types).
-- Requires compliance_verification_sources_country.sql (adds country_code + composite PK).
--
-- US compliance registers are largely STATE-level and fragmented, so most types are
-- 'website' (per-record lookup on the issuing authority) rather than public_api.
-- api_available records that honestly instead of implying a national API that does not exist.

INSERT INTO plenum_cafm.compliance_verification_sources
    (certificate_type_code, country_code, cert_scope, verification_group, channel, register, source_url, api_available, refresh_cadence, notes, code_aliases)
VALUES
('US_FIRE_ALARM_NFPA72','US','building','Fire protection','website','State Fire Marshal / AHJ','https://www.usfa.fema.gov/','no','90d','NFPA 72 ITM record held by the AHJ; alarm contractor licensed at state level','[]'::jsonb),
('US_SPRINKLER_NFPA25','US','building','Fire protection','website','State Fire Marshal / AHJ','https://www.usfa.fema.gov/','no','90d','NFPA 25 ITM report; sprinkler contractor licence verified at state level','[]'::jsonb),
('US_FIRE_EXT_NFPA10','US','building','Fire protection','website','State Fire Marshal / AHJ','https://www.usfa.fema.gov/','no','90d','NFPA 10 annual maintenance tag; servicing company licensed by state','[]'::jsonb),
('US_FIRE_DOOR_NFPA80','US','building','Fire protection','website','State Fire Marshal / AHJ','https://www.usfa.fema.gov/','no','90d','NFPA 80 annual fire door inspection; inspector qualification per AHJ','[]'::jsonb),
('US_EMERGENCY_LIGHT_NFPA101','US','building','Electrical','website','State Fire Marshal / AHJ','https://www.usfa.fema.gov/','no','90d','NFPA 101 emergency and exit lighting test record','[]'::jsonb),
('US_GENERATOR_NFPA110','US','building','Electrical','self_produced','Self-held',NULL,'n/a','n/a','NFPA 110 generator test log is self-held; no external register','[]'::jsonb),
('US_ELEVATOR_A17','US','building','Lifts','website','State elevator authority','https://www.asme.org/codes-standards','no','90d','ASME A17.1 inspection; operating certificate issued by the state elevator authority','[]'::jsonb),
('US_BOILER_INSPECTION','US','building','Pressure systems','website','National Board / state boiler authority','https://www.nationalboard.org/index.aspx?pageID=178','no','90d','National Board commission number verifiable; jurisdiction issues the operating certificate','[]'::jsonb),
('US_BACKFLOW','US','building','Water / plumbing','website','State / water purveyor tester registry','https://www.epa.gov/dwreginfo','no','90d','Assembly test filed with the local water purveyor; tester certified by state','[]'::jsonb),
('US_LEGIONELLA_ASHRAE188','US','building','Water / Legionella','self_produced','Self-held',NULL,'n/a','n/a','ASHRAE 188 water management plan is a self-held living document','[]'::jsonb),
('US_ASBESTOS_OM','US','building','Asbestos','website','EPA AHERA / state asbestos program','https://www.epa.gov/asbestos/asbestos-laws-and-regulations','no','90d','AHERA O&M plan; abatement contractors licensed per state asbestos program','[]'::jsonb),
('US_EPA_608','US','vendor','HVAC / F-Gas','website','EPA 608 certifying organization','https://www.epa.gov/section608/section-608-technician-certification-0','no','90d','Technician card issued by an EPA-approved certifying body; verify with the issuer','[]'::jsonb),
('US_LEAD_RRP','US','vendor','Environmental','public_api','SAM.gov entity register + EPA Lead-Safe firm search','https://sam.gov/','yes','90d','Firm entity verified against the federal SAM.gov register. The EPA Lead-Safe firm directory (cdxapps.epa.gov/ocspp-oppt-lead/firm-location-search) has no public API, so the RRP certification itself is confirmed there manually.','[]'::jsonb),
('US_NICET_FIRE','US','vendor','Fire protection','website','NICET certification verification','https://www.nicet.org/about-us/frequently-asked-questions/verification/','no','90d','NICET publishes a per-record certification verification lookup','[]'::jsonb),
('US_OSHA_30','US','vendor','HSE','website','OSHA Outreach card check','https://www.osha.gov/training/outreach','no','90d','OSHA 30-hour card verified via the DOL Outreach card-verification service','[]'::jsonb),
('US_STATE_CONTRACTOR_LICENSE','US','vendor','General','data_dump','State contractor licence registers (WA L&I open data)','https://data.wa.gov/resource/m8qx-ubtq.json','yes','weekly','State-issued licence. Washington L&I publishes the full licence register as open data (Socrata dataset m8qx-ubtq) - ingested weekly and matched locally by licence number. Other states are verified on their own board site until their open data is added.','[]'::jsonb),
('US_GL_INSURANCE','US','vendor','Insurance','public_api','SAM.gov entity register (insured contractor)','https://sam.gov/','yes','90d','No national US API exists for insurance carriers (NAIC is web-only), so the INSURED CONTRACTOR entity is verified against the federal SAM.gov register (active registration + not excluded). Carrier licensing is confirmed manually on NAIC / the state DOI.','[]'::jsonb),
('US_WORKERS_COMP','US','vendor','Insurance','public_api','SAM.gov entity register (insured contractor)','https://sam.gov/','yes','90d','Insured contractor entity verified against the federal SAM.gov register (active + not excluded). Workers-comp carrier licensing is confirmed manually on NAIC / the state DOI.','[]'::jsonb)
ON CONFLICT (certificate_type_code, country_code) DO UPDATE SET
    cert_scope = EXCLUDED.cert_scope, verification_group = EXCLUDED.verification_group,
    channel = EXCLUDED.channel, register = EXCLUDED.register, source_url = EXCLUDED.source_url,
    api_available = EXCLUDED.api_available, refresh_cadence = EXCLUDED.refresh_cadence,
    notes = EXCLUDED.notes, code_aliases = EXCLUDED.code_aliases, updated_at = now();
