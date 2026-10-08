"""A contract priced by the hour, per trade, had nowhere to put its rates.

Read against the source PDF on 17 Sep 2026. `04_WKU_Facilities-Management-SLA-2022.pdf`
carries a full rate card in Appendix B — "WKU CHARGE RATE SCHEDULE — Estimated Average
Labor Charges (Hourly rates)" — twelve trades, each with a straight and an overtime figure:

    Area Technicians  44.82 / 67.23      Plumbers    51.71 / 77.56
    Electricians      48.20 / 73.29      HVAC        51.40 / 77.10
    Painters          34.12 / 51.17      Custodial   18.18 / 27.27   (+6 more)

The system stored none of it. `raw_extraction` for that contract contains no rate field at
all, because CONTRACT_FIELDS — which is joined straight into the prompt — asked only for
`labour_day_rate`. The panel then showed £350/day sourced "default" and told the reader the
contract "did not state" a rate. It states twelve.

Two separate faults:

  THE MODEL WAS NEVER ASKED for an hourly rate, though the heuristic fallback parses one
  and the display has a row for it.

  ONE SCALAR CANNOT HOLD A RATE CARD. Twelve trades times two is twenty-four numbers, plus
  a currency — these are dollars, displayed as pounds.

Storage is a JSONB column, `rate_card_json`, which does not exist in production yet. Every
read and write probes for it once and degrades to today's behaviour when it is absent, so
this ships dark and turns on when the migration runs.
"""
from __future__ import annotations

import pytest

from src.engines.contract_performance import extract as extract_mod
from src.engines.contract_performance import rate_card as rc


# ───────────────────────────────────────────── the model is asked for what the contract has

def test_the_extractor_asks_for_an_hourly_rate():
    assert "labour_hour_rate" in extract_mod.CONTRACT_FIELDS, (
        "the heuristic parses one and the panel displays one; the model was never asked"
    )


def test_the_extractor_asks_for_the_whole_rate_card():
    assert "rate_card_json" in extract_mod.CONTRACT_FIELDS


def test_a_day_rate_is_still_asked_for():
    # Plenty of UK contracts genuinely are priced per day. This adds, it does not replace.
    assert "labour_day_rate" in extract_mod.CONTRACT_FIELDS


# ─────────────────────────────────────────────────────────────── reading a card

def test_a_per_trade_card_is_normalised_from_what_the_model_returns():
    got = rc.normalise({
        "currency": "USD", "basis": "hour", "source": "Appendix B",
        "lines": [
            {"trade": "HVAC", "straight": 51.40, "overtime": 77.10},
            {"trade": "Plumbers", "straight": "51.71", "overtime": None},
        ],
    })
    assert got["currency"] == "USD"
    assert got["basis"] == "hour"
    assert got["lines"][0] == {"trade": "HVAC", "straight": 51.4, "overtime": 77.1}
    assert got["lines"][1]["straight"] == 51.71, "a numeric string is a number"
    assert got["lines"][1]["overtime"] is None


def test_a_card_with_no_usable_line_is_nothing_rather_than_an_empty_shell():
    assert rc.normalise({"currency": "USD", "lines": []}) is None
    assert rc.normalise({"lines": [{"trade": "", "straight": None}]}) is None
    assert rc.normalise(None) is None


def test_a_line_with_no_rate_is_dropped_but_its_siblings_are_kept():
    got = rc.normalise({"lines": [
        {"trade": "HVAC", "straight": 51.40},
        {"trade": "Nothing stated", "straight": None, "overtime": None},
    ]})
    assert [ln["trade"] for ln in got["lines"]] == ["HVAC"]


def test_currency_is_not_assumed_to_be_sterling():
    # The WKU rates are dollars and the panel renders £. An unstated currency stays unstated
    # rather than inheriting the platform's.
    got = rc.normalise({"lines": [{"trade": "HVAC", "straight": 51.40}]})
    assert got["currency"] is None


# ───────────────────────────────────────────── the rate a check should actually use

def test_the_rate_for_a_named_trade_is_the_one_the_contract_states():
    card = rc.normalise({"currency": "USD", "basis": "hour", "lines": [
        {"trade": "HVAC", "straight": 51.40, "overtime": 77.10},
        {"trade": "Plumbers", "straight": 51.71, "overtime": 77.56},
    ]})
    assert rc.rate_for(card, "HVAC") == 51.40
    assert rc.rate_for(card, "plumbers", overtime=True) == 77.56


def test_an_unnamed_trade_does_not_silently_borrow_another_trades_rate():
    card = rc.normalise({"lines": [{"trade": "HVAC", "straight": 51.40}]})
    assert rc.rate_for(card, "Roofer") is None, (
        "checking a roofer's invoice against the HVAC rate is how £350/day happened"
    )


def test_no_card_means_no_rate_rather_than_a_guess():
    assert rc.rate_for(None, "HVAC") is None


# ───────────────────────────────────────────── it ships dark until the column exists

