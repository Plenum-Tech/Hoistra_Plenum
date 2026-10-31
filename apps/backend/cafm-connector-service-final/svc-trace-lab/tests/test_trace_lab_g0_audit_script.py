"""ops/g0_audit.sh, the final Golden Rule audit: read-only, exits non-zero when anything other than an approved
attachment (A1 to A8) has changed, or when svc-deepagents, svc-udr or svc-operations-intelligence has.

Every test builds a throwaway git repository under pytest's tmp_path and runs the script in it. The real
repository is never touched. They need git, which the testenv copy lacks, so they skip there; run them on
the host:

    cd apps/backend/cafm-connector-service-final/svc-trace-lab
    python3 -m pytest tests/test_trace_lab_g0_audit_script.py -q -p no:cacheprovider
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
SCRIPT = HERE.parents[1] / "ops" / "g0_audit.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed in this environment")

BE = "apps/backend/cafm-connector-service-final"
APPROVED = [
    "apps/frontend/src/App.jsx",
    "apps/frontend/src/logic/integrations.js",
    "apps/frontend/src/components/shell/PlatformCostPage.jsx",
    "apps/frontend/Dockerfile",
    "deploy/allinone/Dockerfile",
    "apps/frontend/infra/single-url/nginx.conf",
    "deploy/allinone/nginx.conf",
    "deploy/allinone/supervisord.conf",
]
FOREIGN = [
    "apps/frontend/src/logic/instances.js",
    "apps/frontend/src/logic/superAdmin.js",
    "apps/frontend/src/screens/InstancePicker.jsx",
    "apps/frontend/test/instances.test.mjs",
    "apps/frontend/test/superAdminLive.test.mjs",
    # The token-saving session's trim of the backend rules file (17 Oct); its full text moved to docs/architecture/.
    "apps/backend/cafm-connector-service-final/CLAUDE.md",
]
# Another session's NEW folder: its untracked files are listed apart too. Tracked files there still count.
FOREIGN_NEW = "apps/backend/cafm-connector-service-final/docs"
DOC_FILES = [f"{FOREIGN_NEW}/architecture/cafm-platform-spec.md", f"{FOREIGN_NEW}/notes/other.md"]
# What the audit prints beside each approved file's counts (the addendum's figures; Dockerfile = A4 2 + A6 1).
EXPECTED = {
    "apps/frontend/src/App.jsx": "A1: 2 added",
    "apps/frontend/src/logic/integrations.js": "A2: 3 added and 1 removed (2 new lines and 1 changed line)",
    "apps/frontend/src/components/shell/PlatformCostPage.jsx": "A3: 2 added",
    "apps/frontend/Dockerfile": "A4: 2 added",
    "deploy/allinone/Dockerfile": "A4 2 + A6 1: 3 added",
    "apps/frontend/infra/single-url/nginx.conf": "A5: 6 added",
    "deploy/allinone/nginx.conf": "A7: about 3 added",
    "deploy/allinone/supervisord.conf": "A8: about 12 added",
}
SERVICES = [f"{BE}/svc-deepagents", f"{BE}/svc-udr", f"{BE}/svc-operations-intelligence"]
SERVICE_FILES = [f"{SERVICES[0]}/src/a.py", f"{SERVICES[1]}/src/b.py", f"{SERVICES[2]}/src/c.py"]
OTHER = ["apps/frontend/src/other.js", "docs/notes.md", "dir with space/file name.txt",
         f"{BE}/svc-work-order-management/a.py", f"{BE}/svc-udr-tools/x.py",
         f"{BE}/README.md", f"{FOREIGN_NEW}/tracked.md"]


def _env(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null", GIT_CEILING_DIRECTORIES=str(tmp_path),
               HOME=str(tmp_path), LC_ALL="C")
    return env


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                          cwd=repo, env=_env(repo.parent), check=True, capture_output=True, text=True)
    return done.stdout


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    for rel in [*APPROVED, *FOREIGN, *SERVICE_FILES, *OTHER]:
        _write(root, rel, "line 1\nline 2\n")
    _write(root, "apps/frontend/src/App.jsx.bak", "line 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def audit(repo: Path, cwd: Path | None = None) -> tuple[int, str]:
    done = subprocess.run(["sh", str(SCRIPT)], cwd=cwd or repo, env=_env(repo.parent), capture_output=True, text=True,
                          timeout=60)
    return done.returncode, done.stdout + done.stderr


def append(repo: Path, rel: str, lines: int = 1) -> None:
    with (repo / rel).open("a", encoding="utf-8") as handle:
        handle.write("added\n" * lines)


# ── passing ───────────────────────────────────────────────────────────────────────────────


def test_a_clean_tree_passes(repo):
    code, out = audit(repo)
    assert code == 0, out
    assert "RESULT: PASS" in out
    assert "== git status --short ==" in out


@pytest.mark.parametrize("path", APPROVED)
def test_each_approved_attachment_may_change_and_is_counted(repo, path):
    append(repo, path, 3)
    code, out = audit(repo)
    assert code == 0, out
    assert f"modified: {path}" in out
    numstat = [line for line in out.splitlines() if re.match(rf"^3\t0\t{re.escape(path)}\b", line)]
    assert len(numstat) == 1, out
    assert numstat[0].endswith(f"addendum: {EXPECTED[path]}"), numstat[0]


def test_all_approved_attachments_together_still_pass(repo):
    for path in APPROVED:
        append(repo, path)
    code, out = audit(repo)
    assert code == 0, out
    assert out.count("addendum:") == len(APPROVED)


def test_another_sessions_files_are_listed_apart_and_never_fail_the_audit(repo):
    for path in FOREIGN:
        append(repo, path)
    code, out = audit(repo)
    assert code == 0, out
    apart = out.split("not Trace Lab's", 1)[1].split("==", 2)[1]
    for path in FOREIGN:
        assert path in apart, path
    assert "numstat" not in apart
    head = out.split("not Trace Lab's", 1)[0]
    assert "modified:" not in head.split("== modified or deleted tracked files ==", 1)[1]


def test_untracked_files_outside_the_three_services_do_not_fail_the_audit(repo):
    _write(repo, f"{BE}/svc-trace-lab/new_module.py", "x = 1\n")
    _write(repo, "docker-compose.tracelab.yml", "services: {}\n")
    code, out = audit(repo)
    assert code == 0, out
    assert "?? " in out, "the untracked files show in git status --short"


def test_it_works_from_a_subdirectory(repo):
    append(repo, APPROVED[0])
    append(repo, f"{SERVICES[1]}/src/b.py")
    code, out = audit(repo, cwd=repo / "apps" / "frontend" / "src")
    assert code == 1, out
    assert f"modified: {APPROVED[0]}" in out
    assert re.search(rf"^1\t0\t{re.escape(APPROVED[0])}\b", out, re.MULTILINE), "the numstat reads the file from the root"
    assert f"SERVICE CHANGED:  M {SERVICES[1]}/src/b.py" in out, "the services are checked from the root"


def _apart(out: str) -> str:
    """The text of the 'not Trace Lab's' section."""
    return out.split("not Trace Lab's", 1)[1].split("\n== ", 1)[0]


