"""Trace Lab's allow-list proxy (G0 addendum 2.5): the only way a golden run's chat reaches another service.

In the trace-lab container every downstream base URL svc-deepagents reads (UDR_BASE_URL,
OPERATIONS_INTELLIGENCE_BASE_URL, WO_MANAGEMENT_BASE_URL, DOC_RAG_BASE_URL) points at
http://127.0.0.1:8010/__proxy/<service>. A request is forwarded to the real service only when it reads:
GET, HEAD and OPTIONS, plus the read-only POSTs in ALLOW_TABLE, each with a rule for its body. Anything
else is refused with 403 refused_by_trace_lab and recorded against the golden case that is running, which
then reads "degraded", never "wrong". There is no REPLAY flag: the chat runs exactly as it does live and
the proxy decides what may leave the process.

This part (check, ALLOW_TABLE) is pure: no I/O, no application code. The routes and the per-case refusal
log follow below.
"""
from __future__ import annotations

# The whole module's imports. Phase 3 Task 4 appends the routes and the refusal log to the end of this
# file and uses the ones this part does not (none of them does I/O at import).
import hashlib
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

log = structlog.get_logger(__name__)

#: settings.Settings.upstreams keys, and the path segment after /__proxy/.
SERVICES: tuple[str, ...] = ("udr", "oi", "wom", "docrag")
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
REFUSED_REASON = "refused_by_trace_lab"
SERVICE_LABELS: dict[str, str] = {
    "udr": "the data service",
    "oi": "the operations service",
    "wom": "the work-order service",
    "docrag": "the document service",
}


@dataclass(frozen=True)
class AllowRule:
    """One read-only POST: the service, the method, the whole path (a regex), a body rule (None when the
    body is fine, else why not), a plain label and the code that shows it only reads."""
    service: str
    method: str
    path: re.Pattern[str]
    body_ok: Callable[[Any], str | None]
    label: str
    evidence: str


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def label_for(service: str | None) -> str:
    """The plain sentence a reviewer sees for a refusal (no route, table or service code names)."""
    return f"Asked {SERVICE_LABELS.get(service or '', 'another service')} to change something; a test run only reads"


# ── SQL text: the custom SELECT rule ──────────────────────────────────────────────────────────

# PostgreSQL's scanner: a dollar tag starts with a letter, '_' or any high-bit byte (\200-\377) and goes on
# with those or digits (dolq_start, dolq_cont), so $€$ and $_é9$ open a dollar-quote just as $a$ does.
_DOLLAR_TAG = re.compile(r"\$([A-Za-z_\u0080-\U0010FFFF][A-Za-z0-9_\u0080-\U0010FFFF]*)?\$")
# Between two quoted pieces, spaces, newlines and -- comments (with a newline) continue one string.
_CONTINUE_GAP = re.compile(r"(?:[ \t\n\r\f\v]|--[^\n\r]*)*")
#: strip_sql's marker for a quote PostgreSQL versions read differently (select_is_read refuses it).
_UNCLEAR = " unclear&quote "
_LINE_END = re.compile(r"[\n\r]")


def _ident_char(ch: str) -> bool:
    """A character that can be part of a PostgreSQL identifier ('$' included), so a '$' or a quote right
    after one is part of that name, not the start of a dollar-quote, an E-string or a U& name. PostgreSQL's
    scanner treats every high-bit byte (\\200-\\377) as an identifier character, so every non-ASCII
    character counts here, whether or not Python calls it alphanumeric (the euro sign, the middle dot)."""
    return ch.isalnum() or ch in "_$" or ord(ch) >= 0x80


def _ident_start(ch: str) -> bool:
    """A character that can start a PostgreSQL identifier (ident_start: a letter, '_' or a high-bit byte).
    A run of identifier characters that starts with a digit or '$' is a number or a $n parameter, not a name."""
    return (ch.isascii() and ch.isalpha()) or ch == "_" or ord(ch) >= 0x80


