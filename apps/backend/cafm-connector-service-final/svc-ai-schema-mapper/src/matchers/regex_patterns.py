"""CMMS naming convention regex patterns for field detection.

Strategy 3 in the 4-tier deterministic mapping pipeline.
16 patterns covering common CMMS naming conventions.
"""

import re
from typing import Optional, Tuple

# Pattern tuples: (compiled_regex, canonical_field, confidence)
# Ordered from most specific to most general
_PATTERNS_LIST = [
    # Asset patterns
    (re.compile(r"^asset_?(?:code|id|no|num|number)$", re.I), "asset_code", 0.93),
    (re.compile(r"^(?:equipment|equip)_?(?:code|id|no|num|number)$", re.I), "asset_code", 0.92),
    (re.compile(r"^asset_?(?:name|description|desc)$", re.I), "asset_name", 0.91),
    (re.compile(r"^asset_?(?:category|class|type|group)$", re.I), "category", 0.90),
    (re.compile(r"^(?:site|location|area|building|floor)_?(?:code|id|no)$", re.I), "location_code", 0.91),
    (re.compile(r"^(?:location|building|floor)_?(?:name|description)$", re.I), "location_code", 0.89),
    (re.compile(r"^(?:manufacturer|maker|make|brand|vendor)_?(?:code|name)$", re.I), "make", 0.88),
    (re.compile(r"^(?:model|model_number|model_no)$", re.I), "model", 0.90),
    (re.compile(r"^(?:serial|serial_number|serial_no|sn)$", re.I), "serial", 0.92),
    (re.compile(r"^(?:parent_?)?asset_?(?:parent|parent_code)$", re.I), "parent_asset_code", 0.89),

    # Work order patterns
    (re.compile(r"^(?:work_?order|wo)_?(?:code|id|no|num|number)$", re.I), "wo_code", 0.94),
    (re.compile(r"^(?:work_?order|wo)_?(?:priority|priority_level|level)$", re.I), "wo_priority", 0.91),
    (re.compile(r"^(?:work_?order|wo)_?(?:status|state)$", re.I), "wo_status", 0.92),
    (re.compile(r"^(?:work_?order|wo|job)_?(?:type|category|class)$", re.I), "wo_type", 0.90),
    (re.compile(r"^(?:maintenance_?)?(?:type|plan|code|description)$", re.I), "maintenance_type", 0.80),

    # Date patterns
    (re.compile(r"^(?:created|raised|opened|issued)_?(?:date|time|datetime|ts)$", re.I), "created_date", 0.90),
    (re.compile(r"^(?:due|target|scheduled|deadline)_?(?:date|time|datetime|ts)$", re.I), "due_date", 0.90),
    (re.compile(r"^(?:completed|closed|finished)_?(?:date|time|datetime|ts)$", re.I), "last_completion_date", 0.88),

    # Scheduled PM patterns
    (re.compile(r"^(?:pm|preventive_?maintenance|scheduled_?maintenance)_?(?:code|id|no|num)$", re.I), "sm_code", 0.92),
    (re.compile(r"^(?:pm|maintenance)_?(?:type|trigger|interval_type)$", re.I), "trigger_type", 0.88),
    (re.compile(r"^(?:frequency|interval|schedule|cycle)_?(?:value|days|months|interval)$", re.I), "schedule_interval", 0.87),

    # Parts/Inventory patterns
    (re.compile(r"^(?:part|item|inventory)_?(?:code|id|no|num|number)$", re.I), "part_code", 0.92),
    (re.compile(r"^(?:part|item|part|product)_?(?:name|description|desc)$", re.I), "part_description", 0.89),
    (re.compile(r"^(?:qty|quantity|stock|quantity_?)on_?hand$", re.I), "stock_on_hand", 0.93),
    (re.compile(r"^(?:min|minimum)_?(?:qty|quantity|stock|level|allowed)$", re.I), "minimum_allowed_stock", 0.91),
    (re.compile(r"^(?:supplier|vendor|distributor)_?(?:name|code|id)$", re.I), "supplier", 0.89),
    (re.compile(r"^(?:unit|uom|unit_of_measure|measurement|measure)$", re.I), "unit_of_measure", 0.89),

    # User/Assignment patterns
    (re.compile(r"^(?:user|person|employee)_?(?:name|full_name|fullname)$", re.I), "user_full_name", 0.89),
    (re.compile(r"^(?:user|job)_?(?:title|role|position)$", re.I), "user_title", 0.88),
    (re.compile(r"^(?:user|login|user_?)(?:name|id|username)$", re.I), "user_name", 0.88),
    (re.compile(r"^(?:reports_?to|manager|supervisor|assigned_to)$", re.I), "reports_to", 0.87),
    (re.compile(r"^(?:assigned|assigned_to|technician|responsible)$", re.I), "assigned_to", 0.85),

    # Inspection patterns
    (re.compile(r"^(?:inspector|inspected_?by|inspection_?by)_?(?:name|user)$", re.I), "inspector_name", 0.88),
    (re.compile(r"^(?:inspection|inspected|survey)_?(?:date|time|datetime)$", re.I), "inspection_date", 0.90),
    (re.compile(r"^(?:inspection|inspected|location|site|area)_?(?:location|site|area|place)$", re.I), "inspection_location", 0.87),
    (re.compile(r"^(?:finding|issue|defect|observation)_?(?:type|category|class)$", re.I), "finding_type", 0.86),
    (re.compile(r"^(?:risk|severity|criticality|level|priority)_?(?:level|rating|score)$", re.I), "risk_level", 0.85),
]

# Compile into immutable list
PATTERNS = _PATTERNS_LIST


def match_field_by_pattern(field_name: str) -> Optional[Tuple[str, float]]:
    """
    Match a field name against regex patterns.

    Patterns are checked in order (most specific to most general).
    Returns the first match.

    Args:
        field_name: Raw field name from customer CMMS export

    Returns:
        Tuple of (canonical_field_name, confidence) if matched, else None
        Confidence range: 0.80–0.94 (depends on pattern specificity)
    """
    normalized = field_name.lower().strip()

    for pattern, canonical, confidence in PATTERNS:
        if pattern.match(normalized):
            return (canonical, confidence)

    return None
