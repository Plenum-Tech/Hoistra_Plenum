"""Collapse documents that are the same file filed more than once.

plenum_cafm.documents is keyed on document_id, which doc-rag mints fresh for every upload,
so re-uploading one file always made a second document for the same bytes. Riverside Court
listed one invoice four times; Town Hall listed one certificate eight times.

Going forward the uploader records file_hash_sha256 and collapses on it at ingest. This
cleans up what was written before that existed — rows whose hash is NULL and can never be
computed, because the files are long gone from disk.

Two passes, within one building:
  1. by content — rows that have a hash, grouped by it. The same bytes are one document
     however the file was named.
  2. by name — the original filename with the "{session}_" prefix stripped, merging only
     where the group holds at most one distinct hash. count(DISTINCT h) ignores nulls, so a
     row written before hashing existed joins a freshly hashed one, while two rows with
     different hashes are left alone however they are named.

Keying on "hash, else name" instead looks equivalent and is not: it puts an old unhashed row
and a new hashed one in different groups, which is the one pair that matters while the
hashes are being filled in. That version shipped and let a sixth copy of one invoice through.

Neither pass splits on doc_type: that is derived, and unstable enough that the same
certificate has been filed as `compliance_certificate` on one upload and left NULL on
another — keying on it splits one file in two for a reason that says nothing about the file.

Pass 2 can still merge two different files that share a name with no hashes between them.
That is why --dry-run prints every group before anything is deleted.

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

_SELECT = f"""
    SELECT d.building_id::text                                   AS building_id,
           COALESCE(b.name, '(no building)')                     AS building_name,
           regexp_replace(d.file_name, '{SESSION_PREFIX}', '')    AS original_name,
           array_agg(d.document_id::text
                     ORDER BY d.uploaded_at NULLS LAST, d.document_id) AS ids
      FROM plenum_cafm.documents d
      LEFT JOIN plenum_cafm.buildings b ON b.building_id = d.building_id
      LEFT JOIN plenum_cafm.ingestion_documents i ON i.id = d.document_id
     WHERE d.file_name IS NOT NULL
"""

#: Same bytes, whatever the file was called.
BY_CONTENT = _SELECT + """
       AND i.file_hash_sha256 IS NOT NULL
     GROUP BY d.building_id, b.name, 3, i.file_hash_sha256
    HAVING count(*) > 1
     ORDER BY 2, 3
"""

#: Same original name, where nothing proves the files differ.
BY_NAME = _SELECT + """
     GROUP BY d.building_id, b.name, 3
    HAVING count(*) > 1
       AND count(DISTINCT i.file_hash_sha256) <= 1
     ORDER BY 2, 3
"""

PASSES = (("content", BY_CONTENT), ("name", BY_NAME))

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

        total_before = await conn.fetchval("SELECT count(*) FROM plenum_cafm.documents")
        # Content first, so a merge that can be proven from the bytes happens before names
        # are consulted. In a dry run nothing is written, so the second pass still reports
        # the groups the first one would have collapsed — they are listed under both.
        found = [(how, g) for how, sql in PASSES for g in await conn.fetch(sql)]
        if not found:
            print(f"{total_before} documents, no duplicates.")
            return

        print(f"{total_before} documents; {len(found)} group(s) hold more than one copy:\n")
        removed = repointed = 0
        seen: set[str] = set()
        for how, g in found:
            ids = [str(i) for i in g["ids"] if str(i) not in seen]
            if len(ids) < 2:
                continue  # already collapsed by the pass before this one
            keep, drop = ids[0], ids[1:]
            print(f"  {g['building_name']:<20} {g['original_name'][:44]:<44} "
                  f"x{len(ids)}  [{how}]")
            print(f"      keep {keep[:8]}   drop {', '.join(i[:8] for i in drop)}")
            if not args.apply:
                continue
            seen.update(drop)
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
            would = len({str(i) for _, g in found for i in g["ids"][1:]})
            print(f"dry run — nothing written. --apply would remove {would} document(s).")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