def strip_sql(sql: str, keep_identifiers: bool = False) -> str:
    """Comments, 'literals', $$bodies$$ and "identifiers" removed; lower case; whitespace collapsed.
    With keep_identifiers the lower-cased text of each "identifier" stays (a quoted function name is still
    a function call); a U&"..." name becomes the marker 'u&ident' because its escapes can spell anything.
    A quote that PostgreSQL versions read differently (a number or a $n parameter running straight into
    it, or a string continued across a newline after an E'...' piece) becomes the marker 'unclear&quote'.

    `run` is the run of identifier characters just before the current one (empty after anything else, a
    comment or a quoted piece included), so a '$' or a quote is judged by the token it follows, not by the
    raw character: x$a$ is a name, while $a$x$a$$b$ is two dollar-quotes."""
    s, out, i = sql or "", [], 0
    n = len(s)
    run, last, amp_run = "", "", ""
    str_end, str_escaped = -1, False
    while i < n:
        ch = s[i]
        if s.startswith("--", i):
            # A line comment ends at a newline or a carriage return (non_newline = [^\n\r]).
            m = _LINE_END.search(s, i)
            i = n if m is None else m.end()
            out.append(" ")
            run, last = "", ""
        elif s.startswith("/*", i):
            # Block comments nest in PostgreSQL: the comment ends at the */ that matches the first /*,
            # and an unclosed one runs to the end of the text.
            depth, j = 1, i + 2
            while j < n and depth:
                if s.startswith("/*", j):
                    depth += 1
                    j += 2
                elif s.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            i = j
            out.append(" ")
            run, last = "", ""
        elif ch == "'":
            # E'...' honours backslash escapes (E'\'' is one quote); a plain '...' does not.
            escaped = run in ("e", "E")
            unclear = bool(run) and not _ident_start(run[0]) and run[-1] in "eE"
            if not run and str_escaped and str_end >= 0:
                gap = s[str_end:i]
                if _CONTINUE_GAP.fullmatch(gap) and ("\n" in gap or "\r" in gap):
                    unclear = True  # this piece continues an E'...' string, so it keeps the E rules
            j = i + 1
            while j < n:
                if escaped and s[j] == "\\":
                    j += 2
                    continue
                if s[j] == "'":
                    if j + 1 < n and s[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            out.append(_UNCLEAR if unclear else "''")
            str_end, str_escaped = i, escaped or (str_escaped and unclear)
            run, last = "", ""
        elif ch == '"':
            j = i + 1
            while j < n:
                if s[j] == '"':
                    if j + 1 < n and s[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            inner = s[i + 1:j].replace('""', '"')
            i = j + 1
            # U&"..." when the & follows a lone u (or a number running into u): its escapes can spell any name.
            unicode_name = (last == "&" and amp_run[-1:] in ("u", "U")
                            and (len(amp_run) == 1 or not _ident_start(amp_run[0])))
            if unicode_name:
                out.append(" u&ident ")
            else:
                out.append(f" {inner} " if keep_identifiers else " ident ")
            run, last = "", ""
        elif ch == "$" and not (run and _ident_start(run[0])) and _DOLLAR_TAG.match(s, i):
            tag = _DOLLAR_TAG.match(s, i).group(0)
            j = s.find(tag, i + len(tag))
            i = n if j == -1 else j + len(tag)
            # After a number or a $n parameter, versions disagree on whether this opens a dollar-quote.
            out.append(_UNCLEAR if run else " $$ ")
            run, last = "", ""
        else:
            out.append(ch)
            if ch == "&":
                amp_run = run
            run = run + ch if _ident_char(ch) else ""
            last = ch
            i += 1
    return " ".join("".join(out).lower().split())


_SELECT_WRITE = re.compile(r"\b(insert|update|delete|merge|truncate|copy|create|alter|drop|grant|revoke|call|do)\b")
_SELECT_LOCK = re.compile(r"\bfor\s+(no\s+key\s+)?update\b|\bfor\s+(key\s+)?share\b")
_SELECT_SIDE_EFFECT = re.compile(r"\b(nextval|setval|set_config|pg_advisory\w*|pg_try_advisory\w*|lo_\w+|dblink\w*|"
                                 r"pg_terminate_backend|pg_cancel_backend|pg_notify|pg_sleep\w*|pg_reload_conf|"
                                 r"pg_rotate_logfile|pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|"
                                 r"txid_current|pg_current_xact_id|"
                                 # these run a query given as text, side effects included
                                 r"query_to_xml\w*|cursor_to_xml\w*|ts_stat|ts_rewrite)\s*\(")


def select_is_read(sql: str) -> str | None:
    """None when a custom SELECT only reads, else why it is refused. svc-udr itself takes only text that
    starts with SELECT and has no ';' inside (database_service.py execute_select); this also refuses row
    locks, SELECT INTO, writing words and functions with side effects."""
    s = strip_sql(sql)
    while s.endswith(";"):
        s = s[:-1].rstrip()
    if not s.startswith("select"):
        return "not a SELECT"
    if ";" in s:
        return "more than one statement"
    if _SELECT_LOCK.search(s):
        return "it locks rows"
    m = _SELECT_WRITE.search(s)
    if m:
        return f"it contains {m.group(1).upper()}"
    if re.search(r"\binto\b", s):
        return "SELECT INTO writes a table"
    if "u&ident" in s:
        return "it uses an escaped name"
    if "unclear&quote" in s:
        return "it has a quote PostgreSQL versions read differently"
    m = _SELECT_SIDE_EFFECT.search(strip_sql(sql, keep_identifiers=True))
    if m:
        return f"it calls {m.group(1)}"
    return None


# ── body rules ─────────────────────────────────────────────────────────────────────────────────

_SEARCH_KEYS = frozenset({"search_term", "search_columns", "limit", "offset"})


def _search_body(body: Any) -> str | None:
    if not isinstance(body, dict):
        return "the search body is not an object"
    extra = sorted(set(body) - _SEARCH_KEYS)
    return f"unexpected search fields: {', '.join(extra)}" if extra else None


def _select_body(body: Any) -> str | None:
    if not isinstance(body, dict):
        return "the query body is not an object"
    extra = sorted(set(body) - {"sql", "params"})
    if extra:
        return f"unexpected query fields: {', '.join(extra)}"
    sql = body.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return "no SQL text"
    return select_is_read(sql)


def _object_body(body: Any) -> str | None:
    return None if isinstance(body, dict) else "the body is not an object"


def _no_certificate(body: Any) -> str | None:
    if not isinstance(body, dict):
        return "the body is not an object"
    if body.get("certificate_id") not in (None, ""):
        return "a certificate id would store the verify link"
    return None


def _no_work_order(body: Any) -> str | None:
    if not isinstance(body, dict):
        return "the body is not an object"
    if body.get("work_order_id") not in (None, ""):
        return "a work order id would store the approval chain"
    return None


#: The read-only POSTs the chat sends (G0 findings, replay-footprint section 1). Everything else that is
#: not a read is refused. Paths are the whole path after /__proxy/<service>, without the query string.
ALLOW_TABLE: tuple[AllowRule, ...] = (
    AllowRule("udr", "POST", re.compile(r"^/api/tables/[A-Za-z_][A-Za-z0-9_]*/records/search$"), _search_body,
              "search records",
              "udr_agent.py:664 (search_term, search_columns, limit, offset) -> svc-udr routes/tables.py:112-134 "
              "-> one SELECT"),
    AllowRule("udr", "POST", re.compile(r"^/api/tables/query/select$"), _select_body,
              "custom read",
              "udr_agent.py:745 ({sql, params}) -> svc-udr routes/tables.py:215-232 -> database_service.py "
              "execute_select (SELECT only)"),
    AllowRule("oi", "POST", re.compile(r"^/api/compliance/vendor-registration-check$"), _object_body,
              "check a registration",
              "compliance_engine_agent.py:841 -> OI routes/compliance.py:1000 (reads vendor accreditations)"),
    AllowRule("oi", "POST", re.compile(r"^/api/compliance/verify-now$"), _no_certificate,
              "build a verify link",
              "compliance_engine_agent.py:798 -> OI routes/compliance.py:965 (stores only with certificate_id)"),
    AllowRule("oi", "POST", re.compile(r"^/api/compliance/forensics$"), _object_body,
              "check a document",
              "document_forensics.py:260 -> OI routes/compliance.py:983 (analysis only)"),
    AllowRule("oi", "POST", re.compile(r"^/api/compliance/extract$"), _object_body,
              "read a document",
              "compliance_single_door.py:984 -> OI routes/compliance.py:1079 (extraction only)"),
    AllowRule("wom", "POST", re.compile(r"^/api/work-orders/suggest-approval$"), _no_work_order,
              "suggest an approval chain",
              "wo_engine_agent.py:604 -> WOM routes/work_orders.py:434 (stores only with work_order_id)"),
)

_SEGMENT_BAD = re.compile(r"[\x00-\x1f\x7f\\]")


def clean_path(path: str) -> str | None:
    """The path with one leading '/', or None when it is unsafe to forward (an empty, '.' or '..' segment
    inside it, a backslash, a control character or a scheme)."""
    p = str(path or "").split("?", 1)[0].split("#", 1)[0]
    if not p.startswith("/"):
        p = "/" + p
    if _SEGMENT_BAD.search(p) or "://" in p:
        return None
    inner = p[1:-1] if p.endswith("/") else p[1:]
    if inner and any(seg in ("", ".", "..") for seg in inner.split("/")):
        return None
    return p


def _parse_body(body: Any) -> Any:
    if isinstance(body, (bytes, bytearray)):
        body = bytes(body).decode("utf-8", errors="replace")
    if isinstance(body, str):
        if not body.strip():
            return None
        try:
            return json.loads(body)
        except ValueError:
            return None
    return body


def check(service: str, method: str, path: str, body: Any) -> Decision:
    """May this request leave the process? Reads always; a POST only when ALLOW_TABLE names it and its
    body passes the rule (body may be the raw bytes, a JSON string or the parsed object)."""
    if service not in SERVICES:
        return Decision(False, "not a service Trace Lab forwards to")
    bare = clean_path(path)
    if bare is None:
        return Decision(False, "the path is not one Trace Lab forwards")
    m = str(method or "").upper()
    if m in READ_METHODS:
        return Decision(True, "read")
    reason = "not on Trace Lab's list of read-only requests"
    for rule in ALLOW_TABLE:
        if rule.service == service and rule.method == m and rule.path.match(bare):
            why = rule.body_ok(_parse_body(body))
            if why is None:
                return Decision(True, f"allowed: {rule.label}")
            reason = why
            break
    return Decision(False, reason)


# ── the routes and the per-case refusal log (Phase 3 Task 4) ───────────────────────────────────

PROXY_PREFIX = "/__proxy"
#: The only peers the proxy answers: this process's own calls arrive from the loopback interface.
LOOPBACK_CLIENTS = frozenset({"127.0.0.1", "::1"})
LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})
#: Both gateways (single-url and all-in-one nginx) set X-Real-IP and X-Forwarded-For on every request at
#: server level, so a request that came through a gateway always carries one of these, even when nginx
#: itself connects from 127.0.0.1 (the all-in-one image). svc-deepagents' http_client never sets them.
FORWARDING_HEADERS: tuple[str, ...] = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
                                       "x-real-ip")
