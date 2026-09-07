-- CCC 8.8 - UAE verification sources (45 pack types).
-- Requires compliance_verification_sources_country.sql (adds country_code + composite PK).
--
-- IMPORTANT: several UAE codes collide with UK codes (FIRE_DOOR, SPRINKLER_TEST,
-- ISO_9001, ISO_14001, PUBLIC_LIABILITY, CONTRACTOR_LIABILITY). Before the composite
-- PK these resolved to the UK row, so a UAE liability certificate was checked against
-- the UK FCA register and a UAE ISO certificate against UKAS. These rows fix that by
-- pointing at the correct UAE authority (CBUAE, EIAC, DCD, DM, SIRA, DEWA, DED).

INSERT INTO plenum_cafm.compliance_verification_sources
    (certificate_type_code, country_code, cert_scope, verification_group, channel, register, source_url, api_available, refresh_cadence, notes, code_aliases)
VALUES
-- ---------------- Building (28) ----------------
('FIRE_SAFETY_CERT','UAE','building','Fire','website','Dubai Civil Defence (DCD)','https://www.dcd.gov.ae/','no','90d','UAE Fire and Life Safety Code certificate issued by Civil Defence','[]'::jsonb),
('FIRE_ALARM_TEST','UAE','building','Fire','website','Dubai Civil Defence (DCD)','https://www.dcd.gov.ae/','no','90d','Alarm ITM by a Civil Defence approved contractor','[]'::jsonb),
('FIRE_AMC','UAE','building','Fire','website','Dubai Civil Defence (DCD)','https://www.dcd.gov.ae/','no','90d','Annual Maintenance Contract must be with a CD-approved company','[]'::jsonb),
('FIRE_EXTINGUISHER','UAE','building','Fire','website','Dubai Civil Defence (DCD)','https://www.dcd.gov.ae/','no','90d','Extinguisher servicing by a CD-approved company','[]'::jsonb),
('FIRE_PUMP','UAE','building','Fire','website','Dubai Civil Defence (DCD)','https://www.dcd.gov.ae/','no','90d','Fire pump test by a CD-approved contractor','[]'::jsonb),
('SPRINKLER_TEST','UAE','building','Fire','website','Dubai Civil Defence (DCD)','https://www.dcd.gov.ae/','no','90d','Sprinkler ITM per UAE Fire and Life Safety Code (NOT the UK BAFE SP101 route)','[]'::jsonb),
('FIRE_DOOR','UAE','building','Fire','website','Dubai Civil Defence (DCD)','https://www.dcd.gov.ae/','no','90d','Fire door inspection per UAE Fire and Life Safety Code (NOT the UK FDIS route)','[]'::jsonb),
('KITCHEN_SUPPRESSION','UAE','building','Fire','website','Dubai Civil Defence (DCD)','https://www.dcd.gov.ae/','no','90d','Kitchen hood suppression servicing by a CD-approved company','[]'::jsonb),
('EMERGENCY_LIGHTING','UAE','building','Fire','website','Dubai Civil Defence (DCD)','https://www.dcd.gov.ae/','no','90d','Emergency lighting test per UAE Fire and Life Safety Code','[]'::jsonb),
('CCTV_COMPLIANCE','UAE','building','Security','website','SIRA','https://www.sira.gov.ae/','no','90d','CCTV must comply with SIRA; installer must hold a SIRA licence','[]'::jsonb),
('LIFT_INSPECTION','UAE','building','Lifts','website','Dubai Municipality','https://www.dm.gov.ae/','no','90d','Lift inspection certificate issued or accepted by the Municipality','[]'::jsonb),
('ELECTRICAL_SAFETY','UAE','building','Electrical','website','DEWA / local distribution authority','https://www.dewa.gov.ae/','no','90d','Electrical safety per DEWA regulations; contractor DEWA-registered','[]'::jsonb),
('EARTHING_LPS','UAE','building','Electrical','website','DEWA / local distribution authority','https://www.dewa.gov.ae/','no','90d','Earthing and lightning protection system test','[]'::jsonb),
('THERMOGRAPHY','UAE','building','Electrical','self_produced','Self-held',NULL,'n/a','n/a','Thermographic survey report is self-held','[]'::jsonb),
('COOLING_TOWER','UAE','building','Water','website','Dubai Municipality','https://www.dm.gov.ae/','no','90d','Cooling tower hygiene per Municipality public-health rules','[]'::jsonb),
('WATER_TANK_CLEANING','UAE','building','Water','website','Dubai Municipality','https://www.dm.gov.ae/','no','90d','Tank cleaning by a Municipality-approved company','[]'::jsonb),
('WATER_QUALITY','UAE','building','Water','data_dump','EIAC accredited laboratory','https://eiac.gov.ae/','no','weekly','Potable water test must come from an EIAC-accredited lab','[]'::jsonb),
('IAQ_TEST','UAE','building','HVAC','data_dump','EIAC accredited laboratory','https://eiac.gov.ae/','no','weekly','Indoor air quality test by an EIAC-accredited lab','[]'::jsonb),
('DUCT_CLEANING','UAE','building','HVAC','website','Dubai Municipality','https://www.dm.gov.ae/','no','90d','Duct cleaning by a Municipality-approved company','[]'::jsonb),
('REFRIGERANT_LOG','UAE','building','HVAC/F-Gas','self_produced','Self-held',NULL,'n/a','n/a','Refrigerant handling log is self-held','[]'::jsonb),
('PRESSURE_VESSEL','UAE','building','Pressure Systems','data_dump','EIAC accredited inspection body','https://eiac.gov.ae/','no','weekly','Pressure vessel examination by an EIAC-accredited inspection body','[]'::jsonb),
('LPG_GAS_SAFETY','UAE','building','Gas','website','Dubai Civil Defence / Municipality','https://www.dcd.gov.ae/','no','90d','LPG installation safety per Civil Defence and Municipality rules (NOT UK Gas Safe)','[]'::jsonb),
('PEST_CONTROL','UAE','building','Pest Control','website','Dubai Municipality','https://www.dm.gov.ae/','no','90d','Pest control by a Municipality-permitted company','[]'::jsonb),
('GREEN_BUILDING','UAE','building','Energy','website','Al Safat / Estidama green building','https://www.dm.gov.ae/','no','90d','Al Safat (Dubai) or Estidama (Abu Dhabi) green building rating (NOT a UK EPC)','[]'::jsonb),
('HSE_POLICY','UAE','building','HSE','self_produced','Self-held',NULL,'n/a','n/a','HSE policy statement is self-held','[]'::jsonb),
('RISK_ASSESSMENT','UAE','building','HSE','self_produced','Self-held',NULL,'n/a','n/a','Risk assessment register is a self-held living document','[]'::jsonb),
('PUBLIC_LIABILITY','UAE','building','Insurance','website','CBUAE insurance supervision','https://www.centralbank.ae/en/our-operations/insurance-supervision/','no','90d','UAE insurer authorisation via the Central Bank - NOT the UK FCA register','[]'::jsonb),
('WORKMEN_INSURANCE','UAE','building','Insurance','website','CBUAE insurance supervision','https://www.centralbank.ae/en/our-operations/insurance-supervision/','no','90d','Workmen compensation insurer authorised by the Central Bank of the UAE','[]'::jsonb),
-- ---------------- Vendor (17) ----------------
('TRADE_LICENCE','UAE','vendor','General','website','DED / Department of Economy and Tourism','https://u.ae/en/information-and-services/business/doing-business','no','90d','Company trade licence verified on the issuing emirate DED portal','[]'::jsonb),
('CD_APPROVED_CONTRACTOR','UAE','vendor','Fire','website','Dubai Civil Defence approved companies','https://www.dcd.gov.ae/','no','90d','Civil Defence maintains the approved fire-contractor list','[]'::jsonb),
('FIRE_TECH_CERT','UAE','vendor','Fire','website','Dubai Civil Defence (DCD)','https://www.dcd.gov.ae/','no','90d','Fire technician competency accepted by Civil Defence','[]'::jsonb),
('ELEC_CONTRACTOR','UAE','vendor','Electrical','website','DEWA registered contractors','https://www.dewa.gov.ae/','no','90d','Electrical contractor must be DEWA-registered (NOT UK NICEIC/NAPIT)','[]'::jsonb),
('HVAC_CONTRACTOR','UAE','vendor','HVAC','website','Dubai Municipality','https://www.dm.gov.ae/','no','90d','HVAC contractor approval held by the Municipality','[]'::jsonb),
('REFRIGERANT_HANDLER','UAE','vendor','HVAC/F-Gas','website','MoCCAE / Municipality','https://www.moccae.gov.ae/','no','90d','Refrigerant handler permit per MoCCAE or Municipality (NOT UK REFCOM)','[]'::jsonb),
('LIFT_MAINT_APPROVAL','UAE','vendor','Lifts','website','Dubai Municipality','https://www.dm.gov.ae/','no','90d','Lift maintenance company approval from the Municipality','[]'::jsonb),
('ASBESTOS_REMOVAL','UAE','vendor','Asbestos','website','MoCCAE / Municipality environment dept','https://www.moccae.gov.ae/','no','90d','Asbestos removal permit issued by the environment authority (NOT the UK HSE licence)','[]'::jsonb),
('TANK_CLEANING_APPROVAL','UAE','vendor','Water','website','Dubai Municipality','https://www.dm.gov.ae/','no','90d','Water tank cleaning company approval from the Municipality','[]'::jsonb),
('PEST_COMPANY_PERMIT','UAE','vendor','Pest Control','website','Dubai Municipality','https://www.dm.gov.ae/','no','90d','Pest control company permit from the Municipality (NOT UK BPCA)','[]'::jsonb),
('SECURITY_COMPANY_LICENCE','UAE','vendor','Security','website','SIRA','https://www.sira.gov.ae/','no','90d','Security company licence issued by SIRA (NOT the UK SIA)','[]'::jsonb),
('INSPECTION_BODY_EIAC','UAE','vendor','General','data_dump','EIAC accredited bodies','https://eiac.gov.ae/','no','weekly','EIAC publishes its accredited inspection and certification bodies','[]'::jsonb),
('EQM','UAE','vendor','General','website','MoIAT / Emirates Quality Mark','https://www.moiat.gov.ae/','no','90d','Emirates Quality Mark issued by MoIAT','[]'::jsonb),
('ISO_9001','UAE','vendor','General','data_dump','EIAC accredited certification body','https://eiac.gov.ae/','no','weekly','UAE ISO certificate must come from an EIAC-accredited body, not UKAS CertCheck','[]'::jsonb),
('ISO_14001','UAE','vendor','General','data_dump','EIAC accredited certification body','https://eiac.gov.ae/','no','weekly','UAE ISO certificate must come from an EIAC-accredited body, not UKAS CertCheck','[]'::jsonb),
('ISO_45001','UAE','vendor','General','data_dump','EIAC accredited certification body','https://eiac.gov.ae/','no','weekly','UAE ISO certificate must come from an EIAC-accredited body, not UKAS CertCheck','[]'::jsonb),
('CONTRACTOR_LIABILITY','UAE','vendor','Insurance','website','CBUAE insurance supervision','https://www.centralbank.ae/en/our-operations/insurance-supervision/','no','90d','UAE insurer authorisation via the Central Bank - NOT the UK FCA register','[]'::jsonb)
ON CONFLICT (certificate_type_code, country_code) DO UPDATE SET
    cert_scope = EXCLUDED.cert_scope, verification_group = EXCLUDED.verification_group,
    channel = EXCLUDED.channel, register = EXCLUDED.register, source_url = EXCLUDED.source_url,
    api_available = EXCLUDED.api_available, refresh_cadence = EXCLUDED.refresh_cadence,
    notes = EXCLUDED.notes, code_aliases = EXCLUDED.code_aliases, updated_at = now();
