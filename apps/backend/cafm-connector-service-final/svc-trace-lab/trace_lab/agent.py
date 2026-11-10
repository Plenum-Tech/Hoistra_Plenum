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


#: The second draft (10 Nov 2026). R41-5's first draft was a generic paragraph appended after 14,000 characters of
#: rules such as "do not append the full list" and "at most four": replayed on the same data the analyst listed 0 of the
#: 5 at-risk work orders with it, and 5 of 5 (twice) with a rule scoped to the read's own fields that names what it
#: overrides. This pass is given the document as it reads now and the tools and fields the failing answers read.
REFINE_SYSTEM = ("You are given a section drafted for an instruction document of a facilities-management assistant, the "
                 "document's CURRENT TEXT, the approved rule, the failure, people's notes and the tools and data fields the "
                 "failing answers read. Rewrite the section so the agent follows it when it reads the whole document:\n"
                 "1. Scope: name the read (tool) and the field (e.g. a list returned as `at_risk`) the failure is about - "
                 "only the fields the failing answers left out or got wrong. Never ask to list a field that the read or the "
                 "document says to only count (e.g. a `clear` list).\n"
                 "2. Layout: the document says how an answer is laid out (groups or sections, KPIs, actions). Say exactly "
                 "where the items go in that layout - e.g. 'a group of its own, one line per item with its code, vendor "
                 "and reason' - and that a count, KPI or sentence never stands in for the items it counts.\n"
                 "3. Conflicts: quote every line of the current text that pulls the other way (a cap, a 'do not list', a "
                 "'count only') and say this rule takes precedence over it for this case only.\n"
                 "4. Only this rule: add nothing about any other failure, even if the notes mention one.\n"
                 "5. If a LAST TEST is given, the change before this one was tested and still failed: read what that "
                 "answer did and where each value was lost, and make the rule say exactly what that answer got wrong "
                 "- stronger and more specific, not the same wording again.\n"
                 "6. If a value was not in the data the answer read, the fix belongs where the question is turned into "
                 "a lookup (the planner or tool-selection document): make it map the names people use to the data's own "
                 "type code (e.g. a brand name to its certificate type code) and never substitute a different type.\n"
                 "Keep it short and plain, with no data values from the failing answers. Return the document name you "
                 "were given as `doc`, and a title without the rule code.")

_ENGINE_ALIASES = {"maintenance": "wo_engine"}


def _engines_of_route(route: str | None) -> list[str]:
    """The engines a recorded route names: 'single:compliance', 'multi:compliance,contract_performance'."""
    r = str(route or "")
    if ":" not in r:
        return []
    return [_ENGINE_ALIASES.get(e.strip(), e.strip()) for e in r.split(":", 1)[1].split(",") if e.strip()]


def _skill_from_meta(meta: dict[str, Any]) -> str | None:
    """The skill the failing answers were routed to - the agent that actually wrote them. The first engine of a route
    counts twice. None when nothing was recorded."""
    from src.agents import skills

    slugs: Counter = Counter()
    for route in meta.get("routes") or []:
        for i, engine in enumerate(_engines_of_route(route)):
            skill = skills.skill_for_agent(engine)
            if skill:
                slugs[skill.slug] += 2 if i == 0 else 1
    return slugs.most_common(1)[0][0] if slugs else None


_KEY = re.compile(r'\\?"([a-z][a-z0-9_]{1,40})\\?": [\[{]')


def _keys_of(raw: Any) -> list[str]:
    """The list- and object-valued fields of a read, in the order they appear (a record cut at 16,000 characters
    still names the ones before the cut)."""
    s = raw if isinstance(raw, str) else json.dumps(raw, default=str)
    return list(dict.fromkeys(_KEY.findall(s or "")))[:15]


def _data_lines(meta: dict[str, Any]) -> str:
    tools = meta.get("tools") or {}
    return "\n".join(f"- {name}: {', '.join(keys)}" if keys else f"- {name}" for name, keys in tools.items()) \
        or "(none recorded)"


