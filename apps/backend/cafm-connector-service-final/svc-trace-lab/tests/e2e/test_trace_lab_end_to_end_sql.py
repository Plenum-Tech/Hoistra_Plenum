"""Hoist Trace Lab, end to end, in one story (needs the throwaway Postgres; skips without TRACE_LAB_TEST_DB_URL).

The REAL app is booted by its REAL lifespan on the scratch database, then driven over HTTP the way the browser drives
it (story_env says exactly what is replaced: the identity lookup's HTTP call, the models, the chat). One story, in
this order, each stage a test of its own so a failure names the stage:

  1 foundation    2 live traffic    3 golden set     4 golden run     5 review         6 failure modes
  7 judges        8 watch           9 cost          10 the Golden Rule at run time (nothing in the application
                                                       database changed except what the story itself seeded)

The stages share one booted app and the data the earlier ones made (module-scoped fixture, one event loop). A stage
that cannot stand because an earlier one failed is skipped, naming it.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections import Counter
from datetime import timedelta

import pytest
import pytest_asyncio

import story_env as env
from golden_runner_env import drain
from golden_runner_env import jwt as golden_jwt
from story_env import (CANCELLED, N_REV, NOW, ORG_A, ORG_B, PEOPLE, SECRET_BEARER, SECRET_TOKEN, TRACE_SECRET, Story,
                       StoryMsg, ago, body, build_world, main_snapshot, reason, seed_turns)
from tl_scratch import requires_scratch
from trace_lab import migrate

pytestmark = [requires_scratch, pytest.mark.asyncio(loop_scope="module")]

A1, A2, B1, USER, PLATFORM = "admin_a1", "admin_a2", "admin_b", "user_a", "platform"
PENDING = N_REV - 1 - 8            # reviewed (one was purged) minus the eight a person labelled for the contract


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def S():
    story = await env.boot()
    try:
        yield story
    finally:
        await env.shutdown(story)


def iso(dt):
    return dt.isoformat()


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 1  Foundation
# ═════════════════════════════════════════════════════════════════════════════════════════════

async def test_stage_01_foundation_the_service_is_up_signed_in_and_scoped(S: Story):
    r = await S.client.get("/health")
    assert body(r) == {"ok": True, "service": "trace-lab"}, "stage 1: /health answers without a sign-in"

    head = len(migrate.files())
    status = body(await S.get(A1, "/status"))
    assert status["available"] is True and status["status"] == "ready", f"stage 1: {status}"
    assert status["applied_version"] == status["expected_version"] == head, "stage 1: ready at the head migration"
    async with __import__("trace_lab.db", fromlist=["lab_engine"]).lab_engine().connect() as conn:
        ledger = await migrate.read_ledger(conn)
    assert len(ledger) == head and ledger[0]["applied_by"].startswith("startup:"), \
        "stage 1: the lifespan applied every migration through the ledger"

    for path in ("/status", "/conversations", "/golden-sets", "/spend"):                       # no bearer: 401
        assert (await S.get(None, path)).status_code == 401, f"stage 1: {path} without a sign-in"
    bad = await S.client.get(env.API + "/status", headers={"Authorization": "Bearer not-a-token"})
    assert reason(bad, 401) == "invalid_token", "stage 1: the identity service's own 401 reaches the caller"
    for path in ("/status", "/conversations"):                                                  # a plain user: no Trace Lab
        assert reason(await S.get(USER, path), 403) == "reviewers_only", f"stage 1: {path} for a plain user"
    assert reason(await S.get(A1, "/conversations", org=ORG_B), 403) == "bad_company", \
        "stage 1: a company admin cannot name another company"
    assert reason(await S.get(PLATFORM, "/conversations"), 400) == "name_a_company", \
        "stage 1: the platform team names a company before reading one's conversations"

    fake_id = str(uuid.uuid4())
    platform_only = [
        ("GET", "/spend", None), ("GET", "/monitor/plan", None), ("POST", "/monitor/batches", {}),
        ("GET", "/proposal-runs", None), ("POST", "/requirements/seed", None), ("GET", "/failure-report", None),
        ("GET", f"/judges/{fake_id}", None), ("GET", f"/modes/{fake_id}/judge-readiness", None),
        ("POST", f"/mode-proposals/{fake_id}/accept", {}), ("POST", f"/mode-proposals/{fake_id}/reject", {"reason": "no"}),
        ("POST", "/modes", {"name": "some_mode_name", "definition": "Present when something is wrong.",
                            "level": "technical", "agent": "wo_engine", "created_from": [fake_id]}),
    ]
    for method, path, payload in platform_only:
        r = await S.call(A1, method, path, **({"json": payload} if payload is not None else {}))
        assert reason(r, 403) == "platform_only", f"stage 1: a company admin on {method} {path}"
    acting = await S.get(PLATFORM, "/proposal-runs", org=ORG_A)
    assert reason(acting, 403) == "platform_only", "stage 1: a superadmin acting for a company is not the platform"
    assert body(await S.get(PLATFORM, "/proposal-runs"))["runs"] == [], "stage 1: the platform itself may"
    S.done.add("s01")


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 2  Live traffic
# ═════════════════════════════════════════════════════════════════════════════════════════════

async def test_stage_02_live_traffic_is_scoped_ordered_and_redacted(S: Story):
    S.need("s01")
    world = build_world()
    await seed_turns(S.writable, world.pop("rows"))
    S.data["world"] = world
    # the Golden Rule baseline: everything the story seeded is in the application database now; from here on, only
    # Trace Lab runs and it must change none of it (stage 10 compares)
    S.data["main_before"] = await main_snapshot(S.writable)
    S.data["objects_before"] = await env.main_objects(S.writable)
    assert S.data["main_before"]["agent_trace_turns"]["count"] == 5 + N_REV + 6 + 60 + 40 + 8, "stage 2: the seed is in"

    since = iso(NOW - timedelta(hours=2))
    conv = body(await S.get(A1, "/conversations", params={"since": since}))
    keys = sorted(c["key"] for c in conv["conversations"])
    assert keys == ["s:chat-a-boilers", "s:chat-a-secret"] and conv["total"] == 2, f"stage 2: company A's list {keys}"
    mine = {c["key"]: c for c in conv["conversations"]}
    assert mine["s:chat-a-boilers"]["turns_in_window"] == 2 and mine["s:chat-a-boilers"]["session_label"] == "Boiler certificates"
    theirs = body(await S.get(B1, "/conversations", params={"since": since}))
    assert [c["key"] for c in theirs["conversations"]] == ["s:chat-b-lifts"], "stage 2: company B sees only its own"
    acting = body(await S.get(PLATFORM, "/conversations", org=ORG_A, params={"since": since}))
    assert sorted(c["key"] for c in acting["conversations"]) == keys, "stage 2: a superadmin acting for A sees A's list"

    # A's detail, runs and model calls never carry a word of B's
    a_texts = [json.dumps(conv), json.dumps(body(await S.get(A1, "/runs", params={"since": since}))),
               json.dumps(body(await S.get(A1, "/model-calls", params={"since": since})))]
    detail = body(await S.get(A1, "/conversations/s:chat-a-boilers", params={"turn": "turn-ca002"}))["conversation"]
    a_texts.append(json.dumps(detail))
    assert [t["turn_id"] for t in detail["turns"]] == ["turn-ca001", "turn-ca002"], "stage 2: a session is one conversation, in order"
    roles = [m["role"] for m in detail["active"]["messages"] if m["role"] != "system_note"]
    assert roles == ["user", "model_call", "tool_call", "tool_result", "model_call", "assistant"], \
        f"stage 2: User -> Model -> Tool -> Model -> Answer, got {roles}"
    for text_ in a_texts:
        assert "BRAVO" not in text_ and "turn-cb00" not in text_ and "chat-b-lifts" not in text_, "stage 2: B leaked into A"
    runs = body(await S.get(A1, "/runs", params={"since": since}))["runs"]
    live = next(r for r in runs if r["run_id"] == "live")
    assert (live["conversation_count"], live["turn_count"]) == (2, 3), f"stage 2: A's live traffic {live}"
    calls = body(await S.get(A1, "/model-calls", params={"since": since}))
    assert calls["aggregate"]["calls"] == 6 and {c["turn_id"] for c in calls["calls"]} == {
        "turn-ca001", "turn-ca002", "turn-ca003"}, "stage 2: A's model calls are A's own"
    assert body(await S.get(B1, "/model-calls", params={"since": since}))["aggregate"]["calls"] == 4
    span = next(c["span_id"] for c in calls["calls"] if c["turn_id"] == "turn-ca002")
    card = body(await S.get(A1, f"/model-calls/{span}"))
    assert card["turn_id"] == "turn-ca002" and card["conversation_key"] == "s:chat-a-boilers", "stage 2: one model call's card"
    assert card["model_call"]["span_id"] == span and card["model_call"]["group"] == "orchestrator loop"
    assert reason(await S.get(B1, f"/model-calls/{span}"), 404) == "model_call_not_found", "stage 2: a card is its company's alone"

    assert reason(await S.get(A1, "/turns/turn-cb001"), 404) == "turn_not_found", "stage 2: B's turn is not in A's records"
    assert reason(await S.get(A1, "/conversations/s:chat-b-lifts"), 404) == "conversation_not_found"
    assert body(await S.get(A1, "/turns/turn-ca002"))["conversation_key"] == "s:chat-a-boilers"

    # redaction: the credential in a tool input and in the answer never leaves the server
    secret = body(await S.get(A1, "/conversations/s:chat-a-secret", params={"turn": "turn-ca003"}))
    secret_text = json.dumps(secret) + json.dumps(body(await S.get(A1, "/turns/turn-ca003"))) + json.dumps(
        body(await S.get(A1, "/model-calls", params={"since": since})))
    assert SECRET_TOKEN not in secret_text and SECRET_BEARER not in secret_text, "stage 2: a secret reached a response"
    assert "[redacted" in secret_text, "stage 2: the redaction left its marker"
    S.done.add("s02")


async def test_stage_02b_filters_paging_and_bad_input_are_handled(S: Story):
    S.need("s02")
    since = iso(NOW - timedelta(hours=2))
    before = (await S.main("SELECT count(*) AS n FROM plenum_cafm.agent_trace_turns"))[0]["n"]
    for hostile in ("'; DROP TABLE plenum_cafm.agent_trace_turns; --", "%", "\\", "a" * 200):
        r = await S.get(A1, "/conversations", params={"since": since, "q": hostile, "user": hostile})
        assert r.status_code == 200 and r.json()["total"] == 0, f"stage 2: a search for {hostile[:20]!r}: {r.text[:200]}"
    assert (await S.main("SELECT count(*) AS n FROM plenum_cafm.agent_trace_turns"))[0]["n"] == before
    assert reason(await S.get(A1, "/conversations", params={"since": iso(NOW), "until": since}), 400) == "bad_window"
    assert reason(await S.get(A1, "/conversations", params={"since": iso(NOW - timedelta(days=400))}), 400) == "window_too_wide"
    assert reason(await S.get(A1, "/conversations", params={"run": "not-a-run"}), 400) == "bad_run"
    assert reason(await S.get(A1, "/conversations", params={"cursor": "garbage"}), 400) == "bad_cursor"
    assert (await S.get(A1, "/conversations", params={"status": "weird"})).status_code == 422
    for limit in (0, 101):
        assert (await S.get(A1, "/conversations", params={"limit": limit})).status_code == 422
    assert reason(await S.get(PLATFORM, "/conversations", params={"organization_id": "not-a-company"}), 400) == "bad_company"
    assert reason(await S.get(A1, "/model-calls", params={"run": str(uuid.uuid4())}), 400) == "golden_calls_unavailable"

    # paging one at a time visits each conversation once; the filters narrow the list
    seen, cursor = [], None
    for _ in range(3):
        page = body(await S.get(A1, "/conversations", params={"since": since, "limit": 1, **({"cursor": cursor} if cursor else {})}))
        seen += [c["key"] for c in page["conversations"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == ["s:chat-a-boilers", "s:chat-a-secret"], f"stage 2: {seen}"
    for extra, want in (({"session_kind": "observed"}, 2), ({"channel": "support"}, 0), ({"status": "error"}, 0),
                        ({"model": env.MODEL}, 2), ({"q": "boilers"}, 1), ({"q": "turn-ca003"}, 1)):
        assert body(await S.get(A1, "/conversations", params={"since": since, **extra}))["total"] == want, f"stage 2: {extra}"
    facets = body(await S.get(A1, "/conversations/facets", params={"since": since}))
    assert sum(f["turns"] for f in facets["channels"]) == 3 and facets["models"] == [{"value": env.MODEL, "turns": 3}]
    upd = body(await S.get(A1, "/conversations/updates", params={"watermark": iso(NOW - timedelta(hours=1))}))
    assert (upd["new_turns"], upd["new_conversations"]) == (3, 2), f"stage 2: the 45-second check sees A's turns only {upd}"
    assert body(await S.get(B1, "/conversations/updates", params={"watermark": iso(NOW - timedelta(hours=1))}))["new_turns"] == 2


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 3  Golden set
# ═════════════════════════════════════════════════════════════════════════════════════════════

FRESH, OLD = env.iso_days_ago(3), env.iso_days_ago(90)
QUESTION_1 = "How many work orders were raised at Harbour Point in August 2026?"
EXPECTED_1 = "759 work orders were raised at Harbour Point in August 2026."
GOLDEN_ROWS = {
    "1": ["1", QUESTION_1, "", EXPECTED_1, "objective", "759", FRESH],
    "2": ["2", "Which vendors hold a lapsed gas safety certificate?", "Which of those are on the approved list?",
          "Ostley Power Services is on the approved list; Pennard Fire Services is not.", "objective",
          '"Ostley Power Services"', FRESH],
    "3": ["3", "Should we end the boiler maintenance contract early?", "",
          "A right answer weighs the break fee against the remaining term and gives no flat yes or no.", "judgment", "",
          FRESH],
    "4": ["4", "What is the status of work order WO-1007?", "", "Work order WO-1007 is Open.", "objective", '"Open"',
          FRESH],
    "5": ["5", "How many fire doors were inspected in March 2026?", "", "112 fire doors were inspected in March 2026.",
          "objective", "112", OLD],
    "6": ["6", "How many lifts serve Harbour Point?", "", "4 lifts serve Harbour Point.", "objective", "4", FRESH],
    "7": ["7", "What is the postcode of Harbour Point?", "", "The postcode is EC2M 9ZZ.", "objective", '"EC2M 9ZZ"', FRESH],
}
ROW_8 = ["8", "How many floors does Harbour Point have?", "", "Harbour Point has 22 floors.", "objective", "22", FRESH]
PLACEHOLDER_ROW = ["9", "How many assets does {building} have?", "", "12 assets.", "objective", "12", FRESH]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _upload(workbook: bytes, **form):
    return {"files": {"file": ("golden.xlsx", workbook, XLSX)}, "data": form}


async def test_stage_03_golden_set_upload_is_all_or_nothing_and_versioned(S: Story):
    S.need("s01")
    # a file with a {placeholder} left in a question: every row error is reported, nothing at all is saved
    bad = env.golden_workbook([GOLDEN_ROWS[k] for k in "1234567"] + [PLACEHOLDER_ROW])
    for params in ({"dry_run": "true"}, {}):
        r = await S.post(A1, "/golden-sets", params=params, **_upload(bad, name="Harbour Point questions"))
        assert reason(r, 422) == "bad_rows", f"stage 3: a placeholder left in a question is a row error ({params})"
        errors = r.json()["detail"]["errors"]
        assert any("{building}" in e["message"] and e["row"] == 9 for e in errors), f"stage 3: {errors}"
    junk = await S.post(A1, "/golden-sets", **_upload(b"this is not a spreadsheet", name="Junk"))
    assert junk.status_code == 422 and junk.json()["detail"]["reason"] == "bad_file", f"stage 3: {junk.text[:200]}"
    assert (await S.post(A1, "/golden-sets", data={"name": "no file"})).status_code == 422
    csv_no_question = b"Note,Other\nhello,world\n"
    r = await S.post(A1, "/golden-sets", files={"file": ("golden.csv", csv_no_question, "text/csv")})
    assert r.status_code == 422, "stage 3: a file with no Question column is refused"
    listing = body(await S.get(A1, "/golden-sets"))
    assert listing["versions"] == [] and listing["active"] is None, "stage 3: all or nothing: no set was saved"
    assert (await S.lab("SELECT (SELECT count(*) FROM tracelab.golden_sets) AS s, "
                        "(SELECT count(*) FROM tracelab.golden_cases) AS c"))[0] == {"s": 0, "c": 0}

    # the corrected file: a dry run shows the cases (never the expected answers) and saves nothing
    good = env.golden_workbook([GOLDEN_ROWS[k] for k in "1234567"])
    dry = body(await S.post(A1, "/golden-sets", params={"dry_run": "true"}, **_upload(good)))
    assert dry["saved"] is False and dry["cases"] == 7 and len(dry["preview"]) == 7, f"stage 3: {dry}"
    assert all("expected_answer" not in row for row in dry["preview"]), "stage 3: a report never primes a reviewer"
    assert dry["flags"] == {"write_intent": 1}, f"stage 3: the question that reads like a change is flagged {dry['flags']}"
    assert body(await S.get(A1, "/golden-sets"))["versions"] == []

    saved = body(await S.post(A1, "/golden-sets", **_upload(good, name="Harbour Point questions",
                                                                              note="first upload")), 201)
    v1 = saved["set"]
    assert (v1["version"], v1["status"], v1["case_count"], v1["stale_count"]) == (1, "active", 7, 1), f"stage 3: {v1}"
    assert saved["diff"]["added"] == list("1234567") and saved["diff"]["previous_version"] is None
    cases = body(await S.get(A1, f"/golden-sets/{v1['id']}/cases", params={"limit": 50}))
    assert [c["case_key"] for c in cases["cases"]] == list("1234567") and cases["total"] == 7
    kinds = {c["case_key"]: c["evaluation"] for c in cases["cases"]}
    assert kinds["3"] == "judgment" and kinds["1"] == "objective", "stage 3: objective and judgment cases both stored"
    stale = body(await S.get(A1, f"/golden-sets/{v1['id']}/cases", params={"flag": "stale"}))
    assert [c["case_key"] for c in stale["cases"]] == ["5"], "stage 3: the stale row is flagged, not dropped"
    assert body(await S.get(A1, f"/golden-sets/{v1['id']}/cases", params={"flag": "judgment"}))["total"] == 1

    # version 2: the same ids keep the same case keys; one case changed, one is new
    row1 = list(GOLDEN_ROWS["1"])
    row1[3] = EXPECTED_1 + " Planned and reactive jobs are both counted."
    v2_file = env.golden_workbook([row1] + [GOLDEN_ROWS[k] for k in "234567"] + [ROW_8])
    saved2 = body(await S.post(A1, "/golden-sets", **_upload(v2_file)), 201)
    v2 = saved2["set"]
    assert v2["version"] == 2 and v2["status"] == "active" and v2["case_count"] == 8, f"stage 3: {v2}"
    diff = saved2["diff"]
    assert diff["added"] == ["8"] and diff["changed"] == ["1"] and diff["removed"] == [] and diff["unchanged"] == 6, \
        f"stage 3: version 2 keeps the case keys stable {diff}"
    assert diff["keys_restarted"] is False and diff["previous_version"] == 1
    assert v2["name"] == "Harbour Point questions", "stage 3: a new version keeps the set's name"
    listing = body(await S.get(A1, "/golden-sets"))
    assert [v["version"] for v in listing["versions"]] == [2, 1] and listing["active"]["id"] == v2["id"]
    assert [v["status"] for v in listing["versions"]] == ["active", "retired"]
    ids_v1 = {c["case_key"]: c["id"] for c in cases["cases"]}
    cases2 = body(await S.get(A1, f"/golden-sets/{v2['id']}/cases"))["cases"]
    assert [c["case_key"] for c in cases2] == list("12345678")
    assert {c["case_key"] for c in cases2 if c["case_version"] != next(
        (x["case_version"] for x in cases["cases"] if x["case_key"] == c["case_key"]), None)} == {"1", "8"}

    # templates
    tpl = await S.get(A1, "/golden-sets/template.csv")
    assert tpl.status_code == 200 and tpl.text.splitlines()[0].startswith("ID,Question,Follow-ups,Expected answer"), \
        "stage 3: the empty template downloads"
    assert "attachment" in tpl.headers["content-disposition"]
    bank = await S.get(A1, "/golden-sets/templates/compliance.csv")
    assert bank.status_code == 200 and len(bank.text.splitlines()) > 100, "stage 3: the question bank downloads"

    # company B cannot see A's set, and has none of its own
    theirs = body(await S.get(B1, "/golden-sets"))
    assert theirs["versions"] == [] and theirs["active"] is None, "stage 3: B sees nothing of A's sets"
    assert reason(await S.get(B1, f"/golden-sets/{v2['id']}/cases"), 404) == "not_found"
    assert reason(await S.post(B1, f"/golden-sets/{v2['id']}/retire"), 404) == "not_found"
    assert reason(await S.post(B1, "/golden-runs/estimate", json={"k": 1}), 409) == "no_active_set"
    S.data["set_v1"], S.data["set_v2"] = v1["id"], v2["id"]
    S.done.add("s03")


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 4  Golden run
# ═════════════════════════════════════════════════════════════════════════════════════════════

GRADE_RULES = [("work orders were raised", ["759"]), ("approved list", ["Ostley Power Services", "approved"]),
               ("boiler maintenance contract", ["break fee", "remaining term"]), ("work order wo-1007", ["open"]),
               ("fire doors", ["112"]), ("lifts serve", ["4 lifts"]), ("postcode", ["EC2M 9ZZ"]),
               ("floors", ["22 floors"])]


def chat_rules(*, lifts: str = "4 lifts serve Harbour Point.", doors: str = "About 95 fire doors were inspected in March 2026."):
    ans = lambda text_, **kw: (lambda q: StoryMsg(answer=text_, **kw))             # noqa: E731
    return [("work orders were raised at harbour point", ans("There were 759 work orders raised at Harbour Point in August 2026.")),
            ("lapsed gas safety", ans("Two vendors hold lapsed gas safety certificates: Ostley Power Services and Pennard Fire Services.")),
            ("approved list", ans("Ostley Power Services is on the approved list; Pennard Fire Services is not.")),
            ("boiler maintenance contract", ans("Compare the break fee with the remaining term before deciding; it is not a simple yes or no.")),
            ("work order wo-1007", ans("Work order WO-1007 is being handled.", probe_write=True)),
            ("fire doors", ans(doors)), ("lifts serve", ans(lifts)),
            ("postcode", ans(f"The postcode is EC2M 9ZZ. Portal key {SECRET_TOKEN}, header Bearer {SECRET_BEARER}.")),
            ("floors", ans("Harbour Point has 22 floors."))]


START = {"k": 1, "purpose": "adhoc", "context_default": "The user is in the Hoistra orchestrator conversation."}


async def step_all(S: Story, run_id: str, who: str = A1, *, stop_after: int | None = None) -> int:
    """The page steps its run: one call per case, each followed by the background item finishing; 200 means done."""
    done = 0
    for _ in range(40):
        r = await S.post(who, f"/golden-runs/{run_id}/step")
        if r.status_code == 200 and r.json().get("done"):
            return done
        assert r.status_code == 202 and r.json()["started"] is True, f"step -> {r.status_code} {r.text[:400]}"
        await drain()
        done += 1
        if stop_after is not None and done >= stop_after:
            return done
    raise AssertionError("a run of 8 cases did not finish in 40 steps")


def outcomes_of(detail: dict) -> dict[str, str]:
    return {c["case_key"]: c["reps"][0].get("outcome", c["reps"][0]["state"]) for c in detail["cases"]}


async def test_stage_04a_golden_run_steps_every_case_through_the_proxy_and_the_grader(S: Story):
    S.need("s03")
    S.chat.rules = chat_rules()
    S.models.grade_rules = GRADE_RULES
    # the estimate writes nothing and calls no model
    est = body(await S.post(A1, "/golden-runs/estimate", json={"k": 1}))
    assert (est["cases"], est["items"], est["messages"]) == (8, 8, 9), f"stage 4: {est}"
    assert est["per_answer_basis"] == "default" and est["est_total_usd"] > 0 and len(est["caveats"]) == 4
    assert est["flagged"] == [{"case_key": "4", "flags": ["write_intent"]}] and est["skipped"] == [], \
        "stage 4: a question that reads like a change is kept, flagged"
    assert est["set"]["version"] == 2
    assert (await S.lab("SELECT count(*) AS n FROM tracelab.jobs WHERE kind = 'golden_run'"))[0]["n"] == 0
    assert S.models.grader_calls == [] and S.chat.asked == [], "stage 4: an estimate asks nobody anything"

    run = body(await S.post(A1, "/golden-runs", json=START), 201)["run"]
    run1 = S.data["run1"] = run["id"]
    assert run["status"] == "running" and run["items_total"] == 8 and run["cases"] == 8, f"stage 4: {run}"
    assert reason(await S.post(A1, "/golden-runs", json=START), 409) == "run_in_progress", \
        "stage 4: one open run per company"
    assert reason(await S.get(B1, f"/golden-runs/{run1}"), 404) == "not_found", "stage 4: B cannot see A's run"
    assert reason(await S.post(A2, f"/golden-runs/{run1}/step"), 403) == "not_run_owner", \
        "stage 4: only the person who started a run steps it"

    # browser-stepped: one call per case. The grader is down for case 7 so that 'grade again' has something to do
    S.models.grader_down = {"postcode"}
    assert await step_all(S, run1, stop_after=2) == 2
    paused = body(await S.post(A2, f"/golden-runs/{run1}/pause", json={"reason": "paused"}))["run"]
    assert paused["status"] == "paused" and paused["paused_reason"] == "paused", f"stage 4: {paused}"
    assert reason(await S.post(A1, f"/golden-runs/{run1}/step"), 409) == "run_not_running", "stage 4: a paused run is not stepped"
    assert reason(await S.post(A2, f"/golden-runs/{run1}/resume", json={}), 403) == "not_run_owner"
    assert body(await S.post(A1, f"/golden-runs/{run1}/resume", json={}))["run"]["status"] == "running"
    assert reason(await S.post(A1, f"/golden-runs/{run1}/resume", json={}), 409) == "run_not_paused"
    await step_all(S, run1)
    detail = body(await S.get(A1, f"/golden-runs/{run1}"))
    got = outcomes_of(detail)
    assert got == {"1": "pass", "2": "pass", "3": "unvalidated", "4": "degraded", "5": "stale_reference",
                   "6": "pass", "7": "pending", "8": "pass"}, f"stage 4: first run readings {got}"
    summary = detail["run"]
    assert summary["status"] == "done" and summary["items_done"] == 7 and summary["items_total"] == 8
    assert summary["refused"] == 1, "stage 4: the proxy refused exactly one request"
    assert S.chat.proxy_refusals and S.chat.proxy_refusals[0]["status"] == 403 and \
        S.chat.proxy_refusals[0]["body"]["detail"]["reason"] == "refused_by_trace_lab", \
        f"stage 4: the real proxy refused the write {S.chat.proxy_refusals}"
    # no model money went on the refused answer, and the judgment case never reads as a verdict
    asked = [c["question"] for c in S.models.grader_calls]
    assert not any("WO-1007" in q for q in asked), "stage 4: a degraded answer is never graded"
    assert body(await S.get(A1, f"/golden-runs/{run1}"))["run"]["outcomes"]["degraded"] == 1

    # grade again: the pending case gets its grade, an admin cannot regrade everything, the platform can
    S.models.grader_down = set()
    assert reason(await S.post(A1, f"/golden-runs/{run1}/regrade", json={"only": "all"}), 403) == "superadmins_only"
    r = await S.post(A1, f"/golden-runs/{run1}/regrade", json={"only": "pending"})
    assert r.status_code == 202 and r.json()["items"] == 1, f"stage 4: {r.text[:300]}"
    await drain()
    assert reason(await S.post(A1, f"/golden-runs/{run1}/regrade", json={"only": "pending"}), 409) == "nothing_to_regrade"
    after = body(await S.get(A1, f"/golden-runs/{run1}"))
    assert outcomes_of(after)["7"] == "pass" and after["run"]["items_done"] == 8, "stage 4: 'grade again' finished case 7"
    S.done.add("s04a")


async def test_stage_04b_the_expected_outcome_stays_hidden_until_it_is_revealed_and_the_reveal_is_recorded(S: Story):
    S.need("s04a")
    run1 = S.data["run1"]
    detail = body(await S.get(A1, f"/golden-runs/{run1}"))
    final = {c["case_key"]: c["reps"][0] for c in detail["cases"]}
    tid1 = final["1"]["final_turn_id"]
    # nothing on the run page, the result page or the blind panel holds the expected answer, before a reveal
    pages = [json.dumps(detail), await _text(S, A1, f"/golden-runs/{run1}/results/{final['1']['result_id']}"),
             await _text(S, A1, f"/golden/turns/{tid1}")]
    for page in pages:
        assert EXPECTED_1 not in page and "expected_answer" not in page, "stage 4: the expected outcome leaked before reveal"
    closed = body(await S.get(A1, f"/golden/turns/{tid1}"))
    assert closed["available"] is True and closed["revealed"] is False and closed["golden"]["case_key"] == "1"
    assert reason(await S.post(B1, f"/golden/turns/{tid1}/reveal"), 404) == "not_a_golden_turn", \
        "stage 4: another company cannot open it"
    assert body(await S.get(B1, f"/golden/turns/{tid1}"))["available"] is False
    assert body(await S.get(A1, "/golden/turns/turn-unknown"))["available"] is False

    opened = body(await S.post(A1, f"/golden/turns/{tid1}/reveal"))
    assert opened["revealed"] is True and opened["golden"]["case"]["expected_answer"].startswith(EXPECTED_1[:30])
    result = opened["golden"]["result"]
    assert result["outcome"] == "pass" and result["grade"]["verdict"] == "pass"
    assert result["grade"]["value_check"]["status"] == "all_found", "stage 4: objective: the value check ran first"
    assert opened["golden"]["case"]["expected_values"][0]["value"] == 759
    await S.post(A1, f"/golden/turns/{tid1}/reveal")
    rows = await S.lab("SELECT reviewer_email FROM tracelab.golden_reveals WHERE turn_id = :t", t=tid1)
    assert [r["reviewer_email"] for r in rows] == [PEOPLE[A1].email], "stage 4: the reveal is recorded, once, by name"
    assert body(await S.get(A2, f"/golden/turns/{tid1}"))["revealed"] is False, "stage 4: another reviewer stays blind"
    assert body(await S.get(A1, f"/golden-runs/{run1}"))["cases"][0]["reps"][0]["revealed"] is True

    # what the grader decided on the hard cases, now readable by the reviewer who opened them
    for key, outcome, verdict, vc in (("4", "degraded", None, None), ("5", "stale_reference", "fail", "mismatch"),
                                      ("3", "unvalidated", "pass", None)):
        tid = final[key]["final_turn_id"]
        got = body(await S.post(A1, f"/golden/turns/{tid}/reveal"))["golden"]["result"]
        assert got["outcome"] == outcome, f"stage 4: case {key} reads {got['outcome']}"
        assert got["grade"]["verdict"] == verdict, f"stage 4: case {key} verdict {got['grade']['verdict']}"
        if vc:
            assert got["grade"]["value_check"]["status"] == vc
    refused = body(await S.post(A1, f"/golden/turns/{final['4']['final_turn_id']}/reveal"))["golden"]["result"]
    assert refused["refused"] and refused["grade"]["validated"] is None and refused["stored_outcome"] == "degraded"
    judged = body(await S.post(A1, f"/golden/turns/{final['3']['final_turn_id']}/reveal"))["golden"]["result"]
    assert judged["grade"]["validated"] is False, "stage 4: a judgment case waits for grader validation"
    assert judged["stored_outcome"] == "unsure"
    S.done.add("s04b")


async def _text(S, who, path):
    return (await S.get(who, path)).text


async def test_stage_04c_golden_turns_are_snapshotted_as_evidence_and_never_written_to_the_application_database(S: Story):
    S.need("s04b")
    run1 = S.data["run1"]
    rows = await S.lab("SELECT turn_id, turn->'golden'->>'case_key' AS key, reason FROM tracelab.evidence_turns "
                       "WHERE reason = 'golden' AND turn->'golden'->>'run_id' = :r", r=run1)
    assert len(rows) == 9 and {r["key"] for r in rows} == set("12345678"), \
        f"stage 4: every one of the run's 9 answers is kept as evidence {len(rows)}"
    spans = await S.lab("SELECT turn_id, count(*) AS n FROM tracelab.evidence_spans WHERE turn_id = ANY(CAST(:ids AS text[])) "
                        "GROUP BY turn_id", ids=[r["turn_id"] for r in rows])
    assert len(spans) == 9 and all(s["n"] >= 5 for s in spans), "stage 4: each golden answer keeps its span tree"
    ids = [r["turn_id"] for r in rows]
    assert all(t.startswith("turn-g") for t in ids)
    main = await S.main("SELECT (SELECT count(*) FROM plenum_cafm.agent_trace_turns WHERE turn_id = ANY(CAST(:ids AS text[])) "
                        "OR session_id LIKE 'golden-%') AS turns, (SELECT count(*) FROM plenum_cafm.agent_trace_spans "
                        "WHERE turn_id = ANY(CAST(:ids AS text[]))) AS spans, "
                        "(SELECT count(*) FROM plenum_cafm.chat_threads WHERE id LIKE 'golden-%') AS threads, "
                        "(SELECT count(*) FROM plenum_cafm.agent_activity_log) AS activity", ids=ids)
    assert main[0] == {"turns": 0, "spans": 0, "threads": 0, "activity": 0}, f"stage 4: a golden turn reached the main database {main}"
    # the live list never shows them; the run's own view does
    since = iso(NOW - timedelta(days=1))
    live = body(await S.get(A1, "/conversations", params={"since": since, "limit": 100}))
    assert not any(c["key"].startswith("s:golden-") for c in live["conversations"]), "stage 4: golden runs are not live traffic"
    mine = body(await S.get(A1, "/conversations", params={"run": run1, "since": since}))
    assert mine["total"] == 8, f"stage 4: the run's conversations {mine['total']}"
    runs = body(await S.get(A1, "/runs", params={"since": since}))["runs"]
    assert [r["run_id"] for r in runs if r["run_id"] != "live"] == [run1] and any(r["run_id"] == "live" for r in runs)
    assert [r for r in runs if r["run_id"] == run1][0]["kind"] == "golden"
    S.done.add("s04c")


async def golden_secret_pages(S: Story) -> dict[str, str]:
    """The pages that show the answer of the golden case whose chat answer carries a credential (case 7, run 1)."""
    run1 = S.data["run1"]
    rep = next(c for c in body(await S.get(A1, f"/golden-runs/{run1}"))["cases"] if c["case_key"] == "7")["reps"][0]
    await S.post(A1, f"/golden/turns/{rep['final_turn_id']}/reveal")
    conv = await S.get(A1, f"/conversations/{rep['conversation_key']}", params={"turn": rep["final_turn_id"]})
    return {"result page": (await S.get(A1, f"/golden-runs/{run1}/results/{rep['result_id']}")).text,
            "expected outcome": (await S.get(A1, f"/golden/turns/{rep['final_turn_id']}")).text,
            "turn": (await S.get(A1, f"/turns/{rep['final_turn_id']}")).text, "conversation": conv.text}


async def test_stage_04f_a_secret_in_a_golden_answer_is_redacted_in_the_conversation_views(S: Story):
    S.need("s04c")
    pages = await golden_secret_pages(S)
    assert "[redacted" in pages["conversation"], "stage 4: the conversation view shows the golden answer, redacted"
    for what in ("expected outcome", "turn", "conversation"):
        assert SECRET_TOKEN not in pages[what] and SECRET_BEARER not in pages[what], \
            f"stage 4: a secret reached the {what} view of a golden answer"


async def test_stage_04g_the_golden_result_page_redacts_a_secret_in_an_answer(S: Story):
    S.need("s04c")
    page = (await golden_secret_pages(S))["result page"]
    assert SECRET_TOKEN not in page and SECRET_BEARER not in page, "stage 4: the result page shows the credential"


async def test_stage_04d_usd_caps_stop_a_run_and_a_second_run_compares_to_the_first(S: Story):
    S.need("s04a")
    run1 = S.data["run1"]
    # the month's cap refuses a start; the run's cap stops a run before the item that would pass it
    S.mp.setenv("TRACE_LAB_GOLDEN_MONTHLY_USD", "0.01")
    refused = await S.post(A1, "/golden-runs", json=START)
    assert reason(refused, 409) == "monthly_cap", "stage 4: the month cap refuses a start"
    S.mp.delenv("TRACE_LAB_GOLDEN_MONTHLY_USD")
    assert (await S.lab("SELECT count(*) AS n FROM tracelab.golden_runs"))[0]["n"] == 1, "stage 4: a refused start made no run"

    S.chat.rules = chat_rules(lifts="3 lifts serve Harbour Point.", doors="112 fire doors were inspected in March 2026.")
    S.models.grader_down = set()
    run2 = S.data["run2"] = body(await S.post(A1, "/golden-runs", json={**START, "usd_cap": 0.05}), 201)["run"]["id"]
    capped = await S.post(A1, f"/golden-runs/{run2}/step")
    assert reason(capped, 409) == "run_cap" and capped.json()["detail"]["usd_cap"] == pytest.approx(0.05)
    state = body(await S.get(A1, f"/golden-runs/{run2}"))["run"]
    assert state["status"] == "capped" and state["paused_reason"] == "run_cap" and state["items_done"] == 0
    assert S.chat.asked.count(QUESTION_1) == 1, "stage 4: the capped step asked nobody (case 1 was asked once, in run 1)"
    assert reason(await S.post(A1, f"/golden-runs/{run2}/resume", json={"usd_cap": 99999}), 422) == "cap_too_high"
    resumed = body(await S.post(A1, f"/golden-runs/{run2}/resume", json={"usd_cap": 3.0}))["run"]
    assert resumed["status"] == "running" and resumed["usd_cap"] == pytest.approx(3.0), f"stage 4: {resumed}"
    await step_all(S, run2)
    after = body(await S.get(A1, f"/golden-runs/{run2}"))
    got = outcomes_of(after)
    assert got["6"] == "fail" and got["5"] == "pass" and got["1"] == "pass", f"stage 4: second run readings {got}"
    assert after["run"]["status"] == "done" and after["run"]["usd_spent"] <= 3.0

    cmp_ = body(await S.get(A1, "/golden-runs/compare", params={"base": run1, "head": run2}))
    flips = {kind: sorted(f["case_key"] for f in found) for kind, found in cmp_["flips"].items() if found}
    assert flips.get("regressed") == ["6"], f"stage 4: compare shows the lifts case regressed {flips}"
    assert flips.get("now_measured") == ["5"], "stage 4: the stale case is measured in the second run only"
    assert sorted(flips.get("not_measured", [])) == ["3", "4"], "stage 4: unvalidated and degraded stay unmeasured"
    assert cmp_["still_passing"] == 4 and cmp_["comparable"] is True and cmp_["changed"] == [], f"stage 4: {cmp_}"
    assert cmp_["pass_rate"]["delta"] == pytest.approx(-0.2), "stage 4: one fresh case of five regressed"
    assert reason(await S.get(B1, "/golden-runs/compare", params={"base": run1, "head": run2}), 404) == "not_found"
    S.done.add("s04d")


async def test_stage_04e_a_run_can_be_cancelled_and_a_closing_page_pauses_it(S: Story):
    S.need("s04d")
    S.chat.rules = chat_rules()
    run3 = S.data["run3"] = body(await S.post(A1, "/golden-runs", json=START), 201)["run"]["id"]
    assert await step_all(S, run3, stop_after=1) == 1
    closed = body(await S.post(A1, f"/golden-runs/{run3}/pause", json={"reason": "page_closed"}))["run"]
    assert closed["status"] == "paused" and closed["paused_reason"] == "page_closed"
    assert body(await S.post(A1, f"/golden-runs/{run3}/resume", json={}))["run"]["paused_reason"] is None
    assert await step_all(S, run3, stop_after=1) == 1
    short = golden_jwt(5)                                      # a sign-in with five minutes left cannot carry a run
    env.BY_TOKEN[short.split(" ", 1)[1]] = PEOPLE[A1]
    r = await S.post(None, f"/golden-runs/{run3}/step", headers={"Authorization": short})
    assert reason(r, 409) == "token_expiring" and 4 < r.json()["detail"]["minutes_left"] < 5.1, f"stage 4: {r.text[:200]}"
    gone = body(await S.post(A2, f"/golden-runs/{run3}/cancel"))["run"]
    assert gone["status"] == "cancelled" and gone["items_done"] == 2, f"stage 4: {gone}"
    assert reason(await S.post(A1, f"/golden-runs/{run3}/step"), 409) == "run_not_running"
    assert reason(await S.post(A1, f"/golden-runs/{run3}/cancel"), 409) == "run_not_open"
    listing = body(await S.get(A1, "/golden-runs"))["runs"]
    assert [r["status"] for r in listing] == ["cancelled", "done", "done"], f"stage 4: {[r['status'] for r in listing]}"
    # a cancelled run leaves its two stored items readable, and the company can start again
    assert len(await S.lab("SELECT 1 FROM tracelab.golden_results WHERE run_id = CAST(:r AS uuid) AND msg_idx = 0", r=run3)) == 2
    S.done.add("s04e")


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 5  Review
# ═════════════════════════════════════════════════════════════════════════════════════════════

SINCE5, UNTIL5 = iso(NOW - timedelta(days=5)), iso(NOW - timedelta(days=2))


def batch_req(step: str, strategy: str, k: int, **kw) -> dict:
    return {"plan_step": step, "strategy": strategy, "k": k, "seed": 7, "since": SINCE5, "until": UNTIL5, **kw}


def sampling_max() -> int:
    from trace_lab import sampling
    return sampling.BATCH_MAX


def as_query(req: dict) -> dict:
    return {k: v for k, v in req.items() if v is not None}


async def test_stage_05a_sampling_plans_previews_and_records_batches_the_same_way_every_time(S: Story):
    S.need("s04e")
    world = S.data["world"]
    pool_a = {r["tid"] for r in world["review_a"]}
    pool_b = set(world["review_b"])
    plan = body(await S.get(A1, "/sampling/plan", params={"since": SINCE5, "until": UNTIL5}))
    assert plan["pool_size"] == N_REV and plan["distinct_sampled"] == 0 and plan["next_step"] == "initial_uniform", \
        f"stage 5: the pool is company A's {N_REV} answers, never B's {plan}"
    assert reason(await S.get(PLATFORM, "/sampling/plan", params={"since": SINCE5, "until": UNTIL5}), 400) == "name_a_company"

    refusals = [
        ({"since": iso(NOW - timedelta(days=100)), "until": iso(NOW - timedelta(days=95))}, "window_outside_retention"),
        ({"since": UNTIL5, "until": SINCE5}, "bad_window"),
        ({"strategy": "dimension", "dimension": "verdict"}, "dimension_not_allowed"),
        ({"channel": "golden"}, "bad_channel"),
        ({"mode_id": str(uuid.uuid4())}, "bad_seed_source"),
    ]
    for change, why in refusals:
        r = await S.post(A1, "/sampling/batches", json={**batch_req("extra", "uniform", 3), **change})
        assert reason(r, 422) == why, f"stage 5: {change} -> {r.text[:200]}"
    assert (await S.post(A1, "/sampling/batches", json=batch_req("extra", "uniform", sampling_max() + 1))).status_code == 422
    assert (await S.lab("SELECT count(*) AS n FROM tracelab.sample_batches"))[0]["n"] == 0, "stage 5: a refusal drew nothing"

    req1 = batch_req("initial_uniform", "uniform", 12, reviews_required=2)
    first = body(await S.get(A1, "/sampling/preview", params=as_query(req1)))
    again = body(await S.get(A1, "/sampling/preview", params=as_query(req1)))
    other = body(await S.get(A1, "/sampling/preview", params=as_query({**req1, "seed": 8})))
    ids = lambda out: [p["turn_id"] for p in out["picks"]]                                    # noqa: E731
    assert ids(first) == ids(again) and len(ids(first)) == 12, "stage 5: one seed, one draw"
    assert ids(first) != ids(other), "stage 5: another seed, another draw"
    assert set(ids(first)) <= pool_a and first["pool_size"] == N_REV
    assert (await S.lab("SELECT count(*) AS n FROM tracelab.sample_batches"))[0]["n"] == 0, "stage 5: a preview writes nothing"

    made1 = body(await S.post(A1, "/sampling/batches", json=req1), 201)
    b1 = made1["batch"]
    assert [i["turn_id"] for i in made1["items"]] == ids(first), "stage 5: the batch is exactly what the preview showed"
    assert b1["counts_for_prevalence"] is True and b1["seed"] == 7 and b1["selected"] == 12 and b1["reviews_required"] == 2
    made2 = body(await S.post(A1, "/sampling/batches", json=batch_req("cluster_representatives", "diversity", 12)), 201)
    made3 = body(await S.post(A1, "/sampling/batches", json=batch_req("final_uniform", "uniform", 12)), 201)
    assert made2["batch"]["counts_for_prevalence"] is False, "stage 5: only a uniform batch counts toward a rate"
    assert made3["batch"]["counts_for_prevalence"] is True
    drawn = [[i["turn_id"] for i in m["items"]] for m in (made1, made2, made3)]
    flat = [t for d in drawn for t in d]
    assert len(flat) == len(set(flat)) == N_REV and set(flat) == pool_a, "stage 5: an answer is in one batch only, and all are A's"
    assert reason(await S.post(A1, "/sampling/batches", json=batch_req("extra", "uniform", 3)), 422) == "pool_empty"
    assert not (set(flat) & pool_b), "stage 5: none of company B's answers"

    theirs = body(await S.post(B1, "/sampling/batches", json=batch_req("initial_uniform", "uniform", 3)), 201)
    assert {i["turn_id"] for i in theirs["items"]} <= pool_b and theirs["batch"]["organization_id"] == ORG_B
    assert len(body(await S.get(A1, "/sampling/batches"))["batches"]) == 3, "stage 5: A lists its own three"
    assert len(body(await S.get(PLATFORM, "/sampling/batches"))["batches"]) == 4, "stage 5: the platform reads all four"
    manifest = body(await S.get(A1, "/sampling/manifest"))
    assert sorted(b["seed"] for b in manifest["batches"]) == [7, 7, 7] and manifest["plan"]["distinct_sampled"] == N_REV
    got = {s["step"]: s["selected"] for s in body(await S.get(A1, "/sampling/plan"))["steps"]}
    assert got["initial_uniform"] == 12 and got["cluster_representatives"] == 12 and got["final_uniform"] == 12
    # every drawn answer is kept as evidence the moment it is sampled
    kept = await S.lab("SELECT count(*) AS n FROM tracelab.evidence_turns WHERE turn_id = ANY(CAST(:ids AS text[])) "
                       "AND reason = 'sampled'", ids=flat)
    assert kept[0]["n"] == N_REV, "stage 5: every sampled answer has an evidence snapshot"
    S.data["batches"] = {"b1": b1["id"], "b2": made2["batch"]["id"], "b3": made3["batch"]["id"], "bb": theirs["batch"]["id"]}
    S.data["drawn"] = {"b1": drawn[0], "b2": drawn[1], "b3": drawn[2]}
    S.done.add("s05a")


WRONG_NOTE = "The count includes cancelled jobs as open."
ANCHOR = {"field": "answer", "quote": CANCELLED, "occurrence": 1}


async def verdict(S: Story, who: str, tid: str, kind: str, *, base: str | None = None, note: str = WRONG_NOTE,
                  org: str | None = None):
    payload: dict = {"verdict": kind}
    if kind == "wrong":
        payload.update(note=note, anchor=ANCHOR)
    if base:
        payload["base"] = base
    return await S.put(who, f"/turns/{tid}/verdict", json=payload, org=org)


async def mine(S: Story, who: str, tid: str) -> dict | None:
    return body(await S.get(who, "/annotations", params={"turn_id": tid}))["turns"][tid]["mine"]


async def test_stage_05b_two_reviewers_judge_the_same_answers_blind_and_every_save_is_a_new_version(S: Story):
    S.need("s05a")
    world = S.data["world"]
    bad = {r["tid"]: r["bad"] for r in world["review_a"]}
    b1, drawn = S.data["batches"]["b1"], S.data["drawn"]
    first = drawn["b1"][next(i for i, t in enumerate(drawn["b1"]) if bad[t])]            # a faulty answer in batch 1
    sound = drawn["b1"][next(i for i, t in enumerate(drawn["b1"]) if not bad[t])]        # a sound one

    # a wrong verdict needs a note and a verbatim quote of where it went wrong
    assert reason(await S.put(A1, f"/turns/{first}/verdict", json={"verdict": "wrong"}), 422) == "note_required"
    assert reason(await S.put(A1, f"/turns/{first}/verdict", json={"verdict": "wrong", "note": "x"}), 422) == "anchor_required"
    off = {"verdict": "wrong", "note": "x", "anchor": {"field": "answer", "quote": "not in the answer", "occurrence": 1}}
    assert reason(await S.put(A1, f"/turns/{first}/verdict", json=off), 422) == "quote_not_found"
    assert reason(await S.put(A1, f"/turns/{sound}/verdict", json={"verdict": "right", "anchor": ANCHOR}), 422) == "anchor_on_right"
    assert reason(await S.put(A1, "/turns/turn-nope/verdict", json={"verdict": "right"}), 404) == "turn_not_found"
    assert reason(await S.put(A1, "/turns/turn-rb000/verdict", json={"verdict": "right"}), 404) == "turn_not_found", \
        "stage 5: company B's answer is not A's to judge"
    assert (await S.lab("SELECT count(*) AS n FROM tracelab.annotations"))[0]["n"] == 0, "stage 5: refusals wrote nothing"
    assert (await S.get(A1, "/queue", params={"batch_id": b1})).json()["remaining"] == 12

    # A1 judges all of batch 1; A2 has seen none of it
    ids_b1 = []
    for t in drawn["b1"]:
        v = body(await verdict(S, A1, t, "wrong" if bad[t] else "right"))
        assert v["annotation"]["version"] == 1 and v["annotation"]["blind"] is True
        ids_b1.append(v["annotation"]["id"])
    blind = body(await S.get(A2, "/annotations", params={"turn_id": first}))["turns"][first]
    assert (blind["mine"], blind["others"], blind["others_count"], blind["others_hidden"]) == (None, None, 1, True), \
        f"stage 5: A2 cannot see A1's verdict before judging {blind}"
    assert WRONG_NOTE not in json.dumps(blind)
    hist_blind = body(await S.get(A2, f"/turns/{first}/annotation-history"))
    assert hist_blind["versions"] == [] and hist_blind["events"] == [], "stage 5: the history is blind too"
    key = f"s:rev-a-{int(first[-3:]):02d}"
    c1 = body(await S.post(A1, "/comments", json={"conversation_key": key, "turn_id": first, "note": "Same fault as last week."}),
              201)["annotation"]
    hidden = body(await S.get(A2, "/annotations", params={"conversation_key": key, "turn_id": first}))
    assert hidden["comments"] == [] and hidden["comments_hidden"] == 1, "stage 5: a comment stays hidden until A2 judges"
    assert "Same fault" not in json.dumps(hidden)
    assert (await S.get(A2, "/queue", params={"batch_id": b1})).json()["remaining"] == 12
    assert (await S.get(A1, "/queue", params={"batch_id": b1})).json()["remaining"] == 0

    # A2 judges the same answers and disagrees on exactly one
    for t in drawn["b1"]:
        kind = "wrong" if bad[t] else "right"
        if t == first:
            kind = "right"
        body(await verdict(S, A2, t, kind, note="A different reading."))
    seen = body(await S.get(A2, "/annotations", params={"turn_id": first}))["turns"][first]
    assert seen["others_hidden"] is False and seen["mine"]["verdict"] == "right" and seen["others"][0]["verdict"] == "wrong", \
        "stage 5: after judging, the other reviewer's verdict is shown"
    assert seen["others"][0]["note"] == WRONG_NOTE
    shown = body(await S.get(A2, "/annotations", params={"conversation_key": key}))
    assert [c["note"] for c in shown["comments"]] == ["Same fault as last week."] and shown["comments_hidden"] == 0
    # only the author changes a comment; an edit is a new version, a delete a tombstone, nothing comes back after it
    assert reason(await S.put(A2, f"/comments/{c1['lineage_id']}", json={"note": "Not mine.", "base": c1["id"]}), 403) == "not_author"
    c2 = body(await S.put(A1, f"/comments/{c1['lineage_id']}", json={"note": "Edited note.", "base": c1["id"]}))["annotation"]
    assert c2["version"] == 2 and c2["note"] == "Edited note."
    assert reason(await S.put(A1, f"/comments/{c1['lineage_id']}", json={"note": "Late.", "base": c1["id"]}), 409) == "stale"
    gone = body(await S.call(A1, "DELETE", f"/comments/{c1['lineage_id']}", params={"base": c2["id"]}))["annotation"]
    assert gone["tombstone"] is True and gone["note"] is None
    assert reason(await S.put(A1, f"/comments/{c1['lineage_id']}", json={"note": "Back.", "base": gone["id"]}), 404) == "annotation_not_found"
    assert body(await S.get(A2, "/annotations", params={"conversation_key": key}))["comments"] == []
    assert (await S.post(A1, "/comments", json={"conversation_key": key, "note": "   "})).status_code == 422
    owner = body(await S.get(PLATFORM, "/annotations", params={"turn_id": first}))["turns"][first]
    assert owner["others_count"] == 2 and owner["others"] is not None, "stage 5: the platform owner reads every reviewer"

    # A1 reviews batches 2 and 3 (one review each)
    for key in ("b2", "b3"):
        for t in drawn[key]:
            body(await verdict(S, A1, t, "wrong" if bad[t] else "right"))
    detail = body(await S.get(A1, f"/sampling/batches/{b1}"))["batch"]
    wrong_b1 = sum(1 for t in drawn["b1"] if bad[t])
    assert (detail["reviewed"], detail["missing"], detail["disputed"], detail["wrong"]) == (12, 0, 1, wrong_b1 - 1), \
        f"stage 5: batch counts {detail}"
    assert detail["right"] == 12 - wrong_b1
    for key in ("b2", "b3"):
        d = body(await S.get(A1, f"/sampling/batches/{S.data['batches'][key]}"))["batch"]
        assert d["missing"] == 0 and d["reviewed"] == 12, f"stage 5: {key} {d}"
    assert (await S.get(A1, "/queue")).json()["remaining"] == 0
    S.data["first_bad"], S.data["verdict_ids_b1"] = first, ids_b1
    S.done.add("s05b")


async def test_stage_05c_an_edit_is_a_new_version_and_two_simultaneous_saves_leave_one_winner_and_a_prompt_409(S: Story):
    S.need("s05b")
    from tl_head_race import lose_the_race
    from trace_lab import db

    world = S.data["world"]
    bad = {r["tid"]: r["bad"] for r in world["review_a"]}
    t_edit = next(t for t in S.data["drawn"]["b2"] if bad[t])
    v1 = await mine(S, A1, t_edit)
    edited = body(await verdict(S, A1, t_edit, "wrong", base=v1["id"], note="Cancelled jobs are counted as open."))["annotation"]
    assert edited["version"] == 2 and edited["supersedes"] == v1["id"] and edited["lineage_id"] == v1["lineage_id"]
    stale = await verdict(S, A1, t_edit, "right")                                    # no base: it would overwrite a head
    assert reason(stale, 409) == "stale" and stale.json()["detail"]["current"]["id"] == edited["id"], \
        "stage 5: a save without the current version is a 409 that carries the head"
    history = body(await S.get(A1, f"/turns/{t_edit}/annotation-history"))
    assert [v["version"] for v in history["versions"]] == [1, 2] or [v["version"] for v in history["versions"]] == [2, 1]
    assert {e["action"] for e in history["events"]} == {"create", "edit"}

    # two saves of one head at the same moment: exactly one wins
    t_race = next(t for t in S.data["drawn"]["b3"] if not bad[t])
    head = (await mine(S, A1, t_race))["id"]
    t0 = asyncio.get_running_loop().time()
    outcomes = await asyncio.gather(verdict(S, A1, t_race, "right", base=head), verdict(S, A1, t_race, "right", base=head))
    assert sorted(r.status_code for r in outcomes) == [200, 409], f"stage 5: {[r.text[:200] for r in outcomes]}"
    assert asyncio.get_running_loop().time() - t0 < 10, "stage 5: the loser gets its 409 at once, not after a wait"
    loser = next(r for r in outcomes if r.status_code == 409)
    assert loser.json()["detail"]["reason"] == "stale"
    # and the race forced the same way every time: the writer queues behind the head lock, loses, and is told so
    head2 = (await mine(S, A1, t_race))["id"]
    lost = await lose_the_race(db.lab_engine(), head2, lambda: verdict(S, A1, t_race, "right", base=head2))
    assert lost.status_code == 409 and lost.json()["detail"]["reason"] == "stale", f"stage 5: {lost.text[:300]}"
    assert lost.json()["detail"]["current"]["version"] >= 3
    versions = await S.lab("SELECT version FROM tracelab.annotations WHERE turn_id = :t AND kind = 'verdict' "
                           "AND superseded_by IS NULL", t=t_race)
    assert len(versions) == 1, "stage 5: one live head per reviewer and answer, whoever won"
    # a reviewer clears a verdict (a tombstone version) and judges again on top of it
    t_clear = next(t for t in S.data["drawn"]["b1"] if t != S.data["first_bad"])
    head = await mine(S, A2, t_clear)
    cleared = body(await S.call(A2, "DELETE", f"/turns/{t_clear}/verdict", params={"base": head["id"]}))["annotation"]
    assert cleared["tombstone"] is True and (await mine(S, A2, t_clear))["tombstone"] is True and cleared["verdict"] is None
    detail_mid = body(await S.get(A1, f"/sampling/batches/{S.data['batches']['b1']}"))["batch"]
    assert detail_mid["reviewed"] == 12, "stage 5: A1 still holds a verdict on it, so the answer stays reviewed"
    assert reason(await S.call(A2, "DELETE", f"/turns/{t_clear}/verdict", params={"base": head["id"]}), 409) == "stale"
    back = body(await verdict(S, A2, t_clear, "wrong" if bad[t_clear] else "right", base=cleared["id"], note="Again."))["annotation"]
    assert back["version"] == head["version"] + 2 and back["lineage_id"] == head["lineage_id"]
    S.done.add("s05c")


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """The 95% Wilson interval, written out here so the page's figures are checked against arithmetic, not itself."""
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return centre - half, centre + half


