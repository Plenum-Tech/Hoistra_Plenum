"""Give the test portfolio real documents to hold.

``04_demo_data.sql`` builds a portfolio of *records*: 56 document rows, every one of them a
statement that a document exists, none of them a file. That is honest — the generator never
had a file to point at — but it means nobody testing this environment can exercise the half
of the product that opens a document, and the whole certificate register reads "no scan".

This copies the real compliance PDFs from a database that has them into the test database,
attaching each one to a certificate of *its own type*. An EICR pdf filed against a gas safety
record would be worse than no file at all, so a document is only ever placed on a certificate
whose ``certificate_type_code`` matches, and where the test portfolio has no certificate of
that type, one is created rather than the file being forced onto something else.

    python db/tools/import_real_documents.py --from <dsn> --to <dsn>            # plan only
    python db/tools/import_real_documents.py --from <dsn> --to <dsn> --apply    # write

Both DSNs are given on the command line and nothing is hard-coded, deliberately: the source
is somebody's real data and the URLs point into a private storage container, so neither
belongs in a file that gets committed. ``--from`` defaults to the local stack in
``docker-compose.local-db.yml``; ``--to`` has no default, because writing to the wrong
database is the one mistake this script could make that is not recoverable by re-running it.

What it does NOT copy: inspector names and accreditation numbers. A shared test environment
needs the file, its type and its dates to be useful; it does not need the name of the engineer
who signed it, and the fewer real people in a database several testers can read, the better.

Re-running is safe. Documents keep their source ids, so a second run updates in place rather
than duplicating, and a certificate that already points at its file is left alone.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from typing import Any

import asyncpg

DEFAULT_FROM = "postgresql://cafm:cafm@localhost:5432/hoistra"

# The two scopes a certificate can have, and what a document of that scope hangs off. A
# building certificate's document belongs to that building; a vendor certificate's document
# belongs to no building at all, and giving it one would put it in a drawer it has no business
# being in.
SCOPES = ("Building", "Vendor")


async def read_source(dsn: str) -> list[dict[str, Any]]:
    """Every document in the source that actually has a file, with the certificate it backs."""
    conn = await asyncpg.connect(dsn, ssl=_ssl_for(dsn))
    try:
        rows = await conn.fetch(
            """
            SELECT d.document_id, d.doc_type, d.title, d.file_name, d.blob_url,
                   c.certificate_type_code, c.cert_scope, c.certificate_number,
                   c.issue_date, c.expiry_date, c.next_due_date, c.status, c.issuer,
                   c.country_code
              FROM plenum_cafm.documents d
              JOIN plenum_cafm.compliance_certificates c
                ON COALESCE(c.document_id, c.source_document_id) = d.document_id
             WHERE NULLIF(d.blob_url, '') IS NOT NULL
               AND c.certificate_type_code IS NOT NULL
             ORDER BY c.certificate_type_code, d.file_name
            """
        )
        # One row per document: a document backing two certificates would otherwise be placed
        # twice, and the second placement would overwrite the first.
        seen, out = set(), []
        for r in rows:
            if r["document_id"] in seen:
                continue
            seen.add(r["document_id"])
            out.append(dict(r))
        return out
    finally:
        await conn.close()


def _ssl_for(dsn: str) -> Any:
    """Azure Postgres requires TLS; a local container has none to offer."""
    host = re.search(r"@([^/:]+)", dsn)
    return "require" if host and not host.group(1).startswith(("localhost", "127.")) else False


async def plan(src: list[dict[str, Any]], conn: asyncpg.Connection) -> list[dict[str, Any]]:
    """Where each source document should land, without writing anything."""
    # A building carries no country of its own — that sits on the site above it, and which
    # column holds it differs between deployments, so it is looked up rather than assumed.
    site_country = await conn.fetchval(
        """SELECT column_name FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name='sites'
              AND column_name IN ('country_code','country')
            ORDER BY column_name LIMIT 1""")
    buildings = await conn.fetch(
        f"""SELECT b.building_id, b.name,
                   {('s.' + site_country) if site_country else 'NULL'}::text AS country_code
              FROM plenum_cafm.buildings b
              LEFT JOIN plenum_cafm.sites s ON s.site_id = b.site_id
             ORDER BY b.name""")
    vendors = await conn.fetch(
        "SELECT id, vendor_name, vendor_code, country FROM plenum_cafm.vendors "
        "ORDER BY vendor_name")
    if not buildings:
        sys.exit("target has no buildings — load 04_demo_data.sql first")

    org = await conn.fetchval(
        "SELECT organization_id FROM plenum_cafm.compliance_certificates "
        "WHERE organization_id IS NOT NULL LIMIT 1")

    taken: set[Any] = set()
    steps: list[dict[str, Any]] = []
    for i, d in enumerate(src):
        code = d["certificate_type_code"]
        scope = d["cert_scope"] if d["cert_scope"] in SCOPES else "Building"

        # An unevidenced certificate of the same type is the best home: it is a real gap in
        # the register that this file closes, and nothing gets displaced.
        cert = await conn.fetchrow(
            """SELECT id, building_id, vendor_id FROM plenum_cafm.compliance_certificates
                WHERE certificate_type_code = $1 AND COALESCE(cert_scope,'Building') = $2
                  AND COALESCE(document_id, source_document_id) IS NULL
                  AND NOT (id = ANY($3::uuid[]))
                ORDER BY certificate_ref LIMIT 1""",
            code, scope, list(taken))
        if cert:
            taken.add(cert["id"])
            steps.append({"doc": d, "cert_id": cert["id"], "create_cert": False,
                          "building_id": cert["building_id"], "vendor_id": cert["vendor_id"],
                          "scope": scope, "why": "attached to an unevidenced certificate"})
            continue

        # Nothing of that type is going spare, so the register gains a certificate rather
        # than the file being pushed onto a certificate of some other type.
        if scope == "Vendor":
            v = vendors[i % len(vendors)] if vendors else None
            if v is None:
                steps.append({"doc": d, "skip": "no vendors in the target"})
                continue
            holder = {"building_id": None, "vendor_id": v["id"],
                      "country": d["country_code"] or v["country"] or "UK",
                      "where": v["vendor_name"]}
        else:
            b = buildings[i % len(buildings)]
            holder = {"building_id": b["building_id"], "vendor_id": None,
                      "country": d["country_code"] or b["country_code"] or "UK",
                      "where": b["name"]}
        steps.append({"doc": d, "cert_id": None, "create_cert": True, "org": org,
                      "scope": scope, **holder,
                      "why": f"new {scope.lower()} certificate on {holder['where']}"})
    return steps


async def apply(steps: list[dict[str, Any]], conn: asyncpg.Connection) -> None:
    async with conn.transaction():
        for s in steps:
            if s.get("skip"):
                continue
            d = s["doc"]
            # The document keeps its source id, which is what makes a second run an update
            # rather than a duplicate.
            await conn.execute(
                """INSERT INTO plenum_cafm.documents
                       (document_id, building_id, doc_type, title, file_name, blob_url,
                        uploaded_at)
                   VALUES ($1, $2, 'compliance_certificate', $3, $4, $5, now())
                   ON CONFLICT (document_id) DO UPDATE
                      SET building_id = EXCLUDED.building_id,
                          title       = EXCLUDED.title,
                          file_name   = EXCLUDED.file_name,
                          blob_url    = EXCLUDED.blob_url""",
                d["document_id"], s.get("building_id"),
                d["title"] or d["certificate_type_code"], d["file_name"], d["blob_url"])

            if s["create_cert"]:
                ref = f"{d['certificate_type_code']}-REAL-{str(d['document_id'])[:8]}"
                await conn.execute(
                    """INSERT INTO plenum_cafm.compliance_certificates
                           (id, org_id, organization_id, certificate_ref, cert_type,
                            certificate_type_code, certificate_number, cert_scope,
                            building_id, vendor_id, country_code, issue_date, expiry_date,
                            next_due_date, status, issuer, document_id, source_document_id,
                            raw_metadata)
                       VALUES (gen_random_uuid(), $1, $1, $2, $3, $3, $2, $4, $5, $6, $7,
                               $8, $9, $10, $11, $12, $13, $13,
                               '{"source_system": "import_real_documents.py"}'::jsonb)""",
                    s.get("org"), ref, d["certificate_type_code"], s["scope"],
                    s.get("building_id"), s.get("vendor_id"), s.get("country"),
                    d["issue_date"], d["expiry_date"], d["next_due_date"],
                    d["status"] or "Current", d["issuer"], d["document_id"])
            else:
                await conn.execute(
                    """UPDATE plenum_cafm.compliance_certificates
                          SET document_id = $2, source_document_id = $2
                        WHERE id = $1""", s["cert_id"], d["document_id"])


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", default=DEFAULT_FROM,
                    help="source DSN — a database whose documents carry blob_urls")
    ap.add_argument("--to", dest="dst", required=True, help="target DSN — the test database")
    ap.add_argument("--apply", action="store_true", help="write; without it, plan only")
    args = ap.parse_args()

    src = await read_source(args.src)
    print(f"source: {len(src)} documents with a file behind them")
    if not src:
        sys.exit("nothing to copy")

    conn = await asyncpg.connect(args.dst, ssl=_ssl_for(args.dst))
    try:
        steps = await plan(src, conn)
        for s in steps:
            d = s["doc"]
            name = re.sub(r"^(?:[0-9a-f]{8,}[-_])+", "", str(d["file_name"]))
            print(f"  {str(d['certificate_type_code']):26} {name[:46]:46} "
                  f"{s.get('skip') or s['why']}")
        if not args.apply:
            print("\nplan only — pass --apply to write")
            return
        await apply(steps, conn)
        held, total = await conn.fetchrow(
            """SELECT count(*) FILTER (WHERE NULLIF(blob_url,'') IS NOT NULL), count(*)
                 FROM plenum_cafm.documents""")
        certs, evidenced = await conn.fetchrow(
            """SELECT count(*), count(*) FILTER (WHERE COALESCE(document_id,
                                                               source_document_id) IS NOT NULL)
                 FROM plenum_cafm.compliance_certificates""")
        print(f"\ndocuments:    {total} rows, {held} now hold a file")
        print(f"certificates: {certs} rows, {evidenced} evidenced, "
              f"{certs - evidenced} still with no document on file")
    finally:
        await conn.close()


asyncio.run(main())
