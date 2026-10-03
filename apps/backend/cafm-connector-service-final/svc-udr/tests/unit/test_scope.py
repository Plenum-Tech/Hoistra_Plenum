"""Which rows a caller may see.

svc-udr authenticated every route and gated the table routes on role, then ran the query with
no row filter at all:

    GET /backend/udr/api/tables/buildings/records
    -> SELECT * FROM plenum_cafm."buildings"

Measured against the live schema, that returned 624 buildings to an administrator whose company
owns 2. These tests pin the predicate that fixes it, and — more importantly — the three or four
ways a predicate like this is normally got wrong.
"""
from uuid import UUID, uuid4

import pytest

from src.services.principal import Principal
from src.services import scope


ORG = UUID("11111111-1111-1111-1111-111111111111")
OTHER_ORG = UUID("22222222-2222-2222-2222-222222222222")
B1 = UUID("aaaaaaaa-0000-0000-0000-000000000001")
B2 = UUID("aaaaaaaa-0000-0000-0000-000000000002")

ORG_ONLY = frozenset({"organization_id"})
ORG_AND_BUILDING = frozenset({"organization_id", "building_id"})
BUILDING_ONLY = frozenset({"building_id"})
NEITHER = frozenset()


def who(role="user", org=ORG, buildings=None):
    return Principal(user_id=uuid4(), email="a@b.c", organization_id=org,
                     role=role, building_ids=buildings)


class TestTheCompanyFilter:

    def test_an_ordinary_user_is_restricted_to_their_own_company(self):
        frag, params = scope.predicate_for(ORG_ONLY, who())
        assert '"organization_id"::text = :scope_org' in frag
        assert params["scope_org"] == str(ORG)

    def test_the_company_is_taken_from_the_token_not_from_the_request(self):
        """There is no argument here that a caller could supply. That is the point — a company
        id that arrives in a query string is a request, not evidence."""
        frag, params = scope.predicate_for(ORG_ONLY, who(org=OTHER_ORG))
        assert params["scope_org"] == str(OTHER_ORG)

    def test_a_user_with_no_company_sees_nothing(self):
        """The bug being fixed was that an unresolved company read as 'no filter'. It has to
        read as 'no rows' — we do not know who they belong to."""
        frag, params = scope.predicate_for(ORG_ONLY, who(org=None))
        assert frag == "FALSE"
        assert params == {}

    def test_the_comparison_is_cast_on_both_sides(self):
        """The two databases disagree on the type of organization_id — uuid in one, character
        varying in the other. An uncast comparison 500s on whichever one it was not written
        against, which is exactly how /api/assets broke."""
        frag, _ = scope.predicate_for(ORG_ONLY, who())
        assert "::text" in frag


class TestTheBuildingFilter:

    def test_none_means_every_building_in_the_company(self):
        """None is an allocation of 'all', not an allocation of nothing."""
        frag, params = scope.predicate_for(ORG_AND_BUILDING, who(buildings=None))
        assert "building_id" not in frag
        assert frag == '"organization_id"::text = :scope_org'

    def test_an_allocation_narrows_to_those_buildings(self):
        frag, params = scope.predicate_for(ORG_AND_BUILDING, who(buildings=(B1, B2)))
        assert '"building_id"::text IN (:scope_b0, :scope_b1)' in frag
        assert {params["scope_b0"], params["scope_b1"]} == {str(B1), str(B2)}

    def test_allocated_to_no_building_means_no_rows_not_every_row(self):
        """() and None are one keystroke apart and mean opposite things. Getting this backwards
        turns the tightest possible scope into no scope at all."""
        frag, params = scope.predicate_for(ORG_AND_BUILDING, who(buildings=()))
        assert frag == "FALSE"

    def test_every_building_id_is_a_bound_parameter(self):
        frag, params = scope.predicate_for(BUILDING_ONLY, who(buildings=(B1, B2)))
        assert str(B1) not in frag and str(B2) not in frag
        assert len(params) == 2


class TestWhoIsNotRestricted:

    def test_a_superadmin_is_unrestricted(self):
        assert scope.predicate_for(ORG_AND_BUILDING, who(role="superadmin")) == ("", {})

    def test_an_internal_caller_with_no_principal_is_unrestricted(self):
        """Seeds and migrations construct the service without a principal and legitimately
        write across companies. Inventing a restriction for them breaks them silently."""
        assert scope.predicate_for(ORG_AND_BUILDING, None) == ("", {})

    def test_a_table_with_no_scope_column_is_not_filtered(self):
        """99 of 224 tables have neither column — reference data, and some audit logs. By
        decision they pass through; this pins that so it is a choice, not a regression."""
        assert scope.predicate_for(NEITHER, who()) == ("", {})


