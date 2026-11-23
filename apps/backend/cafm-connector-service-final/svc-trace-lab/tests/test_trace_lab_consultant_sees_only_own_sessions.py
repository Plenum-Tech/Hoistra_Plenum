"""A Hoistra consultant in Trace Lab (23 Nov 2026, Hussain): "when i login through it in trace lab i need to only see
the session done by it and no one else", and "they annotate the questions"; then "i need to see everything of the
tracelab here but the question should be consultant account specific".

A consultant is a plain Hoistra user with the access role `consultant` (svc-operations-intelligence
engines/auth/pages.py). They get into Trace Lab without a company admin turning it on and have a plain member's
workspace (Runs, Labels, Progress, Loop, Notes, Learning), in which every read is their own: the sessions they ran
(tracelab.sessions.user_id), the traces of those sessions, the runs holding a question they asked and, in those runs,
only their questions (runs.own_questions). People, the platform's tools and every curator's action stay refused.
These pin the rules that need no database; the SQL they produce is checked here as text."""
from __future__ import annotations

import asyncio
import inspect
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.services.principal import Principal
from trace_lab import eval_store, labelling, learning, loop, routes_scenarios, runs
from trace_lab.review_errors import NotFound

ORG = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


def person(role="user", access_role=None, org=ORG):
    return Principal(user_id=uuid.uuid4(), email="cara@x.test", organization_id=uuid.UUID(org), role=role,
                     can_ingest=False, building_ids=(uuid.uuid4(),), access_role=access_role)


def run(coro):
    return asyncio.run(coro)


def consultant_member(p=None):
    p = p or person(access_role="consultant")
    return run(labelling.access(p, None, all_companies_ok=True))


# ── who they are ────────────────────────────────────────────────────────────────────────────────────

def test_a_consultant_is_known_by_the_hoistra_role():
    assert labelling.is_hoistra_consultant(person(access_role="consultant"))
    assert labelling.is_hoistra_consultant(person(access_role=" Consultant "))
    assert not labelling.is_hoistra_consultant(person())
    assert not labelling.is_hoistra_consultant(person(access_role="compliance_officer"))
    # An admin is never held to their own sessions, whatever the row says.
    assert not labelling.is_hoistra_consultant(person(role="admin", access_role="consultant"))


def test_a_consultant_is_in_at_once_for_their_own_company_and_their_own_sessions(monkeypatch):
    async def no_platform_role(_uid):           # never asked: the Hoistra role decides first
        raise AssertionError("a consultant is not looked up among the platform-wide reviewers")
    monkeypatch.setattr(labelling, "platform_role", no_platform_role)
    p = person(access_role="consultant")
    m = consultant_member(p)
    assert m.own_only is True
    assert m.active and m.status == "active"
    assert m.org == ORG and m.all_companies is False and m.manages is False
    assert m.user_id == str(p.user_id)
    assert labelling.me_out(m)["own_only"] is True
    # A role the database accepts on every row they write (step_labels.reviewer_role is checked): never a new one.
    assert m.role == "user"


def test_a_consultant_cannot_name_another_company():
    with pytest.raises(HTTPException) as e:
        run(labelling.access(person(access_role="consultant"), OTHER, all_companies_ok=True))
    assert e.value.status_code == 403


def test_everyone_else_is_not_held_to_their_own():
    assert labelling.me_out(labelling.Member(person(), ORG, "u", "e", "tester", "active", False))["own_only"] is False


# ── what they read with ─────────────────────────────────────────────────────────────────────────────

def test_a_consultant_reads_the_chat_as_themselves_never_as_the_company_admin():
    p = person(access_role="consultant")
    assert labelling.company_view(p, ORG) is p
    # anyone else who is an active member still reads the company's answers, as before
    q = person()
    seen = labelling.company_view(q, ORG)
    assert seen.role == "admin" and seen.building_ids is None


