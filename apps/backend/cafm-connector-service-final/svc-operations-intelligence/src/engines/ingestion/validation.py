"""Does this document belong to this building? — the check that runs before anything binds.

The risk is ordinary and expensive: a contract filed under the wrong property, a vendor
certificate for a firm that has never worked there, a Bishopsgate document uploaded under
Riverside, a company-wide policy attached to one building as though it were about that
building. Each is one click to make, months to notice, and by then a compliance position,
a renewal ladder and a report have all been computed from it.

So the claim is checked against the evidence before the write, for everyone. An admin
approving their own upload is not a check — an admin can select the wrong building exactly
as easily as anyone — so the protocol does not branch on role.

What comes out is a **case**, not a boolean:

    matched     every identifying claim the document makes agrees with this building, and
                nothing contradicts it. It proceeds.
    uncertain   nothing contradicts it and nothing confirms it either — a new building, a
                document that names no property, a claim the register cannot speak to.
                Explicit yes/no before it is written.
    mismatch    something the document says contradicts this building, and often another
                building fits it better. The uploader is told what and which, asked to
                explain, and the explanation is assessed. Explicit yes/no either way.

Nothing here decides on its own that a document may be filed against an objection. The
furthest the agent goes is "your explanation accounts for the warning — shall I proceed?",
and a person answers that.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ..auth import ingestion_audit
from . import claims as claims_engine
from . import ontology as onto
from .claims import Claims, name_overlap
from .ontology import BuildingOntology, normalise

log = get_logger(__name__)

MATCHED, UNCERTAIN, MISMATCH = "matched", "uncertain", "mismatch"
#: A case's position in the protocol.
VALIDATED = "validated"                      # clean — ingestion may proceed
NEEDS_CLARIFICATION = "needs_clarification"  # a question is outstanding
NEEDS_CONFIRMATION = "needs_confirmation"    # an explicit yes/no is outstanding
ACCEPTED, OVERRIDDEN, REASSIGNED, REJECTED = "accepted", "overridden", "reassigned", "rejected"
OPEN_STATUSES = (NEEDS_CLARIFICATION, NEEDS_CONFIRMATION, VALIDATED)
CLOSED_STATUSES = (ACCEPTED, OVERRIDDEN, REASSIGNED, REJECTED)

#: A name match at or above this is the same building; at or below the lower bound it is a
#: different one. Between them is a question, not an answer.
NAME_SAME = 0.7
NAME_DIFFERENT = 0.34
#: How much better another building must fit before it is worth suggesting.
SUGGEST_MARGIN = 2.0

SUPPORTS, CONFLICTS, UNKNOWN = "supports", "conflicts", "unknown"


@dataclass
class Finding:
    check: str
    direction: str
    weight: float
    message: str
    claimed: Any = None
    known: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.check, "direction": self.direction, "weight": self.weight,
                "message": self.message, "claimed": self.claimed, "known": self.known}


# ── the checks ───────────────────────────────────────────────────────────────────────

def compare(c: Claims, o: BuildingOntology) -> list[Finding]:
    """Every check this document and this building can answer between them.

    A check the evidence cannot speak to is reported as ``unknown`` rather than skipped: the
    difference between "the vendor is not one of this building's" and "this building has no
    vendors on record" is the whole difference between a warning and a shrug.
    """
    out: list[Finding] = []

    # ── identity: the property the document names ────────────────────────────────────
    if c.buildings:
        best, matched_name = 0.0, None
        for claim in c.buildings:
            score, name = name_overlap(claim, o.names)
            if score > best:
                best, matched_name = score, name
        # Only a name the DOCUMENT states can contradict this building. A file name may
        # agree with one and may point at a better candidate, but a scan saved as
        # "scan001.pdf" is not the document claiming to belong somewhere else.
        authored = c.authored_buildings()
        claimed = c.buildings[0] if len(c.buildings) == 1 else c.buildings
        if best >= NAME_SAME:
            out.append(Finding("building_name", SUPPORTS, 3.0,
                               f"The document names “{matched_name}”, which is this building.",
                               claimed, matched_name))
        elif not authored:
            out.append(Finding("building_name", UNKNOWN, 0.0,
                               "The document does not name a property; only the file name "
                               "suggests one, which is not evidence either way.",
                               claimed, o.name))
        elif best <= NAME_DIFFERENT:
            out.append(Finding("building_name", CONFLICTS, 3.0,
                               f"The document names “{authored[0]}”; this building is "
                               f"“{o.name}”.", authored, o.name))
        else:
            out.append(Finding("building_name", UNKNOWN, 1.0,
                               f"The document names “{authored[0]}”, which is close to but "
                               f"not clearly “{o.name}”.", authored, o.name))
    else:
        out.append(Finding("building_name", UNKNOWN, 0.0,
                           "The document does not name a property.", None, o.name))

    # ── identity: a code, which is exact or it is nothing ────────────────────────────
    if c.codes and o.codes:
        hit = next((k for k in c.codes for v in o.codes if normalise(k) == normalise(v)), None)
        if hit:
            out.append(Finding("building_code", SUPPORTS, 3.0,
                               f"The document carries building reference {hit}, which is this "
                               f"building's.", hit, o.codes))
        else:
            out.append(Finding("building_code", CONFLICTS, 2.0,
                               f"The document carries building reference {c.codes[0]}; this "
                               f"building is {', '.join(o.codes)}.", c.codes, o.codes))

    # ── place ────────────────────────────────────────────────────────────────────────
    if c.countries and o.country_code:
        if o.country_code in c.countries:
            out.append(Finding("country", SUPPORTS, 1.5,
                               f"The document is a {o.country_code} document and so is this "
                               f"building.", c.countries, o.country_code))
        else:
            out.append(Finding("country", CONFLICTS, 3.0,
                               f"The document relates to {', '.join(c.countries)}; this "
                               f"building is in {o.country_code}. Regulations, vendors and "
                               f"certificate types differ by country.", c.countries, o.country_code))
    elif c.countries and not o.country_code:
        out.append(Finding("country", UNKNOWN, 0.0,
                           f"The document relates to {', '.join(c.countries)}; this building "
                           f"has no country recorded.", c.countries, None))

    for kind, claimed_list, known in (("region", c.regions, o.region or o.city),
                                      ("state", c.states, o.state)):
        if claimed_list and known:
            best, _ = name_overlap(known, claimed_list)
            if best >= NAME_SAME:
                out.append(Finding(kind, SUPPORTS, 1.0,
                                   f"The document's {kind} matches this building's ({known}).",
                                   claimed_list, known))
            elif best <= NAME_DIFFERENT:
                out.append(Finding(kind, CONFLICTS, 1.0,
                                   f"The document's {kind} is {claimed_list[0]}; this building "
                                   f"is in {known}.", claimed_list, known))

    if c.postcodes and o.postcode:
        norm = {normalise(p).replace(" ", "") for p in c.postcodes}
        if normalise(o.postcode).replace(" ", "") in norm:
            out.append(Finding("postcode", SUPPORTS, 3.0,
                               f"The document's postcode is this building's ({o.postcode}).",
                               c.postcodes, o.postcode))
        else:
            out.append(Finding("postcode", CONFLICTS, 2.0,
                               f"The document's postcode is {c.postcodes[0]}; this building's "
                               f"is {o.postcode}.", c.postcodes, o.postcode))

    # ── the vendor whose paper this is ───────────────────────────────────────────────
    if c.vendors:
        if o.vendors:
            hit = next((v for v in c.vendors
                        if name_overlap(v, o.vendors)[0] >= NAME_SAME), None)
            if hit:
                out.append(Finding("vendor", SUPPORTS, 2.0,
                                   f"{hit} is a vendor known at this building.", hit, o.vendors))
            else:
                out.append(Finding("vendor", CONFLICTS, 2.0,
                                   f"“{c.vendors[0]}” is not a vendor recorded at this "
                                   f"building. Known here: {', '.join(o.vendors[:5])}.",
                                   c.vendors, o.vendors))
        else:
            out.append(Finding("vendor", UNKNOWN, 0.0,
                               f"The document names “{c.vendors[0]}”; this building has no "
                               f"vendors on record to check against.", c.vendors, []))

    # ── the plant it talks about ─────────────────────────────────────────────────────
    if c.assets:
        if o.assets:
            hits = [a for a in c.assets
                    if any(normalise(a) == normalise(k) for k in o.assets)]
            if hits:
                out.append(Finding("assets", SUPPORTS, 2.0,
                                   f"The document references {', '.join(hits[:4])}, which "
                                   f"{'is' if len(hits) == 1 else 'are'} on this building.",
                                   hits, None))
            else:
                out.append(Finding("assets", CONFLICTS, 1.0,
                                   f"None of the asset references in the document "
                                   f"({', '.join(c.assets[:4])}) are on this building.",
                                   c.assets, o.assets[:8]))
        else:
            out.append(Finding("assets", UNKNOWN, 0.0,
                               "The document references assets; this building has none on "
                               "record to check against.", c.assets, []))

    # ── references that should already exist somewhere ───────────────────────────────
    for kind, claimed_list, known in (("contract_ref", c.contract_refs, o.contract_refs),
                                      ("certificate_number", c.certificate_numbers,
                                       o.certificate_numbers)):
        if claimed_list and known:
            hit = next((k for k in claimed_list
                        if any(normalise(k) == normalise(v) for v in known)), None)
            if hit:
                out.append(Finding(kind, SUPPORTS, 1.5,
                                   f"{hit} is already on record against this building.",
                                   hit, None))

    # ── how much this building can say at all ────────────────────────────────────────
    if o.is_new:
        out.append(Finding("building_evidence", UNKNOWN, 0.0,
                           "This building has no assets, documents, certificates or vendors "
                           "on record yet, so there is little to check the document against.",
                           None, o.counts))
    return out


def verdict_for(findings: list[Finding], o: BuildingOntology) -> tuple[str, float]:
    """The verdict and how sure it is.

    One rule decides it: a document is *matched* only when something identifies this
    building — its name, its reference or its postcode — and nothing contradicts it.
    Supporting evidence that does not identify (a country that agrees, a vendor who works
    here) is not identity: half the portfolio is in the same country. Everything else is
    uncertain, and anything contradicted is a mismatch.
    """
    conflicts = [f for f in findings if f.direction == CONFLICTS]
    supports = [f for f in findings if f.direction == SUPPORTS]
    identifying = {"building_name", "building_code", "postcode"}
    identified = [f for f in supports if f.check in identifying]
    weight_for = sum(f.weight for f in supports)
    weight_against = sum(f.weight for f in conflicts)

    if conflicts:
        confidence = min(0.99, weight_against / (weight_against + weight_for + 1.0))
        return MISMATCH, round(confidence, 3)
    if identified:
        confidence = min(0.99, weight_for / (weight_for + 2.0))
        return MATCHED, round(confidence, 3)
    return UNCERTAIN, round(min(0.6, weight_for / (weight_for + 4.0)), 3)


def score_candidate(c: Claims, row: dict[str, Any]) -> float:
    """How well a portfolio building fits the document's claims. Used only for ranking."""
    names = [row.get("name"), row.get("building_name"), row.get("site_name")]
    codes = [row.get("building_code"), row.get("site_code")]
    score = 0.0
    for claim in c.buildings:
        best, _ = name_overlap(claim, [n for n in names if n])
        score = max(score, best * 3.0)
    if c.codes and any(normalise(k) == normalise(v) for k in c.codes for v in codes if v):
        score += 3.0
    if c.postcodes and row.get("postcode") and \
            normalise(row["postcode"]).replace(" ", "") in {normalise(p).replace(" ", "") for p in c.postcodes}:
        score += 3.0
    if c.countries and (row.get("country_code") or "").upper() in c.countries:
        score += 1.0
    for claimed_list, known in ((c.regions, row.get("region") or row.get("city")),
                                (c.states, row.get("state"))):
        if claimed_list and known and name_overlap(known, claimed_list)[0] >= NAME_SAME:
            score += 1.0
    return round(score, 3)


