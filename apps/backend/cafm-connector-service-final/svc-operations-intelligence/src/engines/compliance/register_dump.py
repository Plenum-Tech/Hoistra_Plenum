"""CCC §8.2 — ingest weekly register dumps into local verification rows."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from .verification_sources import canonicalize_type_code

log = get_logger(__name__)


async def lookup_register_row(
    session: AsyncSession,
    *,
    certificate_type_code: str | None,
    lookup_key: str | None,
) -> dict[str, Any] | None:
    code = canonicalize_type_code(certificate_type_code)
    key = (lookup_key or "").strip().upper()
    if not code or not key:
        return None
    row = (
        await session.execute(
            text(
                """
                SELECT id::text, certificate_type_code, lookup_key, payload, source_file,
                       ingested_at
                FROM plenum_cafm.compliance_verification_register_rows
                WHERE certificate_type_code = :code
                  AND upper(lookup_key) = :key
                ORDER BY ingested_at DESC
                LIMIT 1
                """
            ),
            {"code": code, "key": key},
        )
    ).mappings().first()
    if not row:
        return None
    return dict(row)


def _row_lookup_key(r: dict[str, Any]) -> str:
    return str(
        r.get("lookup_key")
        or r.get("registration_number")
        or r.get("licence_number")
        or r.get("license_number")
        or r.get("member_number")
        or r.get("company_number")
        or r.get("acs_number")
        or r.get("id")
        or ""
    ).strip()


async def ingest_dump_payload(
    session: AsyncSession,
    *,
    certificate_type_code: str,
    rows: list[dict[str, Any]],
    source_file: str | None = None,
) -> dict[str, Any]:
    code = canonicalize_type_code(certificate_type_code) or certificate_type_code
    inserted = 0
    upserted = 0
    for r in rows:
        key = _row_lookup_key(r)
        if not key:
            continue
        existing = (
            await session.execute(
                text(
                    """
                    SELECT id::text
                    FROM plenum_cafm.compliance_verification_register_rows
                    WHERE certificate_type_code = :code
                      AND upper(lookup_key) = upper(:key)
                    LIMIT 1
                    """
                ),
                {"code": code, "key": key},
            )
        ).scalar_one_or_none()
        if existing:
            await session.execute(
                text(
                    """
                    UPDATE plenum_cafm.compliance_verification_register_rows
                    SET payload = CAST(:payload AS jsonb),
                        source_file = :source_file,
                        ingested_at = :ingested_at,
                        lookup_key = :key
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {
                    "id": existing,
                    "key": key,
                    "payload": json.dumps(r),
                    "source_file": source_file,
                    "ingested_at": datetime.now(timezone.utc),
                },
            )
            upserted += 1
        else:
            await session.execute(
                text(
                    """
                    INSERT INTO plenum_cafm.compliance_verification_register_rows
                        (id, certificate_type_code, lookup_key, payload, source_file, ingested_at)
                    VALUES
                        (:id, :code, :key, CAST(:payload AS jsonb), :source_file, :ingested_at)
                    """
                ),
                {
                    "id": str(uuid4()),
                    "code": code,
                    "key": key,
                    "payload": json.dumps(r),
                    "source_file": source_file,
                    "ingested_at": datetime.now(timezone.utc),
                },
            )
            inserted += 1
    await session.commit()
    return {
        "ok": True,
        "certificate_type_code": code,
        "inserted": inserted,
        "upserted": upserted,
        "total": inserted + upserted,
    }


async def ingest_dump_text(
    session: AsyncSession,
    *,
    certificate_type_code: str,
    content: str,
    source_file: str | None = None,
    format: str = "auto",
) -> dict[str, Any]:
    fmt = (format or "auto").lower()
    text_s = (content or "").strip()
    if not text_s:
        return {"ok": False, "error": "empty_content"}
    rows: list[dict[str, Any]] = []
    if fmt == "json" or (fmt == "auto" and text_s.startswith(("[", "{"))):
        data = json.loads(text_s)
        if isinstance(data, dict):
            rows = data.get("rows") or data.get("items") or [data]
        else:
            rows = list(data)
    else:
        reader = csv.DictReader(io.StringIO(text_s))
        rows = [dict(r) for r in reader]
    return await ingest_dump_payload(
        session,
        certificate_type_code=certificate_type_code,
        rows=rows,
        source_file=source_file,
    )


async def weekly_dump_cron(session: AsyncSession) -> dict[str, Any]:
    """Scan VERIFICATION_DUMP_DIR for *.csv / *.json named `{CODE}.*` and ingest."""
    dump_dir = Path(settings.verification_dump_dir or "/app/data/verification_dumps")
    if not dump_dir.exists():
        log.info("register_dump.cron.empty", path=str(dump_dir))
        return {"ok": True, "ingested": 0, "message": "dump dir missing or empty", "path": str(dump_dir)}

    results = []
    for path in sorted(dump_dir.glob("*")):
        if path.suffix.lower() not in {".csv", ".json"}:
            continue
        code = path.stem.split(".")[0].upper()
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            res = await ingest_dump_text(
                session,
                certificate_type_code=code,
                content=content,
                source_file=path.name,
            )
            results.append({"file": path.name, **res})
        except Exception as exc:  # noqa: BLE001
            log.warning("register_dump.cron.file_failed", file=path.name, error=str(exc))
            results.append({"file": path.name, "ok": False, "error": str(exc)[:200]})
    return {
        "ok": True,
        "ingested": sum(int(r.get("total") or r.get("inserted") or 0) for r in results if r.get("ok")),
        "files": results,
        "path": str(dump_dir),
    }
