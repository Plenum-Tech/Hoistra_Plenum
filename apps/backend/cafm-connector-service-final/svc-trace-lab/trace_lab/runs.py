"""Runs above questions (22 Oct 2026, migration 0011): the review loop the domain experts work in.

* A run is one synthetic batch (written by the consultant's coding agent straight into the database, imported from Excel,
  or typed on the page) or one person's live questions on one day ("Sync now" builds those from the chat's records).
  Runs have serial codes R1, R2 ... and their questions R1-1, R1-2 ...; the coding agent maps expected answers and labels
  back by those codes (tracelab.question_id('R3-12') in SQL).
* A question shows its expected answer (the newest version), the recorded answers that belong to it (live: the one it was
  synced from) and everyone's free-text notes on its steps (step_labels, read through labelling).
* Failure modes are the columns of each run's labels view; every person sets a question positive or negative per mode, and
  disagreement stays visible because each person's newest cell is kept.
* Who sees what: the platform team and a consultant see every company's runs; anyone else only their own company's.

Every table here is append-only: a change is a new row and the newest row wins. Reads of the chat go through
conversations.py on the read-only engine; nothing here writes to Hoistra's database.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import PurePath
from typing import Any

from sqlalchemy import text

from . import conversations, db, golden, labelling, provenance
from .annotations import _limits
from .review_errors import Forbidden, Invalid, NotFound

RUN_CODE = re.compile(r"^\s*R(\d{1,9})\s*$", re.I)
QUESTION_CODE = re.compile(r"^\s*R(\d{1,9})-(\d{1,9})\s*$", re.I)
KINDS = ("synthetic", "production")
CELL_VALUES = ("positive", "negative")
SYNC_DAYS = 90
SYNC_BATCH = 2000
SYNC_LOCK = 4_201_011
IMPORT_MAX = 2000
TITLE_MAX = 200
QUESTION_MAX = 4000
EXPECTED_MAX = 8000
MODE_NAME_MAX = 120
DEFINITION_MAX = 4000
LIST_MAX = 200
TREND_RUNS = 8
EXAMPLES = 3
TURNS = conversations.TURNS
USERS = labelling.USERS
SOURCES = ("coding_agent", "excel", "manual")
NONE_ORG = "00000000-0000-0000-0000-000000000000"


def run_code(no: int) -> str:
    return f"R{int(no)}"


def question_code(run_no: int, seq_no: int) -> str:
    return f"R{int(run_no)}-{int(seq_no)}"


def parse_run_code(code: str) -> int:
    m = RUN_CODE.match(str(code or ""))
    if not m:
        raise NotFound("run_not_found", "That run is not in Trace Lab.")
    return int(m.group(1))


def parse_question_code(code: str) -> tuple[int, int]:
    m = QUESTION_CODE.match(str(code or ""))
    if not m:
        raise NotFound("question_not_found", "That question is not in Trace Lab.")
    return int(m.group(1)), int(m.group(2))


def _iso(v: Any) -> str | None:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v) if v is not None else None


def _uuid_or_none(v: Any) -> str | None:
    try:
        return str(uuid.UUID(str(v))) if v not in (None, "") else None
    except ValueError:
        return None


def _scope(m: labelling.Member, alias: str = "r", company: str | None = None) -> tuple[str, dict[str, Any]]:
    """The runs the caller may see: every company's (and platform-wide synthetic runs) for the platform team and a
    consultant, optionally narrowed to one company; otherwise only their own company's."""
    if m.all_companies:
        org = _uuid_or_none(company)
        if org:
            return f"{alias}.organization_id = CAST(:scope_org AS uuid)", {"scope_org": org}
        return "TRUE", {}
    return f"{alias}.organization_id = CAST(:scope_org AS uuid)", {"scope_org": m.org}


def can_curate(m: labelling.Member) -> bool:
    """Creating runs, importing and linking answers: the platform team, a consultant and company admins."""
    return m.active and (m.all_companies or m.manages)


def require_curator(m: labelling.Member) -> None:
    labelling.require_active(m)
    if not can_curate(m):
        raise Forbidden("curators_only", "Only company admins, the platform team and the consultant can do this.")


def require_taxonomist(m: labelling.Member) -> None:
    """Failure modes are shared by every company: only the platform team and the consultant add or change them."""
    labelling.require_active(m)
    if not m.all_companies:
        raise Forbidden("platform_only", "Only the platform team and the consultant can change the failure modes.")


# ── runs ───────────────────────────────────────────────────────────────────────────────────────

_RUN_COLS = ("r.id::text AS id, r.run_no, r.kind, r.title, r.organization_id::text AS organization_id, r.owner_email, "
             "r.run_day, r.source, r.created_by_email, r.created_at")

_RUN_STATS = """
    (SELECT count(*) FROM tracelab.lab_questions q WHERE q.run_id = r.id) AS n_questions,
    (SELECT count(DISTINCT t.question_id) FROM tracelab.lab_question_traces t
       JOIN tracelab.lab_questions q ON q.id = t.question_id WHERE q.run_id = r.id) AS n_traced,
    (SELECT count(DISTINCT e.question_id) FROM tracelab.lab_expected_answers e
       JOIN tracelab.lab_questions q ON q.id = e.question_id WHERE q.run_id = r.id) AS n_expected,
    (SELECT count(*) FROM tracelab.step_labels s JOIN tracelab.lab_question_traces t ON t.turn_id = s.turn_id
       JOIN tracelab.lab_questions q ON q.id = t.question_id
      WHERE q.run_id = r.id AND s.superseded_by IS NULL AND NOT s.tombstone) AS n_notes,
    (SELECT count(DISTINCT q.id) FROM tracelab.step_labels s JOIN tracelab.lab_question_traces t ON t.turn_id = s.turn_id
       JOIN tracelab.lab_questions q ON q.id = t.question_id
      WHERE q.run_id = r.id AND s.superseded_by IS NULL AND NOT s.tombstone) AS n_reviewed,
    (SELECT min(q.asked_at) FROM tracelab.lab_questions q WHERE q.run_id = r.id) AS first_at,
    (SELECT max(q.asked_at) FROM tracelab.lab_questions q WHERE q.run_id = r.id) AS last_at"""


