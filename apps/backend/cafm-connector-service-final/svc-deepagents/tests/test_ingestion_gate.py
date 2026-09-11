"""Nothing binds to a building while a validation case is open.

The upload route indexes a file, then binds it to the building the uploader chose. Binding
is what makes the document evidence — from that moment it counts in the building's
compliance position and its reports — so the check runs in between, and a document that
does not clearly belong is held: registered, indexed, not bound.

These pin the three things that make the gate worth having: that a held file is not bound,
that an unreachable validator holds rather than waves through, and that the audit does not
claim a held file was accepted.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from src.services import ingestion_validation as gate


class FakeResponse:
    def __init__(self, body): self._b = body
    def json(self): return self._b


def case(verdict="matched", **over):
    row = {"ok": True, "id": "11111111-1111-5111-8111-111111111111", "verdict": verdict,
           "status": "validated" if verdict == "matched" else "needs_clarification",
           "may_ingest": False, "document_name": "contract.pdf",
           "message": "Nothing in this document identifies Riverside Court."}
    row.update(over)
    return row


class TestBlocking:
    def test_a_matched_document_does_not_block(self):
        assert gate.blocking([case("matched")]) == []

    @pytest.mark.parametrize("verdict", ["uncertain", "mismatch"])
    def test_anything_else_blocks(self, verdict):
        assert len(gate.blocking([case(verdict)])) == 1

    def test_a_decided_case_no_longer_blocks(self):
        assert gate.blocking([case("mismatch", may_ingest=True, status="overridden")]) == []

    def test_one_held_file_holds_only_itself(self):
        held = gate.blocking([case("matched", document_name="a.pdf"),
                              case("mismatch", document_name="b.pdf")])
        assert [c["document_name"] for c in held] == ["b.pdf"]


class TestUnavailableHolds:
    """A validator that cannot be reached must not become an open door.

    Everywhere else in this codebase a failed side call is swallowed so the ingest survives.
    Here the opposite: an unbound document is recoverable from the row it already is, while
    a wrongly bound one silently moves a compliance position somebody will act on.
    """

    def test_a_connection_error_is_a_held_document(self, monkeypatch):
        async def boom(*a, **k):
            raise httpx.ConnectError("refused")
        monkeypatch.setattr(gate, "_request", boom)
        out = asyncio.run(gate.validate(building_id="b", document_name="x.pdf"))
        assert out["ok"] is False and out["held"] is True
        assert out["reason"] == "validation_unavailable"
        assert gate.blocking([out]) == [out]

    def test_a_refusal_from_the_validator_is_passed_through_and_holds(self, monkeypatch):
        async def refuse(*a, **k):
            raise httpx.HTTPStatusError(
                "forbidden", request=httpx.Request("POST", "http://x/api/ingestion/validate"),
                response=httpx.Response(403, json={"detail": {"reason": "building_not_allocated"}}))
        monkeypatch.setattr(gate, "_request", refuse)
        out = asyncio.run(gate.validate(building_id="b", document_name="x.pdf"))
        assert out["ok"] is False and out["held"] is True

    def test_a_clean_check_comes_back_whole(self, monkeypatch):
        async def fine(method, base, path, **kw):
            assert path == "/api/ingestion/validate"
            assert kw["json"]["building_id"] == "b-1"
            assert kw["headers"]["Authorization"] == "Bearer tok"
            return FakeResponse(case("matched"))
        monkeypatch.setattr(gate, "_request", fine)
        out = asyncio.run(gate.validate(building_id="b-1", document_name="x.pdf",
                                        authorization="Bearer tok"))
        assert out["verdict"] == "matched" and gate.blocking([out]) == []


class TestTheNotice:
    def test_it_names_the_file_the_reason_and_the_case(self):
        text = gate.summarise([case("mismatch", document_name="EICR.pdf")])
        assert "EICR.pdf" in text and "nothing has been filed" in text.lower()
        assert "11111111-1111-5111-8111-111111111111" in text

    def test_it_offers_the_suggested_building(self):
        text = gate.summarise([case("mismatch", suggestion={"name": "Bishopsgate Tower"})])
        assert "Bishopsgate Tower" in text

    def test_it_says_nothing_when_nothing_is_held(self):
        assert gate.summarise([case("matched")]) == ""


def test_the_upload_route_only_binds_when_nothing_is_held():
    """Read as source, because reaching this branch live needs a database and an LLM.

    What matters is the shape: `bind_and_log` with a building is called only in the
    not-held branch, and the held branch calls it with None — registering the rows without
    writing a building_id, so the release can find them later.
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src" / "api" / "routes" / "workflow.py"
           ).read_text(encoding="utf-8")
    assert "bound = {} if held else await building_binding.bind_and_log(" in src
    assert 'None, flow.tool_calls, where="inline-held"' in src
    # and the audit never calls a held file accepted
    assert "if Path(_p).name in _held_names:" in src
    assert src.index("held = validation_gate.blocking") < src.index("bound = {} if held else")


def test_a_release_needs_a_decision_first():
    """The decide route binds only what the authority marked may_ingest."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src" / "api" / "routes" /
           "ingestion_cases.py").read_text(encoding="utf-8")
    assert 'if out.get("may_ingest"):' in src
    assert src.index("await gate.decide(") < src.index('if out.get("may_ingest"):')
    assert "principal.can_ingest" in src
