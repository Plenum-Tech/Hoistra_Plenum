"""The ingestion audit trail's filters, and the boundary they must never cross.

The page filters by person, building, document text and date. Every one of those is a
narrowing INSIDE one company's trail — none of them may ever widen it. That distinction is
the whole security surface here: a filter that replaces the organisation clause instead of
adding to it hands one tenant another tenant's documents, uploader names and building names,
and it would look like a working feature while it did it.

So these tests read the SQL the builder produces rather than its results. What matters is
not that the right rows came back from a fixture — it is that the organisation predicate is
present in EVERY statement, that every caller-supplied value arrives as a bound parameter,
and that the two derived tallies (per-outcome counts, the actor roster) are scoped the same
way the rows are.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.engines.auth import ingestion_audit as ia

ORG = uuid4()
OTHER_ORG = uuid4()
BLD = uuid4()
ACTOR = uuid4()


class Captured:
    """One execute() call: the SQL as text, and the parameters bound to it."""

    def __init__(self, sql, params):
        self.sql, self.params = " ".join(str(sql).split()), params or {}

    @property
    def kind(self):
        # Classified by what it SELECTS, not by what it filters on: both tallies now carry
        # the other's filters in their WHERE, so matching on the whole statement confuses them.
        s = self.sql.lower()
        selected = s.split(" from ")[0]
        if "count(*) as n" in selected:
            return "actors" if "actor_user_id" in selected else "by_outcome"
        if selected.startswith("select count(*)"):
            return "total"
        return "rows"


class FakeResult:
    def __init__(self, rows): self._rows = rows
    def mappings(self): return self
    def all(self): return self._rows
    def scalar(self): return len(self._rows)


class FakeSession:
    """Records every statement, answers each with an empty result of the right shape."""

    def __init__(self):
        self.calls: list[Captured] = []

    async def execute(self, stmt, params=None):
        c = Captured(stmt, params)
        self.calls.append(c)
        return FakeResult([])

    def of(self, kind):
        return [c for c in self.calls if c.kind == kind]


async def run(**kw):
    s = FakeSession()
    out = await ia.list_events(s, organization_id=kw.pop("organization_id", ORG), **kw)
    return s, out


# ── the boundary ─────────────────────────────────────────────────────────────────────

async def test_every_statement_is_scoped_to_one_company():
    """Rows, the total, the per-outcome tally and the actor roster are four separate
    statements. A filter added to the first and forgotten on the others is how a count or a
    name from another tenant reaches the screen without a single row doing so."""
    s, _ = await run(q="lift", building_id=BLD, actor_user_id=ACTOR, outcome="accepted")
    assert len(s.calls) >= 4, "rows, total, by_outcome and actors are all read"
    for c in s.calls:
        assert "organization_id = :o" in c.sql, f"unscoped statement: {c.sql[:90]}"
        assert c.params.get("o") == ORG


async def test_a_building_filter_narrows_within_the_company_and_never_replaces_it():
    s, _ = await run(building_id=BLD)
    rows = s.of("rows")[0]
    assert "organization_id = :o" in rows.sql
    assert rows.sql.index("organization_id = :o") < rows.sql.index("WHERE") + len(rows.sql)
    assert "AND" in rows.sql, "the building clause is added to the org clause, not swapped for it"
    assert str(BLD) in str(rows.params.values())


async def test_a_person_filter_narrows_within_the_company_too():
    s, _ = await run(actor_user_id=ACTOR)
    rows = s.of("rows")[0]
    assert "organization_id = :o" in rows.sql and "actor_user_id" in rows.sql
    assert rows.params.get("o") == ORG


@pytest.mark.parametrize("hostile", [
    "'; DROP TABLE plenum_cafm.ingestion_audit_events; --",
    "x' OR 1=1 --",
    "%' UNION SELECT email FROM plenum_cafm.users --",
])
async def test_a_search_term_is_bound_never_interpolated(hostile):
    """The search box is the only free text a caller controls. It reaches the database as a
    parameter or it does not reach it at all."""
    s, _ = await run(q=hostile)
    for c in s.calls:
        assert hostile not in c.sql, "the term was written into the statement"
    assert any(hostile in str(v) for v in s.of("rows")[0].params.values()), "…and bound instead"


async def test_an_unknown_role_is_refused_rather_than_quietly_ignored():
    """Ignoring it would return the unfiltered company trail to a caller who asked for a
    subset — more than they asked for, which is the wrong direction to be wrong in."""
    _, out = await run(actor_role="superuser'; --")
    assert out["ok"] is False
    assert "role" in out["error"].lower()


async def test_an_unknown_outcome_is_still_refused():
    _, out = await run(outcome="deleted")
    assert out["ok"] is False


# ── the filters themselves ───────────────────────────────────────────────────────────

async def test_the_search_term_reaches_document_person_and_building():
    s, _ = await run(q="northgate")
    sql = s.of("rows")[0].sql.lower()
    for column in ("document_name", "full_name", "b.name"):
        assert column in sql, f"the search must look at {column}"


async def test_a_date_range_binds_both_ends():
    since = datetime(2026, 9, 1, tzinfo=timezone.utc)
    until = datetime(2026, 9, 17, tzinfo=timezone.utc)
    s, _ = await run(since=since, until=until)
    rows = s.of("rows")[0]
    assert "occurred_at >=" in rows.sql and "occurred_at <" in rows.sql
    assert since in rows.params.values() and until in rows.params.values()


async def test_limit_is_capped_however_much_is_asked_for():
    s, _ = await run(limit=100000)
    assert s.of("rows")[0].params["lim"] <= 500


# ── the two derived tallies ──────────────────────────────────────────────────────────

async def test_the_outcome_tally_respects_every_filter_except_outcome():
    """The chips read these. A chip whose own count changed when you pressed it could never
    tell you what pressing a different one would give you — and a chip that ignored the date
    range would contradict the rows beneath it."""
    s, _ = await run(outcome="accepted", building_id=BLD, q="lift")
    tally = s.of("by_outcome")[0]
    assert "building_id" in tally.sql, "the building filter still applies"
    assert "document_name" in tally.sql.lower(), "so does the search"
    assert "e.outcome = :oc" not in tally.sql, "but not the chip being counted"


async def test_the_actor_roster_respects_every_filter_except_the_person():
    """Same rule, one dimension over: the picker's counts must survive picking a name."""
    s, _ = await run(actor_user_id=ACTOR, building_id=BLD, outcome="accepted")
    roster = s.of("actors")[0]
    assert "building_id" in roster.sql and "e.outcome = :oc" in roster.sql
    assert "actor_user_id = :au" not in roster.sql, "counting a person out of their own tally"


