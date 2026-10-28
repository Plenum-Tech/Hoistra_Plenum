"""A laid-out answer must mean what the answer means (agents/answer_presenter.py, review of 27 Oct 2026).

The presenter's check used to compare sets: every figure, every table cell and every name somewhere in
the layout. A layout could then say the opposite of the answer and still pass: a "not" dropped, a fall
shown as a rise, a figure under another vendor, a unit or a date changed, a data gap left only in a
follow-up chip or closed away in a collapsed section. Each case below is one the review reproduced;
every one must send no view (the chat then draws the Markdown, which loses nothing), and its reason
must say what failed. The good layout beside each one must still be sent, or charts never show.
"""
from __future__ import annotations

import pytest

from src.agents import answer_presenter as ap

P = "\n".join


def sent(program: str, answer: str) -> bool:
    view, why = ap.read(program, answer)
    assert (view is None) == bool(why)
    return view is not None


def refused(program: str, answer: str, *reasons: str) -> str:
    view, why = ap.read(program, answer)
    assert view is None, "sent a layout that changes the answer"
    assert any(r in why for r in reasons), why
    return why


# ── a "not" or a "no" dropped or flipped ────────────────────────────────────────────────────────────

NOT_COMPLIANT = ("Bishopsgate Tower is not compliant this week. Its gas safety certificate GS-4471 expired on "
                 "2026-10-20 and the fire risk assessment FRA-B-301-2025 is overdue, so both need booking before Friday.")
NOT_COMPLIANT_LAYOUT = P([
    'root = Answer([h, t])',
    'h = Headline("Bishopsgate Tower is not compliant this week.")',
    ('t = Text("Its gas safety certificate GS-4471 expired on 2026-10-20 and the fire risk assessment '
     'FRA-B-301-2025 is overdue, so both need booking before Friday.")'),
])

SCORECARD = ("## Vendor scorecards\n\nThere is no October 2026 scorecard yet for Apex Mechanical. The September 2026 "
             "scorecard shows an overall score of 82.5 across 14 work orders, and the vendor met its response target "
             "on 12 of them. Scores are refreshed after month close.")
SCORECARD_LAYOUT = P([
    'root = Answer([s])',
    's = Section("Vendor scorecards", [c, k, t])',
    'c = Callout("info", "There is no October 2026 scorecard yet for Apex Mechanical.")',
    'k = Metrics([m1, m2])',
    'm1 = Metric("Overall score, September 2026", "82.5", "across 14 work orders")',
    'm2 = Metric("Response target met", "12", "of the 14 work orders")',
    't = Text("Scores are refreshed after month close.")',
])

RENEWAL = ("Apex Mechanical has not renewed its gas safety certificate GS-4471, so the boiler service at Bishopsgate "
           "Tower cannot be signed off. Ask Apex Mechanical for the renewed certificate before booking the service.")
RENEWAL_LAYOUT = P([
    'root = Answer([c, s])',
    ('c = Callout("risk", "Apex Mechanical has not renewed its gas safety certificate GS-4471, so the boiler service '
     'at Bishopsgate Tower cannot be signed off.")'),
    's = Steps([Step("Ask Apex Mechanical for the renewed certificate before booking the service")])',
])

PROVISIONAL = ("The provisional electricity figure for Harbour Point is 41,200 kWh for September 2026. The meter "
               "correction from the supplier has not been applied yet, so treat it as an estimate until the October "
               "bill arrives.")
PROVISIONAL_LAYOUT = P([
    'root = Answer([h, k, c])',
    'h = Headline("The provisional electricity figure for Harbour Point is 41,200 kWh for September 2026.")',
    'k = Metrics([Metric("Harbour Point electricity, September 2026", "41,200 kWh")])',
    ('c = Callout("warn", "The meter correction from the supplier has not been applied yet, so treat it as an '
     'estimate until the October bill arrives.")'),
])


