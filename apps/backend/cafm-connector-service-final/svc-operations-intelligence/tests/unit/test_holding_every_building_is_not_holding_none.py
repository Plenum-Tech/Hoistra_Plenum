"""An account that holds every building is told which ones those are.

`/api/auth/me` returns `buildings[]`, and the client picks the building an ingest is filed
against out of it. It filled that list only for an account restricted to named buildings and
returned `[]` for everything else — so an admin or superadmin, whose `building_ids` is None
*because* they are unrestricted, got an empty list, and the picker said "No building matches
that. Check the code, or hoist a new one." over a database holding two.

`building_ids is None` is "every building in this organisation". `building_ids == ()` is
"allocated to nothing". They are different facts and had one answer.

It hid for a long time because selecting a building narrows `building_ids` to that one element,
so any session that had ever chosen one took the first branch and saw a list. A session that
never has — a new browser, an incognito window — is the one that gets nothing, and that is the
first ingest of the day for the person setting the data up.
"""
from __future__ import annotations

import inspect
import re

from src.api.routes import auth as auth_routes


def _me_source() -> str:
    """The body of the /me handler, whatever it is decorated as."""
    src = inspect.getsource(auth_routes)
    start = src.index('user["building_ids"] = (')
    end = src.index('class SelectBuilding', start)
    return src[start:end]


class TestTheThreeCasesAreThreeAnswers:
    def test_named_buildings_are_looked_up_by_id(self):
        body = _me_source()
        assert "if principal.building_ids:" in body
        assert "WHERE building_id = ANY(CAST(:b AS uuid[]))" in body

    def test_unrestricted_lists_the_organisations_buildings(self):
        """The branch that did not exist: None meant unrestricted and returned nothing."""
        body = _me_source()
        assert "elif principal.building_ids is None:" in body
        assert "WHERE organization_id = CAST(:o AS uuid)" in body

    def test_allocated_to_nothing_is_still_an_empty_list(self):
        """() is a real answer and must not be turned into the whole estate."""
        body = _me_source()
        tail = body[body.index("elif principal.building_ids is None:"):]
        assert "else:" in tail
        assert 'user["buildings"] = []' in tail

    def test_the_empty_list_is_reached_only_by_the_last_branch(self):
        """One `buildings = []` in the handler, under `else`, or the fix is undone."""
        body = _me_source()
        assert body.count('user["buildings"] = []') == 1


class TestScopeIsNotLost:
    def test_a_superadmin_acting_for_a_company_gets_that_company(self):
        body = _me_source()
        assert "org = principal.organization_id" in body
        assert 'where = "WHERE organization_id = CAST(:o AS uuid)" if org else ""' in body

    def test_an_account_with_no_organisation_is_not_handed_an_empty_list(self):
        """buildings.organization_id is not backfilled everywhere; matching nothing by CAST must
        not reproduce the fault one level down."""
        body = _me_source()
        assert '{"o": str(org)} if org else {}' in body

    def test_all_buildings_still_says_which_case_this_is(self):
        """The client needs to tell "every building" from "these two" — the list alone cannot."""
        src = inspect.getsource(auth_routes)
        assert 'user["all_buildings"] = principal.building_ids is None' in src


class TestTheQueryIsTheSameShapeAsTheNamedOne:
    def test_both_return_id_name_and_code_ordered_by_name(self):
        body = _me_source()
        selects = re.findall(r"SELECT building_id::text AS id, name, building_code", body)
        assert len(selects) == 2
        assert body.count("ORDER BY name") == 2
