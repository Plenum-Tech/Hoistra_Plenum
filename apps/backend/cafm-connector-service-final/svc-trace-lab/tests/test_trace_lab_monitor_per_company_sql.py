"""Monitoring batches per company over HTTP, on a scratch Postgres (tracelab_scratch), through the real monitor store, the
real job store and the review module's batch maker; only the judge's model call is replaced (Phase 8, decision A).

A superadmin acting for a company starts that company's batch: the row, its job, its verdicts, the evidence kept for
its answers and the review batches it opens all carry that company, the checks and the sample are that company's
answers only, and that company's admins read the batch, its Due marker and its chart while another company's admins
read nothing of it. A superadmin acting for no company starts a platform-wide batch (organization_id NULL) that no
company admin can read, as before. A company admin starts nothing (403 platform_only). Resume keeps the company; a
review batch opens only for the batch's own company. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from src.services.principal import Principal, current_principal
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import jobs, judges, monitor, routes_monitor
from trace_lab_m2_fixtures import truncate_chat, turn_id

pytestmark = requires_scratch

UTC = timezone.utc
ORG_A = fx.ORG_A
ORG_B = fx.ORG_B
OWNER = fx.OWNER_ID
B = "/api/trace-lab"
MARKER = "WRONG-PERIOD"
WEEK_START, WEEK_END = monitor.last_complete_week(datetime.now(UTC))        # the window a batch with no dates covers
EX3 = [{"turn_id": "turn-a", "role": "clear_fail", "critique": "Names AHU-09.", "verdict": "Fail"},
       {"turn_id": "turn-b", "role": "clear_pass", "critique": "Every code returned.", "verdict": "Pass"},
       {"turn_id": "turn-c", "role": "borderline", "critique": "Implied code.", "verdict": "Fail"}]
EVIDENCE = json.dumps({"text": "QUESTION\nq", "chars": 10, "sha": "x", "truncated": False})


def _p(role: str, org: str | None = None, uid: str = OWNER) -> Principal:
    return Principal(user_id=uuid.UUID(uid), email=f"{role}@example.com",
                     organization_id=uuid.UUID(org) if org else None, role=role, can_ingest=True, building_ids=None)


def _client(principal: Principal) -> httpx.AsyncClient:
    """The router on the real stores, in this test's own event loop; only the caller is replaced."""
    app = FastAPI()
    app.include_router(routes_monitor.router, prefix=B)
    app.dependency_overrides[current_principal] = lambda: principal
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://trace-lab")


@pytest.fixture
async def chat(main_scratch, lab_scratch, monkeypatch):  # noqa: F811
    """Seeded chat tables, a frozen judge in use, no real model, and no background task: a test works a batch itself."""
    routes_monitor.SPENDING.reset()
    started: list[str] = []
    monkeypatch.setattr(monitor, "run_in_background", lambda batch_id: started.append(batch_id))
    calls: list[str] = []

    async def ask(model, prompt_text, judge_text):
        calls.append(judge_text)
        fail = MARKER in judge_text
        return SimpleNamespace(failure_present=1 if fail else 0, critique="Quotes July." if fail else "Matches.",
                               status="decided", usd=0.01, error=None)

    real = monitor.stats.corrected_prevalence
    monkeypatch.setattr(judges, "ASK", ask)
    monkeypatch.setattr(monitor.stats, "corrected_prevalence",
                        lambda s, l, p, **kw: real(s, l, p, bootstrap_iterations=500))     # the same arithmetic, fast

    async def propose(batch, mode_id, ids):
        return []

    monkeypatch.setattr(monitor, "_propose_cases", propose)
    env = SimpleNamespace(main=main_scratch, lab=lab_scratch, started=started, judge_calls=calls)
    env.judge_id, env.mode_id = await _judge_in_use(lab_scratch)
    yield env
    await truncate_chat(main_scratch)
    routes_monitor.SPENDING.reset()


