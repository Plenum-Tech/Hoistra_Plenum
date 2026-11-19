"""Values and a sentence from a records run (18 Nov 2026, Hussain and Bala).

`records_run.run_query` re-runs a records query the way Hoistra's records tool does and returns the result with every
query it made as a receipt. This module turns that run into what a person sees as the expected answer: a list of
values, each `{id, kind, label, value, receipt}` pointing at the receipt (the query and the rows) it was read from, and
one sentence in fixed words (no model). Pure functions: nothing here reads a database.

    count     "Floor records found: 0"              the `count` query
    stated    "ABC states floors: 25"               the `stated <table> on <Concept>` query: a figure the record holds itself
    group     "Bishopsgate Tower: 12"               one per row of an aggregate (the `aggregate` query)
    record    each listed record's name or code     up to VALUES_SHOWN, then "and N more" with the exact count
    field     one record's non-empty attributes     a single record, keys and timestamps left out
    linked    "WO-4527 → vendor: Apex Mechanical"   the `include <name> [...]` query that read it
    pending   "Overdue open work orders: 3"         what Hoistra reads after a work-order answer (pending_context)
    missing   a name the records do not hold        from `unresolved` only; no reason is guessed. Its receipt is the last
                                                    `resolve` query that carried the name, else None

A value whose receipt cannot be told for certain has `receipt: None`: never a neighbour's. Trace Lab does not judge
Hoistra's answer: nothing here says right or wrong.

`changed_since` compares the row counts of a re-run with the ones Hoistra recorded when it ran its own queries.
"""
from __future__ import annotations

import math
import re
from types import SimpleNamespace
from typing import Any

from .answer_key import ROWS_KEPT, _generic_item, _grouped, _words, jsonable

#: Values listed for one query: records, groups, fields, linked records, and each kind of pending count.
VALUES_SHOWN = 20
#: Items of one linked kind shown in one value; the rest are "and N more".
LINKED_SHOWN = 3

#: A record's own bookkeeping is not an answer: its keys (the id, any *_id) and when the row was stored.
_NOT_FIELDS = ("id", "key", "created_at", "updated_at")
#: pending_context's groups, in the order it reads them: (its key, the word the label uses).
_PENDING_GROUPS = (("open_by_trade", "trade"), ("open_by_vendor", "vendor"), ("open_by_wo_type", "type"),
                   ("open_by_priority", "priority"))
#: What a pending group's label starts with; the sentence folds consecutive labels with the same head.
_PENDING_HEAD = "Open work orders by "
_INCLUDE_KEY = re.compile(r"^(.*?) \((\w+)\)$")


# ── receipts ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _label(receipt: Any) -> str:
    return str(receipt.get("label") or "") if isinstance(receipt, dict) else ""


def _main_end(receipts: list) -> int:
    """Where the answer's own queries end: the answer's `aggregate`, or its `count`, `focus` and `include` queries.
    Everything after is pending_context reading its own variants of the same plan, with labels of its own kind."""
    for i, r in enumerate(receipts):
        label = _label(r).lower()
        if label == "aggregate":
            return i + 1
        if label.startswith("count"):
            j = i + 1
            if j < len(receipts) and _label(receipts[j]).lower().startswith("focus "):
                j += 1
            while j < len(receipts) and _label(receipts[j]).lower().startswith("include "):
                j += 1
            return j
    return len(receipts)


def _first(receipts: list, prefix: str, *, exact: bool = False) -> str | None:
    """The id of the first receipt whose label starts with `prefix` (or is it), else None: never another receipt."""
    for r in receipts:
        label = _label(r).lower()
        if label == prefix or (not exact and label.startswith(prefix)):
            return r.get("id")
    return None


def _carries(value: Any, name: str) -> bool:
    """Whether a query parameter (or an item of a list parameter) is this name, compared whole and without case."""
    if isinstance(value, (list, tuple)):
        return any(_carries(v, name) for v in value)
    return value is not None and not isinstance(value, (dict, bool)) and str(value).strip().casefold() == name.strip().casefold()


def _lookup_receipt(receipts: list, name: str) -> str | None:
    """The last `resolve ...` query (Hoistra's ladder for finding a record by name or code) that had this name as a
    parameter, so a person can see how it was looked for; None when none did, never another query."""
    for r in reversed(receipts):
        if _label(r).lower().startswith("resolve ") and any(_carries(v, name) for v in (r.get("params") or {}).values()):
            return r.get("id")
    return None


