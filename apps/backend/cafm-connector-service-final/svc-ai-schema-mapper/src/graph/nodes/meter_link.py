"""The meter link — how a migrated meter finds its building, and a reading finds its meter.

`building_link.py` taught the writer to resolve a site reference into `buildings.building_id`,
but only for two tables: the guard read `safe_table in ("assets", "work_orders")`. Energy went
through the same writer and came out unlinked, which is worse than it sounds, because nothing
complains. `energy_meters.building_id` is a plain uuid with no constraint behind it, so a
meter with no building inserts cleanly; the anomaly scan then sweeps it, raises findings, and
writes them with a null building. Those findings are invisible on every building-scoped page
and no EUI snapshot is produced for them at all. The scan reports success and the page stays
empty.

`meter_readings` failed harder and more honestly. Its `meter_id` is NOT NULL behind a real
foreign key and the writer had no way to fill it, so every reading row was rejected and
counted as skipped.

This module is the resolution the writer lacked, kept pure so it can be tested without a
database:

  meter_hint(row)              what in a source row names a meter (or None)
  supply_numbers(row)          the MPAN and MPRN a row carries, however they were spelled
  meter_type_for(row)          gas when there is an MPRN and no MPAN, electricity otherwise
  MeterResolver.resolve(...)   hint -> energy_meters.id, creating the meter when the row also
                               names a building that resolves, and REFUSING to create one
                               when it does not

That refusal is the important rule. A meter created without a building satisfies the foreign
key, so the readings land and the run reports success, and every figure they should have
produced is silently attributed to nothing. Skipping the row and saying so is worse to look
at and better to act on.

The resolver takes an async `fetch(sql, params) -> list[tuple]` so the writer can hand it the
session (inside a savepoint) and a test can hand it a dict.
"""
from __future__ import annotations

import re
from typing import Awaitable, Callable

Fetch = Callable[[str, dict], Awaitable[list]]
Create = Callable[..., Awaitable[str | None]]

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

#: Columns that name a meter, most specific first. `meter_id` counts only when it is not
#: already a uuid, because then it is a customer's own reference under a header that happens
#: to match ours.
_METER_HINT_KEYS = (
    "meter_id", "mpan", "mprn", "mpan_mprn", "meter_ref", "meter_reference",
    "meter_point", "supply_number", "meter_number", "meter_serial", "msn",
    "serial_number", "meter",
)

#: Columns that can only be an electricity supply number, and ones that can only be gas.
_MPAN_KEYS = ("mpan", "mpan_core", "mpan_number", "electricity_supply_number")
_MPRN_KEYS = ("mprn", "mprn_number", "gas_supply_number")
#: Columns that carry one or the other without saying which.
_EITHER_KEYS = (
    "mpan_mprn", "meter_ref", "meter_reference", "meter_point", "supply_number",
    "meter_number", "meter_serial", "msn", "serial_number", "meter", "meter_id",
)

#: Columns that name where in the building a meter sits. A building with a meter per floor
#: has several rows that differ only here.
_SECTION_KEYS = (
    "section", "section_name", "floor", "floor_name", "level", "storey", "story",
    "zone", "area", "location", "sub_location", "space", "demise", "unit",
)

#: Columns that say outright that a meter is a sub-meter.
_SUB_METER_KEYS = ("is_sub_meter", "sub_meter", "submeter", "meter_level", "meter_role")

#: Columns that say which fuel a row is about.
_FUEL_KEYS = ("meter_type", "fuel", "fuel_type", "supply_type", "utility", "commodity", "energy_type")

_GAS_WORDS = ("gas", "mprn", "therm")
_ELEC_WORDS = ("electric", "elec", "power", "mpan", "kva")

GAS = "gas"
ELECTRICITY = "electricity"


def looks_like_uuid(value: object) -> bool:
    return bool(value) and bool(_UUID.match(str(value).strip()))


def _clean(value: object) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _keys(row: dict) -> dict:
    """The row under normalised keys.

    A CMMS export writes "Meter Reference", "MPAN Core", "Supply No."; matching those against
    snake_case constants by lower-casing alone silently finds nothing, which reads as "this
    sheet names no meter" rather than as a spelling the reader does not know.
    """
    out: dict = {}
    for k, v in row.items():
        norm = re.sub(r"[^a-z0-9]+", "_", str(k).strip().lower()).strip("_")
        if norm:
            out.setdefault(norm, v)
    return out


