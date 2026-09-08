"""Energy Intelligence API — Feature C."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...engines.energy import anomalies as anom_svc
from ...engines.energy import buildings as bld_svc
from ...engines.energy import building_backfill as bld_backfill
from ...engines.energy import sites_uuid_migration as sites_uuid
from ...engines.energy import building_resolver as bld_resolver
from ...engines.energy import cost_drivers as cost
from ...engines.energy import building_create as bld_create
from ...engines.energy import condition as cond_svc
from ...engines.energy import eui as eui_svc
from ...engines.energy import meters as meter_svc
from ...engines.energy import occupancy as occ_svc
from ...engines.energy import reports as report_svc
from ...models.energy import EnergyMonthlyReport
from ...shared import approvals as approvals_svc
from ..schemas.energy import (
    AnomalyActionRequest,
    AnomalyScanRequest,
    BuildingProfileRequest,
    ConditionDeduceRequest,
    ConditionFromVectorsRequest,
    CrossRefRequest,
    EuiComputeRequest,
    MeterPullRequest,
    MeterUpsertRequest,
    MonthlyReportRequest,
    OccupancyLogRequest,
    QueueDecisionRequest,
    ReadingsIngestRequest,
)

router = APIRouter(prefix="/api/energy", tags=["energy-intelligence"])


@router.post("/meters")
async def upsert_meter(body: MeterUpsertRequest, session: AsyncSession = Depends(get_session)):
    return await meter_svc.upsert_meter(session, body.model_dump(exclude_none=True))


@router.get("/meters")
async def list_meters(
    organization_id: UUID | None = None,
    site_id: UUID | None = None,
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
):
    rows = await meter_svc.list_meters(
        session, organization_id=organization_id, site_id=site_id, limit=limit
    )
    return {"ok": True, "count": len(rows), "meters": rows}


@router.post("/meters/pull")
async def pull_meter(body: MeterPullRequest, session: AsyncSession = Depends(get_session)):
    """Pull half-hourly readings from DCC (MPAN) / gas (MPRN) API for a window."""
    return await meter_svc.pull_and_ingest_meter(
        session,
        meter_id=body.meter_id,
        window_start=body.window_start,
        window_end=body.window_end,
        organization_id=body.organization_id,
    )


@router.post("/readings/ingest")
async def ingest_readings(body: ReadingsIngestRequest, session: AsyncSession = Depends(get_session)):
    return await meter_svc.ingest_readings(
        session,
        meter_id=body.meter_id,
        readings=body.readings,
        organization_id=body.organization_id,
        source=body.source,
        detect_gaps=body.detect_gaps,
    )


@router.post("/readings/ingest/csv")
async def ingest_readings_csv(
    file: UploadFile = File(...),
    meter_id: UUID | None = Form(None),
    organization_id: UUID | None = Form(None),
    source: str = Form("csv"),
    detect_gaps: bool = Form(True),
    session: AsyncSession = Depends(get_session),
):
    """Half-hourly smart-meter CSV → MeterReading (+ auto-create meter from MPAN/MPRN)."""
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return await meter_svc.ingest_readings_csv(
        session,
        csv_text=text,
        meter_id=meter_id,
        organization_id=organization_id,
        source=source,
        detect_gaps=detect_gaps,
    )


@router.get("/meters/{meter_id}/readings")
async def list_meter_readings(
    meter_id: UUID,
    limit: int = Query(2000, le=5000),
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    session: AsyncSession = Depends(get_session),
):
    rows = await meter_svc.list_readings(
        session,
        meter_id=meter_id,
        limit=limit,
        window_start=window_start,
        window_end=window_end,
    )
    return {"ok": True, "count": len(rows), "readings": rows}


@router.get("/gaps")
async def list_meter_gaps(
    meter_id: UUID | None = None,
    status: str | None = Query("open"),
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
):
    rows = await meter_svc.list_gaps(
        session, meter_id=meter_id, status=status, limit=limit
    )
    return {"ok": True, "count": len(rows), "gaps": rows}


@router.post("/gaps/process-retries")
async def process_gap_retries(session: AsyncSession = Depends(get_session)):
    return await meter_svc.process_gap_retries(session)


@router.post("/buildings/profile")
async def building_profile(body: BuildingProfileRequest, session: AsyncSession = Depends(get_session)):
    return await eui_svc.upsert_building_profile(
        session,
        site_id=body.site_id,
        gia_m2=body.gia_m2,
        building_type=body.building_type,
        organization_id=body.organization_id,
    )


@router.get("/buildings")
async def list_buildings(
    organization_id: UUID | None = None,
    limit: int = Query(500, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
):
    """Building table for the dashboard — every site with its energy profile, latest EUI,
    the benchmark it is read against (and where that benchmark came from) and a record
    completeness figure. See engines/energy/buildings.py."""
    return await bld_svc.list_buildings(session, organization_id=organization_id, limit=limit)


@router.post("/buildings/backfill-from-sites")
async def backfill_buildings_from_sites(
    dry_run: bool = Query(True, description="true (default) reports what would be created and writes nothing."),
    country_code: str | None = Query(None, description="Country to stamp on every created building, e.g. UK. Omitted = left empty; a country is never guessed from a region."),
    limit: int = Query(5000, ge=1, le=20000),
    session: AsyncSession = Depends(get_session),
):
    """Create one building per site in plenum_cafm.buildings.

    A site that already has a building is skipped, so this is safe to re-run and it never
    stands in the way of a site later holding several buildings — add those rows and they
    appear alongside. Defaults to a dry run: the report names every building it would
    create, every conflict, and the fields that would be left empty.
    """
    return await bld_backfill.backfill_buildings_from_sites(
        session, dry_run=dry_run, country_code=country_code, limit=limit
    )


class UseMixItem(BaseModel):
    use: str
    pct: float


class CreateBuildingRequest(BaseModel):
    """What the Hoist-a-building form submits.

    `name` and `site_name` are both accepted, as are `region` and `state` — the form and the
    canonical table use different words for the same thing and neither has to change.
    """
    name: str | None = None
    site_name: str | None = None
    country_code: str | None = Field(None, description="UK | US | AE | SG. GB and UAE are accepted.")
    country: str | None = None
    region: str | None = None
    state: str | None = None
    use_type: str | None = Field(None, description="Mall is accepted and stored as Retail.")
    use_mix: list[UseMixItem] | None = Field(None, description="Percentages summing to 100.")
    # Deliberately unconstrained here. Every rejection comes back in ONE shape — 400 with
    # field-keyed errors from validate_payload — instead of some as Pydantic's 422 and the
    # rest as ours, which would make the form handle two error formats for one submission.
    floors: int | None = None
    metering_granularity: str | None = Field(None, description="none | building-level | sub-metered")
    source: str | None = Field(None, description='Who is creating this, e.g. "hoistra-ui".')

    site_id: str | None = Field(None, description="An EXISTING site. Never allocated here.")
    building_code: str | None = Field(None, description="Allocated as the next free B-NNN when absent.")
    city: str | None = None
    postcode: str | None = None
    gfa_sqm: float | None = Field(None, description="Square METRES. Converted to the sqft column on write.")
    metering_route: str | None = None
    organization_id: str | None = None
    created_by: str | None = None


@router.post("/buildings", status_code=201)
async def create_building(
    body: CreateBuildingRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """Create one building, and return it in the same shape GET /buildings uses.

    A country and region are properties of a location, not of a building, and the location
    is what carries the regulation pack — so one is resolved or created here. Without it the
    building has no benchmark at all.

    Benchmark standard, standing and note are NOT stored: they are derived per request from
    that pack, and a stored copy would go stale the day a standard changes.

    201 on success. 400 with field-keyed errors. 409 when a supplied building_code is taken
    — an existing building is never overwritten.
    """
    out = await bld_create.create_building(session, body.model_dump(exclude_none=True))
    response.status_code = int(out.get("status") or (201 if out.get("ok") else 400))
    return out


@router.delete("/buildings/{building_id}")
async def delete_building(
    building_id: str,
    response: Response,
    confirm: bool = Query(False, description="Required to actually delete. Without it this reports what would be touched and changes nothing."),
    detach: bool = Query(True, description="true unlinks attached records and keeps them; false refuses to delete a building that holds any."),
    actor: str = Query("hoistra-ui"),
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Remove a building. Reports what it would touch unless confirm=true.

    Attached records are **detached, never deleted**. A certificate, an invoice or a work
    order is the record of something that actually happened, and the building row going away
    does not make it untrue — cascading would destroy evidence to tidy up a directory entry.
    They are left unlinked, which the resolver reports as unplaced and a person can re-link.
    """
    out = await bld_create.delete_building(
        session, building_id, confirm=confirm, detach=detach,
        actor=actor, organization_id=organization_id,
    )
    response.status_code = int(out.get("status") or (200 if out.get("ok") else 400))
    return out


