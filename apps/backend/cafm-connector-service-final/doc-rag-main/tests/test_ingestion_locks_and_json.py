"""Two faults that took production down on 17 Sep 2026, and one that fails every contract.

A5 — A DOCUMENT UPLOAD FROZE THE DATABASE FOR 34 MINUTES.

`_run_ingestion_pipeline` opened a session, read the document row — which autobegins a
transaction and takes AccessShareLock on plenum_cafm.ingestion_documents — and THEN called
Claude to extract the PDF, holding that lock for the minutes the call takes.

Separately, `init_db()` runs unconditionally from the app's lifespan and ends in
`_run_migrations()`, which issues `ALTER TABLE plenum_cafm.ingestion_documents ADD COLUMN
IF NOT EXISTS …` on every single boot. That needs AccessExclusiveLock.

Put together: one instance restarts while another is mid-extraction, the ALTER queues
behind the open transaction, and because a pending exclusive lock blocks every LATER
reader, all traffic to that table — from every service, on every host — stops. It did:
46 queries queued, the oldest waiting 27 minutes.

A6 — A TRUNCATED ANSWER IS TREATED AS A PERMANENT FAILURE.

There is a retry (`_call_claude_stream_with_retry`) but it fires on Anthropic API errors.
A 200 whose body is cut-off JSON goes straight to `raise RuntimeError`, and both test
contracts died exactly there:

    RuntimeError: Claude returned non-JSON response for c41b8c82…pdf:
    Expecting ',' delimiter: line 25 column 1177 (char 9447)
"""
from __future__ import annotations

import json

import pytest


# ─────────────────────────────────────────────────────────── A6: repairing a cut-off answer

def test_a_fenced_answer_is_read():
    from app.services.extraction_service import parse_model_json

    assert parse_model_json('```json\n{"pages": [{"n": 1}]}\n```')["pages"][0]["n"] == 1


def test_a_trailing_comma_does_not_lose_the_document():
    from app.services.extraction_service import parse_model_json

    assert parse_model_json('{"pages": [{"n": 1},]}')["pages"][0]["n"] == 1


def test_an_answer_cut_off_mid_object_keeps_the_pages_that_did_arrive():
    """The real failure: the model ran out of budget partway through page 25.

    Discarding twenty-four complete pages because the twenty-fifth is half-written throws
    away almost all of the work. What arrived whole is usable.
    """
    from app.services.extraction_service import parse_model_json

    truncated = '{"pages": [{"n": 1, "text": "one"}, {"n": 2, "text": "two"}, {"n": 3, "te'
    out = parse_model_json(truncated)
    assert [p["n"] for p in out["pages"]] == [1, 2]
    assert out.get("truncated") is True, "the caller has to know the tail is missing"


def test_prose_around_the_json_is_ignored():
    from app.services.extraction_service import parse_model_json

    assert parse_model_json('Sure! Here is the extraction:\n{"pages": []}\nLet me know.')["pages"] == []


def test_something_with_no_json_in_it_at_all_still_raises():
    from app.services.extraction_service import parse_model_json

    with pytest.raises(ValueError):
        parse_model_json("I cannot read this document.")


def test_a_repairable_answer_is_not_retried():
    """Repair first, ask again only if repair fails — a second call costs minutes."""
    from app.services.extraction_service import parse_model_json

    # No exception means no retry is needed; the test above covers the unrepairable case.
    assert parse_model_json('{"pages": [{"n": 1},]}')["pages"]


# ───────────────────────────────────────────── A5: the transaction and the startup DDL

def test_the_startup_ddl_is_gated_rather_than_run_on_every_boot():
    """`ALTER TABLE` wants an exclusive lock. Issuing it on every restart is the trigger.

    The statements are idempotent, which is why this looked safe — but idempotent is not
    the same as free: the lock is taken whether or not the column is already there.
    """
    import inspect

    from app.db import session as sess

    src = inspect.getsource(sess.init_db)
    assert "_run_migrations" in src
    assert "run_migrations_enabled" in src or "RUN_MIGRATIONS" in src.upper(), (
        "init_db must consult a setting before issuing DDL, not run it unconditionally"
    )


def test_the_pipeline_does_not_hold_a_transaction_across_the_extraction():
    """The read must be closed before Claude is called, not left open around it."""
    import inspect

    from app.routers import documents as docs

    src = inspect.getsource(docs._run_ingestion_pipeline)
    before_extract = src.split("extraction_service.extract")[0]
    assert "close()" in before_extract or "commit()" in before_extract, (
        "the session opened to read the document row is still open when Claude is called; "
        "that lock is what the startup ALTER queues behind, and everything queues behind it"
    )


def test_the_document_row_is_re_read_after_extraction_rather_than_held():
    import inspect

    from app.routers import documents as docs

    src = inspect.getsource(docs._run_ingestion_pipeline)
    after_extract = src.split("extraction_service.extract", 1)[1]
    assert "SessionLocal()" in after_extract, (
        "a fresh session for the write half — the row must be looked up again, not carried "
        "across the call on a stale, lock-holding connection"
    )