def _empty_group(onto: Any, group: dict) -> str:
    """What a group with no name is called. A blank name of the concept's own (its label column) is no such record
    on record; any other blank column is that column: 7 work orders with no priority are 7 work orders."""
    concept, attribute = group.get("concept"), group.get("attribute")
    c = onto.concepts.get(concept)
    if attribute and attribute not in (getattr(c, "label", None), "name"):
        return "No " + str(attribute).replace("_", " ") + " on record"
    return "No " + _words(concept, 1) + " on record"


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


# ── values ────────────────────────────────────────────────────────────────────────────────────────────────────────

def _plain(v: Any) -> str | int | float:
    """A value a person can read and a JSON column can hold."""
    v = jsonable(v)
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, float) and not math.isfinite(v):
        return str(v)
    if isinstance(v, (int, float, str)):
        return v
    return str(v)


def _figure(text: str) -> str | int | float:
    """A figure a record states, as a number when it is one."""
    s = text.strip()
    if "_" in s:  # Python reads 1_000 as a number; a record that states "1_000" is not stating one
        return text
    for kind in (int, float):
        try:
            n = kind(s)
        except ValueError:
            continue
        if not (isinstance(n, float) and not math.isfinite(n)):
            return n
    return text


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, (str, list, tuple, dict, set)) and not (v.strip() if isinstance(v, str) else v))


def _fact(kind: str, label: str, value: Any, receipt: str | None) -> dict:
    return {"id": "", "kind": kind, "label": label, "value": value, "receipt": receipt}


def _concept_name(plan: dict, result: dict | None) -> str:
    return str(((plan or {}).get("focus") or {}).get("concept") or (result or {}).get("focus") or "record")


def _count_label(concept: str) -> str:
    return _words(concept, 1).capitalize() + " records found"


def _stated(main: list) -> list[dict]:
    """The figures a record states itself, from the `stated <table> on <Concept>` queries (rows {l, v})."""
    out = []
    for r in main:
        m = re.match(r"^stated (.+) on (.+)$", _label(r))
        if not m:
            continue
        for row in r.get("rows") or []:
            v = str((row or {}).get("v") or "")
            if v.strip():
                out.append(_fact("stated", f"{row.get('l') or m.group(2)} states {m.group(1).replace('_', ' ')}", _figure(v), r.get("id")))
    return out


def _field_items(c: Any, attrs: dict) -> list[tuple[str, Any]]:
    """Every non-empty attribute of a record that is an answer, not bookkeeping: (label, value)."""
    skip = {*_NOT_FIELDS, getattr(c, "key", None)}
    return [(str(name).replace("_", " ").capitalize(), _plain(v)) for name, v in (attrs or {}).items()
            if not (name in skip or str(name).endswith("_id") or _empty(v))]


def _fields(c: Any, attrs: dict, receipt: str | None) -> list[dict]:
    return [_fact("field", label, v, receipt) for label, v in _field_items(c, attrs)[:VALUES_SHOWN]]


def _target(onto: Any, plan: dict, key: str) -> tuple[str, Any]:
    """The include's own name and the concept it reads. Hoistra keys `related` as "<name> (<Concept>)"; a plain name is
    looked up among the plan's includes."""
    m = _INCLUDE_KEY.match(key)
    name, concept = (m.group(1), m.group(2)) if m else (key, None)
    if concept is None:
        concept = next((i.get("concept") for i in (plan or {}).get("include") or [] if isinstance(i, dict) and i.get("name") == name), None)
    return name, (onto.concepts.get(concept) if concept else None) or SimpleNamespace()