async def _answer_meta(turn_ids: list[str]) -> dict[str, Any]:
    """The routes and the tool reads (name and fields) of the failing answers: Trace Lab's own records first, then
    the chat's, read-only. Missing records give less to go on, never an error."""
    ids = [t for t in dict.fromkeys(turn_ids) if t and not t.startswith("browser:")]
    routes: list[str] = []
    tools: dict[str, list[str]] = {}

    def add(name: Any, output: Any) -> None:
        if not name:
            return
        have = tools.setdefault(str(name), [])
        for k in _keys_of(output):
            if k not in have and len(have) < 15:
                have.append(k)

    if not ids:
        return {"routes": routes, "tools": tools}
    seen: set[str] = set()
    try:
        async with db.lab_engine().connect() as conn:
            for r in (await conn.execute(text(
                    "SELECT turn_id, turn->>'route' AS route FROM tracelab.evidence_turns WHERE turn_id = ANY(:ids)"),
                    {"ids": ids})).mappings().all():
                seen.add(r["turn_id"])
                if r["route"]:
                    routes.append(r["route"])
            for r in (await conn.execute(text(
                    "SELECT span->>'name' AS name, span->>'output' AS output FROM tracelab.evidence_spans "
                    "WHERE turn_id = ANY(:ids) AND span->>'kind' = 'tool' ORDER BY turn_id, seq"),
                    {"ids": ids})).mappings().all():
                add(r["name"], r["output"])
    except Exception as exc:  # noqa: BLE001 - a draft with less context, not a failure
        log.warning("agent.answer_meta_lab_failed", error=str(exc)[:200])
    rest = [t for t in ids if t not in seen]
    if rest:
        try:
            from . import conversations

            async with conversations._read() as conn:
                for r in (await conn.execute(text(
                        f"SELECT route FROM {conversations.TURNS} WHERE turn_id = ANY(:ids)"), {"ids": rest})).mappings().all():
                    if r["route"]:
                        routes.append(r["route"])
                for r in (await conn.execute(text(
                        f"SELECT name, output::text AS output FROM {conversations.SPANS} WHERE turn_id = ANY(:ids) "
                        "AND kind = 'tool' ORDER BY started_at"), {"ids": rest})).mappings().all():
                    add(r["name"], r["output"])
        except Exception as exc:  # noqa: BLE001
            log.warning("agent.answer_meta_chat_failed", error=str(exc)[:200])
    return {"routes": routes, "tools": tools}


_STAGE_TEXT = {
    "not_in_data": "not in the data it read: the lookup asked for the wrong thing, or no tool returns it",
    "not_stated": "a figure worked out from the data, not stated",
    "dropped_by_writer": "in the data, left out by the answer",
    "removed_in_review": "written, then removed by the review",
    "lost_at_the_end": "written, then lost before the final answer",
    "said_anyway": "still said",
}


def _last_test(cmp: dict[str, Any] | None) -> tuple[str, list[str]]:
    """What the mode's last test says for the next draft (10 Nov 2026): each question still failing, its answer and
    where each value was lost. The second part lists the values the data never had - only when nothing else is
    missing, because then no instruction change can pass."""
    lines: list[str] = []
    gaps: list[str] = []
    other = False
    for q in (cmp or {}).get("questions") or []:
        lost = q.get("lost") or []
        if q.get("verdict") not in ("still_failing", "regressed") or not lost:
            continue
        lines.append(f"- {q.get('question')}: the answer was:\n{str((q.get('after') or {}).get('answer') or '')[:1500]}")
        for x in lost:
            lines.append(f"  - {x.get('raw')}: {_STAGE_TEXT.get(x.get('stage'), x.get('stage'))}")
            if x.get("stage") == "not_in_data":
                gaps.append(str(x.get("raw")))
            else:
                other = True
    block = (f"LAST TEST {(cmp or {}).get('run') or ''} (still failing)\n" + "\n".join(lines)) if lines else ""
    return block, ([] if other else gaps)


