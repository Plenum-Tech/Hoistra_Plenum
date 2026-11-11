"""What a tool result is, as evidence, and how a failure is said to the user (Workstream A3, 11 Nov 2026).

Before this, a tool answered in one of three shapes - rows, `[]`, or `{"error": <raw HTTP body up to
2,000 chars>, "status_code": 403}` - and nothing told an answer writer which of five different
situations it was looking at. "No overdue work orders" and "the work-order lookup timed out" read the
same to it, and a refused call handed the model the downstream service's own error text to repeat.

Every result now has one state:

  ok            - the lookup ran and returned what was asked
  zero_results  - the lookup ran and found nothing (a real answer: "there are none")
  not_recorded  - the records were found but the field asked about is empty in them
  unavailable   - the source could not be reached or is not available here (a service down, still
                  loading, not part of this space)
  tool_failed   - the lookup itself failed (timeout, server error, a request it could not run)
  denied        - the user is not allowed to read it, or their sign-in has expired

and a failure carries a record for the answer writer - {status, category, user_actionable, message,
next_step} - in business words. The service's own text goes to the log, never to the model or the user.
"""
from __future__ import annotations

import json
import re
from typing import Any

import structlog

log = structlog.get_logger(__name__)

OK, ZERO, NOT_RECORDED, UNAVAILABLE, FAILED, DENIED = (
    "ok", "zero_results", "not_recorded", "unavailable", "tool_failed", "denied")
STATUSES = (OK, ZERO, NOT_RECORDED, UNAVAILABLE, FAILED, DENIED)
#: States that are an answer (the lookup worked), as against a failure to look.
ANSWERED = frozenset({OK, ZERO, NOT_RECORDED})

#: (status, category, user_actionable, message, next_step) by HTTP status code.
_HTTP: dict[int, tuple[str, str, bool, str, str]] = {
    401: (DENIED, "signed_out", True, "Your sign-in has expired, so this could not be read.",
          "Sign in again and ask once more."),
    403: (DENIED, "no_access", True, "You do not have access to this information.",
          "Ask an administrator of your company to give you access."),
    404: (ZERO, "not_found", True, "Nothing was found for that reference.",
          "Check the reference, or ask without it."),
    408: (FAILED, "timeout", True, "The lookup took too long and was stopped.", "Ask again in a moment."),
    409: (FAILED, "conflict", True, "It was changed by someone else, or is already in that state.",
          "Refresh and check its current state."),
    413: (FAILED, "too_large", True, "The request was too large to run.", "Narrow it to one building or period."),
    422: (FAILED, "request", True, "The request could not be run as asked.", "Rephrase it, or narrow it down."),
    400: (FAILED, "request", True, "The request could not be run as asked.", "Rephrase it, or narrow it down."),
    429: (FAILED, "busy", True, "The service is busy right now.", "Ask again in a minute."),
    500: (FAILED, "service_error", True, "The service that holds this information hit an error.",
          "Ask again in a moment; if it keeps happening, tell your administrator."),
    502: (UNAVAILABLE, "service_down", True, "The service that holds this information is not responding.",
          "Ask again in a few minutes."),
    503: (UNAVAILABLE, "service_down", True, "The service that holds this information is not available right now.",
          "Ask again in a few minutes."),
    504: (FAILED, "timeout", True, "The lookup took too long and was stopped.", "Ask again in a moment."),
}

_DENIED_TEXT = re.compile(r"\b(forbidden|not authori[sz]ed|unauthori[sz]ed|permission|not allowed|access denied|"
                          r"not allocated to any building|outside (?:your|the caller'?s?) (?:company|scope)|"
                          r"no_ingest_right|not_your_run)\b", re.I)
_SIGNED_OUT = re.compile(r"\b(token (?:expired|missing|invalid)|not authenticated|sign(?:ed)? ?in again|"
                         r"no (?:bearer )?token|unauthenticated)\b", re.I)
_TIMEOUT = re.compile(r"\b(time ?out|timed out|ReadTimeout|ConnectTimeout|deadline exceeded)\b", re.I)
_UNREACHABLE = re.compile(r"\b(connection (?:refused|reset|error)|ConnectError|name or service not known|"
                          r"circuit (?:is )?open|service unavailable|still loading|not configured)\b", re.I)
