"""Confirming a contract's terms can be undone.

Confirming turns extracted readings into the numbers every later judgement is enforced
with. Until now there was no way back: `extract`, `ingest`, `patch` and `confirm` were the
whole router, and a set confirmed by mistake could only be replaced by re-ingesting the
document it came from.

That is not academic. On 18 Sep 2026 a contract was confirmed whose extraction had read
0 of 16 terms — the document was a property management agreement, not a service contract,
so every value behind it is a platform default. Sixteen assumed figures are now the agreed
terms for that vendor, and the only documented escape was to upload the file again.

Reopening is the inverse of confirming and is audited the same way: the row goes back to
draft, scoring refuses it again, and who reopened it is recorded.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.engines.contract_performance import parameters as params_mod

ORG = UUID("00000000-0000-0000-0000-000000000001")
USER = UUID("00000000-0000-0000-0001-000000000017")


class _Row:
    def __init__(self, status="confirmed"):
        self.id = uuid4()
        self.organization_id = ORG
        self.status = status
        self.confirmed_by = USER if status == "confirmed" else None
        self.confirmed_at = datetime(2026, 9, 18, 7, 1, 51, tzinfo=timezone.utc) if status == "confirmed" else None
        self.updated_at = None
        self.vendor_id = None
        self.contract_id = None
        self.document_id = None
        self.contract_ref = "UKRI-2938"
        self.signed_date = None
        self.defaults_used = []
        self.overrides_log = []
        self.field_sources = {}
        self.created_at = None
        for f in (
            "sla_response_p1_hours", "sla_response_p2_hours", "sla_response_p3_hours",
            "sla_response_p4_hours", "sla_completion_p1_hours", "sla_completion_p2_hours",
            "sla_completion_p3_hours", "sla_completion_p4_hours", "labour_day_rate",
            "labour_hour_rate", "overtime_rate", "call_out_rate", "payment_terms",
        ):
            setattr(self, f, None)
        for f in ("parts_pricing_json", "kpi_clauses_json", "ppm_obligations_json", "task_criticality_json"):
            setattr(self, f, {})


class FakeSession:
    def __init__(self, row):
        self._row = row
        self.audits: list[dict] = []
        self.committed = False

    async def get(self, model, pk):
        return self._row

    async def execute(self, stmt, params=None):
        class _R:
            def mappings(self_inner): return self_inner
            def first(self_inner): return None
            def all(self_inner): return []
            def scalars(self_inner): return self_inner
        return _R()

    def add(self, obj):
        self.audits.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_reopening_returns_a_confirmed_set_to_draft():
    row = _Row("confirmed")
    s = FakeSession(row)
    out = await params_mod.reopen_contract_parameters(s, row.id, reopened_by=USER)
    assert out["ok"] is True
    assert row.status == "draft", "scoring must refuse it again, which is what draft means"
    assert s.committed


@pytest.mark.asyncio
async def test_the_confirmation_itself_is_cleared_not_merely_overridden():
    row = _Row("confirmed")
    s = FakeSession(row)
    await params_mod.reopen_contract_parameters(s, row.id, reopened_by=USER)
    assert row.confirmed_by is None, (
        "a draft that still names a confirmer reads as confirmed to anything checking the "
        "column rather than the status"
    )
    assert row.confirmed_at is None


@pytest.mark.asyncio
async def test_who_reopened_it_is_audited():
    row = _Row("confirmed")
    s = FakeSession(row)
    await params_mod.reopen_contract_parameters(s, row.id, reopened_by=USER)
    kinds = [getattr(a, "action_type", None) for a in s.audits]
    assert "contract_params.reopen" in kinds, f"no reopen audit row; got {kinds}"
    row_audit = next(a for a in s.audits if getattr(a, "action_type", "") == "contract_params.reopen")
    assert str(USER) in str(getattr(row_audit, "actor", "")), (
        "undoing an agreement is as much a decision as making one"
    )


@pytest.mark.asyncio
async def test_reopening_a_draft_is_harmless_rather_than_an_error():
    # Two people on the same screen, or a double click. The end state is what was asked for.
    row = _Row("draft")
    s = FakeSession(row)
    out = await params_mod.reopen_contract_parameters(s, row.id, reopened_by=USER)
    assert out["ok"] is True
    assert row.status == "draft"


@pytest.mark.asyncio
async def test_a_contract_that_does_not_exist_is_reported_not_invented():
    class _Empty(FakeSession):
        async def get(self, model, pk):
            return None

    out = await params_mod.reopen_contract_parameters(_Empty(None), uuid4(), reopened_by=USER)
    assert out["ok"] is False
    assert out["error"] == "not_found"


def test_the_route_is_registered_and_sits_beside_confirm():
    from src.app import app

    def paths(routes):
        got = set()
        for r in routes:
            p = getattr(r, "path", None)
            if p:
                got.add(p)
            inner = getattr(r, "original_router", None)
            if inner is not None:
                got |= paths(getattr(inner, "routes", []))
        return got

    all_paths = paths(app.routes)
    assert "/api/contract-performance/contracts/{parameters_id}/reopen" in all_paths
    assert "/api/contract-performance/contracts/{parameters_id}/confirm" in all_paths, (
        "the pair is the point — an action with no inverse is a trap"
    )


# ───────────────────────────── confirming is a decision, and it is gated as one

"""On 21 Sep 2026 the orchestrator created contract 4e9d3371 and confirmed it ONE SECOND
later, unattended, with confirmed_by NULL and zero terms read from the document. Seventeen
platform defaults became the agreed terms for a vendor because a skill recipe listed
`confirm_contract_parameters` as step four of an ingest chain — and told the model, wrongly,
that "unconfirmed parameters still score", so confirming looked like tidying up.

