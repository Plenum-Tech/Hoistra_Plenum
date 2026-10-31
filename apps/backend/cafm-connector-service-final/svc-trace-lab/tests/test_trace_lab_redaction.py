"""Trace Lab redaction: keys AND values, everywhere a record leaves the server. JSON stored as strings
and cut JSON are redacted too; token counts and emails are kept (reviewers are the company's admins)."""
from __future__ import annotations

import json

from src.agents.fiix_credential_parse import _CRED_LINE_PATTERNS
from trace_lab import normalize as n


def test_the_normalizer_constants():
    assert n.NORMALIZER_VERSION == "norm-1" and n.REDACTED == "[redacted]"
    assert n.LOOP_GROUP == "orchestrator loop" and n.QUESTION_CHARS == 4000 and n.ANSWER_CHARS == 20000
    assert n.QUESTION_MARKER == "\n\n---\n\n**Current user message:**\n"
    assert "gate" not in n.ROLES and n.ROLES[0] == "user" and n.ROLES[-1] == "system_note"
    assert n.COT_NOTICE.startswith("Model reasoning is not shown.") and "not a missing step" in n.COT_CARD_NOTE
    assert "provider" in n.ALWAYS_UNAVAILABLE
    assert n._FIIX_SECRET_LINES == tuple(p for p, f in _CRED_LINE_PATTERNS if f in ("app_key", "access_key", "secret_key"))


def test_credential_keys_are_redacted_everywhere():
    for key in ("Authorization", "x-api-key", "apiKey", "APIKey", "access_key", "app_key", "client_secret",
                "refresh_token", "id_token", "sessionToken", "password", "AccountKey", "connection_string",
                "dsn", "sas_token", "token", "sig", "signature", "private_key", "cookie", "set-cookie"):
        assert n.sensitive_key(key), key
    value = {"headers": {"x-api-key": "abc123", "Authorization": "Bearer abcdefghijkl"},
             "creds": {"app_key": "A", "access_key": "B", "secret_key": "C"},
             "nested": [{"client_secret": {"v": 1}}, {"refresh_token": ["x"]}],
             "empty_password": "", "fiix_credentials_configured": True, "password_changed_at": "2026-10-01"}
    out = n.redact(value)
    assert out["headers"]["x-api-key"] == "[redacted]" and out["headers"]["Authorization"] == "[redacted]"
    assert out["creds"] == {"app_key": "[redacted]", "access_key": "[redacted]", "secret_key": "[redacted]"}
    assert out["nested"] == [{"client_secret": "[redacted]"}, {"refresh_token": "[redacted]"}]
    assert out["empty_password"] == "" and out["fiix_credentials_configured"] is True
    assert out["password_changed_at"] == "2026-10-01"
    assert value["headers"]["x-api-key"] == "abc123"          # the input is never changed


def test_token_counts_are_not_redacted():
    for key in ("input_tokens", "output_tokens", "cache_read_tokens", "prompt_tokens", "max_tokens", "token_count",
                "total_tokens"):
        assert not n.sensitive_key(key), key
    assert n.redact({"input_tokens": 900, "max_tokens": 4096}) == {"input_tokens": 900, "max_tokens": 4096}


def test_secret_values_are_redacted_in_free_text():
    text = ("call with Bearer abcdefghijklmnop and sk-ant-api03-ABCDEFGHIJKLMNOPQRST and sk-proj-abcdefghijklmnopqrstu "
            "jwt eyJhbGciOi.eyJzdWIiOiIx.SflKxwRJSM aws AKIAABCDEFGHIJKLMNOP gh ghp_" + "a" * 36 + " "
            "DefaultEndpointsProtocol=https;AccountKey=abc/def==;EndpointSuffix=core "
            "https://blob.example/x?sv=1&sig=abcdef&se=2 postgresql://user:pw123@db.example:5432/x "
            "api_key=zzz password: 'hunter2' "
            "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----")
    out = n.redact_text(text)
    for secret in ("abcdefghijklmnop", "ABCDEFGHIJKLMNOPQRST", "abcdefghijklmnopqrstu", "SflKxwRJSM",
                   "AKIAABCDEFGHIJKLMNOP", "a" * 36, "abc/def==", "sig=abcdef", "pw123", "zzz", "hunter2", "MIIabc"):
        assert secret not in out, secret
    assert "Bearer [redacted]" in out and "AccountKey=[redacted]" in out and "sig=[redacted]" in out
    assert "postgresql://user:[redacted]@db.example" in out and "[redacted private key]" in out


