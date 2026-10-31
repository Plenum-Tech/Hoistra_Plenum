from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

from src.api.homework_identity import TestPrincipal
from src.services.trace_hw1.service import TraceService


class Repo:
    def __init__(self):
        self.sessions = {
            "own": {"id": "own", "buildings": ["b1"], "data": {"owner": "u1", "role": "property_manager"}},
            "other": {"id": "other", "buildings": ["b1"], "data": {"owner": "u2", "role": "property_manager"}},
        }
        self.traces = [
            {"id": "t1", "buildings": ["b1"], "data": {"actor": "u1", "role": "property_manager",
                "conversation_id": "own", "selected_route": "energy", "actual_tools": [{"tool": "energy_eui"}],
                "outcome": "answered", "generated_at": "2026-10-25T00:00:00+00:00"}},
            {"id": "t2", "buildings": ["b1"], "data": {"actor": "u2", "role": "property_manager",
                "conversation_id": "other", "selected_route": "contract", "actual_tools": [],
                "outcome": "answered", "generated_at": "2026-10-25T00:01:00+00:00"}},
        ]

    async def get(self, table, identifier):
        if table != "sessions" or identifier not in self.sessions:
            raise HTTPException(404, "Conversation unavailable")
        return self.sessions[identifier]

    async def list(self, table, kind=None):
        if (table,kind)==("history","unified_trace"):return []
        assert (table, kind) == ("history", "trace")
        return self.traces

    async def audit(self, *args, **kwargs):
        return None


class Store:
    def __init__(self):
        self.repo = Repo()

    @asynccontextmanager
    async def transaction(self, *_args, **_kwargs):
        yield self.repo


@pytest.mark.asyncio
async def test_trace_export_is_owner_and_scope_filtered_without_conversation_text():
    principal = TestPrincipal("u1", "property_manager", "o1", ("b1",))
    result = await TraceService(Store()).export(principal, "own")
    assert result["returned"] == 1
    assert result["traces"][0]["actual_tools"] == [{"tool": "energy_eui"}]
    assert result["conversation_content_included"] is False
    assert "user" not in result["traces"][0]


@pytest.mark.asyncio
async def test_trace_export_hides_another_principals_session():
    principal = TestPrincipal("u1", "property_manager", "o1", ("b1",))
    with pytest.raises(HTTPException) as error:
        await TraceService(Store()).export(principal, "other")
    assert error.value.status_code == 404
