"""Step 7's AI pass returns its whole answer.

Its answer was capped at 2,000 tokens; for a 17-table workbook the JSON stopped mid-string every run
("Unterminated string starting at: line 149"), so 28 s of AI time ended in the column-name fallback
(2–3 Oct 2026). The cap now fits a workbook's relationships, the answer is asked for compactly, and
an answer that still hits the cap is reported as cut off, not as a JSON error.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from src.graph.nodes import hierarchy_node as hn

SCHEMA = {f"T{i}": {"columns": ["id", "site_id", "asset_code"], "row_count": 10, "sample_row": {"id": "1"}}
          for i in range(17)}


class _Client:
    def __init__(self, text, stop="end_turn"):
        self.kwargs = None
        self.text, self.stop = text, stop
        self.messages = self

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(content=[SimpleNamespace(text=self.text)], stop_reason=self.stop)


def _run(monkeypatch, client):
    import src.app as app_module

    monkeypatch.setattr(app_module, "get_anthropic_client", lambda: client, raising=False)
    return asyncio.run(hn._claude_infer_hierarchies(SCHEMA, lambda m: None))


def test_the_answer_has_room_for_a_whole_workbook(monkeypatch):
    answer = {"inferred_fks": [{"source_table": "T1", "source_column": "site_id", "target_table": "T0",
                                "target_column": "id", "relationship_type": "CONTAINMENT", "confidence": 0.9,
                                "reasoning": "site_id references sites"}], "hierarchy_levels": {"T0": 0, "T1": 1}}
    client = _Client(json.dumps(answer))
    fks, levels = _run(monkeypatch, client)
    assert client.kwargs["max_tokens"] >= 8000
    assert "at most 8 words" in client.kwargs["messages"][0]["content"]
    assert fks == answer["inferred_fks"] and levels == answer["hierarchy_levels"]


def test_an_answer_that_hits_the_cap_says_so(monkeypatch):
    with pytest.raises(ValueError, match="cut off"):
        _run(monkeypatch, _Client('{"inferred_fks": [{"source_table": "T1", "reas', stop="max_tokens"))