def test_the_eval_store_scope_of_a_consultant_is_their_own_sessions_and_traces():
    m = consultant_member()
    where, p = runs._scope(m, "s")
    assert "s.organization_id = CAST(:scope_org AS uuid)" in where
    assert "s.user_id = CAST(:scope_uid AS uuid)" in where
    assert p == {"scope_org": ORG, "scope_uid": m.user_id}
    where, p = runs._scope(m, "ss")
    assert "ss.user_id = CAST(:scope_uid AS uuid)" in where
    where, p = runs._scope(m, "t")
    assert "t.organization_id = CAST(:scope_org AS uuid)" in where
    assert "own_s.id = t.session_id" in where and "own_s.user_id = CAST(:scope_uid AS uuid)" in where


OWN_Q = "q.created_by = CAST(:scope_uid AS uuid)"


@pytest.mark.parametrize("alias", ["r", "g", "gm"])
def test_a_consultant_sees_only_the_runs_holding_a_question_they_asked(alias):
    m = consultant_member()
    where, p = runs._scope(m, alias)
    assert f"{alias}.organization_id = CAST(:scope_org AS uuid)" in where
    assert f"{alias}_q.run_id = {alias}.id" in where
    assert f"{alias}_q.created_by = CAST(:scope_uid AS uuid)" in where
    # or a question answered by a turn of a session they ran
    assert f"{alias}_q_s.user_id = CAST(:scope_uid AS uuid)" in where
    assert p == {"scope_org": ORG, "scope_uid": m.user_id}


def test_any_other_alias_is_nothing_for_a_consultant():
    where, _ = runs._scope(consultant_member(), "x")
    assert where == "FALSE"


def test_the_scope_of_everyone_else_is_unchanged():
    m = labelling.Member(person(), ORG, "u", "e", "tester", "active", False)
    assert runs._scope(m, "s") == ("s.organization_id = CAST(:scope_org AS uuid)", {"scope_org": ORG})
    assert runs._scope(m, "r") == ("r.organization_id = CAST(:scope_org AS uuid)", {"scope_org": ORG})


# ── which questions of a run they see ───────────────────────────────────────────────────────────────

def test_a_consultant_sees_only_their_own_questions_of_a_run():
    m = consultant_member()
    own, p = runs.own_questions(m)
    assert "q.organization_id = CAST(:scope_org AS uuid)" in own and OWN_Q in own
    assert "q_l.question_id = q.id" in own and "q_s.user_id = CAST(:scope_uid AS uuid)" in own
    assert p == {"scope_org": ORG, "scope_uid": m.user_id}
    own_x, _ = runs.own_questions(m, "qx")
    assert "qx.created_by = CAST(:scope_uid AS uuid)" in own_x


def test_everyone_else_sees_every_question_of_a_run_they_may_see():
    assert runs.own_questions(labelling.Member(person(), ORG, "u", "e", "tester", "active", False)) == ("TRUE", {})
    assert runs.own_questions(None) == ("TRUE", {})
    m = labelling.Member(person(), ORG, "u", "e", "tester", "active", False)
    assert runs._scope_q(m) == runs._scope(m, "r")


def test_the_scope_of_a_question_joined_to_its_run_holds_both():
    m = consultant_member()
    where, p = runs._scope_q(m)
    assert "r_q.run_id = r.id" in where and OWN_Q in where
    assert p == {"scope_org": ORG, "scope_uid": m.user_id}


def test_a_run_card_counts_only_their_questions_and_nobody_elses_card_changes():
    assert runs._stats_sql() == runs._RUN_STATS
    own, _ = runs.own_questions(consultant_member())
    sql = runs._stats_sql("= r.id", own)
    # questions, traced, expected, notes, reviewed, first and last asked: every count is narrowed
    assert sql.count(OWN_Q) == 7
    assert runs._run_order() == runs.RUN_ORDER and OWN_Q in runs._run_order("= r.id", own)


class Rec:
    """A connection that records each statement and answers with the rows queued for it."""

    def __init__(self, *answers):
        self.calls = []
        self.answers = list(answers)

    async def execute(self, sql, params=None):
        self.calls.append((str(sql), dict(params or {})))
        got = self.answers.pop(0) if self.answers else []
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: list(got),
                                                                first=lambda: got[0] if got else None))


DAY = {"id": "r1", "kind": "production", "run_no": 7, "run_ids": ["r1"], "organization_id": ORG}


def test_a_live_day_lists_only_their_questions():
    m = consultant_member()
    conn = Rec([])
    rows, n = run(runs.label_questions(conn, DAY, batch=None, m=m))
    assert (rows, n) == ([], 0)
    sql, p = conn.calls[0]
    assert OWN_Q in sql and p["scope_uid"] == m.user_id and p["rids"] == ["r1"]