_CODE = re.compile(r"\b(4\d\d|5\d\d)\b")


def record(status: str, category: str, user_actionable: bool, message: str, next_step: str,
           **extra: Any) -> dict[str, Any]:
    """A failure as a tool returns it. `error` carries the same business message, so every reader
    that looks for an `error` key still finds a failure."""
    out = {"ok": status in ANSWERED, "status": status, "category": category, "user_actionable": user_actionable,
           "message": message, "next_step": next_step, "error": message}
    out.update(extra)
    return out


def from_status_code(code: int, *, detail: str | None = None, keep_detail: bool = False) -> dict[str, Any]:
    base = _HTTP.get(int(code)) or (_HTTP[500] if int(code) >= 500 else _HTTP[400])
    extra: dict[str, Any] = {"status_code": int(code)}
    # A validation message can tell the user what to change ("start_date must be before end_date");
    # it is kept only when it reads as a sentence, never a trace, SQL or a raw JSON body. keep_detail
    # keeps it for any 4xx: a flow whose own service explains the refusal ("Gate already answered",
    # "Fiix rejected these credentials").
    if detail and (base[1] == "request" or (keep_detail and 400 <= int(code) < 500)):
        d = _plain(detail)
        if d:
            extra["detail"] = d
    return record(*base, **extra)


def _plain(text: str) -> str | None:
    t = str(text or "").strip()
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            t = obj.get("detail") if isinstance(obj.get("detail"), str) else ""
        elif isinstance(obj, str):
            t = obj
        else:
            t = ""
    except ValueError:
        pass
    t = " ".join(str(t).split())
    if not t or len(t) > 240 or re.search(r"Traceback|File \"|SELECT |INSERT |plenum_cafm|sqlalchemy|asyncpg|"
                                          r"\bat 0x|Exception|Error:|\{|\}", t):
        return None
    return t


def http_failure(exc: Exception, *, service: str, op: str, keep_detail: bool = False) -> dict[str, Any]:
    """What an agent's tool returns when its call to a service failed. The service's response body
    and the exception go to the log; the model gets the record."""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        body = exc.response.text or ""
        log.error(f"{service}.tool.http_error", operation=op, status_code=code,
                  url=str(exc.request.url) if exc.request else None, body=body[:2000])
        return from_status_code(code, detail=body, keep_detail=keep_detail)
    log.error(f"{service}.tool.error", operation=op, error=str(exc)[:2000], exc_type=type(exc).__name__)
    return from_exception(exc)


def from_exception(exc: BaseException | str) -> dict[str, Any]:
    """A failure from an exception (or its text), in business words."""
    text = str(exc) if not isinstance(exc, str) else exc
    name = "" if isinstance(exc, str) else type(exc).__name__
    if getattr(exc, "for_model", False):
        # Our own refusal written for the model ("call task(<engine>) instead"): the model gets it whole.
        return record(FAILED, "routed_elsewhere", False, "This part is answered from another source.",
                      "Nothing for the user to do.", guidance=text)
    blob = f"{name} {text}"
    found = _classify_text(text, name, blob)
    # A plain sentence our own code wrote ("No company to read vendors for.") tells the model more
    # than the category does; it travels as `detail`. A trace, SQL or a raw body never does.
    d = _plain(text)
    if d and "detail" not in found:
        found["detail"] = d
    return found


def _classify_text(text: str, name: str, blob: str) -> dict[str, Any]:
    if "ValidationError" in name:
        return record(FAILED, "request", True, "The request could not be run as asked.", "Rephrase it, or narrow it down.")
    if "GraphRecursionError" in name or "recursion limit" in text.lower():
        return record(FAILED, "too_many_steps", True, "This needed too many steps to answer in one go.",
                      "Narrow it to one building, status or period and ask again.")
    if _SIGNED_OUT.search(blob):
        return record(*_HTTP[401])
    if _DENIED_TEXT.search(blob):
        return record(*_HTTP[403])
    if _TIMEOUT.search(blob):
        return record(*_HTTP[504])
    if _UNREACHABLE.search(blob) or "ConnectError" in name:
        return record(*_HTTP[503])
    m = _CODE.search(text)
    if m and ("status" in text.lower() or "http" in text.lower()):
        return from_status_code(int(m.group(1)))
    return record(FAILED, "error", True, "The lookup failed.", "Ask again in a moment; if it keeps happening, tell your administrator.")