async def _judge_in_use(lab) -> tuple[str, str]:
    """A frozen LLM judge with decision 'use', a finished held-out test run and a 15% threshold."""
    jid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    async with lab.begin() as conn:
        mid = await fx.add_mode(conn, "wrong_period_pc", status="confirmed", agent="udr",
                                definition="Present when the answer quotes the wrong period.")
        await conn.execute(text(
            "INSERT INTO tracelab.judge_splits (id, mode_id, version, fractions, seed, min_per_class, min_labels, counts, "
            "labels_sha) VALUES (CAST(:id AS uuid), CAST(:m AS uuid), 1, CAST(:fr AS numeric(4,3)[]), 7, 10, 30, "
            "CAST('{}' AS jsonb), :sha)"), {"id": sid, "m": mid, "fr": [0.2, 0.4, 0.4], "sha": "0" * 64})
        for tid, split, present in (("turn-a", "train", True), ("turn-b", "train", False), ("turn-c", "train", True)):
            await conn.execute(text(
                "INSERT INTO tracelab.judge_split_members (split_id, turn_id, organization_id, conversation_key, split, "
                "label_present, label_source, evidence_snapshot) VALUES (CAST(:s AS uuid), :t, CAST(:o AS uuid), :ck, "
                ":sp, :p, 'human_structured_label', CAST(:ev AS jsonb))"),
                {"s": sid, "t": tid, "o": ORG_A, "ck": "turn:" + tid, "sp": split, "p": present, "ev": EVIDENCE})
        await conn.execute(text(
            "INSERT INTO tracelab.judges (id, mode_id, version, judge_key, kind, split_id, model, prompt_text, "
            "prompt_parts, prompt_hash) VALUES (CAST(:id AS uuid), CAST(:m AS uuid), 0, 'wrong_period_pc-v0', 'llm', "
            "CAST(:s AS uuid), 'claude-sonnet-5', 'prompt', CAST(:parts AS jsonb), 'hash00000000')"),
            {"id": jid, "m": mid, "s": sid, "parts": json.dumps({"fail_rule": "Wrong period.", "pass_rule": "Right.",
                                                                  "examples": EX3})})
        pairs = [{"turn_id": f"held-{i}", "label": 1 if i < 10 else 0, "failure_present": 1 if i < 10 else 0,
                  "status": "decided"} for i in range(20)]
        await conn.execute(text(
            "UPDATE tracelab.judges SET status = 'frozen', stop_reason = 'Stable.', frozen_at = now(), "
            "frozen_by = CAST(:u AS uuid), frozen_by_email = 'owner@plenum.test', decision = 'use', "
            "monitor_threshold = 0.15 WHERE id = CAST(:j AS uuid)"), {"j": jid, "u": OWNER})
        await conn.execute(text(
            "INSERT INTO tracelab.judge_runs (id, judge_id, split, status, items, results, n) VALUES "
            "(CAST(:id AS uuid), CAST(:j AS uuid), 'test', 'done', CAST('[]' AS jsonb), CAST(:r AS jsonb), 20)"),
            {"id": str(uuid.uuid4()), "j": jid, "r": json.dumps(pairs)})
    return jid, mid


async def _seed(engine, org: str, first: int, count: int, *, wrong: set[int]) -> list[str]:
    """`count` chat answers of one company inside last complete week, numbered from `first`; those in `wrong` carry the
    marker the fake judge fails on. One span each (the evidence copy has something to keep)."""
    ids = []
    async with engine.begin() as conn:
        for n in range(first, first + count):
            at = WEEK_START + timedelta(days=1, minutes=n)
            await conn.execute(text(
                "INSERT INTO plenum_cafm.agent_trace_turns (turn_id, session_id, organization_id, user_id, email, question, "
                "answer, route, started_at, ok) VALUES (:t, :s, CAST(:o AS uuid), CAST(:u AS uuid), 'asker@example.com', "
                "'How many jobs were raised in August?', :a, 'single:wo_engine', :at, true)"),
                {"t": turn_id(n), "s": f"sess-{n}", "o": org, "u": str(uuid.uuid4()),
                 "a": MARKER + " in July" if n in wrong else "In August 2026.", "at": at})
            await conn.execute(text(
                "INSERT INTO plenum_cafm.agent_trace_spans (id, turn_id, organization_id, seq, kind, name, agent, "
                "started_at, output, ok) VALUES (CAST(:id AS uuid), :t, CAST(:o AS uuid), 1, 'tool', "
                "'answer_from_records', 'wo_engine', :at, CAST(:out AS jsonb), true)"),
                {"id": str(uuid.uuid4()), "t": turn_id(n), "o": org, "at": at, "out": json.dumps({"count": 758})})
            ids.append(turn_id(n))
    return ids


