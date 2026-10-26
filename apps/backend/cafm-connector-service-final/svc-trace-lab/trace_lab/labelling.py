"""Step labelling for the standalone Trace Lab app (20 Oct 2026): who may label for a company, how one answer was made
step by step, one person's Right / Wrong / Not sure and note on any step, and every label collected with who did it.

Who may label (lab_members, migration 0008):
* The platform team (a superadmin acting for a company) and the company's admins always may; they manage people.
* Anyone else gets in only when a company admin turns Trace Lab on for them, in Hoistra (Users & access) or on the
  app's People page, with a Trace Lab role (domain expert, tester, admin or user). Without that they read nothing: a
  plain Hoistra user may be limited to a few buildings, and labelling shows every answer of the company.
* An active member with the Trace Lab role `admin` also manages people and sees every label.
* A label is on a whole step or on text the person selected in it (a quote); a person may give several per step.

Reads of the chat go through conversations.py on the read-only engine, as the company's admin would see them (an active
member labels the whole company's answers); every label is stored in Trace Lab's own database with a copy of the answer's
evidence (evidence.snapshot), so the label survives the chat's 90-day purge. No SQL spans the two databases.
"""
from __future__ import annotations

import csv
import dataclasses
import io
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from . import auth, conversations, db, evidence
from .annotations import _constraint, _limits, _lock_busy
from .review_errors import Forbidden, Invalid, NotFound, Stale

ROLES = ("domain_expert", "tester", "admin", "user")
STATUSES = ("pending", "active", "removed")
LABELS = ("right", "wrong", "unsure")
NOTE_MAX = 2000
TITLE_MAX = 300
QUESTION_MAX = 2000
QUOTE_MAX = 2000
EXPECTED_MAX = 2000
GROUP_MIN, GROUP_MAX = 2, 30
USERS = "plenum_cafm.users"
LIST_MAX = 500
SNAPSHOT_REASON = "step_labelled"
LABELLABLE = frozenset({"user", "assistant", "model_call", "tool_call", "tool_result", "db_query", "plan", "step",
                        "system_note"})

PENDING_TEXT = "Your company admin has not given you access to Trace Lab. They can turn it on in Hoistra, under Users & access."
REMOVED_TEXT = "Your access to Trace Lab was removed by your company admin."
MANAGERS_ONLY = "Only company admins can do this."


def _iso(v: Any) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else (str(v) if v is not None else None)


@dataclass(frozen=True)
class Member:
    """The caller as Trace Lab sees them, for one company, or across every company (`all_companies`: the platform team
    acting for no company, or a consultant)."""
    principal: Any
    org: str | None
    user_id: str
    email: str | None
    role: str            # a Trace Lab role, "platform" for the platform team, "consultant" for a platform-wide reviewer
    status: str
    manages: bool
    all_companies: bool = False

    @property
    def active(self) -> bool:
        return self.status == "active"


def company_view(principal: Any, org: str) -> Any:
    """The principal conversations.py reads with: the company's whole set of answers. A superadmin acting for the company
    already reads it; anyone else reads it as that company's admin would, which only an active member ever reaches."""
    if principal.role == auth.SUPERADMIN:
        return principal
    return dataclasses.replace(principal, role="admin", organization_id=uuid.UUID(org), building_ids=None)


# ── members ─────────────────────────────────────────────────────────────────────────────────────

_MEMBER_COLS = ("id::text AS id, organization_id::text AS organization_id, user_id::text AS user_id, email, lab_role, "
                "status, decided_by::text AS decided_by, decided_by_email, created_at")
_MEMBER_HEAD = (f"SELECT {_MEMBER_COLS} FROM tracelab.lab_members WHERE organization_id = CAST(:org AS uuid) "
                "AND user_id = CAST(:uid AS uuid) AND superseded_by IS NULL")


def member_out(r: Any) -> dict[str, Any]:
    return {"id": r["id"], "user_id": r["user_id"], "email": r.get("email"), "role": r["lab_role"], "status": r["status"],
            "decided_by_email": r.get("decided_by_email"), "since": _iso(r.get("created_at"))}


