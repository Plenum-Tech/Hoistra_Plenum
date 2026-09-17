"""A vendor answer is written from the rows, and checked against them before it ships.

Compliance hands its rows to an analyst that returns zones (narrative, kpis, groups, actions,
insights), has code strip anything the rows do not support, and emits the result as
`compliance_response` — which is the only reason the interface draws cards rather than prose.
The vendor engine had none of that: a small model wrote a paragraph and the paragraph was the
answer, checked by nobody.

The grounding rule here is numeric rather than by id. A vendor answer's claims ARE its numbers
— a score, a month's delta, how many vendors sit below a threshold — so a figure that appears
in no fetched row is the failure mode, and "£6,353.70" invented one place to the left is
indistinguishable from a real total to the reader.
"""
from __future__ import annotations

import pytest

from src.agents import contract_answer as ca

ROWS = [
    {"vendor_name": "Gough and Kelly Ltd", "overall_score": 72.4, "score_month": "2026-08-01",
     "trend_delta": -3.2, "block_capped": False},
    {"vendor_name": "Apex Mechanical Services Ltd", "overall_score": 60, "score_month": "2026-08-01",
     "trend_delta": 0, "block_capped": True},
]


# ── the evidence the analyst is given ────────────────────────────────────────────────

def test_rows_are_gathered_from_every_read_the_agent_made():
    calls = [
        {"tool": "list_vendor_scorecards", "output": {"scorecards": [ROWS[0]]}},
        {"tool": "list_contract_parameters", "output": {"parameters": [{"contract_ref": "C-1"}]}},
        {"tool": "list_invoices", "output": {"invoices": [{"invoice_ref": "INV-1", "amount": 6353.70}]}},
    ]
    rows = ca.evidence_rows(calls)
    assert len(rows) == 3
    assert {"scorecard", "contract", "invoice"} == {r["_kind"] for r in rows}


def test_a_tool_that_errored_contributes_no_rows():
    calls = [{"tool": "list_vendor_scorecards", "output": {"error": "401", "status_code": 401}}]
    assert ca.evidence_rows(calls) == []


# ── grounding: every number must come from a row ─────────────────────────────────────

def test_a_figure_that_appears_in_no_row_is_reported():
    payload = {"narrative": "Apex is at 88.5 this month.", "kpis": [], "groups": []}
    assert "88.5" in " ".join(ca.ungrounded_numbers(payload, ROWS))


def test_a_figure_that_is_in_a_row_passes():
    payload = {"narrative": "Gough and Kelly scored 72.4, down 3.2.", "kpis": [], "groups": []}
    assert ca.ungrounded_numbers(payload, ROWS) == []


def test_rounding_is_not_a_hallucination():
    # The analyst writes 72.4 from a row holding 72.43. Comparing as strings made every
    # rounded figure a false alarm, and each one cost a revision pass.
    rows = [{"overall_score": 72.43}]
    payload = {"narrative": "It came to 72.4.", "kpis": [], "groups": []}
    assert ca.ungrounded_numbers(payload, rows) == []


@pytest.mark.parametrize("text", [
    "Two vendors are below 80.",          # a small count the answer itself derived
    "Blocked caps the score at 60.",      # a scoring constant
    "Flags over £500 go to the Adversary.",
    "PPM tolerance is ±7 days.",
])
def test_counts_and_scoring_constants_are_not_treated_as_invented(text):
    assert ca.ungrounded_numbers({"narrative": text, "kpis": [], "groups": []}, ROWS) == []


def test_grounding_strips_an_unsupported_kpi_and_says_it_did():
    payload = {
        "narrative": "Two vendors scored this month.",
        "kpis": [{"label": "Worst score", "value": "72.4"},
                 {"label": "Money at risk", "value": "£14,900"}],
        "groups": [],
    }
    out, issues = ca.ground(payload, ROWS)
    assert [k["label"] for k in out["kpis"]] == ["Worst score"]
    assert any("Money at risk" in i for i in issues)


def test_a_clean_answer_is_returned_unchanged():
    payload = {"narrative": "Gough and Kelly scored 72.4.", "kpis": [], "groups": []}
    out, issues = ca.ground(payload, ROWS)
    assert issues == [] and out["narrative"] == payload["narrative"]


# ── emission: the shape the interface draws cards from ───────────────────────────────

