"""The Hoist Score is ingestion coverage, not data entry.

A building hoisted through the form has a name, a country, a floor count and an area, and
the platform knows nothing about it: no assets, no certificates, no contract, no meter, no
maintenance. That building scores nothing, and the score rises as each of those arrives.

The failure this replaces went the other way twice. create_building wrote a hard 0 that
nothing ever updated, so every hoisted building reported zero forever; the first fix then
derived the score from record completeness, which scored a building for having had its form
filled in. Neither is what the score means.
"""
from __future__ import annotations

from src.engines.energy.buildings import (
    HOIST_SCORE_DOMAINS,
    apply_graph_rollup,
    hoist_score_for,
)


class TestWhatItCounts:
    def test_a_building_nobody_has_ingested_anything_about_scores_nothing(self):
        out = hoist_score_for({})
        assert out["score"] == 0
        assert out["missing"] == ["assets", "compliance", "contracts", "energy", "maintenance"]

    def test_the_form_fields_do_not_earn_a_point(self):
        """floors, spaces, documents and equipment are all counted by the graph and none of
        them is one of the five. A building can carry plenty of those and still score 0."""
        out = hoist_score_for({"floors": 12, "spaces": 40, "documents": 3, "equipment": 9})
        assert out["score"] == 0

    def test_each_domain_is_an_equal_share_and_the_score_rises_with_each(self):
        steps = [
            ({"assets": 23}, 20),
            ({"assets": 23, "work_orders": 16}, 40),
            ({"assets": 23, "work_orders": 16, "meters": 2}, 60),
            ({"assets": 23, "work_orders": 16, "meters": 2, "certificates": 13}, 80),
            ({"assets": 23, "work_orders": 16, "meters": 2, "certificates": 13, "contracts": 2}, 100),
        ]
        for counts, expected in steps:
            assert hoist_score_for(counts)["score"] == expected, counts

    def test_one_full_branch_does_not_carry_the_rest(self):
        """A thousand assets is one domain, not a covered building."""
        assert hoist_score_for({"assets": 1000})["score"] == 20

    def test_energy_is_met_by_a_reading_as_well_as_by_a_meter(self):
        """A building whose EUI is on record has its energy covered however the reading
        arrived — refusing it because no meter row exists would score the measurement out."""
        assert hoist_score_for({}, has_eui=True)["score"] == 20
        assert "energy" in hoist_score_for({}, has_eui=True)["covered"]

    def test_the_missing_domains_are_named(self):
        """A number nobody can act on is half an answer: the row says what would raise it."""
        out = hoist_score_for({"assets": 1, "certificates": 2})
        assert out["covered"] == ["assets", "compliance"]
        assert out["missing"] == ["contracts", "energy", "maintenance"]
        assert out["of"] == [d for d, _ in HOIST_SCORE_DOMAINS]


class TestOnTheRow:
    def test_the_rollup_puts_the_derived_score_on_the_building(self):
        row = {"hoist_score": 0, "hoist_score_source": "graph_coverage"}
        out = apply_graph_rollup(row, {"counts": {"assets": 4, "certificates": 1}})
        assert out["hoist_score"] == 40
        assert out["hoist_score_source"] == "graph_coverage"
        assert out["hoist_score_missing"] == ["contracts", "energy", "maintenance"]

    def test_a_recorded_score_is_someone_s_judgement_and_outranks_the_derivation(self):
        row = {"hoist_score": 88, "hoist_score_source": "recorded"}
        out = apply_graph_rollup(row, {"counts": {"assets": 4}})
        assert out["hoist_score"] == 88
        assert out["hoist_score_source"] == "recorded"

    def test_a_building_with_no_rows_at_all_keeps_its_zero(self):
        row = {"hoist_score": 0, "hoist_score_source": "graph_coverage"}
        assert apply_graph_rollup(row, None)["hoist_score"] == 0


