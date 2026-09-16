"""Writing energy, asset and maintenance facts into the tables they belong in.

The catalogue said where every field lands and the mapper worked out which column of a sheet
fills it. Nothing wrote them: compliance and vendors had extractors, the other three domains
returned their facts and held them. This is those three extractors.

Four things this is careful about, each because the careless version fails silently.

**The schema is probed, never assumed.** The two databases disagree on more than column names.
``hoistra_test`` declares ``work_orders.title``, ``priority`` and ``status`` NOT NULL and
``plenum_agent`` declares almost nothing; ``inspections.ingestion_id`` exists on one and not
the other. A writer built against either one breaks on the other, at insert time, halfway
through a file. So columns and their nullability are read from ``information_schema`` once per
process, and a row missing something the table actually requires is refused BY NAME rather
than attempted and rolled back.

**Nothing is interpolated into SQL.** Table and column names are checked against a regex and
then against the probed catalogue — the same rule the Table Editor follows. Values are always
parameters.

**Writes are idempotent.** An import run twice must not double a portfolio. Every table has a
natural key here — asset_code, mpan_mprn, meter_id+reading_at, ppm_ref, wo_code — and a row
that matches one is updated, not inserted again. A table with no natural key available says so
and skips rather than guessing.

**A human's edit is not overwritten by an import.** Update fills columns that are empty and
leaves populated ones alone, unless ``overwrite`` is asked for explicitly. Someone corrected
the serial number by hand; a re-import should not quietly undo that.

Dry run is the default. ``apply=True`` is the caller saying they mean it.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

SCHEMA = "plenum_cafm"
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

#: Probed once per process: {table: {column: {"nullable": bool, "type": str, "default": bool}}}
_shape: dict[str, dict[str, dict[str, Any]]] | None = None

#: What a row is keyed on when deciding insert-or-update. Ordered: the first key whose columns
#: are all present in the row is the one used.
NATURAL_KEYS: dict[str, list[list[str]]] = {
    "assets": [["asset_code"]],
    # energy_meters, not `meters`: meter_readings.meter_id points at energy_meters.id, and
    # `meters` is a vestigial table nothing references for readings.
    "energy_meters": [["mpan"], ["mprn"], ["dcc_device_id"]],
    "meter_readings": [["meter_id", "reading_at"]],
    "ppm_visits": [["ppm_ref"], ["asset_code", "scheduled_date"]],
    "work_orders": [["wo_code"]],
    # inspections has no natural key: two visits by the same engineer to the same asset on the
    # same day are two findings, not one. Keyed on all three anyway is the least-bad option and
    # is stated as such rather than pretended to be an identity.
    "inspections": [["asset_code", "inspection_date", "inspector"]],
}

#: Filled on insert when the table demands the column and the sheet did not supply it. Every
#: one of these is a statement about the row, not a placeholder: `source` says where it came
#: from, and a status of 'imported' is visibly not a status anybody chose.
REQUIRED_DEFAULTS: dict[str, dict[str, Any]] = {
    "assets": {"status": "active", "is_online": False, "is_site": False},
    "meter_readings": {"period_minutes": 1440, "source": "import"},
    "ppm_visits": {"tolerance_days": 0, "status": "imported", "source": "import",
                   "raw_metadata": {}, "deferred": False},
    "work_orders": {"title": "Imported work order", "priority": "medium",
                    "status": "imported", "conflict_flag": False, "source": "import"},
    "inspections": {"corrective_action": False},
}

#: Where the import records itself, when the table has somewhere to put it.
PROVENANCE = ("source", "source_reference", "source_file", "source_document_id", "ingestion_id")


async def shape(session: AsyncSession, *, refresh: bool = False) -> dict[str, dict[str, dict[str, Any]]]:
    """Columns, nullability and defaults for the tables written here, read from the database."""
    global _shape
    if _shape is not None and not refresh:
        return _shape
    rows = (await session.execute(sa_text("""
        SELECT table_name, column_name, is_nullable, data_type, column_default
          FROM information_schema.columns
         WHERE table_schema = :s AND table_name = ANY(CAST(:t AS text[]))
    """), {"s": SCHEMA, "t": list(NATURAL_KEYS)})).mappings().all()
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["table_name"], {})[r["column_name"]] = {
            "nullable": r["is_nullable"] == "YES",
            "type": r["data_type"],
            "has_default": r["column_default"] is not None,
        }
    _shape = out
    return out


def _safe(name: str, allowed: dict[str, Any]) -> str:
    """A name that passed the regex AND exists on this database. Both, every time."""
    n = str(name or "").strip().lower()
    if not _IDENT.match(n) or n not in allowed:
        raise ValueError(f"{n!r} is not a column of this table on this database")
    return n


def _coerce(value: Any, pg_type: str) -> Any:
    """A spreadsheet cell as the column's type, or None when it plainly is not one.

    Refusing is the point. '' in a date column is not 1970 and not today; it is a blank cell,
    and writing anything for it invents a date the source never carried.
    """
    if value is None:
        return None
    s = str(value).strip()
    if s == "" or s.lower() in {"n/a", "na", "null", "-", "—"}:
        return None
    t = (pg_type or "").lower()
    try:
        if t in {"integer", "bigint", "smallint"}:
            return int(float(s.replace(",", "")))
        if t in {"numeric", "double precision", "real"}:
            return float(re.sub(r"[^0-9eE.\-+]", "", s.replace(",", "")) or "nan")
        if t == "boolean":
            if s.lower() in {"true", "yes", "y", "1"}:
                return True
            if s.lower() in {"false", "no", "n", "0"}:
                return False
            return None
        if t == "date":
            return value if isinstance(value, date) else _parse_date(s)
        if t.startswith("timestamp"):
            return value if isinstance(value, datetime) else _parse_date(s)
        if t in {"json", "jsonb"}:
            return value if isinstance(value, (dict, list)) else json.loads(s)
        if t == "uuid":
            return str(UUID(s))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    return s


#: Unambiguous by construction — the year leads, or the month is spelled.
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                 "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d-%B-%Y")
#: Ambiguous by construction — 03/04/2026 is 3 April to a British export and 4 March to an
#: American one, and the file does not say which.
_SLASH = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$")


def _parse_date(s: str, *, day_first: bool | None = None) -> datetime | None:
    """A date, or None when the string does not unambiguously name one.

    The numeric-first formats are handled separately and deliberately: 03/04/2026 is 3 April
    on a British export and 4 March on an American one, and nothing in the cell says which.
    Trying day-first and falling back to month-first does not resolve that — it silently
    prefers one reading and is right about half the time, which is how a PPM visit lands a
    month out and nobody can see why.

    So an ambiguous numeric date is refused unless the caller states the convention. A row
    refused by name can be fixed; a date quietly shifted by a month cannot even be found.
    """
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    m = _SLASH.match(s)
    if not m:
        return None
    a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    both_valid = a <= 12 and b <= 12
    if both_valid and day_first is None:
        return None
    day, month = (a, b) if (day_first or not both_valid and a > 12) else (b, a)
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _key_for(table: str, row: dict[str, Any]) -> list[str] | None:
    for key in NATURAL_KEYS.get(table) or []:
        if all(row.get(k) not in (None, "") for k in key):
            return key
    return None


async def write_rows(
    session: AsyncSession,
    table: str,
    rows: list[dict[str, Any]],
    *,
    organization_id: UUID | str | None = None,
    building_id: UUID | str | None = None,
    source: str = "import",
    source_reference: str | None = None,
    apply: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Insert or update rows in one table, keyed on whatever natural key they carry.

    Returns what it did AND what it refused, with the reason per row. A writer that reports
    only its successes leaves the caller believing a file imported cleanly when a third of it
    was dropped for a missing NOT NULL column.
    """
    sh = await shape(session)
    cols = sh.get(table)
    if not cols:
        return {"ok": False, "table": table,
                "error": f"{SCHEMA}.{table} does not exist on this database"}

    defaults = REQUIRED_DEFAULTS.get(table) or {}
    # What this table insists on having and cannot invent for itself.
    #
    # `id` is NOT excluded here. plenum_cafm.assets.id is NOT NULL with no default on
    # hoistra_test, so assuming the database generates its own key failed at insert with a
    # null-violation on a column nothing had been asked for — and only against a real
    # database, which is why the dry run could not show it. A uuid key with no default is
    # generated below; anything else is refused by name like any other missing column.
    required = [c for c, m in cols.items() if not m["nullable"] and not m["has_default"]]
    # Key columns of uuid type that the table will not fill for itself.
    generate = [c for c in required
                if cols[c]["type"] == "uuid" and (c == "id" or c == f"{table[:-1]}_id"
                                                  or c == f"{table}_id")]

    inserted = updated = skipped = 0
    refused: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []

    for i, raw_row in enumerate(rows or []):
        # Only columns this table actually has, coerced to the type it declares.
        row: dict[str, Any] = {}
        dropped: list[str] = []
        for k, v in (raw_row or {}).items():
            kk = str(k or "").strip().lower()
            if kk not in cols:
                dropped.append(str(k))
                continue
            row[kk] = _coerce(v, cols[kk]["type"])

        if organization_id and "organization_id" in cols:
            row.setdefault("organization_id", str(organization_id))
        if building_id and "building_id" in cols:
            row.setdefault("building_id", str(building_id))
        for p, val in (("source", source), ("source_reference", source_reference)):
            if val and p in cols and p in PROVENANCE:
                row.setdefault(p, val)

        # The lookup value has done its job once the id is filled; it is not a column here.
        for spec in (REFERENCES.get(table) or {}).values():
            if spec["from"] in row and spec["from"] not in cols:
                row.pop(spec["from"], None)

        key = _key_for(table, row)
        if key is None:
            skipped += 1
            refused.append({"row": i, "why": "no natural key on this row",
                            "needed_one_of": NATURAL_KEYS.get(table)})
            continue

        for c, val in defaults.items():
            if c in cols:
                row.setdefault(c, val)
        for c in generate:
            row.setdefault(c, str(uuid4()))

        missing = [c for c in required if row.get(c) is None]
        if missing:
            skipped += 1
            refused.append({"row": i, "why": "this table requires columns the row has no value for",
                            "missing": missing, "key": {k: row.get(k) for k in key}})
            continue

        where = " AND ".join(f"{_safe(k, cols)} = :k_{n}" for n, k in enumerate(key))
        params = {f"k_{n}": row[k] for n, k in enumerate(key)}
        if organization_id and "organization_id" in cols:
            where += " AND organization_id = CAST(:k_org AS uuid)"
            params["k_org"] = str(organization_id)

        existing = (await session.execute(
            sa_text(f"SELECT * FROM {SCHEMA}.{table} WHERE {where} LIMIT 1"), params)
        ).mappings().first()

        writable = [c for c in row if c not in key and c in cols]
        if existing:
            # An import fills blanks. It does not undo somebody's correction unless asked.
            fill = [c for c in writable
                    if overwrite or existing.get(c) in (None, "")]
            if not fill:
                skipped += 1
                planned.append({"row": i, "action": "no change", "key": params})
                continue
            sets = ", ".join(f"{_safe(c, cols)} = :v_{n}" for n, c in enumerate(fill))
            vals = {f"v_{n}": row[c] for n, c in enumerate(fill)}
            if "updated_at" in cols:
                sets += ", updated_at = now()"
            planned.append({"row": i, "action": "update", "key": params, "columns": fill})
            if apply:
                await session.execute(
                    sa_text(f"UPDATE {SCHEMA}.{table} SET {sets} WHERE {where}"), {**params, **vals})
            updated += 1
        else:
            names = [_safe(c, cols) for c in writable] + [_safe(k, cols) for k in key]
            vals = {f"v_{n}": row[c] for n, c in enumerate(writable)}
            vals.update({f"kk_{n}": row[k] for n, k in enumerate(key)})
            ph = ([f":v_{n}" for n in range(len(writable))]
                  + [f":kk_{n}" for n in range(len(key))])
            planned.append({"row": i, "action": "insert", "key": params, "columns": names})
            if apply:
                await session.execute(
                    sa_text(f"INSERT INTO {SCHEMA}.{table} ({', '.join(names)}) "
                            f"VALUES ({', '.join(ph)})"), vals)
            inserted += 1

        if dropped:
            planned[-1]["dropped_columns"] = dropped

    if apply:
        await session.commit()

    return {
        "ok": True, "table": table, "applied": apply,
        "inserted": inserted, "updated": updated, "skipped": skipped,
        "refused": refused,
        "plan": planned if not apply else planned[:50],
        "required_columns": required,
        "note": (f"{inserted} to insert, {updated} to update, {skipped} refused"
                 + (" — DRY RUN, nothing written" if not apply else "")),
    }