def user_message(exc: BaseException | str) -> str:
    """One sentence for the user when a whole turn fails: what happened and what to do. Never the
    exception's own text (that is logged by the caller)."""
    r = from_exception(exc)
    return f"{r['message']} {r['next_step']}"


# -- classifying a result -----------------------------------------------------------------------

def _rows(output: Any) -> list[dict[str, Any]] | None:
    if isinstance(output, list):
        return output if all(isinstance(r, dict) for r in output) else None
    if isinstance(output, dict):
        lists = [v for v in output.values() if isinstance(v, list) and all(isinstance(r, dict) for r in v)]
        if lists:
            return max(lists, key=len)
    return None


def _empty(output: Any) -> bool:
    if output is None:
        return True
    if isinstance(output, str):
        return not output.strip()
    if isinstance(output, (list, tuple)):
        return len(output) == 0
    if isinstance(output, dict):
        if not output:
            return True
        if output.get("found") is False:
            return True
        lists = [v for v in output.values() if isinstance(v, list)]
        counts = [output.get(k) for k in ("count", "total", "row_count", "rows_returned") if isinstance(output.get(k), int)]
        if lists and all(len(v) == 0 for v in lists) and all(c == 0 for c in counts):
            return True
        if not lists and counts and all(c == 0 for c in counts):
            return True
    return False


def classify(tool: str, output: Any, error: Any = None, *, question: str = "") -> dict[str, Any]:
    """The state of one tool result: {status, ...} with the failure record for a failure."""
    if isinstance(output, str):
        s = output.strip()
        if s.startswith("{"):
            try:
                output = json.loads(s)
            except ValueError:
                pass
        elif re.match(r"^(Error|Exception)\b[:\s]", s):
            error = error or s
    if error:
        return from_exception(str(error))
    if isinstance(output, dict):
        st = output.get("status")
        if st in STATUSES and st not in ANSWERED:
            return {**record(st, str(output.get("category") or st), bool(output.get("user_actionable", True)),
                             str(output.get("message") or output.get("error") or "The lookup failed."),
                             str(output.get("next_step") or "Ask again in a moment."))}
        if output.get("still_loading"):
            return record(UNAVAILABLE, "still_loading", True, "The records are still being loaded.",
                          "Ask again in a few minutes.")
        failed = output.get("ok") is False or (
            "error" in output and set(output) <= {"error", "status_code", "detail", "message", "ok"})
        if failed:
            code = output.get("status_code")
            if isinstance(code, int):
                return from_status_code(code, detail=str(output.get("detail") or ""))
            return from_exception(str(output.get("error") or output.get("message") or "failed"))
    if _empty(output):
        return {"ok": True, "status": ZERO, "category": "none_found", "user_actionable": True,
                "message": "The lookup ran and found nothing.", "next_step": "Widen the building, period or status."}
    missing = _not_recorded(output, question)
    if missing:
        return {"ok": True, "status": NOT_RECORDED, "category": "field_empty", "user_actionable": True,
                "message": "The records were found, but " + ", ".join(missing) + " is not recorded in them.",
                "next_step": "Record it in the source system, then ask again.", "fields": missing}
    return {"ok": True, "status": OK}


def _not_recorded(output: Any, question: str) -> list[str]:
    """The fields the question asks about that the rows carry as columns but never fill."""
    if not question:
        return []
    from .planner import FIELD_KEYS, fields_asked

    rows = _rows(output)
    if not rows:
        return []
    out = []
    for phrase in fields_asked(question):
        keys = [k for k in FIELD_KEYS[phrase] if any(k in r for r in rows)]
        if keys and not any(r.get(k) not in (None, "", [], {}) for r in rows for k in keys):
            out.append(phrase)
    return out


# -- conflicts ----------------------------------------------------------------------------------

_ID_KEYS = ("code", "id", "reference", "ref", "asset_code", "building_code", "work_order_code", "wo_code",
            "certificate_number", "vendor_code", "name", "building", "vendor", "asset")
