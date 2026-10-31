"""Sign-in must not hold a row lock across bcrypt, and must not block the event loop.

Observed in production on 2026-09-16: two logins that SUCCEEDED took 102 and 147 seconds,
while ~20 other requests in the same window served in 1-10ms. nginx gives up at 60s, so the
browser was told "couldn't reach the sign-in service" for a login the server had accepted —
and a person told that retries, each retry queueing behind the last.

Two causes, both fixed here:
  * find_by_email(lock=True) took SELECT ... FOR UPDATE before the password check and held it
    across bcrypt, so attempts on one account serialised in the database.
  * verify_password is ~250ms of CPU called directly from an async handler, blocking the loop
    for every other request on a container running eight services on two cores.

These are source-level assertions on purpose. The failure they guard is a concurrency
property, and a unit test that calls sign_in once cannot see it.
"""
import ast
import inspect
from pathlib import Path

SRC_PATH = (Path(__file__).resolve().parents[1]
            / "src" / "engines" / "auth" / "accounts.py")
SRC = SRC_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SRC)


def body_of(func_name: str) -> str:
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return ast.get_source_segment(SRC, node) or ""
    raise AssertionError(f"{func_name} not found")


class TestSignInTakesNoRowLock:

    def test_it_reads_the_account_without_for_update(self):
        """The lock is what made two sign-ins for one account queue. It existed to keep the
        failed-attempt counter race-free, which the SQL increment now does instead."""
        assert "lock=True" not in body_of("sign_in")

    def test_the_failure_counter_is_incremented_in_sql(self):
        """Not computed in Python from a value read before the password check — two
        simultaneous wrong guesses would both write the same number and lose an attempt,
        which is the one thing a lockout must not do."""
        body = body_of("sign_in")
        assert "failed_login_count = COALESCE(failed_login_count, 0) + 1" in body

    def test_the_lockout_still_fires_at_the_cap(self):
        body = body_of("sign_in")
        assert "locked_until" in body and "CAST(:cap AS INT)" in body

    def test_it_still_reports_the_real_count(self):
        """RETURNING, so the log and the lockout decision use the number actually written."""
        assert "RETURNING failed_login_count" in body_of("sign_in")


class TestBcryptNeverBlocksTheEventLoop:

    def test_sign_in_hashes_in_a_worker_thread(self):
        body = body_of("sign_in")
        assert "await asyncio.to_thread(verify_password" in body

    def test_the_timing_equaliser_does_too(self):
        """burn_time is the same 250ms of bcrypt, on the path taken by an unknown address —
        the one an attacker exercises most."""
        assert "await asyncio.to_thread(burn_time)" in body_of("sign_in")

    def test_no_async_function_here_calls_bcrypt_directly(self):
        """Fixing sign_in alone would leave password change and reset blocking the loop for
        every other request in flight."""
        offenders = []
        for node in ast.walk(TREE):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            src = ast.get_source_segment(SRC, node) or ""
            for call in ("verify_password(", "burn_time("):
                for line in src.splitlines():
                    if call in line and "to_thread" not in line and "import" not in line:
                        offenders.append(f"{node.name}: {line.strip()}")
        assert not offenders, "bcrypt called directly from async code: " + "; ".join(offenders)


class TestTheTimingDefenceSurvives:
    """An unknown address must still cost what a wrong password costs, or the response time
    answers the question the generic error message refuses to."""

    def test_an_unknown_address_still_burns_the_same_time(self):
        body = body_of("sign_in")
        i_burn = body.index("burn_time")
        i_verify = body.index("verify_password")
        assert i_burn < i_verify, "the unknown-address path must still hash before answering"

    def test_both_paths_raise_the_same_error(self):
        body = body_of("sign_in")
        assert body.count("GENERIC_SIGNIN_FAILURE") >= 2
