"""The coding agent, inside the Trace Lab container (Bala, 5 Nov 2026).

Bala's review of the correction loop:
1. Everything runs from the same container, generation included - no separate container.
2. The agent may auto-label and suggest changes; a person reviews and approves before anything proceeds.
3. After approval the agent updates the spec MD - no branch.
4. The approved text stays in one place while testing repeats; nothing reaches the repo until a person confirms.
5-7. The MD lives in one place and is read on the fly, from the main MD through to the sub-agent MDs; an update needs
   no review, merge, rebuild or redeploy.
8. The final MD goes into the repo only at commit, once testing is complete.

So the agent's "fix" is a change to an instruction document, not to code. The one place those documents live is
svc-deepagents' instruction store (services/skill_overlays.py); every agent reads it at runtime. Trace Lab never
writes Hoistra's database: it asks deepagents' own API (/api/skill-lab/overlays) with the person's sign-in, so every
step is that person's, and deepagents checks they are an administrator.

    notes --(propose_from_notes: agent)--> failure modes + labels (proposals) --(people approve)-->
    SPEC.md version (people approve) --(publish_spec: agent)--> SPEC.md under test
    --(draft_fix: agent)--> instruction change (proposal) --(people approve)--> under test
    --(Ask again reads the text under test; Compare)--> --(people: Make live)--> every agent reads it
    --(Export for commit)--> the repo

A model is called only here and in the reruns; a proposal is never acted on until a person approves it.
"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile
from collections import Counter
from typing import Any

import httpx
import structlog
from sqlalchemy import text

from . import db, labelling, loop, runs
from .annotations import _limits
from .review_errors import Forbidden, Invalid, NotFound

log = structlog.get_logger(__name__)

ROLE = "tracelab_agent"
ENV_MODEL = "TRACE_LAB_AGENT_MODEL"
AGENT_EMAIL = "trace-lab-agent"
NOTE_WINDOW_DAYS = 14
MAX_NOTES = 60
MODE_NAME = re.compile(r"^[a-z][a-z0-9_]{2,59}$")
SECTION_MAX = 4000


#: deep-agents in the all-in-one container (deploy/allinone/supervisord.conf, --port 8008). The default, so the approved
#: Azure environment needs no new variable; a local compose stack sets TRACE_LAB_DEEPAGENTS_URL=http://svc-deepagents:8008.
DEFAULT_DEEPAGENTS_URL = "http://127.0.0.1:8008"


def deepagents_url() -> str:
    """Where deepagents answers: TRACE_LAB_DEEPAGENTS_URL, else the all-in-one container's own deep-agents."""
    return (os.environ.get("TRACE_LAB_DEEPAGENTS_URL") or DEFAULT_DEEPAGENTS_URL).rstrip("/")


async def _deepagents(method: str, path: str, authorization: str | None, body: Any = None) -> dict[str, Any]:
    """deepagents' instruction-store API, as the signed-in person. A refusal is said in the person's words."""
    if not authorization:
        raise Forbidden("signed_out", "Sign in again: this step is taken in your name.")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.request(method, deepagents_url() + "/api/skill-lab" + path, json=body,
                                 headers={"Authorization": authorization, "Accept": "application/json"})
    if r.status_code == 403:
        raise Forbidden("admins_only", "Only an administrator can change the agents' instructions.")
    if r.status_code == 404:
        raise NotFound("not_found", "That instruction change is not waiting for this step.")
    if r.status_code >= 400:
        raise Invalid("store_unavailable", "The instruction store did not accept this (" + str(r.status_code) + ").")
    return r.json()


async def _ask(system: str, prompt: str, schema: dict, max_tokens: int) -> dict[str, Any]:
    from src.agents import skill_lab

    return await skill_lab._ask_json(system, prompt, schema, role=ROLE, env_model=ENV_MODEL, max_tokens=max_tokens)


# ── 1. notes -> proposed failure modes and labels ───────────────────────────────────────────────

NOTES_SYSTEM = ("You group reviewers' notes on a facilities-management assistant's answers into failure modes. You "
                "only PROPOSE: a person approves each one. A failure mode is one kind of mistake, named in snake_case, "
                "with a one-sentence definition a reviewer can apply to any answer. Reuse an existing mode only when "
                "its definition names the same CAUSE as the note (existing true, its exact name) - a similar symptom, "
                "such as records left out, is not enough: when the cause differs or you are unsure, propose a new mode. "
                "Never invent a problem no note states. A note that says the answer is correct is no failure.")