HOP_BY_HOP = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
                        "transfer-encoding", "upgrade"})
_DROP_REQUEST = HOP_BY_HOP | {"host", "content-length", "accept-encoding"}
_DROP_RESPONSE = HOP_BY_HOP | {"content-length", "content-encoding"}
MAX_BODY_BYTES = 2_000_000
PROXY_TIMEOUT_S = 180.0
MAX_LOG = 50
METHODS = ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
REFUSED_TEXT = "Trace Lab refused this request: a test run only reads."


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(authorization: str | None) -> str | None:
    raw = (authorization or "").strip()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else None


class RefusalLog:
    """The refusals, and the upstream failures, of the one golden case running in this process.

    Concurrency is one case per process (the runner), so one holder behind a lock is enough. Outside a
    case nothing is kept (a refusal is still written to the log). An upstream failure (401, 429, 5xx or
    unreachable) is kept only when the request carried the case's own Authorization header, so an
    identity lookup by another Trace Lab request at the same time is not counted against the case. The
    header is kept as a sha256 digest, never as the token."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._case: str | None = None
        self._auth: str | None = None
        self._refused: list[dict[str, Any]] = []
        self._upstream: list[dict[str, Any]] = []

    def begin(self, case_ref: str, *, authorization: str | None = None) -> None:
        with self._lock:
            if self._case is not None:
                raise RuntimeError(f"a golden case is already running in this process ({self._case})")
            self._case, self._auth = str(case_ref), _digest(authorization)
            self._refused, self._upstream = [], []

    def active(self) -> str | None:
        with self._lock:
            return self._case

    def current(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._refused]

    def upstream_failures(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._upstream]

    def end(self) -> list[dict[str, Any]]:
        """The case's refusals; the holder is empty again afterwards."""
        with self._lock:
            out = [dict(e) for e in self._refused]
            self._case, self._auth, self._refused, self._upstream = None, None, [], []
            return out

    def note(self, entry: dict[str, Any]) -> None:
        with self._lock:
            if self._case is not None and len(self._refused) < MAX_LOG:
                self._refused.append({**entry, "case": self._case})

    def note_upstream(self, entry: dict[str, Any], *, authorization: str | None) -> None:
        with self._lock:
            if self._case is None or len(self._upstream) >= MAX_LOG:
                return
            if self._auth is not None and _digest(authorization) != self._auth:
                return
            self._upstream.append({**entry, "case": self._case})

    def reset(self) -> None:
        with self._lock:
            self._case, self._auth, self._refused, self._upstream = None, None, [], []


