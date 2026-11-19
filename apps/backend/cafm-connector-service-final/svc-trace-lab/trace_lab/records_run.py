"""A faithful re-run of a records query (18 Nov 2026, Hussain and Bala).

Hoistra's records tool (svc-deepagents ontology_qa.answer_question) turns a plan into values in a fixed sequence of
steps: it groups, swaps empty links, applies the page's filters, pins live vendors, names the filters' values, adds the
codes the question mentions, resolves every name to records, reads the figure a record states itself, points a date at
a column that holds dates, notes which statuses mean finished, adds the statuses and certificates it always reads,
validates, and only then executes - and afterwards reads what is still open. Trace Lab reads the expected values by
running those SAME steps, in the same order, on Hoistra's own functions (imported, never edited), so "open", "overdue"
and a building that was added by hand mean here exactly what they mean to Hoistra.

    run_query(plan, question=, org=)                  Hoistra's steps, the question as asked
    run_query(plan, ..., mode="as_written")           only resolve, stated figures, dates, validate, execute

The read is one READ ONLY transaction through plenum_scoped for one company (and, for a person's own view, their
buildings); every query it makes is kept as a receipt with its parameters and rows so anyone can check it in psql. Trace
Lab never writes to the records and never builds Hoistra's map (a build is ~15 minutes of reads): the map is only LOADED
from the file the orchestrator built, and NotReady says when there is none.

test_trace_lab_records_run.py reads Hoistra's source and fails when the steps change, saying what to update here.
"""
from __future__ import annotations

import copy
import os
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from src.agents import ontology_qa as oq

#: What answer_question calls between planning and the evidence, in order (apply_pinned twice, as it does).
STEPS = ("ensure_grouping", "swap_empty_links", "apply_pinned", "apply_pinned", "pin_live_vendors",
         "names_from_filters", "add_missing_codes", "resolve_plan", "stated_count_notes", "fill_date_filters",
         "finished_states_note", "ensure_status_includes", "ensure_compliance_includes", "validate_plan", "execute_plan")
#: The steps a plan that is already written the way the records want it still needs.
AS_WRITTEN = ("resolve_plan", "stated_count_notes", "fill_date_filters", "validate_plan", "execute_plan")
MODES = ("hoistra", "as_written")


class NotReady(RuntimeError):
    """Hoistra's map of the database has not been built yet, so no key can be read now."""


def hoistra_steps(source: str, names: set[str]) -> list[str]:
    """The functions of `names` that `source` calls between the first plan_question( and the first evidence(, in order."""
    start = re.search(r"\bplan_question\s*\(", source)
    body = source[start.end():] if start else source
    end = re.search(r"\bevidence\s*\(", body)
    body = body[:end.start()] if end else body
    return [m.group(1) for m in re.finditer(r"\b(\w+)\s*\(", body) if m.group(1) in names]


async def load_map(oq_module: Any = None) -> Any:
    """Hoistra's map of the database, loaded from the file the orchestrator built; NotReady when there is none."""
    from sqlalchemy import text

    from . import db

    if db._main is None:
        raise NotReady("Trace Lab's read-only connection to the records is not installed")
    o = oq_module or oq
    factory = o._session_factory()
    async with factory() as s:
        async with s.begin():
            await s.execute(text("SET TRANSACTION READ ONLY"))
            cat = await o.load_catalog(s, o.BASE_SCHEMA)
    fp = o.schema_fingerprint(cat)
    if fp not in o._ONTOLOGY and not os.path.exists(o._cache_path(fp)):
        raise NotReady("Hoistra's map of the records is not built yet")
    return await o.get_ontology()


