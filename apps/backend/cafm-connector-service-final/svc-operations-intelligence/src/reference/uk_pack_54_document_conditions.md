# UK Compliance Pack v1.1 — 54 document generation conditions

Total types: **54**
- Building: **27**
- Vendor: **27**

## How Single Door routes a file to Feature A

A document triggers Compliance Engine when **any** of these match:

1. **Filename** contains type tokens (e.g. `FRA`, `Fire_Risk`, `EICR`, `LOLER`, `Gas_Safe`)
2. **Chat message** contains compliance keywords (`certificate`, `compliance`, `fire risk`, …)
3. **Document body text** contains those tokens (Word/PDF is peeked)

Platform always expects these canonical fields for extract/upsert:
`certificate_number`, `issue_date`, `expiry_date` (Review date → expiry for FRA/L8).

Suggested filename pattern:
`NN_<TypeCode_or_Name>_<SiteOrVendor>.docx`
Example: `01_Fire_Risk_Assessment_FRA_Kingsgate.docx`

---

### 1. `FRA` — Fire Risk Assessment (FRA) (Building)

- **Trade:** Fire
- **Regulation:** Regulatory Reform (Fire Safety) Order 2005 (SI 2005/1541)
- **Frequency:** Annual review minimum; after significant material change; no fixed statutory expiry
- **Issuing body:** Qualified fire risk assessor — IFSM, IFE, FPA, or BAFE SP205 membership recommended
- **Required contractor accreditation:** BAFE SP205
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Building address`
- `Date`
- `Assessor name and qualification`
- `Scope`
- `Significant findings`
- `Persons at risk`
- `Existing precautions`
- `Action plan (High`
- `Medium`
- `Low priority)`
- `Completion dates`
- `Review date`
- `Assessor signature`

**Word pack source columns:** Building address | Date | Assessor name and qualification | Scope | Significant findings | Persons at risk | Existing precautions | Action plan (High / Medium / Low priority) | Completion dates | Review date | Assessor signature

**Suggested filename:** `01_FRA_{SiteOrVendor}.docx` (or include `FRA` / `Fire Risk Assessment` in the name)

---

### 2. `FIRE_ALARM_SERVICE` — Fire Alarm System Service Certificate (Building)

- **Trade:** Fire
- **Regulation:** BS 5839-1:2017; Regulatory Reform (Fire Safety) Order 2005
- **Frequency:** Quarterly inspection; 6-monthly or annual full service per BS 5839-1
- **Issuing body:** BAFE SP203-1 registered firm or NSI/SSAIB-approved company
- **Required contractor accreditation:** BAFE SP203-1
- **Verification URL:** https://www.bafe.org.uk/find-a-bafe-registered-company/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Site address`
- `System type`
- `grade`
- `Panel ID`
- `Date`
- `Engineer name`
- `BAFE no.`
- `Zones tested`
- `Faults (C1`
- `C2)`
- `Remedial actions`
- `Client signature`
- `Next service due`

**Word pack source columns:** Site address | System type/grade | Panel ID | Date | Engineer name/BAFE no. | Zones tested | Faults (C1/C2) | Remedial actions | Client signature | Next service due

**Suggested filename:** `02_FIRE_ALARM_SERVICE_{SiteOrVendor}.docx` (or include `FIRE ALARM SERVICE` / `Fire Alarm System Service Certificate` in the name)

---

### 3. `EMERGENCY_LIGHTING` — Emergency Lighting Test Certificate (Building)

- **Trade:** Fire
- **Regulation:** BS 5266-1:2011; BS EN 50172; FSO 2005
- **Frequency:** Monthly flick test (self-test log); Annual 3-hour duration test
- **Issuing body:** NICEIC/NAPIT-registered electrician or specialist fire systems firm
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Site`
- `Test date`
- `Test type (monthly`
- `annual)`
- `Luminaire count`
- `Duration achieved`
- `Failures`
- `Pass`
- `Fail`
- `Remedial actions`
- `Next test due`

**Word pack source columns:** Site | Test date | Test type (monthly/annual) | Luminaire count | Duration achieved | Failures | Pass/Fail | Remedial actions | Next test due

**Suggested filename:** `03_EMERGENCY_LIGHTING_{SiteOrVendor}.docx` (or include `EMERGENCY LIGHTING` / `Emergency Lighting Test Certificate` in the name)

---

### 4. `SPRINKLER` — Sprinkler / Suppression System Annual Test Certificate (Building)

- **Trade:** Fire
- **Regulation:** BS EN 12845; LPC Rules; FSO 2005
- **Frequency:** Quarterly checks; Annual main test; 5-yearly full trip test
- **Issuing body:** BAFE SP101 / LPS 1048-registered suppression contractor
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Site`
- `System type`
- `Date`
- `Inspector name`
- `BAFE no.`
- `Pressure test`
- `Flow switch test`
- `Control valve positions`
- `Deficiencies`
- `Corrective actions`
- `Next inspection`

**Word pack source columns:** Site | System type | Date | Inspector name/BAFE no. | Pressure test | Flow switch test | Control valve positions | Deficiencies | Corrective actions | Next inspection

**Suggested filename:** `04_SPRINKLER_{SiteOrVendor}.docx` (or include `SPRINKLER` / `Sprinkler / Suppression System Annual Test Certificate` in the name)

---

### 5. `FIRE_DOOR` — Fire Door Inspection Report (Building)

- **Trade:** Fire
- **Regulation:** Building Safety Act 2022 (HRBs); BS 8214:2016; FSO 2005
- **Frequency:** HRBs: quarterly (communal doors), annual (flat entrance doors). Other commercial: annual
- **Issuing body:** Certificated inspector — BWF-CERTIFIRE, BM TRADA Q-Mark, or FDIS trained
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Building`
- `Door ID`
- `location`
- `Inspector cert no.`
- `Date`
- `Gap measurements`
- `Seal condition`
- `Closer function`
- `Hold-open device`
- `Signage`
- `Ironmongery`
- `Pass`
- `Refer`
- `Fail`
- `Priority (Urgent`
- `Routine)`

**Word pack source columns:** Building | Door ID/location | Inspector cert no. | Date | Gap measurements | Seal condition | Closer function | Hold-open device | Signage | Ironmongery | Pass/Refer/Fail | Priority (Urgent/Routine)

**Suggested filename:** `05_FIRE_DOOR_{SiteOrVendor}.docx` (or include `FIRE DOOR` / `Fire Door Inspection Report` in the name)

---

### 6. `EICR` — Electrical Installation Condition Report (EICR) (Building)

- **Trade:** Electrical
- **Regulation:** Electricity at Work Regulations 1989 (SI 1989/635); BS 7671:2018+A4:2026
- **Frequency:** —
- **Issuing body:** NICEIC Approved Contractor / NAPIT / ECA member firm
- **Required contractor accreditation:** NICEIC
- **Verification URL:** https://www.niceic.com/find-a-contractor

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Client name`
- `address`
- `Inspector name, qualification, NICEIC`
- `NAPIT no.`
- `Inspection date`
- `Scope`
- `limitations`
- `Observations: C1 (danger)`
- `C2 (potentially dangerous)`
- `C3 (improvement)`
- `FI (further investigation)`
- `Overall: Satisfactory`
- `Unsatisfactory`
- `Next inspection due`
- `Signatures`

