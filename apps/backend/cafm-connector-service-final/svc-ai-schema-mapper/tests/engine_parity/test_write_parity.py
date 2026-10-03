"""The engine writes what the Python writer writes — row for row, on a fresh parity database.

Each scenario runs write_node._apply_records_with_schema_alignment on a fresh copy of the
template (uuid.uuid4 patched to the counter the engine's deterministic ids count), dumps every
plenum_cafm table, resets, runs `hoist-engine write` on the same cleaned tables, dumps again and
compares: every row of every table (columns that default to now() aside), every column's type,
and the result the node reads. Known differences are the ones the spec lists, asserted as such.
"""
from __future__ import annotations

import copy
import itertools
import json
import os
import uuid
from collections import defaultdict
from pathlib import Path

import pytest

from src.engine import client, steps, store
from src.graph.nodes import write_node as wn

from .conftest import DSN, reset_parity_run

ORG = "11111111-1111-4111-8111-111111111111"
B101 = "00000000-0000-4000-8000-0000000b0101"
SEED_B101 = (f"INSERT INTO plenum_cafm.buildings (building_id, organization_id, name, building_code) "
             f"VALUES ('{B101}', '{ORG}', 'Harbour Point', 'B-101')")

_RUN_ID_BLOCK = 500_000

pytestmark = pytest.mark.skipif(not client.engine_available(),
                                reason="hoist-engine not built (engine/scripts/dev.sh build-linux)")


# ── the two writers ───────────────────────────────────────────────────────────────────────

async def _seed(sqls: list[str]) -> None:
    import asyncpg

    conn = await asyncpg.connect(DSN)
    try:
        for q in sqls:
            await conn.execute(q)
    finally:
        await conn.close()


async def _dump() -> dict:
    import asyncpg

    conn = await asyncpg.connect(DSN)
    try:
        skip: dict[str, set] = defaultdict(set)
        for r in await conn.fetch(
                "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'plenum_cafm' "
                "AND (column_name = 'updated_at' OR column_default ILIKE '%now()%' "
                "OR column_default ILIKE '%current_timestamp%')"):
            skip[r["table_name"]].add(r["column_name"])
        out = {}
        for r in await conn.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema = "
                                  "'plenum_cafm' AND table_type = 'BASE TABLE' ORDER BY 1"):
            t = r["table_name"]
            cols = await conn.fetch("SELECT column_name, data_type FROM information_schema.columns WHERE "
                                    "table_schema = 'plenum_cafm' AND table_name = $1 ORDER BY ordinal_position", t)
            rows = await conn.fetch(f'SELECT to_jsonb(x)::text AS j FROM plenum_cafm."{t}" x')
            out[t] = {
                "columns": [f'{c["column_name"]} {c["data_type"]}' for c in cols],
                "rows": sorted(json.dumps({k: v for k, v in json.loads(x["j"]).items() if k not in skip[t]},
                                          sort_keys=True) for x in rows),
            }
        return out
    finally:
        await conn.close()


async def _python_write(sc: dict, monkeypatch, runs: int = 1) -> tuple[list[dict], dict]:
    await reset_parity_run()
    await _seed(sc.get("seed", []))
    results = []
    try:
        for run in range(runs):
            # Each run draws its own block of ids, on both sides: a row a re-run writes twice then
            # appears in the dump instead of colliding on the id its first write took.
            counter = itertools.count(1 + run * _RUN_ID_BLOCK)
            monkeypatch.setattr(uuid, "uuid4", lambda c=counter: uuid.UUID(f"00000000-0000-4000-9000-{next(c):012d}"))
            results.append(await wn._apply_records_with_schema_alignment(
                cleaned_tables=copy.deepcopy(sc["tables"]), organization_id=ORG, schema_name="plenum_cafm",
                table_routing=sc["routing"], approved_new_columns={t: set(c) for t, c in sc.get("approved", {}).items()},
                confirmed_hierarchies=[], default_building_id=sc.get("building"), beat=None))
    finally:
        monkeypatch.undo()
    return results, await _dump()