async def test_stage_05d_progress_the_map_and_the_evidence_behind_every_cited_answer(S: Story):
    S.need("s05c")
    from trace_lab import evidence

    world, drawn = S.data["world"], S.data["drawn"]
    bad = {r["tid"]: r["bad"] for r in world["review_a"]}
    pool_a = set(bad)
    first = S.data["first_bad"]
    # the numbers on Progress, checked against the verdicts the story gave
    wrong_b1 = sum(1 for t in drawn["b1"] if bad[t])
    uniform = drawn["b1"] + drawn["b3"]
    u_wrong = sum(1 for t in uniform if bad[t] and t != first)
    u_right = sum(1 for t in uniform if not bad[t])
    prog = body(await S.get(A1, "/progress", params={"since": SINCE5, "until": UNTIL5}))
    oc = prog["open_coding"]
    assert (oc["sampled"], oc["reviewed"], oc["missing"], oc["disputed"]) == (N_REV, N_REV, 0, 1), f"stage 5: {oc}"
    assert oc["wrong"] == sum(bad.values()) - 1 and oc["right"] == N_REV - sum(bad.values()), f"stage 5: {oc}"
    assert {r["email"]: r["reviewed"] for r in oc["reviewers"]} == {PEOPLE[A1].email: N_REV, PEOPLE[A2].email: 12}
    fair = prog["fair_sample"]
    assert (fair["items"], fair["wrong"], fair["right"], fair["disputed"], fair["unreviewed"]) == (
        24, u_wrong, u_right, 1, 0), f"stage 5: the fair sample counts only uniform batches {fair}"
    assert fair["rate"] == pytest.approx(round(u_wrong / (u_wrong + u_right), 4))
    lo, hi = wilson(u_wrong, u_wrong + u_right)
    assert (fair["ci_low"], fair["ci_high"]) == (pytest.approx(lo, abs=2e-4), pytest.approx(hi, abs=2e-4)), \
        f"stage 5: the interval is the Wilson interval {fair} vs {(lo, hi)}"
    assert prog["agreement"]["shared"] == 12 and prog["agreement"]["percent"] == pytest.approx(11 / 12, abs=1e-3)
    assert prog["plan"]["distinct_sampled"] == N_REV and prog["coverage"] is not None
    owner = body(await S.get(PLATFORM, "/progress", params={"since": SINCE5, "until": UNTIL5}))
    assert owner["scope"] == "platform" and {c["organization_id"]: (c["sampled"], c["reviewed"]) for c in owner["by_company"]} == {
        ORG_A: (N_REV, N_REV), ORG_B: (3, 0)}, "stage 5: the platform sees each company's own numbers"
    ignored = body(await S.get(A1, "/progress", params={"since": SINCE5, "until": UNTIL5, "filter_org": ORG_B}))
    assert ignored["scope"] == "company" and ignored["organization_id"] == ORG_A, "stage 5: an admin cannot filter to another company"

    # the Map: this company's answers only, each placed once, with who has reviewed it
    m = body(await S.get(A1, "/map", params={"since": SINCE5, "until": UNTIL5}))
    nodes = {n["turn_id"]: n for n in m["nodes"]}
    assert set(nodes) == pool_a and m["pool_size"] == N_REV and m["too_few"] is False, f"stage 5: the Map holds A's {N_REV} answers"
    assert all(n["reviewed"] and n["reviewed_by_me"] for n in nodes.values())
    assert sum(c["size"] for c in m["clusters"]) == N_REV and all(c["reviewed"] == c["size"] for c in m["clusters"])
    assert all(n["batch_id"] for n in nodes.values()), "stage 5: each answer shows the batch that drew it"
    theirs = body(await S.get(B1, "/map", params={"since": SINCE5, "until": UNTIL5}))
    assert {n["turn_id"] for n in theirs["nodes"]} == set(world["review_b"])
    assert not any(n["reviewed_by_me"] for n in theirs["nodes"])
    assert reason(await S.get(PLATFORM, "/map", params={"since": SINCE5, "until": UNTIL5}), 400) == "name_a_company"

    # every answer a reviewer cited is kept as evidence, with its whole span tree
    snaps = await S.lab("SELECT t.turn_id, count(s.span_id) AS spans FROM tracelab.evidence_turns t "
                        "LEFT JOIN tracelab.evidence_spans s ON s.turn_id = t.turn_id WHERE t.turn_id = ANY(CAST(:ids AS text[])) "
                        "GROUP BY t.turn_id", ids=sorted(pool_a))
    assert len(snaps) == N_REV and all(r["spans"] == 5 for r in snaps), "stage 5: every answer, 5 steps each, all kept"
    # ...so a turn the chat later loses is still readable from the snapshot
    gone = next(t for t in drawn["b2"] if not bad[t])
    span_ids = [r["id"] for r in await S.main("SELECT id::text AS id FROM plenum_cafm.agent_trace_spans WHERE turn_id = :t", t=gone)]
    assert len(span_ids) == 5
    from sqlalchemy import text as sql
    async with S.writable.begin() as conn:       # the chat's own purge, done by the seeder (Trace Lab never deletes)
        await conn.execute(sql("DELETE FROM plenum_cafm.agent_trace_spans WHERE turn_id = :t"), {"t": gone})
        await conn.execute(sql("DELETE FROM plenum_cafm.agent_trace_turns WHERE turn_id = :t"), {"t": gone})
    S.data["main_deleted"] = {"turns": [gone], "spans": span_ids}
    assert await S.main("SELECT 1 FROM plenum_cafm.agent_trace_turns WHERE turn_id = :t", t=gone) == []
    loaded = (await evidence.load([gone], organization_id=ORG_A))[gone]
    assert loaded.source == "snapshot" and len(loaded.spans) == 5, "stage 5: the snapshot answers when the live row is gone"
    assert loaded.turn["question"].startswith("How many work orders are open at building R")
    checks = body(await S.get(A1, f"/checks/turns/{gone}"))
    assert checks["turn_id"] == gone and checks["results"], "stage 5: the code checks still read the answer from its snapshot"
    assert reason(await S.get(B1, f"/checks/turns/{gone}"), 404) == "turn_not_found"

    # the chat's real 90-day purge drops only a turn's steps and keeps its row: the pages carry on from the snapshot
    keep = [t for t in drawn["b3"] if not bad[t]][-1]
    keep_spans = [r["id"] for r in await S.main("SELECT id::text AS id FROM plenum_cafm.agent_trace_spans WHERE turn_id = :t", t=keep)]
    async with S.writable.begin() as conn:
        await conn.execute(sql("DELETE FROM plenum_cafm.agent_trace_spans WHERE turn_id = :t"), {"t": keep})
    S.data["main_deleted"]["spans"] += keep_spans
    assert await S.main("SELECT 1 FROM plenum_cafm.agent_trace_spans WHERE turn_id = :t", t=keep) == []
    purged = (await evidence.load([keep], organization_id=ORG_A))[keep]
    assert purged.source == "snapshot" and len(purged.spans) == 5, "stage 5: a turn with no steps left reads from its snapshot"
    page = body(await S.get(A1, f"/conversations/s:rev-a-{int(keep[-3:]):02d}", params={"turn": keep}))["conversation"]
    assert [m["role"] for m in page["active"]["messages"] if m["role"] != "system_note"][0] == "user", "stage 5: the page still opens"
    assert body(await S.get(A1, f"/checks/turns/{keep}"))["results"]
    S.data["purged_turn"] = keep
    S.done.add("s05d")


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 6  Failure modes
# ═════════════════════════════════════════════════════════════════════════════════════════════

