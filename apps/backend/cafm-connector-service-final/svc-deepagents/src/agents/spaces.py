"""Which domains a role space may answer from.

A customer works inside one space — Ops Brain, the Compliance Engine, the Assets Operating
System, or the full platform — and each shows only its own reports in the navigator. The chat
did not know that: the active space is declared in the FRONTEND (logic/instances.js) and never
reached the orchestrator, so a question asked in Ops Brain was routed across every engine and
compliance certificates came back from a space that does not have compliance (30 Oct 2026).

The space's `reports` list is the module, because that is what the person is looking at. Ops
Brain also names compliance in its VALUE ledger, which is a headline figure on the home page and
not a report anybody can open; a figure in a card is not permission to read the records behind
it.

Only the four DOMAIN engines are gated. Ingestion, migration and document search are not a
domain — every space ingests its own files and reads its own documents — so they are left alone.
Document search can still reach a document from another module; scoping the corpus is a separate
piece of work and is noted rather than half-done here.
"""
from __future__ import annotations

#: The report each domain engine serves, as logic/instances.js names reports.
_ENGINE_REPORT = {
    "wo_engine": "ops",
    "contract_performance": "vendors",
    "compliance": "compliance",
    "energy_intelligence": "energy",
}

#: Each space's reports, from logic/instances.js. `full` is every report there is.
SPACE_REPORTS: dict[str, frozenset[str]] = {
    "ops": frozenset({"buildings", "ops", "vendors"}),
    "comp": frozenset({"buildings", "graph", "compliance", "vendors"}),
    "assets": frozenset({"buildings", "energy", "assets", "ops", "vendors"}),
    "full": frozenset({"buildings", "graph", "compliance", "vendors", "ops", "energy", "assets"}),
}

#: What the space is called when the answer has to name it.
SPACE_NAMES: dict[str, str] = {
    "ops": "Ops Brain",
    "comp": "the Compliance Engine",
    "assets": "the Assets Operating System",
    "full": "the full platform",
}

#: What a domain is called in a sentence.
DOMAIN_NAMES: dict[str, str] = {
    "wo_engine": "work orders and maintenance",
    "contract_performance": "vendor performance",
    "compliance": "compliance",
    "energy_intelligence": "energy",
}


def engines_for(space: str | None) -> frozenset[str] | None:
    """The domain engines this space may use, or None when every engine is allowed.

    None — not one of the known spaces, or no space at all — means no restriction. A space this
    module has not heard of must not silently lose its engines.
    """
    key = str(space or "").strip().lower()
    reports = SPACE_REPORTS.get(key)
    if reports is None:
        return None
    return frozenset(e for e, r in _ENGINE_REPORT.items() if r in reports)


def allows(space: str | None, engine: str | None) -> bool:
    """True when this space may answer from this engine.

    An engine that is not a domain engine — migration, udr, doc_rag, the orchestrator loop — is
    allowed everywhere: ingesting a file or reading a document is not a module.
    """
    if not engine or engine not in _ENGINE_REPORT:
        return True
    allowed = engines_for(space)
    return allowed is None or engine in allowed


def space_with(engine: str | None) -> str | None:
    """The space key a person should switch to for this engine, or None if several have it.

    Named only when the answer is unambiguous: suggesting one of three is worse than suggesting
    none, and the full platform has everything so it never counts as the place to go.
    """
    if engine not in _ENGINE_REPORT:
        return None
    having = [k for k in SPACE_REPORTS if k != "full" and engine in (engines_for(k) or ())]
    return having[0] if len(having) == 1 else None


def refusal(space: str | None, engine: str | None) -> str | None:
    """What to say when the question belongs to a module this space does not have.

    Says which space the person is in, what the question needs, and — when exactly one space has
    it — where to go. It never offers to answer anyway: the point of the space is that its data
    is its own.
    """
    if allows(space, engine):
        return None
    here = SPACE_NAMES.get(str(space or "").strip().lower(), "this space")
    domain = DOMAIN_NAMES.get(engine or "", engine or "that")
    elsewhere = space_with(engine)
    where = (" It is part of " + SPACE_NAMES[elsewhere] + ", which you can switch to from the "
             "top bar.") if elsewhere else ""
    return ("That question is about " + domain + ", which is not part of " + here + "."
            + where)
