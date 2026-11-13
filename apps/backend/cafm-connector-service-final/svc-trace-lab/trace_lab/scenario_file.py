"""Scenario files, and the scenarios stored from them (11 Nov 2026, task plan B2.1 and B2.2).

A scenario is one line of a .jsonl file (or one entry of a JSON array):

    {"scenario_id": "MT-014", "type": "multi_turn",
     "spec_sections": ["multi_turn_continuity", "scope_preservation"],     the sections of the specification it tests
     "role": "property_manager", "space": "ops",
     "scope": {"buildings": ["B-301"], "vendors": [], "period": {"from": "2026-10-01", "to": "2026-10-31"}},
     "faults": [{"service": "ops-intelligence", "path_prefix": "/api/compliance", "fault": "timeout", "times": 1}],
     "grounded_on": [{"table": "buildings", "pk": "..."}], "domain_rules": [{"id": "R-1", "text": "..."}],
     "turns": [{"message": "what is typed",
                "expected": {"plan": [{"call_id": "c1", "domain": "wo_engine", "tool": null, "params": {},
                                       "depends_on": []}],
                             "failure_handling": null, "answer_format": "success",
                             "must_include": [], "must_not_include": [], "answer": null}}]}

The expected outcome is per turn and is written BEFORE the scenario is asked: the plan (which calls; `depends_on`
says which must finish first, so the call order is implied and never written twice), how a failure must be handled,
and which of the four answer formats the answer takes. It is never taken from an answer the model gave.

A file is taken whole or not at all, like a golden set: one bad line refuses the file and every refusal names its
line and field. Stored scenarios are versioned and append-only: an unchanged scenario is left alone, a changed one is
the next version, approving one is the next version marked approved, and the older versions stay because a session
keeps pointing at the version it was asked with.

Pure except for the four functions that read or write tracelab.scenarios. G0: Trace Lab's own tables only.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sqlalchemy import text

from . import conversations, db, eval_store, expected_flags, labelling, runs, spec_doc
from .review_errors import Invalid

TYPES = ("single_domain", "multi_domain", "multi_turn", "failure")
ANSWER_FORMATS = ("success", "partial_success", "denial", "failure")
EVIDENCE_STATES = ("zero_results", "not_recorded", "unavailable", "tool_failed")
FAILURE_STATES = ("partial_failure", "total_failure", "authorization_failure", "evidence_conflict", *EVIDENCE_STATES)
FAULTS = ("timeout", "http_500", "http_429", "http_403")
#: The sections of the specification a scenario can test: the areas of the 11 Nov 2026 task plan (A1 to A4), named
#: the way spec_doc.section_id_of names a heading with that title.
SPEC_SECTIONS = ("final_synthesis", "multi_read_planning", "dependency_order", "context_passing",
                 "scope_preservation", "retry_rules", "partial_failure", "total_failure", "authorization_failure",
                 "missing_evidence", "evidence_conflicts", "multi_turn_continuity", "answer_completeness",
                 "output_format", "few_shot_examples")
SPACES = ("ops", "comp", "assets", "full")
SOURCES = ("generator", "import", "manual")
STATUSES = ("draft", "approved", "retired")
MAX_BYTES = 5_000_000
MAX_SCENARIOS = 2000
MAX_TURNS = 6
MESSAGE_MAX = 4000
ANSWER_MAX = 8000
_CONTENT_KEYS = ("scenario_id", "type", "spec_sections", "role", "space", "scope", "faults", "grounded_on",
                 "domain_rules", "turns")


# ── the shape ───────────────────────────────────────────────────────────────────────────

def _strings(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)


def _expected_problems(where: str, e: Any) -> list[str]:
    if not isinstance(e, Mapping):
        return [f"{where}.expected must be an object: the expected outcome of that turn"]
    out: list[str] = []
    plan = e.get("plan") or []
    seen: list[str] = []
    if not isinstance(plan, list):
        out.append(f"{where}.expected.plan must be a list of calls")
        plan = []
    for i, call in enumerate(plan):
        at = f"{where}.expected.plan[{i}]"
        if not isinstance(call, Mapping):
            out.append(f"{at} must be an object")
            continue
        cid = call.get("call_id")
        if not isinstance(cid, str) or not cid.strip() or len(cid) > 64:
            out.append(f"{at}.call_id must be a short name such as c1")
        elif cid in seen:
            out.append(f"{at}.call_id {cid!r} is used twice in the same turn")
        domain, tool = call.get("domain"), call.get("tool")
        if not any(isinstance(x, str) and x.strip() for x in (domain, tool)):
            out.append(f"{at} must name a domain or a tool")
        if call.get("params") is not None and not isinstance(call.get("params"), Mapping):
            out.append(f"{at}.params must be an object")
        deps = call.get("depends_on") or []
        if not isinstance(deps, list) or any(d not in seen for d in deps):
            out.append(f"{at}.depends_on must name calls listed before it in the same turn")
        if isinstance(cid, str):
            seen.append(cid)
    fmt = e.get("answer_format")
    if fmt not in ANSWER_FORMATS:
        out.append(f"{where}.expected.answer_format must be one of {', '.join(ANSWER_FORMATS)}")
    fh = e.get("failure_handling")
    if fh is not None:
        if not isinstance(fh, Mapping) or fh.get("state") not in FAILURE_STATES:
            out.append(f"{where}.expected.failure_handling.state must be one of {', '.join(FAILURE_STATES)}")
        else:
            failed = fh.get("failed") or []
            if not isinstance(failed, list) or any(f not in seen for f in failed):
                out.append(f"{where}.expected.failure_handling.failed must name calls of that turn's plan")
    for key in ("must_include", "must_not_include"):
        if e.get(key) is not None and not _strings(e.get(key)):
            out.append(f"{where}.expected.{key} must be a list of phrases")
    answer = e.get("answer")
    if answer is not None and (not isinstance(answer, str) or len(answer) > ANSWER_MAX):
        out.append(f"{where}.expected.answer must be text of at most {ANSWER_MAX:,} characters")
    return out


def validate(s: Any, sections: Collection[str] = SPEC_SECTIONS) -> list[str]:
    """What is wrong with one scenario, each problem naming its field. An empty list means it is valid. `sections` are
    the section ids it may cite: the planned ones by default; allowed_sections() adds the loaded specification's."""
    if not isinstance(s, Mapping):
        return ["a scenario must be an object"]
    out: list[str] = []
    if not eval_store.ok_part(eval_store.SCENARIO_RE, s.get("scenario_id")):
        out.append("scenario_id must be 1 to 60 letters, digits, dots, dashes or single underscores, starting with a "
                   "letter or digit, for example MT-014")
    kind = s.get("type")
    if kind not in TYPES:
        out.append(f"type must be one of {', '.join(TYPES)}")
    allowed = sections
    sections = s.get("spec_sections")
    if not isinstance(sections, list) or not sections:
        out.append("spec_sections must name at least one section of the specification")
    else:
        unknown = [x for x in sections if x not in allowed]
        if unknown:
            out.append(f"spec_sections names {', '.join(map(str, unknown))}, which is not a section of the "
                       f"specification; the sections are {', '.join(sorted(allowed))}")
    role = s.get("role")
    if role is not None and (not isinstance(role, str) or not role.strip() or len(role) > 64):
        out.append("role must be a short name such as property_manager")
    if s.get("space") is not None and s.get("space") not in SPACES:
        out.append(f"space must be one of {', '.join(SPACES)}")
    scope = s.get("scope")
    if scope is not None:
        if not isinstance(scope, Mapping):
            out.append("scope must be an object with buildings, vendors and a period")
        else:
            for key in ("buildings", "vendors"):
                if scope.get(key) is not None and not _strings(scope.get(key)):
                    out.append(f"scope.{key} must be a list of names or ids")
            period = scope.get("period")
            if period is not None and (not isinstance(period, Mapping) or any(
                    period.get(k) is not None and not isinstance(period.get(k), str) for k in ("from", "to"))):
                out.append("scope.period must be an object with a from and a to date")
    faults = s.get("faults") or []
    if not isinstance(faults, list):
        out.append("faults must be a list")
    else:
        for i, f in enumerate(faults):
            ok = (isinstance(f, Mapping) and isinstance(f.get("service"), str) and f.get("service")
                  and isinstance(f.get("path_prefix"), str) and f.get("fault") in FAULTS
                  and (f.get("times") is None or (isinstance(f.get("times"), int) and f.get("times") >= 1)))
            if not ok:
                out.append(f"faults[{i}] needs a service, a path_prefix and a fault that is one of {', '.join(FAULTS)}")
    grounded = s.get("grounded_on") or []
    if not isinstance(grounded, list) or any(not isinstance(g, Mapping) for g in grounded):
        out.append("grounded_on must be a list of the rows the scenario names")
    rules = s.get("domain_rules") or []
    if not isinstance(rules, list):
        out.append("domain_rules must be a list")
    else:
        for i, r in enumerate(rules):
            if not (isinstance(r, Mapping) and isinstance(r.get("id"), str) and r.get("id")
                    and isinstance(r.get("text"), str) and r.get("text").strip()):
                out.append(f"domain_rules[{i}] needs an id and a text")
    turns = s.get("turns")
    if not isinstance(turns, list) or not 1 <= len(turns) <= MAX_TURNS:
        out.append(f"turns: a scenario has between 1 and {MAX_TURNS} turns")
        return out
    handled, widest = False, 0
    for i, t in enumerate(turns):
        where = f"turns[{i}]"
        if not isinstance(t, Mapping):
            out.append(f"{where} must be an object with a message and an expected outcome")
            continue
        message = t.get("message")
        if not isinstance(message, str) or not message.strip() or len(message) > MESSAGE_MAX:
            out.append(f"{where}.message must be what is typed, at most {MESSAGE_MAX:,} characters")
        out += _expected_problems(where, t.get("expected"))
        e = t.get("expected") if isinstance(t.get("expected"), Mapping) else {}
        handled = handled or e.get("failure_handling") is not None
        widest = max(widest, len(e.get("plan") or []) if isinstance(e.get("plan"), list) else 0)
    if kind == "multi_turn" and len(turns) < 2:
        out.append("type multi_turn needs two or more turns")
    if kind == "failure" and not handled and not (isinstance(faults, list) and faults):
        out.append("type failure needs a turn whose expected.failure_handling says how the failure must be handled, "
                   "or a fault the answer must recover from")
    if kind == "multi_domain" and widest < 2:
        out.append("type multi_domain needs a turn whose plan has two or more calls")
    return out


