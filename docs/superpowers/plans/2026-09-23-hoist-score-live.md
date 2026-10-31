# Hoist Score Live Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Home page's Hoist Score reads, per hoisted building, whether an asset register, a contract, a meter and a certificate have been ingested, and moves as each file lands.

**Architecture:** `svc-operations-intelligence` already scores one building on five domains from graph counts (`buildings.hoist_score_for`). Two of those counts miss the tables the ingests actually write; fix them, then add one read, `GET /api/energy/hoist-score`, that aggregates the per-building result into portfolio bars. The frontend's `homeLive.js` reads that one endpoint instead of deriving four unlike ratios in the browser.

**Tech Stack:** Python 3.11+ / FastAPI / SQLAlchemy async (backend, pytest runs locally with the system Python 3.14); React + plain JS view-model with `node --test` (frontend, Node 24).

**Spec:** `docs/superpowers/specs/2026-09-23-hoist-score-live-design.md`

## Global Constraints

- **No database writes and no direct SQL from this session.** Verification against `hoistra_test` is Hussain's (memory: `no-database-writes`).
- **No git commits, stashes, branch switches or pushes** from this session; Hussain commits (memory: `no-commits-without-asking`). Every "Commit" step below is therefore "leave uncommitted; report".
- **The ops-intelligence container has no source mount.** Backend changes reach `:3000` only after `docker compose -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml up -d --build svc-operations-intelligence`, run by Hussain (the base file alone turns startup migrations and pack seeding on against Azure).
- The dev frontend on `:5174` bind-mounts `apps/frontend`, so frontend edits are live there; the built `frontend-app` behind `:3000` needs the same `--build` for `frontend-app`.
- Backend tests: `cd apps/backend/cafm-connector-service-final/svc-operations-intelligence && python3 -m pytest tests/unit/test_c_hoist_score.py tests/unit/test_c_building_graph.py -q` (baseline 30 passed).
- Frontend tests: `cd apps/frontend && node --test test/homeLive.test.mjs` (baseline 14 passed). Do not run the whole `npm test`: other uncommitted work on the `test` branch has its own test files.
- The Home tile keeps four bars: Contracts, Assets, Meter consent, Certificates. Bands unchanged: `>= 85` Delegated, `>= 60` Supervised, else Ingestion in progress.
- `spacesLive.js` reads `homeRaw.compliance`, `homeRaw.anomalies`, `homeRaw.approvals` — those three reads stay in `homeLoad()`. Only `contracts` and `meters` are dropped.

## Review Focus

1. A building whose stored `hoist_score` is `0` (what `create_building` used to write) is treated as `recorded` by `shape_building_row`; the portfolio bars must still be derived from `graph_counts`, never from that stored figure. → Task 2 test `test_a_recorded_score_does_not_decide_which_records_exist`.
2. A company with buildings hoisted but `plenum_cafm.buildings` empty (`root: "sites"`) must show "not counted", not 0%. → Task 2 test `test_a_table_rooted_on_sites_counts_nothing_and_says_so`; Task 4 test `a coverage read rooted on sites leaves every bar unsourced and says why`.
3. A one-building user must get a score over their own building only. → Task 3 test `test_a_restricted_caller_scores_only_their_buildings`.
4. Zero hoisted buildings must not read as 0% coverage or divide by zero. → Task 2 test `test_no_buildings_is_not_zero_coverage`; Task 4 test `no buildings hoisted is said in words, not as 0%`.
5. A meter that arrived by half-hourly CSV upload lives in `energy_meters`, not `meters`; the energy domain must count it. → Task 1 test `test_a_meter_created_by_a_readings_upload_counts_as_energy`.

---

### Task 1: Count contracts and meters where the ingests actually write them

**Files:**
- Modify: `apps/backend/cafm-connector-service-final/svc-operations-intelligence/src/engines/energy/building_rollup.py` (`_KEYS` at ~line 30; new builders after `invoices_count_sql`; `child_counts` at ~line 263)
- Test: `apps/backend/cafm-connector-service-final/svc-operations-intelligence/tests/unit/test_c_hoist_score.py`

**Interfaces:**
- Produces: `contracts_count_sql(shape: dict) -> list[str]`, `meters_count_sql(shape: dict) -> list[str]`, `merge_counts(*parts: dict[str, int]) -> dict[str, int]`. `child_counts()` keeps returning `{building_id: {relation: n}}` with relations `"contracts"` and `"meters"` unchanged in name.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_c_hoist_score.py`:

```python
from src.engines.energy import building_rollup
from src.engines.energy.building_rollup import (
    contracts_count_sql,
    merge_counts,
    meters_count_sql,
)


def _shape(**tables):
    """A graph shape holding only the named tables. Each value is that table's column set;
    the key column is the first of building_rollup._KEYS' candidates present in it."""
    shape = {t: {"exists": False, "key": None, "columns": set()} for t in building_rollup._KEYS}
    for table, cols in tables.items():
        key = next((c for c in building_rollup._KEYS[table] if c in cols), None)
        shape[table] = {"exists": bool(key), "key": key, "columns": set(cols)}
    return shape


