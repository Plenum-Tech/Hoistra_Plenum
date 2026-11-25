"""The skills name the columns production has, and the shared loop asks the records engine first
(25 Nov 2026).

An agent writes SQL with the names its skill gives it. Checked against production's column list
(information_schema, read-only), these were wrong:

* doc-rag listed doc-rag's Python attribute names as table columns: `file_name`, `num_pages`,
  `document_id`, `section_label`, `text_content`. The columns are `original_filename`,
  `page_count`, `ingestion_id`, `heading`, `chunk_text`.
* query-builder and doc-rag sent documents through `assets.document_ids` (assets has no such
  column - the link is the asset_documents table) and named `building_energy_profiles.site_id`
  (it has building_id).

And the shared query-builder loop told every sub-agent to call find_tables first, ahead of each
agent's own "answer_from_records first" - so the udr agent searched the catalogue and wrote SQL
for "which vendors work at Aire Mill Court" instead of asking the records engine.
"""
from __future__ import annotations

import pathlib
import re

import pytest

SKILLS = pathlib.Path(__file__).resolve().parents[1] / "skills"
DOC_RAG = (SKILLS / "doc-rag" / "SKILL.md").read_text(encoding="utf-8")
QUERY_BUILDER = (SKILLS / "query-builder" / "SKILL.md").read_text(encoding="utf-8")


def _row(text: str, table: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(f"| `{table}` |"))


@pytest.mark.parametrize("table,real,wrong", [
    ("ingestion_documents", ["original_filename", "page_count"], ["file_name", "num_pages"]),
    ("document_chunks", ["ingestion_id", "heading", "chunk_text"],
     ["document_id", "section_label", "text_content"]),
])
def test_doc_rag_tables_name_the_real_columns(table, real, wrong):
    key_columns = _row(DOC_RAG, table).split("|")[3]
    for col in real:
        assert f"`{col}`" in key_columns
    for col in wrong:
        assert not re.search(rf"(?<!API: )`{col}`", key_columns), col


def _outside_the_traps_table(text: str) -> str:
    """The text without query-builder's "You would assume | Live schema actually has" rows, which
    name a missing column on purpose, to say it is missing."""
    lines, out, in_traps = text.splitlines(), [], False
    for line in lines:
        if line.startswith("| You would assume"):
            in_traps = True
        elif in_traps and not line.startswith("|"):
            in_traps = False
        if not in_traps:
            out.append(line)
    return "\n".join(out)


@pytest.mark.parametrize("ghost", ["assets.document_ids", "building_energy_profiles.site_id",
                                   "document_chunks.document_id"])
def test_no_skill_names_a_column_that_does_not_exist(ghost):
    for name, text in (("doc-rag", DOC_RAG), ("query-builder", QUERY_BUILDER)):
        assert ghost not in _outside_the_traps_table(text), f"{name} names {ghost}"


def test_documents_of_an_asset_go_through_asset_documents():
    assert "asset_documents" in QUERY_BUILDER and "asset_documents" in DOC_RAG


def test_the_shared_loop_asks_the_records_engine_before_the_catalogue():
    locate = QUERY_BUILDER[QUERY_BUILDER.index("## Step 2"):]
    locate = locate[:locate.index("**Call `get_schema()`")]
    assert "answer_from_records" in locate
    assert locate.index("answer_from_records") < locate.index("find_tables")