**Word pack source columns:** Client name/address | Inspector name, qualification, NICEIC/NAPIT no. | Inspection date | Scope/limitations | Observations: C1 (danger) / C2 (potentially dangerous) / C3 (improvement) / FI (further investigation) | Overall: Satisfactory / Unsatisfactory | Next inspection due | Signatures

**Suggested filename:** `06_EICR_{SiteOrVendor}.docx` (or include `EICR` / `Electrical Installation Condition Report` in the name)

---

### 7. `EIC` — Electrical Installation Certificate (EIC) — new/modified installations (Building)

- **Trade:** Electrical
- **Regulation:** BS 7671:2018+A4:2026; Building Regulations Part P
- **Frequency:** On completion of any new or significantly altered electrical installation
- **Issuing body:** NICEIC / NAPIT / ECA-registered contractor
- **Required contractor accreditation:** NICEIC
- **Verification URL:** https://www.niceic.com/find-a-contractor

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Client`
- `Description of installation`
- `Design`
- `construction`
- `inspection signatures`
- `Circuit schedule`
- `Test results (insulation resistance, continuity, earth fault loop impedance, RCD test times)`
- `Date`
- `Contractor NICEIC`
- `NAPIT no.`

**Word pack source columns:** Client | Description of installation | Design/construction/inspection signatures | Circuit schedule | Test results (insulation resistance, continuity, earth fault loop impedance, RCD test times) | Date | Contractor NICEIC/NAPIT no.

**Suggested filename:** `07_EIC_{SiteOrVendor}.docx` (or include `EIC` / `Electrical Installation Certificate` in the name)

---

### 8. `PAT` — Portable Appliance Testing (PAT) Register (Building)

- **Trade:** Electrical
- **Regulation:** Electricity at Work Regulations 1989; IET Code of Practice for In-Service Inspection
- **Frequency:** Risk-based — annually for most commercial environments
- **Issuing body:** Competent person with City & Guilds 2377 or equivalent
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Asset ID`
- `Description`
- `Location`
- `Serial no.`
- `Test date`
- `Earth continuity (Ω)`
- `Insulation resistance (MΩ)`
- `Pass`
- `Fail`
- `Withdrawn`
- `Next test due`
- `Tester name and signature`

**Word pack source columns:** Asset ID | Description | Location | Serial no. | Test date | Earth continuity (Ω) | Insulation resistance (MΩ) | Pass/Fail/Withdrawn | Next test due | Tester name and signature

**Suggested filename:** `08_PAT_{SiteOrVendor}.docx` (or include `PAT` / `Portable Appliance Testing` in the name)

---

### 9. `CP17` — Gas Safety Certificate — Commercial (CP17) (Building)

- **Trade:** Gas
- **Regulation:** Gas Safety (Installation and Use) Regulations 1998 (SI 1998/2451) HSE minimum fields HSE: what a gas safety record must contain
- **Frequency:** Annual — must not lapse
- **Issuing body:** Gas Safe registered engineer (mandatory by law)
- **Required contractor accreditation:** Gas Safe
- **Verification URL:** https://www.gassaferegister.co.uk/find-an-engineer/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Property address`
- `Appliance description`
- `location`
- `Gas Safe registration no.`
- `Date`
- `Inlet pressure (mbar)`
- `Heat input (kW)`
- `Burner pressure`
- `Flue flow test`
- `CO`
- `CO2 readings`
- `Safety device operation`
- `Safe to use: Yes`
- `No`
- `Remedial actions`
- `Engineer signature`
- `Next check due`

**Word pack source columns:** Property address | Appliance description/location | Gas Safe registration no. | Date | Inlet pressure (mbar) | Heat input (kW) | Burner pressure | Flue flow test | CO/CO2 readings | Safety device operation | Safe to use: Yes/No | Remedial actions | Engineer signature | Next check due

**Suggested filename:** `09_CP17_{SiteOrVendor}.docx` (or include `CP17` / `Gas Safety Certificate — Commercial` in the name)

---

### 10. `BOILER_SERVICE` — Boiler Service Record (Building)

- **Trade:** Gas
- **Regulation:** Gas Safety Regulations 1998; Manufacturer maintenance requirements
- **Frequency:** Annual minimum; some manufacturers require 6-monthly
- **Issuing body:** Gas Safe registered engineer
- **Required contractor accreditation:** Gas Safe
- **Verification URL:** https://www.gassaferegister.co.uk/find-an-engineer/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Boiler make`
- `model`
- `serial no.`
- `Location`
- `Service date`
- `Tasks completed`
- `Parts replaced`
- `Combustion analysis (CO, CO2, flue temp, efficiency %)`
- `Fault codes cleared`
- `Gas Safe no.`
- `Engineer signature`
- `Next service due`

**Word pack source columns:** Boiler make/model/serial no. | Location | Service date | Tasks completed | Parts replaced | Combustion analysis (CO, CO2, flue temp, efficiency %) | Fault codes cleared | Gas Safe no. | Engineer signature | Next service due

**Suggested filename:** `10_BOILER_SERVICE_{SiteOrVendor}.docx` (or include `BOILER SERVICE` / `Boiler Service Record` in the name)

---

### 11. `L8_RISK` — Legionella Risk Assessment (Building)

- **Trade:** Water/Legionella
- **Regulation:** ACOP L8 (4th ed); COSHH 2002 (SI 2002/2677) HSG274 HSE Technical Guidance Parts 1-3
- **Frequency:** Every 2 years or after significant system change or incident
- **Issuing body:** Competent assessor — LCA registered company strongly recommended
- **Required contractor accreditation:** LCA
- **Verification URL:** https://www.legionellacontrol.org.uk/members/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Site`
- `Date`
- `Assessor name`
- `LCA no.`
- `Water system description`
- `Risk rating (High`
- `Medium`
- `Low)`
- `Population at risk`
- `Significant hazards`
- `Control measures`
- `Recommended actions`
- `Responsible person`
- `Monitoring scheme`
- `Review date`

**Word pack source columns:** Site | Date | Assessor name/LCA no. | Water system description | Risk rating (High/Medium/Low) | Population at risk | Significant hazards | Control measures | Recommended actions | Responsible person | Monitoring scheme | Review date

**Suggested filename:** `11_L8_RISK_{SiteOrVendor}.docx` (or include `L8 RISK` / `Legionella Risk Assessment` in the name)

---

### 12. `LEGIONELLA_MONITORING` — Legionella Monitoring Log / Water Treatment Records (Building)

- **Trade:** Water/Legionella
- **Regulation:** ACOP L8; HSG274; COSHH 2002
- **Frequency:** Monthly temperature monitoring; Weekly flushing; Treatment dosing as scheme requires
- **Issuing body:** Water treatment contractor or trained in-house person
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Date`
- `Outlet reference`
- `Temperature reading (°C)`
- `Target met Y`
- `N`
- `Flushing duration`
- `Biocide dose`
- `concentration`
- `Chlorine residual (mg`
- `L)`
- `Technician name`
- `signature`
- `Corrective action`
- `Next date`

**Word pack source columns:** Date | Outlet reference | Temperature reading (°C) | Target met Y/N | Flushing duration | Biocide dose/concentration | Chlorine residual (mg/L) | Technician name/signature | Corrective action | Next date

**Suggested filename:** `12_LEGIONELLA_MONITORING_{SiteOrVendor}.docx` (or include `LEGIONELLA MONITORING` / `Legionella Monitoring Log / Water Treatment Records` in the name)

---

### 13. `COLD_WATER_TANK` — Cold Water Storage Tank Inspection Certificate (Building)

- **Trade:** Water/Legionella
- **Regulation:** ACOP L8; Water Supply (Water Fittings) Regulations 1999 (SI 1999/1148)
- **Frequency:** Annual visual inspection; full clean and disinfection every 1-2 years per risk assessment
- **Issuing body:** LCA-registered water hygiene company
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Site`
- `Tank ID`
- `location`
- `Capacity (litres)`
- `Inspection date`
- `Lid`
- `screen`
- `valve condition`
- `Water clarity`
- `Debris`
- `sediment`
- `Lining condition`
- `Insulation`
- `Temperature (°C)`
- `Sample taken Y`
- `N`
- `Chlorination Y`
- `N`
- `Remedial actions`
- `Technician signature`