def normalise(s: Mapping[str, Any]) -> dict[str, Any]:
    """A valid scenario with every field present, as it is stored, hashed and exported."""
    turns = []
    for t in s["turns"]:
        e = t["expected"]
        fh = e.get("failure_handling")
        turns.append({"message": t["message"].strip(), "expected": {
            "plan": [{"call_id": c["call_id"], "domain": c.get("domain") or None, "tool": c.get("tool") or None,
                      "params": dict(c.get("params") or {}), "depends_on": list(c.get("depends_on") or [])}
                     for c in (e.get("plan") or [])],
            "failure_handling": {"state": fh["state"], "failed": list(fh.get("failed") or [])} if fh else None,
            "answer_format": e["answer_format"],
            "must_include": list(e.get("must_include") or []),
            "must_not_include": list(e.get("must_not_include") or []),
            "answer": e.get("answer")}})
    return {"scenario_id": s["scenario_id"], "type": s["type"], "spec_sections": list(s["spec_sections"]),
            "role": s.get("role"), "space": s.get("space"), "scope": dict(s.get("scope") or {}),
            "faults": [{"service": f["service"], "path_prefix": f["path_prefix"], "fault": f["fault"],
                        "times": f.get("times")} for f in (s.get("faults") or [])],
            "grounded_on": [dict(g) for g in (s.get("grounded_on") or [])],
            "domain_rules": [{"id": r["id"], "text": r["text"]} for r in (s.get("domain_rules") or [])],
            "turns": turns}


