"""Codes and names in a spreadsheet, resolved to the ids the database keys on.

A CSV cannot carry a uuid anybody would type. It carries `asset_code`, `vendor_name`,
`contract_name` — and the columns they belong to are `asset_id`, `vendor_id`, `contract_id`.
Nothing bridged the two, so a file naming perfectly real things wrote null into every one of
them.

For most columns that is a thin row. For a few it is an invisible one. The PPM health table
inner-joins assets:

    FROM plenum_cafm.ppm_visits v
    JOIN plenum_cafm.assets a ON a.id::text = v.asset_id::text

so a visit with no `asset_id` is dropped before anything is counted. A database holding 264 of
them reported "0 of 0 planned visits" — not a small number, but no number at all. And a visit
with no `contract_id` groups under "Unassigned", which hides the one contract that is behind
among all the others.

This is the same shape as building_link and meter_link: read the hint the file carries, ask
the register what it names, and leave the column absent rather than guess. A name matching two
rows resolves to neither, because putting a year of visits on the wrong contract is worse than
leaving them unattributed and saying so.

Pure, so it can be tested without a database: the resolver takes an async
`fetch(sql, params) -> list[tuple]`.
"""
from __future__ import annotations

import re
from typing import Awaitable, Callable

Fetch = Callable[[str, dict], Awaitable[list]]

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def looks_like_uuid(value: object) -> bool:
    return bool(value) and bool(_UUID.match(str(value).strip()))


def _keys(row: dict) -> dict:
    """The row under normalised keys, because an export writes "Asset Code", not asset_code."""
    out: dict = {}
    for k, v in (row or {}).items():
        norm = re.sub(r"[^a-z0-9]+", "_", str(k).strip().lower()).strip("_")
        if norm:
            out.setdefault(norm, v)
    return out


#: destination column -> (columns that name it, table, columns to match on)
#:
#: Order matters inside each tuple: the most specific name first, so an export carrying both a
#: code and a display name is matched on the code.
REFERENCES: dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]] = {
    "asset_id": (
        ("asset_id", "asset_code", "asset_ref", "asset_tag", "equipment_code", "equipment_id",
         "plant_ref", "asset"),
        "assets", ("asset_code", "serial_number", "inventory_code"),
    ),
    "vendor_id": (
        ("vendor_id", "vendor_code", "vendor_name", "vendor", "supplier", "contractor",
         "supplier_name"),
        "vendors", ("vendor_code", "vendor_name"),
    ),
    "contract_id": (
        ("contract_id", "contract_ref", "contract_number", "contract_name", "contract"),
        "vendor_contracts", ("contract_name",),
    ),
    "part_id": (
        ("part_id", "part_code", "part_number", "part_name", "part"),
        "spare_parts", ("part_code", "part_name"),
    ),
}

LOOKUP_SQL = """
SELECT id::text FROM {schema}.{table}
 WHERE organization_id::text = :org
   AND ({match})
 LIMIT 2
"""


def hint_for(column: str, row: dict | None) -> str | None:
    """What this row calls the thing `column` points at, or None when it already carries a uuid."""
    spec = REFERENCES.get(column)
    if not spec or not isinstance(row, dict):
        return None
    lower = _keys(row)
    if looks_like_uuid(lower.get(column)):
        return None
    for key in spec[0]:
        v = lower.get(key)
        s = str(v).strip() if v not in (None, "") else ""
        if s and not looks_like_uuid(s):
            return s
    return None


class ReferenceResolver:
    """Resolves codes and names to ids for one organisation, one run.

    Every distinct hint costs one read; the answer, including "no match", is cached, so a year
    of visits against a dozen assets does a dozen lookups rather than a hundred.
    """

    def __init__(self, fetch: Fetch, organization_id: str, schema: str = "plenum_cafm") -> None:
        self._fetch = fetch
        self._org = str(organization_id or "")
        self._schema = schema
        self._cache: dict[tuple[str, str], str | None] = {}
        self.reads = 0
        self.resolved = 0
        self.ambiguous: list[str] = []
        self.unresolved: dict[str, set] = {}

    async def resolve(self, column: str, hint: str | None) -> str | None:
        spec = REFERENCES.get(column)
        if not spec or not hint:
            return None
        key = (column, str(hint).strip().lower())
        if not key[1]:
            return None
        if key in self._cache:
            return self._cache[key]

        _names, table, match_cols = spec
        match = " OR ".join(f"lower(coalesce({c}::text, '')) = :k" for c in match_cols)
        match += " OR id::text = :k"
        self.reads += 1
        rows = await self._fetch(
            LOOKUP_SQL.format(schema=self._schema, table=table, match=match),
            {"org": self._org, "k": key[1]},
        )
        ids = sorted({str(r[0]) for r in rows if r and r[0]})
        if len(ids) == 1:
            found = ids[0]
            self.resolved += 1
        else:
            found = None
            if len(ids) > 1:
                # Two rows answer to the same name. Choosing one would put this row on the
                # wrong asset or the wrong contract, which is worse than leaving it unplaced.
                self.ambiguous.append(f"{column}={hint}")
            else:
                self.unresolved.setdefault(column, set()).add(str(hint))
        self._cache[key] = found
        return found

    def report(self) -> dict:
        return {
            "reads": self.reads,
            "resolved": self.resolved,
            "ambiguous": sorted(set(self.ambiguous))[:10],
            "unresolved": {k: sorted(v)[:10] for k, v in self.unresolved.items()},
        }
