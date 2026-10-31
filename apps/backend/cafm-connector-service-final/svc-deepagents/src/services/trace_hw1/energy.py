"""Allowlisted Energy execution metadata; results and free text stay out of traces."""
from copy import deepcopy
from ..energy_hw1.contracts import EnergyQuery

READ_TOOLS = {"energy_eui", "energy_readings"}
FIELDS = {"building_id", "meter_id", "section_id", "period_start", "period_end"}


def observation(principal, tool, arguments, origin, succeeded):
    """Only successful scoped typed reads may expose exact passed arguments."""
    item = {"tool": tool, "succeeded": succeeded, "execution_origin": origin if origin in {"model", "fallback"} else "unknown",
            "arguments_capture": "withheld_or_not_supported"}
    if tool not in READ_TOOLS or not succeeded or principal is None or principal.role not in {"property_manager", "portfolio_manager"}:
        return item
    try:
        if not isinstance(arguments, dict) or not set(arguments) <= FIELDS:
            return item
        query = EnergyQuery.model_validate(arguments)
        if query.building_id not in principal.building_ids:
            return item
        # Preserve original passed keys/values, not schema defaults or inferred IDs.
        if any(not isinstance(value, (str, type(None))) for value in arguments.values()):
            return item
        item.update(arguments=deepcopy(arguments), arguments_capture="exact_passed_arguments")
    except (ValueError, TypeError):
        pass
    return item


def export_tools(principal, tools):
    """Recheck saved observations before export; historical missing data stays missing."""
    output = []
    for item in tools:
        if "execution_origin" in item:
            output.append(observation(principal, item.get("tool"), item.get("arguments"),
                                      item.get("execution_origin"), item.get("succeeded") is True))
        else:
            output.append({key: item[key] for key in ("tool", "succeeded") if key in item})
    return output
