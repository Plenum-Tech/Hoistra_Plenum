"""The world the Trace Lab end-to-end story runs in (a plain helper module, like tl_scratch; tests only).

Nothing here is a Trace Lab mock. The story drives the REAL FastAPI app (trace_lab.app.create_app) booted by its
REAL lifespan (TRACE_LAB_SCHEMA=apply on the throwaway database applies every migration) over httpx.ASGITransport,
and only the things that would leave the machine are replaced:

* identity: svc-deepagents' own principal.resolve runs for real (Authorization header -> /api/auth/me ->
  Principal); only its HTTP call is answered from a table of four people (a company-A admin and her colleague,
  a company-B admin, the platform superadmin) and a plain user;
* the models: skill_lab._ask_json (grader, AI proposals) and judges.ASK (judges) answer from rules, and record
  their cost on the llm_cost ledger exactly as the real call does, so every dollar the caps and the spend page
  see is the real arithmetic over a fixed token count;
* the chat: golden_fakes.RecordedOrchestrator, the recorded orchestrator Trace Lab's own tests use, behind
  orchestrator_host.get_orchestrator (one wrapper makes a message do a real write attempt through the REAL
  allow-list proxy route).

The databases are the throwaway Postgres behind TRACE_LAB_TEST_DB_URL only (tl_scratch refuses anything else):
schema tracelab is Trace Lab's own database, schema plenum_cafm is the "application" database, which the code
under test reads through the read-only engine and which this module writes (through a separate writable
engine) only to seed live traffic.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx
import pytest
from sqlalchemy import text

from golden_fakes import Msg, RecordedOrchestrator
from golden_xlsx import make_xlsx
from src import database
from src.agents import llm_cost, skill_lab
from src.services import principal as principal_module
from tl_scratch import SCRATCH_URL, _create_main_tables
from trace_lab import app as app_module
from trace_lab import db, judges, migrate, orchestrator_host, proxy
from trace_lab.settings import Settings

API = "/api/trace-lab"
ORG_A = "11111111-1111-4111-8111-aaaaaaaaaaaa"
ORG_B = "22222222-2222-4222-8222-bbbbbbbbbbbb"
ORG_PLATFORM = "99999999-9999-4999-8999-999999999999"
MODEL = "claude-sonnet-5"                    # priced in llm_cost.PRICES: $2 per million in, $10 per million out
CANCELLED = "including cancelled jobs"       # the fault the story's failure mode is about, present in failing answers
SECRET_TOKEN = "sk-ant-api03-ZZZZZZZZZZZZZZZZZZZZZZZZ"
SECRET_BEARER = "abcdefghijklmnopqrstuvwx"
TRACE_SECRET = "sk-ant-api03-TTTTTTTTTTTTTTTTTTTTTTTT"      # a credential typed into a live question


# ── the people ────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Person:
    name: str
    token: str
    user_id: str
    email: str
    org: str | None
    role: str

    def me(self) -> dict[str, Any]:
        """What operations-intelligence GET /api/auth/me answers for this person."""
        return {"user": {"id": self.user_id, "email": self.email, "organization_id": self.org, "role": self.role,
                         "can_ingest": True, "building_ids": None, "selected_building_id": None}}


PEOPLE: dict[str, Person] = {p.name: p for p in (
    Person("admin_a1", "tok-admin-a1", "a1a1a1a1-0000-4000-8000-000000000001", "ada@alpha.test", ORG_A, "admin"),
    Person("admin_a2", "tok-admin-a2", "a2a2a2a2-0000-4000-8000-000000000002", "ben@alpha.test", ORG_A, "admin"),
    Person("admin_b", "tok-admin-b", "b1b1b1b1-0000-4000-8000-000000000003", "cleo@bravo.test", ORG_B, "admin"),
    Person("user_a", "tok-user-a", "a3a3a3a3-0000-4000-8000-000000000004", "dan@alpha.test", ORG_A, "user"),
    Person("platform", "tok-platform", "99999999-0000-4000-8000-000000000005", "owner@plenum.test", ORG_PLATFORM,
           "superadmin"),
)}
BY_TOKEN = {p.token: p for p in PEOPLE.values()}


async def fake_identity(method: str, base: str, path: str, *, service: str | None = None, timeout: Any = None,
                        headers: dict[str, str] | None = None, max_attempts: int | None = None, **_: Any) -> httpx.Response:
    """Stands in for http_client.request in the one place the story's code asks who someone is: GET /api/auth/me
    with the caller's Authorization header. An unknown token is a 401 from the identity service."""
    assert path == "/api/auth/me", f"the story answers only the identity lookup, not {path}"
    token = ((headers or {}).get("Authorization") or "").partition(" ")[2].strip()
    request = httpx.Request(method, "http://identity.test" + path)
    person = BY_TOKEN.get(token)
    if person is None:
        response = httpx.Response(401, json={"detail": {"error": "Sign in again.", "reason": "invalid_token"}},
                                  request=request)
        raise httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)
    return httpx.Response(200, json=person.me(), request=request)


