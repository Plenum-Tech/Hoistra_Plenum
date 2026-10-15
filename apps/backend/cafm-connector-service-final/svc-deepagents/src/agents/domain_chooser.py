"""A general question is asked back with the six areas; a reply continues the conversation.

The user's rule (7 Oct 2026): a question that does not say which part of the business it is about
- "what is pending today's approval" - is answered with a choice first: compliance, contracts,
invoices, assets, energy or maintenance, or all of them. The same six options replace the router's
own clarifying question, which listed tables ("parts stock, assets, work orders...").

Two failures from the same conversation are handled here too:

* a reply to a question the assistant asked ("vendor name - LPS, email - ...") was routed as a new
  question, with no conversation, and bounced back as unclear - twice;
* a typed choice ("all of them") after a clarifying question was answered with a promise, not
  with data, because nothing remembered which question it answered.

Pure functions; the orchestrator keeps the pending question in the session state.
"""
from __future__ import annotations

import re

DOMAINS: list[dict[str, str]] = [
    {"key": "compliance", "label": "Compliance", "icon": "ph-shield-check",
     "detail": "Certificates, accreditations and statutory duties"},
    {"key": "contract", "label": "Contracts", "icon": "ph-handshake",
     "detail": "Vendor contracts, SLAs and scorecards"},
    {"key": "invoice", "label": "Invoices", "icon": "ph-receipt",
     "detail": "Invoice lines, contract rates and held payments"},
    {"key": "asset", "label": "Assets", "icon": "ph-cube",
     "detail": "Condition, criticality, replacement and repair"},
    {"key": "energy", "label": "Energy", "icon": "ph-lightning",
     "detail": "Consumption, anomalies, EUI and cost"},
    {"key": "maintenance", "label": "Maintenance", "icon": "ph-wrench",
     "detail": "Work orders, PPM and decisions owed"},
]
_LABEL = {d["key"]: d["label"] for d in DOMAINS}

#: Words that already say which area a question is about.
_DOMAIN_WORDS = re.compile(
    r"\b(complian\w*|certificat\w*|accredit\w*|statutory|licen[cs]\w*|insurance|fra|eicr|loler|gas safe|"
    r"contract\w*|sla|slas|scorecard\w*|vendor\w*|supplier\w*|contractor\w*|"
    r"invoice\w*|billing|bill|bills|payment\w*|credit note\w*|"
    r"asset\w*|equipment|boiler\w*|chiller\w*|lift\w*|ahu\w*|pump\w*|generator\w*|condition|"
    r"energy|meter\w*|consumption|kwh|eui|anomal\w*|carbon|utility|utilities|"
    r"maintenance|work ?orders?|wo|ppm|repair\w*|technician\w*|inspection\w*|spare parts?|stock|"
    r"migrat\w*|import|upload|document\w*|hoist score)\b", re.I)

#: What a general question asks about when it names no area.
_GENERAL = re.compile(
    r"\b(pending|approv\w*|outstanding|attention|to ?do|overdue|urgent|priorit\w*|"
    r"issues?|problems?|summary|status|what'?s new|anything|everything|worry|"
    r"decisions?|action\w*|due|late|risk\w*|alerts?|notifications?|backlog|open items?)\b", re.I)

#: A short reply that picks areas.
_PICK = {
    "compliance": re.compile(r"\b(complian\w*|certificat\w*|statutory)\b", re.I),
    "contract": re.compile(r"\b(contract\w*|vendor\w*|sla|scorecard\w*)\b", re.I),
    "invoice": re.compile(r"\b(invoice\w*|billing|payments?)\b", re.I),
    "asset": re.compile(r"\b(asset\w*|equipment)\b", re.I),
    "energy": re.compile(r"\b(energy|meters?|consumption)\b", re.I),
    "maintenance": re.compile(r"\b(maintenance|work ?orders?|ppm)\b", re.I),
}
_ALL = re.compile(r"\b(all( of (them|these|it))?|every(thing| one)?|both|each|any)\b", re.I)

#: The assistant asked the user for something and is waiting for it.
_ASKS_BACK = re.compile(
    r"(\b(reply with|tell me|let me know|please (provide|confirm|share|send|give)|which (one|of|do you)|"
    r"do you want|would you like|shall i|i need the|send me|give me the|confirm (the|which|whether))\b)|\?\s*$",
    re.I | re.M)


#: A message that leans on the last answer without "these" or "those": "each one of the assets",
#: "you just gave me", "the previous list", "go ahead". thread_scope.is_followup stays narrow
#: because it also decides the working-set filter; this only decides what the turn is told.
_REFERS_BACK = re.compile(
    r"\b(each (one )?of the|for each (one|of them)|every one of the|you (just )?(gave|listed|showed|sent)|"
    r"(the )?(previous|prior|earlier|last) (answer|list|table|thread|results?|response|reply)|"
    r"go ahead|as (above|before)|from (your|the) (answer|table|list))\b", re.I)


def refers_back(message: str | None) -> bool:
    return bool(_REFERS_BACK.search(message or ""))


def names_domain(text: str | None) -> bool:
    return bool(_DOMAIN_WORDS.search(text or ""))


def is_general(question: str, page_context: str | None = None) -> bool:
    """A question that asks what is pending, at risk or needing attention, without saying in which
    area - and not asked from a page that already says it (the Maintenance page's ask bar)."""
    q = " ".join((question or "").split())
    if not q or len(q) > 160:
        return False
    if names_domain(q) or names_domain(page_context):
        return False
    return bool(_GENERAL.search(q))


def pick_domains(reply: str) -> list[str] | None:
    """The areas a short reply chooses: a list of keys, ["all"], or None if it chooses nothing."""
    r = " ".join((reply or "").split())
    if not r or len(r) > 80:
        return None
    picked = [k for k, rx in _PICK.items() if rx.search(r)]
    if picked:
        return picked
    if _ALL.search(r):
        return ["all"]
    return None


def question_for(question: str, domains: list[str]) -> str:
    """The original question, narrowed to the areas chosen."""
    q = (question or "").strip().rstrip("?").strip()
    if not domains or domains == ["all"]:
        return f"{q} - across compliance, contracts, invoices, assets, energy and maintenance?"
    names = [_LABEL.get(d, d).lower() for d in domains]
    return f"{q} - {', '.join(names)} only?"


def asks_back(answer: str | None) -> bool:
    """The previous answer ended by asking the user for something (a name, an email, a choice)."""
    tail = (answer or "").strip()[-600:]
    return bool(tail) and bool(_ASKS_BACK.search(tail))


def reply_text(reason: str | None = None) -> str:
    lead = ("That could be about more than one part of the business"
            + (f" ({reason.rstrip('.')})" if reason else "") + ".")
    return (lead + " Which area do you mean - compliance, contracts, invoices, assets, energy or "
            "maintenance? Or all of them.")


def choices(question: str) -> list[dict]:
    """The six areas and "All of these" as option cards, each asking the narrowed question."""
    out = [{"id": d["key"], "n": i + 1, "icon": d["icon"], "title": d["label"], "detail": d["detail"],
            "cta": "Ask about " + d["label"].lower(),
            "action": {"kind": "ask", "text": question_for(question, [d["key"]])}}
           for i, d in enumerate(DOMAINS)]
    out.append({"id": "all", "n": len(DOMAINS) + 1, "icon": "ph-squares-four", "title": "All of these",
                "detail": "Answer for every area, one section each", "cta": "Ask about all",
                "action": {"kind": "ask", "text": question_for(question, ["all"])}})
    return out