class ResolveBuildingItem(BaseModel):
    """What extraction managed to read off one document or row."""
    name: str | None = None
    code: str | None = None
    site_name: str | None = None
    site_id: str | None = None
    #: The asset this record is against. Tried first and separately from the matching
    #: tiers: an asset already placed carries the answer, so this is a link the graph
    #: holds rather than another string to match.
    asset_code: str | None = None


class ResolveBuildingsRequest(BaseModel):
    items: list[ResolveBuildingItem] = Field(..., min_length=1, max_length=1000)


@router.get("/buildings/resolve")
async def resolve_building(
    name: str | None = Query(None, description="Building name as the document states it."),
    code: str | None = Query(None, description="Building code / reference."),
    site_name: str | None = Query(None),
    site_id: str | None = Query(None),
    asset_code: str | None = Query(None, description="Asset this record is against; its building wins."),
    session: AsyncSession = Depends(get_session),
):
    """Which building does this document belong to? Reads only, writes nothing.

    Returns outcome resolved | review | unmatched with the reason. An ambiguous match
    returns no building and the candidates it saw — filing against the wrong building
    misstates two buildings' obligations at once, so it declines rather than guesses.
    """
    return await bld_resolver.resolve_one(
        session, name=name, code=code, site_name=site_name, site_id=site_id,
        asset_code=asset_code,
    )


