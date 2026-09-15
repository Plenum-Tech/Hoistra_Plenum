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
    status: str = "open",
    organization_id: str | None = None,
) -> dict:
    """C — List energy anomalies (status open|acknowledged|monitoring|expected)."""
    try:
        params: dict[str, Any] = {"status": status}
        if organization_id:
            params["organization_id"] = organization_id
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


ENERGY_INTELLIGENCE_TOOLS = [
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
