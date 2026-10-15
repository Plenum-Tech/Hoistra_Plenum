"""The skill lab: instruction changes are kept only when replayed questions say they help.

Three parts, built on Hoist Traces:

**Test set** (``test_set``) - real questions this company asked, from agent_trace_turns: chat
turns that answered, deduplicated, with anything that asks for an action left out (a replay must
only read). Each question lands in the TRAIN or HELD-OUT split by a hash of its text, so a
question never moves between them and a proposal is never scored on what it was written from.

**Measurement** (``compare``) - each question is replayed in-process through the real chat path
(``run_stateful``) under ``replay_guard.REPLAY``: same routing, tools and reads, nothing written,
nothing billed, no write request leaves the process. The REFERENCE is the same question answered
with the old behaviour (oldest-first trim, no compaction). A CANDIDATE is answered with
self-managed context and, when given, a candidate text for one skill document
(skill_overlays.CANDIDATE). A judge model scores the candidate against the reference: every fact
in the reference the candidate leaves out or contradicts costs score. Tokens, cost, the largest
prompt and the compaction counters come from the replay's own ledger. With almost no rated turns
on record (1 thumbs-up in 130 turns on 6 Oct 2026), "as complete as the uncompacted answer, for
fewer tokens" is the measure that does not need labels.

**Optimisation** (``optimise``) - the current text of a document is measured on the TRAIN split;
a proposer model reads where it lost facts or failed to compact and rewrites the document; both
texts are then measured on the HELD-OUT split. A rewrite that is at least as complete and cheaper,
or clearly more complete, is stored as a PROPOSAL (skill_overlays) for an admin to approve. Nothing
reaches the agents until then.

Only an admin starts a run; one run at a time per process. Replays call real models: a run of
6 + 6 questions makes about 30 replays (roughly $2-4 at the measured $0.09 per question).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from typing import Any

import structlog
from sqlalchemy import text

from .. import database
from ..config import settings
from ..services import skill_overlays
from . import context_budget, llm_cost, replay_guard

log = structlog.get_logger(__name__)

RUNS = "plenum_cafm.skill_lab_runs"
RESULTS = "plenum_cafm.skill_lab_results"

_DDL = [
    f"""
    CREATE TABLE IF NOT EXISTS {RUNS} (
        id               UUID PRIMARY KEY,
        kind             TEXT NOT NULL,
        status           TEXT NOT NULL,
        organization_id  UUID,
        skill            TEXT,
        doc              TEXT,
        sample           INTEGER,
        started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at      TIMESTAMPTZ,
        summary          JSONB,
        proposal_id      UUID,
        error            TEXT,
        created_by       TEXT
    )""",
    f"""
    CREATE TABLE IF NOT EXISTS {RESULTS} (
        id                  UUID PRIMARY KEY,
        run_id              UUID NOT NULL,
        idx                 INTEGER NOT NULL,
        split               TEXT,
        variant             TEXT NOT NULL,
        question            TEXT,
        answer              TEXT,
        score               NUMERIC(5,3),
        judge               JSONB,
        usd                 NUMERIC(12,6),
        input_tokens        INTEGER,
        peak_prompt_tokens  INTEGER,
        llm_calls           INTEGER,
        context             JSONB,
        ms                  INTEGER,
        error               TEXT,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    f"CREATE INDEX IF NOT EXISTS ix_skill_lab_results_run ON {RESULTS} (run_id, idx)",
]

#: The documents the lab can tune. Each must be read at call time (see skill_overlays).
TUNABLE = {("query-builder", "context-budget")}

#: A question that asks for an action is never replayed, even though writes are refused anyway.
WRITE_INTENT = re.compile(
    r"\b(raise|create|send|email|e-mail|approve|reject|delete|remove|schedule|assign|close|cancel|"
    r"book|order|upload|ingest|migrate|update|change|set up|remind|forget|remember|teach|connect)\b", re.I)

MAX_SAMPLE = 10
#: A rewrite this much less complete than the current text is never proposed, however cheap.
COMPLETENESS_SLACK = 0.02
#: A rewrite this much more complete is proposed even if it costs more.
CLEAR_WIN = 0.03

_lock = asyncio.Lock()
_ready = False


