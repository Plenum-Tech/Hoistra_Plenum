"""The company a superadmin is viewing reaches every service that can honour it.

Measured 17 Sep 2026. A superadmin viewing TechCorp asked the Maintenance page and the chat
the same question. The page sent ?organization_id=<TechCorp> and showed 368 decisions; the
chat's tool called work-order-management with no company and got 374 — six "To raise" rows at
Town Hall, which belongs to Plenum Tech LLC.

The fix that landed first stamped the acting company onto operations-intelligence calls only,
on the belief that work-order-management "has no per-company scoping". It does: every
/api/maintenance route takes `organization_id` as "Superadmin only: act as this company"
(routes/maintenance.py) and narrows to that company's buildings. Leaving it out is exactly
how 374 happened. UDR is the one that genuinely has no act-as parameter, so it stays out.
"""
import pytest

import src.http_client as hc

ORG = "00000000-0000-0000-0000-000000000001"


@pytest.fixture()
def captured(monkeypatch):
    seen = {}

    class _Resp:
        status_code = 200
        def raise_for_status(self): return None
        def json(self): return {}

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, path, **kw):
            seen.clear(); seen.update(method=method, path=path, **kw); return _Resp()

    monkeypatch.setattr(hc.httpx, "AsyncClient", _Client)
    hc.caller_authorization.set("Bearer t")
    return seen


@pytest.mark.parametrize("service", ["operations_intelligence", "wo_management"])
async def test_a_scoped_service_gets_the_acting_company(captured, service):
    hc.caller_organization_id.set(ORG)
    await hc.request("GET", "http://x", "/api/maintenance/decisions", service=service,
                     max_attempts=1, params={"limit": 200})
    assert captured["params"]["organization_id"] == ORG
    assert captured["params"]["limit"] == 200


async def test_a_read_with_no_params_at_all_is_stamped_too(captured):
    """get_maintenance_overview / get_ppm_contracts / get_inspection_intelligence pass
    params=None when no building is named — the whole-estate reads. A rule that only
    stamped an existing dict left every one of them unscoped."""
    hc.caller_organization_id.set(ORG)
    await hc.request("GET", "http://x", "/api/maintenance/overview", service="wo_management",
                     max_attempts=1, params=None)
    assert captured["params"] == {"organization_id": ORG}


async def test_a_service_with_no_act_as_parameter_is_left_alone(captured):
    """UDR scopes from the token alone; a parameter it does not read is a stray."""
    hc.caller_organization_id.set(ORG)
    await hc.request("GET", "http://x", "/api/tables/assets/records", service="udr",
                     max_attempts=1, params={"limit": 50})
    assert "organization_id" not in captured["params"]


async def test_a_tool_that_named_a_company_is_not_overruled(captured):
    hc.caller_organization_id.set(ORG)
    other = "00000000-0000-0000-0000-000000000005"
    await hc.request("GET", "http://x", "/api/x", service="wo_management", max_attempts=1,
                     params={"organization_id": other})
    assert captured["params"]["organization_id"] == other


async def test_no_acting_company_means_no_parameter(captured):
    hc.caller_organization_id.set(None)
    await hc.request("GET", "http://x", "/api/x", service="wo_management", max_attempts=1)
    assert not (captured.get("params") or {}).get("organization_id")


def test_the_scoped_services_are_the_two_that_read_the_parameter():
    assert set(hc._ORG_SCOPED_SERVICES) == {"operations_intelligence", "wo_management"}