**Word pack source columns:** Site | Tank ID/location | Capacity (litres) | Inspection date | Lid/screen/valve condition | Water clarity | Debris/sediment | Lining condition | Insulation | Temperature (°C) | Sample taken Y/N | Chlorination Y/N | Remedial actions | Technician signature

**Suggested filename:** `13_COLD_WATER_TANK_{SiteOrVendor}.docx` (or include `COLD WATER TANK` / `Cold Water Storage Tank Inspection Certificate` in the name)

---

### 14. `WSE` — Written Scheme of Examination (WSE) & Pressure Vessel Inspection Certificate (Building)

- **Trade:** Pressure Systems
- **Regulation:** Pressure Systems Safety Regulations 2000 (SI 2000/128) HSE Guidance HSG261; PSSR ACOP guidance
- **Frequency:** As defined in WSE — typically 12-26 months for heating and hot water vessels
- **Issuing body:** Competent person — Allianz Engineering, Lloyd's Register, Bureau Veritas, TÜV SÜD
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Site`
- `System description`
- `Vessel ID`
- `serial no.`
- `MAWP (bar)`
- `Design temperature`
- `Fluid`
- `Last exam date`
- `Exam type (external`
- `internal`
- `hydraulic)`
- `Defects found`
- `Safe operating limits`
- `Next exam due`
- `Competent person name`
- `body`
- `signature`

**Word pack source columns:** Site | System description | Vessel ID/serial no. | MAWP (bar) | Design temperature | Fluid | Last exam date | Exam type (external/internal/hydraulic) | Defects found | Safe operating limits | Next exam due | Competent person name/body/signature

**Suggested filename:** `14_WSE_{SiteOrVendor}.docx` (or include `WSE` / `Written Scheme of Examination` in the name)

---

### 15. `LOLER` — LOLER Thorough Examination Report — Passenger / Goods Lift (Building)

- **Trade:** LOLER
- **Regulation:** Lifting Operations and Lifting Equipment Regulations 1998 (SI 1998/2307) HSE Guidance L113 ACOP; HSE LOLER thorough examination guidance
- **Frequency:** 6 months (lifts carrying persons); 12 months (goods lifts)
- **Issuing body:** Competent person — typically insurance-based inspection body (Allianz, Bureau Veritas, Lloyd's Register, TÜV SÜD)
- **Required contractor accreditation:** LEEA
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Site`
- `Lift ID`
- `serial no.`
- `Lift type`
- `SWL (persons`
- `kg)`
- `Exam date`
- `Parts examined (ropes, sheaves, buffers, safety gear, car, doors, electrics, overload)`
- `Defects`
- `observations`
- `Immediate danger Y`
- `N`
- `Remedy before next use Y`
- `N`
- `Next exam due`
- `Competent person name`
- `employer`
- `qualification`
- `signature`

**Word pack source columns:** Site | Lift ID/serial no. | Lift type | SWL (persons/kg) | Exam date | Parts examined (ropes, sheaves, buffers, safety gear, car, doors, electrics, overload) | Defects/observations | Immediate danger Y/N | Remedy before next use Y/N | Next exam due | Competent person name/employer/qualification/signature

**Suggested filename:** `15_LOLER_{SiteOrVendor}.docx` (or include `LOLER` / `LOLER Thorough Examination Report — Passenger / Goods Lift` in the name)

---

### 16. `ASBESTOS_SURVEY` — Asbestos Management Survey Report (Building)

- **Trade:** Asbestos
- **Regulation:** Control of Asbestos Regulations 2012 (SI 2012/632); HSG264 HSE Guidance HSG264 — Asbestos: The survey guide
- **Frequency:** One-off survey with annual management plan review
- **Issuing body:** UKAS-accredited surveying organisation; individual with P402 competency
- **Required contractor accreditation:** UKAS
- **Verification URL:** https://www.ukas.com/find-an-organisation/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Client`
- `site`
- `Survey type`
- `Date`
- `Surveyor name`
- `UKAS no.`
- `P402 cert`
- `Areas surveyed`
- `ACM reference`
- `Location`
- `Material type`
- `Sample ref`
- `Condition`
- `Surface treatment`
- `Risk score`
- `Management recommendation`
- `Analyst name`
- `UKAS no.`

**Word pack source columns:** Client/site | Survey type | Date | Surveyor name/UKAS no./P402 cert | Areas surveyed | ACM reference | Location | Material type | Sample ref | Condition | Surface treatment | Risk score | Management recommendation | Analyst name/UKAS no.

**Suggested filename:** `16_ASBESTOS_SURVEY_{SiteOrVendor}.docx` (or include `ASBESTOS SURVEY` / `Asbestos Management Survey Report` in the name)

---

### 17. `ASBESTOS_REGISTER` — Asbestos Register (Living Document) (Building)

- **Trade:** Asbestos
- **Regulation:** Control of Asbestos Regulations 2012 (Regulation 4)
- **Frequency:** Continuously maintained; updated after any disturbance or reinspection
- **Issuing body:** PM maintains; populated from survey reports
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Building address`
- `ACM reference`
- `Floor`
- `area`
- `room`
- `Material description`
- `Quantity`
- `Condition (current)`
- `Risk score`
- `Management action`
- `Survey reference`
- `Last reinspection`
- `Reinspection frequency`
- `Action completed Y`
- `N`
- `Date`
- `Notes`

**Word pack source columns:** Building address | ACM reference | Floor/area/room | Material description | Quantity | Condition (current) | Risk score | Management action | Survey reference | Last reinspection | Reinspection frequency | Action completed Y/N | Date | Notes

**Suggested filename:** `17_ASBESTOS_REGISTER_{SiteOrVendor}.docx` (or include `ASBESTOS REGISTER` / `Asbestos Register` in the name)

---

### 18. `EPC` — Energy Performance Certificate (EPC) (Building)

- **Trade:** Energy
- **Regulation:** Energy Performance of Buildings Regulations 2012 (SI 2012/3118); MEES 2015 MEES Minimum Energy Efficiency Standards (England and Wales) Regulations 2015
- **Frequency:** Valid 10 years; current at every letting; MEES minimum EPC E (2023), proposed B by 2030
- **Issuing body:** Accredited Energy Assessor registered on CIBSE or Elmhurst scheme
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Property address`
- `EPC ref no.`
- `Assessment date`
- `Expiry date`
- `Asset rating (A-G) and score`
- `Typical energy cost`
- `Main heating fuel`
- `Building type`
- `area`
- `CO2 emissions rating`
- `Primary energy use (kWh`
- `m²`
- `year)`
- `Improvement recommendations`
- `Assessor name and accreditation no.`