async def ensure_tables() -> bool:
    global _ready
    if _ready:
        return True
    try:
        async with database.AsyncSessionLocal() as s:
            for ddl in _DDL:
                await s.execute(text(ddl))
            await s.commit()
        _ready = True
    except Exception as exc:  # noqa: BLE001
        log.warning("skill_lab.ddl_failed", error=str(exc)[:200])
    return _ready


def is_running() -> bool:
    return _lock.locked()


# ── the test set ─────────────────────────────────────────────────────────────────────────

def split_of(question: str) -> str:
    """TRAIN or HELD-OUT, fixed by the question's text: a third of questions are held out."""
    h = int(hashlib.sha1(" ".join(question.lower().split()).encode()).hexdigest(), 16)
    return "held_out" if h % 3 == 0 else "train"


def usable(question: str) -> bool:
    q = " ".join((question or "").split())
    return 12 <= len(q) <= 400 and not WRITE_INTENT.search(q)


async def test_set(organization_id: str, *, limit: int = 40) -> list[dict[str, Any]]:
    """Distinct questions this company asked in chat that were answered, largest context first:
    those are the ones where managing the context matters."""
    async with database.AsyncSessionLocal() as s:
        rows = (await s.execute(text("""
            SELECT DISTINCT ON (lower(trim(question))) question, answer, route, input_tokens,
                   feedback_rating, feedback_comment, started_at
              FROM plenum_cafm.agent_trace_turns
             WHERE organization_id = CAST(:o AS uuid) AND ok AND answer IS NOT NULL
               AND coalesce(session_id, '') NOT LIKE 'report-%'
               AND coalesce(session_id, '') NOT LIKE 'rerun%'
               AND coalesce(session_id, '') NOT LIKE 'skilllab-%'
               AND coalesce(route, '') <> 'single:clarify'
             ORDER BY lower(trim(question)), started_at DESC"""), {"o": str(organization_id)})).mappings().all()
    out = [dict(r, split=split_of(r["question"])) for r in rows if usable(r["question"])]
    out.sort(key=lambda r: -(r.get("input_tokens") or 0))
    return out[:limit]


# ── one replay ───────────────────────────────────────────────────────────────────────────

async def replay(orchestrator, question: str, *, run_id: str, idx: int, variant: str, mode: str,
                 candidate: dict[tuple[str, str], str] | None, principal, organization_id: str | None,
                 authorization: str | None) -> dict[str, Any]:
    """Answer one question for real, measure it, write nothing."""
    from ..http_client import caller_authorization, caller_organization_id
    from ..services.principal import caller_principal

    sid = f"skilllab-{run_id[:8]}-{idx}-{variant}-{uuid.uuid4().hex[:6]}"
    stats = context_budget.new_stats()
    tokens = [
        (replay_guard.REPLAY, replay_guard.REPLAY.set(True)),
        (context_budget.MODE, context_budget.MODE.set(mode)),
        (context_budget.STATS, context_budget.STATS.set(stats)),
        (skill_overlays.CANDIDATE, skill_overlays.CANDIDATE.set(candidate)),
        (caller_principal, caller_principal.set(principal)),
        (caller_organization_id, caller_organization_id.set(organization_id)),
        (caller_authorization, caller_authorization.set(authorization)),
    ]
    ledger = llm_cost.begin_turn(sid)
    t0 = time.perf_counter()
    answer, error = "", None
    try:
        result = await orchestrator.run_stateful(user_message=question, session_id=sid)
        answer = str((result or {}).get("answer") or "")
        if result and result.get("error"):
            error = str(result["error"])[:300]
    except Exception as exc:  # noqa: BLE001 - a failed replay is a result, not a failed run
        error = f"{type(exc).__name__}: {str(exc)[:280]}"
    finally:
        for var, tok in reversed(tokens):
            var.reset(tok)
    s = ledger.summary()
    peak = max((int(e.get("input_tokens") or 0) + int(e.get("cache_read") or 0)
                for e in ledger.entries), default=0)
    return {"variant": variant, "answer": answer, "error": error, "usd": s["usd"],
            "input_tokens": s["input_tokens"] + s["cache_read"], "peak_prompt_tokens": peak,
            "llm_calls": s["calls"], "context": stats, "ms": int((time.perf_counter() - t0) * 1000)}