class TestWhatTheCountsRead:
    """The per-building domains are only as true as the tables they count."""

    def test_a_contract_reaches_its_building_through_the_document_it_was_read_from(self):
        shape = _shape(contract_sla_parameters={"id", "document_id"},
                       documents={"document_id", "building_id"})
        sql = contracts_count_sql(shape)
        assert len(sql) == 1
        assert "FROM plenum_cafm.contract_sla_parameters p" in sql[0]
        assert "d.document_id::text = p.document_id::text" in sql[0]
        assert sql[0].lstrip().startswith("SELECT d.building_id::text, count(*)")

    def test_the_graphs_own_contracts_table_is_still_counted_when_it_exists(self):
        shape = _shape(contracts={"contract_id", "building_id"},
                       contract_sla_parameters={"id", "document_id"},
                       documents={"document_id", "building_id"})
        assert len(contracts_count_sql(shape)) == 2

    def test_no_contract_table_at_all_counts_nothing(self):
        assert contracts_count_sql(_shape()) == []

    def test_a_meter_created_by_a_readings_upload_counts_as_energy(self):
        shape = _shape(energy_meters={"id", "building_id", "active"})
        sql = meters_count_sql(shape)
        assert len(sql) == 1
        assert "FROM plenum_cafm.energy_meters WHERE active IS NOT FALSE GROUP BY 1" in sql[0]

    def test_the_graph_meters_table_reaches_a_building_through_its_asset(self):
        shape = _shape(meters={"meter_id", "building_id", "asset_id"},
                       assets={"asset_id", "building_id"})
        sql = meters_count_sql(shape)
        assert len(sql) == 1
        assert "LEFT JOIN plenum_cafm.assets a ON a.asset_id::text = m.asset_id::text" in sql[0]

    def test_the_two_meter_tables_are_summed_not_chosen_between(self):
        shape = _shape(meters={"meter_id", "building_id", "asset_id"},
                       assets={"asset_id", "building_id"},
                       energy_meters={"id", "building_id"})
        assert len(meters_count_sql(shape)) == 2
        assert merge_counts({"b1": 1}, {"b1": 2, "b2": 1}) == {"b1": 3, "b2": 1}

    def test_child_counts_runs_every_road(self):
        """A builder nobody calls is a road nobody drives."""
        import inspect

        source = inspect.getsource(building_rollup.child_counts)
        assert "contracts_count_sql(shape)" in source
        assert "meters_count_sql(shape)" in source
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend/cafm-connector-service-final/svc-operations-intelligence && python3 -m pytest tests/unit/test_c_hoist_score.py -q`
Expected: FAIL at import — `ImportError: cannot import name 'contracts_count_sql'`.

- [ ] **Step 3: Add the two tables to `_KEYS`**

In `building_rollup.py`, after the line `"contracts": ("contract_id", "id"),` add:

```python
    # The tables the contract ingest and the readings upload actually write. Neither is a
    # graph table, and both reach a building on a road child_counts() has to draw itself.
    "contract_sla_parameters": ("id",),
    "energy_meters": ("id",),
```

- [ ] **Step 4: Add the pure builders**

Immediately after `invoices_count_sql` (before `async def child_counts`), add:

```python
def contracts_count_sql(shape: dict[str, Any]) -> list[str]:
    """SQL statements that each count contracts per building. Run them all and sum.

    Two roads reach a building. plenum_cafm.contracts carries building_id itself and is the
    table the graph draws — but nothing in this service writes it. A contract that arrives
    through the contract ingest lands in contract_sla_parameters, which has no building
    column: it reaches its building through the document it was extracted from, the same
    join parameters.list_contract_parameters() makes to say which building a contract
    covers. Counting only the first road is how every building read "contracts missing"
    after a contract had been ingested against it.

    Pure string-building over introspected column names, so it is tested without a database.
    """
    out: list[str] = []
    if (shape.get("contracts") or {}).get("exists"):
        out.append("SELECT building_id::text, count(*) FROM plenum_cafm.contracts GROUP BY 1")
    csp = shape.get("contract_sla_parameters") or {}
    docs = shape.get("documents") or {}
    if (
        csp.get("exists")
        and "document_id" in (csp.get("columns") or set())
        and docs.get("exists")
        and "building_id" in (docs.get("columns") or set())
    ):
        dkey = docs["key"]
        out.append(
            "SELECT d.building_id::text, count(*)\n"
            "  FROM plenum_cafm.contract_sla_parameters p\n"
            f"  JOIN plenum_cafm.documents d ON d.{dkey}::text = p.document_id::text\n"
            " GROUP BY 1"
        )
    return out


def meters_count_sql(shape: dict[str, Any]) -> list[str]:
    """SQL statements that each count meters per building. Run them all and sum.

    plenum_cafm.meters is the graph's table and reaches a building directly or through the
    asset it is fitted to. plenum_cafm.energy_meters is what the half-hourly readings upload
    creates from an MPAN / MPRN, and it carries the building itself. A building whose only
    meter arrived by upload read as having no energy at all until an EUI was computed for
    it, because only the first table was counted.
    """
    out: list[str] = []
    m = shape.get("meters") or {}
    a = shape.get("assets") or {}
    mcols = m.get("columns") or set()
    if m.get("exists"):
        if a.get("exists") and "asset_id" in mcols:
            akey = a["key"]
            out.append(
                "SELECT COALESCE(m.building_id::text, a.building_id::text), count(*)\n"
                "  FROM plenum_cafm.meters m\n"
                f"  LEFT JOIN plenum_cafm.assets a ON a.{akey}::text = m.asset_id::text\n"
                " GROUP BY 1"
            )
        elif "building_id" in mcols:
            out.append("SELECT building_id::text, count(*) FROM plenum_cafm.meters GROUP BY 1")
    em = shape.get("energy_meters") or {}
    ecols = em.get("columns") or set()
    if em.get("exists") and "building_id" in ecols:
        where = " WHERE active IS NOT FALSE" if "active" in ecols else ""
        out.append(
            f"SELECT building_id::text, count(*) FROM plenum_cafm.energy_meters{where} GROUP BY 1"
        )
    return out