def _should_refuse(gaps: list[str], *, tried: bool) -> bool:
    """A value the answer never read can be a wrong lookup (Gas Safe looked up as another type: a planner fix) or data
    no tool returns (a code change). Drafted for the first time; called a code change only when a fix was tried."""
    return bool(gaps) and tried


def _refuse_data_gap(gaps: list[str]) -> None:
    if gaps:
        raise Invalid("data_gap", "An instruction change was tried and the answer still never read "
                      + ", ".join(gaps[:6]) + ". If the records hold it, no tool returns it to the answer: that is a "
                        "code change. Record it as a correction once it is made, then test again.")


def _without_rule(doc: str, code: str) -> str:
    """The document without the earlier sections for this rule, so a redraft replaces them instead of stacking a
    second, older wording beside the new one."""
    pat = re.compile(r"\n*^## [^\n]*\(" + re.escape(code) + r"\)[ \t]*$.*?(?=^#{1,2} |\Z)", re.M | re.S)
    out = pat.sub("\n\n", doc)
    return doc if out == doc else re.sub(r"\n{3,}", "\n\n", out).rstrip() + "\n"


async def _mode_last_test(m: labelling.Member, lin: str) -> dict[str, Any] | None:
    """The newest regression run of the mode, compared, or None."""
    async with db.lab_engine().connect() as conn:
        row = (await conn.execute(text(
            "SELECT r.run_no FROM tracelab.lab_regression_runs g JOIN tracelab.lab_runs r ON r.id = g.run_id "
            "WHERE g.mode_lineage_id = CAST(:m AS uuid) ORDER BY g.created_at DESC LIMIT 1"), {"m": lin})).first()
    if row is None:
        return None
    return await loop.compare(m, runs.run_code(row[0]))


def _refine_prompt(*, rule: dict[str, Any], mode: dict[str, Any], notes: list[str], draft: dict[str, Any],
                   doc_text: str, data: str, last_test: str = "") -> str:
    return (f"RULE {rule.get('code')}: {rule.get('text')}\nFAILURE MODE {mode.get('name')}: {mode.get('definition') or ''}"
            "\n\nNOTES\n" + "\n".join(f"- {n[:500]}" for n in notes)
            + f"\n\nTOOLS AND FIELDS THE FAILING ANSWERS READ\n{data}"
            + (f"\n\n{last_test}" if last_test else "")
            + f"\n\nDRAFTED SECTION\nTitle: {draft.get('title') or ''}\n{draft.get('text') or ''}"
            + f"\n\nCURRENT TEXT OF THE DOCUMENT\n{doc_text[:30000]}")


def _primary_doc(chosen: str, docs: list[str]) -> str:
    """The document the rule is written into. A reviewer's document (review, scope-eval) is read only after the answer
    is written, so a rule the answer must follow goes to the writer's (answering) and the reviewer gets its clause
    beside it (R41-6, 10 Nov 2026: a count rule drafted into review.md never reached the step that writes counts)."""
    return "answering" if chosen in ("review", "scope-eval") and "answering" in docs else chosen


def _review_doc(docs: list[str]) -> str | None:
    """The document the skill's reviewer judges scope by: scope-eval, else review, else none."""
    return next((d for d in ("scope-eval", "review") if d in docs), None)


def _keep_section(code: str, rule_text: str) -> str:
    """The reviewer's half of a fix (10 Nov 2026): R45's review ordered the at-risk list S-13 requires removed as "a
    different, forward-looking question", and the revision dropped it."""
    return (f"## Keep what rule {code} requires ({code})\n\nThis rule takes precedence over anything above that "
            f"conflicts with it, for the case it covers.\n\nThe answering document requires this (rule {code}):\n\n"
            + str(rule_text or "").strip()[:SECTION_MAX]
            + f"\n\nWhen an answer contains what rule {code} requires, that IS what the question earns: do not list it as "
              "not asked for, do not ask for it to be removed or shortened, and if it is missing, ask for it.")