def test_the_token_saving_sessions_files_alone_pass_and_are_listed_apart(repo):
    """The state the audit exited 1 on: CLAUDE.md trimmed, its full text now in the untracked docs folder."""
    append(repo, f"{BE}/CLAUDE.md")
    _write(repo, DOC_FILES[0], "the spec\n")
    code, out = audit(repo)
    assert code == 0, out
    assert "RESULT: PASS" in out
    apart = _apart(out)
    assert f"modified: {BE}/CLAUDE.md" in apart
    assert f"new (untracked): {DOC_FILES[0]}" in apart
    assert "NOT APPROVED" not in out and "SERVICE CHANGED" not in out
    assert f"modified: {BE}/CLAUDE.md" not in out.split("not Trace Lab's", 1)[0], "never counted among the real changes"


def test_every_untracked_file_in_the_other_sessions_folder_is_listed_apart(repo):
    for path in DOC_FILES:
        _write(repo, path, "x\n")
    code, out = audit(repo)
    assert code == 0, out
    apart = _apart(out)
    for path in DOC_FILES:
        assert f"new (untracked): {path}" in apart, path
    assert "(none)" not in apart, "the section is not empty, so it must not say so"


def test_untracked_files_elsewhere_are_not_listed_apart(repo):
    _write(repo, f"{BE}/docs-extra/x.md", "x\n")           # a name that only starts like the folder
    _write(repo, f"{BE}/svc-trace-lab/new_module.py", "x\n")
    code, out = audit(repo)
    assert code == 0, out
    assert "new (untracked)" not in out
    assert "(none)" in _apart(out)