def meter_hint(row: dict | None) -> str | None:
    """The first non-empty value in `row` that names a meter.

    None when the row already carries a real `meter_id` uuid, because then there is nothing to
    resolve, and None when it names nothing.
    """
    if not isinstance(row, dict):
        return None
    if looks_like_uuid(row.get("meter_id")):
        return None
    lower = _keys(row)
    for key in _METER_HINT_KEYS:
        s = _clean(lower.get(key))
        if s and not looks_like_uuid(s):
            return s
    return None


def supply_numbers(row: dict | None) -> tuple[str | None, str | None]:
    """The (MPAN, MPRN) a row carries.

    A column named `mpan` is an MPAN whatever it holds; likewise `mprn`. A column that could be
    either is decided by the row's own fuel column, and falls to electricity when the row does
    not say, because that is the commoner supply and a wrong guess here only affects which
    column the reference is stored under, not which building the meter lands on.
    """
    if not isinstance(row, dict):
        return None, None
    lower = _keys(row)

    mpan = next((_clean(lower[k]) for k in _MPAN_KEYS if _clean(lower.get(k))), None)
    mprn = next((_clean(lower[k]) for k in _MPRN_KEYS if _clean(lower.get(k))), None)
    if mpan or mprn:
        return mpan or None, mprn or None

    ambiguous = next((_clean(lower[k]) for k in _EITHER_KEYS if _clean(lower.get(k))), None)
    if not ambiguous or looks_like_uuid(ambiguous):
        return None, None
    if _fuel_word(lower) == GAS:
        return None, ambiguous
    return ambiguous, None


def _fuel_word(lower: dict) -> str | None:
    for key in _FUEL_KEYS:
        s = _clean(lower.get(key)).lower()
        if not s:
            continue
        if any(w in s for w in _GAS_WORDS):
            return GAS
        if any(w in s for w in _ELEC_WORDS):
            return ELECTRICITY
    return None


def section_hint(row: dict | None) -> str | None:
    """Where in the building this meter sits, as the row names it.

    A tower with a meter per floor produces rows that are identical but for this. Without it
    every one of them is created as the building's main meter, and the building's consumption
    is counted once per floor.
    """
    if not isinstance(row, dict):
        return None
    lower = _keys(row)
    for key in _SECTION_KEYS:
        v = _clean(lower.get(key))
        if v and not looks_like_uuid(v):
            return v
    return None


def is_sub_meter_for(row: dict | None) -> bool:
    """Whether this row describes a sub-meter rather than the building's incoming supply.

    Believed when the row says so outright; otherwise inferred from it naming a floor, a zone
    or a section, because a meter that sits somewhere in particular is not the whole building.
    A main meter is the one that names nowhere.
    """
    if not isinstance(row, dict):
        return False
    lower = _keys(row)
    for key in _SUB_METER_KEYS:
        v = _clean(lower.get(key)).lower()
        if not v:
            continue
        if v in ("true", "yes", "y", "1", "sub", "submeter", "sub_meter", "sub-meter", "tenant"):
            return True
        if v in ("false", "no", "n", "0", "main", "incoming", "primary", "site"):
            return False
    return section_hint(row) is not None


#: A section of THIS building, by its own name, its type, or the floor it sits on. Scoped to
#: the building so "Level 3" means this building's third floor and not another tower's.
SECTION_LOOKUP_SQL = """
SELECT s.section_id::text
  FROM {schema}.building_sections s
  LEFT JOIN {schema}.floors f ON f.floor_id = s.floor_id
 WHERE s.building_id = CAST(:b AS uuid)
   AND (lower(s.name) = :k
     OR lower(coalesce(s.section_type, '')) = :k
     OR lower(coalesce(f.name, '')) = :k
     OR coalesce(f.level::text, '') = :k)
 LIMIT 2
"""


