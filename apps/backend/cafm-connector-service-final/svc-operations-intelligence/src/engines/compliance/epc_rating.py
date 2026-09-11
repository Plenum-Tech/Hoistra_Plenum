"""B5 — the EPC band, and what MEES says about it.

An Energy Performance Certificate carries an asset rating: a band A–G and the numeric score
(0–100+) behind it. MEES — the Minimum Energy Efficiency Standards — makes the band a legal
fact about a building: below E it cannot be let (enforceable since April 2023 for existing
leases), and the proposed trajectory is C by 2027 and B by 2030. The register stored none of
this. It stored the EPC as a certificate with an expiry, so "which of my buildings is below
E" had no answer.

Three sources, one column. The band is read from the document at extraction (the label
varies: "Asset rating", "Energy rating", "This building's energy rating is"), from the
GOV.UK register when a certificate is verified there (``current_energy_rating`` in the
evidence), or typed in by hand. The register wins when it and the document disagree,
because it is the issued figure and the document may be a superseded copy.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

BANDS = ("A", "B", "C", "D", "E", "F", "G")
#: Score thresholds for non-domestic EPCs (SBEM): A+ ≤ 0 … G ≥ 151. The upper bound of each band.
BAND_UPPER_SCORE = {"A": 25, "B": 50, "C": 75, "D": 100, "E": 125, "F": 150, "G": 10_000}
MEES_MINIMUM_NOW = "E"           # enforceable: England and Wales, all lettable non-domestic
MEES_PROPOSED_2027 = "C"
MEES_PROPOSED_2030 = "B"
MEES_LARGE_BUILDING_M2 = 1000.0  # the frontend qualifies the 2030 count by "over 1,000 m²"

#: Certificate type codes that carry an energy band. Anything else never gets one.
EPC_TYPE_CODES = frozenset({"EPC", "DEC", "EPC_NON_DOMESTIC", "EPC_COMMERCIAL"})

_BAND_LABELS = (
    r"asset\s+rating", r"energy\s+rating", r"epc\s+rating", r"energy\s+performance\s+rating",
    r"this\s+building'?s\s+energy\s+rating\s+is", r"current\s+rating", r"rating",
)
# "Asset rating: C 63", "Energy rating B (42)", "This building's energy rating is D",
# "Rating | C | 63". The band must be a single letter A–G standing alone; the score, if it
# follows within a few characters, is one to three digits.
_BAND_RE = re.compile(
    r"(?:" + "|".join(_BAND_LABELS) + r")\s*(?:\(a[-–]g\)(?:\s*and\s+score)?)?\s*[:|\-–]?\s*"
    r"\b([A-G])\b(?!\w)\s*[:|(\-–]?\s*(\d{1,3})?",
    re.IGNORECASE,
)
_BARE_BAND_SCORE_RE = re.compile(r"\b([A-G])\s*[\(\-–:]\s*(\d{1,3})\)?", re.IGNORECASE)


def normalise_band(value: Any) -> str | None:
    """'c' → 'C'; 'Band D' → 'D'; 'A+' → 'A'; anything else → None."""
    if value is None:
        return None
    s = str(value).strip().upper()
    s = re.sub(r"^(BAND|RATING|EPC)\s*[:\-]?\s*", "", s)
    s = s.rstrip("+").strip()
    return s if s in BANDS else None


def band_for_score(score: int | None) -> str | None:
    """The band an SBEM score falls in. Used only when a document gives a score and no band."""
    if score is None or score < 0:
        return None
    for band in BANDS:
        if score <= BAND_UPPER_SCORE[band]:
            return band
    return "G"


def parse_energy_rating(text_or_value: Any) -> tuple[str | None, int | None]:
    """Band and score from free text or a bare value. (None, None) when there is none.

    A bare value ("C", "c 63", "Band D") is tried first; then the labelled patterns; then a
    band immediately followed by a score anywhere in the text. A score without a band gives
    the band the score implies.
    """
    if text_or_value is None:
        return None, None
    raw = str(text_or_value).strip()
    if not raw:
        return None, None
    bare = normalise_band(raw)
    if bare:
        return bare, None
    m = re.fullmatch(r"(?:band\s*)?([A-Ga-g])\s*[\(\-–:\s]\s*(\d{1,3})\)?", raw)
    if m:
        return m.group(1).upper(), int(m.group(2))
    if raw.isdigit():
        score = int(raw)
        return band_for_score(score), score
    m = _BAND_RE.search(raw)
    if m:
        return m.group(1).upper(), (int(m.group(2)) if m.group(2) else None)
    m = _BARE_BAND_SCORE_RE.search(raw)
    if m:
        return m.group(1).upper(), int(m.group(2))
    return None, None


#: Keys a document extraction or an API body may carry the rating under.
RATING_KEYS = (
    "energy_rating", "epc_rating", "epc_band", "asset_rating", "rating", "current_energy_rating",
    "Asset rating (A-G) and score", "Asset rating", "Energy rating",
)
SCORE_KEYS = ("energy_score", "epc_score", "asset_rating_score", "score")


def rating_from_fields(fields: dict[str, Any] | None) -> tuple[str | None, int | None]:
    """Band and score from an extracted-fields dict, whichever key carries them."""
    if not fields:
        return None, None
    band: str | None = None
    score: int | None = None
    for key in RATING_KEYS:
        if key in fields and fields[key] not in (None, ""):
            b, s = parse_energy_rating(fields[key])
            band = band or b
            score = score if score is not None else s
            if band:
                break
    for key in SCORE_KEYS:
        v = fields.get(key)
        if v not in (None, "") and score is None:
            try:
                score = int(str(v).strip().rstrip("+"))
            except ValueError:
                pass
    if band is None and score is not None:
        band = band_for_score(score)
    return band, score


def rating_from_verification(meta: dict[str, Any] | None) -> str | None:
    """The band the GOV.UK register reported, if this certificate was checked there."""
    try:
        return normalise_band(((meta or {}).get("verification") or {}).get("evidence", {}).get("current_energy_rating"))
    except AttributeError:
        return None


def is_epc_type(certificate_type_code: str | None) -> bool:
    return (certificate_type_code or "").upper() in EPC_TYPE_CODES


def band_index(band: str | None) -> int | None:
    return BANDS.index(band) if band in BANDS else None


def below(band: str | None, minimum: str) -> bool | None:
    """Whether ``band`` is worse than ``minimum`` (F is below E). None when unknown."""
    b, m = band_index(band), band_index(minimum)
    if b is None or m is None:
        return None
    return b > m


def mees_position(band: str | None, *, gia_m2: float | None = None) -> dict[str, Any]:
    """What MEES says about one band."""
    return {
        "band": band,
        "below_minimum_now": below(band, MEES_MINIMUM_NOW),
        "below_proposed_2027": below(band, MEES_PROPOSED_2027),
        "below_proposed_2030": below(band, MEES_PROPOSED_2030),
        "large_building": (gia_m2 or 0) > MEES_LARGE_BUILDING_M2 if gia_m2 is not None else None,
        "minimum_now": MEES_MINIMUM_NOW,
        "proposed_2027": MEES_PROPOSED_2027,
        "proposed_2030": MEES_PROPOSED_2030,
        "status": (
            "unknown" if band is None
            else "below_minimum" if below(band, MEES_MINIMUM_NOW)
            else "at_risk_2030" if below(band, MEES_PROPOSED_2030)
            else "compliant"
        ),
    }


async def mees_summary(
    session: AsyncSession,
    *,
    organization_id: UUID | None,
    building_ids: Iterable[UUID] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """The UK ratings tiles, from the register: how many lettable buildings sit below E now,
    how many below B (the 2030 proposal) and over 1,000 m², and how many hold a current EPC.

    Per building, the most recent EPC with a band wins. A building with no EPC on file is
    reported separately — an unknown band is not a compliant one.
    """
    today = today or date.today()
    where = ["c.cert_scope = 'Building'", "c.certificate_type_code = ANY(CAST(:codes AS varchar[]))",
             "c.building_id IS NOT NULL", "coalesce(c.status,'') <> 'archived'"]
    params: dict[str, Any] = {"codes": sorted(EPC_TYPE_CODES)}
    if organization_id:
        where.append("coalesce(c.organization_id, c.org_id) = CAST(:org AS uuid)")
        params["org"] = str(organization_id)
    ids = [str(b) for b in (building_ids or [])]
    if building_ids is not None:
        where.append("c.building_id = ANY(CAST(:bids AS uuid[]))")
        params["bids"] = ids
    rows = (await session.execute(text(f"""
        SELECT DISTINCT ON (c.building_id)
               c.building_id::text, b.name, b.gross_area_sqft, c.energy_rating, c.energy_score,
               c.expiry_date, c.certificate_number, c.id::text AS certificate_id
          FROM plenum_cafm.compliance_certificates c
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = c.building_id
         WHERE {' AND '.join(where)}
         ORDER BY c.building_id, (c.energy_rating IS NULL), c.issue_date DESC NULLS LAST,
                  c.created_at DESC
    """), params)).mappings().all()

    buildings = []
    below_e = below_b_large = current = expiring_12m = 0
    for r in rows:
        gia = float(r["gross_area_sqft"]) * 0.092903 if r["gross_area_sqft"] is not None else None
        pos = mees_position(r["energy_rating"], gia_m2=gia)
        expiry = r["expiry_date"]
        is_current = bool(expiry and expiry >= today)
        if is_current:
            current += 1
            if (expiry - today).days <= 365:
                expiring_12m += 1
        if pos["below_minimum_now"]:
            below_e += 1
        if pos["below_proposed_2030"] and (pos["large_building"] or gia is None):
            below_b_large += 1
        buildings.append({
            "building_id": r["building_id"], "name": r["name"], "band": r["energy_rating"],
            "score": r["energy_score"], "gia_m2": round(gia, 1) if gia else None,
            "expiry_date": expiry.isoformat() if expiry else None, "current": is_current,
            "certificate_id": r["certificate_id"], "certificate_number": r["certificate_number"],
            "mees": pos["status"],
        })
    with_band = sum(1 for b in buildings if b["band"])
    return {
        "ok": True,
        "buildings_with_epc": len(buildings),
        "buildings_with_band": with_band,
        "below_minimum_now": below_e,
        "below_proposed_2030_over_1000m2": below_b_large,
        "epcs_current": current,
        "epcs_expiring_within_12_months": expiring_12m,
        "minimum_now": MEES_MINIMUM_NOW,
        "proposed_2030": MEES_PROPOSED_2030,
        "buildings": buildings,
        "tiles": [
            {"l": "MEES — enforceable now", "v": str(below_e),
             "s": f"below EPC {MEES_MINIMUM_NOW} · {len(buildings)} with an EPC on file",
             "tone": "risk" if below_e else "ok", "basis": "certificate"},
            {"l": "MEES — proposed 2030", "v": str(below_b_large),
             "s": f"below EPC {MEES_PROPOSED_2030} · over {int(MEES_LARGE_BUILDING_M2):,} m²",
             "tone": "risk" if below_b_large else "ok", "basis": "certificate"},
            {"l": "EPCs on file", "v": f"{current} / {len(buildings)}",
             "s": ("none expiring inside 12 months" if not expiring_12m
                   else f"{expiring_12m} expiring inside 12 months"),
             "tone": "warn" if expiring_12m else "ok", "basis": "certificate"},
        ],
    }