async def _member_head(conn: Any, org: str, uid: str, *, lock: bool = False) -> Any:
    return (await conn.execute(text(_MEMBER_HEAD + (" FOR UPDATE" if lock else "")),
                               {"org": org, "uid": uid})).mappings().first()


async def access(principal: Any, organization_id: str | None, *, register: bool = False,
                 all_companies_ok: bool = False) -> Member:
    """Who the caller is in Trace Lab for the company the request acts for. `all_companies_ok` (runs, labels, progress)
    lets the platform team act for no company and see every company. A consultant (lab_platform_members) sees every
    company and acts for the company a request names. `register` is kept for older callers and records nothing."""
    uid, email = str(principal.user_id), (principal.email or None)
    if principal.role == auth.SUPERADMIN:
        org = auth.acting_org(principal, organization_id)
        if org is None:
            if all_companies_ok:
                return Member(principal, None, uid, email, "platform", "active", True, True)
            raise db.http_error(400, "name_a_company", auth.NAME_A_COMPANY)
        return Member(principal, org, uid, email, "platform", "active", True, True)
    consultant = await platform_role(uid)
    if consultant is not None:
        named = auth._named(organization_id) if organization_id else None
        own = str(principal.organization_id) if principal.organization_id else None
        if consultant["status"] == "active":
            return Member(principal, named or (None if all_companies_ok else own), uid, email, "consultant", "active",
                          False, True)
    org = auth.acting_org(principal, organization_id)
    if org is None:
        raise db.http_error(403, "no_company", "Your account belongs to no company.")
    if principal.role == "admin":
        return Member(principal, org, uid, email, "admin", "active", True)
    async with db.lab_engine().connect() as conn:
        head = await _member_head(conn, org, uid)
    if head is None:                                     # nobody turned Trace Lab on for this person
        return Member(principal, org, uid, email, "user", "none", False)
    role, status = head["lab_role"], head["status"]
    return Member(principal, org, uid, email, role, status, role == "admin" and status == "active")


async def platform_role(uid: str) -> dict[str, Any] | None:
    """The newest platform-wide Trace Lab role of a person (lab_platform_members, migration 0011), or None."""
    async with db.lab_engine().connect() as conn:
        row = (await conn.execute(text(
            "SELECT lab_role, status, email, created_at FROM tracelab.lab_platform_members WHERE user_id = CAST(:uid AS uuid) "
            "ORDER BY created_at DESC, id DESC LIMIT 1"), {"uid": uid})).mappings().first()
    return dict(row) if row else None


def for_org(m: Member, org: str | None) -> Member:
    """The caller acting for the company `org` (a question's or an answer's): the platform team and a consultant may act
    for any company; anyone else only for their own. NotFound otherwise, so another company's records stay unseen."""
    if not org:
        raise NotFound("question_not_found", "That question is not in Trace Lab.")
    if m.all_companies:
        return dataclasses.replace(m, org=str(org))
    if str(org) != str(m.org):
        raise NotFound("question_not_found", "That question is not in Trace Lab.")
    return m


def require_active(m: Member) -> Member:
    if m.status == "removed":
        raise db.http_error(403, "lab_removed", REMOVED_TEXT)
    if m.status == "none":
        raise db.http_error(403, "lab_no_access", PENDING_TEXT)
    if not m.active:
        raise db.http_error(403, "lab_pending", PENDING_TEXT)
    return m


def require_manager(m: Member) -> Member:
    require_active(m)
    if not m.manages:
        raise db.http_error(403, "lab_admins_only", MANAGERS_ONLY)
    return m


def me_out(m: Member) -> dict[str, Any]:
    return {"user_id": m.user_id, "email": m.email, "organization_id": m.org, "role": m.role, "status": m.status,
            "manages": m.manages, "all_companies": m.all_companies}


async def list_members(m: Member) -> list[dict[str, Any]]:
    require_manager(m)
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(
            f"SELECT {_MEMBER_COLS} FROM tracelab.lab_members WHERE organization_id = CAST(:org AS uuid) "
            "AND superseded_by IS NULL ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END, "
            "lower(coalesce(email, ''))"), {"org": m.org})).mappings().all()
    return [member_out(r) for r in rows]


