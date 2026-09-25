"""Which meters a building's consumption is counted from.

A sub-meter reads a share of the incoming supply: the electricity on Level 3 has already passed
through the building's main meter on its way there. Add the two together and the building has
used that energy twice. Before floor-level sub-meters existed on any building here this could
not go wrong, because every meter WAS the incoming supply; the moment a building carries a
meter per floor, an EUI summed over "every active meter" reads nearly double, its gap to the
reference becomes a large number, and the cost card prices the excess of a building that does
not exist.

So a building is measured by its main meters where it has one on that fuel. Sub-meters count
only where no main meter exists for the fuel — a landlord who only meters the tenants has no
better figure, and the sum of the parts is then the honest one.

One predicate, used by every read that sums a building's kWh, so the EUI, the benchmark gap and
the contracted tariff all agree on which meters a building is.
"""
from __future__ import annotations


def counted_meters(alias: str = "em") -> str:
    """SQL: this row is a main meter, or a sub-meter on a fuel with no main meter to count."""
    return (
        f"(NOT {alias}.is_sub_meter OR NOT EXISTS ("
        f"SELECT 1 FROM plenum_cafm.energy_meters p "
        f"WHERE p.building_id = {alias}.building_id AND p.active AND NOT p.is_sub_meter "
        f"AND lower(coalesce(p.meter_type, 'electricity')) "
        f"= lower(coalesce({alias}.meter_type, 'electricity'))))"
    )


#: The floor a section sits on, when the floors table has no row for it and only the section's
#: own text names the floor. -1 for a basement, 0 for the ground floor, the number for a level.
def floor_level_from_name(name: str | None) -> int | None:
    s = (name or "").strip().lower()
    if not s:
        return None
    if s.startswith("basement") or s in ("lower ground", "lg", "b1"):
        return -1
    if s.startswith("ground") or s in ("g", "gf", "level 0", "l0"):
        return 0
    digits = "".join(ch for ch in s if ch.isdigit())
    if digits and any(s.startswith(p) for p in ("level", "floor", "l", "storey", "story")):
        return int(digits)
    return None