@pytest.mark.asyncio
async def test_the_column_is_probed_before_it_is_touched():
    """`rate_card_json` is not in production yet. Naming a column that is not there fails
    the whole statement at parse time, so it is probed once — the pattern vendor_contacts
    .is_primary already uses in this codebase."""
    seen: list[str] = []

    class FakeSession:
        async def execute(self, stmt, params=None):
            sql = " ".join(str(stmt).split())
            seen.append(sql)

            class _R:
                def scalar(self_inner):
                    return None          # the column is absent
                def mappings(self_inner):
                    return self_inner
                def first(self_inner):
                    return None
            return _R()

        def begin_nested(self):
            class _N:
                async def __aenter__(self_inner): return self_inner
                async def __aexit__(self_inner, *a): return False
            return _N()

    rc._HAS_RATE_CARD = None
    ok = await rc.column_present(FakeSession())
    assert ok is False
    assert any("information_schema.columns" in s for s in seen), (
        f"it must ASK before it selects; queries were {seen}"
    )


@pytest.mark.asyncio
async def test_the_probe_runs_once_per_process_not_once_per_contract():
    calls = {"n": 0}

    class FakeSession:
        async def execute(self, stmt, params=None):
            calls["n"] += 1

            class _R:
                def scalar(self_inner): return 1
                def mappings(self_inner): return self_inner
                def first(self_inner): return None
            return _R()

        def begin_nested(self):
            class _N:
                async def __aenter__(self_inner): return self_inner
                async def __aexit__(self_inner, *a): return False
            return _N()

    rc._HAS_RATE_CARD = None
    s = FakeSession()
    assert await rc.column_present(s) is True
    assert await rc.column_present(s) is True
    assert calls["n"] == 1, "a catalogue lookup per ingest is a cost for nothing"
    rc._HAS_RATE_CARD = None


# ───────────────────────────── a PDF with no text alongside it must still be read

@pytest.mark.asyncio
async def test_a_pdf_on_its_own_is_extracted_rather_than_skipped(monkeypatch):
    """`if not extracted and source_text:` skipped extraction entirely for a PDF-only call.

    Found on 21 Sep 2026 re-ingesting 04_WKU_Facilities-Management-SLA-2022.pdf. The call
    passed `pdf_base64` and no `source_text` — which is what an uploaded PDF is — so the
    guard was False, Claude was never called, and the function returned ok: True with zero
    extracted fields. A contract row was written with all seventeen values as platform
    defaults, indistinguishable from a document that genuinely states nothing.

    The model reads the PDF perfectly when asked: the same file returns 21 fields, rate card
    included. Nothing was wrong with the extraction — it was never invoked.
    """
    called: dict[str, object] = {}

    async def fake_claude(source_text, pdf_base64=None):
        called["source_text"] = source_text
        called["had_pdf"] = bool(pdf_base64)
        return {"extracted": {"labour_hour_rate": 51.4}, "field_confidence": {}}

    monkeypatch.setattr(extract_mod, "_claude_contract_extract", fake_claude)
    monkeypatch.setattr(extract_mod.settings, "anthropic_api_key", "test-key")

    out = await extract_mod.extract_contract_parameters(
        _NullSession(), source_text=None, pdf_base64="JVBERi0xLjQK", auto_ingest=False,
    )
    assert called.get("had_pdf") is True, (
        "a PDF with no accompanying text was never sent to the model at all"
    )
    assert (out.get("extracted") or {}).get("labour_hour_rate") == 51.4


@pytest.mark.asyncio
async def test_with_neither_text_nor_pdf_nothing_is_called(monkeypatch):
    called = {"n": 0}

    async def fake_claude(source_text, pdf_base64=None):
        called["n"] += 1
        return {"extracted": {}, "field_confidence": {}}

    monkeypatch.setattr(extract_mod, "_claude_contract_extract", fake_claude)
    monkeypatch.setattr(extract_mod.settings, "anthropic_api_key", "test-key")
    await extract_mod.extract_contract_parameters(
        _NullSession(), source_text=None, pdf_base64=None, auto_ingest=False,
    )
    assert called["n"] == 0, "there is nothing to read; calling the model would bill for nothing"


class _NullSession:
    """Enough session for the no-ingest path, which touches the database only for a vendor."""

    async def execute(self, stmt, params=None):
        class _R:
            def mappings(self_inner): return self_inner
            def first(self_inner): return None
            def all(self_inner): return []
            def scalar(self_inner): return None
        return _R()

    def begin_nested(self):
        class _N:
            async def __aenter__(self_inner): return self_inner
            async def __aexit__(self_inner, *a): return False
        return _N()

    def add(self, obj): return None
    async def flush(self): return None
    async def commit(self): return None


# ───────────────────────────── the card has to reach the panel to be of any use

