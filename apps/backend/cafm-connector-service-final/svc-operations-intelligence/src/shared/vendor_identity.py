"""One answer to "is this the same vendor?", shared by every path that resolves one.

Vendors arrive by different routes and spell themselves differently each time: a contract
says "Gough and Kelly Ltd.", a certificate says "Gough & Kelly Limited", an invoice says
"GOUGH AND KELLY". Matching on the literal string treats those as three companies and
registers three rows, after which coverage, scoring and blocking each see a different
fraction of the same vendor's record.

Two rules live here so the compliance and contract paths cannot drift apart:

  normalize_vendor_name  what counts as the same name
  find_vendor_id         which row a name resolves to, deterministically

Determinism matters as much as matching. The register currently holds many rows sharing a
name, and a bare ``LIMIT 1`` over them returns whichever row Postgres happens to yield —
so the same vendor could resolve to one row today and another tomorrow, splitting their
history across duplicates. Every lookup here is ordered, so a name always resolves to the
same row for as long as that row exists.
"""
from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Trailing words that describe a company's legal form rather than its identity.
# Stripped only from the END of a name — "Group Services Ltd" keeps "group services"
# if that is genuinely what the company is called, because we stop at the first
# non-suffix token.
_LEGAL_SUFFIXES = {
    "ltd",
    "limited",
    "plc",
    "llp",
    "llc",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "gmbh",
    "bv",
    "nv",
    "sa",
    "ag",
    "pty",
    "lda",
    "srl",
}

# Noise that appears on certificates and contracts but is not part of the company name.
_TRAILING_NOISE = re.compile(
    r"\b(and\s+subsidiary\s+companies|and\s+subsidiaries|t/?a|trading\s+as)\b.*$",
    re.IGNORECASE,
)


def normalize_vendor_name(raw: str | None) -> str:
    """Reduce a company name to the part that identifies it.

    Lowercases, expands ``&`` to ``and``, drops punctuation and legal-form suffixes.
    Returns "" for anything too short to identify a company, and callers treat that as
    "do not match" rather than "matches everything".
    """
    s = (raw or "").strip()
    if not s:
        return ""
    # "&" expands first: the noise below is written with "and", and a certificate that says
    # "X Ltd & Subsidiary Companies" would otherwise keep the whole tail and fail to match
    # the same company written plainly.
    s = s.replace("&", " and ")
    s = _TRAILING_NOISE.sub(" ", s)
    s = re.sub(r"[^0-9A-Za-z]+", " ", s).strip().lower()
    if not s:
        return ""
    tokens = s.split()
    # Strip legal-form words from the tail only, and never strip the whole name away:
    # a vendor genuinely called "Company" keeps its one token.
    while len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    out = " ".join(tokens)
    return out if len(out) >= 2 else ""


def _org_clause(organization_id: object | None, binds: dict) -> str:
    """The tenant filter, and the bind that goes with it.

    Every question here — "is this the same vendor?", "which vendor does this document
    name?" — is asked on behalf of one company, and the register holds them all. Without
    this clause a contract uploaded by one tenant matched, and attached itself to, another
    tenant's supplier row. Seven compliance certificates in the live database are already
    linked that way.

    Returns "" when the caller genuinely has no tenant to scope by — a certificate that
    arrived with no organization on it. That is a narrower hole than the one it replaces,
    and the callers that can supply an organization now all do.
    """
    if not organization_id:
        return ""
    binds["org"] = str(organization_id)
    return "AND organization_id = CAST(:org AS uuid)"


