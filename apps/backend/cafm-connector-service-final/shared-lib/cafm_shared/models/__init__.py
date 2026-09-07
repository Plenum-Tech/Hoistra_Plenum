"""
cafm_shared/models/__init__.py

Re-exports all 29 CAFM ORM models from cafm-connector-service.

Import all models from here. Never import directly from
cafm_connector.models.plenum_cafm in new services.
"""

from __future__ import annotations

from cafm_connector.models.plenum_cafm import (
    Asset,
    AssetCategory,
    AssetDocument,
    AssetReading,
    AuditLog,
    InventoryTransaction,
    Location,
    MaintenanceHistory,
    MaintenancePlan,
    Notification,
    Organization,
    Permission,
    PlenumBase,
    Role,
    RolePermission,
    SLAPolicy,
    SparePart,
    Technician,
    TechnicianSkill,
    User,
    UserRole,
    Vendor,
    VendorContact,
    VendorContract,
    WorkOrder,
    WorkOrderAttachment,
    WorkOrderComment,
    WorkOrderHistory,
    WorkOrderPart,
    WorkOrderTask,
)

__all__ = [
    "PlenumBase",
    # Auth / Org
    "Organization",
    "User",
    "Role",
    "Permission",
    "UserRole",
    "RolePermission",
    # Facilities
    "Location",
    "AssetCategory",
    "Asset",
    "AssetDocument",
    "AssetReading",
    # Maintenance
    "MaintenancePlan",
    "Technician",
    "TechnicianSkill",
    "MaintenanceHistory",
    # Vendors / SLA
    "Vendor",
    "VendorContact",
    "VendorContract",
    "SLAPolicy",
    # Work Orders
    "WorkOrder",
    "WorkOrderTask",
    "WorkOrderComment",
    "WorkOrderAttachment",
    "WorkOrderHistory",
    "WorkOrderPart",
    # Inventory
    "SparePart",
    "InventoryTransaction",
    # System
    "Notification",
    "AuditLog",
]
