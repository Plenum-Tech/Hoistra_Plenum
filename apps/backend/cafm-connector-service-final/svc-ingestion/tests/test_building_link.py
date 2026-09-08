"""Building link for CSV asset ingestion — what it sends, and what it refuses to file.

The governing rule is the resolver's: an ambiguous answer is not an answer. These tests
pin the two things that make bulk different from a single certificate — one call per file,
and never raising into an ingest — plus the refusals.
"""
from __future__ import annotations

import asyncio
from typing import Any

from shared import building_link
from shared.building_link import building_hint, resolve_buildings


class _Resp:
    def __init__(self, payload: dict[str, Any], status: int = 200):
        self._payload = payload
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self) -> dict[str, Any]:
        return self._payload


class _Client:
    """Records every call so "one call per file" is an assertion, not a hope."""

    def __init__(self, results_for=None, *, raises: Exception | None = None):
        self.calls: list[list[dict[str, Any]]] = []
        self._results_for = results_for or (lambda items: [])
        self._raises = raises

    async def post(self, url: str, json: dict[str, Any]) -> _Resp:
        items = json["items"]
        self.calls.append(items)
        if self._raises:
            raise self._raises
        return _Resp({"results": self._results_for(items)})


def _resolved(bid: str, reason: str = "name") -> dict[str, Any]:
    return {"building_id": bid, "outcome": "resolved", "reason": reason}


def _review(reason: str) -> dict[str, Any]:
    return {"building_id": None, "outcome": "review", "reason": reason}


def _run(rows, client):
    return asyncio.run(resolve_buildings(rows, url="http://x", client=client))


# ── What a row is read as saying ──────────────────────────────────────────────


def test_a_building_column_is_read_whatever_it_is_spelled():
    for header in ("Building", "BUILDING NAME", "bldg", "Block"):
        assert building_hint({header: "Bishopsgate Tower", "asset_code": "A1"}) == {
            "name": "Bishopsgate Tower"
        }


def test_a_code_and_a_name_are_both_sent():
    hint = building_hint({"Building Code": "BT-01", "Building": "Bishopsgate Tower"})
    assert hint == {"code": "BT-01", "name": "Bishopsgate Tower"}


def test_location_is_only_ever_read_as_a_code_never_as_a_name():
    """In a CMMS export 'Location' is as often a room as a building. Treating it as a
    building name would match a room against a building by containment."""
    assert building_hint({"Location": "Level 3 Plant Room"}) == {"code": "Level 3 Plant Room"}


def test_a_bare_site_column_is_sent_as_both_spellings():
    """Whether 'Site' holds a code or a name is unknowable from the header. Sending both
    lets the resolver's own tiers decide; guessing would drop the row's only hint."""
    assert building_hint({"Site": "S-01"}) == {"site_id": "S-01", "site_name": "S-01"}


def test_an_explicit_site_id_and_site_name_stay_distinct():
    hint = building_hint({"site_id": "S-02", "Site Name": "Riverside Campus"})
    assert hint == {"site_id": "S-02", "site_name": "Riverside Campus"}


def test_a_row_that_says_nothing_about_a_building_produces_no_hint():
    assert building_hint({"asset_code": "A1", "make": "Trane"}) is None


def test_empty_and_placeholder_values_are_not_hints():
    for junk in ("", "   ", "nan", "N/A", "-", None):
        assert building_hint({"Building": junk}) is None


# ── One call per file ─────────────────────────────────────────────────────────


def test_identical_hints_are_deduped_into_one_lookup():
    rows = [{"Building": "Bishopsgate Tower", "asset_code": f"A{i}"} for i in range(500)]
    client = _Client(lambda items: [_resolved("u-1")] * len(items))
    out = _run(rows, client)

    assert len(client.calls) == 1, "a file must cost one call, not one per row"
    assert len(client.calls[0]) == 1, "500 rows naming one building is one hint"
    assert out["unique_hints"] == 1
    assert out["linked"] == 500
    assert set(out["by_row"]) == {"u-1"}


