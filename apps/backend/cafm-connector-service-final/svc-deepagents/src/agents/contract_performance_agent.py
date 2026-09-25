"""
Contract Performance agent tools — wraps svc-operations-intelligence (port 8009).

PRD tool agent: contract-performance-engine → Orchestrator agent id `contract_performance` (Feature B).
Intent keywords (SSOT: phase2_intents.CONTRACT_PERFORMANCE_INTENT_KEYWORDS):
  SLA, contractor, performance, KPI, PPM completion rate, vendor score, first fix,
  recall, invoice, overrun, contract breach.

Phase 2 Feature B (B1–B3):
  - ingest_contract_parameters / update / confirm
  - upsert_asset_criticality / approve
  - score_vendor_work_orders
  - generate_vendor_scorecard / list_scorecards
  - list_contract_parameters (read-only: SLA targets, rates, KPI clauses, PPM obligations)
  - get/update_score_weights (admin)
  - verify_invoice / decide_invoice_line
  - list_contract_approvals / decide_contract_approval
"""
from __future__ import annotations

import difflib
import re
import uuid as _uuid
from datetime import date
from typing import Any

import httpx
import structlog
from langchain_core.tools import tool

from ..config import settings
from ..http_client import request as _request

log = structlog.get_logger(__name__)

_TIMEOUT = 60.0
_SERVICE = "operations_intelligence"


# ─────────────────────────────────────────────────────────────────────────────────────
# Company names, and why these tools match them instead of rejecting them
#
# A person asks about "Gough and Kelly"; the scorecard and contract tools are keyed by
# vendor_id. On 16 Sep 2026 the agent put the name in vendor_id, the service answered
# 422 uuid_parsing, and the agent wrote an answer from nothing. The rows themselves DO carry
# vendor_name (scoring.list_scorecards and parameters.list_contract_parameters both resolve it
# after the query), so the name never needed to reach the service — it is matched here,
# against what came back.
#
# Compliance has had tolerant matching since it shipped; these tools never did. Same rules:
# case, punctuation, spacing and legal-form words do not distinguish two companies, and one
# wrong letter should not lose a match.
# ─────────────────────────────────────────────────────────────────────────────────────

#: Words that say what kind of company it is, not which company.
_LEGAL_FORM_WORDS = frozenset({
    "ltd", "limited", "llc", "inc", "incorporated", "plc", "co", "corp", "corporation",
    "company", "the", "and", "group", "holdings", "services", "service", "uk",
})


def _name_words(name):
    """The distinctive words of a company name, lowercased, legal forms dropped."""
    return [
        w for w in re.split(r"[^a-z0-9]+", str(name or "").lower())
        if w and w not in _LEGAL_FORM_WORDS
    ]


def _name_matches(typed, row_name) -> bool:
    """Whether what the user typed names the company on this row.

    Every distinctive word typed must appear on the row — so "Apex" matches both Apex
    companies (the caller is told it is ambiguous rather than handed one), while "Apex Lifts"
    matches only one. A close near-miss counts, so a single wrong letter does not lose it.
    """
    want, have = _name_words(typed), _name_words(row_name)
    if not want or not have:
        return False
    for w in want:
        if not any(
            w == h or (len(w) > 3 and difflib.SequenceMatcher(None, w, h).ratio() >= 0.85)
            for h in have
        ):
            return False
    return True


#: Words that appear where a company name is expected and name no company. A question says
#: "what is the total for my vendor"; the agent puts the sentence's own word in vendor_name;
#: nothing matches it; and an empty set is reported as "there are no invoices for that vendor"
#: — about a vendor the user never named. `hoistra` and `plenum` are here because it happened:
#: the product's own name was passed as the company.
_NOT_A_COMPANY = frozenset({
    "vendor", "vendors", "supplier", "suppliers", "contractor", "contractors",
    "firm", "firms", "provider", "providers", "hoistra", "plenum",
    "my", "our", "their", "this", "that", "a", "an", "any", "each", "all", "specified",
})


def _names_a_company(typed) -> bool:
    """Whether what was typed could be a company at all.

    A filter that narrows to nothing is indistinguishable, in the answer, from a register that
    holds nothing. So a value with no distinctive word in it is not treated as a filter: the
    set comes back and the answer is written from it.
    """
    return any(w not in _NOT_A_COMPANY for w in _name_words(typed))


