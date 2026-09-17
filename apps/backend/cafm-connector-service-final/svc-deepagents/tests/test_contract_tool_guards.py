"""The vendor tools refuse to make a false claim easy.

Three answers shipped to a user on 16 Sep 2026, all wrong, all from the same cause: the tool
accepted something the model had guessed and returned a result that looked like an answer.

  * ``list_vendor_scorecards(vendor_id="Gough and Kelly Ltd.")`` — a company name where a UUID
    belongs. The service replied 422 and the agent answered anyway, from nothing.
  * ``list_invoices(status="disputed")`` — a status this system never writes. 200 with an empty
    set, reported to the user as "there are no disputed invoices".
  * SLA hours quoted as "the contract terms" when 1h/2h are ``SYSTEM_DEFAULTS`` — the row said
    so in ``defaults_used`` and nothing made the agent look.

The skill documents tell the model not to do these. It did them anyway, on a 39k-character
prompt with a small model. So the tools now carry the guard: a name is matched instead of
rejected, an unknown filter returns the set with a note instead of silence, and a defaulted
field carries its provenance on the row it is read from.
"""
from __future__ import annotations

import pytest
from datetime import date

from src.agents import contract_performance_agent as cp


# ── a name where an id belongs ───────────────────────────────────────────────────────

SCORECARDS = {
    "ok": True,
    "scorecards": [
        {"vendor_id": "11111111-1111-5111-8111-111111111111",
         "vendor_name": "Gough and Kelly Ltd", "overall_score": 72, "score_month": "2026-08-01"},
        {"vendor_id": "22222222-2222-5222-8222-222222222222",
         "vendor_name": "Apex Mechanical Services Ltd", "overall_score": 60, "score_month": "2026-08-01"},
    ],
}


@pytest.mark.parametrize(
    "typed",
    ["Gough and Kelly Ltd.", "gough and kelly", "GOUGH AND KELLY LTD", "Gough & Kelly"],
    ids=["trailing dot", "lowercase no suffix", "shouting", "ampersand"],
)
async def test_a_company_name_finds_its_scorecard_however_it_was_typed(monkeypatch, typed):
    monkeypatch.setattr(cp, "_request", _fake(SCORECARDS))
    out = await cp.list_vendor_scorecards.ainvoke({"vendor_name": typed})
    assert [r["vendor_name"] for r in out["scorecards"]] == ["Gough and Kelly Ltd"]


async def test_a_name_in_the_id_field_is_matched_rather_than_sent_as_a_uuid(monkeypatch):
    # This is the 422 verbatim. The tool must not forward it to the service.
    sent: dict = {}
    monkeypatch.setattr(cp, "_request", _fake(SCORECARDS, capture=sent))
    out = await cp.list_vendor_scorecards.ainvoke({"vendor_id": "Gough and Kelly Ltd."})
    assert "vendor_id" not in (sent.get("params") or {}), "a name must never reach the service as an id"
    assert [r["vendor_name"] for r in out["scorecards"]] == ["Gough and Kelly Ltd"]


async def test_a_name_that_matches_nothing_returns_the_set_and_refuses_to_attribute_it(monkeypatch):
    """A miss must not come back empty.

    It did, and the answer became "there are no invoices recorded for a vendor named Hoistra
    Energy" — a company the user never mentioned, invented by the agent from the product name
    and the page it was on. Reporting absence from a filter nobody asked for is the worst
    outcome available: the register HAS vendors and the reader was told it did not.

    So the set comes back and the note carries the protection instead: these rows are not that
    company's, and must not be attributed to it.
    """
    monkeypatch.setattr(cp, "_request", _fake(SCORECARDS))
    out = await cp.list_vendor_scorecards.ainvoke({"vendor_name": "Hoistra Energy"})
    assert len(out["scorecards"]) == 2, "an unmatched name must not empty the register"
    assert out["vendor_match"] == "none"
    assert "Hoistra Energy" in out["answer_hint"]
    assert "Gough and Kelly Ltd" in out["answer_hint"]
    # The anti-substitution rule has to survive the change, or we have traded one wrong answer
    # for another.
    assert "not" in out["answer_hint"].lower()


