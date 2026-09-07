"""Per-model request parameters that the endpoint will actually accept.

Both rules here exist because omitting a parameter is not the same as sending a safe value:
LangChain drops the field when it is None, the endpoint then applies its own default, and for
some models that default is rejected. The failure surfaces as "extraction failed" rather than
as a configuration error, so it is worth pinning.
"""

import pytest

from src.llm_factory import _needs_explicit_no_reasoning, _supports_temperature


# Measured against the live endpoint with tools bound. Widening the reasoning rule to all
# gpt-5*/o* models breaks the three that reject "none" outright.
@pytest.mark.parametrize(
    "model,needs_none",
    [
        ("gpt-5.6-terra", True),
        ("gpt-5.6-luna", True),
        ("gpt-5.6-sol", True),
        ("GPT-5.6-Terra", True),  # case-insensitive
        ("gpt-5", False),
        ("gpt-5-mini", False),
        ("gpt-5-mini-2025-08-07", False),
        ("o4-mini", False),
        ("gpt-4o-mini", False),
        ("", False),
    ],
)
def test_only_the_gpt_5_6_family_needs_reasoning_effort_none(model, needs_none):
    assert _needs_explicit_no_reasoning(model) is needs_none


@pytest.mark.parametrize(
    "model,supports",
    [
        ("gpt-4o-mini", True),
        ("gpt-5", False),
        ("gpt-5.6-terra", False),
        ("o4-mini", False),
    ],
)
def test_reasoning_tier_models_take_only_the_default_temperature(model, supports):
    assert _supports_temperature(model) is supports