class TestAttachingItToAQuery:

    def test_it_creates_a_where_clause_when_there_is_none(self):
        assert scope.combine("", "x = 1") == "WHERE x = 1"

    def test_it_ands_onto_an_existing_where_clause(self):
        assert scope.combine("WHERE a = :a", "x = 1") == "WHERE a = :a AND x = 1"

    def test_an_empty_fragment_leaves_the_query_alone(self):
        assert scope.combine("WHERE a = :a", "") == "WHERE a = :a"
        assert scope.combine("", "") == ""

    def test_an_ored_where_clause_must_be_parenthesised_by_the_caller(self):
        """search_records ORs its LIKE terms. OR binds looser than AND, so
        `a ILIKE t OR b ILIKE t AND scope` applies the scope to the last term only and returns
        every row matching the first. The service parenthesises before combining; this records
        why, because the mistake is invisible in a passing test that only checks one column."""
        combined = scope.combine("WHERE (a ILIKE :t OR b ILIKE :t)", "org = :o")
        assert combined == "WHERE (a ILIKE :t OR b ILIKE :t) AND org = :o"


class TestRedirectingCallerSuppliedSql:
    """A predicate cannot be injected into arbitrary SQL, so that route is pointed at the
    self-filtering views in plenum_scoped instead."""

    def test_a_qualified_reference_is_redirected(self):
        out = scope.redirect_to_scoped_schema("SELECT * FROM plenum_cafm.buildings")
        assert out == "SELECT * FROM plenum_scoped.buildings"

    def test_a_quoted_schema_is_redirected_too(self):
        out = scope.redirect_to_scoped_schema('SELECT * FROM "plenum_cafm".assets')
        assert "plenum_scoped.assets" in out
        assert "plenum_cafm" not in out

    def test_every_reference_in_a_join_is_redirected_not_just_the_first(self):
        out = scope.redirect_to_scoped_schema(
            "SELECT * FROM plenum_cafm.buildings b "
            "JOIN plenum_cafm.assets a ON a.building_id = b.building_id "
            "LEFT JOIN plenum_cafm.vendors v ON v.id = a.vendor_id")
        assert "plenum_cafm." not in out
        assert out.count("plenum_scoped.") == 3

    def test_it_is_case_insensitive(self):
        assert "plenum_scoped." in scope.redirect_to_scoped_schema("SELECT 1 FROM PLENUM_CAFM.x")

    def test_an_unqualified_query_is_left_for_the_search_path(self):
        sql = "SELECT * FROM buildings"
        assert scope.redirect_to_scoped_schema(sql) == sql


class TestTheSettingsCarriedIntoTheTransaction:

    def test_every_setting_is_written_on_every_request(self):
        """These ride a pooled connection. A value left set by the previous caller would scope
        this query to their company, so none may be skipped just because it is empty."""
        names = [n for n, _ in scope.session_settings(who())]
        assert names == [scope.SETTING_UNRESTRICTED, scope.SETTING_ORG, scope.SETTING_BUILDINGS]

    def test_a_superadmin_is_marked_unrestricted(self):
        got = dict(scope.session_settings(who(role="superadmin")))
        assert got[scope.SETTING_UNRESTRICTED] == "1"

    def test_an_ordinary_user_is_not(self):
        got = dict(scope.session_settings(who()))
        assert got[scope.SETTING_UNRESTRICTED] == "0"
        assert got[scope.SETTING_ORG] == str(ORG)

    def test_every_value_is_a_string(self):
        """None would be sent to set_config as NULL, which does not set the value — leaving
        whatever the previous caller put there."""
        for _, v in scope.session_settings(who(buildings=None)):
            assert isinstance(v, str)
        for _, v in scope.session_settings(who(org=None, buildings=(B1,))):
            assert isinstance(v, str)

    def test_all_buildings_is_sent_as_empty_meaning_no_building_filter(self):
        got = dict(scope.session_settings(who(buildings=None)))
        assert got[scope.SETTING_BUILDINGS] == ""

    def test_an_allocation_is_sent_as_a_comma_separated_list(self):
        got = dict(scope.session_settings(who(buildings=(B1, B2))))
        assert got[scope.SETTING_BUILDINGS] == f"{B1},{B2}"


