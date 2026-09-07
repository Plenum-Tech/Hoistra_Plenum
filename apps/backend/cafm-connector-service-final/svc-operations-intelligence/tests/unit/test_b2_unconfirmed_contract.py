"""US1 scenario 4 — scoring is refused while contract parameters are unconfirmed.

A score is a measurement against a commitment. With no confirmed contract there is no
commitment, only platform defaults, and a scorecard built on those reads as though the
vendor had been held to terms they never agreed to. This went unnoticed because nothing
asserted it: _load_confirmed_params silently substituted defaults and scoring carried on.
"""

from datetime import date, datetime
from uuid import uuid4

import pytest

from src.engines.contract_performance import scoring as sc
from src.engines.contract_performance.scoring import weights_to_dict


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def mappings(self):
        return self

    def first(self):
        # No stored work order -> conflict detection finds nothing to compare against.
        return None


class _FakeSession:
    """Returns no confirmed ContractSlaParameters, whatever is asked."""

    def __init__(self):
        self.added = []

    async def execute(self, *_a, **_kw):
        return _FakeResult([])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        return None


def _wo():
    return {
        "wo_code": "WO-1",
        "priority": "P1",
        "status": "Completed",
        "reported_at": datetime(2023, 9, 1, 9, 0),
        "attended_at": datetime(2023, 9, 1, 9, 30),
        "completed_at": datetime(2023, 9, 1, 11, 0),
        "first_fix": True,
        "recall": False,
    }


@pytest.mark.asyncio
async def test_scoring_is_refused_when_no_contract_is_confirmed(monkeypatch):
    async def _weights(*_a, **_kw):
        return weights_to_dict(None)

    monkeypatch.setattr(sc, "get_or_create_weights", _weights)

    out = await sc.score_completed_work_orders(
        _FakeSession(), [_wo()], vendor_id=uuid4(), score_month=date(2023, 9, 1)
    )

    assert out["ok"] is False
    assert out["error"] == "contract_parameters_unconfirmed"
    assert out["scored"] == 0
    # It must NAME what is unconfirmed, not just refuse.
    assert out["unconfirmed_parameters"], "the block must list the defaulted parameters"
    assert any("sla_response_p1_hours" in p for p in out["unconfirmed_parameters"])


@pytest.mark.asyncio
async def test_the_opt_out_lets_a_client_score_on_defaults(monkeypatch):
    """A client with no contract loaded yet can still be scored — deliberately."""

    async def _weights(*_a, **_kw):
        return weights_to_dict(None)

    async def _not_blocked(*_a, **_kw):
        return False

    async def _accred(*_a, **_kw):
        return True, None

    monkeypatch.setattr(sc, "get_or_create_weights", _weights)
    monkeypatch.setattr(sc, "_vendor_blocked", _not_blocked)
    monkeypatch.setattr(sc, "_accreditation_current", _accred)

    out = await sc.score_completed_work_orders(
        _FakeSession(), [_wo()], vendor_id=uuid4(), score_month=date(2023, 9, 1),
        allow_default_parameters=True,
    )
    assert out.get("ok") is not False, "the explicit opt-out must not be blocked"


def test_the_loader_reports_where_its_parameters_came_from():
    """The provenance the loader used to discard is what makes the block possible."""
    from src.engines.contract_performance.parameters import merge_extraction_with_defaults

    merged, defaults_used, field_sources = merge_extraction_with_defaults({})
    assert defaults_used, "an empty extraction must report every field as defaulted"
    assert field_sources, "field_sources must say where each value came from"
