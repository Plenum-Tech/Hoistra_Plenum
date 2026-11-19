"""The answer key (16 Nov 2026, Hussain and Bala): expected answers whose values are read from the records, each value
with its receipt (the query, its parameters and the rows it read) so anyone can check it, in Trace Lab or in psql.

    live question --match--> a key (work orders, certificates, vendors) with its parameters
    --> a FIXED plan in Hoistra's own terms --> Hoistra's records engine resolves it and compiles the SQL
    --> read-only, company-scoped read through plenum_scoped --> values (facts), each with its receipt

The plan is written here, never by a model, and compiled by svc-deepagents' ontology_qa - the engine the orchestrator's
record tool uses - so "open", "overdue", "lapsed" and "blocked" mean exactly what they mean to Hoistra. What can still
differ from the live answer is what the orchestrator's model chose to ask: that is what this checks.

Nothing in the orchestrator changes: its module is imported, never edited; the read runs in Trace Lab's own process on
its read-only main engine (src.database is Trace Lab's read-only engine here, db.install_main_engine), inside one
READ ONLY transaction with a statement timeout. The engine's map of the database (the ontology) is only LOADED from the
file the orchestrator built: when there is none, the key is skipped, never built here (a build is ~15 minutes of reads).
TRACE_LAB_ANSWER_KEY=false turns it off.
"""
from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from . import records_run

ENV_SWITCH = "TRACE_LAB_ANSWER_KEY"
#: Rows kept with each receipt; the full count is kept too.
ROWS_KEPT = 50
#: Values listed per answer (the count always comes first).
ITEMS_SHOWN = 20
SCOPED_SCHEMA = "plenum_scoped"


#: The faithful re-run of Hoistra's records query lives in records_run; these keep their old names here.
NotReady = records_run.NotReady


def enabled() -> bool:
    return (os.environ.get(ENV_SWITCH) or "true").strip().lower() not in ("false", "0", "no", "off")


# ── matching a question to a key ───────────────────────────────────────────────────────────────────────────────────

_WO = re.compile(r"\bwork[\s-]?orders?\b|\bwos?\b")
_DELAYED = re.compile(r"\bdelay|\boverdue\b|\blate\b|past (?:their |the )?sla|\bbehind\b")
_MOST = re.compile(r"\b(?:most|max\w*|highest|largest|greatest)\b")
_WHICH_BUILDING = re.compile(r"\bwhich (?:building|site)s?\b")
_WO_STATE = re.compile(r"\b(?:open|overdue|delayed|late|outstanding|pending)\b")
_CERT = re.compile(r"\bcertificat")
_CERT_STATES = (("lapsed", re.compile(r"\blapsed\b|\bexpired\b")), ("expiring_soon", re.compile(r"\bexpiring\b|\babout to expire\b")),
                ("due_for_renewal", re.compile(r"\bdue for renewal\b|\brenewal\b")), ("overdue", re.compile(r"\boverdue\b")))
# A certificate that stops being valid, in any of the words people use (R59 asked "lapse in the next 30 days").
_EXPIRE = re.compile(r"\bexpir|\blaps(?:e|es|ing)\b|\bruns?\s+out\b|\brunning\s+out\b|\bend(?:s|ing)?\b|\bdue\b")
_WINDOW = re.compile(r"\b(?:in|within)\s+(?:the\s+)?(?:next\s+)?(\d{1,4})\s*(day|week|month)s?\b"
                     r"|\bnext\s+(\d{1,4})\s*(day|week|month)s?\b")
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}


def _window(q: str) -> str | None:
    m = _WINDOW.search(q)
    if m:
        n, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        days = int(n) * _UNIT_DAYS[unit]
        return f"next_{days}_days" if 0 < days <= 3660 else None
    if re.search(r"\bthis month\b", q):
        return "this_month"
    if re.search(r"\bnext month\b", q):
        return "next_month"
    return None


def window_words(w: str) -> str:
    m = re.match(r"^next_(\d+)_days$", w or "")
    return f"within the next {m.group(1)} days" if m else str(w or "").replace("_", " ")