def merge_counts(*parts: dict[str, int]) -> dict[str, int]:
    """Sum per-building counts from several statements into one dict. The Hoist Score asks
    only whether a building has ANY such record, so a row reachable on two roads being
    added twice changes nothing it reads."""
    out: dict[str, int] = {}
    for part in parts:
        for bid, n in (part or {}).items():
            out[bid] = out.get(bid, 0) + int(n or 0)
    return out
```

- [ ] **Step 5: Make `child_counts` drive both roads**

In `child_counts`, delete the nested meters block inside `if shape["assets"]["exists"]:` — the seven lines from `if shape["meters"]["exists"] and "asset_id" in shape["meters"]["columns"]:` through its closing `))`. Delete the contracts block — the five lines from `if shape["contracts"]["exists"]:` through its closing `))`. Then, directly after the `if shape["work_orders"]["exists"]:` block, add:

```python
    meter_parts = [await _scalar_counts(session, sql) for sql in meters_count_sql(shape)]
    if meter_parts:
        add("meters", merge_counts(*meter_parts))
```

and, directly before `inv_sql = invoices_count_sql(shape)`, add:

```python
    contract_parts = [await _scalar_counts(session, sql) for sql in contracts_count_sql(shape)]
    if contract_parts:
        add("contracts", merge_counts(*contract_parts))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_c_hoist_score.py tests/unit/test_c_building_graph.py -q`
Expected: all pass (30 baseline + 7 new).

- [ ] **Step 7: Leave uncommitted; report**

No commit from this session. Note the two changed files for Hussain's commit.

---

### Task 2: The portfolio aggregation

**Files:**
- Create: `apps/backend/cafm-connector-service-final/svc-operations-intelligence/src/engines/energy/hoist_score.py`
- Test: `apps/backend/cafm-connector-service-final/svc-operations-intelligence/tests/unit/test_c_hoist_score.py`

**Interfaces:**
- Consumes: `buildings.HOIST_SCORE_DOMAINS`, `buildings.hoist_score_for(counts, *, has_eui)`; rows shaped by `buildings.list_buildings()` carrying `building_id`, `name`, `building_code`, `graph_counts`, `eui_kwh_per_m2`.
- Produces: `portfolio_hoist_score(rows: list[dict], *, root: str) -> dict` with keys `ok, root, buildings, domains[{key, covered, of, pct, missing_buildings[{building_id,name,building_code}], note}], score, rows[{building_id,name,building_code,score,covered,missing}]`; constants `DOMAINS`, `NOT_COUNTED`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_c_hoist_score.py`:

```python
from src.engines.energy.hoist_score import DOMAINS, NOT_COUNTED, portfolio_hoist_score


def _row(bid, name, counts, **extra):
    """A row as list_buildings() shapes it, reduced to what the portfolio score reads."""
    return {
        "building_id": bid, "name": name, "building_code": extra.get("code"),
        "graph_counts": counts, "eui_kwh_per_m2": extra.get("eui"),
        "hoist_score": extra.get("hoist_score", 0),
        "hoist_score_source": extra.get("source", "graph_coverage"),
    }


class TestAcrossThePortfolio:
    """A bar is buildings reached over buildings hoisted — nothing else."""

    def two(self):
        return [
            _row("b1", "Harbour Point", {"assets": 23, "certificates": 3, "contracts": 1, "meters": 2}, code="B-101"),
            _row("b2", "Ashgrove Court", {"assets": 12, "meters": 1}, code="B-102"),
        ]

    def test_a_bar_is_buildings_reached_over_buildings_hoisted(self):
        out = portfolio_hoist_score(self.two(), root="buildings")
        by = {d["key"]: d for d in out["domains"]}
        assert out["buildings"] == 2
        assert (by["assets"]["covered"], by["assets"]["of"], by["assets"]["pct"]) == (2, 2, 100)
        assert (by["contracts"]["covered"], by["contracts"]["pct"]) == (1, 50)
        assert (by["compliance"]["covered"], by["compliance"]["pct"]) == (1, 50)
        assert (by["energy"]["covered"], by["energy"]["pct"]) == (2, 100)
        assert (by["maintenance"]["covered"], by["maintenance"]["pct"]) == (0, 0)

    def test_the_bar_names_the_buildings_it_is_missing(self):
        by = {d["key"]: d for d in portfolio_hoist_score(self.two(), root="buildings")["domains"]}
        assert by["contracts"]["missing_buildings"] == [
            {"building_id": "b2", "name": "Ashgrove Court", "building_code": "B-102"}
        ]
        assert by["assets"]["missing_buildings"] == []

    def test_a_recorded_score_does_not_decide_which_records_exist(self):
        """create_building used to store a hard 0, which shape_building_row reads as a
        recorded judgement. The bars are about which records exist, and that is counted."""
        out = portfolio_hoist_score(
            [_row("b1", "HP", {"assets": 1}, hoist_score=0, source="recorded")], root="buildings"
        )
        assert {d["key"]: d for d in out["domains"]}["assets"]["covered"] == 1

    def test_an_eui_on_record_covers_energy_without_a_meter_row(self):
        out = portfolio_hoist_score([_row("b1", "HP", {}, eui=214.0)], root="buildings")
        assert {d["key"]: d for d in out["domains"]}["energy"]["covered"] == 1

    def test_a_table_rooted_on_sites_counts_nothing_and_says_so(self):
        out = portfolio_hoist_score([_row("s1", "Site", {})], root="sites")
        assert out["buildings"] == 1
        assert all(d["covered"] is None and d["pct"] is None and d["note"] == NOT_COUNTED
                   for d in out["domains"])
        assert out["score"] is None

    def test_no_buildings_is_not_zero_coverage(self):
        out = portfolio_hoist_score([], root="buildings")
        assert out["buildings"] == 0
        assert all(d["covered"] is None and d["pct"] is None
                   and d["note"] == "no buildings hoisted yet" for d in out["domains"])

    def test_the_domains_are_the_per_building_scores_five_in_its_order(self):
        assert DOMAINS == ("assets", "compliance", "contracts", "energy", "maintenance")
        assert [d["key"] for d in portfolio_hoist_score([], root="buildings")["domains"]] == list(DOMAINS)

    def test_score_is_the_mean_of_the_per_building_scores(self):
        out = portfolio_hoist_score([
            _row("b1", "HP", {"assets": 1, "certificates": 1, "contracts": 1, "meters": 1, "work_orders": 1}),
            _row("b2", "AC", {"assets": 1}),
        ], root="buildings")
        assert out["score"] == 60  # (100 + 20) / 2
        assert out["rows"][1]["missing"] == ["compliance", "contracts", "energy", "maintenance"]
        assert out["rows"][0]["score"] == 100
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_c_hoist_score.py -q`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'src.engines.energy.hoist_score'`.

- [ ] **Step 3: Create the module**

Create `src/engines/energy/hoist_score.py`:

```python
"""The portfolio Hoist Score: how many hoisted buildings each kind of record has reached.

buildings.hoist_score_for() answers for ONE building: does it have an asset register, a
certificate, a contract, a meter and a work order on record. The Home tile asks the same
question across the portfolio — of the buildings hoisted, how many has each kind of record
reached — and draws one bar per kind: "1 of 2 hoisted buildings with a contract on record".

That is coverage of the buildings, and it needs no ground truth beyond the buildings
themselves. It is deliberately NOT statutory-catalogue completeness (how many of the pack's
certificate types are filed), nor a count of vendors with terms, nor of meters linked: the
tile used to average those four unlike denominators into one number, and read 7 for a
portfolio whose two buildings had eight certificates between them.

A bar is derived from the graph counts on each row, never from a recorded hoist_score: a
stored figure is someone's judgement about the number, not a statement of which records
exist. When the table is rooted on sites (plenum_cafm.buildings has no rows) nothing was
counted, and every bar says so instead of reporting a zero nobody measured.
"""
from __future__ import annotations

