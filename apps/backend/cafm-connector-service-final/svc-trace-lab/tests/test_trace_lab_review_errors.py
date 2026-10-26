"""Phase 4's review errors (trace_lab/review_errors.py): each one becomes the house envelope with the right
status and a plain sentence through db.http_error. Open coding, sampling and the later taxonomy and label
modules raise these and their routes call http_error_for. No database."""
from __future__ import annotations

import re

import pytest
from fastapi import HTTPException

from trace_lab import auth, db, review_errors
from trace_lab.review_errors import Conflict, Forbidden, Invalid, NotFound, ReviewError, Stale

INTERNAL = re.compile(r"plenum_|tracelab|svc-|\bUDR\b|\bRAG\b|deep-?agents|agent_trace|turn_id|span", re.I)


def test_review_errors_keep_the_house_envelope():
    stale = review_errors.http_error_for(Stale({"id": "v2"}))
    assert isinstance(stale, HTTPException) and stale.status_code == 409
    assert stale.detail == {"reason": "stale", "error": "Changed since you opened it.", "current": {"id": "v2"}}
    assert review_errors.http_error_for(Invalid("quote_not_found")).status_code == 422
    assert review_errors.http_error_for(NotFound("batch_not_found")).status_code == 404
    assert review_errors.http_error_for(Forbidden("not_author")).status_code == 403
    assert review_errors.http_error_for(Conflict("already_withdrawn")).detail["reason"] == "already_withdrawn"
    custom = review_errors.http_error_for(Invalid("window_outside_retention", "Pick a period inside the last 90 days."))
    assert custom.detail["error"] == "Pick a period inside the last 90 days."
    assert custom.detail["reason"] == "window_outside_retention"


def test_a_stale_write_with_no_head_says_so():
    err = review_errors.http_error_for(Stale(None))
    assert err.status_code == 409 and err.detail["current"] is None and err.detail["reason"] == "stale"


def test_the_classes_are_one_family_with_their_statuses():
    assert [c.status for c in (ReviewError, NotFound, Invalid, Forbidden, Conflict, Stale)] == [400, 404, 422, 403, 409, 409]
    assert all(issubclass(c, ReviewError) for c in (NotFound, Invalid, Forbidden, Conflict, Stale))
    assert issubclass(Stale, Conflict)
    assert str(NotFound("turn_not_found")) == "turn_not_found"          # the reason is the exception text


def test_an_unknown_reason_still_has_a_plain_sentence_and_extra_fields_ride_along():
    err = review_errors.http_error_for(Invalid("something_new", hint="try again", count=3))
    assert err.detail == {"reason": "something_new", "error": "That could not be done.", "hint": "try again", "count": 3}


def test_an_extra_field_may_not_be_called_status():
    with pytest.raises(TypeError, match="status"):
        Invalid("quote_not_found", status=500)


def test_db_http_error_keeps_its_signature():
    plain = db.http_error(404, "batch_not_found", "That batch was not found.", retryable=False)
    assert plain.status_code == 404
    assert plain.detail == {"reason": "batch_not_found", "error": "That batch was not found.", "retryable": False}
    assert not hasattr(review_errors, "http_error")                       # one http_error, in db.py


def test_error_sentences_name_nothing_internal():
    assert review_errors.REVIEW_ERRORS
    for reason, sentence in review_errors.REVIEW_ERRORS.items():
        assert not INTERNAL.search(sentence), reason
        assert sentence.endswith("."), reason
        assert re.fullmatch(r"[a-z][a-z_]*", reason), reason


def test_the_auth_reasons_are_not_redefined_here():
    for code in ("reviewers_only", "name_a_company", "bad_company", "platform_only", "trace_lab_unavailable"):
        assert code not in review_errors.REVIEW_ERRORS, code
    assert auth.REVIEWERS_ONLY not in review_errors.REVIEW_ERRORS.values()


def test_the_phase_4_reasons_are_all_there():
    expected = {"turn_not_found", "annotation_not_found", "batch_not_found", "note_required", "anchor_required",
                "anchor_on_right", "quote_not_found", "span_not_in_turn", "window_outside_retention", "bad_window",
                "dimension_not_allowed", "needs_failure_modes", "pool_empty", "pool_changed", "not_author",
                "already_withdrawn", "stale"}
    assert expected <= set(review_errors.REVIEW_ERRORS)