The recipe is corrected, but a prompt is advice. These are the rules."""


def _row_with(sources, status="draft"):
    r = _Row(status)
    r.field_sources = sources
    r.confirmed_by = None
    r.confirmed_at = None
    return r


@pytest.mark.asyncio
async def test_a_set_that_read_nothing_from_its_document_cannot_be_confirmed():
    row = _row_with({"labour_day_rate": "default", "payment_terms": "default"})
    s = FakeSession(row)
    out = await params_mod.confirm_contract_parameters(s, row.id, confirmed_by=USER)
    assert out["ok"] is False
    assert out["error"] == "no_contract_terms"
    assert row.status == "draft", "seventeen assumptions must not become the agreed terms"
    assert not s.committed


@pytest.mark.asyncio
async def test_a_set_with_real_terms_confirms_normally():
    row = _row_with({"sla_response_p1_hours": "contract", "labour_day_rate": "default"})
    s = FakeSession(row)
    out = await params_mod.confirm_contract_parameters(s, row.id, confirmed_by=USER)
    assert out["ok"] is True
    assert row.status == "confirmed"


@pytest.mark.asyncio
async def test_a_confirmation_nobody_is_named_for_is_refused():
    """The agent called this with confirmed_by=None. A decision with no decider is not one,
    and the audit row would read `actor: pm` — indistinguishable from a person."""
    row = _row_with({"sla_response_p1_hours": "contract"})
    s = FakeSession(row)
    out = await params_mod.confirm_contract_parameters(s, row.id, confirmed_by=None)
    assert out["ok"] is False
    assert out["error"] == "confirmed_by_required"
    assert row.status == "draft"
    assert not s.committed


@pytest.mark.asyncio
async def test_the_filename_fallback_does_not_count_as_a_contract_term():
    # contract_ref falls back to the filename when the document names no reference; a
    # uuid-prefixed filename is not something the contract said.
    row = _row_with({"contract_ref": "filename", "labour_day_rate": "default"})
    s = FakeSession(row)
    out = await params_mod.confirm_contract_parameters(s, row.id, confirmed_by=USER)
    assert out["ok"] is False
    assert out["error"] == "no_contract_terms"