def test_the_answer_is_emitted_under_the_names_the_interface_matches_on():
    payload = {"narrative": "n", "sections": [], "groups": [], "kpis": [],
               "actions": [], "insights": [], "pending": []}
    calls = ca.as_tool_calls(payload, question="how is apex doing",
                             steps=[{"stage": "plan", "label": "Planned the question"}],
                             cost={"calls": 2, "usd": 0.05})
    names = [c["tool"] for c in calls]
    # extractComplianceAnswer in the frontend finds these two by name; anything else renders
    # as plain prose no matter how well structured it is.
    assert names == ["compliance_response", "compliance_pipeline"]
    assert calls[0]["output"]["narrative"] == "n"
    assert calls[1]["output"]["steps"] and calls[1]["output"]["cost"]["usd"] == 0.05


def test_an_analyst_that_returned_nothing_emits_nothing():
    # No zones means the prose answer stands; half a card set is worse than none.
    assert ca.as_tool_calls(None, question="q", steps=[], cost=None) == []


# ── the analyst call itself ──────────────────────────────────────────────────────────

class _FakeAnthropic:
    """Mimics anthropic.AsyncAnthropic.messages.create returning content blocks."""

    def __init__(self, text): self._text, self.messages = text, self
    async def create(self, **kwargs):
        self.seen = kwargs
        return type("R", (), {"content": [type("B", (), {"text": self._text})()]})()


async def test_the_analyst_writes_zones_from_the_rows():
    fake = _FakeAnthropic('{"narrative": "Gough and Kelly scored 72.4.", "kpis": []}')
    zones, meta = await ca.write_answer(
        question="how is gough and kelly doing", rows=ROWS, prose="prose",
        api_key="k", client=fake,
    )
    assert zones["narrative"].startswith("Gough and Kelly")
    assert "ms" in meta
    # The rows must actually reach the model — an analyst given no evidence invents freely.
    assert "72.4" in str(fake.seen["messages"])


async def test_no_anthropic_key_means_the_prose_answer_stands():
    zones, meta = await ca.write_answer(
        question="q", rows=ROWS, prose="prose", api_key="",
    )
    assert zones is None and meta["skipped"] == "no anthropic key"


async def test_an_unparsable_reply_does_not_take_the_turn_down():
    zones, meta = await ca.write_answer(
        question="q", rows=ROWS, prose="prose", api_key="k",
        client=_FakeAnthropic("sorry, I cannot do that"),
    )
    assert zones is None and "error" in meta


async def test_no_rows_means_no_analyst_pass():
    zones, meta = await ca.write_answer(question="q", rows=[], prose="p", api_key="k")
    assert zones is None and meta["skipped"] == "no rows fetched"


# ── the analyst does not always return bare JSON ─────────────────────────────────────

@pytest.mark.parametrize("wrapper", [
    'Here is the analysis:\n{"narrative": "Gough and Kelly scored 72.4."}',
    '```json\n{"narrative": "Gough and Kelly scored 72.4."}\n```',
    '```\n{"narrative": "Gough and Kelly scored 72.4."}\n```',
    '{"narrative": "Gough and Kelly scored 72.4."}\n\nLet me know if you need more.',
], ids=["preamble", "json fence", "bare fence", "trailing note"])
async def test_json_wrapped_in_prose_is_still_read(wrapper):
    """Measured on 16 Sep 2026: one analyst pass in three failed with
    "Expecting value: line 1 column 1" and fell back to prose with no card on screen.

    Sonnet is asked for strict JSON with no schema enforcement, so it sometimes explains
    itself first. The object is there; refusing to find it throws away a good answer.
    """
    zones, meta = await ca.write_answer(
        question="q", rows=ROWS, prose="p", api_key="k", client=_FakeAnthropic(wrapper),
    )
    assert zones is not None, f"unparsed: {meta.get('error')}"
    assert zones["narrative"].startswith("Gough and Kelly")


async def test_a_reply_with_no_json_at_all_still_fails_cleanly():
    zones, meta = await ca.write_answer(
        question="q", rows=ROWS, prose="p", api_key="k",
        client=_FakeAnthropic("I am unable to help with that."),
    )
    assert zones is None and "error" in meta


# ── the schema the interface actually reads ──────────────────────────────────────────