#: The process's one refusal log (the runner's capture wraps each message in begin/end).
refusals = RefusalLog()

_upstreams_cache: dict[str, str] | None = None
_http: httpx.AsyncClient | None = None


def is_loopback(request: Request) -> bool:
    """A call from inside this process: a loopback peer, a loopback Host and no forwarding header."""
    client = request.client.host if request.client else ""
    if client not in LOOPBACK_CLIENTS:
        return False
    if any(h in request.headers for h in FORWARDING_HEADERS):
        return False
    host = urlsplit("//" + (request.headers.get("host") or "")).hostname or ""
    return host in LOOPBACK_HOSTNAMES


def upstreams(request: Request | None = None) -> dict[str, str]:
    """Where allowed requests go: the app's own Settings when it was built with them, else the
    TRACE_LAB_UPSTREAM_* environment (read once; ValueError for a bad value)."""
    global _upstreams_cache
    configured = getattr(request.app.state, "trace_lab_settings", None) if request is not None else None
    if configured is not None:
        return dict(configured.upstreams)
    if _upstreams_cache is None:
        from .settings import load

        _upstreams_cache = dict(load().upstreams)
    return dict(_upstreams_cache)


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(PROXY_TIMEOUT_S, connect=10.0), follow_redirects=False)
    return _http