async def test_an_ambiguous_name_refuses_to_pick(monkeypatch):
    two = {"ok": True, "scorecards": [
        {"vendor_id": "a", "vendor_name": "Apex Mechanical Services Ltd", "overall_score": 60},
        {"vendor_id": "b", "vendor_name": "Apex Lifts Ltd", "overall_score": 88},
    ]}
    monkeypatch.setattr(cp, "_request", _fake(two))
    out = await cp.list_vendor_scorecards.ainvoke({"vendor_name": "Apex"})
    assert out["vendor_match"] == "ambiguous"
    assert "Apex Lifts Ltd" in out["answer_hint"] and "Apex Mechanical Services Ltd" in out["answer_hint"]


# ── an invented filter value ─────────────────────────────────────────────────────────

INVOICES_EMPTY = {"ok": True, "invoices": []}
INVOICES_ALL = {"ok": True, "invoices": [
    {"invoice_ref": "INV-2847", "status": "flagged", "flagged_count": 3, "matched_count": 9},
    {"invoice_ref": "INV-2901", "status": "complete", "flagged_count": 0, "matched_count": 12},
]}


async def test_an_unknown_status_returns_the_set_and_names_the_real_ones(monkeypatch):
    # "disputed" is not a status this system writes. Empty-and-silent is how the agent came to
    # tell a user there were no disputed invoices.
    monkeypatch.setattr(cp, "_request", _fake_sequence([INVOICES_EMPTY, INVOICES_ALL]))
    out = await cp.list_invoices.ainvoke({"status": "disputed"})
    assert len(out["invoices"]) == 2, "the set must come back rather than nothing"
    note = out["STATUS_FILTER_NOTE"]
    assert "disputed" in note
    assert "flagged" in note and "complete" in note, "the note must name the statuses on record"
    assert "do not" in note.lower() or "not" in note.lower()


async def test_a_valid_status_that_hides_every_invoice_still_says_so(monkeypatch):
    """The guard used to fire only on statuses this system never writes. Live turn, 16 Sep 2026,
    the same question that had just been answered correctly:

        "There are currently no pending invoices, so there are no disputed amounts to report."

    `pending` IS a real status, so the guard stood down — and the one invoice on record is
    `completed`, so the filter matched nothing and the register looked empty. Nobody asked
    about pending invoices: the agent picked that status itself, translating "disputed", which
    is not a status at all.

    Valid-vs-invalid was never the distinction that mattered. What matters is that the register
    HAS invoices and the caller's own filter hid them — the caller cannot tell that from an
    empty list, whichever word it chose.
    """
    monkeypatch.setattr(cp, "_request", _fake_sequence([INVOICES_EMPTY, INVOICES_ALL]))
    out = await cp.list_invoices.ainvoke({"status": "pending"})
    assert len(out["invoices"]) == 2, "the set must come back rather than nothing"
    note = out["STATUS_FILTER_NOTE"]
    assert "pending" in note
    assert "flagged" in note and "complete" in note, "the note must name the statuses on record"


async def test_an_empty_register_needs_no_note(monkeypatch):
    """Nothing is being hidden when there is nothing there. A note would invent a discrepancy."""
    monkeypatch.setattr(cp, "_request", _fake_sequence([INVOICES_EMPTY, INVOICES_EMPTY]))
    out = await cp.list_invoices.ainvoke({"status": "flagged"})
    assert "STATUS_FILTER_NOTE" not in out


# ── a default quoted as a contract term ──────────────────────────────────────────────

async def test_a_defaulted_field_carries_its_provenance_on_the_row(monkeypatch):
    contracts = {"ok": True, "parameters": [{
        "vendor_id": "v1", "vendor_name": "Gough and Kelly Ltd", "contract_ref": "C-1",
        "status": "draft",
        "sla_response_p1_hours": 1, "sla_response_p2_hours": 4, "labour_hour_rate": 62.0,
        "defaults_used": ["sla_response_p1_hours", "sla_response_p2_hours"],
    }]}
    monkeypatch.setattr(cp, "_request", _fake(contracts))
    out = await cp.list_contract_parameters.ainvoke({})
    rule = out["parameters"][0]["PRESENTATION_RULE"]
    assert "sla_response_p1_hours" in rule and "sla_response_p2_hours" in rule
    assert "labour_hour_rate" not in rule, "a contract-sourced field must not be labelled a default"
    assert "not contract-sourced" in rule.lower()


