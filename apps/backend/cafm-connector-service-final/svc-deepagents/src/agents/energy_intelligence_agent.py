"""
Energy Intelligence agent tools — wraps svc-operations-intelligence (port 8009).

PRD tool agent: energy-intelligence-engine → Orchestrator agent id `energy_intelligence` (Feature C).
Intent keywords (SSOT: phase2_intents.ENERGY_INTELLIGENCE_INTENT_KEYWORDS):
  energy, meter, consumption, kWh, spike, anomaly, EUI, NABERS, carbon, EPC,
  smart meter, utility, electricity, benchmark.

Phase 2 Feature C:
  - meters / HH readings / gap retries
  - EUI vs CIBSE TM46
  - asset condition 1–5 + cross-ref recommendations (no WO)
  - anomaly scan + Acknowledge / Monitor / Mark expected
  - monthly energy report → Energy Saved Space
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog
from langchain_core.tools import tool

from ..config import settings
from ..http_client import request as _request

log = structlog.get_logger(__name__)

_TIMEOUT = 60.0
_SERVICE = "operations_intelligence"


def _base() -> str:
    return settings.operations_intelligence_base_url.rstrip("/")


def _err(exc: Exception, op: str) -> dict:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text or ""
        log.error(
            "energy_intelligence.http_error",
            operation=op,
            status_code=exc.response.status_code,
            body=body[:2000],
        )
        return {"error": body[:2000], "status_code": exc.response.status_code}
    log.error("energy_intelligence.error", operation=op, error=str(exc), exc_info=True)
    return {"error": str(exc)[:2000]}


@tool
async def upsert_energy_meter(
    meter_type: str = "electricity",
    mpan: str | None = None,
    mprn: str | None = None,
    site_id: str | None = None,
    asset_id: str | None = None,
    organization_id: str | None = None,
    tariff_gbp_per_kwh: float = 0.28,
    is_sub_meter: bool = False,
    asset_type_benchmark_kwh: float | None = None,
    dcc_device_id: str | None = None,
) -> dict:
    """C — Register/update a UK smart meter (MPAN electricity / MPRN gas)."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/energy/meters",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "meter_type": meter_type,
                "mpan": mpan,
                "mprn": mprn,
                "site_id": site_id,
                "asset_id": asset_id,
                "organization_id": organization_id,
                "tariff_gbp_per_kwh": tariff_gbp_per_kwh,
                "is_sub_meter": is_sub_meter,
                "asset_type_benchmark_kwh": asset_type_benchmark_kwh,
                "dcc_device_id": dcc_device_id,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "upsert_energy_meter")


@tool
async def ingest_meter_readings(
    meter_id: str,
    readings: list[dict],
    organization_id: str | None = None,
    source: str = "dcc",
    detect_gaps: bool = True,
) -> dict:
    """C — Ingest half-hourly readings into MeterReading; flags 2+ consecutive gaps."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/energy/readings/ingest",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "meter_id": meter_id,
                "readings": readings,
                "organization_id": organization_id,
                "source": source,
                "detect_gaps": detect_gaps,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "ingest_meter_readings")


@tool
async def process_meter_gap_retries() -> dict:
    """C — Retry missing HH periods (3× at 10 min) then escalate to Approvals queue."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/energy/gaps/process-retries",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "process_meter_gap_retries")


@tool
async def upsert_building_energy_profile(
    site_id: str,
    gia_m2: float,
    building_type: str = "office",
    organization_id: str | None = None,
) -> dict:
    """C — Set GIA + building type; locks TM46 benchmark for the site."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/energy/buildings/profile",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "site_id": site_id,
                "gia_m2": gia_m2,
                "building_type": building_type,
                "organization_id": organization_id,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "upsert_building_energy_profile")


@tool
async def compute_site_eui(
    site_id: str,
    period_start: str,
    period_end: str,
    meter_type: str = "electricity",
    organization_id: str | None = None,
) -> dict:
    """C — Compute EUI (kWh÷GIA÷period) vs CIBSE TM46; returns deviation % and £ at tariff."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/energy/eui/compute",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "site_id": site_id,
                "period_start": period_start,
                "period_end": period_end,
                "meter_type": meter_type,
                "organization_id": organization_id,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "compute_site_eui")


@tool
async def list_tm46_benchmarks() -> dict:
    """C — List CIBSE TM46 UK building-type electricity/gas benchmarks (kWh/m²/yr)."""
    try:
        resp = await _request(
            "GET",
            _base(),
            "/api/energy/tm46",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "list_tm46_benchmarks")


