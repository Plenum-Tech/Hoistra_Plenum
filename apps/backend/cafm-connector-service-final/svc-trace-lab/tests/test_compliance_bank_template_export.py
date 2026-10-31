"""The compliance question bank is exported as a downloadable CSV of question TEMPLATES only: 161 rows, nothing
pre-filled from one deployment's register. The frozen data file, not the bank, drives the export; a drift test reads
the bank (read-only) and fails when it changes. 15 Oct 2026. Findings question-bank.md section 5, decision 4."""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from pathlib import Path

import pytest

from compliance_bank_generator import BANK, EXCLUDED, FROZEN, bank_rows, build
from trace_lab import golden

PLACEHOLDERS = {"{building}", "{building_2}", "{building_variant}", "{vendor}", "{vendor_variant}", "{vendor_prefix}",
                "{certificate_type}", "{accreditation}", "{trade}", "{absent_building}", "{absent_vendor}",
                "{absent_trade}"}
CONTEXT = "The user is on the Hoistra compliance console. No scope filter is applied."


def _rows(text_=None):
    return list(csv.DictReader(io.StringIO(text_ if text_ is not None else golden.compliance_bank_template_csv())))


@pytest.fixture(scope="module")
def rows():
    return {r["ID"]: r for r in _rows()}


def test_the_template_is_the_header_row_only():
    assert golden.template_csv() == ",".join(golden.TEMPLATE_HEADER) + "\r\n"
    assert golden.TEMPLATE_HEADER == ("ID", "Question", "Follow-ups", "Expected answer", "Evaluation",
                                      "Expected values", "Source", "Verified on", "Page/context", "Ask as",
                                      "Requirement IDs", "Notes")


def test_the_export_has_161_rows_without_the_four_that_write():
    out = _rows()
    assert len(out) == 161
    ids = [r["ID"] for r in out]
    assert ids == [f"CQB-{r['id']}" for r in json.loads(FROZEN.read_text(encoding="utf-8"))]
    assert len(set(ids)) == 161 and ids[0] == "CQB-A1" and ids[-1] == "CQB-L15"
    for gone in EXCLUDED:
        assert f"CQB-{gone}" not in ids
    assert list(out[0].keys()) == list(golden.TEMPLATE_HEADER)


def test_nothing_from_the_banks_answer_column_is_exported(rows):
    for r in rows.values():
        for col in ("Follow-ups", "Expected answer", "Expected values", "Source", "Verified on", "Ask as"):
            assert r[col] == "", (r["ID"], col)
        assert r["Page/context"] == CONTEXT
    text_ = golden.compliance_bank_template_csv()
    assert "2026-09-03" not in text_ and "[27 / 11]" not in text_


def test_questions_are_clean_and_the_37_entity_rows_use_the_closed_list(rows):
    with_ph = {i for i, r in rows.items() if golden.PLACEHOLDER.search(r["Question"])}
    assert len(with_ph) == 37
    for i, r in rows.items():
        assert "⚠" not in r["Question"] and r["Question"] == r["Question"].strip()
        assert set(golden.PLACEHOLDER.findall(r["Question"])) <= PLACEHOLDERS, i
    used = {p for r in rows.values() for p in golden.PLACEHOLDER.findall(r["Question"])}
    assert used == PLACEHOLDERS
    assert rows["CQB-E9"]["Question"] == "Certificates for {building}"
    assert rows["CQB-E4"]["Question"] == "{vendor_variant} — anything on file?"
    assert rows["CQB-G7"]["Question"] == "{building} or {building_2} — which is worse off?"
    assert rows["CQB-C11"]["Question"] == "Has the {building} {certificate_type} renewal been chased?"
    assert rows["CQB-E13"]["Question"] == "Certificates for {absent_building}"
    assert "not in your register" in rows["CQB-E13"]["Notes"] or "NOT in your register" in rows["CQB-E13"]["Notes"]
    assert rows["CQB-A1"]["Question"] == "Which certificates must a UK building owner hold?"


def test_evaluation_suggestions_105_objective_56_judgment(rows):
    counts = {k: sum(1 for r in rows.values() if r["Evaluation"] == k) for k in ("objective", "judgment")}
    assert counts == {"objective": 105, "judgment": 56}
    traps = [i for i, r in rows.items() if r["Notes"].find("Trap:") >= 0]
    assert len(traps) == 30 and all(rows[i]["Evaluation"] == "judgment" for i in traps)
    for i in ("A5", "B1", "C10", "E1", "H3", "F2"):
        assert rows[f"CQB-{i}"]["Evaluation"] == "objective", i
    for i in ("I1", "D8", "J1", "K1", "D12", "C18", "F12", "D6"):
        assert rows[f"CQB-{i}"]["Evaluation"] == "judgment", i