from src.engines.energy import building_rollup
from src.engines.energy.building_rollup import (
    contract_parameters_count_sql,
    contracts_count_sql,
    energy_meters_count_sql,
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
    """The per-building domains are only as true as the tables they count.

    Each table is its own relation on graph_counts. The Buildings card prints
    graph_counts["meters"] and graph_counts["contracts"] and its drawer opens
    plenum_cafm.meters and plenum_cafm.contracts — so those two must count exactly the rows
    the drawer can list. The roads the ingests actually write (energy_meters, and
    contract_sla_parameters where the contracts view is missing) are counted beside them
    under their own names, and it is the Hoist Score's domain table that reads both.
    """

    def test_the_contracts_relation_is_the_graphs_own_table_or_view(self):
        shape = _shape(contracts={"contract_id", "building_id", "document_id"},
                       contract_sla_parameters={"id", "document_id"},
                       documents={"document_id", "building_id"})
        assert contracts_count_sql(shape) == "SELECT building_id::text, count(*) FROM plenum_cafm.contracts GROUP BY 1"

    def test_where_the_contracts_view_exists_the_parameters_are_not_counted_again(self):
        """plenum_cafm.contracts is a VIEW over contract_sla_parameters joined to documents
        (udr_building_graph.sql). Reading the parameters table beside it counts every
        contract twice — the graph's Contracts branch drew "2" for one contract."""
        shape = _shape(contracts={"contract_id", "building_id", "document_id"},
                       contract_sla_parameters={"id", "document_id"},
                       documents={"document_id", "building_id"})
        assert contract_parameters_count_sql(shape) is None

    def test_where_the_view_is_missing_a_contract_reaches_its_building_through_its_document(self):
        """Every database built from scratch before the view's swallowed-error fix."""
        shape = _shape(contract_sla_parameters={"id", "document_id"},
                       documents={"document_id", "building_id"})
        assert contracts_count_sql(shape) is None
        sql = contract_parameters_count_sql(shape)
        assert "FROM plenum_cafm.contract_sla_parameters p" in sql
        assert "d.document_id::text = p.document_id::text" in sql
        assert sql.lstrip().startswith("SELECT d.building_id::text, count(*)")

    def test_a_real_contracts_base_table_is_counted_beside_the_parameters(self):
        """A base table without document_id is a different set of rows, not the view."""
        shape = _shape(contracts={"contract_id", "building_id"},
                       contract_sla_parameters={"id", "document_id"},
                       documents={"document_id", "building_id"})
        assert contracts_count_sql(shape) is not None
        assert contract_parameters_count_sql(shape) is not None

    def test_no_contract_table_at_all_counts_nothing(self):
        assert contracts_count_sql(_shape()) is None
        assert contract_parameters_count_sql(_shape()) is None

    def test_a_meter_created_by_a_readings_upload_is_its_own_relation(self):
        shape = _shape(energy_meters={"id", "building_id", "active"})
        assert meters_count_sql(shape) is None
        assert energy_meters_count_sql(shape) == (
            "SELECT building_id::text, count(*) FROM plenum_cafm.energy_meters WHERE active IS NOT FALSE GROUP BY 1"
        )

    def test_the_graph_meters_table_reaches_a_building_through_its_asset(self):
        shape = _shape(meters={"meter_id", "building_id", "asset_id"},
                       assets={"asset_id", "building_id"})
        sql = meters_count_sql(shape)
        assert "LEFT JOIN plenum_cafm.assets a ON a.asset_id::text = m.asset_id::text" in sql
        assert energy_meters_count_sql(shape) is None

    def test_the_two_meter_tables_are_kept_apart_and_the_score_reads_both(self):
        """Summing them made the card say "2 meters" over a drawer listing one. Each table
        keeps its own count; the energy domain is met by either."""
        from src.engines.energy.buildings import HOIST_SCORE_DOMAINS

        shape = _shape(meters={"meter_id", "building_id", "asset_id"},
                       assets={"asset_id", "building_id"},
                       energy_meters={"id", "building_id"})
        assert meters_count_sql(shape) is not None and energy_meters_count_sql(shape) is not None
        domains = dict(HOIST_SCORE_DOMAINS)
        assert domains["energy"] == ("meters", "energy_meters")
        assert domains["contracts"] == ("contracts", "contract_parameters")
        assert hoist_score_for({"energy_meters": 1})["covered"] == ["energy"]
        assert hoist_score_for({"contract_parameters": 1})["covered"] == ["contracts"]

    async def test_child_counts_lands_both_roads_under_the_names_the_score_reads(self, monkeypatch):
        """End to end over a fake session: the merged counts arrive under "meters" and
        "contracts", which is what hoist_score_for() reads. A builder nobody calls, or a
        merge that lands under another name, fails here rather than in production."""

        class _Exec:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        class _Nested:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a):
                return False

        class FakeSession:
            def __init__(self, answers):
                self.answers, self.seen = answers, []

            def begin_nested(self):
                return _Nested()

            async def execute(self, stmt):
                sql = str(stmt)
                self.seen.append(sql)
                for needle, rows in self.answers.items():
                    if needle in sql:
                        return _Exec(rows)
                return _Exec([])

        shape = _shape(
            buildings={"building_id"},
            assets={"asset_id", "building_id"},
            meters={"meter_id", "building_id", "asset_id"},
            energy_meters={"id", "building_id", "active"},
            contract_sla_parameters={"id", "document_id"},
            documents={"document_id", "building_id"},
        )
        monkeypatch.setattr(building_rollup, "_SHAPE_CACHE", shape)
        session = FakeSession({
            "FROM plenum_cafm.assets GROUP": [("b1", 5)],
            "FROM plenum_cafm.meters m": [("b1", 1)],
            "FROM plenum_cafm.energy_meters": [("b1", 2), ("b2", 1)],
            "contract_sla_parameters p": [("b2", 1)],
        })
        out = await building_rollup.child_counts(session)
        # Each table under its own name — what the card prints is what the drawer opens.
        assert out["b1"]["meters"] == 1 and out["b1"]["energy_meters"] == 2
        assert "meters" not in out["b2"] and out["b2"]["energy_meters"] == 1
        assert out["b2"]["contract_parameters"] == 1 and "contracts" not in out["b2"]
        # And the score reads every road.
        assert hoist_score_for(out["b2"])["covered"] == ["contracts", "energy"]
        assert hoist_score_for(out["b1"])["covered"] == ["assets", "energy"]