# ── the models ────────────────────────────────────────────────────────────────────────────────

def _between(text_: str, start: str, end: str) -> str:
    i = text_.index(start) + len(start)
    return text_[i:text_.index(end, i)].strip()


class FakeModels:
    """Deterministic stand-ins for every model call Trace Lab makes. No network; each call costs 4,000 tokens in and
    300 out on the real ledger (0.011 dollars at the pinned model's real price)."""

    IN_TOKENS, OUT_TOKENS = 4000, 300
    UNIT_USD = 0.011

    def __init__(self) -> None:
        self.grade_rules: list[tuple[str, list[str]]] = []      # (question substring, words the answer must hold)
        self.grader_down: set[str] = set()                      # question substrings whose grading raises
        self.grader_calls: list[dict[str, Any]] = []
        self.cells_calls: list[dict[str, Any]] = []
        self.judge_calls: list[dict[str, Any]] = []
        self.judge_misses: set[str] = set()                     # a "ref-..." token whose verdict the judge gets wrong
        self.judge_usd = 0.004

    # skill_lab._ask_json: the grader and the AI proposals (the grader's own role names are Trace Lab's)
    async def ask_json(self, system: str, prompt: str, schema: dict, *, role: str, env_model: str,
                       max_tokens: int = 1500) -> dict[str, Any]:
        llm_cost.record(role, MODEL, {"input_tokens": self.IN_TOKENS, "output_tokens": self.OUT_TOKENS}, 5)
        if role == "tracelab_grader":
            return self._grade(prompt)
        if role == "trace_lab_cells":
            return self._cells(prompt, system)
        raise AssertionError(f"the story has no fake for model role {role!r}")

    def _grade(self, prompt: str) -> dict[str, Any]:
        question = _between(prompt, "TEST QUESTION (the last message the user sent)\n", "\n\nEARLIER CONVERSATION")
        answer = _between(prompt, "ASSISTANT'S FINAL ANSWER\n", "\n\nCARDS SHOWN WITH THE ANSWER")
        self.grader_calls.append({"question": question, "answer": answer, "prompt": prompt})
        if any(sub.lower() in question.lower() for sub in self.grader_down):
            raise RuntimeError("the grading model is down")
        for sub, words in self.grade_rules:
            if sub.lower() in question.lower():
                ok = all(w.lower() in answer.lower() for w in words)
                return {"critique": f"The answer {'holds' if ok else 'does not hold'} {', '.join(words)}.",
                        "mismatches": [] if ok else [f"expected {', '.join(words)}"], "verdict": "pass" if ok else "fail"}
        raise AssertionError(f"no grading rule for the question {question!r}")

    def _cells(self, prompt: str, system: str = "") -> dict[str, Any]:
        asked = re.match(r"Decide these modes: ([^\n]+)\n", prompt)
        assert asked, "the cells prompt no longer starts with 'Decide these modes: ...'"
        handles = [h.strip() for h in asked.group(1).split(",")]
        self.cells_calls.append({"handles": handles, "prompt": prompt, "system": system})
        turn = re.search(r"\(ref-([a-z]+\d+)\)", prompt)
        tag = turn.group(1) if turn else "x"
        labels = []
        for h in handles:
            if CANCELLED in prompt:
                labels.append({"mode": h, "decision": "present", "basis": "answer", "quote": CANCELLED,
                               "location": "answer", "evidence": f"Answer {tag} counts the cancelled jobs as open."})
            else:
                labels.append({"mode": h, "decision": "absent", "basis": "answer", "quote": "", "location": "",
                               "evidence": f"Checked answer {tag}: it leaves cancelled jobs out of the count."})
        return {"labels": labels}

    # judges.ASK(model, prompt_text, shown_text): a Fail when the answer carries the fault, unless told to miss it
    async def ask_judge(self, model: str, prompt_text: str, shown: str) -> judges.JudgeReply:
        token = re.search(r"ref-[a-z]+\d+", shown)
        ref = token.group(0) if token else ""
        self.judge_calls.append({"model": model, "ref": ref, "prompt": prompt_text, "shown": shown})
        fault = CANCELLED in shown
        if ref in self.judge_misses:
            fault = not fault
        return judges.JudgeReply(failure_present=1 if fault else 0,
                                 critique=("The answer counts cancelled jobs as open." if fault
                                           else "The answer leaves cancelled jobs out."),
                                 status="decided", usd=self.judge_usd, error=None)