def test_requirement_ids_only_as_listed(rows):
    want = {"D11": "COMP-7", "L10": "COMP-7", "H1": "COMP-3, COMP-4", "H5": "COMP-1, COMP-4", "L8": "COMP-4",
            "A3": "COMP-5, COMP-6", "A4": "COMP-1, COMP-5", "A16": "COMP-2", "I7": "COMP-1, COMP-2",
            "C17": "COMP-1, GEN-1", "E13": "GEN-1", "E14": "GEN-1", "E20": "GEN-1", "D16": "GEN-1",
            "A18": "COMP-3", "G7": "COMP-3", "B1": "", "L1": "", "A1": "COMP-6", "F3": "COMP-1, COMP-5"}
    for i, ids in want.items():
        assert rows[f"CQB-{i}"]["Requirement IDs"] == ids, i
    assert sum(1 for r in rows.values() if "COMP-1" in r["Requirement IDs"]) == 18
    assert all(not r["Requirement IDs"] for i, r in rows.items() if i.split("-")[1] in ("B1", "B2"))
    for i in ("L3", "A7", "B12"):                                   # traps are never marked in this column
        assert "TRAP" not in rows[f"CQB-{i}"]["Requirement IDs"].upper()


def test_notes_follow_the_stated_format_and_name_nothing_internal(rows):
    for i, r in rows.items():
        parts = r["Notes"].split(" · ")
        assert 3 <= len(parts) <= 5, (i, r["Notes"])
        assert parts[1].startswith("Shape: "), i
        assert r["Notes"].count("Trap:") == (1 if "Trap:" in r["Notes"] else 0)
    assert rows["CQB-A1"]["Notes"].startswith("Pack question · Shape: law, ")
    assert rows["CQB-B1"]["Notes"].startswith("Register question · Shape: lifecycle, ")
    assert rows["CQB-E9"]["Notes"].startswith("Entity question · Shape: particular, ")
    assert "Replace {building}" in rows["CQB-E9"]["Notes"]
    assert "{building} or {building_2}" in rows["CQB-G7"]["Notes"] or "{building} and {building_2}" in rows["CQB-G7"]["Notes"]
    assert rows["CQB-L3"]["Notes"].endswith(
        "Trap: false premise; keep only if you have at least one blocked vendor; the answer must correct it with your count")
    assert rows["CQB-B12"]["Notes"].endswith("Trap: names a filter value; the answer must still list rows normally")
    assert "Time window" in rows["CQB-B1"]["Notes"] and "Time window" not in rows["CQB-A1"]["Notes"]
    assert sum(1 for r in rows.values() if "Time window" in r["Notes"]) == 21
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    names = {t for r in frozen for t in re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", r["tools"])} - {"building_2"}
    assert names, "the frozen data should carry tool names"
    exported = golden.compliance_bank_template_csv()
    in_questions = " ".join(r["Question"] for r in rows.values())     # F11 quotes risk_filter in the user's own words
    for n in names - set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", in_questions)):
        assert n not in exported, n
    for word in ("taxonomy", "answering", "recipes", "core.md", "doc_rag", "wo_engine", "prompt_doc", "skills/"):
        assert word not in exported, word


def test_the_export_is_excel_safe_and_repeatable():
    first = golden.compliance_bank_template_csv()
    assert first == golden.compliance_bank_template_csv()
    for r in _rows(first):
        for cell in r.values():
            assert cell[:1] not in ("=", "+", "-", "@", "\t", "\r")


def test_an_untouched_export_is_refused_row_by_row_and_a_filled_row_passes():
    data = golden.compliance_bank_template_csv().encode("utf-8")
    r = golden.parse_upload("t.csv", data, today=date(2026, 10, 15))
    assert r.cases == []
    missing = [e for e in r.errors if e["column"] == "Expected answer"]
    assert len(missing) == 161
    assert len([e for e in r.errors if e["column"] == "Question"]) == 37
    filled = golden.compliance_bank_template_csv().replace("\r\nCQB-B1,", "\r\nCQB-B1,", 1)
    rows = _rows(filled)
    rows = [row for row in rows if row["ID"] == "CQB-B1"]
    rows[0]["Expected answer"] = "Two certificates are current."
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(golden.TEMPLATE_HEADER))
    w.writeheader()
    w.writerows(rows)
    ok = golden.parse_upload("one.csv", buf.getvalue().encode("utf-8"), today=date(2026, 10, 15))
    assert ok.errors == [] and ok.cases[0].case_key == "CQB-B1" and ok.cases[0].evaluation == "objective"


def test_a_custom_frozen_file_is_not_needed_at_runtime_and_the_bank_is_never_read():
    src = Path(golden.__file__).read_text(encoding="utf-8")
    for banned in ("skills", "prompt_doc", "questions.md", "BANK_PATH"):
        assert banned not in src, banned
    assert not hasattr(golden, "SEED_VALUES") and not hasattr(golden, "compliance_bank_seed_csv")


def test_csv_safe():
    for bad in ("=1", "+1", "-1", "@x", "\tx"):
        assert golden.csv_safe(bad) == "'" + bad
    assert golden.csv_safe("fine") == "fine" and golden.csv_safe(None) == ""


# --- drift: the bank may change; the frozen file must be regenerated when it does ----------------------------

def test_the_frozen_ids_and_questions_still_match_the_bank():
    bank = {r["id"]: r["bank_question"] for r in bank_rows(BANK.read_text(encoding="utf-8"))}
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    assert [r["id"] for r in frozen] == [i for i in bank if i not in EXCLUDED], \
        "the bank's rows changed: run tests/compliance_bank_generator.py and review the diff"
    for r in frozen:
        assert r["bank_question"] == bank[r["id"]], f"{r['id']}: the bank's question changed"


def test_the_frozen_file_is_what_the_generator_makes():
    assert json.loads(FROZEN.read_text(encoding="utf-8")) == build(BANK.read_text(encoding="utf-8"))
