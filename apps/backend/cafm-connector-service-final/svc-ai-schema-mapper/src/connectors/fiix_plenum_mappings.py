"""Context-aware Fiix field → plenum_cafm column mappings.

Design principles (Fiix migration / schema-mapping flow):
- **Source schema**: Fiix API field names are preserved as-is (strCode, intAssetID, …).
- **Target schema**: plenum_cafm column names from the live internal model.
- **Duplicates**: The same Fiix field name on different objects keeps the same source
  name; the plenum target may differ per object (e.g. Asset.strCode → asset_code,
  WorkOrder.strCode → wo_code).
- **Uniques**: Fields that only appear on one object or map globally use GLOBAL_FIIX_TO_PLENUM.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

# (Fiix object, Fiix field) → plenum_cafm column — overrides GLOBAL for ambiguous names
OBJECT_FIELD_TO_PLENUM: Dict[Tuple[str, str], str] = {
    # ── strCode (code column differs per entity) ──────────────────────────
    ("Asset", "strCode"): "asset_code",
    ("WorkOrder", "strCode"): "wo_code",
    ("ScheduledMaintenance", "strCode"): "sm_code",
    ("Business", "strCode"): "vendor_code",
    ("ChargeDepartment", "strCode"): "code",
    ("Account", "strCode"): "code",
    ("BOMGroup", "strCode"): "name",
    # ── strName ───────────────────────────────────────────────────────────
    ("Asset", "strName"): "asset_name",
    ("AssetCategory", "strName"): "name",
    ("Business", "strName"): "name",
    ("BusinessClassification", "strName"): "name",
    ("BusinessGroup", "strName"): "name",
    ("BillingTerm", "strName"): "name",
    ("Country", "strName"): "name",
    ("Currency", "strName"): "name",
    ("MaintenanceType", "strName"): "name",
    ("MeterReadingUnit", "strName"): "name",
    ("MiscCostType", "strName"): "name",
    ("MoveStatus", "strName"): "name",
    ("Priority", "strName"): "name",
    ("Project", "strName"): "name",
    ("PurchaseOrderStatus", "strName"): "name",
    ("RFQStatus", "strName"): "name",
    ("ReceiptStatus", "strName"): "name",
    ("StockTxType", "strName"): "name",
    ("TaskGroup", "strName"): "name",
    ("WorkOrderStatus", "strName"): "name",
    ("File", "strName"): "name",
    ("UserCertification", "strName"): "name",
    ("ReasonToSetAssetOffline", "strName"): "name",
    ("ReasonToSetAssetOnline", "strName"): "name",
    # ── strDescription (same Fiix name, different plenum targets) ─────────
    ("Asset", "strDescription"): "notes",
    ("WorkOrder", "strDescription"): "description",
    ("ScheduledMaintenance", "strDescription"): "description",
    ("ChargeDepartment", "strDescription"): "description",
    ("Project", "strDescription"): "description",
    ("MiscCost", "strDescription"): "description",
    ("PurchaseOrderLineItem", "strDescription"): "description",
    ("RFQLineItem", "strDescription"): "description",
    ("RCAAction", "strDescription"): "description",
    ("RCACause", "strDescription"): "description",
    ("RCAProblem", "strDescription"): "description",
    ("Warranty", "strDescription"): "description",
    ("WorkOrderTask", "strDescription"): "description",
    ("StockHistory", "strDescription"): "description",
    ("UserCertification", "strDescription"): "description",
    # ── intCode (PO / RFQ numeric codes) ──────────────────────────────────
    ("PurchaseOrder", "intCode"): "po_number",
    ("RFQ", "intCode"): "rfq_number",
}

# Fiix field → plenum column when meaning is stable across objects
GLOBAL_FIIX_TO_PLENUM: Dict[str, str] = {
    "id": "id",                          # Fiix PK (int → UUID) → plenum_cafm 'id' PK

    "strMake": "make",
    "strModel": "model",
    "strSerialNumber": "serial_number",
    "strBarcode": "barcode",
    "strInventoryCode": "inventory_code",
    "strCriticality": "criticality",
    "strNotes": "notes",
    "strTimezone": "timezone",
    "strAddress": "address",
    "strCity": "city",
    "strProvince": "province",
    "strPostalCode": "postal_code",
    "strAisle": "aisle",
    "strRow": "row",
    "strBinNumber": "bin_number",
    "strStockLocation": "stock_location",
    "strShippingTerms": "shipping_terms",
    "bolIsSite": "is_site",
    "bolIsOnline": "is_online",
    "bolIsRegion": "is_region",
    "qtyStockCount": "stock_count",
    "strProblem": "problem",
    "strSolution": "solution",
    "strCompletionNotes": "completion_notes",
    "strAssignedUserIds": "assigned_user_ids",
    "strAssignedUsers": "assigned_users",
    "strAssetIds": "asset_ids",
    "strAssets": "assets",
    "strFullName": "full_name",
    "strUserName": "username",
    "strEmailAddress": "email",
    "strTelephone": "phone",
    "strTelephone2": "phone2",
    "strPersonnelCode": "personnel_code",
    "bolGroup": "is_group",
    "dblHourlyRate": "hourly_rate",
    "qtyOnHand": "quantity_on_hand",
    "qtyMinQty": "minimum_quantity",
    "qtyMaxQty": "maximum_quantity",
    "strPhone": "phone",
    "strFax": "fax",
    "strWebsite": "website",
    "dblMeterReading": "value",
    "dblSubtotal": "subtotal",
    "dblUnitPrice": "unit_price",
    "dblTotalPrice": "total_price",
    "dblTaxRate": "tax_rate",
    "dblTimeEstimatedHours": "estimated_hours",
    "dblTimeSpentHours": "hours_spent",
    "intOrder": "sort_order",
    "intTaskType": "task_type",
    "intUpdated": "updated_at",
    "strSymbol": "symbol",
    "intPrecision": "precision",
    "intSize": "file_size",
    "strLink": "file_url",
    "strEventCode": "event_code",
    "strContents": "contents",
    "dtmDateCreated": "created_at",
    "dtmCreated": "created_at",          # Asset uses dtmCreated (not dtmDateCreated)
    "dtmDateCompleted": "completed_at",
    "dtmDateReceived": "received_at",
    "dtmDateSubmitted": "recorded_at",
    "dtmDate": "transaction_date",
    # Asset geolocation + additional attributes (validated against the target table's
    # real columns at runtime; unknown targets are skipped, never an error).
    "dblLatitude": "latitude",
    "dblLongitude": "longitude",
    "dblLastPrice": "last_price",
    "qtyMinStockCount": "min_stock_count",
    "strUnspcCode": "unspsc_code",
    "strQuotingTerms": "quoting_terms",
    "strBin": "bin_number",
    "bolDeactivated": "deactivated",
    "intSuggestedCompletion": "suggested_completion_days",
}

# Fiix int*ID FK fields → plenum FK column (UUID column on ingest)
FK_FIIX_TO_PLENUM: Dict[str, str] = {
    "intAssetID": "asset_id",
    "intAssetParentID": "parent_asset_id",
    "intAssetLocationID": "location_id",
    "intSiteID": "location_id",
    "intCategoryID": "category_id",
    "intSuperCategoryID": "parent_category_id",
    "intAccountID": "account_code",
    "intChargeDepartmentID": "charge_department_id",
    "intCountryID": "country_id",
    "intProjectID": "project_id",
    "intWorkOrderID": "work_order_id",
    "intScheduledMaintenanceID": "scheduled_maintenance_id",
    "intWorkOrderStatusID": "status_id",
    "intPriorityID": "priority_id",
    "intMaintenanceTypeID": "maintenance_type_id",
    "intCompletedByUserID": "completed_by_id",
    "intRequestedByUserID": "requested_by_id",
    "intAssignedToUserID": "assigned_to_id",
    "intSubmittedByUserID": "submitted_by_id",
    "intUserID": "user_id",
    "intStockID": "spare_part_id",
    "intSupplierID": "vendor_id",
    "intPurchaseOrderID": "purchase_order_id",
    "intPurchaseOrderStatusID": "status_id",
    "intPurchaseOrderLineItemID": "purchase_order_line_id",
    "intBillingTermID": "billing_term_id",
    "intMiscCostTypeID": "misc_cost_type_id",
    "intBOMGroupID": "bom_group_id",
    "intReceiptID": "receipt_id",
    "intFileID": "file_id",
    "intWorkOrderTaskID": "work_order_task_id",
    "intTaskGroupControlID": "task_group_id",
    "intRCAGroupingID": "rca_grouping_id",
    "intRCAActionID": "rca_action_id",
    "intRCACauseID": "rca_cause_id",
    "intRCAProblemID": "rca_problem_id",
    "intMoveID": "move_id",
    "intMoveBackID": "move_back_id",
    "intMoveStatusID": "move_status_id",
    "intCycleCountID": "cycle_count_id",
    "intStockTxTypeID": "stock_tx_type_id",
    "intRFQID": "rfq_id",
    "intRFQStatusID": "status_id",
    "intReceiptStatusID": "status_id",
    "intAssetEventTypeID": "asset_event_type_id",
    "intBusinessID": "business_id",
    "intBusinessRoleTypeID": "business_role_type_id",
    "intBusinessGroupID": "business_group_id",
    "intScheduledMaintenanceStatusID": "status_id",
    "intParentID": "parent_id",
    "intConsumesAssetID": "consumes_asset_id",
    "intBOMPartControlID": "bom_part_control_id",
    "intBOMControlID": "bom_control_id",
    "intRegionID": "region_id",
}


def resolve_plenum_column(fiix_object: str, fiix_field: str) -> Optional[str]:
    """
    Return the plenum_cafm column for a Fiix field on a given object, or None if
  the field should remain in raw_metadata only.
    """
    key = (fiix_object, fiix_field)
    if key in OBJECT_FIELD_TO_PLENUM:
        return OBJECT_FIELD_TO_PLENUM[key]

    if fiix_field in FK_FIIX_TO_PLENUM:
        return FK_FIIX_TO_PLENUM[fiix_field]

    if fiix_field in GLOBAL_FIIX_TO_PLENUM:
        return GLOBAL_FIIX_TO_PLENUM[fiix_field]

    return None


def build_field_aliases_by_object(
    tables_by_object: Dict[str, Dict[str, Optional[str]]],
) -> Dict[str, Dict[str, str]]:
    """Per Fiix object: {fiix_field_name: plenum_column} for mapped fields only."""
    out: Dict[str, Dict[str, str]] = {}
    for obj_name, fields in tables_by_object.items():
        mapped = {
            fiix_field: plenum_col
            for fiix_field, plenum_col in fields.items()
            if plenum_col
        }
        if mapped:
            out[obj_name] = mapped
    return out


def build_internal_vendor_aliases(
    tables_by_object: Dict[str, Dict[str, Optional[str]]],
) -> Dict[str, list[str]]:
    """
    plenum column → list of Fiix source field names (migration alias table).

    The same Fiix field name (e.g. strDescription) may appear under multiple plenum
    columns; each plenum key lists every Fiix field that maps to it in any object.
    """
    aliases: Dict[str, list[str]] = {}
    for fields in tables_by_object.values():
        for fiix_field, plenum_col in fields.items():
            if not plenum_col:
                continue
            bucket = aliases.setdefault(plenum_col, [])
            if fiix_field not in bucket:
                bucket.append(fiix_field)
    return aliases


def fiix_objects_for_schema() -> list[str]:
    """Objects included in schema export (all defined fields; ingest uses table map subset)."""
    from .fiix_connector import FiixSchemaConnector

    return list(FiixSchemaConnector.ALL_OBJECTS)


def plenum_table_for_fiix_object(fiix_object: str) -> Optional[str]:
    from .fiix_data_connector import OBJECT_TABLE_MAP

    return OBJECT_TABLE_MAP.get(fiix_object)