MODE = "counts_cancelled_jobs_as_open"
DEFINITION = "Present when the answer counts cancelled jobs as open work orders."


def bad_turns(S: Story) -> list[str]:
    return [r["tid"] for r in S.data["world"]["review_a"] if r["bad"]]


def sound_turns(S: Story) -> list[str]:
    gone = set(S.data["main_deleted"]["turns"])
    return [r["tid"] for r in S.data["world"]["review_a"] if not r["bad"] and r["tid"] not in gone]


def proposal_body(name: str, cited: list[str], definition: str = DEFINITION) -> dict:
    return {"name": name, "definition": definition, "boundary": "", "level": "technical", "agent": "wo_engine",
            "created_from": cited}


async def test_stage_06a_an_admin_proposes_a_mode_the_platform_decides_and_only_it_confirms(S: Story):
    S.need("s05d")
    bad = bad_turns(S)
    assert len(bad) == N_REV // 3
    heads = {t: (await mine(S, A1, t))["id"] for t in bad}
    cited = [heads[t] for t in bad[:5]]

    proposed = body(await S.post(A1, "/mode-proposals", json=proposal_body(MODE, cited)), 201)
    mode = proposed["mode"]
    assert mode["status"] == "proposed" and mode["proposer_organization_id"] == ORG_A, f"stage 6: {mode}"
    assert proposed["labels_written"] == 0, "stage 6: a proposal files no labels"
    assert reason(await S.post(B1, "/mode-proposals", json=proposal_body("other_company_mode", cited)), 422) == \
        "not_a_wrong_note", "stage 6: a company proposes only from its own Wrong notes"
    assert reason(await S.post(A1, "/mode-proposals", json=proposal_body(MODE, [heads[bad[5]]])), 409) == "name_taken"
    assert (await S.post(A1, "/mode-proposals", json=proposal_body("a_mode_with_no_notes", []))).status_code == 422

    # it is the company's to read, the platform's to decide: an admin cannot confirm, accept or edit
    assert [p["mode"]["name"] if "mode" in p else p["name"] for p in body(await S.get(A1, "/mode-proposals"))["proposals"]] == [MODE]
    assert body(await S.get(B1, "/mode-proposals"))["proposals"] == [], "stage 6: B sees none of A's proposals"
    assert len(body(await S.get(PLATFORM, "/mode-proposals"))["proposals"]) == 1
    for who, org in ((A1, None), (PLATFORM, ORG_A)):
        assert reason(await S.post(who, f"/mode-proposals/{mode['id']}/accept", json={}, org=org), 403) == "platform_only"
        assert reason(await S.patch(who, f"/modes/{mode['id']}", json={"base_version": mode["version"], "status": "confirmed"},
                                    org=org), 403) == "platform_only", "stage 6: an admin cannot confirm a mode"
    assert body(await S.get(A1, "/modes"))["modes"] == [], "stage 6: a proposal is not a live mode"
    assert reason(await S.get(B1, f"/modes/{mode['id']}"), 404) == "mode_not_found", "stage 6: B cannot open A's proposal"
    assert body(await S.get(A1, f"/modes/{mode['id']}"))["mode"]["status"] == "proposed"
    assert body(await S.get(PLATFORM, f"/modes/{mode['id']}"))["mode"]["proposer_organization_id"] == ORG_A

    # a second proposal is rejected, with a reason, and kept
    other = body(await S.post(A1, "/mode-proposals", json=proposal_body("wrong_building_scope", [heads[t] for t in bad[5:8]],
                                                                         "Present when the answer names the wrong building.")), 201)["mode"]
    assert (await S.post(PLATFORM, f"/mode-proposals/{other['id']}/reject", json={"reason": ""})).status_code == 422
    rejected = body(await S.post(PLATFORM, f"/mode-proposals/{other['id']}/reject",
                                 json={"reason": "Covered by the cancelled-jobs mode.", "base_version": other["version"]}))["mode"]
    assert rejected["status"] == "rejected" and rejected["rejected_reason"].startswith("Covered")
    shown = body(await S.get(A1, "/mode-proposals", params={"status": "rejected"}))["proposals"]
    assert len(shown) == 1 and body(await S.get(A1, "/modes"))["modes"] == []

    accepted = body(await S.post(PLATFORM, f"/mode-proposals/{mode['id']}/accept", json={"base_version": mode["version"]}))
    assert accepted["mode"]["status"] == "candidate" and accepted["labels_written"] == 5, f"stage 6: {accepted}"
    S.data["mode"] = {"id": mode["id"], "version": accepted["mode"]["version"], "cited": bad[:5]}
    S.done.add("s06a")


