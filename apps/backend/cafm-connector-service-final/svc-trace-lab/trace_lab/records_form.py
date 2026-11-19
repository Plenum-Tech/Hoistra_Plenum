"""Editing the whole query (19 Nov 2026, Hussain and Bala): what a person may pick from, whether what they wrote fits, and
what they changed, in words.

Hoistra's records tool writes a plan (kind of answer, the records read, their states and conditions, the records they
are limited to, how they are grouped, what is shown with each one). A person who finds the plan wrong changes it in
fields, never in SQL. Everything they can choose comes from Hoistra's own map of the records (the ontology): its kinds of
record, their columns, states (with descriptions), what links to what, the conditions and the periods.

    query_map(onto)               what the fields offer: concepts (columns with their kind, states, links), ops, periods
    check_plan(onto, plan)        why a plan does not fit the map, in plain words naming the part of the query; [] when it does
    plan_changes(onto, old, new)  what a person changed, in words ("What: Floor records → Building records")

Pure functions over the map: nothing here reads the records. The check is Hoistra's own (ontology_qa.validate_plan) with
its messages put in words, plus the two things only the compiler would say later (a period, a range).
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
from typing import Any

from src.agents import ontology_qa as oq

from . import answer_key as ak

#: The most parts of one kind a plan may have (records it is limited to, records shown with each result, conditions on
#: one record): a plan beyond it is refused before it reaches the database.
MAX_PARTS = 20
#: The most a plan may weigh as JSON: a plan is a few hundred bytes; one beyond this is not one a person wrote to read records.
MAX_PLAN_BYTES = 60_000

#: The conditions in the words the editor uses: in its select and inside a sentence ("the expiry date is within the next
#: 60 days"). The page (queryForm.js) reads them from query_map, so this is the one place they are written.
OP_WORDS = {"eq": "is", "ne": "is not", "in": "is one of", "not_in": "is not one of", "lt": "is before",
            "lte": "is on or before", "gt": "is after", "gte": "is on or after", "between": "is between",
            "within": "is within", "contains": "contains", "is_null": "is empty"}
#: The comparisons, for a column that holds a number ("the cost is less than 5").
NUMBER_WORDS = {"lt": "is less than", "lte": "is at most", "gt": "is more than", "gte": "is at least"}
_OP_ORDER = tuple(OP_WORDS)
#: The same conditions inside a sentence about a change: by the kind of column, and "within" as it reads with a period.
_SAY = {"eq": "is", "ne": "is not", "in": "is one of", "not_in": "is not one of", "between": "is between",
        "within": "within", "contains": "contains"}
_SAY_COMPARE = {"lt": ("is before", "is less than"), "lte": ("is on or before", "is at most"),
                "gt": ("is after", "is more than"), "gte": ("is on or after", "is at least")}
_ANSWER_KINDS = {"single": "one record", "list": "list", "count": "count"}


# ── the map ───────────────────────────────────────────────────────────────────────────────────────────────────────

def _kind(c: Any, attribute: str, types: dict) -> str:
    """What a column holds: date, number or text. The map's column types decide when it holds them; without them only a
    name that reads like a date is one (there is no telling a number)."""
    kind = types.get((getattr(c, "table", None), c.attributes[attribute]))
    if kind:
        return "date" if kind in oq.DATE_TYPES else "number" if kind in oq.NUMERIC_TYPES else "text"
    return "date" if ak._DATE_COL.search(attribute) else "text"


def _links(onto: Any, name: str) -> list[str]:
    """The kinds of record an empty `via` can reach from this one: what a "limited to" or "also show" may name."""
    out = []
    for other in onto.concepts:
        if other == name:
            continue
        try:
            onto.resolve_path(name, other, [])
        except (oq.OntologyError, KeyError):
            continue
        out.append(other)
    return out


def query_map(onto: Any) -> dict[str, Any]:
    """What the query's fields offer, from Hoistra's map: every kind of record (its name in plural words) with its columns
    (and whether each is a date, a number or text), its states with their descriptions, and the kinds of record it links
    to; the conditions (id, words, and for a comparison the words for a number) and the periods a condition can name."""
    types = getattr(onto, "col_types", None) or {}
    concepts = [{
        "name": name, "words": ak._words(name),         # plural, as the editor says it: "a count of floors"
        "attributes": [{"name": a, "kind": _kind(c, a, types)} for a in c.attributes],
        "states": [{"id": s, "description": str((v or {}).get("description") or "")[:240]} for s, v in (c.states or {}).items()],
        "links": _links(onto, name)} for name, c in onto.concepts.items()]
    ops = [o for o in _OP_ORDER if o in oq.OPS] + sorted(set(oq.OPS) - set(_OP_ORDER))
    return {"concepts": concepts,
            "ops": [{"id": o, "words": OP_WORDS.get(o) or o.replace("_", " "),
                     **({"number_words": NUMBER_WORDS[o]} if o in NUMBER_WORDS else {})} for o in ops],
            "periods": list(oq.PERIODS)}


#: query_map is a few seconds of work for a big map (every kind of record against every other), and one map of the records
#: stays the same until the database changes, so it is built once, off the event loop, and kept for the latest few.
_MAPS: dict[int, tuple[Any, dict]] = {}
_KEPT = 4


async def map_for(onto: Any) -> dict[str, Any]:
    held = _MAPS.get(id(onto))
    if held is not None and held[0] is onto:
        return held[1]
    built = await asyncio.to_thread(query_map, onto)
    _MAPS[id(onto)] = (onto, built)
    while len(_MAPS) > _KEPT:
        _MAPS.pop(next(iter(_MAPS)))
    return built


# ── words ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def _records(concept: Any) -> str:
    """"Floor records": a kind of record in words, capitalised."""
    return ak._words(str(concept), 1).capitalize() + " records" if concept else "nothing"


def _records_lower(concept: Any) -> str:
    return ak._words(str(concept), 1) + " records"


def _nth(plan: Any, key: str, i: int) -> dict:
    items = plan.get(key) if isinstance(plan, dict) else None
    node = items[i] if isinstance(items, list) and i < len(items) else None
    return node if isinstance(node, dict) else {}


def _part(where: str, plan: Any) -> str:
    """The part of the query a validate_plan problem names, as the editor labels it."""
    m = re.match(r"^(focus|aggregate|constraints\[(\d+)\]|include\[(\d+)\])( sort)?$", where)
    if not m:
        return where
    if m.group(1) == "focus":
        return "What"
    if m.group(1) == "aggregate":
        return "Grouped by"
    if m.group(2) is not None:
        i = int(m.group(2))
        concept = _nth(plan, "constraints", i).get("concept")
        return "Limited to (" + (ak._words(concept, 1) if concept else "#" + str(i + 1)) + ")"
    i = int(m.group(3))
    node = _nth(plan, "include", i)
    return "Also show (" + str(node.get("name") or node.get("concept") or "#" + str(i + 1)) + ")" + (" sort" if m.group(4) else "")


def _reword(error: str, plan: Any, onto: Any) -> str:
    """One of validate_plan's problems in plain words, naming the part of the query it is in."""
    where, sep, rest = error.partition(": ")
    if not sep:
        return error
    part = _part(where, plan)
    m = re.match(r"unknown concept '(.*)'\. Valid:", rest)
    if m:
        if m.group(1) in ("None", ""):
            return part + ": choose the kind of record" + (" to read." if where == "focus" else ".")
        return f"{part}: '{m.group(1)}' is not a kind of record Hoistra knows."
    m = re.match(r"(\w+) has no state '(.*)'\. Valid:", rest)
    if m:
        have = list(getattr(onto.concepts.get(m.group(1)), "states", None) or {})
        more = " Its states are: " + ", ".join(have) + "." if have else " " + _records(m.group(1)) + " have no states."
        return f"{part}: '{m.group(2)}' is not a state of {_records_lower(m.group(1))}.{more}"
    m = re.match(r"bad filter (.*) \(op must be one of", rest)
    if m:
        try:
            f = json.loads(m.group(1))
        except ValueError:
            f = None
        if isinstance(f, dict) and f.get("op") is not None:
            return f"{part}: '{f['op']}' is not a condition Hoistra knows."
        return part + ": a condition needs a column, a condition and a value."
    m = re.match(r"(\w+) has no attribute '(.*)'\. Valid:", rest)
    if m:
        return f"{part}: {_records_lower(m.group(1))} have no column '{m.group(2)}'."
    m = re.match(r"No automatic path from (\w+) to (\w+)", rest)
    if m:
        return f"{part}: {_records_lower(m.group(1))} are not linked to {_records_lower(m.group(2))}."
    m = re.match(r"Unknown relation '(.*)'", rest)
    if m:
        return f"{part}: '{m.group(1)}' is not a link Hoistra knows."
    m = re.match(r"via .* from (\w+) ends at (\w+), not (\w+)", rest)
    if m:
        return f"{part}: that link leads from {_records_lower(m.group(1))} to {_records_lower(m.group(2))}, not {_records_lower(m.group(3))}."
    m = re.match(r"(\w+) connects (\w+) -> (\w+), not (\w+)", rest)
    if m:
        return f"{part}: the link '{m.group(1)}' does not start from {_records_lower(m.group(4))}."
    m = re.match(r"count_by concept (\w+) must be", rest)
    if m:
        return f"{part}: {_records_lower(m.group(1))} must be the records read or one of the records they are limited to."
    if where == "focus" and "'via'" in rest:
        return part + ": the records read cannot be reached through a link."
    return part + ": " + rest.split(". Valid:")[0].rstrip(".") + "."