@pytest.mark.parametrize("zone,fields", [
    ("kpis", ["label", "count", "unit", "sublabel"]),
    ("groups", ["owner", "headline", "points"]),
    ("actions", ["title", "scope", "severity"]),
    ("insights", ["type", "text"]),
    ("pending", ["name", "what_is_pending"]),
])
def test_the_analyst_is_told_the_field_names_the_renderer_reads(zone, fields):
    """ComplianceAnswer.jsx reads these keys and nothing else.

    The first version of this prompt was written from compliance's own prompt rather than from
    the renderer: it asked for groups {name, headline, bullets} where the card reads
    {owner, headline, points}, and kpis {value} where the card reads {count}. The analyst
    returned good data under the wrong keys, and every card drew EMPTY with no error in any
    log. Nothing else in the stack would catch that, which is why it is pinned here.
    """
    prompt = ca._ANALYST_SYSTEM
    assert zone in prompt
    for f in fields:
        assert f'"{f}"' in prompt, f"{zone} must name {f!r} — the renderer reads it"


# ── the rules the tools wrote onto the rows ──────────────────────────────────────────

@pytest.mark.parametrize("field", [
    "answer_hint",          # no vendor matched the name that was typed
    "STATUS_FILTER_NOTE",   # the status asked for is not one this system records
    "PRESENTATION_RULE",    # these contract figures are system defaults
    "MULTI_VENDOR_NOTE",    # nobody was named and the contracts belong to different companies
    "AMOUNT_RULE",          # the invoice total is not the amount in dispute
    "FRESHNESS_RULE",       # this newest scorecard is years old
])
def test_every_rule_a_tool_can_put_on_a_row_is_binding_on_the_analyst(field):
    """Measured across five behaviours on 16 Sep 2026: skill documents changed two of them,
    a rule written into the tool's own return value changed three of three. The rule only
    works if the analyst is told the field is an instruction rather than a column to render.

    The prompt used to list three such fields by name, so each new one a tool learned to write
    was ignored until someone remembered to edit this sentence. Naming them all here is what
    makes forgetting visible.
    """
    assert field in ca._ANALYST_SYSTEM, f"the analyst is never told to obey {field}"


# ── how much of the answer is cards ──────────────────────────────────────────────────

def test_the_analyst_is_told_figures_become_kpis_rather_than_sentences():
    """Measured 16 Sep 2026 across eight live answers: five came back as a single paragraph
    with no card at all, including ones carrying four distinct money figures.

    The cause was this prompt. It said a one-fact question gets "narrative and one section and
    NOTHING else" and that only a portfolio question "earns kpis, groups, actions and insights
    together" — so the analyst read anything not obviously portfolio-wide as prose-only, and
    £6,353.70 / £3,031.59 / £1,644.46 / £1,387.13 were four numbers buried in a sentence
    instead of four cards a reader can take in at a glance.

    The test for padding was never "is this a portfolio question". It is "does this zone carry
    something the rows support" — a figure earns a KPI, a named company earns a group.
    """
    prompt = ca._ANALYST_SYSTEM
    assert "NOTHING else" not in prompt, "the minimalism rule suppressed cards on real answers"
    lowered = prompt.lower()
    assert "every figure" in lowered or "each figure" in lowered
    assert "kpi" in lowered


# ── the same composer on every path that reaches the vendor tools ────────────────────

SCORECARD_CALLS = [{"tool": "list_vendor_scorecards", "output": {"scorecards": [ROWS[0]]}}]


async def test_a_turn_that_read_the_vendor_tools_gets_cards_whichever_path_it_took():
    """Measured 16 Sep 2026. Two routes reach the contract tools and only one carded its answer.

    "Does trend_delta agree with the scores?" matched no intent keyword, so the orchestrator took
    the general path, the meta agent handed the question to contract_performance through `task`,
    and the reply came back as a markdown list — no compliance_response, no card, the run trace
    showing only `list_vendor_scorecards · task`. The analyst pass lived inside
    _invoke_phase2_engine, so anything arriving by another door skipped it.

    Which door a question came through is not a property of the answer. Composition belongs
    with the rows.
    """
    answer, calls, meta = await ca.compose(
        question="does trend_delta agree with the scores",
        answer="Gough and Kelly scored 72.4 in August.",
        tool_calls=SCORECARD_CALLS,
        api_key="k",
        client=_FakeAnthropic('{"narrative": "Gough and Kelly scored 72.4.", "kpis": []}'),
    )
    assert [c["tool"] for c in calls[1:]] == ["compliance_response", "compliance_pipeline"]
    assert answer.startswith("Gough and Kelly")


