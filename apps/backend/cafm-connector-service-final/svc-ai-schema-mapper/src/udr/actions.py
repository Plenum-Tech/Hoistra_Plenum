"""Feature 4 — Activity Log Section 3: human-in-the-loop inline actions (AL.3/AL.4), PURE.

A red-flagged processing-log item becomes a 'closed query' with <=4 predefined options
(AL.3 AC3). The system records a chosen option and resumes (AL.3) — it NEVER auto-picks
(AL.4 AC3). After 15 min with no response (AL.4 AC1), a server-side sweep marks the action
``timed_out`` and raises a 'Pending Human Response' summary entry (red).

These functions are pure (no DB/LLM, ``now`` is injected) so the 15-min logic + the
<=4-option guard are unit-testable. The async DB side lives in :mod:`udr.actions_persist`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

ACTION_TIMEOUT_SECONDS = 15 * 60   # AL.4 AC1 — 15-minute no-response window
ACTION_TIMEOUT_TOLERANCE = 30      # AL.4 AC1 — ±30s sweep jitter allowance
MAX_OPTIONS = 4                    # AL.3 AC3 — at most 4 single-click options

VALID_KINDS = {"field_change", "approval", "escalation", "reassignment"}


def normalize_options(options) -> list[dict]:
    """Coerce options to ``[{id, label, value}]`` and enforce the <=4 cap (AL.3 AC3)."""
    out: list[dict] = []
    for i, o in enumerate((options or [])[:MAX_OPTIONS]):
        if isinstance(o, dict):
            out.append(
                {
                    "id": str(o.get("id", i)),
                    "label": str(o.get("label", o.get("value", ""))),
                    "value": o.get("value", o.get("label")),
                }
            )
        else:
            out.append({"id": str(i), "label": str(o), "value": o})
    return out


def build_inline_action(
    label: str,
    *,
    kind: str = "approval",
    options=None,
    action_id=None,
) -> dict:
    """Build one inline action (a closed query). ``options`` capped at 4; defaults to a
    plain Approve/Reject pair when none are given."""
    norm = normalize_options(
        options if options is not None else [
            {"id": "approve", "label": "Approve", "value": True},
            {"id": "reject", "label": "Reject", "value": False},
        ]
    )
    return {
        "id": action_id,
        "label": label,
        "kind": kind if kind in VALID_KINDS else "approval",
        "options": norm,
        "status": "pending",
    }


def deadline_from(created_at: datetime) -> datetime:
    """The 15-minute no-response deadline for an action created at ``created_at`` (AL.4 AC1)."""
    return created_at + timedelta(seconds=ACTION_TIMEOUT_SECONDS)


def is_overdue(deadline_at: datetime, now: datetime) -> bool:
    """True once ``now`` has reached the action's deadline (the sweep should run within
    ±ACTION_TIMEOUT_TOLERANCE of this)."""
    return now >= deadline_at


def select_overdue(actions: list[dict], now: datetime) -> list[dict]:
    """The still-``pending`` actions whose deadline has passed — the timeout sweep set."""
    out = []
    for a in actions or []:
        if a.get("status") != "pending":
            continue
        deadline = a.get("deadline_at")
        if deadline is not None and is_overdue(deadline, now):
            out.append(a)
    return out


def is_valid_option(action: dict, option_id: str) -> bool:
    """Whether ``option_id`` is one of the action's options — guards against resolving with
    an option the system never offered (and never auto-picks for the user)."""
    return str(option_id) in {str(o.get("id")) for o in action.get("options", []) or []}


def build_pending_response_entry(action: dict, *, trigger: str = "threshold") -> dict:
    """AL.4 — the 'Pending Human Response' Activity Summary entry raised when an action
    times out. Red, never carries a chosen option (the system did not proceed)."""
    label = action.get("label") or "a decision"
    return {
        "trigger": trigger,
        "trigger_detail": f"No human response within 15 minutes: {label}",
        "outcome": f"Pending human response — {label}",
        "status": "pending_human_input",
        "notif_color": "red",
        "refs": {"action_id": action.get("id"), "entry_id": action.get("entry_id")},
        "processing_log": None,
    }