def _looks_like_uuid(value) -> bool:
    try:
        _uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _match_by_name(rows, typed, kind):
    """Narrow rows to one company, or say why that could not be done.

    On a miss the `answer_hint` is what keeps the agent from answering about somebody else:
    "that company is not on record" is the answer to a question about a company that is not on
    record; another vendor's figures never are.
    """
    named = [r for r in rows if _name_matches(typed, r.get("vendor_name"))]
    on_record = sorted({str(r.get("vendor_name")) for r in rows if r.get("vendor_name")})
    if not named:
        # The whole set comes back, never an empty one. An unmatched name used to return
        # nothing, and the answer became "there are no invoices recorded for a vendor named
        # Hoistra Energy" — a company the user never mentioned, invented by the agent from the
        # product name and the page it was on. The register HAS vendors; reporting absence from
        # a filter nobody asked for is a worse failure than the substitution this guard was
        # built to stop. So the rows stay and the note carries the protection.
        return {
            "vendor_match": "none",
            "rows": rows,
            "answer_hint": (
                f"Nothing VISIBLE TO THIS USER matches {typed!r} — and if the question did not "
                f"actually name a company, that name was not the user's. Do NOT say that "
                f"{typed!r} does not exist, is not in the system, or is not on record "
                "anywhere: every row here is scoped to the caller's own organisation and "
                "buildings, so a vendor scored under another organisation is invisible here "
                "and still real. Say there is no data for that name IN THEIR ORGANISATION, "
                f"and that the name may belong elsewhere. These {len(rows)} row(s) are what "
                "they can see: answer from them, covering every vendor, and do NOT attribute "
                + f"any of these figures to {typed!r}. Visible: "
                + (", ".join(on_record) or "no vendors at all")
                + "."
            ),
        }
    distinct = sorted({str(r.get("vendor_name")) for r in named if r.get("vendor_name")})
    if len(distinct) > 1:
        return {
            "vendor_match": "ambiguous",
            "rows": named,
            "answer_hint": (
                f"{typed!r} matches more than one vendor: " + ", ".join(distinct)
                + ". Ask which one is meant rather than choosing; a figure attributed to the "
                "wrong company is worse than no answer."
            ),
        }
    return {"vendor_match": "one", "rows": named}


#: The statuses invoice verification actually writes. An unknown value returns 200 and an
#: empty list, which reads as "there are none" — that is how "there are no disputed invoices"
#: reached a user when `disputed` is not a status this system has ever written.
_INVOICE_STATUSES = frozenset({"pending", "flagged", "matched", "complete", "completed"})



# A monthly scorecard that stopped being written does not announce itself: the newest row is
# still the newest row, and a 2024 score reads exactly like this month's. Three months is the
# line — one skipped month is a late run, a whole quarter means the scoring stopped.
_STALE_AFTER_MONTHS = 3


def _months_old(score_month: Any) -> int | None:
    """How many months back this scorecard's month sits. None when it does not parse."""
    parts = str(score_month or "").strip()[:7].split("-")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    year, month = int(parts[0]), int(parts[1])
    today = date.today()
    return (today.year - year) * 12 + (today.month - month)


def _mark_stale_scorecards(rows: list[dict]) -> None:
    """Flag each vendor's NEWEST scorecard when that newest one is months out of date.

    Per vendor, and only the newest row: a twelve-month history is not twelve stale
    scorecards, and one vendor going unscored since 2024 does not make the others stale.
    """
    newest: dict[str, tuple[int, dict]] = {}
    for row in rows:
        age = _months_old(row.get("score_month"))
        if age is None:
            continue
        vendor = str(row.get("vendor_name") or row.get("vendor_id") or "")
        held = newest.get(vendor)
        if held is None or age < held[0]:
            newest[vendor] = (age, row)
    for vendor, (age, row) in newest.items():
        if age < _STALE_AFTER_MONTHS:
            continue
        month = str(row.get("score_month") or "")[:7]
        row["FRESHNESS_RULE"] = (
            f"This is the LATEST scorecard on record for {vendor or 'this vendor'} and it is "
            f"for {month} — {age} months ago. It is NOT current performance. Say the month "
            f"whenever you quote any figure from this row, do not write it in the present "
            f"tense, and say that no scorecard has been produced since."
        )