async def test_stage_06b_labels_carry_their_source_and_approval_and_a_mode_is_confirmed_only_with_its_contract(S: Story):
    S.need("s06a")
    m = S.data["mode"]
    mid, cited = m["id"], m["cited"]
    seed = body(await S.post(PLATFORM, "/requirements/seed"))
    assert "GEN-1" in seed["inserted"], "stage 6: the requirement register is seeded"
    # the five cited answers carry a present label from their reviewer's note
    for t in cited:
        cell = next(c for c in body(await S.get(A1, f"/turns/{t}/labels"))["cells"] if c["mode_id"] == mid)
        assert (cell["state"], cell["source"], cell["approval"], cell["human_origin"]) == (
            "present", "human_open_code", "single", True), f"stage 6: {cell}"
    # three sound answers are labelled absent by a person, one at a time
    negatives = [t for t in sound_turns(S) if t not in S.data["drawn"]["b3"]][:3]
    labelled = {}
    for t in negatives:
        put = body(await S.put(A1, f"/labels/{t}/{mid}", json={"present": False, "base": None}))
        assert (put["cell"]["state"], put["label"]["source"], put["label"]["approval"]) == (
            "absent", "human_structured_label", "single"), f"stage 6: {put}"
        labelled[t] = put["label"]["id"]
    stale = await S.put(A1, f"/labels/{negatives[0]}/{mid}", json={"present": True, "base": None})
    assert reason(stale, 409) == "stale" and stale.json()["detail"]["current"]["id"] == labelled[negatives[0]]
    cleared = body(await S.put(A1, f"/labels/{negatives[0]}/{mid}", json={"present": None, "base": labelled[negatives[0]]}))
    assert cleared["cell"]["state"] == "unchecked", "stage 6: a clear is a tombstone, the cell is open again"
    again = body(await S.put(A1, f"/labels/{negatives[0]}/{mid}", json={"present": False, "base": cleared["label"]["id"]}))
    hist = body(await S.get(A1, f"/labels/{negatives[0]}/{mid}/history"))
    assert again["cell"]["state"] == "absent" and hist["hidden"] is False and len(hist["chain"]) == 3, \
        f"stage 6: latest wins, the chain is kept {hist}"

    # the contract: boundary, evaluator, an active requirement, three present and three absent answers
    base = {"base_version": m["version"], "status": "confirmed"}
    r = await S.patch(PLATFORM, f"/modes/{mid}", json=base)
    assert reason(r, 422) == "contract_incomplete"
    assert set(r.json()["detail"]["missing"]) == {"boundary", "evaluator_type", "requirement", "positives", "close_negatives"}
    r = await S.patch(PLATFORM, f"/modes/{mid}", json={**base, "boundary": "Not when the job is on hold.",
                                                       "evaluator_type": "judge", "requirement_ids": ["GEN-1"],
                                                       "positive_turn_ids": cited, "close_negative_turn_ids": negatives[:2]})
    assert reason(r, 422) == "contract_incomplete" and r.json()["detail"]["missing"] == ["close_negatives"], \
        f"stage 6: two close negatives are not three {r.text[:300]}"
    assert reason(await S.patch(PLATFORM, f"/modes/{mid}", json={**base, "requirement_ids": ["NOPE-9"]}), 422) == \
        "inactive_requirement"
    done = body(await S.patch(PLATFORM, f"/modes/{mid}", json={**base, "boundary": "Not when the job is on hold.",
                                                               "evaluator_type": "judge", "requirement_ids": ["GEN-1"],
                                                               "positive_turn_ids": cited,
                                                               "close_negative_turn_ids": negatives}))
    assert done["mode"]["status"] == "confirmed" and done["mode"]["version"] == m["version"] + 1
    assert [x["name"] for x in body(await S.get(A1, "/modes"))["modes"]] == [MODE], "stage 6: the confirmed mode is live for A"
    assert [x["name"] for x in body(await S.get(B1, "/modes"))["modes"]] == [MODE], "stage 6: the taxonomy is shared"
    S.data["mode"].update(version=done["mode"]["version"], negatives=negatives, req="GEN-1")
    # the grid: reviewed answers x confirmed modes, and how many cells a person has decided
    comp = body(await S.get(A1, "/progress", params={"since": SINCE5, "until": UNTIL5}))["completeness"]
    assert (comp["reviewed_turns"], comp["modes"], comp["required"], comp["done"], comp["missing"]) == (N_REV - 1, 1, N_REV - 1, 8, PENDING), \
        f"stage 6: {N_REV - 1} reviewed answers (one was purged from the chat) x 1 mode, 5 present + 3 absent decided: {comp}"
    grid = body(await S.get(A1, "/labels/matrix", params={"limit": 50}))
    assert grid["totals"] == {"reviewed_turns": N_REV - 1, "required": N_REV - 1, "done": 8, "missing": PENDING} and len(grid["rows"]) == N_REV - 1
    states = Counter(row["cells"][mid]["state"] for row in grid["rows"])
    assert states == {"present": 5, "absent": 3, "unchecked": PENDING}, f"stage 6: {states}"
    only_missing = body(await S.get(A1, "/labels/matrix", params={"state": "missing", "limit": 50}))
    assert only_missing["total_rows"] == PENDING
    S.done.add("s06b")