def test_a_live_day_lists_every_question_for_everyone_else():
    conn = Rec([])
    run(runs.label_questions(conn, DAY, batch=None))
    sql, p = conn.calls[0]
    assert "created_by" not in sql and "scope_uid" not in p


def test_a_codex_batch_has_no_rows_for_a_consultant():
    conn = Rec()
    out = run(runs.label_questions(conn, DAY, batch={"run_tag": "t", "since": None, "until": None},
                                   m=consultant_member()))
    assert out == ([], 0) and conn.calls == []


def test_another_persons_question_in_their_run_is_not_found_for_a_consultant():
    m = consultant_member()
    conn = Rec([{"id": "r1", "run_no": 7, "kind": "production"}], [])
    with pytest.raises(NotFound):
        run(runs._question_row(conn, m, "R7-3"))
    (run_sql, run_p), (q_sql, q_p) = conn.calls
    assert "r_q.created_by = CAST(:scope_uid AS uuid)" in run_sql and OWN_Q in run_sql
    assert OWN_Q in q_sql and q_p["seq"] == 3 and q_p["scope_uid"] == m.user_id


@pytest.mark.parametrize("fn", [runs.turn_expected, runs.list_questions, runs.list_runs, runs.group_row,
                                runs.with_group, eval_store.run_sessions, loop.proposals, loop.decide,
                                loop.decide_label, loop.overview])
def test_every_read_of_questions_on_their_pages_is_narrowed(fn):
    """Pinned as text: each read a consultant's pages make of a run's questions goes through own_questions/_scope_q."""
    src = inspect.getsource(fn)
    assert "own_questions(" in src or "_scope_q(" in src, fn.__name__


# ── only their own marks ─────────────────────────────────────────────────────────────────────────────

MINE = "= CAST(:scope_uid AS uuid)"


def test_a_consultant_sees_only_the_marks_they_made():
    m = consultant_member()
    assert runs.mine_sql(m, "c.author_id") == " AND c.author_id " + MINE
    assert runs.mine_expected_sql(m) == " AND (e.source = 'coding_agent' OR e.created_by " + MINE + ")"
    assert runs.mine_params(m) == {"scope_uid": m.user_id}
    assert "c.author_id " + MINE in runs._cells_sql(m) and "c.author_id " + MINE in runs._positives_sql(m)
    stats = runs._q_stats_sql(m)
    assert stats.count("s.reviewer_id " + MINE) == 2 and "e.created_by " + MINE in stats
    own, _ = runs.own_questions(m)
    card = runs._stats_sql("= r.id", own)
    assert card.count("s.reviewer_id " + MINE) == 2 and "e.created_by " + MINE in card


def test_everyone_else_still_sees_everyones_marks():
    m = labelling.Member(person(), ORG, "u", "e", "tester", "active", False)
    assert runs.mine_sql(m, "c.author_id") == "" and runs.mine_expected_sql(m) == "" and runs.mine_params(m) == {}
    assert runs._cells_sql(m) == runs._CELLS_SQL and runs._positives_sql(m) == runs._POSITIVES_SQL
    assert runs._q_stats_sql(m) == runs._Q_STATS and "scope_uid" not in runs._CELLS_SQL


def test_their_scores_are_their_own_and_the_newest_is_current():
    from trace_lab import scores

    mine1 = {"name": "domain", "source": "ANNOTATION", "author_email": "Cara@x.test", "verdict": "pass"}
    mine0 = {"name": "domain", "source": "ANNOTATION", "author_email": "cara@x.test", "verdict": "fail"}
    other = {"name": "domain", "source": "ANNOTATION", "author_email": "bob@x.test", "verdict": "fail"}
    judge = {"name": "logic", "source": "EVAL", "author_email": None, "verdict": "fail"}
    current, hist = scores.mine_only({"t1": [other, mine1, judge, mine0], "t2": [other]}, "cara@x.test", set())
    assert hist == {"t1": [mine1, mine0], "t2": []}
    assert current["t1"]["domain"] is mine1 and current["t1"]["logic"] is None, "an evaluator's is not theirs"
    assert all(v is None for v in current["t2"].values())
    flagged, _ = scores.mine_only({"t1": [mine1]}, "cara@x.test", {"t1"})
    assert flagged["t1"]["domain"] is None, "a turn they flag counts no score"