@tool
async def pull_smart_meter_readings(
    meter_id: str,
    window_start: str,
    window_end: str,
    organization_id: str | None = None,
) -> dict:
    """C — Pull HH readings from DCC (MPAN electricity) / MPRN gas API and ingest."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/energy/meters/pull",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "meter_id": meter_id,
                "window_start": window_start,
                "window_end": window_end,
                "organization_id": organization_id,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "pull_smart_meter_readings")


@tool
async def deduce_asset_condition(
    asset_id: str,
    report_text: str | None = None,
    source_report_ref: str | None = None,
    asset_code: str | None = None,
    organization_id: str | None = None,
    from_vectors: bool = False,
) -> dict:
    """C — Deduce condition 1–5; pass report_text or set from_vectors=true to read Doc RAG/UDR."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/energy/condition/deduce",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "asset_id": asset_id,
                "report_text": report_text,
                "source_report_ref": source_report_ref,
                "asset_code": asset_code,
                "organization_id": organization_id,
                "write_to_asset": True,
                "auto_cross_ref": True,
                "from_vectors": from_vectors or not (report_text or "").strip(),
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "deduce_asset_condition")


@tool
async def deduce_condition_from_inspection_vectors(
    asset_id: str,
    asset_code: str | None = None,
    organization_id: str | None = None,
) -> dict:
    """C — Read inspection reports from Doc RAG / UDR vector layer and write condition 1–5."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/energy/condition/deduce-from-vectors",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "asset_id": asset_id,
                "asset_code": asset_code,
                "organization_id": organization_id,
                "write_to_asset": True,
                "auto_cross_ref": True,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "deduce_condition_from_inspection_vectors")


@tool
async def log_site_occupancy_change(
    site_id: str,
    occupancy_state: str,
    changed_at: str | None = None,
    notes: str | None = None,
    organization_id: str | None = None,
) -> dict:
    """C — Log occupancy change; suppresses baseline-drift anomalies when present in window."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/energy/occupancy/log",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "site_id": site_id,
                "occupancy_state": occupancy_state,
                "changed_at": changed_at,
                "notes": notes,
                "organization_id": organization_id,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "log_site_occupancy_change")


@tool
async def cross_ref_condition_consumption(
    asset_id: str,
    organization_id: str | None = None,
    days: int = 30,
) -> dict:
    """C — If condition ≤2 AND consumption >15% above type benchmark → remediation queue (no WO)."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/energy/recommendations/cross-ref",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "asset_id": asset_id,
                "organization_id": organization_id,
                "days": days,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "cross_ref_condition_consumption")


@tool
async def scan_energy_anomalies(
    meter_id: str,
    organization_id: str | None = None,
) -> dict:
    """C — Run all 13 detection rules on a meter: weekend spike, baseline drift, asset spike,
    non-occupancy spike, schedule mismatch, baseload creep, peak excursion, data quality,
    time-of-use, and — when the meter has the inputs — weather residual (degree days),
    simultaneous heating/cooling (BMS trends), post-works regression (closed work orders).
    Returns rules_run and which rules were skipped and why. Queue only — no WO."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/energy/anomalies/scan",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"meter_id": meter_id, "organization_id": organization_id},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "scan_energy_anomalies")


