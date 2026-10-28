"""The orchestrator's OpenUI answers, read on the server (agents/openui_view.py).

The model writes its final answer as an OpenUI Lang program, which the chat draws as UI. Every
other reader of `answer` (reopened chats, report cards, PDF export, cron and support emails,
Trace Lab, memory extraction, the answer checkers) reads Markdown, so the server keeps `answer`
Markdown: it reads the program, writes a rendition of what the chat draws, and carries the
program beside it as an `openui_view` card payload.

The Python reader is held to OpenUI's own parser: tests/fixtures/openui/cases.json is what
@openuidev/lang-core makes of each program (apps/frontend/scripts/openui-fixtures.mjs writes it),
and schema.json is the component set the chat draws (apps/frontend/src/openui/spec.js).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents import openui_view as ov

FIXTURES = Path(__file__).parent / "fixtures" / "openui"
CASES = json.loads((FIXTURES / "cases.json").read_text())
SCHEMA = json.loads((FIXTURES / "schema.json").read_text())
PROGRAM = {c["name"]: c["program"] for c in CASES}


def test_the_component_set_and_prop_order_match_the_chat():
    assert {name: [[p, req] for p, _typ, req in props] for name, props in ov.SCHEMA.items()} == SCHEMA


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_reader_builds_the_tree_openui_builds(case):
    result = ov.parse_full(case["program"])
    assert ov.summary(result.root) == case["root"]
    assert sorted(result.unresolved) == case["unresolved"]
    assert sorted(result.orphans) == case["orphaned"]


DASHBOARD_MD = """Three things need you this week: the gas safety certificate at Building A expires on **Friday**, **7 work orders** are overdue and Building A is using **18% more energy** than in early October.

- Overdue work orders: 7 (+3 on last week)
- Certificates due in 30 days: 4 (1 this week)
- SLA met this month: 91% (-2 pt on September)
- Building A energy use: 241.0 kWh/m² (Building B: 178.4)

## Certificates to act on

**Gas safety certificate, Building A** (GSC-A-2025) — Expires on Friday and the inspection is not booked yet.

**Lift thorough examination, Building B** — Expires on 9 Nov; Liftcare has the visit booked for 4 Nov.

**Overdue work orders by trade**

| Item | Value |
| --- | --- |
| HVAC | 3 |
| Electrical | 2 |
| Plumbing | 1 |
| Fire safety | 1 |

## Overdue work orders

7 past their due date, 3 of them HVAC

| Work order | Job | Building | Days overdue | Vendor |
| --- | --- | --- | --- | --- |
| WO-1184 | AHU-2 drive belt replacement | Building A | 9 | Acme Mechanical |
| WO-1179 | Emergency lighting monthly test | Building B | 6 | Brightline Electrical |
| WO-1171 | Chiller 1 high-pressure alarm | Building A | 5 | Acme Mechanical |

_4 more: 2 plumbing, 1 electrical, 1 fire safety_

## What to do

1. **Book the gas safety inspection at Building A** — The certificate expires on Friday. (Draft the booking email)
2. **Chase Acme Mechanical on the 3 HVAC jobs** — Two are past their SLA. (Show the HVAC backlog)
3. **Check the AHU-2 schedule at Building A** — It has run overnight since 12 Oct.

