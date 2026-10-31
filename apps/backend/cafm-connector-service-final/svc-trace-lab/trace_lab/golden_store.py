"""Golden sets in Trace Lab's own database: versions, cases, the active set, retire and roll back, and the one
Regression cases set (Phase 3).

A company admin uploads a list of questions with known right answers; golden.parse_upload (Phase 1) reads it whole
and save_version stores it as the company's next version in the SAME transaction that retires the previous active
version, so a company always has at most one active scenario set. Cases are never edited (the table is append-only):
a changed case is a new version of the set. The Regression cases set (kind 'regression', version 0) is created only
on demand (Phase 7) and is never the active set, never retired and never listed among the uploads.

Every function is company-scoped: a set of another company is NotFound, never a hint that it exists. Everything runs
on db.lab_engine() (schema tracelab); nothing here reads or writes the application database.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import text

from . import db, golden

log = structlog.get_logger(__name__)

REGRESSION_SET_NAME = "Regression cases"
CASE_FLAGS = ("stale", "unverified", "objective", "judgment", "write_intent")
MAX_LIST = 100
SYSTEM_ACTOR = "system"


class NotFound(LookupError):
    """The set is not this company's (or does not exist). Routes answer 404."""


class Conflict(Exception):
    """The change is not allowed on this set (for example retiring the Regression cases set). Routes answer 409."""


# ── row shapes ─────────────────────────────────────────────────────────────────────────────

def _plain(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, Decimal):
        return float(v)
    return v