# ── the judge ────────────────────────────────────────────────────────────────────────────

JUDGE_PROMPT = """You compare two answers to the same question about a company's buildings. The REFERENCE was
written with every tool result in view. The CANDIDATE was written by an assistant that compacted some
results into notes. Decide whether the candidate lost or changed anything the reference stated.

- List each specific fact in the reference (a figure, date, code, name, status, count or amount) that the
  candidate leaves out: "missing". Wording, order and layout do not matter.
- List each fact the candidate states differently from the reference: "contradicted".
- Facts the candidate adds are not penalised unless they contradict the reference.
- score: 1.0 when nothing is missing or contradicted; subtract in proportion to how much of the
  reference's substance is lost; a contradiction costs at least as much as an omission. 0.0 when the
  candidate failed to answer.

QUESTION
{question}

REFERENCE
{reference}

CANDIDATE
{candidate}"""

_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": "number"}, "missing": {"type": "array", "items": {"type": "string"}},
                   "contradicted": {"type": "array", "items": {"type": "string"}}, "note": {"type": "string"}},
    "required": ["score", "missing", "contradicted", "note"],
    "additionalProperties": False,
}


def _model(env_name: str) -> str:
    import os
    return (os.getenv(env_name) or "").strip() or (getattr(settings, "compliance_summary_model", "") or
                                                    "claude-sonnet-5")


async def _ask_json(system: str, prompt: str, schema: dict, *, role: str, env_model: str,
                    max_tokens: int = 1500) -> dict[str, Any]:
    import anthropic

    model = _model(env_model)
    client = anthropic.AsyncAnthropic(api_key=(getattr(settings, "anthropic_api_key", "") or "").strip())
    t0 = time.perf_counter()
    msg = await client.messages.create(
        model=model, max_tokens=max_tokens, system=system,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}])
    llm_cost.record(role, model, llm_cost.usage_from_anthropic(msg.usage), (time.perf_counter() - t0) * 1000)
    raw = next((b.text for b in (msg.content or []) if getattr(b, "type", "") == "text"), "{}")
    return json.loads(raw)


async def judge(question: str, reference: str, candidate: str) -> dict[str, Any]:
    if not (candidate or "").strip():
        return {"score": 0.0, "missing": ["no answer"], "contradicted": [], "note": "candidate empty"}
    if not (reference or "").strip():
        return {"score": None, "missing": [], "contradicted": [], "note": "reference failed; not scored"}
    try:
        out = await _ask_json("You grade answers strictly and say exactly what was lost.",
                              JUDGE_PROMPT.format(question=question, reference=reference[:12000],
                                                  candidate=candidate[:12000]),
                              _JUDGE_SCHEMA, role="skill_lab_judge", env_model="SKILL_LAB_JUDGE_MODEL")
        out["score"] = max(0.0, min(1.0, float(out.get("score") or 0.0)))
        return out
    except Exception as exc:  # noqa: BLE001
        return {"score": None, "missing": [], "contradicted": [], "note": f"judge failed: {str(exc)[:160]}"}


# ── aggregation (pure) ───────────────────────────────────────────────────────────────────

