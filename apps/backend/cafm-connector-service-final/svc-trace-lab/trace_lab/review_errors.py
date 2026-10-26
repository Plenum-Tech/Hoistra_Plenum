"""Review errors for open coding, sampling and the modules after them (Phase 4).

annotations, sampling, and later the taxonomy and label modules raise these; their route modules turn
them into HTTP answers with http_error_for(), which builds db.http_error's house envelope
({"detail": {"reason": <code>, "error": <sentence>, **extra}}) with the error's own status.

The `error` text is shown on screen, so it is a plain sentence naming no internal table, service or id;
`reason` is the code the frontend switches on. Extra fields are keyword arguments only (never status,
reason or error). Who may ask is auth.py's rule (require_reader and require_reviewer, with their own
reasons), never repeated here.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from . import db

DEFAULT_SENTENCE = "That could not be done."


class ReviewError(Exception):
    """A refused review action. status is the HTTP status; reason the code; error the sentence."""

    status = 400

    def __init__(self, reason: str, error: str | None = None, **extra: Any) -> None:
        if "status" in extra:
            raise TypeError("status is the error's HTTP status, not an extra field")
        super().__init__(reason)
        self.reason = reason
        self.error = error or REVIEW_ERRORS.get(reason, DEFAULT_SENTENCE)
        self.extra = extra


class NotFound(ReviewError):
    status = 404


class Invalid(ReviewError):
    status = 422


class Forbidden(ReviewError):
    status = 403


class Conflict(ReviewError):
    status = 409


class Stale(Conflict):
    """A write whose `base` is not the current head. `current` is that head (or None)."""

    def __init__(self, current: dict[str, Any] | None) -> None:
        super().__init__("stale", current=current)
        self.current = current


REVIEW_ERRORS: dict[str, str] = {
    "turn_not_found": "That answer was not found.",
    "annotation_not_found": "There is no verdict or comment of yours to change.",
    "batch_not_found": "That batch was not found.",
    "note_required": "Say what went wrong first.",
    "anchor_required": "Select the text where it first went wrong.",
    "anchor_on_right": "A Right verdict has no highlighted text.",
    "quote_not_found": "The selected text is no longer in that step. Select it again.",
    "span_not_in_turn": "That step is not part of this answer.",
    "window_outside_retention": "Pick a more recent period; the steps of older answers are no longer kept.",
    "bad_window": "The period must end after it starts.",
    "dimension_not_allowed": "Batches can be split only by channel, where the question was routed, the asker's role or page.",
    "needs_failure_modes": "This option needs failure modes, which are not set up yet.",
    "pool_empty": "No answers are left to draw in that period.",
    "pool_changed": "Another batch took some of these answers just now. Preview again.",
    "not_author": "Only the person who wrote a comment can change it.",
    "already_withdrawn": "That batch was already withdrawn.",
    "stale": "Changed since you opened it.",
}


def http_error_for(exc: ReviewError) -> HTTPException:
    """A review error as db.http_error's envelope, with the error's status."""
    return db.http_error(exc.status, exc.reason, exc.error, **exc.extra)
