"""The model is never handed a database tool the database service will refuse its caller.

svc-udr keeps its table and catalogue reads for an administrator or a consultant
(require_reader), and its caller-supplied SQL, natural-language agent and writes for an
administrator (require_admin). The chat handed every caller all of them anyway, so a plain user
or a consultant got a 403 back from a tool the model chose, and the answer said "you do not have
access … ask an administrator" over data the caller does hold (24 Nov 2026: "Aire Mill Court
vendors" from a consultant). Withholding them sends the model to the tools that answer for that
caller: the typed lookups and answer_from_records, which read through the scoped views.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.agents import page_access as PA
from src.services.principal import caller_principal

ADMIN_ONLY = ["udr_execute_select", "udr_agent_query", "udr_create_record",
              "udr_update_record", "udr_delete_record"]
READERS = ["udr_list_tables", "udr_describe_table", "find_tables", "table_card",
           "udr_read_records", "udr_get_record", "udr_search_records"]
ALWAYS = ["answer_from_records", "find_asset", "find_location", "query_table", "get_schema"]


def atool(name, module="udr_agent"):
    async def fn():
        return None
    fn.__module__ = "src.agents." + module
    return SimpleNamespace(name=name, func=None, coroutine=fn)


def caller(role="user", access_role=None, pages=None):
    return SimpleNamespace(role=role, access_role=access_role, pages=pages,
                           is_admin=role in ("admin", "superadmin"))


@pytest.fixture(autouse=True)
def _reset():
    pages = PA.active_pages.set(PA.FROM_CALLER)
    token = caller_principal.set(None)
    yield
    caller_principal.reset(token)
    PA.active_pages.reset(pages)


def names(kept):
    return [t.name for t in kept]


TOOLS = [atool(n) for n in ADMIN_ONLY + READERS + ALWAYS]


def test_no_caller_is_handed_everything_as_before():
    assert PA.filter_tools(TOOLS) is TOOLS


@pytest.mark.parametrize("role", ["admin", "superadmin"])
def test_an_administrator_is_handed_everything_as_before(role):
    caller_principal.set(caller(role=role))
    assert PA.filter_tools(TOOLS) is TOOLS


def test_a_consultant_is_handed_the_reads_but_not_sql_the_agent_or_writes():
    caller_principal.set(caller(access_role="consultant"))
    kept = names(PA.filter_tools(TOOLS))
    assert kept == READERS + ALWAYS
    for n in ADMIN_ONLY:
        assert not PA.tool_allowed(atool(n))


def test_a_plain_user_is_handed_neither_the_reads_nor_the_admin_tools():
    caller_principal.set(caller())
    assert names(PA.filter_tools(TOOLS)) == ALWAYS


def test_a_withheld_tool_reached_some_other_way_refuses_without_calling_the_service():
    caller_principal.set(caller(access_role="consultant"))
    t = PA.guard_tools([atool("udr_execute_select")])[0]
    import asyncio
    out = asyncio.run(t.coroutine())
    assert out["ok"] is False


def test_pages_still_apply_on_top():
    # A caller held to pages keeps losing the tools their pages do not own (test_page_access).
    caller_principal.set(caller(access_role="compliance_officer", pages=frozenset({"compliance"})))
    assert "udr_read_records" not in names(PA.filter_tools(TOOLS))


# ── the sub-agents (24 Nov 2026) ──────────────────────────────────────────────
# The udr sub-agent was built once over every tool and handed to a consultant as it was: the
# guard then refused udr_execute_select with the pages sentence, and the answer said the asset
# register was "outside the pages you have access to" while the consultant holds every page.

def test_a_withheld_tool_tells_the_model_to_use_another_not_the_user_that_a_page_is_missing():
    caller_principal.set(caller(access_role="consultant"))
    t = PA.guard_tools([atool("udr_execute_select")])[0]
    import asyncio
    out = asyncio.run(t.coroutine())
    text = str(out).lower()
    assert "outside the pages" not in text and "page_not_allowed" not in text
    assert out["category"] == "routed_elsewhere" and out["user_actionable"] is False
    assert "answer_from_records" in out["guidance"]
    from src.agents import evidence
    assert evidence.classify("udr_execute_select", out)["category"] == "routed_elsewhere"


def test_a_page_refusal_is_unchanged_for_a_caller_held_to_pages():
    caller_principal.set(caller(access_role="compliance_officer", pages=frozenset({"compliance"})))
    PA.active_pages.set(frozenset({"compliance"}))
    t = PA.guard_tools([atool("list_energy_anomalies", "energy_intelligence_agent")])[0]
    import asyncio
    assert asyncio.run(t.coroutine())["reason"] == "page_not_allowed"


def test_a_consultant_gets_the_sub_agent_without_the_tools_the_service_refuses():
    from src.agents import meta_tools
    runner = meta_tools._TaskRunner.__new__(meta_tools._TaskRunner)
    runner._page_runners = {}
    built = []

    def _sub(name, *, tools, prompt):
        built.append((name, [t.name for t in tools]))
        return SimpleNamespace(name=name, tools=built[-1][1])
    runner._sub = _sub
    runner._specs = {"udr": ([atool("answer_from_records", "ontology_qa"), atool("udr_read_records"),
                              atool("udr_execute_select")], "p")}
    assert PA.narrows_tools() is False                    # no caller: the shared agent, as before
    caller_principal.set(caller(access_role="consultant"))
    assert PA.narrows_tools() is True
    sub = runner._runner_for_pages("udr")
    assert sub.tools == ["answer_from_records", "udr_read_records"]
    caller_principal.set(caller(role="admin"))
    assert PA.narrows_tools() is False
    caller_principal.set(caller())                        # a plain user: built apart, without the reads
    assert runner._runner_for_pages("udr").tools == ["answer_from_records"]
    assert len(built) == 2
