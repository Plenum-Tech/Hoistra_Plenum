"""Migration 0014, the eval store (11 Nov 2026): sessions, traces, observations and scores named as Langfuse names
them, beside run tags, versioned scenarios, the sections of the specification and the log of pushes to another
Trace Lab database.

Every table is append-only, because that is what makes copying one Trace Lab database into another safe: a row is
either there or it is not, and nothing is ever rewritten. The checks hold the values the task plan fixes (the three
score levels, the four scenario types, the trace statuses), so a typo cannot be stored.
Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import migrate

EVAL_TABLES = {"run_tags", "spec_sections", "scenarios", "sessions", "traces", "observations", "scores", "sync_log"}
ORG = str(uuid.uuid4())
SHA = "a" * 64


def test_0014_lints_clean():
    f = next(x for x in migrate.files() if x.name == "0014_eval_store.sql")
    assert migrate.lint(f) == []


def test_catalog_is_version_15_with_the_eight_tables_and_the_trace_labels():
    c = migrate.expected_catalog()
    assert c["version"] == 15
    assert EVAL_TABLES | {"trace_label_cells"} <= set(c["tables"])


def test_0015_lints_clean():
    f = next(x for x in migrate.files() if x.name == "0015_trace_labels.sql")
    assert migrate.lint(f) == []


async def _seed(engine) -> None:
    """One run tag, one scenario, one session, one trace: what the other rows hang from."""
    async with engine.begin() as c:
        await c.execute(text(
            "INSERT INTO tracelab.run_tags (tag, kind, origin) VALUES ('baseline_v0', 'baseline', 'test')"))
        await c.execute(text(
            "INSERT INTO tracelab.scenarios (scenario_id, version, type, spec_sections, turns, content_sha256, status, source)"
            " VALUES ('MT-001', 1, 'multi_turn', ARRAY['multi_turn_continuity'], CAST(:turns AS jsonb), :sha, 'draft',"
            " 'import')"), {"turns": '[{"message": "a"}, {"message": "b"}]', "sha": SHA})
        await c.execute(text(
            "INSERT INTO tracelab.sessions (id, organization_id, run_tag, scenario_id, scenario_version, origin, source,"
            " started_at) VALUES ('scn__baseline_v0__MT-001__1', CAST(:org AS uuid), 'baseline_v0', 'MT-001', 1, 'test',"
            " 'runner', now())"), {"org": ORG})
        await c.execute(text(
            "INSERT INTO tracelab.traces (id, session_id, turn_index, organization_id, status, header)"
            " VALUES ('turn-1', 'scn__baseline_v0__MT-001__1', 1, CAST(:org AS uuid), 'ok', CAST('{}' AS jsonb))"),
            {"org": ORG})


async def _refused(engine, sql: str, params: dict | None = None) -> bool:
    try:
        async with engine.begin() as c:
            await c.execute(text(sql), params or {})
    except DBAPIError:
        return True
    return False


pytestmark_sql = requires_scratch


@requires_scratch
async def test_the_eight_tables_exist_after_every_migration(lab_scratch):
    async with lab_scratch.connect() as conn:
        live = await migrate.read_catalog(conn)
    assert EVAL_TABLES <= set(live["tables"])


@requires_scratch
async def test_eval_tables_are_append_only(lab_scratch):
    await _seed(lab_scratch)
    for table in ("run_tags", "scenarios", "sessions", "traces"):
        assert await _refused(lab_scratch, f"UPDATE tracelab.{table} SET created_at = now()"), table
        assert await _refused(lab_scratch, f"DELETE FROM tracelab.{table}"), table
        assert await _refused(lab_scratch, f"TRUNCATE tracelab.{table} CASCADE"), table
    async with lab_scratch.connect() as c:
        assert (await c.execute(text("SELECT count(*) FROM tracelab.traces"))).scalar_one() == 1


@requires_scratch
@pytest.mark.parametrize("why, sql", [
    ("a run tag may not contain the separator of a session id",
     "INSERT INTO tracelab.run_tags (tag, kind, origin) VALUES ('base__line', 'adhoc', 'test')"),
    ("a run tag kind is baseline, post_fix or adhoc",
     "INSERT INTO tracelab.run_tags (tag, kind, origin) VALUES ('t1', 'nightly', 'test')"),
    ("a scenario type is one of the four",
     "INSERT INTO tracelab.scenarios (scenario_id, version, type, spec_sections, turns, content_sha256, status, source)"
     " VALUES ('X-1', 1, 'other', ARRAY['output_format'], CAST('[{}]' AS jsonb), repeat('a', 64), 'draft', 'import')"),
    ("a scenario has between one and six turns",
     "INSERT INTO tracelab.scenarios (scenario_id, version, type, spec_sections, turns, content_sha256, status, source)"
     " VALUES ('X-2', 1, 'failure', ARRAY['output_format'], CAST('[]' AS jsonb), repeat('a', 64), 'draft', 'import')"),
    ("a scenario names at least one section of the specification",
     "INSERT INTO tracelab.scenarios (scenario_id, version, type, spec_sections, turns, content_sha256, status, source)"
     " VALUES ('X-3', 1, 'failure', ARRAY[]::text[], CAST('[{}]' AS jsonb), repeat('a', 64), 'draft', 'import')"),
    ("a trace status is ok, error, timeout or interrupted",
     "INSERT INTO tracelab.traces (id, session_id, turn_index, organization_id, status, header)"
     " VALUES ('turn-2', 'scn__baseline_v0__MT-001__1', 2, gen_random_uuid(), 'refused', CAST('{}' AS jsonb))"),
    ("a session has one trace per turn",
     "INSERT INTO tracelab.traces (id, session_id, turn_index, organization_id, status, header)"
     " VALUES ('turn-3', 'scn__baseline_v0__MT-001__1', 1, gen_random_uuid(), 'ok', CAST('{}' AS jsonb))"),
    ("a trace belongs to a session that exists",
     "INSERT INTO tracelab.traces (id, session_id, turn_index, organization_id, status, header)"
     " VALUES ('turn-4', 'no-such-session', 1, gen_random_uuid(), 'ok', CAST('{}' AS jsonb))"),
    ("a session names a scenario version that exists",
     "INSERT INTO tracelab.sessions (id, organization_id, scenario_id, scenario_version, origin, source, started_at)"
     " VALUES ('s-2', gen_random_uuid(), 'MT-001', 9, 'test', 'runner', now())"),
    ("a session names a scenario and its version together",
     "INSERT INTO tracelab.sessions (id, organization_id, scenario_id, origin, source, started_at)"
     " VALUES ('s-3', gen_random_uuid(), 'MT-001', 'test', 'runner', now())"),
    ("an observation type is one Langfuse has",
     "INSERT INTO tracelab.observations (trace_id, id, depth, type, name, level)"
     " VALUES ('turn-1', 'o-1', 0, 'step', 'chat', 'DEFAULT')"),
    ("a score level is logic, ux or domain",
     "INSERT INTO tracelab.scores (trace_id, session_id, name, source, string_value)"
     " VALUES ('turn-1', 'scn__baseline_v0__MT-001__1', 'style', 'EVAL', 'pass')"),
    ("a score verdict is pass, fail or not_evaluated",
     "INSERT INTO tracelab.scores (trace_id, session_id, name, source, string_value)"
     " VALUES ('turn-1', 'scn__baseline_v0__MT-001__1', 'logic', 'EVAL', 'maybe')"),
    ("a score value is between 0 and 1",
     "INSERT INTO tracelab.scores (trace_id, session_id, name, source, string_value, value)"
     " VALUES ('turn-1', 'scn__baseline_v0__MT-001__1', 'logic', 'EVAL', 'pass', 1.5)"),
    ("a person's score names the person",
     "INSERT INTO tracelab.scores (trace_id, session_id, name, source, string_value)"
     " VALUES ('turn-1', 'scn__baseline_v0__MT-001__1', 'ux', 'ANNOTATION', 'pass')"),
    ("an automatic score names no person",
     "INSERT INTO tracelab.scores (trace_id, session_id, name, source, string_value, author_id)"
     " VALUES ('turn-1', 'scn__baseline_v0__MT-001__1', 'ux', 'EVAL', 'pass', gen_random_uuid())"),
])
async def test_checks_hold_the_spec_values(lab_scratch, why, sql):
    await _seed(lab_scratch)
    assert await _refused(lab_scratch, sql), why


@requires_scratch
async def test_valid_rows_of_every_table_are_stored(lab_scratch):
    await _seed(lab_scratch)
    async with lab_scratch.begin() as c:
        await c.execute(text(
            "INSERT INTO tracelab.spec_sections (spec_version, section_id, position, title, sha256, body)"
            " VALUES ('abc123def456', 'retry_rules', 1, 'Retry rules', :sha, 'Retry only temporary errors.')"), {"sha": SHA})
        await c.execute(text(
            "INSERT INTO tracelab.observations (trace_id, id, parent_observation_id, seq, depth, type, name, level,"
            " input, output, usage, metadata) VALUES ('turn-1', 'o-1', NULL, 0, 0, 'agent', 'chat', 'DEFAULT',"
            " CAST('\"q\"' AS jsonb), CAST('\"a\"' AS jsonb), CAST('{\"input\": 1}' AS jsonb), CAST('{}' AS jsonb))"))
        await c.execute(text(
            "INSERT INTO tracelab.observations (trace_id, id, parent_observation_id, seq, depth, type, name, level)"
            " VALUES ('turn-1', 'o-2', 'o-1', 1, 1, 'generation', 'final_synthesis', 'ERROR')"))
        await c.execute(text(
            "INSERT INTO tracelab.scores (trace_id, session_id, name, source, string_value, value, evaluator)"
            " VALUES ('turn-1', 'scn__baseline_v0__MT-001__1', 'logic', 'EVAL', 'pass', 1, 'logic-1')"))
        await c.execute(text(
            "INSERT INTO tracelab.scores (trace_id, session_id, name, source, string_value, value, comment, author_id,"
            " author_email, author_role) VALUES ('turn-1', 'scn__baseline_v0__MT-001__1', 'domain', 'ANNOTATION', 'fail',"
            " 0.25, 'The certificate named is the wrong one.', gen_random_uuid(), 'sme@example.com', 'domain_expert')"))
        await c.execute(text(
            "INSERT INTO tracelab.sync_log (peer, status, started_at, pushed) VALUES ('db.example.com/hoist_tracelab',"
            " 'done', now(), CAST('{\"sessions\": 1}' AS jsonb))"))
    async with lab_scratch.connect() as c:
        counts = {t: (await c.execute(text(f"SELECT count(*) FROM tracelab.{t}"))).scalar_one() for t in EVAL_TABLES}
    assert counts == {"run_tags": 1, "spec_sections": 1, "scenarios": 1, "sessions": 1, "traces": 1,
                      "observations": 2, "scores": 2, "sync_log": 1}
