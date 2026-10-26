"""Small .xlsx files built in memory for the golden-set tests (no spreadsheet library, nothing on disk)."""
from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
RNS = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


class Cell:
    def __init__(self, value=None, *, style=0, formula=None, kind="n"):
        self.value, self.style, self.formula, self.kind = value, style, formula, kind


def num(v, style=0):
    return Cell(v, style=style, kind="n")


def formula(f, cached=None, style=0):
    return Cell(cached, style=style, formula=f, kind="str" if isinstance(cached, str) else "n")


def error(code="#N/A"):
    return Cell(code, kind="e")


def boolean(v):
    return Cell("1" if v else "0", kind="b")


def inline(text):
    return Cell(text, kind="inlineStr")


def _col(i: int) -> str:
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _sheet(rows, row_numbers, strings) -> str:
    out = []
    for idx, row in enumerate(rows):
        r = row_numbers[idx] if row_numbers else idx + 1
        cells = []
        for ci, v in enumerate(row):
            if v is None:
                continue
            ref = f"{_col(ci)}{r}"
            if isinstance(v, str):
                if v not in strings:
                    strings.append(v)
                cells.append(f'<c r="{ref}" t="s"><v>{strings.index(v)}</v></c>')
            elif isinstance(v, bool):
                cells.append(f'<c r="{ref}" t="b"><v>{1 if v else 0}</v></c>')
            elif isinstance(v, (int, float)):
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
            else:
                s = f' s="{v.style}"' if v.style else ""
                f = f"<f>{escape(v.formula)}</f>" if v.formula else ""
                if v.kind == "inlineStr":
                    cells.append(f'<c r="{ref}" t="inlineStr"{s}><is><t>{escape(str(v.value))}</t></is></c>')
                else:
                    t = "" if v.kind == "n" else f' t="{v.kind}"'
                    val = "" if v.value is None else f"<v>{escape(str(v.value))}</v>"
                    cells.append(f'<c r="{ref}"{t}{s}>{f}{val}</c>')
        out.append(f'<row r="{r}">' + "".join(cells) + "</row>")
    return f"{HEAD}<worksheet {NS}><sheetData>" + "".join(out) + "</sheetData></worksheet>"


def make_xlsx(sheets, *, hidden=(), row_numbers=None, date1904=False, xfs=(0,), custom_formats=None,
              extra=None, macro=False, shared_override=None) -> bytes:
    """sheets: a list of rows (one sheet named Sheet1) or a list of (name, rows). A str cell is a shared
    string, an int/float a number, None a gap; tests/golden_xlsx.Cell for styles, formulas, errors."""
    if sheets and not isinstance(sheets[0], tuple):
        sheets = [("Sheet1", sheets)]
    strings: list[str] = []
    parts = [_sheet(rows, (row_numbers or {}).get(name), strings) for name, rows in sheets]
    wb_pr = '<workbookPr date1904="1"/>' if date1904 else ""
    tags = []
    for i, (name, _) in enumerate(sheets):
        state = ' state="hidden"' if name in hidden else ""
        tags.append(f'<sheet name="{escape(name)}" sheetId="{i + 1}"{state} r:id="rId{i + 1}"/>')
    sheet_tags = "".join(tags)
    workbook = f"{HEAD}<workbook {NS} {RNS}>{wb_pr}<sheets>{sheet_tags}</sheets></workbook>"
    n = len(sheets)
    rels = (f'{HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                      f'relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(n))
            + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
              f'relationships/sharedStrings" Target="sharedStrings.xml"/>'
            + f'<Relationship Id="rId{n + 2}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
              f'relationships/styles" Target="styles.xml"/></Relationships>')
    main = ("application/vnd.ms-excel.sheet.macroEnabled.main+xml" if macro
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
    types = (f'{HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             f'<Override PartName="/xl/workbook.xml" ContentType="{main}"/></Types>')
    shared = shared_override or (f'{HEAD}<sst {NS} count="{len(strings)}" uniqueCount="{len(strings)}">'
                                 + "".join(f'<si><t xml:space="preserve">{escape(s)}</t></si>' for s in strings)
                                 + "</sst>")
    fmts = "".join(f'<numFmt numFmtId="{k}" formatCode="{escape(v, {chr(34): "&quot;"})}"/>'
                   for k, v in (custom_formats or {}).items())
    styles = (f"{HEAD}<styleSheet {NS}>" + (f'<numFmts count="{len(custom_formats)}">{fmts}</numFmts>' if fmts else "")
              + f'<cellXfs count="{len(xfs)}">' + "".join(f'<xf numFmtId="{x}"/>' for x in xfs)
              + "</cellXfs></styleSheet>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/sharedStrings.xml", shared)
        z.writestr("xl/styles.xml", styles)
        for i, p in enumerate(parts):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", p)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def lie_about_size(data: bytes, new_size: int) -> bytes:
    """Rewrite every declared uncompressed size (local headers and central directory) to new_size."""
    b, i = bytearray(data), 0
    while (i := b.find(b"PK\x03\x04", i)) != -1:
        b[i + 22:i + 26] = new_size.to_bytes(4, "little")
        i += 4
    i = 0
    while (i := b.find(b"PK\x01\x02", i)) != -1:
        b[i + 24:i + 28] = new_size.to_bytes(4, "little")
        i += 4
    return bytes(b)


def set_encrypted_flag(data: bytes) -> bytes:
    """Set general-purpose bit 0 (encrypted) on every entry, as a password-protected zip carries it."""
    b, i = bytearray(data), 0
    while (i := b.find(b"PK\x03\x04", i)) != -1:
        b[i + 6] |= 0x01
        i += 4
    i = 0
    while (i := b.find(b"PK\x01\x02", i)) != -1:
        b[i + 8] |= 0x01
        i += 4
    return bytes(b)