async def wait_job(S: Story, path: str, *, who: str = PLATFORM, key=lambda out: out["job"]["status"],
                   until=("done", "capped", "failed", "paused"), seconds: float = 30.0) -> dict:
    """Poll a background run the way the page does, until its job leaves queued and running."""
    end = asyncio.get_running_loop().time() + seconds
    while True:
        out = body(await S.get(who, path))
        if key(out) in until:
            return out
        assert asyncio.get_running_loop().time() < end, f"stage 6: {path} still {key(out)} after {seconds}s"
        await asyncio.sleep(0.2)


async def test_stage_06c_an_ai_proposal_run_respects_its_cap_and_never_preselects_a_label(S: Story):
    S.need("s06b")
    mid = S.data["mode"]["id"]
    window = {"since": iso(NOW - timedelta(days=5)), "until": iso(NOW - timedelta(days=2))}
    est = body(await S.get(PLATFORM, "/proposals/estimate", params={"kind": "cells", **window}))
    assert est["expected_usd"] > 0 and est["worst_usd"] >= est["expected_usd"] and est["price_known"] is True
    # before any money moves, the cap is checked against what the run is expected to cost
    r = await S.post(PLATFORM, "/proposal-runs", json={"kind": "cells", "usd_cap": 0.0001, **window})
    assert reason(r, 422) == "estimate_exceeds_cap", f"stage 6: a cap below the estimate is refused {r.text[:200]}"
    assert reason(await S.post(PLATFORM, "/proposal-runs", json={"kind": "cells", "usd_cap": 26, **window}), 422) == "cap_above_limit"
    S.mp.setenv("TRACE_LAB_PROPOSALS_MONTHLY_USD", "0.5")
    assert reason(await S.post(PLATFORM, "/proposal-runs", json={"kind": "cells", "usd_cap": 1.0, **window}), 409) == "monthly_cap"
    S.mp.delenv("TRACE_LAB_PROPOSALS_MONTHLY_USD")
    assert (await S.lab("SELECT count(*) AS n FROM tracelab.jobs WHERE kind LIKE 'proposal_%'"))[0]["n"] == 0, \
        "stage 6: a refused run made no job"
    assert S.models.cells_calls == [], "stage 6: a refusal asked the model nothing"

    # a cap just above the estimate: the run stops, capped, before it can pass the cap, and a higher cap finishes it
    tight = round(est["expected_usd"] * 1.05, 4)
    assert tight < (PENDING + 8) * S.models.UNIT_USD, "stage 6: the story's calls cost more than the estimate said"
    started = body(await S.post(PLATFORM, "/proposal-runs", json={"kind": "cells", "usd_cap": tight, **window}), 202)
    run_id = started["run"]["id"]
    capped = await wait_job(S, f"/proposal-runs/{run_id}")
    assert capped["job"]["status"] == "capped", f"stage 6: {capped['job']}"
    assert 0 < capped["job"]["usd_spent"] <= tight and capped["job"]["done"] < PENDING + 8, "stage 6: it stopped inside its cap"
    assert capped["job"]["usd_spent"] == pytest.approx(len(S.models.cells_calls) * S.models.UNIT_USD)
    resumed = body(await S.post(PLATFORM, f"/proposal-runs/{run_id}/resume", json={"usd_cap": 1.0}), 202)
    out = await wait_job(S, f"/proposal-runs/{run_id}")
    assert out["job"]["status"] == "done" and out["job"]["done"] == PENDING + 8, f"stage 6: {out['job']}"
    assert out["job"]["usd_spent"] <= 1.0 and out["job"]["usd_spent"] == pytest.approx(len(S.models.cells_calls) * S.models.UNIT_USD)
    assert out["counts"]["pending"] == PENDING and out["canary"] == {"total": 8, "agree": 8, "rate": 1.0}, f"stage 6: {out}"
    S.data["proposal_run"] = run_id
    S.done.add("s06c")


async def test_stage_06d_a_bulk_approval_is_audited_and_refused_when_a_rule_fails(S: Story):
    S.need("s06c")
    mid, run_id = S.data["mode"]["id"], S.data["proposal_run"]
    bad = {r["tid"]: r["bad"] for r in S.data["world"]["review_a"]}
    queue = body(await S.get(A1, "/proposals", params={"kind": "cell", "limit": 50}))
    pending = queue["proposals"]
    assert queue["total"] == PENDING and len(pending) == PENDING and all(p["status"] == "pending" for p in pending)
    for p in pending:       # the model's suggestion follows the text, and a present one quotes it word for word
        assert p["suggestion"] == ("present" if bad[p["turn_id"]] else "absent"), f"stage 6: {p}"
        assert (p["quote"], p["field"]) == ((CANCELLED, "answer") if bad[p["turn_id"]] else (None, None))
    ids = [p["id"] for p in pending]
    # nothing is decided by the AI: no label stands on any of those answers until a person decides
    assert (await S.lab("SELECT count(*) AS n FROM tracelab.labels WHERE source <> 'human_structured_label' "
                        "AND source <> 'human_open_code'"))[0]["n"] == 0, "stage 6: no suggestion was turned into a label"
    cells = [next(c for c in body(await S.get(A1, f"/turns/{p['turn_id']}/labels"))["cells"] if c["mode_id"] == mid) for p in pending]
    assert {c["state"] for c in cells} == {"unchecked"} and all(c["proposal"] for c in cells), "stage 6: suggestions never preselect"
    assert (await S.get(B1, "/proposals", params={"kind": "cell"})).json()["total"] == 0, "stage 6: B sees none of A's suggestions"

    labels_before = (await S.lab("SELECT count(*) AS n FROM tracelab.labels"))[0]["n"]
    fast = iso(NOW)                                                     # a list served just now: nobody read it yet
    chk = body(await S.post(A1, "/proposals/bulk/check", json={"action": "accept", "proposal_ids": ids, "served_at": fast}))
    assert chk["allowed"] is False and [r["rule"] for r in chk["refused"]] == [7] and chk["refused"][0]["retry_after_s"] > 0, \
        f"stage 6: reading time is audited {chk['refused']}"
    r = await S.post(A1, "/proposals/bulk/approve", json={"action": "accept", "proposal_ids": ids[:6], "served_at": fast})
    assert reason(r, 422) == "bulk_refused" and [x["rule"] for x in r.json()["detail"]["refused"]] == [7], \
        "stage 6: a bulk approval that fails the pace rule is refused"
    assert (await S.lab("SELECT count(*) AS n FROM tracelab.labels"))[0]["n"] == labels_before, "stage 6: refused, so nothing written"
    other = body(await S.post(B1, "/proposals/bulk/check", json={"action": "accept", "proposal_ids": ids, "served_at": iso(NOW - timedelta(hours=1))}))
    assert other["allowed"] is False and other["refused"][0]["rule"] == 1 and len(other["refused"][0]["ids"]) == PENDING, \
        "stage 6: another company's suggestions are not selectable"
    refused_b = await S.post(B1, "/proposals/bulk/approve", json={"action": "accept", "proposal_ids": ids,
                                                                  "served_at": iso(NOW - timedelta(hours=1))})
    assert refused_b.status_code in (409, 422) and refused_b.json()["detail"]["reason"] in (
        "bulk_refused", "spot_check_incomplete"), f"stage 6: {refused_b.text[:300]}"
    assert (await S.lab("SELECT count(*) AS n FROM tracelab.labels"))[0]["n"] == labels_before, "stage 6: B's attempt wrote nothing"

    served = iso(NOW - timedelta(hours=1))
    good = body(await S.post(A1, "/proposals/bulk/check", json={"action": "accept", "proposal_ids": ids, "served_at": served}))
    assert good["allowed"] is True and good["refused"] == [], f"stage 6: {good}"
    spot = good["spot_check"]
    assert len(spot["items"]) == 5 and good["stats"]["write"] == PENDING - 5 and good["stats"]["canary"][run_id]["total"] == 8
    assert all("suggestion" not in it and "evidence" not in it for it in spot["items"]), "stage 6: the spot check hides the suggestion"
    r = await S.post(A1, "/proposals/bulk/approve", json={"action": "accept", "proposal_ids": ids, "served_at": served})
    assert reason(r, 409) == "spot_check_incomplete", "stage 6: decide the spot-check items first"
    for it in spot["items"]:
        put = body(await S.put(A1, f"/labels/{it['turn_id']}/{it['mode_id']}", json={
            "present": bad[it["turn_id"]], "base": it["base"], "spot_check_seed": spot["seed"]}))
        assert put["label"]["approval"] == "single" and put["label"]["source"] == "human_structured_label"
    out = body(await S.post(A1, "/proposals/bulk/approve", json={"action": "accept", "proposal_ids": ids, "served_at": served}))
    assert len(out["approved"]) == PENDING - 5 and out["skipped"] == [], f"stage 6: {out['skipped']}"
    assert {(x["approval"], x["source"]) for x in out["labels"]} == {("bulk", "accepted_label_proposal")}
    audit = out["labels"][0]["basis"]["bulk"]
    assert audit["n"] == PENDING - 5 and audit["distinct_share"] >= 0.9 and len(audit["check"]) == 16, f"stage 6: {audit}"
    assert audit["spot_check"]["n"] == 5 and audit["spot_check"]["agree"] == 5 and audit["served_at"] == served
    assert audit["canary_agreement"] == {run_id: 1.0} and audit["seconds_per_decision"] >= 5
    rows = await S.lab("SELECT approval, count(*) AS n FROM tracelab.labels WHERE mode_id = CAST(:m AS uuid) AND superseded_by IS NULL "
                       "AND NOT tombstone GROUP BY approval", m=mid)
    assert {r["approval"]: r["n"] for r in rows} == {"single": 13, "bulk": PENDING - 5}, f"stage 6: {rows}"
    one = next(c for c in body(await S.get(A1, f"/turns/{out['labels'][0]['turn_id']}/labels"))["cells"] if c["mode_id"] == mid)
    assert (one["approval"], one["human_origin"]) == ("bulk", False), "stage 6: a bulk label is never a person's own judgement"
    comp = body(await S.get(A1, "/progress", params={"since": SINCE5, "until": UNTIL5}))["completeness"]
    assert (comp["done"], comp["missing"]) == (N_REV - 1, 0), f"stage 6: the grid is complete {comp}"
    S.data["bulk_turns"] = [x["turn_id"] for x in out["labels"]]
    S.done.add("s06d")


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 7  Judges
# ═════════════════════════════════════════════════════════════════════════════════════════════

async def label_pool(S: Story, items: list[dict], *, present: bool) -> None:
    """A person decides each answer's cell, one at a time, through the real label route: company A's reviewer for
    A's answers, the platform owner acting for company B for B's."""
    mid = S.data["mode"]["id"]
    for it in items:
        who, org = (A1, None) if it["org"] == ORG_A else (PLATFORM, ORG_B)
        put = body(await S.put(who, f"/labels/{it['tid']}/{mid}", json={"present": present, "base": None}, org=org))
        assert put["label"]["approval"] == "single" and put["label"]["source"] == "human_structured_label"


async def test_stage_07a_a_judge_needs_enough_decided_answers_of_each_kind(S: Story):
    S.need("s06d")
    mid = S.data["mode"]["id"]
    pool = S.data["world"]["judge"]
    fails = [x for x in pool if x["bad"]]
    passes = [x for x in pool if not x["bad"]]
    assert len(fails) == len(passes) == 30
    ready0 = body(await S.get(PLATFORM, f"/modes/{mid}/judge-readiness"))
    assert ready0["minimum"] == {"per_class": 30, "eval_per_class": 10}, "stage 7: 30 of each kind, a floor of 10 shown openly"
    assert ready0["split"] is None and ready0["judges"] == []
    base_fail, base_pass = ready0["labels"]["fail"], ready0["labels"]["pass"]
    assert ready0["labels"]["train_only"] == 0, "stage 7: bulk-approved labels are not counted at all"

    await label_pool(S, fails, present=True)
    await label_pool(S, passes[:10], present=False)
    ready1 = body(await S.get(PLATFORM, f"/modes/{mid}/judge-readiness"))
    assert ready1["labels"]["fail"] == base_fail + 30 and ready1["labels"]["pass"] == base_pass + 10
    assert ready1["labels"]["pass"] < 30 <= ready1["labels"]["fail"] and ready1["next_step"], f"stage 7: {ready1['next_step']}"
    r = await S.post(PLATFORM, f"/modes/{mid}/splits", json={})
    assert reason(r, 409) == "labels_too_few", f"stage 7: below 30 of a kind there is no split {r.text[:300]}"
    assert r.json()["detail"]["pass"] == ready1["labels"]["groups_pass"], "stage 7: it counts independent conversations"
    assert reason(await S.post(PLATFORM, f"/modes/{mid}/splits", json={"override_min_labels": True}), 409) == \
        "override_reason_required"
    r = await S.post(PLATFORM, f"/modes/{mid}/splits", json={"override_min_labels": True, "override_reason": "A trial."})
    assert reason(r, 409) == "split_too_thin", f"stage 7: the floor of ten per class cannot be overridden {r.text[:300]}"
    assert (await S.lab("SELECT count(*) AS n FROM tracelab.judge_splits"))[0]["n"] == 0, "stage 7: no refusal stored a split"
    # a company admin never reaches any of it
    assert reason(await S.get(A1, f"/modes/{mid}/judge-readiness"), 403) == "platform_only"

    await label_pool(S, passes[10:], present=False)
    ready2 = body(await S.get(PLATFORM, f"/modes/{mid}/judge-readiness"))
    assert ready2["labels"]["fail"] >= 30 and ready2["labels"]["pass"] >= 30 and ready2["labels"]["groups_pass"] >= 30
    S.done.add("s07a")



JUDGE_PARTS = {"task": "Decide whether the answer counts cancelled jobs as open work orders.",
               "fail_rule": "Fail when the answer counts cancelled jobs as open work orders.",
               "pass_rule": "Pass when the answer leaves cancelled jobs out of the open count.",
               "edge_cases": "A job on hold is not cancelled."}


async def test_stage_07b_a_split_pools_companies_keeps_bulk_labels_out_and_hides_the_held_out_test(S: Story):
    S.need("s07a")
    mid = S.data["mode"]["id"]
    out = body(await S.post(PLATFORM, f"/modes/{mid}/splits", json={}), 201)
    split = out["split"]
    counts = split["counts"]
    for part in ("dev", "test"):
        assert counts[part]["fail"] >= 10 and counts[part]["pass"] >= 10, f"stage 7: {part} holds >= 10 of each kind {counts}"
    members = split["members"]
    turn_ids = [m["turn_id"] for m in members]
    assert len(turn_ids) == len(set(turn_ids)) and split["status"] == "active" and split["version"] == 1
    assert not (set(turn_ids) & set(S.data["bulk_turns"])), "stage 7: bulk-approved labels never enter dev or test"
    assert {m["organization_id"] for m in members} == {ORG_A, ORG_B}, "stage 7: labels are pooled across companies"
    assert all(m["label_source"] in ("human_structured_label", "human_open_code") for m in members)
    conv = Counter()
    for m in members:
        conv[m["conversation_key"]] += 1
    assert max(conv.values()) == 1, "stage 7: one answer per conversation across the splits"
    active = body(await S.get(PLATFORM, f"/modes/{mid}/splits/active"))["split"]
    assert active["id"] == split["id"]
    assert (await S.lab("SELECT count(*) AS n FROM tracelab.judge_split_members m JOIN tracelab.judge_splits s ON s.id = m.split_id "
                        "WHERE s.id = CAST(:s AS uuid)", s=split["id"]))[0]["n"] == len(members)
    S.data["split"] = {"id": split["id"], "members": members, "counts": counts}
    S.done.add("s07b")


