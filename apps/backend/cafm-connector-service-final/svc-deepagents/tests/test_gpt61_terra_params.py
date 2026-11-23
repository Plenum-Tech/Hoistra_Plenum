"""gpt-6.1-terra replaced gpt-5.6-terra as OPENAI_MODEL (23 Nov 2026).

Every parameter rule in llm_factory works by name prefix. "gpt-6.1-terra" matched none of them, so the
switch alone would have sent temperature=0 and max_tokens - the two parameters the reasoning-tier models
reject with a 400 - and no reasoning_effort, which the gpt-5.6 family needs before it accepts tools.
"""
from __future__ import annotations

import pytest

from src import llm_factory as lf
from src.agents import llm_cost


@pytest.mark.parametrize("model", ["gpt-6.1-terra", "GPT-6.1-Terra ", "gpt-6.1-terra-2026-10-26"])
def test_gpt61_terra_is_given_the_reasoning_tier_parameters(model):
    assert lf._supports_temperature(model) is False
    assert lf.token_limit_kwargs(model, 60) == {"max_completion_tokens": 60}
    assert lf._needs_explicit_no_reasoning(model) is True


def test_the_models_it_did_not_replace_keep_their_rules():
    assert lf._needs_explicit_no_reasoning("gpt-5.6-terra") is True
    assert lf._needs_explicit_no_reasoning("gpt-5") is False          # rejects "none"
    assert lf._needs_explicit_no_reasoning("o4-mini") is False
    assert lf._supports_temperature("gpt-4o-mini") is True
    assert lf.token_limit_kwargs("gpt-4o-mini", 60) == {"max_tokens": 60}


def test_the_chat_model_is_built_with_them(monkeypatch):
    seen = {}

    def fake_init(name, **kw):
        seen.update(kw, name=name)
        return object()

    monkeypatch.setattr(lf, "init_chat_model", fake_init)
    monkeypatch.setattr(lf, "_provider_choice", lambda: "openai")
    monkeypatch.setattr(lf.settings, "openai_api_base", "", raising=False)
    lf.create_chat_model(api_key="sk-test", model="gpt-6.1-terra")
    assert seen["name"].endswith("gpt-6.1-terra")
    assert seen["temperature"] == 1 and seen["reasoning_effort"] == "none"


def test_its_cost_is_priced():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read": 0, "cache_write": 0}
    assert llm_cost.price_usd("gpt-6.1-terra", usage) == 6.0