_VENDOR = re.compile(r"\b(?:vendors?|contractors?|suppliers?)\b")
_BLOCKED = re.compile(r"\bblocked\b|\bnot eligible\b|\bineligible\b|\bcan(?:no|')t work\b|\bsuspended\b|\bnot allowed\b")
_PLACE = re.compile(r"\b(?:at|in|for)\s+(?:the\s+)?([A-Za-z0-9][\w&'.\- ]*?)\s*(?:[?.!,]|$|\bright now\b|\btoday\b|\bthis\b)")
_NOT_A_PLACE = re.compile(r"^(?:next|last|past|coming|this|today|now|renewal|my|all|each|every|it|them|total|"
                          r"the (?:next|last|past)|\d|"
                          # words that follow "in"/"for" without naming a building (16 Nov 2026 review)
                          r"progress|the moment|moment|place|time|hvac|plumbing|electrical|mechanical|fire|lifts?|"
                          r"maintenance|repair|review|scope|risk|use|general)\b", re.I)


def _place(question: str) -> str | None:
    for m in _PLACE.finditer(question):
        name = m.group(1).strip()
        if name and not _NOT_A_PLACE.match(name):
            return name
    return None


@dataclass(frozen=True)
class Key:
    id: str
    title: str
    plan: Callable[[dict], dict]
    facts: Callable[[dict, dict, list], list]


def _node(concept: str, match: Any = None, states: list | None = None, *, via: bool = False) -> dict:
    out = {"concept": concept, "match": match, "states": list(states or []), "filters": []}
    if via:
        out["via"] = []
    return out


def _receipt(receipts: list, *labels: str) -> str | None:
    for label in labels:
        for r in reversed(receipts):
            if str(r.get("label") or "").lower().startswith(label):
                return r["id"]
    return receipts[-1]["id"] if receipts else None


def _grouped(params: dict, result: dict, receipts: list, empty: str = "No building on record") -> list:
    rid = _receipt(receipts, "aggregate")
    rows = sorted(result.get("rows") or [], key=lambda r: -(int(r.get("count") or 0)))
    out = []
    for i, row in enumerate(rows[:ITEMS_SHOWN], start=1):
        label = " · ".join(str(v) for k, v in row.items() if k != "count" and v not in (None, "")) or empty
        out.append({"id": "f" + str(i), "label": label, "value": int(row.get("count") or 0), "receipt": rid})
    return out


def _listed(total_label: Callable[[dict], str], item: Callable[[dict], str]):
    def facts(params: dict, result: dict, receipts: list) -> list:
        total = int(result.get("total") or 0)
        out = [{"id": "f1", "label": total_label(params), "value": total, "receipt": _receipt(receipts, "count", "focus")}]
        rid = _receipt(receipts, "focus")
        for i, rec in enumerate((result.get("records") or [])[:ITEMS_SHOWN], start=2):
            out.append({"id": "f" + str(i), "label": "", "value": item(rec.get("attributes") or {}), "receipt": rid})
        return out
    return facts


def _wo_states_text(states: list) -> str:
    return "open and overdue" if "overdue" in states else "open"


_CERT_WORDS = {"lapsed": "lapsed", "expiring_soon": "expiring soon", "due_for_renewal": "due for renewal",
               "overdue": "overdue"}


def _wo_item(a: dict) -> str:
    return " — ".join(str(x) for x in (a.get("wo_code"), a.get("title")) if x) or str(a.get("id") or "work order")


def _cert_item(a: dict) -> str:
    kind = a.get("cert_type") or a.get("certificate_type_code") or a.get("category") or "Certificate"
    ref = a.get("certificate_ref") or a.get("certificate_number") or ""
    exp = (" (expiry " + str(a["expiry_date"])[:10] + ")") if a.get("expiry_date") else ""
    return (str(kind) + (" " + str(ref) if ref else "") + exp).strip()


def _vendor_item(a: dict) -> str:
    return str(a.get("vendor_name") or a.get("vendor_code") or a.get("id") or "vendor")


