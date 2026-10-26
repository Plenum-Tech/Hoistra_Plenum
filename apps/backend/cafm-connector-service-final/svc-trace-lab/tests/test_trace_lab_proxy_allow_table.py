"""trace_lab.proxy.check and ALLOW_TABLE: what a golden run's chat may send to another service (G0
addendum 2.5). Reads always; the read-only POSTs the chat really sends, each with its body rule; nothing
else. Pure: no database, no network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trace_lab import proxy

DEEPAGENTS_SRC = Path(__file__).resolve().parents[2] / "svc-deepagents" / "src"

ALLOWED = [
    ("udr", "POST", "/api/tables/buildings/records/search",
     {"search_term": "B-301", "search_columns": ["name"], "limit": 5, "offset": 0}),
    ("udr", "POST", "/api/tables/query/select",
     {"sql": "SELECT count(*) FROM work_orders WHERE status = 'Open' -- open only", "params": {}}),
    ("udr", "POST", "/api/tables/query/select", {"sql": "select 'delete me' as x, \"update\" from t;"}),
    ("oi", "POST", "/api/compliance/vendor-registration-check", {"certificate_type_code": "GAS", "vendor_name": "A"}),
    ("oi", "POST", "/api/compliance/verify-now", {"certificate_type_code": "GAS_SAFE", "accreditation_number": "1"}),
    ("oi", "POST", "/api/compliance/forensics", {"document_id": "d1"}),
    ("oi", "POST", "/api/compliance/extract", {"text": "x"}),
    ("wom", "POST", "/api/work-orders/suggest-approval", {"estimated_cost": 100, "work_order_id": None}),
]


@pytest.mark.parametrize("service,method,path,body", ALLOWED)
def test_each_read_only_post_passes_with_its_body_rule(service, method, path, body):
    for given in (body, json.dumps(body), json.dumps(body).encode()):
        d = proxy.check(service, method, path + "?page=1", given)
        assert d.allowed and d.reason.startswith("allowed: "), d


@pytest.mark.parametrize("service,method,path,body,why", [
    ("oi", "POST", "/api/compliance/verify-now", {"certificate_id": "c1"}, "certificate id"),
    ("wom", "POST", "/api/work-orders/suggest-approval", {"work_order_id": "WO-1"}, "work order id"),
    ("udr", "POST", "/api/tables/buildings/records/search", {"search_term": "x", "filters": {}}, "unexpected search"),
    ("udr", "POST", "/api/tables/buildings/records/search", b"--boundary\r\nnot json", "not an object"),
    ("udr", "POST", "/api/tables/query/select", {"sql": "SELECT 1; DELETE FROM work_orders"}, "more than one"),
    ("udr", "POST", "/api/tables/query/select", {"sql": "SELECT nextval('s')"}, "nextval"),
    ("udr", "POST", "/api/tables/query/select", {"sql": "SELECT pg_sleep(60)"}, "pg_sleep"),
    ("udr", "POST", "/api/tables/query/select", {"sql": "SELECT * INTO t FROM work_orders"}, "SELECT INTO"),
    ("udr", "POST", "/api/tables/query/select", {"sql": "SELECT * FROM work_orders FOR SHARE"}, "locks rows"),
    ("udr", "POST", "/api/tables/query/select", {"sql": "WITH x AS (DELETE FROM t RETURNING *) SELECT 1"},
     "not a SELECT"),
    ("udr", "POST", "/api/tables/query/select", {"sql": "SELECT 1", "role": "admin"}, "unexpected query"),
    ("udr", "POST", "/api/agent/query", {"message": "count"}, "list of read-only requests"),
    ("udr", "POST", "/api/tables/work_orders/records", {"status": "Open"}, "list of read-only requests"),
    ("udr", "PATCH", "/api/tables/work_orders/records/1", {"status": "Closed"}, "list of read-only requests"),
    ("udr", "DELETE", "/api/tables/work_orders/records/1", None, "list of read-only requests"),
    ("oi", "POST", "/api/compliance/scan", {}, "list of read-only requests"),
    ("oi", "POST", "/api/energy/ratings/compute", {}, "list of read-only requests"),
    ("wom", "POST", "/api/work-orders/", {"title": "x"}, "list of read-only requests"),
    ("docrag", "POST", "/doc-rag/documents/abc/match-rows", {}, "list of read-only requests"),
    ("docrag", "POST", "/doc-rag/documents/upload", b"binary", "list of read-only requests"),
])
def test_writes_and_bad_bodies_are_refused_with_a_reason(service, method, path, body, why):
    d = proxy.check(service, method, path, body)
    assert not d.allowed and why in d.reason, d


def test_reads_always_pass():
    for service in proxy.SERVICES:
        for m in ("GET", "HEAD", "OPTIONS", "get"):
            assert proxy.check(service, m, "/api/anything/at/all", None) == proxy.Decision(True, "read")


@pytest.mark.parametrize("path", ["/api/../admin", "/api//x", "/api/./x", "/a\\b", "/a\x00b", "/http://evil/x"])
def test_unsafe_paths_are_refused_even_for_a_read(path):
    d = proxy.check("udr", "GET", path, None)
    assert not d.allowed and "path" in d.reason


def test_a_trailing_slash_is_a_safe_path():
    assert proxy.clean_path("api/work-orders/") == "/api/work-orders/"
    assert proxy.check("wom", "GET", "/api/work-orders/", None).allowed


def test_an_unknown_service_is_refused():
    d = proxy.check("schema_mapper", "GET", "/api/x", None)
    assert not d.allowed and "service" in d.reason


def test_select_is_read_keeps_quoted_text_out_of_the_checks():
    assert proxy.select_is_read("SELECT 'drop table x' AS note /* delete */ -- update\nFROM t") is None
    assert proxy.select_is_read("SELECT $$; delete$$ AS s") is None
    assert proxy.select_is_read("SELECT 1;") is None
    assert proxy.select_is_read("UPDATE t SET a = 1") == "not a SELECT"


def test_labels_are_plain_sentences_without_code_names():
    for service in (*proxy.SERVICES, None, "other"):
        label = proxy.label_for(service)
        assert label.startswith("Asked ") and "/api" not in label and "udr" not in label.lower()


def test_every_rule_names_a_call_site_the_chat_really_has():
    """Each allowed POST path is sent by svc-deepagents code today (the evidence column names the file)."""
    literal = {
        r"^/api/tables/[A-Za-z_][A-Za-z0-9_]*/records/search$": ("agents/udr_agent.py", "/records/search"),
        r"^/api/tables/query/select$": ("agents/udr_agent.py", '"/api/tables/query/select"'),
        r"^/api/compliance/vendor-registration-check$": ("agents/compliance_engine_agent.py",
                                                         '"/api/compliance/vendor-registration-check"'),
        r"^/api/compliance/verify-now$": ("agents/compliance_engine_agent.py", '"/api/compliance/verify-now"'),
        r"^/api/compliance/forensics$": ("agents/document_forensics.py", '"/api/compliance/forensics"'),
        r"^/api/compliance/extract$": ("agents/compliance_single_door.py", '"/api/compliance/extract"'),
        r"^/api/work-orders/suggest-approval$": ("agents/wo_engine_agent.py", '"/api/work-orders/suggest-approval"'),
    }
    assert {r.path.pattern for r in proxy.ALLOW_TABLE} == set(literal)
    for rule in proxy.ALLOW_TABLE:
        file, needle = literal[rule.path.pattern]
        assert needle in (DEEPAGENTS_SRC / file).read_text(encoding="utf-8"), (file, needle)
        assert file.split("/")[-1] in rule.evidence and rule.method == "POST" and rule.label


def test_the_check_imports_no_application_code():
    text = Path(proxy.__file__).read_text(encoding="utf-8")
    assert "from src" not in text and "import src" not in text


# ── fix round 1: the side-effect screen cannot be hidden by quoting, '$' in a name, or E'\'' ──────────

HIDDEN_SIDE_EFFECTS = [
    'SELECT "nextval"(\'work_orders_id_seq\')',
    'SELECT "pg_sleep"(60)',
    'SELECT "pg_advisory_lock"(1)',
    'SELECT pg_catalog."nextval" (\'s\')',
    'SELECT "NextVal"(\'s\')',
    "SELECT 1 AS x$a$, nextval('s') AS y, $a$$a$ AS z",
    "SELECT 1 AS x$$, nextval('s') AS y, $$ AS z$$",
    "SELECT E'\\'' , nextval('s')",
    "SELECT e'\\'' , setval('s', 1)",
    'SELECT U&"\\006eextval"(\'s\')',
]


@pytest.mark.parametrize("sql", HIDDEN_SIDE_EFFECTS)
def test_select_is_read_refuses_side_effects_hidden_by_quoting_or_lexing(sql):
    assert proxy.select_is_read(sql) is not None


def test_select_is_read_still_allows_quoted_names_that_are_not_calls():
    assert proxy.select_is_read('SELECT "nextval" FROM t') is None
    assert proxy.select_is_read('SELECT "my ""odd"" col", "update" FROM "work orders"') is None
    assert proxy.select_is_read("SELECT 1 AS x$a, 'it''s' AS y, $t$ nextval('s') $t$ AS z") is None
    assert proxy.select_is_read("SELECT E'it\\'s fine' AS y, 'a\\' AS z") is None


def test_strip_sql_keeps_the_text_of_quoted_names_only_when_asked():
    assert proxy.strip_sql('SELECT "NextVal"(1)') == "select ident (1)"
    assert proxy.strip_sql('SELECT "NextVal"(1)', keep_identifiers=True) == "select nextval (1)"


# ── fix round 2: PostgreSQL identifier rule (high-bit characters) and nested block comments ───────────

HIDDEN_BY_LEXING_ROUND_2 = [
    # a non-ASCII character (euro sign, middle dot) is an identifier character in PostgreSQL, so x€$a$ is one name
    "SELECT 1 AS x€$a$, nextval('s') AS y, $a$$a$ AS z",
    "SELECT 1 AS x·$a$, nextval('s') AS y, $a$$a$ AS z",
    "SELECT 1 AS x€$$, nextval('s') AS y, $$ AS z$$",
    "SELECT 1 AS x·$a$, pg_sleep(60) AS y, $a$$a$ AS z",
    # block comments nest: the comment runs to the matching */, so the quote inside it never opens a string
    "SELECT 1 /* /* */ ' */ , pg_sleep(60) AS y, ' ' AS z",
    "SELECT 1 /* /* */ ' */ , nextval('s') AS y, ' ' AS z",
    "SELECT 1 /* /* /* */ */ ' */ , nextval('s') AS y, ' ' AS z",
]


@pytest.mark.parametrize("sql", HIDDEN_BY_LEXING_ROUND_2)
def test_select_is_read_refuses_what_postgresql_lexes_differently(sql):
    assert proxy.select_is_read(sql) is not None


def test_ident_char_follows_the_postgresql_scanner():
    for ch in ("a", "Z", "7", "_", "$", "€", "·", "é", "ÿ", "中"):
        assert proxy._ident_char(ch), ch
    for ch in (" ", "'", '"', "(", ",", ";", "-", "\n"):
        assert not proxy._ident_char(ch), ch


def test_e_string_prefix_uses_the_same_identifier_rule():
    # 'é' is part of the name 'ée', so the quote after it opens a plain string: the backslash does not escape.
    assert proxy.strip_sql("SELECT ée'\\' , nextval('s')") == "select ée'' , nextval('')"
    assert proxy.select_is_read("SELECT ée'\\' , nextval('s')") is not None
    # a lone e is the E-string prefix: the backslash escapes the quote and the string runs on
    assert proxy.strip_sql("SELECT e'\\' , nextval('s')") == "select e''s''"


def test_strip_sql_tracks_comment_depth():
    assert proxy.strip_sql("select 1 /* a /* b */ c */ + 2") == "select 1 + 2"
    assert proxy.strip_sql("select 1 /* a /* b */ c") == "select 1"  # unclosed runs to the end
    assert proxy.strip_sql("select 1 /* a */ , 2 /* b */") == "select 1 , 2"


def test_select_is_read_still_allows_comments_and_non_ascii_text():
    assert proxy.select_is_read("SELECT 1 /* a /* nested nextval('s') */ still comment */ + 2") is None
    assert proxy.select_is_read("SELECT 'café €' AS naïve_€, \"x·y\" FROM t") is None
    assert proxy.select_is_read("SELECT 1 AS x€$a, $t$ nextval('s') $t$ AS z") is None


# ── fix round 3: dollar tags with high-bit characters, '\r' ends a line comment, E-string continuation,
#    a number or $n running into a quote, a second dollar-quote straight after one, functions that run SQL text ──

HIDDEN_BY_LEXING_ROUND_3 = [
    # PostgreSQL accepts any high-bit character in a dollar tag, so $€$'$€$ is a one-character string
    "SELECT $€$'$€$ AS a, pg_sleep(60) AS y, ' ' AS z",
    "SELECT $é$'$é$ AS a, nextval(1) AS y, ' ' AS z",
    "SELECT $_€$'$_€$ AS a, pg_sleep(60) AS y, ' ' AS z",
    "SELECT $€é9$'$€é9$ AS a, nextval('s') AS y, ' ' AS z",
    # a carriage return ends a -- comment, so the code after it runs
    "SELECT 1 AS a --c\r, pg_sleep(60) AS b",
    "SELECT 1 AS a --c\r, nextval('s') AS b",
    # a string continued across a newline keeps the E (backslash) rules of the first piece
    "SELECT E'a'\n'\\'' AS a, pg_sleep(60) AS b, ' ' AS c",
    "SELECT E'a' -- note\n  '\\'' AS a, nextval('s') AS b, ' ' AS c",
    # a dollar-quote can open straight after another one closes
    "SELECT $a$x$a$$b$'$b$ AS a, pg_sleep(60) AS b, ' ' AS c",
    # a number or a $n parameter running into a quote is read differently by PostgreSQL versions: refused
    "SELECT 1$a$'$a$ AS a, pg_sleep(60) AS y, ' ' AS z",
    "SELECT 0x$a$, pg_sleep(60) AS b, $a$$a$ AS c",
    "SELECT $1$a$'$a$ AS a, pg_sleep(60) AS y, ' ' AS z",
    "SELECT 1e'\\'' AS a, nextval('s') AS b, ' ' AS c",
    # functions that run a query given as text run its side effects too
    "SELECT query_to_xml('select nextval(''s'')', true, false, '')",
    "SELECT query_to_xml_and_xmlschema('select pg_sleep(60)', true, false, '')",
    "SELECT * FROM ts_stat('select to_tsvector(nextval(''s'')::text)')",
    "SELECT ts_rewrite('a'::tsquery, 'select ''a''::tsquery, nextval(''s'')::text::tsquery')",
]


@pytest.mark.parametrize("sql", HIDDEN_BY_LEXING_ROUND_3)
def test_select_is_read_refuses_what_postgresql_lexes_differently_round_3(sql):
    assert proxy.select_is_read(sql) is not None


def test_strip_sql_follows_postgresql_for_dollar_tags_and_line_ends():
    assert proxy.strip_sql("select $€$a'b$€$ as x") == "select $$ as x"
    assert proxy.strip_sql("select $é1$ x $é1$, $_$y$_$") == "select $$ , $$"
    assert proxy.strip_sql("select 1 --c\rx") == "select 1 x"
    assert proxy.strip_sql("select $a$x$a$$b$y$b$") == "select $$ $$"
    assert proxy._DOLLAR_TAG.match("$€$") and proxy._DOLLAR_TAG.match("$_€9$")
    assert not proxy._DOLLAR_TAG.match("$1$") and not proxy._DOLLAR_TAG.match("$-$")


def test_select_is_read_still_allows_ordinary_text_after_round_3():
    assert proxy.select_is_read("SELECT $€$ hello ' $€$ AS a, 1 AS b") is None
    assert proxy.select_is_read("SELECT 1 AS a -- note\r\n, 2 AS b") is None
    assert proxy.select_is_read("SELECT E'a\\'b' AS a, 'x'\n'y' AS b, 'p' 'q' AS c") is None
    assert proxy.select_is_read("SELECT x1$a$ AS y, 1.5 AS z, $1 AS p FROM t WHERE n = 10") is None
    assert proxy.select_is_read("SELECT xu&\"v\" FROM t") is None
    assert proxy.select_is_read("SELECT table_to_xml('t', true, false, '')") is None
