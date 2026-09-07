"""Structured migration-failure classification.

Turns a raw error (an exception or its message) into the structured, user-facing shape every surface
renders — the chat error card, the Migration Panel, and the Activity Log — so users always see WHAT
failed, WHY, whether it's recoverable, and WHAT TO DO next, instead of a raw stack trace or a generic
"something went wrong".

Pure + offline-testable. The classifier is keyword-driven over the error text (the underlying nodes
raise real exceptions — ``ExcelReadError`` for files, anthropic/httpx errors for AI, asyncpg/SQLAlchemy
for DB, etc.), mapping each to a category, a stable code, a concise user message, the recovery flags,
and contextual suggested actions.
"""
from __future__ import annotations

import re
from typing import Any

# ── Categories (spec "Error Categorization") ────────────────────────────────────────────────
CATEGORY_FILE = "File Error"
CATEGORY_USER_INPUT = "User Input Error"
CATEGORY_VALIDATION = "Validation Error"
CATEGORY_AI = "AI/LLM Error"
CATEGORY_NETWORK = "Network Error"
CATEGORY_API = "API Error"
CATEGORY_DATABASE = "Database Error"
CATEGORY_TIMEOUT = "Timeout"
CATEGORY_PERMISSION = "Permission Error"
CATEGORY_INTERNAL = "Internal Processing Error"

# Reusable suggested-action items ({label, action} — the FE maps `action` to a control).
_RETRY = {"label": "Retry the migration", "action": "retry"}
_RESTART = {"label": "Restart from the beginning", "action": "restart"}
_REUPLOAD = {"label": "Upload a corrected file", "action": "reupload"}
_WAIT_RETRY = {"label": "Wait a moment and retry", "action": "retry"}
_REVIEW = {"label": "Review the mappings", "action": "review"}
_SUPPORT = {"label": "Contact your administrator", "action": "support"}


class _Rule:
    __slots__ = ("pattern", "category", "code", "user_message", "impact",
                 "recoverable", "retry_supported", "actions")

    def __init__(self, pattern, category, code, user_message, impact,
                 recoverable, retry_supported, actions):
        self.pattern = re.compile(pattern, re.IGNORECASE)
        self.category = category
        self.code = code
        self.user_message = user_message
        self.impact = impact
        self.recoverable = recoverable
        self.retry_supported = retry_supported
        self.actions = actions