@tool
async def list_energy_anomalies(
    status: str | None = "open",
    organization_id: str | None = None,
    building_id: str | None = None,
    asset_id: str | None = None,
    meter_id: str | None = None,
    anomaly_type: str | None = None,
    min_cost: float | None = None,
    min_days_active: int | None = None,
    order_by: str = "detected_at",
    limit: int = 100,
) -> dict:
    """C — Anomalies, narrowed by any dimension the activity log shows.

    Every field a person reads on an entry is something they can ask by, and each maps to one
    argument here:

        Building          -> building_id       a NAME or an id; "Building 5" resolves
        Asset / circuit   -> asset_id
        Anomaly type      -> anomaly_type      "Baseline drift" or baseline_drift both work
        Annualised cost   -> min_cost          at or above
        Days active       -> min_days_active   persisted at least this long
        Status            -> status            open | acknowledged | monitoring | expected
                                               pass None for every status

    So "baseline drift on Town Hall over £5k, active more than ten days" is one call:
    `anomaly_type="baseline drift", building_id=…, min_cost=5000, min_days_active=10`.

    `order_by="financial"` sorts dearest first, which is usually what the question means. The
    default is newest first.

    Every row carries the labels a person reads — `asset_code` ("ACS-DL-07"), `meter_ref` (the
    MPAN/MPRN) and `building_name` — alongside the ids. Use them. Quoting a uuid at somebody and
    saying the name is unavailable is not an answer they can act on, and the dashboard beside
    them is showing the same row as "SYN-SUB-FDCC31 on Building 5".

    `asset_code` None means the metering is building-level and the circuit is genuinely unknown
    — not that the name could not be looked up. Say "whole building", not "unnamed asset".

    A building name that matches nothing raises rather than returning an empty list, because
    empty reads as "this building is clean". That is not hypothetical: "no energy anomalies were
    found for Building 5" was said about a building with nine open findings, because the name
    was passed where an id was compared.

    Filter HERE, not after. Filtering the result in the answer discards rows that were never
    fetched once there are more than `limit`, and reports a subset as though it were the whole
    answer. This is a list of WHICH anomalies exist; for HOW MUCH they cost together, use
    `get_anomaly_rollup` — summing this list double counts overlapping detectors.
    """
    try:
        params: dict[str, Any] = {"limit": limit, "order_by": order_by}
        if status:
            params["status"] = status
        if organization_id:
            params["organization_id"] = organization_id
        if building_id:
            params["building_id"] = building_id
        if asset_id:
            params["asset_id"] = asset_id
        if meter_id:
            params["meter_id"] = meter_id
        if anomaly_type:
            params["anomaly_type"] = anomaly_type
        if min_cost is not None:
            params["min_cost"] = min_cost
        if min_days_active is not None:
            params["min_days_active"] = min_days_active
        resp = await _request(
            "GET",
            _base(),
            "/api/energy/anomalies",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "list_energy_anomalies")


@tool
async def act_on_energy_anomaly(
    anomaly_id: str,
    action: str,
    reason: str | None = None,
) -> dict:
    """C — Acknowledge / Monitor / Mark expected (reason required for mark_expected). No WO."""
    try:
        resp = await _request(
            "POST",
            _base(),
            f"/api/energy/anomalies/{anomaly_id}/act",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"action": action, "reason": reason},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "act_on_energy_anomaly")


