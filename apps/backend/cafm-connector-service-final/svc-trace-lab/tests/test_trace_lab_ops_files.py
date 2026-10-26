"""svc-trace-lab/ops: the database set-up SQL and the runbook (files only; nothing in them is executed by
any test or by the service).

* ops/create_database.sql holds exactly one executable statement, CREATE DATABASE hoist_tracelab. The
  optional SELECT-only role for the main database is documented in ops/README.md instead, because the
  Golden Rule guard (tests/test_trace_lab_golden_rule.py) refuses any privilege statement on a
  plenum_cafm object in a .sql file under this folder, comments included.
* ops/README.md is the ordered runbook. These tests keep it in step with the code it names: the compose
  files, the migrate commands and the highest migration, and the tabs the click-test must visit.

Tests that read other parts of the repository skip where the repository's deploy/ folder is absent (the
testenv copy of svc-trace-lab); run them on the host:

    cd apps/backend/cafm-connector-service-final/svc-trace-lab
    python3 -m pytest tests/test_trace_lab_ops_files.py -q -p no:cacheprovider
"""
import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
TL_ROOT = HERE.parents[1]  # svc-trace-lab
OPS = TL_ROOT / "ops"
SQL = OPS / "create_database.sql"
README = OPS / "README.md"

TABS = ("Conversations", "Runs", "Golden set", "Model calls", "Map", "Progress", "Labels", "Judges", "Watch")
MAIN_TABLES = ("agent_trace_turns", "agent_trace_spans", "chat_threads", "agent_activity_log")


def _repo_root() -> Path | None:
    for parent in HERE.parents:
        if (parent / "deploy" / "allinone" / "supervisord.conf").is_file():
            return parent
    return None


ROOT = _repo_root()
needs_repo = pytest.mark.skipif(ROOT is None, reason="the repository's deploy/ folder is not beside this copy")


def _sql_statements(text: str) -> list[str]:
    """The executable statements of a SQL file: comments (-- to end of line) removed, split on ;."""
    uncommented = "\n".join(re.sub(r"--.*$", "", line) for line in text.splitlines())
    return [" ".join(part.split()) for part in uncommented.split(";") if part.strip()]


def _steps(text: str) -> list[tuple[int, str]]:
    return [(int(m.group(1)), m.group(2).strip()) for m in re.finditer(r"^## (\d+)\. (.+)$", text, re.MULTILINE)]


def _section(text: str, number: int) -> str:
    start = re.search(rf"^## {number}\. .+$", text, re.MULTILINE)
    assert start, f"step {number} is missing"
    nxt = re.search(r"^## ", text[start.end():], re.MULTILINE)
    return text[start.start(): start.end() + nxt.start()] if nxt else text[start.start():]


# ── create_database.sql ───────────────────────────────────────────────────────────────────


def test_the_sql_is_one_create_database_statement():
    assert _sql_statements(SQL.read_text(encoding="utf-8")) == ["CREATE DATABASE hoist_tracelab"]


def test_the_sql_comments_name_the_owner_and_the_encoding():
    text = SQL.read_text(encoding="utf-8")
    comments = " ".join(line for line in text.splitlines() if line.lstrip().startswith("--")).lower()
    assert "owner" in comments and "encoding" in comments and "utf8" in comments.replace("-", "")


def test_the_sql_touches_nothing_in_the_main_database():
    text = SQL.read_text(encoding="utf-8").lower()
    for word in ("plenum_cafm", "grant", "revoke", "create role", "tracelab_ro"):
        assert word not in text, f"{word} (the Golden Rule guard also reads comments)"
    statements = " ".join(_sql_statements(text))
    for word in ("drop ", "alter ", "insert ", "update ", "delete ", "truncate "):
        assert word not in statements, word


def test_the_statement_scanner_sees_more_than_one_statement():
    # The one-statement check above must not pass because the scanner reads nothing.
    assert _sql_statements("-- c\nCREATE DATABASE a; -- x\nDROP DATABASE b;\n") == ["CREATE DATABASE a", "DROP DATABASE b"]


