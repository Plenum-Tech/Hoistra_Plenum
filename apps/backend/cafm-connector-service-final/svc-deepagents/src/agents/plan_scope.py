"""One scope for a plan, and every call checked against it before it runs (Workstream A2, 11 Nov 2026).

Company and buildings were enforced in SQL by the services, and the turn's building and period were a
softer working set the tools fell back on - but nothing looked at what a plan step actually asked a
tool for. A step could name another company's id, a building the user is not allocated to, or drift
from the conversation's building and period, and the call went out; the services' own scoping was the
only thing between it and the data.

`build()` gathers the scope once, from the signed-in caller (company, role, the buildings they may
read) and the turn's working set (the conversation's building and period - agents/thread_scope.py).
`check()` is asked before each tool step: a call outside the caller's access is DENIED, a call that
drifts from the conversation's building or period is not run either, and both come back as the
step's failure record (agents/evidence.py), so the answer says plainly which part was not read.
The services keep their own scoping; this is the plan's.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import evidence as ev
from . import name_match

_ORG_KEYS = ("organization_id", "org_id", "company_id")
_BUILDING_ID_KEYS = ("building_id",)
_BUILDING_NAME_KEYS = ("building_name", "building")


@dataclass
class PlanScope:
    organization_id: str | None = None
    role: str | None = None
    #: The buildings the caller may read: [{id, name, building_code}], or None for every building.
    allowed: list[dict[str, Any]] | None = None
    #: The conversation's building and period (the turn's working set), a hard filter when set.
    building: dict[str, Any] = field(default_factory=dict)
    period: dict[str, Any] | None = None

    def describe(self) -> dict[str, Any]:
        return {"organization_id": self.organization_id, "role": self.role,
                "buildings_allowed": None if self.allowed is None else len(self.allowed),
                "building": self.building.get("building_name") or self.building.get("building_code") or None,
                "period": (self.period or {}).get("label")}

    # -- the check --------------------------------------------------------------------------

    def check(self, target: str, args: dict[str, Any] | None) -> dict[str, Any] | None:
        """None when the call stays inside the scope, else the failure record for the step."""
        args = args or {}
        for k in _ORG_KEYS:
            v = args.get(k)
            if v not in (None, "") and self.organization_id and str(v) != str(self.organization_id):
                return ev.record(ev.DENIED, "other_company", True, "This part asked for another company's records.",
                                 "Ask about your own company's records.", scope_rule="company", argument=k)
        bid = next((str(args[k]) for k in _BUILDING_ID_KEYS if args.get(k) not in (None, "")), None)
        bname = next((str(args[k]) for k in _BUILDING_NAME_KEYS if isinstance(args.get(k), str) and args[k].strip()), None)
        if self.allowed is not None and (bid or bname) and not self._allowed(bid, bname):
            return ev.record(ev.DENIED, "no_access", True, "This part is about a building you do not have access to.",
                             "Ask an administrator of your company to give you access to it.",
                             scope_rule="buildings_allowed", argument=bid or bname)
        if self.building and (bid or bname) and not self._is_conversation_building(bid, bname):
            return ev.record(ev.FAILED, "out_of_scope", True,
                             "This part was planned for a different building from the one you asked about, so it was not run.",
                             "Ask about that building on its own if you need it.",
                             scope_rule="conversation_building", argument=bid or bname)
        p = args.get("period")
        if self.period and isinstance(p, str) and p.strip() and not self._same_period(p):
            return ev.record(ev.FAILED, "out_of_scope", True,
                             "This part was planned for a different period from the one you asked about, so it was not run.",
                             "Ask about that period on its own if you need it.",
                             scope_rule="conversation_period", argument=p)
        return None

    def _allowed(self, bid: str | None, bname: str | None) -> bool:
        for b in self.allowed or []:
            if bid and str(b.get("id")) == bid:
                return True
            for label in (b.get("name"), b.get("building_code")):
                if bname and label and (name_match.compact(label) == name_match.compact(bname)
                                         or name_match.names_match(bname, str(label), ())
                                         or name_match.names_match(str(label), bname, ())):
                    return True
        return False

    def _is_conversation_building(self, bid: str | None, bname: str | None) -> bool:
        b = self.building
        if bid and b.get("building_id"):
            return str(b["building_id"]) == bid
        if bname:
            from .energy_intelligence_agent import BUILDING_KIND_WORDS

            for label in (b.get("building_name"), b.get("building_code")):
                if label and (name_match.compact(label) == name_match.compact(bname)
                              or name_match.names_match(bname, str(label), BUILDING_KIND_WORDS)
                              or name_match.names_match(str(label), bname, BUILDING_KIND_WORDS)):
                    return True
            return not (b.get("building_name") or b.get("building_code"))
        return True

    def _same_period(self, p: str) -> bool:
        from . import thread_scope

        want = self.period or {}
        if want.get("key") and p.strip() == want["key"]:
            return True
        got = thread_scope.parse_period(p.replace("_", " "))
        if got is None:
            return True  # not a period this check can read; the tool's own filter decides
        return got.get("from") == want.get("from") and got.get("to") == want.get("to")


def build() -> PlanScope:
    """The scope of the plan about to run: the signed-in caller's and the turn's."""
    from ..http_client import caller_organization_id
    from ..services.principal import caller_principal
    from . import thread_scope

    p = caller_principal.get()
    org = caller_organization_id.get() or (str(p.organization_id) if p and p.organization_id else None)
    allowed = None
    if p is not None and p.building_ids is not None:
        named = {str(b.get("id")): b for b in (p.buildings or ()) if isinstance(b, dict)}
        allowed = [named.get(str(i), {"id": str(i)}) for i in p.building_ids]
    ws = thread_scope.active_scope.get() or {}
    building = {k: ws.get(k) for k in ("building_name", "building_id", "building_code") if ws.get(k)}
    if ws.get("buildings"):
        building = {}  # several buildings in play: no single building to hold the plan to
    return PlanScope(organization_id=org, role=p.role if p else None, allowed=allowed, building=building,
                     period=ws.get("period") if isinstance(ws.get("period"), dict) else None)