async def vendor_named_in(
    session: AsyncSession,
    text_body: str | None,
    *,
    organization_id: object | None = None,
) -> str | None:
    """The vendor this document names, chosen from the ones we already have.

    The opposite question to find_vendor_id, and a much safer one. Reading a supplier's name
    out of free text means guessing where the name starts and stops, and a wrong guess
    attributes an invoice to a company that does not exist. Asking instead which of the
    register's own names appears in the text can only ever return a vendor we already know,
    or nothing.

    Longest name first, so "Halden Building Services" wins over a "Halden" that would also
    match — the more specific name is the more likely one to be meant.
    """
    body = re.sub(r"\s+", " ", (text_body or "")).lower()
    if len(body) < 3:
        return None
    binds: dict = {}
    org_sql = _org_clause(organization_id, binds)
    # Never a retired vendor (Hussain, 12 Oct 2026): an invoice naming a deleted supplier
    # belongs to no one until that supplier is taken on again, and it is the substring match
    # here that the " (deleted)" mark cannot stop.
    async with session.begin_nested():
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT id::text AS id, vendor_name
                      FROM plenum_cafm.vendors v
                     WHERE NULLIF(TRIM(vendor_name), '') IS NOT NULL
                       AND {live_vendor_sql('v')}
                       {org_sql}
                     ORDER BY length(vendor_name) DESC, created_at NULLS LAST, id
                    """
                ),
                binds,
            )
        ).mappings().all()
    for r in rows:
        name = re.sub(r"\s+", " ", str(r["vendor_name"]).strip()).lower()
        # Two characters is not a name; matching one would attribute a document to whichever
        # vendor happened to be initialised.
        if len(name) >= 3 and name in body:
            return r["id"]
    return None


async def find_vendor_id(
    session: AsyncSession,
    name: str | None,
    *,
    organization_id: object | None = None,
) -> str | None:
    """The id of the vendor this name refers to, or None if the register has no such vendor.

    Tried in order, most confident first:
      1. the same name, ignoring case and surrounding space
      2. the same name once normalised — "Gough & Kelly Limited" == "Gough and Kelly Ltd."

    Each step is ordered by creation time then id, so a name that matches several rows
    always resolves to the earliest of them rather than an arbitrary one.

    Both steps are scoped to ``organization_id`` when the caller knows it. Two companies
    may each use a supplier of the same name, and they are not the same vendor: one has a
    contract with them, the other does not, and answering with the wrong row hands one
    tenant the other's terms.

    Neither step ever answers with a retired vendor (Hussain, 12 Oct 2026). A supplier named
    again after its vendor was deleted is registered afresh by the caller, not revived; the
    name mark already stops the exact step, the status filter stops both.

    The id comes back as the register stores it — which is not always a uuid. Roughly half
    the rows carry legacy ids like "V-01". Callers that write to a uuid column must check
    before they cast; see ``resolve_or_create_vendor``.
    """
    raw = re.sub(r"\s+", " ", (name or "").strip())
    if len(raw) < 2:
        return None

    exact_binds: dict = {"name": raw}
    exact_org = _org_clause(organization_id, exact_binds)
    async with session.begin_nested():
        row = (
            await session.execute(
                text(
                    f"""
                    SELECT id::text AS id
                    FROM plenum_cafm.vendors v
                    WHERE LOWER(TRIM(vendor_name)) = LOWER(TRIM(:name))
                      AND {live_vendor_sql('v')}
                      {exact_org}
                    ORDER BY created_at NULLS LAST, id
                    LIMIT 1
                    """
                ),
                exact_binds,
            )
        ).mappings().first()
        if row:
            return str(row["id"])

        target = normalize_vendor_name(raw)
        if not target:
            return None

        # Narrow to rows sharing the first significant word before normalising in Python.
        # A full scan of the register per lookup would work at today's ~2,000 rows and stop
        # working quietly as it grows.
        first = target.split()[0]
        cand_binds: dict = {"first": f"%{first}%"}
        cand_org = _org_clause(organization_id, cand_binds)
        candidates = (
            await session.execute(
                text(
                    f"""
                    SELECT id::text AS id, vendor_name
                    FROM plenum_cafm.vendors v
                    WHERE vendor_name IS NOT NULL
                      AND LOWER(vendor_name) LIKE :first
                      AND {live_vendor_sql('v')}
                      {cand_org}
                    ORDER BY created_at NULLS LAST, id
                    LIMIT 200
                    """
                ),
                cand_binds,
            )
        ).mappings().all()

    for cand in candidates:
        if normalize_vendor_name(cand["vendor_name"]) == target:
            return str(cand["id"])
    return None


# ── a deleted vendor ─────────────────────────────────────────────────────────
#
# Deleting a vendor that has history RETIRES it (Hussain, 12 Oct 2026): the row stays so the
# work orders, invoices and certificates it appears on still resolve, its status becomes
# 'deleted', and its name carries a visible mark. The status is the authority — every read
# that lists, matches, offers, scans, scores or emails a vendor filters on it. The mark is
# what every history display shows, in every service, without being taught; and an exact
# name match ("Acme" against "Acme (deleted)") no longer lands on the retired row, so a
# supplier taken on again is registered afresh rather than revived.

DELETED_STATUS = "deleted"
DELETED_MARK = " (deleted)"
_NAMELESS = "Deleted vendor"
_ALIAS = re.compile(r"^[a-z_][a-z0-9_]*$")


def mark_deleted_name(name: str | None, max_len: int | None) -> str:
    """The name a retired vendor carries: its own, cut to fit the column, plus the mark.
    Never marked twice; a vendor with no name still reads as a deleted one."""
    base = (name or "").strip()
    if base.endswith(DELETED_MARK):
        return base
    if not base:
        return _NAMELESS
    if max_len and len(base) + len(DELETED_MARK) > max_len:
        base = base[: max(0, max_len - len(DELETED_MARK))].rstrip()
    return base + DELETED_MARK


def unmarked_name(name: str | None) -> str:
    """The name as it was before the vendor was retired."""
    s = (name or "").strip()
    return s[: -len(DELETED_MARK)].rstrip() if s.endswith(DELETED_MARK) else s


def live_vendor_sql(alias: str = "v") -> str:
    """A predicate true for a vendor that is not deleted. Read through to_jsonb so a
    deployment whose vendors table lacks the column reads every row as live."""
    if not _ALIAS.match(alias or ""):
        raise ValueError(f"not a table alias: {alias!r}")
    return f"COALESCE(lower(to_jsonb({alias})->>'status'), 'active') <> '{DELETED_STATUS}'"


def deleted_vendor_ids_sql() -> str:
    """The ids of every retired vendor, as text — for filtering tables that carry vendor_id."""
    return f"SELECT dv.id::text FROM plenum_cafm.vendors dv WHERE NOT ({live_vendor_sql('dv')})"


# The id is matched case-blind: a uuid sent upper-case is the same vendor, and an exact text
# match read it as a vendor with no row, i.e. live (review, 12 Oct 2026).
_STATUS_BY_ID = ("SELECT lower(to_jsonb(v)->>'status') FROM plenum_cafm.vendors v "
                 "WHERE lower(v.id::text) = lower(:vid) LIMIT 1")


async def is_deleted_vendor(session: AsyncSession, vendor_id: object | None, *,
                            unreadable_is_deleted: bool = False, for_share: bool = False) -> bool:
    """Whether this vendor has been deleted. A status that cannot be read answers False for a
    read, and True when ``unreadable_is_deleted`` — a write that would link new data to the
    vendor refuses rather than guess.

    ``for_share`` is for those writes: the delete holds the vendor row FOR UPDATE while it
    runs, and a plain read does not wait for it, so it saw 'active' and the write landed on a
    vendor retired a moment later. FOR SHARE waits for the delete and then reads 'deleted'."""
    vid = str(vendor_id or "").strip()
    if not vid:
        return False
    try:
        async with session.begin_nested():
            status = (await session.execute(
                text(_STATUS_BY_ID + (" FOR SHARE" if for_share else "")),
                {"vid": vid},
            )).scalar()
    except Exception:  # noqa: BLE001 — see docstring
        return unreadable_is_deleted
    return str(status or "").strip().lower() == DELETED_STATUS


async def vendor_link_refusal(session: AsyncSession, vendor_id: object | None,
                              organization_id: object | None, *,
                              what: str = "nothing new can be linked to it") -> dict | None:
    """None when new data may be linked to this vendor (or none is named); otherwise the refusal.

    For a write that links new data to a vendor. is_deleted_vendor answers "not deleted" for a
    vendor with no row, so a certificate or skill could be linked to a vendor that had been
    REMOVED, or to another company's (review, 12 Oct 2026). This refuses a vendor that does not
    exist, or is not ``organization_id``'s (when that is known) — both as ``vendor_not_found``,
    so another company's id says nothing about it — a retired one, and one whose record cannot
    be read. Read FOR SHARE, so a link racing the delete waits for it."""
    vid = str(vendor_id or "").strip()
    if not vid:
        return None
    try:
        async with session.begin_nested():
            row = (await session.execute(
                text("SELECT to_jsonb(v)->>'organization_id' AS organization_id, "
                     "lower(to_jsonb(v)->>'status') AS status FROM plenum_cafm.vendors v "
                     "WHERE lower(v.id::text) = lower(:vid) LIMIT 1 FOR SHARE"),
                {"vid": vid},
            )).mappings().first()
    except Exception:  # noqa: BLE001 — a read that failed is not a vendor that is missing
        return {"ok": False, "reason": "vendor_unreadable",
                "error": f"This vendor's record could not be checked just now, so {what}. "
                         "Try again in a moment."}
    org = str(organization_id or "").strip().lower()
    if not row or (org and str(row.get("organization_id") or "").strip().lower() != org):
        return {"ok": False, "reason": "vendor_not_found",
                "error": f"No such vendor in your company, so {what}."}
    if str(row.get("status") or "").strip().lower() == DELETED_STATUS:
        return {"ok": False, "reason": "vendor_deleted",
                "error": f"This vendor has been deleted, so {what}."}
    return None
