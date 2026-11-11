"""The parts of the push that need no database (11 Nov 2026, task plan B5.1 and B5.3): where the address comes from
(the environment, never the code), what is shown of it (the host and database, nothing else), which addresses are
refused, and the one line a person reads afterwards."""
from __future__ import annotations

import pytest

from trace_lab import sync_push


def test_the_tables_go_in_an_order_that_never_breaks_a_reference():
    assert sync_push.TABLES == ("run_tags", "spec_sections", "scenarios", "sessions", "traces", "observations", "scores")


def test_peer_name_is_the_host_and_database_and_nothing_else():
    url = "postgresql+asyncpg://sync_user:secret@db.example.com:5432/hoist_tracelab?ssl=require"
    assert sync_push.peer_name(url) == "db.example.com/hoist_tracelab"
    other = "postgresql+asyncpg://sync_user:secret@db.example.com:6432/hoist_tracelab"
    assert sync_push.peer_name(other) == "db.example.com:6432/hoist_tracelab", "two servers on one host are two places"


def test_the_address_comes_from_the_environment():
    url = "postgresql+asyncpg://u:p@db.example.com/hoist_tracelab"
    assert sync_push.dest_url({"DEV_DB_URL": url}) == url
    assert sync_push.dest_url({"DEV_DB_URL": "postgresql://u:p@db.example.com/hoist_tracelab"}) == url
    assert sync_push.dest_url({"DEV_DB_URL": "postgres://u:p@db.example.com/hoist_tracelab"}) == url


def test_an_unset_address_is_refused():
    with pytest.raises(sync_push.Refused) as e:
        sync_push.dest_url({})
    assert e.value.reason == "not_configured" and "DEV_DB_URL" in e.value.error


@pytest.mark.parametrize("bad", [
    "mysql://u:secretpw@db.example.com/x",
    "not a url secretpw",
    "postgresql+asyncpg://u:secretpw@db.example.com/",                        # no database named
    "postgresql+asyncpg://u:secretpw@db.example.com/x?host=other.example.com",   # would connect somewhere else
    "postgresql+asyncpg://u:secretpw@db.example.com/x#?host=other.example.com",
])
def test_a_bad_address_is_refused_without_repeating_it(bad):
    with pytest.raises(sync_push.Refused) as e:
        sync_push.dest_url({"DEV_DB_URL": bad})
    assert e.value.reason == "bad_url" and "secretpw" not in e.value.error and "other.example.com" not in e.value.error


def test_the_line_a_person_reads_after_a_push():
    out = {"status": "done", "dry_run": False, "peer": "db.example.com/hoist_tracelab",
           "pushed": {"run_tags": 1, "spec_sections": 0, "scenarios": 250, "sessions": 250, "traces": 312,
                      "observations": 9804, "scores": 0}}
    assert sync_push.summary_line(out) == ("Pushed to db.example.com/hoist_tracelab: 250 sessions, 312 traces, "
                                           "9,804 spans, 0 scores, 250 scenarios, 1 run tag, 0 spec sections.")
    dry = sync_push.summary_line({**out, "dry_run": True})
    assert dry.startswith("Would push to db.example.com/hoist_tracelab: 250 sessions")
    failed = sync_push.summary_line({**out, "status": "failed", "error": "traces: connection lost"})
    assert failed.startswith("The push to db.example.com/hoist_tracelab stopped part of the way (traces: connection lost).")
    assert failed.endswith("Run it again to finish.")
    held = sync_push.summary_line({**out, "conflicts": {"sessions": 0, "traces": 2, "observations": 14}})
    assert held.endswith("0 spec sections. 16 rows were left out because the dev database already holds something "
                         "else in their place (2 traces, 14 spans). Nothing there was changed.")
    would = sync_push.summary_line({**out, "dry_run": True, "conflicts": {"scores": 1}})
    assert "1 row would be left out because the dev database already holds something else in its place (1 score)." in would


def test_a_failure_is_named_by_its_kind_never_by_the_drivers_words():
    class DbError(Exception):
        def __init__(self, state: str) -> None:
            super().__init__("DETAIL: Failing row contains (a tenant's answer text)")
            self.orig = type("Orig", (), {"sqlstate": state})()

    assert sync_push.failure_kind(DbError("42501")) == "the login is not allowed to write there"
    assert sync_push.failure_kind(DbError("23503")) == "the dev database refused a row"
    assert sync_push.failure_kind(DbError("08006")) == "the connection was lost"
    assert sync_push.failure_kind(ConnectionResetError()) == "the connection was lost"
    assert sync_push.failure_kind(TimeoutError()) == "it took too long"
    assert sync_push.failure_kind(ValueError("row 7: the answer text")) == "an unexpected error (ValueError)"


def test_the_command_line_says_when_there_is_nowhere_to_push_to(monkeypatch, capsys):
    monkeypatch.delenv("DEV_DB_URL", raising=False)
    assert sync_push.main(["--dry-run"]) == 2
    assert "DEV_DB_URL" in capsys.readouterr().err