async def test_a_fully_sourced_contract_gets_no_default_warning(monkeypatch):
    contracts = {"ok": True, "parameters": [{
        "vendor_id": "v1", "vendor_name": "Gough and Kelly Ltd", "status": "confirmed",
        "sla_response_p1_hours": 2, "defaults_used": [],
    }]}
    monkeypatch.setattr(cp, "_request", _fake(contracts))
    out = await cp.list_contract_parameters.ainvoke({})
    assert "PRESENTATION_RULE" not in out["parameters"][0]


# ── helpers ──────────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, payload): self._p = payload
    def json(self): return self._p


def _fake(payload, capture: dict | None = None):
    async def _req(method, base, path, **kwargs):
        if capture is not None:
            capture.update(kwargs)
        return _Resp(payload)
    return _req


def _fake_sequence(payloads):
    calls = {"n": 0}

    async def _req(method, base, path, **kwargs):
        i = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        return _Resp(payloads[i])
    return _req


# ── a word that is not a company name ────────────────────────────────────────────────

@pytest.mark.parametrize("word", ["vendor", "the vendor", "my vendor", "supplier",
                                  "contractor", "vendors", "Hoistra"])
async def test_a_generic_word_is_not_treated_as_a_company(monkeypatch, word):
    """"What is the total for my vendor" names no company.

    The agent puts the sentence's own word in vendor_name, nothing matches it, and an empty
    set comes back as "there are no invoices recorded for that vendor" — about a vendor the
    user never named. A word that names no company is not a filter: answer from the set.

    "Hoistra" is here because it happened: the product's own name was passed as the vendor.
    """
    monkeypatch.setattr(cp, "_request", _fake(SCORECARDS))
    out = await cp.list_vendor_scorecards.ainvoke({"vendor_name": word})
    assert len(out["scorecards"]) == 2, f"{word!r} narrowed the set to nothing"
    assert out.get("vendor_match") != "none"


# ── whose contract is this? ──────────────────────────────────────────────────────────

CONTRACTS_TWO = {"ok": True, "parameters": [
    {"vendor_name": "Gough and Kelly Ltd", "contract_ref": "C-1", "status": "draft",
     "sla_response_p1_hours": 1, "defaults_used": ["sla_response_p1_hours"]},
    {"vendor_name": "Apex Mechanical Services Ltd", "contract_ref": "C-2", "status": "confirmed",
     "sla_response_p1_hours": 2, "defaults_used": []},
]}


async def test_a_named_vendors_contract_is_the_only_one_returned(monkeypatch):
    monkeypatch.setattr(cp, "_request", _fake(CONTRACTS_TWO))
    out = await cp.list_contract_parameters.ainvoke({"vendor_name": "apex"})
    assert [p["vendor_name"] for p in out["parameters"]] == ["Apex Mechanical Services Ltd"]


async def test_a_vendor_with_no_contract_is_told_so_not_handed_another(monkeypatch):
    monkeypatch.setattr(cp, "_request", _fake(CONTRACTS_TWO))
    out = await cp.list_contract_parameters.ainvoke({"vendor_name": "Northgate"})
    assert len(out["parameters"]) == 2, "the register must still come back"
    assert out["vendor_match"] == "none"
    assert "Gough and Kelly Ltd" in out["answer_hint"]


async def test_no_vendor_named_across_several_contracts_must_not_be_answered_as_one(monkeypatch):
    """"What is my SLA response time for my vendor" names nobody.

    The set came back, the agent picked a row, and the answer opened "Your vendor, Gough and
    Kelly Ltd." — a vendor the question never mentioned and nothing had verified. With more
    than one contract on record the honest answer says whose terms these are, or asks.
    """
    monkeypatch.setattr(cp, "_request", _fake(CONTRACTS_TWO))
    out = await cp.list_contract_parameters.ainvoke({})
    note = out["MULTI_VENDOR_NOTE"]
    assert "Gough and Kelly Ltd" in note and "Apex Mechanical Services Ltd" in note
    assert "which" in note.lower() or "not" in note.lower()