**Word pack source columns:** Property address | EPC ref no. | Assessment date | Expiry date | Asset rating (A-G) and score | Typical energy cost | Main heating fuel | Building type/area | CO2 emissions rating | Primary energy use (kWh/m²/year) | Improvement recommendations | Assessor name and accreditation no.

**Suggested filename:** `18_EPC_{SiteOrVendor}.docx` (or include `EPC` / `Energy Performance Certificate` in the name)

---

### 19. `TM44` — Air Conditioning Inspection Report (TM44) (Building)

- **Trade:** Energy
- **Regulation:** Energy Performance of Buildings Regulations 2012 — Article 12; SI 2012/3118
- **Frequency:** Every 5 years
- **Issuing body:** CIBSE-registered Energy Assessor (Level 3 or 4 Non-Domestic)
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Property`
- `Inspection date`
- `Inspector name`
- `CIBSE no.`
- `AC system inventory (type, make, model, age, refrigerant, rated output)`
- `Maintenance history`
- `Controls assessment`
- `Sizing assessment`
- `Efficiency observations`
- `Recommended measures`
- `Certificate ref`
- `Next inspection due`

**Word pack source columns:** Property | Inspection date | Inspector name/CIBSE no. | AC system inventory (type, make, model, age, refrigerant, rated output) | Maintenance history | Controls assessment | Sizing assessment | Efficiency observations | Recommended measures | Certificate ref | Next inspection due

**Suggested filename:** `19_TM44_{SiteOrVendor}.docx` (or include `TM44` / `Air Conditioning Inspection Report` in the name)

---

### 20. `DEC` — Display Energy Certificate (DEC) — Public Buildings (Building)

- **Trade:** Energy
- **Regulation:** Energy Performance of Buildings Regulations 2012 (public buildings >250m²)
- **Frequency:** Operational rating: annual; Advisory report: every 7 years
- **Issuing body:** Accredited Energy Assessor
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Property`
- `DEC reference`
- `Annual operational rating (A-G)`
- `CO2 emissions (kg`
- `m²`
- `year)`
- `Energy use breakdown`
- `Benchmark comparison`
- `Year of construction`
- `Floor area`
- `Assessor name`
- `accreditation no.`
- `Advisory report reference`
- `Valid until`

**Word pack source columns:** Property | DEC reference | Annual operational rating (A-G) | CO2 emissions (kg/m²/year) | Energy use breakdown | Benchmark comparison | Year of construction | Floor area | Assessor name/accreditation no. | Advisory report reference | Valid until

**Suggested filename:** `20_DEC_{SiteOrVendor}.docx` (or include `DEC` / `Display Energy Certificate` in the name)

---

### 21. `ESOS` — ESOS Assessment Evidence Pack (Building)

- **Trade:** Energy
- **Regulation:** Energy Savings Opportunity Scheme Regulations 2014 (SI 2014/1643)
- **Frequency:** Every 4 years (Phase 3 deadline: December 2023)
- **Issuing body:** ESOS Lead Assessor registered with CIBSE, Energy Institute, or IEMA
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Organisation name`
- `Companies House no.`
- `ESOS notification reference`
- `Phase`
- `Compliance date`
- `Lead assessor name`
- `registration body`
- `Energy audits completed (buildings, transport, industrial)`
- `Total energy consumption (kWh by vector)`
- `Opportunities identified`
- `Estimated savings`
- `Board director sign-off`
- `Environment Agency notification reference`

**Word pack source columns:** Organisation name | Companies House no. | ESOS notification reference | Phase | Compliance date | Lead assessor name/registration body | Energy audits completed (buildings, transport, industrial) | Total energy consumption (kWh by vector) | Opportunities identified | Estimated savings | Board director sign-off | Environment Agency notification reference

**Suggested filename:** `21_ESOS_{SiteOrVendor}.docx` (or include `ESOS` / `ESOS Assessment Evidence Pack` in the name)

---

### 22. `FGAS` — F-Gas Leak Check Certificate / Refrigerant Log (Building)

- **Trade:** F-Gas
- **Regulation:** UK F-Gas Regulations 2022 (retained from EU 517/2014); SI 2015/310 as amended GOV.UK Guidance F-Gas guidance for engineers and refrigeration businesses
- **Frequency:** <5 tCO2e: annual; 5-50t: 6-monthly; >50t: 3-monthly; >500t: 3-monthly with ALD system
- **Issuing body:** REFCOM-registered company; F-Gas certified technician (City & Guilds 2079)
- **Required contractor accreditation:** REFCOM
- **Verification URL:** https://www.refcom.org.uk/find-a-company/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Site`
- `Equipment ID`
- `Refrigerant type`
- `Charge quantity (kg)`
- `CO2e (tCO2e)`
- `Check date`
- `Leak detection method`
- `Leaks found Y`
- `N`
- `Leak location`
- `Repair action`
- `Post-repair check result`
- `Refrigerant added (kg)`
- `Recovered (kg)`
- `Company cert no.`
- `Technician cert no.`
- `Signature`
- `Next check due`

**Word pack source columns:** Site | Equipment ID | Refrigerant type | Charge quantity (kg) | CO2e (tCO2e) | Check date | Leak detection method | Leaks found Y/N | Leak location | Repair action | Post-repair check result | Refrigerant added (kg) | Recovered (kg) | Company cert no. | Technician cert no. | Signature | Next check due

**Suggested filename:** `22_FGAS_{SiteOrVendor}.docx` (or include `FGAS` / `F-Gas Leak Check Certificate / Refrigerant Log` in the name)

---

### 23. `HS_POLICY` — Health & Safety Policy Statement (Building)

- **Trade:** H&S
- **Regulation:** Health and Safety at Work etc. Act 1974 (Section 2(3))
- **Frequency:** Annual review or following significant change
- **Issuing body:** —
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Organisation name`
- `Policy statement`
- `Signed by (name, title, date)`
- `Organisation structure`
- `Responsibilities`
- `Arrangements`
- `Distribution list`
- `Version`
- `Review date`

**Word pack source columns:** Organisation name | Policy statement | Signed by (name, title, date) | Organisation structure | Responsibilities | Arrangements | Distribution list | Version | Review date

**Suggested filename:** `23_HS_POLICY_{SiteOrVendor}.docx` (or include `HS POLICY` / `Health & Safety Policy Statement` in the name)

---

### 24. `HS_RISK_ASSESSMENT` — General Risk Assessment Register (Building)

- **Trade:** H&S
- **Regulation:** Management of H&S at Work Regulations 1999 (SI 1999/3242); HSWA 1974
- **Frequency:** Annual review; immediately after incident or significant change
- **Issuing body:** —
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Activity`
- `task`
- `Hazard`
- `Who might be harmed`
- `Existing controls`
- `Likelihood (1-5)`
- `Severity (1-5)`
- `Risk rating`
- `Additional controls`
- `Responsible person`
- `Target date`
- `Completion date`
- `Residual risk`
- `Assessor name`
- `Date`

**Word pack source columns:** Activity/task | Hazard | Who might be harmed | Existing controls | Likelihood (1-5) | Severity (1-5) | Risk rating | Additional controls | Responsible person | Target date | Completion date | Residual risk | Assessor name | Date

