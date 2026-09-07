"""CMMS vendor field aliases mapping to canonical field names.

Covers: Maximo, Fiix, SAP PM, Archibus, and other common CMMS systems.
Strategy 2 in the 4-tier deterministic mapping pipeline.
"""

from typing import Optional

# Canonical field registry (target fields)
CANONICAL_FIELDS = {
    # Asset fields
    "id",            # system/business primary key (assets.id) — distinct from asset_code
    "barcode",       # physical barcode/QR/RFID tag (assets.barcode) — distinct from asset_code
    "asset_code",
    "asset_name",
    "category",
    "location_code",
    "make",
    "model",
    "serial",
    "asset_status",
    "asset_type",
    "parent_asset_code",
    # Work order fields
    "wo_code",
    "wo_priority",
    "wo_status",
    "wo_type",
    "maintenance_type",
    "wo_description",
    "created_date",
    "due_date",
    "assigned_to",
    # Scheduled PM fields
    "sm_code",
    "trigger_type",
    "schedule_interval",
    "sm_priority",
    "last_completion_date",
    # Part fields
    "part_code",
    "part_description",
    "stock_on_hand",
    "minimum_allowed_stock",
    "supplier",
    "unit_of_measure",
    "bom_group_name",
    # User fields
    "user_full_name",
    "user_title",
    "user_name",
    "reports_to",
    # Inspection fields
    "inspector_name",
    "inspection_date",
    "inspection_location",
    "finding_type",
    "risk_level",
}