@pytest.mark.parametrize("answer,layout,change,reason", [
    (NOT_COMPLIANT, NOT_COMPLIANT_LAYOUT, ("is not compliant", "is compliant"), "negation"),
    (SCORECARD, SCORECARD_LAYOUT, ("There is no October 2026 scorecard yet", "There is an October 2026 scorecard"),
     "negation"),
    (RENEWAL, RENEWAL_LAYOUT, ("has not renewed", "has renewed"), "negation"),
    (RENEWAL, RENEWAL_LAYOUT, ("cannot be signed off", "can be signed off"), "negation"),
    (PROVISIONAL, PROVISIONAL_LAYOUT, ("has not been applied yet", "has been applied"), "negation"),
    (PROVISIONAL, PROVISIONAL_LAYOUT, ('"The provisional electricity', '"The electricity'), "caveat"),
], ids=["not-compliant", "no-scorecard", "not-renewed", "cannot-sign-off", "not-applied", "provisional"])
def test_a_negation_or_caveat_dropped_is_not_sent(answer, layout, change, reason):
    assert sent(layout, answer)
    assert change[0] in layout
    refused(layout.replace(*change), answer, reason)


# ── a figure's sign, direction, unit, currency or date changed ──────────────────────────────────────

MOVES = ("Northgate Facilities moved **-0.62 points** to 81.40 against August, while Apex Lifts moved **+0.45 points** "
         "to 88.10. Northgate Facilities is now the lowest-scoring vendor in the portfolio and needs a review this month.")
MOVES_LAYOUT = P([
    'root = Answer([h, k])',
    'h = Headline("Northgate Facilities is now the lowest-scoring vendor in the portfolio and needs a review this month.")',
    'k = Metrics([m1, m2])',
    'm1 = Metric("Northgate Facilities overall score", "81.40", "-0.62 points against August", "risk")',
    'm2 = Metric("Apex Lifts overall score", "88.10", "+0.45 points", "ok")',
])


def test_a_sign_flipped_is_not_sent_but_a_sign_written_in_words_is():
    assert sent(MOVES_LAYOUT, MOVES)
    assert sent(MOVES_LAYOUT.replace('"-0.62 points', '"down 0.62 points').replace('"+0.45 points', '"up 0.45 points'),
                MOVES)
    flipped = MOVES_LAYOUT.replace('"-0.62 points', '"+0.62 points').replace('"+0.45 points', '"-0.45 points')
    refused(flipped, MOVES, "changed the direction of 0.62", "changed the direction of 0.45")
    # A sign the answer never wrote is a direction it never gave.
    refused(MOVES_LAYOUT.replace('"88.10"', '"+88.10"'), MOVES, "changed the direction of 88.1")


CHANGES = ("Apex Mechanical's overall score fell 2.04 points to 86.12 in September 2026, while Corvane Mechanical "
           "improved by 0.45 to 91.30. Apex Mechanical is the vendor to chase this month.")
CHANGES_LAYOUT = P([
    'root = Answer([h, k])',
    'h = Headline("Apex Mechanical is the vendor to chase this month.")',
    'k = Metrics([m1, m2])',
    'm1 = Metric("Apex Mechanical overall score, September 2026", "86.12", "fell 2.04 points", "risk")',
    'm2 = Metric("Corvane Mechanical overall score", "91.30", "improved by 0.45", "ok")',
])


def test_a_change_is_never_drawn_as_a_level_or_turned_round():
    assert sent(CHANGES_LAYOUT, CHANGES)
    level = CHANGES_LAYOUT.replace(
        'm1 = Metric("Apex Mechanical overall score, September 2026", "86.12", "fell 2.04 points", "risk")',
        'm1 = Metric("Apex Mechanical overall score", "2.04", "September 2026", "risk")\n'
        'm3 = Metric("Apex Mechanical points", "86.12")').replace("Metrics([m1, m2])", "Metrics([m1, m2, m3])")
    refused(level, CHANGES, "2.04")
    # Even with the sentence kept beside it, the tile still reads as the score.
    kept = level.replace("Answer([h, k])", "Answer([h, k, t])") + (
        '\nt = Text("Apex Mechanical\'s overall score fell 2.04 points to 86.12 in September 2026.")')
    refused(kept, CHANGES, "drew the change 2.04 as a level")
    refused(CHANGES_LAYOUT.replace('"improved by 0.45"', '"-0.45"'), CHANGES, "changed the direction of 0.45")


