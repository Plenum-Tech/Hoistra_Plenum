"""CRUD routes — Maintenance Plans, Technicians, Vendors, SLA Policies."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_connector.api.routes.plenum_cafm.deps import get_plenum_db
from cafm_connector.api.schemas.plenum_cafm import (
    MaintenancePlanCreate, MaintenancePlanResponse, MaintenancePlanUpdate,
    SLAPolicyCreate, SLAPolicyResponse, SLAPolicyUpdate,
    TechnicianCreate, TechnicianResponse, TechnicianSkillCreate,
    TechnicianSkillResponse, TechnicianSkillUpdate, TechnicianUpdate,
    VendorContactCreate, VendorContactResponse, VendorContactUpdate,
    VendorContractCreate, VendorContractResponse, VendorContractUpdate,
    VendorCreate, VendorResponse, VendorUpdate,
    PaginatedResponse,
)
from cafm_connector.models.plenum_cafm import (
    MaintenancePlan, SLAPolicy, Technician, TechnicianSkill,
    Vendor, VendorContact, VendorContract,
)

router = APIRouter()


# ══════════════════════════════════════════════════════════════════════
# MAINTENANCE PLANS
# ══════════════════════════════════════════════════════════════════════

@router.get("/maintenance-plans", response_model=PaginatedResponse, tags=["Maintenance"])
async def list_maintenance_plans(
    organization_id: str | None = Query(None),
    asset_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(MaintenancePlan)
    if organization_id:
        stmt = stmt.where(MaintenancePlan.organization_id == organization_id)
    if asset_id:
        stmt = stmt.where(MaintenancePlan.asset_id == asset_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(MaintenancePlan.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[MaintenancePlanResponse.model_validate(r) for r in rows])


@router.get("/maintenance-plans/{plan_id}", response_model=MaintenancePlanResponse, tags=["Maintenance"])
async def get_maintenance_plan(plan_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(MaintenancePlan, plan_id)
    if not row:
        raise HTTPException(404, "Maintenance plan not found")
    return MaintenancePlanResponse.model_validate(row)


@router.post("/maintenance-plans", response_model=MaintenancePlanResponse, status_code=201, tags=["Maintenance"])
async def create_maintenance_plan(body: MaintenancePlanCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = MaintenancePlan(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return MaintenancePlanResponse.model_validate(obj)


@router.put("/maintenance-plans/{plan_id}", response_model=MaintenancePlanResponse, tags=["Maintenance"])
async def update_maintenance_plan(plan_id: str, body: MaintenancePlanUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(MaintenancePlan, plan_id)
    if not obj:
        raise HTTPException(404, "Maintenance plan not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return MaintenancePlanResponse.model_validate(obj)


@router.delete("/maintenance-plans/{plan_id}", status_code=200, tags=["Maintenance"])
async def delete_maintenance_plan(plan_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(MaintenancePlan, plan_id)
    if not obj:
        raise HTTPException(404, "Maintenance plan not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# TECHNICIANS
# ══════════════════════════════════════════════════════════════════════

@router.get("/technicians", response_model=PaginatedResponse, tags=["Technicians"])
async def list_technicians(
    organization_id: str | None = Query(None),
    availability_status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(Technician)
    if organization_id:
        stmt = stmt.where(Technician.organization_id == organization_id)
    if availability_status:
        stmt = stmt.where(Technician.availability_status == availability_status)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(Technician.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[TechnicianResponse.model_validate(r) for r in rows])


@router.get("/technicians/{tech_id}", response_model=TechnicianResponse, tags=["Technicians"])
async def get_technician(tech_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(Technician, tech_id)
    if not row:
        raise HTTPException(404, "Technician not found")
    return TechnicianResponse.model_validate(row)


@router.post("/technicians", response_model=TechnicianResponse, status_code=201, tags=["Technicians"])
async def create_technician(body: TechnicianCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = Technician(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return TechnicianResponse.model_validate(obj)


@router.put("/technicians/{tech_id}", response_model=TechnicianResponse, tags=["Technicians"])
async def update_technician(tech_id: str, body: TechnicianUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(Technician, tech_id)
    if not obj:
        raise HTTPException(404, "Technician not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return TechnicianResponse.model_validate(obj)


@router.delete("/technicians/{tech_id}", status_code=200, tags=["Technicians"])
async def delete_technician(tech_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(Technician, tech_id)
    if not obj:
        raise HTTPException(404, "Technician not found")
    await db.delete(obj)
    await db.commit()


# ── Technician Skills ──────────────────────────────────────────────────

@router.get("/technician-skills", response_model=PaginatedResponse, tags=["Technicians"])
async def list_technician_skills(
    technician_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(TechnicianSkill)
    if technician_id:
        stmt = stmt.where(TechnicianSkill.technician_id == technician_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(TechnicianSkill.id.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[TechnicianSkillResponse.model_validate(r) for r in rows])


@router.get("/technician-skills/{skill_id}", response_model=TechnicianSkillResponse, tags=["Technicians"])
async def get_technician_skill(skill_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(TechnicianSkill, skill_id)
    if not row:
        raise HTTPException(404, "Technician skill not found")
    return TechnicianSkillResponse.model_validate(row)


@router.post("/technician-skills", response_model=TechnicianSkillResponse, status_code=201, tags=["Technicians"])
async def create_technician_skill(body: TechnicianSkillCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = TechnicianSkill(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return TechnicianSkillResponse.model_validate(obj)


@router.put("/technician-skills/{skill_id}", response_model=TechnicianSkillResponse, tags=["Technicians"])
async def update_technician_skill(skill_id: str, body: TechnicianSkillUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(TechnicianSkill, skill_id)
    if not obj:
        raise HTTPException(404, "Technician skill not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return TechnicianSkillResponse.model_validate(obj)


@router.delete("/technician-skills/{skill_id}", status_code=200, tags=["Technicians"])
async def delete_technician_skill(skill_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(TechnicianSkill, skill_id)
    if not obj:
        raise HTTPException(404, "Technician skill not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# VENDORS
# ══════════════════════════════════════════════════════════════════════

@router.get("/vendors", response_model=PaginatedResponse, tags=["Vendors"])
async def list_vendors(
    organization_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(Vendor)
    if organization_id:
        stmt = stmt.where(Vendor.organization_id == organization_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(Vendor.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[VendorResponse.model_validate(r) for r in rows])


@router.get("/vendors/{vendor_id}", response_model=VendorResponse, tags=["Vendors"])
async def get_vendor(vendor_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(Vendor, vendor_id)
    if not row:
        raise HTTPException(404, "Vendor not found")
    return VendorResponse.model_validate(row)


@router.post("/vendors", response_model=VendorResponse, status_code=201, tags=["Vendors"])
async def create_vendor(body: VendorCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = Vendor(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return VendorResponse.model_validate(obj)


@router.put("/vendors/{vendor_id}", response_model=VendorResponse, tags=["Vendors"])
async def update_vendor(vendor_id: str, body: VendorUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(Vendor, vendor_id)
    if not obj:
        raise HTTPException(404, "Vendor not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return VendorResponse.model_validate(obj)


@router.delete("/vendors/{vendor_id}", status_code=200, tags=["Vendors"])
async def delete_vendor(vendor_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(Vendor, vendor_id)
    if not obj:
        raise HTTPException(404, "Vendor not found")
    await db.delete(obj)
    await db.commit()


# ── Vendor Contacts ────────────────────────────────────────────────────

@router.get("/vendor-contacts", response_model=PaginatedResponse, tags=["Vendors"])
async def list_vendor_contacts(
    vendor_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(VendorContact)
    if vendor_id:
        stmt = stmt.where(VendorContact.vendor_id == vendor_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(VendorContact.id.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[VendorContactResponse.model_validate(r) for r in rows])


@router.get("/vendor-contacts/{contact_id}", response_model=VendorContactResponse, tags=["Vendors"])
async def get_vendor_contact(contact_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(VendorContact, contact_id)
    if not row:
        raise HTTPException(404, "Vendor contact not found")
    return VendorContactResponse.model_validate(row)


@router.post("/vendor-contacts", response_model=VendorContactResponse, status_code=201, tags=["Vendors"])
async def create_vendor_contact(body: VendorContactCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = VendorContact(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return VendorContactResponse.model_validate(obj)


@router.put("/vendor-contacts/{contact_id}", response_model=VendorContactResponse, tags=["Vendors"])
async def update_vendor_contact(contact_id: str, body: VendorContactUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(VendorContact, contact_id)
    if not obj:
        raise HTTPException(404, "Vendor contact not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return VendorContactResponse.model_validate(obj)


@router.delete("/vendor-contacts/{contact_id}", status_code=200, tags=["Vendors"])
async def delete_vendor_contact(contact_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(VendorContact, contact_id)
    if not obj:
        raise HTTPException(404, "Vendor contact not found")
    await db.delete(obj)
    await db.commit()


# ── Vendor Contracts ───────────────────────────────────────────────────

@router.get("/vendor-contracts", response_model=PaginatedResponse, tags=["Vendors"])
async def list_vendor_contracts(
    organization_id: str | None = Query(None),
    vendor_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(VendorContract)
    if organization_id:
        stmt = stmt.where(VendorContract.organization_id == organization_id)
    if vendor_id:
        stmt = stmt.where(VendorContract.vendor_id == vendor_id)
    if status:
        stmt = stmt.where(VendorContract.status == status)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(VendorContract.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[VendorContractResponse.model_validate(r) for r in rows])


@router.get("/vendor-contracts/{contract_id}", response_model=VendorContractResponse, tags=["Vendors"])
async def get_vendor_contract(contract_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(VendorContract, contract_id)
    if not row:
        raise HTTPException(404, "Vendor contract not found")
    return VendorContractResponse.model_validate(row)


@router.post("/vendor-contracts", response_model=VendorContractResponse, status_code=201, tags=["Vendors"])
async def create_vendor_contract(body: VendorContractCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = VendorContract(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return VendorContractResponse.model_validate(obj)


@router.put("/vendor-contracts/{contract_id}", response_model=VendorContractResponse, tags=["Vendors"])
async def update_vendor_contract(contract_id: str, body: VendorContractUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(VendorContract, contract_id)
    if not obj:
        raise HTTPException(404, "Vendor contract not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return VendorContractResponse.model_validate(obj)


@router.delete("/vendor-contracts/{contract_id}", status_code=200, tags=["Vendors"])
async def delete_vendor_contract(contract_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(VendorContract, contract_id)
    if not obj:
        raise HTTPException(404, "Vendor contract not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# SLA POLICIES
# ══════════════════════════════════════════════════════════════════════

@router.get("/sla-policies", response_model=PaginatedResponse, tags=["SLA"])
async def list_sla_policies(
    organization_id: str | None = Query(None),
    priority: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(SLAPolicy)
    if organization_id:
        stmt = stmt.where(SLAPolicy.organization_id == organization_id)
    if priority:
        stmt = stmt.where(SLAPolicy.priority == priority)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(SLAPolicy.id.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[SLAPolicyResponse.model_validate(r) for r in rows])


@router.get("/sla-policies/{sla_id}", response_model=SLAPolicyResponse, tags=["SLA"])
async def get_sla_policy(sla_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(SLAPolicy, sla_id)
    if not row:
        raise HTTPException(404, "SLA policy not found")
    return SLAPolicyResponse.model_validate(row)


@router.post("/sla-policies", response_model=SLAPolicyResponse, status_code=201, tags=["SLA"])
async def create_sla_policy(body: SLAPolicyCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = SLAPolicy(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return SLAPolicyResponse.model_validate(obj)


@router.put("/sla-policies/{sla_id}", response_model=SLAPolicyResponse, tags=["SLA"])
async def update_sla_policy(sla_id: str, body: SLAPolicyUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(SLAPolicy, sla_id)
    if not obj:
        raise HTTPException(404, "SLA policy not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return SLAPolicyResponse.model_validate(obj)


@router.delete("/sla-policies/{sla_id}", status_code=200, tags=["SLA"])
async def delete_sla_policy(sla_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(SLAPolicy, sla_id)
    if not obj:
        raise HTTPException(404, "SLA policy not found")
    await db.delete(obj)
    await db.commit()