async def test_an_existing_run_panel_is_extended_not_duplicated():
    """The phase-2 engine now emits a run panel for every engine before this composer runs.

    The interface draws the first `compliance_pipeline` it finds. Two of them on a vendor turn
    meant either the engine's routing-and-tools half or the analyst's gather-analyse-ground half
    was invisible, depending on order. They are one run; they belong in one panel.
    """
    panel = {"tool": "compliance_pipeline", "input": {"question": "q"},
             "output": {"engine": "contract_performance",
                        "steps": [{"stage": "route", "label": "Routed"}], "cost": None}}
    _, calls, _ = await ca.compose(
        question="how is gough doing",
        answer="72.4.",
        tool_calls=[*SCORECARD_CALLS, panel],
        api_key="k",
        cost={"total_usd": 0.01},
        client=_FakeAnthropic('{"narrative": "Gough and Kelly scored 72.4.", "kpis": []}'),
    )
    panels = [c for c in calls if c["tool"] == "compliance_pipeline"]
    assert len(panels) == 1
    stages = [s["stage"] for s in panels[0]["output"]["steps"]]
    assert stages == ["route", "gather", "analyse", "ground"]
    assert panels[0]["output"]["cost"] == {"total_usd": 0.01}
    assert panels[0]["output"]["engine"] == "contract_performance"


async def test_a_turn_that_read_no_vendor_rows_is_left_exactly_as_it_was():
    calls_in = [{"tool": "list_buildings", "output": {"buildings": [{"name": "Mob"}]}}]
    answer, calls, meta = await ca.compose(
        question="how many buildings", answer="Six.", tool_calls=calls_in, api_key="k",
    )
    assert calls == calls_in and answer == "Six."


async def test_compliances_own_cards_are_never_rewritten_by_the_vendor_analyst():
    """A compliance turn builds its own compliance_response through its own pipeline. Running
    this composer over it would add a second one and the renderer reads the first it finds."""
    calls_in = [
        {"tool": "list_vendor_accreditations", "output": {"vendors": [{"vendor_name": "Apex"}]}},
        {"tool": "compliance_response", "output": {"narrative": "compliance wrote this"}},
    ]
    answer, calls, meta = await ca.compose(
        question="which accreditations lapsed", answer="One.", tool_calls=calls_in, api_key="k",
        client=_FakeAnthropic('{"narrative": "should never be used"}'),
    )
    assert calls == calls_in and answer == "One."


# ── arithmetic the rows support is not invention ─────────────────────────────────────

def test_a_difference_between_two_row_figures_is_not_ungrounded():
    """Live turn, 16 Sep 2026 — five entries stripped from a correct answer, among them:

        dropped kpis entry 'Unresolved gap': 1387.13 in no row

    £1,387.13 is £3,031.59 claimed minus £1,644.46 agreed. Both are on the row; the gap between
    them is the single most useful figure in the answer, and it is what the property manager
    has to decide about. The card was removed and the same number survived in the narrative,
    so the check did not even make the answer consistent — it just cost it a card.

    Grounding exists to catch a figure with no basis in the data. A subtraction of two figures
    that ARE in the data has a basis; refusing it forbids the analyst from doing arithmetic,
    which is most of what reasoning about money is.
    """
    rows = [{"amount": 6353.70, "flagged_delta": 3031.59, "agreed_delta": 1644.46}]
    payload = {"narrative": "£1,387.13 is unresolved.",
               "kpis": [{"label": "Unresolved gap", "count": "£1,387.13"}], "groups": []}
    assert ca.ungrounded_numbers(payload, rows) == []


def test_a_sum_of_two_row_figures_is_not_ungrounded():
    rows = [{"claimed": 1015.13, "wo_missing": 372.00}]
    payload = {"narrative": "That comes to 1387.13 across both.", "kpis": [], "groups": []}
    assert ca.ungrounded_numbers(payload, rows) == []


def test_a_figure_that_is_no_combination_of_the_rows_is_still_caught():
    """The relaxation must not become a licence. 88.5 is not any pair's sum or difference."""
    rows = [{"overall_score": 72.4, "trend_delta": -3.2}]
    payload = {"narrative": "Apex is at 88.5 this month.", "kpis": [], "groups": []}
    assert "88.5" in " ".join(ca.ungrounded_numbers(payload, rows))


def test_the_analyst_is_told_to_scan_every_row_before_naming_a_best_or_worst():
    """Asked "what was Gough and Kelly's best month?", the answer was "December 2023, 85.09" —
    and rendered a table directly underneath in which July 2023 shows 85.11. It named a
    superlative while displaying the evidence against it, losing by 0.02 to a row two lines
    further down.

    A superlative is a claim about EVERY row, not about the rows that caught the eye. This is
    the one kind of answer where reading all of them is the whole job.
    """
    lowered = ca._ANALYST_SYSTEM.lower()
    assert "best" in lowered and "worst" in lowered
    assert "every row" in lowered
