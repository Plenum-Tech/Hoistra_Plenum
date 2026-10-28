"""Faithful layouts of real-shaped answers that the presenter must send (agents/answer_presenter.py).

The check that keeps a layout from changing what an answer means (tests/test_answer_presenter_meaning.py)
is only worth having while good layouts still pass it: a layout it refuses costs the user the charts and
tiles. Each answer here is shaped like one the engines write (the vendor SLA answer, the weekly summary,
a compliance list, changes written with direction words, possessive names), and each layout is one the
model writes or could write for it, saying the same thing with the chat's components.
"""
from __future__ import annotations

import pytest

from src.agents import answer_presenter as ap

P = "\n".join

# contract_performance, 27 Oct 2026 (live).
VENDORS = """**Apex Mechanical is slipping most on SLA** in the latest available scorecard month, **September 2026**. Its SLA response component fell **0.73 points** to **23.44/25**, and SLA completion fell **0.62 points** to **21.88/25** versus August; its overall score also declined **2.04 points** to **86.12**.

| Vendor | Sep 2026 SLA response | Change vs Aug | Sep 2026 SLA completion | Change vs Aug | Overall trend |
| --- | --- | --- | --- | --- | --- |
| **Apex Mechanical** | 23.44/25 | **-0.73** | 21.88/25 | **-0.62** | **-2.04** |
| Apex Lifts | 24.11/25 | -0.06 | 23.21/25 | -0.12 | -0.40 |
| Corvane Mechanical | 22.45/25 | +0.45 | 20.92/25 | +0.42 | No prior trend |

All figures are from confirmed, contract-sourced scorecards and none is compliance-block capped. There is **no October 2026 scorecard yet**, so September is the newest available month.

**What to do:** Review Apex Mechanical's September SLA misses first—particularly completion performance—and request the underlying work-order scoring if you need the jobs and causes behind the decline."""

VENDORS_TABLE = (
    'performance = Table(["Vendor", "Sep 2026 SLA response", "Change vs Aug", "Sep 2026 SLA completion", '
    '"Change vs Aug", "Overall trend"], [["Apex Mechanical", "23.44/25", "-0.73", "21.88/25", "-0.62", "-2.04"], '
    '["Apex Lifts", "24.11/25", "-0.06", "23.21/25", "-0.12", "-0.40"], ["Corvane Mechanical", "22.45/25", "+0.45", '
    '"20.92/25", "+0.42", "No prior trend"]])')

# The model's own layout of VENDORS, 27 Oct 2026 (live_out3), as it came back.
VENDORS_LIVE = P([
    'root = Answer([head, metrics, performance, dataGap, sourceNote, actions, followUps])',
    ('head = Headline("**Apex Mechanical is slipping most on SLA** in the latest available scorecard month, '
     '**September 2026**.")'),
    'metrics = Metrics([responseMetric, completionMetric, overallMetric])',
    'responseMetric = Metric("SLA response", "23.44/25", "-0.73 points versus August", "risk")',
    'completionMetric = Metric("SLA completion", "21.88/25", "-0.62 points versus August", "risk")',
    'overallMetric = Metric("Overall score", "86.12", "-2.04 points", "risk")',
    VENDORS_TABLE,
    ('dataGap = Callout("warn", "There is **no October 2026 scorecard yet**, so September is the newest available '
     'month.", "Latest available scorecard")'),
    ('sourceNote = Section("Source and limitation", [sourceText], "Confirmed, contract-sourced scorecards; none is '
     'compliance-block capped.", true)'),
    'sourceText = Text("All figures are from confirmed, contract-sourced scorecards and none is compliance-block capped.")',
    'actions = Section("What to do", [steps])',
    'steps = Steps([step1, step2])',
    'step1 = Step("Review Apex Mechanical\'s September SLA misses first", "Particularly completion performance.")',
    ('step2 = Step("Request the underlying work-order scoring", "Use this if you need the jobs and causes behind the '
     'decline.", requestButton)'),
    ('requestButton = Button("Request the underlying work-order scoring", @ToAssistant("Request the underlying '
     'work-order scoring for Apex Mechanical\'s September SLA misses"), "primary")'),
    ('followUps = FollowUps(["Which Apex Mechanical jobs caused the SLA decline?", "How does Apex Lifts compare on SLA?", '
     '"When will the October 2026 scorecard be available?"])'),
])