from typing import Any

from .buildings import HOIST_SCORE_DOMAINS, hoist_score_for

#: The five domains, in the order the per-building score names them.
DOMAINS: tuple[str, ...] = tuple(d for d, _ in HOIST_SCORE_DOMAINS)

NOT_COUNTED = "the building graph has no rows yet, so nothing is counted against a building"
NO_BUILDINGS = "no buildings hoisted yet"


def _who(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "building_id": row.get("building_id"),
        "name": row.get("name"),
        "building_code": row.get("building_code"),
    }


def portfolio_hoist_score(rows: list[dict[str, Any]], *, root: str) -> dict[str, Any]:
    """Aggregate the buildings table (list_buildings()'s rows) into per-domain coverage.

    ``root`` is the table's own ``root`` field: "buildings" when the graph counted, anything
    else when the rows came from sites and carry no counts.
    """
    counted = root == "buildings"
    n = len(rows)

    per_row: list[dict[str, Any]] = []
    for r in rows:
        if counted:
            hs = hoist_score_for(r.get("graph_counts") or {},
                                 has_eui=r.get("eui_kwh_per_m2") is not None)
            score: int | None = hs["score"]
            covered, missing = hs["covered"], hs["missing"]
        else:
            score, covered, missing = None, [], list(DOMAINS)
        per_row.append({**_who(r), "score": score, "covered": covered, "missing": missing})

    domains: list[dict[str, Any]] = []
    for key in DOMAINS:
        if not counted or n == 0:
            domains.append({
                "key": key, "covered": None, "of": n, "pct": None,
                "missing_buildings": [],
                "note": NOT_COUNTED if not counted else NO_BUILDINGS,
            })
            continue
        reached = [p for p in per_row if key in p["covered"]]
        without = [p for p in per_row if key in p["missing"]]
        domains.append({
            "key": key,
            "covered": len(reached),
            "of": n,
            "pct": int(round(100.0 * len(reached) / n)),
            "missing_buildings": [_who(p) for p in without],
            "note": None,
        })

    scores = [p["score"] for p in per_row if isinstance(p["score"], int)]
    return {
        "ok": True,
        "root": root,
        "buildings": n,
        "domains": domains,
        # The mean of the per-building scores over all five domains — what the Buildings
        # page's column averages to. The Home tile draws four of the five and takes its own
        # mean of those, so the two figures are related but not the same number.
        "score": int(round(sum(scores) / len(scores))) if scores else None,
        "rows": per_row,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_c_hoist_score.py -q`
Expected: all pass (previous + 8 new).

- [ ] **Step 5: Leave uncommitted; report**

---

### Task 3: The route, narrowed the same way the buildings table is

**Files:**
- Modify: `apps/backend/cafm-connector-service-final/svc-operations-intelligence/src/api/routes/energy.py` (imports ~line 4 and ~line 17; `list_buildings` at ~line 292)
- Test: `apps/backend/cafm-connector-service-final/svc-operations-intelligence/tests/unit/test_c_hoist_score.py`

**Interfaces:**
- Consumes: `hoist_score.portfolio_hoist_score(rows, *, root)`; `bld_svc.list_buildings(session, organization_id=..., limit=...)` returning `{ok, root, count, buildings: [...]}`; `access.Scope` with `.restricted` and `.allows_building(id)`.
- Produces: `GET /api/energy/hoist-score?organization_id=` returning `portfolio_hoist_score`'s dict; module-level `_narrow_to_scope(out: dict, s: access.Scope) -> dict`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_c_hoist_score.py`:

```python
class TestTheRouteNarrows:
    """A score over buildings the caller cannot see is a number about somebody else."""

    def test_a_restricted_caller_scores_only_their_buildings(self):
        from src.api.routes.energy import _narrow_to_scope

        class OneBuilding:
            restricted = True

            def allows_building(self, bid):
                return str(bid) == "b1"

        out = _narrow_to_scope(
            {"ok": True, "root": "buildings", "count": 2,
             "buildings": [{"building_id": "b1"}, {"building_id": "b2"}]},
            OneBuilding(),
        )
        assert [r["building_id"] for r in out["buildings"]] == ["b1"]
        assert out["count"] == 1
        assert out["scoped_to_buildings"] == 1

    def test_an_unrestricted_caller_is_left_alone(self):
        from src.api.routes.energy import _narrow_to_scope

        class Company:
            restricted = False

        table = {"buildings": [{"building_id": "b1"}, {"building_id": "b2"}]}
        assert _narrow_to_scope(table, Company()) is table

    def test_the_route_exists_and_is_a_get(self):
        from src.api.routes.energy import router

        paths = {(r.path, tuple(sorted(r.methods or []))) for r in router.routes}
        assert ("/api/energy/hoist-score", ("GET",)) in paths
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_c_hoist_score.py -q -k TheRouteNarrows`
Expected: FAIL — `ImportError: cannot import name '_narrow_to_scope'`.

- [ ] **Step 3: Add the imports**

In `energy.py`, after `from pathlib import Path` add `from typing import Any`. After `from ...engines.energy import buildings as bld_svc` add `from ...engines.energy import hoist_score as hoist_svc`.

- [ ] **Step 4: Factor the narrowing and use it in `list_buildings`**

Directly above `@router.get("/buildings")` add:

```python
def _narrow_to_scope(out: dict[str, Any], s: access.Scope) -> dict[str, Any]:
    """A building-restricted caller sees only their allocated buildings.

    The engine returns the table under "buildings". The filter first looked for "rows",
    found nothing, and let a one-building user read the whole portfolio — so the key is
    checked, not assumed. Shared by GET /buildings and GET /hoist-score so the two cannot
    drift: a score over buildings the caller cannot see would be a number about somebody
    else's portfolio.
    """
    if not (s.restricted and isinstance(out, dict)):
        return out
    key = "buildings" if isinstance(out.get("buildings"), list) else "rows"
    rows = [r for r in (out.get(key) or [])
            if s.allows_building(r.get("building_id") or r.get("id"))]
    return dict(out, **{key: rows}, count=len(rows), scoped_to_buildings=len(rows))
```

Replace the body of `list_buildings` from `org_id = access.organization_for(s, organization_id)` to `return out` with:

```python
    org_id = access.organization_for(s, organization_id)
    out = await bld_svc.list_buildings(session, organization_id=org_id, limit=limit)
    return _narrow_to_scope(out, s)
```

- [ ] **Step 5: Add the route**

Directly after `list_buildings` (before `@router.post("/buildings/backfill-from-sites")`) add:

```python
@router.get("/hoist-score")
async def hoist_score(
    organization_id: UUID | None = Query(None),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """The portfolio Hoist Score: of the buildings this caller may see, how many has each
    kind of record reached — asset register, certificate, contract, meter, work order.

    One bar per kind, each "covered of hoisted" with the buildings still missing it named,
    plus the mean of the per-building scores. Read by the Home tile. Same company and
    building narrowing as GET /buildings; engines/energy/hoist_score.py says what a bar
    means and what it deliberately is not."""
    org_id = access.organization_for(s, organization_id)
    table = _narrow_to_scope(
        await bld_svc.list_buildings(session, organization_id=org_id, limit=5000), s
    )
    return hoist_svc.portfolio_hoist_score(
        table.get("buildings") or [], root=str(table.get("root") or "sites")
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_c_hoist_score.py tests/unit/test_c_building_graph.py tests/unit/test_c_building_scope.py -q`
Expected: all pass. `test_c_building_scope.py` is included because it exercises the buildings route's narrowing that Step 4 refactored.

- [ ] **Step 7: Leave uncommitted; report**

---

### Task 4: The Home tile reads the one endpoint

**Files:**
- Modify: `apps/frontend/src/api/energy.js` (add one wrapper after `listBuildings`)
- Modify: `apps/frontend/src/logic/homeLive.js` (header comment, imports, `BARS`, the score block in `shapeLiveHome`, the `live` flag, `homeLoad` reads)
- Test: `apps/frontend/test/homeLive.test.mjs`

**Interfaces:**
- Consumes: the Task 3 response under `raw.coverage`.
- Produces: `energyApi.hoistScore()`; `shapeLiveHome` unchanged in signature and in the shape of `score` (`value, band, bars[], sourced, total, gap, note`), `crons`, `hero`, `pending`, `live`. `renderVals.js` needs no change.

- [ ] **Step 1: Rewrite the failing tests**

In `test/homeLive.test.mjs`:

Replace the three fixtures `compliance`, `meters`, `contracts` (keep `compliance` — Spaces still reads it and `full()` should still carry it) so that `meters` and `contracts` are deleted and this fixture is added after `compliance`:

```js
// GET /api/energy/hoist-score — two buildings hoisted; Ashgrove Court has no contract and no
// certificate yet, neither has a work order.
const AC = { building_id: 'b-102', name: 'Ashgrove Court', building_code: 'B-102' };
const HP = { building_id: 'b-101', name: 'Harbour Point', building_code: 'B-101' };
const coverage = {
  ok: true, root: 'buildings', buildings: 2, score: 60,
  domains: [
    { key: 'assets', covered: 2, of: 2, pct: 100, missing_buildings: [], note: null },
    { key: 'compliance', covered: 1, of: 2, pct: 50, missing_buildings: [AC], note: null },
    { key: 'contracts', covered: 1, of: 2, pct: 50, missing_buildings: [AC], note: null },
    { key: 'energy', covered: 2, of: 2, pct: 100, missing_buildings: [], note: null },
    { key: 'maintenance', covered: 0, of: 2, pct: 0, missing_buildings: [HP, AC], note: null }
  ],
  rows: []
};
```

Change `full()` to `shapeLiveHome({ raw: { compliance, coverage, approvals, anomalies }, register }, NOW)`.

Replace the five bar tests (`certificates bar is…`, `meter bar is…`, `contracts bar is…` ×2, `assets bar has no source yet…`) and the `score is the mean…` and `score bands…` tests with:

```js
test('each bar is hoisted buildings with that record over hoisted buildings', () => {
  const bars = full().score.bars;
  const by = (k) => bars.find((b) => b.key === k);
  assert.equal(by('assets').pct, 100);
  assert.equal(by('assets').val, '100%');
  assert.match(by('assets').note, /2 of 2 hoisted buildings with an asset register on record/);
  assert.equal(by('assets').tone, 'ok');
  assert.equal(by('contracts').pct, 50);
  assert.match(by('contracts').note, /1 of 2 hoisted buildings with a contract on record/);
  assert.equal(by('contracts').tone, 'risk');
  assert.equal(by('meters').pct, 100);
  assert.match(by('meters').note, /with a meter on record/);
  assert.equal(by('certificates').pct, 50);
  assert.match(by('certificates').note, /with a certificate on record/);
});

test('score is the mean of the four bars, with the band and the gap naming the building', () => {
  const s = full().score;
  assert.equal(s.value, 75); // (50 + 100 + 100 + 50) / 4
  assert.equal(s.band, 'Supervised autonomy');
  assert.equal(s.sourced, 4);
  assert.equal(s.total, 4);
  assert.match(s.gap, /^Contracts lowest at 50% — Ashgrove Court has none on record$/);
  assert.match(s.note, /Live · 4 of 4 sources · 2 buildings hoisted/);
});

test('the gap line lists up to three buildings and counts the rest', () => {
  const many = Array.from({ length: 5 }, (_, i) => ({ building_id: 'b' + i, name: 'Building ' + i, building_code: null }));
  const cov = { ok: true, root: 'buildings', buildings: 6, domains: [
    { key: 'assets', covered: 6, of: 6, pct: 100, missing_buildings: [] },
    { key: 'compliance', covered: 6, of: 6, pct: 100, missing_buildings: [] },
    { key: 'contracts', covered: 1, of: 6, pct: 17, missing_buildings: many },
    { key: 'energy', covered: 6, of: 6, pct: 100, missing_buildings: [] },
    { key: 'maintenance', covered: 0, of: 6, pct: 0, missing_buildings: [] }
  ] };
  const s = shapeLiveHome({ raw: { coverage: cov }, register: null }, NOW).score;
  assert.equal(s.gap, 'Contracts lowest at 17% — Building 0, Building 1, Building 2 and 2 more have none on record');
});

test('a coverage read rooted on sites leaves every bar unsourced and says why', () => {
  const cov = { ok: true, root: 'sites', buildings: 3, domains: ['assets', 'compliance', 'contracts', 'energy', 'maintenance'].map((k) => (
    { key: k, covered: null, of: 3, pct: null, missing_buildings: [], note: 'the building graph has no rows yet, so nothing is counted against a building' }
  )) };
  const s = shapeLiveHome({ raw: { coverage: cov }, register: null }, NOW).score;
  assert.equal(s.value, null);
  assert.ok(s.bars.every((b) => b.pct === null && /building graph has no rows/.test(b.note)));
  assert.equal(s.note, 'No source has answered yet');
});

test('no buildings hoisted is said in words, not as 0%', () => {
  const cov = { ok: true, root: 'buildings', buildings: 0, domains: ['assets', 'compliance', 'contracts', 'energy', 'maintenance'].map((k) => (
    { key: k, covered: null, of: 0, pct: null, missing_buildings: [], note: 'no buildings hoisted yet' }
  )) };
  const s = shapeLiveHome({ raw: { coverage: cov }, register: null }, NOW).score;
  assert.equal(s.value, null);
  assert.equal(s.bars[0].val, '—');
  assert.equal(s.note, 'No buildings hoisted yet — hoist one and ingest against it');
});

test('when the coverage read fails the bars say so and the other tiles still render', () => {
  const h = shapeLiveHome({ raw: { compliance, approvals, anomalies, coverage: null }, register }, NOW);
  assert.equal(h.live, true);
  assert.equal(h.score.value, null);
  assert.ok(h.score.bars.every((b) => b.pct === null && b.note === 'coverage read did not answer'));
  assert.ok(h.crons.length > 0);
});

test('score bands: delegated at 85, supervised at 60', () => {
  const at = (pct) => shapeLiveHome({
    raw: { coverage: { ok: true, root: 'buildings', buildings: 100, domains: ['assets', 'compliance', 'contracts', 'energy', 'maintenance'].map((k) => (
      { key: k, covered: pct, of: 100, pct: pct, missing_buildings: [] }
    )) } },
    register: null
  }, NOW).score;
  assert.equal(at(85).band, 'Delegated autonomy');
  assert.equal(at(60).band, 'Supervised autonomy');
  assert.equal(at(59).band, 'Ingestion in progress');
});
```

Leave `with nothing loaded…`, the four crons tests, `hero…` and `pending…` unchanged.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/frontend && node --test test/homeLive.test.mjs`
Expected: the new bar / score / gap / sites / no-buildings / failed-read tests FAIL (assets bar still `null` with the connector note; score 39-based); `crons`, `hero`, `pending` still pass.

- [ ] **Step 3: Add the API wrapper**

In `src/api/energy.js`, directly after the `listBuildings` entry add:

```js
  // The portfolio Hoist Score: of the buildings the caller may see, how many has each kind
  // of record reached — asset register, certificate, contract, meter, work order — with the
  // buildings still missing each one named. The Home tile's one read (logic/homeLive.js);
  // engines/energy/hoist_score.py says what a bar means.
  hoistScore: () => apiFetch(B, '/api/energy/hoist-score', { query: withOrg(), timeoutMs: 30000 }),
```

- [ ] **Step 4: Rewrite the score block in `homeLive.js`**

Header comment: replace the `Hoist Score   ingestion coverage per source, the mean of the sources that answered` line with `Hoist Score   per hoisted building, which kinds of record have reached it — one read, GET /api/energy/hoist-score` and delete the paragraph beginning `Not sourced yet, and disclosed as such on the page: asset registers` through `P&L (no ledger in any backend).`, replacing it with `Not sourced yet, and disclosed as such on the page: the budget-vs-actual P&L (no ledger in any backend).`

Imports: keep `opsApi`, `complianceApi`, `isStaleScope`; add `import { energyApi } from '../api/energy.js';`.

Replace `BARS` with:

```js
// The four bars the tile draws, in drawing order. `domain` is the key the backend's
// per-building score uses for the same thing; `what` finishes "n of N hoisted buildings
// with … on record". The fifth domain the read returns, maintenance, is not drawn here.
const BARS = [
  { key: "contracts", domain: "contracts", label: "Contracts and framework agreements", short: "Contracts", what: "a contract" },
  { key: "assets", domain: "assets", label: "Asset registers", short: "Assets", what: "an asset register" },
  { key: "meters", domain: "energy", label: "Meter consent — MPAN / MPRN", short: "Meter consent", what: "a meter" },
  { key: "certificates", domain: "compliance", label: "Certificates and evidence", short: "Certificates", what: "a certificate" }
];
```

Add after `humanise`:

```js
// "A", "A and B", "A, B and C", "A, B, C and 2 more" — the buildings a bar is missing.
export function listNames(names, max) {
  const cap = max || 3;
  const shown = names.slice(0, cap), rest = names.length - shown.length;
  if (rest > 0) return shown.join(", ") + " and " + rest + " more";
  if (shown.length <= 1) return shown.join("");
  return shown.slice(0, -1).join(", ") + " and " + shown[shown.length - 1];
}
```

Replace the input comment block above `shapeLiveHome` so the `input.raw` list reads:

```
//                   coverage    GET /api/energy/hoist-score   (the bars)
//                   compliance  GET /api/compliance/saved-space/summary  (kept for the Spaces panel)
//                   anomalies   GET /api/energy/anomalies
//                   approvals   GET /api/approvals
```

Replace everything in `shapeLiveHome` from `if (raw.compliance) {` through the `set("assets", null, …)` line with:

```js
  // One read answers all four: for each kind of record, how many hoisted buildings it has
  // reached. The bar's note is the fraction in words; a bar the backend could not count
  // (no building graph, no buildings) draws empty and carries the backend's reason.
  const cov = raw.coverage || null;
  const domainOf = (b) => (cov && (cov.domains || []).find((d) => d.key === b.domain)) || null;
  BARS.forEach((b) => {
    const d = domainOf(b);
    if (!cov) return set(b.key, null, "coverage read did not answer");
    if (!d) return set(b.key, null, "the coverage read did not name this domain");
    if (typeof d.covered !== "number" || !d.of) return set(b.key, null, d.note || "not counted");
    set(b.key, pctOf(d.covered, d.of), d.covered + " of " + d.of + " hoisted buildings with " + b.what + " on record");
  });
```

Replace the `gap:` and `note:` lines in the `score` object with:

```js
    gap: lowest
      ? lowest.short + " lowest at " + lowest.pct + "%" + gapWho(lowest)
      : "No source has answered yet.",
    note: value === null
      ? (cov && cov.buildings === 0 ? "No buildings hoisted yet — hoist one and ingest against it" : "No source has answered yet")
      : "Live · " + sourced.length + " of " + bars.length + " sources · " + cov.buildings + " building" + (cov.buildings === 1 ? "" : "s") + " hoisted"
        + (unsourced.length ? " · " + unsourced.join(", ") + " unsourced" : "")
```

and, just above `const score = {`, add:

```js
  // Who is missing the lowest bar, by name — a number nobody can act on is half an answer.
  const gapWho = (low) => {
    const d = domainOf(BARS.find((b) => b.key === low.key));
    const names = ((d && d.missing_buildings) || []).map((m) => m.name || m.building_code || m.building_id).filter(Boolean);
    if (!names.length) return " — the gap to delegated autonomy";
    return " — " + listNames(names) + (names.length === 1 ? " has" : " have") + " none on record";
  };
```

Replace the `live` line with:

```js
  const live = ["coverage", "compliance", "anomalies", "approvals"].some((k) => !!raw[k]);
```

In `homeLoad`, replace the `reads` object with:

```js
    const reads = {
      approvals: () => opsApi.approvals(),
      // Kept although the bars no longer read it: spacesLive.js shapes the Compliance saved
      // space from homeRaw.compliance.
      compliance: () => complianceApi.savedSpaceSummary(),
      anomalies: () => opsApi.anomalies(),
      coverage: () => energyApi.hoistScore()
    };
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `node --test test/homeLive.test.mjs`
Expected: `tests 14`, `fail 0` — the 7 kept (nothing loaded, four crons, hero, pending) plus the 7 new.

- [ ] **Step 6: Check nothing else read the dropped keys**

Run: `grep -rn "raw\.meters\|raw\.contracts\|homeRaw\.meters\|homeRaw\.contracts" src --include='*.js' --include='*.jsx'`
Expected: only `vendorsLive.js:271` (its own `raw`, not the Home one). No output naming `homeLive.js` or `spacesLive.js`.

- [ ] **Step 7: Leave uncommitted; report**

---

### Task 5: Docs and the spec's status

**Files:**
- Modify: `README.md` (the `## Home — live data` section, ~lines 82-97)
- Modify: `docs/superpowers/specs/2026-09-23-hoist-score-live-design.md` (status line)

- [ ] **Step 1: Replace the first README bullet**

Replace the two-line bullet beginning ``- `GET /api/compliance/saved-space/summary`, `/api/contract-performance/contracts`, `/api/energy/meters` —`` with:

```markdown
- `GET /api/energy/hoist-score` — the Hoist Score: for each kind of record (asset register,
  contract, meter, certificate) how many hoisted buildings it has reached, and which are still
  missing it. Derived from the same per-building graph counts the Buildings table scores on
  (`svc-operations-intelligence/src/engines/energy/hoist_score.py`). Assets arrive by CMMS
  migration; contracts, meters and certificates by their file ingests.
- `GET /api/compliance/saved-space/summary` — no longer a bar; still read for the Compliance saved space
```

Change the parenthetical `wrappers in `src/api/opsIntelligence.js`` to `wrappers in `src/api/opsIntelligence.js` and `src/api/energy.js``.

- [ ] **Step 2: Flip the spec status**

In the spec, change `**Status:** DRAFT for Hussain's review. Uncommitted. Nothing implemented yet.` to `**Status:** Implemented 2026-09-23 on branch `test`, uncommitted; verification against `hoistra_test` pending (see plan).`

- [ ] **Step 3: Leave uncommitted; report**

---

### Task 6: Hussain's deploy and verification (not for this session)

**Files:** none.

- [ ] **Step 1: Rebuild the two images with the safe overlay**

```bash
cd ~/Desktop/hoist/Hoistra_Plenum
docker compose -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml up -d --build svc-operations-intelligence frontend-app
```

The overlay stands down startup migrations and pack seeding. `init_db()`'s `create_all` still runs on boot (no flag exists); it is a no-op when every model's table exists.

- [ ] **Step 2: Preflight, read-only, signed in as `ops.director@northbridge-estates.example`**

`GET http://localhost:3000/backend/ops-intelligence/api/energy/hoist-score` → expect `root: "buildings"`, `buildings: 2`, five domains with `covered` numbers. If `root` is `"sites"`, every bar is "not counted" and the fix is hoisting the buildings into `plenum_cafm.buildings`, not this change. Then `GET /backend/work-order/api/assets` → the migrated assets' `building_id` values should be the two ids in `rows[]`.

- [ ] **Step 3: Ingest in the senior's order and watch the tile**

Follow `~/Desktop/Harbour-Point-Upload-Set/UPLOAD-ORDER.md`: migration workbook → Assets rises; contracts with Harbour Point chosen → Contracts "1 of 2"; both buildings' energy CSVs → Meter consent "2 of 2"; compliance certificates → Certificates "1 of 2". Expected tile: 75, Supervised autonomy, gap line naming Ashgrove Court.

---

## Post-review fixes (2026-09-23, after the fresh-context review)

- **Contracts counted twice where `plenum_cafm.contracts` is the view** (`db/01_schema.sql:914`,
  `migrations/udr_building_graph.sql:314`): the view already joins `contract_sla_parameters`
  to `documents`, so Task 1's second road duplicated it. `contracts_count_sql` now takes the
  parameters road only when the `contracts` relation is absent or lacks `document_id` (the
  view's signature). Tests inverted/added in `TestWhatTheCountsRead`.
- **Seed 78% shown for "not counted" / "no buildings hoisted"**: `renderVals.js` gated the tile
  on a non-null value. `shapeLiveHome` now returns `score.answered`, a band of "Not counted" /
  "Nothing hoisted", and the tile note in the backend's words; `renderVals.js` gates on
  `hmScore = hmLive && (value !== null || answered)` and renders "—". New
  `test/homeTileNotCounted.test.mjs`. The spec's "renderVals.js: no change" line was wrong and
  is corrected.
- **`missing_buildings` capped at 25 with an exact `missing_count`**; the gap line's "and N
  more" uses the count.