# ── the chat ──────────────────────────────────────────────────────────────────────────────────

@dataclass
class StoryMsg(Msg):
    """One scripted message of the recorded chat; probe_write makes the chat try a write through the real proxy."""
    probe_write: bool = False


class StoryChat(RecordedOrchestrator):
    """The recorded orchestrator, answering by rule: the first rule whose text is in the user's message gives the
    scripted answer. A message with probe_write first does what a runaway tool would: a PATCH through Trace Lab's
    own allow-list proxy route, which must come back refused (and is logged against the running case)."""

    def __init__(self, app: Any) -> None:
        super().__init__([])
        self.app = app
        self.rules: list[tuple[str, Callable[[str], StoryMsg]]] = []
        self.proxy_refusals: list[dict[str, Any]] = []
        self.asked: list[str] = []

    def pick(self, message: str) -> StoryMsg:
        for sub, make in self.rules:
            if sub.lower() in message.lower():
                return make(message)
        raise AssertionError(f"the recorded chat has no answer for {message!r}")

    async def _write_through_the_proxy(self) -> None:
        transport = httpx.ASGITransport(app=self.app, client=("127.0.0.1", 40123))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8010") as c:
            r = await c.patch("/__proxy/udr/api/tables/work_orders/records/1007", json={"status": "Closed"},
                              headers={"Authorization": "Bearer proxy-probe"})
        self.proxy_refusals.append({"status": r.status_code, "body": r.json()})

    async def stream(self, user_message: str, session_id: str | None = None, extra_context: str | None = None):
        msg = self.pick(user_message)
        self.asked.append(user_message)
        if msg.probe_write:
            await self._write_through_the_proxy()
            msg = dataclasses.replace(msg, probe_write=False)
        self.script.append(msg)
        async for event in super().stream(user_message, session_id=session_id, extra_context=extra_context):
            yield event


# ── seeding live traffic into the throwaway "application" database ──────────────────────────

NOW = datetime.now(timezone.utc).replace(microsecond=0)

_TURN_SQL = text("""
    INSERT INTO plenum_cafm.agent_trace_turns
        (turn_id, session_id, organization_id, user_id, email, question, answer, route, started_at, ended_at,
         latency_ms, llm_calls, tool_calls, cost_usd, cost_complete, input_tokens, output_tokens, cache_read_tokens,
         models, tools, ok, error, created_at)
    VALUES (:tid, :sid, CAST(:org AS uuid), CAST(:uid AS uuid), :email, :q, :a, :route, :t0, :t1, 1500, 2, 1, :usd,
            true, 900, 60, 0, CAST('["claude-sonnet-5"]' AS jsonb), CAST('["answer_from_records"]' AS jsonb), :ok,
            NULL, :t1)""")