def test_fiix_credential_lines_are_redacted():
    text = "Subdomain: acme\nApp Key: app-123\nAPI Access Key = acc-456\nSecret: sec-789\n"
    out = n.redact_text(text)
    assert "Subdomain: acme" in out
    for secret in ("app-123", "acc-456", "sec-789"):
        assert secret not in out


def test_json_inside_strings_and_truncated_json_are_redacted():
    inner = json.dumps({"query": "boilers", "api_key": "sk-live-1", "rows": [{"access_token": "t0k"}]})
    out = n.redact({"output": inner})
    parsed = json.loads(out["output"])
    assert parsed["api_key"] == "[redacted]" and parsed["rows"][0]["access_token"] == "[redacted]"
    assert parsed["query"] == "boilers"
    cut = {"_truncated": '{"system_prompt": "You are...", "client_secret": "s3cr3t-value", "x": "y", "password": "abc'}
    red = n.redact(cut)["_truncated"]
    assert "s3cr3t-value" not in red and "abc" not in red.split('"password"')[1]
    assert '"system_prompt": "You are..."' in red


def test_emails_are_kept():
    assert n.redact_text("Ask ops@acme.co.uk or fm@example.com") == "Ask ops@acme.co.uk or fm@example.com"
    assert n.redact({"email": "fm@example.com"}) == {"email": "fm@example.com"}


def test_deep_nesting_is_cut_not_crashed():
    deep: dict = {}
    cur = deep
    for _ in range(100):
        cur["x"] = {}
        cur = cur["x"]
    assert "[cut: nested too deep]" in json.dumps(n.redact(deep))


def test_json_pairs_inside_prose_are_redacted():
    # Fix round 1: the key-in-text rule runs on every string, not only strings that start with a bracket.
    text = 'Tool returned: {"client_secret": "xyz-secret-1", "api_key": "plainkey99", "query": "boilers"} done'
    out = n.redact_text(text)
    assert "xyz-secret-1" not in out and "plainkey99" not in out
    assert out.startswith("Tool returned: ") and out.endswith(" done")
    assert '"query": "boilers"' in out and '"client_secret": "[redacted]"' in out
    nested = n.redact({"error": 'HTTP 401 for {"headers": {"x-api-key": "abc-999"}, "id_token": "tok-777"}'})
    assert "abc-999" not in nested["error"] and "tok-777" not in nested["error"]
    assert n.redact_text('He said "name": "Ann" and left') == 'He said "name": "Ann" and left'


def test_python_repr_dict_strings_are_redacted():
    # Fix round 1: str(dict) text (single quotes) reaches spans; bracketed text that is a Python literal
    # is parsed and redacted as a structure, anything else gets the single-quote key-in-text rule.
    out = n.redact_text("{'api_key': 'plainkey99', 'password': 'hunter2', 'query': 'boilers'}")
    assert "plainkey99" not in out and "hunter2" not in out and "boilers" in out
    deep = n.redact_text("{'client_secret': {'v': 'topsecretvalue'}, 'rows': [{'access_token': 't0k'}], 'n': 3}")
    assert "topsecretvalue" not in deep and "t0k" not in deep and json.loads(deep)["n"] == 3
    in_prose = n.redact_text("Tool input: {'api_key': 'plainkey99', 'user': 'ann'} (retrying)")
    assert "plainkey99" not in in_prose and "'user': 'ann'" in in_prose and in_prose.endswith("(retrying)")
    mixed = n.redact_text("""{'password': "it's secret", 'ok': 'yes'}""")
    assert "it's secret" not in mixed and "yes" in mixed
    not_literal = n.redact_text("{'api_key': 'plainkey99', 'at': datetime.datetime(2026, 1, 1)}")
    assert "plainkey99" not in not_literal and "datetime.datetime(2026, 1, 1)" in not_literal
    cut = n.redact_text("{'client_secret': 's3cr3t-value', 'password': 'abc")
    assert "s3cr3t-value" not in cut and "abc" not in cut.split("'password'")[1]
    assert n.redact_text("Don't panic: it's fine") == "Don't panic: it's fine"


