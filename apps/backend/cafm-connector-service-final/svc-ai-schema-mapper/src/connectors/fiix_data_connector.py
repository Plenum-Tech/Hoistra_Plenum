"""Fiix full data connector — fetches ALL records from the Fiix API with pagination.

Extends FiixSchemaConnector with:
  - OBJECT_TABLE_MAP   Fiix object name → plenum_cafm table name
  - FK_OBJECT_MAP      Fiix int*ID field → Fiix object name (for UUID resolution)
  - fetch_object_paginated()  yields batches of records for one object
  - fetch_all_data()   fetches every object in OBJECT_TABLE_MAP

UUID strategy: every Fiix integer ID is converted to a deterministic UUID via
  uuid5(NAMESPACE_OID, "fiix:{ObjectName}:{fiix_integer_id}")
This makes the ingestion idempotent — re-running produces the same UUIDs.
"""

import asyncio
import uuid
from typing import Any, Dict, Generator, List, Optional

from cafm_shared.logging import get_logger

from .fiix_connector import FiixAPI, FiixSchemaConnector, FiixError

logger = get_logger(__name__)

_NS = uuid.NAMESPACE_OID   # stable namespace for deterministic UUIDs


def fiix_uuid(object_name: str, fiix_id: Any) -> str:
    """Generate a deterministic UUID for a Fiix record."""
    return str(uuid.uuid5(_NS, f"fiix:{object_name}:{fiix_id}"))


# ── Object → table mapping ────────────────────────────────────────────────────
# Only objects that have a corresponding plenum_cafm table are listed here.
# Lookup-only objects (Priority, Country, Currency, etc.) are omitted.

OBJECT_TABLE_MAP: Dict[str, str] = {
    "Asset":                    "assets",
    "AssetCategory":            "asset_categories",
    "WorkOrder":                "work_orders",
    "WorkOrderTask":            "work_order_tasks",
    "WorkOrderPart":            "work_order_parts",
    "ScheduledMaintenance":     "scheduled_maintenance",
    "ScheduledMaintenancePart": "scheduled_maintenance_parts",  # may not exist; write node skips
    "ScheduledTask":            "work_order_tasks",             # merged into tasks table
    "ScheduleTrigger":          "schedule_triggers",
    "User":                     "technicians",
    "Business":                 "vendors",
    "Stock":                    "spare_parts",
    "InventoryTransaction":     "inventory_transactions",
    "PurchaseOrder":            "purchase_orders",
    "PurchaseOrderLineItem":    "purchase_order_lines",
    "Receipt":                  "receipts",
    "ReceiptLineItem":          "receipt_line_items",
    "MeterReading":             "meter_readings",
    "BOMGroup":                 "bom_groups",
    "BOMGroupPart":             "bom_group_parts",
    "MiscCost":                 "misc_costs",
    "MiscCostType":             "misc_cost_types",
    "Project":                  "projects",                    # may not exist; write node skips
    "AssetOfflineTracker":      "asset_offline_log",
    "RCAProblem":               "rca_problems",
    "RCAAction":                "rca_actions",
    "RCACause":                 "rca_causes",
    "RCAGrouping":              "rca_groupings",
    "RCAGroupingAction":        "rca_grouping_actions",
    "RCAGroupingCause":         "rca_grouping_causes",
    "TaskGroup":                "task_groups",
    "File":                     "files",
    "UserCertification":        "technician_skills",
    "Warranty":                 "asset_documents",             # stored as document type
    "MeterReadingUnit":         "asset_meters",                # unit reference
}

# ── FK field → Fiix object (for UUID resolution of int*ID foreign keys) ───────
FK_OBJECT_MAP: Dict[str, str] = {
    "intAssetID":                       "Asset",
    "intAssetParentID":                 "Asset",
    "intAssetLocationID":               "Asset",
    "intSiteID":                        "Asset",
    "intWorkOrderID":                   "WorkOrder",
    "intScheduledMaintenanceID":        "ScheduledMaintenance",
    "intStockID":                       "Stock",
    "intUserID":                        "User",
    "intSubmittedByUserID":             "User",
    "intCompletedByUserID":             "User",
    "intRequestedByUserID":             "User",
    "intAssignedToUserID":              "User",
    "intPurchaseOrderID":               "PurchaseOrder",
    "intPurchaseOrderLineItemID":       "PurchaseOrderLineItem",
    "intSupplierID":                    "Business",
    "intCategoryID":                    "AssetCategory",
    "intParentID":                      "AssetCategory",
    "intMiscCostTypeID":                "MiscCostType",
    "intBOMGroupID":                    "BOMGroup",
    "intRCAGroupingID":                 "RCAGrouping",
    "intRCAActionID":                   "RCAAction",
    "intRCACauseID":                    "RCACause",
    "intRCAProblemID":                  "RCAProblem",
    "intTaskGroupControlID":            "TaskGroup",
    "intReceiptID":                     "Receipt",
    "intFileID":                        "File",
    "intAssetEventTypeID":              "AssetEventType",
    "intBusinessID":                    "Business",
    "intBusinessRoleTypeID":            "BusinessRole",
    "intBillingTermID":                 "BillingTerm",
    "intMoveStatusID":                  "MoveStatus",
    "intMoveID":                        "Move",
    "intMoveBackID":                    "MoveBack",
    "intWorkOrderTaskID":               "WorkOrderTask",
    "intCycleCountID":                  "CycleCount",
    "intStockTxTypeID":                 "StockTxType",
}