def run_out(r: Any) -> dict[str, Any]:
    out = {"id": r["id"], "code": run_code(r["run_no"]), "run_no": r["run_no"], "kind": r["kind"], "title": r["title"],
           "organization_id": r.get("organization_id"), "owner_email": r.get("owner_email"),
           "run_day": _iso(r.get("run_day")), "source": r["source"], "created_by_email": r.get("created_by_email"),
           "created_at": _iso(r.get("created_at"))}
    for k in ("n_questions", "n_traced", "n_expected", "n_notes", "n_reviewed"):
        if k in r:
            out[k] = int(r[k] or 0)
    for k in ("first_at", "last_at"):
        if k in r:
            out[k] = _iso(r[k])
    return out


async def list_runs(m: labelling.Member, *, kind: str | None = None, q: str | None = None, company: str | None = None,
                    limit: int = 100, offset: int = 0) -> dict[str, Any]:
    labelling.require_active(m)
    where, p = _scope(m, "r", company)
    if kind:
        if kind not in KINDS:
            raise Invalid("bad_kind", "Choose synthetic or production.")
        where += " AND r.kind = :kind"
        p["kind"] = kind
    if q:
        where += " AND (r.title ILIKE :q OR coalesce(r.owner_email, '') ILIKE :q OR ('R' || r.run_no) ILIKE :q)"
        p["q"] = conversations._like(q.strip())
    limit, offset = max(1, min(int(limit), LIST_MAX)), max(0, int(offset))
    async with db.lab_engine().connect() as conn:
        total = (await conn.execute(text(f"SELECT count(*) FROM tracelab.lab_runs r WHERE {where}"), p)).scalar_one()
        rows = (await conn.execute(text(
            f"SELECT {_RUN_COLS}, {_RUN_STATS} FROM tracelab.lab_runs r WHERE {where} "
            "ORDER BY r.run_no DESC LIMIT :lim OFFSET :off"), {**p, "lim": limit, "off": offset})).mappings().all()
    return {"total": int(total), "runs": [run_out(r) for r in rows]}


async def _run_row(conn: Any, m: labelling.Member, code: str) -> Any:
    no = parse_run_code(code)
    where, p = _scope(m, "r")
    row = (await conn.execute(text(f"SELECT {_RUN_COLS}, {_RUN_STATS} FROM tracelab.lab_runs r WHERE r.run_no = :no "
                                   f"AND {where}"), {**p, "no": no})).mappings().first()
    if row is None:
        raise NotFound("run_not_found", "That run is not in Trace Lab.")
    return row


async def get_run(m: labelling.Member, code: str) -> dict[str, Any]:
    labelling.require_active(m)
    async with db.lab_engine().connect() as conn:
        return run_out(await _run_row(conn, m, code))


