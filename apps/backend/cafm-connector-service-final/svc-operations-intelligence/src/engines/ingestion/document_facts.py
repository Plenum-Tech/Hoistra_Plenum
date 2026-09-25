"""Every ingested document, read for every domain — not routed to one.

A document is not one kind of thing. An FM contract names the supplier, the assets it covers,
the PPM frequency it commits to and the certificates it obliges the contractor to hold: four
domains in one file. The pipeline picked ONE winner — from the filename and the user's chat
message, never from the document — and ran ONE extractor. The other three domains were never
read, and nothing said so; the file was indexed, the page said "ingested", and the facts were
simply not there.

This reads the text once and tests every domain against it. Three properties hold:

**Absence is a finding.** A domain with no signal in the document is returned as absent with
the reason, not omitted. "We looked and this document says nothing about energy" and "energy
was never checked" are different statements, and only the first is worth trusting.

**The rules are data.** src/reference/document_extraction_rules.json declares, per domain,
what signals its presence and which fields to fetch — each with the real column it is destined
for. Adding a field is an edit to that file. A rule pointing at a column that does not exist
is visible as a bug rather than silently extracting into nothing, which is what
``validate_targets`` is for.

**Nothing is written on thin evidence.** Facts come back with a confidence. At or above the
accept threshold a fact may be persisted by whichever handler owns that domain; between review
and accept it is a draft for a person; below review it is reported and discarded. A
low-confidence figure written into a domain table is indistinguishable from a measured one
once it is a row, and the Assets page has already shown what that costs.

Two domains have handlers that own the write — compliance certificates and vendor contracts,
both of which existed and are deferred to rather than duplicated. Assets, energy and
maintenance have none, so their facts are returned and held. Inventing a meter from a bill
gives a portfolio supply points nobody contracted; inventing a visit from a misread service
sheet creates evidence of work that may never have happened.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import structlog
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

_PATH = pathlib.Path(__file__).resolve().parents[2] / "reference" / "document_extraction_rules.json"
_CACHE: dict[str, Any] | None = None

#: A strong signal is worth this much more than a weak one. A document saying "MPAN" is
#: about energy; one saying "meter" might be a water meter in a lease. The gap is the point.
STRONG_WEIGHT = 3.0
WEAK_WEIGHT = 1.0
#: What it takes to call a domain present. One strong signal clears it; three weak ones do
#: too, because three weak words agreeing is itself evidence. One weak word is not.
PRESENT_SCORE = 3.0


def load_rules() -> dict[str, Any]:
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(_PATH.read_text(encoding="utf-8"))
    return _CACHE


def domains() -> dict[str, Any]:
    """The catalogue, for the page that wants to say what ingestion will look for."""
    r = load_rules()
    return {
        "ok": True,
        "version": r.get("version"),
        "confidence": r.get("confidence") or {},
        "domains": [
            {
                "domain": key,
                "label": d.get("label") or key,
                "handler": d.get("handler"),
                "handler_note": d.get("handler_note"),
                "writes": bool(d.get("handler")),
                "signals_strong": list((d.get("signals") or {}).get("strong") or []),
                "signals_weak": list((d.get("signals") or {}).get("weak") or []),
                "fields": [
                    {"name": f["name"], "question": f["question"],
                     "lands": f.get("lands"), "type": f.get("type") or "text"}
                    for f in (d.get("fields") or [])
                ],
                "field_count": len(d.get("fields") or []),
            }
            for key, d in (r.get("domains") or {}).items()
        ],
    }


def _hits(haystack: str, needles: list[str]) -> list[str]:
    """Which phrases appear, matched on word boundaries so 'ppm' does not fire inside 'ppmv'."""
    found = []
    for n in needles:
        pat = r"(?<![a-z0-9])" + re.escape(n.lower()) + r"(?![a-z0-9])"
        if re.search(pat, haystack):
            found.append(n)
    return found


def detect(document_text: str | None) -> dict[str, Any]:
    """Which domains this document actually speaks to, and for the rest, why not.

    Pure. The whole point of the change is testable here, without a database or a model.
    """
    hay = " " + re.sub(r"\s+", " ", (document_text or "").lower()) + " "
    rules = load_rules()
    out: dict[str, Any] = {}
    for key, d in (rules.get("domains") or {}).items():
        sig = d.get("signals") or {}
        strong = _hits(hay, list(sig.get("strong") or []))
        weak = _hits(hay, list(sig.get("weak") or []))
        score = len(strong) * STRONG_WEIGHT + len(weak) * WEAK_WEIGHT
        present = score >= PRESENT_SCORE
        out[key] = {
            "domain": key,
            "label": d.get("label") or key,
            "present": present,
            "score": round(score, 2),
            "threshold": PRESENT_SCORE,
            "matched_strong": strong,
            "matched_weak": weak,
            # Said in words, because "not present" with no reason is the thing this module
            # exists to stop: a domain that was never read looks exactly like one that was
            # read and found nothing.
            "why": (
                f"{len(strong)} strong and {len(weak)} weak signals matched"
                if present else
                ("nothing in this document matched any signal for this domain"
                 if not strong and not weak else
                 f"only {len(strong)} strong and {len(weak)} weak signals matched, "
                 f"below the threshold of {PRESENT_SCORE}")
            ),
        }
    return out


def fields_for(domain: str) -> list[dict[str, Any]]:
    d = (load_rules().get("domains") or {}).get(domain) or {}
    return list(d.get("fields") or [])


def extraction_plan(document_text: str | None) -> dict[str, Any]:
    """What WOULD be fetched from this document, before anything is fetched.

    Worth having on its own: it is the cheap, model-free answer to "will this document give
    me my vendor data", and it is what the ingest receipt should show.
    """
    det = detect(document_text)
    present = [k for k, v in det.items() if v["present"]]
    absent = [k for k, v in det.items() if not v["present"]]
    plan = {
        k: {
            "label": det[k]["label"],
            "why": det[k]["why"],
            "handler": ((load_rules().get("domains") or {}).get(k) or {}).get("handler"),
            "fields": [f["name"] for f in fields_for(k)],
        }
        for k in present
    }
    return {
        "ok": True,
        "domains_present": present,
        "domains_absent": absent,
        "detection": det,
        "plan": plan,
        "fields_total": sum(len(v["fields"]) for v in plan.values()),
    }


async def validate_targets(session: AsyncSession, *, schema: str = "plenum_cafm") -> dict[str, Any]:
    """Does every `lands` in the catalogue name a column that exists?

    A rule pointing nowhere extracts into nothing and says it succeeded. This is the check
    that makes that loud. JSONB sub-paths (``raw_metadata.unit_rate``) are resolved to their
    containing column, because that is what has to exist.
    """
    rows = (await session.execute(sa_text(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = :s"
    ), {"s": schema})).all()
    have = {(r[0], r[1]) for r in rows}

    ok: list[str] = []
    missing: list[dict[str, str]] = []
    for key, d in (load_rules().get("domains") or {}).items():
        for f in d.get("fields") or []:
            lands = f.get("lands")
            if not lands:
                continue
            parts = lands.split(".")
            if len(parts) < 2:
                missing.append({"domain": key, "field": f["name"], "lands": lands,
                                "why": "not in table.column form"})
                continue
            table, column = parts[0], parts[1]
            if (table, column) in have:
                ok.append(lands)
            else:
                missing.append({"domain": key, "field": f["name"], "lands": lands,
                                "why": f"{schema}.{table}.{column} does not exist here"})
    return {"ok": not missing, "checked": len(ok) + len(missing),
            "resolved": len(ok), "missing": missing, "schema": schema}


def _band(confidence: float | None, rules: dict[str, Any] | None = None) -> str:
    c = (rules or load_rules()).get("confidence") or {}
    accept = float(c.get("accept", 0.85))
    review = float(c.get("review", 0.6))
    if confidence is None:
        return "unknown"
    if confidence >= accept:
        return "accept"
    if confidence >= review:
        return "review"
    return "discard"


def grade(facts: dict[str, Any]) -> dict[str, Any]:
    """Split extracted fields by what may be written, what a person must see, and what is
    too weak to keep.

    ``facts`` is {field_name: {"value": ..., "confidence": 0-1}}.
    """
    rules = load_rules()
    accept: dict[str, Any] = {}
    review: dict[str, Any] = {}
    discard: dict[str, Any] = {}
    for name, f in (facts or {}).items():
        if not isinstance(f, dict):
            f = {"value": f, "confidence": None}
        if f.get("value") in (None, "", []):
            continue
        band = _band(f.get("confidence"), rules)
        {"accept": accept, "review": review, "unknown": review}.get(band, discard)[name] = f
    return {"accept": accept, "review": review, "discard": discard,
            "counts": {"accept": len(accept), "review": len(review), "discard": len(discard)}}
