"""Hoist crons: the catalogue is fixed, a cadence is the report cards', a run's figures are summed up.

The end-to-end path - create, run through the real routes as the creator, pause, remove, a
plain user refused - was run against hoistra_test on 1 Oct 2026; these hold the pure rules.
"""
from __future__ import annotations

import pytest

from src.engines.crons import jobs as cron


def test_the_catalogue_is_routes_this_service_has():
    keys = {j["key"] for j in cron.catalogue()}
    assert {"energy_anomaly_scan", "compliance_expiry_scan", "question"} <= keys
    for k, spec in cron.CATALOGUE.items():
        if spec["call"] == "question":
            assert spec.get("needs_prompt")
            continue
        method, path, _q, _b = spec["call"]
        assert method == "POST" and path.startswith("/api/"), k


def test_every_job_suggests_a_cadence_the_cards_accept():
    from src.engines.reports import cards
    for j in cron.catalogue():
        assert cards.parse_refresh(j["suggested_refresh"]) == j["suggested_refresh"]
        assert j["suggested_label"]


def test_a_run_is_summed_up_in_its_numbers():
    s = cron.summarise({"ok": True, "meters_scanned": 58, "results": [1, 2], "note": "x" * 300,
                        "summary": {"alerts_created": 22}})
    assert s == {"alerts_created": 22, "ok": True, "meters_scanned": 58, "results": 2}
    assert cron.summarise([1, 2, 3]) == {"result": 3}
    assert cron.summarise("text") == {}


@pytest.mark.asyncio
async def test_an_unknown_job_or_a_question_without_one_is_refused():
    with pytest.raises(cron.CronError) as e:
        await cron.create_job(None, organization_id=None, owner_user_id=None, job_key="nope", refresh="1h")
    assert e.value.reason == "unknown_job"
    with pytest.raises(cron.CronError) as e:
        await cron.create_job(None, organization_id=None, owner_user_id=None, job_key="question", refresh="1h",
                              params={"prompt": "  "})
    assert e.value.reason == "no_prompt"
    with pytest.raises(cron.CronError) as e:
        await cron.create_job(None, organization_id=None, owner_user_id=None, job_key="energy_anomaly_scan",
                              refresh={"every_minutes": 1})
    assert e.value.reason == "bad_refresh"


@pytest.mark.asyncio
async def test_a_question_job_mails_its_answer_to_its_creator_and_a_failed_send_never_fails_the_run(monkeypatch):
    import uuid
    from src.shared import approvals

    sent = []

    async def fake_email(session, user_id):
        return "owner@example.com"

    async def fake_send(session, **kw):
        sent.append(kw)
        return {"ok": True, "status": "sent"}
    monkeypatch.setattr(cron, "_email", fake_email)
    monkeypatch.setattr(approvals, "send_platform_email", fake_send)
    job = {"id": str(uuid.uuid4()), "name": "Ask: compliance summary", "refresh_label": "Mon at 09:00",
           "timezone": "Asia/Dubai"}
    out = await cron._email_answer(None, job, uuid.uuid4(), uuid.uuid4(), "Two certificates lapsed.")
    assert out == {"emailed_to": "owner@example.com", "email_status": "sent"}
    assert sent[0]["to_address"] == "owner@example.com" and sent[0]["subject"].startswith("Hoistra · Ask: compliance summary")
    assert "Two certificates lapsed." in sent[0]["body"] and "Hoist Crons" in sent[0]["body"]

    async def boom(session, **kw):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(approvals, "send_platform_email", boom)
    out = await cron._email_answer(None, job, uuid.uuid4(), uuid.uuid4(), "x")
    assert out["email_status"] == "failed" and "smtp down" in out["email_error"]
