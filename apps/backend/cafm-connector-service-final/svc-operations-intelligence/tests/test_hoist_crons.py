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


@pytest.mark.asyncio
async def test_a_job_mails_every_recipient_once_and_one_bad_address_is_a_partial_send(monkeypatch):
    import uuid
    from src.shared import approvals

    sent = []

    async def fake_email(session, user_id):
        return "Owner@Example.com"

    async def fake_send(session, **kw):
        if kw["to_address"] == "down@example.com":
            raise RuntimeError("mailbox unavailable")
        sent.append(kw["to_address"])
        return {"ok": True, "status": "sent"}
    monkeypatch.setattr(cron, "_email", fake_email)
    monkeypatch.setattr(approvals, "send_platform_email", fake_send)
    job = {"id": str(uuid.uuid4()), "name": "Compliance expiry scan", "refresh_label": "Mon, Thu at 15:45",
           "timezone": "Asia/Dubai", "params": {"recipients": ["owner@example.com", "fm@example.com"]}}
    out = await cron._email_answer(None, job, uuid.uuid4(), uuid.uuid4(), "Alerts created: 25")
    assert sent == ["owner@example.com", "fm@example.com"] and out["email_status"] == "sent"
    # Recipients only: the creator did not tick "email me".
    sent.clear()
    job["params"]["recipients"] = ["fm@example.com", "down@example.com"]
    out = await cron._email_answer(None, job, uuid.uuid4(), uuid.uuid4(), "x", to_owner=False)
    assert sent == ["fm@example.com"]
    assert out["email_status"] == "partial" and out["email_failed"] == "down@example.com"


def test_recipients_are_cleaned_and_a_typo_is_refused():
    assert cron.clean_recipients("A@x.com; b@y.org, a@x.com  c@z.io") == ["a@x.com", "b@y.org", "c@z.io"]
    assert cron.clean_recipients(None) == []
    with pytest.raises(cron.CronError) as e:
        cron.clean_recipients(["ok@x.com", "not-an-address"])
    assert e.value.reason == "bad_recipient"
    with pytest.raises(cron.CronError) as e:
        cron.clean_recipients([f"u{i}@x.com" for i in range(cron.MAX_RECIPIENTS + 1)])
    assert e.value.reason == "too_many_recipients"
    assert "Alerts created: 25" in cron.result_text({"alerts_created": 25, "ok": True, "email_status": "sent"})


def test_the_run_email_is_a_report_with_a_link_to_the_jobs_page(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(cron.settings, "public_app_url", "https://app.example.com/", raising=False)
    url = cron.report_url("6f1c0d1e-0000-4000-8000-000000000001")
    assert url == "https://app.example.com/?cron=6f1c0d1e-0000-4000-8000-000000000001"
    job = {"id": "x", "name": "Compliance expiry scan", "refresh_label": "Mon, Thu at 15:45", "timezone": "Asia/Dubai"}
    subject, text, html = cron.report_email(job, "Blocks set: 6\nAlerts created: 25", failed=False, took_ms=1200,
                                            when=datetime(2026, 10, 1, 11, 45, tzinfo=timezone.utc),
                                            week={"runs": 2, "failed": 0}, url=url)
    assert subject == "Hoistra · Compliance expiry scan · 01 Oct 2026"
    assert "ran 01 Oct 2026 15:45 (Asia/Dubai), took 1.2 s" in text and "Alerts created: 25" in text
    assert "Last 7 days: 3 runs, 0 failed" in text and url in text
    assert "See the full report" in html and url in html and "<td" in html
    s2, t2, h2 = cron.report_email(job, "register <down>", failed=True, url="")
    assert s2.startswith("Hoistra · Compliance expiry scan failed") and "What went wrong" in t2
    assert "&lt;down&gt;" in h2 and "See the full report" not in h2