_SPAN_SQL = text("""
    INSERT INTO plenum_cafm.agent_trace_spans
        (id, turn_id, session_id, organization_id, parent_id, seq, kind, name, agent, model, started_at, ended_at,
         latency_ms, input, output, input_tokens, output_tokens, cache_read_tokens, cost_usd, ok, error)
    VALUES (CAST(:id AS uuid), :tid, NULL, CAST(:org AS uuid), CAST(:parent AS uuid), :seq, :kind, :name, :agent,
            :model, :t0, :t1, 400, CAST(:inp AS jsonb), CAST(:out AS jsonb), :tin, :tout, 0, :usd, true, NULL)""")
_THREAD_SQL = text("""
    INSERT INTO plenum_cafm.chat_threads (id, organization_id, user_id, title)
    VALUES (:id, CAST(:org AS uuid), CAST(:uid AS uuid), :title)""")


def _asker(org: str) -> str:
    return PEOPLE["admin_a1"].user_id if org == ORG_A else PEOPLE["admin_b"].user_id


def tree(tid: str, org: str, t0: datetime, question: str, answer: str, *, tool_in: Any = None,
         tool_out: Any = None) -> list[dict[str, Any]]:
    """A turn's span tree as the chat records it: turn root, the orchestrator loop, a model call that asks for a
    tool, the tool, and the model call that writes the answer (User -> Model -> Tool -> Model -> Answer)."""
    root, loop, m1, tool, m2 = (str(uuid.uuid4()) for _ in range(5))

    def span(sid, parent, seq, kind, name, **kw):
        return {"id": sid, "tid": tid, "org": org, "parent": parent, "seq": seq, "kind": kind, "name": name,
                "agent": kw.get("agent", "orchestrator"), "model": kw.get("model"),
                "t0": t0 + timedelta(seconds=seq), "t1": t0 + timedelta(seconds=seq + 0.5),
                "inp": json.dumps(kw.get("inp")) if kw.get("inp") is not None else None,
                "out": json.dumps(kw.get("out")) if kw.get("out") is not None else None,
                "tin": kw.get("tin"), "tout": kw.get("tout"), "usd": kw.get("usd")}

    return [
        span(root, None, 0, "turn", "turn", inp={"message": question}, out={"answer": answer}),
        span(loop, root, 1, "agent", "orchestrator loop", agent="orchestrator loop"),
        span(m1, loop, 2, "llm", "model", agent="orchestrator loop", model=MODEL,
             inp={"messages": 3, "last_human_message": question[:80], "tool_results_since_last_human": 0},
             out={"content": "", "tool_calls": [{"name": "answer_from_records", "args": {"q": question[:40]}}]},
             tin=900, tout=40, usd=0.004),
        span(tool, root, 3, "tool", "answer_from_records", agent="tool:wo_engine",
             inp=tool_in if tool_in is not None else {"q": question[:40]},
             out=tool_out if tool_out is not None else {"total": 12, "scope": "company"}),
        span(m2, loop, 4, "llm", "model", agent="orchestrator loop", model=MODEL,
             inp={"messages": 5, "last_human_message": question[:80], "tool_results_since_last_human": 1},
             out={"content": answer, "tool_calls": []}, tin=1100, tout=60, usd=0.005),
    ]


async def seed_turns(engine, rows: list[dict[str, Any]]) -> None:
    """rows: {tid, org, session, question, answer, at, [ok, usd, tool_in, tool_out]}. One transaction."""
    async with engine.begin() as conn:
        for r in rows:
            org = r["org"]
            await conn.execute(_TURN_SQL, {
                "tid": r["tid"], "sid": r["session"], "org": org, "uid": _asker(org), "email": f"asker@{org[:4]}.test",
                "q": r["question"], "a": r["answer"], "route": r.get("route", "single:wo_engine"), "t0": r["at"],
                "t1": r["at"] + timedelta(seconds=5), "usd": r.get("usd"), "ok": r.get("ok", True)})
            await conn.execute(_SPAN_SQL, tree(r["tid"], org, r["at"], r["question"], r["answer"],
                                               tool_in=r.get("tool_in"), tool_out=r.get("tool_out")))
        for r in rows:
            if r.get("title"):
                await conn.execute(_THREAD_SQL, {"id": r["session"], "org": r["org"], "uid": _asker(r["org"]),
                                                 "title": r["title"]})


# ── the xlsx of a golden set ──────────────────────────────────────────────────────────────────