# "What is the SLA completion for my vendor?" names two different measures, and the platform
# holds both. Asked twice, it was answered twice from two different tables — each answer right
# on its own terms, neither mentioning the other existed, and the two support opposite
# conclusions: one says what the vendor promised, the other whether they met it.
_SLA_SENSE_CONTRACT = (
    "These sla_completion_* and sla_response_* hours are the TARGETS the contract allows — "
    "what the vendor promised, not what they achieved. The MEASURED performance is a scored "
    "component on the monthly scorecard: `list_vendor_scorecards` → "
    "component_breakdown.sla_completion, out of 25 points. 'SLA completion' means either of "
    "these, so say which one you are giving; if the question did not make it clear, give both "
    "or say plainly which reading you took."
)
_SLA_SENSE_SCORECARD = (
    "component_breakdown.sla_completion is the MEASURED score out of 25 — how the vendor "
    "actually performed that month. It is NOT the contracted target hours, which live on the "
    "contract: `list_contract_parameters` → sla_completion_p1..p4_hours. 'SLA completion' "
    "means either of these, so say which one you are giving; if the question did not make it "
    "clear, give both or say plainly which reading you took."
)


def _base() -> str:
    return settings.operations_intelligence_base_url.rstrip("/")


def _err(exc: Exception, op: str) -> dict:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text or ""
        log.error(
            "contract_performance.http_error",
            operation=op,
            status_code=exc.response.status_code,
            body=body[:2000],
        )
        return {"error": body[:2000], "status_code": exc.response.status_code}
    log.error("contract_performance.error", operation=op, error=str(exc), exc_info=True)
    return {"error": str(exc)[:2000]}


@tool
async def ingest_contract_parameters(
    extracted: dict | None = None,
    vendor_id: str | None = None,
    vendor_name: str | None = None,
    contract_ref: str | None = None,
    organization_id: str | None = None,
    document_id: str | None = None,
) -> dict:
    """B1 — Ingest extracted FM contract SLA parameters (draft for PM edit/confirm).

    Fills silent fields with system defaults labelled 'Default — not contract-sourced'.

    Pass the vendor's name in ``vendor_name`` when the platform id is unknown — the service
    matches it against the register and registers a new vendor if there is no match. A name
    passed in ``vendor_id`` is also accepted rather than rejected.
    """
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/contracts/ingest",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "extracted": extracted or {},
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "contract_ref": contract_ref,
                "organization_id": organization_id,
                "document_id": document_id,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "ingest_contract_parameters")


@tool
async def update_contract_parameters(
    parameters_id: str,
    updates: dict,
    actor: str = "pm",
) -> dict:
    """B1 — PM inline override of contract SLA fields (logged)."""
    try:
        resp = await _request(
            "PATCH",
            _base(),
            f"/api/contract-performance/contracts/{parameters_id}",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"updates": updates, "actor": actor},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "update_contract_parameters")


@tool
async def confirm_contract_parameters(
    parameters_id: str,
    confirmed_by: str | None = None,
) -> dict:
    """B1 — Confirm contract SLA parameters. A PERSON'S DECISION, NEVER YOURS.

    Confirming turns extracted readings into the numbers a vendor is scored, breached and
    invoiced against. Call this ONLY when the reader has asked, on this turn, for these
    terms to be confirmed. Never as the tail of an ingest, never to tidy a draft, and never
    because a recipe looks unfinished — a draft is a correct resting state.

    `confirmed_by` is the reader's user id and is REQUIRED; the server refuses a
    confirmation with no named person, and refuses any set where nothing was read from the
    document."""
    try:
        resp = await _request(
            "POST",
            _base(),
            f"/api/contract-performance/contracts/{parameters_id}/confirm",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"confirmed_by": confirmed_by},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "confirm_contract_parameters")


