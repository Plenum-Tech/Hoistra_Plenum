"""Take the invented documents out, and put real ones where a real one fits.

``04_demo_data.sql`` mints a document row per certificate, contract and invoice so the graph
has the shape a real deployment has. None of them has a file: the names — CP17-B-005-002.pdf,
INV-SENT-202601.pdf — describe PDFs that do not exist anywhere. The panel now says so on every
row, which is an improvement on pretending otherwise, but a portfolio that is 56/74 invented
is not much of a test.

Three different answers, because the three kinds are not in the same position:

  certificates  DELETED. There are 221 real certificate PDFs to draw on and they are the
                wrong types — ISO 9001, BAFE, EICR — for the certificates these rows
                evidence. Putting an EICR pdf behind a CP17 record would be a worse lie than
                the empty row, so the row goes and the certificate reads "no document",
                which is what it always was.

  contracts     SWAPPED for a real contract PDF. Contract for contract is a genuine match:
                the row keeps its id, so the contract it anchors keeps its link, and gains a
                file that opens.

  invoices      LEFT ALONE, and this is the one thing here that stays invented. Production
                holds no invoice PDFs at all, and deleting these rows would take invoices out
                of every building drawer — plenum_cafm.invoices is a view that reaches its
                building through the document. A record with no file, saying so, beats no
                record.

    python db/tools/replace_fabricated_documents.py --from <dsn> --to <dsn>
    python db/tools/replace_fabricated_documents.py --from <dsn> --to <dsn> --apply

Without --apply it prints the plan and writes nothing. Re-running is safe: a row that already
holds a file is not fabricated any more and is skipped.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from typing import Any

import asyncpg

DEFAULT_FROM = "postgresql://cafm:cafm@localhost:5432/hoistra"


def _ssl_for(dsn: str) -> Any:
    host = re.search(r"@([^/:]+)", dsn)
    return "require" if host and not host.group(1).startswith(("localhost", "127.")) else False


def looks_like_a_contract(name: str) -> bool:
    """A contract, not a contractor.

    The first version asked whether "contract" appeared in the name and matched
    CONTRACTOR_PL_INSURANCE — a contractor's public liability certificate, which is a
    certificate. It would have put six insurance certificates behind six maintenance
    contracts: the exact mismatch this tool refuses to make with certificates, arrived at by
    substring rather than on purpose. The word has to be the word.
    """
    n = (name or "").lower()
    if "invoice" in n or re.search(r"\binv[-_]", n):
        return False
    if "contractor" in n or "insuran" in n or "_coi_" in n:
        return False
    # Not \b: underscore is a word character, so \bcontract\b never matches
    # UKRI-2938_FM_Contract_Gough-and-Kelly.pdf, which is the clearest contract in the
    # set. The separators that matter here are the ones filenames actually use.
    return bool(re.search(r"(?:^|[^a-z])contract(?:[^a-z]|$)", n)
                or "framework" in n)


async def real_contracts(dsn: str) -> list[dict[str, Any]]:
    """Distinct contract PDFs that the download route would actually serve.

    Not filtered on status. status describes what the extraction pipeline made of the file —
    indexed, extracting, error — and the download route never looks at it: it redirects to
    blob_url. Every genuine contract in this set is 'error' or 'extracting', so filtering on
    'indexed' returned nothing while nine real, openable contracts sat there. What matters
    is whether there is a file, and that is the WHERE clause.
    """
    conn = await asyncpg.connect(dsn, ssl=_ssl_for(dsn))
    try:
        rows = await conn.fetch(
            """SELECT DISTINCT ON (blob_url) id, original_filename, blob_url
                 FROM plenum_cafm.ingestion_documents
                WHERE NULLIF(blob_url, '') IS NOT NULL
                ORDER BY blob_url, id""")
        return [dict(r) for r in rows if looks_like_a_contract(r["original_filename"])]
    finally:
        await conn.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", default=DEFAULT_FROM,
                    help="a database whose ingestion_documents carry real blob_urls")
    ap.add_argument("--to", dest="dst", required=True, help="the test database")
    ap.add_argument("--apply", action="store_true", help="write; without it, plan only")
    args = ap.parse_args()

    pool = await real_contracts(args.src)
    conn = await asyncpg.connect(args.dst, ssl=_ssl_for(args.dst))
    try:
        fabricated = await conn.fetch(
            """SELECT document_id, doc_type, file_name FROM plenum_cafm.documents
                WHERE NULLIF(blob_url, '') IS NULL
                ORDER BY doc_type, file_name""")
        certs = [r for r in fabricated if r["doc_type"] == "compliance_certificate"]
        contracts = [r for r in fabricated if r["doc_type"] == "contract"]
        invoices = [r for r in fabricated if r["doc_type"] == "invoice"]

        print(f"fabricated rows: {len(fabricated)}"
              f"  ({len(certs)} certificate, {len(contracts)} contract, "
              f"{len(invoices)} invoice)")
        print(f"real contract PDFs available: {len(pool)}")

        print(f"\nDELETE {len(certs)} certificate documents — the certificates they evidence")
        print("       become 'no document', which is what they always were")
        print(f"SWAP   {min(len(contracts), len(pool))} contract documents for a real "
              f"contract PDF")
        for row, real in zip(contracts, pool):
            short = re.sub(r"^[0-9a-f]{8,}[-_]", "", str(real["original_filename"]))
            print(f"         {str(row['file_name'])[:24]:26} -> {short[:52]}")
        if len(contracts) > len(pool):
            print(f"       {len(contracts) - len(pool)} left as records: the pool ran out")
        print(f"KEEP   {len(invoices)} invoice documents as records with no file — there are")
        print("       no invoice PDFs to substitute, and deleting them would take invoices")
        print("       out of every building drawer")

        if not args.apply:
            print("\nplan only — pass --apply to write")
            return

        async with conn.transaction():
            ids = [r["document_id"] for r in certs]
            if ids:
                # The reference goes first. A certificate pointing at a document that is
                # about to stop existing is a dangling id, and the drawer would read it as
                # "there is a document here" right up until it tried to open one.
                await conn.execute(
                    """UPDATE plenum_cafm.compliance_certificates
                          SET document_id = NULL, source_document_id = NULL
                        WHERE COALESCE(document_id, source_document_id) = ANY($1::uuid[])""",
                    ids)
                await conn.execute(
                    "DELETE FROM plenum_cafm.ingestion_documents WHERE id = ANY($1::uuid[])",
                    ids)
                await conn.execute(
                    "DELETE FROM plenum_cafm.documents WHERE document_id = ANY($1::uuid[])",
                    ids)

            for row, real in zip(contracts, pool):
                # The row keeps its id so the contract it anchors keeps its link, and gains
                # a real name and a real file.
                await conn.execute(
                    """UPDATE plenum_cafm.documents
                          SET file_name = $2, blob_url = $3, title = COALESCE(title, $2)
                        WHERE document_id = $1""",
                    row["document_id"], real["original_filename"], real["blob_url"])
                await conn.execute(
                    """INSERT INTO plenum_cafm.ingestion_documents
                           (id, source_type, agent_id, original_filename, blob_url, status,
                            uploaded_at)
                       VALUES ($1, 'pdf', 'replace_fabricated_documents', $2, $3, 'indexed',
                               now())
                       ON CONFLICT (id) DO UPDATE
                          SET original_filename = EXCLUDED.original_filename,
                              blob_url          = EXCLUDED.blob_url""",
                    row["document_id"], real["original_filename"], real["blob_url"])

        d = await conn.fetchrow(
            """SELECT count(*) AS n,
                      count(*) FILTER (WHERE NULLIF(blob_url,'') IS NOT NULL) AS held
                 FROM plenum_cafm.documents""")
        c = await conn.fetchrow(
            """SELECT count(*) AS n,
                      count(*) FILTER (WHERE COALESCE(document_id, source_document_id)
                                             IS NOT NULL) AS ev
                 FROM plenum_cafm.compliance_certificates""")
        print(f"\ndocuments:    {d['n']} rows, {d['held']} hold a file, "
              f"{d['n'] - d['held']} still records only")
        print(f"certificates: {c['n']} rows, {c['ev']} evidenced, {c['n'] - c['ev']} "
              f"with no document")
    finally:
        await conn.close()


# Guarded so the helpers above can be imported and tested without the script
# running itself — which it did, the first time something tried.
if __name__ == "__main__":
    asyncio.run(main())
