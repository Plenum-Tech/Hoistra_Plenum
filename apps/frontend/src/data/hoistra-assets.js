/* Hoistra — asset condition from energy. Portfolio as at 2 Sep 2026.
   Condition is inferred, never asserted: a section's EUI against its reference
   says where the load is; an anomaly attributed to an asset says which asset.
   The two together are the only thing called a threat. */
export const HOISTRA_AS = (function () {

  /* Sections (zones) with their own sub-metered EUI. `ref` is the reference for
     the section's use: the building pack for general space, an IT reference for
     server rooms, a car-park reference for parking. kWh/m²/yr. */
  var sections = [
    { b: "Bishopsgate Tower", sec: "Central plant · basement", eui: 262, ref: 180, area: "1,400 m²", meter: "sub-meter · BMS trend" },
    { b: "Bishopsgate Tower", sec: "L4 East · tenant floor", eui: 231, ref: 180, area: "2,100 m²", meter: "sub-meter" },
    { b: "Bishopsgate Tower", sec: "L12–L20 · tenant floors", eui: 178, ref: 180, area: "18,900 m²", meter: "sub-meter" },
    { b: "Bishopsgate Tower", sec: "Car park", eui: 41, ref: 45, area: "3,200 m²", meter: "sub-meter" },
    { b: "Kingsway House", sec: "Whole building", eui: 198, ref: 180, area: "9,400 m²", meter: "building-level · inferred" },
    { b: "Town Hall", sec: "Plant room · DHW", eui: 204, ref: 180, area: "800 m²", meter: "sub-meter · gas" },
    { b: "Town Hall", sec: "Chambers · offices", eui: 171, ref: 180, area: "6,100 m²", meter: "sub-meter" },
    { b: "Meridian Quay", sec: "Server room · L2", eui: 412, ref: 380, area: "260 m²", meter: "sub-meter · IT reference" },
    { b: "Marina Heights", sec: "Chiller plant", eui: 296, ref: 228, area: "1,100 m²", meter: "BMS trend · kW/RT" },
    { b: "Northgate Mall", sec: "Common areas", eui: 212, ref: 205, area: "7,800 m²", meter: "utility account" },
    { b: "Raffles Link", sec: "L12 · AHU-2 zone", eui: 184, ref: 192, area: "1,900 m²", meter: "SP sub-meter" },
    { b: "Riverside Court", sec: "Fire pumps · basement", eui: 190, ref: 180, area: "300 m²", meter: "sub-meter" },
    { b: "Building 5", sec: "Car park", eui: 38, ref: 45, area: "2,600 m²", meter: "sub-meter" }
  ];

  /* Asset register. `anom` links to hoistway-data anomalies by id. */
  var assets = [
    { id: "AS-1042", name: "AHU-3", cls: "Air handling", b: "Bishopsgate Tower", sec: "L4 East · tenant floor", vendor: "Apex Mechanical", email: "ops@apexmechanical.co.uk", installed: 2009, ppm: "14 Jun 2026", anom: "a1", l1: false },
    { id: "AS-1007", name: "CHILLER-101", cls: "Chiller", b: "Bishopsgate Tower", sec: "Central plant · basement", vendor: "Apex Mechanical", email: "ops@apexmechanical.co.uk", installed: 2009, ppm: "02 Aug 2026", anom: "a2", l1: true },
    { id: "AS-1008", name: "CHILLER-102", cls: "Chiller", b: "Bishopsgate Tower", sec: "Central plant · basement", vendor: "Apex Mechanical", email: "ops@apexmechanical.co.uk", installed: 2009, ppm: "02 Aug 2026", anom: null, l1: true },
    { id: "AS-1011", name: "CHW pump P1", cls: "Pump", b: "Bishopsgate Tower", sec: "Central plant · basement", vendor: "Apex Mechanical", email: "ops@apexmechanical.co.uk", installed: 2015, ppm: "22 Jul 2026", anom: null, l1: false },
    { id: "AS-1188", name: "FCU L4-12", cls: "Fan coil", b: "Bishopsgate Tower", sec: "L4 East · tenant floor", vendor: "Apex Mechanical", email: "ops@apexmechanical.co.uk", installed: 2012, ppm: "30 Aug 2026", anom: null, l1: false },
    { id: "AS-1046", name: "AHU-7", cls: "Air handling", b: "Bishopsgate Tower", sec: "L12–L20 · tenant floors", vendor: "Apex Mechanical", email: "ops@apexmechanical.co.uk", installed: 2009, ppm: "14 Jun 2026", anom: null, l1: false },
    { id: "AS-4471", name: "Lift Asset-4471", cls: "Lift", b: "Bishopsgate Tower", sec: "L12–L20 · tenant floors", vendor: "Apex Lifts", email: "ops@apexlifts.co.uk", installed: 2010, ppm: "21 Aug 2026", anom: null, l1: true },
    { id: "AS-2207", name: "Boiler-7", cls: "Boiler", b: "Kingsway House", sec: "Whole building", vendor: "Meridian Heating Ltd", email: "ops@meridianheating.co.uk", installed: 2004, ppm: "11 May 2026", anom: "a3", l1: true },
    { id: "AS-2211", name: "AHU-1", cls: "Air handling", b: "Kingsway House", sec: "Whole building", vendor: "Apex Mechanical", email: "ops@apexmechanical.co.uk", installed: 2012, ppm: "19 Jul 2026", anom: null, l1: false },
    { id: "AS-3022", name: "Boiler-22", cls: "Boiler", b: "Town Hall", sec: "Plant room · DHW", vendor: "Meridian Heating Ltd", email: "ops@meridianheating.co.uk", installed: 2004, ppm: "overdue · 12 Aug 2026", anom: "a5", l1: true },
    { id: "AS-3025", name: "DHW calorifier", cls: "Hot water", b: "Town Hall", sec: "Plant room · DHW", vendor: "Meridian Heating Ltd", email: "ops@meridianheating.co.uk", installed: 2011, ppm: "03 Jun 2026", anom: null, l1: false },
    { id: "AS-5101", name: "CRAC-1", cls: "Precision cooling", b: "Meridian Quay", sec: "Server room · L2", vendor: "Apex Mechanical", email: "ops@apexmechanical.co.uk", installed: 2018, ppm: "08 Aug 2026", anom: "a4", l1: true },
    { id: "AS-7002", name: "Chiller plant CH-2", cls: "Chiller", b: "Marina Heights", sec: "Chiller plant", vendor: "Gulf Cooling", email: "fm@gulfcooling.ae", installed: 2014, ppm: "27 Jun 2026", anom: "a7", l1: true },
    { id: "AS-7001", name: "Chiller plant CH-1", cls: "Chiller", b: "Marina Heights", sec: "Chiller plant", vendor: "Gulf Cooling", email: "fm@gulfcooling.ae", installed: 2014, ppm: "27 Jun 2026", anom: null, l1: true },
    { id: "AS-7010", name: "Cooling tower CT-1", cls: "Cooling tower", b: "Marina Heights", sec: "Chiller plant", vendor: "Gulf Cooling", email: "fm@gulfcooling.ae", installed: 2014, ppm: "12 Jul 2026", anom: null, l1: false },
    { id: "AS-8301", name: "Common area lighting", cls: "Lighting", b: "Northgate Mall", sec: "Common areas", vendor: "Metro Facilities", email: "dispatch@metrofacilities.com", installed: 2016, ppm: "15 Aug 2026", anom: "a8", l1: false },
    { id: "AS-9112", name: "AHU-2 · level 12", cls: "Air handling", b: "Raffles Link", sec: "L12 · AHU-2 zone", vendor: "Sembawang M&E", email: "service@sembawang-me.sg", installed: 2017, ppm: "20 Aug 2026", anom: "a9", l1: false },
    { id: "AS-6404", name: "Wet riser pump", cls: "Pump", b: "Riverside Court", sec: "Fire pumps · basement", vendor: "Sentinel Fire Systems", email: "ops@sentinelfire.co.uk", installed: 2008, ppm: "04 Sep 2026 · scheduled", anom: null, l1: true },
    { id: "AS-5510", name: "Car park lighting", cls: "Lighting", b: "Building 5", sec: "Car park", vendor: "Northgate Electrical", email: "ops@northgateelectrical.co.uk", installed: 2019, ppm: "01 Jul 2026", anom: "a6", l1: false }
  ];

  /* Asset classes: register-average replacement value, design life, and a wear
     coefficient — how much of an energy deviation is read as condition
     deviation. Rotating plant close to 1; static or lightly loaded kit less. */
  var classes = {
    "Chiller": { replace: 140000, life: 20, wear: 1.0 },
    "Boiler": { replace: 60000, life: 20, wear: 1.0 },
    "Air handling": { replace: 48000, life: 20, wear: 0.9 },
    "Generator": { replace: 142000, life: 25, wear: 1.0 },
    "Pump": { replace: 12000, life: 15, wear: 0.8 },
    "Fan coil": { replace: 2500, life: 15, wear: 0.6 },
    "Cooling tower": { replace: 55000, life: 20, wear: 0.8 },
    "Precision cooling": { replace: 35000, life: 15, wear: 0.9 },
    "Hot water": { replace: 18000, life: 20, wear: 0.5 },
    "Lighting": { replace: 22000, life: 12, wear: 0.3 },
    "Lift": { replace: 180000, life: 25, wear: 0.4 }
  };

  /* What each condition recommends, by asset class. Inspection first where the
     cause is unknown; a work order where the anomaly type already names it. */
  var recommend = {
    threat: { primary: "wo", label: "Raise work order", why: "Section over reference and an anomaly attributed to this asset. Both signals agree; a work order is justified without waiting for a fault." },
    zone: { primary: "inspect", label: "Request inspection", why: "The section is over reference but no anomaly is attributed to this asset. It shares the load; an inspection settles whether it contributes." },
    persist: { primary: "inspect", label: "Request inspection", why: "The section is in control, but the anomaly has persisted past the threshold. Something on this asset has changed; inspect before it shows in the section." }
  };

  /* Instrumented assets. Two with sensors streaming into the graph: a standby
     generator and an air handling unit. `hist` is the last 24 readings (one per
     hour); `lo`/`hi` is the normal band from the OEM manual or commissioning;
     `drift` is what the live tick adds so the trend keeps moving. */
  var iot = [
    {
      id: "AS-1101", name: "Standby generator GEN-1", cls: "Generator · 800 kVA diesel", b: "Bishopsgate Tower", sec: "Central plant · basement",
      vendor: "Apex Mechanical", email: "ops@apexmechanical.co.uk", feed: "Modbus TCP → MQTT · 1 Hz", sensors: 14, installed: 2009, designLife: 25, hoursRun: 1840,
      readings: [
        { k: "Coolant temp", v: 87, u: "°C", lo: 70, hi: 95, hist: [82,82,83,83,84,84,84,85,85,85,86,86,86,86,87,87,87,87,87,87,88,88,87,87], drift: 0.02, note: "creeping up on load test — was 79 °C in March" },
        { k: "Oil pressure", v: 3.1, u: "bar", lo: 3.0, hi: 5.5, hist: [3.9,3.9,3.8,3.8,3.8,3.7,3.7,3.6,3.6,3.5,3.5,3.4,3.4,3.4,3.3,3.3,3.3,3.2,3.2,3.2,3.1,3.1,3.1,3.1], drift: -0.005, note: "at the low limit; bearing wear or pump" },
        { k: "Battery voltage", v: 25.8, u: "V", lo: 25.5, hi: 28.5, hist: [27.4,27.4,27.3,27.2,27.2,27.1,27.0,26.9,26.8,26.7,26.6,26.5,26.4,26.3,26.2,26.1,26.0,26.0,25.9,25.9,25.9,25.8,25.8,25.8], drift: -0.003, note: "float charge falling; starter battery near end of life" },
        { k: "Fuel level", v: 78, u: "%", lo: 60, hi: 100, hist: [80,80,80,80,79,79,79,79,79,79,79,78,78,78,78,78,78,78,78,78,78,78,78,78], drift: 0, note: "" },
        { k: "Frequency on test", v: 49.6, u: "Hz", lo: 49.5, hi: 50.5, hist: [50.0,50.0,50.0,49.9,49.9,49.9,49.9,49.8,49.8,49.8,49.8,49.7,49.7,49.7,49.7,49.7,49.6,49.6,49.6,49.6,49.6,49.6,49.6,49.6], drift: 0, note: "governor hunting under step load" },
        { k: "Vibration", v: 7.2, u: "mm/s", lo: 0, hi: 7.1, hist: [4.1,4.2,4.2,4.4,4.5,4.6,4.8,5.0,5.1,5.3,5.5,5.6,5.8,6.0,6.1,6.3,6.5,6.6,6.8,6.9,7.0,7.1,7.1,7.2], drift: 0.01, note: "ISO 10816 zone C — unsatisfactory for long-term running" },
        { k: "Exhaust temp", v: 512, u: "°C", lo: 350, hi: 550, hist: [470,472,474,478,480,483,486,488,490,492,495,497,499,501,503,505,506,508,509,510,511,511,512,512], drift: 0.05, note: "" },
        { k: "Load on test", v: 62, u: "%", lo: 30, hi: 100, hist: [60,60,61,61,61,62,62,62,62,62,62,62,62,62,62,62,62,62,62,62,62,62,62,62], drift: 0, note: "" }
      ],
      model: { horizon: "90 days", pFail: 41, accuracy: 91, precision: 84, recall: 88, trained: "2,140 generator-years · 312 failures", drivers: ["vibration trend (+75% in 24 h of running)", "oil pressure at low limit", "battery float decay"] },
      life: { design: 25, age: 17, rulP50: 14, rulLo: 9, rulHi: 22, unit: "months" },
      value: { book: "£38,000", replace: "£142,000", remediate: "£9,600", remediateWhat: "bearing set, oil pump, starter batteries, governor recalibration", remediateGain: "RUL +36 months · vibration back to zone A · pFail 41% → 9%", replaceGain: "new 25-year life · 12% lower fuel burn · Stage V emissions · book value £142k", when: "Remediate inside 6 weeks — before the next statutory load test. Replacement case opens in 2029 when RUL after remediation runs out." },
      recommend: "remediate"
    },
    {
      id: "AS-1042", name: "AHU-3", cls: "Air handling · 12,000 m³/h", b: "Bishopsgate Tower", sec: "L4 East · tenant floor",
      vendor: "Apex Mechanical", email: "ops@apexmechanical.co.uk", feed: "BACnet/IP → MQTT · 0.2 Hz", sensors: 11, installed: 2009, designLife: 20, hoursRun: 61200,
      readings: [
        { k: "Supply air temp", v: 15.8, u: "°C", lo: 13, hi: 16, hist: [14.0,14.0,14.1,14.1,14.2,14.3,14.4,14.5,14.6,14.7,14.9,15.0,15.1,15.2,15.3,15.4,15.5,15.5,15.6,15.7,15.7,15.8,15.8,15.8], drift: 0.004, note: "setpoint 14 °C not being met — cooling valve at 100%" },
        { k: "Return air temp", v: 24.1, u: "°C", lo: 21, hi: 24, hist: [22.8,22.8,22.9,23.0,23.0,23.1,23.2,23.3,23.3,23.4,23.5,23.6,23.6,23.7,23.8,23.8,23.9,23.9,24.0,24.0,24.0,24.1,24.1,24.1], drift: 0.003, note: "" },
        { k: "Relative humidity", v: 61, u: "%", lo: 40, hi: 60, hist: [52,52,53,53,54,54,55,55,56,56,57,57,58,58,58,59,59,60,60,60,61,61,61,61], drift: 0.01, note: "over 60% — condensation risk in the ductwork" },
        { k: "Filter ΔP", v: 285, u: "Pa", lo: 50, hi: 250, hist: [190,192,195,198,202,206,210,214,218,222,227,232,237,242,247,252,257,262,267,272,276,280,283,285], drift: 0.08, note: "past change-out threshold; two PPM visits deferred" },
        { k: "Fan motor current", v: 18.4, u: "A", lo: 12, hi: 17.5, hist: [14.8,14.9,15.0,15.1,15.3,15.4,15.6,15.8,16.0,16.2,16.4,16.6,16.8,17.0,17.2,17.4,17.6,17.8,17.9,18.1,18.2,18.3,18.4,18.4], drift: 0.004, note: "working harder against the blocked filter" },
        { k: "Fan vibration", v: 4.6, u: "mm/s", lo: 0, hi: 4.5, hist: [2.9,2.9,3.0,3.0,3.1,3.2,3.2,3.3,3.4,3.5,3.6,3.7,3.8,3.9,4.0,4.1,4.2,4.3,4.4,4.5,4.5,4.6,4.6,4.6], drift: 0.003, note: "belt or bearing; rising with current" },
        { k: "CO₂ (zone)", v: 880, u: "ppm", lo: 400, hi: 1000, hist: [720,730,740,750,760,770,780,790,800,810,820,830,840,850,855,860,865,870,872,875,878,880,880,880], drift: 0.2, note: "" },
        { k: "Run hours / week", v: 118, u: "h", lo: 60, hi: 84, hist: [84,84,84,86,88,90,92,95,98,100,103,106,108,110,112,114,115,116,117,117,118,118,118,118], drift: 0, note: "running unoccupied hours — the non-occupancy anomaly" }
      ],
      model: { horizon: "90 days", pFail: 67, accuracy: 89, precision: 81, recall: 90, trained: "6,800 AHU-years · 1,140 failures", drivers: ["fan current + vibration rising together", "filter ΔP past threshold for 3 weeks", "supply setpoint unmet with valve saturated"] },
      life: { design: 20, age: 17, rulP50: 7, rulLo: 3, rulHi: 12, unit: "months" },
      value: { book: "£6,200", replace: "£48,000", remediate: "£3,400", remediateWhat: "filter change, belt and bearing set, cooling coil clean, schedule reset to occupied hours", remediateGain: "RUL +18 months · fan current −20% · £38k/yr anomaly closed · pFail 67% → 22%", replaceGain: "EC fan and heat recovery · 35% lower fan energy · supports EPC B measure · book value £48k", when: "Remediate now — it closes the £38,400 anomaly and holds the unit to 2028. Replace in the 2028 capex round; the EPC B case funds it." },
      recommend: "remediate"
    }
  ];

  return { sections: sections, assets: assets, classes: classes, recommend: recommend, iot: iot };
})();