async def _go_write(sc: dict, tmp_path: Path, runs: int = 1, bulk_min_rows: int = 0) -> tuple[list[dict], dict]:
    await reset_parity_run()
    await _seed(sc.get("seed", []))
    cleaned = tmp_path / "cleaned"
    store.write_tables(cleaned, copy.deepcopy(sc["tables"]))
    results = []
    for i in range(runs):
        out = tmp_path / f"out{i}"
        job = steps.write_job(mode="apply", organization_id=ORG, default_building_id=sc.get("building"),
                              cleaned_dir=str(cleaned), out_dir=str(out), table_routing=sc["routing"],
                              approved_new_columns=sc.get("approved", {}), confirmed_hierarchies=[],
                              ddl_statements=[], column_renames=[], deterministic_ids=True)
        if i:
            job["deterministic_ids_from"] = i * _RUN_ID_BLOCK
        if bulk_min_rows:
            job["bulk_min_rows"] = bulk_min_rows
        results.append(await client.run_engine("write", job, workdir=out, env={"HOIST_ENGINE_DSN": DSN}))
    return results, await _dump()


def _comparable(result: dict) -> dict:
    keep = ("tables_written", "rows_skipped", "rows_merged", "buildings_linked", "meters_linked", "meters_created",
            "meters_matched", "meters_unlinked", "references")
    return {k: result.get(k) for k in keep}


def _same_row_errors(py: list[str], go: list[str]) -> None:
    """Python's 20 row errors, except that the engine also reports the line Python builds for an
    abandoned table and never reports — ahead of that table's errors, inside the same cap of 20."""
    rest = [e for e in go if "consecutive identical failures" not in e]
    assert rest == py[:len(rest)]
    assert len(py) - len(rest) == len(go) - len(rest) or len(go) == 20


def _uniform(tables: dict) -> dict:
    """Every row with every column of its table, in first-seen order — as preprocess leaves them."""
    out = {}
    for name, rows in tables.items():
        cols: list[str] = []
        for r in rows:
            cols += [k for k in r if k not in cols]
        out[name] = [{c: r.get(c) for c in cols} for r in rows]
    return out


async def _parity(sc: dict, monkeypatch, tmp_path, runs: int = 1, inserted_less_by: "int | tuple" = 0,
                  written_less_by: tuple = ()) -> tuple[list, list]:
    sc = {**sc, "tables": _uniform(sc["tables"])}
    py, py_db = await _python_write(sc, monkeypatch, runs)
    assert any(p["rows_inserted"] or p["rows_merged"] or p["rows_skipped"] for p in py), "the scenario wrote nothing"
    # The engine twice: as it runs by default, and with the bulk load taking every run of chunks
    # it can (bulk_min_rows=1) — the bulk load may change nothing but the bytes on the wire.
    for bulk in (0, 1):
        go, go_db = await _go_write(sc, tmp_path / f"bulk{bulk}", runs, bulk_min_rows=bulk)
        assert set(py_db) == set(go_db)
        for t in py_db:
            assert go_db[t]["columns"] == py_db[t]["columns"], f"{t}: columns differ (bulk={bulk})"
            assert go_db[t]["rows"] == py_db[t]["rows"], f"{t}: rows differ (bulk={bulk})"
        for i, (p, g) in enumerate(zip(py, go)):
            # tables_written follows rows_inserted (the spec's intentional change): a table whose rows
            # ON CONFLICT DO NOTHING all skipped wrote nothing, though Python counts its batch.
            less = written_less_by[i] if i < len(written_less_by) else 0
            assert {**_comparable(g), "tables_written": g["tables_written"] + less} == _comparable(p)
            _same_row_errors(p["row_errors"], g["row_errors"])
            # Python counts every row of a batch that succeeded, ON CONFLICT DO NOTHING ones too.
            fewer = inserted_less_by[i] if isinstance(inserted_less_by, tuple) else inserted_less_by
            assert g["rows_inserted"] == p["rows_inserted"] - fewer
    return py, go


# ── scenarios ─────────────────────────────────────────────────────────────────────────────

async def test_assets_buildings_merge(parity_db, monkeypatch, tmp_path):
    sc = {"seed": [SEED_B101,
                   f"INSERT INTO plenum_cafm.assets (id, organization_id, asset_name, asset_code) VALUES "
                   f"('00000000-0000-4000-8000-0000000a0001', '{ORG}', 'Old pump', 'P-1')"],
          "routing": {"Assets": "assets"},
          "tables": {"Assets": [
              {"asset_code": "P-1", "asset_name": "Pump 1", "site": "Harbour Point", "serial_number": None},
              {"asset_code": "P-2", "asset_name": "Pump 2", "site": "B-101", "serial_number": "S-2"},
              {"asset_code": "P-3", "asset_name": "Fan", "site": "Nowhere", "serial_number": ""},
              {"asset_code": "P-1", "asset_name": "Pump 1 again", "site": "B-101", "serial_number": None},
          ]}}
    py, _go = await _parity(sc, monkeypatch, tmp_path)
    assert py[0]["rows_merged"] == 2 and py[0]["buildings_linked"] == 3