BODY = {"sample_rate": 0.5, "risk_groups": ["thumbs_down"]}


async def _start(client, env, org: str | None = None, **body) -> dict:
    """Start a batch over HTTP (for a company when `org` is named). Returns the response's batch; the background task is
    recorded, not run."""
    r = await client.post(f"{B}/monitor/batches", json={**BODY, **body}, params={"organization_id": org} if org else None)
    assert r.status_code == 202, r.text
    assert env.started[-1] == r.json()["batch"]["id"]
    return r.json()["batch"]


async def _lab(env, sql: str, **params):
    async with env.lab.connect() as conn:
        return (await conn.execute(text(sql), params)).all()


# -- who starts, and for whom ---------------------------------------------------------------------------------------

async def test_a_superadmin_acting_for_a_company_starts_that_companys_batch_and_only_that_company_reads_it(chat):
    await _seed(chat.main, ORG_A, 1, 12, wrong=set(range(1, 9)))
    await _seed(chat.main, ORG_B, 101, 6, wrong=set(range(101, 107)))
    async with _client(_p("superadmin")) as boss, _client(_p("admin", ORG_A, "aaaaaaaa-0000-4000-8000-0000000000a1")) as a, \
            _client(_p("admin", ORG_B, "bbbbbbbb-0000-4000-8000-0000000000b1")) as b:
        r = await boss.get(f"{B}/monitor/plan", params={"organization_id": ORG_A})
        assert r.status_code == 200 and r.json()["n_window"] == 12            # the preview counts that company's answers
        assert (await boss.get(f"{B}/monitor/plan")).json()["n_window"] == 18  # acting for none: every company's
        batch = await _start(boss, chat, ORG_A)
        assert batch["organization_id"] == ORG_A and batch["scope"] == "company" and batch["status"] == "queued"
        assert [r[0] for r in await _lab(chat, "SELECT organization_id::text FROM tracelab.monitor_batches")] == [ORG_A]
        job = (await _lab(chat, "SELECT kind, scope_key, organization_id::text, usd_cap FROM tracelab.jobs"))[0]
        assert job[:3] == ("monitor_batch", f"monitor_batch:{ORG_A}", ORG_A) and job[3] == Decimal("5.0000")

        await monitor.work_batch(batch["id"])
        done = await monitor._load_batch(batch["id"])
        assert done["status"] == "done" and str(done["organization_id"]) == ORG_A
        plan, results = done["plan"], done["results"]
        assert results["n_window"] == 12 and len(plan["random"]) == 6           # that company's answers only
        assert all(int(t[-16:], 16) < 100 for t in plan["to_judge"]) and len(chat.judge_calls) == len(plan["to_judge"])
        mode = results["modes"][0]
        assert mode["mode_id"] == chat.mode_id and mode["n_sample"] == 6 and mode["crossed"] is True

        # the company's own admin reads the batch, its Due marker and its chart
        listed = (await a.get(f"{B}/monitor/batches")).json()
        assert [x["id"] for x in listed["batches"]] == [batch["id"]] and listed["batches"][0]["scope"] == "company"
        assert listed["due"] == {"period_label": monitor.stats.iso_week_label(WEEK_START), "is_due": False}
        assert (await a.get(f"{B}/monitor/batches/{batch['id']}")).json()["batch"]["id"] == batch["id"]
        chart = (await a.get(f"{B}/monitor/chart", params={"mode_id": chat.mode_id})).json()
        assert [p["period_label"] for p in chart["points"]] == [monitor.stats.iso_week_label(WEEK_START)]
        assert chart["points"][0]["raw"] == mode["raw"] and chart["threshold"] == 0.15
        checks = (await a.get(f"{B}/monitor/code-checks")).json()["checks"]
        assert sum(w["n"] for c in checks for w in c["weeks"]) == 12 * len(checks)   # every live check saw all 12

        # another company reads nothing of it: no batch, a Due marker of its own, an empty chart, a 404 for the batch
        other = (await b.get(f"{B}/monitor/batches")).json()
        assert other["batches"] == [] and other["due"]["is_due"] is True
        r = await b.get(f"{B}/monitor/batches/{batch['id']}")
        assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"
        assert (await b.get(f"{B}/monitor/chart", params={"mode_id": chat.mode_id})).json()["points"] == []
        assert all(w["n"] == 0 for c in (await b.get(f"{B}/monitor/code-checks")).json()["checks"] for w in c["weeks"])

        # the platform view lists it as one company's; its own Due marker (platform scope) is untouched by it
        platform = (await boss.get(f"{B}/monitor/batches")).json()
        assert [x["id"] for x in platform["batches"]] == [batch["id"]] and platform["due"]["is_due"] is True
        acting_b = (await boss.get(f"{B}/monitor/batches", params={"organization_id": ORG_B})).json()
        assert acting_b["batches"] == [] and acting_b["due"]["is_due"] is True

    # every verdict, every kept copy of an answer, and the job's spend belong to that company
    verdict_orgs = {r[0] for r in await _lab(chat, "SELECT DISTINCT organization_id::text FROM tracelab.monitor_verdicts")}
    assert verdict_orgs == {ORG_A}
    kept = await _lab(chat, "SELECT turn_id, organization_id::text, reason FROM tracelab.evidence_turns")
    assert {r[1] for r in kept} == {ORG_A} and {r[2] for r in kept} == {"monitored"}
    assert {r[0] for r in kept} >= set(plan["to_judge"])                          # every judged answer outlives the purge
    spent = (await _lab(chat, "SELECT organization_id::text, usd_spent, status FROM tracelab.jobs"))[0]
    assert spent[0] == ORG_A and spent[2] == "done"
    assert Decimal(str(spent[1])) == Decimal("0.01") * len(chat.judge_calls) and Decimal(str(done["usd"])) == Decimal(str(spent[1]))


