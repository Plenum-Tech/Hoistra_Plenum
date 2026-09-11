"""Collapse documents that are the same file filed more than once.

plenum_cafm.documents is keyed on document_id, which doc-rag mints fresh for every upload,
so re-uploading one file always made a second document for the same bytes. Riverside Court
listed one invoice four times; Town Hall listed one certificate eight times.

Going forward the uploader records file_hash_sha256 and collapses on it at ingest. This
cleans up what was written before that existed — rows whose hash is NULL and can never be
computed, because the files are long gone from disk.

Grouping, in order:
  1. file_hash_sha256, when both rows have one.
  2. otherwise the original filename, with the "{session}_" prefix stripped, within one
     building. Deliberately NOT split by doc_type: that is a derived classification and an
     unstable one — the same certificate has been filed as `compliance_certificate` on one
     upload and left NULL on another — so keying on it splits one file into two documents
     for a reason that says nothing about the file.

Rule 2 is a migration aid, not the ongoing rule: two genuinely different files can share a
name, and this would merge them. It is scoped to one building to make that unlikely, and
--dry-run prints every group so a person decides before anything is deleted.

The earliest row survives and its gaps are filled from the ones it absorbs, so a duplicate
is never traded for a document that has lost its file. Nothing declares a foreign key to
documents, so every soft reference is re-pointed by name first.

    python db/tools/collapse_duplicate_documents.py --to <dsn>            # dry run
    python db/tools/collapse_duplicate_documents.py --to <dsn> --apply

Dry run by default. --apply is the only thing that writes.
"""
from __future__ import annotations

import argparse
import asyncio
import re
from typing import Any

import asyncpg

#: Columns that point at a document without declaring a foreign key. Kept in step with
#: svc-deepagents/src/services/building_binding.py:_DOCUMENT_REFERENCES.
#: udr_run_versions.document_ids is an array and is deliberately absent: rewriting one
#: element of a recorded run would edit history rather than repair a link.
REFERENCES: tuple[tuple[str, str], ...] = (
    ("compliance_certificates", "document_id"),
    ("compliance_certificates", "source_document_id"),
    ("compliance_vector_membership_audit", "document_id"),
    ("contract_documents", "document_id"),
    ("contract_sla_parameters", "document_id"),
    ("invoice_verifications", "document_id"),
    ("ppm_visits", "source_document_id"),
)

SESSION_PREFIX = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_"

GROUPS_SQL = f"""
    SELECT d.building_id::text                                   AS building_id,
           COALESCE(b.name, '(no building)')                     AS building_name,
           COALESCE(
               i.file_hash_sha256,
               'name:' || regexp_replace(d.file_name, '{SESSION_PREFIX}', '')
           )                                                     AS group_key,
           (i.file_hash_sha256 IS NOT NULL)                      AS by_hash,
           regexp_replace(d.file_name, '{SESSION_PREFIX}', '')    AS original_name,
           array_agg(d.document_id::text
                     ORDER BY d.uploaded_at NULLS LAST, d.document_id) AS ids
      FROM plenum_cafm.documents d
      LEFT JOIN plenum_cafm.buildings b ON b.building_id = d.building_id
      LEFT JOIN plenum_cafm.ingestion_documents i ON i.id = d.document_id
     WHERE d.file_name IS NOT NULL
     GROUP BY d.building_id, b.name, 3, 4, 5
    HAVING count(*) > 1
     ORDER BY 2, 5
"""

_SAFE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _ident(name: str) -> str:
    if not _SAFE.match(name or ""):
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


def _ssl_for(dsn: str) -> Any:
    host = re.search(r"@([^/:]+)", dsn)
    return "require" if host and not host.group(1).startswith(("localhost", "127.")) else False


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--to", required=True, help="Postgres DSN")
    ap.add_argument("--apply", action="store_true", help="Write. Without it, nothing changes.")
    args = ap.parse_args()

    conn = await asyncpg.connect(args.to, ssl=_ssl_for(args.to))
    try:
        present = {
            (r["table_name"], r["column_name"])
            for r in await conn.fetch(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'plenum_cafm'"
            )
        }
        columns = [(t, c) for t, c in REFERENCES if (t, c) in present]
        missing = [f"{t}.{c}" for t, c in REFERENCES if (t, c) not in present]
        if missing:
            print(f"not in this database, skipped: {', '.join(missing)}")

        groups = await conn.fetch(GROUPS_SQL)
        total_before = await conn.fetchval("SELECT count(*) FROM plenum_cafm.documents")
        if not groups:
            print(f"{total_before} documents, no duplicates.")
            return

        print(f"{total_before} documents; {len(groups)} group(s) hold more than one copy:\n")
        removed = repointed = 0
        for g in groups:
            ids = [str(i) for i in g["ids"]]
            keep, drop = ids[0], ids[1:]
            how = "hash" if g["by_hash"] else "name"
            print(f"  {g['building_name']:<20} {g['original_name'][:44]:<44} "
                  f"x{len(ids)}  [{how}]")
            print(f"      keep {keep[:8]}   drop {', '.join(i[:8] for i in drop)}")
            if not args.apply:
                continue
            async with conn.transaction():
                await conn.execute(
                    """UPDATE plenum_cafm.documents k
                          SET blob_url  = COALESCE(k.blob_url,  f.blob_url),
                              doc_type  = COALESCE(k.doc_type,  f.doc_type),
                              title     = COALESCE(k.title,     f.title),
                              file_name = COALESCE(k.file_name, f.file_name)
                         FROM (SELECT max(blob_url) AS blob_url, max(doc_type) AS doc_type,
                                      max(title) AS title, max(file_name) AS file_name
                                 FROM plenum_cafm.documents
                                WHERE document_id::text = ANY($1)) f
                        WHERE k.document_id::text = $2""",
                    drop, keep,
                )
                for table, column in columns:
                    tag = await conn.execute(
                        f"UPDATE plenum_cafm.{_ident(table)} "
                        f"SET {_ident(column)} = $1::uuid WHERE {_ident(column)}::text = ANY($2)",
                        keep, drop,
                    )
                    repointed += int(tag.rsplit(" ", 1)[-1] or 0)
                tag = await conn.execute(
                    "DELETE FROM plenum_cafm.documents WHERE document_id::text = ANY($1)",
                    drop,
                )
                removed += int(tag.rsplit(" ", 1)[-1] or 0)

        print()
        if args.apply:
            after = await conn.fetchval("SELECT count(*) FROM plenum_cafm.documents")
            print(f"removed {removed} duplicate document(s), re-pointed {repointed} "
                  f"reference(s). {total_before} -> {after}.")
        else:
            would = sum(len(g["ids"]) - 1 for g in groups)
            print(f"dry run — nothing written. --apply would remove {would} document(s).")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
