"""Give Bishopsgate Tower's certificates their files: upload the test set's PDFs and link each.

The single workbook migrates the certificates as rows, so the compliance vault shows every one
as "No file" - the PDFs exist only in the test set (build_bishopsgate_test_set.py). This uploads
each certificate's PDF to Azure Blob (the storage account in the repo's .env, the container the
app's own uploads use, under hoistra-test/bishopsgate/) and links it the way an upload is
linked: an ingestion_documents row holding the blob, a documents row that says whose it is (the
building, and the company), and the certificate's document_id.

Nothing is linked on a filename's say-so. A PDF is linked only when its text carries its
certificate's number and the row's issue and expiry dates (dd/mm/yyyy); any disagreement is
reported and skipped, so the vault never serves a document that contradicts its certificate.
(The one Bishopsgate file already in the document store, an EPC uploaded on 28 Sep, is a day
out on both dates - it came from an earlier build - and is left unlinked.)

These are invented test certificates; each carries the test set's mark in its PDF metadata.
Keyed by uuid5, so a second run re-uploads to the same blob and changes nothing else. Dry run by
default; --apply uploads and links. hoistra_test only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SVC = os.path.join(HERE, "..", "..", "apps", "backend", "cafm-connector-service-final", "svc-operations-intelligence")
REPO_ENV = os.path.join(HERE, "..", "..", ".env")
NS = uuid.UUID("9b2f5e61-0c3a-4d8e-b7a4-5f1c2e9d8a03")
CONTAINER = "plenum-agentic-ai-attachments"
PREFIX = "hoistra-test/bishopsgate"
SET = r"C:\Users\balap\Documents\test_data\plenum_technologies"


def connection_string() -> str:
    v = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not v and os.path.exists(REPO_ENV):
        for line in open(REPO_ENV, encoding="utf-8"):
            if line.startswith("AZURE_STORAGE_CONNECTION_STRING="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not v:
        raise SystemExit("AZURE_STORAGE_CONNECTION_STRING is not set and not in the repo's .env")
    return v


def pdf_text(path: str) -> str:
    from pypdf import PdfReader

    return " ".join((p.extract_text() or "") for p in PdfReader(path).pages)


def dmy(d) -> str | None:
    return d.strftime("%d/%m/%Y") if d else None


def find_file(name: str) -> str | None:
    for root, _dirs, files in os.walk(os.path.join(SET, "certificates")):
        if name in files:
            return os.path.join(root, name)
    return None


async def main() -> None:
    from _env import hoistra_test_dsn
    import asyncpg

    ap = argparse.ArgumentParser()
    ap.add_argument("--org-name", default="Plenum Technologies")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    manifest = json.load(open(os.path.join(SET, "manifest.json"), encoding="utf-8"))
    dsn = hoistra_test_dsn()
    c = await asyncpg.connect(dsn.replace("postgresql+asyncpg", "postgresql"), timeout=15)
    try:
        if await c.fetchval("SELECT current_database()") != "hoistra_test":
            raise SystemExit("refusing: hoistra_test only")
        org = await c.fetchval("SELECT id FROM plenum_cafm.organizations WHERE lower(name) = lower($1)", args.org_name)
        bld = await c.fetchval("SELECT building_id FROM plenum_cafm.buildings WHERE building_code = 'B-301' AND organization_id = $1", org)
        rows = {r["certificate_number"]: dict(r) for r in await c.fetch(
            """SELECT id, certificate_number, certificate_type_code, issue_date, expiry_date, document_id
                 FROM plenum_cafm.compliance_certificates WHERE organization_id = $1""", org)}
        plan, skipped = [], []
        for m in manifest["certificates"]:
            num = m["number"]
            path = find_file(os.path.basename(m["path"].replace("\\", "/")))
            row = rows.get(num)
            if not row or not path:
                skipped.append((num, "no certificate row" if not row else "no PDF in the test set"))
                continue
            text = pdf_text(path)
            missing = [what for what, val in (("number", num), ("issue date", dmy(row["issue_date"])),
                                              ("expiry date", dmy(row["expiry_date"]))) if val and val not in text]
            if missing:
                skipped.append((num, "PDF does not carry the row's " + ", ".join(missing)))
                continue
            plan.append((num, path, row))
        print(f"  {len(plan)} to upload and link, {len(skipped)} skipped")
        for num, why in skipped:
            print(f"    skip {num}: {why}")
        if not args.apply:
            print("\n  dry run - pass --apply to upload and link")
            return

        from azure.storage.blob import BlobServiceClient, ContentSettings

        svc = BlobServiceClient.from_connection_string(connection_string())
        account = svc.account_name
        box = svc.get_container_client(CONTAINER)
        for num, path, row in plan:
            doc_id = uuid.uuid5(NS, f"document:{org}:{num}")
            name = os.path.basename(path)
            blob = f"{PREFIX}/{doc_id}/{name}"
            with open(path, "rb") as fh:
                data = fh.read()
            box.upload_blob(blob, data, overwrite=True, content_settings=ContentSettings(content_type="application/pdf"))
            url = f"https://{account}.blob.core.windows.net/{CONTAINER}/{blob}"
            async with c.transaction():
                await c.execute(
                    """INSERT INTO plenum_cafm.ingestion_documents (id, source_type, agent_id, original_filename, blob_url,
                              status, tokens_in, tokens_out, cache_read_tokens, cost_usd, processing_ms, uploaded_at,
                              mime_type, document_type, updated_at)
                       VALUES ($1, 'document', 'test-set', $2, $3, 'stored', 0, 0, 0, 0, 0, now(), 'application/pdf',
                               'compliance_certificate', now())
                       ON CONFLICT (id) DO UPDATE SET blob_url = EXCLUDED.blob_url, updated_at = now()""",
                    doc_id, name, url)
                await c.execute(
                    """INSERT INTO plenum_cafm.documents (document_id, building_id, organization_id, doc_type, title, file_name,
                              blob_url, uploaded_at, created_at)
                       VALUES ($1, $2, $3, 'compliance_certificate', $4, $5, $6, now(), now())
                       ON CONFLICT (document_id) DO UPDATE SET blob_url = EXCLUDED.blob_url, file_name = EXCLUDED.file_name""",
                    doc_id, bld, org, row["certificate_type_code"], name, url)
                await c.execute(
                    """UPDATE plenum_cafm.compliance_certificates
                          SET document_id = $2, source_document_id = $2,
                              raw_metadata = coalesce(raw_metadata, '{}'::jsonb) || $3::jsonb, updated_at = now()
                        WHERE id = $1""",
                    row["id"], doc_id, json.dumps({"file_name": name, "document_id": str(doc_id),
                                                   "document_source": "test set PDF, Azure Blob (hoistra-test/bishopsgate)"}))
        print(f"\n  uploaded and linked {len(plan)} to {account}/{CONTAINER}/{PREFIX}/")
    finally:
        await c.close()

    os.environ["DB_URL"] = dsn if "+asyncpg" in dsn else dsn.replace("postgresql://", "postgresql+asyncpg://")
    sys.path.insert(0, os.path.abspath(SVC))
    from src.db import AsyncSessionLocal
    from src.engines.compliance.certificate_file import _first_document
    from src.models.compliance import ComplianceCertificate

    served = 0
    for num, _path, row in plan:
        async with AsyncSessionLocal() as s:
            found = await _first_document(s, await s.get(ComplianceCertificate, row["id"]))
            served += 1 if found and found.get("blob_url") else 0
    print(f"  the vault's download resolves {served} of {len(plan)} to their file")


if __name__ == "__main__":
    asyncio.run(main())