@tool
async def propose_asset_criticality(
    asset_id: str,
    asset_code: str | None = None,
    load_dependence: str | None = None,
    function_type: str | None = None,
    sub_meter_high: bool = False,
    organization_id: str | None = None,
) -> dict:
    """B1 — Propose L1/L2/L3 asset criticality from UDR signals (HITL required)."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/asset-criticality",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "asset_id": asset_id,
                "asset_code": asset_code,
                "load_dependence": load_dependence,
                "function_type": function_type,
                "sub_meter_high": sub_meter_high,
                "organization_id": organization_id,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "propose_asset_criticality")


@tool
async def approve_asset_criticality(
    criticality_id: str,
    criticality: str | None = None,
    approved_by: str | None = None,
) -> dict:
    """B1 — PM approve/edit asset criticality (unapproved assets score as L2)."""
    try:
        resp = await _request(
            "POST",
            _base(),
            f"/api/contract-performance/asset-criticality/{criticality_id}/approve",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"criticality": criticality, "approved_by": approved_by},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "approve_asset_criticality")


@tool
async def score_vendor_work_orders(
    work_orders: list[dict],
    vendor_id: str | None = None,
    organization_id: str | None = None,
    score_month: str | None = None,
) -> dict:
    """B2 — Score ingested completed work orders against contract SLA for a vendor.

    Components: SLA response 25%, completion 25%, first fix 20%, recall 15%,
    accreditation 15%. Blocked vendors capped at 60.
    Prefer `score_work_orders_from_udr` when WOs were migrated via Phase 1 UDR CSV.
    """
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/score/work-orders",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "work_orders": work_orders,
                "vendor_id": vendor_id,
                "organization_id": organization_id,
                "score_month": score_month,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "score_vendor_work_orders")


@tool
async def score_work_orders_from_udr(
    vendor_id: str | None = None,
    organization_id: str | None = None,
    score_month: str | None = None,
    all_buckets: bool = True,
    limit: int = 500,
) -> dict:
    """B2 — Score completed work orders from Phase 1 UDR migration tables
    (`plenum_cafm.work_orders` written by CSV/XLS migration).

    Use after FM company reports are migrated via Single Door / Schema Mapper.
    Set all_buckets=true to score every vendor×month present in UDR.
    """
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/score/from-udr",
            service=_SERVICE,
            timeout=max(_TIMEOUT, 180.0),
            json={
                "vendor_id": vendor_id,
                "organization_id": organization_id,
                "score_month": score_month,
                "all_buckets": all_buckets,
                "limit": limit,
                "generate_scorecard": True,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "score_work_orders_from_udr")


@tool
async def generate_vendor_scorecard(
    vendor_id: str,
    score_month: str | None = None,
    ppm_visits: list[dict] | None = None,
    organization_id: str | None = None,
) -> dict:
    """B2 — Generate monthly vendor scorecard (overall, trend, components, PPM)."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/scorecards/monthly",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "vendor_id": vendor_id,
                "score_month": score_month,
                "ppm_visits": ppm_visits or [],
                "organization_id": organization_id,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "generate_vendor_scorecard")