async def create_run(m: labelling.Member, *, title: str, company: str | None = None,
                     questions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A synthetic run typed on the page (or created empty for an import): its questions numbered from 1."""
    require_curator(m)
    title = _text(title, TITLE_MAX, "title", "Give the run a title.")
    org = _uuid_or_none(company) if m.all_companies else m.org
    rows = [_new_question(q, i) for i, q in enumerate(questions or [], start=1)]
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        run = await _insert_run(conn, m, kind="synthetic", title=title, org=org, source="manual")
        for i, q in enumerate(rows, start=1):
            await _insert_question(conn, m, run_id=run["id"], seq=i, row=q, org=org, source="manual")
    return await get_run(m, run_code(run["run_no"]))


async def _insert_run(conn: Any, m: labelling.Member | None, *, kind: str, title: str, org: str | None, source: str,
                      owner_user_id: str | None = None, owner_email: str | None = None,
                      run_day: date | None = None) -> dict[str, Any]:
    row = (await conn.execute(text(
        "INSERT INTO tracelab.lab_runs (kind, title, organization_id, owner_user_id, owner_email, run_day, source, "
        "created_by, created_by_email) VALUES (:kind, :title, CAST(:org AS uuid), CAST(:owner AS uuid), :owner_email, "
        ":day, :source, CAST(:by AS uuid), :by_email) RETURNING id::text AS id, run_no"),
        {"kind": kind, "title": title[:TITLE_MAX], "org": org, "owner": owner_user_id, "owner_email": owner_email,
         "day": run_day, "source": source, "by": m.user_id if m else None,
         "by_email": m.email if m else None})).mappings().one()
    return dict(row)


async def _insert_question(conn: Any, m: labelling.Member | None, *, run_id: str, seq: int, row: dict[str, Any],
                           org: str | None, source: str, asked_at: datetime | None = None) -> str:
    qid = (await conn.execute(text(
        "INSERT INTO tracelab.lab_questions (run_id, seq_no, question, followups, role, intent, scenario_ref, "
        "organization_id, asked_at, source, created_by, created_by_email) VALUES (CAST(:run AS uuid), :seq, :q, "
        "CAST(:followups AS jsonb), :role, :intent, :ref, CAST(:org AS uuid), :asked, :source, CAST(:by AS uuid), "
        ":by_email) RETURNING id::text"),
        {"run": run_id, "seq": seq, "q": row["question"], "followups": json.dumps(row["followups"]) if row.get("followups")
         else None, "role": row.get("role"), "intent": row.get("intent"), "ref": row.get("scenario_ref"), "org": org,
         "asked": asked_at, "source": source, "by": m.user_id if m else None,
         "by_email": m.email if m else None})).scalar_one()
    if row.get("expected"):
        await _insert_expected(conn, m, question_id=qid, expected=row["expected"], expected_id=row.get("expected_id"),
                               evidence=row.get("evidence"), source="excel" if source == "excel" else "manual")
    return qid


async def _insert_expected(conn: Any, m: labelling.Member | None, *, question_id: str, expected: str,
                           expected_id: str | None, evidence: Any, source: str) -> None:
    await conn.execute(text(
        "INSERT INTO tracelab.lab_expected_answers (question_id, expected, expected_id, evidence, source, author_email, "
        "created_by) VALUES (CAST(:qid AS uuid), :expected, :eid, CAST(:evidence AS jsonb), :source, :email, "
        "CAST(:by AS uuid))"),
        {"qid": question_id, "expected": expected, "eid": expected_id,
         "evidence": json.dumps(evidence) if evidence is not None else None, "source": source,
         "email": m.email if m else None, "by": m.user_id if m else None})


def _text(v: Any, most: int, field: str, missing: str | None = None) -> str:
    s = str(v or "").strip()
    if not s:
        if missing:
            raise Invalid(f"{field}_required", missing)
        return ""
    if len(s) > most:
        raise Invalid(f"{field}_too_long", f"Keep the {field.replace('_', ' ')} under {most:,} characters.")
    return s


def _opt(v: Any, most: int, field: str) -> str | None:
    return _text(v, most, field) or None


def _new_question(q: dict[str, Any], n: int) -> dict[str, Any]:
    """One question to create, validated: the question, optional follow-ups, role, intent, scenario id and expected answer."""
    followups = q.get("followups")
    if isinstance(followups, str):
        followups = [x.strip() for x in followups.split("\n") if x.strip()]
    if followups is not None and not isinstance(followups, list):
        raise Invalid("bad_followups", f"Question {n}: follow-ups must be a list.")
    return {"question": _text(q.get("question"), QUESTION_MAX, "question", f"Question {n} has no question."),
            "followups": [str(x)[:QUESTION_MAX] for x in followups or [] if str(x).strip()] or None,
            "role": _opt(q.get("role"), 200, "role"), "intent": _opt(q.get("intent"), 500, "intent"),
            "scenario_ref": _opt(q.get("scenario_ref"), 200, "scenario_ref"),
            "expected": _opt(q.get("expected"), EXPECTED_MAX, "expected"),
            "expected_id": _opt(q.get("expected_id"), 200, "expected_id"), "evidence": q.get("evidence")}


# ── live traffic: one run per person per day ─────────────────────────────────────────────────────

async def sync_production(m: labelling.Member, *, now: datetime | None = None) -> dict[str, Any]:
    """Make questions of the chat's newer live answers, grouped one run per person per company per day (UTC). Reads the
    chat's records read-only, then writes Trace Lab's own rows under one lock, so two syncs never number twice."""
    labelling.require_active(m)
    now = now or datetime.now(timezone.utc)
    org = None if m.all_companies else m.org
    async with db.lab_engine().connect() as conn:
        last = (await conn.execute(text(
            "SELECT max(asked_at) FROM tracelab.lab_questions WHERE source = 'sync'"
            + ("" if org is None else " AND organization_id = CAST(:org AS uuid)")), {"org": org})).scalar_one()
    floor = now - timedelta(days=SYNC_DAYS)
    since = max(floor, (last - timedelta(hours=1)) if last else floor)
    where = ["tt.started_at >= :since", "tt.user_id IS NOT NULL", f"({provenance.INFERRED_CHANNEL_SQL}) <> 'golden'"]
    p: dict[str, Any] = {"since": since, "lim": SYNC_BATCH}
    if org is not None:
        where.append("tt.organization_id = CAST(:org AS uuid)")
        p["org"] = org
    async with conversations._read() as conn:
        turns = [dict(r) for r in (await conn.execute(text(
            f"SELECT tt.turn_id, tt.organization_id::text AS org, tt.user_id::text AS uid, tt.email, tt.started_at, "
            f"left({conversations._ASKER_WORDS_SQL}, {QUESTION_MAX}) AS q FROM {TURNS} tt WHERE {' AND '.join(where)} "
            "ORDER BY tt.started_at, tt.turn_id LIMIT :lim"), p)).mappings().all()]
    if not turns:
        return {"new_questions": 0, "new_runs": 0, "more": False}
    new_q = new_r = 0
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": SYNC_LOCK})
        seen = {r[0] for r in (await conn.execute(text(
            "SELECT turn_id FROM tracelab.lab_question_traces WHERE source = 'sync' AND turn_id = ANY(:ids)"),
            {"ids": [t["turn_id"] for t in turns]})).all()}
        runs: dict[tuple[str, str, date], tuple[str, int]] = {}
        for t in turns:
            if t["turn_id"] in seen:
                continue
            day = t["started_at"].astimezone(timezone.utc).date()
            key = (t["uid"], t["org"] or NONE_ORG, day)
            if key not in runs:
                row = (await conn.execute(text(
                    "SELECT id::text AS id, (SELECT coalesce(max(seq_no), 0) FROM tracelab.lab_questions q "
                    "WHERE q.run_id = r.id) AS top FROM tracelab.lab_runs r WHERE r.kind = 'production' "
                    "AND r.owner_user_id = CAST(:uid AS uuid) AND coalesce(r.organization_id, CAST(:none AS uuid)) = "
                    "CAST(:org AS uuid) AND r.run_day = :day"),
                    {"uid": t["uid"], "org": key[1], "none": NONE_ORG, "day": day})).mappings().first()
                if row is None:
                    made = await _insert_run(conn, None, kind="production", title=f"{t['email'] or 'Unknown person'} · {day}",
                                             org=t["org"], source="sync", owner_user_id=t["uid"],
                                             owner_email=t["email"], run_day=day)
                    runs[key] = (made["id"], 0)
                    new_r += 1
                else:
                    runs[key] = (row["id"], int(row["top"]))
            run_id, top = runs[key]
            qtext = (t["q"] or "").strip() or "(no question recorded)"
            qid = await _insert_question(conn, None, run_id=run_id, seq=top + 1, row={"question": qtext[:QUESTION_MAX]},
                                         org=t["org"], source="sync", asked_at=t["started_at"])
            await conn.execute(text(
                "INSERT INTO tracelab.lab_question_traces (question_id, turn_id, organization_id, source) "
                "VALUES (CAST(:qid AS uuid), :tid, CAST(:org AS uuid), 'sync')"),
                {"qid": qid, "tid": t["turn_id"], "org": t["org"]})
            runs[key] = (run_id, top + 1)
            new_q += 1
    return {"new_questions": new_q, "new_runs": new_r, "more": len(turns) >= SYNC_BATCH}


# ── questions ──────────────────────────────────────────────────────────────────────────────────

_Q_COLS = ("q.id::text AS id, q.seq_no, q.question, q.followups, q.role, q.intent, q.scenario_ref, "
           "q.organization_id::text AS organization_id, q.asked_at, q.source, q.created_at")

_Q_STATS = """
    (SELECT json_agg(t.turn_id ORDER BY t.created_at, t.turn_id) FROM tracelab.lab_question_traces t
      WHERE t.question_id = q.id) AS turn_ids,
    (SELECT e.expected FROM tracelab.lab_expected_answers e WHERE e.question_id = q.id
      ORDER BY e.created_at DESC, e.id DESC LIMIT 1) AS expected,
    (SELECT count(*) FROM tracelab.step_labels s JOIN tracelab.lab_question_traces t ON t.turn_id = s.turn_id
      WHERE t.question_id = q.id AND s.superseded_by IS NULL AND NOT s.tombstone) AS n_notes,
    (SELECT count(DISTINCT s.reviewer_id) FROM tracelab.step_labels s JOIN tracelab.lab_question_traces t
       ON t.turn_id = s.turn_id WHERE t.question_id = q.id AND s.superseded_by IS NULL AND NOT s.tombstone) AS n_annotators"""


def question_out(r: Any, run_no: int) -> dict[str, Any]:
    followups = r.get("followups")
    if isinstance(followups, str):
        followups = json.loads(followups)
    turn_ids = r.get("turn_ids")
    if isinstance(turn_ids, str):
        turn_ids = json.loads(turn_ids)
    return {"id": r["id"], "code": question_code(run_no, r["seq_no"]), "seq_no": r["seq_no"], "question": r["question"],
            "followups": followups or [], "role": r.get("role"), "intent": r.get("intent"),
            "scenario_ref": r.get("scenario_ref"), "organization_id": r.get("organization_id"),
            "asked_at": _iso(r.get("asked_at")), "source": r.get("source"), "turn_ids": turn_ids or [],
            "has_expected": bool(r.get("expected")), "expected": r.get("expected"),
            "n_notes": int(r.get("n_notes") or 0), "n_annotators": int(r.get("n_annotators") or 0)}


async def list_questions(m: labelling.Member, code: str) -> dict[str, Any]:
    labelling.require_active(m)
    async with db.lab_engine().connect() as conn:
        run = await _run_row(conn, m, code)
        rows = (await conn.execute(text(f"SELECT {_Q_COLS}, {_Q_STATS} FROM tracelab.lab_questions q "
                                        "WHERE q.run_id = CAST(:rid AS uuid) ORDER BY q.seq_no"),
                                   {"rid": run["id"]})).mappings().all()
    return {"run": run_out(run), "questions": [question_out(r, run["run_no"]) for r in rows]}


async def _question_row(conn: Any, m: labelling.Member, code: str) -> tuple[Any, Any]:
    run_no, seq = parse_question_code(code)
    run = await _run_row(conn, m, run_code(run_no))
    row = (await conn.execute(text(f"SELECT {_Q_COLS}, {_Q_STATS} FROM tracelab.lab_questions q "
                                   "WHERE q.run_id = CAST(:rid AS uuid) AND q.seq_no = :seq"),
                              {"rid": run["id"], "seq": seq})).mappings().first()
    if row is None:
        raise NotFound("question_not_found", "That question is not in Trace Lab.")
    return run, row


def expected_out(r: Any) -> dict[str, Any]:
    ev = r.get("evidence")
    if isinstance(ev, str):
        try:
            ev = json.loads(ev)
        except ValueError:
            pass
    return {"id": r["id"], "expected": r["expected"], "expected_id": r.get("expected_id"), "evidence": ev,
            "source": r["source"], "author_email": r.get("author_email"), "at": _iso(r.get("created_at"))}


async def get_question(m: labelling.Member, code: str, *, turn_id: str | None = None) -> dict[str, Any]:
    """One question: its run, expected answer (newest first, with history), the recorded answers that belong to it, the
    active one's steps and everyone's notes on them."""
    labelling.require_active(m)
    async with db.lab_engine().connect() as conn:
        run, row = await _question_row(conn, m, code)
        expected = [expected_out(r) for r in (await conn.execute(text(
            "SELECT id::text AS id, expected, expected_id, evidence, source, author_email, created_at "
            "FROM tracelab.lab_expected_answers WHERE question_id = CAST(:qid AS uuid) ORDER BY created_at DESC, id DESC"),
            {"qid": row["id"]})).mappings().all()]
        links = [dict(r) for r in (await conn.execute(text(
            "SELECT turn_id, organization_id::text AS organization_id, source, created_by_email, created_at "
            "FROM tracelab.lab_question_traces WHERE question_id = CAST(:qid AS uuid) ORDER BY created_at, turn_id"),
            {"qid": row["id"]})).mappings().all()]
    q = question_out(row, run["run_no"])
    out = {"question": q, "run": run_out(run), "expected": expected[0] if expected else None,
           "expected_history": expected[1:],
           "traces": [{"turn_id": t["turn_id"], "organization_id": t["organization_id"], "source": t["source"],
                       "linked_by": t.get("created_by_email"), "at": _iso(t.get("created_at"))} for t in links],
           "active": None, "labels": [], "me": m.user_id}
    pick = next((t for t in links if t["turn_id"] == turn_id), links[0] if links else None)
    if pick is None:
        return out
    mm = labelling.for_org(m, pick["organization_id"] or row.get("organization_id") or run.get("organization_id"))
    got = await conversations.get_turn(labelling.company_view(mm.principal, mm.org), mm.org, pick["turn_id"])
    if got is None:
        out["active"] = {"turn_id": pick["turn_id"], "missing": True}
        return out
    out["active"] = {"turn_id": pick["turn_id"], "organization_id": mm.org, "conversation_key": got["conversation_key"],
                     "trace": got["trace"]}
    out["labels"] = await labelling.labels_for_turn(mm, pick["turn_id"])
    return out


async def set_expected(m: labelling.Member, code: str, *, expected: str, expected_id: str | None = None,
                       evidence: Any = None) -> dict[str, Any]:
    """A person types or corrects the expected answer: a new version, the newest wins."""
    labelling.require_active(m)
    expected = _text(expected, EXPECTED_MAX, "expected", "Write the expected answer.")
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        _run, row = await _question_row(conn, m, code)
        await _insert_expected(conn, m, question_id=row["id"], expected=expected,
                               expected_id=_opt(expected_id, 200, "expected_id"), evidence=evidence, source="manual")
    return (await get_question(m, code))["expected"]


async def link_trace(m: labelling.Member, code: str, turn_id: str) -> dict[str, Any]:
    """Attach a recorded answer to a question (a synthetic question once it has been asked). The answer must be readable
    by the caller in the question's company."""
    require_curator(m)
    turn_id = _text(turn_id, 200, "turn_id", "Name the recorded answer (its turn id).")
    async with db.lab_engine().connect() as conn:
        run, row = await _question_row(conn, m, code)
    org = row.get("organization_id") or run.get("organization_id")
    if org is None:
        async with conversations._read() as conn:
            org = (await conn.execute(text(f"SELECT organization_id::text FROM {TURNS} WHERE turn_id = :tid"),
                                      {"tid": turn_id})).scalar()
    mm = labelling.for_org(m, org)
    if await conversations.get_turn(labelling.company_view(mm.principal, mm.org), mm.org, turn_id) is None:
        raise NotFound("turn_not_found", "That answer is not in this company's records.")
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        await conn.execute(text(
            "INSERT INTO tracelab.lab_question_traces (question_id, turn_id, organization_id, source, created_by, "
            "created_by_email) VALUES (CAST(:qid AS uuid), :tid, CAST(:org AS uuid), 'manual', CAST(:by AS uuid), :email) "
            "ON CONFLICT (question_id, turn_id) DO NOTHING"),
            {"qid": row["id"], "tid": turn_id, "org": mm.org, "by": m.user_id, "email": m.email})
    return await get_question(m, code, turn_id=turn_id)


CANDIDATES_MAX = 50


async def answer_candidates(m: labelling.Member, code: str, *, q: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Recorded answers a curator may link to a question, read-only from the chat's records: the same question asked
    word for word first, then the newest. A search term matches the asked words, or a turn id exactly. Only the
    question's company is searched; a platform-wide question searches every company for the platform team and the
    consultant. Answers already linked to the question are left out."""
    require_curator(m)
    limit = max(1, min(int(limit), CANDIDATES_MAX))
    term = (q or "").strip()[:200]
    async with db.lab_engine().connect() as conn:
        run, row = await _question_row(conn, m, code)
        linked = {r[0] for r in (await conn.execute(text(
            "SELECT turn_id FROM tracelab.lab_question_traces WHERE question_id = CAST(:qid AS uuid)"),
            {"qid": row["id"]})).all()}
    org = row.get("organization_id") or run.get("organization_id")
    if org is not None:
        org = labelling.for_org(m, org).org
    elif not m.all_companies:
        org = m.org
    where = ["tt.started_at >= :since", "tt.user_id IS NOT NULL", f"({provenance.INFERRED_CHANNEL_SQL}) <> 'golden'"]
    p: dict[str, Any] = {"since": datetime.now(timezone.utc) - timedelta(days=SYNC_DAYS), "qtext": row["question"],
                         "lim": limit + len(linked)}
    if org is not None:
        where.append("tt.organization_id = CAST(:org AS uuid)")
        p["org"] = org
    if term:
        where.append(f"({conversations._ASKER_WORDS_SQL} ILIKE :pat ESCAPE '\\' OR tt.turn_id = :term)")
        p["pat"] = "%" + re.sub(r"([\\%_])", r"\\\1", term) + "%"
        p["term"] = term
    async with conversations._read() as conn:
        rows = (await conn.execute(text(
            f"SELECT tt.turn_id, tt.organization_id::text AS organization_id, tt.email, tt.started_at, tt.ok, tt.route, "
            f"left({conversations._ASKER_WORDS_SQL}, 300) AS question, left(tt.answer, 240) AS answer, "
            f"lower(btrim({conversations._ASKER_WORDS_SQL})) = lower(btrim(:qtext)) AS same "
            f"FROM {TURNS} tt WHERE {' AND '.join(where)} ORDER BY same DESC, tt.started_at DESC LIMIT :lim"), p)).mappings().all()
    out = [{"turn_id": r["turn_id"], "organization_id": r["organization_id"], "email": r["email"],
            "asked_at": _iso(r["started_at"]), "ok": r["ok"], "route": r["route"], "question": r["question"],
            "answer": r["answer"], "same": bool(r["same"])} for r in rows if r["turn_id"] not in linked]
    return {"question": question_code(run["run_no"], row["seq_no"]), "candidates": out[:limit]}


# ── import: Excel or CSV ─────────────────────────────────────────────────────────────────────────

HEADERS = {
    "code": "code", "question code": "code", "question_code": "code",
    "question": "question", "opening": "question", "opening message": "question", "opening_message": "question",
    "input": "question", "prompt": "question",
    "follow-ups": "followups", "followups": "followups", "follow ups": "followups", "follow-up": "followups",
    "role": "role", "persona": "role", "intent": "intent",
    "expected": "expected", "expected answer": "expected", "expected_answer": "expected", "expected outcome": "expected",
    "expected_outcome": "expected", "expected output": "expected", "expected_output": "expected", "outcome": "expected",
    "expected id": "expected_id", "expected_id": "expected_id", "outcome id": "expected_id", "outcome_id": "expected_id",
    "evidence": "evidence", "source": "evidence", "reference": "evidence",
    "scenario": "scenario_ref", "scenario id": "scenario_ref", "scenario_id": "scenario_ref", "id": "scenario_ref",
    "turn id": "turn_id", "turn_id": "turn_id", "trace": "turn_id", "trace id": "turn_id",
}


def _header(v: Any) -> str | None:
    return HEADERS.get(re.sub(r"\s+", " ", str(v or "").strip().lower()))


def _evidence(v: Any) -> Any:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except ValueError:
        return {"reference": s}


def parse_rows(filename: str, data: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The file's question rows (first sheet with a recognised header) and per-row problems; nothing is written."""
    try:
        _kind, tables = golden.read_tables(filename, data)
    except golden.UploadRefused as exc:
        raise Invalid("bad_file", str(exc)) from None
    for _sheet, rows, _warnings in tables:
        header_at, cols = None, {}
        for n, cells in rows[:20]:
            mapped = {i: _header(v) for i, v in cells.items() if _header(v)}
            if any(v in ("question", "code") for v in mapped.values()):
                header_at, cols = n, mapped
                break
        if header_at is None:
            continue
        out, problems = [], []
        for n, cells in rows:
            if n <= header_at:
                continue
            rec: dict[str, Any] = {}
            for i, field in cols.items():
                v = cells.get(i)
                if v not in (None, ""):
                    rec[field] = str(v).strip() if not isinstance(v, str) else v.strip()
            if not rec:
                continue
            if "evidence" in rec:
                rec["evidence"] = _evidence(rec["evidence"])
            rec["_row"] = n
            if not rec.get("question") and not rec.get("code"):
                problems.append({"row": n, "error": "This row has neither a question nor a question code."})
                continue
            out.append(rec)
        if len(out) > IMPORT_MAX:
            raise Invalid("too_many_rows", f"Import up to {IMPORT_MAX:,} rows at a time.")
        return out, problems
    raise Invalid("no_header", "The file needs a header row with a 'question' (or 'code') column.")


async def import_file(m: labelling.Member, filename: str, data: bytes, *, run: str | None = None,
                      title: str | None = None, company: str | None = None) -> dict[str, Any]:
    """Rows with a question code add an expected answer (or link an answer) to that existing question. Rows with a question
    become new questions: of the run named (appended after its last question) or of a new synthetic run."""
    require_curator(m)
    rows, problems = parse_rows(filename, data)
    if not rows:
        raise Invalid("nothing_to_import", "No rows to import.")
    added = expected_added = 0
    made_code = None
    linked: list[tuple[str, str]] = []
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        target = None
        for rec in rows:
            if rec.get("code"):
                try:
                    _r, q = await _question_row(conn, m, rec["code"])
                except NotFound:
                    problems.append({"row": rec["_row"], "error": f"No question {rec['code']} in Trace Lab."})
                    continue
                if rec.get("expected"):
                    await _insert_expected(conn, m, question_id=q["id"], expected=rec["expected"][:EXPECTED_MAX],
                                           expected_id=(rec.get("expected_id") or None), evidence=rec.get("evidence"),
                                           source="excel")
                    expected_added += 1
                if rec.get("turn_id"):
                    linked.append((rec["code"], rec["turn_id"]))
                continue
            if target is None:
                if run:
                    r = await _run_row(conn, m, run)
                    top = (await conn.execute(text("SELECT coalesce(max(seq_no), 0) FROM tracelab.lab_questions "
                                                   "WHERE run_id = CAST(:rid AS uuid)"), {"rid": r["id"]})).scalar_one()
                    target = {"id": r["id"], "run_no": r["run_no"], "org": r.get("organization_id"), "top": int(top)}
                else:
                    org = _uuid_or_none(company) if m.all_companies else m.org
                    stem = PurePath(filename or "import").stem[:120] or "Import"
                    r = await _insert_run(conn, m, kind="synthetic", title=(title or stem).strip()[:TITLE_MAX] or stem,
                                          org=org, source="excel")
                    target = {"id": r["id"], "run_no": r["run_no"], "org": org, "top": 0}
                    made_code = run_code(r["run_no"])
            try:
                row = _new_question(rec, rec["_row"])
            except Invalid as exc:
                problems.append({"row": rec["_row"], "error": exc.error or exc.reason})
                continue
            target["top"] += 1
            await _insert_question(conn, m, run_id=target["id"], seq=target["top"], row=row, org=target["org"],
                                   source="excel")
            if row.get("expected"):
                expected_added += 1
            if rec.get("turn_id"):
                linked.append((question_code(target["run_no"], target["top"]), rec["turn_id"]))
            added += 1
    links_done = 0
    for qcode, tid in linked:
        try:
            await link_trace(m, qcode, tid)
            links_done += 1
        except (NotFound, Invalid) as exc:
            problems.append({"row": None, "error": f"{qcode}: answer {tid} not linked ({exc.error or exc.reason})."})
    return {"run": made_code or (run.upper().strip() if run else None), "questions_added": added,
            "expected_added": expected_added, "answers_linked": links_done, "problems": problems[:200]}


# ── failure modes and the labels view ─────────────────────────────────────────────────────────────

_MODES_SQL = """
    SELECT * FROM (
      SELECT DISTINCT ON (coalesce(lineage_id, id)) coalesce(lineage_id, id)::text AS lineage_id, id::text AS id, name,
             definition, tombstone, created_by_email, created_at,
             min(created_at) OVER (PARTITION BY coalesce(lineage_id, id)) AS first_at
        FROM tracelab.lab_failure_modes
       ORDER BY coalesce(lineage_id, id), created_at DESC, id DESC) x
     WHERE NOT tombstone ORDER BY first_at, lineage_id"""


def mode_out(r: Any) -> dict[str, Any]:
    return {"lineage_id": r["lineage_id"], "id": r["id"], "name": r["name"], "definition": r.get("definition"),
            "by": r.get("created_by_email"), "at": _iso(r.get("created_at")), "since": _iso(r.get("first_at"))}


async def list_modes(m: labelling.Member) -> list[dict[str, Any]]:
    labelling.require_active(m)
    async with db.lab_engine().connect() as conn:
        return [mode_out(r) for r in (await conn.execute(text(_MODES_SQL))).mappings().all()]


async def save_mode(m: labelling.Member, *, name: str, definition: str | None = None,
                    lineage_id: str | None = None) -> dict[str, Any]:
    """Add a failure mode (a new column in every run's labels view), or change one's name or definition."""
    require_taxonomist(m)
    name = _text(name, MODE_NAME_MAX, "name", "Name the failure mode.")
    definition = _opt(definition, DEFINITION_MAX, "definition")
    lin = _uuid_or_none(lineage_id) if lineage_id else None
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": SYNC_LOCK + 1})
        live = [dict(r) for r in (await conn.execute(text(_MODES_SQL))).mappings().all()]
        if lineage_id and not any(x["lineage_id"] == lin for x in live):
            raise NotFound("mode_not_found", "That failure mode is not in Trace Lab.")
        if any(x["name"].strip().lower() == name.lower() and x["lineage_id"] != lin for x in live):
            raise Invalid("mode_exists", "A failure mode with that name already exists.")
        await conn.execute(text(
            "INSERT INTO tracelab.lab_failure_modes (lineage_id, name, definition, created_by, created_by_email) "
            "VALUES (CAST(:lin AS uuid), :name, :definition, CAST(:by AS uuid), :email)"),
            {"lin": lin, "name": name, "definition": definition, "by": m.user_id, "email": m.email})
    modes = await list_modes(m)
    return next(x for x in reversed(modes) if x["name"] == name)


async def retire_mode(m: labelling.Member, lineage_id: str) -> dict[str, Any]:
    require_taxonomist(m)
    lin = _uuid_or_none(lineage_id)
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        live = [dict(r) for r in (await conn.execute(text(_MODES_SQL))).mappings().all()]
        if not any(x["lineage_id"] == lin for x in live):
            raise NotFound("mode_not_found", "That failure mode is not in Trace Lab.")
        await conn.execute(text(
            "INSERT INTO tracelab.lab_failure_modes (lineage_id, tombstone, created_by, created_by_email) "
            "VALUES (CAST(:lin AS uuid), true, CAST(:by AS uuid), :email)"), {"lin": lin, "by": m.user_id, "email": m.email})
    return {"lineage_id": lin, "retired": True}


_CELLS_SQL = """
    SELECT DISTINCT ON (c.question_id, c.mode_lineage_id, coalesce(c.author_id::text, c.author_email))
           c.question_id::text AS question_id, c.mode_lineage_id::text AS mode, c.value, c.author_id::text AS author_id,
           c.author_email, c.author_role, c.source, c.created_at
      FROM tracelab.lab_label_cells c JOIN tracelab.lab_questions q ON q.id = c.question_id
     WHERE q.run_id = CAST(:rid AS uuid)
     ORDER BY c.question_id, c.mode_lineage_id, coalesce(c.author_id::text, c.author_email), c.created_at DESC, c.id DESC"""


async def matrix(m: labelling.Member, code: str) -> dict[str, Any]:
    """A run's labels view: its questions as rows, the failure modes as columns, and each person's newest value per cell."""
    labelling.require_active(m)
    async with db.lab_engine().connect() as conn:
        run = await _run_row(conn, m, code)
        questions = (await conn.execute(text(
            f"SELECT {_Q_COLS}, {_Q_STATS} FROM tracelab.lab_questions q WHERE q.run_id = CAST(:rid AS uuid) "
            "ORDER BY q.seq_no"), {"rid": run["id"]})).mappings().all()
        modes = [mode_out(r) for r in (await conn.execute(text(_MODES_SQL))).mappings().all()]
        cells = (await conn.execute(text(_CELLS_SQL), {"rid": run["id"]})).mappings().all()
        last = (await conn.execute(text(
            "SELECT max(c.created_at) FROM tracelab.lab_label_cells c JOIN tracelab.lab_questions q ON q.id = c.question_id "
            "WHERE q.run_id = CAST(:rid AS uuid)"), {"rid": run["id"]})).scalar_one()
    by: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for c in cells:
        if c["value"] is None:
            continue
        by.setdefault(c["question_id"], {}).setdefault(c["mode"], []).append({
            "value": c["value"], "author_id": c.get("author_id"), "author_email": c.get("author_email"),
            "author_role": c.get("author_role"), "source": c["source"], "at": _iso(c["created_at"]),
            "mine": c.get("author_id") == m.user_id})
    return {"run": run_out(run), "questions": [question_out(q, run["run_no"]) for q in questions], "modes": modes,
            "cells": by, "last_updated": _iso(last), "me": m.user_id}


async def set_cell(m: labelling.Member, code: str, *, mode: str, value: str | None) -> dict[str, Any]:
    """The caller's value for one cell: positive (the failure is there), negative (it is not) or none (cleared)."""
    labelling.require_active(m)
    if value is not None and value not in CELL_VALUES:
        raise Invalid("bad_value", "Choose positive or negative.")
    lin = _uuid_or_none(mode)
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        _run, q = await _question_row(conn, m, code)
        live = [dict(r) for r in (await conn.execute(text(_MODES_SQL))).mappings().all()]
        if not any(x["lineage_id"] == lin for x in live):
            raise NotFound("mode_not_found", "That failure mode is not in Trace Lab.")
        row = (await conn.execute(text(
            "INSERT INTO tracelab.lab_label_cells (question_id, mode_lineage_id, value, author_id, author_email, author_role) "
            "VALUES (CAST(:qid AS uuid), CAST(:mode AS uuid), :value, CAST(:by AS uuid), :email, :role) "
            "RETURNING created_at"), {"qid": q["id"], "mode": lin, "value": value, "by": m.user_id, "email": m.email,
                                       "role": m.role})).mappings().one()
    return {"question": code.upper().strip(), "mode": lin, "value": value, "at": _iso(row["created_at"])}


# ── progress ───────────────────────────────────────────────────────────────────────────────────

async def progress(m: labelling.Member, code: str | None = None) -> dict[str, Any]:
    """Review completion for one run (the newest visible run when none is named) and, per failure mode, how many of its
    questions someone marked positive, with the same count over the last runs so a mode's movement shows."""
    labelling.require_active(m)
    where, p = _scope(m, "r")
    async with db.lab_engine().connect() as conn:
        if code:
            run = await _run_row(conn, m, code)
        else:
            run = (await conn.execute(text(f"SELECT {_RUN_COLS}, {_RUN_STATS} FROM tracelab.lab_runs r WHERE {where} "
                                           "ORDER BY r.run_no DESC LIMIT 1"), p)).mappings().first()
            if run is None:
                return {"run": None, "tiles": None, "modes": [], "runs": []}
        modes = [mode_out(r) for r in (await conn.execute(text(_MODES_SQL))).mappings().all()]
        cells = [c for c in (await conn.execute(text(_CELLS_SQL), {"rid": run["id"]})).mappings().all() if c["value"]]
        recent = [dict(r) for r in (await conn.execute(text(
            f"SELECT r.id::text AS id, r.run_no, r.title FROM tracelab.lab_runs r WHERE {where} AND r.run_no <= :top "
            "ORDER BY r.run_no DESC LIMIT :n"), {**p, "top": run["run_no"], "n": TREND_RUNS})).mappings().all()]
        trend_rows = (await conn.execute(text("""
            SELECT run_id, mode, count(DISTINCT question_id) FILTER (WHERE value = 'positive') AS positives
              FROM (SELECT DISTINCT ON (c.question_id, c.mode_lineage_id, coalesce(c.author_id::text, c.author_email))
                           q.run_id::text AS run_id, c.mode_lineage_id::text AS mode, c.question_id, c.value
                      FROM tracelab.lab_label_cells c JOIN tracelab.lab_questions q ON q.id = c.question_id
                     WHERE q.run_id = ANY(CAST(:runs AS uuid[]))
                     ORDER BY c.question_id, c.mode_lineage_id, coalesce(c.author_id::text, c.author_email),
                              c.created_at DESC, c.id DESC) x
             GROUP BY run_id, mode"""), {"runs": [r["id"] for r in recent]})).mappings().all()
        notes = (await conn.execute(text(
            "SELECT t.question_id::text AS question_id, s.note, s.reviewer_email, s.created_at "
            "FROM tracelab.step_labels s JOIN tracelab.lab_question_traces t ON t.turn_id = s.turn_id "
            "JOIN tracelab.lab_questions q ON q.id = t.question_id WHERE q.run_id = CAST(:rid AS uuid) "
            "AND s.superseded_by IS NULL AND NOT s.tombstone AND s.note IS NOT NULL ORDER BY s.created_at DESC"),
            {"rid": run["id"]})).mappings().all()
    filled = {(c["question_id"], c["mode"]) for c in cells}
    noted = {n["question_id"] for n in notes}
    reviewed = noted | {c["question_id"] for c in cells}
    n_q = int(run.get("n_questions") or 0)
    trend = {(t["run_id"], t["mode"]): int(t["positives"] or 0) for t in trend_rows}
    out_modes = []
    for md in modes:
        pos = sorted({c["question_id"] for c in cells if c["mode"] == md["lineage_id"] and c["value"] == "positive"})
        neg = {c["question_id"] for c in cells if c["mode"] == md["lineage_id"] and c["value"] == "negative"}
        examples = [{"note": n["note"], "by": n.get("reviewer_email"), "at": _iso(n["created_at"])}
                    for n in notes if n["question_id"] in set(pos)][:EXAMPLES]
        out_modes.append({**md, "positives": len(pos), "negatives": len(neg - set(pos)), "examples": examples,
                          "trend": [{"run": run_code(r["run_no"]), "positives": trend.get((r["id"], md["lineage_id"]), 0)}
                                    for r in reversed(recent)]})
    return {"run": run_out(run),
            "tiles": {"questions": n_q, "reviewed": len(reviewed), "traced": int(run.get("n_traced") or 0),
                      "modes": len(modes), "cells_filled": len(filled), "cells_total": n_q * len(modes),
                      "notes": len(notes)},
            "modes": out_modes, "runs": [{"code": run_code(r["run_no"]), "title": r["title"]} for r in reversed(recent)]}


# ── platform-wide roles: the consultant ───────────────────────────────────────────────────────────

def require_platform(m: labelling.Member) -> None:
    labelling.require_active(m)
    if m.role != "platform":
        raise Forbidden("platform_only", "Only the platform team can do this.")


async def list_platform_members(m: labelling.Member) -> list[dict[str, Any]]:
    require_platform(m)
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(
            "SELECT DISTINCT ON (user_id) id::text AS id, user_id::text AS user_id, email, lab_role, status, "
            "decided_by_email, created_at FROM tracelab.lab_platform_members ORDER BY user_id, created_at DESC, id DESC"
        ))).mappings().all()
    return [{"id": r["id"], "user_id": r["user_id"], "email": r.get("email"), "role": r["lab_role"], "status": r["status"],
             "decided_by_email": r.get("decided_by_email"), "since": _iso(r["created_at"])}
            for r in sorted(rows, key=lambda r: (r["status"] != "active", str(r.get("email") or "").lower()))]