def meter_type_for(row: dict | None) -> str:
    """`energy_meters.meter_type` is NOT NULL with no default, so a meter sheet that does not
    carry one fails every row. Read the fuel the row states; otherwise infer it from which
    supply number is present, which is the same rule the chat upload path uses."""
    if not isinstance(row, dict):
        return ELECTRICITY
    lower = _keys(row)
    stated = _fuel_word(lower)
    if stated:
        return stated
    mpan, mprn = supply_numbers(row)
    return GAS if (mprn and not mpan) else ELECTRICITY


LOOKUP_SQL = """
SELECT id::text FROM {schema}.energy_meters
 WHERE organization_id::text = :org
   AND (lower(coalesce(mpan, '')) = :k
     OR lower(coalesce(mprn, '')) = :k
     OR lower(coalesce(dcc_device_id, '')) = :k
     OR id::text = :k)
 LIMIT 2
"""


#: Creating a meter is the one write this module implies, and it is deliberately narrow: an
#: organisation, a building, a fuel and whichever supply number the sheet carried. Tariff and
#: carbon factor keep their column defaults, because a migration that invents a tariff prices
#: every excess figure on the page with a number nobody chose.
CREATE_METER_SQL = """
INSERT INTO {schema}.energy_meters
       (id, organization_id, building_id, meter_type, mpan, mprn, active,
        is_sub_meter, section_id)
VALUES (gen_random_uuid(), CAST(:org AS uuid), CAST(:bid AS uuid), :mtype, :mpan, :mprn, true,
        :is_sub, CAST(:sid AS uuid))
RETURNING id::text
"""


class MeterResolver:
    """Resolves meter references for one organisation, one run.

    Every distinct hint costs at most one read; the answer, including "no match", is cached, so
    a year of half-hourly readings against four meters does four lookups rather than seventy
    thousand.
    """

    def __init__(self, fetch: Fetch, organization_id: str, schema: str = "plenum_cafm",
                 create: Create | None = None) -> None:
        self._fetch = fetch
        self._org = str(organization_id or "")
        self._schema = schema
        self._create = create
        self._cache: dict[str, str | None] = {}
        self.reads = 0
        self.created = 0
        self.ambiguous: list[str] = []
        #: Hints that named no existing meter and carried no building to create one against.
        self.unlinked: list[str] = []

    async def resolve(
        self, hint: str | None, *, building_id: str | None = None,
        meter_type: str = ELECTRICITY, mpan: str | None = None, mprn: str | None = None,
        section_id: str | None = None, is_sub_meter: bool = False,
    ) -> str | None:
        if not hint:
            return None
        key = str(hint).strip().lower()
        if not key:
            return None
        if key in self._cache:
            return self._cache[key]

        found, ambiguous = await self._existing(key)
        if ambiguous:
            # Two meters answer to this reference. Creating a third would be the worst of the
            # three outcomes, and picking one puts a year of consumption on a building at random.
            self._cache[key] = None
            return None
        if found is None and self._create is not None:
            if building_id:
                found = await self._create(
                    mpan=mpan, mprn=mprn, meter_type=meter_type, building_id=building_id,
                    section_id=section_id, is_sub_meter=is_sub_meter,
                )
                if found:
                    self.created += 1
            else:
                # Deliberately not created. See the module docstring: a meter with no building
                # produces findings that are attributed to nothing and no EUI at all, and the
                # run would report success.
                self.unlinked.append(str(hint))
        self._cache[key] = found
        return found

    async def _existing(self, key: str) -> tuple[str | None, bool]:
        """(the meter, whether the reference was ambiguous). The two are different answers:
        nothing found may be created, more than one found may not."""
        self.reads += 1
        rows = await self._fetch(
            LOOKUP_SQL.format(schema=self._schema), {"org": self._org, "k": key}
        )
        ids = sorted({str(r[0]) for r in rows if r and r[0]})
        if len(ids) == 1:
            return ids[0], False
        if len(ids) > 1:
            self.ambiguous.append(key)
            return None, True
        return None, False

    def report(self) -> dict:
        return {
            "reads": self.reads,
            "created": self.created,
            "ambiguous": sorted(set(self.ambiguous)),
            "unlinked": sorted(set(self.unlinked)),
        }