async def test_work_orders_vendors_references(parity_db, monkeypatch, tmp_path):
    sc = {"seed": [SEED_B101],
          "routing": {"Vendors": "vendors", "Assets": "assets", "WOs": "work_orders"},
          "tables": {
              "WOs": [
                  {"wo_code": "W-1", "asset_id": "A-1", "vendor_name": "acme ltd", "description": "Fix pump", "priority": "High"},
                  {"wo_code": "W-2", "asset_id": "A-404", "vendor_name": "Nobody", "description": None, "priority": None},
                  {"wo_code": "W-3", "asset_id": "a-1", "vendor_name": "Nobody", "description": "Third", "priority": "Low"},
              ],
              "Vendors": [{"vendor_code": "V1", "vendor_name": "Acme Ltd"}, {"vendor_code": "V2", "vendor_name": "Bolt"}],
              "Assets": [{"asset_code": "A-1", "asset_name": "Pump", "site": "B-101"}],
          }}
    py, _go = await _parity(sc, monkeypatch, tmp_path)
    assert py[0]["references"]["resolved"] >= 2


async def test_meters_and_readings(parity_db, monkeypatch, tmp_path):
    readings = []
    for mpan, site in (("M1", "B-101"), ("M2", "B-101"), ("M3", "B-101"), ("M9", None)):
        for h in range(3):
            readings.append({"mpan": mpan, "timestamp": f"2026-01-01T{h:02d}:00:00", "kwh": "1.5", "site": site})
    sc = {"seed": [SEED_B101],
          "routing": {"Meters": "energy_meters", "Readings": "meter_readings"},
          "tables": {
              "Readings": readings,
              "Meters": [{"mpan": "M1", "site": "B-101", "meter_type": "electricity"},
                         {"mpan": "M2", "site": "B-101", "sub_meter": "yes", "fuel": "gas"},
                         {"mpan": "M1", "site": "B-101", "meter_type": ""}],
          }}
    py, _go = await _parity(sc, monkeypatch, tmp_path)
    assert py[0]["meters_created"] >= 1 and py[0]["meters_unlinked"] == ["M9"]


async def test_sections_and_floors(parity_db, monkeypatch, tmp_path):
    sc = {"seed": [SEED_B101,
                   f"INSERT INTO plenum_cafm.floors (floor_id, building_id, level, name) VALUES "
                   f"('00000000-0000-4000-8000-0000000f0001', '{B101}', 1, 'Level 1'), "
                   f"('00000000-0000-4000-8000-0000000f0002', '{B101}', 2, 'Level 2')"],
          "routing": {"Sections": "building_sections", "Meters": "energy_meters", "Assets": "assets"},
          "tables": {
              "Assets": [{"asset_code": "A-1", "asset_name": "AHU", "site": "B-101", "section": "East Wing"}],
              "Meters": [{"mpan": "M5", "site": "B-101", "section": "east wing"}],
              "Sections": [{"site": "B-101", "name": "East Wing", "floor_name": "Level 1"},
                           {"site": "B-101", "name": "West Wing", "floor": "Level 9"}],
          }}
    await _parity(sc, monkeypatch, tmp_path)


async def test_type_mismatch_and_widening(parity_db, monkeypatch, tmp_path):
    sc = {"seed": ["CREATE TABLE plenum_cafm.custom_gauges (id uuid PRIMARY KEY, organization_id uuid, label text, "
                   "reading integer, factor numeric(5,2))"],
          "routing": {"Parts": "spare_parts", "Gauges": "custom_gauges"},
          "tables": {
              "Parts": [{"part_code": "SP-1", "part_name": "Belt", "reorder_level": "lots", "unit_price": "12.5",
                         "stock_quantity": "x", "max_quantity": "T9"},
                        {"part_code": "SP-2", "part_name": "Fuse", "reorder_level": "3", "unit_price": "1,200",
                         "stock_quantity": 0, "max_quantity": "40"}],
              "Gauges": [{"label": "G1", "reading": "T001", "factor": "999.999"}, {"label": "G2", "reading": "17", "factor": "1.5"},
                         {"label": "G3", "reading": 0, "factor": "1e3"}],
          }}
    await _parity(sc, monkeypatch, tmp_path)