async def test_a_superadmin_acting_for_no_company_starts_a_platform_batch_that_no_company_admin_reads(chat):
    await _seed(chat.main, ORG_A, 1, 12, wrong=set(range(1, 9)))
    await _seed(chat.main, ORG_B, 101, 6, wrong=set(range(101, 107)))
    async with _client(_p("superadmin")) as boss, _client(_p("admin", ORG_A, "aaaaaaaa-0000-4000-8000-0000000000a1")) as a, \
            _client(_p("admin", ORG_B, "bbbbbbbb-0000-4000-8000-0000000000b1")) as b:
        batch = await _start(boss, chat)
        assert batch["organization_id"] is None and batch["scope"] == "platform"
        assert [r[0] for r in await _lab(chat, "SELECT organization_id FROM tracelab.monitor_batches")] == [None]
        assert (await _lab(chat, "SELECT scope_key, organization_id FROM tracelab.jobs"))[0] == ("monitor_batch:*", None)
        await monitor.work_batch(batch["id"])
        done = await monitor._load_batch(batch["id"])
        assert done["status"] == "done" and done["results"]["n_window"] == 18          # every company's answers
        assert {r[0] for r in await _lab(chat, "SELECT DISTINCT organization_id::text FROM tracelab.monitor_verdicts")} == {
            ORG_A, ORG_B}
        assert {r[0] for r in await _lab(chat, "SELECT DISTINCT organization_id::text FROM tracelab.evidence_turns")} == {
            ORG_A, ORG_B}

        for admin in (a, b):                                                    # a platform batch belongs to no company
            listed = (await admin.get(f"{B}/monitor/batches")).json()
            assert listed["batches"] == [] and listed["due"]["is_due"] is True
            r = await admin.get(f"{B}/monitor/batches/{batch['id']}")
            assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"
            assert (await admin.get(f"{B}/monitor/chart", params={"mode_id": chat.mode_id})).json()["points"] == []
        platform = (await boss.get(f"{B}/monitor/batches")).json()
        assert [x["id"] for x in platform["batches"]] == [batch["id"]] and platform["due"]["is_due"] is False
        assert len((await boss.get(f"{B}/monitor/chart", params={"mode_id": chat.mode_id})).json()["points"]) == 1
    assert chat.judge_calls                                                    # the platform batch did judge answers