async def rank_candidates(
    session: AsyncSession,
    c: Claims,
    *,
    organization_id: UUID | str | None,
    building_ids: list[UUID] | None,
    selected_building_id: str,
) -> list[dict[str, Any]]:
    """Buildings the document fits better than the one that was chosen, best first.

    Only ever within the caller's own scope: suggesting a building somebody may not see
    would leak the portfolio, and "did you mean this building?" is useless if they cannot
    file against it anyway.
    """
    rows = await onto.portfolio(session, organization_id=organization_id, building_ids=building_ids)
    scored = []
    for r in rows:
        s = score_candidate(c, r)
        if s <= 0:
            continue
        scored.append({"building_id": r["building_id"],
                       "name": r.get("name") or r.get("building_name") or r.get("site_name"),
                       "score": s, "country_code": r.get("country_code"),
                       "region": r.get("region") or r.get("city"),
                       "selected": r["building_id"] == selected_building_id})
    scored.sort(key=lambda x: -x["score"])
    return scored[:5]


def suggestion_from(candidates: list[dict[str, Any]], selected_building_id: str) -> dict[str, Any] | None:
    """The building to offer instead, when one fits materially better than the choice made."""
    if not candidates:
        return None
    best = candidates[0]
    if best["building_id"] == selected_building_id:
        return None
    mine = next((c["score"] for c in candidates if c["building_id"] == selected_building_id), 0.0)
    if best["score"] - mine < SUGGEST_MARGIN:
        return None
    return best


