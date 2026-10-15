# Investigation sources: BMS trend, utility bill and weather, summarised by the orchestrator

Date: 28 Sep 2026 · Status: design approved in conversation, awaiting spec review · Branch: `test` (uncommitted)

## Why

The Investigate panel on the Assets page shows one line per source, for example
"bms_trend · 96 points · 0 out of band". The data behind that line never leaves ops-intelligence:
the readings over time, the weekly consumption, the monthly degree days. The orchestrator in the
dock has none of it either. On the Assets page, `chatContext()` tells svc-deepagents only
"N assets and M work orders", and deepagents has no tool that reads an asset's BMS, bill or
weather. So "summarise the BMS trend" is answered with no data behind it.

## Outcome

1. Three read-only endpoints on svc-operations-intelligence return the data behind the
   `bms_trend`, `utility_bill` and `weather` lines.
2. Every Investigate automatically ends with an orchestrator summary of those three datasets.
   The summary is checked so that it never cites a figure the data does not contain.
3. A follow-up question typed in the dock while an investigation is open reaches the
   orchestrator with the same three datasets attached.

**Success check:** Boiler 1 at Bishopsgate Tower. Its summary must agree with what the workbook
and the fixed walk already establish:

- BMS: 96 points, 0 out of band. Latest readings 69.4 °C (band 5–95) and 4.02 bar (band 3–5.5).
- Bill: no comparable period. Supply-meter history starts 2025-09-28.
- Weather: heating and cooling degree days for London from Open-Meteo, for this period against
  the same dates last year.
- AHU-3: six readings out of band, now a finding and part of the conclusion.

## Constraints

- **No database writes.** Every new endpoint only reads. The summarizer reads nothing: it sees
  only what the frontend passes it.
- **Scoped to the company in view.** The same `building_ids_for(session, s, None)` scoping as
  `/investigate`. An asset outside the caller's buildings gets a 404, the same answer as an asset
  that does not exist.
- **Absence is stated, never filled.** Each dataset carries `status`:
  - `found` — data was read.
  - `not_found` — the query ran and there is nothing on record.
  - `unreadable` — the query failed.
  
  The summary uses the same three words.
- **Nothing is committed** without Hussain asking.

## 1. Backend: svc-operations-intelligence

### New module `src/engines/energy/asset_sources.py`

This module holds the three reads. The existing Investigate walkers in `investigate.py` are
refactored to call these functions and derive their one-line answers from the result. That way
the walk's line and the endpoint's dataset cannot disagree.

Every query follows the rules the 28 Sep fixes established:

- Casts are `CAST(:p AS type)`, never `:p::type`.
- Window bounds are `CAST(:since AS timestamptz)`, because some timestamp columns on hoistra_test
  have no time zone.
- A failed query returns `None`, and the dataset reports `unreadable`.

### `GET /api/energy/assets/{asset_id}/bms-trend?weeks=8`

- `weeks` must be between 1 and 52; the default is 8.
- The asset may be given by id or by code; it is resolved with `anom_svc.resolve_asset`, as
  `/investigate` does.
- `kind` says which record was read:
  - `"chiller_performance"` when `chiller_performance_readings` has rows for the asset in the
    window. In this case, `series` holds the daily mean kW/RT with the design figure. `drift`
    only appears when it was measured at matched ambient, reusing the existing rule.
  - `"asset_readings"` otherwise.
- `types` has one entry per `reading_type`:
  - `unit`
  - `band` (`lo`, `hi`, `note`), from `asset_intelligence.bands_for`
  - `days`: a list of `{day, min, mean, max, n, out_of_band}`
  - `latest`: `{value, at, state}`
  - `points`
  - `out_of_band`
- It also returns `points`, `reading_types`, `first_at`, `last_at` and `detail`.
- Daily buckets rather than raw points keep eight weeks of hourly data small enough to summarise.

### `GET /api/energy/assets/{asset_id}/utility-bill?weeks=8`

- Reads the asset's building. **Supply meters only** (`is_sub_meter` false). Sub-meters are used
  only when the building has no supply meter; `meters_basis` says which.