def _jsonish(v: Any, default: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return default
    return default if v is None else v


def set_dict(row: Any) -> dict[str, Any]:
    d = {k: _plain(v) for k, v in dict(row).items()}
    d["warnings"] = _jsonish(d.get("warnings"), [])
    d["diff"] = _jsonish(d.get("diff"), {})
    return d


def case_dict(row: Any) -> dict[str, Any]:
    """A case as the runner and the grader read it: followups and expected_values as lists, verified_on as an ISO
    date string (or None), requirement_ids and flags as lists."""
    d = {k: _plain(v) for k, v in dict(row).items()}
    d["followups"] = list(_jsonish(d.get("followups"), []) or [])
    d["expected_values"] = list(_jsonish(d.get("expected_values"), []) or [])
    d["requirement_ids"] = list(d.get("requirement_ids") or [])
    d["flags"] = list(d.get("flags") or [])
    return d


_SET_COLUMNS = """
    s.id::text AS id, s.organization_id::text AS organization_id, s.name, s.kind, s.version, s.status,
    s.source_filename, s.source_sha256, s.source_kind, s.sheet_name, s.set_hash, s.header_row, s.case_count,
    s.warnings, s.diff, s.note, s.created_by, s.created_by_email, s.created_at, s.retired_at"""
_CASE_COLUMNS = """
    c.id::text AS id, c.set_id::text AS set_id, c.organization_id::text AS organization_id, c.case_key, c.key_source,
    c.case_version, c.idx, c.row_no, c.question, c.followups, c.expected_answer, c.evaluation, c.expected_values,
    c.source, c.verified_on, c.page_context, c.ask_as, c.requirement_ids, c.notes, c.stale, c.flags"""
_COUNTS = """
    (SELECT count(*) FROM tracelab.golden_cases c WHERE c.set_id = s.id AND c.stale) AS stale_count,
    (SELECT count(*) FROM tracelab.golden_cases c WHERE c.set_id = s.id AND c.verified_on IS NULL) AS unverified_count"""


async def _set_row(conn: Any, org: str, set_id: str) -> dict[str, Any] | None:
    try:
        sid = str(uuid.UUID(str(set_id)))
    except ValueError:
        return None
    row = (await conn.execute(text(
        f"SELECT {_SET_COLUMNS}, {_COUNTS} FROM tracelab.golden_sets s "
        "WHERE s.id = CAST(:sid AS uuid) AND s.organization_id = CAST(:org AS uuid)"),
        {"sid": sid, "org": org})).mappings().first()
    return set_dict(row) if row else None


async def _lock(conn: Any, org: str) -> None:
    """One writer of a company's golden sets at a time (released at commit)."""
    await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": "tracelab-golden-set:" + org})


def _actor(actor: Any) -> tuple[str, str | None]:
    if actor is None:
        return SYSTEM_ACTOR, None
    uid = getattr(actor, "user_id", None)
    return (str(uid) if uid is not None else SYSTEM_ACTOR)[:200], (getattr(actor, "email", None) or None)


# ── writes ─────────────────────────────────────────────────────────────────────────────────

async def save_version(conn: Any, org: str, parse_result: golden.ParseResult, *, actor: Any, filename: str,
                       source_sha256: str, name: str | None = None, note: str | None = None) -> dict[str, Any]:
    """Store an upload as the company's next scenario version inside the caller's transaction (conn from
    db.lab_engine().begin()) and retire the active one. The caller has already refused a file with row errors.
    Returns {"set": <the new set with its counts>, "diff": golden.version_diff(...)}."""
    if parse_result.errors:
        raise ValueError("a golden set with row errors is never saved")
    if not parse_result.cases:
        raise ValueError("a golden set needs at least one case")
    await _lock(conn, org)
    prev = (await conn.execute(text(
        "SELECT id::text AS id, version, name FROM tracelab.golden_sets "
        "WHERE organization_id = CAST(:org AS uuid) AND kind = 'scenario' AND status = 'active'"),
        {"org": org})).mappings().first()
    top = (await conn.execute(text(
        "SELECT coalesce(max(version), 0) FROM tracelab.golden_sets "
        "WHERE organization_id = CAST(:org AS uuid) AND kind = 'scenario'"), {"org": org})).scalar_one()
    previous: list[tuple[str, str, str]] = []
    if prev:
        previous = [(r[0], r[1], r[2]) for r in (await conn.execute(text(
            "SELECT case_key, case_version, key_source FROM tracelab.golden_cases WHERE set_id = CAST(:s AS uuid)"),
            {"s": prev["id"]})).all()]
        await conn.execute(text(
            "UPDATE tracelab.golden_sets SET status = 'retired', retired_at = now() WHERE id = CAST(:s AS uuid)"),
            {"s": prev["id"]})
    diff = golden.version_diff(previous, parse_result.cases, previous_version=prev["version"] if prev else None)
    new_id = str(uuid.uuid4())
    version = int(top) + 1
    by, email = _actor(actor)
    set_name = ((name or "").strip() or (prev["name"] if prev else "") or (filename or "").strip()
                or "Golden set")[:200]
    await conn.execute(text("""
        INSERT INTO tracelab.golden_sets (id, organization_id, name, kind, version, status, source_filename,
               source_sha256, source_kind, sheet_name, set_hash, header_row, case_count, warnings, diff, note,
               created_by, created_by_email)
        VALUES (CAST(:id AS uuid), CAST(:org AS uuid), :name, 'scenario', :version, 'active', :filename, :sha,
                :kind, :sheet, :hash, :header, :n, CAST(:warnings AS jsonb), CAST(:diff AS jsonb), :note, :by,
                :email)"""),
        {"id": new_id, "org": org, "name": set_name, "version": version,
         "filename": (filename or "golden-set")[:255], "sha": source_sha256, "kind": parse_result.source_kind,
         "sheet": (parse_result.sheet_name or None) and parse_result.sheet_name[:200],
         "hash": golden.set_hash([(c.case_key, c.case_version) for c in parse_result.cases]),
         "header": int(parse_result.header_row or 0), "n": len(parse_result.cases),
         "warnings": json.dumps(parse_result.warnings[:200], default=str), "diff": json.dumps(diff),
         "note": (note or "").strip()[:500] or None, "by": by, "email": email})
    await conn.execute(text("""
        INSERT INTO tracelab.golden_cases (set_id, organization_id, case_key, key_source, case_version, idx, row_no,
               question, followups, expected_answer, evaluation, expected_values, source, verified_on, page_context,
               ask_as, requirement_ids, notes, stale, flags)
        VALUES (CAST(:set AS uuid), CAST(:org AS uuid), :key, :ksrc, :ver, :idx, :row, :q, CAST(:fu AS jsonb), :exp,
                :ev, CAST(:vals AS jsonb), :src, :vo, :ctx, :ask, CAST(:reqs AS text[]), :notes, :stale,
                CAST(:flags AS text[]))"""),
        [{"set": new_id, "org": org, "key": c.case_key, "ksrc": c.key_source, "ver": c.case_version, "idx": i,
          "row": c.row_no or None, "q": c.question, "fu": json.dumps(list(c.followups)), "exp": c.expected_answer,
          "ev": c.evaluation, "vals": json.dumps(list(c.expected_values)), "src": c.source, "vo": c.verified_on,
          "ctx": c.page_context, "ask": c.ask_as, "reqs": list(c.requirement_ids), "notes": c.notes,
          "stale": golden.is_stale(c.verified_on), "flags": list(c.flags)}
         for i, c in enumerate(parse_result.cases)])
    saved = await _set_row(conn, org, new_id)
    log.info("trace_lab.golden.saved", organization_id=org, set_id=new_id, version=version,
             cases=len(parse_result.cases))
    return {"set": saved, "diff": diff}


async def activate(org: str, set_id: str) -> dict[str, Any]:
    """Make a retired scenario version the active one again (a roll-back); the active one is retired."""
    async with db.lab_engine().begin() as conn:
        await _lock(conn, org)
        target = await _set_row(conn, org, set_id)
        if target is None:
            raise NotFound(set_id)
        if target["kind"] == "regression":
            raise Conflict("The Regression cases set is kept by eval cases: it is never activated or retired here.")
        if target["status"] != "active":
            await conn.execute(text(
                "UPDATE tracelab.golden_sets SET status = 'retired', retired_at = now() "
                "WHERE organization_id = CAST(:org AS uuid) AND kind = 'scenario' AND status = 'active'"), {"org": org})
            await conn.execute(text(
                "UPDATE tracelab.golden_sets SET status = 'active', retired_at = NULL WHERE id = CAST(:sid AS uuid)"),
                {"sid": target["id"]})
        return await _set_row(conn, org, target["id"])


async def retire(org: str, set_id: str) -> dict[str, Any]:
    """Retire a scenario version. Retiring the active one leaves the company with no active set."""
    async with db.lab_engine().begin() as conn:
        await _lock(conn, org)
        target = await _set_row(conn, org, set_id)
        if target is None:
            raise NotFound(set_id)
        if target["kind"] == "regression":
            raise Conflict("The Regression cases set is kept by eval cases: it is never activated or retired here.")
        if target["status"] != "retired":
            await conn.execute(text(
                "UPDATE tracelab.golden_sets SET status = 'retired', retired_at = now() WHERE id = CAST(:sid AS uuid)"),
                {"sid": target["id"]})
        return await _set_row(conn, org, target["id"])


async def regression_set(org: str, *, create: bool = False, actor: Any = None) -> dict[str, Any] | None:
    """The company's one Regression cases set (kind 'regression', version 0, no cases until Phase 7 adds them), or
    None. With create=True it is created when missing; two concurrent creators get the same row."""
    async with db.lab_engine().begin() as conn:
        row = (await conn.execute(text(
            f"SELECT {_SET_COLUMNS}, {_COUNTS} FROM tracelab.golden_sets s "
            "WHERE s.organization_id = CAST(:org AS uuid) AND s.kind = 'regression'"), {"org": org})).mappings().first()
        if row is not None or not create:
            return set_dict(row) if row else None
        await _lock(conn, org)
        by, email = _actor(actor)
        await conn.execute(text("""
            INSERT INTO tracelab.golden_sets (organization_id, name, kind, version, status, set_hash, created_by,
                   created_by_email)
            VALUES (CAST(:org AS uuid), :name, 'regression', 0, 'active', :hash, :by, :email)
            ON CONFLICT DO NOTHING"""),
            {"org": org, "name": REGRESSION_SET_NAME, "hash": golden.set_hash([]), "by": by, "email": email})
        row = (await conn.execute(text(
            f"SELECT {_SET_COLUMNS}, {_COUNTS} FROM tracelab.golden_sets s "
            "WHERE s.organization_id = CAST(:org AS uuid) AND s.kind = 'regression'"), {"org": org})).mappings().first()
        return set_dict(row) if row else None


# ── reads ──────────────────────────────────────────────────────────────────────────────────

async def get_set(org: str, set_id: str) -> dict[str, Any] | None:
    async with db.lab_engine().connect() as conn:
        return await _set_row(conn, org, set_id)


async def active_set(org: str) -> dict[str, Any] | None:
    """The company's active scenario set (with its counts), or None."""
    async with db.lab_engine().connect() as conn:
        row = (await conn.execute(text(
            f"SELECT {_SET_COLUMNS}, {_COUNTS} FROM tracelab.golden_sets s WHERE s.organization_id = CAST(:org AS uuid) "
            "AND s.kind = 'scenario' AND s.status = 'active'"), {"org": org})).mappings().first()
    return set_dict(row) if row else None


async def list_sets(org: str) -> dict[str, Any]:
    """{"active": <set or None>, "versions": [uploaded versions, newest first], "regression": <set or None>}."""
    base = f"SELECT {_SET_COLUMNS}, {_COUNTS} FROM tracelab.golden_sets s WHERE s.organization_id = CAST(:org AS uuid) "
    async with db.lab_engine().connect() as conn:
        # The cap applies to the versions list only; the active and Regression cases sets are looked up on their own.
        rows = (await conn.execute(text(base + "AND s.kind = 'scenario' ORDER BY s.version DESC LIMIT :lim"),
                                   {"org": org, "lim": MAX_LIST + 1})).mappings().all()
        active = (await conn.execute(text(base + "AND s.kind = 'scenario' AND s.status = 'active'"),
                                     {"org": org})).mappings().first()
        regression = (await conn.execute(text(base + "AND s.kind = 'regression'"), {"org": org})).mappings().first()
    return {"active": set_dict(active) if active else None, "versions": [set_dict(r) for r in rows[:MAX_LIST]],
            "regression": set_dict(regression) if regression else None}


def _like(q: str) -> str:
    return "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def get_cases(org: str, set_id: str, *, q: str | None = None, flag: str | None = None, limit: int = 50,
                    offset: int = 0) -> dict[str, Any]:
    """One page of a set's cases in sheet order: {"set", "cases", "total"}. flag is one of CASE_FLAGS."""
    if flag is not None and flag not in CASE_FLAGS:
        raise ValueError(f"flag must be one of {', '.join(CASE_FLAGS)}")
    async with db.lab_engine().connect() as conn:
        s = await _set_row(conn, org, set_id)
        if s is None:
            raise NotFound(set_id)
        where = ["c.set_id = CAST(:sid AS uuid)"]
        p: dict[str, Any] = {"sid": s["id"], "lim": max(1, min(int(limit), 200)), "off": max(0, int(offset))}
        if q and q.strip():
            where.append("(c.question ILIKE :q OR c.case_key ILIKE :q)")
            p["q"] = _like(q)
        if flag == "stale":
            where.append("c.stale")
        elif flag == "unverified":
            where.append("c.verified_on IS NULL")
        elif flag in ("objective", "judgment"):
            where.append("c.evaluation = :ev")
            p["ev"] = flag
        elif flag == "write_intent":
            where.append("'write_intent' = ANY(c.flags)")
        w = " AND ".join(where)
        total = (await conn.execute(text(f"SELECT count(*) FROM tracelab.golden_cases c WHERE {w}"), p)).scalar_one()
        rows = (await conn.execute(text(
            f"SELECT {_CASE_COLUMNS} FROM tracelab.golden_cases c WHERE {w} ORDER BY c.idx LIMIT :lim OFFSET :off"),
            p)).mappings().all()
    return {"set": s, "cases": [case_dict(r) for r in rows], "total": int(total)}


async def cases_for_run(org: str, set_id: str) -> list[dict[str, Any]]:
    """Every case of the set in sheet order (case_dict shape). NotFound when the set is not this company's."""
    async with db.lab_engine().connect() as conn:
        s = await _set_row(conn, org, set_id)
        if s is None:
            raise NotFound(set_id)
        rows = (await conn.execute(text(
            f"SELECT {_CASE_COLUMNS} FROM tracelab.golden_cases c WHERE c.set_id = CAST(:sid AS uuid) ORDER BY c.idx"),
            {"sid": s["id"]})).mappings().all()
    return [case_dict(r) for r in rows]