GOLDEN_HEADER = ["ID", "Question", "Follow-ups", "Expected answer", "Evaluation", "Expected values", "Verified on"]


def golden_workbook(rows: list[list[Any]]) -> bytes:
    """An .xlsx (golden_xlsx: no spreadsheet library) with the header row and the rows given."""
    return make_xlsx([("Golden set", [GOLDEN_HEADER, *rows])])


def iso_days_ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()




# ── the world of live traffic the story seeds (stage 2) ─────────────────────────────────────────

N_REV = 36                         # company A's answers in the review pool: three batches of twelve


def ago(**kw: Any) -> datetime:
    return NOW - timedelta(**kw)


def _row(tid, org, session, question, answer, at, **kw):
    return {"tid": tid, "org": org, "session": session, "question": question, "answer": answer, "at": at, **kw}


def build_world() -> dict:
    """Every chat turn the story reads, by purpose. Faulty answers carry the fault the story's failure mode is about
    (counting cancelled jobs as open). Each purpose lives in its own time window so batches never meet."""
    w: dict = {"conv": {}, "review_a": [], "review_b": [], "judge": [], "monitor_a": [], "monitor_b": []}
    rows: list[dict] = []
    # live conversations, minutes old: company A (a multi-turn chat and a turn that carries secrets), company B
    q1, q2 = "Which boilers are at Harbour Point?", "How many work orders were raised there in August?"
    rows.append(_row("turn-ca001", ORG_A, "chat-a-boilers", q1, "Harbour Point has four boilers.", ago(minutes=9),
                     title="Boiler certificates"))
    rows.append(_row("turn-ca002", ORG_A, "chat-a-boilers", q2, "759 work orders were raised in August.", ago(minutes=7)))
    rows.append(_row("turn-ca003", ORG_A, "chat-a-secret", "Which key do we use for the vendor portal?",
                     f"Send the header Bearer {SECRET_BEARER} to reach it.", ago(minutes=5), title="Portal access",
                     tool_in={"api_key": SECRET_TOKEN, "headers": {"Authorization": f"Bearer {SECRET_BEARER}"}}))
    rows.append(_row("turn-cb001", ORG_B, "chat-b-lifts", "BRAVO which lifts are out of service?",
                     "BRAVO lift 3 is out of service.", ago(minutes=8), title="BRAVO lifts"))
    rows.append(_row("turn-cb002", ORG_B, "chat-b-lifts", "BRAVO when is the next lift inspection?",
                     "BRAVO the next inspection is in March.", ago(minutes=6)))
    w["conv"] = {"a_turns": ["turn-ca001", "turn-ca002"], "a_secret": "turn-ca003", "b_turns": ["turn-cb001", "turn-cb002"]}
    # the review pool: three days old; every third answer is faulty
    for i in range(N_REV):
        bad = i % 3 == 0
        tid = f"turn-ra{i:03d}"
        ans = f"There are {10 + i} open work orders at building R{i:02d}{', ' + CANCELLED if bad else ''} (ref-ra{i:03d})."
        extra = {}
        question = f"How many work orders are open at building R{i:02d}?"
        if i == 5:                                         # a credential typed into a question, and one in a tool input
            question += f" (my api_key is {TRACE_SECRET})"
            extra = {"tool_in": {"api_key": TRACE_SECRET, "q": "open work orders"}}
        rows.append(_row(tid, ORG_A, f"rev-a-{i:02d}", question, ans, ago(days=3, minutes=7 * i), **extra))
        w["review_a"].append({"tid": tid, "bad": bad})
    for i in range(6):
        tid = f"turn-rb{i:03d}"
        rows.append(_row(tid, ORG_B, f"rev-b-{i:02d}", f"BRAVO how many lifts at building R{i:02d}?",
                         f"BRAVO there are {2 + i} lifts at building R{i:02d} (ref-rb{i:03d}).", ago(days=3, minutes=11 * i)))
        w["review_b"].append(tid)
    # the labelled pool the judges learn from: six days old; 30 faulty and 30 sound answers, ten of them company B's
    for i in range(60):
        bad = i < 30
        org = ORG_B if i % 6 == 5 else ORG_A
        tid = f"turn-j{'b' if org == ORG_B else 'a'}{i:03d}"
        ans = f"Building J{i:02d} has {3 + i} assets overdue{', ' + CANCELLED if bad else ''} (ref-j{i:03d})."
        rows.append(_row(tid, org, f"jud-{i:02d}", f"Which assets at building J{i:02d} are overdue for service?", ans,
                         ago(days=6, minutes=5 * i)))
        w["judge"].append({"tid": tid, "org": org, "bad": bad})
    # the monitored window: 3 to 26 hours old; a quarter of company A's answers are faulty
    for i in range(40):
        bad = i % 4 == 0
        tid = f"turn-ma{i:03d}"
        ans = f"Contract K{i:02d} has an SLA of {4 + i} hours{', ' + CANCELLED if bad else ''} (ref-m{i:03d})."
        rows.append(_row(tid, ORG_A, f"mon-a-{i:02d}", f"What is the SLA for contract K{i:02d}?", ans,
                         ago(hours=3, minutes=34 * i)))
        w["monitor_a"].append({"tid": tid, "bad": bad})
    for i in range(8):
        tid = f"turn-mb{i:03d}"
        rows.append(_row(tid, ORG_B, f"mon-b-{i:02d}", f"BRAVO what is the SLA for contract K{i:02d}?",
                         f"BRAVO contract K{i:02d} has an SLA of {8 + i} hours (ref-n{i:03d}).", ago(hours=4, minutes=50 * i)))
        w["monitor_b"].append(tid)
    w["rows"] = rows
    return w