**Suggested filename:** `24_HS_RISK_ASSESSMENT_{SiteOrVendor}.docx` (or include `HS RISK ASSESSMENT` / `General Risk Assessment Register` in the name)

---

### 25. `EL_INSURANCE` — Employers' Liability Insurance Certificate (Building)

- **Trade:** H&S
- **Regulation:** Employers' Liability (Compulsory Insurance) Act 1969
- **Frequency:** Annual renewal
- **Issuing body:** —
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Insured name`
- `Policy number`
- `Insurer name and FCA authorisation`
- `Period of insurance (from`
- `to)`
- `Minimum indemnity (statutory minimum £5M)`
- `Compliance statement`
- `Insurer signature`
- `Date`
- `Instruction to display prominently`

**Word pack source columns:** Insured name | Policy number | Insurer name and FCA authorisation | Period of insurance (from/to) | Minimum indemnity (statutory minimum £5M) | Compliance statement | Insurer signature | Date | Instruction to display prominently

**Suggested filename:** `25_EL_INSURANCE_{SiteOrVendor}.docx` (or include `EL INSURANCE` / `Employers' Liability Insurance Certificate` in the name)

---

### 26. `HRB_REGISTRATION` — Higher-Risk Building (HRB) Registration Certificate (Building)

- **Trade:** Building Safety Act
- **Regulation:** Building Safety Act 2022; HRB Registration Regulations 2023 (SI 2023/270) BSR Building Safety Regulator — HRB registration guidance
- **Frequency:** One-off registration; annual confirmation. Failure to register = criminal offence
- **Issuing body:** Building Safety Regulator (BSR) — operated by HSE
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Unique Building Identifier (UBI)`
- `Building name`
- `address`
- `PAP name`
- `organisation`
- `Building Manager name`
- `contact`
- `Number of floors`
- `Height (m)`
- `Number of residential units`
- `Registration date`
- `BSR reference`
- `Compliance status`

**Word pack source columns:** Unique Building Identifier (UBI) | Building name/address | PAP name/organisation | Building Manager name/contact | Number of floors | Height (m) | Number of residential units | Registration date | BSR reference | Compliance status

**Suggested filename:** `26_HRB_REGISTRATION_{SiteOrVendor}.docx` (or include `HRB REGISTRATION` / `Higher-Risk Building` in the name)

---

### 27. `SAFETY_CASE_REPORT` — Building Safety Case Report (Building)

- **Trade:** Building Safety Act
- **Regulation:** Building Safety Act 2022 (Sections 76-87); SI 2023/999 BSR Safety case report — what to include (HSE)
- **Frequency:** Living document — updated on significant change; submitted at registration and on BSR request
- **Issuing body:** —
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Building description`
- `UBI`
- `Structural fire safety assessment`
- `Fire safety measures`
- `Structural integrity evidence`
- `Golden Thread information register`
- `Safety management system`
- `Residents engagement strategy`
- `Identified risks and controls`
- `PAP contact`
- `Date`
- `version`

**Word pack source columns:** Building description/UBI | Structural fire safety assessment | Fire safety measures | Structural integrity evidence | Golden Thread information register | Safety management system | Residents engagement strategy | Identified risks and controls | PAP contact | Date/version

**Suggested filename:** `27_SAFETY_CASE_REPORT_{SiteOrVendor}.docx` (or include `SAFETY CASE REPORT` / `Building Safety Case Report` in the name)

---

### 28. `GAS_SAFE` — Gas Safe Registration Certificate (Company) (Vendor)

- **Trade:** Gas
- **Regulation:** Gas Safety (Installation and Use) Regulations 1998 (SI 1998/2451)
- **Frequency:** Annual renewal — unregistered gas work is a criminal offence
- **Issuing body:** Gas Safe Register (appointed by HSE as official registration body)
- **Required contractor accreditation:** —
- **Verification URL:** https://www.gassaferegister.co.uk/find-an-engineer/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Company name`
- `trading name`
- `Registered address`
- `Gas Safe registration no.`
- `Categories of work authorised`
- `Expiry date`
- `Conditions`
- `Authorised signatories`

**Word pack source columns:** Company name/trading name | Registered address | Gas Safe registration no. | Categories of work authorised | Expiry date | Conditions | Authorised signatories

**Suggested filename:** `28_GAS_SAFE_{SiteOrVendor}.docx` (or include `GAS SAFE` / `Gas Safe Registration Certificate` in the name)

---

### 29. `ACS_CARD` — ACS Individual Competency Card (Gas Engineer) (Vendor)

- **Trade:** Gas
- **Regulation:** Gas Safety Regulations 1998; ACOP supporting competency framework
- **Frequency:** 5-year renewal; engineer must carry card on all gas work
- **Issuing body:** CATA / NACS / EUSR (certification bodies)
- **Required contractor accreditation:** —
- **Verification URL:** https://www.gassaferegister.co.uk/find-an-engineer/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Engineer name`
- `photo`
- `Gas Safe card no.`
- `Company`
- `ACS competency codes (CCN1=core, CENWAT=central heating, CKR1=commercial kitchens)`
- `Issue date`
- `Expiry`

**Word pack source columns:** Engineer name/photo | Gas Safe card no. | Company | ACS competency codes (CCN1=core, CENWAT=central heating, CKR1=commercial kitchens) | Issue date | Expiry

**Suggested filename:** `29_ACS_CARD_{SiteOrVendor}.docx` (or include `ACS CARD` / `ACS Individual Competency Card` in the name)

---

### 30. `NICEIC` — NICEIC Approved Contractor Certificate (Vendor)

- **Trade:** Electrical
- **Regulation:** Electricity at Work Regulations 1989; BS 7671
- **Frequency:** Annual assessment and renewal
- **Issuing body:** NICEIC (National Inspection Council for Electrical Installation Contracting)
- **Required contractor accreditation:** —
- **Verification URL:** https://www.niceic.com/find-a-contractor

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Company name`
- `NICEIC roll number`
- `Registration categories`
- `Qualified Supervisor name`
- `qualification`
- `Assessment date`
- `Expiry date`

**Word pack source columns:** Company name | NICEIC roll number | Registration categories | Qualified Supervisor name/qualification | Assessment date | Expiry date

**Suggested filename:** `30_NICEIC_{SiteOrVendor}.docx` (or include `NICEIC` / `NICEIC Approved Contractor Certificate` in the name)

---

### 31. `NAPIT` — NAPIT Registration Certificate (Vendor)

- **Trade:** Electrical
- **Regulation:** Electricity at Work Regulations 1989; Building Regulations Part P
- **Frequency:** Annual
- **Issuing body:** NAPIT (National Association of Professional Inspectors and Testers)
- **Required contractor accreditation:** —
- **Verification URL:** https://www.napit.org.uk/find-an-installer

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Company name`
- `NAPIT member number`
- `Competencies`
- `Approved Inspector name`
- `Issue date`
- `Expiry`

**Word pack source columns:** Company name | NAPIT member number | Competencies | Approved Inspector name | Issue date | Expiry

**Suggested filename:** `31_NAPIT_{SiteOrVendor}.docx` (or include `NAPIT` / `NAPIT Registration Certificate` in the name)

---

### 32. `CG_2382` — City & Guilds 2382 — BS 7671 18th Edition Certificate (Vendor)

