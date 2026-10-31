"""A list of issues laid out as Findings must keep each issue's facts with it (agents/answer_presenter.py).

28 Oct 2026: the chat gained Finding(title, text, tone?, code?), one issue of several, each about one asset,
certificate, vendor or work order (apps/frontend/src/openui/components/Finding.jsx). The presenter may lay
out an answer that lists issues as bold-lead paragraphs with one Finding per issue. The set checks cannot
see a date or a code moved from one asset's finding to another's (every figure is still shown somewhere),
so each Finding is bound to its own issue: its title and code are the answer's own, and every figure, date
and code in it is said in the same paragraph as its title or code. The good layout of the Bishopsgate
answer must still be sent, or the findings never show.
"""
from __future__ import annotations

from src.agents import answer_presenter as ap
from src.agents import openui_view as ov

P = "\n".join

# The compliance answer for Bishopsgate Tower (28 Oct 2026, the user's screenshot), as the chat showed it.
BISHOPSGATE = """Bishopsgate Tower (B-301) has two live life-safety gaps — a lapsed Fire Risk Assessment and a lift whose statutory LOLER inspection has run past its expiry date — plus two blocked vendors (fire alarm and standby generator contractors) still shown against assets. On the maintenance/cost side, four plant items (two chillers, an AHU and two boilers) are running open energy anomalies worth roughly **£228,000 a year** in waste, with the worst offender, CHILLER-101, also carrying the site's lowest condition score. Both tracks need action this week.

## 1. Risk to life — act first

**Fire Risk Assessment (FRA-B-301-2025) — Lapsed**, expired 2026-06-29 (about 4 months ago). The building has no current FRA on record; this is the top statutory gap for the whole site.

**Lift Asset-4471 (B-301-LIFT-4471) — LOLER statutory inspection past date.** The certificate on file (LOLER-B-301-4471) shows an expiry of 2026-10-18, already passed. This is a high-criticality (L1) lift with a condition score of 2 (poor), 1 open work order and 2 PPM visits not done, next plan due 2026-12-03. Get the inspection renewed and the open PPM cleared before the lift runs further on an unverified statutory cert.

**Fire alarm panel — ground (B-301-FIRE-PANEL-01).** High criticality, condition score 3, 2 open work orders. The vendor tied to it, **ProudCastle Fire, is blocked** (its BAFE SP203-1 registration lapsed 2026-08-02/03) — any servicing they do on this panel is outside accreditation. The building's Fire Alarm Service certificate (FAS-B-301-2026-03) is also showing **Critical**, expiring today (2026-10-28).

**Standby generator GEN-1.** Covered by **Ostley Power Services**, also a **blocked vendor** (NICEIC lapsed since 2025-09-06). Backup power cover for the building rests on an unaccredited contractor.

**Smoke ventilation panel — stair cores (B-301-SMOKE-VENT-01).** High criticality, condition score 2, next planned maintenance was due 2026-10-27 — now overdue.

## 2. Statutory/legal exposure

**F-Gas certificate (FGAS-B-301-CH101)** for CHILLER-101 expires 2026-10-29 — tomorrow. Renew now or the chiller's refrigerant compliance lapses.

**Northgate Electrical's NICEIC accreditation is lapsed** (-7 days), yet it's the vendor of record for 11 lighting control panels across Levels 1–11 and Basement. No immediate asset fault recorded, but any further electrical work there is unaccredited until renewed.

## 3. Maintenance driving overcost (service/cost tier)

These four plant assets are the ones to fix to stop wasted spend — worst first by money at stake:

| Asset | Condition | Open issues | Energy waste/yr |
|---|---|---|---|
| CHILLER-101 (B-301-CHILLER-101) | 4 (worst on site), high criticality | 3 open energy anomalies, 2 PPM not done | **£114,716** |
| AHU-3 (B-301-AHU-03) | 3, medium criticality | 5 open energy anomalies, 1 open WO, 1 PPM not done | **£74,092** |
| Boiler 1 — central plant (B-301-BOILER-01) | 3, high criticality | 5 open energy anomalies, 1 open WO | **£22,808** |
| Boiler 2 — central plant (B-301-BOILER-02) | 2, medium criticality | 5 open energy anomalies | **£16,413** |"""