# ── Fetch page size ────────────────────────────────────────────────────────────
_PAGE_SIZE = 1000


class FiixDataConnector(FiixSchemaConnector):
    """Extends FiixSchemaConnector with full data fetching capabilities."""

    def fetch_object_page(
        self,
        object_name: str,
        start_index: int = 0,
    ) -> tuple[list[dict], bool]:
        """
        Fetch one page of records for a Fiix object.

        Returns (records, has_more) where has_more is True when a full page
        was returned (meaning there may be more pages).
        """
        fields_str = self.OBJECT_FIELDS.get(object_name, "id")
        result = self.api.find(
            object_name,
            fields=fields_str,
            start_index=start_index,
            max_objects=_PAGE_SIZE,
        )
        records: list[dict] = result.get("objects", [])
        has_more = len(records) == _PAGE_SIZE
        return records, has_more

    def fetch_object_all(self, object_name: str) -> list[dict]:
        """
        Fetch ALL records for a single Fiix object using pagination.

        Handles objects with more than _PAGE_SIZE records by iterating
        through pages until a partial page is returned.
        """
        all_records: list[dict] = []
        start_index = 0

        while True:
            try:
                records, has_more = self.fetch_object_page(object_name, start_index)
            except FiixError as exc:
                logger.warning(
                    f"[FiixDataConnector] Skipping {object_name} at offset {start_index}: {exc}"
                )
                break

            all_records.extend(records)

            if not has_more:
                break

            start_index += _PAGE_SIZE
            logger.debug(
                f"[FiixDataConnector] {object_name}: fetched {len(all_records)} so far, "
                f"fetching next page from {start_index}"
            )

        return all_records

    def fetch_all_data(
        self,
        objects: Optional[List[str]] = None,
    ) -> tuple[dict[str, list[dict]], dict[str, int], list[str]]:
        """
        Fetch data for all objects in OBJECT_TABLE_MAP (or the given subset).

        Returns:
            fetched:  {object_name: [records]}
            stats:    {object_name: record_count}
            errors:   list of non-fatal error messages
        """
        target_objects = objects or list(OBJECT_TABLE_MAP.keys())
        fetched: dict[str, list[dict]] = {}
        stats: dict[str, int] = {}
        errors: list[str] = []

        # Enforce dependency order so parent objects are fetched before children.
        # Assets must come before WorkOrders (WOs reference asset codes).
        ordered = _dependency_order(target_objects)

        for obj_name in ordered:
            logger.info(f"[FiixDataConnector] Fetching {obj_name}...")
            try:
                records = self.fetch_object_all(obj_name)
                fetched[obj_name] = records
                stats[obj_name] = len(records)
                logger.info(f"[FiixDataConnector]   ✓ {obj_name}: {len(records)} records")
            except Exception as exc:
                msg = f"{obj_name}: {exc}"
                logger.error(f"[FiixDataConnector]   ✗ {msg}")
                errors.append(msg)
                fetched[obj_name] = []
                stats[obj_name] = 0

        total = sum(stats.values())
        logger.info(
            f"[FiixDataConnector] Fetch complete: "
            f"{total} total records across {len(fetched)} objects, "
            f"{len(errors)} errors"
        )
        return fetched, stats, errors


# ── Dependency order ──────────────────────────────────────────────────────────

_FETCH_ORDER = [
    # Lookups / parents first
    "AssetCategory",
    "MiscCostType",
    "BOMGroup",
    "TaskGroup",
    "User",
    "Business",
    "MeterReadingUnit",
    # Core entities
    "Asset",
    "Stock",
    "ScheduledMaintenance",
    "ScheduleTrigger",
    "ScheduledMaintenancePart",
    "WorkOrder",
    "WorkOrderTask",
    "WorkOrderPart",
    "ScheduledTask",
    # Inventory
    "InventoryTransaction",
    "PurchaseOrder",
    "PurchaseOrderLineItem",
    "Receipt",
    "ReceiptLineItem",
    # Measurements & offline
    "MeterReading",
    "AssetOfflineTracker",
    # Maintenance extras
    "BOMGroupPart",
    "MiscCost",
    # RCA cluster
    "RCAProblem",
    "RCACause",
    "RCAAction",
    "RCAGrouping",
    "RCAGroupingAction",
    "RCAGroupingCause",
    # Files & misc
    "File",
    "UserCertification",
    "Warranty",
]


def _dependency_order(objects: list[str]) -> list[str]:
    """Return objects sorted by _FETCH_ORDER; unknowns appended at end."""
    order_map = {o: i for i, o in enumerate(_FETCH_ORDER)}
    known = sorted([o for o in objects if o in order_map], key=lambda x: order_map[x])
    unknown = [o for o in objects if o not in order_map]
    return known + unknown