async def test_orphans_and_overlong(parity_db, monkeypatch, tmp_path):
    sc = {"routing": {"Assets": "assets", "Vendors": "vendors"},
          "tables": {
              "Assets": [{"asset_code": "A-1", "asset_name": "Fan", "location_id": "00000000-0000-4000-8000-00000000dead"},
                         {"asset_code": "A-2", "asset_name": "Fan 2", "location_id": "00000000-0000-4000-8000-00000000dead",
                          "parent_asset_id": "00000000-0000-4000-8000-00000000beef"}],
              "Vendors": [{"vendor_code": "V1", "vendor_name": "Acme"}, {"vendor_code": "V2", "vendor_name": "x" * 400},
                          {"vendor_code": "V3", "vendor_name": "Bolt"}],
          }}
    await _parity(sc, monkeypatch, tmp_path)


async def test_duplicates_inside_the_file(parity_db, monkeypatch, tmp_path):
    sc = {"routing": {"Vendors": "vendors", "Assets": "assets"},
          "tables": {
              "Vendors": [{"vendor_code": "V1", "vendor_name": "First", "city": None},
                          {"vendor_code": "V2", "vendor_name": "Second", "city": "Leeds"},
                          {"vendor_code": "V2", "vendor_name": "Third", "city": None},
                          {"vendor_code": "V4", "vendor_name": "Fourth", "city": None},
                          {"vendor_code": "V4", "vendor_name": "Fourth again", "city": None}],
              "Assets": [{"asset_code": "X1", "serial_number": "S1", "asset_name": "First", "model": None},
                         {"asset_code": "X2", "serial_number": "S1", "asset_name": "Second", "model": "M2"}],
          }}
    # Two in-file duplicates are skipped by ON CONFLICT DO NOTHING. "V2 Second" goes alone (its
    # shape differs), so Python's single execute counts it as 0; "V4 Fourth again" shares a batch
    # with the rows before it, and Python counts the whole successful batch.
    await _parity(sc, monkeypatch, tmp_path, inserted_less_by=1)


async def test_a_table_failing_the_same_way_stops(parity_db, monkeypatch, tmp_path):
    # 650 readings name a meter no building can create, then 20 name one that can be made: the
    # first chunk stops at its 100th identical failure and the table stops with it.
    rows = [{"mpan": "M9", "timestamp": f"2026-01-{1 + i // 48:02d}T{(i % 48) // 2:02d}:{30 * (i % 2):02d}:00",
             "kwh": "1", "site": None} for i in range(650)]
    rows += [{"mpan": "M1", "timestamp": f"2026-02-01T{h:02d}:00:00", "kwh": "2", "site": "B-101"} for h in range(20)]
    sc = {"seed": [SEED_B101], "routing": {"Readings": "meter_readings"}, "tables": {"Readings": rows}}
    py, go = await _parity(sc, monkeypatch, tmp_path)
    assert py[0]["rows_skipped"] == 500 and py[0]["rows_inserted"] == 0
    assert "stopped after 100 consecutive identical failures" in go[0]["row_errors"][0]


async def test_rows_already_on_file_end_a_failure_streak(parity_db, monkeypatch, tmp_path):
    # A re-upload: 150 readings already on file (ON CONFLICT DO NOTHING writes nothing for them)
    # alternate with 150 readings that fail the same way (consumption_kwh overflows numeric(14,6)),
    # all of one shape so they share a chunk, then 10 new readings. Python ends the failure streak
    # after every row that ran without an error, written or not, so the chunk never reaches 100
    # identical failures in a row and the new readings go in.
    meter = "00000000-0000-4000-8000-00000000e001"
    seed = [SEED_B101,
            f"INSERT INTO plenum_cafm.energy_meters (id, organization_id, building_id, meter_type, mpan) "
            f"VALUES ('{meter}', '{ORG}', '{B101}', 'electricity', 'M1')",
            f"INSERT INTO plenum_cafm.meter_readings (organization_id, meter_id, reading_at, consumption_kwh) "
            f"SELECT '{ORG}', '{meter}', timestamptz '2026-03-01 00:00:00+00' + i * interval '30 minutes', 1 "
            f"FROM generate_series(0, 149) AS i"]
    rows = []
    for i in range(150):
        rows.append({"mpan": "M1", "timestamp": f"2026-03-{1 + i // 48:02d}T{(i % 48) // 2:02d}:{30 * (i % 2):02d}:00",
                     "kwh": "1", "site": "B-101"})
        rows.append({"mpan": "M1", "timestamp": f"2026-04-{1 + i // 48:02d}T{(i % 48) // 2:02d}:{30 * (i % 2):02d}:00",
                     "kwh": "999999999", "site": "B-101"})
    rows += [{"mpan": "M1", "timestamp": f"2026-05-01T{h:02d}:00:00", "kwh": "2", "site": "B-101"} for h in range(10)]
    sc = {"seed": seed, "routing": {"Readings": "meter_readings"}, "tables": {"Readings": rows}}
    py, go = await _parity(sc, monkeypatch, tmp_path)
    assert not any("consecutive identical failures" in e for e in py[0]["row_errors"])

    import asyncpg

    conn = await asyncpg.connect(DSN)
    try:
        assert await conn.fetchval("SELECT count(*) FROM plenum_cafm.meter_readings") == 160
    finally:
        await conn.close()