from src.engines.energy.hoist_score import DOMAINS, NOT_COUNTED, portfolio_hoist_score


def _row(bid, name, counts, **extra):
    """A row as list_buildings() shapes it, reduced to what the portfolio score reads."""
    return {
        # `code`, not `building_code`: that is how shape_building_row() spells it on a row.
        "building_id": bid, "name": name, "code": extra.get("code"),
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

    def test_the_lists_the_buildings_page_stored_are_the_ones_the_bars_read(self):
        """_list_from_graph() writes hoist_score_covered / hoist_score_missing on every
        counted row. Reading them, rather than re-deriving from graph_counts, means the Home
        bars and the Buildings column cannot drift the day either derivation changes."""
        row = _row("b1", "HP", {"assets": 1})
        row["hoist_score_covered"] = ["assets", "energy"]
        row["hoist_score_missing"] = ["compliance", "contracts", "maintenance"]
        out = portfolio_hoist_score([row], root="buildings")
        by = {d["key"]: d for d in out["domains"]}
        assert by["energy"]["covered"] == 1, "the stored list was ignored for a re-derivation"
        assert out["rows"][0]["score"] == 40

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

    def test_an_empty_allocation_is_not_an_empty_portfolio(self):
        """A one-building user whose allocation is empty is told that, not that nothing
        has been hoisted — the portfolio may be full of buildings they cannot see."""
        out = portfolio_hoist_score([], root="buildings", restricted=True)
        assert all(d["note"] == "no buildings allocated to you" for d in out["domains"])
        assert all(d["note"] == "no buildings hoisted yet"
                   for d in portfolio_hoist_score([], root="buildings")["domains"])

    def test_the_domains_are_the_per_building_scores_five_in_its_order(self):
        assert DOMAINS == ("assets", "compliance", "contracts", "energy", "maintenance")
        assert [d["key"] for d in portfolio_hoist_score([], root="buildings")["domains"]] == list(DOMAINS)

    def test_the_missing_list_is_capped_and_the_count_is_not(self):
        """The tile names three buildings; a 600-building portfolio does not need 600 stubs
        five times over in every Home load. The count stays exact so "and N more" is true."""
        from src.engines.energy.hoist_score import MISSING_CAP

        rows = [_row(f"b{i}", f"Building {i}", {"assets": 1}) for i in range(MISSING_CAP + 5)]
        by = {d["key"]: d for d in portfolio_hoist_score(rows, root="buildings")["domains"]}
        assert len(by["contracts"]["missing_buildings"]) == MISSING_CAP
        assert by["contracts"]["missing_count"] == MISSING_CAP + 5
        assert by["assets"]["missing_count"] == 0

    def test_score_is_the_mean_of_the_per_building_scores(self):
        out = portfolio_hoist_score([
            _row("b1", "HP", {"assets": 1, "certificates": 1, "contracts": 1, "meters": 1, "work_orders": 1}),
            _row("b2", "AC", {"assets": 1}),
        ], root="buildings")
        assert out["score"] == 60  # (100 + 20) / 2
        assert out["rows"][1]["missing"] == ["compliance", "contracts", "energy", "maintenance"]
        assert out["rows"][0]["score"] == 100


class TestTheRouteAnswersTheTile:
    async def test_rows_are_left_out_unless_asked_for(self, monkeypatch):
        """A 600-building portfolio does not need 600 per-building stubs in every Home
        load; the tile reads domains only. The breakdown is there for whoever asks."""
        from types import SimpleNamespace

        from src.api.routes import energy as energy_routes

        table = {"ok": True, "root": "buildings", "count": 1,
                 "buildings": [_row("b1", "HP", {"assets": 1})]}

        async def fake_list(session, *, organization_id=None, limit=500):
            return table

        monkeypatch.setattr(energy_routes.bld_svc, "list_buildings", fake_list)
        monkeypatch.setattr(energy_routes.access, "organization_for", lambda s, o: o)
        scope = SimpleNamespace(restricted=False)
        slim = await energy_routes.hoist_score(organization_id=None, session=None, s=scope)
        assert "rows" not in slim and len(slim["domains"]) == 5
        full = await energy_routes.hoist_score(organization_id=None, session=None, s=scope,
                                               include_rows=True)
        assert full["rows"][0]["building_id"] == "b1"


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
