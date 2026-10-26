"""Azure attachments A6, A7 and A8 (G0 addendum 3): the three deploy/allinone files carry exactly the
approved Trace Lab lines, and they agree with each other.

A6 deploy/allinone/Dockerfile      one COPY line: svc-trace-lab beside the other backend services
A7 deploy/allinone/nginx.conf      one location: /backend/trace-lab/ -> 127.0.0.1:8010
A8 deploy/allinone/supervisord.conf  [program:trace-lab]: sleeps unless TRACE_LAB_ENABLED=1; its
                                     environment= line also carries the four allow-list proxy URLs and the four
                                     proxy upstream addresses (approved by Hussain, 17 Oct), and nothing else in
                                     the file or under deploy/ differs from the base commit

These read the three files as text (and run the program's own shell command against stubs). They need
the repository's deploy/ folder, which the testenv copy of svc-trace-lab does not include, so they skip
there; run them on the host:

    cd apps/backend/cafm-connector-service-final/svc-trace-lab
    python3 -m pytest tests/test_trace_lab_azure_attachments.py -q -p no:cacheprovider
"""
import configparser
import re
import shlex
import stat
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
TL_ROOT = HERE.parents[1]  # svc-trace-lab

PORT = 8010
ROOT_PATH = "/backend/trace-lab"
COPY_LINE = "COPY ${BE}/svc-trace-lab/ svc-trace-lab/"
NGINX_LOCATION = "location /backend/trace-lab/"
SECTION = "program:trace-lab"


def _tl_source(name: str) -> str:
    return (TL_ROOT / "trace_lab" / name).read_text(encoding="utf-8")


#: What orchestrator_host.assert_isolated() demands, read from the code (not restated here).
PROXY_BASE = re.search(r'PROXY_BASE = "([^"]+)"', _tl_source("orchestrator_host.py")).group(1)
#: environment variable -> proxy service key, from orchestrator_host.BASE_URL_SETTINGS (attribute upper-cased).
PROXY_VARS = {attr.upper(): key for attr, key in re.findall(
    r'"(\w+_base_url)": "(\w+)"', _tl_source("orchestrator_host.py").split("BASE_URL_SETTINGS", 1)[1].split("}", 1)[0])}
#: proxy service key -> the variable naming where the proxy forwards, from settings.UPSTREAM_VARS.
UPSTREAM_VARS_BY_KEY = dict(re.findall(
    r'"(\w+)": "(TRACE_LAB_UPSTREAM_\w+)"', _tl_source("settings.py").split("UPSTREAM_VARS", 1)[1].split("}", 1)[0]))
UPSTREAM_ENV = tuple(UPSTREAM_VARS_BY_KEY.values())
#: proxy service key -> the [program:...] that serves it in the all-in-one container.
UPSTREAM_PROGRAM = {"udr": "udr", "oi": "ops-intelligence", "wom": "work-order", "docrag": "doc-rag"}
#: proxy service key -> the variable the image's own ENV sets to the real service.
PROXY_ENV_OF_KEY = {key: var for var, key in PROXY_VARS.items()}


def _repo_root() -> Path | None:
    for parent in HERE.parents:
        if (parent / "deploy" / "allinone" / "supervisord.conf").is_file():
            return parent
    return None


ROOT = _repo_root()
pytestmark = pytest.mark.skipif(ROOT is None, reason="the repository's deploy/ folder is not beside this copy")


def _read(name: str) -> str:
    assert ROOT is not None
    return (ROOT / "deploy" / "allinone" / name).read_text(encoding="utf-8")


def _lines(name: str) -> list[str]:
    return _read(name).splitlines()


def _supervisor() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.read_string(_read("supervisord.conf"))
    return parser