async def test_a_company_batch_and_the_platform_batch_run_side_by_side_and_a_second_one_per_scope_is_refused(chat):
    await _seed(chat.main, ORG_A, 1, 4, wrong=set())
    async with _client(_p("superadmin")) as boss:
        first = await _start(boss, chat, ORG_A)
        platform = await _start(boss, chat)
        other = await _start(boss, chat, ORG_B)
        assert len({first["id"], platform["id"], other["id"]}) == 3
        for org in (ORG_A, ORG_B, None):                                       # one open batch per scope
            r = await boss.post(f"{B}/monitor/batches", json=BODY, params={"organization_id": org} if org else None)
            assert r.status_code == 409 and r.json()["detail"]["reason"] == "batch_running", org
    scopes = sorted(r[0] for r in await _lab(chat, "SELECT scope_key FROM tracelab.jobs"))
    assert scopes == sorted([f"monitor_batch:{ORG_A}", f"monitor_batch:{ORG_B}", "monitor_batch:*"])


async def test_a_company_admin_starts_nothing_and_no_row_or_job_is_made(chat):
    await _seed(chat.main, ORG_A, 1, 4, wrong=set())
    async with _client(_p("admin", ORG_A, "aaaaaaaa-0000-4000-8000-0000000000a1")) as a:
        for params in (None, {"organization_id": ORG_A}):
            r = await a.post(f"{B}/monitor/batches", json=BODY, params=params)
            assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only", params
            r = await a.get(f"{B}/monitor/plan", params=params)
            assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only", params
            r = await a.post(f"{B}/monitor/batches/{uuid.uuid4()}/resume", json={}, params=params)
            assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only", params
            r = await a.post(f"{B}/monitor/batches/{uuid.uuid4()}/review-batch", json={"mode_id": chat.mode_id},
                             params=params)
            assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only", params
        r = await a.post(f"{B}/monitor/batches", json=BODY, params={"organization_id": ORG_B})
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "bad_company"
    assert await _lab(chat, "SELECT 1 FROM tracelab.monitor_batches") == [] and await _lab(chat, "SELECT 1 FROM tracelab.jobs") == []
    assert chat.started == []


# -- review batches ---------------------------------------------------------------------------------------------------

async def _review_rows(env):
    return await _lab(env, "SELECT b.organization_id::text, b.created_by::text, b.strategy, "
                           "(SELECT array_agg(i.turn_id ORDER BY i.position) FROM tracelab.sample_items i "
                           "WHERE i.batch_id = b.id) FROM tracelab.sample_batches b ORDER BY b.created_at, b.id")


@pytest.fixture
def quiet(monkeypatch):
    """No review batch opens by itself when a batch ends: the routes are what these tests open them with."""
    async def nothing(batch, plan, results):
        return None

    monkeypatch.setattr(monitor, "_act_on_crossings", nothing)


def _review_path(batch: dict) -> str:
    return f"{B}/monitor/batches/{batch['id']}/review-batch"