- The data is **split by fuel** (`meter_type`). Each fuel has:
  - `weeks`: a list of `{week_start, now_kwh, last_year_kwh, now_days, last_year_days}`
  - `now_kwh` and `last_year_kwh` totals
  - `now_days` and `last_year_days`
  - `change_pct`, **present only when** `last_year_days >= BILL_COVERAGE_MIN (0.9) × now_days`
  - `cost_gbp`, only when every meter used carries `tariff_gbp_per_kwh`, marked "at the meter's
    tariff"
- `meters` lists `{meter_ref, meter_type, is_sub_meter, first_reading_at}`.
- `basis` reads: "metered — there is no utility-bill register on this platform, so the meter
  stands in for the bill".
- "Last year" is the same calendar window a year earlier. The existing `_a_year_before` handles
  29 February.

### `GET /api/energy/weather/degree-days?building_id=…&months=12`

This is the read counterpart of the existing `POST /api/energy/weather/degree-days`, on the same
path. `months` must be between 1 and 36; the default is 12. The building must be in the caller's
buildings, checked with the `access.assert_owned` pattern that
`/buildings/{building_id}/operating-hours` uses.

The data is real weather from **Open-Meteo**, chosen by Hussain on 28 Sep. Nothing is stored.

- **Precedence.** When `weather_degree_days` holds rows covering the requested months, they are
  used first: they are the platform's own record, for example from an official supplier.
  Otherwise the figures are computed on the fly from Open-Meteo. `source` names which.
- **Location.** The building's site supplies postcode, city and country. They are read with
  `to_jsonb(s)->>'…'`, so a database without those columns still answers. The country is mapped
  to ISO, so `UK` becomes `GB`. The geocoder
  (`https://geocoding-api.open-meteo.com/v1/search?name=…&countryCode=…&count=1`) tries the
  postcode first and then the city. The response includes
  `location: {query, name, latitude, longitude, resolved_from}`. Bishopsgate's workbook postcode,
  `EC2M 9ZZ`, geocodes to nothing, so it resolves from "London" (51.5085, −0.1257), checked from
  the container on 28 Sep.
- **Temperatures.** Daily mean temperature at 2 m, from
  `https://archive-api.open-meteo.com/v1/archive?latitude&longitude&start_date&end_date&daily=temperature_2m_mean&timezone=auto`.
- **Degree days.** At base 15.5 °C, the table's existing default:
  - HDD = Σ max(0, 15.5 − T)
  - CDD = Σ max(0, T − 15.5)
  
  The endpoint returns `months`, a list of `{month, hdd, cdd, days, last_year_hdd,
  last_year_cdd, last_year_days}`, plus totals and `hdd_change_pct` / `cdd_change_pct`.
- **Window end.** The window ends at the last day the archive holds a value, and never later than
  yesterday, because today is partial. Last year is clipped to the same dates. The response names
  the end date.
- **Failure.** A 10-second timeout, an HTTP error or an empty archive gives `unreadable` with the
  reason, never "no degree days". A building with no postcode or city on record gives
  `not_found` with "no location on record".
- **Caching.** Geocodes and archive answers are cached in process for 6 hours.
- **Licence.** The free endpoint is licensed by Open-Meteo for **non-commercial use**. When
  `OPEN_METEO_API_KEY` is set, the client uses the customer endpoints with `apikey`. **Plenum must
  decide on the paid key before production.**
- **The Investigate weather line** uses the same function over its 8-week window. It asks "heating
  and cooling degree days vs the same weeks last year". Its badge gives both changes, with "flat"
  inside 10%. `flat` stays cooling-based for the chiller rule it serves; `hdd_flat` is added for
  heating plant.

### Evidence: readings outside their bands

This addition was requested by Hussain on 28 Sep, after seeing AHU-3 read "6 out of band" while
its conclusion talked only about a missing report. AHU-3's latest readings: fan current 18.4 A
(band 12–17.5), vibration 4.6 mm/s (0–4.5), filter ΔP 285 Pa (50–250), humidity 61 % (40–60),
return air 24.1 °C (21–24), run hours 118 h/week (60–84).