def test_the_real_state_all_foreign_files_together_with_an_approved_change_passes(repo):
    for path in FOREIGN:
        append(repo, path)
    for path in DOC_FILES:
        _write(repo, path, "x\n")
    append(repo, APPROVED[-1], 17)
    code, out = audit(repo)
    assert code == 0, out
    assert "RESULT: PASS" in out and out.count("addendum:") == 1


def test_a_sibling_of_the_foreign_rules_file_still_fails(repo):
    append(repo, f"{BE}/README.md")
    code, out = audit(repo)
    assert code == 1, out
    assert f"NOT APPROVED: {BE}/README.md" in out


def test_a_tracked_file_edited_in_the_other_sessions_folder_still_fails(repo):
    """Only that folder's UNTRACKED files are another session's; it is not a blanket exemption."""
    append(repo, f"{FOREIGN_NEW}/tracked.md")
    code, out = audit(repo)
    assert code == 1, out
    assert f"NOT APPROVED: {FOREIGN_NEW}/tracked.md" in out


def test_the_other_sessions_untracked_files_do_not_hide_a_service_change_or_an_unlisted_edit(repo):
    for path in DOC_FILES:
        _write(repo, path, "x\n")
    append(repo, f"{BE}/CLAUDE.md")
    append(repo, f"{SERVICES[2]}/src/c.py")
    append(repo, "apps/frontend/src/other.js")
    code, out = audit(repo)
    assert code == 1, out
    assert "SERVICE CHANGED" in out and "NOT APPROVED: apps/frontend/src/other.js" in out
    assert "RESULT: FAIL - 2 problem(s)" in out


# ── failing ───────────────────────────────────────────────────────────────────────────────


def test_an_unlisted_modified_file_fails_and_is_named(repo):
    append(repo, "apps/frontend/src/other.js")
    code, out = audit(repo)
    assert code == 1, out
    assert "NOT APPROVED: apps/frontend/src/other.js" in out
    assert "RESULT: FAIL" in out


def test_a_file_that_only_looks_like_an_approved_one_fails(repo):
    append(repo, "apps/frontend/src/App.jsx.bak")
    code, out = audit(repo)
    assert code == 1, out
    assert "NOT APPROVED: apps/frontend/src/App.jsx.bak" in out


def test_a_path_with_a_space_is_named_whole(repo):
    append(repo, "dir with space/file name.txt")
    code, out = audit(repo)
    assert code == 1, out
    assert "NOT APPROVED: dir with space/file name.txt" in out