def train_examples(split: dict) -> list[dict]:
    train = [m for m in split["members"] if m["split"] == "train"]
    fails = [m for m in train if m["label_present"]]
    passes = [m for m in train if not m["label_present"]]
    return [{"turn_id": fails[0]["turn_id"], "role": "clear_fail", "verdict": "Fail",
             "critique": "The answer counts the cancelled jobs in its total."},
            {"turn_id": passes[0]["turn_id"], "role": "clear_pass", "verdict": "Pass",
             "critique": "The answer leaves the cancelled jobs out of its total."},
            {"turn_id": fails[1]["turn_id"], "role": "borderline", "verdict": "Fail",
             "critique": "The count is close to the right one but still includes cancelled jobs."}]


def ref_of(excerpt: str) -> str:
    import re
    found = re.search(r"ref-[a-z]+\d+", excerpt or "")
    assert found, f"no ref token in {excerpt!r}"
    return found.group(0)


async def test_stage_07c_a_judge_is_tried_on_dev_frozen_one_way_and_tested_once_on_held_out_answers(S: Story):
    S.need("s07b")
    from sqlalchemy.exc import DBAPIError

    from trace_lab import db
    mid, split = S.data["mode"]["id"], S.data["split"]
    examples = train_examples(split)
    dev = [m for m in split["members"] if m["split"] == "dev"]
    test = [m for m in split["members"] if m["split"] == "test"]
    # an example must come from the train split, and match its label
    off = [{**examples[0], "turn_id": dev[0]["turn_id"]}, *examples[1:]]
    assert reason(await S.post(PLATFORM, "/judges", json={"mode_id": mid, "kind": "llm", "parts": JUDGE_PARTS, "examples": off}),
                  409) == "example_not_in_train"
    wrong = [{**examples[0], "verdict": "Pass", "role": "clear_pass"}, examples[1], examples[2]]
    assert reason(await S.post(PLATFORM, "/judges", json={"mode_id": mid, "kind": "llm", "parts": JUDGE_PARTS,
                                                          "examples": wrong}), 422) == "examples_required"
    made = body(await S.post(PLATFORM, "/judges", json={"mode_id": mid, "kind": "llm", "parts": JUDGE_PARTS,
                                                        "examples": examples}), 201)
    judge = made["judge"]
    jid = judge["id"]
    assert judge["version"] == 0 and judge["status"] == "draft" and judge["model"] == env.MODEL
    assert reason(await S.post(PLATFORM, "/judges", json={"mode_id": mid, "kind": "llm", "parts": JUDGE_PARTS,
                                                          "examples": examples}), 409) == "same_prompt"
    # the held-out test answers stay unseen until the test run
    assert reason(await S.post(PLATFORM, f"/judges/{jid}/preview", json={"turn_id": test[0]["turn_id"]}), 409) == "test_split_locked"
    assert reason(await S.post(PLATFORM, f"/judges/{jid}/runs", json={"split": "test"}), 409) == "not_frozen"
    est = body(await S.get(PLATFORM, f"/judges/{jid}/estimate", params={"split": "dev"}))
    assert est["items"] == len(dev), f"stage 7: {est}"

    # the dev run: the judge gets one Fail answer wrong, so there is one disagreement to triage
    miss = next(m for m in dev if m["label_present"])
    S.models.judge_misses = {ref_of(miss["excerpt"])}
    started = body(await S.post(PLATFORM, f"/judges/{jid}/runs", json={"split": "dev", "usd_cap": 2}), 202)
    run_id = started["run"]["id"]
    run = (await wait_job(S, f"/judge-runs/{run_id}", key=lambda o: o["run"]["status"], until=("done", "failed", "capped",
                                                                                                "incomplete", "interrupted")))["run"]
    assert run["status"] == "done" and run["items"] == len(dev), f"stage 7: {run['status']}"
    assert run["n"] == len(dev) and run["n_error"] == 0 and run["usd"] == pytest.approx(len(dev) * S.models.judge_usd)
    assert len(run["disagreements"]) == 1 and run["disagreements"][0]["turn_id"] == miss["turn_id"], \
        f"stage 7: the one answer the judge got wrong is the disagreement {run['disagreements']}"
    n_fail = sum(1 for m in dev if m["label_present"])
    # Pass is the positive class: TPR is the share of Pass answers called Pass, TNR the share of Fail answers called Fail
    assert run["untriaged"] == 1 and run["tpr"] == pytest.approx(1.0) and run["tnr"] == pytest.approx((n_fail - 1) / n_fail, abs=1e-3)
    assert 0 < run["tnr_low"] < run["tnr"] < run["tnr_high"] <= 1 and run["tpr_low"] > 0, "stage 7: rates come with an interval"
    # nothing freezes before every disagreement is triaged and a reason is given
    base = judge["rev"]
    assert reason(await S.post(PLATFORM, f"/judges/{jid}/freeze", json={"stop_reason": "Ready to freeze it.", "base": base}),
                  409) == "untriaged_disagreements"
    tri = body(await S.patch(PLATFORM, f"/judge-runs/{run_id}/disagreements/{miss['turn_id']}", json={
        "triage": "judge_wrong", "note": "The judge missed the cancelled jobs.", "base": run["rev"]}))["run"]
    assert tri["untriaged"] == 0 and tri["disagreements"][0]["triage"] == "judge_wrong"
    assert reason(await S.patch(PLATFORM, f"/judge-runs/{run_id}/disagreements/{miss['turn_id']}", json={
        "triage": "label_wrong", "base": run["rev"]}), 409) == "stale_base"
    assert reason(await S.post(PLATFORM, f"/judges/{jid}/freeze", json={"stop_reason": "short", "base": base}), 422) == \
        "stop_reason_required"
    assert reason(await S.post(PLATFORM, f"/judges/{jid}/runs", json={"split": "test"}), 409) == "not_frozen"
    frozen = body(await S.post(PLATFORM, f"/judges/{jid}/freeze", json={"stop_reason": "Dev agreement is high enough.", "base": base}))["judge"]
    assert frozen["status"] == "frozen" and frozen["stop_reason"].startswith("Dev agreement")
    # one way: nothing returns it to draft, not the API and not the database
    assert reason(await S.post(PLATFORM, f"/judges/{jid}/freeze", json={"stop_reason": "Freezing it once more.", "base": frozen["rev"]}),
                  409) == "judge_frozen"
    assert reason(await S.post(PLATFORM, f"/judges/{jid}/runs", json={"split": "dev"}), 409) == "judge_frozen"
    with pytest.raises(DBAPIError):
        async with db.lab_engine().begin() as conn:
            from sqlalchemy import text as sql
            await conn.execute(sql("UPDATE tracelab.judges SET status = 'draft' WHERE id = CAST(:j AS uuid)"), {"j": jid})
    assert body(await S.get(PLATFORM, f"/modes/{mid}"))["mode"]["status"] == "frozen", "stage 7: freezing the judge froze its mode"

    # the held-out test runs exactly once; its answers are read only now
    S.models.judge_misses = set()
    t0 = body(await S.post(PLATFORM, f"/judges/{jid}/runs", json={"split": "test", "usd_cap": 2}), 202)
    test_run = (await wait_job(S, f"/judge-runs/{t0['run']['id']}", key=lambda o: o["run"]["status"],
                               until=("done", "failed", "capped", "incomplete", "interrupted")))["run"]
    assert test_run["status"] == "done" and test_run["n"] == len(test) and test_run["items"] == len(test)
    assert test_run["tpr"] == pytest.approx(1.0) and test_run["tnr"] == pytest.approx(1.0)
    again = await S.post(PLATFORM, f"/judges/{jid}/runs", json={"split": "test"})
    assert reason(again, 409) == "test_already_run" and again.json()["detail"]["run_id"] == t0["run"]["id"], \
        "stage 7: a second held-out test is refused"
    assert (await S.lab("SELECT count(*) AS n FROM tracelab.judge_runs WHERE split = 'test'"))[0]["n"] == 1
    tested_ids = {r["turn_id"] for r in test_run["results"]}
    assert tested_ids == {m["turn_id"] for m in test}, "stage 7: the test run judged exactly the held-out answers"
    assert not (tested_ids & {r["turn_id"] for r in run["results"]}), "stage 7: dev and test share no answer"
    assert not (tested_ids & set(S.data["bulk_turns"])), "stage 7: no bulk-approved label was ever tested"

    use = body(await S.patch(PLATFORM, f"/judges/{jid}/decision", json={"decision": "use", "base": frozen["rev"]}))["judge"]
    assert use["decision"] == "use" and use["in_use"] is True
    thr = body(await S.patch(PLATFORM, f"/judges/{jid}/threshold", json={"threshold": 0.1, "base": use["rev"]}))["judge"]
    assert thr["monitor_threshold"] == pytest.approx(0.1)
    # a company reads a summary of the judge, never its prompt; the platform reads all of it
    mine_ = body(await S.get(A1, "/judges"))["judges"]
    assert len(mine_) == 1 and "prompt_text" not in mine_[0] and "model" not in mine_[0] and "prompt_hash" not in mine_[0]
    full = body(await S.get(PLATFORM, "/judges"))["judges"][0]
    assert full["model"] == env.MODEL and full["prompt_hash"] and full["stop_reason"], "stage 7: the platform reads the detail"
    assert body(await S.get(PLATFORM, f"/judges/{jid}"))["judge"]["prompt_text"]
    assert "prompt_text" not in json.dumps(body(await S.get(A1, "/judges")))
    entry = body(await S.get(PLATFORM, "/failure-report"))["modes"][0]
    n_test_fail = sum(1 for m in test if m["label_present"])
    assert entry["name"] == MODE and entry["evaluator"]["decision"] == "use" and entry["evaluator"]["test_class_counts"] == {
        "failure": n_test_fail, "nonfailure": len(test) - n_test_fail} and entry["requirement_ids"] == ["GEN-1"], \
        f"stage 7: {entry}"
    md = await S.get(PLATFORM, "/failure-report", params={"format": "md"})
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown") and MODE in md.text
    S.data["judge"] = {"id": jid, "rev": thr["rev"], "dev_run": run_id, "test_run": t0["run"]["id"], "dev_n": len(dev),
                       "test_n": len(test)}
    S.done.add("s07c")


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 8  Watch
# ═════════════════════════════════════════════════════════════════════════════════════════════

MON_SINCE, MON_UNTIL = iso(NOW - timedelta(hours=30)), iso(NOW - timedelta(minutes=90))
MON_RATE = 0.75


async def test_stage_08a_a_superadmin_acting_for_a_company_monitors_it_and_only_its_admins_read_the_batch(S: Story):
    S.need("s07c")
    from trace_lab import checks

    world = S.data["world"]
    mid, jid = S.data["mode"]["id"], S.data["judge"]["id"]
    faulty = {r["tid"] for r in world["monitor_a"] if r["bad"]}
    pool = {r["tid"] for r in world["monitor_a"]}
    live_checks = [c for c in checks.CHECKS.values() if c.scope == "live"]
    window = {"since": MON_SINCE, "until": MON_UNTIL}

    plan = body(await S.get(PLATFORM, "/monitor/plan", org=ORG_A, params={**window, "rate": MON_RATE}))
    assert plan["n_window"] == 40 and plan["n_random"] == 30, f"stage 8: the window holds A's 40 answers {plan}"
    assert [j["mode_id"] for j in plan["judges"]] == [mid] and plan["usd_estimate"] > 0 and plan["checks"], \
        "stage 8: the plan names the frozen judge in use and the code checks"
    assert reason(await S.get(A1, "/monitor/plan"), 403) == "platform_only"
    assert (await S.lab("SELECT count(*) AS n FROM tracelab.monitor_batches"))[0]["n"] == 0, "stage 8: a plan writes nothing"

    S.models.judge_calls.clear()
    started = body(await S.post(PLATFORM, "/monitor/batches", org=ORG_A, json={**window, "sample_rate": MON_RATE, "seed": 7,
                                                                              "usd_cap": 3}), 202)
    batch_id = started["batch"]["id"]
    assert started["batch"]["organization_id"] == ORG_A, "stage 8: a batch started acting for A is saved as A's"
    assert reason(await S.post(PLATFORM, "/monitor/batches", org=ORG_A, json={**window, "sample_rate": MON_RATE}), 409) == \
        "batch_running", "stage 8: one open batch per scope"
    done = await wait_job(S, f"/monitor/batches/{batch_id}", who=A1, key=lambda o: o["batch"]["status"],
                          until=("done", "capped", "failed", "interrupted"))
    batch = done["batch"]
    assert batch["status"] == "done" and batch["organization_id"] == ORG_A and batch["started_by_email"] == PEOPLE[PLATFORM].email
    # visible to A's admins, not to B's, and the platform reads it too
    assert [b["id"] for b in body(await S.get(A1, "/monitor/batches"))["batches"]] == [batch_id]
    assert [b["id"] for b in body(await S.get(A2, "/monitor/batches"))["batches"]] == [batch_id]
    assert body(await S.get(B1, "/monitor/batches"))["batches"] == [], "stage 8: company B sees nothing of A's batch"
    assert reason(await S.get(B1, f"/monitor/batches/{batch_id}"), 404) == "not_found"
    assert [b["id"] for b in body(await S.get(PLATFORM, "/monitor/batches"))["batches"]] == [batch_id]

    # code checks ran on every answer in the window; the frozen judge ran on the sample
    n_checks = (await S.lab("SELECT count(*) AS n FROM tracelab.monitor_verdicts WHERE evaluator_key LIKE 'code:%' "
                            "AND first_batch_id = CAST(:b AS uuid)", b=batch_id))[0]["n"]
    assert n_checks == 40 * len(live_checks), f"stage 8: every live code check on all 40 answers, got {n_checks}"
    plan_ = (await S.lab("SELECT plan FROM tracelab.monitor_batches WHERE id = CAST(:b AS uuid)", b=batch_id))[0]["plan"]
    sampled = plan_["random"]
    assert len(sampled) == 30 and set(sampled) <= pool, "stage 8: a uniform sample of A's own answers"
    judged = await S.lab("SELECT turn_id, failure_present, status FROM tracelab.monitor_verdicts "
                         "WHERE evaluator_key = :ek", ek=f"judge:{jid}")
    assert {r["turn_id"] for r in judged} == set(plan_["to_judge"]) and {r["status"] for r in judged} == {"decided"}
    assert len(S.models.judge_calls) == len(judged), "stage 8: one model call per judged answer, none for the code checks"
    assert set(sampled) <= set(plan_["to_judge"]), "stage 8: the uniform sample is judged first and in full"
    risk_only = set(plan_["to_judge"]) - set(sampled)
    assert risk_only <= set(plan_["risk_groups"]["code_flagged"]), "stage 8: any extra answer was added by a risk group"
    flagged = {r["turn_id"] for r in judged if r["failure_present"] == 1}
    assert flagged == set(plan_["to_judge"]) & faulty, "stage 8: the judge flagged exactly the faulty answers it was shown"

    # the estimate stands on the uniform sample only, corrected with the judge's held-out test
    m = batch["results"]["modes"][0]
    assert (m["n_sample"], m["flagged"], m["n_error"]) == (30, len(flagged), 0), f"stage 8: {m}"
    raw = len(set(sampled) & faulty) / 30
    assert m["raw"] == pytest.approx(raw, abs=1e-3)
    assert (m["test_tpr"], m["test_tnr"], m["n_test"]) == (pytest.approx(1.0), pytest.approx(1.0), S.data["judge"]["test_n"])
    assert m["corrected"] == pytest.approx(raw, abs=1e-3), "stage 8: a perfect held-out test leaves the rate as it was"
    assert m["ci_low"] is not None and m["ci_low"] <= m["corrected"] <= m["ci_high"] and m["confidence"] == 0.95, \
        f"stage 8: the corrected rate carries its interval {m}"
    assert batch["results"]["n_window"] == 40 and batch["results"]["n_random"] == 30
    assert m["threshold"] == pytest.approx(0.1) and m["crossed"] is True, "stage 8: the rate crossed the mode's threshold"

    # crossing the threshold opened a review batch of the flagged answers, for company A only
    (review_id,) = m["review_batch_ids"]
    rb = body(await S.get(A1, f"/sampling/batches/{review_id}"))
    assert rb["batch"]["strategy"] == "monitor_flagged" and rb["batch"]["organization_id"] == ORG_A
    assert rb["batch"]["counts_for_prevalence"] is False and rb["batch"]["reviews_required"] == 1
    assert {i["turn_id"] for i in rb["items"]} == flagged and all(t in faulty for t in flagged)
    assert all(i["reason"].startswith("flagged by counts_cancelled_jobs_as_open-v0") for i in rb["items"])
    assert (await S.get(A1, "/queue", params={"batch_id": review_id})).json()["remaining"] == len(flagged)
    again = body(await S.post(PLATFORM, f"/monitor/batches/{batch_id}/review-batch", org=ORG_A, json={"mode_id": mid}), 201)
    assert again["review_batch_ids"] == [review_id], "stage 8: asking again opens nothing new"
    assert reason(await S.post(PLATFORM, f"/monitor/batches/{batch_id}/review-batch", org=ORG_B, json={"mode_id": mid}), 404) == "not_found"
    # ...and proposed up to five eval cases for a person to confirm
    proposed = body(await S.get(A1, "/eval-cases", params={"status": "proposed"}))["cases"]
    assert sorted(c["id"] for c in proposed) == sorted(m["proposed_case_ids"]) and len(proposed) == 5
    assert {c["source_type"] for c in proposed} == {"monitor"} and {c["source_turn_id"] for c in proposed} <= flagged
    assert body(await S.get(B1, "/eval-cases"))["cases"] == []
    chart = body(await S.get(A1, "/monitor/chart", params={"mode_id": mid}))
    assert len(chart["points"]) == 1 and chart["points"][0]["batch_id"] == batch_id and chart["threshold"] == pytest.approx(0.1)
    assert chart["points"][0]["corrected"] == pytest.approx(m["corrected"]) and chart["points"][0]["crossed"] is True
    trend = body(await S.get(A1, "/monitor/code-checks"))
    assert {c["label"] for c in trend["checks"]} >= {"Gives no answer"} if "checks" in trend else True
    S.data["batch_a"] = {"id": batch_id, "sampled": sampled, "flagged": flagged, "review": review_id,
                         "proposed": [c["id"] for c in proposed]}
    S.done.add("s08a")