def content_hash(s: Mapping[str, Any]) -> str:
    """sha256 of what a scenario says, whatever order its keys came in. Two scenarios with the same hash are the same
    scenario; status, source and who stored it are not part of it."""
    body = {k: s.get(k) for k in _CONTENT_KEYS}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def parse_jsonl(data: bytes, sections: Collection[str] = SPEC_SECTIONS) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A scenario file as (scenarios, errors). Any error means no scenarios: the file is taken whole or not at all.
    Each error is {"line", "error"}; line 0 is the file itself. `sections` as for validate."""
    if len(data) > MAX_BYTES:
        return [], [{"line": 0, "error": f"The file is larger than {MAX_BYTES:,} bytes."}]
    try:
        body = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [{"line": 0, "error": "The file is not UTF-8 text."}]
    entries: list[tuple[int, Any]] = []
    errors: list[dict[str, Any]] = []
    whole: Any = None
    if body.lstrip().startswith("["):
        try:
            whole = json.loads(body)
        except ValueError:
            whole = None
    if isinstance(whole, list):
        entries = list(enumerate(whole, start=1))
    else:
        for n, line in enumerate(body.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entries.append((n, json.loads(line)))
            except ValueError:
                errors.append({"line": n, "error": "This line is not JSON."})
    if not entries and not errors:
        return [], [{"line": 0, "error": "The file has no scenarios in it."}]
    if len(entries) > MAX_SCENARIOS:
        return [], [{"line": 0, "error": f"The file has more than {MAX_SCENARIOS:,} scenarios."}]
    seen: dict[str, int] = {}
    good: list[dict[str, Any]] = []
    for n, obj in entries:
        problems = validate(obj, sections)
        if problems:
            errors.append({"line": n, "error": "; ".join(problems)})
            continue
        sid = obj["scenario_id"]
        if sid in seen:
            errors.append({"line": n, "error": f"scenario_id {sid} is also on line {seen[sid]}."})
            continue
        seen[sid] = n
        good.append(normalise(obj))
    errors.sort(key=lambda e: e["line"])
    return ([], errors) if errors else (good, [])


def export_jsonl(rows: Sequence[Mapping[str, Any]]) -> bytes:
    """Scenarios as a file parse_jsonl reads back to the same scenarios."""
    return "".join(json.dumps({k: r.get(k) for k in _CONTENT_KEYS}, ensure_ascii=False) + "\n"
                   for r in rows).encode("utf-8")


# ── stored scenarios ─────────────────────────────────────────────────────────────────

_COLS = ("sc.scenario_id, sc.version, sc.organization_id::text AS organization_id, sc.type, sc.spec_sections, sc.role,"
         " sc.space, sc.turns::text AS turns, sc.scope::text AS scope, sc.faults::text AS faults,"
         " sc.grounded_on::text AS grounded_on, sc.domain_rules::text AS domain_rules, sc.spec_version,"
         " sc.spec_hashes::text AS spec_hashes, sc.content_sha256, sc.status, sc.source, sc.created_at")
_LATEST = "sc.version = (SELECT max(x.version) FROM tracelab.scenarios x WHERE x.scenario_id = sc.scenario_id)"
_INSERT = text(
    "INSERT INTO tracelab.scenarios (scenario_id, version, organization_id, type, spec_sections, role, space, turns,"
    " scope, faults, grounded_on, domain_rules, spec_version, spec_hashes, content_sha256, status, source, created_by,"
    " created_by_email) VALUES (:id, :version, CAST(:org AS uuid), :type, CAST(:sections AS text[]), :role, :space,"
    " CAST(:turns AS jsonb), CAST(:scope AS jsonb), CAST(:faults AS jsonb), CAST(:grounded AS jsonb),"
    " CAST(:rules AS jsonb), :spec_version, CAST(:spec_hashes AS jsonb), :sha, :status, :source, CAST(:by AS uuid),"
    " :email)")


def _out(r: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(r)
    for key in ("turns", "scope", "faults", "grounded_on", "domain_rules", "spec_hashes"):
        row[key] = json.loads(row[key]) if isinstance(row[key], str) else row[key]
    row["spec_sections"] = list(row["spec_sections"] or [])
    row["created_at"] = row["created_at"].isoformat() if row.get("created_at") else None
    row["stale"] = []
    return row


async def latest(conn: Any, *, scenario_ids: Sequence[str] | None = None, type: str | None = None,  # noqa: A002
                 section: str | None = None, status: str | None = None,
                 org_scope: tuple[str, dict[str, Any]] | None = None, stale: bool = False) -> list[dict[str, Any]]:
    """The current version of each scenario, by id. Each row's "stale" lists the sections it cites whose text has
    changed, gone or arrived since it was written (`stale` keeps only the rows that have any); "uncovered" lists the
    cited sections the current specification does not have."""
    _version, hashes = await spec_doc.current_hashes(conn)
    where, p = [_LATEST], {}
    if scenario_ids is not None:
        where.append("sc.scenario_id = ANY(CAST(:ids AS text[]))")
        p["ids"] = list(scenario_ids)
    if type:
        where.append("sc.type = :type")
        p["type"] = type
    if section:
        where.append(":section = ANY(sc.spec_sections)")
        p["section"] = section
    if status:
        where.append("sc.status = :status")
        p["status"] = status
    if org_scope is not None:
        where.append(org_scope[0])
        p.update(org_scope[1])
    rows = (await conn.execute(text(
        f"SELECT {_COLS} FROM tracelab.scenarios sc WHERE {' AND '.join(where)} ORDER BY sc.scenario_id"),
        p)).mappings().all()
    out = []
    for r in rows:
        row = _out(r)
        row["stale"] = spec_doc.stale_sections(row["spec_hashes"], hashes, row["spec_sections"])
        row["uncovered"] = spec_doc.uncovered_sections(row["spec_sections"], hashes)
        if not stale or row["stale"]:
            out.append(row)
    return out


def _visible(m: labelling.Member) -> tuple[str, dict[str, Any]] | None:
    """Which scenarios a member may see: every one for the platform team and a consultant; otherwise their own
    company's and the ones that name no company."""
    if m.all_companies:
        return None
    return "(sc.organization_id IS NULL OR sc.organization_id = CAST(:scope_org AS uuid))", {"scope_org": m.org}


