"""The engine applies every per-row write rule exactly as the Python writer does.

Python is the oracle: a generated corpus of rows goes through the real functions
(_normalize_row_for_table, the building/meter/reference hints, _to_safe_identifier,
_natural_keys_for, asset_match_code, _system_default_for_db_type, _ordered_source_tables,
site_names_from_run) and through `hoist-engine rules-eval`; every answer must be identical."""
import random
import uuid

import pytest

from src.engine.client import engine_available, run_engine
from src.engine.rules_spec import write_rules
from src.graph.nodes import building_link as bl
from src.graph.nodes import meter_link as ml
from src.graph.nodes import reference_link as rl
from src.graph.nodes import write_node as wn
from tests.test_engine_coerce_oracle import _encode

pytestmark = pytest.mark.skipif(not engine_available(), reason="hoist-engine binary not mounted")

KEYS = ["asset_id", "id", "asset_type", "asset_code", "site_id", "location", "location_code", "category",
        "serial", "serial_number", "install_date", "installation_date", "asset_name", "name", "asset",
        "site_name", "location_name", "site_type", "type", "mpan", "mprn", "MPAN Core", "Supply No.",
        "Meter Reference", "meter_ref", "meter_id", "meter_type", "Fuel", "fuel_type", "utility", "is_sub_meter",
        "Sub Meter", "meter_level", "floor", "floor_name", "Level", "zone", "section", "timestamp", "reading_date",
        "reading_at", "kwh", "consumption", "consumption_kwh", "value", "wo_code", "work_order_number",
        "work_order_id", "order_number", "description", "title", "vendor_name", "Contractor", "vendor_code",
        "supplier", "contract_name", "contract_ref", "part_code", "Part Number", "building", "building_id",
        "building_name", "building_code", "site_ref", "site", "site_code", "location_site", "dcc_device_id",
        "Asset Code", "Equipment ID", "plant_ref", "msn", "serial_number"]
VALUES = [None, 0, "", "  ", "A-1", "Gas", "Electricity", "Elec supply", "therm meter", "1200034567890",
          "G-77", "2025-01-01", "Pump", "sub", "main", "yes", "no", "tenant", "Level 3", "B-101",
          "Bishopsgate Tower", "  S-01 ", str(uuid.UUID(int=7)), "UPPER CASE", "kVA feed", "MPRN 99"]
TABLES = ["assets", "locations", "energy_meters", "meter_readings", "work_orders", "vendors",
          "building_sections", "ppm_visits", "compliance_certificates", "custom_table"]
DB_TYPES = ["text", "integer", "numeric", "boolean", "uuid", "date", "timestamp without time zone", "jsonb",
            "character varying", "USER-DEFINED", "interval", "point", "double precision", "real"]


