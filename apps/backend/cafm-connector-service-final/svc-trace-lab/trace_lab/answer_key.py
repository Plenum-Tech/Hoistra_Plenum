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

Since 19 Nov 2026 every read here goes through records_run.run_query, the faithful re-run of Hoistra's records tool, and
this module is also where the routes of a turn's query meet it: Run again now (recheck), the query editor's start
(orchestrator_query) and a person's own plan run beside Hoistra's (try_query; records_form says what may be chosen,
whether it fits and what changed).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from src.agents import ontology_qa as oq

from . import records_run

ENV_SWITCH = "TRACE_LAB_ANSWER_KEY"
#: Rows kept with each receipt; the full count is kept too.
ROWS_KEPT = 50
#: Values listed per answer (the count always comes first).
ITEMS_SHOWN = 20
SCOPED_SCHEMA = "plenum_scoped"
log = structlog.get_logger(__name__)


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
    """A value safe to store as JSON: dates as ISO text, numbers as numbers, and anything JSON does not know (an object of
    the records engine, say) as its text, never an error when a draft is stored."""
    if v is None or isinstance(v, (str, bool, int, float)):
        return v
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
    return str(v)


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


async def run(key: Key, params: dict, org: str, *, question: str = "") -> dict:
    """Read the key's values for one company, the way Hoistra's records tool reads a plan (records_run.run_query, with the
    question as asked). Returns found, facts, receipts (each with its psql text), read_at, plan (as run), notes (what
    Hoistra's steps said), and unresolved (names the records do not hold) when found is false."""
    return await _run_plan(key.plan(params), org, lambda onto, plan, result, receipts: key.facts(params, result, receipts),
                           question=question)


async def _run_plan(raw_plan: dict, org: str, facts_of: Callable, *, question: str = "") -> dict:
    """One plan read through records_run.run_query (read-only, for one company, every query recorded as a receipt) and
    its values made by `facts_of(onto, plan_as_run, result, receipts)` from the answer's own queries (what Hoistra reads
    after a work-order answer comes after them). A plan the records cannot run is a ValueError; NotReady is raised when
    there is no map of the records."""
    from . import records_values

    run = await records_run.run_query(raw_plan, question=question, org=org)
    out = {"found": bool(run["found"]), "facts": [], "receipts": run["receipts"], "read_at": run["read_at"],
           "plan": run["plan"], "notes": run.get("notes") or []}
    if not run["found"]:
        return dict(out, unresolved=list(run["unresolved"]))
    receipts = run["receipts"]
    out["facts"] = facts_of(await records_run.load_map(), run["plan"], run["result"],
                            receipts[:records_values._main_end(receipts)])
    return out


# ── Run again now, and a person's own plan beside Hoistra's (19 Nov 2026) ──────────────────────────────────────────

async def read_plan(plan: dict, *, question: str, org: str, mode: str = "hoistra", scope: dict | None = None) -> dict:
    """One plan read through records_run.run_query and shown the way an expected answer shows it: found, the values with
    their receipts (facts), the receipts, read_at, what the records do not hold (unresolved), what the steps said (notes),
    the sentence in fixed words and the plan as it ran."""
    from . import records_values

    run = await records_run.run_query(plan, question=question, org=org, mode=mode, scope=scope)
    onto = await records_run.load_map()
    as_run = run.get("plan") or plan                # the steps can refocus a plan: the values read the plan as run
    facts = records_values.values_of(onto, as_run, run)
    return {"found": bool(run.get("found")), "facts": facts, "receipts": list(run.get("receipts") or []),
            "read_at": run.get("read_at"), "unresolved": list(run.get("unresolved") or []),
            "notes": list(run.get("notes") or []), "sentence": records_values.sentence_of(onto, as_run, run, facts),
            "plan": as_run}