@pytest.mark.parametrize("fn", [labelling.labels_for_turn, runs.get_question, runs.turn_expected, runs.progress,
                                runs._grid_cells, loop.overview, loop.proposals, loop.decide_label])
def test_every_read_of_marks_on_their_pages_keeps_only_theirs(fn):
    src = inspect.getsource(fn)
    assert "own_only" in src or "mine_" in src or "_cells_sql(m)" in src or "_positives" in src, fn.__name__


def test_trace_labels_flags_and_session_scores_keep_only_theirs():
    from trace_lab import expected_flags, trace_labels

    assert "own_only" in inspect.getsource(trace_labels.labels_of)
    assert "own_only" in inspect.getsource(expected_flags.flags_of)
    assert "mine_only" in inspect.getsource(eval_store.get_session)
    assert "mine_only" in inspect.getsource(eval_store.list_sessions)
    src = inspect.getsource(learning.gather)
    assert "reviewer_id = CAST(:scope_uid AS uuid)" in src and "e.created_by = CAST(:scope_uid AS uuid)" in src


# ── the expected answer of a question the compliance agent answered ─────────────────────────────────

def test_a_turn_that_read_records_in_a_stage_is_about_records():
    from trace_lab import records_queries as rq

    stage = [{"kind": "agent", "name": "compliance", "type": "agent"},
             {"kind": "db", "name": "linked records: 13 queries", "type": "span"}]
    assert rq.data_tools(stage) == [] and rq.reads_records(stage) is True
    assert rq.reads_records([{"kind": "llm", "name": "plan", "type": "generation"}]) is False
    assert rq.reads_records(None) is False


def test_the_planner_is_asked_for_a_stage_that_read_records(monkeypatch):
    from trace_lab import records_queries as rq

    async def no_map():
        raise rq.records_run.NotReady("not built")
    monkeypatch.setattr(rq, "extract_queries", lambda nodes: [])
    monkeypatch.setattr(rq.ak, "match", lambda q: None)
    monkeypatch.setattr(rq.records_run, "load_map", no_map)
    stage = [{"kind": "db", "name": "linked records: 13 queries", "type": "span"}]
    # it got past "not about records" to the map, which is the planner's first step
    assert run(rq.queries_for(stage, "Which buildings put me at risk this month?", propose=True))["none"] == "no_map"
    assert run(rq.queries_for([], "x", propose=True))["none"] == "not_about_records"


def test_a_draft_with_no_values_tried_before_the_fix_is_read_again_once():
    from datetime import datetime, timezone

    from trace_lab import auto_expected as ax

    now = datetime(2026, 11, 23, 14, 0, tzinfo=timezone.utc)
    old = {"evidence": {"drafted_from": "SPEC.md", "no_query": "not_about_records", "tries": 1,
                        "last_tried": now.isoformat()}}
    assert ax._due(old, now) is True, "due at once, whatever its tries"
    stamped = {"evidence": dict(old["evidence"], records_gate=ax.RECORDS_GATE)}
    assert ax._due(stamped, now) is False
    # a try that met no map before the fix had gone on to wait hours: it too is read again once, at once
    no_map = {"evidence": {"drafted_from": "SPEC.md", "no_query": "no_map", "tries": 2, "last_tried": now.isoformat()}}
    assert ax._due(no_map, now) is True
    assert ax._due({"evidence": dict(no_map["evidence"], records_gate=ax.RECORDS_GATE)}, now) is False
    assert "records_gate" in ax._RETRY_DRAFTS and "not_about_records" in ax._RETRY_DRAFTS
    assert "records_gate" in inspect.getsource(ax._note_try) and "records_gate" in inspect.getsource(ax._draft_one)


# ── their failure modes are their own ────────────────────────────────────────────────────────────────

MODES = [{"lineage_id": "m-platform", "name": "Wrong building"}, {"lineage_id": "m-cara", "name": "Missed a lapse"},
         {"lineage_id": "m-dan", "name": "Dan's own"}]


