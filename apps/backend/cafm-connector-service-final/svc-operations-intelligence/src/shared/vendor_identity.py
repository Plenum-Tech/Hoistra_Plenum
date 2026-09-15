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


async def vendor_named_in(session: AsyncSession, text_body: str | None) -> str | None:
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
    async with session.begin_nested():
        rows = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS id, vendor_name
                      FROM plenum_cafm.vendors
                     WHERE NULLIF(TRIM(vendor_name), '') IS NOT NULL
                     ORDER BY length(vendor_name) DESC, created_at NULLS LAST, id
                    """
                )
            )
        ).mappings().all()
    for r in rows:
        name = re.sub(r"\s+", " ", str(r["vendor_name"]).strip()).lower()
        # Two characters is not a name; matching one would attribute a document to whichever
        # vendor happened to be initialised.
        if len(name) >= 3 and name in body:
            return r["id"]
    return None


async def find_vendor_id(session: AsyncSession, name: str | None) -> str | None:
    """The id of the vendor this name refers to, or None if the register has no such vendor.

    Tried in order, most confident first:
      1. the same name, ignoring case and surrounding space
      2. the same name once normalised — "Gough & Kelly Limited" == "Gough and Kelly Ltd."

    Each step is ordered by creation time then id, so a name that matches several rows
    always resolves to the earliest of them rather than an arbitrary one.
    """
    raw = re.sub(r"\s+", " ", (name or "").strip())
    if len(raw) < 2:
        return None

    async with session.begin_nested():
        row = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS id
                    FROM plenum_cafm.vendors
                    WHERE LOWER(TRIM(vendor_name)) = LOWER(TRIM(:name))
                    ORDER BY created_at NULLS LAST, id
                    LIMIT 1
                    """
                ),
                {"name": raw},
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
        candidates = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS id, vendor_name
                    FROM plenum_cafm.vendors
                    WHERE vendor_name IS NOT NULL
                      AND LOWER(vendor_name) LIKE :first
                    ORDER BY created_at NULLS LAST, id
                    LIMIT 200
                    """
                ),
                {"first": f"%{first}%"},
            )
        ).mappings().all()

    for cand in candidates:
        if normalize_vendor_name(cand["vendor_name"]) == target:
            return str(cand["id"])
    return None
