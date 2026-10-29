"""A reference no row of the database answers is asked once per table, not once per row.

29 Oct 2026: a workbook's 2,270 work orders took 25 minutes at step 10. Their assets and
contracts had not landed, so every row asked the database again for the same missing asset,
vendor and contract — three round trips to Azure each, and a scan of the parent table.

A miss is still not kept for the whole run: vendors are written after work_orders, and a vendor
missing while work orders were written must be found by the next table (23 Sep 2026). The writer
resolves every row of a table before inserting any, so the miss is kept until it moves on.
"""
from __future__ import annotations

import asyncio

from src.graph.nodes.reference_link import ReferenceResolver


def _resolver(rows_for):
    calls = []

    async def fetch(sql, params):
        calls.append(params["k"])
        return rows_for(params["k"])

    return ReferenceResolver(fetch, "org-1"), calls


def test_a_miss_is_asked_once_while_one_table_is_written():
    r, calls = _resolver(lambda k: [])
    r.begin_table("work_orders")
    for _ in range(50):
        assert asyncio.run(r.resolve("vendor_id", "Acme Ltd")) is None
    assert len(calls) == 1
    assert r.report()["unresolved"] == {"vendor_id": ["Acme Ltd"]}


def test_the_next_table_asks_again_and_finds_what_was_written_since():
    found = {"acme ltd": False}
    r, calls = _resolver(lambda k: [("v-1",)] if found[k] else [])
    r.begin_table("work_orders")
    assert asyncio.run(r.resolve("vendor_id", "Acme Ltd")) is None
    found["acme ltd"] = True
    r.begin_table("ppm_visits")
    assert asyncio.run(r.resolve("vendor_id", "acme ltd")) == "v-1"
    assert len(calls) == 2


def test_without_a_table_pass_a_miss_is_asked_again():
    r, calls = _resolver(lambda k: [])
    asyncio.run(r.resolve("vendor_id", "Acme Ltd"))
    asyncio.run(r.resolve("vendor_id", "Acme Ltd"))
    assert len(calls) == 2


def test_an_ambiguous_name_is_asked_once_and_reported_once():
    r, calls = _resolver(lambda k: [("v-1",), ("v-2",)])
    r.begin_table("work_orders")
    for _ in range(5):
        assert asyncio.run(r.resolve("vendor_id", "Acme")) is None
    assert len(calls) == 1
    assert r.report()["ambiguous"] == ["vendor_id=Acme"]
