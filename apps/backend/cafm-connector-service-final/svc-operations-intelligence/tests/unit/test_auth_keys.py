"""The auth engine must not care what the CAFM tables key on.

It was written against the connector ORM's UUIDs; the deployments that predate it key on
integers. These cover the coercion rules directly — the end-to-end proof is a throwaway
integer-keyed database, which a unit test cannot stand up.
"""
from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest

from src.engines.auth import keys


@pytest.fixture(autouse=True)
def _clean_cache():
    keys.reset_cache()
    yield
    keys.reset_cache()


class TestCoerce:
    def test_integer_key_binds_as_an_int(self):
        # asyncpg refuses '3' for an integer parameter, so a string here is not a
        # harmless convenience — it is the 500 that made registration impossible.
        assert keys.coerce("3", "integer") == 3
        assert keys.coerce(3, "integer") == 3
        assert isinstance(keys.coerce("3", "bigint"), int)

    def test_uuid_key_binds_as_text(self):
        u = uuid4()
        assert keys.coerce(u, "uuid") == str(u)
        assert keys.coerce(str(u), "uuid") == str(u)

    def test_none_survives_as_none(self):
        # user_id is nullable on auth_otp_codes: a code is addressed to an email and may
        # be issued before the row it belongs to is certain.
        assert keys.coerce(None, "integer") is None
        assert keys.coerce(None, "uuid") is None

    def test_a_value_that_cannot_be_that_type_is_none_not_a_crash(self):
        assert keys.coerce("not-a-number", "integer") is None


class TestIsValid:
    def test_integer_column_wants_digits(self):
        assert keys.is_valid("1", "integer")
        assert keys.is_valid("-1", "integer")
        assert not keys.is_valid(str(uuid4()), "integer")
        assert not keys.is_valid("", "integer")

    def test_uuid_column_wants_a_uuid(self):
        assert keys.is_valid(str(uuid4()), "uuid")
        assert not keys.is_valid("1", "uuid")

    def test_the_message_names_what_the_caller_should_send(self):
        # "organization_id must be a UUID" on an integer-keyed database is both wrong and
        # unactionable, which is how it read before.
        assert keys.describe("integer") == "a whole number"
        assert keys.describe("uuid") == "a UUID"


class TestKeyShape:
    def test_unknown_column_types_are_refused_not_guessed(self):
        # A type nobody has thought about is a deployment nobody has thought about.
        # Treating it as text quietly is how a mismatch becomes a data problem.
        assert keys._checked("jsonb", "users.id") == "uuid"
        assert keys._checked("integer", "users.id") == "integer"

    def test_a_failed_lookup_falls_back_to_the_orm_shape(self):
        """The engine must still answer when information_schema cannot be read.

        A fresh database created by create_all is UUID-keyed, so that is the safe guess —
        and it is a guess made loudly, with a warning, not silently.
        """
        class _Boom:
            async def execute(self, *a, **k):
                raise RuntimeError("no database here")

        shape = asyncio.run(keys.key_shape(_Boom()))
        assert shape.users == "uuid"
        assert shape.organizations is None

    def test_the_shape_is_read_once(self):
        calls = {"n": 0}

        class _Once:
            async def execute(self, *a, **k):
                calls["n"] += 1
                raise RuntimeError("counted")

        asyncio.run(keys.key_shape(_Once()))
        asyncio.run(keys.key_shape(_Once()))
        assert calls["n"] == 1, "the key shape is a deployment property, not a per-request one"


class TestSelfAssigningIds:
    """Whether the INSERT supplies users.id at all depends on the column, both ways.

    The ORM declares users.id with a PYTHON-side default, so create_all emits no server
    default and an INSERT that omits the column violates NOT NULL. A sequence-backed
    integer column is the mirror image: supplying a value defeats the sequence, and
    supplying a uuid is a type error.
    """

    def test_orm_uuid_without_a_server_default_must_be_supplied(self):
        shape = keys.KeyShape(users="uuid", organizations="uuid",
                              users_id_self_assigning=False)
        assert not shape.users_id_self_assigning
        assert not shape.users_numeric

    def test_sequence_backed_integer_fills_itself(self):
        shape = keys.KeyShape(users="integer", organizations="integer",
                              users_id_self_assigning=True)
        assert shape.users_id_self_assigning
        assert shape.users_numeric
        assert shape.organizations_numeric
