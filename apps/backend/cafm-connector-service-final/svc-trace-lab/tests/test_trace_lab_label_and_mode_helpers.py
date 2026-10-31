"""The pure parts of the label writes and the mode lifecycle: who is the platform owner, how a label row becomes a
payload, which anchors are well formed, how a lost head race is recognised, and that every refusal the two modules can
raise has a plain sentence for the screen (naming no table, service or id). No database, no network."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from trace_lab import labels, taxonomy
from trace_lab.review_errors import Conflict, Forbidden, Invalid, NotFound

PKG = Path(labels.__file__).parent


def _p(role, org=None):
    return SimpleNamespace(role=role, organization_id=org, user_id=uuid.uuid4(), email="a@b.test")


def test_the_platform_owner_is_a_superadmin_acting_for_no_company():
    assert labels.is_owner(_p("superadmin"), None) is True
    assert labels.is_owner(_p("superadmin", str(uuid.uuid4())), None) is True          # their own company is not "acting"
    assert labels.is_owner(_p("superadmin"), str(uuid.uuid4())) is False
    assert labels.is_owner(_p("admin"), None) is False and labels.is_owner(_p("user"), None) is False


def test_a_label_row_becomes_a_payload_of_plain_json_values():
    lid, mid = uuid.uuid4(), uuid.uuid4()
    at = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    out = labels.label_payload({"id": lid, "mode_id": mid, "present": True, "created_at": at, "supersedes": None,
                                "basis": '{"via": "split"}', "decided_by": uuid.uuid4()})
    assert out["id"] == str(lid) and out["mode_id"] == str(mid) and out["created_at"] == at.isoformat()
    assert out["basis"] == {"via": "split"} and out["supersedes"] is None and out["present"] is True
    assert labels.label_payload({"basis": None})["basis"] == {} and labels.label_payload({"basis": {"a": 1}})["basis"] == {"a": 1}


class _Model:
    def __init__(self, **kw):
        self.kw = kw

    def model_dump(self):
        return dict(self.kw)


def test_a_well_formed_anchor_is_read_from_a_dict_or_a_request_model():
    a = labels.anchor_of({"span_id": None, "field": "answer", "quote": "12 open"})
    assert (a.span_id, a.field, a.quote, a.occurrence) == (None, "answer", "12 open", 1)
    s = labels.anchor_of(_Model(span_id="s-1", field="output", quote="758", occurrence=2))
    assert (s.span_id, s.field, s.quote, s.occurrence) == ("s-1", "output", "758", 2)
    t = labels.anchor_of({"span_id": "ignored", "field": "question", "quote": "How many"})
    assert t.span_id is None                                                            # the turn's own text has no step


@pytest.mark.parametrize("anchor,reason", [
    ({"field": "answer", "quote": ""}, "anchor_required"), ({"field": "answer", "quote": "   "}, "anchor_required"),
    ({"field": "answer"}, "anchor_required"), ({"quote": "x"}, "anchor_required"),
    ({"field": "bogus", "quote": "x"}, "anchor_required"), ({"field": "answer", "quote": "x" * 2001}, "anchor_required"),
    ({"field": "answer", "quote": "x", "occurrence": 0}, "anchor_required"),
    ({"field": "answer", "quote": "x", "occurrence": "2"}, "anchor_required"),
    ({"field": "answer", "quote": "x", "occurrence": True}, "anchor_required"),
    ({"field": "input", "quote": "x"}, "span_not_in_turn"), ("a quote", "anchor_required"), (None, "anchor_required")])
def test_a_malformed_anchor_is_refused(anchor, reason):
    with pytest.raises(Invalid) as e:
        labels.anchor_of(anchor)
    assert e.value.reason == reason and e.value.error


def test_only_a_lost_head_race_is_recognised_as_one():
    class Orig(Exception):
        constraint_name = "ux_tl_labels_head"

    class Wrapped(Exception):
        orig = Orig()

    assert labels.is_head_race(Wrapped("duplicate key value violates unique constraint")) is True
    assert labels.is_head_race(Exception("duplicate key value violates unique constraint ux_tl_labels_supersedes")) is True
    assert labels.is_head_race(Exception("duplicate key value violates unique constraint ux_tl_modes_live_name")) is False
    assert labels.is_head_race(Exception("boom")) is False


def test_a_label_source_is_one_of_the_five_the_table_allows():
    assert set(labels.SOURCES) == {"human_structured_label", "accepted_suggestion", "rejected_suggestion",
                                   "accepted_label_proposal", "human_open_code"}


def _reasons(path: Path) -> set[str]:
    src = path.read_text(encoding="utf-8")
    return set(re.findall(r"\b(?:invalid|not_found|conflict|forbidden)\(\s*\"([a-z_]+)\"", src))


def test_every_refusal_the_two_modules_can_raise_has_a_sentence():
    used = _reasons(PKG / "taxonomy.py") | _reasons(PKG / "labels.py")
    assert used, "the scan found no refusals: the pattern is wrong"
    assert used <= set(taxonomy.SENTENCES), sorted(used - set(taxonomy.SENTENCES))


def test_no_sentence_names_a_table_a_service_or_an_id_and_each_reads_as_a_sentence():
    for reason, sentence in taxonomy.SENTENCES.items():
        assert re.match(r"^[a-z][a-z0-9_]*$", reason)
        assert sentence and sentence[0].isupper() and sentence.rstrip()[-1] in ".?", (reason, sentence)
        low = sentence.lower()
        for bad in ("tracelab", "plenum", "svc-", "_id", "uuid", "sql", "postgres", "constraint"):
            assert bad not in low, (reason, sentence)


def test_the_helpers_build_the_review_errors_with_their_status_and_extras():
    for helper, cls, status in ((taxonomy.invalid, Invalid, 422), (taxonomy.not_found, NotFound, 404),
                                (taxonomy.conflict, Conflict, 409), (taxonomy.forbidden, Forbidden, 403)):
        reason = next(r for r in taxonomy.SENTENCES)
        exc = helper(reason, mode_id="m-1")
        assert isinstance(exc, cls) and exc.status == status and exc.reason == reason
        assert exc.error == taxonomy.SENTENCES[reason] and exc.extra == {"mode_id": "m-1"}
    with pytest.raises(KeyError):
        taxonomy.invalid("a_reason_with_no_sentence")
