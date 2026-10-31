"""A skill's reference documents must actually reach the prompt.

SKILL.md is the contract — what to do, what never to do — and stays short enough to be read.
The detail one kind of question needs (the thirteen anomaly detectors and what each claims,
kW/RT and what good looks like, how excess becomes money) lives in sibling files declared in
front matter as `references:`.

Nothing loaded sibling files before: only SKILL.md reached the prompt, and compliance routes
its own documents through a separate LLM router gated behind env flags that default to off. So
adding .md files without wiring them would have produced three files nobody reads.
"""
import pytest

from src.agents.skills import agent_system_prompt, skill_for_agent


@pytest.fixture(scope="module")
def energy_prompt() -> str:
    return agent_system_prompt("energy_intelligence") or ""


class TestTheReferencesAreDeclaredAndLoaded:

    def test_the_skill_declares_its_references(self):
        sk = skill_for_agent("energy_intelligence")
        assert sk is not None
        assert sk.references == ("anomalies", "assets", "costs")

    @pytest.mark.parametrize("marker,doc", [
        ("weekend_spike", "anomalies"),
        ("kW/RT", "assets"),
        ("Cost above benchmark", "costs"),
    ])
    def test_each_reference_reaches_the_prompt(self, energy_prompt, marker, doc):
        assert marker in energy_prompt, f"{doc}.md did not reach the prompt"

    def test_the_prompt_is_larger_than_the_skill_alone(self, energy_prompt):
        sk = skill_for_agent("energy_intelligence")
        assert len(energy_prompt) > len(sk.body) * 1.5


class TestAddressedBySlugNotAgentId:
    """The directory is the skill's folder name. Compliance is the one place the slug and the
    agent id are the same string ("compliance"), so passing the agent id looked right until a
    skill whose folder is `energy-intelligence` and whose agent is `energy_intelligence` tried
    to load a file — and silently got nothing."""

    def test_the_two_differ_for_energy(self):
        sk = skill_for_agent("energy_intelligence")
        assert sk.slug == "energy-intelligence"
        assert sk.agent == "energy_intelligence"
        assert sk.slug != sk.agent, "this test is pointless if they ever become equal"

    def test_the_references_still_resolve(self, energy_prompt):
        assert "Domestic hot water" in energy_prompt


class TestOtherSkillsAreUnaffected:
    """`references:` is opt-in. A SKILL.md without the key must load exactly as before."""

    def test_compliance_declares_none(self):
        sk = skill_for_agent("compliance")
        assert sk is not None and sk.references == ()

    def test_compliance_does_not_inherit_energy_documents(self):
        cp = agent_system_prompt("compliance") or ""
        assert "kW/RT" not in cp
        assert "weekend_spike" not in cp


class TestTheContentTheQuestionsNeed:
    """The shapes a person actually asks about, per the Energy page."""

    @pytest.mark.parametrize("fact", [
        "contained in `nonocc_spike`",   # why the detectors must not be summed
        "annualised",                    # not money already spent
        "Whole building",                # building-level metering infers, not attributes
        "calorific value",               # gas m3 vs kWh
        "unpriceable",                   # LPG has no per-kWh rate
        "at or under reference",         # a negative excess is a real answer
    ])
    def test_the_trap_is_written_down(self, energy_prompt, fact):
        assert fact in energy_prompt, fact

    def test_the_two_money_figures_are_kept_apart(self, energy_prompt):
        assert "is not £523k" in energy_prompt or "never summed" in energy_prompt