def test_distinct_buildings_each_get_one_hint_and_map_back_in_order():
    rows = [
        {"Building": "Bishopsgate Tower"},
        {"Building": "Kingsway House"},
        {"Building": "Bishopsgate Tower"},
    ]
    client = _Client(
        lambda items: [
            _resolved("u-1") if i["name"] == "Bishopsgate Tower" else _resolved("u-2")
            for i in items
        ]
    )
    out = _run(rows, client)
    assert len(client.calls[0]) == 2
    assert out["by_row"] == ["u-1", "u-2", "u-1"]


def test_rows_with_no_hint_are_not_sent_but_are_still_counted():
    rows = [{"Building": "Bishopsgate Tower"}, {"asset_code": "A2"}]
    client = _Client(lambda items: [_resolved("u-1")])
    out = _run(rows, client)
    assert len(client.calls[0]) == 1
    assert out["by_row"] == ["u-1", None]
    assert out["by_reason"]["no_building_column"] == 1


# ── What it refuses to file ───────────────────────────────────────────────────


def test_an_ambiguous_row_is_left_unlinked_with_its_reason():
    """A campus names the estate, not the structure. Filing against a guess misstates two
    buildings' records at once."""
    rows = [{"Site": "S-02"}]
    client = _Client(lambda items: [_review("site_has_several_buildings")])
    out = _run(rows, client)
    assert out["by_row"] == [None]
    assert out["linked"] == 0
    assert out["by_reason"]["site_has_several_buildings"] == 1
    assert out["by_outcome"]["review"] == 1


def test_a_resolved_outcome_with_no_id_is_not_treated_as_a_link():
    rows = [{"Building": "Bishopsgate Tower"}]
    client = _Client(lambda items: [{"outcome": "resolved", "reason": "name",
                                     "building_id": None}])
    assert _run(rows, client)["by_row"] == [None]


# ── Never raises into an ingest ───────────────────────────────────────────────


def test_an_unreachable_resolver_leaves_every_row_unlinked_and_says_so():
    """The assets must still ingest. A lost link is recoverable; a lost asset row is not."""
    rows = [{"Building": "Bishopsgate Tower"}, {"Building": "Kingsway House"}]
    client = _Client(raises=RuntimeError("connection refused"))
    out = _run(rows, client)
    assert out["by_row"] == [None, None]
    assert out["by_reason"]["resolver_unavailable"] == 2
    assert "connection refused" in out["error"]


def test_a_misaligned_response_links_nothing_rather_than_guessing():
    """Order is the only thing tying a result to its hint. If the lengths disagree that
    assumption is gone, and a mis-aligned link is worse than no link."""
    rows = [{"Building": "A House"}, {"Building": "B House"}]
    client = _Client(lambda items: [_resolved("u-1")])  # 2 sent, 1 returned
    out = _run(rows, client)
    assert out["by_row"] == [None, None]
    assert out["by_reason"]["resolver_response_misaligned"] == 2


def test_an_error_status_is_an_unavailable_resolver_not_a_crash():
    class _Bad(_Client):
        async def post(self, url, json):
            self.calls.append(json["items"])
            return _Resp({}, status=503)

    out = _run([{"Building": "A House"}], _Bad())
    assert out["by_row"] == [None]
    assert out["by_reason"]["resolver_unavailable"] == 1


def test_an_empty_file_makes_no_call_at_all():
    client = _Client()
    out = _run([], client)
    assert client.calls == [] and out["by_row"] == []


def test_a_file_where_no_row_names_a_building_makes_no_call():
    client = _Client()
    out = _run([{"asset_code": "A1"}, {"asset_code": "A2"}], client)
    assert client.calls == []
    assert out["by_reason"]["no_building_column"] == 2


# ── The endpoint it targets ───────────────────────────────────────────────────


def test_the_base_url_comes_from_the_env_var_the_platform_already_uses(monkeypatch):
    monkeypatch.setenv("OPERATIONS_INTELLIGENCE_BASE_URL", "http://ops:8009/")
    assert building_link.base_url() == "http://ops:8009"
    monkeypatch.delenv("OPERATIONS_INTELLIGENCE_BASE_URL")
    assert building_link.base_url() == "http://svc-operations-intelligence:8009"