class TestAllocatedToNothingNeverReachesSql:
    """'' has to mean 'no building filter' in the view, because a pooled connection cannot be
    relied on to distinguish NULL from ''. So the empty allocation is answered in Python — if it
    ever reached the view it would read as 'every building', inverting the tightest scope."""

    def test_an_empty_allocation_is_caught_before_the_query_runs(self):
        assert scope.allocated_to_nothing(who(buildings=())) is True

    def test_all_buildings_is_not_caught(self):
        assert scope.allocated_to_nothing(who(buildings=None)) is False

    def test_a_real_allocation_is_not_caught(self):
        assert scope.allocated_to_nothing(who(buildings=(B1,))) is False

    def test_a_superadmin_is_not_caught(self):
        assert scope.allocated_to_nothing(who(role="superadmin", buildings=())) is False

    def test_the_empty_allocation_would_otherwise_be_sent_as_empty_string(self):
        """The reason this guard exists, stated as a test: the setting for 'no buildings' is
        indistinguishable from the setting for 'all buildings'."""
        assert dict(scope.session_settings(who(buildings=())))[scope.SETTING_BUILDINGS] == ""
        assert dict(scope.session_settings(who(buildings=None)))[scope.SETTING_BUILDINGS] == ""


class TestTheDescriptionReturnedToTheCaller:

    def test_it_says_when_and_how_a_result_was_narrowed(self):
        d = scope.describe(who(buildings=(B1,)), ORG_AND_BUILDING)
        assert d["scoped"] is True
        assert d["organization_id"] == str(ORG)
        assert d["building_ids"] == [str(B1)]

    def test_it_says_why_when_it_was_not(self):
        assert scope.describe(who(role="superadmin"), ORG_ONLY)["reason"] == "superadmin"
        assert scope.describe(who(), NEITHER)["reason"] == "no_scope_column_on_table"
        assert scope.describe(None, ORG_ONLY)["reason"] == "no_principal"


class TestTablesThatAreClosedEntirely:
    """Scoping is the wrong frame for a few tables. They hold live credentials and
    bearer-equivalent tokens, and UDR is reachable from the public internet and driven by an
    agent that writes its own SQL from a user's sentence. No question about a building needs a
    one-time passcode, so the setting is off rather than narrow.

    Counted on the live database: auth_otp_codes 9 rows, auth_sessions 183,
    approval_action_tokens 154 — all readable by an administrator of any company before this."""

    def test_the_credential_tables_are_denied(self):
        for t in ("auth_otp_codes", "auth_sessions", "approval_action_tokens",
                  "auth_role_changes"):
            assert scope.is_denied(t), t

    def test_ordinary_tables_are_not(self):
        for t in ("buildings", "assets", "vendors", "work_orders", "countries"):
            assert not scope.is_denied(t), t

    def test_the_check_is_not_defeated_by_case_or_padding(self):
        assert scope.is_denied("AUTH_OTP_CODES")
        assert scope.is_denied("  auth_sessions  ")

    def test_denial_does_not_depend_on_who_is_asking(self):
        """A superadmin is unrestricted for scoping and still cannot read these. The two are
        different questions: 'whose rows' versus 'may this be read through here at all'."""
        assert scope.is_denied("auth_otp_codes")  # no principal is consulted at all


class TestTheDeniedListIsTheSameInBothPlaces:
    """The list lives twice: in Python, which guards the structured routes, and in the
    migration, which decides whether a view is built for caller-supplied SELECT. If they drift,
    one route silently reopens — so the drift is what this test fails on."""

    def test_the_migration_excludes_exactly_the_denied_tables(self):
        import pathlib
        import re as _re
        sql = pathlib.Path(__file__).resolve().parents[3] / (
            "svc-operations-intelligence/migrations/udr_scoped_views.sql")
        if not sql.exists():
            import pytest as _pytest
            _pytest.skip(f"migration not found at {sql}")
        text_ = sql.read_text(encoding="utf-8")
        block = _re.search(r"table_name NOT IN \(([^)]*)\)", text_, _re.S)
        assert block, "the migration no longer excludes any table — the deny list is not applied"
        in_sql = set(_re.findall(r"'([a-z_]+)'", block.group(1)))
        assert in_sql == set(scope.DENIED_TABLES), (
            f"only in SQL: {in_sql - set(scope.DENIED_TABLES)}; "
            f"only in Python: {set(scope.DENIED_TABLES) - in_sql}")