Ask next: Compare energy use at Building A and Building B · Which vendors are slipping on SLA?"""


def test_the_rendition_reads_as_the_markdown_the_model_used_to_write():
    assert ov.render_markdown(ov.parse(PROGRAM["example-dashboard"])) == DASHBOARD_MD


def test_the_rendition_carries_every_block_the_chat_draws():
    md = ov.render_markdown(ov.parse(PROGRAM["everything"]))
    assert md.startswith("All **12 assets** at Building A are covered.")
    assert "- Assets: 12 (all covered)" in md
    assert "**Lift:** The lift certificate expires in 9 days." in md
    assert "## Asset detail\n\n12 assets, 3 types" in md
    assert "| Asset | Type | Age |" in md and "| AHU-1 | Air handling | 9 |" in md
    assert "**AHU-1** — Air handling unit\n- Location: Roof\n- Installed: 2017" in md
    assert "**Energy** (kWh/m²)\n\n| Period | A | B |" in md and "| W2 | 4.3 | 3.3 |" in md
    assert "1. **Book the lift inspection** — Expires in 9 days. (Draft email)\n2. **Nothing else**" in md
    assert "```\nSubject: Lift inspection\nTo: ops@example.com\nCc: pm@example.com\nEmail body:\nHello,\n\nPlease book.\n```" in md
    # What the reader can click is part of what they read: the answer check must see it too
    # (a figure or a code can sit in a button or a follow-up — review, 26 Oct 2026).
    assert md.endswith("Ask next: What else is due? · Show the lift history")


def test_a_button_to_a_page_reads_as_a_link_and_one_that_asks_as_its_words():
    md = ov.render_markdown(ov.parse(
        'root = Answer([b])\nb = Buttons([x, y])\nx = Button("Open Energy", @OpenUrl("/energy"))\n'
        'y = Button("Chase the 9 overdue HVAC jobs", @ToAssistant("Chase them"))'))
    assert md == "[Open Energy](/energy) · Chase the 9 overdue HVAC jobs"


def test_markdown_passes_through_untouched():
    for text in ["Seven work orders are overdue.", "## Heading\n\n| a | b |\n| --- | --- |\n| 1 | 2 |",
                 "Use `result = Query(x)` in the report.", "total = Metric(7) is not a program"]:
        assert ov.split_answer(text) == (text, None)


def test_a_program_becomes_markdown_with_its_view():
    md, view = ov.split_answer(PROGRAM["example-dashboard"])
    assert md == DASHBOARD_MD
    assert view == ov.View(program=PROGRAM["example-dashboard"].strip(), markdown=DASHBOARD_MD)


def test_a_program_that_draws_nothing_becomes_its_words_never_its_code():
    md, view = ov.split_answer('root = Answer([h])\nh = Headline(42)\nt = Text("Some words survive")')
    assert view is None
    assert "Some words survive" in md
    assert "root =" not in md and "Answer(" not in md


def test_paired_lists_never_shift():
    # A label or value that is not a plain string/number stays in its place (review, 26 Oct 2026):
    # no figure may land under another item's label in the stored answer.
    years = ov.render_markdown(ov.parse('root = Answer([c])\nc = BarChart("EUI by year", ["2023", 2024, 2025], [241, 230, 225])'))
    assert "| 2024 | 230 |" in years and "| 2025 | 225 |" in years
    spend = ov.render_markdown(ov.parse('root = Answer([c])\nc = BarChart("Spend", ["Acme", "Brightline", "Coolair"], ["£1,200", 800, null])'))
    assert "| Acme | £1,200 |" in spend and "| Brightline | 800 |" in spend and "| Coolair | — |" in spend
    trend = ov.render_markdown(ov.parse('root = Answer([c])\nc = LineChart("Trend", ["Jan", "Feb", "Mar"], [Series("A", [4.1, null, 3.9])])'))
    assert "| Feb | — |" in trend and "| Mar | 3.9 |" in trend
    table = ov.render_markdown(ov.parse('root = Answer([t])\nt = Table(["Year", 2024, "Change"], [["EUI", 230, "-4%"]])'))
    assert "| Year | 2024 | Change |" in table and "| EUI | 230 | -4% |" in table


@pytest.mark.parametrize("value,shown", [
    (0.2847, "0.2847"), (2026, "2026"), (2026.0, "2026"), (12400, "12400"), (241.0, "241"), (-2.04, "-2.04"),
    (1e21, "1e+21"), (1.5e21, "1.5e+21"), (1e-7, "1e-7"), (0.00001, "0.00001"), (0.004, "0.004"),
    (0.1 + 0.2, "0.3"), (-0.0, "0"), (1e16, "10000000000000000"), (12345678901234567890, "12345678901234600000"),
    (1234567890123456, "1234567890123460"), (123456789012344.5, "123456789012345"), (999999999999999.9, "1000000000000000"),
    (True, "Yes"), (False, "No"),
    # rounds past the largest double: JS shows Infinity (28 Oct 2026: this raised instead)
    (1.7976931348623157e308, "Infinity"), (-1.7976931348623157e308, "-Infinity"),
])
def test_a_number_reads_as_the_chat_shows_it(value, shown):
    # 27 Oct 2026: the chat shows a number literal as String(Number(n.toPrecision(15))), -0 as 0 (shared.jsx
    # fmt: never rounded to 2 places, never grouped). The rendition and the answer check must read the
    # same text the reader sees. Expected values printed by node with the chat's own fmt.
    assert ov._num(value) == shown


def test_a_ragged_row_keeps_its_extra_cells_under_blank_headings():
    # 27 Oct 2026: the chat's Table keeps a row's cells past its headings under blank headings, so the
    # Markdown keeps them too instead of cutting the row at the last heading.
    md = ov.render_markdown(ov.parse('root = Answer([t])\nt = Table(["Vendor", "Score"], [["Apex", 86.12, "22.45/25"], ["Corvane", 91.3]])'))
    assert md == "| Vendor | Score |  |\n| --- | --- | --- |\n| Apex | 86.12 | 22.45/25 |\n| Corvane | 91.3 | — |"


def test_a_plus_signed_number_keeps_its_place_and_its_sign():
    # 27 Oct 2026: OpenUI reads `+0.45` as a stray `+` and then a number, so the row gains a null cell and
    # every figure after it moves one column right (@openuidev/lang-core gives ["Corvane Mechanical",
    # null, 0.45, "22.45/25"] too). The plus is part of the figure the model wrote, so the program is
    # read and sent with it as text: "+0.45". A plus inside a string or between two numbers is left.
    prog = ('root = Answer([h, t, n])\nh = Headline("SLA change by vendor")\n'
            't = Table(["Vendor", "Change vs Aug", "Sep 2026 SLA response"], '
            '[["Corvane Mechanical", +0.45, "22.45/25"], ["Apex", -0.73, "23.44/25"]])\n'
            'n = Text("Up (+3 on last week), total 2+3")')
    md, view = ov.split_answer(prog)
    assert "| Corvane Mechanical | +0.45 | 22.45/25 |" in md and "| Apex | -0.73 | 23.44/25 |" in md
    assert '["Corvane Mechanical", "+0.45", "22.45/25"]' in view.program
    assert 'n = Text("Up (+3 on last week), total 2+3")' in view.program
    assert ov.quote_signed_numbers('x = [+1, 2+3, "a +4", Metric("b", + 5), \'c +6\']\ny = +7') == (
        'x = ["+1", 2+3, "a +4", Metric("b", "+5"), \'c +6\']\ny = "+7"')
    # a program without one is sent exactly as written
    assert ov.split_answer(PROGRAM["example-dashboard"])[1].program == PROGRAM["example-dashboard"].strip()


def test_chart_values_the_chat_would_cut_off_are_kept_under_the_view():
    # A chart draws one bar or point per label (Charts.jsx), so a value past the last label is never
    # drawn. It travels as the view's tail like any other part the chat drops, and the presenter, which
    # must lose nothing, sends no view at all.
    result = ov.parse_full('root = Answer([c, l])\nc = BarChart("Overdue", ["HVAC"], [3, 2])\n'
                           'l = LineChart("Trend", ["Jan"], [Series("A", [4.1, 3.9])])')
    assert result.dropped == ["2", "3.9"]
    md, view = ov.split_answer('root = Answer([c])\nc = BarChart("Overdue by trade", ["HVAC"], [3, 2])')
    assert view.tail == "2" and md.endswith("\n\n2")


def test_a_program_written_root_last_is_still_read():
    # 27 Oct 2026: a program whose root statement came last was cut at `root = Answer(`, so its other
    # statements were kept as prose and the reader got code. The chat reads a program from its root
    # on (detect.js splitProgram), so the program is sent with its root first.
    md, view = ov.split_answer('h = Headline("Seven work orders are overdue at Harbour Point.")\nroot = Answer([h])')
    assert md == "Seven work orders are overdue at Harbour Point."
    assert view.program == 'root = Answer([h])\nh = Headline("Seven work orders are overdue at Harbour Point.")'
    fenced = 'Here is the answer.\n```\nh = Headline("Seven are overdue.")\nroot = Answer([h])\n```'
    md, view = ov.split_answer(fenced)
    assert md == "Here is the answer.\n\nSeven are overdue."
    assert view.program == 'Here is the answer.\n\nroot = Answer([h])\nh = Headline("Seven are overdue.")'
    assert ov.split_program('h = Headline("x")\nroot = Answer([h])') == ("", 'root = Answer([h])\nh = Headline("x")', True)


def test_the_shown_rendition_leaves_out_what_the_reader_must_click_for():
    # What the reader sees without a click: no follow-up chips or buttons, and a collapsed section as
    # its title and summary only. The presenter holds the answer's facts to this text.
    root = ov.parse('root = Answer([h, s, st, b, f])\nh = Headline("Seven are overdue")\n'
                    's = Section("Overdue work orders", [t], "3 of them HVAC", true)\n'
                    't = Table(["Work order", "Vendor"], [["WO-9", "Zed Co"]])\n'
                    'st = Steps([Step("Chase Zed Co", "Two are late.", Button("Draft the chase", @ToAssistant("x")))])\n'
                    'b = Buttons([Button("Open the backlog", @OpenUrl("/work-orders"))])\nf = FollowUps(["Which are HVAC?"])')
    full, shown = ov.render_markdown(root), ov.render_markdown(root, shown=True)
    assert "WO-9" in full and "Ask next" in full and "Draft the chase" in full and "Open the backlog" in full
    assert shown == "Seven are overdue\n\n## Overdue work orders\n\n3 of them HVAC\n\n1. **Chase Zed Co** — Two are late."


def test_a_finding_reads_as_its_bold_lead_its_code_and_the_rest_of_it():
    # 28 Oct 2026: Finding(title, text, tone?, code?) is one issue of several. Its rendition is the
    # paragraph the answer would have written, in the order the chat draws it (title, code, text), so
    # every word and figure is kept in the answer's order; the tone is a colour, never a word.
    md = ov.render_markdown(ov.parse(PROGRAM["finding-with-code-and-tone"]))
    assert md == ("**Fire Risk Assessment** (FRA-B-301-2025) — Lapsed, expired 2026-06-29 (about 4 months ago). "
                  "The building has **no current FRA** on record.")
    assert "risk" not in md
    assert ov.render_markdown(ov.parse(PROGRAM["finding-null-tone-with-code"])) == (
        "**Lift 4** (LIFT-B-04) — Examination booked for 4 Nov.")
    assert ov.render_markdown(ov.parse(PROGRAM["finding-plain"])) == (
        "One thing to watch\n\n**Boiler 2** — Pressure dropped twice this week.\n- 1.1 bar on Monday\n- 0.9 bar on Thursday")
    # Consecutive findings read as consecutive paragraphs, inside a Section too.
    assert ov.render_markdown(ov.parse(PROGRAM["findings-inside-a-section"])) == (
        "Two certificates need you.\n\n## Risk to life\n\n1 lapsed, 1 due\n\n"
        "**Fire Risk Assessment** (FRA-B-2025) — Lapsed on 29 Jun.\n\n**Gas safety** — Expires on Friday.")
    # A blank part is left out, as the chat leaves it out; a title already in bold is not bolded twice.
    one = 'root = Answer([f])\nf = Finding({}, {})'
    assert ov.render_markdown(ov.parse(one.format('""', '"Expires on Friday."'))) == "Expires on Friday."
    assert ov.render_markdown(ov.parse(one.format('"Gas safety"', '""'))) == "**Gas safety**"
    assert ov.render_markdown(ov.parse(one.format('"**Gas safety**"', '"Expires on Friday."'))) == (
        "**Gas safety** — Expires on Friday.")


def test_a_finding_is_shown_unless_its_section_is_closed():
    # A Finding is where the answer's facts are shown: the shown rendition keeps it, and only a
    # collapsed Section hides it, as it hides any block.
    prog = ('root = Answer([f, s])\nf = Finding("Gas safety", "Expires on Friday.", "warn", "GS-4471")\n'
            's = Section("More", [g], "1 more", true)\ng = Finding("Lift 4", "Booked for 4 Nov.")')
    root = ov.parse(prog)
    assert ov.render_markdown(root, shown=True) == "**Gas safety** (GS-4471) — Expires on Friday.\n\n## More\n\n1 more"
    assert "**Lift 4** — Booked for 4 Nov." in ov.render_markdown(root)
    md, view = ov.split_answer(prog)
    assert view is not None and md.startswith("**Gas safety** (GS-4471)")
    # The words of a program that draws nothing keep a finding's code and drop its tone keyword.
    assert ov.plain_text('f = Finding("Gas safety", "Expires on Friday.", "warn", "GS-4471")') == (
        "Gas safety\n\nExpires on Friday.\n\nGS-4471")


CAVEAT = "I could not read Building C, so these figures cover A and B only."


def test_prose_before_the_program_is_kept_above_its_rendition():
    md, view = ov.split_answer(CAVEAT + "\n\n" + PROGRAM["example-clarifying-question"])
    assert md.startswith(CAVEAT + "\n\n")
    assert "Which building do you mean?" in md
    assert view.program.startswith(CAVEAT)  # the chat draws the same lead above the view
    assert ov.reconcile(view, md) == {"v": 1, "lang": view.program}


def test_prose_run_on_into_the_program_is_still_split():
    md, _ = ov.split_answer("Here is the picture." + PROGRAM["example-clarifying-question"])
    assert md.startswith("Here is the picture.\n\n")
    assert "root =" not in md


def test_a_truncated_program_with_no_words_never_comes_back_as_code():
    md, view = ov.split_answer("root = Answer([head, kpis, trades")
    assert view is None
    assert md and "root" not in md and "Answer(" not in md


def test_components_the_chat_drops_keep_their_words_and_figures():
    # Named arguments make OpenUI drop the component; its words and figure must not vanish.
    prog = 'root = Answer([h, m])\nh = Headline("Seven are overdue")\nm = Metrics([Metric(label: "Overdue", value: 7)])'
    md, view = ov.split_answer(prog)
    assert md.startswith("Seven are overdue")
    assert "Overdue" in md and "7" in md
    out = ov.reconcile(view, md)
    assert "Overdue" in out["tail"] and "7" in out["tail"]  # drawn under the view in the chat


def test_a_dropped_parent_reports_its_words_once():
    prog = 'root = Answer([s])\ns = Section(42, [t])\nt = Text("Buried detail")'
    md, _ = ov.split_answer(prog)
    assert md.count("Buried detail") == 1


def test_plain_text_is_the_programs_words_in_order():
    text = ov.plain_text('root = Answer([h, m])\nh = Headline("Seven are \\"late\\".")\nm = Metric("Overdue", 7, null, "risk")')
    assert text == 'Seven are "late".\n\nOverdue'


def test_reconcile_keeps_the_view_only_while_it_matches_the_answer():
    view = ov.View(program="root = Answer([h])\nh = Headline(\"Hi\")", markdown="Hi")
    assert ov.reconcile(view, "Hi") == {"v": 1, "lang": view.program}
    # A later check appended a note: the view stays, the note is drawn under it.
    assert ov.reconcile(view, "Hi\n\n_Check the figures before quoting them._") == {
        "v": 1, "lang": view.program, "tail": "_Check the figures before quoting them._"}
    # A later step rewrote the answer: the view would show stale figures, so it goes.
    assert ov.reconcile(view, "Hello there") is None
    assert ov.reconcile(None, "Hi") is None
    big = ov.View(program="x" * (ov.MAX_PROGRAM_CHARS + 1), markdown="Hi")
    assert ov.reconcile(big, "Hi") is None


def test_the_payload_rides_the_tool_calls_like_the_dashboard_cards():
    assert ov.payload_entry({"v": 1, "lang": "p"}) == {"tool": "openui_view", "input": {}, "output": {"v": 1, "lang": "p"}}


@pytest.mark.parametrize("value,on", [(None, True), ("1", True), ("true", True), ("0", False), ("false", False), ("off", False), ("OFF", False)])
def test_the_switch(monkeypatch, value, on):
    if value is None:
        monkeypatch.delenv("OPENUI_ANSWERS", raising=False)
    else:
        monkeypatch.setenv("OPENUI_ANSWERS", value)
    assert ov.enabled() is on


def test_switched_off_no_view_is_sent_but_a_stray_program_is_still_read(monkeypatch):
    # The switch stops the prompt teaching the format and stops views being sent. A thread whose
    # history is full of programs may still answer in one; it must not reach readers as code.
    monkeypatch.setenv("OPENUI_ANSWERS", "0")
    md, view = ov.split_answer(PROGRAM["example-dashboard"])
    assert md == DASHBOARD_MD
    assert ov.reconcile(view, md) is None