async def test_the_roster_comes_back_named_and_counted():
    s = FakeSession()

    async def execute(stmt, params=None):
        c = Captured(stmt, params)
        s.calls.append(c)
        if c.kind == "actors":
            return FakeResult([{"actor_user_id": str(ACTOR), "actor_name": "Clara Novak",
                                "actor_role": "user", "n": 58}])
        return FakeResult([])

    s.execute = execute
    out = await ia.list_events(s, organization_id=ORG)
    assert out["actors"] == [{"user_id": str(ACTOR), "name": "Clara Novak", "role": "user", "count": 58}]


# ── the route: what a caller may and may not ask for ─────────────────────────────────

def test_the_route_exposes_every_filter_the_page_offers():
    """A filter the page has and the route lacks is a filter that silently runs over one
    fetched page instead of the register — the page then tells the reader it searched the
    trail when it searched 200 rows of it."""
    import inspect

    from src.api.routes.admin import ingestion_audit_trail

    named = set(inspect.signature(ingestion_audit_trail).parameters)
    for p in ("outcome", "q", "building_id", "actor_user_id", "actor_role", "since", "until",
              "limit", "offset"):
        assert p in named, f"the route cannot be asked for {p}"


def test_the_route_never_lets_a_caller_name_the_company():
    """`organization_id` on this route is admin_scope's superadmin override and nothing else.
    If the handler took one of its own and passed it through, any admin could read any
    tenant's trail by adding a query parameter."""
    import inspect

    from src.api.routes.admin import ingestion_audit_trail

    src = inspect.getsource(ingestion_audit_trail)
    assert "organization_id=scope.organization_id" in src, \
        "the company comes from the verified scope, never from the query string"
    assert "organization_id: UUID" not in src, "and the handler declares no organization_id of its own"
