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

## What this does not do yet

**It does not run inside the ingest flow.** The catalogue, the detector and the plan are here
and tested; wiring them into `run-stateful-with-files` so every upload is read for all five
domains is the next step. Today that endpoint still classifies on filename and message.

**Three domains have no extractor.** Energy, assets and maintenance return fields and land
nowhere. Building those means, for each: a Claude extraction pass against the domain's field
list, and a handler that writes with provenance. The rules and the targets are already
declared, so the work is the extractor, not the design.

**`standing_charge` has nowhere to live.** No column anywhere holds one. It is kept in the
catalogue with `lands: null` and a note, named as the gap it is rather than dropped — which
would have hidden it.
