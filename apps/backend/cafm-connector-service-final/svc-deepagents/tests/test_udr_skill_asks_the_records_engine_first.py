"""The database agent asks the records engine before it writes SQL of its own (25 Nov 2026).

"Which vendors work at Aire Mill Court" was routed to the udr agent, which never called
answer_from_records - the engine that answers "which vendors at a building" through every link
(work orders, assets, certificates, contracts, PPM visits). It searched the catalogue and wrote
its own SQL instead: a WITH query svc-udr refuses, then a column its skill named that the table
does not have (`vendors.specialty`). The answer came back "Not answered" after 60 s.

The skill gave it two "first call" tools (answer_from_records and find_tables) and sent every
join to udr_execute_select, and "vendors at a building" reads as a join. These hold the skill to
one order: the records engine first, the catalogue and raw SQL only when it could not answer.
"""
from __future__ import annotations

import pathlib
import re

SKILL = (pathlib.Path(__file__).resolve().parents[1] / "skills" / "udr" / "SKILL.md").read_text(
    encoding="utf-8")


def _tool_rows() -> dict[str, str]:
    """The tools table of section 2, as {tool name: its row}."""
    section = SKILL[SKILL.index("## 2. Your tools"):SKILL.index("## 3. Recipes")]
    rows = {}
    for line in section.splitlines():
        m = re.match(r"\|\s*`([a-z_]+)\(", line)
        if m:
            rows[m.group(1)] = line
    return rows


def test_only_the_records_engine_is_called_first():
    firsts = [name for name, row in _tool_rows().items() if "first call" in row.lower()]
    assert firsts == ["answer_from_records"]


def test_raw_sql_waits_for_the_records_engine():
    row = _tool_rows()["udr_execute_select"]
    assert "answer_from_records" in row


def test_vendors_at_a_building_is_named_as_a_records_engine_question():
    section = SKILL[SKILL.index("## 2. Your tools"):SKILL.index("## 3. Recipes")]
    assert re.search(r"which vendors work at", section, re.I)


def test_the_vendors_table_names_no_specialty_column():
    vendors_row = next(line for line in SKILL.splitlines() if line.startswith("| `vendors` |"))
    assert "specialty" not in vendors_row
