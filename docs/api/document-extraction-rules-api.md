# One document, every domain

A document is not one kind of thing. An FM contract names the supplier, the assets it covers,
the PPM frequency it commits to and the certificates the contractor must hold — four domains
in one file.

Ingestion used to pick **one** winner and run **one** extractor, and it picked from the
filename and the user's chat message, never from the document. So three of those four domains
were never read, and nothing said so: the file was indexed, the page said "ingested", and the
facts were simply absent. `scan_20260915_001.pdf` containing a contract reached no extractor
at all.

Every domain is now tested against every document, and a domain that is absent comes back
**with the reason**.

---

## The rules are data

`src/reference/document_extraction_rules.json` declares, per domain: what signals its
presence, which fields to fetch, and the **real column each field is destined for**. Adding a
field is an edit to that file — no pipeline change.

```jsonc
"vendors": {
  "label": "Vendors and contracts",
  "handler": "contract_performance.extract_contract_parameters",
  "signals": {
    "strong": ["service agreement", "contract value", "invoice number", "vat registration", …],
    "weak":   ["contractor", "vendor", "agreement", "invoice", …]
  },
  "fields": [
    {"name": "vendor_name",     "question": "The supplier, contractor or service provider named",
     "lands": "vendors.vendor_name",            "type": "text"},
    {"name": "visits_per_year", "question": "How many planned visits per year the agreement commits to",
     "lands": "vendor_contracts.visits_per_year","type": "number"}
  ]
}
```

**61 fields across five domains**, and every one resolves to a column that exists — verified,
not assumed. `GET /api/ingestion/extraction-rules/validate` re-checks it against whichever
database you are on, because the two disagree on shape. A rule pointing nowhere extracts into
nothing and reports success; this is what makes that loud. It caught three on the first run.

| Domain | Fields | Writes today |
|---|---|---|
| Compliance | 11 | **yes** — defers to the existing certificate pipeline |
| Vendors and contracts | 15 | **yes** — the contract extractor resolves or creates the vendor |
| Energy | 10 | no — held |
| Assets and parts | 13 | no — held |
| Maintenance | 13 | no — held |

**`writes: false` is stated rather than implied.** Three domains have no extractor behind
them. Their facts are returned and shown, never stored. Inventing a meter from a bill gives a
portfolio supply points nobody contracted; inventing a visit from a misread service sheet
creates evidence of work that may never have happened. The catalogue says which is which so
the page does not promise a fact will be stored when it will only be displayed.

---

## `GET /api/ingestion/extraction-rules`

The catalogue. What ingestion will look for, per domain, with the question that fetches each
field and the column it lands in.

## `POST /api/ingestion/extraction-plan`

```jsonc
{"text": "MASTER SERVICES AGREEMENT · Apex Mechanical Services Ltd …"}
```

```jsonc
{
  "ok": true,
  "domains_present": ["compliance", "vendors", "assets", "maintenance"],
  "domains_absent": ["energy"],
  "fields_total": 52,
  "detection": {
    "vendors": {"present": true,  "score": 22.0, "threshold": 3.0,
                "matched_strong": ["service agreement", "contract value", …],
                "why": "6 strong and 4 weak signals matched"},
    "energy":  {"present": false, "score": 1.0,
                "why": "only 0 strong and 1 weak signals matched, below the threshold of 3.0"}
  },
  "plan": {"vendors": {"handler": "…", "fields": ["vendor_name", "contract_start", …]}}
}
```

Model-free and cheap. It is the honest answer to "will this document give me my vendor data"
*before* anything is extracted, and it is what the ingest receipt should show.

**Absence is a finding.** `domains_absent` and the `why` behind it exist because "we looked
and this document says nothing about energy" and "energy was never checked" are different
statements, and only the first is worth trusting. A page that omits the absent domains makes
the second look like the first.

### How presence is decided

A strong signal counts 3, a weak one counts 1, and a domain is present at 3. So one strong
signal clears it, three weak ones do too — three weak words agreeing is itself evidence —
and one weak word is not. Matching is on word boundaries, so `ppm` does not fire inside
`ppmv`, which is a concentration, not maintenance.

Measured on four real document shapes:

| Document | Domains found |
|---|---|
| FM master services agreement | compliance, vendors, assets, maintenance — **52 fields** |
| Electricity bill | energy only |
| Service visit report | maintenance only |
| A lease | **none** — and it says so rather than guessing |

---

## Nothing is written on thin evidence

Facts carry a confidence, and `grade()` splits them three ways:

| Band | Threshold | What happens |
|---|---|---|
| accept | ≥ 0.85 | may be persisted by the domain's handler |
| review | 0.6 – 0.85, **and anything with no confidence at all** | a draft for a person |
| discard | < 0.6 | reported, then dropped |

Unknown is not high. A fact with no confidence goes to review, never to accept — treating
unknown as high is exactly how an unmeasured figure becomes a row that reads like a measured
one. An empty value is not a fact at any confidence.

---

## `POST /api/ingestion/import-plan` — CSV and Excel

The same catalogue, applied to spreadsheet headers. The migration flow's canonical registry
covered assets, work orders, parts, scheduled PM and users; **energy was not in it at all**,
so an MPAN column had no target and a half-hourly export became rows nobody could query.

A field's destination is declared once. Two registries disagreeing about where
`serial_number` lands is how a column silently stops arriving while the import still reports
success.

