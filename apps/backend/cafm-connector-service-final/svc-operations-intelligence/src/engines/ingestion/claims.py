"""What the document says about where it belongs.

Before a document can be checked against a building, it has to be read for the things that
identify one: the property it names, the country and place, the vendor whose paper it is,
the assets and plant it lists, references that already exist somewhere in the register.
These are *claims* — what the document asserts, not what is true — and the validator's job
is to hold them against what the building says about itself.

Claims come from three places, in order of authority:

1. **Fields an extractor already read** (compliance extraction, contract extraction). These
   are labelled — `building_name`, `vendor_name`, `country_code` — so they are the
   strongest: someone's parser said "this field is the property".
2. **The document text**, read with patterns that require a label ("Site:", "Property:",
   "Address:") before they will claim anything. An unlabelled string in a contract is as
   likely to be a law firm's address as the building's.
3. **The file name**, weakest of all and used only to notice a building name that the text
   never stated.

Nothing here guesses. A claim absent is reported absent, because "the document does not say"
and "the document disagrees" are different answers and the second one must never be invented.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..energy.buildings import country_code_for
from .ontology import normalise, tokens

#: Labels that introduce the property a document is about. The label is required: a bare
#: name in a contract is as often the counterparty's address as the site's.
_BUILDING_LABELS = (
    r"(?:building|property|premises|site|location|asset location|place of work|"
    r"site name|building name|property name|premises address|site address)"
)
# The value stops at the end of its sentence as well as at a separator: "Site: Raffles
# Link. Registered in Singapore." claims Raffles Link, not the sentence after it.
_BUILDING_RE = re.compile(
    _BUILDING_LABELS + r"\s*(?:name)?\s*[:\-\u2013]\s*"
    r"([A-Z0-9][^\n;,|]{2,60}?)(?=\.\s|\.$|[\n;,|]|$)",
    re.IGNORECASE)


_CODE_RE = re.compile(
    r"(?:building|site|property)\s*(?:code|ref|reference|id|no\.?|number)\s*[:\-–]\s*"
    r"([A-Z0-9][A-Z0-9\-/_]{1,20})", re.IGNORECASE)
_POSTCODE_UK = re.compile(r"\b([A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2})\b")
_POSTCODE_US = re.compile(r"\b([0-9]{5}(?:-[0-9]{4})?)\b")
_VENDOR_RE = re.compile(
    r"(?:vendor|contractor|supplier|service provider|company name|issued to|"
    r"certificate holder|approved contractor|maintained by|performed by|on behalf of)"
    r"\s*[:\-–]\s*([A-Z0-9][^\n;|]{2,70})", re.IGNORECASE)
#: A company suffix is what separates a firm from a phrase.
_COMPANY_SUFFIX = re.compile(
    r"\b([A-Z][A-Za-z0-9&'\-. ]{2,48}?\s(?:Ltd|Limited|LLP|PLC|Plc|LLC|Inc\.?|GmbH|Pte\.? Ltd|"
    r"Services|Group|Engineering|Maintenance|Facilities|Solutions|Contracts?)\.?)\b")
_ASSET_RE = re.compile(r"\b([A-Z]{1,5}-[A-Z0-9]{1,8}-[A-Z0-9]{1,8}(?:-[A-Z0-9]{1,6})?)\b")
_CONTRACT_RE = re.compile(
    r"(?:contract|agreement|po|purchase order)\s*(?:ref|reference|no\.?|number|id)\s*[:\-–]\s*"
    r"([A-Z0-9][A-Z0-9\-/_]{2,24})", re.IGNORECASE)
_COUNTRY_RE = re.compile(
    r"\b(United Kingdom|Great Britain|England|Scotland|Wales|Northern Ireland|"
    r"United States|United States of America|USA|U\.S\.A\.|"
    r"United Arab Emirates|UAE|Dubai|Abu Dhabi|Sharjah|Ajman|Fujairah|Ras Al Khaimah|"
    r"Umm Al Quwain|Singapore)\b", re.IGNORECASE)

#: Extractor field names that already carry a claim, per kind.
_FIELD_SOURCES: dict[str, tuple[str, ...]] = {
    "buildings": ("building_name", "building", "site_name", "property", "premises",
                  "Property address", "site", "location"),
    "codes": ("building_reference", "building_code", "site_code", "site_ref", "property_ref"),
    "vendors": ("vendor_name", "company_name", "contractor", "supplier", "Company name",
                "certificate_holder", "issued_to"),
    "countries": ("country", "country_code"),
    "regions": ("region", "city", "town"),
    "states": ("state", "province", "emirate"),
    "postcodes": ("postcode", "post_code", "zip", "zip_code"),
    "assets": ("asset_code", "asset", "equipment", "plant_ref", "serial_number"),
    "contract_refs": ("contract_ref", "contract_number", "agreement_ref", "po_number"),
    "certificate_numbers": ("certificate_number", "certificate_ref", "EPC ref no."),
}


@dataclass
class Claims:
    buildings: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    vendors: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)     # ISO-ish codes
    country_names: list[str] = field(default_factory=list)  # as written
    regions: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    postcodes: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    contract_refs: list[str] = field(default_factory=list)
    certificate_numbers: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)   # claim → where it came from

    def authored_buildings(self) -> list[str]:
        """Building names the DOCUMENT states - everything except the file name's guess.

        Only these can contradict a building. A file name can agree with one, and can
        point at a better candidate, but "scan001.pdf" filed against Riverside Court is
        not the document saying it belongs somewhere else.
        """
        return [b for b in self.buildings if self.sources.get(b) != "filename"]

    def is_empty(self) -> bool:
        return not any((self.buildings, self.codes, self.vendors, self.countries,
                        self.regions, self.states, self.postcodes, self.assets,
                        self.contract_refs, self.certificate_numbers))

    def as_dict(self) -> dict[str, Any]:
        return {
            "buildings": self.buildings, "codes": self.codes, "vendors": self.vendors,
            "countries": self.countries, "country_names": self.country_names,
            "regions": self.regions, "states": self.states, "postcodes": self.postcodes,
            "assets": self.assets, "contract_refs": self.contract_refs,
            "certificate_numbers": self.certificate_numbers, "sources": self.sources,
            "empty": self.is_empty(),
        }


def _add(bucket: list[str], value: Any, *, sources: dict[str, str], origin: str,
         limit: int = 12) -> None:
    v = re.sub(r"\s+", " ", str(value or "")).strip(" .,;:-—–|")
    if not v or len(v) < 2 or len(bucket) >= limit:
        return
    if any(normalise(v) == normalise(x) for x in bucket):
        return
    bucket.append(v)
    sources.setdefault(v, origin)


def from_document(
    *,
    text: str | None = None,
    file_name: str | None = None,
    extracted: dict[str, Any] | None = None,
    doc_type: str | None = None,
) -> Claims:
    """Read a document for what it says about where it belongs."""
    c = Claims()
    body = (text or "")[:200_000]
    fields = {k: v for k, v in (extracted or {}).items() if v not in (None, "", [], {})}

    # 1. fields an extractor already labelled
    for bucket_name, keys in _FIELD_SOURCES.items():
        bucket = getattr(c, bucket_name)
        for key in keys:
            for k, v in fields.items():
                if k.lower() != key.lower() or isinstance(v, (dict, list)):
                    continue
                if bucket_name == "countries":
                    code = country_code_for(v)
                    if code:
                        _add(c.countries, code, sources=c.sources, origin=f"field:{k}")
                    _add(c.country_names, v, sources=c.sources, origin=f"field:{k}")
                else:
                    _add(bucket, v, sources=c.sources, origin=f"field:{k}")

    # 2. the text, only where a label introduces the value
    for m in _BUILDING_RE.finditer(body):
        _add(c.buildings, m.group(1), sources=c.sources, origin="text:label")
    for m in _CODE_RE.finditer(body):
        _add(c.codes, m.group(1), sources=c.sources, origin="text:label")
    for m in _VENDOR_RE.finditer(body):
        _add(c.vendors, m.group(1), sources=c.sources, origin="text:label")
    for m in _COMPANY_SUFFIX.finditer(body[:20_000]):
        _add(c.vendors, m.group(1), sources=c.sources, origin="text:company")
    for m in _CONTRACT_RE.finditer(body):
        _add(c.contract_refs, m.group(1), sources=c.sources, origin="text:label")
    for m in _ASSET_RE.finditer(body):
        _add(c.assets, m.group(1), sources=c.sources, origin="text:pattern", limit=20)
    for m in _POSTCODE_UK.finditer(body):
        _add(c.postcodes, m.group(1).upper(), sources=c.sources, origin="text:postcode")
    for m in _COUNTRY_RE.finditer(body):
        name = m.group(1)
        code = country_code_for(name)
        _add(c.country_names, name, sources=c.sources, origin="text:country")
        if code:
            _add(c.countries, code, sources=c.sources, origin="text:country")

    # 3. the file name, for a building nobody labelled. Weakest by a long way: a file
    # is named by whoever saved it, not by the document's author, so "scan001.pdf" is
    # not the document claiming to be about a property called scan001. Recorded with
    # its origin so the comparison can use it to rank candidates and to agree with a
    # building, and never to contradict one.
    if file_name:
        stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", file_name)
        stem = re.sub(r"[_\-]+", " ", stem)
        if any(len(re.sub(r"[^a-z]", "", t)) >= 3 for t in tokens(stem)):
            _add(c.buildings, stem, sources=c.sources, origin="filename")

    return c


def name_overlap(claim: str, candidates: list[str]) -> tuple[float, str | None]:
    """How strongly a claimed name matches any of a building's names. (score, matched name).

    Exact on the normalised form is 1.0. Otherwise it is the share of the claim's
    identifying words — "the", "tower", "house" excluded — that the candidate also has, so
    "Riverside Court" against "Riverside Court, Manchester" scores 1.0 and against
    "Bishopsgate Tower" scores 0.
    """
    cn = normalise(claim)
    best, best_name = 0.0, None
    for cand in candidates:
        if not cand:
            continue
        vn = normalise(cand)
        if not cn or not vn:
            continue
        if cn == vn or cn in vn or vn in cn:
            return 1.0, cand
        a, b = tokens(claim), tokens(cand)
        if not a or not b:
            continue
        score = len(a & b) / len(a)
        if score > best:
            best, best_name = score, cand
    return best, best_name