async def test_rerun_same_file(parity_db, monkeypatch, tmp_path):
    sc = {"seed": [SEED_B101],
          "routing": {"Vendors": "vendors", "Assets": "assets", "WOs": "work_orders", "Buildings": "buildings",
                      "Sections": "building_sections", "Meters": "energy_meters", "Readings": "meter_readings"},
          "tables": {
              "Buildings": [{"building_code": "B-101", "name": "Harbour Point"}],
              "Sections": [{"site": "B-101", "name": "East Wing"}],
              "Vendors": [{"vendor_code": "V1", "vendor_name": "Acme"}],
              "Assets": [{"asset_code": "A-1", "asset_name": "Fan", "site": "B-101"}],
              "WOs": [{"wo_code": "W-1", "asset_id": "A-1", "vendor_name": "Acme", "description": "Fix"}],
              "Meters": [{"mpan": "M1", "site": "B-101", "meter_type": "electricity"}],
              "Readings": [{"mpan": "M1", "timestamp": f"2026-01-01T{h:02d}:00:00", "kwh": "1.5", "site": "B-101"}
                           for h in range(3)],
          }}
    # Run 2: the readings are all on file; Python counts their batch as a table written.
    py, go = await _parity(sc, monkeypatch, tmp_path, runs=2, inserted_less_by=(0, 3), written_less_by=(0, 1))
    assert go[1]["rows_merged"] == 1 and py[1]["rows_merged"] == 1


async def test_tz_values(parity_db, monkeypatch, tmp_path):
    sc = {"routing": {"WOs": "work_orders"},
          "tables": {"WOs": [
              {"wo_code": "W-1", "title": "naive", "sla_due_at": "2026-01-01 10:00:00", "raised_at": "2026-01-01T10:00:00"},
              {"wo_code": "W-2", "title": "offset", "sla_due_at": "2026-01-01T10:00:00", "raised_at": "2026-01-01T10:00:00+05:30"},
              {"wo_code": "W-3", "title": "zulu", "sla_due_at": "2026-03-29T01:30:00", "raised_at": "2026-03-29T01:30:00Z"},
              {"wo_code": "W-4", "title": "aware into naive", "sla_due_at": "2026-01-01T10:00:00+01:00", "raised_at": None},
              {"wo_code": "W-5", "title": "week date", "sla_due_at": "2026-W01-1", "raised_at": "2026-01-01T10:00:00.123456-02:00"},
          ]}}
    await _parity(sc, monkeypatch, tmp_path)


async def test_uuid_spellings(parity_db, monkeypatch, tmp_path):
    sc = {"seed": ["CREATE TABLE plenum_cafm.custom_links (id uuid PRIMARY KEY, organization_id uuid, label text, ref uuid)"],
          "routing": {"Links": "custom_links"},
          "tables": {"Links": [
              {"label": "upper", "ref": "ABCDEF00-0000-4000-8000-000000000001"},
              {"label": "braces", "ref": "{abcdef00-0000-4000-8000-000000000002}"},
              {"label": "short", "ref": "abcdef000000400080000000000003"},
              {"label": "bare", "ref": "abcdef00000040008000000000000004"},
              {"label": "urn", "ref": "urn:uuid:abcdef00-0000-4000-8000-000000000005"},
              {"label": "odd dashes", "ref": "abcdef0-00000-4000-8000-000000000007"},
              {"label": "arabic digits", "ref": "١" * 32},
              {"label": "not hex", "ref": "zzzzzzzz-0000-4000-8000-000000000008"},
          ]}}
    await _parity(sc, monkeypatch, tmp_path)


