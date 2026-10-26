"""Golden Rule G0 guard tests for Trace Lab.

Trace Lab is a separate evaluation module that lives in its own folder, svc-trace-lab/, beside
the existing services. The Golden Rule (G0): existing application logic is never edited for it,
the existing application must not be affected by it, and existing code is reused only by
importing it. These tests keep that enforceable forever:

1. nothing in any existing service imports the trace_lab package unless the attachment is
   approved in ALLOWED_ATTACHMENTS;
2. svc-deepagents holds nothing named after Trace Lab (its src/ and tests/ stay as they were);
3. nothing in svc-trace-lab (outside tests/) writes to or changes an object in the existing
   plenum_cafm schema;
4. importing the package loads no application module;
5. meta-tests prove every scanner really catches each form it promises to catch, and that each
   guard test really fails on a violating tree, so a guard can never pass because it quietly
   stopped looking.

The scanners read source text and syntax trees only. Nothing here touches a database, the
network or a model.
"""

import ast
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
TL_ROOT = HERE.parents[1]  # svc-trace-lab
BACKEND = TL_ROOT.parent  # cafm-connector-service-final

# Approved attachments: paths relative to the backend folder (for example
# "svc-deepagents/src/api/chat.py") of existing modules that Hussain has explicitly approved to
# import Trace Lab. Empty by design: under G0 nothing in the existing application imports it.
ALLOWED_ATTACHMENTS: frozenset[str] = frozenset()

PACKAGE = "trace_lab"
EXISTING_SCHEMA = "plenum_cafm"
APP_PACKAGE = "src"  # svc-deepagents' package, the one Trace Lab reuses by import


# ---------------------------------------------------------------------------------------
# Finding the files to scan
# ---------------------------------------------------------------------------------------