def _program_block(text: str) -> str:
    """The text of [program:trace-lab], from its header to the next section header (or the end)."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"[{SECTION}]")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    return "\n".join(lines[start:end])


def _environment(raw: str) -> dict[str, str]:
    return dict(re.findall(r'(\w+)="([^"]*)"', raw))


def _port_in(path: Path, pattern: str) -> int:
    found = re.search(pattern, path.read_text(encoding="utf-8"))
    assert found, f"{pattern} not found in {path.name}"
    return int(found.group(1))


def _without_block(text: str) -> str:
    """The file with [program:trace-lab] cut out (its header to the next section header or the end); the
    text itself when it has no such block (the base commit, before A8)."""
    if f"[{SECTION}]" not in text:
        return text
    return text.replace(_program_block(text), "")


def _git(*args: str) -> str:
    """Read-only git in the repository; the test skips when git or the base commit is not there."""
    assert ROOT is not None
    try:
        done = subprocess.run(["git", "--no-optional-locks", "-C", str(ROOT), *args], capture_output=True, text=True,
                              timeout=30)
    except FileNotFoundError:
        pytest.skip("git is not installed")
    if done.returncode != 0:
        pytest.skip(f"git could not answer ({args[0]}): {done.stderr.strip()[:120]}")
    return done.stdout


def _image_service_urls() -> dict[str, str]:
    """The image's own `ENV DOC_RAG_BASE_URL=... UDR_BASE_URL=...` block: where the real services are."""
    block = re.search(r"^ENV DOC_RAG_BASE_URL=.*?(?:\n(?!\s)|\Z)", _read("Dockerfile"), re.MULTILINE | re.DOTALL)
    assert block, "the image's service-address ENV block was not found"
    return dict(re.findall(r"(\w+_BASE_URL)=(http://127\.0\.0\.1:\d+)", block.group(0)))


# ── A6: the Dockerfile ────────────────────────────────────────────────────────────────────


def test_a6_the_dockerfile_copies_trace_lab_once():
    copies = [line for line in _lines("Dockerfile") if re.match(r"\s*COPY\b", line) and "svc-trace-lab" in line]
    assert copies == [COPY_LINE]


def test_a6_the_copy_comes_after_every_other_backend_copy_and_before_the_frontend_is_taken():
    lines = _lines("Dockerfile")
    at = lines.index(COPY_LINE)
    backend_copies = [i for i, line in enumerate(lines) if line.startswith("COPY ${BE}/") and i != at]
    assert backend_copies, "no other backend COPY found: the placement check would pass vacuously"
    assert at > max(backend_copies), "the Trace Lab COPY must follow the last other backend-service COPY"
    taken = next(i for i, line in enumerate(lines) if line.startswith("COPY --from=frontend"))
    assert at < taken


def test_a6_nothing_of_trace_lab_is_installed_into_shared_site_packages():
    # Trace Lab's own dependencies are all among svc-deepagents' (already installed), so the image
    # needs no extra install and no venv: no RUN line may name the service.
    runs = [line for line in _lines("Dockerfile") if "svc-trace-lab" in line and not line.lstrip().startswith(("#", "COPY"))]
    assert runs == []


def test_a6_trace_lab_needs_no_package_the_image_lacks():
    """The reason the Dockerfile adds no venv: every direct dependency of svc-trace-lab is also one of
    svc-deepagents', which the image installs. If this fails, add the doc-rag style venv (see the
    Dockerfile) for the missing package, in A6's one place."""
    import tomllib

    def names(path: Path) -> set[str]:
        deps = tomllib.loads(path.read_text(encoding="utf-8"))["project"]["dependencies"]
        return {re.split(r"[\[<>=!~ ;]", dep, maxsplit=1)[0].lower() for dep in deps}

    assert ROOT is not None
    backend = ROOT / "apps" / "backend" / "cafm-connector-service-final"
    lab = names(backend / "svc-trace-lab" / "pyproject.toml")
    image = names(backend / "svc-deepagents" / "pyproject.toml")
    assert lab, "svc-trace-lab lists no dependencies: the check would pass vacuously"
    assert lab <= image, f"svc-trace-lab needs packages the image lacks: {sorted(lab - image)}"


# ── A7: nginx ─────────────────────────────────────────────────────────────────────────────


def test_a7_nginx_has_exactly_one_trace_lab_location_to_the_trace_lab_port():
    locations = [line for line in _lines("nginx.conf") if NGINX_LOCATION in line]
    assert len(locations) == 1
    assert re.fullmatch(rf"\s*{re.escape(NGINX_LOCATION)}\s*\{{ proxy_pass http://127\.0\.0\.1:{PORT}/; \}}", locations[0])


def test_a7_the_location_sits_with_the_other_backend_locations():
    lines = _lines("nginx.conf")
    at = next(i for i, line in enumerate(lines) if NGINX_LOCATION in line)
    deep_agents = next(i for i, line in enumerate(lines) if "location /backend/deep-agents/" in line)
    fallback = next(i for i, line in enumerate(lines) if re.match(r"\s*location / \{", line))
    assert deep_agents < at < fallback
    # The neighbours' proxy headers are server-level: the new location must not add a block of its own.
    assert "proxy_set_header" not in lines[at]


