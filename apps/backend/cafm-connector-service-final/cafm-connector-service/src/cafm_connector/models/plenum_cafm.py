"""
SQLAlchemy ORM models for the plenum_cafm schema.

Fiix-inspired expansion: adds reference tables (priorities, maintenance_types,
work_order_statuses, charge_departments, projects, bom_groups, task_groups),
enriches all core tables with missing Fiix fields, and adds junction tables
for many-to-many relationships (work_order_assets, work_order_users, etc.).

All new columns are nullable — existing rows are unaffected.
All tables live under the "plenum_cafm" schema.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "plenum_cafm"


class PlenumBase(DeclarativeBase):
    pass


# ══════════════════════════════════════════════════════════════════════════════
# REFERENCE / LOOKUP TABLES  (Fiix-inspired — previously missing)
# ══════════════════════════════════════════════════════════════════════════════

class Priority(PlenumBase):
    """Fiix: Priority — ordered priority levels per organisation."""
    __tablename__ = "priorities"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_priorities_org_name"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)        # Highest, High, Medium, Low, Lowest
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    color_hex: Mapped[str | None] = mapped_column(String(10))             # UI colour hint
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class MaintenanceType(PlenumBase):
    """Fiix: MaintenanceType — Preventive, Corrective, Predictive, Emergency, etc."""
    __tablename__ = "maintenance_types"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_maintenance_types_org_name"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class WorkOrderStatus(PlenumBase):
    """Fiix: WorkOrderStatus — configurable WO lifecycle statuses."""
    __tablename__ = "work_order_statuses"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_wo_statuses_org_name"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)        # Open, In Progress, On Hold, Closed, Cancelled
    is_closed_state: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class ChargeDepartment(PlenumBase):
    """Fiix: ChargeDepartment — cost centres that WOs and assets are charged to."""
    __tablename__ = "charge_departments"
    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_charge_departments_org_code"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class Project(PlenumBase):
    """Fiix: Project — groups WOs and assets under a project."""
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_projects_org_name"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.locations.id", ondelete="SET NULL"),
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="active")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class BOMGroup(PlenumBase):
    """Fiix: BOMGroup — bill of materials group; links parts to asset types."""
    __tablename__ = "bom_groups"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_bom_groups_org_name"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class MeterReadingUnit(PlenumBase):
    """Fiix: MeterReadingUnit — units for meter readings (hours, km, cycles, kWh)."""
    __tablename__ = "meter_reading_units"
    __table_args__ = (
        UniqueConstraint("organization_id", "symbol", name="uq_meter_reading_units_org_symbol"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    precision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="2")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class TaskGroup(PlenumBase):
    """Fiix: TaskGroup — reusable named task list templates applied to WOs / SMs."""
    __tablename__ = "task_groups"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_task_groups_org_name"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class PurchaseOrderStatus(PlenumBase):
    """Fiix: PurchaseOrderStatus — reference statuses for purchase orders."""
    __tablename__ = "purchase_order_statuses"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_po_statuses_org_name"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)        # Draft, Submitted, Approved, Received, Cancelled
    is_closed_state: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


# ══════════════════════════════════════════════════════════════════════════════
# CORE AUTH / ORG TABLES  (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

class Organization(PlenumBase):
    __tablename__ = "organizations"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(150))
    address: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(100))
    timezone: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class User(PlenumBase):
    """Fiix: User — enriched with personnel_code, hourly_rate, is_group, phone2."""
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(500), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50))
    phone2: Mapped[str | None] = mapped_column(String(50))                # Fiix: strTelephone2
    personnel_code: Mapped[str | None] = mapped_column(String(100))       # Fiix: strPersonnelCode
    hourly_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))   # Fiix: dblHourlyRate
    is_group: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")  # Fiix: bolGroup
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="active")
    # Platform role: superadmin | admin | user. The database holds the CHECK constraint
    # (see svc-operations-intelligence/migrations/auth_user_roles.sql); this column is
    # declared here because this file is the one declaration of what a user is.
    role: Mapped[str] = mapped_column(String(20), nullable=False, server_default="user")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class Role(PlenumBase):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_roles_org_name"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class Permission(PlenumBase):
    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint("name", name="uq_permissions_name"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class UserRole(PlenumBase):
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_roles"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.roles.id", ondelete="CASCADE"), nullable=False,
    )


class RolePermission(PlenumBase):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permissions"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.roles.id", ondelete="CASCADE"), nullable=False,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.permissions.id", ondelete="CASCADE"), nullable=False,
    )


# ══════════════════════════════════════════════════════════════════════════════
# LOCATION / FACILITY HIERARCHY
# ══════════════════════════════════════════════════════════════════════════════

class Location(PlenumBase):
    __tablename__ = "locations"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(100), nullable=False)          # site, building, floor, room, zone
    parent_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.locations.id", ondelete="SET NULL"),
    )
    level: Mapped[int | None] = mapped_column(Integer)
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(150))
    province: Mapped[str | None] = mapped_column(String(150))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    country: Mapped[str | None] = mapped_column(String(100))
    timezone: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


# ══════════════════════════════════════════════════════════════════════════════
# ASSETS
# ══════════════════════════════════════════════════════════════════════════════

class AssetCategory(PlenumBase):
    """Fiix: AssetCategory — now supports parent/child hierarchy (intParentID)."""
    __tablename__ = "asset_categories"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_asset_categories_org_name"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(                  # Fiix: intParentID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.asset_categories.id", ondelete="SET NULL"),
    )


class Asset(PlenumBase):
    """
    Fiix: Asset — fully expanded with all Fiix fields.

    New fields vs original:
      barcode, inventory_code, criticality, notes, parent_asset_id,
      is_online, is_site, aisle, row, bin_number, stock_location,
      charge_department_id, project_id, account_code,
      make (alias for manufacturer), model (alias for model_number).
    """
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("organization_id", "asset_code", name="uq_assets_org_asset_code"),
        UniqueConstraint("organization_id", "serial_number", name="uq_assets_org_serial_number"),
        CheckConstraint(
            "health_score IS NULL OR (health_score >= 0 AND health_score <= 100)",
            name="chk_assets_health_score",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.locations.id", ondelete="SET NULL"),
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.asset_categories.id", ondelete="SET NULL"),
    )
    # ── Core identity ──
    asset_name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_code: Mapped[str | None] = mapped_column(String(150))           # strCode
    serial_number: Mapped[str | None] = mapped_column(String(150))        # strSerialNumber
    barcode: Mapped[str | None] = mapped_column(String(255))              # Fiix: strBarcode
    inventory_code: Mapped[str | None] = mapped_column(String(150))       # Fiix: strInventoryCode
    # ── Make / model ──
    manufacturer: Mapped[str | None] = mapped_column(String(150))         # strMake (kept as manufacturer for compat)
    make: Mapped[str | None] = mapped_column(String(150))                 # Fiix: strMake (explicit alias)
    model_number: Mapped[str | None] = mapped_column(String(150))         # strModel (kept for compat)
    model: Mapped[str | None] = mapped_column(String(150))                # Fiix: strModel (explicit alias)
    # ── Dates ──
    installation_date: Mapped[date | None] = mapped_column(Date)
    warranty_expiry: Mapped[date | None] = mapped_column(Date)
    # ── Status / flags ──
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="active")
    is_online: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")   # Fiix: bolIsOnline
    is_site: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")    # Fiix: bolIsSite
    criticality: Mapped[str | None] = mapped_column(String(50))           # Fiix: strCriticality (High/Medium/Low)
    health_score: Mapped[int | None] = mapped_column(Integer)
    # ── Physical location within facility ──
    aisle: Mapped[str | None] = mapped_column(String(100))                # Fiix: strAisle
    row: Mapped[str | None] = mapped_column(String(100))                  # Fiix: strRow
    bin_number: Mapped[str | None] = mapped_column(String(100))           # Fiix: strBinNumber
    stock_location: Mapped[str | None] = mapped_column(String(255))       # Fiix: strStockLocation
    # ── Hierarchy ──
    parent_asset_id: Mapped[uuid.UUID | None] = mapped_column(            # Fiix: intAssetParentID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.assets.id", ondelete="SET NULL"),
    )
    # ── Cost / org links ──
    charge_department_id: Mapped[uuid.UUID | None] = mapped_column(       # Fiix: intChargeDepartmentID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.charge_departments.id", ondelete="SET NULL"),
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(                 # Fiix: intProjectID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.projects.id", ondelete="SET NULL"),
    )
    account_code: Mapped[str | None] = mapped_column(String(100))         # Fiix: intAccountID → stored as code
    # ── Additional ──
    notes: Mapped[str | None] = mapped_column(Text)                       # Fiix: strNotes
    qr_code: Mapped[str | None] = mapped_column(String(255))
    timezone: Mapped[str | None] = mapped_column(String(100))             # Fiix: strTimezone
    raw_metadata: Mapped[dict | None] = mapped_column(JSONB)              # unmapped fields preserved
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class AssetDocument(PlenumBase):
    __tablename__ = "asset_documents"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.assets.id", ondelete="CASCADE"), nullable=False,
    )
    file_url: Mapped[str] = mapped_column(Text, nullable=False)
    document_type: Mapped[str | None] = mapped_column(String(100))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class AssetReading(PlenumBase):
    """Fiix: MeterReading — enriched with unit_id FK and submitted_by."""
    __tablename__ = "asset_readings"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.assets.id", ondelete="CASCADE"), nullable=False,
    )
    reading_type: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(50))
    unit_id: Mapped[uuid.UUID | None] = mapped_column(                    # Fiix: MeterReadingUnit FK
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.meter_reading_units.id", ondelete="SET NULL"),
    )
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(               # Fiix: intSubmittedByUserID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class AssetWarranty(PlenumBase):
    """Fiix: Warranty — dedicated warranty records per asset."""
    __tablename__ = "asset_warranties"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.assets.id", ondelete="CASCADE"), nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(String(255))
    start_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    coverage_notes: Mapped[str | None] = mapped_column(Text)
    document_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


# ══════════════════════════════════════════════════════════════════════════════
# PEOPLE — TECHNICIANS / CERTIFICATIONS
# ══════════════════════════════════════════════════════════════════════════════

class Technician(PlenumBase):
    __tablename__ = "technicians"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_technicians_user"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False,
    )
    base_location: Mapped[str | None] = mapped_column(String(255))
    availability_status: Mapped[str | None] = mapped_column(String(50))
    performance_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class TechnicianSkill(PlenumBase):
    __tablename__ = "technician_skills"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    technician_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.technicians.id", ondelete="CASCADE"), nullable=False,
    )
    skill_name: Mapped[str] = mapped_column(String(150), nullable=False)
    skill_level: Mapped[str | None] = mapped_column(String(50))


class UserCertification(PlenumBase):
    """Fiix: UserCertification — technician licences and certs with expiry."""
    __tablename__ = "user_certifications"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    issued_by: Mapped[str | None] = mapped_column(String(255))
    issued_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    document_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


# ══════════════════════════════════════════════════════════════════════════════
# VENDORS / BUSINESSES
# ══════════════════════════════════════════════════════════════════════════════

class Vendor(PlenumBase):
    """Fiix: Business — enriched with code, contact details, address."""
    __tablename__ = "vendors"
    __table_args__ = (
        UniqueConstraint("organization_id", "vendor_code", name="uq_vendors_org_code"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    vendor_code: Mapped[str | None] = mapped_column(String(100))          # Fiix: strCode
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(150))                 # Fiix: strCity
    province: Mapped[str | None] = mapped_column(String(150))             # Fiix: strProvince
    postal_code: Mapped[str | None] = mapped_column(String(20))           # Fiix: strPostalCode
    country: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(50))                 # Fiix: strPhone
    fax: Mapped[str | None] = mapped_column(String(50))                   # Fiix: strFax
    website: Mapped[str | None] = mapped_column(String(500))              # Fiix: strWebsite
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class VendorContact(PlenumBase):
    __tablename__ = "vendor_contacts"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.vendors.id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    designation: Mapped[str | None] = mapped_column(String(150))


class VendorContract(PlenumBase):
    __tablename__ = "vendor_contracts"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.vendors.id", ondelete="CASCADE"), nullable=False,
    )
    contract_name: Mapped[str] = mapped_column(String(255), nullable=False)
    contract_start: Mapped[date | None] = mapped_column(Date)
    contract_end: Mapped[date | None] = mapped_column(Date)
    contract_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    sla_terms: Mapped[str | None] = mapped_column(Text)
    contract_document: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class SLAPolicy(PlenumBase):
    __tablename__ = "sla_policies"
    __table_args__ = (
        CheckConstraint("response_time_minutes >= 0", name="chk_sla_response_time"),
        CheckConstraint("resolution_time_minutes >= 0", name="chk_sla_resolution_time"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    priority: Mapped[str] = mapped_column(String(50), nullable=False)
    response_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False)


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULED MAINTENANCE (PM PLANS)
# ══════════════════════════════════════════════════════════════════════════════

class MaintenancePlan(PlenumBase):
    """
    Fiix: ScheduledMaintenance — expanded with code, priority_id,
    maintenance_type_id, project_id, charge_department_id.
    """
    __tablename__ = "maintenance_plans"
    __table_args__ = (
        CheckConstraint("frequency_value > 0", name="chk_maintenance_frequency_value"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.assets.id", ondelete="CASCADE"), nullable=False,
    )
    # ── Identity ──
    sm_code: Mapped[str | None] = mapped_column(String(150))              # Fiix: strCode
    description: Mapped[str | None] = mapped_column(Text)                 # Fiix: strDescription
    # ── Classification ──
    maintenance_type: Mapped[str] = mapped_column(String(100), nullable=False)
    maintenance_type_id: Mapped[uuid.UUID | None] = mapped_column(        # Fiix: intMaintenanceTypeID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.maintenance_types.id", ondelete="SET NULL"),
    )
    priority_id: Mapped[uuid.UUID | None] = mapped_column(                # Fiix: intPriorityID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.priorities.id", ondelete="SET NULL"),
    )
    task_group_id: Mapped[uuid.UUID | None] = mapped_column(              # Fiix: TaskGroup
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.task_groups.id", ondelete="SET NULL"),
    )
    # ── Schedule ──
    frequency_type: Mapped[str] = mapped_column(String(50), nullable=False)  # time, meter
    frequency_value: Mapped[int] = mapped_column(Integer, nullable=False)
    next_due_date: Mapped[date | None] = mapped_column(Date)
    # ── Status ──
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="active")
    # ── Cost / org links ──
    project_id: Mapped[uuid.UUID | None] = mapped_column(                 # Fiix: intProjectID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.projects.id", ondelete="SET NULL"),
    )
    charge_department_id: Mapped[uuid.UUID | None] = mapped_column(       # Fiix: intChargeDepartmentID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.charge_departments.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class ScheduledTask(PlenumBase):
    """Fiix: ScheduledTask — individual task within a TaskGroup or MaintenancePlan."""
    __tablename__ = "scheduled_tasks"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.task_groups.id", ondelete="CASCADE"),
    )
    maintenance_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.maintenance_plans.id", ondelete="CASCADE"),
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))  # Fiix: dblTimeEstimatedHours
    task_type: Mapped[int | None] = mapped_column(Integer)                # Fiix: intTaskType (0=text, 1=numeric, etc.)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")  # Fiix: intOrder
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.assets.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


# ── SM junction tables ─────────────────────────────────────────────────────────

class ScheduledMaintenanceAsset(PlenumBase):
    """Fiix: ScheduledMaintenanceAsset — one SM plan can cover multiple assets."""
    __tablename__ = "scheduled_maintenance_assets"
    __table_args__ = (
        UniqueConstraint("maintenance_plan_id", "asset_id", name="uq_sm_asset"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    maintenance_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.maintenance_plans.id", ondelete="CASCADE"), nullable=False,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.assets.id", ondelete="CASCADE"), nullable=False,
    )


class ScheduledMaintenanceUser(PlenumBase):
    """Fiix: ScheduledMaintenanceUser — assigned technicians for a PM plan."""
    __tablename__ = "scheduled_maintenance_users"
    __table_args__ = (
        UniqueConstraint("maintenance_plan_id", "user_id", name="uq_sm_user"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    maintenance_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.maintenance_plans.id", ondelete="CASCADE"), nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False,
    )


class ScheduledMaintenancePart(PlenumBase):
    """Fiix: ScheduledMaintenancePart — parts required for a PM plan."""
    __tablename__ = "scheduled_maintenance_parts"
    __table_args__ = (
        UniqueConstraint("maintenance_plan_id", "part_id", name="uq_sm_part"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    maintenance_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.maintenance_plans.id", ondelete="CASCADE"), nullable=False,
    )
    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.spare_parts.id", ondelete="CASCADE"), nullable=False,
    )
    quantity_required: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


# ══════════════════════════════════════════════════════════════════════════════
# WORK ORDERS
# ══════════════════════════════════════════════════════════════════════════════

class WorkOrder(PlenumBase):
    """
    Fiix: WorkOrder — fully expanded.

    New fields vs original:
      wo_code, problem, solution, completion_notes,
      maintenance_type_id, priority_id (FK), status_id (FK),
      requested_by_id, completed_by_id,
      estimated_hours, actual_hours,
      charge_department_id, project_id, task_group_id.
    """
    __tablename__ = "work_orders"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # ── Primary asset/location (kept from original) ──
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.assets.id", ondelete="SET NULL"),
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.locations.id", ondelete="SET NULL"),
    )
    # ── Identity ──
    wo_code: Mapped[str | None] = mapped_column(String(150))              # Fiix: strCode e.g. "WO-2024-0142"
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    problem: Mapped[str | None] = mapped_column(Text)                     # Fiix: strProblem
    solution: Mapped[str | None] = mapped_column(Text)                    # Fiix: strSolution
    completion_notes: Mapped[str | None] = mapped_column(Text)            # Fiix: strCompletionNotes
    # ── Classification (string kept for backward compat + FK added) ──
    priority: Mapped[str] = mapped_column(String(50), nullable=False, server_default="medium")
    priority_id: Mapped[uuid.UUID | None] = mapped_column(                # Fiix: intPriorityID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.priorities.id", ondelete="SET NULL"),
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="open")
    status_id: Mapped[uuid.UUID | None] = mapped_column(                  # Fiix: intWorkOrderStatusID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.work_order_statuses.id", ondelete="SET NULL"),
    )
    maintenance_type: Mapped[str | None] = mapped_column(String(100))
    maintenance_type_id: Mapped[uuid.UUID | None] = mapped_column(        # Fiix: intMaintenanceTypeID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.maintenance_types.id", ondelete="SET NULL"),
    )
    # ── People ──
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(            # Fiix: intRequestedByUserID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    completed_by_id: Mapped[uuid.UUID | None] = mapped_column(            # Fiix: intCompletedByUserID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    assigned_technician: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.technicians.id", ondelete="SET NULL"),
    )
    assigned_vendor: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.vendors.id", ondelete="SET NULL"),
    )
    # ── Time tracking ──
    estimated_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))  # Fiix: dblTimeEstimatedHours
    actual_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))     # Fiix: dblTimeSpentHours
    # ── SLA ──
    sla_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.sla_policies.id", ondelete="SET NULL"),
    )
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime)
    # ── Cost / org links ──
    charge_department_id: Mapped[uuid.UUID | None] = mapped_column(       # Fiix: intChargeDepartmentID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.charge_departments.id", ondelete="SET NULL"),
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(                 # Fiix: intProjectID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.projects.id", ondelete="SET NULL"),
    )
    task_group_id: Mapped[uuid.UUID | None] = mapped_column(              # applied task group
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.task_groups.id", ondelete="SET NULL"),
    )
    # ── Linked SM plan ──
    maintenance_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.maintenance_plans.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    # ── FR-039: re-ingestion conflict detection ──
    conflict_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    conflict_payload: Mapped[dict | None] = mapped_column(JSONB)


class WorkOrderAsset(PlenumBase):
    """Fiix: WorkOrderAsset — a WO can span multiple assets."""
    __tablename__ = "work_order_assets"
    __table_args__ = (
        UniqueConstraint("work_order_id", "asset_id", name="uq_wo_asset"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.work_orders.id", ondelete="CASCADE"), nullable=False,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.assets.id", ondelete="CASCADE"), nullable=False,
    )


class WorkOrderUser(PlenumBase):
    """Fiix: WorkOrderUser — multiple technicians assigned to a single WO."""
    __tablename__ = "work_order_users"
    __table_args__ = (
        UniqueConstraint("work_order_id", "user_id", name="uq_wo_user"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.work_orders.id", ondelete="CASCADE"), nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False,
    )
    hours_spent: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))   # Fiix: dblTimeSpentHours per user


class WorkOrderTask(PlenumBase):
    """Fiix: WorkOrderTask — enriched with time tracking and task group link."""
    __tablename__ = "work_order_tasks"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.work_orders.id", ondelete="CASCADE"), nullable=False,
    )
    task_group_id: Mapped[uuid.UUID | None] = mapped_column(              # Fiix: intTaskGroupControlID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.task_groups.id", ondelete="SET NULL"),
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    task_type: Mapped[int | None] = mapped_column(Integer)                # Fiix: intTaskType
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")  # Fiix: intOrder
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.technicians.id", ondelete="SET NULL"),
    )
    completed_by: Mapped[uuid.UUID | None] = mapped_column(               # Fiix: intCompletedByUserID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    estimated_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))  # Fiix: dblTimeEstimatedHours
    actual_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))     # Fiix: dblTimeSpentHours
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class WorkOrderComment(PlenumBase):
    __tablename__ = "work_order_comments"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.work_orders.id", ondelete="CASCADE"), nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class WorkOrderAttachment(PlenumBase):
    __tablename__ = "work_order_attachments"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.work_orders.id", ondelete="CASCADE"), nullable=False,
    )
    file_url: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class WorkOrderHistory(PlenumBase):
    __tablename__ = "work_order_history"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.work_orders.id", ondelete="CASCADE"), nullable=False,
    )
    old_status: Mapped[str | None] = mapped_column(String(50))
    new_status: Mapped[str | None] = mapped_column(String(50))
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    notes: Mapped[str | None] = mapped_column(Text)


class MaintenanceHistory(PlenumBase):
    __tablename__ = "maintenance_history"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.assets.id", ondelete="CASCADE"), nullable=False,
    )
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.work_orders.id", ondelete="SET NULL"),
    )
    performed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    performed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    notes: Mapped[str | None] = mapped_column(Text)


# ══════════════════════════════════════════════════════════════════════════════
# INVENTORY / SPARE PARTS
# ══════════════════════════════════════════════════════════════════════════════

class SparePart(PlenumBase):
    """
    Fiix: Stock — enriched with max_quantity, storage location, supplier, BOM group.
    """
    __tablename__ = "spare_parts"
    __table_args__ = (
        UniqueConstraint("organization_id", "part_code", name="uq_spare_parts_org_part_code"),
        CheckConstraint("stock_quantity >= 0", name="chk_spare_parts_stock_quantity"),
        CheckConstraint("reorder_level >= 0", name="chk_spare_parts_reorder_level"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    part_name: Mapped[str] = mapped_column(String(255), nullable=False)
    part_code: Mapped[str | None] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")  # Fiix: qtyOnHand
    reorder_level: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")   # Fiix: qtyMinQty
    max_quantity: Mapped[int | None] = mapped_column(Integer)                                  # Fiix: qtyMaxQty
    unit_of_measure: Mapped[str | None] = mapped_column(String(50))
    # ── Storage location ──
    aisle: Mapped[str | None] = mapped_column(String(100))                # Fiix: strAisle
    row: Mapped[str | None] = mapped_column(String(100))                  # Fiix: strRow
    bin_number: Mapped[str | None] = mapped_column(String(100))
    # ── Links ──
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(                # preferred supplier
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.vendors.id", ondelete="SET NULL"),
    )
    bom_group_id: Mapped[uuid.UUID | None] = mapped_column(               # Fiix: BOMGroup
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.bom_groups.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class InventoryTransaction(PlenumBase):
    __tablename__ = "inventory_transactions"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.spare_parts.id", ondelete="CASCADE"), nullable=False,
    )
    transaction_type: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))    # Fiix: dblTotalCost
    reference_type: Mapped[str | None] = mapped_column(String(100))
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class WorkOrderPart(PlenumBase):
    __tablename__ = "work_order_parts"
    __table_args__ = (
        CheckConstraint("quantity_used > 0", name="chk_work_order_parts_quantity_used"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.work_orders.id", ondelete="CASCADE"), nullable=False,
    )
    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.spare_parts.id", ondelete="CASCADE"), nullable=False,
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(                   # Fiix: intAssetID on WO part
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.assets.id", ondelete="SET NULL"),
    )
    quantity_used: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))


# ══════════════════════════════════════════════════════════════════════════════
# PROCUREMENT — PURCHASE ORDERS
# ══════════════════════════════════════════════════════════════════════════════

class PurchaseOrder(PlenumBase):
    """Fiix: PurchaseOrder — procurement from suppliers."""
    __tablename__ = "purchase_orders"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    po_code: Mapped[str | None] = mapped_column(String(150))              # Fiix: intCode → human PO number
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(                # Fiix: intSupplierID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.vendors.id", ondelete="SET NULL"),
    )
    status_id: Mapped[uuid.UUID | None] = mapped_column(                  # Fiix: intPurchaseOrderStatusID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.purchase_order_statuses.id", ondelete="SET NULL"),
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="draft")
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.locations.id", ondelete="SET NULL"),
    )
    charge_department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.charge_departments.id", ondelete="SET NULL"),
    )
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))      # Fiix: dblSubtotal
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())  # Fiix: dtmDateCreated
    received_at: Mapped[datetime | None] = mapped_column(DateTime)        # Fiix: dtmDateReceived
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class PurchaseOrderLineItem(PlenumBase):
    """Fiix: PurchaseOrderLineItem."""
    __tablename__ = "purchase_order_line_items"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.purchase_orders.id", ondelete="CASCADE"), nullable=False,
    )
    part_id: Mapped[uuid.UUID | None] = mapped_column(                    # Fiix: intStockID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.spare_parts.id", ondelete="SET NULL"),
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(                   # Fiix: intAssetID
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.assets.id", ondelete="SET NULL"),
    )
    description: Mapped[str | None] = mapped_column(Text)                 # Fiix: strDescription
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))    # Fiix: dblUnitPrice
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))   # Fiix: dblTotalPrice
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))       # Fiix: dblTaxRate


# ══════════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS + AUDIT  (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

class Notification(PlenumBase):
    __tablename__ = "notifications"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str | None] = mapped_column(String(100))
    entity_type: Mapped[str | None] = mapped_column(String(100))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class AuditLog(PlenumBase):
    __tablename__ = "audit_logs"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    action: Mapped[str] = mapped_column(String(150), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(100))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    metadata_: Mapped[str | None] = mapped_column("metadata", Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


# ══════════════════════════════════════════════════════════════════════════════
# HIGH-IMPACT MISSING FIIX TABLES
# Added after initial Fiix expansion — selected for wide cross-system usage.
# ══════════════════════════════════════════════════════════════════════════════

class ScheduleTrigger(PlenumBase):
    """
    Fiix: ScheduleTrigger — one SM plan can have MULTIPLE triggers.
    Replaces the flat frequency_type/frequency_value on maintenance_plans,
    enabling e.g. "every 30 days OR every 500 hours" on a single plan.
    """
    __tablename__ = "schedule_triggers"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    maintenance_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.maintenance_plans.id", ondelete="CASCADE"), nullable=False,
    )
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False)   # 'time' | 'meter'
    # Time-based
    interval_value: Mapped[int | None] = mapped_column(Integer)             # e.g. 30
    interval_unit: Mapped[str | None] = mapped_column(String(20))           # 'days'|'weeks'|'months'|'years'
    # Meter-based
    meter_interval: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))  # e.g. 500.0
    meter_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.meter_reading_units.id", ondelete="SET NULL"),
    )
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_due_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class BOMGroupPart(PlenumBase):
    """
    Fiix: BOMGroupPart — which spare parts belong to a BOM group.
    BOMGroup is an empty shell without this junction table.
    """
    __tablename__ = "bom_group_parts"
    __table_args__ = (
        UniqueConstraint("bom_group_id", "part_id", name="uq_bom_group_parts"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bom_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.bom_groups.id", ondelete="CASCADE"), nullable=False,
    )
    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.spare_parts.id", ondelete="CASCADE"), nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class MiscCostType(PlenumBase):
    """Fiix: MiscCostType — categories for miscellaneous WO costs (subcontract, travel, crane, etc.)."""
    __tablename__ = "misc_cost_types"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_misc_cost_types_org_name"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class MiscCost(PlenumBase):
    """
    Fiix: MiscCost — miscellaneous costs on a work order beyond parts and labour.
    Required for complete WO cost rollups.
    """
    __tablename__ = "misc_costs"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.work_orders.id", ondelete="CASCADE"), nullable=False,
    )
    misc_cost_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.misc_cost_types.id", ondelete="SET NULL"),
    )
    description: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class AssetOfflineLog(PlenumBase):
    """
    Fiix: AssetOfflineTracker — records each downtime event for an asset.
    Enables MTTR, MTBF, and availability calculations.
    """
    __tablename__ = "asset_offline_log"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.assets.id", ondelete="CASCADE"), nullable=False,
    )
    offline_reason: Mapped[str | None] = mapped_column(String(255))         # free text or lookup value
    online_reason: Mapped[str | None] = mapped_column(String(255))
    went_offline_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    came_online_at: Mapped[datetime | None] = mapped_column(DateTime)
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.work_orders.id", ondelete="SET NULL"),
    )
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    notes: Mapped[str | None] = mapped_column(Text)


class Receipt(PlenumBase):
    """
    Fiix: Receipt — goods received against a purchase order.
    Completes the PO lifecycle: raised → approved → received.
    """
    __tablename__ = "receipts"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.purchase_orders.id", ondelete="CASCADE"), nullable=False,
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="pending")  # pending|partial|complete|cancelled
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.locations.id", ondelete="SET NULL"),
    )
    received_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    received_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class ReceiptLineItem(PlenumBase):
    """Fiix: ReceiptLineItem — actual quantity received per PO line item."""
    __tablename__ = "receipt_line_items"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.receipts.id", ondelete="CASCADE"), nullable=False,
    )
    po_line_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.purchase_order_line_items.id", ondelete="SET NULL"),
    )
    part_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.spare_parts.id", ondelete="SET NULL"),
    )
    quantity_ordered: Mapped[int | None] = mapped_column(Integer)
    quantity_received: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    notes: Mapped[str | None] = mapped_column(Text)


# ── Root Cause Analysis ────────────────────────────────────────────────────────

class RCAProblem(PlenumBase):
    """Fiix: RCAProblem — what failed (e.g. 'Bearing failure', 'Electrical fault')."""
    __tablename__ = "rca_problems"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"), nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class RCACause(PlenumBase):
    """Fiix: RCACause — why it failed (e.g. 'Lack of lubrication', 'Overload')."""
    __tablename__ = "rca_causes"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"), nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class RCAAction(PlenumBase):
    """Fiix: RCAAction — corrective/preventive action taken or recommended."""
    __tablename__ = "rca_actions"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"), nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class RCAGrouping(PlenumBase):
    """
    Fiix: RCAGrouping — ties a closed WO to its root cause analysis.
    Links a work order to one problem + many causes + many corrective actions.
    """
    __tablename__ = "rca_groupings"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.work_orders.id", ondelete="CASCADE"), nullable=False,
    )
    problem_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.rca_problems.id", ondelete="SET NULL"),
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class RCAGroupingCause(PlenumBase):
    """Fiix: RCAGroupingCause — many causes can apply to one RCA grouping."""
    __tablename__ = "rca_grouping_causes"
    __table_args__ = (
        UniqueConstraint("rca_grouping_id", "rca_cause_id", name="uq_rca_grouping_cause"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rca_grouping_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.rca_groupings.id", ondelete="CASCADE"), nullable=False,
    )
    rca_cause_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.rca_causes.id", ondelete="CASCADE"), nullable=False,
    )


class RCAGroupingAction(PlenumBase):
    """Fiix: RCAGroupingAction — corrective actions assigned against an RCA grouping."""
    __tablename__ = "rca_grouping_actions"
    __table_args__ = (
        UniqueConstraint("rca_grouping_id", "rca_action_id", name="uq_rca_grouping_action"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rca_grouping_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.rca_groupings.id", ondelete="CASCADE"), nullable=False,
    )
    rca_action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.rca_actions.id", ondelete="CASCADE"), nullable=False,
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class File(PlenumBase):
    """
    Fiix: File — universal file/attachment record.
    Replaces the siloed asset_documents and work_order_attachments tables
    with a single polymorphic table covering any entity type.
    """
    __tablename__ = "files"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    blob_url: Mapped[str | None] = mapped_column(Text)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(150))
    # Polymorphic link — entity_type + entity_id points to any table
    entity_type: Mapped[str | None] = mapped_column(String(100))            # 'asset'|'work_order'|'work_order_task'|'inspection'|etc.
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
    )
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