ENERGY = ("Energy use moved in opposite directions this month: Harbour Point fell 18%, Bishopsgate rose 7% against "
          "September 2026, and the portfolio average is unchanged.")
ENERGY_LAYOUT = P([
    'root = Answer([h, k])',
    'h = Headline("Energy use moved in opposite directions this month, and the portfolio average is unchanged.")',
    ('k = Metrics([Metric("Harbour Point energy use", "down 18%"), Metric("Bishopsgate energy use", "up 7%", '
     '"against September 2026")])'),
])


def test_a_fall_and_a_rise_drawn_as_plain_bars_are_not_sent():
    assert sent(ENERGY_LAYOUT, ENERGY)
    bars = ENERGY_LAYOUT.replace(
        'k = Metrics([Metric("Harbour Point energy use", "down 18%"), Metric("Bishopsgate energy use", "up 7%", '
        '"against September 2026")])',
        'k = BarChart("Change against September 2026", ["Harbour Point", "Bishopsgate"], [18, 7], "%")')
    refused(bars, ENERGY, "lost the direction of 18")
    swapped = ENERGY_LAYOUT.replace('"down 18%"', '"down 7%"').replace('"up 7%"', '"up 18%"')
    refused(swapped, ENERGY, "direction")


CHILLER = ("Chiller 2 at Harbour Point has been offline for 12 days and there is no redundancy left on the chilled "
           "water loop. Book the repair with Coolair before the next hot spell.")
CHILLER_LAYOUT = P([
    'root = Answer([c, s])',
    ('c = Callout("risk", "Chiller 2 at Harbour Point has been offline for 12 days and there is no redundancy left on '
     'the chilled water loop.")'),
    's = Steps([Step("Book the repair with Coolair before the next hot spell")])',
])
PENALTIES = ("Penalties rose 12% to £1,200 this month at Harbour Point, all of them for late reactive jobs. Ask Coolair "
             "for the job list behind the September 2026 penalties before the next review.")
PENALTIES_LAYOUT = P([
    'root = Answer([h, s])',
    'h = Headline("Penalties rose 12% to £1,200 this month at Harbour Point, all of them for late reactive jobs.")',
    's = Steps([Step("Ask Coolair for the job list behind the September 2026 penalties before the next review")])',
])
EXPIRY = ("Two certificates at Ashgrove expire soon: the gas safety certificate GS-2210 expires 03/09/2026 and the lift "
          "LOLER certificate LL-118 expires 12/11/2026. Book both inspections with the current vendors.")
EXPIRY_LAYOUT = P([
    'root = Answer([h, t, s])',
    'h = Headline("Two certificates at Ashgrove expire soon.")',
    ('t = Table(["Certificate", "Code", "Expires"], [["Gas safety", "GS-2210", "03/09/2026"], '
     '["Lift LOLER", "LL-118", "12/11/2026"]])'),
    's = Steps([Step("Book both inspections with the current vendors")])',
])