def _error(status: int, reason: str, error: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": {"reason": reason, "error": error, **extra}})


def _refuse(service: str, method: str, path: str, why: str) -> JSONResponse:
    entry = {"at": _now(), "service": service, "method": method, "path": path[:300], "reason": why[:300],
             "label": label_for(service)}
    refusals.note(entry)
    log.info("trace_lab.proxy.refused", service=service, method=method, path=path[:200], reason=why[:200],
             case=refusals.active())
    return _error(403, REFUSED_REASON, REFUSED_TEXT, service=service, method=method, path=path[:300], why=why[:300])


def _rewrite_location(value: str, upstream: str, service: str) -> str:
    """A redirect to the upstream is sent back through the proxy, so a redirected request is checked again."""
    if upstream and (value == upstream or value.startswith(upstream + "/")):
        return f"{PROXY_PREFIX}/{service}{value[len(upstream):] or '/'}"
    if value.startswith("/") and not value.startswith("//"):
        return f"{PROXY_PREFIX}/{service}{value}"
    return value


router = APIRouter(include_in_schema=False)


@router.api_route(PROXY_PREFIX + "/{service}/{path:path}", methods=METHODS)
async def forward(service: str, path: str, request: Request) -> Response:
    """Forward an allowed read to the real service, streaming its answer back; refuse anything else."""
    method = request.method.upper()
    if not is_loopback(request):
        log.warning("trace_lab.proxy.not_loopback", service=service[:20], method=method)
        return _error(403, "loopback_only", "This address answers only inside the Trace Lab process.")
    shown = "/" + path
    declared = request.headers.get("content-length") or ""
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return _refuse(service, method, shown, "the body is too large to check")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return _refuse(service, method, shown, "the body is too large to check")
    decision = check(service, method, shown, body)
    if not decision.allowed:
        return _refuse(service, method, shown, decision.reason)
    authorization = request.headers.get("authorization")
    try:
        base = upstreams(request).get(service) or ""
    except ValueError as exc:
        return _error(503, "proxy_unconfigured", "Trace Lab's service addresses are not set correctly.",
                      detail=str(exc)[:200])
    clean = clean_path(shown) or "/"
    if not base:
        refusals.note_upstream({"at": _now(), "service": service, "method": method, "path": clean[:300],
                                "status": None, "error": "upstream not configured"}, authorization=authorization)
        return _error(502, "upstream_unset", "Trace Lab has no address for that service.", service=service)
    url = base + quote(clean, safe="/:@!$&'()*+,;=-._~") + (f"?{request.url.query}" if request.url.query else "")
    headers = [(k, v) for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST]
    client = _client()
    try:
        upstream_req = client.build_request(method, url, headers=headers, content=body or None)
        resp = await client.send(upstream_req, stream=True)
    except httpx.HTTPError as exc:
        refusals.note_upstream({"at": _now(), "service": service, "method": method, "path": clean[:300],
                                "status": None, "error": type(exc).__name__}, authorization=authorization)
        log.warning("trace_lab.proxy.upstream_unreachable", service=service, error=type(exc).__name__)
        return _error(502, "upstream_unreachable", "Trace Lab could not reach that service.", service=service)
    if resp.status_code in (401, 429) or resp.status_code >= 500:
        refusals.note_upstream({"at": _now(), "service": service, "method": method, "path": clean[:300],
                                "status": resp.status_code, "error": None}, authorization=authorization)
    out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _DROP_RESPONSE}
    if "location" in out_headers:
        out_headers["location"] = _rewrite_location(out_headers["location"], base, service)
    return StreamingResponse(resp.aiter_bytes(), status_code=resp.status_code, headers=out_headers,
                             background=BackgroundTask(resp.aclose))


def reset_for_tests() -> None:
    """Forget the cached upstreams, the client and the refusal log (tests only)."""
    global _upstreams_cache, _http
    _upstreams_cache = None
    _http = None
    refusals.reset()