def _owners(monkeypatch, cara, consultants):
    async def owners(_conn):
        return {"m-platform": "p-1", "m-cara": cara, "m-dan": "dan-1"}

    async def held(ids):
        return {i for i in ids if i in consultants}
    monkeypatch.setattr(loop, "mode_owners", owners)
    monkeypatch.setattr(labelling, "hoistra_consultants", held)


def test_a_consultant_sees_only_the_failure_modes_they_made(monkeypatch):
    m = consultant_member()
    _owners(monkeypatch, m.user_id, {m.user_id, "dan-1"})
    assert [x["lineage_id"] for x in run(loop.visible_modes(None, m, MODES))] == ["m-cara"]


def test_nobody_else_sees_a_consultants_failure_modes(monkeypatch):
    m = consultant_member()
    _owners(monkeypatch, m.user_id, {m.user_id, "dan-1"})
    other = labelling.Member(person(), ORG, "u", "e", "tester", "active", False)
    assert [x["lineage_id"] for x in run(loop.visible_modes(None, other, MODES))] == ["m-platform"]
    # unknown who is a consultant (the read failed): everything shows, as before
    _owners(monkeypatch, m.user_id, set())
    assert run(loop.visible_modes(None, other, MODES)) == MODES
    assert run(loop.visible_modes(None, None, MODES)) == MODES


def test_a_consultant_writes_their_own_failure_modes_and_a_plain_member_still_cannot():
    from trace_lab.review_errors import Forbidden

    runs.require_mode_writer(consultant_member())
    with pytest.raises(Forbidden):
        runs.require_mode_writer(labelling.Member(person(), ORG, "u", "e", "tester", "active", False))


def test_who_is_a_consultant_is_none_when_it_cannot_be_read(monkeypatch):
    from contextlib import asynccontextmanager

    from trace_lab import conversations

    @asynccontextmanager
    async def down():
        raise RuntimeError("no main database")
        yield  # pragma: no cover
    monkeypatch.setattr(conversations, "_read", down)
    monkeypatch.setattr(labelling, "_CONSULTANT_CACHE", {})
    assert run(labelling.hoistra_consultants(["11111111-1111-4111-8111-111111111111"])) == set()


@pytest.mark.parametrize("fn", [runs.list_modes, runs.save_mode, runs.retire_mode, runs.matrix, runs.matrix_all,
                                runs.set_cell, runs.progress, loop.overview, loop.proposals, loop.draft_spec])
def test_every_list_of_failure_modes_is_the_callers_own_view(fn):
    src = inspect.getsource(fn)
    assert "_live_modes(conn, m)" in src or "visible_modes(" in src, fn.__name__


# ── the fixed query for "which buildings are at risk" ───────────────────────────────────────────────

@pytest.mark.parametrize("q", ["Which buildings put me at risk this month?", "which building is most at risk?",
                               "Which buildings are non-compliant?", "Which sites have risks right now"])
def test_which_buildings_are_at_risk_has_a_fixed_query(q):
    from trace_lab import answer_key as ak

    key, params = ak.match(q)
    assert key.id == "buildings_at_risk" and params == {}
    plan = key.plan(params)
    assert plan["focus"]["concept"] == "ComplianceCertificate" and plan["focus"]["states"] == ["lapsed"]
    # no window: a certificate that lapsed before this month still puts its building at risk; a vendor's is not the
    # building's own
    assert plan["focus"]["filters"] == [{"attribute": "cert_scope", "op": "not_in", "value": ["Vendor"]}]
    assert plan["aggregate"] == {"count_by": [{"concept": "Building", "attribute": "name"}]}


@pytest.mark.parametrize("q", ["Show me everything that's at risk", "Which buildings have the most open work orders?",
                               "Which vendors are blocked?"])
def test_other_questions_keep_their_keys(q):
    from trace_lab import answer_key as ak

    got = ak.match(q)
    assert got is None or got[0].id != "buildings_at_risk"


