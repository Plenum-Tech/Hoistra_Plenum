"""The chat's finished answers, laid out by the model (agents/answer_presenter.py).

The engines, the planner and the compliance summary answer in Markdown that has passed its checks.
The presenter asks the model to lay that answer out as an OpenUI view. These pin what makes a view
safe to send: it says what the answer says (every figure and name, nothing added), the chat drops
none of it, and the answer itself leaves exactly as it was written. Anything else sends no view.
"""
from __future__ import annotations

import asyncio

import pytest

from src.agents import answer_presenter as ap
from src.agents import openui_view as ov

QUESTION = "Which vendors are slipping on SLA this month?"

# The contract_performance engine's answer, 27 Oct 2026.
ANSWER = """**Apex Mechanical is the vendor slipping most on SLA performance in the latest scorecard month, September 2026.**

Its SLA response component fell from **24.17 to 23.44 / 25**, and SLA completion fell from **22.50 to 21.88 / 25** versus August. Its overall score also dropped **2.04 points to 86.12**.

| Vendor | Sep-26 SLA response | Sep-26 SLA completion | Position |
| --- | --- | --- | --- |
| **Apex Mechanical** | 23.44 / 25 | 21.88 / 25 | Primary SLA slippage |
| Apex Lifts | 24.11 / 25 | 23.21 / 25 | Minor deterioration |
| Corvane Mechanical | 22.45 / 25 | 20.92 / 25 | Lowest current completion result, but improving |

These are **earned, uncapped** September 2026 scorecard components. There is no October scorecard yet.

**What to do:** ask Apex Mechanical for the September jobs behind the completion decline and a recovery plan."""

GOOD = "\n".join([
    'root = Answer([h, k, bars, t, gap, todo])',
    'h = Headline("**Apex Mechanical** is the vendor slipping most on SLA performance in the latest scorecard month, September 2026.")',
    'k = Metrics([m1, m2, m3])',
    'm1 = Metric("Apex Mechanical SLA response", "23.44 / 25", "from 24.17 versus August", "risk")',
    'm2 = Metric("Apex Mechanical SLA completion", "21.88 / 25", "from 22.50 versus August", "risk")',
    'm3 = Metric("Apex Mechanical overall score", 86.12, "dropped 2.04 points", "warn")',
    'bars = BarChart("Sep-26 SLA completion", ["Apex Mechanical", "Apex Lifts", "Corvane Mechanical"], ["21.88 / 25", "23.21 / 25", "20.92 / 25"])',
    't = Table(["Vendor", "Sep-26 SLA response", "Sep-26 SLA completion", "Position"], [["Apex Mechanical", "23.44 / 25", "21.88 / 25", "Primary SLA slippage"], ["Apex Lifts", "24.11 / 25", "23.21 / 25", "Minor deterioration"], ["Corvane Mechanical", "22.45 / 25", "20.92 / 25", "Lowest current completion result, but improving"]])',
    'gap = Callout("info", "These are earned, uncapped September 2026 scorecard components. There is no October scorecard yet.")',
    'todo = Section("What to do", [s])',
    's = Steps([s1])',
    's1 = Step("Ask Apex Mechanical for the September jobs behind the completion decline and a recovery plan")',
])


def test_a_faithful_layout_is_sent_as_the_view():
    view, why = ap.read(GOOD, ANSWER)
    assert why == "" and view == {"v": 1, "lang": GOOD}


@pytest.mark.parametrize("change,reason", [
    # a figure the answer has, left out
    (('["Apex Lifts", "24.11 / 25", "23.21 / 25", "Minor deterioration"], ', ''), "dropped figures"),
    # a row of a table, left out, though its figures are still shown elsewhere
    (('["Apex Mechanical", "23.44 / 25", "21.88 / 25", "Primary SLA slippage"], ', ''), "dropped table cells"),
    # a figure the answer never stated (a total the model worked out)
    (('m3 = Metric("Apex Mechanical overall score", 86.12', 'm3 = Metric("Apex Mechanical overall score", 86.1'), "figures"),
    (('"dropped 2.04 points"', '"dropped 2.04 points; 3 vendors in all"'), "added figures"),
    # a name the answer uses, gone
    (('versus August"', 'on the month before"'), "dropped names august"),
])
def test_a_layout_that_does_not_say_what_the_answer_says_is_not_sent(change, reason):
    bad = GOOD.replace(*change)
    assert bad != GOOD
    view, why = ap.read(bad, ANSWER)
    assert view is None and reason in why


