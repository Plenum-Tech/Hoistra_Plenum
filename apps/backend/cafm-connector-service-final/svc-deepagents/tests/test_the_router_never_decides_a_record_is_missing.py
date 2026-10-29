"""The router routes a question about a building; it never decides the building does not exist.

29 Oct 2026: "how many floors in hussain tower", asked on the home page right after the building
was added, came back "That could be about more than one part of the business (No building named
'Hussain Tower' exists in the register…)". The router sees no data. Its CONTEXT was the home
page's summary — "Live register: 6 buildings … Bishopsgate Tower, LPS Office, Manchester Town
Hall and 3 more have none on record" — and it read three names on a page as the whole register.
Replayed against the model with that exact context: 4 of 6 calls returned clarify. The six-area
question that followed sent "all of them" to compliance, which holds certificates for three
buildings and said, again, that no such building exists.

Two faults, both in what the router is told. Its prompt never said the context is a page and not
the register, nor that whether a record exists is the agent's to find out. And no catalogue entry
claimed the building register: udr's said "assets, locations, sites, vendors" and never
"building", "floors" or "floor area", though its own buildings reference holds exactly those.
"""
from src.agents.agent_router import _PROMPT
from src.agents.skills import skill_for_agent


def test_the_router_is_told_it_cannot_see_the_data():
    p = _PROMPT.lower()
    assert "cannot see the data" in p
    assert "never decide that a building" in p
    assert "not the register" in p


def test_udr_claims_the_building_register():
    d = skill_for_agent("udr").description.lower()
    for word in ("building register", "floors", "floor area", "building id"):
        assert word in d, f"udr description never mentions {word!r}"
