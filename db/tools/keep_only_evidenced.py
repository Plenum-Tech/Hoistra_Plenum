"""Keep the records with a real document behind them; remove the rest.

After ``replace_fabricated_documents.py`` the portfolio still carried two kinds of record that
nothing backs: certificates with no document at all, and invoices whose document was one of
the invented ones. Both render honestly now — "no document", "no file" — but honest about
being invented is still invented, and a test portfolio made mostly of them is not testing
much.

This removes them. What survives is what a real deployment would have: a certificate that was
read off a document we hold, and a contract with its contract.

    python db/tools/keep_only_evidenced.py --to <dsn>
    python db/tools/keep_only_evidenced.py --to <dsn> --apply

Without --apply it counts and writes nothing.

Two things to know before running it.

**It is not selective about which gap it removes.** A certificate with no document is often
a real finding — the certificate exists, nothing was ever filed against it — and that finding
is what the compliance register is for. Deleting these makes the register smaller and
tidier, and blind to that particular problem. In this portfolio they were all invented, so
there was no finding to lose; in a database with real records there would be.

**The invoices branch goes empty.** plenum_cafm.invoices is a view over invoice_verifications
joined to documents, and it reaches its building through the document — so deleting the
document does not leave a buildingless invoice, it removes the invoice from everywhere. The
verifications and their lines go too rather than being left as rows nothing can reach.

Everything here is recoverable: db/04_demo_data.sql rebuilds the portfolio, and the two
import tools re-attach the real documents.
"""
from __future__ import annotations

import argparse
import asyncio
import re
from typing import Any

import asyncpg

# A row is kept when a document with a file stands behind it. Written once, used for both the
# count and the delete, so the number reported is the number removed.
UNEVIDENCED_CERTS = """
    COALESCE(document_id, source_document_id) IS NULL
"""
DOC_HAS_NO_FILE = """
    NULLIF(blob_url, '') IS NULL
"""
IV_WITHOUT_A_REAL_DOC = """
    NOT EXISTS (SELECT 1 FROM plenum_cafm.documents d
                 WHERE d.document_id = v.document_id
                   AND NULLIF(d.blob_url, '') IS NOT NULL)
"""


def _ssl_for(dsn: str) -> Any:
    host = re.search(r"@([^/:]+)", dsn)
    return "require" if host and not host.group(1).startswith(("localhost", "127.")) else False


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", dest="dst", required=True, help="the test database")
    ap.add_argument("--apply", action="store_true", help="write; without it, count only")
    args = ap.parse_args()

    conn = await asyncpg.connect(args.dst, ssl=_ssl_for(args.dst))
    try:
        certs = await conn.fetchval(
            f"SELECT count(*) FROM plenum_cafm.compliance_certificates "
            f"WHERE {UNEVIDENCED_CERTS}")
        docs = await conn.fetchval(
            f"SELECT count(*) FROM plenum_cafm.documents WHERE {DOC_HAS_NO_FILE}")
        ivs = await conn.fetchval(
            f"SELECT count(*) FROM plenum_cafm.invoice_verifications v "
            f"WHERE {IV_WITHOUT_A_REAL_DOC}")
        lines = await conn.fetchval(
            f"""SELECT count(*) FROM plenum_cafm.invoice_lines l
                 WHERE EXISTS (SELECT 1 FROM plenum_cafm.invoice_verifications v
                                WHERE v.id = l.invoice_verification_id
                                  AND {IV_WITHOUT_A_REAL_DOC})""")
        print(f"certificates with no document   {certs}")
        print(f"documents with no file          {docs}")
        print(f"invoice_verifications unbacked  {ivs}")
        print(f"  their invoice_lines           {lines}")

        if not args.apply:
            print("\ncount only — pass --apply to delete")
            return

        async with conn.transaction():
            # Children first, then their parents, then the documents both hung off.
            await conn.execute(
                f"""DELETE FROM plenum_cafm.invoice_lines l
                     WHERE EXISTS (SELECT 1 FROM plenum_cafm.invoice_verifications v
                                    WHERE v.id = l.invoice_verification_id
                                      AND {IV_WITHOUT_A_REAL_DOC})""")
            await conn.execute(
                f"DELETE FROM plenum_cafm.invoice_verifications v "
                f"WHERE {IV_WITHOUT_A_REAL_DOC}")
            await conn.execute(
                f"DELETE FROM plenum_cafm.compliance_certificates "
                f"WHERE {UNEVIDENCED_CERTS}")
            await conn.execute(
                f"DELETE FROM plenum_cafm.documents WHERE {DOC_HAS_NO_FILE}")
            # ingestion_documents rows for files that no longer exist would leave the
            # download route serving a document nothing in the graph refers to.
            await conn.execute(
                """DELETE FROM plenum_cafm.ingestion_documents i
                    WHERE NOT EXISTS (SELECT 1 FROM plenum_cafm.documents d
                                       WHERE d.document_id = i.id)""")

        after = await conn.fetchrow(
            """SELECT (SELECT count(*) FROM plenum_cafm.documents) AS docs,
                      (SELECT count(*) FROM plenum_cafm.compliance_certificates) AS certs,
                      (SELECT count(*) FROM plenum_cafm.contract_sla_parameters) AS contracts,
                      (SELECT count(*) FROM plenum_cafm.invoice_verifications) AS invoices,
                      (SELECT count(*) FROM plenum_cafm.ingestion_documents) AS servable""")
        print(f"\nremaining: {after['docs']} documents, {after['certs']} certificates, "
              f"{after['contracts']} contracts, {after['invoices']} invoices")
        print(f"           {after['servable']} of them servable by the download route")
        left = await conn.fetchval(
            """SELECT count(*) FROM plenum_cafm.documents
                WHERE NULLIF(blob_url,'') IS NULL""")
        print(f"           {left} documents without a file (should be 0)")
    finally:
        await conn.close()


# Guarded so the predicates above can be imported and tested without the script running.
if __name__ == "__main__":
    asyncio.run(main())
