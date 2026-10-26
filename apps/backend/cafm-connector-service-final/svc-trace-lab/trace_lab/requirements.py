"""The requirement register (Trace Lab Phase 5, spec §8): numbered, testable sentences between failure modes and the
prompt docs. Modes cite codes, never files; the prompt docs are the implementation and change with every fix.
A revision is a new row; activating it supersedes the active row, so a code has at most one active revision.
Seeding is an explicit owner action (POST /requirements/seed), never startup work, and never overwrites.

Two databases, never one SQL statement across them (Golden Rule G0). The register and the modes that cite it live in
Trace Lab's own database (db.lab_engine(), schema tracelab). The only read of the chat's tables is the company check
on a requirement's motivating turns (db.main_engine(), read-only); a turn the chat has already purged still counts
for its company when Trace Lab holds a snapshot of it (tracelab.evidence_turns). A requirement cites turns, so
create() and revise() first snapshot every cited turn that the chat still has, each for its own company (G0 2.6:
snapshot wherever a turn is cited); a turn already purged that Trace Lab never captured cannot be recovered.
Who may call each function is the routes' rule (auth.py), never repeated here.

The docs a requirement quotes live in svc-deepagents. This service finds them beside itself (the image copies
svc-deepagents' `skills` and `src` next to Trace Lab) and reads them only to check that a quote is still there.
quote_status is a yes/no oracle over whatever file it reads, so a doc is read only when it is a plain-text name
(.md, .py, .json, .txt) outside hidden folders and its resolved path (symlinks followed) is inside the root it
claims to be under (svc-deepagents, or the repo checkout); anything else is never opened.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import text

from . import db, evidence
from .review_errors import Invalid, NotFound
from .taxonomy import LEVELS, json_value

AREAS = ("GEN", "COMP", "VEN", "UDR", "EN", "WO", "DOC")
AREA_LABELS = {"GEN": "General", "COMP": "Compliance", "VEN": "Vendors", "UDR": "Records lookup", "EN": "Energy",
               "WO": "Work orders", "DOC": "Documents"}
CODE_RE = re.compile(r"^(GEN|COMP|VEN|UDR|EN|WO|DOC)-[0-9]{1,3}$")
STATUSES = ("draft", "active", "superseded", "withdrawn")
STATEMENT_MIN, STATEMENT_MAX = 10, 600
NOTE_MAX = 1000
QUOTE_MAX = 2000
SEED_FILE = Path(__file__).with_name("requirements_seed.json")
#: svc-deepagents, the folder beside svc-trace-lab (in the repo and in the image).
SERVICE_ROOT = Path(__file__).resolve().parents[2] / "svc-deepagents"
SERVICE_PREFIX = "apps/backend/cafm-connector-service-final/svc-deepagents/"
DOC_SUFFIXES = (".md", ".py", ".json", ".txt")     # the only files a requirement may quote
SNAPSHOT_REASON = "requirement"     # evidence_turns.reason of a turn first kept because a requirement cites it
TIMEOUT = "SET LOCAL statement_timeout = '10s'"
_LOCK = "SELECT pg_advisory_xact_lock(hashtext('tracelab.requirements'))"

_SENTENCES = {
    "unknown_area": "Choose one of the listed areas.",
    "unknown_level": "Choose one of the listed levels.",
    "unknown_status": "That status is not one of the register's statuses.",
    "bad_doc_path": "Name a .md, .py, .json or .txt document inside the repository, by a path that stays inside it "
                    "and goes through no hidden folder.",
    "quote_required": "Quote the sentence the document uses for this requirement.",
    "quote_too_long": f"Keep each quote under {QUOTE_MAX} characters: quote the sentence, not the section.",
    "bad_implementation": "Each source needs a document path and a quote.",
    "statement_length": f"A requirement is a sentence of {STATEMENT_MIN} to {STATEMENT_MAX} characters.",
    "note_required": "Say what changed, or why it is withdrawn.",
    "note_too_long": f"Keep the note under {NOTE_MAX} characters.",
    "area_full": "That area has no free numbers left.",
    "requirement_not_found": "That requirement was not found.",
}


def _invalid(reason: str, **extra: Any) -> Invalid:
    return Invalid(reason, _SENTENCES[reason], **extra)


def _not_found() -> NotFound:
    return NotFound("requirement_not_found", _SENTENCES["requirement_not_found"])


def _repo_root() -> Path | None:
    """The repo checkout around the service, when there is one (never inside the image)."""
    parents = SERVICE_ROOT.parents
    if len(parents) > 3 and (parents[3] / "apps").is_dir():
        return parents[3]
    return None


def safe_doc(doc: str) -> bool:
    """A repo-relative path to a plain-text doc that cannot climb out of the repo: not absolute, no '..', no
    backslash, no NUL, no hidden folder or file (.env, .git), and a .md, .py, .json or .txt name."""
    if not isinstance(doc, str) or not doc or "\\" in doc or "\x00" in doc or doc.startswith("~"):
        return False
    p = PurePosixPath(doc)
    if p.is_absolute() or ".." in p.parts or any(part.startswith(".") for part in p.parts):
        return False
    return p.suffix.lower() in DOC_SUFFIXES


def _contained(root: Path, rel: PurePosixPath) -> Path | None:
    """root/rel when its fully resolved location (symlinks followed) is still inside root's, else None.
    Containment is decided on resolved paths, never on a string slice: a second slash in `rel` or a symlink
    cannot make the join leave root."""
    try:
        base = root.resolve()
        candidate = (base / rel).resolve()
    except (OSError, RuntimeError, ValueError):      # a symlink loop, an unreadable folder, a NUL: nothing to read
        return None
    return candidate if candidate.is_relative_to(base) else None


def doc_path(doc: str) -> tuple[Path | None, bool]:
    """(where the doc lives here, whether it is inside svc-deepagents). The path is None when the doc is unsafe or
    its resolved location is outside the root it claims to be under; those are never read."""
    if not safe_doc(doc):
        return None, False
    pure = PurePosixPath(doc)
    try:
        rel = pure.relative_to(PurePosixPath(SERVICE_PREFIX))
    except ValueError:
        pass
    else:
        return _contained(SERVICE_ROOT, rel), True
    root = _repo_root()
    return (_contained(root, pure) if root is not None else None), False


def quote_status(impl: dict) -> str:
    """'ok' when the quote is in its doc, 'drift' when it is not (or a doc inside svc-deepagents is gone),
    'unverifiable_in_image' for a doc that exists only in a repo checkout and is not here. A blank quote proves
    nothing, so it is drift."""
    path, in_service = doc_path(str(impl.get("doc") or ""))
    if path is None or not path.is_file():
        return "drift" if in_service else "unverifiable_in_image"
    try:
        body = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "drift" if in_service else "unverifiable_in_image"
    quote = str(impl.get("quote") or "")
    return "ok" if quote.strip() and quote in body else "drift"


def load_seed() -> list[dict]:
    rows = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    for r in rows:
        if not CODE_RE.match(r["code"]) or r["code"].split("-")[0] != r["area"] or r["level"] not in LEVELS:
            raise ValueError(f"bad seed requirement {r.get('code')}")
    return rows


def _implementations(raw: Any) -> list[dict]:
    """The stored shape of a requirement's sources: doc, section, line, quote and nothing else (a page that sends
    back what it read, quote_status included, never stores it)."""
    out: list[dict] = []
    for impl in raw or []:
        if not isinstance(impl, dict):
            raise _invalid("bad_implementation")
        doc, quote = impl.get("doc"), impl.get("quote")
        if not isinstance(doc, str) or not safe_doc(doc):
            raise _invalid("bad_doc_path", doc=doc if isinstance(doc, str) else None)
        if not isinstance(quote, str) or not quote.strip():
            raise _invalid("quote_required")
        if len(quote) > QUOTE_MAX:
            raise _invalid("quote_too_long")
        line = impl.get("line")
        out.append({"doc": doc, "section": str(impl.get("section") or ""),
                    "line": line if isinstance(line, int) and not isinstance(line, bool) and line >= 1 else None,
                    "quote": quote})
    return out


def _validate(*, area: str, level: str | None, statement: str, implementations: Any,
              note: str | None = None, note_required: bool = False) -> list[dict]:
    """Refuse a bad request before any write. Returns the implementations in their stored shape."""
    if area not in AREAS:
        raise _invalid("unknown_area", area=area)
    if level is not None and level not in LEVELS:
        raise _invalid("unknown_level", level=level)
    if not (STATEMENT_MIN <= len((statement or "").strip()) <= STATEMENT_MAX):
        raise _invalid("statement_length")
    _check_note(note, required=note_required)
    return _implementations(implementations)


def _check_note(note: str | None, *, required: bool) -> None:
    if required and not (note or "").strip():
        raise _invalid("note_required")
    if note is not None and len(note) > NOTE_MAX:
        raise _invalid("note_too_long")


def _payload(r: Any, *, citing: list[dict] | None = None, turn_filter: set[str] | None = None) -> dict[str, Any]:
    impls = list(json_value(r["implementations"]) or [])
    return {"id": str(r["id"]), "code": r["code"], "revision": r["revision"], "statement": r["statement"],
            "area": r["area"], "area_label": AREA_LABELS.get(r["area"], r["area"]), "level": r["level"],
            "status": r["status"], "implementations": [dict(i, quote_status=quote_status(i)) for i in impls],
            "motivating_turn_ids": [t for t in (r["motivating_turn_ids"] or [])
                                    if turn_filter is None or t in turn_filter],
            "revision_of": str(r["revision_of"]) if r["revision_of"] else None, "origin": r["origin"],
            "note": r["note"], "created_by_email": r["created_by_email"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None, "citing_modes": citing or []}


async def _insert(conn, principal, **f: Any) -> Any:
    return (await conn.execute(text(
        "INSERT INTO tracelab.requirements (id, code, revision, statement, area, level, status, "
        "implementations, revision_of, motivating_turn_ids, origin, note, created_by, created_by_email) VALUES "
        "(CAST(:id AS uuid), :code, :revision, :statement, :area, :level, :status, CAST(:impl AS jsonb), "
        "CAST(:rev_of AS uuid), CAST(:mot AS text[]), :origin, :note, CAST(:by AS uuid), :email) RETURNING *"),
        {"id": str(uuid.uuid4()), "code": f["code"], "revision": f["revision"], "statement": f["statement"].strip(),
         "area": f["area"], "level": f["level"], "status": f["status"], "impl": json.dumps(f["implementations"]),
         "rev_of": f.get("revision_of"), "mot": _turn_ids(f.get("motivating_turn_ids")), "origin": f["origin"],
         "note": f.get("note"), "by": str(principal.user_id), "email": principal.email or None})).mappings().first()


def _turn_ids(raw: Any) -> list[str]:
    """Turn ids as stored: text, once each, in the order given, blanks dropped."""
    return list(dict.fromkeys(str(t).strip() for t in (raw or []) if str(t or "").strip()))


async def _citing(conn) -> dict[str, list[dict]]:
    rows = (await conn.execute(text("SELECT CAST(id AS text) AS id, name, status, requirement_ids "
                                    "FROM tracelab.modes WHERE status IN ('candidate','confirmed','frozen')"
                                    ))).mappings().all()
    out: dict[str, list[dict]] = {}
    for r in rows:
        for code in r["requirement_ids"] or []:
            out.setdefault(code, []).append({"id": r["id"], "name": r["name"], "status": r["status"]})
    return out


async def _flagged_modes(conn, code: str) -> list[dict]:
    """Confirmed or frozen modes citing the code: they must recheck their definition against the new wording."""
    return [m for m in (await _citing(conn)).get(code, []) if m["status"] in ("confirmed", "frozen")]


async def _snapshot_cited(turn_ids: Any) -> None:
    """Keep the evidence of every cited turn before the register row is written. The company of each turn is read
    from the chat's tables (read-only, a separate read, no cross-database SQL) and the turns are snapshotted per
    company; a turn the chat no longer has is skipped. A failed snapshot raises: nothing is written for a
    requirement whose cited evidence was not kept."""
    ids = _turn_ids(turn_ids)
    if not ids:
        return
    async with db.main_engine().begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text(TIMEOUT))
        rows = (await conn.execute(text(
            f"SELECT turn_id, CAST(organization_id AS text) FROM {evidence.TURNS} "
            "WHERE turn_id = ANY(CAST(:t AS text[])) AND organization_id IS NOT NULL"), {"t": ids})).all()
    by_org: dict[str, list[str]] = {}
    for turn_id, org in rows:
        by_org.setdefault(org, []).append(turn_id)
    for org, owned in sorted(by_org.items()):
        await evidence.snapshot(owned, organization_id=org, reason=SNAPSHOT_REASON)


async def _org_turns(turn_ids: list[str], org: str) -> set[str]:
    """Which of these turns belong to the company: read from the chat's tables (read-only) and from Trace Lab's
    snapshots, as two reads merged here (never one statement across both databases)."""
    if not turn_ids:
        return set()
    async with db.main_engine().begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text(TIMEOUT))
        live = (await conn.execute(text(
            "SELECT turn_id FROM plenum_cafm.agent_trace_turns WHERE turn_id = ANY(CAST(:t AS text[])) "
            "AND organization_id = CAST(:o AS uuid)"), {"t": turn_ids, "o": org})).all()
    async with db.lab_engine().begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text(TIMEOUT))
        kept = (await conn.execute(text(
            "SELECT turn_id FROM tracelab.evidence_turns WHERE turn_id = ANY(CAST(:t AS text[])) "
            "AND organization_id = CAST(:o AS uuid)"), {"t": turn_ids, "o": org})).all()
    return {r[0] for r in live} | {r[0] for r in kept}


async def list_requirements(*, status: str | None = None, org: str | None = None) -> list[dict]:
    """Every revision, newest first per code. A company caller (org given) sees only its own motivating turns."""
    if status is not None and status not in STATUSES:
        raise _invalid("unknown_status", given=status)
    where, params = ("WHERE status = :s", {"s": status}) if status else ("", {})
    async with db.lab_engine().begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text(TIMEOUT))
        rows = (await conn.execute(text(
            f"SELECT * FROM tracelab.requirements {where} "
            "ORDER BY area, CAST(split_part(code, '-', 2) AS integer), revision DESC"), params)).mappings().all()
        citing = await _citing(conn)
    allowed = None
    if org:
        allowed = await _org_turns(sorted({t for r in rows for t in (r["motivating_turn_ids"] or [])}), org)
    return [_payload(r, citing=citing.get(r["code"], []), turn_filter=allowed) for r in rows]


async def create(principal, *, area: str, statement: str, level: str, implementations: list[dict],
                 motivating_turn_ids: list[str], activate: bool = True) -> dict:
    impls = _validate(area=area, level=level, statement=statement, implementations=implementations)
    await _snapshot_cited(motivating_turn_ids)
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(_LOCK))
        n = (await conn.execute(text("SELECT coalesce(max(CAST(split_part(code, '-', 2) AS integer)), 0) "
                                     "FROM tracelab.requirements WHERE area = :a"), {"a": area})).scalar()
        code = f"{area}-{int(n) + 1}"
        if not CODE_RE.match(code):
            raise _invalid("area_full", area=area)
        row = await _insert(conn, principal, code=code, revision=1, statement=statement, area=area, level=level,
                            status="active" if activate else "draft", implementations=impls,
                            motivating_turn_ids=motivating_turn_ids, origin="ui")
    return {"requirement": _payload(row)}


async def revise(principal, code: str, *, statement: str, implementations: list[dict],
                 motivating_turn_ids: list[str], note: str, activate: bool = True) -> dict:
    if not CODE_RE.match(code or ""):
        raise _not_found()
    impls = _validate(area=code.split("-")[0], level=None, statement=statement, implementations=implementations,
                      note=note, note_required=True)
    await _snapshot_cited(motivating_turn_ids)
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(_LOCK))
        latest = (await conn.execute(text("SELECT * FROM tracelab.requirements WHERE code = :c "
                                          "ORDER BY revision DESC LIMIT 1"), {"c": code})).mappings().first()
        if latest is None:
            raise _not_found()
        if activate:
            await conn.execute(text("UPDATE tracelab.requirements SET status = 'superseded' "
                                    "WHERE code = :c AND status = 'active'"), {"c": code})
        row = await _insert(conn, principal, code=code, revision=int(latest["revision"]) + 1, statement=statement,
                            area=latest["area"], level=latest["level"], status="active" if activate else "draft",
                            implementations=impls, revision_of=str(latest["id"]),
                            motivating_turn_ids=motivating_turn_ids, origin="ui", note=note.strip())
        flagged = await _flagged_modes(conn, code)
    return {"requirement": _payload(row), "flagged_modes": flagged}


async def withdraw(principal, code: str, *, note: str) -> dict:
    """Retire the code's active (and draft) revisions: afterwards it has no active revision, and a mode that still
    cites it is flagged (requirement_flags). Withdrawing a code already withdrawn changes nothing."""
    if not CODE_RE.match(code or ""):
        raise _not_found()
    _check_note(note, required=True)
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(_LOCK))
        rows = (await conn.execute(text(
            "UPDATE tracelab.requirements SET status = 'withdrawn', note = :n "
            "WHERE code = :c AND status IN ('active', 'draft') RETURNING *"),
            {"c": code, "n": note.strip()})).mappings().all()
        if rows:
            row = max(rows, key=lambda r: r["revision"])
        else:
            row = (await conn.execute(text("SELECT * FROM tracelab.requirements WHERE code = :c "
                                           "ORDER BY revision DESC LIMIT 1"), {"c": code})).mappings().first()
            if row is None:
                raise _not_found()
        flagged = await _flagged_modes(conn, code)
    return {"requirement": _payload(row), "flagged_modes": flagged}


async def seed(principal) -> dict:
    """Insert the starting register. A code that already has any revision is skipped, never overwritten. `drift`
    lists every quote of the seed that its doc no longer holds (the seed is still inserted; the owner fixes it)."""
    seeds = load_seed()
    inserted: list[str] = []
    skipped: list[str] = []
    drift: list[dict] = []
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(_LOCK))
        existing = {r[0] for r in (await conn.execute(text("SELECT DISTINCT code FROM tracelab.requirements"))).all()}
        for s in seeds:
            for impl in s["implementations"]:
                if quote_status(impl) == "drift":
                    drift.append({"code": s["code"], "doc": impl["doc"], "quote": impl["quote"]})
            if s["code"] in existing:
                skipped.append(s["code"])
                continue
            await _insert(conn, principal, code=s["code"], revision=1, statement=s["statement"], area=s["area"],
                          level=s["level"], status="active", implementations=s["implementations"],
                          motivating_turn_ids=[], origin="seed")
            inserted.append(s["code"])
    return {"inserted": inserted, "skipped": skipped, "drift": drift}


async def active_codes(conn, codes: list[str]) -> dict[str, dict]:
    """code -> {"revision", "id", "created_at"} for the codes that have an active revision."""
    clean = sorted({c for c in codes or [] if CODE_RE.match(str(c))})
    if not clean:
        return {}
    rows = (await conn.execute(text(
        "SELECT code, revision, CAST(id AS text) AS id, created_at FROM tracelab.requirements "
        "WHERE status = 'active' AND code = ANY(CAST(:c AS text[]))"), {"c": clean})).mappings().all()
    return {r["code"]: {"revision": int(r["revision"]), "id": r["id"], "created_at": r["created_at"]} for r in rows}
