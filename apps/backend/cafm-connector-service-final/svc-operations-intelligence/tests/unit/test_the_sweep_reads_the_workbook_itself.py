"""The workbook-extras sweep reads a finished run's workbook from storage, not over HTTP.

It used to GET the schema-mapper's /api/migration/{id}/source with no token -- which worked only
because that route answered anyone, the internet included (6 Nov 2026). The route now asks who is
calling, and a background sweep has nobody to be. It never needed the route: the route reads
migration_jobs.source_blob_url and downloads the blob, and this service shares that table and holds
the same storage credentials.
"""
from __future__ import annotations

import pytest

from src.engines.contract_performance import workbook_extras_runner as runner


class _Session:
    """Answers the one SELECT _source makes."""

    def __init__(self, ref):
        self.ref = ref

    async def execute(self, *_a, **_k):
        ref = self.ref

        class _R:
            def first(self):
                return None if ref is None else (ref,)
        return _R()


@pytest.mark.parametrize("ref", [None, "", "direct_upload", "direct_upload_multi"])
async def test_a_run_with_no_stored_workbook_is_none_without_touching_storage(ref, monkeypatch):
    monkeypatch.setattr(runner.settings, "azure_storage_connection_string", "")
    assert await runner._source(_Session(ref), "m-1") is None


async def test_unconfigured_storage_is_an_error_the_sweep_records_and_retries(monkeypatch):
    monkeypatch.setattr(runner.settings, "azure_storage_connection_string", "")
    with pytest.raises(RuntimeError, match="not configured"):
        await runner._source(_Session("uploads/m-1/assets.xlsx"), "m-1")


def test_it_no_longer_calls_the_schema_mapper():
    import inspect
    src = inspect.getsource(runner._source)
    assert "httpx" not in src and "/api/migration" not in src