# ── the Golden Rule counters: what changed in the application database ──────────────────────

async def main_snapshot(engine, *, settle: bool = True) -> dict[str, dict[str, Any]]:
    """Per table of schema plenum_cafm: every row's key and md5, and Postgres' own tuple counters. The counters are
    read until two reads agree, because Postgres reports a session's counts when it goes idle or closes."""

    async def counters() -> dict[str, tuple[int, int, int]]:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT pg_stat_clear_snapshot()"))
            rows = (await conn.execute(text(
                "SELECT relname, n_tup_ins, n_tup_upd, n_tup_del FROM pg_stat_user_tables "
                "WHERE schemaname = 'plenum_cafm' ORDER BY relname"))).all()
        return {r[0]: (int(r[1]), int(r[2]), int(r[3])) for r in rows}

    last = await counters()
    for _ in range(8 if settle else 0):
        await asyncio.sleep(0.4)
        now = await counters()
        if now == last:
            break
        last = now
    out: dict[str, dict[str, Any]] = {}
    async with engine.connect() as conn:
        tables = (await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'plenum_cafm' ORDER BY tablename"))).scalars().all()
        for t in tables:
            pk = (await conn.execute(text(
                "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid "
                "AND a.attnum = ANY(i.indkey) WHERE i.indrelid = CAST(:r AS regclass) AND i.indisprimary "
                "ORDER BY a.attnum"), {"r": f"plenum_cafm.{t}"})).scalars().all()
            key = " || '|' || ".join(f'x."{c}"::text' for c in pk) if pk else "md5(x::text)"
            rows = (await conn.execute(text(f'SELECT {key}, md5(x::text) FROM plenum_cafm."{t}" x'))).all()
            out[t] = {"rows": {r[0]: r[1] for r in rows}, "count": len(rows),
                      "ins": last.get(t, (0, 0, 0))[0], "upd": last.get(t, (0, 0, 0))[1],
                      "del": last.get(t, (0, 0, 0))[2]}
    return out


async def main_objects(engine) -> dict[str, list[Any]]:
    """Every object of schema plenum_cafm that is not row data: relations, columns, triggers, functions. A story that
    changes none of them ran no DDL there."""
    async with engine.connect() as conn:
        rels = (await conn.execute(text(
            "SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'plenum_cafm' ORDER BY 1, 2"))).all()
        cols = (await conn.execute(text(
            "SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'plenum_cafm' ORDER BY 1, ordinal_position"))).all()
        trig = (await conn.execute(text(
            "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'plenum_cafm' AND NOT t.tgisinternal "
            "ORDER BY 1, 2"))).all()
        funcs = (await conn.execute(text(
            "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'plenum_cafm' ORDER BY 1"))).all()
    return {"relations": [tuple(r) for r in rels], "columns": [tuple(r) for r in cols],
            "triggers": [tuple(r) for r in trig], "functions": [tuple(r) for r in funcs]}


def diff_snapshots(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """What changed per table between two main_snapshot results; only tables that changed appear. Pure."""
    out: dict[str, dict[str, Any]] = {}
    for t in sorted(set(before) | set(after)):
        b, a = before.get(t) or {"rows": {}}, after.get(t) or {"rows": {}}
        added = sorted(set(a["rows"]) - set(b["rows"]))
        removed = sorted(set(b["rows"]) - set(a["rows"]))
        changed = sorted(k for k in set(a["rows"]) & set(b["rows"]) if a["rows"][k] != b["rows"][k])
        counters = {k: int(a.get(k, 0)) - int(b.get(k, 0)) for k in ("ins", "upd", "del")}
        if added or removed or changed or any(counters.values()):
            out[t] = {"added": added, "removed": removed, "changed": changed, **counters}
    return out


# ── the story ─────────────────────────────────────────────────────────────────────────────────

@dataclass
class Story:
    """Everything the stages share: the booted app, who is calling, the fakes, and what each stage made."""
    app: Any
    client: httpx.AsyncClient
    writable: Any                       # writable engine on schema plenum_cafm (the seeder; Trace Lab never gets it)
    models: FakeModels
    chat: StoryChat
    mp: pytest.MonkeyPatch
    lifespan: Any
    done: set[str] = field(default_factory=set)
    data: dict[str, Any] = field(default_factory=dict)

    async def call(self, who: str | None, method: str, path: str, *, org: str | None = None,
                   params: dict[str, Any] | None = None, **kw: Any) -> httpx.Response:
        headers = dict(kw.pop("headers", None) or {})
        if who is not None:
            headers["Authorization"] = "Bearer " + PEOPLE[who].token
        query = dict(params or {})
        if org:
            query["organization_id"] = org
        return await self.client.request(method, API + path, params=query or None, headers=headers, **kw)

    async def get(self, who, path, **kw):
        return await self.call(who, "GET", path, **kw)

    async def post(self, who, path, **kw):
        return await self.call(who, "POST", path, **kw)

    async def put(self, who, path, **kw):
        return await self.call(who, "PUT", path, **kw)

    async def patch(self, who, path, **kw):
        return await self.call(who, "PATCH", path, **kw)

    async def lab(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        """A read of Trace Lab's own database (schema tracelab), for what an API does not show."""
        async with db.lab_engine().connect() as conn:
            return [dict(r) for r in (await conn.execute(text(sql), params)).mappings().all()]

    async def main(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        """A read of the application database through the READ-ONLY engine Trace Lab itself uses."""
        async with db.main_engine().begin() as conn:
            return [dict(r) for r in (await conn.execute(text(sql), params)).mappings().all()]

    def need(self, stage: str) -> None:
        """A later stage stands on an earlier one: when that one did not finish, say so instead of failing oddly."""
        if stage not in self.done:
            pytest.skip(f"blocked: {stage} did not finish, so this stage has nothing to stand on")


def body(r: httpx.Response, status: int = 200) -> Any:
    """The JSON of a response that must have this status; the failure message carries the body."""
    assert r.status_code == status, f"{r.request.method} {r.request.url.path} -> {r.status_code}, wanted {status}: {r.text[:700]}"
    return r.json()


def reason(r: httpx.Response, status: int) -> str:
    """The house envelope's reason code of a refusal that must have this status."""
    assert r.status_code == status, f"{r.request.method} {r.request.url.path} -> {r.status_code}, wanted {status}: {r.text[:700]}"
    detail = r.json().get("detail")
    return (detail or {}).get("reason") if isinstance(detail, dict) else str(detail)


def writable_engine():
    """The seeder's engine (tl_scratch.new_engine's rules: the scratch database only, one connection per use, so the
    counters Postgres keeps are reported when each closes) with a patient way of connecting: the throwaway database,
    reached through the Docker host, sometimes lets the first connect of a test run time out, so each connect has a
    short timeout and is tried again."""
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from tl_scratch import assert_scratch

    assert_scratch()
    url = make_url(SCRATCH_URL)

    async def connect():
        import asyncpg

        last: BaseException | None = None
        for _ in range(6):
            try:
                return await asyncpg.connect(user=url.username, password=url.password, host=url.host, port=url.port,
                                             database=url.database, timeout=8)
            except (TimeoutError, OSError) as exc:      # asyncio.TimeoutError is TimeoutError on Python 3.11+
                last = exc
                await asyncio.sleep(0.5)
        raise last  # type: ignore[misc]

    return create_async_engine(SCRATCH_URL, poolclass=NullPool, async_creator=connect)


async def boot() -> Story:
    """Boot the app for real on the throwaway database and install the fakes. Undone by shutdown()."""
    mp = pytest.MonkeyPatch()
    mp.delenv("TRACE_LAB_APPLY_DATABASES", raising=False)
    mp.setenv("ACTIVITY_LOG_ENABLED", "false")
    mp.setenv("TRACE_LAB_JUDGE_MODEL", MODEL)
    for name in ("TRACE_LAB_GOLDEN_MAX_K", "TRACE_LAB_GOLDEN_MONTHLY_USD", "TRACE_LAB_GOLDEN_RUN_MAX_USD",
                 "TRACE_LAB_GOLDEN_MIN_TOKEN_MIN", "TRACE_LAB_PROPOSAL_RUN_MAX_USD", "TRACE_LAB_PROPOSALS_MONTHLY_USD",
                 "TRACE_LAB_MONITOR_USD_CAP", "TRACE_LAB_JUDGE_RUN_USD_CAP"):
        mp.delenv(name, raising=False)
    for attr in ("_engine", "_session_factory", "AsyncSessionLocal"):
        mp.setattr(database, attr, None)
    db.reset_for_tests()
    proxy.reset_for_tests()
    writable = writable_engine()
    async with writable.begin() as conn:
        await conn.exec_driver_sql("DROP SCHEMA IF EXISTS plenum_cafm CASCADE")
        await conn.exec_driver_sql(f"DROP SCHEMA IF EXISTS {migrate.SCHEMA} CASCADE")
    await _create_main_tables(mp, writable)          # svc-deepagents' own DDL, on the scratch database only
    settings = Settings(main_db_url=SCRATCH_URL, lab_db_url=SCRATCH_URL, schema_mode="apply", enabled=True)
    app = app_module.create_app(settings)
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()                      # the REAL start-up: read-only main engine, migrations, sweeps
    for _ in range(3):      # the throwaway database's first connection sometimes times out: the service's own re-check
        if db.ready():
            break
        await asyncio.sleep(2)
        await db.refresh_state(max_age_s=0)
    models = FakeModels()
    chat = StoryChat(app)
    import src.http_client as http_client

    mp.setattr(principal_module, "_request", fake_identity)
    principal_module._cache.clear()
    mp.setattr(http_client, "request", fake_identity)
    mp.setattr(skill_lab, "_ask_json", models.ask_json)
    mp.setattr(judges, "ASK", models.ask_judge)
    mp.setattr(orchestrator_host, "get_orchestrator", lambda: chat)
    mp.setattr(orchestrator_host, "assert_isolated", lambda: [])

    async def no_overlays() -> int:
        return 0

    mp.setattr(orchestrator_host, "ensure_overlays_readonly", no_overlays)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://trace-lab", timeout=60.0)
    return Story(app=app, client=client, writable=writable, models=models, chat=chat, mp=mp, lifespan=lifespan)


async def shutdown(story: Story) -> None:
    from trace_lab import runner

    try:
        for task in [*runner._ACTIVE.values(), *getattr(runner, "_GRADING", set())]:
            await asyncio.gather(task, return_exceptions=True)
    finally:
        await story.client.aclose()
        await story.lifespan.__aexit__(None, None, None)
        await story.writable.dispose()
        db.reset_for_tests()
        proxy.reset_for_tests()
        orchestrator_host.reset_for_tests()
        principal_module._cache.clear()
        story.mp.undo()