# Comprehensive alias mapping: vendor_field_name → canonical_field_name
# Confidence: each alias maps to canonical with confidence 0.95–0.98
CMMS_ALIASES = {
    # ── MAXIMO ──────────────────────────────────────────────────────────

    # Asset aliases
    "assetnum": "asset_code",
    "assetid": "asset_code",
    "assetname": "asset_name",
    "asset_description": "asset_name",
    "assetclass": "category",
    "asset_class": "category",
    "class": "category",
    "siteid": "location_code",
    "site_id": "location_code",
    "sitename": "location_code",
    "location": "location_code",
    "manufacturer": "make",
    "vendor_name": "make",
    "modelnum": "model",
    "model_number": "model",
    "serialnumber": "serial",
    "serial_number": "serial",
    "sn": "serial",
    "assetstatus": "asset_status",
    "status": "asset_status",
    "assettype": "asset_type",
    "asset_type": "asset_type",
    "parent_assetnum": "parent_asset_code",
    "parent_asset": "parent_asset_code",

    # Work order aliases
    "wonum": "wo_code",
    "wo_number": "wo_code",
    "workordernum": "wo_code",
    "wopriority": "wo_priority",
    "priority": "wo_priority",
    "wostatus": "wo_status",
    "wo_status": "wo_status",
    "worktype": "wo_type",
    "work_type": "wo_type",
    "maintenance_plan": "maintenance_type",
    "pm_code": "maintenance_type",
    "description": "wo_description",
    "workdescription": "wo_description",
    "createddate": "created_date",
    "created_date": "created_date",
    "duedate": "due_date",
    "due_date": "due_date",
    "assignedto": "assigned_to",
    "assigned_to": "assigned_to",
    "technician": "assigned_to",

    # Scheduled PM aliases
    "pmnumplan": "sm_code",
    "pm_code": "sm_code",
    "pm_num": "sm_code",
    "pmtype": "trigger_type",
    "pm_type": "trigger_type",
    "trigger": "trigger_type",
    "frequency": "schedule_interval",
    "interval": "schedule_interval",
    "schedule_interval": "schedule_interval",
    "pmschedule": "schedule_interval",
    "pmpriority": "sm_priority",
    "pm_priority": "sm_priority",
    "lastpmdate": "last_completion_date",
    "last_pm_date": "last_completion_date",
    "lastcompdate": "last_completion_date",
    "last_completion_date": "last_completion_date",

    # Part aliases
    "itemnum": "part_code",
    "item_num": "part_code",
    "part_number": "part_code",
    "partcode": "part_code",
    "itemdesc": "part_description",
    "description": "part_description",
    "part_desc": "part_description",
    "qty_on_hand": "stock_on_hand",
    "qty_onhand": "stock_on_hand",
    "quantity_on_hand": "stock_on_hand",
    "bin_qty": "stock_on_hand",
    "min_qty": "minimum_allowed_stock",
    "minimum_qty": "minimum_allowed_stock",
    "min_allowed": "minimum_allowed_stock",
    "reorder_point": "minimum_allowed_stock",
    "supplier_name": "supplier",
    "vendor": "supplier",
    "unit": "unit_of_measure",
    "uom": "unit_of_measure",
    "unit_of_measure": "unit_of_measure",
    "bomcode": "bom_group_name",
    "bom_code": "bom_group_name",
    "bom_group": "bom_group_name",

    # User aliases
    "personname": "user_full_name",
    "person_name": "user_full_name",
    "full_name": "user_full_name",
    "name": "user_full_name",
    "jobtitle": "user_title",
    "job_title": "user_title",
    "title": "user_title",
    "username": "user_name",
    "user_id": "user_name",
    "login": "user_name",
    "reportsto": "reports_to",
    "reports_to": "reports_to",
    "manager": "reports_to",
    "supervisor": "reports_to",

    # ── FIIX ─────────────────────────────────────────────────────────

    "xassets": "asset_code",
    "xassetid": "asset_code",
    "xasset_name": "asset_name",
    "xasset_description": "asset_name",
    "xasset_category": "category",
    "xlocationid": "location_code",
    "xlocation_name": "location_code",
    "xmanufacturer": "make",
    "xmodel": "model",
    "xserial_number": "serial",
    "xstatus": "asset_status",
    "xasset_type": "asset_type",
    "xparent_asset": "parent_asset_code",

    "xworkorders": "wo_code",
    "xwo_id": "wo_code",
    "xpriority": "wo_priority",
    "xwo_status": "wo_status",
    "xwork_type": "wo_type",
    "xdescription": "wo_description",
    "xcreated_date": "created_date",
    "xdue_date": "due_date",
    "xassigned_user": "assigned_to",

    "xpm_code": "sm_code",
    "xpm_type": "trigger_type",
    "xfrequency": "schedule_interval",
    "xpriority": "sm_priority",
    "xlast_pm_date": "last_completion_date",

    "xpart_code": "part_code",
    "xpart_description": "part_description",
    "xqty_on_hand": "stock_on_hand",
    "xmin_qty": "minimum_allowed_stock",
    "xsupplier": "supplier",
    "xuom": "unit_of_measure",

    # ── SAP PM ───────────────────────────────────────────────────────

    "equnr": "asset_code",
    "eqnr": "asset_code",
    "equipment_number": "asset_code",
    "equipment_name": "asset_name",
    "eqktx": "asset_name",
    "eqtyp": "asset_type",
    "equipment_type": "asset_type",
    "typbez": "category",
    "klart": "category",
    "tplnr": "location_code",
    "plant_location": "location_code",
    "werks": "location_code",
    "herst": "make",
    "manufacturer_code": "make",
    "typvrt": "model",
    "modellnummer": "model",
    "seriennummer": "serial",
    "sn": "serial",

    "aufnr": "wo_code",
    "work_order_number": "wo_code",
    "aufnr": "wo_code",
    "priok": "wo_priority",
    "priority_code": "wo_priority",
    "astatus": "wo_status",
    "wartype": "wo_type",
    "work_type_code": "wo_type",
    "auftext": "wo_description",
    "erdat": "created_date",
    "fälldatum": "due_date",
    "due_date": "due_date",
    "iaw": "assigned_to",
    "assigned_user": "assigned_to",

    "pldnr": "sm_code",
    "plan_number": "sm_code",
    "plkat": "trigger_type",
    "plan_category": "trigger_type",
    "inaktiv_date": "last_completion_date",
    "last_completion": "last_completion_date",

    "matnr": "part_code",
    "part_number": "part_code",
    "mtart": "part_description",
    "maktx": "part_description",
    "part_description": "part_description",
    "labst": "stock_on_hand",
    "quantity_on_hand": "stock_on_hand",
    "minbestand": "minimum_allowed_stock",
    "lifnr": "supplier",
    "supplier_code": "supplier",
    "meins": "unit_of_measure",
    "unit": "unit_of_measure",

    # ── ARCHIBUS ─────────────────────────────────────────────────────

    "em_id": "asset_code",
    "equip_id": "asset_code",
    "em_name": "asset_name",
    "equip_name": "asset_name",
    "em_category": "category",
    "em_type": "asset_type",
    "bl_id": "location_code",
    "building_id": "location_code",
    "fl_id": "location_code",
    "floor_id": "location_code",
    "rm_id": "location_code",
    "room_id": "location_code",
    "em_brand": "make",
    "em_model": "model",
    "em_sn": "serial",
    "serial_no": "serial",

    "rm_id": "wo_code",
    "request_id": "wo_code",
    "req_priority": "wo_priority",
    "priority": "wo_priority",
    "status": "wo_status",
    "description": "wo_description",
    "created": "created_date",
    "due": "due_date",
    "assigned": "assigned_to",

    "issue_code": "sm_code",
    "issue_type": "trigger_type",
    "frequency": "schedule_interval",
    "priority": "sm_priority",

    "part_id": "part_code",
    "part_num": "part_code",
    "part_name": "part_description",
    "available_qty": "stock_on_hand",
    "minimum_qty": "minimum_allowed_stock",
    "vendor_name": "supplier",
    "unit_measure": "unit_of_measure",

    # ── GENERIC / COMMON ─────────────────────────────────────────────

    "id": None,  # Too generic — skip
    "code": None,  # Too generic — context needed
    "name": None,  # Too generic — context needed
    "type": None,  # Too generic — context needed
    "status": None,  # Too generic — context needed
    "description": None,  # Too generic — context needed
}

