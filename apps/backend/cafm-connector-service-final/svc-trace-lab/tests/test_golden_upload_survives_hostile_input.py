"""A crafted workbook is refused or read as plain text. It never hangs the reader, never leaks a raw
ValueError/OverflowError/RecursionError/LookupError/NotImplementedError, and never gets a DOCTYPE past the check
inside a UTF-16 part. Numbers read at the precision Excel shows (15 significant digits), not the 17 digits a
double is stored with.
Added in review of Task 4 (15 Oct 2026, fix round 1 the same day): each case below was found by probing the
reader with a crafted or damaged file. Spec: Golden set §1.4, §1.5."""
from __future__ import annotations

import io
import zipfile

import pytest

from trace_lab import golden
from golden_xlsx import make_xlsx, num

SST = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _with_part(data: bytes, name: str, change) -> bytes:
    """The same workbook with one part rewritten (change takes and returns that part's text)."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            raw = zin.read(info.filename)
            if info.filename == name:
                raw = change(raw.decode("utf-8")).encode("utf-8")
            zout.writestr(info.filename, raw)
    return out.getvalue()


def _refused(data: bytes) -> str:
    with pytest.raises(golden.UploadRefused) as err:
        golden.read_tables("set.xlsx", data)
    assert err.value.reason == "bad_file"
    return str(err.value)


def _cell(data: bytes, row: int = 2) -> str:
    _, tables = golden.read_tables("set.xlsx", data)
    return dict(tables[0][1])[row][0]


# ── numbers that would hang or crash the reader ───────────────────────────────────────

def test_a_number_with_a_huge_exponent_is_kept_as_text_instead_of_expanding():
    assert _cell(make_xlsx([["a"], [num("1E+999999999")]])) == "1E+999999999"


def test_a_number_with_a_tiny_exponent_is_kept_as_text_instead_of_expanding():
    assert _cell(make_xlsx([["a"], [num("1E-999999999")]])) == "1E-999999999"


def test_a_number_past_the_range_of_a_double_is_kept_as_text():
    assert _cell(make_xlsx([["a"], [num("1E+5000")]])) == "1E+5000"


def test_a_number_written_over_64_characters_is_kept_as_text():
    text = "7" * 5000
    assert _cell(make_xlsx([["a"], [num(text)]])) == text


def test_the_largest_double_still_reads_as_a_whole_number():
    assert _cell(make_xlsx([["a"], [num("1E+300")]])) == "1" + "0" * 300


@pytest.mark.parametrize("serial", ["NaN", "Infinity", "-Infinity", "99999999999", "-99999999999", "1E+400"])
def test_a_date_serial_that_is_not_a_date_is_kept_as_text(serial):
    assert _cell(make_xlsx([["a"], [num(serial, style=1)]], xfs=(0, 14))) == serial


def test_a_non_finite_plain_number_is_kept_as_text():
    assert _cell(make_xlsx([["a"], [num("NaN")]])) == "NaN"


# ── attributes and nesting that raised a bare exception ───────────────────────────────

def test_a_non_numeric_cell_style_is_refused_not_raised():
    base = make_xlsx([["a"], [num(46308, style=1)]], xfs=(0, 14))
    assert "not valid" in _refused(_with_part(base, "xl/worksheets/sheet1.xml", lambda s: s.replace('s="1"', 's="abc"')))


def test_a_non_numeric_row_number_is_refused_not_raised():
    base = make_xlsx([["a"], ["b"]])
    assert "not valid" in _refused(_with_part(base, "xl/worksheets/sheet1.xml",
                                              lambda s: s.replace('<row r="2">', '<row r="x">')))


def test_a_non_numeric_number_format_id_is_refused_not_raised():
    base = make_xlsx([["a"], [num(46308, style=1)]], xfs=(0, 14))
    assert "not valid" in _refused(_with_part(base, "xl/styles.xml", lambda s: s.replace('numFmtId="14"', 'numFmtId="x"')))


def test_a_part_declaring_an_encoding_the_parser_cannot_read_is_refused_not_raised():
    part = f'<?xml version="1.0" encoding="utf-16-le"?><sst xmlns="{SST}"/>'.encode("utf-16-le")
    assert "not valid" in _refused(make_xlsx([["a"]], shared_override=part))


def test_a_deeply_nested_shared_string_is_refused_not_raised():
    deep = (f'<?xml version="1.0"?><sst xmlns="{SST}"><si>' + "<r>" * 1100 + "<t>x</t>" + "</r>" * 1100 + "</si></sst>")
    assert "not valid" in _refused(make_xlsx([["a"]], shared_override=deep))


# ── an encoding the parser has no codec for, and a zip version the library does not implement ────────────

def _declare(encoding: str):
    """Rewrite a part's XML declaration to name `encoding` (every part make_xlsx writes starts with UTF-8)."""
    def change(text: str) -> str:
        assert 'encoding="UTF-8"' in text
        return text.replace('encoding="UTF-8"', f'encoding="{encoding}"', 1)
    return change


@pytest.mark.parametrize("encoding", ["no-such-codec", "UTF-9", "rot13", "base64", "hex"])
@pytest.mark.parametrize("part", ["xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/sharedStrings.xml",
                                  "xl/styles.xml", "xl/worksheets/sheet1.xml"])
