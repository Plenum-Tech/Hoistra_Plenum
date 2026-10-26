"""trace_lab.jobs without a database: kinds and statuses agree with 0001, and every refusal happens
before any database work."""
from __future__ import annotations

import os
import re
import socket
from decimal import Decimal
from pathlib import Path

import pytest

from trace_lab import db, jobs, migrate


@pytest.fixture(autouse=True)
def _no_database(monkeypatch):
    def no_db():
        raise AssertionError("no database in this test")

    monkeypatch.setattr(db, "lab_engine", no_db)


def test_kinds_and_statuses_match_the_ddl():
    sql = (migrate.MIGRATIONS_DIR / "0001_foundation.sql").read_text(encoding="utf-8")
    kinds = re.findall(r"'([a-z_]+)'", re.search(r"CHECK \(kind IN \((.*?)\)\)", sql, re.S).group(1))
    statuses = re.findall(r"'([a-z_]+)'", re.search(r"CHECK \(status IN \((.*?)\)\)", sql, re.S).group(1))
    assert tuple(kinds) == jobs.KINDS and tuple(statuses) == jobs.STATUSES
    assert set(jobs.OPEN_STATUSES) <= set(jobs.STATUSES) and set(jobs.FINISH_STATUSES) <= set(jobs.STATUSES)
    assert "replay" not in jobs.KINDS


@pytest.mark.parametrize("kw, why", [
    ({"kind": "replay"}, "unknown job kind"),
    ({"params": {"Access_Token": "x"}}, "credentials"),
    ({"params": {"authorization": "Bearer x"}}, "credentials"),
    ({"usd_cap": None}, "usd_cap"),
])
async def test_create_refuses_before_touching_the_database(kw, why):
    args = dict(kind="golden_run", scope_key="golden_run:x", organization_id=None, params={},
                usd_cap=Decimal("1"), actor_id=None)
    args.update(kw)
    with pytest.raises(ValueError, match=why):
        await jobs.create(**args)


async def test_finish_refuses_a_status_it_cannot_end_in():
    with pytest.raises(ValueError):
        await jobs.finish("6f0c1d5e-4a7b-4c1e-9d0e-0f1a2b3c4d5e", "running")


async def test_get_answers_none_for_a_malformed_id_without_a_query():
    assert await jobs.get("not-a-uuid", organization_id=None) is None


def test_worker_id_names_host_and_pid():
    assert jobs.worker_id() == f"{socket.gethostname()}:{os.getpid()}"


def test_jobs_never_use_the_main_engine():
    text = Path(jobs.__file__).read_text(encoding="utf-8")
    assert "main_engine" not in text and "src.database" not in text and "plenum_cafm" not in text
    assert text.count("db.lab_engine()") >= 10