@router.post("/buildings/resolve-batch")
async def resolve_buildings_batch(
    body: ResolveBuildingsRequest,
    session: AsyncSession = Depends(get_session),
):
    """Resolve a whole ingest run against one index read. Reads only."""
    return await bld_resolver.resolve_batch(
        session, [i.model_dump() for i in body.items]
    )


@router.post("/sites/uuid-migration")
async def sites_uuid_migration(
    phase: str = Query("plan", pattern="^(plan|expand|contract)$",
                       description="plan (default, reads only) | expand (additive) | contract (swaps the primary key)"),
    confirm: bool = Query(False, description="Required for phase=contract — it rewrites a primary key."),
    session: AsyncSession = Depends(get_session),
):
    """Repoint plenum_cafm.sites onto a uuid key so the nine tables already declaring
    `site_id uuid` can finally join to it.

    plan     reads only — what would change, how many rows, and every orphan it will not invent a site for
    expand   additive and reversible — site_id_map, sites.site_uuid, shadow columns; both keys work
    contract the cutover — needs expand and confirm=true; legacy_site_id is kept on every table
    """
    if phase == "plan":
        return await sites_uuid.plan_migration(session)
    if phase == "expand":
        return await sites_uuid.apply_expand(session)
    return await sites_uuid.apply_contract(session, confirm=confirm)