class ReceiptReader(oq.ScopedReader):
    """Hoistra's scoped reader, keeping every query it runs as a receipt: the SQL, its parameters, how many rows came
    back, the first ROWS_KEPT of them, and the same query as psql text for the company it ran for."""

    def __init__(self, principal):
        super().__init__(principal)
        self.org = str(getattr(principal, "organization_id", None) or "")
        # The buildings the read is limited to, exactly as scope_settings set them: the psql text must read the same rows.
        self.buildings = dict(self.settings).get("app.udr_buildings", "")
        self.receipts: list[dict] = []

    async def fetch(self, sql, p, label=""):
        from . import answer_key as ak

        rows = await super().fetch(sql, p, label)
        values = p.d if isinstance(p, oq.Params) else (p or {})
        receipt = {"id": "q" + str(len(self.receipts) + 1), "label": label or "query", "sql": sql,
                   "params": ak.jsonable(dict(values)), "row_count": len(rows), "rows": ak.jsonable(rows[:ak.ROWS_KEPT])}
        receipt["psql"] = ak.psql_text(receipt, org=self.org, buildings=self.buildings)
        self.receipts.append(receipt)
        return rows


async def run_query(plan: dict, *, question: str, org: str, mode: str = "hoistra", scope: dict | None = None) -> dict:
    """One records plan run the way Hoistra's records tool runs it, for one company, read-only.

    mode "hoistra" runs every step of STEPS, then reads what is still pending (work orders), as answer_question does;
    mode "as_written" runs only AS_WRITTEN. scope is {"building_ids": list | None, "email": str | None}: None (or no
    building_ids) is the whole company. Returns found, unresolved (names the records do not hold, when found is false),
    plan (as run, after the steps), notes (what the steps said, in Hoistra's order), receipts, read_at, result (what
    execute_plan returned) and pending (hoistra mode only). A plan the records cannot run is a ValueError."""
    if mode not in MODES:
        raise ValueError("mode must be one of " + ", ".join(MODES))
    from . import answer_key as ak

    hoistra = mode == "hoistra"
    onto = await load_map()
    scope = scope or {}
    principal = SimpleNamespace(role="admin", organization_id=org, building_ids=scope.get("building_ids"),
                                email=scope.get("email"))
    plan = oq.normalise_plan(copy.deepcopy(plan))
    grouped: list[str] = []
    if hoistra:
        grouped.extend(oq.ensure_grouping(onto, plan, question))
        grouped.extend(oq.swap_empty_links(onto, plan))
        # The page's filters are not recorded in the trace, so none are applied; Hoistra calls it twice.
        grouped.extend(oq.apply_pinned(onto, plan, None))
        grouped.extend(oq.apply_pinned(onto, plan, None))
        grouped.extend(oq.pin_live_vendors(onto, plan, question))
        grouped.extend(oq.names_from_filters(onto, plan))
    async with ReceiptReader(principal) as r:
        if hoistra:
            plan = await oq.add_missing_codes(r, onto, plan, question)
        # As written, the plan is not refocused on what the question says: it is read as it stands.
        plan, notes, unresolved = await oq.resolve_plan(r, onto, plan, question=question if hoistra else None)
        read_at = datetime.now(timezone.utc).isoformat()
        if unresolved:
            return {"found": False, "unresolved": list(unresolved), "plan": ak.jsonable(plan), "notes": list(notes),
                    "receipts": r.receipts, "read_at": read_at, "result": None, "pending": None}
        notes = list(notes) + grouped
        notes.extend(await oq.stated_count_notes(r, onto, plan))
        notes.extend(await oq.fill_date_filters(r, onto, plan))
        if hoistra:
            notes.extend(oq.finished_states_note(onto, plan))
            added = [] if plan.get("aggregate") else (oq.ensure_status_includes(onto, plan)
                                                      + oq.ensure_compliance_includes(onto, plan))
            if added:
                notes.append("Also read, as always for this kind of record: " + ", ".join(added))
        errors = oq.validate_plan(onto, plan)
        if errors:
            raise ValueError("the plan does not fit this database: " + "; ".join(errors))
        result = await oq.execute_plan(r, onto, plan)
        pending = await oq.pending_context(r, onto, plan) if hoistra else None
        return {"found": True, "unresolved": [], "plan": ak.jsonable(plan), "notes": notes, "receipts": r.receipts,
                "read_at": read_at, "result": result, "pending": pending}
