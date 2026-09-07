#!/usr/bin/env python3
"""Download / extract public register dumps into data/verification_dumps.

Produces CCC-ready CSVs with lookup_key for data_dump channel codes.
"""
from __future__ import annotations

import csv
import io
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "verification_dumps"
TESTDATA = ROOT / "testdata" / "verification_dumps"

UA = {"User-Agent": "Plenum-CAFM-verification-dump-fetcher/1.0"}


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._in_cell = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._in_cell and self._row is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell or []).strip())
            self._row.append(text)
            self._cell = None
            self._in_cell = False
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(c.strip() for c in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._in_cell and self._cell is not None:
            self._cell.append(data)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {path.name}: {len(rows)} rows")


def fetch(url: str) -> bytes:
    with httpx.Client(timeout=60.0, follow_redirects=True, headers=UA) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.content


def parse_tables(html: str) -> list[list[list[str]]]:
    p = TableParser()
    p.feed(html)
    return p.tables


def build_sia_from_html(html: str) -> list[dict[str, str]]:
    rows_out: list[dict[str, str]] = []
    for table in parse_tables(html):
        if len(table) < 2:
            continue
        header = [h.lower() for h in table[0]]
        # SIA register: Contractor | Activities | Expiry
        if not any("contractor" in h or "company" in h for h in header):
            # some pages have no header — detect by 3 cols
            if len(table[0]) < 2:
                continue
            start = 0
            if "contractor" in table[0][0].lower():
                start = 1
            for r in table[start:]:
                if len(r) < 2:
                    continue
                name = r[0].strip()
                if not name or name.lower().startswith("contractor"):
                    continue
                rows_out.append(
                    {
                        "lookup_key": name.upper()[:255],
                        "company_name": name,
                        "activities": r[1] if len(r) > 1 else "",
                        "expiry_date": r[2] if len(r) > 2 else "",
                        "status": "approved",
                        "source": "sia_acs_register_html",
                    }
                )
            continue
        name_i = next((i for i, h in enumerate(header) if "contractor" in h or "company" in h), 0)
        act_i = next((i for i, h in enumerate(header) if "activ" in h or "sector" in h), None)
        exp_i = next((i for i, h in enumerate(header) if "expir" in h), None)
        for r in table[1:]:
            if len(r) <= name_i:
                continue
            name = r[name_i].strip()
            if not name:
                continue
            rows_out.append(
                {
                    "lookup_key": name.upper()[:255],
                    "company_name": name,
                    "activities": r[act_i] if act_i is not None and len(r) > act_i else "",
                    "expiry_date": r[exp_i] if exp_i is not None and len(r) > exp_i else "",
                    "status": "approved",
                    "source": "sia_acs_register_html",
                }
            )
    # dedupe by lookup_key
    seen: set[str] = set()
    deduped = []
    for r in rows_out:
        k = r["lookup_key"]
        if k in seen:
            continue
        seen.add(k)
        deduped.append(r)
    return deduped


def build_sia_from_govuk_csv(raw: bytes) -> list[dict[str, str]]:
    text = raw.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for r in reader:
        name = (
            r.get("Contractor")
            or r.get("Company Name")
            or r.get("Organisation")
            or r.get("Name")
            or ""
        ).strip()
        if not name:
            # first column fallback
            name = next((v for v in r.values() if v and v.strip()), "").strip()
        if not name:
            continue
        out.append(
            {
                "lookup_key": name.upper()[:255],
                "company_name": name,
                "activities": r.get("Activities") or r.get("Sectors") or "",
                "expiry_date": r.get("Expiry date") or r.get("Expiry") or "",
                "status": "approved",
                "source": "govuk_foi_2022_snapshot",
            }
        )
    return out


def build_hse_from_html(html: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for table in parse_tables(html):
        if len(table) < 2:
            continue
        header = [h.lower() for h in table[0]]
        if not any("licence" in h or "license" in h for h in header):
            continue
        if not any("company" in h for h in header):
            continue
        name_i = next(i for i, h in enumerate(header) if "company" in h)
        lic_i = next((i for i, h in enumerate(header) if "licence number" in h or "license number" in h or h == "licence"), None)
        if lic_i is None:
            lic_i = next((i for i, h in enumerate(header) if "licence" in h or "license" in h), None)
        exp_i = next((i for i, h in enumerate(header) if "expir" in h), None)
        type_i = next((i for i, h in enumerate(header) if "type" in h), None)
        addr_i = next((i for i, h in enumerate(header) if "address" in h), None)
        for r in table[1:]:
            if len(r) <= name_i:
                continue
            name = r[name_i].strip()
            lic = r[lic_i].strip() if lic_i is not None and len(r) > lic_i else ""
            if not name and not lic:
                continue
            key = (lic or name).upper()[:255]
            out.append(
                {
                    "lookup_key": key,
                    "licence_number": lic,
                    "company_name": name,
                    "address": r[addr_i] if addr_i is not None and len(r) > addr_i else "",
                    "expiry_date": r[exp_i] if exp_i is not None and len(r) > exp_i else "",
                    "licence_type": r[type_i] if type_i is not None and len(r) > type_i else "",
                    "status": "active",
                    "source": "ukata_hse_list",
                }
            )
    seen: set[str] = set()
    deduped = []
    for r in out:
        if r["lookup_key"] in seen:
            continue
        seen.add(r["lookup_key"])
        deduped.append(r)
    return deduped


def try_coniac_excel() -> list[dict[str, str]]:
    """Find and parse CONIAC Excel if openpyxl available."""
    page = fetch("https://www.coniac.org.uk/resources/current-licensed-asbestos-contractors").decode(
        "utf-8", errors="replace"
    )
    candidates = re.findall(
        r'href=["\']([^"\']+\.(?:xlsx?|xls|csv))["\']',
        page,
        flags=re.I,
    )
    candidates += re.findall(r'https?://[^"\'\s<>]+\.(?:xlsx?|xls|csv)', page, flags=re.I)
    # WordPress media patterns
    candidates += re.findall(
        r'https?://www\.coniac\.org\.uk/[^"\'\s<>]+(?:xlsx?|xls|csv)',
        page,
        flags=re.I,
    )
    abs_urls = []
    for c in candidates:
        if c.startswith("http"):
            abs_urls.append(c)
        elif c.startswith("/"):
            abs_urls.append("https://www.coniac.org.uk" + c)
        else:
            abs_urls.append("https://www.coniac.org.uk/" + c.lstrip("./"))
    abs_urls = list(dict.fromkeys(abs_urls))
    print("CONIAC candidates:", abs_urls[:10])
    if not abs_urls:
        return []
    try:
        import openpyxl  # type: ignore
    except ImportError:
        print("openpyxl not installed — skip CONIAC xlsx parse")
        # still download first excel for manual use
        raw = fetch(abs_urls[0])
        (OUT / "_coniac_licensed_asbestos.xlsx").write_bytes(raw)
        print(f"Saved raw CONIAC excel ({len(raw)} bytes)")
        return []

    raw = fetch(abs_urls[0])
    (OUT / "_coniac_licensed_asbestos.xlsx").write_bytes(raw)
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = [str(c or "").strip().lower() for c in next(rows_iter)]
    name_i = next((i for i, h in enumerate(header) if "company" in h or "name" in h), 0)
    lic_i = next((i for i, h in enumerate(header) if "licence" in h or "license" in h), None)
    exp_i = next((i for i, h in enumerate(header) if "expir" in h), None)
    out = []
    for row in rows_iter:
        vals = [str(c).strip() if c is not None else "" for c in row]
        if len(vals) <= name_i:
            continue
        name = vals[name_i]
        lic = vals[lic_i] if lic_i is not None and len(vals) > lic_i else ""
        if not name and not lic:
            continue
        key = (lic or name).upper()[:255]
        out.append(
            {
                "lookup_key": key,
                "licence_number": lic,
                "company_name": name,
                "expiry_date": vals[exp_i] if exp_i is not None and len(vals) > exp_i else "",
                "status": "active",
                "source": "coniac_excel",
            }
        )
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    # --- SIA ACS ---
    sia_rows: list[dict[str, str]] = []
    try:
        html = fetch("https://www.services.sia.homeoffice.gov.uk/Pages/acs-roac.aspx?all=").decode(
            "utf-8", errors="replace"
        )
        (OUT / "_sia_raw.html").write_text(html, encoding="utf-8")
        sia_rows = build_sia_from_html(html)
        print(f"SIA HTML rows: {len(sia_rows)}")
    except Exception as exc:  # noqa: BLE001
        print("SIA HTML fetch failed:", exc)

    if len(sia_rows) < 10:
        try:
            csv_bytes = fetch(
                "https://assets.publishing.service.gov.uk/media/622b2e8ad3bf7f5a89aece95/sia-acs-08-March-2022.csv"
            )
            sia_rows = build_sia_from_govuk_csv(csv_bytes)
            print(f"SIA GOV.UK snapshot rows: {len(sia_rows)}")
        except Exception as exc:  # noqa: BLE001
            print("SIA CSV fallback failed:", exc)

    if sia_rows:
        write_csv(
            OUT / "SIA_ACS.csv",
            ["lookup_key", "company_name", "activities", "expiry_date", "status", "source"],
            sia_rows,
        )

    # --- HSE asbestos ---
    hse_rows: list[dict[str, str]] = []
    try:
        hse_rows = try_coniac_excel()
        print(f"CONIAC excel rows: {len(hse_rows)}")
    except Exception as exc:  # noqa: BLE001
        print("CONIAC failed:", exc)

    if len(hse_rows) < 10:
        try:
            html = fetch(
                "https://www.ukata.org.uk/library/hse-licensed-asbestos-removal-contractors-register/"
            ).decode("utf-8", errors="replace")
            (OUT / "_hse_ukata.html").write_text(html, encoding="utf-8")
            hse_rows = build_hse_from_html(html)
            print(f"UKATA HSE rows: {len(hse_rows)}")
        except Exception as exc:  # noqa: BLE001
            print("UKATA HSE failed:", exc)

    if hse_rows:
        fields = [
            "lookup_key",
            "licence_number",
            "company_name",
            "address",
            "expiry_date",
            "licence_type",
            "status",
            "source",
        ]
        write_csv(OUT / "ASBESTOS_LICENCE.csv", fields, hse_rows)

    # --- UKAS / BPCA / BASIS: no open bulk dump — leave marker READMEs ---
    note = (
        "# No public bulk dump available via open download.\n"
        "# Populate manually from register search exports when available.\n"
        "# Until then verify falls to dump_miss → class 3 human verification.\n"
    )
    for name in (
        "UKAS_ASBESTOS.csv",
        "ISO_9001.csv",
        "ISO_14001.csv",
        "ASBESTOS_SURVEY.csv",
        "BPCA_MEMBER.csv",
        "PESTICIDE_PAx.csv",
    ):
        # Keep existing sample if present in testdata; copy sample as seed if OUT empty
        target = OUT / name
        sample = TESTDATA / name
        if not target.exists() and sample.exists():
            target.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"Seeded {name} from testdata sample (not live register)")
        elif not target.exists():
            # write empty header stub
            write_csv(target, ["lookup_key", "company_name", "status", "notes"], [])
            (OUT / f"{name}.NOTE.txt").write_text(note, encoding="utf-8")

    # Also copy live dumps into testdata for smoke convenience
    for f in ("SIA_ACS.csv", "ASBESTOS_LICENCE.csv"):
        src = OUT / f
        if src.exists() and src.stat().st_size > 100:
            (TESTDATA / f).write_bytes(src.read_bytes())
            print(f"Synced {f} -> testdata/")

    print("Done. Dump dir:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
