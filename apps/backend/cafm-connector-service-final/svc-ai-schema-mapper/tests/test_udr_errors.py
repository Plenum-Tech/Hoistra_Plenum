"""Structured migration-error classifier tests. Run: python tests/test_udr_errors.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.errors import classify_migration_error as classify  # noqa: E402
from udr.run_activity import build_run_activity_entry  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


CASES = [
    # (message, code, category, recoverable) — file errors are recoverable via re-upload; only a
    # permission error is not recoverable by the user.
    ("This Excel file is password-protected.", "FILE_PASSWORD_PROTECTED", "File Error", True),
    ("This Excel file appears to be corrupted or is not a valid workbook.", "FILE_CORRUPT_OR_UNSUPPORTED", "File Error", True),
    ("No columns to parse from file", "FILE_EMPTY_OR_NO_DATA", "User Input Error", True),
    ("rate limit exceeded (429)", "AI_RATE_LIMITED", "AI/LLM Error", True),
    ("anthropic completion request failed unexpectedly", "AI_REQUEST_FAILED", "AI/LLM Error", True),
    ("Read timed out after 60s", "OPERATION_TIMED_OUT", "Timeout", True),
    ("Connection refused to embeddings host", "NETWORK_UNAVAILABLE", "Network Error", True),
    ("asyncpg: could not connect to database server", "DATABASE_ERROR", "Database Error", True),
    ("403 Forbidden: access denied", "PERMISSION_DENIED", "Permission Error", False),
    ("Test 1 fail rate exceeds threshold", "VALIDATION_FAILED", "Validation Error", True),
    ("some totally unexpected boom", "INTERNAL_ERROR", "Internal Processing Error", True),
]

for msg, code, cat, recoverable in CASES:
    r = classify(msg, step="Semantic Mapping")
    check(f"{code}: category", r["category"] == cat)
    check(f"{code}: code", r["code"] == code)
    check(f"{code}: recoverable={recoverable}", r["recoverable"] == recoverable)
    check(f"{code}: has suggested actions", bool(r["suggested_actions"]))
    check(f"{code}: has user_message + impact", bool(r["user_message"]) and bool(r["impact"]))

# Never leak a stack trace — technical_reason is a single trimmed line.
r = classify("boom\n  File x line 12\n    raise ValueError", step="X")
check("no stack-trace leak in technical_reason", "\n" not in r["technical_reason"])

# The failed run entry embeds the structured error on the run_failed step + at refs.error.
entry = build_run_activity_entry(
    "run1", migration_status="failed", completed_node_ids={1, 2},
    error_message="This Excel file is password-protected.",
)
check("failed entry status failed", entry["status"] == "failed")
rf = next((s for s in entry["processing_log"]["steps"] if s.get("stage") == "run_failed"), None)
check("run_failed step carries structured error", bool(rf) and rf.get("error", {}).get("code") == "FILE_PASSWORD_PROTECTED")
check("run_failed text is the user message (not raw)", rf["text"] == rf["error"]["user_message"])
check("entry refs.error present", entry["refs"].get("error", {}).get("category") == "File Error")
check("outcome uses the user message", entry["outcome"] == rf["error"]["user_message"])

print("\nALL TESTS PASSED")
