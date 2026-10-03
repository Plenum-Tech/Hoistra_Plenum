"""The parameters each OpenAI call sends, for the model it goes to.

Creating a work order from the chat failed on 28 Sep 2026: every call here sent max_tokens,
and OPENAI_MODEL=gpt-5.6 answers 400 "Unsupported parameter: 'max_tokens' ... use
'max_completion_tokens'". The rules mirror svc-deepagents/src/llm_factory.py.
"""
from src.llm_params import completion_kwargs


def test_the_gpt_5_6_family_gets_max_completion_tokens_no_temperature_and_no_reasoning():
    assert completion_kwargs("gpt-5.6", 1024, temperature=0.2) == {
        "max_completion_tokens": 1024, "reasoning_effort": "none"}
    assert completion_kwargs("GPT-5.6-terra ", 64) == {"max_completion_tokens": 64, "reasoning_effort": "none"}


def test_gpt_5_and_mini_take_minimal_and_o_series_take_neither():
    assert completion_kwargs("gpt-5", 600) == {"max_completion_tokens": 600, "reasoning_effort": "minimal"}
    assert completion_kwargs("gpt-5-mini", 600) == {"max_completion_tokens": 600, "reasoning_effort": "minimal"}
    assert completion_kwargs("o4-mini", 600, temperature=0) == {"max_completion_tokens": 600}


def test_older_models_keep_max_tokens_and_their_temperature():
    assert completion_kwargs("gpt-4o-mini", 120, temperature=0) == {"max_tokens": 120, "temperature": 0}
    assert completion_kwargs("gpt-4o", 2048) == {"max_tokens": 2048}
    assert completion_kwargs(None, 10) == {"max_tokens": 10}