def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def summarise(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    """Averages for one variant over the questions where it and the reference both answered."""
    mine = [r for r in rows if r["variant"] == variant and not r.get("error")]
    ctx = [r.get("context") or {} for r in mine]
    return {
        "variant": variant, "questions": len(mine),
        "score": _mean([float(r["score"]) for r in mine if r.get("score") is not None]),
        "usd": _mean([float(r["usd"] or 0) for r in mine]),
        "input_tokens": _mean([float(r["input_tokens"] or 0) for r in mine]),
        "peak_prompt_tokens": _mean([float(r["peak_prompt_tokens"] or 0) for r in mine]),
        "compactions": sum(int(c.get("compactions") or 0) for c in ctx),
        "refused": sum(int(c.get("refused") or 0) for c in ctx),
        "cut_to_fit": sum(int(c.get("shrunk") or 0) for c in ctx),
        "errors": sum(1 for r in rows if r["variant"] == variant and r.get("error")),
    }


def better(current: dict[str, Any], proposed: dict[str, Any]) -> tuple[bool, str]:
    """The rule a rewrite must pass to be proposed. Pure."""
    cs, ps = current.get("score"), proposed.get("score")
    if cs is None or ps is None or not proposed.get("questions"):
        return False, "not enough scored answers to compare"
    cu, pu = current.get("input_tokens") or 0, proposed.get("input_tokens") or 0
    if ps >= cs + CLEAR_WIN:
        return True, f"more complete ({ps:.2f} vs {cs:.2f})"
    if ps >= cs - COMPLETENESS_SLACK and pu < cu:
        return True, (f"as complete ({ps:.2f} vs {cs:.2f}) on {round((1 - pu / cu) * 100) if cu else 0}% "
                      "fewer tokens")
    return False, f"not better: completeness {ps:.2f} vs {cs:.2f}, tokens {pu:.0f} vs {cu:.0f}"


# ── storage ──────────────────────────────────────────────────────────────────────────────

async def _store_result(run_id: str, idx: int, split: str, question: str, r: dict[str, Any]) -> None:
    async with database.AsyncSessionLocal() as s:
        await s.execute(text(f"""
            INSERT INTO {RESULTS} (id, run_id, idx, split, variant, question, answer, score, judge, usd,
                                   input_tokens, peak_prompt_tokens, llm_calls, context, ms, error)
            VALUES (CAST(:id AS uuid), CAST(:run AS uuid), :idx, :split, :variant, :q, :a, :score,
                    CAST(:judge AS jsonb), :usd, :it, :peak, :calls, CAST(:ctx AS jsonb), :ms, :err)"""),
            {"id": str(uuid.uuid4()), "run": run_id, "idx": idx, "split": split, "variant": r["variant"],
             "q": question, "a": r.get("answer"), "score": r.get("score"),
             "judge": json.dumps(r.get("judge") or {}), "usd": r.get("usd"), "it": r.get("input_tokens"),
             "peak": r.get("peak_prompt_tokens"), "calls": r.get("llm_calls"),
             "ctx": json.dumps(r.get("context") or {}), "ms": r.get("ms"), "err": r.get("error")})
        await s.commit()


async def _set_run(run_id: str, **fields: Any) -> None:
    sets, params = [], {"id": run_id}
    for k, v in fields.items():
        if k == "summary":
            sets.append("summary = CAST(:summary AS jsonb)")
            params["summary"] = json.dumps(v, default=str)
        elif k == "finished":
            sets.append("finished_at = now()")
        elif k == "proposal_id":
            sets.append("proposal_id = CAST(:proposal_id AS uuid)")
            params["proposal_id"] = v
        else:
            sets.append(f"{k} = :{k}")
            params[k] = v
    async with database.AsyncSessionLocal() as s:
        await s.execute(text(f"UPDATE {RUNS} SET {', '.join(sets)} WHERE id = CAST(:id AS uuid)"), params)
        await s.commit()


# ── measuring a set of questions ─────────────────────────────────────────────────────────

async def _measure(orchestrator, questions: list[dict[str, Any]], *, run_id: str, variants: dict[str, Any],
                   ctx: dict[str, Any], start_idx: int = 0) -> list[dict[str, Any]]:
    """Replay each question as the reference and as each variant; score the variants."""
    rows: list[dict[str, Any]] = []
    for i, q in enumerate(questions, start=start_idx):
        ref = await replay(orchestrator, q["question"], run_id=run_id, idx=i, variant="reference", mode="trim",
                           candidate=None, **ctx)
        ref["score"], ref["judge"] = (1.0 if ref["answer"] and not ref["error"] else None), {}
        rows.append(dict(ref, question=q["question"], split=q["split"]))
        await _store_result(run_id, i, q["split"], q["question"], ref)
        for name, candidate in variants.items():
            r = await replay(orchestrator, q["question"], run_id=run_id, idx=i, variant=name, mode="self",
                             candidate=candidate, **ctx)
            j = await judge(q["question"], ref["answer"] if not ref["error"] else "", r["answer"])
            r["score"], r["judge"] = j.get("score"), j
            rows.append(dict(r, question=q["question"], split=q["split"]))
            await _store_result(run_id, i, q["split"], q["question"], r)
    return rows


# ── the proposer ─────────────────────────────────────────────────────────────────────────

PROPOSE_PROMPT = """You maintain one instruction document that an AI agent reads while it answers questions about
a company's buildings. The document tells it how to keep its working context small by replacing tool
results it has finished with short notes (the compact_context tool), without losing anything the answer
needs. Below is the current text and what happened when it was measured on real questions: the facts
answers lost compared with an answer that kept every result in view, compactions that were refused and
why, and results cut automatically because the agent did not compact in time.

Rewrite the document so the agent loses fewer facts and keeps its context smaller. Keep what works.
Change only what the evidence points at. Write general rules - no company, building, vendor or figure
from the evidence. Keep it under 450 words, plain and direct. Return the full new text in "document"
(no front matter) and one or two sentences in "rationale".

CURRENT TEXT
{current}

EVIDENCE
{evidence}"""

_PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {"document": {"type": "string"}, "rationale": {"type": "string"}},
    "required": ["document", "rationale"], "additionalProperties": False,
}