@tool
async def generate_monthly_energy_report(
    site_id: str,
    report_month: str | None = None,
    organization_id: str | None = None,
    export_pdf: bool = True,
) -> dict:
    """C — Monthly Energy Saved Space report (EUI vs TM46, anomalies by £, carbon, PDF)."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/energy/reports/monthly",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "site_id": site_id,
                "report_month": report_month,
                "organization_id": organization_id,
                "export_pdf": export_pdf,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "generate_monthly_energy_report")


@tool
async def get_energy_saved_space_summary(
    organization_id: str | None = None,
) -> dict:
    """C — Energy Saved Space KPIs: reports, open anomalies, annualised excess cost."""
    try:
        params: dict[str, Any] = {}
        if organization_id:
            params["organization_id"] = organization_id
        resp = await _request(
            "GET",
            _base(),
            "/api/energy/saved-space/summary",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params or None,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "get_energy_saved_space_summary")


@tool
async def list_energy_approvals(
    status: str = "pending",
    organization_id: str | None = None,
) -> dict:
    """C — Feature C Approvals queue (gaps, anomalies, remediation recommendations)."""
    try:
        params: dict[str, Any] = {"status": status}
        if organization_id:
            params["organization_id"] = organization_id
        resp = await _request(
            "GET",
            _base(),
            "/api/energy/approvals",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "list_energy_approvals")


@tool
async def decide_energy_approval(
    item_id: str,
    decision: str,
    pm_notes: str | None = None,
    prepare_email_handoff: bool = False,
) -> dict:
    """C — Decide a Feature C queue item (approve/reject/defer + optional mailto handoff)."""
    try:
        resp = await _request(
            "POST",
            _base(),
            f"/api/energy/approvals/{item_id}/decide",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "decision": decision,
                "pm_notes": pm_notes,
                "prepare_email_handoff": prepare_email_handoff,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "decide_energy_approval")




async def _resolve_building(
    building_name: str | None, building_id: str | None
) -> tuple[str | None, dict | None]:
    """A building id from whatever the user said, or an explanation of why not.

    Returns (building_id, problem). Exactly one is ever set. The resolver declines an
    ambiguous name rather than picking one, and that refusal is passed through as the
    answer — reporting one building's documents under another building's name is worse
    than saying the name was not specific enough.
    """
    if building_id:
        return str(building_id), None
    if not (building_name or "").strip():
        return None, {"error": "building_name or building_id is required"}
    try:
        resp = await _request(
            "GET",
            _base(),
            "/api/energy/buildings/resolve",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params={"name": building_name},
        )
        body = resp.json()
    except Exception as exc:
        return None, _err(exc, "resolve_building")
    if body.get("outcome") == "resolved" and body.get("building_id"):
        return str(body["building_id"]), None
    return None, {
        "error": "building_not_resolved",
        "outcome": body.get("outcome"),
        "reason": body.get("reason"),
        "asked_for": building_name,
        "candidates": body.get("candidates") or [],
        "guidance": (
            "No building matched that name well enough to answer about. Do NOT substitute "
            "another building; ask which one is meant, using the candidates if any."
        ),
    }


@tool
async def list_building_meter_readings(
    building_name: str | None = None,
    building_id: str | None = None,
) -> dict:
    """C — Every meter on one building, with its readings counted and totalled.

    **Use this for any question about a building's meters, MPAN/MPRN, half-hourly reading
    counts, consumption totals or reading coverage** — "how many readings does Riverside
    Court have", "which MPAN", "how much kWh", "is there a gap in the data".

    Pass `building_name` as the user said it. Returns per meter: `meter_ref` (the MPAN or
    MPRN), `meter_type`, `readings`, `total_kwh`, `first_reading_at`, `last_reading_at` and
    `estimated_readings` (readings filled in by gap retry rather than metered), plus
    `readings_total` and `kwh_total` across the building and any `gaps` on record.

    `registered_without_readings` lists meters on the building's register that have no
    readings at all. Report those as metered-but-unread; they are NOT an absence of meters.
    """
    bid, problem = await _resolve_building(building_name, building_id)
    if problem:
        return problem
    try:
        resp = await _request(
            "GET",
            _base(),
            f"/api/energy/buildings/{bid}/meter-summary",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "list_building_meter_readings")


@tool
async def list_building_documents(
    building_name: str | None = None,
    building_id: str | None = None,
) -> dict:
    """Every document FILED AGAINST one building, and what was extracted from each.

    **Use this for "which documents are linked to <building>", "what has been ingested for
    it", "does it have a contract/certificate/invoice on file".** It reads the building
    graph — the structured link — and is the right tool even though a semantic document
    search also exists: that one finds documents whose TEXT mentions a building, which is a
    different and much weaker claim. A document can mention a building it is not filed
    against, and a document filed against one need never name it.

    Returns each document's `label` (the file name), `detail` (its doc_type) and `has_file`
    — false meaning the record exists but no stored original is available to open, which is
    a fact worth stating rather than a failure. Certificates extracted from those documents
    come back under `certificates`.
    """
    bid, problem = await _resolve_building(building_name, building_id)
    if problem:
        return problem
    try:
        resp = await _request(
            "GET",
            _base(),
            f"/api/energy/buildings/{bid}/graph",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        graph = resp.json()
    except Exception as exc:
        return _err(exc, "list_building_documents")

    def _find(branches, table):
        for b in branches or []:
            if b.get("table") == table:
                return b
            hit = _find(b.get("children"), table)
            if hit:
                return hit
        return None

    docs = _find(graph.get("branches"), "documents") or {}
    certs = _find(graph.get("branches"), "compliance_certificates") or {}
    return {
        "ok": bool(graph.get("ok")),
        "building_id": bid,
        "building": graph.get("name"),
        "count": docs.get("count") or 0,
        "documents": docs.get("rows") or [],
        "certificates": certs.get("rows") or [],
        "empty_reason": docs.get("empty_reason") if not (docs.get("rows") or []) else None,
    }


# list_building_documents is defined above and deliberately NOT listed here: it answers a
# question about any building rather than about energy, so it lives on the main tool list
# where every conversation reaches it. Engine lists bind only after content selects that
# engine, which is precisely why it was unreachable from here.
@tool
async def compute_building_rating(
    building_id: str,
    scheme: str,
    months: int = 12,
    year: int | None = None,
) -> dict:
    """C — Compute and snapshot a consumption rating for a building. scheme: "energy_star"
    (ESTIMATED ENERGY STAR score 1–100 vs the national median for its property type — say
    "estimate") or "ll97" (NYC emissions vs the occupancy-group cap, tCO2e, $268/t penalty).
    Needs gross area and ≥3 months of readings; 12 months = actual, fewer = projected."""
    try:
        resp = await _request("POST", _base(), "/api/energy/ratings/compute", service=_SERVICE,
                              timeout=_TIMEOUT,
                              json={"building_id": building_id, "scheme": scheme, "months": months, "year": year})
        return resp.json()
    except Exception as exc:
        return _err(exc, "compute_building_rating")


@tool
async def get_ratings_position(country_code: str, building_id: str | None = None) -> dict:
    """C — The ratings-and-duties tiles for one market, from real records. UK: MEES below E now /
    below B for 2030 / EPCs on file (from the EPC register). US: LL97 cap, Energy Star estimate,
    LL84 filing. SG: BCA submission, EUI vs BCA 192 kWh/m², Green Mark. AE: EUI vs rolling
    benchmark, chiller plant kW/RT. Each tile says its basis (certificate / filing / consumption
    + months)."""
    try:
        params = {"country_code": country_code}
        if building_id:
            params["building_id"] = building_id
        resp = await _request("GET", _base(), "/api/energy/ratings/position", service=_SERVICE,
                              timeout=_TIMEOUT, params=params)
        return resp.json()
    except Exception as exc:
        return _err(exc, "get_ratings_position")


@tool
async def record_chiller_design(
    asset_id: str,
    design_kw_per_rt: float,
    design_capacity_rt: float | None = None,
    design_ambient_c: float | None = None,
    building_id: str | None = None,
    source: str | None = None,
) -> dict:
    """C — Record a chiller's design kW/RT (e.g. 0.68), capacity in RT and design ambient °C.
    Every kW/RT reading is judged against this; without it the chiller cannot be assessed."""
    try:
        resp = await _request("POST", _base(), f"/api/energy/chillers/{asset_id}/design", service=_SERVICE,
                              timeout=_TIMEOUT,
                              json={"design_kw_per_rt": design_kw_per_rt, "design_capacity_rt": design_capacity_rt,
                                    "design_ambient_c": design_ambient_c, "building_id": building_id, "source": source})
        return resp.json()
    except Exception as exc:
        return _err(exc, "record_chiller_design")


@tool
async def ingest_chiller_readings(asset_id: str, readings: list[dict], building_id: str | None = None) -> dict:
    """C — Store chiller BMS / sub-meter samples: [{reading_at, kw_input, cooling_load_rt | cooling_load_kw,
    ambient_c?, chw_supply_c?, chw_return_c?}]. Requires the ingest right."""
    try:
        resp = await _request("POST", _base(), f"/api/energy/chillers/{asset_id}/readings", service=_SERVICE,
                              timeout=_TIMEOUT, json={"readings": readings, "building_id": building_id})
        return resp.json()
    except Exception as exc:
        return _err(exc, "ingest_chiller_readings")


@tool
async def scan_chiller_efficiency(asset_id: str | None = None, window_days: int = 14) -> dict:
    """C — kW/RT over the window against design, at matched ambient and ≥40% load; more than 15%
    above design is raised as a chiller_efficiency anomaly (queue, no WO). One asset, or every
    chiller with a design figure when asset_id is omitted."""
    try:
        resp = await _request("POST", _base(), "/api/energy/chillers/scan", service=_SERVICE, timeout=_TIMEOUT,
                              json={"asset_id": asset_id, "window_days": window_days})
        return resp.json()
    except Exception as exc:
        return _err(exc, "scan_chiller_efficiency")


@tool
async def ingest_degree_days(building_id: str, months: list[dict], base_temp_c: float = 15.5,
                             station: str | None = None) -> dict:
    """C — Monthly heating/cooling degree days for a building: [{month: "YYYY-MM-01", hdd, cdd}].
    The weather-normalised anomaly rule (CUSUM on degree-day regression residuals) needs ≥12
    months of these beside the meter's monthly kWh. Requires the ingest right."""
    try:
        resp = await _request("POST", _base(), "/api/energy/weather/degree-days", service=_SERVICE,
                              timeout=_TIMEOUT,
                              json={"building_id": building_id, "months": months, "base_temp_c": base_temp_c,
                                    "station": station})
        return resp.json()
    except Exception as exc:
        return _err(exc, "ingest_degree_days")