SWAP_ROWS = [  # Apex Lifts' and Corvane's SLA completion swapped, in the table and in the chart
    ('["Apex Lifts", "24.11 / 25", "23.21 / 25"', '["Apex Lifts", "24.11 / 25", "20.92 / 25"'),
    ('["Corvane Mechanical", "22.45 / 25", "20.92 / 25"', '["Corvane Mechanical", "22.45 / 25", "23.21 / 25"'),
    ('["21.88 / 25", "23.21 / 25", "20.92 / 25"]', '["21.88 / 25", "20.92 / 25", "23.21 / 25"]'),
]


@pytest.mark.parametrize("changes,reason", [
    # 27 Oct 2026 review: a figure under the wrong row, bar or label read as faithful, because every
    # figure and cell was still somewhere in the layout.
    (SWAP_ROWS, "moved table cells"),
    (SWAP_ROWS[2:], "chart pairs Apex Lifts with 20.92 / 25"),
    ([('"Apex Mechanical SLA response", "23.44 / 25"', '"Apex Mechanical SLA response", "21.88 / 25"'),
      ('"Apex Mechanical SLA completion", "21.88 / 25"', '"Apex Mechanical SLA completion", "23.44 / 25"')],
     "labels 21.88 'Apex Mechanical SLA response'"),
    # a fall shown as a rise, a change shown as the score itself
    ([('"dropped 2.04 points"', '"+2.04 points"')], "changed the direction of 2.04"),
    ([('86.12, "dropped 2.04 points"', '2.04, "dropped 86.12 points"')], "direction"),
])
def test_a_figure_moved_to_another_row_bar_or_label_is_not_sent(changes, reason):
    bad = GOOD
    for old, new in changes:
        assert old in bad
        bad = bad.replace(old, new)
    view, why = ap.read(bad, ANSWER)
    assert view is None and reason in why, why


def test_a_change_keeps_its_direction_however_it_is_written():
    # "dropped 2.04 points" may read "down 2.04 points" or "-2.04 points": the direction is kept.
    for note in ("down 2.04 points", "-2.04 points", "fell 2.04 points"):
        assert ap.read(GOOD.replace('"dropped 2.04 points"', f'"{note}"'), ANSWER)[1] == "", note
    # The prompt's own wording for a change given without a level.
    only_change = ANSWER.replace("dropped **2.04 points to 86.12**", "dropped **2.04 points**")
    layout = GOOD.replace('Metric("Apex Mechanical overall score", 86.12, "dropped 2.04 points", "warn")',
                          'Metric("Overall score, month-on-month", "down 2.04 points", null, "warn")')
    assert ap.read(layout, only_change) == ({"v": 1, "lang": layout}, "")
    # The same change drawn as the overall score itself.
    level = layout.replace('Metric("Overall score, month-on-month", "down 2.04 points", null, "warn")',
                           'Metric("Apex Mechanical overall score", "2.04", "September 2026", "ok")')
    view, why = ap.read(level, only_change)
    assert view is None and "2.04" in why


def test_a_possessive_name_is_the_name():
    # 27 Oct 2026: "Apex Mechanical's" read as the name "mechanical's", so a layout that wrote the
    # name plainly was rejected and no chart showed.
    owned = ANSWER.replace("ask Apex Mechanical for the September jobs", "ask for Apex Mechanical's September jobs")
    assert ap.read(GOOD, owned)[1] == ""
    assert ap.read(GOOD, owned.replace("Mechanical's", "Mechanical’s"))[1] == ""
    assert ap.names("Ask about Apex Mechanical's score and the workers' rota") == {"apex", "mechanical"}


def test_a_layout_written_root_last_is_sent_root_first():
    # 27 Oct 2026: the root statement written last cut the program short, so the layout drew nothing.
    lines = GOOD.split("\n")
    view, why = ap.read("\n".join(lines[1:] + lines[:1]), ANSWER)
    assert why == "" and view["lang"].startswith("root = Answer([h, k, bars, t, gap, todo])\nh = Headline(")


def test_a_plus_signed_figure_is_sent_as_written():
    # A bare +0.45 would move every cell after it one column right; it is sent as the text "+0.45".
    answer = ANSWER.replace("| Lowest current completion result, but improving |",
                            "| +0.45 |").replace("| Position |", "| Change |")
    layout = GOOD.replace('"Position"]', '"Change"]').replace('"Lowest current completion result, but improving"]', "+0.45]")
    view, why = ap.read(layout, answer)
    assert why == "" and '"20.92 / 25", "+0.45"]' in view["lang"]


