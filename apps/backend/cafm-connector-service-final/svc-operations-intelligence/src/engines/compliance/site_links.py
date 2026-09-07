"""Which building a certificate belongs to — one resolver for ingest, backfill and coverage.

Per-site coverage never worked on real data. Every building certificate in the register had
``site_id`` NULL, so ``building_coverage`` produced a single "Portfolio (no site linked)"
bucket, the dashboard's per-building rings never found a coverage row to join to, and the
"gaps" column described the portfolio rather than any building in it.

The cause was not missing backfill. It was that the only way to record which building a
certificate belongs to was ``compliance_certificates.site_id``, a UUID column, while
``plenum_cafm.sites`` in this deployment keys on ``site_id VARCHAR(50)`` and carries a
nullable ``id TEXT`` — so ``resolve_site_by_name`` (which returned a value only when it
parsed as a UUID) could never return one, and every ingest silently left the link empty.
A backfill built on that resolver would have matched exactly as many rows: none.

So this module answers the question in the form the data actually takes:

* ``sites_shape`` reads the columns ``plenum_cafm.sites`` really has, rather than assuming
  one of the two schemas in this repo. Both are in the tree (``udr_phase1_schema.sql`` keys
  on a UUID ``id``; ``compliance_entity_bootstrap.sql`` keys on a varchar ``site_id``), and
  a query naming a column the live table lacks fails whole — which is what made the old
  ``_site_labels`` return no labels at all.
* a resolved site is carried as a UUID in ``site_id`` when its key is UUID-shaped and in
  ``site_ref`` (text) when it is not, so a varchar-keyed portfolio links properly instead of
  not at all.
* matching is deterministic and refuses to guess: an exact name or reference match wins, a
  single unambiguous containment match is accepted, and anything matching two sites is
  reported as ambiguous rather than linked to whichever row sorted first. A wrong building
  link is worse than an unlinked certificate — it moves a statutory obligation onto a
  building that does not carry it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger

log = get_logger(__name__)

# Columns the two shapes of plenum_cafm.sites use, in preference order. Only ones that
# actually exist in the live table are ever named in SQL.
_KEY_COLUMNS = ("id", "site_id")
_NAME_COLUMNS = ("site_name", "name", "building_name")
_CODE_COLUMNS = ("site_code", "code", "site_ref", "site_id")

# Below this length a containment match ("Mill" inside "Mill House") says nothing.
_MIN_CONTAINMENT_LEN = 5
# A containment match whose lengths differ by more than this is a different building
# ("Tower A" vs "Tower A Annexe Car Park And Grounds").
_MAX_CONTAINMENT_DELTA = 25
_MIN_REFERENCE_LEN = 3

_SHAPE_CACHE: dict[str, Any] | None = None


# ── pure helpers (no DB — unit-tested directly) ──────────────────────────────────────


def normalize_name(value: Any) -> str:
    """Fold a building name to its comparable form: case, punctuation and spacing only.

    Deliberately conservative — it does not drop words. Stripping "House"/"Building" as
    noise would collapse "Mill House" and "Mill Building" onto one another, and they are
    two addresses with two sets of statutory obligations.
    """
    s = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    s = re.sub(r"\s+", " ", s)
    if s.startswith("the "):
        s = s[4:]
    return s


def normalize_ref(value: Any) -> str:
    """Fold a site code / building reference: alphanumerics, uppercased."""
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def as_uuid(value: Any) -> UUID | None:
    """The value as a UUID, or None when it is not UUID-shaped (a varchar site key)."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value).strip())
    except (ValueError, TypeError, AttributeError):
        return None


@dataclass(frozen=True)
class SiteLink:
    """A resolved building, in whichever form the certificate row can store."""

    site_uuid: UUID | None
    site_ref: str | None
    label: str | None
    matched_on: str

    @property
    def key(self) -> str:
        return str(self.site_uuid) if self.site_uuid else str(self.site_ref or "")


def choose_site_match(
    rows: list[dict[str, Any]],
    *,
    name: Any = None,
    reference: Any = None,
) -> tuple[dict[str, Any] | None, str]:
    """Pick the one site a certificate belongs to, or explain why it can't be picked.

    Returns ``(row, reason)``; ``row`` is None whenever the answer is not unambiguous, and
    the reason is what the backfill report shows the PM so an unlinked certificate is a
    stated outcome rather than a silence.
    """
    if not rows:
        return None, "no_sites"

    ref = normalize_ref(reference)
    if len(ref) >= _MIN_REFERENCE_LEN:
        hits = _dedupe([r for r in rows if ref in (r.get("code_keys") or set())])
        if len(hits) == 1:
            return hits[0], "reference"
        if len(hits) > 1:
            return None, "ambiguous_reference"

    n = normalize_name(name)
    if not n:
        return None, "no_building_name"

    hits = _dedupe([r for r in rows if n in (r.get("name_keys") or set())])
    if len(hits) == 1:
        return hits[0], "name"
    if len(hits) > 1:
        return None, "ambiguous_name"

    if len(n) >= _MIN_CONTAINMENT_LEN:
        hits = _dedupe([r for r in rows if _contains_match(n, r)])
        if len(hits) == 1:
            return hits[0], "name_contains"
        if len(hits) > 1:
            return None, "ambiguous_name_contains"

    return None, "no_match"