# The same answer with metrics, a bar chart beside the table, and the changes in words.
VENDORS_CHART = P([
    'root = Answer([head, metrics, bars, performance, dataGap, source, actions, followUps])',
    ('head = Headline("**Apex Mechanical is slipping most on SLA** in the latest available scorecard month, '
     '**September 2026**.")'),
    'metrics = Metrics([responseMetric, completionMetric, overallMetric])',
    'responseMetric = Metric("SLA response", "23.44/25", "fell 0.73 points versus August", "risk")',
    'completionMetric = Metric("SLA completion", "21.88/25", "down 0.62 points versus August", "risk")',
    'overallMetric = Metric("Overall score", "86.12", "declined 2.04 points", "risk")',
    ('bars = BarChart("Sep 2026 SLA completion", ["Apex Mechanical", "Apex Lifts", "Corvane Mechanical"], '
     '["21.88/25", "23.21/25", "20.92/25"])'),
    VENDORS_TABLE,
    'dataGap = Callout("warn", "There is **no October 2026 scorecard yet**, so September is the newest available month.")',
    'source = Text("All figures are from confirmed, contract-sourced scorecards and none is compliance-block capped.")',
    'actions = Section("What to do", [steps])',
    'steps = Steps([step1, step2])',
    'step1 = Step("Review Apex Mechanical\'s September SLA misses first", "Particularly completion performance.")',
    'step2 = Step("Request the underlying work-order scoring", "If you need the jobs and causes behind the decline.")',
    'followUps = FollowUps(["Which Apex Mechanical jobs caused the SLA decline?", "How does Apex Lifts compare on SLA?"])',
])

# The weekly summary, 27 Oct 2026 (live), with a layout that keeps the detection dates the model dropped.
SUMMARY = """# Weekly Summary — Portfolio (Work Orders, Compliance, Energy)

**Headline:** No life-safety job is being actively worked, but one is overdue on both counts — Bishopsgate Tower's Fire Risk Assessment lapsed 120 days ago.

## 1. Risk to life — top priority

**Fire Risk Assessment (FRA-B-301-2025), Bishopsgate Tower — lapsed 120 days ago (expired 2026-06-29)**
Pennard Fire Services found compartmentation breaches at the L12 riser.

## 3. Energy — Bishopsgate Tower

10 open anomalies totalling **£214,927/year** in wasted energy cost. Largest contributors:

- Asset spike +157%, **£40,151/yr**, detected 2026-09-14
- Asset spike +137%, **£112,409/yr**, detected 2026-09-21

## Work orders

No work order data was returned in this week's feed.

## What to do

- **Reassess the Bishopsgate FRA now** (FRA-B-301-2025) and close the L12 riser fire-stopping remedial."""

SUMMARY_LAYOUT = P([
    'root = Answer([headline, risk, energy, workOrders, actions, followUps])',
    ('headline = Headline("No life-safety job is being actively worked, but one is overdue on both counts — Bishopsgate '
     'Tower\'s Fire Risk Assessment lapsed **120 days ago**.")'),
    'risk = Section("1. Risk to life — top priority", [riskDetails, riskCallout])',
    'riskDetails = Details("Fire Risk Assessment", [riskCode, riskBuilding, riskStatus, riskExpiry])',
    'riskCode = Field("Code", "FRA-B-301-2025")',
    'riskBuilding = Field("Building", "Bishopsgate Tower")',
    'riskStatus = Field("Status", "lapsed 120 days ago")',
    'riskExpiry = Field("Expired", "2026-06-29")',
    'riskCallout = Callout("risk", "Pennard Fire Services found compartmentation breaches at the L12 riser.")',
    'energy = Section("3. Energy — Bishopsgate Tower", [energyMetrics, contributors])',
    'energyMetrics = Metrics([anomalies, wasted])',
    'anomalies = Metric("Open anomalies", "10", null, "warn")',
    'wasted = Metric("Wasted energy cost", "£214,927/year", "10 open anomalies in total", "risk")',
    ('contributors = Table(["Largest contributors", "Cost", "Detected"], [["Asset spike +157%", "£40,151/yr", '
     '"2026-09-14"], ["Asset spike +137%", "£112,409/yr", "2026-09-21"]])'),
    'workOrders = Section("Work orders", [gap])',
    'gap = Callout("info", "No work order data was returned in this week\'s feed.")',
    'actions = Section("What to do", [steps])',
    'steps = Steps([reassess])',
    'reassess = Step("Reassess the Bishopsgate FRA now", "FRA-B-301-2025, and close the L12 riser fire-stopping remedial.")',
    'followUps = FollowUps(["What are the energy anomalies at Bishopsgate Tower?", "Show the Fire Risk Assessment details"])',
])

COMPLIANCE = """Four certificates across the Northbridge portfolio expire within 60 days. The gas safety certificate at **Ashgrove** is the most urgent.

| Building | Certificate | Code | Expires | Status |
| --- | --- | --- | --- | --- |
| B-102 Ashgrove | Gas safety | GS-2210 | 2026-11-03 | *Expiring* |
| B-101 Harbour Point | Lift LOLER | LL-118 | 2026-11-19 | *Expiring* |
| B-103 Bishopsgate Tower | Fire alarm service | FA-4471 | 2026-12-08 | Booked |
| B-101 Harbour Point | EICR | EI-0932 | 2026-12-22 | Booked |

Bishopsgate Tower's fire alarm service is already booked with Pennard Fire Services. No certificate has lapsed yet.

**What to do:** (1) book the Ashgrove gas safety inspection this week, (2) ask Harbourside Lift Engineering for a LOLER date."""