@pytest.mark.parametrize("answer,layout,changes,reason", [
    (CHILLER, CHILLER_LAYOUT, [("12 days", "12 hrs")], "changed the unit of 12"),
    (CHILLER, CHILLER_LAYOUT, [("there is no redundancy left", "there is redundancy left")], "negation"),
    (PENALTIES, PENALTIES_LAYOUT, [("rose 12%", "rose 12 kWh")], "changed the unit of 12"),
    (PENALTIES, PENALTIES_LAYOUT, [("£1,200", "$1,200")], "changed the currency of 1200"),
    (EXPIRY, EXPIRY_LAYOUT, [("03/09/2026", "09/03/2026"), ("12/11/2026", "11/12/2026")], "changed dates"),
], ids=["days-as-hours", "no-redundancy", "percent-as-kwh", "pounds-as-dollars", "day-month-swapped"])
def test_a_unit_currency_or_date_changed_is_not_sent(answer, layout, changes, reason):
    assert sent(layout, answer)
    bad = layout
    for old, new in changes:
        assert old in bad
        bad = bad.replace(old, new)
    refused(bad, answer, reason)


# ── a figure under another label ────────────────────────────────────────────────────────────────────

OVERDUE = P([
    "Overdue work orders by vendor this week:",
    "",
    "| Vendor | Overdue |",
    "| --- | --- |",
    "| Acme | 30 |",
    "| Brightline | 12 |",
    "| Corvane | 4 |",
    "",
    "Acme holds most of the backlog.",
])
OVERDUE_LAYOUT = P([
    'root = Answer([h, c, n])',
    'h = Headline("Overdue work orders by vendor this week")',
    'c = BarChart("Overdue", ["Acme", "Brightline", "Corvane"], [30, 12, 4])',
    'n = Text("Acme holds most of the backlog.")',
])
GAS = P([
    "Two of the three buildings hold a current gas safety certificate.",
    "",
    "| Building | Gas safety certificate current | Next inspection |",
    "| --- | --- | --- |",
    "| B-101 Harbour Point | No | November 2026 |",
    "| B-102 Ashgrove | Yes | March 2027 |",
    "| B-103 Bishopsgate Tower | Yes | January 2027 |",
    "",
    "Nothing else is due before November.",
])
GAS_LAYOUT = P([
    'root = Answer([h, t, n])',
    'h = Headline("Two of the three buildings hold a current gas safety certificate.")',
    ('t = Table(["Building", "Gas safety certificate current", "Next inspection"], [["B-101 Harbour Point", "No", '
     '"November 2026"], ["B-102 Ashgrove", "Yes", "March 2027"], ["B-103 Bishopsgate Tower", "Yes", "January 2027"]])'),
    'n = Text("Nothing else is due before November.")',
])


def test_a_bar_or_a_cell_under_another_label_is_not_sent():
    assert sent(OVERDUE_LAYOUT, OVERDUE)
    refused(OVERDUE_LAYOUT.replace("[30, 12, 4]", "[4, 30, 12]"), OVERDUE, "moved table cells")
    assert sent(GAS_LAYOUT, GAS)
    # "No" became "Yes": "no" is still inside "November", but not as that row's cell.
    refused(GAS_LAYOUT.replace('[["B-101 Harbour Point", "No"', '[["B-101 Harbour Point", "Yes"'), GAS,
            "dropped table cells No")


# ── a fact only in a chip or behind a click ─────────────────────────────────────────────────────────

BASIS = ("Northbridge vendors scored an average of 82.5 in September across 14 work orders.\n\n"
         "Apex Mechanical led with 91.2 and Delta Electrical trailed with 68.4.\n\n"
         "There is no October scorecard yet, so these figures may change once October jobs are scored.")
BASIS_LAYOUT = P([
    'root = Answer([h, k, s])',
    'h = Headline("Northbridge vendors scored an average of 82.5 in September across 14 work orders.")',
    'k = Metrics([m1, m2])',
    'm1 = Metric("Apex Mechanical", "91.2", "led")',
    'm2 = Metric("Delta Electrical", "68.4", "trailed")',
    's = Section("Scorecard basis", [c])',
    'c = Callout("warn", "There is no October scorecard yet, so these figures may change once October jobs are scored.")',
])