def test_the_at_risk_sentence_names_each_building_and_its_lapsed_certificates():
    from trace_lab import answer_key as ak

    key = ak.KEYS["buildings_at_risk"]
    assert ak.sentence(key, {}, []) == "No building holds a lapsed certificate."
    assert ak.sentence(key, {}, [{"label": "Bishopsgate Tower", "value": 4}]) == (
        "1 building is at risk: Bishopsgate Tower (4 lapsed certificates).")
    assert ak.sentence(key, {}, [{"label": "A", "value": 2}, {"label": "B", "value": 1}]) == (
        "2 buildings are at risk: A (2 lapsed certificates), B (1 lapsed certificate).")


def test_a_values_draft_is_read_again_with_its_questions_key_as_it_is_now():
    from trace_lab import answer_key as ak
    from trace_lab import auto_expected as ax

    q = "Which buildings put me at risk this month?"
    plan = ak.KEYS["buildings_at_risk"].plan({})

    def draft(question, first):
        return {"question": question, "evidence": {"drafted_from": "records", "queries": [first]}}
    assert "IN ('proposed', 'answer_key')" in ax._PROPOSED_VALUES and "_stale_key" in inspect.getsource(ax.draft_for_live)
    # a proposal, once its question has a key
    assert ax._stale_key(draft(q, {"source": "proposed"})) is True
    assert ax._stale_key(draft("Show me everything that's at risk", {"source": "proposed"})) is False
    # a key draft from before key_plan was kept: only a revised key's
    assert ax._stale_key(draft(q, {"source": "answer_key"})) is True
    assert ax._stale_key(draft("Which vendors are blocked?", {"source": "answer_key"})) is False
    # a key draft that kept its plan: read again only when the key's plan has changed since
    assert ax._stale_key(draft(q, {"source": "answer_key", "key_plan": plan})) is False
    old = dict(plan, focus=dict(plan["focus"], filters=[]))
    assert ax._stale_key(draft(q, {"source": "answer_key", "key_plan": old})) is True
    # Hoistra's own query is never put over
    assert ax._stale_key(draft(q, {"source": "hoistra"})) is False
    assert "key_plan" in inspect.getsource(ax._read_values)


# ── Learning ─────────────────────────────────────────────────────────────────────────────────────────

def test_the_learning_draft_of_a_consultant_is_drawn_from_their_own_questions():
    p = person(access_role="consultant")
    who = run(routes_scenarios._learner_scope(None, p))
    assert who == routes_scenarios.Learner(ORG, str(p.user_id))
    sql = learning._own_turns_sql()
    assert "oq.created_by = CAST(:scope_uid AS uuid)" in sql and "os.user_id = CAST(:scope_uid AS uuid)" in sql
    assert "own_uid" in inspect.signature(learning.gather).parameters


def test_the_learning_draft_still_needs_a_reviewer_for_everyone_else():
    with pytest.raises(HTTPException) as e:
        run(routes_scenarios._learner_scope(None, person()))
    assert e.value.status_code == 403


# ── what they may call ──────────────────────────────────────────────────────────────────────────────

def req(method, path):
    return SimpleNamespace(method=method, scope={"route": SimpleNamespace(path="/api/trace-lab" + path)})