- **Trade:** Electrical
- **Regulation:** BS 7671:2018+A4:2026 (18th Edition Wiring Regulations)
- **Frequency:** Refreshed on each edition update (typically 5 years)
- **Issuing body:** City & Guilds / EAL / BTEC
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Candidate name`
- `Centre no.`
- `Candidate no.`
- `Qualification title (2382-22)`
- `Units passed`
- `Date`
- `Certificate no.`
- `Awarding body signature`

**Word pack source columns:** Candidate name | Centre no. | Candidate no. | Qualification title (2382-22) | Units passed | Date | Certificate no. | Awarding body signature

**Suggested filename:** `32_CG_2382_{SiteOrVendor}.docx` (or include `CG 2382` / `City & Guilds 2382 — BS 7671 18th Edition Certificate` in the name)

---

### 33. `BAFE_SP203_1` — BAFE SP203-1 Registration Certificate (Vendor)

- **Trade:** Fire
- **Regulation:** FSO 2005; BS 5839-1; BAFE scheme
- **Frequency:** Annual audit; 3-year full reassessment
- **Issuing body:** BAFE (British Approvals for Fire Equipment)
- **Required contractor accreditation:** —
- **Verification URL:** https://www.bafe.org.uk/find-a-bafe-registered-company/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Company name`
- `BAFE registration no.`
- `Scope (design`
- `installation`
- `commissioning`
- `maintenance)`
- `Scheme (SP203-1)`
- `Audit date`
- `Expiry date`
- `Certification body`

**Word pack source columns:** Company name | BAFE registration no. | Scope (design/installation/commissioning/maintenance) | Scheme (SP203-1) | Audit date | Expiry date | Certification body

**Suggested filename:** `33_BAFE_SP203_1_{SiteOrVendor}.docx` (or include `BAFE SP203 1` / `BAFE SP203-1 Registration Certificate` in the name)

---

### 34. `NSI_GOLD_FIRE` — NSI Gold Certificate — Fire & Security Systems (Vendor)

- **Trade:** Fire
- **Regulation:** FSO 2005; BS 5839-1; BS EN 54
- **Frequency:** Annual audit
- **Issuing body:** NSI (National Security Inspectorate)
- **Required contractor accreditation:** —
- **Verification URL:** https://www.nsi.org.uk/find-a-company/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Company name`
- `NSI number`
- `Activities (fire alarm design, installation, maintenance)`
- `Grade (Gold`
- `Silver)`
- `Expiry date`

**Word pack source columns:** Company name | NSI number | Activities (fire alarm design, installation, maintenance) | Grade (Gold/Silver) | Expiry date

**Suggested filename:** `34_NSI_GOLD_FIRE_{SiteOrVendor}.docx` (or include `NSI GOLD FIRE` / `NSI Gold Certificate — Fire & Security Systems` in the name)

---

### 35. `BAFE_SP101` — BAFE SP101 — Fixed Gaseous Suppression Systems (Vendor)

- **Trade:** Fire
- **Regulation:** FSO 2005; BS EN 15004
- **Frequency:** Annual
- **Issuing body:** BAFE / LPS 1048 (BRE Global)
- **Required contractor accreditation:** —
- **Verification URL:** https://www.bafe.org.uk/find-a-bafe-registered-company/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Company name`
- `Registration no.`
- `Systems covered (CO2, FM200, Inergen, Novec 1230)`
- `Expiry date`
- `Issuing body`

**Word pack source columns:** Company name | Registration no. | Systems covered (CO2, FM200, Inergen, Novec 1230) | Expiry date | Issuing body

**Suggested filename:** `35_BAFE_SP101_{SiteOrVendor}.docx` (or include `BAFE SP101` / `BAFE SP101 — Fixed Gaseous Suppression Systems` in the name)

---

### 36. `LEIA` — LEIA Membership Certificate (Vendor)

- **Trade:** Lifts
- **Regulation:** LOLER 1998; PSSR 2000; Machinery Directive (retained UK law)
- **Frequency:** Annual membership
- **Issuing body:** LEIA (Lift and Escalator Industry Association)
- **Required contractor accreditation:** —
- **Verification URL:** https://www.leia.co.uk/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Company name`
- `LEIA member number`
- `Category (installer`
- `maintainer`
- `repair`
- `manufacturer)`
- `Membership grade`
- `Expiry date`
- `Assessed to LEIA Code of Practice`

**Word pack source columns:** Company name | LEIA member number | Category (installer/maintainer/repair/manufacturer) | Membership grade | Expiry date | Assessed to LEIA Code of Practice

**Suggested filename:** `36_LEIA_{SiteOrVendor}.docx` (or include `LEIA` / `LEIA Membership Certificate` in the name)

---

### 37. `LOLER_CP` — LOLER Competent Person / Inspection Body Authorisation (Vendor)

- **Trade:** Lifts
- **Regulation:** LOLER 1998 (Regulation 9)
- **Frequency:** —
- **Issuing body:** Inspection bodies: Allianz Engineering, Bureau Veritas, Lloyd's Register, TÜV SÜD, SGS
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Inspector name`
- `Employer`
- `inspection body`
- `Authorisation scope (lift type, SWL range)`
- `LOLER competency reference`
- `Issue date`
- `Authorised signature`

**Word pack source columns:** Inspector name | Employer/inspection body | Authorisation scope (lift type, SWL range) | LOLER competency reference | Issue date | Authorised signature

**Suggested filename:** `37_LOLER_CP_{SiteOrVendor}.docx` (or include `LOLER CP` / `LOLER Competent Person / Inspection Body Authorisation` in the name)

---

### 38. `HSE_ASBESTOS_LICENCE` — HSE Asbestos Removal Licence (Licensed Contractor) (Vendor)

- **Trade:** Asbestos
- **Regulation:** Control of Asbestos Regulations 2012 (Regulation 8)
- **Frequency:** 3-year licence renewal
- **Issuing body:** Health and Safety Executive (HSE) — issued per company
- **Required contractor accreditation:** —
- **Verification URL:** https://www.hse.gov.uk/asbestos/licensing/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Company name`
- `address`
- `Licence number`
- `Licence holder`
- `Scope`
- `Conditions`
- `Issue date`
- `Expiry date`
- `HSE signature`

**Word pack source columns:** Company name/address | Licence number | Licence holder | Scope | Conditions | Issue date | Expiry date | HSE signature

**Suggested filename:** `38_HSE_ASBESTOS_LICENCE_{SiteOrVendor}.docx` (or include `HSE ASBESTOS LICENCE` / `HSE Asbestos Removal Licence` in the name)

---

### 39. `UKAS_ASBESTOS` — UKAS Accreditation Certificate — Asbestos Surveying & Testing (Vendor)

- **Trade:** Asbestos
- **Regulation:** CAR 2012; ISO 17020 (inspection) / ISO 17025 (testing)
- **Frequency:** 4-year cycle; annual surveillance
- **Issuing body:** UKAS (United Kingdom Accreditation Service)
- **Required contractor accreditation:** —
- **Verification URL:** https://www.ukas.com/find-an-organisation/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Organisation name`
- `UKAS accreditation no.`
- `Scope (fibre counting, bulk analysis, air sampling, survey)`
- `Standard (ISO 17020`
- `17025)`
- `Accreditation date`
- `Expiry`
- `Assessment schedule`

**Word pack source columns:** Organisation name | UKAS accreditation no. | Scope (fibre counting, bulk analysis, air sampling, survey) | Standard (ISO 17020/17025) | Accreditation date | Expiry | Assessment schedule

