"""A memory that gives a record a name is recalled whenever that name is used (29 Oct 2026).

"HOW MANY FLOORS IN JASMEET BUILDING" found nothing because every place that compared a typed name
with a stored one did it its own way. recall() ranked memories by cosine over the whole question,
or by word overlap without vectors, so the name a memory gives ("Call it Bishopsgate, not B-301")
was lost in a long question — "how many floors in bishopsgate's tower block, and who is the lift
contractor?" sat far from that sentence — and "B301" shared no word with "B-301", "Bishopgate" none
with "Bishopsgate".

A memory now also counts as named when a name it carries (its subject, a quoted name, a code, a
capitalised name) appears in the question by the shared ladder (agents/name_match.py): exactly, with
other separators, in another word order, or one letter off — never a code that is only close, and
never words for the kind of record alone ("the building").
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest

from src.http_client import caller_organization_id
from src.services import chat_memories as cm
from src.services.principal import Principal, caller_principal

NOW = datetime(2026, 10, 29, tzinfo=timezone.utc)


class _Result:
    def __init__(self, rows=None):
        self._rows, self.rowcount = rows or [], len(rows or [])

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Conn:
    def __init__(self, store):
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.store["sql"].append((sql, params or {}))
        if "FROM plenum_cafm.chat_memories WHERE" in sql:
            return _Result(self.store["mems"])
        return _Result([])


class _Engine:
    def __init__(self, store):
        self.store = store

    def begin(self):
        return _Conn(self.store)

    def connect(self):
        return _Conn(self.store)


def _mem(mid, text, subject=None, embedding=None, kind="fact"):
    return {"id": mid, "kind": kind, "text": text, "subject": subject,
            "embedding": json.dumps(embedding) if embedding else None, "user_id": None,
            "created_by_email": "ops@example.com", "created_at": NOW}


@pytest.fixture
def db(monkeypatch):
    store = {"sql": [], "mems": [], "vec": None}
    import src.database as database

    monkeypatch.setattr(database, "_get_engine", lambda: _Engine(store))
    monkeypatch.setattr(cm, "_ready", True)

    async def embed(texts):
        return [store["vec"]] * len(texts) if store["vec"] else None

    monkeypatch.setattr(cm, "embed", embed)
    p = Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=uuid.uuid4(), role="user",
                  can_ingest=True, building_ids=None)
    caller_principal.set(p)
    caller_organization_id.set(None)
    return store


def _recalled(db, question):
    return [h["id"] for h in asyncio.run(cm.recall(question))]


ALIAS = "Call it Bishopsgate, not B-301."


def test_an_alias_is_recalled_inside_a_long_question_even_when_the_vectors_are_far(db):
    db["mems"] = [_mem("m1", ALIAS, kind="preference", embedding=[1.0, 0.0])]
    db["vec"] = [0.0, 1.0]                     # the question's vector: nothing like the memory's
    assert _recalled(db, "how many floors in bishopsgate's tower block, and who is the lift contractor?") == ["m1"]


@pytest.mark.parametrize("question", [
    "what is the EPC rating, floor count, lift vendor and boiler age for B301",   # the code without its hyphen
    "what is the EPC rating, floor count, lift vendor and boiler age for b 301",
    "what is the EPC rating, floor count, lift vendor and boiler age at Bishopgate",  # one letter missing
    "how many floors does Bishopsgate’s plant room serve, and when was the boiler replaced",  # curly possessive
])
def test_an_alias_is_recalled_however_it_is_typed(db, question):
    db["mems"] = [_mem("m1", "The user calls B-301 Bishopsgate.")]
    assert _recalled(db, question) == ["m1"]


def test_a_subject_code_is_recalled_without_its_hyphen(db):
    db["mems"] = [_mem("m1", "The tenants call it the big unit.", subject="AHU-3", embedding=[1.0, 0.0])]
    db["vec"] = [0.0, 1.0]
    assert _recalled(db, "is ahu3 running on schedule this week or is it still tripping") == ["m1"]


def test_a_quoted_name_is_recalled_in_another_word_order(db):
    db["mems"] = [_mem("m1", "Riverside's boiler room is the one staff call 'the engine room'.", embedding=[1.0, 0.0])]
    db["vec"] = [0.0, 1.0]
    assert _recalled(db, "which vendor services room engine and when is the next visit due") == ["m1"]


def test_a_number_is_never_close_enough(db):
    db["mems"] = [_mem("m1", "At Building 7 the plant room is on level 2.", subject="Building 7", embedding=[1.0, 0.0])]
    db["vec"] = [0.0, 1.0]
    assert _recalled(db, "who maintains Building 70 and what is its EPC rating") == []
    assert _recalled(db, "who maintains Building 7 and what is its EPC rating") == ["m1"]


def test_words_for_a_kind_of_record_name_no_memory(db):
    db["mems"] = [_mem("m1", ALIAS, kind="preference", embedding=[1.0, 0.0]),
                  _mem("m2", "The Building is what the team calls the head office.", subject="the building",
                       embedding=[1.0, 0.0])]
    db["vec"] = [0.0, 1.0]
    assert _recalled(db, "how many floors in the building, and who is the lift contractor?") == []


def test_a_sentence_opening_word_is_not_taken_for_a_name(db):
    db["mems"] = [_mem("m1", ALIAS, kind="preference", embedding=[1.0, 0.0])]
    db["vec"] = [0.0, 1.0]
    assert _recalled(db, "call the lift contractor about the tripping breaker and the noise") == []