async def test_a_single_contract_needs_no_such_warning(monkeypatch):
    one = {"ok": True, "parameters": [CONTRACTS_TWO["parameters"][0]]}
    monkeypatch.setattr(cp, "_request", _fake(one))
    out = await cp.list_contract_parameters.ainvoke({})
    assert "MULTI_VENDOR_NOTE" not in out


# ── the invoice total is not the disputed amount ─────────────────────────────────────

INVOICE_WITH_DELTAS = {"ok": True, "invoices": [{
    "invoice_ref": "UKRI-2938", "vendor_name": "Gough and Kelly Ltd.",
    "amount": 6353.70, "line_count": 23, "matched_count": 0, "flagged_count": 23,
    "status": "completed", "flagged_delta": 3031.59, "agreed_delta": 1644.46,
}]}


async def test_an_invoice_row_says_which_figure_is_the_dispute(monkeypatch):
    """Ground truth, 16 Sep 2026: invoice GBP 6,353.70, flagged delta GBP 3,031.59, agreed
    GBP 1,644.46. The answer said "the full amount remains a claim" — 3.9x the confirmed
    figure, and GBP 3,322.11 of that invoice was never in dispute at all.

    Every line being flagged does NOT make every pound disputed: delta_gbp is the gap between
    billed and contracted. The row now has to say so where the number is read.
    """
    monkeypatch.setattr(cp, "_request", _fake(INVOICE_WITH_DELTAS))
    out = await cp.list_invoices.ainvoke({})
    row = out["invoices"][0]
    rule = row["AMOUNT_RULE"]
    assert "6353.7" in rule or "6,353.70" in rule      # named as the invoice total
    assert "3031.59" in rule or "3,031.59" in rule     # the claim
    assert "1644.46" in rule or "1,644.46" in rule     # what is actually agreed
    assert "not" in rule.lower()


async def test_an_invoice_with_nothing_flagged_gets_no_dispute_warning(monkeypatch):
    clean = {"ok": True, "invoices": [{
        "invoice_ref": "INV-1", "amount": 1200.0, "line_count": 4,
        "matched_count": 4, "flagged_count": 0, "flagged_delta": 0, "agreed_delta": 0,
    }]}
    monkeypatch.setattr(cp, "_request", _fake(clean))
    out = await cp.list_invoices.ainvoke({})
    assert "AMOUNT_RULE" not in out["invoices"][0]


# ── a scorecard old enough to be history, quoted as today ────────────────────────────

def _month(delta_months: int) -> str:
    """A score_month `delta_months` before the current one, as the API returns it."""
    today = date.today()
    m = today.month - 1 - delta_months
    return f"{today.year + m // 12:04d}-{m % 12 + 1:02d}-01"


async def test_a_vendors_newest_scorecard_says_so_when_it_is_years_old(monkeypatch):
    """Ground truth, 16 Sep 2026: Gough and Kelly's most recent scorecard is 2024-01-01 —
    over two years back. It was quoted as how the vendor is performing, with nothing on the
    row or in the answer to say the number predates everything the reader has in mind.

    A score is an assertion about a month. Repeating a 2024 figure as current performance is
    wrong in the way that matters most: the reader acts on it.
    """
    stale = {"ok": True, "scorecards": [
        {"vendor_name": "Gough and Kelly Ltd.", "score_month": "2024-01-01", "overall_score": 72.4},
        {"vendor_name": "Gough and Kelly Ltd.", "score_month": "2023-12-01", "overall_score": 70.0},
    ]}
    monkeypatch.setattr(cp, "_request", _fake(stale))
    out = await cp.list_vendor_scorecards.ainvoke({})
    newest = out["scorecards"][0]
    rule = newest["FRESHNESS_RULE"]
    assert "2024-01" in rule                       # names the month the number belongs to
    assert "current" in rule.lower() or "latest" in rule.lower()