NOTES_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["modes"],
    "properties": {"modes": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["name", "definition", "existing", "questions"],
        "properties": {"name": {"type": "string"}, "definition": {"type": "string"}, "existing": {"type": "boolean"},
                       "questions": {"type": "array", "items": {"type": "string"}}}}}}}


async def propose_from_notes(m: labelling.Member, *, run: str | None = None) -> dict[str, Any]:
    """Read people's recent notes on questions nobody has labelled yet, and propose failure modes and positive labels.
    Nothing counts until a person approves the mode and accepts the label (Loop, Labels).

    `run` (R40): only that run's notes - the run the person has open (5 Nov 2026: without it the agent also read
    older notes on other runs and proposed a mode from one of them, which read as a proposal for the new question)."""
    runs.require_taxonomist(m)
    where, p = runs._scope(m, "r")
    run_code = None
    if run:
        run_no = runs.parse_run_code(run)
        run_code = f"R{run_no}"
        where, p = f"({where}) AND r.run_no = :run_no", {**p, "run_no": run_no}
    async with db.lab_engine().connect() as conn:
        notes = [dict(r) for r in (await conn.execute(text(f"""
            SELECT 'R' || r.run_no || '-' || q.seq_no AS code, q.question, s.note, s.created_at
              FROM tracelab.step_labels s
              JOIN tracelab.lab_question_traces t ON t.turn_id = s.turn_id
              JOIN tracelab.lab_questions q ON q.id = t.question_id
              JOIN tracelab.lab_runs r ON r.id = q.run_id
             WHERE {where} AND s.superseded_by IS NULL AND NOT s.tombstone AND s.note IS NOT NULL
               AND s.created_at > now() - make_interval(days => :days)
               AND NOT EXISTS (SELECT 1 FROM tracelab.lab_label_cells c WHERE c.question_id = q.id
                                  AND COALESCE(c.source, 'manual') <> 'coding_agent')
             ORDER BY s.created_at DESC LIMIT :lim"""), {**p, "days": NOTE_WINDOW_DAYS, "lim": MAX_NOTES})).mappings().all()]
        live, proposed = await loop.resolve_modes(conn)
    if not notes:
        return {"proposed_modes": [], "labels": 0, "notes_read": 0, "run": run_code,
                "message": ("No new notes on unlabelled questions of " + run_code + "." if run_code
                            else "No new notes on unlabelled questions in the last two weeks.")}
    known = {md["name"]: md["lineage_id"] for md in live}
    known_proposed = {(md.get("name") or ""): md["lineage_id"] for md in proposed}
    prompt = ("EXISTING FAILURE MODES\n" + ("\n".join(f"- {md['name']}: {md.get('definition') or ''}" for md in live)
                                            or "(none)")
              + "\n\nNOTES (question code | question | note)\n"
              + "\n".join(f"- {n['code']} | {n['question'][:200]} | {n['note'][:600]}" for n in notes))
    reply = await _ask(NOTES_SYSTEM, prompt, NOTES_SCHEMA, max_tokens=2500)
    codes = {n["code"] for n in notes}
    made, labels = [], 0
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        for mode in (reply.get("modes") or [])[:10]:
            name = str(mode.get("name") or "").strip().lower()
            qs = [c for c in dict.fromkeys(str(x).strip().upper() for x in mode.get("questions") or []) if c in codes]
            if not MODE_NAME.match(name) or not qs:
                continue
            lin = known.get(name) or known_proposed.get(name)
            if lin is None:
                lin = (await conn.execute(text(
                    "INSERT INTO tracelab.lab_failure_modes (name, definition, created_by_email) "
                    "VALUES (:n, :d, :by) RETURNING id::text"),
                    {"n": name, "d": str(mode.get("definition") or "")[:runs.DEFINITION_MAX] or None,
                     "by": AGENT_EMAIL})).scalar_one()
                known_proposed[name] = lin
                made.append({"name": name, "questions": qs})
            for code in qs:
                # Asked again, the agent repeats no suggestion it already made.
                added = await conn.execute(text(
                    "INSERT INTO tracelab.lab_label_cells (question_id, mode_lineage_id, value, author_email, author_role, "
                    "source) SELECT tracelab.question_id(:c), CAST(:m AS uuid), 'positive', :by, 'coding agent', "
                    "'coding_agent' WHERE NOT EXISTS (SELECT 1 FROM tracelab.lab_label_cells x WHERE "
                    "x.question_id = tracelab.question_id(:c) AND x.mode_lineage_id = CAST(:m AS uuid) "
                    "AND x.source = 'coding_agent')"), {"c": code, "m": lin, "by": AGENT_EMAIL})
                labels += added.rowcount or 0
    return {"proposed_modes": made, "labels": labels, "notes_read": len(notes), "run": run_code,
            "questions": sorted({n["code"] for n in notes})}


