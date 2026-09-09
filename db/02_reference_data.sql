--
-- PostgreSQL database dump
--

\restrict VxoNDRFP3vDw27ZcdfB9QKbAARMDKm7CqklyCu068ETSReGPcW1ubAyB1Lo9wWr

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg12+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg12+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: compliance_verification_sources; Type: TABLE DATA; Schema: plenum_cafm; Owner: -
--

COPY plenum_cafm.compliance_verification_sources (certificate_type_code, cert_scope, verification_group, channel, register, source_url, api_available, refresh_cadence, notes, code_aliases, country_code, created_at, updated_at) FROM stdin;
FRA	building	Fire protection	website	Assessor body (IFSM/IFE/FPA/BAFE SP205)	\N	no	90d	No central register; verify assessor membership manually	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
FIRE_ALARM_SVC	building	Fire protection	website	BAFE	https://bafe.my.salesforce-sites.com/wb/wbCompanyVerify	no	90d	\N	["FIRE_ALARM_SERVICE"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
EMERGENCY_LIGHTING	building	Electrical	private_api	NICEIC/NAPIT	https://www.niceic.com/find-a-contractor	partner	90d	\N	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
SPRINKLER_TEST	building	Fire protection	website	BAFE SP101	https://bafe.my.salesforce-sites.com/wb/wbCompanyVerify	no	90d	\N	["SPRINKLER"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
FIRE_DOOR	building	Fire protection	website	FDIS/BWF-CERTIFIRE	https://www.fdis.org.uk/	no	90d	\N	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
EICR	building	Electrical	private_api	NICEIC/NAPIT	https://www.niceic.com/find-a-contractor	partner	90d	\N	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
EIC	building	Electrical	private_api	NICEIC/NAPIT	https://www.niceic.com/find-a-contractor	partner	90d	\N	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
PAT	building	Electrical	self_produced	Self-held	\N	n/a	n/a	Register is self-held; no external check	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
GAS_CP17	building	Gas	private_api	Gas Safe Register	https://www.gassaferegister.co.uk/find-an-engineer/	partner	90d	\N	["CP17", "GAS_SAFE_CP17"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
BOILER_SVC	building	Gas	private_api	Gas Safe Register	https://www.gassaferegister.co.uk/find-an-engineer/	partner	90d	\N	["BOILER_SERVICE"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
LEGIONELLA_RA	building	Water / Legionella	website	LCA	https://www.legionellacontrol.org.uk/members/	no	90d	\N	["L8_RISK", "LEGIONELLA"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
LEGIONELLA_LOG	building	Water / Legionella	self_produced	Self-held	\N	n/a	n/a	Monitoring log; no external register	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
CWST_INSPECTION	building	Water / Legionella	website	LCA	https://www.legionellacontrol.org.uk/members/	no	90d	\N	["COLD_WATER_TANK"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
WSE_PRESSURE	building	Lifts	website	Insurance inspection body	https://www.hse.gov.uk/pressure-systems/written-scheme.htm	no	90d	Competent person via insurer; no register	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
LOLER_LIFT	building	Lifts	website	HSE competent-persons	https://www.hse.gov.uk/work-equipment-machinery/thorough-examinations-lifting-equipment.htm	no	90d	\N	["LOLER"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
ASBESTOS_SURVEY	building	Asbestos	data_dump	UKAS	https://www.ukas.com/find-an-organisation/	no	weekly	Bulk directory ingest	["UKAS_ASBESTOS"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
ASBESTOS_REGISTER	building	Asbestos	self_produced	Self-held	\N	n/a	n/a	Living document	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
EPC	building	Energy	public_api	GOV.UK Find Energy Certificate	https://find-energy-certificate.service.gov.uk/find-a-certificate/search-by-reference-number	yes	90d	Open API	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
TM44	building	Energy	public_api	GOV.UK Find Energy Certificate	https://find-energy-certificate.service.gov.uk/find-a-certificate/search-by-reference-number	yes	90d	AC inspection lodged on EPC register	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
DEC	building	Energy	public_api	GOV.UK Find Energy Certificate	https://find-energy-certificate.service.gov.uk/find-a-certificate/search-by-reference-number	yes	90d	Open API	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
ESOS	building	Energy	public_api	Companies House / Environment Agency	https://find-and-update.company-information.service.gov.uk/	yes	90d	Company check via CH API; EA notification website	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
FGAS_LEAK	building	F-Gas	website	REFCOM	https://www.refcom.org.uk/find-a-company/	no	90d	\N	["FGAS"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
HS_POLICY	building	—	self_produced	Self-held	\N	n/a	n/a	Policy statement	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
RISK_REGISTER	building	—	self_produced	Self-held	\N	n/a	n/a	Risk assessment register	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
EL_INSURANCE	building	Insurance	public_api	FCA Register	https://register.fca.org.uk/	yes	90d	Insurer authorisation via FCA API	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
HRB_REGISTRATION	building	Building safety	website	Building Safety Regulator	https://www.hse.gov.uk/building-safety/registration.htm	no	90d	\N	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
SAFETY_CASE	building	Building safety	website	Building Safety Regulator	https://www.hse.gov.uk/building-safety/registration.htm	no	90d	\N	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
GASSAFE_COMPANY	vendor	Gas	private_api	Gas Safe Register	https://www.gassaferegister.co.uk/find-an-engineer/	partner	90d	\N	["GAS_SAFE"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
ACS_GAS_CARD	vendor	Gas	private_api	Gas Safe Register	https://www.gassaferegister.co.uk/find-an-engineer/	partner	90d	Individual engineer card check	["ACS_CARD"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
NICEIC_CONTRACTOR	vendor	Electrical	private_api	NICEIC	https://www.niceic.com/find-a-contractor	partner	90d	\N	["NICEIC"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
NAPIT_REG	vendor	Electrical	private_api	NAPIT	https://www.napit.org.uk/find-an-installer	partner	90d	\N	["NAPIT"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
CG_2382	vendor	Electrical	cert_format	City & Guilds	https://www.cityandguilds.com/	no	n/a	Qualification; no live register	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
BAFE_SP203	vendor	Fire protection	website	BAFE	https://bafe.my.salesforce-sites.com/wb/wbCompanyVerify	no	90d	\N	["BAFE", "BAFE_SP203_1"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
NSI_GOLD_FIRE	vendor	Fire protection	website	NSI	https://www.nsi.org.uk/find-a-company/	no	90d	\N	["NSI"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
BAFE_SP101	vendor	Fire protection	website	BAFE	https://bafe.my.salesforce-sites.com/wb/wbCompanyVerify	no	90d	\N	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
BAFE_SP105	vendor	Fire protection	website	BAFE	https://bafe.my.salesforce-sites.com/wb/wbCompanyVerify	no	90d	\N	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
LEIA_MEMBER	vendor	Lifts	website	LEIA	https://www.leia.co.uk/	no	90d	\N	["LEIA"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
LOLER_CP	vendor	Lifts	website	HSE competent-persons	https://www.hse.gov.uk/work-equipment-machinery/thorough-examinations-lifting-equipment.htm	no	90d	\N	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
ASBESTOS_LICENCE	vendor	Asbestos	data_dump	HSE licensed contractors	https://www.ukata.org.uk/library/hse-licensed-asbestos-removal-contractors-register/	no	weekly	Full HSE licensed-contractor list + searchable LARC finder (via UKATA)	["HSE_ASBESTOS_LICENCE"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
UKAS_ASBESTOS	vendor	Asbestos	data_dump	UKAS	https://www.ukas.com/find-an-organisation/	no	weekly	Bulk directory ingest	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
BOHS_P40x	vendor	Asbestos	cert_format	BOHS/RSPH	https://www.bohs.org/learning/qualifications/p400-series/	no	n/a	Individual competency; no live register	["P402", "P403", "P404"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
LCA_REG	vendor	Water / Legionella	website	LCA	https://www.legionellacontrol.org.uk/members/	no	90d	\N	["LCA"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
LEGIONELLA_COMP	vendor	Water / Legionella	cert_format	BOHS/WMSoc	https://www.wmsoc.org.uk/	no	n/a	Individual competency	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
REFCOM_COMPANY	vendor	F-Gas	website	REFCOM	https://www.refcom.org.uk/find-a-company/	no	90d	\N	["REFCOM"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
CG_2079	vendor	F-Gas	website	REFCOM (technician)	https://www.refcom.org.uk/find-a-company/	no	90d	Individual technician checkable via REFCOM	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
CHAS_SSIP	vendor	General / SSIP	private_api	SSIP / CHAS	https://ssip.org.uk/members/	partner	90d	\N	["CHAS", "SSIP", "CHAS_SSIP"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
ISO_9001	vendor	General / SSIP	website	UKAS CertCheck (live)	https://certcheck.ukas.com/	no	weekly	Live UKAS CertCheck DB — search by cert number or company	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
ISO_14001	vendor	General / SSIP	website	UKAS CertCheck (live)	https://certcheck.ukas.com/	no	weekly	Live UKAS CertCheck DB — search by cert number or company	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
CONTRACTOR_EL	vendor	Insurance	public_api	FCA Register	https://register.fca.org.uk/	yes	90d	Insurer authorisation via FCA API	["CONTRACTOR_EL_INSURANCE"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
CONTRACTOR_PL	vendor	Insurance	public_api	FCA Register	https://register.fca.org.uk/	yes	90d	Insurer authorisation via FCA API	["CONTRACTOR_PL_INSURANCE"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
SIA_ACS	vendor	Security	data_dump	SIA ACS register	https://www.services.sia.homeoffice.gov.uk/Pages/acs-roac.aspx?all	no	weekly	Register of Approved Contractors — full list (Ctrl+F by company)	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
SIA_LICENCE	vendor	Security	website	SIA licence check	https://www.sia.homeoffice.gov.uk/Pages/licensing-check.aspx	no	90d	Per-record individual check	["SIA", "SIA_INDIVIDUAL"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
NSI_GOLD_SEC	vendor	Security	website	NSI	https://www.nsi.org.uk/find-a-company/	no	90d	\N	[]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
BPCA_MEMBER	vendor	Pest control	data_dump	BPCA	https://bpca.org.uk/find-a-pest-controller/	no	weekly	Published member list ingest	["BPCA"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
PESTICIDE_PAx	vendor	Pest control	data_dump	BASIS PROMPT	https://www.basis-reg.co.uk/register/prompt	no	weekly	PROMPT register ingest	["PA1", "PA2", "PA6"]	UK	2026-09-09 07:30:05.693509+00	2026-09-09 07:30:05.693509+00
FIRE_SAFETY_CERT	building	Fire	website	Dubai Civil Defence (DCD)	https://www.dcd.gov.ae/	no	90d	UAE Fire and Life Safety Code certificate issued by Civil Defence	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
FIRE_ALARM_TEST	building	Fire	website	Dubai Civil Defence (DCD)	https://www.dcd.gov.ae/	no	90d	Alarm ITM by a Civil Defence approved contractor	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
FIRE_AMC	building	Fire	website	Dubai Civil Defence (DCD)	https://www.dcd.gov.ae/	no	90d	Annual Maintenance Contract must be with a CD-approved company	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
FIRE_EXTINGUISHER	building	Fire	website	Dubai Civil Defence (DCD)	https://www.dcd.gov.ae/	no	90d	Extinguisher servicing by a CD-approved company	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
FIRE_PUMP	building	Fire	website	Dubai Civil Defence (DCD)	https://www.dcd.gov.ae/	no	90d	Fire pump test by a CD-approved contractor	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
SPRINKLER_TEST	building	Fire	website	Dubai Civil Defence (DCD)	https://www.dcd.gov.ae/	no	90d	Sprinkler ITM per UAE Fire and Life Safety Code (NOT the UK BAFE SP101 route)	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
FIRE_DOOR	building	Fire	website	Dubai Civil Defence (DCD)	https://www.dcd.gov.ae/	no	90d	Fire door inspection per UAE Fire and Life Safety Code (NOT the UK FDIS route)	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
KITCHEN_SUPPRESSION	building	Fire	website	Dubai Civil Defence (DCD)	https://www.dcd.gov.ae/	no	90d	Kitchen hood suppression servicing by a CD-approved company	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
EMERGENCY_LIGHTING	building	Fire	website	Dubai Civil Defence (DCD)	https://www.dcd.gov.ae/	no	90d	Emergency lighting test per UAE Fire and Life Safety Code	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
CCTV_COMPLIANCE	building	Security	website	SIRA	https://www.sira.gov.ae/	no	90d	CCTV must comply with SIRA; installer must hold a SIRA licence	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
LIFT_INSPECTION	building	Lifts	website	Dubai Municipality	https://www.dm.gov.ae/	no	90d	Lift inspection certificate issued or accepted by the Municipality	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
ELECTRICAL_SAFETY	building	Electrical	website	DEWA / local distribution authority	https://www.dewa.gov.ae/	no	90d	Electrical safety per DEWA regulations; contractor DEWA-registered	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
EARTHING_LPS	building	Electrical	website	DEWA / local distribution authority	https://www.dewa.gov.ae/	no	90d	Earthing and lightning protection system test	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
THERMOGRAPHY	building	Electrical	self_produced	Self-held	\N	n/a	n/a	Thermographic survey report is self-held	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
COOLING_TOWER	building	Water	website	Dubai Municipality	https://www.dm.gov.ae/	no	90d	Cooling tower hygiene per Municipality public-health rules	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
WATER_TANK_CLEANING	building	Water	website	Dubai Municipality	https://www.dm.gov.ae/	no	90d	Tank cleaning by a Municipality-approved company	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
WATER_QUALITY	building	Water	data_dump	EIAC accredited laboratory	https://eiac.gov.ae/	no	weekly	Potable water test must come from an EIAC-accredited lab	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
IAQ_TEST	building	HVAC	data_dump	EIAC accredited laboratory	https://eiac.gov.ae/	no	weekly	Indoor air quality test by an EIAC-accredited lab	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
DUCT_CLEANING	building	HVAC	website	Dubai Municipality	https://www.dm.gov.ae/	no	90d	Duct cleaning by a Municipality-approved company	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
REFRIGERANT_LOG	building	HVAC/F-Gas	self_produced	Self-held	\N	n/a	n/a	Refrigerant handling log is self-held	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
PRESSURE_VESSEL	building	Pressure Systems	data_dump	EIAC accredited inspection body	https://eiac.gov.ae/	no	weekly	Pressure vessel examination by an EIAC-accredited inspection body	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
LPG_GAS_SAFETY	building	Gas	website	Dubai Civil Defence / Municipality	https://www.dcd.gov.ae/	no	90d	LPG installation safety per Civil Defence and Municipality rules (NOT UK Gas Safe)	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
PEST_CONTROL	building	Pest Control	website	Dubai Municipality	https://www.dm.gov.ae/	no	90d	Pest control by a Municipality-permitted company	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
GREEN_BUILDING	building	Energy	website	Al Safat / Estidama green building	https://www.dm.gov.ae/	no	90d	Al Safat (Dubai) or Estidama (Abu Dhabi) green building rating (NOT a UK EPC)	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
HSE_POLICY	building	HSE	self_produced	Self-held	\N	n/a	n/a	HSE policy statement is self-held	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
RISK_ASSESSMENT	building	HSE	self_produced	Self-held	\N	n/a	n/a	Risk assessment register is a self-held living document	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
PUBLIC_LIABILITY	building	Insurance	website	CBUAE insurance supervision	https://www.centralbank.ae/en/our-operations/insurance-supervision/	no	90d	UAE insurer authorisation via the Central Bank - NOT the UK FCA register	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
WORKMEN_INSURANCE	building	Insurance	website	CBUAE insurance supervision	https://www.centralbank.ae/en/our-operations/insurance-supervision/	no	90d	Workmen compensation insurer authorised by the Central Bank of the UAE	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
TRADE_LICENCE	vendor	General	website	DED / Department of Economy and Tourism	https://u.ae/en/information-and-services/business/doing-business	no	90d	Company trade licence verified on the issuing emirate DED portal	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
CD_APPROVED_CONTRACTOR	vendor	Fire	website	Dubai Civil Defence approved companies	https://www.dcd.gov.ae/	no	90d	Civil Defence maintains the approved fire-contractor list	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
FIRE_TECH_CERT	vendor	Fire	website	Dubai Civil Defence (DCD)	https://www.dcd.gov.ae/	no	90d	Fire technician competency accepted by Civil Defence	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
ELEC_CONTRACTOR	vendor	Electrical	website	DEWA registered contractors	https://www.dewa.gov.ae/	no	90d	Electrical contractor must be DEWA-registered (NOT UK NICEIC/NAPIT)	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
HVAC_CONTRACTOR	vendor	HVAC	website	Dubai Municipality	https://www.dm.gov.ae/	no	90d	HVAC contractor approval held by the Municipality	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
REFRIGERANT_HANDLER	vendor	HVAC/F-Gas	website	MoCCAE / Municipality	https://www.moccae.gov.ae/	no	90d	Refrigerant handler permit per MoCCAE or Municipality (NOT UK REFCOM)	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
LIFT_MAINT_APPROVAL	vendor	Lifts	website	Dubai Municipality	https://www.dm.gov.ae/	no	90d	Lift maintenance company approval from the Municipality	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
ASBESTOS_REMOVAL	vendor	Asbestos	website	MoCCAE / Municipality environment dept	https://www.moccae.gov.ae/	no	90d	Asbestos removal permit issued by the environment authority (NOT the UK HSE licence)	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
TANK_CLEANING_APPROVAL	vendor	Water	website	Dubai Municipality	https://www.dm.gov.ae/	no	90d	Water tank cleaning company approval from the Municipality	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
PEST_COMPANY_PERMIT	vendor	Pest Control	website	Dubai Municipality	https://www.dm.gov.ae/	no	90d	Pest control company permit from the Municipality (NOT UK BPCA)	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
SECURITY_COMPANY_LICENCE	vendor	Security	website	SIRA	https://www.sira.gov.ae/	no	90d	Security company licence issued by SIRA (NOT the UK SIA)	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
INSPECTION_BODY_EIAC	vendor	General	data_dump	EIAC accredited bodies	https://eiac.gov.ae/	no	weekly	EIAC publishes its accredited inspection and certification bodies	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
EQM	vendor	General	website	MoIAT / Emirates Quality Mark	https://www.moiat.gov.ae/	no	90d	Emirates Quality Mark issued by MoIAT	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
ISO_9001	vendor	General	data_dump	EIAC accredited certification body	https://eiac.gov.ae/	no	weekly	UAE ISO certificate must come from an EIAC-accredited body, not UKAS CertCheck	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
ISO_14001	vendor	General	data_dump	EIAC accredited certification body	https://eiac.gov.ae/	no	weekly	UAE ISO certificate must come from an EIAC-accredited body, not UKAS CertCheck	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
ISO_45001	vendor	General	data_dump	EIAC accredited certification body	https://eiac.gov.ae/	no	weekly	UAE ISO certificate must come from an EIAC-accredited body, not UKAS CertCheck	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
CONTRACTOR_LIABILITY	vendor	Insurance	website	CBUAE insurance supervision	https://www.centralbank.ae/en/our-operations/insurance-supervision/	no	90d	UAE insurer authorisation via the Central Bank - NOT the UK FCA register	[]	UAE	2026-09-09 07:30:05.734905+00	2026-09-09 07:30:05.734905+00
US_FIRE_ALARM_NFPA72	building	Fire protection	website	State Fire Marshal / AHJ	https://www.usfa.fema.gov/	no	90d	NFPA 72 ITM record held by the AHJ; alarm contractor licensed at state level	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_SPRINKLER_NFPA25	building	Fire protection	website	State Fire Marshal / AHJ	https://www.usfa.fema.gov/	no	90d	NFPA 25 ITM report; sprinkler contractor licence verified at state level	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_FIRE_EXT_NFPA10	building	Fire protection	website	State Fire Marshal / AHJ	https://www.usfa.fema.gov/	no	90d	NFPA 10 annual maintenance tag; servicing company licensed by state	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_FIRE_DOOR_NFPA80	building	Fire protection	website	State Fire Marshal / AHJ	https://www.usfa.fema.gov/	no	90d	NFPA 80 annual fire door inspection; inspector qualification per AHJ	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_EMERGENCY_LIGHT_NFPA101	building	Electrical	website	State Fire Marshal / AHJ	https://www.usfa.fema.gov/	no	90d	NFPA 101 emergency and exit lighting test record	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_GENERATOR_NFPA110	building	Electrical	self_produced	Self-held	\N	n/a	n/a	NFPA 110 generator test log is self-held; no external register	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_ELEVATOR_A17	building	Lifts	website	State elevator authority	https://www.asme.org/codes-standards	no	90d	ASME A17.1 inspection; operating certificate issued by the state elevator authority	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_BOILER_INSPECTION	building	Pressure systems	website	National Board / state boiler authority	https://www.nationalboard.org/index.aspx?pageID=178	no	90d	National Board commission number verifiable; jurisdiction issues the operating certificate	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_BACKFLOW	building	Water / plumbing	website	State / water purveyor tester registry	https://www.epa.gov/dwreginfo	no	90d	Assembly test filed with the local water purveyor; tester certified by state	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_LEGIONELLA_ASHRAE188	building	Water / Legionella	self_produced	Self-held	\N	n/a	n/a	ASHRAE 188 water management plan is a self-held living document	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_ASBESTOS_OM	building	Asbestos	website	EPA AHERA / state asbestos program	https://www.epa.gov/asbestos/asbestos-laws-and-regulations	no	90d	AHERA O&M plan; abatement contractors licensed per state asbestos program	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_EPA_608	vendor	HVAC / F-Gas	website	EPA 608 certifying organization	https://www.epa.gov/section608/section-608-technician-certification-0	no	90d	Technician card issued by an EPA-approved certifying body; verify with the issuer	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_LEAD_RRP	vendor	Environmental	public_api	SAM.gov entity register + EPA Lead-Safe firm search	https://sam.gov/	yes	90d	Firm entity verified against the federal SAM.gov register. The EPA Lead-Safe firm directory (cdxapps.epa.gov/ocspp-oppt-lead/firm-location-search) has no public API, so the RRP certification itself is confirmed there manually.	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_NICET_FIRE	vendor	Fire protection	website	NICET certification verification	https://www.nicet.org/about-us/frequently-asked-questions/verification/	no	90d	NICET publishes a per-record certification verification lookup	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_OSHA_30	vendor	HSE	website	OSHA Outreach card check	https://www.osha.gov/training/outreach	no	90d	OSHA 30-hour card verified via the DOL Outreach card-verification service	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_STATE_CONTRACTOR_LICENSE	vendor	General	data_dump	State contractor licence registers (WA L&I open data)	https://data.wa.gov/resource/m8qx-ubtq.json	yes	weekly	State-issued licence. Washington L&I publishes the full licence register as open data (Socrata dataset m8qx-ubtq) - ingested weekly and matched locally by licence number. Other states are verified on their own board site until their open data is added.	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_GL_INSURANCE	vendor	Insurance	public_api	SAM.gov entity register (insured contractor)	https://sam.gov/	yes	90d	No national US API exists for insurance carriers (NAIC is web-only), so the INSURED CONTRACTOR entity is verified against the federal SAM.gov register (active registration + not excluded). Carrier licensing is confirmed manually on NAIC / the state DOI.	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
US_WORKERS_COMP	vendor	Insurance	public_api	SAM.gov entity register (insured contractor)	https://sam.gov/	yes	90d	Insured contractor entity verified against the federal SAM.gov register (active + not excluded). Workers-comp carrier licensing is confirmed manually on NAIC / the state DOI.	[]	US	2026-09-09 07:30:05.766263+00	2026-09-09 07:30:05.766263+00
\.