async def test_a_scorecard_from_this_month_carries_no_such_warning(monkeypatch):
    fresh = {"ok": True, "scorecards": [
        {"vendor_name": "Apex Mechanical Services Ltd", "score_month": _month(0),
         "overall_score": 60},
    ]}
    monkeypatch.setattr(cp, "_request", _fake(fresh))
    out = await cp.list_vendor_scorecards.ainvoke({})
    assert "FRESHNESS_RULE" not in out["scorecards"][0]


async def test_a_vendors_older_months_are_history_not_staleness(monkeypatch):
    """A twelve-month history is not twelve stale scorecards. Only the newest row per vendor
    answers "how are they doing", so only that row can be out of date."""
    history = {"ok": True, "scorecards": [
        {"vendor_name": "Apex Mechanical Services Ltd", "score_month": _month(0), "overall_score": 60},
        {"vendor_name": "Apex Mechanical Services Ltd", "score_month": _month(9), "overall_score": 55},
    ]}
    monkeypatch.setattr(cp, "_request", _fake(history))
    out = await cp.list_vendor_scorecards.ainvoke({})
    assert not any("FRESHNESS_RULE" in r for r in out["scorecards"])


async def test_each_vendor_is_judged_on_its_own_newest_month(monkeypatch):
    """Four vendors scored; one stopped being scored in 2024. The set must not be called stale
    because of it, nor clean because the others are current."""
    mixed = {"ok": True, "scorecards": [
        {"vendor_name": "Gough and Kelly Ltd.", "score_month": "2024-01-01", "overall_score": 72.4},
        {"vendor_name": "Apex Mechanical Services Ltd", "score_month": _month(0), "overall_score": 60},
    ]}
    monkeypatch.setattr(cp, "_request", _fake(mixed))
    out = await cp.list_vendor_scorecards.ainvoke({})
    marked = {r["vendor_name"] for r in out["scorecards"] if "FRESHNESS_RULE" in r}
    assert marked == {"Gough and Kelly Ltd."}


async def test_an_invoice_with_no_delta_fields_does_not_claim_the_dispute_is_zero(monkeypatch):
    """The deltas come from a JOIN added to svc-operations-intelligence on 16 Sep 2026. Until
    that service is restarted the rows arrive with flagged_count and no deltas at all, and
    `or 0` would have the rule assert "the claimed overcharge is 0 and the Adversary has
    agreed 0" — about an invoice with 23 flagged lines. Absent is not zero.

    Version skew between a caller and its backend has already broken this stack once. The rule
    still has to say the invoice total is not the disputed amount; what it must not do is put
    a figure where it has none.
    """
    no_deltas = {"ok": True, "invoices": [{
        "invoice_ref": "UKRI-2938", "vendor_name": "Gough and Kelly Ltd.",
        "amount": 6353.70, "line_count": 23, "matched_count": 0, "flagged_count": 23,
        "status": "completed",
    }]}
    monkeypatch.setattr(cp, "_request", _fake(no_deltas))
    out = await cp.list_invoices.ainvoke({})
    rule = out["invoices"][0]["AMOUNT_RULE"]
    assert "not" in rule.lower()
    assert "0" not in rule.replace("6353.7", "").replace("6,353.70", "")


async def test_a_null_delta_is_not_read_as_nothing_in_dispute(monkeypatch):
    """The deltas come from a LEFT JOIN onto invoice_lines. An invoice whose lines were never
    loaded joins to nothing, and the column arrives NULL — which is "we do not know", not
    "GBP 0.00 is disputed". Reporting nil on an invoice with 23 flagged lines is the same
    error as reporting the whole total, pointed the other way.
    """
    nulls = {"ok": True, "invoices": [{
        "invoice_ref": "UKRI-2938", "amount": 6353.70, "line_count": 23,
        "matched_count": 0, "flagged_count": 23,
        "flagged_delta": None, "agreed_delta": None,
    }]}
    monkeypatch.setattr(cp, "_request", _fake(nulls))
    out = await cp.list_invoices.ainvoke({})
    rule = out["invoices"][0]["AMOUNT_RULE"]
    assert "not available" in rule.lower() or "not in this payload" in rule.lower()
    assert "0" not in rule.replace("6353.7", "").replace("6,353.70", "")