def _is_index(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


#: What a query a person changed and saved says it came from (the page marks it `source: "person"`; `edited` also counts).
PERSON_WORDS = "Your version of the query"


def _edited(block: dict | None) -> bool:
    """Whether a stored query is one a person changed: it is read as written, because Hoistra's steps would put back what
    they took out (the deleted-vendor filter, the codes it adds)."""
    return bool(block) and (bool(block.get("edited")) or block.get("source") == "person")


def _why(exc: Exception) -> str:
    """The first line of what went wrong, short (a database error's own words, not the statement it carries)."""
    text_ = str(getattr(exc, "orig", None) or exc).strip()
    return (text_.splitlines() or [""])[0][:200]


def _not_ready() -> Any:
    from .review_errors import Invalid

    return Invalid("not_ready", "Hoistra's map of the records is not ready yet. Try again in a few minutes.")


async def _turn_user(turn_id: str, org: str) -> str | None:
    """The person who asked a turn (the chat's own record of it), None when the chat has no such turn in this company."""
    from . import conversations

    async with conversations._read() as conn:
        row = (await conn.execute(text(
            f"SELECT tt.user_id::text AS user_id, tt.organization_id::text AS org FROM {conversations.TURNS} tt"
            " WHERE tt.turn_id = :t"), {"t": turn_id})).mappings().first()
    return row["user_id"] if row is not None and row["org"] == org else None


async def _read_scope(got: dict, turn_id: str, block: dict | None) -> tuple[dict | None, dict]:
    """The access a query is read with: the one it was drafted with. A query of the coding agent's own draft (the newest
    expected answer, untouched: `draft`) that says it was read for the whole company is read for the whole company again;
    any other - a version a person saved, whose blocks anyone could have written, a query not stored yet, one whose block
    does not say - is read as the person who asked, with their access rebuilt the way the drafting round rebuilds it
    (auto_expected.asker_scope): never more than they could see. Returns the scope run_query takes (None: the whole
    company) and {"scope", "scope_email"} for the page."""
    from . import auto_expected
    from .review_errors import Invalid

    company = {"scope": "company", "scope_email": None}
    if block is not None and block.get("scope") == "company" and got.get("draft") is True:
        return None, company
    try:
        asker = await auto_expected.asker_scope(got["company"], await _turn_user(turn_id, got["company"]))
    except Exception as exc:  # noqa: BLE001 - who asked is not guessed: a failed read is told, never read as "no restriction"
        log.warning("trace_lab.answer_key.asker_unreadable", error=str(exc)[:200])
        raise Invalid("asker_unreadable", "Who asked could not be read just now. Try again in a few minutes.") from exc
    if asker["scope"] != "asker":
        return None, company
    if asker["building_ids"] is not None and not asker["building_ids"]:
        raise Invalid("asker_has_no_buildings", "The person who asked has no buildings, so nothing can be read for them.")
    return ({"building_ids": asker["building_ids"], "email": asker["email"]},
            {"scope": "asker", "scope_email": asker["email"]})


async def _read(plan: dict, *, question: str, org: str, mode: str, scope: dict | None, person: bool) -> dict:
    """read_plan with what goes wrong said in words. `person`: the plan is one a person wrote, so whatever the records
    refuse about it (a plan that does not fit, a statement that takes too long) is theirs to fix (bad_query)."""
    from .review_errors import Invalid

    try:
        return await read_plan(plan, question=question, org=org, mode=mode, scope=scope)
    except NotReady as exc:
        raise _not_ready() from exc
    except oq.NotAllowed as exc:
        raise Invalid("asker_has_no_buildings", "The person who asked has no buildings, so nothing can be read for them.") from exc
    except Exception as exc:  # noqa: BLE001 - the records engine's own refusals and the database's are told in words
        if person:
            words = "The records could not be read with this query: " + _why(exc)
            raise Invalid("bad_query", words, problems=[words]) from exc
        if isinstance(exc, (ValueError, KeyError, TypeError, oq.OntologyError)):
            raise Invalid("plan_does_not_fit", "This query does not fit the records any more: " + _why(exc)) from exc
        log.warning("trace_lab.answer_key.read_failed", error=_why(exc))
        raise Invalid("read_failed", "The records could not be read just now. Try again in a minute.") from exc


async def recheck(m: Any, turn_id: str, *, index: int = 0) -> dict:
    """Run again now (the receipt panel): the turn's values read afresh, for the same company, with nothing stored; the page
    shows them beside the values read when the question was asked. For values read from the records it is the stored query
    `index` (evidence.queries[index], the plan as run) read the way Hoistra reads one and with the access it was drafted
    with (_read_scope); a query a person changed is read as written, and only for curators and the platform team (spec
    call 7: everyone else may not run a changed query); for an answer the answer key itself drafted (before 19 Nov 2026) it is the same key with the same parameters.
    Returns found, facts, receipts, read_at, unresolved, notes, sentence, and scope and scope_email (the access it was read
    with)."""
    from . import runs
    from .review_errors import Invalid

    got = await runs.turn_expected(m, turn_id)        # bounded to the caller's company; NotFound otherwise
    ev = ((got.get("expected") or {}).get("evidence") or {})
    ev = ev if isinstance(ev, dict) else {}
    if not got.get("company") or got.get("kind") not in ("records", "answer_key"):
        raise Invalid("no_answer_key", "This expected answer was not read from the records.")
    if got["kind"] == "records":
        stored = ev.get("queries")
        block = stored[index] if isinstance(stored, list) and _is_index(index) and 0 <= index < len(stored) else None
        if not isinstance(block, dict) or not isinstance(block.get("plan"), dict):
            raise Invalid("no_query", "That query is not in this expected answer.")
        if _edited(block) and not got.get("draft"):
            runs.require_curator(m)                 # a query a person changed: curators and the platform team (call 7)
        scope, who = await _read_scope(got, turn_id, block)
        out = await _read(block["plan"], question=str(block.get("question") or got.get("text") or "").strip(), org=got["company"],
                          mode="as_written" if _edited(block) else "hoistra", scope=scope, person=False)
        return {**{k: out[k] for k in ("found", "facts", "receipts", "read_at", "unresolved", "notes", "sentence")}, **who}
    key = KEYS.get(str(ev.get("key") or ""))
    if key is None:
        raise Invalid("no_answer_key", "This expected answer was not read from the records.")
    params = dict(ev.get("params") or {})
    try:
        out = await run(key, params, got["company"], question=str(got.get("text") or ""))
    except NotReady as exc:
        raise _not_ready() from exc
    except (KeyError, ValueError, TypeError) as exc:
        raise Invalid("no_answer_key", "This expected answer cannot be read again: " + str(exc)[:200]) from exc
    return {"found": out["found"], "facts": out["facts"], "receipts": out["receipts"], "read_at": out["read_at"],
            "unresolved": out.get("unresolved") or [], "notes": out.get("notes") or [],
            "sentence": sentence(key, params, out["facts"]) if out["found"] else "",
            "scope": "company", "scope_email": None}


# ── The orchestrator's own query: what the editor starts from, and a person's plan run beside it ───────────────────

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_DATE_COL = re.compile(r"date|_at$|expir|\bdue\b|_due")


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


def _generic_item(c: Any, attrs: dict) -> str:
    for col in [*(getattr(c, "identifiers", None) or []), getattr(c, "label", None), *(getattr(c, "search", None) or []), "name", "title", "id"]:
        v = attrs.get(col)
        if v not in (None, ""):
            return str(v)
    return "record"


async def _turn_queries(m: Any, turn_id: str) -> tuple[dict, list[dict], str | None]:
    """The records queries of a live turn as the editor lists them, and why there are none when there are none.

    A turn drafted with values has them stored (evidence.queries, each the plan as Hoistra's steps ran it: the editor
    starts from what ran); one not drafted yet gets the queries records_queries finds for it (Hoistra's own from its trace,
    else the answer key's), never a proposal: showing the editor makes no model call. Each query is {"index", "source",
    "source_words", "question", "plan"} and, apart from the editor's view, "_block": the stored query or None."""
    from . import eval_store, records_queries, runs
    from .review_errors import Invalid, NotFound

    got = await runs.turn_expected(m, turn_id)          # bounded to the caller's company; NotFound otherwise
    if not got.get("live") or not got.get("company"):
        raise Invalid("not_live", "Only a live question's query can be checked here.")
    ev = ((got.get("expected") or {}).get("evidence") or {})
    ev = ev if isinstance(ev, dict) else {}
    asked = str(got.get("text") or "")

    def listed(index: int, source: Any, question: Any, plan: dict, block: dict | None) -> dict:
        words = records_queries.SOURCE_WORDS.get(source) or (PERSON_WORDS if source == "person" else "")
        return {"index": index, "source": source, "source_words": words, "question": question or asked, "plan": plan,
                "_block": block}

    stored = ev.get("queries") if got.get("kind") == "records" else None
    if isinstance(stored, list) and stored:
        return got, [listed(i, b.get("source"), b.get("question"), b["plan"], b) for i, b in enumerate(stored)
                     if isinstance(b, dict) and isinstance(b.get("plan"), dict)], None
    try:
        nodes = (await eval_store.trace_tree_of(m, turn_id)).get("nodes") or []
    except NotFound:                                    # the conversation is not filed in Trace Lab yet
        nodes = []
    found = await records_queries.queries_for(nodes, asked, propose=False)
    return (got, [listed(i, q["source"], q.get("question"), q["plan"], None) for i, q in enumerate(found["queries"])],
            found["none"] or ev.get("no_query"))


async def orchestrator_query(m: Any, turn_id: str) -> dict:
    """GET .../expected/query: the turn's records queries for the editor, with Hoistra's map to pick from.

    {"ready": the map is built, "can_run": the caller may run a plan (curators and the platform team), "queries": [{"index",
    "source", "source_words", "question", "plan"}], "none": why there is no query (or None), "map": records_form.query_map}.
    A turn that is not a live question's, or another company's, is refused (Invalid / NotFound)."""
    from . import records_form, runs

    _got, queries, none = await _turn_queries(m, turn_id)
    mapped, ready = None, True
    try:
        onto = await records_run.load_map()
        if queries:                                      # the map is big: not built for a turn with nothing to edit
            mapped = await records_form.map_for(onto)
    except NotReady:
        ready = False
    except Exception as exc:  # noqa: BLE001 - a map that cannot be loaded is not ready; the stored queries still show
        log.warning("trace_lab.answer_key.map_unavailable", error=_why(exc))
        ready = False
    return {"ready": ready, "can_run": runs.can_curate(m),
            "queries": [{k: v for k, v in q.items() if k != "_block"} for q in queries], "none": none, "map": mapped}


async def try_query(m: Any, turn_id: str, *, index: int, plan: dict) -> dict:
    """POST .../expected/query: a person's own plan read, as written, beside the turn's query read the way Hoistra reads
    it, both for the question's company (and with the asker's access when the query was drafted with it). Only curators and
    the platform team; the plan must fit Hoistra's map (records_form.check_plan, else Invalid "bad_query" in words); nothing
    is stored: the page saves it when the person says so. Returns {"before": Hoistra's query's values, "after": the
    plan's, "changes": what changed in words, "scope", "scope_email"}; each side is read_plan's result (before carries an
    `error` instead of values when Hoistra's own query could not be read now)."""
    from . import records_form, runs
    from .review_errors import Invalid

    runs.require_curator(m)
    got, queries, _none = await _turn_queries(m, turn_id)
    q = next((x for x in queries if _is_index(index) and x["index"] == index), None)
    if q is None:
        raise Invalid("no_query", "That query is not in this turn.")
    try:
        onto = await records_run.load_map()
    except NotReady as exc:
        raise _not_ready() from exc
    problems = records_form.check_plan(onto, plan)
    if problems:
        raise Invalid("bad_query", " ".join(problems), problems=problems)
    scope, who = await _read_scope(got, turn_id, q["_block"])
    read = dict(question=q["question"], org=got["company"], scope=scope)
    # Hoistra's own query is read with every step of its records tool; one a person changed and saved is read as written.
    mode = "as_written" if _edited(q["_block"]) else "hoistra"
    was, now = await asyncio.gather(_read(q["plan"], mode=mode, person=False, **read),
                                    _read(plan, mode="as_written", person=True, **read), return_exceptions=True)
    if isinstance(now, BaseException):
        raise now
    if isinstance(was, BaseException):                  # the comparison is a help: the person's own plan still ran
        if not isinstance(was, Invalid):
            raise was
        was = {"found": False, "facts": [], "receipts": [], "read_at": None, "unresolved": [], "notes": [], "sentence": "",
               "plan": q["plan"], "error": was.error}
    return {"before": was, "after": now, "changes": records_form.plan_changes(onto, q["plan"], plan), **who}
