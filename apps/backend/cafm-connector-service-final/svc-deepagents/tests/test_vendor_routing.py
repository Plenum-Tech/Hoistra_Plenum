"""A vendor question reaches the vendor engine when the reading model cannot be asked.

An LLM routes first (agent_router.select_agent) and the keyword table is its fallback — used
when routing is disabled, the key is missing or the catalogue is empty. Measured on 16 Sep 2026,
eight of ten questions a property manager actually types matched NOTHING in that table:
"performing" is not "performance", "overcharge" and a bare "score" were absent. On the fallback
path those turns go to the general orchestrator with every tool bound, which is how a vendor
question comes back answered from the certificate register.

The additions are chosen to be substring-safe: `capped` and not `cap`, which would fire on
"capacity"; `overcharge` and not `charge`, which is in every invoice sentence ever written.
"""
from __future__ import annotations

import pytest

from src.agents.phase2_intents import match_phase2_agent

VENDOR_QUESTIONS = [
    "How is Apex Mechanical performing?",
    "Did Gough and Kelly overcharge us?",
    "What does their contract commit them to?",
    "Is that score capped?",
    "What did we overpay last month?",
    "Show me the vendor scorecards",
    "What are the SLA response times for P1?",
    # Measured 16 Sep 2026 against the live stack. "PPM compliance" scored 1.0 for compliance
    # (the word is right there) and 0.0 for vendors, so "compare Gough and Kelly's PPM
    # compliance to Apex's" was answered from the CERTIFICATE register — Gas Safe, EPA 608,
    # public liability — none of which is PPM compliance. `ppm_compliance_pct` is a column on
    # vendor_monthly_scorecards and nowhere else.
    "Compare Gough and Kelly's PPM compliance to Apex's",
    "What is their PPM compliance?",
    # "Does trend_delta agree with the scores?" matched nothing, fell to the generic agent and
    # came back as prose with no card. `trend_delta` is a scorecard column.
    "Does trend_delta agree with the scores?",
    "What is the trend on that vendor?",
]

#: Questions that LOOK like vendor questions and are not. The vendor table must not take them.
NOT_VENDOR = {
    "Which vendor certificates are expired?": "compliance",
    "Which vendor accreditations lapsed?": "compliance",
    "What is the energy spend per meter?": "energy_intelligence",
}


@pytest.mark.parametrize("q", VENDOR_QUESTIONS)
def test_a_question_a_pm_would_type_reaches_the_vendor_engine(q):
    assert match_phase2_agent(q) == "contract_performance", f"unrouted: {q!r}"


#: Vendor questions carrying no vendor vocabulary at all. A keyword table cannot place these
#: and should not pretend to: "which vendors are below 80" is a scorecard question, but the only
#: domain word in it is "vendors", which compliance owns just as legitimately ("which vendors are
#: blocked"). These are the reading model's to route, or the panel's via intent_space. Listed
#: rather than dropped so the limit is visible instead of rediscovered.
UNROUTABLE_BY_KEYWORD = [
    "Which vendors are below 80?",
    "Which vendor should I worry about most?",
]


@pytest.mark.parametrize("q", UNROUTABLE_BY_KEYWORD)
def test_a_question_with_no_domain_word_is_left_to_the_model(q):
    assert match_phase2_agent(q) is None, (
        "forcing this into the vendor table would take compliance's questions with it"
    )


@pytest.mark.parametrize("q,expected", sorted(NOT_VENDOR.items()))
def test_a_question_that_belongs_elsewhere_is_left_alone(q, expected):
    # Widening a keyword table is how one engine starts eating another's questions. A vendor
    # CERTIFICATE is compliance's, wherever the word "vendor" appears.
    assert match_phase2_agent(q) == expected


@pytest.mark.parametrize("word", ["capacity", "capital", "recharge"])
def test_the_new_keywords_do_not_fire_on_words_that_merely_contain_them(word):
    # `cap` would match "capacity"; `charge` would match every invoice sentence. The entries
    # are deliberately the longer forms.
    assert match_phase2_agent(f"What is the {word} of this building?") != "contract_performance"