def test_a_caveat_or_a_table_closed_away_is_not_sent():
    assert sent(BASIS_LAYOUT, BASIS)
    closed = BASIS_LAYOUT.replace('Section("Scorecard basis", [c])',
                                  'Section("Scorecard basis", [c], "Where these figures come from", true)')
    refused(closed, BASIS, "negation", "caveat")
    # The caveat only in a follow-up chip.
    chip = BASIS_LAYOUT.replace("root = Answer([h, k, s])", "root = Answer([h, k, f])").replace(
        's = Section("Scorecard basis", [c])', 'f = FollowUps(["Why is there no October scorecard yet?"])')
    chip = "\n".join(line for line in chip.splitlines() if not line.startswith("c = "))
    refused(chip, BASIS, "negation", "caveat")


# 27 Oct 2026, live: "What needs my attention this week?" (the answer as the dashboard example draws it),
# laid out by the model with the overdue work orders in a collapsed section beside an open bar chart.
ATTENTION = P([
    ("Three things need you this week: the gas safety certificate at Building A expires on **Friday**, **7 work "
     "orders** are overdue and Building A is using **18% more energy** than in early October."),
    "",
    "- Overdue work orders: 7 (+3 on last week)",
    "- Certificates due in 30 days: 4 (1 this week)",
    "- SLA met this month: 91% (-2 pt on September)",
    "- Building A energy use: 241.0 kWh/m² (Building B: 178.4)",
    "",
    "**Overdue work orders by trade**",
    "",
    "| Item | Value |",
    "| --- | --- |",
    "| HVAC | 3 |",
    "| Electrical | 2 |",
    "| Plumbing | 1 |",
    "| Fire safety | 1 |",
    "",
    "## Overdue work orders",
    "",
    "7 past their due date, 3 of them HVAC",
    "",
    "| Work order | Job | Building | Days overdue | Vendor |",
    "| --- | --- | --- | --- | --- |",
    "| WO-1184 | AHU-2 drive belt replacement | Building A | 9 | Acme Mechanical |",
    "| WO-1179 | Emergency lighting monthly test | Building B | 6 | Brightline Electrical |",
    "| WO-1171 | Chiller 1 high-pressure alarm | Building A | 5 | Acme Mechanical |",
    "",
    "_4 more: 2 plumbing, 1 electrical, 1 fire safety_",
    "",
    "## What to do",
    "",
    "1. **Book the gas safety inspection at Building A** — The certificate expires on Friday.",
    "2. **Chase Acme Mechanical on the 3 HVAC jobs** — Two are past their SLA.",
    "3. **Check the AHU-2 schedule at Building A** — It has run overnight since 12 Oct.",
])
ATTENTION_LAYOUT = P([
    'root = Answer([head, metrics, tradeChart, overdueSection, todoSection, next])',
    ('head = Headline("Three things need you this week: the gas safety certificate at Building A expires on '
     '**Friday**, **7 work orders** are overdue and Building A is using **18% more energy** than in early October.")'),
    'metrics = Metrics([overdueMetric, certificatesMetric, slaMetric, energyMetric])',
    'overdueMetric = Metric("Overdue work orders", 7, "+3 on last week", "risk")',
    'certificatesMetric = Metric("Certificates due in 30 days", 4, "1 this week", "warn")',
    'slaMetric = Metric("SLA met this month", "91%", "-2 pt on September", "warn")',
    'energyMetric = Metric("Building A energy use", "241.0 kWh/m²", "Building B: 178.4", "neutral")',
    'tradeChart = BarChart("Overdue work orders by trade", ["HVAC", "Electrical", "Plumbing", "Fire safety"], [3, 2, 1, 1])',
    'overdueSection = Section("Overdue work orders", [overdueTable, moreWork], "7 past their due date, 3 of them HVAC", true)',
    ('overdueTable = Table(["Work order", "Job", "Building", "Days overdue", "Vendor"], [["WO-1184", "AHU-2 drive belt '
     'replacement", "Building A", 9, "Acme Mechanical"], ["WO-1179", "Emergency lighting monthly test", "Building B", 6, '
     '"Brightline Electrical"], ["WO-1171", "Chiller 1 high-pressure alarm", "Building A", 5, "Acme Mechanical"]])'),
    'moreWork = Text("*4 more: 2 plumbing, 1 electrical, 1 fire safety*")',
    'todoSection = Section("What to do", [steps])',
    'steps = Steps([bookInspection, chaseAcme, checkSchedule])',
    'bookInspection = Step("Book the gas safety inspection at Building A", "The certificate expires on Friday.", bookingButton)',
    'bookingButton = Button("Draft the booking email", @ToAssistant("Draft the booking email"), "primary")',
    'chaseAcme = Step("Chase Acme Mechanical on the 3 HVAC jobs", "Two are past their SLA.", backlogButton)',
    'backlogButton = Button("Show the HVAC backlog", @ToAssistant("Show the HVAC backlog"))',
    'checkSchedule = Step("Check the AHU-2 schedule at Building A", "It has run overnight since 12 Oct.")',
    'next = FollowUps(["Compare energy use at Building A and Building B", "Which vendors are slipping on SLA?"])',
])