def test_a_part_declaring_an_encoding_python_has_no_codec_for_is_refused_not_raised(part, encoding):
    base = make_xlsx([["a"], [num(46308, style=1)]], xfs=(0, 14))
    message = _refused(_with_part(base, part, _declare(encoding)))
    assert "not valid XML" in message


def _set_version_needed(data: bytes, version: int, *, first_only: bool = False) -> bytes:
    """Set 'version needed to extract' (byte 6 of a central directory header) on every entry, or only the first."""
    b, i = bytearray(data), 0
    while (i := b.find(b"PK\x01\x02", i)) != -1:
        b[i + 6] = version
        if first_only:
            break
        i += 4
    return bytes(b)


@pytest.mark.parametrize("first_only", [False, True])
def test_a_zip_entry_needing_a_version_the_library_does_not_implement_is_refused_not_raised(first_only):
    base = make_xlsx([["a"], ["b"]])
    message = _refused(_set_version_needed(base, 200, first_only=first_only))
    assert "not a readable .xlsx" in message


def test_the_version_needed_edit_leaves_a_workbook_that_otherwise_reads():
    # Guards the helper: 20 is what every writer uses, so this file must read exactly as the untouched one.
    base = make_xlsx([["a"], ["b"]])
    assert _cell(_set_version_needed(base, 20)) == "b"


# ── numbers read at the precision Excel displays ──────────────────────────────────────────────────────

@pytest.mark.parametrize("stored, shown", [
    ("2.2999999999999998", "2.3"),              # a typed 2.3 written with 17 significant digits
    ("0.30000000000000004", "0.3"),             # a cached =0.1+0.2
    ("79.400000000000006", "79.4"),
    ("1234567.8900000001", "1234567.89"),
    ("-0.30000000000000004", "-0.3"),
    ("1.00000000000000000", "1"),               # trailing zeros are digits too: still a whole number
    ("22.000000000000004", "22"),
    ("0.1234567890123456789", "0.123456789012346"),   # Excel shows 15 significant digits and no more
])
def test_a_number_is_read_at_15_significant_digits(stored, shown):
    assert _cell(make_xlsx([["a"], [num(stored)]])) == shown


def test_a_percent_stored_with_17_digits_reads_as_the_percent_the_admin_sees():
    assert _cell(make_xlsx([["a"], [num("0.79400000000000004", style=1)]], xfs=(0, 10))) == "79.4%"


def test_a_percent_that_is_a_float_sum_reads_without_the_noise():
    assert _cell(make_xlsx([["a"], [num("0.070000000000000007", style=1)]], xfs=(0, 9))) == "7%"


def test_a_date_serial_stored_with_17_digits_still_reads_as_its_date():
    assert _cell(make_xlsx([["a"], [num("46308.000000000007", style=1)]], xfs=(0, 14))) == "2026-10-13"


def test_a_number_with_16_digits_that_does_not_overflow_a_double_is_read_not_kept_as_text():
    assert _cell(make_xlsx([["a"], [num("1.234567890123456E+300")]])) == "1234567890123460" + "0" * 285


@pytest.mark.parametrize("stored", ["1.2345678901234567E+400", "1.2345678901234567E-400", "-1.2345678901234567E+400"])
def test_a_long_number_outside_the_range_of_a_double_is_kept_as_text(stored):
    assert _cell(make_xlsx([["a"], [num(stored)]])) == stored


@pytest.mark.parametrize("stored", ["79.45", "0.1", "123456789012345", "0.000000000000001", "-5.25", "1E+300"])
def test_a_number_with_15_digits_or_fewer_reads_exactly_as_stored(stored):
    expected = {"1E+300": "1" + "0" * 300, "0.000000000000001": "0.000000000000001"}.get(stored, stored)
    assert _cell(make_xlsx([["a"], [num(stored)]])) == expected


# ── a DOCTYPE in a part the parser reads without a byte order mark ────────────────────

_DTD = '<!DOCTYPE x [<!ENTITY a "b">]>'
_BODY = f'<sst xmlns="{SST}"><si><t>Question</t></si><si><t>&a;</t></si></sst>'


@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be"])
@pytest.mark.parametrize("declared", [False, True])
def test_a_dtd_in_utf16_without_a_byte_order_mark_is_refused(codec, declared):
    head = '<?xml version="1.0" encoding="UTF-16"?>' if declared else ""
    part = (head + _DTD + _BODY).encode(codec)
    assert "DOCTYPE or ENTITY" in _refused(make_xlsx([["Question", "x"]], shared_override=part))


@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be"])
def test_a_clean_utf16_part_with_a_byte_order_mark_still_reads(codec):
    bom = b"\xff\xfe" if codec.endswith("le") else b"\xfe\xff"
    part = bom + f'<sst xmlns="{SST}"><si><t>Question</t></si></sst>'.encode(codec)
    _, tables = golden.read_tables("set.xlsx", make_xlsx([["Question"]], shared_override=part))
    assert tables[0][1][0] == (1, {0: "Question"})