@tool
async def list_contract_parameters(
    status: str | None = None,
    organization_id: str | None = None,
    vendor_name: str | None = None,
) -> dict:
    """B1 — List extracted contract parameters: SLA response/completion targets by priority
    (P1/P2/P3/P4, in hours), labour day rate, overtime rate, call-out rate, payment terms,
    parts pricing framework, KPI penalty/bonus clauses, PPM schedule obligations, task
    criticality (L1/L2/L3), per contract with its vendor_id and draft/confirmed status.

    **Call this for ANY question about contract terms, SLA targets/hours, rates, payment
    terms, KPI clauses or PPM obligations** — e.g. "which vendor has SLA completion under
    25h for P2" is answered by comparing `sla_completion_p2_hours` across the returned
    `parameters`. Read-only. `status` filters to "draft" or "confirmed" (omit for all).

    Each row carries `vendor_name` — ALWAYS refer to vendors by name in answers
    (fall back to the contract_ref); never present a raw vendor_id UUID to the user.

    Each row also carries `building_name` and `building_reference` — the property the
    contract covers, resolved through the document it was extracted from. Use these to
    answer "what contracts are on <building>". A null building_name means that contract
    could not be placed against a property, NOT that it belongs to whichever building was
    asked about: say it is unplaced rather than attributing it.
    """
    try:
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if organization_id:
            params["organization_id"] = organization_id
        resp = await _request(
            "GET",
            _base(),
            "/api/contract-performance/contracts",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        data = resp.json()
        rows_all = data.get("parameters") or []
        if vendor_name and not _names_a_company(vendor_name):
            log.info("contract_performance.name_ignored", typed=str(vendor_name)[:60], tool="contracts")
            vendor_name = None
        if vendor_name:
            matched = _match_by_name(rows_all, vendor_name, "contract")
            rows = matched.pop("rows")
            data = dict(data, parameters=rows, count=len(rows), **matched)
        elif len({str(r.get("vendor_name")) for r in rows_all if r.get("vendor_name")}) > 1:
            # Nobody was named and several vendors hold contracts. "What is my SLA response
            # time for my vendor" was answered "Your vendor, Gough and Kelly Ltd." — a company
            # the question never mentioned, picked off the first row. Whose terms these are is
            # part of the answer, not a detail.
            whose = sorted({str(r.get("vendor_name")) for r in rows_all if r.get("vendor_name")})
            data["MULTI_VENDOR_NOTE"] = (
                "No vendor was named and these contracts belong to different companies: "
                + ", ".join(whose)
                + ". Do NOT present one of them as 'your vendor' — say which company each "
                "figure belongs to, or ask which one is meant."
            )
        # The line between what a vendor agreed to and what this platform assumed when the
        # document was silent. A user was told "P1: 1 hour, P2: 4 hours … based on the
        # contract terms" when both are SYSTEM_DEFAULTS; defaults_used said so on the row and
        # nothing made the agent look. Spelling out the affected fields on the row itself
        # means the caveat is in front of whoever reads the number.
        for row in (data.get("parameters") or []):
            defaulted = [str(f) for f in (row.get("defaults_used") or []) if str(f).strip()]
            if defaulted:
                row["PRESENTATION_RULE"] = (
                    "These fields are NOT contract-sourced — the document was silent and the "
                    "platform filled them from system defaults: " + ", ".join(sorted(defaulted))
                    + ". Any of them you quote must be marked '(Default — not contract-sourced)'. "
                    "Never introduce one with 'the contract says' or 'the contract terms are'; "
                    "a default cannot be put to a vendor."
                )
        if any(
            k.startswith("sla_completion") or k.startswith("sla_response")
            for r in (data.get("parameters") or []) for k in r
        ):
            data["SLA_SENSE_NOTE"] = _SLA_SENSE_CONTRACT
        return data
    except Exception as exc:
        return _err(exc, "list_contract_parameters")


@tool
async def list_vendor_scorecards(
    vendor_id: str | None = None,
    vendor_name: str | None = None,
    organization_id: str | None = None,
    limit: int = 50,
) -> dict:
    """B2 — List vendor monthly scorecards: overall score, SLA response/completion, first-fix,
    recall, accreditation breakdown, PPM ±7d and month-on-month trend.

    **Call this for ANY question about vendor performance / scorecards / "how are the vendors
    performing" / SLA / KPI** — no vendor_id needed to list all vendors. The UI renders the
    returned `scorecards` as dashboard cards in the center chat, so pull the live numbers rather
    than describing them from memory.

    **ONE ROW PER VENDOR PER MONTH. A row is NOT a work order.** Seven rows means that vendor
    has been scored for seven months, not that seven jobs were done: "how many work orders were
    scored for Gough and Kelly?" was answered "7 work orders" from seven monthly scorecards
    when the real figure was 1,846. The per-work-order scores live in `vendor_wo_scores`, which
    has NO READ ROUTE from here — if a question needs them, say they cannot be read rather than
    counting these rows instead.
    """
    try:
        params: dict[str, Any] = {"limit": limit}
        # A company name in vendor_id is a name, not an id. Sending it on produced
        # `422 uuid_parsing` and the agent answered from nothing; the rows carry vendor_name,
        # so the name is matched here against what comes back instead.
        named = (vendor_name or "").strip()
        if vendor_id and not _looks_like_uuid(vendor_id):
            named = named or str(vendor_id).strip()
            vendor_id = None
        if named and not _names_a_company(named):
            log.info("contract_performance.name_ignored", typed=named[:60], tool="scorecards")
            named = ""
        if vendor_id:
            params["vendor_id"] = vendor_id
        if organization_id:
            params["organization_id"] = organization_id
        resp = await _request(
            "GET",
            _base(),
            "/api/contract-performance/scorecards",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        data = resp.json()
        if named:
            matched = _match_by_name(data.get("scorecards") or [], named, "scorecard")
            rows = matched.pop("rows")
            # A new dict rather than an edit in place: the payload belongs to the caller, and
            # narrowing it to one vendor is this call's answer, not a change to the register.
            data = dict(data, scorecards=rows, count=len(rows), **matched)
        _mark_stale_scorecards(data.get("scorecards") or [])
        if any((r.get("component_breakdown") or {}) for r in (data.get("scorecards") or [])):
            data["SLA_SENSE_NOTE"] = _SLA_SENSE_SCORECARD
        return data
    except Exception as exc:
        return _err(exc, "list_vendor_scorecards")


@tool
async def get_score_weights(organization_id: str | None = None) -> dict:
    """B2 admin — get vendor score component weights (editable)."""
    try:
        params: dict[str, Any] = {}
        if organization_id:
            params["organization_id"] = organization_id
        resp = await _request(
            "GET",
            _base(),
            "/api/contract-performance/admin/weights",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "get_score_weights")


@tool
async def update_score_weights(
    sla_response_pct: float | None = None,
    sla_completion_pct: float | None = None,
    first_fix_pct: float | None = None,
    recall_pct: float | None = None,
    accreditation_pct: float | None = None,
    blocked_score_cap: float | None = None,
    cost_variance_alert_pct: float | None = None,
    cost_variance_job_count: int | None = None,
    invoice_flag_adversary_gbp: float | None = None,
    organization_id: str | None = None,
) -> dict:
    """B2 admin — update all vendor score parameter components."""
    try:
        payload: dict[str, Any] = {"organization_id": organization_id}
        for k, v in {
            "sla_response_pct": sla_response_pct,
            "sla_completion_pct": sla_completion_pct,
            "first_fix_pct": first_fix_pct,
            "recall_pct": recall_pct,
            "accreditation_pct": accreditation_pct,
            "blocked_score_cap": blocked_score_cap,
            "cost_variance_alert_pct": cost_variance_alert_pct,
            "cost_variance_job_count": cost_variance_job_count,
            "invoice_flag_adversary_gbp": invoice_flag_adversary_gbp,
        }.items():
            if v is not None:
                payload[k] = v
        resp = await _request(
            "PUT",
            _base(),
            "/api/contract-performance/admin/weights",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json=payload,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "update_score_weights")


@tool
async def list_invoices(
    building_name: str | None = None,
    building_id: str | None = None,
    vendor_name: str | None = None,
    invoice_ref: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> dict:
    """B3 — Verified invoices with the building and vendor each belongs to. Read-only.

    **Use this for any question about invoices: what a building has been billed, what an
    invoice is worth, how many lines matched or were flagged.** Until this existed the
    feature could verify an invoice and decide its lines but never read one back, so every
    question about a building's invoices was answered "none found" however many it had.

    Filters: `building_name` as the user said it, `vendor_name`, `invoice_ref` (the invoice
    number, partial and case-insensitive), `status`.

    **DISPUTE IS NOT A STATUS. Do NOT pass `status` for a question about disputed, queried,
    contested or overcharged invoices.** An invoice is in dispute when `flagged_count` > 0,
    which is a property of its LINES. `status` records how far the VERIFICATION got —
    pending, flagged, matched, complete, completed — and a `completed` invoice can be wholly
    under query. Filtering a dispute question by status has twice returned an empty list and
    been reported as "there are no disputed invoices" while a fully flagged invoice sat in
    the register. Call this with no `status` and read `flagged_count` on the rows.

    Each row carries `invoice_ref` (the number printed on the document), `building_name`,
    `vendor_name`, `amount`, `line_count`, `matched_count`, `flagged_count` and
    `building_link`. `building_link: "unplaced"` means that invoice could not be tied to any
    property — say so; it does NOT belong to whichever building was asked about.
    """
    try:
        params: dict[str, Any] = {"limit": limit}
        if building_id:
            params["building_id"] = building_id
        elif building_name:
            resolved = await _request(
                "GET",
                _base(),
                "/api/energy/buildings/resolve",
                service=_SERVICE,
                timeout=_TIMEOUT,
                params={"name": building_name},
            )
            body = resolved.json()
            if body.get("outcome") != "resolved" or not body.get("building_id"):
                return {
                    "error": "building_not_resolved",
                    "outcome": body.get("outcome"),
                    "asked_for": building_name,
                    "candidates": body.get("candidates") or [],
                    "guidance": (
                        "No building matched that name. Do NOT report another building's "
                        "invoices; ask which one is meant."
                    ),
                }
            params["building_id"] = body["building_id"]
        if invoice_ref:
            params["invoice_ref"] = invoice_ref
        if status:
            params["status"] = status
        resp = await _request(
            "GET",
            _base(),
            "/api/contract-performance/invoices",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        out = resp.json()
        # ANY status filter that hides every invoice, not just an invented one. A filter that
        # matches nothing returns 200 with an empty list, which reads as "there are none".
        #
        # This first fired for `disputed`, which is not a status at all. It fired again for
        # `pending` — a perfectly real status — on the same question: nobody had asked about
        # pending invoices, the agent chose that word itself as its translation of "disputed",
        # the one invoice on record is `completed`, and the user was told "there are currently
        # no pending invoices, so there are no disputed amounts to report". Valid-vs-invalid
        # was never the distinction that mattered; the register having rows the caller's own
        # filter hid is.
        if status and not (out.get("invoices") or []):
            every = await _request(
                "GET",
                _base(),
                "/api/contract-performance/invoices",
                service=_SERVICE,
                timeout=_TIMEOUT,
                params={k: v for k, v in params.items() if k != "status"},
            )
            body = every.json()
            rows = body.get("invoices") or []
            if rows:
                seen: dict[str, int] = {}
                for r in rows:
                    key = str(r.get("status"))
                    seen[key] = seen.get(key, 0) + 1
                out = body
                known = str(status).strip().lower() in _INVOICE_STATUSES
                out["STATUS_FILTER_NOTE"] = (
                    (f"No invoice has the status {status!r}, though it is a status this system "
                     f"does record. " if known else
                     f"{status!r} is not a status this system records, so nothing could match "
                     f"it. ")
                    + f"Your filter, not the register, is what came back empty. These "
                    f"{len(rows)} invoice(s) ARE what is on record "
                    f"({', '.join(f'{k}: {v}' for k, v in sorted(seen.items()))}). Do NOT "
                    f"answer that there are no {status} invoices — answer from these rows, "
                    "and note that a flagged line is a claim until the Adversary agrees it."
                )
        if vendor_name and not _names_a_company(vendor_name):
            log.info("contract_performance.name_ignored", typed=str(vendor_name)[:60], tool="invoices")
            vendor_name = None
        if vendor_name:
            matched = _match_by_name(out.get("invoices") or [], vendor_name, "invoice")
            rows = matched.pop("rows")
            out = dict(out, invoices=rows, count=len(rows), vendor_name_filter=vendor_name, **matched)
        # Which figure is the dispute. A flagged LINE is not a flagged AMOUNT — delta_gbp is
        # the gap between billed and contracted, not the line total. An invoice of £6,353.70
        # with all 23 lines flagged carried a claimed gap of £3,031.59, of which the Adversary
        # had agreed £1,644.46; the answer said the full £6,353.70 "remains a claim", which is
        # 3.9x the confirmed figure and the number a PM would have put to the vendor.
        for row in (out.get("invoices") or []):
            if not (row.get("flagged_count") or 0):
                continue
            # `status` is the state of the VERIFICATION, not of the money. "completed" means
            # the check ran to the end, and it sits on the same row as 23 flagged lines — which
            # is how a live GBP 3,031.59 claim was answered "there are currently no disputed
            # invoices for Gough and Kelly Ltd."
            head = (
                f"This invoice IS IN DISPUTE: {row.get('flagged_count')} of "
                f"{row.get('line_count')} line(s) are flagged. Its status "
                f"({row.get('status')!r}) describes the VERIFICATION — whether the check ran — "
                f"and NOT whether the money is agreed; a 'completed' invoice can be wholly "
                f"under query. Never answer that there are no disputed invoices while this row "
                f"is in front of you. `amount` ({row.get('amount')}) is the WHOLE INVOICE and "
                f"is NOT the amount in dispute; every line being flagged does not make every "
                f"pound disputed. "
            )
            # Absent is not zero, and NULL is not zero either. The deltas arrive from a LEFT
            # JOIN onto invoice_lines in svc-operations-intelligence: missing while that
            # service is behind, NULL when the lines were never loaded. Defaulting either to 0
            # would have the rule state an overcharge of nil on a wholly flagged invoice.
            if row.get("flagged_delta") is not None or row.get("agreed_delta") is not None:
                row["AMOUNT_RULE"] = head + (
                    f"The claimed gap is {row.get('flagged_delta')} and the Adversary has "
                    f"agreed {row.get('agreed_delta')}. Quote the agreed figure as money "
                    f"recoverable, the claimed figure as what is being queried, and the "
                    f"invoice total only as the invoice total. "
                    # Unresolved and contested are different states implying different moves:
                    # one is chased internally, the other is argued with the vendor. The
                    # remainder was labelled "still contested" when 22 of 23 lines had simply
                    # never been looked at.
                    "The remainder is NOT 'contested' or 'disputed by the vendor' unless you "
                    "can show it was reviewed and rejected: a line the Adversary has not "
                    "reached is UNREVIEWED, not disagreed. Call it unresolved or unreviewed. "
                    # The claim is only as real as the rate behind it. Asked directly, this is
                    # answered correctly; asked for "disputed invoices with details" the agent
                    # read only the invoices, never saw the contract, and recommended chasing
                    # a gap measured against a rate nobody had agreed to.
                    "BEFORE presenting any of this as recoverable or as something to put to "
                    "the vendor, call `list_contract_parameters` for this vendor and read "
                    "`defaults_used`: the gap was computed against a contracted rate, and if "
                    "that rate is a platform default on an unconfirmed contract then there is "
                    "no agreed benchmark and the claim cannot be pressed as a breach. Say so "
                    "if it is."
                )
            else:
                row["AMOUNT_RULE"] = head + (
                    "The disputed figure itself is NOT in this payload — the per-line deltas "
                    "have no read route here. Report how many lines are flagged, and say the "
                    "amount under query is not available rather than quoting the total as it."
                )
        return out
    except Exception as exc:
        return _err(exc, "list_invoices")


@tool
async def verify_vendor_invoice(
    lines: list[dict],
    work_orders: list[dict],
    invoice_ref: str | None = None,
    vendor_id: str | None = None,
    labour_day_rate: float | None = None,
    parts_pricing_json: dict | None = None,
    organization_id: str | None = None,
) -> dict:
    """B3 — Verify invoice lines against ingested WOs; flags >£500 via Adversary."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/invoices/verify",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "invoice_ref": invoice_ref,
                "lines": lines,
                "work_orders": work_orders,
                "vendor_id": vendor_id,
                "labour_day_rate": labour_day_rate,
                "parts_pricing_json": parts_pricing_json or {},
                "organization_id": organization_id,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "verify_vendor_invoice")


@tool
async def decide_invoice_line(
    verification_id: str,
    line_id: str,
    decision: str,
    pm_notes: str | None = None,
) -> dict:
    """B3 — PM decision on flagged invoice line: approve | challenge | reject."""
    try:
        resp = await _request(
            "POST",
            _base(),
            f"/api/contract-performance/invoices/{verification_id}/lines/decide",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"line_id": line_id, "decision": decision, "pm_notes": pm_notes},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "decide_invoice_line")


@tool
async def list_contract_approvals(
    status: str = "pending",
    organization_id: str | None = None,
) -> dict:
    """List Feature B Approvals queue items."""
    try:
        params: dict[str, Any] = {"status": status}
        if organization_id:
            params["organization_id"] = organization_id
        resp = await _request(
            "GET",
            _base(),
            "/api/contract-performance/approvals",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "list_contract_approvals")


@tool
async def decide_contract_approval(
    item_id: str,
    decision: str,
    pm_notes: str | None = None,
) -> dict:
    """PM decision on a Contract Performance queue item: approve | edit | dismiss."""
    try:
        resp = await _request(
            "POST",
            _base(),
            f"/api/contract-performance/approvals/{item_id}/decide",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"decision": decision, "pm_notes": pm_notes},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "decide_contract_approval")


@tool
async def extract_contract_from_document(
    source_text: str,
    vendor_id: str | None = None,
    contract_ref: str | None = None,
    organization_id: str | None = None,
    auto_ingest: bool = True,
) -> dict:
    """B1 Relationships Agent — extract SLA params from contract/PO text, draft-ingest for PM edit."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/contracts/extract",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "source_text": source_text,
                "vendor_id": vendor_id,
                "contract_ref": contract_ref,
                "organization_id": organization_id,
                "auto_ingest": auto_ingest,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "extract_contract_from_document")


@tool
async def extract_and_verify_invoice(
    source_text: str | None = None,
    lines: list[dict] | None = None,
    work_orders: list[dict] | None = None,
    invoice_ref: str | None = None,
    vendor_id: str | None = None,
    labour_day_rate: float | None = None,
    organization_id: str | None = None,
) -> dict:
    """B3 — Parse invoice (text or lines) and verify against ingested work orders."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/invoices/extract-verify",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "source_text": source_text,
                "lines": lines or [],
                "work_orders": work_orders or [],
                "invoice_ref": invoice_ref,
                "vendor_id": vendor_id,
                "labour_day_rate": labour_day_rate,
                "organization_id": organization_id,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "extract_and_verify_invoice")


@tool
async def propose_asset_criticality_from_udr(
    organization_id: str | None = None,
    limit: int = 100,
) -> dict:
    """B1 — Read assets from UDR and propose L1/L2/L3 criticality for PM HITL approval."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/asset-criticality/propose-from-udr",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"organization_id": organization_id, "limit": limit},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "propose_asset_criticality_from_udr")


CONTRACT_PERFORMANCE_TOOLS = [
    list_invoices,
    extract_contract_from_document,
    ingest_contract_parameters,
    update_contract_parameters,
    confirm_contract_parameters,
    propose_asset_criticality,
    propose_asset_criticality_from_udr,
    approve_asset_criticality,
    score_vendor_work_orders,
    score_work_orders_from_udr,
    generate_vendor_scorecard,
    list_contract_parameters,
    list_vendor_scorecards,
    get_score_weights,
    update_score_weights,
    extract_and_verify_invoice,
    verify_vendor_invoice,
    decide_invoice_line,
    list_contract_approvals,
    decide_contract_approval,
]
