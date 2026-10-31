"""Independent adapter expectations from retained fictional source records."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json

import pytest
from fastapi import HTTPException

from src.api.homework_identity import TestPrincipal
from src.services.contract_hw1.contracts import ConfigProposal, InvoiceQuery, PerformanceQuery
from src.services.contract_hw1.fixtures import BUILDING, VENDOR, dataset
from src.services.contract_hw1.service import ContractService, _performance, _invoice_diagnostic

PRINCIPAL = TestPrincipal("contract-edge-test", "portfolio_manager", "hw1-org-a",
                          (BUILDING, "hw1-building-a2"))


class Repo:
    def __init__(self):
        self.rows = deepcopy(dataset())
        self.rows["records"].append({"id": VENDOR, "kind": "vendor", "org": "hw1-org-a",
            "buildings": [BUILDING], "data": {}})
        self.rows["proposals"] = []

    async def list(self, table, kind=None, lock=False):
        return [r for r in self.rows[table] if r["org"] == PRINCIPAL.organization_id
                and (kind is None or r["kind"] == kind)]

    async def get(self, table, identifier, lock=False):
        row = next((r for r in self.rows[table] if r["id"] == identifier), None)
        if row is None:
            raise HTTPException(404, "Record unavailable")
        return row


def query(number=0):
    return PerformanceQuery(vendor_id=VENDOR if number == 0 else BUILDING + "-vendor-" + str(number),
        period_start="2026-10-01", period_end="2026-10-31", building_ids=[BUILDING],
        idempotency_key="independent-adapter-test")


async def test_independent_per_order_caps_and_month_average():
    result = await _performance(Repo(), PRINCIPAL, query())
    # 101: 25+25+0+0+15=65; 102: 0+0+20+15+0=35; 103: min(85,60)=60.
    assert result["overall_score"] == 53  # half-up average of 160/3
    assert sorted(x["score"] for x in result["work_order_details"].values()) == [35, 60, 65]
    boundary = await _performance(Repo(), PRINCIPAL, query(901))
    assert boundary["overall_score"] == 80  # expiry day 100; following day min(85,60)
    details = boundary["work_order_details"]
    assert not details["hw1-contract-edge-expiry-day-901"]["accreditation_cap_applied"]
    assert details["hw1-contract-edge-lapse-day-901"]["accreditation_cap_applied"]


async def test_recall_looks_past_month_end_and_includes_exact_window_only():
    result = await _performance(Repo(), PRINCIPAL, query(905))
    assert result["eligible_work_orders"] == 1
    assert result["overall_score"] == 65
    assert result["work_order_details"]["hw1-contract-edge-month-end-905"]["recall"] is True
    assert "hw1-contract-edge-next-month-905-source" in result["source_versions"]


@pytest.mark.parametrize("seconds,expected", [(0,65), (1,100)])
async def test_recall_boundary_does_not_truncate_elapsed_days(seconds, expected):
    repo = Repo()
    row = await repo.get("records", "hw1-contract-edge-next-month-905")
    source = await repo.get("sources", row["data"]["source_id"])
    stamp = datetime.fromisoformat("2026-10-31T08:00:00+00:00") + timedelta(days=30, seconds=seconds)
    # This unit fixture is built in memory before scoring; no persisted original is edited.
    fields = {**source["data"]["fields"], "raised_at": stamp.isoformat(),
        "response_at": (stamp + timedelta(hours=1)).isoformat(),
        "completed_at": (stamp + timedelta(hours=2)).isoformat()}
    original = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    source["data"].update(fields=fields, text=original, content_sha256=hashlib.sha256(original.encode()).hexdigest())
    row["data"].update(fields)
    result = await _performance(repo, PRINCIPAL, query(905))
    assert result["overall_score"] == expected


@pytest.mark.parametrize("number,reason", [
    (903, "contract_target_missing"), (904, "accreditation_expiry_unknown"),
    (906, "same_signing_date_contract_tie_requires_review"),
])
async def test_unassessable_source_rows_remain_visible_exclusions(number, reason):
    result = await _performance(Repo(), PRINCIPAL, query(number))
    assert result["overall_score"] is None
    assert result["eligible_work_orders"] == 0
    assert [x["reason"] for x in result["excluded_work_orders"]] == [reason]


async def test_mirror_mismatch_fails_without_touching_original():
    repo = Repo()
    before = deepcopy(repo.rows["sources"])
    with pytest.raises(HTTPException) as error:
        await _performance(repo, PRINCIPAL, query(902))
    assert error.value.status_code == 409
    assert before == repo.rows["sources"]


async def test_invoice_separate_diagnostic_checks_day_rate_and_large_delta():
    q = query(901)
    result = await _invoice_diagnostic(Repo(), PRINCIPAL,
        InvoiceQuery(**q.model_dump(exclude={"idempotency_key"})))
    line = result["invoices"][0]["line_diagnostics"][0]
    assert line["delta_gbp"] == "600.00"  # (380 - 640/8) * 2
    assert line["status"] == "flagged"
    assert line["adversary_gate"]["required"] is True
    assert line["adversary_gate"]["independent_arithmetic_agrees"] is True
    assert result["score_component"] is False
    assert len(result["source_versions"]) == 3


async def test_invoice_cannot_match_other_building_even_in_same_portfolio():
    q = query(907).model_dump(exclude={"idempotency_key"})
    q["building_ids"] = list(PRINCIPAL.building_ids)
    result = await _invoice_diagnostic(Repo(), PRINCIPAL, InvoiceQuery(**q))
    invoice = result["invoices"][0]
    assert invoice["matched_count"] == 0
    assert invoice["flagged_count"] == invoice["unmatched_count"] == 1


async def test_config_confirmed_retry_cannot_change_hash():
    repo = Repo()
    repo.rows["proposals"] = [{"id": "p", "kind": "contract_scoring_configuration",
        "data": {"actor_id": PRINCIPAL.user_id, "status": "confirmed", "idempotency_key": "retry-key",
                 "preview_hash": "a" * 64, "current_version": 3, "result_version": 4}}]

    class Store:
        @asynccontextmanager
        async def transaction(self, *args, **kwargs):
            yield repo

        async def audit(self, *args, **kwargs):
            pass

    with pytest.raises(HTTPException) as error:
        await ContractService(Store()).confirm_configuration(PRINCIPAL, "p", {
            "preview_hash": "b" * 64, "expected_version": 3, "idempotency_key": "retry-key"}, "test-request")
    assert error.value.status_code == 409


def test_config_rejects_sixth_invoice_component_and_invalid_total():
    parameters = dataset()["records"][0]["data"]["parameters"]
    with pytest.raises(ValueError):
        ConfigProposal(**{**parameters, "weights": {**parameters["weights"], "invoice_pct": 0}}, reason="invalid sixth component")