async def decide_member(m: Member, user_id: str, *, role: str, status: str, base: str | None) -> dict[str, Any]:
    """A company admin sets one person's Trace Lab role and status, as a new version. Stale when `base` is not the head."""
    require_manager(m)
    if role not in ROLES:
        raise Invalid("bad_role", "Choose domain expert, tester, admin or user.")
    if status not in STATUSES:
        raise Invalid("bad_status", "Choose pending, active or removed.")
    try:
        uid = str(uuid.UUID(str(user_id)))
    except ValueError:
        raise NotFound("member_not_found", "That person has not opened Trace Lab yet.") from None
    if uid == m.user_id and m.role != "platform":
        raise Invalid("not_yourself", "You cannot lower your own access.")
    new_id = str(uuid.uuid4())
    person = await _company_user(m.org, uid)
    try:
        async with db.lab_engine().begin() as conn:
            await _limits(conn)
            head = await _member_head(conn, m.org, uid, lock=True)
            if (str(head["id"]) if head else None) != (str(base) if base else None):
                raise Stale(member_out(head) if head else None)
            if head is None:                                      # the first decision: who the person is comes from Hoistra
                row = (await conn.execute(text(
                    "INSERT INTO tracelab.lab_members (id, organization_id, user_id, email, lab_role, status, decided_by, "
                    "decided_by_email) VALUES (CAST(:id AS uuid), CAST(:org AS uuid), CAST(:uid AS uuid), :email, :role, "
                    f":status, CAST(:by AS uuid), :by_email) RETURNING {_MEMBER_COLS}"),
                    {"id": new_id, "org": m.org, "uid": uid, "email": person["email"], "role": role, "status": status,
                     "by": m.user_id, "by_email": m.email})).mappings().one()
                return member_out(row)
            await conn.execute(text("UPDATE tracelab.lab_members SET superseded_by = CAST(:new AS uuid), "
                                    "superseded_at = now() WHERE id = CAST(:head AS uuid) AND superseded_by IS NULL"),
                               {"new": new_id, "head": str(head["id"])})
            row = (await conn.execute(text(
                "INSERT INTO tracelab.lab_members (id, organization_id, user_id, email, lab_role, status, decided_by, "
                "decided_by_email, supersedes) VALUES (CAST(:id AS uuid), CAST(:org AS uuid), CAST(:uid AS uuid), :email, "
                ":role, :status, CAST(:by AS uuid), :by_email, CAST(:head AS uuid)) "
                f"RETURNING {_MEMBER_COLS}"), {"id": new_id, "org": m.org, "uid": uid, "email": head["email"],
                                               "role": role, "status": status, "by": m.user_id, "by_email": m.email,
                                               "head": str(head["id"])})).mappings().one()
    except IntegrityError as exc:
        if _constraint(exc) in ("ux_tl_members_head", "ux_tl_members_supersedes"):
            raise Stale(None) from exc
        raise
    except DBAPIError as exc:
        if _lock_busy(exc):
            raise Stale(None) from exc
        raise
    return member_out(row)


async def _company_user(org: str, uid: str) -> dict[str, Any]:
    """The Hoistra account `uid`, which must belong to `org` (read-only, from the application database)."""
    async with db.main_engine().connect() as conn:
        row = (await conn.execute(text(f"SELECT id::text AS id, email, organization_id::text AS org, platform_role AS role "
                                       f"FROM {USERS} WHERE id::text = :uid"), {"uid": uid})).mappings().first()
    if row is None or row["org"] != org:
        raise NotFound("member_not_found", "That person is not a user of this company.")
    return dict(row)


async def member_of(m: Member, user_id: str) -> dict[str, Any]:
    """One person's Trace Lab access, for Hoistra's Users & access screen: their head row, or no access."""
    require_manager(m)
    try:
        uid = str(uuid.UUID(str(user_id)))
    except ValueError:
        raise NotFound("member_not_found", "That person is not a user of this company.") from None
    async with db.lab_engine().connect() as conn:
        head = await _member_head(conn, m.org, uid)
    return member_out(head) if head else {"id": None, "user_id": uid, "email": None, "role": None, "status": "none",
                                          "decided_by_email": None, "since": None}


# ── questions ───────────────────────────────────────────────────────────────────────────────────