def evidence_text(rows: list[dict[str, Any]], variant: str = "current", limit: int = 12) -> str:
    """What went wrong on the train split, in a form a proposer can act on. Pure."""
    lines = []
    for r in rows:
        if r["variant"] != variant:
            continue
        j = r.get("judge") or {}
        c = r.get("context") or {}
        bits = []
        if j.get("missing"):
            bits.append("lost: " + "; ".join(map(str, j["missing"][:4])))
        if j.get("contradicted"):
            bits.append("changed: " + "; ".join(map(str, j["contradicted"][:3])))
        if c.get("refused"):
            bits.append(f"{c['refused']} compaction(s) refused")
        if c.get("shrunk"):
            bits.append(f"{c['shrunk']} result(s) cut to fit because it did not compact in time")
        if not c.get("compactions") and (c.get("peak_working_tokens") or 0) > 12000:
            bits.append(f"never compacted though the context reached {c['peak_working_tokens']} tokens")
        if bits:
            lines.append(f"- score {r.get('score')}: " + " | ".join(bits))
        if len(lines) >= limit:
            break
    return "\n".join(lines) or "- no losses or failures recorded on this sample"


async def propose(current: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return await _ask_json(
        "You write precise operating instructions for AI agents.",
        PROPOSE_PROMPT.format(current=current, evidence=evidence_text(rows)),
        _PROPOSE_SCHEMA, role="skill_lab_proposer", env_model="SKILL_LAB_PROPOSER_MODEL", max_tokens=3000)


# ── runs ─────────────────────────────────────────────────────────────────────────────────

async def start(orchestrator, *, kind: str, sample: int, principal, organization_id: str | None,
                authorization: str | None, skill: str = "query-builder",
                doc: str = "context-budget") -> dict[str, Any]:
    """Create a run and start it in the background. One run at a time."""
    if kind not in ("compare", "optimise"):
        return {"ok": False, "error": "kind must be compare or optimise"}
    if (skill, doc) not in TUNABLE:
        return {"ok": False, "error": f"{skill}/{doc} is not a document the lab can tune"}
    if is_running():
        return {"ok": False, "error": "a skill-lab run is already in progress"}
    if not await ensure_tables():
        return {"ok": False, "error": "skill-lab tables unavailable"}
    org = str(organization_id or getattr(principal, "organization_id", "") or "")
    if not org:
        return {"ok": False, "error": "no company to draw questions from"}
    sample = max(1, min(int(sample or 6), MAX_SAMPLE))
    run_id = str(uuid.uuid4())
    async with database.AsyncSessionLocal() as s:
        await s.execute(text(f"""INSERT INTO {RUNS} (id, kind, status, organization_id, skill, doc, sample, created_by)
                                 VALUES (CAST(:id AS uuid), :kind, 'running', CAST(:o AS uuid), :sk, :dc, :n, :by)"""),
                        {"id": run_id, "kind": kind, "o": org, "sk": skill, "dc": doc, "n": sample,
                         "by": getattr(principal, "email", None)})
        await s.commit()
    ctx = {"principal": principal, "organization_id": org, "authorization": authorization}
    asyncio.get_running_loop().create_task(_run(orchestrator, run_id, kind, sample, org, skill, doc, ctx))
    return {"ok": True, "run_id": run_id, "kind": kind, "sample": sample}


async def _run(orchestrator, run_id: str, kind: str, sample: int, org: str, skill: str, doc: str,
               ctx: dict[str, Any]) -> None:
    async with _lock:
        try:
            await skill_overlays.refresh(force=True)
            questions = await test_set(org)
            train = [q for q in questions if q["split"] == "train"][:sample]
            held = [q for q in questions if q["split"] == "held_out"][:sample]
            current = skill_overlays.doc(skill, doc)
            if kind == "compare":
                qs = (held + train)[:sample]
                rows = await _measure(orchestrator, qs, run_id=run_id, variants={"current": None}, ctx=ctx)
                summary = {"questions": len(qs), "reference": summarise(rows, "reference"),
                           "current": summarise(rows, "current")}
                await _set_run(run_id, status="done", summary=summary, finished=True)
                return
            # optimise: learn on train, judge on held-out
            if not train or not held:
                await _set_run(run_id, status="failed", finished=True,
                               error=f"need questions in both splits (train {len(train)}, held-out {len(held)})")
                return
            train_rows = await _measure(orchestrator, train, run_id=run_id, variants={"current": None}, ctx=ctx)
            proposal = await propose(current, train_rows)
            new_text = str(proposal.get("document") or "").strip()
            if not new_text or new_text == current.strip():
                await _set_run(run_id, status="done", finished=True,
                               summary={"train": summarise(train_rows, "current"), "outcome": "no change proposed"})
                return
            held_rows = await _measure(orchestrator, held, run_id=run_id, ctx=ctx, start_idx=len(train),
                                       variants={"current": None, "proposed": {(skill, doc): new_text}})
            cur, prop = summarise(held_rows, "current"), summarise(held_rows, "proposed")
            ok, why = better(cur, prop)
            summary = {"train": summarise(train_rows, "current"), "held_out": {"reference": summarise(held_rows, "reference"),
                       "current": cur, "proposed": prop}, "verdict": why, "rationale": proposal.get("rationale")}
            pid = None
            if ok:
                pid = await skill_overlays.propose(skill=skill, doc_name=doc, content=new_text,
                                                   evidence={"verdict": why, "held_out": summary["held_out"],
                                                             "rationale": proposal.get("rationale")},
                                                   run_id=run_id, by="skill-lab")
            await _set_run(run_id, status="done", finished=True, summary=summary, **({"proposal_id": pid} if pid else {}))
        except Exception as exc:  # noqa: BLE001
            log.warning("skill_lab.run_failed", run_id=run_id, error=str(exc)[:300])
            try:
                await _set_run(run_id, status="failed", finished=True, error=f"{type(exc).__name__}: {str(exc)[:300]}")
            except Exception:  # noqa: BLE001
                pass


async def list_runs(organization_id: str | None, *, limit: int = 20) -> list[dict[str, Any]]:
    if not await ensure_tables():
        return []
    where, p = ("WHERE organization_id = CAST(:o AS uuid)", {"o": organization_id}) if organization_id else ("", {})
    async with database.AsyncSessionLocal() as s:
        rows = (await s.execute(text(f"""SELECT id::text, kind, status, skill, doc, sample, started_at, finished_at,
                                         summary, proposal_id::text, error, created_by FROM {RUNS} {where}
                                         ORDER BY started_at DESC LIMIT :lim"""), {**p, "lim": limit})).mappings().all()
    return [{**dict(r), "started_at": r["started_at"].isoformat() if r["started_at"] else None,
             "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None} for r in rows]


async def get_run(run_id: str, organization_id: str | None) -> dict[str, Any] | None:
    runs = [r for r in await list_runs(organization_id, limit=200) if r["id"] == run_id]
    if not runs:
        return None
    async with database.AsyncSessionLocal() as s:
        rows = (await s.execute(text(f"""SELECT idx, split, variant, question, left(answer, 4000) AS answer, score,
                                         judge, usd, input_tokens, peak_prompt_tokens, llm_calls, context, ms, error
                                         FROM {RESULTS} WHERE run_id = CAST(:id AS uuid) ORDER BY idx, variant"""),
                                    {"id": run_id})).mappings().all()
    return {**runs[0], "results": [{**dict(r), "score": float(r["score"]) if r["score"] is not None else None,
                                    "usd": float(r["usd"]) if r["usd"] is not None else None} for r in rows]}
