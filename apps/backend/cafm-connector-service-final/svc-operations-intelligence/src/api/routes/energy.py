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
from ...engines.energy import anomaly_rollup as rollup_svc
from ...engines.energy import buildings as bld_svc
from ...engines.energy import building_backfill as bld_backfill
from ...engines.energy import sites_uuid_migration as sites_uuid
from ...engines.energy import building_resolver as bld_resolver
from ...engines.energy import cost_drivers as cost
from ...engines.energy import building_create as bld_create
from ...engines.energy import building_update as bld_update
from ...engines.energy import building_tree as bld_tree
from ...engines.energy import document_delete as doc_delete
from ...engines.energy import condition as cond_svc
from ...engines.energy import eui as eui_svc
from ...engines.energy import meters as meter_svc
from ...engines.energy import occupancy as occ_svc
from ...engines.energy import operating_hours as hours_svc
from ...engines.energy import reports as report_svc
from ...models.energy import EnergyMonthlyReport
from ...shared import approvals as approvals_svc
from ...engines.auth import access
from ...engines.energy import chiller as chiller_svc
from ...engines.energy import market_profiles as profile_svc
from ...engines.energy import benchmarks as bench_svc
from ...engines.energy import detection_coverage as coverage_svc
from ...engines.energy import ask as ask_svc
from ...engines.energy import investigate as inv_svc
from ...engines.energy import market_profiles as mp_svc
from ...engines.energy import pricing as price_svc
from ...engines.energy import asset_intelligence as ai_svc
from ...engines.energy import condition_engine as cond_svc
from ...engines.energy import ratings_position as position_svc
from ...engines.energy import us_ratings as us_svc
from .auth import scope
from ..schemas.energy import (
    BmsTrendsRequest,
    ChillerDesignRequest,
    ChillerReadingsRequest,
    ChillerScanRequest,
    DegreeDaysRequest,
    RatingComputeRequest,
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

router = APIRouter(prefix="/api/energy", tags=["energy-intelligence"],
                   # Every route here needs a signed-in caller, and a company named in
                   # the query string must be the caller's own (or the caller a
                   # superadmin). Before this, every endpoint was open and tenancy
                   # was whatever organization_id the client chose to send.
                   dependencies=[Depends(scope)])


@router.post("/meters")
async def upsert_meter(
    body: MeterUpsertRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    payload = body.model_dump(exclude_none=True)
    payload["organization_id"] = access.organization_for(s, body.organization_id)
    return await meter_svc.upsert_meter(session, payload)


@router.get("/meters")
async def list_meters(
    organization_id: UUID | None = None,
    building_id: UUID | None = None,
    site_id: UUID | None = Query(None, deprecated=True,
                                 description="Former name of building_id. Still accepted."),
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    organization_id = access.organization_for(s, organization_id)
    rows = await meter_svc.list_meters(
        session, organization_id=organization_id, building_id=building_id or site_id, limit=limit
    )
    if s.restricted:
        rows = await bld_svc.restrict_by_site(session, rows, s)
    return {"ok": True, "count": len(rows), "meters": rows}


@router.post("/meters/pull")
async def pull_meter(
    body: MeterPullRequest, session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Pull half-hourly readings from DCC (MPAN) / gas (MPRN) API for a window."""
    org_id = access.organization_for(s, body.organization_id)
    return await meter_svc.pull_and_ingest_meter(
        session,
        meter_id=body.meter_id,
        window_start=body.window_start,
        window_end=body.window_end,
        organization_id=org_id,
    )


@router.post("/readings/ingest")
async def ingest_readings(
    body: ReadingsIngestRequest, session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    access.assert_can_ingest(s)
    org_id = access.organization_for(s, body.organization_id)
    return await meter_svc.ingest_readings(
        session,
        meter_id=body.meter_id,
        readings=body.readings,
        organization_id=org_id,
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
    s: access.Scope = Depends(scope),
):
    """Half-hourly smart-meter CSV → MeterReading (+ auto-create meter from MPAN/MPRN)."""
    access.assert_can_ingest(s)
    organization_id = access.organization_for(s, organization_id)
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
    s: access.Scope = Depends(scope),
):
    # The meter is named in the path, so there is no list to narrow — the only question is
    # whether this meter is the caller's. Unasked, any meter id returned its readings.
    await access.assert_owned(session, s, "energy_meters", meter_id, action="read")
    rows = await meter_svc.list_readings(
        session,
        meter_id=meter_id,
        limit=limit,
        window_start=window_start,
        window_end=window_end,
    )
    return {"ok": True, "count": len(rows), "readings": rows}


@router.get("/buildings/{building_id}/meter-summary")
async def building_meter_summary(
    building_id: UUID,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    access.assert_building(s, building_id, action="read")
    """Every meter on one building, readings counted and totalled. Read-only.

    Answers "how many half-hourly readings does this building have, on which MPAN, and how
    much" in one call. The alternative was three chained calls and several thousand reading
    rows to add up, so the sum is computed where the rows are.
    """
    return await meter_svc.building_meter_summary(session, building_id=building_id)


@router.get("/gaps")
async def list_meter_gaps(
    meter_id: UUID | None = None,
    status: str | None = Query("open"),
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    if meter_id is not None:
        await access.assert_owned(session, s, "energy_meters", meter_id, action="read")
    rows = await meter_svc.list_gaps(
        session, meter_id=meter_id, status=status, limit=limit,
        organization_id=s.organization_id if not s.is_superadmin else None,
        building_ids=s.building_ids,
    )
    return {"ok": True, "count": len(rows), "gaps": rows}


@router.post("/gaps/process-retries")
async def process_gap_retries(session: AsyncSession = Depends(get_session)):
    return await meter_svc.process_gap_retries(session)


@router.post("/buildings/profile")
async def building_profile(
    body: BuildingProfileRequest, session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    org_id = access.organization_for(s, body.organization_id)
    return await eui_svc.upsert_building_profile(
        session,
        building_id=body.building_id,
        gia_m2=body.gia_m2,
        building_type=body.building_type,
        organization_id=org_id,
    )


@router.get("/buildings")
async def list_buildings(
    organization_id: UUID | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Building table for the dashboard — every site with its energy profile, latest EUI,
    the benchmark it is read against (and where that benchmark came from) and a record
    completeness figure. See engines/energy/buildings.py.

    The caller's company by default, and for a plain user only their allocated buildings.
    A superadmin may pass organization_id to view another company's table instead —
    access.organization_for() enforces that only a superadmin gets the company they
    asked for; the company used to be whatever ANY client sent, which is what this and
    organization_for() both replaced."""
    org_id = access.organization_for(s, organization_id)
    out = await bld_svc.list_buildings(session, organization_id=org_id, limit=limit)
    if s.restricted and isinstance(out, dict):
        # The engine returns the table under "buildings". The filter first looked for
        # "rows", found nothing, and let a one-building user read the whole portfolio.
        key = "buildings" if isinstance(out.get("buildings"), list) else "rows"
        rows = [r for r in (out.get(key) or [])
                if s.allows_building(r.get("building_id") or r.get("id"))]
        out = dict(out, **{key: rows}, count=len(rows), scoped_to_buildings=len(rows))
    return out


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
    building_code: str | None = Field(
        None,
        description=(
            "Allocated as ORG-COUNTRY-NN-REGION-USE when absent and organization_id, "
            "country_code, region and use_type are all present; the flat B-NN scheme "
            "otherwise. See GET /buildings/next-code to preview it before submitting."
        ),
    )
    city: str | None = None
    postcode: str | None = None
    gfa_sqm: float | None = Field(None, description="Square METRES. Converted to the sqft column on write.")
    metering_route: str | None = None
    organization_id: str | None = None
    created_by: str | None = None


@router.get("/buildings/next-code")
async def preview_building_code(
    country_code: str | None = Query(None),
    region: str | None = Query(None, description="Also accepted as `state`."),
    state: str | None = Query(None),
    use_type: str | None = Query(None, description="Mall is accepted and previews as Retail."),
    organization_id: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """A read-only preview of the code POST /buildings would allocate for this org, country,
    region and use — so the Hoist-a-building form can show it filling in as the fields above
    it are, rather than only after the button is pressed. Calls the exact same allocator
    create_building itself does, so this can never drift from what actually gets stored.

    Not authoritative for very long: two people previewing the same bucket a moment apart can
    both see the same next number, and only the one who actually submits first gets it — the
    other's create then allocates the number after theirs, same as it always could before this
    existed. A preview commits nothing and takes no lock.

    Every input is optional, same as create_building's own fallback: a field left blank (or
    not yet chosen) previews the flat B-NN scheme rather than refusing to answer.
    """
    requested = UUID(organization_id) if organization_id else None
    org_id = access.organization_for(s, requested)

    cc = (country_code or "").strip().upper()
    if cc == "GB":
        cc = "UK"
    if cc == "UAE":
        cc = "AE"

    raw_use = (use_type or "").strip().lower()
    primary_use = bld_create.USE_TYPE_STORES_AS.get(raw_use)

    code = await bld_create._next_building_code(
        session,
        organization_id=str(org_id) if org_id else None,
        country_code=cc or None,
        region=(region or state or "").strip() or None,
        primary_use=primary_use,
    )
    return {"ok": True, "building_code": code}


@router.post("/buildings", status_code=201)
async def create_building(
    body: CreateBuildingRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
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
    # A user is allocated to buildings; only an administrator makes one. And the building
    # belongs to the caller's company whatever the body said (a superadmin may name one).
    access.assert_admin(s, action="create a building")
    requested = UUID(body.organization_id) if body.organization_id else None
    payload = body.model_dump(exclude_none=True)
    org_id = access.organization_for(s, requested)
    payload["organization_id"] = str(org_id) if org_id else None
    out = await bld_create.create_building(session, payload)
    response.status_code = int(out.get("status") or (201 if out.get("ok") else 400))
    return out


class PatchBuildingRequest(BaseModel):
    """Only the fields sent are touched. Omitting one leaves it alone.

    Sending one EMPTY is a different instruction: an optional field is cleared, and a
    required one is refused — a form submitting a blank box must not be able to leave a
    building with no country.
    """
    model_config = {"extra": "allow"}   # unknown keys are refused BY NAME, not ignored

    name: str | None = None
    site_name: str | None = None
    country_code: str | None = None
    country: str | None = None
    region: str | None = None
    state: str | None = None
    use_type: str | None = None
    use_mix: list[UseMixItem] | None = None
    floors: int | None = None
    metering_granularity: str | None = None
    metering_route: str | None = None
    gfa_sqm: float | None = Field(None, description="Square METRES, as on create.")
    building_code: str | None = None
    site_id: str | None = None
    city: str | None = None
    postcode: str | None = None
    updated_by: str | None = None
    expected_updated_at: str | None = Field(
        None,
        description="The updated_at you read. Sent back, it refuses the patch if the row "
                    "moved meanwhile instead of overwriting someone else's edit.",
    )


@router.patch("/buildings/{building_id}")
async def patch_building(
    building_id: str,
    body: PatchBuildingRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    access.assert_building(s, building_id, action="change")
    """Change one building. Returns it in the same shape GET /buildings uses.

    Changing the country or region re-resolves the building's location, because the location
    is what points at the regulation pack — a building moved between markets without one
    keeps being scored against the standard it left.

    raw_metadata is merged, not replaced, so a patch sending only `floors` does not erase the
    use mix stored with the building.

    200 on success. 400 with field-keyed errors, including for any field that is not
    editable. 409 if a building_code is taken, or if `expected_updated_at` says the row moved
    since it was read.
    """
    out = await bld_update.update_building(
        session, building_id, body.model_dump(exclude_unset=True)
    )
    response.status_code = int(out.get("status") or (200 if out.get("ok") else 400))
    return out


@router.post("/buildings/link-locations")
async def link_buildings_to_locations(
    dry_run: bool = Query(True, description="true (default) reports what would change and writes nothing."),
    limit: int = Query(5000, ge=1, le=20000),
    actor: str = Query("hoistra-ui"),
    session: AsyncSession = Depends(get_session),
):
    """Give every building without a location one, derived from its site.

    A building sits in a country and a region, and both are properties of a location — the
    same location that points at the regulation pack the building is scored against. The
    foreign key and the read-path join have always been there; what was missing is anything
    that populates it, so buildings created by backfill-from-sites have none and are scored
    against no standard at all.

    A building whose site carries no country is left alone and reported. Inventing a market
    would attach a legal standard on no evidence, and an unscored building is the safer
    error. Defaults to a dry run.
    """
    return await bld_create.link_buildings_to_locations(
        session, dry_run=dry_run, limit=limit, actor=actor
    )


@router.delete("/buildings/{building_id}")
async def delete_building(
    building_id: str,
    response: Response,
    confirm: bool = Query(False, description="Required to actually delete. Without it this reports what would be touched and changes nothing."),
    detach: bool = Query(True, description="true unlinks attached records and keeps them; false refuses to delete a building that holds any."),
    actor: str = Query("hoistra-ui"),
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    organization_id = access.organization_for(s, organization_id)
    access.assert_building(s, building_id, action="delete")
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


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    response: Response,
    confirm: bool = Query(False, description="Required to actually delete. Without it this reports what would be removed and changes nothing."),
    actor: str = Query("hoistra-ui"),
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Remove a document and everything read out of it. Reports what it would remove
    unless confirm=true.

    The opposite decision to ``DELETE /buildings/{id}``, deliberately. That route detaches
    its children and keeps them, because a certificate outliving its building is still the
    record of an inspection that happened. A certificate extracted from a document that
    should never have been ingested is not the record of anything, and while the document is
    the only thing a person can see and delete, there is no way to be rid of it. So this
    cascades: the certificate on Compliance, the contract terms and invoice lines on
    Vendors, and the chunks the assistant answers from all go with the document.

    Nothing in the database enforces that — no foreign key points at plenum_cafm.documents —
    so the list is written out in the engine and covered by tests. The original file is kept
    in blob storage; a delete made in error can be re-ingested, but the rows do not come back.
    """
    organization_id = access.organization_for(s, organization_id)
    # Deleting is not ingesting: a user allocated to a building may add documents to it, and
    # that is not the same permission as destroying one and every record read out of it.
    access.assert_admin(s, action="delete a document")
    # Narrows to this caller's company and their allocated buildings, and 404s an id that is
    # not theirs rather than confirming it exists. `documents` carries building_id, so the
    # allocation check is real here rather than waved through.
    await access.assert_owned(
        session, s, "documents", document_id, action="delete", id_column="document_id",
    )
    out = await doc_delete.delete_document(
        session, document_id, confirm=confirm, actor=actor, organization_id=organization_id,
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


@router.get("/graph/shape")
async def graph_shape_stats(session: AsyncSession = Depends(get_session)):
    """The graph as this database actually holds it — tables, columns, foreign keys.

    The Hoist Graph panel is a picture of the schema, and a picture that does not read the
    schema goes stale the first time a migration runs, silently, because nothing checks a
    hardcoded number against anything.
    """
    return await bld_tree.graph_shape_stats(session)


@router.get("/graph/tables")
async def graph_tables(session: AsyncSession = Depends(get_session)):
    """Every graph table with its real row count, column count and history.

    The export panel showed a table name, a row count and four dated "builds". The row
    count was a formula over a seed building; the builds were four fixed percentages of it,
    so every table in the portfolio lost precisely 2.4% overnight.

    These are counted. The historical figures come from each row's ``created_at`` rather
    than a stored snapshot — nothing on this platform records what a table's size was on a
    past day — which means a row created last month and deleted yesterday is missing from
    every figure. A past count is therefore a floor, not the exact size, and the payload
    says so in ``history_basis`` rather than leaving the reader to infer it. A table with
    no ``created_at`` reports ``history_available: false`` and offers the live count alone.
    """
    return await bld_tree.graph_tables(session)


@router.get("/buildings/{building_id}/graph")
async def building_graph(
    building_id: str,
    response: Response,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    access.assert_building(s, building_id, action="read")
    """Everything hanging off one building, nested as the graph is written.

        buildings
          ├──< floors ──< spaces
          ├──< assets ──< equipment / meters
          ├──< documents ──< certificates
          └──< contracts ──< work_orders / invoices

    Separate from the table on purpose: forty buildings do not need four hundred child rows
    to draw, and one open building does.

    Each branch carries its own `count` beside the rows returned, so a branch truncated at
    `row_limit` says so rather than looking complete. An empty branch carries an
    `empty_reason` — "no meter on record" is a fact about the building, and the reason its
    EUI is a recorded figure rather than a reading. A branch this database cannot join at
    all is listed in `unavailable`: that is a fact about the deployment, and a caller that
    cannot tell the two apart will say "nothing billed here" about a building that has
    invoices.
    """
    out = await bld_tree.building_tree(session, building_id)
    if not out.get("ok"):
        response.status_code = int(out.get("status") or 400)
    return out


@router.get("/buildings/{building_id}/cost-drivers")
async def building_cost_drivers(
    building_id: str,
    limit: int = Query(25, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    access.assert_building(s, building_id, action="read")
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
    s: access.Scope = Depends(scope),
):
    """Every work order recorded against this asset, with what each one was billed."""
    await access.assert_owned(session, s, "assets", asset_id, action="read")
    return await cost.asset_work_history(session, asset_id, limit=limit)


@router.get("/buildings/{site_id}")
async def get_building(
    site_id: str,
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """One building by sites.site_id (VARCHAR(50)) — same columns as the table row."""
    organization_id = access.organization_for(s, organization_id)
    out = await bld_svc.get_building(session, site_id, organization_id=organization_id)
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail=out)
    return out


@router.get("/tm46")
async def list_tm46():
    return {"ok": True, **eui_svc.load_tm46()}


@router.post("/eui/compute")
async def compute_eui(
    body: EuiComputeRequest, session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    org_id = access.organization_for(s, body.organization_id)
    return await eui_svc.compute_site_eui(
        session,
        building_id=body.building_id,
        period_start=body.period_start,
        period_end=body.period_end,
        meter_type=body.meter_type,
        organization_id=org_id,
    )


@router.post("/condition/deduce")
async def deduce_condition(
    body: ConditionDeduceRequest, session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    org_id = access.organization_for(s, body.organization_id)
    if body.from_vectors or not (body.report_text or "").strip():
        return await cond_svc.deduce_condition_from_vector_layer(
            session,
            asset_id=body.asset_id,
            asset_code=body.asset_code,
            organization_id=org_id,
            write_to_asset=body.write_to_asset,
            auto_cross_ref=body.auto_cross_ref,
        )
    return await cond_svc.deduce_and_write_condition(
        session,
        asset_id=body.asset_id,
        report_text=body.report_text or "",
        source_report_ref=body.source_report_ref,
        asset_code=body.asset_code,
        organization_id=org_id,
        write_to_asset=body.write_to_asset,
        auto_cross_ref=body.auto_cross_ref,
    )


@router.post("/condition/deduce-from-vectors")
async def deduce_from_vectors(
    body: ConditionFromVectorsRequest, session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    org_id = access.organization_for(s, body.organization_id)
    return await cond_svc.deduce_condition_from_vector_layer(
        session,
        asset_id=body.asset_id,
        asset_code=body.asset_code,
        organization_id=org_id,
        write_to_asset=body.write_to_asset,
        auto_cross_ref=body.auto_cross_ref,
    )


@router.post("/recommendations/cross-ref")
async def cross_ref(
    body: CrossRefRequest, session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    org_id = access.organization_for(s, body.organization_id)
    return await cond_svc.cross_reference_condition_consumption(
        session,
        asset_id=body.asset_id,
        organization_id=org_id,
        days=body.days,
    )


@router.post("/occupancy/log")
async def log_occupancy(
    body: OccupancyLogRequest, session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    org_id = access.organization_for(s, body.organization_id)
    return await occ_svc.log_occupancy_change(
        session,
        building_id=body.building_id,
        occupancy_state=body.occupancy_state,
        changed_at=body.changed_at,
        notes=body.notes,
        organization_id=org_id,
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
async def scan_anomalies(
    body: AnomalyScanRequest, session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    org_id = access.organization_for(s, body.organization_id)
    return await anom_svc.scan_meter_anomalies(
        session, meter_id=body.meter_id, organization_id=org_id
    )


@router.post("/anomalies/scan-all")
async def scan_all_anomalies(
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    organization_id = access.organization_for(s, organization_id)
    return await anom_svc.scan_all_active_meters(session, organization_id=organization_id)


@router.get("/sections")
async def list_sections(
    building_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Sections with their measured intensity against their OWN reference.

    A server room read against an office benchmark looks like a catastrophe and a car park
    looks like a triumph, so each section carries its own reference and is judged on that.
    A section with no sub-meter or no area returns a null intensity, not a zero.
    """
    ids = await position_svc.building_ids_for(session, s, building_id)
    return await ai_svc.sections(session, building_ids=ids)


@router.get("/assets/value-at-risk")
async def assets_value_at_risk(
    building_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """The headline figure, with the assets that make it up and the ones that cannot.

    Only assets carrying a replacement value, a design life and an install date are counted.
    The rest are reported as not computable rather than counted as worth nothing.
    """
    ids = await position_svc.building_ids_for(session, s, building_id)
    return await ai_svc.portfolio_value_at_risk(session, building_ids=ids)


@router.get("/assets/{asset_id}/intelligence")
async def asset_intelligence(
    asset_id: str,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """One asset: its section, its vendor, what its deviation is costing, its latest
    readings against their bands, and a failure assessment.

    The assessment is a named rule over recorded signals, not a fitted model. It carries no
    accuracy, precision or recall, and says why.
    """
    # An asset CODE is what a person says. Unresolved it reached the engine as a literal
    # "ACS-DL-07", matched no row, and came back ok=False — the fourth identifier on this
    # service to fail that way, after building, asset and meter on the anomaly list.
    try:
        asset_id = await anom_svc.resolve_asset(session, asset_id) or asset_id
    except anom_svc.UnknownAsset as exc:
        raise HTTPException(status_code=404, detail={
            "ok": False, "reason": "unknown_asset", "error": str(exc)}) from exc
    ids = await position_svc.building_ids_for(session, s, None)
    out = await ai_svc.asset_detail(session, asset_id=asset_id, building_ids=ids)
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail={
            "ok": False, "error": "No such asset in your buildings.",
            "reason": out.get("reason", "not_found")})
    return out


@router.get("/detection/coverage")
async def detection_coverage(
    building_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Which of the thirteen anomaly rules can run, did run and fired — per building, per
    meter and per chiller asset, from a dry run of the detectors themselves (nothing is
    written). A skipped rule names the input it lacks, which is the fix."""
    ids = await position_svc.building_ids_for(session, s, building_id)
    return await coverage_svc.coverage(session, building_ids=ids, organization_id=s.organization_id)


@router.get("/anomalies")
async def list_anomalies(
    status: str | None = "open",
    organization_id: UUID | None = None,
    limit: int = Query(100, le=500),
    building_id: str | None = Query(None, description="One building"),
    asset_id: str | None = Query(None, description="One asset or sub-metered circuit"),
    meter_id: str | None = Query(None),
    anomaly_type: str | None = Query(
        None, description="baseline_drift, weekend_spike, asset_spike … ; spaces and case are "
                          "tolerated, so 'Baseline drift' finds baseline_drift"),
    min_cost: float | None = Query(None, description="Annualised cost at or above this"),
    min_days_active: int | None = Query(
        None, description="Deviation has persisted at least this many days, from window_start"),
    order_by: str = Query("detected_at", pattern="^(detected_at|financial)$"),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """The anomaly list, narrowed by any dimension the activity log shows.

    Every field a person reads on an entry — the building, the circuit, the type, the cost, how
    long it has been active — is now something they can ask by. It filtered on status alone, so
    "baseline drift on Town Hall over £5k, active more than ten days" meant pulling the first
    500 rows and discarding most of them in the caller, which reports a subset as though it were
    the answer whenever there are more than 500.
    """
    organization_id = access.organization_for(s, organization_id)
    try:
        rows = await anom_svc.list_anomalies(
            session, status=status, organization_id=organization_id, limit=limit,
            building_id=building_id, asset_id=asset_id, meter_id=meter_id,
            anomaly_type=anomaly_type, min_cost=min_cost, min_days_active=min_days_active,
            order_by=order_by,
        )
    except (anom_svc.UnknownBuilding, anom_svc.UnknownAsset, anom_svc.UnknownMeter) as exc:
        # 404 and say so. Returning an empty list would read as "this building is clean",
        # which is what produced "no energy anomalies were found for Building 5" about a
        # building with nine open findings.
        raise HTTPException(status_code=404, detail={
            "ok": False, "reason": "unknown_identifier", "error": str(exc)}) from exc
    if s.restricted:
        rows = await bld_svc.restrict_by_site(session, rows, s)
    return {"ok": True, "count": len(rows), "anomalies": rows}


@router.get("/buildings/{building_id}/operating-hours")
async def building_operating_hours(
    building_id: str,
    days: int = Query(90, ge=14, le=365),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """The hours this building keeps, against the hours its benchmark assumes.

    Part of a building's gap to reference is waste and part is that the reference assumes a
    shorter week than the building works. They take different answers - a work order, or a
    re-benchmark - and nothing could tell them apart before, because the assumption was not in
    the schema and the actual was never derived.

    The assumption is stored on the building profile with its source. The actual is derived from
    the load profile on every call and never stored, because a stored operating profile goes
    stale the moment the pattern changes and then reads as measurement.
    """
    await access.assert_owned(session, s, "buildings", building_id,
                              action="read", id_column="building_id")
    return {"ok": True, **await hours_svc.operating_hours(
        session, building_id=building_id, days=days)}


@router.get("/consumption/by-asset")
async def consumption_by_asset(
    category: str | None = Query(None, description='e.g. "Chiller", "Access Control"'),
    days: int = Query(90, ge=1, le=365),
    organization_id: UUID | None = None,
    limit: int = Query(25, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Consumption ranked by asset - "which chiller uses the most power".

    Could not be answered at all before: readings existed by building and efficiency by chiller,
    and nothing put kWh against an asset. Asked which chiller used most, the agent read the
    building list, found no chiller column and said it could not tell - while CH-01 sat there
    sub-metered with 2,880 readings.

    Sub-metered assets only. An asset missing from this list is unmetered, not idle.
    """
    org_id = access.organization_for(s, organization_id)
    ids, _ = access._ids_or_none(s.building_ids)
    return {"ok": True, **await anom_svc.consumption_by_asset(
        session, organization_id=org_id, building_ids=ids,
        category=category, days=days, limit=limit)}


@router.get("/anomalies/summary")
async def anomalies_summary(
    group_by: str = Query("building", pattern="^(building|type|status|asset)$"),
    status: str | None = None,
    organization_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Counts and totals across the WHOLE table, grouped one way, in a single query.

    "Which building has the most anomalies", "which type dominates", "what is waiting on an
    action" are questions about every row, and answering them by listing rows and counting them
    in the caller truncates at the page size and reports a subset as the answer.

    Each group carries `worst` (the largest single finding - the figure to quote, same basis as
    the rollup) and `naive_sum` (every finding added, which double counts overlapping detectors
    and is named so it cannot be mistaken for a total). Grouped by currency as well: the estate
    spans four, and the dearest buildings here are in AED while the next are in GBP.
    """
    org_id = access.organization_for(s, organization_id)
    ids, _ = access._ids_or_none(s.building_ids)
    return {
        "ok": True,
        **await anom_svc.summarise_anomalies(
            session, group_by=group_by, organization_id=org_id,
            building_ids=ids, status=status, limit=limit,
        ),
    }


@router.get("/anomalies/rollup")
async def anomalies_rollup(
    building_id: UUID | None = None,
    status: str | None = "open",
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Findings grouped by building and meter, with a headline that is not their sum.

    Several detectors read one meter over one period and each prices the whole excess it can
    see. They are different lenses on the same consumption, not separate faults, so adding
    them counts the same kilowatt-hour more than once — which is how a building three per cent
    UNDER its own benchmark came to display two gigawatt-hours of waste.

    The headline is the largest single finding. Every rule is still listed with the sentence
    that defines it and its own figure, and ``if_added`` reports what the sum would have been
    so nothing looks quietly dropped. See engines/energy/anomaly_rollup.py.
    """
    ids = await position_svc.building_ids_for(session, s, building_id)
    return await rollup_svc.rollup(
        session, building_ids=ids, organization_id=access.organization_for(s, None),
        status=status)


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
async def monthly_report(
    body: MonthlyReportRequest, session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    org_id = access.organization_for(s, body.organization_id)
    return await report_svc.generate_monthly_energy_report(
        session,
        site_id=body.site_id,
        report_month=body.report_month,
        organization_id=org_id,
        export_pdf=body.export_pdf,
    )


@router.get("/reports/{report_id}/pdf")
async def download_report_pdf(
    report_id: UUID,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    # A report is a building's consumption and spend written out. Served by id alone, any
    # report id downloaded any company's month.
    await access.assert_owned(session, s, "energy_monthly_reports", report_id, action="download")
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
async def saved_space(
    organization_id: UUID | None = None, session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    organization_id = access.organization_for(s, organization_id)
    return await report_svc.saved_space_summary(session, organization_id=organization_id, scope=s)


@router.get("/approvals")
async def list_approvals(
    status: str = "pending",
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    organization_id = access.organization_for(s, organization_id)
    items = await approvals_svc.list_queue(
        session, source_feature="C", status=status, organization_id=organization_id, scope=s
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


# ── B6 / B7 · ratings, and the per-country position ────────────────────────────────────

@router.post("/ratings/compute")
async def compute_rating(
    body: RatingComputeRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Compute and snapshot a consumption rating for one building: an ENERGY STAR score
    estimate or the LL97 emissions position. Needs the building's gross area and at least
    three months of readings; twelve makes it actual, fewer makes it projected."""
    access.organization_for(s, body.organization_id)
    access.assert_building(s, body.building_id, action="rate")
    scheme = body.scheme.lower().strip()
    if scheme == "energy_star":
        return await us_svc.compute_energy_star(session, building_id=body.building_id, months=body.months)
    if scheme == "ll97":
        return await us_svc.compute_ll97(session, building_id=body.building_id, year=body.year, months=body.months)
    raise HTTPException(status_code=400, detail={"ok": False, "error": "scheme must be energy_star or ll97",
                                                 "reason": "bad_scheme"})


@router.get("/ratings")
async def list_ratings(
    building_id: UUID | None = None,
    scheme: str | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """The latest snapshot per building and scheme, for the caller's buildings."""
    ids = await position_svc.building_ids_for(session, s, building_id)
    rows = await us_svc.latest_ratings(session, building_ids=ids, scheme=scheme)
    return {"ok": True, "count": len(rows), "ratings": rows}


@router.get("/ratings/position")
async def ratings_position(
    country_code: str = Query(..., description="UK | US | SG | AE"),
    building_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """The ratings-and-duties tiles for one market, from real records: MEES from the EPC
    register (UK); LL97, Energy Star and LL84 (US); BCA submission, EUI vs the BCA reference
    and Green Mark (SG); the rolling benchmark and chiller kW/RT (AE). Each tile says what it
    is based on — a certificate, a filing, or consumption and how many months of it."""
    ids = await position_svc.building_ids_for(session, s, building_id)
    return await position_svc.position(session, country_code=country_code, organization_id=s.organization_id,
                                       building_ids=ids)


@router.get("/benchmarks/validation")
async def benchmark_validation(
    building_id: UUID | None = None,
    window_months: int = Query(bench_svc.WINDOW_MONTHS, ge=1, le=36),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Every building in scope against its market's benchmark rule — read-only.

    Per market: how many buildings validate, how many EUIs are derived from readings versus
    recorded by hand, the priced excess, which ratings tiles have the inputs to show a figure,
    and for every building that does not validate, exactly what it still needs (meters,
    readings, floor area, use type, an EPC, comparable buildings, chiller design figures).
    Nothing is written; POST /benchmarks/validate persists the derived positions."""
    ids = await position_svc.building_ids_for(session, s, building_id)
    return await bench_svc.validate(session, building_ids=ids, organization_id=s.organization_id,
                                    persist=False, window_months=window_months)


@router.post("/benchmarks/validate")
async def benchmark_validate(
    building_id: UUID | None = None,
    window_months: int = Query(bench_svc.WINDOW_MONTHS, ge=1, le=36),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Run the benchmark validation and persist what it derives: an EUI snapshot per building
    with readings (so the buildings table, the country tiles and the market profiles all read
    the same figure), and LL97 / Energy Star positions for US buildings with enough months.
    The scheduler runs this daily; this is the same run on demand."""
    ids = await position_svc.building_ids_for(session, s, building_id)
    return await bench_svc.validate(session, building_ids=ids, organization_id=s.organization_id,
                                    persist=True, window_months=window_months)


# ── B9 · chillers ───────────────────────────────────────────────────────────────────────

@router.get("/market-profiles")
async def market_profiles(
    markets: str | None = Query(None, description="Comma-separated subset of UK,US,AE,SG; all four by default"),
    building_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """The market-profile table the Energy page lays side by side: benchmark, data source
    and commercial terms per market. Every cell says what kind of fact it is — a published
    reference, a measurement off this deployment's own records, or a figure derived from
    them — and how many buildings or meters stand behind it. The reference EUI is each
    market's own benchmark rule applied to the buildings actually in scope: TM46 weighted by
    use mix (UK), the LL97 cap by occupancy group with the Energy Star target (US), the
    rolling portfolio median (UAE), the BCA reference with per-building overrides (SG)."""
    ids = await position_svc.building_ids_for(session, s, building_id)
    wanted = tuple(m.strip().upper() for m in markets.split(",") if m.strip()) if markets else None
    return await profile_svc.market_profiles(session, building_ids=ids, markets=wanted)


@router.post("/chillers/{asset_id}/design")
async def chiller_design(
    asset_id: UUID,
    body: ChillerDesignRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Record a chiller's design kW/RT (and capacity, design ambient)."""
    org_id = access.organization_for(s, body.organization_id)
    if body.building_id is not None:
        access.assert_building(s, body.building_id, action="record a chiller on")
    return await chiller_svc.upsert_design_spec(
        session, asset_id=asset_id, design_kw_per_rt=body.design_kw_per_rt, building_id=body.building_id,
        organization_id=org_id, design_capacity_rt=body.design_capacity_rt,
        design_ambient_c=body.design_ambient_c, design_chw_supply_c=body.design_chw_supply_c,
        source=body.source, notes=body.notes)


@router.post("/chillers/{asset_id}/readings")
async def chiller_readings(
    asset_id: UUID,
    body: ChillerReadingsRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Ingest kW / RT / ambient samples for a chiller."""
    access.assert_can_ingest(s)
    org_id = access.organization_for(s, body.organization_id)
    if body.building_id is not None:
        access.assert_building(s, body.building_id, action="ingest chiller readings for")
    return await chiller_svc.ingest_readings(session, asset_id=asset_id, readings=body.readings,
                                             building_id=body.building_id, organization_id=org_id, source=body.source)


@router.get("/chillers/{asset_id}/efficiency")
async def chiller_efficiency(
    asset_id: UUID,
    window_days: int = Query(14, ge=1, le=90),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """kW/RT over the window against design — the position whether or not it breaches."""
    out = await chiller_svc.assess(session, asset_id=asset_id, window_days=window_days)
    if out.get("building_id"):
        access.assert_building(s, out["building_id"], action="read")
    return out


@router.post("/chillers/scan")
async def chiller_scan(
    body: ChillerScanRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Assess one chiller, or every chiller with a design figure, raising breaches as anomalies."""
    org_id = access.organization_for(s, body.organization_id)
    if body.asset_id is not None:
        out = await chiller_svc.scan(session, asset_id=body.asset_id, organization_id=org_id,
                                     window_days=body.window_days)
        if out.get("building_id"):
            access.assert_building(s, out["building_id"], action="scan")
        return out
    ids = await position_svc.building_ids_for(session, s, None)
    return await chiller_svc.scan_all(session, organization_id=org_id, building_ids=ids if s.restricted else None)


# ── B10 inputs · degree days and BMS trends ─────────────────────────────────────────────

@router.post("/weather/degree-days")
async def ingest_degree_days(
    body: DegreeDaysRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Monthly HDD/CDD for a building — the input the weather-normalised rule needs."""
    access.assert_can_ingest(s)
    org_id = access.organization_for(s, body.organization_id)
    access.assert_building(s, body.building_id, action="add weather data to")
    return await position_svc.ingest_degree_days(session, building_id=body.building_id, organization_id=org_id,
                                                 months=body.months, base_temp_c=body.base_temp_c,
                                                 station=body.station, source=body.source)


@router.post("/bms/trends")
async def ingest_bms_trends(
    body: BmsTrendsRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Zone heating/cooling samples from the BMS — the input the simultaneous-heating-and-cooling rule needs."""
    access.assert_can_ingest(s)
    org_id = access.organization_for(s, body.organization_id)
    access.assert_building(s, body.building_id, action="add BMS trends to")
    return await position_svc.ingest_bms_trends(session, building_id=body.building_id, organization_id=org_id,
                                                samples=body.samples, source=body.source)


# ── the condition engine ─────────────────────────────────────────────────────────────

class ConditionRulesIn(BaseModel):
    """The two steppers on the Assets page."""
    section_over_reference_pct: float = Field(
        ..., ge=0, le=500, description="A section counts as over reference above this")
    anomaly_persistent_weeks: float = Field(
        ..., ge=0, le=520, description="An anomaly counts as persistent at or past this")


@router.get("/condition/summary")
async def condition_summary(
    building_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Every asset banded Threat, Watch or In control, with the rollups the page prints.

    Bands are computed from current signals on every read, so nothing here is stale. The
    counts underneath each card are returned too: how many of the Watch assets are watched
    because they share an over-reference section rather than because of an anomaly of their
    own, and how many In control assets do carry an anomaly that has not persisted far enough
    to count — that second figure is the one a person actually wants.
    """
    ids = await position_svc.building_ids_for(session, s, building_id)
    return await cond_svc.summary(session, building_ids=ids,
                                  organization_id=access.organization_for(s, None))


@router.get("/condition/assets")
async def condition_assets(
    building_id: UUID | None = None,
    band: str | None = Query(None, pattern="^(threat|watch|in_control)$"),
    min_deviation_pct: float | None = Query(
        None, description="Only assets whose section is over reference by at least this"),
    limit: int = Query(500, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """The banded assets themselves, each with the sentence that explains its band.

    ``band`` backs the Threat / Watch / In control chips; ``min_deviation_pct`` backs the
    Above 10% and Above 30% chips.
    """
    ids = await position_svc.building_ids_for(session, s, building_id)
    out = await cond_svc.assess(session, building_ids=ids,
                                organization_id=access.organization_for(s, None))
    rows = out["assets"]
    if band:
        rows = [a for a in rows if a["band"] == band]
    if min_deviation_pct is not None:
        rows = [a for a in rows
                if a["section_deviation_pct"] is not None
                and a["section_deviation_pct"] >= min_deviation_pct]
    return {"ok": True, "rules": out["rules"], "method": out["method"],
            "count": len(rows), "total": len(out["assets"]), "assets": rows[:limit]}


@router.get("/condition/rules")
async def condition_rules(
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """The thresholds this organisation bands against. ``is_default`` is true until set."""
    return await cond_svc.rules(session, organization_id=access.organization_for(s, None))


@router.put("/condition/rules")
async def set_condition_rules(
    body: ConditionRulesIn,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Move the steppers. Applies to every read from the next one onward."""
    org = access.organization_for(s, None)
    if org is None:
        raise HTTPException(status_code=400, detail={
            "ok": False, "error": "Thresholds are set per organisation and you have none."})
    out = await cond_svc.set_rules(
        session, organization_id=org,
        section_over_reference_pct=body.section_over_reference_pct,
        anomaly_persistent_weeks=body.anomaly_persistent_weeks,
        updated_by=getattr(s, "user_id", None))
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out)
    return out


@router.post("/condition/scan")
async def condition_scan(
    building_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Run the scan and record it: what the Run condition scan button calls.

    The reads do not depend on this having run — they assess live. What a scan adds is a
    timestamp somebody can point at and a verdict per asset that next week's can be compared
    against.
    """
    ids = await position_svc.building_ids_for(session, s, building_id)
    out = await cond_svc.scan(session, building_ids=ids,
                              organization_id=access.organization_for(s, None))
    if not out.get("ok"):
        raise HTTPException(status_code=500, detail=out)
    return out


@router.get("/condition/last-run")
async def condition_last_run(
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """What "LAST RUN 02:14 today" reads. Null before the first scan."""
    run = await cond_svc.last_run(session, organization_id=access.organization_for(s, None))
    return {"ok": True, "last_run": run,
            "note": "null means no scan has been recorded yet, not that there is nothing to scan"}


# ── the Ask bar ──────────────────────────────────────────────────────────────────────

class AssetAskBody(BaseModel):
    """A question in words about the assets or the energy records."""
    question: str = Field(..., min_length=1, max_length=500)
    page: str | None = Field(
        None, description="assets or energy — narrows the chips offered back")


@router.post("/ask")
async def ask_assets(
    body: AssetAskBody,
    building_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Ask a question about the assets, answered from the caller's buildings only.

    Every answer is composed from rows the condition engine or the asset register returned,
    and `source` names the endpoint it came from. Nothing writes SQL from the question and no
    figure is generated — a question this cannot place comes back saying so, with the list of
    what it can be asked.
    """
    ids = await position_svc.building_ids_for(session, s, building_id)
    return await ask_svc.ask(session, question=body.question, building_ids=ids,
                             organization_id=access.organization_for(s, None),
                             page=body.page)


@router.get("/ask/suggestions")
async def ask_asset_suggestions(
    page: str | None = Query(None, pattern="^(assets|energy)$"),
    s: access.Scope = Depends(scope),
):
    """The chips a page shows, served rather than hard-coded in the frontend."""
    return {"ok": True, "suggestions": ask_svc.suggestions(page)}


@router.get("/tariffs")
async def energy_tariffs(
    market: str | None = Query(None, description="One market code: UK, US, AE or SG"),
    s: access.Scope = Depends(scope),
):
    """The machine-readable tariff band each market bills each fuel at, and the unit factors.

    Bands, not points. A commercial tariff is a contract range — 21.0p to 26.0p in the UK, a
    20 to 33 fils slab plus a fuel surcharge in the UAE — and a single figure priced from one
    invents precision it does not have. A fuel with no per-kWh rate at all, like LPG sold by
    weight in the UAE, is marked unpriceable rather than priced at zero.
    """
    profiles = (mp_svc.load_profiles() or {})
    markets = profiles.get("markets") or {}
    codes = [market] if market else list(markets)
    return {
        "ok": True,
        "conversions": profiles.get("conversions") or {},
        "markets": [{
            "market": c, "name": (markets.get(c) or {}).get("name"),
            "currency": (markets.get(c) or {}).get("cur"),
            "tariff": (markets.get(c) or {}).get("tariff"),
            "statutory": (markets.get(c) or {}).get("statutory"),
        } for c in codes if c in markets],
        "note": ("tariffs are bands; prices computed from them are ranges, and markets in "
                 "different currencies are never added because no exchange rate is held here"),
    }


class PriceBody(BaseModel):
    """Energy to price, per market."""
    kwh_by_market: dict[str, float] = Field(
        ..., description='e.g. {"UK": 50000, "AE": 30000}')
    fuel: str = Field("electricity", pattern="^(electricity|gas)$")


@router.post("/price")
async def price_energy(
    body: PriceBody,
    s: access.Scope = Depends(scope),
):
    """Price energy in each market's own currency, as a range.

    Markets are only totalled when every one in scope shares a currency, because then it is
    arithmetic. Otherwise each stands on its own and `total` is null with the reason — adding
    them would need an exchange rate on a stated date, and a portfolio number that quietly
    used a made-up one is worse than no portfolio number.
    """
    return {"ok": True, **price_svc.price_by_market(body.kwh_by_market, fuel=body.fuel)}


@router.get("/statutory/{market}")
async def statutory_assessment(
    market: str,
    eui_kwh_m2: float | None = Query(None, description="Measured intensity, for a market judged on intensity"),
    tco2e: float | None = Query(None, description="Computed carbon, for a market judged on carbon"),
    carbon_cap_tco2e: float | None = Query(None, description="The statutory cap for this occupancy group"),
    s: access.Scope = Depends(scope),
):
    """Is a building over the line its own country draws, and what does that cost?

    The line is a different shape per market. The UK, UAE and Singapore test intensity against
    their own reference and carry no penalty; New York tests carbon against a cap and fines the
    excess at $268 a tonne. A market whose rule cannot be answered from what was passed says
    what it needs rather than returning a verdict.
    """
    return {"ok": True, **price_svc.assess_statutory(
        market.upper(), eui_kwh_m2=eui_kwh_m2, tco2e=tco2e,
        carbon_cap_tco2e=carbon_cap_tco2e)}


@router.get("/assets/{asset_id}/notes")
async def asset_work_and_notes(
    asset_id: str,
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """The work orders and inspection notes under an asset, and what is still owed on it.

    Each order with its date, vendor, the grade the inspector gave and what they wrote — and
    whether the recommendation they left ever became an order. A recommendation with nothing
    raised off it is the row worth reading, and it is counted as `recommendations_open`.

    Warranty comes back as two separate claims. The asset may be in warranty, and a part
    fitted to it may be under its own term long after the asset's has run out — a contactor
    fitted last month on a chiller installed in 2009. Neither is inferred from the other.

    404 if the asset is not in your buildings, the same answer as an asset that does not
    exist, so the API never confirms somebody else's asset is there.
    """
    ids = await position_svc.building_ids_for(session, s, None)
    out = await ai_svc.asset_notes(session, asset_id=asset_id, building_ids=ids, limit=limit)
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail={
            "ok": False, "error": "No such asset in your buildings.",
            "reason": out.get("reason", "not_found")})
    return out


@router.get("/assets/{asset_id}/investigate")
async def investigate_asset(
    asset_id: str,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Why is this asset costing what it is, and what should be done — from the sources.

    What the Investigate button opens. Six sources are walked and each says what it gave
    back, **including the ones that gave back nothing**: a report the contract requires and
    nobody filed is not a gap in the search, it is a finding in it, and it carries full
    confidence because nothing is inferred from it.

    Confidence is fixed per rule by how directly its evidence supports its statement — a
    measured gap with the confounder ruled out is not a trend consistent with three causes —
    so two investigations that found the same thing say the same thing about it. Nothing here
    asks a model.

    **Nothing is written.** Every action comes back as a proposal naming the endpoint and the
    body it would be raised with; a person approves and the caller raises it. `written` is
    false on every response because this route holds no write at all.

    404 if the asset is not in your buildings — the same answer as an asset that does not
    exist.
    """
    # Resolved first: an asset CODE ("ACS-DL-07", "CHILLER-101") is what a person says, and
    # unresolved it reached the engine as a literal and came back ok=False — which this route
    # then reported as "no such asset in your buildings", about an asset that exists.
    try:
        asset_id = await anom_svc.resolve_asset(session, asset_id) or asset_id
    except anom_svc.UnknownAsset as exc:
        raise HTTPException(status_code=404, detail={
            "ok": False, "reason": "unknown_asset", "error": str(exc)}) from exc
    ids = await position_svc.building_ids_for(session, s, None)
    out = await inv_svc.investigate(session, asset_id=asset_id, building_ids=ids)
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail={
            "ok": False, "error": "No such asset in your buildings.",
            "reason": out.get("reason", "not_found")})
    return out