def test_records_behind_a_closed_section_are_not_sent():
    why = refused(ATTENTION_LAYOUT, ATTENTION, "behind a click")
    assert "1184" in why
    # The same layout with the section open says what the answer says.
    assert sent(ATTENTION_LAYOUT.replace('"7 past their due date, 3 of them HVAC", true)',
                                         '"7 past their due date, 3 of them HVAC")'), ATTENTION)
    minimal = P([
        "Overdue work orders this week:",
        "",
        "| Work order | Vendor | Days overdue |",
        "| --- | --- | --- |",
        "| WO-9 | Zed Co | 9 |",
        "| WO-7 | Ayr Ltd | 2 |",
    ])
    chart = P([
        'root = Answer([h, c, s])',
        'h = Headline("Overdue work orders this week")',
        'c = BarChart("Days overdue", ["WO-9", "WO-7"], [9, 2])',
        's = Section("Work order", [t], "Vendor", true)',
        't = Table(["Work order", "Vendor", "Days overdue"], [["WO-9", "Zed Co", 9], ["WO-7", "Ayr Ltd", 2]])',
    ])
    refused(chart, minimal, "behind a click", "dropped table cells")
    assert sent(chart.replace('"Vendor", true)', '"Vendor")'), minimal)


# ── faithful layouts the old check refused (charts never showed) ────────────────────────────────────

ENERGY_TABLE = P([
    "Harbour Point uses the most energy of the three buildings this year.",
    "",
    "| Building | EUI (kWh/m²) | Spend (£) |",
    "| --- | --- | --- |",
    "| B-101 Harbour Point | 241.0 | 12,400 |",
    "| B-102 Ashgrove | 178.4 | 9,850 |",
    "| B-103 Bishopsgate Tower | 162.9 | 8,120 |",
])
ENERGY_TABLE_LAYOUT = P([
    'root = Answer([h, t])',
    'h = Headline("Harbour Point uses the most energy of the three buildings this year.")',
    ('t = Table(["Building", "EUI (kWh/m²)", "Spend (£)"], [["B-101 Harbour Point", "241.0", 12400], '
     '["B-102 Ashgrove", "178.4", 9850], ["B-103 Bishopsgate Tower", "162.9", 8120]])'),
])


def test_a_numeric_cell_reads_as_the_chat_shows_it():
    # 12400 is shown as "12400": the same figure as "12,400", only without the grouping.
    assert sent(ENERGY_TABLE_LAYOUT, ENERGY_TABLE)
    # 241.0 as a number is shown as "241": the answer's precision is gone, so it is refused.
    refused(ENERGY_TABLE_LAYOUT.replace('"241.0"', "241.0"), ENERGY_TABLE, "dropped table cells 241.0")
    refused(ENERGY_TABLE_LAYOUT.replace("12400", "12500"), ENERGY_TABLE, "figures")