async def list_scenarios(m: labelling.Member, *, type: str | None = None, section: str | None = None,  # noqa: A002
                         status: str | None = None, stale: bool = False) -> list[dict[str, Any]]:
    """The current version of every scenario the caller may see. Each row carries "stale": the cited sections of the
    specification that have changed since that version was written."""
    labelling.require_active(m)
    async with conversations._lab_read() as conn:
        rows = await latest(conn, type=type, section=section, status=status, org_scope=_visible(m), stale=stale)
        # How many turns asked with each scenario's current version have their expected outcome flagged as wrong (in
        # the sessions the caller may see): a corrected version starts at 0.
        scope, p = runs._scope(m, "ss")
        flagged = {(r[0], r[1]): int(r[2]) for r in (await conn.execute(text(
            "SELECT ss.scenario_id, ss.scenario_version, count(*) FROM tracelab.traces t"
            f" JOIN tracelab.sessions ss ON ss.id = t.session_id WHERE {scope} AND {expected_flags.flagged_sql('t')}"
            " GROUP BY 1, 2"), p)).all()}
    for row in rows:
        row["expected_flagged"] = flagged.get((row["scenario_id"], row["version"]), 0)
    return rows


def _stamped(row: Mapping[str, Any], spec_version: str | None, hashes: Mapping[str, str]) -> dict[str, Any]:
    """The scenario with the current specification stamped on it: its version, and the hash of each cited section
    that the specification has."""
    return {**row, "spec_version": spec_version,
            "spec_hashes": {s: hashes[s] for s in row["spec_sections"] if s in hashes}}


