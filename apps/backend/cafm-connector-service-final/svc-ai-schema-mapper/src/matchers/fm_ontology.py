"""FM ontology for semantic Step-1b (RAG/alias) mapping + the UDR's FM personality.

Encoded from docs/FM_RAG_Semantic_Mapping_Reference.txt (COBie v3, BRICK schema, SFG20 / BESA,
and CMMS/CAFM/EAM vocabularies) and enriched from FM_Ontology_Reference_v1.0.docx — the
authoritative UDR destination schema (13 core + 8 elastic entities) with the full per-platform
alias vocabulary (IBM Maximo, SAP EAM, Planon, Archibus, TRIRIGA, COBie, BRICK, Facilio,
Accruent, MaintainX). Those docx-derived aliases live in the auto-generated fm_ontology_data.py
and are merged below (curated core wins). Three uses:

  1. FM_FIELD_ALIASES — FM-native source COLUMN names → canonical UDR fields. Merged into
     CMMS_ALIASES so the deterministic alias step resolves FM terms with no LLM call.
  2. FM_TABLE_SYNONYMS / FM_EQUIPMENT_SYNONYMS / value vocabularies — entity + asset-type +
     status/priority/type recognition (lookup helpers below).
  3. FM_PERSONA — gives the UDR a facilities-management domain personality, injected into the
     LLM mapping/query prompts so reasoning uses FM language (trades, COBie, BRICK, SFG20).

Column aliases map ONLY to canonical fields that exist in the target schema (see
matchers.cmms_aliases.CANONICAL_FIELDS); FM concepts without a canonical target (contracts,
areas, meters, costs) are left for the auto-created-column flow.
"""

from __future__ import annotations

import os
import re

FM_ALIAS_CONFIDENCE = 0.96  # deterministic Step-1b confidence for an FM-ontology hit