def test_emphasis_and_inline_numbering_are_not_facts():
    status = P([
        "Two certificates at Harbour Point need action before the end of November.",
        "",
        "| Certificate | Status |",
        "| --- | --- |",
        "| Gas safety | *Expired* |",
        "| Lift LOLER | *Expiring* |",
    ])
    layout = P([
        'root = Answer([h, t])',
        'h = Headline("Two certificates at Harbour Point need action before the end of November.")',
        't = Table(["Certificate", "Status"], [["Gas safety", "Expired"], ["Lift LOLER", "Expiring"]])',
    ])
    assert sent(layout, status)
    steps = P([
        'root = Answer([h, s])',
        'h = Headline("Apex Mechanical and Corvane Mechanical both missed their SLA completion target in September.")',
        's = Section("What to do", [st])',
        'st = Steps([Step("Ask Apex Mechanical for the late jobs"), Step("Ask Corvane Mechanical for a recovery plan")])',
    ])
    lead = "Apex Mechanical and Corvane Mechanical both missed their SLA completion target in September."
    inline = lead + "\n\nWhat to do: (1) ask Apex Mechanical for the late jobs, (2) ask Corvane Mechanical for a recovery plan."
    bold = lead + "\n\n## What to do\n\n**1.** Ask Apex Mechanical for the late jobs\n**2.** Ask Corvane Mechanical for a recovery plan"
    assert sent(steps, inline) and sent(steps, bold)
    # a figure in brackets mid-sentence is still a figure
    assert ap.figures("Chillers (3) need a service") == {"3"}


CERTS = ("Three certificates need renewal before the week is out: Bishopsgate Tower's Fire Alarm Service certificate "
         "expires tomorrow and Harbourside Lift Engineering's LOLER competent-person certificate expires in five days, "
         "while Clearwater Compliance's Public Liability Insurance lapses today and that vendor has an open work order.")
CERTS_LAYOUT = P([
    'root = Answer([h, t, c])',
    'h = Headline("Three certificates need renewal before the week is out.")',
    ('t = Table(["Holder", "Certificate", "Expires"], [["Bishopsgate Tower", "Fire Alarm Service certificate", "tomorrow"], '
     '["Harbourside Lift Engineering", "LOLER competent-person certificate", "in five days"], '
     '["Clearwater Compliance", "Public Liability Insurance", "lapses today"]])'),
    'c = Callout("warn", "Clearwater Compliance has an open work order.")',
])


@pytest.mark.parametrize("apostrophe", ["'", "’"])
def test_possessive_names_written_plainly_are_sent(apostrophe):
    assert sent(CERTS_LAYOUT, CERTS.replace("'", apostrophe))


# ── a clause dropped ────────────────────────────────────────────────────────────────────────────────

# 28 Oct 2026: a live layout of the Bishopsgate answer dropped the whole clause about CHILLER-101's
# condition score and kept 96% of the answer's words, so the old overall share let it through.
WORST = ("Four plant items are running open energy anomalies worth roughly **£228,000 a year** in waste, "
         "with the worst offender, CHILLER-101, also carrying the site's lowest condition score. "
         "Both tracks need action this week.")
WORST_LAYOUT = P([
    'root = Answer([h, t])',
    'h = Headline("Four plant items are running open energy anomalies worth roughly **£228,000 a year** in waste.")',
    't = Text("With the worst offender, CHILLER-101, also carrying the site\'s lowest condition score. Both tracks need action this week.")',
])


def test_a_clause_dropped_is_not_sent_though_most_words_stay():
    assert sent(WORST_LAYOUT, WORST)
    dropped = WORST_LAYOUT.replace("With the worst offender, CHILLER-101, also carrying the site\'s lowest condition score. ",
                                   "CHILLER-101. ")
    refused(dropped, WORST, "dropped a clause")