**Suggested filename:** `39_UKAS_ASBESTOS_{SiteOrVendor}.docx` (or include `UKAS ASBESTOS` / `UKAS Accreditation Certificate — Asbestos Surveying & Testing` in the name)

---

### 40. `ASBESTOS_P402_P403_P404` — Individual Asbestos Competency — P402 / P403 / P404 (Vendor)

- **Trade:** Asbestos
- **Regulation:** CAR 2012; HSG264
- **Frequency:** —
- **Issuing body:** BOHS (British Occupational Hygiene Society) / RSPH
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Candidate name`
- `Module (P402`
- `P403`
- `P404)`
- `Date`
- `Certificate no.`
- `BOHS`
- `RSPH membership no.`
- `Issuing body signature`

**Word pack source columns:** Candidate name | Module (P402/P403/P404) | Date | Certificate no. | BOHS/RSPH membership no. | Issuing body signature

**Suggested filename:** `40_ASBESTOS_P402_P403_P404_{SiteOrVendor}.docx` (or include `ASBESTOS P402 P403 P404` / `Individual Asbestos Competency — P402 / P403 / P404` in the name)

---

### 41. `LCA` — Legionella Control Association (LCA) Registration Certificate (Vendor)

- **Trade:** Water
- **Regulation:** ACOP L8; COSHH 2002
- **Frequency:** Annual assessment and renewal
- **Issuing body:** LCA (Legionella Control Association)
- **Required contractor accreditation:** —
- **Verification URL:** https://www.legionellacontrol.org.uk/members/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Company name`
- `LCA registration no.`
- `Scope (risk assessment`
- `water treatment`
- `tank cleaning`
- `cooling tower cleaning)`
- `Assessment date`
- `Expiry date`

**Word pack source columns:** Company name | LCA registration no. | Scope (risk assessment/water treatment/tank cleaning/cooling tower cleaning) | Assessment date | Expiry date

**Suggested filename:** `41_LCA_{SiteOrVendor}.docx` (or include `LCA` / `Legionella Control Association` in the name)

---

### 42. `BTEC_LEGIONELLA` — BTEC/BOHS Legionella Competency Certificate (Vendor)

- **Trade:** Water
- **Regulation:** ACOP L8 — requires competent persons for risk assessment and water management
- **Frequency:** —
- **Issuing body:** BOHS / Edexcel BTEC / WMSoc
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Candidate name`
- `Award title`
- `Level`
- `Units completed`
- `Date`
- `Certificate no.`
- `Awarding body`
- `Registration no.`

**Word pack source columns:** Candidate name | Award title | Level | Units completed | Date | Certificate no. | Awarding body | Registration no.

**Suggested filename:** `42_BTEC_LEGIONELLA_{SiteOrVendor}.docx` (or include `BTEC LEGIONELLA` / `BTEC/BOHS Legionella Competency Certificate` in the name)

---

### 43. `REFCOM` — F-Gas Company Certificate (REFCOM Registration) (Vendor)

- **Trade:** HVAC/F-Gas
- **Regulation:** UK F-Gas Regulations 2022; SI 2015/310
- **Frequency:** Annual — must remain current
- **Issuing body:** REFCOM (Register of Companies Competent to Handle Refrigerants)
- **Required contractor accreditation:** —
- **Verification URL:** https://www.refcom.org.uk/find-a-company/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Company name`
- `REFCOM registration no.`
- `Activities (installation`
- `maintenance`
- `leak checking`
- `recovery)`
- `Nominated technician names`
- `cert nos.`
- `Registration date`

**Word pack source columns:** Company name | REFCOM registration no. | Activities (installation/maintenance/leak checking/recovery) | Nominated technician names/cert nos. | Registration date

**Suggested filename:** `43_REFCOM_{SiteOrVendor}.docx` (or include `REFCOM` / `F-Gas Company Certificate` in the name)

---

### 44. `CG_2079` — Individual F-Gas Technician Certificate — City & Guilds 2079 (Vendor)

- **Trade:** HVAC/F-Gas
- **Regulation:** UK F-Gas Regulations 2022 — mandatory for anyone handling fluorinated refrigerants
- **Frequency:** —
- **Issuing body:** City & Guilds / ACRIB (Air Conditioning and Refrigeration Industry Board) Categories Category I: All equipment | Category II: Stationary <3kg | Category III: Hermetically sealed <6kg | Category IV: Recovery only
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Candidate name`
- `Certificate no.`
- `Category (I`
- `II`
- `III`
- `IV)`
- `Refrigerants covered`
- `Date of award`
- `City & Guilds`
- `ACRIB reference`

**Word pack source columns:** Candidate name | Certificate no. | Category (I/II/III/IV) | Refrigerants covered | Date of award | City & Guilds/ACRIB reference

**Suggested filename:** `44_CG_2079_{SiteOrVendor}.docx` (or include `CG 2079` / `Individual F-Gas Technician Certificate — City & Guilds 2079` in the name)

---

### 45. `CHAS_SSIP` — CHAS Premium Plus / SSIP Certificate (Vendor)

- **Trade:** General
- **Regulation:** Health and Safety at Work Act 1974; CDM Regulations 2015
- **Frequency:** Annual renewal
- **Issuing body:** CHAS / Constructionline / SafeContractor / SMAS / Acclaim (all SSIP mutual recognition)
- **Required contractor accreditation:** —
- **Verification URL:** https://ssip.org.uk/members/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Company name`
- `SSIP member scheme`
- `Certificate level`
- `Scope of works assessed`
- `Assessment date`
- `Expiry date`
- `Assessor body`

**Word pack source columns:** Company name | SSIP member scheme | Certificate level | Scope of works assessed | Assessment date | Expiry date | Assessor body

**Suggested filename:** `45_CHAS_SSIP_{SiteOrVendor}.docx` (or include `CHAS SSIP` / `CHAS Premium Plus / SSIP Certificate` in the name)

---

### 46. `ISO_9001` — ISO 9001:2015 Quality Management Certificate (Vendor)

- **Trade:** General
- **Regulation:** —
- **Frequency:** 3-year cycle; annual surveillance audits
- **Issuing body:** UKAS-accredited certification body (BSI, Bureau Veritas, NQA, SGS, Intertek)
- **Required contractor accreditation:** —
- **Verification URL:** https://www.ukas.com/find-an-organisation/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Organisation name`
- `Certificate no.`
- `Scope`
- `Certification body`
- `Date of initial cert`
- `Current cycle start`
- `Expiry date`
- `Issuing signatory`
- `UKAS accreditation no.`

**Word pack source columns:** Organisation name | Certificate no. | Scope | Certification body | Date of initial cert | Current cycle start | Expiry date | Issuing signatory | UKAS accreditation no.

**Suggested filename:** `46_ISO_9001_{SiteOrVendor}.docx` (or include `ISO 9001` / `ISO 9001:2015 Quality Management Certificate` in the name)

---

### 47. `ISO_14001` — ISO 14001:2015 Environmental Management Certificate (Vendor)

- **Trade:** General
- **Regulation:** —
- **Frequency:** 3-year cycle; annual surveillance
- **Issuing body:** UKAS-accredited certification body
- **Required contractor accreditation:** —
- **Verification URL:** https://www.ukas.com/find-an-organisation/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Organisation name`
- `Certificate no.`
- `Scope`
- `Sites covered`
- `Certification body`
- `UKAS no.`
- `Expiry date`