```jsonc
{"headers": ["MPAN", "Fuel", "Read To", "Consumption kWh", "Supplier"], "row_count": 1200}
```

```jsonc
{"would_write": [
   {"table": "meters",         "columns": ["mpan_mprn", "meter_type"], "rows": 1200},
   {"table": "meter_readings", "columns": ["reading_at", "consumption_kwh"], "rows": 1200}],
 "coverage_pct": 100.0, "blocked": [], "unmapped": []}
```

Measured on four real export shapes — asset register, half-hourly energy export, PPM schedule,
service visit log — **100% of columns mapped on each**, into `assets`, `meters`,
`meter_readings`, `ppm_visits`, `work_orders` and `inspections`. 239 aliases cover what real
exports call things: `EQUIP#`, `MPAN`, `p/kWh`, `Attended By`.

Note that one sheet is not one table. A PPM export carries visit, work-order and inspection
fields; writing it into one table would lose two thirds of it.

### Three things it refuses to do quietly

**A header that matches nothing is named, never guessed.** Adding an alias is a five-second
edit to the catalogue. A column quietly dropped is a field the customer thinks they imported.

**Two headers aimed at one column are held back.** The second write lands on top of the first
and the import still reports success — data loss that looks like a clean run. `Make` and
`Manufacturer` in one sheet both mean `assets.manufacturer`; both are blocked and reported.

**A header matching two domains equally is decided by the sheet, or left unresolved.** "Asset
Code" is the asset's own code in a register and the asset a visit was against in a PPM sheet.
When only unambiguous columns are counted and one domain leads outright, that settles it;
a draw stays a draw, because picking the first in dictionary order is a coin toss wearing a
confidence score. Passing `domains` settles it outright.

---

## The ingest flow reports what it read

`POST /api/workflow/run-stateful-with-files` now returns `extraction_plans` — one entry per
uploaded file, carrying `domains_present`, `domains_absent` and the reason for each absence.

It is advisory and never fatal: a plan that cannot be produced is recorded against its own
file rather than raised, because a receipt that cannot say what it looked for is worse than
one that says so, and neither is worth losing an upload over.

---

## `POST /api/ingestion/import` — the extractors

Energy, assets and maintenance now write. Dry run unless `apply: true`.

```jsonc
{"headers": ["MPAN", "Fuel", "Read To", "Consumption kWh"],
 "rows": [{"MPAN": "12 3456 7890 123", "Fuel": "electricity",
           "Read To": "2026-08-31", "Consumption kWh": "184220"}],
 "domains": ["energy"], "building_id": "…", "apply": true}
```

| Domain | Tables, in write order |
|---|---|
| assets | `assets` |
| energy | `energy_meters` → `meter_readings` |
| maintenance | `work_orders` → `ppm_visits` → `inspections` |

Order is not cosmetic — a reading resolves its `meter_id` against the meter the same run just
wrote.

### Five rules, each because the careless version fails silently

**The schema is probed, never assumed.** `hoistra_test` declares `work_orders.title`,
`priority` and `status` NOT NULL; `plenum_agent` declares almost nothing. `assets.id` has no
default on one. A row missing something the table actually requires is refused **by name**,
not attempted and rolled back halfway through a file.

**Writes are idempotent.** `asset_code`, `mpan`, `meter_id`+`reading_at`, `ppm_ref`, `wo_code`
— a row matching its natural key updates. Running the same file twice does not double a
portfolio.

**A human's edit survives an import.** Update fills empty columns and leaves populated ones
alone unless `overwrite` is asked for. Someone corrected a serial by hand; a re-import should
not quietly undo it.

**An ambiguous date is refused, not guessed.** `03/04/2026` is 3 April on a British export and
4 March on an American one, and the cell does not say which. Preferring one reading is right
about half the time, which is how a PPM visit lands a month out and nobody can see why. Pass
`day_first` to state the convention. `25/12/2026` needs no convention — there is no month 25.

**A reference that resolves to nothing is left empty.** The row then fails on its own missing
column, by name, which is fixable. A fabricated id is a reading pointing at the wrong meter
forever.

### What the real write found that a dry run could not

Two bugs surfaced only by executing against the database, inside a transaction that was rolled
back:

- **`assets.id` has no default on `hoistra_test`.** Excluding `id` from the required check —
  on the assumption a database generates its own keys — failed with a null violation on a
  column nothing had been asked for. Uuid keys with no default are now generated.
- **Energy was pointed at the wrong table entirely.** `meter_readings.meter_id` carries a
  foreign key to **`energy_meters.id`**, not `plenum_cafm.meters`. `meters` has the plausible
  column names, 18 rows on one database and 3 on the other, and nothing reads it for
  readings. The insert failed on the foreign key; the schema alone would not have said so.

Verified end to end on `hoistra_test`: assets 1, `energy_meters` 1, `meter_readings` 1 — the
reading resolving against the meter written in the same run — re-run inserting 0, rollback
clean, no probe rows left behind.

---

## What this does not do yet

**Documents still do not write into these three.** Energy, assets and maintenance return fields and land
nowhere. Building those means, for each: a Claude extraction pass against the domain's field
list, and a handler that writes with provenance. The rules and the targets are already
declared, so the work is the extractor, not the design.

**`standing_charge` has nowhere to live.** No column anywhere holds one. It is kept in the
catalogue with `lands: null` and a note, named as the gap it is rather than dropped — which
would have hidden it.