@tool
async def ingest_bms_trends(building_id: str, samples: list[dict]) -> dict:
    """C — BMS zone samples: [{recorded_at, zone, heating_pct, cooling_pct, zone_temp_c?, setpoint_c?}].
    The simultaneous-heating-and-cooling rule reads these (both calling >30 min in one zone).
    Requires the ingest right."""
    try:
        resp = await _request("POST", _base(), "/api/energy/bms/trends", service=_SERVICE, timeout=_TIMEOUT,
                              json={"building_id": building_id, "samples": samples})
        return resp.json()
    except Exception as exc:
        return _err(exc, "ingest_bms_trends")



# ── The portfolio views the Energy page renders, which the agent could not reach ──────────
#
# svc-operations-intelligence serves /market-profiles, /buildings, /buildings/{id}/cost-drivers,
# /anomalies/rollup, /statutory/{market} and /tariffs, and the Energy page renders all of them.
# None had a tool, so the orchestrator could not answer the questions printed as chips on that
# very page — "Which markets drive the excess cost?", "Which buildings are worst against their
# own pack?" — while a person was looking at the answer on screen.


@tool
async def get_market_profiles(markets: str | None = None,
                              building_id: str | None = None) -> dict:
    """C — The four markets side by side: what each benchmarks against, how its data arrives,
    and what it bills.

    Use for any question that compares COUNTRIES or asks why portfolio numbers are normalised
    rather than summed — "which markets drive the excess cost?", "what standard applies in
    Singapore?", "why are these buildings not comparable?".

    Each market returns its benchmark standard and reference EUI (CIBSE TM46 by use class in
    the UK, Energy Star / ASHRAE 100 with the LL97 emissions limit in the US, a rolling
    portfolio benchmark for the UAE where Estidama covers new build only, BCA in Singapore),
    its unit of measure, its data route and refresh, its identifier scheme, its known limits,
    and its commercial tariffs in the local currency.

    The units differ — kWh/m2/yr, kBtu/ft2/yr, tCO2e/ft2, cooling in RTh — so figures from
    different markets must NOT be added together. Say which basis each number is on, and when
    a portfolio figure is shown say that it is normalised rather than totalled.
    """
    try:
        params: dict[str, Any] = {}
        if markets:
            params["markets"] = markets      # comma-separated subset of UK,US,AE,SG
        if building_id:
            params["building_id"] = building_id
        resp = await _request("GET", _base(), "/api/energy/market-profiles", service=_SERVICE,
                              timeout=_TIMEOUT, params=params or None)
        return resp.json()
    except Exception as exc:
        return _err(exc, "get_market_profiles")