@router.get("/buildings/{building_id}/cost-drivers")
async def building_cost_drivers(
    building_id: str,
    limit: int = Query(25, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """Which plant is driving spend on this building, ranked by how far over contract.

    Ranked on the gap between billed and contracted rather than on billed alone — the
    biggest spender is usually the biggest asset, which tells you nothing. Spend whose work
    order names no asset is reported separately as `unattributed`: it is real money and it
    is in the building total, but it cannot be blamed on a piece of plant, and spreading it
    across the ranking would invent an attribution nobody recorded.
    """
    return await cost.building_cost_drivers(session, building_id, limit=limit)


@router.get("/assets/{asset_id}/work-history")
async def asset_work_history(
    asset_id: str,
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    """Every work order recorded against this asset, with what each one was billed."""
    return await cost.asset_work_history(session, asset_id, limit=limit)


@router.get("/buildings/{site_id}")
async def get_building(
    site_id: str,
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
):
    """One building by sites.site_id (VARCHAR(50)) — same columns as the table row."""
    out = await bld_svc.get_building(session, site_id, organization_id=organization_id)
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail=out)
    return out


@router.get("/tm46")
async def list_tm46():
    return {"ok": True, **eui_svc.load_tm46()}


@router.post("/eui/compute")
async def compute_eui(body: EuiComputeRequest, session: AsyncSession = Depends(get_session)):
    return await eui_svc.compute_site_eui(
        session,
        site_id=body.site_id,
        period_start=body.period_start,
        period_end=body.period_end,
        meter_type=body.meter_type,
        organization_id=body.organization_id,
    )


@router.post("/condition/deduce")
async def deduce_condition(body: ConditionDeduceRequest, session: AsyncSession = Depends(get_session)):
    if body.from_vectors or not (body.report_text or "").strip():
        return await cond_svc.deduce_condition_from_vector_layer(
            session,
            asset_id=body.asset_id,
            asset_code=body.asset_code,
            organization_id=body.organization_id,
            write_to_asset=body.write_to_asset,
            auto_cross_ref=body.auto_cross_ref,
        )
    return await cond_svc.deduce_and_write_condition(
        session,
        asset_id=body.asset_id,
        report_text=body.report_text or "",
        source_report_ref=body.source_report_ref,
        asset_code=body.asset_code,
        organization_id=body.organization_id,
        write_to_asset=body.write_to_asset,
        auto_cross_ref=body.auto_cross_ref,
    )


@router.post("/condition/deduce-from-vectors")
async def deduce_from_vectors(
    body: ConditionFromVectorsRequest, session: AsyncSession = Depends(get_session)
):
    return await cond_svc.deduce_condition_from_vector_layer(
        session,
        asset_id=body.asset_id,
        asset_code=body.asset_code,
        organization_id=body.organization_id,
        write_to_asset=body.write_to_asset,
        auto_cross_ref=body.auto_cross_ref,
    )


@router.post("/recommendations/cross-ref")
async def cross_ref(body: CrossRefRequest, session: AsyncSession = Depends(get_session)):
    return await cond_svc.cross_reference_condition_consumption(
        session,
        asset_id=body.asset_id,
        organization_id=body.organization_id,
        days=body.days,
    )


@router.post("/occupancy/log")
async def log_occupancy(body: OccupancyLogRequest, session: AsyncSession = Depends(get_session)):
    return await occ_svc.log_occupancy_change(
        session,
        site_id=body.site_id,
        occupancy_state=body.occupancy_state,
        changed_at=body.changed_at,
        notes=body.notes,
        organization_id=body.organization_id,
        source=body.source,
    )


@router.post("/simulator/tick")
async def simulator_tick(session: AsyncSession = Depends(get_session)):
    """Run one half-hourly simulation step now, instead of waiting for the cron."""
    from ...engines.energy.simulator import simulate_half_hour

    return await simulate_half_hour(session)


@router.post("/simulator/enable")
async def simulator_enable(
    body: dict,
    session: AsyncSession = Depends(get_session),
):
    """Flag a meter for the demo feed: {"meter_id": ..., "enabled": true, "base_kwh": 40}.

    Opt-in per meter and stored on the meter itself, so which data is simulated is a
    property of the meter rather than a setting somewhere else that a reader has to know
    about to interpret what they are looking at.
    """
    from sqlalchemy import select as _select

    from ...models.energy import EnergyMeter

    mid = body.get("meter_id")
    if not mid:
        return {"ok": False, "error": "meter_id required"}
    meter = (
        await session.execute(_select(EnergyMeter).where(EnergyMeter.id == UUID(str(mid))))
    ).scalar_one_or_none()
    if meter is None:
        return {"ok": False, "error": "meter_not_found"}
    meta = dict(meter.raw_metadata or {})
    meta["simulate"] = bool(body.get("enabled", True))
    if body.get("base_kwh") is not None:
        meta["sim_base_kwh"] = float(body["base_kwh"])
    meter.raw_metadata = meta
    await session.commit()
    return {"ok": True, "meter_id": str(meter.id), "simulate": meta["simulate"],
            "sim_base_kwh": meta.get("sim_base_kwh")}


@router.post("/anomalies/scan")
async def scan_anomalies(body: AnomalyScanRequest, session: AsyncSession = Depends(get_session)):
    return await anom_svc.scan_meter_anomalies(
        session, meter_id=body.meter_id, organization_id=body.organization_id
    )


@router.post("/anomalies/scan-all")
async def scan_all_anomalies(
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
):
    return await anom_svc.scan_all_active_meters(session, organization_id=organization_id)


@router.get("/anomalies")
async def list_anomalies(
    status: str | None = "open",
    organization_id: UUID | None = None,
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
):
    rows = await anom_svc.list_anomalies(
        session, status=status, organization_id=organization_id, limit=limit
    )
    return {"ok": True, "count": len(rows), "anomalies": rows}


@router.post("/anomalies/{anomaly_id}/act")
async def act_anomaly(
    anomaly_id: UUID,
    body: AnomalyActionRequest,
    session: AsyncSession = Depends(get_session),
):
    return await anom_svc.act_on_anomaly(
        session, anomaly_id, action=body.action, reason=body.reason
    )


@router.post("/reports/monthly")
async def monthly_report(body: MonthlyReportRequest, session: AsyncSession = Depends(get_session)):
    return await report_svc.generate_monthly_energy_report(
        session,
        site_id=body.site_id,
        report_month=body.report_month,
        organization_id=body.organization_id,
        export_pdf=body.export_pdf,
    )


@router.get("/reports/{report_id}/pdf")
async def download_report_pdf(report_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(EnergyMonthlyReport, report_id)
    if not row or not row.pdf_blob_url:
        raise HTTPException(status_code=404, detail="pdf_not_found")
    url = row.pdf_blob_url
    if url.startswith("http://") or url.startswith("https://"):
        return RedirectResponse(url)
    path = Path(url)
    if not path.exists():
        raise HTTPException(status_code=404, detail="pdf_file_missing")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/saved-space/summary")
async def saved_space(organization_id: UUID | None = None, session: AsyncSession = Depends(get_session)):
    return await report_svc.saved_space_summary(session, organization_id=organization_id)


@router.get("/approvals")
async def list_approvals(
    status: str = "pending",
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
):
    items = await approvals_svc.list_queue(
        session, source_feature="C", status=status, organization_id=organization_id
    )
    return {
        "ok": True,
        "count": len(items),
        "items": [approvals_svc.queue_item_to_dict(i) for i in items],
    }


@router.post("/approvals/{item_id}/decide")
async def decide_approval(
    item_id: UUID,
    body: QueueDecisionRequest,
    session: AsyncSession = Depends(get_session),
):
    return await approvals_svc.decide_queue_item(
        session,
        item_id,
        decision=body.decision,
        pm_notes=body.pm_notes,
        prepare_email_handoff=body.prepare_email_handoff,
    )
