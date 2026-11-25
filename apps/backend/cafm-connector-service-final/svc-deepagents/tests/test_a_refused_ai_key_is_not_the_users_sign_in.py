"""The AI provider refusing the platform's own key is not the user's sign-in (25 Nov 2026).

hoistra.ai answered every question with "Your sign-in has expired, so this could not be read.
Sign in again and ask once more." The user's sign-in was fine: OpenAI was refusing the
orchestrator's API key (`Error code: 401 - Incorrect API key provided`). The turn's exception
text carries a 401 and OpenAI's https:// link, so it was classified as the caller's own 401.
Signing in again cannot fix a dead platform key, and nobody was told it was a setup problem.
"""
from __future__ import annotations

import pytest

from src.agents import evidence as ev


class AuthenticationError(Exception):
    """Stands in for openai.AuthenticationError / anthropic.AuthenticationError (same name)."""


PROVIDER_REFUSALS = [
    "Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-proj-****H6cA. You can find "
    "your API key at https://platform.openai.com/account/api-keys.', 'type': 'invalid_request_error', "
    "'param': None, 'code': 'invalid_api_key'}}",
    "Error code: 401 - {'error': {'message': 'Your API key has expired. Create a new API key to continue.', "
    "'type': 'invalid_request_error', 'code': 'expired_secret_key', 'param': None}, 'status': 401}",
    "Error code: 401 - {'error': {'message': 'Your API key has been invalidated.', 'type': None, "
    "'code': 'token_invalidated', 'param': None}, 'status': 401}",
    "Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}}",
]


@pytest.mark.parametrize("text", PROVIDER_REFUSALS)
def test_a_refused_platform_key_is_a_setup_problem(text):
    r = ev.from_exception(AuthenticationError(text))
    assert r["category"] == "service_setup"
    assert r["user_actionable"] is False
    said = ev.user_message(AuthenticationError(text))
    assert "sign" not in said.lower()
    assert "administrator" in said.lower()


def test_the_users_own_expired_token_is_still_their_sign_in():
    r = ev.from_exception(RuntimeError("Not authorised: token expired"))
    assert r["category"] == "signed_out"
