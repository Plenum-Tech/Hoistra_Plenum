"""An uploaded workbook is refused before any row is read when it is the wrong type, encrypted, a zip bomb,
too large, or carries a DOCTYPE/ENTITY declaration. 15 Oct 2026. Spec: Golden set §1.1, §1.5."""
from __future__ import annotations

import pytest

from trace_lab import golden
from golden_xlsx import lie_about_size, make_xlsx, set_encrypted_flag

GOOD = make_xlsx([["Question", "Expected answer"], ["How many floors?", "22"]])


def _refused(name, data, reason="bad_file"):
    with pytest.raises(golden.UploadRefused) as err:
        golden.read_tables(name, data)
    assert err.value.reason == reason
    return str(err.value)


def test_a_good_workbook_reads():
    kind, tables = golden.read_tables("set.xlsx", GOOD)
    assert kind == "xlsx" and tables[0][1][0] == (1, {0: "Question", 1: "Expected answer"})


@pytest.mark.parametrize("name", ["set.xls", "set.xlsm", "set.ods", "set.txt", "set"])
def test_other_types_are_refused_with_the_save_as_hint(name):
    assert "saved as .xlsx" in _refused(name, GOOD)


def test_an_ole2_file_named_xlsx_is_refused_as_encrypted_or_old():
    assert "without a password" in _refused("set.xlsx", b"\xd0\xcf\x11\xe0" + b"\0" * 600)


def test_an_entry_with_the_encrypted_bit_is_refused():
    assert "without a password" in _refused("set.xlsx", set_encrypted_flag(GOOD))


@pytest.mark.parametrize("decl", ["<!DOCTYPE x>", "<!doctype x>", "<!DocType x>", '<!ENTITY a "b">', '<!entity a "b">'])
def test_a_dtd_declaration_in_any_part_and_any_case_is_refused(decl):
    shared = ('<?xml version="1.0"?>' + decl + '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<si><t>Question</t></si><si><t>Expected answer</t></si></sst>')
    data = make_xlsx([["Question", "Expected answer"]], shared_override=shared)
    assert "DOCTYPE or ENTITY" in _refused("set.xlsx", data)


def test_a_part_that_unpacks_to_over_200_times_its_size_is_a_zip_bomb():
    data = make_xlsx([["Question", "Expected answer"], ["x" * 3_000_000, "y"]])
    assert "zip bomb" in _refused("set.xlsx", data)


def test_a_part_over_the_entry_cap_is_refused_while_streaming(monkeypatch):
    monkeypatch.setattr(golden, "MAX_ENTRY_BYTES", 2000)
    monkeypatch.setattr(golden, "MAX_RATIO", 10 ** 9)
    data = make_xlsx([["Question", "Expected answer"]] + [[f"question {i}", f"answer {i}"] for i in range(200)])
    assert "too large to read safely" in _refused("set.xlsx", data)


def test_a_lying_declared_size_is_refused_as_damaged():
    assert "damaged" in _refused("set.xlsx", lie_about_size(GOOD, 12))


def test_more_than_500_parts_are_refused():
    data = make_xlsx([["Question", "Expected answer"]], extra={f"xl/media/p{i}.bin": b"x" for i in range(500)})
    assert "too many parts" in _refused("set.xlsx", data)


def test_a_macro_workbook_renamed_xlsx_is_refused():
    assert "saved as .xlsx" in _refused("set.xlsx", make_xlsx([["Question", "Expected answer"]], macro=True))


def test_a_file_over_the_byte_cap_is_too_large():
    _refused("set.csv", b"x" * (golden.MAX_BYTES + 1), reason="file_too_large")


def test_not_a_zip_is_refused():
    assert "not a readable .xlsx" in _refused("set.xlsx", b"PK\x03\x04garbage")