def _contains_match(n: str, row: dict[str, Any]) -> bool:
    for nk in row.get("name_keys") or set():
        if len(nk) < _MIN_CONTAINMENT_LEN:
            continue
        if abs(len(nk) - len(n)) > _MAX_CONTAINMENT_DELTA:
            continue
        if n in nk or nk in n:
            return True
    return False


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse rows that are the same site reached by two of its columns."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        k = str(r.get("key") or "")
        if k and k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def coverage_bucket(row: dict[str, Any]) -> tuple[str, str, bool]:
    """The building bucket a certificate row belongs in: ``(key, label, linked)``.

    A certificate that names its building but matches no site row still gets its OWN bucket
    keyed on that name. Coverage for a building the portfolio has not registered as a site
    is still coverage for a building — folding all of them into one "Portfolio" row is what
    made the number meaningless.
    """
    sid = str(row.get("site_id") or "").strip()
    if sid:
        return f"site:{sid}", "", True
    ref = str(row.get("site_ref") or "").strip()
    if ref:
        return f"site:{ref}", "", True
    name = str(row.get("building_name") or "").strip()
    if not name:
        name = str(row.get("building_reference") or "").strip()
    if name:
        return f"building:{normalize_name(name)}", name, False
    return "_portfolio", "Portfolio (no building on certificate)", False


# ── schema-shape introspection ───────────────────────────────────────────────────────