COMPLIANCE_LAYOUT = P([
    'root = Answer([h, t, n, todo, more])',
    ('h = Headline("Four certificates across the Northbridge portfolio expire within 60 days. The gas safety certificate '
     'at **Ashgrove** is the most urgent.")'),
    ('t = Table(["Building", "Certificate", "Code", "Expires", "Status"], [["B-102 Ashgrove", "Gas safety", "GS-2210", '
     '"2026-11-03", "Expiring"], ["B-101 Harbour Point", "Lift LOLER", "LL-118", "2026-11-19", "Expiring"], '
     '["B-103 Bishopsgate Tower", "Fire alarm service", "FA-4471", "2026-12-08", "Booked"], ["B-101 Harbour Point", '
     '"EICR", "EI-0932", "2026-12-22", "Booked"]])'),
    ('n = Callout("ok", "Bishopsgate Tower\'s fire alarm service is already booked with Pennard Fire Services. No '
     'certificate has lapsed yet.")'),
    'todo = Section("What to do", [s])',
    's = Steps([s1, s2])',
    's1 = Step("Book the Ashgrove gas safety inspection this week")',
    's2 = Step("Ask Harbourside Lift Engineering for a LOLER date")',
    'more = FollowUps(["Which certificates expire after December?", "Show the Ashgrove gas safety history"])',
])

DIRECTIONS = ("Two vendors moved in opposite directions on SLA completion in September 2026. Apex Mechanical fell "
              "**0.62 points** to **21.88/25**, while Corvane Mechanical rose **0.42 points** to **20.92/25**. Apex "
              "Lifts slipped **0.12 points** to **23.21/25**.")
DIRECTIONS_LAYOUT = P([
    'root = Answer([h, k, more])',
    'h = Headline("Two vendors moved in opposite directions on SLA completion in September 2026.")',
    'k = Metrics([a, c, l])',
    'a = Metric("Apex Mechanical SLA completion", "21.88/25", "down 0.62 points", "risk")',
    'c = Metric("Corvane Mechanical SLA completion", "20.92/25", "+0.42 points", "ok")',
    'l = Metric("Apex Lifts SLA completion", "23.21/25", "-0.12 points", "warn")',
    'more = FollowUps(["What caused Apex Mechanical\'s fall?", "Show the September scorecards"])',
])

POSSESSIVE = ("Bishopsgate Tower's chiller plant used **18% more energy** in September 2026 than in August, and Harbour "
              "Point's boiler ran **14 hours** a day against a 10-hour schedule. Ask Coolair to check Bishopsgate "
              "Tower's chiller sequencing.")
POSSESSIVE_LAYOUT = P([
    'root = Answer([h, k, s])',
    'h = Headline("Bishopsgate Tower\'s chiller plant used **18% more energy** in September 2026 than in August.")',
    'k = Metrics([Metric("Harbour Point boiler run time", "14 hours", "a day against a 10-hour schedule", "warn")])',
    's = Steps([Step("Ask Coolair to check Bishopsgate Tower chiller sequencing")])',
])


@pytest.mark.parametrize("answer,layout", [
    (VENDORS, VENDORS_LIVE),
    (VENDORS, VENDORS_CHART),
    (SUMMARY, SUMMARY_LAYOUT),
    (COMPLIANCE, COMPLIANCE_LAYOUT),
    (DIRECTIONS, DIRECTIONS_LAYOUT),
    (POSSESSIVE, POSSESSIVE_LAYOUT),
], ids=["vendors-live", "vendors-chart", "weekly-summary", "compliance-list", "directions", "possessives"])
def test_a_faithful_layout_is_sent(answer, layout):
    assert ap.read(layout, answer) == ({"v": 1, "lang": layout}, "")


def test_a_data_gap_left_only_in_a_follow_up_is_not_sent():
    # 27 Oct 2026 review: the weekly summary's "No work order data was returned" section dropped, with a
    # follow-up chip naming work orders, read as faithful because the chip's words counted.
    no_gap = SUMMARY_LAYOUT.replace("root = Answer([headline, risk, energy, workOrders, actions, followUps])",
                                    "root = Answer([headline, risk, energy, actions, followUps])")
    no_gap = "\n".join(line for line in no_gap.splitlines() if not line.startswith(("workOrders =", "gap =")))
    no_gap = no_gap.replace('"Show the Fire Risk Assessment details"',
                            '"Which work orders are open at Bishopsgate Tower?", "What did the feed return this week?"')
    view, why = ap.read(no_gap, SUMMARY)
    assert view is None and "negation" in why, why