# ── outside your tenancy is not "does not exist" ─────────────────────────────────────

async def test_an_unmatched_name_is_not_reported_as_a_vendor_that_does_not_exist(monkeypatch):
    """Ground truth, 16 Sep 2026: 29 scorecards span three organizations. Org …0001 sees only
    Gough and Kelly's 7; Apex, BrightSpark and SafeLift belong to …0005 and …00b1. The scoping
    is right — what was said about it was not:

        "There is no vendor named SafeLift in the system — no scorecard, no contract,
         no block record"
        "There is no vendor named 'Apex' anywhere in the records"

    Both are false. Those vendors exist and are scored; they are outside the caller's
    organization. Every tool here returns what THIS caller may see, so absence from the rows
    can never establish absence from the platform — and a property manager told a live supplier
    was never onboarded will act on it.
    """
    rows = [{"vendor_name": "Gough and Kelly Ltd.", "score_month": "2024-01-01"}]
    monkeypatch.setattr(cp, "_request", _fake({"ok": True, "scorecards": rows}))
    out = await cp.list_vendor_scorecards.ainvoke({"vendor_name": "SafeLift Engineering Ltd"})
    hint = out["answer_hint"].lower()
    assert "does not exist" in hint or "not exist" in hint    # names the error it is preventing
    assert "organisation" in hint or "organization" in hint   # says what the boundary is


async def test_a_completed_invoice_with_flagged_lines_is_still_in_dispute(monkeypatch):
    """Ground truth, 16 Sep 2026: the one invoice on record is status `completed` with 23 of
    23 lines flagged and GBP 3,031.59 claimed. Asked "is GBP 1,387.13 the amount Gough and
    Kelly disputes?", the answer was:

        "No ... There are currently no disputed invoices for Gough and Kelly Ltd."

    `completed` is the state of the VERIFICATION — the check ran to the end. It says nothing
    about whether the money is agreed, and the row that carries it is the row carrying 23
    flagged lines. Reading a lifecycle status as a settlement is how a live GBP 3,031.59 claim
    was reported as no claim at all.
    """
    completed = {"ok": True, "invoices": [{
        "invoice_ref": "UKRI-2938", "vendor_name": "Gough and Kelly Ltd.", "status": "completed",
        "amount": 6353.70, "line_count": 23, "matched_count": 0, "flagged_count": 23,
        "flagged_delta": 3031.59, "agreed_delta": 1644.46,
    }]}
    monkeypatch.setattr(cp, "_request", _fake(completed))
    out = await cp.list_invoices.ainvoke({})
    rule = out["invoices"][0]["AMOUNT_RULE"].lower()
    assert "completed" in rule            # names the status that misled
    assert "in dispute" in rule or "under query" in rule
    assert "verification" in rule         # says what the status actually describes


def test_the_invoice_tool_tells_the_model_dispute_is_not_a_status():
    """Twice now the agent answered a dispute question by filtering on a status: first
    `disputed` (which this system never writes), then `pending` (which it does, but no invoice
    holds). Both returned an empty list and both were reported as "no disputed invoices" while
    a wholly flagged GBP 6,353.70 invoice sat in the register.

    The row guard catches the consequence. This catches the cause: the docstring is what the
    model reads when it decides which arguments to pass, and it never said that dispute is a
    property of the LINES — flagged_count > 0 — and not a value of `status`.
    """
    doc = cp.list_invoices.description
    assert "flagged_count" in doc
    lowered = doc.lower()
    assert "dispute" in lowered
    assert "not a status" in lowered or "is not a status" in lowered


