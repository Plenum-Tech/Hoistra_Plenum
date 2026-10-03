"""A scoring run names the company it scores for — unless the deployment has none.

Review, 28 Sep 2026. With organization_id=None the scorer's replace-DELETE removed only
company-less rows, while the month's overrun count and the monthly card read every company's.
So a Rebuild run with no company in scope (a superadmin not viewing as a company, or
AUTH_ENFORCE_SCOPE=false) wrote a company-less copy of every score beside the company's own,
counted both, and — because a card is one per vendor-month and was looked up by vendor and
month only — overwrote the company's card with the doubled figures. The month-end job named no
company either, so it did the same on the 1st of every month.

Now: a route with no company in scope refuses to score where companies exist; with none (a
single-company deployment, every row company-less) the org-less pass is the only pass and
still runs. The month-end job cuts one card per company. A card belongs to one company and a
cut for another, or for none, does not overwrite it.
"""
from __future__ import annotations

import importlib
import sys
import types
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.routes import auth as auth_routes
from src.app import app
from src.db import get_session
from src.engines.auth.tokens import Principal
from src.engines.contract_performance import scoring as sc
from src.engines.contract_performance.scoring import weights_to_dict
from src.models.contract_performance import VendorMonthlyScorecard

ORG = UUID("11111111-1111-5111-8111-111111111111")
OTHER = UUID("22222222-2222-5222-8222-222222222222")
VENDOR = UUID("33333333-3333-5333-8333-333333333333")


# ── the routes ─────────────────────────────────────────────────────────────────────

def _principal(organization_id, role="superadmin"):
    return Principal(user_id=uuid4(), email="x@example.com", organization_id=organization_id,
                     session_id=None, issued_at=datetime.now(timezone.utc),
                     password_changed_at=0, role=role, can_ingest=True, building_ids=None)


class _NoDatabase:
    """The routes reach the engine through monkeypatched functions; nothing may query."""

    async def execute(self, *a, **k):
        raise AssertionError("the route queried the database itself")


@pytest.fixture
def api(monkeypatch):
    calls: dict[str, list] = {"companies_checked": [], "engine": []}

    async def _session():
        yield _NoDatabase()

    def _engine(name):
        async def _run(*_a, **kw):
            calls["engine"].append((name, kw.get("organization_id")))
            return {"ok": True}
        return _run

    for name in ("score_completed_work_orders", "score_work_orders_from_udr",
                 "score_all_from_udr", "generate_monthly_scorecard"):
        monkeypatch.setattr(sc, name, _engine(name))

    def has_companies(answer):
        async def _check(_session):
            calls["companies_checked"].append(answer)
            return answer
        monkeypatch.setattr(sc, "deployment_has_companies", _check)

    def as_(organization_id):
        async def _dep():
            return _principal(organization_id)
        app.dependency_overrides[auth_routes.current_principal] = _dep

    app.dependency_overrides[get_session] = _session
    try:
        yield SimpleNamespace(client=TestClient(app), calls=calls, has_companies=has_companies, as_=as_)
    finally:
        app.dependency_overrides.clear()


SCORING_ROUTES = [
    ("/api/contract-performance/score/work-orders",
     {"work_orders": [], "vendor_id": str(VENDOR)}, "score_completed_work_orders"),
    ("/api/contract-performance/score/from-udr",
     {"vendor_id": str(VENDOR), "score_month": "2026-09-01"}, "score_work_orders_from_udr"),
    ("/api/contract-performance/score/from-udr",
     {"all_buckets": True}, "score_all_from_udr"),
    ("/api/contract-performance/scorecards/monthly",
     {"vendor_id": str(VENDOR), "score_month": "2026-09-01"}, "generate_monthly_scorecard"),
]


@pytest.mark.parametrize("path,body,engine", SCORING_ROUTES)
def test_no_company_in_scope_is_refused_where_companies_exist(api, path, body, engine):
    api.as_(None)
    api.has_companies(True)
    r = api.client.post(path, json=body)
    assert r.status_code == 400, r.text[:300]
    detail = r.json()["detail"]
    assert detail["reason"] == "no_organization"
    assert "choose a company to score" in detail["error"].lower()
    assert api.calls["engine"] == [], "nothing was scored"


@pytest.mark.parametrize("path,body,engine", SCORING_ROUTES)
def test_a_deployment_with_no_companies_still_scores_without_one(api, path, body, engine):
    api.as_(None)
    api.has_companies(False)
    r = api.client.post(path, json=body)
    assert r.status_code == 200, r.text[:300]
    assert api.calls["engine"] == [(engine, None)]


@pytest.mark.parametrize("path,body,engine", SCORING_ROUTES)
def test_a_company_in_scope_scores_for_it_without_asking_the_database(api, path, body, engine):
    api.as_(ORG)
    api.has_companies(True)
    r = api.client.post(path, json=body)
    assert r.status_code == 200, r.text[:300]
    assert api.calls["engine"] == [(engine, ORG)]
    assert api.calls["companies_checked"] == []


# ── whether the deployment has any company ─────────────────────────────────────────

class _Answers:
    def __init__(self, *answers):
        self.answers, self.sql = list(answers), []

    async def execute(self, stmt, *a, **k):
        self.sql.append(str(stmt))
        value = self.answers.pop(0)
        return SimpleNamespace(scalar=lambda: value)


async def test_no_organizations_table_means_no_companies():
    s = _Answers(False)
    assert await sc.deployment_has_companies(s) is False
    assert len(s.sql) == 1, "the table itself is never read when it is not there"


async def test_an_empty_organizations_table_means_no_companies():
    assert await sc.deployment_has_companies(_Answers(True, False)) is False