# Remove None values from aliases (too generic to be reliable)
CMMS_ALIASES = {k: v for k, v in CMMS_ALIASES.items() if v is not None}

# ── Extend with the FM ontology synonyms (COBie v3 / BRICK / SFG20 / FM trades) ──
# These resolve FM-native source field names to the SAME canonical fields during the
# deterministic alias step (no LLM call). Existing CMMS entries take precedence.
try:
    from .fm_ontology import FM_FIELD_ALIASES as _FM_FIELD_ALIASES

    for _k, _v in _FM_FIELD_ALIASES.items():
        if _v in CANONICAL_FIELDS:
            CMMS_ALIASES.setdefault(_k, _v)
except Exception:  # pragma: no cover — FM ontology is additive, never fatal
    pass


def get_cmms_alias(field_name: str, cmms_system: Optional[str] = None) -> Optional[tuple[str, float]]:
    """
    Look up a field name in the CMMS alias dictionary.

    Args:
        field_name: Raw field name from customer CMMS export
        cmms_system: Optional CMMS system name (unused for now, but can be extended for CMMS-specific rules)

    Returns:
        Tuple of (canonical_field_name, confidence) if found, else None
        Confidence range: 0.95–0.98 (very high confidence for exact aliases)
    """
    normalized = field_name.lower().strip()

    # Direct match
    if normalized in CMMS_ALIASES:
        return (CMMS_ALIASES[normalized], 0.97)

    # Try without underscores/dashes
    normalized_compact = normalized.replace("_", "").replace("-", "")
    for alias_key, canonical in CMMS_ALIASES.items():
        if alias_key.replace("_", "").replace("-", "") == normalized_compact:
            return (canonical, 0.95)

    return None


def get_reversed_aliases() -> dict[str, list[str]]:
    """
    Return vendor_aliases in the format needed by JsonMapperConfig.

    Reverses CMMS_ALIASES so each canonical field maps to all its known aliases.

    Returns:
        Dictionary: {canonical_field: [alias1, alias2, ...], ...}
        Example:
            {
                "asset_code": ["assetnum", "assetid", "xassets", ...],
                "asset_name": ["assetname", "asset_description", "xasset_name", ...],
                ...
            }
    """
    reversed_aliases: dict[str, list[str]] = {}

    for alias, canonical in CMMS_ALIASES.items():
        if canonical not in reversed_aliases:
            reversed_aliases[canonical] = []
        if alias not in reversed_aliases[canonical]:
            reversed_aliases[canonical].append(alias)

    return reversed_aliases
