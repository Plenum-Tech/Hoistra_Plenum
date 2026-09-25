"""Custom report cards: the cadence maths, the boundary, and what a refresh records.

The shell kept reports in one browser and re-ran them only while the tab was open. The
server keeps them now. These pin the parts that can be pinned without a database: which
cadences are accepted and when they next fall (in the owner's zone), that every route needs
a caller and only ever reads the caller's own rows, and that a refresh runs as the owner
with a minted token and records a failure as a run rather than losing it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from src.api.routes import auth as auth_routes
from src.app import app
from src.db import get_session
from src.engines.auth.tokens import Principal
from src.engines.reports import cards as C
from src.engines.reports import scheduler as S
from src.models.reports import ReportCard

ORG = UUID("11111111-1111-5111-8111-111111111111")
LONDON = "Europe/London"


def at(y, mo, d, h=0, mi=0, tz=timezone.utc):
    return datetime(y, mo, d, h, mi, tzinfo=tz)


# ── cadences ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("given,expected", [
    ("1h", {"every_minutes": 60}),
    ("daily", {"daily_at": "02:00"}),
    ({"every_minutes": "30"}, {"every_minutes": 30}),
    ({"daily_at": "7:05"}, {"daily_at": "07:05"}),
    ({"days": [5, 1, 1], "time": "14:00"}, {"days": [1, 5], "time": "14:00"}),
    ({"days": [0]}, {"days": [0], "time": "14:00"}),
])
def test_accepted_cadences_normalise(given, expected):
    assert C.parse_refresh(given) == expected


@pytest.mark.parametrize("given,reason", [
    ("hourly-ish", "bad_refresh"),
    ({"every_minutes": 1}, "bad_refresh"),
    ({"every_minutes": 60, "daily_at": "02:00"}, "bad_refresh"),
    ({"daily_at": "25:00"}, "bad_time"),
    ({"days": []}, "no_days"),
    ({"days": [7]}, "no_days"),
    ("days", "bad_refresh"),      # the pick-days preset needs the days, not just the key
    (42, "bad_refresh"),
])
def test_refused_cadences_say_why(given, reason):
    with pytest.raises(C.RefreshError) as e:
        C.parse_refresh(given)
    assert e.value.reason == reason


def test_timezones_are_iana_names():
    assert C.parse_timezone(None) == "UTC"
    assert C.parse_timezone(LONDON) == LONDON
    with pytest.raises(C.RefreshError) as e:
        C.parse_timezone("Mars/Olympus")
    assert e.value.reason == "bad_timezone"


def test_an_interval_counts_from_now():
    assert C.next_run_at({"every_minutes": 90}, "UTC", at(2026, 9, 14, 10)) == at(2026, 9, 14, 11, 30)


def test_a_daily_time_is_the_owners_clock_not_utc():
    # 02:00 London in September is 01:00 UTC. From 00:30 UTC (01:30 BST) it is still today.
    nxt = C.next_run_at({"daily_at": "02:00"}, LONDON, at(2026, 9, 14, 0, 30))
    assert nxt == at(2026, 9, 14, 1, 0)
    # From 01:30 UTC (02:30 BST) today's has passed: tomorrow, still 01:00 UTC.
    assert C.next_run_at({"daily_at": "02:00"}, LONDON, at(2026, 9, 14, 1, 30)) == at(2026, 9, 15, 1, 0)


def test_a_daily_time_survives_the_clock_change():
    # BST ends 2026-10-25. 02:00 London on the 26th is 02:00 UTC, not 01:00.
    assert C.next_run_at({"daily_at": "02:00"}, LONDON, at(2026, 10, 25, 12)) == at(2026, 10, 26, 2, 0)


def test_chosen_days_use_sunday_as_zero_like_the_shell():
    # 2026-09-14 is a Monday. Days {0 (Sun), 3 (Wed)} at 14:00 UTC → Wednesday the 16th.
    assert C.next_run_at({"days": [0, 3], "time": "14:00"}, "UTC", at(2026, 9, 14, 10)) == at(2026, 9, 16, 14)
    # Later on Wednesday itself → Sunday the 20th.
    assert C.next_run_at({"days": [0, 3], "time": "14:00"}, "UTC", at(2026, 9, 16, 15)) == at(2026, 9, 20, 14)


def test_the_label_reads_back_as_a_person_would_say_it():
    assert C.refresh_label({"every_minutes": 60}) == "every 1 hour"
    assert C.refresh_label({"every_minutes": 360}) == "every 6 hours"
    assert C.refresh_label({"every_minutes": 1440}) == "every 24 hours"
    assert C.refresh_label({"daily_at": "02:00"}) == "daily at 02:00"
    assert C.refresh_label({"days": [1, 3], "time": "09:30"}) == "Mon, Wed at 09:30"
    assert C.refresh_label({"days": list(range(7)), "time": "09:30"}) == "every day at 09:30"


def test_presets_are_all_parseable_except_the_day_picker():
    for p in C.PRESETS:
        if p.get("pick_days"):
            continue
        assert C.parse_refresh(p["key"]) == p["refresh"]


# ── the routes ───────────────────────────────────────────────────────────────

def principal(user_id=None, role="user", buildings=None):
    return Principal(user_id=user_id or uuid4(), email="x@example.com", organization_id=ORG,
                     session_id=None, issued_at=datetime.now(timezone.utc), password_changed_at=0,
                     role=role, can_ingest=False, building_ids=buildings)


class Recording:
    """A session that records statements and answers with the rows it was given."""
    def __init__(self, rows=(), scalar=None):
        self.rows, self.scalar_value, self.statements, self.added = list(rows), scalar, [], []

    class _R:
        def __init__(self, rows, scalar): self._rows, self._scalar = rows, scalar
        def scalars(self): return self
        def all(self): return self._rows
        def scalar_one_or_none(self): return self._rows[0] if self._rows else None
        def scalar(self): return self._scalar
        def mappings(self): return self
        def first(self): return self._rows[0] if self._rows else None
        rowcount = 0

    async def execute(self, stmt, params=None):
        self.statements.append((stmt, params))
        return self._R(self.rows, self.scalar_value)
    async def commit(self): pass
    async def flush(self): pass
    def add(self, obj): self.added.append(obj)


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.clear()


def wire(p, session):
    async def _s():
        yield session
    async def _p():
        return p
    app.dependency_overrides[get_session] = _s
    app.dependency_overrides[auth_routes.current_principal] = _p


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/reports"), ("POST", "/api/reports"), ("GET", "/api/reports/refresh-options"),
    ("POST", "/api/reports/cards"), ("GET", f"/api/reports/cards/{uuid4()}"),
    ("DELETE", f"/api/reports/cards/{uuid4()}"), ("POST", f"/api/reports/cards/{uuid4()}/run"),
])
def test_every_report_route_needs_a_caller(client, method, path):
    async def _s():
        yield Recording()
    app.dependency_overrides[get_session] = _s
    r = client.request(method, path, json={})
    assert r.status_code == 401, (method, path, r.status_code)
    assert r.json()["detail"]["reason"] == "missing_token"


def test_refresh_options_are_the_presets(client):
    wire(principal(), Recording())
    body = client.get("/api/reports/refresh-options").json()
    assert [o["key"] for o in body["options"]] == ["30m", "1h", "6h", "12h", "24h", "daily", "days"]
    assert body["days"][0] == "Sun" and body["min_every_minutes"] == 5


def test_a_card_that_is_not_yours_is_not_found_not_forbidden(client):
    # The engine's lookup carries owner_user_id; a Recording that returns no row is exactly
    # what the database returns for somebody else's card.
    s = Recording()
    wire(principal(), s)
    for method, path in (("GET", f"/api/reports/cards/{uuid4()}"), ("DELETE", f"/api/reports/cards/{uuid4()}"),
                         ("PATCH", f"/api/reports/{uuid4()}")):
        r = client.request(method, path, json={"name": "x"} if method == "PATCH" else None)
        assert r.status_code == 404, (method, path)
        assert r.json()["detail"]["reason"].endswith("_not_found")
    stmt = str(s.statements[0][0])
    assert "owner_user_id" in stmt and "removed_at IS NULL" in stmt


def test_a_bad_cadence_is_refused_before_anything_is_written(client):
    s = Recording()
    wire(principal(), s)
    r = client.post("/api/reports/cards", json={"prompt": "Which certificates lapse this month?",
                                               "refresh": {"every_minutes": 1}})
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "bad_refresh"
    assert s.added == []


def test_removing_a_card_touches_only_that_card(client):
    me = uuid4()
    card = ReportCard(id=uuid4(), report_id=uuid4(), owner_user_id=me, name="Lapsing", prompt="q",
                      refresh={"every_minutes": 60}, timezone="UTC", position=0, enabled=True, status="ready")
    wire(principal(user_id=me), Recording([card]))
    r = client.delete(f"/api/reports/cards/{card.id}")
    assert r.status_code == 200 and r.json()["removed"] is True
    assert card.removed_at is not None and card.enabled is False


# ── a refresh, as the owner ──────────────────────────────────────────────────

class RunSession(Recording):
    """Answers the card lookup with the card and the owner lookup with an active owner."""
    def __init__(self, card, owner_status="active"):
        super().__init__()
        self.card, self.owner_status = card, owner_status

    async def execute(self, stmt, params=None):
        self.statements.append((stmt, params))
        sql = str(stmt)
        if "FROM plenum_cafm.users" in sql:
            row = {"id": self.card.owner_user_id, "email": "o@example.com", "organization_id": ORG,
                   "status": self.owner_status, "password_changed_at": None, "platform_role": "user"}
            return self._R([row], None)
        if "report_cards" in sql and "DELETE" not in sql:
            return self._R([self.card], None)
        return self._R([], None)


def card_for(owner=None):
    return ReportCard(id=uuid4(), report_id=uuid4(), owner_user_id=owner or uuid4(), name="Lapsing certs",
                      prompt="Which certificates lapse this month?", source_page="Compliance",
                      refresh={"every_minutes": 60}, timezone="UTC", position=0, enabled=True, status="pending")


@pytest.mark.asyncio
async def test_a_refresh_is_asked_as_the_owner_and_recorded(monkeypatch):
    card = card_for()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.read()
        return httpx.Response(200, json={"success": True, "answer": "Two lapse: FRA at Town Hall …",
                                         "tool_calls": [{"tool": "list_certificates", "input": {"expiry_month": "this"}}]})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://deep-agents")
    s = RunSession(card)
    run = await C.run_card(s, card.id, trigger="manual", http=http)
    assert run.ok and run.trigger == "manual" and "Two lapse" in run.answer
    assert run.tool_calls == [{"tool": "list_certificates", "input": {"expiry_month": "this"}}]
    # As the owner: a bearer minted for them, carrying their id and no session.
    from src.engines.auth.tokens import decode_access_token
    claims = decode_access_token(seen["auth"].split(" ", 1)[1])
    assert claims["sub"] == str(card.owner_user_id) and claims["sid"] is None
    body = seen["body"].decode()
    assert "Which certificates lapse this month?" in body and "custom report card" in body
    assert card.status == "ready" and card.last_run_at is not None and card.next_run_at > card.last_run_at


@pytest.mark.asyncio
async def test_a_failed_refresh_is_a_run_not_a_silence():
    card = card_for()
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503, text="down")),
                             base_url="http://deep-agents")
    s = RunSession(card)
    run = await C.run_card(s, card.id, http=http)
    assert run.ok is False and "503" in run.error
    assert card.status == "error" and card.last_error == run.error
    assert card.next_run_at is not None      # it will try again on its cadence


@pytest.mark.asyncio
async def test_an_owner_who_was_deactivated_pauses_the_card():
    card = card_for()
    s = RunSession(card, owner_status="inactive")
    run = await C.run_card(s, card.id)
    assert run.ok is False and "owner_inactive" in run.error
    assert card.status == "paused" and card.enabled is False and card.next_run_at is None


@pytest.mark.asyncio
async def test_a_card_already_running_is_not_asked_twice():
    card = card_for()
    card.status = "running"
    s = RunSession(card)
    assert await C.run_card(s, card.id) is None
    assert all("INSERT" not in str(st) for st, _ in s.statements)


def test_the_claim_takes_one_due_live_card_and_skips_locked_rows():
    sql = " ".join(S.CLAIM_SQL.split())
    assert "enabled AND removed_at IS NULL" in sql
    assert "status <> 'running'" in sql and "next_run_at <= now()" in sql
    assert "LIMIT 1 FOR UPDATE SKIP LOCKED" in sql and "RETURNING id" in sql
    assert "SET status = 'running'" in sql


def test_the_scheduler_is_on_by_default_and_points_at_the_local_orchestrator(monkeypatch):
    """The DEFAULT, not whatever this host is set to.

    This read the live `settings` singleton, so it asserted the CONFIGURED value and failed
    on any deployment that had turned the scheduler off — which docker-compose.azure-safe.yml
    does deliberately, and for a good reason: the scheduler is a writer that UPDATEs
    report_cards every 30 seconds for as long as the container is up, and against the
    production database from a developer's machine that is a continuous background write
    nobody asked for.

    So the test was red on every correctly-configured local stack, and green only where the
    safety had been removed. Constructed with the variable absent, what is under test is the
    default in the Field — which is what the name claims and the only part worth pinning.

    The same shape as TestQ1EmailHandoff's delivery-mode test next door; both were written
    against the singleton and both failed for the same reason.
    """
    from src.config import Settings
    # Every one of the three, because every one of them is set by the compose file: the
    # base URL is the service name on the container network, not the loopback default.
    for var in ("REPORT_SCHEDULER_ENABLED", "DEEP_AGENTS_BASE_URL",
                "REPORT_SCHEDULER_TICK_SECONDS"):
        monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(var.lower(), raising=False)
    s = Settings(_env_file=None)
    assert s.report_scheduler_enabled is True
    assert s.deep_agents_base_url.startswith("http://127.0.0.1:8008")
    assert s.report_scheduler_tick_seconds >= 5
