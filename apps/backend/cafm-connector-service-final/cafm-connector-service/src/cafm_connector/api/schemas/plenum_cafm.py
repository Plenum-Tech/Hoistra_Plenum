"""
Pydantic schemas for the plenum_cafm CRUD API.

Each table has:
  - Create   (POST body — no id/timestamps)
  - Update   (PUT body  — all fields optional)
  - Response (GET body — full row)
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID
import uuid as _uuid_mod

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── helpers ────────────────────────────────────────────────────────────

class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_validator("id", mode="before", check_fields=False)
    @classmethod
    def _coerce_int_id(cls, v: Any) -> Any:
        # Azure DB has legacy integer PKs on some tables (pre-UUID migration)
        if isinstance(v, int):
            return _uuid_mod.UUID(int=v)
        return v


# ══════════════════════════════════════════════════════════════════════
# ORGANIZATIONS
# ══════════════════════════════════════════════════════════════════════

class OrganizationCreate(BaseModel):
    name: str = Field(..., max_length=255)
    industry: str | None = Field(None, max_length=150)
    address: str | None = None
    country: str | None = Field(None, max_length=100)
    timezone: str | None = Field(None, max_length=100)
    status: str = Field("active", max_length=50)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(None, max_length=255)
    industry: str | None = Field(None, max_length=150)
    address: str | None = None
    country: str | None = Field(None, max_length=100)
    timezone: str | None = Field(None, max_length=100)
    status: str | None = Field(None, max_length=50)


class OrganizationResponse(_Base):
    id: UUID
    name: str
    industry: str | None
    address: str | None
    country: str | None
    timezone: str | None
    status: str
    created_at: datetime
    updated_at: datetime


# ══════════════════════════════════════════════════════════════════════
# USERS
# ══════════════════════════════════════════════════════════════════════

class UserCreate(BaseModel):
    organization_id: UUID
    full_name: str = Field(..., max_length=255)
    email: str = Field(..., max_length=255)
    password_hash: str = Field(..., max_length=500)
    phone: str | None = Field(None, max_length=50)
    status: str = Field("active", max_length=50)
    email_verified: bool = False


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, max_length=255)
    email: str | None = Field(None, max_length=255)
    password_hash: str | None = Field(None, max_length=500)
    phone: str | None = Field(None, max_length=50)
    status: str | None = Field(None, max_length=50)
    email_verified: bool | None = None
    last_login_at: datetime | None = None


class UserResponse(_Base):
    id: UUID
    organization_id: UUID
    full_name: str
    email: str
    phone: str | None
    status: str
    last_login_at: datetime | None
    email_verified: bool
    created_at: datetime
    updated_at: datetime


# ══════════════════════════════════════════════════════════════════════
# ROLES
# ══════════════════════════════════════════════════════════════════════

class RoleCreate(BaseModel):
    organization_id: UUID
    name: str = Field(..., max_length=100)
    description: str | None = None


class RoleUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    description: str | None = None


class RoleResponse(_Base):
    id: UUID
    organization_id: UUID
    name: str
    description: str | None


# ══════════════════════════════════════════════════════════════════════
# PERMISSIONS
# ══════════════════════════════════════════════════════════════════════

class PermissionCreate(BaseModel):
    name: str = Field(..., max_length=150)
    description: str | None = None


class PermissionUpdate(BaseModel):
    name: str | None = Field(None, max_length=150)
    description: str | None = None


class PermissionResponse(_Base):
    id: UUID
    name: str
    description: str | None


# ══════════════════════════════════════════════════════════════════════
# USER ROLES
# ══════════════════════════════════════════════════════════════════════

class UserRoleCreate(BaseModel):
    user_id: UUID
    role_id: UUID


class UserRoleResponse(_Base):
    id: UUID
    user_id: UUID
    role_id: UUID


# ══════════════════════════════════════════════════════════════════════
# ROLE PERMISSIONS
# ══════════════════════════════════════════════════════════════════════

class RolePermissionCreate(BaseModel):
    role_id: UUID
    permission_id: UUID


class RolePermissionResponse(_Base):
    id: UUID
    role_id: UUID
    permission_id: UUID


# ══════════════════════════════════════════════════════════════════════
# LOCATIONS
# ══════════════════════════════════════════════════════════════════════

class LocationCreate(BaseModel):
    organization_id: UUID
    name: str = Field(..., max_length=255)
    type: str = Field(..., max_length=100)
    parent_location_id: UUID | None = None
    level: int | None = None


class LocationUpdate(BaseModel):
    name: str | None = Field(None, max_length=255)
    type: str | None = Field(None, max_length=100)
    parent_location_id: UUID | None = None
    level: int | None = None


class LocationResponse(_Base):
    id: UUID
    organization_id: UUID
    name: str
    type: str
    parent_location_id: UUID | None
    level: int | None
    created_at: datetime


# ══════════════════════════════════════════════════════════════════════
# ASSET CATEGORIES
# ══════════════════════════════════════════════════════════════════════

class AssetCategoryCreate(BaseModel):
    organization_id: UUID
    name: str = Field(..., max_length=150)
    description: str | None = None


class AssetCategoryUpdate(BaseModel):
    name: str | None = Field(None, max_length=150)
    description: str | None = None


class AssetCategoryResponse(_Base):
    id: UUID
    organization_id: UUID
    name: str
    description: str | None


# ══════════════════════════════════════════════════════════════════════
# ASSETS
# ══════════════════════════════════════════════════════════════════════

class AssetCreate(BaseModel):
    organization_id: UUID
    location_id: UUID | None = None
    category_id: UUID | None = None
    asset_name: str = Field(..., max_length=255)
    asset_code: str | None = Field(None, max_length=150)
    serial_number: str | None = Field(None, max_length=150)
    manufacturer: str | None = Field(None, max_length=150)
    model_number: str | None = Field(None, max_length=150)
    installation_date: date | None = None
    warranty_expiry: date | None = None
    status: str = Field("active", max_length=50)
    health_score: int | None = Field(None, ge=0, le=100)
    qr_code: str | None = Field(None, max_length=255)


class AssetUpdate(BaseModel):
    location_id: UUID | None = None
    category_id: UUID | None = None
    asset_name: str | None = Field(None, max_length=255)
    asset_code: str | None = Field(None, max_length=150)
    serial_number: str | None = Field(None, max_length=150)
    manufacturer: str | None = Field(None, max_length=150)
    model_number: str | None = Field(None, max_length=150)
    installation_date: date | None = None
    warranty_expiry: date | None = None
    status: str | None = Field(None, max_length=50)
    health_score: int | None = Field(None, ge=0, le=100)
    qr_code: str | None = Field(None, max_length=255)


class AssetResponse(_Base):
    id: UUID
    organization_id: UUID
    location_id: UUID | None
    category_id: UUID | None
    asset_name: str
    asset_code: str | None
    serial_number: str | None
    manufacturer: str | None
    model_number: str | None
    installation_date: date | None
    warranty_expiry: date | None
    status: str
    health_score: int | None
    qr_code: str | None
    created_at: datetime
    updated_at: datetime


# ══════════════════════════════════════════════════════════════════════
# ASSET DOCUMENTS
# ══════════════════════════════════════════════════════════════════════

class AssetDocumentCreate(BaseModel):
    asset_id: UUID
    file_url: str
    document_type: str | None = Field(None, max_length=100)
    uploaded_by: UUID | None = None


class AssetDocumentUpdate(BaseModel):
    file_url: str | None = None
    document_type: str | None = Field(None, max_length=100)


class AssetDocumentResponse(_Base):
    id: UUID
    asset_id: UUID
    file_url: str
    document_type: str | None
    uploaded_by: UUID | None
    uploaded_at: datetime


# ══════════════════════════════════════════════════════════════════════
# ASSET READINGS
# ══════════════════════════════════════════════════════════════════════

class AssetReadingCreate(BaseModel):
    organization_id: UUID
    asset_id: UUID
    reading_type: str = Field(..., max_length=100)
    value: Decimal
    unit: str | None = Field(None, max_length=50)
    recorded_at: datetime | None = None


class AssetReadingUpdate(BaseModel):
    reading_type: str | None = Field(None, max_length=100)
    value: Decimal | None = None
    unit: str | None = Field(None, max_length=50)
    recorded_at: datetime | None = None


class AssetReadingResponse(_Base):
    id: UUID
    organization_id: UUID
    asset_id: UUID
    reading_type: str
    value: Decimal
    unit: str | None
    recorded_at: datetime


# ══════════════════════════════════════════════════════════════════════
# MAINTENANCE PLANS
# ══════════════════════════════════════════════════════════════════════

class MaintenancePlanCreate(BaseModel):
    organization_id: UUID
    asset_id: UUID
    maintenance_type: str = Field(..., max_length=100)
    frequency_type: str = Field(..., max_length=50)
    frequency_value: int = Field(..., gt=0)
    next_due_date: date | None = None


class MaintenancePlanUpdate(BaseModel):
    maintenance_type: str | None = Field(None, max_length=100)
    frequency_type: str | None = Field(None, max_length=50)
    frequency_value: int | None = Field(None, gt=0)
    next_due_date: date | None = None


class MaintenancePlanResponse(_Base):
    id: UUID
    organization_id: UUID
    asset_id: UUID
    maintenance_type: str
    frequency_type: str
    frequency_value: int
    next_due_date: date | None
    created_at: datetime


# ══════════════════════════════════════════════════════════════════════
# TECHNICIANS
# ══════════════════════════════════════════════════════════════════════

class TechnicianCreate(BaseModel):
    organization_id: UUID
    user_id: UUID
    base_location: str | None = Field(None, max_length=255)
    availability_status: str | None = Field(None, max_length=50)
    performance_score: Decimal | None = None


class TechnicianUpdate(BaseModel):
    base_location: str | None = Field(None, max_length=255)
    availability_status: str | None = Field(None, max_length=50)
    performance_score: Decimal | None = None


class TechnicianResponse(_Base):
    id: UUID
    organization_id: UUID
    user_id: UUID
    base_location: str | None
    availability_status: str | None
    performance_score: Decimal | None
    created_at: datetime


# ══════════════════════════════════════════════════════════════════════
# TECHNICIAN SKILLS
# ══════════════════════════════════════════════════════════════════════

class TechnicianSkillCreate(BaseModel):
    technician_id: UUID
    skill_name: str = Field(..., max_length=150)
    skill_level: str | None = Field(None, max_length=50)


class TechnicianSkillUpdate(BaseModel):
    skill_name: str | None = Field(None, max_length=150)
    skill_level: str | None = Field(None, max_length=50)


class TechnicianSkillResponse(_Base):
    id: UUID
    technician_id: UUID
    skill_name: str
    skill_level: str | None


# ══════════════════════════════════════════════════════════════════════
# VENDORS
# ══════════════════════════════════════════════════════════════════════

class VendorCreate(BaseModel):
    organization_id: UUID
    vendor_name: str = Field(..., max_length=255)
    address: str | None = None


class VendorUpdate(BaseModel):
    vendor_name: str | None = Field(None, max_length=255)
    address: str | None = None


class VendorResponse(_Base):
    id: UUID
    organization_id: UUID
    vendor_name: str
    address: str | None
    created_at: datetime


# ══════════════════════════════════════════════════════════════════════
# VENDOR CONTACTS
# ══════════════════════════════════════════════════════════════════════

class VendorContactCreate(BaseModel):
    vendor_id: UUID
    name: str = Field(..., max_length=255)
    email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=50)
    designation: str | None = Field(None, max_length=150)


class VendorContactUpdate(BaseModel):
    name: str | None = Field(None, max_length=255)
    email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=50)
    designation: str | None = Field(None, max_length=150)


class VendorContactResponse(_Base):
    id: UUID
    vendor_id: UUID
    name: str
    email: str | None
    phone: str | None
    designation: str | None


# ══════════════════════════════════════════════════════════════════════
# VENDOR CONTRACTS
# ══════════════════════════════════════════════════════════════════════

class VendorContractCreate(BaseModel):
    organization_id: UUID
    vendor_id: UUID
    contract_name: str = Field(..., max_length=255)
    contract_start: date | None = None
    contract_end: date | None = None
    contract_value: Decimal | None = None
    sla_terms: str | None = None
    contract_document: str | None = None
    status: str | None = Field(None, max_length=50)


class VendorContractUpdate(BaseModel):
    contract_name: str | None = Field(None, max_length=255)
    contract_start: date | None = None
    contract_end: date | None = None
    contract_value: Decimal | None = None
    sla_terms: str | None = None
    contract_document: str | None = None
    status: str | None = Field(None, max_length=50)


class VendorContractResponse(_Base):
    id: UUID
    organization_id: UUID
    vendor_id: UUID
    contract_name: str
    contract_start: date | None
    contract_end: date | None
    contract_value: Decimal | None
    sla_terms: str | None
    contract_document: str | None
    status: str | None
    created_at: datetime


# ══════════════════════════════════════════════════════════════════════
# SLA POLICIES
# ══════════════════════════════════════════════════════════════════════

class SLAPolicyCreate(BaseModel):
    organization_id: UUID
    name: str = Field(..., max_length=150)
    priority: str = Field(..., max_length=50)
    response_time_minutes: int = Field(..., ge=0)
    resolution_time_minutes: int = Field(..., ge=0)


class SLAPolicyUpdate(BaseModel):
    name: str | None = Field(None, max_length=150)
    priority: str | None = Field(None, max_length=50)
    response_time_minutes: int | None = Field(None, ge=0)
    resolution_time_minutes: int | None = Field(None, ge=0)


class SLAPolicyResponse(_Base):
    id: UUID
    organization_id: UUID
    name: str
    priority: str
    response_time_minutes: int
    resolution_time_minutes: int


# ══════════════════════════════════════════════════════════════════════
# WORK ORDERS
# ══════════════════════════════════════════════════════════════════════

class WorkOrderCreate(BaseModel):
    organization_id: UUID
    asset_id: UUID | None = None
    location_id: UUID | None = None
    title: str = Field(..., max_length=255)
    description: str | None = None
    priority: str = Field("medium", max_length=50)
    status: str = Field("open", max_length=50)
    created_by: UUID | None = None
    assigned_technician: UUID | None = None
    assigned_vendor: UUID | None = None
    sla_id: UUID | None = None
    sla_due_at: datetime | None = None


class WorkOrderUpdate(BaseModel):
    asset_id: UUID | None = None
    location_id: UUID | None = None
    title: str | None = Field(None, max_length=255)
    description: str | None = None
    priority: str | None = Field(None, max_length=50)
    status: str | None = Field(None, max_length=50)
    assigned_technician: UUID | None = None
    assigned_vendor: UUID | None = None
    sla_id: UUID | None = None
    sla_due_at: datetime | None = None
    completed_at: datetime | None = None


class WorkOrderResponse(_Base):
    id: UUID
    organization_id: UUID
    asset_id: UUID | None
    location_id: UUID | None
    title: str
    description: str | None
    priority: str
    status: str
    created_by: UUID | None
    assigned_technician: UUID | None
    assigned_vendor: UUID | None
    sla_id: UUID | None
    sla_due_at: datetime | None
    created_at: datetime
    completed_at: datetime | None


# ══════════════════════════════════════════════════════════════════════
# WORK ORDER TASKS
# ══════════════════════════════════════════════════════════════════════

class WorkOrderTaskCreate(BaseModel):
    work_order_id: UUID
    title: str = Field(..., max_length=255)
    description: str | None = None
    assigned_to: UUID | None = None
    status: str = Field("pending", max_length=50)


class WorkOrderTaskUpdate(BaseModel):
    title: str | None = Field(None, max_length=255)
    description: str | None = None
    assigned_to: UUID | None = None
    status: str | None = Field(None, max_length=50)
    completed_at: datetime | None = None


class WorkOrderTaskResponse(_Base):
    id: UUID
    work_order_id: UUID
    title: str
    description: str | None
    assigned_to: UUID | None
    status: str
    created_at: datetime
    completed_at: datetime | None


# ══════════════════════════════════════════════════════════════════════
# WORK ORDER COMMENTS
# ══════════════════════════════════════════════════════════════════════

class WorkOrderCommentCreate(BaseModel):
    work_order_id: UUID
    user_id: UUID | None = None
    comment: str


class WorkOrderCommentUpdate(BaseModel):
    comment: str | None = None


class WorkOrderCommentResponse(_Base):
    id: UUID
    work_order_id: UUID
    user_id: UUID | None
    comment: str
    created_at: datetime


# ══════════════════════════════════════════════════════════════════════
# WORK ORDER ATTACHMENTS
# ══════════════════════════════════════════════════════════════════════

class WorkOrderAttachmentCreate(BaseModel):
    work_order_id: UUID
    file_url: str
    uploaded_by: UUID | None = None


class WorkOrderAttachmentResponse(_Base):
    id: UUID
    work_order_id: UUID
    file_url: str
    uploaded_by: UUID | None
    uploaded_at: datetime


# ══════════════════════════════════════════════════════════════════════
# WORK ORDER HISTORY
# ══════════════════════════════════════════════════════════════════════

class WorkOrderHistoryCreate(BaseModel):
    work_order_id: UUID
    old_status: str | None = Field(None, max_length=50)
    new_status: str | None = Field(None, max_length=50)
    changed_by: UUID | None = None


class WorkOrderHistoryResponse(_Base):
    id: UUID
    work_order_id: UUID
    old_status: str | None
    new_status: str | None
    changed_by: UUID | None
    changed_at: datetime


# ══════════════════════════════════════════════════════════════════════
# MAINTENANCE HISTORY
# ══════════════════════════════════════════════════════════════════════

class MaintenanceHistoryCreate(BaseModel):
    asset_id: UUID
    work_order_id: UUID | None = None
    performed_by: UUID | None = None
    performed_at: datetime | None = None
    notes: str | None = None


class MaintenanceHistoryUpdate(BaseModel):
    work_order_id: UUID | None = None
    performed_by: UUID | None = None
    performed_at: datetime | None = None
    notes: str | None = None


class MaintenanceHistoryResponse(_Base):
    id: UUID
    asset_id: UUID
    work_order_id: UUID | None
    performed_by: UUID | None
    performed_at: datetime
    notes: str | None


# ══════════════════════════════════════════════════════════════════════
# SPARE PARTS
# ══════════════════════════════════════════════════════════════════════

class SparePartCreate(BaseModel):
    organization_id: UUID
    part_name: str = Field(..., max_length=255)
    part_code: str | None = Field(None, max_length=150)
    description: str | None = None
    unit_price: Decimal | None = None
    stock_quantity: int = Field(0, ge=0)
    reorder_level: int = Field(0, ge=0)


class SparePartUpdate(BaseModel):
    part_name: str | None = Field(None, max_length=255)
    part_code: str | None = Field(None, max_length=150)
    description: str | None = None
    unit_price: Decimal | None = None
    stock_quantity: int | None = Field(None, ge=0)
    reorder_level: int | None = Field(None, ge=0)


class SparePartResponse(_Base):
    id: UUID
    organization_id: UUID
    part_name: str
    part_code: str | None
    description: str | None
    unit_price: Decimal | None
    stock_quantity: int
    reorder_level: int


# ══════════════════════════════════════════════════════════════════════
# INVENTORY TRANSACTIONS
# ══════════════════════════════════════════════════════════════════════

class InventoryTransactionCreate(BaseModel):
    part_id: UUID
    transaction_type: str = Field(..., max_length=50)
    quantity: int
    reference_type: str | None = Field(None, max_length=100)
    reference_id: UUID | None = None


class InventoryTransactionResponse(_Base):
    id: UUID
    part_id: UUID
    transaction_type: str
    quantity: int
    reference_type: str | None
    reference_id: UUID | None
    created_at: datetime


# ══════════════════════════════════════════════════════════════════════
# WORK ORDER PARTS
# ══════════════════════════════════════════════════════════════════════

class WorkOrderPartCreate(BaseModel):
    work_order_id: UUID
    part_id: UUID
    quantity_used: int = Field(..., gt=0)


class WorkOrderPartUpdate(BaseModel):
    quantity_used: int | None = Field(None, gt=0)


class WorkOrderPartResponse(_Base):
    id: UUID
    work_order_id: UUID
    part_id: UUID
    quantity_used: int


# ══════════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════

class NotificationCreate(BaseModel):
    organization_id: UUID
    user_id: UUID | None = None
    title: str = Field(..., max_length=255)
    message: str
    type: str | None = Field(None, max_length=100)
    entity_type: str | None = Field(None, max_length=100)
    entity_id: UUID | None = None
    is_read: bool = False


class NotificationUpdate(BaseModel):
    is_read: bool | None = None
    title: str | None = Field(None, max_length=255)
    message: str | None = None


class NotificationResponse(_Base):
    id: UUID
    organization_id: UUID
    user_id: UUID | None
    title: str
    message: str
    type: str | None
    entity_type: str | None
    entity_id: UUID | None
    is_read: bool
    created_at: datetime


# ══════════════════════════════════════════════════════════════════════
# AUDIT LOGS
# ══════════════════════════════════════════════════════════════════════

class AuditLogCreate(BaseModel):
    organization_id: UUID
    user_id: UUID | None = None
    action: str = Field(..., max_length=150)
    entity_type: str | None = Field(None, max_length=100)
    entity_id: UUID | None = None
    metadata: str | None = None


class AuditLogResponse(_Base):
    id: UUID
    organization_id: UUID
    user_id: UUID | None
    action: str
    entity_type: str | None
    entity_id: UUID | None
    metadata: str | None
    created_at: datetime


# ── Generic paginated list wrapper ─────────────────────────────────────

class PaginatedResponse(_Base):
    total: int
    limit: int
    offset: int
    data: list[Any]