- **New rule in `_evidence`.** Kind `"out of band"`, confidence `CONF_READING_OUT_OF_BAND = 1.0`,
  source `bms_trend`. It fires when the asset's own BMS readings have a latest value outside its
  band. The statement names each reading with its value, unit and band, and how many days of the
  window it was out of band. A reading measured against the limit set for it is a fact, and no
  cause is inferred, so the confidence is not discounted.
- **Conclusion.**
  - With a missing record as well: "{readings} are outside their bands, and the last visit closed
    without a report." Confirmation is required.
  - Alone: "{readings} are outside their bands." The caveat says the readings establish the
    symptom, not the cause, and to confirm on site.
  - The chiller-specific conclusions keep their precedence.
- **Action.** `inspect_out_of_band`: `POST /api/work-orders/` with `request_type: "inspection"`.
  Its `issue_description` lists the readings against their bands. The frontend already turns such
  an action into an inspection email draft (`assetsActions.js`, the `request_type` check), so the
  dock needs no change for it.

## 2. Summarizer: svc-deepagents

### `POST /api/workflow/investigation-summary`

- The route lives on the existing workflow router, so it has the same
  `current_principal` auth as `/api/workflow/run-stateful`.
- The logic lives in a new module, `src/agents/investigation_summary.py`.
- The request body is `{asset: {name, building}, sources: {bms_trend, utility_bill, weather}}`.
  The sources are the three datasets exactly as the frontend fetched them.
- It is not recorded as a chat turn and holds no session.

### How it works

- **Model call.** One call, with Anthropic called directly, the way
  `contract_answer.write_answer` does. The model defaults to `claude-sonnet-5`, the key is
  `settings.anthropic_api_key`, and `client` is the seam for tests.
- **Instructions.** Return JSON: `{lines: [{source, text}], overall}`. That is one line per
  source and one overall sentence, about 120 words in total. Use only the supplied data.
  Describe `not_found` as "not on record" and `unreadable` as "could not be read". The bill line
  must say it is metered, not billed. For heating plant, the weather line uses heating degree
  days.
- **Number check.** Each line's numbers go through `contract_answer`'s grounding helpers
  (`_row_numbers`, `_is_grounded`, `ungrounded_numbers`). That check tolerates rounding and a sum
  or difference of two supplied figures.
  - A line citing a figure that is not in the data is **dropped and named** in `dropped`.
  - The `overall` sentence is **reported** in `dropped` if it cites such a figure, and is not
    rewritten. This is the rule `contract_answer.ground` already follows.
- **Size.** The input is cut to a character budget. When it is cut, `meta.truncated` is true and
  the model is told, so a cut dataset is never summarised as absent.
- **Response.** `{ok, lines, overall, dropped, meta: {model, ms, truncated}}`.
- **Failure.** Any of these returns `{ok: false, reason}`:
  - no key
  - a model error
  - no JSON in the reply
  - every line dropped

## 3. Frontend

- **API.** Add `energyApi.assetBmsTrend(id, weeks)`, `energyApi.assetUtilityBill(id, weeks)` and
  `energyApi.degreeDays(buildingId, months)`. All three use `withOrg()`, like every other Assets
  read. Add `deepAgentsApi.investigationSummary(body)`.
- **Fetch.** `asCondInvestigate` fetches the three datasets **in parallel with**
  `/investigate`. Weather uses the row's `building_id`. The results are stored on `inv.datasets`,
  with a separate error for each source.
- **Automatic summary.**
  - Once the walk has finished playing (`invStage` reaches 3) and the datasets are in, the
    summarizer is called **once** per investigation.
  - The result is appended to the investigation thread (`inv.replies`) as an orchestrator message
    tagged `summary ·`: one line per source, prefixed with the source name, then the overall
    sentence.
  - Dropped lines are named: "1 line withheld — it cited a figure not in the data".
  - A failure reads "Summary unavailable — reason". The walk and the datasets stay.
- **Stale answers.** The existing `_invToken` guard applies. If the dock is closed or a newer
  investigation starts first, the late summary is discarded.
- **Thread rendering.** A reply with no `you` part does not draw the empty speech bubble. Today
  the bubble is always drawn.