# ── README.md ─────────────────────────────────────────────────────────────────────────────


def test_the_runbook_has_the_eight_steps_in_order():
    steps = _steps(README.read_text(encoding="utf-8"))
    assert [n for n, _ in steps] == [1, 2, 3, 4, 5, 6, 7, 8]
    titles = " ".join(t for _, t in steps).lower()
    for word in ("create the database", "dsn", "start", "migrat", "frontend", "click", "azure", "roll"):
        assert word in titles, word


def test_the_runbook_says_plainly_that_no_claude_session_runs_steps_1_3_4_or_7():
    text = README.read_text(encoding="utf-8")
    assert re.search(r"No Claude session runs step(?:s)? 1, 3, 4 or 7", text)
    for number in (1, 3, 4, 7):
        assert "Run by Hussain" in _section(text, number), number


def test_the_runbook_names_the_environment_the_service_reads():
    text = README.read_text(encoding="utf-8")
    for name in ("TRACE_LAB_DB_DSN", "TRACE_LAB_DB_URL", "ANTHROPIC_API_KEY", "TRACE_LAB_ENABLED", "VITE_TRACE_LAB",
                 "TRACE_LAB_SCHEMA"):
        assert name in text, name


PROXY_VALUES = {
    "UDR_BASE_URL": "http://127.0.0.1:8010/__proxy/udr",
    "OPERATIONS_INTELLIGENCE_BASE_URL": "http://127.0.0.1:8010/__proxy/oi",
    "WO_MANAGEMENT_BASE_URL": "http://127.0.0.1:8010/__proxy/wom",
    "DOC_RAG_BASE_URL": "http://127.0.0.1:8010/__proxy/docrag",
    "TRACE_LAB_UPSTREAM_UDR": "http://127.0.0.1:8006",
    "TRACE_LAB_UPSTREAM_OI": "http://127.0.0.1:8009",
    "TRACE_LAB_UPSTREAM_WOM": "http://127.0.0.1:8007",
    "TRACE_LAB_UPSTREAM_DOCRAG": "http://127.0.0.1:8004",
}


def _readme_value(step: str, name: str) -> list[str]:
    """Every `NAME=http://...` the step states for name (backticks, quotes and punctuation stripped)."""
    return re.findall(rf"\b{name}=\"?(http://[^\s`\",;)]+)", step)


def test_the_azure_step_names_prerequisite_p1_and_the_switches():
    step = _section(README.read_text(encoding="utf-8"), 7)
    assert "P1" in step
    assert "TRACE_LAB_ENABLED=1" in step
    assert "TRACE_LAB_DB_URL" in step


def test_the_azure_step_states_the_proxy_wiring_and_no_longer_calls_golden_runs_unsupported():
    step = _section(README.read_text(encoding="utf-8"), 7)
    flat = " ".join(step.split())
    for name, value in PROXY_VALUES.items():
        assert _readme_value(step, name) == [value], f"step 7 must state {name}={value} exactly once"
    assert "environment=" in step and "allow-list proxy" in flat
    assert "17 Oct" in flat, "the approval is dated"
    assert "do not set these eight in the portal" in flat.lower(), "the program's own line wins: a portal value is ignored"
    # The old limit is gone: golden runs are no longer said to refuse on Azure.
    for stale in ("Known limit", "Until Hussain approves", "none of them can be pointed", "four more entries"):
        assert stale not in flat, stale


def test_the_azure_step_does_not_ask_for_the_upstream_variables_in_the_portal():
    match = re.search(r"^2\. In the Container App.*?(?=^3\. )", _section(README.read_text(encoding="utf-8"), 7),
                      re.MULTILINE | re.DOTALL)
    assert match, "step 7 has no item 2 for the portal variables"
    flat = " ".join(match.group(0).split())
    assert "TRACE_LAB_ENABLED=1" in flat and "TRACE_LAB_DB_URL" in flat and "TRACE_LAB_SCHEMA=verify" in flat
    assert "TRACE_LAB_UPSTREAM" not in flat and "_BASE_URL=" not in flat.replace("TRACE_LAB_DB_URL", "")


