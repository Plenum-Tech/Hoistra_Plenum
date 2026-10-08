"""Link the genuine certificates in hoistra_test to their original PDFs in Azure Blob.

seed_genuine_energy_certificates.py and seed_genuine_vendor_accreditations.py file seven genuine
certificates under the test company without their documents, so the compliance console reads
"No file". Six of the originals were uploaded to plenum_agent, where plenum_cafm.documents holds
each one's blob in plenumstorage / plenum-agentic-ai-attachments. This gives hoistra_test the same
document rows - the same id and the same blob, nothing copied or re-uploaded - and points each
certificate at its document, so the vault's download serves the original through the service
(engines/compliance/certificate_file.py) with the caller's scope checked.

That resolver serves a document only when its row proves it is the certificate's company's: by
naming the company, or by naming the certificate's own building. The energy certificates carry
their building. The vendor accreditations have none, and hoistra_test's documents table had no
company column, so this adds organization_id (nullable; the resolver already reads it when a
tenant has it) and sets it.

The Washington contractor licence came from the state's public data, not an upload: it has no
document and keeps "No file". Reads plenum_agent in a read-only transaction. Dry run by default;
--apply writes. hoistra_test only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SVC = os.path.join(HERE, "..", "..", "apps", "backend", "cafm-connector-service-final", "svc-operations-intelligence")
NUMBERS = ["8835-5966-2832-1595-3906", "9920-1010-0626-0890-2091", "104293", "104021", "101599", "052305041"]


async def main() -> None:
    from _env import hoistra_test_dsn
    import asyncpg

    ap = argparse.ArgumentParser()
    ap.add_argument("--org-name", default="Plenum Technologies")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    dsn = hoistra_test_dsn()
    base = dsn.replace("postgresql+asyncpg", "postgresql")
    c = await asyncpg.connect(base, timeout=15)
    src = await asyncpg.connect(re.sub(r"/hoistra_test(\?|$)", r"/plenum_agent\1", base), timeout=15)
    try:
        if await c.fetchval("SELECT current_database()") != "hoistra_test":
            raise SystemExit("refusing: hoistra_test only")
        org = await c.fetchval("SELECT id FROM plenum_cafm.organizations WHERE lower(name) = lower($1)", args.org_name)
        async with src.transaction(readonly=True):
            docs = {r["certificate_number"]: dict(r) for r in await src.fetch(
                """SELECT c.certificate_number, d.document_id, d.doc_type, d.title, d.file_name, d.blob_url, d.uploaded_at
                     FROM plenum_cafm.compliance_certificates c
                     JOIN plenum_cafm.documents d ON d.document_id = c.document_id
                    WHERE c.certificate_number = ANY($1::text[]) AND d.blob_url IS NOT NULL""", NUMBERS)}
        certs = {r["certificate_number"]: dict(r) for r in await c.fetch(
            """SELECT id, certificate_number, certificate_type_code, building_id, vendor_name, building_name
                 FROM plenum_cafm.compliance_certificates
                WHERE organization_id = $1 AND certificate_number = ANY($2::text[])""", org, NUMBERS)}
        plan = []
        for num in NUMBERS:
            d, k = docs.get(num), certs.get(num)
            if not d or not k:
                print(f"  {num}: {'no document in plenum_agent' if not d else 'no certificate in hoistra_test'} - skipped")
                continue
            plan.append((num, d, k))
            print(f"  {k['certificate_type_code']} {num} ({k['building_name'] or k['vendor_name']}) -> "
                  f"{d['file_name']} (document {d['document_id']})")
        if not args.apply:
            print("\n  dry run - pass --apply to link them")
            return
        async with c.transaction():
            await c.execute("ALTER TABLE plenum_cafm.documents ADD COLUMN IF NOT EXISTS organization_id uuid")
            for num, d, k in plan:
                await c.execute(
                    """INSERT INTO plenum_cafm.documents (document_id, building_id, organization_id, doc_type, title, file_name,
                              blob_url, uploaded_at, created_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
                       ON CONFLICT (document_id) DO UPDATE SET building_id = EXCLUDED.building_id,
                              organization_id = EXCLUDED.organization_id, blob_url = EXCLUDED.blob_url,
                              file_name = EXCLUDED.file_name""",
                    d["document_id"], k["building_id"], org, d["doc_type"], d["title"], d["file_name"], d["blob_url"],
                    d["uploaded_at"])
                await c.execute(
                    """UPDATE plenum_cafm.compliance_certificates
                          SET document_id = $2, source_document_id = $2,
                              raw_metadata = coalesce(raw_metadata, '{}'::jsonb) || $3::jsonb, updated_at = now()
                        WHERE id = $1""",
                    k["id"], d["document_id"], json.dumps({"file_name": d["file_name"], "document_id": str(d["document_id"]),
                                                           "document_source": "original upload, Azure Blob (plenumstorage)"}))
        print(f"\n  linked {len(plan)}")
    finally:
        await c.close()
        await src.close()

    # The resolver the vault's download uses: does each certificate now reach its own file?
    os.environ["DB_URL"] = dsn if "+asyncpg" in dsn else dsn.replace("postgresql://", "postgresql+asyncpg://")
    sys.path.insert(0, os.path.abspath(SVC))
    from src.db import AsyncSessionLocal
    from src.engines.compliance.certificate_file import _first_document
    from src.models.compliance import ComplianceCertificate

    for num, _d, k in plan:
        async with AsyncSessionLocal() as s:
            cert = await s.get(ComplianceCertificate, k["id"])
            try:
                found = await _first_document(s, cert)
                print(f"  download {num}: " + (f"serves {found['name']} from {found['table']}" if found and found.get("blob_url")
                                              else "no file found"))
            except Exception as exc:  # noqa: BLE001
                print(f"  download {num}: refused - {exc}")


if __name__ == "__main__":
    asyncio.run(main())