async def list_questions(m: Member, *, q: str | None, since: datetime | None, until: datetime | None,
                         cursor: str | None, limit: int) -> dict[str, Any]:
    require_active(m)
    f = conversations.make_filter(since=since, until=until, q=q)
    return await conversations.list_conversations(company_view(m.principal, m.org), m.org, f, cursor=cursor, limit=limit)


async def get_question(m: Member, key: str, *, turn_id: str | None) -> dict[str, Any] | None:
    require_active(m)
    return await conversations.get_conversation(company_view(m.principal, m.org), m.org, key, turn_id=turn_id)


# ── step labels ─────────────────────────────────────────────────────────────────────────────────

_LABEL_COLS = ("id::text AS id, organization_id::text AS organization_id, conversation_key, turn_id, step_key, step_kind, "
               "step_title, question, label, note, tombstone, reviewer_id::text AS reviewer_id, reviewer_email, "
               "reviewer_role, supersedes::text AS supersedes, created_at, coalesce(lineage_id, id)::text AS lineage_id, quote, "
               "group_id::text AS group_id, expected, only_if, "
               "(SELECT 'R' || lr.run_no || '-' || lq.seq_no FROM tracelab.lab_question_traces lt "
               "JOIN tracelab.lab_questions lq ON lq.id = lt.question_id JOIN tracelab.lab_runs lr ON lr.id = lq.run_id "
               "WHERE lt.turn_id = step_labels.turn_id ORDER BY lt.created_at, lr.run_no, lq.seq_no LIMIT 1) AS question_code")
_LABEL_HEAD = (f"SELECT {_LABEL_COLS} FROM tracelab.step_labels WHERE organization_id = CAST(:org AS uuid) "
               "AND turn_id = :tid AND coalesce(lineage_id, id) = CAST(:lin AS uuid) AND superseded_by IS NULL")


def label_out(r: Any) -> dict[str, Any] | None:
    if r is None:
        return None
    return {"id": r["id"], "lineage_id": r.get("lineage_id") or r["id"], "quote": r.get("quote"),
            "conversation_key": r["conversation_key"], "turn_id": r["turn_id"], "step_key": r["step_key"],
            "step_kind": r["step_kind"], "step_title": r.get("step_title"), "question": r.get("question"),
            "label": r.get("label"), "note": r.get("note"), "cleared": bool(r.get("tombstone")),
            "reviewer_id": r["reviewer_id"], "reviewer_email": r.get("reviewer_email"),
            "reviewer_role": r["reviewer_role"], "edited": r.get("supersedes") is not None, "at": _iso(r.get("created_at")),
            "group_id": r.get("group_id"), "expected": r.get("expected"), "only_if": r.get("only_if"),
            "question_code": r.get("question_code"), "organization_id": r.get("organization_id")}


def validate_label(label: str | None, note: str | None, step_title: str | None) -> tuple[str | None, str | None]:
    """A note is free text; the Right / Wrong / Not sure tag is optional (migration 0011), but a label needs one of them."""
    note = (note or "").strip() or None
    if label is not None and label not in LABELS:
        raise Invalid("bad_label", "Choose Right, Wrong or Not sure.")
    if label is None and note is None:
        raise Invalid("note_required", "Write a note.")
    if note is not None and len(note) > NOTE_MAX:
        raise Invalid("note_too_long", "Keep the note under 2,000 characters.")
    title = (step_title or "").strip()[:TITLE_MAX] or None
    return note, title


def validate_quote(quote: str | None) -> str | None:
    """The selected text, whitespace runs kept as typed but trimmed at the ends; None for a whole-step label."""
    q = (quote or "").strip()
    if not q:
        return None
    if len(q) > QUOTE_MAX:
        raise Invalid("quote_too_long", "Select less text: up to 2,000 characters.")
    return q


def validate_expected(expected: str | None, only_if: str | None) -> tuple[str | None, str | None]:
    """What should happen in the step, and when (the condition): each trimmed, None when empty, up to 2,000 characters."""
    out = []
    for value, name in ((expected, "What should happen"), (only_if, "Only if")):
        v = (value or "").strip() or None
        if v is not None and len(v) > EXPECTED_MAX:
            raise Invalid("expected_too_long", f"Keep \u201c{name}\u201d under 2,000 characters.")
        out.append(v)
    return out[0], out[1]