async def test_one_organization_is_a_company():
    s = _Answers(True, True)
    assert await sc.deployment_has_companies(s) is True
    assert "plenum_cafm.organizations" in s.sql[1]


# ── a card is one company's ────────────────────────────────────────────────────────

class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _CardSession:
    """One score row for the vendor-month and, optionally, the month's card on record."""

    def __init__(self, card=None):
        self.card, self.added = card, []
        self.score = SimpleNamespace(
            id=uuid4(), overall_score=Decimal("80"), capped_by_block=False,
            component_scores={"sla_response": 25.0, "sla_completion": 25.0, "first_fix": 20.0,
                              "recall": 10.0, "accreditation": 0.0})

    async def execute(self, stmt, *a, **k):
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        if "FROM plenum_cafm.vendor_wo_scores" in sql:
            return _Result([self.score])
        if "FROM plenum_cafm.vendor_monthly_scorecards" in sql and "'2026-09-01'" in sql:
            return _Result([self.card] if self.card else [])
        return _Result([])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        return None


@pytest.fixture
def card_env(monkeypatch):
    async def _weights(*_a, **_kw):
        return weights_to_dict(None)

    async def _params(*_a, **_kw):
        return {"_contract_confirmed": True, "_contract_parameters_id": "p1"}

    async def _nothing(*_a, **_kw):
        return []

    async def _audit(*_a, **_kw):
        return None

    monkeypatch.setattr(sc, "get_or_create_weights", _weights)
    monkeypatch.setattr(sc, "_load_confirmed_params", _params)
    monkeypatch.setattr(sc, "load_ppm_visits_for_month", _nothing)
    monkeypatch.setattr(sc, "write_audit", _audit)


def _card(organization_id):
    return VendorMonthlyScorecard(id=uuid4(), organization_id=organization_id, vendor_id=VENDOR,
                                  score_month=date(2026, 9, 1), overall_score=Decimal("41"),
                                  component_breakdown={}, wo_score_ids=["old"], block_capped=False)


async def _cut(session, organization_id):
    return await sc.generate_monthly_scorecard(
        session, vendor_id=VENDOR, score_month=date(2026, 9, 1), organization_id=organization_id)


@pytest.mark.parametrize("cut_for", [None, OTHER])
async def test_a_companys_card_is_not_overwritten_by_a_cut_for_none_or_another(card_env, cut_for):
    card = _card(ORG)
    out = await _cut(_CardSession(card), cut_for)
    assert out["ok"] is False
    assert out["error"] == "scorecard_belongs_to_another_company"
    assert card.overall_score == Decimal("41") and card.wo_score_ids == ["old"]
    assert card.organization_id == ORG


async def test_the_companys_own_cut_updates_its_card(card_env):
    card = _card(ORG)
    out = await _cut(_CardSession(card), ORG)
    assert out["ok"] is True
    assert card.overall_score != Decimal("41") and card.wo_score_ids != ["old"]


async def test_a_company_less_card_is_taken_over_by_the_companys_cut(card_env):
    """Left by a run that named no company, it kept organization_id NULL through every later
    update, and the company's scorecard list — filtered on its own id — never showed it."""
    card = _card(None)
    out = await _cut(_CardSession(card), ORG)
    assert out["ok"] is True
    assert card.organization_id == ORG


async def test_a_single_company_deployment_cuts_its_company_less_card(card_env):
    session = _CardSession(card=None)
    out = await _cut(session, None)
    assert out["ok"] is True
    (card,) = [o for o in session.added if isinstance(o, VendorMonthlyScorecard)]
    assert card.organization_id is None


# ── the month-end job ─────────────────────────────────────────────────────────────

def _import_worker(monkeypatch):
    """src.worker imports arq at module level; host test runs have no arq installed."""
    try:
        import arq  # noqa: F401
    except ModuleNotFoundError:
        arq = types.ModuleType("arq")
        arq.cron = lambda fn, **kw: (fn, kw)
        connections = types.ModuleType("arq.connections")

        class RedisSettings:
            @classmethod
            def from_dsn(cls, _dsn):
                return cls()

        connections.RedisSettings = RedisSettings
        arq.connections = connections
        monkeypatch.setitem(sys.modules, "arq", arq)
        monkeypatch.setitem(sys.modules, "arq.connections", connections)
    return importlib.import_module("src.worker")


class _PairsSession:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    async def execute(self, stmt, *a, **k):
        self.sql.append(str(stmt))
        rows = self.rows
        return SimpleNamespace(fetchall=lambda: rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


async def _month_end(monkeypatch, rows):
    worker = _import_worker(monkeypatch)
    session = _PairsSession(rows)
    monkeypatch.setattr(worker, "AsyncSessionLocal", lambda: session)
    calls = []

    async def _card_for(_session, *, vendor_id, score_month, organization_id=None, **_kw):
        calls.append((vendor_id, organization_id))
        return {"ok": True}

    monkeypatch.setattr(sc, "generate_monthly_scorecard", _card_for)
    out = await worker.monthly_vendor_scorecards({})
    return session, calls, out


async def test_the_month_end_job_cuts_each_card_for_the_company_whose_scores_they_are(monkeypatch):
    v2 = uuid4()
    session, calls, out = await _month_end(monkeypatch, [(VENDOR, ORG), (VENDOR, None), (v2, OTHER)])
    assert calls == [(VENDOR, ORG), (VENDOR, None), (v2, OTHER)]
    assert "organization_id" in session.sql[0], "the companies come from the month's own rows"
    assert [g["organization_id"] for g in out["generated"]] == [str(ORG), None, str(OTHER)]


async def test_the_month_end_job_still_cuts_a_single_company_deployments_cards(monkeypatch):
    _, calls, _ = await _month_end(monkeypatch, [(VENDOR, None)])
    assert calls == [(VENDOR, None)]