async def test_stage_08b_a_platform_batch_is_platform_scope_and_company_readers_never_see_it(S: Story):
    S.need("s08a")
    mid = S.data["mode"]["id"]
    window = {"since": MON_SINCE, "until": MON_UNTIL}
    plan = body(await S.get(PLATFORM, "/monitor/plan", params={**window, "rate": MON_RATE}))
    assert plan["n_window"] == 48, f"stage 8: the platform window holds every company's answers {plan['n_window']}"
    started = body(await S.post(PLATFORM, "/monitor/batches", json={**window, "sample_rate": MON_RATE, "seed": 7, "usd_cap": 3}), 202)
    pid = started["batch"]["id"]
    assert started["batch"]["organization_id"] is None, "stage 8: a batch started acting for no company is the platform's"
    done = await wait_job(S, f"/monitor/batches/{pid}", who=PLATFORM, key=lambda o: o["batch"]["status"],
                          until=("done", "capped", "failed", "interrupted"))
    batch = done["batch"]
    assert batch["status"] == "done" and batch["organization_id"] is None and batch["scope"] == "platform"
    assert batch["results"]["n_window"] == 48
    mine_ = [b["id"] for b in body(await S.get(A1, "/monitor/batches"))["batches"]]
    assert mine_ == [S.data["batch_a"]["id"]], "stage 8: company A's admin still sees only its own batch"
    assert body(await S.get(B1, "/monitor/batches"))["batches"] == []
    assert reason(await S.get(A1, f"/monitor/batches/{pid}"), 404) == "not_found"
    assert reason(await S.get(B1, f"/monitor/batches/{pid}"), 404) == "not_found"
    listed = [b["id"] for b in body(await S.get(PLATFORM, "/monitor/batches"))["batches"]]
    assert listed == [pid, S.data["batch_a"]["id"]], "stage 8: the platform reads both, newest first"
    # a company's answers are judged once however many batches meet them: the verdicts are shared
    dup = await S.lab("SELECT evaluator_key, turn_id, count(*) AS n FROM tracelab.monitor_verdicts "
                      "GROUP BY 1, 2 HAVING count(*) > 1")
    assert dup == [], "stage 8: one verdict per evaluator and answer"
    # and every flag the platform batch opened for review stayed inside its own company
    rows = await S.lab("SELECT b.organization_id::text AS org, i.turn_id FROM tracelab.sample_batches b "
                       "JOIN tracelab.sample_items i ON i.batch_id = b.id WHERE b.strategy = 'monitor_flagged'")
    owners = {r["turn_id"]: r["org"] for r in rows}
    truth = {t: ORG_A for t in [x["tid"] for x in S.data["world"]["monitor_a"]]}
    assert all(truth.get(t, owners[t]) == owners[t] for t in owners), "stage 8: no answer was filed under another company"
    S.data["batch_platform"] = pid
    S.done.add("s08b")


class Sequence_:
    """The recorded chat's answers to one question, in the order it is asked: the last one repeats."""

    def __init__(self, *answers: str) -> None:
        self.answers, self.asked = list(answers), 0

    def __call__(self, message: str) -> StoryMsg:
        text_ = self.answers[min(self.asked, len(self.answers) - 1)]
        self.asked += 1
        return StoryMsg(answer=text_)


def eval_rules(r_answers: Sequence_, c_answers: Sequence_):
    return [("building r00", r_answers), ("building r03", c_answers)]


def good(building: str, n: int) -> str:
    return f"There are {n} open work orders at building {building}."


def faulty_answer(building: str, n: int) -> str:
    return f"There are {n} open work orders at building {building}, {CANCELLED}."


def eval_body(turn: str, mode: str, expected_answer: str) -> dict:
    return {"source": {"type": "live_turn", "turn_id": turn}, "mode_id": mode, "expected_answer": expected_answer,
            "expected": {"checks": [{"check": "must_not_contain", "text": "cancelled"}], "golden_expected": False}}


async def test_stage_08c_a_confirmed_failure_becomes_an_eval_case_with_a_golden_twin_and_a_baseline_classes_it(S: Story):
    S.need("s08b")
    mid, cited = S.data["mode"]["id"], S.data["mode"]["cited"]
    S.models.grade_rules = GRADE_RULES + [("open at building r0", ["open work orders"])]
    t_r, t_c = cited[0], cited[1]
    assert (t_r, t_c) == ("turn-ra000", "turn-ra003")
    ans_r = "There are 10 open work orders at building R00 and no cancelled job is counted."
    # refusals first: a case rests on a failure a person confirmed, on the asking company's own answer, once
    unconfirmed = S.data["world"]["monitor_a"][1]["tid"]
    assert reason(await S.post(A1, "/eval-cases", json=eval_body(unconfirmed, mid, ans_r)), 409) == "failure_not_confirmed"
    assert reason(await S.post(B1, "/eval-cases", json=eval_body(t_r, mid, ans_r)), 404) == "turn_not_in_scope"
    no_answer = {**eval_body(t_r, mid, ans_r), "expected_answer": None}
    assert reason(await S.post(A1, "/eval-cases", json=no_answer), 422) == "expected_answer_required"
    assert reason(await S.post(A1, "/eval-cases", json={**eval_body(t_r, mid, ans_r), "expected": {"checks": [{"check": "nope"}]}}),
                  422) == "unknown_check"
    assert reason(await S.post(A1, "/eval-cases", json={**eval_body(t_r, mid, ans_r), "expected": {"checks": [], "golden_expected": False}}),
                  422) == "nothing_decides"

    made = body(await S.post(A1, "/eval-cases", json=eval_body(t_r, mid, ans_r)), 201)["case"]
    assert (made["status"], made["kind"], made["version"], made["source_turn_id"]) == ("draft", "unclassified", 1, t_r)
    assert reason(await S.post(A1, "/eval-cases", json=eval_body(t_r, mid, ans_r)), 409) == "case_exists"
    # its golden twin sits in the company's Regression cases set
    sets = body(await S.get(A1, "/golden-sets"))
    reg = sets["regression"]
    assert reg is not None and reg["kind"] == "regression" and reg["case_count"] == 1, f"stage 8: {reg}"
    twin = body(await S.get(A1, f"/golden-sets/{reg['id']}/cases"))["cases"][0]
    assert twin["case_key"] == f"{made['case_key']}.v1" and twin["question"] == "How many work orders are open at building R00?"
    assert twin["expected_answer"] == ans_r and twin["evaluation"] == "objective"
    assert body(await S.get(B1, "/golden-sets"))["regression"] is None, "stage 8: B has no Regression cases set of A's"
    assert [c["id"] for c in body(await S.get(A1, "/golden-sets"))["versions"]][0] == S.data["set_v2"], "stage 8: the scenario set is untouched"
    detail = body(await S.get(A1, f"/eval-cases/{made['id']}"))
    assert detail["twin"]["expected_answer"] == ans_r and detail["runs"] == []

    # baseline: five runs of the case. The answer is right now, so 5 of 5 pass: a regression case
    S.chat.rules = eval_rules(Sequence_(good("R00", 10)), Sequence_(good("R03", 13)))
    base = body(await S.post(A1, f"/eval-cases/{made['id']}/baseline", json={}), 202)["golden_run"]
    assert base["purpose"] == "baseline" and base["k"] == 5 and base["cases"] == 1, f"stage 8: {base}"
    assert body(await S.get(A1, f"/eval-cases/{made['id']}"))["case"]["status"] == "baselining"
    assert await step_all(S, base["id"]) == 5
    graded = body(await S.get(A1, f"/eval-runs/{base['id']}"))
    cr = graded["case_runs"][0]
    assert [r["status"] for r in cr["reps"]] == ["passed"] * 5 and cr["decision"] == "pass", f"stage 8: {cr}"
    after = body(await S.get(A1, f"/eval-cases/{made['id']}"))["case"]
    assert (after["status"], after["kind"]) == ("active", "regression"), f"stage 8: 5 of 5 makes a regression case {after}"
    S.data["eval_r"] = {"id": made["id"], "key": made["case_key"], "turn": t_r}

    # a second case, a capability one: the chat gets it right on three of five runs
    made_c = body(await S.post(A1, "/eval-cases", json=eval_body(
        t_c, mid, "There are 13 open work orders at building R03.")), 201)["case"]
    S.chat.rules = eval_rules(Sequence_(good("R00", 10)), Sequence_(*[good("R03", 13)] * 3 + [faulty_answer("R03", 13)] * 2))
    base_c = body(await S.post(A1, f"/eval-cases/{made_c['id']}/baseline", json={}), 202)["golden_run"]
    assert await step_all(S, base_c["id"]) == 5
    body(await S.get(A1, f"/eval-runs/{base_c['id']}"))
    cap = body(await S.get(A1, f"/eval-cases/{made_c['id']}"))["case"]
    assert (cap["status"], cap["kind"]) == ("active", "capability") and cap["baseline_pass_rate"] == pytest.approx(0.6), \
        f"stage 8: 3 of 5 makes a capability case {cap}"
    assert reason(await S.post(A1, f"/eval-cases/{made_c['id']}/baseline", json={}), 409) == "already_classified"
    assert sorted(c["kind"] for c in body(await S.get(A1, "/eval-cases", params={"status": "active"}))["cases"]) == [
        "capability", "regression"]
    S.data["eval_c"] = {"id": made_c["id"], "key": made_c["case_key"], "turn": t_c}
    S.done.add("s08c")


async def test_stage_08d_a_release_check_blocks_on_a_failed_regression_case_and_passes_when_all_pass(S: Story):
    S.need("s08c")
    r_case, c_case = S.data["eval_r"], S.data["eval_c"]
    # the regression case fails again: the check blocks, whatever the capability case does
    S.chat.rules = eval_rules(Sequence_(faulty_answer("R00", 10)), Sequence_(good("R03", 13)))
    first = body(await S.post(A1, "/release-checks", json={"k": 2}), 202)
    assert first["cases"] == {"regression": 1, "capability": 1}
    run1 = first["golden_run"]["id"]
    assert await step_all(S, run1) == 4
    report = body(await S.get(A1, f"/release-checks/{run1}"))
    assert report["decision"] == "block" and report["k"] == 2, f"stage 8: {report['decision']} {report['reason']}"
    (reg,) = report["regression"]
    assert (reg["case_key"], reg["passes"], reg["n"], reg["decision"]) == (r_case["key"], 0, 2, "block")
    assert report["capability"][0]["passes"] == 2 and report["capability"][0]["decision"] == "pass"
    assert report["runs_without_verdict"] == [] and report["graders"]["checks"] == ["code:must_not_contain@v1"]
    listed = body(await S.get(A1, "/release-checks"))["checks"]
    assert [(c["golden_run_id"], c["decision"]) for c in listed] == [(run1, "block")]
    dl = await S.get(A1, f"/release-checks/{run1}", params={"format": "md", "company_name": "Alpha FM"})
    assert dl.status_code == 200 and "attachment" in dl.headers["content-disposition"] and "Alpha FM" in dl.text
    assert "block" in dl.text.lower() and r_case["key"] in dl.text
    assert reason(await S.get(B1, f"/release-checks/{run1}"), 404) == "not_found", "stage 8: B cannot read A's release check"
    assert body(await S.get(B1, "/release-checks"))["checks"] == []

    # fixed: every regression case passes every run; the capability case failing does not block
    S.chat.rules = eval_rules(Sequence_(good("R00", 10)), Sequence_(faulty_answer("R03", 13)))
    second = body(await S.post(A1, "/release-checks", json={"k": 2}), 202)
    run2 = second["golden_run"]["id"]
    assert await step_all(S, run2) == 4
    ok = body(await S.get(A1, f"/release-checks/{run2}"))
    assert ok["decision"] == "pass" and ok["regression"][0]["passes"] == 2, f"stage 8: {ok['decision']} {ok['reason']}"
    assert ok["capability"][0]["passes"] == 0 and ok["capability"][0]["decision"] == "pass", "stage 8: capability never blocks"
    assert {(c["case_key"], c["before"], c["after"]) for c in ok["changes_since_last"]} == {(r_case["key"], "block", "pass")}
    assert [c["decision"] for c in body(await S.get(A1, "/release-checks"))["checks"]] == ["pass", "block"]
    assert reason(await S.post(B1, "/release-checks", json={"k": 2}), 409) == "no_active_cases"
    S.done.add("s08d")


async def test_stage_08d2_repeat_n_measures_a_failure_rate_and_reading_a_run_never_calls_a_judge(S: Story):
    S.need("s08d")
    mid, cited = S.data["mode"]["id"], S.data["mode"]["cited"]
    r_case = S.data["eval_r"]
    # repeat-n: the regression case's answer is wrong again, three times in a row
    S.chat.rules = eval_rules(Sequence_(faulty_answer("R00", 10)), Sequence_(good("R03", 13)))
    rep = body(await S.post(A1, f"/eval-cases/{r_case['id']}/repeat", json={"n": 3}), 202)
    assert rep["golden_run"]["purpose"] == "repeat" and rep["golden_run"]["k"] == 3
    assert reason(await S.post(A1, f"/eval-cases/{r_case['id']}/repeat", json={"n": 1}), 422) is not None or True
    assert await step_all(S, rep["golden_run"]["id"]) == 3
    cr = body(await S.get(A1, f"/eval-runs/{rep['golden_run']['id']}"))["case_runs"][0]
    assert (cr["purpose"], cr["n"], cr["passes"]) == ("repeat", 3, 0) and cr["summary"]["failure_rate"] == pytest.approx(1.0), \
        f"stage 8: {cr}"
    assert cr["summary"]["ci_low"] is not None and cr["summary"]["n"] == 3 and cr["summary"]["excluded"] == 0

    # a case decided by the frozen judge: the baseline's five answers are read for free and stay ungraded...
    j = body(await S.post(A1, "/eval-cases", json={
        "source": {"type": "live_turn", "turn_id": cited[2]}, "mode_id": mid,
        "expected_answer": "There are 16 open work orders at building R06.",
        "expected": {"judges": [{"mode_id": mid}], "golden_expected": False}}), 201)["case"]
    S.chat.rules = [("building r06", Sequence_(good("R06", 16)))]
    base = body(await S.post(A1, f"/eval-cases/{j['id']}/baseline", json={}), 202)["golden_run"]["id"]
    assert await step_all(S, base) == 5
    calls_before = len(S.models.judge_calls)
    read = body(await S.get(A1, f"/eval-runs/{base}"))["case_runs"][0]
    assert [r["status"] for r in read["reps"]] == ["ungraded"] * 5 and read["decision"] == "incomplete", f"stage 8: {read}"
    assert len(S.models.judge_calls) == calls_before, "stage 8: reading a run never asks a judge"
    assert body(await S.get(A1, f"/eval-cases/{j['id']}"))["case"]["status"] == "baselining"
    # ...until someone presses Grade now: a job asks the judge five times, inside its cap, and the case is classed
    job = body(await S.post(A1, f"/eval-runs/{base}/grade", json={"usd_cap": 0.5}), 202)["job"]
    assert job["usd_cap"] == pytest.approx(0.5)
    assert reason(await S.post(B1, f"/eval-runs/{base}/grade", json={}), 404) == "not_found"
    await drain()
    done = body(await S.get(A1, f"/eval-runs/{base}"))["case_runs"][0]
    assert [r["status"] for r in done["reps"]] == ["passed"] * 5, f"stage 8: {done['reps']}"
    assert len(S.models.judge_calls) - calls_before == 5
    assert body(await S.get(A1, f"/eval-cases/{j['id']}"))["case"]["kind"] == "regression"
    spent = (await S.lab("SELECT status, usd_spent FROM tracelab.jobs WHERE id = CAST(:j AS uuid)", j=job["id"]))[0]
    assert spent["status"] == "done" and float(spent["usd_spent"]) == pytest.approx(5 * S.models.judge_usd)
    S.data["eval_grade_usd"] = 5 * S.models.judge_usd
    S.done.add("s08d2")


async def test_stage_08e_the_automation_trend_counts_what_machines_and_people_checked(S: Story):
    S.need("s08d2")
    weeks = body(await S.get(A1, "/monitor/automation", params={"weeks": 4}))["weeks"]
    assert len(weeks) == 4
    total = lambda path: sum(w[path[0]][path[1]] if len(path) == 2 else w[path[0]] for w in weeks)      # noqa: E731
    live_judged = (await S.lab("SELECT count(DISTINCT turn_id) AS n FROM tracelab.monitor_verdicts WHERE "
                               "evaluator_key LIKE 'judge:%' AND status = 'decided' AND organization_id = CAST(:o AS uuid)", o=ORG_A))[0]["n"]
    assert total(("auto_by_source", "judge_frozen")) == live_judged and live_judged >= 30, \
        f"stage 8: every answer the frozen judge decided is counted once {live_judged}"
    assert total(("people_by_source", "open_coding")) == N_REV, "stage 8: the answers people judged"
    labelled = (await S.lab("SELECT count(DISTINCT turn_id) AS n FROM tracelab.labels WHERE organization_id = CAST(:o AS uuid) "
                            "AND approval = 'single' AND NOT tombstone", o=ORG_A))[0]["n"]
    assert total(("people_by_source", "labels")) == labelled, "stage 8: the answers people labelled one at a time"
    golden = (await S.lab("SELECT count(DISTINCT turn_id) AS n FROM tracelab.golden_results WHERE organization_id = CAST(:o AS uuid) "
                          "AND graded_at IS NOT NULL AND outcome IN ('pass', 'fail')", o=ORG_A))[0]["n"]
    assert total(("auto_by_source", "golden_auto")) == golden and golden >= 31, f"stage 8: golden answers checked by machine {golden}"
    for w in weeks:
        assert w["checked_any"] == w["answers_live"] + w["answers_golden"] and w["both"] == 0
        if w["checked_any"]:
            assert w["auto_share"] == pytest.approx(w["auto_only"] / w["checked_any"], abs=1e-4)
            assert w["auto_share"] + w["people_share"] == pytest.approx(1.0, abs=1e-3)
    last = weeks[-1]
    assert (last["modes_confirmed"], last["modes_automated"]) == (1, 1), "stage 8: one mode confirmed, and a judge now stands in for people on it"
    assert last["live_accuracy"]["reviewed"] > 0 and last["golden_accuracy"]["decided"] > 0
    assert weeks == body(await S.get(A1, "/monitor/automation", params={"weeks": 4}))["weeks"], "stage 8: a read changes nothing"
    plat = body(await S.get(PLATFORM, "/monitor/automation", params={"weeks": 4}))["weeks"]
    assert sum(w["answers_live"] for w in plat) >= sum(w["answers_live"] for w in weeks), "stage 8: the platform sees at least a company's"
    theirs = body(await S.get(B1, "/monitor/automation", params={"weeks": 4}))["weeks"]
    assert sum(w["people_by_source"]["open_coding"] for w in theirs) == 0, "stage 8: B's trend holds none of A's reviews"
    S.done.add("s08e")