KEYS: dict[str, Key] = {
    "wo_most_by_building": Key(
        "wo_most_by_building", "Work orders by building",
        lambda p: {"question_type": "count", "focus": _node("WorkOrder", None, p["states"]),
                   "constraints": [_node("Building", via=True)], "include": [],
                   "aggregate": {"count_by": [{"concept": "Building", "attribute": "name"}]}},
        _grouped),
    "wo_at_building": Key(
        "wo_at_building", "Work orders at a building",
        lambda p: {"question_type": "list", "focus": _node("WorkOrder", None, p["states"]),
                   "constraints": [_node("Building", p["building"], via=True)], "include": [], "aggregate": None},
        _listed(lambda p: _wo_states_text(p["states"]).capitalize() + " work orders at " + p["building"], _wo_item)),
    "certificates": Key(
        "certificates", "Certificates by state",
        lambda p: {"question_type": "list", "focus": _node("ComplianceCertificate", None, p["states"]),
                   "constraints": [_node("Building", p["building"], via=True)] if p.get("building") else [],
                   "include": [], "aggregate": None},
        _listed(lambda p: _CERT_WORDS[p["states"][0]].capitalize() + " certificates"
                + (" at " + p["building"] if p.get("building") else ""), _cert_item)),
    "certificates_window": Key(
        "certificates_window", "Certificates expiring within a period",
        lambda p: {"question_type": "list",
                   "focus": {"concept": "ComplianceCertificate", "match": None, "states": [],
                             "filters": [{"attribute": "expiry_date", "op": "within", "value": p["window"]}]},
                   "constraints": [_node("Building", p["building"], via=True)] if p.get("building") else [],
                   "include": [], "aggregate": None},
        _listed(lambda p: "Certificates expiring " + window_words(p["window"])
                + (" at " + p["building"] if p.get("building") else ""), _cert_item)),
    "vendors_blocked": Key(
        "vendors_blocked", "Blocked vendors",
        lambda p: {"question_type": "list", "focus": _node("Vendor", None, ["blocked"]), "constraints": [],
                   "include": [], "aggregate": None},
        _listed(lambda p: "Blocked vendors", _vendor_item)),
}


def match(question: str | None) -> tuple[Key, dict] | None:
    """The key that answers this question and its parameters, or None (then the SPEC.md draft is used)."""
    raw = (question or "").strip()
    q = raw.lower()
    if not q:
        return None
    if _VENDOR.search(q) and _BLOCKED.search(q) and not _WO.search(q):
        return KEYS["vendors_blocked"], {}
    if _WO.search(q):
        states = ["open", "overdue"] if _DELAYED.search(q) else ["open"]
        if _WHICH_BUILDING.search(q) and _MOST.search(q):
            return KEYS["wo_most_by_building"], {"states": states}
        building = _place(raw)
        if building and _WO_STATE.search(q):
            return KEYS["wo_at_building"], {"states": states, "building": building}
        return None
    if _CERT.search(q):
        window = _window(q) if _EXPIRE.search(q) else None
        if window:
            return KEYS["certificates_window"], {"window": window, "building": _place(raw)}
        for state, rx in _CERT_STATES:
            if rx.search(q):
                cleaned = re.sub(r"\bdue for renewal\b", "", raw, flags=re.I)
                return KEYS["certificates"], {"states": [state], "building": _place(cleaned)}
    return None


def sentence(key: Key, params: dict, facts: list) -> str:
    """The expected answer in one line, from the values."""
    if not facts:
        return "Nothing was found in the records."
    if key.id == "wo_most_by_building":
        top, rest = facts[0], facts[1:4]
        out = f"{top['label']} has the most {_wo_states_text(params['states'])} work orders: {top['value']}."
        if rest:
            out += " Next: " + ", ".join(f"{f['label']} {f['value']}" for f in rest) + "."
        return out
    total, items = facts[0]["value"], [str(f["value"]) for f in facts[1:]]
    if key.id == "certificates_window":
        when_ = window_words(params["window"]) + (" at " + params["building"] if params.get("building") else "")
        if total == 0:
            return f"No certificates expire {when_}."
        more = "" if total <= len(items) else f", and {total - len(items)} more"
        head = f"1 certificate expires {when_}" if total == 1 else f"{total} certificates expire {when_}"
        return head + (": " + ", ".join(items) + more if items else "") + "."
    if key.id == "vendors_blocked":
        noun, state, where = "vendors", "blocked", ""
    elif key.id == "certificates":
        noun, state, where = "certificates", _CERT_WORDS[params["states"][0]], (
            " at " + params["building"] if params.get("building") else "")
    else:
        noun, state, where = "work orders", "", " at " + params["building"]
    if total == 0:
        if key.id == "wo_at_building":
            return f"No {_wo_states_text(params['states'])} work orders at {params['building']}."
        return f"No {noun} are {state}{where}."
    one = noun[:-1] if total == 1 else noun
    if key.id == "wo_at_building":
        head = f"{total} {_wo_states_text(params['states'])} {one} at {params['building']}"
    else:
        head = f"{total} {one} {'is' if total == 1 else 'are'} {state}{where}"
    more = "" if total <= len(items) else f", and {total - len(items)} more"
    return head + (": " + ", ".join(items) + more if items else "") + "."


