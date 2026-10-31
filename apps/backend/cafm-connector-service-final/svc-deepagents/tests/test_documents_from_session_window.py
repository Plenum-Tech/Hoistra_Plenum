"""``documents_from_session`` can actually be called — by either of its callers.

21 Sep 2026. A held WKU contract was approved in the chat: the decision was recorded against
the reader, outcome ``overridden``, ``final_building_id`` Bishopsgate Tower. The document was
never placed on that building, and the reader was told the ingest had completed.

    "event": "ingestion_case.release_failed",
    "error": "documents_from_session() got an unexpected keyword argument 'within_hours'"

The parameter was added to the docstring and to the body — ``"hrs": max(1, int(within_hours))``
— and never to the signature. So the function was broken for BOTH of its callers, in two
different ways: the release route passes ``within_hours=72`` and gets TypeError, and the
collapse path calls it bare and would get NameError on the same line.

It failed quietly because ``_release`` reports a bind failure into ``bound.error`` rather than
raising — deliberately, since a decision already taken must not be lost to a failed write. The
cost of that deliberate softness is that nothing upstream noticed for four days: two cases
approved, zero of their documents placed.

Same family as test_binding_sql_compiles.py, which exists because a statement that never ran
was logged at warning level while everything around it reported success. These tests call the
function for real against a fake session, because the defect is in whether it can be called at
all — a signature nobody exercises is a signature nobody checks.
"""
from __future__ import annotations

import pytest

from src.services import building_binding

SID = "0707f0f2-73ed-48c0-ae66-058a62604060"
DOC = "adda1245-1fd3-42e5-81c4-205ded09f7ab"


class _Result:
    def __init__(self, ids: list[str]) -> None:
        self._ids = ids

    def scalars(self):
        return self

    def all(self):
        return self._ids


class _Session:
    """Records the bind parameters so the tests can assert on the window actually asked for."""

    def __init__(self, sink: list[dict]) -> None:
        self.sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        self.sink.append(dict(params or {}))
        return _Result([DOC])


@pytest.fixture
def asked(monkeypatch):
    sink: list[dict] = []
    monkeypatch.setattr(
        building_binding.database, "AsyncSessionLocal", lambda: _Session(sink)
    )
    return sink


async def test_the_release_route_can_call_it(asked):
    # ingestion_cases.py:72 — the call that failed on a real approval.
    out = await building_binding.documents_from_session(SID, within_hours=72)
    assert out == [DOC]
    assert asked[0]["hrs"] == 72, "the late-arriving caller's window must reach the query"


async def test_the_collapse_path_can_call_it_bare(asked):
    # building_binding.py:495 — no keyword at all. The body references within_hours
    # unconditionally, so this raised NameError rather than defaulting.
    out = await building_binding.documents_from_session(SID)
    assert out == [DOC]
    assert asked[0]["hrs"] == 1, (
        "one hour is the documented norm — 'a filename is only unique within' it — and a "
        "silently wider default would re-bind documents somebody has since corrected"
    )


async def test_the_session_id_is_matched_as_a_prefix_not_a_fragment(asked):
    await building_binding.documents_from_session(SID)
    assert asked[0]["prefix"] == SID + "\\_%", (
        "files are saved as '{session_id}_{filename}'; the underscore is escaped because a "
        "bare _ is a single-character wildcard in LIKE"
    )


async def test_no_session_id_asks_the_database_nothing(asked):
    assert await building_binding.documents_from_session("") == []
    assert await building_binding.documents_from_session("   ") == []
    assert asked == [], "an empty prefix would match every document ever uploaded"