def _cell(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return {"b": v}
    if isinstance(v, int):
        return {"i": v}
    return {"s": v}


def _row(rnd):
    keys = rnd.sample(KEYS, rnd.randint(3, 12))
    return {k: rnd.choice(VALUES) for k in keys}


def _wire_row(row):
    return [[k, _cell(v)] for k, v in row.items()]


def _coerced(row_types):
    out = {}
    for k, v, t in row_types:
        if v is None or str(v) == "":
            continue
        c = wn._coerce_value_for_db_type(v, t)
        if c is wn._COERCE_TYPE_MISMATCH or c is None:
            continue
        out[k] = c
    return out


def _cases():
    rnd = random.Random(20261001)
    cases, want = [], []
    org = "11111111-1111-4111-8111-111111111111"
    for _ in range(600):
        row = _row(rnd)
        table = rnd.choice(TABLES)
        cases.append({"fn": "normalize", "table": table, "row": _wire_row(row), "org": rnd.choice([org, ""])})
        want.append([[k, _cell(v)] for k, v in wn._normalize_row_for_table(table, row, cases[-1]["org"]).items()])
        for fn, py in (("building_hint", bl.building_hint), ("meter_hint", ml.meter_hint),
                       ("section_hint", ml.section_hint), ("floor_hint", ml.floor_hint),
                       ("meter_type_for", ml.meter_type_for), ("is_sub_meter_for", ml.is_sub_meter_for)):
            cases.append({"fn": fn, "row": _wire_row(row)})
            want.append(py(row))
        cases.append({"fn": "supply_numbers", "row": _wire_row(row)})
        want.append(list(ml.supply_numbers(row)))
        for col in rl.REFERENCES:
            cases.append({"fn": "reference_hint", "column": col, "row": _wire_row(row)})
            want.append(rl.hint_for(col, row))
    for raw in KEYS + ["", "  Spaced Name  ", "9lives", "a" * 70, "Ünïcode Ünit", "x-y z", "__", "_ok", "CamelCase"]:
        cases.append({"fn": "safe_ident", "raw": raw})
        want.append(wn._to_safe_identifier(raw))
    for _ in range(300):
        table = rnd.choice(list(wn._NATURAL_KEYS) + ["assets", "custom_table"])
        cols = sorted({c for g in wn._NATURAL_KEYS.get(table, ()) for c in g} | {"asset_code", "id", "name"})
        row_types = [(c, rnd.choice(VALUES + ["0", "00", "1.50", "True"]), rnd.choice(DB_TYPES)) for c in cols]
        db_cols = sorted(c for c in cols if rnd.random() < 0.8)
        filtered = _coerced(row_types)
        cases.append({"fn": "natural_keys", "table": table, "db_cols": db_cols,
                      "filtered": [[k, _cell(v), t] for k, v, t in row_types]})
        want.append([[list(g), [_encode(x, t) for x, t in zip(vals, [dict((k, t) for k, _, t in row_types)[c] for c in g])]]
                     for g, vals in wn._natural_keys_for(table, filtered, set(db_cols))])
        cases.append({"fn": "asset_match_code", "filtered": [[k, _cell(v), t] for k, v, t in row_types]})
        want.append(bl.asset_match_code(filtered))
    for col in ["org_id", "organization_id", "source", "origin", "created_by", "source_system", "flag", "note", "qty"]:
        for t in DB_TYPES + ["bigint", "smallint", "json", "bool", "money"]:
            for o in (org, ""):
                cases.append({"fn": "system_default", "col": col, "db_type": t, "org": o})
                v = wn._system_default_for_db_type(col, t, o or None)
                want.append(None if v is wn._NO_SYSTEM_DEFAULT else _cell(v))
    names = ["Assets", "Work Orders", "Vendors", "Buildings", "Sections", "Meters", "Readings", "PPM",
             "Contracts", "Sites", "Custom"]
    dests = ["assets", "work_orders", "vendors", "buildings", "building_sections", "energy_meters",
             "meter_readings", "ppm_visits", "vendor_contracts", "sites", "custom_table"]
    for _ in range(80):
        srcs = rnd.sample(names, rnd.randint(1, len(names)))
        routing = {s: rnd.choice(dests) for s in srcs if rnd.random() < 0.85}
        hier = [{"source_table": rnd.choice(srcs), "target_table": rnd.choice(srcs)} for _ in range(rnd.randint(0, 4))]
        cleaned = {s: ([{"a": "1"}] if rnd.random() < 0.9 else []) for s in srcs}
        cases.append({"fn": "write_order", "sources": [s for s in srcs if cleaned[s]], "routing": routing,
                      "hierarchies": hier})
        want.append(wn._ordered_source_tables(cleaned, routing, hier))
    for _ in range(60):
        tables = {}
        for s in rnd.sample(["Sites", "sites_2", "Buildings", "Locations", "Assets"], rnd.randint(1, 4)):
            tables[s] = [{k: rnd.choice(VALUES) for k in rnd.sample(
                ["site_name", "building_name", "name", "site_id", "site_code", "building_code", "id", "code", "site_ref"],
                rnd.randint(1, 6))} for _ in range(rnd.randint(1, 5))]
        routing = {s: rnd.choice(["sites", "buildings", "locations", "assets", "site"]) for s in tables if rnd.random() < 0.7}
        cases.append({"fn": "site_names", "tables": [[n, [_wire_row(r) for r in rows]] for n, rows in tables.items()],
                      "routing": routing})
        want.append([[k, v] for k, v in bl.site_names_from_run(tables, routing).items()])
    for v in VALUES + ["0E9A1C1E-6B6F-4F0A-9D0E-8F3B2A1C4D5E", " 0e9a1c1e-6b6f-4f0a-9d0e-8f3b2a1c4d5e\n", "{x}"]:
        cases.append({"fn": "looks_like_uuid", "value": _cell(v)})
        want.append(bl.looks_like_uuid(v))
    return cases, want


async def test_the_engine_applies_the_write_rules_exactly_like_python(tmp_path):
    cases, want = _cases()
    out = await run_engine("rules-eval", {"spec": write_rules(), "cases": cases}, workdir=tmp_path)
    got = out["results"]
    assert len(got) == len(cases)
    diffs = [f"{c['fn']} {c}: python {w!r} engine {g!r}" for c, w, g in zip(cases, want, got) if w != g]
    assert not diffs, f"{len(diffs)} of {len(cases)} differ:\n" + "\n".join(d[:400] for d in diffs[:15])
