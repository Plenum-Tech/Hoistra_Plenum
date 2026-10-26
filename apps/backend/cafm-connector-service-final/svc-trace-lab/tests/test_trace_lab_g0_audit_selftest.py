"""ops/g0_audit_selftest.sh (also `ops/g0_audit.sh --self-test`): proves both outcomes of the Golden Rule audit
without touching the real repository. It builds throwaway git repositories under a temporary directory, runs
the audit in each, and checks the exit code and the RESULT line: PASS with only the other sessions' files
(CLAUDE.md edited, the untracked docs folder) and FAIL on any other unapproved file or any change under
svc-deepagents, svc-udr or svc-operations-intelligence.

These tests run the self-test for real, feed it deliberately broken copies of the audit to show that it notices,
and check that it creates, changes and leaves nothing outside its own temporary directory. They need git, which
the testenv copy lacks, so they skip there; run them on the host:

    cd apps/backend/cafm-connector-service-final/svc-trace-lab
    python3 -m pytest tests/test_trace_lab_g0_audit_selftest.py -q -p no:cacheprovider
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
OPS = HERE.parents[1] / "ops"
AUDIT = OPS / "g0_audit.sh"
SELFTEST = OPS / "g0_audit_selftest.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed in this environment")

BE = "apps/backend/cafm-connector-service-final"
SERVICES = ("svc-deepagents", "svc-udr", "svc-operations-intelligence")


def _env(tmp: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(TMPDIR=str(tmp), LC_ALL="C", **extra)
    return env


def run_selftest(tmp_path: Path, *, argv: tuple[str, ...] = ("sh", str(SELFTEST)), cwd: Path | None = None,
                 **extra: str) -> subprocess.CompletedProcess:
    scratch = tmp_path / "tmp"
    scratch.mkdir(exist_ok=True)
    where = cwd or tmp_path / "elsewhere"
    where.mkdir(exist_ok=True)
    return subprocess.run(list(argv), cwd=where, env=_env(scratch, **extra), capture_output=True, text=True, timeout=120)


def test_the_self_test_passes_against_the_real_audit_and_names_every_outcome(tmp_path):
    done = run_selftest(tmp_path)
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    assert re.search(r"^SELF-TEST: PASS - (\d+) of \1 outcomes as expected$", out, re.MULTILINE), out
    lines = [line for line in out.splitlines() if line.startswith("ok ")]
    assert len(lines) >= 15, out
    assert not any(line.startswith(("FAIL", "MISMATCH")) for line in out.splitlines()), out
    text = out.lower()
    # Both outcomes, and the three services, are really among the scenarios.
    assert "expect PASS" in out and "expect FAIL" in out and "expect exit 2" in out
    assert "claude.md" in text and "docs/" in text
    for service in SERVICES:
        assert service in out, service


def test_the_audit_flag_runs_the_same_self_test_from_anywhere(tmp_path):
    done = run_selftest(tmp_path, argv=("sh", str(AUDIT), "--self-test"))
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    assert "SELF-TEST: PASS" in out


def test_the_self_test_removes_everything_it_made(tmp_path):
    run_selftest(tmp_path)
    assert list((tmp_path / "tmp").iterdir()) == []


def test_the_self_test_does_not_depend_on_or_touch_the_repository_it_is_run_from(tmp_path):
    """Run from inside a repository that violates the rules: the self-test is unaffected and changes nothing there."""
    env = _env(tmp_path, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    repo = tmp_path / "work"
    repo.mkdir()
    for args in (["init", "-q"],):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)
    (repo / "a.txt").write_text("1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, env=env, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"], cwd=repo, env=env,
                   check=True, capture_output=True)
    (repo / "a.txt").write_text("1\nchanged\n", encoding="utf-8")
    (repo / "new.txt").write_text("x\n", encoding="utf-8")

    def snapshot() -> tuple:
        files = {str(p.relative_to(repo)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(repo.rglob("*"))
                 if p.is_file()}
        status = subprocess.run(["git", "--no-optional-locks", "status", "--short"], cwd=repo, env=env, capture_output=True,
                                text=True).stdout
        return files, status

    before = snapshot()
    done = run_selftest(tmp_path, cwd=repo, GIT_DIR=str(repo / ".git"), GIT_WORK_TREE=str(repo))
    assert done.returncode == 0, done.stdout + done.stderr
    assert snapshot() == before, "the self-test must not change the repository it was started in (even with GIT_DIR set)"


def _broken_audit(tmp_path: Path, old: str, new: str) -> Path:
    text = AUDIT.read_text(encoding="utf-8")
    assert old in text, f"the mutation anchor is gone from the audit: {old!r}"
    broken = tmp_path / "broken_audit.sh"
    broken.write_text(text.replace(old, new, 1), encoding="utf-8")
    return broken


@pytest.mark.parametrize(
    "why,old,new,needle",
    [
        # Always passes: the FAIL scenarios must notice.
        ("never fails", 'echo "RESULT: FAIL - $PROBLEMS problem(s)"\nexit 1', 'echo "RESULT: FAIL - $PROBLEMS problem(s)"\nexit 0',
         "expect FAIL"),
        # Counts nothing: unlisted edits and service changes must notice.
        ("counts nothing", 'echo "  NOT APPROVED: $path"\n        PROBLEMS=$((PROBLEMS + 1))', 'echo "  NOT APPROVED: $path"',
         "expect FAIL"),
        # Forgets the trimmed CLAUDE.md: the token-saving scenario must notice.
        ("forgets CLAUDE.md", f"{BE}/CLAUDE.md\n", "", "expect PASS"),
        # Stops looking at the services' untracked files.
        ("ignores new service files", '$G status --porcelain -- $SVC_PATHS', "true", "expect FAIL"),
        # Stops listing the docs folder apart.
        ("forgets the docs folder", f'FOREIGN_NEW=\'\n{BE}/docs\n\'', "FOREIGN_NEW=''", "docs/"),
    ],
)
def test_a_broken_audit_makes_the_self_test_fail(tmp_path, why, old, new, needle):
    broken = _broken_audit(tmp_path, old, new)
    done = run_selftest(tmp_path, G0_AUDIT=str(broken))
    out = done.stdout + done.stderr
    assert done.returncode == 1, f"{why}: the self-test did not notice\n{out}"
    assert re.search(r"^SELF-TEST: FAIL - \d+ of \d+ outcomes", out, re.MULTILINE), out
    assert any(line.startswith("MISMATCH") and needle in line for line in out.splitlines()), (why, out)


def test_the_self_test_fails_closed_when_the_audit_script_is_missing(tmp_path):
    done = run_selftest(tmp_path, G0_AUDIT=str(tmp_path / "nowhere.sh"))
    assert done.returncode == 2
    assert "no audit script" in done.stderr


# ── the script's own safety ───────────────────────────────────────────────────────────────


def _code(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]


@pytest.mark.parametrize("script", [AUDIT, SELFTEST], ids=lambda p: p.name)
def test_both_scripts_are_valid_posix_sh(script):
    done = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_self_test_runs_git_only_inside_its_own_throwaway_repository():
    code = _code(SELFTEST)
    # A git invocation is `git` where a command may start: line start, or after ; & | ( or $(.
    invoked = [line for line in code if re.search(r"(?:^|[;&|(]|\$\()\s*git\s", line)]
    assert invoked, "no git invocation found: the check would pass vacuously"
    assert len(invoked) == 1 and "--git-dir=" in invoked[0] and "--work-tree=" in invoked[0], invoked
    assert any(re.search(r"\btgit init\b", line) for line in code), "the repository is made through tgit"
    body = "\n".join(code)
    for must in ("GIT_CEILING_DIRECTORIES", 'unset "$name"', "mktemp -d"):
        assert must in body, must


def test_the_self_test_deletes_only_its_own_temporary_directory():
    rm = [line.strip() for line in _code(SELFTEST) if re.search(r"\brm\b", line)]
    assert rm, "no rm found: the clean-up check would pass vacuously"
    for line in rm:
        assert re.search(r'rm -rf "\$(?:T|REPO)"', line), line
    body = "\n".join(_code(SELFTEST))
    assert re.search(r'T=\$\(mktemp -d ', body)
    assert re.search(r'\[ -n "\$T" \]', body), "the temporary directory name must be checked non-empty before any rm"