LEAD = BISHOPSGATE.split("\n")[0]
FRA = ('f1 = Finding("Fire Risk Assessment", "Lapsed, expired 2026-06-29 (about 4 months ago). The building has no '
       'current FRA on record; this is the top statutory gap for the whole site.", "risk", "FRA-B-301-2025")')
LIFT = ('f2 = Finding("Lift Asset-4471", "LOLER statutory inspection past date. The certificate on file '
        '(LOLER-B-301-4471) shows an expiry of 2026-10-18, already passed. This is a high-criticality (L1) lift with '
        'a condition score of 2 (poor), 1 open work order and 2 PPM visits not done, next plan due 2026-12-03. Get '
        'the inspection renewed and the open PPM cleared before the lift runs further on an unverified statutory '
        'cert.", "risk", "B-301-LIFT-4471")')
NORTHGATE = ('f7 = Finding("Northgate Electrical\'s NICEIC accreditation is lapsed", "(-7 days), yet it\'s the vendor '
             'of record for 11 lighting control panels across Levels 1–11 and Basement. No immediate asset fault '
             'recorded, but any further electrical work there is unaccredited until renewed.", "risk")')

# One Finding per issue, in the answer's order, each with the answer's own words; the plant table stays a
# Table with its chart.
LAYOUT = P([
    'root = Answer([lead, s1, s2, s3, more])',
    'lead = Text(' + ov.json.dumps(LEAD, ensure_ascii=False) + ')',
    's1 = Section("1. Risk to life — act first", [f1, f2, f3, f4, f5])',
    FRA,
    LIFT,
    ('f3 = Finding("Fire alarm panel — ground", "High criticality, condition score 3, 2 open work orders. The vendor '
     'tied to it, **ProudCastle Fire, is blocked** (its BAFE SP203-1 registration lapsed 2026-08-02/03) — any '
     'servicing they do on this panel is outside accreditation. The building\'s Fire Alarm Service certificate '
     '(FAS-B-301-2026-03) is also showing **Critical**, expiring today (2026-10-28).", "risk", "B-301-FIRE-PANEL-01")'),
    ('f4 = Finding("Standby generator GEN-1", "Covered by **Ostley Power Services**, also a **blocked vendor** (NICEIC '
     'lapsed since 2025-09-06). Backup power cover for the building rests on an unaccredited contractor.", "risk")'),
    ('f5 = Finding("Smoke ventilation panel — stair cores", "High criticality, condition score 2, next planned '
     'maintenance was due 2026-10-27 — now overdue.", "risk", "B-301-SMOKE-VENT-01")'),
    's2 = Section("2. Statutory/legal exposure", [f6, f7])',
    ('f6 = Finding("F-Gas certificate", "for CHILLER-101 expires 2026-10-29 — tomorrow. Renew now or the chiller\'s '
     'refrigerant compliance lapses.", "warn", "FGAS-B-301-CH101")'),
    NORTHGATE,
    's3 = Section("3. Maintenance driving overcost (service/cost tier)", [t3, bars, tbl])',
    't3 = Text("These four plant assets are the ones to fix to stop wasted spend — worst first by money at stake:")',
    ('bars = BarChart("Energy waste/yr", ["CHILLER-101 (B-301-CHILLER-101)", "AHU-3 (B-301-AHU-03)", "Boiler 1 — '
     'central plant (B-301-BOILER-01)", "Boiler 2 — central plant (B-301-BOILER-02)"], ["£114,716", "£74,092", '
     '"£22,808", "£16,413"])'),
    ('tbl = Table(["Asset", "Condition", "Open issues", "Energy waste/yr"], [["CHILLER-101 (B-301-CHILLER-101)", '
     '"4 (worst on site), high criticality", "3 open energy anomalies, 2 PPM not done", "**£114,716**"], ["AHU-3 '
     '(B-301-AHU-03)", "3, medium criticality", "5 open energy anomalies, 1 open WO, 1 PPM not done", "**£74,092**"], '
     '["Boiler 1 — central plant (B-301-BOILER-01)", "3, high criticality", "5 open energy anomalies, 1 open WO", '
     '"**£22,808**"], ["Boiler 2 — central plant (B-301-BOILER-02)", "2, medium criticality", "5 open energy '
     'anomalies", "**£16,413**"]])'),
    'more = FollowUps(["Draft the FRA booking email", "Which vendors are blocked at Bishopsgate Tower?"])',
])