def test_a7_the_other_backend_routes_are_unchanged():
    text = _read("nginx.conf")
    for path, port in (("ops-intelligence", 8009), ("work-order", 8007), ("connector", 8000), ("schema-mapper", 8003),
                       ("udr", 8006), ("deep-agents", 8008)):
        assert re.search(rf"location /backend/{path}/\s*\{{ proxy_pass http://127\.0\.0\.1:{port}/; \}}", text), path
    assert re.search(r"location /backend/doc-rag/\s*\{ return 404; \}", text)


# ── A8: supervisord ───────────────────────────────────────────────────────────────────────


def test_a8_one_trace_lab_program_and_it_is_the_last_section():
    cfg = _supervisor()
    sections = cfg.sections()
    assert sections.count(SECTION) == 1
    assert sections[-1] == SECTION
    for existing in ("nginx", "ops-intelligence", "connector", "work-order", "schema-mapper", "schema-mapper-worker",
                     "udr", "doc-rag", "deep-agents"):
        assert f"program:{existing}" in sections, existing


def test_a8_the_program_settings():
    cfg = _supervisor()
    program = cfg[SECTION]
    assert program["directory"] == "/app/svc-trace-lab"
    assert program["autostart"] == "true"
    assert program["autorestart"] == "true"
    assert program["startsecs"] == "5"
    assert program["startretries"] == "3"
    assert program["priority"] == "30"
    assert program["stdout_logfile"] == "/dev/stdout"
    assert program["stdout_logfile_maxbytes"] == "0"
    assert program["stderr_logfile"] == "/dev/stderr"
    assert program["stderr_logfile_maxbytes"] == "0"
    others = [int(cfg[name]["priority"]) for name in cfg.sections() if name.startswith("program:") and name != SECTION]
    assert int(program["priority"]) > max(others), "Trace Lab starts after the services it calls"


def test_a8_the_environment_is_the_approved_values_the_import_path_and_the_eight_proxy_variables():
    env = _environment(_supervisor()[SECTION]["environment"])
    assert env["ACTIVITY_LOG_ENABLED"] == "false"
    assert env["MIGRATION_BASE_URL"] == "http://127.0.0.1:9"
    # Several services have a top-level `src` package and are installed editable into the shared
    # site-packages, so `src` is ambiguous there: svc-deepagents' must be first on the path.
    assert env["PYTHONPATH"].split(":") == ["/app/svc-trace-lab", "/app/svc-deepagents"]
    # Nothing else: in particular not TRACE_LAB_ENABLED (the portal's switch) or a database URL.
    assert set(env) == {"ACTIVITY_LOG_ENABLED", "MIGRATION_BASE_URL", "PYTHONPATH", *PROXY_VARS, *UPSTREAM_ENV}


def test_a8_the_environment_line_is_one_line_with_no_stray_text():
    # _environment() only collects NAME="value" pairs, so prove the line is nothing but those pairs.
    raw = _supervisor()[SECTION]["environment"]
    assert "\n" not in raw
    assert re.fullmatch(r'\w+="[^"]*"(,\w+="[^"]*")*', raw), raw


def test_a8_every_service_url_points_at_the_allow_list_proxy_the_run_check_demands():
    """orchestrator_host.assert_isolated() refuses a golden run unless each of these is
    http://127.0.0.1:8010/__proxy/<key>. Without them golden runs fail closed on Azure."""
    env = _environment(_supervisor()[SECTION]["environment"])
    assert len(PROXY_VARS) == 4, "orchestrator_host.BASE_URL_SETTINGS no longer lists four services"
    for var, key in PROXY_VARS.items():
        assert env[var] == f"{PROXY_BASE}/{key}", var
    assert PROXY_BASE == f"http://127.0.0.1:{PORT}/__proxy"


def test_a8_the_upstream_addresses_are_the_ports_the_neighbouring_programs_listen_on():
    """Where the proxy forwards allowed reads. Read from the other [program:...] blocks (and the image's
    own ENV for the same services), never guessed."""
    cfg = _supervisor()
    env = _environment(cfg[SECTION]["environment"])
    image = _image_service_urls()
    assert len(UPSTREAM_PROGRAM) == 4 and set(UPSTREAM_PROGRAM) == set(UPSTREAM_VARS_BY_KEY)
    for key, program in UPSTREAM_PROGRAM.items():
        port = int(re.search(r"--port (\d+)", cfg[f"program:{program}"]["command"]).group(1))
        want = f"http://127.0.0.1:{port}"
        assert env[UPSTREAM_VARS_BY_KEY[key]] == want, (key, program)
        assert want == image[PROXY_ENV_OF_KEY[key]], f"the image's own {PROXY_ENV_OF_KEY[key]} must be the same service"
    ports = [env[var] for var in UPSTREAM_ENV]
    assert len(set(ports)) == 4, "four different services, four different addresses"
    assert all(not value.endswith(f":{PORT}") for value in ports), "an upstream must never be Trace Lab itself (a loop)"