async def decide_platform_member(m: labelling.Member, *, email: str, status: str,
                                 role: str = "consultant") -> dict[str, Any]:
    """The platform team gives a Hoistra account a platform-wide Trace Lab role, or takes it away (a new row)."""
    require_platform(m)
    if role != "consultant":
        raise Invalid("bad_role", "The only platform-wide role is consultant.")
    if status not in ("active", "removed"):
        raise Invalid("bad_status", "Choose active or removed.")
    email = _text(email, 320, "email", "Enter the person's Hoistra email.").lower()
    async with db.main_engine().connect() as conn:
        person = (await conn.execute(text(f"SELECT id::text AS id, email FROM {USERS} WHERE lower(email) = :e"),
                                     {"e": email})).mappings().first()
    if person is None:
        raise NotFound("user_not_found", "No Hoistra account has that email. Create the account in Hoistra first.")
    if person["id"] == m.user_id:
        raise Invalid("not_yourself", "The platform team already sees every company.")
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        await conn.execute(text(
            "INSERT INTO tracelab.lab_platform_members (user_id, email, lab_role, status, decided_by, decided_by_email) "
            "VALUES (CAST(:uid AS uuid), :email, :role, :status, CAST(:by AS uuid), :by_email)"),
            {"uid": person["id"], "email": person["email"], "role": role, "status": status, "by": m.user_id,
             "by_email": m.email})
    return next(x for x in await list_platform_members(m) if x["user_id"] == person["id"])