- **Follow-ups.** In `chatContext()`, on the Assets module, when an investigation is open with
  its datasets loaded, a block is added. It names the asset and building, attaches a compact JSON
  of the three datasets (capped at about 6,000 characters, with the cap stated when hit), and
  repeats the not-on-record rule. The dock already renders the chat transcript below the
  investigation, so follow-up answers appear under it.

## Testing

- **ops-intelligence** (run on the host, unit only), extending
  `tests/unit/test_investigate_sources.py`'s `FakeSession`:
  - every `asset_sources` statement compiles with no unbound parameter
  - supply-only counting, with the sub-meter fallback
  - the fuel split
  - daily and weekly buckets
  - the coverage rule, with and without `change_pct`
  - `unreadable` on a failed query
  - cost only when every meter has a tariff
  - routes: 404 outside scope, and `weeks` / `months` bounds
  - the existing 33 Investigate tests stay green
- **deepagents** (testenv container, at real repo depth — see the backend test-run memory), with
  a fake client:
  - a line with an invented number is dropped and named
  - an ungrounded `overall` is reported, not rewritten
  - `not_found` becomes "not on record"
  - truncation is flagged
  - no key returns `ok: false`
  - JSON wrapped in prose is still parsed
- **Frontend** (`npm test`, then `npm run build`):
  - the datasets are fetched with the company scope
  - the summary is appended exactly once
  - a stale summary is dropped
  - a failure shows its reason
  - the context block appears only while an investigation is open, and respects its cap
  - a reply with no `you` part draws no bubble
- **Live:** rebuild ops-intelligence and svc-deepagents (with `--no-deps`, using the same compose
  files as the running containers) and confirm the modules are live in the containers. Then run
  Investigate on Boiler 1 and compare the summary with the success check above.

## Out of scope

- An orchestrator *tool* that fetches these datasets itself, for questions asked away from an
  open investigation. This is approach 3 from the conversation, and worth adding later.
- Tying an out-of-band reading to a work order's wording, for example "fan" matching
  `fan_current`. That is keyword inference; the summarizer and the orchestrator can draw the
  connection in words.
- A Google Weather API integration. It holds only 24 hours of history, so it cannot compare
  periods.
- Fixing `asset_detail` passing `criticality` as the band category.
- The work-order vendor gap ("planned maintenance by the vendor").

## As built (28 Sep 2026)

Four things changed from the design above while it was being built, each for a reason found in
the work.

- **Degree days take `weeks` as well as `months`.** Without it, the dock would fetch whole months
  starting on the 1st, while the walk's weather line covers exactly the last 8 weeks. The summary
  would then quote figures that differ from the line right above it. `?weeks=8` gives the walk's
  own window.
- **Last year is withheld from the summarizer when it is not a comparison.** On the first live
  run, the bill was marked not comparable, but the model still wrote that metered consumption had
  "risen enormously". It got there from the 8,383 kWh of last year's single day on record.
  `investigation_summary._withhold` now removes last year's figures wherever `comparable` is
  false. Grounding is checked against the withheld data, so a line quoting 8,383 is dropped. The
  rules also forbid describing a rise or a fall without a comparison. On the re-run, no line
  claimed a change.
- **The summary sits first in the investigation's thread.** It is the walk's own conclusion. When
  it lands after the reader has already sent a draft, it still goes under the walk, not below the
  send confirmation.
- **The finding chip says what kind of fact it is.** It showed "missing record" for every finding.
  It now shows "out of band" for a reading outside its band.

**Verified live, without a user session or the database:**
- The three ops-intelligence routes and the summary route answer 401 when not signed in. An
  unknown route answers 404, which shows the new routes exist.
- The real `degree_days` code path, run against a stand-in for the database that returns only the
  workbook's site row, gave London for 3 Aug – 27 Sep 2026: HDD 0.7 against 26.9 (−97.4%), CDD
  221.4 against 129.1 (+71.5%), 56 of 56 days on record in both years.
- A real summarizer call on Boiler 1's figures returned three grounded lines and nothing dropped,
  in about 10 s.

**Still to be seen in the app, through a signed-in session:**
- The summary appearing in the dock.
- AHU-3's out-of-band finding and its conclusion.
- Whether Bishopsgate's building row links to its site. If the migration left `buildings.site_id`
  empty, the weather line will honestly read "no location on record".
