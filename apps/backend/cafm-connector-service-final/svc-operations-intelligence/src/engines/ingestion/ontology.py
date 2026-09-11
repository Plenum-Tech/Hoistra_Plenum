"""What the platform already knows about a building — the thing a document is checked against.

A building is not a name. It is a name, a code, a place, the vendors who work there, the
assets and equipment in it, its floors and spaces, the certificates and contracts already
filed, the meters reading it and the documents on it. That set is the building's ontology,
and it is what makes "does this document belong here?" a question with evidence behind it
rather than a guess.

Two things this is careful about.

**It reads whatever exists.** Deployments differ — one has `plenum_cafm.meters`, another has
`energy_meters`; one keys sites on a UUID, another on a VARCHAR. Every query here is guarded
so a missing table or column costs that one signal and not the whole profile.

**It says how much it knows.** A building created this morning has almost nothing, and a
validator that treats thin evidence as agreement would wave the first document through — the
one filing that sets the building's whole shape. ``evidence_strength`` is that count, and the
validator uses it to say "I cannot confirm this" rather than "this is fine".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger

log = get_logger(__name__)

#: Words that carry no identity on their own — "the tower", "house", "court". A document
#: matching a building on nothing but one of these has matched nothing.
GENERIC_NAME_WORDS = frozenset({
    "the", "and", "of", "at", "building", "buildings", "tower", "towers", "house", "court",
    "centre", "center", "plaza", "park", "place", "hall", "mall", "link", "quay", "heights",
    "point", "works", "yard", "wharf", "street", "road", "lane", "avenue", "site", "block",
    "unit", "floor", "ltd", "limited", "plc", "llp", "inc", "llc", "company", "group",
})


def normalise(value: Any) -> str:
    """Lowercased, punctuation-free, single-spaced. The form every comparison uses."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower())).strip()


def tokens(value: Any) -> set[str]:
    """Identifying words of a name: everything except the words every building shares."""
    return {t for t in normalise(value).split() if len(t) > 2 and t not in GENERIC_NAME_WORDS}


@dataclass
class BuildingOntology:
    building_id: str
    name: str | None = None
    names: list[str] = field(default_factory=list)          # name, site name, aliases
    codes: list[str] = field(default_factory=list)          # building_code, site_code, site_id
    country_code: str | None = None
    country: str | None = None
    state: str | None = None
    region: str | None = None
    city: str | None = None
    postcode: str | None = None
    address: str | None = None
    organization_id: str | None = None
    vendors: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)         # codes and names
    equipment: list[str] = field(default_factory=list)
    floors: list[str] = field(default_factory=list)
    meters: list[str] = field(default_factory=list)
    certificate_types: list[str] = field(default_factory=list)
    certificate_numbers: list[str] = field(default_factory=list)
    contract_refs: list[str] = field(default_factory=list)
    document_names: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def evidence_strength(self) -> int:
        """How much there is to check against. 0 means a new building: nothing can be
        confirmed from the data, and the protocol says so rather than assuming."""
        return sum(self.counts.get(k, 0) for k in
                   ("assets", "documents", "certificates", "contracts", "work_orders",
                    "meters", "floors", "vendors"))

    @property
    def is_new(self) -> bool:
        return self.evidence_strength == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "building_id": self.building_id, "name": self.name, "names": self.names,
            "codes": self.codes, "country_code": self.country_code, "country": self.country,
            "state": self.state, "region": self.region, "city": self.city,
            "postcode": self.postcode, "organization_id": self.organization_id,
            "vendors": self.vendors, "assets": self.assets[:120], "equipment": self.equipment[:60],
            "floors": self.floors, "meters": self.meters,
            "certificate_types": self.certificate_types,
            "certificate_numbers": self.certificate_numbers[:60],
            "contract_refs": self.contract_refs[:60], "document_names": self.document_names[:60],
            "counts": self.counts, "evidence_strength": self.evidence_strength,
            "is_new": self.is_new,
        }