def _find_reference_doc() -> str:
    """Walk up from this module to locate docs/FM_RAG_Semantic_Mapping_Reference.txt."""
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(9):
        cand = os.path.join(d, "docs", "FM_RAG_Semantic_Mapping_Reference.txt")
        if os.path.exists(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.path.join(d, "docs", "FM_RAG_Semantic_Mapping_Reference.txt")


RAG_REFERENCE_PATH = _find_reference_doc()


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


# ── FM column synonyms → canonical UDR fields (extends CMMS_ALIASES) ─────────────
# value = the canonical field; keys are normalized at index-build time.
_FM_FIELD_RAW: dict[str, list[str]] = {
    # The entity's system/business PRIMARY KEY (assets.id) — the *_id identity aliases.
    # Kept separate from asset_code because id and asset_code are distinct columns. (Per
    # source table, the table-aware identity strategy still maps the sheet's own id first.)
    "id": [
        "asset_id", "assetid", "assetidentifier", "equipment_id", "eq_id", "eq-id",
        "component_id", "installation_id", "plant_id", "item_id", "device_id", "kit_id",
        "unit_id",
    ],
    # The operational, human-readable asset REFERENCE CODE (assets.asset_code) — NOT the PK.
    "asset_code": [
        "assetnum", "asset_no", "asset_ref", "asset_number", "asset_code", "equnr",
        "equipment_number", "equipment_code", "component_code", "installation_ref",
        "plant_item_no", "plant_number", "plant_no", "plant_code", "item_no",
        "item_number", "device_no", "kit_no", "unit_ref",
    ],
    # Physical tag identifiers (assets.barcode) — barcode / QR / RFID / NFC tag numbers.
    "barcode": [
        "barcode", "asset_barcode", "eq_barcode", "eq_tag", "asset_tag", "tagno",
        "tag_number", "tag_no", "rfid_tag", "nfc_tag",
    ],
    "asset_name": [
        "asset_description", "equipment_name", "equipment_description", "plant_name",
        "plant_description", "component_name", "component_description", "device_name",
        "device_description", "eqktx", "item_description", "unit_name",
    ],
    "asset_type": [
        "asset_type", "equipment_type", "equipment_category", "plant_type", "plant_category",
        "component_type", "device_type", "item_type", "kit_type", "type_code", "type_name",
        "assettype", "eqtyp", "asset_type_id", "classstructure", "uniclass_code",
        "omniclass_code", "classification_code", "eqstd",
    ],
    "category": [
        "asset_category", "asset_class", "equipment_class", "plant_class", "component_category",
        "classification", "class_code", "class_name", "category_code", "klah",
    ],
    "make": ["manufacturer", "make", "mfg", "brand", "maker"],
    "model": ["model", "modelnumber", "model_number", "model_no", "model_ref"],
    "serial": ["serial", "serial_no", "serialnum", "serialnumber", "serial_number"],
    "location_code": [
        "location_id", "loc_id", "loc-id", "location_code", "location_ref", "space_id",
        "space_code", "room_id", "room_code", "room_number", "floor_id", "floor_code",
        "zone_id", "zone_code", "site_code", "building_id", "bl_id", "bl-id",
    ],
    "asset_status": [
        "asset_status", "condition", "asset_condition", "operational_status",
        "service_status", "ustmp", "tristatuscl",
    ],
    "wo_code": [
        "work_order_id", "workorder_id", "wonum", "aufnr", "qmnum", "wo_id", "wo_num",
        "wo_number", "wo_ref", "wo_code", "job_id", "job_number", "job_ref", "job_code",
        "task_id", "task_number", "task_ref", "request_id", "request_number", "ticket_id",
        "ticket_no", "ticket_number", "fault_id", "fault_number", "defect_id",
        "defect_number", "report_id", "report_number", "wr_id", "wr-id", "ac_id", "ac-id",
        "maintenance_ref", "maintenance_no", "service_ref", "service_number", "order_ref",
    ],
    "wo_status": ["wo_status", "job_status", "task_status", "order_status", "request_status", "ticket_status"],
    "wo_priority": ["wo_priority", "job_priority", "task_priority", "priority_code"],
    "wo_type": ["wo_type", "job_type", "work_type", "order_type"],
    "maintenance_type": ["maintenance_type", "maintenance_category", "ppm_type", "service_type"],
    "wo_description": [
        "fault_description", "fault_detail", "fault_details", "problem_description",
        "defect_description", "issue_description", "fault_report", "problem_report",
        "reported_fault", "reported_issue", "reported_problem", "symptom", "complaint",
        "complaint_details", "notification_text", "request_details",
    ],
    "created_date": [
        "date_raised", "date_created", "raised_date", "reported_date", "date_reported",
        "open_date", "date_opened", "log_date", "date_logged", "request_date",
        "date_requested", "submit_date", "reportdate", "erdat", "date_of_request",
    ],
    "due_date": [
        "required_by", "required_date", "target_date", "due_date", "deadline",
        "completion_deadline", "target_completion", "target_close", "targcompdate",
        "wanted_date", "needed_date", "expected_date",
    ],
    "last_completion_date": [
        "completion_date", "date_completed", "closed_date", "date_closed", "finish_date",
        "date_finished", "resolved_date", "date_resolved", "actfinish", "resolution_date",
        "close_date", "done_date", "fixed_date",
    ],
    "assigned_to": ["assigned_to", "assignee", "assigned_engineer", "assigned_technician", "owner"],
    "supplier": [
        "supplier", "vendor", "contractor", "service_provider", "vendor_name", "vendor_code",
        "vendor_id", "vendor_ref", "contractor_name", "contractor_code", "contractor_id",
        "supplier_name", "supplier_code", "supplier_id", "lifnr", "subcontractor",
    ],
    "inspector_name": ["inspector_name", "inspector", "inspected_by", "inspector_no"],
    "inspection_date": [
        "inspection_date", "test_date", "service_date", "visit_date", "inspection_performed",
        "date_inspected", "date_tested", "date_serviced",
    ],
    "part_code": ["part_code", "part_id", "part_number", "part_no", "spare_code", "material_code", "matnr"],
    "part_description": ["part_description", "part_name", "spare_description", "material_description"],
    "user_full_name": [
        "full_name", "person_name", "employee_name", "engineer_name", "technician_name",
        "operative_name", "resource_name", "staff_name", "contact_name",
    ],
    "finding_type": ["finding_type", "defect_type", "fault_type", "issue_type"],
    "risk_level": ["risk_level", "risk_rating", "severity", "level_of_risk"],
}

FM_FIELD_ALIASES: dict[str, str] = {}
for _canon, _syns in _FM_FIELD_RAW.items():
    for _s in _syns:
        FM_FIELD_ALIASES.setdefault(_norm(_s), _canon)

# Merge the auto-generated platform column aliases extracted from
# FM_Ontology_Reference_v1.0.docx (IBM Maximo, SAP EAM, Planon, Archibus, TRIRIGA,
# COBie, BRICK, Facilio, Accruent, MaintainX). The curated core above wins.
try:
    from .fm_ontology_data import DOCX_FIELD_ALIASES as _DOCX_FIELD_ALIASES

    for _a, _c in _DOCX_FIELD_ALIASES.items():
        FM_FIELD_ALIASES.setdefault(_norm(_a), _c)
except Exception:  # pragma: no cover — additive enrichment, never fatal
    _DOCX_FIELD_ALIASES = {}


# ── FM ENTITY (table-level) synonym clusters ────────────────────────────────────
FM_TABLE_SYNONYMS: dict[str, list[str]] = {
    "site": ["property", "estate", "campus", "development", "location", "facility", "premises",
             "portfolio_asset", "managed_property", "managed_estate", "site"],
    "building": ["building", "block", "tower", "structure", "warehouse", "shed",
                 "distribution_centre", "store", "office_building", "industrial_unit", "bldg"],
    "space": ["space", "room", "floor", "level", "zone", "area", "suite", "plant_room",
              "riser", "corridor", "lobby", "common_area", "parking_bay", "basement"],
    "asset": ["asset", "equipment", "plant", "plant_item", "component", "installed_asset",
              "building_component", "fixed_asset", "m_and_e_asset", "asset_register",
              "asset_list", "equipment_register"],
    "asset_type": ["asset_type", "asset_category", "asset_class", "equipment_type",
                   "equipment_category", "plant_category", "classification"],
    "work_order": ["work_order", "workorder", "job", "task", "job_card", "work_ticket",
                   "maintenance_request", "fault_report", "defect_report", "reactive_job",
                   "callout", "ppm_job", "service_request", "notification", "repair_order"],
    "resource": ["resource", "engineer", "technician", "operative", "tradesperson",
                 "craftsperson", "worker", "labour", "field_engineer", "mobile_engineer",
                 "fm_engineer", "maintenance_staff"],
    "vendor": ["vendor", "contractor", "supplier", "service_provider", "subcontractor",
               "service_contractor", "specialist_contractor", "tfm_contractor",
               "managing_contractor", "company", "provider"],
    "contract": ["contract", "service_contract", "maintenance_contract", "service_agreement",
                 "framework_agreement", "ppm_contract", "tfm_contract", "sla", "sla_agreement",
                 "purchase_order"],
    "compliance_certificate": ["compliance_certificate", "compliance_cert", "stat_cert",
                               "statutory_cert", "inspection_cert", "test_certificate",
                               "safety_certificate", "eicr", "loler", "cp12", "fra",
                               "f_gas", "pat_test", "asbestos_survey", "legionella"],
    "ppm_schedule": ["ppm", "ppm_schedule", "planned_maintenance", "preventive_maintenance",
                     "pm_schedule", "maintenance_schedule", "service_schedule",
                     "sfg20_schedule", "besa_schedule", "scheduled_maintenance", "job_plan"],
    "meter": ["meter", "metre", "energy_meter", "electricity_meter", "gas_meter", "water_meter",
              "heat_meter", "sub_meter", "submeter", "smart_meter", "iot_sensor",
              "reading_point", "mpan", "mprn", "utility_meter"],
}

# Short platform table CODES that appear only inside parentheses in the reference doc
# (Archibus 2-letter, SAP, TRIRIGA) — added explicitly so a source SHEET named e.g.
# 'EQ', 'WR', 'LFA1', 'VN' routes to the right entity. Table-name context only.
for _ent, _codes in {
    "site": ["trigeography", "geography"],
    "building": ["bl"],
    "space": ["fl", "rm"],
    "asset": ["eq"],
    "work_order": ["wr", "ac", "triworktask", "work_task"],
    "resource": ["em", "tripeople"],
    "vendor": ["vn"],
    "contract": ["ct", "contractauth", "outline_agreement"],
    "ppm_schedule": ["mpnum", "maintenance_plan", "task_list"],
    "document": ["dir", "dr", "fi"],
}.items():
    FM_TABLE_SYNONYMS.setdefault(_ent, [_ent])
    for _c in _codes:
        if _c not in FM_TABLE_SYNONYMS[_ent]:
            FM_TABLE_SYNONYMS[_ent].append(_c)

# ── FM EQUIPMENT taxonomy (asset-type value recognition) ─────────────────────────
FM_EQUIPMENT_SYNONYMS: dict[str, list[str]] = {
    "air_handling_unit": ["ahu", "air handler", "air handling unit", "air handling plant",
                          "mau", "make-up air unit", "fresh air unit", "rooftop ahu", "dahu"],
    "fan_coil_unit": ["fcu", "fan coil", "fan coil unit", "chilled beam", "induction unit"],
    "vav_box": ["vav", "vav box", "vav terminal", "variable air volume", "terminal box"],
    "chiller": ["chiller", "water-cooled chiller", "air-cooled chiller", "centrifugal chiller",
                "screw chiller", "absorption chiller", "refrigeration plant", "acrm"],
    "boiler": ["boiler", "hot water boiler", "steam boiler", "condensing boiler",
               "biomass boiler", "lphw boiler", "mphw boiler"],
    "cooling_tower": ["cooling tower", "evaporative cooler", "dry cooler", "fluid cooler", "cwt"],
    "heat_exchanger": ["heat exchanger", "phe", "plate heat exchanger", "shell and tube",
                       "thermal wheel", "hrv", "erv", "hex"],
    "pump": ["pump", "circulating pump", "chilled water pump", "chwp", "hot water pump", "hwp",
             "condenser water pump", "cwp", "booster pump", "sump pump", "submersible pump"],
    "fan": ["fan", "extract fan", "supply fan", "exhaust fan", "centrifugal fan", "axial fan",
            "smoke extract fan", "ef", "sf"],
    "vrf_system": ["vrf", "vrv", "variable refrigerant flow", "variable refrigerant volume",
                   "multi-split", "split system", "ceiling cassette"],
    "distribution_board": ["db", "distribution board", "consumer unit", "fuse box", "mcb board",
                           "sdb", "mdb", "main distribution board", "panel", "lvdb"],
    "switchboard": ["switchboard", "lv switchboard", "mv switchboard", "hv switchboard",
                    "mlvs", "main switchboard", "switchgear", "busbar"],
    "transformer": ["transformer", "distribution transformer", "hv transformer", "rmu",
                    "ring main unit", "package substation"],
    "generator": ["generator", "standby generator", "emergency generator", "diesel generator",
                  "genset", "gen set", "chp"],
    "ups": ["ups", "uninterruptible power supply", "battery backup", "static ups", "online ups", "pdu"],
    "vfd": ["vfd", "vsd", "variable frequency drive", "variable speed drive", "inverter", "ac drive"],
    "fire_alarm_panel": ["fire alarm panel", "facp", "fire panel", "fire control panel",
                         "fas panel", "addressable panel", "afd panel"],
    "sprinkler_system": ["sprinkler", "wet sprinkler", "dry sprinkler", "deluge system",
                         "fm200", "gaseous suppression", "novec"],
    "emergency_lighting": ["emergency lighting", "emergency luminaire", "escape lighting",
                           "exit sign", "central battery system"],
    "lift": ["lift", "elevator", "passenger lift", "goods lift", "platform lift", "service lift",
             "dumbwaiter", "lula lift", "stairlift"],
    "escalator": ["escalator", "moving walkway", "travelator"],
    "cold_water_storage_tank": ["cwst", "cold water tank", "break tank", "header tank",
                                "cold water cistern", "water storage tank"],
    "calorifier": ["calorifier", "hot water calorifier", "hot water cylinder", "hwc", "dhws"],
    "bms": ["bms", "bems", "building management system", "building automation system", "bas",
            "ddc", "scada", "ems", "controls system"],
    "access_control": ["access control", "acs", "door entry", "card reader", "proximity reader",
                       "biometric reader", "turnstile"],
    "cctv": ["cctv", "closed circuit television", "security camera", "ip camera", "ptz camera",
             "dome camera", "nvr", "dvr"],
    "meter": ["meter", "energy meter", "electricity meter", "gas meter", "water meter",
              "heat meter", "sub meter", "btu meter", "amr meter"],
}

# ── controlled VALUE vocabularies (cell-value normalization) ─────────────────────
WORK_ORDER_STATUS_VOCAB: dict[str, str] = {}
for _canon, _vals in {
    "open": ["open", "new", "raised", "created", "logged", "submitted", "pending", "draft"],
    "assigned": ["assigned", "dispatched", "allocated", "accepted", "acknowledged"],
    "in_progress": ["in progress", "in_progress", "in work", "active", "attending", "on site", "started", "working"],
    "on_hold": ["on hold", "on_hold", "waiting parts", "awaiting parts", "escalated", "paused", "suspended"],
    "completed": ["completed", "done", "finished", "resolved", "fixed", "closed", "signed off"],
    "cancelled": ["cancelled", "rejected", "deleted", "voided", "withdrawn"],
}.items():
    for _v in _vals:
        WORK_ORDER_STATUS_VOCAB[_norm(_v)] = _canon

WORK_ORDER_PRIORITY_VOCAB: dict[str, str] = {}
for _canon, _vals in {
    "P1": ["p1", "emergency", "critical", "priority 1", "immediate", "life safety", "0-4hr"],
    "P2": ["p2", "urgent", "priority 2", "same day", "4-8hr", "high priority"],
    "P3": ["p3", "routine", "priority 3", "24hr", "next day", "standard", "normal", "medium priority"],
    "P4": ["p4", "low", "priority 4", "planned", "scheduled", "non-urgent", "5-day", "deferred"],
}.items():
    for _v in _vals:
        WORK_ORDER_PRIORITY_VOCAB[_norm(_v)] = _canon

WORK_ORDER_TYPE_VOCAB: dict[str, str] = {}
for _canon, _vals in {
    "reactive": ["reactive", "fault", "defect", "breakdown", "callout", "unplanned", "ad hoc", "break fix", "corrective"],
    "planned": ["ppm", "pm", "planned", "preventive", "preventative", "scheduled", "routine maintenance", "periodic", "annual service", "service"],
    "inspection": ["inspection", "statutory inspection", "compliance check", "audit", "test", "survey", "condition survey"],
    "project": ["project", "cap ex", "capital works", "improvement", "upgrade", "refurbishment", "fit out", "installation"],
    "emergency": ["emergency", "p1 emergency", "out of hours", "ooh emergency"],
}.items():
    for _v in _vals:
        WORK_ORDER_TYPE_VOCAB[_norm(_v)] = _canon


def _build_index(clusters: dict[str, list[str]]) -> dict[str, str]:
    idx: dict[str, str] = {}
    for canon, syns in clusters.items():
        idx[_norm(canon)] = canon
        for s in syns:
            idx.setdefault(_norm(s), canon)
    return idx


_TABLE_IDX = _build_index(FM_TABLE_SYNONYMS)

# Merge the auto-generated platform table/entity aliases from the docx (curated wins).
try:
    from .fm_ontology_data import DOCX_TABLE_ALIASES as _DOCX_TABLE_ALIASES

    for _a, _e in _DOCX_TABLE_ALIASES.items():
        _TABLE_IDX.setdefault(_norm(_a), _e)
except Exception:  # pragma: no cover — additive enrichment, never fatal
    _DOCX_TABLE_ALIASES = {}

_EQUIP_IDX = _build_index(FM_EQUIPMENT_SYNONYMS)


def _lookup(idx: dict[str, str], name) -> tuple:
    canon = idx.get(_norm(name))
    return (canon, FM_ALIAS_CONFIDENCE) if canon else (None, 0.0)


def fm_field_lookup(name) -> tuple:
    """Resolve an FM-native source COLUMN name to a canonical UDR field. -> (canon, conf)."""
    canon = FM_FIELD_ALIASES.get(_norm(name))
    return (canon, FM_ALIAS_CONFIDENCE) if canon else (None, 0.0)


def fm_table_lookup(name) -> tuple:
    """Resolve an FM-native source TABLE/sheet name to a canonical FM entity (handles plurals,
    e.g. 'Resources' -> 'resource'). -> (canon, conf)."""
    n = _norm(name)
    canon = _TABLE_IDX.get(n) or _TABLE_IDX.get(n.rstrip("s"))
    return (canon, FM_ALIAS_CONFIDENCE) if canon else (None, 0.0)


def _best_canonical_for_entity(entity: str, canonical_tables) -> str | None:
    """Find an existing canonical/destination table that corresponds to an FM entity, e.g.
    entity 'resource' -> a 'resources'/'resource' table if one exists."""
    if not entity:
        return None
    target = _norm(entity).rstrip("s")
    for t in canonical_tables or []:
        if _norm(t).rstrip("s") == target:
            return t
    # also accept a destination table whose name is a known synonym of the entity
    for t in canonical_tables or []:
        if _TABLE_IDX.get(_norm(t)) == entity:
            return t
    return None


def suggest_table_routing(
    source_table: str,
    canonical_tables,
    *,
    matched: str | None = None,
    confidence: float = 0.0,
    min_confidence: float = 0.70,
) -> dict:
    """7.4 AC6 — when a source table has no confident destination match, suggest one rather
    than leaving the user with a bare 'pick a table'. Returns
    ``{action: 'assign'|'create', target, suggested_new_name, reason, confidence}``:

    1. a confident existing match -> assign it;
    2. else FM ontology maps the source name to a canonical FM entity:
       - if an existing destination table fits that entity -> assign it (a *suggestion*),
       - otherwise -> suggest CREATING a new table named for the entity (e.g. 'resource');
    3. no FM entity at all -> suggest creating a new table named after the source.
    """
    try:
        conf = float(confidence or 0.0)
    except (TypeError, ValueError):
        conf = 0.0

    if matched and conf >= min_confidence:
        return {"action": "assign", "target": matched, "suggested_new_name": None,
                "reason": "matched", "confidence": round(conf, 2)}

    entity, _ = fm_table_lookup(source_table)
    if entity:
        existing = _best_canonical_for_entity(entity, canonical_tables)
        if existing:
            return {"action": "assign", "target": existing, "suggested_new_name": None,
                    "reason": f"fm_ontology:{entity}", "confidence": round(max(conf, 0.80), 2)}
        return {"action": "create", "target": None, "suggested_new_name": entity,
                "reason": f"fm_ontology:{entity}", "confidence": 0.80}

    return {"action": "create", "target": None, "suggested_new_name": _norm(source_table),
            "reason": "no_match", "confidence": round(conf, 2)}


def fm_equipment_lookup(name) -> tuple:
    """Resolve an equipment value/name to a canonical asset type. -> (canon, conf)."""
    return _lookup(_EQUIP_IDX, name)


def normalize_status(value) -> str | None:
    return WORK_ORDER_STATUS_VOCAB.get(_norm(value))


def normalize_priority(value) -> str | None:
    return WORK_ORDER_PRIORITY_VOCAB.get(_norm(value))


def normalize_wo_type(value) -> str | None:
    return WORK_ORDER_TYPE_VOCAB.get(_norm(value))


# ── the UDR's facilities-management PERSONALITY ─────────────────────────────────
FM_PERSONA = (
    "You are the Plenum CAFM Unified Data Repository (UDR) — a facilities-management domain "
    "expert. You reason in the language of UK/international FM operations: assets and plant "
    "(AHU, FCU, chiller, boiler, pump, lift, fire alarm panel), work orders (reactive / PPM / "
    "statutory inspection, P1-P4 priorities), resources and trades (M&E, electrical, mechanical, "
    "lift, fire), vendors and TFM contracts with SLAs, compliance certificates (EICR, LOLER, "
    "CP12, FRA, L8, F-Gas, PAT), PPM schedules (SFG20 / BESA), and metering (MPAN/MPRN, "
    "sub-metering). You understand the COBie v3 sheet model (Facility, Floor, Space, Zone, Type, "
    "Component, System, Job, Event, Resource, Company, Document) and the BRICK schema equipment "
    "and point hierarchies, and you map source data to canonical FM entities using that domain "
    "knowledge — never treating an FM term as generic text."
)


def fm_reference_text() -> str | None:
    """The full FM RAG reference document text, for retrieval-augmented prompting (or None)."""
    try:
        with open(RAG_REFERENCE_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


# ── FM SKILL CONTEXT — the single injection point for domain-aware AI ───────────
# Every LLM-backed decision in the platform (field mapping, hierarchy inference,
# document Q&A) should prepend this so reasoning happens in FM language. Use
# fm_skill_preamble() for the persona + a compact vocabulary digest (cheap, always
# safe to inject) and pass include_reference=True only where the token budget can
# afford a slice of the full RAG reference.

def fm_domain_digest() -> str:
    """A compact, token-cheap summary of the canonical FM entities, their hierarchy,
    and the work-order value vocabularies — for injecting into LLM prompts where the
    full reference document is too large."""
    entities = ", ".join(sorted(FM_TABLE_SYNONYMS.keys()))
    equipment = ", ".join(sorted(FM_EQUIPMENT_SYNONYMS.keys()))
    statuses = sorted(set(WORK_ORDER_STATUS_VOCAB.values())) if WORK_ORDER_STATUS_VOCAB else []
    priorities = sorted(set(WORK_ORDER_PRIORITY_VOCAB.values())) if WORK_ORDER_PRIORITY_VOCAB else []
    wo_types = sorted(set(WORK_ORDER_TYPE_VOCAB.values())) if WORK_ORDER_TYPE_VOCAB else []
    lines = [
        "FM canonical entities: " + entities + ".",
        "FM hierarchy (parent -> child): Site -> Building -> Space (Floor/Zone/Room) "
        "-> Asset; Asset -> WorkOrder -> Resource/Vendor; Asset -> PPMSchedule; "
        "Asset -> ComplianceCertificate; Meter measures Site/Building/Space/Asset.",
        "Asset types (equipment classes): " + equipment + ".",
    ]
    if statuses:
        lines.append("Work-order status values: " + ", ".join(statuses) + ".")
    if priorities:
        lines.append("Work-order priorities: " + ", ".join(priorities) + " (P1=Emergency ... P4=Low).")
    if wo_types:
        lines.append("Work-order types: " + ", ".join(wo_types) + ".")
    return "\n".join(lines)


def fm_skill_preamble(*, include_reference: bool = False, max_reference_chars: int = 6000) -> str:
    """The canonical FM Skill Context block to prepend to any LLM prompt across the
    platform. Returns the FM persona + the compact domain digest; when
    include_reference is True, appends a leading slice of the full RAG reference
    document (capped at max_reference_chars) for retrieval-augmented grounding."""
    parts = [FM_PERSONA, "", fm_domain_digest()]
    if include_reference:
        ref = fm_reference_text()
        if ref:
            parts += ["", "FM ONTOLOGY REFERENCE (excerpt):", ref[:max_reference_chars].rstrip()]
    return "\n".join(parts)