async def _store(conn: Any, m: labelling.Member, row: Mapping[str, Any], *, version: int, status: str, source: str,
                 org: str | None) -> None:
    await conn.execute(_INSERT, {
        "id": row["scenario_id"], "version": version, "org": org, "type": row["type"],
        "sections": list(row["spec_sections"]), "role": row.get("role"), "space": row.get("space"),
        "turns": eval_store._dumps(row["turns"]), "scope": eval_store._dumps(row.get("scope") or {}),
        "faults": eval_store._dumps(row.get("faults") or []), "grounded": eval_store._dumps(row.get("grounded_on") or []),
        "rules": eval_store._dumps(row.get("domain_rules") or []), "spec_version": row.get("spec_version"),
        "spec_hashes": eval_store._dumps(row.get("spec_hashes") or {}), "sha": content_hash(row), "status": status,
        "source": source, "by": m.user_id, "email": m.email})


def _taken(m: labelling.Member, old: Mapping[str, Any] | None, row: Mapping[str, Any]) -> bool:
    """Whether this member may not store `row` under its id, because the id is another owner's.

    A scenario id is one name on every machine and for every company (it is part of the session id), so an id keeps
    the owner of its first version. The platform team and a consultant may add a version to any scenario (it stays
    with its owner). A company may add one only to its own; a scenario that belongs to every company it may store
    again unchanged (an exported file read back in), and another company's never, changed or not."""
    if old is None or m.all_companies or old["organization_id"] == m.org:
        return False
    return old["organization_id"] is not None or old["content_sha256"] != content_hash(row)