def test_escaped_json_inside_json_values_is_redacted():
    # Fix round 2: double-encoded JSON (a tool output string inside another payload), in prose.
    probes = (
        (r'HTTP 400: {"detail": "{\"client_secret\": \"SEC-4\"}"}', "SEC-4"),
        (r'Tool returned: {"body": "{\"api_key\": \"SEC-1\"}"}', "SEC-1"),
        (r"""Tool input: {'body': '{\"api_key\": \"SEC-2\"}'}""", "SEC-2"),
        (r'{"body": "{\"api_key\": \"SEC-5\"}"} and more', "SEC-5"),
        (r'plain escaped text {\"password\": \"SEC-3\", \"user\": \"ann\"} end', "SEC-3"),
        ("Tool returned: " + json.dumps({"body": json.dumps({"inner": json.dumps({"api_key": "SEC-8"})})}), "SEC-8"),
    )
    for text, secret in probes:
        out = n.redact_text(text)
        assert secret not in out, (text, out)
        assert "[redacted]" in out, (text, out)
    assert n.redact_text(r'plain escaped text {\"password\": \"SEC-3\", \"user\": \"ann\"} end') == \
        r'plain escaped text {\"password\": \"[redacted]\", \"user\": \"ann\"} end'
    nested = "Tool returned: " + json.dumps({"body": json.dumps({"api_key": "SEC-9", "query": "boilers"})})
    out = n.redact_text(nested)
    body = json.loads(out.split(": ", 1)[1])["body"]           # the outer JSON is still valid
    assert json.loads(body) == {"api_key": "[redacted]", "query": "boilers"}
    quiet = r'Tool returned: {"body": "{\"query\": \"boilers\", \"n\": 3}"} ok'
    assert n.redact_text(quiet) == quiet                         # nothing secret: unchanged byte for byte
    assert n.redact_text(n.redact_text(nested)) == out           # idempotent


def test_sensitive_keys_with_object_or_array_values_in_text():
    # Fix round 2: the text path replaces an object or array under a sensitive key, as the structure path does.
    for text, secret in (
        ('Tool returned: {"client_secret": {"value": "SEC-6"}}', "SEC-6"),
        ("Tool input: {'password': ['SEC-7']}", "SEC-7"),
        ('Tool returned: {"client_secret": {"value": "SEC-9", "x": [1', "SEC-9"),       # cut: runs to the end
        ('note {"api_key": {"v": "a]}b", "w": "SEC-10"}, "query": "boilers"} tail', "SEC-10"),
        (r'wrapped {"body": "{\"client_secret\": {\"v\": \"SEC-11\"}}"} tail', "SEC-11"),
    ):
        out = n.redact_text(text)
        assert secret not in out and "[redacted]" in out, (text, out)
    out = n.redact_text('note {"api_key": {"v": "a]}b", "w": "SEC-10"}, "query": "boilers"} tail')
    assert out == 'note {"api_key": "[redacted]", "query": "boilers"} tail'
    assert n.redact_text("Tool input: {'password': ['SEC-7']} (retrying)") == "Tool input: {'password': '[redacted]'} (retrying)"
    assert n.redact_text('seen {"token": 42, "password": null} ok') == 'seen {"token": 42, "password": null} ok'