def _too_many(plan: dict) -> list[str]:
    out = []
    for key, label in (("constraints", "Limited to"), ("include", "Also show")):
        if len(plan[key]) > MAX_PARTS:
            out.append(f"{label}: at most {MAX_PARTS} are allowed.")
    for key, node in [("focus", plan["focus"])] + [("constraints[%d]" % i, n) for i, n in enumerate(plan["constraints"][:MAX_PARTS])] \
            + [("include[%d]" % i, n) for i, n in enumerate(plan["include"][:MAX_PARTS])]:
        if len(node["filters"]) > MAX_PARTS:
            out.append(f"{_part(key, plan)}: at most {MAX_PARTS} conditions are allowed.")
    return out


def _value_problems(plan: dict) -> list[str]:
    """What the compiler would refuse later, said now: a period that is not one, a range that is not two values."""
    nodes = [("focus", plan["focus"])] + [("constraints[%d]" % i, n) for i, n in enumerate(plan["constraints"])] \
        + [("include[%d]" % i, n) for i, n in enumerate(plan["include"])]
    out = []
    for where, node in nodes:
        for f in node["filters"]:
            if not isinstance(f, dict):
                continue
            value = f.get("value")
            if f.get("op") == "within" and not (isinstance(value, str) and oq.period_of(value)):
                out.append(f"{_part(where, plan)}: '{value}' is not a period. Use a name such as last_month, this_quarter or next_60_days.")
            elif f.get("op") == "between" and not (isinstance(value, (list, tuple)) and len(value) == 2):
                out.append(f"{_part(where, plan)}: a range needs two values (from and to).")
    return out