async def allowed_sections() -> set[str]:
    """The section ids a scenario may cite: the planned ones (SPEC_SECTIONS) and every section of the specification
    loaded now (spec_doc.load), such as the real SPEC.md's rules s_1_overdue_reported_as_none ... s_11_..."""
    async with conversations._lab_read() as conn:
        _version, hashes = await spec_doc.current_hashes(conn)
    return set(SPEC_SECTIONS) | set(hashes)


async def save_scenarios(m: labelling.Member, scenarios: Sequence[Mapping[str, Any]], *, source: str,
                         status: str = "draft") -> dict[str, int]:
    """Store scenarios: version 1 for a new id, the next version when what it says has changed, nothing when it has
    not. Curators only. Invalid scenarios, and ids that belong to someone else, are refused together, before anything
    is written."""
    runs.require_curator(m)
    if source not in SOURCES or status not in ("draft", "approved"):
        raise Invalid("bad_request", "That is not a way a scenario can be stored.")
    allowed = await allowed_sections()
    problems = [{"line": i, "error": "; ".join(p)} for i, s in enumerate(scenarios, start=1)
                if (p := validate(s, allowed))]
    ids = [s.get("scenario_id") for s in scenarios if isinstance(s, Mapping)]
    problems += [{"line": 0, "error": f"scenario_id {x} is given twice."} for x in sorted({i for i in ids
                                                                                         if ids.count(i) > 1})]
    if problems:
        raise Invalid("bad_scenarios", "Some scenarios need fixing before they can be stored.", errors=problems[:200])
    rows = [normalise(s) for s in scenarios]
    out = {"added": 0, "new_versions": 0, "unchanged": 0}
    async with db.lab_engine().begin() as conn:
        spec_version, hashes = await spec_doc.current_hashes(conn)
        have = {r["scenario_id"]: r for r in await latest(conn, scenario_ids=[r["scenario_id"] for r in rows])}
        taken = sorted(r["scenario_id"] for r in rows if _taken(m, have.get(r["scenario_id"]), r))
        if taken:
            more = f" and {len(taken) - 10} more" if len(taken) > 10 else ""
            raise Invalid("scenario_id_taken",
                          f"These scenario ids are already used by scenarios you cannot change: {', '.join(taken[:10])}"
                          f"{more}. Give yours other ids; for generated scenarios, use another seed.")
        for row in rows:
            old = have.get(row["scenario_id"])
            # Unchanged means it says the same. Storing the same words again is not a person checking them against
            # the specification, so it never clears an out-of-date flag: a changed scenario or an approval does.
            if old is not None and old["content_sha256"] == content_hash(row) and old["status"] != "retired":
                out["unchanged"] += 1
                continue
            await _store(conn, m, _stamped(row, spec_version, hashes), version=(old["version"] + 1) if old else 1,
                         status=status, source=source, org=old["organization_id"] if old else m.org)
            out["new_versions" if old else "added"] += 1
    return out


async def approve(m: labelling.Member, scenario_ids: Sequence[str]) -> dict[str, Any]:
    """Mark the current version of each scenario approved, as its next version. Curators only. A company approves
    its own scenarios; one that belongs to every company is approved by the platform team ("shared" lists those)."""
    runs.require_curator(m)
    wanted = list(dict.fromkeys(str(x) for x in scenario_ids))[:MAX_SCENARIOS]
    out: dict[str, Any] = {"approved": 0, "already": 0, "missing": [], "shared": []}
    async with db.lab_engine().begin() as conn:
        spec_version, hashes = await spec_doc.current_hashes(conn)
        have = {r["scenario_id"]: r for r in await latest(conn, scenario_ids=wanted, org_scope=_visible(m))}
        for sid in wanted:
            old = have.get(sid)
            if old is None:
                out["missing"].append(sid)
            elif not m.all_companies and old["organization_id"] != m.org:
                out["shared"].append(sid)
            elif old["status"] == "approved" and not old["stale"]:
                out["already"] += 1
            else:
                # Approving is a person saying the expected outcome is right against the specification as it reads
                # now, so the new version is stamped with it.
                await _store(conn, m, _stamped(old, spec_version, hashes), version=old["version"] + 1,
                             status="approved", source=old["source"], org=old["organization_id"])
                out["approved"] += 1
    return out