def _question_of(trace: dict[str, Any]) -> str | None:
    for msg in trace.get("messages") or []:
        if msg.get("role") == "user" and msg.get("text"):
            return str(msg["text"])[:QUESTION_MAX]
    return None


async def _turn(m: Member, turn_id: str) -> dict[str, Any]:
    out = await conversations.get_turn(company_view(m.principal, m.org), m.org, turn_id)
    if out is None:
        raise NotFound("turn_not_found")
    return out


OUTPUT_SUFFIX = "#output"


def _step_kind(trace: dict[str, Any], step_key: str) -> str:
    """The kind of the step a note is on. Besides a message or an agent, a note may be on a step's output block
    ("<step>#output"), on the question as a whole ("<turn>:scenario") or on the orchestrator step ("<turn>:orchestrator")."""
    tid = str(trace.get("id") or "")
    if step_key in (f"{tid}:scenario", f"{tid}:orchestrator"):
        return step_key.rsplit(":", 1)[1]
    if step_key.endswith(OUTPUT_SUFFIX):
        base = step_key[: -len(OUTPUT_SUFFIX)]
        return "output" if base == f"{tid}:orchestrator" else _step_kind(trace, base) + "_output"
    for msg in trace.get("messages") or []:
        if msg.get("id") == step_key:
            kind = str(msg.get("role") or "")
            if kind in LABELLABLE:
                return kind
            break
    # An agent the orchestrator called is a step of its own on the page ("<turn>:<agent span>:agent"), as on Hoistra's
    # Run trace; the record keeps it among its groups, not its messages.
    for group in trace.get("groups") or []:
        if group.get("span_id") and step_key == f"{trace.get('id')}:{group['span_id']}:agent":
            return "agent"
    raise Invalid("step_not_in_answer", "That step is not part of this answer.")


async def labels_for_turn(m: Member, turn_id: str) -> list[dict[str, Any]]:
    """Everyone's live labels on the steps of one answer: who labelled what is visible to every active member."""
    require_active(m)
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(
            f"SELECT {_LABEL_COLS} FROM tracelab.step_labels WHERE organization_id = CAST(:org AS uuid) AND turn_id = :tid "
            "AND superseded_by IS NULL AND NOT tombstone ORDER BY created_at"), {"org": m.org, "tid": turn_id})).mappings().all()
    return [label_out(r) for r in rows]


async def save_label(m: Member, turn_id: str, *, step_key: str | None, label: str, note: str | None,
                     step_title: str | None, quote: str | None = None, lineage_id: str | None = None,
                     base: str | None = None, expected: str | None = None, only_if: str | None = None) -> dict[str, Any]:
    """A new label (no lineage_id: on `step_key`, on the whole step or on the selected `quote`) or a new version of one of
    the caller's labels (lineage_id + base). Either may say what should happen in the step (`expected`) and when
    (`only_if`). The answer must be the company's and the step part of it; its evidence is copied first. Stale (409,
    with the head) when `base` is not that label's head. A version keeps its label's selected text and group."""
    require_active(m)
    note, title = validate_label(label, note, step_title)
    expected, only_if = validate_expected(expected, only_if)
    got = await _turn(m, turn_id)
    trace = got["trace"]
    if lineage_id:
        return await _write(m, turn_id, lineage=_uid(lineage_id), new_step=None, label=label, note=note, title=title,
                            quote=None, tomb=False, base=base, expected=expected, only_if=only_if)
    if not step_key:
        raise Invalid("step_required", "Choose the step to label.")
    kind = _step_kind(trace, step_key)
    await evidence.snapshot([turn_id], organization_id=m.org, reason=SNAPSHOT_REASON)
    return await _write(m, turn_id, lineage=None, label=label, note=note, title=title, quote=validate_quote(quote),
                        tomb=False, base=None, expected=expected, only_if=only_if,
                        new_step={"key": step_key, "kind": kind, "ckey": got["conversation_key"],
                                  "question": _question_of(trace)})