async def test_invalid_json(parity_db, monkeypatch, tmp_path):
    sc = {"seed": ["CREATE TABLE plenum_cafm.custom_docs (id uuid PRIMARY KEY, organization_id uuid, label text, "
                   "doc json, docb jsonb)"],
          "routing": {"Docs": "custom_docs"},
          "tables": {"Docs": [
              {"label": "valid", "doc": '{"a": 1}', "docb": '{"a": [1, 2]}'},
              {"label": "broken", "doc": "{bad", "docb": '{"b": 2}'},
              {"label": "nul in jsonb", "doc": '{"a": "\\u0000"}', "docb": '{"a": "\\u0000"}'},
              {"label": "filled zero", "doc": 0, "docb": 0},
              {"label": "plain text", "doc": "hello", "docb": "hello"},
          ]}}
    await _parity(sc, monkeypatch, tmp_path)


async def test_northbridge_b101(parity_db, monkeypatch, tmp_path):
    src = Path(os.environ.get("HOIST_PARITY_B101_TABLES", "/data/b101_cleaned.json"))
    if not src.exists():
        pytest.skip("the B-101 workbook's cleaned tables are not mounted (HOIST_PARITY_B101_TABLES)")
    payload = json.loads(src.read_text())
    sc = {"seed": [SEED_B101], "routing": payload["table_routing"], "tables": payload["cleaned_tables"],
          "building": B101}
    await _parity(sc, monkeypatch, tmp_path)


async def test_a_rerun_adds_no_contracts_bands_readings_or_plans(parity_db, monkeypatch, tmp_path):
    # These four had no natural key, so every re-upload of the same workbook added them again
    # (3 Oct 2026: three Bishopsgate uploads, three copies of 15 contracts, 23 bands, 1,200 asset
    # readings and 60 plans). The second run must write none of them, on either writer.
    sc = {"seed": [SEED_B101],
          "routing": {"Vendors": "vendors", "Assets": "assets", "Contracts": "vendor_contracts",
                      "Bands": "asset_reading_bands", "AssetReadings": "asset_readings", "Plans": "maintenance_plans"},
          "tables": {
              "Vendors": [{"vendor_code": "V1", "vendor_name": "Apex Mechanical"}],
              "Assets": [{"asset_code": "A-1", "asset_name": "Chiller 1", "site": "B-101"},
                         {"asset_code": "A-2", "asset_name": "AHU 2", "site": "B-101"}],
              "Contracts": [{"contract_name": "Mechanical PPM", "vendor_code": "V1", "contract_start": "2026-01-01",
                             "contract_end": "2026-12-31", "status": "active"},
                            {"contract_name": "Mechanical PPM", "vendor_code": "V1", "contract_start": "2027-01-01",
                             "contract_end": "2027-12-31", "status": "active"}],          # a renewal: its own contract
              "Bands": [{"reading_type": "temperature", "unit": "°C", "lo": "5.0", "hi": "95.0", "note": "plant"},
                        {"reading_type": "pressure", "unit": "bar", "lo": "1.0", "hi": "6.0", "note": "loop"}],
              "AssetReadings": [{"asset_code": "A-1", "reading_type": "temperature", "value": str(80 + h), "unit": "°C",
                                 "recorded_at": f"2026-09-27T{h:02d}:00:00"} for h in range(3)],
              "Plans": [{"sm_code": "PPM-A-1", "asset_code": "A-1", "description": "Chiller PPM",
                         "maintenance_type": "preventive", "frequency_type": "months", "frequency_value": "3",
                         "status": "active"},
                        {"sm_code": "PPM-A-2", "asset_code": "A-2", "description": "AHU PPM",
                         "maintenance_type": "preventive", "frequency_type": "months", "frequency_value": "1",
                         "status": "active"}],
          }}
    py, go = await _parity(sc, monkeypatch, tmp_path, runs=2)

    import asyncpg

    conn = await asyncpg.connect(DSN)
    try:
        counts = {t: await conn.fetchval(f"SELECT count(*) FROM plenum_cafm.{t}")
                  for t in ("vendor_contracts", "asset_reading_bands", "asset_readings", "maintenance_plans")}
    finally:
        await conn.close()
    assert counts == {"vendor_contracts": 2, "asset_reading_bands": 2, "asset_readings": 3, "maintenance_plans": 2}
    run2 = {t["dest"]: t for t in go[1]["tables"]}
    for dest, n in (("vendor_contracts", 2), ("asset_reading_bands", 2), ("asset_readings", 3), ("maintenance_plans", 2)):
        assert run2[dest]["inserted"] == 0 and run2[dest]["already_present"] == n, run2[dest]