@tool
async def list_energy_buildings(limit: int = 500) -> dict:
    """C — Every building with its EUI, the benchmark it is read against and where that
    benchmark came from, plus record completeness. The table the Energy page lays out.

    Use for "which buildings are worst against their own pack?", "which are over benchmark?",
    "anything above 20k?".

    **This endpoint takes no filters.** It returns the caller's buildings and you filter the
    result yourself — over benchmark, with anomalies, above a cost threshold. Do not invent
    query parameters for it: unknown ones are ignored rather than refused, so a call that looks
    filtered comes back unfiltered and the answer is confidently wrong about how many buildings
    qualify.

    Each building is compared to the reference for ITS market and use class, not to the others.
    Kingsway House at 198 kWh/m2 against a reference of 172 is +15%; Bishopsgate Tower at 214
    against 215 is AT reference. Reporting those two raw EUIs side by side says the second is
    worse, which is the opposite of the truth — always carry the reference with the figure.
    """
    try:
        resp = await _request("GET", _base(), "/api/energy/buildings", service=_SERVICE,
                              timeout=_TIMEOUT, params={"limit": limit})
        return resp.json()
    except Exception as exc:
        return _err(exc, "list_energy_buildings")


@tool
async def get_building_cost_drivers(building_id: str, limit: int = 25) -> dict:
    """C — What is actually costing money in one building, ranked. The "Investigate building"
    view.

    Use when a question names ONE building — "why is Kingsway House so expensive?", "what is
    driving the cost at Town Hall?". Returns the drivers beneath the headline number, so the
    answer is a cause rather than a restatement of the total.
    """
    try:
        resp = await _request("GET", _base(), f"/api/energy/buildings/{building_id}/cost-drivers",
                              service=_SERVICE, timeout=_TIMEOUT, params={"limit": limit})
        return resp.json()
    except Exception as exc:
        return _err(exc, "get_building_cost_drivers")


@tool
async def get_anomaly_rollup(building_id: str | None = None,
                             status: str | None = "open") -> dict:
    """C — The honest total across overlapping anomaly detectors. Use this, never a sum of
    list_energy_anomalies.

    Several rules fire on the same kWh — a weekend spike sits inside a non-occupied spike, and
    a baseline drift can overlap both. Adding their costs counts the same energy two or three
    times, which is how a building 3% UNDER its reference came to show a six-figure anomaly
    cost.

    Returns the HEADLINE (the largest single finding, never a sum), what the total would become
    if the next rule were added, what is double counted at least, which rules are contained
    inside others, which may overlap, and which are priced at all. Report the headline as the
    number, and the rest as what it deliberately does not include.
    """
    try:
        params: dict[str, Any] = {}
        if building_id:
            params["building_id"] = building_id
        if status:
            params["status"] = status
        resp = await _request("GET", _base(), "/api/energy/anomalies/rollup", service=_SERVICE,
                              timeout=_TIMEOUT, params=params or None)
        return resp.json()
    except Exception as exc:
        return _err(exc, "get_anomaly_rollup")