# ── 2. an approved rule -> a proposed change to one instruction document ────────────────────────

FIX_SYSTEM = ("You correct a facilities-management assistant by changing ONE of its instruction documents, never code. "
              "You are given an approved rule, the failure it guards against, people's notes and the answers that "
              "failed. Choose the document the agent reads for this kind of question and write ONE short section to "
              "add to it: plain instructions the agent can follow, naming the rule, with no examples copied from the "
              "failing answers and no data values. A person reviews it before anything changes.")
FIX_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["doc", "title", "text", "why"],
              "properties": {"doc": {"type": "string"}, "title": {"type": "string"}, "text": {"type": "string"},
                             "why": {"type": "string"}}}


def _skill_docs(slug: str) -> list[str]:
    from src.agents.skills import skills_dir

    folder = skills_dir() / slug
    return ["SKILL"] + sorted(p.stem for p in folder.glob("*.md") if p.stem != "SKILL")


def _doc_lines(slug: str, docs: list[str]) -> str:
    """Each document with the one line saying what it governs (its front matter title), so the agent can tell the
    planner's document (how a question becomes filters) from the analyst's (how an answer is written)."""
    from src.agents import skills

    out = []
    for name in docs:
        title = ""
        try:
            if name == "SKILL":
                skill = next((s for s in skills.load_skills() if s.slug == slug), None)
                title = (skill.description if skill else "") or "the skill's main document"
            else:
                meta, _body = skills._parse_front_matter((skills.skills_dir() / slug / f"{name}.md").read_text(encoding="utf-8"))
                title = str((meta or {}).get("title") or (meta or {}).get("description") or "")
        except Exception:  # noqa: BLE001 - a title is a help to the agent, never a reason to fail
            title = ""
        out.append(f"- {name}: {re.sub(r'[ \t\r\n]+', ' ', title).strip()[:200]}" if title else f"- {name}")
    return "\n".join(out)


def _current_text(slug: str, doc_name: str) -> str:
    from src.agents import skills

    if doc_name == "SKILL":
        skill = next((s for s in skills.load_skills() if s.slug == slug), None)
        return skills.skill_body(skill) if skill else ""
    return skills.prompt_doc(slug, doc_name)


async def _base_text(slug: str, doc_name: str) -> str:
    """The text a drafted change is added to: the newest version of the document - under test, else live, else the
    shipped file (5 Nov 2026). This process never answers questions, so its copy of the live texts is not refreshed on
    its own; read the store first, or a draft built on the repo file would drop every change already live."""
    from src.services import skill_overlays

    try:
        await skill_overlays.refresh(force=True)
        testing = await skill_overlays.testing_texts()
    except Exception as exc:  # noqa: BLE001 - without the store the draft cannot be built safely
        raise Invalid("store_unavailable", "The instruction store could not be read, so the current text is unknown. "
                                           "Try again.") from exc
    under_test = testing.get((slug, doc_name))
    return under_test if under_test and under_test.strip() else _current_text(slug, doc_name)


def _skill_for(questions: list[str]) -> str:
    """The skill most of the failing questions are routed to, by deepagents' own router."""
    from src.agents import skills

    slugs = Counter()
    for q in questions:
        try:
            agent = (skills.route(q) or {}).get("primary_agent")
        except Exception:  # noqa: BLE001
            agent = None
        skill = skills.skill_for_agent(agent) if agent else None
        if skill:
            slugs[skill.slug] += 1
    return slugs.most_common(1)[0][0] if slugs else "compliance"