def test_it_posts_to_resolve_batch():
    seen: list[str] = []

    class _UrlClient(_Client):
        async def post(self, url, json):
            seen.append(url)
            return await super().post(url, json)

    _run([{"Building": "A House"}],
         _UrlClient(lambda items: [_resolved("u-1")]))
    assert seen == ["http://x/api/energy/buildings/resolve-batch"]


# ── The gap report: what is missing, and what would fill it ───────────────────


def test_a_gap_names_the_field_the_reason_and_the_remedy():
    rows = [{"Building": "Somewhere Else", "asset_code": "A1"}]
    client = _Client(lambda items: [{"building_id": None, "outcome": "unmatched",
                                     "reason": "no_match"}])
    gaps = _run(rows, client)["gaps"]
    assert len(gaps) == 1
    assert gaps[0]["field"] == "building_id"
    assert gaps[0]["reason"] == "no_match"
    assert gaps[0]["row_says"] == {"name": "Somewhere Else"}
    assert "not in the graph yet" in gaps[0]["remedy"]


def test_gaps_are_grouped_by_cause_not_listed_per_row():
    """400 assets in one unnamed building is one problem to solve. Per-row lines bury it."""
    rows = [{"Building": "Somewhere Else", "asset_code": f"A{i}"} for i in range(400)]
    client = _Client(lambda items: [{"building_id": None, "outcome": "unmatched",
                                     "reason": "no_match"}])
    gaps = _run(rows, client)["gaps"]
    assert len(gaps) == 1
    assert gaps[0]["rows_affected"] == 400
    assert gaps[0]["sample_assets"] == ["A0", "A1", "A2", "A3", "A4"], "capped sample"


def test_the_biggest_gap_is_reported_first():
    rows = ([{"Building": "Big Gap", "asset_code": f"B{i}"} for i in range(10)]
            + [{"Building": "Small Gap", "asset_code": "S1"}])
    client = _Client(lambda items: [{"building_id": None, "outcome": "unmatched",
                                     "reason": "no_match"} for _ in items])
    gaps = _run(rows, client)["gaps"]
    assert [g["rows_affected"] for g in gaps] == [10, 1]


def test_a_file_with_no_building_column_reports_that_as_the_gap():
    out = _run([{"asset_code": "A1"}, {"asset_code": "A2"}], _Client())
    assert out["gaps"][0]["reason"] == "no_building_column"
    assert out["gaps"][0]["row_says"] is None
    assert out["gaps"][0]["rows_affected"] == 2
    assert "Add a building" in out["gaps"][0]["remedy"]


def test_an_ambiguous_site_gap_asks_for_the_building_not_the_estate():
    rows = [{"Site": "S-02", "asset_code": "A1"}]
    gaps = _run(rows, _Client(lambda i: [_review("site_has_several_buildings")]))["gaps"]
    assert "name the estate and not the structure" in gaps[0]["remedy"]


def test_an_unreachable_resolver_is_a_gap_that_says_re_run():
    rows = [{"Building": "A House", "asset_code": "A1"}]
    out = _run(rows, _Client(raises=RuntimeError("boom")))
    assert out["gaps"][0]["reason"] == "resolver_unavailable"
    assert "re-run the link" in out["gaps"][0]["remedy"]


def test_rows_that_linked_produce_no_gap():
    rows = [{"Building": "Bishopsgate Tower", "asset_code": "A1"}]
    out = _run(rows, _Client(lambda i: [_resolved("u-1")]))
    assert out["gaps"] == []
    assert out["linked"] == 1 and out["unlinked"] == 0


def test_a_mixed_file_reports_only_the_part_that_failed():
    rows = [
        {"Building": "Bishopsgate Tower", "asset_code": "A1"},
        {"Building": "Somewhere Else", "asset_code": "A2"},
        {"asset_code": "A3"},
    ]

    def _results(items):
        return [_resolved("u-1") if i["name"] == "Bishopsgate Tower"
                else {"building_id": None, "outcome": "unmatched", "reason": "no_match"}
                for i in items]

    out = _run(rows, _Client(_results))
    assert out["linked"] == 1 and out["unlinked"] == 2
    assert {g["reason"] for g in out["gaps"]} == {"no_match", "no_building_column"}
