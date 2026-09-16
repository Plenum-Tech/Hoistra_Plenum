"""The Maintenance page, and the data behind it as it really is.

svc-work-order-management serves thirteen routes under /api/maintenance — decisions,
inspection-intelligence, ppm/contracts, overview — and the page renders all of them. deep-agents
had a tool for none, so the questions printed as chips on that page could not be answered from
the chat beside them.

The reference docs exist because the work_orders table is not what it looks like: fifteen status
spellings that collide on case, ninety columns of which most are empty, and two currencies in
the cost columns.
"""
import pytest

from src.agents.orchestrator import ALL_TOOLS
from src.agents.skills import agent_system_prompt, skill_for_agent

TOOLS = {t.name for t in ALL_TOOLS}


@pytest.fixture(scope="module")
def prompt() -> str:
    return agent_system_prompt("wo_engine") or ""


class TestTheMaintenanceToolsExist:

    @pytest.mark.parametrize("name", [
        "get_maintenance_overview", "list_maintenance_decisions",
        "get_inspection_intelligence", "get_ppm_contracts",
    ])
    def test_it_is_registered(self, name):
        assert name in TOOLS

    def test_the_skill_declares_its_references(self):
        sk = skill_for_agent("wo_engine")
        assert sk is not None
        assert sk.references == ("decisions", "work-orders", "dispatch")


class TestTheFourStatesAreKeptApart:
    """Blocked, To raise, Awaiting approval and Deviation are four different problems. A
    blocked order needs a vendor chased; an approval needs thirty seconds from one person."""

    def test_all_four_are_named(self, prompt):
        for state in ("Blocked", "To raise", "Awaiting approval", "Deviation"):
            assert state in prompt, state

    def test_a_to_raise_row_is_not_an_order(self, prompt):
        """`work_order` is NULL on those — they are recommendations with a price, and quoting
        a reference for one invents it."""
        assert "has no work order yet" in prompt or "work_order` is NULL" in prompt

    def test_they_are_not_one_backlog(self, prompt):
        assert "not one backlog" in prompt


class TestTheStatusVocabulary:
    """Open=72 and open=55 are separate values, so `status = 'Open'` answers 72 where the
    rows a person means come to about 152."""

    def test_the_case_collision_is_written_down(self, prompt):
        assert "`Open` and `open` are different values" in prompt

    def test_the_synonym_sets_are_given(self, prompt):
        for v in ("active", "Raised", "InProgress", "pending_approval"):
            assert v in prompt, v


class TestTheEmptyColumns:
    """Most of work_orders is unpopulated, and a confident "none" drawn from a null column is
    the easiest wrong answer in this domain."""

    def test_raised_at_is_flagged_as_empty(self, prompt):
        assert "raised_at" in prompt and "0%" in prompt

    def test_asset_code_coverage_is_given(self, prompt):
        assert "28%" in prompt

    def test_a_null_type_is_not_none_of_that_kind(self, prompt):
        assert "unrecorded" in prompt


class TestPpmAndInspections:

    def test_a_visit_without_a_report_is_unverified(self, prompt):
        assert "done but UNVERIFIED" in prompt

    def test_deferrals_are_not_misses(self, prompt):
        assert "Deferrals are not misses" in prompt

    def test_unanswerable_must_be_reported(self, prompt):
        low = prompt.lower()
        assert "unanswerable" in low
        assert "could not look" in low

    def test_it_forbids_claiming_checks_that_did_not_run(self, prompt):
        """resource_skills and work_order_tasks are both empty, so no availability check and
        no task list happened however plausible the narration reads."""
        assert "resource_skills" in prompt and "empty" in prompt


class TestCountIsNotTheTotal:
    """At the default limit=200 this estate returns count=200 and total=374. Reading `count`
    is wrong by 174 and wrong in the plausible direction — a round number that is the limit.
    The engine gets this right and says so in its own comments; the risk is the reader, and I
    made exactly this mistake while verifying it."""

    def test_the_four_numbers_are_distinguished(self, prompt):
        for field in ("`count`", "`matched`", "`total`", "`total_is_capped`"):
            assert field in prompt, field

    def test_it_says_count_is_the_page(self, prompt):
        assert "the page size, not a finding" in prompt

    def test_it_gives_the_measured_example(self, prompt):
        assert "200 and `total` 374" in prompt or "count` 200" in prompt

    def test_counting_questions_use_the_rollups(self, prompt):
        """by_state and by_source are computed over every decision, so they stay right when
        the list is truncated."""
        assert "not over the page" in prompt


class TestEmptySourcesAreNotCleanliness:

    def test_the_unpopulated_sources_are_named(self, prompt):
        assert "Assets 0, Energy 0" in prompt

    def test_an_empty_filter_is_about_wiring(self, prompt):
        assert "statement about wiring" in prompt