def sent(layout: str) -> bool:
    view, why = ap.read(layout, BISHOPSGATE)
    assert (view is None) == bool(why)
    return view is not None


def refused(layout: str, *reasons: str) -> str:
    assert layout != LAYOUT
    view, why = ap.read(layout, BISHOPSGATE)
    assert view is None, "sent a layout that moves or changes a finding's facts"
    assert any(r in why for r in reasons), why
    return why


def test_a_faithful_finding_layout_of_the_bishopsgate_answer_is_sent():
    assert ap.read(LAYOUT, BISHOPSGATE) == ({"v": 1, "lang": LAYOUT}, "")
    # what the reader sees is the answer's own paragraph: the lead in bold, the code, then the rest
    shown = ov.render_markdown(ov.parse(LAYOUT), shown=True)
    assert ("**Fire Risk Assessment** (FRA-B-301-2025) — Lapsed, expired 2026-06-29 (about 4 months ago). "
            "The building has no current FRA on record;") in shown


def test_a_title_may_leave_its_code_to_the_finding():
    # "**Fire Risk Assessment (FRA-B-301-2025) — Lapsed**" may be laid out as the title "Fire Risk Assessment —
    # Lapsed" with the code beside it: the same words in the same order.
    moved = LAYOUT.replace(
        'Finding("Fire Risk Assessment", "Lapsed, expired', 'Finding("Fire Risk Assessment — Lapsed", "expired')
    assert sent(moved)


def test_a_date_moved_to_another_assets_finding_is_not_sent():
    # The FRA's and the lift's dates swapped: every date is still shown, so only the binding sees it.
    swapped = LAYOUT.replace("expired 2026-06-29", "expired 2026-10-18").replace(
        "an expiry of 2026-10-18", "an expiry of 2026-06-29")
    why = refused(swapped, "finding")
    assert "Fire Risk Assessment" in why and "2026/10/18" in why


def test_a_code_moved_to_another_assets_finding_is_not_sent():
    # The lift's certificate code given to the generator, and the lift's asset code given to the FRA.
    moved = LAYOUT.replace("certificate on file (LOLER-B-301-4471)", "certificate on file").replace(
        "an unaccredited contractor.", "an unaccredited contractor (LOLER-B-301-4471).")
    refused(moved, "finding 'Standby generator GEN-1' gives the code LOLER-B-301-4471")
    wrong = LAYOUT.replace('"risk", "FRA-B-301-2025")', '"risk", "B-301-LIFT-4471")')
    refused(wrong, "finding 'Fire Risk Assessment' has the code B-301-LIFT-4471")


def test_a_code_or_title_not_in_the_answer_is_not_sent():
    refused(LAYOUT.replace('"FRA-B-301-2025")', '"FRA-B-301-2026")'), "code is not in the answer: FRA-B-301-2026")
    refused(LAYOUT.replace('Finding("Lift Asset-4471"', 'Finding("Passenger lift 4"'), "title is not in the answer")
    refused(LAYOUT.replace('Finding("Lift Asset-4471"', 'Finding(""'), "a finding has no title")


def test_a_dropped_no_in_a_finding_is_not_sent():
    refused(LAYOUT.replace("The building has no current FRA", "The building has a current FRA"), "negation")
    refused(LAYOUT.replace("2 PPM visits not done", "2 PPM visits done"), "negation")