@pytest.mark.asyncio
async def test_the_list_returns_the_rate_card_so_the_panel_can_show_it():
    """params_to_dict cannot read it — rate_card_json is deliberately not on the ORM model,
    because a mapped column that is missing fails every SELECT on databases where the
    migration has not run. The list fetches cards for the page separately, the same way it
    already resolves vendor names, and skips the fetch entirely when the column is absent."""
    import src.engines.contract_performance.parameters as P

    row = _ParamRow()
    card = {"currency": "USD", "basis": "hour",
            "lines": [{"trade": "HVAC", "straight": 51.4, "overtime": 77.1}]}

    class FakeSession:
        async def execute(self, stmt, params=None):
            sql = " ".join(str(stmt).split())

            class _R:
                def scalars(self_inner): return self_inner
                def mappings(self_inner): return self_inner
                def all(self_inner):
                    if "rate_card_json" in sql and "information_schema" not in sql:
                        return [{"id": str(row.id), "rate_card_json": card}]
                    if "FROM plenum_cafm.vendors" in sql or "ingestion_documents" in sql:
                        return []
                    return [row]
                def first(self_inner): return None
                def scalar(self_inner): return 1 if "information_schema" in sql else None
                def __iter__(self_inner): return iter([])
            return _R()

        def begin_nested(self):
            class _N:
                async def __aenter__(self_inner): return self_inner
                async def __aexit__(self_inner, *a): return False
            return _N()

    rc._HAS_RATE_CARD = None
    out = await P.list_contract_parameters(FakeSession(), organization_id=None)
    rc._HAS_RATE_CARD = None
    assert out, "no contracts came back"
    got = out[0].get("rate_card")
    assert got is not None, "the panel has no way to reach the card"
    assert got["currency"] == "USD"
    assert got["lines"][0]["trade"] == "HVAC"


@pytest.mark.asyncio
async def test_no_card_fetch_is_attempted_when_the_column_is_absent():
    import src.engines.contract_performance.parameters as P

    row = _ParamRow()
    seen: list[str] = []

    class FakeSession:
        async def execute(self, stmt, params=None):
            sql = " ".join(str(stmt).split())
            seen.append(sql)

            class _R:
                def scalars(self_inner): return self_inner
                def mappings(self_inner): return self_inner
                def all(self_inner): return [] if "FROM plenum_cafm" in sql and "contract_sla" not in sql else [row]
                def first(self_inner): return None
                def scalar(self_inner): return None      # column absent
                def __iter__(self_inner): return iter([])
            return _R()

        def begin_nested(self):
            class _N:
                async def __aenter__(self_inner): return self_inner
                async def __aexit__(self_inner, *a): return False
            return _N()

    rc._HAS_RATE_CARD = None
    out = await P.list_contract_parameters(FakeSession(), organization_id=None)
    rc._HAS_RATE_CARD = None
    assert out[0].get("rate_card") is None
    selects = [s for s in seen if "rate_card_json" in s and "information_schema" not in s]
    assert not selects, f"it selected a column that is not there: {selects}"


class _ParamRow:
    def __init__(self):
        from uuid import uuid4
        self.id = uuid4()
        self.organization_id = None
        self.vendor_id = None
        self.contract_id = None
        self.document_id = None
        self.contract_ref = "WKU-FM-SLA-2022"
        self.signed_date = None
        self.status = "draft"
        self.confirmed_by = None
        self.confirmed_at = None
        self.created_at = None
        self.updated_at = None
        self.defaults_used = []
        self.overrides_log = []
        self.field_sources = {}
        for f in ("sla_response_p1_hours", "sla_response_p2_hours", "sla_response_p3_hours",
                  "sla_response_p4_hours", "sla_completion_p1_hours", "sla_completion_p2_hours",
                  "sla_completion_p3_hours", "sla_completion_p4_hours", "labour_day_rate",
                  "labour_hour_rate", "overtime_rate", "call_out_rate", "payment_terms"):
            setattr(self, f, None)
        for f in ("parts_pricing_json", "kpi_clauses_json", "ppm_obligations_json", "task_criticality_json"):
            setattr(self, f, {})


# ───────────────────────────── the chat and the panel must count the same ingest

def test_a_rate_card_read_from_the_document_is_sourced_at_merge_time():
    """The chat said "read 12 of 17" while the stored row had 13 contract-sourced fields.

    field_sources is built by merge_extraction_with_defaults and returned to the caller in
    the extract response. rate_card_json was only marked "contract" later, during ingest,
    when the card was written — after that response had been shaped. So the two surfaces
    counted the same ingest differently, which is the exact defect the count fix closed for
    vendor_name and signed_date.
    """
    from src.engines.contract_performance.parameters import merge_extraction_with_defaults

    _, _, sources = merge_extraction_with_defaults({
        "sla_response_p1_hours": 1,
        "rate_card_json": {"currency": "USD", "lines": [{"trade": "HVAC", "straight": 51.4}]},
    })
    assert sources.get("rate_card_json") == "contract"


def test_a_contract_with_no_rate_card_does_not_claim_one():
    from src.engines.contract_performance.parameters import merge_extraction_with_defaults

    _, _, sources = merge_extraction_with_defaults({"sla_response_p1_hours": 1})
    assert sources.get("rate_card_json") != "contract", (
        "an absent card must not count towards source coverage"
    )


def test_an_empty_rate_card_is_not_a_contract_term():
    from src.engines.contract_performance.parameters import merge_extraction_with_defaults

    _, _, sources = merge_extraction_with_defaults({"rate_card_json": {"lines": []}})
    assert sources.get("rate_card_json") != "contract"