@tool
async def get_statutory_duties(market: str) -> dict:
    """C — The statutory position for one market: what is enforceable now, what is proposed,
    and what is on file.

    UK: MEES below EPC E enforceable now, below B proposed for 2030, EPCs held and when they
    expire. US: LL97 emissions limits and LL84 filing. SG: BCA submission and Green Mark.
    AE: Estidama.

    Every tile states its basis — from a certificate, from a filing, or inferred from
    consumption. Say which. "2 buildings below EPC B" taken from certificates is a fact; the
    same number inferred from consumption is an estimate, and that difference decides whether
    somebody can act on it.
    """
    try:
        resp = await _request("GET", _base(), f"/api/energy/statutory/{market}", service=_SERVICE,
                              timeout=_TIMEOUT)
        return resp.json()
    except Exception as exc:
        return _err(exc, "get_statutory_duties")


@tool
async def get_energy_tariffs(market: str | None = None) -> dict:
    """C — The tariff each market bills each fuel at, and the unit conversions.

    Needed whenever kWh becomes money, because the rate and the currency both differ by market:
    28.4p/kWh contracted in the UK, $0.22/kWh in the US, 44.5 fils/kWh in the UAE including the
    fuel surcharge, S$0.30/kWh in Singapore. Gas is billed per therm in the US and by calorific
    value in the UK, and the UAE bills LPG by cylinder or kg.

    Never convert between currencies. Quote each market in its own, and name the tariff used
    for any cost figure.
    """
    try:
        params = {"market": market} if market else None
        resp = await _request("GET", _base(), "/api/energy/tariffs", service=_SERVICE,
                              timeout=_TIMEOUT, params=params)
        return resp.json()
    except Exception as exc:
        return _err(exc, "get_energy_tariffs")


@tool
async def summarise_anomalies(group_by: str = "building", status: str | None = None,
                              limit: int = 50) -> dict:
    """C — Counts and totals across the WHOLE anomaly table, grouped one way, in one call.

    This is the tool for "which …" questions, where the answer is a ranking rather than a row:

        "which building has the most anomalies?"      group_by="building"
        "which building costs us the most?"           group_by="building"
        "which anomaly type dominates?"               group_by="type"
        "what is still open / needs action?"          group_by="status"
        "which asset or circuit is worst?"            group_by="asset"

    Do NOT answer these by listing anomalies and counting them yourself: the list stops at its
    page size, so a count taken from it is a count of the page, reported as a count of the
    estate.

    Each group returns:
      count      how many findings
      worst      the largest SINGLE finding — this is the figure to quote
      naive_sum  every finding added together. Detectors overlap, so this DOUBLE COUNTS. It is
                 returned only so you can see the gap; never quote it as a total.
      unpriced   findings with no cost attached. They are real, and are not £0.

    With group_by="asset" the label is the asset CODE where one exists. Check
    `asset_in_register` on each group: false means the anomaly names an asset_id that is not in
    the asset register at all — 19 of the 26 assets carrying anomalies are dangling references.
    That uuid is a pointer to nothing, not an unnamed asset. Do NOT offer it as plant to
    investigate; say the finding is on the building and its asset link is broken.

    Groups are split BY CURRENCY as well as by the dimension asked for, because the estate
    spans four. The dearest buildings here are in AED and the next in GBP — one ranking across
    both is a ranking of exchange rates, not of waste. Rank within a currency, or say which
    currency each figure is in.
    """
    try:
        params: dict[str, Any] = {"group_by": group_by, "limit": limit}
        if status:
            params["status"] = status
        resp = await _request("GET", _base(), "/api/energy/anomalies/summary", service=_SERVICE,
                              timeout=_TIMEOUT, params=params)
        return resp.json()
    except Exception as exc:
        return _err(exc, "summarise_anomalies")