def question_for(verdict: str, findings: list[Finding], suggestion: dict[str, Any] | None,
                 building_name: str | None) -> str:
    """What to put to the uploader. Written as a question a person can answer, not an error."""
    conflicts = [f for f in findings if f.direction == CONFLICTS]
    if verdict == MISMATCH and suggestion:
        return (f"{conflicts[0].message} This document looks like it belongs to "
                f"{suggestion['name']} rather than {building_name}. Would you like to file it "
                f"against {suggestion['name']} instead — or tell me why it belongs here?")
    if verdict == MISMATCH:
        listed = " ".join(f.message for f in conflicts[:3])
        return (f"{listed} Can you tell me why this document should be filed against "
                f"{building_name}?")
    return (f"Nothing in this document identifies {building_name}, so I cannot confirm it "
            f"belongs here. Can you tell me what connects it to this building?")


# ── the case ─────────────────────────────────────────────────────────────────────────

def _event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"at": datetime.now(timezone.utc).isoformat(), "event": kind, **fields}


def _row_to_case(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    for k in ("id", "organization_id", "actor_user_id", "selected_building_id",
              "suggested_building_id", "final_building_id", "document_id", "decided_by"):
        if out.get(k) is not None:
            out[k] = str(out[k])
    for k in ("created_at", "updated_at", "decided_at"):
        if out.get(k) is not None:
            out[k] = out[k].isoformat()
    if out.get("confidence") is not None:
        out["confidence"] = float(out["confidence"])
    out["open"] = out.get("status") in OPEN_STATUSES
    out["may_ingest"] = out.get("status") in (ACCEPTED, OVERRIDDEN, REASSIGNED)
    return out


async def _load(session: AsyncSession, case_id: UUID | str) -> dict[str, Any] | None:
    r = (await session.execute(text("""
        SELECT * FROM plenum_cafm.ingestion_validation_cases WHERE id = CAST(:i AS uuid)"""),
        {"i": str(case_id)})).mappings().first()
    return _row_to_case(dict(r)) if r else None


async def _building_name(session: AsyncSession, building_id: str | None) -> str | None:
    if not building_id:
        return None
    return (await session.execute(
        text("SELECT name FROM plenum_cafm.buildings WHERE building_id = CAST(:b AS uuid)"),
        {"b": str(building_id)})).scalar()


async def open_case(
    session: AsyncSession,
    *,
    organization_id: UUID | None,
    actor_user_id: UUID | None,
    actor_role: str | None,
    building_id: UUID | str,
    document_name: str | None,
    document_id: UUID | None = None,
    document_sha256: str | None = None,
    doc_type: str | None = None,
    text_body: str | None = None,
    extracted: dict[str, Any] | None = None,
    session_id: str | None = None,
    allowed_building_ids: list[UUID] | None = None,
) -> dict[str, Any]:
    """Run the protocol for one document against one building, and record the case.

    Always creates a case — including when the document is clean — because "this was
    checked and it matched" is as much a part of the trail as a warning that was overridden.
    """
    o = await onto.load(session, building_id)
    if o is None:
        return {"ok": False, "error": "building_not_found", "building_id": str(building_id)}

    c = claims_engine.from_document(text=text_body, file_name=document_name,
                                    extracted=extracted, doc_type=doc_type)
    findings = compare(c, o)
    verdict, confidence = verdict_for(findings, o)
    candidates = await rank_candidates(
        session, c, organization_id=organization_id or o.organization_id,
        building_ids=allowed_building_ids, selected_building_id=str(building_id))
    suggestion = suggestion_from(candidates, str(building_id))
    # A better-fitting building turns an "I cannot confirm this" into "this looks like it
    # belongs elsewhere" — the same evidence, but now with somewhere to point.
    if suggestion and verdict == UNCERTAIN:
        verdict = MISMATCH
    status = VALIDATED if verdict == MATCHED else (
        NEEDS_CLARIFICATION if verdict == MISMATCH else NEEDS_CONFIRMATION)
    question = None if verdict == MATCHED else question_for(verdict, findings, suggestion, o.name)

    case_id = uuid4()
    events = [_event("validated", verdict=verdict, confidence=confidence,
                     checks=[f.check for f in findings],
                     conflicts=[f.message for f in findings if f.direction == CONFLICTS])]
    if question:
        events.append(_event("asked", question=question))
    await session.execute(text("""
        INSERT INTO plenum_cafm.ingestion_validation_cases
            (id, organization_id, actor_user_id, actor_role, session_id, document_name,
             document_id, document_sha256, doc_type, selected_building_id,
             suggested_building_id, verdict, confidence, status, findings, claims, ontology,
             candidates, question, events)
        VALUES (CAST(:id AS uuid), CAST(:org AS uuid), CAST(:actor AS uuid), :role, :sid, :dn,
                CAST(:did AS uuid), :sha, :dt, CAST(:b AS uuid), CAST(:sug AS uuid), :v, :conf,
                :st, CAST(:f AS jsonb), CAST(:cl AS jsonb), CAST(:on AS jsonb),
                CAST(:ca AS jsonb), :q, CAST(:ev AS jsonb))"""),
        {"id": str(case_id), "org": str(organization_id) if organization_id else o.organization_id,
         "actor": str(actor_user_id) if actor_user_id else None, "role": actor_role,
         "sid": session_id, "dn": document_name,
         "did": str(document_id) if document_id else None, "sha": document_sha256, "dt": doc_type,
         "b": str(building_id), "sug": suggestion["building_id"] if suggestion else None,
         "v": verdict, "conf": confidence, "st": status,
         "f": json.dumps([f.as_dict() for f in findings], default=str),
         "cl": json.dumps(c.as_dict(), default=str), "on": json.dumps(o.as_dict(), default=str),
         "ca": json.dumps(candidates, default=str), "q": question,
         "ev": json.dumps(events, default=str)})
    await ingestion_audit.record(
        session, outcome="validated" if verdict == MATCHED else "held",
        organization_id=organization_id, actor_user_id=actor_user_id, actor_role=actor_role,
        document_name=document_name, document_id=document_id, building_id=UUID(str(building_id)),
        warning=None if verdict == MATCHED else question,
        detail={"case_id": str(case_id), "verdict": verdict, "confidence": confidence,
                "checks": [f.as_dict() for f in findings],
                "suggested_building_id": suggestion["building_id"] if suggestion else None},
        case_id=case_id,
    )
    await session.commit()
    case = await _load(session, case_id)
    return {"ok": True, **(case or {}),
            "suggestion": suggestion,
            "message": ("The document matches this building." if verdict == MATCHED
                        else question)}


async def clarify(
    session: AsyncSession,
    *,
    case_id: UUID | str,
    explanation: str,
    actor_user_id: UUID | None,
    actor_role: str | None,
) -> dict[str, Any]:
    """The uploader's answer, and what the agent makes of it.

    The assessment never releases the document by itself — at best it moves the case from
    "explain this" to "I am satisfied; shall I proceed?", which a person still answers.
    """
    from .explanation import assess

    case = await _load(session, case_id)
    if case is None:
        return {"ok": False, "error": "case_not_found"}
    if case["status"] in CLOSED_STATUSES:
        return {"ok": False, "error": "case_closed", "status": case["status"]}

    findings = case.get("findings") or []
    assessment = await assess(
        explanation=explanation,
        findings=findings,
        building_name=(case.get("ontology") or {}).get("name"),
        document_name=case.get("document_name"),
        verdict=case.get("verdict"),
    )
    events = (case.get("events") or []) + [
        _event("explained", explanation=explanation, by=str(actor_user_id) if actor_user_id else None),
        _event("assessed", resolves=assessment["resolves"], reason=assessment["reason"],
               method=assessment.get("method")),
    ]
    next_question = (
        f"{assessment['reason']} Shall I file it against "
        f"{(case.get('ontology') or {}).get('name')}?"
        if assessment["resolves"] else
        f"{assessment['reason']} If you still want it filed against "
        f"{(case.get('ontology') or {}).get('name')}, say yes and I will record the override "
        f"against your name."
    )
    events.append(_event("asked", question=next_question))
    await session.execute(text("""
        UPDATE plenum_cafm.ingestion_validation_cases
           SET explanation = :x, assessment = CAST(:a AS jsonb), question = :q,
               status = :st, events = CAST(:ev AS jsonb), updated_at = now()
         WHERE id = CAST(:i AS uuid)"""),
        {"x": explanation, "a": json.dumps(assessment, default=str), "q": next_question,
         "st": NEEDS_CONFIRMATION, "ev": json.dumps(events, default=str), "i": str(case_id)})
    await ingestion_audit.record(
        session, outcome="clarified", organization_id=_uuid(case.get("organization_id")),
        actor_user_id=actor_user_id, actor_role=actor_role,
        document_name=case.get("document_name"), document_id=_uuid(case.get("document_id")),
        building_id=_uuid(case.get("selected_building_id")),
        warning=case.get("question"), explanation=explanation,
        detail={"case_id": str(case_id), "assessment": assessment}, case_id=UUID(str(case_id)),
    )
    await session.commit()
    return {"ok": True, **(await _load(session, case_id) or {}),
            "assessment": assessment, "message": next_question,
            "requires_confirmation": True}


async def reassign(
    session: AsyncSession,
    *,
    case_id: UUID | str,
    building_id: UUID | str,
    actor_user_id: UUID | None,
    actor_role: str | None,
    allowed_building_ids: list[UUID] | None = None,
) -> dict[str, Any]:
    """Move the case to another building and check it again there.

    The second check is the point: agreeing to a suggestion is not evidence, and a document
    moved to a building it also does not match must say so rather than inherit an approval.
    """
    case = await _load(session, case_id)
    if case is None:
        return {"ok": False, "error": "case_not_found"}
    if case["status"] in CLOSED_STATUSES:
        return {"ok": False, "error": "case_closed", "status": case["status"]}
    o = await onto.load(session, building_id)
    if o is None:
        return {"ok": False, "error": "building_not_found", "building_id": str(building_id)}

    c = Claims(**{k: v for k, v in (case.get("claims") or {}).items()
                  if k in Claims.__dataclass_fields__})
    findings = compare(c, o)
    verdict, confidence = verdict_for(findings, o)
    status = VALIDATED if verdict == MATCHED else (
        NEEDS_CLARIFICATION if verdict == MISMATCH else NEEDS_CONFIRMATION)
    question = (None if verdict == MATCHED
                else question_for(verdict, findings, None, o.name))
    events = (case.get("events") or []) + [
        _event("reassigned", to_building_id=str(building_id), to=o.name,
               by=str(actor_user_id) if actor_user_id else None),
        _event("validated", verdict=verdict, confidence=confidence,
               conflicts=[f.message for f in findings if f.direction == CONFLICTS]),
    ]
    if question:
        events.append(_event("asked", question=question))
    await session.execute(text("""
        UPDATE plenum_cafm.ingestion_validation_cases
           SET selected_building_id = CAST(:b AS uuid), verdict = :v, confidence = :conf,
               status = :st, findings = CAST(:f AS jsonb), ontology = CAST(:on AS jsonb),
               question = :q, events = CAST(:ev AS jsonb), updated_at = now()
         WHERE id = CAST(:i AS uuid)"""),
        {"b": str(building_id), "v": verdict, "conf": confidence, "st": status,
         "f": json.dumps([f.as_dict() for f in findings], default=str),
         "on": json.dumps(o.as_dict(), default=str), "q": question,
         "ev": json.dumps(events, default=str), "i": str(case_id)})
    await ingestion_audit.record(
        session, outcome="reassigned", organization_id=_uuid(case.get("organization_id")),
        actor_user_id=actor_user_id, actor_role=actor_role,
        document_name=case.get("document_name"), document_id=_uuid(case.get("document_id")),
        building_id=_uuid(case.get("selected_building_id")),
        reassigned_to_building_id=UUID(str(building_id)),
        detail={"case_id": str(case_id), "verdict": verdict}, case_id=UUID(str(case_id)),
    )
    await session.commit()
    return {"ok": True, **(await _load(session, case_id) or {}),
            "message": ("The document matches that building." if verdict == MATCHED
                        else question)}


async def decide(
    session: AsyncSession,
    *,
    case_id: UUID | str,
    approve: bool,
    actor_user_id: UUID | None,
    actor_role: str | None,
    note: str | None = None,
) -> dict[str, Any]:
    """The explicit yes or no. Nothing is written to a building without passing through here.

    The outcome recorded distinguishes the three ways a yes can arrive: the document matched
    and was accepted, it was moved and then accepted, or it was filed over a warning the
    agent did not withdraw — which is an override, and is recorded against the name of the
    person who gave it.
    """
    case = await _load(session, case_id)
    if case is None:
        return {"ok": False, "error": "case_not_found"}
    if case["status"] in CLOSED_STATUSES:
        return {"ok": False, "error": "case_closed", "status": case["status"],
                "outcome": case.get("outcome")}

    assessment = case.get("assessment") or {}
    was_reassigned = any(e.get("event") == "reassigned" for e in (case.get("events") or []))
    if not approve:
        outcome, status = "rejected", REJECTED
    elif case.get("verdict") == MATCHED:
        outcome, status = ("reassigned", REASSIGNED) if was_reassigned else ("accepted", ACCEPTED)
    elif assessment.get("resolves"):
        # The agent was satisfied by the explanation and a person confirmed it. Recorded as
        # approved on confirmation, not as a clean acceptance: the document did not match.
        outcome, status = ("reassigned", REASSIGNED) if was_reassigned else \
            ("approved_on_confirmation", ACCEPTED)
    else:
        outcome, status = "overridden", OVERRIDDEN

    events = (case.get("events") or []) + [
        _event("decided", approve=approve, outcome=outcome, note=note,
               by=str(actor_user_id) if actor_user_id else None)]
    await session.execute(text("""
        UPDATE plenum_cafm.ingestion_validation_cases
           SET status = :st, outcome = :oc, decided_by = CAST(:by AS uuid), decided_at = now(),
               decision_note = :note, final_building_id = CASE WHEN :ok THEN selected_building_id
                                                               ELSE NULL END,
               events = CAST(:ev AS jsonb), updated_at = now()
         WHERE id = CAST(:i AS uuid)"""),
        {"st": status, "oc": outcome, "by": str(actor_user_id) if actor_user_id else None,
         "note": note, "ok": bool(approve), "ev": json.dumps(events, default=str),
         "i": str(case_id)})
    await ingestion_audit.record(
        session, outcome=outcome, organization_id=_uuid(case.get("organization_id")),
        actor_user_id=actor_user_id, actor_role=actor_role,
        document_name=case.get("document_name"), document_id=_uuid(case.get("document_id")),
        building_id=_uuid(case.get("selected_building_id")),
        warning=case.get("question"), explanation=case.get("explanation"),
        approved_by=actor_user_id if approve else None,
        detail={"case_id": str(case_id), "verdict": case.get("verdict"),
                "assessment": assessment, "note": note,
                "findings": case.get("findings")},
        case_id=UUID(str(case_id)),
    )
    await session.commit()
    final = await _load(session, case_id)
    return {"ok": True, **(final or {}),
            "message": ("Filed against " + str((case.get("ontology") or {}).get("name"))
                        if approve else "Not filed. The document was not ingested.")}


async def get(session: AsyncSession, case_id: UUID | str) -> dict[str, Any] | None:
    return await _load(session, case_id)


async def list_cases(
    session: AsyncSession,
    *,
    organization_id: UUID | None,
    building_ids: list[UUID] | None = None,
    status: str | None = None,
    open_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    where = ["1=1"]
    params: dict[str, Any] = {"lim": limit}
    if organization_id:
        where.append("organization_id = CAST(:o AS uuid)")
        params["o"] = str(organization_id)
    if building_ids is not None:
        where.append("(selected_building_id = ANY(CAST(:b AS uuid[])) "
                     "OR final_building_id = ANY(CAST(:b AS uuid[])))")
        params["b"] = [str(x) for x in building_ids] or ["00000000-0000-0000-0000-000000000000"]
    if status:
        where.append("status = :st")
        params["st"] = status
    elif open_only:
        where.append("status = ANY(CAST(:sts AS varchar[]))")
        params["sts"] = list(OPEN_STATUSES)
    rows = (await session.execute(text(f"""
        SELECT id, organization_id, actor_user_id, actor_role, document_name, document_id,
               doc_type, selected_building_id, suggested_building_id, final_building_id,
               verdict, confidence, status, outcome, question, explanation, assessment,
               findings, candidates, created_at, updated_at, decided_at, decided_by
          FROM plenum_cafm.ingestion_validation_cases
         WHERE {' AND '.join(where)}
         ORDER BY created_at DESC LIMIT :lim"""), params)).mappings().all()
    return [_row_to_case(dict(r)) for r in rows]


def _uuid(v: Any) -> UUID | None:
    try:
        return UUID(str(v)) if v else None
    except (ValueError, TypeError):
        return None