_LEADING_HEAD = re.compile(r"^\s*#{1,3} [^\n]*\n+")
_LEADING_PRECEDENCE = re.compile(r"^\s*This rule takes precedence over[^\n]*\n+", re.I)


def _section(code: str, title: str, body: str) -> str:
    text_ = str(body or "")
    # The model sometimes starts its text with its own heading and precedence line; one of each is enough.
    while True:
        cut = _LEADING_PRECEDENCE.sub("", _LEADING_HEAD.sub("", text_, count=1), count=1)
        if cut == text_:
            break
        text_ = cut
    return (f"## {title} ({code})\n\nThis rule takes precedence over anything above that conflicts with it, for the "
            f"case it covers.\n\n" + text_.strip()[:SECTION_MAX])


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
    turn_ids = [t for ts in turns.values() for t in ts]
    answers = await loop.turn_answers(turn_ids)
    # The skill of the agent that wrote the failing answers, else the router's guess from the questions (10 Nov 2026:
    # the guess filed R41-4's fix under udr while the compliance agent answered).
    meta = await _answer_meta(turn_ids)
    slug = _skill_from_meta(meta) or _skill_for([r["question"] for r in rows])
    # The mode's last test, if it has one (10 Nov 2026): what the answer did and where each value was lost. A value the
    # data never had is a code change, and no instruction draft is made for it.
    try:
        last_block, gaps = _last_test(await _mode_last_test(m, lin))
    except Exception as exc:  # noqa: BLE001 - a draft without the last test, not a failure
        log.warning("agent.last_test_unreadable", error=str(exc)[:200])
        last_block, gaps = "", []
    async with db.lab_engine().connect() as conn:
        tried = bool((await conn.execute(text(
            "SELECT 1 FROM tracelab.lab_corrections WHERE mode_lineage_id = CAST(:m AS uuid) AND kind = 'skill' LIMIT 1"),
            {"m": lin})).first())
    if _should_refuse(gaps, tried=tried):
        _refuse_data_gap(gaps)
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
    doc_name = _primary_doc(doc_name, docs)
    base = _without_rule(await _base_text(slug, doc_name), str(rule["code"]))
    # Drafted again with the document as it reads now and the reads the failing answers used (10 Nov 2026).
    try:
        refined = await _ask(REFINE_SYSTEM, _refine_prompt(
            rule=rule, mode=modes[lin], notes=[n["note"] for n in notes], draft=reply, doc_text=base,
            data=_data_lines(meta), last_test=last_block), FIX_SCHEMA, max_tokens=1500)
        if str(refined.get("text") or "").strip():
            reply = {**reply, **{k: refined[k] for k in ("title", "text", "why") if str(refined.get(k) or "").strip()}}
    except Exception as exc:  # noqa: BLE001 - the first draft still stands
        log.warning("agent.refine_failed", error=str(exc)[:200])
    title = re.sub(r"\s+", " ", str(reply.get("title") or modes[lin]["name"]))
    title = re.sub(r"\s*\((?:rule\s+)?" + re.escape(str(rule["code"])) + r"\s*\)", "", title, flags=re.I).strip()[:120] \
        or modes[lin]["name"]
    section = _section(rule["code"], title, str(reply.get("text") or ""))
    content = base.rstrip() + "\n\n" + section + "\n"
    out = await _deepagents("POST", "/overlays", authorization, {
        "skill": slug, "doc": doc_name, "content": content,
        "evidence": {"trace_lab": {"mode": lin, "mode_name": modes[lin]["name"], "spec_rule": rule["code"],
                                   "questions": [r["code"] for r in rows], "section": section,
                                   "why": str(reply.get("why") or "")[:1000], "by": m.email}}})
    companions = []
    review = _review_doc(docs)
    if out.get("id") and review and review != doc_name:
        # The reviewer's half: without it the review step removes what the rule makes the answer add (10 Nov 2026).
        keep = _keep_section(rule["code"], str(reply.get("text") or ""))
        extra = await _deepagents("POST", "/overlays", authorization, {
            "skill": slug, "doc": review,
            "content": _without_rule(await _base_text(slug, review), str(rule["code"])).rstrip() + "\n\n" + keep + "\n",
            "evidence": {"trace_lab": {"mode": lin, "mode_name": modes[lin]["name"], "spec_rule": rule["code"],
                                       "questions": [r["code"] for r in rows], "section": keep, "companion_of": out["id"],
                                       "why": "So the review step keeps what the rule requires.", "by": m.email}}})
        if extra.get("id"):
            companions.append({"id": extra["id"], "of": out["id"], "path": f"skills/{slug}/{review}.md"})
    # The new draft replaces this mode's earlier changes to the same document that are still waiting or under test
    # (10 Nov 2026): two wordings of one rule under test at once is two experiments in one rerun.
    if out.get("id"):
        try:
            seen = await changes(m, authorization=authorization)
            for c in seen["proposed"] + seen["testing"]:
                if c.get("mode") == lin and c["id"] != out["id"] and c.get("doc") == doc_name:
                    await reject_change(m, c["id"], authorization=authorization)
        except Exception as exc:  # noqa: BLE001 - the old change stays for a person to reject
            log.warning("agent.supersede_failed", error=str(exc)[:200])
    return {"id": out.get("id"), "skill": slug, "doc": doc_name, "path": f"skills/{slug}/{doc_name}.md", "section": section,
            "why": reply.get("why"), "companions": companions}


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
            "companion_of": tl.get("companion_of"),
            "at": o.get("created_at"), "decided_by": o.get("reviewed_by"), "decided_at": o.get("reviewed_at")}