--
-- Data for Name: country_certificate_packs; Type: TABLE DATA; Schema: plenum_cafm; Owner: -
--

COPY plenum_cafm.country_certificate_packs (id, pack_id, country_code, pack_version, certificate_type_code, certificate_type_name, certificate_scope, trade_category, regulation_reference, regulation_url, frequency_months, issuing_body, required_contractor_accreditation, verification_url, key_fields_schema, alert_thresholds, is_active, created_at, updated_at, change_notes) FROM stdin;
\.


--
-- Data for Name: regulation_packs; Type: TABLE DATA; Schema: plenum_cafm; Owner: -
--

COPY plenum_cafm.regulation_packs (pack_id, standard, required_types, benchmark_source, standing, standing_note, created_at) FROM stdin;
eb2daf33-f14b-447c-9f68-55bf1e9777e1	Rolling portfolio benchmark	{}	Median EUI of comparable buildings in the portfolio	none	no operational standard · comparable buildings in the portfolio	2026-09-09 07:30:07.07033+00
1e97fbaa-89dd-46cf-a430-0127aa8d051c	BCA Benchmarking Report	{}	BCA Building Energy Benchmarking Report	mandatory_submission	submission mandatory · rating voluntary	2026-09-09 07:30:07.07033+00
343b2a98-8662-4e97-b02c-b868d370097a	CIBSE TM46	{FRA,EICR,CP17,L8_RISK,EMERGENCY_LIGHTING}	CIBSE TM46 category benchmark	guidance	guidance · EPC E law, EPC B proposed 2031	2026-09-09 07:30:07.07033+00
0d69f204-6c86-4d12-8d8b-50267772faf7	Energy Star · ASHRAE 100	{US_FIRE_ALARM_NFPA72,US_SPRINKLER_NFPA25}	Energy Star Portfolio Manager	enacted	enacted, city-scoped · NYC LL97	2026-09-09 07:30:07.07033+00
\.


--
-- PostgreSQL database dump complete
--

\unrestrict VxoNDRFP3vDw27ZcdfB9QKbAARMDKm7CqklyCu068ETSReGPcW1ubAyB1Lo9wWr