async def sites_shape(session: AsyncSession, *, refresh: bool = False) -> dict[str, Any]:
    """The columns ``plenum_cafm.sites`` actually has, so queries only name real ones.

    Cached per process — the shape is a deployment property, not a request one. On any
    failure it reports no usable columns, and every caller degrades to "cannot resolve"
    rather than raising into an ingest or a dashboard load.
    """
    global _SHAPE_CACHE
    if _SHAPE_CACHE is not None and not refresh:
        return _SHAPE_CACHE
    cols: dict[str, str] = {}
    try:
        async with session.begin_nested():
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT column_name, data_type
                        FROM information_schema.columns
                        WHERE table_schema = 'plenum_cafm' AND table_name = 'sites'
                        """
                    )
                )
            ).mappings().all()
        cols = {str(r["column_name"]): str(r["data_type"]) for r in rows}
    except Exception as exc:  # noqa: BLE001 — introspection must never break a caller
        log.warning("site_links.shape_lookup_failed", error=str(exc)[:200])

    shape = {
        "columns": cols,
        "key_columns": [c for c in _KEY_COLUMNS if c in cols],
        "name_columns": [c for c in _NAME_COLUMNS if c in cols],
        "code_columns": [c for c in _CODE_COLUMNS if c in cols],
    }
    shape["usable"] = bool(shape["key_columns"] and shape["name_columns"])
    _SHAPE_CACHE = shape
    return shape


def _select_clause(shape: dict[str, Any]) -> str:
    parts = [f"{c}::text AS key_{c}" for c in shape["key_columns"]]
    parts += [f"{c}::text AS name_{c}" for c in shape["name_columns"]]
    parts += [
        f"{c}::text AS code_{c}"
        for c in shape["code_columns"]
        if c not in shape["name_columns"]
    ]
    return ", ".join(parts)


def _shape_site_row(raw: dict[str, Any], shape: dict[str, Any]) -> dict[str, Any] | None:
    """One sites row as the matcher wants it: a key, its comparable names and codes."""
    keys = [str(raw.get(f"key_{c}") or "").strip() for c in shape["key_columns"]]
    keys = [k for k in keys if k]
    if not keys:
        return None
    # compliance_certificates.site_id is a UUID column, so prefer a UUID-shaped key when the
    # table offers one; otherwise the key travels in site_ref instead.
    site_uuid = next((u for u in (as_uuid(k) for k in keys) if u is not None), None)
    names = [str(raw.get(f"name_{c}") or "").strip() for c in shape["name_columns"]]
    names = [n for n in names if n]
    codes = [
        str(raw.get(f"code_{c}") or "").strip()
        for c in shape["code_columns"]
        if c not in shape["name_columns"]
    ]
    codes = [c for c in codes if c]
    return {
        "key": str(site_uuid) if site_uuid else keys[0],
        "site_uuid": site_uuid,
        "site_ref": None if site_uuid else keys[0],
        "label": names[0] if names else (codes[0] if codes else keys[0]),
        "name_keys": {normalize_name(n) for n in names if normalize_name(n)},
        "code_keys": {normalize_ref(c) for c in codes + keys if normalize_ref(c)},
    }


# ── DB-backed resolution ─────────────────────────────────────────────────────────────


async def load_sites(session: AsyncSession, *, limit: int = 5000) -> list[dict[str, Any]]:
    """Every site, shaped for matching. Bounded, and used once per backfill / coverage call."""
    shape = await sites_shape(session)
    if not shape["usable"]:
        return []
    sql = f"SELECT {_select_clause(shape)} FROM plenum_cafm.sites LIMIT {int(limit)}"
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql))).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("site_links.load_sites_failed", error=str(exc)[:200])
        return []
    out = [_shape_site_row(dict(r), shape) for r in rows]
    return [r for r in out if r]


async def resolve_site_link(
    session: AsyncSession,
    *,
    name: Any = None,
    reference: Any = None,
) -> SiteLink | None:
    """Resolve one building to a site, for a single certificate at ingest time.

    Narrows in SQL first so a large portfolio doesn't ship every row for one certificate,
    then applies the same deterministic choice the backfill uses.
    """
    if not str(name or "").strip() and not str(reference or "").strip():
        return None
    shape = await sites_shape(session)
    if not shape["usable"]:
        return None

    nm = re.sub(r"\s+", " ", str(name or "").strip())
    ref = str(reference or "").strip()
    conds: list[str] = []
    params: dict[str, Any] = {}
    if nm:
        params["like"] = f"%{nm[:60]}%"
        params["exact"] = nm
        for c in shape["name_columns"]:
            conds.append(f"{c} ILIKE :like")
            conds.append(f"LOWER(TRIM({c}::text)) = LOWER(TRIM(:exact))")
    if ref:
        params["ref"] = ref
        for c in shape["code_columns"]:
            conds.append(f"UPPER(TRIM({c}::text)) = UPPER(TRIM(:ref))")
    if not conds:
        return None

    sql = (
        f"SELECT {_select_clause(shape)} FROM plenum_cafm.sites "
        f"WHERE {' OR '.join(conds)} LIMIT 200"
    )
    try:
        async with session.begin_nested():
            raw = (await session.execute(text(sql), params)).mappings().all()
    except Exception as exc:  # noqa: BLE001 — best-effort, never blocks an ingest
        log.warning("site_links.resolve_failed", error=str(exc)[:200])
        return None

    rows = [r for r in (_shape_site_row(dict(x), shape) for x in raw) if r]
    match, reason = choose_site_match(rows, name=nm, reference=ref)
    if not match:
        log.info(
            "site_links.unresolved",
            building=str(nm)[:80],
            reason=reason,
            candidates=len(rows),
        )
        return None
    return SiteLink(
        site_uuid=match["site_uuid"],
        site_ref=match["site_ref"],
        label=match["label"],
        matched_on=reason,
    )


async def site_labels(session: AsyncSession, keys: list[str]) -> dict[str, str]:
    """Display names for stored site keys, matched on whichever key columns exist.

    The previous version selected a ``code`` column this table does not have and matched
    only on ``id``, so it raised, was swallowed, and returned nothing — every per-site row
    was labelled with a raw key.
    """
    keys = [str(k).strip() for k in keys if str(k or "").strip()]
    if not keys:
        return {}
    shape = await sites_shape(session)
    if not shape["usable"]:
        return {}
    conds = " OR ".join(f"{c}::text = ANY(:ids)" for c in shape["key_columns"])
    sql = f"SELECT {_select_clause(shape)} FROM plenum_cafm.sites WHERE {conds}"
    try:
        async with session.begin_nested():
            raw = (await session.execute(text(sql), {"ids": keys})).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("site_links.labels_failed", error=str(exc)[:200])
        return {}

    wanted = set(keys)
    out: dict[str, str] = {}
    for x in raw:
        row = _shape_site_row(dict(x), shape)
        if not row:
            continue
        # Label under every key column value the caller might hold (a varchar-keyed table
        # carries both `site_id` and a nullable `id`, and certificates may store either).
        for c in shape["key_columns"]:
            k = str(x.get(f"key_{c}") or "").strip()
            if k and k in wanted:
                out[k] = row["label"]
    return out


# ── backfill ─────────────────────────────────────────────────────────────────────────


async def backfill_site_links(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    dry_run: bool = True,
    limit: int = 1000,
) -> dict[str, Any]:
    """Link building certificates already in the register to their site.

    Defaults to a dry run: the PM sees what WOULD be linked, on what evidence, before any
    row changes. Certificates that match two sites are never linked to one of them — they
    come back as ``ambiguous`` with the candidate names, because a certificate filed against
    the wrong building understates one building's obligations and overstates another's.

    Buildings that match no site row are reported too, with their names. That list is the
    honest output of this exercise: those buildings are not in ``plenum_cafm.sites`` at all,
    and no backfill can invent them — creating site rows from certificate text would seed the
    portfolio from whatever a PDF happened to call a building.
    """
    from ...models import ComplianceCertificate
    from sqlalchemy import select

    q = (
        select(ComplianceCertificate)
        .where(ComplianceCertificate.cert_scope == "Building")
        .limit(int(limit))
    )
    if organization_id:
        q = q.where(
            (ComplianceCertificate.organization_id == organization_id)
            | (ComplianceCertificate.org_id == organization_id)
        )
    certs = list((await session.execute(q)).scalars().all())
    certs = [
        c
        for c in certs
        if not (
            (c.raw_metadata or {}).get("a1_test_fixture")
            or (c.raw_metadata or {}).get("superseded_duplicate")
            or (c.raw_metadata or {}).get("archived")
        )
    ]

    sites = await load_sites(session)
    linked: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    already = 0

    for cert in certs:
        if cert.site_id or getattr(cert, "site_ref", None):
            already += 1
            continue
        name = cert.building_name or (cert.raw_metadata or {}).get("building_name")
        reference = cert.building_reference or (cert.raw_metadata or {}).get(
            "building_reference"
        )
        match, reason = choose_site_match(sites, name=name, reference=reference)
        entry = {
            "certificate_id": str(cert.id),
            "certificate_type_code": cert.certificate_type_code or cert.cert_type,
            "building_name": name,
            "building_reference": reference,
            "reason": reason,
        }
        if not match:
            if reason.startswith("ambiguous"):
                entry["candidates"] = [
                    s["label"]
                    for s in sites
                    if normalize_name(name) in (s.get("name_keys") or set())
                    or _contains_match(normalize_name(name), s)
                ][:8]
                ambiguous.append(entry)
            else:
                unmatched.append(entry)
            continue

        entry["site_key"] = match["key"]
        entry["site_label"] = match["label"]
        linked.append(entry)
        if dry_run:
            continue
        if match["site_uuid"]:
            cert.site_id = match["site_uuid"]
        else:
            cert.site_ref = match["site_ref"]
        meta = dict(cert.raw_metadata or {})
        meta["site_label"] = match["label"]
        meta["site_link_source"] = f"backfill:{reason}"
        cert.raw_metadata = meta

    if not dry_run and linked:
        await session.commit()

    return {
        "ok": True,
        "dry_run": dry_run,
        "sites_available": len(sites),
        "certificates_scanned": len(certs),
        "already_linked": already,
        "linked": linked,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
        "counts": {
            "linked": len(linked),
            "ambiguous": len(ambiguous),
            "unmatched": len(unmatched),
        },
        "note": (
            "Dry run — nothing written. Re-run with dry_run=false to apply."
            if dry_run
            else f"Applied {len(linked)} site link(s)."
        ),
    }


async def search_sites(
    session: AsyncSession, query: str, *, limit: int = 40
) -> list[dict[str, Any]]:
    """Candidate sites for the PM's "review to link" card, as ``{id, name, code}``.

    Returns the key the certificate can actually be linked by. The query this replaces
    selected ``id::text`` unconditionally — a column that is nullable text in this
    deployment — so candidates could come back with no id at all and the PM's pick had
    nothing to write.
    """
    q = re.sub(r"\s+", " ", str(query or "").strip())
    if len(q) < 2:
        return []
    shape = await sites_shape(session)
    if not shape["usable"]:
        return []
    conds = " OR ".join(
        f"{c} ILIKE :q OR {c} ILIKE :first" for c in shape["name_columns"]
    )
    sql = (
        f"SELECT {_select_clause(shape)} FROM plenum_cafm.sites "
        f"WHERE {conds} LIMIT {int(limit)}"
    )
    params = {"q": f"%{q[:60]}%", "first": f"%{q.split(' ')[0][:40]}%"}
    try:
        async with session.begin_nested():
            raw = (await session.execute(text(sql), params)).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("site_links.search_failed", error=str(exc)[:200])
        return []
    out: list[dict[str, Any]] = []
    for x in raw:
        row = _shape_site_row(dict(x), shape)
        if not row:
            continue
        out.append(
            {
                "id": row["key"],
                "name": row["label"],
                "code": next(iter(row["code_keys"]), None),
            }
        )
    return out
