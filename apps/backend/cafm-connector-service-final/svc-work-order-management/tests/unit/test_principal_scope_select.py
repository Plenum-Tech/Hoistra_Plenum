"""scope_select() drew the tenancy boundary for assets, work orders and locations — and for
an admin it drew no boundary at all.

``building_ids is None`` means "every building in the company" (services/principal.py's own
docstring). The first implementation returned the query untouched, which is not the whole
company but the whole database: any admin listing /api/assets or /api/work-orders received
every other tenant's rows, while the Assets screen told them the list was "already scoped to
your building allocation".

These rows carry no organization column of their own, so the boundary runs through
plenum_cafm.buildings, which does.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Column, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import declarative_base
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from src.services.principal import Principal, scope_select

Base = declarative_base()


class Thing(Base):
    __tablename__ = "things"
    __table_args__ = {"schema": "plenum_cafm"}
    id = Column(PGUUID(as_uuid=True), primary_key=True)
    building_id = Column(PGUUID(as_uuid=True))


ORG = UUID("11111111-1111-4111-8111-111111111111")
OTHER_ORG = UUID("22222222-2222-4222-8222-222222222222")
B1, B2 = uuid4(), uuid4()


def principal(**over):
    base = dict(user_id=uuid4(), email="a@b.c", organization_id=ORG, role="admin", building_ids=None)
    base.update(over)
    return Principal(**base)


def sql(q):
    return str(q.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_an_admin_is_bounded_by_their_company_not_left_unbounded():
    """The leak: this used to compile to a bare SELECT with no WHERE at all."""
    out = sql(scope_select(select(Thing), principal(), Thing.building_id))
    assert "WHERE" in out, "an admin query must carry a boundary"
    assert "plenum_cafm.buildings" in out
    assert "organization_id" in out
    assert str(ORG) in out, "and it must be THEIR organisation"


def test_another_companys_organisation_never_appears_in_the_predicate():
    out = sql(scope_select(select(Thing), principal(), Thing.building_id))
    assert str(OTHER_ORG) not in out


def test_an_allocated_user_is_still_narrowed_to_their_own_buildings():
    """Unchanged behaviour — the allocation is more specific than the company."""
    out = sql(scope_select(select(Thing), principal(building_ids=(B1, B2)), Thing.building_id))
    assert str(B1) in out and str(B2) in out
    assert "plenum_cafm.buildings" not in out, "no need to consult the company for an explicit allocation"


def test_a_user_allocated_to_nothing_still_matches_nothing():
    out = sql(scope_select(select(Thing), principal(building_ids=()), Thing.building_id))
    assert "false" in out.lower()


def test_a_superadmin_still_reads_across_companies():
    """The one caller meant to see every tenant keeps doing so; that is what the role is for."""
    out = sql(scope_select(select(Thing), principal(role="superadmin"), Thing.building_id))
    assert "WHERE" not in out
    for role in ("SuperAdmin", "SUPERADMIN", " superadmin "):
        assert "WHERE" not in sql(scope_select(select(Thing), principal(role=role), Thing.building_id)), role


def test_a_principal_with_no_company_matches_nothing_rather_than_everything():
    """Failing closed is the only safe default for a tenancy boundary. No active account is
    in this state, so nothing legitimate is refused by it."""
    out = sql(scope_select(select(Thing), principal(organization_id=None), Thing.building_id))
    assert "false" in out.lower()
    assert "plenum_cafm.buildings" not in out


def test_an_explicit_allocation_outranks_a_missing_company():
    """building_ids is the more specific statement and is honoured even with no org set."""
    out = sql(scope_select(select(Thing), principal(organization_id=None, building_ids=(B1,)), Thing.building_id))
    assert str(B1) in out
    assert "false" not in out.lower()


# ── A superadmin naming a company gets it on EVERY read ─────────────────────────────────
# The Assets screen showed why this matters. The buildings register was asked for company
# 0001 and returned its 622 buildings; /api/assets was asked for nothing and returned company
# 0005's assets as well, so fifteen assets arrived belonging to a building the register had
# correctly left out. The page could only describe them as "not in your buildings register" —
# true, and completely misleading, because the building exists in another company.
from src.services.principal import acting_organization


class TestActingOrganization:

    def test_a_superadmin_gets_the_company_they_asked_for(self):
        assert acting_organization(principal(role="superadmin"), OTHER_ORG) == OTHER_ORG

    def test_a_superadmin_asking_for_nothing_still_reads_across_companies(self):
        """None is not a request for a company; it leaves the role's reach alone."""
        p = principal(role="superadmin")
        assert acting_organization(p, None) == p.organization_id
        assert "WHERE" not in sql(scope_select(select(Thing), p, Thing.building_id))

    def test_a_plain_caller_gets_their_own_company_whatever_they_ask_for(self):
        """The parameter can only narrow a read. Honouring it for anyone else would make a
        query string the thing that decides which tenant you are."""
        for role in ("user", "admin", "manager", ""):
            assert acting_organization(principal(role=role), OTHER_ORG) == ORG, role


class TestSuperadminPinnedToOneCompany:

    def test_naming_a_company_narrows_the_query_to_that_company(self):
        out = sql(scope_select(select(Thing), principal(role="superadmin"),
                               Thing.building_id, organization_id=OTHER_ORG))
        assert "plenum_cafm.buildings" in out
        assert "organization_id" in out

    def test_the_pin_is_ignored_for_a_caller_who_could_not_have_set_it(self):
        """A non-superadmin is their own company in scope_select too, not just in
        acting_organization — the same rule stated twice, deliberately."""
        out = sql(scope_select(select(Thing), principal(role="user"),
                               Thing.building_id, organization_id=OTHER_ORG))
        assert "plenum_cafm.buildings" in out
        assert str(OTHER_ORG) not in out

    def test_an_explicit_building_allocation_still_outranks_the_company(self):
        """The narrower statement wins, as it always has."""
        out = sql(scope_select(select(Thing), principal(role="superadmin", building_ids=(B1,)),
                               Thing.building_id, organization_id=OTHER_ORG))
        assert str(B1) in out

    def test_omitting_the_argument_changes_nothing_for_any_caller(self):
        """Every existing call site passes no organization_id and must behave as before."""
        for role in ("superadmin", "admin", "user"):
            p = principal(role=role)
            assert sql(scope_select(select(Thing), p, Thing.building_id)) == \
                   sql(scope_select(select(Thing), p, Thing.building_id, organization_id=None)), role
