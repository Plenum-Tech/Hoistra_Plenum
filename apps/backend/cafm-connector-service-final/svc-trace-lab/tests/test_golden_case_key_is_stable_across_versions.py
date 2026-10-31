"""A case keeps its key across uploads (the ID column, else a hash of the normalised question and context);
its version changes only when what is asked or what counts as right changes. 15 Oct 2026. Spec §1.7."""
from __future__ import annotations

import dataclasses
import hashlib
from datetime import date

from trace_lab import golden


def test_the_id_column_is_the_key():
    assert golden.case_key_for("Anything?", None, id_cell="21") == ("21", "id_column")


def test_the_hash_key_is_sha1_of_the_normalised_question_and_context():
    want = hashlib.sha1("how many floors|".encode()).hexdigest()[:12]
    assert golden.case_key_for("How many floors?", None) == (want, "hash")
    assert golden.case_key_for("  HOW many   floors ?? ", None)[0] == want


def test_none_context_and_blank_context_are_different_cases():
    assert golden.case_key_for("Q", "")[0] != golden.case_key_for("Q", None)[0]
    assert golden.case_key_for("Q", "The user is on Home.")[0] != golden.case_key_for("Q", None)[0]


def test_norm_text():
    assert golden.norm_text("  Ｈow\tmany  FLOORS?! ") == "how many floors"
    assert golden.norm_text(None) == ""


def _row(**kw):
    base = dict(row_no=2, case_key="C1", key_source="id_column", case_version="", question="How many floors?",
                followups=(), expected_answer="22 floors", evaluation="objective",
                expected_values=tuple(golden.parse_expected_values("22")), source=None, verified_on=None,
                page_context=None, ask_as="company", requirement_ids=(), notes=None, flags=())
    base.update(kw)
    return golden.CaseRow(**base)


def test_the_version_is_twelve_hex_and_changes_with_what_counts():
    v = golden.case_version_for(_row())
    assert len(v) == 12 and int(v, 16) >= 0
    for change in ({"expected_answer": "23 floors"}, {"evaluation": "judgment"}, {"followups": ("And the lifts?",)},
                   {"expected_values": tuple(golden.parse_expected_values("23"))}, {"page_context": ""},
                   {"question": "How many storeys?"}):
        assert golden.case_version_for(_row(**change)) != v, change


def test_the_version_ignores_notes_source_verified_on_and_requirements():
    v = golden.case_version_for(_row())
    same = _row(notes="checked by Ada", source="Buildings sheet", verified_on=date(2026, 10, 13),
                requirement_ids=("R1",), flags=("unverified",), row_no=9)
    assert golden.case_version_for(same) == v
    assert golden.case_version_for(_row(expected_answer="22   floors")) == v      # whitespace in the answer


def test_parse_upload_stores_the_same_version_case_version_for_computes():
    from golden_xlsx import make_xlsx
    r = golden.parse_upload("set.xlsx", make_xlsx([["ID", "Question", "Expected answer"], ["C1", "How many floors?", "22 floors"]]),
                            today=date(2026, 10, 15))
    c = r.cases[0]
    assert c.case_version == golden.case_version_for(dataclasses.replace(c, case_version=""))
