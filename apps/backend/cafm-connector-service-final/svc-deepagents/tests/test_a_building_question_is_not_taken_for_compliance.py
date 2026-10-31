"""A question about a building is not a compliance question because it says "building" (29 Oct 2026).

"HOW MANY FLOORS IN JASMEET BUILDING" was answered from the certificate register: "the certificate
register contains no building named 'Jasmeet Building' and holds no floor-count data". The
streaming path runs a compliance shortcut before the router, and _is_compliance_data_question
fired on any scope word ("building", "vendor", "site") next to any generic verb ("how many",
"which", "show", "list", "summar…") — so every "how many … building" question, and every "list
the vendors", was taken by the compliance analyst and the router never saw it. A compliance
STATUS beside a scope ("blocked vendors", "buildings at risk") and any compliance word still are.
"""
import pytest

from src.agents.orchestrator import DeepAgentOrchestrator as O


@pytest.mark.parametrize("msg", [
    "how many floors in jasmeet building",
    "how many floors does the hussain tower building have",
    "list the buildings",
    "show me the vendors",
    "which site is in london",
])
def test_a_plain_question_about_a_building_or_vendor_is_left_to_the_router(msg):
    assert O._is_compliance_data_question(msg) is False


@pytest.mark.parametrize("msg", [
    "which buildings have expired certificates",
    "how many vendors are blocked",
    "show compliance for bishopsgate tower",
    "which vendors are at risk",
    "list the buildings with lapsed accreditations",
    "what is expiring at the site",
])
def test_compliance_questions_are_still_taken(msg):
    assert O._is_compliance_data_question(msg) is True
