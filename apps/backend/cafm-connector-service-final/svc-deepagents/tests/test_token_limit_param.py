"""Which name this model wants for its output-token ceiling.

The semantic classifier passed `max_tokens=60` and got a 400 back — "Unsupported parameter:
'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead" — on
every call it has ever made on this deployment. It catches its own exception and returns
None, so the caller fell through to a weaker strategy and nothing above it knew the
best-informed step had never run.

The file already had the sibling rule: the same model families reject any temperature but the
default. Only one of the two quirks had been written down.
"""
from __future__ import annotations

import pytest

from src.llm_factory import _supports_temperature, _token_limit_param, token_limit_kwargs

REASONING = ["gpt-5", "gpt-5-mini", "gpt-5.6-terra", "gpt-5.6-luna", "o1", "o3-mini", "o4-mini"]
CLASSIC = ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"]


@pytest.mark.parametrize("model", REASONING)
def test_reasoning_models_want_max_completion_tokens(model: str):
    assert _token_limit_param(model) == "max_completion_tokens"


@pytest.mark.parametrize("model", CLASSIC)
def test_older_models_still_want_max_tokens(model: str):
    assert _token_limit_param(model) == "max_tokens"


def test_an_unset_model_falls_back_to_the_older_name():
    # Sending max_completion_tokens to a model that wants max_tokens is the same 400 in the
    # other direction, so the unknown case takes the name that has always existed.
    for unset in ("", None, "   "):
        assert _token_limit_param(unset) == "max_tokens"


def test_case_and_padding_do_not_change_the_answer():
    assert _token_limit_param("  GPT-5.6-Terra  ") == "max_completion_tokens"


def test_the_kwargs_helper_is_splattable():
    assert token_limit_kwargs("gpt-5.6-terra", 60) == {"max_completion_tokens": 60}
    assert token_limit_kwargs("gpt-4o", 60) == {"max_tokens": 60}


@pytest.mark.parametrize("model", REASONING + CLASSIC)
def test_exactly_one_ceiling_parameter_is_ever_sent(model: str):
    # Both names in one request is also a 400. The helper returns a single-key dict so a
    # caller cannot splat both by accident.
    assert len(token_limit_kwargs(model, 1)) == 1


def test_the_two_model_rules_are_kept_separate():
    # They happen to cover the same families today. The test states that this is a
    # coincidence rather than a shared rule, so that when one list changes the other is
    # looked at rather than assumed to follow.
    for model in REASONING:
        assert not _supports_temperature(model)
        assert _token_limit_param(model) == "max_completion_tokens"
    for model in CLASSIC:
        assert _supports_temperature(model)
        assert _token_limit_param(model) == "max_tokens"
