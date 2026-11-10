"""The trace of one answer goes over the wire compressed (10 Nov 2026).

/lab/q/{code}/tree carries every step's input and output (up to 16,000 characters each) so the
page can show any step without asking again. A long answer is several megabytes of JSON that
compresses roughly tenfold. Production's nginx compresses nothing, and turning it on there would
change every response of the whole app, streams included; so this one route compresses its own
answer when the browser says it can read gzip, and answers as before when it cannot.
"""
from __future__ import annotations

import gzip
import json

from trace_lab import trace_tree

BIG = {"ok": True, "turn_id": "t-1", "missing": False,
       "tree": {"nodes": [{"id": str(i), "input": "SELECT * FROM work_orders " * 400,
                           "output": {"rows": list(range(200))}} for i in range(40)]}}


def test_a_large_tree_is_gzipped_when_the_browser_accepts_it():
    r = trace_tree.json_answer(BIG, "gzip, deflate, br")
    assert r.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in r.headers["vary"].lower()
    assert json.loads(gzip.decompress(r.body)) == BIG
    assert len(r.body) * 5 < len(json.dumps(BIG)), "compression is what this is for"


def test_without_gzip_in_accept_encoding_it_is_plain_json():
    for accept in (None, "", "identity", "br"):
        r = trace_tree.json_answer(BIG, accept)
        assert "content-encoding" not in r.headers
        assert json.loads(r.body) == BIG
        assert r.media_type == "application/json"


def test_a_small_answer_is_not_worth_compressing():
    small = {"ok": True, "turn_id": None, "missing": True, "tree": None}
    r = trace_tree.json_answer(small, "gzip")
    assert "content-encoding" not in r.headers
    assert json.loads(r.body) == small


def test_values_json_cannot_write_directly_are_written_as_fastapi_would():
    from datetime import datetime, timezone
    from decimal import Decimal
    r = trace_tree.json_answer({"cost": Decimal("0.0123"), "at": datetime(2026, 11, 10, tzinfo=timezone.utc)}, None)
    out = json.loads(r.body)
    assert out["cost"] == 0.0123 and out["at"].startswith("2026-11-10")