async def test_a_company_batch_opens_review_batches_for_that_company_and_no_other_company_can_open_it(chat, quiet):
    a_ids = await _seed(chat.main, ORG_A, 1, 12, wrong=set(range(1, 9)))
    await _seed(chat.main, ORG_B, 101, 12, wrong=set(range(101, 109)))
    ask = {"mode_id": chat.mode_id}
    async with _client(_p("superadmin")) as boss:
        mine = await _start(boss, chat, ORG_A)
        await monitor.work_batch(mine["id"])
        r = await boss.post(_review_path(mine), json=ask, params={"organization_id": ORG_B})
        assert r.status_code == 404 and r.json()["detail"] == {"reason": "not_found",
                                                               "error": "That monitoring batch was not found."}
        assert await _review_rows(chat) == []                                  # nothing was made for anyone

        r = await boss.post(_review_path(mine), json=ask, params={"organization_id": ORG_A})
        assert r.status_code == 201 and len(r.json()["review_batch_ids"]) == 1
        rows = await _review_rows(chat)
        assert len(rows) == 1 and rows[0][:3] == (ORG_A, OWNER, "monitor_flagged")
        assert rows[0][3] and set(rows[0][3]) <= set(a_ids)                    # that company's flagged answers only
        kept = {r[0] for r in await _lab(chat, "SELECT organization_id::text FROM tracelab.evidence_turns "
                                               "WHERE turn_id = ANY(CAST(:ids AS text[]))", ids=rows[0][3])}
        assert kept == {ORG_A}                                                 # and their evidence is the company's
        again = await boss.post(_review_path(mine), json=ask, params={"organization_id": ORG_A})
        assert again.status_code == 201 and again.json()["review_batch_ids"] == r.json()["review_batch_ids"]
        assert len(await _review_rows(chat)) == 1                              # opened once per batch and mode


async def test_the_platform_view_still_opens_a_platform_batch_for_every_company_and_a_company_view_cannot(chat, quiet):
    a_ids = await _seed(chat.main, ORG_A, 1, 12, wrong=set(range(1, 9)))
    b_ids = await _seed(chat.main, ORG_B, 101, 12, wrong=set(range(101, 109)))
    ask = {"mode_id": chat.mode_id}
    async with _client(_p("superadmin")) as boss:
        platform = await _start(boss, chat)
        await monitor.work_batch(platform["id"])
        for org in (ORG_A, ORG_B):                                             # a platform batch belongs to no company: it is not theirs to open
            r = await boss.post(_review_path(platform), json=ask, params={"organization_id": org})
            assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found", org
        assert await _review_rows(chat) == []

        r = await boss.post(_review_path(platform), json=ask)
        assert r.status_code == 201 and len(r.json()["review_batch_ids"]) == 2
        rows = await _review_rows(chat)
        assert sorted(x[0] for x in rows) == sorted([ORG_A, ORG_B])
        for org, _by, _strategy, turns in rows:
            assert turns and set(turns) <= set(a_ids if org == ORG_A else b_ids)   # each holds its own company's answers


async def test_the_platform_view_can_still_open_a_company_batch_and_it_files_only_that_company(chat, quiet):
    a_ids = await _seed(chat.main, ORG_A, 1, 12, wrong=set(range(1, 9)))
    await _seed(chat.main, ORG_B, 101, 12, wrong=set(range(101, 109)))
    async with _client(_p("superadmin")) as boss:
        mine = await _start(boss, chat, ORG_A)
        await monitor.work_batch(mine["id"])
        r = await boss.post(_review_path(mine), json={"mode_id": chat.mode_id})
        assert r.status_code == 201 and len(r.json()["review_batch_ids"]) == 1
        rows = await _review_rows(chat)
        assert [x[0] for x in rows] == [ORG_A] and set(rows[0][3]) <= set(a_ids)