def _text_lines(source: bytes) -> list[str]:
    """Split source the way the Python parser counts lines (\\n, \\r\\n and \\r only)."""
    text = source.decode("utf-8-sig", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _python_files(root: Path) -> Iterator[Path]:
    """Every *.py under root, in a stable order."""
    for path in sorted(root.rglob("*.py")):
        if path.is_file():
            yield path


def _existing_services() -> list[Path]:
    """Every sibling folder of svc-trace-lab that holds Python code.

    A service folder that is absent (the test copy may carry only some of them) is simply not
    listed; the guard tests assert that the ones they cannot do without are there.
    """
    own = TL_ROOT.resolve()
    return [
        folder
        for folder in sorted(BACKEND.iterdir())
        if folder.is_dir()
        and not folder.name.startswith((".", "__"))
        and folder.resolve() != own
        and next(_python_files(folder), None) is not None
    ]


# ---------------------------------------------------------------------------------------
# The import scanner
# ---------------------------------------------------------------------------------------


def _names_the_package(dotted: object) -> bool:
    """True when a dotted module name has the trace_lab package as one of its parts."""
    return isinstance(dotted, str) and PACKAGE in dotted.split(".")


def _dynamic_import_name(call: ast.Call) -> object:
    """The literal module name given to importlib.import_module(...) or __import__(...)."""
    func = call.func
    if isinstance(func, ast.Name):
        callee = func.id
    elif isinstance(func, ast.Attribute):
        callee = func.attr
    else:
        return None
    if callee not in ("import_module", "__import__"):
        return None
    if call.args:
        argument = call.args[0]
    else:
        argument = next((kw.value for kw in call.keywords if kw.arg == "name"), None)
    return argument.value if isinstance(argument, ast.Constant) else None


def _imports_the_package(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(_names_the_package(alias.name) for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        # `from .trace_lab import x`, `from src.trace_lab.golden import y` (the module names it)
        # and `from . import trace_lab`, `from src import trace_lab` (an imported name is it).
        return _names_the_package(node.module) or any(a.name == PACKAGE for a in node.names)
    if isinstance(node, ast.Call):
        return _names_the_package(_dynamic_import_name(node))
    return False


def _trace_lab_imports(root: Path) -> list[str]:
    """Every import of the trace_lab package in any *.py under root, as "path:line: text".

    Paths are relative to root. The scan reads the syntax tree, so a comment, a docstring or a
    string that merely mentions trace_lab is never flagged, while a multi-line import is flagged
    on its first line. A file that cannot be parsed is reported rather than skipped, because a
    guard that quietly skips a file is not a guard.
    """
    found: list[str] = []
    for path in _python_files(root):
        relative = path.relative_to(root).as_posix()
        source = path.read_bytes()
        lines = _text_lines(source)
        try:
            with warnings.catch_warnings():
                # Existing files carry invalid escape sequences (a backslash-pipe in a regex
                # string, say); parsing them must not print a SyntaxWarning on every scan.
                warnings.simplefilter("ignore")
                tree = ast.parse(source, filename=relative)
        except (SyntaxError, ValueError) as exc:
            line = getattr(exc, "lineno", None) or 1
            found.append(f"{relative}:{line}: cannot be checked ({type(exc).__name__})")
            continue
        hits = sorted({node.lineno for node in ast.walk(tree) if _imports_the_package(node)})
        found += [f"{relative}:{line}: {lines[line - 1].strip()[:200]}" for line in hits]
    return found


def _unapproved(offenders: list[str], approved: frozenset[str]) -> list[str]:
    """Drop the offenders whose file path (the part before the first colon) is approved."""
    return [offender for offender in offenders if offender.split(":", 1)[0] not in approved]


# ---------------------------------------------------------------------------------------
# The Trace Lab name scanner
# ---------------------------------------------------------------------------------------

_TRACE_LAB_NAMES = ("*trace_lab*", "*tracelab*", "*trace-lab*")


def _trace_lab_named(root: Path) -> list[str]:
    """Every file or folder under root whose name matches *trace_lab*, *tracelab* or
    *trace-lab* (case-insensitive), as a path relative to root.

    Anything inside a __pycache__ folder is left out: bytecode caches are git-ignored build
    artefacts, and a .pyc there cannot be imported without its source, so a stale one (left by a
    deleted test, say) is not Trace Lab code held by the service.
    """
    found = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts:
            continue
        name = path.name.lower()
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in _TRACE_LAB_NAMES):
            found.append(relative.as_posix())
    return sorted(found)


# ---------------------------------------------------------------------------------------
# The SQL write scanner
# ---------------------------------------------------------------------------------------

# Between two tokens of a statement: whitespace and the quote characters that identifiers and
# split-over-several-lines Python string literals put there.
_GAP = r"""[\s"'`]*"""
_MODIFIER = r"(?:IF\s+NOT\s+EXISTS|IF\s+EXISTS|ONLY|CONCURRENTLY)"
# What follows a statement keyword when the statement's object is in the existing schema.
_TARGET = rf"{_GAP}(?:{_MODIFIER}{_GAP})*{EXISTING_SCHEMA}{_GAP}\."
_KINDS = r"(?:TABLE|INDEX|VIEW|MATERIALIZED\s+VIEW|SEQUENCE|FUNCTION|TYPE)"
_NAME = r"""[\w$"]+"""

_WRITE_PATTERNS = (
    rf"INSERT\s+INTO{_TARGET}",
    rf"MERGE\s+INTO{_TARGET}",
    rf"UPDATE{_TARGET}",
    rf"DELETE\s+FROM{_TARGET}",
    rf"TRUNCATE(?:\s+TABLE)?{_TARGET}",
    rf"ALTER\s+{_KINDS}{_TARGET}",
    rf"CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:UNIQUE|UNLOGGED)\s+)?{_KINDS}{_TARGET}",
    # An index is created by name; the existing table it is built on follows ON.
    rf"CREATE\s+(?:UNIQUE\s+)?INDEX\b[^;]{{0,300}}?\bON{_TARGET}",
    rf"DROP\s+{_KINDS}{_TARGET}",
    rf"(?:DROP|ALTER)\s+SCHEMA{_GAP}(?:IF\s+EXISTS{_GAP})?{EXISTING_SCHEMA}\b",
    rf"CREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?TRIGGER\b[^;]{{0,300}}?\bON{_TARGET}",
    # COPY ... FROM loads rows into the table; COPY ... TO only reads it.
    rf"COPY{_TARGET}{_NAME}\s*(?:\([^)]*\)\s*)?FROM\b",
    rf"REFRESH\s+MATERIALIZED\s+VIEW{_TARGET}",
    rf"LOCK(?:\s+TABLE)?{_TARGET}",
    # SELECT ... INTO creates a table from a query result.
    rf"SELECT\b[^;]{{0,300}}?\bINTO{_TARGET}",
    rf"COMMENT\s+ON\s+(?:{_KINDS}|COLUMN){_TARGET}",
    # Privileges: GRANT/REVOKE ... ON [TABLE|SCHEMA|ALL TABLES IN SCHEMA] plenum_cafm[.x]
    rf"(?:GRANT|REVOKE)\b[^;]{{0,300}}?\bON{_GAP}"
    rf"(?:(?:ALL\s+\w+\s+IN\s+)?(?:TABLE|SEQUENCE|FUNCTION|SCHEMA){_GAP})?{EXISTING_SCHEMA}(?!\w)",
    rf"(?:CREATE|ALTER|DROP)\s+POLICY\b[^;]{{0,300}}?\bON{_TARGET}",
)
_WRITE_RES = tuple(re.compile(rf"(?<!\w)(?:{p})", re.IGNORECASE) for p in _WRITE_PATTERNS)


def _writes_to_existing_tables(root: Path, *, skip: Path | None = None) -> list[str]:
    """Every SQL statement in any *.py or *.sql under root that writes or changes a
    plenum_cafm object, as "path:line: text".

    Paths are relative to root; everything under `skip` (a folder inside root) is left out.
    Reads case-insensitively, whole-file, so a statement split over several lines (or over
    adjacent Python string literals) is caught, and reports the line the statement starts on.
    Reads such as SELECT and COPY ... TO are not flagged. The scan reads text, so a comment
    that spells out a write statement is flagged too; reword the comment.
    """
    found: dict[tuple[str, int], str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".py", ".sql"):
            continue
        if skip is not None and path.is_relative_to(skip):
            continue
        relative = path.relative_to(root).as_posix()
        lines = _text_lines(path.read_bytes())
        text = "\n".join(lines)
        for pattern in _WRITE_RES:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                found[(relative, line)] = lines[line - 1].strip()[:200]
    return [f"{path}:{line}: {text}" for (path, line), text in sorted(found.items())]


# ---------------------------------------------------------------------------------------
# The import probe
# ---------------------------------------------------------------------------------------


def _app_modules_loaded_by_importing(
    package: str, *, cwd: Path, search_path: list[Path]
) -> list[str]:
    """Import `package` in a fresh interpreter and list every application module (the `src`
    package or anything under it) that the import loaded.

    The folders in search_path are put on PYTHONPATH, so when svc-deepagents is among them an
    accidental `from src... import` in the package really loads the module (and is reported)
    instead of failing for want of a path.
    """
    code = (
        "import json, sys\n"
        f"import {package}\n"
        "print(json.dumps(sorted(\n"
        f"    name for name in sys.modules if name == {APP_PACKAGE!r} "
        f"or name.startswith({APP_PACKAGE + '.'!r})\n"
        ")))\n"
    )
    paths = [str(folder) for folder in search_path]
    if os.environ.get("PYTHONPATH"):
        paths.append(os.environ["PYTHONPATH"])
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(paths), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        done = subprocess.run(
            [sys.executable, "-c", code],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        pytest.fail(f"importing {package} failed:\n{exc.stdout}\n{exc.stderr}")
    return json.loads(done.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------------------
# The guard tests
# ---------------------------------------------------------------------------------------


def test_no_existing_code_imports_trace_lab():
    services = _existing_services()
    assert "svc-deepagents" in [service.name for service in services], (
        f"svc-deepagents was not found beside svc-trace-lab in {BACKEND}; the import guard "
        "would pass vacuously"
    )
    scanned = [path for service in services for path in _python_files(service)]
    assert scanned, "the import guard found no Python files to scan; it would pass vacuously"
    offenders: list[str] = []
    for service in services:
        offenders += [f"{service.name}/{found}" for found in _trace_lab_imports(service)]
    offenders = _unapproved(offenders, ALLOWED_ATTACHMENTS)
    assert not offenders, (
        "Golden Rule G0: existing code must not import Trace Lab unless the attachment is "
        "approved. Remove these imports, or have Hussain approve the attachment and add its "
        "path (relative to the backend folder) to ALLOWED_ATTACHMENTS in "
        "tests/test_trace_lab_golden_rule.py:\n  " + "\n  ".join(offenders)
    )


def test_svc_deepagents_holds_nothing_of_trace_lab():
    deepagents = BACKEND / "svc-deepagents"
    assert (deepagents / "src").is_dir(), (
        f"{deepagents / 'src'} is missing; the svc-deepagents guard would pass vacuously"
    )
    offenders: list[str] = []
    for folder in ("src", "tests"):
        root = deepagents / folder
        if root.is_dir():
            offenders += [f"svc-deepagents/{folder}/{found}" for found in _trace_lab_named(root)]
    assert not offenders, (
        "Golden Rule G0: svc-deepagents must hold nothing of Trace Lab (its existing test suite's "
        "baseline must not change). Move these into svc-trace-lab:\n  " + "\n  ".join(offenders)
    )


def test_trace_lab_writes_no_existing_table():
    package = TL_ROOT / PACKAGE
    assert package.is_dir() and any(package.rglob("*.py")), (
        f"{package} has no Python files; the write guard would pass vacuously"
    )
    offenders = _writes_to_existing_tables(TL_ROOT, skip=TL_ROOT / "tests")
    assert not offenders, (
        "Golden Rule G0: Trace Lab must not write to or change any plenum_cafm object (it may "
        "only read them); it keeps its own data in its own schema:\n  " + "\n  ".join(offenders)
    )


def test_importing_trace_lab_loads_no_app_module():
    loaded = _app_modules_loaded_by_importing(
        PACKAGE, cwd=TL_ROOT, search_path=[TL_ROOT, BACKEND / "svc-deepagents"]
    )
    assert loaded == [], f"importing {PACKAGE} loaded application modules: {loaded}"


# ---------------------------------------------------------------------------------------
# Meta-tests: the import scanner
# ---------------------------------------------------------------------------------------


def test_the_import_guard_catches_each_form(tmp_path):
    forms = [
        "import trace_lab",
        "import trace_lab as tl",
        "from trace_lab import x",
        "from trace_lab.golden import y",
        "from .trace_lab import x",
        "from ..trace_lab.golden import y",
        "from . import trace_lab",
        "from .. import trace_lab as tl",
        "from .trace_lab import *",
        "import src.trace_lab",
        "import src.trace_lab.golden as g",
        "from src.trace_lab import z",
        "from src.trace_lab.golden import z",
        "from src import trace_lab",
        "from src import trace_lab as tl",
        "from src import a, trace_lab",
        "from src import (\n    a,\n    trace_lab,\n)",
        "importlib.import_module('src.trace_lab')",
        'importlib.import_module("src.trace_lab.golden")',
        "import_module('.trace_lab', package='src')",
        "import_module(name='src.trace_lab')",
        "__import__('src.trace_lab')",
    ]
    for index, form in enumerate(forms):
        (tmp_path / f"form_{index}.py").write_text(f"x = 1\n{form}\ny = 2\n", encoding="utf-8")
    found = _trace_lab_imports(tmp_path)
    expected = [f"form_{index}.py:2: {form.splitlines()[0]}" for index, form in enumerate(forms)]
    assert sorted(found) == sorted(expected)


def test_the_import_guard_does_not_flag_a_mere_mention(tmp_path):
    (tmp_path / "decoys.py").write_text(
        "# from src.trace_lab import z\n"
        '"""Docs.\n'
        "import src.trace_lab\n"
        "from . import trace_lab\n"
        '"""\n'
        "note = 'from src.trace_lab import z'\n"
        'other = "import src.trace_lab"\n'
        "trace_lab = 1\n"
        "label = 'trace_lab'\n"
        "import src.trace_lab_notes\n"
        "from src.notes_trace_lab import q\n"
        "from src import trace_lab_helpers\n"
        "from .trace_labs import w\n"
        "from src.agents import planner\n"
        "importlib.import_module('src.agents.planner')\n"
        "importlib.import_module(some_name)\n"
        "import_module()\n"
        "print(trace_lab)\n",
        encoding="utf-8",
    )
    assert _trace_lab_imports(tmp_path) == []


def test_the_import_guard_reports_paths_relative_to_the_scanned_folder(tmp_path):
    (tmp_path / "src" / "api").mkdir(parents=True)
    (tmp_path / "src" / "api" / "chat.py").write_text(
        "import os\nfrom trace_lab import x\n", encoding="utf-8"
    )
    (tmp_path / "src" / "api" / "other.py").write_text("import os\n", encoding="utf-8")
    assert _trace_lab_imports(tmp_path) == ["src/api/chat.py:2: from trace_lab import x"]


def test_the_import_guard_never_skips_a_file_it_cannot_read(tmp_path):
    (tmp_path / "broken.py").write_text(
        "def broken(:\n    import src.trace_lab\n", encoding="utf-8"
    )
    found = _trace_lab_imports(tmp_path)
    assert len(found) == 1 and found[0].startswith("broken.py:1: ")
    assert "cannot be checked" in found[0]


def test_the_import_guard_reads_files_with_invalid_escapes_quietly(tmp_path):
    (tmp_path / "escapes.py").write_text(
        'PATTERN = "a\\|b"\nimport src.trace_lab\n', encoding="utf-8"
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        found = _trace_lab_imports(tmp_path)
    assert found == ["escapes.py:2: import src.trace_lab"]
    assert [str(w.message) for w in caught] == []


def test_the_allow_list_exempts_only_the_approved_path():
    offenders = [
        "svc-deepagents/src/api/chat.py:3: from ..trace_lab import x",
        "svc-deepagents/src/api/chat.py:9: import src.trace_lab",
        "svc-udr/src/other.py:1: import src.trace_lab",
    ]
    assert _unapproved(offenders, frozenset()) == offenders
    approved = frozenset({"svc-deepagents/src/api/chat.py"})
    assert _unapproved(offenders, approved) == [offenders[2]]
    assert _unapproved(offenders, frozenset({"svc-deepagents/src/api"})) == offenders


# ---------------------------------------------------------------------------------------
# Meta-tests: the SQL write scanner
# ---------------------------------------------------------------------------------------


def test_the_write_guard_catches_each_statement(tmp_path):
    statements = [
        "INSERT INTO plenum_cafm.work_orders (id) VALUES (1);",
        "insert into plenum_cafm.work_orders (id) values (1);",
        'INSERT INTO "plenum_cafm"."work_orders" (id) VALUES (1);',
        "UPDATE plenum_cafm.work_orders SET status = 'x';",
        "update only plenum_cafm.work_orders set status = 'x';",
        "DELETE FROM plenum_cafm.work_orders;",
        "DELETE FROM ONLY plenum_cafm.work_orders WHERE id = 1;",
        "ALTER TABLE plenum_cafm.work_orders ADD COLUMN note text;",
        "ALTER TABLE IF EXISTS ONLY plenum_cafm.work_orders DROP COLUMN note;",
        "CREATE TABLE plenum_cafm.new_thing (id int);",
        "CREATE TABLE IF NOT EXISTS plenum_cafm.new_thing (id int);",
        "CREATE UNIQUE INDEX uq_wo ON plenum_cafm.work_orders (id);",
        "CREATE INDEX IF NOT EXISTS ix_wo ON ONLY plenum_cafm.work_orders (id);",
        "CREATE INDEX CONCURRENTLY ix_wo ON plenum_cafm.work_orders (id);",
        "DROP TABLE plenum_cafm.work_orders;",
        "DROP TABLE IF EXISTS plenum_cafm.work_orders CASCADE;",
        "DROP INDEX plenum_cafm.ix_wo;",
        "DROP SCHEMA plenum_cafm CASCADE;",
        'drop schema if exists "plenum_cafm";',
        "TRUNCATE plenum_cafm.work_orders;",
        "TRUNCATE TABLE ONLY plenum_cafm.work_orders RESTART IDENTITY;",
        "CREATE TRIGGER trg BEFORE UPDATE ON plenum_cafm.work_orders EXECUTE FUNCTION f();",
        "CREATE OR REPLACE TRIGGER t AFTER INSERT ON plenum_cafm.work_orders EXECUTE FUNCTION f();",
        "COPY plenum_cafm.work_orders FROM STDIN;",
        "COPY plenum_cafm.work_orders (id, status) FROM '/tmp/x.csv' CSV;",
        "MERGE INTO plenum_cafm.work_orders t USING s ON t.id = s.id WHEN MATCHED THEN DELETE;",
        "ALTER SCHEMA plenum_cafm RENAME TO old_cafm;",
        "CREATE VIEW plenum_cafm.v AS SELECT 1;",
        "CREATE OR REPLACE FUNCTION plenum_cafm.f() RETURNS int AS 'select 1' LANGUAGE sql;",
        "DROP VIEW IF EXISTS plenum_cafm.v;",
        "REFRESH MATERIALIZED VIEW CONCURRENTLY plenum_cafm.mv;",
        "LOCK TABLE plenum_cafm.work_orders IN ACCESS EXCLUSIVE MODE;",
        "SELECT id INTO plenum_cafm.work_orders_copy FROM somewhere;",
        "COMMENT ON TABLE plenum_cafm.work_orders IS 'x';",
        "COMMENT ON COLUMN plenum_cafm.work_orders.note IS 'x';",
        "GRANT SELECT ON plenum_cafm.work_orders TO reader;",
        "GRANT USAGE ON SCHEMA plenum_cafm TO reader;",
        "GRANT SELECT ON ALL TABLES IN SCHEMA plenum_cafm TO reader;",
        "REVOKE ALL ON plenum_cafm.work_orders FROM reader;",
        "CREATE POLICY p ON plenum_cafm.work_orders USING (true);",
        "DROP POLICY p ON plenum_cafm.work_orders;",
        "ALTER POLICY p ON plenum_cafm.work_orders USING (true);",
    ]
    for index, statement in enumerate(statements):
        (tmp_path / f"statement_{index}.sql").write_text(
            f"-- a migration\nSELECT 1;\n{statement}\n", encoding="utf-8"
        )
    found = _writes_to_existing_tables(tmp_path)
    assert sorted(found) == sorted(
        f"statement_{index}.sql:3: {statement}" for index, statement in enumerate(statements)
    )


def test_the_write_guard_reads_python_files_and_statements_split_over_lines(tmp_path):
    (tmp_path / "repo.py").write_text(
        "async def purge(session):\n"
        '    await session.execute(text("DELETE FROM plenum_cafm.work_orders"))\n'
        "    sql = (\n"
        '        "INSERT INTO "\n'
        '        "plenum_cafm.work_orders (id) VALUES (1)"\n'
        "    )\n"
        '    other = """\n'
        "        UPDATE\n"
        "          plenum_cafm.work_orders\n"
        "        SET status = 'x'\n"
        '    """\n',
        encoding="utf-8",
    )
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "0001.sql").write_text(
        "alter table\n  plenum_cafm.work_orders\n  add column note text;\n", encoding="utf-8"
    )
    assert _writes_to_existing_tables(tmp_path) == [
        "nested/0001.sql:1: alter table",
        'repo.py:2: await session.execute(text("DELETE FROM plenum_cafm.work_orders"))',
        'repo.py:4: "INSERT INTO "',
        "repo.py:8: UPDATE",
    ]


def test_the_write_guard_does_not_flag_reads_or_other_schemas(tmp_path):
    (tmp_path / "reads.sql").write_text(
        "SELECT * FROM plenum_cafm.agent_trace_turns;\n"
        "SELECT t.id FROM plenum_cafm.agent_trace_turns t\n"
        "  JOIN plenum_cafm.agent_trace_spans s ON s.turn_id = t.id\n"
        "  WHERE t.company_id = :company FOR UPDATE OF t;\n"
        "WITH recent AS (SELECT id FROM plenum_cafm.agent_trace_turns) SELECT * FROM recent;\n"
        "COPY plenum_cafm.agent_trace_turns TO STDOUT;\n"
        "COPY (SELECT id FROM plenum_cafm.agent_trace_turns) TO STDOUT;\n"
        "EXPLAIN SELECT 1 FROM plenum_cafm.agent_trace_turns;\n"
        "INSERT INTO plenum_tracelab.runs (id) SELECT id FROM plenum_cafm.agent_trace_turns;\n"
        "UPDATE plenum_tracelab.runs SET note = 'plenum_cafm.agent_trace_turns';\n"
        "DELETE FROM plenum_tracelab.runs;\n"
        "ALTER TABLE plenum_tracelab.runs ADD COLUMN note text;\n"
        "CREATE TABLE plenum_tracelab.runs (turn_id uuid, last_update timestamptz);\n"
        "CREATE INDEX ix_runs ON plenum_tracelab.runs (turn_id);\n"
        "DROP TABLE plenum_tracelab.runs;\n"
        "INSERT INTO plenum_cafm_archive.old_rows (id) VALUES (1);\n"
        "INSERT INTO my_plenum_cafm.rows (id) VALUES (1);\n"
        "DROP SCHEMA plenum_cafm_archive;\n"
        "ON CONFLICT (id) DO UPDATE SET updated_at = now();\n"
        "CREATE TRIGGER trg BEFORE UPDATE ON plenum_tracelab.runs EXECUTE FUNCTION f();\n"
        "SELECT id INTO plenum_tracelab.copy FROM plenum_cafm.agent_trace_turns;\n"
        "COMMENT ON TABLE plenum_tracelab.runs IS 'plenum_cafm.work_orders';\n"
        "GRANT SELECT ON plenum_tracelab.runs TO plenum_cafm_reader;\n"
        "GRANT SELECT ON ALL TABLES IN SCHEMA plenum_tracelab TO reader;\n"
        "CREATE POLICY p ON plenum_tracelab.runs USING (true);\n",
        encoding="utf-8",
    )
    (tmp_path / "reads.py").write_text(
        'READ = "SELECT * FROM plenum_cafm.agent_trace_turns WHERE id = :id"\n'
        "def update_plenum_cafm_cache(): ...\n"
        "last_update = plenum_cafm.agent_trace_turns\n",
        encoding="utf-8",
    )
    assert _writes_to_existing_tables(tmp_path) == []


def test_the_write_guard_skips_only_the_named_folder(tmp_path):
    write = "DELETE FROM plenum_cafm.work_orders;\n"
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "fixture.py").write_text(f'SQL = """{write}"""\n', encoding="utf-8")
    (tmp_path / "trace_lab").mkdir()
    (tmp_path / "trace_lab" / "repo.py").write_text(f'SQL = """{write}"""\n', encoding="utf-8")
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001.sql").write_text(write, encoding="utf-8")
    assert _writes_to_existing_tables(tmp_path, skip=tmp_path / "tests") == [
        "migrations/0001.sql:1: DELETE FROM plenum_cafm.work_orders;",
        'trace_lab/repo.py:1: SQL = """DELETE FROM plenum_cafm.work_orders;',
    ]
    assert len(_writes_to_existing_tables(tmp_path)) == 3


# ---------------------------------------------------------------------------------------
# Meta-tests: the Trace Lab name scanner and the import probe
# ---------------------------------------------------------------------------------------


def test_the_name_guard_catches_each_spelling(tmp_path):
    (tmp_path / "trace_lab").mkdir()
    (tmp_path / "trace_lab" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "tracelab_routes.py").write_text("", encoding="utf-8")
    (tmp_path / "api" / "My_Trace_Lab.py").write_text("", encoding="utf-8")
    (tmp_path / "api" / "trace-lab.md").write_text("", encoding="utf-8")
    (tmp_path / "test_trace_lab_x.py").write_text("", encoding="utf-8")
    (tmp_path / "TraceLab").mkdir()
    assert _trace_lab_named(tmp_path) == [
        "TraceLab",
        "api/My_Trace_Lab.py",
        "api/trace-lab.md",
        "api/tracelab_routes.py",
        "test_trace_lab_x.py",
        "trace_lab",
    ]


def test_the_name_guard_does_not_flag_other_names_or_bytecode_caches(tmp_path):
    (tmp_path / "api").mkdir()
    for name in ("chat.py", "tracing.py", "trace_spans.py", "lab.py", "trace.py"):
        (tmp_path / "api" / name).write_text("", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "test_trace_lab_old.cpython-314.pyc").write_bytes(b"")
    (tmp_path / "api" / "__pycache__").mkdir()
    (tmp_path / "api" / "__pycache__" / "tracelab.cpython-312.pyc").write_bytes(b"")
    assert _trace_lab_named(tmp_path) == []


def test_the_import_probe_reports_each_application_module_a_package_loads(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "agents.py").write_text("", encoding="utf-8")
    (tmp_path / "clean_pkg").mkdir()
    (tmp_path / "clean_pkg" / "__init__.py").write_text('"""Docs."""\nimport json\n', "utf-8")
    (tmp_path / "dirty_pkg").mkdir()
    (tmp_path / "dirty_pkg" / "__init__.py").write_text("from src import agents\n", "utf-8")
    probe = {"cwd": tmp_path, "search_path": [tmp_path]}
    assert _app_modules_loaded_by_importing("clean_pkg", **probe) == []
    assert _app_modules_loaded_by_importing("dirty_pkg", **probe) == ["src", "src.agents"]


# ---------------------------------------------------------------------------------------
# Meta-tests: each guard test fails on a violating tree (and passes on a clean one)
# ---------------------------------------------------------------------------------------


@pytest.fixture
def fake_backend(tmp_path, monkeypatch):
    """A clean stand-in for the backend folder, swapped in for the real one."""
    backend = tmp_path / "backend"
    files = {
        "svc-trace-lab/trace_lab/__init__.py": '"""Docs."""\n',
        "svc-trace-lab/tests/test_x.py": "def test_x(): ...\n",
        "svc-deepagents/src/__init__.py": "",
        "svc-deepagents/src/api/chat.py": "import os\n",
        "svc-deepagents/tests/test_chat.py": "def test_chat(): ...\n",
        "svc-udr/src/main.py": "import os\n",
    }
    for relative, content in files.items():
        path = backend / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "BACKEND", backend)
    monkeypatch.setattr(module, "TL_ROOT", backend / "svc-trace-lab")
    return backend


def test_the_import_guard_test_fails_on_an_offender_and_honours_the_allow_list(
    fake_backend, monkeypatch
):
    test_no_existing_code_imports_trace_lab()  # a clean tree passes
    offender = fake_backend / "svc-udr" / "src" / "main.py"
    offender.write_text("import os\nfrom trace_lab import golden\n", encoding="utf-8")
    with pytest.raises(AssertionError) as raised:
        test_no_existing_code_imports_trace_lab()
    message = str(raised.value)
    sentence = (
        "Golden Rule G0: existing code must not import Trace Lab unless the attachment is approved"
    )
    assert sentence in message
    assert "svc-udr/src/main.py:2: from trace_lab import golden" in message
    monkeypatch.setattr(
        sys.modules[__name__], "ALLOWED_ATTACHMENTS", frozenset({"svc-udr/src/main.py"})
    )
    test_no_existing_code_imports_trace_lab()  # the approved attachment passes


def test_the_import_guard_test_fails_when_svc_deepagents_is_missing(fake_backend):
    shutil.rmtree(fake_backend / "svc-deepagents")
    with pytest.raises(AssertionError, match="svc-deepagents was not found"):
        test_no_existing_code_imports_trace_lab()


def test_the_deepagents_guard_test_fails_on_a_trace_lab_file(fake_backend):
    test_svc_deepagents_holds_nothing_of_trace_lab()  # a clean tree passes
    (fake_backend / "svc-deepagents" / "src" / "trace_lab").mkdir()
    (fake_backend / "svc-deepagents" / "tests" / "test_trace_lab_golden_rule.py").write_text(
        "", encoding="utf-8"
    )
    with pytest.raises(AssertionError) as raised:
        test_svc_deepagents_holds_nothing_of_trace_lab()
    message = str(raised.value)
    assert "Golden Rule G0: svc-deepagents must hold nothing of Trace Lab" in message
    assert "svc-deepagents/src/trace_lab" in message
    assert "svc-deepagents/tests/test_trace_lab_golden_rule.py" in message


def test_the_write_guard_test_fails_on_a_plenum_cafm_write_but_not_in_tests(fake_backend):
    test_trace_lab_writes_no_existing_table()  # a clean tree passes
    tl_root = fake_backend / "svc-trace-lab"
    sql = 'SQL = "DELETE FROM plenum_cafm.work_orders"\n'
    (tl_root / "tests" / "fixture.py").write_text(sql, encoding="utf-8")
    test_trace_lab_writes_no_existing_table()  # tests/ may spell statements out
    (tl_root / "trace_lab" / "repo.py").write_text(sql, encoding="utf-8")
    with pytest.raises(AssertionError) as raised:
        test_trace_lab_writes_no_existing_table()
    message = str(raised.value)
    assert "Golden Rule G0: Trace Lab must not write to or change any plenum_cafm object" in message
    assert 'trace_lab/repo.py:1: SQL = "DELETE FROM plenum_cafm.work_orders"' in message