def validate_group(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The steps of a group label: 2 to 30 different steps, each with its own title, what should happen and when."""
    if not GROUP_MIN <= len(items) <= GROUP_MAX:
        raise Invalid("group_size", f"Choose between {GROUP_MIN} and {GROUP_MAX} steps to label together.")
    keys = [str(i.get("step_key") or "") for i in items]
    if any(not k for k in keys):
        raise Invalid("step_required", "Every step in the group needs its step.")
    if len(set(keys)) != len(keys):
        raise Invalid("group_repeats_a_step", "Each step can be in the group only once.")
    out = []
    for i in items:
        expected, only_if = validate_expected(i.get("expected"), i.get("only_if"))
        out.append({"key": str(i["step_key"]), "title": (i.get("step_title") or "").strip()[:TITLE_MAX] or None,
                    "expected": expected, "only_if": only_if})
    return out


async def save_group(m: Member, turn_id: str, *, label: str, note: str | None,
                     items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Several steps of one answer labelled together, in one transaction: one Right/Wrong/Not sure and one note for all,
    and for each step what should happen and when. Each step gets its own label (its own history); they share a
    group_id, so they show and can be removed together."""
    require_active(m)
    note, _ = validate_label(label, note, None)
    steps = validate_group(items)
    got = await _turn(m, turn_id)
    trace = got["trace"]
    for st in steps:
        st["kind"] = _step_kind(trace, st["key"])
    await evidence.snapshot([turn_id], organization_id=m.org, reason=SNAPSHOT_REASON)
    group_id, question = str(uuid.uuid4()), _question_of(trace)
    rows = []
    try:
        async with db.lab_engine().begin() as conn:
            await _limits(conn)
            for st in steps:
                new_id = str(uuid.uuid4())
                rows.append((await conn.execute(text(_INSERT), {
                    "id": new_id, "lin": new_id, "org": m.org, "ckey": got["conversation_key"], "tid": turn_id,
                    "step": st["key"], "kind": st["kind"], "title": st["title"], "question": question, "quote": None,
                    "label": label, "note": note, "tomb": False, "me": m.user_id, "email": m.email, "role": m.role,
                    "sup": None, "grp": group_id, "expected": st["expected"], "only_if": st["only_if"]})).mappings().one())
    except DBAPIError as exc:
        if _lock_busy(exc):
            raise Stale(None) from exc
        raise
    return [label_out(r) for r in rows]


async def clear_group(m: Member, turn_id: str, *, group_id: str) -> dict[str, Any]:
    """Remove every live label of one of the caller's groups on this answer: a tombstone version of each."""
    require_active(m)
    await _turn(m, turn_id)
    gid = _uid(group_id)
    removed = 0
    try:
        async with db.lab_engine().begin() as conn:
            await _limits(conn)
            heads = (await conn.execute(text(
                f"SELECT {_LABEL_COLS} FROM tracelab.step_labels WHERE organization_id = CAST(:org AS uuid) "
                "AND turn_id = :tid AND group_id = CAST(:grp AS uuid) AND superseded_by IS NULL AND NOT tombstone "
                "ORDER BY created_at FOR UPDATE"), {"org": m.org, "tid": turn_id, "grp": gid})).mappings().all()
            if not heads:
                raise NotFound("label_not_found", "Those labels were not found.")
            if any(h["reviewer_id"] != m.user_id for h in heads):
                raise Forbidden("not_your_label", "Only the person who gave a label can change it.")
            for h in heads:
                new_id = str(uuid.uuid4())
                await conn.execute(text("UPDATE tracelab.step_labels SET superseded_by = CAST(:new AS uuid), "
                                        "superseded_at = now() WHERE id = CAST(:head AS uuid) AND superseded_by IS NULL"),
                                   {"new": new_id, "head": str(h["id"])})
                await conn.execute(text(_INSERT), {
                    "id": new_id, "lin": h["lineage_id"], "org": m.org, "ckey": h["conversation_key"], "tid": turn_id,
                    "step": h["step_key"], "kind": h["step_kind"], "title": h["step_title"], "question": h["question"],
                    "quote": h["quote"], "label": None, "note": None, "tomb": True, "me": m.user_id, "email": m.email,
                    "role": m.role, "sup": str(h["id"]), "grp": h["group_id"], "expected": None, "only_if": None})
                removed += 1
    except IntegrityError as exc:
        if _constraint(exc) in ("ux_tl_step_labels_lineage_head", "ux_tl_step_labels_supersedes"):
            raise Stale(None) from exc
        raise
    except DBAPIError as exc:
        if _lock_busy(exc):
            raise Stale(None) from exc
        raise
    return {"group_id": gid, "removed": removed}


async def clear_label(m: Member, turn_id: str, *, lineage_id: str, base: str) -> dict[str, Any]:
    """Remove one of the caller's labels: a tombstone version."""
    require_active(m)
    await _turn(m, turn_id)
    return await _write(m, turn_id, lineage=_uid(lineage_id), new_step=None, label=None, note=None, title=None, quote=None,
                        tomb=True, base=base, expected=None, only_if=None)


def _uid(v: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except ValueError:
        raise NotFound("label_not_found", "That label was not found.") from None


_INSERT = ("INSERT INTO tracelab.step_labels (id, lineage_id, organization_id, conversation_key, turn_id, step_key, "
           "step_kind, step_title, question, quote, label, note, tombstone, reviewer_id, reviewer_email, reviewer_role, "
           "supersedes, group_id, expected, only_if) VALUES (CAST(:id AS uuid), CAST(:lin AS uuid), CAST(:org AS uuid), "
           ":ckey, :tid, :step, :kind, :title, :question, :quote, :label, :note, :tomb, CAST(:me AS uuid), :email, :role, "
           f"CAST(:sup AS uuid), CAST(:grp AS uuid), :expected, :only_if) RETURNING {_LABEL_COLS}")


async def _write(m: Member, turn_id: str, *, lineage: str | None, new_step: dict[str, Any] | None, label: str | None,
                 note: str | None, title: str | None, quote: str | None, tomb: bool, base: str | None,
                 expected: str | None = None, only_if: str | None = None) -> dict[str, Any]:
    new_id = str(uuid.uuid4())
    try:
        async with db.lab_engine().begin() as conn:
            await _limits(conn)
            head = None
            if lineage:
                head = (await conn.execute(text(_LABEL_HEAD + " FOR UPDATE"),
                                           {"org": m.org, "tid": turn_id, "lin": lineage})).mappings().first()
                if head is None:
                    raise NotFound("label_not_found", "That label was not found.")
                if head["reviewer_id"] != m.user_id:
                    raise Forbidden("not_your_label", "Only the person who gave a label can change it.")
                if str(head["id"]) != str(base or ""):
                    raise Stale(label_out(head))
                if head["tombstone"]:
                    raise NotFound("label_not_found", "That label was removed.")
                await conn.execute(text("UPDATE tracelab.step_labels SET superseded_by = CAST(:new AS uuid), "
                                        "superseded_at = now() WHERE id = CAST(:head AS uuid) AND superseded_by IS NULL"),
                                   {"new": new_id, "head": str(head["id"])})
            src = head or {}
            step = new_step or {"key": src.get("step_key"), "kind": src.get("step_kind"), "ckey": src.get("conversation_key"),
                                "question": src.get("question")}
            row = (await conn.execute(text(_INSERT), {
                "id": new_id, "lin": lineage or new_id, "org": m.org, "ckey": step["ckey"], "tid": turn_id,
                "step": step["key"], "kind": step["kind"], "title": title if title is not None else src.get("step_title"),
                "question": step["question"], "quote": quote if new_step else src.get("quote"), "label": label,
                "note": note, "tomb": tomb, "me": m.user_id, "email": m.email, "role": m.role,
                "sup": str(head["id"]) if head else None, "grp": src.get("group_id"), "expected": expected,
                "only_if": only_if})).mappings().one()
    except IntegrityError as exc:
        if _constraint(exc) in ("ux_tl_step_labels_lineage_head", "ux_tl_step_labels_supersedes"):
            raise Stale(None) from exc
        raise
    except DBAPIError as exc:
        if _lock_busy(exc):
            raise Stale(None) from exc
        raise
    return label_out(row)


# ── every label, collected ──────────────────────────────────────────────────────────────────────

def _filters(m: Member, *, reviewer: str | None, role: str | None, label: str | None, since: datetime | None,
             until: datetime | None, q: str | None) -> tuple[str, dict[str, Any]]:
    where = ["superseded_by IS NULL", "NOT tombstone"]
    p: dict[str, Any] = {}
    if not m.all_companies or m.org:
        where.insert(0, "organization_id = CAST(:org AS uuid)")
        p["org"] = m.org
    if not (m.manages or m.all_companies):                  # a labeller sees their own labels; admins see everyone's
        where.append("reviewer_id = CAST(:me AS uuid)")
        p["me"] = m.user_id
    elif reviewer:
        where.append("lower(coalesce(reviewer_email, '')) = lower(:reviewer)")
        p["reviewer"] = reviewer.strip()
    if role:
        if role not in ROLES + ("platform", "consultant"):
            raise Invalid("bad_role", "Unknown Trace Lab role.")
        where.append("reviewer_role = :role")
        p["role"] = role
    if label:
        if label not in LABELS:
            raise Invalid("bad_label", "Choose Right, Wrong or Not sure.")
        where.append("label = :label")
        p["label"] = label
    if since:
        where.append("created_at >= :since")
        p["since"] = since
    if until:
        where.append("created_at < :until")
        p["until"] = until
    if q:
        where.append("(question ILIKE :q OR note ILIKE :q OR step_title ILIKE :q OR quote ILIKE :q OR expected ILIKE :q "
                     "OR only_if ILIKE :q)")
        p["q"] = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return " AND ".join(where), p


async def list_labels(m: Member, *, reviewer: str | None = None, role: str | None = None, label: str | None = None,
                      since: datetime | None = None, until: datetime | None = None, q: str | None = None,
                      limit: int = 100, offset: int = 0) -> dict[str, Any]:
    require_active(m)
    where, p = _filters(m, reviewer=reviewer, role=role, label=label, since=since, until=until, q=q)
    limit, offset = max(1, min(int(limit), LIST_MAX)), max(0, int(offset))
    async with db.lab_engine().connect() as conn:
        total = (await conn.execute(text(f"SELECT count(*) FROM tracelab.step_labels WHERE {where}"), p)).scalar_one()
        rows = (await conn.execute(text(f"SELECT {_LABEL_COLS} FROM tracelab.step_labels WHERE {where} "
                                        "ORDER BY created_at DESC, id LIMIT :lim OFFSET :off"),
                                   {**p, "lim": limit, "off": offset})).mappings().all()
        counts = (await conn.execute(text(f"SELECT label, count(*) AS n FROM tracelab.step_labels WHERE {where} "
                                          "GROUP BY label"), p)).mappings().all()
    return {"total": int(total), "labels": [label_out(r) for r in rows], "by_label": {r["label"]: int(r["n"]) for r in counts},
            "everyone": m.manages or m.all_companies, "me": m.user_id}


CSV_HEADER = ("When (UTC)", "Question", "Step", "Step kind", "Selected text", "Label", "Note", "Who", "Trace Lab role",
              "Answer id", "Step id", "What should happen", "Only if", "Group")
LABEL_TEXT = {"right": "Right", "wrong": "Wrong", "unsure": "Not sure"}
ROLE_TEXT = {"domain_expert": "Domain expert", "tester": "Tester", "admin": "Admin", "user": "User",
             "platform": "Platform team", "consultant": "Consultant"}


def _cell(v: Any) -> str:
    """A CSV cell a spreadsheet will not run: text starting with = + - @ (or a tab or carriage return) gets a quote mark."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def csv_text(labels: list[dict[str, Any]]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_HEADER)
    for x in labels:
        w.writerow([_cell(c) for c in (x.get("at"), x.get("question"), x.get("step_title"), x.get("step_kind"), x.get("quote"),
                                       LABEL_TEXT.get(x.get("label") or "", x.get("label")), x.get("note"),
                                       x.get("reviewer_email"), ROLE_TEXT.get(x.get("reviewer_role"), x.get("reviewer_role")),
                                       x.get("turn_id"), x.get("step_key"), x.get("expected"), x.get("only_if"),
                                       x.get("group_id"))])
    return out.getvalue()