def test_a8_no_other_program_carries_a_proxy_or_upstream_variable():
    text = _read("supervisord.conf")
    outside = text.replace(_program_block(text), "")
    for name in (*PROXY_VARS, *UPSTREAM_ENV):
        assert name not in outside, f"{name} appears outside [program:trace-lab]"
    assert "__proxy" not in outside


def test_a8_only_the_trace_lab_block_differs_from_the_base_commit():
    """The proxy wiring may change [program:trace-lab] and nothing else in the file."""
    base = _git("show", "HEAD:deploy/allinone/supervisord.conf")
    now = _read("supervisord.conf")
    assert _without_block(base).rstrip() == _without_block(now).rstrip()


def test_nothing_else_under_deploy_differs_from_the_base_commit():
    changed = set(_git("diff", "HEAD", "--name-only", "--", "deploy").split())
    new = set(_git("ls-files", "--others", "--exclude-standard", "--", "deploy").split())
    allowed = {"deploy/allinone/Dockerfile", "deploy/allinone/nginx.conf", "deploy/allinone/supervisord.conf"}
    assert changed <= allowed, sorted(changed - allowed)
    assert not new, sorted(new)


def test_a8_no_environment_interpolation_and_no_percent_sign_in_the_block():
    # supervisord refuses to start when a %(ENV_X)s variable is undefined (and any other % is an error).
    block = _program_block(_read("supervisord.conf"))
    assert "%(ENV_" not in block
    assert "%" not in block


def test_a8_ports_and_paths_agree_across_the_files():
    command = shlex.split(_supervisor()[SECTION]["command"])
    assert command[:2] == ["/bin/sh", "-c"] and len(command) == 3
    script = command[2]
    port = re.search(r"--port (\d+)", script)
    root_path = re.search(r"--root-path (\S+)", script)
    assert port and int(port.group(1)) == PORT
    assert root_path and root_path.group(1) == ROOT_PATH
    assert "--host 127.0.0.1" in script, "only nginx is reachable from outside the container"
    assert script.count("trace_lab.app:app") == 1
    assert "--proxy-headers" in script and "--forwarded-allow-ips=*" in script
    nginx = next(line for line in _lines("nginx.conf") if NGINX_LOCATION in line)
    assert f"127.0.0.1:{PORT}/" in nginx
    assert NGINX_LOCATION.removeprefix("location ").rstrip("/") == root_path.group(1)
    # The golden runs' allow-list proxy is reached on this same port (orchestrator_host.PROXY_BASE).
    assert _port_in(TL_ROOT / "trace_lab" / "orchestrator_host.py", r'PROXY_BASE = "http://127\.0\.0\.1:(\d+)/__proxy"') == PORT
    compose = (ROOT / "docker-compose.tracelab.yml").read_text(encoding="utf-8")
    assert f'- "{PORT}"' in compose and f"- {ROOT_PATH}" in compose


# The program's shell command, run for real against stubs: it starts uvicorn only when
# TRACE_LAB_ENABLED is exactly "1", and otherwise sleeps.


@pytest.fixture
def stub_bin(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("uvicorn", "sleep"):
        stub = bin_dir / name
        stub.write_text('#!/bin/sh\necho "STARTED ${0##*/} $*"\n', encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def _run_program(stub_bin: Path, enabled: str | None) -> str:
    script = shlex.split(_supervisor()[SECTION]["command"])[2]
    env = {"PATH": str(stub_bin)}
    if enabled is not None:
        env["TRACE_LAB_ENABLED"] = enabled
    done = subprocess.run(["/bin/sh", "-c", script], env=env, capture_output=True, text=True, timeout=20, check=True)
    return done.stdout.strip()


def test_a8_the_program_starts_uvicorn_only_when_enabled_is_exactly_one(stub_bin):
    out = _run_program(stub_bin, "1")
    assert out.startswith("STARTED uvicorn trace_lab.app:app --host 127.0.0.1 --port 8010 --root-path /backend/trace-lab")
    assert "--proxy-headers" in out and "--forwarded-allow-ips=" in out


@pytest.mark.parametrize("enabled", [None, "", "0", "true", "yes", " 1", "11", "on"])
def test_a8_the_program_sleeps_unless_enabled_is_exactly_one(stub_bin, enabled):
    assert _run_program(stub_bin, enabled) == "STARTED sleep infinity"