# Order matters — the FIRST matching rule wins, so put specific patterns before broad ones.
_RULES: list[_Rule] = [
    # ── File / parsing ──────────────────────────────────────────────────────────────────────
    _Rule(r"password|encrypt", CATEGORY_FILE, "FILE_PASSWORD_PROTECTED",
          "An uploaded file is password-protected, so it could not be read.",
          "The file was not ingested — the migration cannot continue without it.",
          True, False, [_REUPLOAD, _RESTART]),
    _Rule(r"not a zip|badzip|corrupt|invalid workbook|unsupported format|cannot determine|no valid",
          CATEGORY_FILE, "FILE_CORRUPT_OR_UNSUPPORTED",
          "An uploaded file is corrupted or is not a supported spreadsheet.",
          "The file was not ingested — the migration cannot continue without it.",
          True, False, [_REUPLOAD, _RESTART]),
    _Rule(r"no columns|empty|no parseable|no sheets|missing worksheet|0 rows|no data",
          CATEGORY_USER_INPUT, "FILE_EMPTY_OR_NO_DATA",
          "An uploaded file has no readable table data (empty sheet or no columns).",
          "There was nothing to map — the migration stopped at ingestion.",
          True, False, [_REUPLOAD, _RESTART]),
    _Rule(r"could not parse|parse failed|read the excel|delimiter|encoding|decode",
          CATEGORY_FILE, "FILE_PARSE_FAILED",
          "A file could not be parsed into rows and columns.",
          "The file was not ingested — the migration cannot continue.",
          True, True, [_REUPLOAD, _RETRY]),
    # ── AI / LLM ────────────────────────────────────────────────────────────────────────────
    _Rule(r"rate.?limit|429|overloaded|capacity", CATEGORY_AI, "AI_RATE_LIMITED",
          "The AI service is temporarily rate-limited or over capacity.",
          "Semantic mapping could not run — the migration is paused, not lost.",
          True, True, [_WAIT_RETRY, _SUPPORT]),
    _Rule(r"anthropic|openai|\bllm\b|\bai\b request|model|completion|semantic.*(fail|error)",
          CATEGORY_AI, "AI_REQUEST_FAILED",
          "An AI request failed while resolving mappings.",
          "Semantic mapping could not complete — deterministic results are preserved.",
          True, True, [_RETRY, _SUPPORT]),
    # ── Timeout ─────────────────────────────────────────────────────────────────────────────
    _Rule(r"timed out|timeout|deadline", CATEGORY_TIMEOUT, "OPERATION_TIMED_OUT",
          "An operation took too long and timed out.",
          "The step did not finish — completed work up to this point is preserved.",
          True, True, [_WAIT_RETRY]),
    # ── Network / API ───────────────────────────────────────────────────────────────────────
    _Rule(r"connection refused|connection reset|connection error|network|unreachable|dns|getaddrinfo|ssl",
          CATEGORY_NETWORK, "NETWORK_UNAVAILABLE",
          "A network connection to a required service failed.",
          "The step could not run — retry once connectivity is restored.",
          True, True, [_WAIT_RETRY, _SUPPORT]),
    _Rule(r"5\d\d|service unavailable|bad gateway|upstream|api (error|unavailable)",
          CATEGORY_API, "UPSTREAM_SERVICE_ERROR",
          "A required service returned an error.",
          "The step could not complete — the service may be temporarily unavailable.",
          True, True, [_WAIT_RETRY, _SUPPORT]),
    # ── Dataset too large (Postgres 1 GB MaxAllocSize on the checkpoint) ──────────────────────
    # Must precede the generic Database rule: the raw error ("invalid memory alloc request size N")
    # is a psycopg error, but a plain retry re-hits the same limit — this needs a split, not a retry.
    _Rule(r"invalid memory alloc|cannot enlarge string buffer|out of memory|maxallocsize|requested size exceeds",
          CATEGORY_INTERNAL, "DATASET_TOO_LARGE",
          "This dataset is too large to process in a single migration run — it exceeded an "
          "internal storage limit while saving progress.",
          "Completed mapping steps are preserved. Split the upload into smaller files (or fewer / "
          "smaller sheets) and run them separately.",
          True, False, [_REUPLOAD, _SUPPORT]),
    # ── Database ────────────────────────────────────────────────────────────────────────────
    _Rule(r"asyncpg|psycopg|sqlalchemy|database|postgres|deadlock|could not connect.*(db|database|server)",
          CATEGORY_DATABASE, "DATABASE_ERROR",
          "A database operation failed.",
          "The mapped data could not be written — no partial data was committed.",
          True, True, [_RETRY, _SUPPORT]),
    # ── Permission ──────────────────────────────────────────────────────────────────────────
    _Rule(r"permission|forbidden|unauthor|403|401|access denied",
          CATEGORY_PERMISSION, "PERMISSION_DENIED",
          "The migration was blocked by a permissions error.",
          "The step could not run under the current permissions.",
          False, False, [_SUPPORT]),
    # ── Validation ──────────────────────────────────────────────────────────────────────────
    _Rule(r"confidence.*(below|threshold)|validation fail|test 1 fail|test 2 fail|referential integrity|below.*threshold",
          CATEGORY_VALIDATION, "VALIDATION_FAILED",
          "A validation check did not pass.",
          "The UDR was not finalised — review the flagged items to continue.",
          True, True, [_REVIEW, _RETRY]),
    _Rule(r"output generation|script generation|write.*fail|export",
          CATEGORY_INTERNAL, "OUTPUT_GENERATION_FAILED",
          "The output/UDR script could not be generated.",
          "No script was produced — the mappings are preserved and can be re-run.",
          True, True, [_RETRY, _SUPPORT]),
]

_FALLBACK = _Rule(
    r".*", CATEGORY_INTERNAL, "INTERNAL_ERROR",
    "The migration stopped with an unexpected error.",
    "The run did not finish — completed steps are preserved and you can retry.",
    True, True, [_RETRY, _SUPPORT],
)


def classify_migration_error(
    error: Any,
    *,
    step: str | None = None,
    debug: bool = False,
) -> dict:
    """Classify a raw error into the structured failure shape.

    ``error`` is an exception or a message string. ``step`` is the failed workflow step (e.g.
    "File Ingestion"). ``debug`` includes the raw technical reason verbatim; in production the
    technical_reason is the same user-safe message (never a stack trace).
    """
    raw = "" if error is None else (str(error))
    # Strip a leading "Type: " so keyword matching sees the message, not the class name.
    msg = raw.strip()
    rule = next((r for r in _RULES if r.pattern.search(msg)), _FALLBACK)

    # technical_reason: a trimmed single line — never a stack trace. Full text only in debug.
    reason = msg.splitlines()[0].strip() if msg else ""
    if not debug and len(reason) > 240:
        reason = reason[:240].rstrip() + "…"

    return {
        "step": step,
        "category": rule.category,
        "code": rule.code,
        "user_message": rule.user_message,
        "technical_reason": reason or rule.user_message,
        "impact": rule.impact,
        "recoverable": rule.recoverable,
        "retry_supported": rule.retry_supported,
        "suggested_actions": [dict(a) for a in rule.actions],
    }
