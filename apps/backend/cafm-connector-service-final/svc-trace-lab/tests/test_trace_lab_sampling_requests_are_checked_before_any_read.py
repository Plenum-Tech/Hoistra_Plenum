"""A batch request is refused before any database is read: a size or review count the table would reject, a flagged
batch asked to preview, a company-less read of one company's pool. Both engines are made to fail the test if touched.
No database, no network."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.services.principal import Principal
from trace_lab import db, sampling
from trace_lab.review_errors import Invalid

NOW = datetime.now(timezone.utc)
ORG = str(uuid.uuid4())
WINDOW = {"since": NOW - timedelta(days=3), "until": NOW + timedelta(hours=1)}
ME = Principal(user_id=uuid.uuid4(), email="a@example.com", organization_id=uuid.UUID(ORG), role="admin",
               can_ingest=True, building_ids=None)


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    def boom():
        raise AssertionError("no database may be read before the request is refused")

    monkeypatch.setattr(db, "main_engine", boom)
    monkeypatch.setattr(db, "lab_engine", boom)


def req(**kw):
    return {"plan_step": "initial_uniform", "strategy": "uniform", "k": 15, "seed": 7, "channel": "chat",
            "reviews_required": 1, **WINDOW, **kw}


@pytest.mark.parametrize("kw", [{"k": 0}, {"k": 201}, {"k": -3}, {"k": "15"}, {"k": True}, {"k": 1.5}, {"k": None},
                                {"reviews_required": 0}, {"reviews_required": 4}, {"reviews_required": True}])
async def test_a_size_the_table_would_reject_is_refused_first(kw):
    for call in (sampling.create_batch, sampling.preview):
        with pytest.raises(Invalid) as e:
            await call(ME, ORG, req(**kw))
        assert e.value.reason == "bad_size" and e.value.error and e.value.status == 422


@pytest.mark.parametrize("k", [1, 15, 200])
def test_the_sizes_the_table_accepts_pass(k):
    sampling.check_size(req(k=k))
    sampling.check_size(req(k=k, reviews_required=3))


def test_a_flagged_batch_has_no_size_to_check():
    sampling.check_size({"strategy": "monitor_flagged", "items": [{"turn_id": "t"}], **WINDOW})


async def test_a_flagged_batch_cannot_be_previewed():
    flagged = {"strategy": "monitor_flagged", "seed": 1, "items": [{"turn_id": "turn-1", "reason": "x"}], **WINDOW}
    with pytest.raises(Invalid) as e:
        await sampling.preview(ME, ORG, flagged)
    assert e.value.reason == "bad_strategy"


@pytest.mark.parametrize("bad", [{"channel": "golden"}, {"channel": "email"}, {"dimension": "outcome"},
                                 {"since": NOW - timedelta(days=500)}, {"mode_id": "m1"}, {"source": "depth_scan"}])
async def test_the_pure_request_checks_still_come_first(bad):
    with pytest.raises(Invalid):
        await sampling.create_batch(ME, ORG, req(**bad))


@pytest.mark.parametrize("org", [None, ""])
async def test_one_companys_reads_refuse_no_company(org):
    with pytest.raises(Invalid) as e:
        await sampling.pool(ME, org, **WINDOW)
    assert e.value.reason == "name_a_company"
    with pytest.raises(Invalid):
        await sampling.map_view(ME, org, **WINDOW)