def test_a_flipped_figure_in_a_finding_is_not_sent():
    # A sign turned round: the accreditation lapsed 7 days ago, not in 7 days' time.
    refused(LAYOUT.replace("(-7 days)", "(+7 days)"), "direction")
    # A figure changed to one the answer gives elsewhere (a 3 is in the date 2026-12-03 of the same
    # paragraph, and in the fire alarm panel's condition score): every figure is still shown somewhere.
    refused(LAYOUT.replace("a condition score of 2 (poor)", "a condition score of 3 (poor)"),
            "finding 'Lift Asset-4471' gives the figure 3")


def test_a_possessive_name_and_a_caveat_in_a_finding_are_held_like_any_text():
    # The caveat "yet" must stay.
    refused(LAYOUT.replace("(-7 days), yet it's", "(-7 days), and it's"), "caveat")
    # "Northgate Electrical's" names Northgate Electrical: a title or text that writes the name plainly is
    # the same finding, as it is the same name anywhere else in a layout.
    plain = LAYOUT.replace("Northgate Electrical's NICEIC", "Northgate Electrical NICEIC").replace(
        "the chiller's refrigerant", "the chiller refrigerant")
    assert plain != LAYOUT
    assert sent(plain)
    # A name dropped from a finding's title is a title the answer never wrote; from its text, a dropped name.
    refused(LAYOUT.replace("Northgate Electrical's NICEIC", "Northgate NICEIC"), "title is not in the answer")
    refused(LAYOUT.replace("Covered by **Ostley Power Services**", "Covered by **Ostley**"), "dropped names")


def test_findings_in_one_paragraph_keep_their_own_dates():
    # Two issues in one paragraph: each date belongs to the issue its own clause or sentence names.
    answer = ("Two certificates at Harbour Point need booking this month. The gas safety certificate GS-2210 "
              "expires on 2026-11-03, and the lift examination LL-118 expires on 2026-11-19. Both inspections can "
              "be booked with the usual contractors once the dates are confirmed.")
    good = P([
        'root = Answer([h, a, b, n])',
        'h = Headline("Two certificates at Harbour Point need booking this month.")',
        'a = Finding("The gas safety certificate", "expires on 2026-11-03", "warn", "GS-2210")',
        'b = Finding("the lift examination", "expires on 2026-11-19", "warn", "LL-118")',
        'n = Text("Both inspections can be booked with the usual contractors once the dates are confirmed.")',
    ])
    assert ap.read(good, answer) == ({"v": 1, "lang": good}, "")
    swapped = good.replace('"expires on 2026-11-03", "warn", "GS-2210"', '"expires on 2026-11-19", "warn", "GS-2210"')
    swapped = swapped.replace('"expires on 2026-11-19", "warn", "LL-118"', '"expires on 2026-11-03", "warn", "LL-118"')
    view, why = ap.read(swapped, answer)
    assert view is None and "finding" in why and "date" in why, why


def test_the_layout_prompt_teaches_finding():
    system = ap.system_prompt()
    assert "write one Finding per issue, in the answer's order" in system
    assert "never move one to another issue" in system
    assert "Callout, Finding, Text, Details or Step" in system
    assert "Finding(title: string, text: string" in system  # the component itself, from the chat's spec
    # (28 Oct 2026: live Bishopsgate runs copied "Fire Risk Assessment (FRA-B-301-2025) — Lapsed"
    # whole as the title and also gave FRA-B-301-2025 as code, so the chat drew the code twice.)
    assert "leave the brackets out of the title and give that code as code" in system
    # (28 Oct 2026: 4 of 6 live Bishopsgate runs drew the BarChart, then dropped its column from the
    # Table, and _rows_kept rejected the layout. A Table keeps every column; only a two-column copy
    # of the chart is the repeat.)
    assert "as a Table of only those two columns" in system
    assert "never drop a column because a chart shows it" in system