def test_a_path_that_ends_in_a_space_is_named_whole(repo):
    _write(repo, "dir with space/ends with space ", "line 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "more")
    append(repo, "dir with space/ends with space ")
    code, out = audit(repo)
    assert code == 1, out
    assert "NOT APPROVED: dir with space/ends with space \n" in out


def test_a_staged_change_counts_as_much_as_an_unstaged_one(repo):
    append(repo, "docs/notes.md")
    _git(repo, "add", "docs/notes.md")
    code, out = audit(repo)
    assert code == 1, out
    assert "NOT APPROVED: docs/notes.md" in out


@pytest.mark.parametrize("path", [APPROVED[0], "docs/notes.md"])
def test_a_deleted_tracked_file_fails_even_an_approved_one(repo, path):
    (repo / path).unlink()
    code, out = audit(repo)
    assert code == 1, out
    assert f"deleted: {path}" in out
    assert f"DELETED: {path}" in out


@pytest.mark.parametrize("path", [APPROVED[0], "docs/notes.md"])
def test_a_tracked_file_that_becomes_a_symlink_fails_even_an_approved_one(repo, path):
    (repo / path).unlink()
    os.symlink("elsewhere", repo / path)
    code, out = audit(repo)
    assert code == 1, out
    assert f"UNEXPECTED: {path}" in out


def test_another_sessions_deleted_file_is_listed_apart_and_not_counted(repo):
    (repo / FOREIGN[0]).unlink()
    code, out = audit(repo)
    assert code == 0, out
    assert f"deleted: {FOREIGN[0]}" in out.split("not Trace Lab's", 1)[1]


@pytest.mark.parametrize("service,path", list(zip(SERVICES, SERVICE_FILES)))
def test_a_modified_file_in_the_three_services_fails_with_its_own_message(repo, service, path):
    append(repo, path)
    code, out = audit(repo)
    assert code == 1, out
    assert "SERVICE CHANGED" in out and path in out


@pytest.mark.parametrize("path", [f"{BE}/svc-work-order-management/a.py", f"{BE}/svc-udr-tools/x.py"])
def test_a_change_beside_the_three_services_is_unlisted_but_not_a_service_change(repo, path):
    append(repo, path)
    code, out = audit(repo)
    assert code == 1, out
    assert f"NOT APPROVED: {path}" in out
    assert "SERVICE CHANGED" not in out


@pytest.mark.parametrize("service", SERVICES)
def test_a_new_untracked_file_in_the_three_services_fails(repo, service):
    _write(repo, f"{service}/src/trace_lab_stray.py", "x = 1\n")
    code, out = audit(repo)
    assert code == 1, out
    assert "SERVICE CHANGED" in out and "trace_lab_stray.py" in out


def test_a_service_failure_is_counted_once_not_twice(repo):
    append(repo, f"{SERVICES[0]}/src/a.py")
    code, out = audit(repo)
    assert code == 1
    assert "RESULT: FAIL - 1 problem" in out, out


def test_other_sessions_files_do_not_hide_a_real_violation(repo):
    for path in FOREIGN:
        append(repo, path)
    append(repo, "docs/notes.md")
    code, out = audit(repo)
    assert code == 1, out
    assert "NOT APPROVED: docs/notes.md" in out
    assert all(f"NOT APPROVED: {path}" not in out for path in FOREIGN)


def test_outside_a_git_repository_it_fails_closed(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    done = subprocess.run(["sh", str(SCRIPT)], cwd=plain, env=_env(tmp_path), capture_output=True, text=True)
    assert done.returncode == 2
    assert "not inside a git repository" in done.stderr


def test_a_repository_without_a_commit_fails_closed(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    _git(root, "init", "-q")
    code, out = audit(root)
    assert code == 2, out


# ── read-only ─────────────────────────────────────────────────────────────────────────────


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_audit_changes_nothing_in_the_repository_not_even_the_index(repo):
    append(repo, APPROVED[0])
    append(repo, "docs/notes.md")
    _write(repo, "untracked.txt", "x\n")
    before = _snapshot(repo)
    code, out = audit(repo)
    assert code == 1 and "RESULT: FAIL" in out, "the audit must really have run (docs/notes.md is a violation)"
    assert _snapshot(repo) == before


def test_the_script_runs_no_git_command_that_changes_anything():
    text = SCRIPT.read_text(encoding="utf-8")
    code = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    changing = (r"add|commit|stash|checkout|switch|reset|restore|clean|rm|mv|push|pull|fetch|merge|rebase|tag|branch|"
                r"config|apply|am|cherry-pick|revert|gc|prune|update-index|worktree|init|clone")
    for line in code:
        if re.search(r"(?:\$G|\bgit)\b", line):
            assert not re.search(rf"\b(?:{changing})\b", line), line
    used = set(re.findall(r"(?:\$G|\bgit)\s+(status|diff|rev-parse|ls-files)\b", "\n".join(code)))
    assert used == {"status", "diff", "rev-parse", "ls-files"}, "the audit would pass vacuously if it ran none of these"
    assert all("--others" in line for line in code if re.search(r"ls-files", line)), "ls-files is used only to list untracked files"
    body = "\n".join(code)
    assert "--no-optional-locks" in body, "plain git status can rewrite the index"
    assert not re.search(r"\b(?:rm|mv|tee|touch|mkdir|sed -i)\b|>>|>\s*[^&\s]", body.replace(">&2", "").replace("2>/dev/null", "")), body


def test_the_script_lists_exactly_the_approved_and_the_other_sessions_files():
    text = SCRIPT.read_text(encoding="utf-8")
    for path in [*APPROVED, *FOREIGN, *SERVICES]:
        assert path in text, path
    # Nothing else that looks like an approved path: every apps/ or deploy/ file named in the lists is expected.
    named = set(re.findall(r"^((?:apps|deploy)/[\w./-]+)$", text, re.MULTILINE))
    assert named == {*APPROVED, *FOREIGN, FOREIGN_NEW, *SERVICES}