@needs_repo
def test_the_azure_step_values_are_the_ones_the_supervisord_line_carries():
    import configparser

    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.read_string((ROOT / "deploy" / "allinone" / "supervisord.conf").read_text(encoding="utf-8"))
    env = dict(re.findall(r'(\w+)="([^"]*)"', parser["program:trace-lab"]["environment"]))
    step = _section(README.read_text(encoding="utf-8"), 7)
    for name in PROXY_VALUES:
        assert _readme_value(step, name) == [env[name]], name


def test_the_rollback_unsets_both_switches():
    step = _section(README.read_text(encoding="utf-8"), 8)
    assert "VITE_TRACE_LAB" in step and "TRACE_LAB_ENABLED" in step


def test_the_optional_read_role_covers_exactly_the_four_main_tables():
    text = README.read_text(encoding="utf-8")
    block = re.search(r"```sql\n((?:(?!```).)*?CREATE ROLE tracelab_ro(?:(?!```).)*?)```", text, re.DOTALL)
    assert block, "the optional tracelab_ro block is missing"
    granted = set(re.findall(r"plenum_cafm\.(\w+)", " ".join(
        line for line in block.group(1).splitlines() if not line.lstrip().startswith("--"))))
    granted.discard("")
    assert granted == set(MAIN_TABLES)
    assert "SELECT" in block.group(1)
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALL PRIVILEGES)\b", block.group(1))


def test_the_runbook_says_the_four_table_role_does_not_cover_golden_runs():
    text = README.read_text(encoding="utf-8").lower()
    assert "golden run" in text and "permission denied" in text


def _role_section(text: str) -> str:
    start = re.search(r"^## Optional: a SELECT-only role.+$", text, re.MULTILINE)
    assert start, "the optional role section is missing"
    nxt = re.search(r"^## ", text[start.end():], re.MULTILINE)
    return text[start.start(): start.end() + nxt.start()] if nxt else text[start.start():]


def test_the_runbook_says_the_four_table_role_also_blocks_the_accounts_read_that_sampling_makes():
    """sampling.py reads plenum_cafm.users (superadmin ids, asker roles) over the same main connection and treats a
    refused read as no rows, so with the four-table role the R20 view-as rule silently stops applying: the runbook
    must say so, and must not call the role fine for the sampled evidence (Map, review batches, Monitor)."""
    section = _role_section(README.read_text(encoding="utf-8"))
    flat = " ".join(section.split())
    assert "plenum_cafm.users" in flat, "the role section must name the accounts table that sampling reads"
    for word in ("Map", "superadmin", "view-as", "silently"):
        assert word in flat, f"the role section does not mention {word!r}"
    assert "Hussain" in flat and "approv" in flat, "a wider grant needs Hussain's approval, and the section must say so"
    assert "fine for" not in flat.lower(), "the role must not be described as fine for the sampled evidence"


def test_the_accounts_table_the_runbook_names_is_the_one_sampling_reads():
    """Keep the README's table name in step with the code: sampling.USERS is the only accounts read."""
    source = (TL_ROOT / "trace_lab" / "sampling.py").read_text(encoding="utf-8")
    assert re.search(r'^USERS = "plenum_cafm\.users"$', source, re.MULTILINE)
    assert "plenum_cafm.users" in README.read_text(encoding="utf-8")


def test_the_click_test_visits_every_tab_and_the_two_other_surfaces():
    step = _section(README.read_text(encoding="utf-8"), 6)
    for tab in TABS:
        assert f"- [ ] **{tab}**" in step, f"the checklist has no item for the {tab} tab"
    assert "Platform cost" in step and "Administration" in step