ALLOWED = [
    ("GET", "/lab/me"),
    ("GET", "/eval/run-tags"),
    ("GET", "/eval/sessions"),
    ("GET", "/eval/sessions/{session_id}"),
    ("GET", "/eval/traces/{trace_id}/tree"),
    ("GET", "/eval/traces/{trace_id}/as-shown"),
    ("GET", "/eval/traces/{trace_id}/notes"),
    ("GET", "/eval/traces/{trace_id}/labels"),
    ("PUT", "/eval/traces/{trace_id}/labels/{mode}"),
    ("PUT", "/eval/traces/{trace_id}/scores/{level}"),
    ("GET", "/eval/traces/{trace_id}/expected-flag"),
    ("PUT", "/eval/traces/{trace_id}/expected-flag"),
    ("GET", "/lab/turns/{turn_id}/step-labels"),
    ("PUT", "/lab/turns/{turn_id}/step-labels"),
    ("DELETE", "/lab/turns/{turn_id}/step-labels"),
    ("PUT", "/lab/turns/{turn_id}/step-label-groups"),
    ("DELETE", "/lab/turns/{turn_id}/step-label-groups/{group_id}"),
    # the rest of the workspace, each read narrowed to their own questions
    ("GET", "/lab/sync-status"),
    ("GET", "/lab/runs"),
    ("POST", "/lab/runs/sync"),
    ("GET", "/lab/runs/{code}"),
    ("GET", "/lab/runs/{code}/questions"),
    ("GET", "/lab/runs/{code}/sessions"),
    ("GET", "/lab/q/{code}"),
    ("GET", "/lab/q/{code}/tree"),
    ("PUT", "/lab/q/{code}/expected"),
    ("PUT", "/lab/q/{code}/cells"),
    ("GET", "/lab/turns/{turn_id}/expected"),
    ("POST", "/lab/turns/{turn_id}/expected/check"),
    ("GET", "/lab/turns/{turn_id}/expected/query"),
    ("GET", "/lab/labels"),
    ("GET", "/lab/runs/{code}/labels"),
    ("GET", "/lab/progress"),
    ("GET", "/lab/modes"),
    ("POST", "/lab/modes"),
    ("PUT", "/lab/modes/{lineage_id}"),
    ("DELETE", "/lab/modes/{lineage_id}"),
    ("GET", "/lab/step-labels"),
    ("GET", "/lab/step-labels.csv"),
    ("GET", "/lab/loop"),
    ("GET", "/lab/proposals"),
    ("POST", "/lab/proposals/{kind}/{subject_id}"),
    ("GET", "/lab/spec"),
    ("GET", "/lab/spec.md"),
]
REFUSED = [
    # people, the company's runs and answers, the platform's tools and every curator's action
    ("GET", "/lab/members"), ("PUT", "/lab/members/{user_id}"), ("GET", "/lab/platform-members"),
    ("PUT", "/lab/platform-members"), ("GET", "/lab/questions"), ("GET", "/lab/questions/{key:path}"),
    ("POST", "/lab/runs"), ("POST", "/lab/runs/import"), ("POST", "/lab/q/{code}/traces"),
    ("GET", "/lab/q/{code}/candidates"), ("POST", "/lab/turns/{turn_id}/expected/query"),
    ("GET", "/lab/spec/draft"), ("POST", "/lab/spec"), ("GET", "/lab/corrections"), ("POST", "/lab/corrections"),
    ("POST", "/lab/modes/{lineage_id}/regression"), ("POST", "/lab/runs/{code}/rerun"), ("GET", "/lab/runs/{code}/compare"),
    ("POST", "/lab/agent/propose-from-notes"), ("GET", "/lab/changes"), ("POST", "/lab/changes/{overlay_id}/approve"),
    ("POST", "/eval/ingest"), ("POST", "/eval/evaluate"), ("GET", "/eval/compare"), ("POST", "/eval/sync"),
    ("GET", "/eval/scenarios"),
    # a method the list does not name for a path it does
    ("DELETE", "/eval/sessions/{session_id}"),
]


@pytest.mark.parametrize("method,path", ALLOWED)
def test_a_consultant_may_use_the_workspace_on_their_own_questions(method, path):
    labelling.require_route(consultant_member(), req(method, path))


@pytest.mark.parametrize("method,path", REFUSED)
def test_everything_else_is_refused_for_a_consultant(method, path):
    with pytest.raises(HTTPException) as e:
        labelling.require_route(consultant_member(), req(method, path))
    assert e.value.status_code == 403
    assert e.value.detail["reason"] == "own_sessions_only"


def test_a_route_with_no_name_is_refused():
    with pytest.raises(HTTPException):
        labelling.require_route(consultant_member(), SimpleNamespace(method="GET", scope={}))


@pytest.mark.parametrize("method,path", REFUSED[:5])
def test_the_route_list_holds_nobody_else_back(method, path):
    labelling.require_route(labelling.Member(person(), ORG, "u", "e", "tester", "active", False), req(method, path))


def test_every_route_a_consultant_may_call_exists():
    """The list names real routes: a renamed route would otherwise be refused without anyone noticing."""
    from trace_lab import routes_eval, routes_labelling, routes_loop, routes_runs

    have = set()
    for r in (routes_eval.router, routes_labelling.router, routes_runs.router, routes_loop.router):
        for route in r.routes:
            for method in getattr(route, "methods", ()) or ():
                have.add((method, route.path))
    for method, path in ALLOWED:
        assert (method, path) in have, (method, path)