def check_plan(onto: Any, plan: Any) -> list[str]:
    """Why a plan does not fit Hoistra's map, in plain words that name the part of the query: [] when it fits.

    The plan is checked as a copy (normalised the way the records engine does before it validates), by the engine's own
    validate_plan, so a plan that passes here is one the engine accepts; its messages are put in words. A period or a
    range that is wrong is said here too, instead of by the compiler after a read has begun."""
    if not isinstance(plan, dict) or not isinstance(plan.get("focus"), dict):
        return ["What: choose the kind of record to read."]
    try:
        if len(json.dumps(plan, default=str)) > MAX_PLAN_BYTES:
            return ["The query is too large to run."]
        norm = oq.normalise_plan(copy.deepcopy(plan))
        size = _too_many(norm)
        if size:
            return size
        found = [_reword(e, norm, onto) for e in oq.validate_plan(onto, norm)] + _value_problems(norm)
    except (AttributeError, TypeError, KeyError, ValueError):
        return ["The query is not written the way Hoistra's records expect: check each part of it."]
    return list(dict.fromkeys(found))


# ── what changed ──────────────────────────────────────────────────────────────────────────────────────────────────

def _attribute_kind(onto: Any, concept: Any, attribute: Any) -> str:
    c = onto.concepts.get(concept) if isinstance(getattr(onto, "concepts", None), dict) else None
    attributes = getattr(c, "attributes", None) or {}
    if attribute not in attributes:
        return "text"
    return _kind(c, attribute, getattr(onto, "col_types", None) or {})