@needs_repo
def test_the_click_test_covers_the_tabs_the_frontend_has():
    shell = (ROOT / "apps" / "frontend" / "src" / "tracelab" / "logic" / "shell.js").read_text(encoding="utf-8")
    labels = re.search(r"TAB_LABELS = Object\.freeze\(\{(.*?)\}\)", shell, re.DOTALL)
    assert labels
    found = re.findall(r"\w+: '([^']+)'", labels.group(1))
    assert tuple(found) == TABS, "the page's tabs changed: update the click-test checklist and TABS here"


@needs_repo
def test_the_compose_command_names_real_files_a_real_service_and_a_real_profile():
    step = _section(README.read_text(encoding="utf-8"), 3)
    command = re.search(r"docker compose -p hoistra_plenum.*?trace-lab", step, re.DOTALL)
    assert command, "the compose command is missing"
    flat = " ".join(command.group(0).replace("\\\n", " ").split())
    files = re.findall(r"-f (\S+)", flat)
    assert files == ["docker-compose.single-url.local.yml", "docker-compose.azure-safe.yml", "docker-compose.tracelab.yml"]
    for name in files:
        assert (ROOT / name).is_file(), name
    assert "--profile tracelab" in flat and "up -d --build" in flat
    compose = (ROOT / "docker-compose.tracelab.yml").read_text(encoding="utf-8")
    assert re.search(r"^  trace-lab:\n    profiles: \[\"tracelab\"\]", compose, re.MULTILINE)


def test_the_migrate_commands_exist_and_apply_runs_through_the_last_migration():
    step = _section(README.read_text(encoding="utf-8"), 4)
    cli = (TL_ROOT / "trace_lab" / "migrate.py").read_text(encoding="utf-8")
    for sub in ("status", "plan", "apply"):
        assert f"python -m trace_lab.migrate {sub}" in step, sub
        assert f'add_parser("{sub}"' in cli, sub
    for flag in ("--expect-db", "--through", "--applied-by", "--dry-run"):
        assert flag in step and flag in cli, flag
    head = max(int(p.name[:4]) for p in (TL_ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql"))
    assert set(re.findall(r"--through (\d{4})", step)) == {f"{head:04d}"}, "apply must run through the highest migration file"
    assert "--expect-db hoist_tracelab" in step
    assert "interactive terminal" in step and "-T" in step, "apply needs a TTY: the step says so and warns off -T"


def test_the_frontend_step_rebuilds_with_the_flag_and_reloads_the_gateway():
    step = _section(README.read_text(encoding="utf-8"), 5)
    assert "VITE_TRACE_LAB=1" in step
    assert "nginx -s reload" in step
    commands = [line for line in step.splitlines() if line.startswith("docker ")]
    build = [c for c in commands if " build --build-arg VITE_TRACE_LAB=1 frontend-app" in c]
    assert len(build) == 1, "the frontend is built once, with the flag"
    ups = [c for c in commands if " up " in c and "frontend-app" in c]
    assert len(ups) == 1 and "--build" not in ups[0], "a later up --build would rebuild the image without the flag"
    assert any(" up -d --build --no-deps gateway-app" in c for c in commands), "the gateway carries the A5 route"


def test_every_runbook_command_fits_on_one_line_for_a_paste():
    # The terminal this is pasted into collapses newlines, which breaks a backslash continuation.
    text = README.read_text(encoding="utf-8")
    for block in re.findall(r"```sh\n(.*?)```", text, re.DOTALL):
        assert "\\\n" not in block, block

def test_the_audit_section_names_the_other_sessions_files_and_how_to_prove_both_outcomes():
    text = README.read_text(encoding="utf-8")
    section = text[text.index("## The final Golden Rule audit"):]
    flat = " ".join(section.split())
    assert "CLAUDE.md" in flat and "docs" in flat, "the token-saving session's files are named"
    assert "g0_audit.sh --self-test" in flat and "g0_audit_selftest.sh" in flat
    assert "tests/test_trace_lab_g0_audit_selftest.py" in flat, "the self-test's own tests are in the run command"
    assert "PASS" in flat and "FAIL" in flat, "the section says what each outcome is"