async def changes(m: labelling.Member, *, authorization: str | None) -> dict[str, Any]:
    """The instruction changes this loop made: waiting for a decision, under test, and live."""
    runs.require_taxonomist(m)
    rows = (await _deepagents("GET", "/overlays", authorization)).get("overlays") or []
    every = [_change_out(o) for o in rows if o.get("skill") == "spec" or "trace_lab" in json.dumps(o.get("evidence") or {})]
    # A change's companions (the reviewer's clause) are shown with it and decided with it, not on their own.
    mine = [c for c in every if not c.get("companion_of")]
    for c in mine:
        c["companions"] = [{"id": x["id"], "path": x["path"], "status": x["status"]}
                           for x in every if x.get("companion_of") == c["id"]]
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
    for x in c.get("companions") or []:
        if x["status"] == "proposed":
            await _deepagents("POST", f"/overlays/{x['id']}/test", authorization)
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


async def _companions_of(m: labelling.Member, overlay_id: str, authorization: str | None) -> list[dict[str, Any]]:
    seen = await changes(m, authorization=authorization)
    for c in seen["proposed"] + seen["testing"] + seen["live"]:
        if c["id"] == overlay_id:
            return c.get("companions") or []
    return []


async def reject_change(m: labelling.Member, overlay_id: str, *, authorization: str | None) -> dict[str, Any]:
    runs.require_taxonomist(m)
    companions = await _companions_of(m, overlay_id, authorization)
    out = await _deepagents("POST", f"/overlays/{overlay_id}/drop", authorization)
    for x in companions:
        if x["status"] != "rejected":
            await _deepagents("POST", f"/overlays/{x['id']}/drop", authorization)
    return out


async def make_live(m: labelling.Member, overlay_id: str, *, authorization: str | None) -> dict[str, Any]:
    """A person confirms a change that passed its tests: every agent reads it from now on, with no rebuild."""
    runs.require_taxonomist(m)
    companions = await _companions_of(m, overlay_id, authorization)
    out = await _deepagents("POST", f"/overlays/{overlay_id}/live", authorization)
    for x in companions:
        if x["status"] == "testing":
            await _deepagents("POST", f"/overlays/{x['id']}/live", authorization)
    return out


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