async def _rows(session: AsyncSession, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One guarded read. A deployment without the table loses this signal, not the profile."""
    try:
        return [dict(r) for r in (await session.execute(text(sql), params or {})).mappings().all()]
    except Exception as exc:  # noqa: BLE001 — a missing table is a thinner profile, not an error
        await session.rollback()
        log.info("ontology.read_skipped", error=str(exc)[:160])
        return []


def _vals(rows: list[dict[str, Any]], *keys: str) -> list[str]:
    out: list[str] = []
    for r in rows:
        for k in keys:
            v = r.get(k)
            if v is not None and str(v).strip() and str(v) not in out:
                out.append(str(v).strip())
    return out


async def load(session: AsyncSession, building_id: UUID | str) -> BuildingOntology | None:
    """The building's profile, or None when there is no such building."""
    bid = str(building_id)
    head = await _rows(session, """
        SELECT b.building_id::text AS building_id, b.name, b.building_code,
               b.organization_id::text AS organization_id, b.site_id::text AS site_id,
               s.site_name, s.building_name, s.building_code AS site_building_code,
               s.site_code, s.country, s.country_code, s.state, s.region, s.city,
               s.postcode, s.address
          FROM plenum_cafm.buildings b
          LEFT JOIN plenum_cafm.sites s ON s.id = b.site_id OR s.site_id = b.site_id
         WHERE b.building_id = CAST(:b AS uuid)
         LIMIT 1""", {"b": bid})
    if not head:
        return None
    h = head[0]

    o = BuildingOntology(
        building_id=bid,
        name=h.get("name") or h.get("building_name") or h.get("site_name"),
        organization_id=h.get("organization_id"),
        country_code=(h.get("country_code") or "").upper() or None,
        country=h.get("country"), state=h.get("state"), region=h.get("region"),
        city=h.get("city"), postcode=h.get("postcode"), address=h.get("address"),
    )
    o.names = _vals(head, "name", "building_name", "site_name")
    o.codes = _vals(head, "building_code", "site_building_code", "site_code")

    assets = await _rows(session, """
        SELECT asset_code, asset_name FROM plenum_cafm.assets
         WHERE building_id = CAST(:b AS uuid) LIMIT 500""", {"b": bid})
    o.assets = _vals(assets, "asset_code", "asset_name")

    equip = await _rows(session, """
        SELECT e.name, e.model, e.serial FROM plenum_cafm.equipment e
          JOIN plenum_cafm.assets a ON a.id = e.asset_id
         WHERE a.building_id = CAST(:b AS uuid) LIMIT 200""", {"b": bid})
    o.equipment = _vals(equip, "name", "model", "serial")

    o.floors = _vals(await _rows(session, """
        SELECT name, level::text AS level FROM plenum_cafm.floors
         WHERE building_id = CAST(:b AS uuid) LIMIT 100""", {"b": bid}), "name", "level")

    o.meters = _vals(await _rows(session, """
        SELECT mpan_mprn, meter_type FROM plenum_cafm.meters
         WHERE building_id = CAST(:b AS uuid) LIMIT 100""", {"b": bid}), "mpan_mprn")

    certs = await _rows(session, """
        SELECT certificate_type_code, certificate_number, issuer, building_name
          FROM plenum_cafm.compliance_certificates
         WHERE building_id = CAST(:b AS uuid) LIMIT 300""", {"b": bid})
    o.certificate_types = _vals(certs, "certificate_type_code")
    o.certificate_numbers = _vals(certs, "certificate_number")
    for n in _vals(certs, "building_name"):
        if n not in o.names:
            o.names.append(n)

    docs = await _rows(session, """
        SELECT file_name, title FROM plenum_cafm.documents
         WHERE building_id = CAST(:b AS uuid) ORDER BY uploaded_at DESC NULLS LAST LIMIT 200""",
        {"b": bid})
    o.document_names = _vals(docs, "file_name", "title")

    # Vendors reach a building four ways, and a vendor known by any of them is a vendor
    # known here: a certificate filed against it, a work order raised on it, a contract whose
    # document is filed against it, or an invoice verified against it.
    vendors = await _rows(session, """
        SELECT DISTINCT v.vendor_name
          FROM plenum_cafm.vendors v
         WHERE v.id IN (
                 SELECT vendor_id FROM plenum_cafm.compliance_certificates
                  WHERE building_id = CAST(:b AS uuid) AND vendor_id IS NOT NULL
           UNION SELECT vendor_id FROM plenum_cafm.work_orders
                  WHERE building_id = CAST(:b AS uuid) AND vendor_id IS NOT NULL
           UNION SELECT p.vendor_id FROM plenum_cafm.contract_sla_parameters p
                  JOIN plenum_cafm.documents d ON d.document_id = p.document_id
                 WHERE d.building_id = CAST(:b AS uuid) AND p.vendor_id IS NOT NULL
         ) LIMIT 200""", {"b": bid})
    o.vendors = _vals(vendors, "vendor_name")

    contracts = await _rows(session, """
        SELECT p.contract_ref FROM plenum_cafm.contract_sla_parameters p
          JOIN plenum_cafm.documents d ON d.document_id = p.document_id
         WHERE d.building_id = CAST(:b AS uuid) LIMIT 200""", {"b": bid})
    o.contract_refs = _vals(contracts, "contract_ref")

    wo = await _rows(session, """
        SELECT count(*) AS n FROM plenum_cafm.work_orders
         WHERE building_id = CAST(:b AS uuid)""", {"b": bid})
    o.counts = {
        "assets": len(assets), "equipment": len(equip), "floors": len(o.floors),
        "meters": len(o.meters), "certificates": len(certs), "documents": len(docs),
        "vendors": len(o.vendors), "contracts": len(contracts),
        "work_orders": int(wo[0]["n"]) if wo else 0,
    }
    return o


async def portfolio(
    session: AsyncSession,
    *,
    organization_id: UUID | str | None,
    building_ids: list[UUID] | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Every building the caller may see, in the light form the "which building is this?"
    search needs: identity, place, and the vendors and assets that name it.

    Deliberately not the full profile per building — suggesting a building is a comparison
    across the portfolio, and loading each one's assets and certificates to rank a single
    document would turn one upload into hundreds of queries.
    """
    where = []
    params: dict[str, Any] = {}
    if building_ids is not None:
        where.append("b.building_id = ANY(CAST(:ids AS uuid[]))")
        params["ids"] = [str(b) for b in building_ids] or ["00000000-0000-0000-0000-000000000000"]
    elif organization_id:
        where.append("b.organization_id = CAST(:o AS uuid)")
        params["o"] = str(organization_id)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    params["lim"] = limit
    return await _rows(session, f"""
        SELECT b.building_id::text AS building_id, b.name, b.building_code,
               s.site_name, s.building_name, s.site_code, s.country_code, s.country,
               s.state, s.region, s.city, s.postcode
          FROM plenum_cafm.buildings b
          LEFT JOIN plenum_cafm.sites s ON s.id = b.site_id OR s.site_id = b.site_id
          {clause}
         ORDER BY b.name
         LIMIT :lim""", params)


async def vendor_buildings(session: AsyncSession, vendor_name: str) -> list[dict[str, Any]]:
    """Which buildings this vendor is actually known at. The answer to "this certificate is
    for a firm that does not work here — where does it belong?"."""
    return await _rows(session, """
        SELECT DISTINCT b.building_id::text AS building_id, b.name
          FROM plenum_cafm.buildings b
          JOIN plenum_cafm.vendors v ON lower(v.vendor_name) = lower(:v)
         WHERE b.building_id IN (
                 SELECT building_id FROM plenum_cafm.compliance_certificates WHERE vendor_id = v.id
           UNION SELECT building_id FROM plenum_cafm.work_orders WHERE vendor_id = v.id
           UNION SELECT d.building_id FROM plenum_cafm.documents d
                  JOIN plenum_cafm.contract_sla_parameters p ON p.document_id = d.document_id
                 WHERE p.vendor_id = v.id
         ) LIMIT 20""", {"v": vendor_name})