def _linked(onto: Any, plan: dict, c: Any, records: list, main: list) -> list[dict]:
    out = []
    for rec in records:
        who = _generic_item(c, rec.get("attributes") or {})
        for key, items in (rec.get("related") or {}).items():
            items = [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
            if not items:
                continue
            name, target = _target(onto, plan, key)
            names = [_generic_item(target, x) for x in items[:LINKED_SHOWN]]
            more = len(items) - len(names)
            link = items[0].get("linked by")
            rid = (_first(main, f"include {name} [{link}]".lower(), exact=True) if link else None) or _first(main, f"include {name} ".lower())
            out.append(_fact("linked", f"{who} → {name.replace('_', ' ')}", ", ".join(names) + (f", and {more} more" if more > 0 else ""), rid))
    return out


def _pairs(rows: list) -> list[tuple]:
    """(name, count) per row of a pending group query, as pending_context reads them."""
    out = []
    for row in rows or []:
        row = row if isinstance(row, dict) else {}
        out.append((next((v for k, v in row.items() if k != "count"), None) or "Unassigned", _int(row.get("count"))))
    return out


def _group_receipt(receipts: list, entries: list) -> str | None:
    """The `aggregate` query that read these (name, count) groups: the one whose rows are the same, in the same order."""
    want = [(e.get("name"), _int(e.get("count"))) for e in entries if isinstance(e, dict)][:ROWS_KEPT]
    for r in receipts:
        if _label(r).lower() == "aggregate":
            got = _pairs(r.get("rows"))
            if want and len(got) >= len(want) and got[:len(want)] == want:
                return r.get("id")
    return None


def _count_receipt(receipts: list, value: int, start: int) -> tuple[str | None, int]:
    """The first `count` query from `start` on that read this number, and where to look next."""
    for i in range(start, len(receipts)):
        r = receipts[i]
        if _label(r).lower().startswith("count"):
            rows = r.get("rows") or []
            if rows and isinstance(rows[0], dict) and _int(rows[0].get("n")) == value:
                return r.get("id"), i + 1
    return None, start


def _pending_entries(pending: dict, key: str) -> list[dict]:
    """Every {name, count} of one of pending_context's groups (open_by_trade is a dict of trade to count)."""
    raw = pending.get(key)
    if isinstance(raw, dict):
        raw = [{"name": n, "count": c} for n, c in raw.items()]
    return [e for e in raw or [] if isinstance(e, dict) and _int(e.get("count")) is not None] if isinstance(raw, list) else []


def _pending(pending: Any, receipts: list) -> list[dict]:
    """The counts Hoistra reads after a work-order answer: how many are open, open by trade, vendor, type and priority,
    overdue, blocked. The risk-to-life flags are a keyword match, not a stored fact, so they are not values."""
    if not isinstance(pending, dict):
        return []
    out, at = [], 0
    total = _int(pending.get("open_total"))
    if total is not None:
        rid, at = _count_receipt(receipts, total, at)
        out.append(_fact("pending", "Open work orders", total, rid))
    for key, word in _PENDING_GROUPS:
        entries = _pending_entries(pending, key)
        # a trade is a roll-up of categories: its receipt is the category query
        rid = _group_receipt(receipts, _pending_entries(pending, "open_by_category") if key == "open_by_trade" else entries)
        for e in entries[:VALUES_SHOWN]:
            out.append(_fact("pending", f"{_PENDING_HEAD}{word}: {e.get('name')}", e["count"], rid))
    for key, label in (("overdue_open", "Overdue open work orders"), ("blocked_open", "Blocked open work orders")):
        n = _int(pending.get(key))
        if n is not None:
            rid, at = _count_receipt(receipts, n, at)
            out.append(_fact("pending", label, n, rid))
    return out


def values_of(onto: Any, plan: dict, run: dict) -> list[dict]:
    """The values of one records run, each `{id, kind, label, value, receipt}`: ids are f1, f2, ... in the order shown.

    A name the records do not hold is a `missing` value (from `run["unresolved"]`), never a zero. Otherwise: for a list
    or a single record the count, the figures a record states, then the records (or, for one record, its fields), what
    they are linked to, and Hoistra's pending counts; for an aggregate, one value per group, then the pending counts."""
    if not run.get("found", True):
        seen: list[str] = []
        for name in run.get("unresolved") or []:
            if str(name) not in seen:
                seen.append(str(name))
        receipts = list(run.get("receipts") or [])
        facts = [_fact("missing", "Not on record", name, _lookup_receipt(receipts, name)) for name in seen]
    else:
        result = run.get("result")
        facts = []
        if isinstance(result, dict):
            receipts = list(run.get("receipts") or [])
            end = _main_end(receipts)
            main, after = receipts[:end], receipts[end:]
            concept = _concept_name(plan, result)
            if result.get("kind") == "aggregate":
                by = ((plan or {}).get("aggregate") or {}).get("count_by") or [{}]
                empty = _empty_group(onto, by[0])
                rid = _first(main, "aggregate", exact=True)
                facts = [{**f, "kind": "group", "receipt": rid} for f in _grouped({}, result, main, empty)[:VALUES_SHOWN]]
            else:
                c = onto.concepts.get(concept) or onto.concepts.get(result.get("focus")) or SimpleNamespace(name=concept)
                records = [r for r in result.get("records") or [] if isinstance(r, dict)]
                shown = records[:VALUES_SHOWN]
                focus_rid = _first(main, "focus ")
                facts = [_fact("count", _count_label(concept), int(result.get("total") or 0), _first(main, "count"))]
                facts += _stated(main)
                if (plan or {}).get("question_type") == "single" and len(records) == 1:
                    facts += _fields(c, records[0].get("attributes") or {}, focus_rid)
                else:
                    facts += [_fact("record", "", _generic_item(c, r.get("attributes") or {}), focus_rid) for r in shown]
                facts += _linked(onto, plan, c, shown, main)
            facts += _pending(run.get("pending"), after)
    for i, f in enumerate(facts, start=1):
        f["id"] = "f" + str(i)
    return facts


# ── the sentence ──────────────────────────────────────────────────────────────────────────────────────────────────

def _say(f: dict) -> str:
    return f"{f['label']}: {f['value']}"


def sentence_of(onto: Any, plan: dict, run: dict, facts: list[dict]) -> str:
    """The expected answer in one line, from the values and fixed words. The figure a record states comes first, then
    the count, the groups, the records (with "and N more" past VALUES_SHOWN), a single record's fields, what is linked
    to them, and what is still pending."""
    if not facts:
        return "Nothing was found in the records."
    of: dict[str, list[dict]] = {}
    for f in facts:
        of.setdefault(f["kind"], []).append(f)
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    concept = _concept_name(plan, result)
    parts: list[str] = []
    if of.get("missing"):
        parts.append("Not on record: " + ", ".join(str(f["value"]) for f in of["missing"]))
    parts += [_say(f) for f in of.get("stated", [])]
    parts += [_say(f) for f in of.get("count", [])]
    if of.get("group"):
        more = len(result.get("rows") or []) - len(of["group"])
        parts.append("Counts: " + ", ".join(f"{f['label']} {f['value']}" for f in of["group"]) + (f", and {more} more" if more > 0 else ""))
    if of.get("record"):
        total = of["count"][0]["value"] if of.get("count") else 0
        more = total - len(of["record"])
        parts.append(_words(concept).capitalize() + ": " + ", ".join(str(f["value"]) for f in of["record"])
                     + (f", and {more} more" if more > 0 else ""))
    if of.get("field"):
        records = result.get("records") or [{}]
        who = _generic_item(onto.concepts.get(concept) or SimpleNamespace(), (records[0] or {}).get("attributes") or {})
        more = len(_field_items(onto.concepts.get(concept) or SimpleNamespace(), (records[0] or {}).get("attributes") or {})) - len(of["field"])
        parts.append(who + ": " + ", ".join(f"{f['label'].lower()} {f['value']}" for f in of["field"])
                     + (f", and {more} more" if more > 0 else ""))
    parts += [_say(f) for f in of.get("linked", [])]
    runs: list[list] = []
    for f in of.get("pending", []):
        head, sep, tail = str(f["label"]).partition(": ")
        if sep and runs and runs[-1][0] == head:
            runs[-1][1].append(f"{tail} {f['value']}")
        elif sep:
            runs.append([head, [f"{tail} {f['value']}"]])
        else:
            runs.append([None, _say(f)])
    pending = run.get("pending") if isinstance(run.get("pending"), dict) else {}
    for head, items in runs:
        if head is None:
            parts.append(items)
            continue
        key = next((k for k, word in _PENDING_GROUPS if head == _PENDING_HEAD + word), None)
        more = len(_pending_entries(pending, key)) - len(items) if key else 0
        parts.append(head + ": " + ", ".join(items) + (f", and {more} more" if more > 0 else ""))
    text = ". ".join(parts)
    return text if text.endswith(".") else text + "."


# ── what changed since Hoistra read ───────────────────────────────────────────────────────────────────────────────

def _recorded(span: Any) -> tuple[str, int] | None:
    """(label, row count) of a SQL span Hoistra's trace recorded under its records tool: input.label, output.row_count
    (or flat label and row_count). A span with no row count failed there, and left no receipt here either."""
    if not isinstance(span, dict):
        return None
    i, o = span.get("input"), span.get("output")
    label = i.get("label") if isinstance(i, dict) else span.get("label")
    count = _int(o.get("row_count") if isinstance(o, dict) else span.get("row_count"))
    return (str(label), count) if label and count is not None else None


def changed_since(receipts: list[dict], recorded: list[dict]) -> list[dict]:
    """`[{label, then, now}]` for every re-run query whose row count differs from the one Hoistra recorded for the
    query with the same label: the nth re-run query of a label is compared with the nth recorded query of that label."""
    then: dict[str, list[int]] = {}
    for span in recorded or []:
        got = _recorded(span)
        if got:
            then.setdefault(got[0], []).append(got[1])
    seen: dict[str, int] = {}
    out = []
    for r in receipts or []:
        label = _label(r)
        n = seen[label] = seen.get(label, -1) + 1
        before, now = then.get(label, []), _int((r or {}).get("row_count"))
        if n < len(before) and now is not None and before[n] != now:
            out.append({"label": label, "then": before[n], "now": now})
    return out
