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

#: table -> the predicate (on alias ``t``) a row must pass to be linked to or matched on.
#:
#: Deleting a vendor that has history RETIRES it (Hussain, 12 Oct 2026): the row keeps its id,
#: its status becomes 'deleted' and its name gains " (deleted)". Matched as before, a workbook
#: re-uploaded afterwards put its new work orders, visits and contracts on a vendor that is gone
#: from every list — by id, by a code left on the row, or by the marked name an export of
#: history carries verbatim. The status is the authority; the mark only defends in depth.
#: Source of truth: ops-intelligence src/shared/vendor_identity.py live_vendor_sql.
LIVE_ROW: dict[str, str] = {
    "vendors": "COALESCE(lower(to_jsonb(t)->>'status'), 'active') <> 'deleted'",
}

LOOKUP_SQL = """
SELECT id::text FROM {schema}.{table} t
 WHERE organization_id::text = :org
   AND ({match}){live}
 LIMIT 2
"""

#: Does an id the file already carries name a row that may not be linked to? A count, so the
#: answer is always one row: an empty answer can only mean the read itself failed.
RETIRED_SQL = """
SELECT count(*) FROM {schema}.{table} t
 WHERE lower(t.id::text) = :k
   AND NOT ({live})
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
        self._misses: set[tuple[str, str]] = set()
        self._table = ""
        self._linkable: dict[tuple[str, str], bool] = {}
        self.reads = 0
        self.resolved = 0
        self.ambiguous: list[str] = []
        self.unresolved: dict[str, set] = {}

    def begin_table(self, table: str) -> None:
        """The writer moves on to ``table``: what was missing may have been written since."""
        self._table = str(table or "")
        self._misses.clear()

    async def resolve(self, column: str, hint: str | None) -> str | None:
        spec = REFERENCES.get(column)
        if not spec or not hint:
            return None
        key = (column, str(hint).strip().lower())
        if not key[1]:
            return None
        if key in self._cache:
            return self._cache[key]
        if key in self._misses:
            return None

        _names, table, match_cols = spec
        match = " OR ".join(f"lower(coalesce({c}::text, '')) = :k" for c in match_cols)
        match += " OR id::text = :k"
        live = f"\n   AND {LIVE_ROW[table]}" if table in LIVE_ROW else ""
        self.reads += 1
        rows = await self._fetch(
            LOOKUP_SQL.format(schema=self._schema, table=table, match=match, live=live),
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
        # A HIT is cached; a MISS is not. These resolvers live for the whole run, and a
        # reference that is absent when one table asks may be written by the time the next
        # one does — vendors are loaded after work_orders, so caching work_orders' miss left
        # every later table with a null vendor_id. Found 23 Sep 2026: 13 work orders and 64
        # PPM visits all showing "Unassigned" against vendors that were on record.
        # The writer resolves every row of a table before it inserts any of them, so within that
        # pass a miss stays a miss: it is kept until the writer moves on (begin_table) — 2,270
        # work orders whose assets had not landed asked for the same asset on every row (29 Oct
        # 2026). The engine's tableMemo (write/rows.go) does the same.
        if found is not None:
            self._cache[key] = found
        elif self._table:
            self._misses.add(key)
        return found

    async def linkable(self, column: str, carried: object) -> bool:
        """Whether an id the file already carries may be written as it stands.

        A row carrying a uuid never reaches ``resolve`` — there is nothing to look up. But a
        retired vendor keeps its id, so a workbook exported before the delete still carries
        it, and written through it attached new work to a vendor that no longer exists for
        anybody. Such an id is refused and reported with the references that could not be
        placed. An id naming no row at all is kept: it may be a vendor this same file writes.

        A check that could not be read refuses too. Linking new work to a vendor nobody could
        confirm is live is the one mistake this exists to prevent, and the run says which
        lookup failed. Only a definite answer is remembered, so a failed read is asked again.
        """
        spec = REFERENCES.get(column)
        value = str(carried).strip() if carried not in (None, "") else ""
        if not spec or not value or spec[1] not in LIVE_ROW:
            return True
        key = (column, value.lower())
        ok = self._linkable.get(key)
        if ok is None:
            self.reads += 1
            rows = await self._fetch(
                RETIRED_SQL.format(schema=self._schema, table=spec[1], live=LIVE_ROW[spec[1]]),
                {"k": key[1]},
            )
            ok = bool(rows) and bool(rows[0]) and int(rows[0][0] or 0) == 0
            if rows:
                self._linkable[key] = ok
        if not ok:
            self.unresolved.setdefault(column, set()).add(value)
        return ok

    def report(self) -> dict:
        return {
            "reads": self.reads,
            "resolved": self.resolved,
            "ambiguous": sorted(set(self.ambiguous))[:10],
            "unresolved": {k: sorted(v)[:10] for k, v in self.unresolved.items()},
        }