def _value_words(op: Any, value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return (" and " if op == "between" else ", ").join(str(v) for v in value)
    return "" if value is None else json.dumps(value, sort_keys=True) if isinstance(value, dict) else str(value)


def _condition(onto: Any, concept: Any, f: dict) -> str:
    """One condition in words: "expiry_date within next_60_days"."""
    return f"{f.get('attribute')} " + _say(onto, concept, f)


def _say(onto: Any, concept: Any, f: dict) -> str:
    """The condition and value without the column: "within next_60_days"."""
    op, value = f.get("op"), f.get("value")
    if op == "is_null":
        return "is empty" if value in (True, "true", "True", 1) else "is not empty"
    if op in _SAY_COMPARE:
        words = _SAY_COMPARE[op][0 if _attribute_kind(onto, concept, f.get("attribute")) == "date" else 1]
    else:
        words = _SAY.get(op) or str(op)
    return (words + " " + _value_words(op, value)).strip()


def _condition_changes(onto: Any, concept: Any, old: list, new: list) -> list[str]:
    old = [f for f in old if isinstance(f, dict)]
    new = [f for f in new if isinstance(f, dict)]
    left = list(old)
    fresh = []
    for f in new:
        if f in left:
            left.remove(f)
        else:
            fresh.append(f)
    changed = []
    for f in list(fresh):
        mate = next((o for o in left if o.get("attribute") == f.get("attribute")), None)
        if mate is not None:
            changed.append(f"Condition changed: {f.get('attribute')} {_say(onto, concept, mate)} → {_say(onto, concept, f)}")
            left.remove(mate)
            fresh.remove(f)
    return (changed + [f"Condition added: {_condition(onto, concept, f)}" for f in fresh]
            + [f"Condition removed: {_condition(onto, concept, f)}" for f in left])


def _node_changes(onto: Any, old: dict, new: dict, *, link: bool = False) -> list[str]:
    """What changed on one kind of record of the plan (the records read, one it is limited to, one shown with each)."""
    out = []
    if (old["match"] or None) != (new["match"] or None):
        out.append(f"Name or code: {old['match'] or 'any'} → {new['match'] or 'any'}")
    out += [f"State added: {s}" for s in new["states"] if s not in old["states"]]
    out += [f"State removed: {s}" for s in old["states"] if s not in new["states"]]
    out += _condition_changes(onto, new["concept"], old["filters"], new["filters"])
    if link and list(old["via"]) != list(new["via"]):
        out.append(f"Link: {', '.join(old['via']) or 'found by itself'} → {', '.join(new['via']) or 'found by itself'}")
    return out


def _constraint_name(n: dict) -> str:
    return ak._words(str(n["concept"]), 1).capitalize() + (" " + str(n["match"]) if n["match"] else "")


def _limit_changes(onto: Any, old: list, new: list) -> list[str]:
    left = list(old)
    pairs, fresh = [], []
    for n in new:                                   # the same record kind and name first, then the same kind
        mate = next((o for o in left if o["concept"] == n["concept"] and (o["match"] or None) == (n["match"] or None)), None)
        if mate is not None:
            left.remove(mate)
            pairs.append((mate, n))
        else:
            fresh.append(n)
    for n in list(fresh):
        mate = next((o for o in left if o["concept"] == n["concept"]), None)
        if mate is not None:
            left.remove(mate)
            fresh.remove(n)
            pairs.append((mate, n))
    out = [f"Limited to {ak._words(str(n['concept']), 1).capitalize()}: {line}" for o, n in pairs
           for line in _node_changes(onto, o, n, link=True)]
    return out + [f"Limited to: {_constraint_name(n)} added" for n in fresh] + [f"Limited to: {_constraint_name(o)} removed" for o in left]


def _groups(plan: dict) -> list[str]:
    agg = plan.get("aggregate")
    by = agg.get("count_by") if isinstance(agg, dict) else None
    return [ak._words(str(g.get("concept")), 1).capitalize() + " " + str(g.get("attribute"))
            for g in by or [] if isinstance(g, dict)]


def _sort_words(sort: Any) -> str:
    if not isinstance(sort, dict):
        return "none"
    return f"{sort.get('attribute')} " + ("descending" if sort.get("dir") == "desc" else "ascending")


def _include_changes(onto: Any, old: list, new: list) -> list[str]:
    left = list(old)
    out, fresh = [], []
    for n in new:
        mate = next((o for o in left if o["name"] == n["name"]), None)
        if mate is None:
            fresh.append(n)
            continue
        left.remove(mate)
        lines = []
        if mate["concept"] != n["concept"]:
            lines.append(f"Records: {_records(mate['concept'])} → {_records(n['concept'])}")
        lines += _node_changes(onto, mate, n, link=True)
        if mate["limit"] != n["limit"]:
            lines.append(f"Limit: {mate['limit']} → {n['limit']}")
        if mate["sort"] != n["sort"]:
            lines.append(f"Sorted by: {_sort_words(mate['sort'])} → {_sort_words(n['sort'])}")
        out += [f"Also show {n['name']}: {line}" for line in lines]
    return out + [f"Also show: {n['name']} added" for n in fresh] + [f"Also show: {o['name']} removed" for o in left]


def plan_changes(onto: Any, old: Any, new: Any) -> list[str]:
    """What a person changed between two plans, in words, in the order of the editor's fields: the kind of answer, what is
    read (kind of record, name or code, states, conditions), what it is limited to, how it is grouped, what is shown with
    each. [] when nothing changed or when either is not a plan."""
    try:
        a, b = oq.normalise_plan(copy.deepcopy(old)), oq.normalise_plan(copy.deepcopy(new))
        out = []
        if a["question_type"] != b["question_type"]:
            out.append(f"Kind of answer: {_ANSWER_KINDS[a['question_type']]} → {_ANSWER_KINDS[b['question_type']]}")
        if a["focus"]["concept"] != b["focus"]["concept"]:
            out.append(f"What: {_records(a['focus']['concept'])} → {_records(b['focus']['concept'])}")
        out += _node_changes(onto, a["focus"], b["focus"])
        out += _limit_changes(onto, a["constraints"], b["constraints"])
        ga, gb = _groups(a), _groups(b)
        if ga != gb:
            out.append("Grouped by: " + (", ".join(gb) if not ga else ", ".join(ga) + " removed" if not gb
                                          else ", ".join(ga) + " → " + ", ".join(gb)))
        return out + _include_changes(onto, a["include"], b["include"])
    except (AttributeError, TypeError, KeyError, ValueError):
        return []
