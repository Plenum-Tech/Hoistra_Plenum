# Ingestion validation — API for the frontend

A document is bound to a building only after a **validation case** for it has been decided.
Binding is what makes a document evidence: from that moment it counts in the building's
compliance position, its renewal ladder and its reports. So the check runs in between, and
the protocol is the same for a user and for an admin — selecting the wrong building is a
mistake seniority does not prevent.

Two services are involved and the split is deliberate:

| | Service | Base |
|---|---|---|
| The check, the case, the audit | operations-intelligence | `/backend/ops-intelligence/api/ingestion/…` |
| The conversation + the filing it releases | deep-agents | `/backend/deep-agents/api/ingestion/…` |

**Use the deep-agents routes from the UI.** They forward each step to the authority and, on
an approval, perform the bind that was withheld. The operations-intelligence routes are the
same protocol without the binding — use them for reading cases, the ontology and the audit.

Every route takes the caller's bearer token. A case on a building you are not allocated to
does not exist as far as you are concerned (404/403 as appropriate).

---

## 1. Upload → the gate

`POST /backend/deep-agents/api/workflow/run-stateful-with-files` (unchanged contract) now
returns two extra fields:

```jsonc
{
  "answer": "**Held for a check — nothing has been filed yet.** …",
  "validation_cases": [ /* one case per uploaded file — see §3 */ ],
  "validation_held":  ["<case id>", …]      // the cases that stopped this upload
}
```

* `validation_held` empty → every file matched its building and was filed as before.
* `validation_held` non-empty → those files are **indexed and registered but not bound**.
  `answer` already contains a readable notice naming each file, the reason and the suggested
  building. Render the conversation from `validation_cases` and drive it with §2.

A file is only ever bound when its case is `matched`, or after a decision.

---

## 2. The conversation

### `POST /api/ingestion/cases/{case_id}/clarify`
```json
{"explanation": "Aqua took over the water hygiene contract from Northern in May."}
```
Returns the case with `assessment` (`resolves`, `addressed`, `unaddressed`, `reason`,
`method`) and a new `message` — always a question. **Nothing is filed by this call**, whatever
the assessment says.

### `POST /api/ingestion/cases/{case_id}/reassign`
```json
{"building_id": "4791b70b-…"}
```
Moves the case and **re-runs the check against the new building**. Agreeing to a suggestion
is not evidence: if it does not match there either, the new `message` says so.

### `POST /api/ingestion/cases/{case_id}/decide`
```json
{"approve": true, "note": "optional"}
```
`approve` is required — there is no default, and a client that omits it gets 422. On a yes
the document is filed and the response carries `bound: {documents, certificates}`. On a no
nothing is filed.

The recorded `outcome` distinguishes the four ways a decision arrives:

| outcome | what happened |
|---|---|
| `accepted` | the document matched the building and was filed |
| `approved_on_confirmation` | it did not match, the explanation accounted for it, a person confirmed |
| `overridden` | it did not match, the warning stood, a person filed it anyway — recorded against their name |
| `reassigned` | it was moved to another building and filed there |
| `rejected` | not filed |

### `GET /api/ingestion/cases?open_only=true[&building_id=]`
Everything waiting on somebody. `GET /api/ingestion/cases/{id}` returns one in full.

---

## 3. The case object

```jsonc
{
  "id": "…", "status": "needs_clarification", "verdict": "mismatch", "confidence": 0.75,
  "open": true, "may_ingest": false,
  "document_name": "UKRI-ProcurementContract-2938.pdf", "doc_type": "contract",
  "selected_building_id": "f12e9629-…",      // what the uploader chose
  "suggested_building_id": "4791b70b-…",     // what fits better, when something does
  "final_building_id": null,                 // set on approval
  "question": "The document names “Bishopsgate Tower”; this building is “Riverside Court”. …",
  "findings": [
    {"check": "building_name", "direction": "conflicts", "weight": 3.0,
     "claimed": "Bishopsgate Tower", "known": "Riverside Court",
     "message": "The document names “Bishopsgate Tower”; this building is “Riverside Court”."}
  ],
  "claims":   { "buildings": [...], "vendors": [...], "countries": [...], "sources": {...} },
  "ontology": { "name": "Riverside Court", "vendors": [...], "counts": {...}, "is_new": false },
  "candidates": [{"building_id": "…", "name": "Bishopsgate Tower", "score": 3.0}],
  "explanation": null, "assessment": {}, "events": [ /* the conversation, in order */ ],
  "outcome": null, "decided_by": null, "decided_at": null
}
```

**`verdict`**

| verdict | meaning | `status` |
|---|---|---|
| `matched` | something *identifies* this building — its name, its reference or its postcode — and nothing contradicts it | `validated` (proceeds) |
| `uncertain` | nothing contradicts it and nothing confirms it: a new building, a document naming no property | `needs_confirmation` |
| `mismatch` | something the document says contradicts this building | `needs_clarification` |

A country that agrees is **not** identity — half a portfolio is in the same country — so it
never produces `matched` on its own. Neither does a vendor who works here.

**`status`** → `validated` · `needs_clarification` · `needs_confirmation` · then one of
`accepted` · `overridden` · `reassigned` · `rejected`. `may_ingest` is true only in the last
three of those (excluding `rejected`), and it is the permission to bind.

**`findings[].direction`** → `supports` · `conflicts` · `unknown`. `unknown` is deliberate and
must not be rendered as a warning: "this building has no vendors on record to check against"
is not "the vendor is wrong".

---

## 4. The checks

| check | conflicts when | supports when |
|---|---|---|
| `building_name` | the document names a property whose identifying words do not overlap this one's | it names this one (exact, contained, or ≥ 70% of its identifying words) |
| `building_code` | it carries a building reference that is not this building's | it carries this building's |
| `country` | the document's country differs — regulations, vendors and certificate types differ by country | it agrees |
| `region` / `state` | the document's city or region differs | it agrees |
| `postcode` | a different postcode | this building's postcode |
| `vendor` | the named firm is not recorded at this building (and the building has vendors on record) | it is |
| `assets` | none of the asset references are on this building (and the building has assets) | at least one is |
| `contract_ref`, `certificate_number` | — | the reference is already on record here |
| `building_evidence` | — | reported `unknown` when the building has nothing on record: a new building confirms nothing |

Claims are read from extractor fields first (`building_name`, `vendor_name`, `country_code`,
…), then from labelled text (`Site:`, `Property:`, `Contractor:`, a postcode, an asset code),
then from the file name. An unlabelled name in a contract body is **not** taken as a claim.

`GET /api/ingestion/buildings/{building_id}/ontology` returns exactly what a document is
checked against — names, codes, place, vendors, assets, equipment, floors, meters,
certificates, contracts, documents, `counts` and `is_new`.

---

## 5. New buildings

A building created this morning has nothing to check against. The protocol does not change:
the check still runs, `building_evidence` reports `unknown`, the verdict is `uncertain`
(never `matched` on thin air), and an explicit yes/no is required. The one thing that can
still identify a new building is its own name, and that does produce `matched`.

---

## 6. The audit trail

`GET /backend/ops-intelligence/api/ingestion/audit[?case_id=&building_id=&outcome=]` and the
company view at `GET /api/admin/ingestion-audit`.

Every step is recorded — `validated`, `held`, `clarified`, then the decision — with the user
and their role, the company, the building selected, the building it was moved to, the
document, the time, the checks that ran, the warning shown, the explanation given, the
agent's assessment of it, who approved, and the final outcome. Rows carry `case_id`, so the
whole conversation behind a decision is one filter away.