async def test_a_company_batch_that_crosses_its_threshold_opens_its_review_batches_by_itself_for_that_company(chat):
    a_ids = await _seed(chat.main, ORG_A, 1, 12, wrong=set(range(1, 9)))
    await _seed(chat.main, ORG_B, 101, 12, wrong=set(range(101, 109)))
    async with _client(_p("superadmin")) as boss:
        mine = await _start(boss, chat, ORG_A)
        await monitor.work_batch(mine["id"])
    done = await monitor._load_batch(mine["id"])
    ids = done["results"]["modes"][0]["review_batch_ids"]
    assert done["results"]["modes"][0]["crossed"] is True and len(ids) == 1
    rows = await _review_rows(chat)
    assert [x[0] for x in rows] == [ORG_A] and set(rows[0][3]) <= set(a_ids)


# -- resume --------------------------------------------------------------------------------------------------------------

async def test_resume_keeps_the_batchs_company(chat):
    await _seed(chat.main, ORG_A, 1, 12, wrong={4, 5})
    async with _client(_p("superadmin")) as boss, _client(_p("admin", ORG_A, "aaaaaaaa-0000-4000-8000-0000000000a1")) as a:
        batch = await _start(boss, chat, ORG_A, risk_groups=["thumbs_down"])
        job_id = (await _lab(chat, "SELECT id::text FROM tracelab.jobs"))[0][0]
        await jobs.claim(job_id)                                           # a worker took it, then the service restarted
        await jobs.mark_interrupted(stale_after_s=-1)
        seen = (await a.get(f"{B}/monitor/batches")).json()["batches"][0]  # reading settles an interrupted batch
        assert seen["status"] == "interrupted" and seen["organization_id"] == ORG_A

        for params in ({"organization_id": ORG_B}, None):                  # another company, or acting for none: not found
            r = await boss.post(f"{B}/monitor/batches/{batch['id']}/resume", json={}, params=params)
            assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found", params
        assert (await _lab(chat, "SELECT status FROM tracelab.monitor_batches"))[0][0] == "interrupted"
        r = await a.post(f"{B}/monitor/batches/{batch['id']}/resume", json={})
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only"

        r = await boss.post(f"{B}/monitor/batches/{batch['id']}/resume", json={}, params={"organization_id": ORG_A})
        assert r.status_code == 202 and r.json()["batch"]["status"] == "queued"
        assert r.json()["batch"]["organization_id"] == ORG_A and r.json()["batch"]["scope"] == "company"
        assert chat.started[-1] == batch["id"]
        assert (await _lab(chat, "SELECT status, organization_id::text FROM tracelab.monitor_batches"))[0] == ("queued", ORG_A)
        assert (await _lab(chat, "SELECT status, organization_id::text FROM tracelab.jobs"))[0] == ("queued", ORG_A)

        await monitor.work_batch(batch["id"])                              # the resumed batch finishes as the company's
        done = (await a.get(f"{B}/monitor/batches/{batch['id']}")).json()["batch"]
        assert done["status"] == "done" and done["organization_id"] == ORG_A
        r = await boss.post(f"{B}/monitor/batches/{batch['id']}/resume", json={}, params={"organization_id": ORG_A})
        assert r.status_code == 409 and r.json()["detail"]["reason"] == "not_interrupted"


async def test_a_platform_batch_is_resumed_from_the_platform_view_only(chat):
    await _seed(chat.main, ORG_A, 1, 6, wrong=set())
    async with _client(_p("superadmin")) as boss:
        batch = await _start(boss, chat)
        job_id = (await _lab(chat, "SELECT id::text FROM tracelab.jobs"))[0][0]
        await jobs.claim(job_id)
        await jobs.mark_interrupted(stale_after_s=-1)
        await boss.get(f"{B}/monitor/batches")                             # settles it
        r = await boss.post(f"{B}/monitor/batches/{batch['id']}/resume", json={}, params={"organization_id": ORG_A})
        assert r.status_code == 404                                        # a company's view does not reach it
        r = await boss.post(f"{B}/monitor/batches/{batch['id']}/resume", json={})
        assert r.status_code == 202 and r.json()["batch"]["organization_id"] is None
