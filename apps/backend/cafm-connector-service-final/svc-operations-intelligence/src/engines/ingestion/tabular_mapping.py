"""A spreadsheet's columns, mapped to the tables they belong in — energy, assets, maintenance.

CSV and Excel uploads go to the migration flow, which maps customer columns to canonical
fields and writes into whichever ``plenum_cafm`` table the mapping resolves. That registry
covered assets, work orders, parts, scheduled PM and users. It did not cover energy at all —
a half-hourly export or a supplier bill had no canonical target, so an MPAN column had nowhere
to go and the file became rows nobody could query.

This maps headers against the SAME catalogue the document extractor uses
(``document_extraction_rules.json``), for one reason: a field's destination must be declared
once. Two registries disagreeing about where ``serial_number`` lands is how a column silently
stops arriving, and nothing fails — the import succeeds, the rows are just thinner than they
were last month.

Three properties:

**A header is matched or it is unmapped.** Never guessed. An unmapped column is returned by
name so a person can add an alias, which is a five-second edit to the catalogue. A column
quietly dropped is a field the customer thinks they have imported.

**Matching is explainable.** Every match says HOW it matched — exact field name, a declared
alias, or a normalised form — and at what confidence. A mapping nobody can question is a
mapping nobody can correct.

**Ambiguity is refused, not resolved by luck.** "Date" appears as an alias under three
domains. When a header matches more than one field at the same strength the candidates are
returned and the header is left unmapped, because picking the first one in dictionary order is
a coin toss wearing a confidence score.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from .document_facts import load_rules

log = structlog.get_logger(__name__)

#: How a match was made, and what it is worth. An exact field name is the only certainty here.
CONF_EXACT = 0.99
CONF_ALIAS = 0.92
CONF_NORMALISED = 0.85
#: Below this a header is unmapped rather than mapped weakly. The migration flow writes what it
#: is given, so a wrong column is a wrong row, and a wrong row is worse than a missing one.
CONF_FLOOR = 0.85

_PUNCT = re.compile(r"[^a-z0-9]+")


def normalise(header: Any) -> str:
    """Lowercase, strip punctuation and collapse spacing: 'Asset Code #' -> 'asset code'."""
    return _PUNCT.sub(" ", str(header or "").lower()).strip()


def _squash(value: str) -> str:
    """Remove spacing entirely, so 'assetcode' and 'Asset Code' meet."""
    return _PUNCT.sub("", str(value or "").lower())


def _index() -> dict[str, list[dict[str, Any]]]:
    """Every way a field can be named, pointing back at where it lands.

    Built per call rather than cached at import: the catalogue is small, and a cached index
    that silently outlives an edit to the rules file is a debugging afternoon.
    """
    idx: dict[str, list[dict[str, Any]]] = {}

    def add(key: str, how: str, conf: float, domain: str, field: dict[str, Any]) -> None:
        k = _squash(key)
        if not k:
            return
        entry = {"domain": domain, "field": field["name"], "lands": field.get("lands"),
                 "type": field.get("type") or "text", "how": how, "confidence": conf,
                 "question": field.get("question")}
        # The strongest way of reaching a field wins; equal strength on the SAME field is the
        # same answer, not an ambiguity.
        for existing in idx.setdefault(k, []):
            if existing["domain"] == domain and existing["field"] == field["name"]:
                if conf > existing["confidence"]:
                    existing.update(entry)
                return
        idx[k].append(entry)

    for domain, d in (load_rules().get("domains") or {}).items():
        for f in d.get("fields") or []:
            add(f["name"], "field name", CONF_EXACT, domain, f)
            add(f["name"].replace("_", " "), "field name", CONF_EXACT, domain, f)
            for alias in f.get("aliases") or []:
                add(alias, "alias", CONF_ALIAS, domain, f)
    return idx


def map_headers(headers: list[Any], *, domains: list[str] | None = None) -> dict[str, Any]:
    """Each column of the sheet, and the table.column it is destined for.

    ``domains`` narrows the search when the uploader has already said what the file is — an
    asset register whose "Date" column should not be considered for a compliance issue date.
    """
    idx = _index()
    want = set(domains) if domains else None

    # Which domain this sheet is mostly about, decided by the headers that are NOT in doubt.
    # "Asset Code" means the asset's own code in a register and the asset a visit was against
    # in a PPM sheet — the same two words with two destinations. The sheet's own shape is the
    # evidence: if nine of twelve columns are asset fields, the tenth is too. Only unambiguous
    # headers vote, so a sheet of nothing but ambiguous columns resolves nothing and says so.
    lean: dict[str, int] = {}
    for h in headers or []:
        cands = [c for c in idx.get(_squash(str(h)), []) if want is None or c["domain"] in want]
        doms = {c["domain"] for c in cands}
        if len(doms) == 1:
            only = doms.pop()
            lean[only] = lean.get(only, 0) + 1

    mapped: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    unmapped: list[str] = []

    for h in headers or []:
        raw = str(h)
        cands = [c for c in idx.get(_squash(raw), [])
                 if want is None or c["domain"] in want]
        if not cands:
            # One more attempt on the normalised form, which also catches 'Asset_Code' and
            # 'ASSET CODE#'. Scored lower because it is a looser agreement than the literal.
            cands = [dict(c, how="normalised", confidence=min(c["confidence"], CONF_NORMALISED))
                     for c in idx.get(_squash(normalise(raw)), [])
                     if want is None or c["domain"] in want]
        if not cands:
            unmapped.append(raw)
            continue

        best = max(c["confidence"] for c in cands)
        top = [c for c in cands if c["confidence"] == best]
        if len(top) > 1 and lean:
            # Break the tie on what the rest of the sheet is about, but only when one domain
            # leads outright. A draw stays a draw.
            scored = sorted(top, key=lambda c: lean.get(c["domain"], 0), reverse=True)
            if lean.get(scored[0]["domain"], 0) > lean.get(scored[1]["domain"], 0):
                top = [dict(scored[0], how=scored[0]["how"] + " + sheet leans " + scored[0]["domain"])]
        if len(top) > 1:
            # "Date" is an alias under three domains. Choosing one by dictionary order is a
            # coin toss wearing a confidence score, so the header stays unmapped and says why.
            ambiguous.append({"header": raw, "candidates": [
                {"domain": c["domain"], "field": c["field"], "lands": c["lands"]} for c in top]})
            continue
        c = top[0]
        if c["confidence"] < CONF_FLOOR:
            unmapped.append(raw)
            continue
        mapped.append({"header": raw, **c})

    # Two source columns aimed at one target column is not a mapping, it is data loss: the
    # second write lands on top of the first and the import reports success. Split out rather
    # than written — an energy sheet carrying both MPAN and Meter Serial is the common case.
    seen: dict[str, list[dict[str, Any]]] = {}
    for m in mapped:
        if m["lands"]:
            seen.setdefault(m["lands"], []).append(m)
    collisions = [
        {"lands": lands, "headers": [x["header"] for x in xs],
         "why": "two columns of this sheet both target this one column; the later write would "
                "overwrite the earlier and the import would still report success"}
        for lands, xs in seen.items() if len(xs) > 1
    ]
    collided = {lands for lands, xs in seen.items() if len(xs) > 1}
    mapped = [m for m in mapped if not m["lands"] or m["lands"] not in collided]

    # A field with no destination is extracted and shown, never written. It is kept out of the
    # table plan and named, because a figure with nowhere to live is a gap worth seeing.
    no_target = [{"header": m["header"], "domain": m["domain"], "field": m["field"],
                  "why": "this field has no column on this schema"}
                 for m in mapped if not m["lands"]]

    by_table: dict[str, list[dict[str, Any]]] = {}
    for m in mapped:
        if m["lands"]:
            by_table.setdefault(m["lands"].split(".")[0], []).append(m)

    return {
        "ok": True,
        "headers": len(headers or []),
        "mapped": mapped,
        "ambiguous": ambiguous,
        "unmapped": unmapped,
        "collisions": collisions,
        "no_target": no_target,
        "coverage_pct": round(100.0 * len(mapped) / max(1, len(headers or [])), 1),
        "tables": [
            {"table": t,
             "columns": [{"column": m["lands"].split(".")[1], "from_header": m["header"],
                          "domain": m["domain"], "field": m["field"],
                          "confidence": m["confidence"], "how": m["how"]} for m in ms],
             "domains": sorted({m["domain"] for m in ms})}
            for t, ms in sorted(by_table.items())
        ],
        "domains_covered": sorted({m["domain"] for m in mapped}),
        # The honest headline. A sheet where half the columns did not match is not a
        # successful import with some gaps; it is a mapping that needs aliases first.
        "note": (f"{len(mapped)} of {len(headers or [])} columns mapped"
                 + (f", {len(ambiguous)} ambiguous" if ambiguous else "")
                 + (f", {len(unmapped)} unmapped" if unmapped else "")
                 + (f", {len(collisions)} target collisions held back" if collisions else "")
                 + (f", {len(no_target)} with no column to write to" if no_target else "")
                 + ". Unmapped columns are reported by name rather than guessed into a "
                   "column; adding an alias to document_extraction_rules.json is the fix."),
    }


def import_plan(headers: list[Any], *, row_count: int | None = None,
                domains: list[str] | None = None) -> dict[str, Any]:
    """What this sheet would write, per table, before it writes anything.

    The counterpart to the document extraction plan: cheap, model-free, and the thing to show
    someone before an import rather than after.
    """
    m = map_headers(headers, domains=domains)
    writable = [t for t in m["tables"]]
    return {
        **m,
        "row_count": row_count,
        "would_write": [
            {"table": t["table"], "columns": [c["column"] for c in t["columns"]],
             "rows": row_count, "domains": t["domains"]}
            for t in writable
        ],
        "blocked": (
            [{"header": a["header"], "why": "matches more than one field equally",
              "candidates": a["candidates"]} for a in m["ambiguous"]]
            + [{"header": " + ".join(c["headers"]), "why": c["why"], "lands": c["lands"]}
               for c in m["collisions"]]
        ),
    }