def test_a_scorecard_row_is_a_month_not_a_work_order():
    """Asked "how many work orders were scored for Gough and Kelly?", the agent called
    list_vendor_scorecards, got 7 rows, and answered "7 work orders scored". The real figure is
    1,846 — and `vendor_wo_scores` has no read route, so the honest answer is that it cannot be
    read. Seven is the number of MONTHS that vendor has been scored.

    One row per vendor per month is the single most important fact about this tool's output,
    and the docstring never said it.
    """
    doc = cp.list_vendor_scorecards.description
    lowered = doc.lower()
    assert "one row per vendor per month" in lowered or "vendor x month" in lowered
    assert "work order" in lowered, "must say a row is not a work order"
    assert "no read route" in lowered or "cannot be read" in lowered


async def test_the_unagreed_remainder_is_not_described_as_contested(monkeypatch):
    """Ground truth, 16 Sep 2026: of 23 flagged lines, ONE has been agreed by the Adversary.
    The other 22 have `adversary_reviewed` unset — nobody has looked at them. The answer
    labelled the GBP 1,387.13 remainder "still contested", which says the Adversary considered
    it and disagreed. Nothing of the sort happened.

    Unresolved and contested are different states and they imply different next moves: one is
    chased internally, the other is argued with the vendor.
    """
    monkeypatch.setattr(cp, "_request", _fake(INVOICE_WITH_DELTAS))
    rule = (await cp.list_invoices.ainvoke({}))["invoices"][0]["AMOUNT_RULE"].lower()
    assert "unreviewed" in rule or "not been reviewed" in rule
    assert "contested" in rule or "disagreed" in rule


async def test_a_claim_names_the_benchmark_it_was_measured_against(monkeypatch):
    """The GBP 3,031.59 is the gap between billed and `contract_labour_rate` — GBP 43.75/hr,
    which is the GBP 350.00 DEFAULT day rate divided by 8, on a contract whose status is draft
    and whose every field is a platform default with `confirmed_by` empty.

    Asked directly, the system gets this right and says the figure cannot be put to the vendor
    as a breach. Asked "total amount of disputed invoices with details", it fetched only the
    invoices, never saw the contract, and recommended chasing the gap with no mention that the
    benchmark was never agreed. A recoverable amount is only as real as the rate behind it, and
    the invoice row is where that has to be said, because it is all the agent read.
    """
    monkeypatch.setattr(cp, "_request", _fake(INVOICE_WITH_DELTAS))
    rule = (await cp.list_invoices.ainvoke({}))["invoices"][0]["AMOUNT_RULE"]
    assert "list_contract_parameters" in rule
    assert "defaults_used" in rule


# ── one phrase, two measures ─────────────────────────────────────────────────────────

async def test_contract_sla_hours_say_they_are_targets_not_performance(monkeypatch):
    """"What is the SLA completion for my vendor?" was asked twice and answered twice, from
    two different tables, both correctly:

      - contract_sla_parameters.sla_completion_p1..p4_hours - the hours the contract ALLOWS
      - component_breakdown.sla_completion on the monthly scorecard - what the vendor SCORED

    Neither answer mentioned the other reading existed. The sub-agent picked a table and the
    question's own ambiguity vanished into it; on the second run the router's stated reason
    said "which is a scorecard metric" while the tool call went to the contract. A reader
    cannot tell which of the two they were given, and the two support opposite conclusions:
    one says what was promised, the other says whether it was met.
    """
    monkeypatch.setattr(cp, "_request", _fake({"ok": True, "parameters": [
        {"contract_ref": "UKRI-2938", "vendor_name": "Gough and Kelly Ltd.",
         "sla_completion_p1_hours": 4, "status": "draft"},
    ]}))
    out = await cp.list_contract_parameters.ainvoke({})
    note = out["SLA_SENSE_NOTE"]
    assert "target" in note.lower()
    assert "list_vendor_scorecards" in note


async def test_scorecard_sla_component_says_it_is_the_measured_one(monkeypatch):
    monkeypatch.setattr(cp, "_request", _fake({"ok": True, "scorecards": [
        {"vendor_name": "Gough and Kelly Ltd.", "score_month": "2024-01-01",
         "component_breakdown": {"sla_completion": 16.67}},
    ]}))
    out = await cp.list_vendor_scorecards.ainvoke({})
    note = out["SLA_SENSE_NOTE"]
    assert "measured" in note.lower() or "achieved" in note.lower()
    assert "list_contract_parameters" in note