**Word pack source columns:** Organisation name | Certificate no. | Scope | Sites covered | Certification body | UKAS no. | Expiry date

**Suggested filename:** `47_ISO_14001_{SiteOrVendor}.docx` (or include `ISO 14001` / `ISO 14001:2015 Environmental Management Certificate` in the name)

---

### 48. `CONTRACTOR_EL_INSURANCE` — Contractors' Employers' Liability Insurance Certificate (Vendor)

- **Trade:** General
- **Regulation:** Employers' Liability (Compulsory Insurance) Act 1969
- **Frequency:** Annual — PM must check annually for every contractor
- **Issuing body:** —
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Insured name`
- `Policy no.`
- `Insurer name`
- `FCA no.`
- `Period`
- `Minimum indemnity (£5M statutory minimum)`
- `Date`

**Word pack source columns:** Insured name | Policy no. | Insurer name/FCA no. | Period | Minimum indemnity (£5M statutory minimum) | Date

**Suggested filename:** `48_CONTRACTOR_EL_INSURANCE_{SiteOrVendor}.docx` (or include `CONTRACTOR EL INSURANCE` / `Contractors' Employers' Liability Insurance Certificate` in the name)

---

### 49. `CONTRACTOR_PL_INSURANCE` — Contractors' Public Liability Insurance Certificate (Vendor)

- **Trade:** General
- **Regulation:** —
- **Frequency:** Annual — verify at contract renewal and annually during contract
- **Issuing body:** —
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Insured name`
- `Policy no.`
- `Insurer`
- `Period`
- `Indemnity limit per occurrence`
- `Currency statement`
- `Date`

**Word pack source columns:** Insured name | Policy no. | Insurer | Period | Indemnity limit per occurrence | Currency statement | Date

**Suggested filename:** `49_CONTRACTOR_PL_INSURANCE_{SiteOrVendor}.docx` (or include `CONTRACTOR PL INSURANCE` / `Contractors' Public Liability Insurance Certificate` in the name)

---

### 50. `SIA_ACS` — SIA Approved Contractor Scheme (ACS) Certificate (Vendor)

- **Trade:** Security
- **Regulation:** Private Security Industry Act 2001
- **Frequency:** Annual audit; ACS accreditation reviewed every 3 years
- **Issuing body:** Security Industry Authority (SIA)
- **Required contractor accreditation:** —
- **Verification URL:** https://www.sia.homeoffice.gov.uk/Pages/acs-rosta.aspx

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Company name`
- `ACS score (%)`
- `Activities covered (Manned Guarding`
- `Door Supervision`
- `CCTV`
- `Cash and Valuables)`
- `Audit date`
- `Certificate expiry`
- `SIA reference no.`

**Word pack source columns:** Company name | ACS score (%) | Activities covered (Manned Guarding/Door Supervision/CCTV/Cash and Valuables) | Audit date | Certificate expiry | SIA reference no.

**Suggested filename:** `50_SIA_ACS_{SiteOrVendor}.docx` (or include `SIA ACS` / `SIA Approved Contractor Scheme` in the name)

---

### 51. `SIA_INDIVIDUAL` — Individual SIA Licence (Vendor)

- **Trade:** Security
- **Regulation:** Private Security Industry Act 2001 — mandatory for all frontline security operatives
- **Frequency:** 3-year renewal; must be carried on duty at all times
- **Issuing body:** SIA
- **Required contractor accreditation:** —
- **Verification URL:** https://www.sia.homeoffice.gov.uk/Pages/licensing-check.aspx

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Name`
- `photo`
- `Licence no.`
- `Activity (Door Supervisor`
- `Security Guard`
- `CCTV Operator`
- `Close Protection)`
- `Issue date`
- `Expiry`
- `SIA logo`

**Word pack source columns:** Name/photo | Licence no. | Activity (Door Supervisor/Security Guard/CCTV Operator/Close Protection) | Issue date | Expiry | SIA logo

**Suggested filename:** `51_SIA_INDIVIDUAL_{SiteOrVendor}.docx` (or include `SIA INDIVIDUAL` / `Individual SIA Licence` in the name)

---

### 52. `NSI_GOLD_SECURITY` — NSI Gold Certificate — Security Systems (NACOSS Gold) (Vendor)

- **Trade:** Security
- **Regulation:** BS EN 50131 (intruder); BS EN 62676 (CCTV); BS EN 60839 (access control)
- **Frequency:** Annual audit
- **Issuing body:** NSI (National Security Inspectorate)
- **Required contractor accreditation:** —
- **Verification URL:** https://www.nsi.org.uk/find-a-company/

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Company name`
- `NSI NACOSS Gold no.`
- `Scope (intruder alarm`
- `CCTV`
- `access control)`
- `Grade (Gold)`
- `Expiry date`

**Word pack source columns:** Company name | NSI NACOSS Gold no. | Scope (intruder alarm/CCTV/access control) | Grade (Gold) | Expiry date

**Suggested filename:** `52_NSI_GOLD_SECURITY_{SiteOrVendor}.docx` (or include `NSI GOLD SECURITY` / `NSI Gold Certificate — Security Systems` in the name)

---

### 53. `BPCA` — BPCA Corporate Membership Certificate (Vendor)

- **Trade:** Pest Control
- **Regulation:** Plant Protection Products (Sustainable Use) Regulations 2012
- **Frequency:** Annual
- **Issuing body:** BPCA (British Pest Control Association)
- **Required contractor accreditation:** —
- **Verification URL:** https://bpca.org.uk/find-a-pest-controller

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Company name`
- `BPCA member no.`
- `Category (full`
- `associate member)`
- `Expiry date`
- `Audited to BPCA Code of Practice`

**Word pack source columns:** Company name | BPCA member no. | Category (full/associate member) | Expiry date | Audited to BPCA Code of Practice

**Suggested filename:** `53_BPCA_{SiteOrVendor}.docx` (or include `BPCA` / `BPCA Corporate Membership Certificate` in the name)

---

### 54. `PA1_PA2_PA6` — Individual Pesticide Application Certificate — PA1 + PA2 / PA6 (Vendor)

- **Trade:** Pest Control
- **Regulation:** Plant Protection Products (Sustainable Use) Regulations 2012
- **Frequency:** Certificate of Competence valid 5 years; CPD renewal via BASIS PROMPT Modules PA1: Foundation (mandatory for all) | PA2: Pedestrian applicator | PA6: Hand-held applicator — most common for urban pest control FM
- **Issuing body:** Lantra Awards / City & Guilds / BASIS PROMPT register
- **Required contractor accreditation:** —
- **Verification URL:** —

**Required fields (extract must populate):**
- `certificate_number`
- `issue_date`
- `expiry_date`

**Optional / schedule fields (include in Word table when possible):**
- `Name`
- `Modules held`
- `Certificate of Competence no.`
- `Issuing body`
- `Issue date`
- `Expiry date`
- `BASIS PROMPT registration no.`

**Word pack source columns:** Name | Modules held | Certificate of Competence no. | Issuing body | Issue date | Expiry date | BASIS PROMPT registration no.

**Suggested filename:** `54_PA1_PA2_PA6_{SiteOrVendor}.docx` (or include `PA1 PA2 PA6` / `Individual Pesticide Application Certificate — PA1 + PA2 / PA6` in the name)