# ── reading it ────────────────────────────────────────────────────────────────────────────────────────────────────

def jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (set, frozenset)):
        return sorted(jsonable(x) for x in v)
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return v


def _literal(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "ARRAY[" + ", ".join(_literal(x) for x in v) + "]"
    return "'" + str(v).replace("'", "''") + "'"


def psql_text(receipt: dict, *, org: str, buildings: str = "") -> str:
    """The query as it ran, every value filled in, after the company scope plenum_scoped's views read - paste it into
    psql (any read-only session) to check the value yourself. `buildings` is the comma-joined building ids the read was
    limited to (what app.udr_buildings held); empty is the whole company."""
    params = receipt.get("params") or {}
    sql = re.sub(r":(p\d+)\b", lambda m: _literal(params.get(m.group(1))) if m.group(1) in params else m.group(0),
                 str(receipt.get("sql") or ""))
    head = ["BEGIN READ ONLY;",
            "SELECT set_config('app.udr_unrestricted', '0', false);",
            f"SELECT set_config('app.udr_org', {_literal(str(org))}, false);",
            f"SELECT set_config('app.udr_buildings', {_literal(str(buildings or ''))}, false);",
            f"SET search_path TO {SCOPED_SCHEMA}, public;"]
    return "\n".join(head) + "\n" + sql.strip().rstrip(";") + ";\nROLLBACK;"


#: Hoistra's map of the database, loaded from the file the orchestrator built (moved to records_run).
_ontology = records_run.load_map


async def run(key: Key, params: dict, org: str) -> dict:
    """Read the key's values for one company. Returns found, facts, receipts (each with its psql text), read_at,
    plan, and unresolved (names the records do not hold) when found is false."""
    return await _run_plan(key.plan(params), org, lambda onto, plan, result, receipts: key.facts(params, result, receipts))


async def _run_plan(raw_plan: dict, org: str, facts_of: Callable) -> dict:
    """One plan, compiled by Hoistra's records engine and read for one company, read-only, every query recorded."""
    from . import db

    if db._main is None:
        raise NotReady("Trace Lab's read-only connection to the records is not installed")
    from src.agents import ontology_qa as oq

    onto = await _ontology(oq)
    plan = oq.normalise_plan(raw_plan)
    errors = oq.validate_plan(onto, plan)
    if errors:
        raise ValueError("the plan does not fit this database: " + "; ".join(errors))
    caller = SimpleNamespace(role="admin", organization_id=org, building_ids=None)
    async with records_run.ReceiptReader(caller) as r:
        plan, _notes, unresolved = await oq.resolve_plan(r, onto, plan, question=None)
        read_at = datetime.now(timezone.utc).isoformat()
        if unresolved:
            return {"found": False, "unresolved": list(unresolved), "facts": [], "receipts": r.receipts,
                    "read_at": read_at, "plan": jsonable(plan)}
        result = await oq.execute_plan(r, onto, plan)
        facts = facts_of(onto, plan, result, r.receipts)
        return {"found": True, "facts": facts, "receipts": r.receipts, "read_at": read_at, "plan": jsonable(plan)}


async def recheck(m: Any, turn_id: str) -> dict:
    """Run again now (the receipt panel): the turn's answer-key values read afresh, for the same company, with the same
    key and parameters. Nothing is stored; the page shows them beside the values read when the question was asked."""
    from . import runs
    from .review_errors import Invalid

    got = await runs.turn_expected(m, turn_id)        # bounded to the caller's company; NotFound otherwise
    ev = ((got.get("expected") or {}).get("evidence") or {}) if got.get("kind") == "answer_key" else {}
    key = KEYS.get(str(ev.get("key") or ""))
    if key is None or not got.get("company"):
        raise Invalid("no_answer_key", "This expected answer was not read from the records.")
    params = dict(ev.get("params") or {})
    try:
        out = await run(key, params, got["company"])
    except NotReady as exc:
        raise Invalid("not_ready", "Hoistra's map of the records is not ready yet. Try again in a few minutes.") from exc
    except (KeyError, ValueError, TypeError) as exc:
        raise Invalid("no_answer_key", "This expected answer cannot be read again: " + str(exc)[:200]) from exc
    return {"found": out["found"], "facts": out["facts"], "receipts": out["receipts"], "read_at": out["read_at"],
            "unresolved": out.get("unresolved") or [],
            "sentence": sentence(key, params, out["facts"]) if out["found"] else ""}


# ── correcting the orchestrator's own query (16 Nov 2026, Hussain) ─────────────────────────────────────────────────

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_DATE_COL = re.compile(r"date|_at$|expir|\bdue\b|_due")
_NOT_DATE = ("created_at", "updated_at")
DATE_OPS = ("within", "between", "lt", "lte", "gt", "gte")
_PERIODS = {"today", "yesterday", "this_week", "last_week", "this_month", "last_month", "next_month", "this_quarter",
            "last_quarter", "this_year", "last_year", "last_7_days", "last_30_days", "last_90_days", "year_to_date"}
_ROLLING = re.compile(r"^(next|last|past|previous|coming)_(\d{1,4})_(day|week|month)s?$")
_DAYS = {"day": 1, "week": 7, "month": 30}
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RELATIVE = re.compile(r"^today(?:\s*[+-]\s*\d{1,4}\s*[dwmy])?$", re.I)


def extract_plans(nodes: list | None) -> list[dict]:
    """The records plans the orchestrator's records tool made in this turn (its planner's JSON), first to last, once each."""
    out, seen = [], set()
    for n in nodes or []:
        o = (n or {}).get("output")
        txt = o.get("content") if isinstance(o, dict) else o if isinstance(o, str) else None
        if not isinstance(txt, str) or "focus" not in txt:
            continue
        try:
            d = json.loads(_FENCE.sub("", txt.strip()))
        except ValueError:
            continue
        if isinstance(d, dict) and isinstance(d.get("focus"), dict) and d.get("question_type"):
            k = json.dumps(d, sort_keys=True, default=str)
            if k not in seen:
                seen.add(k)
                out.append(d)
    return out


def _words(concept: str, n: int | None = None) -> str:
    w = re.sub(r"(?<!^)(?=[A-Z])", " ", str(concept or "record")).lower()
    if n == 1:
        return w
    return w[:-1] + "ies" if w.endswith("y") and not w.endswith("ey") else w if w.endswith("s") else w + "s"


def _date_attributes(c: Any) -> list[str]:
    return [a for a in c.attributes if _DATE_COL.search(a) and a not in _NOT_DATE]


def plan_fields(onto: Any, plan: dict) -> dict:
    """The orchestrator's plan as plain fields a person can change: what was read, its states, the building, the dates."""
    focus = plan.get("focus") or {}
    c = onto.concepts[focus.get("concept")]
    dates = _date_attributes(c)
    building = next((x.get("match") for x in plan.get("constraints") or [] if x.get("concept") == "Building"), None)
    return {"concept": c.name, "what": _words(c.name), "states": list(focus.get("states") or []),
            "state_choices": [{"id": k, "description": str((v or {}).get("description") or "")[:160]} for k, v in c.states.items()],
            "building": building,
            "dates": [{"attribute": f.get("attribute"), "op": f.get("op"), "value": f.get("value")}
                      for f in focus.get("filters") or [] if f.get("attribute") in dates and f.get("op") in DATE_OPS],
            "date_attributes": dates}


def _valid_date(v: Any) -> bool:
    if not isinstance(v, str):
        return False
    if _RELATIVE.match(v.strip()):
        return True
    if not _ISO.match(v):
        return False
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


def _check_date(d: dict, allowed: list[str]) -> dict:
    attr, op, value = d.get("attribute"), d.get("op"), d.get("value")
    if attr not in allowed:
        raise ValueError(f"{attr} is not a date of these records")
    if op not in DATE_OPS:
        raise ValueError(f"{op} is not a way to compare dates")
    if op == "within":
        m = _ROLLING.match(value) if isinstance(value, str) else None
        if not (isinstance(value, str) and (value in _PERIODS or (m and 1 <= int(m.group(2)) * _DAYS[m.group(3)] <= 3660))):
            raise ValueError(f"{value} is not a period")
    elif op == "between":
        if not (isinstance(value, list) and len(value) == 2 and all(_valid_date(x) for x in value)):
            raise ValueError("a range needs two dates")
    elif not _valid_date(value):
        raise ValueError(f"{value} is not a date")
    return {"attribute": attr, "op": op, "value": value}


def _date_text(d: dict) -> str:
    v = d.get("value")
    return f"{d.get('op')} " + (" to ".join(v) if isinstance(v, list) else str(v))


def apply_fields(onto: Any, plan: dict, fields: dict) -> tuple[dict, list[str]]:
    """A copy of the orchestrator's plan with a person's changes, and what changed in words. ValueError when a change
    is not one the records allow (an unknown state, a column that is not a date, a malformed date or period)."""
    new = copy.deepcopy(plan)
    focus = new.setdefault("focus", {})
    c = onto.concepts[focus.get("concept")]
    before = plan_fields(onto, plan)
    changes: list[str] = []
    states = [str(s) for s in (fields.get("states") or [])]
    for s in states:
        if s not in c.states:
            raise ValueError(f"{s} is not a state of {_words(c.name)}")
    if states != before["states"]:
        changes.append("States: " + (", ".join(before["states"]) or "any") + " → " + (", ".join(states) or "any"))
    focus["states"] = states
    building = str(fields.get("building") or "").strip() or None
    # Every building is the Building constraint with no match, kept when there was one: a count by building needs it.
    cons = new.setdefault("constraints", [])
    held = next((x for x in cons if x.get("concept") == "Building"), None)
    if held is not None:
        held["match"] = building
    elif building:
        cons.insert(0, {"concept": "Building", "match": building, "via": [], "states": [], "filters": []})
    if building != before["building"]:
        changes.append("Building: " + (before["building"] or "every building") + " → " + (building or "every building"))
    allowed = _date_attributes(c)
    dates = [_check_date(d, allowed) for d in fields.get("dates") or []]
    keep = [f for f in focus.get("filters") or [] if not (f.get("attribute") in allowed and f.get("op") in DATE_OPS)]
    focus["filters"] = keep + dates
    old = {d["attribute"]: d for d in before["dates"]}
    now = {d["attribute"]: d for d in dates}
    for attr in dict.fromkeys(list(old) + list(now)):
        a, b2 = old.get(attr), now.get(attr)
        if a != b2:
            changes.append(f"{attr}: " + (_date_text(a) if a else "any date") + " → " + (_date_text(b2) if b2 else "any date"))
    return new, changes


def _generic_item(c: Any, attrs: dict) -> str:
    for col in [*(getattr(c, "identifiers", None) or []), getattr(c, "label", None), *(getattr(c, "search", None) or []), "name", "title", "id"]:
        v = attrs.get(col)
        if v not in (None, ""):
            return str(v)
    return "record"


def generic_facts(onto: Any, plan: dict, result: dict, receipts: list) -> list:
    """Any records answer as values, each with its receipt: the count by group, or the count and the records."""
    if result.get("kind") == "aggregate":
        return _grouped({}, result, receipts)
    c = onto.concepts[(plan.get("focus") or {}).get("concept")]
    total = int(result.get("total") or 0)
    out = [{"id": "f1", "label": _words(c.name).capitalize() + " found", "value": total, "receipt": _receipt(receipts, "count", "focus")}]
    rid = _receipt(receipts, "focus")
    for i, rec in enumerate((result.get("records") or [])[:ITEMS_SHOWN], start=2):
        out.append({"id": "f" + str(i), "label": "", "value": _generic_item(c, rec.get("attributes") or {}), "receipt": rid})
    return out


def generic_sentence(onto: Any, plan: dict, facts: list) -> str:
    concept = (plan.get("focus") or {}).get("concept")
    if facts and facts[0].get("label") and not str(facts[0]["label"]).endswith(" found"):
        return "Counts: " + ", ".join(f"{f['label']} {f['value']}" for f in facts[:10]) + "."
    total = int(facts[0]["value"]) if facts else 0
    if total == 0:
        return f"No {_words(concept)}."
    items = [str(f["value"]) for f in facts[1:]]
    more = "" if total <= len(items) else f", and {total - len(items)} more"
    return f"{total} {_words(concept, total)}" + (": " + ", ".join(items) + more if items else "") + "."


def used_first(plans: list) -> list:
    """The orchestrator's records plans, the one its answer came from first: a plan it retried is replaced by the next,
    so the last one written is the one that ran (16 Nov 2026 review)."""
    return list(reversed(plans or []))


async def _turn_plans(m: Any, turn_id: str) -> tuple[dict, list[dict], str | None]:
    from . import eval_store, runs
    from .review_errors import Invalid

    got = await runs.turn_expected(m, turn_id)          # bounded to the caller's company; NotFound otherwise
    if not got.get("live") or not got.get("company"):
        raise Invalid("not_live", "Only a live question's query can be checked here.")
    tree = await eval_store.trace_tree_of(m, turn_id)
    plans = used_first(extract_plans(tree.get("nodes") or []))
    if plans:
        return got, plans, "hoistra"
    # Hoistra answered with another tool (16 Nov 2026, R59): the answer key's query for the same question is the start.
    found = match(got.get("text"))
    return (got, [found[0].plan(found[1])], "answer_key") if found else (got, [], None)


async def orchestrator_query(m: Any, turn_id: str) -> dict:
    """The records queries the orchestrator made for this turn, as plain fields (GET .../expected/query)."""
    from . import db

    got, plans, source = await _turn_plans(m, turn_id)
    if not plans:
        return {"ready": True, "plans": [], "source": None}
    if db._main is None:
        return {"ready": False, "plans": []}
    from src.agents import ontology_qa as oq

    try:
        onto = await _ontology(oq)
    except NotReady:
        return {"ready": False, "plans": []}
    return {"ready": True, "source": source, "buildings": await _buildings(got["company"]),
            "plans": [{"index": i, "fields": plan_fields(onto, p)} for i, p in enumerate(plans)
                      if (p.get("focus") or {}).get("concept") in onto.concepts]}


async def _buildings(org: str) -> list[str]:
    """The company's building names, for the Building field to suggest (read-only, the company's own)."""
    try:
        from src.agents import ontology_qa as oq

        async with oq.ScopedReader(SimpleNamespace(role="admin", organization_id=org, building_ids=None)) as r:
            rows = await r.fetch("SELECT DISTINCT name FROM buildings WHERE name IS NOT NULL ORDER BY name LIMIT 500", {},
                                 "building names")
        return [str(x["name"]) for x in rows]
    except Exception:  # noqa: BLE001 - suggestions are a convenience; the field still takes any name
        return []


async def try_query(m: Any, turn_id: str, *, index: int, fields: dict) -> dict:
    """The orchestrator's query with a person's changes, read afresh for the same company: values, receipts, the
    sentence and what changed. Nothing is stored; the page saves it as the expected answer when the person says so."""
    from .review_errors import Invalid

    got, plans, _source = await _turn_plans(m, turn_id)
    if not 0 <= int(index) < len(plans):
        raise Invalid("no_query", "That query is not in this turn.")
    from src.agents import ontology_qa as oq

    try:
        onto = await _ontology(oq)
    except NotReady as exc:
        raise Invalid("not_ready", "Hoistra's map of the records is not ready yet. Try again in a few minutes.") from exc
    original = plans[int(index)]
    try:
        plan, changes = apply_fields(onto, original, fields)
    except (ValueError, KeyError, TypeError) as exc:
        raise Invalid("bad_change", str(exc)) from exc
    # What the starting query found, beside what the changed one finds (16 Nov 2026: "very user friendly").
    try:
        was = await _run_plan(original, got["company"], generic_facts)
        before = {"found": was["found"], "facts": was["facts"],
                  "sentence": generic_sentence(onto, original, was["facts"]) if was["found"] else ""}
    except Exception:  # noqa: BLE001 - the comparison is a help; the corrected answer still stands
        before = None
    try:
        out = await _run_plan(plan, got["company"], generic_facts)
    except NotReady as exc:
        raise Invalid("not_ready", "Hoistra's map of the records is not ready yet. Try again in a few minutes.") from exc
    except Exception as exc:  # noqa: BLE001 - a query the records engine refuses is the person's change, not a crash
        raise Invalid("bad_query", "The records could not be read with this query: " + str(exc)[:200]) from exc
    sentence_ = generic_sentence(onto, plan, out["facts"]) if out["found"] else ""
    return {**out, "sentence": sentence_, "changes": changes, "original": jsonable(original), "before": before}
