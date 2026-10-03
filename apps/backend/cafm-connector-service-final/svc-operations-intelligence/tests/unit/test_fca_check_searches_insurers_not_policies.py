"""The FCA register lists firms, not policies - so it is searched by the insurer's name only.

It used to fall back to the certificate number when no insurer was on record, which searched the
register with a policy number (plenum_agent: B190325042112, UC CMK 3976335) and could mark an
insurance certificate verified on the strength of a stranger's name. Now a missing name, or a
reference number where the name should be, answers "unverified" without calling the register.
"""
import asyncio

import pytest

from src.engines.compliance.channels import public_apis as pa


class _NoNetwork:
    def __init__(self, *a, **k):
        raise AssertionError("the FCA register must not be called for this query")


class _Resp:
    status_code = 200
    content = b"x"

    def json(self):
        return {"Data": [{"Name": "Aviva Insurance Limited", "Status": "Authorised", "Reference Number": "202153"}]}


class _Client:
    queries: list[str] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None):
        _Client.queries.append(params["q"])
        return _Resp()


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setattr(pa.settings, "fca_api_key", "test-key", raising=False)
    monkeypatch.setattr(pa.settings, "fca_api_email", "", raising=False)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.mark.parametrize("number", ["B190325042112", "UC CMK 3976335"])
def test_a_policy_number_with_no_insurer_is_not_searched(keyed, monkeypatch, number):
    monkeypatch.setattr(pa.httpx, "AsyncClient", _NoNetwork)
    r = run(pa.run_public_api_check(certificate_type_code="CONTRACTOR_PL_INSURANCE", certificate_number=number))
    assert r["verified"] is None
    assert r["evidence"]["reason"] == "insurer_name_required"
    assert "not policies" in r["evidence"]["scope"]


def test_a_reference_in_the_name_field_is_refused(keyed, monkeypatch):
    monkeypatch.setattr(pa.httpx, "AsyncClient", _NoNetwork)
    r = run(pa.run_public_api_check(certificate_type_code="EL_INSURANCE", insurer_name="UC CMK 3976335"))
    assert r["verified"] is None and r["evidence"]["reason"] == "query_is_a_reference"


def test_an_insurer_name_is_searched_and_the_answer_says_what_it_covers(keyed, monkeypatch):
    _Client.queries = []
    monkeypatch.setattr(pa.httpx, "AsyncClient", _Client)
    r = run(pa.run_public_api_check(certificate_type_code="CONTRACTOR_PL_INSURANCE",
                                    certificate_number="B190325042112", insurer_name="Aviva Insurance Limited"))
    assert _Client.queries == ["Aviva Insurance Limited"]          # the name, never the policy number
    assert r["verified"] is True and r["evidence"]["frn"] == "202153"
    assert "cannot confirm this policy exists" in r["evidence"]["scope"]


def test_reference_detection():
    assert pa._looks_like_reference("B190325042112") and pa._looks_like_reference("UC CMK 3976335")
    assert not pa._looks_like_reference("Zurich Insurance plc") and not pa._looks_like_reference("AXA XL")