@tool
async def get_operating_hours(building_id: str, days: int = 90) -> dict:
    """C — The hours a building keeps, against the hours its benchmark assumes.

    Use this whenever a building is over its reference and the question is "how much of that can
    I act on". Part of the gap is waste and part is that the reference assumes a shorter week
    than the building works, and the two take different answers: a work order, or a re-benchmark.
    Without this you cannot separate them, and saying "the building keeps longer hours than its
    pack assumes" without it is an invention.

    Returns `assumed` (stored on the building profile, WITH its source), `derived` (computed
    from the load profile every call, never stored, so it cannot go stale) and
    `gap_hours_per_week`.

    Read `known` on each before using a figure:

      assumed.known is False   the benchmark's assumption was never recorded. Say the fit of
                               the benchmark cannot be judged and that the assumption needs
                               setting — do NOT supply a plausible default, because a fabricated
                               assumption behind a re-benchmark argument is worse than no
                               argument.
      derived.known is False   either under 14 days of readings, or the load is flat all day
                               (a continuous process load, or a meter reporting a constant).
                               `note` says which.

    The gap is WEEKLY on purpose. A building running 07:00-19:00 against an assumed 09:00-21:00
    keeps the same twelve hours, so the daily gap is zero — and if it also runs weekends it works
    twelve hours a week more than its pack assumes while a daily comparison reports no difference
    at all. Check `derived.weekend_operation` against `assumed.days_per_week`.

    CHECK `provenance.measured` FIRST. False means the readings behind the pattern are mostly
    simulated, and the hours describe the simulator rather than the building — in this
    deployment 98% of all readings carry source='simulator'. Say the pattern cannot support a
    re-benchmark argument and that real half-hourly data is needed. Do not quote the hours.

    `derived.contiguous` False means the in-use hours have a break in them — two shifts, or a
    cleaning window. Printing "07:00-19:00" over that describes a continuity the readings do not
    show.

    A wide gap is NOT a finding of waste. It is a benchmark-fit question, and the honest answer
    names it as one.
    """
    try:
        resp = await _request("GET", _base(),
                              f"/api/energy/buildings/{building_id}/operating-hours",
                              service=_SERVICE, timeout=_TIMEOUT, params={"days": days})
        return resp.json()
    except Exception as exc:
        return _err(exc, "get_operating_hours")


@tool
async def consumption_by_asset(category: str | None = None, days: int = 90,
                               limit: int = 25) -> dict:
    """C — Consumption ranked by ASSET. The tool for "which chiller uses the most power".

    `category="Chiller"` for chillers, or omit it to rank every sub-metered asset. Ranked on
    kWh over the window.

    Sub-metered assets ONLY, and this is the honest limit to state with any answer: 34 of 67
    meters carry an asset_id, so the rest of the estate's consumption belongs to a building and
    cannot be attributed to plant. An asset missing from this list is UNMETERED, not idle —
    saying "CH-02 uses nothing" about an asset with no sub-meter is a fabrication.

    Check `simulated_pct` per row before ranking. Every sub-metered asset in this deployment
    returns an identical 867,240 kWh, which is the simulator rather than a remarkable
    coincidence; a ranking of identical numbers ranks nothing.

    If the question is about chiller EFFICIENCY rather than consumption, use
    `scan_chiller_efficiency` — kW/RT, where lower is better.
    """
    try:
        params: dict[str, Any] = {"days": days, "limit": limit}
        if category:
            params["category"] = category
        resp = await _request("GET", _base(), "/api/energy/consumption/by-asset",
                              service=_SERVICE, timeout=_TIMEOUT, params=params)
        return resp.json()
    except Exception as exc:
        return _err(exc, "consumption_by_asset")

ENERGY_INTELLIGENCE_TOOLS = [
    # Portfolio and market views - what the Energy page renders.
    summarise_anomalies,
    consumption_by_asset,
    get_operating_hours,
    get_market_profiles,
    list_energy_buildings,
    get_building_cost_drivers,
    get_anomaly_rollup,
    get_statutory_duties,
    get_energy_tariffs,
    compute_building_rating,
    get_ratings_position,
    record_chiller_design,
    ingest_chiller_readings,
    scan_chiller_efficiency,
    ingest_degree_days,
    ingest_bms_trends,
    list_building_meter_readings,
    upsert_energy_meter,
    pull_smart_meter_readings,
    ingest_meter_readings,
    process_meter_gap_retries,
    upsert_building_energy_profile,
    compute_site_eui,
    list_tm46_benchmarks,
    deduce_asset_condition,
    deduce_condition_from_inspection_vectors,
    cross_ref_condition_consumption,
    log_site_occupancy_change,
    scan_energy_anomalies,
    list_energy_anomalies,
    act_on_energy_anomaly,
    generate_monthly_energy_report,
    get_energy_saved_space_summary,
    list_energy_approvals,
    decide_energy_approval,
]