def test_a_number_is_held_to_what_the_reader_sees():
    # 27 Oct 2026: the chat shows a number literal exactly (0.2847, 2025, 950), so a layout that writes
    # the answer's figures as numbers says what it says; one that writes 241.0 as a number shows 241.
    answer = ("The average unit rate in September 2026 was £0.2847/kWh across the portfolio.\n\n"
              "| Year | Spend (£) | Rate |\n| --- | --- | --- |\n| 2025 | 1,200 | 0.2847 |\n| 2026 | 950 | 0.3125 |")
    layout = ('root = Answer([h, t])\nh = Headline("The average unit rate in September 2026 was £0.2847/kWh across the '
              'portfolio.")\nt = Table(["Year", "Spend (£)", "Rate"], [[2025, 1200, 0.2847], [2026, 950, 0.3125]])')
    assert ap.read(layout, answer)[1] == ""
    view, why = ap.read(layout.replace("0.3125]", "0.31]"), answer)
    assert view is None and "figures" in why


def test_a_layout_the_chat_would_partly_drop_is_not_sent():
    named = GOOD.replace('gap = Callout("info", ', 'gap = Callout(tone: "info", text: ')
    assert ap.read(named, ANSWER)[0] is None
    assert ap.read("Here is the answer as a table.", ANSWER) == (None, "no program")
    assert ap.read('root = Answer([x])', ANSWER)[0] is None


def test_the_answer_is_never_rewritten_to_fit():
    # Words may be shortened, but not so many that a fact could go with them.
    thin = GOOD.replace("Lowest current completion result, but improving", "Lowest").replace(
        "These are earned, uncapped September 2026 scorecard components. There is no October scorecard yet.",
        "September 2026 only; no October scorecard.")
    assert ap.read(thin, ANSWER)[0] is None


def test_the_answers_own_title_is_a_label_not_a_fact():
    titled = "# Weekly Summary — Portfolio (Work Orders, Compliance, Energy)\n\n" + ANSWER
    assert ap.read(GOOD, titled)[0] is not None
    # a heading further down is part of the answer and must stay
    inner = ANSWER.replace("These are", "## Scorecard Basis\n\nThese are")
    assert ap.read(GOOD, inner)[0] is None


def test_figures_and_names_read_as_a_reader_reads_them():
    assert ap.figures("£1,200 and 4.50 and 07\n1. **Do it**\n2. Then 9") == {"1200", "4.5", "7", "9"}
    assert ap.cells("| A | B |\n| --- | --- |\n| **Apex** | 23.44 / 25 |\n| x \\| y | — |") == ["A", "B", "**Apex**", "23.44 / 25", "x | y"]
    assert ap.names("Apex Mechanical is late. Its SLA fell at Building A.") == {"mechanical", "sla", "building"}


def test_which_answers_are_laid_out(monkeypatch):
    monkeypatch.delenv("OPENUI_ANSWERS", raising=False)
    monkeypatch.delenv("OPENUI_PRESENTER", raising=False)
    assert ap.wanted(ANSWER, [])
    assert not ap.wanted("Seven work orders are overdue.", [])  # a sentence reads well as it is
    assert not ap.wanted(ANSWER, [{"tool": "openui_view", "output": {"v": 1, "lang": "x"}}])  # the loop laid it out
    assert not ap.wanted(ANSWER, [{"tool": "compliance_response", "output": {}}])  # drawn as the compliance cards
    assert not ap.wanted(ANSWER, [], choices=[{"label": "a"}])
    assert not ap.wanted(GOOD, [])  # already a program
    monkeypatch.setenv("OPENUI_PRESENTER", "0")
    assert not ap.wanted(ANSWER, [])
    monkeypatch.setenv("OPENUI_PRESENTER", "1")
    monkeypatch.setenv("OPENUI_ANSWERS", "0")
    assert not ap.wanted(ANSWER, [])


def test_the_model_is_told_the_answer_is_its_only_source():
    calls = []

    async def llm(system, user, role):
        calls.append((system, user, role))
        return GOOD
    assert asyncio.run(ap.present(QUESTION, ANSWER, llm)) == {"v": 1, "lang": GOOD}
    system, user, role = calls[0]
    assert role == "layout"
    assert "The answer is your only source" in system and ov.prompt_section() in system
    # 27 Oct 2026: a drop of 2.04 points was drawn as the overall score itself, and the vendors'
    # scores were closed away under a collapsed section. A bare number is shown without the answer's
    # commas or trailing zeros, so figures are written as the answer writes them, as text.
    assert '"down 2.04 points"' in system and "never collapses a Section" in system
    assert '"1,200"' in system and '"-2.04"' in system
    assert QUESTION in user and ANSWER.strip() in user


def test_no_model_no_view(monkeypatch):
    async def broken(system, user, role):
        raise RuntimeError("provider down")
    assert asyncio.run(ap.present(QUESTION, ANSWER, broken)) is None

    async def slow(system, user, role):
        await asyncio.sleep(1)
        return GOOD
    monkeypatch.setattr(ap, "TIMEOUT_S", 0.01)
    assert asyncio.run(ap.present(QUESTION, ANSWER, slow)) is None
