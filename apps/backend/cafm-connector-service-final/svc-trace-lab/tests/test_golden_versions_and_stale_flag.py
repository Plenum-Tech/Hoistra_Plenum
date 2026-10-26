"""Each upload is a new version: the diff names added, removed, changed and unchanged cases; a case is stale
when its Verified on is older than N days and unverified when blank. 15 Oct 2026. Spec §1.7, §1.8.
(The database half — version n+1, previous superseded — is Phase 2.)"""
from __future__ import annotations

import hashlib
from datetime import date

from trace_lab import golden


def _case(key, version, source="id_column"):
    return golden.CaseRow(row_no=2, case_key=key, key_source=source, case_version=version, question="q",
                          followups=(), expected_answer="a", evaluation="objective", expected_values=(), source=None,
                          verified_on=None, page_context=None, ask_as="company", requirement_ids=(), notes=None, flags=())


def test_the_diff_against_the_previous_version():
    previous = [("1", "aaaaaaaaaaaa", "id_column"), ("2", "bbbbbbbbbbbb", "id_column"), ("3", "cccccccccccc", "id_column")]
    d = golden.version_diff(previous, [_case("1", "aaaaaaaaaaaa"), _case("2", "dddddddddddd"), _case("4", "eeeeeeeeeeee")],
                            previous_version=3)
    assert d == {"previous_version": 3, "added": ["4"], "removed": ["3"], "changed": ["2"], "unchanged": 1,
                 "keys_restarted": False}


def test_adding_or_removing_the_id_column_restarts_the_keys():
    previous = [("1", "aaaaaaaaaaaa", "id_column")]
    d = golden.version_diff(previous, [_case("0123456789ab", "aaaaaaaaaaaa", source="hash")], previous_version=1)
    assert d["keys_restarted"] is True


def test_the_first_upload_has_everything_added():
    d = golden.version_diff([], [_case("1", "aaaaaaaaaaaa")], previous_version=None)
    assert d["added"] == ["1"] and d["previous_version"] is None and d["keys_restarted"] is False


def test_the_set_hash_is_order_free():
    pairs = [("2", "b" * 12), ("1", "a" * 12)]
    want = hashlib.sha256("1:aaaaaaaaaaaa\n2:bbbbbbbbbbbb".encode()).hexdigest()[:12]
    assert golden.set_hash(pairs) == want == golden.set_hash(list(reversed(pairs)))


def test_stale_after_n_days_and_unverified_is_not_stale(monkeypatch):
    on = date(2026, 10, 15)
    assert golden.is_stale(date(2026, 9, 14), on=on, days=30) is True
    assert golden.is_stale(date(2026, 9, 15), on=on, days=30) is False
    assert golden.is_stale(None, on=on, days=30) is False
    monkeypatch.setenv("TRACE_LAB_GOLDEN_STALE_DAYS", "7")
    assert golden.stale_days() == 7 and golden.is_stale(date(2026, 10, 7), on=on) is True


def test_the_default_window_is_thirty_days(monkeypatch):
    monkeypatch.delenv("TRACE_LAB_GOLDEN_STALE_DAYS", raising=False)
    assert golden.stale_days() == 30