#: Which tables each domain owns, in the order they must be written. Order is not cosmetic:
#: a meter reading needs its meter, and a PPM visit reads better against an asset that exists.
DOMAIN_TABLES: dict[str, list[str]] = {
    "assets": ["assets"],
    "energy": ["energy_meters", "meter_readings"],
    "maintenance": ["work_orders", "ppm_visits", "inspections"],
}


def rows_for_table(mapped_rows: list[dict[str, Any]], table: str,
                   mapping: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The slice of each sheet row that belongs in one table.

    A sheet is not a table. A PPM export carries visit, work-order and inspection columns in
    one line, so the same line contributes a row to three tables — and the columns that belong
    to the other two must not be carried along, because a stray column is either dropped
    silently or refused loudly and neither is the answer.
    """
    cols = {m["header"]: m["lands"].split(".")[1]
            for m in mapping
            if m.get("lands") and m["lands"].split(".")[0] == table}
    # Two kinds of column ride along from other tables' mappings.
    #
    # The key columns, because a table's natural key is often supplied by a column that lands
    # elsewhere — asset_code identifies an inspection row as well as an asset row.
    #
    # And the columns a REFERENCE is looked up by. A reading joins on meter_id and the sheet
    # only ever says MPAN; that MPAN lands in meters, so without carrying it here the reading
    # arrives with nothing to resolve, fails for want of a natural key, and an energy import
    # writes meters and no readings — a file that looks imported and answers no question.
    wanted = {k for keys in NATURAL_KEYS.get(table, []) for k in keys}
    wanted |= {spec["from"] for spec in (REFERENCES.get(table) or {}).values()}
    shared = {m["header"]: m["lands"].split(".")[1]
              for m in mapping
              if m.get("lands") and m["lands"].split(".")[1] in wanted}
    cols.update(shared)
    out = []
    for r in mapped_rows or []:
        row = {col: r.get(h) for h, col in cols.items() if h in r}
        if any(v not in (None, "") for v in row.values()):
            out.append(row)
    return out


#: A sheet names things the way a person does — an MPAN, an asset code, a building name. The
#: tables join on ids. Each entry says: to fill this column, look up this value in this table.
REFERENCES: dict[str, dict[str, dict[str, str]]] = {
    "meter_readings": {
        "meter_id": {"from": "mpan", "table": "energy_meters", "match": "mpan", "select": "id"},
    },
    "ppm_visits": {
        "asset_id": {"from": "asset_code", "table": "assets", "match": "asset_code",
                     "select": "id"},
    },
    "work_orders": {
        "asset_id": {"from": "asset_code", "table": "assets", "match": "asset_code",
                     "select": "id"},
    },
    "inspections": {
        "asset_id": {"from": "asset_code", "table": "assets", "match": "asset_code",
                     "select": "id"},
    },
}


async def resolve_references(
    session: AsyncSession, table: str, rows: list[dict[str, Any]],
    *, organization_id: UUID | str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fill the id columns from the human-readable values a sheet actually carries.

    A meter reading joins on meter_id; the export has an MPAN. Without this the reading has no
    natural key, is refused, and an energy import writes meters and no readings — which is a
    file that looks imported and answers no question.

    A value that resolves to nothing is left alone rather than invented. The row then fails on
    its own missing column, by name, which is a fixable complaint; a fabricated id is a row
    pointing at the wrong meter forever.
    """
    sh = await shape(session)
    unresolved: list[dict[str, Any]] = []
    refs = REFERENCES.get(table) or {}
    if not refs:
        return rows, unresolved

    for target, spec in refs.items():
        if target not in (sh.get(table) or {}):
            continue
        src = spec["from"]
        wanted = sorted({str(r[src]) for r in rows if r.get(src) not in (None, "")})
        if not wanted:
            continue
        cols = sh.get(spec["table"]) or {}
        sel, match = _safe(spec["select"], cols), _safe(spec["match"], cols)
        where = f"{match}::text = ANY(CAST(:v AS text[]))"
        params: dict[str, Any] = {"v": wanted}
        if organization_id and "organization_id" in cols:
            where += " AND organization_id = CAST(:org AS uuid)"
            params["org"] = str(organization_id)
        found = (await session.execute(sa_text(
            f"SELECT {match}::text AS k, {sel}::text AS v "
            f"FROM {SCHEMA}.{_safe(spec['table'], {spec['table']: 1})} WHERE {where}"
        ), params)).mappings().all()
        by = {r["k"]: r["v"] for r in found}
        for i, r in enumerate(rows):
            val = r.get(src)
            if val in (None, ""):
                continue
            hit = by.get(str(val))
            if hit:
                r.setdefault(target, hit)
            else:
                unresolved.append({"row": i, "column": target, "looked_for": str(val),
                                   "in": f"{SCHEMA}.{spec['table']}.{spec['match']}",
                                   "why": "no such row, so the id was left empty rather than "
                                          "invented"})
    return rows, unresolved


async def write_domain(
    session: AsyncSession,
    domain: str,
    mapped_rows: list[dict[str, Any]],
    mapping: list[dict[str, Any]],
    **kw: Any,
) -> dict[str, Any]:
    """One domain's share of a sheet, written to every table that domain owns.

    Each table is reported separately. A run where assets wrote 400 rows and meter_readings
    refused 400 for a missing period is not a partial success to be averaged into a percentage;
    it is one table that worked and one that did not, and the caller needs both facts.
    """
    tables = DOMAIN_TABLES.get(domain)
    if not tables:
        return {"ok": False, "domain": domain,
                "error": f"no tables are declared for domain {domain!r}",
                "known": sorted(DOMAIN_TABLES)}
    results = []
    for t in tables:
        rows = rows_for_table(mapped_rows, t, mapping)
        if not rows:
            results.append({"ok": True, "table": t, "inserted": 0, "updated": 0, "skipped": 0,
                            "refused": [], "plan": [],
                            "note": "no column of this sheet maps to this table"})
            continue
        # Tables are written in order, so a reading resolves against the meter this same run
        # just wrote — but only when the run is real. On a dry run the meter does not exist
        # yet, and the unresolved list says so rather than the reading looking broken.
        rows, unresolved = await resolve_references(
            session, t, rows, organization_id=kw.get("organization_id"))
        res = await write_rows(session, t, rows, **kw)
        if unresolved:
            res["unresolved_references"] = unresolved
            res["note"] = (res.get("note", "") +
                           f" · {len(unresolved)} reference(s) not found"
                           + (" (expected on a dry run: the rows they point at are not "
                              "written yet)" if not kw.get("apply") else ""))
        results.append(res)
    return {
        "ok": all(r.get("ok") for r in results),
        "domain": domain,
        "applied": bool(kw.get("apply")),
        "tables": results,
        "totals": {
            "inserted": sum(r.get("inserted", 0) for r in results),
            "updated": sum(r.get("updated", 0) for r in results),
            "skipped": sum(r.get("skipped", 0) for r in results),
        },
    }
