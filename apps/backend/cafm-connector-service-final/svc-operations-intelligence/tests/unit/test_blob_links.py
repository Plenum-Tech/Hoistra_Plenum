"""Signed links and credentialed reads, so the attachments container can be private."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from src.shared.blob_links import signed_url, split_blob_url

KEY = "a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5"
CONN = "DefaultEndpointsProtocol=https;AccountName=plenumstorage;AccountKey=" + KEY + ";EndpointSuffix=core.windows.net"
URL = "https://plenumstorage.blob.core.windows.net/plenum-agentic-ai-attachments/migrations/m1/out/output.json"


def test_a_blob_url_is_split_and_anything_else_is_not():
    assert split_blob_url(URL) == ("plenumstorage", "plenum-agentic-ai-attachments", "migrations/m1/out/output.json")
    assert split_blob_url("/api/testing/artifacts/m1/output.json") is None
    assert split_blob_url("https://example.com/a/b") is None


def test_this_accounts_blob_gets_a_read_only_link_that_expires():
    t = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    out = signed_url(URL + "?old=sas", CONN, minutes=15, now=t)
    base, _, qs = out.partition("?")
    q = parse_qs(qs)
    assert base == URL and "old" not in q
    assert q["sp"] == ["r"] and q["se"][0].startswith("2026-10-05T12:15") and "sig" in q
    assert urlparse(out).netloc == "plenumstorage.blob.core.windows.net"


def test_anything_that_is_not_this_accounts_blob_or_has_no_key_is_left_alone():
    assert signed_url("/api/testing/artifacts/m1/output.json", CONN) == "/api/testing/artifacts/m1/output.json"
    assert signed_url(URL.replace("plenumstorage", "otheraccount"), CONN).endswith("output.json")
    assert signed_url(URL, "AccountName=plenumstorage") == URL
    assert signed_url(None, CONN) is None and signed_url("", CONN) == ""