async def draft_fix(m: labelling.Member, lineage_id: str, *, authorization: str | None) -> dict[str, Any]:
    """Propose the change to one instruction document that makes answers follow the mode's approved rule."""
    runs.require_taxonomist(m)
    lin = loop._uuid(lineage_id)
    async with db.lab_engine().connect() as conn:
        modes = {md["lineage_id"]: md for md in await loop.live_modes(conn)}
        if lin not in modes:
            raise NotFound("mode_not_found", "That failure mode is not confirmed in Trace Lab.")
        spec = await loop.approved_spec(conn)
        rule = next((r for r in (spec or {}).get("rules") or [] if r.get("mode_lineage_id") == lin), None)
        if rule is None:
            raise Invalid("not_in_spec", "Approve a specification version with a rule for this failure mode first.")
        qids = (await loop._positives(conn, lin)).get(lin, [])
        rows = [dict(r) for r in (await conn.execute(text(
            "SELECT q.id::text AS id, 'R' || r.run_no || '-' || q.seq_no AS code, q.question FROM tracelab.lab_questions q "
            "JOIN tracelab.lab_runs r ON r.id = q.run_id WHERE q.id = ANY(CAST(:ids AS uuid[])) ORDER BY q.created_at DESC "
            "LIMIT 8"), {"ids": qids})).mappings().all()]
        notes = [dict(r) for r in (await conn.execute(text(
            "SELECT t.question_id::text AS qid, s.note FROM tracelab.step_labels s JOIN tracelab.lab_question_traces t "
            "ON t.turn_id = s.turn_id WHERE t.question_id = ANY(CAST(:ids AS uuid[])) AND s.superseded_by IS NULL "
            "AND NOT s.tombstone AND s.note IS NOT NULL LIMIT 20"), {"ids": [r["id"] for r in rows]})).mappings().all()]
        turns = {r["id"]: (await loop.question_turns(conn, r["id"]))[-1:] for r in rows}
    if not rows:
        raise Invalid("no_positives", "Nobody has marked a question positive for this failure mode.")
    answers = await loop.turn_answers([t for ts in turns.values() for t in ts])
    slug = _skill_for([r["question"] for r in rows])
    docs = _skill_docs(slug)
    prompt = (f"RULE {rule['code']}: {rule.get('text')}\nFAILURE MODE {modes[lin]['name']}: {modes[lin].get('definition') or ''}\n\n"
              f"DOCUMENTS of skill '{slug}' (choose one by name):\n{_doc_lines(slug, docs)}\n\nNOTES\n"
              + "\n".join(f"- {n['note'][:500]}" for n in notes)
              + "\n\nFAILING ANSWERS\n"
              + "\n".join(f"- {r['code']} | {r['question'][:200]} | "
                          f"{((answers.get((turns[r['id']] or [''])[0]) or {}).get('answer') or '')[:700]}" for r in rows))
    reply = await _ask(FIX_SYSTEM, prompt, FIX_SCHEMA, max_tokens=1500)
    doc_name = str(reply.get("doc") or "").strip()
    if doc_name not in docs:
        doc_name = "answering" if "answering" in docs else docs[0]
    title = re.sub(r"\s+", " ", str(reply.get("title") or modes[lin]["name"]))[:120]
    section = f"## {title} ({rule['code']})\n\n" + str(reply.get("text") or "").strip()[:SECTION_MAX]
    content = (await _base_text(slug, doc_name)).rstrip() + "\n\n" + section + "\n"
    out = await _deepagents("POST", "/overlays", authorization, {
        "skill": slug, "doc": doc_name, "content": content,
        "evidence": {"trace_lab": {"mode": lin, "mode_name": modes[lin]["name"], "spec_rule": rule["code"],
                                   "questions": [r["code"] for r in rows], "section": section,
                                   "why": str(reply.get("why") or "")[:1000], "by": m.email}}})
    return {"id": out.get("id"), "skill": slug, "doc": doc_name, "path": f"skills/{slug}/{doc_name}.md", "section": section,
            "why": reply.get("why")}


# ── 3. the approved spec, the changes under test, live, and the export ─────────────────────────

async def publish_spec(version: dict[str, Any], *, authorization: str | None) -> dict[str, Any]:
    """After a person approves a SPEC.md version, the agent writes it to the one place instructions live, under test.
    It becomes what every agent reads only when a person makes it live."""
    out = await _deepagents("POST", "/overlays", authorization, {
        "skill": "spec", "doc": "SPEC", "content": version["body"],
        "evidence": {"trace_lab": {"spec_version": version.get("version"), "spec_id": version.get("id")}}})
    return await _deepagents("POST", f"/overlays/{out['id']}/test", authorization)