_SKIP_FIELDS = re.compile(r"(^id$|_id$|^seq$|^rank$|^row|_at$|date|time|^page)", re.I)


def conflicts(results: dict[str, dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    """Where two tool results disagree about the same thing: the same record (the same identifier
    field and value - its code, id or name) carrying a different figure for the same field. Each
    finding is a sentence for the writer.

    Results that came from the same tool are not compared (two pages of one register are not two
    sources), and a field that changes by nature (dates, ranks, ids) is not compared."""
    seen: dict[tuple[str, str, str], tuple[str, str, Any]] = {}
    out: list[str] = []
    for rid, r in results.items():
        if not r.get("ok") or rid == "previous_answer":
            continue
        rows = _rows(r.get("output")) or []
        tool = str(r.get("tool") or rid)
        for row in rows[:500]:
            ident = next(((k, str(row[k]).strip()) for k in _ID_KEYS if isinstance(row.get(k), (str, int)) and str(row[k]).strip()), None)
            if ident is None:
                continue
            for field, value in row.items():
                # Figures only: a text field such as `status` means different things in different
                # registers (an accreditation's status is not a contract's), so comparing it would
                # report disagreements that are not there.
                if field in _ID_KEYS or _SKIP_FIELDS.search(field) or isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                key = (ident[0], ident[1].lower(), field.lower())
                prev = seen.get(key)
                if prev is None:
                    seen[key] = (tool, rid, value)
                    continue
                ptool, prid, pvalue = prev
                if ptool == tool:
                    continue
                if _same(pvalue, value):
                    continue
                out.append({"record": ident[1], "field": field, "values": [pvalue, value], "sources": [prid, rid],
                            "sentence": f"{ident[1]} has {field} = {pvalue} in [{prid}] but {value} in [{rid}]."})
                if len(out) >= limit:
                    return out
    return out


def _same(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(a)))
    except (TypeError, ValueError):
        return " ".join(str(a).lower().split()) == " ".join(str(b).lower().split())


# -- what the answer writer is told, and checked against -----------------------------------------

#: How each state is said. Shared by the planner's synthesis and the final answer step.
STATE_RULES = """How to speak about each result's state:
- OK: answer from it.
- ZERO RESULTS: the lookup worked and found none - say so plainly for that part ("there are no overdue
  work orders at Bishopsgate"), with the scope it searched. It is an answer, not a failure.
- NOT RECORDED: the records exist but the field asked about is empty in them - say the records do not
  hold it; never call it zero, and never estimate it.
- UNAVAILABLE / FAILED / DENIED: that part could not be read. Name the part of the question it was for,
  say why in the record's own plain words, and give its next step. Never say "none" or "no records" for
  a part whose lookup failed. Never quote an error code, a status number or technical text.
- If every part failed: say what could not be done and the next step, and nothing else.
- CONFLICTS: where two results disagree, say which source says what. Never pick one silently or merge
  them into one figure."""

_SAYS_NONE = re.compile(r"\b(no|none|zero|nothing)\b[^.\n]{0,60}\b(found|records?|recorded|results?|returned|work orders?|"
                        r"certificates?|vendors?|assets?|buildings?|invoices?|meters?)\b|\bthere (?:are|were|is) no\b", re.I)
_SAYS_ACCESS = re.compile(r"\b(access|permission|allowed|sign(?:ed)? ?in|administrator|admin)\b", re.I)
# Case-sensitive: "an exception was raised on the inspection" is plain English; "ReadTimeout" is not.
_TECHNICAL = re.compile(r"\b(Traceback|status_code|[A-Z][A-Za-z]*(?:Error|Exception)\b|[A-Z][a-z]+Timeout\b|"
                        r"[45]\d\d (?:Internal Server Error|Forbidden|Unauthorized|Bad Request|Not Found|Service Unavailable)|"
                        r"asyncpg|sqlalchemy)")


def state_mismatches(answer: str, results: dict[str, dict[str, Any]]) -> list[str]:
    """Where the answer's wording does not match what the lookups actually did. Each is a sentence
    for a rewrite."""
    a = answer or ""
    rs = [r for k, r in results.items() if k not in ("previous_answer", "__plan__") and isinstance(r, dict)]
    out: list[str] = []
    failed = [r for r in rs if not r.get("ok")]
    if rs and len(failed) == len(rs) and _SAYS_NONE.search(a):
        out.append("The answer says nothing was found, but every lookup failed: say the lookup could not be "
                   "done and give the next step - not that there are none.")
    if any(r.get("status") == DENIED for r in failed) and not _SAYS_ACCESS.search(a):
        out.append("A lookup was refused for lack of access: say so in plain words and give the next step.")
    if _TECHNICAL.search(a):
        out.append("Remove the technical error text (codes, exception names, status numbers); say what happened in plain words.")
    return out


# -- the results as the writer reads them -------------------------------------------------------

def _text(v: Any, limit: int) -> str:
    t = v if isinstance(v, str) else json.dumps(v, default=str)
    return t if len(t) <= limit else t[:limit] + f" …[{len(t) - limit} more chars]"


def _label(r: dict[str, Any]) -> str:
    st = r.get("status") or (OK if r.get("ok") else FAILED)
    if st == ZERO:
        return "ZERO RESULTS (the lookup worked and found none)"
    if st == NOT_RECORDED:
        return "NOT RECORDED (" + ", ".join((r.get("failure") or {}).get("fields") or ["the field asked"]) + " is empty in these records)"
    return "OK"


def render(results: dict[str, dict[str, Any]], *, asks: dict[str, str] | None = None,
           per: int = 8000, total: int = 48000) -> tuple[str, list[dict[str, Any]]]:
    """The results as the writer reads them - SUCCEEDED (with each one's state), FAILED (each with
    the part of the question it was for, why in plain words, and the next step), CONFLICTS - and the
    conflicts found. `asks` maps a result id to the sub-question it answers (a plan step's ask)."""
    asks = asks or {}
    ok_parts, failed_parts, used = [], [], 0
    for rid, r in results.items():
        if rid == "__plan__":
            continue
        if rid == "previous_answer":
            ok_parts.append("[previous answer] (the answer this question follows; its figures may be quoted)\n"
                            + _text(r.get("output"), per))
            continue
        what = asks.get(rid) or r.get("tool") or ""
        head = f"[{rid}]" + (f' for "{_text(what, 200)}"' if what else "")
        if r.get("ok"):
            room = per if used < total else 1500
            body = _text(r.get("output"), room)
            used += len(body)
            ok_parts.append(f"{head} - {_label(r)}\n{body}")
        else:
            f = r.get("failure") or {}
            st = str(f.get("status") or r.get("status") or FAILED).upper()
            msg = f.get("message") or r.get("error") or "The lookup failed."
            nxt = f.get("next_step") or "Ask again in a moment."
            why = f" ({f['detail']})" if f.get("detail") else ""
            failed_parts.append(f"{head} - {st}: {msg}{why} Next step for the user: {nxt}")
    found = conflicts(results)
    text = "SUCCEEDED\n" + ("\n\n".join(ok_parts) or "(nothing)")
    text += "\n\nFAILED\n" + ("\n".join(failed_parts) or "(nothing)")
    if found:
        text += "\n\nCONFLICTS\n" + "\n".join("- " + c["sentence"] for c in found)
    return text, found


def outcome(results: dict[str, dict[str, Any]]) -> str:
    """success / partial / failure / none (no lookup ran) for a turn's results."""
    rs = [r for k, r in results.items() if k not in ("previous_answer", "__plan__") and isinstance(r, dict)]
    if not rs:
        return "none"
    bad = sum(1 for r in rs if not r.get("ok"))
    return "success" if bad == 0 else ("failure" if bad == len(rs) else "partial")


def conflict_mismatches(answer: str, found: list[dict[str, Any]]) -> list[str]:
    """A conflict the answer speaks about but gives only one side of."""
    a = (answer or "").lower()
    digits = re.sub(r"[,£$€\s]", "", a)
    out = []
    for c in found:
        if str(c["record"]).lower() not in a:
            continue
        shown = [v for v in c["values"] if re.sub(r"[,£$€\s]", "", str(v)).lower() in digits]
        if len(shown) < 2:
            out.append(f"The sources disagree about {c['record']} ({c['field']}: {c['values'][0]} vs {c['values'][1]}); "
                       "the answer must say which source says what, not give one figure.")
    return out