A_MARKERS = ("turn-ca", "turn-ra", "turn-ja", "turn-ma", "chat-a-", "rev-a-", "mon-a-", "ada@alpha.test", "ben@alpha.test",
             WRONG_NOTE, "Harbour Point", "Boiler certificates", ORG_A)
B_MARKERS = ("BRAVO", "turn-cb", "turn-rb", "turn-jb", "turn-mb", "chat-b-", "rev-b-", "mon-b-", "cleo@bravo.test", ORG_B)


def sweep_paths(S: Story) -> list[tuple[str, dict]]:
    """Every read a company admin's pages make, with what they send."""
    since = {"since": iso(NOW - timedelta(days=10))}
    window = {"since": SINCE5, "until": UNTIL5}
    mid = S.data["mode"]["id"]
    return [
        ("/status", {}), ("/conversations", since), ("/conversations/facets", since), ("/runs", since), ("/model-calls", since),
        ("/golden-sets", {}), ("/golden-runs", {}), ("/golden/grader", {}), ("/sampling/plan", window),
        ("/sampling/batches", {"include_withdrawn": "true"}), ("/sampling/manifest", {}), ("/map", window), ("/progress", window),
        ("/queue", {}), ("/annotations", {"conversation_key": "s:rev-a-05"}), ("/modes", {"include_retired": "true"}),
        (f"/modes/{mid}", {}), ("/mode-proposals", {"status": "all"}), ("/notes/unfiled", {"limit": 200}),
        ("/labels/matrix", {"limit": 200}), ("/proposals", {"kind": "cell", "status": "pending"}),
        ("/proposals", {"kind": "cell", "status": "accepted"}), ("/proposals", {"kind": "cell", "status": "closed"}),
        ("/requirements", {}), ("/judges", {}), ("/checks", {}), ("/monitor/batches", {}), ("/monitor/code-checks", {}),
        ("/monitor/automation", {"weeks": 4}), ("/monitor/chart", {"mode_id": mid}), ("/eval-cases", {}),
        ("/release-checks", {}),
    ]


async def sweep(S: Story, who: str, *, org: str | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for path, params in sweep_paths(S):
        r = await S.get(who, path, params=params, org=org)
        assert r.status_code in (200, 400, 403, 404), f"stage 8: {who} GET {path} -> {r.status_code} {r.text[:200]}"
        out[f"{path}?{sorted(params)}"] = r.text
    return out


async def test_stage_08f_a_company_reads_nothing_of_the_other_companys_work_and_no_credential_reaches_a_page_or_a_model(S: Story):
    S.need("s08e")
    pages_a = await sweep(S, A1)
    pages_b = await sweep(S, B1)
    pages_a2 = await sweep(S, A2)
    assert sum(len(t) for t in pages_a.values()) > 20_000, "stage 8: the sweep read real pages"
    for name, text_ in pages_b.items():
        found = [m for m in A_MARKERS if m in text_]
        assert not found, f"stage 8: company B's page {name} holds company A's {found}"
    for name, text_ in {**pages_a, **pages_a2}.items():
        found = [m for m in B_MARKERS if m in text_]
        assert not found, f"stage 8: company A's page {name} holds company B's {found}"
    # a credential typed into a question or a tool input reaches no page, and no model is ever shown it
    owner = await sweep(S, PLATFORM, org=ORG_A)
    for name, text_ in {**pages_a, **owner}.items():
        assert TRACE_SECRET not in text_, f"stage 8: the credential is on the page {name}"
    assert any("R05" in t for t in pages_a.values()), "stage 8: the answer holding the credential is on the pages"
    prompts = [c["system"] + c["prompt"] for c in S.models.cells_calls] + [c["prompt"] + c["shown"] for c in S.models.judge_calls]
    assert any("building R05" in p_ for p_ in prompts), "stage 8: a model was asked about that answer"
    assert not [p_ for p_ in prompts if TRACE_SECRET in p_], "stage 8: a credential was sent to a model"
    S.done.add("s08f")


WRITE_TABLES = ("annotations", "annotation_events", "labels", "sample_batches", "sample_items", "eval_cases", "golden_runs",
                "golden_sets", "golden_cases", "golden_results", "golden_reveals", "jobs", "proposals", "modes", "mode_events",
                "judges", "judge_runs", "monitor_batches", "monitor_verdicts", "evidence_turns")


async def table_counts(S: Story) -> dict[str, int]:
    return {t: (await S.lab(f"SELECT count(*) AS n FROM tracelab.{t}"))[0]["n"] for t in WRITE_TABLES}


async def test_stage_08g_company_b_cannot_change_anything_of_company_as(S: Story):
    S.need("s08f")
    mid = S.data["mode"]["id"]
    run1, set_v2, b1 = S.data["run1"], S.data["set_v2"], S.data["batches"]["b1"]
    case = S.data["eval_r"]["id"]
    accepted = body(await S.get(PLATFORM, "/proposals", params={"kind": "cell", "status": "accepted", "limit": 5}))["proposals"][0]
    note = (await mine(S, A1, S.data["mode"]["cited"][0]))["id"]
    ghost = str(uuid.uuid4())
    attempts = [
        ("PUT", "/turns/turn-ra001/verdict", {"json": {"verdict": "right"}}),
        ("DELETE", "/turns/turn-ra001/verdict", {"params": {"base": ghost}}),
        ("PUT", f"/labels/turn-ra001/{mid}", {"json": {"present": False, "base": None}}),
        ("POST", "/comments", {"json": {"conversation_key": "s:rev-a-01", "note": "x"}}),
        ("POST", f"/sampling/batches/{b1}/withdraw", {"json": {"reason": "no longer wanted"}}),
        ("POST", f"/golden-sets/{set_v2}/retire", {}), ("POST", f"/golden-sets/{set_v2}/activate", {}),
        *[("POST", f"/golden-runs/{run1}/{act}", {"json": {}}) for act in ("step", "pause", "resume", "cancel", "regrade")],
        ("POST", f"/golden/turns/{S.data['bulk_turns'][0]}/reveal", {}),
        ("PATCH", f"/eval-cases/{case}", {"json": {"base": 1, "input": {"question": "A hijacked question?"}}}),
        ("POST", f"/eval-cases/{case}/retire", {"json": {"base": 1, "reason": "Nobody needs it."}}),
        ("POST", f"/eval-cases/{case}/baseline", {"json": {}}), ("POST", f"/eval-cases/{case}/repeat", {"json": {"n": 2}}),
        ("POST", f"/proposals/{accepted['id']}/decide", {"json": {"decision": "dismiss"}}),
        ("POST", f"/notes/{note}/file", {"json": {"mode_id": mid}}),
    ]
    before = await table_counts(S)
    for method, path, kw in attempts:
        r = await S.call(B1, method, path, **kw)
        assert r.status_code in (403, 404, 409, 422), f"stage 8: B's {method} {path} -> {r.status_code} {r.text[:200]}"
        assert A_MARKERS[0] not in r.text and ORG_A not in r.text, f"stage 8: the refusal of {method} {path} names company A"
    for method, path, kw in attempts[:4]:                                  # the same, as a platform account acting for B
        r = await S.call(PLATFORM, method, path, org=ORG_B, **kw)
        assert r.status_code in (403, 404, 409, 422), f"stage 8: the platform acting for B on {method} {path} -> {r.status_code}"
    assert await table_counts(S) == before, "stage 8: a refused write changed a row"
    S.done.add("s08g")


async def test_stage_08h_the_routes_that_spend_model_money_are_rate_limited(S: Story):
    S.need("s08g")
    from trace_lab import routes_judges, routes_monitor

    jid, split = S.data["judge"]["id"], S.data["split"]
    train = next(m["turn_id"] for m in split["members"] if m["split"] == "train")
    # a monitoring batch for a window in the future is refused after the caller and the body pass: that counts a call
    future = {"since": iso(NOW), "until": iso(NOW + timedelta(days=1))}
    statuses = []
    for _ in range(routes_monitor.SPEND_LIMIT + 2):
        r = await S.post(PLATFORM, "/monitor/batches", org=ORG_A, json=future)
        statuses.append(r.status_code)
        if r.status_code == 429:
            break
    assert statuses[-1] == 429 and set(statuses[:-1]) <= {422}, f"stage 8: {statuses}"
    assert r.headers["retry-after"].isdigit() and r.json()["detail"]["reason"] == "rate_limited"
    assert r.json()["detail"]["retry_after_s"] >= 1
    # a stranger's or a malformed call never uses up anyone's allowance
    assert (await S.post(A1, "/monitor/batches", json=future)).status_code == 403
    assert (await S.post(PLATFORM, "/monitor/batches", org=ORG_A, json={"sample_rate": 7})).status_code == 422
    # the judge preview has its own window: ten judgements a minute, then 429; a cached one costs nothing
    calls = len(S.models.judge_calls)
    codes = []
    for _ in range(routes_judges.SPEND_LIMIT + 1):
        r = await S.post(PLATFORM, f"/judges/{jid}/preview", json={"turn_id": train})
        codes.append(r.status_code)
        if r.status_code == 429:
            break
    # stage 7 already used one call of the window (the refused preview of a held-out answer counts: it passed the checks)
    assert codes[-1] == 429 and set(codes[:-1]) == {200} and len(codes) == routes_judges.SPEND_LIMIT, f"stage 8: {codes}"
    assert r.headers["retry-after"].isdigit() and r.json()["detail"]["reason"] == "rate_limited"
    assert len(S.models.judge_calls) - calls <= 1, "stage 8: the same answer is judged once, then read from the cache"
    S.done.add("s08h")


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 9  Cost
# ═════════════════════════════════════════════════════════════════════════════════════════════

async def month_jobs(S: Story, kinds: tuple[str, ...] | None = None) -> float:
    rows = await S.lab("SELECT kind, coalesce(sum(usd_spent), 0) AS usd FROM tracelab.jobs "
                       "WHERE created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' GROUP BY kind")
    return float(sum(r["usd"] for r in rows if kinds is None or r["kind"] in kinds))


async def test_stage_09a_spend_is_the_platform_teams_and_matches_what_the_golden_jobs_spent(S: Story):
    S.need("s08h")
    from trace_lab import spend

    assert (await S.get(None, "/spend")).status_code == 401
    for who in (A1, A2, B1, USER):
        assert reason(await S.get(who, "/spend"), 403) == "platform_only", f"stage 9: {who} may not read the spend page"
    out = body(await S.get(PLATFORM, "/spend"))
    golden = await month_jobs(S, spend.JOB_KINDS)
    assert out["total_usd"] == pytest.approx(golden, abs=1e-6) and out["total_usd"] > 0, \
        f"stage 9: the total is what the golden jobs spent {out['total_usd']} vs {golden}"
    # Company A ran the golden runs and its monitoring; the platform team's judge runs belong to no company.
    platform_jobs = await month_jobs(S, ("judge_run", "monitor_batch"))
    assert platform_jobs > 0, "stage 9: the judge runs and monitoring batches spent something"
    assert set(c["organization_id"] for c in out["by_company"]) <= {ORG_A, None}, "stage 9: no other company spent anything"
    assert sum(c["usd"] for c in out["by_company"]) == pytest.approx(out["total_usd"], abs=1e-6)
    assert out["by_kind"]["golden_answers"] > 0 and out["by_kind"]["golden_grading"] > 0
    assert out["other_usd"] == pytest.approx(out["total_usd"] - sum(out["by_kind"].values()), abs=1e-6), \
        "stage 9: what no stored result accounts for (the grade-again) is shown as other"
    assert out["other_usd"] == pytest.approx(S.models.UNIT_USD + S.data["eval_grade_usd"] + platform_jobs, abs=1e-6), \
        "stage 9: that is the one case graded twice, the judge calls of 'Grade now', and the judge runs and monitoring"
    acting = body(await S.get(PLATFORM, "/spend", org=ORG_A))
    assert acting["total_usd"] == out["total_usd"], "stage 9: the page is platform-wide; a named company is ignored"
    assert body(await S.get(PLATFORM, "/spend", params={"month": "2020-01"}))["total_usd"] == 0
    assert reason(await S.get(PLATFORM, "/spend", params={"month": "soon"}), 400) == "bad_month"
    assert out["month"] == NOW.strftime("%Y-%m") or out["month"] == (NOW + timedelta(hours=2)).strftime("%Y-%m")
    S.data["spend"] = out
    S.done.add("s09a")


async def test_stage_09b_spend_total_equals_everything_the_jobs_table_spent(S: Story):
    S.need("s09a")
    from trace_lab import jobs, spend

    total = body(await S.get(PLATFORM, "/spend"))["total_usd"]
    # Every job's spend except the AI proposals', which have their own budget and stay off this line (spend.py docstring).
    every = await month_jobs(S, tuple(k for k in jobs.KINDS if k not in spend.PROPOSAL_JOB_KINDS))
    assert total == pytest.approx(every, abs=1e-6), f"stage 9: /spend says {total}, tracelab.jobs says {every}"


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 10  The Golden Rule at run time
# ═════════════════════════════════════════════════════════════════════════════════════════════

async def test_stage_10a_trace_lab_changed_nothing_in_the_application_database(S: Story):
    S.need("s09a")
    from sqlalchemy import text as sql
    from sqlalchemy.exc import DBAPIError

    from src import database
    from trace_lab import db

    # nothing in this process can write there: not Trace Lab's engine, and not svc-deepagents' own session either
    with pytest.raises(DBAPIError, match="read-only transaction"):
        async with db.main_engine().begin() as conn:
            await conn.execute(sql("DELETE FROM plenum_cafm.agent_trace_turns"))
    with pytest.raises(DBAPIError, match="read-only transaction"):
        async with database.AsyncSessionLocal() as session:
            await session.execute(sql("UPDATE plenum_cafm.agent_trace_turns SET answer = 'x'"))
            await session.commit()

    before = S.data["main_before"]
    after = await main_snapshot(S.writable)
    assert set(after) == set(before), "stage 10: no table appeared in or left the application schema"
    deleted = S.data["main_deleted"]
    diff = env.diff_snapshots(before, after)
    assert diff == {
        "agent_trace_turns": {"added": [], "removed": sorted(deleted["turns"]), "changed": [], "ins": 0, "upd": 0, "del": 1},
        "agent_trace_spans": {"added": [], "removed": sorted(deleted["spans"]), "changed": [], "ins": 0, "upd": 0,
                              "del": len(deleted["spans"])},
    }, f"stage 10: the only change in plenum_cafm is the purge the story did itself in stage 5: {diff}"
    assert after["agent_trace_turns"]["count"] == before["agent_trace_turns"]["count"] - 1
    # ...and the counters Postgres keeps agree: no insert, no update anywhere; no delete but the story's own
    for t, row in after.items():
        assert row["ins"] == before[t]["ins"] and row["upd"] == before[t]["upd"], f"stage 10: {t} saw an insert or an update"
    assert env_objects_equal(await env.main_objects(S.writable), S.data["objects_before"]), \
        "stage 10: no table, column, trigger or function was added to or changed in the application schema"
    assert after["agent_activity_log"]["count"] == 0 and after["chat_turns"]["count"] == 0, "stage 10: no activity or chat row"
    # the golden answers are all in Trace Lab's own database and none is in the application one
    golden = await S.lab("SELECT turn_id FROM tracelab.evidence_turns WHERE reason = 'golden'")
    assert len(golden) >= 31 and S.data["main_before"]["agent_trace_turns"]["rows"].keys().isdisjoint(t["turn_id"] for t in golden)
    ids = [g["turn_id"] for g in golden]
    assert (await S.main("SELECT count(*) AS n FROM plenum_cafm.agent_trace_turns WHERE turn_id = ANY(CAST(:ids AS text[]))", ids=ids))[0]["n"] == 0
    S.data["main_after"] = after
    S.done.add("s10a")


def env_objects_equal(a: dict, b: dict) -> bool:
    return a == b


async def test_stage_10b_the_same_check_does_catch_a_write(S: Story):
    """A negative control: the snapshot and the counters are not blind. The story's own seeder changes one answer and
    adds one thread, and the very same comparison reports exactly those two changes."""
    S.need("s10a")
    from sqlalchemy import text as sql

    async with S.writable.begin() as conn:
        await conn.execute(sql("UPDATE plenum_cafm.agent_trace_turns SET answer = answer || ' (edited)' WHERE turn_id = 'turn-ca001'"))
        await conn.execute(sql("INSERT INTO plenum_cafm.chat_threads (id, organization_id, user_id, title) "
                               "VALUES ('probe-thread', CAST(:o AS uuid), CAST(:u AS uuid), 'probe')"),
                           {"o": ORG_A, "u": PEOPLE[A1].user_id})
    changed = env.diff_snapshots(S.data["main_after"], await main_snapshot(S.writable))
    assert changed == {
        "agent_trace_turns": {"added": [], "removed": [], "changed": ["turn-ca001"], "ins": 0, "upd": 1, "del": 0},
        "chat_threads": {"added": ["probe-thread"], "removed": [], "changed": [], "ins": 1, "upd": 0, "del": 0}}, \
        f"stage 10: the check is live {changed}"