def _change_out(o: dict[str, Any]) -> dict[str, Any]:
    ev = o.get("evidence") or {}
    if isinstance(ev, str):
        try:
            ev = json.loads(ev)
        except ValueError:
            ev = {}
    tl = ev.get("trace_lab") or {}
    return {"id": o["id"], "skill": o["skill"], "doc": o["doc"], "status": o["status"],
            "path": f"skills/{o['skill']}/{o['doc']}.md", "mode": tl.get("mode"), "mode_name": tl.get("mode_name"),
            "spec_rule": tl.get("spec_rule"), "spec_version": tl.get("spec_version"), "section": tl.get("section"),
            "why": tl.get("why"), "questions": tl.get("questions") or [], "by": o.get("created_by"),
            "at": o.get("created_at"), "decided_by": o.get("reviewed_by"), "decided_at": o.get("reviewed_at")}


async def changes(m: labelling.Member, *, authorization: str | None) -> dict[str, Any]:
    """The instruction changes this loop made: waiting for a decision, under test, and live."""
    runs.require_taxonomist(m)
    rows = (await _deepagents("GET", "/overlays", authorization)).get("overlays") or []
    mine = [_change_out(o) for o in rows if o.get("skill") == "spec" or "trace_lab" in json.dumps(o.get("evidence") or {})]
    return {"proposed": [c for c in mine if c["status"] == "proposed"],
            "testing": [c for c in mine if c["status"] == "testing"],
            "live": [c for c in mine if c["status"] == "active"]}


async def approve_change(m: labelling.Member, overlay_id: str, *, authorization: str | None) -> dict[str, Any]:
    """A person approves an agent's change: it goes under test (only Trace Lab's reruns read it) and is recorded as
    the correction for its failure mode."""
    runs.require_taxonomist(m)
    found = {c["id"]: c for c in (await changes(m, authorization=authorization))["proposed"]}
    if overlay_id not in found:
        raise NotFound("not_found", "That change is not waiting for a decision.")
    out = await _deepagents("POST", f"/overlays/{overlay_id}/test", authorization)
    c = found[overlay_id]
    if c.get("mode"):
        async with db.lab_engine().begin() as conn:
            await _limits(conn)
            await conn.execute(text(
                "INSERT INTO tracelab.lab_corrections (mode_lineage_id, spec_rule, kind, reference, description, source, "
                "created_by, created_by_email) VALUES (CAST(:m AS uuid), :rule, 'skill', :ref, :d, 'coding_agent', "
                "CAST(:by AS uuid), :email)"),
                {"m": c["mode"], "rule": c.get("spec_rule"), "ref": f"{c['path']} · change {overlay_id}"[:500],
                 "d": (c.get("why") or "")[:2000] or None, "by": m.user_id, "email": m.email})
    return {**out, "path": c["path"]}


async def reject_change(m: labelling.Member, overlay_id: str, *, authorization: str | None) -> dict[str, Any]:
    runs.require_taxonomist(m)
    return await _deepagents("POST", f"/overlays/{overlay_id}/drop", authorization)


async def make_live(m: labelling.Member, overlay_id: str, *, authorization: str | None) -> dict[str, Any]:
    """A person confirms a change that passed its tests: every agent reads it from now on, with no rebuild."""
    runs.require_taxonomist(m)
    return await _deepagents("POST", f"/overlays/{overlay_id}/live", authorization)


async def export_zip(m: labelling.Member, *, authorization: str | None) -> bytes:
    """The live instruction documents at their repo paths, for the commit once testing is complete."""
    runs.require_taxonomist(m)
    files = (await _deepagents("GET", "/export", authorization)).get("files") or []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.writestr("svc-deepagents/" + f["path"], f["content"])
        z.writestr("README.txt", "The live instruction documents from Hoist Trace Lab, at their paths under "
                                 "apps/backend/cafm-connector-service-final/. Copy them into the repo and commit.\n")
    return buf.getvalue()


async def under_test() -> dict[tuple[str, str], str]:
    """The text under test, for a rerun (read-only; deepagents' own module reads its own table)."""
    from src.services import skill_overlays

    try:
        await skill_overlays.refresh(force=True)
        return await skill_overlays.testing_texts()
    except Exception as exc:  # noqa: BLE001 - a rerun still runs, on the live text
        log.warning("agent.under_test_unreadable", error=str(exc)[:200])
        return {}
